from __future__ import annotations

import asyncio
import base64
import json
import logging
import shlex
import time
from typing import Any, Dict, List, Optional, Tuple

from workers.core.enhanced_base_tool import CCToolBase
from workers.core.tool_schemas import OpenAIFunctionToolSchema, ToolResult, create_openai_tool_schema

logger = logging.getLogger(__name__)

try:
    from kodo import KubernetesManager
except ImportError:
    KubernetesManager = None  # type: ignore[misc,assignment]


def _process_output_text(text: Any) -> str:
    if text is None:
        return ""
    if isinstance(text, list):
        s = "".join(str(t) for t in text)
    else:
        s = str(text)
    if len(s) > 8000:
        return s[:8000]
    return s


def _extract_image(data: Dict[str, Any]) -> Optional[Dict[str, str]]:
    if isinstance(data, dict):
        if isinstance(data.get("image/png"), str):
            return {"image_data": data["image/png"], "media_type": "image/png"}
        if isinstance(data.get("image/jpeg"), str):
            return {"image_data": data["image/jpeg"], "media_type": "image/jpeg"}
    return None


def _process_output(output: Dict[str, Any]) -> Dict[str, Any]:
    ot = output.get("output_type")
    if ot == "stream":
        return {"output_type": ot, "text": _process_output_text(output.get("text"))}
    if ot in ("execute_result", "display_data"):
        data = output.get("data") or {}
        return {
            "output_type": ot,
            "text": _process_output_text(data.get("text/plain")),
            "image": _extract_image(data),
        }
    if ot == "error":
        ename = output.get("ename", "")
        evalue = output.get("evalue", "")
        tb = output.get("traceback") or []
        tb_str = "\n".join(tb) if isinstance(tb, list) else str(tb)
        return {"output_type": ot, "text": _process_output_text(f"{ename}: {evalue}\n{tb_str}")}
    return {"output_type": str(ot or ""), "text": ""}


class K8sReadNotebookTool(CCToolBase):
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        if KubernetesManager is None:
            raise ImportError("kodo is required for K8s-backed tools.")
        config = config or {}
        self.pod_name: str = str(config.get("pod_name", "target-pod"))
        self.namespace: str = str(config.get("namespace", "default"))
        self.kubeconfig_path: Optional[str] = config.get("kubeconfig_path")
        self.timeout: float = float(config.get("timeout", 30))
        self.container: Optional[str] = config.get("container")
        self.allow_dangerous: bool = bool(config.get("allow_dangerous", False))
        super().__init__(config)
        self._k8s: Optional[KubernetesManager] = None
        self.execution_history: Dict[str, List[Dict[str, Any]]] = {}

    def get_openai_tool_schema(self) -> OpenAIFunctionToolSchema:
        return create_openai_tool_schema(
        name="ReadNotebook",
        description="Read a Jupyter notebook (.ipynb) from the Kubernetes pod and return its cells.",
        parameters={
            "notebook_path": {
                "type": "string",
                "description": "The absolute path to the Jupyter notebook file to read (must be absolute, not relative)",
            }
        },
        required=["notebook_path"],
    )

    async def _initialize_instance(self, instance_id: str, **kwargs) -> None:
        self.execution_history[instance_id] = []

    async def _cleanup_instance(self, instance_id: str, **kwargs) -> None:
        self.execution_history.pop(instance_id, None)

    async def execute_tool(self, instance_id: str, parameters: Dict[str, Any], **kwargs) -> ToolResult:
        t0 = time.time()
        try:
            if "notebook_path" not in parameters:
                raise ValueError("Invalid input: 'notebook_path' is required")
            notebook_path = parameters["notebook_path"]
            if not isinstance(notebook_path, str):
                raise ValueError("Invalid input: 'notebook_path' must be a string")
            if not notebook_path.startswith("/"):
                return ToolResult(success=False, error="File must be a Jupyter notebook (.ipynb file).", metrics={})
            if not notebook_path.endswith(".ipynb"):
                return ToolResult(success=False, error="File must be a Jupyter notebook (.ipynb file).", metrics={})
            check_cmd = f'fp={shlex.quote(notebook_path)}; [ -f "$fp" ] || echo "ENOENT"'
            chk_out, _ = await self._run_in_pod(check_cmd)
            if "ENOENT" in chk_out:
                return ToolResult(success=False, error="File does not exist.", metrics={})
            read_cmd = f'fp={shlex.quote(notebook_path)}; cat "$fp" | base64'
            b64, rc = await self._run_in_pod(read_cmd)
            duration_ms = int((time.time() - t0) * 1000)
            metrics = {
                "duration_ms": duration_ms,
                "execution_location": f"{self.namespace}/{self.pod_name}",
                "rc": rc,
                "stdout_size": len(b64),
                "in_pod_command": "sh -lc " + shlex.quote(read_cmd),
            }
            if rc != 0:
                return ToolResult(success=False, error=f"In-pod command failed with rc={rc}.", metrics=metrics)
            try:
                nb_bytes = base64.b64decode(b64.encode("ascii"), validate=False)
                nb = json.loads(nb_bytes.decode("utf-8", errors="replace"))
            except Exception:
                return ToolResult(success=False, error="Notebook is not valid JSON.", metrics=metrics)
            language = "python"
            try:
                language = nb.get("metadata", {}).get("language_info", {}).get("name") or "python"
            except Exception:
                pass
            cells_raw = nb.get("cells", [])
            cells: List[Dict[str, Any]] = []
            for idx, cell in enumerate(cells_raw):
                src = cell.get("source", "")
                if isinstance(src, list):
                    source = "".join(str(s) for s in src)
                else:
                    source = str(src)
                cell_entry: Dict[str, Any] = {
                    "cell": idx,
                    "cellType": cell.get("cell_type", "code"),
                    "source": source,
                    "language": language,
                    "execution_count": cell.get("execution_count"),
                }
                outs = cell.get("outputs", [])
                if isinstance(outs, list) and outs:
                    processed = [_process_output(o if isinstance(o, dict) else {}) for o in outs]
                    cell_entry["outputs"] = processed
                cells.append(cell_entry)
            return ToolResult(success=True, result=cells, metrics=metrics)
        except asyncio.TimeoutError:
            return ToolResult(success=False, error=f"Timeout after {self.timeout}s", metrics={})
        except Exception as e:
            logger.exception("K8sReadNotebookTool execution failed")
            return ToolResult(success=False, error=str(e), metrics={})

    def _mgr(self) -> KubernetesManager:
        if self._k8s is None:
            self._k8s = KubernetesManager(namespace=self.namespace, kubeconfig_path=self.kubeconfig_path)
        return self._k8s

    async def _run_in_pod(self, user_cmd: str) -> Tuple[str, int]:
        shell_cmd = f"sh -lc {shlex.quote(user_cmd)}"
        def _call():
            return self._mgr().execute_command(self.pod_name, shell_cmd)
        stdout, rc = await asyncio.wait_for(asyncio.to_thread(_call), timeout=self.timeout)
        return (str(stdout), int(rc))

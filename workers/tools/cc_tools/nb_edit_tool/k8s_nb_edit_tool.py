from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import shlex
import time
from typing import Any, Dict, List, Optional, Tuple

from workers.core.cc_tool_base import CCToolBase
from workers.core.tool_schemas import OpenAIFunctionToolSchema, ToolResult, create_openai_tool_schema

logger = logging.getLogger(__name__)

try:
    from kodo import KubernetesManager
except ImportError:
    KubernetesManager = None


class K8sNotebookEditCellTool(CCToolBase):
    def __init__(self, config: Optional[Dict[str, Any]] = None, tool_schema: Optional[OpenAIFunctionToolSchema] = None):
        if KubernetesManager is None:
            raise ImportError("kodo is required for K8s-backed tools.")
        config = config or {}
        self.pod_name: str = str(config.get("pod_name", "target-pod"))
        self.namespace: str = str(config.get("namespace", "default"))
        self.kubeconfig_path: Optional[str] = config.get("kubeconfig_path")
        self.timeout: float = float(config.get("timeout", 30))
        self.container: Optional[str] = config.get("container")
        self.allow_dangerous: bool = bool(config.get("allow_dangerous", False))
        super().__init__(config, tool_schema)
        self._k8s: Optional[KubernetesManager] = None
        self.execution_history: Dict[str, List[Dict[str, Any]]] = {}

    def get_openai_tool_schema(self) -> OpenAIFunctionToolSchema:
        return create_openai_tool_schema(
            name="NotebookEditCell",
            description="Edit, insert, or delete a Jupyter notebook cell inside the Kubernetes pod.",
            parameters={
                "notebook_path": {
                    "type": "string",
                    "description": "The absolute path to the Jupyter notebook file to edit (must be absolute, not relative)",
                },
                "cell_number": {
                    "type": "number",
                    "description": "The index of the cell to edit (0-based)",
                },
                "new_source": {
                    "type": "string",
                    "description": "The new source for the cell",
                },
                "cell_type": {
                    "type": "string",
                    "enum": ["code", "markdown"],
                    "description": "The type of the cell (code or markdown). If not specified, it defaults to the current cell type. If using edit_mode=insert, this is required.",
                },
                "edit_mode": {
                    "type": "string",
                    "description": "The type of edit to make (replace, insert, delete). Defaults to replace.",
                },
            },
            required=["notebook_path", "cell_number", "new_source"],
        )

    async def _initialize_instance(self, instance_id: str, **kwargs) -> None:
        self.execution_history[instance_id] = []

    async def _cleanup_instance(self, instance_id: str, **kwargs) -> None:
        self.execution_history.pop(instance_id, None)

    async def execute_tool(self, instance_id: str, parameters: Dict[str, Any], **kwargs) -> ToolResult:
        t0 = time.time()
        notebook_path = parameters.get("notebook_path")
        cell_number = parameters.get("cell_number")
        new_source = parameters.get("new_source")
        cell_type = parameters.get("cell_type")
        edit_mode = parameters.get("edit_mode") or "replace"
        try:
            if not isinstance(notebook_path, str):
                return self._result_with_error(cell_number, new_source, cell_type, "python", edit_mode, "Notebook file does not exist.", t0)
            if not isinstance(cell_number, (int, float)) or int(cell_number) != cell_number:
                return self._result_with_error(0, new_source, cell_type, "python", edit_mode, "Cell number must be non-negative.", t0)
            cell_number = int(cell_number)
            if cell_number < 0:
                return self._result_with_error(cell_number, new_source, cell_type, "python", edit_mode, "Cell number must be non-negative.", t0)
            if not isinstance(new_source, str):
                return self._result_with_error(cell_number, "", cell_type, "python", edit_mode, "Notebook is not valid JSON.", t0)
            if not notebook_path.startswith("/"):
                return self._result_with_error(cell_number, new_source, cell_type, "python", edit_mode, "The absolute path to the Jupyter notebook file to edit (must be absolute, not relative)", t0)
            if os.path.splitext(notebook_path)[1] != ".ipynb":
                return self._result_with_error(
                    cell_number,
                    new_source,
                    cell_type,
                    "python",
                    edit_mode,
                    "File must be a Jupyter notebook (.ipynb file). For editing other file types, use the FileEdit tool.",
                    t0,
                )
            check_cmd = f'fp={shlex.quote(notebook_path)}; [ -f "$fp" ] || echo "ENOENT"'
            stdout_check, rc_check = await self._run_in_pod(check_cmd)
            if "ENOENT" in stdout_check:
                return self._result_with_error(cell_number, new_source, cell_type, "python", edit_mode, "Notebook file does not exist.", t0)
            read_cmd = f'fp={shlex.quote(notebook_path)}; base64 "$fp"'
            b64, rc = await self._run_in_pod(read_cmd)
            if rc != 0:
                return self._result_with_error(cell_number, new_source, cell_type, "python", edit_mode, f"In-pod command failed with rc={rc}.", t0, rc=rc, stdout_len=len(b64), cmd=read_cmd)
            nb_bytes = base64.b64decode(b64.encode("ascii"), validate=False)
            try:
                nb = json.loads(nb_bytes.decode("utf-8", errors="replace"))
            except Exception:
                return self._result_with_error(cell_number, new_source, cell_type, "python", edit_mode, "Notebook is not valid JSON.", t0)
            language = "python"
            try:
                language = nb.get("metadata", {}).get("language_info", {}).get("name") or "python"
            except Exception:
                pass
            if edit_mode not in ("replace", "insert", "delete"):
                return self._result_with_error(cell_number, new_source, cell_type, language, edit_mode, "Edit mode must be replace, insert, or delete.", t0)
            cells = nb.get("cells", [])
            if not isinstance(cells, list):
                return self._result_with_error(cell_number, new_source, cell_type, language, edit_mode, "Notebook is not valid JSON.", t0)
            if edit_mode == "insert" and not cell_type:
                return self._result_with_error(cell_number, new_source, cell_type, language, edit_mode, "Cell type is required when using edit_mode=insert.", t0)
            if edit_mode == "insert":
                if cell_number > len(cells):
                    return self._result_with_error(
                        cell_number,
                        new_source,
                        cell_type,
                        language,
                        edit_mode,
                        f"Cell number is out of bounds. For insert mode, the maximum value is {len(cells)} (to append at the end).",
                        t0,
                    )
                new_cell = {"cell_type": cell_type, "source": new_source, "metadata": {}}
                if cell_type != "markdown":
                    new_cell["outputs"] = []
                cells[cell_number:cell_number] = [new_cell]
            elif edit_mode == "delete":
                if cell_number >= len(cells) or cell_number < 0 or not cells[cell_number:cell_number+1]:
                    return self._result_with_error(
                        cell_number, new_source, cell_type, language, edit_mode, f"Cell number is out of bounds. Notebook has {len(cells)} cells.", t0
                    )
                del cells[cell_number]
            else:
                if cell_number >= len(cells) or cell_number < 0 or not cells[cell_number:cell_number+1]:
                    return self._result_with_error(
                        cell_number, new_source, cell_type, language, edit_mode, f"Cell number is out of bounds. Notebook has {len(cells)} cells.", t0
                    )
                target = cells[cell_number]
                target["source"] = new_source
                target["execution_count"] = None
                target["outputs"] = []
                if cell_type and cell_type != target.get("cell_type"):
                    target["cell_type"] = cell_type
            nb["cells"] = cells
            serialized = json.dumps(nb, indent=1)
            b64_out = base64.b64encode(serialized.encode("utf-8")).decode("ascii")
            write_cmd = f'fp={shlex.quote(notebook_path)}; echo {shlex.quote(b64_out)} | base64 -d > "$fp"'
            stdout_w, rc_w = await self._run_in_pod(write_cmd)
            duration_ms = int((time.time() - t0) * 1000)
            metrics = {
                "duration_ms": duration_ms,
                "execution_location": f"{self.namespace}/{self.pod_name}",
                "rc": rc_w,
                "stdout_size": len(stdout_w),
                "in_pod_command": "sh -lc " + shlex.quote(write_cmd),
            }
            if rc_w != 0:
                return ToolResult(success=True, result={
                    "cell_number": cell_number,
                    "new_source": new_source,
                    "cell_type": (cell_type or "code"),
                    "language": language,
                    "edit_mode": edit_mode,
                    "error": f"In-pod command failed with rc={rc_w}.",
                }, metrics=metrics)
            return ToolResult(success=True, result={
                "cell_number": cell_number,
                "new_source": new_source,
                "cell_type": (cell_type or "code"),
                "language": language,
                "edit_mode": edit_mode,
                "error": "",
            }, metrics=metrics)
        except asyncio.TimeoutError:
            return ToolResult(success=False, error=f"Timeout after {self.timeout}s", metrics={})
        except Exception as e:
            logger.exception("K8sNotebookEditCellTool execution failed")
            return ToolResult(success=True, result={
                "cell_number": parameters.get("cell_number", 0),
                "new_source": parameters.get("new_source", ""),
                "cell_type": parameters.get("cell_type") or "code",
                "language": "python",
                "edit_mode": parameters.get("edit_mode") or "replace",
                "error": str(e),
            }, metrics={})

    def _result_with_error(self, cell_number: int, new_source: str, cell_type: Optional[str], language: str, edit_mode: str, msg: str, t0: float, rc: int = 0, stdout_len: int = 0, cmd: Optional[str] = None) -> ToolResult:
        duration_ms = int((time.time() - t0) * 1000)
        metrics = {
            "duration_ms": duration_ms,
            "execution_location": f"{self.namespace}/{self.pod_name}",
            "rc": rc,
            "stdout_size": stdout_len,
        }
        if cmd:
            metrics["in_pod_command"] = "sh -lc " + shlex.quote(cmd)
        return ToolResult(success=True, result={
            "cell_number": int(cell_number) if isinstance(cell_number, int) else 0,
            "new_source": new_source or "",
            "cell_type": (cell_type or "code"),
            "language": language or "python",
            "edit_mode": edit_mode or "replace",
            "error": msg,
        }, metrics=metrics)

    def _mgr(self) -> KubernetesManager:
        if self._k8s is None:
            self._k8s = KubernetesManager(namespace=self.namespace, kubeconfig_path=self.kubeconfig_path)
        return self._k8s

    async def _run_in_pod(self, user_cmd: str) -> Tuple[str, int]:
        shell_cmd = f"sh -lc {shlex.quote(user_cmd)}"
        def _call():
            return self._mgr().execute_command(self.pod_name, shell_cmd)
        stdout, rc = await asyncio.wait_for(asyncio.to_thread(_call), timeout=self.timeout)
        if isinstance(rc, int):
            rc_int = rc
        elif isinstance(rc, str):
            m = re.search(r'(-?\d+)', rc)
            rc_int = int(m.group(1)) if m else 1
        else:
            rc_int = 1
        return (str(stdout), rc_int)

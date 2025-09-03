from __future__ import annotations

import asyncio
import base64
import logging
import posixpath
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


class K8sMemoryReadTool(CCToolBase):
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
        self.memory_dir: str = str(config.get("memory_dir", "/memory"))
        super().__init__(config, tool_schema)
        self._k8s: Optional[KubernetesManager] = None
        self.execution_history: Dict[str, List[Dict[str, Any]]] = {}

    def get_openai_tool_schema(self) -> OpenAIFunctionToolSchema:
        return create_openai_tool_schema(
            name="MemoryRead",
            description="Read memory files from a directory inside the Kubernetes pod.",
            parameters={
                "file_path": {
                    "type": "string",
                    "description": "Optional path to a specific memory file to read",
                }
            },
            required=[],
        )

    async def _initialize_instance(self, instance_id: str, **kwargs) -> None:
        self.execution_history[instance_id] = []

    async def _cleanup_instance(self, instance_id: str, **kwargs) -> None:
        self.execution_history.pop(instance_id, None)

    async def execute_tool(self, instance_id: str, parameters: Dict[str, Any], **kwargs) -> ToolResult:
        t0 = time.time()
        try:
            file_path = parameters.get("file_path")
            if file_path is not None and not isinstance(file_path, str):
                raise ValueError("Invalid input: 'file_path' must be a string")
            mem = self.memory_dir if self.memory_dir.endswith("/") else self.memory_dir + "/"
            if not mem.startswith("/"):
                mem = "/" + mem.lstrip("/")
            if file_path:
                sanitized = file_path.lstrip("/")
                full_path = posixpath.normpath(mem + sanitized)
                if not (full_path == mem.rstrip("/") or full_path.startswith(mem)):
                    return ToolResult(success=False, error="Invalid memory file path", metrics={})
                mem_q = shlex.quote(mem.rstrip("/"))
                fp_q = shlex.quote(full_path)
                user_cmd = f'mem={mem_q}; mkdir -p "$mem"; fp={fp_q}; if [ ! -f "$fp" ]; then echo "ENOENT"; exit 3; fi; cat "$fp"'
                stdout, rc = await self._run_in_pod(user_cmd)
                duration_ms = int((time.time() - t0) * 1000)
                metrics = {
                    "duration_ms": duration_ms,
                    "execution_location": f"{self.namespace}/{self.pod_name}",
                    "rc": rc,
                    "stdout_size": len(stdout),
                    "in_pod_command": "sh -lc " + shlex.quote(user_cmd),
                }
                if rc != 0:
                    if stdout.strip() == "ENOENT":
                        return ToolResult(success=False, error="Memory file does not exist", metrics=metrics)
                    return ToolResult(success=False, error=f"In-pod command failed with rc={rc}.", metrics=metrics)
                return ToolResult(success=True, result={"content": stdout}, metrics={**metrics})
            mem_q = shlex.quote(mem.rstrip("/"))
            idx_marker = "__IDX__"
            files_marker = "__FILES__"
            user_cmd = (
                f'mem={mem_q}; mkdir -p "$mem"; idx="$mem/index.md"; '
                f'{{ echo {shlex.quote(idx_marker)}; if [ -f "$idx" ]; then base64 "$idx" | tr -d "\\n"; else echo -n ""; fi; echo; '
                f'echo {shlex.quote(files_marker)}; find "$mem" -type f -print 2>/dev/null | sort; }}'
            )
            stdout, rc = await self._run_in_pod(user_cmd)
            duration_ms = int((time.time() - t0) * 1000)
            metrics = {
                "duration_ms": duration_ms,
                "execution_location": f"{self.namespace}/{self.pod_name}",
                "rc": rc,
                "stdout_size": len(stdout),
                "in_pod_command": "sh -lc " + shlex.quote(user_cmd),
            }
            if rc != 0:
                return ToolResult(success=False, error=f"In-pod command failed with rc={rc}.", metrics=metrics)
            lines = stdout.splitlines()
            try:
                i_idx = lines.index(idx_marker)
                i_files = lines.index(files_marker)
            except ValueError:
                return ToolResult(success=False, error="Unexpected pod output format.", metrics=metrics)
            idx_b64 = "".join(lines[i_idx + 1 : i_files]).strip()
            index_bytes = base64.b64decode(idx_b64) if idx_b64 else b""
            index_text = index_bytes.decode("utf-8", errors="replace")
            file_lines = lines[i_files + 1 :]
            files_text = "\n".join(f"- {p}" for p in file_lines)
            index_path = posixpath.join(mem.rstrip("/"), "index.md")
            quotes = "'''"
            content = (
                f"Here are the contents of the root memory file, `{index_path}`:\n"
                f"{quotes}\n"
                f"{index_text}\n"
                f"{quotes}\n\n"
                f"Files in the memory directory:\n"
                f"{files_text}"
            )
            return ToolResult(success=True, result={"content": content}, metrics={**metrics})
        except asyncio.TimeoutError:
            return ToolResult(success=False, error=f"Timeout after {self.timeout}s", metrics={})
        except Exception as e:
            logger.exception("K8sMemoryReadTool execution failed")
            return ToolResult(success=False, error=str(e), metrics={})

    def _mgr(self) -> KubernetesManager:
        if self._k8s is None:
            self._k8s = KubernetesManager(namespace=self.namespace, kubeconfig_path=self.kubeconfig_path)
        return self._k8s

    async def _run_in_pod(self, user_cmd: str) -> Tuple[str, int]:
        shell_cmd = f"sh -lc {shlex.quote(user_cmd)}"

        def _call():
            return self._mgr().execute_command(self.pod_name, shell_cmd)

        res = await asyncio.wait_for(asyncio.to_thread(_call), timeout=self.timeout)

        if isinstance(res, tuple) and len(res) >= 2:
            stdout, rc = res[0], res[1]
        elif isinstance(res, dict):
            stdout = res.get("stdout", "")
            rc = res.get("rc", res.get("exit_code", 0))
        else:
            stdout, rc = res, 0

        if isinstance(stdout, (bytes, bytearray)):
            stdout = stdout.decode("utf-8", errors="replace")
        else:
            stdout = str(stdout)

        if not isinstance(rc, int):
            m = re.search(r"(-?\d+)", str(rc))
            rc = int(m.group(1)) if m else 1

        return (stdout, rc)

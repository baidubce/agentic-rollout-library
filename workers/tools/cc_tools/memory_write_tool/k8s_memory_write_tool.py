from __future__ import annotations

import asyncio
import base64
import logging
import posixpath
import shlex
import time
from typing import Any, Dict, List, Optional, Tuple

from workers.core.cc_tool_base import CCToolBase
from workers.core.tool_schemas import (
    OpenAIFunctionToolSchema,
    ToolResult,
    create_openai_tool_schema,
)

logger = logging.getLogger(__name__)

try:
    from kodo import KubernetesManager
except ImportError:
    KubernetesManager = None  # type: ignore[misc,assignment]


class K8sMemoryWriteTool(CCToolBase):
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
        """
        Return the OpenAI tool schema for this tool.

        NOTE:
        - `create_openai_tool_schema` expects `parameters` to be the *properties* dict only.
        - Do not pass a full JSON Schema object (i.e., without top-level `type`, `required`, etc.).
        """
        return create_openai_tool_schema(
            name="MemoryWrite",
            description="Write content to a memory file inside the Kubernetes pod.",
            parameters={
                "file_path": {
                    "type": "string",
                    "description": "Path to the memory file to write",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write to the file",
                },
            },
            required=["file_path", "content"],
        )

    async def _initialize_instance(self, instance_id: str, **kwargs) -> None:
        self.execution_history[instance_id] = []

    async def _cleanup_instance(self, instance_id: str, **kwargs) -> None:
        self.execution_history.pop(instance_id, None)

    async def execute_tool(
        self, instance_id: str, parameters: Dict[str, Any], **kwargs
    ) -> ToolResult:
        t0 = time.time()
        try:
            if "file_path" not in parameters:
                raise ValueError("Invalid input: 'file_path' is required")
            if "content" not in parameters:
                raise ValueError("Invalid input: 'content' is required")

            file_path = parameters["file_path"]
            content = parameters["content"]

            if not isinstance(file_path, str):
                raise ValueError("Invalid input: 'file_path' must be a string")
            if not isinstance(content, str):
                raise ValueError("Invalid input: 'content' must be a string")

            mem = self.memory_dir if self.memory_dir.endswith("/") else self.memory_dir + "/"
            if not mem.startswith("/"):
                mem = "/" + mem.lstrip("/")
            sanitized = file_path.lstrip("/")
            full_path = posixpath.normpath(mem + sanitized)

            # Ensure the final path is inside memory_dir
            if not (full_path == mem.rstrip("/") or full_path.startswith(mem)):
                return ToolResult(success=False, error="Invalid memory file path", metrics={})

            b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
            fp_q = shlex.quote(full_path)
            b64_q = shlex.quote(b64)

            user_cmd = (
                f'fp={fp_q}; '
                f'dir="${{fp%/*}}"; '
                f'[ -n "$dir" ] && mkdir -p "$dir"; '
                f'echo {b64_q} | base64 -d > "$fp"'
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
            return ToolResult(success=True, result="Saved", metrics=metrics)

        except asyncio.TimeoutError:
            return ToolResult(success=False, error=f"Timeout after {self.timeout}s", metrics={})
        except Exception as e:
            logger.exception("K8sMemoryWriteTool execution failed")
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
        return str(stdout), int(rc)

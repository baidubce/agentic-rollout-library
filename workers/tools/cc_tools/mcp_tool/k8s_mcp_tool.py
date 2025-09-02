from __future__ import annotations

import asyncio
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


class K8sMCPTool(CCToolBase):
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
            name="mcp",
            description="Model Context Protocol passthrough tool.",
            parameters={},
            required=[],
        )

    async def _initialize_instance(self, instance_id: str, **kwargs) -> None:
        self.execution_history[instance_id] = []

    async def _cleanup_instance(self, instance_id: str, **kwargs) -> None:
        self.execution_history.pop(instance_id, None)

    async def execute_tool(self, instance_id: str, parameters: Dict[str, Any], **kwargs) -> ToolResult:
        t0 = time.time()
        try:
            user_cmd = 'echo "ACK:mcp"'
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
            return ToolResult(success=True, result="", metrics=metrics)
        except asyncio.TimeoutError:
            return ToolResult(success=False, error=f"Timeout after {self.timeout}s", metrics={})
        except Exception as e:
            logger.exception("K8sMCPTool execution failed")
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

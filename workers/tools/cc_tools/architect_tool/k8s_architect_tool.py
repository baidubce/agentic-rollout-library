from __future__ import annotations

import asyncio
import base64
import logging
import re
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


def _format_duration_ms(ms: float) -> str:
    if ms < 1000:
        return f"{ms:.0f}ms"
    return f"{ms/1000.0:.2f}s"


def _format_int(n: int) -> str:
    return f"{n:,}"


class K8sArchitectTool(CCToolBase):
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
        # IMPORTANT: pass only the "properties" mapping; "required" separately.
        return create_openai_tool_schema(
            name="Architect",
            description="Analyze a technical request or coding task with optional context.",
            parameters={
                "prompt": {
                    "type": "string",
                    "description": "The technical request or coding task to analyze",
                },
                "context": {
                    "type": "string",
                    "description": "Optional context from previous conversation or system state",
                },
            },
            required=["prompt"],
        )

    async def _initialize_instance(self, instance_id: str, **kwargs) -> None:
        self.execution_history[instance_id] = []

    async def _cleanup_instance(self, instance_id: str, **kwargs) -> None:
        self.execution_history.pop(instance_id, None)

    async def execute_tool(self, instance_id: str, parameters: Dict[str, Any], **kwargs) -> ToolResult:
        t0 = time.time()
        try:
            if "prompt" not in parameters:
                raise ValueError("Invalid input: 'prompt' is required")
            prompt = parameters["prompt"]
            if not isinstance(prompt, str):
                raise ValueError("Invalid input: 'prompt' must be a string")
            context = parameters.get("context")
            if context is not None and not isinstance(context, str):
                raise ValueError("Invalid input: 'context' must be a string")

            content = f"<context>{context}</context>\n\n{prompt}" if context else prompt
            payload = base64.b64encode(content.encode("utf-8")).decode("ascii")
            user_cmd = f'echo {shlex.quote(payload)} | base64 -d >/dev/null 2>&1; echo "ACK:architect"'

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

            summary = f"Done (analysis staged · {_format_int(len(content))} chars · {_format_duration_ms(duration_ms)})"
            result_obj: List[Dict[str, str]] = [{"type": "text", "text": summary}]
            return ToolResult(success=True, result=result_obj, metrics=metrics)

        except asyncio.TimeoutError:
            return ToolResult(success=False, error=f"Timeout after {self.timeout}s", metrics={})
        except Exception as e:
            logger.exception("K8sArchitectTool execution failed")
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

        # Normalize return shapes from different KubernetesManager implementations
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

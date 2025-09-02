from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
import time
from typing import Any, Dict, List, Optional

try:
    from kodo import KubernetesManager
except ImportError:
    KubernetesManager = None  # type: ignore

from workers.core.enhanced_base_tool import CCToolBase
from workers.core.tool_schemas import (
    OpenAIFunctionToolSchema,
    ToolResult,
    create_openai_tool_schema,
)

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SEC = 30
MAX_FILES = 1000


class K8sGlobTool(CCToolBase):
    """
    Kubernetes-based file pattern matching tool using glob patterns.
    Converts from Claude-Code GlobTool.tsx
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        if KubernetesManager is None:
            raise ImportError("kodo library is required for K8s tools. Please install it before using K8sGlobTool.")
        
        config = config or {}
        self.pod_name: str = config.get("pod_name", "default-pod")
        self.namespace: str = config.get("namespace", "default")
        self.kubeconfig_path: Optional[str] = config.get("kubeconfig_path")
        self.timeout: float = float(config.get("timeout", DEFAULT_TIMEOUT_SEC))
        self.container: Optional[str] = config.get("container")
        self.allow_dangerous: bool = bool(config.get("allow_dangerous", False))
        self.allowed_root: str = config.get("allowed_root", "/")
        self.base_dir: str = config.get("base_dir", "/workspace")
        
        super().__init__(config)
        self._k8s_mgr: Optional[KubernetesManager] = None
        self.execution_history: Dict[str, list] = {}

    def get_openai_tool_schema(self) -> OpenAIFunctionToolSchema:
        return create_openai_tool_schema(
            name="k8s_glob",
            description=(
                "Fast file pattern matching tool that works with any codebase size. "
                f"Executes inside '{self.namespace}/{self.pod_name}'. "
                "Supports glob patterns like '**/*.js' or 'src/**/*.ts'. "
                "Returns matching file paths sorted by modification time."
            ),
            parameters={
                "pattern": {
                    "type": "string",
                    "description": "The glob pattern to match files against (e.g., '**/*.py', 'src/**/*.ts')",
                },
                "path": {
                    "type": "string", 
                    "description": "The directory to search in. Defaults to current working directory if not specified.",
                },
            },
            required=["pattern"],
        )

    async def _initialize_instance(self, instance_id: str, **kwargs) -> None:
        self.execution_history[instance_id] = []

    async def _cleanup_instance(self, instance_id: str, **kwargs) -> None:
        self.execution_history.pop(instance_id, None)

    def _get_k8s_manager(self) -> KubernetesManager:
        if self._k8s_mgr is None:
            if self.kubeconfig_path:
                self._k8s_mgr = KubernetesManager(
                    namespace=self.namespace,
                    kubeconfig_path=self.kubeconfig_path
                )
            else:
                self._k8s_mgr = KubernetesManager(namespace=self.namespace)
        return self._k8s_mgr

    def _normalize_path(self, path: str) -> str:
        """Normalize path to absolute path within pod"""
        path = path.strip()
        if not path.startswith("/"):
            # Convert relative to absolute based on base_dir
            path = os.path.join(self.base_dir, path)
        return os.path.normpath(path)

    def _is_within_allowed_root(self, path: str) -> bool:
        """Check if path is within allowed root directory"""
        root = os.path.normpath(self.allowed_root)
        normalized_path = os.path.normpath(path)
        if root == "/":
            return True
        return normalized_path == root or normalized_path.startswith(root + "/")

    def _has_injection_tokens(self, s: str) -> bool:
        """Check for command injection tokens"""
        return any(tok in s for tok in [";", "|", "&", "`", "$(", ")", "<", ">", "\n", "\r"])

    async def _run_k8s_command(self, command: str) -> Dict[str, Any]:
        """Execute command in Kubernetes pod"""
        mgr = self._get_k8s_manager()
        try:
            # Try with timeout first
            try:
                result = mgr.execute_command(self.pod_name, command, timeout_ms=int(self.timeout * 1000))
            except TypeError:
                # Fallback without timeout parameter
                result = mgr.execute_command(self.pod_name, command)

            stdout, stderr, rc = "", "", -1
            if isinstance(result, tuple):
                if len(result) == 3:
                    stdout, stderr, rc = result
                elif len(result) == 2:
                    stdout, rc = result
                    stderr = ""
            else:
                stdout = str(result)
                rc = 0

            return {
                "stdout": stdout,
                "stderr": stderr,
                "return_code": rc,
                "command": command,
                "pod_name": self.pod_name,
                "namespace": self.namespace,
            }
        except Exception as e:
            logger.error(f"K8s command execution failed: {e}")
            return {
                "stdout": "",
                "stderr": str(e),
                "return_code": -1,
                "command": command,
                "pod_name": self.pod_name,
                "namespace": self.namespace,
            }

    async def execute_tool(self, instance_id: str, parameters: Dict[str, Any], **kwargs) -> ToolResult:
        t0 = time.time()
        try:
            pattern = parameters.get("pattern")
            search_path = parameters.get("path", self.base_dir)

            if not isinstance(pattern, str) or not pattern.strip():
                return ToolResult(success=False, error="`pattern` must be a non-empty string.")

            if not self.allow_dangerous and self._has_injection_tokens(pattern):
                return ToolResult(success=False, error="`pattern` contains disallowed characters.")

            # Normalize the search path
            search_path = self._normalize_path(search_path)
            if not self._is_within_allowed_root(search_path):
                return ToolResult(success=False, error="Access to the requested path is not allowed.")

            # Use find command with shell patterns for glob functionality
            # Convert glob pattern to find-compatible pattern
            escaped_pattern = shlex.quote(pattern)
            escaped_path = shlex.quote(search_path)
            
            # Use find with -name pattern for basic glob support
            # For more complex patterns, we'll use shell globbing
            if "**" in pattern:
                # Recursive pattern - use find with globbing
                find_pattern = pattern.replace("**", "*")
                command = f"cd {escaped_path} && find . -type f -name {shlex.quote(find_pattern)} | head -{MAX_FILES} | sort"
            else:
                # Simple pattern - use find with direct pattern
                command = f"cd {escaped_path} && find . -maxdepth 1 -type f -name {escaped_pattern} | head -{MAX_FILES} | sort"

            result = await self._run_k8s_command(command)
            
            if result["return_code"] != 0:
                return ToolResult(
                    success=False, 
                    error=f"Glob command failed: {result['stderr'] or 'Unknown error'}"
                )

            # Parse output into file list
            stdout = result["stdout"].strip()
            if not stdout:
                filenames = []
            else:
                # Remove leading "./" and filter out empty lines
                filenames = [
                    line.lstrip("./") for line in stdout.split("\n") 
                    if line.strip() and not line.startswith("find:")
                ]

            duration_ms = int((time.time() - t0) * 1000)
            num_files = len(filenames)
            truncated = num_files >= MAX_FILES

            # Build result
            result_data = {
                "pattern": pattern,
                "search_path": search_path,
                "duration_ms": duration_ms,
                "num_files": num_files,
                "filenames": filenames,
                "truncated": truncated,
                "execution_location": f"{self.namespace}/{self.pod_name}",
            }

            # Format output for assistant
            if not filenames:
                content = f"No files found matching pattern '{pattern}' in {search_path}"
            else:
                content = f"Found {num_files} files matching pattern '{pattern}' in {search_path}"
                if truncated:
                    content += f" (truncated to first {MAX_FILES})"
                content += ":\n\n" + "\n".join(filenames)

            # Log execution
            self.execution_history[instance_id].append({
                "action": "glob_search",
                "pattern": pattern,
                "search_path": search_path,
                "num_files": num_files,
                "duration_ms": duration_ms,
                "truncated": truncated,
            })

            return ToolResult(
                success=True,
                content=content,
                result=result_data
            )

        except Exception as e:
            duration_ms = int((time.time() - t0) * 1000)
            logger.error("K8sGlobTool execution failed", exc_info=True)
            return ToolResult(
                success=False,
                error=f"Glob operation failed: {str(e)}",
                result={"duration_ms": duration_ms, "error": str(e)}
            )

    def _append_history(self, instance_id: str, entry: Dict[str, Any]) -> None:
        """Append execution history entry"""
        if instance_id not in self.execution_history:
            self.execution_history[instance_id] = []
        self.execution_history[instance_id].append(entry)



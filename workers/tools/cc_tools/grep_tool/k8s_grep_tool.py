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
MAX_RESULTS = 100


class K8sGrepTool(CCToolBase):
    """
    Kubernetes-based content search tool using grep/ripgrep patterns.
    Converts from Claude-Code GrepTool.tsx
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        if KubernetesManager is None:
            raise ImportError("kodo library is required for K8s tools. Please install it before using K8sGrepTool.")
        
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
            name="k8s_grep",
            description=(
                "Fast content search tool that works with any codebase size. "
                f"Executes inside '{self.namespace}/{self.pod_name}'. "
                "Searches file contents using regular expressions. "
                "Supports full regex syntax and file filtering."
            ),
            parameters={
                "pattern": {
                    "type": "string",
                    "description": "The regular expression pattern to search for in file contents",
                },
                "path": {
                    "type": "string",
                    "description": "The directory to search in. Defaults to current working directory if not specified.",
                },
                "include": {
                    "type": "string",
                    "description": "File pattern to include in the search (e.g., '*.js', '*.{ts,tsx}'). Optional.",
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

    def _build_grep_command(self, pattern: str, search_path: str, include_pattern: Optional[str] = None) -> str:
        """Build grep command with proper escaping"""
        escaped_pattern = shlex.quote(pattern)
        escaped_path = shlex.quote(search_path)
        
        # Try to use ripgrep (rg) if available, fallback to grep
        base_cmd = f"cd {escaped_path} && "
        
        # Check if ripgrep is available first
        check_rg_cmd = "command -v rg >/dev/null 2>&1"
        
        if include_pattern:
            escaped_include = shlex.quote(include_pattern)
            # ripgrep command
            rg_cmd = f"rg --type-add 'custom:{escaped_include}' -t custom -n --no-heading -r '${{0}}' {escaped_pattern} . | head -{MAX_RESULTS}"
            # grep fallback command  
            grep_cmd = f"find . -name {escaped_include} -type f -exec grep -n {escaped_pattern} {{}} + | head -{MAX_RESULTS}"
        else:
            # ripgrep command - search all files
            rg_cmd = f"rg -n --no-heading -r '${{0}}' {escaped_pattern} . | head -{MAX_RESULTS}"
            # grep fallback command
            grep_cmd = f"find . -type f -exec grep -n {escaped_pattern} {{}} + | head -{MAX_RESULTS}"
        
        # Combine: try rg first, fallback to grep
        full_cmd = f"{base_cmd}({check_rg_cmd} && {rg_cmd}) || {grep_cmd}"
        
        return full_cmd

    async def execute_tool(self, instance_id: str, parameters: Dict[str, Any], **kwargs) -> ToolResult:
        t0 = time.time()
        try:
            pattern = parameters.get("pattern")
            search_path = parameters.get("path", self.base_dir)
            include_pattern = parameters.get("include")

            if not isinstance(pattern, str) or not pattern.strip():
                return ToolResult(success=False, error="`pattern` must be a non-empty string.")

            if not self.allow_dangerous and self._has_injection_tokens(pattern):
                return ToolResult(success=False, error="`pattern` contains disallowed characters.")

            # Normalize the search path
            search_path = self._normalize_path(search_path)
            if not self._is_within_allowed_root(search_path):
                return ToolResult(success=False, error="Access to the requested path is not allowed.")

            # Build and execute grep command
            command = self._build_grep_command(pattern, search_path, include_pattern)
            result = await self._run_k8s_command(command)
            
            if result["return_code"] != 0 and result["return_code"] != 1:  # grep returns 1 when no matches
                # Only error on actual command failures, not "no results"
                if "No such file" in result["stderr"] or "command not found" in result["stderr"]:
                    return ToolResult(
                        success=False, 
                        error=f"Grep command failed: {result['stderr']}"
                    )

            # Parse output into matches
            stdout = result["stdout"].strip()
            matches = []
            filenames = set()
            
            if stdout:
                for line in stdout.split("\n"):
                    line = line.strip()
                    if line and ":" in line:
                        # Parse grep output: filename:line_number:content
                        parts = line.split(":", 2)
                        if len(parts) >= 2:
                            filename = parts[0].lstrip("./")
                            filenames.add(filename)
                            matches.append(line)

            duration_ms = int((time.time() - t0) * 1000)
            num_files = len(filenames)
            num_matches = len(matches)
            truncated = num_matches >= MAX_RESULTS

            # Build result
            result_data = {
                "pattern": pattern,
                "search_path": search_path,
                "include_pattern": include_pattern,
                "duration_ms": duration_ms,
                "num_files": num_files,
                "num_matches": num_matches,
                "filenames": list(filenames),
                "matches": matches,
                "truncated": truncated,
                "execution_location": f"{self.namespace}/{self.pod_name}",
            }

            # Format output for assistant
            if not matches:
                content = f"No matches found for pattern '{pattern}' in {search_path}"
                if include_pattern:
                    content += f" (filtered to {include_pattern})"
            else:
                content = f"Found {num_matches} matches for pattern '{pattern}' in {num_files} files"
                if include_pattern:
                    content += f" (filtered to {include_pattern})"
                if truncated:
                    content += f" (truncated to first {MAX_RESULTS})"
                content += f" in {search_path}:\n\n" + "\n".join(matches)

            # Log execution
            self.execution_history[instance_id].append({
                "action": "grep_search",
                "pattern": pattern,
                "search_path": search_path,
                "include_pattern": include_pattern,
                "num_files": num_files,
                "num_matches": num_matches,
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
            logger.error("K8sGrepTool execution failed", exc_info=True)
            return ToolResult(
                success=False,
                error=f"Grep operation failed: {str(e)}",
                result={"duration_ms": duration_ms, "error": str(e)}
            )

    def _append_history(self, instance_id: str, entry: Dict[str, Any]) -> None:
        """Append execution history entry"""
        if instance_id not in self.execution_history:
            self.execution_history[instance_id] = []
        self.execution_history[instance_id].append(entry)



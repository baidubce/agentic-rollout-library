import logging
import json
import os
import re
import time
from urllib.parse import unquote

from workers.core.enhanced_base_tool import CCToolBase
from workers.core.tool_schemas import ToolResult, create_openai_tool_schema

try:
    from kodo import KubernetesManager
except ImportError:
    KubernetesManager = None

logger = logging.getLogger(__name__)


class K8sFileWriteTool(CCToolBase):
    """
    Tool to write a file to the filesystem in a Kubernetes pod.
    Inspired by a TypeScript FileWriteTool and architected like K8sFileEditorTool.
    """

    def __init__(self, config: dict = None):
        """Initializes the K8s file write tool."""
        if KubernetesManager is None:
            raise ImportError("The 'kodo' library is required to use K8s tools.")

        config = config or {}
        self.pod_name = config.get("pod_name", "target-pod")
        self.namespace = config.get("namespace", "default")
        self.kubeconfig_path = config.get("kubeconfig_path", None)

        super().__init__(config)

        self.max_lines_for_assistant = 16000
        self.truncated_message = (
            '<response clipped><NOTE>To save on context only part of this file has been shown to you. '
            'You should retry this tool after you have searched inside the file with Grep in order to find '
            'the line numbers of what you are looking for.</NOTE>'
        )
        self.k8s_manager = None
        self.allowed_base_dirs = config.get("allowed_base_dirs", ["/app", "/tmp", "/var/www/html", "/etc/config", "/home/user"])
        self.invalid_path_chars_pattern = re.compile(r"[<>|:&;`$()]")

    def get_openai_tool_schema(self):
        """Returns the OpenAI tool schema, matching the TypeScript version's input."""
        return create_openai_tool_schema(
            name="Replace",
            description="Write a file to the local filesystem.",
            parameters={
                "file_path": {
                    "type": "string",
                    "description": "The absolute path to the file to write (must be absolute, not relative)",
                },
                "content": {
                    "type": "string",
                    "description": "The content to write to the file",
                },
            },
            required=["file_path", "content"],
        )

    def _get_k8s_manager(self):
        """
        Gets or creates the KubernetesManager instance. This lazy singleton pattern
        proved to be the most stable way to interact with the kodo library.
        """
        if self.k8s_manager is None:
            self.k8s_manager = KubernetesManager(
                namespace=self.namespace,
                kubeconfig_path=self.kubeconfig_path,
            )
        return self.k8s_manager

    async def _safe_execute_command(self, command: str, retries: int = 2, delay: float = 0.5) -> tuple[str, int]:
        """
        A robust wrapper for kodo's execute_command that includes retries
        and correctly parses all known inconsistent return formats from kodo.
        """
        last_exception = None
        for attempt in range(retries + 1):
            try:
                manager = self._get_k8s_manager()
                output, exit_code_raw = manager.execute_command(self.pod_name, command)
                exit_code_str = str(exit_code_raw)
                try:
                    return output, int(exit_code_str)
                except ValueError:
                    match = re.search(r'-?\d+', exit_code_str)
                    if match:
                        return output, int(match.group(0))
                    else:
                        logger.error(f"Kodo returned a non-parsable error. Command: '{command}', Raw Exit: {exit_code_raw}")
                        return output, -1
            except Exception as e:
                last_exception = e
                logger.warning(f"Attempt {attempt + 1} failed for command '{command}'. Error: {e}. Retrying in {delay}s...")
                time.sleep(delay)
        
        logger.error(f"Kodo command execution failed after {retries} retries. Command: '{command}', Last Error: {last_exception}")
        return str(last_exception), -1

    def _validate_and_normalize_path(self, file_path: str) -> tuple[str | None, str | None]:
        """Validates and normalizes the file path, returning (normalized_path, error_message)."""
        try:
            decoded_path = unquote(file_path)
        except Exception:
            return None, f"Failed to decode file path: {file_path}"
        
        if self.invalid_path_chars_pattern.search(decoded_path):
             return None, f"Path contains invalid characters: {file_path}"

        if ".." in decoded_path.split('/'):
            return None, f"Path traversal attempt detected with '..': {file_path}"

        if not os.path.isabs(decoded_path):
             return None, f"File path must be absolute: {file_path}"
        
        normalized_path = os.path.normpath(decoded_path)
        
        abs_path = os.path.abspath(normalized_path)
        
        if not any(abs_path.startswith(os.path.abspath(base)) for base in self.allowed_base_dirs):
            return None, f"Path is outside of allowed directories: {abs_path}"
            
        return abs_path, None

    async def execute_tool(self, instance_id: str, parameters: dict, **kwargs) -> ToolResult:
        """Core logic to write a file inside a Kubernetes pod."""
        try:
            file_path = parameters.get("file_path")
            content = parameters.get("content")

            if file_path is None or content is None:
                return ToolResult(success=False, error="Parameters 'file_path' and 'content' are required.")
            if not isinstance(file_path, str) or not isinstance(content, str):
                return ToolResult(success=False, error="'file_path' and 'content' must be strings.")

            final_path, error = self._validate_and_normalize_path(file_path)
            if error:
                return ToolResult(success=False, error=error)

            check_output, check_code = await self._safe_execute_command(f"test -f '{final_path}' && echo 'exists' || echo 'not_exists'")
            if check_code != 0 and "not_exists" not in str(check_output):
                return ToolResult(success=False, error=f"Failed to check file existence for '{final_path}': {check_output}")
            
            file_exists = str(check_output).strip() == "exists"
            operation_type = "update" if file_exists else "create"

            parent_dir = os.path.dirname(final_path)
            if parent_dir and parent_dir != '/':
                mkdir_output, mkdir_code = await self._safe_execute_command(f"mkdir -p '{parent_dir}'")
                if mkdir_code != 0:
                    return ToolResult(success=False, error=f"Failed to create parent directory '{parent_dir}': {mkdir_output}")

            escaped_content = content.replace('\\', '\\\\').replace("'", "'\\''")
            write_cmd = f"printf '%s' '{escaped_content}' > '{final_path}'"
            write_output, write_code = await self._safe_execute_command(write_cmd)

            if write_code != 0:
                return ToolResult(success=False, error=f"Failed to write to file '{final_path}': {write_output}")

            if operation_type == 'create':
                result_message = f"File created successfully at: {final_path}"
            else:
                content_lines = content.splitlines()
                if len(content_lines) > self.max_lines_for_assistant:
                    display_content = '\n'.join(content_lines[:self.max_lines_for_assistant]) + '\n' + self.truncated_message
                else:
                    display_content = content
                
                numbered_lines = [f"{i:6d}\t{line}" for i, line in enumerate(display_content.splitlines(), 1)]
                numbered_content = '\n'.join(numbered_lines)
                result_message = (
                    f"The file {final_path} has been updated. Here's the result of running `cat -n` "
                    f"on a snippet of the edited file:\n{numbered_content}"
                )
            
            line_count_output, _ = await self._safe_execute_command(f"wc -l < '{final_path}'")
            lines_written = int(line_count_output.strip().split()[0]) if line_count_output.strip() and line_count_output.strip().split() else 0

            return ToolResult(
                success=True,
                result=result_message,
                metrics={
                    "operation_type": operation_type,
                    "file_path": final_path,
                    "lines_written": lines_written,
                    "content_length": len(content),
                },
            )

        except Exception as e:
            logger.error(f"K8s file write tool execution failed with an unhandled exception: {e}", exc_info=True)
            return ToolResult(success=False, error=str(e))

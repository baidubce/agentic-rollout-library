"""
Tool wrapper class for the Agentic Rollout Library.

This module provides the Tool wrapper class for registering script-based tools.
"""

from typing import Any, Callable, Dict


class Tool:
    """
    Tool wrapper for registering script-based tools with ToolExecutionNode.

    This class wraps a script path and result parser for subprocess-based tool execution.
    All tools are executed via subprocess using their script_path.
    """

    def __init__(self,
                 name: str,
                 script_path: str,
                 result_parser: Callable = None,
                 execution_mode: str = "k8s"):
        """
        Initialize a tool wrapper.

        Args:
            name: Tool name
            script_path: Path to executable script for subprocess execution
            result_parser: Optional function to parse tool results
            execution_mode: Execution mode - "local" or "k8s" (default: "k8s")
        """
        self.name = name
        self.script_path = script_path
        self.result_parser = result_parser or self._default_parser
        self.execution_mode = execution_mode
        self.local_module = None  # Cached module for local execution


    def parse_result(self, result: Any) -> Any:
        """
        Parse the tool execution result.

        Args:
            result: Raw tool result

        Returns:
            Parsed result
        """
        return self.result_parser(result)

    def _default_parser(self, result: Any) -> str:
        """
        Default result parser - formats subprocess output into standard format.

        Args:
            result: Raw result from subprocess (dict with stdout/stderr/exit_code)

        Returns:
            Formatted string: [STDOUT]...\n[STDERR]...\n[EXIT CODE]...
        """
        if isinstance(result, dict):
            # Extract subprocess output
            stdout = result.get('stdout', '')
            stderr = result.get('stderr', '')
            exit_code = result.get('exit_code')

            # Format as standard output
            parts = []
            parts.append("[STDOUT]:")
            if stdout:
                parts.append(stdout)
            else:
                parts.append("")

            parts.append("\n[STDERR]:")
            if stderr:
                parts.append(stderr)
            else:
                parts.append("")

            if exit_code is not None:
                parts.append(f"\n[EXIT CODE]: {exit_code}")

            return "\n".join(parts)

        # Fallback for non-dict results
        return str(result)

    def __repr__(self) -> str:
        """String representation of the tool."""
        return f"Tool(name={self.name})"

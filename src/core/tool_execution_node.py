"""Tool Execution Node implementation."""

import sys
from pathlib import Path
from typing import Any, Callable, Dict, List
import asyncio

# Add tools directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from .base_node import BaseNode
from tools.base_tool import Tool


class ToolExecutionNode(BaseNode):
    """Node for executing registered tools."""

    def __init__(self, name: str = None, timeline_enabled: bool = False, timeout: float = None):
        """
        Initialize the Tool Execution Node.

        Args:
            name: Optional name for the node
            timeline_enabled: Enable automatic timeline tracking for this node
            timeout: Timeout in seconds for tool execution (None = no timeout)
        """
        super().__init__(name, timeline_enabled=timeline_enabled, timeout=timeout)
        self.tools: Dict[str, Tool] = {}
        self.last_execution_results = []
        self.termination_status = None

        # Register default stop tool
        self._register_stop_tool()

    def register_tool(self,
                     name: str,
                     script_path: str,
                     result_parser: Callable = None,
                     execution_mode: str = "k8s") -> None:
        """
        Register a new tool.

        All tools are executed via subprocess using their script_path.
        Tool descriptions for LLM should be defined in CUSTOM_TOOL_DESCRIPTIONS config.

        Args:
            name: Tool name
            script_path: Path to executable script for subprocess execution
            result_parser: Optional result parser function
            execution_mode: Execution mode - "local" or "k8s" (default: "k8s")
        """
        tool = Tool(
            name=name,
            script_path=script_path,
            result_parser=result_parser,
            execution_mode=execution_mode
        )
        self.tools[name] = tool
        self.logger.info(f"Registered tool: {name} (mode: {execution_mode})")

    def _register_stop_tool(self) -> None:
        """
        Register the default stop tool.

        Creates a temporary stop.py script for the stop tool.
        """
        import tempfile
        import os

        stop_script_content = '''#!/usr/bin/env python3
import sys
import json
import argparse

parser = argparse.ArgumentParser()
parser.add_argument('--reason', type=str, default='User requested stop')
args = parser.parse_args()

result = {"status": "stop", "reason": args.reason}
print(json.dumps(result))
'''

        # Create temporary script
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(stop_script_content)
            stop_script_path = f.name

        # Store the path for cleanup
        self._stop_script_path = stop_script_path

        def parse_stop_result(result):
            """Parse stop tool result."""
            if isinstance(result, dict):
                stdout = result.get("stdout", result.get("output", ""))
                try:
                    import json
                    return json.loads(stdout)
                except:
                    return {"status": "stop", "reason": "Stop executed"}
            return {"status": "stop", "reason": "Stop executed"}

        self.register_tool(
            name="stop",
            script_path=stop_script_path,
            result_parser=parse_stop_result
        )

    def unregister_tool(self, name: str) -> None:
        """
        Unregister a tool.

        Args:
            name: Tool name to unregister
        """
        if name in self.tools:
            del self.tools[name]
            self.logger.info(f"Unregistered tool: {name}")


    async def process_async(self, input_data: List[Dict]) -> List[Dict]:
        """
        Execute tools based on parsed tool calls asynchronously.

        Args:
            input_data: List of tool call dictionaries from Tool Parsing Node

        Returns:
            List of execution results
        """
        if not self.validate_input(input_data):
            raise ValueError("Invalid input format for Tool Execution Node")

        results = []
        self.termination_status = None

        tasks = [self._execute_single_tool_async(tool_call) for tool_call in input_data]
        results = await asyncio.gather(*tasks)

        for result in results:
            if result.get("status") == "stop":
                self.termination_status = "stop"
                self.logger.info("Stop tool executed, setting termination status")
                break

        self.last_execution_results = list(results)
        return list(results)

    def process(self, input_data: List[Dict]) -> List[Dict]:
        """
        Execute tools based on parsed tool calls.

        Args:
            input_data: List of tool call dictionaries from Tool Parsing Node

        Returns:
            List of execution results
        """
        if not self.validate_input(input_data):
            raise ValueError("Invalid input format for Tool Execution Node")

        results = []
        self.termination_status = None

        for tool_call in input_data:
            result = self._execute_single_tool(tool_call)
            results.append(result)

            # Check for termination
            if result.get("status") == "stop":
                self.termination_status = "stop"
                self.logger.info("Stop tool executed, setting termination status")
                break

        self.last_execution_results = results
        return results

    def _execute_single_tool(self, tool_call: Dict) -> Dict:
        """
        Execute a single tool call using subprocess.

        All tools are executed via subprocess using their script_path.

        Args:
            tool_call: Tool call dictionary

        Returns:
            Execution result dictionary
        """
        # Extract tool name and arguments
        tool_name = (tool_call.get("tool") or
                    tool_call.get("function") or
                    tool_call.get("name") or
                    tool_call.get("action"))

        arguments = (tool_call.get("arguments") or
                    tool_call.get("parameters") or
                    tool_call.get("args") or
                    tool_call.get("input") or
                    {})

        # Ensure arguments is a dictionary
        if not isinstance(arguments, dict):
            arguments = {"input": arguments}

        # Check if tool is registered
        if not tool_name:
            return {
                "error": "No tool name specified",
                "tool_call": tool_call,
                "status": "error"
            }

        if tool_name not in self.tools:
            self.logger.warning(f"Tool not found: {tool_name}")
            return {
                "error": f"Tool '{tool_name}' not registered",
                "tool": tool_name,
                "status": "error"
            }

        # Execute tool
        tool = self.tools[tool_name]
        try:
            self.logger.info(f"Executing tool: {tool_name}")

            # Execute via subprocess
            raw_result = self._execute_tool_subprocess(tool, arguments)

            # Parse the result
            parsed_result = tool.parse_result(raw_result)

            # Extract stdout/stderr/exit_code from raw_result
            stdout = raw_result.get("stdout", "")
            stderr = raw_result.get("stderr", "")
            exit_code = raw_result.get("exit_code", raw_result.get("returncode", 0))

            # Format response - always include stdout/stderr/exit_code
            result = {
                "tool": tool_name,
                "result": parsed_result,
                "status": "success" if exit_code == 0 else "error",
                "stdout": stdout,        # Always provide subprocess stdout
                "stderr": stderr,        # Always provide subprocess stderr
                "exit_code": exit_code  # Always provide exit code
            }

            # If execution failed, add error information
            if exit_code != 0:
                error_msg = stderr.strip() if stderr.strip() else None
                if not error_msg and isinstance(parsed_result, dict):
                    error_msg = parsed_result.get("output", "")

                result["error"] = error_msg or f"Command failed with exit code {exit_code}"
                result["error_type"] = "CommandExecutionError"

            # Check if this is the stop tool
            if tool_name == "stop" and isinstance(parsed_result, dict):
                result["status"] = parsed_result.get("status", "success")

            return result

        except Exception as e:
            self.logger.error(f"Error executing tool {tool_name}: {str(e)}")
            return {
                "tool": tool_name,
                "error": str(e),
                "status": "error"
            }

    async def _execute_single_tool_async(self, tool_call: Dict) -> Dict:
        """
        Execute a single tool call using subprocess asynchronously.

        Args:
            tool_call: Tool call dictionary

        Returns:
            Execution result dictionary
        """
        tool_name = (tool_call.get("tool") or
                    tool_call.get("function") or
                    tool_call.get("name") or
                    tool_call.get("action"))

        arguments = (tool_call.get("arguments") or
                    tool_call.get("parameters") or
                    tool_call.get("args") or
                    tool_call.get("input") or
                    {})

        if not isinstance(arguments, dict):
            arguments = {"input": arguments}

        if not tool_name:
            return {
                "error": "No tool name specified",
                "tool_call": tool_call,
                "status": "error"
            }

        if tool_name not in self.tools:
            self.logger.warning(f"Tool not found: {tool_name}")
            return {
                "error": f"Tool '{tool_name}' not registered",
                "tool": tool_name,
                "status": "error"
            }

        tool = self.tools[tool_name]
        try:
            self.logger.info(f"Executing tool: {tool_name}")

            raw_result = await self._execute_tool_subprocess_async(tool, arguments)

            parsed_result = tool.parse_result(raw_result)

            stdout = raw_result.get("stdout", "")
            stderr = raw_result.get("stderr", "")
            exit_code = raw_result.get("exit_code", raw_result.get("returncode", 0))

            result = {
                "tool": tool_name,
                "result": parsed_result,
                "status": "success" if exit_code == 0 else "error",
                "stdout": stdout,
                "stderr": stderr,
                "exit_code": exit_code
            }

            if exit_code != 0:
                error_msg = stderr.strip() if stderr.strip() else None
                if not error_msg and isinstance(parsed_result, dict):
                    error_msg = parsed_result.get("output", "")

                result["error"] = error_msg or f"Command failed with exit code {exit_code}"
                result["error_type"] = "CommandExecutionError"

            if tool_name == "stop" and isinstance(parsed_result, dict):
                result["status"] = parsed_result.get("status", "success")

            return result

        except Exception as e:
            self.logger.error(f"Error executing tool {tool_name}: {str(e)}")
            return {
                "tool": tool_name,
                "error": str(e),
                "status": "error"
            }

    async def _execute_tool_subprocess_async(self, tool: Any, arguments: Dict) -> Dict:
        """
        Execute a tool using subprocess asynchronously to capture stdout and stderr.

        Args:
            tool: Tool instance with script_path attribute
            arguments: Tool arguments

        Returns:
            Dictionary with stdout, stderr, exit_code
        """
        import json

        cmd_parts = ['python3', tool.script_path]

        if 'command' in arguments:
            cmd_parts.append(str(arguments['command']))

        for key, value in arguments.items():
            if key == 'command':
                continue

            if isinstance(value, bool):
                if value:
                    cmd_parts.append(f'--{key}')
            elif isinstance(value, (list, dict)):
                cmd_parts.extend([f'--{key}', json.dumps(value)])
            else:
                cmd_parts.extend([f'--{key}', str(value)])

        process = await asyncio.create_subprocess_exec(
            *cmd_parts,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=300
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return {
                "stdout": "",
                "stderr": "Process timeout after 300 seconds",
                "returncode": -1,
                "exit_code": -1,
                "success": False
            }

        return {
            "stdout": stdout.decode('utf-8') if stdout else "",
            "stderr": stderr.decode('utf-8') if stderr else "",
            "returncode": process.returncode,
            "exit_code": process.returncode,
            "success": process.returncode == 0
        }

    def _execute_tool_subprocess(self, tool: Any, arguments: Dict) -> Dict:
        """
        Execute a tool using subprocess to capture stdout and stderr.

        Args:
            tool: Tool instance with script_path attribute
            arguments: Tool arguments

        Returns:
            Dictionary with stdout, stderr, exit_code
        """
        import subprocess
        import json

        # Build command line arguments
        cmd_parts = ['python3', tool.script_path]

        # Handle 'command' as positional argument if present
        if 'command' in arguments:
            cmd_parts.append(str(arguments['command']))

        # Add other arguments as named parameters
        for key, value in arguments.items():
            if key == 'command':
                continue  # Already handled as positional

            if isinstance(value, bool):
                if value:
                    cmd_parts.append(f'--{key}')
            elif isinstance(value, (list, dict)):
                cmd_parts.extend([f'--{key}', json.dumps(value)])
            else:
                cmd_parts.extend([f'--{key}', str(value)])

        # Execute the command
        result = subprocess.run(
            cmd_parts,
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout
        )

        # Return in the same format as bash_func
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
            "exit_code": result.returncode,
            "success": result.returncode == 0
        }

    def check_termination(self) -> bool:
        """
        Check if termination condition has been met.

        Returns:
            True if should terminate, False otherwise
        """
        return self.termination_status == "stop"

    def validate_input(self, input_data: Any) -> bool:
        """
        Validate input is a list of tool call dictionaries.

        Args:
            input_data: Input to validate

        Returns:
            True if valid, False otherwise
        """
        if not isinstance(input_data, list):
            # Allow single tool call
            if isinstance(input_data, dict):
                return True
            return False

        return all(isinstance(item, dict) for item in input_data)

    def validate_output(self, output_data: Any) -> bool:
        """
        Validate output is a list of result dictionaries.

        Args:
            output_data: Output to validate

        Returns:
            True if valid, False otherwise
        """
        if not isinstance(output_data, list):
            return False

        for item in output_data:
            if not isinstance(item, dict):
                return False
            if "status" not in item:
                return False

        return True

    def get_last_results(self) -> List[Dict]:
        """
        Get the last execution results.

        Returns:
            List of last execution results
        """
        return self.last_execution_results.copy()

    def reset(self) -> None:
        """Reset the node to initial state."""
        super().reset()
        self.last_execution_results = []
        self.termination_status = None

    def __del__(self):
        """Cleanup temporary stop script."""
        if hasattr(self, '_stop_script_path'):
            try:
                import os
                os.unlink(self._stop_script_path)
            except:
                pass

    def list_tools(self) -> List[str]:
        """
        List all registered tool names.

        Returns:
            List of tool names
        """
        return list(self.tools.keys())


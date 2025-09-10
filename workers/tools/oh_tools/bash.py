#!/usr/bin/env python3
"""
Bash executor tool adapted from OpenHands for the agentic rollout framework.
Supports command execution with timeout and interaction capabilities.
"""

import sys
import logging
from typing import Any, Dict, Optional

from ...core.base_tool import BaseAgenticTool
from ...core.tool_schemas import OpenAIFunctionToolSchema, ToolResult, create_openai_tool_schema

logger = logging.getLogger(__name__)


_DETAILED_BASH_DESCRIPTION = """Execute a bash command in the terminal within a persistent shell session.


### Command Execution
* One command at a time: You can only execute one bash command at a time. If you need to run multiple commands sequentially, use `&&` or `;` to chain them together.
* Persistent session: Commands execute in a persistent shell session where environment variables, virtual environments, and working directory persist between commands.
* Soft timeout: Commands have a soft timeout of 10 seconds, once that's reached, you have the option to continue or interrupt the command (see section below for details)

### Long-running Commands
* For commands that may run indefinitely, run them in the background and redirect output to a file, e.g. `python3 app.py > server.log 2>&1 &`.
* For commands that may run for a long time (e.g. package installation commands), or commands that run for a fixed amount of time (e.g. sleep), you should set the "timeout" parameter of your function call to an appropriate value.
  - `pnpm/npm add`, should use timeout=60
* If a bash command returns exit code `-1`, this means the process hit the soft timeout and is not yet finished. By setting `is_input` to `true`, you can:  
  - Send control commands like `C-c` (Ctrl+C), `C-d` (Ctrl+D), or `C-z` (Ctrl+Z) to interrupt the process
  - If you do C-c, you can re-start the process with a longer "timeout" parameter to let it run to completion

### Best Practices
* Directory verification: Before creating new directories or files, first verify the parent directory exists and is the correct location.
* Directory management: Try to maintain working directory by using absolute paths and avoiding excessive use of `cd`.

### Output Handling
* Output truncation: If the output exceeds a maximum length, it will be truncated before being returned.
"""

_SHORT_BASH_DESCRIPTION = """Execute a bash command in the terminal.
* Long running commands: For commands that may run indefinitely, it should be run in the background and the output should be redirected to a file, e.g. command = `python3 app.py > server.log 2>&1 &`. For commands that need to run for a specific duration, you can set the "timeout" argument to specify a hard timeout in seconds.
* Interact with running process: If a bash command returns exit code `-1`, this means the process is not yet finished. By setting `is_input` to `true`, the assistant can interact with the running process and send empty `command` to retrieve any additional logs, or send additional text (set `command` to the text) to STDIN of the running process, or send command like `C-c` (Ctrl+C), `C-d` (Ctrl+D), `C-z` (Ctrl+Z) to interrupt the process.
* One command at a time: You can only execute one bash command at a time. If you need to run multiple commands sequentially, you can use `&&` or `;` to chain them together."""

def refine_prompt(prompt: str) -> str:
    """Refine prompt for platform-specific shells."""
    if sys.platform == 'win32':
        return prompt.replace('bash', 'powershell')
    return prompt


class OHBashTool(BaseAgenticTool):
    """
    Bash executor tool adapted from OpenHands.
    Provides command execution with timeout and interaction capabilities.
    """
    
    def __init__(self, config: Optional[Dict] = None):
        """Initialize the bash executor tool."""
        config = config or {}
        self.use_short_description = config.get("use_short_description", False)
        super().__init__(config)
        
        # Session management
        self.sessions = {}  # instance_id -> session state
        self.max_output_length = config.get("max_output_length", 10000)
        self.default_timeout = config.get("default_timeout", 10)
    
    def get_openai_tool_schema(self) -> OpenAIFunctionToolSchema:
        """Return the OpenAI function schema for this tool."""
        description = (
            _SHORT_BASH_DESCRIPTION if self.use_short_description 
            else _DETAILED_BASH_DESCRIPTION
        )
        
        return create_openai_tool_schema(
            name="execute_bash",
            description=refine_prompt(description),
            parameters={
                "command": {
                    "type": "string",
                    "description": refine_prompt(
                        "The bash command to execute."
                        "Can be `C-c` (Ctrl+C) to interrupt the currently running process. "
                        "Note: You can only execute one bash command at a time. "
                        "If you need to run multiple commands sequentially, you can use `&&` or `;` to chain them together."
                    ),
                },
                "is_input": {
                    "type": "string",
                    "description": refine_prompt(
                        "If True, the command is an input to the running process. "
                        "If False, the command is a bash command to be executed in the terminal. Default is False."
                    ),
                    "enum": ["true", "false"],
                },
                "timeout": {
                    "type": "number",
                    "description": "Optional. Sets a hard timeout in seconds for the command execution. "
                                 "If not provided, the command will use the default soft timeout behavior.",
                },
            },
            required=["command"]
        )
    
    async def execute_tool(self, instance_id: str, parameters: Dict[str, Any], **kwargs) -> ToolResult:
        """
        Execute a bash command.
        
        Args:
            instance_id: Tool instance ID
            parameters: Tool parameters containing 'command', optional 'is_input' and 'timeout'
            **kwargs: Additional execution arguments
            
        Returns:
            ToolResult with command output or error
        """
        try:
            import subprocess
            import asyncio
            
            command = parameters.get("command", "")
            is_input = parameters.get("is_input", "false") == "true"
            timeout = parameters.get("timeout", self.default_timeout)
            
            # Get or create session for this instance
            if instance_id not in self.sessions:
                self.sessions[instance_id] = {
                    "cwd": None,
                    "env": None,
                    "running_process": None
                }
            
            session = self.sessions[instance_id]
            
            # Handle input to running process
            if is_input and session.get("running_process"):
                process = session["running_process"]
                
                # Send input or control signal
                if command == "C-c":
                    process.terminate()
                    session["running_process"] = None
                    return ToolResult(
                        success=True,
                        result="Process terminated with Ctrl+C"
                    )
                elif command == "C-d":
                    process.stdin.close() if process.stdin else None
                    return ToolResult(
                        success=True,
                        result="Sent EOF (Ctrl+D) to process"
                    )
                else:
                    # Send text input
                    if process.stdin:
                        process.stdin.write((command + "\n").encode())
                        process.stdin.flush()
                    return ToolResult(
                        success=True,
                        result=f"Sent input to process: {command}"
                    )
            
            # Execute new command
            try:
                # Run command with timeout
                process = await asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    stdin=asyncio.subprocess.PIPE,
                    cwd=session.get("cwd"),
                    env=session.get("env")
                )
                
                # Store as running process if long-running
                if timeout > self.default_timeout:
                    session["running_process"] = process
                
                try:
                    stdout, stderr = await asyncio.wait_for(
                        process.communicate(),
                        timeout=timeout
                    )
                    
                    # Clear running process on completion
                    if session.get("running_process") == process:
                        session["running_process"] = None
                    
                    output = stdout.decode('utf-8', errors='replace')
                    error = stderr.decode('utf-8', errors='replace')
                    
                    # Truncate if needed
                    if len(output) > self.max_output_length:
                        output = output[:self.max_output_length] + "\n<output truncated>"
                    
                    result = output
                    if error:
                        result += f"\nSTDERR:\n{error}"
                    
                    return ToolResult(
                        success=process.returncode == 0,
                        result=result
                    )
                    
                except asyncio.TimeoutError:
                    # Soft timeout - process still running
                    session["running_process"] = process
                    return ToolResult(
                        success=False,
                        result=f"Command timed out after {timeout} seconds. Process is still running. "
                               "Use is_input=true to interact with it or send C-c to terminate."
                    )
                    
            except Exception as e:
                logger.error(f"Command execution failed: {e}")
                return ToolResult(
                    success=False,
                    error=f"Failed to execute command: {str(e)}"
                )
                
        except Exception as e:
            logger.error(f"Bash tool execution failed: {e}")
            return ToolResult(
                success=False,
                error=f"Bash tool error: {str(e)}"
            )
    
    async def calculate_reward(self, instance_id: str, **kwargs) -> float:
        """
        Calculate reward for bash execution.
        
        Args:
            instance_id: Tool instance ID
            **kwargs: Additional arguments
            
        Returns:
            Reward score (0.0 to 1.0)
        """
        # Simple reward based on successful execution
        last_result = kwargs.get("last_result")
        if last_result and last_result.get("success"):
            return 1.0
        return 0.0


__all__ = ['OHBashTool']
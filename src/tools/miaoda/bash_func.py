#!/usr/bin/env python3
"""
Description: Execute a bash command in the terminal.
Parameters:
  command (string, required): The bash command to execute. Positional argument.

Usage:
  As a script: python bash_func.py "pwd"
  As a module: python -m tools.bash_func "pwd"
  As a function: from tools.bash_func import bash_func; bash_func("pwd")
"""

import argparse
import subprocess
import sys
from typing import Dict, Any

# Optional: Add blocked commands for safety
BLOCKED_BASH_COMMANDS = ["rm -rf /", "dd if=/dev/zero"]


def bash_func(command: str) -> Dict[str, Any]:
    """
    Execute a bash command and return the result.

    Args:
        command: The bash command to execute

    Returns:
        Dictionary containing:
            - stdout: Standard output from the command
            - stderr: Standard error from the command
            - returncode: Exit code of the command
            - success: Boolean indicating if command succeeded
    """
    try:
        # Check for blocked commands
        for blocked in BLOCKED_BASH_COMMANDS:
            if blocked in command:
                return {
                    "stdout": "",
                    "stderr": f"Command '{blocked}' is blocked for safety",
                    "returncode": 1,
                    "success": False
                }

        # Try to use capture_output (Python 3.7+)
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=300  # 5 minute timeout
            )
        except TypeError:
            # Fallback for Python 3.5 and 3.6
            result = subprocess.run(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=300
            )

        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
            "success": result.returncode == 0
        }

    except subprocess.TimeoutExpired:
        return {
            "stdout": "",
            "stderr": f"Command timed out after 300 seconds: {command}",
            "returncode": 124,  # Standard timeout exit code
            "success": False
        }
    except Exception as e:
        return {
            "stdout": "",
            "stderr": f"Error executing command: {str(e)}",
            "returncode": 1,
            "success": False
        }


def parse_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse bash execution result for agent use.

    Args:
        result: Raw result from bash_func or K8S execution

    Returns:
        Formatted result for agent
    """
    if isinstance(result, dict):
        # Handle both local execution result and K8S result format
        return {
            "output": result.get("stdout", result.get("output", "")),
            "error": result.get("stderr", result.get("error", "")),
            "return_code": result.get("returncode", result.get("exit_code", 0)),
            "status": "success" if result.get("success", result.get("returncode", 0) == 0) else "error"
        }
    return {
        "output": str(result),
        "status": "success"
    }


def build_k8s_command(command: str) -> str:
    """
    Build command for K8S pod execution.
    For bash commands, just return them directly.

    Args:
        command: The bash command

    Returns:
        Command string for K8S execution
    """
    return command


def main():
    """Main entry point for CLI usage."""
    import os
    # Add parent directory to path for importing arg_utils
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from arg_utils import decode_arg

    parser = argparse.ArgumentParser(
        description="Execute a bash command.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python bash_func.py "pwd"
  python bash_func.py "ls -la"
  python bash_func.py "echo 'Hello World'"
        """
    )
    parser.add_argument(
        "command",
        help="The bash command to execute (positional argument)"
    )
    parser.add_argument(
        "--app_id",
        default=None,
        help="Application ID"
    )
    parser.add_argument(
        "--user_id",
        default=None,
        help="User ID"
    )
    parser.add_argument(
        "--session_id",
        default=None,
        help="Session ID"
    )
    parser.add_argument(
        "--trace_id",
        default=None,
        help="Trace ID"
    )
    parser.add_argument(
        "--app_type",
        default=None,
        help="Application type"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output result as JSON"
    )

    args = parser.parse_args()

    # Decode base64-encoded command if needed
    command = decode_arg(args.command)

    # Execute the command
    result = bash_func(command)

    if args.json:
        # Output as JSON for programmatic use
        import json
        print(json.dumps(result, indent=2))
    else:
        # Human-readable output
        if result["returncode"] != 0:
            print(f"Error executing command:", file=sys.stderr)
            print(f"Exit code: {result['returncode']}", file=sys.stderr)

        if result["stdout"]:
            print("[STDOUT]")
            print(result["stdout"].rstrip())

        if result["stderr"]:
            print("[STDERR]", file=sys.stderr)
            print(result["stderr"].rstrip(), file=sys.stderr)

    # Exit with the same code as the command
    sys.exit(result["returncode"])


if __name__ == "__main__":
    main()
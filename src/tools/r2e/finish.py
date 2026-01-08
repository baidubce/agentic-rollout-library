#!/usr/bin/env python3
"""
R2E Finish/Submit Tool - Submit final results and complete the task.
"""

from typing import Dict, Any


def finish_func(**kwargs) -> Dict[str, Any]:
    """
    Submit final results and mark task as complete.

    Parameters:
        command (str, required): Should be 'submit'
        result (str, optional): The result text to submit. Defaults to empty string.

    Returns:
        Dict with 'output', 'error', 'status', and 'message' keys
    """
    command = kwargs.get('command')
    result = kwargs.get('result', '')

    if not command:
        return {
            "output": "",
            "error": "Missing required parameter 'command'",
            "status": "error"
        }

    if command != "submit":
        return {
            "output": "",
            "error": f"Unknown command '{command}'. Only 'submit' is supported.",
            "status": "error"
        }

    # Submit the result
    output = "<<<Finished>>>"
    if result:
        output += f"\nFinal result: {result}"

    return {
        "output": output,
        "message": "Task completed",
        "status": "stop"  # Special status to signal task completion
    }


if __name__ == "__main__":
    # Test the function
    import argparse

    parser = argparse.ArgumentParser(description="Submit/Finish tool")
    parser.add_argument("command", help="Subcommand (only 'submit' is supported)")
    parser.add_argument("--result", default="", help="Result text to submit (optional)")

    args = parser.parse_args()

    result = finish_func(command=args.command, result=args.result)

    if result.get("status") == "error":
        print(f"ERROR: {result.get('error', 'Unknown error')}")
    else:
        print(result.get("output", ""))

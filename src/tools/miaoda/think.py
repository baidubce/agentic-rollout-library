#!/usr/bin/env python3
"""
Description: Log agent's thinking process and reasoning.
Parameters:
  thought (string, required): The agent's explanation of its actions and reasoning. Positional argument.

Usage:
  As a script: python think.py "I need to first understand the codebase structure"
  As a module: python -m tools.miaoda.impl.think "Analyzing the error in the logs"
  As a function: from tools.miaoda.impl.think import think_func; think_func("Planning next steps")
"""

import argparse
import sys
from typing import Dict, Any


def think_func(thought: str, **kwargs) -> Dict[str, Any]:
    """
    Log the agent's thought process and reasoning.

    Args:
        thought: The agent's explanation of its actions and reasoning
        **kwargs: Additional context parameters (app_id, user_id, session_id, trace_id, app_type)
                  These are accepted for compatibility but not used in this mock implementation.

    Returns:
        Dictionary containing:
            - output: Formatted thought message
            - status: Status of the action (always 'success')
            - message: Brief description
    """
    # Note: kwargs may contain app_id, user_id, session_id, trace_id, app_type
    # These are accepted for compatibility with the tool execution framework
    if not thought:
        return {
            "output": "",
            "error": "Thought cannot be empty",
            "status": "error"
        }

    return {
        "output": f"I am thinking...: {thought}",
        "message": "Thought logged successfully",
        "status": "success"
    }


def parse_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse think result for agent use.

    Args:
        result: Raw result from think_func

    Returns:
        Formatted result for agent
    """
    return result


def build_k8s_command(thought: str) -> str:
    """
    Build command for K8S pod execution.
    For think actions, we just echo the thought (no actual execution needed).

    Uses base64 encoding to avoid shell escaping issues with special characters.

    Args:
        thought: The thought text

    Returns:
        Command string for K8S execution
    """
    import base64

    # Use base64 encoding to avoid escaping issues
    encoded_thought = base64.b64encode(thought.encode()).decode()
    return f'python3 -c "import base64, json; from tools.miaoda.think import think_func; thought = base64.b64decode(\'{encoded_thought}\').decode(); print(json.dumps(think_func(thought), ensure_ascii=False))"'


def main():
    """Main entry point for CLI usage."""
    import os
    # Add parent directory to path for importing arg_utils
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from arg_utils import decode_arg

    parser = argparse.ArgumentParser(
        description="Log agent's thinking process.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python think.py --thought "I need to analyze the error logs first"
  python think.py --thought "The issue seems to be related to the authentication module"
  python think.py --thought "Planning to implement the feature in three steps"
        """
    )
    parser.add_argument(
        "--thought",
        help="The agent's thought or reasoning (positional argument)"
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

    # Decode base64-encoded thought if needed
    thought = decode_arg(args.thought)

    result = think_func(thought)

    if args.json:
        import json
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        if result["status"] == "error":
            print(f"Error: {result.get('error', 'Unknown error')}", file=sys.stderr)
            sys.exit(1)
        
        print(result["output"])

    sys.exit(0)


if __name__ == "__main__":
    main()

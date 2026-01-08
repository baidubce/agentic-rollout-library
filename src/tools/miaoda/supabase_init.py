#!/usr/bin/env python3
"""
Description: Initialize Supabase project and retrieve credentials via MCP server.
Parameters:
  name (string, required): Project name. Positional argument.
  app_id (string, optional): Application ID.

Usage:
  As a script: python supabase_init.py "my_project" --app_id "my_app"
  As a module: python -m tools.miaoda.impl.supabase_init "my_project"
  As a function: from tools.miaoda.impl.supabase_init import supabase_init_func; supabase_init_func("my_project")
"""

import argparse
import json
import os
import sys
from typing import Dict, Any, Optional


def supabase_init_func(name: str, app_id: Optional[str] = None, **kwargs) -> Dict[str, Any]:
    """
    Initialize Supabase project and retrieve credentials.

    Args:
        name: Project name
        app_id: Application ID (optional)
        **kwargs: Additional context parameters (user_id, session_id, trace_id, app_type)
                  These are accepted for compatibility but not used in this mock implementation.

    Returns:
        Dictionary containing:
            - result: Supabase initialization result (endpoint, anon_key, etc.)
            - status: Status of the operation (success/error)
            - error: Error message if failed
    """
    # Note: kwargs may contain user_id, session_id, trace_id, app_type
    # These are accepted for compatibility with the tool execution framework
    app_info = f" (app_id: {app_id})" if app_id else ""
    success_message = f"Supabase project '{name}'{app_info} initialized successfully. Database credentials retrieved and ready to use."
    
    return {
        "result": success_message,
        "status": "success"
    }


def parse_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse Supabase init result for agent use.

    Args:
        result: Raw result from supabase_init_func or K8S execution

    Returns:
        Formatted result for agent
    """
    if isinstance(result, dict):
        return {
            "result": result.get("result", str(result)),
            "status": result.get("status", "success")
        }
    
    return {
        "result": str(result),
        "status": "success"
    }


def build_k8s_command(name: str, app_id: Optional[str] = None) -> str:
    """
    Build command for K8S pod execution.

    Uses base64 encoding to avoid shell escaping issues with special characters
    like quotes, backslashes, ampersands, semicolons, etc.

    Args:
        name: Project name
        app_id: Application ID (optional)

    Returns:
        Command string for K8S execution
    """
    import base64

    # Encode parameters using base64 to completely avoid escaping issues
    encoded_name = base64.b64encode(name.encode()).decode()

    if app_id:
        encoded_app_id = base64.b64encode(app_id.encode()).decode()
        return f'python3 -c "import base64, json; from tools.miaoda.supabase_init import supabase_init_func; name = base64.b64decode(\'{encoded_name}\').decode(); app_id = base64.b64decode(\'{encoded_app_id}\').decode(); print(json.dumps(supabase_init_func(name, app_id), ensure_ascii=False))"'
    else:
        return f'python3 -c "import base64, json; from tools.miaoda.supabase_init import supabase_init_func; name = base64.b64decode(\'{encoded_name}\').decode(); print(json.dumps(supabase_init_func(name), ensure_ascii=False))"'


def main():
    """Main entry point for CLI usage."""
    parser = argparse.ArgumentParser(
        description="Initialize Supabase project.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python supabase_init.py --name "my_project"
  python supabase_init.py --name "my_project" --app_id "my_app"
  python supabase_init.py "test_db" --json
        """
    )
    parser.add_argument(
        "--name",
        required=True,
        help="Project name"
    )
    parser.add_argument(
        "--app_id",
        help="Application ID (optional)"
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

    # Add parent directory to path for importing arg_utils
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from arg_utils import decode_arg

    # Decode base64-encoded arguments if needed
    name = decode_arg(args.name)
    app_id = decode_arg(args.app_id)

    # Validate required parameters
    if not name:
        print(json.dumps({
            "status": "error",
            "error": "Missing required parameter: name"
        }, ensure_ascii=False))
        sys.exit(1)

    result = supabase_init_func(name, app_id)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        if result["status"] == "error":
            print(f"Error: {result.get('error', 'Unknown error')}", file=sys.stderr)
            sys.exit(1)
        
        parsed = parse_result(result)
        print(parsed["result"])

    sys.exit(0 if result["status"] == "success" else 1)


if __name__ == "__main__":
    main()

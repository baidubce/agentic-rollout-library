#!/usr/bin/env python3
"""
Description: Apply database migration to Supabase via MCP server.
Parameters:
  name (string, required): Migration name (use snake_case). Positional argument.
  query (string, required): SQL query to apply.

Usage:
  As a script: python supabase_migration.py "create_users_table" "CREATE TABLE users (id SERIAL PRIMARY KEY);"
  As a module: python -m tools.miaoda.impl.supabase_migration "add_index" "CREATE INDEX idx_users ON users(email);"
  As a function: from tools.miaoda.impl.supabase_migration import supabase_migration_func; supabase_migration_func("migration_name", "SQL query")
"""

import argparse
import json
import os
import sys
import uuid
from typing import Dict, Any


def supabase_migration_func(name: str, query: str, **kwargs) -> Dict[str, Any]:
    """
    Apply database migration to Supabase.

    Args:
        name: Migration name (use snake_case)
        query: SQL query to apply (DDL operations)
        **kwargs: Additional context parameters (app_id, user_id, session_id, trace_id, app_type)
                  These are accepted for compatibility but not used in this mock implementation.

    Returns:
        Dictionary containing:
            - result: Migration result
            - status: Status of the operation (success/error)
            - error: Error message if failed
    """
    # Note: kwargs may contain app_id, user_id, session_id, trace_id, app_type
    # These are accepted for compatibility with the tool execution framework
    app_id = kwargs.get('app_id') or f"app-{uuid.uuid4().hex[:6]}"
    success_message = f"Migration '{name}' (app_id: {app_id}) applied successfully. Database schema updated."
    
    return {
        "result": success_message,
        "status": "success",
        "app_id": app_id
    }


def parse_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse Supabase migration result for agent use.

    Args:
        result: Raw result from supabase_migration_func or K8S execution

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


def build_k8s_command(name: str, query: str) -> str:
    """
    Build command for K8S pod execution.

    Uses base64 encoding to avoid shell escaping issues with special characters
    like quotes, backslashes, ampersands, semicolons, newlines, comments, etc.
    This is especially important for SQL queries which often contain complex
    characters that can break shell command parsing.

    Args:
        name: Migration name
        query: SQL query

    Returns:
        Command string for K8S execution
    """
    import base64

    # Encode parameters using base64 to completely avoid escaping issues
    encoded_name = base64.b64encode(name.encode()).decode()
    encoded_query = base64.b64encode(query.encode()).decode()

    return f'python3 -c "import base64, json; from tools.miaoda.supabase_migration import supabase_migration_func; name = base64.b64decode(\'{encoded_name}\').decode(); query = base64.b64decode(\'{encoded_query}\').decode(); print(json.dumps(supabase_migration_func(name, query), ensure_ascii=False))"'


def main():
    """Main entry point for CLI usage."""
    parser = argparse.ArgumentParser(
        description="Apply database migration to Supabase.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python supabase_migration.py --name "create_users_table" --query "CREATE TABLE users (id SERIAL PRIMARY KEY, username TEXT);"
  python supabase_migration.py --name "add_index" --query "CREATE INDEX idx_users_email ON users(email);"
  python supabase_migration.py --name "alter_table" --query "ALTER TABLE users ADD COLUMN created_at TIMESTAMP;" --json
        """
    )
    parser.add_argument(
        "--name",
        required=True,
        help="Migration name (use snake_case)"
    )
    parser.add_argument(
        "--query",
        required=True,
        help="SQL query to apply"
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

    # Add parent directory to path for importing arg_utils
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from arg_utils import decode_arg

    # Decode base64-encoded arguments if needed
    name = decode_arg(args.name)
    query = decode_arg(args.query)

    # Handle None values (shouldn't happen with required=True, but just in case)
    if not name or not query:
        print(json.dumps({
            "status": "error",
            "error": "Missing required parameters: name and query are required"
        }, ensure_ascii=False))
        sys.exit(1)

    result = supabase_migration_func(name, query)

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

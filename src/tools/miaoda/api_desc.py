#!/usr/bin/env python3
"""
Description: Query API description by API name using MCP server.
Parameters:
  api_name (string, required): The API name to query. Positional argument.
  app_id (string, optional): Application ID (default: "demo_app").

Usage:
  As a script: python api_desc.py "user.login" --app_id "my_app"
  As a module: python -m tools.miaoda.impl.api_desc "user.login"
  As a function: from tools.miaoda.impl.api_desc import api_desc_func; api_desc_func("user.login")
"""

import argparse
import asyncio
import json
import sys
import os
from typing import Dict, Any
from mcp.client.session import ClientSession
from mcp.client.sse import sse_client

SERVER_URL = os.getenv("MIAODA_MCP_SERVER_URL", "http://aos-mcp-sandbox.miaoda-bj-offline.baidu-int.com/v1/agentos/mcp/sse")

def api_desc_func(api_name: str, app_id: str = "demo_app") -> Dict[str, Any]:
    """
    Query API description from MCP server.

    Args:
        api_name: The API name to query
        app_id: Application ID

    Returns:
        Dictionary containing:
            - result: API description data
            - status: Status of the query (success/error)
            - error: Error message if failed
    """
    headers = {}
    headers["x-miaoda-app-id"] = app_id
    try:
        async def _call():
            async with sse_client(SERVER_URL, headers=headers) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        "api_desc",
                        {
                            "input": api_name,
                            "app_id": app_id
                        }
                    )
                    return result

        result = asyncio.run(_call())
        result_dict = json.loads(result.model_dump_json())
        
        return {
            "result": result_dict,
            "status": "success"
        }

    except Exception as e:
        return {
            "result": "",
            "error": f"Error querying API description: {str(e)}",
            "status": "error"
        }


def parse_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse API description query result for agent use.

    Args:
        result: Raw result from api_desc_func or K8S execution

    Returns:
        Formatted result for agent
    """
    if isinstance(result, dict):
        if result.get("status") == "error":
            return {
                "result": result.get("error", "Unknown error"),
                "status": "error"
            }
        
        result_data = result.get("result", {})
        if isinstance(result_data, dict):
            content = result_data.get("content", [])
            if content and isinstance(content, list) and len(content) > 0:
                return {
                    "result": content[0].get("text", str(result_data)),
                    "status": "success"
                }
        
        return {
            "result": str(result_data),
            "status": "success"
        }
    
    return {
        "result": str(result),
        "status": "success"
    }


def build_k8s_command(api_name: str, app_id: str = "demo_app") -> str:
    """
    Build command for K8S pod execution.

    Args:
        api_name: The API name to query
        app_id: Application ID

    Returns:
        Command string for K8S execution
    """
    escaped_api_name = api_name.replace('"', '\\"').replace("'", "\\'")
    escaped_app_id = app_id.replace('"', '\\"').replace("'", "\\'")
    
    return f'python3 -c "from tools.miaoda.impl.api_desc import api_desc_func; import json; print(json.dumps(api_desc_func(\'{escaped_api_name}\', \'{escaped_app_id}\')))"'


def main():
    """Main entry point for CLI usage."""
    parser = argparse.ArgumentParser(
        description="Query API description by API name.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python api_desc.py "user.login"
  python api_desc.py "product.search" --app_id "my_app"
  python api_desc.py "order.create" --json
        """
    )
    parser.add_argument(
        "--api_name",
        help="The API name to query (positional argument)"
    )
    parser.add_argument(
        "--app_id",
        default="demo_app",
        help="Application ID (default: demo_app)"
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

    result = api_desc_func(args.api_name, args.app_id)

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

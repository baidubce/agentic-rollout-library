#!/usr/bin/env python3
"""
Description: Search for images by keyword using MCP server.
Parameters:
  query (string, required): Search keyword(s). Positional argument.
  limit (int, optional): Number of results to return (default: 10).
  app_id (string, optional): Application ID (default: "demo_app").

Usage:
  As a script: python image_search.py "咖啡" --limit 5 --app_id "my_app"
  As a module: python -m tools.miaoda.impl.image_search "coffee"
  As a function: from tools.miaoda.impl.image_search import image_search_func; image_search_func("coffee")
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

def image_search_func(query: str, limit: int = 10, app_id: str = "demo_app") -> Dict[str, Any]:
    """
    Search for images by keyword from MCP server.

    Args:
        query: Search keyword(s)
        limit: Number of results to return
        app_id: Application ID

    Returns:
        Dictionary containing:
            - result: Image search results
            - status: Status of the query (success/error)
            - error: Error message if failed
    """
    inputs = eval(query)
    if not isinstance(inputs, list):
        inputs = [query]

    headers = {}
    headers["x-miaoda-app-id"] = app_id    

    try:
        async def _call():
            async with sse_client(SERVER_URL, headers=headers) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        "image_search",
                        {
                            "inputs": inputs
                        }
                    )
                    return result

        result = asyncio.run(_call())
        result_dict = json.loads(result.model_dump_json())
        
        return {
            "result": result_dict,
            "status": "success",
            "limit": limit
        }

    except Exception as e:
        return {
            "result": "",
            "error": f"Error searching images: {str(e)}",
            "status": "error"
        }


def parse_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse image search result for agent use.

    Args:
        result: Raw result from image_search_func or K8S execution

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


def build_k8s_command(query: str, limit: int = 10, app_id: str = "demo_app") -> str:
    """
    Build command for K8S pod execution.

    Args:
        query: Search keyword(s)
        limit: Number of results to return
        app_id: Application ID

    Returns:
        Command string for K8S execution
    """
    escaped_query = query.replace('"', '\\"').replace("'", "\\'")
    escaped_app_id = app_id.replace('"', '\\"').replace("'", "\\'")
    
    return f'python3 -c "from tools.miaoda.impl.image_search import image_search_func; import json; print(json.dumps(image_search_func(\'{escaped_query}\', {limit}, \'{escaped_app_id}\'), ensure_ascii=False))"'


def main():
    """Main entry point for CLI usage."""
    parser = argparse.ArgumentParser(
        description="Search for images by keyword.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python image_search.py --inputs '["咖啡", "牛奶"]'
  python image_search.py --inputs '["coffee"]' --limit 5
  python image_search.py --inputs '["sunset"]' --app_id "my_app" --json
        """
    )
    parser.add_argument(
        "--inputs",
        type=str,
        default="",
        help="Search keywords (positional argument)"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Number of results to return (default: 10)"
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

    result = image_search_func(args.inputs, args.limit, args.app_id)

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

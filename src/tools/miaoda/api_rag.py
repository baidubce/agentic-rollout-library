#!/usr/bin/env python3
"""
Description: Query API information using RAG (Retrieval-Augmented Generation) via MCP server.
Parameters:
  query (string, required): The query question. Positional argument.
  app_id (string, optional): Application ID (default: "1111").

Usage:
  As a script: python api_rag.py "图像内容理解-获取结果接口" --app_id "my_app"
  As a module: python -m tools.miaoda.impl.api_rag "search API"
  As a function: from tools.miaoda.impl.api_rag import api_rag_func; api_rag_func("search API")
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

def api_rag_func(query: str, app_id: str = "1111") -> Dict[str, Any]:
    """
    Query API information using RAG from MCP server.

    Args:
        query: The query question
        app_id: Application ID

    Returns:
        Dictionary containing:
            - result: API RAG query result data
            - status: Status of the query (success/error)
            - error: Error message if failed
    """
    # 构建 headers
    headers = {}
    headers["x-miaoda-app-id"] = app_id

    try:
        async def _call():
            async with sse_client(SERVER_URL, headers=headers) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        "api_rag",
                        {
                            "input": query,
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
            "error": f"Error querying API via RAG: {str(e)}",
            "status": "error"
        }


def parse_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse API RAG query result for agent use.

    Args:
        result: Raw result from api_rag_func or K8S execution

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


def build_k8s_command(query: str, app_id: str = "1111") -> str:
    """
    Build command for K8S pod execution.

    Args:
        query: The query question
        app_id: Application ID

    Returns:
        Command string for K8S execution
    """
    escaped_query = query.replace('"', '\\"').replace("'", "\\'")
    escaped_app_id = app_id.replace('"', '\\"').replace("'", "\\'")
    
    return f'python3 -c "from tools.miaoda.impl.api_rag import api_rag_func; import json; print(json.dumps(api_rag_func(\'{escaped_query}\', \'{escaped_app_id}\'), ensure_ascii=False))"'


def main():
    """Main entry point for CLI usage."""
    parser = argparse.ArgumentParser(
        description="Query API information using RAG.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python api_rag.py "图像内容理解-获取结果接口"
  python api_rag.py "search API" --app_id "my_app"
  python api_rag.py "user authentication" --json
        """
    )
    parser.add_argument(
        "--query",
        help="The input question (positional argument)"
    )
    parser.add_argument(
        "--app_id",
        default="1111",
        help="Application ID (default: 1111)"
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

    result = api_rag_func(args.query, args.app_id)

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

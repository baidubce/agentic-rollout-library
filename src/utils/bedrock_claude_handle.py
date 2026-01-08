#!/usr/bin/env python3
"""
AWS Bedrock Claude API Handle for Agentic Rollout Library

This module provides a unified LLM call handler for AWS Bedrock Claude models,
compatible with the agentic rollout library's LLMNode interface.
"""

import boto3
import json
import os
from typing import Dict, List, Optional, Any
import asyncio
from concurrent.futures import ThreadPoolExecutor


class BedrockClaudeHandle:
    """
    AWS Bedrock Claude API handler compatible with LLMNode interface.

    This handler supports:
    - Prompt caching for cost optimization
    - Async API calls
    - Chat message format
    - Configurable model parameters
    """

    def __init__(
        self,
        endpoint_url: str = None,
        region_name: str = "us-west-2",
        aws_access_key_id: str = None,
        aws_secret_access_key: str = None,
        model_id: str = "us.anthropic.claude-sonnet-4-20250514-v1:0",
        read_timeout: int = 300,
        connect_timeout: int = 300,
        max_retries: int = 20,
        use_cache: bool = True
    ):
        """
        Initialize Bedrock Claude API handle.

        Args:
            endpoint_url: Custom endpoint URL for Bedrock service
            region_name: AWS region name
            aws_access_key_id: AWS access key ID (defaults to AK env var)
            aws_secret_access_key: AWS secret access key (defaults to SK env var)
            model_id: Claude model ID to use
            read_timeout: Read timeout in seconds
            connect_timeout: Connection timeout in seconds
            max_retries: Maximum number of retry attempts
            use_cache: Enable prompt caching
        """
        self.model_id = model_id
        self.use_cache = use_cache

        # Get credentials from environment or parameters
        access_key = aws_access_key_id or os.getenv("BEDROCK_AK")
        secret_key = aws_secret_access_key or os.getenv("BEDROCK_SK")

        if not access_key or not secret_key:
            raise ValueError(
                "AWS credentials not provided. Please set BEDROCK_AK and BEDROCK_SK "
                "environment variables or pass aws_access_key_id and aws_secret_access_key "
                "to the constructor."
            )

        # Use provided endpoint URL or default
        self.endpoint_url = endpoint_url or "https://mxyf-br.miaoda.io"

        # Initialize Bedrock client
        self.bedrock = boto3.client(
            'bedrock-runtime',
            endpoint_url=self.endpoint_url,
            region_name=region_name,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=boto3.session.Config(
                read_timeout=read_timeout,
                connect_timeout=connect_timeout,
                retries={'max_attempts': max_retries}
            )
        )

        # Thread pool for async execution
        self.executor = ThreadPoolExecutor(max_workers=5)

    def _convert_messages_to_bedrock_format(
        self,
        messages: List[Dict[str, Any]]
    ) -> tuple[List[Dict], List[Dict]]:
        """
        Convert standard chat messages to Bedrock Converse API format.

        Args:
            messages: List of message dicts with 'role' and 'content' keys

        Returns:
            Tuple of (bedrock_messages, system_prompts)
        """
        bedrock_messages = []
        system_prompts = []

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if role == "system":
                # System messages go into system_prompts
                system_prompts.append({
                    "text": content
                })
            else:
                # User and assistant messages
                bedrock_messages.append({
                    "role": role,
                    "content": [{"text": content}]
                })

        # Add cache point to system prompts AFTER all system messages (if caching is enabled)
        if self.use_cache and system_prompts:
            system_prompts.append({
                "cachePoint": {"type": "default"}
            })

        return bedrock_messages, system_prompts

    def _add_cache_point_to_last_message(self, bedrock_messages: List[Dict[str, Any]]) -> None:
        """
        Add cache point to the last user message and remove all previous cache points.

        This ensures:
        1. Only one cache point exists (avoiding the 4 cache point limit)
        2. The entire conversation history is cached via Claude's prefix caching

        Args:
            bedrock_messages: List of messages in Bedrock format (modified in-place)
        """
        if not self.use_cache or not bedrock_messages:
            return

        # STEP 1: Remove ALL existing cache points from all messages
        for message in bedrock_messages:
            if "content" in message:
                message["content"] = [
                    item for item in message["content"]
                    if not (isinstance(item, dict) and "cachePoint" in item)
                ]

        # STEP 2: Add cache point ONLY to the LAST user message
        # Find the last user message
        for i in range(len(bedrock_messages) - 1, -1, -1):
            if bedrock_messages[i]["role"] == "user":
                bedrock_messages[i]["content"].append({
                    "cachePoint": {"type": "default"}
                })
                break

    def _call_bedrock_sync(
        self,
        messages: List[Dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: int = 8000,
        top_p: float = 0.9
    ) -> Dict[str, Any]:
        """
        Synchronous call to Bedrock Converse API.

        Args:
            messages: List of chat messages
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            top_p: Nucleus sampling parameter

        Returns:
            Response dictionary with content and usage metrics
        """
        # Convert messages to Bedrock format
        bedrock_messages, system_prompts = self._convert_messages_to_bedrock_format(messages)

        # Add cache point to the last user message (and remove old cache points)
        # This enables prompt caching for multi-turn conversations
        self._add_cache_point_to_last_message(bedrock_messages)

        # Build API request
        kwargs = {
            "modelId": self.model_id,
            "messages": bedrock_messages,
            "inferenceConfig": {
                "maxTokens": max_tokens,
                "temperature": temperature,
            }
        }

        # Add system prompts if present
        if system_prompts:
            kwargs["system"] = system_prompts

        # Debug: Print the request to see if cache points are present
        if os.getenv("DEBUG_CACHE"):
            print("\n[DEBUG] Request payload:")
            print(f"System prompts: {json.dumps(system_prompts, indent=2)}")
            print(f"Messages ({len(bedrock_messages)} total):")
            for i, msg in enumerate(bedrock_messages):
                has_cache = any(isinstance(item, dict) and "cachePoint" in item for item in msg.get("content", []))
                cache_marker = " [CACHE POINT]" if has_cache else ""
                print(f"  {i}: {msg['role']}{cache_marker}")
                if has_cache:
                    print(f"      Content: {msg['content']}")

        try:
            # Call Bedrock Converse API
            response = self.bedrock.converse(**kwargs)

            # Extract response content
            output_text = response['output']['message']['content'][0]['text']

            # Extract usage metrics
            usage = response.get('usage', {})

            return {
                "content": output_text,
                "role": "assistant",
                "usage": {
                    "input_tokens": usage.get('inputTokens', 0),
                    "output_tokens": usage.get('outputTokens', 0),
                    "cache_read_tokens": usage.get('cacheReadInputTokens', 0),
                    "cache_write_tokens": usage.get('cacheWriteInputTokens', 0)
                },
                "stop_reason": response.get('stopReason', 'unknown'),
                "model": self.model_id
            }

        except Exception as e:
            raise Exception(f"Bedrock API call failed: {str(e)}")

    async def __call__(
        self,
        messages: List[Dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: int = 8000,
        top_p: float = 0.9,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Async call to Bedrock Claude API (compatible with LLMNode).

        This is the main entry point used by LLMNode.

        Args:
            messages: List of chat messages
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            top_p: Nucleus sampling parameter
            **kwargs: Additional parameters (ignored for compatibility)

        Returns:
            Response dictionary with content and usage metrics
        """
        loop = asyncio.get_event_loop()

        # Run synchronous call in thread pool
        result = await loop.run_in_executor(
            self.executor,
            lambda: self._call_bedrock_sync(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                top_p=top_p
            )
        )

        return result

    def close(self):
        """Close thread pool executor."""
        self.executor.shutdown(wait=True)

    async def close_async(self):
        """Async close method for compatibility."""
        await asyncio.get_event_loop().run_in_executor(None, self.close)


def create_bedrock_claude_handle(
    endpoint_url: str = None,
    region_name: str = "us-west-2",
    aws_access_key_id: str = None,
    aws_secret_access_key: str = None,
    model_id: str = "us.anthropic.claude-sonnet-4-20250514-v1:0",
    use_cache: bool = True,
    **kwargs
) -> BedrockClaudeHandle:
    """
    Factory function to create a Bedrock Claude API handle.

    This function provides a simple interface for creating handlers
    compatible with the agentic rollout library's LLMNode.

    Args:
        endpoint_url: Custom endpoint URL for Bedrock service
        region_name: AWS region name
        aws_access_key_id: AWS access key ID
        aws_secret_access_key: AWS secret access key
        model_id: Claude model ID to use
        use_cache: Enable prompt caching
        **kwargs: Additional parameters passed to BedrockClaudeHandle

    Returns:
        BedrockClaudeHandle instance

    Example:
        >>> llm_handle = create_bedrock_claude_handle(
        ...     endpoint_url="https://mxyf-br.miaoda.io",
        ...     model_id="us.anthropic.claude-sonnet-4-20250514-v1:0",
        ...     use_cache=True
        ... )
        >>> llm_node = LLMNode(
        ...     name="ClaudeLLM",
        ...     function_handle=llm_handle,
        ...     model_config={"temperature": 0.7, "max_tokens": 8000}
        ... )
    """
    return BedrockClaudeHandle(
        endpoint_url=endpoint_url,
        region_name=region_name,
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
        model_id=model_id,
        use_cache=use_cache,
        **kwargs
    )


# Example usage and testing
async def test_bedrock_handle():
    """Test function to verify the handle works correctly."""
    print("=== Testing Bedrock Claude Handle ===\n")

    # Create handle
    handle = create_bedrock_claude_handle(
        model_id="us.anthropic.claude-sonnet-4-20250514-v1:0",
        use_cache=True
    )

    # Test messages
    messages = [
        {
            "role": "system",
            "content": "You are a helpful AI assistant."
        },
        {
            "role": "user",
            "content": "What is the capital of France? Answer in one sentence."
        }
    ]

    try:
        print("Sending test message to Claude...")
        response = await handle(messages, temperature=0.7, max_tokens=100)

        print(f"Response: {response['content']}\n")
        print(f"Usage: {response['usage']}")
        print(f"Model: {response['model']}")
        print(f"Stop reason: {response['stop_reason']}")

        print("\n✅ Test successful!")

    except Exception as e:
        print(f"\n❌ Test failed: {str(e)}")

    finally:
        await handle.close_async()


if __name__ == "__main__":
    # Run test
    asyncio.run(test_bedrock_handle())

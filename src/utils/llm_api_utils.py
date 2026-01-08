#!/usr/bin/env python3
"""
LLM API utilities for creating API handles for different LLM providers.
"""

import os
import requests
import asyncio
import json
import logging
from typing import List, Dict, Callable

# Configure logger
logger = logging.getLogger(__name__)

def is_debug_enabled() -> bool:
    """Check if debug mode is enabled via environment variables."""
    return os.getenv('DEBUG', '').lower() in ('1', 'true', 'yes') or \
           os.getenv('LLM_DEBUG', '').lower() in ('1', 'true', 'yes')


def parse_pd_separated_response(response_text: str) -> Dict:
    """
    Parse response text that may contain multiple JSON objects (PD separated version).

    PD separated models return two JSON responses:
    1. First JSON: contains draft/prefix content
    2. Second JSON: contains final complete response

    The final content should be the concatenation of first JSON's content + second JSON's content.

    This function also handles Claude Opus 4.5 format which returns content as an array directly.

    Args:
        response_text: Raw response text that may contain one or more JSON objects

    Returns:
        Merged JSON response with combined content, normalized to OpenAI format

    Raises:
        ValueError: If no valid JSON found or if error response is returned
    """
    # Try to find all JSON objects in the response
    json_objects = []
    decoder = json.JSONDecoder()
    pos = 0

    while pos < len(response_text):
        # Skip whitespace
        while pos < len(response_text) and response_text[pos].isspace():
            pos += 1

        if pos >= len(response_text):
            break

        try:
            obj, end_pos = decoder.raw_decode(response_text, pos)
            json_objects.append(obj)
            # raw_decode returns absolute position, not relative offset
            pos = end_pos
        except json.JSONDecodeError:
            # No more valid JSON objects
            break

    if len(json_objects) == 0:
        raise ValueError("No valid JSON found in response")

    # Separate error objects from normal objects
    error_objects = [obj for obj in json_objects if obj.get('object') == 'error']
    normal_objects = [obj for obj in json_objects if obj.get('object') != 'error']

    # If there are error objects, check if we also have normal objects
    if error_objects:
        if not normal_objects:
            # Only errors, no valid response - raise the error
            error_obj = error_objects[0]
            error_msg = error_obj.get('message', 'Unknown error')
            error_type = error_obj.get('type', 'Unknown type')
            error_code = error_obj.get('code', 'Unknown code')
            raise ValueError(f"API returned error: {error_msg} - {error_code} (type: {error_type})")
        else:
            # We have both errors and normal objects
            # Log the error but continue with normal objects
            print(f"Warning: API returned error objects along with normal response: {error_objects}")
            # Use only normal objects for further processing
            json_objects = normal_objects

    # If only one JSON, normalize and return it
    if len(json_objects) == 1:
        return _normalize_response_format(json_objects[0])

    # If two or more JSONs (PD separated), merge them
    # Use the last JSON as base and prepend content from previous JSONs
    first_json = json_objects[0]
    final_json = json_objects[-1]  # Use the last one as the base

    # Extract content from first JSON
    first_content = ""
    if 'choices' in first_json and len(first_json['choices']) > 0:
        first_message = first_json['choices'][0].get('message', {})
        first_content = first_message.get('content', '')

    # Merge: prepend first content to final content
    if first_content and 'choices' in final_json and len(final_json['choices']) > 0:
        final_message = final_json['choices'][0].get('message', {})
        final_content = final_message.get('content', '')
        final_json['choices'][0]['message']['content'] = first_content + final_content

    return _normalize_response_format(final_json)


def _normalize_response_format(response: Dict) -> Dict:
    """
    Normalize different response formats to OpenAI-compatible format.

    Handles:
    1. OpenAI format: {"choices": [{"message": {"role": "assistant", "content": "..."}}]}
    2. Claude Opus 4.5 format: {"type": "message", "role": "assistant", "content": [{"type": "text", "text": "..."}]}

    Args:
        response: Raw response dictionary

    Returns:
        Normalized response in OpenAI format
    """
    # Check if it's already in OpenAI format
    if 'choices' in response:
        return response

    # Check if it's Claude Opus 4.5 format
    if response.get('type') == 'message' and 'content' in response and isinstance(response['content'], list):
        # Extract text content from content array
        text_content = ""
        for content_block in response['content']:
            if isinstance(content_block, dict) and content_block.get('type') == 'text':
                text_content += content_block.get('text', '')

        # Convert to OpenAI format
        normalized = {
            'id': response.get('id', ''),
            'object': 'chat.completion',
            'created': 0,
            'model': response.get('model', ''),
            'choices': [{
                'index': 0,
                'message': {
                    'role': response.get('role', 'assistant'),
                    'content': text_content
                },
                'finish_reason': response.get('stop_reason', 'stop')
            }]
        }

        # Add usage information if present
        if 'usage' in response:
            # Map Claude usage format to OpenAI format
            claude_usage = response['usage']
            normalized['usage'] = {
                'prompt_tokens': claude_usage.get('input_tokens', 0) +
                                claude_usage.get('cache_creation_input_tokens', 0) +
                                claude_usage.get('cache_read_input_tokens', 0),
                'completion_tokens': claude_usage.get('output_tokens', 0),
                'total_tokens': (claude_usage.get('input_tokens', 0) +
                               claude_usage.get('cache_creation_input_tokens', 0) +
                               claude_usage.get('cache_read_input_tokens', 0) +
                               claude_usage.get('output_tokens', 0))
            }

        return normalized

    # If format is unknown, return as-is and let error handling deal with it
    return response


def create_openai_api_handle(
    base_url: str,
    api_key: str,
    model: str,
    clear_proxy: bool = True
) -> Callable:
    """
    Create a function handle for OpenAI-compatible API using requests.

    Args:
        base_url: Base URL of the API endpoint (e.g., "http://api.openai.com/v1")
        api_key: API key for authentication
        model: Model name to use (e.g., "gpt-4", "deepseek-v3-1-terminus")
        clear_proxy: Whether to clear proxy environment variables (default: True)

    Returns:
        A callable function that takes messages and kwargs, returns LLM response

    Example:
        >>> llm_handle = create_openai_api_handle(
        ...     base_url="http://localhost:8000/v1",
        ...     api_key="your-api-key",
        ...     model="gpt-4"
        ... )
        >>> response = llm_handle([{"role": "user", "content": "Hello"}])
    """

    # Clear proxy environment variables to avoid SOCKS proxy issues
    if clear_proxy:
        os.environ.pop('HTTP_PROXY', None)
        os.environ.pop('HTTPS_PROXY', None)
        os.environ.pop('http_proxy', None)
        os.environ.pop('https_proxy', None)
        os.environ.pop('ALL_PROXY', None)
        os.environ.pop('all_proxy', None)

    def openai_api_handle(messages: List[Dict], **kwargs) -> Dict:
        """
        Handle OpenAI-compatible API requests.

        Args:
            messages: List of message dictionaries with 'role' and 'content'
            **kwargs: Additional parameters (temperature, max_tokens, etc.)

        Returns:
            Response dictionary with 'role', 'content', and optionally 'usage'

        Raises:
            RuntimeError: If API request fails or response is invalid
        """
        if "messages" in base_url:
            url = base_url
        else:
            url = f"{base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json; charset=utf-8"
        }
        
        payload = {
            "model": model,
            "messages": messages,
            "temperature": kwargs.get("temperature", 0.7),
            "max_tokens": kwargs.get("max_tokens", 4000),
            "top_p": kwargs.get("top_p", 0.95),
            "stream": False
            #"tool_choice": "none"
        }

        # Debug logging for input
        if is_debug_enabled():
            logger.info("=" * 80)
            logger.info("LLM API Request (sync):")
            logger.info(f"Model: {model}")
            logger.info(f"URL: {url}")
            logger.info(f"Messages: {json.dumps(messages, indent=2, ensure_ascii=False)}")
            logger.info(f"Parameters: temperature={payload['temperature']}, max_tokens={payload['max_tokens']}, top_p={payload['top_p']}")
            logger.info("=" * 80)

        try:
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=kwargs.get('timeout', 120),
                proxies={'http': None, 'https': None}  # Disable proxy
            )
            response.raise_for_status()

            # Parse response text to handle PD separated responses (multiple JSONs)
            response.encoding = "utf-8"
            response_text = response.text
            data = parse_pd_separated_response(response_text)

            print(data)

            choice = data['choices'][0]
            message = choice['message']

            result = {
                "role": message.get('role', 'assistant'),
                "content": message.get('content', ''),
                "model": data.get('model', model)
            }

            if 'usage' in data:
                result['usage'] = data['usage']

            # Debug logging for output
            if is_debug_enabled():
                logger.info("=" * 80)
                logger.info("LLM API Response (sync):")
                logger.info(f"Model: {result.get('model', 'unknown')}")
                logger.info(f"Content: {result.get('content', '')}")
                if 'usage' in result:
                    logger.info(f"Usage: {json.dumps(result['usage'], indent=2)}")
                logger.info("=" * 80)

            return result

        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"API request failed: {str(e)}")
        except (KeyError, json.JSONDecodeError, ValueError) as e:
            raise RuntimeError(f"Invalid API response: {str(e)}")

    return openai_api_handle



def create_openai_api_handle_async(
    base_url: str,
    api_key: str,
    model: str,
    clear_proxy: bool = True,
    use_completion: bool = False
) -> Callable:
    """
    Create an async function handle for OpenAI-compatible API using aiohttp.

    Args:
        base_url: Base URL of the API endpoint (e.g., "http://api.openai.com/v1")
        api_key: API key for authentication
        model: Model name to use (e.g., "gpt-4", "deepseek-v3-1-terminus")
        clear_proxy: Whether to clear proxy environment variables (default: True)
        use_completion: Whether to use completion endpoint instead of chat endpoint (default: False)

    Returns:
        An async callable function that takes messages and kwargs, returns LLM response

    Example:
        >>> llm_handle = create_openai_api_handle_async(
        ...     base_url="http://localhost:8000/v1",
        ...     api_key="your-api-key",
        ...     model="gpt-4"
        ... )
        >>> response = await llm_handle([{"role": "user", "content": "Hello"}])
    """

    if clear_proxy:
        os.environ.pop('HTTP_PROXY', None)
        os.environ.pop('HTTPS_PROXY', None)
        os.environ.pop('http_proxy', None)
        os.environ.pop('https_proxy', None)
        os.environ.pop('ALL_PROXY', None)
        os.environ.pop('all_proxy', None)

    async def openai_api_handle_async(messages: List[Dict], **kwargs) -> Dict:
        """
        Handle OpenAI-compatible API requests asynchronously.

        Args:
            messages: List of message dictionaries with 'role' and 'content'
                      For completion endpoint: expects single message with 'content' as prompt
            **kwargs: Additional parameters (temperature, max_tokens, etc.)

        Returns:
            Response dictionary with 'role', 'content', and optionally 'usage'

        Raises:
            RuntimeError: If API request fails or response is invalid
        """
        import aiohttp

        # Create a new session for each request to avoid unclosed session warnings
        # Increase connection pool limits for high concurrency
        connector = aiohttp.TCPConnector(
            limit=1000,              # Support up to 1000 concurrent connections
            limit_per_host=600,      # Support 600 connections to same host (for 500+ concurrent)
            force_close=False,       # Reuse connections (HTTP Keep-Alive)
            enable_cleanup_closed=True
        )

        # Choose endpoint based on use_completion flag
        if use_completion:
            url = f"{base_url.rstrip('/')}/completions"
            # For completion endpoint, extract prompt from messages
            if isinstance(messages, list) and len(messages) > 0:
                prompt = messages[0].get('content', '')
            else:
                prompt = ''

            payload = {
                "model": model,
                "prompt": prompt,
                "temperature": kwargs.get("temperature", 0.7),
                "max_tokens": kwargs.get("max_tokens", 8000),
                "top_p": kwargs.get("top_p", 0.95),
                "stream": False
                #"tool_choice": "none"
            }
        else:
            if "messages" in base_url:
                url = base_url
            else:
                url = f"{base_url.rstrip('/')}/chat/completions"
            payload = {
                "model": model,
                "messages": messages,
                "temperature": kwargs.get("temperature", 0.7),
                "max_tokens": kwargs.get("max_tokens", 4000),
                "top_p": kwargs.get("top_p", 0.95),
                "stream": False
                #"tool_choice": "none"
            }

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }


        # Debug logging for input
        if is_debug_enabled():
            logger.info("=" * 80)
            logger.info("LLM API Request (async):")
            logger.info(f"Model: {model}")
            logger.info(f"URL: {url}")
            if use_completion:
                logger.info(f"Prompt: {payload.get('prompt', '')}")
            else:
                logger.info(f"Messages: {json.dumps(messages, indent=2, ensure_ascii=False)}")
            logger.info(f"Parameters: temperature={payload['temperature']}, max_tokens={payload['max_tokens']}, top_p={payload['top_p']}")
            logger.info("=" * 80)

        # Use async context manager to ensure session is properly closed
        async with aiohttp.ClientSession(connector=connector) as session:
            try:
                timeout = aiohttp.ClientTimeout(total=kwargs.get('timeout', 120))
                async with session.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=timeout
                ) as response:
                    # Check status code before reading response
                    if response.status >= 400:
                        error_text = await response.text()
                        raise RuntimeError(
                            f"API request failed: {response.status}, "
                            f"message='{response.reason}', url='{url}'"
                        )

                    # Get response text first to handle PD separated responses
                    response_text = await response.text()

                # Parse response text to handle PD separated responses (multiple JSONs)
                data = parse_pd_separated_response(response_text)

                choice = data['choices'][0]

                # Parse response based on endpoint type
                if use_completion:
                    # Completion endpoint returns 'text' field
                    content = choice.get('text', '')
                    result = {
                        "role": "assistant",
                        "content": content,
                        "model": data.get('model', model)
                    }
                else:
                    # Chat endpoint returns 'message' object
                    message = choice['message']
                    result = {
                        "role": message.get('role', 'assistant'),
                        "content": message.get('content', ''),
                        "model": data.get('model', model)
                    }

                if 'usage' in data:
                    result['usage'] = data['usage']

                # Debug logging for output
                if is_debug_enabled():
                    logger.info("=" * 80)
                    logger.info("LLM API Response (async):")
                    logger.info(f"Model: {result.get('model', 'unknown')}")
                    logger.info(f"Content: {result.get('content', '')}")
                    if 'usage' in result:
                        logger.info(f"Usage: {json.dumps(result['usage'], indent=2)}")
                    logger.info("=" * 80)

                return result

            except aiohttp.ClientError as e:
                raise RuntimeError(f"API request failed: {str(e)}")
            except (KeyError, ValueError) as e:
                raise RuntimeError(f"Invalid API response: {str(e)}")

    return openai_api_handle_async


__all__ = [
    'create_openai_api_handle',
    'create_openai_api_handle_async'
]

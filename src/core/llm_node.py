"""LLM Node implementation."""

from typing import Any, Callable, Dict, List, Optional
import requests
import json
import os
import time
import asyncio
from .base_node import BaseNode


class LLMNode(BaseNode):
    """Node for interfacing with language models."""

    def __init__(self,
                 name: str = None,
                 function_handle: Callable = None,
                 model_config: Dict = None,
                 timeline_enabled: bool = False,
                 timeout: float = None):
        """
        Initialize the LLM Node.

        Args:
            name: Optional name for the node
            function_handle: Callable that implements the LLM interface
            model_config: Configuration dictionary for the model
            timeline_enabled: Enable automatic timeline tracking for this node
            timeout: Timeout in seconds for LLM execution (None = no timeout)
        """
        super().__init__(name, timeline_enabled=timeline_enabled, timeout=timeout)
        self.function_handle = function_handle
        self.model_config = model_config or {}
        self.default_params = {
            "temperature": 0.7,
            "max_tokens": 2000,
            "top_p": 0.95
        }
        # Shared aiohttp session for connection pooling (created lazily)
        self._shared_session = None
        self.retry_config = {
            "max_retries": 3,
            "initial_delay": 1,
            "max_delay": 60,
            "exponential_base": 2
        }
        self.last_response = None

    def set_function_handle(self, function_handle: Callable) -> None:
        """
        Set or update the LLM function handle.

        Args:
            function_handle: Callable that takes List[Dict] and returns Dict
        """
        self.function_handle = function_handle
        self.logger.info("Updated LLM function handle")

    def set_model_config(self, config: Dict) -> None:
        """
        Update model configuration.

        Args:
            config: Configuration dictionary
        """
        self.model_config.update(config)
        self.logger.info(f"Updated model config: {config}")

    def set_parameter(self, key: str, value: Any) -> None:
        """
        Set a specific model parameter.

        Args:
            key: Parameter key
            value: Parameter value
        """
        self.default_params[key] = value

    def set_retry_config(self, max_retries: int = None, initial_delay: float = None,
                        max_delay: float = None, exponential_base: float = None) -> None:
        """
        Configure retry behavior.

        Args:
            max_retries: Maximum number of retry attempts (default: 3)
            initial_delay: Initial delay in seconds between retries (default: 1)
            max_delay: Maximum delay in seconds between retries (default: 60)
            exponential_base: Base for exponential backoff (default: 2)
        """
        if max_retries is not None:
            self.retry_config['max_retries'] = max_retries
        if initial_delay is not None:
            self.retry_config['initial_delay'] = initial_delay
        if max_delay is not None:
            self.retry_config['max_delay'] = max_delay
        if exponential_base is not None:
            self.retry_config['exponential_base'] = exponential_base
        self.logger.info(f"Updated retry config: {self.retry_config}")

    async def _retry_with_backoff_async(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute an async function with retry logic and exponential backoff.

        Args:
            func: Async function to execute
            *args: Positional arguments for the function
            **kwargs: Keyword arguments for the function

        Returns:
            Function result

        Raises:
            Last exception if all retries failed
        """
        max_retries = self.retry_config['max_retries']
        delay = self.retry_config['initial_delay']
        max_delay = self.retry_config['max_delay']
        exponential_base = self.retry_config['exponential_base']

        last_exception = None
        for attempt in range(max_retries + 1):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                last_exception = e
                if attempt < max_retries:
                    self.logger.warning(f"Attempt {attempt + 1} failed: {str(e)}. Retrying in {delay} seconds...")
                    await asyncio.sleep(delay)
                    delay = min(delay * exponential_base, max_delay)
                else:
                    self.logger.error(f"All {max_retries + 1} attempts failed. Last error: {str(e)}")

        raise last_exception

    def _retry_with_backoff(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute a function with retry logic and exponential backoff.

        Args:
            func: Function to execute
            *args: Positional arguments for the function
            **kwargs: Keyword arguments for the function

        Returns:
            Function result

        Raises:
            Last exception if all retries failed
        """
        max_retries = self.retry_config['max_retries']
        delay = self.retry_config['initial_delay']
        max_delay = self.retry_config['max_delay']
        exponential_base = self.retry_config['exponential_base']

        last_exception = None
        for attempt in range(max_retries + 1):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_exception = e
                if attempt < max_retries:
                    self.logger.warning(f"Attempt {attempt + 1} failed: {str(e)}. Retrying in {delay} seconds...")
                    time.sleep(delay)
                    delay = min(delay * exponential_base, max_delay)
                else:
                    self.logger.error(f"All {max_retries + 1} attempts failed. Last error: {str(e)}")

        raise last_exception

    async def process_async(self, input_data: List[Dict]) -> Dict:
        """
        Process the input messages through the LLM asynchronously.

        Args:
            input_data: List of message dictionaries from Context Engineering Node

        Returns:
            LLM response as a dictionary
        """
        if not self.function_handle:
            self.logger.warning("No function handle set, using default mock response")
            return await self._default_llm_response_async(input_data)

        if not self.validate_input(input_data):
            raise ValueError("Invalid input format for LLM Node")

        params = {**self.default_params, **self.model_config}
        
        # Add timeout if configured
        if self.timeout:
            params['timeout'] = self.timeout

        try:
            response = await self._retry_with_backoff_async(
                self.function_handle,
                messages=input_data,
                **params
            )

            if not isinstance(response, dict):
                response = {"content": str(response), "role": "assistant"}

            if "role" not in response:
                response["role"] = "assistant"

            self.last_response = response
            self.logger.info("LLM processing completed successfully")

            return response

        except Exception as e:
            self.logger.error(f"Error in LLM processing: {str(e)}")
            raise

    def process(self, input_data: List[Dict]) -> Dict:
        """
        Process the input messages through the LLM.

        Args:
            input_data: List of message dictionaries from Context Engineering Node

        Returns:
            LLM response as a dictionary
        """
        if not self.function_handle:
            # Default implementation - returns a mock response
            self.logger.warning("No function handle set, using default mock response")
            return self._default_llm_response(input_data)

        # Validate input
        if not self.validate_input(input_data):
            raise ValueError("Invalid input format for LLM Node")

        # Merge default params with model config
        params = {**self.default_params, **self.model_config}
        
        # Add timeout if configured
        if self.timeout:
            params['timeout'] = self.timeout

        try:
            # Call the custom LLM function with retry logic
            response = self._retry_with_backoff(
                self.function_handle,
                messages=input_data,
                **params
            )

            # Ensure response is in expected format
            if not isinstance(response, dict):
                response = {"content": str(response), "role": "assistant"}

            if "role" not in response:
                response["role"] = "assistant"

            self.last_response = response
            self.logger.info("LLM processing completed successfully")

            return response

        except Exception as e:
            self.logger.error(f"Error in LLM processing: {str(e)}")
            raise

    async def _default_llm_response_async(self, messages: List[Dict]) -> Dict:
        """
        Default LLM response using OpenAI API standard via aiohttp.

        Args:
            messages: Input messages

        Returns:
            LLM response from OpenAI-compatible endpoint
        """
        api_key = self.model_config.get('api_key') or os.getenv('OPENAI_API_KEY')
        base_url = self.model_config.get('base_url') or os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1')
        model = self.model_config.get('model') or os.getenv('OPENAI_MODEL', 'gpt-3.5-turbo')

        if not api_key:
            self.logger.warning("No API key configured, returning mock response")
            last_user_message = None
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    last_user_message = msg.get("content", "")
                    break
            return {
                "role": "assistant",
                "content": f"I received your message: {last_user_message}",
                "model": "mock",
                "usage": {
                    "prompt_tokens": sum(len(str(m.get("content", ""))) for m in messages),
                    "completion_tokens": 10,
                    "total_tokens": sum(len(str(m.get("content", ""))) for m in messages) + 10
                }
            }

        url = f"{base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": model,
            "messages": messages,
            "temperature": self.default_params.get("temperature", 0.7),
            "max_tokens": self.default_params.get("max_tokens", 2000),
            "top_p": self.default_params.get("top_p", 0.95)
        }

        for key in ['stream', 'n', 'stop', 'presence_penalty', 'frequency_penalty', 'logit_bias', 'user']:
            if key in self.model_config:
                payload[key] = self.model_config[key]

        async def _make_api_call():
            import aiohttp
            # Create shared session if not exists (lazy initialization with connection pooling)
            if self._shared_session is None or self._shared_session.closed:
                connector = aiohttp.TCPConnector(limit=100, limit_per_host=30)
                self._shared_session = aiohttp.ClientSession(connector=connector)

            async with self._shared_session.post(
                url,
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=self.model_config.get('timeout', 60))
            ) as response:
                response.raise_for_status()
                return await response.json()

        try:
            data = await self._retry_with_backoff_async(_make_api_call)

            choice = data['choices'][0]
            message = choice['message']

            result = {
                "role": message.get('role', 'assistant'),
                "content": message.get('content', ''),
                "model": data.get('model', model)
            }

            if 'usage' in data:
                result['usage'] = data['usage']

            if 'function_call' in message:
                result['function_call'] = message['function_call']

            if 'tool_calls' in message:
                result['tool_calls'] = message['tool_calls']

            return result

        except Exception as e:
            self.logger.error(f"API request failed: {str(e)}")
            raise RuntimeError(f"Failed to call LLM API: {str(e)}")

    def _default_llm_response(self, messages: List[Dict]) -> Dict:
        """
        Default LLM response using OpenAI API standard via requests.

        Args:
            messages: Input messages

        Returns:
            LLM response from OpenAI-compatible endpoint
        """
        # Get API configuration from environment or model_config
        api_key = self.model_config.get('api_key') or os.getenv('OPENAI_API_KEY')
        base_url = self.model_config.get('base_url') or os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1')
        model = self.model_config.get('model') or os.getenv('OPENAI_MODEL', 'gpt-3.5-turbo')

        if not api_key:
            self.logger.warning("No API key configured, returning mock response")
            last_user_message = None
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    last_user_message = msg.get("content", "")
                    break
            return {
                "role": "assistant",
                "content": f"I received your message: {last_user_message}",
                "model": "mock",
                "usage": {
                    "prompt_tokens": sum(len(str(m.get("content", ""))) for m in messages),
                    "completion_tokens": 10,
                    "total_tokens": sum(len(str(m.get("content", ""))) for m in messages) + 10
                }
            }

        # Prepare the request
        url = f"{base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        # Build the request payload
        payload = {
            "model": model,
            "messages": messages,
            "temperature": self.default_params.get("temperature", 0.7),
            "max_tokens": self.default_params.get("max_tokens", 2000),
            "top_p": self.default_params.get("top_p", 0.95)
        }

        # Add any additional parameters from model_config
        for key in ['stream', 'n', 'stop', 'presence_penalty', 'frequency_penalty', 'logit_bias', 'user']:
            if key in self.model_config:
                payload[key] = self.model_config[key]

        def _make_api_call():
            # Make the API request
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=self.model_config.get('timeout', 60)
            )
            response.raise_for_status()
            return response.json()

        try:
            # Make the API request with retry logic
            data = self._retry_with_backoff(_make_api_call)

            # Extract the message content
            choice = data['choices'][0]
            message = choice['message']

            # Build the response
            result = {
                "role": message.get('role', 'assistant'),
                "content": message.get('content', ''),
                "model": data.get('model', model)
            }

            # Add usage information if available
            if 'usage' in data:
                result['usage'] = data['usage']

            # Add function call information if present
            if 'function_call' in message:
                result['function_call'] = message['function_call']

            # Add tool calls if present (for newer models)
            if 'tool_calls' in message:
                result['tool_calls'] = message['tool_calls']

            return result

        except requests.exceptions.RequestException as e:
            self.logger.error(f"API request failed: {str(e)}")
            raise RuntimeError(f"Failed to call LLM API: {str(e)}")
        except (KeyError, json.JSONDecodeError) as e:
            self.logger.error(f"Failed to parse API response: {str(e)}")
            raise RuntimeError(f"Invalid API response format: {str(e)}")

    def validate_input(self, input_data: Any) -> bool:
        """
        Validate that input is a list of message dictionaries.

        Args:
            input_data: Input to validate

        Returns:
            True if valid, False otherwise
        """
        if not isinstance(input_data, list):
            return False

        for item in input_data:
            if not isinstance(item, dict):
                return False
            if "role" not in item or "content" not in item:
                return False

        return True

    def validate_output(self, output_data: Any) -> bool:
        """
        Validate that output is a properly formatted response dictionary.

        Args:
            output_data: Output to validate

        Returns:
            True if valid, False otherwise
        """
        if not isinstance(output_data, dict):
            return False

        return "content" in output_data and "role" in output_data

    def get_last_response(self) -> Optional[Dict]:
        """
        Get the last LLM response.

        Returns:
            Last response dictionary or None
        """
        return self.last_response

    async def close_async(self) -> None:
        """Close the shared aiohttp session asynchronously."""
        if self._shared_session and not self._shared_session.closed:
            await self._shared_session.close()
            self._shared_session = None

    def reset(self) -> None:
        """Reset the node to initial state."""
        super().reset()
        self.last_response = None

    def __del__(self):
        """Cleanup on deletion - close session if exists."""
        if self._shared_session and not self._shared_session.closed:
            # Can't use await in __del__, so just close synchronously
            try:
                import asyncio
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self.close_async())
                else:
                    loop.run_until_complete(self.close_async())
            except:
                pass  # Best effort cleanup


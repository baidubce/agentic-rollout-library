"""
Test cases for LLM Node.

Tests LLM node functionality including async execution, retry logic, and timeout.
"""

import pytest
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.llm_node import LLMNode


class TestLLMNode:
    """Test LLM Node functionality."""

    def test_init_with_defaults(self):
        """Test LLM node initialization with defaults."""
        node = LLMNode(name="TestLLM")

        assert node.name == "TestLLM"
        assert node.function_handle is None
        assert node.model_config == {}
        assert node.default_params["temperature"] == 0.7
        assert node.default_params["max_tokens"] == 2000

    def test_set_function_handle(self):
        """Test setting custom function handle."""
        node = LLMNode()

        def custom_llm(messages, **kwargs):
            return {"role": "assistant", "content": "Response"}

        node.set_function_handle(custom_llm)
        assert node.function_handle == custom_llm

    def test_set_model_config(self):
        """Test updating model configuration."""
        node = LLMNode()
        node.set_model_config({"temperature": 0.5, "model": "gpt-4"})

        assert node.model_config["temperature"] == 0.5
        assert node.model_config["model"] == "gpt-4"

    def test_set_parameter(self):
        """Test setting individual parameters."""
        node = LLMNode()
        node.set_parameter("temperature", 0.9)

        assert node.default_params["temperature"] == 0.9

    def test_set_retry_config(self):
        """Test configuring retry behavior."""
        node = LLMNode()
        node.set_retry_config(max_retries=5, initial_delay=2.0)

        assert node.retry_config["max_retries"] == 5
        assert node.retry_config["initial_delay"] == 2.0

    def test_validate_input_valid(self):
        """Test input validation with valid messages."""
        node = LLMNode()
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"}
        ]

        assert node.validate_input(messages) is True

    def test_validate_input_invalid(self):
        """Test input validation with invalid messages."""
        node = LLMNode()

        # Not a list
        assert node.validate_input("invalid") is False

        # Missing role
        assert node.validate_input([{"content": "test"}]) is False

        # Missing content
        assert node.validate_input([{"role": "user"}]) is False

    def test_validate_output_valid(self):
        """Test output validation with valid response."""
        node = LLMNode()
        response = {"role": "assistant", "content": "Response"}

        assert node.validate_output(response) is True

    def test_validate_output_invalid(self):
        """Test output validation with invalid response."""
        node = LLMNode()

        assert node.validate_output("invalid") is False
        assert node.validate_output({"role": "assistant"}) is False
        assert node.validate_output({"content": "test"}) is False

    def test_process_with_custom_handle(self):
        """Test processing with custom function handle."""
        def mock_llm(messages, **kwargs):
            return {
                "role": "assistant",
                "content": f"Received {len(messages)} messages"
            }

        node = LLMNode(function_handle=mock_llm)
        messages = [{"role": "user", "content": "Test"}]

        result = node.process(messages)

        assert result["role"] == "assistant"
        assert "1 messages" in result["content"]

    @pytest.mark.asyncio
    async def test_process_async_with_custom_handle(self):
        """Test async processing with custom function handle."""
        async def mock_llm_async(messages, **kwargs):
            await asyncio.sleep(0.01)  # Simulate async work
            return {
                "role": "assistant",
                "content": f"Async response to {len(messages)} messages"
            }

        node = LLMNode(function_handle=mock_llm_async)
        messages = [{"role": "user", "content": "Test"}]

        result = await node.process_async(messages)

        assert result["role"] == "assistant"
        assert "Async response" in result["content"]

    @pytest.mark.asyncio
    async def test_retry_logic_success(self):
        """Test retry logic with eventual success."""
        call_count = {"count": 0}

        async def flaky_llm(messages, **kwargs):
            call_count["count"] += 1
            if call_count["count"] < 3:
                raise Exception("Simulated failure")
            return {"role": "assistant", "content": "Success"}

        node = LLMNode(function_handle=flaky_llm)
        node.set_retry_config(max_retries=5, initial_delay=0.01)

        messages = [{"role": "user", "content": "Test"}]
        result = await node.process_async(messages)

        assert result["content"] == "Success"
        assert call_count["count"] == 3  # Failed twice, succeeded on third

    @pytest.mark.asyncio
    async def test_retry_logic_failure(self):
        """Test retry logic with all attempts failing."""
        async def failing_llm(messages, **kwargs):
            raise Exception("Always fails")

        node = LLMNode(function_handle=failing_llm)
        node.set_retry_config(max_retries=2, initial_delay=0.01)

        messages = [{"role": "user", "content": "Test"}]

        with pytest.raises(Exception, match="Always fails"):
            await node.process_async(messages)

    @pytest.mark.asyncio
    async def test_process_with_timeout(self):
        """Test async processing with timeout."""
        async def slow_llm(messages, **kwargs):
            await asyncio.sleep(2)
            return {"role": "assistant", "content": "Slow response"}

        node = LLMNode(
            function_handle=slow_llm,
            timeline_enabled=True,
            timeout=0.5  # 500ms timeout
        )

        messages = [{"role": "user", "content": "Test"}]

        with pytest.raises(asyncio.TimeoutError):
            await node.process_with_timing(messages, event_type="llm_call")

    @pytest.mark.asyncio
    async def test_timeline_tracking(self):
        """Test timeline tracking during execution."""
        from core.timeline import get_timeline

        async def mock_llm(messages, **kwargs):
            await asyncio.sleep(0.05)
            return {"role": "assistant", "content": "Response"}

        # Clear timeline
        timeline = get_timeline()
        timeline.clear()

        node = LLMNode(
            name="TestLLMWithTimeline",
            function_handle=mock_llm,
            timeline_enabled=True
        )

        messages = [{"role": "user", "content": "Test"}]
        await node.process_with_timing(messages, event_type="llm_call")

        # Check timeline recorded the event
        events = timeline.get_timeline()
        assert len(events) > 0
        assert any(e["node_name"] == "TestLLMWithTimeline" for e in events)

    def test_get_last_response(self):
        """Test getting last response."""
        def mock_llm(messages, **kwargs):
            return {"role": "assistant", "content": "Test response"}

        node = LLMNode(function_handle=mock_llm)
        messages = [{"role": "user", "content": "Hello"}]

        node.process(messages)
        last_response = node.get_last_response()

        assert last_response is not None
        assert last_response["content"] == "Test response"

    def test_reset(self):
        """Test resetting node state."""
        def mock_llm(messages, **kwargs):
            return {"role": "assistant", "content": "Response"}

        node = LLMNode(function_handle=mock_llm)
        messages = [{"role": "user", "content": "Test"}]

        node.process(messages)
        assert node.last_response is not None

        node.reset()
        assert node.last_response is None

    def test_process_converts_string_response(self):
        """Test that string responses are converted to dict."""
        def mock_llm(messages, **kwargs):
            return "Plain string response"

        node = LLMNode(function_handle=mock_llm)
        messages = [{"role": "user", "content": "Test"}]

        result = node.process(messages)

        assert isinstance(result, dict)
        assert result["role"] == "assistant"
        assert result["content"] == "Plain string response"

    def test_process_adds_role_if_missing(self):
        """Test that role is added if missing from response."""
        def mock_llm(messages, **kwargs):
            return {"content": "Response without role"}

        node = LLMNode(function_handle=mock_llm)
        messages = [{"role": "user", "content": "Test"}]

        result = node.process(messages)

        assert "role" in result
        assert result["role"] == "assistant"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

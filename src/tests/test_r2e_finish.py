"""
Test cases for R2E finish/submit tool.

Tests both local and K8S execution modes.
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.r2e.finish import finish_func


class TestR2EFinishLocal:
    """Test R2E finish tool in local execution mode."""

    def test_submit_without_result(self):
        """Test submit command without result."""
        result = finish_func(command="submit")

        assert result["status"] == "stop"
        assert "<<<Finished>>>" in result["output"]
        assert result.get("message") == "Task completed"

    def test_submit_with_result(self):
        """Test submit command with result text."""
        result = finish_func(
            command="submit",
            result="The answer is 42"
        )

        assert result["status"] == "stop"
        assert "<<<Finished>>>" in result["output"]
        assert "The answer is 42" in result["output"]
        assert result.get("message") == "Task completed"

    def test_submit_with_multiline_result(self):
        """Test submit with multiline result."""
        multiline_result = "Result:\nLine 1\nLine 2\nLine 3"
        result = finish_func(
            command="submit",
            result=multiline_result
        )

        assert result["status"] == "stop"
        assert "Line 1" in result["output"]
        assert "Line 2" in result["output"]
        assert "Line 3" in result["output"]

    def test_submit_with_empty_string_result(self):
        """Test submit with empty string result."""
        result = finish_func(
            command="submit",
            result=""
        )

        assert result["status"] == "stop"
        assert "<<<Finished>>>" in result["output"]

    def test_invalid_command(self):
        """Test invalid command returns error."""
        result = finish_func(command="invalid")

        assert result["status"] == "error"
        assert "Unknown command" in result["error"]
        assert "submit" in result["error"]

    def test_missing_command(self):
        """Test missing command parameter."""
        result = finish_func(result="some result")

        assert result["status"] == "error"
        assert "Missing required parameter" in result["error"]
        assert "command" in result["error"]

    def test_stop_status_triggers_termination(self):
        """Test that status='stop' signals task completion."""
        result = finish_func(command="submit", result="Done")

        # The stop status should signal the agent loop to terminate
        assert result["status"] == "stop"
        assert isinstance(result, dict)
        assert "output" in result

    def test_submit_with_json_result(self):
        """Test submit with JSON-like result."""
        json_result = '{"status": "success", "value": 123}'
        result = finish_func(
            command="submit",
            result=json_result
        )

        assert result["status"] == "stop"
        assert json_result in result["output"]

    def test_submit_with_special_chars(self):
        """Test submit with special characters in result."""
        special_result = "Result: @#$%^&*() <>?/\\|"
        result = finish_func(
            command="submit",
            result=special_result
        )

        assert result["status"] == "stop"
        assert special_result in result["output"]


class TestR2EFinishK8S:
    """Test R2E finish tool in K8S execution mode."""

    @pytest.mark.skipif(
        not __import__('importlib.util').util.find_spec('kodo'),
        reason="Kodo not installed"
    )
    def test_k8s_submit(self):
        """Test submit command in K8S pod."""
        from core.k8s_tool_execution_node import K8SToolExecutionNode

        with K8SToolExecutionNode(
            name="TestFinishK8S",
            namespace="default",
            image="python:3.11-slim",
            pod_name="test-finish-pod"
        ) as executor:
            # Register with result parser to extract stop status
            def finish_parse_result(result):
                if isinstance(result, dict) and result.get('stdout'):
                    # Check if output contains finish marker
                    if '<<<Finished>>>' in result['stdout']:
                        return {
                            "output": result['stdout'],
                            "status": "stop",
                            "message": "Task completed"
                        }
                return result

            executor.register_tool(
                "r2e_submit",
                "src/tools/r2e/finish.py",
                finish_parse_result
            )

            tool_call = {
                "tool": "r2e_submit",
                "parameters": {
                    "command": "submit",
                    "result": "K8S task completed successfully"
                }
            }

            results = executor.process([tool_call])
            result = results[0]

            # Check that the result contains stop status
            assert result.get("status") == "stop"
            assert "K8S task completed" in str(result)

    @pytest.mark.skipif(
        not __import__('importlib.util').util.find_spec('kodo'),
        reason="Kodo not installed"
    )
    def test_k8s_submit_triggers_termination(self):
        """Test that K8S submit properly signals termination."""
        from core.k8s_tool_execution_node import K8SToolExecutionNode

        with K8SToolExecutionNode(
            name="TestFinishTerminationK8S",
            namespace="default",
            image="python:3.11-slim",
            pod_name="test-finish-term-pod"
        ) as executor:
            # Register with result parser to extract stop status
            def finish_parse_result(result):
                if isinstance(result, dict) and result.get('stdout'):
                    # Check if output contains finish marker
                    if '<<<Finished>>>' in result['stdout']:
                        return {
                            "output": result['stdout'],
                            "status": "stop",
                            "message": "Task completed"
                        }
                return result

            executor.register_tool(
                "r2e_submit",
                "src/tools/r2e/finish.py",
                finish_parse_result
            )

            # Submit should set status to "stop"
            tool_call = {
                "tool": "r2e_submit",
                "parameters": {
                    "command": "submit",
                    "result": "Final answer: 42"
                }
            }

            results = executor.process([tool_call])
            result = results[0]

            # Verify termination signal
            assert isinstance(result, dict)
            assert result.get("status") == "stop"
            assert "<<<Finished>>>" in result.get("output", "") or "<<<Finished>>>" in str(result)


class TestR2EFinishIntegration:
    """Integration tests for finish tool in agent loop."""

    def test_finish_in_mock_agent_loop(self):
        """Test finish tool behavior in simulated agent loop."""
        # Simulate an agent loop
        max_iterations = 5
        iteration = 0
        stop_signal = False

        while iteration < max_iterations and not stop_signal:
            iteration += 1

            # Simulate tool execution
            if iteration == 3:
                # Agent decides to finish
                result = finish_func(
                    command="submit",
                    result="Task completed after 3 iterations"
                )

                if result.get("status") == "stop":
                    stop_signal = True
                    final_result = result

        # Verify loop terminated early due to stop signal
        assert iteration == 3
        assert stop_signal is True
        assert "Task completed after 3 iterations" in final_result["output"]

    def test_multiple_finish_attempts(self):
        """Test multiple finish calls (edge case)."""
        result1 = finish_func(command="submit", result="First attempt")
        result2 = finish_func(command="submit", result="Second attempt")

        # Both should return stop status
        assert result1["status"] == "stop"
        assert result2["status"] == "stop"

        # Results should be different
        assert "First attempt" in result1["output"]
        assert "Second attempt" in result2["output"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

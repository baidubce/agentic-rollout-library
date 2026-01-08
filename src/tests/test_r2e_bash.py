"""
Test cases for R2E bash tool.

Tests both local and K8S execution modes.
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.r2e.bash_func import bash_func


class TestR2EBashLocal:
    """Test R2E bash tool in local execution mode."""

    def test_simple_command(self):
        """Test simple bash command execution."""
        result = bash_func("echo 'Hello World'")

        assert result["success"] is True
        assert result["returncode"] == 0
        assert "Hello World" in result["stdout"]
        assert result["stderr"] == ""

    def test_pwd_command(self):
        """Test pwd command."""
        result = bash_func("pwd")

        assert result["success"] is True
        assert result["returncode"] == 0
        assert "/" in result["stdout"]

    def test_ls_command(self):
        """Test ls command."""
        result = bash_func("ls -la")

        assert result["success"] is True
        assert result["returncode"] == 0
        assert len(result["stdout"]) > 0

    def test_failed_command(self):
        """Test command that fails."""
        result = bash_func("ls /nonexistent_directory_12345")

        assert result["success"] is False
        assert result["returncode"] != 0
        assert len(result["stderr"]) > 0

    def test_command_with_pipe(self):
        """Test command with pipe."""
        result = bash_func("echo 'test' | wc -l")

        assert result["success"] is True
        assert result["returncode"] == 0
        assert "1" in result["stdout"]

    def test_multiline_output(self):
        """Test command with multiline output."""
        result = bash_func("echo -e 'line1\\nline2\\nline3'")

        assert result["success"] is True
        assert "line1" in result["stdout"]
        assert "line2" in result["stdout"]
        assert "line3" in result["stdout"]

    def test_blocked_command(self):
        """Test that dangerous commands are blocked."""
        result = bash_func("rm -rf /")

        assert result["success"] is False
        assert "blocked" in result["stderr"].lower()

    def test_environment_variables(self):
        """Test command with environment variables."""
        result = bash_func("export TEST_VAR=hello && echo $TEST_VAR")

        assert result["success"] is True
        assert "hello" in result["stdout"]

    def test_command_with_special_chars(self):
        """Test command with special characters."""
        result = bash_func("echo 'test@#$%^&*()'")

        assert result["success"] is True
        assert "test@#$%^&*()" in result["stdout"]


class TestR2EBashK8S:
    """Test R2E bash tool in K8S execution mode."""

    @pytest.mark.skipif(
        not __import__('importlib.util').util.find_spec('kodo'),
        reason="Kodo not installed"
    )
    def test_k8s_simple_command(self):
        """Test simple bash command in K8S pod."""
        from core.k8s_tool_execution_node import K8SToolExecutionNode

        with K8SToolExecutionNode(
            name="TestBashK8S",
            namespace="default",
            image="python:3.11-slim",
            pod_name="test-bash-pod"
        ) as executor:
            executor.register_tool(
                "r2e_bash_executor",
                "src/tools/r2e/bash_func.py"
            )

            tool_call = {
                "tool": "r2e_bash_executor",
                "parameters": {"command": "echo 'K8S Test'"}
            }

            results = executor.process([tool_call])
            result = results[0]

            assert "K8S Test" in str(result)

    @pytest.mark.skipif(
        not __import__('importlib.util').util.find_spec('kodo'),
        reason="Kodo not installed"
    )
    def test_k8s_file_operations(self):
        """Test file operations in K8S pod."""
        from core.k8s_tool_execution_node import K8SToolExecutionNode

        with K8SToolExecutionNode(
            name="TestBashFileK8S",
            namespace="default",
            image="python:3.11-slim",
            pod_name="test-bash-file-pod"
        ) as executor:
            executor.register_tool(
                "r2e_bash_executor",
                "src/tools/r2e/bash_func.py"
            )

            # Create a file
            tool_call = {
                "tool": "r2e_bash_executor",
                "parameters": {"command": "echo 'test content' > /tmp/test.txt"}
            }
            executor.process([tool_call])

            # Read the file
            tool_call = {
                "tool": "r2e_bash_executor",
                "parameters": {"command": "cat /tmp/test.txt"}
            }
            results = executor.process([tool_call])

            assert "test content" in str(results[0])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

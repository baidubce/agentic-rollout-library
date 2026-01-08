#!/usr/bin/env python3
"""
Test suite for bash_func.py tool.
"""

import sys
import os
from pathlib import Path

# Add parent directories to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from tools.r2e.bash_func import bash_func, parse_result, build_k8s_command, BLOCKED_BASH_COMMANDS
from tools.tests.r2e.test_base import R2EToolTestBase


class TestBashFunc(R2EToolTestBase):
    """Test cases for bash_func tool."""

    def test_local_simple_command(self):
        """Test simple bash command execution."""
        result = bash_func("echo 'Hello World'")

        assert result["success"] is True, f"Command should succeed"
        assert "Hello World" in result["stdout"], "Output should contain expected text"
        assert result["returncode"] == 0, "Return code should be 0"
        print("✅ test_local_simple_command: PASSED")

    def test_local_pwd_command(self):
        """Test pwd command."""
        result = bash_func("pwd")

        assert result["success"] is True, f"Command should succeed"
        assert result["stdout"].strip(), "Output should not be empty"
        assert result["returncode"] == 0, "Return code should be 0"
        print("✅ test_local_pwd_command: PASSED")

    def test_local_ls_command(self):
        """Test ls command."""
        result = bash_func("ls -la")

        assert result["success"] is True, f"Command should succeed"
        assert result["stdout"], "Output should not be empty"
        print("✅ test_local_ls_command: PASSED")

    def test_local_command_with_pipe(self):
        """Test command with pipe."""
        result = bash_func("echo 'test' | wc -l")

        assert result["success"] is True, f"Command should succeed"
        assert "1" in result["stdout"], "Output should contain 1"
        print("✅ test_local_command_with_pipe: PASSED")

    def test_local_command_with_variables(self):
        """Test command with environment variables."""
        result = bash_func("echo $PATH")

        assert result["success"] is True, f"Command should succeed"
        assert result["stdout"].strip(), "PATH should not be empty"
        print("✅ test_local_command_with_variables: PASSED")

    def test_local_multiline_command(self):
        """Test multiline command."""
        command = """
        echo "Line 1"
        echo "Line 2"
        echo "Line 3"
        """
        result = bash_func(command)

        assert result["success"] is True, f"Command should succeed"
        assert "Line 1" in result["stdout"], "Output should contain Line 1"
        assert "Line 2" in result["stdout"], "Output should contain Line 2"
        assert "Line 3" in result["stdout"], "Output should contain Line 3"
        print("✅ test_local_multiline_command: PASSED")

    def test_local_command_with_special_chars(self):
        """Test command with special characters."""
        result = bash_func("echo \"It's a test with 'quotes' & special chars\"")

        assert result["success"] is True, f"Command should succeed"
        assert "It's a test" in result["stdout"], "Output should contain expected text"
        print("✅ test_local_command_with_special_chars: PASSED")

    def test_local_failed_command(self):
        """Test command that should fail."""
        result = bash_func("ls /nonexistent_directory_12345")

        assert result["success"] is False, "Command should fail"
        assert result["returncode"] != 0, "Return code should not be 0"
        assert result["stderr"], "stderr should contain error message"
        print("✅ test_local_failed_command: PASSED")

    def test_local_command_with_redirect(self):
        """Test command with output redirection."""
        temp_file = "/tmp/test_bash_output.txt"
        result = bash_func(f"echo 'test output' > {temp_file} && cat {temp_file}")

        assert result["success"] is True, f"Command should succeed"
        assert "test output" in result["stdout"], "Output should contain expected text"

        # Cleanup
        bash_func(f"rm -f {temp_file}")
        print("✅ test_local_command_with_redirect: PASSED")

    def test_local_blocked_command(self):
        """Test that blocked commands are rejected."""
        for blocked in BLOCKED_BASH_COMMANDS:
            result = bash_func(blocked)

            assert result["success"] is False, f"Blocked command should fail: {blocked}"
            assert "blocked" in result["stderr"].lower(), "Error should mention blocked command"

        print("✅ test_local_blocked_command: PASSED")

    def test_local_command_with_arithmetic(self):
        """Test command with arithmetic."""
        result = bash_func("echo $((2 + 3))")

        assert result["success"] is True, f"Command should succeed"
        assert "5" in result["stdout"], "Output should contain 5"
        print("✅ test_local_command_with_arithmetic: PASSED")

    def test_local_command_with_subshell(self):
        """Test command with subshell."""
        result = bash_func("echo $(date +%Y)")

        assert result["success"] is True, f"Command should succeed"
        assert result["stdout"].strip(), "Output should not be empty"
        print("✅ test_local_command_with_subshell: PASSED")

    def test_parse_result_success(self):
        """Test parse_result function with success case."""
        raw_result = {
            "stdout": "output text",
            "stderr": "",
            "returncode": 0,
            "success": True
        }

        parsed = parse_result(raw_result)

        assert parsed["status"] == "success", "Status should be success"
        assert parsed["output"] == "output text", "Output should match"
        assert parsed["return_code"] == 0, "Return code should be 0"
        print("✅ test_parse_result_success: PASSED")

    def test_parse_result_error(self):
        """Test parse_result function with error case."""
        raw_result = {
            "stdout": "",
            "stderr": "error message",
            "returncode": 1,
            "success": False
        }

        parsed = parse_result(raw_result)

        assert parsed["status"] == "error", "Status should be error"
        assert parsed["error"] == "error message", "Error message should match"
        print("✅ test_parse_result_error: PASSED")

    def test_build_k8s_command(self):
        """Test K8S command building."""
        command = "ls -la"
        k8s_cmd = build_k8s_command(command)

        assert k8s_cmd == command, "K8S command should be the same as input"
        print("✅ test_build_k8s_command: PASSED")

    def test_local_command_with_glob(self):
        """Test command with glob pattern."""
        result = bash_func("ls *.py 2>/dev/null | head -3")

        # May or may not find files, but should not error
        assert result["returncode"] in [0, 1], "Command should execute"
        print("✅ test_local_command_with_glob: PASSED")


def run_all_tests():
    """Run all bash_func tool tests."""
    print("=" * 80)
    print("BASH_FUNC TOOL TEST SUITE")
    print("=" * 80)

    test = TestBashFunc()

    # Local function tests
    print("\n[Local Function Tests]")
    test.test_local_simple_command()
    test.test_local_pwd_command()
    test.test_local_ls_command()
    test.test_local_command_with_pipe()
    test.test_local_command_with_variables()
    test.test_local_multiline_command()
    test.test_local_command_with_special_chars()
    test.test_local_failed_command()
    test.test_local_command_with_redirect()
    test.test_local_blocked_command()
    test.test_local_command_with_arithmetic()
    test.test_local_command_with_subshell()
    test.test_local_command_with_glob()

    # Utility function tests
    print("\n[Utility Function Tests]")
    test.test_parse_result_success()
    test.test_parse_result_error()
    test.test_build_k8s_command()

    print("\n" + "=" * 80)
    print("✅ ALL BASH_FUNC TOOL TESTS PASSED")
    print("=" * 80)


if __name__ == "__main__":
    try:
        run_all_tests()
        sys.exit(0)
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ UNEXPECTED ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

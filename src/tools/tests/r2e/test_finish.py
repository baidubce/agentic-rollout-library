#!/usr/bin/env python3
"""
Test suite for finish.py tool (R2E version).
"""

import sys
from pathlib import Path

# Add parent directories to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from tools.r2e.finish import finish_func
from tools.tests.r2e.test_base import R2EToolTestBase


class TestFinish(R2EToolTestBase):
    """Test cases for finish tool."""

    def test_local_submit_command(self):
        """Test finish with submit command."""
        result = finish_func(command="submit")

        assert result["status"] == "stop", f"Expected 'stop' status, got {result['status']}"
        assert "<<<Finished>>>" in result["output"], "Output should contain finish marker"
        print("✅ test_local_submit_command: PASSED")

    def test_local_submit_with_result(self):
        """Test finish with submit command and result text."""
        result_text = "Task completed successfully. All tests passed."
        result = finish_func(command="submit", result=result_text)

        assert result["status"] == "stop", f"Expected 'stop' status, got {result['status']}"
        assert "<<<Finished>>>" in result["output"], "Output should contain finish marker"
        assert result_text in result["output"], "Output should contain result text"
        print("✅ test_local_submit_with_result: PASSED")

    def test_local_submit_with_multiline_result(self):
        """Test finish with multiline result."""
        result_text = """Task Summary:
- Fixed 3 bugs
- Added 2 new features
- All tests passing"""

        result = finish_func(command="submit", result=result_text)

        assert result["status"] == "stop", f"Expected 'stop' status, got {result['status']}"
        assert "<<<Finished>>>" in result["output"], "Output should contain finish marker"
        assert "Fixed 3 bugs" in result["output"], "Output should contain result details"
        print("✅ test_local_submit_with_multiline_result: PASSED")

    def test_local_missing_command(self):
        """Test finish without command parameter (should fail)."""
        result = finish_func()

        assert result["status"] == "error", f"Expected error for missing command, got {result['status']}"
        assert "Missing" in result.get("error", ""), "Error should mention missing parameter"
        print("✅ test_local_missing_command: PASSED")

    def test_local_invalid_command(self):
        """Test finish with invalid command (should fail)."""
        result = finish_func(command="invalid")

        assert result["status"] == "error", f"Expected error for invalid command, got {result['status']}"
        assert "Unknown command" in result.get("error", ""), "Error should mention unknown command"
        print("✅ test_local_invalid_command: PASSED")

    def test_local_submit_with_special_characters(self):
        """Test finish with special characters in result."""
        special_results = [
            "Fixed O'Brien's bug with \"quotes\"",
            "Path updated: C:\\Users\\test\\file.txt",
            "Command executed: echo $VAR && ls -la",
            "SQL query: SELECT * FROM users WHERE id = 1; -- done",
        ]

        for result_text in special_results:
            result = finish_func(command="submit", result=result_text)
            assert result["status"] == "stop", f"Failed for result: {result_text}"
            assert result_text in result["output"], f"Output should contain: {result_text}"

        print("✅ test_local_submit_with_special_characters: PASSED")

    def test_local_submit_with_unicode(self):
        """Test finish with Unicode characters."""
        result_text = "任务完成 ✓ Task completed 🎉"
        result = finish_func(command="submit", result=result_text)

        assert result["status"] == "stop", f"Expected 'stop' status, got {result['status']}"
        assert "任务完成" in result["output"], "Output should contain Unicode text"
        print("✅ test_local_submit_with_unicode: PASSED")

    def test_local_empty_result(self):
        """Test finish with empty result (should still work)."""
        result = finish_func(command="submit", result="")

        assert result["status"] == "stop", f"Expected 'stop' status, got {result['status']}"
        assert "<<<Finished>>>" in result["output"], "Output should contain finish marker"
        print("✅ test_local_empty_result: PASSED")


def run_all_tests():
    """Run all finish tool tests."""
    print("=" * 80)
    print("FINISH TOOL TEST SUITE (R2E)")
    print("=" * 80)

    test = TestFinish()

    # Local function tests
    print("\n[Local Function Tests]")
    test.test_local_submit_command()
    test.test_local_submit_with_result()
    test.test_local_submit_with_multiline_result()
    test.test_local_missing_command()
    test.test_local_invalid_command()
    test.test_local_submit_with_special_characters()
    test.test_local_submit_with_unicode()
    test.test_local_empty_result()

    print("\n" + "=" * 80)
    print("✅ ALL FINISH TOOL TESTS PASSED")
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

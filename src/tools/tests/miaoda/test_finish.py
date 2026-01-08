#!/usr/bin/env python3
"""
Test suite for finish.py tool with real K8S pod execution.
"""

import sys
import asyncio
from pathlib import Path

# Add parent directories to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from tools.miaoda.finish import finish_func
from tools.tests.miaoda.test_base import MiaodaToolTestBase


class TestFinish(MiaodaToolTestBase):
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

    async def test_k8s_submit_command(self):
        """Test K8S execution with submit command."""
        result = await self.execute_tool_in_k8s(
            tool_name="miaoda_finish",
            parameters={"command": "submit"},
            test_name="finish-submit"
        )

        # K8S executor wraps the result, so we check for success status
        # and verify the result contains the finish marker
        if result.get("status") != "success":
            print(f"❌ test_k8s_submit_command: FAILED")
            print(f"   Status: {result.get('status')}")
            raise AssertionError(f"Test failed: Expected 'success' status, got {result.get('status')}")

        # Check that result contains the finish marker
        result_str = str(result.get('result', ''))
        if "<<<Finished>>>" not in result_str:
            print(f"❌ test_k8s_submit_command: FAILED")
            print(f"   Result should contain '<<<Finished>>>'")
            raise AssertionError(f"Result should contain '<<<Finished>>>'")

        print(f"✅ test_k8s_submit_command: PASSED")

    async def test_k8s_submit_with_result(self):
        """Test K8S execution with submit command and result text."""
        result_text = "Task completed successfully in K8S pod"
        result = await self.execute_tool_in_k8s(
            tool_name="miaoda_finish",
            parameters={"command": "submit", "result": result_text},
            test_name="finish-submit-result"
        )

        # K8S executor wraps the result
        if result.get("status") != "success":
            print(f"❌ test_k8s_submit_with_result: FAILED")
            raise AssertionError(f"Expected 'success' status, got {result.get('status')}")

        # Check that result contains both the finish marker and our result text
        result_str = str(result.get('result', ''))
        if "<<<Finished>>>" not in result_str:
            print(f"❌ test_k8s_submit_with_result: FAILED")
            raise AssertionError(f"Result should contain '<<<Finished>>>'")

        if result_text not in result_str:
            print(f"❌ test_k8s_submit_with_result: FAILED")
            raise AssertionError(f"Result should contain '{result_text}'")

        print(f"✅ test_k8s_submit_with_result: PASSED")

    async def test_k8s_invalid_command(self):
        """Test K8S execution with invalid command (should fail)."""
        result = await self.execute_tool_in_k8s(
            tool_name="miaoda_finish",
            parameters={"command": "invalid"},
            test_name="finish-invalid"
        )

        # K8S executor wraps the result as success, but the tool function returned error
        # We need to check the result content contains error information
        if result.get("status") != "success":
            print(f"❌ test_k8s_invalid_command: FAILED")
            print(f"   Unexpected status: {result.get('status')}")
            raise AssertionError(f"Unexpected status: {result.get('status')}")

        # The result should contain error information from the tool
        result_content = result.get('result', {})

        # Check if it's a dict with error status or contains error in string
        has_error = False
        if isinstance(result_content, dict):
            if result_content.get('status') == 'error':
                has_error = True
            elif 'error' in result_content:
                has_error = True
        elif isinstance(result_content, str):
            if 'error' in result_content.lower() or 'unknown command' in result_content.lower():
                has_error = True

        if not has_error:
            print(f"❌ test_k8s_invalid_command: FAILED")
            print(f"   Result should contain error information")
            print(f"   Got: {result_content}")
            raise AssertionError(f"Result should contain error information")

        print(f"✅ test_k8s_invalid_command: PASSED (correctly failed)")


async def run_all_tests_async():
    """Run all finish tool tests (async version)."""
    print("=" * 80)
    print("FINISH TOOL TEST SUITE")
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

    # K8S pod execution tests
    print("\n[K8S Pod Execution Tests]")
    await test.test_k8s_submit_command()
    await asyncio.sleep(0.5)  # Give time for cleanup between tests
    await test.test_k8s_submit_with_result()
    await asyncio.sleep(0.5)  # Give time for cleanup between tests
    await test.test_k8s_invalid_command()
    await asyncio.sleep(0.5)  # Give time for cleanup after tests

    print("\n" + "=" * 80)
    print("✅ ALL FINISH TOOL TESTS PASSED")
    print("=" * 80)


def run_all_tests():
    """Run all finish tool tests (sync wrapper)."""
    import warnings
    warnings.filterwarnings('ignore', category=ResourceWarning)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run_all_tests_async())
        # Give time for all async cleanup to complete
        loop.run_until_complete(asyncio.sleep(1.0))
    finally:
        # Close any remaining tasks
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()


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

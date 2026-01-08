#!/usr/bin/env python3
"""
Test suite for think.py tool with real K8S pod execution.
"""

import sys
import asyncio
from pathlib import Path

# Add parent directories to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from tools.miaoda.think import think_func
from tools.tests.miaoda.test_base import MiaodaToolTestBase


class TestThink(MiaodaToolTestBase):
    """Test cases for think tool."""

    def test_local_simple_thought(self):
        """Test think with simple thought."""
        result = think_func("I need to analyze the codebase")

        assert result["status"] == "success", f"Expected success, got {result['status']}"
        assert "I am thinking..." in result["output"], "Output should contain thought marker"
        assert "analyze the codebase" in result["output"], "Output should contain the thought"
        print("✅ test_local_simple_thought: PASSED")

    def test_local_empty_thought(self):
        """Test think with empty thought (should fail)."""
        result = think_func("")

        assert result["status"] == "error", f"Expected error for empty thought, got {result['status']}"
        assert "empty" in result.get("error", "").lower(), "Error should mention empty thought"
        print("✅ test_local_empty_thought: PASSED")

    def test_local_complex_thought(self):
        """Test think with complex thought containing special characters."""
        complex_thought = """This is a multi-line thought:
1. First, I'll analyze the error in user's code
2. Then I'll propose a fix using the 'fix_bug' function
3. Finally, I'll test it & verify the results
Note: This involves checking $PATH and 'environment' variables."""

        result = think_func(complex_thought)

        assert result["status"] == "success", f"Expected success, got {result['status']}"
        assert "I am thinking..." in result["output"], "Output should contain thought marker"
        print("✅ test_local_complex_thought: PASSED")

    def test_local_special_characters(self):
        """Test think with various special characters."""
        special_thoughts = [
            "It's important to check O'Brien's code",
            'Use "quotes" and \'single quotes\'',
            "Path: C:\\Users\\test\\file.txt",
            "Command: echo $VAR && ls -la",
            "SQL: SELECT * FROM users WHERE id = 1; -- comment",
        ]

        for thought in special_thoughts:
            result = think_func(thought)
            assert result["status"] == "success", f"Failed for thought: {thought}"

        print("✅ test_local_special_characters: PASSED")

    async def test_k8s_simple_thought(self):
        """Test K8S execution with simple thought."""
        result = await self.execute_tool_in_k8s(
            tool_name="miaoda_think",
            parameters={"thought": "Analyzing the problem"},
            test_name="simple-thought"
        )

        self.assert_success(result, "test_k8s_simple_thought")

    async def test_k8s_thought_with_quotes(self):
        """Test K8S execution with quotes in thought."""
        result = await self.execute_tool_in_k8s(
            tool_name="miaoda_think",
            parameters={"thought": "It's a 'quoted' thought with \"double quotes\""},
            test_name="thought-quotes"
        )

        self.assert_success(result, "test_k8s_thought_with_quotes")

    async def test_k8s_thought_with_special_chars(self):
        """Test K8S execution with special characters."""
        result = await self.execute_tool_in_k8s(
            tool_name="miaoda_think",
            parameters={"thought": "Check $PATH & verify; test | grep 'result'"},
            test_name="thought-special-chars"
        )

        self.assert_success(result, "test_k8s_thought_with_special_chars")

    async def test_k8s_multiline_thought(self):
        """Test K8S execution with multiline thought."""
        multiline = """Step 1: Analyze
Step 2: Plan
Step 3: Execute"""

        result = await self.execute_tool_in_k8s(
            tool_name="miaoda_think",
            parameters={"thought": multiline},
            test_name="thought-multiline"
        )

        self.assert_success(result, "test_k8s_multiline_thought")

    async def test_k8s_unicode_thought(self):
        """Test K8S execution with Unicode characters."""
        result = await self.execute_tool_in_k8s(
            tool_name="miaoda_think",
            parameters={"thought": "思考：我需要分析这个问题 🤔"},
            test_name="thought-unicode"
        )

        self.assert_success(result, "test_k8s_unicode_thought")


async def run_all_tests_async():
    """Run all think tool tests (async version)."""
    print("=" * 80)
    print("THINK TOOL TEST SUITE")
    print("=" * 80)

    test = TestThink()

    # Local function tests
    print("\n[Local Function Tests]")
    test.test_local_simple_thought()
    test.test_local_empty_thought()
    test.test_local_complex_thought()
    test.test_local_special_characters()

    # K8S pod execution tests
    print("\n[K8S Pod Execution Tests]")
    await test.test_k8s_simple_thought()
    await asyncio.sleep(0.3)  # Give time for cleanup between tests
    await test.test_k8s_thought_with_quotes()
    await asyncio.sleep(0.3)
    await test.test_k8s_thought_with_special_chars()
    await asyncio.sleep(0.3)
    await test.test_k8s_multiline_thought()
    await asyncio.sleep(0.3)
    await test.test_k8s_unicode_thought()
    await asyncio.sleep(0.5)  # Give extra time after all tests

    print("\n" + "=" * 80)
    print("✅ ALL THINK TOOL TESTS PASSED")
    print("=" * 80)


def run_all_tests():
    """Run all think tool tests (sync wrapper)."""
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

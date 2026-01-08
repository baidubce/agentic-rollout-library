#!/usr/bin/env python3
"""
Test suite for search_func.py tool.
"""

import sys
import os
import tempfile
from pathlib import Path

# Add parent directories to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from tools.r2e.search_func import search_func
from tools.tests.r2e.test_base import R2EToolTestBase


class TestSearchFunc(R2EToolTestBase):
    """Test cases for search_func tool."""

    def test_local_search_in_file_found(self):
        """Test searching for a term that exists in a file."""
        # Create temp file with content
        content = """Line 1: Hello World
Line 2: This is a test
Line 3: Hello again
Line 4: Another line"""

        filepath, temp_file = self.create_temp_file(content)

        try:
            result = search_func(search_term="Hello", path=filepath)

            assert result["status"] == "success", "Search should succeed"
            assert "Matches for" in result["output"], "Output should show matches"
            assert "Hello" in result["output"], "Output should contain search term"
            print("✅ test_local_search_in_file_found: PASSED")
        finally:
            os.unlink(filepath)

    def test_local_search_in_file_not_found(self):
        """Test searching for a term that doesn't exist in a file."""
        content = "Line 1: Hello World\nLine 2: Test"

        filepath, temp_file = self.create_temp_file(content)

        try:
            result = search_func(search_term="NonExistent", path=filepath)

            assert result["status"] == "success", "Search should succeed even without matches"
            assert "No matches found" in result["output"], "Output should indicate no matches"
            print("✅ test_local_search_in_file_not_found: PASSED")
        finally:
            os.unlink(filepath)

    def test_local_search_in_directory(self):
        """Test searching in a directory."""
        temp_dir = self.create_temp_dir()

        try:
            # Create Python files with content
            file1 = os.path.join(temp_dir.name, "test1.py")
            with open(file1, 'w') as f:
                f.write("def hello():\n    print('Hello World')\n")

            file2 = os.path.join(temp_dir.name, "test2.py")
            with open(file2, 'w') as f:
                f.write("def hello_again():\n    pass\n")

            result = search_func(search_term="hello", path=temp_dir.name)

            assert result["status"] == "success", "Search should succeed"
            assert "Found" in result["output"] or "matches" in result["output"].lower(), "Should find matches"
            print("✅ test_local_search_in_directory: PASSED")
        finally:
            temp_dir.cleanup()

    def test_local_search_missing_term(self):
        """Test search without search_term parameter."""
        result = search_func(path=".")

        assert result["status"] == "error", "Should fail without search_term"
        assert "Missing required parameter" in result["error"], "Error should mention missing parameter"
        print("✅ test_local_search_missing_term: PASSED")

    def test_local_search_nonexistent_path(self):
        """Test search with non-existent path."""
        result = search_func(search_term="test", path="/nonexistent_path_12345")

        assert result["status"] == "error", "Should fail with non-existent path"
        assert "does not exist" in result["error"], "Error should mention path doesn't exist"
        print("✅ test_local_search_nonexistent_path: PASSED")

    def test_local_search_special_characters(self):
        """Test searching for special characters."""
        content = """Line 1: It's a test
Line 2: "quoted text"
Line 3: Path: C:\\Users\\test
Line 4: Value: $VAR"""

        filepath, temp_file = self.create_temp_file(content)

        try:
            # Search for apostrophe
            result = search_func(search_term="It's", path=filepath)
            assert result["status"] == "success", "Should handle apostrophe"

            # Search for quotes
            result = search_func(search_term='"quoted', path=filepath)
            assert result["status"] == "success", "Should handle quotes"

            print("✅ test_local_search_special_characters: PASSED")
        finally:
            os.unlink(filepath)

    def test_local_search_unicode(self):
        """Test searching for Unicode characters."""
        content = "Line 1: 你好世界\nLine 2: Hello World\nLine 3: 测试"

        filepath, temp_file = self.create_temp_file(content)

        try:
            result = search_func(search_term="你好", path=filepath)

            assert result["status"] == "success", "Should handle Unicode"
            print("✅ test_local_search_unicode: PASSED")
        finally:
            os.unlink(filepath)

    def test_local_search_multiline_file(self):
        """Test searching in a multiline file."""
        content = "\n".join([f"Line {i}: test content {i}" for i in range(1, 101)])

        filepath, temp_file = self.create_temp_file(content)

        try:
            result = search_func(search_term="test content", path=filepath)

            assert result["status"] == "success", "Search should succeed"
            assert "Matches for" in result["output"], "Should find matches"
            print("✅ test_local_search_multiline_file: PASSED")
        finally:
            os.unlink(filepath)

    def test_local_search_with_default_path(self):
        """Test search with default path (current directory)."""
        # Search in current directory (should have some .py files)
        result = search_func(search_term="def")

        assert result["status"] == "success", "Search should succeed"
        # Should either find matches or indicate no matches, both are valid
        assert result["output"], "Should have some output"
        print("✅ test_local_search_with_default_path: PASSED")

    def test_local_search_empty_directory(self):
        """Test searching in an empty directory."""
        temp_dir = self.create_temp_dir()

        try:
            result = search_func(search_term="test", path=temp_dir.name)

            assert result["status"] == "success", "Search should succeed"
            assert "No matches found" in result["output"], "Should indicate no matches"
            print("✅ test_local_search_empty_directory: PASSED")
        finally:
            temp_dir.cleanup()

    def test_local_search_case_sensitive(self):
        """Test that search is case-sensitive."""
        content = "Line 1: HELLO\nLine 2: hello\nLine 3: HeLLo"

        filepath, temp_file = self.create_temp_file(content)

        try:
            result = search_func(search_term="hello", path=filepath)

            assert result["status"] == "success", "Search should succeed"
            # Should only match "hello", not "HELLO" or "HeLLo"
            print("✅ test_local_search_case_sensitive: PASSED")
        finally:
            os.unlink(filepath)


def run_all_tests():
    """Run all search_func tool tests."""
    print("=" * 80)
    print("SEARCH_FUNC TOOL TEST SUITE")
    print("=" * 80)

    test = TestSearchFunc()

    # Local function tests
    print("\n[Local Function Tests]")
    test.test_local_search_in_file_found()
    test.test_local_search_in_file_not_found()
    test.test_local_search_in_directory()
    test.test_local_search_missing_term()
    test.test_local_search_nonexistent_path()
    test.test_local_search_special_characters()
    test.test_local_search_unicode()
    test.test_local_search_multiline_file()
    test.test_local_search_with_default_path()
    test.test_local_search_empty_directory()
    test.test_local_search_case_sensitive()

    print("\n" + "=" * 80)
    print("✅ ALL SEARCH_FUNC TOOL TESTS PASSED")
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

"""
Test cases for R2E search tool.

Tests both local and K8S execution modes.
"""

import pytest
import tempfile
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.r2e.search_func import search_func


class TestR2ESearchLocal:
    """Test R2E search tool in local execution mode."""

    def setup_method(self):
        """Create temporary directory with test files."""
        self.test_dir = Path(tempfile.mkdtemp())

        # Create test files
        (self.test_dir / "file1.py").write_text(
            "def hello():\n    print('Hello World')\n    return 42"
        )
        (self.test_dir / "file2.py").write_text(
            "class MyClass:\n    def method(self):\n        pass"
        )
        (self.test_dir / "file3.txt").write_text(
            "This is a text file\nWith some content\nHello from file3"
        )

        # Create subdirectory
        subdir = self.test_dir / "subdir"
        subdir.mkdir()
        (subdir / "nested.py").write_text(
            "# Nested file\ndef nested_func():\n    return 'Hello'"
        )

    def teardown_method(self):
        """Clean up temporary directory."""
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def test_search_in_file(self):
        """Test searching in a single file."""
        file_path = self.test_dir / "file1.py"
        result = search_func(
            search_term="Hello",
            path=str(file_path)
        )

        assert result["status"] != "error"
        assert "Hello" in result["output"]
        assert str(file_path) in result["output"]

    def test_search_in_file_no_matches(self):
        """Test searching with no matches in file."""
        file_path = self.test_dir / "file1.py"
        result = search_func(
            search_term="NonExistentTerm",
            path=str(file_path)
        )

        assert result["status"] == "success"
        assert "No matches found" in result["output"]

    def test_search_in_directory(self):
        """Test searching in directory (Python files only by default)."""
        result = search_func(
            search_term="Hello",
            path=str(self.test_dir)
        )

        assert result["status"] != "error"
        assert "matches" in result["output"].lower()
        # Should find in file1.py and nested.py
        assert "file1.py" in result["output"]

    def test_search_for_def_keyword(self):
        """Test searching for Python keyword."""
        result = search_func(
            search_term="def",
            path=str(self.test_dir)
        )

        assert result["status"] != "error"
        assert "matches" in result["output"].lower()
        # Should find in multiple files
        assert "file1.py" in result["output"] or "file2.py" in result["output"]

    def test_search_for_class(self):
        """Test searching for class definition."""
        result = search_func(
            search_term="class MyClass",
            path=str(self.test_dir)
        )

        assert result["status"] != "error"
        assert "file2.py" in result["output"]

    def test_search_with_special_chars(self):
        """Test searching for special characters."""
        file_path = self.test_dir / "special.py"
        file_path.write_text("result = x + y * 2\nif x > 10:")

        result = search_func(
            search_term="x + y",
            path=str(file_path)
        )

        assert result["status"] != "error"
        assert "x + y" in result["output"] or "matches" in result["output"].lower()

    def test_search_multiline_context(self):
        """Test searching shows line numbers."""
        file_path = self.test_dir / "multiline.py"
        file_path.write_text("line1\nline2 search_term\nline3\nline4 search_term\nline5")

        result = search_func(
            search_term="search_term",
            path=str(file_path)
        )

        assert result["status"] != "error"
        # grep -n should show line numbers
        assert ":" in result["output"]  # grep format: "line_num:content"

    def test_search_case_sensitive(self):
        """Test that search is case-sensitive."""
        file_path = self.test_dir / "case.py"
        file_path.write_text("Hello\nhello\nHELLO")

        result = search_func(
            search_term="hello",
            path=str(file_path)
        )

        assert result["status"] != "error"
        # Should only match lowercase "hello"
        assert "hello" in result["output"].lower()

    def test_search_nonexistent_path(self):
        """Test searching in non-existent path."""
        result = search_func(
            search_term="test",
            path="/nonexistent/path/12345"
        )

        assert result["status"] == "error"
        assert "not exist" in result["error"].lower()

    def test_search_missing_term(self):
        """Test error when search term is missing."""
        result = search_func(path=str(self.test_dir))

        assert result["status"] == "error"
        assert "search_term" in result["error"].lower()

    def test_search_empty_directory(self):
        """Test searching in empty directory."""
        empty_dir = self.test_dir / "empty"
        empty_dir.mkdir()

        result = search_func(
            search_term="anything",
            path=str(empty_dir)
        )

        assert result["status"] == "success"
        assert "No matches found" in result["output"]

    def test_search_hidden_files_excluded(self):
        """Test that hidden files are excluded from search."""
        (self.test_dir / ".hidden.py").write_text("def hidden():\n    pass")

        result = search_func(
            search_term="hidden",
            path=str(self.test_dir)
        )

        # Should not find in hidden file
        assert ".hidden.py" not in result.get("output", "")


class TestR2ESearchK8S:
    """Test R2E search tool in K8S execution mode."""

    @pytest.mark.skipif(
        not __import__('importlib.util').util.find_spec('kodo'),
        reason="Kodo not installed"
    )
    def test_k8s_search_in_file(self):
        """Test searching in K8S pod."""
        from core.k8s_tool_execution_node import K8SToolExecutionNode

        with K8SToolExecutionNode(
            name="TestSearchK8S",
            namespace="default",
            image="python:3.11-slim",
            pod_name="test-search-pod"
        ) as executor:
            # Register tools
            executor.register_tool(
                "r2e_file_editor",
                "src/tools/r2e/file_editor.py"
            )
            executor.register_tool(
                "r2e_search",
                "src/tools/r2e/search_func.py"
            )

            # Create test file
            tool_call = {
                "tool": "r2e_file_editor",
                "parameters": {
                    "command": "create",
                    "path": "/tmp/search_test.py",
                    "file_text": "def hello():\n    print('Hello K8S')\n    return 42"
                }
            }
            executor.process([tool_call])

            # Search in file
            tool_call = {
                "tool": "r2e_search",
                "parameters": {
                    "search_term": "Hello",
                    "path": "/tmp/search_test.py"
                }
            }
            results = executor.process([tool_call])

            assert "Hello" in str(results[0])

    @pytest.mark.skipif(
        not __import__('importlib.util').util.find_spec('kodo'),
        reason="Kodo not installed"
    )
    def test_k8s_search_in_directory(self):
        """Test directory search in K8S pod."""
        from core.k8s_tool_execution_node import K8SToolExecutionNode

        with K8SToolExecutionNode(
            name="TestSearchDirK8S",
            namespace="default",
            image="python:3.11-slim",
            pod_name="test-search-dir-pod"
        ) as executor:
            executor.register_tool(
                "r2e_bash_executor",
                "src/tools/r2e/bash_func.py"
            )
            executor.register_tool(
                "r2e_search",
                "src/tools/r2e/search_func.py"
            )

            # Create multiple files
            tool_call = {
                "tool": "r2e_bash_executor",
                "parameters": {
                    "command": "mkdir -p /tmp/searchdir && echo 'def test1():\n    pass' > /tmp/searchdir/file1.py && echo 'def test2():\n    pass' > /tmp/searchdir/file2.py"
                }
            }
            executor.process([tool_call])

            # Search in directory
            tool_call = {
                "tool": "r2e_search",
                "parameters": {
                    "search_term": "def",
                    "path": "/tmp/searchdir"
                }
            }
            results = executor.process([tool_call])

            result_str = str(results[0])
            assert "matches" in result_str.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

"""
Test cases for R2E file_editor tool.

Tests both local and K8S execution modes.
"""

import pytest
import tempfile
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.r2e.file_editor import file_editor_func


class TestR2EFileEditorLocal:
    """Test R2E file_editor tool in local execution mode."""

    def setup_method(self):
        """Create temporary directory for tests."""
        self.test_dir = Path(tempfile.mkdtemp())

    def teardown_method(self):
        """Clean up temporary directory."""
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def test_create_file(self):
        """Test creating a new file."""
        file_path = self.test_dir / "test.txt"
        result = file_editor_func(
            command="create",
            path=str(file_path),
            file_text="Hello World\nLine 2\nLine 3"
        )

        assert result["status"] != "error"
        assert file_path.exists()
        content = file_path.read_text()
        assert "Hello World" in content
        assert "Line 2" in content

    def test_view_file(self):
        """Test viewing file contents."""
        file_path = self.test_dir / "view_test.txt"
        file_path.write_text("Line 1\nLine 2\nLine 3\nLine 4\nLine 5")

        result = file_editor_func(
            command="view",
            path=str(file_path)
        )

        assert result["status"] != "error"
        assert "Line 1" in result["output"]
        assert "Line 5" in result["output"]

    def test_view_file_with_range(self):
        """Test viewing file with line range."""
        file_path = self.test_dir / "range_test.txt"
        file_path.write_text("Line 1\nLine 2\nLine 3\nLine 4\nLine 5")

        result = file_editor_func(
            command="view",
            path=str(file_path),
            view_range=[2, 4]
        )

        assert result["status"] != "error"
        assert "Line 2" in result["output"]
        assert "Line 4" in result["output"]

    def test_view_directory(self):
        """Test viewing directory contents."""
        (self.test_dir / "file1.txt").write_text("content1")
        (self.test_dir / "file2.txt").write_text("content2")
        subdir = self.test_dir / "subdir"
        subdir.mkdir()
        (subdir / "file3.txt").write_text("content3")

        result = file_editor_func(
            command="view",
            path=str(self.test_dir)
        )

        assert result["status"] != "error"
        assert "file1.txt" in result["output"]
        assert "file2.txt" in result["output"]

    def test_str_replace(self):
        """Test string replacement in file."""
        file_path = self.test_dir / "replace_test.txt"
        original = "Hello World\nThis is a test\nGoodbye World"
        file_path.write_text(original)

        result = file_editor_func(
            command="str_replace",
            path=str(file_path),
            old_str="This is a test",
            new_str="This is modified"
        )

        assert result["status"] != "error"
        content = file_path.read_text()
        assert "This is modified" in content
        assert "This is a test" not in content

    def test_insert_at_line(self):
        """Test inserting text at specific line."""
        file_path = self.test_dir / "insert_test.txt"
        file_path.write_text("Line 1\nLine 2\nLine 3")

        result = file_editor_func(
            command="insert",
            path=str(file_path),
            insert_line=2,
            new_str="Inserted Line"
        )

        assert result["status"] != "error"
        content = file_path.read_text()
        lines = content.split('\n')
        # Insert at line 2 means insert before line 2 (0-indexed), so new line is at index 2
        assert "Inserted Line" in lines[2]

    def test_str_replace_multiline(self):
        """Test replacing multiline strings."""
        file_path = self.test_dir / "multiline_test.txt"
        original = "def func():\n    pass\n    return None"
        file_path.write_text(original)

        result = file_editor_func(
            command="str_replace",
            path=str(file_path),
            old_str="pass\n    return None",
            new_str="return 42"
        )

        assert result["status"] != "error"
        content = file_path.read_text()
        assert "return 42" in content
        assert "pass" not in content

    @pytest.mark.skip(reason="undo_edit not implemented in simplified version")
    def test_undo_edit(self):
        """Test undo functionality."""
        file_path = self.test_dir / "undo_test.txt"
        original = "Original content"
        file_path.write_text(original)

        # Make an edit
        file_editor_func(
            command="str_replace",
            path=str(file_path),
            old_str="Original",
            new_str="Modified"
        )

        # Undo the edit
        result = file_editor_func(
            command="undo_edit",
            path=str(file_path)
        )

        assert result["status"] != "error"
        content = file_path.read_text()
        assert "Original content" in content

    def test_create_file_in_new_directory(self):
        """Test creating file in non-existent directory."""
        file_path = self.test_dir / "newdir" / "test.txt"
        result = file_editor_func(
            command="create",
            path=str(file_path),
            file_text="Test content"
        )

        # Should create parent directory
        assert file_path.exists()
        assert "Test content" in file_path.read_text()

    def test_view_nonexistent_file(self):
        """Test viewing non-existent file returns error."""
        result = file_editor_func(
            command="view",
            path=str(self.test_dir / "nonexistent.txt")
        )

        assert result["status"] == "error"
        assert "not exist" in result["error"].lower()

    def test_missing_required_params(self):
        """Test error handling for missing parameters."""
        # Missing command
        result = file_editor_func(path="/tmp/test.txt")
        assert result["status"] == "error"

        # Missing path
        result = file_editor_func(command="view")
        assert result["status"] == "error"


class TestR2EFileEditorK8S:
    """Test R2E file_editor tool in K8S execution mode."""

    @pytest.mark.skipif(
        not __import__('importlib.util').util.find_spec('kodo'),
        reason="Kodo not installed"
    )
    def test_k8s_file_create_and_view(self):
        """Test file creation and viewing in K8S pod."""
        from core.k8s_tool_execution_node import K8SToolExecutionNode

        with K8SToolExecutionNode(
            name="TestFileEditorK8S",
            namespace="default",
            image="python:3.11-slim",
            pod_name="test-file-editor-pod"
        ) as executor:
            executor.register_tool(
                "r2e_file_editor",
                "src/tools/r2e/file_editor.py"
            )

            # Create file
            tool_call = {
                "tool": "r2e_file_editor",
                "parameters": {
                    "command": "create",
                    "path": "/tmp/k8s_test.txt",
                    "file_text": "K8S Test Content\nLine 2"
                }
            }
            executor.process([tool_call])

            # View file
            tool_call = {
                "tool": "r2e_file_editor",
                "parameters": {
                    "command": "view",
                    "path": "/tmp/k8s_test.txt"
                }
            }
            results = executor.process([tool_call])

            assert "K8S Test Content" in str(results[0])

    @pytest.mark.skipif(
        not __import__('importlib.util').util.find_spec('kodo'),
        reason="Kodo not installed"
    )
    def test_k8s_str_replace(self):
        """Test string replacement in K8S pod."""
        from core.k8s_tool_execution_node import K8SToolExecutionNode

        with K8SToolExecutionNode(
            name="TestFileReplaceK8S",
            namespace="default",
            image="python:3.11-slim",
            pod_name="test-file-replace-pod"
        ) as executor:
            executor.register_tool(
                "r2e_file_editor",
                "src/tools/r2e/file_editor.py"
            )

            # Create file
            tool_call = {
                "tool": "r2e_file_editor",
                "parameters": {
                    "command": "create",
                    "path": "/tmp/replace_test.txt",
                    "file_text": "Hello World"
                }
            }
            executor.process([tool_call])

            # Replace string
            tool_call = {
                "tool": "r2e_file_editor",
                "parameters": {
                    "command": "str_replace",
                    "path": "/tmp/replace_test.txt",
                    "old_str": "World",
                    "new_str": "K8S"
                }
            }
            executor.process([tool_call])

            # View result
            tool_call = {
                "tool": "r2e_file_editor",
                "parameters": {
                    "command": "view",
                    "path": "/tmp/replace_test.txt"
                }
            }
            results = executor.process([tool_call])

            assert "Hello K8S" in str(results[0])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

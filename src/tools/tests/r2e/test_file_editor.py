#!/usr/bin/env python3
"""
Test suite for file_editor.py tool.
"""

import sys
import os
import tempfile
from pathlib import Path

# Add parent directories to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from tools.r2e.file_editor import file_editor_func
from tools.tests.r2e.test_base import R2EToolTestBase


class TestFileEditor(R2EToolTestBase):
    """Test cases for file_editor tool."""

    def test_local_view_file(self):
        """Test viewing a file."""
        content = """Line 1: Hello World
Line 2: This is a test
Line 3: Another line"""

        filepath, temp_file = self.create_temp_file(content)

        try:
            result = file_editor_func(command="view", path=filepath)

            assert result["status"] == "success", "View should succeed"
            assert "Line 1" in result["output"], "Output should contain file content"
            assert "Line 2" in result["output"], "Output should contain file content"
            print("✅ test_local_view_file: PASSED")
        finally:
            os.unlink(filepath)

    def test_local_view_directory(self):
        """Test viewing a directory."""
        temp_dir = self.create_temp_dir()

        try:
            # Create some files
            Path(temp_dir.name, "test1.txt").write_text("content1")
            Path(temp_dir.name, "test2.txt").write_text("content2")

            result = file_editor_func(command="view", path=temp_dir.name)

            assert result["status"] == "success", "View directory should succeed"
            assert "test1.txt" in result["output"] or temp_dir.name in result["output"], "Should show directory contents"
            print("✅ test_local_view_directory: PASSED")
        finally:
            temp_dir.cleanup()

    def test_local_view_nonexistent(self):
        """Test viewing a non-existent path."""
        result = file_editor_func(command="view", path="/nonexistent_path_12345")

        assert result["status"] == "error", "Should fail for non-existent path"
        assert "does not exist" in result["error"], "Error should mention path doesn't exist"
        print("✅ test_local_view_nonexistent: PASSED")

    def test_local_create_file(self):
        """Test creating a new file."""
        temp_dir = self.create_temp_dir()

        try:
            new_file = os.path.join(temp_dir.name, "new_file.txt")
            content = "This is new content"

            result = file_editor_func(command="create", path=new_file, file_text=content)

            assert result["status"] == "success", "Create should succeed"
            assert os.path.exists(new_file), "File should be created"

            # Verify content
            with open(new_file, 'r') as f:
                actual_content = f.read()
            assert actual_content == content, "File content should match"

            print("✅ test_local_create_file: PASSED")
        finally:
            temp_dir.cleanup()

    def test_local_create_existing_file(self):
        """Test creating a file that already exists (should fail)."""
        filepath, temp_file = self.create_temp_file("existing content")

        try:
            result = file_editor_func(
                command="create",
                path=filepath,
                file_text="new content"
            )

            assert result["status"] == "error", "Should fail when file exists"
            assert "already exists" in result["error"].lower() or "exist" in result["error"].lower(), \
                "Error should mention file exists"
            print("✅ test_local_create_existing_file: PASSED")
        finally:
            os.unlink(filepath)

    def test_local_str_replace_simple(self):
        """Test simple string replacement."""
        content = "Hello World\nHello Python\nGoodbye World"
        filepath, temp_file = self.create_temp_file(content)

        try:
            result = file_editor_func(
                command="str_replace",
                path=filepath,
                old_str="Hello World",
                new_str="Hi Universe"
            )

            assert result["status"] == "success", "str_replace should succeed"

            # Verify replacement
            with open(filepath, 'r') as f:
                new_content = f.read()
            assert "Hi Universe" in new_content, "New string should be in file"
            assert "Hello World" not in new_content, "Old string should be removed"

            print("✅ test_local_str_replace_simple: PASSED")
        finally:
            os.unlink(filepath)

    def test_local_str_replace_multiline(self):
        """Test multiline string replacement."""
        content = """def hello():
    print("Hello")
    return True

def goodbye():
    print("Goodbye")"""

        filepath, temp_file = self.create_temp_file(content)

        try:
            old_str = """def hello():
    print("Hello")
    return True"""

            new_str = """def hello():
    print("Hi")
    return False"""

            result = file_editor_func(
                command="str_replace",
                path=filepath,
                old_str=old_str,
                new_str=new_str
            )

            assert result["status"] == "success", "Multiline replace should succeed"

            # Verify replacement
            with open(filepath, 'r') as f:
                new_content = f.read()
            assert 'print("Hi")' in new_content, "New content should be in file"
            assert "return False" in new_content, "New content should be in file"

            print("✅ test_local_str_replace_multiline: PASSED")
        finally:
            os.unlink(filepath)

    def test_local_str_replace_not_found(self):
        """Test replacement when string is not found."""
        content = "Hello World"
        filepath, temp_file = self.create_temp_file(content)

        try:
            result = file_editor_func(
                command="str_replace",
                path=filepath,
                old_str="NonExistent String",
                new_str="New String"
            )

            # Should fail or indicate no match found
            assert result["status"] == "error" or "not found" in result.get("output", "").lower(), \
                "Should indicate string not found"

            print("✅ test_local_str_replace_not_found: PASSED")
        finally:
            os.unlink(filepath)

    def test_local_insert(self):
        """Test inserting content at a specific line."""
        content = """Line 1
Line 2
Line 3"""

        filepath, temp_file = self.create_temp_file(content)

        try:
            result = file_editor_func(
                command="insert",
                path=filepath,
                insert_line=1,
                new_str="Inserted Line"
            )

            assert result["status"] == "success", "Insert should succeed"

            # Verify insertion
            with open(filepath, 'r') as f:
                new_content = f.read()
            lines = new_content.split('\n')
            assert "Inserted Line" in new_content, "Inserted content should be in file"

            print("✅ test_local_insert: PASSED")
        finally:
            os.unlink(filepath)

    def test_local_missing_command(self):
        """Test with missing command parameter."""
        result = file_editor_func(path="/some/path")

        assert result["status"] == "error", "Should fail without command"
        assert "Missing required parameter 'command'" in result["error"], \
            "Error should mention missing command"
        print("✅ test_local_missing_command: PASSED")

    def test_local_missing_path(self):
        """Test with missing path parameter."""
        result = file_editor_func(command="view")

        assert result["status"] == "error", "Should fail without path"
        assert "Missing required parameter 'path'" in result["error"], \
            "Error should mention missing path"
        print("✅ test_local_missing_path: PASSED")

    def test_local_invalid_command(self):
        """Test with invalid command."""
        result = file_editor_func(command="invalid_command", path="/some/path")

        assert result["status"] == "error", "Should fail with invalid command"
        assert "Unknown command" in result["error"], "Error should mention unknown command"
        print("✅ test_local_invalid_command: PASSED")

    def test_local_create_with_special_chars(self):
        """Test creating file with special characters."""
        temp_dir = self.create_temp_dir()

        try:
            new_file = os.path.join(temp_dir.name, "special.txt")
            content = """It's a test with "quotes"
Path: C:\\Users\\test
Command: echo $VAR && ls -la
Unicode: 你好世界 🎉"""

            result = file_editor_func(command="create", path=new_file, file_text=content)

            assert result["status"] == "success", "Create with special chars should succeed"
            assert os.path.exists(new_file), "File should be created"

            # Verify content
            with open(new_file, 'r', encoding='utf-8') as f:
                actual_content = f.read()
            assert "It's a test" in actual_content, "Special chars should be preserved"
            assert "你好世界" in actual_content, "Unicode should be preserved"

            print("✅ test_local_create_with_special_chars: PASSED")
        finally:
            temp_dir.cleanup()

    def test_local_view_with_range(self):
        """Test viewing file with line range."""
        content = "\n".join([f"Line {i}" for i in range(1, 21)])
        filepath, temp_file = self.create_temp_file(content)

        try:
            result = file_editor_func(
                command="view",
                path=filepath,
                view_range=[5, 10]
            )

            assert result["status"] == "success", "View with range should succeed"
            # Should show lines 5-10
            print("✅ test_local_view_with_range: PASSED")
        finally:
            os.unlink(filepath)


def run_all_tests():
    """Run all file_editor tool tests."""
    print("=" * 80)
    print("FILE_EDITOR TOOL TEST SUITE")
    print("=" * 80)

    test = TestFileEditor()

    # Local function tests
    print("\n[Local Function Tests - View]")
    test.test_local_view_file()
    test.test_local_view_directory()
    test.test_local_view_nonexistent()
    test.test_local_view_with_range()

    print("\n[Local Function Tests - Create]")
    test.test_local_create_file()
    test.test_local_create_existing_file()
    test.test_local_create_with_special_chars()

    print("\n[Local Function Tests - Replace]")
    test.test_local_str_replace_simple()
    test.test_local_str_replace_multiline()
    test.test_local_str_replace_not_found()

    print("\n[Local Function Tests - Insert]")
    test.test_local_insert()

    print("\n[Local Function Tests - Error Cases]")
    test.test_local_missing_command()
    test.test_local_missing_path()
    test.test_local_invalid_command()

    print("\n" + "=" * 80)
    print("✅ ALL FILE_EDITOR TOOL TESTS PASSED")
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

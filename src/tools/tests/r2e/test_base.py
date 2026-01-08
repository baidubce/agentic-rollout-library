"""
Base test utilities for R2E tools testing.
"""

import os
import tempfile
from pathlib import Path
from typing import Tuple


class R2EToolTestBase:
    """Base class for R2E tool tests."""

    @staticmethod
    def get_src_dir() -> Path:
        """Get the src directory path."""
        # From src/tools/tests/r2e, go up to src/
        return Path(__file__).parent.parent.parent.parent

    @staticmethod
    def create_temp_file(content: str, suffix: str = ".txt") -> Tuple[str, tempfile.NamedTemporaryFile]:
        """
        Create a temporary file with given content.

        Args:
            content: Content to write to file
            suffix: File suffix

        Returns:
            Tuple of (filepath, temp_file_object)
        """
        temp_file = tempfile.NamedTemporaryFile(mode='w', suffix=suffix, delete=False)
        temp_file.write(content)
        temp_file.flush()
        return temp_file.name, temp_file

    @staticmethod
    def create_temp_dir() -> tempfile.TemporaryDirectory:
        """
        Create a temporary directory.

        Returns:
            Temporary directory object
        """
        return tempfile.TemporaryDirectory()

    @staticmethod
    def assert_success(result: dict, test_name: str):
        """Assert that a test result indicates success."""
        if result.get("status") == "error" or result.get("success") is False:
            print(f"❌ {test_name}: FAILED")
            print(f"   Error: {result.get('error', result.get('stderr', 'Unknown error'))}")
            raise AssertionError(f"Test {test_name} failed")

        print(f"✅ {test_name}: PASSED")

    @staticmethod
    def assert_failure(result: dict, test_name: str):
        """Assert that a test should fail (for negative testing)."""
        if result.get("status") != "error" and result.get("success") is not False:
            print(f"❌ {test_name}: Should have failed but succeeded")
            raise AssertionError(f"Test {test_name} should have failed")

        print(f"✅ {test_name}: PASSED (correctly failed)")

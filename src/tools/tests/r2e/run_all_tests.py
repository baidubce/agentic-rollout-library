#!/usr/bin/env python3
"""
Master test runner for all R2E tools.

This script runs all test suites for R2E tools including:
- bash_func.py
- file_editor.py
- search_func.py
- finish.py

Usage:
    python run_all_tests.py                    # Run all tests
    python run_all_tests.py --tool bash        # Run tests for specific tool
    python run_all_tests.py --verbose          # Verbose output
"""

import sys
import argparse
from pathlib import Path

# Add parent directories to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

# Import test modules
from tools.tests.r2e import test_bash_func, test_file_editor, test_search_func, test_finish


def run_bash_tests():
    """Run bash_func tool tests."""
    print("\n" + "╔" + "═" * 78 + "╗")
    print("║" + " " * 27 + "BASH_FUNC TOOL TESTS" + " " * 31 + "║")
    print("╚" + "═" * 78 + "╝")
    test_bash_func.run_all_tests()


def run_file_editor_tests():
    """Run file_editor tool tests."""
    print("\n" + "╔" + "═" * 78 + "╗")
    print("║" + " " * 25 + "FILE_EDITOR TOOL TESTS" + " " * 30 + "║")
    print("╚" + "═" * 78 + "╝")
    test_file_editor.run_all_tests()


def run_search_tests():
    """Run search_func tool tests."""
    print("\n" + "╔" + "═" * 78 + "╗")
    print("║" + " " * 26 + "SEARCH_FUNC TOOL TESTS" + " " * 29 + "║")
    print("╚" + "═" * 78 + "╝")
    test_search_func.run_all_tests()


def run_finish_tests():
    """Run finish tool tests."""
    print("\n" + "╔" + "═" * 78 + "╗")
    print("║" + " " * 27 + "FINISH TOOL TESTS" + " " * 33 + "║")
    print("╚" + "═" * 78 + "╝")
    test_finish.run_all_tests()


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Run R2E tools test suite",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--tool",
        choices=["bash", "file_editor", "search", "finish", "all"],
        default="all",
        help="Which tool to test (default: all)"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Verbose output"
    )

    args = parser.parse_args()

    print("\n" + "╔" + "═" * 78 + "╗")
    print("║" + " " * 24 + "R2E TOOLS TEST SUITE" + " " * 33 + "║")
    print("╚" + "═" * 78 + "╝")

    failed = False
    tests_to_run = []

    if args.tool == "all":
        tests_to_run = [
            ("bash_func", run_bash_tests),
            ("file_editor", run_file_editor_tests),
            ("search_func", run_search_tests),
            ("finish", run_finish_tests),
        ]
    elif args.tool == "bash":
        tests_to_run = [("bash_func", run_bash_tests)]
    elif args.tool == "file_editor":
        tests_to_run = [("file_editor", run_file_editor_tests)]
    elif args.tool == "search":
        tests_to_run = [("search_func", run_search_tests)]
    elif args.tool == "finish":
        tests_to_run = [("finish", run_finish_tests)]

    for tool_name, test_func in tests_to_run:
        try:
            test_func()
        except (AssertionError, Exception) as e:
            print(f"\n❌ {tool_name.upper()} TESTS FAILED: {e}")
            failed = True
            if not args.verbose:
                print("   (Use --verbose for more details)")

    # Final summary
    print("\n" + "╔" + "═" * 78 + "╗")
    print("║" + " " * 32 + "SUMMARY" + " " * 39 + "║")
    print("╚" + "═" * 78 + "╝")

    if failed:
        print("❌ SOME TESTS FAILED")
        print("\nRe-run with specific tool: python run_all_tests.py --tool <tool_name>")
        sys.exit(1)
    else:
        print("✅ ALL TESTS PASSED!")
        print(f"\nTools tested: {', '.join([t[0] for t in tests_to_run])}")
        print("\nTest coverage:")
        print("  ✓ Local function execution")
        print("  ✓ Parameter validation")
        print("  ✓ Special characters and edge cases")
        print("  ✓ Error handling and validation")
        print("  ✓ File operations (create, view, edit)")
        sys.exit(0)


if __name__ == "__main__":
    main()

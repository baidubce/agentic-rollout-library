#!/usr/bin/env python3
"""
Master test runner for all Miaoda tools.

This script runs all test suites for Miaoda tools including:
- think.py
- finish.py
- supabase_init.py
- supabase_migration.py
- supabase_sql_execution.py

Usage:
    python run_all_tests.py                    # Run all tests
    python run_all_tests.py --tool think       # Run tests for specific tool
    python run_all_tests.py --verbose          # Verbose output
"""

import sys
import argparse
from pathlib import Path

# Add parent directories to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

# Import test modules
from tools.tests.miaoda import test_think, test_finish, test_supabase


def run_think_tests():
    """Run think tool tests."""
    import warnings
    warnings.filterwarnings('ignore', category=ResourceWarning)
    print("\n" + "╔" + "═" * 78 + "╗")
    print("║" + " " * 28 + "THINK TOOL TESTS" + " " * 34 + "║")
    print("╚" + "═" * 78 + "╝")
    test_think.run_all_tests()


def run_finish_tests():
    """Run finish tool tests."""
    import warnings
    warnings.filterwarnings('ignore', category=ResourceWarning)
    print("\n" + "╔" + "═" * 78 + "╗")
    print("║" + " " * 27 + "FINISH TOOL TESTS" + " " * 33 + "║")
    print("╚" + "═" * 78 + "╝")
    test_finish.run_all_tests()


def run_supabase_tests():
    """Run Supabase tools tests."""
    import warnings
    warnings.filterwarnings('ignore', category=ResourceWarning)
    print("\n" + "╔" + "═" * 78 + "╗")
    print("║" + " " * 25 + "SUPABASE TOOLS TESTS" + " " * 32 + "║")
    print("╚" + "═" * 78 + "╝")
    test_supabase.run_all_tests()


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Run Miaoda tools test suite",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--tool",
        choices=["think", "finish", "supabase", "all"],
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
    print("║" + " " * 21 + "MIAODA TOOLS TEST SUITE" + " " * 33 + "║")
    print("╚" + "═" * 78 + "╝")

    failed = False
    tests_to_run = []

    if args.tool == "all":
        tests_to_run = [
            ("think", run_think_tests),
            ("finish", run_finish_tests),
            ("supabase", run_supabase_tests),
        ]
    elif args.tool == "think":
        tests_to_run = [("think", run_think_tests)]
    elif args.tool == "finish":
        tests_to_run = [("finish", run_finish_tests)]
    elif args.tool == "supabase":
        tests_to_run = [("supabase", run_supabase_tests)]

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
        print("  ✓ K8S command generation and execution")
        print("  ✓ Special characters and edge cases")
        print("  ✓ Error handling and validation")
        sys.exit(0)


if __name__ == "__main__":
    main()

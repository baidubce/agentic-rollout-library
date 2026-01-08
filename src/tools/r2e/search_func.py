#!/usr/bin/env python3
"""
R2E Search Tool - Search for terms in files and directories.
"""

import os
import subprocess
from typing import Dict, Any


def search_func(**kwargs) -> Dict[str, Any]:
    """
    Search for a term in files or directories.

    Parameters:
        search_term (str, required): The term to search for
        path (str, optional): Path to search in (file or directory). Defaults to '.'

    Returns:
        Dict with 'output', 'error', and 'status' keys
    """
    search_term = kwargs.get('search_term')
    path_str = kwargs.get('path', '.')

    if not search_term:
        return {
            "output": "",
            "error": "Missing required parameter 'search_term'",
            "status": "error"
        }

    path_str = os.path.realpath(path_str)

    if not os.path.exists(path_str):
        return {
            "output": "",
            "error": f"Path does not exist: {path_str}",
            "status": "error"
        }

    try:
        if os.path.isfile(path_str):
            # Search in a single file using grep -n
            result = _search_in_file(search_term, path_str)
        else:
            # Search in directory recursively
            result = _search_in_directory(search_term, path_str)

        return result

    except Exception as e:
        return {
            "output": "",
            "error": f"Search error: {e}",
            "status": "error"
        }


def _search_in_file(search_term: str, filepath: str) -> Dict[str, Any]:
    """
    Uses grep -n to search for `search_term` in a single file.
    Prints lines (with line numbers) where matches occur.
    """
    try:
        # Try modern subprocess parameters (Python 3.7+)
        try:
            result = subprocess.run(
                ["grep", "-n", search_term, filepath],
                capture_output=True,
                text=True,
                check=False
            )
        except TypeError:
            # Fallback for Python 3.5/3.6
            result = subprocess.run(
                ["grep", "-n", search_term, filepath],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                check=False
            )

        if result.returncode == 0:
            # Found matches
            output = f'Matches for "{search_term}" in {filepath}:\n'
            output += result.stdout.strip()
            return {"output": output, "error": "", "status": "success"}
        elif result.returncode == 1:
            # No matches found (grep returns 1 when no matches)
            output = f'No matches found for "{search_term}" in {filepath}'
            return {"output": output, "error": "", "status": "success"}
        else:
            # Real error occurred
            error_msg = result.stderr.strip() if result.stderr else "grep command failed"
            return {"output": "", "error": error_msg, "status": "error"}

    except FileNotFoundError:
        return {
            "output": "",
            "error": "grep is not available on this system",
            "status": "error"
        }
    except Exception as e:
        return {"output": "", "error": str(e), "status": "error"}


def _search_in_directory(search_term: str, directory: str, python_only: bool = True) -> Dict[str, Any]:
    """
    Searches for `search_term` in all non-hidden files under `directory`
    (or only in .py files if `python_only=True`), excluding hidden directories.
    Returns how many matches were found per file.
    """
    matches = {}
    num_files_matched = 0

    try:
        for root, dirs, files in os.walk(directory):
            # Exclude hidden directories
            dirs[:] = [d for d in dirs if not d.startswith(".")]

            for file in files:
                # Skip hidden files
                if file.startswith("."):
                    continue

                # If python_only is set, only search .py files
                if python_only and not file.endswith(".py"):
                    continue

                filepath = os.path.join(root, file)
                try:
                    with open(filepath, "r", errors="ignore") as f:
                        file_matches = 0
                        for line_num, line in enumerate(f, 1):
                            if search_term in line:
                                file_matches += 1
                        if file_matches > 0:
                            matches[filepath] = file_matches
                            num_files_matched += 1
                except (UnicodeDecodeError, PermissionError):
                    # Skip files that can't be read
                    continue

        # Build output
        if not matches:
            output = f'No matches found for "{search_term}" in {directory}'
            return {"output": output, "error": "", "status": "success"}

        # Check if too many files matched
        if num_files_matched > 100:
            output = (
                f'More than {num_files_matched} files matched for "{search_term}" in {directory}. '
                "Please narrow your search."
            )
            return {"output": output, "error": "", "status": "success"}

        # Summarize results
        num_matches = sum(matches.values())
        output = f'Found {num_matches} matches for "{search_term}" in {directory}:\n'

        # List matched files with counts
        for filepath, count in matches.items():
            relative_path = os.path.relpath(filepath, start=os.getcwd())
            if not relative_path.startswith("./"):
                relative_path = "./" + relative_path
            output += f"{relative_path} ({count} matches)\n"

        output += f'End of matches for "{search_term}" in {directory}'

        return {"output": output, "error": "", "status": "success"}

    except Exception as e:
        return {"output": "", "error": str(e), "status": "error"}


if __name__ == "__main__":
    # Test the function
    import argparse

    parser = argparse.ArgumentParser(description="Search tool for files and directories")
    parser.add_argument("--search_term", required=True, help="Term to search for")
    parser.add_argument("--path", default=".", help="Path to search in")

    args = parser.parse_args()

    result = search_func(search_term=args.search_term, path=args.path)

    if result["error"]:
        print(f"ERROR: {result['error']}")
    else:
        print(result["output"])

#!/usr/bin/env python3
"""
R2E File Editor Tool - Simplified version for tool execution.

Supports commands: view, create, str_replace, insert, undo_edit
"""

import subprocess
from pathlib import Path
from typing import Dict, Any, Optional, List


def file_editor_func(**kwargs) -> Dict[str, Any]:
    """
    File editor function for R2E tool execution.

    Parameters:
        command (str): One of: view, create, str_replace, insert, undo_edit
        path (str): Absolute path to file or directory
        file_text (str, optional): Content for create command
        old_str (str, optional): String to replace (for str_replace)
        new_str (str, optional): Replacement string (for str_replace/insert)
        insert_line (int, optional): Line number for insert command
        view_range (list, optional): [start_line, end_line] for view
        concise (bool, optional): Use concise view for Python files

    Returns:
        Dict with 'output', 'error', and 'status' keys
    """
    command = kwargs.get('command')
    path_str = kwargs.get('path')

    if not command:
        return {
            "output": "",
            "error": "Missing required parameter 'command'",
            "status": "error"
        }

    if not path_str:
        return {
            "output": "",
            "error": "Missing required parameter 'path'",
            "status": "error"
        }

    path = Path(path_str)

    try:
        if command == "view":
            return _view(
                path,
                kwargs.get('view_range'),
                kwargs.get('concise', False)
            )
        elif command == "create":
            return _create(path, kwargs.get('file_text'))
        elif command == "str_replace":
            return _str_replace(
                path,
                kwargs.get('old_str'),
                kwargs.get('new_str', '')
            )
        elif command == "insert":
            return _insert(
                path,
                kwargs.get('insert_line'),
                kwargs.get('new_str')
            )
        elif command == "undo_edit":
            return _undo_edit(path)
        else:
            return {
                "output": "",
                "error": f"Unknown command '{command}'. Allowed: view, create, str_replace, insert, undo_edit",
                "status": "error"
            }
    except Exception as e:
        return {
            "output": "",
            "error": str(e),
            "status": "error"
        }


def _view(path: Path, view_range: Optional[List[int]] = None, concise: bool = False) -> Dict[str, Any]:
    """View file or directory contents."""
    if not path.exists():
        return {
            "output": "",
            "error": f"Path does not exist: {path}",
            "status": "error"
        }

    if path.is_dir():
        # List directory contents
        try:
            cmd = ["find", str(path), "-maxdepth", "2", "-not", "-path", "*/.*"]
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False
            )

            if proc.stderr:
                return {"output": "", "error": proc.stderr.strip(), "status": "error"}

            output = f"Here's the files and directories up to 2 levels deep in {path}, excluding hidden:\n{proc.stdout}"
            return {"output": output, "error": "", "status": "success"}
        except Exception as e:
            return {"output": "", "error": str(e), "status": "error"}

    # View file
    try:
        content = path.read_text(encoding='utf-8')
        lines = content.splitlines()

        # Apply view_range if specified
        if view_range and len(view_range) == 2:
            start, end = view_range
            if end == -1:
                end = len(lines)

            if not (1 <= start <= len(lines)):
                return {
                    "output": "",
                    "error": f"Invalid start line {start}. Must be between 1 and {len(lines)}",
                    "status": "error"
                }

            # Convert to 0-based indexing
            lines = lines[start-1:end]
            start_num = start
        else:
            start_num = 1

        # Format with line numbers (cat -n style)
        numbered_lines = [f"{i + start_num:6d}\t{line}" for i, line in enumerate(lines)]
        output = f"Here's the result of running `cat -n` on {path}:\n"
        output += "\n".join(numbered_lines)

        # Truncate if too long
        max_len = 10000
        if len(output) > max_len:
            output = output[:max_len] + "\n<response clipped>"

        return {"output": output, "error": "", "status": "success"}

    except Exception as e:
        return {"output": "", "error": f"Error reading file: {e}", "status": "error"}


def _create(path: Path, file_text: Optional[str]) -> Dict[str, Any]:
    """Create a new file."""
    if path.exists():
        return {
            "output": "",
            "error": f"File already exists at {path}. Cannot overwrite with 'create'.",
            "status": "error"
        }

    if file_text is None:
        return {
            "output": "",
            "error": "Missing required parameter 'file_text' for create command",
            "status": "error"
        }

    try:
        # Create parent directories if needed
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(file_text, encoding='utf-8')

        # Show snippet of created file
        lines = file_text.splitlines()[:20]  # First 20 lines
        numbered = "\n".join(f"{i+1:6d}\t{line}" for i, line in enumerate(lines))

        output = f"File created successfully at {path}.\n"
        output += f"Here's the result of running `cat -n` on {path}:\n{numbered}"
        if len(file_text.splitlines()) > 20:
            output += "\n... (file continues)"

        return {"output": output, "error": "", "status": "success"}

    except Exception as e:
        return {"output": "", "error": f"Error creating file: {e}", "status": "error"}


def _str_replace(path: Path, old_str: Optional[str], new_str: str) -> Dict[str, Any]:
    """Replace string in file."""
    if not path.exists():
        return {"output": "", "error": f"File does not exist: {path}", "status": "error"}

    if old_str is None:
        return {
            "output": "",
            "error": "Missing required parameter 'old_str' for str_replace",
            "status": "error"
        }

    try:
        content = path.read_text(encoding='utf-8')

        # Check occurrences
        count = content.count(old_str)
        if count == 0:
            return {
                "output": "",
                "error": f"No occurrences of the specified string found in {path}",
                "status": "error"
            }

        if count > 1:
            return {
                "output": "",
                "error": f"Multiple ({count}) occurrences found. The string must be unique.",
                "status": "error"
            }

        # Perform replacement
        new_content = content.replace(old_str, new_str)
        path.write_text(new_content, encoding='utf-8')

        # Show snippet around the change
        replacement_line = content.split(old_str)[0].count('\n')
        lines = new_content.splitlines()
        start = max(0, replacement_line - 4)
        end = min(len(lines), replacement_line + new_str.count('\n') + 5)

        snippet_lines = lines[start:end]
        numbered = "\n".join(f"{i+start+1:6d}\t{line}" for i, line in enumerate(snippet_lines))

        output = f"The file {path} has been edited successfully.\n"
        output += f"Here's a snippet of the edited section:\n{numbered}\n"
        output += "Review the changes and make sure they are as expected."

        return {"output": output, "error": "", "status": "success"}

    except Exception as e:
        return {"output": "", "error": f"Error performing str_replace: {e}", "status": "error"}


def _insert(path: Path, insert_line: Optional[int], new_str: Optional[str]) -> Dict[str, Any]:
    """Insert text at specified line."""
    if not path.exists():
        return {"output": "", "error": f"File does not exist: {path}", "status": "error"}

    if insert_line is None:
        return {
            "output": "",
            "error": "Missing required parameter 'insert_line' for insert",
            "status": "error"
        }

    if new_str is None:
        return {
            "output": "",
            "error": "Missing required parameter 'new_str' for insert",
            "status": "error"
        }

    try:
        content = path.read_text(encoding='utf-8')
        lines = content.splitlines()

        if not (0 <= insert_line <= len(lines)):
            return {
                "output": "",
                "error": f"Invalid insert_line {insert_line}. Must be between 0 and {len(lines)}",
                "status": "error"
            }

        # Insert new lines
        new_lines = new_str.splitlines()
        updated_lines = lines[:insert_line] + new_lines + lines[insert_line:]

        new_content = '\n'.join(updated_lines)
        path.write_text(new_content, encoding='utf-8')

        # Show snippet
        start = max(0, insert_line - 4)
        end = min(len(updated_lines), insert_line + len(new_lines) + 4)
        snippet_lines = updated_lines[start:end]
        numbered = "\n".join(f"{i+start+1:6d}\t{line}" for i, line in enumerate(snippet_lines))

        output = f"The file {path} has been edited successfully.\n"
        output += f"Here's a snippet of the edited section:\n{numbered}\n"
        output += "Review the changes (correct indentation, no duplicate lines, etc)."

        return {"output": output, "error": "", "status": "success"}

    except Exception as e:
        return {"output": "", "error": f"Error performing insert: {e}", "status": "error"}


def _undo_edit(path: Path) -> Dict[str, Any]:
    """Undo last edit (not implemented in simplified version)."""
    return {
        "output": "",
        "error": "undo_edit is not implemented in this simplified version",
        "status": "error"
    }


if __name__ == "__main__":
    # Test the function
    import argparse
    import json

    parser = argparse.ArgumentParser(description="File editor tool")
    parser.add_argument("command", help="Command: view, create, str_replace, insert, undo_edit")
    parser.add_argument("--path", required=True, help="Path to file or directory")
    parser.add_argument("--file_text", default=None, help="File content (for create)")
    parser.add_argument("--old_str", default=None, help="String to replace (for str_replace)")
    parser.add_argument("--new_str", default=None, help="Replacement string (for str_replace/insert)")
    parser.add_argument("--insert_line", type=int, default=None, help="Line number for insert")
    parser.add_argument("--view_range", default=None, help="View range [start, end], e.g., '[1, 50]'")
    parser.add_argument("--concise", action="store_true", help="Use concise view for Python files")

    args = parser.parse_args()

    # Parse view_range if provided
    view_range = None
    if args.view_range:
        try:
            view_range = json.loads(args.view_range)
        except:
            print(f"Error: Invalid view_range format. Use [start, end], e.g., '[1, 50]'")
            import sys
            sys.exit(1)

    result = file_editor_func(
        command=args.command,
        path=args.path,
        file_text=args.file_text,
        old_str=args.old_str,
        new_str=args.new_str,
        insert_line=args.insert_line,
        view_range=view_range,
        concise=args.concise
    )

    if result["error"]:
        print(f"ERROR: {result['error']}")
    else:
        print(result["output"])

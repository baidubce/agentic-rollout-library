#!/usr/bin/env python3
"""
String replace editor tool adapted from OpenHands for the agentic rollout framework.
Provides file viewing, creation, and editing capabilities.
"""

import os
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from ...core.base_tool import BaseAgenticTool
from ...core.tool_schemas import OpenAIFunctionToolSchema, ToolResult, create_openai_tool_schema

logger = logging.getLogger(__name__)


_DETAILED_STR_REPLACE_EDITOR_DESCRIPTION = """Custom editing tool for viewing, creating and editing files in plain-text format
* State is persistent across command calls and discussions with the user
* The `view` command displays the contents of a text file with line numbers (similar to `cat -n`)
* CRITICAL: The `view` command ONLY works with FILES, NOT directories. It will FAIL if you try to view a directory.
* To list directory contents, you MUST use the bash command `ls <directory_path>` instead of view
* The following binary file extensions can be viewed in Markdown format: [".xlsx", ".pptx", ".wav", ".mp3", ".m4a", ".flac", ".pdf", ".docx"]. IT DOES NOT HANDLE IMAGES.
* The `create` command cannot be used if the specified `path` already exists as a file
* IMPORTANT: The `create` command will FAIL if parent directories don't exist. Always create necessary directories first using `mkdir -p <directory>`
* If a `command` generates a long output, it will be truncated and marked with `<response clipped>`
* The `undo_edit` command will revert the last edit made to the file at `path`
* This tool can be used for creating and editing files in plain-text format.


Before using this tool:
1. NEVER use `view` command on directories - it will fail. Use execute_bash with ls to list directory contents
2. Use the `view` command ONLY for files to understand the file's contents and context
3. When creating new files:
   - ALWAYS verify the parent directory exists first using `ls <parent_directory>`
   - If creating a file in a new subdirectory (e.g., /workspace/src/components/game/file.tsx where 'game' doesn't exist):
     * First create the directory: `mkdir -p /workspace/src/components/game`
     * Then create the file with the `create` command
   - Example: To create /workspace/app/src/components/game/GameCell.tsx:
     * Check if parent exists: `ls /workspace/app/src/components`
     * Create subdirectory: `mkdir -p /workspace/app/src/components/game`
     * Then use `create` command with path `/workspace/app/src/components/game/GameCell.tsx`

When making edits:
   - Ensure the edit results in idiomatic, correct code
   - Do not leave the code in a broken state
   - Always use absolute file paths (starting with /)

CRITICAL REQUIREMENTS FOR USING THIS TOOL:

1. EXACT MATCHING: The `old_str` parameter must match EXACTLY one or more consecutive lines from the file, including all whitespace and indentation. The tool will fail if `old_str` matches multiple locations or doesn't match exactly with the file content.

2. UNIQUENESS: The `old_str` must uniquely identify a single instance in the file:
   - Include sufficient context before and after the change point (3-5 lines recommended)
   - If not unique, the replacement will not be performed

3. REPLACEMENT: The `new_str` parameter should contain the edited lines that replace the `old_str`. Both strings must be different.

Remember: when making multiple file edits in a row to the same file, you should prefer to send all edits in a single message with multiple calls to this tool, rather than multiple messages with a single call each.
"""

_SHORT_STR_REPLACE_EDITOR_DESCRIPTION = """Custom editing tool for viewing, creating and editing files in plain-text format
* State is persistent across command calls and discussions with the user
* The `view` command displays FILE contents with line numbers. CRITICAL: Only works with FILES, NOT directories - use `ls` for directories
* The `create` command cannot be used if the specified `path` already exists as a file. REQUIRES parent directories to exist - create them first with `mkdir -p`
* If a `command` generates a long output, it will be truncated and marked with `<response clipped>`
* The `undo_edit` command will revert the last edit made to the file at `path`
Notes for using the `str_replace` command:
* The `old_str` parameter should match EXACTLY one or more consecutive lines from the original file. Be mindful of whitespaces!
* If the `old_str` parameter is not unique in the file, the replacement will not be performed. Make sure to include enough context in `old_str` to make it unique
* The `new_str` parameter should contain the edited lines that should replace the `old_str`
"""


class OHStrReplaceEditorTool(BaseAgenticTool):
    """
    String replace editor tool adapted from OpenHands.
    Provides file viewing, creation, and editing capabilities.
    """
    
    def __init__(self, config: Optional[Dict] = None):
        """Initialize the string replace editor tool."""
        config = config or {}
        self.use_short_description = config.get("use_short_description", False)
        super().__init__(config)
        
        # File operation settings
        self.max_output_length = config.get("max_output_length", 10000)
        self.max_file_size = config.get("max_file_size", 1024 * 1024)  # 1MB default
        
        # Track file history for undo capability
        self.file_history = {}  # instance_id -> {file_path -> [history]}
    
    def get_openai_tool_schema(self) -> OpenAIFunctionToolSchema:
        """Return the OpenAI function schema for this tool."""
        description = (
            _SHORT_STR_REPLACE_EDITOR_DESCRIPTION if self.use_short_description 
            else _DETAILED_STR_REPLACE_EDITOR_DESCRIPTION
        )
        
        return create_openai_tool_schema(
            name="str_replace_editor",
            description=description,
            parameters={
                'command': {
                    'description': 'The commands to run. Allowed options are: `view`, `create`, `str_replace`, `insert`, `undo_edit`.',
                    'enum': [
                        'view',
                        'create',
                        'str_replace',
                        'insert',
                        'undo_edit',
                    ],
                    'type': 'string',
                },
                'path': {
                    'description': 'Absolute path to the FILE only (e.g. `/workspace/file.py`). NEVER use with directories - the view command will FAIL on directories. For `create` command: Parent directories MUST exist or creation will fail - use `mkdir -p` to create them first. To list directory contents, use execute_bash with ls instead.',
                    'type': 'string',
                },
                'file_text': {
                    'description': 'Required parameter of `create` command, with the content of the file to be created.',
                    'type': 'string',
                },
                'old_str': {
                    'description': 'Required parameter of `str_replace` command containing the string in `path` to replace.',
                    'type': 'string',
                },
                'new_str': {
                    'description': 'Optional parameter of `str_replace` command containing the new string (if not given, no string will be added). Required parameter of `insert` command containing the string to insert.',
                    'type': 'string',
                },
                'insert_line': {
                    'description': 'Required parameter of `insert` command. The `new_str` will be inserted AFTER the line `insert_line` of `path`.',
                    'type': 'integer',
                },
                'view_range': {
                    'description': 'Optional parameter of `view` command when `path` points to a file. If none is given, the full file is shown. If provided, the file will be shown in the indicated line number range, e.g. [11, 12] will show lines 11 and 12. Indexing at 1 to start. Setting `[start_line, -1]` shows all lines from `start_line` to the end of the file.',
                    'items': {'type': 'integer'},
                    'type': 'array',
                },
            },
            required=['command', 'path']
        )
    
    async def execute_tool(self, instance_id: str, parameters: Dict[str, Any], **kwargs) -> ToolResult:
        """
        Execute the string replace editor command.
        
        Args:
            instance_id: Tool instance ID
            parameters: Tool parameters
            **kwargs: Additional execution arguments
            
        Returns:
            ToolResult with command output or error
        """
        try:
            command = parameters.get("command")
            path = parameters.get("path")
            
            if not command or not path:
                return ToolResult(
                    success=False,
                    error="Missing required parameters: command and path"
                )
            
            # Initialize history for this instance if needed
            if instance_id not in self.file_history:
                self.file_history[instance_id] = {}
            
            # Execute command based on type
            if command == "view":
                return await self._view_file(path, parameters.get("view_range"))
            elif command == "create":
                file_text = parameters.get("file_text")
                if not file_text:
                    return ToolResult(
                        success=False,
                        error="Missing required parameter 'file_text' for create command"
                    )
                return await self._create_file(instance_id, path, file_text)
            elif command == "str_replace":
                old_str = parameters.get("old_str")
                new_str = parameters.get("new_str", "")
                if not old_str:
                    return ToolResult(
                        success=False,
                        error="Missing required parameter 'old_str' for str_replace command"
                    )
                return await self._str_replace(instance_id, path, old_str, new_str)
            elif command == "insert":
                insert_line = parameters.get("insert_line")
                new_str = parameters.get("new_str")
                if insert_line is None or not new_str:
                    return ToolResult(
                        success=False,
                        error="Missing required parameters 'insert_line' and 'new_str' for insert command"
                    )
                return await self._insert_line(instance_id, path, insert_line, new_str)
            elif command == "undo_edit":
                return await self._undo_edit(instance_id, path)
            else:
                return ToolResult(
                    success=False,
                    error=f"Unknown command: {command}"
                )
                
        except Exception as e:
            logger.error(f"String replace editor tool execution failed: {e}")
            return ToolResult(
                success=False,
                error=f"Editor tool error: {str(e)}"
            )
    
    async def _view_file(self, path: str, view_range: Optional[List[int]] = None) -> ToolResult:
        """View a file or directory."""
        try:
            path_obj = Path(path)
            
            # Check if path exists
            if not path_obj.exists():
                return ToolResult(
                    success=False,
                    error=f"Path does not exist: {path}"
                )
            
            # Handle directory viewing
            if path_obj.is_dir():
                return await self._view_directory(path_obj)
            
            # Handle file viewing
            if path_obj.is_file():
                # Check file size
                if path_obj.stat().st_size > self.max_file_size:
                    return ToolResult(
                        success=False,
                        error=f"File too large (>{self.max_file_size} bytes): {path}"
                    )
                
                # Read file content
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        lines = f.readlines()
                except UnicodeDecodeError:
                    # Try with different encoding
                    with open(path, 'r', encoding='latin-1') as f:
                        lines = f.readlines()
                
                # Apply view range if specified
                if view_range:
                    start = view_range[0] - 1 if view_range[0] > 0 else 0
                    end = view_range[1] if len(view_range) > 1 and view_range[1] != -1 else len(lines)
                    lines = lines[start:end]
                    line_offset = start
                else:
                    line_offset = 0
                
                # Format with line numbers
                result = ""
                for i, line in enumerate(lines, start=line_offset + 1):
                    result += f"{i:6d}→{line}"
                
                # Truncate if needed
                if len(result) > self.max_output_length:
                    result = result[:self.max_output_length] + "\n<response clipped>"
                
                return ToolResult(
                    success=True,
                    result=result
                )
            
            return ToolResult(
                success=False,
                error=f"Path is neither a file nor a directory: {path}"
            )
            
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"Error viewing {path}: {str(e)}"
            )
    
    async def _view_directory(self, path_obj: Path) -> ToolResult:
        """View directory contents up to 2 levels deep."""
        try:
            result = []
            
            # List directory contents
            for root, dirs, files in os.walk(path_obj):
                level = root.replace(str(path_obj), '').count(os.sep)
                if level >= 2:
                    dirs[:] = []  # Don't recurse deeper
                    continue
                
                indent = ' ' * 2 * level
                result.append(f"{indent}{os.path.basename(root)}/")
                
                sub_indent = ' ' * 2 * (level + 1)
                for file in sorted(files):
                    if not file.startswith('.'):  # Skip hidden files
                        result.append(f"{sub_indent}{file}")
                
                for dir_name in sorted(dirs):
                    if not dir_name.startswith('.'):  # Skip hidden directories
                        result.append(f"{sub_indent}{dir_name}/")
            
            output = '\n'.join(result)
            
            # Truncate if needed
            if len(output) > self.max_output_length:
                output = output[:self.max_output_length] + "\n<response clipped>"
            
            return ToolResult(
                success=True,
                result=output
            )
            
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"Error viewing directory: {str(e)}"
            )
    
    async def _create_file(self, instance_id: str, path: str, file_text: str) -> ToolResult:
        """Create a new file."""
        try:
            path_obj = Path(path)
            
            # Check if file already exists
            if path_obj.exists():
                return ToolResult(
                    success=False,
                    error=f"File already exists: {path}"
                )
            
            # Check if parent directory exists
            if not path_obj.parent.exists():
                return ToolResult(
                    success=False,
                    error=f"Parent directory does not exist: {path_obj.parent}. "
                          "Please create it first using `mkdir -p`"
                )
            
            # Write file
            with open(path, 'w', encoding='utf-8') as f:
                f.write(file_text)
            
            # Store in history
            if path not in self.file_history[instance_id]:
                self.file_history[instance_id][path] = []
            self.file_history[instance_id][path].append(("create", None, file_text))
            
            # Show created file with line numbers
            lines = file_text.splitlines()
            result = f"File created at {path}\n"
            for i, line in enumerate(lines[:20], start=1):  # Show first 20 lines
                result += f"{i:6d}→{line}\n"
            
            if len(lines) > 20:
                result += f"... ({len(lines) - 20} more lines)"
            
            return ToolResult(
                success=True,
                result=result
            )
            
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"Error creating file: {str(e)}"
            )
    
    async def _str_replace(self, instance_id: str, path: str, old_str: str, new_str: str) -> ToolResult:
        """Replace string in file."""
        try:
            path_obj = Path(path)
            
            # Check if file exists
            if not path_obj.exists():
                return ToolResult(
                    success=False,
                    error=f"File does not exist: {path}"
                )
            
            # Read file content
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Check if old_str exists in file
            if old_str not in content:
                return ToolResult(
                    success=False,
                    error=f"String not found in file: {old_str[:100]}..."
                )
            
            # Check if old_str is unique
            if content.count(old_str) > 1:
                return ToolResult(
                    success=False,
                    error=f"String appears {content.count(old_str)} times in file. "
                          "Please provide more context to make it unique."
                )
            
            # Store original content in history
            if path not in self.file_history[instance_id]:
                self.file_history[instance_id][path] = []
            self.file_history[instance_id][path].append(("str_replace", content, None))
            
            # Perform replacement
            new_content = content.replace(old_str, new_str)
            
            # Write back to file
            with open(path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            
            # Show the change with context
            lines = new_content.splitlines()
            
            # Find the line where change occurred
            old_lines = content.splitlines()
            change_line = -1
            for i, (old_line, new_line) in enumerate(zip(old_lines, lines)):
                if old_line != new_line:
                    change_line = i
                    break
            
            if change_line == -1 and len(lines) != len(old_lines):
                change_line = min(len(lines), len(old_lines)) - 1
            
            # Show context around change
            start = max(0, change_line - 2)
            end = min(len(lines), change_line + 3)
            
            result = f"String replaced in {path}\n"
            for i in range(start, end):
                if i < len(lines):
                    result += f"{i+1:6d}→{lines[i]}\n"
            
            return ToolResult(
                success=True,
                result=result
            )
            
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"Error replacing string: {str(e)}"
            )
    
    async def _insert_line(self, instance_id: str, path: str, insert_line: int, new_str: str) -> ToolResult:
        """Insert text after a specific line."""
        try:
            path_obj = Path(path)
            
            # Check if file exists
            if not path_obj.exists():
                return ToolResult(
                    success=False,
                    error=f"File does not exist: {path}"
                )
            
            # Read file content
            with open(path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            # Validate insert_line
            if insert_line < 0 or insert_line > len(lines):
                return ToolResult(
                    success=False,
                    error=f"Invalid insert_line: {insert_line}. File has {len(lines)} lines."
                )
            
            # Store original content in history
            if path not in self.file_history[instance_id]:
                self.file_history[instance_id][path] = []
            self.file_history[instance_id][path].append(("insert", ''.join(lines), None))
            
            # Insert new text
            if not new_str.endswith('\n'):
                new_str += '\n'
            
            lines.insert(insert_line, new_str)
            
            # Write back to file
            with open(path, 'w', encoding='utf-8') as f:
                f.writelines(lines)
            
            # Show context around insertion
            start = max(0, insert_line - 2)
            end = min(len(lines), insert_line + 3)
            
            result = f"Text inserted in {path} after line {insert_line}\n"
            for i in range(start, end):
                result += f"{i+1:6d}→{lines[i]}"
            
            return ToolResult(
                success=True,
                result=result
            )
            
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"Error inserting text: {str(e)}"
            )
    
    async def _undo_edit(self, instance_id: str, path: str) -> ToolResult:
        """Undo the last edit to a file."""
        try:
            # Check if we have history for this file
            if instance_id not in self.file_history or path not in self.file_history[instance_id]:
                return ToolResult(
                    success=False,
                    error=f"No edit history for {path}"
                )
            
            history = self.file_history[instance_id][path]
            if not history:
                return ToolResult(
                    success=False,
                    error=f"No edits to undo for {path}"
                )
            
            # Get last edit
            last_edit = history.pop()
            operation, old_content, _ = last_edit
            
            if operation == "create":
                # Delete the file
                if Path(path).exists():
                    os.remove(path)
                return ToolResult(
                    success=True,
                    result=f"File creation undone, file deleted: {path}"
                )
            else:
                # Restore old content
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(old_content)
                
                return ToolResult(
                    success=True,
                    result=f"Last edit undone for {path}"
                )
                
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"Error undoing edit: {str(e)}"
            )
    
    async def calculate_reward(self, instance_id: str, **kwargs) -> float:
        """
        Calculate reward for editor operations.
        
        Args:
            instance_id: Tool instance ID
            **kwargs: Additional arguments
            
        Returns:
            Reward score (0.0 to 1.0)
        """
        # Simple reward based on successful execution
        last_result = kwargs.get("last_result")
        if last_result and last_result.get("success"):
            return 1.0
        return 0.0


__all__ = ['OHStrReplaceEditorTool']
# Extracted from Claude-Code: src/tools/MemoryReadTool/MemoryReadTool.tsx
# Note: Original prompt file was not found, content inferred from tool functionality

DESCRIPTION = 'Read memory files to access persistent information across conversations.'

PROMPT = """Read memory files from the memory directory. These files store persistent information that can be accessed across different conversations and sessions. If no file_path is specified, it will list all available memory files. Use this tool to:

- Access previously stored information
- Retrieve context from past conversations
- Read user preferences and settings
- Access project-specific information

The file_path parameter is optional. If provided, it should be relative to the memory directory."""




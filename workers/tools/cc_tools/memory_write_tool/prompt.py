# Extracted from Claude-Code: src/tools/MemoryWriteTool/MemoryWriteTool.tsx
# Note: Original prompt file was not found, content inferred from tool functionality

DESCRIPTION = 'Write memory files to store persistent information across conversations.'

PROMPT = """Write content to memory files in the memory directory. These files store persistent information that can be accessed across different conversations and sessions. Use this tool to:

- Store important information for future reference
- Save user preferences and settings
- Record project-specific context
- Keep track of conversation history

The file_path parameter specifies the memory file to write to, and content contains the information to store."""



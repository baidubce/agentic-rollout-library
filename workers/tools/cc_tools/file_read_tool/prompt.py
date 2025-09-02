# Extracted from Claude-Code: src/tools/FileReadTool/prompt.ts

MAX_LINES_TO_READ = 2000
MAX_LINE_LENGTH = 2000

DESCRIPTION = 'Read a file from the local filesystem.'

PROMPT = f"""Reads a file from the local filesystem. The file_path parameter must be an absolute path, not a relative path. By default, it reads up to {MAX_LINES_TO_READ} lines starting from the beginning of the file. You can optionally specify a line offset and limit (especially handy for long files), but it's recommended to read the whole file by not providing these parameters. Any lines longer than {MAX_LINE_LENGTH} characters will be truncated. For image files, the tool will display the image for you. For Jupyter notebooks (.ipynb files), use the NotebookReadTool instead."""



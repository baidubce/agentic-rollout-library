# Extracted from Claude-Code: src/tools/AgentTool/prompt.ts

DESCRIPTION = 'Launch a new agent that has access to specific tools for complex search and analysis tasks.'

def get_prompt(dangerous_skip_permissions: bool = False) -> str:
    """Generate the agent tool prompt based on permissions."""
    
    # Available tools for the agent (simplified for our implementation)
    tool_names = "FileReadTool, GlobTool, GrepTool, LSTool, ThinkTool"
    
    prompt = f"""Launch a new agent that has access to the following tools: {tool_names}. When you are searching for a keyword or file and are not confident that you will find the right match on the first try, use the Agent tool to perform the search for you. For example:

- If you are searching for a keyword like "config" or "logger", the Agent tool is appropriate
- If you want to read a specific file path, use the FileReadTool or GlobTool tool instead of the Agent tool, to find the match more quickly
- If you are searching for a specific class definition like "class Foo", use the GlobTool tool instead, to find the match more quickly

Usage notes:
1. Launch multiple agents concurrently whenever possible, to maximize performance; to do that, use a single message with multiple tool uses
2. When the agent is done, it will return a single message back to you. The result returned by the agent is not visible to the user. To show the user the result, you should send a text message back to the user with a concise summary of the result.
3. Each agent invocation is stateless. You will not be able to send additional messages to the agent, nor will the agent be able to communicate with you outside of its final report. Therefore, your prompt should contain a highly detailed task description for the agent to perform autonomously and you should specify exactly what information the agent should return back to you in its final and only message to you.
4. The agent's outputs should generally be trusted"""
    
    if not dangerous_skip_permissions:
        prompt += """
5. IMPORTANT: The agent can not use BashTool, FileWriteTool, FileEditTool, NotebookEditTool, so can not modify files. If you want to use these tools, use them directly instead of going through the agent."""
    
    return prompt

# Default prompt for static usage
PROMPT = get_prompt(False)



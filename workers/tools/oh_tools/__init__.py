#!/usr/bin/env python3
"""
OpenHands-inspired tools for the agentic rollout library.

This module contains tools adapted from OpenHands' CodeAct agent implementation,
providing command execution, file editing, thinking, and task completion capabilities.
"""

from .bash import OHBashTool
from .str_replace_editor import OHStrReplaceEditorTool
from .think import OHThinkTool
from .finish import OHFinishTool

__all__ = [
    'OHBashTool',
    'OHStrReplaceEditorTool',
    'OHThinkTool',
    'OHFinishTool',
]

# Tool registry for easy access
OH_TOOLS = {
    'bash': OHBashTool,
    'str_replace_editor': OHStrReplaceEditorTool,
    'think': OHThinkTool,
    'finish': OHFinishTool,
}


def get_oh_tool(tool_name: str, config: dict = None):
    """
    Get an OpenHands tool by name.
    
    Args:
        tool_name: Name of the tool ('bash', 'str_replace_editor', 'think', 'finish')
        config: Optional configuration dictionary for the tool
    
    Returns:
        Tool instance
    
    Raises:
        ValueError: If tool_name is not recognized
    """
    if tool_name not in OH_TOOLS:
        raise ValueError(f"Unknown OH tool: {tool_name}. Available tools: {list(OH_TOOLS.keys())}")
    
    tool_class = OH_TOOLS[tool_name]
    return tool_class(config=config)


def get_all_oh_tools(config: dict = None):
    """
    Get all OpenHands tools.
    
    Args:
        config: Optional configuration dictionary for the tools
    
    Returns:
        Dictionary of tool_name -> tool_instance
    """
    return {
        name: tool_class(config=config)
        for name, tool_class in OH_TOOLS.items()
    }
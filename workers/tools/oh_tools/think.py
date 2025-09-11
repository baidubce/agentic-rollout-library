#!/usr/bin/env python3
"""
Think tool adapted from OpenHands for the agentic rollout framework.
Provides a tool for agents to log their thought processes and reasoning.
"""

import logging
from typing import Any, Dict, List, Optional

from ...core.base_tool import BaseAgenticTool
from ...core.tool_schemas import OpenAIFunctionToolSchema, ToolResult, create_openai_tool_schema

logger = logging.getLogger(__name__)


_THINK_DESCRIPTION = """Use the tool to think about something. It will not obtain new information or make any changes to the repository, but just log the thought. Use it when complex reasoning or brainstorming is needed.

Common use cases:
1. When exploring a repository and discovering the source of a bug, call this tool to brainstorm several unique ways of fixing the bug, and assess which change(s) are likely to be simplest and most effective.
2. After receiving test results, use this tool to brainstorm ways to fix failing tests.
3. When planning a complex refactoring, use this tool to outline different approaches and their tradeoffs.
4. When designing a new feature, use this tool to think through architecture decisions and implementation details.
5. When debugging a complex issue, use this tool to organize your thoughts and hypotheses.

The tool simply logs your thought process for better transparency and does not execute any code or make changes."""


class OHThinkTool(BaseAgenticTool):
    """
    Think tool adapted from OpenHands.
    Allows agents to log their thought processes and reasoning.
    """
    
    def __init__(self, config: Optional[Dict] = None):
        """Initialize the think tool."""
        config = config or {}
        super().__init__(config)
        
        # Store thoughts for each instance
        self.thoughts_history = {}  # instance_id -> [thoughts]
        self.max_thought_length = config.get("max_thought_length", 5000)
    
    def get_openai_tool_schema(self) -> OpenAIFunctionToolSchema:
        """Return the OpenAI function schema for this tool."""
        return create_openai_tool_schema(
            name="think",
            description=_THINK_DESCRIPTION,
            parameters={
                "thought": {
                    "type": "string",
                    "description": "The thought to log."
                }
            },
            required=["thought"]
        )
    
    async def execute_tool(self, instance_id: str, parameters: Dict[str, Any], **kwargs) -> ToolResult:
        """
        Execute the think tool.
        
        Args:
            instance_id: Tool instance ID
            parameters: Tool parameters containing 'thought'
            **kwargs: Additional execution arguments
            
        Returns:
            ToolResult indicating successful logging
        """
        try:
            thought = parameters.get("thought", "")
            
            if not thought:
                return ToolResult(
                    success=False,
                    error="No thought provided to log"
                )
            
            # Truncate if too long
            if len(thought) > self.max_thought_length:
                thought = thought[:self.max_thought_length] + "... (truncated)"
            
            # Initialize history for this instance if needed
            if instance_id not in self.thoughts_history:
                self.thoughts_history[instance_id] = []
            
            # Store the thought
            self.thoughts_history[instance_id].append({
                "thought": thought,
                "timestamp": kwargs.get("timestamp", None)
            })
            
            # Log the thought
            logger.info(f"[Think Tool - Instance {instance_id}] {thought}")
            
            # Create result
            result_message = "Thought logged successfully."
            
            # Include thought count if configured
            if self.config.get("include_thought_count", False):
                thought_count = len(self.thoughts_history[instance_id])
                result_message += f" (Thought #{thought_count})"
            
            return ToolResult(
                success=True,
                result=result_message
            )
            
        except Exception as e:
            logger.error(f"Think tool execution failed: {e}")
            return ToolResult(
                success=False,
                error=f"Failed to log thought: {str(e)}"
            )
    
    async def get_thoughts_for_instance(self, instance_id: str) -> List[Dict[str, Any]]:
        """
        Get all thoughts for a specific instance.
        
        Args:
            instance_id: Tool instance ID
            
        Returns:
            List of thought entries
        """
        return self.thoughts_history.get(instance_id, [])
    
    async def clear_thoughts(self, instance_id: str) -> bool:
        """
        Clear thoughts for a specific instance.
        
        Args:
            instance_id: Tool instance ID
            
        Returns:
            True if thoughts were cleared
        """
        if instance_id in self.thoughts_history:
            self.thoughts_history[instance_id] = []
            return True
        return False
    
    async def calculate_reward(self, instance_id: str, **kwargs) -> float:
        """
        Calculate reward for thinking.
        
        Since thinking is always successful if a thought is provided,
        this returns 1.0 for any valid thought.
        
        Args:
            instance_id: Tool instance ID
            **kwargs: Additional arguments
            
        Returns:
            Reward score (1.0 for successful thinking)
        """
        return 1.0


__all__ = ['OHThinkTool']
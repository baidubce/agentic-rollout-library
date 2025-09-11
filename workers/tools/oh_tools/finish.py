#!/usr/bin/env python3
"""
Finish tool adapted from OpenHands for the agentic rollout framework.
Signals task completion and provides final results.
"""

import logging
from typing import Any, Dict, Optional

from ...core.base_tool import BaseAgenticTool
from ...core.tool_schemas import OpenAIFunctionToolSchema, ToolResult, create_openai_tool_schema

logger = logging.getLogger(__name__)


_FINISH_DESCRIPTION = """Signals the completion of the current task or conversation.

CRITICAL: Use this tool ONLY when you have verified complete implementation:

Required verification checklist before using this tool:
- ALL pages/components mentioned in user requirements are implemented
- ALL features requested by the user are functional
- ALL navigation links work and connect to existing pages

Use this tool when:
- You have successfully completed EVERY aspect of the user's requested task
- You have verified that all requirements are 100% implemented
- The application is fully functional from start to end
- You cannot proceed further due to technical limitations or missing information

The message should include:
- A clear summary of actions taken and their results
- Explanation if you're unable to complete the task
- Confirmation that every requirement has been fulfilled
- Any next steps or usage instructions for the user
- Any follow-up questions if more information is needed

The task_completed field should be set to 'true' if you believed you have completed the task, 'false' if you cannot proceed, and 'partial' if only some requirements were met."""


class OHFinishTool(BaseAgenticTool):
    """
    Finish tool adapted from OpenHands.
    Signals task completion and provides final results.
    """
    
    def __init__(self, config: Optional[Dict] = None):
        """Initialize the finish tool."""
        config = config or {}
        super().__init__(config)
        
        # Track completions for each instance
        self.completions = {}  # instance_id -> completion info
    
    def get_openai_tool_schema(self) -> OpenAIFunctionToolSchema:
        """Return the OpenAI function schema for this tool."""
        return create_openai_tool_schema(
            name="finish",
            description=_FINISH_DESCRIPTION,
            parameters={
                "message": {
                    "type": "string",
                    "description": "The final message should be shown to beginner-level users. "
                                 "Do not include any details about code, programming, architecture, or environment. "
                                 "The description should be simple and easy to understand, making it convenient for users to grasp."
                },
                "commit_message": {
                    "type": "string",
                    "description": "Descriptive commit message for git, use the SAME language as query"
                },
                "task_completed": {
                    "type": "string",
                    "enum": ["true", "false", "partial"],
                    "description": "Whether you have completed the task."
                }
            },
            required=["message", "commit_message", "task_completed"]
        )
    
    async def execute_tool(self, instance_id: str, parameters: Dict[str, Any], **kwargs) -> ToolResult:
        """
        Execute the finish tool.
        
        Args:
            instance_id: Tool instance ID
            parameters: Tool parameters containing 'message', 'commit_message', and 'task_completed'
            **kwargs: Additional execution arguments
            
        Returns:
            ToolResult indicating successful completion
        """
        try:
            message = parameters.get("message", "")
            commit_message = parameters.get("commit_message", "")
            task_completed = parameters.get("task_completed", "false")
            
            # Validate required parameters
            if not message:
                return ToolResult(
                    success=False,
                    error="Missing required parameter 'message'"
                )
            
            if not commit_message:
                return ToolResult(
                    success=False,
                    error="Missing required parameter 'commit_message'"
                )
            
            if task_completed not in ["true", "false", "partial"]:
                return ToolResult(
                    success=False,
                    error=f"Invalid task_completed value: {task_completed}. Must be 'true', 'false', or 'partial'"
                )
            
            # Store completion info
            completion_info = {
                "message": message,
                "commit_message": commit_message,
                "task_completed": task_completed,
                "timestamp": kwargs.get("timestamp", None)
            }
            self.completions[instance_id] = completion_info
            
            # Log the completion
            logger.info(f"[Finish Tool - Instance {instance_id}] Task completed: {task_completed}")
            logger.info(f"Message: {message}")
            logger.info(f"Commit message: {commit_message}")
            
            # Create result based on completion status
            if task_completed == "true":
                result_status = "Task successfully completed"
            elif task_completed == "partial":
                result_status = "Task partially completed"
            else:
                result_status = "Task could not be completed"
            
            # Build result data
            result_data = {
                "status": result_status,
                "message": message,
                "commit_message": commit_message,
                "task_completed": task_completed,
                "completed": True  # Indicates the finish tool was executed
            }
            
            return ToolResult(
                success=True,
                result=result_data
            )
            
        except Exception as e:
            logger.error(f"Finish tool execution failed: {e}")
            return ToolResult(
                success=False,
                error=f"Failed to complete task: {str(e)}"
            )
    
    async def get_completion_info(self, instance_id: str) -> Optional[Dict[str, Any]]:
        """
        Get completion information for a specific instance.
        
        Args:
            instance_id: Tool instance ID
            
        Returns:
            Completion information or None if not completed
        """
        return self.completions.get(instance_id)
    
    async def calculate_reward(self, instance_id: str, **kwargs) -> float:
        """
        Calculate reward for task completion.
        
        Args:
            instance_id: Tool instance ID
            **kwargs: Additional arguments
            
        Returns:
            Reward score based on completion status
        """
        completion_info = self.completions.get(instance_id)
        if not completion_info:
            return 0.0
        
        task_completed = completion_info.get("task_completed", "false")
        
        # Reward based on completion status
        if task_completed == "true":
            return 1.0
        elif task_completed == "partial":
            return 0.5
        else:
            return 0.2  # Small reward for proper termination even if task failed


__all__ = ['OHFinishTool']
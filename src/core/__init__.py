"""
Agentic Rollout Library - Core Components

A modular node-based library for building agent execution flows.
"""

from .base_node import BaseNode
from .context_engineering_node import ContextEngineeringNode, Message
from .llm_node import (
    LLMNode
)
from .tool_parsing_node import (
    ToolParsingNode,
    create_json_only_parser,
    create_xml_parser,
    create_structured_parser
)
from .tool_execution_node import ToolExecutionNode
from .timeline import TimelineManager, get_timeline

try:
    from .k8s_tool_execution_node import K8SToolExecutionNode
    K8S_AVAILABLE = True
except ImportError:
    K8SToolExecutionNode = None
    K8S_AVAILABLE = False

__version__ = "0.1.0"

__all__ = [
    # Base class
    "BaseNode",

    # Core nodes
    "ContextEngineeringNode",
    "LLMNode",
    "ToolParsingNode",
    "ToolExecutionNode",
    "K8SToolExecutionNode",

    # Supporting classes
    "Message",

    # K8S availability flag
    "K8S_AVAILABLE",

    # Factory functions for parsers
    "create_json_only_parser",
    "create_xml_parser",
    "create_structured_parser",

    # Timeline tracking
    "TimelineManager",
    "get_timeline",
]
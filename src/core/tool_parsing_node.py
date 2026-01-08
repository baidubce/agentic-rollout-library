"""Tool Parsing Node implementation."""

import json
import re
import asyncio
from typing import Any, Callable, Dict, List, Optional
from .base_node import BaseNode


class ToolParsingNode(BaseNode):
    """Node for parsing tool calls from LLM responses."""

    def __init__(self,
                 name: str = None,
                 parse_function: Callable = None,
                 timeline_enabled: bool = False,
                 timeout: float = None):
        """
        Initialize the Tool Parsing Node.

        Args:
            name: Optional name for the node
            parse_function: Custom parsing function
            timeline_enabled: Enable automatic timeline tracking for this node
            timeout: Timeout in seconds for parsing execution (None = no timeout)
        """
        super().__init__(name, timeline_enabled=timeline_enabled, timeout=timeout)
        self.parse_function = parse_function or self._default_parser
        self.last_parsed_tools = []

    def set_parse_function(self, parse_function: Callable) -> None:
        """
        Set or update the parsing function.

        Args:
            parse_function: Function that takes Dict and returns List[Dict]
        """
        self.parse_function = parse_function
        self.logger.info("Updated parse function")

    async def process_async(self, input_data: Dict) -> List[Dict]:
        """
        Parse tool calls from LLM response asynchronously.

        Args:
            input_data: LLM response dictionary

        Returns:
            List of parsed tool call dictionaries
        """
        if not self.validate_input(input_data):
            raise ValueError("Invalid input format for Tool Parsing Node")

        try:
            if asyncio.iscoroutinefunction(self.parse_function):
                parsed_tools = await self.parse_function(input_data)
            else:
                parsed_tools = self.parse_function(input_data)

            if not isinstance(parsed_tools, list):
                parsed_tools = [parsed_tools] if parsed_tools else []

            validated_tools = []
            for tool in parsed_tools:
                if self._validate_tool_call(tool):
                    validated_tools.append(tool)
                else:
                    self.logger.warning(f"Invalid tool call format: {tool}")

            self.last_parsed_tools = validated_tools
            self.logger.info(f"Parsed {len(validated_tools)} tool calls")

            return validated_tools

        except Exception as e:
            self.logger.error(f"Error parsing tools: {str(e)}")
            return []

    def process(self, input_data: Dict) -> List[Dict]:
        """
        Parse tool calls from LLM response.

        Args:
            input_data: LLM response dictionary

        Returns:
            List of parsed tool call dictionaries
        """
        if not self.validate_input(input_data):
            raise ValueError("Invalid input format for Tool Parsing Node")

        try:
            # Use custom or default parser
            parsed_tools = self.parse_function(input_data)

            # Ensure output is in expected format
            if not isinstance(parsed_tools, list):
                parsed_tools = [parsed_tools] if parsed_tools else []

            # Validate each tool call
            validated_tools = []
            for tool in parsed_tools:
                if self._validate_tool_call(tool):
                    validated_tools.append(tool)
                else:
                    self.logger.warning(f"Invalid tool call format: {tool}")

            self.last_parsed_tools = validated_tools
            self.logger.info(f"Parsed {len(validated_tools)} tool calls")

            return validated_tools

        except Exception as e:
            self.logger.error(f"Error parsing tools: {str(e)}")
            return []

    def _default_parser(self, llm_response: Dict) -> List[Dict]:
        """
        Default parser for extracting tool calls from LLM response.

        Supports multiple formats:
        1. JSON blocks in content
        2. Function call format
        3. Tool use format

        Args:
            llm_response: LLM response dictionary

        Returns:
            List of parsed tool calls
        """
        content = llm_response.get("content", "")
        tools = []

        # Try to parse JSON blocks from content
        json_pattern = r'```json\s*(.*?)\s*```'
        json_matches = re.findall(json_pattern, content, re.DOTALL)

        for match in json_matches:
            try:
                parsed = json.loads(match)
                if isinstance(parsed, dict):
                    tools.append(parsed)
                elif isinstance(parsed, list):
                    tools.extend(parsed)
            except json.JSONDecodeError:
                self.logger.debug(f"Failed to parse JSON block: {match[:100]}...")

        # Try to parse tool/function format
        if not tools:
            # Look for tool use patterns
            tool_pattern = r'Tool:\s*(\w+)\s*Arguments:\s*({.*?})'
            tool_matches = re.findall(tool_pattern, content, re.DOTALL)

            for name, args in tool_matches:
                try:
                    arguments = json.loads(args)
                    tools.append({
                        "tool": name,
                        "arguments": arguments
                    })
                except json.JSONDecodeError:
                    self.logger.debug(f"Failed to parse tool arguments: {args[:100]}...")

        # Try to parse function call format
        if not tools:
            function_pattern = r'<function>(\w+)</function>\s*<parameters>(.*?)</parameters>'
            function_matches = re.findall(function_pattern, content, re.DOTALL)

            for name, params in function_matches:
                try:
                    parameters = json.loads(params) if params else {}
                    tools.append({
                        "function": name,
                        "parameters": parameters
                    })
                except json.JSONDecodeError:
                    self.logger.debug(f"Failed to parse function parameters: {params[:100]}...")

        # Check if response has tool_calls or function_call field
        if "tool_calls" in llm_response:
            tool_calls = llm_response["tool_calls"]
            if isinstance(tool_calls, list):
                tools.extend(tool_calls)

        if "function_call" in llm_response:
            function_call = llm_response["function_call"]
            if isinstance(function_call, dict):
                tools.append(function_call)

        return tools

    def _validate_tool_call(self, tool_call: Dict) -> bool:
        """
        Validate a single tool call dictionary.

        Args:
            tool_call: Tool call to validate

        Returns:
            True if valid, False otherwise
        """
        if not isinstance(tool_call, dict):
            return False

        # Check for common tool call formats
        has_tool = "tool" in tool_call or "function" in tool_call or "name" in tool_call
        has_args = "arguments" in tool_call or "parameters" in tool_call or "args" in tool_call

        return has_tool or has_args

    def validate_input(self, input_data: Any) -> bool:
        """
        Validate input is a dictionary with content.

        Args:
            input_data: Input to validate

        Returns:
            True if valid, False otherwise
        """
        return isinstance(input_data, dict) and ("content" in input_data or "tool_calls" in input_data or "function_call" in input_data)

    def validate_output(self, output_data: Any) -> bool:
        """
        Validate output is a list of dictionaries.

        Args:
            output_data: Output to validate

        Returns:
            True if valid, False otherwise
        """
        if not isinstance(output_data, list):
            return False

        return all(isinstance(item, dict) for item in output_data)

    def get_last_parsed_tools(self) -> List[Dict]:
        """
        Get the last parsed tool calls.

        Returns:
            List of last parsed tool calls
        """
        return self.last_parsed_tools.copy()

    def reset(self) -> None:
        """Reset the node to initial state."""
        super().reset()
        self.last_parsed_tools = []


# Example custom parsers

def create_json_only_parser():
    """
    Create a parser that only extracts JSON blocks.

    Returns:
        Parser function
    """
    def json_parser(llm_response: Dict) -> List[Dict]:
        content = llm_response.get("content", "")
        tools = []

        # Find all JSON blocks
        json_pattern = r'```json\s*(.*?)\s*```'
        json_matches = re.findall(json_pattern, content, re.DOTALL)

        for match in json_matches:
            try:
                parsed = json.loads(match)
                if isinstance(parsed, dict):
                    tools.append(parsed)
                elif isinstance(parsed, list):
                    tools.extend(parsed)
            except json.JSONDecodeError:
                continue

        return tools

    return json_parser


def create_xml_parser():
    """
    Create a parser for XML-style tool calls.

    Returns:
        Parser function
    """
    def xml_parser(llm_response: Dict) -> List[Dict]:
        content = llm_response.get("content", "")
        tools = []

        # Parse XML-style tool calls
        tool_pattern = r'<tool name="(\w+)">(.*?)</tool>'
        tool_matches = re.findall(tool_pattern, content, re.DOTALL)

        for name, params in tool_matches:
            try:
                # Try to parse params as JSON
                parameters = json.loads(params) if params else {}
            except json.JSONDecodeError:
                # Fallback to key-value parsing
                parameters = {}
                param_pattern = r'<(\w+)>(.*?)</\1>'
                param_matches = re.findall(param_pattern, params)
                for key, value in param_matches:
                    parameters[key] = value

            tools.append({
                "tool": name,
                "parameters": parameters
            })

        return tools

    return xml_parser


def create_structured_parser(tool_format: str = "openai"):
    """
    Create a parser for specific structured formats.

    Args:
        tool_format: Format type ("openai", "anthropic", "langchain")

    Returns:
        Parser function
    """
    def structured_parser(llm_response: Dict) -> List[Dict]:
        tools = []

        if tool_format == "openai":
            # OpenAI function calling format
            if "function_call" in llm_response:
                fc = llm_response["function_call"]
                tools.append({
                    "function": fc.get("name"),
                    "arguments": json.loads(fc.get("arguments", "{}"))
                })
            elif "tool_calls" in llm_response:
                for tc in llm_response.get("tool_calls", []):
                    if "function" in tc:
                        tools.append({
                            "function": tc["function"].get("name"),
                            "arguments": json.loads(tc["function"].get("arguments", "{}"))
                        })

        elif tool_format == "anthropic":
            # Anthropic tool use format
            if "tool_use" in llm_response:
                for tu in llm_response.get("tool_use", []):
                    tools.append({
                        "tool": tu.get("name"),
                        "parameters": tu.get("input", {})
                    })

        elif tool_format == "langchain":
            # LangChain agent format
            content = llm_response.get("content", "")
            action_pattern = r'Action:\s*(\w+)\s*Action Input:\s*({.*?})'
            action_matches = re.findall(action_pattern, content, re.DOTALL)

            for action, input_str in action_matches:
                try:
                    action_input = json.loads(input_str)
                    tools.append({
                        "action": action,
                        "input": action_input
                    })
                except json.JSONDecodeError:
                    tools.append({
                        "action": action,
                        "input": input_str
                    })

        return tools

    return structured_parser
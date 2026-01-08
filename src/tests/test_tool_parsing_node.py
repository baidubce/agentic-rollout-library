"""
Test cases for Tool Parsing Node.

Tests tool parsing functionality including JSON, XML, and custom parsers.
"""

import pytest
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.tool_parsing_node import (
    ToolParsingNode,
    create_json_only_parser,
    create_xml_parser,
    create_structured_parser
)


class TestToolParsingNode:
    """Test Tool Parsing Node functionality."""

    def test_init_with_defaults(self):
        """Test initialization with default parser."""
        node = ToolParsingNode(name="TestParser")

        assert node.name == "TestParser"
        assert node.parse_function is not None
        assert node.last_parsed_tools == []

    def test_init_with_custom_parser(self):
        """Test initialization with custom parser."""
        def custom_parser(llm_response):
            return [{"tool": "custom", "args": {}}]

        node = ToolParsingNode(parse_function=custom_parser)
        assert node.parse_function == custom_parser

    def test_set_parse_function(self):
        """Test setting parse function."""
        node = ToolParsingNode()

        def new_parser(llm_response):
            return []

        node.set_parse_function(new_parser)
        assert node.parse_function == new_parser

    def test_validate_input_valid(self):
        """Test input validation with valid LLM response."""
        node = ToolParsingNode()

        # Dict with content
        assert node.validate_input({"content": "test"}) is True

        # Dict with tool_calls
        assert node.validate_input({"tool_calls": []}) is True

        # Dict with function_call
        assert node.validate_input({"function_call": {}}) is True

    def test_validate_input_invalid(self):
        """Test input validation with invalid input."""
        node = ToolParsingNode()

        assert node.validate_input("string") is False
        assert node.validate_input([]) is False
        assert node.validate_input({}) is False

    def test_validate_output(self):
        """Test output validation."""
        node = ToolParsingNode()

        # Valid: list of dicts
        assert node.validate_output([{"tool": "test"}]) is True
        assert node.validate_output([]) is True

        # Invalid
        assert node.validate_output("string") is False
        assert node.validate_output({"tool": "test"}) is False
        assert node.validate_output([1, 2, 3]) is False

    def test_parse_json_block(self):
        """Test parsing JSON code block."""
        node = ToolParsingNode()
        llm_response = {
            "content": """Here's the tool call:
```json
{"tool": "calculator", "arguments": {"x": 5, "y": 3}}
```"""
        }

        result = node.process(llm_response)

        assert len(result) == 1
        assert result[0]["tool"] == "calculator"
        assert result[0]["arguments"]["x"] == 5

    def test_parse_multiple_json_blocks(self):
        """Test parsing multiple JSON blocks."""
        node = ToolParsingNode()
        llm_response = {
            "content": """First tool:
```json
{"tool": "search", "args": {"query": "test"}}
```
Second tool:
```json
{"tool": "calculator", "args": {"x": 1}}
```"""
        }

        result = node.process(llm_response)

        assert len(result) == 2
        assert result[0]["tool"] == "search"
        assert result[1]["tool"] == "calculator"

    def test_parse_tool_use_pattern(self):
        """Test parsing Tool: ... Arguments: ... pattern."""
        node = ToolParsingNode()
        llm_response = {
            "content": 'Tool: search Arguments: {"query": "test", "limit": 10}'
        }

        result = node.process(llm_response)

        assert len(result) == 1
        assert result[0]["tool"] == "search"
        assert result[0]["arguments"]["query"] == "test"

    def test_parse_function_xml_pattern(self):
        """Test parsing XML function call pattern."""
        node = ToolParsingNode()
        llm_response = {
            "content": '<function>calculator</function> <parameters>{"x": 5}</parameters>'
        }

        result = node.process(llm_response)

        assert len(result) == 1
        assert result[0]["function"] == "calculator"
        assert result[0]["parameters"]["x"] == 5

    def test_parse_tool_calls_field(self):
        """Test parsing tool_calls field from response."""
        node = ToolParsingNode()
        llm_response = {
            "tool_calls": [
                {"function": {"name": "search", "arguments": '{"q": "test"}'}},
                {"function": {"name": "calc", "arguments": '{"x": 1}'}}
            ]
        }

        result = node.process(llm_response)

        assert len(result) >= 2

    def test_parse_function_call_field(self):
        """Test parsing function_call field from response."""
        node = ToolParsingNode()
        llm_response = {
            "function_call": {
                "name": "search",
                "arguments": '{"query": "test"}'
            }
        }

        result = node.process(llm_response)

        assert len(result) >= 1

    def test_parse_returns_empty_on_no_match(self):
        """Test parser returns empty list when no tool calls found."""
        node = ToolParsingNode()
        llm_response = {
            "content": "Just plain text without any tool calls"
        }

        result = node.process(llm_response)

        assert result == []

    def test_parse_validates_tool_calls(self):
        """Test that invalid tool calls are filtered out."""
        def bad_parser(llm_response):
            return [
                {"tool": "valid"},  # Valid
                {"invalid": "data"},  # Invalid - no tool/function/name
                {"tool": "also_valid", "args": {}}  # Valid
            ]

        node = ToolParsingNode(parse_function=bad_parser)
        result = node.process({"content": "test"})

        assert len(result) == 2
        assert result[0]["tool"] == "valid"
        assert result[1]["tool"] == "also_valid"

    @pytest.mark.asyncio
    async def test_process_async(self):
        """Test async processing."""
        async def async_parser(llm_response):
            await asyncio.sleep(0.01)
            return [{"tool": "async_result"}]

        node = ToolParsingNode(parse_function=async_parser)
        result = await node.process_async({"content": "test"})

        assert len(result) == 1
        assert result[0]["tool"] == "async_result"

    def test_get_last_parsed_tools(self):
        """Test getting last parsed tools."""
        node = ToolParsingNode()
        llm_response = {
            "content": '```json\n{"tool": "test"}\n```'
        }

        node.process(llm_response)
        last_tools = node.get_last_parsed_tools()

        assert len(last_tools) == 1
        assert last_tools[0]["tool"] == "test"

    def test_reset(self):
        """Test resetting node state."""
        node = ToolParsingNode()
        llm_response = {
            "content": '```json\n{"tool": "test"}\n```'
        }

        node.process(llm_response)
        assert len(node.last_parsed_tools) > 0

        node.reset()
        assert node.last_parsed_tools == []


class TestJSONOnlyParser:
    """Test JSON-only parser factory."""

    def test_parse_json_blocks(self):
        """Test parsing JSON blocks only."""
        parser = create_json_only_parser()
        llm_response = {
            "content": """
```json
{"tool": "test", "args": {"x": 1}}
```
Tool: invalid Arguments: {...}
```json
{"tool": "another"}
```"""
        }

        result = parser(llm_response)

        # Should only parse JSON blocks, not Tool: pattern
        assert len(result) == 2
        assert result[0]["tool"] == "test"
        assert result[1]["tool"] == "another"

    def test_parse_json_array(self):
        """Test parsing JSON array."""
        parser = create_json_only_parser()
        llm_response = {
            "content": '```json\n[{"tool": "a"}, {"tool": "b"}]\n```'
        }

        result = parser(llm_response)

        assert len(result) == 2


class TestXMLParser:
    """Test XML parser factory."""

    def test_parse_xml_tools(self):
        """Test parsing XML-style tool calls."""
        parser = create_xml_parser()
        llm_response = {
            "content": '<tool name="search">{"query": "test"}</tool>'
        }

        result = parser(llm_response)

        assert len(result) == 1
        assert result[0]["tool"] == "search"
        assert result[0]["parameters"]["query"] == "test"

    def test_parse_xml_with_nested_params(self):
        """Test parsing XML with nested parameter tags."""
        parser = create_xml_parser()
        llm_response = {
            "content": '<tool name="calc"><x>5</x><y>3</y></tool>'
        }

        result = parser(llm_response)

        assert len(result) == 1
        assert result[0]["tool"] == "calc"
        assert result[0]["parameters"]["x"] == "5"
        assert result[0]["parameters"]["y"] == "3"


class TestStructuredParser:
    """Test structured parser factory."""

    def test_parse_openai_function_call(self):
        """Test parsing OpenAI function call format."""
        parser = create_structured_parser("openai")
        llm_response = {
            "function_call": {
                "name": "search",
                "arguments": '{"query": "test"}'
            }
        }

        result = parser(llm_response)

        assert len(result) == 1
        assert result[0]["function"] == "search"
        assert result[0]["arguments"]["query"] == "test"

    def test_parse_openai_tool_calls(self):
        """Test parsing OpenAI tool_calls format."""
        parser = create_structured_parser("openai")
        llm_response = {
            "tool_calls": [
                {
                    "function": {
                        "name": "calc",
                        "arguments": '{"x": 5}'
                    }
                }
            ]
        }

        result = parser(llm_response)

        assert len(result) == 1
        assert result[0]["function"] == "calc"

    def test_parse_langchain_format(self):
        """Test parsing LangChain agent format."""
        parser = create_structured_parser("langchain")
        llm_response = {
            "content": "Action: search Action Input: {\"query\": \"test\"}"
        }

        result = parser(llm_response)

        assert len(result) == 1
        assert result[0]["action"] == "search"
        assert result[0]["input"]["query"] == "test"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

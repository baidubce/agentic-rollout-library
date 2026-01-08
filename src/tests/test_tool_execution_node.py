"""
Test cases for Tool Execution Node.

Tests tool registration, execution, and result parsing.
"""

import pytest
import tempfile
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.tool_execution_node import ToolExecutionNode


class TestToolExecutionNode:
    """Test Tool Execution Node functionality."""

    def setup_method(self):
        """Create temporary directory for test tools."""
        self.test_dir = Path(tempfile.mkdtemp())

        # Create a simple test tool script
        self.simple_tool = self.test_dir / "simple_tool.py"
        self.simple_tool.write_text("""#!/usr/bin/env python3
import sys
import json

# Parse arguments
args = sys.argv[1:]
result = {
    "output": f"Executed with {len(args)} args",
    "args": args
}
print(json.dumps(result))
""")
        self.simple_tool.chmod(0o755)

        # Create an echo tool
        self.echo_tool = self.test_dir / "echo_tool.py"
        self.echo_tool.write_text("""#!/usr/bin/env python3
import sys
message = sys.argv[1] if len(sys.argv) > 1 else "No message"
print(f"ECHO: {message}")
""")
        self.echo_tool.chmod(0o755)

    def test_init(self):
        """Test initialization."""
        node = ToolExecutionNode(name="TestExecutor")

        assert node.name == "TestExecutor"
        assert len(node.tools) > 0  # Has default stop tool
        assert node.last_execution_results == []
        assert node.termination_status is None

    def test_register_tool_basic(self):
        """Test basic tool registration."""
        node = ToolExecutionNode()
        node.register_tool(
            "test_tool",
            str(self.simple_tool)
        )

        assert "test_tool" in node.tools
        assert node.tools["test_tool"].script_path == str(self.simple_tool)

    def test_register_tool_with_parser(self):
        """Test tool registration with custom result parser."""
        def custom_parser(result):
            return {"parsed": True, "original": result}

        node = ToolExecutionNode()
        node.register_tool(
            "test_tool",
            str(self.simple_tool),
            custom_parser
        )

        assert node.tools["test_tool"].result_parser == custom_parser

    def test_list_tools(self):
        """Test listing registered tools."""
        node = ToolExecutionNode()
        node.register_tool("tool1", str(self.simple_tool))
        node.register_tool("tool2", str(self.echo_tool))

        tools = node.list_tools()

        assert "tool1" in tools
        assert "tool2" in tools
        assert "stop" in tools  # Default stop tool

    def test_get_tool_schema(self):
        """Test getting tool schema."""
        node = ToolExecutionNode()
        node.register_tool("test_tool", str(self.simple_tool))

        schema = node.get_tool_schema("test_tool")

        assert schema is not None
        assert schema["name"] == "test_tool"

    def test_get_all_tool_schemas(self):
        """Test getting all tool schemas."""
        node = ToolExecutionNode()
        node.register_tool("tool1", str(self.simple_tool))
        node.register_tool("tool2", str(self.echo_tool))

        schemas = node.get_all_tool_schemas()

        assert len(schemas) >= 2
        tool_names = [s["name"] for s in schemas]
        assert "tool1" in tool_names
        assert "tool2" in tool_names

    def test_process_single_tool(self):
        """Test processing single tool call."""
        node = ToolExecutionNode()
        node.register_tool("echo", str(self.echo_tool))

        tool_calls = [
            {
                "tool": "echo",
                "parameters": {"message": "Hello"}
            }
        ]

        results = node.process(tool_calls)

        assert len(results) == 1
        assert "ECHO: Hello" in str(results[0])

    def test_process_multiple_tools(self):
        """Test processing multiple tool calls."""
        node = ToolExecutionNode()
        node.register_tool("echo", str(self.echo_tool))

        tool_calls = [
            {"tool": "echo", "parameters": {"message": "First"}},
            {"tool": "echo", "parameters": {"message": "Second"}}
        ]

        results = node.process(tool_calls)

        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_process_async(self):
        """Test async tool processing."""
        node = ToolExecutionNode()
        node.register_tool("echo", str(self.echo_tool))

        tool_calls = [
            {"tool": "echo", "parameters": {"message": "Async test"}}
        ]

        results = await node.process_async(tool_calls)

        assert len(results) == 1
        assert "Async test" in str(results[0])

    def test_stop_tool(self):
        """Test stop tool execution."""
        node = ToolExecutionNode()

        tool_calls = [
            {"tool": "stop", "parameters": {}}
        ]

        results = node.process(tool_calls)

        assert len(results) == 1
        assert results[0].get("status") == "stop"
        assert node.check_termination() is True

    def test_custom_result_parser(self):
        """Test custom result parser application."""
        def extract_output(result):
            if isinstance(result, dict):
                return result.get("stdout", "")
            return str(result)

        node = ToolExecutionNode()
        node.register_tool(
            "echo",
            str(self.echo_tool),
            extract_output
        )

        tool_calls = [{"tool": "echo", "parameters": {"message": "Test"}}]
        results = node.process(tool_calls)

        # Parser should have extracted just the stdout
        assert isinstance(results[0], str)
        assert "ECHO: Test" in results[0]

    def test_unknown_tool_error(self):
        """Test handling of unknown tool."""
        node = ToolExecutionNode()

        tool_calls = [
            {"tool": "nonexistent", "parameters": {}}
        ]

        results = node.process(tool_calls)

        assert len(results) == 1
        assert "error" in str(results[0]).lower() or "not found" in str(results[0]).lower()

    def test_check_termination_false(self):
        """Test termination check returns False initially."""
        node = ToolExecutionNode()

        assert node.check_termination() is False

    def test_check_termination_true_after_stop(self):
        """Test termination check returns True after stop tool."""
        node = ToolExecutionNode()

        tool_calls = [{"tool": "stop", "parameters": {}}]
        node.process(tool_calls)

        assert node.check_termination() is True

    def test_get_last_execution_results(self):
        """Test getting last execution results."""
        node = ToolExecutionNode()
        node.register_tool("echo", str(self.echo_tool))

        tool_calls = [{"tool": "echo", "parameters": {"message": "Test"}}]
        node.process(tool_calls)

        last_results = node.get_last_execution_results()

        assert len(last_results) == 1

    def test_reset(self):
        """Test resetting node state."""
        node = ToolExecutionNode()
        node.register_tool("echo", str(self.echo_tool))

        tool_calls = [{"tool": "echo", "parameters": {"message": "Test"}}]
        node.process(tool_calls)

        assert len(node.last_execution_results) > 0

        node.reset()

        assert node.last_execution_results == []
        assert node.termination_status is None

    def test_tool_with_arguments_dict(self):
        """Test tool execution with arguments dict."""
        node = ToolExecutionNode()
        node.register_tool("echo", str(self.echo_tool))

        tool_calls = [
            {
                "tool": "echo",
                "arguments": {"message": "Via arguments"}
            }
        ]

        results = node.process(tool_calls)

        assert len(results) == 1

    def test_empty_tool_calls(self):
        """Test processing empty tool calls list."""
        node = ToolExecutionNode()

        results = node.process([])

        assert results == []

    @pytest.mark.asyncio
    async def test_concurrent_tool_execution(self):
        """Test concurrent execution of multiple tools."""
        node = ToolExecutionNode()
        node.register_tool("echo", str(self.echo_tool))

        tool_calls = [
            {"tool": "echo", "parameters": {"message": f"Call {i}"}}
            for i in range(5)
        ]

        results = await node.process_async(tool_calls)

        assert len(results) == 5

    def test_tool_execution_preserves_order(self):
        """Test that tool execution preserves call order."""
        node = ToolExecutionNode()
        node.register_tool("echo", str(self.echo_tool))

        tool_calls = [
            {"tool": "echo", "parameters": {"message": "First"}},
            {"tool": "echo", "parameters": {"message": "Second"}},
            {"tool": "echo", "parameters": {"message": "Third"}}
        ]

        results = node.process(tool_calls)

        assert len(results) == 3
        # Results should be in order
        assert "First" in str(results[0])
        assert "Second" in str(results[1])
        assert "Third" in str(results[2])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

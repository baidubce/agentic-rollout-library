"""
Test cases for ContextEngineeringNode.

Tests cover message management, context operations, and filtering.
"""

import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core import ContextEngineeringNode, Message


class TestContextEngineeringNode(unittest.TestCase):
    """Test cases for ContextEngineeringNode."""

    def setUp(self):
        """Set up test fixtures."""
        self.context = ContextEngineeringNode(name="TestContext")

    def tearDown(self):
        """Clean up after tests."""
        self.context.reset()

    def test_initialization(self):
        """Test node initialization."""
        self.assertEqual(self.context.name, "TestContext")
        self.assertEqual(self.context.get_message_count(), 0)

    def test_add_simple_message(self):
        """Test adding a simple message."""
        self.context.add_message(
            message_content="Hello, world!",
            message_role="user",
            message_type="text"
        )

        self.assertEqual(self.context.get_message_count(), 1)
        messages = self.context.get_llm_context()
        self.assertEqual(messages[0]['content'], "Hello, world!")
        self.assertEqual(messages[0]['role'], "user")

    def test_add_formatted_message(self):
        """Test adding messages with Python string formatting."""
        # Using f-string
        agent_name = "TestAgent"
        task = "analyze code"
        message = f"Agent {agent_name} is performing: {task}"

        self.context.add_message(
            message_content=message,
            message_role="assistant",
            message_type="text"
        )

        messages = self.context.get_llm_context()
        self.assertEqual(messages[0]['content'], "Agent TestAgent is performing: analyze code")

    def test_add_multiple_messages(self):
        """Test adding multiple messages."""
        self.context.add_message("Message 1", "user", "text")
        self.context.add_message("Message 2", "assistant", "text")
        self.context.add_message("Message 3", "tool", "tool_result")

        self.assertEqual(self.context.get_message_count(), 3)

        messages = self.context.get_llm_context()
        self.assertEqual(len(messages), 3)
        self.assertEqual(messages[0]['content'], "Message 1")
        self.assertEqual(messages[1]['content'], "Message 2")
        self.assertEqual(messages[2]['content'], "Message 3")

    def test_clear_messages(self):
        """Test clearing all messages."""
        self.context.add_message("Message 1", "user", "text")
        self.context.add_message("Message 2", "user", "text")

        self.assertEqual(self.context.get_message_count(), 2)

        self.context.clear_messages()
        self.assertEqual(self.context.get_message_count(), 0)

    def test_compress_context(self):
        """Test context compression."""
        # Add 10 messages
        for i in range(10):
            self.context.add_message(f"Message {i}", "user", "text")

        self.assertEqual(self.context.get_message_count(), 10)

        # Compress to keep first 2 and last 3
        self.context.compress_context(keep_first=2, keep_last=3)

        # Should have 2 + 1 (summary) + 3 = 6 messages
        self.assertEqual(self.context.get_message_count(), 6)

        messages = self.context.messages
        self.assertEqual(messages[0].content, "Message 0")
        self.assertEqual(messages[1].content, "Message 1")
        self.assertIn("compressed", messages[2].content.lower())
        self.assertEqual(messages[3].content, "Message 7")
        self.assertEqual(messages[4].content, "Message 8")
        self.assertEqual(messages[5].content, "Message 9")

    def test_get_messages_by_role(self):
        """Test filtering messages by role."""
        self.context.add_message("User msg", "user", "text")
        self.context.add_message("System msg", "system", "text")
        self.context.add_message("User msg 2", "user", "text")

        user_messages = self.context.get_messages_by_role("user")
        self.assertEqual(len(user_messages), 2)
        self.assertEqual(user_messages[0].content, "User msg")
        self.assertEqual(user_messages[1].content, "User msg 2")

    def test_get_messages_by_type(self):
        """Test filtering messages by type."""
        self.context.add_message("Query 1", "user", "query")
        self.context.add_message("Text 1", "user", "text")
        self.context.add_message("Query 2", "user", "query")

        query_messages = self.context.get_messages_by_type("query")
        self.assertEqual(len(query_messages), 2)
        self.assertEqual(query_messages[0].content, "Query 1")
        self.assertEqual(query_messages[1].content, "Query 2")

    def test_max_context_length(self):
        """Test automatic compression when max length is reached."""
        context = ContextEngineeringNode(name="LimitedContext", max_context_length=5)

        # Add more messages than max_context_length
        for i in range(10):
            context.add_message(f"Message {i}", "user", "text")

        # Process should trigger compression
        context.process()

        # Should be compressed to max_context_length or less
        self.assertLessEqual(context.get_message_count(), 5)

    def test_reset(self):
        """Test resetting the node."""
        self.context.add_message("Test message", "user", "text")

        self.assertEqual(self.context.get_message_count(), 1)

        self.context.reset()

        self.assertEqual(self.context.get_message_count(), 0)

    def test_get_message(self):
        """Test getting a specific message by index."""
        self.context.add_message("Message 0", "user", "text")
        self.context.add_message("Message 1", "assistant", "text")
        self.context.add_message("Message 2", "tool", "tool_result")

        # Test positive indexing
        msg0 = self.context.get_message(0)
        self.assertEqual(msg0.content, "Message 0")
        self.assertEqual(msg0.role, "user")

        # Test negative indexing
        msg_last = self.context.get_message(-1)
        self.assertEqual(msg_last.content, "Message 2")
        self.assertEqual(msg_last.role, "tool")

    def test_update_message_content(self):
        """Test updating message content."""
        self.context.add_message("Original content", "user", "text")

        # Update the message
        self.context.update_message_content(0, "Updated content")

        msg = self.context.get_message(0)
        self.assertEqual(msg.content, "Updated content")

    def test_delete_message(self):
        """Test deleting a message."""
        self.context.add_message("Message 0", "user", "text")
        self.context.add_message("Message 1", "user", "text")
        self.context.add_message("Message 2", "user", "text")

        self.assertEqual(self.context.get_message_count(), 3)

        # Delete middle message
        self.context.delete_message(1)

        self.assertEqual(self.context.get_message_count(), 2)
        self.assertEqual(self.context.get_message(0).content, "Message 0")
        self.assertEqual(self.context.get_message(1).content, "Message 2")

    def test_llm_context_format(self):
        """Test that get_llm_context returns correct format."""
        self.context.add_message("System message", "system", "system_prompt")
        self.context.add_message("User message", "user", "query")

        llm_context = self.context.get_llm_context()

        self.assertEqual(len(llm_context), 2)

        # Check format
        self.assertIn('role', llm_context[0])
        self.assertIn('content', llm_context[0])
        self.assertEqual(llm_context[0]['role'], 'system')
        self.assertEqual(llm_context[0]['content'], 'System message')

    def test_message_formatting_example(self):
        """Test using Python native string formatting with messages."""
        # Example with f-string
        agent = "CodeAssistant"
        capabilities = "debugging, testing"
        system_msg = f"You are {agent} with capabilities: {capabilities}"

        self.context.add_message(system_msg, "system", "system_prompt")

        # Example with .format()
        template = "[{timestamp}] {agent}: {content}"
        formatted = template.format(
            timestamp="2025-01-15",
            agent=agent,
            content="Task completed"
        )

        self.context.add_message(formatted, "assistant", "text")

        messages = self.context.get_llm_context()
        self.assertEqual(len(messages), 2)
        self.assertIn("CodeAssistant", messages[0]['content'])
        self.assertIn("debugging", messages[0]['content'])
        self.assertIn("2025-01-15", messages[1]['content'])


if __name__ == "__main__":
    unittest.main()

"""Context Engineering Node implementation."""

from typing import Any, Dict, List, Union
from .base_node import BaseNode


class Message:
    """Represents a message in the context."""

    def __init__(self, role: str, content: Union[str, Dict], message_type: str = "text"):
        """
        Initialize a message.

        Args:
            role: Message role (system, user, assistant, tool)
            content: Message content (string or dict)
            message_type: Type of message (system_prompt, query, tool_result, text)
        """
        self.role = role
        self.content = content
        self.message_type = message_type

    def to_dict(self) -> Dict:
        """Convert message to dictionary format."""
        return {
            "role": self.role,
            "content": self.content,
            "type": self.message_type
        }

    def __repr__(self) -> str:
        """String representation of the message."""
        return f"Message(role={self.role}, type={self.message_type})"

    def __str__(self) -> str:
        """String representation of the message."""
        return self.pretty_printable()

    def pretty_printable(self, max_content_length: int = 500) -> str:
        """
        Return a pretty-printable string representation of the message.

        Args:
            max_content_length: Maximum length of content to display before truncating

        Returns:
            Formatted string representation
        """
        lines = []
        lines.append("=" * 60)
        lines.append(f"Message Role: {self.role}")
        lines.append(f"Message Type: {self.message_type}")
        lines.append("-" * 60)

        # Format content
        lines.append("Message Content:")
        if isinstance(self.content, str):
            # Preserve newlines in the content
            content_lines = self.content.split('\n')
            total_length = 0
            displayed_lines = []
            truncated = False

            for line in content_lines:
                if total_length + len(line) > max_content_length:
                    truncated = True
                    break
                displayed_lines.append(f"  {line}")
                total_length += len(line)

            lines.extend(displayed_lines)
            if truncated:
                lines.append(f"  ... (truncated, total length: {len(self.content)} chars)")
        elif isinstance(self.content, dict):
            # Format dict content
            import json
            try:
                content_str = json.dumps(self.content, indent=2, ensure_ascii=False)
                if len(content_str) > max_content_length:
                    content_str = content_str[:max_content_length] + "\n  ... (truncated)"
                for line in content_str.split('\n'):
                    lines.append(f"  {line}")
            except:
                lines.append(f"  {self.content}")
        else:
            lines.append(f"  {self.content}")

        lines.append("=" * 60)

        return '\n'.join(lines)


class ContextEngineeringNode(BaseNode):
    """Node for managing conversation context and message templates."""

    def __init__(self, name: str = None, max_context_length: int = None, timeline_enabled: bool = False, timeout: float = None):
        """
        Initialize the Context Engineering Node.

        Args:
            name: Optional name for the node
            max_context_length: Maximum number of messages to keep in context
            timeline_enabled: Enable automatic timeline tracking for this node
            timeout: Timeout in seconds for context operations (None = no timeout)
        """
        super().__init__(name, timeline_enabled=timeline_enabled, timeout=timeout)
        self.messages: List[Message] = []
        self.max_context_length = max_context_length

    def add_message(self,
                   message_content: Union[str, Dict],
                   message_role: str = "user",
                   message_type: str = "text") -> None:
        """
        Add a message to the context.

        Args:
            message_content: Content of the message
            message_role: Role of the message sender
            message_type: Type of the message
        """
        message = Message(role=message_role, content=message_content, message_type=message_type)
        self.messages.append(message)
        self.logger.info(f"Added message: {message}")

    def get_all_messages(self) -> List[Message]:
        """
        get all messages
        """
        return self.messages

    def get_llm_context(self) -> List[Dict]:
        """
        Get formatted messages for LLM input.

        Returns:
            List of message dictionaries formatted for LLM
        """
        context = []
        for message in self.messages:
            msg_dict = {
                "role": message.role,
                "content": message.content
            }
            context.append(msg_dict)
        return context

    def clear_messages(self) -> None:
        """Clear all messages from the context."""
        self.messages.clear()
        self.logger.info("Cleared all messages")

    def compress_context(self, keep_first: int = 1, keep_last: int = 5) -> None:
        """
        Compress the context by keeping only essential messages.

        Args:
            keep_first: Number of messages to keep from the beginning
            keep_last: Number of messages to keep from the end
        """
        if len(self.messages) <= keep_first + keep_last:
            return

        first_messages = self.messages[:keep_first]
        last_messages = self.messages[-keep_last:]

        # Create a summary message for compressed content
        compressed_count = len(self.messages) - keep_first - keep_last
        summary_message = Message(
            role="system",
            content=f"[{compressed_count} messages compressed]",
            message_type="compression"
        )

        self.messages = first_messages + [summary_message] + last_messages
        self.logger.info(f"Compressed context: kept {keep_first} first and {keep_last} last messages")

    def process(self, input_data: Any = None) -> List[Dict]:
        """
        Process method to comply with BaseNode interface.
        Returns the current LLM context.

        Args:
            input_data: Optional input (not used)

        Returns:
            Formatted context for LLM
        """
        # Auto-compress if max length exceeded
        if self.max_context_length and len(self.messages) > self.max_context_length:
            # Calculate keep_first and keep_last to fit within max_context_length
            # Account for 1 compression summary message
            keep_first = 1
            keep_last = self.max_context_length - keep_first - 1
            if keep_last < 1:
                keep_last = 1
            self.compress_context(keep_first=keep_first, keep_last=keep_last)

        return self.get_llm_context()

    def reset(self) -> None:
        """Reset the node to initial state."""
        super().reset()
        self.clear_messages()

    def get_message_count(self) -> int:
        """Get the number of messages in context."""
        return len(self.messages)

    def get_messages_by_role(self, role: str) -> List[Message]:
        """
        Get all messages with a specific role.

        Args:
            role: Message role to filter by

        Returns:
            List of messages with the specified role
        """
        return [msg for msg in self.messages if msg.role == role]

    def get_message(self, index: int) -> Message:
        """
        Get a specific message by index.

        Args:
            index: Message index (supports negative indexing)

        Returns:
            Message at the specified index

        Raises:
            IndexError: If index is out of range
        """
        if -len(self.messages) <= index < len(self.messages):
            return self.messages[index]
        raise IndexError(f"Message index {index} out of range (total: {len(self.messages)})")

    def update_message_content(self, index: int, new_content: Union[str, Dict]) -> None:
        """
        Update the content of a specific message.

        Args:
            index: Message index (supports negative indexing)
            new_content: New content to set

        Raises:
            IndexError: If index is out of range
        """
        message = self.get_message(index)
        old_content_preview = str(message.content)[:50] + "..." if len(str(message.content)) > 50 else str(message.content)
        message.content = new_content
        self.logger.info(f"Updated message {index} content (was: '{old_content_preview}')")

    def delete_message(self, index: int) -> None:
        """
        Delete a message by index.

        Args:
            index: Message index (supports negative indexing)

        Raises:
            IndexError: If index is out of range
        """
        if -len(self.messages) <= index < len(self.messages):
            deleted_msg = self.messages[index]
            del self.messages[index]
            self.logger.info(f"Deleted message at index {index} (role: {deleted_msg.role}, type: {deleted_msg.message_type})")
        else:
            raise IndexError(f"Message index {index} out of range (total: {len(self.messages)})")

    def get_messages_by_type(self, message_type: str) -> List[Message]:
        """
        Get all messages with a specific type.

        Args:
            message_type: Message type to filter by

        Returns:
            List of messages with the specified type
        """
        return [msg for msg in self.messages if msg.message_type == message_type]
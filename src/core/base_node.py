"""Base Node class for the Agentic Rollout Library."""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
import logging
import asyncio
from .timeline import get_timeline


class TimeoutError(Exception):
    """Raised when a node execution exceeds the timeout."""
    pass


class BaseNode(ABC):
    """Abstract base class for all nodes in the Agentic Rollout Library."""

    def __init__(self, name: str = None, timeline_enabled: bool = False, timeout: Optional[float] = None):
        """
        Initialize the base node.

        Args:
            name: Optional name for the node instance
            timeline_enabled: Enable automatic timeline tracking for this node
            timeout: Timeout in seconds for node execution (None = no timeout)
        """
        self.name = name or self.__class__.__name__
        self.logger = logging.getLogger(self.name)
        self._metadata = {}
        self.timeline_enabled = timeline_enabled
        self._timeline = get_timeline() if timeline_enabled else None
        self.timeout = timeout

    @abstractmethod
    def process(self, input_data: Any) -> Any:
        """
        Process input data and return output.

        Args:
            input_data: Input data to process

        Returns:
            Processed output data
        """
        pass

    async def process_with_timing(self, input_data: Any, event_type: str = "process", **metadata) -> Any:
        """
        Process input data with automatic timeline tracking (async version).

        Args:
            input_data: Input data to process
            event_type: Type of event to record (default: "process")
            **metadata: Additional metadata to record with the event

        Returns:
            Processed output data

        Raises:
            TimeoutError: If execution exceeds timeout
        """
        async def _execute():
            if hasattr(self, 'process_async'):
                return await self.process_async(input_data)
            else:
                return self.process(input_data)

        if self.timeline_enabled and self._timeline:
            event_id = self._timeline.start_event(self.name, event_type, metadata)
            try:
                if self.timeout:
                    result = await asyncio.wait_for(_execute(), timeout=self.timeout)
                else:
                    result = await _execute()
                return result
            except asyncio.TimeoutError:
                self.logger.error(f"Node {self.name} execution exceeded timeout of {self.timeout}s")
                raise TimeoutError(f"Node {self.name} execution exceeded timeout of {self.timeout}s")
            finally:
                self._timeline.end_event(event_id)
        else:
            if self.timeout:
                return await asyncio.wait_for(_execute(), timeout=self.timeout)
            else:
                return await _execute()

    def validate_input(self, input_data: Any) -> bool:
        """
        Validate input data before processing.

        Args:
            input_data: Input data to validate

        Returns:
            True if input is valid, False otherwise
        """
        return True

    def validate_output(self, output_data: Any) -> bool:
        """
        Validate output data after processing.

        Args:
            output_data: Output data to validate

        Returns:
            True if output is valid, False otherwise
        """
        return True

    def set_metadata(self, key: str, value: Any) -> None:
        """
        Set metadata for the node.

        Args:
            key: Metadata key
            value: Metadata value
        """
        self._metadata[key] = value

    def get_metadata(self, key: str) -> Optional[Any]:
        """
        Get metadata for the node.

        Args:
            key: Metadata key

        Returns:
            Metadata value if exists, None otherwise
        """
        return self._metadata.get(key)

    def reset(self) -> None:
        """Reset the node to initial state."""
        self._metadata.clear()

    def __repr__(self) -> str:
        """String representation of the node."""
        return f"{self.__class__.__name__}(name={self.name})"
"""
Test cases for Timeline Manager.

Tests timeline tracking, statistics, and profiling functionality.
"""

import pytest
import time
import asyncio
import sys
from pathlib import Path
import tempfile

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.timeline import TimelineManager, TimelineEvent, get_timeline


class TestTimelineEvent:
    """Test TimelineEvent dataclass."""

    def test_event_creation(self):
        """Test creating a timeline event."""
        event = TimelineEvent(
            node_name="TestNode",
            event_type="process",
            start_time=100.0,
            end_time=105.0,
            metadata={"key": "value"}
        )

        assert event.node_name == "TestNode"
        assert event.event_type == "process"
        assert event.start_time == 100.0
        assert event.end_time == 105.0
        assert event.metadata["key"] == "value"

    def test_duration_calculation(self):
        """Test duration calculation."""
        event = TimelineEvent(
            node_name="Test",
            event_type="process",
            start_time=100.0,
            end_time=105.5
        )

        assert event.duration == 5.5

    def test_duration_none_when_not_ended(self):
        """Test duration is None when event not ended."""
        event = TimelineEvent(
            node_name="Test",
            event_type="process",
            start_time=100.0
        )

        assert event.duration is None

    def test_to_dict(self):
        """Test converting event to dictionary."""
        event = TimelineEvent(
            node_name="Test",
            event_type="process",
            start_time=100.0,
            end_time=102.0,
            metadata={"test": "data"}
        )

        result = event.to_dict()

        assert result["node_name"] == "Test"
        assert result["event_type"] == "process"
        assert result["start_time"] == 100.0
        assert result["end_time"] == 102.0
        assert result["duration"] == 2.0
        assert result["metadata"]["test"] == "data"


class TestTimelineManager:
    """Test Timeline Manager functionality."""

    def setup_method(self):
        """Clear timeline before each test."""
        timeline = get_timeline()
        timeline.clear()
        timeline.enable()

    def test_singleton_pattern(self):
        """Test that TimelineManager is a singleton."""
        timeline1 = TimelineManager()
        timeline2 = TimelineManager()

        assert timeline1 is timeline2

    def test_get_timeline_returns_singleton(self):
        """Test get_timeline() returns singleton instance."""
        timeline1 = get_timeline()
        timeline2 = get_timeline()

        assert timeline1 is timeline2

    def test_start_event(self):
        """Test starting an event."""
        timeline = get_timeline()

        event_id = timeline.start_event(
            "TestNode",
            "process",
            {"key": "value"}
        )

        assert event_id is not None
        assert event_id.startswith("TestNode:process:")
        assert event_id in timeline.active_events

    def test_end_event(self):
        """Test ending an event."""
        timeline = get_timeline()

        event_id = timeline.start_event("TestNode", "process")
        time.sleep(0.01)
        duration = timeline.end_event(event_id)

        assert duration is not None
        assert duration > 0
        assert event_id not in timeline.active_events
        assert len(timeline.events) == 1

    def test_end_event_invalid_id(self):
        """Test ending event with invalid ID."""
        timeline = get_timeline()

        duration = timeline.end_event("invalid_id")

        assert duration is None

    def test_get_timeline(self):
        """Test getting timeline as list of dicts."""
        timeline = get_timeline()

        timeline.start_event("Node1", "process")
        timeline.end_event(timeline.start_event("Node2", "call"))

        result = timeline.get_timeline()

        assert isinstance(result, list)
        assert len(result) >= 1
        assert all(isinstance(e, dict) for e in result)

    def test_get_stats_empty(self):
        """Test getting stats from empty timeline."""
        timeline = get_timeline()
        timeline.clear()

        stats = timeline.get_stats()

        assert stats["total_duration"] == 0
        assert stats["total_events"] == 0
        assert stats["nodes"] == {}

    def test_get_stats_with_events(self):
        """Test getting stats with events."""
        timeline = get_timeline()

        # Add some events
        id1 = timeline.start_event("Node1", "process", {})
        time.sleep(0.01)
        timeline.end_event(id1)

        id2 = timeline.start_event("Node2", "call", {})
        time.sleep(0.01)
        timeline.end_event(id2)

        stats = timeline.get_stats()

        assert stats["total_events"] == 2
        assert stats["total_duration"] > 0
        assert "Node1" in stats["nodes"]
        assert "Node2" in stats["nodes"]
        assert stats["nodes"]["Node1"]["event_count"] == 1
        assert stats["nodes"]["Node2"]["event_count"] == 1

    def test_get_stats_multiple_events_same_node(self):
        """Test stats aggregation for multiple events from same node."""
        timeline = get_timeline()

        for i in range(3):
            event_id = timeline.start_event("TestNode", "process")
            time.sleep(0.01)
            timeline.end_event(event_id)

        stats = timeline.get_stats()

        assert stats["total_events"] == 3
        assert stats["nodes"]["TestNode"]["event_count"] == 3
        assert len(stats["nodes"]["TestNode"]["events"]["process"]) == 3

    def test_clear(self):
        """Test clearing timeline."""
        timeline = get_timeline()

        timeline.start_event("Node1", "process")
        timeline.end_event(timeline.start_event("Node2", "call"))

        assert len(timeline.events) > 0

        timeline.clear()

        assert len(timeline.events) == 0
        assert len(timeline.active_events) == 0

    def test_disable_enable(self):
        """Test disabling and enabling timeline."""
        timeline = get_timeline()

        timeline.disable()

        event_id = timeline.start_event("Node", "process")

        assert event_id == ""
        assert len(timeline.active_events) == 0

        timeline.enable()

        event_id = timeline.start_event("Node", "process")

        assert event_id != ""
        assert len(timeline.active_events) > 0

    def test_export_json(self):
        """Test exporting timeline to JSON."""
        timeline = get_timeline()

        event_id = timeline.start_event("TestNode", "process")
        time.sleep(0.01)
        timeline.end_event(event_id)

        # Export to temp file
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
            temp_path = f.name

        try:
            timeline.export_json(temp_path)

            # Verify file was created and contains data
            import json
            with open(temp_path, 'r') as f:
                data = json.load(f)

            assert "stats" in data
            assert "events" in data
            assert len(data["events"]) >= 1
        finally:
            Path(temp_path).unlink()

    def test_print_summary(self, capsys):
        """Test printing timeline summary."""
        timeline = get_timeline()

        event_id = timeline.start_event("TestNode", "process")
        time.sleep(0.01)
        timeline.end_event(event_id)

        timeline.print_summary()

        captured = capsys.readouterr()
        assert "TIMELINE SUMMARY" in captured.out
        assert "TestNode" in captured.out

    def test_print_summary_detailed(self, capsys):
        """Test printing detailed timeline summary."""
        timeline = get_timeline()

        event_id = timeline.start_event("TestNode", "llm_call")
        time.sleep(0.01)
        timeline.end_event(event_id)

        timeline.print_summary(detailed=True)

        captured = capsys.readouterr()
        assert "llm_call" in captured.out
        assert "avg=" in captured.out

    def test_print_summary_empty(self, capsys):
        """Test printing summary with no events."""
        timeline = get_timeline()
        timeline.clear()

        timeline.print_summary()

        captured = capsys.readouterr()
        assert "No timeline events" in captured.out

    def test_multiple_event_types(self):
        """Test tracking multiple event types."""
        timeline = get_timeline()

        id1 = timeline.start_event("Node1", "llm_call")
        time.sleep(0.01)
        timeline.end_event(id1)

        id2 = timeline.start_event("Node1", "parse")
        time.sleep(0.01)
        timeline.end_event(id2)

        stats = timeline.get_stats()

        assert "llm_call" in stats["nodes"]["Node1"]["events"]
        assert "parse" in stats["nodes"]["Node1"]["events"]

    def test_event_metadata_preserved(self):
        """Test that event metadata is preserved."""
        timeline = get_timeline()

        metadata = {
            "model": "gpt-4",
            "tokens": 100,
            "temperature": 0.7
        }

        event_id = timeline.start_event("LLMNode", "call", metadata)
        timeline.end_event(event_id)

        events = timeline.get_timeline()

        assert len(events) == 1
        assert events[0]["metadata"]["model"] == "gpt-4"
        assert events[0]["metadata"]["tokens"] == 100

    @pytest.mark.asyncio
    async def test_concurrent_event_tracking(self):
        """Test tracking events from concurrent tasks."""
        timeline = get_timeline()
        timeline.clear()

        async def tracked_task(node_name, task_id):
            event_id = timeline.start_event(node_name, "async_task", {"task_id": task_id})
            await asyncio.sleep(0.01)
            timeline.end_event(event_id)

        # Run multiple tasks concurrently
        await asyncio.gather(
            tracked_task("Node1", 1),
            tracked_task("Node2", 2),
            tracked_task("Node3", 3)
        )

        stats = timeline.get_stats()

        assert stats["total_events"] == 3
        assert "Node1" in stats["nodes"]
        assert "Node2" in stats["nodes"]
        assert "Node3" in stats["nodes"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

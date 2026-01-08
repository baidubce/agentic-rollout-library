"""Timeline tracking for node execution profiling."""

import time
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from collections import defaultdict
import json


@dataclass
class TimelineEvent:
    """Represents a single event in the timeline."""

    node_name: str
    event_type: str  # "process", "tool_call", etc.
    start_time: float
    end_time: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def duration(self) -> Optional[float]:
        """Get event duration in seconds."""
        if self.end_time is None:
            return None
        return self.end_time - self.start_time

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "node_name": self.node_name,
            "event_type": self.event_type,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration": self.duration,
            "metadata": self.metadata
        }


class TimelineManager:
    """
    Global timeline manager for tracking node execution times.

    Usage:
        # Create singleton instance
        timeline = TimelineManager()

        # Record events
        event_id = timeline.start_event("LLMNode", "process", {"model": "gpt-4"})
        # ... do work ...
        timeline.end_event(event_id)

        # View results
        timeline.print_summary()
        timeline.export_json("timeline.json")
    """

    _instance = None

    def __new__(cls):
        """Singleton pattern."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        """Initialize timeline manager."""
        if self._initialized:
            return

        self.events: List[TimelineEvent] = []
        self.active_events: Dict[str, TimelineEvent] = {}
        self.enabled = True
        self._initialized = True

    def start_event(self, node_name: str, event_type: str, metadata: Dict[str, Any] = None) -> str:
        """
        Start tracking an event.

        Args:
            node_name: Name of the node
            event_type: Type of event (e.g., "process", "tool_call")
            metadata: Additional metadata

        Returns:
            Event ID for ending the event later
        """
        if not self.enabled:
            return ""

        event = TimelineEvent(
            node_name=node_name,
            event_type=event_type,
            start_time=time.time(),
            metadata=metadata or {}
        )

        event_id = f"{node_name}:{event_type}:{len(self.events)}"
        self.active_events[event_id] = event

        return event_id

    def end_event(self, event_id: str) -> Optional[float]:
        """
        End tracking an event.

        Args:
            event_id: Event ID from start_event()

        Returns:
            Duration in seconds, or None if event not found
        """
        if not self.enabled or not event_id:
            return None

        if event_id not in self.active_events:
            return None

        event = self.active_events.pop(event_id)
        event.end_time = time.time()
        self.events.append(event)

        return event.duration

    def get_timeline(self) -> List[Dict[str, Any]]:
        """Get complete timeline as list of dicts."""
        return [event.to_dict() for event in self.events]

    def get_stats(self) -> Dict[str, Any]:
        """
        Get timeline statistics.

        Returns:
            Statistics dictionary with node-wise breakdown
        """
        if not self.events:
            return {
                "total_duration": 0,
                "total_events": 0,
                "nodes": {}
            }

        # Calculate total duration
        start_time = min(e.start_time for e in self.events)
        end_time = max(e.end_time for e in self.events if e.end_time is not None)
        total_duration = end_time - start_time

        # Group by node
        node_stats = defaultdict(lambda: {
            "total_duration": 0,
            "event_count": 0,
            "events": defaultdict(list)
        })

        for event in self.events:
            if event.duration is None:
                continue

            node_stats[event.node_name]["total_duration"] += event.duration
            node_stats[event.node_name]["event_count"] += 1
            node_stats[event.node_name]["events"][event.event_type].append(event.duration)

        return {
            "total_duration": total_duration,
            "total_events": len(self.events),
            "nodes": dict(node_stats)
        }

    def print_summary(self, detailed: bool = False):
        """
        Print timeline summary.

        Args:
            detailed: If True, show individual events
        """
        if not self.events:
            print("No timeline events recorded.")
            return

        stats = self.get_stats()

        print("\n" + "=" * 60)
        print("TIMELINE SUMMARY")
        print("=" * 60)
        print(f"Total Duration: {stats['total_duration']:.3f}s")
        print(f"Total Events: {stats['total_events']}")
        print()

        # Print per-node stats
        for node_name, node_data in sorted(stats['nodes'].items()):
            duration = node_data['total_duration']
            percentage = (duration / stats['total_duration'] * 100) if stats['total_duration'] > 0 else 0

            print(f"Node: {node_name}")
            print(f"  Total: {duration:.3f}s ({percentage:.1f}%)")
            print(f"  Events: {node_data['event_count']}")

            if detailed:
                for event_type, durations in node_data['events'].items():
                    avg_duration = sum(durations) / len(durations)
                    print(f"    - {event_type}: avg={avg_duration:.3f}s, count={len(durations)}")

            print()

    def export_json(self, filepath: str):
        """
        Export timeline to JSON file.

        Args:
            filepath: Path to save JSON file
        """
        data = {
            "stats": self.get_stats(),
            "events": self.get_timeline()
        }

        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)

        print(f"Timeline exported to {filepath}")

    def clear(self):
        """Clear all timeline data."""
        self.events.clear()
        self.active_events.clear()

    def disable(self):
        """Disable timeline tracking."""
        self.enabled = False

    def enable(self):
        """Enable timeline tracking."""
        self.enabled = True


# Global timeline instance
_global_timeline = TimelineManager()


def get_timeline() -> TimelineManager:
    """Get the global timeline manager."""
    return _global_timeline

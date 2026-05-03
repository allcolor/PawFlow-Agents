"""
Provenance module for tracking FlowFile history through the DAG.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import threading
import uuid
from typing import Any, Dict, List, Optional


class ProvenanceEventType(Enum):
    """Provenance event types."""
    CREATE = "CREATE"
    RECEIVE = "RECEIVE"
    SEND = "SEND"
    MODIFY = "MODIFY"
    CLONE = "CLONE"
    DROP = "DROP"
    ROUTE = "ROUTE"


@dataclass
class ProvenanceEvent:
    """FlowFile provenance event."""
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    event_type: ProvenanceEventType = ProvenanceEventType.CREATE
    timestamp: datetime = field(default_factory=datetime.now)
    flowfile_id: str = ""
    parent_flowfile_ids: List[str] = field(default_factory=list)
    child_flowfile_ids: List[str] = field(default_factory=list)
    task_id: str = ""
    task_type: str = ""
    flow_id: str = ""
    content_size: int = 0
    attributes: Dict[str, str] = field(default_factory=dict)
    details: str = ""
    duration_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "timestamp": self.timestamp.isoformat(),
            "flowfile_id": self.flowfile_id,
            "parent_flowfile_ids": self.parent_flowfile_ids.copy(),
            "child_flowfile_ids": self.child_flowfile_ids.copy(),
            "task_id": self.task_id,
            "task_type": self.task_type,
            "flow_id": self.flow_id,
            "content_size": self.content_size,
            "attributes": self.attributes.copy(),
            "details": self.details,
            "duration_ms": self.duration_ms,
        }


class ProvenanceRepository:
    """Thread-safe repository for provenance events."""

    def __init__(self, max_events: int = 100000):
        self._events: List[ProvenanceEvent] = []
        self._lock = threading.Lock()
        self._max_events = max_events

    def record(self, event: ProvenanceEvent) -> None:
        """Record an event (thread-safe, FIFO)."""
        with self._lock:
            self._events.append(event)
            if len(self._events) > self._max_events:
                self._events = self._events[-self._max_events:]

    def get_events(
        self,
        flowfile_id: Optional[str] = None,
        task_id: Optional[str] = None,
        event_type: Optional[ProvenanceEventType] = None,
        flow_id: Optional[str] = None,
        limit: int = 100
    ) -> List[ProvenanceEvent]:
        """Filter events."""
        with self._lock:
            filtered = self._events.copy()

        if flowfile_id:
            filtered = [e for e in filtered if e.flowfile_id == flowfile_id]
        if task_id:
            filtered = [e for e in filtered if e.task_id == task_id]
        if event_type:
            filtered = [e for e in filtered if e.event_type == event_type]
        if flow_id:
            filtered = [e for e in filtered if e.flow_id == flow_id]

        return filtered[-limit:]

    def get_lineage(self, flowfile_id: str) -> List[ProvenanceEvent]:
        """Reconstruire le lignage complet d'un FlowFile (parents + enfants recursifs)."""
        with self._lock:
            all_events = self._events.copy()

        lineage_events: List[ProvenanceEvent] = []
        visited: set = set()

        events_by_ff: Dict[str, List[ProvenanceEvent]] = {}
        for event in all_events:
            events_by_ff.setdefault(event.flowfile_id, []).append(event)

        def explore(ff_id: str) -> None:
            if ff_id in visited:
                return
            visited.add(ff_id)

            if ff_id in events_by_ff:
                lineage_events.extend(events_by_ff[ff_id])

            for event in all_events:
                if ff_id in event.parent_flowfile_ids:
                    explore(event.flowfile_id)
                if ff_id in event.child_flowfile_ids:
                    explore(event.flowfile_id)

        explore(flowfile_id)
        return sorted(lineage_events, key=lambda e: e.timestamp)

    def get_flow_events(self, flow_id: str) -> List[ProvenanceEvent]:
        """All events for a flow."""
        with self._lock:
            filtered = [e for e in self._events if e.flow_id == flow_id]
        return sorted(filtered, key=lambda e: e.timestamp)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()

    def size(self) -> int:
        with self._lock:
            return len(self._events)

    def to_dict(self) -> Dict[str, Any]:
        """Resume statistique."""
        with self._lock:
            events = self._events.copy()

        by_type: Dict[str, int] = {}
        by_task: Dict[str, int] = {}

        for event in events:
            t = event.event_type.value
            by_type[t] = by_type.get(t, 0) + 1
            task = event.task_type or "unknown"
            by_task[task] = by_task.get(task, 0) + 1

        return {
            "total_events": len(events),
            "max_events": self._max_events,
            "events_by_type": by_type,
            "events_by_task": by_task,
        }


# Singleton instance
_provenance_repository: Optional[ProvenanceRepository] = None


def get_provenance_repository(max_events: int = 100000) -> ProvenanceRepository:
    """Get or create the singleton ProvenanceRepository instance."""
    global _provenance_repository
    if _provenance_repository is None:
        _provenance_repository = ProvenanceRepository(max_events=max_events)
    return _provenance_repository

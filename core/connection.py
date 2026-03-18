"""Connection - queued connection between tasks with backpressure.

In NiFi, a Connection is a queue between two processors.
It holds FlowFiles waiting to be processed, with configurable
backpressure thresholds (by count and size).
"""

import threading
import time
from typing import Optional, List, Dict, Any
from datetime import datetime

from core import FlowFile
from core.prioritizer import PrioritizedQueue, PrioritizerType


class Connection:
    """A queued connection between two tasks with backpressure.

    Supports FlowFile TTL: if flowfile_ttl_seconds > 0, FlowFiles that
    have been queued longer than this are considered expired on dequeue.
    Expired FlowFiles get attribute 'expired'='true' and are skipped
    (returned separately via drain_expired()).
    """

    def __init__(self, source_id: str, target_id: str,
                 relationship: str = "success",
                 max_queue_size: int = 10000,
                 max_queue_bytes: int = 100 * 1024 * 1024,  # 100MB
                 prioritizer: PrioritizerType = PrioritizerType.PRIORITY_ATTRIBUTE,
                 priority_attribute: str = "priority",
                 flowfile_ttl_seconds: int = 0):
        self.source_id = source_id
        self.target_id = target_id
        self.relationship = relationship
        self.max_queue_size = max_queue_size
        self.max_queue_bytes = max_queue_bytes
        self.flowfile_ttl_seconds = flowfile_ttl_seconds

        self._queue = PrioritizedQueue(
            prioritizer_type=prioritizer,
            max_size=max_queue_size,
            priority_attribute=priority_attribute,
        )
        self._total_bytes: int = 0
        self._lock = threading.RLock()
        self._flowfiles_in: int = 0
        self._flowfiles_out: int = 0
        # Track enqueue timestamps for TTL
        self._enqueue_times: Dict[str, float] = {}

    def enqueue(self, flowfile: FlowFile) -> bool:
        """Add a FlowFile to the connection queue.
        Returns False if backpressure threshold reached."""
        with self._lock:
            content_size = flowfile.size()
            if self._total_bytes + content_size > self.max_queue_bytes:
                return False
            if not self._queue.put(flowfile):
                return False
            self._total_bytes += content_size
            self._flowfiles_in += 1
            if self.flowfile_ttl_seconds > 0:
                self._enqueue_times[flowfile.process_id] = time.time()
            return True

    def dequeue(self) -> Optional[FlowFile]:
        """Get next FlowFile from the queue."""
        with self._lock:
            ff = self._queue.get()
            if ff:
                self._total_bytes -= ff.size()
                self._flowfiles_out += 1
                self._enqueue_times.pop(ff.process_id, None)
            return ff

    def is_expired(self, flowfile: FlowFile) -> bool:
        """Check if a FlowFile has exceeded its TTL in this queue."""
        if self.flowfile_ttl_seconds <= 0:
            return False
        enqueue_time = self._enqueue_times.get(flowfile.process_id)
        if enqueue_time is None:
            return False
        return (time.time() - enqueue_time) > self.flowfile_ttl_seconds

    def drain_expired(self) -> List[FlowFile]:
        """Remove and return all expired FlowFiles from the queue.

        Expired FlowFiles get 'expired'='true' attribute set.
        This is O(n) and should be called periodically, not on every dequeue.
        """
        if self.flowfile_ttl_seconds <= 0:
            return []

        expired = []
        remaining = []
        now = time.time()

        with self._lock:
            # Drain the queue
            while not self._queue.is_empty():
                ff = self._queue.get()
                if ff:
                    enqueue_time = self._enqueue_times.get(ff.process_id, now)
                    if (now - enqueue_time) > self.flowfile_ttl_seconds:
                        self._total_bytes -= ff.size()
                        self._flowfiles_out += 1
                        self._enqueue_times.pop(ff.process_id, None)
                        ff.set_attribute("expired", "true")
                        ff.set_attribute("expired.connection",
                                         f"{self.source_id}->{self.target_id}")
                        expired.append(ff)
                    else:
                        remaining.append(ff)

            # Re-enqueue non-expired
            for ff in remaining:
                self._queue.put(ff)

        return expired

    def peek(self) -> Optional[FlowFile]:
        """Peek at next FlowFile without removing."""
        return self._queue.peek()

    def peek_all(self, limit: int = 100) -> List[FlowFile]:
        """Return up to `limit` FlowFiles without removing them."""
        return self._queue.peek_all(limit)

    def is_backpressured(self) -> bool:
        """Check if backpressure threshold is reached."""
        with self._lock:
            return (self._queue.size() >= self.max_queue_size or
                    self._total_bytes >= self.max_queue_bytes)

    def queue_size(self) -> int:
        """Number of FlowFiles in queue."""
        return self._queue.size()

    def queue_bytes(self) -> int:
        """Total bytes in queue."""
        with self._lock:
            return self._total_bytes

    def is_empty(self) -> bool:
        return self._queue.is_empty()

    def remove(self, flowfile: FlowFile) -> bool:
        """Remove a specific FlowFile from the queue. Returns True on success."""
        with self._lock:
            items = self._queue._items
            for i, ff in enumerate(items):
                if ff is flowfile:
                    items.pop(i)
                    self._total_bytes -= ff.size()
                    self._flowfiles_out += 1
                    self._enqueue_times.pop(ff.process_id, None)
                    return True
            return False

    def remove_by_index(self, index: int) -> bool:
        """Remove a FlowFile by its position in the queue. Returns True on success."""
        with self._lock:
            items = self._queue._items
            if 0 <= index < len(items):
                ff = items.pop(index)
                self._total_bytes -= ff.size()
                self._flowfiles_out += 1
                self._enqueue_times.pop(ff.process_id, None)
                return True
            return False

    def clear(self):
        """Drop all queued FlowFiles."""
        with self._lock:
            self._queue.clear()
            self._total_bytes = 0
            self._enqueue_times.clear()

    def get_stats(self) -> Dict[str, Any]:
        """Get connection statistics."""
        with self._lock:
            return {
                "source": self.source_id,
                "target": self.target_id,
                "relationship": self.relationship,
                "queue_size": self._queue.size(),
                "queue_bytes": self._total_bytes,
                "max_queue_size": self.max_queue_size,
                "max_queue_bytes": self.max_queue_bytes,
                "backpressured": self.is_backpressured(),
                "flowfiles_in": self._flowfiles_in,
                "flowfiles_out": self._flowfiles_out,
                "ttl_seconds": self.flowfile_ttl_seconds,
            }

    def __repr__(self):
        return (f"Connection({self.source_id} -> {self.target_id} "
                f"[{self.relationship}], queue={self._queue.size()})")


class ConnectionManager:
    """Manages all connections in a flow."""

    def __init__(self):
        self._connections: List[Connection] = []
        self._by_source: Dict[str, List[Connection]] = {}
        self._by_target: Dict[str, List[Connection]] = {}

    def add_connection(self, connection: Connection):
        """Register a connection."""
        self._connections.append(connection)
        self._by_source.setdefault(connection.source_id, []).append(connection)
        self._by_target.setdefault(connection.target_id, []).append(connection)

    def get_outgoing(self, task_id: str) -> List[Connection]:
        """Get all outgoing connections from a task."""
        return self._by_source.get(task_id, [])

    def get_incoming(self, task_id: str) -> List[Connection]:
        """Get all incoming connections to a task."""
        return self._by_target.get(task_id, [])

    def get_all_stats(self) -> List[Dict[str, Any]]:
        """Get stats for all connections."""
        return [c.get_stats() for c in self._connections]

    def any_backpressured(self, task_id: str) -> bool:
        """Check if any outgoing connection from task is backpressured."""
        for conn in self.get_outgoing(task_id):
            if conn.is_backpressured():
                return True
        return False

    def get_connection(self, source_id: str, target_id: str) -> Optional[Connection]:
        """Find a specific connection by source and target."""
        for conn in self._connections:
            if conn.source_id == source_id and conn.target_id == target_id:
                return conn
        return None

    @property
    def connections(self) -> List[Connection]:
        """All registered connections."""
        return list(self._connections)

    def all_empty(self) -> bool:
        """Check if all connection queues are empty."""
        return all(conn.is_empty() for conn in self._connections)

    def clear_all(self):
        """Clear all connection queues."""
        for conn in self._connections:
            conn.clear()

    def build_from_flow(self, flow_dict: Dict[str, Any],
                        default_max_size: int = 10000,
                        default_max_bytes: int = 100 * 1024 * 1024):
        """Build connections from a flow dictionary."""
        self._connections.clear()
        self._by_source.clear()
        self._by_target.clear()

        for rel in flow_dict.get("relations", []):
            conn = Connection(
                source_id=rel["from"],
                target_id=rel["to"],
                relationship=rel.get("type", "success"),
                max_queue_size=default_max_size,
                max_queue_bytes=default_max_bytes,
            )
            self.add_connection(conn)

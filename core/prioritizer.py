"""FlowFile prioritizer system for queue ordering.

Supports FIFO, time-based, and priority-attribute ordering.
Priority convention: higher number = more urgent (10 = urgent, 0 = normal, -5 = low).
"""

import threading
from collections import defaultdict, deque
from enum import Enum
from typing import List, Optional

from core import FlowFile


class PrioritizerType(Enum):
    FIFO = "fifo"
    NEWEST_FIRST = "newest_first"
    OLDEST_FIRST = "oldest_first"
    PRIORITY_ATTRIBUTE = "priority_attribute"


class PrioritizedQueue:
    """Thread-safe priority queue for FlowFiles.

    When using PRIORITY_ATTRIBUTE mode, FlowFiles are grouped by priority
    level. Within the same priority level, FIFO order is preserved.
    Higher priority numbers are dequeued first.
    """

    def __init__(self, prioritizer_type: PrioritizerType = PrioritizerType.FIFO,
                 max_size: int = 10000, priority_attribute: str = "priority"):
        self._type = prioritizer_type
        self._max_size = max_size
        self._priority_attribute = priority_attribute
        self._lock = threading.Lock()

        if self._type == PrioritizerType.PRIORITY_ATTRIBUTE:
            # Multi-deque: priority_level → FIFO deque
            self._priority_queues: dict = defaultdict(deque)
            self._total: int = 0
            self._items: Optional[List[FlowFile]] = None  # not used
        else:
            self._items: List[FlowFile] = []
            self._priority_queues = None
            self._total = 0

    def _get_ff_priority(self, ff: FlowFile) -> int:
        val = ff.get_attribute(self._priority_attribute)
        try:
            return int(val) if val else 0
        except (ValueError, TypeError):
            return 0

    def put(self, flowfile: FlowFile) -> bool:
        """Add a FlowFile. Returns False if full (backpressure)."""
        with self._lock:
            if self._type == PrioritizerType.PRIORITY_ATTRIBUTE:
                if self._total >= self._max_size:
                    return False
                prio = self._get_ff_priority(flowfile)
                self._priority_queues[prio].append(flowfile)
                self._total += 1
                return True
            else:
                if len(self._items) >= self._max_size:
                    return False
                self._items.append(flowfile)
                return True

    def get(self) -> Optional[FlowFile]:
        """Get next FlowFile according to priority (higher first, FIFO within)."""
        with self._lock:
            if self._type == PrioritizerType.PRIORITY_ATTRIBUTE:
                if self._total == 0:
                    return None
                # Highest priority first
                for prio in sorted(self._priority_queues.keys(), reverse=True):
                    q = self._priority_queues[prio]
                    if q:
                        self._total -= 1
                        ff = q.popleft()
                        if not q:
                            del self._priority_queues[prio]
                        return ff
                return None

            if not self._items:
                return None

            if self._type == PrioritizerType.FIFO:
                return self._items.pop(0)
            elif self._type == PrioritizerType.NEWEST_FIRST:
                self._items.sort(
                    key=lambda ff: ff.get_attribute("timestamp") or "",
                    reverse=True
                )
                return self._items.pop(0)
            elif self._type == PrioritizerType.OLDEST_FIRST:
                self._items.sort(
                    key=lambda ff: ff.get_attribute("timestamp") or ""
                )
                return self._items.pop(0)
            return self._items.pop(0)

    def peek(self) -> Optional[FlowFile]:
        """Peek at the next FlowFile (highest priority) without removing."""
        with self._lock:
            if self._type == PrioritizerType.PRIORITY_ATTRIBUTE:
                for prio in sorted(self._priority_queues.keys(), reverse=True):
                    q = self._priority_queues[prio]
                    if q:
                        return q[0]
                return None
            return self._items[0] if self._items else None

    def peek_all(self, limit: int = 100) -> List[FlowFile]:
        """Return up to `limit` FlowFiles ordered by priority then FIFO."""
        with self._lock:
            if self._type == PrioritizerType.PRIORITY_ATTRIBUTE:
                result = []
                for prio in sorted(self._priority_queues.keys(), reverse=True):
                    for ff in self._priority_queues[prio]:
                        result.append(ff)
                        if len(result) >= limit:
                            return result
                return result
            return list(self._items[:limit])

    def remove(self, flowfile: FlowFile) -> bool:
        """Remove a specific FlowFile (for selective dequeue)."""
        with self._lock:
            if self._type == PrioritizerType.PRIORITY_ATTRIBUTE:
                for prio in list(self._priority_queues.keys()):
                    q = self._priority_queues.get(prio)
                    if not q:
                        continue
                    try:
                        q.remove(flowfile)
                        self._total -= 1
                        if not q:
                            del self._priority_queues[prio]
                        return True
                    except ValueError:
                        continue
                return False
            try:
                self._items.remove(flowfile)
                return True
            except ValueError:
                return False

    def size(self) -> int:
        with self._lock:
            if self._type == PrioritizerType.PRIORITY_ATTRIBUTE:
                return self._total
            return len(self._items)

    def is_full(self) -> bool:
        with self._lock:
            if self._type == PrioritizerType.PRIORITY_ATTRIBUTE:
                return self._total >= self._max_size
            return len(self._items) >= self._max_size

    def is_empty(self) -> bool:
        with self._lock:
            if self._type == PrioritizerType.PRIORITY_ATTRIBUTE:
                return self._total == 0
            return len(self._items) == 0

    def clear(self):
        with self._lock:
            if self._type == PrioritizerType.PRIORITY_ATTRIBUTE:
                self._priority_queues.clear()
                self._total = 0
            else:
                self._items.clear()

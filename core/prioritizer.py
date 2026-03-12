"""FlowFile prioritizer system for queue ordering."""

import threading
from enum import Enum
from typing import List, Optional

from core import FlowFile


class PrioritizerType(Enum):
    FIFO = "fifo"
    NEWEST_FIRST = "newest_first"
    OLDEST_FIRST = "oldest_first"
    PRIORITY_ATTRIBUTE = "priority_attribute"


class PrioritizedQueue:
    """Thread-safe priority queue for FlowFiles."""

    def __init__(self, prioritizer_type: PrioritizerType = PrioritizerType.FIFO,
                 max_size: int = 10000, priority_attribute: str = "priority"):
        self._type = prioritizer_type
        self._max_size = max_size
        self._priority_attribute = priority_attribute
        self._items: List[FlowFile] = []
        self._lock = threading.Lock()

    def put(self, flowfile: FlowFile) -> bool:
        """Add a FlowFile. Returns False if full (backpressure)."""
        with self._lock:
            if len(self._items) >= self._max_size:
                return False
            self._items.append(flowfile)
            return True

    def get(self) -> Optional[FlowFile]:
        """Get next FlowFile according to priority."""
        with self._lock:
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

            elif self._type == PrioritizerType.PRIORITY_ATTRIBUTE:
                def _get_priority(ff):
                    val = ff.get_attribute(self._priority_attribute)
                    try:
                        return int(val) if val else 999999
                    except (ValueError, TypeError):
                        return 999999
                self._items.sort(key=_get_priority)
                return self._items.pop(0)

            return self._items.pop(0)

    def peek(self) -> Optional[FlowFile]:
        """Peek at the next FlowFile without removing it."""
        with self._lock:
            return self._items[0] if self._items else None

    def peek_all(self, limit: int = 100) -> List[FlowFile]:
        """Return up to `limit` FlowFiles without removing them."""
        with self._lock:
            return list(self._items[:limit])

    def size(self) -> int:
        with self._lock:
            return len(self._items)

    def is_full(self) -> bool:
        with self._lock:
            return len(self._items) >= self._max_size

    def is_empty(self) -> bool:
        with self._lock:
            return len(self._items) == 0

    def clear(self):
        with self._lock:
            self._items.clear()

from __future__ import annotations
from typing import ClassVar, Optional, List, Dict
import threading
from datetime import datetime


class BulletinBoard:
    """
    Singleton bulletin board for tasks to post messages during execution.
    Thread-safe in-memory message store.
    """

    _instance: ClassVar[Optional['BulletinBoard']] = None
    _lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self):
        self.messages: List[Dict] = []
        self._message_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> 'BulletinBoard':
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def post(self, level: str, source: str, message: str) -> None:
        entry = {
            "level": level,
            "source": source,
            "message": message,
            "timestamp": datetime.now().isoformat(),
        }
        with self._message_lock:
            self.messages.append(entry)

    def get_messages(self, limit: int = 100, level: str = None) -> List[Dict]:
        with self._message_lock:
            msgs = self.messages[:]

        if level is not None:
            msgs = [m for m in msgs if m["level"] == level]

        # Newest first, limited
        return list(reversed(msgs))[:limit]

    def clear(self) -> None:
        with self._message_lock:
            self.messages.clear()

    def count_by_level(self) -> Dict[str, int]:
        counts: Dict[str, int] = {"INFO": 0, "WARNING": 0, "ERROR": 0}
        with self._message_lock:
            for msg in self.messages:
                level = msg["level"]
                if level in counts:
                    counts[level] += 1
        return counts

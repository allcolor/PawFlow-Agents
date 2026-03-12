"""TokenTracker — Track LLM token usage per user.

Simple JSON-file-based tracking of input/output tokens per user.
No rate limiting by default — just counting for visibility.
"""

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_DEFAULT_PATH = "data/token_usage.json"


class TokenTracker:
    """Singleton tracker for LLM token usage."""

    _instance: Optional["TokenTracker"] = None
    _lock = threading.Lock()

    def __init__(self, path: str = ""):
        self._path = Path(path or _DEFAULT_PATH)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._data: Dict[str, Dict[str, Any]] = {}
        self._store_lock = threading.Lock()
        self._loaded = False
        self._dirty = False

    @classmethod
    def instance(cls) -> "TokenTracker":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls):
        with cls._lock:
            cls._instance = None

    def track(self, user_id: str, tokens_in: int, tokens_out: int,
              model: str = ""):
        """Record token usage for a user."""
        with self._store_lock:
            self._ensure_loaded()
            entry = self._data.setdefault(user_id, {
                "total_in": 0, "total_out": 0,
                "daily": {}, "models": {},
            })
            entry["total_in"] += tokens_in
            entry["total_out"] += tokens_out

            # Daily tracking
            today = time.strftime("%Y-%m-%d")
            day = entry["daily"].setdefault(today, {"in": 0, "out": 0})
            day["in"] += tokens_in
            day["out"] += tokens_out

            # Per-model tracking
            if model:
                m = entry["models"].setdefault(model, {"in": 0, "out": 0})
                m["in"] += tokens_in
                m["out"] += tokens_out

            self._dirty = True

    def get_usage(self, user_id: str) -> Dict[str, Any]:
        """Get usage stats for a user."""
        with self._store_lock:
            self._ensure_loaded()
            entry = self._data.get(user_id)
            if not entry:
                return {"total_in": 0, "total_out": 0, "daily": {}, "models": {}}
            return dict(entry)

    def get_all_usage(self) -> Dict[str, Dict[str, Any]]:
        """Get usage for all users (admin)."""
        with self._store_lock:
            self._ensure_loaded()
            return dict(self._data)

    def flush(self):
        """Write to disk if dirty."""
        with self._store_lock:
            if not self._dirty:
                return
            self._save()
            self._dirty = False

    def _ensure_loaded(self):
        if self._loaded:
            return
        self._loaded = True
        if self._path.exists():
            try:
                self._data = json.loads(
                    self._path.read_text(encoding="utf-8")
                )
            except Exception as e:
                logger.warning(f"Failed to load token usage: {e}")

    def _save(self):
        try:
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(self._data, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp.replace(self._path)
        except Exception as e:
            logger.error(f"Failed to save token usage: {e}")

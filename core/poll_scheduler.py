"""Persistent poll scheduler for agent conversations.

Stores scheduled rechecks on disk so they survive process restarts.
The agent can schedule future rechecks via the ``schedule_recheck`` tool
or via the ``[RECHECK_IN:X]`` tag convention.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("openpaw.poll_scheduler")

_DATA_DIR = os.path.join("data", "poll_schedule")
_SCHEDULE_FILE = os.path.join(_DATA_DIR, "schedule.json")


class PollScheduler:
    """Singleton persistent scheduler for agent conversation rechecks.

    Each entry: ``{conversation_id, recheck_at (epoch), user_id, reason}``.
    Entries are stored as a JSON file and loaded on startup.
    """

    _instance: Optional["PollScheduler"] = None
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "PollScheduler":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset singleton (for tests)."""
        cls._instance = None

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._schedules: Dict[str, Dict[str, Any]] = {}  # keyed by conversation_id
        self._load()

    # ── Public API ──────────────────────────────────────────────────

    def schedule(
        self,
        conversation_id: str,
        recheck_at: float,
        user_id: str = "",
        reason: str = "",
        key: str = "",
    ) -> None:
        """Schedule or update a recheck for a conversation.

        Args:
            conversation_id: The conversation to recheck.
            recheck_at: Unix epoch timestamp when the recheck is due.
            user_id: Owner of the conversation.
            reason: Human-readable reason (e.g., "check stock price").
            key: Custom key (default: conversation_id). Allows multiple
                 schedules per conversation (e.g. ``conv::thought::agent``).
        """
        actual_key = key or conversation_id
        with self._lock:
            self._schedules[actual_key] = {
                "conversation_id": conversation_id,
                "key": actual_key,
                "recheck_at": recheck_at,
                "user_id": user_id,
                "reason": reason,
                "created_at": time.time(),
            }
            self._save()
        logger.info(
            f"[poll_scheduler] Scheduled recheck for {conversation_id[:8]} "
            f"at {datetime.fromtimestamp(recheck_at, tz=timezone.utc).isoformat()} "
            f"(in {int(recheck_at - time.time())}s) — {reason or 'no reason'}"
        )

    def schedule_delay(
        self,
        conversation_id: str,
        delay_seconds: int,
        user_id: str = "",
        reason: str = "",
        key: str = "",
    ) -> float:
        """Schedule a recheck N seconds from now. Returns the recheck_at epoch."""
        recheck_at = time.time() + delay_seconds
        self.schedule(conversation_id, recheck_at, user_id, reason, key=key)
        return recheck_at

    def cancel(self, conversation_id: str) -> bool:
        """Cancel a scheduled recheck. Returns True if it existed."""
        with self._lock:
            if conversation_id in self._schedules:
                del self._schedules[conversation_id]
                self._save()
                logger.info(f"[poll_scheduler] Cancelled recheck for {conversation_id[:8]}")
                return True
        return False

    def get_due(self) -> List[Dict[str, Any]]:
        """Return all entries whose recheck_at <= now, removing them from schedule."""
        now = time.time()
        due: List[Dict[str, Any]] = []
        with self._lock:
            expired_keys = [
                k for k, v in self._schedules.items()
                if v["recheck_at"] <= now
            ]
            for k in expired_keys:
                due.append(self._schedules.pop(k))
            if expired_keys:
                self._save()
        return due

    def get(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """Get the scheduled recheck for a conversation (if any)."""
        with self._lock:
            return self._schedules.get(conversation_id)

    def list_all(self) -> List[Dict[str, Any]]:
        """Return all scheduled rechecks (for debugging/UI)."""
        with self._lock:
            return list(self._schedules.values())

    # ── Persistence ─────────────────────────────────────────────────

    def _load(self) -> None:
        if not os.path.exists(_SCHEDULE_FILE):
            return
        try:
            with open(_SCHEDULE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                for entry in data:
                    actual_key = entry.get("key") or entry.get("conversation_id")
                    if actual_key:
                        self._schedules[actual_key] = entry
            logger.info(f"[poll_scheduler] Loaded {len(self._schedules)} scheduled rechecks")
        except Exception as e:
            logger.error(f"[poll_scheduler] Failed to load schedule: {e}")

    def _save(self) -> None:
        try:
            os.makedirs(_DATA_DIR, exist_ok=True)
            with open(_SCHEDULE_FILE, "w", encoding="utf-8") as f:
                json.dump(list(self._schedules.values()), f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"[poll_scheduler] Failed to save schedule: {e}")

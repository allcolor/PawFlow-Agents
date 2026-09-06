"""Persistent poll scheduler for agent conversations.

Stores scheduled wake-ups on disk so they survive process restarts.
The agent can schedule future wake-ups via the ``ScheduleWakeup`` tool
(replaces the Claude Code built-in of the same name).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("pawflow.poll_scheduler")

import core.paths as _paths


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
        # Only the most recent poll batch may put consumed entries back. Keep
        # the original objects as claims, invalidated by cancellation/replacement.
        self._due_entries: Dict[str, Dict[str, Any]] = {}
        self._due_cancelled_keys: set[str] = set()
        self._due_cancel_filters: list = []
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
            self._due_entries.pop(actual_key, None)
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

    def schedule_loop(
        self, conversation_id: str, interval_seconds: int,
        prompt: str = "", user_id: str = "", key: str = "",
    ) -> str:
        """Schedule a recurring prompt loop. Returns the loop key."""
        import hashlib
        prompt_hash = hashlib.md5(prompt.encode(), usedforsecurity=False).hexdigest()[:6]
        loop_key = key or f"loop::{conversation_id}::{prompt_hash}"
        recheck_at = time.time() + interval_seconds
        with self._lock:
            self._due_entries.pop(loop_key, None)
            self._schedules[loop_key] = {
                "conversation_id": conversation_id,
                "key": loop_key,
                "recheck_at": recheck_at,
                "user_id": user_id,
                "reason": f"[loop] {prompt[:60]}",
                "created_at": time.time(),
                "recurring": True,
                "interval_seconds": interval_seconds,
                "prompt": prompt,
            }
            self._save()
        logger.info(f"[poll_scheduler] Loop started: {loop_key} every {interval_seconds}s")
        return loop_key

    def cancel(self, key: str) -> bool:
        """Cancel a scheduled recheck by key. Returns True if it existed."""
        with self._lock:
            if self._due_entries:
                self._due_cancelled_keys.add(key)
            claimed = self._due_entries.pop(key, None)
            if key in self._schedules:
                del self._schedules[key]
                self._save()
                logger.info(f"[poll_scheduler] Cancelled: {key}")
                return True
        return claimed is not None

    def cancel_for_conversation(
        self,
        conversation_id: str,
        key_prefixes: Optional[List[str]] = None,
        reason_prefixes: Optional[List[str]] = None,
    ) -> int:
        """Cancel selected schedules for one conversation.

        Prefix filters keep force stop from disabling unrelated task or
        random-thought schedules.
        """
        if not conversation_id:
            return 0
        with self._lock:
            removed = []
            if self._due_entries:
                self._due_cancel_filters.append((
                    conversation_id, tuple(key_prefixes or ()),
                    tuple(reason_prefixes or ())))
            candidates = {**self._due_entries, **self._schedules}
            for key, entry in candidates.items():
                if entry.get("conversation_id") != conversation_id:
                    continue
                reason = entry.get("reason", "") or ""
                key_match = (not key_prefixes or any(
                    key.startswith(prefix) for prefix in key_prefixes))
                reason_match = (not reason_prefixes or any(
                    reason.startswith(prefix) for prefix in reason_prefixes))
                if key_match and reason_match:
                    removed.append(key)
            disk_changed = False
            for key in removed:
                self._due_entries.pop(key, None)
                disk_changed = self._schedules.pop(key, None) is not None or disk_changed
            if disk_changed:
                self._save()
        for key in removed:
            logger.info("[poll_scheduler] Cancelled: %s", key)
        return len(removed)

    def list_loops(self, conversation_id: str = "") -> list:
        """List active recurring loops, optionally filtered by conversation."""
        with self._lock:
            return [
                v for v in self._schedules.values()
                if v.get("recurring")
                and (not conversation_id or v.get("conversation_id") == conversation_id)
            ]

    def get_due(self) -> List[Dict[str, Any]]:
        """Return all entries whose recheck_at <= now, removing them from schedule.

        Recurring entries are automatically re-scheduled. The single poller may
        defer this batch through reschedule_due until its next get_due call.
        """
        now = time.time()
        due: List[Dict[str, Any]] = []
        with self._lock:
            self._due_entries.clear()
            self._due_cancelled_keys.clear()
            self._due_cancel_filters.clear()
            expired_keys = [
                k for k, v in self._schedules.items()
                if v["recheck_at"] <= now
            ]
            for k in expired_keys:
                entry = self._schedules.pop(k)
                self._due_entries[k] = entry
                due.append(entry)
                # Re-schedule recurring entries
                if entry.get("recurring") and entry.get("interval_seconds"):
                    next_at = now + entry["interval_seconds"]
                    self._schedules[k] = {
                        **entry,
                        "recheck_at": next_at,
                        "created_at": now,
                    }
                    logger.info(f"[poll_scheduler] Re-scheduled recurring {k} "
                                f"in {entry['interval_seconds']}s")
            if expired_keys:
                self._save()
        return due

    def reschedule_due(self, retries: List[tuple[Dict[str, Any], str, float]]) -> int:
        """Persist one batch of (original due entry, target key, delay) retries.

        Activity decisions happen outside this lock. Cancellation, replacement,
        or a later poll invalidates their claims, so moving persistence out of
        the activity lock cannot resurrect stale work. Claims never reach disk.
        """
        updated = 0
        now = time.time()
        with self._lock:
            for entry, key, delay in retries:
                original_key = entry.get("key") or entry["conversation_id"]
                if self._due_entries.get(original_key) is not entry:
                    continue
                self._due_entries.pop(original_key)
                if key in self._due_cancelled_keys or any(
                        cid == entry["conversation_id"]
                        and (not prefixes or any(key.startswith(p) for p in prefixes))
                        and (not reasons or any(
                            (entry.get("reason") or "").startswith(p) for p in reasons))
                        for cid, prefixes, reasons in self._due_cancel_filters):
                    continue
                if key != original_key and key in self._schedules:
                    # A separate wake now owns the destination. Do not replace
                    # its reason, owner or deadline with this older decision.
                    continue
                current = self._schedules.get(key, {})
                self._schedules[key] = {
                    **current,
                    "conversation_id": entry["conversation_id"],
                    "key": key,
                    "recheck_at": now + delay,
                    "user_id": entry.get("user_id", ""),
                    "reason": entry.get("reason", ""),
                    "created_at": now,
                }
                updated += 1
            if updated:
                self._save()
        return updated

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
        if not os.path.exists(str(_paths.POLL_SCHEDULE_FILE)):
            return
        try:
            with open(str(_paths.POLL_SCHEDULE_FILE), "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                now = time.time()
                loaded = 0
                stale = 0
                for entry in data:
                    actual_key = entry.get("key") or entry.get("conversation_id")
                    if not actual_key:
                        continue
                    # Purge entries that are very old (>24h past due)
                    recheck_at = entry.get("recheck_at", 0)
                    if recheck_at and recheck_at < now - 86400:
                        stale += 1
                        continue
                    # Purge entries with no conversation_id
                    if not entry.get("conversation_id"):
                        stale += 1
                        continue
                    self._schedules[actual_key] = entry
                    loaded += 1
                if stale:
                    self._save()  # persist the cleanup
                    logger.info(f"[poll_scheduler] Purged {stale} stale entries")
            logger.info(f"[poll_scheduler] Loaded {len(self._schedules)} scheduled rechecks")
        except Exception as e:
            logger.error(f"[poll_scheduler] Failed to load schedule: {e}")

    def _save(self) -> None:
        try:
            os.makedirs(str(_paths.POLL_SCHEDULE_FILE.parent), exist_ok=True)
            with open(str(_paths.POLL_SCHEDULE_FILE), "w", encoding="utf-8") as f:
                json.dump(list(self._schedules.values()), f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"[poll_scheduler] Failed to save schedule: {e}")

"""Pending message queue — per-(conversation, agent), backed by the inbox store.

The single source of truth for "messages an agent must see on its
next turn but hasn't consumed yet".

Every ingress that wants an agent to react does three things:
  1. stamp + persist the message to transcript (history)
  2. PendingQueue.for_agent(conv, agent).enqueue(msg, source="...")
  3. wake_agent(conv, agent)

Storage: every entry is a row of the durable SQLite inbox
(core/agent_inbox_store.py). Entries whose source starts with ``inbox:``
are inbox items (core/agent_inbox.py): ``drain``/``peek``/``peek_count``
never see them, only ``read_inbox`` consumes them. ``clear`` (force stop)
discards both kinds.

Design choices:
- Singleton per (conv, agent) so a lock is shared across all code
  paths for that queue.
- Queue stores the full stamped message dict, not a FlowFile — this
  makes boot recovery trivial (already-stamped message dicts, no
  FlowFile reconstruction).
- Force stop is a durable creation-time fence. The store checks the fence
  and inserts in one SQLite transaction, including writes from other producers.
- One-shot migration: a legacy ``<conv_dir>/<agent>/pending.jsonl`` is
  imported into the store the first time its queue is used (or by the boot
  scan in ``all_nonempty``) and renamed to ``pending.jsonl.migrated``.
"""

import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Same literal as core.agent_inbox.INBOX_SOURCE_PREFIX (that module imports
# this one, so the constant is duplicated rather than imported).
INBOX_SOURCE_PREFIX = "inbox:"
_LEGACY_FILE = "pending.jsonl"
# Rows claimed per store round trip while draining (bounded SQL variables).
_DRAIN_BATCH = 500
_PEEK_LIMIT = 100000


def _inbox_store():
    from core.agent_inbox_store import AgentInboxStore
    return AgentInboxStore.instance()


class PendingQueue:
    """Per-(conv, agent) queue of pending messages, persisted in SQLite."""

    _instances: Dict[Tuple[str, str], "PendingQueue"] = {}
    _instances_lock = threading.Lock()

    def __init__(self, conv_id: str, agent_name: str):
        self.conv_id = conv_id
        self.agent_name = agent_name
        self._lock = threading.Lock()
        self._migration_lock = threading.Lock()
        self._legacy_checked = False
        self._path = self._resolve_path()

    def _store(self):
        """The inbox store, after importing a legacy JSONL queue once."""
        store = _inbox_store()
        if not self._legacy_checked:
            with self._migration_lock:
                if not self._legacy_checked:
                    self._migrate_legacy(store)
                    self._legacy_checked = True
        return store

    def _migrate_legacy(self, store) -> dict:
        """Import this queue's legacy pending.jsonl and rename it."""
        if self._path is None:
            self._path = self._resolve_path()
        if self._path is None or not self._path.exists():
            previous = store.migration_status(self.conv_id, self.agent_name)
            if previous is not None:
                return {
                    "migrated": False,
                    "count": int(previous["item_count"]),
                    "sha256": previous["source_sha256"],
                }
            return {"migrated": False, "count": 0, "sha256": ""}
        result = store.migrate_pending_jsonl(
            self.conv_id, self.agent_name, self._path)
        # An unchanged file already recorded as migrated (crash before the
        # rename) is renamed too, so it is never scanned again.
        self._path.replace(self._path.with_suffix(".jsonl.migrated"))
        logger.info("[pending-queue] migrated %d legacy message(s) for %s/%s",
                    result["count"], self.conv_id[:8], self.agent_name)
        return result

    @classmethod
    def migrate_agent_to_inbox(cls, conv_id: str, agent_name: str):
        """One-shot import of the legacy JSONL queue of (conv, agent)."""
        queue = cls.for_agent(conv_id, agent_name)
        with queue._migration_lock:
            result = queue._migrate_legacy(_inbox_store())
            queue._legacy_checked = True
        return result

    @classmethod
    def for_agent(cls, conv_id: str, agent_name: str) -> "PendingQueue":
        if not str(conv_id or "").strip() or not str(agent_name or "").strip():
            raise ValueError("conversation_id and agent_name are required")
        conv_id = str(conv_id).strip()
        agent_name = str(agent_name).strip().lower()
        key = (conv_id, agent_name)
        with cls._instances_lock:
            q = cls._instances.get(key)
            if q is None:
                q = cls(conv_id, agent_name)
                cls._instances[key] = q
            return q

    @classmethod
    def drop_cache(cls):
        """Test helper: clear the singleton cache."""
        with cls._instances_lock:
            cls._instances.clear()

    def _resolve_path(self) -> Optional[Path]:
        """Return the legacy JSONL path of this queue. None if conv doesn't exist."""
        if not self.agent_name:
            return None
        try:
            from core.conversation_store import ConversationStore
            conv_dir = ConversationStore.instance()._conv_dir(self.conv_id)
            return (conv_dir / ConversationStore._canon_agent(self.agent_name)
                    / _LEGACY_FILE)
        except Exception as e:
            logger.debug("[pending-queue] cannot resolve path for %s/%s: %s",
                         self.conv_id[:8], self.agent_name, e)
            return None

    # ── Writes ──────────────────────────────────────────────────────

    def enqueue(self, message: Dict, source: str = "agent_msg") -> bool:
        """Store a stamped message in the queue.

        message must already have msg_id + ts (stamp_message or
        LLMMessage.__post_init__ guarantees this at the producer). seq
        is NOT required — it's the on-disk line index assigned by
        ConversationStore._stamp_line at write time, not at creation.
        source is a free-form tag for debugging ("http", "delegate",
        "bg_tool", "cross_agent", "telegram", ...); ``inbox:<kind>`` marks
        an inbox item.
        """
        if not isinstance(message, dict):
            raise TypeError("PendingQueue.enqueue: message must be a dict")
        if not message.get("msg_id") or (
                message.get("ts") is None and message.get("timestamp") is None):
            raise ValueError(
                f"PendingQueue.enqueue: message must be stamped "
                f"(msg_id+ts). Got keys: {list(message.keys())}")
        if not isinstance(source, str) or not source.strip():
            raise ValueError("source is required")
        source = source.strip()

        inbox = self._store()
        with self._lock:
            try:
                from core.conversation_store import ConversationStore
                store = ConversationStore.instance()
                global_cutoff = float(store.get_extra(
                    self.conv_id, "last_force_stop_at") or 0.0)
                agent_cutoff = float(store.get_extra(
                    self.conv_id,
                    f"last_force_stop_at:{self.agent_name.lower()}") or 0.0)
                cutoff = max(global_cutoff, agent_cutoff)
            except Exception:
                logger.debug("[pending-queue] force-stop cutoff lookup failed",
                             exc_info=True)
                cutoff = 0.0
            if cutoff:
                # Import the pre-migration metadata fence. The store owns
                # rejection, including ISO timestamps and concurrent writers.
                inbox.discard_through(self.conv_id, self.agent_name, cutoff)

            entry = dict(message)
            entry["_pending_source"] = source
            entry["_pending_enqueued_at"] = time.time()
            item = inbox.enqueue(
                self.conv_id, self.agent_name, entry,
                source)
            if item.state == "discarded":
                return False
        logger.info("[pending-queue] enqueued %s → %s/%s (source=%s)",
                    message.get("msg_id"), self.conv_id[:8],
                    self.agent_name or "_shared", source or "?")
        return True

    def drain(self) -> List[Dict]:
        """Consume and return every pending non-inbox message, oldest first.

        Inbox items stay pending: only ``read_inbox`` consumes them.
        """
        inbox = self._store()
        entries: List[Dict] = []
        while True:
            pending = inbox.list_pending(
                self.conv_id, self.agent_name,
                exclude_source_prefix=INBOX_SOURCE_PREFIX,
                limit=_DRAIN_BATCH)
            if not pending:
                break
            run_id = f"pending-drain:{uuid.uuid4().hex}"
            _claim, items = inbox.claim(
                self.conv_id, self.agent_name, run_id, "legacy_drain",
                max_messages=0, lease_seconds=60,
                include_msg_ids=[item.msg_id for item in pending])
            if not items:
                break
            inbox.acknowledge(
                self.conv_id, self.agent_name, run_id,
                [item.msg_id for item in items])
            entries.extend(dict(item.payload) for item in items)
            if len(pending) < _DRAIN_BATCH:
                break
        if entries:
            # Diagnostic: log age of each drained entry. Fresh entries
            # (<60s) are normal — messages that arrived during the just-
            # ended turn. Stale entries (>300s) indicate a leak where an
            # intermediate drain should have fired but didn't.
            _now = time.time()
            _ages = []
            for _e in entries:
                _enq = _e.get("_pending_enqueued_at")
                if isinstance(_enq, (int, float)):
                    _ages.append(int(_now - _enq))
            _age_summary = ",".join(f"{a}s" for a in _ages) if _ages else "?"
            logger.info("[pending-queue] drained %d message(s) from %s/%s "
                        "(ages=%s)",
                        len(entries), self.conv_id[:8],
                        self.agent_name or "_shared", _age_summary)
        return entries

    def clear(self, reason: str = "") -> int:
        """Drop all queued work (inbox items included), used by force stop."""
        inbox = self._store()
        with self._lock:
            removed = inbox.discard_through(
                self.conv_id, self.agent_name, time.time())
        if removed:
            logger.info("[pending-queue] cleared %d message(s) from %s/%s%s",
                        removed, self.conv_id[:8],
                        self.agent_name or "_shared",
                        f" ({reason})" if reason else "")
        return removed

    def discard_msg_ids(self, msg_ids, sources=None) -> int:
        """Remove queued entries whose msg_id is already represented elsewhere.

        Used after provider-triggered compaction: the compacted context is
        rebuilt from the flushed transcript, so matching pending entries would
        replay the same user turn after the new provider session starts.
        """
        ids = {str(mid) for mid in (msg_ids or []) if mid}
        source_set = {str(s) for s in (sources or []) if s} if sources else None
        removed = self._store().discard_msg_ids(
            self.conv_id, self.agent_name, ids, source_set)
        if removed:
            logger.info("[pending-queue] discarded %d already-compacted "
                        "message(s) from %s/%s",
                        removed, self.conv_id[:8],
                        self.agent_name or "_shared")
        return removed

    def peek_count(self) -> int:
        """How many non-inbox messages are waiting (no side effects)."""
        return len(self._pending())

    def peek(self) -> List[Dict]:
        """The waiting non-inbox messages, oldest first, without consuming them."""
        return [dict(item.payload) for item in self._pending()]

    def _pending(self):
        return self._store().list_pending(
            self.conv_id, self.agent_name,
            exclude_source_prefix=INBOX_SOURCE_PREFIX, limit=_PEEK_LIMIT)

    # ── Boot recovery ───────────────────────────────────────────────

    @classmethod
    def _migrate_legacy_files(cls) -> None:
        """Import every legacy pending.jsonl still on disk."""
        try:
            from core.conversation_store import ConversationStore
            root = ConversationStore.instance()._store_dir
        except Exception as e:
            logger.debug("[pending-queue] recovery scan failed: %s", e)
            return
        if not root.exists():
            return
        # Layout: root/{user}/{conv}/{agent}/pending.jsonl
        for user_dir in root.iterdir():
            if not user_dir.is_dir():
                continue
            for conv_dir in user_dir.iterdir():
                if not conv_dir.is_dir():
                    continue
                for sub in conv_dir.iterdir():
                    if not (sub / _LEGACY_FILE).is_file():
                        continue
                    if sub.name == "_shared":
                        logger.warning(
                            "[pending-queue] legacy queue without agent left "
                            "unmigrated: %s", sub / _LEGACY_FILE)
                        continue
                    try:
                        cls.migrate_agent_to_inbox(conv_dir.name, sub.name)
                    except Exception:
                        # Keep scanning: one bad file must not block the
                        # boot recovery of every other agent.
                        logger.error(
                            "[pending-queue] legacy migration failed for %s",
                            sub / _LEGACY_FILE, exc_info=True)

    @classmethod
    def all_nonempty(cls) -> List[Tuple[str, str, int]]:
        """Every (conv_id, agent_name, count) with pending actionable work.

        Used at startup to recover regular messages and actionable inbox
        items. Passive participant catch-up never wakes an idle agent.
        Legacy JSONL queues still on disk are migrated first.
        """
        cls._migrate_legacy_files()
        store = _inbox_store()
        store.purge_acknowledged(30 * 24 * 60 * 60)
        return list(store.list_recovery_queues())

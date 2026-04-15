"""Pending message queue — per-(conversation, agent), disk-backed.

The single source of truth for "messages an agent must see on its
next turn but hasn't consumed yet". Replaces the three overlapping
mechanisms (`_pending_user_msgs` in-memory dict, transcript scan via
`_last_known_msg_count`, and various ad-hoc injection paths).

Every ingress that wants an agent to react does three things:
  1. stamp + persist the message to transcript (history)
  2. PendingQueue.for_agent(conv, agent).enqueue(msg, source="...")
  3. wake_agent(conv, agent)

Disk layout:
  data/runtime/conversations/{cid}/{agent}/pending.jsonl

Design choices:
- Append on enqueue (one line per message). O(1), crash-safe — no
  partial write scenario leaves the file inconsistent because jsonl
  is line-oriented and truncated lines are simply skipped on read.
- Drain = read all + atomic truncate (write empty tmp, rename).
- Singleton per (conv, agent) so a lock is shared across all code
  paths for that queue.
- Queue stores the full stamped message dict, not a FlowFile — this
  makes boot recovery trivial (replay from disk = already-stamped
  message dicts, no FlowFile reconstruction).
"""

import json
import logging
import os
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class PendingQueue:
    """Per-(conv, agent) queue of pending messages, persisted to disk."""

    _instances: Dict[Tuple[str, str], "PendingQueue"] = {}
    _instances_lock = threading.Lock()

    def __init__(self, conv_id: str, agent_name: str):
        self.conv_id = conv_id
        self.agent_name = agent_name
        self._lock = threading.Lock()
        self._path = self._resolve_path()
        # Make sure parent dir exists (may be a fresh conv + agent)
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def for_agent(cls, conv_id: str, agent_name: str) -> "PendingQueue":
        key = (conv_id, (agent_name or "").lower())
        with cls._instances_lock:
            q = cls._instances.get(key)
            if q is None:
                q = cls(conv_id, agent_name or "")
                cls._instances[key] = q
            return q

    @classmethod
    def drop_cache(cls):
        """Test helper: clear the singleton cache."""
        with cls._instances_lock:
            cls._instances.clear()

    def _resolve_path(self) -> Optional[Path]:
        """Return disk path for this queue's jsonl. None if conv doesn't exist."""
        try:
            from core.conversation_store import ConversationStore
            conv_dir = ConversationStore.instance()._conv_dir(self.conv_id)
            safe_agent = self.agent_name or "_shared"
            # Reuse canonicalization — agent names are case-insensitive
            from core.conversation_store import ConversationStore as _CS
            safe_agent = _CS._canon_agent(self.agent_name) if self.agent_name else "_shared"
            return conv_dir / safe_agent / "pending.jsonl"
        except Exception as e:
            logger.debug("[pending-queue] cannot resolve path for %s/%s: %s",
                         self.conv_id[:8], self.agent_name, e)
            return None

    # ── Writes ──────────────────────────────────────────────────────

    def enqueue(self, message: Dict, source: str = "") -> bool:
        """Append a stamped message to the queue.

        message must already have msg_id + ts + seq (stamp_message or
        LLMMessage.__post_init__ guarantees this at the producer).
        source is a free-form tag for debugging ("http", "delegate",
        "bg_tool", "cross_agent", "telegram", …).
        """
        if not isinstance(message, dict):
            raise TypeError("PendingQueue.enqueue: message must be a dict")
        if not message.get("msg_id") or not (message.get("ts") or message.get("timestamp")) \
                or not message.get("seq"):
            raise ValueError(
                f"PendingQueue.enqueue: message must be stamped "
                f"(msg_id+ts+seq). Got keys: {list(message.keys())}")

        if self._path is None:
            self._path = self._resolve_path()
            if self._path is None:
                logger.warning("[pending-queue] cannot enqueue — conv %s has no dir",
                                self.conv_id[:8])
                return False
            self._path.parent.mkdir(parents=True, exist_ok=True)

        entry = dict(message)
        if source:
            entry["_pending_source"] = source

        with self._lock:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        logger.info("[pending-queue] enqueued %s → %s/%s (source=%s)",
                    message.get("msg_id"), self.conv_id[:8],
                    self.agent_name or "_shared", source or "?")
        return True

    def drain(self) -> List[Dict]:
        """Remove and return all pending messages. Atomic (read + truncate)."""
        if self._path is None or not self._path.exists():
            return []
        with self._lock:
            entries: List[Dict] = []
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entries.append(json.loads(line))
                        except Exception as e:
                            logger.warning("[pending-queue] skipping corrupt line "
                                            "in %s: %s", self._path, e)
            except FileNotFoundError:
                return []
            # Truncate — atomic via empty file replace. Keep the file so
            # next enqueue doesn't race with mkdir / parent detection.
            tmp = self._path.with_suffix(".jsonl.tmp")
            with open(tmp, "w", encoding="utf-8") as _fh:
                pass
            tmp.replace(self._path)
        if entries:
            logger.info("[pending-queue] drained %d message(s) from %s/%s",
                        len(entries), self.conv_id[:8],
                        self.agent_name or "_shared")
        return entries

    def peek_count(self) -> int:
        """How many messages are waiting (no side effects)."""
        if self._path is None or not self._path.exists():
            return 0
        with self._lock:
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    return sum(1 for line in f if line.strip())
            except FileNotFoundError:
                return 0

    # ── Boot recovery ───────────────────────────────────────────────

    @classmethod
    def all_nonempty(cls) -> List[Tuple[str, str, int]]:
        """Scan every conversation on disk for non-empty pending queues.

        Returns list of (conv_id, agent_name, count). Used at startup
        to wake agents that had pending messages when the server died.
        """
        try:
            from core.conversation_store import ConversationStore
            store = ConversationStore.instance()
            root = store._store_dir
        except Exception as e:
            logger.debug("[pending-queue] recovery scan failed: %s", e)
            return []
        if not root.exists():
            return []
        out: List[Tuple[str, str, int]] = []
        # Layout: root/{user}/{conv}/{agent}/pending.jsonl
        for user_dir in root.iterdir():
            if not user_dir.is_dir():
                continue
            for conv_dir in user_dir.iterdir():
                if not conv_dir.is_dir():
                    continue
                for sub in conv_dir.iterdir():
                    if not sub.is_dir():
                        continue
                    p = sub / "pending.jsonl"
                    if not p.exists():
                        continue
                    # Count non-empty lines
                    n = 0
                    try:
                        with open(p, "r", encoding="utf-8") as f:
                            for line in f:
                                if line.strip():
                                    n += 1
                    except Exception:
                        continue
                    if n > 0:
                        agent = sub.name if sub.name != "_shared" else ""
                        out.append((conv_dir.name, agent, n))
        return out

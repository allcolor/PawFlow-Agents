"""Passive memory recall — memories surface without the agent asking.

``recall`` and ``semantic_recall`` are tools: they only fire when the agent
decides to look, which means the agent has to already suspect that something
relevant exists. The static digest (:mod:`core.memory_digest`) is the opposite
extreme — top-N per category, identical whatever the user just said.

This module closes the gap: it embeds the turn the user just sent, searches the
memory store by similarity, and hands the hits to the next context build. Two
properties make it cheap enough to run on every turn:

* **Off the critical path.** The embedding and the search run in a daemon
  thread. The turn is never delayed, and a slow or unavailable embedding
  provider degrades to "no passive memories this turn", never to a stall.
* **One turn late.** Results computed while turn N is being answered are
  injected into turn N+1. In a conversation this is nearly free: the topic of
  turn N is almost always still the topic of turn N+1.

The rendered block rides the dynamic-metadata channel (merged into the last
user message, after all cache breakpoints), so it costs nothing in prompt-cache
terms — see ``docs/AGENT_SYSTEM.md``, *Prompt cache prefix*.
"""

from __future__ import annotations

import logging
import os
import threading
from collections import OrderedDict
from typing import Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: Hits below this cosine similarity are noise: with a small memory store,
#: *something* always scores above zero, and injecting an unrelated memory is
#: worse than injecting none.
MIN_SCORE = 0.4

#: Entries rendered at most, and characters per entry. The block competes with
#: the conversation for context, so it stays small on purpose.
DEFAULT_LIMIT = 5
MAX_ENTRY_CHARS = 220

#: Query text sent to the embedder. A long paste embeds to an average of
#: everything and matches nothing in particular.
MAX_QUERY_CHARS = 1000

#: Conversations holding a pending result at once.
MAX_TRACKED = 64

BLOCK_TITLE = "Memories relevant to this turn"


def recall_limit() -> int:
    """How many memories to surface per turn. 0 disables the feature.

    Reads ``PAWFLOW_PASSIVE_RECALL_LIMIT`` first, then ``passive_recall_limit``
    in ``global_parameters.json``, so it can be turned off from the UI without
    a restart.
    """
    raw = os.environ.get("PAWFLOW_PASSIVE_RECALL_LIMIT", "").strip()
    if not raw:
        try:
            from core.expression import _load_global_parameters
            raw = str(_load_global_parameters().get("passive_recall_limit", "")).strip()
        except Exception:
            logger.debug("Could not read passive_recall_limit", exc_info=True)
            raw = ""
    if not raw:
        return DEFAULT_LIMIT
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_LIMIT


def render(entries: List[Tuple[Any, float]], exclude: str = "") -> str:
    """Render scored memories as a compact block, skipping known ones.

    ``exclude`` is the static digest already being injected; a memory that is
    quoted there would otherwise appear twice in the same prompt.
    """
    lines: List[str] = []
    for entry, score in entries:
        text = (getattr(entry, "text", "") or "").strip()
        if not text:
            continue
        if exclude and text[:60] in exclude:
            continue
        if len(text) > MAX_ENTRY_CHARS:
            text = text[:MAX_ENTRY_CHARS - 1].rstrip() + "…"
        category = getattr(entry, "category", "") or ""
        prefix = f"[{category}] " if category else ""
        lines.append(f"- {prefix}{text} ({score:.2f})")
    return "\n".join(lines)


class PassiveRecall:
    """Per-conversation pending recall results, computed in the background."""

    _instance: Optional["PassiveRecall"] = None
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "PassiveRecall":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ready: OrderedDict = OrderedDict()   # key -> rendered block
        self._in_flight: set = set()               # keys currently computing

    @staticmethod
    def _key(user_id: str, conversation_id: str, agent_name: str) -> Tuple[str, str, str]:
        return (user_id or "", conversation_id or "", agent_name or "")

    def take(self, user_id: str, conversation_id: str, agent_name: str) -> str:
        """Return the block computed during the previous turn, and clear it.

        Cleared rather than kept so a stale block never outlives the topic that
        produced it: a turn with nothing ready simply gets no block.
        """
        with self._lock:
            return self._ready.pop(self._key(user_id, conversation_id, agent_name), "")

    def schedule(self, user_id: str, conversation_id: str, agent_name: str,
                 query_text: str, exclude: str = "") -> bool:
        """Start a background recall for this turn. Returns False when skipped.

        Skipped when the feature is off, the turn carries no text, or a recall
        for the same conversation is still running — piling threads up behind a
        slow embedder would help nobody.
        """
        limit = recall_limit()
        if limit <= 0 or not user_id or not (query_text or "").strip():
            return False

        key = self._key(user_id, conversation_id, agent_name)
        with self._lock:
            if key in self._in_flight:
                return False
            self._in_flight.add(key)

        thread = threading.Thread(
            target=self._run, name="passive-recall", daemon=True,
            args=(key, user_id, conversation_id, agent_name,
                  query_text[:MAX_QUERY_CHARS], exclude, limit))
        thread.start()
        return True

    def _run(self, key, user_id: str, conversation_id: str, agent_name: str,
             query_text: str, exclude: str, limit: int) -> None:
        try:
            block = self._compute(user_id, conversation_id, agent_name,
                                  query_text, exclude, limit)
        except Exception:
            # Never surface a failure into the turn: no memories is a fine
            # outcome, a broken turn is not.
            logger.debug("Passive recall failed", exc_info=True)
            block = ""
        finally:
            with self._lock:
                self._in_flight.discard(key)
        if not block:
            return
        with self._lock:
            self._ready[key] = block
            self._ready.move_to_end(key)
            while len(self._ready) > MAX_TRACKED:
                self._ready.popitem(last=False)

    @staticmethod
    def _compute(user_id: str, conversation_id: str, agent_name: str,
                 query_text: str, exclude: str, limit: int) -> str:
        from core.embeddings import build_memory_embed_fn
        from core.memory_store import MemoryStore

        embed = build_memory_embed_fn(user_id=user_id, conversation_id=conversation_id)
        vector = embed(query_text)
        if not vector:
            return ""
        hits = MemoryStore.instance().semantic_recall(
            user_id, vector, limit=limit * 2,
            agent_name=agent_name, conversation_id=conversation_id)
        kept = [(e, s) for e, s in hits if s >= MIN_SCORE][:limit]
        return render(kept, exclude=exclude)


def pending_block(user_id: str, conversation_id: str, agent_name: str) -> str:
    """Convenience wrapper: the block ready for this turn, if any."""
    return PassiveRecall.instance().take(user_id, conversation_id, agent_name)


def schedule_for_turn(user_id: str, conversation_id: str, agent_name: str,
                      query_text: str, exclude: str = "") -> bool:
    """Convenience wrapper: compute this turn's recall for the next one."""
    return PassiveRecall.instance().schedule(
        user_id, conversation_id, agent_name, query_text, exclude=exclude)

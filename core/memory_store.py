"""MemoryStore — Persistent long-term memory for the agent.

Stores facts, preferences, and knowledge per user across conversations.
Each memory entry has text content, tags for retrieval, and metadata.

Storage: JSON files per user in data/memories/
Retrieval: tag-based filtering + text search (no vector DB needed).
"""

import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

import core.paths as _paths


# ── BM25 ranker (no external dep) ────────────────────────────────
# Replaces the previous substring-only fallback for `recall(query=...)`.
# Tokenisation is naive on purpose: lowercase, split on non-alnum, drop
# 1-char tokens. BM25's IDF naturally down-weights common words so a
# stoplist isn't needed.

import re as _re_bm25

_BM25_SPLIT = _re_bm25.compile(r"[^\w]+", _re_bm25.UNICODE)
_BM25_K1 = 1.5
_BM25_B = 0.75


def _bm25_tokens(text: str) -> List[str]:
    return [t for t in _BM25_SPLIT.split(text.lower()) if len(t) > 1]


def _bm25_score(query_tokens: List[str], docs: List[List[str]]) -> List[float]:
    """Return one score per doc. Higher = more relevant."""
    import math
    n = len(docs)
    if n == 0 or not query_tokens:
        return [0.0] * n
    avgdl = sum(len(d) for d in docs) / n if n > 0 else 0.0
    if avgdl == 0:
        return [0.0] * n
    df: Dict[str, int] = {}
    for d in docs:
        for t in set(d):
            df[t] = df.get(t, 0) + 1
    scores = [0.0] * n
    for t in query_tokens:
        if t not in df:
            continue
        idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
        for i, d in enumerate(docs):
            tf = d.count(t)
            if tf == 0:
                continue
            denom = tf + _BM25_K1 * (
                1 - _BM25_B + _BM25_B * len(d) / avgdl)
            scores[i] += idf * (tf * (_BM25_K1 + 1)) / denom
    return scores


class MemoryEntry:
    """A single memory entry."""

    __slots__ = ("id", "text", "tags", "created_at", "updated_at", "source",
                 "embedding", "agent", "conversation_id",
                 "category", "valid_from", "ended", "expires_at")

    def __init__(self, text: str, tags: List[str],
                 entry_id: str = "", source: str = "",
                 created_at: float = 0, updated_at: float = 0,
                 embedding: Optional[List[float]] = None,
                 agent: str = "", conversation_id: str = "",
                 category: str = "",
                 valid_from: float = 0, ended: float = 0,
                 expires_at: float = 0):
        self.id = entry_id or uuid.uuid4().hex[:12]
        self.text = text
        self.tags = [t.lower().strip() for t in tags if t.strip()]
        self.created_at = created_at or time.time()
        self.updated_at = updated_at or self.created_at
        self.source = source
        self.embedding = embedding
        self.agent = agent  # "" = not scoped to agent
        self.conversation_id = conversation_id  # "" = not scoped to conversation
        self.category = category  # memory type category (facts/events/discoveries/preferences/advice)
        self.valid_from = valid_from  # 0 = valid since creation
        self.ended = ended            # temporal-validity end (0 = still valid). Kept for as_of queries.
        self.expires_at = expires_at  # hard TTL for storage cleanup (0 = no TTL, keep forever)

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "id": self.id,
            "text": self.text,
            "tags": self.tags,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "source": self.source,
        }
        if self.embedding is not None:
            d["embedding"] = self.embedding
        if self.agent:
            d["agent"] = self.agent
        if self.conversation_id:
            d["conversation_id"] = self.conversation_id
        if self.category:
            d["category"] = self.category
        if self.valid_from:
            d["valid_from"] = self.valid_from
        if self.ended:
            d["ended"] = self.ended
        if self.expires_at:
            d["expires_at"] = self.expires_at
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryEntry":
        return cls(
            text=data.get("text", ""),
            tags=data.get("tags", []),
            entry_id=data.get("id", ""),
            source=data.get("source", ""),
            created_at=data.get("created_at", 0),
            updated_at=data.get("updated_at", 0),
            embedding=data.get("embedding"),
            agent=data.get("agent", ""),
            conversation_id=data.get("conversation_id", ""),
            category=data.get("category", "") or data.get("hall", ""),
            valid_from=data.get("valid_from", 0),
            ended=data.get("ended", 0),
            expires_at=data.get("expires_at", 0),
        )

    def matches(self, query: str) -> bool:
        """Check if this entry matches a text query (case-insensitive)."""
        q = query.lower()
        if q in self.text.lower():
            return True
        if any(q in tag for tag in self.tags):
            return True
        if self.category and q in self.category.lower():
            return True
        return False

    def matches_tags(self, tags: List[str]) -> bool:
        """Check if this entry has any of the given tags."""
        search_tags = {t.lower().strip() for t in tags}
        return bool(search_tags & set(self.tags))


class MemoryStore:
    """Singleton store for persistent agent memory, per user."""

    _instance: Optional["MemoryStore"] = None
    _lock = threading.Lock()

    def __init__(self, store_dir: str = ""):
        self._store_dir = Path(store_dir or str(_paths.MEMORIES_DIR))
        self._store_dir.mkdir(parents=True, exist_ok=True)
        self._memories: Dict[str, List[MemoryEntry]] = {}  # user_id -> entries
        self._store_lock = threading.Lock()
        self._loaded_users: set = set()

    @classmethod
    def instance(cls) -> "MemoryStore":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls):
        with cls._lock:
            cls._instance = None

    # ── Public API ────────────────────────────────────────────────

    def remember(self, user_id: str, text: str, tags: List[str],
                 source: str = "",
                 embedding: Optional[List[float]] = None,
                 agent: str = "", conversation_id: str = "",
                 category: str = "",
                 valid_from: float = 0,
                 expires_at: float = 0) -> MemoryEntry:
        """Store a new memory for the user. Returns the created entry."""
        with self._store_lock:
            self._ensure_loaded(user_id)
            # Check for duplicate (same text)
            entries = self._memories.setdefault(user_id, [])
            for e in entries:
                if e.text.strip().lower() == text.strip().lower():
                    # Update existing entry
                    e.tags = list(set(e.tags + [t.lower().strip() for t in tags]))
                    e.updated_at = time.time()
                    if source:
                        e.source = source
                    if embedding is not None:
                        e.embedding = embedding
                    if agent:
                        e.agent = agent
                    if category:
                        e.category = category
                    if expires_at:
                        e.expires_at = expires_at
                    self._save_user(user_id)
                    return e

            entry = MemoryEntry(text=text, tags=tags, source=source,
                                embedding=embedding, agent=agent,
                                conversation_id=conversation_id,
                                category=category,
                                valid_from=valid_from,
                                expires_at=expires_at)
            entries.append(entry)
            self._save_user(user_id)
            return entry

    def recall(self, user_id: str, query: str = "",
               tags: Optional[List[str]] = None,
               limit: int = 20,
               agent_name: str = "",
               conversation_id: str = "",
               category: str = "",
               as_of: float = 0,
               verbatim: bool = False) -> List[MemoryEntry]:
        """Retrieve memories matching query and/or tags.

        Scoping: returns memories visible to this agent in this conversation.
        Priority order: private (agent+conv) → conversation → agent → global.
        """
        with self._store_lock:
            self._ensure_loaded(user_id)
            entries = self._memories.get(user_id, [])

        # Category filtering
        if category:
            entries = [e for e in entries if e.category == category]

        # Temporal filtering: only entries valid at as_of time
        if as_of:
            entries = [e for e in entries
                       if (not e.valid_from or e.valid_from <= as_of)
                       and (not e.ended or e.ended > as_of)]
        else:
            # Default: exclude ended entries
            entries = [e for e in entries if not e.ended]

        # Filter to entries visible for this agent/conversation. The
        # query filter is deferred to the BM25 step below — substring
        # match was the historical pre-filter but it discards entries
        # that share tokens with the query without containing the
        # whole phrase, which is exactly the case BM25 is designed to
        # handle. Tag filter stays a hard pre-filter when set without
        # a query (callers explicitly want tag-only retrieval).
        visible = []
        for e in entries:
            if not self._is_visible(e, agent_name, conversation_id):
                continue
            if tags and not query:
                if e.matches_tags(tags):
                    visible.append(e)
            else:
                visible.append(e)

        # Sort: private → conversation → agent → global, then relevance/recency
        def _scope_priority(e):
            if e.agent and e.conversation_id:
                return 0  # private
            if e.conversation_id:
                return 1  # conversation
            if e.agent:
                return 2  # agent
            return 3  # global

        if query:
            # BM25 over text + tags + category. Beats the previous
            # substring-only ranker for multi-token queries ("slow auth
            # middleware" finds entries containing 2/3 tokens even if
            # no entry contains the full phrase). Scope priority still
            # wins over relevance — a global hit shouldn't outrank a
            # private one with the same content. Entries that don't
            # share a single token with the query get score 0 and are
            # dropped (the historical substring filter rejected them
            # too; we keep that behaviour to avoid noise).
            q_tokens = _bm25_tokens(query)
            docs = [_bm25_tokens(
                e.text + " " + " ".join(e.tags)
                + (" " + e.category if e.category else ""))
                for e in visible]
            scores = _bm25_score(q_tokens, docs)
            ranked = [(e, s) for e, s in zip(visible, scores) if s > 0]
            ranked.sort(key=lambda x: (
                _scope_priority(x[0]),
                -x[1],
                -x[0].updated_at,
            ))
            visible = [e for e, _s in ranked]
        else:
            visible.sort(key=lambda e: (_scope_priority(e), -e.updated_at))

        return visible[:limit]

    @staticmethod
    def _is_visible(entry: MemoryEntry, agent_name: str,
                    conversation_id: str) -> bool:
        """Check if a memory entry is visible for this agent/conversation."""
        ea, ec = entry.agent, entry.conversation_id
        # Global: visible to all
        if not ea and not ec:
            return True
        # Agent-scoped: visible if agent matches (or no agent filter)
        if ea and not ec:
            return not agent_name or ea == agent_name
        # Conversation-scoped: visible if conversation matches
        if not ea and ec:
            return ec == conversation_id
        # Private (agent+conversation): visible only if both match
        return ea == agent_name and ec == conversation_id

    def end_memory(self, user_id: str, memory_id: str, ended: float = 0) -> bool:
        """Mark a memory as ended (no longer valid). Does not delete."""
        with self._store_lock:
            self._ensure_loaded(user_id)
            for e in self._memories.get(user_id, []):
                if e.id == memory_id:
                    e.ended = ended or time.time()
                    e.updated_at = time.time()
                    self._save_user(user_id)
                    return True
        return False

    def forget(self, user_id: str, memory_id: str) -> bool:
        """Delete a specific memory entry."""
        with self._store_lock:
            self._ensure_loaded(user_id)
            entries = self._memories.get(user_id, [])
            for i, e in enumerate(entries):
                if e.id == memory_id:
                    entries.pop(i)
                    self._save_user(user_id)
                    return True
            return False

    def forget_by_text(self, user_id: str, text: str) -> int:
        """Delete memories containing the given text. Returns count deleted."""
        with self._store_lock:
            self._ensure_loaded(user_id)
            entries = self._memories.get(user_id, [])
            before = len(entries)
            q = text.lower()
            self._memories[user_id] = [
                e for e in entries if q not in e.text.lower()
            ]
            after = len(self._memories[user_id])
            if before != after:
                self._save_user(user_id)
            return before - after

    def upsert_by_tag(self, user_id: str, dedup_tag: str, text: str,
                      tags: List[str], *,
                      source: str = "",
                      embedding: Optional[List[float]] = None,
                      agent: str = "", conversation_id: str = "",
                      category: str = "") -> MemoryEntry:
        """Insert or update a memory identified by a stable dedup tag.

        Used by mirrors that own an external key (e.g. a file slug) which
        stays constant across edits while the entry's text/embedding may
        change. Text-based dedup (`remember`) can't see that two different
        bodies describe the same source file.
        """
        key = (dedup_tag or "").lower().strip()
        norm_tags = [t.lower().strip() for t in tags if t.strip()]
        if key and key not in norm_tags:
            norm_tags.append(key)
        with self._store_lock:
            self._ensure_loaded(user_id)
            entries = self._memories.setdefault(user_id, [])
            if key:
                for e in entries:
                    if key in e.tags:
                        e.text = text
                        e.tags = norm_tags
                        e.updated_at = time.time()
                        if source:
                            e.source = source
                        if embedding is not None:
                            e.embedding = embedding
                        if agent:
                            e.agent = agent
                        if conversation_id:
                            e.conversation_id = conversation_id
                        if category:
                            e.category = category
                        self._save_user(user_id)
                        return e
            entry = MemoryEntry(text=text, tags=norm_tags, source=source,
                                embedding=embedding, agent=agent,
                                conversation_id=conversation_id,
                                category=category)
            entries.append(entry)
            self._save_user(user_id)
            return entry

    def forget_by_tag(self, user_id: str, dedup_tag: str) -> int:
        """Delete every entry carrying the given tag. Returns count deleted."""
        key = (dedup_tag or "").lower().strip()
        if not key:
            return 0
        with self._store_lock:
            self._ensure_loaded(user_id)
            entries = self._memories.get(user_id, [])
            before = len(entries)
            self._memories[user_id] = [e for e in entries if key not in e.tags]
            after = len(self._memories[user_id])
            if before != after:
                self._save_user(user_id)
            return before - after

    def list_all(self, user_id: str) -> List[MemoryEntry]:
        """List all memories for a user."""
        with self._store_lock:
            self._ensure_loaded(user_id)
            return list(self._memories.get(user_id, []))

    def count(self, user_id: str) -> int:
        with self._store_lock:
            self._ensure_loaded(user_id)
            return len(self._memories.get(user_id, []))

    def list_by_agent(self, user_id: str, agent_name: str) -> List[MemoryEntry]:
        """List memories for a specific agent (or global if agent_name is empty)."""
        with self._store_lock:
            self._ensure_loaded(user_id)
            entries = self._memories.get(user_id, [])
        return [e for e in entries if e.agent == agent_name]

    def update_text(self, user_id: str, memory_id: str, new_text: str) -> bool:
        """Update the text of an existing memory."""
        with self._store_lock:
            self._ensure_loaded(user_id)
            for e in self._memories.get(user_id, []):
                if e.id == memory_id:
                    e.text = new_text
                    e.updated_at = time.time()
                    self._save_user(user_id)
                    return True
            return False

    def update_tags(self, user_id: str, memory_id: str, tags: List[str]) -> bool:
        """Replace the tags of an existing memory."""
        with self._store_lock:
            self._ensure_loaded(user_id)
            for e in self._memories.get(user_id, []):
                if e.id == memory_id:
                    e.tags = [t.lower().strip() for t in tags if t.strip()]
                    e.updated_at = time.time()
                    self._save_user(user_id)
                    return True
            return False

    def update_agent(self, user_id: str, memory_id: str, agent: str) -> bool:
        """Change the agent scope of a memory (empty = global)."""
        with self._store_lock:
            self._ensure_loaded(user_id)
            for e in self._memories.get(user_id, []):
                if e.id == memory_id:
                    e.agent = agent
                    e.updated_at = time.time()
                    self._save_user(user_id)
                    return True
            return False

    # ── Semantic search ─────────────────────────────────────────

    def semantic_recall(self, user_id: str, query_embedding: List[float],
                        limit: int = 10,
                        agent_name: str = "",
                        conversation_id: str = "",
                        category: str = "") -> List[Tuple[MemoryEntry, float]]:
        """Find memories by semantic similarity using embeddings.

        Filters by visibility (same scoping as recall).
        Returns list of (entry, similarity_score) sorted by score descending.
        """
        from core.embeddings import cosine_similarity as cos_sim

        with self._store_lock:
            self._ensure_loaded(user_id)
            entries = self._memories.get(user_id, [])

        # Category filtering
        if category:
            entries = [e for e in entries if e.category == category]

        results = []
        for e in entries:
            if e.embedding is not None and self._is_visible(e, agent_name, conversation_id):
                try:
                    score = cos_sim(query_embedding, e.embedding)
                    results.append((e, score))
                except (ValueError, ZeroDivisionError):
                    continue

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:limit]

    def re_embed_all(self, user_id: str,
                     embed_fn: Callable[[str], List[float]]) -> int:
        """Re-embed all memories for a user using the given function.

        Args:
            user_id: User whose memories to re-embed.
            embed_fn: Function that takes text and returns embedding vector.

        Returns:
            Number of entries re-embedded.
        """
        with self._store_lock:
            self._ensure_loaded(user_id)
            entries = self._memories.get(user_id, [])
            count = 0
            for e in entries:
                try:
                    e.embedding = embed_fn(e.text)
                    count += 1
                except Exception as exc:
                    logger.warning(f"Failed to embed memory {e.id}: {exc}")
            if count > 0:
                self._save_user(user_id)
            return count

    def ensure_embeddings(self, user_id: str,
                          embed_fn: Callable[[str], List[float]]) -> int:
        """Embed only entries that currently have no embedding.

        Idempotent backfill: safe to call on every agent wire-up. Returns
        the number of entries freshly embedded (0 means everything was
        already covered).

        Runs `embed_fn` WITHOUT holding `_store_lock` so concurrent
        `remember`/`recall`/`forget` aren't blocked by a long backfill
        (several hundred entries times model latency can add up to tens
        of seconds).
        """
        with self._store_lock:
            self._ensure_loaded(user_id)
            todo = [(e.id, e.text)
                    for e in self._memories.get(user_id, [])
                    if e.embedding is None]

        if not todo:
            return 0

        updates: Dict[str, List[float]] = {}
        for mid, text in todo:
            try:
                vec = embed_fn(text)
            except Exception as exc:
                logger.warning(f"ensure_embeddings: failed on {mid}: {exc}")
                continue
            if vec:
                updates[mid] = vec

        if not updates:
            return 0

        with self._store_lock:
            entries = self._memories.get(user_id, [])
            count = 0
            for e in entries:
                if e.embedding is not None:
                    continue
                vec = updates.get(e.id)
                if vec is None:
                    continue
                e.embedding = vec
                count += 1
            if count > 0:
                self._save_user(user_id)
            return count

    # ── Disk persistence ──────────────────────────────────────────

    def _user_path(self, user_id: str) -> Path:
        safe = "".join(c for c in user_id if c.isalnum() or c in "-_@.")
        return self._store_dir / f"{safe}.json"

    def _ensure_loaded(self, user_id: str):
        if user_id in self._loaded_users:
            return
        self._loaded_users.add(user_id)
        path = self._user_path(user_id)
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            entries = [MemoryEntry.from_dict(d) for d in data]
            # Lazy TTL cleanup at load time — cheap (constant comparison
            # per entry) and means we never need a separate cron. Only
            # entries with expires_at>0 are eligible (no implicit TTL).
            now = time.time()
            kept = [e for e in entries if not e.expires_at or e.expires_at > now]
            dropped = len(entries) - len(kept)
            self._memories[user_id] = kept
            if dropped:
                logger.info(
                    "[memory-store] dropped %d expired entr%s for user=%s on load",
                    dropped, "y" if dropped == 1 else "ies", user_id)
                # Persist the cleanup so the file shrinks rather than
                # accumulating dead-but-not-yet-removed entries.
                self._save_user(user_id)
        except Exception as e:
            logger.warning(f"Failed to load memories for {user_id}: {e}")

    def cleanup_expired(self, user_id: str = "") -> int:
        """Drop entries whose hard TTL (expires_at) has passed.

        If user_id is empty, runs over every loaded user. Returns total
        count dropped. Safe to call from a cron / on-demand: idempotent
        (a fresh call after the previous returns 0).
        """
        now = time.time()
        dropped_total = 0
        with self._store_lock:
            users = [user_id] if user_id else list(self._loaded_users)
            for uid in users:
                self._ensure_loaded(uid)
                entries = self._memories.get(uid, [])
                kept = [e for e in entries
                        if not e.expires_at or e.expires_at > now]
                dropped = len(entries) - len(kept)
                if dropped:
                    self._memories[uid] = kept
                    self._save_user(uid)
                    dropped_total += dropped
        return dropped_total

    def _save_user(self, user_id: str):
        entries = self._memories.get(user_id, [])
        data = [e.to_dict() for e in entries]
        path = self._user_path(user_id)
        try:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
        except Exception as e:
            logger.error(f"Failed to save memories for {user_id}: {e}")

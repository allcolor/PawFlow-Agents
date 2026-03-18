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

_DEFAULT_DIR = "data/memories"


class MemoryEntry:
    """A single memory entry."""

    __slots__ = ("id", "text", "tags", "created_at", "updated_at", "source",
                 "embedding", "agent")

    def __init__(self, text: str, tags: List[str],
                 entry_id: str = "", source: str = "",
                 created_at: float = 0, updated_at: float = 0,
                 embedding: Optional[List[float]] = None,
                 agent: str = ""):
        self.id = entry_id or uuid.uuid4().hex[:12]
        self.text = text
        self.tags = [t.lower().strip() for t in tags if t.strip()]
        self.created_at = created_at or time.time()
        self.updated_at = updated_at or self.created_at
        self.source = source  # e.g. "conversation:abc123", "agent", "user"
        self.embedding = embedding  # optional vector for semantic search
        self.agent = agent  # agent name ("" = global memory)

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
        )

    def matches(self, query: str) -> bool:
        """Check if this entry matches a text query (case-insensitive)."""
        q = query.lower()
        if q in self.text.lower():
            return True
        if any(q in tag for tag in self.tags):
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
        self._store_dir = Path(store_dir or _DEFAULT_DIR)
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
                 agent: str = "") -> MemoryEntry:
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
                    self._save_user(user_id)
                    return e

            entry = MemoryEntry(text=text, tags=tags, source=source,
                                embedding=embedding, agent=agent)
            entries.append(entry)
            self._save_user(user_id)
            return entry

    def recall(self, user_id: str, query: str = "",
               tags: Optional[List[str]] = None,
               limit: int = 20) -> List[MemoryEntry]:
        """Retrieve memories matching query and/or tags."""
        with self._store_lock:
            self._ensure_loaded(user_id)
            entries = self._memories.get(user_id, [])

        results = []
        for e in entries:
            if query and tags:
                if e.matches(query) or e.matches_tags(tags):
                    results.append(e)
            elif query:
                if e.matches(query):
                    results.append(e)
            elif tags:
                if e.matches_tags(tags):
                    results.append(e)
            else:
                results.append(e)

        # Sort by relevance: exact query match first, then by recency
        if query:
            q_lower = query.lower()
            results.sort(key=lambda e: (
                0 if q_lower in e.text.lower() else 1,
                -e.updated_at,
            ))
        else:
            results.sort(key=lambda e: -e.updated_at)

        return results[:limit]

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
                        limit: int = 10) -> List[Tuple[MemoryEntry, float]]:
        """Find memories by semantic similarity using embeddings.

        Returns list of (entry, similarity_score) sorted by score descending.
        Only considers entries that have embeddings.
        """
        from core.embeddings import cosine_similarity as cos_sim

        with self._store_lock:
            self._ensure_loaded(user_id)
            entries = self._memories.get(user_id, [])

        results = []
        for e in entries:
            if e.embedding is not None:
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
            self._memories[user_id] = entries
        except Exception as e:
            logger.warning(f"Failed to load memories for {user_id}: {e}")

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

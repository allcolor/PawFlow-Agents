"""Full-text index over raw conversation transcripts (learning loop P4).

``recall`` searches *extracted memories* — facts an agent decided to keep at
the time. This indexes what was actually said, so an agent can answer "we
solved this before, in which conversation?" without having extracted a memory
back then. See ``docs/LEARNING_LOOP_PLAN.md``.

One SQLite FTS5 database per user under ``data/runtime/conversation_index/``.
The index is derived data: deleting a file costs the next search one rebuild
and nothing else.

Two deliberate design points:

- **Refreshed at search time, not on append.** The plan called for updating
  the index on every appended message. That puts a write on the hot path that
  the UI waits behind, for a feature nobody may call. The refresh is
  incremental either way — it reads each conversation's rows past the recorded
  watermark — so the only difference is *when* the cost lands, and search time
  is the moment the caller has already accepted a wait.
- **Encrypted conversations are never indexed.** An FTS index is plaintext by
  construction; indexing an encrypted conversation would copy its content
  outside the encrypted store and make the encryption decorative. A
  conversation that becomes encrypted after being indexed is purged on the
  next refresh.
"""

import logging
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

import core.paths as _paths

logger = logging.getLogger(__name__)

# Roles worth searching. Tool payloads and traces are noise here: they are
# machine output, they dominate the token mass of a transcript, and an agent
# looking for "where did we discuss X" means the conversation, not a diff.
_INDEXED_ROLES = ("user", "assistant")

_MAX_LIMIT = 50
_DEFAULT_LIMIT = 10
# One message is one row; a row far past this is a paste, not a discussion.
# Truncated for the index only — the transcript keeps the whole thing.
_MAX_ROW_CHARS = 20000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS indexed_conversations (
    conversation_id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    rows_indexed INTEGER NOT NULL DEFAULT 0,
    source_updated_at REAL NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0
);
CREATE VIRTUAL TABLE IF NOT EXISTS messages USING fts5(
    content,
    conversation_id UNINDEXED,
    title UNINDEXED,
    agent UNINDEXED,
    role UNINDEXED,
    msg_id UNINDEXED,
    ts UNINDEXED,
    tokenize = 'unicode61'
);
"""

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


class FTSUnavailable(RuntimeError):
    """This interpreter's SQLite was built without FTS5."""


def _safe_name(name: str) -> str:
    """Same sanitizing the conversation store applies to user directories."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(name or "")).strip("_") or "_"


def sanitize_query(query: str) -> str:
    """Rewrite a query FTS5 refused into one it accepts.

    Raw input goes to MATCH first, so ``"exact phrase"`` and ``a OR b`` keep
    working. Only when FTS5 raises does this strip the syntax down to quoted
    tokens joined by AND -- an unbalanced quote or a stray ``*`` should search
    for the words, not fail the call.
    """
    tokens = _TOKEN_RE.findall(query or "")
    return " AND ".join(f'"{tok}"' for tok in tokens)


class ConversationIndex:
    """Per-user FTS5 index over the conversations that user owns."""

    _instances: Dict[str, "ConversationIndex"] = {}
    _instances_lock = threading.Lock()

    def __init__(self, user_id: str, path: str = ""):
        if not user_id:
            raise ValueError("user_id is required")
        self._user_id = user_id
        self._path = Path(path) if path else (
            Path(str(_paths.CONVERSATION_INDEX_DIR)) / f"{_safe_name(user_id)}.db")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db_lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._db_lock:
            try:
                self._conn.executescript(_SCHEMA)
            except sqlite3.OperationalError as exc:
                self._conn.close()
                raise FTSUnavailable(
                    f"SQLite FTS5 is not available: {exc}") from exc
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.DatabaseError:
                logger.debug("WAL unavailable for %s", self._path, exc_info=True)

    @classmethod
    def for_user(cls, user_id: str) -> "ConversationIndex":
        with cls._instances_lock:
            inst = cls._instances.get(user_id)
            if inst is None:
                inst = cls(user_id)
                cls._instances[user_id] = inst
            return inst

    @classmethod
    def reset(cls) -> None:
        """Drop cached instances (tests, and a user's index being deleted)."""
        with cls._instances_lock:
            for inst in cls._instances.values():
                inst.close()
            cls._instances.clear()

    def close(self) -> None:
        with self._db_lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                logger.debug("index close failed", exc_info=True)

    # -- Indexing ------------------------------------------------------

    def refresh(self, store=None) -> Dict[str, int]:
        """Bring the index up to date with the user's conversations.

        Incremental twice over, because a search pays for this: a conversation
        whose ``updated_at`` has not moved since it was indexed is not opened
        at all, and one that has moved is read only past its row watermark.
        Without the first check every search would read every transcript of
        every conversation from disk, which is not incremental in any sense
        that matters. Returns counts for logging and for the tool's footer.
        """
        if store is None:
            from core.conversation_store import ConversationStore
            store = ConversationStore.instance()

        stats = {"conversations": 0, "indexed": 0, "messages": 0,
                 "skipped_encrypted": 0, "purged": 0, "unchanged": 0}
        try:
            listed = store.list_conversations(user_id=self._user_id)
        except Exception:
            logger.warning("conversation listing failed for index refresh",
                           exc_info=True)
            return stats

        seen = set()
        known = self._known()
        for entry in listed:
            cid = str(entry.get("conversation_id") or "")
            if not cid:
                continue
            seen.add(cid)
            stats["conversations"] += 1
            if self._is_encrypted(store, cid):
                stats["skipped_encrypted"] += 1
                if cid in known:
                    self.purge(cid)
                    stats["purged"] += 1
                continue
            title = str(entry.get("title") or "")
            updated_at = float(entry.get("updated_at") or 0.0)
            row = known.get(cid)
            if (row is not None and updated_at > 0
                    and updated_at <= row["source_updated_at"]
                    and title == row["title"]):
                stats["unchanged"] += 1
                continue
            added = self._index_conversation(
                store, cid, title, row["rows_indexed"] if row else 0,
                updated_at)
            if added:
                stats["indexed"] += 1
                stats["messages"] += added

        for cid in known:
            if cid not in seen:
                self.purge(cid)
                stats["purged"] += 1
        return stats

    def _known(self) -> Dict[str, Dict[str, Any]]:
        with self._db_lock:
            rows = self._conn.execute(
                "SELECT conversation_id, title, rows_indexed, source_updated_at "
                "FROM indexed_conversations"
            ).fetchall()
        return {r["conversation_id"]: {
            "title": str(r["title"] or ""),
            "rows_indexed": int(r["rows_indexed"] or 0),
            "source_updated_at": float(r["source_updated_at"] or 0.0),
        } for r in rows}

    @staticmethod
    def _is_encrypted(store, cid: str) -> bool:
        """Fail closed: an unreadable encryption state counts as encrypted.

        Guessing "not encrypted" here would write the conversation's plaintext
        into the index, which is the one outcome this must never produce.
        """
        try:
            return bool(store.encryption_status(cid).get("enabled"))
        except Exception:
            logger.debug("encryption status unreadable for %s", cid[:8],
                         exc_info=True)
            return True

    @staticmethod
    def _agent_of(msg: Dict[str, Any]) -> str:
        """Which agent a row belongs to.

        The store does not stamp an agent field: an assistant row carries
        ``source={"type": "agent", "name": ...}`` and a user row names the
        agent it was addressed to. Both answer "whose thread is this", which
        is what the ``agent`` filter is for.
        """
        source = msg.get("source")
        if isinstance(source, dict):
            name = source.get("name") if source.get("type") == "agent" else ""
            name = name or source.get("target_agent") or ""
            if name and str(name).lower() != "all":
                return str(name)
        return str(msg.get("agent") or msg.get("agent_name") or "")

    def _index_conversation(self, store, cid: str, title: str,
                            watermark: int, source_updated_at: float = 0.0) -> int:
        try:
            messages = store.load(cid, user_id=self._user_id) or []
        except Exception:
            logger.debug("transcript unreadable for %s", cid[:8], exc_info=True)
            return 0
        total = len(messages)
        if total < watermark:
            # The transcript shrank (rollback, context edit, deletion): the
            # watermark no longer addresses the same rows, so reindex it whole
            # rather than append onto stale ones.
            self.purge(cid)
            watermark = 0
        fresh = messages[watermark:]
        rows = []
        for msg in fresh:
            if not isinstance(msg, dict):
                continue
            if str(msg.get("role") or "") not in _INDEXED_ROLES:
                continue
            content = msg.get("content")
            if not isinstance(content, str):
                continue
            content = content.strip()
            if not content:
                continue
            rows.append((
                content[:_MAX_ROW_CHARS],
                cid,
                title,
                self._agent_of(msg),
                str(msg.get("role") or ""),
                str(msg.get("msg_id") or ""),
                float(msg.get("ts") or 0.0),
            ))

        with self._db_lock:
            if title:
                # A renamed conversation must stop reporting its old title,
                # on the rows already indexed as much as on the new ones.
                self._conn.execute(
                    "UPDATE messages SET title = ? WHERE conversation_id = ? "
                    "AND title != ?", (title, cid, title))
            if rows:
                self._conn.executemany(
                    "INSERT INTO messages (content, conversation_id, title, "
                    "agent, role, msg_id, ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    rows)
            # The watermark advances over every row read, indexed or not --
            # it addresses transcript positions, not stored rows. Advancing it
            # only by len(rows) would re-read every tool row on each refresh.
            self._conn.execute(
                "INSERT INTO indexed_conversations (conversation_id, title, "
                "rows_indexed, source_updated_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(conversation_id) DO UPDATE SET "
                "title=excluded.title, rows_indexed=excluded.rows_indexed, "
                "source_updated_at=excluded.source_updated_at, "
                "updated_at=excluded.updated_at",
                (cid, title, total, source_updated_at, time.time()))
            self._conn.commit()
        return len(rows)

    def purge(self, cid: str) -> None:
        """Forget a conversation entirely (deleted, or newly encrypted)."""
        with self._db_lock:
            self._conn.execute(
                "DELETE FROM messages WHERE conversation_id = ?", (cid,))
            self._conn.execute(
                "DELETE FROM indexed_conversations WHERE conversation_id = ?",
                (cid,))
            self._conn.commit()

    # -- Searching -----------------------------------------------------

    def search(self, query: str, agent: str = "", limit: int = _DEFAULT_LIMIT,
               exclude_conversation: str = "") -> List[Dict[str, Any]]:
        """Best matches for ``query``, most relevant first (bm25 rank)."""
        query = (query or "").strip()
        if not query:
            return []
        try:
            limit = int(limit or _DEFAULT_LIMIT)
        except (TypeError, ValueError):
            limit = _DEFAULT_LIMIT
        limit = max(1, min(limit, _MAX_LIMIT))

        sql = ("SELECT content, conversation_id, title, agent, role, msg_id, ts, "
               "snippet(messages, 0, '[', ']', ' … ', 16) AS snippet "
               "FROM messages WHERE messages MATCH ?")
        params: List[Any] = [query]
        if agent:
            sql += " AND agent = ?"
            params.append(agent)
        if exclude_conversation:
            sql += " AND conversation_id != ?"
            params.append(exclude_conversation)
        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)

        with self._db_lock:
            try:
                rows = self._conn.execute(sql, params).fetchall()
            except sqlite3.OperationalError:
                cleaned = sanitize_query(query)
                if not cleaned:
                    return []
                params[0] = cleaned
                rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> Dict[str, int]:
        with self._db_lock:
            convs = self._conn.execute(
                "SELECT COUNT(*) FROM indexed_conversations").fetchone()[0]
            msgs = self._conn.execute(
                "SELECT COUNT(*) FROM messages").fetchone()[0]
        return {"conversations": int(convs), "messages": int(msgs)}

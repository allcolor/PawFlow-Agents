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
from typing import Any, Dict, List, Optional

import core.paths as _paths
from core.sqlite_store_guard import (
    SqliteStoreGuard,
    is_corruption_error,
)
from core._conversation_store_base import TRANSCRIPT_GENERATION

logger = logging.getLogger(__name__)

# Roles worth searching. Tool payloads and traces are noise here: they are
# machine output, they dominate the token mass of a transcript, and an agent
# looking for "where did we discuss X" means the conversation, not a diff.
_INDEXED_ROLES = ("user", "assistant")

_MAX_LIMIT = 50
_DEFAULT_LIMIT = 10

_SCHEMA = """
CREATE TABLE IF NOT EXISTS indexed_conversations (
    conversation_id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    rows_indexed INTEGER NOT NULL DEFAULT 0,
    source_updated_at REAL NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0,
    -- The store's transcript rewrite counter as of the last index.
    source_generation INTEGER NOT NULL DEFAULT -1
);
CREATE VIRTUAL TABLE IF NOT EXISTS messages USING fts5(
    content,
    conversation_id UNINDEXED,
    title UNINDEXED,
    agent UNINDEXED,
    role UNINDEXED,
    msg_id UNINDEXED,
    ts UNINDEXED,
    -- Display index of the row, numbered like read_history's [#n]: file
    -- order, one per role row. Lets read_history answer a search from here.
    pos UNINDEXED,
    -- source.name, for rendering a hit without reading the transcript.
    speaker UNINDEXED,
    -- Every agent the row involves (read_history's agent_filter), each
    -- wrapped in _AGENT_SEP so instr() matches whole names only.
    agents UNINDEXED,
    tokenize = 'unicode61'
);
"""

# An index missing any of these predates positions; it is dropped and rebuilt.
_REQUIRED_MESSAGE_COLUMNS = frozenset({"pos", "speaker", "agents"})
_AGENT_SEP = "\x1f"
_INSERT_SQL = (
    "INSERT INTO messages (content, conversation_id, title, agent, role, "
    "msg_id, ts, pos, speaker, agents) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")

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
        # One writer per conversation: a background rebuild and a search-time
        # refresh of the same conversation would otherwise insert its rows
        # twice.
        self._conv_locks: Dict[str, threading.Lock] = {}
        self._conv_locks_guard = threading.Lock()
        self._rebuilding: set = set()
        self._guard = SqliteStoreGuard("Conversation index")
        self._guard.preflight(self._path)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._guard.runtime(self._path), self._db_lock:
            try:
                self._conn.executescript(_SCHEMA)
            except sqlite3.OperationalError as exc:
                if is_corruption_error(exc):
                    raise
                self._conn.close()
                raise FTSUnavailable(
                    f"SQLite FTS5 is not available: {exc}") from exc
            # CREATE TABLE IF NOT EXISTS leaves an older database alone. One
            # without row positions cannot serve read_history, and one without
            # generations may hold text since edited or deleted: the index is
            # derived data, so it is dropped and rebuilt rather than migrated.
            columns = {row[1] for row in self._conn.execute(
                "PRAGMA table_info(messages)")}
            if not _REQUIRED_MESSAGE_COLUMNS <= columns:
                self._conn.execute("DROP TABLE IF EXISTS messages")
                self._conn.execute("DROP TABLE IF EXISTS indexed_conversations")
                self._conn.executescript(_SCHEMA)
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.DatabaseError as exc:
                if is_corruption_error(exc):
                    raise
                logger.debug("WAL unavailable for %s", self._path, exc_info=True)

    @property
    def available(self) -> bool:
        """Return whether the index is safe to read or write."""
        return self._guard.available

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

    def refresh(self, store=None, skip=()) -> Dict[str, int]:
        """Bring the index up to date with the user's conversations.

        Incremental twice over, because a search pays for this: a conversation
        whose ``updated_at`` has not moved since it was indexed is not opened
        at all, and one that has moved is read only past its row watermark.
        Without the first check every search would read every transcript of
        every conversation from disk, which is not incremental in any sense
        that matters. Returns counts for logging and for the tool's footer.

        ``skip`` names conversations the caller will not search (the one it
        excludes): they are left as indexed, neither read nor purged.
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
            # A failed listing cannot distinguish "temporarily unreadable"
            # from "deleted". Derived plaintext must not outlive its source.
            self._purge_all()
            return stats

        seen = set()
        known = self._known()
        for entry in listed:
            cid = str(entry.get("conversation_id") or "")
            if not cid:
                continue
            seen.add(cid)
            stats["conversations"] += 1
            if cid in skip:
                continue
            if self._is_encrypted(store, cid):
                stats["skipped_encrypted"] += 1
                if cid in known:
                    self.purge(cid)
                    stats["purged"] += 1
                continue
            title = str(entry.get("title") or "")
            updated_at = float(entry.get("updated_at") or 0.0)
            generation = int(entry.get(TRANSCRIPT_GENERATION) or 0)
            row = known.get(cid)
            # `updated_at` alone cannot see a rewrite: an in-place edit leaves
            # it untouched and deleting the newest message moves it backwards,
            # so both read as "nothing new" and the conversation would keep
            # serving text that was redacted or deleted. The generation is the
            # signal that survives both.
            if (row is not None and updated_at > 0
                    and updated_at <= row["source_updated_at"]
                    and title == row["title"]
                    and generation == row["source_generation"]):
                stats["unchanged"] += 1
                continue
            with self._conversation_lock(cid):
                added = self._sync_conversation(store, cid, title, updated_at)
            if added:
                stats["indexed"] += 1
                stats["messages"] += added

        for cid in known:
            if cid not in seen:
                self.purge(cid)
                stats["purged"] += 1
        return stats

    def _known(self, cid: str = "") -> Dict[str, Dict[str, Any]]:
        sql = ("SELECT conversation_id, title, rows_indexed, source_updated_at, "
               "source_generation FROM indexed_conversations")
        params: tuple = ()
        if cid:
            sql += " WHERE conversation_id = ?"
            params = (cid,)
        with self._guard.runtime(self._path), self._db_lock:
            rows = self._conn.execute(sql, params).fetchall()
        return {r["conversation_id"]: {
            "title": str(r["title"] or ""),
            "rows_indexed": int(r["rows_indexed"] or 0),
            "source_updated_at": float(r["source_updated_at"] or 0.0),
            "source_generation": int(r["source_generation"]
                                     if r["source_generation"] is not None else -1),
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

    def _conversation_lock(self, cid: str) -> threading.Lock:
        with self._conv_locks_guard:
            lock = self._conv_locks.get(cid)
            if lock is None:
                lock = self._conv_locks[cid] = threading.Lock()
            return lock

    def _sync_conversation(self, store, cid: str, title: str,
                           source_updated_at: float,
                           allow_full: bool = True) -> Optional[int]:
        """Index what ``cid`` gained since its watermark. Caller holds its lock.

        Positions, the watermark and the shrink check all count display rows
        the way read_history numbers them: ``display_row_count`` and the
        display windows, in file order. The metadata ``message_count`` and the
        timestamp-sorted ``load()`` count differently; mixing them left
        watermarks above the count, which read as a shrunken transcript and
        purged and re-read the whole conversation (~80 s at 700k rows on the
        first search after a restart, 2026-09-29).

        Returns the rows indexed, or None when the index cannot answer for
        the conversation: its transcript was unreadable (and it was purged),
        or it needs a full reindex and ``allow_full`` is False.
        """
        row = self._known(cid).get(cid)
        watermark = row["rows_indexed"] if row else 0
        try:
            with store._get_conv_lock(cid):
                generation = int(store.get_extra(
                    cid, TRANSCRIPT_GENERATION, 0) or 0)
                total = int(store.display_row_count(cid))
                # A moved generation: indexed rows may hold text since edited
                # or deleted, and an edit at constant row count leaves nothing
                # else to notice. Fewer rows than the watermark: it no longer
                # addresses the same rows.
                full = (row is None
                        or generation != row["source_generation"]
                        or total < watermark)
                if not full:
                    appended = total - watermark
                    fresh = (store.load_window_by_index(cid, watermark, appended)
                             if appended else [])
                    if len(fresh) < appended:
                        raise OSError(
                            "incremental transcript tail was incomplete")
            if full:
                if not allow_full:
                    return None
                return self._reindex_whole(store, cid, title,
                                           source_updated_at, generation)
        except Exception:
            logger.debug("transcript unreadable for %s", cid[:8], exc_info=True)
            # Do this even on an incremental refresh: the existing rows may be
            # exactly the text that was deleted or redacted before the read
            # failed. Keeping them would turn an I/O failure into disclosure.
            self.purge(cid)
            return None
        rows = self._rows(cid, title, fresh, watermark)
        self._record(cid, title, row["title"], rows, watermark + len(fresh),
                     source_updated_at, generation)
        return len(rows)

    def _reindex_whole(self, store, cid: str, title: str,
                       source_updated_at: float, generation: int) -> int:
        """Purge ``cid`` and re-read it one display window at a time.

        ``store.load()`` held the whole transcript in memory (2 GB at 700k
        rows). Windows are committed as they come; until the final record
        lands the conversation has no ``indexed_conversations`` row, so
        read_history keeps using its scan meanwhile.
        """
        self.purge(cid)
        added = 0
        total = 0
        for start, msgs in store.iter_display_windows(cid):
            rows = self._rows(cid, title, msgs, start)
            if rows:
                with self._guard.runtime(self._path), self._db_lock:
                    self._conn.executemany(_INSERT_SQL, rows)
                    self._conn.commit()
            added += len(rows)
            total = start + len(msgs)
        self._record(cid, title, None, [], total, source_updated_at,
                     generation)
        return added

    def _rows(self, cid: str, title: str, msgs: List[Dict[str, Any]],
              first_pos: int) -> List[tuple]:
        """Index rows for ``msgs``, whose first display index is first_pos."""
        from core.handlers.history import _msg_agents_involved
        from core.secret_sanitization import strip_secret_runtime_values

        rows = []
        for offset, msg in enumerate(msgs):
            if not isinstance(msg, dict):
                continue
            role = str(msg.get("role") or "")
            if role not in _INDEXED_ROLES:
                continue
            content = msg.get("content")
            if content is None:
                continue
            # read_history searches str(content), so the index holds the same.
            if not isinstance(content, str):
                content = str(strip_secret_runtime_values(content))
            if not content.strip():
                continue
            source = msg.get("source")
            speaker = (str(source.get("name") or "")
                       if isinstance(source, dict) else "")
            agents = sorted(_msg_agents_involved(msg))
            # Whole, not capped: read_history matches anywhere in a message.
            # Messages over 20k chars are ~145 of 214k in production, ~15 MB.
            rows.append((
                content,
                cid,
                title,
                self._agent_of(msg),
                role,
                str(msg.get("msg_id") or ""),
                float(msg.get("ts") or 0.0),
                first_pos + offset,
                speaker,
                "".join(_AGENT_SEP + a for a in agents) + _AGENT_SEP
                if agents else "",
            ))
        return rows

    def _record(self, cid: str, title: str, stored_title: Optional[str],
                rows: List[tuple], total: int, source_updated_at: float,
                generation: int) -> None:
        """Insert ``rows`` and move the watermark to ``total``, atomically."""
        with self._guard.runtime(self._path), self._db_lock:
            if (title and stored_title is not None
                    and title != stored_title):
                # A renamed conversation must stop reporting its old title,
                # on the rows already indexed as much as on the new ones.
                # `messages` is an FTS5 table whose conversation_id column is
                # UNINDEXED, so this statement scans the whole table: it runs
                # only when the stored title actually differs. Rows written
                # by this refresh already carry the current title, and a
                # purged conversation has no old rows left to rename.
                self._conn.execute(
                    "UPDATE messages SET title = ? WHERE conversation_id = ? "
                    "AND title != ?", (title, cid, title))
            if rows:
                self._conn.executemany(_INSERT_SQL, rows)
            # The watermark advances over every row read, indexed or not --
            # it addresses transcript positions, not stored rows. Advancing it
            # only by len(rows) would re-read every tool row on each refresh.
            self._conn.execute(
                "INSERT INTO indexed_conversations (conversation_id, title, "
                "rows_indexed, source_updated_at, updated_at, "
                "source_generation) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(conversation_id) DO UPDATE SET "
                "title=excluded.title, rows_indexed=excluded.rows_indexed, "
                "source_updated_at=excluded.source_updated_at, "
                "updated_at=excluded.updated_at, "
                "source_generation=excluded.source_generation",
                (cid, title, total, source_updated_at, time.time(),
                 generation))
            self._conn.commit()

    def purge(self, cid: str) -> None:
        """Forget a conversation entirely (deleted, or newly encrypted)."""
        with self._guard.runtime(self._path), self._db_lock:
            self._conn.execute(
                "DELETE FROM messages WHERE conversation_id = ?", (cid,))
            self._conn.execute(
                "DELETE FROM indexed_conversations WHERE conversation_id = ?",
                (cid,))
            self._conn.commit()

    def _purge_all(self) -> None:
        """Fail-closed reset used when the source listing is unreadable."""
        with self._guard.runtime(self._path), self._db_lock:
            self._conn.execute("DELETE FROM messages")
            self._conn.execute("DELETE FROM indexed_conversations")
            self._conn.commit()

    # -- One conversation, for read_history -----------------------------

    def ensure_current(self, store, cid: str) -> bool:
        """Bring ``cid`` up to date; True when the index may answer for it.

        Appended rows are indexed inline -- cheap, they are the tail. A
        conversation that needs a full reindex (never indexed, rewritten,
        shrunk) is rebuilt in the background and this returns False, so an
        interactive caller falls back to its scan instead of waiting a minute
        on the largest transcripts. So does a conversation whose rebuild is
        already running.
        """
        if self._is_encrypted(store, cid):
            return False
        lock = self._conversation_lock(cid)
        if not lock.acquire(blocking=False):
            return False
        try:
            row = self._known(cid).get(cid)
            if row is not None:
                meta = store.get_metadata(cid) or {}
                if self._sync_conversation(
                        store, cid, row["title"],
                        float(meta.get("updated_at") or 0.0),
                        allow_full=False) is not None:
                    return True
        finally:
            lock.release()
        self._rebuild_in_background(store, cid)
        return False

    def _rebuild_in_background(self, store, cid: str) -> None:
        with self._conv_locks_guard:
            if cid in self._rebuilding:
                return
            self._rebuilding.add(cid)

        def _run():
            try:
                with self._conversation_lock(cid):
                    meta = store.get_metadata(cid) or {}
                    title = str(store.get_extra(cid, "title", "") or "")
                    self._sync_conversation(
                        store, cid, title,
                        float(meta.get("updated_at") or 0.0))
            except Exception:
                logger.warning("conversation index rebuild failed for %s",
                               cid[:8], exc_info=True)
            finally:
                with self._conv_locks_guard:
                    self._rebuilding.discard(cid)

        threading.Thread(target=_run, daemon=True,
                         name=f"conv-index-rebuild-{cid[:8]}").start()

    def search_conversation(self, cid: str, role: str, agent: str = "",
                            needles: Optional[List[str]] = None
                            ) -> List[Dict[str, Any]]:
        """Rows of ``cid`` with ``role``, in display order, prefiltered.

        ``agent`` keeps rows involving that agent (read_history's
        agent_filter). ``needles`` keeps rows whose lowercased content holds
        any of them. SQLite's lower() folds ASCII only, so pass ASCII needles,
        and re-check each returned row: this narrows the candidates, it does
        not decide.
        """
        sql = ("SELECT pos, content, role, speaker FROM messages "
               "WHERE conversation_id = ? AND role = ?")
        params: List[Any] = [cid, role]
        if agent:
            sql += " AND instr(agents, ?) > 0"
            params.append(_AGENT_SEP + agent + _AGENT_SEP)
        if needles:
            sql += (" AND ("
                    + " OR ".join("instr(lower(content), ?) > 0"
                                  for _ in needles) + ")")
            params.extend(needles)
        sql += " ORDER BY pos"
        with self._guard.runtime(self._path), self._db_lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

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

        with self._guard.runtime(self._path), self._db_lock:
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
        with self._guard.runtime(self._path), self._db_lock:
            convs = self._conn.execute(
                "SELECT COUNT(*) FROM indexed_conversations").fetchone()[0]
            msgs = self._conn.execute(
                "SELECT COUNT(*) FROM messages").fetchone()[0]
        return {"conversations": int(convs), "messages": int(msgs)}

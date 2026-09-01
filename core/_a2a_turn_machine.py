"""AG-UI thread handles, generations, tombstone tables and TTL sweep.

Implements the B1-T / B1-G / B1.4-6 contracts of
``docs/WEBMCP_INTEGRATION_PLAN.md`` (v8.2), step P1-A:

- a thread's identity is ``(threadId, generation)``; the first contact
  creates generation 0 (a first POST presenting generation 0 is
  idempotent with the GET bootstrap — documented behaviour) and every
  run must present the generation it holds — a stale or closed
  generation raises :class:`AguiThreadRotated` carrying the current one,
  checked before anything else;
- generation validation and context creation/refresh happen under ONE
  ``BEGIN IMMEDIATE`` transaction (and the store lock), so a concurrent
  rotation can never resurrect a stale generation's context;
- rotation is a CAS on ``expected_generation`` and refuses to run while
  a previous rotation's cleanup is still pending;
- rotation cleanup is crash-ordered: the thread row records the
  conversation to delete before the deletion happens, the marker is
  cleared only by CAS after the callback SUCCEEDS, and the next resolve
  finishes an interrupted cleanup (refusing to hand out a context while
  it cannot run the cleanup);
- only ``isolated`` publications ever schedule a conversation deletion —
  a ``shared`` rotation drops the context row and never touches the
  parent conversation;
- ``closed_before_generation`` is a monotonic watermark — closed
  generations are rejected without keeping one row per generation;
- the TTL sweep is scoped: only ``agui_``-prefixed contexts of
  TTL-bearing isolated publications, at most once per ``min_interval``
  (atomic reservation, safe across processes). The ``has_active_lease``
  callback is an optional extra filter — the HARD guard is the
  rotation itself, which refuses any active admission atomically
  (leases share this commit domain since P1-C). Conversation expiry
  itself lives in the
  conversation extras (``_meta_expires_at``) — the store never grows a
  second expiry clock, it asks the caller through callbacks.

This mixin runs on :class:`core.a2a_store.A2AStore`'s lock and
connection factory: every table lives in the same SQLite database, which
is the plan's single commit domain (B1-D).
"""

from __future__ import annotations

import hashlib
import time
from contextlib import contextmanager
from typing import Any, Callable, Dict, Optional


class AguiThreadRotated(Exception):
    """The presented generation is stale or closed.

    Carries the thread's current generation so the client can adopt it
    (empty history) or start a fresh thread. Maps to HTTP
    ``409 thread_rotated`` at the endpoint layer.
    """

    def __init__(self, current_generation: int) -> None:
        super().__init__(f"thread_rotated: current generation is {current_generation}")
        self.current_generation = current_generation


class AguiCleanupPending(Exception):
    """A previous rotation's conversation cleanup has not completed yet."""


class TurnMachineMixin:
    """AG-UI turn-machine tables and thread/generation operations."""

    # Provided by A2AStore:
    _lock: Any
    _connect: Callable[[], Any]

    # ── schema ───────────────────────────────────────────────────────

    @staticmethod
    def _initialize_turn_tables(connection) -> None:
        has_threads = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='agui_threads'"
        ).fetchone() is not None
        if has_threads and not connection.execute(
                "PRAGMA foreign_key_list(agui_threads)").fetchall():
            # One-shot migration: early FK-less tables are dropped.
            connection.executescript(
                "DROP TABLE IF EXISTS agui_run_tombstones;"
                "DROP TABLE IF EXISTS agui_threads;")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS agui_threads (
                publication_id TEXT NOT NULL REFERENCES a2a_publications(publication_id)
                    ON DELETE CASCADE,
                key_id TEXT NOT NULL REFERENCES a2a_api_keys(key_id)
                    ON DELETE CASCADE,
                thread_id TEXT NOT NULL,
                generation INTEGER NOT NULL DEFAULT 0,
                closed_before_generation INTEGER NOT NULL DEFAULT 0,
                pending_cleanup_conversation TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (publication_id, key_id, thread_id)
            );

            CREATE TABLE IF NOT EXISTS agui_run_tombstones (
                publication_id TEXT NOT NULL,
                key_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                generation INTEGER NOT NULL,
                run_id TEXT NOT NULL,
                body_hash TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY (publication_id, key_id, thread_id, generation, run_id),
                FOREIGN KEY (publication_id, key_id, thread_id)
                    REFERENCES agui_threads(publication_id, key_id, thread_id)
                    ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS agui_meta (
                k TEXT PRIMARY KEY,
                v TEXT NOT NULL
            );
            """
        )
        columns = {
            row[1] for row in connection.execute(
                "PRAGMA table_info(a2a_publications)").fetchall()
        }
        if "thread_ttl_seconds" not in columns:
            connection.execute(
                "ALTER TABLE a2a_publications "
                "ADD COLUMN thread_ttl_seconds INTEGER NOT NULL DEFAULT 0"
            )

    @contextmanager
    def _immediate(self):
        """One explicit BEGIN IMMEDIATE transaction (cross-process safety)."""
        connection = self._connect()
        connection.isolation_level = None
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.execute("COMMIT")
        except Exception:
            try:
                connection.execute("ROLLBACK")
            except Exception:  # nosec B110 - preserve the transaction error
                pass
            raise
        finally:
            connection.close()

    # ── identities ───────────────────────────────────────────────────

    @staticmethod
    def agui_context_id(publication_id: str, key_id: str, thread_id: str,
                        generation: int) -> str:
        digest = hashlib.sha256(
            f"{publication_id}|{key_id}|{thread_id}|{int(generation)}".encode("utf-8")
        ).hexdigest()[:24]
        return "agui_" + digest

    @staticmethod
    def _agui_internal_conversation(publication: Dict[str, Any],
                                    context_id: str) -> str:
        internal = publication["conversation_id"]
        if publication["context_policy"] == "isolated":
            internal = f"{internal}::a2a::{context_id[5:]}"
        return internal

    # ── thread bootstrap / resolution ────────────────────────────────

    def ensure_agui_thread(self, publication: Dict[str, Any], key_id: str,
                           thread_id: str) -> Dict[str, Any]:
        """Create the thread at generation 0 when unseen; return its handle.

        This is the ``GET ?thread_id=`` bootstrap: the returned
        ``generation`` is what every subsequent run must present.
        """
        if not thread_id:
            raise ValueError("An AG-UI thread requires a non-empty thread_id")
        now = time.time()
        with self._lock, self._immediate() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO agui_threads (publication_id, key_id, "
                "thread_id, generation, closed_before_generation, "
                "pending_cleanup_conversation, created_at, updated_at) "
                "VALUES (?, ?, ?, 0, 0, '', ?, ?)",
                (publication["publication_id"], key_id, thread_id, now, now),
            )
            row = connection.execute(
                "SELECT generation FROM agui_threads WHERE publication_id=? "
                "AND key_id=? AND thread_id=?",
                (publication["publication_id"], key_id, thread_id),
            ).fetchone()
            generation = int(row["generation"])
        return {
            "thread_id": thread_id,
            "generation": generation,
            "context_id": self.agui_context_id(
                publication["publication_id"], key_id, thread_id, generation),
        }

    def resolve_agui_thread(
        self, publication: Dict[str, Any], key_id: str, thread_id: str,
        generation: int, *,
        delete_conversation: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        """Validate the presented generation and return the live context.

        Generation checks come FIRST (plan B1-A branch 0). The final
        validation and the context creation/refresh share one
        ``BEGIN IMMEDIATE`` transaction, so a concurrent rotation can
        never resurrect a stale generation's context. An interrupted
        rotation cleanup is finished here before the context is handed
        out — and without a ``delete_conversation`` callback the resolve
        refuses (:class:`AguiCleanupPending`) rather than dropping the
        cleanup.
        """
        generation = int(generation)
        self.ensure_agui_thread(publication, key_id, thread_id)
        pub_id = publication["publication_id"]
        with self._lock, self._immediate() as connection:
            row = connection.execute(
                "SELECT * FROM agui_threads WHERE publication_id=? AND "
                "key_id=? AND thread_id=?", (pub_id, key_id, thread_id),
            ).fetchone()
            current = int(row["generation"])
            closed_before = int(row["closed_before_generation"])
            pending = str(row["pending_cleanup_conversation"] or "")
            if generation < closed_before or generation != current:
                raise AguiThreadRotated(current)
        if pending:
            if delete_conversation is None:
                raise AguiCleanupPending(
                    "AG-UI thread has a pending rotation cleanup and no "
                    "delete_conversation callback was provided")
            # The callback runs OUTSIDE the store lock: it may do
            # filesystem I/O and take other locks. The durable marker
            # keeps rotations refused meanwhile, and a concurrent
            # deletion of the same conversation is idempotent.
            delete_conversation(pending)
            with self._lock, self._immediate() as connection:
                connection.execute(
                    "UPDATE agui_threads SET pending_cleanup_conversation='', "
                    "updated_at=? WHERE publication_id=? AND key_id=? AND "
                    "thread_id=? AND pending_cleanup_conversation=?",
                    (time.time(), pub_id, key_id, thread_id, pending),
                )
        # Final validation + context creation in ONE transaction.
        now = time.time()
        with self._lock:
            with self._immediate() as connection:
                row = connection.execute(
                    "SELECT generation, closed_before_generation FROM agui_threads "
                    "WHERE publication_id=? AND key_id=? AND thread_id=?",
                    (pub_id, key_id, thread_id),
                ).fetchone()
                if row is None:
                    raise AguiThreadRotated(0)
                current = int(row["generation"])
                if generation < int(row["closed_before_generation"]) \
                        or generation != current:
                    raise AguiThreadRotated(current)
                context_id = self.agui_context_id(pub_id, key_id, thread_id,
                                                  current)
                context = connection.execute(
                    "SELECT * FROM a2a_contexts WHERE context_id=? AND "
                    "publication_id=? AND key_id=?",
                    (context_id, pub_id, key_id),
                ).fetchone()
                if context:
                    connection.execute(
                        "UPDATE a2a_contexts SET last_seen_at=? WHERE context_id=?",
                        (now, context_id))
                    return dict(context)
                internal = self._agui_internal_conversation(publication,
                                                            context_id)
                connection.execute(
                    "INSERT INTO a2a_contexts VALUES (?, ?, ?, ?, ?, ?)",
                    (context_id, pub_id, key_id, internal, now, now),
                )
        return {
            "context_id": context_id, "publication_id": pub_id,
            "key_id": key_id, "internal_conversation_id": internal,
            "created_at": now, "last_seen_at": now,
        }

    # ── rotation ─────────────────────────────────────────────────────

    def rotate_agui_thread(
        self, publication: Dict[str, Any], key_id: str, thread_id: str, *,
        expected_generation: int,
        delete_conversation: Optional[Callable[[str], None]] = None,
        close_generation: bool = False,
    ) -> Dict[str, Any]:
        """CAS-advance the thread to a fresh generation (crash-ordered).

        Refuses when ``expected_generation`` is stale
        (:class:`AguiThreadRotated` — two concurrent rotations bump only
        once) or when a previous cleanup is still pending
        (:class:`AguiCleanupPending`). Only ``isolated`` publications
        schedule a conversation deletion; a ``shared`` rotation drops the
        context row and never touches the parent conversation. The
        cleanup marker is cleared by CAS only after the callback
        succeeds; an exception from the callback leaves it intact.
        """
        now = time.time()
        pub_id = publication["publication_id"]
        isolated = publication["context_policy"] == "isolated"
        expected_generation = int(expected_generation)
        with self._lock, self._immediate() as connection:
            row = connection.execute(
                "SELECT * FROM agui_threads WHERE publication_id=? AND "
                "key_id=? AND thread_id=?", (pub_id, key_id, thread_id),
            ).fetchone()
            if row is None:
                raise ValueError("Unknown AG-UI thread")
            current = int(row["generation"])
            if expected_generation != current:
                raise AguiThreadRotated(current)
            if str(row["pending_cleanup_conversation"] or ""):
                raise AguiCleanupPending(
                    "A previous rotation cleanup is still pending; "
                    "resolve must finish it first")
            old_context = self.agui_context_id(pub_id, key_id, thread_id,
                                               current)
            # A thread with an ACTIVE admission (reserved..running) is
            # never rotated from under its run — leases live in the same
            # commit domain since P1-C. A read failure here propagates:
            # the rotation must fail closed, never proceed blind.
            active = connection.execute(
                "SELECT run_id FROM agui_admissions WHERE context_id=? "
                "AND state IN ('reserved','dispatching','accepted',"
                "'running')", (old_context,),
            ).fetchone()
            if active is not None:
                from core._a2a_turn_acquire import AguiTurnBusy
                raise AguiTurnBusy(active["run_id"])
            # An open managed batch (frontend-execution lease) equally
            # pins the thread against rotation (plan B1-L).
            open_batch = connection.execute(
                "SELECT run_id FROM agui_batches WHERE context_id=? AND "
                "state IN ('frozen','reserved_pre_effect',"
                "'execution_committed') LIMIT 1", (old_context,),
            ).fetchone()
            if open_batch is not None:
                from core._a2a_turn_acquire import AguiTurnBusy
                raise AguiTurnBusy(open_batch["run_id"])
            context_row = connection.execute(
                "SELECT internal_conversation_id FROM a2a_contexts "
                "WHERE context_id=?", (old_context,),
            ).fetchone()
            old_internal = context_row["internal_conversation_id"] \
                if context_row else ""
            marker = old_internal if (isolated and old_internal) else ""
            new_generation = current + 1
            closed_before = int(row["closed_before_generation"])
            if close_generation:
                closed_before = max(closed_before, new_generation)
            connection.execute(
                "UPDATE agui_threads SET generation=?, "
                "closed_before_generation=?, pending_cleanup_conversation=?, "
                "updated_at=? WHERE publication_id=? AND key_id=? AND "
                "thread_id=? AND generation=?",
                (new_generation, closed_before, marker, now,
                 pub_id, key_id, thread_id, current),
            )
            connection.execute(
                "DELETE FROM a2a_contexts WHERE context_id=?", (old_context,))
            connection.execute(
                "DELETE FROM agui_run_tombstones WHERE publication_id=? AND "
                "key_id=? AND thread_id=? AND generation<=?",
                (pub_id, key_id, thread_id, current),
            )
            # The old context's runs (and their journal, by cascade) go
            # with it — a rotated thread keeps nothing replayable. Its
            # admissions and fence row cascade with the context row (the
            # explicit fence delete below is belt-and-braces).
            self.drop_agui_runs_for_context(connection, old_context)  # type: ignore[attr-defined]
            connection.execute(
                "DELETE FROM agui_fences WHERE context_id=?", (old_context,))
        if marker and delete_conversation is not None:
            # Outside the store lock: the deletion may do filesystem I/O
            # and take other locks; the committed marker keeps further
            # rotations refused until the CAS clear below succeeds.
            delete_conversation(marker)
            with self._lock, self._immediate() as connection:
                connection.execute(
                    "UPDATE agui_threads SET pending_cleanup_conversation='', "
                    "updated_at=? WHERE publication_id=? AND key_id=? AND "
                    "thread_id=? AND pending_cleanup_conversation=? AND "
                    "generation=?",
                    (time.time(), pub_id, key_id, thread_id, marker,
                     new_generation),
                )
        return {
            "thread_id": thread_id,
            "generation": new_generation,
            "context_id": self.agui_context_id(pub_id, key_id, thread_id,
                                               new_generation),
        }

    # ── TTL sweep ────────────────────────────────────────────────────

    def sweep_agui_threads(
        self, *,
        is_conversation_expired: Callable[[str], bool],
        delete_conversation: Callable[[str], None],
        has_active_lease: Optional[Callable[[str], bool]] = None,
        min_interval_seconds: float = 300.0,
        now: Optional[float] = None,
    ) -> int:
        """Rotate every expired AG-UI thread of TTL-bearing publications.

        Scope is structural: only ``agui_``-prefixed contexts, only
        publications with ``thread_ttl_seconds > 0`` and an ``isolated``
        policy — server-issued A2A contexts (``ctx_`` prefix), shared
        conversations and TTL=0 publications never enter the query.
        The throttle reservation is a single transaction, so concurrent
        workers on the same database pass it at most once per interval.
        ``has_active_lease`` is an optional extra filter; the hard
        guard is ``rotate_agui_thread`` refusing active admissions
        atomically (``AguiTurnBusy``). Returns the number of threads rotated
        (0 when throttled).
        """
        current = now if now is not None else time.time()
        with self._lock:
            with self._immediate() as connection:
                row = connection.execute(
                    "SELECT v FROM agui_meta WHERE k='last_sweep_at'").fetchone()
                if row:
                    try:
                        if current - float(row["v"]) < min_interval_seconds:
                            return 0
                    except ValueError:
                        pass
                connection.execute(
                    "INSERT INTO agui_meta (k, v) VALUES ('last_sweep_at', ?) "
                    "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (str(current),))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT t.publication_id, t.key_id, t.thread_id, t.generation "
                "FROM agui_threads t "
                "JOIN a2a_publications p ON p.publication_id = t.publication_id "
                "WHERE p.thread_ttl_seconds > 0 AND p.context_policy = 'isolated' "
                "AND t.pending_cleanup_conversation = ''"
            ).fetchall()
        swept = 0
        for row in rows:
            generation = int(row["generation"])
            context_id = self.agui_context_id(
                row["publication_id"], row["key_id"], row["thread_id"],
                generation)
            with self._lock, self._connect() as connection:
                context = connection.execute(
                    "SELECT internal_conversation_id FROM a2a_contexts "
                    "WHERE context_id=?", (context_id,),
                ).fetchone()
            if context is None:
                continue
            if has_active_lease is not None and has_active_lease(context_id):
                continue
            if not is_conversation_expired(context["internal_conversation_id"]):
                continue
            publication = self.get_publication(row["publication_id"])  # type: ignore[attr-defined]
            if publication is None:
                continue
            from core._a2a_turn_acquire import AguiTurnBusy
            try:
                self.rotate_agui_thread(
                    publication, row["key_id"], row["thread_id"],
                    expected_generation=generation,
                    delete_conversation=delete_conversation)
            except (AguiThreadRotated, AguiCleanupPending, AguiTurnBusy):
                continue
            swept += 1
        return swept

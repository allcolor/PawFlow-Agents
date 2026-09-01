"""Durable AG-UI run event journal (plan v8.2, B1-J / B1-G — step P1-B).

Run execution is decoupled from any HTTP subscriber: events are appended
to a durable per-run journal with monotonically increasing sequence
numbers, committed atomically with the run's counters so a subscriber
only ever observes committed sequences (it replays the prefix then
tails).

Contracts implemented here:

- **Monotonic sequences**: one ``BEGIN IMMEDIATE`` transaction per
  append assigns ``seq = committed_sequence + 1`` and advances the
  counter — no gaps, no torn reads.
- **Per-run quota with a terminal reserve**: a non-terminal append that
  would exceed the event or byte quota is replaced, in the SAME
  transaction, by a journaled terminal ``RUN_ERROR
  {reason: "run_quota_exceeded"}`` — the terminal event itself is
  exempt from the quota, so the server can always record how a run
  ended (:class:`AguiRunQuotaExceeded` tells the caller).
- **Terminal-only pruning + replay watermark**: only terminal runs are
  ever pruned; pruning deletes the event rows, advances
  ``replay_watermark`` to ``committed_sequence`` and keeps the compact
  run row as the terminal snapshot. Reading below the watermark raises
  :class:`AguiReplayExpired` carrying that snapshot — never a silent
  rejoin past missed events. Active runs are never pruned and never
  expire replay.
- Run rows of a rotated thread's old context are dropped by the
  rotation itself (``ON DELETE CASCADE`` from ``agui_runs`` to the
  journal keeps that bounded).
- **Authoritative subscriber takeover**: every initial stream and attach
  atomically advances a durable per-run subscriber epoch. Journal reads
  made by an older epoch fail closed, so only the newest subscriber may
  continue emitting frames while the pilot remains independent.

Token-addressed attach (``attach_token``) arrives with the v8.2 token
scheme in step P1-D; this layer addresses runs by their internal
identity ``(context_id, run_id)``.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any, Callable, Dict, List, Optional

# Defaults are deliberately generous; the endpoint layer may tighten
# them per publication later (§9.1 of the plan).
DEFAULT_MAX_EVENTS_PER_RUN = 10_000
DEFAULT_MAX_BYTES_PER_RUN = 8 * 1024 * 1024
DEFAULT_JOURNAL_RETENTION_SECONDS = 24 * 3600.0

QUOTA_TERMINAL_EVENT = (
    '{"type": "RUN_ERROR", "message": "run_quota_exceeded", '
    '"code": "run_quota_exceeded"}'
)


class AguiRunUnknown(Exception):
    """No such run in the journal."""


class AguiRunTerminal(Exception):
    """A non-terminal append was attempted on an already-terminal run."""

    def __init__(self, outcome: str) -> None:
        super().__init__(f"run is terminal ({outcome})")
        self.outcome = outcome


class AguiRunQuotaExceeded(Exception):
    """The run hit its event/byte quota and was terminated with a
    journaled ``run_quota_exceeded`` error event."""


class AguiManagedSuccessWithoutBatch(Exception):
    """A managed run with pending emitted calls tried to terminalize as
    ``success`` through the journal alone — that would publish
    ``RUN_FINISHED`` without a claimable batch. T-freeze inside
    ``finish_agui_turn`` is the ONLY path to a managed success (B1-D)."""


class AguiReplayExpired(Exception):
    """The requested replay position is below the pruning watermark.

    Carries the terminal snapshot of the run — final state, outcome and
    committed sequence — which is the safe answer the endpoint returns
    instead of silently skipping the missing span.
    """

    def __init__(self, snapshot: Dict[str, Any]) -> None:
        super().__init__("replay_expired")
        self.snapshot = snapshot


class AguiSubscriberTakenOver(Exception):
    """The journal tail no longer owns the run's subscriber epoch."""


class TurnJournalMixin:
    """Durable event journal for AG-UI runs."""

    # Provided by A2AStore / TurnMachineMixin:
    _lock: Any
    _connect: Callable[[], Any]
    _immediate: Callable[[], Any]

    @staticmethod
    def _initialize_journal_tables(connection) -> None:
        has_runs = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='agui_runs'"
        ).fetchone() is not None
        if has_runs and not connection.execute(
                "PRAGMA foreign_key_list(agui_runs)").fetchall():
            # One-shot migration: early FK-less tables are dropped.
            connection.executescript(
                "DROP TABLE IF EXISTS agui_journal;"
                "DROP TABLE IF EXISTS agui_runs;")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS agui_runs (
                context_id TEXT NOT NULL REFERENCES a2a_contexts(context_id)
                    ON DELETE CASCADE,
                run_id TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'active',
                outcome TEXT NOT NULL DEFAULT '',
                event_count INTEGER NOT NULL DEFAULT 0,
                byte_count INTEGER NOT NULL DEFAULT 0,
                committed_sequence INTEGER NOT NULL DEFAULT 0,
                replay_watermark INTEGER NOT NULL DEFAULT 0,
                subscriber_epoch INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (context_id, run_id)
            );

            CREATE TABLE IF NOT EXISTS agui_journal (
                context_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                event_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY (context_id, run_id, seq),
                FOREIGN KEY (context_id, run_id)
                    REFERENCES agui_runs(context_id, run_id)
                    ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_agui_runs_state
                ON agui_runs(state, updated_at);
            """
        )
        columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(agui_runs)").fetchall()
        }
        if "subscriber_epoch" not in columns:
            connection.execute(
                "ALTER TABLE agui_runs ADD COLUMN subscriber_epoch "
                "INTEGER NOT NULL DEFAULT 0")

    # ── run lifecycle (journal view — the full B1-O machine is P1-C) ──

    def open_agui_run(self, context_id: str, run_id: str) -> Dict[str, Any]:
        """Create the run's journal row (idempotent).

        The context must exist: opening a run on a rotated or unknown
        context raises :class:`AguiRunUnknown` (FK-enforced). Insert and
        read-back share one transaction, so a concurrent rotation can
        never leave this method returning a phantom row.
        """
        if not context_id or not run_id:
            raise ValueError("context_id and run_id are required")
        now = time.time()
        with self._lock, self._immediate() as connection:
            try:
                connection.execute(
                    "INSERT OR IGNORE INTO agui_runs (context_id, run_id, state, "
                    "outcome, event_count, byte_count, committed_sequence, "
                    "replay_watermark, created_at, updated_at) "
                    "VALUES (?, ?, 'active', '', 0, 0, 0, 0, ?, ?)",
                    (context_id, run_id, now, now),
                )
            except sqlite3.IntegrityError as exc:
                raise AguiRunUnknown(
                    f"unknown AG-UI context {context_id}") from exc
            row = connection.execute(
                "SELECT * FROM agui_runs WHERE context_id=? AND run_id=?",
                (context_id, run_id),
            ).fetchone()
            if row is None:
                raise AguiRunUnknown(f"unknown AG-UI context {context_id}")
            return dict(row)

    def get_agui_run(self, context_id: str, run_id: str) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agui_runs WHERE context_id=? AND run_id=?",
                (context_id, run_id),
            ).fetchone()
        return dict(row) if row else None

    def acquire_agui_subscriber(
        self, context_id: str, run_id: str, *, after_seq: int = 0,
    ) -> Dict[str, int]:
        """Atomically validate a replay cursor and take over one run's SSE.

        Every successful initial stream or attach advances the persisted
        epoch exactly once. An invalid future cursor mutates nothing and
        therefore cannot evict the current subscriber.
        """
        after_seq = int(after_seq)
        if after_seq < 0:
            raise ValueError("after_seq must be >= 0")
        now = time.time()
        with self._lock, self._immediate() as connection:
            row = connection.execute(
                "SELECT committed_sequence, subscriber_epoch FROM "
                "agui_runs WHERE context_id=? AND run_id=?",
                (context_id, run_id),
            ).fetchone()
            if row is None:
                raise AguiRunUnknown(f"unknown run {run_id}")
            committed = int(row["committed_sequence"])
            if after_seq > committed:
                raise ValueError(
                    "after_seq is beyond the committed sequence")
            epoch = int(row["subscriber_epoch"]) + 1
            connection.execute(
                "UPDATE agui_runs SET subscriber_epoch=?, updated_at=? "
                "WHERE context_id=? AND run_id=?",
                (epoch, now, context_id, run_id),
            )
            return {"subscriber_epoch": epoch,
                    "committed_sequence": committed}

    def is_agui_subscriber_current(self, context_id: str, run_id: str,
                                   subscriber_epoch: int) -> bool:
        """Return whether ``subscriber_epoch`` still owns the SSE tail."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT subscriber_epoch FROM agui_runs WHERE "
                "context_id=? AND run_id=?", (context_id, run_id),
            ).fetchone()
        return bool(row is not None and
                    int(row["subscriber_epoch"]) == int(subscriber_epoch))

    def agui_terminal_snapshot(self, run: Dict[str, Any],
                               connection=None) -> Dict[str, Any]:
        """The safe answer to an expired replay. When the run's batch is
        still OPEN, the snapshot carries its ``batch_token`` (B1-G): a
        reconnecting widget whose journal span was pruned must still be
        able to claim the batch."""
        snapshot = {
            "run_id": run["run_id"],
            "state": run["state"],
            "outcome": run["outcome"],
            "committed_sequence": int(run["committed_sequence"]),
            "replay_watermark": int(run["replay_watermark"]),
        }
        context_id = str(run.get("context_id") or "")
        if context_id:
            if connection is not None:
                token = self._batch_token_in_tx(  # type: ignore[attr-defined]
                    connection, context_id, run["run_id"])
            else:
                with self._lock, self._connect() as own_connection:
                    token = self._batch_token_in_tx(  # type: ignore[attr-defined]
                        own_connection, context_id, run["run_id"])
            if token:
                snapshot["batch_token"] = token
        return snapshot

    # ── append ───────────────────────────────────────────────────────

    @staticmethod
    def _refuse_unfrozen_managed_success(connection, context_id: str,
                                         run_id: str) -> None:
        """T-freeze is the ONLY path to a managed success with pending
        calls: a success published through the journal alone would leave
        ``RUN_FINISHED`` without a claimable batch (B1-D)."""
        publication = connection.execute(
            "SELECT p.managed_mode FROM a2a_contexts c "
            "JOIN a2a_publications p ON p.publication_id=c.publication_id "
            "WHERE c.context_id=?", (context_id,)).fetchone()
        if publication is None or not publication["managed_mode"]:
            return
        pending = connection.execute(
            "SELECT 1 FROM agui_calls WHERE context_id=? AND run_id=? AND "
            "state='emitted' LIMIT 1", (context_id, run_id)).fetchone()
        if pending is None:
            return
        batch = connection.execute(
            "SELECT 1 FROM agui_batches WHERE context_id=? AND run_id=? "
            "LIMIT 1", (context_id, run_id)).fetchone()
        if batch is None:
            raise AguiManagedSuccessWithoutBatch(
                "managed success with pending calls must be terminalized "
                "through finish_agui_turn (T-freeze)")

    def append_agui_event(
        self, context_id: str, run_id: str, event_json: str, *,
        terminal: bool = False, outcome: str = "",
        max_events: int = DEFAULT_MAX_EVENTS_PER_RUN,
        max_bytes: int = DEFAULT_MAX_BYTES_PER_RUN,
    ) -> int:
        """Append one event; returns its committed sequence number.

        Terminal appends (``terminal=True`` with an ``outcome``) are
        exempt from the quota — the reserve guarantees a run can always
        record how it ended. A non-terminal append over quota writes the
        ``run_quota_exceeded`` terminal event instead and raises
        :class:`AguiRunQuotaExceeded`; appending to a terminal run
        raises :class:`AguiRunTerminal`.
        """
        if terminal and not outcome:
            raise ValueError("A terminal event requires an outcome")
        payload = str(event_json)
        now = time.time()
        with self._lock, self._immediate() as connection:
            row = connection.execute(
                "SELECT state, outcome, event_count, byte_count, "
                "committed_sequence FROM agui_runs WHERE context_id=? AND "
                "run_id=?", (context_id, run_id),
            ).fetchone()
            if row is None:
                raise AguiRunUnknown(f"unknown run {run_id}")
            if terminal and outcome == "success":
                self._refuse_unfrozen_managed_success(
                    connection, context_id, run_id)
            if row["state"] != "active":
                raise AguiRunTerminal(str(row["outcome"]))
            sequence = int(row["committed_sequence"]) + 1
            payload_bytes = len(payload.encode("utf-8"))
            over_quota = (not terminal) and (
                int(row["event_count"]) + 1 > int(max_events)
                or int(row["byte_count"]) + payload_bytes > int(max_bytes))
            if over_quota:
                payload = QUOTA_TERMINAL_EVENT
                payload_bytes = len(payload.encode("utf-8"))
                terminal = True
                outcome = "run_quota_exceeded"
            connection.execute(
                "INSERT INTO agui_journal (context_id, run_id, seq, "
                "event_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (context_id, run_id, sequence, payload, now),
            )
            connection.execute(
                "UPDATE agui_runs SET committed_sequence=?, event_count="
                "event_count+1, byte_count=byte_count+?, state=?, outcome=?, "
                "updated_at=? WHERE context_id=? AND run_id=?",
                (sequence, payload_bytes,
                 "terminal" if terminal else "active",
                 outcome if terminal else "",
                 now, context_id, run_id),
            )
        # Raised AFTER the transaction committed: the quota-terminal
        # event must be durable (raising inside _immediate() would roll
        # it back).
        if over_quota:
            raise AguiRunQuotaExceeded(
                f"run {run_id} exceeded its journal quota")
        return sequence

    # ── read / replay ────────────────────────────────────────────────

    def read_agui_events(
        self, context_id: str, run_id: str, *, after_seq: int = 0,
        limit: int = 1000, subscriber_epoch: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Read the committed tail after ``after_seq`` (gapless replay).

        Run lookup, cursor validation and the event select share ONE
        transaction, so a concurrent prune can never turn a valid replay
        into a silently empty tail: the caller gets either the pre-prune
        events or :class:`AguiReplayExpired` with the terminal snapshot
        (only possible for terminal, pruned runs). Cursors are domain-
        checked: ``0 <= after_seq <= committed_sequence`` and
        ``limit > 0``.
        """
        after_seq = int(after_seq)
        limit = int(limit)
        if limit <= 0:
            raise ValueError("limit must be positive")
        if after_seq < 0:
            raise ValueError("after_seq must be >= 0")
        with self._lock, self._immediate() as connection:
            row = connection.execute(
                "SELECT * FROM agui_runs WHERE context_id=? AND run_id=?",
                (context_id, run_id),
            ).fetchone()
            if row is None:
                raise AguiRunUnknown(f"unknown run {run_id}")
            run = dict(row)
            if subscriber_epoch is not None and int(
                    run["subscriber_epoch"]) != int(subscriber_epoch):
                raise AguiSubscriberTakenOver(
                    f"subscriber epoch {subscriber_epoch} was taken over")
            if after_seq > int(run["committed_sequence"]):
                raise ValueError(
                    "after_seq is beyond the committed sequence")
            if after_seq < int(run["replay_watermark"]):
                raise AguiReplayExpired(
                    self.agui_terminal_snapshot(run, connection))
            rows = connection.execute(
                "SELECT seq, event_json, created_at FROM agui_journal "
                "WHERE context_id=? AND run_id=? AND seq>? "
                "ORDER BY seq LIMIT ?",
                (context_id, run_id, after_seq, limit),
            ).fetchall()
            return [dict(item) for item in rows]

    # ── pruning ──────────────────────────────────────────────────────

    def prune_agui_journals(
        self, *,
        retention_seconds: float = DEFAULT_JOURNAL_RETENTION_SECONDS,
        now: Optional[float] = None,
    ) -> int:
        """Prune the journals of old TERMINAL runs (active runs never).

        Deletes the event rows, advances ``replay_watermark`` to
        ``committed_sequence`` and keeps the run row as the terminal
        snapshot. Returns the number of runs pruned.
        """
        current = now if now is not None else time.time()
        cutoff = current - max(0.0, float(retention_seconds))
        pruned = 0
        with self._lock, self._immediate() as connection:
            rows = connection.execute(
                "SELECT context_id, run_id, committed_sequence, "
                "replay_watermark FROM agui_runs WHERE state='terminal' "
                "AND updated_at<=? AND replay_watermark<committed_sequence",
                (cutoff,),
            ).fetchall()
            for row in rows:
                connection.execute(
                    "DELETE FROM agui_journal WHERE context_id=? AND run_id=?",
                    (row["context_id"], row["run_id"]),
                )
                connection.execute(
                    "UPDATE agui_runs SET replay_watermark=committed_sequence, "
                    "updated_at=? WHERE context_id=? AND run_id=?",
                    (current, row["context_id"], row["run_id"]),
                )
                pruned += 1
        return pruned

    def drop_agui_runs_for_context(self, connection, context_id: str) -> None:
        """Delete a context's runs (journal cascades). Caller's transaction."""
        connection.execute(
            "DELETE FROM agui_runs WHERE context_id=?", (context_id,))

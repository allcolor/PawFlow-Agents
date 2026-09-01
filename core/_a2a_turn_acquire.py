"""AG-UI turn admission, outbox, run lifecycle and fencing (plan v8.2,
B1-A / B1-I / B1-O / B1-P — step P1-C).

One durable state machine, in the same SQLite commit domain as the
thread/generation tables (P1-A) and the event journal (P1-B):

- **Acquire** (B1-A): branch order is generation check FIRST (delegated
  to :meth:`resolve_agui_thread`), then the idempotency key lookup
  ``(publication, key, thread, generation, run_id)`` — same ``body_hash``
  → replay (nothing consumed), different hash →
  :class:`AguiIdempotencyConflict`, tombstone →
  :class:`AguiIdempotencyExpired` — and only for genuinely new runs the
  admission itself: one-writer per thread
  (:class:`AguiTurnBusy` while another admission is not terminal),
  parent linkage when pending frontend calls exist
  (:class:`AguiParentMismatch`), then ONE transaction that reserves the
  admission, opens the journal run row and advances the context fence.
- **Deterministic turn id**: ``turn_id = "aguiturn_" +
  digest(context_id | run_id)`` — generation-aware by construction
  because the context id already encodes the generation (P1-A).
- **Outbox claim/ack** (B1-O): the reserved admission carries the turn
  payload; a dispatcher CAS-claims it (``owner`` + ``deadline``), the
  runtime acks it (``accepted`` = durably persisted, payload cleared).
  A crash after claim and before ack is recovered by a CAS re-claim
  after the deadline; an ack for an already-accepted row answers
  ``True`` so a re-claimer can complete a lost ack (dedupe answer).
- **Run lifecycle**: ``reserved → dispatching → accepted → running →
  terminal | orphaned``; the running lease is bound to a worker with a
  heartbeat and the monotonic per-context **fence token**; wrong-worker
  operations fail (:class:`AguiFenceLost`) and never touch the row.
  Heartbeat expiry moves the run to ``orphaned`` and journals a
  terminal ``RUN_ERROR {run_lost}`` in the SAME transaction — never a
  silent relaunch.
- **Frontend-call ledger** (B1.3/B1-P): emitted tool calls are recorded
  per run; any non-success terminal atomically abandons the run's
  pending calls (they stop being consumable and stop blocking the next
  acquire); consumption validates the EXACT pending set (all of them,
  only them) exactly once.

Relay-level fence enforcement and the managed batch/receipt machine are
the next steps (P1-E, P1-D).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from typing import Any, Callable, Dict, List, Optional

from core._a2a_turn_batch import AguiBatchIncomplete
from core._a2a_turn_journal import AguiRunTerminal, AguiRunUnknown
from core._a2a_turn_machine import AguiThreadRotated

_ACTIVE_STATES = ("reserved", "dispatching", "accepted", "running")
RUN_LOST_EVENT = '{"type": "RUN_ERROR", "message": "run_lost", "code": "run_lost"}'
RUN_FINISHED_EVENT = '{"type": "RUN_FINISHED"}'


class AguiIdempotencyConflict(Exception):
    """Same run id replayed with a different canonical body."""


class AguiIdempotencyExpired(Exception):
    """The run id is only known through a tombstone — never re-executed."""


class AguiTurnBusy(Exception):
    """Another run of the same thread is still active (one-writer)."""

    def __init__(self, active_run_id: str) -> None:
        super().__init__(f"thread busy: run {active_run_id} is active")
        self.active_run_id = active_run_id


class AguiParentMismatch(Exception):
    """Pending frontend calls exist and the follow-up run does not name
    their run as its parent (or the result set does not match)."""


class AguiFenceLost(Exception):
    """The caller's worker/fence no longer owns the run."""


class TurnAcquireMixin:
    """Admission, outbox, lifecycle, fencing and call-ledger primitives."""

    # Provided by A2AStore / sibling mixins:
    _lock: Any
    _connect: Callable[[], Any]
    _immediate: Callable[[], Any]

    @staticmethod
    def _initialize_acquire_tables(connection) -> None:
        # Fence migration (anti-ABA, crash-atomic): the whole
        # introspection + copy runs under ONE explicit BEGIN IMMEDIATE —
        # two concurrent startups serialize, and an interruption at any
        # point rolls back completely. A residual intermediate table
        # from an interrupted older run is cleaned first, so restart
        # always succeeds.
        if connection.in_transaction:
            connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute("DROP TABLE IF EXISTS agui_fences_migrated")
            has_fences = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND "
                "name='agui_fences'").fetchone() is not None
            if has_fences and not connection.execute(
                    "PRAGMA foreign_key_list(agui_fences)").fetchall():
                # Preserve the high-water tokens of still-live contexts:
                # dropping them would reopen the fence to an ABA (a
                # fresh counter could re-issue an old token).
                connection.execute(
                    "CREATE TABLE agui_fences_migrated ("
                    "context_id TEXT PRIMARY KEY REFERENCES "
                    "a2a_contexts(context_id) ON DELETE CASCADE, "
                    "token INTEGER NOT NULL DEFAULT 0)")
                connection.execute(
                    "INSERT INTO agui_fences_migrated "
                    "SELECT f.context_id, f.token FROM agui_fences f "
                    "JOIN a2a_contexts c ON c.context_id = f.context_id")
                connection.execute("DROP TABLE agui_fences")
                connection.execute(
                    "ALTER TABLE agui_fences_migrated RENAME TO agui_fences")
            connection.execute("COMMIT")
        except Exception:
            try:
                connection.execute("ROLLBACK")
            except Exception:  # nosec B110 - preserve the migration error
                pass
            raise
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS agui_admissions (
                publication_id TEXT NOT NULL,
                key_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                generation INTEGER NOT NULL,
                run_id TEXT NOT NULL,
                context_id TEXT NOT NULL REFERENCES a2a_contexts(context_id)
                    ON DELETE CASCADE,
                turn_id TEXT NOT NULL,
                body_hash TEXT NOT NULL,
                parent_run_id TEXT NOT NULL DEFAULT '',
                state TEXT NOT NULL DEFAULT 'reserved',
                outcome TEXT NOT NULL DEFAULT '',
                fence_token INTEGER NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '',
                claim_owner TEXT NOT NULL DEFAULT '',
                claim_deadline REAL NOT NULL DEFAULT 0,
                lease_owner TEXT NOT NULL DEFAULT '',
                lease_heartbeat_at REAL NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (publication_id, key_id, thread_id, generation, run_id)
            );
            CREATE INDEX IF NOT EXISTS idx_agui_admissions_context
                ON agui_admissions(context_id, state);
            CREATE INDEX IF NOT EXISTS idx_agui_admissions_outbox
                ON agui_admissions(state, claim_deadline);

            CREATE TABLE IF NOT EXISTS agui_fences (
                context_id TEXT PRIMARY KEY REFERENCES a2a_contexts(context_id)
                    ON DELETE CASCADE,
                token INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS agui_calls (
                context_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                tool_call_id TEXT NOT NULL,
                tool TEXT NOT NULL DEFAULT '',
                state TEXT NOT NULL DEFAULT 'emitted',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (context_id, run_id, tool_call_id),
                FOREIGN KEY (context_id, run_id)
                    REFERENCES agui_runs(context_id, run_id)
                    ON DELETE CASCADE
            );
            """
        )

    # ── identities ───────────────────────────────────────────────────

    @staticmethod
    def agui_turn_id(context_id: str, run_id: str) -> str:
        digest = hashlib.sha256(
            f"{context_id}|{run_id}".encode("utf-8")).hexdigest()[:24]
        return "aguiturn_" + digest

    @staticmethod
    def agui_body_hash(body: Any) -> str:
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    # ── acquire ──────────────────────────────────────────────────────

    def acquire_agui_turn(
        self, publication: Dict[str, Any], key_id: str, thread_id: str,
        generation: int, run_id: str, body_hash: str, *,
        payload_json: str = "", parent_run_id: str = "",
        delete_conversation: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        """Admit (or replay) one run. See module docstring for the order.

        Returns the admission row plus ``replay: bool``. The journal run
        row is opened and the context fence advanced in the SAME
        transaction as the reservation.
        """
        if not run_id or not body_hash:
            raise ValueError("run_id and body_hash are required")
        # Branch 0 — generation checks (raises AguiThreadRotated), and
        # the live context under the generation-aware identity.
        context = self.resolve_agui_thread(  # type: ignore[attr-defined]
            publication, key_id, thread_id, generation,
            delete_conversation=delete_conversation)
        context_id = context["context_id"]
        pub_id = publication["publication_id"]
        now = time.time()
        with self._lock, self._immediate() as connection:
            # Branch 0 (re-checked INSIDE this transaction): a rotation
            # racing between resolve and this admission must surface as
            # thread_rotated, never as an FK error on the insert.
            thread_row = connection.execute(
                "SELECT generation, closed_before_generation FROM agui_threads "
                "WHERE publication_id=? AND key_id=? AND thread_id=?",
                (pub_id, key_id, thread_id),
            ).fetchone()
            if thread_row is None:
                raise AguiThreadRotated(0)
            current_generation = int(thread_row["generation"])
            if int(generation) < int(thread_row["closed_before_generation"]) \
                    or int(generation) != current_generation:
                raise AguiThreadRotated(current_generation)
            if connection.execute(
                    "SELECT 1 FROM a2a_contexts WHERE context_id=?",
                    (context_id,)).fetchone() is None:
                raise AguiThreadRotated(current_generation)
            # Branch 1 — idempotency key lookup.
            row = connection.execute(
                "SELECT * FROM agui_admissions WHERE publication_id=? AND "
                "key_id=? AND thread_id=? AND generation=? AND run_id=?",
                (pub_id, key_id, thread_id, int(generation), run_id),
            ).fetchone()
            if row is not None:
                if row["body_hash"] != body_hash:
                    raise AguiIdempotencyConflict(
                        "run replayed with a different body")
                admission = dict(row)
                admission["replay"] = True
                return admission
            tombstone = connection.execute(
                "SELECT 1 FROM agui_run_tombstones WHERE publication_id=? AND "
                "key_id=? AND thread_id=? AND generation=? AND run_id=?",
                (pub_id, key_id, thread_id, int(generation), run_id),
            ).fetchone()
            if tombstone is not None:
                raise AguiIdempotencyExpired(
                    "run is only known through a tombstone")
            # Branch 2 — new runs only. One writer per thread.
            active = connection.execute(
                "SELECT run_id FROM agui_admissions WHERE context_id=? AND "
                "state IN (?, ?, ?, ?)",
                (context_id, *_ACTIVE_STATES),
            ).fetchone()
            if active is not None:
                raise AguiTurnBusy(active["run_id"])
            # Managed mode: an open batch pins the thread; a complete one
            # is consumed here, by the follow-up that names it as parent
            # (the agent is fed from the server-held deposits).
            batch = connection.execute(
                "SELECT run_id, state FROM agui_batches WHERE context_id=? "
                "AND state IN ('frozen','reserved_pre_effect',"
                "'execution_committed','complete') LIMIT 1",
                (context_id,)).fetchone()
            parent_bound = False
            if batch is not None:
                if batch["state"] != "complete":
                    raise AguiBatchIncomplete(batch["run_id"])
                if parent_run_id != batch["run_id"]:
                    raise AguiParentMismatch(
                        "a completed batch exists; the follow-up run must "
                        f"name its run as parent ({batch['run_id']})")
                self._consume_batch_in_tx(  # type: ignore[attr-defined]
                    connection, context_id, batch["run_id"], now)
                parent_bound = True
            else:
                # Classic mode: pending (non-abandoned) emitted calls of a
                # previous run must be named as the parent.
                pending_parent = connection.execute(
                    "SELECT DISTINCT run_id FROM agui_calls WHERE "
                    "context_id=? AND state='emitted'", (context_id,),
                ).fetchall()
                if pending_parent:
                    parents = {item["run_id"] for item in pending_parent}
                    if parent_run_id not in parents:
                        raise AguiParentMismatch(
                            "pending frontend calls exist; the follow-up run "
                            f"must name their run as parent ({sorted(parents)})")
                    parent_bound = True
            # Fail closed: a declared parent that binds to nothing (no
            # consumable batch, no pending calls) is a stale or replayed
            # follow-up — admitting it would silently drop its results.
            if parent_run_id and not parent_bound:
                raise AguiParentMismatch(
                    f"parentRunId '{parent_run_id}' names no consumable "
                    "batch and no pending frontend calls")
            # Reserve + open journal + advance fence — one transaction.
            connection.execute(
                "INSERT INTO agui_fences (context_id, token) VALUES (?, 0) "
                "ON CONFLICT(context_id) DO NOTHING", (context_id,))
            connection.execute(
                "UPDATE agui_fences SET token=token+1 WHERE context_id=?",
                (context_id,))
            fence = connection.execute(
                "SELECT token FROM agui_fences WHERE context_id=?",
                (context_id,)).fetchone()["token"]
            turn_id = self.agui_turn_id(context_id, run_id)
            try:
                connection.execute(
                    "INSERT INTO agui_admissions (publication_id, key_id, "
                    "thread_id, generation, run_id, context_id, turn_id, "
                    "body_hash, parent_run_id, state, outcome, fence_token, "
                    "payload_json, claim_owner, claim_deadline, lease_owner, "
                    "lease_heartbeat_at, created_at, updated_at) VALUES "
                    "(?, ?, ?, ?, ?, ?, ?, ?, ?, 'reserved', '', ?, ?, '', 0, "
                    "'', 0, ?, ?)",
                    (pub_id, key_id, thread_id, int(generation), run_id,
                     context_id, turn_id, body_hash, parent_run_id, fence,
                     payload_json, now, now),
                )
            except sqlite3.IntegrityError as exc:
                # Belt-and-braces: any FK loss inside the window IS a
                # rotation, and must read as one.
                raise AguiThreadRotated(current_generation) from exc
            connection.execute(
                "INSERT OR IGNORE INTO agui_runs (context_id, run_id, state, "
                "outcome, event_count, byte_count, committed_sequence, "
                "replay_watermark, created_at, updated_at) "
                "VALUES (?, ?, 'active', '', 0, 0, 0, 0, ?, ?)",
                (context_id, run_id, now, now),
            )
            # Mint the run's attach/cancel handle in the SAME transaction
            # (B1-J): the credentials exist from the moment the admission
            # is durable, and replays re-derive the same bytes.
            self._ensure_run_tokens_in_tx(  # type: ignore[attr-defined]
                connection, context_id, run_id, now)
            admission = dict(connection.execute(
                "SELECT * FROM agui_admissions WHERE context_id=? AND run_id=?",
                (context_id, run_id)).fetchone())
            admission["replay"] = False
            return admission

    def get_agui_admission(self, context_id: str, run_id: str
                           ) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agui_admissions WHERE context_id=? AND run_id=?",
                (context_id, run_id)).fetchone()
        return dict(row) if row else None

    # ── outbox claim / ack ───────────────────────────────────────────

    def claim_agui_dispatch(self, owner: str, *, deadline_seconds: float = 60.0,
                            now: Optional[float] = None
                            ) -> Optional[Dict[str, Any]]:
        """CAS-claim one dispatchable admission (oldest first).

        Claims a ``reserved`` row, or re-claims a ``dispatching`` row
        whose claim deadline has passed (crash between claim and ack).
        """
        if not owner:
            raise ValueError("owner is required")
        current = now if now is not None else time.time()
        with self._lock, self._immediate() as connection:
            row = connection.execute(
                "SELECT context_id, run_id FROM agui_admissions WHERE "
                "state='reserved' OR (state='dispatching' AND "
                "claim_deadline<=?) ORDER BY created_at LIMIT 1",
                (current,),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE agui_admissions SET state='dispatching', "
                "claim_owner=?, claim_deadline=?, updated_at=? "
                "WHERE context_id=? AND run_id=? AND (state='reserved' OR "
                "(state='dispatching' AND claim_deadline<=?))",
                (owner, current + max(1.0, float(deadline_seconds)), current,
                 row["context_id"], row["run_id"], current),
            )
            claimed = connection.execute(
                "SELECT * FROM agui_admissions WHERE context_id=? AND "
                "run_id=? AND claim_owner=? AND state='dispatching'",
                (row["context_id"], row["run_id"], owner)).fetchone()
            return dict(claimed) if claimed else None

    def ack_agui_dispatch(self, context_id: str, run_id: str, owner: str
                          ) -> bool:
        """Mark the turn durably accepted by the runtime (payload cleared).

        Idempotent dedupe answer: an already-``accepted``/later row
        returns True so a re-claimer can complete a lost ack. A
        ``dispatching`` row owned by someone else returns False (the
        claim was legitimately re-claimed).
        """
        now = time.time()
        with self._lock, self._immediate() as connection:
            row = connection.execute(
                "SELECT state, claim_owner FROM agui_admissions "
                "WHERE context_id=? AND run_id=?",
                (context_id, run_id)).fetchone()
            if row is None:
                return False
            if row["state"] in ("accepted", "running", "terminal", "orphaned"):
                return True
            if row["state"] != "dispatching" or row["claim_owner"] != owner:
                return False
            connection.execute(
                "UPDATE agui_admissions SET state='accepted', "
                "payload_json='', claim_owner='', claim_deadline=0, "
                "updated_at=? WHERE context_id=? AND run_id=? AND "
                "state='dispatching' AND claim_owner=?",
                (now, context_id, run_id, owner),
            )
            return True

    # ── running lease / fencing ──────────────────────────────────────

    def adopt_agui_run(self, context_id: str, run_id: str, worker: str,
                       *, now: Optional[float] = None) -> int:
        """A SYNCHRONOUS pilot adopts its own just-acquired run end-to-end
        (``reserved``/``accepted`` → ``running``) in one transaction.

        The outbox claim/ack dance (``claim_agui_dispatch`` /
        ``ack_agui_dispatch``) exists for DISTRIBUTED workers that pull a
        run they did not create; an SSE pilot that just called
        ``acquire_agui_turn`` for THIS run takes it directly by id — it
        never races another pilot for the oldest ``reserved`` row.
        Idempotent (an already-``running`` run returns its fence token);
        returns the fence token the pilot must present at every effect
        boundary.
        """
        if not worker:
            raise ValueError("worker is required")
        current = now if now is not None else time.time()
        with self._lock, self._immediate() as connection:
            row = connection.execute(
                "SELECT state, fence_token, lease_owner FROM agui_admissions "
                "WHERE context_id=? AND run_id=?",
                (context_id, run_id)).fetchone()
            if row is None:
                raise AguiFenceLost("run has no admission to adopt")
            if row["state"] == "running":
                if row["lease_owner"] != worker:
                    raise AguiFenceLost("run is already owned by another "
                                        "worker")
                return int(row["fence_token"])  # idempotent re-adopt
            if row["state"] not in ("reserved", "accepted", "dispatching"):
                raise AguiFenceLost(
                    f"run is not adoptable (state={row['state']})")
            connection.execute(
                "UPDATE agui_admissions SET state='running', lease_owner=?, "
                "lease_heartbeat_at=?, claim_owner='', claim_deadline=0, "
                "payload_json='', updated_at=? WHERE context_id=? AND "
                "run_id=?",
                (worker, current, current, context_id, run_id))
            return int(row["fence_token"])

    def start_agui_turn(self, context_id: str, run_id: str, worker: str,
                        *, now: Optional[float] = None) -> int:
        """``accepted → running`` under a worker lease; returns the fence
        token the worker must present at every effect boundary."""
        if not worker:
            raise ValueError("worker is required")
        current = now if now is not None else time.time()
        with self._lock, self._immediate() as connection:
            row = connection.execute(
                "SELECT state, fence_token FROM agui_admissions "
                "WHERE context_id=? AND run_id=?",
                (context_id, run_id)).fetchone()
            if row is None or row["state"] != "accepted":
                raise AguiFenceLost(
                    f"run is not startable (state={row['state'] if row else 'missing'})")
            connection.execute(
                "UPDATE agui_admissions SET state='running', lease_owner=?, "
                "lease_heartbeat_at=?, updated_at=? WHERE context_id=? AND "
                "run_id=? AND state='accepted'",
                (worker, current, current, context_id, run_id),
            )
            return int(row["fence_token"])

    def heartbeat_agui_turn(self, context_id: str, run_id: str, worker: str,
                            *, now: Optional[float] = None) -> bool:
        current = now if now is not None else time.time()
        with self._lock, self._immediate() as connection:
            result = connection.execute(
                "UPDATE agui_admissions SET lease_heartbeat_at=?, updated_at=? "
                "WHERE context_id=? AND run_id=? AND state='running' AND "
                "lease_owner=?",
                (current, current, context_id, run_id, worker),
            )
            return bool(result.rowcount)

    def check_agui_fence(self, context_id: str, fence_token: int) -> bool:
        """True iff ``fence_token`` is the context's CURRENT fence."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT token FROM agui_fences WHERE context_id=?",
                (context_id,)).fetchone()
        return bool(row) and int(row["token"]) == int(fence_token)

    @staticmethod
    def _terminalize_run_in_tx(connection, context_id: str, run_id: str,
                               outcome: str, event_json: str,
                               now: float) -> str:
        """Bring the JOURNAL run to terminal inside the caller's tx and
        return the EFFECTIVE outcome.

        Active run → append the terminal event (all `append_agui_event`
        counter invariants preserved: sequence, event_count AND
        byte_count), mark the run terminal, return ``outcome``. A run
        whose journal is already terminal is left untouched and ITS
        outcome is returned — the journal wins, and the caller must use
        that effective outcome for the admission and the abandonment.
        A missing journal run fails closed (:class:`AguiRunUnknown`).
        """
        run = connection.execute(
            "SELECT state, outcome, committed_sequence FROM agui_runs "
            "WHERE context_id=? AND run_id=?",
            (context_id, run_id)).fetchone()
        if run is None:
            raise AguiRunUnknown(
                f"run {run_id} has no journal row — refusing to terminalize")
        if run["state"] != "active":
            return str(run["outcome"])
        sequence = int(run["committed_sequence"]) + 1
        connection.execute(
            "INSERT INTO agui_journal (context_id, run_id, seq, event_json, "
            "created_at) VALUES (?, ?, ?, ?, ?)",
            (context_id, run_id, sequence, event_json, now),
        )
        connection.execute(
            "UPDATE agui_runs SET state='terminal', outcome=?, "
            "committed_sequence=?, event_count=event_count+1, "
            "byte_count=byte_count+?, updated_at=? "
            "WHERE context_id=? AND run_id=?",
            (outcome, sequence, len(event_json.encode("utf-8")), now,
             context_id, run_id),
        )
        return outcome

    def finish_agui_turn(self, context_id: str, run_id: str, worker: str,
                         outcome: str, *, terminal_event_json: str = "",
                         batch_deadline_seconds: float = 900.0,
                         now: Optional[float] = None) -> None:
        """``running → terminal(outcome)`` — ONE transaction synchronizing
        the admission, the journal (terminal event + run state + both
        counters, unless the journal is already terminal — explicit
        reconciliation), the fence (invalidated on lease exit) and, for
        non-success outcomes, the abandonment of pending calls (B1-P).
        Wrong worker → :class:`AguiFenceLost`, row untouched.

        Managed vs classic is resolved from the PUBLICATION inside the
        transaction — a caller can never select the mode (B1-X). A
        managed success with pending emitted calls T-freezes the batch
        and journalizes ``RUN_FINISHED`` CARRYING the ``batch_token`` in
        the same transaction (B1-D)."""
        if not outcome:
            raise ValueError("outcome is required")
        if terminal_event_json:
            # The terminal TYPE is server-imposed: a success journalizes
            # RUN_FINISHED and nothing else; RUN_FINISHED is reserved
            # for success (no spoofed terminal shapes in the journal).
            try:
                provided_type = str(
                    json.loads(terminal_event_json).get("type", ""))
            except (ValueError, AttributeError):
                raise ValueError("terminal_event_json must be a JSON object")
            if outcome == "success" and provided_type != "RUN_FINISHED":
                raise ValueError(
                    "a success terminal must journalize RUN_FINISHED")
            if outcome != "success" and provided_type == "RUN_FINISHED":
                raise ValueError(
                    "RUN_FINISHED is reserved for success terminals")
        current = now if now is not None else time.time()
        with self._lock, self._immediate() as connection:
            row = connection.execute(
                "SELECT state, lease_owner FROM agui_admissions "
                "WHERE context_id=? AND run_id=?",
                (context_id, run_id)).fetchone()
            if row is None or row["state"] != "running" \
                    or row["lease_owner"] != worker:
                raise AguiFenceLost("run is not owned by this worker")
            # Mode is publication-pinned — read server-side, never taken
            # from the request (B1-X: no bypass of receipts/deposits).
            publication = connection.execute(
                "SELECT p.managed_mode FROM a2a_contexts c "
                "JOIN a2a_publications p "
                "ON p.publication_id=c.publication_id "
                "WHERE c.context_id=?", (context_id,)).fetchone()
            managed = bool(publication["managed_mode"]) \
                if publication is not None else False
            pending = connection.execute(
                "SELECT 1 FROM agui_calls WHERE context_id=? AND "
                "run_id=? AND state='emitted' LIMIT 1",
                (context_id, run_id)).fetchone()
            journal = connection.execute(
                "SELECT state FROM agui_runs WHERE context_id=? AND "
                "run_id=?", (context_id, run_id)).fetchone()
            batch_token = ""  # nosec B105 - opaque claim state, not a password
            if outcome == "success" and managed and pending is not None \
                    and journal is not None \
                    and journal["state"] == "active":
                # T-freeze (same transaction as the success terminal):
                # the run freezes its batch, takes the frontend-execution
                # lease and arms the absolute deadline (plan B1-X 1).
                batch_token = self._freeze_batch_in_tx(  # type: ignore[attr-defined]
                    connection, context_id, run_id, current,
                    batch_deadline_seconds=batch_deadline_seconds)
            event_json = terminal_event_json or (
                RUN_FINISHED_EVENT if outcome == "success"
                else json.dumps({"type": "RUN_ERROR", "code": outcome},
                                separators=(",", ":")))
            if batch_token:
                # RUN_FINISHED carries the batch_token — a crash can
                # never publish it without a claimable batch (B1-D).
                payload = json.loads(event_json)
                payload["batch_token"] = batch_token
                event_json = json.dumps(payload, separators=(",", ":"))
            # The journal decides the effective outcome (it may already
            # be terminal with a DIFFERENT outcome, e.g. quota).
            effective = self._terminalize_run_in_tx(
                connection, context_id, run_id, outcome, event_json, current)
            connection.execute(
                "UPDATE agui_admissions SET state='terminal', outcome=?, "
                "lease_owner='', lease_heartbeat_at=0, updated_at=? "
                "WHERE context_id=? AND run_id=?",
                (effective, current, context_id, run_id),
            )
            # Leaving the lease invalidates the fence token: a zombie
            # holding the old token fails every later check.
            connection.execute(
                "UPDATE agui_fences SET token=token+1 WHERE context_id=?",
                (context_id,))
            if effective != "success":
                connection.execute(
                    "UPDATE agui_calls SET state='abandoned', updated_at=? "
                    "WHERE context_id=? AND run_id=? AND state='emitted'",
                    (current, context_id, run_id),
                )

    def orphan_expired_agui_turns(
        self, *, heartbeat_timeout_seconds: float = 120.0,
        now: Optional[float] = None,
    ) -> int:
        """Move stale-heartbeat running turns to ``orphaned``.

        In the SAME transaction: the admission becomes ``orphaned``, the
        journal gets a terminal ``RUN_ERROR {run_lost}`` event, the run
        row turns terminal, and pending calls are abandoned — the run is
        NEVER silently relaunched.
        """
        current = now if now is not None else time.time()
        cutoff = current - max(1.0, float(heartbeat_timeout_seconds))
        orphaned = 0
        with self._lock, self._immediate() as connection:
            rows = connection.execute(
                "SELECT context_id, run_id FROM agui_admissions WHERE "
                "state='running' AND lease_heartbeat_at<=?",
                (cutoff,),
            ).fetchall()
            for row in rows:
                context_id, run_id = row["context_id"], row["run_id"]
                run = connection.execute(
                    "SELECT state, outcome FROM agui_runs "
                    "WHERE context_id=? AND run_id=?",
                    (context_id, run_id)).fetchone()
                if run is not None and run["state"] == "terminal":
                    # Reconciliation: the journal already recorded how the
                    # run ended (e.g. success just before the worker died).
                    # The admission adopts THAT outcome — never a fabricated
                    # run_lost over a completed journal.
                    connection.execute(
                        "UPDATE agui_admissions SET state='terminal', "
                        "outcome=?, lease_owner='', lease_heartbeat_at=0, "
                        "updated_at=? WHERE context_id=? AND run_id=? AND "
                        "state='running'",
                        (run["outcome"], current, context_id, run_id),
                    )
                    reconciled_outcome = str(run["outcome"])
                else:
                    connection.execute(
                        "UPDATE agui_admissions SET state='orphaned', "
                        "outcome='run_lost', lease_owner='', "
                        "lease_heartbeat_at=0, updated_at=? "
                        "WHERE context_id=? AND run_id=? AND state='running'",
                        (current, context_id, run_id),
                    )
                    self._terminalize_run_in_tx(connection, context_id,
                                                run_id, "run_lost",
                                                RUN_LOST_EVENT, current)
                    reconciled_outcome = "run_lost"
                # Lease exit invalidates the fence in every case.
                connection.execute(
                    "UPDATE agui_fences SET token=token+1 WHERE context_id=?",
                    (context_id,))
                if reconciled_outcome != "success":
                    connection.execute(
                        "UPDATE agui_calls SET state='abandoned', updated_at=? "
                        "WHERE context_id=? AND run_id=? AND state='emitted'",
                        (current, context_id, run_id),
                    )
                orphaned += 1
        return orphaned

    # ── frontend-call ledger ─────────────────────────────────────────

    def record_agui_call(self, context_id: str, run_id: str,
                         tool_call_id: str, tool: str = "", *,
                         catalogue_id: str = "",
                         catalogue_version: str = "") -> None:
        """Record an emitted frontend call — allowed ONLY while the run
        is ``running`` (calls are emitted by the executing worker; a call
        before dispatch/start or after terminal must never create an
        ``emitted`` row — P1-E adds the worker/fence control on top).
        ``catalogue_id`` / ``catalogue_version`` pin the call's catalogue
        identity for the pre-begin check (B1-X 4); on a MANAGED
        publication the FULL identity is required — a managed call
        without it could never be begin-verified and would bypass the
        catalogue gate."""
        if not tool_call_id:
            raise ValueError("tool_call_id is required")
        now = time.time()
        with self._lock, self._immediate() as connection:
            admission = connection.execute(
                "SELECT state, outcome FROM agui_admissions "
                "WHERE context_id=? AND run_id=?",
                (context_id, run_id)).fetchone()
            if admission is None or admission["state"] != "running":
                raise AguiRunTerminal(
                    str(admission["outcome"] or admission["state"])
                    if admission else "missing")
            publication = connection.execute(
                "SELECT p.managed_mode FROM a2a_contexts c "
                "JOIN a2a_publications p "
                "ON p.publication_id=c.publication_id "
                "WHERE c.context_id=?", (context_id,)).fetchone()
            if publication is not None and publication["managed_mode"] \
                    and (not catalogue_id or not catalogue_version):
                raise ValueError(
                    "managed publications require the call's full "
                    "catalogue identity (catalogue_id and "
                    "catalogue_version)")
            connection.execute(
                "INSERT OR IGNORE INTO agui_calls (context_id, run_id, "
                "tool_call_id, tool, catalogue_id, catalogue_version, "
                "state, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'emitted', ?, ?)",
                (context_id, run_id, tool_call_id, tool, catalogue_id,
                 catalogue_version, now, now),
            )

    def pending_agui_calls(self, context_id: str, run_id: str) -> List[str]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT tool_call_id FROM agui_calls WHERE context_id=? AND "
                "run_id=? AND state='emitted' ORDER BY created_at",
                (context_id, run_id)).fetchall()
        return [row["tool_call_id"] for row in rows]

    def consume_agui_calls(self, context_id: str, run_id: str,
                           tool_call_ids: List[str]) -> None:
        """Exactly-once consumption of the EXACT pending set.

        The provided ids must equal the run's pending (emitted) calls —
        all of them, only them; abandoned or consumed calls are not part
        of the expected set. Mismatch names the missing/extra ids.
        """
        provided = set(tool_call_ids)
        now = time.time()
        with self._lock, self._immediate() as connection:
            rows = connection.execute(
                "SELECT tool_call_id FROM agui_calls WHERE context_id=? AND "
                "run_id=? AND state='emitted'",
                (context_id, run_id)).fetchall()
            pending = {row["tool_call_id"] for row in rows}
            if provided != pending:
                missing = sorted(pending - provided)
                extra = sorted(provided - pending)
                raise AguiParentMismatch(
                    f"result batch mismatch: missing={missing} extra={extra}")
            connection.execute(
                "UPDATE agui_calls SET state='consumed', updated_at=? "
                "WHERE context_id=? AND run_id=? AND state='emitted'",
                (now, context_id, run_id),
            )

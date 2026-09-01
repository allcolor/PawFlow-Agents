"""Run-scoped attach/cancel credentials for the managed AG-UI protocol
(plan ``docs/WEBMCP_INTEGRATION_PLAN.md`` §B1-J — step P1-F/5).

Every managed admission mints ONE run handle in ``agui_run_tokens``
(same transaction as the admission), pinned to the current keyring
version. Two credentials derive from it with the v8.2 scheme —
byte-identical on every re-derivation, self-addressing via the handle,
never containing the ``runId``:

- ``attach_token`` (usage ``attach``): lets a client re-attach to the
  run's SSE without replaying the full body — it can NEVER admit; the
  tail replays gaplessly from the caller's watermark. Issued in the
  journaled ``RUN_STARTED``.
- ``cancel_token`` (usage ``cancel``): explicit cancellation over
  ``DELETE`` + header. Idempotent and journaled: the first cancel
  terminalizes the admission AND the journal (``RUN_ERROR cancelled``)
  in one transaction, cuts the lease (the pilot's next lease check
  refuses every further effect), bumps the fence and abandons pending
  calls; replays return the recorded terminal without mutation.

Failure discipline matches the batch tokens: unknown handle, bad MAC,
cross-usage replay and cross-publication presentation all answer the
UNIFORM :class:`AguiTokenInvalid` — a probe learns nothing.
"""

from __future__ import annotations

import secrets
import time
from typing import Any, Callable, Dict, Optional

from core._a2a_turn_batch import AguiTokenInvalid

CANCELLED_EVENT = ('{"type": "RUN_ERROR", "message": "cancelled", '
                   '"code": "cancelled"}')

_CANCELLABLE_STATES = ("reserved", "dispatching", "accepted", "running")


class TurnAttachMixin:
    """Run-handle credentials: attach (tail) and cancel (terminalize)."""

    # Provided by A2AStore / sibling mixins:
    _lock: Any
    _connect: Callable[[], Any]
    _immediate: Callable[[], Any]

    @staticmethod
    def _initialize_attach_tables(connection) -> None:
        """Create ``agui_run_tokens`` and extend the keyring guarantees
        to it (audit fail-closed + version high-water) — runs AFTER the
        batch tables so the keyring exists."""
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS agui_run_tokens (
                handle TEXT PRIMARY KEY,
                context_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                key_version INTEGER NOT NULL,
                created_at REAL NOT NULL,
                UNIQUE (context_id, run_id),
                FOREIGN KEY (context_id, run_id)
                    REFERENCES agui_runs(context_id, run_id)
                    ON DELETE CASCADE
            );
            """
        )
        # Same referential audit as the batches: a run token pinning a
        # missing key version means its credentials silently changed —
        # fail closed at startup.
        missing = connection.execute(
            "SELECT DISTINCT key_version FROM agui_run_tokens WHERE "
            "key_version NOT IN (SELECT version FROM agui_token_keys)"
        ).fetchall()
        if missing:
            versions = sorted(int(row[0]) for row in missing)
            raise RuntimeError(
                f"token key versions {versions} are referenced by run "
                "tokens but missing from the keyring — failing closed")
        connection.execute(
            "UPDATE agui_token_key_meta SET high_water=MAX(high_water, "
            "COALESCE((SELECT MAX(key_version) FROM agui_run_tokens), 0)) "
            "WHERE id=1")

    # ── minting (inside the admission transaction) ───────────────────

    def _ensure_run_tokens_in_tx(self, connection, context_id: str,
                                 run_id: str, now: float) -> None:
        """Mint the run's handle if it does not exist yet (idempotent —
        a replayed admission keeps the same handle, so both tokens stay
        byte-identical). Caller's transaction."""
        row = connection.execute(
            "SELECT handle FROM agui_run_tokens WHERE context_id=? AND "
            "run_id=?", (context_id, run_id)).fetchone()
        if row is not None:
            return
        key_version = self._current_key_version(connection)  # type: ignore[attr-defined]
        connection.execute(
            "INSERT INTO agui_run_tokens (handle, context_id, run_id, "
            "key_version, created_at) VALUES (?, ?, ?, ?, ?)",
            ("rh" + secrets.token_hex(12), context_id, run_id,
             key_version, now))

    # ── derivation / resolution ──────────────────────────────────────

    def agui_run_tokens_for(self, context_id: str, run_id: str
                            ) -> Dict[str, str]:
        """(Re-)derive the run's ``attach_token`` and ``cancel_token`` —
        byte-identical on every call."""
        with self._lock, self._immediate() as connection:
            row = connection.execute(
                "SELECT handle, key_version FROM agui_run_tokens WHERE "
                "context_id=? AND run_id=?",
                (context_id, run_id)).fetchone()
            if row is None:
                raise AguiTokenInvalid("no run tokens for this run")
            identity = f"{context_id}|{run_id}"
            return {
                "attach_token": self._derive_token(  # type: ignore[attr-defined]
                    connection, key_version=int(row["key_version"]),
                    handle=row["handle"], usage="attach",
                    identity=identity, generation=0),
                "cancel_token": self._derive_token(  # type: ignore[attr-defined]
                    connection, key_version=int(row["key_version"]),
                    handle=row["handle"], usage="cancel",
                    identity=identity, generation=0),
            }

    def _resolve_run_token_in_tx(self, connection, token: str, usage: str,
                                 scope) -> Dict[str, str]:
        """handle → run-token row → scope gate → MAC check. Every failure
        is the uniform :class:`AguiTokenInvalid`."""
        handle = self._parse_token(token)  # type: ignore[attr-defined]
        row = connection.execute(
            "SELECT context_id, run_id, key_version FROM agui_run_tokens "
            "WHERE handle=?", (handle,)).fetchone()
        if row is None:
            raise AguiTokenInvalid("unknown token")
        context_id, run_id = str(row["context_id"]), str(row["run_id"])
        self._verify_scope_in_tx(connection, context_id, scope)  # type: ignore[attr-defined]
        self._verify_against_row(  # type: ignore[attr-defined]
            connection, token, key_version=int(row["key_version"]),
            handle=handle, usage=usage,
            identity=f"{context_id}|{run_id}", generation=0)
        return {"context_id": context_id, "run_id": run_id}

    def resolve_agui_attach(self, attach_token: str, *,
                            scope=None) -> Dict[str, str]:
        """Resolve an ``attach_token`` to its run identity. Attach can
        NEVER admit — the caller only tails the journal."""
        with self._lock, self._immediate() as connection:
            return self._resolve_run_token_in_tx(connection, attach_token,
                                                 "attach", scope)

    # ── cancellation (idempotent, journaled) ─────────────────────────

    def cancel_agui_run(self, cancel_token: str, *, scope=None,
                        now: Optional[float] = None) -> Dict[str, Any]:
        """Explicitly cancel one run — ONE transaction synchronizing the
        admission, the journal terminal, the fence and the pending-call
        abandonment (mirrors ``finish_agui_turn``'s discipline).

        Idempotent: an already-terminal run replays its recorded outcome
        with ``already=True`` and mutates nothing. A journal that
        terminalized first (e.g. success racing the cancel) wins — the
        admission adopts ITS outcome, exactly like the sweep's
        reconciliation."""
        current = now if now is not None else time.time()
        with self._lock, self._immediate() as connection:
            resolved = self._resolve_run_token_in_tx(connection,
                                                     cancel_token, "cancel",
                                                     scope)
            context_id, run_id = resolved["context_id"], resolved["run_id"]
            admission = connection.execute(
                "SELECT state, outcome FROM agui_admissions WHERE "
                "context_id=? AND run_id=?",
                (context_id, run_id)).fetchone()
            if admission is None:
                raise AguiTokenInvalid("unknown token")
            if admission["state"] not in _CANCELLABLE_STATES:
                return {"outcome": str(admission["outcome"]),
                        "already": True,
                        "context_id": context_id, "run_id": run_id}
            # Journal first — it decides the effective outcome (a journal
            # already terminal keeps ITS outcome, reconciliation).
            effective = self._terminalize_run_in_tx(  # type: ignore[attr-defined]
                connection, context_id, run_id, "cancelled",
                CANCELLED_EVENT, current)
            connection.execute(
                "UPDATE agui_admissions SET state='terminal', outcome=?, "
                "lease_owner='', lease_heartbeat_at=0, claim_owner='', "
                "claim_deadline=0, payload_json='', updated_at=? "
                "WHERE context_id=? AND run_id=?",
                (effective, current, context_id, run_id))
            # Leaving the lease invalidates the fence: the pilot's next
            # lease check refuses, and no effect can follow the cancel.
            connection.execute(
                "UPDATE agui_fences SET token=token+1 WHERE context_id=?",
                (context_id,))
            if effective != "success":
                connection.execute(
                    "UPDATE agui_calls SET state='abandoned', updated_at=? "
                    "WHERE context_id=? AND run_id=? AND state='emitted'",
                    (current, context_id, run_id))
            return {"outcome": effective, "already": False,
                    "context_id": context_id, "run_id": run_id}


__all__ = ["TurnAttachMixin", "CANCELLED_EVENT"]

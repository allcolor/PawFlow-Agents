"""Managed frontend-execution batches and the v8.2 token scheme
(plan ``docs/WEBMCP_INTEGRATION_PLAN.md`` §B0 tokens / B1-X / B1-L —
step P1-D).

Contracts implemented here, all in the single SQLite commit domain:

- **Token scheme** ``v<K>.<handle>.<MAC>``: credentials are DERIVED, not
  minted-and-stored. ``handle`` is a non-secret indexed id (row lookup —
  no scanning); ``MAC = HMAC(server_key_K, handle | usage |
  canonical_identity | credential_generation)`` with strict usage
  separation (``batch``, ``owner``, ``receipt``). The key version ``K``
  is PINNED on the batch row at freeze; replays re-derive byte-identical
  tokens across key rotations; the token's own ``vK`` prefix is
  untrusted input — only the pinned version is used. The keyring is
  durable in the same database; a missing pinned key fails closed.
- **T-freeze** happens inside ``finish_agui_turn``'s transaction (see
  ``_a2a_turn_acquire``): a successful run with pending emitted calls
  freezes its batch, issues the batch handle/key version and the
  per-call handles, arms the absolute deadline, and thereby takes the
  **frontend-execution lease** (an open batch blocks rotation and the
  TTL sweep, and refuses new admissions).
- **Idempotent claim**: ``claim_agui_batch`` CAS
  ``frozen → reserved_pre_effect(owner, claim_generation, lease)``.
  The same ``batch_claim_id`` retried returns byte-identical
  ``owner_token`` + receipts; a different one while the reservation
  lives is refused; a pre-effect lease expiry returns the batch to
  ``frozen`` and invalidates the whole claim generation (old
  credentials answer ``claim_expired``); the expired ``batch_claim_id``
  is recorded durably and can never be resurrected. Past the absolute
  deadline neither claim nor begin ever wins.
- **Catalogue identity**: recorded per call at emission; ``begin``
  checks the presented live ``catalogueVersion`` against it and
  terminalizes ``catalogue_unverifiable`` / ``catalogue_changed``
  without executing; deposits need no live catalogue.
- **Two deadlines only**: the claim lease exists in
  ``reserved_pre_effect`` (renew = idempotent CAS, no-op once
  committed); the absolute batch deadline is set at freeze, never
  extended, and terminalizes never-begun calls to ``abandoned`` and
  executing calls to ``indeterminate`` — then T-complete.
- **begin/deposit state matrix**: ``begin`` is an idempotent CAS that
  commits the claim on first use (``reserved_pre_effect →
  execution_committed``); deposits from a not-begun call accept only
  no-effect outcomes, deposits from an executing call only effect
  outcomes; duplicate identical deposits replay the recorded answer,
  different payloads are conflicts; a deposit after T-complete replays
  the terminal outcome and never mutates the consumable batch.
- **T-complete**: the last terminal deposit (or the deadline sweep)
  completes the batch in the same transaction, releasing the frontend
  lease. The follow-up acquire consumes the completed batch via
  ``parentRunId`` (wired in ``_a2a_turn_acquire``).
"""

from __future__ import annotations

import hashlib
import hmac as hmac_module
import secrets
import time
from typing import Any, Callable, Dict, List, Optional

NO_EFFECT_OUTCOMES = frozenset({
    "denied", "ledger_unavailable", "cancelled_before_begin",
    "catalogue_unverifiable", "catalogue_changed",
})
EFFECT_OUTCOMES = frozenset({
    "result", "null_navigation", "error", "indeterminate",
})
_TERMINAL_EXEC_STATES = NO_EFFECT_OUTCOMES | EFFECT_OUTCOMES | {"abandoned"}


class AguiTokenInvalid(Exception):
    """Unknown handle, bad MAC or wrong usage — one uniform error."""


class AguiClaimExpired(Exception):
    """The credential belongs to an invalidated claim generation."""


class AguiBatchClaimed(Exception):
    """Another live claim owns the batch (different batch_claim_id)."""


class AguiBatchIncomplete(Exception):
    """The thread's batch is not complete yet — no new run can be
    admitted until every call is terminal."""

    def __init__(self, run_id: str) -> None:
        super().__init__(f"batch of run {run_id} is not complete")
        self.run_id = run_id


class AguiCatalogueRejected(Exception):
    """The call's recorded catalogue identity cannot be verified live —
    the call is terminalized WITHOUT executing the new registration."""

    def __init__(self, outcome: str) -> None:
        super().__init__(outcome)
        self.outcome = outcome


class AguiDepositRejected(Exception):
    """The outcome kind is not allowed from the call's current state."""


class AguiReceiptConflict(Exception):
    """Same receipt deposited twice with different payloads."""


class TurnBatchMixin:
    """Managed batch state machine + derived-credential helpers."""

    # Provided by A2AStore / sibling mixins:
    _lock: Any
    _connect: Callable[[], Any]
    _immediate: Callable[[], Any]

    # ── schema ───────────────────────────────────────────────────────

    @staticmethod
    def _initialize_batch_tables(connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS agui_token_keys (
                version INTEGER PRIMARY KEY,
                secret TEXT NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS agui_token_key_meta (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                high_water INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS agui_expired_claims (
                handle TEXT NOT NULL,
                batch_claim_id TEXT NOT NULL,
                expired_at REAL NOT NULL,
                PRIMARY KEY (handle, batch_claim_id)
            );

            CREATE TABLE IF NOT EXISTS agui_batches (
                context_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'frozen',
                handle TEXT NOT NULL UNIQUE,
                key_version INTEGER NOT NULL,
                claim_generation INTEGER NOT NULL DEFAULT 0,
                batch_claim_id TEXT NOT NULL DEFAULT '',
                claim_lease_deadline REAL NOT NULL DEFAULT 0,
                absolute_deadline REAL NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (context_id, run_id),
                FOREIGN KEY (context_id, run_id)
                    REFERENCES agui_runs(context_id, run_id)
                    ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_agui_batches_state
                ON agui_batches(state, absolute_deadline);
            """
        )
        columns = {
            row[1] for row in connection.execute(
                "PRAGMA table_info(agui_calls)").fetchall()}
        for name, ddl in (("handle", "TEXT NOT NULL DEFAULT ''"),
                          ("exec_state", "TEXT NOT NULL DEFAULT ''"),
                          ("result_kind", "TEXT NOT NULL DEFAULT ''"),
                          ("result_json", "TEXT NOT NULL DEFAULT ''"),
                          ("catalogue_id", "TEXT NOT NULL DEFAULT ''"),
                          ("catalogue_version", "TEXT NOT NULL DEFAULT ''")):
            if name not in columns:
                connection.execute(
                    f"ALTER TABLE agui_calls ADD COLUMN {name} {ddl}")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_agui_calls_handle "
            "ON agui_calls(handle)")
        publication_columns = {
            row[1] for row in connection.execute(
                "PRAGMA table_info(a2a_publications)").fetchall()}
        if "managed_mode" not in publication_columns:
            connection.execute(
                "ALTER TABLE a2a_publications "
                "ADD COLUMN managed_mode INTEGER NOT NULL DEFAULT 0")
        # Referential keyring audit — FAIL CLOSED at startup: a batch
        # pinning a key version that no longer exists means every one of
        # its derived credentials silently changed; refuse to start.
        missing = connection.execute(
            "SELECT DISTINCT key_version FROM agui_batches WHERE "
            "key_version NOT IN (SELECT version FROM agui_token_keys)"
        ).fetchall()
        if missing:
            versions = sorted(int(row[0]) for row in missing)
            raise RuntimeError(
                f"token key versions {versions} are referenced by batches "
                "but missing from the keyring — failing closed")
        # Version high-water: durable and NEVER reusable, even after the
        # keyring is emptied (a recreated version would re-derive old
        # tokens with different bytes instead of failing closed).
        connection.execute(
            "INSERT OR IGNORE INTO agui_token_key_meta (id, high_water) "
            "VALUES (1, 0)")
        connection.execute(
            "UPDATE agui_token_key_meta SET high_water=MAX(high_water, "
            "COALESCE((SELECT MAX(version) FROM agui_token_keys), 0), "
            "COALESCE((SELECT MAX(key_version) FROM agui_batches), 0)) "
            "WHERE id=1")

    # ── keyring / token derivation ───────────────────────────────────

    def _current_key_version(self, connection) -> int:
        row = connection.execute(
            "SELECT version FROM agui_token_keys ORDER BY version DESC "
            "LIMIT 1").fetchone()
        if row is not None:
            return int(row["version"])
        # Empty keyring: mint a key at high_water+1 — a version number is
        # NEVER reused (a reused version with a fresh secret would make
        # old pinned tokens re-derive to different bytes silently).
        high = connection.execute(
            "SELECT MAX(COALESCE((SELECT high_water FROM "
            "agui_token_key_meta WHERE id=1), 0), "
            "COALESCE((SELECT MAX(key_version) FROM agui_batches), 0), "
            "COALESCE((SELECT MAX(key_version) FROM agui_run_tokens), 0))"
        ).fetchone()[0]
        version = int(high) + 1
        connection.execute(
            "INSERT INTO agui_token_keys (version, secret, created_at) "
            "VALUES (?, ?, ?)", (version, secrets.token_hex(32), time.time()))
        connection.execute(
            "INSERT INTO agui_token_key_meta (id, high_water) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "high_water=MAX(high_water, excluded.high_water)", (version,))
        return version

    def prune_agui_token_keys(self) -> int:
        """The ONLY sanctioned key-deletion path: drop keys that are
        neither current nor referenced by any batch. A referenced key is
        never deletable — its batches pin it for byte-identical
        re-derivation."""
        with self._lock, self._immediate() as connection:
            current = connection.execute(
                "SELECT MAX(version) FROM agui_token_keys").fetchone()[0]
            if current is None:
                return 0
            cursor = connection.execute(
                "DELETE FROM agui_token_keys WHERE version!=? AND version "
                "NOT IN (SELECT DISTINCT key_version FROM agui_batches) "
                "AND version NOT IN (SELECT DISTINCT key_version FROM "
                "agui_run_tokens)",
                (int(current),))
            return int(cursor.rowcount)

    def _key_secret(self, connection, version: int) -> str:
        row = connection.execute(
            "SELECT secret FROM agui_token_keys WHERE version=?",
            (int(version),)).fetchone()
        if row is None:
            raise RuntimeError(
                f"token key version {version} is referenced but missing — "
                "failing closed")
        return str(row["secret"])

    @staticmethod
    def _mac(secret: str, handle: str, usage: str, identity: str,
             generation: int) -> str:
        message = f"{handle}|{usage}|{identity}|{int(generation)}"
        return hmac_module.new(secret.encode("utf-8"),
                               message.encode("utf-8"),
                               hashlib.sha256).hexdigest()[:32]

    def _derive_token(self, connection, *, key_version: int, handle: str,
                      usage: str, identity: str, generation: int) -> str:
        secret = self._key_secret(connection, key_version)
        mac = self._mac(secret, handle, usage, identity, generation)
        return f"v{int(key_version)}.{handle}.{mac}"

    @staticmethod
    def _parse_token(token: str) -> str:
        """Return the handle; everything else is verified against the row
        (the ``vK`` prefix is untrusted input)."""
        parts = str(token or "").split(".")
        if len(parts) != 3 or not parts[1]:
            raise AguiTokenInvalid("malformed token")
        return parts[1]

    def _verify_against_row(self, connection, token: str, *,
                            key_version: int, handle: str, usage: str,
                            identity: str, generation: int) -> None:
        expected = self._derive_token(
            connection, key_version=key_version, handle=handle, usage=usage,
            identity=identity, generation=generation)
        if not hmac_module.compare_digest(expected, str(token)):
            raise AguiTokenInvalid("token verification failed")

    def _verify_generation_bound(self, connection, token: str, *,
                                 key_version: int, handle: str, usage: str,
                                 identity: str, generation: int) -> None:
        """Uniform failure discipline for generation-bound credentials:
        only a token that WAS valid for an older claim generation is
        :class:`AguiClaimExpired`; anything else — tampered MAC,
        cross-usage replay, doctored prefix — is the uniform
        :class:`AguiTokenInvalid`."""
        expected = self._derive_token(
            connection, key_version=key_version, handle=handle, usage=usage,
            identity=identity, generation=generation)
        if hmac_module.compare_digest(expected, str(token)):
            return
        for stale_generation in range(int(generation) - 1, -1, -1):
            stale = self._derive_token(
                connection, key_version=key_version, handle=handle,
                usage=usage, identity=identity, generation=stale_generation)
            if hmac_module.compare_digest(stale, str(token)):
                raise AguiClaimExpired(
                    f"{usage} token generation is stale")
        raise AguiTokenInvalid("token verification failed")

    # ── freeze (called from finish_agui_turn's transaction) ──────────

    def _freeze_batch_in_tx(self, connection, context_id: str, run_id: str,
                            now: float, *,
                            batch_deadline_seconds: float) -> str:
        """Create the frozen batch + per-call handles (T-freeze part).
        Returns the derived ``batch_token`` so the caller can journalize
        it inside the SAME transaction (``RUN_FINISHED`` carries it)."""
        key_version = self._current_key_version(connection)
        handle = "bh" + secrets.token_hex(12)
        connection.execute(
            "INSERT INTO agui_batches (context_id, run_id, state, handle, "
            "key_version, claim_generation, batch_claim_id, "
            "claim_lease_deadline, absolute_deadline, created_at, "
            "updated_at) VALUES (?, ?, 'frozen', ?, ?, 0, '', 0, ?, ?, ?)",
            (context_id, run_id, handle, key_version,
             now + max(1.0, float(batch_deadline_seconds)), now, now),
        )
        rows = connection.execute(
            "SELECT tool_call_id FROM agui_calls WHERE context_id=? AND "
            "run_id=? AND state='emitted'", (context_id, run_id)).fetchall()
        for row in rows:
            connection.execute(
                "UPDATE agui_calls SET handle=?, updated_at=? "
                "WHERE context_id=? AND run_id=? AND tool_call_id=?",
                ("ch" + secrets.token_hex(12), now, context_id, run_id,
                 row["tool_call_id"]),
            )
        return self._derive_token(
            connection, key_version=key_version, handle=handle,
            usage="batch", identity=f"{context_id}|{run_id}", generation=0)

    def get_agui_batch(self, context_id: str, run_id: str
                       ) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agui_batches WHERE context_id=? AND run_id=?",
                (context_id, run_id)).fetchone()
        return dict(row) if row else None

    def batch_token_for(self, context_id: str, run_id: str) -> str:
        """(Re-)derive the batch token — byte-identical on every call."""
        with self._lock, self._immediate() as connection:
            row = connection.execute(
                "SELECT handle, key_version FROM agui_batches "
                "WHERE context_id=? AND run_id=?",
                (context_id, run_id)).fetchone()
            if row is None:
                raise AguiTokenInvalid("no batch for this run")
            return self._derive_token(
                connection, key_version=int(row["key_version"]),
                handle=row["handle"], usage="batch",
                identity=f"{context_id}|{run_id}", generation=0)

    def _verify_scope_in_tx(self, connection, context_id: str,
                            scope) -> None:
        """Fail closed unless ``context_id`` belongs to the authenticated
        (publication_id, key_id) ``scope`` (B0 canonical identity). A
        credential presented on ANOTHER publication resolves its handle
        but its context is not in that publication+key, so it answers the
        UNIFORM :class:`AguiTokenInvalid` — never leaking that the batch
        exists. ``scope=None`` skips the check (internal/store-direct
        callers); the HTTP endpoint always passes it."""
        if scope is None:
            return
        publication_id, key_id = scope
        row = connection.execute(
            "SELECT 1 FROM a2a_contexts WHERE context_id=? AND "
            "publication_id=? AND key_id=?",
            (context_id, publication_id, key_id)).fetchone()
        if row is None:
            raise AguiTokenInvalid("token does not belong to this scope")

    def _batch_token_in_tx(self, connection, context_id: str,
                           run_id: str) -> str:
        """Derived ``batch_token`` while the run's batch is still open —
        empty string otherwise (used by the terminal snapshot, B1-G)."""
        row = connection.execute(
            "SELECT handle, key_version FROM agui_batches WHERE "
            "context_id=? AND run_id=? AND state IN ('frozen',"
            "'reserved_pre_effect','execution_committed')",
            (context_id, run_id)).fetchone()
        if row is None:
            return ""
        return self._derive_token(
            connection, key_version=int(row["key_version"]),
            handle=row["handle"], usage="batch",
            identity=f"{context_id}|{run_id}", generation=0)

    def has_frontend_execution_lease(self, context_id: str) -> bool:
        """True while an open (non-complete) batch pins the thread."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM agui_batches WHERE context_id=? AND "
                "state IN ('frozen','reserved_pre_effect',"
                "'execution_committed') LIMIT 1", (context_id,)).fetchone()
        return row is not None

    # ── claim / renew ────────────────────────────────────────────────

    def _receipts_for(self, connection, batch: Dict[str, Any]) -> List[Dict[str, str]]:
        rows = connection.execute(
            "SELECT tool_call_id, handle FROM agui_calls WHERE context_id=? "
            "AND run_id=? AND state='emitted' ORDER BY created_at",
            (batch["context_id"], batch["run_id"])).fetchall()
        receipts = []
        for row in rows:
            receipts.append({
                "tool_call_id": row["tool_call_id"],
                "receipt": self._derive_token(
                    connection, key_version=int(batch["key_version"]),
                    handle=row["handle"], usage="receipt",
                    identity=(f"{batch['context_id']}|{batch['run_id']}|"
                              f"{row['tool_call_id']}"),
                    generation=int(batch["claim_generation"])),
            })
        return receipts

    def _owner_token_for(self, connection, batch: Dict[str, Any]) -> str:
        return self._derive_token(
            connection, key_version=int(batch["key_version"]),
            handle=batch["handle"], usage="owner",
            identity=f"{batch['context_id']}|{batch['run_id']}",
            generation=int(batch["claim_generation"]))

    @staticmethod
    def _expire_claim_in_tx(connection, batch: Dict[str, Any],
                            now: float) -> None:
        """Pre-effect lease expiry: back to ``frozen``, the expired
        ``batch_claim_id`` recorded DURABLY as terminal, and the claim
        generation BUMPED — the expiry itself revokes every credential
        of the expired claim (owner token AND receipts answer
        ``claim_expired`` from now on, deposits included — B1-X 2)."""
        connection.execute(
            "INSERT OR IGNORE INTO agui_expired_claims (handle, "
            "batch_claim_id, expired_at) VALUES (?, ?, ?)",
            (batch["handle"], batch["batch_claim_id"], now))
        connection.execute(
            "UPDATE agui_batches SET state='frozen', batch_claim_id='', "
            "claim_lease_deadline=0, claim_generation=claim_generation+1, "
            "updated_at=? WHERE handle=? AND "
            "state='reserved_pre_effect'", (now, batch["handle"]))
        batch["claim_generation"] = int(batch["claim_generation"]) + 1

    def claim_agui_batch(self, batch_token: str, batch_claim_id: str, *,
                         lease_seconds: float = 60.0, scope=None,
                         now: Optional[float] = None) -> Dict[str, Any]:
        """Idempotently claim the WHOLE batch (one execution owner)."""
        if not batch_claim_id:
            raise ValueError("batch_claim_id is required")
        current = now if now is not None else time.time()
        handle = self._parse_token(batch_token)
        with self._lock, self._immediate() as connection:
            row = connection.execute(
                "SELECT * FROM agui_batches WHERE handle=?",
                (handle,)).fetchone()
            if row is None:
                raise AguiTokenInvalid("unknown batch token")
            batch = dict(row)
            self._verify_scope_in_tx(connection, batch["context_id"], scope)
            identity = f"{batch['context_id']}|{batch['run_id']}"
            self._verify_against_row(
                connection, batch_token, key_version=int(batch["key_version"]),
                handle=handle, usage="batch", identity=identity, generation=0)
            state = batch["state"]
            if state in ("frozen", "reserved_pre_effect",
                         "execution_committed") \
                    and float(batch["absolute_deadline"]) <= current:
                # Claim vs absolute deadline: single winner — the claim
                # NEVER opens past the deadline; the sweep terminalizes
                # the batch (plan v8.2 delta).
                raise AguiClaimExpired("absolute batch deadline has passed")
            if state == "reserved_pre_effect" \
                    and batch["claim_lease_deadline"] <= current:
                # Pre-effect lease expiry: back to frozen, expired claim
                # id durably terminal, generation invalidated below.
                self._expire_claim_in_tx(connection, batch, current)
                state = "frozen"
            # An expired claim id is refused AFTER the commit, so an
            # expiry observed just above is materialized durably even
            # though the claim is rejected.
            expired_id = connection.execute(
                "SELECT 1 FROM agui_expired_claims WHERE handle=? AND "
                "batch_claim_id=?", (handle, batch_claim_id)).fetchone() \
                is not None
            if not expired_id:
                if state == "frozen":
                    generation = int(batch["claim_generation"]) + 1
                    connection.execute(
                        "UPDATE agui_batches SET "
                        "state='reserved_pre_effect', claim_generation=?, "
                        "batch_claim_id=?, claim_lease_deadline=?, "
                        "updated_at=? WHERE handle=?",
                        (generation, batch_claim_id,
                         current + max(1.0, float(lease_seconds)), current,
                         handle),
                    )
                    batch["claim_generation"] = generation
                    batch["state"] = "reserved_pre_effect"
                elif state in ("reserved_pre_effect", "execution_committed"):
                    if batch["batch_claim_id"] != batch_claim_id:
                        raise AguiBatchClaimed(
                            "batch already claimed by another owner")
                    # Same claim id: idempotent replay of the SAME
                    # credentials.
                else:
                    raise AguiClaimExpired(
                        f"batch is {state}; it can no longer be claimed")
                result = {
                    "context_id": batch["context_id"],
                    "run_id": batch["run_id"],
                    "state": batch["state"],
                    "claim_generation": int(batch["claim_generation"]),
                    "owner_token": self._owner_token_for(connection, batch),
                    "receipts": self._receipts_for(connection, batch),
                }
        if expired_id:
            raise AguiClaimExpired(
                "batch_claim_id belongs to an expired claim — "
                "a re-claim must use a fresh id")
        return result

    def renew_agui_batch(self, owner_token: str, *,
                         lease_seconds: float = 60.0, scope=None,
                         now: Optional[float] = None) -> bool:
        """CAS-extend the pre-effect lease; idempotent no-op once the
        claim is committed. Bounded by the absolute deadline."""
        current = now if now is not None else time.time()
        handle = self._parse_token(owner_token)
        lease_expired = False
        with self._lock, self._immediate() as connection:
            row = connection.execute(
                "SELECT * FROM agui_batches WHERE handle=?",
                (handle,)).fetchone()
            if row is None:
                raise AguiTokenInvalid("unknown owner token")
            batch = dict(row)
            self._verify_scope_in_tx(connection, batch["context_id"], scope)
            identity = f"{batch['context_id']}|{batch['run_id']}"
            self._verify_generation_bound(
                connection, owner_token,
                key_version=int(batch["key_version"]), handle=handle,
                usage="owner", identity=identity,
                generation=int(batch["claim_generation"]))
            if batch["state"] == "execution_committed":
                return True  # lease ignored after the first begin
            if batch["state"] != "reserved_pre_effect":
                raise AguiClaimExpired(f"batch is {batch['state']}")
            if float(batch["absolute_deadline"]) <= current:
                raise AguiClaimExpired("absolute batch deadline has passed")
            if batch["claim_lease_deadline"] <= current:
                # The OBSERVED expiry is materialized durably (back to
                # frozen, claim id terminal) and COMMITTED before the
                # refusal is raised — the expiry itself invalidates the
                # generation, not some later claim.
                self._expire_claim_in_tx(connection, batch, current)
                lease_expired = True
            else:
                new_deadline = min(
                    current + max(1.0, float(lease_seconds)),
                    float(batch["absolute_deadline"]))
                connection.execute(
                    "UPDATE agui_batches SET claim_lease_deadline=?, "
                    "updated_at=? WHERE handle=? AND "
                    "state='reserved_pre_effect'",
                    (new_deadline, current, handle))
        if lease_expired:
            raise AguiClaimExpired(
                "claim lease already expired — it cannot be renewed")
        return True

    # ── begin / deposit ──────────────────────────────────────────────

    def _call_row_by_receipt(self, connection, receipt: str,
                             scope=None) -> Dict[str, Any]:
        handle = self._parse_token(receipt)
        row = connection.execute(
            "SELECT * FROM agui_calls WHERE handle=?", (handle,)).fetchone()
        if row is None:
            raise AguiTokenInvalid("unknown receipt")
        call = dict(row)
        self._verify_scope_in_tx(connection, call["context_id"], scope)
        batch_row = connection.execute(
            "SELECT * FROM agui_batches WHERE context_id=? AND run_id=?",
            (call["context_id"], call["run_id"])).fetchone()
        if batch_row is None:
            raise AguiTokenInvalid("receipt has no batch")
        batch = dict(batch_row)
        identity = (f"{call['context_id']}|{call['run_id']}|"
                    f"{call['tool_call_id']}")
        self._verify_generation_bound(
            connection, receipt, key_version=int(batch["key_version"]),
            handle=handle, usage="receipt", identity=identity,
            generation=int(batch["claim_generation"]))
        call["_batch"] = batch
        return call

    def begin_agui_call(self, receipt: str, *,
                        catalogue_id: Optional[str] = None,
                        catalogue_version: Optional[str] = None,
                        scope=None,
                        now: Optional[float] = None) -> bool:
        """Idempotent CAS to the effect boundary; the FIRST successful
        begin commits the claim (``reserved_pre_effect →
        execution_committed``).

        When the call recorded a catalogue identity at emission, the
        FULL live identity — ``catalogue_id`` (host stable id or generic
        snapshot reference) AND ``catalogue_version`` — must be presented
        and match exactly; otherwise the call terminalizes as
        ``catalogue_unverifiable`` / ``catalogue_changed`` WITHOUT
        executing
        (:class:`AguiCatalogueRejected`). Past the absolute deadline a
        begin never wins — ``executeTool`` must not run."""
        current = now if now is not None else time.time()
        rejection = ""
        lease_expired = False
        with self._lock, self._immediate() as connection:
            call = self._call_row_by_receipt(connection, receipt, scope)
            batch = call["_batch"]
            if batch["state"] in ("complete", "consumed"):
                raise AguiClaimExpired(f"batch is {batch['state']}")
            if float(batch["absolute_deadline"]) <= current:
                # Begin vs absolute deadline: single winner (v8.2) — a
                # begin past the budget is discarded, never executed.
                raise AguiClaimExpired("absolute batch deadline has passed")
            if batch["state"] == "reserved_pre_effect":
                if batch["claim_lease_deadline"] <= current:
                    # Materialize the observed expiry durably, COMMIT,
                    # then refuse (the expiry itself invalidates).
                    self._expire_claim_in_tx(connection, batch, current)
                    lease_expired = True
            elif batch["state"] != "execution_committed":
                raise AguiClaimExpired(f"batch is {batch['state']}")
            if not lease_expired:
                if call["exec_state"] == "executing":
                    return True  # idempotent
                if call["exec_state"]:
                    raise AguiDepositRejected(
                        f"call already terminal ({call['exec_state']})")
                if call["catalogue_id"]:
                    # Catalogue identity gate — BEFORE committing the
                    # claim or crossing the effect boundary (B1-X 4).
                    # BOTH halves of the identity must be presented and
                    # match: a different stable id with the same version
                    # is a changed registration, never executable. An
                    # absent OR empty half is not comparable — the
                    # identity is unverifiable, not changed.
                    if not catalogue_id or not catalogue_version:
                        rejection = "catalogue_unverifiable"
                    elif (str(catalogue_id) != call["catalogue_id"]
                          or str(catalogue_version)
                          != call["catalogue_version"]):
                        rejection = "catalogue_changed"
                    if rejection:
                        connection.execute(
                            "UPDATE agui_calls SET exec_state=?, "
                            "result_kind=?, updated_at=? WHERE "
                            "context_id=? AND run_id=? AND "
                            "tool_call_id=? AND exec_state=''",
                            (rejection, rejection, current,
                             call["context_id"], call["run_id"],
                             call["tool_call_id"]))
                        self._complete_batch_if_done_in_tx(
                            connection, call["context_id"],
                            call["run_id"], current)
            if not rejection and not lease_expired:
                if batch["state"] == "reserved_pre_effect":
                    connection.execute(
                        "UPDATE agui_batches SET "
                        "state='execution_committed', updated_at=? WHERE "
                        "context_id=? AND run_id=? AND "
                        "state='reserved_pre_effect'",
                        (current, call["context_id"], call["run_id"]))
                connection.execute(
                    "UPDATE agui_calls SET exec_state='executing', "
                    "updated_at=? WHERE context_id=? AND run_id=? AND "
                    "tool_call_id=? AND exec_state=''",
                    (current, call["context_id"], call["run_id"],
                     call["tool_call_id"]))
        if lease_expired:
            raise AguiClaimExpired("claim lease expired")
        if rejection:
            # Raised AFTER the commit: the terminalization is durable.
            raise AguiCatalogueRejected(rejection)
        return True

    def deposit_agui_call(self, receipt: str, kind: str,
                          payload_json: str = "", *, scope=None,
                          now: Optional[float] = None) -> Dict[str, Any]:
        """Deposit one call outcome under the closed state matrix.

        Returns ``{kind, payload_json, batch_state}``. Duplicate
        identical deposits replay the recorded answer; different
        payloads raise :class:`AguiReceiptConflict`; a deposit after
        T-complete replays the terminal outcome without mutating the
        batch.
        """
        current = now if now is not None else time.time()
        kind = str(kind)
        lease_expired = False
        with self._lock, self._immediate() as connection:
            call = self._call_row_by_receipt(connection, receipt, scope)
            batch = call["_batch"]
            if call["exec_state"] in _TERMINAL_EXEC_STATES:
                if call["result_kind"] == kind \
                        and call["result_json"] == payload_json:
                    return {"kind": kind, "payload_json": payload_json,
                            "batch_state": batch["state"], "replay": True}
                if batch["state"] in ("complete", "consumed"):
                    # Never mutate a consumable batch: replay the
                    # recorded terminal outcome (audit only).
                    return {"kind": call["result_kind"] or call["exec_state"],
                            "payload_json": call["result_json"],
                            "batch_state": batch["state"], "replay": True}
                raise AguiReceiptConflict(
                    "receipt already deposited with a different payload")
            if batch["state"] in ("complete", "consumed"):
                return {"kind": call["result_kind"] or call["exec_state"],
                        "payload_json": call["result_json"],
                        "batch_state": batch["state"], "replay": True}
            if call["exec_state"] == "executing":
                if kind not in EFFECT_OUTCOMES:
                    raise AguiDepositRejected(
                        f"'{kind}' is not a valid outcome after begin")
            else:  # not begun
                if kind not in NO_EFFECT_OUTCOMES:
                    raise AguiDepositRejected(
                        f"'{kind}' claims an effect but the call never "
                        "passed begin")
                if batch["state"] == "reserved_pre_effect" \
                        and batch["claim_lease_deadline"] <= current:
                    # Materialize the observed expiry durably, COMMIT,
                    # then refuse.
                    self._expire_claim_in_tx(connection, batch, current)
                    lease_expired = True
            if not lease_expired:
                connection.execute(
                    "UPDATE agui_calls SET exec_state=?, result_kind=?, "
                    "result_json=?, updated_at=? WHERE context_id=? AND "
                    "run_id=? AND tool_call_id=?",
                    (kind, kind, payload_json, current, call["context_id"],
                     call["run_id"], call["tool_call_id"]))
                batch_state = self._complete_batch_if_done_in_tx(
                    connection, call["context_id"], call["run_id"], current)
        if lease_expired:
            raise AguiClaimExpired("claim lease expired")
        return {"kind": kind, "payload_json": payload_json,
                "batch_state": batch_state, "replay": False}

    def _complete_batch_if_done_in_tx(self, connection, context_id: str,
                                      run_id: str, now: float) -> str:
        """T-complete (same transaction as the final deposit/sweep)."""
        remaining = connection.execute(
            "SELECT COUNT(*) FROM agui_calls WHERE context_id=? AND "
            "run_id=? AND state='emitted' AND (exec_state='' OR "
            "exec_state='executing')", (context_id, run_id)).fetchone()[0]
        if remaining:
            # Report the REAL batch state (a no-effect deposit does not
            # commit the claim — the batch may still be pre-effect).
            row = connection.execute(
                "SELECT state FROM agui_batches WHERE context_id=? AND "
                "run_id=?", (context_id, run_id)).fetchone()
            return str(row["state"]) if row else ""
        connection.execute(
            "UPDATE agui_batches SET state='complete', "
            "claim_lease_deadline=0, updated_at=? WHERE context_id=? AND "
            "run_id=? AND state!='complete'", (now, context_id, run_id))
        return "complete"

    # ── deadline sweep ───────────────────────────────────────────────

    def _expire_batch_in_tx(self, connection, context_id: str, run_id: str,
                            now: float) -> None:
        """Absolute-deadline terminalization of ONE batch: never-begun
        calls → ``abandoned``, executing calls → ``indeterminate``, then
        T-complete — inside the caller's transaction."""
        connection.execute(
            "UPDATE agui_calls SET exec_state='abandoned', "
            "result_kind='abandoned', updated_at=? WHERE "
            "context_id=? AND run_id=? AND state='emitted' AND "
            "exec_state=''", (now, context_id, run_id))
        connection.execute(
            "UPDATE agui_calls SET exec_state='indeterminate', "
            "result_kind='indeterminate', updated_at=? WHERE "
            "context_id=? AND run_id=? AND state='emitted' AND "
            "exec_state='executing'", (now, context_id, run_id))
        self._complete_batch_if_done_in_tx(connection, context_id, run_id,
                                           now)

    def expire_agui_batches(self, *, now: Optional[float] = None) -> int:
        """Absolute-deadline sweep — ONE transaction PER batch: a failure
        on batch N never rolls back batches 1..N-1. Returns batches
        completed."""
        current = now if now is not None else time.time()
        expired = 0
        while True:
            with self._lock, self._immediate() as connection:
                row = connection.execute(
                    "SELECT context_id, run_id FROM agui_batches WHERE "
                    "state IN ('frozen','reserved_pre_effect',"
                    "'execution_committed') AND absolute_deadline<=? "
                    "ORDER BY absolute_deadline LIMIT 1",
                    (current,)).fetchone()
                if row is None:
                    return expired
                self._expire_batch_in_tx(
                    connection, row["context_id"], row["run_id"], current)
            expired += 1

    # ── consumption by the follow-up acquire (same tx) ───────────────

    @staticmethod
    def _consume_batch_in_tx(connection, context_id: str, run_id: str,
                             now: float) -> None:
        connection.execute(
            "UPDATE agui_batches SET state='consumed', updated_at=? "
            "WHERE context_id=? AND run_id=? AND state='complete'",
            (now, context_id, run_id))
        connection.execute(
            "UPDATE agui_calls SET state='consumed', updated_at=? "
            "WHERE context_id=? AND run_id=? AND state='emitted'",
            (now, context_id, run_id))

    def batch_results(self, context_id: str, run_id: str
                      ) -> List[Dict[str, Any]]:
        """The server-held deposited results the agent is fed from."""
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT tool_call_id, tool, result_kind, result_json "
                "FROM agui_calls WHERE context_id=? AND run_id=? "
                "ORDER BY created_at", (context_id, run_id)).fetchall()
        return [dict(row) for row in rows]

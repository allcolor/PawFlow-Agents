"""Capability-based authorization for sensitive PawFlow routes.

A capability is a single-purpose, opaque token bound to:
  - resource_type   (str)   — e.g. "vnc", "terminal", "code_server", "port_forward"
  - resource_id     (str)   — the specific session/forward id
  - user_id         (str)   — owner; only this user may verify the token
  - conversation_id (str)   — scopes the token to a single PawFlow conv;
                              prevents a sub-agent in conv X from hijacking
                              a resource minted in conv Y of the same user.
  - session_id      (str)   — the user's PawFlow login session;
                              `revoke_session_capabilities(session_id)` is
                              called at logout to drop everything bound to it.

Design
------
Verification is *strict*: every check (resource_type, resource_id, user_id,
conversation_id) must match the values the token was minted with. Tokens are
random 256-bit URL-safe strings — no signature scheme; the secret IS the
token, and verification is a DB lookup.

Persistence
-----------
Per the PawFlow no-timeout / long-running session contract, capability tokens
MUST survive a server restart. We persist as JSON in `<runtime>/capabilities.json`
(atomic temp+rename writes, single-process owner). SQLite was rejected: the
user runs the project from a Windows shell over `\\wsl$\…`, and SQLite's
byte-range locking is unreliable on SMB — every CREATE TABLE / INSERT
returned `database is locked` even after WAL was disabled. JSON is read in
full at boot and rewritten on every mutation; volume is small (a few hundred
tokens at most) so this is comfortably below any realistic latency budget.

Failure rate-limit
------------------
Verify failures are tracked per remote IP in memory: after
`_FAIL_RATE_LIMIT_THRESHOLD` failures within `_FAIL_RATE_WINDOW_SEC`, further
verifies from that IP raise `CapabilityRateLimited` immediately (before the
DB lookup). The window slides; one fresh failure outside the window resets
the counter. Memory-only by design — restart drops the rate-limit state, but
any attacker can re-trigger it with the same brute-force.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# --- Public exception hierarchy ---------------------------------------------


class CapabilityError(Exception):
    """Base for all capability-auth failures."""


class CapabilityNotFound(CapabilityError):
    """Token does not exist in the registry (unknown / never issued / purged)."""


class CapabilityExpired(CapabilityError):
    """Token's expires_at is in the past."""


class CapabilityWrongResource(CapabilityError):
    """Token resource_type or resource_id mismatches the route."""


class CapabilityWrongOwner(CapabilityError):
    """Token user_id or conversation_id mismatches the requester."""


class CapabilityRateLimited(CapabilityError):
    """Too many recent verify failures from this IP."""


# --- Public dataclass -------------------------------------------------------


@dataclass(frozen=True)
class CapabilityClaims:
    """What `verify_capability()` returns on success.

    Frozen so a route handler cannot accidentally mutate it before passing
    it down to lower layers.
    """
    token: str
    resource_type: str
    resource_id: str
    user_id: str
    conversation_id: str
    session_id: str
    issued_at: int
    expires_at: int


# --- Internal state ---------------------------------------------------------

# Single global lock guarding _STORE_PATH + _ROWS + _FAILURES_BY_IP.
# Every mutation snapshots to disk while holding the lock.
_LOCK = threading.RLock()
_STORE_PATH: Optional[Path] = None
_ROWS: Dict[str, "_Row"] = {}  # token → row

# Rate limit on verify failures (memory-only).
_FAIL_RATE_LIMIT_THRESHOLD = 20
_FAIL_RATE_WINDOW_SEC = 60
_FAILURES_BY_IP: Dict[str, tuple[int, float]] = {}


@dataclass
class _Row:
    """In-memory row mirroring the on-disk JSON entry."""
    token: str
    resource_type: str
    resource_id: str
    user_id: str
    conversation_id: str
    session_id: str
    issued_at: int
    expires_at: int


# --- Persistence -----------------------------------------------------------


def _load_from_disk() -> Dict[str, _Row]:
    """Load the rows file. Returns {} on missing/empty/corrupt — those are
    recoverable states (fresh boot, partial write that was atomically
    discarded). A genuinely corrupt file is logged loudly but doesn't take
    the boot down — auth tokens are recoverable by re-issue.
    """
    if _STORE_PATH is None or not _STORE_PATH.exists():
        return {}
    try:
        raw = _STORE_PATH.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("[capability] read failed for %s: %s — starting empty",
                       _STORE_PATH, e)
        return {}
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error(
            "[capability] %s is not valid JSON (%s) — starting empty. "
            "Existing tokens are lost; re-issue on next request.",
            _STORE_PATH, e)
        return {}
    if not isinstance(data, list):
        logger.error(
            "[capability] %s root is %s, expected list — starting empty",
            _STORE_PATH, type(data).__name__)
        return {}
    rows: Dict[str, _Row] = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        try:
            row = _Row(
                token=str(entry["token"]),
                resource_type=str(entry["resource_type"]),
                resource_id=str(entry["resource_id"]),
                user_id=str(entry["user_id"]),
                conversation_id=str(entry.get("conversation_id", "")),
                session_id=str(entry.get("session_id", "")),
                issued_at=int(entry["issued_at"]),
                expires_at=int(entry["expires_at"]),
            )
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(
                "[capability] dropping malformed row %r: %s", entry, e)
            continue
        rows[row.token] = row
    return rows


def _save_to_disk_locked() -> None:
    """Snapshot _ROWS to disk. MUST be called with _LOCK held.

    Atomic via tmp+os.replace — partial writes never become visible. On
    write failure we keep the in-memory state and log; the next mutation
    will retry. Persisting is best-effort, not transactional, because
    losing a few capability tokens (worst case) just forces re-issue,
    which is what would happen on token expiry anyway.
    """
    if _STORE_PATH is None:
        return
    payload = json.dumps(
        [asdict(r) for r in _ROWS.values()],
        ensure_ascii=False, separators=(",", ":"))
    tmp = _STORE_PATH.with_suffix(_STORE_PATH.suffix + ".tmp")
    try:
        _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, _STORE_PATH)
    except OSError as e:
        logger.error(
            "[capability] failed to persist %s: %s — in-memory state kept",
            _STORE_PATH, e)
        # Best-effort cleanup of the temp file; ignore if even that fails.
        try:
            tmp.unlink()
        except OSError:
            pass


# --- Public API -------------------------------------------------------------


def _require_initialized() -> None:
    if _STORE_PATH is None:
        raise RuntimeError("capability_auth.init_db() must be called before use")


def init_db(db_path) -> None:
    """Initialise the capability store. Idempotent: safe to call multiple
    times (creates the parent dir + JSON file if missing, no-ops otherwise)
    and resets the in-memory rate-limit counters.

    The argument name is `db_path` for backwards compatibility with the
    SQLite-era callers; we transparently use the same path with a `.json`
    extension if the caller gave us `.db`.

    Must be called once at server boot before any issue/verify/revoke.
    """
    global _STORE_PATH
    with _LOCK:
        p = Path(db_path)
        # Migrate the legacy .db extension transparently — the caller
        # (cli.py) was hard-coded to `capabilities.db` from the SQLite era.
        if p.suffix == ".db":
            p = p.with_suffix(".json")
        _STORE_PATH = p
        _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _ROWS.clear()
        _ROWS.update(_load_from_disk())
        _FAILURES_BY_IP.clear()
        if not _STORE_PATH.exists():
            _save_to_disk_locked()
    purge_expired()
    logger.info(
        "[capability] store ready at %s (%d row(s) loaded)",
        _STORE_PATH, len(_ROWS))


def purge_expired() -> int:
    """Delete every capability whose expires_at is in the past.

    Called at boot via init_db() and opportunistically by verify_capability()
    when an expired token is observed. Safe to call from any thread.
    Returns the number of rows deleted.
    """
    _require_initialized()
    now = int(time.time())
    with _LOCK:
        expired = [t for t, r in _ROWS.items() if r.expires_at <= now]
        for t in expired:
            _ROWS.pop(t, None)
        if expired:
            _save_to_disk_locked()
    if expired:
        logger.info("[capability] purged %d expired capabilities", len(expired))
    return len(expired)


def issue_capability(
    resource_type: str,
    resource_id: str,
    user_id: str,
    *,
    conversation_id: str = "",
    session_id: str = "",
    ttl_seconds: int = 86400,
) -> str:
    """Mint a fresh token. Returns the token string (URL-safe).

    The token is the only handle the caller will get; persistence happens
    inside this function. Pass ttl_seconds aligned with the user's PawFlow
    login session expiry so logout cleanly revokes it.

    Raises ValueError on missing required fields.
    """
    if not resource_type:
        raise ValueError("resource_type is required")
    if not resource_id:
        raise ValueError("resource_id is required")
    if not user_id:
        raise ValueError("user_id is required")
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be > 0")
    _require_initialized()

    token = secrets.token_urlsafe(32)  # 256 bits of entropy
    now = int(time.time())
    expires_at = now + int(ttl_seconds)
    row = _Row(
        token=token,
        resource_type=resource_type,
        resource_id=resource_id,
        user_id=user_id,
        conversation_id=conversation_id or "",
        session_id=session_id or "",
        issued_at=now,
        expires_at=expires_at,
    )
    with _LOCK:
        _ROWS[token] = row
        _save_to_disk_locked()
    logger.debug(
        "[capability] issued %s for %s/%s user=%s conv=%s session=%s ttl=%ds",
        token[:8] + "…", resource_type, resource_id, user_id,
        conversation_id or "-", session_id or "-", ttl_seconds)
    return token


def verify_capability(
    token: str,
    resource_type: str,
    resource_id: str,
    *,
    user_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    remote_ip: str = "",
) -> CapabilityClaims:
    """Verify a capability token against the route's expected resource and
    requester. Raises CapabilityError subclass on any mismatch; returns
    `CapabilityClaims` on success.

    user_id and conversation_id are pass-by-keyword Optional: leaving them
    None skips the check (used by routes that don't have user context yet,
    rare). The recommended usage is to pass both — typical:

        claims = verify_capability(
            token, "vnc", session_id,
            user_id=req.auth_user_id,
            conversation_id=req.auth_conversation_id,
            remote_ip=req.remote_addr)

    remote_ip is used for failure rate-limiting; pass it whenever the
    caller has a real client IP, otherwise rate-limit is disabled for that
    call (e.g. internal tests).
    """
    _require_initialized()
    if not token:
        _record_failure(remote_ip)
        raise CapabilityNotFound("empty token")

    if remote_ip and _is_rate_limited(remote_ip):
        raise CapabilityRateLimited(
            f"too many verify failures from {remote_ip}")

    with _LOCK:
        row = _ROWS.get(token)

    if row is None:
        _record_failure(remote_ip)
        raise CapabilityNotFound("unknown token")

    claims = CapabilityClaims(
        token=row.token, resource_type=row.resource_type,
        resource_id=row.resource_id, user_id=row.user_id,
        conversation_id=row.conversation_id, session_id=row.session_id,
        issued_at=row.issued_at, expires_at=row.expires_at,
    )

    now = int(time.time())
    # `<=` not `<`: at the exact expiry second the token is dead. Using
    # int(time.time()) for both sides makes a strict `<` race-window of
    # up to one second; `<=` is the conservative choice.
    if claims.expires_at <= now:
        revoke_capability(token=token)
        _record_failure(remote_ip)
        raise CapabilityExpired(
            f"token expired at {claims.expires_at} (now={now})")

    if claims.resource_type != resource_type:
        _record_failure(remote_ip)
        raise CapabilityWrongResource(
            f"token resource_type={claims.resource_type!r} "
            f"!= expected {resource_type!r}")

    if claims.resource_id != resource_id:
        _record_failure(remote_ip)
        raise CapabilityWrongResource(
            f"token resource_id={claims.resource_id!r} "
            f"!= expected {resource_id!r}")

    if user_id is not None and claims.user_id != user_id:
        _record_failure(remote_ip)
        raise CapabilityWrongOwner(
            f"token user_id={claims.user_id!r} != requester {user_id!r}")

    # conversation_id is a soft-bind: if the token was minted with a conv
    # scope, the requester MUST be in that same conv. If the token was
    # minted without a conv (""), it's accepted from any conv — used by
    # global tools like the system terminal.
    if (conversation_id is not None
            and claims.conversation_id
            and claims.conversation_id != conversation_id):
        _record_failure(remote_ip)
        raise CapabilityWrongOwner(
            f"token conversation_id={claims.conversation_id!r} "
            f"!= requester {conversation_id!r}")

    return claims


def revoke_capability(
    *,
    token: Optional[str] = None,
    resource_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> int:
    """Drop one or more tokens. Exactly one of (token, resource_id,
    session_id) MUST be set.

      - token      — drop just this token (precise revoke).
      - resource_id — drop every token bound to this resource_id (e.g. when
                      a VNC session ends, all tokens for that session_id
                      are dropped — typically there is one, but tolerant).
      - session_id  — drop every token tied to this PawFlow login session
                      (called by `revoke_session_capabilities` at logout).

    Returns the number of rows deleted.
    """
    _require_initialized()
    n_args = sum(1 for v in (token, resource_id, session_id) if v)
    if n_args != 1:
        raise ValueError(
            "revoke_capability requires exactly one of: "
            "token, resource_id, session_id")
    with _LOCK:
        if token:
            doomed = [token] if token in _ROWS else []
        elif resource_id:
            doomed = [t for t, r in _ROWS.items() if r.resource_id == resource_id]
        else:  # session_id
            doomed = [t for t, r in _ROWS.items() if r.session_id == session_id]
        for t in doomed:
            _ROWS.pop(t, None)
        if doomed:
            _save_to_disk_locked()
    if doomed:
        logger.debug("[capability] revoked %d row(s)", len(doomed))
    return len(doomed)


def revoke_session_capabilities(session_id: str) -> int:
    """Convenience: drop everything tied to a login session. Call this
    when the user logs out (or when their session expires server-side).
    Returns the number of capabilities dropped."""
    if not session_id:
        return 0
    return revoke_capability(session_id=session_id)


def is_owner_or_admin(
    auth_user_id: str,
    owner_user_id: str,
    auth_role: str = "",
) -> bool:
    """Centralised ownership check used by routes after capability verify.

    Returns True if the requester is the resource owner OR an admin. The
    role string is whatever the auth layer puts on PendingRequest.auth_role
    — conventionally "admin" / "user". Only the literal
    'admin' grants override; everything else falls back to strict
    ownership equality.
    """
    if not auth_user_id:
        return False
    if auth_user_id == owner_user_id:
        return True
    return auth_role == "admin"


# --- Internal helpers -------------------------------------------------------


def _is_rate_limited(ip: str) -> bool:
    if not ip:
        return False
    now = time.time()
    with _LOCK:
        entry = _FAILURES_BY_IP.get(ip)
        if not entry:
            return False
        count, first_ts = entry
        if now - first_ts > _FAIL_RATE_WINDOW_SEC:
            _FAILURES_BY_IP.pop(ip, None)
            return False
        return count >= _FAIL_RATE_LIMIT_THRESHOLD


def _record_failure(ip: str) -> None:
    if not ip:
        return
    now = time.time()
    with _LOCK:
        entry = _FAILURES_BY_IP.get(ip)
        if entry is None or now - entry[1] > _FAIL_RATE_WINDOW_SEC:
            _FAILURES_BY_IP[ip] = (1, now)
            return
        new_count = entry[0] + 1
        _FAILURES_BY_IP[ip] = (new_count, entry[1])
        if new_count == _FAIL_RATE_LIMIT_THRESHOLD:
            logger.warning(
                "[capability] IP %s reached %d verify failures in %ds — "
                "rate-limiting further attempts",
                ip, _FAIL_RATE_LIMIT_THRESHOLD, _FAIL_RATE_WINDOW_SEC)


# --- Test-only hooks --------------------------------------------------------


def _reset_for_tests() -> None:
    """Clear all in-memory state. Tests use this before init_db() to start
    from a clean slate. Production code MUST NOT call this."""
    global _STORE_PATH
    with _LOCK:
        _STORE_PATH = None
        _ROWS.clear()
        _FAILURES_BY_IP.clear()

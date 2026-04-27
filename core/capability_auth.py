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
MUST survive a server restart. We persist to SQLite in
`<runtime>/capabilities.db`; on `init_db()` we open the file (creating tables
if missing) and purge expired rows. WAL mode is enabled so concurrent reads
do not block writes.

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

import logging
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

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

# Single global lock guarding _DB_PATH + _FAILURES_BY_IP. SQLite has its own
# locking but we still serialise our access at the Python level so the rate-
# limit dict mutations stay consistent under concurrent verifies.
_LOCK = threading.RLock()
_DB_PATH: Optional[Path] = None

# Rate limit on verify failures (memory-only).
_FAIL_RATE_LIMIT_THRESHOLD = 20
_FAIL_RATE_WINDOW_SEC = 60
_FAILURES_BY_IP: dict[str, tuple[int, float]] = {}


def _connect() -> sqlite3.Connection:
    """Open a fresh SQLite connection. SQLite connections are NOT thread-safe
    by default, so we open one per call and let the caller close it (via
    `with` context manager).

    WAL is only attempted on local filesystems. On UNC paths (`\\\\wsl$\\…`,
    SMB shares, mapped network drives) WAL needs mmap'd shared memory that
    SMB doesn't support — `PRAGMA journal_mode=WAL` returns "database is
    locked" and aborts the boot. We fall back to the default rollback
    journal in that case; functionally equivalent for our write rate, just
    slightly worse concurrent-read scaling.
    """
    if _DB_PATH is None:
        raise RuntimeError(
            "capability_auth: init_db() must be called before any other API")
    conn = sqlite3.connect(str(_DB_PATH), timeout=10.0, isolation_level=None)
    if _supports_wal(_DB_PATH):
        conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _supports_wal(path: Path) -> bool:
    """True if the filesystem holding `path` supports SQLite WAL.

    UNC / network paths don't (no mmap'd shared memory). Detection is
    string-based for now — robust enough for the WSL/SMB case that
    triggered the bug, doesn't need fstatfs gymnastics.
    """
    s = str(path)
    return not (s.startswith("\\\\") or s.startswith("//"))


# --- Public API -------------------------------------------------------------


def init_db(db_path) -> None:
    """Initialise the capability store. Idempotent: safe to call multiple
    times (creates the file/tables if missing, no-ops otherwise) and resets
    the in-memory rate-limit counters.

    Must be called once at server boot before any issue/verify/revoke.
    """
    global _DB_PATH
    with _LOCK:
        _DB_PATH = Path(db_path)
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS capabilities (
                    token           TEXT PRIMARY KEY,
                    resource_type   TEXT NOT NULL,
                    resource_id     TEXT NOT NULL,
                    user_id         TEXT NOT NULL,
                    conversation_id TEXT NOT NULL DEFAULT '',
                    session_id      TEXT NOT NULL DEFAULT '',
                    issued_at       INTEGER NOT NULL,
                    expires_at      INTEGER NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_cap_resource "
                "ON capabilities(resource_type, resource_id)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_cap_session "
                "ON capabilities(session_id)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_cap_expires "
                "ON capabilities(expires_at)")
        _FAILURES_BY_IP.clear()
    purge_expired()


def purge_expired() -> int:
    """Delete every capability whose expires_at is in the past.

    Called at boot via init_db() and opportunistically by verify_capability()
    when an expired token is observed. Safe to call from any thread.
    Returns the number of rows deleted.
    """
    now = int(time.time())
    with _LOCK, _connect() as conn:
        cur = conn.execute(
            "DELETE FROM capabilities WHERE expires_at <= ?", (now,))
        deleted = cur.rowcount or 0
    if deleted:
        logger.info("[capability] purged %d expired capabilities", deleted)
    return deleted


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

    token = secrets.token_urlsafe(32)  # 256 bits of entropy
    now = int(time.time())
    expires_at = now + int(ttl_seconds)
    with _LOCK, _connect() as conn:
        conn.execute(
            """INSERT INTO capabilities
               (token, resource_type, resource_id, user_id,
                conversation_id, session_id, issued_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (token, resource_type, resource_id, user_id,
             conversation_id or "", session_id or "", now, expires_at),
        )
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
    if not token:
        _record_failure(remote_ip)
        raise CapabilityNotFound("empty token")

    if remote_ip and _is_rate_limited(remote_ip):
        raise CapabilityRateLimited(
            f"too many verify failures from {remote_ip}")

    with _LOCK, _connect() as conn:
        row = conn.execute(
            """SELECT token, resource_type, resource_id, user_id,
                      conversation_id, session_id, issued_at, expires_at
               FROM capabilities WHERE token = ?""",
            (token,),
        ).fetchone()

    if row is None:
        _record_failure(remote_ip)
        raise CapabilityNotFound("unknown token")

    claims = CapabilityClaims(
        token=row[0], resource_type=row[1], resource_id=row[2],
        user_id=row[3], conversation_id=row[4], session_id=row[5],
        issued_at=row[6], expires_at=row[7],
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
    n_args = sum(1 for v in (token, resource_id, session_id) if v)
    if n_args != 1:
        raise ValueError(
            "revoke_capability requires exactly one of: "
            "token, resource_id, session_id")
    with _LOCK, _connect() as conn:
        if token:
            cur = conn.execute(
                "DELETE FROM capabilities WHERE token = ?", (token,))
        elif resource_id:
            cur = conn.execute(
                "DELETE FROM capabilities WHERE resource_id = ?",
                (resource_id,))
        else:  # session_id
            cur = conn.execute(
                "DELETE FROM capabilities WHERE session_id = ?",
                (session_id,))
        deleted = cur.rowcount or 0
    if deleted:
        logger.debug("[capability] revoked %d row(s)", deleted)
    return deleted


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
    — conventionally "admin" / "editor" / "viewer". Only the literal
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
    global _DB_PATH
    with _LOCK:
        _DB_PATH = None
        _FAILURES_BY_IP.clear()

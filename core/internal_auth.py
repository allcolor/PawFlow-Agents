"""Ephemeral internal-auth tokens for on-host service-to-server WS calls.

The CC Docker container runs inside the server process (docker exec on the
same host) and has no user session cookies. It needs to authenticate its
WebSocket tool-relay connection against the main HTTPListener, which
normally enforces the private gateway cookie and a live session/API key.

The server mints a fresh token at each MCP config write and passes it to
the MCP bridge via env. On WS upgrade for /ws/tools/* routes, presentation
of a valid token bypasses the gateway + session checks (those routes still
enforce their own tool-relay register-step token).

Tokens have NO expiry — they remain valid as long as they sit in the
registry. The caller revokes explicitly when the container / call ends
(e.g. on pool release, on provider teardown). The registry is in-memory
only (no disk), so a server restart wipes everything.
"""

import logging
import secrets
import threading
from typing import Set

logger = logging.getLogger(__name__)

_tokens: Set[str] = set()
_lock = threading.Lock()


def mint_token() -> str:
    """Create a fresh internal token. Valid until revoke_token() is called."""
    tok = secrets.token_urlsafe(32)
    with _lock:
        _tokens.add(tok)
    return tok


def validate_token(tok: str) -> bool:
    """Return True iff tok is in the registry."""
    if not tok:
        return False
    with _lock:
        return tok in _tokens


def revoke_token(tok: str) -> None:
    """Invalidate a token. Idempotent."""
    if not tok:
        return
    with _lock:
        _tokens.discard(tok)


def active_count() -> int:
    with _lock:
        return len(_tokens)

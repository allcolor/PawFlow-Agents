"""Ephemeral access tokens for the relay HTTP proxy.

Tokens are issued per Claude Code session (or any client that needs to
reach a local service through a relay). They bind together:
  - the relay_id allowed to handle the call
  - the user_id owning the relay
  - an expiry (default: 10 minutes)

Only the holder of the token and the server know its value. Tokens are
stored in memory (no persistence) and revoked on session end.

The route that consumes the token is gateway-exempt for non-browser clients,
but remains private-IP only. Generated URLs use the listener's private address.
Keep the token short-lived.
"""

import logging
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class _ProxyTokenEntry:
    user_id: str
    relay_id: str
    expires_at: float
    conv_id: str = ""


_tokens: Dict[str, _ProxyTokenEntry] = {}
_lock = threading.Lock()

DEFAULT_TTL = 600.0  # 10 minutes


def issue_token(user_id: str, relay_id: str, ttl: float = DEFAULT_TTL,
                conv_id: str = "") -> str:
    """Mint a new proxy token bound to (user_id, relay_id[, conv_id]).

    `conv_id` lets the proxy handler resolve conversation-scoped relays;
    without it only user/global relays can be reached.
    """
    if not user_id or not relay_id:
        raise ValueError("user_id and relay_id are required")
    token = secrets.token_urlsafe(32)
    with _lock:
        _tokens[token] = _ProxyTokenEntry(
            user_id=user_id,
            relay_id=relay_id,
            expires_at=time.time() + ttl,
            conv_id=conv_id,
        )
        _gc_expired_locked()
    logger.debug("Proxy token issued for user=%s relay=%s conv=%s", user_id, relay_id, conv_id)
    return token


def lookup_token(token: str) -> Optional[Tuple[str, str, str]]:
    """Return (user_id, relay_id, conv_id) if the token is valid, else None."""
    with _lock:
        entry = _tokens.get(token)
        if entry is None:
            return None
        if time.time() > entry.expires_at:
            _tokens.pop(token, None)
            return None
        return entry.user_id, entry.relay_id, entry.conv_id


def revoke_token(token: str) -> bool:
    with _lock:
        return _tokens.pop(token, None) is not None


def revoke_for_relay(user_id: str, relay_id: str) -> int:
    """Revoke all tokens matching (user_id, relay_id). Returns count revoked."""
    with _lock:
        to_drop = [t for t, e in _tokens.items()
                   if e.user_id == user_id and e.relay_id == relay_id]
        for t in to_drop:
            _tokens.pop(t, None)
    return len(to_drop)


def _gc_expired_locked() -> None:
    now = time.time()
    expired = [t for t, e in _tokens.items() if now > e.expires_at]
    for t in expired:
        _tokens.pop(t, None)


def is_private_ip(ip: str) -> bool:
    """Return True if the IP is in a private range (RFC 1918) or localhost.

    The proxy route MUST reject public IPs even if the token is valid,
    so leaked URLs cannot be abused from the internet.
    """
    if not ip:
        return False
    if ip in ("127.0.0.1", "::1", "localhost"):
        return True
    # IPv4-mapped IPv6
    if ip.startswith("::ffff:"):
        ip = ip[7:]
    try:
        parts = [int(p) for p in ip.split(".")]
    except ValueError:
        return False
    if len(parts) != 4:
        return False
    a, b, *_ = parts
    if a == 10:
        return True
    if a == 172 and 16 <= b <= 31:
        return True
    if a == 192 and b == 168:
        return True
    if a == 127:
        return True
    # Docker default bridge is commonly 172.17.x — already covered by 172.16-31
    return False

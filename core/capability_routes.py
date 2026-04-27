"""Helpers wiring `core.capability_auth` to HTTP/WS route handlers.

Callers (vnc_proxy, terminal_proxy, code_server_proxy, port_forward_proxy)
use `mint_route_token()` at session-register time and
`verify_route_request()` / `verify_route_ws()` from inside their handler
before serving any content. The helpers centralise the owner check so a
route never accidentally accepts a token whose user_id matches but whose
resource_type / resource_id do not.
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

from core import capability_auth as _ca

logger = logging.getLogger(__name__)


def mint_route_token(
    resource_type: str,
    resource_id: str,
    user_id: str,
    *,
    conversation_id: str = "",
    session_id: str = "",
    ttl_seconds: int = 86400,
) -> str:
    """Mint a fresh capability token for a sensitive route.

    Returns the URL-safe token. Caller embeds it in the URL handed to
    the user (`/vnc/<session>/<token>/...`); the matching handler
    calls `verify_route_request` / `verify_route_ws` to check it.
    """
    return _ca.issue_capability(
        resource_type, resource_id, user_id,
        conversation_id=conversation_id, session_id=session_id,
        ttl_seconds=ttl_seconds)


def revoke_route_tokens(resource_id: str) -> int:
    """Drop every capability token attached to a resource_id (called
    when the underlying VNC / terminal / code-server / port-forward is
    closed)."""
    if not resource_id:
        return 0
    return _ca.revoke_capability(resource_id=resource_id)


def _http_response(status: int, body: str = "") -> dict:
    """Shape a quick HTTP response dict for handlers that don't want to
    raise. Keys mirror PendingRequest.response_* so the relay can pass
    it through unchanged."""
    return {
        "status": status,
        "headers": {"Content-Type": "text/plain; charset=utf-8"},
        "body": body or _DEFAULT_BODIES.get(status, ""),
    }


_DEFAULT_BODIES = {
    401: "401 Unauthorized",
    403: "403 Forbidden",
    404: "404 Not Found",
    429: "429 Too Many Requests",
}


def verify_route_request(
    req,
    resource_type: str,
    resource_id: str,
    token: str,
    *,
    conversation_id: Optional[str] = None,
) -> Tuple[Optional[_ca.CapabilityClaims], Optional[dict]]:
    """Check a capability token attached to an HTTP `PendingRequest`.

    Returns `(claims, None)` on success, or `(None, http_response)` on
    failure where `http_response` is a ready-to-send 4xx dict.

    The check requires the request to have been authenticated (the
    HTTP listener stamps `auth_user_id` after the session/API-key
    check passes); a public / unauthenticated route reaching this
    helper indicates a misconfiguration and is rejected.

    `conversation_id` is forwarded to `verify_capability` when the
    caller knows the conversation the requester is acting from
    (typical for sub-agent paths via tool_relay_service). Browser
    routes do not carry the conv id in the URL and pass `None`,
    which means "no conv check" — a token minted without conv
    scope (`""`) is accepted from any conv, by design.
    """
    auth_user = getattr(req, "auth_user_id", "") or ""
    if not auth_user:
        return None, _http_response(401)
    remote = getattr(req, "remote_addr", "") or ""
    return _verify_or_403(
        token, resource_type, resource_id, auth_user, remote,
        conversation_id=conversation_id)


def _ws_close_frame(code: int, reason: str) -> bytes:
    """Build a WebSocket close control frame. The handler is called
    AFTER the listener has completed the 101 upgrade, so an HTTP
    status line on the socket would be invalid bytes from the
    browser's perspective. A close frame is the protocol-correct way
    to refuse the connection.

    `code` is the WS close code (1008 Policy Violation, 1011 Internal
    Error, etc.); `reason` is a short UTF-8 string truncated to fit
    the 125-byte single-frame payload limit.
    """
    import struct
    payload = struct.pack("!H", code & 0xFFFF) + reason.encode("utf-8")[:123]
    return bytes([0x88, len(payload)]) + payload


def verify_route_ws(
    meta: dict,
    resource_type: str,
    resource_id: str,
    token: str,
    *,
    conversation_id: Optional[str] = None,
) -> Tuple[Optional[_ca.CapabilityClaims], Optional[bytes]]:
    """WebSocket variant. `meta` is the dict the HTTP listener passes
    to ws_handler (already carries `auth_user_id` etc.). Returns
    `(claims, None)` on success, or `(None, ws_close_frame_bytes)`
    on failure — a CLOSE frame the handler MUST sendall + close.

    The listener has already completed the 101 upgrade by the time
    this runs, so we cannot use HTTP status lines (the previous
    `b"HTTP/1.1 403"` trick produced protocol-violation bytes that
    confused some browsers). Close codes follow RFC 6455:
      1008 — Policy Violation (auth failure)
      1011 — Internal Error (rate limited / unknown)
    """
    auth_user = (meta or {}).get("auth_user_id", "") or ""
    if not auth_user:
        return None, _ws_close_frame(1008, "unauthenticated")
    remote = (meta or {}).get("remote_addr", "") or ""
    claims, response = _verify_or_403(
        token, resource_type, resource_id, auth_user, remote,
        conversation_id=conversation_id)
    if claims is not None:
        return claims, None
    status = (response or {}).get("status", 403)
    if status == 401:
        return None, _ws_close_frame(1008, "unauthenticated")
    if status == 429:
        return None, _ws_close_frame(1013, "rate limited")
    return None, _ws_close_frame(1008, (response or {}).get("body") or "forbidden")


def _verify_or_403(
    token: str,
    resource_type: str,
    resource_id: str,
    auth_user: str,
    remote_ip: str,
    *,
    conversation_id: Optional[str] = None,
) -> Tuple[Optional[_ca.CapabilityClaims], Optional[dict]]:
    if not token:
        return None, _http_response(401, "missing capability token")
    try:
        claims = _ca.verify_capability(
            token, resource_type, resource_id,
            user_id=auth_user,
            conversation_id=conversation_id,
            remote_ip=remote_ip)
        return claims, None
    except _ca.CapabilityRateLimited as e:
        logger.warning("[route-auth] rate-limited: %s", e)
        return None, _http_response(429)
    except _ca.CapabilityNotFound:
        return None, _http_response(403, "unknown capability")
    except _ca.CapabilityExpired:
        return None, _http_response(403, "capability expired")
    except _ca.CapabilityWrongResource as e:
        logger.warning("[route-auth] wrong resource: %s", e)
        return None, _http_response(403, "capability mismatched")
    except _ca.CapabilityWrongOwner as e:
        logger.warning("[route-auth] wrong owner: %s", e)
        return None, _http_response(403, "capability not yours")

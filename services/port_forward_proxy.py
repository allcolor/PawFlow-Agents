"""Port Forward Proxy — forwards HTTP requests to a relay's local port.

Routes: /fwd/{forward_id}/{token}/{path+}. Each forward gets its own
random forward_id (UUID prefix); the capability token bound to it
decides who can hit the URL. The internal routing (relay_id, int_port)
is kept server-side and never exposed in the URL.
"""

import base64
import json
import logging
import threading
import uuid
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# forward_id → {relay_id, int_port, ext_port (label only),
#               relay_service, owner_user_id, capability_token,
#               created_at, expires_at, description}
_forwards: Dict[str, dict] = {}
_lock = threading.Lock()

_ROUTE_OWNER = "_port_fwd_routes"


def add_forward(relay_id: str, int_port: int, relay_service,
                ext_port: int = 0, *,
                owner_user_id: str = "",
                conversation_id: str = "",
                login_session_id: str = "",
                ttl_seconds: int = 28800,
                description: str = "") -> Tuple[bool, str, str]:
    """Register a port forward and mint its capability token.

    Returns (first, forward_id, token):
      - first: True if this is the first forward registered (caller
        uses this signal to register the HTTP route the first time).
      - forward_id: opaque random id for the URL.
      - token: capability token — caller embeds it in the URL handed
        to the user (`/fwd/<forward_id>/<token>/...`).

    `owner_user_id` is required for non-test callers — every forward
    is bound to exactly one PawFlow user and a leaked URL stops
    working at logout (revoke_session_capabilities).
    """
    if not ext_port:
        ext_port = int_port
    if not owner_user_id:
        raise ValueError("add_forward: owner_user_id is required")
    forward_id = uuid.uuid4().hex[:16]
    from core.capability_routes import mint_route_token
    token = mint_route_token(
        "port_forward", forward_id, owner_user_id,
        conversation_id=conversation_id,
        session_id=login_session_id,
        ttl_seconds=ttl_seconds)
    import time as _time
    now = int(_time.time())
    with _lock:
        first = len(_forwards) == 0
        _forwards[forward_id] = {
            "forward_id": forward_id,
            "relay_id": relay_id,
            "int_port": int_port,
            "ext_port": ext_port,
            "relay_service": relay_service,
            "owner_user_id": owner_user_id,
            "capability_token": token,
            "created_at": now,
            "expires_at": now + ttl_seconds,
            "description": description,
        }
    logger.info(
        "Port forward registered: forward_id=%s relay=%s ext=%d → int=%d owner=%s",
        forward_id, relay_id, ext_port, int_port, owner_user_id)
    return first, forward_id, token


def remove_forward(forward_id: str = "", *,
                   relay_id: str = "", ext_port: int = 0) -> bool:
    """Remove a forward. Either pass `forward_id` (preferred) or the
    legacy (relay_id, ext_port) pair. Returns True when no forwards
    remain (caller signal to unregister the HTTP route).
    """
    with _lock:
        if not forward_id:
            for fid, entry in list(_forwards.items()):
                if (entry.get("relay_id") == relay_id
                        and entry.get("ext_port") == ext_port):
                    forward_id = fid
                    break
        entry = _forwards.pop(forward_id, None) if forward_id else None
        last = entry is not None and len(_forwards) == 0
    if entry:
        try:
            from core.capability_routes import revoke_route_tokens
            revoke_route_tokens(forward_id)
        except Exception:
            pass
        logger.info(
            "Port forward removed: forward_id=%s relay=%s ext=%d",
            forward_id, entry.get("relay_id", ""), entry.get("ext_port", 0))
    return last


def list_forwards() -> List[dict]:
    """List all active port forwards (with their capability URL)."""
    with _lock:
        return [
            {
                "forward_id": v["forward_id"],
                "relay_id": v["relay_id"],
                "int_port": v["int_port"],
                "ext_port": v["ext_port"],
                "owner_user_id": v["owner_user_id"],
                "created_at": v["created_at"],
                "expires_at": v["expires_at"],
                "description": v["description"],
                "url": f"/fwd/{v['forward_id']}/{v['capability_token']}/",
            }
            for v in _forwards.values()
        ]


def remove_all_for_relay(relay_id: str) -> bool:
    """Remove all forwards for a relay. Returns True when no forwards
    remain after the cleanup."""
    with _lock:
        to_remove = [
            fid for fid, v in _forwards.items() if v["relay_id"] == relay_id]
    for fid in to_remove:
        remove_forward(forward_id=fid)
    with _lock:
        return len(_forwards) == 0


# -- HTTP proxy callback --

def fwd_http_proxy(pending_req):
    """HTTP callback for /fwd/{forward_id}/{token}/{path+}.

    The capability token in the path binds the requester (auth_user)
    to this forward; cross-user access is rejected 403 here, before
    any backend call.
    """
    forward_id = pending_req.path_params.get("forward_id", "")
    token = pending_req.path_params.get("token", "")
    sub_path = pending_req.path_params.get("path", "")

    from core.capability_routes import verify_route_request
    claims, err = verify_route_request(
        pending_req, "port_forward", forward_id, token)
    if err is not None:
        pending_req.complete(
            err["status"], err["headers"], err["body"].encode("utf-8"))
        return

    with _lock:
        entry = _forwards.get(forward_id)
    if not entry:
        pending_req.complete(404, {"Content-Type": "application/json"},
                             json.dumps({"error": "unknown forward"}).encode())
        return

    relay_service = entry["relay_service"]
    int_port = entry["int_port"]
    proxied_path = "/" + sub_path
    query = pending_req.query_string
    if query:
        proxied_path += "?" + query

    # Forward headers (strip hop-by-hop)
    fwd_headers = {}
    for k, v in pending_req.headers.items():
        kl = k.lower()
        if kl in ("host", "connection", "upgrade", "sec-websocket-key",
                  "sec-websocket-version", "sec-websocket-extensions"):
            continue
        fwd_headers[k] = v
    fwd_headers["Host"] = f"127.0.0.1:{int_port}"

    try:
        result = relay_service._request(
            "http_proxy",
            port=int_port,
            method=pending_req.method,
            req_path=proxied_path,
            req_headers=fwd_headers,
            req_body=base64.b64encode(pending_req.body).decode("ascii") if pending_req.body else "",
        )
        if not isinstance(result, dict) or "status" not in result:
            pending_req.complete(502, {"Content-Type": "text/plain"},
                                 f"Bad proxy response: {result}".encode())
            return

        status = result["status"]
        resp_headers = result.get("headers", {})
        resp_body = base64.b64decode(result.get("body", "")) if result.get("body") else b""

        for k in list(resp_headers):
            if k.lower() in ("transfer-encoding", "connection", "keep-alive"):
                del resp_headers[k]

        pending_req.complete(status, resp_headers, resp_body)
    except Exception as e:
        logger.warning(
            "Port forward proxy error for %s (relay=%s int=%d): %s",
            forward_id, entry.get("relay_id", ""), int_port, e)
        pending_req.complete(502, {"Content-Type": "application/json"},
                             json.dumps({"error": str(e)}).encode())


def fwd_root_redirect(pending_req):
    """Redirect /fwd/{forward_id}/{token} → /fwd/{forward_id}/{token}/
    (trailing slash). The redirect is unauthenticated by design — a
    bad token still receives the redirect, then the followed request
    is rejected 403 by the real handler."""
    fid = pending_req.path_params.get("forward_id", "")
    tok = pending_req.path_params.get("token", "")
    pending_req.complete(301, {"Location": f"/fwd/{fid}/{tok}/"}, b"")

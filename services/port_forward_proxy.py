"""Port Forward Proxy — forwards HTTP requests to a relay's local port.

Similar to code_server_proxy.py but generic: any relay, any port.
Routes: /fwd/{relay_id}/{ext_port}/{path+}
Proxies to localhost:{int_port} on the relay.

External port (in URL) can differ from internal port (on relay).
"""

import base64
import json
import logging
import threading
from typing import Dict

logger = logging.getLogger(__name__)

# relay_id:ext_port → {relay_service, int_port, ext_port, relay_id}
_forwards: Dict[str, dict] = {}
_lock = threading.Lock()


def _fwd_key(relay_id: str, ext_port: int) -> str:
    return f"{relay_id}:{ext_port}"


_ROUTE_OWNER = "_port_fwd_routes"


def add_forward(relay_id: str, int_port: int, relay_service,
               ext_port: int = 0) -> bool:
    """Register a port forward. Returns True if this is the first forward (routes need registering).

    Args:
        relay_id: Relay service ID.
        int_port: Port on the relay (internal).
        ext_port: Port in the URL (external). Defaults to int_port.
    """
    if not ext_port:
        ext_port = int_port
    key = _fwd_key(relay_id, ext_port)
    with _lock:
        first = len(_forwards) == 0
        _forwards[key] = {
            "relay_id": relay_id,
            "int_port": int_port,
            "ext_port": ext_port,
            "relay_service": relay_service,
        }
    logger.info("Port forward registered: %s ext=%d → int=%d", relay_id, ext_port, int_port)
    return first


def remove_forward(relay_id: str, ext_port: int) -> bool:
    """Remove a port forward. Returns True if no forwards remain (routes should be unregistered)."""
    key = _fwd_key(relay_id, ext_port)
    with _lock:
        entry = _forwards.pop(key, None)
        last = entry is not None and len(_forwards) == 0
    if entry:
        logger.info("Port forward removed: %s ext=%d", relay_id, ext_port)
    return last


def list_forwards() -> list:
    """List all active port forwards."""
    with _lock:
        return [
            {"relay_id": v["relay_id"],
             "int_port": v["int_port"],
             "ext_port": v["ext_port"],
             "url": f"/fwd/{v['relay_id']}/{v['ext_port']}/"}
            for v in _forwards.values()
        ]


def remove_all_for_relay(relay_id: str) -> bool:
    """Remove all forwards for a relay. Returns True if no forwards remain."""
    with _lock:
        to_remove = [k for k, v in _forwards.items() if v["relay_id"] == relay_id]
        for k in to_remove:
            _forwards.pop(k)
        return len(to_remove) > 0 and len(_forwards) == 0


# -- HTTP proxy callback --

def fwd_http_proxy(pending_req):
    """HTTP callback for /fwd/{relay_id}/{ext_port}/{path+}."""
    relay_id = pending_req.path_params.get("relay_id", "")
    ext_port_str = pending_req.path_params.get("ext_port", "")
    sub_path = pending_req.path_params.get("path", "")

    try:
        ext_port = int(ext_port_str)
    except (ValueError, TypeError):
        pending_req.complete(400, {"Content-Type": "application/json"},
                             b'{"error": "Invalid port"}')
        return

    key = _fwd_key(relay_id, ext_port)
    with _lock:
        entry = _forwards.get(key)
    if not entry:
        pending_req.complete(404, {"Content-Type": "application/json"},
                             json.dumps({"error": f"No forward for {relay_id}:{ext_port}"}).encode())
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
        logger.warning("Port forward proxy error for %s:%d: %s", relay_id, int_port, e)
        pending_req.complete(502, {"Content-Type": "application/json"},
                             json.dumps({"error": str(e)}).encode())


def fwd_root_redirect(pending_req):
    """Redirect /fwd/{relay_id}/{ext_port} to /fwd/{relay_id}/{ext_port}/ (trailing slash)."""
    relay_id = pending_req.path_params.get("relay_id", "")
    ext_port = pending_req.path_params.get("ext_port", "")
    pending_req.complete(301, {"Location": f"/fwd/{relay_id}/{ext_port}/"}, b"")

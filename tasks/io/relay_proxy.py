"""Relay HTTP Proxy Task — expose LLM calls through the user's relay.

Registers a route `ANY /relay-proxy/<relay_id>/<token>/...` on the shared
HTTP listener. Incoming requests are forwarded to the relay via its
WebSocket connection; the relay executes an http_fetch on the user's
machine and streams the response back.

Security:
  - The token in the URL is an ephemeral (~1h) credential bound to
    (user_id, relay_id). See core/relay_proxy_auth.py.
  - Only requests from private IPs (RFC 1918 / localhost) are accepted
    even if the token is valid — prevents abuse if the URL leaks.
  - The token and route bypass the auth gateway because the CC container
    has no HTTP session to carry cookies.
"""

import base64
import json
import logging
import threading
from typing import Any, Dict, List, Optional

from core import FlowFile, TaskFactory
from core.base_task import BaseTask

logger = logging.getLogger(__name__)

_ROUTE_OWNER = "_relay_proxy"


def _get_http_listener():
    from services.http_listener_service import HTTPListenerService
    instances = HTTPListenerService.all_instances()
    return next(iter(instances.values()), None)


def _resolve_relay_service(user_id: str, relay_id: str, conv_id: str = ""):
    """Return the live RelayService instance for (user_id, relay_id)."""
    from core.service_registry import ServiceRegistry
    reg = ServiceRegistry.get_instance()
    try:
        # Canonical scope walk: conv > user > global, so conversation-scoped
        # relays are reachable when the token carries a conv_id.
        return reg.resolve(relay_id, user_id=user_id, conv_id=conv_id)
    except Exception:
        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
    return None


def _relay_proxy_handler(pending_req):
    """Handle /relay-proxy/<relay_id>/<token>/[s/]<host>:<port>/<path>."""
    from core.relay_proxy_auth import lookup_token, is_private_ip

    relay_id = pending_req.path_params.get("relay_id", "")
    token = pending_req.path_params.get("token", "")
    rest = pending_req.path_params.get("rest", "")
    # DEBUG-level entry log — per-request IN/END below are the INFO
    # line operators actually watch. Re-enable at INFO only when
    # chasing an intercept (gateway, auth, route match) that rejects
    # before the IN line.
    logger.debug(
        "relay-proxy HIT method=%s path=%s src=%s relay=%s token_prefix=%s",
        pending_req.method, pending_req.path,
        pending_req.remote_addr, relay_id, token[:8])

    # Source IP restriction — no external access even with a valid token
    src_ip = pending_req.remote_addr or ""
    if not is_private_ip(src_ip):
        logger.warning("relay-proxy: rejected request from public IP %s", src_ip)
        pending_req.complete(403, {"Content-Type": "application/json"},
                             b'{"error":"Forbidden: external IP"}')
        return

    # Token check
    auth = lookup_token(token)
    if auth is None:
        pending_req.complete(401, {"Content-Type": "application/json"},
                             b'{"error":"Invalid or expired proxy token"}')
        return
    user_id, bound_relay_id, conv_id = auth
    if bound_relay_id != relay_id:
        pending_req.complete(403, {"Content-Type": "application/json"},
                             b'{"error":"Token does not match relay"}')
        return

    # Parse target from rest: [s/]host:port/path
    target_scheme = "http"
    if rest.startswith("s/"):
        target_scheme = "https"
        rest = rest[2:]
    # First segment is host:port, remainder is the path to forward
    if "/" in rest:
        target_hostport, _, target_path = rest.partition("/")
        target_path = "/" + target_path
    else:
        target_hostport = rest
        target_path = "/"
    if ":" not in target_hostport:
        pending_req.complete(400, {"Content-Type": "application/json"},
                             b'{"error":"Malformed target host:port"}')
        return
    # Preserve the query string
    if pending_req.query_string:
        target_path = f"{target_path}?{pending_req.query_string}"

    svc = _resolve_relay_service(user_id, relay_id, conv_id)
    if svc is None or not hasattr(svc, "http_fetch_stream"):
        logger.warning("relay-proxy: relay '%s' not available for user '%s'",
                       relay_id, user_id)
        pending_req.complete(502, {"Content-Type": "application/json"},
                             b'{"error":"Relay not connected"}')
        return

    target_url = f"{target_scheme}://{target_hostport}{target_path}"
    method = pending_req.method or "GET"
    # Per-request log tag so concurrent flows are traceable. Using the
    # proxy token's first 8 chars as a tag — ephemeral + unique per
    # request, and already safe to log (it's only useful for this one
    # in-flight request).
    _log_tag = f"relay-proxy[{token[:8]}]"
    logger.debug(
        "%s IN %s → target=%s body=%dB",
        _log_tag, method, target_url, len(pending_req.body or b""))

    # Forward headers (minus hop-by-hop and Host)
    _drop = {"host", "connection", "content-length", "transfer-encoding",
             "cookie"}
    fwd_headers = {k: v for k, v in pending_req.headers.items()
                   if k.lower() not in _drop}

    # Streaming state
    _started = threading.Event()
    _state = {"status": 502, "headers": {}, "error": ""}
    _queue = []  # list of bytes chunks
    _queue_lock = threading.Lock()
    _queue_event = threading.Event()
    _done = threading.Event()
    _stats = {"bytes": 0, "chunks": 0, "t0": 0.0}

    def _on_chunk(kind: str, data: Any):
        if kind == "start":
            _state["status"] = int(data.get("status", 200))
            _state["headers"] = dict(data.get("headers") or {})
            import time as _t
            _stats["t0"] = _t.monotonic()
            _started.set()
        elif kind == "chunk":
            try:
                raw = base64.b64decode(data) if isinstance(data, str) else data
            except Exception:
                raw = b""
            _stats["bytes"] += len(raw)
            _stats["chunks"] += 1
            with _queue_lock:
                _queue.append(raw)
            _queue_event.set()
        elif kind == "end":
            import time as _t
            _elapsed = _t.monotonic() - _stats["t0"] if _stats["t0"] else 0.0
            # Summary line at INFO only for error statuses or WARN
            # worthy conditions; otherwise DEBUG. Success + streaming
            # bodies are the common case and don't need to spam the log.
            _log = (logger.warning
                    if _state["status"] >= 400 or _stats["bytes"] == 0
                    else logger.debug)
            _log("%s END bytes=%d chunks=%d elapsed=%.2fs status=%d",
                 _log_tag, _stats["bytes"], _stats["chunks"],
                 _elapsed, _state["status"])
            _done.set()
            _queue_event.set()

    # Run the fetch in a background thread so we can start streaming
    # the response back as soon as the first chunk arrives.
    def _run_fetch():
        try:
            svc.http_fetch_stream(
                url=target_url, method=method,
                headers=fwd_headers, body=pending_req.body,
                on_output=_on_chunk,
            )
        except Exception as e:
            logger.warning("relay-proxy fetch failed: %s", e)
            _state["error"] = str(e)
            _done.set()
            _queue_event.set()
        finally:
            _started.set()  # ensure we unblock the main thread

    threading.Thread(target=_run_fetch, daemon=True,
                      name=f"relay-proxy-{relay_id[:8]}").start()

    # Wait for the first chunk (or error). NO timeout — the only
    # legitimate end conditions are the WS layer signalling _done
    # (relay disconnect, fetch error) or a normal first chunk. Any
    # arbitrary timeout here would silently break long prompt
    # processing on slow LLM backends.
    _started.wait()
    if _state["error"] and not _state["headers"]:
        pending_req.complete(502,
                             {"Content-Type": "application/json"},
                             json.dumps({"error": _state["error"]}).encode())
        return

    # Generator that yields queued chunks until end
    def _stream():
        while True:
            with _queue_lock:
                chunks = _queue[:]
                _queue.clear()
            for c in chunks:
                yield c
            if _done.is_set():
                with _queue_lock:
                    chunks = _queue[:]
                    _queue.clear()
                for c in chunks:
                    yield c
                return
            _queue_event.clear()
            # No inter-chunk timeout. We block until either a new chunk
            # arrives (event set by _on_chunk) or the relay signals end
            # (_done set, which also wakes us via _queue_event.set()
            # in the 'end' / error branches of _on_chunk / _run_fetch).
            _queue_event.wait()

    pending_req.complete_stream(_state["status"], _state["headers"], _stream())


def _register_routes(http_svc) -> None:
    """Idempotent route registration on the shared HTTP listener.

    The route is declared public=True (skips auth gateway) but
    private_only=True (rejects external IPs) — the ephemeral token in
    the URL is the actual credential.
    """
    routes = http_svc.get_routes()
    if any(r.get("owner") == _ROUTE_OWNER for r in routes):
        return
    pattern = "/relay-proxy/{relay_id}/{token}/{rest+}"
    for method in ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
        http_svc.register_route(
            method, pattern, _ROUTE_OWNER,
            callback=_relay_proxy_handler,
            public=True, private_only=True,
        )
    logger.info("Relay HTTP proxy routes registered (%s)", pattern)


class ServeRelayProxyTask(BaseTask):
    """Register the /relay-proxy/... route on the shared HTTP listener.

    The route is registered at task initialization — it runs outside the
    FlowFile pipeline because the handler streams directly back to the
    client via PendingRequest.complete_stream().
    """

    TYPE = "serveRelayProxy"
    VERSION = "1.0.0"
    NAME = "Serve Relay Proxy"
    DESCRIPTION = "Expose /relay-proxy/ for LLM calls routed through the user's relay"
    ICON = "share"

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "service_id": {
                "type": "string", "required": True,
                "description": "ID of the HTTPListenerService to register on",
            },
        }

    def initialize(self) -> None:
        service_id = self.config.get("service_id", "")
        svc = self.get_service(service_id) if service_id else _get_http_listener()
        if svc is None:
            logger.warning("serveRelayProxy: no HTTP listener — routes not registered")
            return
        try:
            svc.ensure_connected()
            _register_routes(svc)
        except Exception as e:
            logger.error("serveRelayProxy: route registration failed: %s", e)

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        # Route is registered at init; nothing per-flowfile.
        return [flowfile]


TaskFactory.register(ServeRelayProxyTask)

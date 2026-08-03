"""Relay HTTP Proxy Task — expose LLM calls through the user's relay.

Registers a route `ANY /relay-proxy/<relay_id>/<token>/...` on the shared
HTTP listener. Incoming requests are forwarded to the relay via its
WebSocket connection; the relay executes an http_fetch on the user's
machine and streams the response back.

Security:
  - The token in the URL is an ephemeral (~10 min) credential bound to
    (user_id, relay_id). See core/relay_proxy_auth.py.
  - Only requests from private IPs (RFC 1918 / localhost) are accepted
    even if the token is valid. Generated URLs use the listener's private
    address, never its public hostname.
  - The token and route bypass the auth gateway because the CC container
    and server-side providers have no HTTP session to carry cookies.
"""

import json
import logging
from typing import Any, Dict, List

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
    """Handle /relay-proxy/<relay_id>/<token>/[l|c/][s/]<host>:<port>/<path>."""
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

    src_ip = pending_req.remote_addr or ""
    if not is_private_ip(src_ip):
        logger.warning("relay-proxy: rejected request from public IP %s relay=%s", src_ip, relay_id)
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

    # Parse target from rest: [l|c/][s/]host:port/path. Older generated URLs
    # omitted l/c and used the host helper by default, so keep local=True.
    target_local = True
    target_scheme = "http"
    while True:
        if rest.startswith("l/"):
            target_local = True
            rest = rest[2:]
            continue
        if rest.startswith("c/"):
            target_local = False
            rest = rest[2:]
            continue
        if rest.startswith("s/"):
            target_scheme = "https"
            rest = rest[2:]
            continue
        break
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
    logger.info(
        "relay-proxy access src=%s method=%s relay=%s user=%s conv=%s target=%s local=%s body=%dB",
        pending_req.remote_addr or "", method, relay_id, user_id, conv_id,
        target_url, target_local, len(pending_req.body or b""),
    )
    logger.debug(
        "%s IN %s → target=%s local=%s body=%dB",
        _log_tag, method, target_url, target_local,
        len(pending_req.body or b""))

    # Forward headers (minus hop-by-hop and Host)
    _drop = {"host", "connection", "content-length", "transfer-encoding",
             "cookie"}
    fwd_headers = {k: v for k, v in pending_req.headers.items()
                   if k.lower() not in _drop}

    from services._relay_http_response import RelayHttpResponseStream
    stream = RelayHttpResponseStream.for_fetch(
        svc, url=target_url, method=method, headers=fwd_headers,
        body=pending_req.body, local=target_local,
        label=f"relay-proxy-{relay_id[:8]}",
    ).start()
    # No arbitrary response timeout: long-running local model requests are
    # legitimate. The relay transport itself reports disconnects and errors.
    stream.wait_ready()
    if stream.error:
        stream.discard()
        logger.warning("relay-proxy fetch failed: %s", stream.error)
        pending_req.complete(502,
                             {"Content-Type": "application/json"},
                             json.dumps({"error": stream.error}).encode())
        return
    logger.debug("%s upstream response started status=%d", _log_tag, stream.status)
    pending_req.complete_stream(
        stream.status, stream.headers, stream.iter_bytes())


def _register_routes(http_svc) -> None:
    """Idempotent route registration on the shared HTTP listener.

    The route is declared public=True so clients without a browser session can
    call it, private_only=True so it is not reachable from the internet, and
    gateway_exempt=True so the human private gateway does not return HTML to
    non-browser clients. The ephemeral token in the URL is the credential.
    """
    pattern = "/relay-proxy/{relay_id}/{token}/{rest+}"
    for method in ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
        http_svc.register_route(
            method, pattern, _ROUTE_OWNER,
            callback=_relay_proxy_handler,
            public=True, private_only=True, gateway_exempt=True,
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

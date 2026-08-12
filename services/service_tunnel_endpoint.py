"""FRP server-plugin endpoint for service tunnel authorization."""

import json
import os

from core.service_tunnel_authorizer import authorize


_ROUTE_OWNER = "_service_tunnel_authorizer"
_ROUTE_PATH = "/internal/service-tunnels/frp"
_MAX_BODY_BYTES = 256 * 1024


def _handle_request(request) -> None:
    """Validate one FRP plugin request and complete the pending HTTP response."""
    headers = {"Content-Type": "application/json", "Cache-Control": "no-store"}
    body = request.body or b""
    if len(body) > _MAX_BODY_BYTES:
        request.complete(
            413, headers,
            b'{"reject":true,"reject_reason":"Invalid service tunnel request","unchange":true}')
        return
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        decision = {
            "reject": True,
            "reject_reason": "Invalid service tunnel request",
            "unchange": True,
        }
    else:
        signing_key = str(
            os.environ.get("PAWFLOW_SERVICE_TUNNEL_SIGNING_KEY") or "").strip()
        if not signing_key:
            request.complete(
                503, headers,
                b'{"reject":true,"reject_reason":"Service tunnels unavailable","unchange":true}')
            return
        decision = authorize(payload, signing_key)
    request.complete(
        200, headers,
        json.dumps(decision, separators=(",", ":")).encode("utf-8"))


def register_service_tunnel_route(listener) -> bool:
    """Register the idempotent private FRP callback on one HTTP listener."""
    if not str(os.environ.get("PAWFLOW_SERVICE_TUNNEL_SIGNING_KEY") or "").strip():
        return False
    listener.register_route(
        "POST", _ROUTE_PATH, _ROUTE_OWNER, callback=_handle_request,
        public=True, private_only=True)
    return True


def ensure_service_tunnel_route() -> bool:
    """Install the FRP callback on the running main listener when configured."""
    if not str(os.environ.get("PAWFLOW_SERVICE_TUNNEL_SIGNING_KEY") or "").strip():
        return False

    from services.http_listener_service import HTTPListenerService

    listener = next(iter(HTTPListenerService.all_instances().values()), None)
    if listener is None:
        raise RuntimeError("No HTTP listener is available for service tunnels")
    return register_service_tunnel_route(listener)

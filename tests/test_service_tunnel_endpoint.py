"""FRP service-tunnel HTTP endpoint tests."""

import json

from services import service_tunnel_endpoint as endpoint


class Request:
    def __init__(self, body=b""):
        self.body = body
        self.completed = None

    def complete(self, status, headers, body):
        self.completed = (status, headers, body)


class Listener:
    def __init__(self):
        self.routes = []

    def register_route(self, method, pattern, owner, **kwargs):
        self.routes.append((method, pattern, owner, kwargs))


def test_route_is_private_public_and_disabled_without_signing_key(monkeypatch):
    listener = Listener()
    monkeypatch.delenv("PAWFLOW_SERVICE_TUNNEL_SIGNING_KEY", raising=False)
    assert endpoint.register_service_tunnel_route(listener) is False
    assert endpoint.ensure_service_tunnel_route() is False
    assert listener.routes == []

    monkeypatch.setenv("PAWFLOW_SERVICE_TUNNEL_SIGNING_KEY", "signing")
    assert endpoint.register_service_tunnel_route(listener) is True
    method, path, owner, kwargs = listener.routes[0]
    assert (method, path, owner) == (
        "POST", "/internal/service-tunnels/frp",
        "_service_tunnel_authorizer")
    assert kwargs["public"] is True
    assert kwargs["private_only"] is True


def test_handler_delegates_json_and_returns_frp_decision(monkeypatch):
    monkeypatch.setenv("PAWFLOW_SERVICE_TUNNEL_SIGNING_KEY", "signing")
    seen = []
    monkeypatch.setattr(
        endpoint, "authorize",
        lambda payload, key: seen.append((payload, key)) or {
            "reject": False, "unchange": True})

    request = Request(b'{"version":"0.1.0","op":"Login","content":{}}')
    endpoint._handle_request(request)

    assert seen == [({
        "version": "0.1.0", "op": "Login", "content": {}}, "signing")]
    assert request.completed[0] == 200
    assert request.completed[1]["Cache-Control"] == "no-store"
    assert json.loads(request.completed[2]) == {
        "reject": False, "unchange": True}


def test_handler_fails_closed_for_malformed_oversized_and_missing_key(monkeypatch):
    malformed = Request(b"{")
    endpoint._handle_request(malformed)
    assert malformed.completed[0] == 200
    assert json.loads(malformed.completed[2])["reject"] is True

    oversized = Request(b"x" * (endpoint._MAX_BODY_BYTES + 1))
    endpoint._handle_request(oversized)
    assert oversized.completed[0] == 413

    monkeypatch.delenv("PAWFLOW_SERVICE_TUNNEL_SIGNING_KEY", raising=False)
    unavailable = Request(b"{}")
    endpoint._handle_request(unavailable)
    assert unavailable.completed[0] == 503
    assert json.loads(unavailable.completed[2])["reject"] is True

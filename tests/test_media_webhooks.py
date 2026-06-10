"""Media provider webhook delivery."""

import json

import pytest

from core import ServiceError
from core.handlers.media import ImageGenerationHandler
from services.media_webhook_registry import MediaWebhookRegistry
from services.pixazo_image_service import PixazoImageService


class _FakeRequest:
    def __init__(self, body):
        self.body = body
        self.completed = None

    def complete(self, status, headers, body):
        self.completed = (status, headers, body)


class _FakeListener:
    def __init__(self):
        self.routes = []
        self.unregistered = []

    def register_route(self, method, pattern, owner_id, callback,
                       ws_handler=None, public=False, private_only=False,
                       gateway_exempt=False):
        self.routes.append({
            "method": method,
            "pattern": pattern,
            "owner_id": owner_id,
            "callback": callback,
            "public": public,
            "gateway_exempt": gateway_exempt,
        })

    def unregister_routes(self, owner_id):
        self.unregistered.append(owner_id)


def test_media_webhook_registry_builds_public_prefixed_route(monkeypatch):
    listener = _FakeListener()
    monkeypatch.setattr(
        "services.http_listener_service.HTTPListenerService.all_instances",
        lambda: {9090: listener},
    )

    ticket = MediaWebhookRegistry.instance().register(
        "Pixazo", "https://webchat.example.org/chat/")
    try:
        assert ticket.url.startswith(
            "https://webchat.example.org/chat/webhooks/media/pixazo/")
        assert listener.routes[0]["pattern"] == ticket.route_path
        assert listener.routes[0]["public"] is True
        # The callback comes from the provider's public egress, so the route
        # must bypass the private gateway challenge — otherwise the POST gets
        # the Matrix page and the waiting job times out with no error.
        assert listener.routes[0]["gateway_exempt"] is True
    finally:
        ticket.close()

    assert listener.unregistered


def test_media_webhook_registry_rejects_localhost():
    with pytest.raises(ServiceError, match="localhost"):
        MediaWebhookRegistry.instance().register("pixazo", "http://localhost:9090")


def test_pixazo_webhook_mode_sends_header_and_waits_for_callback(monkeypatch):
    listener = _FakeListener()
    monkeypatch.setattr(
        "services.http_listener_service.HTTPListenerService.all_instances",
        lambda: {9090: listener},
    )
    svc = PixazoImageService({
        "api_key": "key",
        "model": "nano-banana-pro",
        "poll_interval": 0,
        "timeout": 5,
        "use_webhook": True,
    })
    svc._create_connection = lambda: {"ready": True}  # type: ignore[method-assign]
    svc.set_callback_base_url("https://webchat.example.org")

    captured_headers = {}

    def _post(endpoint, body, **kwargs):
        captured_headers.update(kwargs.get("extra_headers") or {})
        assert listener.routes
        callback = listener.routes[-1]["callback"]
        req = _FakeRequest(json.dumps({
            "status": "completed",
            "output": {"media_url": ["https://cdn.example/image.png"]},
        }).encode())
        callback(req)
        assert req.completed[0] == 200
        return {"request_id": "rid", "status": "QUEUED"}

    svc._post = _post  # type: ignore[assignment]
    svc._get_url = lambda _url: (_ for _ in ()).throw(AssertionError("polling used"))  # type: ignore[assignment]
    svc._download_image = lambda url: (b"PNG", "image/png")  # type: ignore[assignment]

    out = svc.generate(prompt="robot")

    assert out["image_bytes"] == b"PNG"
    assert out["source_url"] == "https://cdn.example/image.png"
    assert captured_headers["X-Webhook-URL"].startswith(
        "https://webchat.example.org/webhooks/media/pixazo/")
    assert listener.unregistered


def test_pixazo_webhook_mode_surfaces_ack_error_without_waiting(monkeypatch):
    listener = _FakeListener()
    monkeypatch.setattr(
        "services.http_listener_service.HTTPListenerService.all_instances",
        lambda: {9090: listener},
    )
    svc = PixazoImageService({
        "api_key": "key",
        "model": "nano-banana-pro",
        "poll_interval": 0,
        "timeout": 5,
        "use_webhook": True,
    })
    svc._create_connection = lambda: {"ready": True}  # type: ignore[method-assign]
    svc.set_callback_base_url("https://webchat.example.org")

    # The generate POST acks 200 but already reports a failure.
    svc._post = lambda endpoint, body, **kwargs: {  # type: ignore[assignment]
        "status": "failed",
        "error": "URL must be a valid HTTP or HTTPS URL",
    }
    # If the fix regresses, the code would block on the callback instead of
    # raising — make that an explicit failure.
    svc._wait_webhook = lambda *a, **k: (_ for _ in ()).throw(  # type: ignore[assignment]
        AssertionError("waited for callback despite an ack-level error"))

    with pytest.raises(ServiceError, match="valid HTTP or HTTPS URL"):
        svc.generate(prompt="robot")
    # The one-shot webhook route is still torn down.
    assert listener.unregistered


class _FakeRouteEntry:
    def __init__(self, public=False, private_only=False, gateway_exempt=False):
        self.public = public
        self.private_only = private_only
        self.gateway_exempt = gateway_exempt


class _FakeRegistry:
    def __init__(self, entry):
        self._entry = entry

    def match(self, command, path):
        return (self._entry, {}) if self._entry is not None else None


class _FakeServer:
    def __init__(self, entry):
        self._route_registry = _FakeRegistry(entry)


class _FakeWFile:
    def write(self, _data):
        pass

    def flush(self):
        pass


class _FakeGatewayHandler:
    def __init__(self, entry, *, ip="203.0.113.7"):
        self.server = _FakeServer(entry)
        self.command = "POST"
        self.path = "/webhooks/media/pixazo/sometoken"
        self.headers = {}
        self.client_address = (ip, 4321)
        self.wfile = _FakeWFile()
        self.responses = []

    def send_response(self, code):
        self.responses.append(code)

    def send_header(self, *_args):
        pass

    def end_headers(self):
        pass


def test_gateway_exempt_route_bypasses_private_gateway_for_public_ip():
    from services import private_gateway

    entry = _FakeRouteEntry(public=True, gateway_exempt=True)
    handler = _FakeGatewayHandler(entry)
    # enabled gateway + a public-internet client IP (the provider egress).
    handled = private_gateway._check_request_inner(
        handler, {"enabled": True, "secret_refs": ""})
    # False == 'let it proceed to the route'; the challenge page must NOT fire.
    assert handled is False
    assert handler.responses == []


def test_public_only_route_is_still_challenged_for_public_ip():
    from services import private_gateway

    # public=True but NOT gateway_exempt: the old behaviour that served the
    # Matrix page to the provider callback and caused the silent timeout.
    entry = _FakeRouteEntry(public=True, gateway_exempt=False)
    handler = _FakeGatewayHandler(entry)
    handled = private_gateway._check_request_inner(
        handler, {"enabled": True, "secret_refs": ""})
    assert handled is True
    assert handler.responses  # a challenge/redirect response was sent


def test_pixazo_webhook_mode_falls_back_to_polling_when_callback_never_arrives(monkeypatch):
    listener = _FakeListener()
    monkeypatch.setattr(
        "services.http_listener_service.HTTPListenerService.all_instances",
        lambda: {9090: listener},
    )
    svc = PixazoImageService({
        "api_key": "key",
        "model": "nano-banana-pro",
        "poll_interval": 0,
        "timeout": 5,
        "use_webhook": True,
    })
    svc._create_connection = lambda: {"ready": True}  # type: ignore[method-assign]
    svc.set_callback_base_url("https://webchat.example.org")

    # The ack hands us a polling_url but the provider never POSTs the webhook.
    svc._post = lambda endpoint, body, **kwargs: {  # type: ignore[assignment]
        "request_id": "rid", "status": "QUEUED",
        "polling_url": "https://gw/v2/requests/status/rid",
    }
    polls = {"n": 0}

    def _get_url(url):
        polls["n"] += 1
        return {"status": "completed",
                "output": {"media_url": ["https://cdn.example/p.png"]}}

    svc._get_url = _get_url  # type: ignore[assignment]
    svc._download_image = lambda url: (b"PNG", "image/png")  # type: ignore[assignment]

    out = svc.generate(prompt="robot")

    # Polling rescued the call instead of hanging until the 5s timeout.
    assert out["source_url"] == "https://cdn.example/p.png"
    assert out["image_bytes"] == b"PNG"
    assert polls["n"] >= 1
    assert listener.unregistered


def test_pixazo_webhook_callback_wins_over_polling_when_it_arrives(monkeypatch):
    listener = _FakeListener()
    monkeypatch.setattr(
        "services.http_listener_service.HTTPListenerService.all_instances",
        lambda: {9090: listener},
    )
    svc = PixazoImageService({
        "api_key": "key",
        "model": "nano-banana-pro",
        "poll_interval": 0,
        "timeout": 5,
        "use_webhook": True,
    })
    svc._create_connection = lambda: {"ready": True}  # type: ignore[method-assign]
    svc.set_callback_base_url("https://webchat.example.org")

    def _post(endpoint, body, **kwargs):
        # Fire the callback synchronously, and also hand back a polling_url.
        callback = listener.routes[-1]["callback"]
        req = _FakeRequest(json.dumps({
            "status": "completed",
            "output": {"media_url": ["https://cdn.example/webhook.png"]},
        }).encode())
        callback(req)
        return {"request_id": "rid", "status": "QUEUED",
                "polling_url": "https://gw/v2/requests/status/rid"}

    svc._post = _post  # type: ignore[assignment]
    # If polling is consulted at all the webhook result was ignored — fail loud.
    svc._get_url = lambda _url: (_ for _ in ()).throw(  # type: ignore[assignment]
        AssertionError("polled despite an available webhook result"))
    svc._download_image = lambda url: (b"PNG", "image/png")  # type: ignore[assignment]

    out = svc.generate(prompt="robot")

    assert out["source_url"] == "https://cdn.example/webhook.png"
    assert listener.unregistered


def test_image_handler_passes_callback_base_url_to_service():
    class _Service:
        def __init__(self):
            self.callback_base_url = ""

        def set_callback_base_url(self, base_url):
            self.callback_base_url = base_url

        def generate(self, **_kwargs):
            return {"image_bytes": b"PNG", "content_type": "image/png"}

    svc = _Service()
    handler = ImageGenerationHandler()
    handler.set_base_url("https://webchat.example.org")
    handler.set_service_resolver(lambda: (svc, ""))

    result = handler.execute({"prompt": "test"})

    assert result.startswith("Image generated:")
    assert svc.callback_base_url == "https://webchat.example.org"

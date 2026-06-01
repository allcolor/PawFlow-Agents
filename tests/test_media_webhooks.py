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
                       ws_handler=None, public=False, private_only=False):
        self.routes.append({
            "method": method,
            "pattern": pattern,
            "owner_id": owner_id,
            "callback": callback,
            "public": public,
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

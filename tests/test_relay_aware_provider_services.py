"""Relay-aware URL support for configurable provider services."""

import json
import base64

from services import http_listener_service as _hl_mod
from services.http_client_service import HTTPClientService
from services.openai_image_service import OpenAIImageService


class _Resp:
    def __init__(self, body, content_type="application/json"):
        self._body = body
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self._body


class _Listener:
    is_ssl = False
    public_hostname = ""


def _relay(monkeypatch):
    monkeypatch.setattr(_hl_mod, "_instances", {9090: _Listener()})
    monkeypatch.setattr("core.relay_proxy_auth.issue_token", lambda user_id, relay_id: "tok")
    monkeypatch.setattr("core.relay_proxy_url.get_host_ip", lambda: "10.0.0.2")
    monkeypatch.setattr("core.relay_bindings.get_default", lambda cid, agent="": "relay1")


def test_openai_image_service_uses_relay_aware_base_url(monkeypatch):
    _relay(monkeypatch)
    calls = []

    def fake_urlopen(req, timeout=0):
        calls.append(req.full_url)
        if req.full_url.endswith("/images/generations"):
            return _Resp(json.dumps({"data": [{"url": "https://cdn.example/i.png"}]}).encode())
        return _Resp(b"PNG", "image/png")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    svc = OpenAIImageService({
        "api_key": "sk-test",
        "base_url": "http://${conv.relay}/localhost:8080/v1",
    })
    svc.set_runtime_context(user_id="alice", conversation_id="conv1")

    out = svc.generate(prompt="cat")

    assert out["image_bytes"] == b"PNG"
    assert calls[0] == "http://10.0.0.2:9090/relay-proxy/relay1/tok/localhost:8080/v1/images/generations"


def test_openai_image_service_handles_gpt_image_base64(monkeypatch):
    bodies = []

    def fake_urlopen(req, timeout=0):
        bodies.append(json.loads(req.data.decode("utf-8")))
        payload = base64.b64encode(b"PNG").decode("ascii")
        return _Resp(json.dumps({"data": [{"b64_json": payload}]}).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    svc = OpenAIImageService({"api_key": "sk-test", "model": "gpt-image-1"})

    out = svc.generate(prompt="cat", width=1536, height=1024, output_format="png")

    assert out == {"image_bytes": b"PNG", "content_type": "image/png"}
    assert bodies[0]["model"] == "gpt-image-1"
    assert bodies[0]["size"] == "1536x1024"
    assert bodies[0]["output_format"] == "png"
    assert "response_format" not in bodies[0]


def test_http_client_service_resolves_relay_shaped_urls(monkeypatch):
    _relay(monkeypatch)
    svc = HTTPClientService({"base_url": "http://${conv.relay}/localhost:3000/api"})
    svc.set_runtime_context(user_id="alice", conversation_id="conv1")

    assert svc._build_url("/items") == "http://10.0.0.2:9090/relay-proxy/relay1/tok/localhost:3000/api/items"

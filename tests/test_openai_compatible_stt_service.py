"""OpenAI-compatible STT service tests."""

import json

import pytest

from core import ServiceError
from services import http_listener_service as _hl_mod
from services.openai_compatible_stt_service import OpenAICompatibleSTTService


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


def test_openai_compatible_stt_posts_openai_transcription_multipart(monkeypatch):
    captured = {}

    monkeypatch.setattr(_hl_mod, "_instances", {9090: _Listener()})
    monkeypatch.setattr("core.relay_proxy_auth.issue_token", lambda user_id, relay_id, conv_id="": "tok")
    monkeypatch.setattr("core.relay_proxy_url.get_host_ip", lambda: "10.0.0.2")

    def fake_urlopen(req, timeout=0, context=None):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["body"] = req.data
        return _Resp(json.dumps({"text": "bonjour", "language": "fr"}).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    svc = OpenAICompatibleSTTService({
        "base_url": "https://${conv.relay}/localhost:1234/v1",
        "api_key": "",
        "model": "whisper-large-v3-turbo",
    })
    svc.set_runtime_context(user_id="alice", conversation_id="conv1")
    monkeypatch.setattr("core.relay_bindings.get_default", lambda cid, agent="": "relay1")

    result = svc.transcribe(
        audio_bytes=b"audio", mime_type="audio/webm", language="fr",
        filename="speech.webm")

    assert result["text"] == "bonjour"
    assert captured["url"] == "http://10.0.0.2:9090/relay-proxy/relay1/tok/s/localhost:1234/v1/audio/transcriptions"
    assert "Authorization" not in captured["headers"]
    assert b'name="file"; filename="speech.webm"' in captured["body"]
    assert b'name="model"' in captured["body"]
    assert b"whisper-large-v3-turbo" in captured["body"]


def test_openai_compatible_stt_supports_openrouter_json_protocol(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=0, context=None):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _Resp(json.dumps({
            "text": "hello",
            "usage": {"seconds": 2.5},
        }).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    svc = OpenAICompatibleSTTService({
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "or-key",
        "model": "openai/whisper-1",
        "provider_options": '{"groq":{"prompt":"Expected vocabulary"}}',
    })

    result = svc.transcribe(
        audio_bytes=b"audio", mime_type="audio/wav", filename="speech.wav",
        language="en")

    assert result["text"] == "hello"
    assert result["duration"] == 2.5
    assert captured["url"] == "https://openrouter.ai/api/v1/audio/transcriptions"
    assert captured["headers"]["Authorization"] == "Bearer or-key"
    assert captured["body"] == {
        "model": "openai/whisper-1",
        "input_audio": {"data": "YXVkaW8=", "format": "wav"},
        "language": "en",
        "provider": {"options": {"groq": {"prompt": "Expected vocabulary"}}},
    }


def test_openai_compatible_stt_requires_valid_base_url():
    svc = OpenAICompatibleSTTService({"base_url": "localhost:1234"})

    with pytest.raises(ServiceError, match="invalid OpenAI-compatible STT base_url"):
        svc.connect()


def test_openai_compatible_stt_blocks_private_base_url_by_default():
    svc = OpenAICompatibleSTTService({"base_url": "http://169.254.169.254/latest"})

    with pytest.raises(ServiceError, match="private/local network"):
        svc.connect()


def test_openai_compatible_stt_allows_private_base_url_with_explicit_opt_in():
    svc = OpenAICompatibleSTTService({
        "base_url": "http://127.0.0.1:1234/v1",
        "allow_private_base_url": True,
    })

    svc.connect()

    assert svc.base_url == "http://127.0.0.1:1234/v1"


def test_openai_compatible_stt_blocks_public_host_resolving_private(monkeypatch):
    def fake_getaddrinfo(*_args, **_kwargs):
        return [(None, None, None, "", ("10.0.0.5", 443))]

    monkeypatch.setattr("socket.getaddrinfo", fake_getaddrinfo)
    svc = OpenAICompatibleSTTService({"base_url": "https://stt.example.test/v1"})

    with pytest.raises(ServiceError, match="resolves to a private/local"):
        svc.connect()


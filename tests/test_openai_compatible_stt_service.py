"""OpenAI-compatible STT service tests."""

import json

import pytest

from core import ServiceError
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


def test_openai_compatible_stt_posts_openai_transcription_multipart(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["body"] = req.data
        return _Resp(json.dumps({"text": "bonjour", "language": "fr"}).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    svc = OpenAICompatibleSTTService({
        "base_url": "https://${convrelay}/localhost:1234/v1",
        "api_key": "",
        "model": "whisper-large-v3-turbo",
    })

    result = svc.transcribe(
        audio_bytes=b"audio", mime_type="audio/webm", language="fr",
        filename="speech.webm")

    assert result["text"] == "bonjour"
    assert captured["url"] == "https://${convrelay}/localhost:1234/v1/audio/transcriptions"
    assert "Authorization" not in captured["headers"]
    assert b'name="file"; filename="speech.webm"' in captured["body"]
    assert b'name="model"' in captured["body"]
    assert b"whisper-large-v3-turbo" in captured["body"]


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


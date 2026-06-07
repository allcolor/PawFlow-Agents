"""OpenAI-compatible TTS service tests."""

import json

import pytest

from core import ServiceError, ServiceFactory
from services.openai_compatible_tts_service import OpenAICompatibleTTSService


class _Resp:
    def __init__(self, body, content_type="audio/mpeg"):
        self._body = body
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self._body


def test_openai_compatible_tts_is_registered():
    assert ServiceFactory.get("openaiCompatibleTTS") is OpenAICompatibleTTSService


def test_register_all_services_reregisters_missing_cached_builtin_service_type():
    import services.openai_compatible_tts_service  # noqa: F401
    import tasks

    tasks._register_all_services()
    ServiceFactory._services.pop("openaiCompatibleTTS", None)

    tasks._register_all_services()

    assert "openaiCompatibleTTS" in ServiceFactory.list_types()


def test_media_discovery_returns_installed_openai_compatible_tts_after_late_import(monkeypatch, tmp_path):
    import core.service_registry as service_registry_mod
    from core.service_registry import SCOPE_USER, ServiceRegistry
    from services.base_tts import BaseTTSService
    from tasks.ai.agent_loop import AgentLoopTask

    ServiceRegistry.reset()
    monkeypatch.setattr(
        service_registry_mod,
        "_global_services_dir",
        lambda: tmp_path / "global_services",
    )
    monkeypatch.setattr(
        service_registry_mod,
        "_user_services_dir",
        lambda: tmp_path / "user_services",
    )
    reg = ServiceRegistry.get_instance()
    reg.install(
        SCOPE_USER,
        "alice",
        "openai_tts",
        "openaiCompatibleTTS",
        config={"api_key": "", "response_format": "wav"},
        enabled=True,
    )
    ServiceFactory._services.pop("openaiCompatibleTTS", None)

    services = AgentLoopTask({})._discover_media_services(
        "alice", BaseTTSService, "")

    assert any(sid == "openai_tts" for sid, _stype, _scope in services)
    ServiceRegistry.reset()


def test_openai_compatible_tts_posts_openai_speech_json(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _Resp(b"mp3-bytes", "audio/mpeg")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    svc = OpenAICompatibleTTSService({
        "base_url": "https://api.openai.com/v1",
        "api_key": "sk-test",
        "model": "gpt-4o-mini-tts",
        "voice": "coral",
        "instructions": "Speak clearly.",
        "response_format": "wav",
        "timeout": 17,
    })

    result = svc.speak("Bonjour", speed=1.25)

    assert result == {"audio_bytes": b"mp3-bytes", "content_type": "audio/mpeg", "source_url": ""}
    assert captured["url"] == "https://api.openai.com/v1/audio/speech"
    assert captured["headers"]["Authorization"] == "Bearer sk-test"
    assert captured["body"] == {
        "model": "gpt-4o-mini-tts",
        "input": "Bonjour",
        "voice": "coral",
        "response_format": "wav",
        "instructions": "Speak clearly.",
        "speed": 1.25,
    }
    assert captured["timeout"] == 17


def test_openai_compatible_tts_supports_openrouter_model_and_provider_options(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _Resp(b"audio", "audio/mpeg")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    svc = OpenAICompatibleTTSService({
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "or-key",
        "model": "openai/gpt-4o-mini-tts-2025-12-15",
        "voice": "nova",
        "response_format": "mp3",
        "provider_options": '{"openai":{"instructions":"Speak warmly."}}',
    })

    result = svc.speak("Hello")

    assert result["audio_bytes"] == b"audio"
    assert captured["url"] == "https://openrouter.ai/api/v1/audio/speech"
    assert captured["body"] == {
        "model": "openai/gpt-4o-mini-tts-2025-12-15",
        "input": "Hello",
        "voice": "nova",
        "response_format": "mp3",
        "provider": {
            "options": {
                "openai": {"instructions": "Speak warmly."},
            },
        },
    }


def test_openai_compatible_tts_decodes_json_audio_response(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=0: _Resp(json.dumps({
            "audio_base64": "ZGF0YQ==",
            "content_type": "audio/wav",
        }).encode(), "application/json"),
    )
    svc = OpenAICompatibleTTSService({"api_key": "", "response_format": "wav"})

    result = svc.speak("hello")

    assert result["audio_bytes"] == b"data"
    assert result["content_type"] == "audio/wav"


def test_openai_compatible_tts_requires_valid_base_url():
    svc = OpenAICompatibleTTSService({"base_url": "localhost:1234"})

    with pytest.raises(ServiceError, match="invalid OpenAI-compatible TTS base_url"):
        svc.connect()


def test_openai_compatible_tts_rejects_unknown_response_format():
    with pytest.raises(ServiceError, match="unsupported response_format"):
        OpenAICompatibleTTSService({"response_format": "zip"})

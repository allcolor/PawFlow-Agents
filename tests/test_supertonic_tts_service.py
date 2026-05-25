"""Supertonic local TTS service tests."""

import urllib.error

import pytest

from core import ServiceError
from core import voice_clone_cache as _cache
from core.handlers.capabilities import SpeakHandler
from core.handlers.media import AudioGenerationHandler
from services.supertonic_tts_service import SupertonicTTSService


def test_supertonic_generate_posts_native_tts_payload(monkeypatch):
    svc = SupertonicTTSService({
        "base_url": "http://127.0.0.1:7788",
        "voice": "F1",
        "lang": "fr",
        "steps": 9,
        "speed": 1.1,
    })
    captured = {}

    def fake_post(body):
        captured.update(body)
        return b"WAV", "audio/wav"

    monkeypatch.setattr(svc, "_post_tts", fake_post)
    monkeypatch.setattr(svc, "ensure_connected", lambda: None)

    out = svc.generate(prompt="Bonjour tout le monde")

    assert out == {"audio_bytes": b"WAV", "content_type": "audio/wav", "source_url": ""}
    assert captured == {
        "text": "Bonjour tout le monde",
        "voice": "F1",
        "lang": "fr",
        "steps": 9,
        "speed": 1.1,
        "response_format": "wav",
    }


def test_supertonic_generate_allows_per_call_tts_overrides(monkeypatch):
    svc = SupertonicTTSService({})
    captured = {}
    monkeypatch.setattr(svc, "_post_tts", lambda body: captured.update(body) or (b"OGG", "audio/ogg"))
    monkeypatch.setattr(svc, "ensure_connected", lambda: None)

    out = svc.generate(
        prompt="Hello",
        voice="M4",
        lang="en",
        steps=5,
        speed=1.25,
        response_format="ogg",
        max_chunk_length=120,
    )

    assert out["audio_bytes"] == b"OGG"
    assert captured["voice"] == "M4"
    assert captured["lang"] == "en"
    assert captured["steps"] == 5
    assert captured["speed"] == 1.25
    assert captured["response_format"] == "ogg"
    assert captured["max_chunk_length"] == 120


def test_supertonic_rejects_unsupported_format():
    svc = SupertonicTTSService({})
    svc.ensure_connected = lambda: None  # type: ignore[method-assign]

    with pytest.raises(ServiceError, match="unsupported Supertonic response_format"):
        svc.generate(prompt="Hello", response_format="mp3")


def test_supertonic_http_error_includes_preview(monkeypatch):
    svc = SupertonicTTSService({})

    class _HTTPError(urllib.error.HTTPError):
        def read(self):
            return b'{"detail":"bad voice"}'

    def raise_http_error(_req, timeout=None):
        raise _HTTPError("http://127.0.0.1:7788/v1/tts", 400, "Bad Request", {}, None)

    monkeypatch.setattr("services.supertonic_tts_service.urllib.request.urlopen", raise_http_error)

    with pytest.raises(ServiceError, match="bad voice"):
        svc.generate(prompt="Hello")


def test_supertonic_connection_starts_managed_daemon(monkeypatch):
    svc = SupertonicTTSService({"startup_timeout": 1})
    ready = {"n": 0}

    class _Proc:
        stderr = None

        def __init__(self, *args, **kwargs):
            self.args = args[0]
            self.kwargs = kwargs

        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

    def fake_ready():
        ready["n"] += 1
        return ready["n"] >= 2

    monkeypatch.setattr(svc, "_server_ready", fake_ready)
    monkeypatch.setattr("services.supertonic_tts_service.subprocess.Popen", _Proc)
    monkeypatch.setattr("services.supertonic_tts_service.time.sleep", lambda _s: None)

    conn = svc._create_connection()

    assert conn["managed"] is True
    assert conn["process"].args[:3] == [__import__("sys").executable, "-c", "from supertonic.cli import main; raise SystemExit(main())"]
    assert conn["process"].args[3:6] == ["serve", "--host", "127.0.0.1"]


def test_supertonic_schema_does_not_expose_executable_override():
    schema = SupertonicTTSService({}).get_parameter_schema()
    assert "python_executable" not in schema
    assert "executable" not in "\n".join(schema)


def test_audio_handler_passes_tts_arguments_to_supertonic_service():
    handler = AudioGenerationHandler()
    svc = SupertonicTTSService({})
    calls = []
    svc.generate = lambda **kwargs: calls.append(kwargs) or {  # type: ignore[method-assign]
        "audio_bytes": b"WAV", "content_type": "audio/wav"}
    handler.set_service_resolver(lambda: (svc, None))

    from unittest.mock import patch
    with patch("core.storage_resolver.StorageResolver") as storage:
        storage.return_value.write.return_value = {"file_id": "file123"}
        result = handler.execute({
            "prompt": "Bonjour",
            "voice": "F2",
            "lang": "fr",
            "steps": 8,
            "speed": 1.05,
            "response_format": "wav",
        })

    assert calls == [{
        "prompt": "Bonjour",
        "voice": "F2",
        "lang": "fr",
        "steps": 8,
        "speed": 1.05,
        "response_format": "wav",
    }]
    assert "Audio generated" in result
    assert "file123" in result


def test_speak_handler_uses_native_tts_provider_voice(monkeypatch):
    handler = SpeakHandler()
    handler.set_user_id("u_super_speak")
    handler.set_conversation_id("c_super_speak")
    svc = SupertonicTTSService({})
    calls = []
    svc.speak = lambda **kwargs: calls.append(kwargs) or {  # type: ignore[method-assign]
        "audio_bytes": b"WAV", "content_type": "audio/wav"}
    handler.set_service_resolver(lambda: (svc, None))
    monkeypatch.setattr(_cache, "tts_find", lambda *a, **k: None)
    monkeypatch.setattr(_cache, "tts_store", lambda **kwargs: "tts123")

    result = handler.execute({
        "text": "Bonjour",
        "voice": "F2",
        "language": "fr",
        "response_format": "wav",
    })

    assert calls == [{
        "text": "Bonjour",
        "voice": "F2",
        "language": "fr",
        "response_format": "wav",
    }]
    assert "Speech synthesized" in result
    assert "tts123" in result

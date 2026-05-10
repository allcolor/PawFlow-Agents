"""Suno audio service payload handling."""

from unittest.mock import MagicMock, patch

from core.handlers.media import AudioGenerationHandler
from services.suno_audio_service import SunoAudioService


def _service(**config) -> SunoAudioService:
    cfg = {"api_key": "key", "model": "V4_5ALL", "poll_interval": 0}
    cfg.update(config)
    return SunoAudioService(cfg)


def test_suno_generate_adds_callback_url_from_runtime_base(monkeypatch):
    svc = _service()
    svc.set_callback_base_url("https://pawflow.example")
    bodies = []

    def _api_request(method, path, body=None):
        if method == "POST":
            bodies.append(body)
            return {"code": 200, "data": {"taskId": "task-1"}}
        return {
            "data": {
                "status": "complete",
                "response": {"sunoData": [{"audioUrl": "https://cdn/song.mp3"}]},
            }
        }

    monkeypatch.setattr(svc, "_api_request", _api_request)
    monkeypatch.setattr(svc, "_download_audio", lambda url: {
        "audio_bytes": b"MP3",
        "content_type": "audio/mpeg",
    })
    monkeypatch.setattr("services.suno_audio_service.time.sleep", lambda _s: None)

    out = svc.generate(prompt="bikutsi")

    assert out["audio_bytes"] == b"MP3"
    assert bodies[0]["callBackUrl"] == "https://pawflow.example/webhooks/suno/callback"


def test_suno_generate_prefers_explicit_callback_url(monkeypatch):
    svc = _service(callback_url="https://configured.example/suno")
    svc.set_callback_base_url("https://pawflow.example")
    bodies = []

    def _api_request(method, path, body=None):
        if method == "POST":
            bodies.append(body)
            return {"code": 200, "data": {"taskId": "task-1"}}
        return {
            "data": {
                "status": "complete",
                "response": {"sunoData": [{"audioUrl": "https://cdn/song.mp3"}]},
            }
        }

    monkeypatch.setattr(svc, "_api_request", _api_request)
    monkeypatch.setattr(svc, "_download_audio", lambda url: {
        "audio_bytes": b"MP3",
        "content_type": "audio/mpeg",
    })
    monkeypatch.setattr("services.suno_audio_service.time.sleep", lambda _s: None)

    svc.generate(prompt="bikutsi", callback_url="https://call.example/hook")

    assert bodies[0]["callBackUrl"] == "https://call.example/hook"
    assert "callback_url" not in bodies[0]


def test_audio_handler_passes_callback_base_url_to_audio_service():
    handler = AudioGenerationHandler()
    handler.set_base_url("https://pawflow.example")
    handler.set_service_resolver(lambda: (MagicMock(), None))
    svc = MagicMock()
    svc.generate.return_value = {"audio_bytes": b"MP3", "content_type": "audio/mpeg"}
    handler.set_service_resolver(lambda: (svc, None))

    with patch("core.storage_resolver.StorageResolver") as storage:
        storage.return_value.write.return_value = {"file_id": "file123"}
        handler.execute({"prompt": "bikutsi"})

    svc.set_callback_base_url.assert_called_once_with("https://pawflow.example")
    svc.generate.assert_called_once_with(prompt="bikutsi")


def test_audio_handler_schema_exposes_callback_url():
    schema = AudioGenerationHandler().parameters_schema

    assert "callback_url" in schema["properties"]

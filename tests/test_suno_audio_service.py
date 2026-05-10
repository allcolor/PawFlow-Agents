"""Suno audio service payload handling."""

from unittest.mock import MagicMock, patch

import pytest

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
    get_paths = []

    def _api_request(method, path, body=None):
        if method == "POST":
            bodies.append(body)
            return {"code": 200, "data": {"taskId": "task/1"}}
        get_paths.append(path)
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
    assert get_paths == ["/api/v1/generate/record-info?taskId=task%2F1"]


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


def test_audio_handler_stores_each_variation_once():
    handler = AudioGenerationHandler()
    handler.set_service_resolver(lambda: (MagicMock(), None))
    svc = MagicMock()
    svc.generate.return_value = {
        "audio_bytes": b"MP3-1",
        "content_type": "audio/mpeg",
        "variations": [
            {"audio_bytes": b"MP3-1", "content_type": "audio/mpeg", "title": "v1"},
            {"audio_bytes": b"MP3-2", "content_type": "audio/mpeg", "title": "v2"},
        ],
    }
    handler.set_service_resolver(lambda: (svc, None))

    with patch("core.storage_resolver.StorageResolver") as storage:
        storage.return_value.write.side_effect = [
            {"file_id": "file-v1"},
            {"file_id": "file-v2"},
        ]
        result = handler.execute({"prompt": "bikutsi"})

    assert storage.return_value.write.call_count == 2
    written_names = [call.args[1] for call in storage.return_value.write.call_args_list]
    assert written_names[0].endswith("_v1.mp3")
    assert written_names[1].endswith("_v2.mp3")
    assert "file-v1" in result
    assert "file-v2" in result


def test_audio_handler_stores_single_variation_once():
    handler = AudioGenerationHandler()
    svc = MagicMock()
    svc.generate.return_value = {
        "audio_bytes": b"MP3-1",
        "content_type": "audio/mpeg",
        "variations": [
            {
                "audio_bytes": b"MP3-1",
                "content_type": "audio/mpeg",
                "title": "v1",
                "variation_index": 1,
            },
        ],
    }
    handler.set_service_resolver(lambda: (svc, None))

    with patch("core.storage_resolver.StorageResolver") as storage:
        storage.return_value.write.return_value = {"file_id": "file-v1"}
        result = handler.execute({"prompt": "bikutsi"})

    storage.return_value.write.assert_called_once()
    assert storage.return_value.write.call_args.args[1].endswith("_v1.mp3")
    assert "file-v1" in result


def test_audio_handler_suffixes_explicit_path_for_variations():
    handler = AudioGenerationHandler()
    svc = MagicMock()
    svc.generate.return_value = {
        "audio_bytes": b"MP3-1",
        "content_type": "audio/mpeg",
        "variations": [
            {"audio_bytes": b"MP3-1", "content_type": "audio/mpeg"},
            {"audio_bytes": b"MP3-2", "content_type": "audio/mpeg"},
        ],
    }
    handler.set_service_resolver(lambda: (svc, None))

    with patch("core.storage_resolver.StorageResolver") as storage:
        storage.return_value.write.side_effect = [
            {"file_id": "file-v1"},
            {"file_id": "file-v2"},
        ]
        handler.execute({"prompt": "bikutsi", "path": "music/fete.mp3"})

    written_names = [call.args[1] for call in storage.return_value.write.call_args_list]
    assert written_names == ["music/fete_v1.mp3", "music/fete_v2.mp3"]


def test_audio_handler_schema_exposes_callback_url():
    schema = AudioGenerationHandler().parameters_schema

    assert "callback_url" in schema["properties"]


def test_suno_generate_raises_on_failed_status(monkeypatch):
    svc = _service()
    svc.set_callback_base_url("https://pawflow.example")

    def _api_request(method, path, body=None):
        if method == "POST":
            return {"code": 200, "data": {"taskId": "task-1"}}
        return {
            "data": {
                "status": "GENERATE_AUDIO_FAILED",
                "errorMessage": "provider rejected prompt",
            }
        }

    monkeypatch.setattr(svc, "_api_request", _api_request)
    monkeypatch.setattr("services.suno_audio_service.time.sleep", lambda _s: None)

    with pytest.raises(Exception, match="provider rejected prompt"):
        svc.generate(prompt="bikutsi")


def test_suno_generate_waits_when_record_info_data_is_null(monkeypatch):
    svc = _service()
    svc.set_callback_base_url("https://pawflow.example")
    polls = []

    def _api_request(method, path, body=None):
        if method == "POST":
            return {"code": 200, "data": {"taskId": "task-1"}}
        polls.append(path)
        if len(polls) == 1:
            return {"code": 200, "msg": "success", "data": None}
        return {
            "code": 200,
            "msg": "success",
            "data": {
                "status": "SUCCESS",
                "response": {"sunoData": [{"audioUrl": "https://cdn/song.mp3"}]},
            },
        }

    monkeypatch.setattr(svc, "_api_request", _api_request)
    monkeypatch.setattr(svc, "_download_audio", lambda url: {
        "audio_bytes": b"MP3",
        "content_type": "audio/mpeg",
    })
    monkeypatch.setattr("services.suno_audio_service.time.sleep", lambda _s: None)

    out = svc.generate(prompt="bikutsi")

    assert out["audio_bytes"] == b"MP3"
    assert len(polls) == 2


def test_suno_generate_waits_for_terminal_status_before_returning_partial_ready(monkeypatch):
    svc = _service()
    svc.set_callback_base_url("https://pawflow.example")
    polls = []
    downloads = []

    def _api_request(method, path, body=None):
        if method == "POST":
            return {"code": 200, "data": {"taskId": "task-1"}}
        polls.append(path)
        if len(polls) == 1:
            return {
                "code": 200,
                "data": {
                    "status": "RUNNING",
                    "response": {"sunoData": [{
                        "audioUrl": "https://cdn/song-a.mp3",
                        "title": "A",
                    }]},
                },
            }
        return {
            "code": 200,
            "data": {
                "status": "SUCCESS",
                "response": {"sunoData": [
                    {"audioUrl": "https://cdn/song-a.mp3", "title": "A"},
                    {"audioUrl": "https://cdn/song-b.mp3", "title": "B"},
                ]},
            },
        }

    def _download_audio(url):
        downloads.append(url)
        return {"audio_bytes": url.encode(), "content_type": "audio/mpeg"}

    monkeypatch.setattr(svc, "_api_request", _api_request)
    monkeypatch.setattr(svc, "_download_audio", _download_audio)
    monkeypatch.setattr("services.suno_audio_service.time.sleep", lambda _s: None)

    out = svc.generate(prompt="bikutsi")

    assert len(polls) == 2
    assert downloads == ["https://cdn/song-a.mp3", "https://cdn/song-b.mp3"]
    assert [v["title"] for v in out["variations"]] == ["A", "B"]
    assert [v["variation_index"] for v in out["variations"]] == [1, 2]
    assert out["audio_bytes"] == b"https://cdn/song-a.mp3"

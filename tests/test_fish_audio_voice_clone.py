"""Tests for FishAudioVoiceCloneService (zero-shot voice cloning TTS).

Network is mocked at `http.client.HTTPSConnection` and
`urllib.request.urlopen`. We assert:
  - parameter schema is exposed
  - missing api_key raises on connect
  - clone_speak requires text and a reference
  - the HTTPS POST carries the expected JSON body (auth, text, refs)
  - API errors are surfaced as ServiceError
  - `reference_audio_bytes` short-circuits the HTTP fetch
"""

import base64
import json
from unittest.mock import MagicMock, patch

import pytest

from core import ServiceError
from services.fish_audio_voice_clone_service import FishAudioVoiceCloneService


def _svc(**overrides):
    cfg = {"api_key": "k-test", "timeout": 5}
    cfg.update(overrides)
    return FishAudioVoiceCloneService(cfg)


def _mock_conn(status=200, body=b"\xff\xfbMP3DATA",
               content_type="audio/mpeg"):
    conn = MagicMock()
    resp = MagicMock()
    resp.status = status
    resp.headers = {"Content-Type": content_type}
    resp.read.return_value = body
    conn.getresponse.return_value = resp
    return conn


def test_schema_declares_api_key_and_format():
    schema = _svc().get_parameter_schema()
    assert schema["api_key"]["required"] is True
    assert schema["api_key"]["sensitive"] is True
    assert "mp3" in schema["format"]["options"]
    assert schema["model"]["default"].startswith("speech-")


def test_connect_requires_api_key():
    svc = FishAudioVoiceCloneService({})
    with pytest.raises(ServiceError, match="api_key"):
        svc.ensure_connected()


def test_clone_speak_requires_text():
    svc = _svc()
    with pytest.raises(ServiceError, match="text"):
        svc.clone_speak(text="", reference_audio_bytes=b"aa")


def test_clone_speak_requires_reference():
    svc = _svc()
    with pytest.raises(ServiceError, match="reference"):
        svc.clone_speak(text="hello")


def test_clone_speak_posts_expected_body_with_bytes():
    svc = _svc(format="mp3", latency="normal", model="speech-1.6")
    conn = _mock_conn(status=200, body=b"AUDIOBYTES")
    ref_bytes = b"REFERENCE_WAV"

    with patch("services.fish_audio_voice_clone_service."
               "http.client.HTTPSConnection", return_value=conn) as _cls:
        out = svc.clone_speak(
            text="Bonjour le monde",
            reference_audio_bytes=ref_bytes,
            reference_text="sample transcription",
            language="fr",
        )

    _cls.assert_called_once()
    conn.request.assert_called_once()
    method, path = conn.request.call_args.args[:2]
    assert method == "POST"
    assert path == "/v1/tts"

    headers = conn.request.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer k-test"
    assert headers["Content-Type"] == "application/json"

    body = json.loads(conn.request.call_args.kwargs["body"])
    assert body["text"] == "Bonjour le monde"
    assert body["format"] == "mp3"
    assert body["latency"] == "normal"
    assert body["model"] == "speech-1.6"
    assert body["language"] == "fr"
    assert len(body["references"]) == 1
    ref = body["references"][0]
    assert base64.b64decode(ref["audio"]) == ref_bytes
    assert ref["text"] == "sample transcription"

    assert out["audio_bytes"] == b"AUDIOBYTES"
    assert out["content_type"] == "audio/mpeg"
    assert out["source_url"] == ""


def test_clone_speak_fetches_reference_when_only_url_given():
    svc = _svc()
    conn = _mock_conn()
    fetched = MagicMock()
    fetched.__enter__.return_value.read.return_value = b"DOWNLOADED_WAV"
    fetched.__exit__.return_value = False

    with patch("services.fish_audio_voice_clone_service."
               "http.client.HTTPSConnection", return_value=conn), \
         patch("services.fish_audio_voice_clone_service."
               "urllib.request.urlopen", return_value=fetched) as _open:
        svc.clone_speak(
            text="hi",
            reference_audio_url="https://example.com/voice.wav",
        )

    _open.assert_called_once()
    body = json.loads(conn.request.call_args.kwargs["body"])
    assert base64.b64decode(body["references"][0]["audio"]) == b"DOWNLOADED_WAV"


def test_clone_speak_rejects_non_http_url():
    svc = _svc()
    with pytest.raises(ServiceError, match="absolute URL"):
        svc.clone_speak(text="hi",
                        reference_audio_url="fs://filestore/x/y.wav")


def test_clone_speak_surfaces_api_error():
    svc = _svc()
    conn = _mock_conn(status=401, body=b'{"error":"bad key"}')
    with patch("services.fish_audio_voice_clone_service."
               "http.client.HTTPSConnection", return_value=conn):
        with pytest.raises(ServiceError, match="401"):
            svc.clone_speak(text="hi", reference_audio_bytes=b"aa")


def test_clone_speak_empty_response_raises():
    svc = _svc()
    conn = _mock_conn(status=200, body=b"")
    with patch("services.fish_audio_voice_clone_service."
               "http.client.HTTPSConnection", return_value=conn):
        with pytest.raises(ServiceError, match="empty"):
            svc.clone_speak(text="hi", reference_audio_bytes=b"aa")


def test_invalid_format_falls_back_to_mp3():
    svc = _svc(format="flac")  # not supported
    assert svc.format == "mp3"


def test_ensure_voice_id_is_noop_for_zero_shot():
    svc = _svc()
    assert svc.ensure_voice_id("https://x/y.wav") == ""
    assert svc.delete_voice_id("whatever") is True

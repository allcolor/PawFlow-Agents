"""Tests for ElevenLabsVoiceCloneService (paradigm A: persistent voice_id).

Network is mocked at `http.client.HTTPSConnection`. We assert:
  - parameter schema exposes api_key as sensitive + required
  - connect requires api_key
  - ensure_voice_id POSTs multipart to /v1/voices/add and returns voice_id
  - delete_voice_id issues DELETE /v1/voices/{id}
  - clone_speak requires text and voice_id
  - clone_speak POSTs JSON with model+voice_settings and returns audio
  - API errors (401, etc.) are surfaced as ServiceError
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from core import ServiceError
from services.elevenlabs_voice_clone_service import ElevenLabsVoiceCloneService


def _svc(**overrides):
    cfg = {"api_key": "k-test", "timeout": 5}
    cfg.update(overrides)
    return ElevenLabsVoiceCloneService(cfg)


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
    assert schema["model_id"]["default"].startswith("eleven_")
    assert "mp3_44100_128" in schema["output_format"]["options"]


def test_connect_requires_api_key():
    svc = ElevenLabsVoiceCloneService({})
    with pytest.raises(ServiceError, match="api_key"):
        svc.ensure_connected()


def test_ensure_voice_id_posts_multipart_and_returns_id():
    svc = _svc()
    conn = _mock_conn(
        status=200,
        body=json.dumps({"voice_id": "vid-abc123"}).encode(),
        content_type="application/json",
    )
    with patch("services.elevenlabs_voice_clone_service."
               "http.client.HTTPSConnection", return_value=conn) as _cls:
        vid = svc.ensure_voice_id(
            reference_audio_bytes=b"REFERENCE_WAV",
            name="alice",
            reference_text="hello world",
        )

    assert vid == "vid-abc123"
    # Two HTTPS calls: GET /v1/user/subscription (quota pre-check) then
    # POST /v1/voices/add. The last request is the upload.
    assert _cls.call_count == 2
    method, path = conn.request.call_args.args[:2]
    assert method == "POST"
    assert path == "/v1/voices/add"

    headers = conn.request.call_args.kwargs["headers"]
    assert headers["xi-api-key"] == "k-test"
    assert headers["Content-Type"].startswith("multipart/form-data; boundary=")

    body = conn.request.call_args.kwargs["body"]
    assert b'name="name"' in body
    assert b"alice" in body
    assert b'name="files"' in body
    assert b"REFERENCE_WAV" in body


def test_ensure_voice_id_requires_bytes():
    svc = _svc()
    with pytest.raises(ServiceError, match="reference_audio_bytes"):
        svc.ensure_voice_id(name="alice")


def test_ensure_voice_id_blocks_when_quota_reached():
    """Pre-flight quota check: refuse upload when voice_slots_used >= limit."""
    svc = _svc()
    conn = _mock_conn(
        status=200,
        body=json.dumps({"voice_slots_used": 30,
                          "voice_limit": 30}).encode(),
        content_type="application/json",
    )
    with patch("services.elevenlabs_voice_clone_service."
               "http.client.HTTPSConnection", return_value=conn) as _cls:
        with pytest.raises(ServiceError, match="quota"):
            svc.ensure_voice_id(
                reference_audio_bytes=b"REFERENCE_WAV", name="alice")
    # Only the subscription GET was attempted — upload never ran.
    assert _cls.call_count == 1
    method, path = conn.request.call_args.args[:2]
    assert method == "GET"
    assert path == "/v1/user/subscription"


def test_ensure_voice_id_surfaces_missing_id():
    svc = _svc()
    conn = _mock_conn(status=200,
                      body=json.dumps({"unexpected": True}).encode(),
                      content_type="application/json")
    with patch("services.elevenlabs_voice_clone_service."
               "http.client.HTTPSConnection", return_value=conn):
        with pytest.raises(ServiceError, match="no voice_id"):
            svc.ensure_voice_id(reference_audio_bytes=b"x")


def test_delete_voice_id_issues_http_delete():
    svc = _svc()
    conn = _mock_conn(status=200, body=b'{"status":"ok"}',
                      content_type="application/json")
    with patch("services.elevenlabs_voice_clone_service."
               "http.client.HTTPSConnection", return_value=conn):
        assert svc.delete_voice_id("vid-abc") is True

    method, path = conn.request.call_args.args[:2]
    assert method == "DELETE"
    assert path == "/v1/voices/vid-abc"


def test_delete_voice_id_returns_false_on_error():
    svc = _svc()
    conn = _mock_conn(status=404, body=b'{"detail":"not found"}',
                      content_type="application/json")
    with patch("services.elevenlabs_voice_clone_service."
               "http.client.HTTPSConnection", return_value=conn):
        assert svc.delete_voice_id("vid-missing") is False


def test_delete_voice_id_empty_is_noop():
    assert _svc().delete_voice_id("") is True


def test_clone_speak_requires_text():
    svc = _svc()
    with pytest.raises(ServiceError, match="text"):
        svc.clone_speak(text="", voice_id="vid-1")


def test_clone_speak_requires_voice_id():
    svc = _svc()
    with pytest.raises(ServiceError, match="voice_id"):
        svc.clone_speak(text="hi")


def test_clone_speak_posts_json_and_returns_audio():
    svc = _svc(model_id="eleven_multilingual_v2",
               output_format="mp3_44100_128",
               stability=0.6, similarity_boost=0.8)
    conn = _mock_conn(status=200, body=b"AUDIOBYTES",
                      content_type="audio/mpeg")
    with patch("services.elevenlabs_voice_clone_service."
               "http.client.HTTPSConnection", return_value=conn):
        out = svc.clone_speak(
            text="Bonjour le monde",
            voice_id="vid-abc",
            language="fr",
        )

    method, path = conn.request.call_args.args[:2]
    assert method == "POST"
    assert path.startswith("/v1/text-to-speech/vid-abc?output_format=")
    assert "mp3_44100_128" in path

    headers = conn.request.call_args.kwargs["headers"]
    assert headers["xi-api-key"] == "k-test"
    assert headers["Content-Type"] == "application/json"

    body = json.loads(conn.request.call_args.kwargs["body"])
    assert body["text"] == "Bonjour le monde"
    assert body["model_id"] == "eleven_multilingual_v2"
    assert body["voice_settings"]["stability"] == 0.6
    assert body["voice_settings"]["similarity_boost"] == 0.8
    assert body["language_code"] == "fr"

    assert out["audio_bytes"] == b"AUDIOBYTES"
    assert out["content_type"] == "audio/mpeg"
    assert out["voice_id"] == "vid-abc"
    assert out["source_url"] == ""


def test_clone_speak_surfaces_api_error():
    svc = _svc()
    conn = _mock_conn(status=401, body=b'{"detail":"bad key"}',
                      content_type="application/json")
    with patch("services.elevenlabs_voice_clone_service."
               "http.client.HTTPSConnection", return_value=conn):
        with pytest.raises(ServiceError, match="401"):
            svc.clone_speak(text="hi", voice_id="vid-1")


def test_clone_speak_empty_response_raises():
    svc = _svc()
    conn = _mock_conn(status=200, body=b"",
                      content_type="audio/mpeg")
    with patch("services.elevenlabs_voice_clone_service."
               "http.client.HTTPSConnection", return_value=conn):
        with pytest.raises(ServiceError, match="empty"):
            svc.clone_speak(text="hi", voice_id="vid-1")


def test_invalid_output_format_falls_back():
    svc = _svc(output_format="flac_lossless")  # not supported
    assert svc.output_format == "mp3_44100_128"

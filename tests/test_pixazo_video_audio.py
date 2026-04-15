"""Pixazo video + audio services — catalog-driven dispatch.

Same mocking pattern as test_pixazo_image.py (patch _post / _get_url /
_download_media on the instance). The base class wires the three
conventions (sync / legacy_poll / polling_url) for every category, so
these tests only assert the service honours `category` filtering and
calls the right ops.
"""

import pytest

from services.pixazo_video_service import PixazoVideoService
from services.pixazo_audio_service import PixazoAudioService
from services._pixazo_base import models_for_category


def _video(model: str = "sora-video") -> PixazoVideoService:
    s = PixazoVideoService({"api_key": "k", "model": model, "poll_interval": 0})
    s._create_connection = lambda: {"ready": True}
    return s


def _audio(model: str = "minimax-music") -> PixazoAudioService:
    s = PixazoAudioService({"api_key": "k", "model": model, "poll_interval": 0})
    s._create_connection = lambda: {"ready": True}
    return s


# ── Category filtering ─────────────────────────────────────────────────


def test_video_category_contains_known_models():
    models = models_for_category("video")
    assert "sora-video" in models
    assert "runway" in models
    assert "kling-video" in models
    assert "veo" in models


def test_audio_category_contains_known_models():
    models = models_for_category("audio")
    assert "minimax-music" in models
    assert "lyria" in models
    assert "elevenlabs-v3" in models


def test_video_rejects_image_model():
    s = _video("sdxl")  # sdxl is category=image
    with pytest.raises(Exception, match="category"):
        s._model()


def test_audio_rejects_video_model():
    s = _audio("sora-video")  # sora-video is category=video
    with pytest.raises(Exception, match="category"):
        s._model()


# ── Video dispatch ─────────────────────────────────────────────────────


def test_video_text_to_video_legacy_poll():
    """Sora: POST /video/generate → id → POST /video/result until ready."""
    s = _video("sora-video")
    calls = []

    def _fake_post(ep, body):
        calls.append((ep, body))
        if ep.endswith("/video/generate"):
            return {"id": "vid-1"}
        return {"status": "completed", "video_url": "https://cdn/v.mp4"}

    s._post = _fake_post  # type: ignore[assignment]
    s._download_media = lambda u, default_mime="": (b"MP4", "video/mp4")  # type: ignore[assignment]

    out = s.generate(prompt="a robot dancing", duration=4)
    assert out["video_bytes"] == b"MP4"
    assert out["content_type"] == "video/mp4"
    assert out["source_url"] == "https://cdn/v.mp4"
    # First call must be generate, second must be result (poll)
    assert calls[0][0].endswith("/video/generate")
    assert calls[1][0].endswith("/video/result")


def test_video_image_to_video_uses_input_field():
    """Sora image_to_video: image_url goes into the body under the configured input_field."""
    s = _video("sora-video")
    captured = {}

    def _fake_post(ep, body):
        # Capture the FIRST call (the generate POST) only; the second
        # call is the poll and has no user-visible body.
        if "ep" not in captured:
            captured["ep"] = ep
            captured["body"] = body
        if ep.endswith("/i2v/generate"):
            return {"id": "vid-1"}
        return {"status": "completed", "video_url": "https://cdn/o.mp4"}

    s._post = _fake_post  # type: ignore[assignment]
    s._download_media = lambda u, default_mime="": (b"MP4", "video/mp4")  # type: ignore[assignment]

    out = s.image_to_video(prompt="make it move",
                            image_url="https://src/in.png", duration=4)
    assert out["video_bytes"] == b"MP4"
    assert captured["body"]["image_url"] == "https://src/in.png"


# ── Audio dispatch ─────────────────────────────────────────────────────


def test_audio_music_generation_legacy_poll():
    """MiniMax music: POST /getAudio → task_id → POST /getAudioResult until ready."""
    s = _audio("minimax-music")
    calls = []

    def _fake_post(ep, body):
        calls.append((ep, body))
        if ep.endswith("/getAudio"):
            return {"task_id": "t-1"}
        return {"status": "completed", "audio_url": "https://cdn/a.mp3"}

    s._post = _fake_post  # type: ignore[assignment]
    s._download_media = lambda u, default_mime="": (b"MP3", "audio/mpeg")  # type: ignore[assignment]

    out = s.generate(prompt="upbeat jazz", duration=30)
    assert out["audio_bytes"] == b"MP3"
    assert out["content_type"] == "audio/mpeg"
    assert out["source_url"] == "https://cdn/a.mp3"
    assert calls[0][0].endswith("/getAudio")
    assert calls[1][0].endswith("/getAudioResult")


def test_audio_picks_first_matching_op():
    """generate() picks the first of {music_generation,text_to_audio,text_to_music}."""
    s = _audio("minimax-music")
    # Only music_generation exists on minimax-music → should be picked.

    def _fake_post(ep, body):
        if ep.endswith("/getAudio"):
            return {"task_id": "t-1"}
        return {"status": "completed", "audio_url": "https://cdn/a.mp3"}

    s._post = _fake_post  # type: ignore[assignment]
    s._download_media = lambda u, default_mime="": (b"MP3", "audio/mpeg")  # type: ignore[assignment]
    out = s.generate(prompt="x")
    assert out["source_url"] == "https://cdn/a.mp3"


def test_audio_text_to_speech_uses_configured_input_field():
    """ElevenLabs v3 TTS: text goes into body under `text` (configured input_field)."""
    s = _audio("elevenlabs-v3")
    captured = {}

    def _fake_post(ep, body):
        if "ep" not in captured:
            captured["ep"] = ep
            captured["body"] = body
        if "eleven-v3-alpha/generate" in ep:
            return {"request_id": "r-1"}
        return {"status": "completed", "audio_url": "https://cdn/tts.mp3"}

    s._post = _fake_post  # type: ignore[assignment]
    s._download_media = lambda u, default_mime="": (b"TTS", "audio/mpeg")  # type: ignore[assignment]
    out = s.text_to_speech(text="Bonjour tout le monde")
    assert out["audio_bytes"] == b"TTS"
    assert captured["body"]["text"] == "Bonjour tout le monde"


# ── output_path resolution ─────────────────────────────────────────────


def test_output_path_dotted_resolves_nested_url():
    from services._pixazo_base import _resolve_output_path, _extract_media_url
    data = {"output": {"video_url": "https://cdn/x.mp4"}}
    assert _resolve_output_path(data, "output.video_url") == "https://cdn/x.mp4"
    data2 = {"output": {"media_url": ["https://cdn/y.mp4"]}}
    assert _extract_media_url(
        data2, output_path="output.media_url[0]") == "https://cdn/y.mp4"


def test_output_path_falls_back_when_missing():
    from services._pixazo_base import _extract_media_url
    # output_path misses, legacy videoUrl field wins.
    data = {"videoUrl": "https://cdn/fallback.mp4"}
    assert _extract_media_url(
        data, output_path="output.video_url") == "https://cdn/fallback.mp4"

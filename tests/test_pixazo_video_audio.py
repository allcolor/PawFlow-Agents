"""Pixazo video + audio services — catalog-driven dispatch.

Same mocking pattern as test_pixazo_image.py (patch _post / _get_url /
_download_media on the instance). The base class wires sync and polling_url
conventions for every category, so
these tests only assert the service honours `category` filtering and
calls the right ops.
"""

import pytest

from services.pixazo_video_service import PixazoVideoService
from services.pixazo_audio_service import PixazoAudioService
from services._pixazo_base import models_for_category


def _video(model: str = "p-video") -> PixazoVideoService:
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
    assert "p-video" in models
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
    s = _audio("p-video")  # p-video is category=video
    with pytest.raises(Exception, match="category"):
        s._model()


# ── Video dispatch ─────────────────────────────────────────────────────


def test_video_text_to_video_polling_url_dispatch():
    """P Video: POST generate → polling_url → GET it until ready.

    Pixazo's gateway returns the absolute polling URL on every async
    generate; per-model poll endpoints don't exist.
    """
    s = _video("p-video")
    calls = []

    def _fake_post(ep, body):
        calls.append((ep, body))
        return {"id": "vid-1",
                "polling_url": "https://gw/v2/requests/status/vid-1"}

    s._post = _fake_post  # type: ignore[assignment]
    s._get_url = lambda u: {  # type: ignore[assignment]
        "status": "completed", "video_url": "https://cdn/v.mp4"}
    s._download_media = lambda u, default_mime="": (b"MP4", "video/mp4")  # type: ignore[assignment]

    out = s.generate(prompt="a robot dancing", duration=4)
    assert out["video_bytes"] == b"MP4"
    assert out["source_url"] == "https://cdn/v.mp4"
    assert len(calls) == 1  # generate POST only — poll is GET
    assert calls[0][0].endswith("/p-video/generateTextToVideoRequest")


def test_video_image_to_video_uses_input_field():
    """Image-to-video sends image_url under the configured input_field."""
    s = _video("kling-3-0-image-to-video-standard")
    captured = {}

    def _fake_post(ep, body):
        captured["ep"] = ep
        captured["body"] = body
        return {"id": "vid-1",
                "polling_url": "https://gw/v2/requests/status/vid-1"}

    s._post = _fake_post  # type: ignore[assignment]
    s._get_url = lambda u: {  # type: ignore[assignment]
        "status": "completed", "video_url": "https://cdn/o.mp4"}
    s._download_media = lambda u, default_mime="": (b"MP4", "video/mp4")  # type: ignore[assignment]

    out = s.image_to_video(prompt="make it move",
                            image_url="https://src/in.png", duration=4)
    assert out["video_bytes"] == b"MP4"
    assert captured["body"]["image_url"] == "https://src/in.png"


# ── Audio dispatch ─────────────────────────────────────────────────────


def test_audio_music_generation_polling_url_dispatch():
    """MiniMax music: POST /getAudio → polling_url → GET it until ready."""
    s = _audio("minimax-music")
    calls = []

    def _fake_post(ep, body):
        calls.append((ep, body))
        return {"task_id": "t-1",
                "polling_url": "https://gw/v2/requests/status/t-1"}

    s._post = _fake_post  # type: ignore[assignment]
    s._get_url = lambda u: {  # type: ignore[assignment]
        "status": "completed", "audio_url": "https://cdn/a.mp3"}
    s._download_media = lambda u, default_mime="": (b"MP3", "audio/mpeg")  # type: ignore[assignment]

    out = s.generate(prompt="upbeat jazz", duration=30)
    assert out["audio_bytes"] == b"MP3"
    assert out["source_url"] == "https://cdn/a.mp3"
    assert len(calls) == 1
    assert calls[0][0].endswith("/getAudio")


def test_audio_picks_first_matching_op():
    """generate() picks the first of {music_generation,text_to_audio,text_to_music}."""
    s = _audio("minimax-music")

    def _fake_post(ep, body):
        return {"task_id": "t-1",
                "polling_url": "https://gw/v2/requests/status/t-1"}

    s._post = _fake_post  # type: ignore[assignment]
    s._get_url = lambda u: {  # type: ignore[assignment]
        "status": "completed", "audio_url": "https://cdn/a.mp3"}
    s._download_media = lambda u, default_mime="": (b"MP3", "audio/mpeg")  # type: ignore[assignment]
    out = s.generate(prompt="x")
    assert out["source_url"] == "https://cdn/a.mp3"


def test_audio_text_to_speech_uses_configured_input_field():
    """ElevenLabs v3 TTS: text goes into body under `text` (configured input_field)."""
    s = _audio("elevenlabs-v3")
    captured = {}

    def _fake_post(ep, body):
        captured["ep"] = ep
        captured["body"] = body
        return {"request_id": "r-1",
                "polling_url": "https://gw/v2/requests/status/r-1"}

    s._post = _fake_post  # type: ignore[assignment]
    s._get_url = lambda u: {  # type: ignore[assignment]
        "status": "completed", "audio_url": "https://cdn/tts.mp3"}
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


# ── Body shape rewriting ───────────────────────────────────────────────


def test_body_shape_flat_passthrough():
    from services._pixazo_base import _PixazoBaseService
    body = {"prompt": "x", "duration": 5}
    assert _PixazoBaseService._reshape_body(body, "flat") == body


def test_body_shape_content_array_collapses_prompt_and_image():
    """Seedance / OpenAI-style multimodal request:
    {prompt, image_url, duration} → {content: [text, image_url], duration}.
    """
    from services._pixazo_base import _PixazoBaseService
    body = {
        "prompt": "A cat on the beach",
        "image_url": "https://src/c.png",
        "duration": 5,
        "ratio": "16:9",
    }
    out = _PixazoBaseService._reshape_body(body, "content_array")
    assert out["duration"] == 5
    assert out["ratio"] == "16:9"
    assert "prompt" not in out and "image_url" not in out
    types = [c["type"] for c in out["content"]]
    assert "text" in types
    assert "image_url" in types
    text_item = next(c for c in out["content"] if c["type"] == "text")
    assert text_item["text"] == "A cat on the beach"
    img_item = next(c for c in out["content"] if c["type"] == "image_url")
    assert img_item["image_url"]["url"] == "https://src/c.png"


def test_body_shape_content_array_handles_video_and_audio_urls():
    from services._pixazo_base import _PixazoBaseService
    body = {
        "prompt": "remix this",
        "video_url": "https://v/a.mp4",
        "audio_url": "https://a/b.mp3",
    }
    out = _PixazoBaseService._reshape_body(body, "content_array")
    types = {c["type"] for c in out["content"]}
    assert types == {"text", "video_url", "audio_url"}


def test_body_shape_unknown_passes_through_with_warning(caplog):
    from services._pixazo_base import _PixazoBaseService
    body = {"prompt": "x"}
    with caplog.at_level("WARNING"):
        out = _PixazoBaseService._reshape_body(body, "doesnotexist")
    assert out == body
    assert any("unknown body_shape" in r.message for r in caplog.records)


def test_seedance_uses_content_array_body_via_invoke(monkeypatch):
    """End-to-end: seedance-2-0 has body_shape=content_array in the
    catalog → _invoke wraps the prompt before POSTing."""
    s = _video("seedance-2-0")
    captured = {}

    def _fake_post(ep, body):
        captured["body"] = body
        return {"request_id": "rid",
                "polling_url": "https://gw/v2/requests/status/rid"}

    s._post = _fake_post  # type: ignore[assignment]
    s._get_url = lambda u: {  # type: ignore[assignment]
        "status": "completed", "video_url": "https://cdn/v.mp4"}
    s._download_media = lambda u, default_mime="": (b"MP4", "video/mp4")  # type: ignore[assignment]

    out = s.generate(prompt="A cat on the beach", duration=5)
    assert out["video_bytes"] == b"MP4"
    # Body must be content-array shape, not flat
    assert "content" in captured["body"]
    assert "prompt" not in captured["body"]
    text_item = next(c for c in captured["body"]["content"]
                      if c["type"] == "text")
    assert text_item["text"] == "A cat on the beach"

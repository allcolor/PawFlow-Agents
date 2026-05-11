"""WaveSpeedAI catalog-driven media services."""

import pytest

from services._wavespeed_base import models_for_category
from services.wavespeed_audio_service import WaveSpeedAudioService
from services.wavespeed_capability_services import (
    WaveSpeed3DService, WaveSpeedLipsyncService,
)
from services.wavespeed_image_service import WaveSpeedImageService
from services.wavespeed_video_service import WaveSpeedVideoService
from services.wavespeed_voice_clone_service import WaveSpeedVoiceCloneService


def _image(model: str = "wavespeed-ai/flux-dev") -> WaveSpeedImageService:
    svc = WaveSpeedImageService({"api_key": "k", "model": model, "poll_interval": 0})
    svc._create_connection = lambda: {"ready": True}
    return svc


def _video(model: str = "wavespeed-ai/wan-2.2/image-to-video") -> WaveSpeedVideoService:
    svc = WaveSpeedVideoService({"api_key": "k", "model": model, "poll_interval": 0})
    svc._create_connection = lambda: {"ready": True}
    return svc


def _audio(model: str = "wavespeed-ai/qwen3-tts/text-to-speech") -> WaveSpeedAudioService:
    svc = WaveSpeedAudioService({"api_key": "k", "model": model, "poll_interval": 0})
    svc._create_connection = lambda: {"ready": True}
    return svc


def _voice(model: str = "wavespeed-ai/qwen3-tts/voice-clone") -> WaveSpeedVoiceCloneService:
    svc = WaveSpeedVoiceCloneService({"api_key": "k", "model": model, "poll_interval": 0})
    svc._create_connection = lambda: {"ready": True}
    return svc


def _three_d(model: str = "") -> WaveSpeed3DService:
    cfg = {"api_key": "k", "poll_interval": 0}
    if model:
        cfg["model"] = model
    svc = WaveSpeed3DService(cfg)
    svc._create_connection = lambda: {"ready": True}
    return svc


def _lipsync(model: str = "wavespeed-ai/ltx-2.3/lipsync") -> WaveSpeedLipsyncService:
    svc = WaveSpeedLipsyncService({"api_key": "k", "model": model, "poll_interval": 0})
    svc._create_connection = lambda: {"ready": True}
    return svc


def test_catalog_contains_expected_wavespeed_categories():
    assert "wavespeed-ai/flux-dev" in models_for_category("image")
    assert "wavespeed-ai/wan-2.2/image-to-video" in models_for_category("video")
    assert "wavespeed-ai/qwen3-tts/text-to-speech" in models_for_category("audio")
    assert "wavespeed-ai/qwen3-tts/voice-clone" in models_for_category("voice_clone")


def test_image_generate_submits_prediction_and_polls_result():
    svc = _image()
    captured = {}

    def fake_post(endpoint, body):
        captured["endpoint"] = endpoint
        captured["body"] = body
        return {"data": {"id": "p1", "status": "processing",
                         "urls": {"get": "https://api.wavespeed.ai/api/v3/predictions/p1/result"}}}

    svc._post = fake_post  # type: ignore[assignment]
    svc._get = lambda endpoint: {"data": {"status": "completed", "outputs": ["https://cdn/i.png"]}}  # type: ignore[assignment]
    svc._download_media = lambda url, default_mime="": (b"PNG", "image/png")  # type: ignore[assignment]

    out = svc.generate(prompt="a fox", width=1024, height=768, steps=20)
    assert out["image_bytes"] == b"PNG"
    assert captured["endpoint"] == "/wavespeed-ai/flux-dev"
    assert captured["body"]["size"] == "1024*768"
    assert captured["body"]["num_inference_steps"] == 20
    assert captured["body"]["enable_sync_mode"] is False


def test_video_image_to_video_uses_wavespeed_image_field():
    svc = _video()
    captured = {}

    def fake_post(endpoint, body):
        captured["endpoint"] = endpoint
        captured["body"] = body
        return {"data": {"id": "v1", "status": "processing",
                         "urls": {"get": "/predictions/v1/result"}}}

    svc._post = fake_post  # type: ignore[assignment]
    svc._get = lambda endpoint: {"data": {"status": "completed", "outputs": ["https://cdn/v.mp4"]}}  # type: ignore[assignment]
    svc._download_media = lambda url, default_mime="": (b"MP4", "video/mp4")  # type: ignore[assignment]

    out = svc.image_to_video(prompt="slow dolly", image_url="https://src/in.png")
    assert out["video_bytes"] == b"MP4"
    assert captured["endpoint"] == "/wavespeed-ai/wan-2.2/image-to-video"
    assert captured["body"]["image"] == "https://src/in.png"


def test_audio_text_to_speech_dispatches_voice_and_language():
    svc = _audio()
    captured = {}

    def fake_post(endpoint, body):
        captured["endpoint"] = endpoint
        captured["body"] = body
        return {"data": {"id": "a1", "status": "completed", "outputs": ["https://cdn/a.mp3"]}}

    svc._post = fake_post  # type: ignore[assignment]
    svc._download_media = lambda url, default_mime="": (b"MP3", "audio/mpeg")  # type: ignore[assignment]

    out = svc.text_to_speech(text="Bonjour", voice="Vivian", language="French")
    assert out["audio_bytes"] == b"MP3"
    assert captured["endpoint"] == "/wavespeed-ai/qwen3-tts/text-to-speech"
    assert captured["body"]["text"] == "Bonjour"
    assert captured["body"]["voice"] == "Vivian"
    assert captured["body"]["language"] == "French"


def test_voice_clone_uses_reference_audio_url_without_provider_voice_id():
    svc = _voice()
    captured = {}

    def fake_post(endpoint, body):
        captured["endpoint"] = endpoint
        captured["body"] = body
        return {"data": {"id": "vc1", "status": "completed", "outputs": ["https://cdn/vc.mp3"]}}

    svc._post = fake_post  # type: ignore[assignment]
    svc._download_media = lambda url, default_mime="": (b"VOICE", "audio/mpeg")  # type: ignore[assignment]

    out = svc.clone_speak(
        text="Bonjour",
        reference_audio_url="https://src/ref.wav",
        reference_text="Salut",
        language="French",
    )
    assert out["audio_bytes"] == b"VOICE"
    assert captured["endpoint"] == "/wavespeed-ai/qwen3-tts/voice-clone"
    assert captured["body"]["text"] == "Bonjour"
    assert captured["body"]["audio"] == "https://src/ref.wav"
    assert captured["body"]["language"] == "French"
    assert svc.ensure_voice_id("https://src/ref.wav") == ""


def test_3d_image_input_uses_image_to_3d_default_model():
    svc = _three_d()
    captured = {}

    def fake_post(endpoint, body):
        captured["endpoint"] = endpoint
        captured["body"] = body
        return {"data": {"id": "m1", "status": "completed", "outputs": ["https://cdn/m.glb"]}}

    svc._post = fake_post  # type: ignore[assignment]
    svc._download_media = lambda url, default_mime="": (b"GLB", "model/gltf-binary")  # type: ignore[assignment]

    out = svc.generate_3d(image_url="https://src/in.png")
    assert out["bytes"] == b"GLB"
    assert captured["endpoint"] == "/wavespeed-ai/hunyuan3d-v3/image-to-3d"
    assert captured["body"]["image"] == "https://src/in.png"


def test_lipsync_dispatches_audio_and_image_fields():
    svc = _lipsync()
    captured = {}

    def fake_post(endpoint, body):
        captured["endpoint"] = endpoint
        captured["body"] = body
        return {"data": {"id": "l1", "status": "completed", "outputs": ["https://cdn/l.mp4"]}}

    svc._post = fake_post  # type: ignore[assignment]
    svc._download_media = lambda url, default_mime="": (b"MP4", "video/mp4")  # type: ignore[assignment]

    out = svc.lipsync(image_url="https://src/face.png", audio_url="https://src/a.mp3")
    assert out["bytes"] == b"MP4"
    assert captured["endpoint"] == "/wavespeed-ai/ltx-2.3/lipsync"
    assert captured["body"]["audio"] == "https://src/a.mp3"
    assert captured["body"]["image"] == "https://src/face.png"


def test_category_mismatch_errors_clearly():
    svc = _video("wavespeed-ai/flux-dev")
    with pytest.raises(Exception, match="category"):
        svc._model()

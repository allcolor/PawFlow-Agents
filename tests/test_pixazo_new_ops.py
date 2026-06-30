"""Tests for new Pixazo operations added in the API coverage expansion.

Covers: video_edit, reference_to_video, frame_to_video, speech_to_video,
        describe_image, remix_image, remove_background, upscale_video,
        and the updated VideoGenerationHandler routing.
"""

import pytest

from services.pixazo_video_service import PixazoVideoService
from services.pixazo_image_service import PixazoImageService
from services.pixazo_capability_services import PixazoUpscaleService
from core.handlers.media import VideoGenerationHandler
from core.handlers.capabilities import (
    UpscaleVideoHandler, RemoveBackgroundHandler,
    DescribeImageHandler, RemixImageHandler, SpeechToVideoHandler,
)


# ── Helpers ───────────────────────────────────────────────────────────


def _video(model: str = "seedance-2-0-fast") -> PixazoVideoService:
    s = PixazoVideoService({"api_key": "k", "model": model, "poll_interval": 0})
    s._create_connection = lambda: {"ready": True}
    return s


def _image(model: str = "ideogram") -> PixazoImageService:
    s = PixazoImageService({"api_key": "k", "model": model, "poll_interval": 0})
    s._create_connection = lambda: {"ready": True}
    return s


def _upscale(model: str = "seedvr-upscale-video") -> PixazoUpscaleService:
    s = PixazoUpscaleService({"api_key": "k", "model": model, "poll_interval": 0})
    s._create_connection = lambda: {"ready": True}
    return s


def _stub(svc, media_url="https://cdn/out.mp4", media_bytes=b"MEDIA",
          content_type="video/mp4"):
    """Patch _post / _get_url / _download_media on a service instance."""
    captured = {}

    def _fake_post(ep, body, **kw):
        captured["ep"] = ep
        captured["body"] = body
        return {"id": "req-1",
                "polling_url": "https://gw/v2/requests/status/req-1"}

    svc._post = _fake_post
    svc._get_url = lambda u: {
        "status": "completed", "video_url": media_url,
        "image_url": media_url, "media_url": media_url,
        "output": {"media_url": [media_url]}}
    svc._download_media = lambda u, default_mime="": (media_bytes, content_type)
    return captured


# ── Video service: video_edit ─────────────────────────────────────────


def test_video_edit_sends_video_url():
    s = _video("kling-o1-edit-video-video-to-video-634")
    cap = _stub(s)
    out = s.video_edit(prompt="make it dreamy", video_url="https://src/v.mp4")
    assert out["video_bytes"] == b"MEDIA"
    assert cap["body"]["video_url"] == "https://src/v.mp4"


def test_video_edit_requires_video_url():
    s = _video("seedance-2-0-fast")
    _stub(s)
    with pytest.raises(Exception, match="video_url"):
        s.video_edit(prompt="x")


# ── Video service: reference_to_video ────────────────────────────────


def test_reference_to_video_builds_content_array():
    s = _video("seedance-2-0-fast")
    cap = _stub(s)
    out = s.reference_to_video(prompt="animate this",
                                image_url="https://src/img.png")
    assert out["video_bytes"] == b"MEDIA"
    content = cap["body"]["content"]
    assert isinstance(content, list)
    # First item = text prompt, second = image_url
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"] == "https://src/img.png"


def test_reference_to_video_requires_image_url():
    s = _video("seedance-2-0-fast")
    _stub(s)
    with pytest.raises(Exception, match="image_url"):
        s.reference_to_video(prompt="x")


# ── Video service: frame_to_video ────────────────────────────────────


def test_frame_to_video_sends_start_end_urls():
    s = _video("kling-3-0-text-to-video-standard")
    cap = _stub(s)
    out = s.frame_to_video(
        prompt="transition",
        image_url="https://src/start.png",
        end_image_url="https://src/end.png",
        model="kling-o1-first-frame-last-frame-to-video-857")
    assert out["video_bytes"] == b"MEDIA"
    assert cap["body"]["start_image_url"] == "https://src/start.png"
    assert cap["body"]["end_image_url"] == "https://src/end.png"


def test_frame_to_video_requires_both_images():
    s = _video("seedance-2-0-fast")
    _stub(s)
    with pytest.raises(Exception, match="image_url"):
        s.frame_to_video(prompt="x", end_image_url="https://e.png")
    with pytest.raises(Exception, match="end_image_url"):
        s.frame_to_video(prompt="x", image_url="https://s.png")


# ── Video service: speech_to_video ───────────────────────────────────


def test_speech_to_video_sends_image_and_audio():
    s = _video("seedance-2-0-fast")
    cap = _stub(s)
    out = s.speech_to_video(
        prompt="talking head",
        image_url="https://src/face.png",
        audio_url="https://src/speech.mp3")
    assert out["video_bytes"] == b"MEDIA"
    assert cap["body"]["image_url"] == "https://src/face.png"
    assert cap["body"]["audio_url"] == "https://src/speech.mp3"


def test_speech_to_video_requires_both_urls():
    s = _video("seedance-2-0-fast")
    _stub(s)
    with pytest.raises(Exception, match="image_url"):
        s.speech_to_video(audio_url="https://a.mp3")
    with pytest.raises(Exception, match="audio_url"):
        s.speech_to_video(image_url="https://i.png")


# ── Image service: describe_image ────────────────────────────────────


def test_describe_image_sends_image_url():
    s = _image("ideogram")
    s._fetch_multipart_file = lambda u: None  # deterministic: skip real fetch, URL fallback
    # describe uses convention=sync — _post must return the description inline
    captured = {}
    def _fake_post(ep, body, **kw):
        captured["ep"] = ep
        captured["body"] = body
        return {"data": {"description": "A cat on a table"}}
    s._post = _fake_post
    s._download_media = lambda u, default_mime="": (u.encode(), "text/plain")
    out = s.describe_image(image_url="https://src/photo.jpg")
    assert "description" in out


def test_describe_image_requires_image_url():
    s = _image("ideogram")
    _stub(s)
    with pytest.raises(Exception, match="image_url"):
        s.describe_image()


# ── Image service: remix_image ───────────────────────────────────────


def test_remix_image_sends_prompt_and_image():
    s = _image("ideogram")
    s._fetch_multipart_file = lambda u: None  # deterministic: skip real fetch, URL fallback
    cap = _stub(s, media_bytes=b"PNG", content_type="image/png")
    out = s.remix_image(prompt="make it cyberpunk",
                         image_url="https://src/base.png")
    assert out["image_bytes"] == b"PNG"
    assert cap["body"]["prompt"] == "make it cyberpunk"


def test_remix_image_requires_both():
    s = _image("ideogram")
    _stub(s)
    with pytest.raises(Exception, match="image_url"):
        s.remix_image(prompt="x")
    with pytest.raises(Exception, match="prompt"):
        s.remix_image(image_url="https://i.png")


# ── Upscale service: upscale_video ───────────────────────────────────


def test_upscale_video_sends_video_url():
    s = _upscale("seedvr-upscale-video")
    cap = _stub(s)
    out = s.upscale_video(video_url="https://src/v.mp4", scale=2)
    assert out["bytes"] == b"MEDIA"
    assert "video_url" in cap["body"] or cap["body"].get("video_url")


def test_upscale_video_requires_video_url():
    s = _upscale("seedvr-upscale-video")
    _stub(s)
    with pytest.raises(Exception, match="video_url"):
        s.upscale_video()


# ── Upscale service: remove_background ───────────────────────────────


def test_remove_background_sends_image_url():
    s = _upscale("bria-rmbg-2-0-682")
    _stub(s, media_bytes=b"PNG", content_type="image/png")
    out = s.remove_background(image_url="https://src/photo.jpg")
    assert out["bytes"] == b"PNG"


def test_remove_background_requires_image_url():
    s = _upscale("bria-rmbg-2-0-682")
    _stub(s)
    with pytest.raises(Exception, match="image_url"):
        s.remove_background()


# ── VideoGenerationHandler routing ───────────────────────────────────


def _make_handler_with_mock_service():
    """Build a VideoGenerationHandler with a mock service that records calls."""
    h = VideoGenerationHandler()
    h._user_id = "test"
    h._conversation_id = "conv"

    class MockService:
        calls = []

        def generate(self, **kw):
            self.calls.append(("generate", kw))
            return {"video_bytes": b"T2V", "content_type": "video/mp4",
                    "source_url": ""}

        def image_to_video(self, **kw):
            self.calls.append(("image_to_video", kw))
            return {"video_bytes": b"I2V", "content_type": "video/mp4",
                    "source_url": ""}

        def video_edit(self, **kw):
            self.calls.append(("video_edit", kw))
            return {"video_bytes": b"VE", "content_type": "video/mp4",
                    "source_url": ""}

        def reference_to_video(self, **kw):
            self.calls.append(("reference_to_video", kw))
            return {"video_bytes": b"REF", "content_type": "video/mp4",
                    "source_url": ""}

        def frame_to_video(self, **kw):
            self.calls.append(("frame_to_video", kw))
            return {"video_bytes": b"F2V", "content_type": "video/mp4",
                    "source_url": ""}

    svc = MockService()
    h.set_service_resolver(lambda: (svc, None))
    return h, svc


def test_handler_routes_text_to_video(monkeypatch):
    h, svc = _make_handler_with_mock_service()
    monkeypatch.setattr("core.storage_resolver.StorageResolver",
                        type("SR", (), {"__init__": lambda *a, **k: None,
                                        "write": lambda *a, **k: {"file_id": "f1"}}))
    result = h.execute({"prompt": "a cat"})
    assert "Video generated" in result
    assert svc.calls[-1][0] == "generate"


def test_handler_routes_image_to_video(monkeypatch):
    h, svc = _make_handler_with_mock_service()
    monkeypatch.setattr("core.storage_resolver.StorageResolver",
                        type("SR", (), {"__init__": lambda *a, **k: None,
                                        "write": lambda *a, **k: {"file_id": "f1"}}))
    result = h.execute({"prompt": "animate", "image_url": "https://img.png"})
    assert "Video generated" in result
    assert svc.calls[-1][0] == "image_to_video"


def test_handler_routes_video_edit(monkeypatch):
    h, svc = _make_handler_with_mock_service()
    monkeypatch.setattr("core.storage_resolver.StorageResolver",
                        type("SR", (), {"__init__": lambda *a, **k: None,
                                        "write": lambda *a, **k: {"file_id": "f1"}}))
    result = h.execute({"prompt": "stylize", "video_url": "https://v.mp4"})
    assert "Video generated" in result
    assert svc.calls[-1][0] == "video_edit"


def test_handler_routes_frame_to_video(monkeypatch):
    h, svc = _make_handler_with_mock_service()
    monkeypatch.setattr("core.storage_resolver.StorageResolver",
                        type("SR", (), {"__init__": lambda *a, **k: None,
                                        "write": lambda *a, **k: {"file_id": "f1"}}))
    result = h.execute({"prompt": "transition",
                        "image_url": "https://s.png",
                        "end_image_url": "https://e.png"})
    assert "Video generated" in result
    assert svc.calls[-1][0] == "frame_to_video"


# ── Capability handlers: schema validation ───────────────────────────


def test_upscale_video_handler_schema():
    h = UpscaleVideoHandler()
    assert h.name == "upscale_video"
    assert "video_url" in h.parameters_schema["properties"]
    assert "video_url" in h.parameters_schema["required"]


def test_remove_background_handler_schema():
    h = RemoveBackgroundHandler()
    assert h.name == "remove_background"
    assert "image_url" in h.parameters_schema["properties"]
    assert "image_url" in h.parameters_schema["required"]


def test_describe_image_handler_schema():
    h = DescribeImageHandler()
    assert h.name == "describe_image"
    assert "image_url" in h.parameters_schema["properties"]
    assert "llm_service" in h.parameters_schema["properties"]
    assert "image_url" in h.parameters_schema["required"]


def test_describe_image_handler_can_use_pawflow_vision_llm_service(monkeypatch):
    from types import SimpleNamespace

    class FakeClient:
        supports_vision = True

    class FakeLLMService:
        TYPE = "llmConnection"
        default_model = "vision-model"

        def __init__(self):
            self.calls = []

        def get_client(self):
            return FakeClient()

        def complete(self, messages, **kwargs):
            self.calls.append((messages, kwargs))
            return SimpleNamespace(content="a chart with blue bars")

    svc = FakeLLMService()
    monkeypatch.setattr(
        "core.service_registry.ServiceRegistry.get_instance",
        lambda: SimpleNamespace(resolve=lambda service_id, user_id="", conv_id="": svc),
    )
    h = DescribeImageHandler()
    h.set_user_id("alice")
    h.set_conversation_id("conv1")
    h.set_agent_name("assistant")

    result = h.execute({
        "image_url": "https://example.test/image.png",
        "llm_service": "vision_llm",
        "prompt": "Describe the data.",
    })

    assert result == "Image description: a chart with blue bars"
    messages, kwargs = svc.calls[0]
    assert kwargs["call_user_id"] == "alice"
    assert messages[0].content[0]["text"] == "Describe the data."
    assert messages[0].content[1]["image_url"]["url"] == "https://example.test/image.png"


def test_remix_image_handler_schema():
    h = RemixImageHandler()
    assert h.name == "remix_image"
    assert "prompt" in h.parameters_schema["required"]
    assert "image_url" in h.parameters_schema["required"]


def test_speech_to_video_handler_schema():
    h = SpeechToVideoHandler()
    assert h.name == "speech_to_video"
    assert "image_url" in h.parameters_schema["required"]
    assert "audio_url" in h.parameters_schema["required"]


def test_video_handler_has_new_params():
    h = VideoGenerationHandler()
    props = h.parameters_schema["properties"]
    assert "image_url" in props
    assert "video_url" in props
    assert "end_image_url" in props


# ── Capability handlers: no-resolver error ───────────────────────────


def test_upscale_video_no_resolver():
    h = UpscaleVideoHandler()
    assert "Error" in h.execute({"video_url": "https://v.mp4"})


def test_remove_background_no_resolver():
    h = RemoveBackgroundHandler()
    assert "Error" in h.execute({"image_url": "https://i.png"})


def test_describe_image_no_resolver():
    h = DescribeImageHandler()
    assert "Error" in h.execute({"image_url": "https://i.png"})


def test_remix_image_no_resolver():
    h = RemixImageHandler()
    assert "Error" in h.execute({"prompt": "x", "image_url": "https://i.png"})


def test_speech_to_video_no_resolver():
    h = SpeechToVideoHandler()
    assert "Error" in h.execute({"image_url": "https://i.png",
                                  "audio_url": "https://a.mp3"})


# ── describe/remix upload bytes instead of a URL Pixazo must fetch ────


def test_describe_image_uploads_bytes_when_multipart():
    """describe sends the image as a binary multipart part (not a URL Pixazo
    fetches), so PawFlow-local filestore URLs work."""
    from services._pixazo_base import _MultipartFile
    s = _image("ideogram")
    s._fetch_multipart_file = lambda u: _MultipartFile("p.png", "image/png", b"PNGBYTES")
    captured = {}

    def _fake_post(ep, body, **kw):
        captured["body"] = body
        captured["multipart"] = kw.get("multipart")
        return {"data": {"description": "a cat"}}

    s._post = _fake_post
    out = s.describe_image(image_url="http://localhost:9090/files/abc/x.png")
    assert out["description"] == "a cat"
    assert captured["multipart"] is True
    part = captured["body"]["image_file"]
    assert isinstance(part, _MultipartFile)
    assert part.data == b"PNGBYTES"


def test_encode_multipart_emits_binary_file_part():
    from services._pixazo_base import _PixazoBaseService, _MultipartFile
    body, boundary = _PixazoBaseService._encode_multipart({
        "prompt": "hi",
        "image_file": _MultipartFile("a.png", "image/png", b"\x89PNGDATA"),
    })
    assert b'name="prompt"' in body and b"hi" in body
    assert b'name="image_file"; filename="a.png"' in body
    assert b"Content-Type: image/png" in body
    assert b"\x89PNGDATA" in body

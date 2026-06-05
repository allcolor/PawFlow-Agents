"""Direct xAI Grok media services."""

import json

from services.grok_image_service import GrokImageService
from services.grok_video_service import GrokVideoService
from services.xai_stt_service import XAISTTService
from services.xai_tts_service import XAITTSService


class _Resp:
    def __init__(self, body: bytes, content_type="application/json"):
        self._body = body
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self._body


def _json(req):
    return json.loads((req.data or b"{}").decode("utf-8"))


def test_grok_image_generation_uses_quality_model_and_b64(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["body"] = _json(req)
        return _Resp(json.dumps({"data": [{"b64_json": "UE5H"}]}).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    svc = GrokImageService({"api_key": "xai-key"})

    out = svc.generate(prompt="a logo", width=2048, height=1024,
                       response_format="b64_json")

    assert out["image_bytes"] == b"PNG"
    assert captured["url"] == "https://api.x.ai/v1/images/generations"
    assert captured["body"]["model"] == "grok-imagine-image-quality"
    assert captured["body"]["aspect_ratio"] == "2:1"
    assert captured["body"]["response_format"] == "b64_json"


def test_grok_image_edit_sends_json_images(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["body"] = _json(req)
        return _Resp(json.dumps({"data": [{"b64_json": "RURJVA=="}]}).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    svc = GrokImageService({"api_key": "xai-key"})

    out = svc.edit_image(
        prompt="combine them",
        image_urls=["https://example.test/a.png", "data:image/png;base64,QQ=="],
        response_format="b64_json",
    )

    assert out["image_bytes"] == b"EDIT"
    assert captured["url"] == "https://api.x.ai/v1/images/edits"
    assert captured["body"]["model"] == "grok-imagine-image-quality"
    assert len(captured["body"]["images"]) == 2
    assert captured["body"]["images"][0] == {
        "url": "https://example.test/a.png", "type": "image_url"}


def test_grok_video_modes_use_direct_xai_endpoints(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout=0):
        calls.append((req.full_url, _json(req) if req.data else None))
        if req.full_url.endswith("/videos/generations"):
            return _Resp(json.dumps({"request_id": "req1"}).encode())
        if req.full_url.endswith("/videos/edits"):
            return _Resp(json.dumps({"request_id": "req2"}).encode())
        if req.full_url.endswith("/videos/extensions"):
            return _Resp(json.dumps({"request_id": "req3"}).encode())
        if "/videos/req" in req.full_url:
            return _Resp(json.dumps({
                "status": "done",
                "video": {"url": "https://cdn.example/out.mp4"},
            }).encode())
        if req.full_url == "https://cdn.example/out.mp4":
            return _Resp(b"MP4", "video/mp4")
        raise AssertionError(req.full_url)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    svc = GrokVideoService({"api_key": "xai-key", "poll_interval": 0})

    assert svc.image_to_video(prompt="move", image_url="https://img/in.png")["video_bytes"] == b"MP4"
    assert svc.reference_to_video(prompt="use refs", reference_image_urls=["https://img/a.png"])["video_bytes"] == b"MP4"
    assert svc.video_edit(prompt="add snow", video_url="https://vid/in.mp4")["video_bytes"] == b"MP4"
    assert svc.video_extend(prompt="continue", video_url="https://vid/in.mp4")["video_bytes"] == b"MP4"

    bodies = [body for _url, body in calls if body]
    assert bodies[0]["image"] == {"url": "https://img/in.png"}
    assert bodies[1]["reference_images"] == [{"url": "https://img/a.png"}]
    assert bodies[2]["video"] == {"url": "https://vid/in.mp4"}
    assert bodies[3]["duration"] == 6


def test_xai_tts_posts_to_direct_tts_endpoint(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["body"] = _json(req)
        return _Resp(b"AUDIO", "audio/mpeg")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    svc = XAITTSService({"api_key": "xai-key"})

    out = svc.speak("Bonjour", voice="ara", language="fr", codec="mp3")

    assert out["audio_bytes"] == b"AUDIO"
    assert captured["url"] == "https://api.x.ai/v1/tts"
    assert captured["body"]["text"] == "Bonjour"
    assert captured["body"]["voice"] == "ara"
    assert captured["body"]["language"] == "fr"


def test_xai_stt_posts_data_uri_to_direct_stt_endpoint(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["body"] = _json(req)
        return _Resp(json.dumps({"text": "hello", "duration": 1.2}).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    svc = XAISTTService({"api_key": "xai-key"})

    out = svc.transcribe(audio_bytes=b"WEBM", mime_type="audio/webm", language="en")

    assert out["text"] == "hello"
    assert captured["url"] == "https://api.x.ai/v1/stt"
    assert captured["body"]["file"] == "data:audio/webm;base64,V0VCTQ=="
    assert captured["body"]["model"] == "grok-transcribe"
    assert captured["body"]["language"] == "en"

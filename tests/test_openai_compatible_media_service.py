import base64
import json

from core import ServiceFactory
from services.openai_compatible_media_service import (
    OpenAICompatibleImageGenerationService,
    OpenAICompatibleVideoGenerationService,
)


class FakeClient:
    @staticmethod
    def _openai_tokens_key(model, base_url):
        if "api.openai.com" in base_url and model.startswith("gpt-5"):
            return "max_completion_tokens"
        return "max_tokens"


class FakeLLMService:
    provider = "openai"
    api_key = "sk-test"
    timeout = 30

    def __init__(self, base_url, default_model):
        self.base_url = base_url
        self.default_model = default_model
        self._client = FakeClient()


class FakeRequest:
    def __init__(self, body):
        self.body = body
        self.completed = None

    def complete(self, status, headers, body):
        self.completed = (status, headers, body)


class FakeListener:
    def __init__(self):
        self.routes = []
        self.unregistered = []

    def register_route(self, method, pattern, owner_id, callback,
                       ws_handler=None, public=False, private_only=False):
        self.routes.append({"callback": callback, "pattern": pattern, "public": public})

    def unregister_routes(self, owner_id):
        self.unregistered.append(owner_id)


def test_image_service_uses_openai_images_for_bare_openai(monkeypatch):
    svc = OpenAICompatibleImageGenerationService({
        "llm_service": "openai_llm",
        "model": "gpt-image-1",
    })
    llm = FakeLLMService("https://api.openai.com/v1", "gpt-image-1")
    calls = []

    monkeypatch.setattr(svc, "_resolve_llm_service", lambda: llm)
    monkeypatch.setattr(
        svc,
        "_request_json",
        lambda resolved, method, path, body=None: calls.append((method, path, body)) or {
            "data": [{"url": "https://cdn.example.com/out.png"}],
        },
    )
    monkeypatch.setattr(
        svc,
        "_download_url",
        lambda url, default_content_type, timeout: (b"PNG", "image/png"),
    )

    result = svc.generate(prompt="draw a cat", width=1024, height=1024)

    assert result == {"image_bytes": b"PNG", "content_type": "image/png"}
    assert calls[0][0] == "POST"
    assert calls[0][1] == "/images/generations"
    assert calls[0][2]["model"] == "gpt-image-1"
    assert calls[0][2]["prompt"] == "draw a cat"


def test_image_service_uses_chat_completions_for_openrouter_and_max_tokens(monkeypatch):
    svc = OpenAICompatibleImageGenerationService({
        "llm_service": "openrouter_llm",
        "model": "openai/gpt-5.4-image-2",
        "max_output_tokens": 2048,
        "extra_body": {"modalities": ["image"]},
    })
    llm = FakeLLMService("https://openrouter.ai/api/v1", "openai/gpt-5.4-image-2")
    calls = []
    raw = base64.b64encode(b"IMAGE").decode("ascii")

    monkeypatch.setattr(svc, "_resolve_llm_service", lambda: llm)
    monkeypatch.setattr(
        svc,
        "_request_json",
        lambda resolved, method, path, body=None: calls.append((method, path, body)) or {
            "choices": [{"message": {"content": {"image_base64": raw}}}],
        },
    )

    result = svc.generate(prompt="draw a city")

    assert result == {"image_bytes": b"IMAGE", "content_type": "image/png"}
    assert calls[0][1] == "/chat/completions"
    assert calls[0][2]["max_tokens"] == 2048
    assert calls[0][2]["modalities"] == ["image"]


def test_video_service_uses_openrouter_chat_and_downloads_direct_video(monkeypatch):
    svc = OpenAICompatibleVideoGenerationService({
        "llm_service": "openrouter_llm",
        "model": "kwaivgi/kling-v3.0-pro",
        "max_tokens": 512,
    })
    llm = FakeLLMService("https://openrouter.ai/api/v1", "kwaivgi/kling-v3.0-pro")
    calls = []

    monkeypatch.setattr(svc, "_resolve_llm_service", lambda: llm)
    monkeypatch.setattr(
        svc,
        "_request_json",
        lambda resolved, method, path, body=None: calls.append((method, path, body)) or {
            "choices": [{"message": {"content": "https://cdn.example.com/video.mp4"}}],
        },
    )
    monkeypatch.setattr(
        svc,
        "_download_url",
        lambda url, default_content_type, timeout: (b"MP4", "video/mp4"),
    )

    result = svc.generate(prompt="waves", duration=12, width=1920, height=1080,
                          image_url="https://cdn.example.com/first.png")

    assert result == {"video_bytes": b"MP4", "content_type": "video/mp4"}
    assert calls[0][1] == "/chat/completions"
    body = calls[0][2]
    assert body["model"] == "kwaivgi/kling-v3.0-pro"
    assert body["duration"] == 12
    assert body["max_tokens"] == 512
    assert body["messages"][0]["content"][1]["image_url"]["url"] == "https://cdn.example.com/first.png"


def test_video_service_default_timeout_matches_schema():
    svc = OpenAICompatibleVideoGenerationService({"llm_service": "openrouter_llm"})

    assert svc.timeout == 900
    assert svc.get_parameter_schema()["timeout"]["default"] == 900


def test_video_service_polls_generation_id(monkeypatch):
    svc = OpenAICompatibleVideoGenerationService({
        "llm_service": "openrouter_llm",
        "model": "kwaivgi/kling-v3.0-pro",
        "poll_interval": 0,
    })
    llm = FakeLLMService("https://openrouter.ai/api/v1", "kwaivgi/kling-v3.0-pro")
    calls = []

    def fake_request(resolved, method, path, body=None):
        calls.append((method, path, body))
        if method == "POST":
            return {"id": "gen_123", "status": "queued"}
        return {"status": "completed", "video": {"url": "https://cdn.example.com/final.mp4"}}

    monkeypatch.setattr(svc, "_resolve_llm_service", lambda: llm)
    monkeypatch.setattr(svc, "_request_json", fake_request)
    monkeypatch.setattr(
        svc,
        "_download_url",
        lambda url, default_content_type, timeout: (b"MP4", "video/mp4"),
    )

    result = svc.generate(prompt="waves")

    assert result["video_bytes"] == b"MP4"
    assert calls[1][0] == "GET"
    assert calls[1][1] == "/generation?id=gen_123"


def test_video_service_openrouter_video_webhook_uses_callback_url(monkeypatch):
    listener = FakeListener()
    monkeypatch.setattr(
        "services.http_listener_service.HTTPListenerService.all_instances",
        lambda: {9090: listener},
    )
    svc = OpenAICompatibleVideoGenerationService({
        "llm_service": "openrouter_llm",
        "model": "google/veo-3.1",
        "protocol": "openai_video",
        "submit_path": "/videos",
        "poll_interval": 0,
        "timeout": 5,
        "use_webhook": True,
    })
    svc.set_callback_base_url("https://webchat.example.org")
    llm = FakeLLMService("https://openrouter.ai/api/v1", "google/veo-3.1")
    calls = []

    def fake_request(resolved, method, path, body=None):
        calls.append((method, path, body))
        assert body["callback_url"].startswith(
            "https://webchat.example.org/webhooks/media/openrouter/")
        req = FakeRequest(json.dumps({
            "status": "completed",
            "outputs": ["https://cdn.example.com/final.mp4"],
        }).encode())
        listener.routes[-1]["callback"](req)
        assert req.completed[0] == 200
        return {"id": "vid_123", "status": "queued"}

    monkeypatch.setattr(svc, "_resolve_llm_service", lambda: llm)
    monkeypatch.setattr(svc, "_request_json", fake_request)
    monkeypatch.setattr(
        svc,
        "_download_url",
        lambda url, default_content_type, timeout: (b"MP4", "video/mp4"),
    )

    result = svc.generate(prompt="waves")

    assert result == {"video_bytes": b"MP4", "content_type": "video/mp4"}
    assert calls[0][1] == "/videos"
    assert listener.unregistered


def test_openai_compatible_media_services_are_registered():
    assert ServiceFactory.get("openaiCompatibleImageGeneration") is OpenAICompatibleImageGenerationService
    assert ServiceFactory.get("openaiCompatibleVideoGeneration") is OpenAICompatibleVideoGenerationService

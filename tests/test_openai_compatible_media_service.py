import base64
import json

from core import ServiceFactory
from services.openai_compatible_media_service import (
    OpenAICompatibleImageGenerationService,
    OpenAICompatibleVideoGenerationService,
)


class FakeClient:
    def __init__(self):
        self._user_id = ""
        self._conversation_id = ""

    @staticmethod
    def _openai_tokens_key(model, base_url):
        if "api.openai.com" in base_url and model.startswith("gpt-5"):
            return "max_completion_tokens"
        return "max_tokens"

    def clone_for_call(self):
        return FakeClient()


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
                       ws_handler=None, public=False, private_only=False,
                       gateway_exempt=False):
        self.routes.append({"callback": callback, "pattern": pattern,
                            "public": public, "gateway_exempt": gateway_exempt})

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
    raw = base64.b64encode(b"PNG").decode("ascii")
    monkeypatch.setattr(
        svc,
        "_request_json",
        lambda resolved, method, path, body=None: calls.append((method, path, body)) or {
            "data": [{"b64_json": raw}],
        },
    )

    result = svc.generate(prompt="draw a cat", width=1536, height=1024, output_format="png")

    assert result == {"image_bytes": b"PNG", "content_type": "image/png"}
    assert calls[0][0] == "POST"
    assert calls[0][1] == "/images/generations"
    assert calls[0][2]["model"] == "gpt-image-1"
    assert calls[0][2]["prompt"] == "draw a cat"
    assert calls[0][2]["size"] == "1536x1024"
    assert calls[0][2]["output_format"] == "png"
    assert "response_format" not in calls[0][2]


def test_resolved_llm_service_gets_runtime_context_on_cloned_client(monkeypatch):
    svc = OpenAICompatibleImageGenerationService({
        "llm_service": "openai_llm",
        "model": "gpt-image-1",
    })
    svc.set_runtime_context(user_id="alice", conversation_id="conv1")
    llm = FakeLLMService("https://api.openai.com/v1", "gpt-image-1")

    class FakeRegistry:
        def resolve_definition(self, *_args, **_kwargs):
            return type("Def", (), {"service_type": "llmConnection"})()

        def resolve(self, *_args, **_kwargs):
            return llm

    monkeypatch.setattr(
        "core.service_registry.ServiceRegistry.get_instance",
        staticmethod(lambda: FakeRegistry()),
    )

    resolved = svc._resolve_llm_service()

    assert resolved is not llm
    assert resolved._client is not llm._client
    assert resolved._client._user_id == "alice"
    assert resolved._client._conversation_id == "conv1"
    assert llm._client._user_id == ""
    assert llm._client._conversation_id == ""


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


def test_video_service_uses_openrouter_videos_endpoint_and_downloads_direct_video(monkeypatch):
    svc = OpenAICompatibleVideoGenerationService({
        "llm_service": "openrouter_llm",
        "model": "kwaivgi/kling-v3.0-pro",
        "extra_body": {"provider": {"sort": "throughput"}},
    })
    llm = FakeLLMService("https://openrouter.ai/api/v1", "kwaivgi/kling-v3.0-pro")
    calls = []

    monkeypatch.setattr(svc, "_resolve_llm_service", lambda: llm)
    monkeypatch.setattr(
        svc,
        "_request_json",
        lambda resolved, method, path, body=None: calls.append((method, path, body)) or {
            "unsigned_urls": ["https://cdn.example.com/video.mp4"],
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
    assert calls[0][1] == "/videos"
    body = calls[0][2]
    assert body["model"] == "kwaivgi/kling-v3.0-pro"
    assert body["duration"] == 12
    assert body["provider"] == {"sort": "throughput"}
    assert body["frame_images"][0]["image_url"]["url"] == "https://cdn.example.com/first.png"
    assert body["frame_images"][0]["frame_type"] == "first_frame"


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
    assert calls[1][1] == "/videos/gen_123"


def test_video_service_uses_openrouter_polling_url(monkeypatch):
    svc = OpenAICompatibleVideoGenerationService({
        "llm_service": "openrouter_llm",
        "model": "google/veo-3.1",
        "poll_interval": 0,
    })
    llm = FakeLLMService("https://openrouter.ai/api/v1", "google/veo-3.1")
    calls = []

    def fake_request(resolved, method, path, body=None):
        calls.append((method, path, body))
        if method == "POST":
            return {
                "id": "job_123",
                "status": "queued",
                "polling_url": "https://openrouter.ai/api/v1/videos/job_123",
            }
        return {"status": "completed", "unsigned_urls": ["https://cdn.example.com/final.mp4"]}

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
    assert calls[1][1] == "https://openrouter.ai/api/v1/videos/job_123"


def test_video_service_openrouter_video_webhook_uses_callback_url(monkeypatch):
    listener = FakeListener()
    monkeypatch.setattr(
        "services.http_listener_service.HTTPListenerService.all_instances",
        lambda: {9090: listener},
    )
    svc = OpenAICompatibleVideoGenerationService({
        "llm_service": "openrouter_llm",
        "model": "google/veo-3.1",
        "protocol": "openrouter",
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


def test_video_service_chat_completions_does_not_use_webhook(monkeypatch):
    svc = OpenAICompatibleVideoGenerationService({
        "llm_service": "openrouter_llm",
        "model": "legacy-video-model",
        "protocol": "chat_completions",
        "poll_interval": 0,
        "timeout": 5,
        "use_webhook": True,
    })
    llm = FakeLLMService("https://api.example.com/v1", "legacy-video-model")
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

    result = svc.generate(prompt="waves")

    assert result == {"video_bytes": b"MP4", "content_type": "video/mp4"}
    assert "callback_url" not in calls[0][2]


def test_openai_compatible_media_services_are_registered():
    assert ServiceFactory.get("openaiCompatibleImageGeneration") is OpenAICompatibleImageGenerationService
    assert ServiceFactory.get("openaiCompatibleVideoGeneration") is OpenAICompatibleVideoGenerationService


def test_video_service_atlascloud_predictions_poll_with_extension_less_output(monkeypatch):
    # AtlasCloud-style: submit returns the id nested under data, poll exposes
    # status + a signed result URL (no .mp4 extension) under data.outputs[].
    svc = OpenAICompatibleVideoGenerationService({
        "llm_service": "atlas_llm",
        "protocol": "openai_video",
        "model": "wan-2.7",
        "poll_interval": 0,
        "submit_path": "/model/generateVideo",
        "status_path_template": "/model/prediction/{id}",
    })
    llm = FakeLLMService("https://api.atlascloud.ai/api/v1", "wan-2.7")
    calls = []

    def fake_request(resolved, method, path, body=None):
        calls.append((method, path, body))
        if method == "POST":
            return {"data": {"id": "pred_42", "status": "queued"}}
        return {"data": {"status": "completed",
                         "outputs": ["https://cdn.atlascloud.ai/o/pred_42?sig=abc"]}}

    monkeypatch.setattr(svc, "_resolve_llm_service", lambda: llm)
    monkeypatch.setattr(svc, "_request_json", fake_request)
    monkeypatch.setattr(
        svc, "_download_url",
        lambda url, default_content_type, timeout: (b"MP4", "video/mp4"),
    )

    result = svc.generate(prompt="a rocket launching")

    assert result == {"video_bytes": b"MP4", "content_type": "video/mp4"}
    assert calls[0][0:2] == ("POST", "/model/generateVideo")
    assert calls[1][0:2] == ("GET", "/model/prediction/pred_42")


def test_video_service_minimal_submit_body_sends_only_model_and_prompt(monkeypatch):
    svc = OpenAICompatibleVideoGenerationService({
        "llm_service": "atlas_llm",
        "protocol": "openai_video",
        "model": "wan-2.7",
        "poll_interval": 0,
        "minimal_submit_body": True,
        "submit_path": "/model/generateVideo",
        "status_path_template": "/model/prediction/{id}",
    })
    llm = FakeLLMService("https://api.atlascloud.ai/api/v1", "wan-2.7")
    calls = []

    def fake_request(resolved, method, path, body=None):
        calls.append((method, path, body))
        if method == "POST":
            return {"data": {"id": "pred_7"}}
        return {"data": {"status": "completed",
                         "outputs": ["https://cdn.atlascloud.ai/o/pred_7"]}}

    monkeypatch.setattr(svc, "_resolve_llm_service", lambda: llm)
    monkeypatch.setattr(svc, "_request_json", fake_request)
    monkeypatch.setattr(
        svc, "_download_url",
        lambda url, default_content_type, timeout: (b"MP4", "video/mp4"),
    )

    svc.generate(prompt="a rocket", duration=12, width=1920, height=1080,
                 image_url="https://cdn.example.com/first.png", resolution="1080p")

    submit_body = calls[0][2]
    # Trimmed to the essentials; bulky fields the provider rejects are dropped.
    assert submit_body["model"] == "wan-2.7"
    assert submit_body["prompt"] == "a rocket"
    assert submit_body["image_url"] == "https://cdn.example.com/first.png"
    assert "duration" not in submit_body
    assert "aspect_ratio" not in submit_body
    assert "resolution" not in submit_body
    assert svc.get_parameter_schema()["minimal_submit_body"]["default"] is False


def test_image_service_extracts_extension_less_outputs_url():
    # AtlasCloud generateImage returns the asset under data.outputs[] with a
    # signed, extension-less URL — the regex path would miss it; the outputs
    # fallback must still find it.
    url = OpenAICompatibleImageGenerationService._extract_image_url(
        {"data": {"status": "completed",
                  "outputs": ["https://cdn.atlascloud.ai/o/img_1?sig=xyz"]}})
    assert url == "https://cdn.atlascloud.ai/o/img_1?sig=xyz"


def _atlas_video_service(extra_config=None):
    config = {
        "llm_service": "atlas_llm",
        "protocol": "openai_video",
        "model": "alibaba/wan-2.7/image-to-video",
        "poll_interval": 0,
        "submit_path": "/model/generateVideo",
        "status_path_template": "/model/prediction/{id}",
        # AtlasCloud Wan 2.7 body field names
        "image_field": "image",
        "end_image_field": "last_image",
        "video_field": "video",
        "audio_field": "audio",
    }
    config.update(extra_config or {})
    return OpenAICompatibleVideoGenerationService(config)


def _wire_atlas_video(monkeypatch, svc):
    llm = FakeLLMService("https://api.atlascloud.ai/api/v1", "alibaba/wan-2.7/image-to-video")
    calls = []

    def fake_request(resolved, method, path, body=None):
        calls.append((method, path, body))
        if method == "POST":
            return {"data": {"id": "pred_1"}}
        return {"data": {"status": "completed",
                         "outputs": ["https://cdn.atlascloud.ai/o/pred_1"]}}

    monkeypatch.setattr(svc, "_resolve_llm_service", lambda: llm)
    monkeypatch.setattr(svc, "_request_json", fake_request)
    monkeypatch.setattr(
        svc, "_download_url",
        lambda url, default_content_type, timeout: (b"MP4", "video/mp4"),
    )
    return calls


def test_video_service_exposes_all_mode_methods():
    # media_av.py dispatches each video operation by hasattr — the openai-
    # compatible service must advertise every mode so image/frame/reference/
    # video-edit requests are not rejected before reaching the provider.
    svc = _atlas_video_service()
    for method in ("image_to_video", "frame_to_video", "reference_to_video",
                   "video_edit", "video_extend", "speech_to_video"):
        assert hasattr(svc, method)


def test_video_service_image_to_video_uses_configured_image_field(monkeypatch):
    svc = _atlas_video_service()
    calls = _wire_atlas_video(monkeypatch, svc)

    svc.image_to_video(prompt="she flies away", duration=10,
                       image_url="https://cdn.example.com/girl.jpg")

    method, path, body = calls[0]
    assert (method, path) == ("POST", "/model/generateVideo")
    # The source image must ride under AtlasCloud's `image` field, not image_url.
    assert body["image"] == "https://cdn.example.com/girl.jpg"
    assert "image_url" not in body
    assert body["prompt"] == "she flies away"


def test_video_service_frame_to_video_sends_first_and_last_fields(monkeypatch):
    svc = _atlas_video_service()
    calls = _wire_atlas_video(monkeypatch, svc)

    svc.frame_to_video(prompt="morph", image_url="https://x/first.jpg",
                       end_image_url="https://x/last.jpg")

    body = calls[0][2]
    assert body["image"] == "https://x/first.jpg"
    assert body["last_image"] == "https://x/last.jpg"


def test_video_service_reference_to_video_sends_reference_list(monkeypatch):
    svc = _atlas_video_service({"reference_field": "reference_images"})
    calls = _wire_atlas_video(monkeypatch, svc)

    svc.reference_to_video(prompt="hero",
                           reference_image_urls=["https://x/a.jpg", "https://x/b.jpg"])

    body = calls[0][2]
    assert body["reference_images"] == ["https://x/a.jpg", "https://x/b.jpg"]


def test_video_service_video_edit_sends_video_field(monkeypatch):
    svc = _atlas_video_service()
    calls = _wire_atlas_video(monkeypatch, svc)

    svc.video_edit(prompt="restyle", video_url="https://x/clip.mp4")

    body = calls[0][2]
    assert body["video"] == "https://x/clip.mp4"


def test_video_service_image_field_defaults_to_image_url(monkeypatch):
    # Without an override the generic OpenAI convention is preserved.
    svc = OpenAICompatibleVideoGenerationService({
        "llm_service": "llm", "protocol": "openai_video", "model": "sora",
        "poll_interval": 0,
    })
    calls = _wire_atlas_video(monkeypatch, svc)
    svc.image_to_video(prompt="p", image_url="https://x/i.png")
    body = calls[0][2]
    assert body["image_url"] == "https://x/i.png"
    assert "image" not in body

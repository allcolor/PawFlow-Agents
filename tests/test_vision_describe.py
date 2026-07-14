import base64
from types import SimpleNamespace

import pytest


B64 = base64.b64encode(b"fake-png-bytes").decode("ascii")
B64_OTHER = base64.b64encode(b"other-png-bytes").decode("ascii")


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    import core.vision_describe as vd
    monkeypatch.setattr(vd, "_disk_cache_path",
                        lambda: str(tmp_path / "vision_cache.json"))
    vd._mem_cache.clear()
    monkeypatch.setattr(vd, "_disk_loaded", False)
    yield
    vd._mem_cache.clear()


class FakeVisionClient:
    supports_vision = True


class FakeVisionService:
    TYPE = "llmConnection"
    _service_id = "vision_svc"

    def __init__(self, description="a red button at [10, 20, 80, 30]"):
        self.calls = []
        self._description = description

    def get_client(self):
        return FakeVisionClient()

    def complete(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return SimpleNamespace(content=self._description)


def _patch_registry(monkeypatch, svc):
    monkeypatch.setattr(
        "core.service_registry.ServiceRegistry.get_instance",
        lambda: SimpleNamespace(
            resolve=lambda service_id, user_id="", conv_id="": svc),
    )


def _image_message(b64=B64):
    from core.llm_client import LLMMessage
    return LLMMessage(
        role="user",
        conversation_id="c1",
        content=[
            {"type": "text", "text": "what do you see?"},
            {"type": "image_url",
             "image_url": {"url": f"data:image/png;base64,{b64}"}},
        ],
    )


def test_apply_vision_fallback_replaces_images_with_descriptions(monkeypatch):
    from core.vision_describe import apply_vision_fallback
    svc = FakeVisionService()
    _patch_registry(monkeypatch, svc)

    msg = _image_message()
    original_parts = list(msg.content)
    out = apply_vision_fallback([msg], "vision_svc", source_service_id="glm_svc",
                                user_id="alice", conversation_id="c1")

    assert len(svc.calls) == 1
    assert out[0].content[0] == {"type": "text", "text": "what do you see?"}
    assert out[0].content[1]["type"] == "text"
    assert "a red button at [10, 20, 80, 30]" in out[0].content[1]["text"]
    assert "vision model described it" in out[0].content[1]["text"]
    # The stored message is never mutated
    assert msg.content == original_parts
    assert msg.content[1]["type"] == "image_url"


def test_apply_vision_fallback_caches_by_image_hash(monkeypatch):
    from core.vision_describe import apply_vision_fallback
    svc = FakeVisionService()
    _patch_registry(monkeypatch, svc)

    apply_vision_fallback([_image_message()], "vision_svc", user_id="alice")
    apply_vision_fallback([_image_message()], "vision_svc", user_id="alice")
    assert len(svc.calls) == 1  # second identical image hits the cache

    apply_vision_fallback([_image_message(B64_OTHER)], "vision_svc", user_id="alice")
    assert len(svc.calls) == 2  # different image bytes -> new describe call


def test_vision_describe_cache_survives_restart(monkeypatch):
    import core.vision_describe as vd
    svc = FakeVisionService()
    _patch_registry(monkeypatch, svc)

    vd.apply_vision_fallback([_image_message()], "vision_svc", user_id="alice")
    assert len(svc.calls) == 1

    # Simulate a server restart: memory gone, disk cache remains
    vd._mem_cache.clear()
    monkeypatch.setattr(vd, "_disk_loaded", False)

    vd.apply_vision_fallback([_image_message()], "vision_svc", user_id="alice")
    assert len(svc.calls) == 1


def test_apply_vision_fallback_skips_self_reference(monkeypatch):
    from core.vision_describe import apply_vision_fallback
    svc = FakeVisionService()
    _patch_registry(monkeypatch, svc)

    msg = _image_message()
    out = apply_vision_fallback([msg], "glm_svc", source_service_id="glm_svc")
    assert out[0] is msg
    assert not svc.calls


def test_apply_vision_fallback_requires_vision_enabled_target(monkeypatch):
    from core.vision_describe import apply_vision_fallback

    class NoVisionClient:
        supports_vision = False

    svc = FakeVisionService()
    svc.get_client = lambda: NoVisionClient()
    _patch_registry(monkeypatch, svc)

    msg = _image_message()
    out = apply_vision_fallback([msg], "vision_svc", source_service_id="glm_svc")
    assert out[0] is msg
    assert not svc.calls


def test_apply_vision_fallback_no_images_is_noop(monkeypatch):
    from core.llm_client import LLMMessage
    from core.vision_describe import apply_vision_fallback
    svc = FakeVisionService()
    _patch_registry(monkeypatch, svc)

    msgs = [LLMMessage(role="user", content="plain text", conversation_id="c1")]
    assert apply_vision_fallback(msgs, "vision_svc") is msgs
    assert not svc.calls


def test_llm_connection_schema_and_rules_expose_vision_llm_service():
    from services.llm_connection import LLMConnectionService

    schema = LLMConnectionService({}).get_parameter_schema()
    param = schema["vision_llm_service"]
    assert param["type"] == "service_ref"
    assert param["service_type"] == "llmConnection"

    rules = object.__new__(LLMConnectionService).get_parameter_rules()
    show_rules = [r for r in rules
                  if r["set"].get("vision_llm_service", {}).get("visible") is True]
    assert show_rules, "a rule must reveal vision_llm_service"
    assert show_rules[-1]["when"] == {"supports_vision": ["false", False]}
    # supports_vision is configurable for every provider (CLI base_url can
    # point at a non-vision model)
    for rule in rules:
        vis = rule["set"].get("supports_vision", {}).get("visible")
        assert vis is not False


def test_service_complete_applies_fallback_only_when_vision_disabled(monkeypatch):
    from services.llm_connection import LLMConnectionService

    captured = {}

    def fake_apply(messages, target, **kwargs):
        captured["target"] = target
        captured["kwargs"] = kwargs
        return ["transformed"]

    monkeypatch.setattr("core.vision_describe.apply_vision_fallback", fake_apply)

    svc = object.__new__(LLMConnectionService)
    # _service_id lives in the config injected by the ServiceRegistry —
    # it is never an instance attribute on LLMConnectionService.
    svc.config = {"vision_llm_service": "vision_svc", "_service_id": "glm_svc"}

    class NoVisionClient:
        supports_vision = False

    svc.get_client = lambda: NoVisionClient()
    out = svc._maybe_apply_vision_fallback(
        ["m"], {"call_user_id": "alice", "call_conversation_id": "c1",
                "call_agent_name": "assistant"})
    assert out == ["transformed"]
    assert captured["target"] == "vision_svc"
    assert captured["kwargs"]["source_service_id"] == "glm_svc"
    assert captured["kwargs"]["user_id"] == "alice"

    class VisionClient:
        supports_vision = True

    svc.get_client = lambda: VisionClient()
    msgs = ["m"]
    assert svc._maybe_apply_vision_fallback(msgs, {}) is msgs


@pytest.mark.parametrize("streaming", [False, True])
def test_agent_loop_direct_client_call_applies_service_vision_fallback(streaming):
    from core.llm_client import LLMMessage
    from tasks.ai.agent_core import AgentCoreMixin

    original = LLMMessage(
        role="user",
        content=[
            {"type": "text", "text": "describe"},
            {"type": "image_ref", "file_id": "img-1"},
        ],
        conversation_id="c1",
    )
    transformed = LLMMessage(
        role="user",
        content=[{"type": "text", "text": "described image"}],
        conversation_id="c1",
    )
    fallback_calls = []
    provider_calls = []

    class Service:
        def _maybe_apply_vision_fallback(self, messages, call_kwargs):
            fallback_calls.append((messages, call_kwargs))
            return [transformed]

    class Client:
        def complete(self, **kwargs):
            provider_calls.append(kwargs)
            return SimpleNamespace(content="ok")

        def complete_stream(self, **kwargs):
            provider_calls.append(kwargs)
            return SimpleNamespace(content="ok")

    class Emitter:
        is_streaming = streaming

        @staticmethod
        def get_token_callback(_poll_silent):
            return None

        @staticmethod
        def get_thinking_callback(_poll_silent):
            return None

    st = SimpleNamespace(
        user_id="alice",
        conversation_id="c1",
        ctx={
            "active_agent_name": "assistant",
            "_event_cid": "event-c1",
            "temperature": 0.2,
            "max_tokens": 500,
        },
        resolved_svc=Service(),
        client=Client(),
        emitter=Emitter(),
        model="model",
        tool_defs=[],
        _tb=0,
        _client_provider="openai",
        _claude_code_turn_callback=None,
        _cli_block_callback=None,
    )

    core = object.__new__(AgentCoreMixin)
    response = core._alc_llm_call(st, [original], False)

    assert response.content == "ok"
    assert len(fallback_calls) == 1
    assert fallback_calls[0][0] == [original]
    assert fallback_calls[0][1] == {
        "call_user_id": "alice",
        "call_conversation_id": "c1",
        "call_agent_name": "assistant",
        "call_event_cid": "event-c1",
        "call_ephemeral_stream": False,
    }
    assert provider_calls[0]["messages"] == [transformed]
    # The transcript object passed into preprocessing remains image-backed.
    assert original.content[1]["type"] == "image_ref"


def test_apply_vision_fallback_describe_failure_replaces_with_placeholder(monkeypatch):
    """When the vision model raises, the raw image must NOT leak to the
    non-vision LLM — it should be replaced by a text placeholder."""
    from core.vision_describe import apply_vision_fallback

    class BrokenVisionService(FakeVisionService):
        def complete(self, messages, **kwargs):
            raise RuntimeError("vision model 500")

    svc = BrokenVisionService()
    _patch_registry(monkeypatch, svc)

    msg = _image_message()
    out = apply_vision_fallback([msg], "vision_svc", source_service_id="glm_svc",
                                user_id="alice", conversation_id="c1")

    assert out[0].content[1]["type"] == "text"
    assert "could not be described" in out[0].content[1]["text"]
    # No image_url part should remain
    assert not any(p.get("type") in ("image_url", "image_ref")
                   for p in out[0].content)


def test_apply_vision_fallback_empty_description_replaces_with_placeholder(monkeypatch):
    """When the vision model returns an empty string, the raw image must
    NOT leak — it should be replaced by a text placeholder."""
    from core.vision_describe import apply_vision_fallback

    svc = FakeVisionService(description="")
    _patch_registry(monkeypatch, svc)

    msg = _image_message()
    out = apply_vision_fallback([msg], "vision_svc", source_service_id="glm_svc",
                                user_id="alice", conversation_id="c1")

    assert out[0].content[1]["type"] == "text"
    assert "could not be described" in out[0].content[1]["text"]
    assert not any(p.get("type") in ("image_url", "image_ref")
                   for p in out[0].content)


def test_downscale_b64_returns_original_when_small():
    """Small images should pass through unchanged."""
    from core.vision_describe import _downscale_b64
    mime, b64 = _downscale_b64("image/png", B64)
    assert mime == "image/png"
    assert b64 == B64


def test_downscale_b64_returns_original_on_invalid_image():
    """Invalid image bytes should fall back to the original pair."""
    from core.vision_describe import _downscale_b64
    mime, b64 = _downscale_b64("image/png", B64)  # B64 is fake bytes
    assert mime == "image/png"
    assert b64 == B64


def test_downscale_b64_resizes_large_image():
    """A real large image should be downscaled so neither dimension
    exceeds _MAX_IMAGE_DIM."""
    import io
    import base64 as _b64
    from PIL import Image
    from core.vision_describe import _downscale_b64, _MAX_IMAGE_DIM

    # Create a 2000x1500 solid-color image
    img = Image.new("RGB", (2000, 1500), color=(100, 150, 200))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    big_b64 = _b64.b64encode(buf.getvalue()).decode("ascii")

    out_mime, out_b64 = _downscale_b64("image/png", big_b64)
    assert out_mime in ("image/png", "image/jpeg")

    # Verify the output dimensions are within the limit
    out_img = Image.open(io.BytesIO(_b64.b64decode(out_b64)))
    assert out_img.width <= _MAX_IMAGE_DIM
    assert out_img.height <= _MAX_IMAGE_DIM
    # Aspect ratio preserved
    assert abs(out_img.width / out_img.height - 2000 / 1500) < 0.01


def test_agent_loop_vision_fallback_failure_is_fail_open():
    from core.llm_client import LLMMessage
    from tasks.ai.agent_core import AgentCoreMixin

    message = LLMMessage(
        role="user", content="plain", conversation_id="c1")
    provider_calls = []

    class BrokenService:
        @staticmethod
        def _maybe_apply_vision_fallback(_messages, _call_kwargs):
            raise RuntimeError("vision service unavailable")

    class Client:
        @staticmethod
        def complete(**kwargs):
            provider_calls.append(kwargs)
            return SimpleNamespace(content="ok")

    st = SimpleNamespace(
        user_id="alice",
        conversation_id="c1",
        ctx={
            "active_agent_name": "assistant",
            "temperature": 0.2,
            "max_tokens": 500,
        },
        resolved_svc=BrokenService(),
        client=Client(),
        emitter=SimpleNamespace(is_streaming=False),
        model="model",
        tool_defs=[],
        _tb=0,
    )

    core = object.__new__(AgentCoreMixin)
    response = core._alc_llm_call(st, [message], False)

    assert response.content == "ok"
    assert provider_calls[0]["messages"] == [message]

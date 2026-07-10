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
    svc.config = {"vision_llm_service": "vision_svc"}
    svc._service_id = "glm_svc"

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

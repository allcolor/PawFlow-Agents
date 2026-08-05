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


def test_apply_vision_fallback_describes_multiple_images_parallel(monkeypatch):
    """Two images in one pass must both be described (parallel workers),
    preserving message and part order in the rebuilt output."""
    import threading
    from core.llm_client import LLMMessage
    from core.vision_describe import apply_vision_fallback

    active = []
    lock = threading.Lock()
    peak = [0]

    class SlowVisionClient:
        supports_vision = True

    class SlowVisionService(FakeVisionService):
        def complete(self, messages, **kwargs):
            self.calls.append((messages, kwargs))
            with lock:
                active.append(threading.current_thread().name)
                peak[0] = max(peak[0], len(active))
            try:
                import time
                time.sleep(0.05)
                return SimpleNamespace(content=self._description)
            finally:
                with lock:
                    active.remove(threading.current_thread().name)

    svc = SlowVisionService()
    _patch_registry(monkeypatch, svc)

    msg = LLMMessage(
        role="user",
        conversation_id="c1",
        content=[
            {"type": "image_url",
             "image_url": {"url": f"data:image/png;base64,{B64}"}},
            {"type": "image_url",
             "image_url": {"url": f"data:image/png;base64,{B64_OTHER}"}},
        ],
    )

    out = apply_vision_fallback([msg], "vision_svc", user_id="alice",
                                conversation_id="c1")

    assert len(svc.calls) == 2
    assert peak[0] >= 2  # at least two describes overlapped
    assert len(out) == 1
    assert len(out[0].content) == 2
    assert out[0].content[0]["type"] == "text"
    assert out[0].content[1]["type"] == "text"
    assert "a red button" in out[0].content[0]["text"]
    assert "a red button" in out[0].content[1]["text"]


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


def test_apply_vision_fallback_only_describes_current_turn(monkeypatch):
    """A — only images of the CURRENT turn (message marked
    _pawflow_current_user_message) are described. Images in OLDER context
    messages are replaced by a short placeholder with NO vision call — their
    description is already persisted in the context (persistence B), so
    re-describing them would re-submit the image to the vision model every
    turn (and, after a server restart, redo network calls for nothing)."""
    from core.llm_client import LLMMessage
    from core.vision_describe import apply_vision_fallback
    svc = FakeVisionService()
    _patch_registry(monkeypatch, svc)

    old_msg = _image_message()  # historical message (not marked current)
    current_msg = _image_message()
    current_msg._pawflow_current_user_message = True

    out = apply_vision_fallback(
        [old_msg, current_msg], "vision_svc", source_service_id="glm_svc",
        user_id="alice", conversation_id="c1")

    # Exactly ONE image described: the current turn's. The historical image
    # is NOT re-submitted to the vision model.
    assert len(svc.calls) == 1
    # Historical image → short placeholder, no description.
    assert out[0].content[1]["type"] == "text"
    assert "previously described" in out[0].content[1]["text"]
    # Current-turn image → described normally.
    assert "a red button" in out[1].content[1]["text"]


def test_apply_vision_fallback_persists_description(monkeypatch):
    """B — after describing the current user message, its image is replaced
    in the store by the PERSISTED description (attachment marked described),
    so it is never re-submitted to the vision model on later turns."""
    from core.llm_client import LLMMessage
    from core.vision_describe import apply_vision_fallback
    svc = FakeVisionService()
    _patch_registry(monkeypatch, svc)

    patched = []

    class _FakeStore:
        def patch_message(self, cid, msg_id, **fields):
            patched.append((cid, msg_id, fields))

    monkeypatch.setattr(
        "core.conversation_store.ConversationStore.instance",
        lambda: _FakeStore())
    monkeypatch.setattr(
        "core.file_store.FileStore.instance",
        lambda: SimpleNamespace(
            get_required=lambda file_id, user_id="", conversation_id="": (
                "photo.png", b"fake-png-bytes", "image/png"),
        ))

    msg = LLMMessage(
        role="user",
        conversation_id="c1",
        msg_id="m-user-image",
        content=[
            {"type": "text", "text": "what do you see?"},
            {"type": "image_ref", "file_id": "img-1", "filename": "photo.png",
             "mime_type": "image/png", "size": 123},
        ],
    )
    msg._pawflow_current_user_message = True

    apply_vision_fallback(
        [msg], "vision_svc", source_service_id="glm_svc",
        user_id="alice", conversation_id="c1")

    assert len(svc.calls) == 1
    assert len(patched) == 1
    cid, mid, fields = patched[0]
    assert cid == "c1"
    assert mid == "m-user-image"
    atts = fields["attachments"]
    assert len(atts) == 1
    assert atts[0]["described"] is True
    assert atts[0]["file_id"] == "img-1"
    assert "a red button" in atts[0]["description"]
    # The in-memory flag prevents redundant I/O on the next iteration.
    assert msg._vision_persisted is True


def test_apply_vision_fallback_persist_skips_when_already_persisted(monkeypatch):
    """B — a message already persisted this turn (flag set) is not patched
    again on the next iteration of the same turn (no redundant I/O)."""
    from core.llm_client import LLMMessage
    from core.vision_describe import apply_vision_fallback
    svc = FakeVisionService()
    _patch_registry(monkeypatch, svc)

    patched = []

    class _FakeStore:
        def patch_message(self, cid, msg_id, **fields):
            patched.append((cid, msg_id, fields))

    monkeypatch.setattr(
        "core.conversation_store.ConversationStore.instance",
        lambda: _FakeStore())
    monkeypatch.setattr(
        "core.file_store.FileStore.instance",
        lambda: SimpleNamespace(
            get_required=lambda file_id, user_id="", conversation_id="": (
                "photo.png", b"fake-png-bytes", "image/png"),
        ))

    msg = LLMMessage(
        role="user",
        conversation_id="c1",
        msg_id="m-user-image",
        content=[
            {"type": "text", "text": "what do you see?"},
            {"type": "image_ref", "file_id": "img-1", "filename": "photo.png",
             "mime_type": "image/png", "size": 123},
        ],
    )
    msg._pawflow_current_user_message = True

    apply_vision_fallback(
        [msg], "vision_svc", source_service_id="glm_svc",
        user_id="alice", conversation_id="c1")
    assert len(patched) == 1

    # Second iteration of the same turn: same in-memory message (flag set) →
    # the vision cache serves the description, but NO second store write.
    apply_vision_fallback(
        [msg], "vision_svc", source_service_id="glm_svc",
        user_id="alice", conversation_id="c1")
    assert len(patched) == 1


def test_llm_connection_schema_and_rules_expose_vision_llm_service():
    from services.llm_connection import LLMConnectionService

    schema = LLMConnectionService({}).get_parameter_schema()
    param = schema["vision_llm_service"]
    assert param["type"] == "service_ref"
    assert param["service_type"] == "llmConnection"
    mt = schema["vision_max_tokens"]
    assert mt["type"] == "integer"
    assert mt["default"] == 1024

    rules = object.__new__(LLMConnectionService).get_parameter_rules()
    show_rules = [r for r in rules
                  if r["set"].get("vision_llm_service", {}).get("visible") is True]
    assert show_rules, "a rule must reveal vision_llm_service"
    assert show_rules[-1]["when"] == {"supports_vision": ["false", False]}
    assert show_rules[-1]["set"].get("vision_max_tokens", {}).get("visible") is True
    # supports_vision is configurable for every provider (CLI base_url can
    # point at a non-vision model)
    for rule in rules:
        vis = rule["set"].get("supports_vision", {}).get("visible")
        assert vis is not False


def test_describe_image_b64_uses_service_vision_max_tokens(monkeypatch):
    """The vision service's own config overrides the default token budget,
    and the cache key changes so a truncated v1 description is not served
    after the budget is raised."""
    from core.vision_describe import describe_image_b64
    import core.vision_describe as vd

    captured = {}

    class ConfVisionService(FakeVisionService):
        config = {"vision_max_tokens": 4096}

        def complete(self, messages, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(content="described")

    svc = ConfVisionService()
    out = describe_image_b64(svc, "image/png", B64, user_id="alice")
    assert out == "described"
    assert captured["max_tokens"] == 4096

    # Same image, same service, but a different budget -> must re-describe
    # (cache key includes max_tokens), so a truncated old entry is not reused.
    vd._mem_cache.clear()
    svc.config = {"vision_max_tokens": 1024}
    out = describe_image_b64(svc, "image/png", B64, user_id="alice")
    assert captured["max_tokens"] == 1024

    # Default (no config): falls back to the 1024 parameter default. Use a
    # fresh image so the earlier 4096/1024 entries are not served from cache.
    plain = FakeVisionService()
    fresh_b64 = base64.b64encode(b"default-budget-image").decode("ascii")
    describe_image_b64(plain, "image/png", fresh_b64, user_id="alice")
    assert plain.calls[-1][1]["max_tokens"] == 1024


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


def test_describe_image_b64_single_flight_parallel_same_image(monkeypatch):
    """Two parallel describe calls for the SAME image must make exactly
    ONE network call: the second waits on the first's in-flight event and
    serves from the fresh cache entry (no stampede)."""
    import threading
    import time
    from core.vision_describe import describe_image_b64
    import core.vision_describe as vd

    calls = []
    lock = threading.Lock()
    gate = threading.Event()

    class GateVisionService(FakeVisionService):
        def complete(self, messages, **kwargs):
            with lock:
                calls.append(1)
            # Hold the call open so the second worker is guaranteed to
            # arrive while this one is still in flight.
            gate.wait(timeout=5)
            return SimpleNamespace(content="described once")

    svc = GateVisionService()
    results = []

    def _worker():
        results.append(describe_image_b64(
            svc, "image/png", B64, user_id="alice"))

    t1 = threading.Thread(target=_worker)
    t2 = threading.Thread(target=_worker)
    t1.start()
    time.sleep(0.1)  # ensure t1 registers as leader first
    t2.start()
    time.sleep(0.1)
    gate.set()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert len(calls) == 1  # single flight: one network call for the image
    assert results == ["described once", "described once"]


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

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
    msg = LLMMessage(
        role="user",
        conversation_id="c1",
        content=[
            {"type": "text", "text": "what do you see?"},
            {"type": "image_url",
             "image_url": {"url": f"data:image/png;base64,{b64}"}},
        ],
    )
    msg._pawflow_current_user_message = True
    return msg


def test_apply_vision_fallback_replaces_images_with_descriptions(monkeypatch):
    from core.vision_describe import apply_vision_fallback
    svc = FakeVisionService()
    _patch_registry(monkeypatch, svc)

    msg = _image_message()
    out = apply_vision_fallback([msg], "vision_svc", source_service_id="glm_svc",
                                user_id="alice", conversation_id="c1")

    assert len(svc.calls) == 1
    assert out[0].content[0] == {"type": "text", "text": "what do you see?"}
    assert out[0].content[1]["type"] == "text"
    assert "a red button at [10, 20, 80, 30]" in out[0].content[1]["text"]
    assert "vision model described it" in out[0].content[1]["text"]
    # The live agent context keeps the description, not the raw image.
    assert msg.content == out[0].content
    assert msg.content[1]["type"] == "text"


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
    msg._pawflow_current_user_message = True

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

    old_msg = _image_message()
    del old_msg._pawflow_current_user_message
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


def test_apply_vision_fallback_without_marker_uses_last_user_message(monkeypatch):
    """The prompt marker can be lost when context builders rebuild the
    message; the most recent user message is then the active prompt and
    its images MUST still be described — never sent raw to the LLM."""
    from core.llm_client import LLMMessage
    from core.vision_describe import apply_vision_fallback

    svc = FakeVisionService()
    _patch_registry(monkeypatch, svc)
    historical = _image_message()
    del historical._pawflow_current_user_message

    out = apply_vision_fallback(
        [historical], "vision_svc", source_service_id="glm_svc",
        user_id="alice", conversation_id="c1")

    assert out is not None
    assert len(svc.calls) == 1
    assert not any(p.get("type") in ("image_url", "image_ref")
                   for p in out[0].content)

    # But a historical image (an older user message) is NOT re-described
    # when a more recent user message exists without images.
    svc2 = FakeVisionService()
    _patch_registry(monkeypatch, svc2)
    old = _image_message()
    del old._pawflow_current_user_message
    cur = LLMMessage(role="user", conversation_id="c1", content=[
        {"type": "text", "text": "hello"}])
    out2 = apply_vision_fallback(
        [old, cur], "vision_svc", source_service_id="glm_svc",
        user_id="alice", conversation_id="c1")
    assert svc2.calls == []
    assert out2[0].content[1]["type"] == "image_url"


@pytest.mark.parametrize("tool_name", ["read", "see"])
def test_current_read_and_see_images_become_tool_descriptions(
        monkeypatch, tool_name):
    from core.llm_client import LLMMessage, LLMToolCall
    from core.vision_describe import apply_vision_fallback

    svc = FakeVisionService()
    _patch_registry(monkeypatch, svc)
    current = LLMMessage(
        role="user", content="inspect it", msg_id="u1",
        conversation_id="c1")
    current._pawflow_current_user_message = True
    call = LLMMessage(
        role="assistant", content="",
        tool_calls=[LLMToolCall(id="tc1", name=tool_name, arguments={})],
        conversation_id="c1")
    result = _image_message()
    result.role = "tool"
    result.tool_call_id = "tc1"
    del result._pawflow_current_user_message

    out = apply_vision_fallback(
        [current, call, result], "vision_svc", source_service_id="glm_svc",
        user_id="alice", conversation_id="c1")

    assert len(svc.calls) == 1
    assert out[2].role == "tool"
    assert all(part.get("type") != "image_url" for part in out[2].content)
    assert "a red button" in out[2].content[1]["text"]


def test_other_current_tool_images_are_not_sent_to_vision(monkeypatch):
    from core.llm_client import LLMMessage, LLMToolCall
    from core.vision_describe import apply_vision_fallback

    svc = FakeVisionService()
    _patch_registry(monkeypatch, svc)
    current = LLMMessage(
        role="user", content="browse", msg_id="u1",
        conversation_id="c1")
    current._pawflow_current_user_message = True
    call = LLMMessage(
        role="assistant", content="",
        tool_calls=[LLMToolCall(id="tc1", name="browser", arguments={})],
        conversation_id="c1")
    result = _image_message()
    result.role = "tool"
    result.tool_call_id = "tc1"
    del result._pawflow_current_user_message

    apply_vision_fallback(
        [current, call, result], "vision_svc", source_service_id="glm_svc",
        user_id="alice", conversation_id="c1")

    assert svc.calls == []


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

    # The first pass replaces the live agent-context image with its text, so a
    # later iteration performs neither a second vision call nor a store write.
    apply_vision_fallback(
        [msg], "vision_svc", source_service_id="glm_svc",
        user_id="alice", conversation_id="c1")
    assert len(patched) == 1
    assert len(svc.calls) == 1
    assert not any(part.get("type") == "image_ref" for part in msg.content)


def test_find_msg_in_context_reuses_preexisting_user_message():
    """_pac_p3 must not inject a second copy of the start-of-turn user
    message: the streaming ingress pre-persists it BEFORE the context is
    built, so the loaded context already carries it (same msg_id). Reusing
    it (and marking it current) avoids the duplicate-image bug where the
    same upload gets TWO image_refs with different file_ids and is
    described twice by the vision fallback."""
    from core.llm_client import LLMMessage
    from tasks.ai._agentctx_p3 import _find_msg_in_context

    ctx = [
        LLMMessage(role="user", content="old", conversation_id="c1",
                   msg_id="m-old"),
        LLMMessage(role="user", conversation_id="c1",
                   msg_id="m-current",
                   content=[
                       {"type": "text", "text": "with image"},
                       {"type": "image_ref", "file_id": "fid-x"},
                   ]),
    ]

    found = _find_msg_in_context(ctx, "m-current")
    assert found is ctx[1]
    assert _find_msg_in_context(ctx, "m-absent") is None
    assert _find_msg_in_context(ctx, "") is None
    # Mark the canonical row as the current prompt without injecting a copy.
    found._pawflow_current_user_message = True
    assert [m.msg_id for m in ctx] == ["m-old", "m-current"]


def test_agentctx_p3_reuses_prepersisted_message_instead_of_duplicating():
    """Source-level guard: _pac_p3 must look up the current user msg_id in
    the already-loaded context and reuse it (skip the inject) rather than
    appending a second copy — otherwise the same upload produces two
    image_refs (different file_ids) and the vision fallback describes the
    same image twice."""
    import re
    from pathlib import Path

    src = re.sub(r"\bst\.", "", Path(
        "tasks/ai/_agentctx_p3.py").read_text(encoding="utf-8"))
    assert "_find_msg_in_context(" in src
    assert "_existing_user_msg._pawflow_current_user_message = True" in src
    assert "reusing it, not re-injecting (no duplicate)" in src
    # Injection happens only when the row is absent from loaded context.
    assert "if _append_user_message:" in src


def test_vision_describe_never_uses_thinking_as_description(monkeypatch):
    """The vision model's thinking events (reasoning narration like
    'I need to describe...') must NEVER leak into the description. When the
    content is empty but thinking is full, the description must stay empty
    (the fallback treats it as 'no description'), not adopt the reasoning."""
    from core.vision_describe import apply_vision_fallback

    class ThinkingVisionService(FakeVisionService):
        def complete(self, messages, **kwargs):
            self.calls.append((messages, kwargs))
            return SimpleNamespace(
                content="",  # no visible content
                thinking=(
                    "I need to carefully describe this screenshot. "
                    "I'll make sure to mention all coordinates..."
                ),
            )

    svc = ThinkingVisionService()
    _patch_registry(monkeypatch, svc)

    msg = _image_message()
    out = apply_vision_fallback([msg], "vision_svc", source_service_id="glm_svc",
                                user_id="alice", conversation_id="c1")

    assert len(svc.calls) == 1
    assert out[0].content[1]["type"] == "text"
    # The reasoning narration must not appear in the description.
    assert "I need to" not in out[0].content[1]["text"]
    assert "could not be described" in out[0].content[1]["text"]


def test_describe_image_b64_forwards_vision_thinking_budget(monkeypatch):
    """The vision service's own vision_thinking_budget config is forwarded
    to the vision model call, so a reasoning model's narration can be capped
    or disabled from the service settings."""
    import core.vision_describe as vd
    from core.vision_describe import describe_image_b64

    captured = {}

    class ConfVisionService(FakeVisionService):
        config = {"vision_thinking_budget": 0}

        def complete(self, messages, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(content="described")

    svc = ConfVisionService()
    out = describe_image_b64(svc, "image/png", B64, user_id="alice")
    assert out == "described"
    assert captured["thinking_budget"] == 0

    # Un budget explicite (ex. 512) est aussi forwarde.
    svc.config = {"vision_thinking_budget": 512}
    vd._mem_cache.clear()
    describe_image_b64(svc, "image/png", B64, user_id="alice")
    assert captured["thinking_budget"] == 512


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

    # Default (no config): zero means no output ceiling. Use a
    # fresh image so the earlier 4096/1024 entries are not served from cache.
    plain = FakeVisionService()
    fresh_b64 = base64.b64encode(b"default-budget-image").decode("ascii")
    describe_image_b64(plain, "image/png", fresh_b64, user_id="alice")
    assert plain.calls[-1][1]["max_tokens"] == 0


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


def test_tool_result_force_mode_uses_vision_capable_active_service(monkeypatch):
    import core.vision_describe as vd
    from core.service_registry import ServiceRegistry

    class VisionClient:
        supports_vision = True

    class VisionService:
        TYPE = "llmConnection"
        config = {}

        def get_client(self):
            return VisionClient()

    class Registry:
        def resolve(self, service_id, user_id="", conv_id=""):
            assert service_id == "agent-vision"
            return VisionService()

    monkeypatch.setattr(ServiceRegistry, "get_instance", lambda: Registry())
    monkeypatch.setattr(
        vd, "describe_image_b64",
        lambda service, mime, b64, **kwargs: "A blue application window.",
    )
    marker = "Image: screen.png\n__image_data__:image/png:aW1hZ2U="

    assert vd.describe_tool_result_images(
        marker, agent_svc="agent-vision") is None
    described = vd.describe_tool_result_images(
        marker, agent_svc="agent-vision", force=True)

    assert "A blue application window." in described
    assert "__image_data__:" not in described


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
    original._pawflow_current_user_message = True
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


def test_has_current_vision_inputs_without_marker_uses_last_user_message():
    """The marker can be lost when provider context builders rebuild the
    prompt message (identity / dynamic-metadata injection). The most recent
    user message must still be treated as the active prompt — otherwise the
    vision fallback silently stops running and raw images reach a
    non-vision LLM (provider 400 on parallel see/read)."""
    from core.llm_client import LLMMessage
    from core.vision_describe import has_current_vision_inputs

    # No marker at all: the last user message carries an image -> eligible.
    msg = LLMMessage(role="user", conversation_id="c1", content=[
        {"type": "image_ref", "file_id": "img-1"}])
    assert has_current_vision_inputs([msg]) is True

    # Older user image is NOT current when a more recent user message
    # (without image) exists.
    old = LLMMessage(role="user", conversation_id="c1", content=[
        {"type": "image_ref", "file_id": "img-old"}])
    cur = LLMMessage(role="user", conversation_id="c1", content=[
        {"type": "text", "text": "hi"}])
    assert has_current_vision_inputs([old, cur]) is False

    # The marked prompt still wins over a more recent unmarked user message.
    # That is the cancel case: an unmarked user resume row persisted AFTER the
    # real prompt is not a new prompt, and must not move the boundary past it.
    marked = LLMMessage(role="user", conversation_id="c1", content=[
        {"type": "image_ref", "file_id": "img-marked"}])
    marked._pawflow_current_user_message = True
    later = LLMMessage(role="user", conversation_id="c1", content=[
        {"type": "text", "text": "after"}])
    assert has_current_vision_inputs([later, marked]) is True

    # But a LATER user message that bears images is a real visual prompt whose
    # marker a rebuild dropped. A stale marker on an older message must not
    # send its images raw to a non-vision LLM.
    stale = LLMMessage(role="user", conversation_id="c1", content=[
        {"type": "text", "text": "previous turn"}])
    stale._pawflow_current_user_message = True
    rebuilt = LLMMessage(role="user", conversation_id="c1", content=[
        {"type": "image_ref", "file_id": "img-rebuilt"}])
    assert has_current_vision_inputs([stale, rebuilt]) is True


def test_apply_vision_fallback_without_marker_describes_last_user_image(monkeypatch):
    """Integration: a rebuilt prompt (marker lost) still gets its image
    replaced by the vision description — never sent raw to the LLM."""
    from core.llm_client import LLMMessage
    from core.vision_describe import apply_vision_fallback

    svc = FakeVisionService()
    _patch_registry(monkeypatch, svc)

    msg = LLMMessage(role="user", conversation_id="c1", content=[
        {"type": "text", "text": "look"},
        {"type": "image_url",
         "image_url": {"url": f"data:image/png;base64,{B64}"}},
    ])
    # NOTE: no _pawflow_current_user_message marker — as after a rebuild.

    out = apply_vision_fallback([msg], "vision_svc",
                                source_service_id="glm_svc",
                                user_id="alice", conversation_id="c1")

    assert len(svc.calls) == 1
    assert out[0].content[0]["type"] == "text"
    assert out[0].content[1]["type"] == "text"
    assert "red button" in out[0].content[1]["text"]
    assert not any(p.get("type") in ("image_url", "image_ref")
                   for p in out[0].content)


def test_alc_carry_pawflow_attrs_preserves_marker_on_rebuild():
    """Provider-context rebuilds construct fresh LLMMessage objects; the
    dynamic flags driving the vision fallback must survive them."""
    from core.llm_client import LLMMessage
    from tasks.ai._alc_closures2 import _alc_carry_pawflow_attrs

    src = LLMMessage(role="user", content="x", conversation_id="c1")
    src._pawflow_current_user_message = True
    src._pawflow_other = 42
    src.normal_attr = "not-carried"

    dst = LLMMessage(role="user", content="y", conversation_id="c1")
    out = _alc_carry_pawflow_attrs(src, dst)

    assert out is dst
    assert dst._pawflow_current_user_message is True
    assert dst._pawflow_other == 42
    assert not hasattr(dst, "normal_attr")


def test_alc_inject_dynamic_metadata_keeps_current_user_marker():
    """The exact rebuild that used to drop the marker: dynamic-metadata
    injection replaces the last user message with a fresh LLMMessage."""
    from core.llm_client import LLMMessage
    from tasks.ai._alc_closures2 import _ALCClosures2Mixin

    msg = LLMMessage(role="user", conversation_id="c1", content=[
        {"type": "image_ref", "file_id": "img-1"}])
    msg._pawflow_current_user_message = True

    st = SimpleNamespace(
        ctx={"_datetime_str": "2026-01-01", "_dynamic_blocks": []},
        conversation_id="c1",
        _max_ctx=1000,
        tool_defs=[],
    )
    core = object.__new__(_ALCClosures2Mixin)
    core._estimate_tokens = lambda *a, **k: 10

    out = core._alc_inject_dynamic_metadata(st, [msg])
    rebuilt = out[0]

    assert rebuilt is not msg
    assert getattr(rebuilt, "_pawflow_current_user_message", False) is True
    assert any(p.get("type") == "image_ref" for p in rebuilt.content)

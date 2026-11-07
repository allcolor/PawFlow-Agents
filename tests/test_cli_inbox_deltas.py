"""Queued human input survives a trailing inbox notice on legacy CLI resumes."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.llm_client import LLMClient, LLMMessage

NOTICE = "[PawFlow inbox: ACTION REQUIRED] Call read_inbox() NOW."


def _msg(role, content, **kwargs):
    return LLMMessage(role=role, content=content, conversation_id="conv", **kwargs)


def _queued_messages(human_count):
    humans = [_msg("user", [{"type": "text", "text": f"human {i}"},
                            {"type": "text", "text": "request"}])
              for i in range(human_count)]
    messages = [
        _msg("system", "system policy"),
        _msg("user", "already answered"),
        _msg("assistant", "previous answer"),
        humans[0],
        _msg("system", "internal system body"),
        _msg("tool", "private tool body", tool_call_id="tc"),
        *humans[1:],
        _msg("user", "display row", display_only=True),
        _msg("user", NOTICE),
    ]
    expected = "\n\n".join([f"human {i} request" for i in range(human_count)] + [NOTICE])
    return messages, expected


@pytest.mark.parametrize("provider,method", [
    ("gemini", "_gemini_acp_resume_text"),
    ("codex-app-server", "_codex_app_resume_text"),
    ("antigravity-interactive", "_agi_live_text"),
])
@pytest.mark.parametrize("human_count", [1, 2])
def test_resume_keeps_humans_before_notice_and_excludes_non_user_bodies(
        provider, method, human_count):
    messages, expected = _queued_messages(human_count)
    client = LLMClient(provider, config={"api_key": "test"})
    assert getattr(client, method)(messages) == expected


class _PromptCaptured(Exception):
    pass


def _cc_resume_payload(messages, tmp_path, monkeypatch):
    """Drive the real warm stream branch through its outgoing JSON payload."""
    client = LLMClient("claude-code", config={"api_key": "test"})
    client._agent_service = "svc"
    client._pawflow_context_is_delta = True
    proc = MagicMock()
    proc._pf_pid = 0
    live = SimpleNamespace(proc=proc, pool_container="pool", mcp_internal_token="",
                           session_id="session", turn_lock=MagicMock(),
                           reuse_count=0, spawn_at=0)
    registry = MagicMock()
    registry.get.return_value = live
    store = MagicMock()
    store.get_extra.side_effect = lambda _conv, key: "session" if key.startswith("claude_session:") else -1
    monkeypatch.setattr("core.cc_live_registry.LiveSessionRegistry.instance", lambda: registry)
    monkeypatch.setattr("core.conversation_store.ConversationStore.instance", lambda: store)
    monkeypatch.setattr(client, "_get_session_workdir", lambda *args: str(tmp_path))
    monkeypatch.setattr(client, "_cc_namespace_workdir", lambda _path: "/cc_sessions/conv/a")
    monkeypatch.setattr(client, "_build_catchup_context", lambda *args: "")
    payloads = []

    def capture(_proc, payload):
        payloads.append(json.loads(payload))
        raise _PromptCaptured()

    monkeypatch.setattr("core.llm_providers._cc_native_input.write_message", capture)
    with pytest.raises(_PromptCaptured):
        client._stream_claude_code(
            messages, "model", 0.7, 0, None, call_user_id="u",
            call_conversation_id="conv", call_agent_name="a")
    return payloads[0]["message"]["content"]


@pytest.mark.parametrize("human_count", [1, 2])
def test_claude_code_warm_stream_keeps_all_humans_before_notice(
        human_count, tmp_path, monkeypatch):
    messages, expected = _queued_messages(human_count)
    payload = _cc_resume_payload(messages, tmp_path, monkeypatch)
    # CC keeps its existing system envelope, but only user bodies enter the delta.
    assert payload.split("</system_instructions>\n\n", 1)[1] == expected
    assert "internal system body" not in payload
    assert "private tool body" not in payload
    assert "already answered" not in payload


def _image_messages():
    def image_ref(file_id):
        return [{"type": "image_ref", "file_id": file_id, "filename": file_id + ".png"}]

    return [
        _msg("user", image_ref("old")),
        _msg("assistant", "previous answer"),
        _msg("user", [{"type": "text", "text": "first image"}, *image_ref("current")]),
        _msg("system", image_ref("system")),
        _msg("user", [{"type": "text", "text": "second image"},
                      {"type": "image_url", "image_url": {"url": "data:image/png;base64,SU5MSU5F"}},
                      {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                       "data": "U09VUkNF"}}]),
        _msg("user", image_ref("display"), display_only=True),
        _msg("user", NOTICE),
    ]


@pytest.fixture
def image_store(monkeypatch):
    store = MagicMock()
    store.get_required.return_value = ("current.png", b"PNG", "image/png")
    monkeypatch.setattr("core.file_store.FileStore.instance", lambda: store)
    return store


@pytest.mark.parametrize("provider,extractor,resume", [
    ("gemini", "_gemini_acp_extract_images", "_gemini_acp_resume_text"),
    ("codex-app-server", "_codex_app_extract_images", "_codex_app_resume_text"),
])
def test_resume_native_images_cover_every_human_before_notice(
        provider, extractor, resume, image_store, tmp_path):
    client = LLMClient(provider, config={"api_key": "test"})
    messages = _image_messages()
    blocks = getattr(client, extractor)(messages, user_id="u", conversation_id="conv")
    assert [block["source"]["data"] for block in blocks] == ["UE5H", "SU5MSU5F", "U09VUkNF"]
    image_store.get_required.assert_called_once_with("current", user_id="u", conversation_id="conv")
    text = getattr(client, resume)(messages)
    assert text.index("first image") < text.index("second image") < text.index(NOTICE)
    if provider == "gemini":
        items = client._gemini_acp_prompt_items(text, blocks)
        assert [item["data"] for item in items if item["type"] == "image"] == ["UE5H", "SU5MSU5F", "U09VUkNF"]
    else:
        items = client._codex_app_input_items(text, blocks, str(tmp_path), "/cc_sessions/conv/a")
        assert len([item for item in items if item["type"] == "localImage"]) == 3


def test_claude_code_warm_stream_keeps_native_human_images_before_notice(
        image_store, tmp_path, monkeypatch):
    payload = _cc_resume_payload(_image_messages(), tmp_path, monkeypatch)
    assert [block["source"]["data"] for block in payload[1:]] == ["UE5H", "SU5MSU5F", "U09VUkNF"]
    text = payload[0]["text"]
    assert text.index("first image") < text.index("second image") < text.index(NOTICE)
    image_store.get_required.assert_called_once_with("current", user_id="u", conversation_id="conv")


def test_antigravity_resume_prompt_keeps_human_images_before_notice(image_store, tmp_path):
    client = LLMClient("antigravity-interactive")
    prompt = client._agi_prompt(_image_messages(), None, str(tmp_path),
                               "/cc_sessions/conv/a", "u", "conv")
    assert "fs://filestore/current/current.png" in prompt
    assert "@/cc_sessions/conv/a/.pawflow_vision/current.png" in prompt
    assert prompt.index("first image") < prompt.index("second image") < prompt.index(NOTICE)
    assert sorted(path.read_bytes() for path in (tmp_path / ".pawflow_vision").iterdir()) == [b"INLINE", b"PNG"]
    image_store.get_required.assert_called_once_with("current", user_id="u", conversation_id="conv")


def test_antigravity_resume_does_not_fall_back_to_history_without_user_tail(tmp_path):
    client = LLMClient("antigravity-interactive")
    messages = [_msg("user", "already answered"), _msg("assistant", "answer"),
                _msg("system", "internal body"), _msg("user", "display row", display_only=True)]
    assert client._agi_prompt(messages, None, str(tmp_path), "/cc_sessions/conv/a", "u", "conv").strip() == ""


class _ProviderCalled(BaseException):
    """Stop at the provider boundary without invoking the LLM error handlers."""


def _main_loop_provider_messages(provider, warm, human_count, monkeypatch):
    from tasks.ai.agent_core import AgentCoreMixin

    loop = AgentCoreMixin()
    client = SimpleNamespace(config={}, complete_stream=MagicMock(side_effect=_ProviderCalled))
    messages, _expected = _queued_messages(human_count)
    messages[3].content.append({"type": "image_url", "image_url": {"url": "data:image/png;base64,UE5H"}})
    queued = messages[3:]
    stored = messages[:3]
    emitter = MagicMock(is_streaming=True)
    emitter.check_interrupt.return_value = False
    emitter.drain_pending.side_effect = lambda target, _append, _iteration: target.extend(queued)
    st = SimpleNamespace(
        messages=stored, emitter=emitter, client=client, ctx={
            "active_agent_name": "a", "_cli_has_session": warm,
            "_is_claude_code": provider == "claude-code", "max_iterations": 1,
            "temperature": 0.7, "max_tokens": 0,
        }, conversation_id="conv", user_id="u", iteration=0, current_round=1,
        max_rounds=1, tools_called=[], _append=stored.append,
        _compact_threshold_fraction=lambda _value: 0, _client_provider=provider,
        _budget_precheck_done=True, total_tokens_in=0, total_tokens_out=0,
        total_cache_read=0, total_cache_write=0, model="model", tool_defs=[],
    )
    # Keep context decoration and compaction inert; run the actual iteration,
    # warm-context selection, LLM turn and provider dispatch.
    monkeypatch.setattr(loop, "_alc_should_proactive_compact", lambda *args: False)
    monkeypatch.setattr(loop, "_alc_build_provider_context", lambda _st, msgs: (list(msgs), 0))
    monkeypatch.setattr(loop, "_alc_inject_dynamic_metadata", lambda _st, msgs: msgs)
    monkeypatch.setattr(loop, "_inject_identity", lambda msgs, _names: msgs, raising=False)
    monkeypatch.setattr(loop, "_apply_identity_suffix", lambda msgs, _suffix: msgs, raising=False)
    monkeypatch.setattr(loop, "_microcompact_time_based", lambda _msgs: None, raising=False)
    monkeypatch.setattr(loop, "_estimate_tokens", lambda *args, **kwargs: 0, raising=False)
    monkeypatch.setattr(loop, "_alc_apply_vision_fallback", lambda _st, msgs, _scope: msgs)
    monkeypatch.setattr("core.agent_inbox.turn_mode_messages", lambda *args: [])
    with pytest.raises(_ProviderCalled):
        loop._alc_iteration(st)
    return client.complete_stream.call_args.kwargs["messages"], messages


@pytest.mark.parametrize("provider", [
    "claude-code", "claude-code-interactive", "antigravity-interactive",
    "codex-app-server", "codex-interactive", "gemini", "cc_mcp", "codex_mcp", "agy_mcp",
])
@pytest.mark.parametrize("human_count", [1, 2])
def test_outer_warm_cli_loop_passes_every_queued_human_and_image_before_notice(
        provider, human_count, monkeypatch):
    actual, messages = _main_loop_provider_messages(provider, True, human_count, monkeypatch)
    expected = [msg for msg in messages[3:] if msg.role == "user" and not msg.display_only]
    assert [msg.msg_id for msg in actual] == [msg.msg_id for msg in expected]
    assert actual[0].content[-1] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,UE5H"}}
    assert actual[-1].content == NOTICE


@pytest.mark.parametrize("provider,warm", [("gemini", False), ("openai", True)])
def test_outer_loop_keeps_full_context_for_cold_cli_and_api(provider, warm, monkeypatch):
    actual, messages = _main_loop_provider_messages(provider, warm, 1, monkeypatch)
    assert [msg.msg_id for msg in actual] == [msg.msg_id for msg in messages]


@pytest.mark.parametrize("provider", [
    "claude-code", "claude-code-interactive", "antigravity-interactive",
    "codex-app-server", "codex-interactive", "gemini", "cc_mcp", "codex_mcp", "agy_mcp",
    "openai",
])
def test_cli_completion_does_not_continue_with_an_empty_delta_for_background_work(
        provider, monkeypatch):
    from core.llm_client import LLMResponse
    from tasks.ai._alc_base import _ALC_BREAK, _ALC_CONTINUE
    from tasks.ai.agent_core import AgentCoreMixin

    loop = AgentCoreMixin()
    messages = [_msg("user", "Original request")]
    emitter = MagicMock(is_streaming=True)
    emitter.check_interrupt.return_value = False
    st = SimpleNamespace(
        messages=messages, emitter=emitter,
        client=SimpleNamespace(config={}, _last_turn_visible_msg_id="answer"),
        ctx={"active_agent_name": "a", "_cli_has_session": True,
             "_claude_has_session": True, "max_iterations": 10},
        conversation_id="conv", user_id="u", iteration=0, current_round=1,
        max_rounds=1, tools_called=[], _append=messages.append,
        _compact_threshold_fraction=lambda _: 0, _client_provider=provider,
        _budget_precheck_done=True, total_tokens_in=0, total_tokens_out=0,
        total_cache_read=0, total_cache_write=0, model="model", tool_defs=[],
        _schedule_cc_turn_gauge_patch=MagicMock(), all_assistant_msg_ids=["answer"],
    )
    for name in ("_alc_should_proactive_compact", "_microcompact_time_based",
                 "_deflate_image_messages", "_alc_release_active_after_terminal_visible_answer"):
        monkeypatch.setattr(loop, name, lambda *args, **kwargs: False, raising=False)
    monkeypatch.setattr(loop, "_alc_build_provider_context", lambda _st, msgs: (list(msgs), 0))
    monkeypatch.setattr(loop, "_alc_inject_dynamic_metadata", lambda _st, msgs: msgs)
    monkeypatch.setattr(loop, "_inject_identity", lambda msgs, _: msgs, raising=False)
    monkeypatch.setattr(loop, "_apply_identity_suffix", lambda msgs, _: msgs, raising=False)
    monkeypatch.setattr(loop, "_estimate_tokens", lambda *args, **kwargs: 0, raising=False)
    monkeypatch.setattr("core.agent_inbox.turn_mode_messages", lambda *args: [])
    monkeypatch.setattr("tasks.ai._alc_iteration._apply_bg_results", lambda *args: None)
    monkeypatch.setattr("core.background_tool.has_pending", lambda *args: True)
    wait = MagicMock(return_value=1)
    monkeypatch.setattr("core.background_tool.wait_pending", wait)
    deltas = []

    def complete(state):
        deltas.append([msg.content for msg in state._call_context])
        state.response = LLMResponse()
        state.messages.append(_msg("assistant", "Completed visible answer"))

    monkeypatch.setattr(loop, "_alc_llm_turn", complete)
    signal = loop._alc_iteration(st)

    assert deltas == [["Original request"]]
    if provider == "openai":
        assert signal is _ALC_CONTINUE
        wait.assert_called_once_with("conv", "a", cancel_check=emitter.check_cancelled)
    else:
        assert signal is _ALC_BREAK
        wait.assert_not_called()

"""Regression for API compaction loops after a native request measurement."""
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.llm_client import LLMClient, LLMMessage
from tasks.ai._alc_closures1 import _ALCClosures1Mixin
from tasks.ai._alc_closures2 import _ALCClosures2Mixin
from tasks.ai.agent_emitter import StreamEmitter
from tasks.ai.agent_serialization import AgentSerializationMixin
from tasks.ai.context_usage import compute_context_usage


class _CompactingLoop(_ALCClosures1Mixin, _ALCClosures2Mixin,
                      AgentSerializationMixin):
    pass


@pytest.fixture
def compacting_api(monkeypatch):
    from tasks.ai import context_usage
    from tasks.ai.agent_loop import AgentLoopTask

    cid, name = "conv-compact", "GameDev7"
    client = LLMClient("openai", {"api_key": "test", "max_retries": 1})
    client._record_observed_context(cid, name, 614_000, mode="request")
    client._record_observed_context(cid, "other", 50_000, mode="request")
    messages = [LLMMessage(role="user", content="old history " * 100,
                           conversation_id=cid) for _ in range(3)]
    ctx = {
        "active_agent_name": name, "active_llm_provider": "openai",
        "client": client, "messages": messages, "max_context_size": 400_000,
        "resolved_svc": SimpleNamespace(config={"max_context_size": 400_000}),
    }
    loop = _CompactingLoop()
    st = SimpleNamespace(
        conversation_id=cid, user_id="u1", ctx=ctx, messages=messages,
        client=client.clone_for_call(), compact_client=client,
        use_conv_store=True, new_messages=[messages[-1]], base_count=3,
        _auto_compact_state={}, _client_provider="openai",
        _agent_compact_threshold_fraction=lambda: 0.95,
    )
    st._set_provider_compact_barrier = (
        lambda reason: loop._alc_set_provider_compact_barrier(st, reason))
    st._clear_provider_compact_barrier = (
        lambda: loop._alc_clear_provider_compact_barrier(st))
    st._adopt_compacted_context = (
        lambda messages, **kw: loop._alc_adopt_compacted_context(st, messages, **kw))
    st.emitter = StreamEmitter(cid, Mock(), ctx, loop, "gen", 1)
    live = SimpleNamespace(
        _active_contexts={f"{cid}:{name}": ctx},
        _active_contexts_lock=threading.RLock(),
        _active_claude_client={f"{cid}:{name}": st.client},
    )
    monkeypatch.setattr(AgentLoopTask, "_live_instance", live)
    monkeypatch.setattr("core.conversation_store.ConversationStore.instance",
                        Mock(return_value=Mock()))
    monkeypatch.setattr(context_usage, "persist_context_usage_async", Mock())
    usage = compute_context_usage(cid, name, source="test")
    assert usage["used"] == 614_000
    return loop, st


@pytest.mark.parametrize("message_delta", [-1, 0, 2])
def test_api_post_append_compact_is_adopted_once(compacting_api, message_delta):
    loop, st = compacting_api
    compacted = [LLMMessage(role="user", content="compact summary",
                            conversation_id=st.conversation_id)
                 for _ in range(len(st.messages) + message_delta)]
    loop._compact = Mock(return_value=compacted)
    original_list = st.messages
    result = LLMMessage(role="tool", content="result",
                        conversation_id=st.conversation_id)

    loop._alc_maybe_auto_compact_after_append(st, result, "tool")

    assert st.messages is original_list
    assert st.messages == compacted
    assert st.base_count == len(compacted)
    assert st.new_messages == []
    usage = compute_context_usage(st.conversation_id, "GameDev7", source="test")
    assert usage["used"] < 380_000
    assert not usage.get("context_source_measured")
    st.ctx["_context_usage_cache"] = usage
    st.messages.append(result)
    st.emitter._publish_context_usage("append")
    loop._alc_maybe_auto_compact_after_append(st, result, "tool")
    assert loop._compact.call_count == 1
    assert not st._auto_compact_state["running"]


def test_api_context_adoption_retires_only_its_old_request_sample(compacting_api):
    loop, st = compacting_api
    compacted = [LLMMessage(role="user", content="compact summary",
                            conversation_id=st.conversation_id)]

    st._adopt_compacted_context(compacted, reason="pre_send")

    key = (st.conversation_id, "GameDev7")
    assert key not in st.client._cli_observed_context_tokens_by_stream
    assert st.client._cli_observed_context_tokens_by_stream[
        (st.conversation_id, "other")] == 50_000
    assert st.ctx["_context_usage_cache"]["source"] == "compact_post"
    assert st.ctx["_context_usage_cache"]["used"] < 380_000
    st.client._record_observed_context(*key, 12_345, mode="request")
    usage = compute_context_usage(*key, source="next_request")
    assert usage["used"] == 12_345
    assert usage["context_measurement_revision"] == 2

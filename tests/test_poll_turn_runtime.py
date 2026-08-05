"""Scheduled agent wakeups have a first-class runtime turn identity."""

import inspect
import sys
import threading
from types import SimpleNamespace
import uuid

from tasks.ai._agent_streaming_loop import _AgentStreamingLoopMixin
from tasks.ai._agent_media import _AgentMediaMixin
from tasks.ai.agent_poller import AgentPollerMixin, _poll_generation_key


class _PollLoopHarness(_AgentStreamingLoopMixin, _AgentMediaMixin):
    def __init__(self, conversation_id):
        self._active_contexts_lock = threading.Lock()
        self._active_turns = {}
        self._active_claude_client = {}
        self._active_lock = threading.Lock()
        self._active_conversations = {conversation_id: 1}
        self._user_active_conversations = set()
        self._active_thoughts = set()
        self.seen = None

    def _streaming_agent_loop_inner(self, ctx, conversation_id, _bus):
        self.seen = (dict(ctx), dict(self._active_turns), conversation_id)

    def _is_current_generation(self, _key, _generation):
        return True


def _isolate_wrapper_cleanup(monkeypatch):
    monkeypatch.setitem(sys.modules, "core.background_tool", SimpleNamespace(
        list_tasks=lambda _conversation_id: [],
        cancel=lambda _tc_id: None,
        pop_completed=lambda _conversation_id, _tc_id: None,
    ))

    class _EmptyPendingQueue:
        @classmethod
        def for_agent(cls, _conversation_id, _agent_name):
            return cls()

        def peek_count(self):
            return 0

    monkeypatch.setitem(sys.modules, "core.pending_queue", SimpleNamespace(
        PendingQueue=_EmptyPendingQueue,
    ))


def test_poll_generation_key_matches_the_active_turn_key():
    assert _poll_generation_key("conv-1", "assistant") == "conv-1:assistant"
    assert _poll_generation_key("conv-1", "") == "conv-1"


def test_poll_allocates_generation_after_resolving_the_agent():
    source = inspect.getsource(AgentPollerMixin._poll_once)
    build_at = source.index("ctx = self._build_poll_context")
    key_at = source.index("_gen_key = _poll_generation_key(")
    bump_at = source.index("with self._conv_gen_lock:", key_at)

    assert build_at < key_at < bump_at
    assert 'self._conv_generation.get(_gen_key, 0) + 1' in source
    assert 'self._conv_generation[_gen_key] = gen' in source
    assert 'ctx["_gen_key"] = _gen_key' in source


def test_poll_loop_registers_distinct_runtime_turn_and_cleans_it_up(monkeypatch):
    _isolate_wrapper_cleanup(monkeypatch)
    conversation_id = f"poll-runtime-{uuid.uuid4().hex}"
    loop = _PollLoopHarness(conversation_id)
    ctx = {
        "is_poll": True,
        "active_agent_name": "assistant",
        "_generation": 4,
        "_scheduled_reasons": [
            "[scheduled:assistant] [continuation] finish release validation",
        ],
    }

    loop._streaming_agent_loop(ctx, conversation_id, object())

    seen_ctx, active, conversation_id = loop.seen
    turn_id = seen_ctx["request_msg_id"]
    uuid.UUID(turn_id)
    assert conversation_id == seen_ctx["_active_turn_key"].split(":", 1)[0]
    assert seen_ctx["_active_turn_key"] == f"{conversation_id}:assistant"
    marker = active[f"{conversation_id}:assistant"]
    assert marker["turn_id"] == turn_id
    assert marker["owner_type"] == "poll_worker"
    assert marker["message_preview"] == "finish release validation"
    assert loop._active_turns == {}


def test_non_poll_loop_does_not_replace_streaming_worker_identity(monkeypatch):
    _isolate_wrapper_cleanup(monkeypatch)
    conversation_id = f"poll-runtime-{uuid.uuid4().hex}"
    loop = _PollLoopHarness(conversation_id)
    ctx = {"active_agent_name": "assistant", "request_msg_id": "user-turn"}

    loop._streaming_agent_loop(ctx, conversation_id, object())

    seen_ctx, active, _ = loop.seen
    assert seen_ctx["request_msg_id"] == "user-turn"
    assert active == {}


def test_poll_loop_preserves_a_foreign_active_turn_owner(monkeypatch):
    _isolate_wrapper_cleanup(monkeypatch)
    conversation_id = f"poll-runtime-{uuid.uuid4().hex}"
    loop = _PollLoopHarness(conversation_id)
    foreign = {
        "conversation_id": conversation_id,
        "agent_name": "assistant",
        "turn_id": "captured-turn",
        "owner_id": "capture-owner",
        "owner_type": "cci_capture",
        "generation": 4,
    }
    loop._active_turns[f"{conversation_id}:assistant"] = foreign
    ctx = {
        "is_poll": True,
        "active_agent_name": "assistant",
        "_generation": 4,
        "_scheduled_reasons": ["[continuation] resume"],
    }

    loop._streaming_agent_loop(ctx, conversation_id, object())

    assert loop.seen[1][f"{conversation_id}:assistant"] == foreign
    assert loop._active_turns[f"{conversation_id}:assistant"] == foreign

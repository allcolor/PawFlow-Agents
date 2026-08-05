"""Scheduled agent wakeups have a first-class runtime turn identity."""

import threading
import uuid

from tasks.ai._agent_streaming_loop import _AgentStreamingLoopMixin
from tasks.ai._agent_media import _AgentMediaMixin


class _PollLoopHarness(_AgentStreamingLoopMixin, _AgentMediaMixin):
    def __init__(self):
        self._active_contexts_lock = threading.Lock()
        self._active_turns = {}
        self._active_claude_client = {}
        self._active_lock = threading.Lock()
        self._active_conversations = {"conv-1": 1}
        self._user_active_conversations = set()
        self._active_thoughts = set()
        self.seen = None

    def _streaming_agent_loop_inner(self, ctx, conversation_id, _bus):
        self.seen = (dict(ctx), dict(self._active_turns), conversation_id)

    def _is_current_generation(self, _key, _generation):
        return True


def test_poll_loop_registers_distinct_runtime_turn_and_cleans_it_up():
    loop = _PollLoopHarness()
    ctx = {
        "is_poll": True,
        "active_agent_name": "assistant",
        "_generation": 4,
        "_scheduled_reasons": [
            "[scheduled:assistant] [continuation] finish release validation",
        ],
    }

    loop._streaming_agent_loop(ctx, "conv-1", object())

    seen_ctx, active, conversation_id = loop.seen
    turn_id = seen_ctx["request_msg_id"]
    uuid.UUID(turn_id)
    assert conversation_id == "conv-1"
    assert seen_ctx["_active_turn_key"] == "conv-1:assistant"
    marker = active["conv-1:assistant"]
    assert marker["turn_id"] == turn_id
    assert marker["owner_type"] == "poll_worker"
    assert marker["message_preview"] == "finish release validation"
    assert loop._active_turns == {}


def test_non_poll_loop_does_not_replace_streaming_worker_identity():
    loop = _PollLoopHarness()
    ctx = {"active_agent_name": "assistant", "request_msg_id": "user-turn"}

    loop._streaming_agent_loop(ctx, "conv-1", object())

    seen_ctx, active, _ = loop.seen
    assert seen_ctx["request_msg_id"] == "user-turn"
    assert active == {}


def test_poll_loop_preserves_a_foreign_active_turn_owner():
    loop = _PollLoopHarness()
    foreign = {
        "conversation_id": "conv-1",
        "agent_name": "assistant",
        "turn_id": "captured-turn",
        "owner_id": "capture-owner",
        "owner_type": "cci_capture",
        "generation": 4,
    }
    loop._active_turns["conv-1:assistant"] = foreign
    ctx = {
        "is_poll": True,
        "active_agent_name": "assistant",
        "_generation": 4,
        "_scheduled_reasons": ["[continuation] resume"],
    }

    loop._streaming_agent_loop(ctx, "conv-1", object())

    assert loop.seen[1]["conv-1:assistant"] == foreign
    assert loop._active_turns["conv-1:assistant"] == foreign

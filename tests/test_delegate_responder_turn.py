"""A delegate-mode turn is the responder's own activity; one reply closes it.

The responder's rows used to be re-stamped as agent_delegate replies, and every
text segment of a CLI turn became a ``final_reply`` (text and tool calls are
flushed separately, so no segment can be known to be final when appended). The
webchat drew the whole turn, tool calls included, inside the delegate frame.
"""

import inspect
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.llm_client import LLMMessage
from tasks.ai.agent_core import AgentCoreMixin
from tasks.ai.agent_loop import AgentLoopTask


def _append(message, turn_mode):
    task = AgentLoopTask({"api_key": "test-key"})
    writer = MagicMock()
    state = SimpleNamespace(
        ctx={"active_agent_name": "GameDev8", "_turn_mode": turn_mode},
        emitter=MagicMock(_current_msg_id="", is_streaming=False),
        messages=[], new_messages=[], all_assistant_msg_ids=[],
        conversation_id="conv-delegate", user_id="alice", use_conv_store=True,
        _maybe_auto_compact_after_append=lambda *args: None,
    )
    with patch("core.agent_hooks.AgentHookRunner") as hooks, patch(
            "core.conversation_writer.ConversationWriter.for_conversation",
            return_value=writer):
        hooks.return_value.run.return_value = {"decision": "allow"}
        task._alc_append(state, message)
    return writer.enqueue_message.call_args.args[0]["source"]


DELEGATE_TURN = {"type": "delegate_reply", "source_agent": "GameDev2",
                 "task_id": "3416df6b"}


def test_delegate_turn_text_is_the_responders_private_activity():
    source = _append(LLMMessage(
        role="assistant", content="This is a new job.",
        source={"type": "agent", "name": "GameDev8", "provider": "cci",
                "model": "haiku", "delegate_visibility": "self_only"},
        conversation_id="conv-delegate"), DELEGATE_TURN)

    assert source == {
        "provider": "cci", "model": "haiku",
        "type": "agent", "name": "GameDev8", "visibility": "target_only",
        "delegate_reply_to": "GameDev2", "delegate_task_id": "3416df6b",
    }


def test_delegate_turn_tool_result_is_the_responders_private_activity():
    source = _append(LLMMessage(
        role="tool", content="out", tool_call_id="call_1",
        conversation_id="conv-delegate"), DELEGATE_TURN)

    assert source["type"] == "agent"
    assert source["name"] == "GameDev8"
    assert source["visibility"] == "target_only"
    assert "kind" not in source


def test_user_turn_source_is_untouched():
    source = _append(LLMMessage(
        role="assistant", content="hello",
        source={"type": "agent", "name": "GameDev8"},
        conversation_id="conv-delegate"), {"type": "user", "source_agent": None})

    assert source == {"type": "agent", "name": "GameDev8"}


def test_end_of_turn_reply_is_persisted_once_for_both_delivery_paths():
    src = inspect.getsource(AgentCoreMixin._run_agent_loop_inner)
    done = src[src.index("# If this was a delegate-reply turn"):]
    reply_src = done[done.index("st._reply_src = {"):done.index("st._reply_mid =")]
    # append_message refuses a user row without target_agent.
    assert '"target_agent": st._src_agent' in reply_src
    persist = done.index("SpawnAgentsHandler._record_delegate_reply(")
    assert persist < done.index("SpawnAgentsHandler._preempt_caller(")
    assert persist < done.index("SpawnAgentsHandler._wake_caller(")
    wake = done[done.index("SpawnAgentsHandler._wake_caller("):]
    assert "pre_persisted=True" in wake[:400]


def test_recorded_reply_is_a_transcript_only_row_published_live(monkeypatch):
    from core.handlers.resource_agent import SpawnAgentsHandler
    writer = MagicMock()
    monkeypatch.setattr(
        "core.conversation_writer.ConversationWriter.for_conversation",
        lambda _cid: writer)
    message = {"role": "user", "content": "the answer", "msg_id": "r1",
               "source": {"type": "agent_delegate", "from": "GameDev8",
                          "to": "GameDev2", "target_agent": "GameDev2",
                          "kind": "reply", "task_id": "t1"}}

    SpawnAgentsHandler._record_delegate_reply("conv", "GameDev2", "alice", message)

    row = writer.enqueue_message.call_args.args[0]
    assert row == {**message, "display_only": True}
    assert "display_only" not in message
    kwargs = writer.enqueue_message.call_args.kwargs
    assert kwargs["agent_name"] == "GameDev2"
    assert kwargs["sse_events"] == [{"type": "new_message", "data": row}]


def test_delegate_frame_closes_on_the_delivered_user_reply():
    render = (Path(__file__).resolve().parents[1] / "tasks" / "io" / "chat_ui"
              / "messages_render.js").read_text(encoding="utf-8")
    start = render.index("function _delegateReplyCompletesFrame(")
    fn = render[start:render.index("\n}\n", start) + 2]
    reply = {"kind": "reply"}
    cases = [
        ["user", "the answer", reply],
        ["assistant", "the answer", reply],
        ["user", "   ", reply],
        ["user", "do X", {"kind": "request"}],
        ["assistant", "x", {"kind": "reply", "delegate_visibility": "self_only"}],
        ["tool", "x", reply],
    ]
    script = (fn + "\nconsole.log(JSON.stringify(" + json.dumps(cases)
              + ".map(c => _delegateReplyCompletesFrame(c[0], c[1], c[2]))));")
    out = subprocess.run(["node", "-e", script], capture_output=True,
                         text=True, check=True)
    assert json.loads(out.stdout) == [True, True, False, False, False, False]

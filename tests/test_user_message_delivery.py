"""/msg and /msg @all deliver the user's message to the live agents now.

Regression (2026-09-29): /msg @agent waited in the PendingQueue for the end of
the running turn, and /msg @all ran throw-away clones of every agent in
sub-conversations -- the live agents never received the message.
"""
import json
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from tasks.ai import _user_message_delivery as delivery

CONV = "conv-msg"


class _Executor:
    """Records ingress calls; each one can be slowed down."""

    def __init__(self, delay=0.0):
        self.delay = delay
        self.calls = []
        self.active = {}
        self.overlap = False
        self.lock = threading.Lock()

    def _execute_streaming(self, flowfile):
        body = json.loads(flowfile.get_content())
        agent = body["target_agent"]
        with self.lock:
            if self.active.get(agent):
                self.overlap = True
            self.active[agent] = True
        time.sleep(self.delay)
        with self.lock:
            self.active[agent] = False
            self.calls.append((agent, body, dict(flowfile.attributes)))
        return [flowfile]

    def wait(self, count):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with self.lock:
                if len(self.calls) >= count:
                    return
            time.sleep(0.01)
        raise AssertionError(f"only {len(self.calls)} of {count} delivered")


def test_a_message_goes_through_the_streaming_ingress():
    executor = _Executor()
    msg_id = delivery.deliver_user_message(
        executor, CONV, "GameDev2", "stop and read this", "alice")
    executor.wait(1)

    agent, body, attrs = executor.calls[0]
    assert agent == "GameDev2"
    assert body == {"message": "stop and read this", "conversation_id": CONV,
                    "target_agent": "GameDev2", "msg_id": msg_id}
    assert attrs["http.auth.principal"] == "alice"
    assert "skip_pre_persist" not in attrs
    assert "broadcast_pre_persisted" not in attrs


def test_messages_to_one_agent_are_delivered_in_order_one_at_a_time():
    executor = _Executor(delay=0.05)
    for index in range(4):
        delivery.deliver_user_message(
            executor, CONV, "GameDev3", f"m{index}", "alice")
    executor.wait(4)

    assert [body["message"] for _a, body, _t in executor.calls] == [
        "m0", "m1", "m2", "m3"]
    assert executor.overlap is False


def test_different_agents_are_served_in_parallel():
    executor = _Executor(delay=0.3)
    started = time.monotonic()
    for agent in ("A1", "A2", "A3"):
        delivery.deliver_user_message(executor, CONV, agent, "hi", "alice")
    executor.wait(3)

    assert time.monotonic() - started < 0.8


def test_a_broadcast_persists_one_all_row_then_reaches_every_agent():
    executor = _Executor()
    writer = MagicMock()
    with patch("core.conversation_access.authorize_message_submission") as auth, \
            patch("core.conversation_writer.ConversationWriter.for_conversation",
                  return_value=writer):
        msg_id = delivery.broadcast_user_message(
            executor, CONV, ["GameDev2", "GameDev5"], "all stop", "alice")
    executor.wait(2)

    auth.assert_called_once_with(CONV, "alice")
    writer.enqueue_message.assert_called_once()
    row = writer.enqueue_message.call_args.args[0]
    assert row["msg_id"] == msg_id
    assert row["content"] == "all stop"
    assert row["source"]["target_agent"] == "ALL"
    assert writer.enqueue_message.call_args.kwargs["agent_name"] == ""
    sse = writer.enqueue_message.call_args.kwargs["sse_events"]
    assert [event["type"] for event in sse] == ["new_message"]

    delivered = sorted((agent, body["msg_id"], attrs["broadcast_pre_persisted"])
                       for agent, body, attrs in executor.calls)
    assert delivered == [("GameDev2", msg_id, "1"), ("GameDev5", msg_id, "1")]


def test_a_refused_broadcast_writes_nothing():
    from core.conversation_access import ConversationAccessError
    executor = _Executor()
    writer = MagicMock()
    with patch("core.conversation_access.authorize_message_submission",
               side_effect=ConversationAccessError("no")), \
            patch("core.conversation_writer.ConversationWriter.for_conversation",
                  return_value=writer):
        with pytest.raises(ConversationAccessError):
            delivery.broadcast_user_message(
                executor, CONV, ["GameDev2"], "x", "mallory")
    writer.enqueue_message.assert_not_called()
    assert executor.calls == []


def test_agent_msg_action_delivers_instead_of_queuing():
    from tasks.ai.actions._agentres_k2 import _handle_agentres_k2
    from core import FlowFile

    task = MagicMock()
    task._resolve_agent_name.side_effect = lambda name, conv: name
    flowfile = FlowFile(content=b"{}")
    with patch("core.conv_agent_config.require_agent_member", return_value=""), \
            patch("tasks.ai.agent_loop.AgentLoopTask._live_instance", task), \
            patch("tasks.ai._user_message_delivery.deliver_user_message",
                  return_value="abc123abc123") as deliver, \
            patch("core.pending_queue.PendingQueue.for_agent") as pending:
        result = _handle_agentres_k2(
            task, "agent_msg",
            {"conversation_id": CONV, "target_agent": "GameDev4",
             "message": "look now"},
            MagicMock(), "alice", flowfile)

    deliver.assert_called_once_with(task, CONV, "GameDev4", "look now", "alice")
    pending.assert_not_called()
    assert json.loads(result[0].get_content())["msg_id"] == "abc123abc123"


def test_msg_all_is_parsed_as_a_broadcast():
    from tasks.ai.actions.command_dispatch import _parse_command
    parsed = _parse_command("/msg @all hello team", CONV, "alice", "claude")
    assert parsed["action"] == "broadcast_agents"
    assert parsed["message"] == "hello team"

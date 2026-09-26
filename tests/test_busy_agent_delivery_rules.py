"""How a message reaches an agent that is already running a turn.

Rules (2026-09-26): a user message is submitted at once WITH interrupt --
every tool call in flight is cancelled first. A /nimsg user message and an
agent trigger of another mode are submitted at once WITHOUT interrupt. None of
them waits for the end of the turn.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from core import FlowFile

CONV = "conv-delivery"
AGENT = "Worker"


class _CliClient:
    supports_live_preempt = True

    def __init__(self, accepts=True):
        self.accepts = accepts
        self.interrupts = []
        self.queued = []

    def send_user_message(self, text, **kwargs):
        self.interrupts.append(text)
        return self.accepts

    def send_queued_message(self, text, **kwargs):
        self.queued.append((text, kwargs.get("msg_id")))
        return self.accepts


class _ApiClient:
    supports_live_preempt = False
    _active_http_conn = None

    def __init__(self):
        self.aborted = False

    def abort(self):
        self.aborted = True


class _Queue:
    def __init__(self):
        self.items = []

    def enqueue(self, message, source=""):
        self.items.append((message.get("msg_id"), source))
        return True


class _HookRunner:
    def __init__(self, **_kwargs):
        pass

    def run(self, *_args, **_kwargs):
        return {"decision": "allow"}


def _deliver(client, body_extra=None, running_mode=None, source=None):
    from tasks.ai.agent_loop import AgentLoopTask

    task = AgentLoopTask({
        "api_key": "test", "streaming": True, "conversation_store": False})
    key = f"{CONV}:{AGENT}"
    with task._active_contexts_lock:
        task._active_turns[key] = {"agent": AGENT}
        task._active_contexts[key] = {
            "_turn_mode": running_mode or {"type": "user",
                                           "source_agent": None}}
        task._active_claude_client[key] = client
    body = {"message": "hello", "conversation_id": CONV,
            "target_agent": AGENT, "msg_id": "m-1"}
    body.update(body_extra or {})
    attributes = {"http.auth.principal": "alice"}
    if source:
        attributes["message_source"] = json.dumps(source)
        attributes["skip_pre_persist"] = "1"
    ff = FlowFile(content=json.dumps(body).encode("utf-8"),
                  attributes=attributes)
    store = MagicMock()
    store.get_extra_snapshot.return_value = 1
    store.resolve_owner.return_value = "alice"
    queue = _Queue()
    cancelled = []
    try:
        with patch("core.conversation_access.authorize_message_submission"), \
                patch("core.conversation_store.ConversationStore.instance",
                      return_value=store), \
                patch("core.agent_hooks.AgentHookRunner", _HookRunner), \
                patch("core.conversation_writer.ConversationWriter.for_conversation",
                      return_value=MagicMock()), \
                patch("core.pending_queue.PendingQueue.for_agent",
                      return_value=queue), \
                patch("services.tool_relay_service.ToolRelayService.cancel_agent",
                      side_effect=lambda cid, agent, **kw: cancelled.append(agent)):
            result = task._execute_streaming(ff)
    finally:
        with task._active_contexts_lock:
            task._active_turns.pop(key, None)
            task._active_contexts.pop(key, None)
            task._active_claude_client.pop(key, None)
    return json.loads(result[0].get_content()), queue, cancelled


def test_a_user_message_cancels_the_tools_then_interrupts():
    client = _CliClient()
    ack, queue, cancelled = _deliver(client)

    assert ack["status"] == "accepted"
    assert cancelled == [AGENT]
    assert client.interrupts == ["hello"]
    assert client.queued == []
    assert queue.items == [("m-1", "preempt_rescue")]


def test_a_nimsg_is_submitted_without_interrupting():
    client = _CliClient()
    ack, queue, cancelled = _deliver(client, {"no_interrupt": True})

    assert ack["status"] == "accepted"
    assert cancelled == []
    assert client.interrupts == []
    assert client.queued == [("hello", "m-1")]
    assert queue.items == [("m-1", "preempt_rescue")]


def test_an_agent_trigger_of_another_mode_is_submitted_now_not_at_turn_end():
    client = _CliClient()
    ack, queue, cancelled = _deliver(
        client,
        running_mode={"type": "delegate_reply", "source_agent": "A",
                      "task_id": "t1"},
        source={"type": "agent_delegate", "from": "B", "task_id": "t2"})

    assert ack["status"] == "accepted"
    assert client.interrupts == []
    assert [text for text, _mid in client.queued] == ["hello"]
    assert cancelled == []


def test_a_message_the_cli_refuses_is_queued():
    client = _CliClient(accepts=False)
    ack, queue, _ = _deliver(client, {"no_interrupt": True})

    assert ack["status"] == "queued"
    assert queue.items == [("m-1", "http")]


def test_a_nimsg_to_an_api_agent_neither_aborts_nor_cancels():
    client = _ApiClient()
    ack, queue, cancelled = _deliver(client, {"no_interrupt": True})

    assert ack["status"] == "queued"
    assert client.aborted is False
    assert cancelled == []
    assert queue.items == [("m-1", "http")]


@pytest.mark.parametrize("extra", [{}, {"no_interrupt": True}])
def test_an_external_request_turn_is_never_entered(extra):
    client = _CliClient()
    ack, queue, _ = _deliver(
        client, extra,
        running_mode={"type": "external_request", "source_agent": "x"})

    assert client.interrupts == [] and client.queued == []
    assert ack["status"] == "queued"

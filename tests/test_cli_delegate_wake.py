"""A delegate wake survives CLI catch-up boundaries through the durable inbox."""

from unittest.mock import MagicMock

import pytest

from core import agent_inbox
from core.agent_inbox_store import AgentInboxStore
from core.conversation_store import ConversationStore
from core.handlers.spawn_agents import SpawnAgentsHandler
from core.llm_client import LLMClient, LLMMessage
from core.pending_queue import PendingQueue
from tasks.ai.agent_loop import AgentLoopTask

CID = "delegate-wake"
AGENT = "GameDev2"
SOURCE = {"type": "agent_delegate", "from": "GameDev4", "to": AGENT,
          "target_agent": AGENT, "kind": "reply"}


@pytest.fixture
def delivery(tmp_path, monkeypatch):
    inbox = AgentInboxStore(tmp_path / "inbox.sqlite3")
    monkeypatch.setattr(AgentInboxStore, "_instance", inbox)
    store = ConversationStore(store_dir=str(tmp_path / "conversations"))
    monkeypatch.setattr(ConversationStore, "instance", classmethod(lambda cls: store))
    store.save(CID, [], user_id="u")
    PendingQueue.drop_cache()
    writer = MagicMock()
    monkeypatch.setattr(
        "core.conversation_writer.ConversationWriter.for_conversation", lambda cid: writer)
    wakes = []
    monkeypatch.setattr(AgentLoopTask, "wake_agent", lambda *a, **k: wakes.append((a, k)))
    monkeypatch.setattr("tasks.ai._live_submit._active_cli_client", lambda *a: None)
    yield writer, wakes
    PendingQueue.drop_cache()


def _wake(pre_persisted=True):
    SpawnAgentsHandler._wake_caller(
        MagicMock(), CID, AGENT, "u", "GD4 DELEGATE CONTENT", "reply-mid",
        source=SOURCE, pre_persisted=pre_persisted)


def test_idle_wake_stores_data_and_schedules_the_exact_recipient(delivery):
    writer, wakes = delivery
    _wake()
    writer.enqueue_message.assert_not_called()
    assert PendingQueue.for_agent(CID, AGENT).drain() == []
    assert [i.msg_id for i in agent_inbox.unread_items(CID, AGENT)] == ["reply-mid"]
    assert wakes[0][0][:2] == (CID, AGENT)
    assert "GD4 DELEGATE CONTENT" in agent_inbox.read_inbox(CID, AGENT)
    assert agent_inbox.read_inbox(CID, AGENT) == "No unread items."


def test_unpersisted_reply_is_visible_in_the_transcript_without_inline_copy(delivery):
    writer, _ = delivery
    _wake(pre_persisted=False)
    message = writer.enqueue_message.call_args.args[0]
    assert message["display_only"] is True
    assert message["msg_id"] == "reply-mid"
    assert message["source"] == SOURCE
    assert "GD4 DELEGATE CONTENT" in agent_inbox.read_inbox(CID, AGENT)


def test_a_later_own_reply_cannot_move_the_delegate_out_of_the_prompt(delivery):
    _wake()
    client = LLMClient("claude-code-interactive")
    notice = agent_inbox.take_notice(CID, AGENT)
    prompt = client._cci_prompt(
        [LLMMessage(role="user", content=notice,
                    source={"type": "system", "name": "inbox"}, conversation_id=CID)],
        None, "/w", "/cw", "u", CID, initial_context=False, agent_name=AGENT)
    assert "STOP. Call read_inbox() NOW" in prompt
    assert "GD4 DELEGATE CONTENT" not in prompt
    assert "GD4 DELEGATE CONTENT" in agent_inbox.read_inbox(CID, AGENT)


def test_failed_notice_paste_does_not_remove_the_delivery(delivery):
    _wake()
    notice, _ = agent_inbox.peek_notice(CID, AGENT, only_new=True)
    assert notice
    assert "GD4 DELEGATE CONTENT" in agent_inbox.read_inbox(CID, AGENT)
    assert agent_inbox.peek_notice(CID, AGENT, only_new=True)[0] == ""

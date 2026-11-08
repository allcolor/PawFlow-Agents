"""Voluntary public-agent catchup and absence of automatic inbox fanout."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from core import agent_inbox
from core.agent_inbox_store import AgentInboxStore
from core.conversation_store import ConversationStore
from core.llm_client import stamp_message
from core.pending_queue import PendingQueue

CID = "catchup-conv"


@pytest.fixture
def stores(tmp_path, monkeypatch):
    store = ConversationStore(store_dir=str(tmp_path / "conversations"))
    inbox = AgentInboxStore(tmp_path / "inbox.sqlite3")
    monkeypatch.setattr(ConversationStore, "instance", classmethod(lambda cls: store))
    monkeypatch.setattr(AgentInboxStore, "_instance", inbox)
    PendingQueue.drop_cache()
    store.save(CID, [], user_id="u")
    for agent in ("A", "B", "C"):
        store.save_agent_context(CID, agent, [stamp_message({
            "role": "system", "content": "rules",
            "source": {"type": "context"}}, CID)])
    yield store, inbox
    PendingQueue.drop_cache()


def publish(store, text, agent="A", **source_fields):
    row = stamp_message({
        "role": "assistant", "content": text,
        "source": {"type": "agent", "name": agent, **source_fields}}, CID)
    store.append_message(CID, row, agent_name=agent, user_id="u")
    return row


def read(agent="A", reader="B", **kwargs):
    from core.agent_catchup import read_catchup
    return json.loads(read_catchup(CID, reader, agent, **kwargs))


@pytest.mark.parametrize("batch", [False, True])
def test_public_append_keeps_shared_but_creates_no_inbox_or_notice(stores, batch):
    store, inbox = stores
    row = stamp_message({"role": "assistant", "content": "PUBLIC SENTINEL",
                         "source": {"type": "agent", "name": "A"}}, CID)
    if batch:
        store.append_messages(CID, [{"msg": row, "agent_name": "A", "user_id": "u"}])
    else:
        store.append_message(CID, row, agent_name="A", user_id="u")
    assert "PUBLIC SENTINEL" in str(store.load_shared_for_agent(CID, "B"))
    assert "PUBLIC SENTINEL" not in str(store.load_agent_context(CID, "B"))
    assert not inbox.list_items(CID, "B")
    assert agent_inbox.take_notice(CID, "B") == ""
    assert agent_inbox.read_inbox(CID, "B") == "No unread items."
    assert read()["messages"][0]["text"] == "PUBLIC SENTINEL"


def test_cursors_are_per_reader_source_conversation_and_survive_restart(stores, monkeypatch):
    store, _ = stores
    publish(store, "first A")
    publish(store, "first C", agent="C")
    assert read()["messages"][0]["text"] == "first A"
    assert read()["messages"] == []
    assert read(reader="C")["messages"][0]["text"] == "first A"
    assert read(agent="C")["messages"][0]["text"] == "first C"
    reopened = ConversationStore(store_dir=str(store._store_dir))
    monkeypatch.setattr(ConversationStore, "instance", classmethod(lambda cls: reopened))
    assert read()["messages"] == []
    publish(reopened, "second A")
    assert [m["text"] for m in read()["messages"]] == ["second A"]


def test_public_output_excludes_private_channels_tools_and_other_authors(stores):
    store, _ = stores
    publish(store, "VISIBLE")
    publish(store, "TASK SECRET", task_id="t1")
    publish(store, "BTW SECRET", btw=True)
    publish(store, "TARGET SECRET", visibility="target_only")
    publish(store, "OTHER AUTHOR", agent="C")
    store.append_message(CID, stamp_message({
        "role": "assistant", "content": "PRIVATE REPLY",
        "source": {"type": "agent_delegate", "from": "A", "to": "C",
                   "kind": "reply"}}, CID), agent_name="A", user_id="u")
    assert [m["text"] for m in read()["messages"]] == ["VISIBLE"]


def test_paging_never_skips_or_replays_even_with_escaped_and_unicode_text(stores):
    store, _ = stores
    text = ('é"' + chr(10) + chr(92)) * 3000
    publish(store, text)
    received = []
    pages = []
    for _ in range(100):
        from core.agent_catchup import read_catchup
        raw = read_catchup(CID, "B", "A", max_chars=2000)
        assert len(raw) <= 2000
        page = json.loads(raw)
        pages.append(page)
        received.extend(m["text"] for m in page["messages"])
        if not page["has_more"]:
            break
    else:
        pytest.fail("catchup did not finish")
    assert len(pages) > 1
    assert "".join(received) == text
    assert read()["messages"] == []


def test_concurrent_reads_deliver_each_public_message_once(stores):
    store, _ = stores
    publish(store, "ONCE")
    with ThreadPoolExecutor(max_workers=2) as pool:
        outputs = list(pool.map(lambda _: read(), range(2)))
    assert sum(len(page["messages"]) for page in outputs) == 1


def test_rewrite_invalidates_cursor_and_stale_legacy_catchup_never_notifies(stores):
    store, inbox = stores
    publish(store, "old")
    assert read()["messages"]
    store.save(CID, [stamp_message({
        "role": "assistant", "content": "replacement",
        "source": {"type": "agent", "name": "A"}}, CID)], user_id="u")
    assert [m["text"] for m in read()["messages"]] == ["replacement"]
    inbox.enqueue(CID, "B", stamp_message({
        "role": "user", "content": "OLD AUTOMATIC CATCHUP"}, CID), "inbox:catch_up")
    assert agent_inbox.unread_items(CID, "B") == []
    assert agent_inbox.take_notice(CID, "B") == ""
    assert agent_inbox.read_inbox(CID, "B") == "No unread items."


def test_context_preparation_never_enqueues_shared_history(stores):
    from core.llm_client import LLMMessage
    _, inbox = stores
    messages = [LLMMessage(role="assistant", content="OTHER HISTORY",
                          source={"type": "agent", "name": "A"}, conversation_id=CID)]
    assert agent_inbox.prepare_context_messages(messages, CID, "B") == []
    assert not inbox.list_items(CID, "B")


def test_handler_uses_caller_identity_and_validates_arguments(stores):
    from core.handlers.catchup import CatchUpHandler
    store, _ = stores
    publish(store, "PUBLIC")
    handler = CatchUpHandler()
    handler.set_conversation_id(CID)
    handler.set_source_agent("B", "")
    assert handler.name == "catchUp"
    assert handler.parameters_schema["required"] == ["agent"]
    for args in ({}, {"agent": ""}, {"agent": 1}, {"agent": "A", "max_chars": True},
                 {"agent": "A", "max_chars": 1999}):
        assert handler.execute(args).startswith("Error:")
    assert json.loads(handler.execute({"agent": "A"}))["messages"][0]["text"] == "PUBLIC"


def test_catchup_is_a_read_only_tool_under_normalized_policy_names():
    from core.tool_approval import ToolApprovalGate
    assert ToolApprovalGate.normalize_tool_name("catchUp") in ToolApprovalGate.EXEMPT_TOOLS
    assert ToolApprovalGate.is_read_only_allowed("catchUp", {"agent": "A"})
    assert ToolApprovalGate.is_advisor_read_only_allowed("catchUp", {"agent": "A"})


def test_locked_encrypted_conversation_cannot_advance_cursor(stores, monkeypatch):
    from core._conversation_store_base import ConversationLockedError
    from core.agent_catchup import read_catchup
    store, _ = stores
    publish(store, "READ AFTER UNLOCK")
    with monkeypatch.context() as locked:
        locked.setattr(store, "_is_encryption_enabled", lambda _: True)
        locked.setattr(store, "_codec_for", lambda _: None)
        with pytest.raises(ConversationLockedError):
            read_catchup(CID, "B", "A")
    assert read()["messages"][0]["text"] == "READ AFTER UNLOCK"


def test_wrapped_cli_stream_result_is_not_truncated_after_cursor_read():
    from types import SimpleNamespace
    from tasks.ai._alc_closures2 import _ALCClosures2Mixin
    persisted = []
    st = SimpleNamespace(ctx={}, client=SimpleNamespace(), conversation_id=CID,
                         user_id="u", tools_called=[], _tool_max=10,
                         _agent_source=lambda **_: {"type": "agent", "name": "B"},
                         _append=persisted.append)
    harness = _ALCClosures2Mixin()
    harness._materialize_tool_result_images = lambda result, **_: result
    harness._wrap_tool_output = lambda name, result: result
    harness._alc_cli_block_callback(st, "tool_use", {
        "id": "catchup-call", "name": "mcp__pawflow__use_tool",
        "arguments": {"tool_name": "catchUp", "arguments_json": '{"agent":"A"}'}})
    harness._alc_cli_block_callback(st, "tool_result", {
        "tc_id": "catchup-call", "tool": "mcp__pawflow__use_tool", "result": "X" * 5000})
    assert persisted[-1].content == "X" * 5000


def test_voice_result_is_not_clipped_after_catchup_cursor_read(stores):
    from core.handlers.catchup import CatchUpHandler
    from core.tool_registry import ToolRegistry
    from services._realtime_tools import RealtimeToolBridge
    store, _ = stores
    publish(store, "X" * 5000)
    handler = CatchUpHandler()
    handler.set_conversation_id(CID)
    handler.set_agent_name("B")
    registry = ToolRegistry()
    registry.register(handler)
    bridge = RealtimeToolBridge("catchUp", CID, "B", "u", registry=registry)
    results = []
    assert bridge.handle_call("c1", "catchUp", {"agent": "A"},
                              send_result=lambda call_id, text: results.append(text)) == "done"
    assert json.loads(results[0])["messages"][0]["text"] == "X" * 5000


@pytest.mark.parametrize("wrapped", [False, True])
def test_registry_never_clears_a_page_after_advancing_its_cursor(stores, wrapped):
    from core.handlers.catchup import CatchUpHandler
    from core.handlers.meta_tools import UseToolHandler
    from core.tool_registry import ToolRegistry
    store, _ = stores
    publish(store, "X" * 3000)
    handler = CatchUpHandler()
    handler.set_conversation_id(CID)
    handler.set_source_agent("B", "")
    handler._tool_result_max_chars = 10
    registry = ToolRegistry()
    registry.register(handler)
    if wrapped:
        wrapper = UseToolHandler(registry)
        wrapper._tool_result_max_chars = 10
        registry.register(wrapper)
        raw = registry.execute("use_tool", {"tool_name": "catchUp",
                                            "arguments_json": '{"agent":"A"}'})
    else:
        raw = registry.execute("catchUp", {"agent": "A"})
    assert json.loads(raw)["messages"][0]["text"] == "X" * 3000
    assert read()["messages"] == []

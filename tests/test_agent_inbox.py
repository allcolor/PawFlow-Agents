"""Inbox delivery: non-user messages are read through read_inbox.

See docs/inbox_delivery.md. Incident that motivated it: 2026-11-07 08:15Z,
a delegate message pasted into a Codex composer was never delivered.
"""

import threading
import time
from unittest.mock import patch

import pytest

from core import agent_inbox
from core.agent_inbox_store import AgentInboxStore
from core.pending_queue import PendingQueue

CID = "conv-inbox"
AGENT = "GameDev3"


@pytest.fixture
def inbox(tmp_path, monkeypatch):
    store = AgentInboxStore(tmp_path / "inbox.sqlite3")
    monkeypatch.setattr(AgentInboxStore, "_instance", store)

    class _FakeStore:
        _store_dir = tmp_path / "convs"
        extras = {}

        def _conv_dir(self, cid, user_id=""):
            d = self._store_dir / "u" / cid
            d.mkdir(parents=True, exist_ok=True)
            return d

        def get_extra(self, cid, key, default=None):
            return self.extras.get((cid, key), default)

    from core.conversation_store import ConversationStore
    fake = _FakeStore()
    monkeypatch.setattr(ConversationStore, "instance", classmethod(lambda cls: fake))
    PendingQueue.drop_cache()
    yield store
    PendingQueue.drop_cache()


def _msg(msg_id, content, source=None):
    return {"role": "user", "content": content, "msg_id": msg_id,
            "ts": time.time(), "source": source or {"type": "agent_delegate"}}


def _put(msg_id, content, kind="delegate_message", sender="GameDev2",
         source=None):
    assert agent_inbox.enqueue_item(CID, AGENT, _msg(msg_id, content, source),
                                    kind, sender)


# ── enqueue ─────────────────────────────────────────────────────────────

def test_enqueue_requires_a_known_kind_and_a_sender(inbox):
    with pytest.raises(ValueError):
        agent_inbox.enqueue_item(CID, AGENT, _msg("m1", "x"), "gossip", "A")
    with pytest.raises(ValueError):
        agent_inbox.enqueue_item(CID, AGENT, _msg("m1", "x"),
                                 "delegate_message", "")
    with pytest.raises(ValueError):
        agent_inbox.enqueue_item(CID, "", _msg("m1", "x"),
                                 "delegate_message", "A")


def test_inbox_items_never_drain_inline(inbox):
    _put("m1", "delegate text")
    PendingQueue.for_agent(CID, AGENT).enqueue(_msg("u1", "user text"),
                                               source="http")
    drained = PendingQueue.for_agent(CID, AGENT).drain()
    assert [m["msg_id"] for m in drained] == ["u1"]
    assert [item.msg_id for item in agent_inbox.unread_items(CID, AGENT)] == ["m1"]


# ── the notice ──────────────────────────────────────────────────────────

def test_notice_is_imperative_and_never_carries_content(inbox):
    _put("m1", "SECRET-CONTENT " * 10)
    _put("m2", "r" * 12000, kind="background_result", sender="tool bash")
    notice = agent_inbox.take_notice(CID, AGENT)
    assert notice.startswith("[PawFlow inbox: ACTION REQUIRED] You have 2 unread items:")
    assert "1. delegate message from GameDev2 (150 chars)" in notice
    assert "2. background result from tool bash (12 KB)" in notice
    assert "STOP. Call read_inbox() NOW" in notice
    assert "ONLY available through read_inbox()" in notice
    assert "SECRET-CONTENT" not in notice


def test_live_notice_is_coalesced_while_a_notice_is_outstanding(inbox):
    _put("m1", "a")
    assert agent_inbox.take_notice(CID, AGENT, only_new=True)
    assert agent_inbox.take_notice(CID, AGENT, only_new=True) == ""
    # A turn start still repeats it while anything is unread.
    assert agent_inbox.take_notice(CID, AGENT)
    _put("m2", "b")
    assert agent_inbox.take_notice(CID, AGENT, only_new=True) == ""
    agent_inbox.read_inbox(CID, AGENT)
    _put("m3", "c")
    assert "1 unread item" in agent_inbox.take_notice(CID, AGENT, only_new=True)


def test_peek_notice_records_nothing_until_record_notice(inbox):
    _put("m1", "a")
    notice, newest = agent_inbox.peek_notice(CID, AGENT, only_new=True)
    assert notice
    assert agent_inbox.peek_notice(CID, AGENT, only_new=True)[0] == notice
    agent_inbox.record_notice(CID, AGENT, newest)
    assert agent_inbox.peek_notice(CID, AGENT, only_new=True)[0] == ""


# ── read_inbox ──────────────────────────────────────────────────────────

def test_read_returns_items_in_order_and_marks_them_read(inbox):
    _put("m1", "first message")
    _put("m2", "second message", kind="delegate_result", sender="GameDev7")
    out = agent_inbox.read_inbox(CID, AGENT)
    assert out.index("first message") < out.index("second message")
    assert "msg_id=m1" in out and "delegate result from GameDev7" in out
    assert agent_inbox.unread_items(CID, AGENT) == []
    assert agent_inbox.read_inbox(CID, AGENT) == "No unread items."


def test_read_stops_at_the_budget_and_says_more_are_unread(inbox):
    _put("m1", "a" * 1500)
    _put("m2", "b" * 1500)
    out = agent_inbox.read_inbox(CID, AGENT, max_chars=2000)
    assert "a" * 1500 in out and "b" * 1500 not in out
    assert "1 more unread item(s): call read_inbox() again" in out
    assert [i.msg_id for i in agent_inbox.unread_items(CID, AGENT)] == ["m2"]


def test_a_large_item_is_paged_and_read_once_complete(inbox):
    text = "".join(chr(65 + i % 26) for i in range(5000))
    _put("big", text)
    first = agent_inbox.read_inbox(CID, AGENT, max_chars=2000)
    first_end = inbox.read_offset(CID, AGENT, "big")
    assert 0 < first_end < 2000
    assert text[:first_end] in first
    assert len(first) <= 2000
    assert f'read_inbox(item="big", offset={first_end})' in first
    assert [i.msg_id for i in agent_inbox.unread_items(CID, AGENT)] == ["big"]
    second = agent_inbox.read_inbox(CID, AGENT, max_chars=2000)
    second_end = inbox.read_offset(CID, AGENT, "big")
    assert text[first_end:second_end] in second  # stored offset resumes the page
    assert len(second) <= 2000
    third = agent_inbox.read_inbox(CID, AGENT, item="big", offset=second_end)
    assert text[second_end:] in third
    assert agent_inbox.unread_items(CID, AGENT) == []


def test_headers_and_continuation_fit_the_read_budget(inbox):
    for index in range(25):
        _put(f"small-{index}", f"body-{index}")
    result = agent_inbox.read_inbox(CID, AGENT, max_chars=2000)
    assert len(result) <= 2000
    assert "more unread item(s): call read_inbox() again" in result
    unread = {entry.msg_id for entry in agent_inbox.unread_items(CID, AGENT)}
    for index in range(25):
        assert (f"msg_id=small-{index} " in result) == (f"small-{index}" not in unread)


def test_reading_an_unknown_item_says_so(inbox):
    assert agent_inbox.read_inbox(CID, AGENT, item="nope") == "No unread inbox item nope."


# ── turns that read nothing ─────────────────────────────────────────────

def test_end_of_turn_counts_unread_turns_and_a_read_resets_it(inbox):
    assert agent_inbox.end_of_turn(CID, AGENT) == 0
    _put("m1", "a")
    assert agent_inbox.end_of_turn(CID, AGENT) == 1
    assert agent_inbox.end_of_turn(CID, AGENT) == 2
    agent_inbox.read_inbox(CID, AGENT)
    assert agent_inbox.end_of_turn(CID, AGENT) == 0


def test_a_new_item_ends_a_streak_of_unread_turns(inbox):
    _put("m1", "a")
    for _ in range(4):
        agent_inbox.end_of_turn(CID, AGENT)
    _put("m2", "b")
    assert agent_inbox.end_of_turn(CID, AGENT) == 1


def test_catch_up_does_not_reset_the_unread_work_retry_limit(inbox):
    _put("work", "ignored delegate request")
    for turn in range(1, agent_inbox.MAX_UNREAD_TURNS + 1):
        inbox.enqueue(CID, AGENT, _msg(f"chatter-{turn}", "other agent output"),
                      "inbox:catch_up")
        assert agent_inbox.end_of_turn(CID, AGENT) == turn


def test_legacy_catch_up_is_invisible_without_retriggering_a_real_turn(inbox):
    inbox.enqueue(CID, AGENT, _msg("chatter", "other agent output"), "inbox:catch_up")
    assert agent_inbox.peek_notice(CID, AGENT, only_new=True)[0] == ""
    assert agent_inbox.take_notice(CID, AGENT) == ""
    assert agent_inbox.end_of_turn(CID, AGENT) == 0
    assert agent_inbox.unread_count(CID, AGENT) == 0


def test_catch_up_delivery_never_wakes_an_idle_agent_even_with_wake_enabled(
        inbox, monkeypatch):
    from tasks.ai import _live_submit
    from tasks.ai.agent_loop import AgentLoopTask

    def unexpected(*args, **kwargs):
        pytest.fail("passive catch-up must not wake an idle agent")

    monkeypatch.setattr(_live_submit, "_active_cli_client", lambda *_: None)
    monkeypatch.setattr(AgentLoopTask, "wake_agent", unexpected)
    with pytest.raises(ValueError, match="unknown inbox kind"):
        _live_submit.submit_or_queue(
            CID, AGENT, _msg("chatter", "context only"), "catch_up",
            kind="catch_up", sender="GameDev2", wake=True)
    assert agent_inbox.read_inbox(CID, AGENT) == "No unread items."


@pytest.mark.parametrize("wake", [False, True])
def test_workflow_catch_up_is_rejected_without_resolving_or_submitting_runtime(
        inbox, monkeypatch, wake):
    from tasks.ai import _live_submit
    from tasks.ai.agent_loop import AgentLoopTask

    def unexpected(*args, **kwargs):
        pytest.fail("passive catch-up must not submit or wake a workflow")

    monkeypatch.setattr("core.conv_agent_config.get_agent_config",
                        lambda *_: {"runtime_kind": "workflow"})
    monkeypatch.setattr(_live_submit, "_submit_workflow_delivery", unexpected)
    monkeypatch.setattr(_live_submit, "_active_cli_client", lambda *_: None)
    monkeypatch.setattr(AgentLoopTask, "wake_agent", unexpected)
    with pytest.raises(ValueError, match="unknown inbox kind"):
        _live_submit.submit_or_queue(
            CID, AGENT, _msg("chatter", "workflow context only"), "catch_up",
            kind="catch_up", sender="GameDev2", wake=wake)
    assert agent_inbox.read_inbox(CID, AGENT) == "No unread items."


def test_turn_mode_messages_expose_the_item_sources(inbox):
    source = {"type": "agent_delegate", "from": "GameDev2", "to": AGENT}
    _put("m1", "a", source=source)
    (only,) = agent_inbox.turn_mode_messages(CID, AGENT)
    assert only.source == source


# ── live delivery ───────────────────────────────────────────────────────

class _Client:
    provider = "codex-interactive"

    def __init__(self, ok=True):
        self.ok = ok
        self.sent = []
        self.done = threading.Event()

    def send_queued_message(self, text, **kwargs):
        self.sent.append(text)
        self.done.set()
        return self.ok


def _live(monkeypatch, active, client):
    from tasks.ai import _live_submit
    from tasks.ai.agent_loop import AgentLoopTask
    monkeypatch.setattr(AgentLoopTask, "is_agent_active",
                        classmethod(lambda cls, _c, _a: active))
    monkeypatch.setattr(_live_submit, "_active_cli_client",
                        lambda _c, _a: client)
    monkeypatch.setattr(_live_submit, "_RETRY_DELAYS_SECONDS", ())
    return _live_submit


def test_a_running_cli_gets_the_notice_never_the_content(inbox, monkeypatch):
    client = _Client()
    live = _live(monkeypatch, True, client)
    assert live.submit_or_queue(CID, AGENT, _msg("m1", "DELEGATE BODY"),
                                "delegate_reply", kind="delegate_message",
                                sender="GameDev2")
    assert client.done.wait(5)
    (pasted,) = client.sent
    assert pasted.startswith("[PawFlow inbox: ACTION REQUIRED]")
    assert "DELEGATE BODY" not in pasted
    assert PendingQueue.for_agent(CID, AGENT).peek_count() == 0
    assert agent_inbox.peek_notice(CID, AGENT, only_new=True)[0] == ""


def test_a_refused_notice_paste_leaves_the_notice_to_the_next_drain(inbox, monkeypatch):
    client = _Client(ok=False)
    live = _live(monkeypatch, True, client)
    live.submit_or_queue(CID, AGENT, _msg("m1", "x"), "delegate_reply",
                         kind="delegate_message", sender="GameDev2")
    assert client.done.wait(5)
    deadline = time.time() + 5
    while time.time() < deadline and not agent_inbox.peek_notice(
            CID, AGENT, only_new=True)[0]:
        time.sleep(0.05)
    assert agent_inbox.peek_notice(CID, AGENT, only_new=True)[0]


def test_an_idle_agent_keeps_content_in_the_inbox_and_wakes(inbox, monkeypatch):
    live = _live(monkeypatch, False, None)
    woken = []
    from tasks.ai.agent_loop import AgentLoopTask
    monkeypatch.setattr(AgentLoopTask, "wake_agent",
                        classmethod(lambda cls, *a, **k: woken.append(a)))
    assert live.submit_or_queue(CID, AGENT, _msg("m1", "x"), "delegate_reply",
                                kind="delegate_message", sender="GameDev2")
    assert PendingQueue.for_agent(CID, AGENT).drain() == []
    assert [i.msg_id for i in agent_inbox.unread_items(CID, AGENT)] == ["m1"]
    assert woken


# ── end of turn in the agent loop ───────────────────────────────────────

class _St:
    conversation_id = CID
    user_id = "u"

    def __init__(self):
        self.ctx = {"active_agent_name": AGENT}
        self._new_user_msgs = []
        self.appended = []
        self.modes = []

    def _append(self, msg):
        self.appended.append(msg)

    def _apply_queued_delegate_turn_mode(self, msgs):
        self.modes.append([getattr(m, "source", {}) for m in msgs])


def _core():
    from tasks.ai.agent_core import AgentCoreMixin

    class _Core(AgentCoreMixin):
        @staticmethod
        def _serialize_messages(msgs):
            return [m.content for m in msgs]
    return _Core()


def test_unread_items_retrigger_the_agent_with_a_notice(inbox):
    source = {"type": "agent_delegate", "from": "GameDev2", "to": AGENT}
    _put("m1", "a", source=source)
    st = _St()
    _core()._alc_inbox_end_of_turn(st)
    assert st.ctx["_retrigger_after_done"] is True
    (notice,) = st.ctx["_retrigger_messages"]
    assert notice.startswith("[PawFlow inbox: ACTION REQUIRED]")
    assert st.modes == [[source]]


def test_nothing_unread_means_no_retrigger(inbox):
    st = _St()
    _core()._alc_inbox_end_of_turn(st)
    assert "_retrigger_after_done" not in st.ctx


def test_repeated_catch_up_never_retriggers_or_warns_at_end_of_turn(
        inbox, monkeypatch):
    core = _core()
    monkeypatch.setattr(type(core), "_alc_inbox_unread_warning",
                        staticmethod(lambda *_: pytest.fail("passive catch-up warning")))
    for turn in range(agent_inbox.MAX_UNREAD_TURNS + 2):
        inbox.enqueue(CID, AGENT, _msg(f"chatter-{turn}", "other participant output"),
                      "inbox:catch_up")
        st = _St()
        core._alc_inbox_end_of_turn(st)
        assert "_retrigger_after_done" not in st.ctx
        assert inbox.notice_state(CID, AGENT)[1] == 0
    assert agent_inbox.unread_count(CID, AGENT) == 0
    _put("real-work", "explicit delegate request")
    st = _St()
    core._alc_inbox_end_of_turn(st)
    assert st.ctx["_retrigger_after_done"] is True
    output = agent_inbox.read_inbox(CID, AGENT)
    assert "other participant output" not in output
    assert "explicit delegate request" in output


def test_after_max_unread_turns_the_agent_stops_and_warns_once(inbox, monkeypatch):
    _put("m1", "a")
    warned = []
    core = _core()
    monkeypatch.setattr(type(core), "_alc_inbox_unread_warning",
                        staticmethod(lambda st, agent: warned.append(agent)))
    for _ in range(agent_inbox.MAX_UNREAD_TURNS - 1):
        st = _St()
        core._alc_inbox_end_of_turn(st)
        assert st.ctx.get("_retrigger_after_done") is True
    for _ in range(2):
        st = _St()
        core._alc_inbox_end_of_turn(st)
        assert "_retrigger_after_done" not in st.ctx
    assert warned == [AGENT]


def test_api_setup_does_not_reset_unread_streak_on_retriggers(inbox, monkeypatch):
    from types import SimpleNamespace
    from core.llm_client import LLMMessage
    from tasks.ai._alc_setup import _ALCSetupMixin

    human = LLMMessage(role="user", content="Human input", source={"type": "user"},
                       conversation_id=CID)
    human._pawflow_current_user_message = True
    ctx = {"conversation_id": CID, "active_agent_name": AGENT,
           "client": SimpleNamespace(provider="openai"),
           "registry": SimpleNamespace(list_tools=lambda: []),
           "tool_defs": [], "messages": [human], "model": "test-model"}
    _put("m1", "ignored result")
    warned = []
    core = _core()
    monkeypatch.setattr(type(core), "_alc_inbox_unread_warning",
                        staticmethod(lambda st, agent: warned.append(agent)))
    for turn in range(1, agent_inbox.MAX_UNREAD_TURNS + 1):
        st = _St()
        st.ctx = ctx
        st.emitter = SimpleNamespace(on_loop_start=lambda ctx: None)
        _ALCSetupMixin()._alc_setup(st)
        st._append = ctx["messages"].append
        core._alc_inbox_end_of_turn(st)
        assert inbox.notice_state(CID, AGENT)[1] == turn
        if turn < agent_inbox.MAX_UNREAD_TURNS:
            assert ctx.pop("_retrigger_after_done") is True
        else:
            assert "_retrigger_after_done" not in ctx
    assert warned == [AGENT]
    fresh = LLMMessage(role="user", content="New human input", source={"type": "user"},
                       conversation_id=CID)
    fresh._pawflow_current_user_message = True
    ctx["messages"].append(fresh)
    _ALCSetupMixin()._alc_setup(st)
    assert inbox.notice_state(CID, AGENT)[1] == 0
    # A different human can arrive after setup through the iteration drain.
    inbox.set_notice_state(CID, AGENT, unread_turns=2)
    during = LLMMessage(role="user", content="Input received mid-turn",
                        source={"type": "user"}, conversation_id=CID)
    during._pawflow_current_user_message = True
    ctx["messages"].append(during)
    st._append = ctx["messages"].append
    core._alc_inbox_end_of_turn(st)
    assert inbox.notice_state(CID, AGENT)[1] == 1
    assert ctx.pop("_retrigger_after_done") is True


def test_paging_cannot_acknowledge_content_that_was_never_returned(inbox):
    _put("big", "x" * 5000)
    with pytest.raises(ValueError, match="skip unread"):
        agent_inbox.read_inbox(CID, AGENT, item="big", offset=5000)
    assert [i.msg_id for i in agent_inbox.unread_items(CID, AGENT)] == ["big"]


def test_reading_a_page_resets_the_no_read_streak(inbox):
    _put("big", "x" * 5000)
    agent_inbox.end_of_turn(CID, AGENT)
    agent_inbox.end_of_turn(CID, AGENT)
    agent_inbox.read_inbox(CID, AGENT, max_chars=2000)
    assert agent_inbox.end_of_turn(CID, AGENT) == 1


def test_read_is_scoped_to_the_exact_agent_and_subconversation(inbox):
    _put("m1", "main content")
    sub = CID + "::task::child"
    assert agent_inbox.enqueue_item(sub, AGENT, _msg("m2", "child content"),
                                    "delegate_result", "GameDev2")
    assert agent_inbox.read_inbox(CID, "GameDev4") == "No unread items."
    assert "child content" in agent_inbox.read_inbox(sub, AGENT)
    assert [i.msg_id for i in agent_inbox.unread_items(CID, AGENT)] == ["m1"]


def test_catch_up_does_not_select_a_private_delegate_reply_mode(inbox):
    inbox.enqueue(CID, AGENT, _msg("m1", "A delegates to B",
                  {"type": "agent_delegate", "from": "A", "to": "B"}), "inbox:catch_up")
    assert agent_inbox.turn_mode_messages(CID, AGENT) == []


def test_cold_rebuild_keeps_own_output_and_user_input_inline(inbox):
    from core.llm_client import LLMMessage
    rows = [
        LLMMessage(role="assistant", content="my work",
                   source={"type": "agent", "name": AGENT}, conversation_id=CID),
        LLMMessage(role="user", content="human input",
                   source={"type": "user", "target_agent": AGENT}, conversation_id=CID),
        LLMMessage(role="user", content="legacy private data",
                   source={"type": "agent_delegate", "from": "A", "to": AGENT},
                   conversation_id=CID),
        LLMMessage(role="assistant", content="other participant",
                   source={"type": "agent", "name": "A"}, conversation_id=CID),
        LLMMessage(role="user", content="background content",
                   source={"type": "system", "name": "background"}, conversation_id=CID),
    ]
    assert [m.content for m in agent_inbox.inline_context_messages(rows, AGENT)] == [
        "my work", "human input"]


def test_cli_inbox_notice_keeps_explicit_deliveries(inbox):
    from core.llm_client import LLMClient
    client = LLMClient("claude-code", config={"api_key": "test"})
    _put("history", "PARTICIPANT CONTENT", kind="delegate_message")
    notice = client._build_catchup_context(CID, AGENT)
    assert "STOP. Call read_inbox() NOW" in notice
    assert "PARTICIPANT CONTENT" not in notice
    assert "PARTICIPANT CONTENT" in agent_inbox.read_inbox(CID, AGENT)
    assert client._build_catchup_context(CID, AGENT) == ""


def test_broadcast_stays_in_shared_without_inbox_or_recipient_copy(inbox, tmp_path, monkeypatch):
    from core.conversation_store import ConversationStore
    from core.llm_client import stamp_message
    store = ConversationStore(store_dir=str(tmp_path / "routing"))
    monkeypatch.setattr(ConversationStore, "instance", classmethod(lambda cls: store))
    store.save(CID, [], user_id="u")
    for agent in ("A", AGENT):
        store.save_agent_context(CID, agent, [stamp_message({
            "role": "system", "content": "rules",
            "source": {"type": "context", "name": "pawflow"}}, CID)])

    message = stamp_message({"role": "assistant", "content": "TEAM DATA",
                             "source": {"type": "agent", "name": "A"}}, CID)
    store.append_message(CID, message, agent_name="A", user_id="u")
    assert not any("TEAM DATA" in str(row.get("content"))
                   for row in store.load_agent_context(CID, AGENT))
    assert "TEAM DATA" in str(store.load_shared_for_agent(CID, AGENT))
    assert agent_inbox.read_inbox(CID, AGENT) == "No unread items."
    notice = stamp_message({
        "role": "user", "content": "[PawFlow inbox: ACTION REQUIRED]",
        "source": {"type": "system", "name": "inbox", "target_agent": "A",
                   "visibility": "target_only"}}, CID)
    store.append_message(CID, notice, agent_name="A", user_id="u")
    assert agent_inbox.unread_items(CID, AGENT) == []


def test_concurrent_reads_do_not_return_the_same_item_twice(inbox):
    from concurrent.futures import ThreadPoolExecutor
    _put("m1", "ONCE ONLY")
    barrier = threading.Barrier(2)

    def read():
        barrier.wait(timeout=5)
        return agent_inbox.read_inbox(CID, AGENT)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outputs = [future.result() for future in [executor.submit(read), executor.submit(read)]]
    assert sum("ONCE ONLY" in output for output in outputs) == 1
    assert "No unread items." in outputs


def test_notice_length_is_bounded_when_many_items_are_unread(inbox):
    for index in range(30):
        _put(f"m{index}", "BODY MUST STAY IN TOOL OUTPUT")
    notice = agent_inbox.take_notice(CID, AGENT)
    assert "30 unread items" in notice
    assert "10 more unread items" in notice
    assert "BODY MUST STAY" not in notice
    assert len(notice) < 2000
    assert agent_inbox.read_inbox(CID, AGENT).count("BODY MUST STAY") == 30


def test_shared_history_creates_no_inbox_items_across_cold_rebuilds(inbox):
    from core.llm_client import LLMMessage
    history = [LLMMessage(role="assistant", content="PAST TEAM WORK",
                          source={"type": "agent", "name": "A"},
                          conversation_id=CID)]
    assert agent_inbox.prepare_context_messages(history, CID, AGENT) == []
    assert agent_inbox.prepare_context_messages(history, CID, AGENT) == []
    assert agent_inbox.unread_items(CID, AGENT) == []
    assert agent_inbox.read_inbox(CID, AGENT) == "No unread items."
    inbox.purge_acknowledged(0, now=time.time() + 1)
    assert agent_inbox.prepare_context_messages(history, CID, AGENT) == []
    assert agent_inbox.read_inbox(CID, AGENT) == "No unread items."


@pytest.mark.parametrize("active", [False, True])
def test_delegate_delivery_preserves_creation_time_across_force_stop(inbox, active):
    from core.handlers.spawn_agents import SpawnAgentsHandler
    from core.llm_client import stamp_message
    source = {"type": "agent_delegate", "from": "A", "to": AGENT, "kind": "request"}
    message = stamp_message({"role": "user", "content": "CANCELLED WORK",
                             "msg_id": "old-delegate", "ts": 1.0, "source": source}, CID)
    inbox.discard_through(CID, AGENT, 2.0)
    if active:
        SpawnAgentsHandler._preempt_caller(
            None, CID, AGENT, message["content"], message["msg_id"], source,
            user_id="u", message=message)
    else:
        SpawnAgentsHandler._wake_caller(
            None, CID, AGENT, "u", message["content"], message["msg_id"],
            source=source, message=message)
    item, = inbox.list_items(CID, AGENT)
    assert item.state == "discarded"
    assert item.payload["ts"] == 1.0
    assert agent_inbox.read_inbox(CID, AGENT) == "No unread items."


def test_workflow_recipient_keeps_claimable_lease_delivery(inbox, monkeypatch):
    from types import SimpleNamespace
    from tasks.ai._live_submit import submit_or_queue
    submitted = []
    written = []
    runtime = SimpleNamespace(canonical_agent_name=AGENT,
                              adapter=SimpleNamespace(submit=submitted.append))
    monkeypatch.setattr("core.conv_agent_config.get_agent_config",
                        lambda *a: {"runtime_kind": "workflow"})
    monkeypatch.setattr("core.agent_runtime_router.AgentRuntimeRouter.instance",
                        lambda: SimpleNamespace(resolve=lambda *a: runtime))
    monkeypatch.setattr("core.workflow_agent_runtime.prepare_workflow_turn",
                        lambda **kwargs: kwargs)
    monkeypatch.setattr("core.conversation_writer.ConversationWriter.for_conversation",
                        lambda *a: SimpleNamespace(enqueue_message_if_absent=
                                                  lambda message, **k: written.append(message)))
    message = _msg("workflow-delegate", "LEASE WORK", {
        "type": "agent_delegate", "from": "A", "to": AGENT})
    assert submit_or_queue(CID, AGENT, message, "delegate_reply",
                           kind="delegate_message", sender="A", user_id="u")
    assert written[0]["ts"] == message["ts"]
    assert submitted[0]["message_id"] == message["msg_id"]
    assert submitted[0]["message"] == "LEASE WORK"
    assert agent_inbox.read_inbox(CID, AGENT) == "No unread items."
    _, items = inbox.claim(CID, AGENT, "workflow-run", "receive")
    assert [item.msg_id for item in items] == ["workflow-delegate"]


def test_legacy_rescue_migration_separates_human_and_non_user_content(inbox, tmp_path):
    import json
    rows = [
        _msg("human", "HUMAN INPUT", {"type": "user", "name": "u"}),
        _msg("delegate", "DELEGATE INPUT", {"type": "agent_delegate", "from": "A",
                                             "to": AGENT, "kind": "request"}),
        _msg("background", "BACKGROUND INPUT", {"type": "system", "name": "background"}),
        _msg("wake", "WAKE INPUT", {"type": "scheduled_wakeup"}),
    ]
    for row in rows:
        row["_pending_source"] = "preempt_rescue"
    path = tmp_path / "pending.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    inbox.migrate_pending_jsonl(CID, AGENT, path)
    assert [row["msg_id"] for row in PendingQueue.for_agent(CID, AGENT).drain()] == ["human"]
    assert [item.msg_id for item in agent_inbox.unread_items(CID, AGENT)] == [
        "delegate", "background", "wake"]
    assert agent_inbox.turn_mode_messages(CID, AGENT)[0].source["from"] == "A"

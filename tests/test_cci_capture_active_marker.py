"""A captured tmux turn must show as active work in the webchat.

Production sequence (2026-07-28): a PawFlow turn ended cleanly at 08:24:22
(`active released`). At 08:26:47 Claude Code resumed on its own — a
background-task notification delivered inside its container — so no PawFlow
streaming worker was involved and `_active_turns` stayed empty: the webchat
showed the agent idle while the tmux was visibly working. PawFlow cannot
restart that turn (a second prompt would duplicate the work in flight); it
attaches to it, and must mirror it into the UI's active-agent truth.
"""

import pytest

from services.cc_interactive_event_service import CCInteractiveEventService


class _Turns:
    """Stand-in for AgentLoopTask's class-level active-turn bookkeeping."""

    def __init__(self):
        self._active_turns = {}
        import threading
        self._active_contexts_lock = threading.Lock()


@pytest.fixture
def live_task(monkeypatch):
    import tasks.ai.agent_loop as agent_loop

    inst = _Turns()
    monkeypatch.setattr(agent_loop.AgentLoopTask, "_live_instance", inst,
                        raising=False)
    return inst


def _state(svc):
    return svc.register_session(
        "sess", user_id="allcol", conversation_id="80c37670",
        agent_name="claude")


def test_capture_registers_and_releases_the_active_turn(live_task):
    svc = CCInteractiveEventService({"token": "tok", "_service_id": "events"})
    state = _state(svc)

    svc._active_turn_marker(state, register=True)
    assert "80c37670:claude" in live_task._active_turns
    entry = live_task._active_turns["80c37670:claude"]
    assert entry["conversation_id"] == "80c37670"
    assert entry["agent_name"] == "claude"
    assert entry["owner_type"] == "cci_capture"
    assert entry["owner_id"] == state.active_turn_owner_id

    svc._active_turn_marker(state, register=False)
    assert live_task._active_turns == {}


def test_capture_cannot_replace_or_release_a_streaming_worker(live_task):
    key = "80c37670:claude"
    live_task._active_turns[key] = {
        "conversation_id": "80c37670",
        "agent_name": "claude",
        "owner_id": "worker-owner",
        "owner_type": "streaming_worker",
        "generation": 4,
    }
    svc = CCInteractiveEventService({"token": "tok", "_service_id": "events"})
    state = _state(svc)

    assert svc._active_turn_marker(state, register=True) is False
    assert state.active_turn_owner_id == ""
    assert live_task._active_turns[key]["owner_id"] == "worker-owner"

    assert svc._active_turn_marker(state, register=False) is False
    assert live_task._active_turns[key]["owner_id"] == "worker-owner"


def test_unregister_releases_capture_and_prevents_late_marker(live_task, monkeypatch):
    svc = CCInteractiveEventService({"token": "tok", "_service_id": "events"})
    state = _state(svc)
    state.connected = True
    state.manual_capture_pending = 1
    assert svc._active_turn_marker(state, register=True)
    live_task._active_turns["80c37670:other"] = {"owner_id": "other-worker"}
    monkeypatch.setattr(svc, "_drain_pending_after_capture", lambda _state: pytest.fail(
        "session teardown must not restart queued work"))

    svc.unregister_session("sess")

    assert svc.session_state("sess") is None
    assert not state.connected
    assert state.manual_capture_pending == 0
    assert live_task._active_turns == {"80c37670:other": {"owner_id": "other-worker"}}
    assert svc._active_turn_marker(state, register=True) is False


def test_unregister_does_not_release_replacement_worker(live_task):
    svc = CCInteractiveEventService({"token": "tok", "_service_id": "events"})
    state = _state(svc)
    assert svc._active_turn_marker(state, register=True)
    replacement = {"owner_id": "replacement-worker", "owner_type": "streaming_worker"}
    live_task._active_turns["80c37670:claude"] = replacement

    svc.unregister_session("sess")

    assert live_task._active_turns["80c37670:claude"] is replacement


def test_closed_session_cannot_restart_capture(live_task, monkeypatch):
    import threading

    svc = CCInteractiveEventService({"token": "tok", "_service_id": "events"})
    state = _state(svc)
    svc.unregister_session("sess")
    monkeypatch.setattr(threading, "Thread", lambda **_kwargs: pytest.fail(
        "a closed event session must not start a capture thread"))

    svc._start_manual_capture(state)
    assert svc.claim_consumer("sess", kind="capture") == 0
    assert svc.session_state("sess") is None


def test_unregister_wakes_blocked_capture_reader(live_task):
    import threading
    from services.cc_interactive_event_service import CCIConsumerEvicted

    svc = CCInteractiveEventService({"token": "tok", "_service_id": "events"})
    state = _state(svc)
    waiting = threading.Event()
    errors = []

    class ObservedCondition(threading.Condition):
        def wait(self, timeout=None):
            waiting.set()
            return super().wait(timeout)

    state.stream_condition = ObservedCondition()
    epoch = svc.claim_consumer("sess", kind="capture")

    def read_event():
        try:
            svc.wait_event("sess", epoch=epoch)
        except Exception as exc:
            errors.append(exc)

    reader = threading.Thread(target=read_event, daemon=True)
    reader.start()
    try:
        assert waiting.wait(1), "reader did not reach its event wait"
    finally:
        svc.unregister_session("sess")
        reader.join(1)

    assert not reader.is_alive(), "session teardown left an event reader blocked"
    assert len(errors) == 1 and isinstance(errors[0], CCIConsumerEvicted)


@pytest.mark.parametrize("method", ["kill_and_evict_by_conv", "kill_and_evict_by_conv_agent"])
def test_explicit_pool_release_unregisters_only_victim_event_session(monkeypatch, method):
    import threading
    from types import SimpleNamespace
    from core.codex_interactive_pool import CodexInteractivePool

    pool = object.__new__(CodexInteractivePool)
    pool._lock = threading.RLock()
    victim = SimpleNamespace(name="finished-container", session_token="finished-token")
    other = SimpleNamespace(name="other-container", session_token="other-token")
    key = ("user", "conv::task::done", "assistant::flash::worker", "svc")
    other_key = ("user", "other-conv", "assistant", "svc")
    pool._sessions = {key: victim, other_key: other}
    events = []
    monkeypatch.setattr(pool, "_recover_container_tokens", lambda _state: None)
    monkeypatch.setattr(pool, "_unregister_event_session", lambda state: events.append(
        ("unregister", state.session_token)))
    monkeypatch.setattr(pool, "_kill_container", lambda name: events.append(("kill", name)))
    args = (key[1],) if method == "kill_and_evict_by_conv" else (key[1], key[2])

    assert getattr(pool, method)(*args, reason="subagent_run_finished") == 1

    assert events == [("unregister", "finished-token"), ("kill", "finished-container")]
    assert pool._sessions == {other_key: other}


def test_capture_release_does_not_remove_replacement_worker(live_task):
    key = "80c37670:claude"
    svc = CCInteractiveEventService({"token": "tok", "_service_id": "events"})
    state = _state(svc)
    assert svc._active_turn_marker(state, register=True) is True

    live_task._active_turns[key] = {
        "conversation_id": "80c37670",
        "agent_name": "claude",
        "owner_id": "replacement-worker",
        "owner_type": "streaming_worker",
        "generation": 5,
    }

    assert svc._active_turn_marker(state, register=False) is False
    assert state.active_turn_owner_id == ""
    assert live_task._active_turns[key]["owner_id"] == "replacement-worker"


def test_marker_is_skipped_without_a_bound_conversation(live_task):
    # An unbound session has nothing to show in any conversation.
    svc = CCInteractiveEventService({"token": "tok", "_service_id": "events"})
    state = svc.register_session("sess")

    svc._active_turn_marker(state, register=True)

    assert live_task._active_turns == {}


def test_live_session_follows_the_proxy_connection(monkeypatch):
    """The proxy WebSocket is the evidence of a live tmux, not a turn flag."""
    svc = CCInteractiveEventService({"token": "tok", "_service_id": "events"})
    state = _state(svc)
    monkeypatch.setitem(CCInteractiveEventService._instances, "events", svc)

    assert CCInteractiveEventService.live_session("80c37670", "claude") is None

    state.connected = True
    assert CCInteractiveEventService.live_session("80c37670", "claude") is state
    # A session belongs to one (conversation, agent) pair only.
    assert CCInteractiveEventService.live_session("80c37670", "other") is None
    assert CCInteractiveEventService.live_session("other", "claude") is None

    # Liveness does not depend on whether a capture happens to hold the turn:
    # traffic through the proxy proves the container either way.
    state.manual_capture_active = True
    assert CCInteractiveEventService.live_session("80c37670", "claude") is state
    state.connected = False
    assert CCInteractiveEventService.live_session("80c37670", "claude") is None


def test_capture_release_hands_queued_messages_back(monkeypatch):
    """Messages typed during a captured turn must not stay queued forever.

    A capture registers `_active_turns` without a streaming worker, so
    agent_streaming parks incoming messages in the PendingQueue and no
    end-of-turn drain ever runs. Before this handback they sat there until a
    force stop discarded them.
    """
    woken = []

    class _Queue:
        @staticmethod
        def for_agent(_cid, _agent):
            return _Queue()

        def peek_count(self):
            return 3

    import core.pending_queue as pq
    monkeypatch.setattr(pq, "PendingQueue", _Queue)

    import tasks.ai.agent_loop as agent_loop
    monkeypatch.setattr(agent_loop.AgentLoopTask, "wake_agent",
                        classmethod(lambda cls, cid, agent, **kw: woken.append(
                            (cid, agent, kw.get("reason", "")))))

    svc = CCInteractiveEventService({"token": "tok", "_service_id": "events"})
    svc._drain_pending_after_capture(_state(svc))

    assert len(woken) == 1
    assert woken[0][0] == "80c37670"
    assert woken[0][1] == "claude"
    assert "3 queued msg(s) after tmux capture" in woken[0][2]


def test_capture_release_is_quiet_on_an_empty_queue(monkeypatch):
    woken = []

    class _Queue:
        @staticmethod
        def for_agent(_cid, _agent):
            return _Queue()

        def peek_count(self):
            return 0

    import core.pending_queue as pq
    monkeypatch.setattr(pq, "PendingQueue", _Queue)
    import tasks.ai.agent_loop as agent_loop
    monkeypatch.setattr(agent_loop.AgentLoopTask, "wake_agent",
                        classmethod(lambda cls, *a, **k: woken.append(a)))

    svc = CCInteractiveEventService({"token": "tok", "_service_id": "events"})
    svc._drain_pending_after_capture(_state(svc))

    assert woken == []


def test_capture_publishes_activity_then_release(live_task, monkeypatch):
    published = []

    class _Writer:
        @staticmethod
        def for_conversation(_cid):
            return _Writer()

        def enqueue_sse_events(self, events):
            published.extend(events)

    import core.conversation_writer as cw
    monkeypatch.setattr(cw, "ConversationWriter", _Writer)

    svc = CCInteractiveEventService({"token": "tok", "_service_id": "events"})
    state = _state(svc)

    svc._publish_capture_active(state, active=True)
    svc._publish_capture_active(state, active=False)

    assert [e["type"] for e in published] == ["thinking", "active_released"]
    assert all(e["data"]["agent_name"] == "claude" for e in published)
    assert live_task._active_turns == {}

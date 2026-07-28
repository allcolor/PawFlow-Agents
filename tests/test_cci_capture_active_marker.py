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

    svc._active_turn_marker(state, register=False)
    assert live_task._active_turns == {}


def test_marker_is_skipped_without_a_bound_conversation(live_task):
    # An unbound session has nothing to show in any conversation.
    svc = CCInteractiveEventService({"token": "tok", "_service_id": "events"})
    state = svc.register_session("sess")

    svc._active_turn_marker(state, register=True)

    assert live_task._active_turns == {}


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

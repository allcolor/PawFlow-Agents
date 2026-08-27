"""A tmux/container crash mid-turn must fail the turn, not hang it.

A tmux server that crashes mid-turn takes the CLI down with it: no Stop
hook, no proxy event and no error ever arrive, so the coordinator waited
forever while queued messages piled up behind an "active" turn that could
never end — the user's only way out was a force stop + resend. The
coordinator now probes container/tmux liveness once the event stream has
been silent, and two consecutive dead probes fail the turn.
"""

from queue import Queue

import pytest

import core.llm_providers._cci_turn as cci_turn
from core.llm_providers._cci_turn import _CCITurnCoordinator


class _Events:
    def __init__(self, events):
        self.q = Queue()
        for event in events:
            self.q.put(event)

    def wait_event(self, session_token, timeout=None):
        if self.q.empty():
            return {}
        return self.q.get()


def _coord(events, liveness):
    return _CCITurnCoordinator(
        _Events(events), "tok-liveness-test", liveness_callback=liveness)


def test_dead_session_fails_the_turn_after_two_probes(monkeypatch):
    monkeypatch.setattr(cci_turn, "_LIVENESS_PROBE_IDLE_SECONDS", 0.0)
    probes = []

    def dead():
        probes.append(1)
        return False

    coord = _coord(
        [{"type": "request_start", "request_id": "r1",
          "path": "/v1/messages"}], dead)
    with pytest.raises(RuntimeError, match="died mid-turn"):
        coord.run()
    # Exactly two consecutive dead probes, never one.
    assert len(probes) == 2


def test_one_dead_probe_is_not_enough_and_a_live_probe_resets(monkeypatch):
    monkeypatch.setattr(cci_turn, "_LIVENESS_PROBE_IDLE_SECONDS", 0.0)
    coord = _coord([], lambda: False)
    coord._probe_liveness(0.0)
    assert coord._liveness_dead_probes == 1
    # A live probe in between clears the strike: transient docker
    # slowness must never accumulate into a kill.
    coord.liveness_callback = lambda: True
    coord._probe_liveness(0.0)
    assert coord._liveness_dead_probes == 0
    coord.liveness_callback = lambda: False
    coord._probe_liveness(0.0)
    assert coord._liveness_dead_probes == 1


def test_probe_errors_never_kill_a_live_turn(monkeypatch):
    monkeypatch.setattr(cci_turn, "_LIVENESS_PROBE_IDLE_SECONDS", 0.0)

    def broken():
        raise OSError("docker daemon busy")

    coord = _coord([], broken)
    coord._probe_liveness(0.0)
    coord._probe_liveness(0.0)
    assert coord._liveness_dead_probes == 0


def test_dead_session_still_fails_during_post_stop_pending_drain(monkeypatch):
    # A response may remain marked as owed after Stop for up to 90 seconds.
    # A dead tmux is definitive and must not leave Active Agents visible for
    # that whole cap.
    monkeypatch.setattr(cci_turn, "_LIVENESS_PROBE_IDLE_SECONDS", 0.0)
    calls = []
    coord = _coord([], lambda: calls.append(1) or False)
    coord._stop_seen = True
    coord._probe_liveness(0.0)
    with pytest.raises(RuntimeError, match="died mid-turn"):
        coord._probe_liveness(0.0)
    assert len(calls) == 2


def test_probe_waits_for_the_idle_window(monkeypatch):
    # Default arming: a stream that produced an event recently is not
    # probed at all — silence is only suspicious after the idle window.
    calls = []
    coord = _coord([], lambda: calls.append(1) or False)
    import time
    coord._last_event_at = time.time()
    coord._probe_liveness(time.time())
    assert not calls


def test_no_callback_keeps_the_old_behavior():
    coord = _CCITurnCoordinator(_Events([]), "tok-liveness-test")
    assert coord.liveness_callback is None
    coord._probe_liveness(0.0)  # must be a no-op, not an error


def test_liveness_probe_is_wired_everywhere():
    """Both interactive providers, both paths, and the manual capture."""
    import inspect
    from core.llm_client import LLMClient
    from core._cci_pool_spawn import _InteractiveContainerSpawnMixin
    from services.cc_interactive_event_service import CCInteractiveEventService

    assert hasattr(_InteractiveContainerSpawnMixin, "session_is_live")
    for method in (LLMClient._stream_claude_code_interactive,
                   LLMClient.interrupt_claude_code_interactive,
                   LLMClient._stream_codex_interactive,
                   LLMClient.interrupt_codex_interactive):
        src = inspect.getsource(method)
        assert "liveness_callback=lambda: pool.session_is_live(state.name)" in src
    capture = inspect.getsource(CCInteractiveEventService._run_manual_capture)
    assert "liveness_callback=self._capture_liveness_callback(state)" in capture
    helper = inspect.getsource(
        CCInteractiveEventService._capture_liveness_callback)
    assert "container_id" in helper

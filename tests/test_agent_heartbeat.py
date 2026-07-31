"""Agent heartbeat lifecycle invariants."""

from types import SimpleNamespace

import pytest

from tasks.ai._alc_base import _ALC_BREAK, _ALC_CONTINUE
from tasks.ai._alc_iteration import _ALCIterationMixin


def test_stream_emitter_keeps_single_heartbeat_and_stops_on_done():
    from tasks.ai.agent_emitter import AgentResult, StreamEmitter

    class _Bus:
        def publish_event(self, *_args, **_kwargs):
            pass

    class _Agent:
        def _is_current_generation(self, *_args, **_kwargs):
            return True

    emitter = StreamEmitter(
        "conv-heartbeat", _Bus(),
        {"active_agent_name": "assistant", "client": SimpleNamespace()},
        _Agent(), "conv-heartbeat:assistant", 1)

    first = emitter.start_heartbeat(poll_silent=True)
    assert len(emitter._active_heartbeats) == 1

    second = emitter.start_heartbeat(poll_silent=True)
    assert first[0].is_set()
    assert len(emitter._active_heartbeats) == 1
    assert emitter._active_heartbeats[0] is second

    emitter.on_done(AgentResult())
    assert second[0].is_set()
    assert emitter._active_heartbeats == []


# -- one iteration owns one heartbeat, on every way out ----------------------

class _CountingEmitter:
    """Records heartbeat starts and stops by handle."""

    def __init__(self):
        self.started = []
        self.stopped = []

    def start_heartbeat(self, poll_silent=False):
        handle = f"hb{len(self.started)}"
        self.started.append(handle)
        return handle

    def stop_heartbeat(self, handle):
        self.stopped.append(handle)


def _iteration_leaving_by(outcome):
    """An iteration whose body starts a heartbeat and then leaves by `outcome`."""

    class _Loop(_ALCIterationMixin):
        def _alc_iteration_body(self, st):
            st._iter_hb = st.emitter.start_heartbeat(False)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

    st = SimpleNamespace(emitter=_CountingEmitter())
    return _Loop(), st


@pytest.mark.parametrize("outcome", [_ALC_CONTINUE, _ALC_BREAK, None])
def test_every_return_stops_the_iteration_heartbeat(outcome):
    """The body leaves by five different returns and only two of them stopped
    it. A compact restart, a cold restart or an overflow retry each left a
    live thread behind — one per attempt, all publishing for the same
    conversation."""
    loop, st = _iteration_leaving_by(outcome)

    assert loop._alc_iteration(st) is outcome
    assert st.emitter.stopped == st.emitter.started == ["hb0"]
    assert st._iter_hb is None


def test_an_exception_stops_the_iteration_heartbeat_too():
    """Cancellation and fatal errors leave through the same door."""
    boom = RuntimeError("cancelled mid-iteration")
    loop, st = _iteration_leaving_by(boom)

    with pytest.raises(RuntimeError):
        loop._alc_iteration(st)
    assert st.emitter.stopped == ["hb0"]


def test_the_heartbeat_is_never_stopped_twice():
    """The body stops it early on purpose — the heartbeat covers the LLM call
    and the tools, not the bookkeeping that follows — and the finally must
    then find nothing left to do."""

    class _Loop(_ALCIterationMixin):
        def _alc_iteration_body(self, st):
            st._iter_hb = st.emitter.start_heartbeat(False)
            self._alc_stop_iteration_heartbeat(st)
            return None

    st = SimpleNamespace(emitter=_CountingEmitter())
    _Loop()._alc_iteration(st)

    assert st.emitter.stopped == ["hb0"]


def test_a_body_that_never_started_one_stops_nothing():
    class _Loop(_ALCIterationMixin):
        def _alc_iteration_body(self, st):
            return None

    st = SimpleNamespace(emitter=_CountingEmitter())
    _Loop()._alc_iteration(st)

    assert st.emitter.stopped == []

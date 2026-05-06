"""Agent heartbeat lifecycle invariants."""

from types import SimpleNamespace


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

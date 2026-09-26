"""A delivery that would cross the threshold makes the running turn compact."""
from types import SimpleNamespace

import pytest

from core.llm_client import CCCompactDetected, LLMMessage
from tasks.ai._alc_closures1 import _ALCClosures1Mixin
from tasks.ai._delivery_limits import COMPACT_REQUEST_KEY, backlog_exceeded


def _state(ctx):
    barriers = []
    return SimpleNamespace(
        conversation_id="conv", ctx=ctx, _auto_compact_state={},
        _agent_compact_threshold_fraction=lambda: 0.9,
        _client_provider="codex-interactive",
        _set_provider_compact_barrier=barriers.append), barriers


def _ctx(**extra):
    ctx = {"max_context_size": 1000, "_is_cli_provider": True,
           # A CLI reconstruction, below the threshold.
           "_context_usage_cache": {"used": 100}}
    ctx.update(extra)
    return ctx


def _append(st):
    _ALCClosures1Mixin._alc_maybe_auto_compact_after_append(
        _ALCClosures1Mixin(), st,
        LLMMessage(role="tool", content="result", conversation_id="conv"),
        "tool")


def test_a_requested_compaction_hands_the_cli_to_the_restart_path():
    st, barriers = _state(_ctx(**{COMPACT_REQUEST_KEY: True}))

    with pytest.raises(CCCompactDetected):
        _append(st)

    assert COMPACT_REQUEST_KEY not in st.ctx
    assert barriers == ["post_append:tool"]


def test_without_a_request_an_unmeasured_gauge_never_compacts():
    st, barriers = _state(_ctx())

    _append(st)

    assert barriers == []


def test_the_turn_checks_the_backlog_at_most_every_five_seconds(monkeypatch):
    from core.llm_providers._cci_turn import _CCITurnCoordinator

    class _Service:
        def wait_event(self, token, timeout=None):
            return {}
    calls = []
    coord = _CCITurnCoordinator(_Service(), "sess",
                                backlog_callback=lambda: calls.append(1))
    clock = [1000.0]
    monkeypatch.setattr("core.llm_providers._cci_turn.time.time",
                        lambda: clock[0])

    coord._probe_backlog()
    coord._probe_backlog()
    clock[0] += 5.0
    coord._probe_backlog()

    assert len(calls) == 2


def test_an_exceeded_backlog_cancels_the_blocking_tool(monkeypatch):
    from tasks.ai import _delivery_limits as limits

    pending = [SimpleNamespace(text="x" * 4000, accepted_at=0.0)]
    pool = SimpleNamespace(unprocessed_submissions=lambda state: pending)
    cancelled = []
    monkeypatch.setattr(
        "services.tool_relay_service.ToolRelayService.cancel_agent",
        lambda cid, agent, **kw: cancelled.append(agent))
    monkeypatch.setattr(limits.time, "time", lambda: 100.0)
    monkeypatch.setattr(limits, "running_turn_context", lambda c, a: {
        "max_context_size": 5000, "chars_per_token": 4})

    assert limits.unblock_backlog(pool, object(), "conv", "GameDev7") is True
    assert cancelled == ["GameDev7"]

    monkeypatch.setattr(limits, "running_turn_context", lambda c, a: {
        "max_context_size": 500000, "chars_per_token": 4})
    assert limits.unblock_backlog(pool, object(), "conv", "GameDev7") is False
    assert cancelled == ["GameDev7"]


def _pending(text, accepted_at):
    return SimpleNamespace(text=text, accepted_at=accepted_at)


def test_backlog_needs_both_the_size_and_the_wait():
    big = [_pending("x" * 4000, 0.0)]          # 1000 tokens
    small = [_pending("x" * 40, 0.0)]
    assert backlog_exceeded(big, max_ctx=5000, chars_per_token=4, now=61.0)
    assert not backlog_exceeded(big, max_ctx=5000, chars_per_token=4, now=59.0)
    assert not backlog_exceeded(small, max_ctx=5000, chars_per_token=4,
                                now=600.0)
    assert not backlog_exceeded([], max_ctx=5000, chars_per_token=4, now=600.0)

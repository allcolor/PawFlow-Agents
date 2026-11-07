"""A delivery that would cross the threshold makes the running turn compact."""
from types import SimpleNamespace

import pytest

from core.llm_client import CCCompactDetected, LLMMessage
from tasks.ai._alc_closures1 import _ALCClosures1Mixin
from tasks.ai._delivery_limits import COMPACT_REQUEST_KEY


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


def test_user_submission_requests_compaction_before_crossing_the_limit(monkeypatch):
    from tasks.ai import _delivery_limits as limits

    ctx = {
        "max_context_size": 1000, "_compact_trigger_fraction": 0.9,
        "_context_usage_cache": {"used": 850}, "chars_per_token": 4,
    }
    monkeypatch.setattr(limits, "running_turn_context", lambda cid, agent: ctx)

    assert not limits.submission_needs_compaction("conv", "a", "small request")
    assert COMPACT_REQUEST_KEY not in ctx
    assert limits.submission_needs_compaction("conv", "a", "x" * 200)
    assert ctx[COMPACT_REQUEST_KEY] is True
    assert limits.take_compact_request("conv", "a") is True
    assert limits.take_compact_request("conv", "a") is False


def test_unknown_limits_do_not_block_a_user_submission(monkeypatch):
    from tasks.ai import _delivery_limits as limits

    monkeypatch.setattr(limits, "running_turn_context", lambda cid, agent: {})

    assert not limits.submission_needs_compaction("conv", "a", "request")


def test_idle_coordinator_serves_a_user_compaction_request(monkeypatch):
    from core.llm_providers._cci_turn import _CCITurnCoordinator

    coord = _CCITurnCoordinator(
        object(), "sess", compact_request_callback=lambda: True)
    monkeypatch.setattr("core.llm_providers._cci_turn.time.time", lambda: 1000)

    with pytest.raises(CCCompactDetected, match="delivery requested compaction"):
        coord._probe_compact_request()

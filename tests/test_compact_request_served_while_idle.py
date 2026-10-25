"""A delivery's compaction request must be served even while the CLI is idle.

``submission_needs_compaction`` sets ``ctx[COMPACT_REQUEST_KEY]`` when the
message it is about to submit would cross the threshold, and the running turn
is supposed to compact before that message lands. Only
``_alc_maybe_auto_compact_after_append`` ever consumed that key, and it runs on
the turn's next assistant/tool append.

A Claude Code turn that is blocked -- waiting on its own native autocompact
offer, or on a tool that produces nothing -- appends nothing for as long as it
is blocked. GameDev7 on 2026-10-24 requested compaction at 22:16:56, 22:42:10,
23:11:20 and 23:12:35 with 839K against a 570K trigger; no append came, so
nothing compacted until 23:29:55, and the queued message sat behind it the
whole time.

These tests pin the idle path: the coordinator's idle probe serves a pending
compaction request by raising ``CCCompactDetected``, which is exactly the
signal the provider already turns into a compact-and-restart.
"""

import pytest

from core.llm_client import CCCompactDetected
from core.llm_providers._cci_turn import _CCITurnCoordinator


def _coord(**kwargs):
    return _CCITurnCoordinator(None, "tok", **kwargs)


def test_idle_probe_serves_a_pending_compaction_request():
    """The idle probe must not wait for an append that never comes."""
    calls = []
    coord = _coord(compact_request_callback=lambda: calls.append(1) or True)

    with pytest.raises(CCCompactDetected):
        coord._probe_compact_request()

    assert calls == [1]


def test_idle_probe_is_quiet_without_a_pending_request():
    coord = _coord(compact_request_callback=lambda: False)
    coord._probe_compact_request()  # must not raise


def test_idle_probe_is_quiet_when_no_callback_is_wired():
    """Providers without the concept (and test doubles) keep working."""
    coord = _coord()
    coord._probe_compact_request()  # must not raise


def test_take_compact_request_consumes_the_flag_once():
    """The request is served exactly once; a second probe stays quiet."""
    import threading
    from types import SimpleNamespace

    from tasks.ai._delivery_limits import (
        COMPACT_REQUEST_KEY, take_compact_request)

    ctx = {"active_agent_name": "assistant", COMPACT_REQUEST_KEY: True}
    fake = SimpleNamespace(_active_contexts={"c:assistant": ctx},
                           _active_contexts_lock=threading.Lock())
    import tasks.ai.agent_loop as agent_loop
    original = agent_loop.AgentLoopTask._live_instance
    agent_loop.AgentLoopTask._live_instance = fake
    try:
        assert take_compact_request("c", "assistant") is True
        assert COMPACT_REQUEST_KEY not in ctx
        assert take_compact_request("c", "assistant") is False
    finally:
        agent_loop.AgentLoopTask._live_instance = original


def test_a_failing_probe_never_breaks_the_turn():
    """A gauge/delivery hiccup must not kill a live turn."""
    def boom():
        raise RuntimeError("delivery state unavailable")

    coord = _coord(compact_request_callback=boom)
    coord._probe_compact_request()  # must not raise

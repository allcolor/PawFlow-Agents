"""A silent interactive CLI turn is surfaced instead of polling forever.

Regression for a turn that stalled with the agent stuck in Active Agents:
the tmux pane was showing a rate-limit banner (or waiting for an answer) while
the coordinator only ever saw an empty event queue, so nothing on the wire
ever explained why the turn could not end.
"""

import time

import pytest

from core._llm_types import LLMCallError
from core.llm_providers._cci_turn import _CCITurnCoordinator
from core.llm_providers._codex_interactive_turn import (
    _CodexInteractiveTurnCoordinator)
from core.llm_providers._managed_mcp_turn import _ManagedMcpTurnCoordinator


class _EmptyService:
    """A session whose queue stays empty: the CLI says nothing at all."""

    def wait_event(self, session_token, timeout=None):
        return {}


class _QueuedService:
    def __init__(self, events):
        self._events = list(events)

    def wait_event(self, session_token, timeout=None):
        return self._events.pop(0) if self._events else {}


def _probe_now(monkeypatch):
    """Make the idle window elapse immediately instead of after 20s."""
    monkeypatch.setattr(
        "core.llm_providers._cci_turn._LIVENESS_PROBE_IDLE_SECONDS", 0.0)


def test_a_rate_limit_banner_in_the_pane_fails_the_turn(monkeypatch):
    _probe_now(monkeypatch)
    pane = ("Claude Code\n"
            "API Error: 429 you have reached your session usage limit\n")
    with pytest.raises(LLMCallError) as info:
        _CCITurnCoordinator(_EmptyService(), "sess",
                            pane_callback=lambda: pane).run()
    assert info.value.retryable is False
    assert info.value.category == "rate_limited"
    assert info.value.provider == "claude-code-interactive"
    # The pane line reaches the user: it is what says why nothing moved.
    assert "usage limit" in str(info.value)


def test_a_question_in_the_pane_fails_the_turn(monkeypatch):
    _probe_now(monkeypatch)
    pane = "1. Switch to another model\n2. Keep this model\nEsc to cancel\n"
    with pytest.raises(LLMCallError) as info:
        _CCITurnCoordinator(_EmptyService(), "sess",
                            pane_callback=lambda: pane).run()
    assert info.value.retryable is False
    assert info.value.category == "question"
    assert "Esc to cancel" in str(info.value)


def test_a_probe_error_never_fails_a_live_turn(monkeypatch):
    """A pane capture that breaks is not evidence the turn is blocked."""
    _probe_now(monkeypatch)

    def _boom():
        raise RuntimeError("docker exec failed")

    coord = _CCITurnCoordinator(_EmptyService(), "sess", pane_callback=_boom)
    coord._probe_pane_blocker(time.time())  # must not raise
    assert coord._rate_limit_responses == 0


def test_the_pane_is_not_read_before_the_idle_window(monkeypatch):
    """A stream that just went quiet for a moment is normal."""
    monkeypatch.setattr(
        "core.llm_providers._cci_turn._LIVENESS_PROBE_IDLE_SECONDS", 20.0)
    captured = []
    coord = _CCITurnCoordinator(
        _EmptyService(), "sess",
        pane_callback=lambda: captured.append(1) or "429 rate limit")
    coord._last_event_at = time.time()
    coord._probe_pane_blocker(time.time())
    assert captured == []


def test_no_pane_callback_keeps_the_old_behavior():
    coord = _CCITurnCoordinator(_EmptyService(), "sess")
    assert coord.pane_callback is None
    coord._probe_pane_blocker(time.time())  # must not raise


def test_three_bare_429_on_the_model_endpoint_fail_the_turn():
    events = [{"type": "response_start", "request_id": f"r{index}",
               "status": "429", "path": "/v1/messages"} for index in range(3)]
    with pytest.raises(LLMCallError) as info:
        _CCITurnCoordinator(_QueuedService(events), "sess").run()
    assert info.value.category == "rate_limited"
    assert info.value.provider_status == 429
    assert info.value.retryable is False


def test_two_bare_429_do_not_fail_the_turn():
    """One or two are normal traffic: the CLI retries them itself."""
    coord = _CCITurnCoordinator(_EmptyService(), "sess")
    for index in range(2):
        coord._remember_response_status(
            {"type": "response_start", "request_id": f"r{index}",
             "status": "429", "path": "/v1/messages"})
    assert coord._rate_limit_responses == 2


def test_a_429_on_a_side_endpoint_is_not_a_blocked_turn():
    coord = _CCITurnCoordinator(_EmptyService(), "sess")
    for index in range(5):
        coord._remember_response_status(
            {"type": "response_start", "request_id": f"m{index}",
             "status": "429", "path": "/api/claude_code/metrics"})
    assert coord._rate_limit_responses == 0


def test_a_discarded_429_body_is_counted_once():
    """The undecodable body carries no status; the paired start does.

    That one response is reported twice (`response_start status=429`, then
    `response_ignored`), and counting both put the real threshold one short of
    what the failure message announced.
    """
    coord = _CCITurnCoordinator(_EmptyService(), "sess")
    coord._remember_response_status(
        {"type": "response_start", "request_id": "r9", "status": "429",
         "path": "/v1/messages"})
    ignored = {"type": "response_ignored", "request_id": "r9",
               "reason": "unsupported_content_encoding"}
    for _ in range(2):
        coord._note_ignored_response(ignored)
    assert coord._rate_limit_responses == 1


def test_three_undecodable_429_responses_still_fail_the_turn():
    coord = _CCITurnCoordinator(_EmptyService(), "sess")
    with pytest.raises(LLMCallError) as info:
        for index in range(3):
            coord._remember_response_status(
                {"type": "response_start", "request_id": f"r{index}",
                 "status": "429", "path": "/v1/messages"})
            coord._note_ignored_response(
                {"type": "response_ignored", "request_id": f"r{index}",
                 "reason": "unsupported_content_encoding"})
    assert info.value.category == "rate_limited"


def test_a_served_model_response_resets_the_streak():
    """Transients the CLI recovered from must not add up to a dead end."""
    coord = _CCITurnCoordinator(_EmptyService(), "sess")
    for index in range(4):
        coord._remember_response_status(
            {"type": "response_start", "request_id": f"r{index}",
             "status": "429", "path": "/v1/messages"})
        assert coord._rate_limit_responses == 1
        coord._remember_response_status(
            {"type": "response_start", "request_id": f"ok{index}",
             "status": "200", "path": "/v1/messages"})
    assert coord._rate_limit_responses == 0


def test_an_ignored_response_without_a_429_status_is_ignored():
    coord = _CCITurnCoordinator(_EmptyService(), "sess")
    coord._remember_response_status(
        {"type": "response_start", "request_id": "r1", "status": "200",
         "path": "/v1/messages"})
    coord._note_ignored_response(
        {"type": "response_ignored", "request_id": "r1",
         "reason": "not_a_message"})
    assert coord._rate_limit_responses == 0


def test_codex_counts_429_on_the_responses_endpoint_only():
    coord = _CodexInteractiveTurnCoordinator(_EmptyService(), "sess")
    coord._remember_response_status(
        {"type": "response_start", "request_id": "a", "status": "429",
         "path": "https://api.openai.com/v1/responses"})
    assert coord._rate_limit_responses == 1
    coord._remember_response_status(
        {"type": "response_start", "request_id": "b", "status": "429",
         "path": "/v1/messages"})
    assert coord._rate_limit_responses == 1


def test_codex_reports_its_own_provider_name(monkeypatch):
    _probe_now(monkeypatch)
    pane = "429 usage limit reached for this session\n"
    with pytest.raises(LLMCallError) as info:
        _CodexInteractiveTurnCoordinator(
            _EmptyService(), "sess", pane_callback=lambda: pane).run()
    assert info.value.provider == "codex-interactive"
    assert info.value.category == "rate_limited"


def test_a_managed_mcp_turn_reports_its_own_provider(monkeypatch):
    """A managed session has no proxy, but it can still be blocked."""
    _probe_now(monkeypatch)
    pane = "Do you want to switch to a different model? [y/n]\n"
    with pytest.raises(LLMCallError) as info:
        _ManagedMcpTurnCoordinator(
            _EmptyService(), "sess", provider="cc_mcp",
            pane_callback=lambda: pane).run()
    assert info.value.provider == "cc_mcp"
    assert info.value.category == "question"
    assert info.value.retryable is False

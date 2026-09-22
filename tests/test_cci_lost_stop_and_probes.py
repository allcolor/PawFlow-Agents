"""An interactive CLI that finished, or was only slow to probe, is not stuck.

Regression for GameDev2 (2026-09-22): the CLI answered at 08:30 and sat at its
prompt, but the Stop hook never reached the server, so the agent stayed in
Active Agents. A cancel started a fresh session while the old container --
dropped from the pool on a `docker inspect` that timed out, never killed --
kept running; its telemetry was adopted as an orphan capture that waited for a
Stop that could never come, and held the agent active for hours.
"""

import subprocess
import threading
import types

import pytest

from core._llm_types import LLMCallError
from core._cci_pool_spawn import _InteractiveContainerSpawnMixin
from core.llm_providers._cci_turn import _CCITurnCoordinator
from core.llm_providers._cli_blockers import cli_pane_is_idle, detect_cli_blocker
from core.llm_providers._codex_interactive_turn import (
    _CodexInteractiveTurnCoordinator)


IDLE_PANE = (
    "● Done: the fix is in place.\n"
    "✻ Worked for 32s\n"
    "────────────────\n"
    "❯ \n"
    "────────────────\n"
    "  ⏵⏵ bypass permissions on (shift+tab to cycle)\n"
)
WORKING_PANE = (
    "✢ Snowflake-crystallizing… (12s · esc to interrupt)\n"
    "  ⏵⏵ bypass permissions on (shift+tab to cycle)\n"
)


class _EmptyService:
    def wait_event(self, session_token, timeout=None):
        return {}


class _QueuedService:
    def __init__(self, events):
        self._events = list(events)

    def wait_event(self, session_token, timeout=None):
        return self._events.pop(0) if self._events else {}


def _probe_now(monkeypatch):
    monkeypatch.setattr(
        "core.llm_providers._cci_turn._LIVENESS_PROBE_IDLE_SECONDS", 0.0)


def _run(coord):
    result = {}

    def _target():
        try:
            result["response"] = coord.run()
        except Exception as exc:  # surfaced to the test below
            result["error"] = exc
    runner = threading.Thread(target=_target, daemon=True)
    runner.start()
    runner.join(15)
    assert not runner.is_alive(), "the coordinator never finished the turn"
    return result


# -- pane classification ---------------------------------------------------

def test_an_idle_prompt_is_idle_and_a_working_one_is_not():
    assert cli_pane_is_idle(IDLE_PANE)
    assert not cli_pane_is_idle(WORKING_PANE)
    # An unreadable pane is not an answer.
    assert not cli_pane_is_idle("")
    # Text alone, without the prompt footer, says nothing about the TUI.
    assert not cli_pane_is_idle("some output\nmore output\n")


@pytest.mark.parametrize("line", [
    "API Error: 401 {\"type\":\"error\",\"error\":{\"type\":"
    "\"authentication_error\"}} · Please run /login",
    "OAuth token has expired. Please obtain a new token.",
    "Invalid API key · Please run /login",
    "■ unexpected status 401 Unauthorized: token expired",
    "Your access token could not be refreshed because your refresh token "
    "was already used. Please log out and sign in again.",
])
def test_authentication_failures_are_blockers(line):
    blocker = detect_cli_blocker(IDLE_PANE + line + "\n")
    assert blocker is not None
    assert blocker.kind == "auth_invalid"


def test_codex_usage_limit_wording_is_a_rate_limit():
    pane = ("■ You've hit your usage limit. Upgrade to Pro or try again at "
            "3:05 PM.\n› \n")
    assert detect_cli_blocker(pane).kind == "rate_limited"


def test_an_auth_error_while_working_is_not_a_blocker():
    assert detect_cli_blocker(
        "reading notes about 401 unauthorized handling\n" + WORKING_PANE) is None


# -- lost Stop hook ----------------------------------------------------------

def test_an_idle_prompt_stands_in_for_a_lost_stop(monkeypatch):
    _probe_now(monkeypatch)
    reads = []
    coord = _CCITurnCoordinator(_QueuedService([
        {"type": "request_start", "request_id": "r1", "path": "/v1/messages"},
        {"type": "sse", "request_id": "r1", "event": "content_block_start",
         "payload": {"type": "content_block_start", "index": 0,
                     "content_block": {"type": "text", "text": ""}}},
        {"type": "sse", "request_id": "r1", "event": "content_block_delta",
         "payload": {"type": "content_block_delta", "index": 0,
                     "delta": {"type": "text_delta", "text": "All done."}}},
        {"type": "sse", "request_id": "r1", "event": "content_block_stop",
         "payload": {"type": "content_block_stop", "index": 0}},
        # request_stop and the Stop hook were both lost.
    ]), "sess", pane_callback=lambda: reads.append(1) or IDLE_PANE)
    result = _run(coord)
    assert "error" not in result, result.get("error")
    assert result["response"].content == "All done."
    assert len(reads) >= 2, "one idle probe alone must never end a turn"


def test_one_idle_probe_is_not_enough(monkeypatch):
    _probe_now(monkeypatch)
    coord = _CCITurnCoordinator(_EmptyService(), "sess",
                                pane_callback=lambda: IDLE_PANE)
    coord._saw_model_content = True
    coord._probe_pane_blocker(0.0)
    assert coord._stop_seen is False
    # A working pane in between restarts the count.
    coord.pane_callback = lambda: WORKING_PANE
    coord._last_pane_probe_at = 0.0
    coord._probe_pane_blocker(0.0)
    coord.pane_callback = lambda: IDLE_PANE
    coord._last_pane_probe_at = 0.0
    coord._probe_pane_blocker(0.0)
    assert coord._stop_seen is False


def test_an_idle_prompt_without_any_answer_fails_visibly(monkeypatch):
    _probe_now(monkeypatch)
    result = _run(_CCITurnCoordinator(
        _EmptyService(), "sess", pane_callback=lambda: IDLE_PANE))
    error = result.get("error")
    assert isinstance(error, LLMCallError)
    assert error.category == "cli_idle"
    assert error.retryable is False


def test_codex_keeps_its_own_end_of_turn_rule(monkeypatch):
    _probe_now(monkeypatch)
    coord = _CodexInteractiveTurnCoordinator(
        _EmptyService(), "sess", pane_callback=lambda: IDLE_PANE)
    for _ in range(3):
        coord._last_pane_probe_at = 0.0
        coord._probe_pane_blocker(0.0)
    assert coord._stop_seen is False


def test_codex_probes_liveness_mid_turn(monkeypatch):
    _probe_now(monkeypatch)
    coord = _CodexInteractiveTurnCoordinator(
        _QueuedService([{"type": "request_start", "request_id": "r1",
                         "path": "/v1/responses", "method": "POST"}]),
        "sess", liveness_callback=lambda: False)
    result = _run(coord)
    assert "died mid-turn" in str(result.get("error"))


# -- docker probes: no answer is not death ---------------------------------

class _Mixin(_InteractiveContainerSpawnMixin):
    def _user_spec(self):
        return "1001:1001"


def _fake_run(monkeypatch, *, returncode=0, stdout="", stderr="", exc=None):
    def _run_cmd(*args, **kwargs):
        if exc is not None:
            raise exc
        return types.SimpleNamespace(returncode=returncode, stdout=stdout,
                                     stderr=stderr)
    monkeypatch.setattr("core._cci_pool_spawn.subprocess.run", _run_cmd)


def test_inspect_timeout_is_not_a_stopped_container(monkeypatch):
    _fake_run(monkeypatch, exc=subprocess.TimeoutExpired("docker", 5))
    assert _Mixin._is_alive("c") is True


def test_inspect_daemon_error_is_not_a_stopped_container(monkeypatch):
    _fake_run(monkeypatch, returncode=1, stderr="Error response from daemon: "
              "context deadline exceeded")
    assert _Mixin._is_alive("c") is True


def test_definitive_inspect_answers_are_trusted(monkeypatch):
    _fake_run(monkeypatch, stdout="false\n")
    assert _Mixin._is_alive("c") is False
    _fake_run(monkeypatch, stdout="true\n")
    assert _Mixin._is_alive("c") is True
    _fake_run(monkeypatch, returncode=1,
              stderr="Error: No such object: c")
    assert _Mixin._is_alive("c") is False


def test_tmux_probe_distinguishes_no_session_from_no_answer(monkeypatch):
    _fake_run(monkeypatch, returncode=1, stderr="can't find session: pawflow")
    assert _Mixin()._tmux_is_alive("c") is False
    _fake_run(monkeypatch, returncode=0)
    assert _Mixin()._tmux_is_alive("c") is True
    _fake_run(monkeypatch, exc=subprocess.TimeoutExpired("docker", 5))
    assert _Mixin()._tmux_is_alive("c") is True
    _fake_run(monkeypatch, returncode=126, stderr="OCI runtime exec failed")
    assert _Mixin()._tmux_is_alive("c") is True


# -- pool bookkeeping: a dropped container never outlives its session -------

def _pool_with_stopped(monkeypatch):
    from core.claude_code_interactive_pool import InteractiveClaudeCodePool
    pool = InteractiveClaudeCodePool.__new__(InteractiveClaudeCodePool)
    pool._lock = threading.RLock()
    state = types.SimpleNamespace(
        name="old", key=("u", "c", "a", "s"), last_used=0.0,
        session_token="tok-old")
    pool._sessions = {state.key: state}
    retired = []
    monkeypatch.setattr(pool, "_is_alive", lambda name: False)
    monkeypatch.setattr(pool, "_recover_container_tokens", lambda s: None)
    monkeypatch.setattr(pool, "_kill_container",
                        lambda name: retired.append(("kill", name)))
    monkeypatch.setattr(pool, "_unregister_event_session",
                        lambda s: retired.append(("unregister", s.session_token)))
    return pool, retired


def test_find_session_retires_a_stopped_container(monkeypatch):
    pool, retired = _pool_with_stopped(monkeypatch)
    assert pool.find_session("u", "c", "a", "s") is None
    assert ("unregister", "tok-old") in retired
    assert ("kill", "old") in retired


def test_list_sessions_retires_a_stopped_container(monkeypatch):
    pool, retired = _pool_with_stopped(monkeypatch)
    assert pool.list_sessions("u", "c") == []
    assert ("unregister", "tok-old") in retired


def test_a_capture_gets_the_pane_probe():
    import inspect
    from services.cc_interactive_event_service import CCInteractiveEventService
    capture = inspect.getsource(CCInteractiveEventService._run_manual_capture)
    assert "pane_callback=self._capture_pane_callback(state)" in capture
    helper = inspect.getsource(CCInteractiveEventService._capture_pane_callback)
    assert "container_id" in helper

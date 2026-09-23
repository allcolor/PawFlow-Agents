"""A container removal that fails must be visible, and must be retried.

Observed incident: five live `pf-*-cci-*` containers for ONE agent. Each
eviction popped the pool entry and called `docker rm -f` without looking at
the result, so a failed removal left a CLI running that nothing tracked: the
webchat answered "No live interactive tmux session for agent 'GameDev2'", the
next turn launched a fresh container, and the abandoned one held Active Agents
until an orphan capture adopted it three hours later.
"""

import subprocess

from core.claude_code_interactive_pool import (
    InteractiveClaudeCodePool, InteractiveContainer)


class _Result:
    def __init__(self, returncode=0, stderr=""):
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = ""


def _state(name="container", token="sess", **kw):
    return InteractiveContainer(
        key=("u", "c", "a", "svc"),
        name=name,
        workdir="/host",
        container_workdir="/cc_sessions/c/a",
        session_token=token,
        event_service_id="events",
        internal_token="internal",
        **kw,
    )


def test_failed_removal_is_reported_and_queued(monkeypatch):
    pool = InteractiveClaudeCodePool()
    monkeypatch.setattr(
        "core.claude_code_interactive_pool.subprocess.run",
        lambda *a, **kw: _Result(1, "Error response from daemon: cannot kill"))

    assert pool._kill_container("live-orphan") is False
    assert "live-orphan" in pool._pending_kills


def test_successful_removal_clears_the_queue(monkeypatch):
    pool = InteractiveClaudeCodePool()
    pool._pending_kills.add("container")
    monkeypatch.setattr(
        "core.claude_code_interactive_pool.subprocess.run",
        lambda *a, **kw: _Result(0))

    assert pool._kill_container("container") is True
    assert not pool._pending_kills


def test_already_gone_is_not_a_failure(monkeypatch):
    """`No such container` is the outcome we wanted, not a reason to retry."""
    pool = InteractiveClaudeCodePool()
    monkeypatch.setattr(
        "core.claude_code_interactive_pool.subprocess.run",
        lambda *a, **kw: _Result(1, "Error: No such container: gone"))

    assert pool._kill_container("gone") is True
    assert not pool._pending_kills


def test_a_docker_that_never_answers_is_queued(monkeypatch):
    pool = InteractiveClaudeCodePool()

    def _timeout(*_a, **_kw):
        raise subprocess.TimeoutExpired(cmd="docker", timeout=15)

    monkeypatch.setattr(
        "core.claude_code_interactive_pool.subprocess.run", _timeout)

    assert pool._kill_container("slow") is False
    assert "slow" in pool._pending_kills


def test_sweeper_retries_a_failed_kill_until_the_container_is_gone(monkeypatch):
    pool = InteractiveClaudeCodePool()
    attempts = []
    alive = {"orphan": True}

    def _run(argv, **_kw):
        attempts.append(argv[-1])
        if len(attempts) < 3:
            return _Result(1, "Error response from daemon: cannot kill")
        alive["orphan"] = False
        return _Result(0)

    monkeypatch.setattr(
        "core.claude_code_interactive_pool.subprocess.run", _run)
    monkeypatch.setattr(pool, "_is_alive", lambda name: alive.get(name, False))

    assert pool._kill_container("orphan") is False
    assert pool.sweep_idle() == 0          # tick 1: retry, still refused
    assert pool._pending_kills == {"orphan"}
    assert pool.sweep_idle() == 0          # tick 2: removal succeeds
    assert not pool._pending_kills
    assert attempts == ["orphan", "orphan", "orphan"]


def test_a_kill_that_failed_is_retried_not_forgotten_by_a_silent_probe(monkeypatch):
    """A docker inspect that cannot answer reads as alive, so the retry stays."""
    pool = InteractiveClaudeCodePool()
    pool._pending_kills.add("orphan")
    monkeypatch.setattr(pool, "_is_alive", lambda name: True)
    monkeypatch.setattr(
        "core.claude_code_interactive_pool.subprocess.run",
        lambda *a, **kw: _Result(1, "Error response from daemon: cannot kill"))

    assert pool._retry_pending_kills() == 1
    assert pool._pending_kills == {"orphan"}


def test_evicting_a_session_whose_removal_fails_keeps_the_orphan_tracked(monkeypatch):
    """The pool entry still goes (the session is unusable), but the container
    is not forgotten: the sweeper owns it now."""
    pool = InteractiveClaudeCodePool()
    state = _state(name="orphan")
    pool._sessions[state.key] = state
    monkeypatch.setattr(pool, "_recover_container_tokens", lambda _s: None)
    monkeypatch.setattr(pool, "_unregister_event_session", lambda _s: None)
    monkeypatch.setattr(
        "core.claude_code_interactive_pool.subprocess.run",
        lambda *a, **kw: _Result(1, "Error response from daemon: cannot kill"))

    assert pool.kill_session("u", "c", "a", "svc") is True
    assert state.key not in pool._sessions
    assert pool._pending_kills == {"orphan"}

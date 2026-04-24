"""Unit tests for the 1-CC-per-container ClaudeCodePool.

The real pool shells out to `docker run / rm / inspect / exec`. We patch
the two choke points it uses (subprocess.run and subprocess.Popen) so the
tests exercise only the bookkeeping logic: cap enforcement, ready-pool
preference, release-kills-container, top-up behavior, reaping.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from unittest.mock import patch, MagicMock

import pytest

import core.claude_code_pool as pool_mod
from core.claude_code_pool import ClaudeCodePool, _ContainerInfo


@pytest.fixture
def docker_stub(monkeypatch):
    """Stub every docker subprocess.run call to a harmless success.

    Records the argv of each invocation so tests can assert on what the
    pool actually shelled out.
    """
    calls: list = []

    def _fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        # ps -a --filter name=pf-cc-pool- --> return no orphans
        # inspect --> Running=true by default
        # run / rm / exec --> returncode 0
        if "inspect" in cmd and "-f" in cmd:
            mock = MagicMock(returncode=0, stdout="true\n", stderr="")
        elif "ps" in cmd:
            mock = MagicMock(returncode=0, stdout="", stderr="")
        else:
            mock = MagicMock(returncode=0, stdout="ok\n", stderr="")
        return mock

    monkeypatch.setattr(subprocess, "run", _fake_run)
    return calls


@pytest.fixture
def pool(docker_stub, monkeypatch, tmp_path):
    """Fresh pool instance (NOT the singleton) with safe env."""
    # Force small cap for tests + no prewarm unless test opts in
    monkeypatch.setenv("PAWFLOW_CC_POOL_MAX", "3")
    monkeypatch.setenv("PAWFLOW_CC_POOL_PREWARM", "0")
    monkeypatch.setenv("PAWFLOW_CC_POOL_IDLE", "300")
    # Point CLAUDE_SESSIONS_DIR at tmp so the pool doesn't touch the repo.
    import core.paths as _paths
    monkeypatch.setattr(_paths, "CLAUDE_SESSIONS_DIR", tmp_path / "cc")
    p = ClaudeCodePool()
    # Stop any reaper thread that might get started by _ensure_reaper.
    p._reaper_started = True  # suppress spawn
    return p


# ── acquire / release ───────────────────────────────────────


def test_acquire_spawns_when_ready_empty(pool):
    name = pool.acquire()
    assert name in pool._active
    assert name not in pool._ready
    assert name.startswith("pf-cc-pool-")
    assert pool._active[name].active_sessions == 1


def test_acquire_prefers_ready_pool(pool):
    """Inject a ready container directly; acquire should grab it instead of
    spawning."""
    pool._ready["pf-cc-pool-ready01"] = _ContainerInfo(
        name="pf-cc-pool-ready01", active_sessions=0, max_sessions=1,
        last_used=time.monotonic())
    name = pool.acquire()
    assert name == "pf-cc-pool-ready01"
    assert name in pool._active
    assert name not in pool._ready
    assert pool._active[name].active_sessions == 1


def test_acquire_ready_is_fifo(pool):
    """First-in ready container wins (oldest reused first)."""
    for i in range(3):
        pool._ready[f"pf-cc-pool-r{i}"] = _ContainerInfo(
            name=f"pf-cc-pool-r{i}", active_sessions=0, max_sessions=1,
            last_used=time.monotonic())
    # dict preserves insertion order
    picked = [pool.acquire() for _ in range(3)]
    assert picked == ["pf-cc-pool-r0", "pf-cc-pool-r1", "pf-cc-pool-r2"]


def test_release_kills_container(pool, docker_stub):
    name = pool.acquire()
    before_count = sum(1 for c in docker_stub if "rm" in c and "-f" in c)
    pool.release(name)
    after_count = sum(1 for c in docker_stub if "rm" in c and "-f" in c)
    assert after_count == before_count + 1, "release must docker rm -f the container"
    assert name not in pool._active
    assert name not in pool._ready


def test_release_unknown_container_is_noop(pool, docker_stub):
    before = len(docker_stub)
    pool.release("pf-cc-pool-nonexistent")
    # Only the warn log, no docker call (we can't assert log; assert
    # there's no docker rm call for this name).
    rm_calls = [c for c in docker_stub[before:]
                if "rm" in c and "-f" in c and "pf-cc-pool-nonexistent" in c]
    assert rm_calls == []


def test_release_forced_on_ready_container(pool, docker_stub):
    """Buggy caller release()s a container that's still in ready — we kill
    it anyway to avoid a leak."""
    pool._ready["pf-cc-pool-ready"] = _ContainerInfo(
        name="pf-cc-pool-ready", active_sessions=0, max_sessions=1)
    pool.release("pf-cc-pool-ready")
    assert "pf-cc-pool-ready" not in pool._ready
    rm_calls = [c for c in docker_stub
                if "rm" in c and "-f" in c and "pf-cc-pool-ready" in c]
    assert len(rm_calls) == 1


# ── exhaustion ──────────────────────────────────────────────


def test_acquire_raises_when_at_cap(pool):
    for _ in range(3):  # cap = 3 from fixture
        pool.acquire()
    assert len(pool._active) == 3
    with pytest.raises(RuntimeError, match="pool exhausted"):
        pool.acquire()


def test_active_plus_ready_counted_toward_cap(pool):
    """A ready container fills a slot just like an active one."""
    # Fill cap with 2 active + 1 ready
    pool.acquire()
    pool.acquire()
    pool._ready["pf-cc-pool-ready"] = _ContainerInfo(
        name="pf-cc-pool-ready", active_sessions=0, max_sessions=1)
    assert len(pool._active) + len(pool._ready) == 3
    # Next acquire should take the ready one (preferred)
    name = pool.acquire()
    assert name == "pf-cc-pool-ready"
    # Now all 3 slots are active; cap hit
    with pytest.raises(RuntimeError, match="pool exhausted"):
        pool.acquire()


# ── top-up / pre-warm ─────────────────────────────────────────


def test_topup_spawns_up_to_prewarm_count(docker_stub, monkeypatch, tmp_path):
    monkeypatch.setenv("PAWFLOW_CC_POOL_MAX", "5")
    monkeypatch.setenv("PAWFLOW_CC_POOL_PREWARM", "2")
    import core.paths as _paths
    monkeypatch.setattr(_paths, "CLAUDE_SESSIONS_DIR", tmp_path / "cc")
    p = ClaudeCodePool()
    p._reaper_started = True

    p._topup_ready()  # synchronous call for determinism
    assert len(p._ready) == 2
    assert len(p._active) == 0


def test_topup_respects_cap(docker_stub, monkeypatch, tmp_path):
    monkeypatch.setenv("PAWFLOW_CC_POOL_MAX", "3")
    monkeypatch.setenv("PAWFLOW_CC_POOL_PREWARM", "10")  # way over cap
    import core.paths as _paths
    monkeypatch.setattr(_paths, "CLAUDE_SESSIONS_DIR", tmp_path / "cc")
    p = ClaudeCodePool()
    p._reaper_started = True

    p._topup_ready()
    assert len(p._ready) == 3  # capped


def test_topup_no_op_when_prewarm_zero(pool):
    # pool fixture has PREWARM=0 — trigger_topup() must return immediately
    # without spawning.
    before_active = len(pool._active)
    before_ready = len(pool._ready)
    pool._trigger_topup()
    time.sleep(0.05)
    assert len(pool._active) == before_active
    assert len(pool._ready) == before_ready


# ── reaper ───────────────────────────────────────────────────


def test_reap_idle_kills_stale_ready(pool, docker_stub):
    # Insert a ready container that's been idle past the TTL (300s).
    stale = _ContainerInfo(
        name="pf-cc-pool-stale", active_sessions=0, max_sessions=1,
        last_used=time.monotonic() - 301)
    fresh = _ContainerInfo(
        name="pf-cc-pool-fresh", active_sessions=0, max_sessions=1,
        last_used=time.monotonic() - 10)
    pool._ready["pf-cc-pool-stale"] = stale
    pool._ready["pf-cc-pool-fresh"] = fresh
    pool._reap_idle()
    assert "pf-cc-pool-stale" not in pool._ready
    assert "pf-cc-pool-fresh" in pool._ready
    rm_stale = [c for c in docker_stub
                if "rm" in c and "-f" in c and "pf-cc-pool-stale" in c]
    assert len(rm_stale) == 1


def test_reap_forgets_dead_active(pool, monkeypatch, docker_stub):
    """An active container that died outside our control is dropped from
    bookkeeping so the cap accounting stays honest."""
    name = pool.acquire()
    # Patch inspect to say 'Running=false' for THIS container.
    def _fake_run_dead(cmd, *args, **kwargs):
        if "inspect" in cmd and name in cmd:
            return MagicMock(returncode=0, stdout="false\n", stderr="")
        return MagicMock(returncode=0, stdout="ok\n", stderr="")
    monkeypatch.setattr(subprocess, "run", _fake_run_dead)

    pool._reap_idle()
    assert name not in pool._active


# ── status / shutdown ─────────────────────────────────────────


def test_status_shape(pool):
    pool.acquire()
    pool._ready["pf-cc-pool-r1"] = _ContainerInfo(
        name="pf-cc-pool-r1", active_sessions=0, max_sessions=1)
    s = pool.status()
    assert s["total"] == 2
    assert s["max"] == 3
    assert s["prewarm"] == 0
    assert len(s["active"]) == 1
    assert len(s["ready"]) == 1
    assert "image" in s


def test_shutdown_kills_all(pool, docker_stub):
    for _ in range(2):
        pool.acquire()
    pool._ready["pf-cc-pool-r1"] = _ContainerInfo(
        name="pf-cc-pool-r1", active_sessions=0, max_sessions=1)
    pool.shutdown()
    assert len(pool._active) == 0
    assert len(pool._ready) == 0
    # At least 3 docker rm -f calls (2 active + 1 ready)
    rm_calls = [c for c in docker_stub if "rm" in c and "-f" in c]
    assert len(rm_calls) >= 3


# ── invariant: no slot sharing ──────────────────────────────────────


def test_max_sessions_is_one(pool):
    """Regression guard: a container must never host more than one exec.
    The 1:1 invariant is the whole reason for the rewrite (kill-cascade
    isolation via separate PID namespaces)."""
    name = pool.acquire()
    info = pool._active[name]
    assert info.max_sessions == 1
    assert info.active_sessions == 1
    # No way to grab the same container again
    second = pool.acquire()
    assert second != name
    assert pool._active[name].active_sessions == 1
    assert pool._active[second].active_sessions == 1

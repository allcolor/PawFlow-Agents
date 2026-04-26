"""Unit tests for core.cc_live_registry.

Covers register/get/touch/evict/kill_and_evict, idle sweeper, shutdown,
and drift-key safety (different keys map to different entries).
No Docker, no real subprocess — a fake proc with a settable poll value.
"""

from __future__ import annotations

import queue
import threading
import time

import pytest

from core.cc_live_registry import (
    CCLiveSession,
    LiveSessionRegistry,
)


class _FakeProc:
    """Minimal stand-in for subprocess.Popen with controllable poll()."""

    def __init__(self, alive: bool = True):
        self._alive = alive
        self.terminated = False
        self.killed = False
        self.wait_calls = 0

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self.terminated = True
        self._alive = False

    def wait(self, timeout=None):
        self.wait_calls += 1
        return 0

    def kill(self):
        self.killed = True
        self._alive = False

    def die(self):
        self._alive = False


def _mk_session(alive: bool = True, svc: str = "svc-a",
                idx: int = 0, last_used_offset: float = 0.0,
                pool_container: str = "") -> CCLiveSession:
    """Build a fake session; last_used_offset lets tests age the entry."""
    session = CCLiveSession(
        proc=_FakeProc(alive=alive),
        event_q=queue.Queue(),
        reader_thread=threading.Thread(target=lambda: None),
        stop_event=threading.Event(),
        pool_container=pool_container or None,
        workdir="/tmp/workdir",
        service_id=svc,
        svc_pool_idx=idx,
    )
    if last_used_offset:
        session.last_used = time.monotonic() - last_used_offset
    return session


@pytest.fixture
def reg():
    """Fresh (non-singleton) registry per test.

    We bypass `instance()` to avoid cross-test state pollution.
    """
    return LiveSessionRegistry()


# ── register / get ──────────────────────────────────────────


def test_register_then_get_roundtrip(reg):
    key = ("u1", "c1", "agent", "svc-a", 0)
    s = _mk_session()
    reg.register(key, s)
    assert reg.get(key) is s
    assert len(reg) == 1


def test_get_missing_returns_none(reg):
    assert reg.get(("u", "c", "a", "svc", 0)) is None


def test_get_auto_evicts_dead_proc(reg):
    key = ("u1", "c1", "agent", "svc-a", 0)
    s = _mk_session(alive=True)
    reg.register(key, s)
    s.proc.die()
    assert reg.get(key) is None
    assert len(reg) == 0, "dead entry must be auto-evicted on get"


def test_different_keys_distinct_entries(reg):
    k1 = ("u1", "c1", "agent", "svc-a", 0)
    k2 = ("u1", "c1", "agent", "svc-b", 0)  # service drift
    k3 = ("u1", "c1", "agent", "svc-a", 1)  # pool_idx drift
    reg.register(k1, _mk_session(svc="svc-a", idx=0))
    reg.register(k2, _mk_session(svc="svc-b", idx=0))
    reg.register(k3, _mk_session(svc="svc-a", idx=1))
    assert len(reg) == 3
    assert reg.get(k1).service_id == "svc-a"
    assert reg.get(k2).service_id == "svc-b"
    assert reg.get(k3).svc_pool_idx == 1


def test_register_same_key_overwrites(reg):
    key = ("u1", "c1", "agent", "svc", 0)
    s1 = _mk_session()
    s2 = _mk_session()
    reg.register(key, s1)
    reg.register(key, s2)
    assert reg.get(key) is s2
    assert len(reg) == 1


# ── touch ─────────────────────────────────────────────────────


def test_touch_increments_reuse_count_and_bumps_last_used(reg):
    key = ("u", "c", "a", "svc", 0)
    s = _mk_session(last_used_offset=10.0)
    reg.register(key, s)
    before = s.last_used
    reg.touch(key)
    reg.touch(key)
    assert s.reuse_count == 2
    assert s.last_used > before


def test_touch_missing_key_is_noop(reg):
    # Must not raise.
    reg.touch(("nope", "x", "y", "z", 0))


def test_touch_bump_reuse_false_only_bumps_last_used(reg):
    # Per-turn keep-alive uses bump_reuse=False so the reuse counter
    # tracks stream-call resumes, not individual turns.
    key = ("u", "c", "a", "svc", 0)
    s = _mk_session(last_used_offset=10.0)
    reg.register(key, s)
    before = s.last_used
    reg.touch(key, bump_reuse=False)
    reg.touch(key, bump_reuse=False)
    assert s.reuse_count == 0
    assert s.last_used > before


def test_per_turn_touch_prevents_idle_eviction(reg):
    # Repro of the sweeper eviction bug: a long stream that bumps
    # last_used per turn must not be evicted, even past idle_ttl.
    key = ("u", "c", "a", "svc", 0)
    s = _mk_session(last_used_offset=2000.0)  # registered 2000s ago
    reg.register(key, s)
    reg.touch(key, bump_reuse=False)  # one turn just flushed
    killed = reg.sweep_idle(idle_ttl_seconds=1800)
    assert killed == 0
    assert reg.get(key) is s


def test_reuse_entry_touch_prevents_eviction_in_init_window(reg):
    # Repro of the second sweeper eviction bug: a session whose previous
    # stream ended >idle_ttl ago is REUSED (caller does get(key)) and
    # the new stream needs >0s to emit its first turn (CC init, slow
    # tool reply). If the caller does NOT touch at REUSE entry, the
    # sweeper will fire in the init window and evict the still-alive
    # session, surfacing as a hard-fail mid-stream.
    #
    # Contract under test: touch(key) at REUSE entry resets last_used
    # AND bumps reuse_count, closing the race.
    key = ("u", "c", "a", "svc", 0)
    s = _mk_session(last_used_offset=1830.0)  # last touch 1830s ago
    reg.register(key, s)
    # The caller's REUSE flow: get + touch (bump_reuse=True default).
    got = reg.get(key)
    assert got is s
    reg.touch(key)
    # Sweeper ticks BEFORE any per-turn flush — must keep the session.
    killed = reg.sweep_idle(idle_ttl_seconds=1800)
    assert killed == 0
    assert reg.get(key) is s
    assert s.reuse_count == 1


# ── evict / kill_and_evict ─────────────────────────────────────


def test_evict_removes_without_killing(reg):
    key = ("u", "c", "a", "svc", 0)
    s = _mk_session()
    reg.register(key, s)
    out = reg.evict(key, "test")
    assert out is s
    assert len(reg) == 0
    assert s.proc._alive  # NOT killed


def test_kill_and_evict_calls_killer(reg):
    key = ("u", "c", "a", "svc", 0)
    s = _mk_session()
    reg.register(key, s)
    killed = []
    reg.kill_and_evict(key, "compact", killer=lambda p: killed.append(p))
    assert killed == [s.proc]
    assert len(reg) == 0
    assert s.stop_event.is_set()


def test_kill_and_evict_fallback_without_killer(reg):
    key = ("u", "c", "a", "svc", 0)
    s = _mk_session()
    reg.register(key, s)
    reg.kill_and_evict(key, "shutdown", killer=None)
    assert s.proc.terminated
    assert s.proc.wait_calls >= 1


def test_kill_and_evict_missing_is_noop(reg):
    calls = []
    reg.kill_and_evict(
        ("nope", "x", "y", "z", 0), "gone",
        killer=lambda p: calls.append(p))
    assert calls == []


def test_teardown_revokes_mcp_internal_token(reg, monkeypatch):
    """MCP internal-auth token lifetime is tied to the live session, so
    teardown (kill_and_evict / sweep / shutdown) must revoke it — not
    turn end. This prevents token accumulation in core.internal_auth.
    """
    revoked = []

    import core.internal_auth as ia
    monkeypatch.setattr(ia, "revoke_token", lambda t: revoked.append(t))

    key = ("u", "c", "a", "svc", 0)
    s = _mk_session()
    s.mcp_internal_token = "token-xyz"
    reg.register(key, s)
    reg.kill_and_evict(key, "compact", killer=lambda p: None)
    assert revoked == ["token-xyz"]


def test_teardown_skips_revoke_when_no_token(reg, monkeypatch):
    revoked = []
    import core.internal_auth as ia
    monkeypatch.setattr(ia, "revoke_token", lambda t: revoked.append(t))

    key = ("u", "c", "a", "svc", 0)
    s = _mk_session()  # no token set
    reg.register(key, s)
    reg.kill_and_evict(key, "shutdown", killer=lambda p: None)
    assert revoked == []


def test_kill_and_evict_by_conv_targets_only_matching_conv(reg):
    """Context edit on conv C1 must kill every agent/service combination
    pinned to C1 while leaving C2 untouched."""
    reg.register(("u", "C1", "agentA", "svc", 0), _mk_session())
    reg.register(("u", "C1", "agentB", "svc", 0), _mk_session())
    reg.register(("u", "C1", "agentA", "svc", 1), _mk_session())  # pool-idx drift
    reg.register(("u", "C2", "agentA", "svc", 0), _mk_session())
    killed = []
    n = reg.kill_and_evict_by_conv(
        "C1", reason="edit", killer=lambda p: killed.append(p))
    assert n == 3
    assert len(reg) == 1
    # Only C2's session survives.
    assert reg.get(("u", "C2", "agentA", "svc", 0)) is not None
    assert len(killed) == 3


def test_kill_and_evict_by_conv_agent_scope(reg):
    """Per-agent invalidation must spare sibling agents in the same conv."""
    reg.register(("u", "C1", "agentA", "svc", 0), _mk_session())
    reg.register(("u", "C1", "agentA", "svc", 1), _mk_session())
    reg.register(("u", "C1", "agentB", "svc", 0), _mk_session())
    n = reg.kill_and_evict_by_conv_agent(
        "C1", "agentA", reason="compact", killer=lambda p: None)
    assert n == 2
    assert reg.get(("u", "C1", "agentB", "svc", 0)) is not None
    # agentA entries both gone
    assert reg.get(("u", "C1", "agentA", "svc", 0)) is None
    assert reg.get(("u", "C1", "agentA", "svc", 1)) is None


def test_kill_and_evict_by_conv_empty_registry_noop(reg):
    # Must not raise; just returns 0.
    assert reg.kill_and_evict_by_conv(
        "nope", reason="edit", killer=lambda p: None) == 0


# ── sweep_idle ──────────────────────────────────────────────


def test_sweep_idle_evicts_expired_and_dead(reg):
    k_fresh = ("u", "c", "fresh", "svc", 0)
    k_stale = ("u", "c", "stale", "svc", 0)
    k_dead = ("u", "c", "dead", "svc", 0)
    s_fresh = _mk_session(last_used_offset=10.0)
    s_stale = _mk_session(last_used_offset=7200.0)
    s_dead = _mk_session(alive=False)
    reg.register(k_fresh, s_fresh)
    reg.register(k_stale, s_stale)
    reg.register(k_dead, s_dead)

    killed = []
    n = reg.sweep_idle(idle_ttl_seconds=3600,
                      killer=lambda p: killed.append(p))
    assert n == 2
    assert reg.get(k_fresh) is s_fresh
    assert reg.get(k_stale) is None
    assert reg.get(k_dead) is None
    assert s_stale.proc in killed  # killer called for stale
    # dead proc may not end up in killed list if poll() == 0 triggers
    # killer branch anyway — the important thing is it's evicted.


def test_sweep_idle_noop_when_all_fresh(reg):
    reg.register(("u", "c", "a", "svc", 0), _mk_session())
    reg.register(("u", "c", "b", "svc", 0), _mk_session())
    n = reg.sweep_idle(idle_ttl_seconds=3600,
                      killer=lambda p: None)
    assert n == 0
    assert len(reg) == 2


# ── shutdown_all ────────────────────────────────────────────


def test_shutdown_all_kills_everything(reg):
    for i in range(5):
        reg.register(("u", "c", f"a{i}", "svc", i), _mk_session())
    killed = []
    reg.shutdown_all(killer=lambda p: killed.append(p))
    assert len(killed) == 5
    assert len(reg) == 0


def test_shutdown_stops_sweeper(reg):
    # Start sweeper, then shutdown; the sweeper_stop event must be set.
    reg.ensure_sweeper(tick_seconds=60, idle_ttl_seconds=3600,
                       killer=lambda p: None)
    assert reg._sweeper_started
    reg.shutdown_all(killer=lambda p: None)
    assert reg._sweeper_stop.is_set()


# ── status ───────────────────────────────────────────────────


def test_status_snapshot_shape(reg):
    reg.register(("user1", "conv1", "agentA", "svc-a", 2),
                 _mk_session(svc="svc-a", idx=2))
    st = reg.status()
    assert len(st) == 1
    entry = st[0]
    for field_name in ("user_id", "conv_id", "agent_name", "service_id",
                       "svc_pool_idx", "live", "idle_seconds",
                       "reuse_count", "spawn_at", "lived_seconds"):
        assert field_name in entry, f"missing {field_name} in status entry"
    assert entry["service_id"] == "svc-a"
    assert entry["svc_pool_idx"] == 2
    assert entry["live"] is True


# ── concurrency ──────────────────────────────────────────────


def test_concurrent_register_and_get(reg):
    N = 50
    errors = []

    def writer(i):
        try:
            reg.register(("u", "c", f"a{i}", "svc", i), _mk_session())
        except Exception as e:
            errors.append(e)

    def reader(i):
        try:
            _ = reg.get(("u", "c", f"a{i}", "svc", i))
        except Exception as e:
            errors.append(e)

    threads = []
    for i in range(N):
        threads.append(threading.Thread(target=writer, args=(i,)))
        threads.append(threading.Thread(target=reader, args=(i,)))
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(reg) == N


# ── singleton ─────────────────────────────────────────────────


def test_instance_is_singleton():
    a = LiveSessionRegistry.instance()
    b = LiveSessionRegistry.instance()
    assert a is b

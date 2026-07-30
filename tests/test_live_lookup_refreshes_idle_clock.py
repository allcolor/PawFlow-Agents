"""Looking a live session up IS using it: the idle clock restarts.

The idle TTL exists to reap containers nobody asks for. ``last_used`` is the
only evidence the sweeper has, and the lookups that hand a container to a
caller did not refresh it -- so a session sitting at the very end of its TTL
could be found alive by the context phase and swept before the provider
claimed it, one tick later. The turn then started cold against a context the
context phase had already thrown away, because it had been told the session
was warm.

Refreshing on every access removes the window instead of compensating for it:
the clock cannot expire while somebody holds the session. CC's per-turn
``touch`` already documents the same bug caught mid-stream; these are the
lookups that were still missing the rule.
"""
import time


class _Alive:
    """A container the liveness probes accept."""

    def __call__(self):
        return True


def _stale(entry, seconds):
    """Push an entry to ``seconds`` of idleness."""
    entry.last_used = time.monotonic() - seconds
    entry.is_container_alive = _Alive()
    return entry


def _no_reaping(reg):
    """Record evictions instead of talking to docker."""
    killed = []
    reg.kill_and_evict = lambda key, reason: killed.append((key, reason))
    return killed


# -- codex ------------------------------------------------------------------

def _codex():
    from core.codex_live_registry import CodexLiveRegistry
    reg = CodexLiveRegistry()
    reg._sweeper_stop.set()
    return reg


def test_codex_exact_lookup_restarts_the_idle_clock():
    reg = _codex()
    key = ("user", "conv", "assistant", "svc", 0)
    entry = _stale(reg.register(key, "container", "/tmp/work",
                                service_id="svc"), 1799)

    reg.get(key)

    assert entry.last_used > time.monotonic() - 5


def test_codex_compatible_lookup_restarts_the_idle_clock():
    reg = _codex()
    key = ("user", "conv", "assistant", "svc", 2)
    entry = _stale(reg.register(key, "container", "/tmp/work",
                                service_id="svc"), 1799)

    assert reg.get_compatible("user", "conv", "assistant", "svc") is not None
    assert entry.last_used > time.monotonic() - 5


def test_codex_a_session_just_handed_out_survives_the_next_sweep():
    """The exact race: found at 1799s, swept at 1800s, dead on arrival."""
    reg = _codex()
    key = ("user", "conv", "assistant", "svc", 0)
    _stale(reg.register(key, "container", "/tmp/work", service_id="svc"), 1801)
    killed = _no_reaping(reg)

    reg.get(key)                      # the context phase asks
    assert reg.sweep_idle(1800) == 0  # the sweeper ticks right after
    assert killed == []


def test_codex_a_session_nobody_asked_for_is_still_reaped():
    """The refresh must not turn the idle TTL off."""
    reg = _codex()
    key = ("user", "conv", "assistant", "svc", 0)
    _stale(reg.register(key, "container", "/tmp/work", service_id="svc"), 1801)
    killed = _no_reaping(reg)

    assert reg.sweep_idle(1800) == 1
    assert [k for k, _ in killed] == [key]


def test_codex_an_active_turn_is_never_reaped_however_old():
    reg = _codex()
    key = ("user", "conv", "assistant", "svc", 0)
    _stale(reg.register(key, "container", "/tmp/work", service_id="svc",
                        active_turn=True), 9999)
    killed = _no_reaping(reg)

    assert reg.sweep_idle(1800) == 0
    assert killed == []


# -- gemini -----------------------------------------------------------------

def _gemini():
    from core.gemini_live_registry import GeminiLiveRegistry
    reg = GeminiLiveRegistry()
    reg._sweeper_stop.set()
    return reg


def test_gemini_exact_lookup_restarts_the_idle_clock():
    reg = _gemini()
    key = ("user", "conv", "assistant", "svc", 0)
    entry = _stale(reg.register(key, "container", "/tmp/work",
                                service_id="svc"), 1799)

    reg.get(key)

    assert entry.last_used > time.monotonic() - 5


def test_gemini_compatible_lookup_restarts_the_idle_clock():
    reg = _gemini()
    key = ("user", "conv", "assistant", "svc", 2)
    entry = _stale(reg.register(key, "container", "/tmp/work",
                                service_id="svc"), 1799)

    assert reg.get_compatible("user", "conv", "assistant", "svc") is not None
    assert entry.last_used > time.monotonic() - 5


def test_gemini_a_session_just_handed_out_survives_the_next_sweep():
    reg = _gemini()
    key = ("user", "conv", "assistant", "svc", 0)
    _stale(reg.register(key, "container", "/tmp/work", service_id="svc"), 1801)
    killed = _no_reaping(reg)

    reg.get(key)
    assert reg.sweep_idle(1800) == 0
    assert killed == []


def test_gemini_a_session_nobody_asked_for_is_still_reaped():
    reg = _gemini()
    key = ("user", "conv", "assistant", "svc", 0)
    _stale(reg.register(key, "container", "/tmp/work", service_id="svc"), 1801)
    killed = _no_reaping(reg)

    assert reg.sweep_idle(1800) == 1
    assert [k for k, _ in killed] == [key]


# -- the shared helper inherits the rule -------------------------------------

def test_the_context_phase_probe_restarts_the_idle_clock():
    """The probe goes through the helper, which goes through the registry."""
    from core.cli_live_sessions import find_live_cli_session

    reg = _codex()
    key = ("user", "conv", "assistant", "svc", 0)
    entry = _stale(reg.register(key, "container", "/tmp/work",
                                service_id="svc"), 1799)
    entry.is_process_alive = _Alive()

    assert find_live_cli_session(
        reg, "user", "conv", "assistant", "svc", 0) is entry
    assert entry.last_used > time.monotonic() - 5

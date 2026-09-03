'''A finished sub-agent gives its CLI container back.

Every CLI provider keeps its container warm between turns of the same
(conversation, agent). That is right for a conversation and wrong for a
one-shot run: flash agents, delegates, task and plan steps have no next turn,
yet their container held a slot in a capped 1:1 pool until the idle sweeper
reaped it one full service timeout later -- 30 minutes by default. Reuse is
keyed on identity, never on availability, so nothing could borrow the slot in
the meantime: a fan-out of differently named flash agents simply consumed the
pool until acquire raised "pool exhausted".
'''

from pathlib import Path

from core.cli_live_sessions import (
    release_cli_live_sessions,
    release_cli_live_sessions_for_context,
)


class _Registry:
    def __init__(self, count):
        self.count = count
        self.calls = []

    def kill_and_evict_by_conv_agent(self, conv_id, agent_name, reason):
        self.calls.append((conv_id, agent_name, reason))
        return self.count

    def kill_and_evict_by_conv(self, conv_id, reason):
        self.calls.append((conv_id, reason))
        return self.count


class _Broken:
    def kill_and_evict_by_conv_agent(self, *_args, **_kwargs):
        raise RuntimeError("registry down")


def test_releases_every_provider_and_counts_them(monkeypatch):
    registries = [_Registry(1), _Registry(2), _Registry(0)]
    monkeypatch.setattr(
        "core.cli_live_sessions._live_registries",
        lambda: [("a", registries[0]), ("b", registries[1]),
                 ("c", registries[2])])

    assert release_cli_live_sessions("conv1", "agent::flash::critic",
                                     reason="subagent_run_finished") == 3
    for registry in registries:
        assert registry.calls == [
            ("conv1", "agent::flash::critic", "subagent_run_finished")]


def test_one_broken_registry_does_not_keep_the_others_holding_containers(
        monkeypatch):
    healthy = _Registry(1)
    monkeypatch.setattr(
        "core.cli_live_sessions._live_registries",
        lambda: [("broken", _Broken()), ("healthy", healthy)])

    assert release_cli_live_sessions("conv1", "agent", reason="x") == 1
    assert healthy.calls


def test_an_unidentified_run_releases_nothing(monkeypatch):
    called = []
    monkeypatch.setattr(
        "core.cli_live_sessions._live_registries",
        lambda: called.append(1) or [])

    assert release_cli_live_sessions("", "agent", reason="x") == 0
    assert release_cli_live_sessions("conv1", "", reason="x") == 0
    assert not called, "no conversation/agent pair means nothing to release"


def test_context_eviction_targets_one_agent_or_the_whole_conversation(monkeypatch):
    targeted = _Registry(2)
    shared = _Registry(3)
    registries = iter([[("targeted", targeted)], [("shared", shared)]])
    monkeypatch.setattr(
        "core.cli_live_sessions._live_registries", lambda: next(registries))

    assert release_cli_live_sessions_for_context(
        "conv1", "agent", reason="compact_started") == 2
    assert targeted.calls == [("conv1", "agent", "compact_started")]

    assert release_cli_live_sessions_for_context(
        "conv1", "", reason="compact_started") == 3
    assert shared.calls == [("conv1", "compact_started")]


def test_all_cli_registries_are_covered():
    src = Path("core/cli_live_sessions.py").read_text(encoding="utf-8")
    for registry in ("LiveSessionRegistry", "InteractiveClaudeCodePool",
                     "CodexInteractivePool", "CodexLiveRegistry",
                     "GeminiLiveRegistry", "AntigravityObserverPool"):
        assert registry in src, registry


def test_subagent_run_releases_its_container_unless_it_persists():
    src = Path("core/_agent_executor_loop.py").read_text(encoding="utf-8")
    assert "release_cli_live_sessions(" in src
    # A multi-turn delegate is the one caller that asked to be spoken to
    # again: its warm session is the point.
    assert "if not task.persist and _delegate_conv_id and task.agent_name:" in src
    # And it must sit in the cleanup path, not on the success path only.
    finally_at = src.index("        finally:")
    assert src.index("release_cli_live_sessions(") > finally_at

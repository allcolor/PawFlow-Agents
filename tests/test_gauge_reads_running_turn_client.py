"""The gauge must read the client the RUNNING turn measures on.

GameDev7, 2026-10-24 21:05-22:07: a loop restarted on an edited service reused
the Claude Code process its previous loop had launched. Every one of the 246
``[compact-check]`` lines logged ``SKIP: gauge not natively measured`` while the
CLI reported ~840K of a 600K window, so auto-compaction never fired and the
session kept growing.

The cause was not the recorder: the turn's clone recorded the wire usage
correctly. It was the READER. ``_observed_context_measurement`` resolves the
client from the active context (``ctx['client']``, the resolver's object) and
falls back to ``ServiceRegistry.resolve(...).get_client()`` -- both are OTHER
objects than the clone the turn actually runs on. A clone shares the
observation maps by reference only with its own descendants, so the measurement
sat on a client nobody read.

These tests pin the reader: when a turn is running, the gauge asks the client
that turn registered, then the active context, then the service.
"""

import threading
from types import SimpleNamespace

from core.llm_client import LLMClient
from tasks.ai.context_usage import _observed_context_measurement


def _client(provider="claude-code-interactive"):
    return LLMClient(provider)


def _fake_exec(active_ctx=None, running_client=None, key="c:assistant"):
    return SimpleNamespace(
        _active_contexts={key: active_ctx} if active_ctx else {},
        _active_contexts_lock=threading.Lock(),
        _active_claude_client={key: running_client} if running_client else {},
    )


def _patch_exec(monkeypatch, fake):
    import tasks.ai.agent_loop as agent_loop
    monkeypatch.setattr(agent_loop.AgentLoopTask, "_live_instance", fake,
                        raising=False)


def test_measurement_is_read_from_the_running_turn_client(monkeypatch):
    """The clone that runs the turn holds the number; the gauge must see it.

    This is the GameDev7 shape: the active context still points at the
    resolver's client (no measurement), while the turn's own clone recorded
    the provider's prompt size.
    """
    resolver_client = _client()
    turn_client = _client()
    turn_client.record_observed_wire_usage(
        {"input_tokens": 4_000,
         "cache_read_input_tokens": 800_000,
         "cache_creation_input_tokens": 36_900},
        "c", "assistant")

    active_ctx = {
        "active_agent_name": "assistant",
        "active_llm_provider": "claude-code-interactive",
        "_is_cli_provider": True,
        "client": resolver_client,
        "resolved_svc": SimpleNamespace(config={}),
    }
    _patch_exec(monkeypatch, _fake_exec(active_ctx, turn_client))

    tokens, mode, revision = _observed_context_measurement(
        "c", "assistant", "user", active_ctx)

    assert tokens == 840_900
    assert mode == "session"
    assert revision >= 1


def test_active_context_client_is_used_when_no_turn_is_running(monkeypatch):
    """Between turns the clone is gone; the active context still answers."""
    active_client = _client()
    active_client.record_observed_wire_usage(
        {"input_tokens": 1_000, "cache_read_input_tokens": 120_000},
        "c", "assistant")
    active_ctx = {
        "active_agent_name": "assistant",
        "active_llm_provider": "claude-code-interactive",
        "_is_cli_provider": True,
        "client": active_client,
        "resolved_svc": SimpleNamespace(config={}),
    }
    _patch_exec(monkeypatch, _fake_exec(active_ctx, running_client=None))

    tokens, _mode, _revision = _observed_context_measurement(
        "c", "assistant", "user", active_ctx)

    assert tokens == 121_000


def test_a_running_turn_without_a_measurement_does_not_hide_the_active_one(
        monkeypatch):
    """An empty running client must not shadow a measured active context.

    The running client is asked first, but a client that holds nothing for
    this stream falls through instead of reporting 0 -- a recorded 0 would
    look like 'measured an empty window' and could trip a compaction.
    """
    active_client = _client()
    active_client.record_observed_wire_usage(
        {"input_tokens": 900, "cache_read_input_tokens": 60_000},
        "c", "assistant")
    active_ctx = {
        "active_agent_name": "assistant",
        "active_llm_provider": "claude-code-interactive",
        "_is_cli_provider": True,
        "client": active_client,
        "resolved_svc": SimpleNamespace(config={}),
    }
    _patch_exec(monkeypatch, _fake_exec(active_ctx, running_client=_client()))

    tokens, _mode, _revision = _observed_context_measurement(
        "c", "assistant", "user", active_ctx)

    assert tokens == 60_900

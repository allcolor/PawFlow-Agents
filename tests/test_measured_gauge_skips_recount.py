"""A measured CLI gauge does not load or re-count the stored context.

Once a CLI provider has reported its own prompt size, compute_context_usage
replaces PawFlow's count with that measurement. Loading the agent's stored
context and tokenizing all of it first was therefore wasted work -- and it ran
on every appended message: 0.1-0.7 s of CPU per call on a 2,800-message
context, 1-49 s per append with eight CLI agents writing at once
(2026-09-29, beta.300).
"""
import threading
from types import SimpleNamespace
from unittest.mock import patch

from tasks.ai.context_usage import compute_context_usage


class _CountingStore:
    """Stored context of a long session; records every load."""

    def __init__(self):
        self.loads = 0

    def load_agent_context(self, *_args, **_kwargs):
        self.loads += 1
        return [{"role": "user", "msg_id": f"m{i}",
                 "content": "stored context " * 50} for i in range(200)]

    def load_transcript_for_agent(self, *_args, **_kwargs):
        return []

    def get_extra_snapshot(self, *_args, **_kwargs):
        return {}


def _client(measured, mode="session"):
    key = ("conv", "agent")
    return SimpleNamespace(
        provider="claude-code-interactive",
        _cli_observed_context_tokens_by_stream=(
            {key: measured} if measured else {}),
        _observed_context_mode_by_stream={key: mode},
        _observed_context_revision_by_stream={key: 3})


def _compute(client, store):
    active_ctx = {
        "active_agent_name": "agent",
        "messages": [],
        "_is_cli_provider": True,
        "_cli_has_session": True,
        "client": client,
    }
    fake_exec = SimpleNamespace(
        _active_contexts={"conv:agent": active_ctx},
        _active_contexts_lock=threading.Lock())
    with patch("tasks.ai.agent_loop.AgentLoopTask._live_instance", fake_exec), \
            patch("tasks.ai.context_usage._service_config",
                  return_value=({"max_context_size": 800_000}, 0,
                                "claude-code-interactive")), \
            patch("core.token_counter.count_messages_tokens",
                  wraps=lambda msgs, multiplier=1.0: 10 * len(msgs)) as count:
        usage = compute_context_usage(
            "conv", "agent", user_id="user", store=store, source="append")
    return usage, count, active_ctx


def test_measured_session_neither_loads_nor_counts_the_context():
    store = _CountingStore()
    usage, count, active_ctx = _compute(_client(300_000), store)

    assert store.loads == 0
    assert all(not call.args[0] for call in count.call_args_list)
    assert usage["used"] == 300_000
    assert usage["max"] == 800_000
    assert usage["pct"] == 300_000 / 800_000
    assert usage["context_source_measured"] is True
    assert usage["context_measurement_revision"] == 3
    assert usage["cache_mode"] == "measured"
    assert usage["cli_context_state"] == "active"
    assert active_ctx["_context_usage_cache"] is usage


def test_unmeasured_session_still_counts_the_stored_context():
    """Falsification: with no measurement the count IS the gauge."""
    store = _CountingStore()
    usage, count, _ = _compute(_client(0), store)

    assert store.loads == 1
    assert any(len(call.args[0]) == 200 for call in count.call_args_list)
    assert usage["used"] == 2_000
    assert not usage.get("context_source_measured")


def test_request_measurement_keeps_the_count():
    """Stateless request measurements are advanced from the messages."""
    store = _CountingStore()
    usage, _, _ = _compute(_client(50_000, mode="request"), store)

    assert store.loads == 1
    assert usage["used"] == 50_000
    assert usage["cache_mode"] != "measured"

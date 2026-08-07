"""The Codex interactive context gauge uses Codex's native token counter.

Codex owns its context window. PawFlow cannot enumerate what is in it -- the
provider's own system prompt and tool schemas are invisible, and a code-mode
harness reads PawFlow's bootstrap file from inside its script, so that read
never reaches PawFlow either. Reconstructing the gauge from the messages
PawFlow holds reported 0 for the whole life of such a session, and a gauge
stuck at 0 never trips auto-compaction.

The native rollout does not have that problem: every ``token_count`` event
carries both the last prompt occupancy and the model context window.
"""
from types import SimpleNamespace
import json
import os
from unittest.mock import patch

from core.llm_providers._codex_interactive_turn import observed_context_tokens
from core.llm_providers.codex_interactive import codex_rollout_context_usage
from tasks.ai.context_usage import compute_context_usage


class _Store:
    def resolve_owner(self, _cid):
        return "user"

    def load_agent_context(self, *_args, **_kwargs):
        return []

    def load_transcript_for_agent(self, *_args, **_kwargs):
        return []

    def get_extra_snapshot(self, *_args, **_kwargs):
        return {}


def _client(measured):
    return SimpleNamespace(
        provider="codex-interactive",
        _cli_observed_context_tokens_by_stream=dict(measured))


def _active_ctx(client, **overrides):
    ctx = {
        "active_agent_name": "assistant",
        "messages": [],
        "_is_cli_provider": True,
        # The exact state a code-mode session is stuck in: no resumed session,
        # and the bootstrap read was never observed because it happened inside
        # the harness's script.
        "_cli_has_session": False,
        "_cli_bootstrap_read_seen": False,
        "client": client,
    }
    ctx.update(overrides)
    return ctx


def _compute(active_ctx, client, conv="conv-codex", store=None):
    fake_exec = SimpleNamespace(
        _active_contexts=({f"{conv}:assistant": active_ctx}
                          if active_ctx else {}),
        _active_contexts_lock=__import__("threading").Lock())
    # No shortcut on the lookup under test: with an active context the client
    # is read from it, and without one it must be found through the service
    # registry exactly as a conversation switch would.
    registry = SimpleNamespace(
        resolve=lambda *_a, **_k: SimpleNamespace(
            get_client=lambda: client, config={}),
        resolve_definition=lambda *_a, **_k: None)
    with patch("tasks.ai.agent_loop.AgentLoopTask._live_instance", fake_exec), \
            patch("tasks.ai.context_usage._service_config",
                  return_value=({"max_context_size": 400000}, 0,
                                "codex-interactive")), \
            patch("core.conv_agent_config.get_agent_config",
                  return_value={"llm_service": "codex"}), \
            patch("core.service_registry.ServiceRegistry.get_instance",
                  return_value=registry):
        return compute_context_usage(
            conv, "assistant", user_id="user", store=store or _Store(),
            source="test")


def test_wire_measurement_is_the_gauge_when_no_bootstrap_read_was_seen():
    """The reported bug: 0% for a whole codex-interactive session."""
    client = _client({("conv-codex", "assistant"): 128_000})
    usage = _compute(_active_ctx(client), client)

    assert usage["used"] == 128_000
    assert usage["max"] == 400_000
    assert usage["pct"] == 128_000 / 400_000
    # Not "cold"/"bootstrap": the window is demonstrably full of something,
    # and the UI drops an unexplained zero only for a cold CLI.
    assert usage["cli_context_state"] == "active"


def test_gauge_survives_a_conversation_switch():
    """Switching conversation showed 0: there is no active context then.

    The measurement lives on the resolved service client, which outlives any
    one turn, so it must still be found with no active context at all.
    """
    client = _client({("conv-codex", "assistant"): 96_000})
    usage = _compute(None, client)

    assert usage["used"] == 96_000


def test_server_restart_discards_the_dead_codex_gauge_before_next_turn():
    """A fresh server has neither a live turn nor a wire measurement.

    The persisted value describes the TUI process killed by the restart and
    must not be rebuilt from PawFlow's externalized stored context.
    """
    class _RestartStore(_Store):
        def load_agent_context(self, *_args, **_kwargs):
            return [{"role": "user", "content": "externalized context"}]

        def get_extra_snapshot(self, *_args, **_kwargs):
            return {"assistant": {
                "used": 400_000,
                "max": 400_000,
                "pct": 1.0,
                "source": "message_meta",
                "updated_at": 1.0,
                "message_count": 1,
                "cli_context_state": "active",
            }}

    client = _client({})
    with patch("core.token_counter.count_messages_tokens", return_value=0) as count:
        usage = _compute(None, client, store=_RestartStore())

    count.assert_called_once_with([], multiplier=1.0)
    assert usage["used"] == 0
    assert usage["pct"] == 0.0
    assert usage["message_count"] == 0
    assert usage["cli_context_state"] == "cold"


def test_no_measurement_yet_leaves_the_cold_gauge_alone():
    """A session that has not exchanged anything still reads 0."""
    client = _client({})
    usage = _compute(_active_ctx(client), client)

    assert usage["used"] == 0
    assert usage["cli_context_state"] == "cold"


def test_observed_context_tokens_reads_the_prompt_side_only():
    assert observed_context_tokens(
        {"input_tokens": 4200, "output_tokens": 900}) == 4200


def test_cached_prompt_is_not_counted_twice():
    """Responses already includes the cached prefix in input_tokens."""
    assert observed_context_tokens({
        "input_tokens": 50_000,
        "input_tokens_details": {"cached_tokens": 48_000},
    }) == 50_000


def test_a_payload_without_an_input_side_keeps_the_previous_measurement():
    """0 means 'no measurement', never 'the window is empty'."""
    assert observed_context_tokens({"output_tokens": 120}) == 0
    assert observed_context_tokens(None) == 0


def _write_rollout(tmp_path, rows):
    rollout = (tmp_path / ".codex" / "sessions" / "2026" / "08" / "07" /
               "rollout-test.jsonl")
    rollout.parent.mkdir(parents=True)
    rollout.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return rollout


def _token_count(last_input, window, total_input):
    return {
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {
                "total_token_usage": {"input_tokens": total_input},
                "last_token_usage": {"input_tokens": last_input},
                "model_context_window": window,
            },
        },
    }


def test_rollout_uses_last_prompt_not_cumulative_usage(tmp_path):
    _write_rollout(tmp_path, [
        _token_count(210_000, 258_400, 4_100_000),
        {"type": "event_msg", "payload": {"type": "agent_message"}},
        _token_count(223_627, 258_400, 4_900_000),
    ])

    assert codex_rollout_context_usage(str(tmp_path)) == (223_627, 258_400)


def test_rollout_measurement_beats_a_misleading_proxy_value(tmp_path):
    _write_rollout(tmp_path, [_token_count(223_627, 258_400, 4_900_000)])

    from core.llm_providers.codex_interactive import LLMCodexInteractiveMixin

    class _Client(LLMCodexInteractiveMixin):
        def __init__(self):
            self._cli_observed_context_tokens_by_stream = {}
            self._cli_observed_context_window_by_stream = {}

    client = _Client()
    state = SimpleNamespace(workdir=str(tmp_path), created_at=0)
    with patch.object(client, "_publish_codex_context_gauge") as publish:
        client.record_codex_live_context(
            state, "conv", "assistant", 880, user_id="user")

    assert client._cli_observed_context_tokens_by_stream[
        ("conv", "assistant")] == 223_627
    assert client._cli_observed_context_window_by_stream[
        ("conv", "assistant")] == 258_400
    publish.assert_called_once()


def test_native_window_is_not_overwritten_by_the_tui_fallback(tmp_path):
    _write_rollout(tmp_path, [_token_count(223_627, 258_400, 4_900_000)])

    from core.llm_providers.codex_interactive import LLMCodexInteractiveMixin

    class _Client(LLMCodexInteractiveMixin):
        def __init__(self):
            self._cli_observed_context_tokens_by_stream = {}
            self._cli_observed_context_window_by_stream = {}

    client = _Client()
    state = SimpleNamespace(workdir=str(tmp_path), created_at=0, name="session")
    with patch.object(client, "_publish_codex_context_gauge"):
        client.record_codex_live_context(state, "conv", "assistant", 880)
    pool = SimpleNamespace(
        _pane_text=lambda _name: (_ for _ in ()).throw(
            AssertionError("native window must skip the pane probe")))

    client.record_codex_context_window(
        pool, state, "conv", "assistant", 223_627)

    assert client._cli_observed_context_window_by_stream[
        ("conv", "assistant")] == 258_400


def test_rollout_from_an_old_session_is_not_reused(tmp_path):
    rollout = _write_rollout(
        tmp_path, [_token_count(223_627, 258_400, 4_900_000)])
    os.utime(rollout, (10, 10))

    assert codex_rollout_context_usage(
        str(tmp_path), not_before=100) == (0, 0)


def _coordinator(sink):
    from core.llm_providers._codex_interactive_turn import (
        _CodexInteractiveTurnCoordinator)

    return _CodexInteractiveTurnCoordinator(
        SimpleNamespace(wait_event=lambda *_a, **_k: {}),
        "tok", context_tokens_callback=sink.append)


def test_the_gauge_takes_the_last_exchange_not_the_sum_of_the_turn():
    """Usage sums the turn; the gauge is one prompt's gross size.

    A Codex turn runs several /responses exchanges. Summing their prompts
    would report several times the window on a long turn.
    """
    measured = []
    coord = _coordinator(measured)

    coord._merge_usage({"input_tokens": 10_000, "output_tokens": 100})
    coord._merge_usage({"input_tokens": 12_500, "output_tokens": 200})

    assert measured == [10_000, 12_500]
    assert coord.observed_context_tokens == 12_500
    # Cost accounting keeps summing, untouched.
    assert coord.usage["input_tokens"] == 22_500
    assert coord.usage["output_tokens"] == 300


def test_cached_inputs_are_split_without_shrinking_the_context_gauge():
    """Reproduce the reported 1,455,240 IN footer without shrinking gauge."""
    measured = []
    coord = _coordinator(measured)

    exchanges = [
        (205_879, 204_544, 276),
        (207_071, 205_568, 310),
        (207_513, 206_592, 184),
        (208_183, 206_592, 248),
        (208_478, 207_616, 188),
        (208_874, 207_616, 264),
        (209_242, 208_640, 71),
    ]
    for prompt, cached, output in exchanges:
        coord._merge_usage({
            "input_tokens": prompt,
            "input_tokens_details": {"cached_tokens": cached},
            "output_tokens": output,
            "total_tokens": prompt + output,
        })

    assert measured == [row[0] for row in exchanges]
    assert coord.observed_context_tokens == 209_242
    assert sum(row[0] for row in exchanges) == 1_455_240
    assert coord.usage["input_tokens"] == 8_072
    assert coord.usage["cached_input_tokens"] == 1_447_168
    assert coord.usage["output_tokens"] == 1_541
    assert coord.usage["total_tokens"] == 1_456_781


def test_a_shrinking_window_is_followed_down():
    """Codex compacting its own session must move the gauge down."""
    measured = []
    coord = _coordinator(measured)

    coord._merge_usage({"input_tokens": 300_000, "output_tokens": 10})
    coord._merge_usage({"input_tokens": 20_000, "output_tokens": 10})

    assert coord.observed_context_tokens == 20_000

"""The Codex interactive context gauge is measured on the wire.

Codex owns its context window. PawFlow cannot enumerate what is in it -- the
provider's own system prompt and tool schemas are invisible, and a code-mode
harness reads PawFlow's bootstrap file from inside its script, so that read
never reaches PawFlow either. Reconstructing the gauge from the messages
PawFlow holds reported 0 for the whole life of such a session, and a gauge
stuck at 0 never trips auto-compaction.

The observing proxy does not have that problem: every Responses exchange
carries the prompt-token count Codex itself computed.
"""
from types import SimpleNamespace
from unittest.mock import patch

from core.llm_providers._codex_interactive_turn import observed_context_tokens
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


def _compute(active_ctx, client, conv="conv-codex"):
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
            conv, "assistant", user_id="user", store=_Store(),
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


def _coordinator(sink):
    from core.llm_providers._codex_interactive_turn import (
        _CodexInteractiveTurnCoordinator)

    return _CodexInteractiveTurnCoordinator(
        SimpleNamespace(wait_event=lambda *_a, **_k: {}),
        "tok", context_tokens_callback=sink.append)


def test_the_gauge_takes_the_last_exchange_not_the_sum_of_the_turn():
    """Cost accounting sums the turn; the gauge is one prompt's size.

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


def test_a_shrinking_window_is_followed_down():
    """Codex compacting its own session must move the gauge down."""
    measured = []
    coord = _coordinator(measured)

    coord._merge_usage({"input_tokens": 300_000, "output_tokens": 10})
    coord._merge_usage({"input_tokens": 20_000, "output_tokens": 10})

    assert coord.observed_context_tokens == 20_000

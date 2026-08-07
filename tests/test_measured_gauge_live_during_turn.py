"""The measured gauge must land DURING the turn, not only after it.

beta.139 recorded the observed prompt size once ``coord.run()`` returned. Every
live gauge update happens strictly before that: the emitter recomputes on each
appended message and on heartbeats, all inside the turn. So a session whose
reconstruction reads 0 -- claude-code-interactive once its externalized context
outgrows the native read's size ceiling -- displayed 0% for the whole turn and
only snapped to the real number after the last token.

These tests pin the mid-stream recording: the coordinators must hand each
revised prompt size to the recorder as the stream produces it, and the
emitter's heartbeat gate must notice a measurement that moved on its own.
"""

import inspect

from core.llm_client import LLMClient
from core.llm_providers._cci_turn import _CCITurnCoordinator
from core.llm_providers.antigravity_interactive import (
    _AntigravityTurnCoordinator)


def _coord(**kwargs):
    """A coordinator with no event service -- we drive the parser directly."""
    return _CCITurnCoordinator(None, "tok", **kwargs)


def test_cci_coordinator_publishes_each_message_start_usage():
    seen = []
    coord = _coord(usage_callback=seen.append)
    coord.usage.update({"input_tokens": 1_000,
                        "cache_read_input_tokens": 120_000})
    coord._publish_usage_observation()
    coord.usage.update({"input_tokens": 1_400,
                        "cache_read_input_tokens": 180_000})
    coord._publish_usage_observation()
    assert [u["cache_read_input_tokens"] for u in seen] == [120_000, 180_000]


def test_published_usage_is_a_snapshot_not_the_live_dict():
    """The recorder must not see later mutations of the coordinator's dict."""
    seen = []
    coord = _coord(usage_callback=seen.append)
    coord.usage.update({"input_tokens": 10})
    coord._publish_usage_observation()
    coord.usage["input_tokens"] = 999
    assert seen[0]["input_tokens"] == 10


def test_usage_callback_failure_never_breaks_the_turn():
    """A gauge update is cosmetic; a raising callback must not kill a stream."""
    def _boom(_usage):
        raise RuntimeError("gauge exploded")

    _coord(usage_callback=_boom)._publish_usage_observation()


def test_no_callback_is_a_no_op():
    _coord()._publish_usage_observation()


def test_message_start_branch_calls_the_publisher():
    """Pin the wiring: the usage update site must publish.

    The parser loop needs a live event service to run, so assert on the source
    that the publish sits with the message_start usage update rather than
    somewhere that only runs at the end of the turn.
    """
    src = inspect.getsource(_CCITurnCoordinator.run)
    marker = 'if ptype == "message_start"'
    start = src.index(marker)
    end = src.index('elif ptype == "content_block_start"', start)
    assert "_publish_usage_observation()" in src[start:end]


def test_antigravity_coordinator_publishes_observed_usage():
    seen = []
    coord = _AntigravityTurnCoordinator("/nonexistent.log",
                                        usage_callback=seen.append)
    coord.usage.update({"input_tokens": 64_000})
    coord._publish_usage_observation()
    assert seen == [{"input_tokens": 64_000}]


def test_cci_provider_observer_records_onto_the_shared_map():
    """The observer the provider hands the coordinator feeds the gauge map."""
    client = LLMClient("claude-code-interactive")
    observe = client._cci_usage_observer("conv-live", "claude")
    observe({"input_tokens": 2_000, "cache_read_input_tokens": 300_000,
             "output_tokens": 40})
    assert client._cli_observed_context_tokens_by_stream[
        ("conv-live", "claude")] == 302_000


def test_antigravity_provider_observer_records_onto_the_shared_map():
    client = LLMClient("antigravity-interactive")
    observe = client._agi_usage_observer("conv-agi", "agi")
    observe({"input_tokens": 64_000})
    assert client._cli_observed_context_tokens_by_stream[
        ("conv-agi", "agi")] == 64_000


class _Emitter:
    """Just enough of AgentEmitter to exercise the heartbeat signature."""

    from tasks.ai.agent_emitter import StreamEmitter
    _observed_context_measurement = StreamEmitter._observed_context_measurement
    _context_usage_input_signature = StreamEmitter._context_usage_input_signature

    def __init__(self, client):
        self.ctx = {"client": client, "messages": [], "max_context_size": 800_000}
        self.conversation_id = "conv-hb"
        self._agent_name = "claude"


def test_heartbeat_signature_moves_with_the_measurement_alone():
    """A turn can revise the prompt size without appending any message.

    Keying the heartbeat on the PawFlow message list alone made every such
    update invisible until something happened to be appended.
    """
    client = LLMClient("claude-code-interactive")
    em = _Emitter(client)
    before = em._context_usage_input_signature()
    client.record_observed_wire_usage(
        {"input_tokens": 380_000}, "conv-hb", "claude")
    assert em._context_usage_input_signature() != before


def test_signature_is_stable_when_nothing_moved():
    client = LLMClient("claude-code-interactive")
    em = _Emitter(client)
    assert (em._context_usage_input_signature()
            == em._context_usage_input_signature())

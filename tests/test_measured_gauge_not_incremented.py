"""A measured context gauge may only be advanced by a new measurement.

For an observed CLI provider (codex-interactive) the gauge is not PawFlow's
count of its own messages: it is the prompt size the provider reported on its
last observed exchange, read off the MITM wire. That number already contains
everything in the provider's window.

The streaming hot path advanced the cached gauge by adding PawFlow's own token
count for each appended message. On a measured cache that double-counts, and
the error compounds append after append: the gauge climbed all turn -- observed
in the wild going from 62% to 92% with no compaction of any kind -- until the
next full recompute overwrote it with the measurement again and it resumed
growing correctly from the true value.
"""

from tasks.ai.context_usage_cache import (
    context_usage_append_delta, context_usage_entry, context_usage_from_cache)


class _Msg:
    """Minimal stand-in for an LLMMessage the counter can size."""

    def __init__(self, content: str, msg_id: str = "", role: str = "tool"):
        self.content = content
        self.role = role
        self.msg_id = msg_id
        self.thinking = ""
        self.tool_calls = []


def _measured_cache(used: int, max_ctx: int, mode: str = "session") -> dict:
    """What compute_context_usage stores once the measurement wins."""
    cache = context_usage_entry(
        [_Msg("x", "m1")], used, max_ctx, source="pawflow_context")
    cache["used"] = used
    cache["pct"] = used / max_ctx
    cache["context_source_measured"] = True
    cache["context_measurement_mode"] = mode
    cache["context_measurement_revision"] = 1
    cache["context_measurement_tokens"] = used
    return cache


def test_append_delta_refuses_to_advance_a_measured_cache():
    cache = _measured_cache(used=170_000, max_ctx=272_000)
    assert context_usage_append_delta(
        cache, _Msg("a large tool result " * 500, "m2"),
        source="append") is None


def test_append_delta_still_advances_a_counted_cache():
    """The guard must be scoped to measured caches: every other provider
    depends on this hot path staying incremental."""
    cache = context_usage_entry(
        [_Msg("x", "m1")], 1_000, 100_000, source="pawflow_context")
    out = context_usage_append_delta(cache, _Msg("y" * 400, "m2"),
                                     source="append")
    assert out is not None
    assert out["used"] > 1_000
    assert out["cache_mode"] == "append_delta"


def test_append_delta_advances_a_stateless_request_measurement():
    cache = _measured_cache(used=50_000, max_ctx=100_000, mode="request")
    out = context_usage_append_delta(
        cache, _Msg("the response that joins the next prompt", "m2"),
        source="append")
    assert out is not None
    assert out["used"] > 50_000
    assert out["context_measurement_revision"] == 1


def test_the_drift_compounds_without_the_guard():
    """Falsification: the same appends on a cache NOT marked as measured.

    This is what the gauge was doing -- it is here so the regression is
    visible as a number, not only as a None/not-None distinction.
    """
    counted = context_usage_entry(
        [_Msg("x", "m1")], 170_000, 272_000, source="pawflow_context")
    measured = _measured_cache(used=170_000, max_ctx=272_000)

    drifting = counted
    for i in range(20):
        nxt = context_usage_append_delta(
            drifting, _Msg("tool result " * 400, f"m{i + 2}"), source="append")
        assert nxt is not None, "the counted path must stay incremental"
        drifting = nxt

    # Unguarded, every append is added to the base. The rate is what matters:
    # a turn with many large tool results walks the gauge up without limit,
    # which is how a measured 62% was showing 92% by the end of one.
    added = drifting["used"] - counted["used"]
    assert added > 0
    assert drifting["used"] == counted["used"] + added
    assert drifting["pct"] > counted["pct"]

    # The measured cache does not move at all, whatever is appended: only the
    # provider's next reported prompt size may move it.
    for i in range(20):
        assert context_usage_append_delta(
            measured, _Msg("tool result " * 400, f"m{i + 2}"),
            source="append") is None
    assert measured["pct"] == 170_000 / 272_000


def test_from_cache_does_not_add_a_suffix_onto_a_measured_base():
    """The other incremental path has the same shape: cached_used + delta.

    It is masked while a measurement exists (the caller overwrites `used`
    afterwards), and stops being masked the moment the CLI session is
    invalidated and no measurement is left to overwrite it.
    """
    msgs = [_Msg("x", "m1"), _Msg("y" * 4_000, "m2")]
    measured = _measured_cache(used=170_000, max_ctx=272_000)
    out = context_usage_from_cache(
        msgs, 272_000, measured, source="pawflow_context")
    assert out["cache_mode"] == "full", (
        "a measured cache is not a valid base for a suffix delta")
    assert out["used"] < 170_000, "the measured value was carried into a count"


def test_from_cache_adds_suffix_onto_a_stateless_request_measurement():
    msgs = [_Msg("x", "m1"), _Msg("y" * 4_000, "m2")]
    measured = _measured_cache(used=50_000, max_ctx=100_000, mode="request")
    out = context_usage_from_cache(
        msgs, 100_000, measured, source="pawflow_context")
    assert out["cache_mode"] == "delta"
    assert out["used"] > 50_000
    assert out["context_measurement_revision"] == 1


def test_from_cache_still_deltas_a_counted_base():
    msgs = [_Msg("x", "m1")]
    counted = context_usage_entry(msgs, 1_000, 100_000, source="pawflow_context")
    grown = msgs + [_Msg("y" * 400, "m2")]
    out = context_usage_from_cache(
        grown, 100_000, counted, source="pawflow_context")
    assert out["cache_mode"] == "delta"
    assert out["used"] > 1_000

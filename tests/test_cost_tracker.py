"""Tests for CostTracker — pricing must come from the LLM service config,
not a hardcoded table.
"""

import pytest

from core.cost_tracker import CostTracker


@pytest.fixture(autouse=True)
def _fresh_tracker():
    # Replace the singleton with a fresh instance per test so state does
    # not leak between tests.
    CostTracker._instance = CostTracker()
    yield
    CostTracker._instance = CostTracker()


def test_zero_price_yields_zero_cost():
    t = CostTracker.instance()
    delta = t.track(
        "conv1", "claude-opus-4-7",
        tokens_in=100_000, tokens_out=10_000,
        cost_per_1m_input=0.0, cost_per_1m_output=0.0)
    assert delta == 0.0
    assert t.get_conversation_cost("conv1")["total"] == 0.0


def test_missing_price_yields_zero_cost():
    """No hardcoded fallback: missing pricing = $0."""
    t = CostTracker.instance()
    delta = t.track(
        "conv1", "some-unknown-model",
        tokens_in=1_000_000, tokens_out=500_000)
    assert delta == 0.0


def test_explicit_price_is_used():
    t = CostTracker.instance()
    delta = t.track(
        "conv1", "m",
        tokens_in=1_000_000, tokens_out=2_000_000,
        cost_per_1m_input=3.0, cost_per_1m_output=15.0)
    # 1M * $3 + 2M * $15 = $33
    assert delta == pytest.approx(33.0)


def test_cache_rates_default_to_anthropic_ratios():
    t = CostTracker.instance()
    # Only input/output given; cache_read should default to input*0.1
    # and cache_write to input*1.25.
    delta = t.track(
        "conv1", "m",
        tokens_in=0, tokens_out=0,
        cache_read=1_000_000, cache_write=1_000_000,
        cost_per_1m_input=10.0, cost_per_1m_output=0.0)
    # 1M * (10*0.1) + 1M * (10*1.25) = 1 + 12.5 = 13.5
    assert delta == pytest.approx(13.5)


def test_explicit_cache_rates_override_defaults():
    t = CostTracker.instance()
    delta = t.track(
        "conv1", "m",
        cache_read=1_000_000, cache_write=1_000_000,
        cost_per_1m_input=10.0, cost_per_1m_output=0.0,
        cost_per_1m_cache_read=2.0, cost_per_1m_cache_write=20.0)
    # 1M * 2 + 1M * 20 = 22
    assert delta == pytest.approx(22.0)


def test_track_accumulates_into_conversation_total():
    t = CostTracker.instance()
    t.track("conv1", "m", tokens_in=1_000_000,
            cost_per_1m_input=3.0, cost_per_1m_output=0.0)
    t.track("conv1", "m", tokens_in=2_000_000,
            cost_per_1m_input=3.0, cost_per_1m_output=0.0)
    total = t.get_conversation_cost("conv1")["total"]
    assert total == pytest.approx(9.0)


def test_track_returns_turn_delta_not_cumulative():
    t = CostTracker.instance()
    t.track("conv1", "m", tokens_in=1_000_000,
            cost_per_1m_input=3.0, cost_per_1m_output=0.0)
    # Second turn: delta must be independent of what's already accumulated.
    delta = t.track("conv1", "m", tokens_in=2_000_000,
                    cost_per_1m_input=3.0, cost_per_1m_output=0.0)
    assert delta == pytest.approx(6.0)  # just this turn, not 3+6=9


def test_by_model_tally_is_recorded_even_when_free():
    """When pricing is zero we still want token counts for diagnostics."""
    t = CostTracker.instance()
    t.track("conv1", "claude-opus-4-7",
            tokens_in=142337, tokens_out=6114,
            cost_per_1m_input=0.0, cost_per_1m_output=0.0)
    by_model = t.get_conversation_cost("conv1")["by_model"]
    assert by_model["claude-opus-4-7"]["in"] == 142337
    assert by_model["claude-opus-4-7"]["out"] == 6114
    assert by_model["claude-opus-4-7"]["cost"] == 0.0

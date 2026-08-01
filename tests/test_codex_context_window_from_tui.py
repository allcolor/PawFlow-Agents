"""Codex's real context window, read off the one place it is visible.

The Responses API reports the size of every prompt but never the window it is
measured against. So PawFlow knew `used` exactly and had to divide it by
whatever `max_context_size` happened to be configured -- for the gauge AND for
the auto-compact threshold, which is armed as `max_ctx * compact_threshold_pct`.
A budget set well above or below the model's real window made that trigger fire
late or early, on a number with no relation to the session.

The Codex TUI prints the missing half in its status bar ("context left 74%").
Together with the measured prompt size the window follows exactly.
"""

from core.codex_interactive_pool import (
    context_left_fraction, derive_context_window)
from tasks.ai.context_usage import _client_real_window

STATUS_PANE = """\
>_ OpenAI Codex (v0.146.0)

\u203a You
  do the thing

> Ask Codex

  gpt-5.6-sol medium  ~  context left 74%
"""


def test_the_status_bar_percentage_is_read():
    assert context_left_fraction(STATUS_PANE) == 0.74


def test_a_pane_without_the_status_bar_reports_nothing():
    """None means "cannot tell", which must never be read as "0% left"."""
    assert context_left_fraction("> Ask Codex\n") is None
    assert context_left_fraction("") is None
    assert context_left_fraction(None) is None


def test_the_last_reading_wins():
    """The status bar is redrawn at the bottom; an older one may still be
    scrolled up in the transcript."""
    stale = "  context left 91%\n" + STATUS_PANE
    assert context_left_fraction(stale) == 0.74


def test_a_nonsense_percentage_is_refused():
    assert context_left_fraction("context left 250%") is None


def test_the_window_follows_from_used_and_left():
    # 70 720 tokens sitting in 26% of the window -> 272 000.
    assert derive_context_window(70_720, 0.74) == 272_000


def test_a_low_occupancy_reading_is_not_trusted():
    """The TUI rounds to a whole percent. At 5% used, half a point of rounding
    moves the derived window by 10%, so the previous value is kept."""
    assert derive_context_window(13_600, 0.95, previous=272_000) == 272_000
    assert derive_context_window(13_600, 0.95) == 0


def test_rounding_noise_does_not_move_a_stored_window():
    """A denominator that breathes turn to turn is the defect this whole area
    exists to remove -- a 1% wobble must not republish a new gauge scale."""
    assert derive_context_window(70_000, 0.74, previous=272_000) == 272_000


def test_a_genuine_change_does_move_it():
    """Switching to a 1m-context variant is not noise."""
    assert derive_context_window(260_000, 0.74, previous=272_000) == 1_000_000


def test_no_measurement_leaves_the_window_alone():
    assert derive_context_window(0, 0.74, previous=272_000) == 272_000
    assert derive_context_window(70_720, None, previous=272_000) == 272_000


class _State:
    name = "pawflow-codex-int-test"


def _provider_client():
    from core.llm_providers.codex_interactive import LLMCodexInteractiveMixin

    class _C(LLMCodexInteractiveMixin):
        def __init__(self):
            self._cli_observed_context_window_by_stream = {}

    return _C()


class _Pool:
    def __init__(self, pane):
        self._pane = pane

    def _pane_text(self, _name):
        return self._pane


def test_the_provider_records_the_window_it_derived():
    client = _provider_client()
    client.record_codex_context_window(
        _Pool(STATUS_PANE), _State(), "c1", "codex", 70_720)
    assert client._cli_observed_context_window_by_stream[("c1", "codex")] == 272_000


def test_a_pane_capture_failure_never_breaks_the_turn():
    class _Broken:
        def _pane_text(self, _name):
            raise RuntimeError("docker exec failed")

    client = _provider_client()
    client.record_codex_context_window(_Broken(), _State(), "c1", "codex", 70_720)
    assert client._cli_observed_context_window_by_stream == {}


def test_the_gauge_reads_the_derived_window():
    """The whole point: this window becomes the gauge denominator, and with it
    the base of `max_ctx * compact_threshold_pct`."""
    client = _provider_client()
    client.record_codex_context_window(
        _Pool(STATUS_PANE), _State(), "c1", "codex", 70_720)
    assert _client_real_window(client, "c1", "codex") == 272_000
    assert _client_real_window(client, "c1", "other") == 0


def test_the_codex_window_wins_over_a_stale_claude_code_one():
    """A client that has served both providers must not hand Codex's gauge the
    Claude Code window."""
    client = _provider_client()
    client._cc_context_window_by_stream = {("c1", "codex"): 200_000}
    client.record_codex_context_window(
        _Pool(STATUS_PANE), _State(), "c1", "codex", 70_720)
    assert _client_real_window(client, "c1", "codex") == 272_000


def test_the_window_survives_a_call_clone():
    """The gauge is read from the resolver client, the turn runs on a clone.
    They must expose one authoritative value -- same contract as the counts."""
    from core.llm_client import LLMClient

    parent = LLMClient(provider="codex-interactive", config={"api_key": "k"})
    clone = parent.clone_for_call()
    clone._cli_observed_context_window_by_stream[("c1", "codex")] = 272_000
    assert _client_real_window(parent, "c1", "codex") == 272_000

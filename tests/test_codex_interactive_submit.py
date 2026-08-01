"""A pasted prompt that never left the input box must not report success.

The Codex TUI does not render pasted text. It replaces it with an attachment
chip -- ``[Pasted Content 24470 chars]`` -- so the prompt waiting unsent in the
composer is, to a text probe, indistinguishable from a prompt that was
accepted. ``_verify_submitted`` probed for a tail fragment of the injected text
and read its absence as "the input box let it go": on Codex that condition is
true from the first poll onwards, always. Every send reported success, none of
them submitted, and six pastes stacked up in one composer until a human pressed
Enter.

These tests pin the chip probe that replaces the fragment heuristic on TUIs
that collapse pastes, and pin that the Claude Code path -- whose TUI does echo
pasted text -- is left on the fragment heuristic unchanged.
"""

import core.claude_code_interactive_pool as ccip
from core.claude_code_interactive_pool import InteractiveClaudeCodePool
from core.codex_interactive_pool import CodexInteractivePool

PROMPT = "Some long injected prompt\nwith a distinctive trailing line here"

# What `tmux capture-pane` returns while the paste sits unsent: the composer
# line carries the chip, the text itself appears nowhere.
UNSENT_PANE = """\
>_ OpenAI Codex (v0.146.0)

  model:  gpt-5.6-sol medium

> [Pasted Content 24470 chars][Pasted Content 1013 chars]

  gpt-5.6-sol medium  ~
"""

# After submission: the chip moves into the transcript (the TUI keeps showing
# the user turn that way) and the composer is empty again.
SUBMITTED_PANE = """\
>_ OpenAI Codex (v0.146.0)

› You
  [Pasted Content 24470 chars]

> Ask Codex

  gpt-5.6-sol medium  ~  context left 74%
"""

RUNNING_PANE = SUBMITTED_PANE + "\n  Working (Esc to interrupt)\n"


class _State:
    name = "pawflow-codex-int-test"


def _harness(pool, panes, monkeypatch):
    """Drive _verify_submitted over a scripted pane sequence.

    Returns the list of key batches it sent. `panes` is consumed one poll at a
    time; the last entry repeats once exhausted.
    """
    sent = []
    seq = list(panes)

    monkeypatch.setattr(pool, "_pane_text",
                        lambda _name: seq.pop(0) if len(seq) > 1 else seq[0])
    monkeypatch.setattr(pool, "send_keys",
                        lambda _state, keys: sent.append(list(keys)) or True)
    monkeypatch.setattr(ccip.time, "sleep", lambda _s: None)
    monkeypatch.setenv("PAWFLOW_CCI_SUBMIT_VERIFY_SECONDS", "1.2")
    pool._verify_submitted(_State(), PROMPT)
    return sent


def test_unsent_paste_chip_is_detected_in_the_composer():
    pool = CodexInteractivePool()
    assert pool._pane_holds_unsent_paste(UNSENT_PANE) is True


def test_chip_left_in_the_transcript_is_not_an_unsent_prompt():
    """The submitted turn keeps its chip on screen. Scanning the whole pane
    would see it forever and press Enter forever, so the probe is scoped to
    the composer -- the last prompt line onward."""
    pool = CodexInteractivePool()
    assert pool._pane_holds_unsent_paste(SUBMITTED_PANE) is False


def test_missing_composer_line_is_unknown_not_empty():
    pool = CodexInteractivePool()
    assert pool._pane_holds_unsent_paste("a pane with no prompt line") is None


# The composer has scrolled off, but the header has NOT: it is permanent, and
# it starts with the composer prefix `>`. Everything below it is transcript,
# including a chip from a message submitted long ago.
NO_COMPOSER_PANE = """\
>_ OpenAI Codex (v0.146.0)

\u203a You
  [Pasted Content 24470 chars]

  Working (Esc to interrupt)
"""


def test_the_permanent_header_is_not_mistaken_for_the_composer():
    """`>_ OpenAI Codex` starts with `>`, so a bare startswith() matched it.

    The scan runs bottom-up and stopped on the header, making _composer_text()
    return the whole transcript. The historical chip below it then read as an
    unsent paste.
    """
    pool = CodexInteractivePool()
    assert pool._composer_text(NO_COMPOSER_PANE) == ""
    assert pool._pane_holds_unsent_paste(NO_COMPOSER_PANE) is None


def test_no_enter_is_sent_when_only_the_header_and_a_stale_chip_are_visible(
        monkeypatch):
    """The reviewer's repro: this sent three Enter into a running turn.

    The chip probe is authoritative over the running marker on purpose -- a
    chip in the composer means the paste never left it, whatever else the pane
    shows. That authority is only safe while the composer region is real.
    """
    pool = CodexInteractivePool()
    assert _harness(pool, [NO_COMPOSER_PANE], monkeypatch) == []


def test_the_composer_is_still_found_when_it_is_on_screen():
    """The header fix must not cost us the composer itself: both the empty box
    (`> Ask Codex`) and the loaded one (`> [Pasted ...]`) are prefix+space."""
    pool = CodexInteractivePool()
    assert pool._composer_text(UNSENT_PANE).startswith("> [Pasted Content")
    assert pool._composer_text(SUBMITTED_PANE).startswith("> Ask Codex")


def test_verify_presses_enter_again_while_the_chip_is_in_the_composer(monkeypatch):
    """The bug: this returned immediately having sent nothing."""
    pool = CodexInteractivePool()
    sent = _harness(pool, [UNSENT_PANE], monkeypatch)
    assert sent == [["Enter"]] * 3


def test_verify_stops_as_soon_as_the_composer_clears(monkeypatch):
    pool = CodexInteractivePool()
    sent = _harness(pool, [UNSENT_PANE, SUBMITTED_PANE], monkeypatch)
    assert sent == [["Enter"]]


def test_verify_never_presses_enter_while_a_turn_runs(monkeypatch):
    pool = CodexInteractivePool()
    assert _harness(pool, [RUNNING_PANE], monkeypatch) == []


def test_claude_code_keeps_the_fragment_heuristic():
    """Its TUI echoes pasted text, so absence of the fragment really does mean
    submitted. No chip markers -> the probe abstains."""
    pool = InteractiveClaudeCodePool()
    assert pool._PASTE_CHIP_MARKERS == ()
    assert pool._pane_holds_unsent_paste(UNSENT_PANE) is None


def test_claude_code_verify_returns_when_its_text_left_the_box(monkeypatch):
    pool = InteractiveClaudeCodePool()
    pane = "> \n  ? for shortcuts\n"
    assert _harness(pool, [pane], monkeypatch) == []


def test_paste_settle_is_longer_for_codex_than_for_claude_code(monkeypatch):
    monkeypatch.delenv("PAWFLOW_CCI_PASTE_SETTLE_SECONDS", raising=False)
    assert CodexInteractivePool()._paste_settle_seconds() == 1.0
    assert InteractiveClaudeCodePool()._paste_settle_seconds() == 0.2


def test_paste_settle_env_override_still_wins(monkeypatch):
    monkeypatch.setenv("PAWFLOW_CCI_PASTE_SETTLE_SECONDS", "2.5")
    assert CodexInteractivePool()._paste_settle_seconds() == 2.5
    assert InteractiveClaudeCodePool()._paste_settle_seconds() == 2.5


# ── the proof that the paste landed ────────────────────────────────────────
#
# `_paste_landed` is what stands between a paste that never arrived and a turn
# that waits out its 300s no-event timeout. Two ways it said yes without
# evidence.

# The header is drawn the instant the TUI starts; the input box is not. Past
# the readiness timeout, or during a redraw, this is the whole pane.
HEADER_ONLY_PANE = ">_ OpenAI Codex (v0.104.0)\n\n  model:  gpt-5.6-sol\n"


def _landed(pool, pane, monkeypatch):
    monkeypatch.setattr(pool, "_pane_text", lambda _name: pane)
    monkeypatch.setattr(ccip.time, "sleep", lambda _s: None)
    monkeypatch.setattr(type(pool), "_PASTE_LANDED_SECONDS", 0.0)
    return pool._paste_landed(_State(), PROMPT)


def test_a_pane_with_no_input_box_is_not_proof_of_a_paste(monkeypatch):
    """The reviewer's repro. `>_ OpenAI Codex` locates no composer, and
    "cannot tell" was answered True -- so a paste into a TUI that has no input
    box yet was declared landed, Enter went nowhere, and the turn sat on a
    session nobody had prompted until the no-event timeout fired."""
    assert _landed(CodexInteractivePool(), HEADER_ONLY_PANE, monkeypatch) is False


def test_the_chip_in_the_composer_is_still_proof(monkeypatch):
    assert _landed(CodexInteractivePool(), UNSENT_PANE, monkeypatch) is True


def test_an_empty_composer_is_still_a_refusal(monkeypatch):
    assert _landed(CodexInteractivePool(), SUBMITTED_PANE, monkeypatch) is False


def test_a_running_turn_is_still_proof(monkeypatch):
    """Accepted between the paste and this look."""
    assert _landed(CodexInteractivePool(), RUNNING_PANE, monkeypatch) is True


def test_an_unreadable_pane_still_must_not_block_a_send(monkeypatch):
    assert _landed(CodexInteractivePool(), "", monkeypatch) is True


def test_a_tui_that_declares_no_composer_is_judged_exactly_as_before(
        monkeypatch):
    """Claude Code declares no composer prefix, so its whole pane IS the
    composer and is always located: the new refusal cannot reach it, and its
    fragment heuristic answers as it always did."""
    pool = InteractiveClaudeCodePool()
    pane = "> " + pool._submit_probe_fragment(PROMPT) + "\n  ? for shortcuts\n"
    assert _landed(pool, pane, monkeypatch) is True
    assert _landed(pool, "> \n  ? for shortcuts\n", monkeypatch) is False

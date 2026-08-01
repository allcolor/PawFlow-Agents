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


# ── the paste that landed and was pasted three times anyway ────────────────
#
# Both probes above recognise a TUI. The chip probe needs the composer line to
# start with `>`; the fragment probe needs the pasted text to be RENDERED as
# text. A Codex build that draws its composer inside a box and collapses the
# whole paste into chips satisfies neither, and the send then failed with
# "prompt never reached the composer" on a prompt that was in the composer and
# needed nothing but Enter -- pasted three times over on the way there.

IDLE_PANE = """\
>_ OpenAI Codex (v0.146.0)

\u256d\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u256e
\u2502 > Ask Codex \u2502
\u2570\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u256f
  gpt-5.6-sol medium  ~
"""

BOXED_UNSENT_PANE = """\
>_ OpenAI Codex (v0.146.0)

\u256d\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u256e
\u2502 > [Pasted Content 1024 chars][Pasted Content 1021 chars] \u2502
\u2570\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u256f
  gpt-5.6-sol medium  ~
"""


def test_both_probes_are_blind_to_a_boxed_composer_full_of_chips():
    """Neither of them is wrong. Both are modelling a TUI that moved."""
    pool = CodexInteractivePool()
    # The composer line starts with the box border, not with `>`.
    assert pool._pane_holds_unsent_paste(BOXED_UNSENT_PANE) is None
    # And the pasted text is a chip, so no fragment of it is on screen.
    fragment = pool._submit_probe_fragment(PROMPT)
    assert pool._fragment_on_pane(BOXED_UNSENT_PANE, fragment) is False


def _landed_after(pool, before, after, monkeypatch):
    monkeypatch.setattr(pool, "_pane_text", lambda _name: after)
    monkeypatch.setattr(ccip.time, "sleep", lambda _s: None)
    monkeypatch.setattr(type(pool), "_PASTE_LANDED_SECONDS", 0.0)
    return pool._paste_landed(_State(), PROMPT, before)


def test_a_pane_that_changed_is_proof_the_paste_arrived(monkeypatch):
    """The screen moved between the two captures. Only the paste moved it."""
    pool = CodexInteractivePool()
    assert _landed_after(
        pool, IDLE_PANE, BOXED_UNSENT_PANE, monkeypatch) is True


def test_without_the_comparison_the_same_pane_is_still_a_refusal(monkeypatch):
    """Pins that the comparison is what fixes it, not a loosened probe: the
    recognition tests alone still fail on this pane, exactly as they did."""
    assert _landed(CodexInteractivePool(), BOXED_UNSENT_PANE,
                   monkeypatch) is False


def test_an_unchanged_pane_is_still_a_refusal(monkeypatch):
    """The case the retry exists for: nothing arrived, so paste again."""
    pool = CodexInteractivePool()
    assert _landed_after(pool, IDLE_PANE, IDLE_PANE, monkeypatch) is False


# ── a failure carries the screen it happened on ────────────────────────────


def test_a_refused_paste_logs_the_pane_it_refused(monkeypatch):
    """Every check here reads the pane and none of them reported it. When a
    TUI release moved its composer, the log said our reading failed and never
    what was drawn -- which is how the pane ended up being inferred from a
    photograph of a terminal."""
    pool = CodexInteractivePool()
    monkeypatch.setattr(pool, "_pane_text", lambda _name: BOXED_UNSENT_PANE)
    out = pool._pane_diagnostic(_State.name)
    assert "[Pasted Content 1024 chars]" in out
    assert out.startswith("; pane")


def test_the_pane_diagnostic_is_bounded(monkeypatch):
    pool = CodexInteractivePool()
    monkeypatch.setattr(pool, "_pane_text", lambda _name: "x" * 5000)
    out = pool._pane_diagnostic(_State.name)
    assert "(+3000 chars)" in out
    assert out.count("x") == pool._PANE_DIAGNOSTIC_CHARS


def test_an_unreadable_pane_never_raises_inside_a_warning(monkeypatch):
    pool = CodexInteractivePool()

    def _boom(_name):
        raise RuntimeError("docker exec failed")

    monkeypatch.setattr(pool, "_pane_text", _boom)
    assert pool._pane_diagnostic(_State.name) == " [pane unreadable]"
    monkeypatch.setattr(pool, "_pane_text", lambda _name: "")
    assert pool._pane_diagnostic(_State.name) == " [pane empty or unreadable]"


def test_the_prompt_is_pasted_once_when_the_screen_reacts(monkeypatch):
    """The user's report, end to end: one paste, then Enter.

    Before the comparison this pasted the prompt three times and then failed
    the send, leaving a composer holding it four chips deep.
    """
    pool = CodexInteractivePool()
    state = _State()
    state.prompt_ready = True
    state.last_error = ""
    panes = [IDLE_PANE]
    pastes = []
    keys = []

    monkeypatch.setattr(pool, "_is_alive", lambda _name: True)
    monkeypatch.setattr(pool, "_cancel_copy_mode", lambda _state: None)
    monkeypatch.setattr(pool, "_remember_injected_prompt",
                        lambda _state, _text: None)
    monkeypatch.setattr(pool, "_remember_injected_prompt_for_event_service",
                        lambda _state, _text: None)
    monkeypatch.setattr(pool, "_load_buffer", lambda _state, _text: True)
    monkeypatch.setattr(pool, "_verify_submitted",
                        lambda _state, _text: None)
    monkeypatch.setattr(pool, "_pane_text", lambda _name: panes[-1])

    def _paste(_state):
        pastes.append(1)
        panes.append(BOXED_UNSENT_PANE)
        return True

    monkeypatch.setattr(pool, "_paste_buffer", _paste)
    monkeypatch.setattr(pool, "send_keys",
                        lambda _state, batch: keys.append(list(batch)) or True)
    monkeypatch.setattr(ccip.time, "sleep", lambda _s: None)

    assert pool.send_text(state, PROMPT) is True
    assert len(pastes) == 1
    assert keys == [["Enter"], ["Enter"]]

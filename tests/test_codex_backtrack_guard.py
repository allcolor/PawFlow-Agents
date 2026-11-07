"""Codex backtrack overlay and `›` composer guards (codex-cli 0.156.1).

Incident 2026-09-23: "Codex prompt submission was not confirmed after the
canonical Enter sequence (the submission pane is inconclusive)". Reproduced
on a disposable Codex: two Esc keys reaching the TUI as two reads open
"Browsing transcript ... ↵ rewind · esc back"; the paste is dropped there and
Enter rewinds the conversation ("Conversation reverted to this point"). And
the composer is drawn `›`, which the old `>` prefix never located. The panes
below are transcribed from that reproduction.

Incident 2026-11-07 08:15Z (codex-cli 0.167.0, GameDev3): Codex ran a turn on
the PREVIOUS prompt, re-submitted byte for byte, and the new prompt was never
delivered. Reproduced on a disposable 0.167: Esc, Esc on the idle composer,
paste dropped, Enter (rewind) then Enter (re-submit). An idle composer now
gets no Esc, and an Enter goes only onto the send's own paste.
"""

import core.claude_code_interactive_pool as ccip
from core.claude_code_interactive_pool import InteractiveClaudeCodePool
from core.codex_interactive_pool import CodexInteractivePool

PROMPT = "Some long injected prompt\nwith a distinctive trailing line here"

IDLE = """\
› petit message test
• Worked for 7m 17s · 6:08 AM

› Ask Codex to do anything

  GPT-6-Astra xhigh · /tmp/w
"""

CHIP = IDLE.replace("› Ask Codex to do anything",
                    "› [Pasted Content 2400 chars]")

# The first Enter rewound inside the overlay: the composer now holds the
# previous prompt as text, and a second Enter would submit it again.
RECALLED = IDLE.replace("› Ask Codex to do anything",
                        "› GD7 -> GD3 (copie Root GD2) : -03 SCELLEE pour "
                        "relecture d'UNE ligne.")

PRIMED = IDLE.replace("  GPT-6-Astra xhigh · /tmp/w",
                      "  esc again to edit previous message")

RUNNING = """\
› petit message test
• Reconnecting... 3/5 (1s • esc to interrupt)
  └ Unexpected status 401 Unauthorized
› Ask Codex to do anything
  GPT-6-Astra xhigh · /tmp/w · ⠸
"""

BACKTRACK = """\
› [Pasted Content 2400 chars]
  ## [2] Ma situation : rien n'est en attente chez moi
Browsing transcript · ↑↓/jk scroll · ←→/hl prompts · ctrl+t details · ↵ rewind · esc back
"""

TRANSCRIPT_PAGER = """\
› [Pasted Content 2400 chars]
Ctrl+Space select
 ↑/↓ to scroll · pgup/pgdn to page · home/end to jump
 q close · f3 find · esc browse prompts
"""


class _State:
    name = "pawflow-codex-int-test"
    session_token = "sess"
    last_error = ""
    prompt_ready = True


def _no_sleep(monkeypatch):
    monkeypatch.setattr(ccip.time, "sleep", lambda _s: None)


# ── locating the `›` composer ───────────────────────────────────────────

def test_the_0156_composer_is_located_and_read():
    pool = CodexInteractivePool()
    assert pool._composer_text(IDLE).startswith("› Ask Codex")
    assert pool._pane_holds_unsent_paste(IDLE) is False
    assert pool._pane_holds_unsent_paste(CHIP) is True


def test_a_transcript_user_turn_is_not_the_composer():
    """Transcript turns share the `›`; running chrome below one proves it
    is not the composer, so a stale chip there never authorizes Enter."""
    pool = CodexInteractivePool()
    pane = "› [Pasted Content 2400 chars]\n• Working (12s • esc to interrupt)\n"
    assert pool._composer_text(pane) == ""
    assert pool._pane_holds_unsent_paste(pane) is None
    assert pool._composer_text(RUNNING).startswith("› Ask Codex")


def test_overlays_hide_the_composer():
    pool = CodexInteractivePool()
    for pane in (BACKTRACK, TRANSCRIPT_PAGER):
        assert pool._composer_text(pane) == ""
        assert pool._pane_holds_unsent_paste(pane) is None


def test_claude_code_composer_is_untouched():
    pool = InteractiveClaudeCodePool()
    assert pool._composer_text(BACKTRACK) == BACKTRACK


# ── preparation: no Esc on an idle composer, one Esc otherwise ──────────

def _prepare(monkeypatch, panes):
    pool = CodexInteractivePool()
    state = _State()
    state.last_error = ""
    keys = []
    seq = list(panes)
    monkeypatch.setattr(pool, "_check_native_compaction", lambda _s: None)
    monkeypatch.setattr(pool, "_pane_text",
                        lambda _n: seq.pop(0) if len(seq) > 1 else seq[0])
    monkeypatch.setattr(InteractiveClaudeCodePool, "send_keys",
                        lambda _self, _state, batch: keys.append(list(batch)) or True)
    _no_sleep(monkeypatch)
    return pool._prepare_prompt_input(state), keys, state


def test_prepare_sends_no_escape_to_an_idle_composer(monkeypatch):
    ok, keys, _state = _prepare(monkeypatch, [IDLE])
    assert ok is True
    assert keys == []


def test_prepare_never_completes_a_primed_backtrack(monkeypatch):
    """One Esc already primed backtrack: a second one opens the overlay."""
    ok, keys, _state = _prepare(monkeypatch, [PRIMED])
    assert ok is True
    assert keys == []


def test_prepare_stops_a_running_turn_with_a_single_escape(monkeypatch):
    ok, keys, _state = _prepare(monkeypatch, [RUNNING, IDLE])
    assert ok is True
    assert keys == [["Escape"]]


def test_prepare_keeps_its_escape_when_the_pane_is_unreadable(monkeypatch):
    ok, keys, _state = _prepare(monkeypatch, [""])
    assert ok is True
    assert keys == [["Escape"]]


def test_prepare_closes_an_open_backtrack_overlay(monkeypatch):
    ok, keys, _state = _prepare(monkeypatch, [BACKTRACK, IDLE])
    assert ok is True
    assert keys == [["Escape"]]


def test_prepare_refuses_to_paste_into_a_stuck_overlay(monkeypatch):
    ok, keys, state = _prepare(monkeypatch, [BACKTRACK])
    assert ok is False
    assert keys == [["Escape"]] * 4
    assert "overlay" in state.last_error


# ── Enter is a rewind inside the overlay ────────────────────────────────

def _enter(monkeypatch, pane, expected=""):
    pool = CodexInteractivePool()
    state = _State()
    state.last_error = ""
    state.expected_paste = expected
    sent = []
    panes = list(pane) if isinstance(pane, list) else [pane]
    monkeypatch.setattr(pool, "_pane_text",
                        lambda _n: panes.pop(0) if len(panes) > 1 else panes[0])
    monkeypatch.setattr(InteractiveClaudeCodePool, "send_keys",
                        lambda _self, _state, batch: sent.append(list(batch)) or True)
    return pool, state, sent


def test_enter_is_refused_while_the_backtrack_overlay_is_open(monkeypatch):
    pool, state, sent = _enter(monkeypatch, BACKTRACK, PROMPT)
    ok = pool.send_keys(state, ["Enter"])
    assert ok is False
    assert sent == []
    assert "rewind" in state.last_error


def test_enter_goes_through_on_the_composer(monkeypatch):
    pool, state, sent = _enter(monkeypatch, CHIP, PROMPT)
    ok = pool.send_keys(state, ["Enter"])
    assert ok is True
    assert sent == [["Enter"]]


def test_enter_is_skipped_when_the_composer_holds_another_prompt(monkeypatch):
    """The incident: never submit a recalled previous prompt."""
    pool, state, sent = _enter(monkeypatch, RECALLED, PROMPT)
    ok = pool.send_keys(state, ["Enter"])
    assert ok is True
    assert sent == []


def test_second_enter_does_not_resubmit_a_recalled_prompt(monkeypatch):
    """Enter, 200ms, Enter: the second one sees the recalled prompt."""
    pool, state, sent = _enter(monkeypatch, [CHIP, RECALLED], PROMPT)
    assert pool.send_keys(state, ["Enter"]) is True
    assert pool.send_keys(state, ["Enter"]) is True
    assert sent == [["Enter"]]


def test_second_enter_is_skipped_once_the_first_submitted(monkeypatch):
    pool, state, sent = _enter(monkeypatch, [CHIP, IDLE], PROMPT)
    assert pool.send_keys(state, ["Enter"]) is True
    assert pool.send_keys(state, ["Enter"]) is True
    assert sent == [["Enter"]]


def test_enter_goes_through_when_the_composer_is_not_located(monkeypatch):
    """Unknown chrome cannot veto: the submission verifier still decides."""
    pool, state, sent = _enter(monkeypatch, "some unknown chrome", PROMPT)
    assert pool.send_keys(state, ["Enter"]) is True
    assert sent == [["Enter"]]


def test_send_text_records_its_paste_only_while_it_runs(monkeypatch):
    import threading

    pool = CodexInteractivePool()
    state = _State()
    state.send_lock = threading.RLock()
    state.expected_paste = ""
    seen = []
    monkeypatch.setattr(pool, "_check_native_compaction", lambda _s: None)
    monkeypatch.setattr(InteractiveClaudeCodePool, "send_text",
                        lambda _self, st, _text: seen.append(st.expected_paste) or True)
    assert pool.send_text(state, PROMPT) is True
    assert seen == [PROMPT]
    assert state.expected_paste == ""


# ── what proves that the paste landed ───────────────────────────────────

def _landed(monkeypatch, before, after):
    pool = CodexInteractivePool()
    monkeypatch.setattr(pool, "_pane_text", lambda _n: after)
    monkeypatch.setattr(type(pool), "_PASTE_LANDED_SECONDS", 0.0)
    _no_sleep(monkeypatch)
    return pool._paste_landed(_State(), PROMPT, before)


def test_the_overlay_opening_is_not_a_landed_paste(monkeypatch):
    """The incident: the pane changed because the overlay opened, and the old
    comparison took that for the paste and pressed Enter -- a rewind."""
    assert _landed(monkeypatch, PRIMED, BACKTRACK) is False


def test_a_located_empty_composer_outweighs_a_changed_pane(monkeypatch):
    assert _landed(monkeypatch, PRIMED, IDLE) is False


def test_the_chip_in_the_0156_composer_is_proof(monkeypatch):
    assert _landed(monkeypatch, IDLE, CHIP) is True


# ── diagnostics carry structure, never the prompt ───────────────────────

def test_state_summary_is_structural_only():
    pool = CodexInteractivePool()
    typed = IDLE.replace("Ask Codex to do anything", "secret prompt words")
    summary = pool._pane_state_summary(typed)
    assert "secret" not in summary
    assert summary.startswith("composer=nonempty")
    assert pool._pane_state_summary(CHIP).startswith("composer=chip")
    assert "backtrack_overlay=True" in pool._pane_state_summary(BACKTRACK)
    assert "backtrack_primed=True" in pool._pane_state_summary(PRIMED)
    assert pool._pane_state_summary("") == "pane=unreadable"

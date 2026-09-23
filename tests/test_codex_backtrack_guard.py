"""Codex backtrack overlay and `›` composer guards (codex-cli 0.156.1).

Incident 2026-09-23: "Codex prompt submission was not confirmed after the
canonical Enter sequence (the submission pane is inconclusive)". Reproduced
on a disposable Codex: two Esc keys reaching the TUI as two reads open
"Browsing transcript ... ↵ rewind · esc back"; the paste is dropped there and
Enter rewinds the conversation ("Conversation reverted to this point"). And
the composer is drawn `›`, which the old `>` prefix never located. The panes
below are transcribed from that reproduction.
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


# ── preparation: one Esc, never two ─────────────────────────────────────

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


def test_prepare_sends_a_single_escape(monkeypatch):
    ok, keys, _state = _prepare(monkeypatch, [PRIMED])
    assert ok is True
    assert keys == [["Escape"]]


def test_prepare_closes_an_open_backtrack_overlay(monkeypatch):
    ok, keys, _state = _prepare(monkeypatch, [BACKTRACK, IDLE])
    assert ok is True
    assert keys == [["Escape"], ["Escape"]]


def test_prepare_refuses_to_paste_into_a_stuck_overlay(monkeypatch):
    ok, keys, state = _prepare(monkeypatch, [BACKTRACK])
    assert ok is False
    assert keys == [["Escape"]] * 4
    assert "overlay" in state.last_error


# ── Enter is a rewind inside the overlay ────────────────────────────────

def _enter(monkeypatch, pane):
    pool = CodexInteractivePool()
    state = _State()
    state.last_error = ""
    sent = []
    monkeypatch.setattr(pool, "_pane_text", lambda _n: pane)
    monkeypatch.setattr(InteractiveClaudeCodePool, "send_keys",
                        lambda _self, _state, batch: sent.append(list(batch)) or True)
    return pool.send_keys(state, ["Enter"]), sent, state


def test_enter_is_refused_while_the_backtrack_overlay_is_open(monkeypatch):
    ok, sent, state = _enter(monkeypatch, BACKTRACK)
    assert ok is False
    assert sent == []
    assert "rewind" in state.last_error


def test_enter_goes_through_on_the_composer(monkeypatch):
    ok, sent, _state = _enter(monkeypatch, CHIP)
    assert ok is True
    assert sent == [["Enter"]]


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

"""A tmux command that times out fails the send; it never kills the session.

Incident 2026-09-29 08:05Z: `docker rm -f` of two compacting containers took
12-17 s, and the `docker exec ... tmux paste-buffer` of a scheduled wake-up
hit its 10 s timeout. The TimeoutExpired escaped send_text, the turn was
filed as "Claude Code session lost" and the claude_session marker was wiped.
The killed exec still ran once the daemon recovered: the first piece of the
wake-up landed in the input box and the next prompt was pasted under it and
submitted with it.
"""

import subprocess
import threading
from pathlib import Path

import core.claude_code_interactive_pool as ccip
from core.claude_code_interactive_pool import InteractiveClaudeCodePool

PROMPT = ("<catch_up_context>\nNew messages from other participants\n"
          "the distinctive trailing line of this injected prompt")
FOOTER = "  \u23f5\u23f5 bypass permissions on (shift+tab to cycle)\n"
LATE_HEAD = "\u276f <catch_up_context>\nNew messages from other participants\n" + FOOTER
EMPTY = "\u276f \n" + FOOTER
RUNNING = ("\u276f <catch_up_context>\n\u273d Hatching... (3s)\n"
           "  \u23f5\u23f5 bypass permissions on \u00b7 esc to interrupt\n")


class _State:
    name = "pf-test-cci"
    last_error = ""
    unconfirmed_paste = ""
    send_lock = threading.RLock()


def _timeout(*args, **kwargs):
    raise subprocess.TimeoutExpired(args[0], kwargs.get("timeout"))


def _pool(monkeypatch, pane):
    pool = InteractiveClaudeCodePool()
    keys = []
    monkeypatch.setattr(pool, "_pane_text", lambda _n: pane)
    monkeypatch.setattr(pool, "send_keys",
                        lambda _s, batch: keys.append(list(batch)) or True)
    return pool, keys


def test_a_paste_buffer_timeout_fails_the_paste_and_is_remembered(monkeypatch):
    pool = InteractiveClaudeCodePool()
    monkeypatch.setattr(pool, "_load_buffer", lambda *_a: True)
    monkeypatch.setattr(ccip.subprocess, "run", _timeout)
    state = _State()
    assert pool._paste_text(state, PROMPT) is False
    assert state.last_error == "tmux paste-buffer timed out after 10s"
    assert state.unconfirmed_paste == PROMPT


def test_a_load_buffer_timeout_fails_the_paste(monkeypatch):
    pool = InteractiveClaudeCodePool()
    monkeypatch.setattr(ccip.subprocess, "run", _timeout)
    state = _State()
    assert pool._paste_text(state, PROMPT) is False
    assert state.last_error == "tmux load-buffer timed out after 15s"


def test_a_send_keys_timeout_fails_the_keys(monkeypatch):
    pool = InteractiveClaudeCodePool()
    monkeypatch.setattr(pool, "_is_alive", lambda _n: True)
    monkeypatch.setattr(ccip.subprocess, "run", _timeout)
    state = _State()
    assert pool.send_keys(state, ["Enter"]) is False
    assert state.last_error == "tmux send-keys timed out after 10s"


def test_the_next_send_empties_a_failed_paste_that_landed_late(monkeypatch):
    pool, keys = _pool(monkeypatch, LATE_HEAD)
    state = _State()
    state.unconfirmed_paste = PROMPT
    pool._clear_unconfirmed_paste(state)
    assert keys == [["Space", "Space", "Escape", "Escape",
                     "BSpace", "BSpace"]]
    assert state.unconfirmed_paste == ""


def test_an_unconfirmed_paste_is_remembered_for_the_next_send(monkeypatch):
    # 2026-09-29 09:19Z: GameDev1's pane did not move for 3 s, the send was
    # refused, and the paste sat in the composer a minute later.
    pool = InteractiveClaudeCodePool()
    for name, value in {
        "_is_alive": lambda _n: True,
        "_sync_slot_credentials": lambda _s: None,
        "_cancel_copy_mode": lambda _s: None,
        "_prepare_prompt_input": lambda _s: True,
        "_remember_injected_prompt": lambda _s, _t: None,
        "_remember_injected_prompt_for_event_service": lambda _s, _t: None,
        "_paste_settle_seconds": lambda: 0,
        "_pane_text": lambda _n: EMPTY,
        "_journal_mark": lambda _s: None,
        "_paste_text": lambda _s, _t: True,
        "_paste_landed": lambda _s, _t, _b: False,
        "_pane_diagnostic": lambda _n: "",
    }.items():
        monkeypatch.setattr(pool, name, value)
    state = _State()
    state.prompt_ready = True

    assert pool.send_text(state, PROMPT) is False
    assert state.last_error == "prompt was not confirmed after the single paste"
    assert state.unconfirmed_paste == pool._composer_safe_text(PROMPT)


def test_an_empty_input_box_is_left_alone(monkeypatch):
    pool, keys = _pool(monkeypatch, EMPTY)
    state = _State()
    state.unconfirmed_paste = PROMPT
    pool._clear_unconfirmed_paste(state)
    assert keys == []
    assert state.unconfirmed_paste == ""


def test_a_running_turn_is_never_escaped(monkeypatch):
    pool, keys = _pool(monkeypatch, RUNNING)
    state = _State()
    state.unconfirmed_paste = PROMPT
    pool._clear_unconfirmed_paste(state)
    assert keys == []


def test_no_failed_paste_means_no_pane_read(monkeypatch):
    pool = InteractiveClaudeCodePool()
    monkeypatch.setattr(pool, "_pane_text", lambda _n: (_ for _ in ()).throw(
        AssertionError("pane read without a failed paste")))
    pool._clear_unconfirmed_paste(_State())


def test_a_failed_paste_keeps_the_live_session():
    """Nothing reached the CLI: its claude_session marker must survive."""
    src = Path("tasks/ai/_alc_llm_turn.py").read_text(encoding="utf-8")
    body = src.split("st._is_transport_kill = (")[1].split("return _ALC_BREAK")[0]

    assert '"Failed to paste prompt" in st.err_str' in body
    assert "or st._is_delivery_failure" in body
    assert "Claude Code prompt not delivered" in body

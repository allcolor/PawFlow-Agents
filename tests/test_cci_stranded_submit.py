"""Claude Code submit verification is decided by the UserPromptSubmit receipt.

Incident 2026-09-23 (GameDev2): the pasted prompt stayed in the input box after
three Enter retries 0.3 s apart. Verification returned "inconclusive", the send
was reported successful, and the turn waited on a CLI that never received the
prompt until the idle-pane probe failed it a minute later ("went back to its
prompt without answering and without a Stop hook").
"""

import threading

import core.claude_code_interactive_pool as ccip
from core.claude_code_interactive_pool import InteractiveClaudeCodePool
from core.codex_interactive_pool import CodexInteractivePool

PROMPT = ("<catch_up_context>\nNew messages from other participants\n"
          "the distinctive trailing line of this injected prompt")

FOOTER = "  ⏵⏵ bypass permissions on (shift+tab to cycle)\n"
STRANDED = "❯ " + PROMPT + "\n" + FOOTER
EMPTY = "❯ \n" + FOOTER
RUNNING = ("❯ " + PROMPT + "\n✽ Hatching... (3s)\n"
           "  ⏵⏵ bypass permissions on · esc to interrupt\n")


class _State:
    name = "pf-test-cci"
    session_token = "sess"
    last_error = ""
    prompt_ready = True
    send_lock = threading.RLock()


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now


class _Receipts:
    """Fake event service: answers from a script, advancing the clock."""

    def __init__(self, clock, answers=()):
        self.clock = clock
        self.answers = list(answers)
        self.timeouts = []

    def wait_for_prompt_submission(self, _token, _text, *, after_submit,
                                   after_request, timeout):
        self.timeouts.append(timeout)
        self.clock.now += timeout
        return self.answers.pop(0) if self.answers else ""

    def submission_marker(self, _token):
        return (5, 5)


def _pool(monkeypatch, panes, *, delay="1.0", window="6"):
    pool = InteractiveClaudeCodePool()
    clock = _Clock()
    keys = []
    monkeypatch.setattr(ccip.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(ccip.time, "sleep", lambda _s: None)
    monkeypatch.setenv("PAWFLOW_CCI_SUBMIT_DELAY_SECONDS", delay)
    monkeypatch.setenv("PAWFLOW_CCI_SUBMIT_VERIFY_SECONDS", window)
    pane_iter = iter(panes)
    last = [panes[-1]]

    def pane_text(_name):
        try:
            last[0] = next(pane_iter)
        except StopIteration:
            pass
        return last[0]

    monkeypatch.setattr(pool, "_pane_text", pane_text)

    def send_keys(state, batch):
        keys.append((round(clock.now - 1000.0, 3), list(batch)))
        return True

    monkeypatch.setattr(pool, "send_keys", send_keys)
    return pool, clock, keys


def test_the_receipt_proves_the_submit_without_any_retry(monkeypatch):
    pool, clock, keys = _pool(monkeypatch, [STRANDED])
    service = _Receipts(clock, ["hook"])
    assert pool._verify_submitted(_State(), PROMPT, event_service=service,
                                  submit_marker=(0, 0)) is True
    assert keys == []


def test_a_stranded_prompt_fails_the_send_after_spaced_retries(monkeypatch):
    pool, clock, keys = _pool(monkeypatch, [STRANDED])
    state = _State()
    service = _Receipts(clock)
    assert pool._verify_submitted(state, PROMPT, event_service=service,
                                  submit_marker=(0, 0)) is False
    enters = [at for at, batch in keys if batch == ["Enter"]]
    assert len(enters) == 3
    # One submit delay apart: never inside the paste-detection window.
    assert all(b - a >= 1.0 for a, b in zip(enters, enters[1:]))
    assert "input box" in state.last_error


def test_retries_stop_once_the_turn_runs_and_the_receipt_arrives(monkeypatch):
    pool, clock, keys = _pool(monkeypatch, [STRANDED, RUNNING])
    service = _Receipts(clock, ["", "", "hook"])
    assert pool._verify_submitted(_State(), PROMPT, event_service=service,
                                  submit_marker=(0, 0)) is True
    assert keys == [(1.0, ["Enter"])]


def test_no_receipt_but_nothing_stranded_stays_inconclusive(monkeypatch):
    pool, clock, keys = _pool(monkeypatch, [EMPTY])
    service = _Receipts(clock)
    assert pool._verify_submitted(_State(), PROMPT, event_service=service,
                                  submit_marker=(0, 0)) is None
    assert keys == []


def test_a_fragment_receipt_fails_the_send(monkeypatch):
    pool, clock, keys = _pool(monkeypatch, [EMPTY])
    state = _State()
    service = _Receipts(clock, ["fragment"])
    assert pool._verify_submitted(state, PROMPT, event_service=service,
                                  submit_marker=(0, 0)) is False
    assert "fragment" in state.last_error


def test_a_different_prompt_moves_the_marker_and_keeps_waiting(monkeypatch):
    pool, clock, keys = _pool(monkeypatch, [EMPTY])
    service = _Receipts(clock, ["other", "hook"])
    assert pool._verify_submitted(_State(), PROMPT, event_service=service,
                                  submit_marker=(0, 0)) is True


def _send(monkeypatch, pool, verified):
    monkeypatch.setattr(pool, "_is_alive", lambda _n: True)
    monkeypatch.setattr(pool, "_cancel_copy_mode", lambda _s: None)
    monkeypatch.setattr(pool, "_prepare_prompt_input", lambda _s: True)
    monkeypatch.setattr(pool, "_remember_injected_prompt", lambda *_a: None)
    monkeypatch.setattr(pool, "_remember_injected_prompt_for_event_service",
                        lambda *_a: None)
    monkeypatch.setattr(pool, "_load_buffer", lambda *_a: True)
    monkeypatch.setattr(pool, "_paste_buffer", lambda *_a: True)
    monkeypatch.setattr(pool, "_paste_landed", lambda *_a: True)
    monkeypatch.setattr(pool, "_verify_submitted",
                        lambda *_a, **_k: verified)
    state = _State()
    return state, pool.send_text(state, PROMPT)


def test_a_failed_send_empties_the_input_box_it_stranded(monkeypatch):
    pool, _clock, keys = _pool(monkeypatch, [STRANDED])
    state, ok = _send(monkeypatch, pool, False)
    assert ok is False
    assert keys[-1][1] == ["Space", "Space", "Escape", "Escape",
                           "BSpace", "BSpace"]
    assert state.last_error == "prompt submission was not confirmed"


def test_a_failed_send_never_touches_an_empty_input_box(monkeypatch):
    pool, _clock, keys = _pool(monkeypatch, [EMPTY])
    _state, ok = _send(monkeypatch, pool, False)
    assert ok is False
    assert [b for _t, b in keys if b != ["Enter"]] == []


def test_codex_never_clears_with_the_claude_code_keys():
    assert CodexInteractivePool._CLEAR_STRANDED_ON_FAILED_SEND is False
    assert InteractiveClaudeCodePool._CLEAR_STRANDED_ON_FAILED_SEND is True


def test_the_pane_only_fallback_spaces_its_retries(monkeypatch):
    pool, _clock, _keys = _pool(monkeypatch, [STRANDED])
    stamps = iter(range(0, 100000))
    fake_now = [0.0]

    def fake_time():
        fake_now[0] = next(stamps) * 0.3
        return fake_now[0]

    enters = []
    monkeypatch.setattr(ccip.time, "time", fake_time)
    monkeypatch.setattr(pool, "send_keys",
                        lambda _s, batch: enters.append(fake_now[0]) or True)
    pool._verify_submitted(_State(), PROMPT)
    assert 1 <= len(enters) <= 3
    assert all(b - a >= 1.0 for a, b in zip(enters, enters[1:]))

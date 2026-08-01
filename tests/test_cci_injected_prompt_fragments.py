"""One paste, several submits: the pieces are still ours, not the user's.

``remember_injected_prompt`` records one digest and one ignore ticket per
injection, and ``_consume_injected_prompt`` spends exactly one of each per
``UserPromptSubmit`` hook. That holds while a TUI turns one paste into one
submit. The Codex TUI does not: a composer holding several attachments fires a
hook per piece, and a piece's digest matches nothing.

What followed was in the server log -- ``CC interactive manual tmux prompt
persisted ... chars=47`` then ``chars=26`` -- fragments of a background tool
result PawFlow had just injected, filed as messages the *user* typed, published
under their name, and answered one by one.

The fragment must be recognised as ours. A short one must not be: "ok" occurs
inside any large paste and is also exactly what a human types.
"""

import queue
import threading
import time

import pytest

from services.cc_interactive_event_service import (
    CCInteractiveEventService, CCInteractiveSessionEvents)


def _session():
    return CCInteractiveSessionEvents(
        session_token="tok", events=queue.Queue())

TOOL_RESULT = (
    'Here is the result of your tool call bg_9f2 (read):\n'
    '  206\t    "messages": history,\n'
    '  207\t    "message_count": page["total_count"],\n'
    '  208\t    "has_more": page["has_more"],\n'
    '  209\t    "offset": page["offset"],\n'
)


@pytest.fixture
def service():
    svc = object.__new__(CCInteractiveEventService)
    return svc


def _state_with_injection(text=TOOL_RESULT):
    state = _session()
    # Real clock: the guard drops anything older than its 600s window.
    now = time.time()
    state.injected_prompts = {"digest-of-the-whole-thing": now}
    state.injected_prompt_texts = [(now, text)]
    state.pending_injected_prompt_ignores = [now]
    return state


def test_a_fragment_of_the_injection_is_recognised_as_ours():
    state = _state_with_injection()
    assert CCInteractiveEventService._is_fragment_of_injection(
        state, '"message_count": page["total_count"],') is True


def test_the_whole_injection_is_not_a_fragment():
    """It has a digest of its own; the fragment path must not shadow the
    digest path or the ticket accounting drifts."""
    state = _state_with_injection()
    assert CCInteractiveEventService._is_fragment_of_injection(
        state, TOOL_RESULT) is False


def test_rewrapped_whitespace_still_matches():
    state = _state_with_injection()
    assert CCInteractiveEventService._is_fragment_of_injection(
        state, '206\n"messages":    history,') is True


def test_a_short_prompt_is_never_stolen_from_the_user():
    state = _state_with_injection("please read the file and say ok when done")
    assert CCInteractiveEventService._is_fragment_of_injection(
        state, "ok") is False


def test_an_unrelated_prompt_stays_manual():
    state = _state_with_injection()
    assert CCInteractiveEventService._is_fragment_of_injection(
        state, "commit push release et tag") is False


def test_nothing_injected_means_nothing_to_match():
    state = _session()
    assert CCInteractiveEventService._is_fragment_of_injection(
        state, '"messages": history,') is False


def test_second_submit_of_one_paste_is_consumed_not_persisted(service):
    """The bug, end to end at the guard: the first piece spends the ticket,
    the second used to fall through to "manual prompt"."""
    service._sessions_lock = threading.RLock()
    state = _state_with_injection()

    first = service._consume_injected_prompt(
        state, '  206\t    "messages": history,')
    second = service._consume_injected_prompt(
        state, '  207\t    "message_count": page["total_count"],')

    assert (first, second) == (True, True)


def test_a_real_user_prompt_after_an_injection_is_still_manual(service):
    service._sessions_lock = threading.RLock()
    state = _state_with_injection()
    service._consume_injected_prompt(state, '  206\t    "messages": history,')

    assert service._consume_injected_prompt(
        state, "attends, montre-moi plutot le diff") is False

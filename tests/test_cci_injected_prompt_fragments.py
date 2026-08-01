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
inside any large paste and is also exactly what a human types. And once the
injection is accounted for, NOTHING more may be claimed against it -- see
``test_a_real_prompt_is_not_swallowed_after_the_injection_was_consumed``.
"""

import queue
import threading
import time

import pytest

from services.cc_interactive_event_service import (
    CCInteractiveEventService, CCInteractiveSessionEvents, _InjectedText)


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

DIGEST = "digest-of-the-whole-thing"


@pytest.fixture
def service():
    svc = object.__new__(CCInteractiveEventService)
    svc._sessions_lock = threading.RLock()
    return svc


def _state_with_injection(text=TOOL_RESULT, digest=DIGEST):
    state = _session()
    # Real clock: the guard drops anything older than its 600s window.
    now = time.time()
    normalized = " ".join(text.split())
    state.injected_prompts = {digest: now}
    state.injected_prompt_texts = [_InjectedText(
        at=now, digest=digest, full=normalized, remaining=normalized,
        last_seen=now)]
    state.pending_injected_prompt_ignores = [now]
    return state


def _matches(state, prompt):
    return CCInteractiveEventService._is_fragment_of_injection(
        state, prompt) is not None


def test_a_fragment_of_the_injection_is_recognised_as_ours():
    state = _state_with_injection()
    assert _matches(state, '"message_count": page["total_count"],')


def test_the_whole_injection_is_not_a_fragment():
    """It has a digest of its own; the fragment path must not shadow the
    digest path or the ticket accounting drifts."""
    state = _state_with_injection()
    assert not _matches(state, TOOL_RESULT)


def test_rewrapped_whitespace_still_matches():
    state = _state_with_injection()
    assert _matches(state, '206\n"messages":    history,')


def test_a_short_prompt_is_never_stolen_from_the_user():
    state = _state_with_injection("please read the file and say ok when done")
    assert not _matches(state, "ok")


def test_an_unrelated_prompt_stays_manual():
    state = _state_with_injection()
    assert not _matches(state, "commit push release et tag")


def test_nothing_injected_means_nothing_to_match():
    state = _session()
    assert not _matches(state, '"messages": history,')


def test_second_submit_of_one_paste_is_consumed_not_persisted(service):
    """The bug, end to end at the guard: the first piece spends the ticket,
    the second used to fall through to "manual prompt"."""
    state = _state_with_injection()

    first = service._consume_injected_prompt(
        state, '  206\t    "messages": history,')
    second = service._consume_injected_prompt(
        state, '  207\t    "message_count": page["total_count"],')

    assert (first, second) == (True, True)


def test_a_real_user_prompt_after_an_injection_is_still_manual(service):
    state = _state_with_injection()
    service._consume_injected_prompt(state, '  206\t    "messages": history,')

    assert service._consume_injected_prompt(
        state, "attends, montre-moi plutot le diff") is False


# ── the injection is spent, and stops claiming ──────────────────────────────


def test_a_claimed_piece_cannot_be_claimed_twice(service):
    """Each piece is cut out of what is left, so the same text arriving again
    is a new prompt -- the user repeating themselves, not our paste."""
    state = _state_with_injection()
    piece = '  207\t    "message_count": page["total_count"],'

    assert service._consume_injected_prompt(state, piece) is True
    # The ignore ticket is gone with the first piece, so a second claim would
    # have to come from the text itself -- and the text no longer holds it.
    assert service._consume_injected_prompt(state, piece) is False


def test_a_real_prompt_is_not_swallowed_after_the_injection_was_consumed(service):
    """The reviewer's reproduction.

    The injection is consumed exactly, by digest. The ticket is spent. Ten
    minutes of window remained, during which any normalized substring of at
    least twelve characters was still accepted as "a fragment of ours" -- so a
    genuine prompt the user typed returned True from _consume_injected_prompt
    and was neither persisted nor answered.
    """
    injected = "Continue from the latest user request and finish the release"
    state = _state_with_injection(injected)
    state.injected_prompts = {
        CCInteractiveEventService._prompt_digest(injected): time.time()}
    state.injected_prompt_texts[0].digest = (
        CCInteractiveEventService._prompt_digest(injected))

    assert service._consume_injected_prompt(state, injected) is True
    assert state.pending_injected_prompt_ignores == []

    assert service._consume_injected_prompt(
        state, "Continue from the latest user request") is False


def test_an_injection_spent_piece_by_piece_also_stops_claiming(service):
    """Same invariant reached the other way: no digest match, just pieces,
    until nothing identifiable is left."""
    injected = "alpha bravo charlie delta echo foxtrot golf hotel india juliet"
    state = _state_with_injection(injected)

    assert service._consume_injected_prompt(
        state, "alpha bravo charlie delta") is True
    assert service._consume_injected_prompt(
        state, "echo foxtrot golf hotel india juliet") is True
    assert state.injected_prompt_texts == []

    assert service._consume_injected_prompt(
        state, "alpha bravo charlie delta") is False


def test_a_stale_burst_no_longer_claims_fragments(service):
    """Time bound, independent of the content bound: pieces of one paste are
    one burst. A substring typed long after it is the user's."""
    state = _state_with_injection()
    state.injected_prompt_texts[0].last_seen = (
        time.time() - CCInteractiveEventService._FRAGMENT_BURST_SECONDS - 1)
    state.pending_injected_prompt_ignores = []

    assert service._consume_injected_prompt(
        state, '  207\t    "message_count": page["total_count"],') is False


def test_the_burst_window_slides_with_each_piece(service):
    """A turn may run between two pieces of the same paste, so the window is
    refreshed by every piece claimed rather than fixed at the injection."""
    state = _state_with_injection()
    old = time.time() - CCInteractiveEventService._FRAGMENT_BURST_SECONDS + 5
    state.injected_prompt_texts[0].last_seen = old

    assert service._consume_injected_prompt(
        state, '  206\t    "messages": history,') is True
    assert state.injected_prompt_texts[0].last_seen > old

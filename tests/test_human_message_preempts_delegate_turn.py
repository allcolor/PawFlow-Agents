"""A human message to a busy agent is never delayed.

Reported 2026-09-21: while a CLI agent ran a delegate turn, a webchat message
was queued with "mode mismatch" and only read after the delegate finished,
whereas typing the same text into the CLI terminal (grab) reached it at once.
Agent triggers keep the sticky-mode rule; external requests stay isolated.
"""

from tasks.ai.agent_streaming import can_preempt_running_turn

USER = {"type": "user", "source_agent": None}
DELEGATE_TURN = {"type": "delegate_reply", "source_agent": "GameDev2",
                 "task_id": "t1"}
EXTERNAL_TURN = {"type": "external_request", "source_agent": "a2a-1"}


def test_human_message_preempts_a_delegate_turn():
    assert can_preempt_running_turn(USER, DELEGATE_TURN)


def test_human_message_preempts_a_user_turn():
    assert can_preempt_running_turn(USER, USER)


def test_human_message_does_not_enter_an_isolated_external_turn():
    assert not can_preempt_running_turn(USER, EXTERNAL_TURN)


def test_same_delegate_thread_still_preempts():
    assert can_preempt_running_turn(dict(DELEGATE_TURN), DELEGATE_TURN)


def test_other_delegate_waits_for_the_running_turn():
    other_task = dict(DELEGATE_TURN, task_id="t2")
    other_caller = dict(DELEGATE_TURN, source_agent="GameDev4")

    assert not can_preempt_running_turn(other_task, DELEGATE_TURN)
    assert not can_preempt_running_turn(other_caller, DELEGATE_TURN)


def test_delegate_request_does_not_enter_a_user_turn():
    assert not can_preempt_running_turn(DELEGATE_TURN, USER)

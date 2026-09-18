"""A failed turn must never be delivered as a delegate reply.

Regression: a provider outage (ollama/zai usage limit) made every turn fail on
an open circuit. The failed turn had no reply, so the delegate wake fell back to
the last persisted assistant message -- the error text -- and delivered it to
the caller, whose own turn then failed identically. The failure ping-ponged
through the whole agent chain and every hop posted the same error in the webchat.
"""

from types import SimpleNamespace

from tasks.ai.agent_core import _delegate_reply_text


def _state(**overrides):
    state = {
        "response_content": "",
        "messages": [],
        "_fatal_error": False,
        "_fatal_error_msg": "",
    }
    state.update(overrides)
    return SimpleNamespace(**state)


def _assistant(content, tool_calls=None):
    return SimpleNamespace(role="assistant", content=content, tool_calls=tool_calls)


def test_failed_turn_delivers_no_delegate_reply():
    state = _state(
        _fatal_error=True,
        _fatal_error_msg="LLM call failed after retry: LLM circuit open",
        messages=[_assistant("LLM call failed after retry: LLM circuit open")])

    assert _delegate_reply_text(state) == ""


def test_successful_turn_falls_back_to_the_persisted_message():
    state = _state(messages=[_assistant("first"), _assistant("the answer")])

    assert _delegate_reply_text(state) == "the answer"


def test_response_content_wins_over_persisted_messages():
    state = _state(response_content="direct answer",
                   messages=[_assistant("older answer")])

    assert _delegate_reply_text(state) == "direct answer"


def test_tool_call_only_messages_are_not_a_reply_body():
    state = _state(messages=[
        _assistant("the answer"),
        _assistant("", tool_calls=[{"name": "bash"}]),
    ])

    assert _delegate_reply_text(state) == "the answer"


def test_turn_without_any_assistant_text_has_no_reply():
    assert _delegate_reply_text(_state()) == ""

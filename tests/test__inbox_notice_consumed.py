"""Neutralize-after-read invariant: the imperative notice never repeats."""

from types import SimpleNamespace

import pytest

from core.agent_inbox import EMPTY_READ_MESSAGE, build_notice
from tasks.ai._inbox_notice_consumed import (
    _DELIVERED_MARKER,
    neutralize_after_inbox_read,
    neutralize_notices_in_messages,
)
from tasks.ai.agent_core import AgentCoreMixin

_ITEM = SimpleNamespace(source="inbox:delegate_reply", payload={"content": "done"})
_NOTICE = build_notice([_ITEM])


def _message(content=_NOTICE, role="user", source=None):
    return SimpleNamespace(role=role, content=content, source=source)


def test_notice_is_disarmed_in_place():
    msg = _message(source={"type": "system", "name": "inbox"})
    assert neutralize_notices_in_messages([msg, _message("unrelated")]) == 1
    assert msg.content == _DELIVERED_MARKER


def test_idempotent_and_does_not_touch_other_messages():
    disarmed = _message(_DELIVERED_MARKER)
    plain = _message("no notice here")
    assert neutralize_notices_in_messages([disarmed, plain]) == 0


def test_notice_rewrite_preserves_surrounding_text_and_other_notices():
    notice = build_notice([_ITEM] * 21)
    msg = _message(f"{_DELIVERED_MARKER}\nKeep this\n{notice}\nAnd this\n{_NOTICE}")
    assert neutralize_notices_in_messages([msg]) == 2
    assert msg.content == (
        f"{_DELIVERED_MARKER}\nKeep this\n{_DELIVERED_MARKER}\nAnd this\n"
        f"{_DELIVERED_MARKER}")


def test_tool_result_and_human_quotes_are_untouched():
    result = AgentCoreMixin._wrap_tool_output(
        "read_inbox", f"Quote [PawFlow inbox: ACTION REQUIRED]\n{_NOTICE}")
    msg = _message(result, role="tool")
    quote = _message(_NOTICE, source={"type": "user"})
    assert neutralize_notices_in_messages([msg, quote]) == 0
    assert msg.content == result
    assert quote.content == _NOTICE


@pytest.mark.parametrize("result", [
    "Error: failed",
    "No unread inbox item stale.",
    "That page was already delivered. Continue with read_inbox(item=\"old\", offset=2000).",
])
def test_failed_or_stale_read_does_not_consume_a_notice(result):
    msg = _message()
    neutralize_after_inbox_read([msg], result)
    assert msg.content == _NOTICE


@pytest.mark.parametrize("result", [EMPTY_READ_MESSAGE, "--- #1 delegate message ---\nData"])
def test_successful_read_consumes_notice(result):
    msg = _message()
    neutralize_after_inbox_read([msg], result)
    assert msg.content == _DELIVERED_MARKER

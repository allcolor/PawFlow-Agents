"""Neutralize-after-read invariant: the imperative notice never repeats."""

from types import SimpleNamespace

from tasks.ai._inbox_notice_consumed import (
    _DELIVERED_MARKER,
    neutralize_notices_in_messages,
)

_NOTICE = (
    "[PawFlow inbox: ACTION REQUIRED] You have 2 unread items:\n"
    "  1. delegate reply from GameDev2 (msg_id=abc)\n"
    "STOP. Call read_inbox() NOW, before any other tool call or answer."
)


def _wrap(_name, text):
    return f"<tool_output>{text}</tool_output>"


def test_notice_is_disarmed_in_place():
    msg = SimpleNamespace(content=_NOTICE)
    messages = [msg, SimpleNamespace(content="unrelated")]
    assert neutralize_notices_in_messages(messages, _wrap) == 1
    assert _DELIVERED_MARKER in msg.content
    assert "STOP. Call read_inbox" not in msg.content
    assert "ACTION REQUIRED] You have" not in msg.content


def test_idempotent_and_does_not_touch_other_messages():
    disarmed = SimpleNamespace(content=_DELIVERED_MARKER)
    plain = SimpleNamespace(content="no notice here")
    assert neutralize_notices_in_messages([disarmed, plain]) == 0


def test_notice_in_tool_envelope_is_also_disarmed():
    wrapped = _wrap("read_inbox", _NOTICE)
    msg = SimpleNamespace(content=wrapped)
    assert neutralize_notices_in_messages([msg]) == 1
    assert _DELIVERED_MARKER in msg.content


def test_plain_read_result_is_untouched():
    result = _wrap("read_inbox", "--- #1 delegate message msg_id=x ---\nDO IT")
    msg = SimpleNamespace(content=result)
    assert neutralize_notices_in_messages([msg]) == 0
    assert msg.content == result

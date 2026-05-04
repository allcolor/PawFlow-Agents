"""Regression tests for chat UI message ordering."""

from pathlib import Path


MESSAGES_JS = Path("tasks/io/chat_ui/messages.js").read_text(encoding="utf-8")


def test_add_msg_inserts_by_message_timestamp():
    """Late SSE tool events must not render after newer assistant text."""
    assert "function _messageSortTs(extra)" in MESSAGES_JS
    assert "function _insertMessageChronologically(container, el, sortTs)" in MESSAGES_JS
    assert "childTs > sortTs" in MESSAGES_JS
    assert "_insertMessageChronologically(container, el, _ts)" in MESSAGES_JS


def test_notification_rows_use_same_ordering_path():
    assert "const notifSortTs = _messageSortTs(extra);" in MESSAGES_JS
    assert "_insertMessageChronologically(notifContainer, notifEl, notifSortTs)" in MESSAGES_JS

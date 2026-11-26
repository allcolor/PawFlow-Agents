"""Remove fulfilled inbox instructions from the model's working context."""

import re

from core.agent_inbox import EMPTY_READ_MESSAGE, NOTICE_INSTRUCTIONS, NOTICE_PREFIX

_DELIVERED_MARKER = "[PawFlow inbox: DELIVERED]"

_NOTICE_IMPERATIVE = re.compile(
    rf"^{re.escape(NOTICE_PREFIX)} You have \d+ unread items?:\r?\n"
    r"(?:  \d+\. [^\r\n]+\r?\n)+"
    r"(?:  \.\.\. and \d+ more unread items\.\r?\n)?"
    + r"\r?\n".join(map(re.escape, NOTICE_INSTRUCTIONS))
    + r"(?=\r?\n|</tool_output>|\Z)",
    re.MULTILINE,
)


def neutralize_notices_in_messages(messages) -> int:
    """Replace complete notice blocks in place, preserving surrounding text."""
    changed = 0
    for msg in messages:
        if getattr(msg, "role", None) != "user":
            continue
        content = msg.content
        if not isinstance(content, str) or NOTICE_PREFIX not in content:
            continue
        source = getattr(msg, "source", None)
        if source and (source.get("type"), source.get("name")) != ("system", "inbox"):
            continue
        new_content, replaced = _NOTICE_IMPERATIVE.subn(_DELIVERED_MARKER, content)
        if replaced:
            msg.content = new_content
            changed += replaced
    return changed


def neutralize_after_inbox_read(messages, result) -> None:
    """A failed or misdirected read leaves the notice actionable."""
    if isinstance(result, str) and (
            result == EMPTY_READ_MESSAGE or result.startswith("--- #")):
        neutralize_notices_in_messages(messages)

"""Neutralize an inbox notice once read_inbox has actually run.

The notice ("[PawFlow inbox: ACTION REQUIRED] ... STOP. Call read_inbox()
NOW") stays in the turn's context forever; a model that obeys it re-calls
read_inbox at every loop iteration even after the box was delivered. When
read_inbox has really executed, the ENTIRE notice is rewritten in place so
later iterations see an auditable marker instead of an order:

    [PawFlow inbox: DELIVERED — read_inbox executed this turn. Do not
    call read_inbox again; a NEW ACTION REQUIRED notice is the only
    reason to read.]

The DELIVERED marker does not match the imperative pattern, so this is
idempotent. Listed items are named in "fully delivered" on the marker;
per-item audit rows live in the conversation transcript, not the notice.
"""

import re

_DELIVERED_MARKER = (
    "[PawFlow inbox: DELIVERED — read_inbox executed this turn. Do not "
    "call read_inbox again; a NEW ACTION REQUIRED notice is the only "
    "reason to read.]"
)

_NOTICE_IMPERATIVE = re.compile(
    r"\[PawFlow inbox: DELIVERED.*?(?=\n|\Z)|"
    r"\[PawFlow inbox: ACTION REQUIRED\].*?(?=\n\n|\Z)",
    re.DOTALL,
)


def neutralize_notices_in_messages(messages, wrap=None) -> int:
    """Replace imperative inbox notices in already-appended messages.

    Mutates messages in place (the turn's LLM context list). Returns the
    number of notices disarmed. Wrapped-envelope variants are matched by
    content, so the rewrite lands regardless of the envelope used.
    """
    changed = 0
    for msg in messages:
        content = getattr(msg, "content", "") or ""
        if "PawFlow inbox: DELIVERED" in content:
            continue
        if "PawFlow inbox: ACTION REQUIRED" not in content:
            continue
        new_content = _NOTICE_IMPERATIVE.sub(_DELIVERED_MARKER, content)
        if new_content == content:
            continue
        msg.content = new_content
        changed += 1
    return changed

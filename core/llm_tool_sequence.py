"""Tool-call sequence repair shared by the agent setup and provider builders.

Strict OpenAI-compatible providers reject any context where an assistant
message with ``tool_calls`` is not immediately followed by a ``tool`` message
for every call id (HTTP 400 "insufficient tool messages following tool_calls
message"). A cancel, compact, or rewind can leave the persisted context with
tool results out of order, duplicated, orphaned, or missing.

``repair_tool_sequence`` rebuilds the list so every assistant tool-call block
is immediately followed by its results, in call order. It never mutates the
input list and returns ``(rebuilt, changed)``.
"""

from __future__ import annotations

from typing import Any, List, Tuple

_UNAVAILABLE_RESULT = (
    "[Result unavailable because the previous turn was "
    "cancelled before this tool result was persisted.]"
)


def repair_tool_sequence(messages: List[Any],
                         conversation_id: str) -> Tuple[List[Any], bool]:
    """Return a provider-valid copy of ``messages``.

    Guarantees:
      * every assistant message with ``tool_calls`` is immediately followed by
        its tool results, in call order;
      * a result persisted BEFORE its assistant message, or interleaved with
        unrelated messages, is moved right after the assistant;
      * duplicate results for one call id keep only the earliest occurrence;
      * orphan results (no owning assistant) are dropped;
      * unanswered calls (preempted turn) get a synthetic "[Result
        unavailable...]" tool message, the same text the agent setup repair
        uses, so the model sees the call existed but its result was lost;
      * un-addressable calls (empty id) are stripped from the assistant
        message, dropping the message entirely when nothing is left of it.
    """
    # Earliest tool message per call id.
    result_indexes = {}
    for idx, message in enumerate(messages):
        if getattr(message, "role", "") != "tool":
            continue
        call_id = getattr(message, "tool_call_id", "") or ""
        if call_id and call_id not in result_indexes:
            result_indexes[call_id] = idx

    moved = set()
    rebuilt = []
    changed = False

    for idx, message in enumerate(messages):
        if idx in moved:
            continue
        if getattr(message, "role", "") == "tool":
            # Tool messages are never appended directly: an assistant block
            # must own them. Duplicates and orphans disappear here.
            call_id = getattr(message, "tool_call_id", "") or ""
            if call_id not in result_indexes or result_indexes[call_id] != idx:
                changed = True
            continue
        rebuilt.append(message)
        if (getattr(message, "role", "") != "assistant"
                or not getattr(message, "tool_calls", None)):
            continue

        expected = []
        stripped = []
        for call in message.tool_calls:
            call_id = getattr(call, "id", "") or ""
            if not call_id:
                stripped.append(call)
                changed = True
                continue
            result_idx = result_indexes.get(call_id)
            if result_idx is not None and result_idx not in moved:
                tool_message = messages[result_idx]
                moved.add(result_idx)
            elif result_idx is not None:
                # The only persisted result for this id was already claimed
                # by an earlier assistant block; never emit a second copy.
                tool_message = _synthetic_result(call_id, conversation_id)
                changed = True
            else:
                tool_message = _synthetic_result(call_id, conversation_id)
                changed = True
            expected.append(tool_message)

        immediate = messages[idx + 1:idx + 1 + len(expected)]
        if immediate != expected:
            changed = True

        if stripped:
            kept = [c for c in message.tool_calls
                    if (getattr(c, "id", "") or "")]
            if kept:
                rebuilt[-1] = _replace_tool_calls(message, kept)
            elif not getattr(message, "content", ""):
                rebuilt.pop()
            elif expected:
                rebuilt[-1] = _replace_tool_calls(message, None)

        if expected:
            rebuilt.extend(expected)

    # A first-occurrence result never claimed by an assistant block is an
    # orphan that just got dropped.
    if any(idx not in moved for idx in result_indexes.values()):
        changed = True

    return rebuilt, changed


def _synthetic_result(call_id: str, conversation_id: str) -> Any:
    from core.llm_client import LLMMessage
    return LLMMessage(
        role="tool",
        content=_UNAVAILABLE_RESULT,
        tool_call_id=call_id,
        conversation_id=conversation_id,
    )


def _replace_tool_calls(message: Any, tool_calls: Any) -> Any:
    import dataclasses
    return dataclasses.replace(message, tool_calls=tool_calls)

"""Regroup split assistant message blocks before handing them to a provider.

Persistence writes each block on its own line (Commit 4):
  - an assistant text-only message, and
  - an assistant tool_calls-only message (content='', thinking on this one)

The Anthropic API requires text and tool_use to live in the same assistant
message, and OpenAI is happiest with one assistant turn = one API message.
This helper is called at the top of every provider-specific message builder
(_build_anthropic_messages, _build_openai_messages, _serialize_messages_for_cli)
to fuse the split pair back into one logical turn for the API.

Old combined messages (pre-split transcripts) pass through untouched.
"""

from __future__ import annotations

from typing import Any, List


def regroup_split_assistant_messages(messages: List[Any]) -> List[Any]:
    """Merge consecutive (assistant text-only) + (assistant tool_calls-only)
    messages into a single combined LLMMessage.

    Args:
        messages: ordered LLMMessage list (assumed well-formed after
            the single-path router in ConversationStore.append_message).

    Returns:
        New list with pairs merged. Does not mutate inputs.
    """
    from core.llm_client import LLMMessage

    out: List[Any] = []
    i = 0
    n = len(messages)
    while i < n:
        m = messages[i]
        nxt = messages[i + 1] if i + 1 < n else None
        if (getattr(m, "role", "") == "assistant"
                and m.content and not getattr(m, "tool_calls", None)
                and nxt is not None
                and getattr(nxt, "role", "") == "assistant"
                and not nxt.content
                and getattr(nxt, "tool_calls", None)):
            merged = LLMMessage(
                role="assistant",
                content=m.content,
                tool_calls=nxt.tool_calls,
                thinking=(getattr(nxt, "thinking", "") or getattr(m, "thinking", "") or ""),
                source=getattr(m, "source", None) or getattr(nxt, "source", None),
                msg_id=m.msg_id,
                timestamp=m.timestamp,
                seq=m.seq,
                conversation_id=(getattr(m, "conversation_id", "")
                                  or getattr(nxt, "conversation_id", "")),
            )
            out.append(merged)
            i += 2
            continue
        out.append(m)
        i += 1
    return out

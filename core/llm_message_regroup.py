"""Provider adapter hook for assistant-turn messages.

Conversation storage is row-centric, but AgentSerializationMixin reconstructs
provider-facing LLMMessage objects before they reach providers. By the time this
hook runs, assistant text, thinking, and tool calls are already attached to one
LLMMessage. The function remains as an explicit boundary for provider builders.
"""

from __future__ import annotations

from typing import Any, List


def regroup_split_assistant_messages(messages: List[Any]) -> List[Any]:
    """Return provider-facing messages unchanged."""
    return list(messages)

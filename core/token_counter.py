"""Precise token counting using tiktoken."""

import logging
import tiktoken

logger = logging.getLogger(__name__)

_encoding = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    """Count tokens precisely."""
    return len(_encoding.encode(text))


def count_messages_tokens(messages: list) -> int:
    """Count tokens for a list of LLM messages."""
    total = 0
    for msg in messages:
        overhead = 4  # role + separators
        content = msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
        if isinstance(content, str):
            total += count_tokens(content) + overhead
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    text = block.get("text", "")
                    if text:
                        total += count_tokens(text)
            total += overhead
        else:
            total += overhead
    return total

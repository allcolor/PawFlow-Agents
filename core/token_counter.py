"""Precise token counting using tiktoken.

tiktoken's `cl100k_base` is OpenAI's tokenizer — it is an approximation
for Anthropic / Google / Meta models. Opus 4.7 in particular uses a new
tokenizer that costs ~1.33-1.47x more tokens than 4.6 for the same
content (measured on code-heavy Claude Code content). The `multiplier`
parameter lets each llm_service scale the raw count back to real tokens
so the compact trigger and the context gauge reflect reality.

Pass the multiplier from `llm_service.config["token_multiplier"]`; 0 or
unset = 1.0 (no correction).
"""

import logging
import tiktoken

logger = logging.getLogger(__name__)

_encoding = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str, multiplier: float = 1.0) -> int:
    """Count tokens precisely and scale by `multiplier`."""
    raw = len(_encoding.encode(text))
    if multiplier and multiplier != 1.0:
        return int(raw * multiplier)
    return raw


def count_messages_tokens(messages: list, multiplier: float = 1.0) -> int:
    """Count tokens for a list of LLM messages, scaled by `multiplier`."""
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
    if multiplier and multiplier != 1.0:
        return int(total * multiplier)
    return total


def resolve_token_multiplier(service_config) -> float:
    """Read `token_multiplier` from an llm_service config (dict-like).

    Returns 1.0 when unset or 0. Centralized so every call-site reads
    the same key with the same fallback.
    """
    if service_config is None:
        return 1.0
    try:
        raw = service_config.get("token_multiplier", 0)
    except AttributeError:
        return 1.0
    try:
        v = float(raw or 0)
    except (TypeError, ValueError):
        return 1.0
    return v if v > 0 else 1.0

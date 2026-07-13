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

_encoding = None
_encoding_failed_at = 0.0  # monotonic timestamp of last failure; 0 = never tried

# Retry tiktoken after this delay (seconds) so a transient network/cache
# failure at startup doesn't permanently degrade token counting for the
# entire server lifetime.
_ENCODING_RETRY_SECONDS = 300.0  # 5 minutes


def _get_encoding():
    """Return the tiktoken encoding, or None when its cache/download fails.

    A failure is not permanent: after _ENCODING_RETRY_SECONDS we retry so
    a transient network issue at startup doesn't degrade all token counts
    for the server's lifetime (which would inflate the context gauge by
    ~1.1-2x depending on content).
    """
    global _encoding, _encoding_failed_at
    if _encoding is not None:
        return _encoding
    import time as _time
    now = _time.monotonic()
    if _encoding_failed_at and (now - _encoding_failed_at) < _ENCODING_RETRY_SECONDS:
        return None
    try:
        _encoding = tiktoken.get_encoding("cl100k_base")
        _encoding_failed_at = 0.0
        return _encoding
    except Exception as exc:  # pragma: no cover - exercised with monkeypatch
        _encoding_failed_at = now
        logger.warning(
            "tiktoken cl100k_base unavailable; falling back to approximate token counts: %s",
            exc,
        )
        return None


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    # Conservative local fallback when tiktoken cannot load its BPE file.
    return max(1, (len(text.encode("utf-8")) + 3) // 4)


def count_tokens(text: str, multiplier: float = 1.0) -> int:
    """Count tokens precisely and scale by `multiplier`."""
    encoding = _get_encoding()
    if encoding is None:
        raw = _estimate_tokens(text)
    else:
        raw = len(encoding.encode(text, disallowed_special=()))
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

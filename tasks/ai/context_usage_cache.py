"""Cached context-window usage for PawFlow agent contexts."""

from __future__ import annotations
import logging

import hashlib
import json
import time
from typing import Any, Dict, Iterable, List, Optional


def _message_content(msg: Any) -> Any:
    if isinstance(msg, dict):
        return msg.get("content", "")
    return getattr(msg, "content", "")


def _message_role(msg: Any) -> str:
    if isinstance(msg, dict):
        return str(msg.get("role", "") or "")
    return str(getattr(msg, "role", "") or "")


def _message_id(msg: Any) -> str:
    if isinstance(msg, dict):
        return str(msg.get("msg_id") or msg.get("id") or "")
    return str(getattr(msg, "msg_id", "") or getattr(msg, "id", "") or "")


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        ptype = content.get("type")
        if ptype in ("image", "image_url", "image_ref"):
            return "[image]"
        return str(content.get("text", "") or content.get("content", "") or "")
    if isinstance(content, list):
        parts: List[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            ptype = part.get("type")
            if ptype in ("image", "image_url", "image_ref"):
                parts.append("[image]")
            else:
                parts.append(str(part.get("text", "") or ""))
        return " ".join(parts)
    try:
        return json.dumps(content, sort_keys=True, ensure_ascii=False)
    except Exception:
        return str(content)


def _strip_for_count(messages: Iterable[Any]) -> List[Dict[str, str]]:
    return [{"content": _content_text(_message_content(m))} for m in messages]


def _marker(msg: Any) -> str:
    text = _content_text(_message_content(msg))
    sample = f"{text[:160]}\0{text[-160:] if len(text) > 160 else text}"
    digest = hashlib.sha1(
        sample.encode("utf-8", "ignore"),
        usedforsecurity=False,
    ).hexdigest()[:16]
    return f"{_message_role(msg)}:{_message_id(msg)}:{len(text)}:{digest}"


def _cache_params(max_context_size: int, token_multiplier: float,
                  overhead: int = 0) -> Dict[str, Any]:
    return {
        "max": int(max_context_size or 0),
        "token_multiplier": round(float(token_multiplier or 1.0), 6),
        "overhead": int(overhead or 0),
    }


def _count(messages: Iterable[Any], token_multiplier: float = 1.0) -> int:
    from core.token_counter import count_messages_tokens

    return int(count_messages_tokens(
        _strip_for_count(messages),
        multiplier=float(token_multiplier or 1.0),
    ) or 0)


def context_usage_entry(messages: Iterable[Any], used: int,
                        max_context_size: int, *, source: str,
                        token_multiplier: float = 1.0,
                        cache_mode: str = "full",
                        overhead: int = 0,
                        updated_at: Optional[float] = None) -> Dict[str, Any]:
    """Build a context-usage entry.

    ``used`` is the FINAL token count and already includes ``overhead``
    (the invisible provider system-prompt/tooling tokens). ``overhead``
    is only recorded for transparency and cache invalidation.
    """
    msg_list = list(messages or [])
    max_ctx = int(max_context_size or 0)
    overhead_i = max(0, int(overhead or 0))
    used_i = max(0, int(used or 0))
    return {
        "used": used_i,
        "max": max_ctx,
        "pct": (used_i / max_ctx) if max_ctx > 0 else 0.0,
        "source": source,
        "updated_at": updated_at if updated_at is not None else time.time(),
        "message_count": len(msg_list),
        "overhead_tokens": overhead_i,
        "first_marker": _marker(msg_list[0]) if msg_list else "",
        "last_marker": _marker(msg_list[-1]) if msg_list else "",
        "cache_params": _cache_params(max_ctx, token_multiplier, overhead_i),
        "cache_mode": cache_mode,
    }


def context_usage_from_cache(messages: Iterable[Any], max_context_size: int,
                             cache: Optional[Dict[str, Any]] = None, *,
                             source: str,
                             token_multiplier: float = 1.0,
                             overhead: int = 0) -> Dict[str, Any]:
    """Return cached context usage, counting only the appended suffix when safe.

    ``overhead`` is a fixed token count added on top of the message
    recount (the provider's invisible system-prompt/tooling tokens). It
    is baked into the cached ``used`` value so the delta path stays
    consistent across calls.
    """
    msg_list = list(messages or [])
    max_ctx = int(max_context_size or 0)
    overhead_i = max(0, int(overhead or 0))
    params = _cache_params(max_ctx, token_multiplier, overhead_i)
    n = len(msg_list)

    if isinstance(cache, dict):
        try:
            cached_n = int(cache.get("message_count", -1))
            cached_used = int(cache.get("used", -1))
            same_params = cache.get("cache_params") == params
            same_first = (not msg_list) or cache.get("first_marker") == _marker(msg_list[0])
            same_last = (n == cached_n and
                         (not msg_list or cache.get("last_marker") == _marker(msg_list[-1])))
            if same_params and same_first and cached_used >= 0 and cached_n == n and same_last:
                out = dict(cache)
                out["source"] = source
                out["updated_at"] = time.time()
                return out
            if (same_params and same_first and cached_used >= 0 and
                    0 < cached_n < n and
                    cache.get("last_marker") == _marker(msg_list[cached_n - 1])):
                # cached_used already includes `overhead`; the delta is
                # a raw recount of the appended suffix only.
                delta = _count(msg_list[cached_n:], token_multiplier)
                return context_usage_entry(
                    msg_list, cached_used + delta, max_ctx,
                    source=source, token_multiplier=token_multiplier,
                    cache_mode="delta", overhead=overhead_i)
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

    return context_usage_entry(
        msg_list, _count(msg_list, token_multiplier) + overhead_i, max_ctx,
        source=source, token_multiplier=token_multiplier,
        cache_mode="full", overhead=overhead_i)


def context_usage_append_delta(cache: Dict[str, Any], message: Any, *,
                               source: str) -> Optional[Dict[str, Any]]:
    """Advance a valid context-usage cache by one appended message.

    Streaming callbacks need a cheap live gauge update. For CLI providers the
    full authoritative calculation may reload stored context on every append;
    this helper keeps the hot path O(size of appended message) once a valid
    cache exists.
    """
    if not isinstance(cache, dict):
        return None
    try:
        params = cache.get("cache_params") or {}
        max_ctx = int(cache.get("max", 0) or params.get("max", 0) or 0)
        if max_ctx <= 0:
            return None
        token_multiplier = float(params.get("token_multiplier", 1.0) or 1.0)
        used = int(cache.get("used", 0) or 0)
        message_count = int(cache.get("message_count", 0) or 0)
        overhead = int(cache.get("overhead_tokens", 0) or 0)
        delta = _count([message], token_multiplier)
        out = dict(cache)
        out.update({
            "used": max(0, used + delta),
            "max": max_ctx,
            "pct": ((used + delta) / max_ctx) if max_ctx > 0 else 0.0,
            "source": source,
            "updated_at": time.time(),
            "message_count": message_count + 1,
            "overhead_tokens": overhead,
            "last_marker": _marker(message),
            "cache_mode": "append_delta",
        })
        return out
    except Exception:
        logging.getLogger(__name__).debug("append delta context usage failed",
                                          exc_info=True)
        return None

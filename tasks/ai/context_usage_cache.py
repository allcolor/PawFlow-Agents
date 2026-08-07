"""Cached context-window usage for PawFlow agent contexts."""

from __future__ import annotations
import logging

import hashlib
import json
import re
import time
from typing import Any, Dict, Iterable, List, Optional


_DATA_URI_RE = re.compile(r'data:image/[^;\s]+;base64,[A-Za-z0-9+/=]+')
_IMAGE_MARKER_RE = re.compile(r'__image_data__:image/[^:\s]+:[A-Za-z0-9+/=]+')
_JSON_IMAGE_DATA_RE = re.compile(
    r'(["\'](?:data|content)["\']\s*:\s*["\'])[A-Za-z0-9+/=]{1000,}(["\'])')
_CLI_BOOTSTRAP_CONTEXT_PATHS = (
    "/.pawflow_cli/initial_context.md",
    "/.pawflow_cci/initial_context.md",
    "/.pawflow_ag/initial_context.md",
)
# 4: PawFlow's messages are always charged, and the bootstrap read body --
# the same context in its serialized form -- never is. 3 did the opposite,
# so every cached `used` written under it describes a different quantity.
_CONTEXT_ACCOUNTING_VERSION = 4


def _scrub_image_payloads(text: str) -> str:
    if not text:
        return text
    text = _DATA_URI_RE.sub('[image]', text)
    text = _IMAGE_MARKER_RE.sub('[image]', text)
    return _JSON_IMAGE_DATA_RE.sub(r'\1[image]\2', text)


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


def _message_tool_calls(msg: Any) -> List[Any]:
    if isinstance(msg, dict):
        return list(msg.get("tool_calls") or [])
    return list(getattr(msg, "tool_calls", None) or [])


def _tool_call_field(tool_call: Any, name: str) -> Any:
    if isinstance(tool_call, dict):
        return tool_call.get(name)
    return getattr(tool_call, name, None)


def _is_cli_bootstrap_read(tool_call: Any) -> bool:
    """Return whether a native provider tool accesses PawFlow's bootstrap file.

    The bootstrap body is a serialization of the PawFlow messages that the
    gauge already counts.  Native reads are persisted for UI/history parity,
    but counting their output would charge the same context a second time.
    """
    if str(_tool_call_field(tool_call, "tool_origin") or "").lower() != "native":
        return False
    arguments = _tool_call_field(tool_call, "arguments")
    try:
        rendered = json.dumps(arguments, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        rendered = str(arguments or "")
    normalized = re.sub(r"/+", "/", rendered.replace("\\", "/")).lower()
    return any(path in normalized for path in _CLI_BOOTSTRAP_CONTEXT_PATHS)


def _message_source(msg: Any) -> Dict[str, Any]:
    if isinstance(msg, dict):
        source = msg.get("source")
    else:
        source = getattr(msg, "source", None)
    return source if isinstance(source, dict) else {}


def _is_cli_bootstrap_boundary(msg: Any) -> bool:
    return (_message_source(msg).get("context_usage_boundary")
            == "cli_bootstrap_read")


def _cli_bootstrap_boundary_index(messages: Iterable[Any]) -> int:
    boundary = -1
    for index, msg in enumerate(messages):
        if _is_cli_bootstrap_boundary(msg):
            boundary = index
    return boundary


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return _scrub_image_payloads(content)
    if isinstance(content, dict):
        ptype = content.get("type")
        if ptype in ("image", "image_url", "image_ref"):
            return "[image]"
        return _scrub_image_payloads(
            str(content.get("text", "") or content.get("content", "") or ""))
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


def _message_tool_call_id(msg: Any) -> str:
    if isinstance(msg, dict):
        return str(msg.get("tool_call_id") or "")
    return str(getattr(msg, "tool_call_id", "") or "")


def _is_bootstrap_body(msg: Any, call_ids: set) -> bool:
    """Whether this message carries a copy of the serialized PawFlow context.

    Two ways to know, because a gauge runs on both fresh and reloaded data.
    The flag is stamped on the result when the turn produces it and is the
    only signal available to the single-message append delta, which has no
    list to correlate a ``tool_call_id`` against. The id set covers messages
    written before the flag existed, and any path that re-reads a stored
    context whole.
    """
    if _message_source(msg).get("context_usage_bootstrap_body"):
        return True
    if not call_ids or _message_role(msg) != "tool":
        return False
    return _message_tool_call_id(msg) in call_ids


def _bootstrap_body_call_ids(messages: Iterable[Any]) -> set:
    """Ids of every call that read the bootstrap file, paginated ones included.

    Deliberately not gated on "first read seen": a bootstrap file past the
    native read ceiling is consumed page by page, and each page returns
    another slice of the same serialized context.
    """
    call_ids = set()
    for msg in messages or []:
        for tool_call in _message_tool_calls(msg):
            if not _is_cli_bootstrap_read(tool_call):
                continue
            call_id = str(
                tool_call.get("id") if isinstance(tool_call, dict)
                else getattr(tool_call, "id", "") or "")
            if call_id:
                call_ids.add(call_id)
    return call_ids


def _strip_for_count(messages: Iterable[Any],
                     call_ids: Optional[set] = None) -> List[Dict[str, str]]:
    """Render messages down to countable text, charging each context once.

    PawFlow's own messages are the authoritative account of what the agent
    holds, on every provider and at every moment -- no provider has to report
    anything for this number to exist, and it cannot collapse because a
    native read failed or has not happened yet.

    What must NOT be added on top is the bootstrap read output: a cold CLI
    start serializes those same messages into initial_context.md, and the
    agent reads that file back. Its body and the messages are the same
    context in two representations, so only the messages are charged.
    """
    msg_list = list(messages or [])
    if call_ids is None:
        call_ids = _bootstrap_body_call_ids(msg_list)
    stripped = []
    for msg in msg_list:
        if _is_bootstrap_body(msg, call_ids):
            stripped.append({"content": ""})
            continue
        stripped.append({"content": _content_text(_message_content(msg))})
    return stripped


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
        "accounting_version": _CONTEXT_ACCOUNTING_VERSION,
        "max": int(max_context_size or 0),
        "token_multiplier": round(float(token_multiplier or 1.0), 6),
        "overhead": int(overhead or 0),
    }


def _count(messages: Iterable[Any], token_multiplier: float = 1.0,
           call_ids: Optional[set] = None) -> int:
    from core.token_counter import count_messages_tokens

    return int(count_messages_tokens(
        _strip_for_count(messages, call_ids),
        multiplier=float(token_multiplier or 1.0),
    ) or 0)


def context_usage_entry(messages: Iterable[Any], used: int,
                        max_context_size: int, *, source: str,
                        token_multiplier: float = 1.0,
                        cache_mode: str = "full",
                        overhead: int = 0,
                        bootstrap_context_start: Optional[int] = None,
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
        "bootstrap_context_start": (
            _cli_bootstrap_boundary_index(msg_list)
            if bootstrap_context_start is None
            else int(bootstrap_context_start)),
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
                    not cache.get("context_source_measured") and
                    0 < cached_n < n and
                    cache.get("last_marker") == _marker(msg_list[cached_n - 1])):
                # cached_used already includes `overhead`; the delta is
                # a raw recount of the appended suffix only. It is also our
                # OWN count, which is why a measured cache is excluded above:
                # there, cached_used is the provider's reported prompt size and
                # the suffix is already inside it.
                suffix = msg_list[cached_n:]
                # The bootstrap ids come from the FULL list: a read result can
                # land in the suffix while the assistant call that identifies
                # it sits before the cache boundary, and counting that body
                # would charge the serialized context a second time.
                delta = _count(suffix, token_multiplier,
                               _bootstrap_body_call_ids(msg_list))
                return context_usage_entry(
                    msg_list, cached_used + delta, max_ctx,
                    source=source, token_multiplier=token_multiplier,
                    cache_mode="delta", overhead=overhead_i,
                    bootstrap_context_start=int(
                        cache.get("bootstrap_context_start", -1) or -1))
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
        if cache.get("context_source_measured"):
            # `used` is not PawFlow's count here: it is the prompt size the
            # provider itself reported on its last observed exchange, and that
            # number ALREADY contains everything in its window -- including the
            # message being appended right now. Adding our own count on top
            # double-counts it, and the error compounds with every append: the
            # gauge climbs all turn (observed: 62% -> 92% with no compaction)
            # until the next full recompute drops it back onto the measurement.
            # A measured cache can only be advanced by a new measurement, so
            # decline and let the caller run the authoritative calculation.
            return None
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

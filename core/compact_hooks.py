"""Compact hook registry — pre/post callbacks around _compact().

Third-party code (tasks, services, user extensions) can subscribe a
callable to run before a compact starts or after it finishes. The
bundled _compact() in tasks/ai/agent_compaction.py fires both events
around the actual summarisation work.

Contracts
─────────
pre_compact(ctx: dict) -> Optional[dict]
    ctx = {
        "trigger":            "manual" | "auto" | "cc_boundary",
        "conversation_id":    str,
        "agent_name":         str,
        "user_id":            str,
        "compact_instructions": str,       # caller-provided, may be empty
        "force":              bool,
        "original_tokens":    int,         # estimated pre-compact size
    }
    Return a dict with any of:
        "compact_instructions": str   # OVERRIDES ctx["compact_instructions"]
        "append_instructions":  str   # APPENDED to current instructions
        "user_display_message": str   # shown in the UI banner
        "abort":                bool  # True → skip compact entirely
    Any other keys are ignored. Return None / {} for a no-op hook.

post_compact(ctx: dict) -> Optional[dict]
    ctx = {
        "trigger", "conversation_id", "agent_name", "user_id",
        "before_messages": int,
        "after_messages":  int,
        "tokens_before":   int,
        "tokens_after":    int,
        "bucket_id":       str | None,   # new bucket created in step 1, if any
        "compacted":       List[LLMMessage],   # the output (mutable — hooks can append)
    }
    Return value is currently informational (logged); mutating
    ctx["compacted"] in place is how a hook injects extra messages.

Hooks are synchronous, run in registration order, exceptions logged and
swallowed (a broken third-party hook can't kill compaction).
"""

import logging
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# (name, callable) pairs, registration order preserved.
_pre_hooks: List[Tuple[str, Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]]] = []
_post_hooks: List[Tuple[str, Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]]] = []
_lock = threading.Lock()


def subscribe_pre_compact(name: str,
                          fn: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]) -> None:
    """Register a pre_compact hook. `name` is purely a label (logs/unsubscribe)."""
    with _lock:
        _pre_hooks.append((name, fn))
    logger.info("[compact-hooks] subscribed pre_compact: %s", name)


def subscribe_post_compact(name: str,
                           fn: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]) -> None:
    with _lock:
        _post_hooks.append((name, fn))
    logger.info("[compact-hooks] subscribed post_compact: %s", name)


def unsubscribe_pre_compact(name: str) -> int:
    with _lock:
        before = len(_pre_hooks)
        _pre_hooks[:] = [(n, f) for n, f in _pre_hooks if n != name]
        return before - len(_pre_hooks)


def unsubscribe_post_compact(name: str) -> int:
    with _lock:
        before = len(_post_hooks)
        _post_hooks[:] = [(n, f) for n, f in _post_hooks if n != name]
        return before - len(_post_hooks)


def list_hooks() -> Dict[str, List[str]]:
    with _lock:
        return {
            "pre_compact":  [n for n, _ in _pre_hooks],
            "post_compact": [n for n, _ in _post_hooks],
        }


def fire_pre_compact(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Run every pre_compact hook in order, merge their directives.

    Returns an aggregate dict that the caller applies:
      - compact_instructions: final value (last-write-wins if multiple
        hooks set it; hooks using append_instructions stack up)
      - user_display_message: last non-empty wins
      - abort: True if ANY hook requested it
    """
    with _lock:
        hooks = list(_pre_hooks)
    aggregate: Dict[str, Any] = {
        "compact_instructions": ctx.get("compact_instructions", ""),
        "user_display_message": "",
        "abort": False,
    }
    for name, fn in hooks:
        try:
            result = fn(dict(ctx)) or {}
        except Exception as e:
            logger.warning("[compact-hooks] pre_compact hook %r raised %s — skipping",
                           name, e, exc_info=True)
            continue
        if not isinstance(result, dict):
            continue
        if "compact_instructions" in result:
            aggregate["compact_instructions"] = str(result["compact_instructions"])
        _append = result.get("append_instructions")
        if _append:
            base = aggregate["compact_instructions"]
            aggregate["compact_instructions"] = (
                f"{base}\n\n{_append}" if base else str(_append))
        if result.get("user_display_message"):
            aggregate["user_display_message"] = str(result["user_display_message"])
        if result.get("abort"):
            aggregate["abort"] = True
            logger.info("[compact-hooks] pre_compact %r requested abort", name)
    return aggregate


def fire_post_compact(ctx: Dict[str, Any]) -> None:
    """Run every post_compact hook in order. Return values are logged only.

    Hooks mutate ctx['compacted'] in place to inject extra messages.
    """
    with _lock:
        hooks = list(_post_hooks)
    for name, fn in hooks:
        try:
            fn(dict(ctx) if ctx.get("compacted") is None else ctx)
        except Exception as e:
            logger.warning("[compact-hooks] post_compact hook %r raised %s — skipping",
                           name, e, exc_info=True)

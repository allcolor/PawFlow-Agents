"""TTL resolution helpers for transient FileStore entries."""

from __future__ import annotations

import os
from typing import Iterable, Optional


def _raw_value(value):
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, dict) and "value" in value:
        return value.get("value")
    return value


def _positive_int(value) -> Optional[int]:
    try:
        parsed = int(str(_raw_value(value)).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _conversation_param(conversation_id: str, keys: Iterable[str]) -> Optional[int]:
    if not conversation_id:
        return None
    try:
        from core.conversation_store import ConversationStore
        params = ConversationStore.instance().get_extra(
            conversation_id, "conv_parameters") or {}
    except Exception:
        return None
    if not isinstance(params, dict):
        return None
    for key in keys:
        if key in params:
            value = _positive_int(params.get(key))
            if value is not None:
                return value
    return None


def resolve_ttl_seconds(*, conversation_id: str = "", conv_keys: Iterable[str],
                        env_key: str = "", default: int,
                        minimum: int = 60) -> int:
    """Resolve a transient FileStore TTL.

    Priority: conversation parameter, environment variable, default. Values must
    be positive; the final TTL is clamped to ``minimum`` to avoid accidental
    permanent or near-immediate expiry for user-visible files.
    """
    ttl = _conversation_param(conversation_id, conv_keys)
    if ttl is None and env_key:
        ttl = _positive_int(os.environ.get(env_key))
    if ttl is None:
        ttl = int(default)
    return max(int(minimum), int(ttl))

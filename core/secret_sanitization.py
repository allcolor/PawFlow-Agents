"""Remove runtime-only secret material from serializable values."""

from __future__ import annotations

from typing import Any, Tuple


_SECRET_RUNTIME_KEYS = frozenset({"_secret_env"})


def strip_secret_runtime_values(value: Any) -> Any:
    """Return a copy of ``value`` without runtime-only secret mappings."""
    clean, _removed = strip_secret_runtime_values_counted(value)
    return clean


def strip_secret_runtime_values_counted(value: Any) -> Tuple[Any, int]:
    """Return ``(clean_value, removed_key_count)`` recursively."""
    if isinstance(value, dict):
        clean = {}
        removed = 0
        changed = False
        for key, item in value.items():
            if str(key) in _SECRET_RUNTIME_KEYS:
                removed += 1
                changed = True
                continue
            clean_item, item_removed = strip_secret_runtime_values_counted(item)
            clean[key] = clean_item
            removed += item_removed
            changed = changed or clean_item is not item
        return (clean if changed else value), removed
    if isinstance(value, list):
        clean = []
        removed = 0
        changed = False
        for item in value:
            clean_item, item_removed = strip_secret_runtime_values_counted(item)
            clean.append(clean_item)
            removed += item_removed
            changed = changed or clean_item is not item
        return (clean if changed else value), removed
    if isinstance(value, tuple):
        clean_items = []
        removed = 0
        changed = False
        for item in value:
            clean_item, item_removed = strip_secret_runtime_values_counted(item)
            clean_items.append(clean_item)
            removed += item_removed
            changed = changed or clean_item is not item
        return (tuple(clean_items) if changed else value), removed
    return value, 0

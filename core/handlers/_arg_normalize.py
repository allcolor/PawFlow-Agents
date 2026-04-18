"""Tolerant argument normalization for tool-handler parameters.

LLMs don't always respect the JSON schema they're handed — they may send
a single string where a list is expected, or a list of strings where a
list of objects is expected, especially for smaller models or in
stream-json mode.

These helpers accept the common wrong shapes and coerce them into the
canonical form declared by the schema. Where coercion isn't possible
(multi-field objects where we can't guess which field the agent meant),
they return a friendly error message instead of crashing with an
`AttributeError: 'str' object has no attribute 'get'`.
"""

from typing import Any, List, Optional, Tuple


def normalize_string_list(value: Any, sep: str = ",") -> List[str]:
    """Coerce a string-list parameter to a real list of non-empty strings.

    Handles:
    - None → []
    - [] → []
    - list of str → trimmed non-empty entries
    - str → split by `sep` (fallback to newline split if no sep found)
    - other → [str(value)] as a best effort
    """
    if value is None:
        return []
    if isinstance(value, str):
        parts = value.split(sep) if sep in value else value.splitlines()
        return [p.strip() for p in parts if p.strip()]
    if isinstance(value, list):
        out = []
        for item in value:
            if item is None:
                continue
            if isinstance(item, str):
                s = item.strip()
                if s:
                    out.append(s)
            else:
                s = str(item).strip()
                if s:
                    out.append(s)
        return out
    return [str(value).strip()] if str(value).strip() else []


def normalize_single_field_object_list(
    value: Any,
    key: str,
    line_split: bool = True,
) -> List[dict]:
    """Coerce a list-of-single-field-objects to its canonical shape.

    For schemas like `[{"description": "..."}]` where the object has
    exactly one meaningful field, we can unambiguously map a plain
    string or list of strings into the expected dict form.

    Handles:
    - None → []
    - list of dict → passthrough
    - list of str → [{key: s}, ...]
    - str (line_split=True) → split on \\n, then on ',' if single line
    - str (line_split=False) → [{key: value}]

    Dicts missing the key keep their shape; caller still validates.
    """
    if value is None:
        return []
    if isinstance(value, str):
        if line_split:
            parts = value.splitlines()
            if len(parts) <= 1:
                parts = value.split(",")
            return [{key: p.strip()} for p in parts if p.strip()]
        s = value.strip()
        return [{key: s}] if s else []
    if isinstance(value, list):
        out = []
        for item in value:
            if item is None:
                continue
            if isinstance(item, dict):
                out.append(item)
            elif isinstance(item, str):
                s = item.strip()
                if s:
                    out.append({key: s})
            else:
                s = str(item).strip()
                if s:
                    out.append({key: s})
        return out
    return []


def validate_object_list(
    value: Any,
    param_name: str,
    required_keys: List[str],
    example: str,
) -> Tuple[Optional[List[dict]], Optional[str]]:
    """Shape check for multi-field object lists without auto-coercion.

    Multi-field objects like `{path, old_string, new_string}` can't be
    built from a bare string — we'd have to guess which field the agent
    meant, and guessing wrong silently corrupts data. Instead we return
    an explicit error that tells the agent the expected shape, so its
    next attempt will be correct.

    Returns (normalized_list, None) on success,
    or (None, error_message) on failure.
    """
    if value is None:
        return ([], None)
    if not isinstance(value, list):
        return (None,
                f"'{param_name}' must be a list of objects, got "
                f"{type(value).__name__}. Expected: {example}")
    bad = []
    for i, item in enumerate(value):
        if not isinstance(item, dict):
            bad.append(f"{param_name}[{i}] is {type(item).__name__} (need object)")
            continue
        missing = [k for k in required_keys if not item.get(k)]
        if missing:
            bad.append(f"{param_name}[{i}] missing {missing}")
    if bad:
        return (None,
                f"malformed '{param_name}' — " + "; ".join(bad) + ". "
                f"Expected: {example}")
    return (value, None)

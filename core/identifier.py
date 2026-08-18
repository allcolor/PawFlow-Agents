"""Case-insensitive resolution for user-defined PawFlow identifiers."""

from collections.abc import Iterable, Mapping
from typing import Optional


def identifier_key(value: object) -> str:
    """Return the comparison key for a user-defined identifier."""
    return str(value or "").strip().casefold()


def resolve_identifier(values: Iterable[str], requested: object) -> Optional[str]:
    """Return the stored spelling matching ``requested`` case-insensitively.

    Exact matches win. Multiple legacy spellings with the same case-folded value
    are rejected instead of selecting a target nondeterministically.
    """
    names = list(values.keys() if isinstance(values, Mapping) else values)
    raw = str(requested or "").strip()
    if raw in names:
        return raw
    folded = identifier_key(raw)
    matches = [name for name in names if identifier_key(name) == folded]
    if len(matches) > 1:
        raise ValueError(
            f"Ambiguous identifier '{raw}': {', '.join(sorted(matches))}")
    return matches[0] if matches else None


def identifiers_equal(first: object, second: object) -> bool:
    """Compare two user-defined identifiers without case sensitivity."""
    return identifier_key(first) == identifier_key(second)

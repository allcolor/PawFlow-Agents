"""Context-window budget helpers.

PawFlow distinguishes the provider's real context window from the budget
configured by the user. The effective window is the smaller known value so
compaction and gauges can intentionally run below a model's hard limit.
"""

from __future__ import annotations


def positive_int(value) -> int:
    """Return value as a positive int, or 0 when unset/invalid."""
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def effective_context_window(configured=0, real=0, fallback: int = 200000) -> int:
    """Resolve the PawFlow context budget.

    configured: user/PawFlow configured budget in tokens.
    real: provider-reported/model hard window when known.
    fallback: only used when neither configured nor real is known.
    """
    configured_i = positive_int(configured)
    real_i = positive_int(real)
    if configured_i and real_i:
        return min(configured_i, real_i)
    if configured_i:
        return configured_i
    if real_i:
        return real_i
    try:
        fallback_i = int(fallback)
    except (TypeError, ValueError):
        fallback_i = 200000
    return fallback_i if fallback_i >= 0 else 200000

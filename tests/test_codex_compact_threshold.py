"""Lock the codex end-of-turn compact threshold check against
regression: it MUST compare against the context window (1M default,
or the agent service's `max_context_size`), NOT against the
`max_tokens` function parameter — which is the OUTPUT response
limit and is typically 0 ("no cap"), silently disabling every
subsequent compact.

The check lives at the `turn.completed` event handler in
`core/llm_providers/codex.py`. We verify by static parsing that:
  - `_ctx_max_live` is computed from `_codex_context_window` /
    config `max_context_size` / 1_000_000 fallback,
  - it is NOT assigned `= max_tokens` directly.

This matches how the per-tool live-gauge bump at item.completed
already computes the value, ensuring a single source of truth.
"""

import re
from pathlib import Path

_CODEX_SRC = Path("core/llm_providers/codex.py").read_text(encoding="utf-8")


def _turn_completed_block() -> str:
    """Return the source text from the start of the turn.completed
    handler up to (and excluding) `if etype == "turn.failed"` — the
    next handler. Plain string slicing keeps us tolerant to local
    refactors as long as the structure is preserved."""
    start = _CODEX_SRC.index('if etype == "turn.completed":')
    end = _CODEX_SRC.index('if etype == "turn.failed"', start)
    return _CODEX_SRC[start:end]


def test_turn_completed_does_not_use_max_tokens_as_context_window():
    block = _turn_completed_block()
    # The bug we are guarding against: `_ctx_max_live = max_tokens`
    # which is the OUTPUT response limit, not the context window.
    assert "_ctx_max_live = max_tokens" not in block, (
        "_ctx_max_live must not be set from the output `max_tokens` "
        "parameter — that's the response cap, not the context window. "
        "Use _codex_context_window / config max_context_size / 1M fallback.")


def test_turn_completed_uses_context_window_helper_or_fallback():
    block = _turn_completed_block()
    assert "_codex_context_window" in block, (
        "turn.completed must consult `_codex_context_window(model)` "
        "(with hasattr guard) before falling back to config / 1M.")
    assert "max_context_size" in block, (
        "turn.completed must fall back to the agent service's "
        "`max_context_size` when no per-model window helper exists.")
    assert "1_000_000" in block or "1000000" in block, (
        "turn.completed must have a hard 1M default so the threshold "
        "check never silently no-ops on a missing config.")


def test_threshold_check_still_present_and_uses_ctx_max_live():
    block = _turn_completed_block()
    # The actual gate: `_cthp > 0 and _ctx_max_live > 0 and
    # _ctx_used_live >= int(_ctx_max_live * _cthp / 100)`.
    pattern = re.compile(
        r"_cthp > 0\s+and\s+_ctx_max_live > 0\s+and\s+"
        r"_ctx_used_live >= int\(_ctx_max_live \* _cthp / 100\)")
    assert pattern.search(block), (
        "end-of-turn compact threshold gate is missing or no longer "
        "references _ctx_max_live")

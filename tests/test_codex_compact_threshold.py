"""Lock the codex end-of-turn compact threshold check against
regression: it MUST compare against the context window from provider
metadata when available, otherwise the required agent service
`max_context_size`, NOT against the `max_tokens` function parameter —
which is the OUTPUT response limit and is typically 0 ("no cap"),
silently disabling every subsequent compact.

The check lives at the `turn.completed` event handler in
`core/llm_providers/codex.py`. We verify by static parsing that:
  - `_ctx_max_live` is computed from `_codex_context_window`,
  - it is NOT assigned `= max_tokens` directly,
  - the turn.completed path has no silent hard fallback like 1_000_000.

This matches how the initial/per-tool live-gauge bumps compute the
value, ensuring a single source of truth.
"""

import re
from pathlib import Path

import pytest

from core.llm_client import LLMClientError
from core.llm_providers.codex import LLMCodexMixin

_CODEX_SRC = Path("core/llm_providers/codex.py").read_text(encoding="utf-8")


def test_codex_context_window_uses_runtime_metadata_first():
    provider = LLMCodexMixin()
    provider._config_ref = {"max_context_size": 400_000}
    provider._codex_context_windows = {"gpt-5.5": 1_048_576}

    assert provider._codex_context_window("gpt-5.5") == 1_048_576


def test_codex_context_window_uses_required_service_config():
    provider = LLMCodexMixin()
    provider._config_ref = {"max_context_size": "400000"}

    assert provider._codex_context_window("gpt-5.5") == 400_000


def test_codex_context_window_fails_without_service_config():
    provider = LLMCodexMixin()
    provider._config_ref = {}

    with pytest.raises(LLMClientError, match="max_context_size"):
        provider._codex_context_window("gpt-5.5")


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
        "Use _codex_context_window, which fails loud when max_context_size "
        "is missing.")


def test_turn_completed_uses_context_window_helper_without_hard_fallback():
    block = _turn_completed_block()
    assert "_ctx_max_live = self._codex_context_window(model)" in block, (
        "turn.completed must use `_codex_context_window(model)` as the "
        "single source of truth for the context window.")
    assert "1_000_000" not in block and "1000000" not in block, (
        "turn.completed must not silently fall back to a hard-coded context "
        "window; missing max_context_size is a service config error.")


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

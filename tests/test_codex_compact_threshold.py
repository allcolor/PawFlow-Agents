"""Lock CLI context-window compact threshold checks against regressions."""

from pathlib import Path

import pytest

from core.llm_client import LLMClientError
from core.llm_providers.gemini import LLMGeminiMixin

_GEMINI_SRC = Path("core/llm_providers/gemini.py").read_text(encoding="utf-8")


def test_gemini_context_window_uses_runtime_metadata_first():
    provider = LLMGeminiMixin()
    provider._config_ref = {"max_context_size": 400_000}
    provider._gemini_context_windows = {"gemini-3-pro": 1_048_576}

    assert provider._gemini_context_window("gemini-3-pro") == 1_048_576


def test_gemini_context_window_uses_required_service_config():
    provider = LLMGeminiMixin()
    provider._config_ref = {"max_context_size": "400000"}

    assert provider._gemini_context_window("gemini-3-pro") == 400_000


def test_gemini_context_window_fails_without_service_config():
    provider = LLMGeminiMixin()
    provider._config_ref = {}

    with pytest.raises(LLMClientError, match="max_context_size"):
        provider._gemini_context_window("gemini-3-pro")


def _gemini_tool_result_block() -> str:
    start = _GEMINI_SRC.index('if etype == "tool_result":')
    end = _GEMINI_SRC.index('if etype == "result":', start)
    return _GEMINI_SRC[start:end]


def _gemini_result_block() -> str:
    start = _GEMINI_SRC.index('if etype == "result":')
    end = _GEMINI_SRC.index('if etype == "error":', start)
    return _GEMINI_SRC[start:end]


def test_gemini_tool_result_uses_context_window_helper_without_hard_fallback():
    block = _gemini_tool_result_block()
    assert "self._gemini_context_window(model)" in block, (
        "gemini tool_result must use _gemini_context_window(model) as "
        "the single source of truth for the live gauge and compact gate")
    assert "1_000_000" not in block and "1000000" not in block, (
        "gemini tool_result must not silently fall back to a hard-coded "
        "context window; missing max_context_size is a service config error")


def test_gemini_result_uses_context_window_helper_without_hard_fallback():
    block = _gemini_result_block()
    assert "_ctx_max_live = self._gemini_context_window(model)" in block, (
        "gemini result handler must use _gemini_context_window(model) as "
        "the single source of truth for the context window")
    assert "1_000_000" not in block and "1000000" not in block, (
        "gemini result handler must not silently fall back to a hard-coded "
        "context window; missing max_context_size is a service config error")

"""Lock Gemini context-window and ACP gauge regressions."""

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


def test_gemini_acp_result_uses_actual_prompt_token_estimate_without_hard_fallback():
    assert "count_messages_tokens" in _GEMINI_SRC
    assert "return _count_msgs([{" in _GEMINI_SRC
    assert "prompt_mode = \"resume\" if session_id else \"cold\"" in _GEMINI_SRC
    response_start = _GEMINI_SRC.index("return LLMResponse(")
    response_block = _GEMINI_SRC[response_start:response_start + 500]
    assert "tokens_in=max(0, int(prompt_tokens or 0))" in response_block
    assert "1_000_000" not in response_block and "1000000" not in response_block


def test_gemini_acp_mcp_uses_absolute_python():
    assert '"command": "/usr/bin/python3"' in _GEMINI_SRC
    assert '"/opt/pawflow/mcp_bridge.py"' in _GEMINI_SRC

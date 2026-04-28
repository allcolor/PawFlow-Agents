"""Lock mid-turn compact threshold handling and Gemini parity."""

from pathlib import Path

_GEMINI = Path("core/llm_providers/gemini.py").read_text(encoding="utf-8")
_AGENT_CORE = Path("tasks/ai/agent_core.py").read_text(encoding="utf-8")


def test_ccd_handler_propagates_trigger_fraction():
    """agent_core.CCCompactDetected handler must pass trigger_fraction."""
    h_start = _AGENT_CORE.index("PawFlow compact: %d → %d messages")
    handler = _AGENT_CORE[max(0, h_start - 4000):h_start]
    assert "trigger_fraction=_ccd_trigger_frac" in handler, (
        "CCCompactDetected handler must pass the per-service trigger_fraction "
        "to _compact()")
    assert "compact_threshold_pct" in handler, (
        "CCCompactDetected handler must read compact_threshold_pct from the "
        "agent client config")


def test_gemini_bumps_prompt_tokens_per_tool_result():
    """Gemini must update prompt_tokens at every tool_result event."""
    block_start = _GEMINI.index('if etype == "tool_result":')
    block_end = _GEMINI.index('if etype == "result":', block_start)
    block = _GEMINI[block_start:block_end]
    assert "prompt_tokens += _ct(" in block
    assert "_ctx_max_mid = self._gemini_context_window(model)" in block
    assert "compact_threshold_pct" in block
    assert "prompt_tokens >= int(" in block
    assert "_ctx_max_mid * _cthp_mid / 100" in block


def test_gemini_mid_turn_compact_kills_gemini_to_break_loop():
    block_start = _GEMINI.index('if etype == "tool_result":')
    block_end = _GEMINI.index('if etype == "result":', block_start)
    block = _GEMINI[block_start:block_end]
    assert "_compact_pending[0] = True" in block
    assert "_kill_gemini_hard(proc)" in block
    assert "break" in block

"""Lock compact threshold handling and Gemini ACP parity."""

from pathlib import Path

_GEMINI = Path("core/llm_providers/gemini.py").read_text(encoding="utf-8")
_AGENT_CORE = Path("tasks/ai/agent_core.py").read_text(encoding="utf-8")


def test_ccd_handler_propagates_trigger_fraction():
    """agent_core.CCCompactDetected handler must pass trigger_fraction."""
    h_start = _AGENT_CORE.index("PawFlow compact:")
    handler = _AGENT_CORE[max(0, h_start - 4000):h_start]
    assert "trigger_fraction=_ccd_trigger_frac" in handler, (
        "CCCompactDetected handler must pass the per-service trigger_fraction "
        "to _compact()")
    assert "compact_threshold_pct" in handler, (
        "CCCompactDetected handler must read compact_threshold_pct from the "
        "agent client config")


def test_gemini_acp_uses_full_context_for_gauge():
    """Gemini ACP must count full PawFlow context, not only resume deltas."""
    assert "count_messages_tokens" in _GEMINI
    assert "prompt_tokens = _count_msgs(" in _GEMINI
    assert "tokens_in=max(0, int(prompt_tokens or 0))" in _GEMINI
    assert "[gemini-acp] gauge: prompt_tokens=%d" in _GEMINI


def test_gemini_acp_preempt_uses_session_cancel_not_hard_kill():
    """ACP cancellation is a protocol notification, not a process kill path."""
    send_start = _GEMINI.index("def _gemini_send_user_message")
    send_end = _GEMINI.index("def cancel_gemini", send_start)
    block = _GEMINI[send_start:send_end]
    assert '"session/cancel"' in block
    assert "_kill_gemini_hard" not in block

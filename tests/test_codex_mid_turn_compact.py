"""Lock compact threshold handling and Gemini ACP parity."""

from pathlib import Path

_GEMINI = Path("core/llm_providers/gemini.py").read_text(encoding="utf-8")
_AGENT_CORE = Path("tasks/ai/agent_core.py").read_text(encoding="utf-8")
_AGENT_COMPACTION = Path("tasks/ai/agent_compaction.py").read_text(
    encoding="utf-8")


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


def test_gemini_acp_gauge_counts_actual_prompt_payload():
    """Gemini ACP resume must count/send the resume delta, not full history."""
    assert "count_messages_tokens" in _GEMINI
    assert "return _count_msgs([{" in _GEMINI
    assert "prompt_mode = \"resume\" if session_id else \"cold\"" in _GEMINI
    assert "mode=%s" in _GEMINI
    assert "tokens_in=max(0, int(prompt_tokens or 0))" in _GEMINI


def test_gemini_acp_preempt_is_live_prompt_reuse_not_reloop():
    """ACP preempt must stay inside the active live session."""
    send_start = _GEMINI.index("def _gemini_send_user_message")
    send_end = _GEMINI.index("def cancel_gemini", send_start)
    block = _GEMINI[send_start:send_end]
    assert '"session/cancel"' in block
    assert '"session/prompt"' in block
    assert "preempt_req_id" in block
    assert "return True" in block
    assert "_kill_gemini_hard" not in block


def test_gemini_system_prompt_prefers_pawflow_mcp_over_builtins():
    ctx_src = Path("tasks/ai/agent_context.py").read_text(encoding="utf-8")
    assert "GEMINI ACP TOOL ROUTING" in ctx_src
    assert "Do not use" in ctx_src and "Gemini built-in" in ctx_src
    assert "mcp_pawflow_get_tool_schema" in ctx_src
    assert "mcp_pawflow_use_tool" in ctx_src
    assert "PawFlow MCP virtual" in ctx_src


def test_agent_core_rechecks_compact_threshold_after_context_injections():
    """Identity/date metadata can push the final prompt above the service
    compact threshold; the pre-send guard must compact before the LLM call."""
    assert "pre-send threshold crossed after injections" in _AGENT_CORE
    guard_start = _AGENT_CORE.index("# Force-fit guard")
    call_start = _AGENT_CORE.index("# LLM call", guard_start)
    guard = _AGENT_CORE[guard_start:call_start]
    assert "_trigger_frac > 0" in guard
    assert "_pre_send_est > _trigger_tokens" in guard
    assert "self._compact(" in guard
    assert "force=True" in guard


def test_force_fit_notifies_ui_as_compaction():
    """A hard pre-send force-fit shrinks the prompt, so the UI must be told
    to accept the next context gauge decrease."""
    assert '"reason": "force_fit"' in _AGENT_CORE
    assert '"compact_progress"' in _AGENT_CORE


def test_auto_compact_threshold_is_enforced_after_visible_appends():
    """Streaming CLI providers can grow context mid-turn; threshold compact
    must run after tool/message appends, not only before the next LLM call."""
    assert "def _maybe_auto_compact_after_append" in _AGENT_CORE
    assert "auto threshold crossed after" in _AGENT_CORE
    assert "_maybe_auto_compact_after_append(msg, msg.role)" in _AGENT_CORE
    assert "msg.role == \"assistant\" and msg.tool_calls and not msg.content" in _AGENT_CORE
    helper = _AGENT_CORE[
        _AGENT_CORE.index("def _maybe_auto_compact_after_append"):
        _AGENT_CORE.index("def _append")
    ]
    assert "trigger_fraction=trigger_fraction" in helper
    assert "force=True" not in helper


def test_proactive_compact_replaces_active_messages_for_cli_providers():
    """Codex/Gemini pre-send compaction must update the live message list.

    Otherwise the LLM receives the compacted context, but the next visible
    assistant append sees the old over-threshold list and immediately runs a
    duplicate compact in the same turn.
    """
    cli_block = _AGENT_CORE[
        _AGENT_CORE.index("# codex / gemini / etc."):
        _AGENT_CORE.index("# Pre-injection char count")
    ]
    assert "compacted_messages = self._compact(" in cli_block
    assert "messages[:] = compacted_messages" in cli_block
    assert "ctx.pop(\"_auto_compact_usage_cache\", None)" in cli_block
    assert "llm_context = list(messages)" in cli_block


def test_manual_compact_done_publishes_context_usage():
    """Manual compact completion has no provider message_meta; it must
    publish and persist the new gauge itself."""
    done_block = _AGENT_COMPACTION[
        _AGENT_COMPACTION.index('"stage": "done"'):
        _AGENT_COMPACTION.index('"conv_total_messages"') + 400]
    assert '"context_used"' in done_block
    assert '"context_max"' in done_block
    assert '"context_pct"' in done_block
    assert 'set_extra(\n                                conversation_id, "context_usage"' in _AGENT_COMPACTION

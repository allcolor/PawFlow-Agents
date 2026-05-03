"""Lock compact threshold handling and Gemini ACP parity."""

from pathlib import Path

_GEMINI = Path("core/llm_providers/gemini.py").read_text(encoding="utf-8")
_AGENT_CORE = Path("tasks/ai/agent_core.py").read_text(encoding="utf-8")
_AGENT_ACTIONS = Path("tasks/ai/agent_actions.py").read_text(encoding="utf-8")
_AGENT_COMPACTION = Path("tasks/ai/agent_compaction.py").read_text(
    encoding="utf-8")
_CONTEXT_OPS = Path("tasks/ai/actions/context_ops.py").read_text(
    encoding="utf-8")
_AGENT_RESOURCE = Path("tasks/ai/actions/agent_resource.py").read_text(
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
    from core.agent_prompt_policy import CLI_MCP_SYSTEM_PROMPT
    from core.llm_providers.gemini import LLMGeminiMixin

    assert LLMGeminiMixin._GEMINI_PAWFLOW_PREAMBLE == CLI_MCP_SYSTEM_PROMPT
    assert "Do not call native/internal provider tools" in CLI_MCP_SYSTEM_PROMPT
    assert "list schemas first" in CLI_MCP_SYSTEM_PROMPT
    assert "use_tool" in CLI_MCP_SYSTEM_PROMPT
    assert "`/workspace`" in CLI_MCP_SYSTEM_PROMPT


def test_agent_core_rechecks_compact_threshold_after_context_injections():
    """Identity/date metadata can push the final prompt above the service
    compact threshold; the pre-send guard must compact before the LLM call."""
    assert "pre-send threshold crossed after injections" in _AGENT_CORE
    guard_start = _AGENT_CORE.index("# Force-fit guard")
    call_start = _AGENT_CORE.index("# LLM call", guard_start)
    guard = _AGENT_CORE[guard_start:call_start]
    assert "_trigger_frac > 0" in guard
    assert "_pre_send_est >= _trigger_tokens" in guard
    assert "self._compact(" in guard
    assert "force=True" in guard


def test_api_pre_send_compact_replaces_active_messages_not_provider_view():
    """API providers have no CLI session boundary to recover from.

    The post-injection guard must compact and persist the active agent context,
    then rebuild the provider-only prompt. Compacting only `llm_context` protects
    a single API call but leaves the live/store context oversized.
    """
    guard_start = _AGENT_CORE.index("# Force-fit guard")
    call_start = _AGENT_CORE.index("# LLM call", guard_start)
    guard = _AGENT_CORE[guard_start:call_start]
    assert "compacted_messages = self._compact(" in guard
    assert "copy.deepcopy(messages)" in guard
    assert "copy.deepcopy(llm_context)" not in guard
    assert "messages[:] = compacted_messages" in guard
    assert "llm_context, _pre_inject_chars = _build_provider_context(" in guard


def test_manual_compact_refreshes_active_context_without_cancelling_loop():
    """Manual /compact during an API turn must not force-stop that turn."""
    op_start = _AGENT_ACTIONS.index("def _run_bg_context_op")
    op_end = _AGENT_ACTIONS.index("# ═════════════════", op_start)
    block = _AGENT_ACTIONS[op_start:op_end]
    assert 'if op_name != "compact":' in block
    assert "self.cancel_agent(conv_id, agent_name=agent_name, silent=True)" in block
    assert "def _refresh_active_context_from_store" in block
    assert "active_msgs[:] = refreshed" in block
    assert "_context_usage_cache" in block


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
    assert "force=True" in helper


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


def test_compact_budget_uses_active_service_config_not_summarizer():
    """The summarizer writes the summary, but active LLM service config owns
    compact_target_tokens, token_multiplier, and the post-compact gauge.
    """
    assert "budget_config: dict | None = None" in _AGENT_COMPACTION
    assert "_budget_cfg = (budget_config" in _AGENT_COMPACTION
    assert "_abs_cap = int(_budget_cfg.get(\"compact_target_tokens\", 0) or 0)" in _AGENT_COMPACTION
    assert "resolve_token_multiplier(_budget_cfg)" in _AGENT_COMPACTION


def test_codex_forced_compact_passes_active_budget_config():
    """Codex compact_boundary uses the summarizer client, but must pass the
    codex appserver service config so compact_target_tokens=50k is honored.
    """
    h_start = _AGENT_CORE.index("PawFlow compact:")
    handler = _AGENT_CORE[max(0, h_start - 2500):h_start]
    assert "_full_messages, _sc" in handler
    assert "budget_config=getattr(ctx.get(\"resolved_svc\"), \"config\", None)" in handler


def test_manual_compact_uses_selected_agent_llm_service_budget():
    """The /compact path must use the selected agent's llm_service config,
    not the summarizer service, for max_context_size and compact_target_tokens.
    """
    assert "def _ctx_llm_service_config" in _CONTEXT_OPS
    assert "get_agent_config(conv_id, _name).get(\"llm_service\")" in _CONTEXT_OPS
    assert "_compact_budget_config = _ctx_llm_service_config(conv_id, _ctx_agent)" in _CONTEXT_OPS
    assert "_compact_max = _ctx_max_tokens(conv_id, _ctx_agent)" in _CONTEXT_OPS
    assert "budget_config=_compact_budget_config" in _CONTEXT_OPS
    assert "effective_context_window(" in _CONTEXT_OPS


def test_list_resources_does_not_resolve_services_on_ui_refresh():
    """Resource polling must stay cheap; context_usage is repaired when written,
    not by resolving live LLM services during list_resources.
    """
    block = _AGENT_RESOURCE[
        _AGENT_RESOURCE.index('if action == "list_resources":'):
        _AGENT_RESOURCE.index('if action == "get_resource_detail":')]
    assert "def _stored_context_usage" in block
    assert "reg.resolve(llm_service" not in block
    assert "resolve_definition(\n                                llm_service" not in block
    assert "effective_context_window(" not in block
    assert "get_client()" not in block

"""Lock compact threshold handling and Gemini ACP parity."""

from pathlib import Path

_GEMINI = Path("core/llm_providers/gemini.py").read_text(encoding="utf-8")
_AGENT_CORE = Path("tasks/ai/agent_core.py").read_text(encoding="utf-8")
_AGENT_CONTEXT = Path("tasks/ai/agent_context.py").read_text(encoding="utf-8")
_AGENT_EMITTER = Path("tasks/ai/agent_emitter.py").read_text(encoding="utf-8")
_AGENT_ACTIONS = Path("tasks/ai/agent_actions.py").read_text(encoding="utf-8")
_AGENT_POLLER = Path("tasks/ai/agent_poller.py").read_text(encoding="utf-8")
_CODEX_APP = Path("core/llm_providers/codex_app_server.py").read_text(encoding="utf-8")
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


def test_stateful_cli_resume_skips_pawflow_context_load():
    """Codex/Gemini/CCI resume sends only the live delta.

    Loading hundreds of persisted messages before immediately reducing the
    provider payload to the latest user message adds seconds to cold resume.
    The skip must key off the generic CLI session flag, not Claude-only state.
    """
    block = _AGENT_CONTEXT[
        _AGENT_CONTEXT.index("elif use_conv_store and conversation_id:"):
        _AGENT_CONTEXT.index("elif conv_attr:")]
    assert "if _cli_has_session:" in block
    assert "CLI session active — skipping context load" in block
    assert "if _claude_has_session:" not in block


def test_stateful_cli_resume_skips_provider_prompt_decoration():
    """Active CLI sessions already hold provider prompt state.

    Resume turns must not rebuild expensive provider-only prompt decorations;
    agent_core also skips injecting that prompt when _cli_has_session is true.
    """
    assert "and not _cli_has_session" in _AGENT_CONTEXT[
        _AGENT_CONTEXT.index("# Inject {agent_name}.md project instructions"):
        _AGENT_CONTEXT.index("# NOTE: the fully-built system_prompt")]
    start = _AGENT_CONTEXT.index(
        "if _cli_has_session:\n            logger.info(\n"
        "                \"[context:%s] CLI session active")
    stop = _AGENT_CONTEXT.index(
        "else:\n            # Inject persistent memory digest", start)
    fast_path = _AGENT_CONTEXT[start:stop]
    assert "build_memory_digest" not in fast_path
    assert "build_diary_digest" not in fast_path
    assert "build_kg_digest" not in fast_path
    assert "build_project_graph_digest" not in fast_path


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
    completion_block = _GEMINI[
        _GEMINI.index("if _preempt_prompt_active:"):
        _GEMINI.index("if stop_reason not in", _GEMINI.index("if _preempt_prompt_active:"))]
    assert 'pstatus in ("done", "pending")' in completion_block
    assert '"unknown"' not in completion_block


def test_gemini_system_prompt_prefers_pawflow_mcp_over_builtins():
    from core.agent_prompt_policy import CLI_MCP_SYSTEM_PROMPT
    from core.llm_providers.gemini import LLMGeminiMixin

    assert LLMGeminiMixin._GEMINI_PAWFLOW_PREAMBLE == CLI_MCP_SYSTEM_PROMPT
    assert "Native/internal provider tools are forbidden" in CLI_MCP_SYSTEM_PROMPT
    assert "hidden native edits are an audit failure" in CLI_MCP_SYSTEM_PROMPT
    assert "list schemas first" in CLI_MCP_SYSTEM_PROMPT
    assert "use_tool" in CLI_MCP_SYSTEM_PROMPT
    assert "`/workspace`" in CLI_MCP_SYSTEM_PROMPT


def test_agent_core_rechecks_compact_threshold_after_context_injections():
    """Identity/date metadata can push the final prompt above the service
    compact threshold; the pre-send guard must compact before the LLM call."""
    guard_start = _AGENT_CORE.index("# Force-fit guard")
    call_start = _AGENT_CORE.index("# LLM call", guard_start)
    guard = _AGENT_CORE[guard_start:call_start]
    assert "_trigger_frac > 0" in guard
    assert "_threshold_used = _pre_send_est" in guard
    assert "_threshold_used >= _trigger_tokens" in guard
    assert "self._compact(" in guard
    assert "force=True" in guard
    assert "llm_context, _pre_inject_chars = _build_provider_context(messages)" in guard


def test_pre_send_threshold_uses_actual_prompt_not_live_gauge():
    """CLI resume can have a large persisted context while sending one delta."""
    guard_start = _AGENT_CORE.index("# Force-fit guard")
    call_start = _AGENT_CORE.index("# LLM call", guard_start)
    guard = _AGENT_CORE[guard_start:call_start]
    assert "_threshold_used = _pre_send_est" in guard
    assert "_auto_compact_usage(" not in guard


def test_proactive_compact_threshold_uses_current_messages():
    helper = _AGENT_CORE[
        _AGENT_CORE.index("def _threshold_estimate"):
        _AGENT_CORE.index("def _messages_changed")]
    assert "_with_provider_system_prompt(list(stored_msgs or []))" in helper
    assert "compute_context_usage" not in helper


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
    assert "_adopt_compacted_context(" in guard
    assert "reason=\"pre_send\"" in guard
    assert "llm_context, _pre_inject_chars = _build_provider_context(" in guard


def test_manual_compact_stops_active_loop_before_replacing_context():
    """Every compact stops the active loop before replacing context."""
    op_start = _AGENT_ACTIONS.index("def _run_bg_context_op")
    op_end = _AGENT_ACTIONS.index("# ═════════════════", op_start)
    block = _AGENT_ACTIONS[op_start:op_end]
    bg_start = block.index("def _bg():")
    bg_block = block[bg_start:block.index("if not self._acquire_context_op", bg_start)]
    assert 'if op_name != "compact":' not in bg_block
    assert "self.cancel_agent(conv_id, agent_name=agent_name, silent=True)" in bg_block
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


def test_post_append_cli_compact_uses_provider_restart_path():
    """Live CLI providers must not be killed from inside stream callbacks.

    Crossing the PawFlow threshold mid-turn should raise the same compact signal
    as provider-native compaction, so agent_core restarts the CLI session and the
    provider can keep the fresh compacted instance live after the turn.
    """
    helper = _AGENT_CORE[
        _AGENT_CORE.index("def _maybe_auto_compact_after_append"):
        _AGENT_CORE.index("def _append")
    ]
    cli_guard = helper[helper.index('if _client_provider in ('):
                       helper.index('compact_owner =')]
    except_block = helper[helper.index("except CCCompactDetected:"):
                          helper.index("except Exception as compact_err:")]
    assert '"claude-code"' in cli_guard
    assert '"codex-app-server"' in cli_guard
    assert '"gemini"' in cli_guard
    assert "raise CCCompactDetected(" in cli_guard
    assert "self._compact(" not in cli_guard
    assert "raise" in except_block
    assert "auto compact after" not in except_block


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
    assert "_adopt_compacted_context(" in cli_block
    assert "reason=\"proactive\"" in cli_block
    assert "llm_context = list(messages)" in cli_block


def test_manual_compact_done_does_not_publish_context_gauge_event():
    """Manual compact may persist usage, but live gauge updates come from
    the dedicated context_usage/message_meta paths, not compact_progress.
    """
    done_block = _AGENT_COMPACTION[
        _AGENT_COMPACTION.index('"stage": "done"'):
        _AGENT_COMPACTION.index('"conv_total_messages"') + 400]
    assert '"context_used"' not in done_block
    assert '"context_max"' not in done_block
    assert '"context_pct"' not in done_block
    assert 'context_usage_entry(' not in _AGENT_COMPACTION
    assert 'set_extra(\n                                conversation_id, "context_usage"' not in _AGENT_COMPACTION


def test_compact_done_event_exposes_target_tokens():
    """Progress UI compares post-compact usage to the actual final cap,
    not the summarizer's smaller per-bucket summary target.
    """
    done_block = _AGENT_COMPACTION[
        _AGENT_COMPACTION.index('"stage": "done"'):
        _AGENT_COMPACTION.index('"conv_total_messages"') + 400]
    assert '"target_tokens": cap' in done_block


def test_codex_context_session_skip_requires_live_session_or_rollout():
    block = _AGENT_CONTEXT[
        _AGENT_CONTEXT.index('elif _is_codex_app_server:'):
        _AGENT_CONTEXT.index('# Resolve max_context early')]
    assert "CodexLiveRegistry" in block
    assert "_codex_app_rollout_path" in block
    assert 'stale codex app-server thread' in block
    assert '_cli_has_session = False' in block


def test_codex_context_compaction_clears_thread_before_pawflow_compact():
    block = _CODEX_APP[
        _CODEX_APP.index('def _hard_kill_for_context_compaction'):
        _CODEX_APP.index('try:', _CODEX_APP.index('lock = self._codex_app_ensure_lock'))]
    assert 'codex_app_server_thread:' in block
    assert 'codex_app_pool_idx:' in block
    assert 'store.set_extra' in block


def test_compact_resume_wake_is_provider_agnostic():
    assert 'reason=f"[compact_resume:{_resume_agent}]' in _AGENT_ACTIONS
    assert "AgentLoopTask.wake_agent" in _AGENT_ACTIONS
    assert "even_if_active=True" in _AGENT_ACTIONS
    assert "provider turn will restart immediately" in _AGENT_CORE


def test_compact_resume_poll_prompt_continues_without_new_user_message():
    assert "compact_resume" in _AGENT_POLLER
    assert "Context compaction completed" in _AGENT_POLLER
    assert "Do not wait for a " in _AGENT_POLLER
    assert "new user message" in _AGENT_POLLER


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


def test_codex_forced_compact_adopts_persisted_agent_context_before_restart():
    """Provider compact must replace active PawFlow context before restart."""
    marker = "provider compact detected"
    h_start = _AGENT_CORE.rindex("except CCCompactDetected:", 0,
                                 _AGENT_CORE.index(marker))
    h_end = _AGENT_CORE.index("PawFlow compact done", h_start)
    handler = _AGENT_CORE[h_start:h_end]
    assert "\n                            messages = list(self._compact(" not in handler
    assert "_compacted_messages = list(self._compact(" in handler
    assert "_adopt_compacted_context(" in handler
    assert "reason=\"provider_compact\"" in handler
    assert "async_cleanup=True" in handler
    assert "already_persisted=True" in handler
    assert "post_hooks_async=True" in handler
    assert "llm_context = list(messages)" in handler
    assert handler.index("emitter.check_cancelled()") < handler.index("_adopt_compacted_context(")
    assert handler.index("_adopt_compacted_context(") < handler.index("llm_context = list(messages)")


def test_compact_adoption_skips_duplicate_save_when_already_persisted():
    """_compact() already persisted its output; restart adoption must not
    repeat that write on the foreground path.
    """
    helper = _AGENT_CORE[
        _AGENT_CORE.index("def _adopt_compacted_context"):
        _AGENT_CORE.index("# Claude-code: CC session")]
    assert "already_persisted: bool = False" in helper
    assert "if not already_persisted:" in helper
    assert helper.index("if not already_persisted:") < helper.index(
        "save_agent_context(")
    assert "already_persisted=True" in _AGENT_CORE


def test_provider_compact_post_hooks_do_not_block_restart():
    """Provider compact publishes done before post hooks; restart must not wait
    for memory/bucket hook side effects after the UI already says compact done.
    """
    assert "post_hooks_async: bool = False" in _AGENT_COMPACTION
    assert "threading.Thread(" in _AGENT_COMPACTION
    assert "_hook_runner.run(\"post_compact\", _post_ctx)" in _AGENT_COMPACTION
    assert "post_hooks_async=True" in _AGENT_CORE


def test_provider_compact_restart_path_has_correlated_timing_logs():
    """The post-compact restart path must be diagnosable from server logs."""
    for marker in (
            "[compact-restart:%s/%s] writer flush done",
            "[compact-restart:%s/%s] context loaded",
            "[compact-restart:%s/%s] compact returned",
            "[compact-restart:%s/%s] cancellation gate passed",
            "[compact-restart:%s/%s] adopted compacted context",
            "[compact-restart:%s/%s] pending dedupe done",
            "[compact-restart:%s/%s] post-compact foreground release",
            "[compact-restart:%s/%s] post-compact gauge refresh done"):
        assert marker in _AGENT_CORE
    assert "elapsed_ms=%.1f" in _AGENT_CORE
    assert "[compact] post hooks scheduled async" in _AGENT_COMPACTION
    assert "[compact] post hooks start" in _AGENT_COMPACTION
    assert "[compact] post hooks done" in _AGENT_COMPACTION


def test_provider_compact_gauge_refresh_uses_compacted_messages_in_memory():
    """Post-compact gauge refresh must not reload the just-persisted context."""
    start = _AGENT_CORE.index("PawFlow compact done, provider turn will restart immediately")
    end = _AGENT_CORE.index("except Exception as compact_err", start)
    block = _AGENT_CORE[start:end]
    assert "context_usage_for_messages" in block
    assert "_compacted_messages" in block
    assert "compute_context_usage" not in block
    assert "target=_persist_post_compact_usage" in block


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


def test_list_resources_does_not_transport_or_resolve_context_usage():
    """Resource polling must stay cheap and must not hydrate the live gauge."""
    block = _AGENT_RESOURCE[
        _AGENT_RESOURCE.index('if action == "list_resources":'):
        _AGENT_RESOURCE.index('if action == "get_resource_detail":')]
    assert "context_usage" not in block
    assert "reg.resolve(llm_service" not in block
    assert "resolve_definition(\n                                llm_service" not in block
    assert "effective_context_window(" not in block
    assert "get_client()" not in block


def test_compact_threshold_accepts_fractional_ui_values():
    """LLM API services may store compact_threshold_pct as 0.8, not 80."""
    helper = _AGENT_CORE[
        _AGENT_CORE.index("def _compact_threshold_fraction"):
        _AGENT_CORE.index("def _agent_compact_threshold_fraction")]
    assert "if value < 1:" in helper
    assert "return value" in helper
    assert "return value / 100.0" in helper
    assert "_compact_threshold_fraction(" in _AGENT_CORE


def test_streaming_api_live_context_usage_is_not_published_by_done():
    """HTTP/API providers update gauge while active; done only closes the turn."""
    assert "def _context_usage_payload" in _AGENT_EMITTER
    assert "def _publish_context_usage" in _AGENT_EMITTER
    stream_start = _AGENT_EMITTER.index("class StreamEmitter")
    iter_start = _AGENT_EMITTER.index("def on_iteration_start", stream_start)
    iter_end = _AGENT_EMITTER.index("def on_iteration_end", iter_start)
    iter_block = _AGENT_EMITTER[iter_start:iter_end]
    assert 'self._publish_context_usage("iteration_start")' in iter_block
    heartbeat_start = _AGENT_EMITTER.index("def start_heartbeat", stream_start)
    heartbeat_end = _AGENT_EMITTER.index("def stop_heartbeat", heartbeat_start)
    heartbeat_block = _AGENT_EMITTER[heartbeat_start:heartbeat_end]
    assert 'emitter._publish_context_usage("heartbeat")' in heartbeat_block
    assert 'reason == "heartbeat" and input_sig == self._context_usage_input_sig' in _AGENT_EMITTER
    assert 'name=f"ctx-gauge-persist-{self.event_cid[:8]}"' in _AGENT_EMITTER
    done_start = _AGENT_EMITTER.index("def on_done", stream_start)
    done_end = _AGENT_EMITTER.index("def on_error", done_start)
    done_block = _AGENT_EMITTER[done_start:done_end]
    assert 'self._publish_context_usage("done")' not in done_block
    assert '_context_usage_payload("done")' not in done_block

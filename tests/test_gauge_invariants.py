"""Lock the chat-UI context gauge invariants.

Real server gauge updates are authoritative and may move up or down. A
post-compact decrease must not be blocked by stale UI state. The frontend
must not estimate gauge values; older server polls are rejected by timestamp
when available.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

_ACTIVE_AGENTS_JS = Path(
    "tasks/io/chat_ui/active_agents.js").read_text(encoding="utf-8")
_SSE_JS = Path("tasks/io/chat_ui/sse.js").read_text(encoding="utf-8")
_RESOURCES_JS = Path("tasks/io/chat_ui/resources.js").read_text(encoding="utf-8")
_SERVICES_JS = Path("tasks/io/chat_ui/services.js").read_text(encoding="utf-8")
_DIALOGS_JS = Path("tasks/io/chat_ui/dialogs.js").read_text(encoding="utf-8")
_TERMINAL_JS = Path("tasks/io/chat_ui/terminal.js").read_text(encoding="utf-8")
_TABS_JS = Path("tasks/io/chat_ui/tabs.js").read_text(encoding="utf-8")
_FLOW_GRAPH_HTML = Path("tasks/io/chat_ui/flow_graph.html").read_text(encoding="utf-8")
_MESSAGES_JS = Path("tasks/io/chat_ui/messages.js").read_text(encoding="utf-8")
_AGENT_CONTEXT_PY = Path("tasks/ai/agent_context.py").read_text(encoding="utf-8")
_AGENT_CORE_PY = Path("tasks/ai/agent_core.py").read_text(encoding="utf-8")
_AGENT_COMPACTION_PY = Path("tasks/ai/agent_compaction.py").read_text(encoding="utf-8")
_CONTEXT_OPS_PY = Path("tasks/ai/actions/context_ops.py").read_text(encoding="utf-8")
_CONTEXT_EDITOR_JS = Path(
    "tasks/io/chat_ui/context_editor.js").read_text(encoding="utf-8")
_AGENT_ACTIONS_PY = Path("tasks/ai/agent_actions.py").read_text(encoding="utf-8")
_AGENT_POLLER_PY = Path("tasks/ai/agent_poller.py").read_text(encoding="utf-8")
_FILESYSTEM_SERVICE_PY = Path("services/filesystem_service.py").read_text(encoding="utf-8")


def test_set_context_usage_blocks_demote_to_zero():
    """setContextUsage must short-circuit when realUsed===0 and the
    cache already holds a non-zero value."""
    body = _extract_function_body(_ACTIVE_AGENTS_JS, "setContextUsage")
    assert "realUsed === 0" in body and "cachedUsed > 0" in body, (
        "Rule 1 (no demote-to-zero) is missing from setContextUsage")


def test_set_context_usage_accepts_real_decrease_and_rejects_stale_timestamp():
    """Real server values may decrease after compact; only stale dated
    payloads should be rejected."""
    body = _extract_function_body(_ACTIVE_AGENTS_JS, "setContextUsage")
    assert "incomingAt && cachedAt && incomingAt < cachedAt" in body
    assert "realUsed < cachedUsed" not in body


def test_compact_pending_is_consumed_but_not_required_for_decrease():
    """Compact events still clear their marker, but the marker must not be
    required for accepting real server decreases."""
    body = _extract_function_body(_ACTIVE_AGENTS_JS, "setContextUsage")
    assert "delete window._compactPending[key]" in body
    assert "!window._compactPending[key]" not in body


def test_compact_progress_done_marks_compact_pending():
    """The SSE `compact_progress stage=done` listener keeps the compact
    marker for UI bookkeeping while applying the authoritative gauge."""
    # Locate the stage==='done' branch and ensure it calls
    # markCompactJustHappened with the agent name.
    assert "markCompactJustHappened(agent)" in _SSE_JS, (
        "compact_progress 'done' must call markCompactJustHappened")


def test_compact_progress_start_finalizes_live_tool_calls():
    """Provider compact can arrive after tool_call but before tool_result.

    The UI must close pending technical rows immediately so BG/Kill controls
    are not left open for a generation that has already been interrupted.
    """
    assert "function _finalizeLiveToolCalls" in _SSE_JS
    start_block = _SSE_JS[
        _SSE_JS.index("if (data.stage === 'start')"):
        _SSE_JS.index("} else if (data.stage === 'chunking'", _SSE_JS.index("if (data.stage === 'start')"))]
    assert "_finalizeLiveToolCalls(data.agent || '', '[Interrupted by compact]')" in start_block
    helper = _SSE_JS[
        _SSE_JS.index("function _finalizeLiveToolCalls"):
        _SSE_JS.index("// Expose a reset hook", _SSE_JS.index("function _finalizeLiveToolCalls"))]
    assert "querySelectorAll('#messages .tc-bullet.pending')" in helper
    assert "tc-bg-btn, .tc-kl-btn" in helper
    assert "delete tcEl.dataset.live" in helper


def test_message_meta_syncs_error_class_on_existing_message():
    """message_meta patches must repaint existing DOM bubbles both ways."""
    block = _SSE_JS[
        _SSE_JS.index("eventSource.addEventListener('message_meta'"):
        _SSE_JS.index("eventSource.addEventListener('iteration_status'")]
    assert "hasOwnProperty.call(data, 'is_error')" in block
    assert "el.classList.toggle('error', !!data.is_error)" in block


def test_flow_graph_opens_reactflow_via_blob_to_avoid_frame_refusal():
    assert "_openFlowGraphTab(instanceId)" in _SERVICES_JS
    assert "window.__PAWFLOW_FLOW_INSTANCE_ID" in _SERVICES_JS
    assert "addBlobHtmlTab(instanceId, html)" in _SERVICES_JS
    assert "URL.createObjectURL(blob)" in _TABS_JS
    assert "URL.revokeObjectURL" in _TABS_JS
    assert "ReactFlow" in _FLOW_GRAPH_HTML
    assert "@xyflow/react" in _FLOW_GRAPH_HTML
    assert "window.__PAWFLOW_FLOW_INSTANCE_ID" in _FLOW_GRAPH_HTML
    assert "ReactFlow base CSS inlined" in _FLOW_GRAPH_HTML
    assert '<link rel="stylesheet" href="https://esm.sh/@xyflow/react@12.6.0/dist/style.css">' not in _FLOW_GRAPH_HTML
    assert "panOnDrag: true" in _FLOW_GRAPH_HTML
    assert "zoomOnScroll: true" in _FLOW_GRAPH_HTML


def test_existing_vscode_tab_refreshes_new_capability_url():
    block = _TABS_JS[
        _TABS_JS.index("function addVSCodeTab"):
        _TABS_JS.index("/** Close a VSCode tab.")]
    assert "existingPanel.querySelector('iframe')" in block
    assert "document.createElement('iframe')" in block
    assert "iframe.replaceWith(nextIframe)" in block
    assert "switchTab(tabId)" in block


def test_core_dialog_labels_use_i18n_catalogs():
    catalogs = [
        json.loads(Path("tasks/io/chat_ui/i18n/en.json").read_text(encoding="utf-8")),
        json.loads(Path("tasks/io/chat_ui/i18n/fr.json").read_text(encoding="utf-8")),
        json.loads(Path("tasks/io/chat_ui/i18n/es.json").read_text(encoding="utf-8")),
    ]
    keys = set(catalogs[0])
    assert all(set(cat) == keys for cat in catalogs)

    for key in [
        "flowEditParameters", "flowLoadingParameters", "flowNoParameters",
        "chooseRelay", "executionMode", "toolNoArguments", "execute",
    ]:
        assert all(key in cat for cat in catalogs)

    dialog_sources = _DIALOGS_JS + _SERVICES_JS + _TERMINAL_JS
    assert "Edit Flow Parameters" not in dialog_sources
    assert "Loading parameters..." not in dialog_sources
    assert "Choose relay" not in dialog_sources
    assert "Execution mode" not in dialog_sources


def test_chat_tool_display_unwraps_meta_use_tool_calls():
    assert "function _unwrapDisplayedToolCall" in _MESSAGES_JS
    assert "function _hasCompleteMcpDisplayedToolCall" in _MESSAGES_JS
    assert "mcp_pawflow_use_tool" in _MESSAGES_JS
    assert "mcp__pawflow__use_tool" in _MESSAGES_JS
    assert "mcp__pawflow__.use_tool" in _MESSAGES_JS
    assert "pawflow.use_tool" in _MESSAGES_JS
    assert "pawflow/use_tool" in _MESSAGES_JS
    assert "toolArgs.tool_name" in _MESSAGES_JS
    assert "toolArgs.parameters" in _MESSAGES_JS
    assert "payload.arguments || payload.parameters || {}" in _MESSAGES_JS
    assert "if (value === 'mcp' || value === 'native') return value;" in _MESSAGES_JS
    assert "_toolOriginValue(extra, (extra && (extra.tool_name || extra.tool)) || toolName)" in _MESSAGES_JS
    assert "if (!_hasCompleteMcpDisplayedToolCall(rawToolName, rawToolArgs)) return null;" in _MESSAGES_JS
    assert "!_hasCompleteMcpDisplayedToolCall(data.tool, data.arguments || {})" in _SSE_JS


def test_transcript_tool_events_are_not_filtered_by_cancelled_ui_state():
    """Received transcript events must render; msg_id dedup is the filter."""
    assert "_cancelledAgents" not in _SSE_JS
    tool_call = _SSE_JS[
        _SSE_JS.index("eventSource.addEventListener('tool_call'"):
        _SSE_JS.index("eventSource.addEventListener('tool_result'")]
    tool_result = _SSE_JS[
        _SSE_JS.index("eventSource.addEventListener('tool_result'"):
        _SSE_JS.index("eventSource.addEventListener('bg_task_update'")]
    assert "cancel" not in tool_call.lower()
    assert "cancel" not in tool_result.lower()


def test_live_tool_call_passes_arguments_to_renderer():
    tool_call = _SSE_JS[
        _SSE_JS.index("eventSource.addEventListener('tool_call'"):
        _SSE_JS.index("eventSource.addEventListener('tool_result'")]
    assert "arguments: data.arguments || {}" in tool_call
    assert "tool_args: data.arguments || {}" in tool_call


def test_incomplete_mcp_tool_calls_are_filtered_before_persist_and_display():
    core_src = Path("tasks/ai/agent_core.py").read_text(encoding="utf-8")
    serialization_src = Path("tasks/ai/agent_serialization.py").read_text(encoding="utf-8")
    client_src = Path("core/llm_client.py").read_text(encoding="utf-8")

    assert "def has_complete_mcp_tool_call" in client_src
    assert "if not has_complete_mcp_tool_call(_raw_name, _raw_args):" in core_src
    assert "return" in core_src[core_src.index("if not has_complete_mcp_tool_call(_raw_name, _raw_args):"):]
    assert "if not has_complete_mcp_tool_call(raw_name, raw_args):" in serialization_src
    assert "continue" in serialization_src[serialization_src.index("if not has_complete_mcp_tool_call(raw_name, raw_args):"):]


def test_iteration_status_updates_state_without_polluting_chat_timeline():
    start = _SSE_JS.index("eventSource.addEventListener('iteration_status'")
    end = _SSE_JS.index("eventSource.addEventListener('flowfile_in'", start)
    block = _SSE_JS[start:end]
    assert "updateActivePanel()" in block
    assert "document.getElementById('status').textContent" in block
    assert "addMsg(" not in block


def test_active_agent_tool_hints_use_task_scoped_keys():
    """Task tool SSE hints must update the server-poll row, not create a ghost."""
    key_body = _extract_function_body(_ACTIVE_AGENTS_JS, "activeAgentKey")
    assert "agentName + '::' + taskId" in key_body

    tool_body = _extract_function_body(_ACTIVE_AGENTS_JS, "trackAgentTool")
    assert "activeAgentKey(agentName, taskId || '')" in tool_body
    assert "trackAgentStart(agentName, '', taskId || '')" in tool_body

    sync_body = _extract_function_body(_ACTIVE_AGENTS_JS, "syncActiveFromServer")
    assert "activeAgentKey(a.agent_name, a.task_id || '')" in sync_body
    assert "agentKey(a.agent_name + '::' + a.task_id)" not in sync_body

    tool_call = _SSE_JS[
        _SSE_JS.index("eventSource.addEventListener('tool_call'"):
        _SSE_JS.index("eventSource.addEventListener('tool_result'")]
    tool_result = _SSE_JS[
        _SSE_JS.index("eventSource.addEventListener('tool_result'"):
        _SSE_JS.index("eventSource.addEventListener('bg_task_update'")]
    task_done = _SSE_JS[
        _SSE_JS.index("eventSource.addEventListener('done'"):
        _SSE_JS.index("// Single update path", _SSE_JS.index("eventSource.addEventListener('done'"))]
    assert "trackAgentTool(tcAgent, data.tool, data.task_id || '')" in tool_call
    assert "trackAgentToolDone(data.agent_name, data.tool, data.task_id || '')" in tool_result
    assert "trackAgentDone(doneAgent, data.task_id)" in task_done


def test_compact_progress_done_does_not_publish_gauge():
    """Gauge updates come from message_meta produced by compute_context_usage.
    compact_progress is progress UI only and must not carry a second formula."""
    done_branch = _SSE_JS[
        _SSE_JS.index("} else if (data.stage === 'done')"):
        _SSE_JS.index("} else if (data.stage === 'error')")]
    assert "setContextUsage(agent" not in done_branch
    assert "data.context_used" not in done_branch
    assert "data.context_max" not in done_branch


def test_context_shrinks_only_through_threshold_compact():
    assert "_clear_seen_tool_results(" not in _AGENT_CORE_PY
    assert "mid-turn compact" not in _AGENT_CORE_PY


def test_proactive_compact_only_runs_after_threshold_estimate():
    """A non-zero compact_threshold_pct is only a configured limit.

    It must not call _compact on every turn below that limit; otherwise the
    returned unchanged copy is mistaken for a compact and live CLI sessions are
    invalidated on normal turns.
    """
    assert "def _should_proactive_compact" in _AGENT_CORE_PY
    assert "used_tokens >= trigger_tokens" in _AGENT_CORE_PY
    proactive_region = _AGENT_CORE_PY[
        _AGENT_CORE_PY.index("if ctx.get(\"_is_claude_code\"):"):
        _AGENT_CORE_PY.index("# Pre-injection char count")]
    assert "if _trigger_frac > 0:" not in proactive_region
    assert "if _should_proactive_compact(messages, _max_ctx, _cpt):" in proactive_region


def test_cli_session_invalidation_requires_compacted_context_adoption():
    """CLI sessions are invalidated only when compacted context is adopted."""
    helper = _AGENT_CORE_PY[
        _AGENT_CORE_PY.index("def _adopt_compacted_context"):
        _AGENT_CORE_PY.index("# Claude-code: CC session")]
    assert "messages[:] = compacted_list" in helper
    assert "save_agent_context(" in helper
    assert "if ctx.get(\"_is_cli_provider\"):" in helper
    assert "invalidate_claude_session_for_agent(" in helper
    assert "ctx[\"_cli_has_session\"] = False" in helper


def test_no_direct_active_interactions_mutation_for_context():
    """Direct mutation of `activeInteractions[k].contextUsed` from
    sse.js bypasses the monotonic invariants — that path must go
    through setContextUsage instead. The only allowed assignment lives
    inside active_agents.js (the setContextUsage body itself)."""
    forbidden = re.findall(
        r"activeInteractions\[[^\]]+\]\.contextUsed\s*=", _SSE_JS)
    assert not forbidden, (
        f"Direct contextUsed mutation in sse.js bypasses gauge "
        f"invariants: {forbidden!r}")


def test_setcontextusage_mirrors_to_active_interactions():
    """setContextUsage must mirror its accepted value into
    `activeInteractions[key]` so the active-agents panel sees the same
    monotonic value as the header / Resource Panel."""
    body = _extract_function_body(_ACTIVE_AGENTS_JS, "setContextUsage")
    assert "activeInteractions[key].contextUsed = realUsed" in body, (
        "setContextUsage must mirror the accepted value into "
        "activeInteractions for the active-agents panel")


def test_resource_and_active_polling_do_not_update_context_gauge():
    """Polling endpoints must not touch the live gauge. Initial hydration uses
    list_context_usage; live updates use one server event: message_meta."""
    assert "setContextUsage(a.name" not in _RESOURCES_JS
    assert "setContextUsage(a.agent_name" not in _ACTIVE_AGENTS_JS
    assert "window._contextUsage[aKeyLc] =" not in _RESOURCES_JS
    active_poll = _ACTIVE_AGENTS_JS[_ACTIVE_AGENTS_JS.index("function syncActiveFromServer") :]
    setter_body = _extract_function_body(_ACTIVE_AGENTS_JS, "setContextUsage")
    outside_setter = active_poll.replace(setter_body, "")
    assert "window._contextUsage[" not in outside_setter


def test_active_panel_uses_context_cache_as_source_of_truth():
    """The active-agent row must render the shared context cache even when
    it is lower than a stale activeInteractions mirror after compact."""
    body = _extract_function_body(_ACTIVE_AGENTS_JS, "updateActivePanel")
    assert "(cached.used || 0) > ctxUsed" not in body
    assert "if (cached && cached.max)" in body
    assert "ctxUsed = cached.used || 0" in body


def test_list_active_does_not_surface_idle_live_only_rows():
    """Warm live CLI sessions are reusable telemetry, not active work.
    They must stay in the side-channel live lists and must not create
    Active Agents rows that render as fake "thinking" work."""
    src = Path("tasks/ai/actions/usage.py").read_text(encoding="utf-8")
    assert "def _ensure_live_rows" not in src
    assert "_ensure_live_rows(" not in src
    assert '"status": "live"' not in src
    assert "cc_live_list = _cc_entries" in src
    assert "codex_live_list = _cdx_entries" in src
    assert "gemini_live_list = _gem_entries" in src


def test_message_meta_context_used_comes_from_central_context_usage():
    src = Path("tasks/ai/agent_core.py").read_text(encoding="utf-8")
    agent_source = src[src.index("def _agent_source") : src.index("# SpawnAgentsHandler source tracking")]
    assert "compute_context_usage" in agent_source
    assert "source=\"pawflow_context\"" in agent_source
    assert "context_usage_from_cache" not in agent_source
    assert 'src["tokens_in"] = tok_in' in agent_source


def test_context_gauge_refreshes_on_every_append():
    """Every message appended to the agent's PawFlow context refreshes the
    gauge. The gauge is the size of that context, so it must move with each
    message — not only at iteration/turn boundaries. _append is the single
    point every provider routes through (the classic loop AND the
    claude-code/CCI turn_callback), so the refresh lives there and is
    identical for all providers. The recompute reuses the delta token cache.
    """
    src = Path("tasks/ai/agent_core.py").read_text(encoding="utf-8")
    append_body = src[src.index("def _append(msg: LLMMessage)") : src.index("# Repair orphan tool_calls")]
    assert "messages.append(msg)" in append_body
    assert 'emitter._publish_context_usage("append")' in append_body


def test_cli_tool_callbacks_do_not_compute_context_usage_on_hot_path():
    src = Path("tasks/ai/agent_core.py").read_text(encoding="utf-8")
    turn_callback = src[src.index("def _claude_code_turn_callback") : src.index("def _apply_queued_delegate_turn_mode")]
    live_block = src[src.index("def _cli_block_callback") : src.index("def _llm_call")]
    assert "_agent_source(include_context=False)" in turn_callback
    assert "compute_context_usage" not in turn_callback
    assert "_agent_source(include_context=False)" in live_block
    assert "compute_context_usage" not in live_block


def test_manual_compact_suspends_stale_live_gauge_until_refresh():
    actions_src = Path("tasks/ai/agent_actions.py").read_text(encoding="utf-8")
    emitter_src = Path("tasks/ai/agent_emitter.py").read_text(encoding="utf-8")
    assert "def _set_context_usage_suspended" in actions_src
    compact_block = actions_src[
        actions_src.index("def _bg()") : actions_src.index("thread = threading.Thread")
    ]
    assert "_set_context_usage_suspended(agent_name, True)" in compact_block
    assert "_refresh_active_context_from_store(_agent)" in compact_block
    assert "_set_context_usage_suspended(_agent, False)" in compact_block
    assert "_context_usage_suspended" in emitter_src
    assert "compute_context_usage" in emitter_src
    assert "usage_event_payload" in emitter_src


def test_frontend_context_pct_is_always_used_over_max():
    active_src = Path("tasks/io/chat_ui/active_agents.js").read_text(encoding="utf-8")
    setter = active_src[
        active_src.index("function setContextUsage") : active_src.index("function _refreshGaugeSurfaces")
    ]
    assert "const pct = newMax > 0 ? realUsed / newMax : 0;" in setter
    assert "usage.pct || usage.context_pct" not in setter


def test_frontend_context_gauge_has_no_client_estimator():
    assert "function bumpContextEstimate" not in _ACTIVE_AGENTS_JS
    assert "_contextEstChars" not in _ACTIVE_AGENTS_JS
    assert "estimated" not in _ACTIVE_AGENTS_JS
    assert "bumpContextEstimate(" not in _SSE_JS


def test_context_command_shared_view_exposes_display_role_not_raw_llm_role():
    context_src = Path("tasks/ai/actions/context_ops.py").read_text(encoding="utf-8")
    get_context = context_src[
        context_src.index('if action == "get_context":'):
        context_src.index('if action == "get_context_full":')]
    assert "display_role = role" in get_context
    assert "if stype == \"agent\":" in get_context
    assert "display_role = \"assistant\"" in get_context
    assert '"raw_role": role' in get_context
    assert '"role": display_role' in get_context


def test_context_command_exposes_full_agent_context_usage():
    context_src = Path("tasks/ai/actions/context_ops.py").read_text(encoding="utf-8")
    ui_src = Path("tasks/io/chat_ui/context_editor.js").read_text(encoding="utf-8")
    get_context = context_src[
        context_src.index('if action == "get_context":'):
        context_src.index('if action == "get_context_full":')]
    assert "compute_context_usage" not in get_context
    assert "load_agent_context_page" in get_context
    assert '"computed_from": "persisted_context_usage"' in context_src
    assert '"context_usage": _context_usage' in get_context
    assert "t('contextGauge') + ': '" in ui_src
    assert "used.toLocaleString() + ' / ' + max.toLocaleString()" in ui_src


def test_context_editor_edits_visible_rows_without_full_context_fetch():
    edit_body = _CONTEXT_EDITOR_JS[
        _CONTEXT_EDITOR_JS.index("async function ctxEditMessage"):
        _CONTEXT_EDITOR_JS.index("let _ctxDirty = false")]
    assert "_ctxVisibleById.get(msgId)" in edit_body
    assert "ctxLoadFull()" not in edit_body
    assert "contextMessageNotFoundRefresh" in edit_body


def test_context_editor_never_uses_full_context_loader():
    assert "action$('get_context_full'" not in _CONTEXT_EDITOR_JS
    assert "Full context loading is disabled" in _CONTEXT_OPS_PY
    assert "Full context replacement is disabled" in _CONTEXT_OPS_PY


def test_webchat_live_display_window_trims_only_autoscroll():
    assert "function trimLiveDisplayWindowIfAutoscrolling" in _MESSAGES_JS
    trim_body = _MESSAGES_JS[
        _MESSAGES_JS.index("function trimLiveDisplayWindowIfAutoscrolling"):
        _MESSAGES_JS.index("function addMsg", _MESSAGES_JS.index("function trimLiveDisplayWindowIfAutoscrolling"))]
    assert "if (!wasAutoscroll) return;" in trim_body
    assert "LIVE_DISPLAY_WINDOW_MULTIPLIER" in trim_body
    assert "el.remove()" in trim_body


def test_prompt_bar_has_fast_conversation_refresh_button():
    template = Path("tasks/io/chat_ui/template.html").read_text(encoding="utf-8")
    assert 'id="refreshConvBtn"' in template
    assert 'onclick="refreshCurrentConversation()"' in template
    assert "function refreshCurrentConversation" in Path(
        "tasks/io/chat_ui/conversations.js").read_text(encoding="utf-8")


def test_context_usage_cache_counts_suffix_then_resets():
    from tasks.ai.context_usage_cache import context_usage_from_cache

    first_msgs = [{"role": "user", "content": "alpha " * 200}]
    first = context_usage_from_cache(
        first_msgs, 10000, source="stored_context")

    second = context_usage_from_cache(
        first_msgs + [{"role": "assistant", "content": "beta " * 200}],
        10000, first, source="active_context")

    compacted = context_usage_from_cache(
        [{"role": "system", "content": "compact summary"}],
        10000, second, source="compact_reset")

    assert first["cache_mode"] == "full"
    assert second["cache_mode"] == "delta"
    assert second["used"] > first["used"]
    assert compacted["cache_mode"] == "full"
    assert compacted["used"] < second["used"]


def test_list_resources_does_not_transport_context_usage():
    src = Path("tasks/ai/actions/agent_resource.py").read_text(encoding="utf-8")
    list_resources_block = src[
        src.index('if action == "list_resources":'):
        src.index('if action == "get_resource_detail":')]
    assert "context_usage_from_cache" not in list_resources_block
    assert "load_agent_context" not in list_resources_block
    assert '"context_usage"' not in list_resources_block


def test_list_resources_uses_async_flow_template_cache():
    src = Path("tasks/ai/actions/agent_resource.py").read_text(encoding="utf-8")
    list_resources_block = src[
        src.index('if action == "list_resources":'):
        src.index('if action == "get_resource_detail":')]
    assert "result[\"flow_templates\"] = _get_flow_templates_cached(user_id)" in list_resources_block
    assert ".rglob(" not in list_resources_block


def test_audio_frontend_never_opens_stream_without_token():
    audio_src = Path("tasks/io/chat_ui/audio.js").read_text(encoding="utf-8")
    terminal_src = Path("tasks/io/chat_ui/terminal.js").read_text(encoding="utf-8")
    tabs_src = Path("tasks/io/chat_ui/tabs.js").read_text(encoding="utf-8")

    assert "if (!sessionId || !_audioToken)" in audio_src
    assert "if (resp.audio_session && resp.audio_token)" in terminal_src
    assert "audioConnect(resp.audio_session, resp.audio_token)" in terminal_src
    assert "audioConnect(resp.audio_session, resp.audio_token || '')" not in terminal_src
    assert "audioConnect(resp.audio_session);" not in terminal_src
    assert "panel.dataset.audioToken = audioToken || ''" in tabs_src
    assert "activeAudioTab.dataset.audioToken" in audio_src
    assert "if (!sid || !token)" in audio_src
    assert "audioConnect(sid, token)" in audio_src


def test_view_menu_has_three_grouping_toggles():
    template = Path("tasks/io/chat_ui/template.html").read_text(encoding="utf-8")
    convs = Path("tasks/io/chat_ui/conversations.js").read_text(encoding="utf-8")
    messages = Path("tasks/io/chat_ui/messages.js").read_text(encoding="utf-8")
    sse = Path("tasks/io/chat_ui/sse.js").read_text(encoding="utf-8")
    conversation_py = Path("tasks/ai/actions/conversation.py").read_text(encoding="utf-8")

    for item_id in ("viewMenuToggle", "viewItemTechnical", "viewItemTask", "viewItemDelegate"):
        assert item_id in template
    assert 'onclick="onViewGroupingToggle(\'technical\')"' in template
    assert 'onclick="onViewGroupingToggle(\'task\')"' in template
    assert 'onclick="onViewGroupingToggle(\'delegate\')"' in template
    assert "technicalGroupingToggle" not in template

    for key in ("technical", "task", "delegate"):
        assert f"chat.group_{key}_messages" in convs
    assert "VIEW_TOGGLES" in convs
    assert "onViewGroupingToggle" in convs
    assert "toggleViewMenu" in convs

    for flag in (
        "PAWFLOW_GROUP_TECHNICAL_MESSAGES",
        "PAWFLOW_GROUP_TASK_MESSAGES",
        "PAWFLOW_GROUP_DELEGATE_MESSAGES",
    ):
        assert flag in messages
    assert "setTaskMessageGrouping" in messages
    assert "setDelegateMessageGrouping" in messages

    assert "PAWFLOW_GROUP_TASK_MESSAGES" in sse
    assert "PAWFLOW_GROUP_DELEGATE_MESSAGES" in messages
    # Live delegate path must also honour the toggle, not just the historical render.
    get_or_create_group = sse[sse.index("function _getOrCreateGroup"):]
    assert get_or_create_group.split("\n", 4)[1].strip() == "if (!window.PAWFLOW_GROUP_DELEGATE_MESSAGES) return null;"
    get_or_create_sub = sse[sse.index("function _getOrCreateSubBlock"):]
    assert get_or_create_sub.split("\n", 4)[1].strip() == "if (!window.PAWFLOW_GROUP_DELEGATE_MESSAGES) return null;"
    # Ungrouped task_msg must use addMsg to keep chrome (timestamp, badge, dedup).
    assert "addMsg('user', data.message || '', {" in sse
    # Live delegate tool_call must only be hidden when grouping is on,
    # otherwise the main timeline loses the launch + result.
    assert "data.tool === 'delegate' && window.PAWFLOW_GROUP_DELEGATE_MESSAGES" in sse

    assert '_resolve_chat_flag("group_task_messages")' in conversation_py
    assert '_resolve_chat_flag("group_delegate_messages")' in conversation_py
    assert '"group_task_messages": group_task_messages' in conversation_py
    assert '"group_delegate_messages": group_delegate_messages' in conversation_py

    for lang in ("en", "fr", "es"):
        i18n = Path(f"tasks/io/chat_ui/i18n/{lang}.json").read_text(encoding="utf-8")
        for key in ("viewMenuTitle", "viewGroupTechnical", "viewGroupTasks", "viewGroupDelegates"):
            assert f'"{key}"' in i18n, f"{lang} missing {key}"


def test_terminal_frontend_keeps_scrollback_and_cci_tmux_mouse():
    terminal_src = Path("tasks/io/chat_ui/terminal.js").read_text(encoding="utf-8")
    service_flow_src = Path("tasks/ai/actions/service_flow.py").read_text(encoding="utf-8")

    assert "scrollback: 10000" in terminal_src
    assert "fastScrollModifier" in terminal_src
    assert "attachCustomKeyEventHandler" in terminal_src
    assert "_copyTerminalSelection(term)" in terminal_src
    assert "_pasteClipboardToTerminal(ws)" in terminal_src
    assert "container.addEventListener('paste'" in terminal_src
    assert "function _estimateTerminalSize()" in terminal_src
    assert "cols: termSize.cols" in terminal_src
    assert "rows: termSize.rows" in terminal_src
    assert "_fitAndNotifyTerminal(container)" in terminal_src
    assert "container._fitAddon.fit()" in terminal_src
    assert '("mouse", "on")' in service_flow_src
    assert '("history-limit", "50000")' in service_flow_src
    assert '["tmux", "set-option", "-g", *option]' in service_flow_src
    assert "except Exception:\n        pass" in service_flow_src


def test_open_desktop_backend_does_not_emit_audio_session_without_token():
    src = Path("tasks/ai/actions/service_flow.py").read_text(encoding="utf-8")
    assert "_audio_token = register_audio_source" in src
    assert '"audio_session": session_id if _audio_token else ""' in src
    assert '"audio_session": _sid if _audio_token else ""' in src
    assert '"audio_session": session_id,\n                "audio_token": _audio_lookup_token(session_id)' not in src
    assert '"audio_session": _sid,\n                            "audio_token": _audio_lookup_token(_sid)' not in src


def test_list_active_does_not_transport_context_usage_or_count_tokens():
    from core import FlowFile
    from core.llm_client import LLMMessage
    from tasks.ai.actions.usage import _handle_usage

    ctx = {
        "active_agent_name": "assistant",
        "messages": [
            LLMMessage(
                role="user",
                content="live pawflow context " * 200,
                conversation_id="conv-live"),
        ],
        "max_context_size": 10000,
        "_started_at": 0,
    }
    fake_exec = SimpleNamespace(
        _active_contexts={"conv-live": ctx},
        _active_contexts_lock=threading.Lock())

    class _Store:
        def get_extra(self, *_args, **_kwargs):
            raise AssertionError("list_active must not read context_usage")

        def get_extra_snapshot(self, *_args, **_kwargs):
            raise AssertionError("list_active must not read context_usage")

    ff = FlowFile()
    with patch("tasks.ai.agent_loop.AgentLoopTask._live_instance", fake_exec), \
            patch("core.conversation_store.ConversationStore.instance", return_value=_Store()), \
            patch("core.cc_live_registry.LiveSessionRegistry.instance") as cc_reg, \
            patch("core.codex_live_registry.CodexLiveRegistry.instance") as codex_reg, \
            patch("core.gemini_live_registry.GeminiLiveRegistry.instance") as gemini_reg:
        cc_reg.return_value.status.return_value = []
        codex_reg.return_value.status.return_value = []
        gemini_reg.return_value.status.return_value = []

        out = _handle_usage(
            SimpleNamespace(), "list_active", {"conversation_id": "conv-live"},
            None, "user", ff)

    data = json.loads(out[0].get_content().decode("utf-8"))
    row = data["active"][0]
    assert row["agent_name"] == "assistant"
    assert "context_usage" not in row


def test_list_active_does_not_surface_assigned_task_between_turns():
    from core import FlowFile
    from tasks.ai.actions.usage import _handle_usage

    fake_exec = SimpleNamespace(
        _active_turns={},
        _active_contexts={},
        _active_contexts_lock=threading.Lock())

    class _Store:
        def get_extra_snapshot(self, cid, key, default=None):
            raise AssertionError("list_active must not read assigned/scheduled tasks")

    ff = FlowFile()
    with patch("tasks.ai.agent_loop.AgentLoopTask._live_instance", fake_exec), \
            patch("core.cc_live_registry.LiveSessionRegistry.instance") as cc_reg, \
            patch("core.codex_live_registry.CodexLiveRegistry.instance") as codex_reg, \
            patch("core.gemini_live_registry.GeminiLiveRegistry.instance") as gemini_reg:
        cc_reg.return_value.status.return_value = []
        codex_reg.return_value.status.return_value = []
        gemini_reg.return_value.status.return_value = []

        out = _handle_usage(
            SimpleNamespace(), "list_active", {"conversation_id": "conv-task"},
            _Store(), "user", ff)

    data = json.loads(out[0].get_content().decode("utf-8"))
    assert data["active"] == []


def test_list_active_surfaces_agent_currently_working_inside_task():
    from core import FlowFile
    from tasks.ai.actions.usage import _handle_usage

    fake_exec = SimpleNamespace(
        _active_turns={
            "conv-task::task::t_running:PawFlowAgent": {
                "agent_name": "PawFlowAgent",
                "started_at": 0,
                "status": "thinking",
            }
        },
        _active_contexts={},
        _active_contexts_lock=threading.Lock())

    class _Store:
        def get_extra_snapshot(self, *_args, **_kwargs):
            raise AssertionError("active task-agent rows must come from live execution state")

    ff = FlowFile()
    with patch("tasks.ai.agent_loop.AgentLoopTask._live_instance", fake_exec), \
            patch("core.cc_live_registry.LiveSessionRegistry.instance") as cc_reg, \
            patch("core.codex_live_registry.CodexLiveRegistry.instance") as codex_reg, \
            patch("core.gemini_live_registry.GeminiLiveRegistry.instance") as gemini_reg:
        cc_reg.return_value.status.return_value = []
        codex_reg.return_value.status.return_value = []
        gemini_reg.return_value.status.return_value = []

        out = _handle_usage(
            SimpleNamespace(), "list_active", {"conversation_id": "conv-task"},
            _Store(), "user", ff)

    data = json.loads(out[0].get_content().decode("utf-8"))
    assert data["active"] == [{
        "agent_name": "PawFlowAgent",
        "task_id": "t_running",
        "iteration": 0,
        "round": 0,
        "max_rounds": 0,
        "last_tool": "",
        "duration_s": 0,
        "status": "thinking",
        "message_preview": "",
    }]


def test_initial_context_usage_has_dedicated_action():
    usage_src = Path("tasks/ai/actions/usage.py").read_text(encoding="utf-8")
    list_context_usage_block = _extract_action_block(
        usage_src, 'if action == "list_context_usage":')
    assert "compute_context_usage" in list_context_usage_block
    assert "context_usage_from_cache" not in list_context_usage_block
    assert 'source="list_context_usage"' in list_context_usage_block
    assert '"context_usage": out' in list_context_usage_block

    list_active_block = _extract_action_block(
        usage_src, 'if action == "list_active":')
    assert '"context_usage"' not in list_active_block


def test_list_context_usage_prefers_active_context_over_disk():
    from core import FlowFile
    from core.llm_client import LLMMessage
    from tasks.ai.actions.usage import _handle_usage

    active_messages = [
        LLMMessage(
            role="user", content="live context " * 20,
            conversation_id="conv-live"),
        LLMMessage(
            role="assistant", content="current assistant context " * 20,
            conversation_id="conv-live"),
    ]
    fake_exec = SimpleNamespace(
        _active_contexts={
            "conv-live:assistant": {
                "active_agent_name": "assistant",
                "messages": active_messages,
                "max_context_size": 10000,
            },
        },
        _active_contexts_lock=threading.Lock())

    class _Store:
        def load_agent_context(self, *_args, **_kwargs):
            raise AssertionError("active agent gauge must not read disk context")

        def load_transcript_for_agent(self, *_args, **_kwargs):
            raise AssertionError("active agent gauge must not read transcript")

    ff = FlowFile()
    with patch("tasks.ai.agent_loop.AgentLoopTask._live_instance", fake_exec), \
            patch("core.conv_agent_config.get_all_agent_configs",
                  return_value={"assistant": {"llm_service": "svc"}}), \
            patch("core.service_registry.ServiceRegistry.get_instance",
                  return_value=SimpleNamespace()):
        out = _handle_usage(
            SimpleNamespace(), "list_context_usage",
            {"conversation_id": "conv-live"}, _Store(), "user", ff)

    data = json.loads(out[0].get_content().decode("utf-8"))
    usage = data["context_usage"]["assistant"]
    assert usage["source"] == "list_context_usage"
    assert usage["message_count"] == len(active_messages)
    assert usage["used"] > 0


def test_inactive_context_usage_reuses_persisted_snapshot_cache():
    from tasks.ai.context_usage import compute_context_usage
    from tasks.ai.context_usage_cache import context_usage_entry

    stored_messages = [
        {"role": "user", "content": "stored user", "msg_id": "s1"},
        {"role": "assistant", "content": "stored assistant", "msg_id": "s2"},
    ]
    cached = context_usage_entry(
        stored_messages, 123, 10000, source="message_meta")

    class _Store:
        def load_agent_context(self, *_args, **_kwargs):
            return stored_messages

        def load_transcript_for_agent(self, *_args, **_kwargs):
            raise AssertionError("agent context should be used first")

        def get_extra_snapshot(self, *_args, **_kwargs):
            return {"assistant": cached}

    with patch("tasks.ai.agent_loop.AgentLoopTask._live_instance", None), \
            patch("tasks.ai.context_usage._service_config",
                  return_value=({"max_context_size": 10000}, 0, "")), \
            patch("core.token_counter.count_messages_tokens",
                  side_effect=AssertionError("valid cache should avoid recount")):
        usage = compute_context_usage(
            "conv-cached", "assistant", user_id="user", store=_Store(),
            source="list_context_usage")

    assert usage["used"] == 123
    assert usage["source"] == "list_context_usage"
    assert usage["cache_mode"] == "full"


def test_cli_resumed_context_usage_uses_stored_context_plus_live_delta():
    from core.llm_client import LLMMessage
    from tasks.ai.context_usage import compute_context_usage

    stored_messages = [
        {"role": "user", "content": "stored user " * 20, "msg_id": "s1"},
        {"role": "assistant", "content": "stored assistant " * 20, "msg_id": "s2"},
        {"role": "tool", "content": "stored tool " * 20, "msg_id": "s3"},
    ]
    live_messages = [
        LLMMessage(role="user", content="live delta " * 20,
                   conversation_id="conv-live"),
    ]
    fake_exec = SimpleNamespace(
        _active_contexts={
            "conv-live:assistant": {
                "active_agent_name": "assistant",
                "messages": live_messages,
                "_is_cli_provider": True,
                "_cli_has_session": True,
                "resolved_svc": SimpleNamespace(config={"max_context_size": 10000}),
            },
        },
        _active_contexts_lock=threading.Lock())

    class _Store:
        def load_agent_context(self, *_args, **_kwargs):
            return stored_messages

        def load_transcript_for_agent(self, *_args, **_kwargs):
            raise AssertionError("stored agent context should be used first")

    with patch("tasks.ai.agent_loop.AgentLoopTask._live_instance", fake_exec):
        usage = compute_context_usage(
            "conv-live", "assistant", user_id="user", store=_Store(),
            source="test")

    assert usage["message_count"] == len(stored_messages) + len(live_messages)
    assert usage["used"] > 0


def test_cli_gauge_uses_stored_context_after_session_restart():
    """Regression: a tmux/container restart leaves active_ctx["messages"]
    nearly empty (_cli_has_session is False). The gauge must still count
    the stored PawFlow context — it changes only on compaction/edit, not
    on a session restart.
    """
    from tasks.ai.context_usage import compute_context_usage

    stored_messages = [
        {"role": "user", "content": "stored content " * 30, "msg_id": f"s{i}"}
        for i in range(40)
    ]
    fake_exec = SimpleNamespace(
        _active_contexts={
            "conv-live:assistant": {
                "active_agent_name": "assistant",
                "messages": [],  # session restarting -> transient delta empty
                "_is_cli_provider": True,
                "_cli_has_session": False,
                "resolved_svc": SimpleNamespace(
                    config={"max_context_size": 1000000}),
            },
        },
        _active_contexts_lock=threading.Lock())

    class _Store:
        def load_agent_context(self, *_args, **_kwargs):
            return stored_messages

    with patch("tasks.ai.agent_loop.AgentLoopTask._live_instance", fake_exec):
        usage = compute_context_usage(
            "conv-live", "assistant", user_id="user", store=_Store(),
            source="test")

    assert usage["message_count"] == len(stored_messages)
    assert usage["used"] > 0


def test_cli_claude_gauge_adds_invisible_overhead():
    """claude-code gauges add a fixed offset for the CLI's invisible
    system-prompt/tooling tokens that PawFlow never sees."""
    from tasks.ai.context_usage import (
        compute_context_usage, _CLI_INVISIBLE_OVERHEAD_TOKENS)

    stored = [{"role": "user", "content": "hello world", "msg_id": "s1"}]
    fake_exec = SimpleNamespace(
        _active_contexts={
            "c:assistant": {
                "active_agent_name": "assistant",
                "messages": [],
                "_is_cli_provider": True,
                "active_llm_provider": "claude-code-interactive",
                "resolved_svc": SimpleNamespace(
                    config={"max_context_size": 1000000}),
            },
        },
        _active_contexts_lock=threading.Lock())

    class _Store:
        def load_agent_context(self, *_args, **_kwargs):
            return stored

    with patch("tasks.ai.agent_loop.AgentLoopTask._live_instance", fake_exec):
        usage = compute_context_usage(
            "c", "assistant", user_id="user", store=_Store(), source="test")

    assert usage["overhead_tokens"] == _CLI_INVISIBLE_OVERHEAD_TOKENS
    assert usage["used"] >= _CLI_INVISIBLE_OVERHEAD_TOKENS


def test_claude_final_metadata_does_not_rewrite_conversation_rows():
    block = _AGENT_CORE_PY[
        _AGENT_CORE_PY.index("def _patch_cc_turn_gauge"):
        _AGENT_CORE_PY.index("# SpawnAgentsHandler source tracking")]
    assert "patch_message(" not in block
    assert "persist_context_usage(" in block
    assert '"context_used" not in _cc_src' in block
    assert 'publish_event(' in block
    assert '"message_meta"' in block
    assert '"context_message_count"' in block
    assert '"context_cache_mode"' in block
    # Helper is invoked by both the final-turn and the CCI interrupt path.
    assert _AGENT_CORE_PY.count("_patch_cc_turn_gauge(") >= 3


def test_cci_gauge_uses_pawflow_calculation_not_provider_usage():
    """The CCI context gauge is the stable PawFlow stored-context
    calculation (via _agent_source -> compute_context_usage), NOT the
    provider's per-session reported usage. Provider usage resets when the
    CLI/tmux session cold-starts, which made the gauge jump.
    """
    src = Path("tasks/ai/agent_core.py").read_text(encoding="utf-8")
    block = src[
        src.index("def _patch_cc_turn_gauge"):
        src.index("# SpawnAgentsHandler source tracking")]
    assert "_agent_source(" in block
    # The provider per-session usage path must be fully gone.
    assert "_apply_provider_context_usage" not in src
    assert "_provider_context_usage_from_response" not in src
    assert "claude_code_interactive_provider" not in src


def test_cci_gauge_refreshes_between_tool_rounds():
    """CCI gauge must update mid-run, not freeze until the agent stops.

    The emitter skips CCI (_context_usage_payload returns None) and the
    final-turn patch only fires on a no-tool response. Without a per-round
    patch, a long tool-looping run freezes the context gauge.
    """
    src = Path("tasks/ai/agent_core.py").read_text(encoding="utf-8")
    round_block = src[
        src.index("emitter.drain_pending(messages, _append, iteration)"):
        src.index("# Max iterations reached")]
    assert 'if _client_provider == "claude-code-interactive":' in round_block
    assert "_patch_cc_turn_gauge(" in round_block


def test_context_panel_token_estimate_uses_gauge_when_available():
    """The panel header token count must match the authoritative gauge.

    The per-page estimate only covers the loaded page; showing it next to
    the full message_count contradicts the gauge line.
    """
    src = Path("tasks/ai/actions/context_ops.py").read_text(encoding="utf-8")
    assert 'estimated = int(_context_usage.get("used", 0) or 0)' in src


def test_idle_polling_cannot_stack_unbounded_work():
    assert "_syncActiveSub" in _ACTIVE_AGENTS_JS
    assert "_SYNC_ACTIVE_STALE_MS" in _ACTIVE_AGENTS_JS
    assert "if (!force && now - _syncActiveStartedAt < _SYNC_ACTIVE_STALE_MS) return" in _ACTIVE_AGENTS_JS
    assert "setInterval(syncActiveFromServer, 10000)" in _ACTIVE_AGENTS_JS
    assert "document.hidden) return" in _ACTIVE_AGENTS_JS
    assert "_MAX_BG_ACTIONS" in _AGENT_ACTIONS_PY


def test_done_event_cannot_be_undone_by_stale_active_poll():
    """A list_active request started before done must not resurrect the row.

    Server cleanup runs immediately after publishing done, so an already
    in-flight poll can return a stale _active_turns snapshot. The browser must
    reject that response and force a fresh sync after cleanup has landed.
    """
    active_src = _ACTIVE_AGENTS_JS
    sse_done = _SSE_JS[
        _SSE_JS.index("eventSource.addEventListener('done'"):
        _SSE_JS.index("// Refresh conversation list",
                      _SSE_JS.index("eventSource.addEventListener('done'"))]
    assert "let _activeDoneAt = {}" in active_src
    assert "_activeDoneAt[key] = now" in active_src
    assert "const requestStartedAt = now" in active_src
    assert "if (doneAt && requestStartedAt <= doneAt) return false" in active_src
    assert "syncActiveFromServer(true)" in sse_done
def test_poller_watchdogs_are_throttled():
    assert "PAWFLOW_AGENT_WATCHDOG_INTERVAL_SECONDS" in _AGENT_POLLER_PY
    assert "_last_task_watchdog" in _AGENT_POLLER_PY
    assert "_last_thought_watchdog" in _AGENT_POLLER_PY
    ensure_block = _AGENT_POLLER_PY[
        _AGENT_POLLER_PY.index("def _ensure_tasks_scheduled"):
        _AGENT_POLLER_PY.index("def _ensure_thoughts_scheduled")]
    assert 'all_tasks = _cache.get("extras", {}).get("agent_tasks") or {}' in ensure_block
    assert 'get_extra_cached(cid, "agent_tasks")' not in ensure_block


def test_relay_connection_tasks_are_tracked_and_cancelled():
    assert "relay_tasks = set()" in _FILESYSTEM_SERVICE_PY
    assert "task.add_done_callback(relay_tasks.discard)" in _FILESYSTEM_SERVICE_PY
    assert "task.cancel()" in _FILESYSTEM_SERVICE_PY
    assert '"tasks": relay_tasks' in _FILESYSTEM_SERVICE_PY


def test_runtime_context_agent_follows_resolved_active_agent():
    """Context loading must use the resolved active agent, not an early
    stale active_resources value. Otherwise Gemini/Codex cold starts can
    read the shared context while the private agent context is intact."""
    sync_block = _AGENT_CONTEXT_PY[
        _AGENT_CONTEXT_PY.index("if _active_agent_name and _context_agent"):
        _AGENT_CONTEXT_PY.index("# Ensure we have a client")]
    assert "_context_agent = _active_agent_name" in sync_block


def test_context_ops_distinguishes_missing_and_empty_agent_context():
    """An existing empty private context is a deliberate diverged state.
    `_ctx_load` must only fall back to transcript when the context file is
    missing (`None`), not when it is an empty list."""
    assert "if ctx is not None:" in _CONTEXT_OPS_PY
    assert "agent_name == \"transcript\"" in _CONTEXT_OPS_PY
    assert "if action == \"edit_message\"" in _CONTEXT_OPS_PY


def test_context_editor_scopes_mutations_to_visible_context():
    """The context editor must not send `agent_name: transcript` to
    context-writing actions, and multi-delete must target the visible
    private/shared context instead of always deleting transcript rows."""
    assert "function _ctxScopedAgentName" in _CONTEXT_EDITOR_JS
    assert "_ctxAgentFilter !== 'transcript'" in _CONTEXT_EDITOR_JS
    assert "action: _ctxAgentFilter === 'transcript' ? 'edit_message' : 'edit_context'" in _CONTEXT_EDITOR_JS
    assert "action: 'delete_context_messages', msg_ids: mids" in _CONTEXT_EDITOR_JS
    assert "action: 'delete_message', msg_ids: mids" in _CONTEXT_EDITOR_JS


def test_context_editor_loaded_rows_show_scrollable_full_message():
    assert "row.onclick = function(event)" in _CONTEXT_EDITOR_JS
    assert "row.querySelector('.ctx-full')" in _CONTEXT_EDITOR_JS
    assert _CONTEXT_EDITOR_JS.count('class="ctx-preview"') >= 2
    assert _CONTEXT_EDITOR_JS.count("max-height:96px;overflow-y:auto") >= 2
    assert _CONTEXT_EDITOR_JS.count("max-height:min(60vh,640px);overflow-y:auto") >= 2
    assert "event.target.closest('.ctx-preview,.ctx-full')" in _CONTEXT_EDITOR_JS


def test_empty_assistant_no_tools_never_persists_blank_message():
    tool_exec_src = Path("tasks/ai/agent_tool_exec.py").read_text(encoding="utf-8")
    agent_core_src = Path("tasks/ai/agent_core.py").read_text(encoding="utf-8")
    serialization_src = Path("tasks/ai/agent_serialization.py").read_text(encoding="utf-8")
    assert "if final.strip():" in tool_exec_src
    assert "forced-synthesis path" in tool_exec_src
    assert "thinking-only live delta" in agent_core_src
    assert "publish_event" in agent_core_src
    assert "and not msg.tool_calls" in agent_core_src
    assert "if not _resp_text and _has_thinking:" in agent_core_src
    assert "if not _need_more_retried:" in agent_core_src
    assert "role == \"assistant\" and not str(content).strip()" in serialization_src


def test_list_active_keeps_idle_live_sessions_out_of_active_payload():
    from core import FlowFile
    from tasks.ai.actions.usage import _handle_usage

    fake_exec = SimpleNamespace(
        _active_contexts={}, _active_contexts_lock=threading.Lock())

    class _Store:
        def get_extra(self, *_args, **_kwargs):
            return {}

    live_entry = {
        "conv_id": "conv-live", "agent_name": "assistant",
        "live": True, "idle_seconds": 12, "reuse_count": 3,
        "lived_seconds": 90,
    }
    ff = FlowFile()
    with patch("tasks.ai.agent_loop.AgentLoopTask._live_instance", fake_exec), \
            patch("core.conversation_store.ConversationStore.instance", return_value=_Store()), \
            patch("core.cc_live_registry.LiveSessionRegistry.instance") as cc_reg, \
            patch("core.codex_live_registry.CodexLiveRegistry.instance") as codex_reg, \
            patch("core.gemini_live_registry.GeminiLiveRegistry.instance") as gemini_reg:
        cc_reg.return_value.status.return_value = []
        codex_reg.return_value.status.return_value = [live_entry]
        gemini_reg.return_value.status.return_value = []

        out = _handle_usage(
            SimpleNamespace(), "list_active", {"conversation_id": "conv-live"},
            None, "user", ff)

    data = json.loads(out[0].get_content().decode("utf-8"))
    assert data["active"] == []
    assert data["codex_live"] == [live_entry]


def test_list_active_uses_provider_agnostic_active_turn_without_context():
    """Active Agents must not disappear during context preparation/compact.
    `_active_contexts` is only present inside _run_agent_loop; `_active_turns`
    covers the provider-agnostic worker lifetime before and between loops.
    """
    from core import FlowFile
    from tasks.ai.actions.usage import _handle_usage

    fake_exec = SimpleNamespace(
        _active_turns={
            "conv-live:assistant": {
                "conversation_id": "conv-live",
                "agent_name": "assistant",
                "started_at": 100.0,
                "status": "preparing",
                "message_preview": "compact is running",
            },
        },
        _active_contexts={},
        _active_contexts_lock=threading.Lock())

    class _Store:
        def get_extra(self, *_args, **_kwargs):
            return {}

    ff = FlowFile()
    with patch("tasks.ai.agent_loop.AgentLoopTask._live_instance", fake_exec), \
            patch("core.conversation_store.ConversationStore.instance", return_value=_Store()), \
            patch("core.cc_live_registry.LiveSessionRegistry.instance") as cc_reg, \
            patch("core.codex_live_registry.CodexLiveRegistry.instance") as codex_reg, \
            patch("core.gemini_live_registry.GeminiLiveRegistry.instance") as gemini_reg, \
            patch("time.time", return_value=130.0):
        cc_reg.return_value.status.return_value = []
        codex_reg.return_value.status.return_value = []
        gemini_reg.return_value.status.return_value = []
        out = _handle_usage(
            SimpleNamespace(), "list_active", {"conversation_id": "conv-live"},
            None, "user", ff)

    data = json.loads(out[0].get_content().decode("utf-8"))
    assert data["active"] == [{
        "agent_name": "assistant",
        "task_id": "",
        "iteration": 0,
        "round": 0,
        "max_rounds": 0,
        "last_tool": "",
        "duration_s": 30.0,
        "status": "preparing",
        "message_preview": "compact is running",
    }]


def test_is_agent_active_uses_provider_agnostic_active_turns():
    from tasks.ai.agent_loop import AgentLoopTask

    fake_exec = SimpleNamespace(
        _active_turns={"conv-live:assistant": {"agent_name": "assistant"}},
        _active_contexts={},
        _active_contexts_lock=threading.Lock())

    with patch("tasks.ai.agent_loop.AgentLoopTask._live_instance", fake_exec):
        assert AgentLoopTask.is_agent_active("conv-live", "assistant") is True
        assert AgentLoopTask.is_agent_active("conv-live", "") is True
        assert AgentLoopTask.is_agent_active("conv-live", "other") is False


def test_live_badge_requires_reused_live_process():
    """The LIVE badge means this active turn reused an already-live process.
    First execution has a live process entry, but reuse_count is still zero.
    """
    from core import FlowFile
    from tasks.ai.actions.usage import _handle_usage

    fake_exec = SimpleNamespace(
        _active_contexts={
            "conv-live": {
                "active_agent_name": "assistant",
                "messages": [],
                "max_context_size": 10000,
                "_started_at": 0,
            },
        },
        _active_contexts_lock=threading.Lock())

    class _Store:
        def get_extra(self, *_args, **_kwargs):
            return {}

    def _call_with(live_entry):
        ff = FlowFile()
        with patch("tasks.ai.agent_loop.AgentLoopTask._live_instance", fake_exec), \
                patch("core.conversation_store.ConversationStore.instance", return_value=_Store()), \
                patch("core.cc_live_registry.LiveSessionRegistry.instance") as cc_reg, \
                patch("core.codex_live_registry.CodexLiveRegistry.instance") as codex_reg, \
                patch("core.gemini_live_registry.GeminiLiveRegistry.instance") as gemini_reg:
            cc_reg.return_value.status.return_value = []
            codex_reg.return_value.status.return_value = [live_entry]
            gemini_reg.return_value.status.return_value = []
            out = _handle_usage(
                SimpleNamespace(), "list_active", {"conversation_id": "conv-live"},
                None, "user", ff)
        return json.loads(out[0].get_content().decode("utf-8"))

    first_exec = {
        "conv_id": "conv-live", "agent_name": "assistant",
        "live": True, "idle_seconds": 0, "reuse_count": 0,
        "lived_seconds": 3,
    }
    reused_exec = {**first_exec, "reuse_count": 1}

    first_data = _call_with(first_exec)
    reused_data = _call_with(reused_exec)

    assert first_data["active"][0]["codex_live"] is False
    assert first_data["active"][0]["codex_reuse_count"] == 0
    assert first_data["codex_live"] == [first_exec]
    assert reused_data["active"][0]["codex_live"] is True
    assert reused_data["active"][0]["codex_reuse_count"] == 1


def test_interrupt_uses_live_stop_or_graceful_api_stop_turn():
    """Interrupt steers live providers; APIs run one STOP turn then end."""
    loop_src = Path("tasks/ai/agent_loop.py").read_text(encoding="utf-8")
    core_src = Path("tasks/ai/agent_core.py").read_text(encoding="utf-8")
    emitter_src = Path("tasks/ai/agent_emitter.py").read_text(encoding="utf-8")
    assert "send_user_message(" in loop_src
    assert "SOFT_INTERRUPT_USER_COMMAND" in loop_src
    assert "user_id=str(_active_ctx.get(\"user_id\") or \"\")" in loop_src
    assert "conversation_id=conversation_id" in loop_src
    assert "agent_name=agent_name" in loop_src
    assert "Do NOT bump generation here; that is force-stop semantics" in loop_src
    assert "self._conv_interrupt[_key] = True" in loop_src
    assert "interrupt cancels active loop" not in loop_src
    assert "def _run_interrupt_turn():" in core_src
    assert "discarding current turn and running STOP turn" in core_src
    assert "tools=None" in core_src
    assert "def on_interrupted(self, result: AgentResult)" in emitter_src
    assert "self.on_done(result)" in emitter_src
    assert "STOP user message queued" not in loop_src
    assert "source=\"interrupt\"" not in loop_src
    assert "interrupt-synth" not in loop_src


def test_api_tool_execution_registers_kill_hooks_for_ui_kill():
    src = Path("tasks/ai/agent_tool_exec.py").read_text(encoding="utf-8")
    assert "ToolRelayService._inflight[tc.id]" in src
    assert "_set_current_cancel_event(_cancel_event)" in src
    assert "_set_current_kill_hooks(_kill_hooks)" in src
    assert "ToolRelayService._inflight.pop(tc.id, None)" in src


def test_screen_actions_have_server_side_timeout_and_cancel_pending():
    screen_src = Path("core/handlers/screen.py").read_text(encoding="utf-8")
    tool_config_src = Path("tasks/ai/agent_tool_config.py").read_text(encoding="utf-8")
    fs_src = Path("services/filesystem_service.py").read_text(encoding="utf-8")
    host_screen_src = Path("tools/screen_actions.py").read_text(encoding="utf-8")
    assert "Provider-invariant handler context" in tool_config_src
    assert "hasattr(h, 'set_user_id')" in tool_config_src
    assert "hasattr(h, 'set_conversation_id')" in tool_config_src
    assert "hasattr(h, 'set_agent_name')" in tool_config_src
    assert "hasattr(h, 'set_base_url')" in tool_config_src
    assert "def set_conversation_id" in screen_src
    assert "h.name == 'screen'" in tool_config_src
    assert "h.set_conversation_id(conversation_id)" in tool_config_src
    assert '"timeout"' in screen_src
    assert "_request_timeout=timeout" in screen_src
    assert 'req_args["timeout"] = max(1, timeout - 1)' in screen_src
    assert 'kwargs.pop("_request_timeout", None)' in fs_src
    assert "self.cancel_pending(request_id)" in fs_src
    assert "subprocess.run(" in host_screen_src
    assert "timeout=max(1, timeout)" in host_screen_src
    assert "PAWFLOW_SCREEN_ACTION_CHILD" in host_screen_src
    assert "def _screen_action_subprocess" in host_screen_src
    assert "GetCursorPos" in host_screen_src


def test_live_agent_thread_without_context_is_not_killed_as_zombie():
    streaming_src = Path("tasks/ai/agent_streaming.py").read_text(encoding="utf-8")
    loop_src = Path("tasks/ai/agent_loop.py").read_text(encoding="utf-8")
    core_src = Path("tasks/ai/agent_core.py").read_text(encoding="utf-8")
    codex_src = Path("core/llm_providers/codex_app_server.py").read_text(encoding="utf-8")
    assert "zombie thread detected" not in streaming_src
    assert "active turn has no context yet" in streaming_src
    assert "active turn not preemptable yet — queuing for next drain" in streaming_src
    assert "preempted preparing provider turn — fast-restarting" not in streaming_src
    assert "t.name == f\"agent-stream-{conversation_id}\"" in loop_src
    assert "t.name.startswith(f\"agent-stream-{conversation_id}:\")" in loop_src
    assert "resurrects" in core_src
    assert "emitter.generation = self._conv_generation.get" not in core_src
    assert "def _hard_kill_for_context_compaction" in codex_src
    assert "_hard_kill_for_context_compaction(\"item/started\")" in codex_src
    assert "_hard_kill_for_context_compaction(\"item/completed\")" in codex_src
    assert "not keep_alive and not compact_hard_killed" in codex_src



def test_live_preempt_uses_hidden_provider_capability():
    from core.llm_client import LLMClient

    assert LLMClient("claude-code").supports_live_preempt is True
    assert LLMClient("codex-app-server").supports_live_preempt is True
    assert LLMClient("gemini").supports_live_preempt is True
    assert LLMClient("openai").supports_live_preempt is False
    assert LLMClient("anthropic").supports_live_preempt is False



def test_accepted_live_preempt_keeps_pending_rescue():
    """A live provider steer is not proof that the turn consumed the message.

    The stamped user message must remain in PendingQueue until the final drain:
    providers that can prove inline consumption suppress the rerun; providers
    without proof cannot lose a late steer.
    """
    src = Path("tasks/ai/agent_streaming.py").read_text(encoding="utf-8")
    assert "supports_live_preempt" in src
    assert "source=\"preempt_rescue\"" in src
    assert "preempted active provider session" in src
    assert "_queue_pending_user(source=\"http\")" in src
    assert "even_if_active=True" in src


def test_final_drain_suppresses_confirmed_live_preempt_rescues():
    from core.llm_client import LLMMessage
    from tasks.ai.agent_core import _preempt_rescue_requires_retrigger

    text_rescue = LLMMessage(
        role="user", content="late text", conversation_id="conv-live")
    text_rescue._pending_source = "preempt_rescue"
    text_rescue._pending_enqueued_at = 100.0

    image_rescue = LLMMessage(role="user", content=[
        {"type": "text", "text": "look"},
        {"type": "image_ref", "image_id": "img"},
    ], conversation_id="conv-live")
    image_rescue._pending_source = "preempt_rescue"
    image_rescue._pending_enqueued_at = 100.0

    http_msg = LLMMessage(
        role="user", content="normal queued text", conversation_id="conv-live")
    http_msg._pending_source = "http"
    http_msg._pending_enqueued_at = 100.0

    assert _preempt_rescue_requires_retrigger(
        text_rescue, 101.0, "claude-code", preempt_proven_handled=True) is False
    assert _preempt_rescue_requires_retrigger(
        image_rescue, 101.0, "claude-code", preempt_proven_handled=True) is False
    assert _preempt_rescue_requires_retrigger(
        text_rescue, 101.0, "codex-app-server", preempt_proven_handled=True) is False
    assert _preempt_rescue_requires_retrigger(
        image_rescue, 101.0, "codex-app-server", preempt_proven_handled=True) is False
    assert _preempt_rescue_requires_retrigger(
        text_rescue, 101.0, "gemini", preempt_proven_handled=True) is False
    assert _preempt_rescue_requires_retrigger(
        text_rescue, 101.0, "codex-app-server", preempt_proven_handled=False) is True
    assert _preempt_rescue_requires_retrigger(text_rescue, 0.0, "claude-code") is True
    assert _preempt_rescue_requires_retrigger(http_msg, 101.0, "claude-code") is True


def test_context_gauge_events_always_include_timestamp():
    core_src = Path("tasks/ai/agent_core.py").read_text(encoding="utf-8")
    emitter_src = Path("tasks/ai/agent_emitter.py").read_text(encoding="utf-8")
    compact_src = Path("tasks/ai/agent_compaction.py").read_text(encoding="utf-8")
    usage_src = Path("tasks/ai/context_usage.py").read_text(encoding="utf-8")
    provider_src = Path("core/llm_providers/claude_code.py").read_text(encoding="utf-8")

    assert '"updated_at": time.time()' in usage_src
    assert "usage_event_payload" in core_src
    assert "usage_event_payload" in emitter_src
    assert "updated_at=_context_updated_at" not in compact_src
    compact_event = compact_src[
        compact_src.index('"stage": "done"'):
        compact_src.index('except Exception:', compact_src.index('"stage": "done"'))]
    assert '"context_used"' not in compact_event
    assert '"context_pct"' not in compact_event
    assert 'int(result.tokens_in or 0)' not in emitter_src
    done_block = emitter_src[
        emitter_src.index('def on_done'):
        emitter_src.index('def on_cancelled')]
    assert '"context_used"' not in done_block
    assert '"context_pct"' not in done_block
    provider_live_usage = provider_src[
        provider_src.index("Capture freshest provider usage"):
        provider_src.index("Claude Code sends INCREMENTAL")]
    provider_result_meta = provider_src[
        provider_src.index("Cache CC's reported contextWindow"):
        provider_src.index("# If one or more preempts")]
    assert '"context_used"' not in provider_live_usage
    assert '"context_pct"' not in provider_live_usage
    assert '"context_used"' not in provider_result_meta
    assert '"context_pct"' not in provider_result_meta


def test_provider_compact_discards_pending_messages_already_in_compacted_context():
    src = Path("tasks/ai/agent_core.py").read_text(encoding="utf-8")
    compact_block = src[
        src.index("PawFlow compact: %d"):
        src.index("# 3. Invalidate CLI session")]
    assert "discard_msg_ids" in compact_block
    assert "_compacted_ids" in compact_block


def test_visible_answer_releases_active_before_slow_done_bookkeeping():
    """After the visible answer, slow done bookkeeping must not keep Active Agents."""
    src = Path("tasks/ai/agent_core.py").read_text(encoding="utf-8")
    callback_block = src[
        src.index("def _release_active_after_terminal_visible_answer"):
        src.index("def _claude_code_turn_callback", src.index("def _release_active_after_terminal_visible_answer"))]
    assert "_codex_app_turn_completed_for_callback" in callback_block
    assert "force: bool = False" in callback_block
    assert "self._active_contexts.pop(_ctx_key_done, None)" in callback_block
    assert "self._decrement_active(conversation_id, ctx)" in callback_block
    assert '"type": "active_released"' in callback_block
    assert "enqueue_sse_events" in callback_block

    turn_callback = src[
        src.index("def _claude_code_turn_callback"):
        src.index("# Finalize streaming element", src.index("def _claude_code_turn_callback"))]
    assert "_release_active_after_terminal_visible_answer()" in turn_callback
    assert turn_callback.index("_append(msg)") < turn_callback.index(
        "_release_active_after_terminal_visible_answer()")

    done_block = src[
        src.index('result = _make_result()'):
        src.index('return result', src.index('logger.info("[agent:%s] enqueueing done'))]
    assert "active released before done enqueue" in done_block
    assert "self._active_contexts.pop(_ctx_key_done, None)" in done_block
    assert "flush(timeout=30.0)" not in done_block
    assert "ConversationWriter.for_conversation" not in done_block
    assert "enqueue_done_after_writes" in done_block
    assert done_block.index("active released before done enqueue") < done_block.index(
        "enqueue_done_after_writes")
    assert "threading.Thread(" in done_block
    assert "target=_commit_turn_bg" in done_block
    assert "async commit_turn scheduled" in done_block
    assert "async commit_turn finished" in done_block
    assert "commit_turn(conversation_id, reason=_commit_reason)" in done_block
    foreground = done_block[:done_block.index("def _commit_turn_bg")]
    assert "commit_turn(conversation_id" not in foreground
    assert "source=_agent_source()" not in done_block
    make_result_block = src[
        src.index("def _make_result(reason=\"\")"):
        src.index("# Final drain", src.index("def _make_result(reason=\"\")"))]
    assert "source=_agent_source_cached()" in make_result_block

    final_response_block = src[
        src.index("# No tools \u2192 final response"):
        src.index("# Tool calls", src.index("# No tools \u2192 final response"))]
    assert "_release_active_after_terminal_visible_answer(force=True)" in final_response_block
    assert "_schedule_cc_turn_gauge_patch(" in final_response_block
    assert "_patch_cc_turn_gauge(" not in final_response_block


def test_provider_compact_blocks_late_callbacks_until_restart_finishes():
    """Once compact starts, stale provider callbacks must not keep appending work."""
    src = Path("tasks/ai/agent_core.py").read_text(encoding="utf-8")
    barrier_block = src[
        src.index("def _set_provider_compact_barrier"):
        src.index("def _compact_threshold_fraction")]
    assert 'ctx["_provider_compact_in_progress"] = True' in barrier_block
    assert 'ctx.pop("_provider_compact_in_progress", None)' in barrier_block

    append_block = src[
        src.index("def _append(msg: LLMMessage):"):
        src.index("# Sync msg_id", src.index("def _append(msg: LLMMessage):"))]
    assert 'ctx.get("_provider_compact_in_progress")' in append_block
    assert 'msg.role in ("assistant", "tool")' in append_block
    assert "rejected late provider callback during compact" in append_block
    assert "raise CCCompactDetected" in append_block

    compact_check_block = src[
        src.index("def _maybe_auto_compact_after_append"):
        src.index("def _append(msg: LLMMessage):")]
    assert '_set_provider_compact_barrier(f"post_append:{reason}")' in compact_check_block
    assert '_auto_compact_state["handoff"] = True' in compact_check_block
    assert 'if not _auto_compact_state.get("handoff")' in compact_check_block

    restart_block = src[
        src.index("except CCCompactDetected:"):
        src.index("except Exception as llm_err:", src.index("except CCCompactDetected:"))]
    assert '_set_provider_compact_barrier("provider_compact_detected")' in restart_block
    assert "_clear_provider_compact_barrier()" in restart_block


def test_done_hotpath_does_not_compute_context_usage():
    src = Path("tasks/ai/agent_core.py").read_text(encoding="utf-8")
    cached_source = src[
        src.index("def _agent_source_cached"):
        src.index("def _patch_cc_turn_gauge")]
    assert "include_context=False" in cached_source
    assert "compute_context_usage" not in cached_source

    done_block = src[
        src.index('result = _make_result()'):
        src.index('return result', src.index('logger.info("[agent:%s] enqueueing done'))]
    done_before_enqueue = done_block[:done_block.index('logger.info("[agent:%s] enqueueing done')]
    assert "compute_context_usage" not in done_before_enqueue
    assert "_agent_source(" not in done_before_enqueue


def test_relay_reconnect_shuts_down_command_pool():
    src = Path("pawflow_relay/worker.py").read_text(encoding="utf-8")
    cleanup = src[
        src.index("# Stop watchdog"):
        src.index("# Always close socket before reconnecting")]
    assert "locals().get('_pool')" in cleanup
    assert "shutdown(wait=False, cancel_futures=True)" in cleanup


def test_codex_app_marks_terminal_turn_callback_after_turn_completed():
    src = Path("core/llm_providers/codex_app_server.py").read_text(encoding="utf-8")
    assert "self._codex_app_turn_completed_for_callback = False" in src
    turn_done = src[
        src.index('if method == "turn/completed"'):
        src.index("break", src.index('if method == "turn/completed"'))]
    assert "self._codex_app_turn_completed_for_callback = True" in turn_done


def test_pending_wake_is_not_lost_while_conversation_is_still_active():
    loop_src = Path("tasks/ai/agent_loop.py").read_text(encoding="utf-8")
    poller_src = Path("tasks/ai/agent_poller.py").read_text(encoding="utf-8")
    assert "even_if_active: bool = False" in loop_src
    assert "and not even_if_active" in loop_src
    assert "pending::" in poller_src
    assert "hashlib_resched" in poller_src


def test_api_summarizer_preserves_thinking_signature_across_tool_loop():
    src = Path("tasks/ai/agent_summarize.py").read_text(encoding="utf-8")
    assert "thinking=getattr(response, \"thinking\", \"\") or \"\"" in src
    assert "thinking_signature=getattr(response, \"thinking_signature\", \"\") or \"\"" in src


def test_pre_send_compact_threshold_uses_sent_prompt_not_gauge():
    src = Path("tasks/ai/agent_core.py").read_text(encoding="utf-8")
    usage_block = src[
        src.index("def _auto_compact_usage"):
        src.index("def _maybe_auto_compact_after_append")]
    threshold_block = src[
        src.index("def _should_proactive_compact"):
        src.index("def _messages_changed")]
    pre_send_block = src[
        src.index("if _trigger_frac > 0:", src.index("# Force-fit guard")):
        src.index("if _pre_send_est > _max_ctx:")]
    assert "compute_context_usage" in usage_block
    assert "context_usage_from_cache" not in usage_block
    assert "_threshold_estimate(stored_msgs, cpt)" in threshold_block
    assert "_auto_compact_usage(" not in threshold_block
    assert "_threshold_used = _pre_send_est" in pre_send_block
    assert "_auto_compact_usage(" not in pre_send_block


def test_ui_actions_have_no_sync_allowlist_and_use_reply_bus():
    actions_src = Path("tasks/ai/agent_actions.py").read_text(encoding="utf-8")
    rxbus_src = Path("tasks/io/chat_ui/rxbus.js").read_text(encoding="utf-8")

    assert "_SYNC_ACTIONS" not in actions_src
    assert "action in _SYNC_ACTIONS" not in actions_src
    assert "_reply_conversation_id" in actions_src
    assert "reply_conversation_id, \"command_result\"" in actions_src
    assert "body._call_id = _callId" in rxbus_src
    assert "body._reply_conversation_id = _uiActionConversationId()" in rxbus_src
    assert "new EventSource(url)" in rxbus_src
    assert "new rxjs.ReplaySubject" in rxbus_src
    assert "r._callId !== _callId" in rxbus_src
    assert "list_ui_action_status" in actions_src
    assert "list_ui_action_status" in rxbus_src
    assert "setTimeout(() =>" not in rxbus_src[rxbus_src.index("function action$"):]


def test_agent_background_llm_calls_pass_provider_agnostic_scope():
    """Agent-side auxiliary LLM calls must behave the same for every provider.

    CLI providers need call_* identity for sessions/events; API providers need
    the same scope to resolve FileStore/image attachments. Passing it everywhere
    keeps the selected llm_service irrelevant to the task semantics.
    """
    expected = [
        ("tasks/ai/agent_summarize.py", "call_scope = {"),
        ("tasks/ai/agent_summarize.py", "read_handler.set_user_id(user_id)"),
        ("tasks/ai/agent_core.py", "_interrupt_call_kwargs = {"),
        ("tasks/ai/agent_compaction.py", "_synth_call_kwargs = {"),
        ("tasks/ai/agent_streaming.py", "call_agent_name=\"title\""),
        ("tasks/ai/agent_side_channels.py", "_btw_call_kwargs = {"),
        ("core/agent_executor.py", "_delegate_call_kwargs"),
        ("core/agent_executor.py", "**(call_kwargs or {})"),
        ("core/handlers/learn.py", "call_agent_name=self._agent_name or \"learn\""),
        ("core/memory_auto_extract.py", "call_agent_name=\"memory\""),
    ]
    for path, marker in expected:
        assert marker in Path(path).read_text(encoding="utf-8")


def test_sub_agent_provider_compact_is_provider_agnostic():
    src = Path("core/agent_executor.py").read_text(encoding="utf-8")
    block = src[src.index("except Exception as _llm_err:"):
                src.index("# Recover OAuth tokens", src.index("except Exception as _llm_err:"))]

    assert "from core.llm_client import CCCompactDetected" in block
    assert "isinstance(" in block
    assert "_llm_err, CCCompactDetected" in block
    assert "contextCompaction" in block
    assert "invalidate_claude_session_for_agent" in block
    assert "set_extra(\n                                _delegate_conv_id,\n                                f\"claude_session:" not in block


def test_flash_delegate_is_registered_and_prompted():
    registry_src = Path("core/tool_registry.py").read_text(encoding="utf-8")
    adapter_src = Path("core/tool_task_adapter.py").read_text(encoding="utf-8")
    prompt_src = Path("core/agent_prompt_policy.py").read_text(encoding="utf-8")

    assert "FlashAgentHandler" in registry_src
    assert "registry.register(FlashAgentHandler())" in registry_src
    assert '"flash_delegate"' in adapter_src
    assert "temporary flash agents" in prompt_src
    assert "empty context" in prompt_src
    assert "uses your current LLM service" in prompt_src


def test_relay_desktop_has_periodic_healthcheck():
    src = Path("pawflow_relay/worker.py").read_text(encoding="utf-8")
    assert "def _desktop_is_healthy" in src
    assert "def _start_desktop_watchdog" in src
    assert "desktop-healthcheck" in src
    assert "healthcheck failed" in src


def test_relay_desktop_waits_for_reachable_novnc():
    src = Path("pawflow_relay/worker.py").read_text(encoding="utf-8")
    start_desktop = src[src.index('if action == "start_desktop"'):src.index('if action == "stop_desktop"')]

    assert 'f"0.0.0.0:{_novnc_port}"' in start_desktop
    assert 'GET /vnc.html HTTP/1.1' in start_desktop
    assert "noVNC failed to become ready" in start_desktop


def test_vnc_proxy_retries_backend_startup_window():
    src = Path("services/vnc_proxy.py").read_text(encoding="utf-8")

    assert "deadline = time.time() + 8" in src
    assert "time.sleep(0.2)" in src
    assert "Backend unavailable" in src


def test_bg_bucket_is_independent_from_foreground_agent_state():
    """Background pyramid jobs must not touch foreground agent state.

    The builder works from persisted shared/transcript snapshots. It must not
    inspect PendingQueue/AgentLoopTask liveness and must not invalidate or
    mutate foreground provider sessions.
    """
    src = Path("core/bg_bucket_builder.py").read_text(encoding="utf-8")
    assert "ThreadPoolExecutor" in src
    assert "thread_name_prefix=\"bg-bucket\"" in src
    assert "seq cache cold — seeding asynchronously" in src
    forbidden = (
        "PendingQueue",
        "from tasks.ai.agent_loop",
        "tasks.ai.agent_loop",
        "foreground busy",
        "_foreground_busy_reason",
        "invalidate_claude_session",
        "save_agent_context(",
        "set_extra(",
        "codex_app_server_thread:",
    )
    for term in forbidden:
        assert term not in src

    store_src = Path("core/conversation_store.py").read_text(encoding="utf-8")
    assert "maybe_trigger_async" in store_src


def test_bg_bucket_trace_does_not_load_full_transcript():
    src = Path("core/bg_bucket_builder.py").read_text(encoding="utf-8")
    start = src.index("    def _extract_trace")
    end = src.index("    def _pick_chunk", start)
    body = src[start:end]

    assert "load_transcript_seq_range" in body
    assert ".load(cid)" not in body
    assert "cs.load(" not in body


def test_provider_compact_uses_bounded_transcript_tail():
    start = _AGENT_CORE_PY.index("provider compact detected")
    end = _AGENT_CORE_PY.index("PawFlow compact done", start)
    body = _AGENT_CORE_PY[start:end]

    assert "COMPACT_TAIL_MESSAGES = 250" in _AGENT_COMPACTION_PY
    assert "_compact_context_from_store" in body
    assert "tail_limit=COMPACT_TAIL_MESSAGES" in body
    assert "load_transcript_tail_for_agent" not in body
    assert "load_transcript_for_agent" not in body
    assert "Loaded %d compact source messages" in _AGENT_COMPACTION_PY
    assert "Loaded %d messages from shared context for compaction" not in body


def test_manual_compact_uses_same_bounded_source_loader():
    start = _CONTEXT_OPS_PY.index('    if action == "compact":')
    end = _CONTEXT_OPS_PY.index('    if action == "rebuild":', start)
    body = _CONTEXT_OPS_PY[start:end]

    assert "_compact_context_from_store" in body
    assert "_get_summarizer_client" in body
    assert "No summarizer service configured — compaction needs one" not in body
    assert "load_transcript_for_agent" not in body
    assert "load_context" not in body
    assert "store.load(conv_id" not in body


def test_context_editor_never_treats_transcript_as_agent_context():
    """Transcript edits/deletes must use transcript actions; context
    mutations must target Shared or a real agent context, not an accidental
    agent named 'transcript'."""
    src = Path("tasks/io/chat_ui/context_editor.js").read_text(encoding="utf-8")
    assert "function _ctxScopedAgentName()" in src
    assert "_ctxAgentFilter !== 'transcript'" in src
    assert "edit_message" in src
    assert "delete_context_messages" in src


def test_missing_agent_context_is_seeded_from_shared_before_first_append():
    """The first message routed to a new agent context must not create a
    one-message private context. The store seeds the agent file from shared
    before appending the current row; context preparation then treats an
    existing agent context as authoritative.
    """
    store_src = Path("core/conversation_store.py").read_text(encoding="utf-8")
    assert "def _seed_agent_context_from_shared_if_missing" in store_src
    assert "copy the current shared context personalized for this agent" in store_src
    assert "_seed_agent_context_from_shared_if_missing(\n                            cid, route_agent)" in store_src

    src = Path("tasks/ai/agent_context.py").read_text(encoding="utf-8")
    assert "def _load_pawflow_initial_context" in src
    assert "store.load_shared_for_agent" in src
    assert "Agent context exists: use it as the PawFlow agent" in src
    assert "No established agent context: build it from PawFlow" in src
    assert "cold CLI session has truncated agent context" not in src
    assert "len(_cold_cli_initial) > len(messages)" not in src
    assert "_materialize_pawflow_initial_context" in src
    assert "_pawflow_initial_context_source" in src
    assert "gemini_acp_session_version" in src
    assert "codex_app_server_thread" in src

    core_src = Path("tasks/ai/agent_core.py").read_text(encoding="utf-8")
    assert "materialized PawFlow initial" in core_src
    assert "save_agent_context(" in core_src


def test_force_stop_kills_cli_processes_and_blocks_late_appends():
    """Force stop is a hard stop: kill live CLI containers and reject late
    provider/tool callbacks before they can persist or publish messages.
    """
    cancel_src = Path("tasks/ai/actions/cancel_interrupt.py").read_text(encoding="utf-8")
    loop_src = Path("tasks/ai/agent_loop.py").read_text(encoding="utf-8")
    core_src = Path("tasks/ai/agent_core.py").read_text(encoding="utf-8")
    openai_src = Path("core/llm_providers/openai.py").read_text(encoding="utf-8")
    codex_app_src = Path("core/llm_providers/codex_app_server.py").read_text(encoding="utf-8")
    assert "def _kill_live_cli_sessions" in cancel_src
    # The interactive CC pool is intentionally NOT force-killed: its
    # container is a persistent tmux session holding OAuth credentials
    # and is only soft-interrupted on force stop.
    assert "InteractiveClaudeCodePool" not in cancel_src
    assert "CodexLiveRegistry" in cancel_src
    assert "GeminiLiveRegistry" in cancel_src
    assert "LiveSessionRegistry" in cancel_src
    anthropic_src = Path("core/llm_providers/anthropic.py").read_text(encoding="utf-8")
    llm_client_src = Path("core/llm_client.py").read_text(encoding="utf-8")
    assert "_kill_live_cli_sessions(conv_id, agent_name, \"force_stop\")" in cancel_src
    assert "client.abort()" in cancel_src
    assert "or getattr(_cc, 'abort', None)" in loop_src
    assert "or getattr(_cc, 'cancel_codex', None)" in loop_src
    assert "hasattr(client, 'send_user_message') or hasattr(client, 'abort')" in core_src
    assert "ToolRelayService.uncancel_agent(" in core_src
    assert "self._active_http_conn = conn" in openai_src
    assert "self._active_http_conn = conn" in anthropic_src
    assert "conn.close()" in llm_client_src
    assert "self._abort.is_set()" in openai_src
    assert "self._abort.is_set()" in anthropic_src
    assert "self._abort.is_set()" in codex_app_src
    assert "_codex_app_abort_active" in codex_app_src
    assert "proc.kill()" in codex_app_src
    assert "Codex app-server abort failed" in llm_client_src
    assert "raise AgentCancelled()" in openai_src
    assert "raise AgentCancelled()" in anthropic_src
    assert "raise AgentCancelled()" in codex_app_src
    assert "emitter.check_cancelled()" in core_src
    assert core_src.index("emitter.check_cancelled()") < core_src.index("thinking-only live delta")
    assert core_src.index("thinking-only live delta") < core_src.index("ConversationWriter.for_conversation(conversation_id)")
    assert "emitter.check_cancelled()\n                        _cc_turn_count" in core_src


def test_soft_interrupt_live_stop_is_not_persisted_for_api_fallback():
    policy_src = Path("core/interrupt_policy.py").read_text(encoding="utf-8")
    loop_src = Path("tasks/ai/agent_loop.py").read_text(encoding="utf-8")
    core_src = Path("tasks/ai/agent_core.py").read_text(encoding="utf-8")
    cc_src = Path("core/llm_providers/claude_code.py").read_text(encoding="utf-8")
    assert "STOP IMMEDIATELY!" in policy_src
    assert "send_user_message(" in loop_src
    assert "SOFT_INTERRUPT_USER_COMMAND" in loop_src
    assert "\"content\": SOFT_INTERRUPT_USER_COMMAND" not in loop_src
    assert "SOFT_INTERRUPT_USER_COMMAND" in core_src
    assert "SOFT_INTERRUPT_USER_COMMAND" in cc_src
    assert "STOP IMMEDIATELY!" not in loop_src
    assert "STOP IMMEDIATELY!" not in core_src
    assert "STOP IMMEDIATELY!" not in cc_src
    assert "[System: INTERRUPTED" not in loop_src


# ---------------------------------------------------------------------------
# Tiny brace-counting JS function-body extractor — plenty for our checks.
# ---------------------------------------------------------------------------


def _extract_function_body(src: str, fname: str) -> str:
    m = re.search(rf"function\s+{re.escape(fname)}\s*\([^)]*\)\s*\{{", src)
    if not m:
        raise AssertionError(f"function {fname} not found")
    start = m.end()
    depth = 1
    i = start
    while i < len(src) and depth > 0:
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        i += 1
    return src[start:i - 1]


def _extract_action_block(src: str, marker: str) -> str:
    start = src.index(marker)
    next_marker = src.find('\n    if action == "', start + len(marker))
    return src[start:next_marker if next_marker != -1 else len(src)]

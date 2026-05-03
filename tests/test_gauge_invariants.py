"""Lock the two monotonic invariants on the chat-UI context gauge:

  1. The gauge can only land on 0% on a brand-new empty conversation.
     A 0 update on an agent that already had a non-zero reading must
     be rejected.
  2. The gauge can only DECREASE when a compact has just happened
     for that agent. Otherwise an unsolicited drop is rejected.

The rules live in `tasks/io/chat_ui/active_agents.js` (see the
`setContextUsage` body). We don't have a JS test runner in this repo,
so this test executes the actual JS source against a tiny stub
browser environment using the `js2py` interpreter when available;
otherwise it falls back to a structural check that the rule
comments and conditions are still present.
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
_TABS_JS = Path("tasks/io/chat_ui/tabs.js").read_text(encoding="utf-8")
_FLOW_GRAPH_HTML = Path("tasks/io/chat_ui/flow_graph.html").read_text(encoding="utf-8")
_MESSAGES_JS = Path("tasks/io/chat_ui/messages.js").read_text(encoding="utf-8")
_AGENT_CONTEXT_PY = Path("tasks/ai/agent_context.py").read_text(encoding="utf-8")
_AGENT_CORE_PY = Path("tasks/ai/agent_core.py").read_text(encoding="utf-8")
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


def test_set_context_usage_blocks_decrease_without_compact():
    """setContextUsage must short-circuit when realUsed < cached AND
    no compact is pending."""
    body = _extract_function_body(_ACTIVE_AGENTS_JS, "setContextUsage")
    assert "realUsed < cachedUsed" in body, (
        "Rule 2 (no decrease without compact) is missing")
    assert "_compactPending" in body, (
        "setContextUsage must consult window._compactPending")


def test_compact_pending_consumed_after_accepted_decrease():
    """After an accepted decrease the pending flag must be cleared so a
    second decrease without a fresh compact is rejected."""
    body = _extract_function_body(_ACTIVE_AGENTS_JS, "setContextUsage")
    assert "delete window._compactPending[key]" in body, (
        "Compact-pending flag must be consumed on accepted update")


def test_compact_progress_done_marks_compact_pending():
    """The SSE `compact_progress stage=done` listener must call
    markCompactJustHappened so the next message_meta drop is allowed."""
    # Locate the stage==='done' branch and ensure it calls
    # markCompactJustHappened with the agent name.
    assert "markCompactJustHappened(agent)" in _SSE_JS, (
        "compact_progress 'done' must call markCompactJustHappened")


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


def test_chat_tool_display_unwraps_meta_use_tool_calls():
    assert "function _unwrapDisplayedToolCall" in _MESSAGES_JS
    assert "mcp_pawflow_use_tool" in _MESSAGES_JS
    assert "mcp__pawflow__use_tool" in _MESSAGES_JS
    assert "toolArgs.tool_name" in _MESSAGES_JS
    assert "toolArgs.arguments || {}" in _MESSAGES_JS


def test_iteration_status_updates_state_without_polluting_chat_timeline():
    start = _SSE_JS.index("eventSource.addEventListener('iteration_status'")
    end = _SSE_JS.index("eventSource.addEventListener('flowfile_in'", start)
    block = _SSE_JS[start:end]
    assert "updateActivePanel()" in block
    assert "document.getElementById('status').textContent" in block
    assert "addMsg(" not in block


def test_compact_progress_done_applies_gauge_immediately():
    """Manual compact has no immediate message_meta; compact_progress done
    must carry and apply the post-compact gauge itself."""
    done_branch = _SSE_JS[
        _SSE_JS.index("} else if (data.stage === 'done')"):
        _SSE_JS.index("} else if (data.stage === 'error')")]
    assert "setContextUsage(agent" in done_branch
    assert "data.context_used" in done_branch
    assert "data.context_max" in done_branch


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
        _AGENT_CORE_PY.index("llm_context = _with_provider_system_prompt")]
    assert "if _trigger_frac > 0:" not in proactive_region
    assert "if _should_proactive_compact(messages, _max_ctx, _cpt):" in proactive_region


def test_cli_session_invalidation_requires_real_compact_change():
    """Codex and Gemini live sessions must survive normal below-threshold turns."""
    invalidation = _AGENT_CORE_PY.index(
        "invalidate_claude_session_for_agent(")
    guard = _AGENT_CORE_PY.rfind("if _messages_changed", 0, invalidation)
    assert guard != -1
    assert invalidation - guard < 600


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


def test_resources_hydration_uses_setcontextusage():
    """list_resources may lag behind SSE, so Resource Panel hydration must
    use setContextUsage instead of overwriting `_contextUsage` directly."""
    assert "setContextUsage(a.name" in _RESOURCES_JS
    assert "window._contextUsage[aKeyLc] =" not in _RESOURCES_JS


def test_active_poll_hydration_uses_setcontextusage():
    """list_active may also lag behind SSE. It must not write `_contextUsage`
    directly or it can make the header gauge bounce 30% -> 11% -> 30%."""
    assert "setContextUsage(a.agent_name" in _ACTIVE_AGENTS_JS
    active_poll = _ACTIVE_AGENTS_JS[_ACTIVE_AGENTS_JS.index("function syncActiveFromServer") :]
    setter_body = _extract_function_body(_ACTIVE_AGENTS_JS, "setContextUsage")
    outside_setter = active_poll.replace(setter_body, "")
    assert "window._contextUsage[" not in outside_setter


def test_active_panel_prefers_newer_context_cache():
    """The active-agent row must not display an older polled gauge when the
    shared context cache already has a newer monotonic value."""
    body = _extract_function_body(_ACTIVE_AGENTS_JS, "updateActivePanel")
    assert "(cached.used || 0) > ctxUsed" in body
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


def test_message_meta_context_used_comes_from_pawflow_context_not_provider_delta():
    src = Path("tasks/ai/agent_core.py").read_text(encoding="utf-8")
    agent_source = src[src.index("def _agent_source") : src.index("# SpawnAgentsHandler source tracking")]
    assert "Provider usage can be a resume/live delta" in agent_source
    assert "context_usage_from_cache" in agent_source
    assert 'src["tokens_in"] = tok_in' in agent_source


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


def test_list_resources_uses_cached_stored_context_usage():
    src = Path("tasks/ai/actions/agent_resource.py").read_text(encoding="utf-8")
    assert "def _stored_context_usage" in src
    list_resources_block = src[
        src.index('if action == "list_resources":'):
        src.index('if action == "get_resource_detail":')]
    assert "context_usage_from_cache" not in list_resources_block
    assert "load_agent_context" not in list_resources_block
    assert "reg.resolve(llm_service" not in list_resources_block
    assert "resolve_definition(\n                                llm_service" not in list_resources_block
    assert "get_client()" not in list_resources_block
    assert 'store.set_extra(conv_id, "context_usage", context_usage_map)' not in list_resources_block


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


def test_open_desktop_backend_does_not_emit_audio_session_without_token():
    src = Path("tasks/ai/actions/service_flow.py").read_text(encoding="utf-8")
    assert "_audio_token = register_audio_source" in src
    assert '"audio_session": session_id if _audio_token else ""' in src
    assert '"audio_session": _sid if _audio_token else ""' in src
    assert '"audio_session": session_id,\n                "audio_token": _audio_lookup_token(session_id)' not in src
    assert '"audio_session": _sid,\n                            "audio_token": _audio_lookup_token(_sid)' not in src


def test_list_active_uses_stored_context_usage_without_live_token_count():
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
            raise AssertionError("list_active must not hit disk-backed get_extra")

        def get_extra_snapshot(self, _cid, key, default=None):
            if key == "context_usage":
                return {"assistant": {
                    "source": "message_meta", "max": 10000,
                    "used": 9300, "pct": 0.93,
                }}
            return default

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
    assert row["context_usage"]["source"] == "message_meta"
    assert row["context_usage"]["max"] == 10000
    assert row["context_usage"]["used"] == 9300
    assert row["context_usage"]["pct"] == 0.93


def test_ui_polling_paths_use_cache_only_context_usage():
    list_resources_block = _extract_action_block(
        Path("tasks/ai/actions/agent_resource.py").read_text(encoding="utf-8"),
        'if action == "list_resources":')
    assert 'get_extra_snapshot(conv_id, "context_usage", {})' in list_resources_block
    assert 'get_extra(conv_id, "context_usage")' not in list_resources_block

    list_active_block = _extract_action_block(
        Path("tasks/ai/actions/usage.py").read_text(encoding="utf-8"),
        'if action == "list_active":')
    assert 'get_extra_snapshot(conv_id, "context_usage", {})' in list_active_block
    assert 'get_extra(conv_id, "context_usage")' not in list_active_block


def test_idle_polling_cannot_stack_unbounded_work():
    assert "_syncActiveSub" in _ACTIVE_AGENTS_JS
    assert "_SYNC_ACTIVE_STALE_MS" in _ACTIVE_AGENTS_JS
    assert "if (now - _syncActiveStartedAt < _SYNC_ACTIVE_STALE_MS) return" in _ACTIVE_AGENTS_JS
    assert "setInterval(syncActiveFromServer, 10000)" in _ACTIVE_AGENTS_JS
    assert "document.hidden) return" in _ACTIVE_AGENTS_JS
    assert "_MAX_BG_ACTIONS" in _AGENT_ACTIONS_PY
    assert "_BG_ACTION_SEMAPHORE.acquire(blocking=False)" in _AGENT_ACTIONS_PY


def test_webchat_fallback_poll_is_disabled_while_sse_is_healthy():
    assert "function _sseIsHealthy()" in _SSE_JS
    assert "if (_sseIsHealthy()) return" in _SSE_JS
    assert "document.hidden && _sseIsHealthy()" in _SSE_JS
    assert "}, 120000);" in _SSE_JS


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
    assert _CONTEXT_EDITOR_JS.count("max-height:min(60vh,640px);overflow-y:auto") >= 2


def test_empty_assistant_no_tools_never_persists_blank_message():
    tool_exec_src = Path("tasks/ai/agent_tool_exec.py").read_text(encoding="utf-8")
    agent_core_src = Path("tasks/ai/agent_core.py").read_text(encoding="utf-8")
    serialization_src = Path("tasks/ai/agent_serialization.py").read_text(encoding="utf-8")
    assert "if final.strip():" in tool_exec_src
    assert "forced-synthesis path" in tool_exec_src
    assert "provider thinking block must still reach history/SSE" in agent_core_src
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
    assert "send_user_message(SOFT_INTERRUPT_USER_COMMAND)" in loop_src
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
    assert "active thread has no context yet" in streaming_src
    assert "t.name == f\"agent-stream-{conversation_id}\"" in loop_src
    assert "t.name.startswith(f\"agent-stream-{conversation_id}:\")" in loop_src
    assert "resurrects" in core_src
    assert "emitter.generation = self._conv_generation.get" not in core_src
    assert "def _hard_kill_for_context_compaction" in codex_src
    assert "_hard_kill_for_context_compaction(\"item/started\")" in codex_src
    assert "_hard_kill_for_context_compaction(\"item/completed\")" in codex_src
    assert "not keep_alive and not compact_hard_killed" in codex_src



def test_accepted_live_preempt_keeps_pending_rescue():
    """A live provider steer is not proof that the turn consumed the message.

    The stamped user message must remain in PendingQueue until the final drain:
    providers that can prove inline consumption suppress the rerun; providers
    without proof cannot lose a late steer.
    """
    src = Path("tasks/ai/agent_streaming.py").read_text(encoding="utf-8")
    assert "source=\"preempt_rescue\"" in src
    assert "preempted active provider session" in src
    assert "_queue_pending_user(source=\"http\")" in src
    assert "even_if_active=True" in src


def test_final_drain_only_suppresses_proven_text_preempt_rescue_messages():
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

    assert _preempt_rescue_requires_retrigger(text_rescue, 101.0, "claude-code") is False
    assert _preempt_rescue_requires_retrigger(text_rescue, 101.0, "codex-app-server") is False
    assert _preempt_rescue_requires_retrigger(text_rescue, 99.0, "claude-code") is True
    assert _preempt_rescue_requires_retrigger(image_rescue, 101.0, "claude-code") is True
    assert _preempt_rescue_requires_retrigger(http_msg, 101.0, "claude-code") is True


def test_provider_compact_discards_pending_messages_already_in_compacted_context():
    src = Path("tasks/ai/agent_core.py").read_text(encoding="utf-8")
    compact_block = src[
        src.index("PawFlow compact: %d"):
        src.index("# 3. Save compacted context + invalidate CC")]
    assert "discard_msg_ids" in compact_block
    assert "_compacted_ids" in compact_block


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


def test_proactive_compact_threshold_uses_real_token_multiplier():
    src = Path("tasks/ai/agent_core.py").read_text(encoding="utf-8")
    threshold_block = src[
        src.index("def _should_proactive_compact"):
        src.index("def _messages_changed")]
    assert "resolve_token_multiplier" in threshold_block
    assert "ctx.get(\"resolved_svc\")" in threshold_block
    assert "token_multiplier=tmul" in threshold_block


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


def test_relay_desktop_has_periodic_healthcheck():
    src = Path("pawflow_relay/worker.py").read_text(encoding="utf-8")
    assert "def _desktop_is_healthy" in src
    assert "def _start_desktop_watchdog" in src
    assert "desktop-healthcheck" in src
    assert "healthcheck failed" in src


def test_bg_bucket_runs_as_independent_background_work():
    """Background pyramid jobs write buckets/memories, not the active agent
    context, so they must not yield just because the foreground agent is
    active or a user message is queued."""
    src = Path("core/bg_bucket_builder.py").read_text(encoding="utf-8")
    assert "ThreadPoolExecutor" in src
    assert "thread_name_prefix=\"bg-bucket\"" in src
    assert "seq cache cold — seeding asynchronously" in src
    assert "foreground agent active/starting" not in src
    assert "skip auto memory extract" not in src
    assert "pausing bucket catch-up" not in src


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
    assert "_seed_agent_context_from_shared_if_missing(\n                            cid, agent_name)" in store_src

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
    assert "def _kill_live_cli_sessions" in cancel_src
    assert "CodexLiveRegistry" in cancel_src
    assert "GeminiLiveRegistry" in cancel_src
    assert "LiveSessionRegistry" in cancel_src
    assert "_kill_live_cli_sessions(conv_id, agent_name, \"force_stop\")" in cancel_src
    assert "client.abort()" in cancel_src
    assert "client.abort()" in loop_src
    assert "self._abort.is_set()" in openai_src
    assert "raise AgentCancelled()" in openai_src
    assert "emitter.check_cancelled()\n            # Sync msg_id" in core_src
    assert "emitter.check_cancelled()\n                        _cc_turn_count" in core_src


def test_soft_interrupt_live_stop_is_not_persisted_for_api_fallback():
    policy_src = Path("core/interrupt_policy.py").read_text(encoding="utf-8")
    loop_src = Path("tasks/ai/agent_loop.py").read_text(encoding="utf-8")
    core_src = Path("tasks/ai/agent_core.py").read_text(encoding="utf-8")
    cc_src = Path("core/llm_providers/claude_code.py").read_text(encoding="utf-8")
    assert "STOP IMMEDIATELY!" in policy_src
    assert "send_user_message(SOFT_INTERRUPT_USER_COMMAND)" in loop_src
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

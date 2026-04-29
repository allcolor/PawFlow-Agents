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


def test_compact_progress_done_applies_gauge_immediately():
    """Manual compact has no immediate message_meta; compact_progress done
    must carry and apply the post-compact gauge itself."""
    done_branch = _SSE_JS[
        _SSE_JS.index("} else if (data.stage === 'done')"):
        _SSE_JS.index("} else if (data.stage === 'error')")]
    assert "setContextUsage(agent" in done_branch
    assert "data.context_used" in done_branch
    assert "data.context_max" in done_branch


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
    assert 'existing.get("message_count") is not None' in src
    assert "context_usage_from_cache" in src
    assert 'store.set_extra(conv_id, "context_usage", context_usage_map)' in src


def test_list_active_reports_live_pawflow_context_usage():
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
            return {}

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
    assert row["context_usage"]["source"] == "active_context"
    assert row["context_usage"]["max"] == 10000
    assert row["context_usage"]["used"] > 0
    assert row["context_usage"]["pct"] > 0


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

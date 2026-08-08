"""Regression coverage for reused interactive CLI badges in Active Agents."""

import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ACTIVE_AGENTS_JS = Path("tasks/io/chat_ui/active_agents.js").read_text(
    encoding="utf-8"
)


class _Store:
    def resolve_owner(self, _conversation_id):
        return "user"


def _session(agent_name, provider):
    return {
        "conv_id": "conv-live",
        "agent_name": agent_name,
        "service_id": "svc",
        "container_name": "container-" + agent_name,
        "live": True,
        "reuse_count": 1,
        "idle_seconds": 2,
        "lived_seconds": 42,
        "provider": provider,
    }


def test_list_active_enriches_both_interactive_provider_rows():
    from core import FlowFile
    from tasks.ai.actions.usage import _handle_usage

    fake_exec = SimpleNamespace(
        _active_turns={},
        _active_contexts={
            "conv-live:cc-agent": {
                "active_agent_name": "cc-agent",
                "active_llm_provider": "claude-code",
                "_started_at": 0,
            },
            "conv-live:cci-agent": {
                "active_agent_name": "cci-agent",
                "active_llm_provider": "claude-code-interactive",
                "_started_at": 0,
            },
            "conv-live:codex-agent": {
                "active_agent_name": "codex-agent",
                "active_llm_provider": "codex-interactive",
                "_started_at": 0,
            },
        },
        _active_contexts_lock=threading.Lock(),
    )
    cci = _session("cci-agent", "claude-code-interactive")
    codex_interactive = _session("codex-agent", "codex-interactive")
    cc = _session("cc-agent", "claude-code")
    cc["reuse_count"] = 0

    with patch(
        "tasks.ai.agent_loop.AgentLoopTask._live_instance", fake_exec
    ), patch(
        "core.cc_live_registry.LiveSessionRegistry.instance"
    ) as cc_registry, patch(
        "core.claude_code_interactive_pool.InteractiveClaudeCodePool.instance"
    ) as cci_pool, patch(
        "core.codex_interactive_pool.CodexInteractivePool.instance"
    ) as codex_interactive_pool, patch(
        "core.codex_live_registry.CodexLiveRegistry.instance"
    ) as codex_registry, patch(
        "core.gemini_live_registry.GeminiLiveRegistry.instance"
    ) as gemini_registry:
        cc_registry.return_value.status.return_value = [cc]
        cci_pool.return_value.list_sessions_snapshot.return_value = [cci]
        codex_interactive_pool.return_value.list_sessions_snapshot.return_value = [
            codex_interactive
        ]
        codex_registry.return_value.status.return_value = []
        gemini_registry.return_value.status.return_value = []

        flowfile = FlowFile()
        out = _handle_usage(
            SimpleNamespace(),
            "list_active",
            {"conversation_id": "conv-live"},
            _Store(),
            "user",
            flowfile,
        )

    payload = json.loads(out[0].get_content().decode("utf-8"))
    rows = {row["agent_name"]: row for row in payload["active"]}

    assert rows["cci-agent"]["cci_live"] is True
    assert rows["cci-agent"]["cci_reuse_count"] == 1
    assert rows["codex-agent"]["codex_interactive_live"] is True
    assert rows["codex-agent"]["codex_interactive_reuse_count"] == 1
    assert rows["cc-agent"]["cc_live"] is True
    assert rows["cc-agent"]["cc_reuse_count"] == 0
    assert payload["cci_live"] == [cci]
    assert payload["codex_interactive_live"] == [codex_interactive]


def test_active_agents_maps_and_renders_interactive_live_telemetry():
    assert "cciLive: !!a.cci_live" in ACTIVE_AGENTS_JS
    assert (
        "codexInteractiveLive: !!a.codex_interactive_live"
        in ACTIVE_AGENTS_JS
    )
    assert "info.cciLive ? 'cci'" in ACTIVE_AGENTS_JS
    assert "info.codexInteractiveLive ? 'codex-interactive'" in ACTIVE_AGENTS_JS
    assert "Claude Code Interactive" in ACTIVE_AGENTS_JS
    assert "Codex Interactive" in ACTIVE_AGENTS_JS

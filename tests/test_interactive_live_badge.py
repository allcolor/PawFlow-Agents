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
        cci_pool.return_value.list_sessions.return_value = [cci]
        codex_interactive_pool.return_value.list_sessions.return_value = [
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
    # cc -p cold start (reuse_count=0): no LIVE badge. The badge means
    # "reuses a warm container", not "an active turn is running".
    assert rows["cc-agent"]["cc_live"] is False
    assert rows["cc-agent"]["cc_reuse_count"] == 0
    assert payload["cci_live"] == [cci]
    assert payload["codex_interactive_live"] == [codex_interactive]


def test_cli_active_context_without_runtime_is_not_surfaced():
    """A CLI marker without its process is stale, not active work."""
    from core import FlowFile
    from tasks.ai.actions.usage import _handle_usage

    fake_exec = SimpleNamespace(
        _active_turns={
            "conv-live:codex-agent": {
                "agent_name": "codex-agent",
                "active_llm_provider": "codex-interactive",
                "started_at": 1,
            },
        },
        _active_contexts={
            "conv-live:codex-agent": {
                "active_agent_name": "codex-agent",
                "_started_at": 1,
                # Deliberately not populated yet.
            },
        },
        _active_contexts_lock=threading.Lock(),
    )

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
        cc_registry.return_value.status.return_value = []
        cci_pool.return_value.list_sessions.return_value = []
        codex_interactive_pool.return_value.list_sessions.return_value = []
        codex_registry.return_value.status.return_value = []
        gemini_registry.return_value.status.return_value = []

        out = _handle_usage(
            SimpleNamespace(), "list_active",
            {"conversation_id": "conv-live"}, _Store(), "user", FlowFile())

    payload = json.loads(out[0].get_content().decode("utf-8"))
    assert payload["active"] == []


def test_active_context_merge_keeps_provider_with_real_runtime():
    """A partial context must not erase the provider from a live CLI turn."""
    from core import FlowFile
    from tasks.ai.actions.usage import _handle_usage

    fake_exec = SimpleNamespace(
        _active_turns={
            "conv-live:codex-agent": {
                "agent_name": "codex-agent",
                "active_llm_provider": "codex-interactive",
                "started_at": 1,
            },
        },
        _active_contexts={
            "conv-live:codex-agent": {
                "active_agent_name": "codex-agent",
                "_started_at": 1,
            },
        },
        _active_contexts_lock=threading.Lock(),
    )
    runtime = _session("codex-agent", "codex-interactive")

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
        cc_registry.return_value.status.return_value = []
        cci_pool.return_value.list_sessions.return_value = []
        codex_interactive_pool.return_value.list_sessions.return_value = [runtime]
        codex_registry.return_value.status.return_value = []
        gemini_registry.return_value.status.return_value = []

        out = _handle_usage(
            SimpleNamespace(), "list_active",
            {"conversation_id": "conv-live"}, _Store(), "user", FlowFile())

    row = json.loads(out[0].get_content().decode("utf-8"))["active"][0]
    assert row["agent_name"] == "codex-agent"
    assert row["active_llm_provider"] == "codex-interactive"
    assert row["codex_interactive_live"] is True


def test_reuse_count_drives_live_badge_not_active_turn():
    """The LIVE badge tracks reuse_count > 0, not whether a turn is active.

    Two active CCI turns in one conversation: one cold (reuse_count 0) and
    one reusing a warm container (reuse_count 3). Only the reused turn gets
    the badge.
    """
    from core import FlowFile
    from tasks.ai.actions.usage import _handle_usage

    fake_exec = SimpleNamespace(
        _active_turns={},
        _active_contexts={
            "conv-live:cold": {
                "active_agent_name": "cold",
                "active_llm_provider": "claude-code-interactive",
                "_started_at": 0,
            },
            "conv-live:warm": {
                "active_agent_name": "warm",
                "active_llm_provider": "claude-code-interactive",
                "_started_at": 0,
            },
        },
        _active_contexts_lock=threading.Lock(),
    )
    cold = _session("cold", "claude-code-interactive")
    cold["reuse_count"] = 0
    warm = _session("warm", "claude-code-interactive")
    warm["reuse_count"] = 3

    with patch(
        "tasks.ai.agent_loop.AgentLoopTask._live_instance", fake_exec
    ), patch(
        "core.claude_code_interactive_pool.InteractiveClaudeCodePool.instance"
    ) as cci_pool, patch(
        "core.cc_live_registry.LiveSessionRegistry.instance"
    ) as cc_registry, patch(
        "core.codex_interactive_pool.CodexInteractivePool.instance"
    ) as codex_interactive_pool, patch(
        "core.codex_live_registry.CodexLiveRegistry.instance"
    ) as codex_registry, patch(
        "core.gemini_live_registry.GeminiLiveRegistry.instance"
    ) as gemini_registry:
        cc_registry.return_value.status.return_value = []
        cci_pool.return_value.list_sessions.return_value = [cold, warm]
        codex_interactive_pool.return_value.list_sessions.return_value = []
        codex_registry.return_value.status.return_value = []
        gemini_registry.return_value.status.return_value = []

        flowfile = FlowFile()
        out = _handle_usage(
            SimpleNamespace(), "list_active",
            {"conversation_id": "conv-live"}, _Store(), "user", flowfile)

    rows = {r["agent_name"]: r for r in
            json.loads(out[0].get_content().decode("utf-8"))["active"]}
    assert rows["cold"]["cci_live"] is False
    assert rows["cold"]["cci_reuse_count"] == 0
    assert rows["warm"]["cci_live"] is True
    assert rows["warm"]["cci_reuse_count"] == 3


def test_list_active_requires_a_real_runtime_for_every_cli_provider():
    from core import FlowFile
    from tasks.ai.actions.usage import _handle_usage

    providers = {
        "cc": "claude-code",
        "cci": "claude-code-interactive",
        "codex-app": "codex-app-server",
        "codex-interactive": "codex-interactive",
        "gemini": "gemini",
        "antigravity": "antigravity-interactive",
        "api": "anthropic",
    }
    fake_exec = SimpleNamespace(
        _active_turns={},
        _active_contexts={
            f"conv-live:{agent}": {
                "active_agent_name": agent,
                "active_llm_provider": provider,
                "_started_at": 0,
            }
            for agent, provider in providers.items()
        },
        _active_contexts_lock=threading.Lock(),
    )

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
    ) as gemini_registry, patch(
        "core.antigravity_observer_pool.AntigravityObserverPool.instance"
    ) as antigravity_pool:
        cc_registry.return_value.status.return_value = []
        cci_pool.return_value.list_sessions.return_value = []
        codex_interactive_pool.return_value.list_sessions.return_value = []
        codex_registry.return_value.status.return_value = []
        gemini_registry.return_value.status.return_value = []
        antigravity_pool.return_value.list_sessions.return_value = []

        out = _handle_usage(
            SimpleNamespace(), "list_active",
            {"conversation_id": "conv-live"}, _Store(), "user", FlowFile())

    payload = json.loads(out[0].get_content().decode("utf-8"))
    assert [row["agent_name"] for row in payload["active"]] == ["api"]
    cci_pool.return_value.list_sessions.assert_called_once_with(
        "user", "conv-live")
    codex_interactive_pool.return_value.list_sessions.assert_called_once_with(
        "user", "conv-live")


def test_legacy_cli_status_uses_real_process_liveness():
    from core.codex_live_registry import CodexLiveRegistry
    from core.gemini_live_registry import GeminiLiveRegistry

    for registry_cls, attr in (
        (CodexLiveRegistry, "_containers"),
        (GeminiLiveRegistry, "_containers"),
    ):
        registry = registry_cls.__new__(registry_cls)
        registry._lock = threading.RLock()
        dead = SimpleNamespace(
            container_name="dead",
            active_turn=True,
            last_used=0,
            reuse_count=1,
            spawn_at=0,
            is_process_alive=lambda: False,
        )
        setattr(registry, attr, {
            ("user", "conv", "agent", "svc", 0): dead,
        })
        assert registry.status()[0]["live"] is False


def test_active_agents_maps_and_renders_interactive_live_telemetry():
    assert "cciLive: !!a.cci_live" in ACTIVE_AGENTS_JS
    assert (
        "codexInteractiveLive: !!a.codex_interactive_live"
        in ACTIVE_AGENTS_JS
    )
    assert "info.cciLive ? 'cci'" in ACTIVE_AGENTS_JS
    assert "info.codexInteractiveLive ? 'codex-interactive'" in ACTIVE_AGENTS_JS
    assert "function trackAgentProviderLive" in ACTIVE_AGENTS_JS
    assert "Claude Code Interactive" in ACTIVE_AGENTS_JS
    assert "Codex Interactive" in ACTIVE_AGENTS_JS

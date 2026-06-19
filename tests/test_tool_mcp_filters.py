"""Tool and MCP availability filter invariants."""

import json
from pathlib import Path

from core import FlowFile
from core.conversation_store import ConversationStore
from core.tool_mcp_filters import (
    disabled_names,
    enabled_dynamic_tool_names,
    enabled_mcp_names,
    is_enabled,
    is_tool_enabled,
    is_tool_enabled_from_filters,
    set_filters,
)
from tasks.ai.actions.agent_resource import _handle_agent_resource


def _flowfile():
    ff = FlowFile(b"")
    ff.set_attribute("http.auth.roles", "user")
    return ff


def _call_resource_action(action, body, user_id="u1"):
    ff = _flowfile()
    result = _handle_agent_resource(None, action, body, None, user_id, ff)
    assert result == [ff]
    return json.loads(ff.content.decode("utf-8"))


def test_tool_mcp_filters_inherit_and_agent_override(tmp_path):
    ConversationStore.reset()
    store = ConversationStore.instance()
    store._store_dir = tmp_path
    cid = "conv_filters"
    store.save(cid, [], user_id="u1")

    set_filters(cid, {
        "disabled_tools": ["bash"],
        "enabled_dynamic_tools": ["shared_tool"],
        "enabled_mcps": ["global_mcp"],
        "agent_overrides": {
            "assistant": {
                "tools": {"mode": "custom", "selected": ["read"]},
                "mcps": {"mode": "inherit", "enabled": []},
            }
        },
    })

    assert disabled_names(cid, kind="tools") == {"bash"}
    assert enabled_dynamic_tool_names(cid) == {"shared_tool"}
    assert enabled_mcp_names(cid, "assistant") == {"global_mcp"}
    assert is_tool_enabled(cid, "read", "assistant")
    assert not is_tool_enabled(cid, "bash", "assistant")
    assert not is_enabled(cid, "other_mcp", "assistant", kind="mcps")
    assert is_enabled(cid, "global_mcp", "assistant", kind="mcps")
    assert is_tool_enabled(cid, "conv_tool", origin="dynamic", origin_scope="conversation")
    assert not is_tool_enabled(cid, "user_tool", origin="dynamic", origin_scope="user")
    assert is_tool_enabled(cid, "shared_tool", origin="dynamic", origin_scope="user")
    filters = {
        "disabled_tools": ["bash"],
        "enabled_dynamic_tools": ["shared_tool"],
        "agent_overrides": {
            "assistant": {"tools": {"mode": "custom", "selected": ["read"]}}
        },
    }
    assert is_tool_enabled_from_filters(filters, "read", "assistant")
    assert not is_tool_enabled_from_filters(filters, "bash", "assistant")
    assert is_tool_enabled_from_filters(
        filters, "shared_tool", origin="dynamic", origin_scope="user")


def test_tool_relay_registry_filter_is_loaded_once_and_cached(monkeypatch, tmp_path):
    from core.tool_registry import ToolRegistry
    from services.tool_relay_service import ToolRelayService

    ConversationStore.reset()
    store = ConversationStore.instance()
    store._store_dir = tmp_path
    store.save("conv1", [], user_id="u1")

    class Handler:
        def __init__(self, name):
            self.name = name
            self.display_name = name
            self.description = ""

    def make_registry():
        registry = ToolRegistry()
        registry.register(Handler("read"))
        registry.register(Handler("bash"))
        registry.register(Handler("search"))
        return registry

    calls = []
    disabled = ["bash"]

    def fake_get_filters(conversation_id):
        calls.append(conversation_id)
        return {"disabled_tools": list(disabled), "agent_overrides": {}}

    ToolRelayService.clear_registry_cache()
    svc = ToolRelayService({})
    monkeypatch.setattr("core.tool_registry.create_default_registry", make_registry)
    monkeypatch.setattr("core.tool_mcp_filters.get_filters", fake_get_filters)
    monkeypatch.setattr(svc, "_load_mcp_tools", lambda *args, **kwargs: None)
    monkeypatch.setattr(svc, "_find_filesystem_service", lambda *args, **kwargs: None)

    first = svc._get_registry("u1", "conv1", "assistant")
    second = svc._get_registry("u1", "conv1", "assistant")

    assert first is second
    assert calls == ["conv1"]
    assert first.get("read") is not None
    assert first.get("search") is not None
    assert first.get("bash") is None

    disabled.clear()
    set_filters("conv1", {"disabled_tools": []})
    third = svc._get_registry("u1", "conv1", "assistant")

    assert third is not first
    assert third.get("bash") is not None


def test_tool_mcp_filters_subconversations_inherit_parent_until_overridden(tmp_path):
    ConversationStore.reset()
    store = ConversationStore.instance()
    store._store_dir = tmp_path
    parent = "conv_parent"
    child = "conv_parent::task::t_1"
    verify = "conv_parent::task_verify::t_1"
    delegate = "conv_parent::delegate::agent"
    store.save(parent, [], user_id="u1")
    store.save(child, [], user_id="u1")

    set_filters(parent, {
        "disabled_tools": ["bash"],
        "enabled_dynamic_tools": ["shared_tool"],
        "enabled_mcps": ["global_mcp"],
    })

    for cid in (child, verify, delegate):
        assert not is_tool_enabled(cid, "bash")
        assert is_tool_enabled(cid, "shared_tool", origin="dynamic", origin_scope="user")
        assert not is_tool_enabled(cid, "other_tool", origin="dynamic", origin_scope="user")
        assert enabled_mcp_names(cid) == {"global_mcp"}

    set_filters(child, {
        "disabled_tools": [],
        "enabled_dynamic_tools": ["child_tool"],
        "enabled_mcps": ["child_mcp"],
    })

    assert is_tool_enabled(child, "bash")
    assert not is_tool_enabled(child, "shared_tool", origin="dynamic", origin_scope="user")
    assert is_tool_enabled(child, "child_tool", origin="dynamic", origin_scope="user")
    assert enabled_mcp_names(child) == {"child_mcp"}


def test_tool_mcp_filter_actions_round_trip(tmp_path):
    ConversationStore.reset()
    store = ConversationStore.instance()
    store._store_dir = tmp_path
    cid = "conv_filter_actions"
    store.save(cid, [], user_id="u1")

    update = _call_resource_action("update_tool_mcp_filters", {
        "conversation_id": cid,
        "filters": {
            "disabled_tools": ["bash"],
            "enabled_dynamic_tools": ["shared_tool"],
            "enabled_mcps": ["local_mcp"],
            "agent_overrides": {
                "assistant": {
                    "tools": {"mode": "custom", "selected": ["read"]},
                    "mcps": {"mode": "inherit", "enabled": []},
                }
            },
        },
    })
    assert update["ok"] is True

    data = _call_resource_action("get_tool_mcp_filters", {
        "conversation_id": cid,
    })
    assert data["filters"]["disabled_tools"] == ["bash"]
    assert data["filters"]["enabled_dynamic_tools"] == ["shared_tool"]
    assert data["filters"]["enabled_mcps"] == ["local_mcp"]
    assert data["filters"]["agent_overrides"]["assistant"]["tools"] == {
        "mode": "custom",
        "selected": ["read"],
    }
    assert any(t["name"] == "bash" for t in data["tools"])


def test_mcp_loader_uses_conversation_scope_local_and_proxy_hooks():
    import re as _re
    # agent_context.py split into _agentctx_*; strip state-obj `st.` namespacing
    agent_context_src = _re.sub(r"\bst\.", "", "".join(
        Path(f"tasks/ai/{_f}").read_text(encoding="utf-8")
        for _f in ("agent_context.py", "_agentctx_base.py", "_agentctx_p1.py",
                   "_agentctx_p2.py", "_agentctx_p3.py")))
    tool_relay_src = "".join(f.read_text(encoding="utf-8") for f in sorted(Path("services").glob("*tool_relay*.py")))  # split across _tool_relay_*.py
    agent_tools_src = Path("core/handlers/agent_tools.py").read_text(encoding="utf-8")

    for src in (agent_context_src, tool_relay_src):
        assert 'get_any("mcp", mcp_name' in src
        assert "conversation_id=conversation_id" in src
        assert '"local": bool(mcp_def.get("local"))' in src
        assert "maybe_transform_relay_proxy_url" in src

    assert "self._local = bool(local)" in agent_tools_src
    assert '"local": self._local' in agent_tools_src


def test_mcp_resource_form_exposes_http_and_stdio_configuration():
    src = "".join(p.read_text(encoding="utf-8") for p in sorted(Path("tasks/io/chat_ui").glob("resources*.js")))

    assert "['transport','mcp_transport']" in src
    assert "['via','mcp_via']" in src
    assert "['relay_service','mcp_relay']" in src
    assert "['local','checkbox']" in src
    assert "['command','text']" in src
    assert "['args','json']" in src
    assert "['env','json']" in src
    assert "_showToolMcpFilterDialog" in src

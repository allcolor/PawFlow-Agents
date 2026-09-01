"""Tool exposure modes: one vocabulary shared by MCP publications and agents.

The mode decides how tools are advertised, never which tools exist -- that is
still core/tool_mcp_filters.py. These tests pin the precedence rule and the
fact that the two consumers cannot drift apart.
"""

import pytest

from core.tool_exposure import (
    DEFAULT_MODE,
    MODES,
    filter_read_only,
    is_full_mode,
    is_readonly_mode,
    normalize_mode,
    resolve_mode,
)


class TestVocabulary:

    def test_the_four_modes(self):
        assert MODES == {"api", "full", "api_readonly", "full_readonly"}

    def test_default_is_the_historical_behaviour(self):
        assert DEFAULT_MODE == "api"

    @pytest.mark.parametrize("mode", sorted(MODES))
    def test_known_modes_survive_normalisation(self, mode):
        assert normalize_mode(mode) == mode

    @pytest.mark.parametrize("value", ["", None, "nonsense", "FULL_READONLY "])
    def test_unknown_values_narrow_rather_than_raise(self, value):
        """A typo must never widen the surface."""
        assert normalize_mode(value) in MODES

    def test_case_and_whitespace_are_tolerated(self):
        assert normalize_mode("  Full_ReadOnly ") == "full_readonly"

    def test_full_and_readonly_predicates(self):
        assert is_full_mode("full") and is_full_mode("full_readonly")
        assert not is_full_mode("api") and not is_full_mode("api_readonly")
        assert is_readonly_mode("api_readonly") and is_readonly_mode("full_readonly")
        assert not is_readonly_mode("api") and not is_readonly_mode("full")


class TestPrecedence:
    """Agent override first, then the service default, then api."""

    def test_agent_wins_over_service(self):
        assert resolve_mode("full", "api_readonly") == "full"

    def test_service_used_when_agent_unset(self):
        for empty in ("", None, "   "):
            assert resolve_mode(empty, "full_readonly") == "full_readonly"

    def test_default_when_neither_is_set(self):
        assert resolve_mode("", "") == "api"
        assert resolve_mode(None, None) == "api"

    def test_invalid_agent_value_falls_through_to_service(self):
        """A junk override must not silently become the service value's veto."""
        assert resolve_mode("nonsense", "full") == "full"

    def test_override_replaces_and_never_merges(self):
        """A read-only service default cannot restrain a `full` agent.

        Documented on purpose: merging would make the effective surface
        impossible to read off either screen.
        """
        assert resolve_mode("full", "api_readonly") == "full"
        assert resolve_mode("api", "full_readonly") == "api"


class TestReadOnlyFilter:

    def test_non_readonly_modes_keep_everything(self):
        names = ["read", "write", "bash"]
        assert filter_read_only(names, "api") == names
        assert filter_read_only(names, "full") == names

    def test_readonly_mode_uses_the_shared_predicate(self, monkeypatch):
        monkeypatch.setattr(
            "core.tool_approval.ToolApprovalGate.is_read_only_allowed",
            staticmethod(lambda name: name == "read"))
        assert filter_read_only(["read", "write"], "full_readonly") == ["read"]

    def test_order_is_preserved(self, monkeypatch):
        monkeypatch.setattr(
            "core.tool_approval.ToolApprovalGate.is_read_only_allowed",
            staticmethod(lambda name: True))
        assert filter_read_only(["c", "a", "b"], "api_readonly") == ["c", "a", "b"]

    def test_empty_names_are_dropped(self):
        assert filter_read_only(["read", "", None], "api") == ["read"]


class TestConsumersShareTheDefinition:
    """The whole point of the module: no second copy of the vocabulary."""

    def test_mcp_store_imports_the_shared_modes(self):
        from core import mcp_server_store

        assert mcp_server_store._MODES is MODES

    def test_mcp_endpoint_normalises_through_the_shared_helper(self):
        from services.mcp_server_endpoint import _publication_mode

        assert _publication_mode({"mode": "full_readonly"}) == "full_readonly"
        assert _publication_mode({"mode": "nonsense"}) == "api"
        assert _publication_mode({}) == "api"


class TestSettingIsDeclaredAtBothLevels:

    def test_llm_service_declares_the_default(self):
        from services.llm_connection import LLMConnectionService

        schema = LLMConnectionService.get_parameter_schema(
            LLMConnectionService)
        field = schema["tool_exposure"]
        assert field["default"] == "api"
        assert set(field["options"]) == MODES

    def test_agent_config_declares_an_inheriting_override(self):
        from core.conv_agent_config import AGENT_CONFIG_DEFAULTS

        assert AGENT_CONFIG_DEFAULTS["tool_exposure"] == ""
        assert resolve_mode(
            AGENT_CONFIG_DEFAULTS["tool_exposure"], "full") == "full"


class _Handler:
    def __init__(self, name):
        self.name = name
        self.display_name = name.title()
        self.description = f"{name} description"
        self.parameters_schema = {
            "type": "object",
            "properties": {"value": {"type": "string"}},
        }


class _Registry:
    def __init__(self, include_meta=False):
        self.handlers = {
            "read": _Handler("read"),
            "write": _Handler("write"),
        }
        if include_meta:
            self.handlers.update({
                "get_tool_schema": _Handler("get_tool_schema"),
                "use_tool": _Handler("use_tool"),
            })

    def list_tools(self):
        return list(self.handlers.values())

    def get(self, name):
        return self.handlers.get(name)


class TestCliBridgeExposure:

    def test_cli_provider_keeps_the_resolved_mode(self, monkeypatch):
        from tasks.ai._agentctx_tools import resolve_exposure

        monkeypatch.setattr(
            "core.conv_agent_config.get_agent_config",
            lambda *_args: {"tool_exposure": "full"},
        )

        assert resolve_exposure("conv", "assistant", {}, True) == (
            "full", "full")

    def test_server_resolves_service_default_and_agent_override(self, monkeypatch):
        from services.tool_relay_service import ToolRelayService

        class _ServiceRegistry:
            @staticmethod
            def resolve_definition(service_id, user_id="", conv_id=""):
                assert (service_id, user_id, conv_id) == (
                    "llm", "user", "conv")
                return type("ServiceDef", (), {
                    "config": {"tool_exposure": "full_readonly"},
                })()

        configs = [{"llm_service": "llm"}, {
            "llm_service": "llm", "tool_exposure": "api_readonly"}]
        monkeypatch.setattr(
            "core.conv_agent_config.get_agent_config",
            lambda *_args: configs.pop(0),
        )
        monkeypatch.setattr(
            "core.service_registry.ServiceRegistry.get_instance",
            lambda: _ServiceRegistry(),
        )

        service = ToolRelayService({})
        assert service._active_tool_exposure(
            "user", "conv", "assistant") == "full_readonly"
        assert service._active_tool_exposure(
            "user", "conv", "assistant") == "api_readonly"

    @pytest.mark.parametrize(("mode", "expected"), [
        ("api", ["get_tool_schema", "use_tool"]),
        ("api_readonly", ["get_tool_schema", "use_tool"]),
        ("full", ["read", "write"]),
        ("full_readonly", ["read"]),
    ])
    def test_server_builds_the_exact_cli_surface(
            self, monkeypatch, mode, expected):
        from services.tool_relay_service import ToolRelayService

        service = ToolRelayService({})
        monkeypatch.setattr(
            service, "_get_registry", lambda *_args: _Registry(include_meta=True))
        monkeypatch.setattr(
            service, "_active_tool_exposure", lambda *_args: mode,
            raising=False)
        monkeypatch.setattr(
            "core.workflow_tool_scope.workflow_tool_visible_names",
            lambda *_args: None)

        response = service._handle_list_exposed_tools(
            "request", "user", "conv", "assistant")

        assert response["data"]["mode"] == mode
        assert [tool["name"] for tool in response["data"]["tools"]] == expected
        assert all("inputSchema" in tool for tool in response["data"]["tools"])

    def test_discovery_uses_agent_registry_and_hides_writes_in_readonly(
            self, monkeypatch):
        from services.tool_relay_service import ToolRelayService

        calls = []
        service = ToolRelayService({})
        monkeypatch.setattr(
            service, "_get_registry",
            lambda *args: calls.append(args) or _Registry())
        monkeypatch.setattr(
            service, "_active_tool_exposure",
            lambda *_args: "api_readonly", raising=False)
        monkeypatch.setattr(
            "core.workflow_tool_scope.workflow_tool_visible_names",
            lambda *_args: None)

        response = service._handle_list_tools(
            "request", "user", "conv", "assistant")

        assert calls == [("user", "conv", "assistant")]
        assert [tool["name"] for tool in response["data"]] == ["read"]

    def test_schema_hides_write_tool_in_readonly(self, monkeypatch):
        from services.tool_relay_service import ToolRelayService

        service = ToolRelayService({})
        monkeypatch.setattr(
            service, "_get_registry", lambda *_args: _Registry())
        monkeypatch.setattr(
            service, "_active_tool_exposure",
            lambda *_args: "full_readonly", raising=False)
        monkeypatch.setattr(
            "core.workflow_tool_scope.workflow_tool_visible_names",
            lambda *_args: None)

        response = service._handle_get_schema(
            "request", "write", "user", "conv", "assistant")

        assert response["type"] == "error"
        assert "read-only" in response["error"]

    def test_execute_rejects_forged_write_in_readonly(self, monkeypatch):
        from services.tool_relay_service import ToolRelayService

        executed = []
        service = ToolRelayService({})
        monkeypatch.setattr(
            service, "_active_tool_exposure",
            lambda *_args: "full_readonly", raising=False)
        monkeypatch.setattr(
            service, "_do_execute",
            lambda *_args, **_kwargs: executed.append(True) or {
                "type": "result", "request_id": "request", "data": "ran"})

        response = service._handle_execute(
            "request", "write", {}, "user", "conv", "assistant")

        assert response["data"].startswith("Error:")
        assert "tool_exposure=full_readonly" in response["data"]
        assert executed == []

    def test_hook_cannot_replace_read_with_write_in_readonly(self, monkeypatch):
        from services.tool_relay_service import ToolRelayService

        class _HookRunner:
            def __init__(self, **_kwargs):
                pass

            @staticmethod
            def run(*_args, **_kwargs):
                return {
                    "decision": "replace",
                    "payload": {"tool_name": "write", "arguments": {}},
                }

        service = ToolRelayService({})
        monkeypatch.setattr(
            service, "_active_tool_exposure",
            lambda *_args: "full_readonly", raising=False)
        monkeypatch.setattr(
            service, "_get_registry", lambda *_args: _Registry())
        monkeypatch.setattr(
            service, "_conversation_has_hooks", lambda *_args: True)
        monkeypatch.setattr(
            service, "_publish_code_mode_call", lambda *_args: "")
        monkeypatch.setattr("core.agent_hooks.AgentHookRunner", _HookRunner)

        response = service._handle_execute(
            "request", "read", {}, "user", "conv", "assistant")

        assert response["data"].startswith("Error:")
        assert "tool 'write'" in response["data"]
        assert "tool_exposure=full_readonly" in response["data"]

    def test_bridge_loads_its_surface_from_the_relay(self):
        from tools import mcp_bridge

        expected = {
            "mode": "full",
            "tools": [{
                "name": "read",
                "description": "Read",
                "inputSchema": {"type": "object", "properties": {}},
            }],
        }

        class _Client:
            def __init__(self):
                self.calls = []

            def request(self, method):
                self.calls.append(method)
                return expected

        client = _Client()
        assert mcp_bridge._load_mcp_surface(client) == expected
        assert client.calls == ["list_exposed_tools"]

    def test_shared_cli_prompt_describes_both_shapes(self):
        from core.agent_prompt_policy import CLI_MCP_SYSTEM_PROMPT

        assert "get_tool_schema" in CLI_MCP_SYSTEM_PROMPT
        assert "directly advertised" in CLI_MCP_SYSTEM_PROMPT
        assert "Follow the tool surface advertised" in CLI_MCP_SYSTEM_PROMPT

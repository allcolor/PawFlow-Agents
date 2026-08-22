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

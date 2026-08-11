"""Availability-aware tool routing and lazy family discovery contracts."""

import json
from pathlib import Path

from core.handlers.meta_tools import GetToolSchemaHandler
from core.tool_handler import ToolHandler
from core.tool_registry import ToolRegistry, create_default_registry
from core.tool_selection import (
    TOOL_FAMILIES,
    build_tool_selection_hint,
    declared_tool_names,
    render_tool_family,
)


class _Handler(ToolHandler):
    def __init__(self, name):
        self._name = name

    @property
    def name(self):
        return self._name

    @property
    def description(self):
        return f"Description for {self._name}"

    @property
    def parameters_schema(self):
        return {"type": "object", "properties": {}}

    def execute(self, arguments):
        return "ok"


def _registry(*names):
    registry = ToolRegistry()
    for name in names:
        registry.register(_Handler(name))
    return registry


def test_hint_is_filtered_and_omits_irrelevant_families():
    hint = build_tool_selection_hint({
        "delegate", "flash_delegate", "consult_agent", "todolist",
    })
    assert "## Tool selection" in hint
    assert "`delegate`" in hint
    assert "`flash_delegate`" in hint
    assert "`consult_agent`" in hint
    assert "`a2a`" not in hint
    assert "Waiting" not in hint
    assert "Knowledge and work state" not in hint


def test_hint_omits_a_family_with_only_one_available_route():
    assert build_tool_selection_hint({"delegate"}) == ""


def test_family_comparison_contains_only_available_routes():
    result = render_tool_family(
        "delegation", {"delegate", "flash_delegate", "unrelated"})
    assert result["available"] is True
    assert [route["tools"] for route in result["routes"]] == [
        ["delegate"], ["flash_delegate"]]
    assert result["routes"][0]["label"] == "existing conversation agent"


def test_unknown_family_lists_valid_families():
    result = render_tool_family("missing", {"delegate"})
    assert "error" in result
    assert result["available_families"] == sorted(TOOL_FAMILIES)


def test_get_tool_schema_accepts_family_or_tool_but_not_both():
    handler = GetToolSchemaHandler(_registry("delegate", "flash_delegate"))
    schema = handler.parameters_schema
    assert schema["required"] == []
    assert schema["properties"]["family"]["enum"] == sorted(TOOL_FAMILIES)

    family = json.loads(handler.execute({"family": "delegation"}))
    assert family["family"] == "delegation"
    assert [route["tools"] for route in family["routes"]] == [
        ["delegate"], ["flash_delegate"]]

    exact = json.loads(handler.execute({"tool_name": "delegate"}))
    assert exact["name"] == "delegate"

    invalid = json.loads(handler.execute({
        "tool_name": "delegate", "family": "delegation"}))
    assert "not both" in invalid["error"]


def test_get_tool_schema_without_selector_lists_tools_and_families():
    payload = json.loads(GetToolSchemaHandler(
        _registry("delegate", "scratchpad")).execute({}))
    assert [tool["name"] for tool in payload["available_tools"]] == [
        "delegate", "scratchpad"]
    assert payload["available_families"] == sorted(TOOL_FAMILIES)


def test_discovery_family_can_describe_the_meta_tools_themselves():
    registry = _registry("get_tool_schema", "use_tool", "pawflow_help")
    payload = json.loads(
        GetToolSchemaHandler(registry).execute({"family": "discovery"}))
    assert [route["tools"] for route in payload["routes"]] == [
        ["get_tool_schema"], ["use_tool"], ["pawflow_help"]]


def test_every_declared_tool_exists_in_the_default_registry():
    registered = {handler.name for handler in create_default_registry().list_tools()}
    assert declared_tool_names() <= registered


def test_full_default_hint_stays_bounded():
    names = {handler.name for handler in create_default_registry().list_tools()}
    hint = build_tool_selection_hint(names)
    assert len(hint) < 7000
    assert "## Tool selection" in hint
    assert "Delegation" in hint
    assert "Todo, plans, tasks, and flows" in hint
    assert "Waiting, resuming, and user contact" in hint
    assert "Knowledge and work state" in hint


def test_cli_cold_bootstrap_serializes_selection_under_system_instructions(
        tmp_path):
    from core.llm_client import LLMClient, LLMMessage

    hint = build_tool_selection_hint({
        "delegate", "flash_delegate", "todolist", "create_plan"})
    messages = [LLMMessage(
        role="user", content="latest", conversation_id="c")]
    LLMClient("claude-code")._build_cli_initial_context_prompt(
        messages,
        system_prompt=hint,
        user_text="latest",
        workdir=str(tmp_path),
        provider_workdir="/provider",
        user_id="u",
        conversation_id="c",
        agent_name="assistant",
    )
    body = (tmp_path / ".pawflow_cli" / "initial_context.md").read_text()
    assert body.index("## Tool selection") > body.index(
        "## System Instructions")
    assert body.index("## Tool selection") < body.index(
        "## Bootstrap Contract")


def test_context_builder_injects_availability_aware_selection():
    source = (Path(__file__).resolve().parents[1] / "tasks" / "ai" /
              "_agentctx_p3.py").read_text(encoding="utf-8")
    assert "build_tool_selection_hint" in source
    assert "st.registry.list_tools()" in source
    assert "st._selection_hint" in source

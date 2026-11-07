"""Mandatory inbox access through main-agent filters and policy gates."""

import json
import time
import uuid
from types import SimpleNamespace

import pytest

from core import agent_inbox
from core.agent_inbox_store import AgentInboxStore
from core.conversation_store import ConversationStore
from core.gating_policy import classify_call
from core.handlers.meta_tools import GetToolSchemaHandler, UseToolHandler
from core.tool_approval import ToolApprovalGate
from core.tool_mcp_filters import (
    disabled_names,
    is_tool_enabled,
    is_tool_enabled_from_filters,
    set_filters,
)
from services.gating_service import GatingService
from services.tool_relay_service import ToolRelayService
from tasks.ai.agent_tool_exec import AgentToolExecMixin

CID = "inbox-filter-policy"
AGENT = "assistant"


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    ConversationStore.reset()
    store = ConversationStore.instance()
    store._store_dir = tmp_path / "conversations"
    store.save(CID, [], user_id="u1")
    monkeypatch.setattr(
        AgentInboxStore, "_instance",
        AgentInboxStore(tmp_path / "inbox.sqlite3"))
    monkeypatch.setattr(
        "core.conv_agent_config.get_agent_config", lambda *args: {})
    monkeypatch.setattr(
        "core.agent_hooks.AgentHookRunner.run", lambda *args, **kwargs: {})

    service = ToolRelayService({})
    monkeypatch.setattr(service, "_load_mcp_tools", lambda *args: None)
    monkeypatch.setattr(
        service, "_list_available_filesystem_services", lambda *args: [])
    monkeypatch.setattr(
        service, "_filesystem_service_from_available", lambda *args: None)
    monkeypatch.setattr(
        service, "_active_tool_result_max_chars", lambda *args: None)
    monkeypatch.setattr(
        service, "_conversation_has_hooks", lambda *args: False)
    monkeypatch.setattr(
        service, "_active_tool_exposure", lambda *args: "full")

    ToolRelayService.clear_registry_cache()
    yield SimpleNamespace(service=service, store=store)
    ToolRelayService.clear_registry_cache()
    ConversationStore.reset()


def _restrict(selected):
    set_filters(CID, {
        "disabled_tools": ["read_inbox", "write", "bash", "delegate"],
        "agent_overrides": {
            AGENT: {"tools": {"mode": "custom", "selected": selected}},
        },
    })


def _put(cid=CID, agent=AGENT, body="OWN INBOX"):
    msg_id = str(uuid.uuid4())
    assert agent_inbox.enqueue_item(
        cid, agent, {"msg_id": msg_id, "ts": time.time(), "content": body,
                     "role": "user", "source": {"type": "agent_delegate"}},
        "delegate_message", "sender")
    return msg_id


def _bind_gate(monkeypatch, decision):
    # Use a real configured service: an unavailable evaluator must fail with
    # deny/ask for ordinary reads, while inbox plumbing skips evaluation.
    gate = GatingService({
        "prompt": "Require authorization for every call.",
        "llm_service": "unavailable",
        "llm_scope": "all",
        "failure_decision": decision,
    })
    monkeypatch.setattr(
        gate, "resolve_llm_service", lambda *args: (None, "unavailable"))
    resolved = {
        "bound": True, "broken": False,
        "gates": [{"origin": "conversation", "service": gate, "ref": {}}],
    }
    monkeypatch.setattr(
        "core.gating_bindings.resolve_gates", lambda *args: resolved)

    from core.tool_authorization import authorize_tool_call
    control = authorize_tool_call(
        tool_name="read", arguments={"path": "file"}, user_id="u1",
        conversation_id=CID, agent_name=AGENT)
    assert control.decision == decision
    return gate


@pytest.mark.parametrize("selected", [[], ["read"]])
def test_builtin_reader_survives_custom_and_disabled_filters(runtime, selected):
    _restrict(selected)
    assert is_tool_enabled(CID, "read_inbox", AGENT)
    assert not is_tool_enabled(CID, "write", AGENT)
    assert not is_tool_enabled(CID, "bash", AGENT)
    assert not is_tool_enabled(CID, "delegate", AGENT)
    assert is_tool_enabled(CID, "read", AGENT) == bool(selected)

    set_filters(CID, {"disabled_tools": ["read_inbox", "bash"]})
    assert is_tool_enabled(CID, "read_inbox", AGENT)
    assert not is_tool_enabled(CID, "bash", AGENT)
    assert disabled_names(CID, AGENT) == {"bash"}


@pytest.mark.parametrize("origin", ["dynamic", "mcp", "agui", "client"])
def test_reader_name_does_not_exempt_scoped_handlers(origin):
    filters = {
        "disabled_tools": ["read_inbox"],
        "agent_overrides": {
            AGENT: {"tools": {"mode": "custom", "selected": []}},
        },
    }
    assert not is_tool_enabled_from_filters(
        filters, "read_inbox", AGENT, origin, "other-conversation")


@pytest.mark.parametrize("selected", [[], ["read"]])
@pytest.mark.parametrize("mode", ["full", "full_readonly", "api", "api_readonly"])
def test_relay_and_main_discovery_keep_reader(runtime, monkeypatch, selected, mode):
    from tasks.ai._agentctx_tools import build_tool_defs

    _restrict(selected)
    service = runtime.service
    monkeypatch.setattr(service, "_active_tool_exposure", lambda *args: mode)
    registry = service._get_registry("u1", CID, AGENT)
    expected = {"read_inbox", *selected}
    assert {handler.name for handler in registry.list_tools()} == expected
    exposed = service._handle_list_exposed_tools("list", "u1", CID, AGENT)
    names = {tool["name"] for tool in exposed["data"]["tools"]}
    meta = (GetToolSchemaHandler(registry), UseToolHandler(registry))
    assert {tool.name for tool in build_tool_defs(registry, mode, meta)} == names
    if mode.startswith("full"):
        assert names == expected
    else:
        assert names == {"get_tool_schema", "use_tool"}
    discovery = json.loads(meta[0].execute({}))
    assert {tool["name"] for tool in discovery["available_tools"]} == expected
    assert json.loads(meta[0].execute({"tool_name": "read_inbox"}))["name"] == "read_inbox"
    assert "error" in json.loads(meta[0].execute({"tool_name": "write"}))
    assert service._handle_get_schema(
        "schema", "read_inbox", "u1", CID, AGENT)["data"]["name"] == "read_inbox"

    _put()
    assert "OWN INBOX" in meta[1].execute({
        "tool_name": "read_inbox", "arguments_json": "{}"})
    _put(body="DIRECT INBOX")
    assert "DIRECT INBOX" in service._do_execute(
        "direct", "read_inbox", {}, "u1", CID, AGENT)["data"]


@pytest.mark.parametrize("permission", ["deny", "confirm"])
@pytest.mark.parametrize("mode", ["default", "read_only", "advisor_read_only"])
def test_reader_is_policy_plumbing_even_with_tool_overrides(permission, mode):
    assert classify_call(
        "read_inbox", {}, tool_permission=permission,
        permission_mode=mode)[0] == "internal_ungated"
    assert ToolApprovalGate.is_advisor_read_only_allowed("read_inbox")
    assert classify_call(
        "write", {}, tool_permission=permission,
        permission_mode=mode)[0] != "internal_ungated"


@pytest.mark.parametrize("decision", ["deny", "ask"])
@pytest.mark.parametrize("wrapped", [False, True])
def test_main_reader_executes_under_real_policy_gate(runtime, monkeypatch, decision, wrapped):
    _restrict([])
    _bind_gate(monkeypatch, decision)
    runtime.store.set_extra(CID, "tool_permissions", {"read_inbox": "confirm"})
    registry = runtime.service._get_registry("u1", CID, AGENT)
    _put()
    arguments = {"tool_name": "read_inbox", "arguments_json": "{}"} if wrapped else {}
    call = SimpleNamespace(
        id=str(uuid.uuid4()), name="use_tool" if wrapped else "read_inbox",
        arguments=arguments)
    result = AgentToolExecMixin()._execute_tool_calls(
        [call], registry, {}, 0, agent_name=AGENT, conversation_id=CID,
        user_id="u1", permission_mode_override="read_only")
    assert "OWN INBOX" in result[0][1]


@pytest.mark.parametrize("decision", ["deny", "ask"])
@pytest.mark.parametrize("advisor", [False, True])
def test_relay_reader_executes_under_real_policy_gate(runtime, monkeypatch, decision, advisor):
    _restrict([])
    _bind_gate(monkeypatch, decision)
    runtime.store.set_extra(CID, "tool_permissions", {"read_inbox": "confirm"})
    if advisor:
        ToolApprovalGate.mark_advisor_read_only(CID)
    try:
        _put()
        result = runtime.service._do_execute(
            "relay-policy", "read_inbox", {}, "u1", CID, AGENT)
        assert "OWN INBOX" in result["data"]
    finally:
        ToolApprovalGate.unmark_advisor_read_only(CID)


@pytest.mark.parametrize("permission", ["deny", "confirm"])
@pytest.mark.parametrize("wrapped", [False, True])
def test_main_reader_ignores_explicit_permissions_without_policy_gate(
        runtime, monkeypatch, permission, wrapped):
    _restrict([])
    runtime.store.set_extra(
        CID, f"tool_permissions:{AGENT}", {"read_inbox": permission})
    assert ToolApprovalGate._get_permissions(CID, AGENT)["read_inbox"] == permission
    monkeypatch.setattr(
        ToolApprovalGate, "check",
        lambda *args, **kwargs: pytest.fail("Inbox reading requested approval"))
    registry = runtime.service._get_registry("u1", CID, AGENT)
    _put()
    arguments = {"tool_name": "read_inbox", "arguments_json": "{}"} if wrapped else {}
    call = SimpleNamespace(
        id=str(uuid.uuid4()), name="use_tool" if wrapped else "read_inbox",
        arguments=arguments)
    result = AgentToolExecMixin()._execute_tool_calls(
        [call], registry, {}, 0, agent_name=AGENT, conversation_id=CID,
        user_id="u1")
    assert "OWN INBOX" in result[0][1]


@pytest.mark.parametrize("permission", ["deny", "confirm"])
def test_relay_reader_ignores_explicit_permissions_without_policy_gate(
        runtime, monkeypatch, permission):
    _restrict([])
    runtime.store.set_extra(CID, "tool_permissions", {"read_inbox": permission})
    monkeypatch.setattr(
        ToolApprovalGate, "check",
        lambda *args, **kwargs: pytest.fail("Inbox reading requested approval"))
    _put()
    result = runtime.service._do_execute(
        "relay-permission", "read_inbox", {}, "u1", CID, AGENT)
    assert "OWN INBOX" in result["data"]


@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("prepared", [False, True])
def test_reader_page_survives_registry_caps(runtime, wrapped, prepared):
    _restrict([])
    registry = runtime.service._get_registry("u1", CID, AGENT)
    wrapper = UseToolHandler(registry)
    registry.register(wrapper)
    registry.get("read_inbox")._tool_result_max_chars = 500
    wrapper._tool_result_max_chars = 500
    body = "BEGIN-" + "x" * 60000 + "-END"
    _put(body=body)
    name = "use_tool" if wrapped else "read_inbox"
    arguments = {"max_chars": 100000}
    if wrapped:
        arguments = {"tool_name": "read_inbox",
                     "arguments_json": json.dumps(arguments)}
    if prepared:
        result = registry.execute_prepared(registry.prepare(name, arguments))
    else:
        result = registry.execute(name, arguments)
    assert body in result
    assert "Result cleared" not in result
    assert agent_inbox.unread_items(CID, AGENT) == []


def test_mandatory_reader_keeps_conversation_and_agent_isolation(runtime):
    _restrict([])
    foreign = _put(agent="other-agent", body="OTHER AGENT SECRET")
    _put(cid=CID + "::flash::other", body="OTHER CONVERSATION SECRET")
    _put()
    service = runtime.service
    result = service._do_execute(
        "own", "read_inbox", {}, "u1", CID, AGENT)["data"]
    assert "OWN INBOX" in result
    assert "SECRET" not in result
    assert "No unread inbox item" in service._do_execute(
        "foreign", "read_inbox", {"item": foreign}, "u1", CID, AGENT)["data"]
    registry = service._get_registry("u1", CID, AGENT)
    assert "unknown argument" in registry.execute(
        "read_inbox", {"agent_name": "other-agent", "conversation_id": "other"})
    assert len(agent_inbox.unread_items(CID, "other-agent")) == 1
    assert len(agent_inbox.unread_items(CID + "::flash::other", AGENT)) == 1

"""Tests for the read_inbox built-in tool and its wiring.

core.agent_inbox.read_inbox is monkeypatched: its store is implemented and
tested separately (step 1 of docs/INBOX_DELIVERY_PLAN.md).
"""
import inspect

import pytest

from core.handlers.inbox import ReadInboxHandler


@pytest.fixture
def calls(monkeypatch):
    recorded = []

    def fake_read_inbox(conversation_id, agent_name, *, max_chars=40000,
                        item="", offset=0):
        recorded.append({"conversation_id": conversation_id,
                         "agent_name": agent_name, "max_chars": max_chars,
                         "item": item, "offset": offset})
        return "INBOX-OUTPUT"

    monkeypatch.setattr("core.agent_inbox.read_inbox", fake_read_inbox)
    return recorded


def _handler(conversation_id="conv_inbox", source_agent="agentA",
             agent_name=""):
    h = ReadInboxHandler()
    h.set_conversation_id(conversation_id)
    h.set_user_id("user1")
    h.set_agent_name(agent_name)
    h.set_source_agent(source_agent, "svc_a")
    return h


class TestMetadata:
    def test_name_description_schema(self):
        h = ReadInboxHandler()
        assert h.name == "read_inbox"
        assert "[PawFlow inbox: ACTION REQUIRED]" in h.description
        assert "IMMEDIATELY" in h.description
        schema = h.parameters_schema
        assert schema["required"] == []
        props = schema["properties"]
        assert set(props) == {"max_chars", "item", "offset"}
        assert props["max_chars"] == {
            **props["max_chars"], "type": "integer", "minimum": 2000,
            "maximum": 100000, "default": 40000}
        assert props["item"]["type"] == "string"
        assert props["offset"]["type"] == "integer"
        assert props["offset"]["minimum"] == 0


class TestExecute:
    def test_defaults_call_inbox_with_caller_identity(self, calls):
        out = _handler().execute({})
        assert out == "INBOX-OUTPUT"
        assert calls == [{"conversation_id": "conv_inbox",
                          "agent_name": "agentA", "max_chars": 40000,
                          "item": "", "offset": 0}]

    def test_paged_item_arguments_are_forwarded(self, calls):
        _handler().execute({"max_chars": 2000, "item": " m-1 ",
                            "offset": 1234})
        assert calls[-1]["max_chars"] == 2000
        assert calls[-1]["item"] == "m-1"
        assert calls[-1]["offset"] == 1234

    def test_sub_conversation_key_is_kept(self, calls):
        _handler(conversation_id="conv::flash::critic",
                 source_agent="agentA::flash::critic").execute({})
        assert calls[-1]["conversation_id"] == "conv::flash::critic"
        assert calls[-1]["agent_name"] == "agentA::flash::critic"

    def test_falls_back_to_configured_agent_instance(self, calls):
        _handler(source_agent="", agent_name="agentB").execute({})
        assert calls[-1]["agent_name"] == "agentB"

    @pytest.mark.parametrize("value", [1999, 100001, "40000", True, 4.5])
    def test_bad_max_chars(self, calls, value):
        out = _handler().execute({"max_chars": value})
        assert out.startswith("Error: read_inbox max_chars")
        assert calls == []

    def test_offset_without_item(self, calls):
        out = _handler().execute({"offset": 10})
        assert out == "Error: read_inbox offset requires item."
        assert calls == []

    @pytest.mark.parametrize("value", [-1, "5", False])
    def test_bad_offset(self, calls, value):
        out = _handler().execute({"item": "m-1", "offset": value})
        assert out.startswith("Error: read_inbox offset must be")
        assert calls == []

    def test_bad_item_type(self, calls):
        out = _handler().execute({"item": 7})
        assert out.startswith("Error: read_inbox item")
        assert calls == []

    def test_missing_agent_identity(self, calls):
        out = _handler(source_agent="", agent_name="").execute({})
        assert out.startswith("Error: read_inbox could not determine")
        assert calls == []

    def test_missing_conversation_id(self, calls):
        out = _handler(conversation_id="").execute({})
        assert out.startswith("Error: read_inbox could not determine")
        assert calls == []


class TestWiring:
    def test_registered_in_default_registry(self):
        from core.tool_registry import create_default_registry
        handler = create_default_registry().get("read_inbox")
        assert isinstance(handler, ReadInboxHandler)

    def test_relay_sets_source_identity_for_read_inbox(self):
        import services._tool_relay_execute as tre
        src = inspect.getsource(tre)
        assert ('"delegate_status",\n                         '
                '"delegate_result", "read_inbox"}' in src)

    def test_handler_receives_relay_source_agent(self):
        # The relay sets the identity on every SpawnAgentsHandler instance.
        from core.handlers.spawn_agents import SpawnAgentsHandler
        assert isinstance(ReadInboxHandler(), SpawnAgentsHandler)

    def test_not_a_flow_task(self):
        from core.tool_task_adapter import SKIP_TOOLS
        assert "read_inbox" in SKIP_TOOLS

    def test_approval_exempt_and_read_only_allowed(self):
        from core.tool_approval import ToolApprovalGate
        assert "read_inbox" in ToolApprovalGate.EXEMPT_TOOLS
        assert "read_inbox" in ToolApprovalGate.READ_ONLY_ALLOWED
        assert ToolApprovalGate.is_read_only_allowed("read_inbox")

    def test_in_delegation_discovery_family(self):
        from core.tool_selection import TOOL_FAMILIES
        tools = {name for route in TOOL_FAMILIES["delegation"]["routes"]
                 for name in route[1]}
        assert "read_inbox" in tools

"""manage_resource agent create/delete keep conversation membership coherent."""
import pytest

import core.conv_agent_config as conv_agent_config
import core.conversation_store as conversation_store
import core.resource_store as resource_store
from core.handlers.manage_resource import ManageResourceHandler

CONV = "conv-1"


class _FakeResources:
    def __init__(self):
        self.items = {}
        self.deleted = []

    def create(self, rtype, name, user_id, data, conversation_id=""):
        scope = "conversation" if conversation_id else "user"
        self.items[(rtype, name)] = dict(data, _scope=scope)

    def get_any(self, rtype, name, user_id, conversation_id=""):
        return self.items.get((rtype, name))

    def delete(self, rtype, name, user_id, conversation_id=""):
        item = self.items.get((rtype, name))
        # A conversation-scoped item is only reachable with its conversation.
        if item is None or (item["_scope"] == "conversation"
                            and not conversation_id):
            return False
        self.deleted.append((rtype, name, conversation_id))
        del self.items[(rtype, name)]
        return True


class _FakeConversationStore:
    def __init__(self):
        self.extras = {}

    def get_extra(self, cid, key):
        return self.extras.get((cid, key))

    def set_extra(self, cid, key, value):
        self.extras[(cid, key)] = value


@pytest.fixture
def env(monkeypatch):
    resources = _FakeResources()
    conv = _FakeConversationStore()
    members = {}
    added = []

    def add_agent_to_conv(conv_id, name, llm_service, definition, **kwargs):
        if not llm_service or not definition:
            raise ValueError("missing llm_service or definition")
        added.append(dict(kwargs, conv_id=conv_id, name=name,
                          llm_service=llm_service, definition=definition))
        members[name] = {"definition": definition, "llm_service": llm_service}

    monkeypatch.setattr(resource_store.ResourceStore, "instance",
                        classmethod(lambda cls: resources))
    monkeypatch.setattr(conversation_store.ConversationStore, "instance",
                        classmethod(lambda cls: conv))
    monkeypatch.setattr(conv_agent_config, "add_agent_to_conv",
                        add_agent_to_conv)
    monkeypatch.setattr(conv_agent_config, "get_all_agent_configs",
                        lambda cid: {k: dict(v) for k, v in members.items()})
    monkeypatch.setattr(conv_agent_config, "remove_agent_config",
                        lambda cid, name: members.pop(name, None))

    handler = ManageResourceHandler()
    handler.set_user_id("alice")
    handler.set_conversation_id(CONV)
    handler.set_agent_name("claude")
    handler.set_llm_service("claude_code_interactive_llm_service")
    members["claude"] = {"definition": "claude"}
    conv.set_extra(CONV, "active_resources", {"agent": "claude"})
    return handler, resources, conv, members, added


def _create(handler, name, **data):
    return handler.execute({"action": "create", "resource_type": "agent",
                            "name": name, "data": dict(data, prompt="p")})


def test_create_registers_member_with_requested_service_and_definition(env):
    handler, resources, conv, members, added = env

    result = _create(handler, "probe",
                     llm_service="codex_interactive_llm_service",
                     model="m1", tools=["bash"], max_depth=2)

    assert result.startswith("Created agent 'probe'")
    assert added == [{
        "conv_id": CONV, "name": "probe",
        "llm_service": "codex_interactive_llm_service",
        "definition": "probe", "model": "m1", "tools": ["bash"],
        "max_depth": 2, "user_id": "alice",
    }]


def test_create_inherits_caller_service_when_none_requested(env):
    handler, resources, conv, members, added = env

    _create(handler, "probe")

    assert added[0]["llm_service"] == "claude_code_interactive_llm_service"


def test_create_never_changes_the_selected_agent(env):
    handler, resources, conv, members, added = env

    _create(handler, "probe", llm_service="svc")

    assert conv.get_extra(CONV, "active_resources") == {"agent": "claude"}


def test_create_without_any_service_writes_nothing(env):
    handler, resources, conv, members, added = env
    handler.set_llm_service("")

    result = _create(handler, "probe")

    assert result.startswith("Error: llm_service is required")
    assert resources.items == {}
    assert added == []


def test_delete_conversation_scoped_agent_removes_definition_and_member(env):
    handler, resources, conv, members, added = env
    _create(handler, "probe", llm_service="svc")
    conv.set_extra(CONV, "active_resources", {"agent": "probe"})

    result = handler.execute({"action": "delete", "resource_type": "agent",
                              "name": "probe"})

    assert result == "Deleted agent 'probe'."
    assert resources.deleted == [("agent", "probe", CONV)]
    assert "probe" not in members
    assert conv.get_extra(CONV, "active_resources") == {"agent": "claude"}


def test_delete_refuses_the_only_member_before_deleting(env):
    handler, resources, conv, members, added = env
    _create(handler, "probe", llm_service="svc")
    members.pop("claude")

    result = handler.execute({"action": "delete", "resource_type": "agent",
                              "name": "probe"})

    assert result.startswith("Error: cannot delete agent 'probe'")
    assert resources.deleted == []
    assert "probe" in members

"""Regression coverage for conversation-agent skill assignment isolation."""

from copy import deepcopy


class _ConversationStore:
    def __init__(self):
        self.extras = {
            "conv-a": {
                "conv_agents": {
                    "assistant": {
                        "definition": "assistant",
                        "llm_service": "llm",
                    },
                },
            },
            "conv-b": {
                "conv_agents": {
                    "assistant": {
                        "definition": "assistant",
                        "llm_service": "llm",
                    },
                },
            },
        }

    def get_extra(self, conversation_id, key):
        return deepcopy(self.extras.get(conversation_id, {}).get(key))

    def set_extra(self, conversation_id, key, value):
        self.extras.setdefault(conversation_id, {})[key] = deepcopy(value)

    def resolve_owner(self, conversation_id):
        return "alice" if conversation_id in self.extras else ""


class _ResourceStore:
    def __init__(self):
        self.agent = {
            "name": "assistant",
            "prompt": "You are an assistant.",
            "_scope": "user",
        }
        self.skill = {
            "name": "operate-comfyui",
            "description": "Operate ComfyUI",
            "instructions": "Use the ComfyUI relay.",
            "_scope": "user",
        }
        self.agent_updates = []

    def get_any(self, resource_type, name, user_id, conversation_id=""):
        if resource_type == "agent" and name == "assistant":
            return deepcopy(self.agent)
        if resource_type == "skill" and name == "operate-comfyui":
            return deepcopy(self.skill)
        return None

    def update(self, resource_type, name, user_id, data, **kwargs):
        assert resource_type == "agent"
        self.agent_updates.append((name, user_id, deepcopy(data), kwargs))
        self.agent.update(deepcopy(data))
        return deepcopy(self.agent)


def test_assignment_isolated_to_one_conversation_agent(monkeypatch):
    from core.conversation_store import ConversationStore
    from core.resource_store import ResourceStore
    from core.skill_lifecycle import assign_skill_to_agent
    from core.skill_resolver import _agent_assigned_skill_entry

    conversations = _ConversationStore()
    resources = _ResourceStore()
    monkeypatch.setattr(
        ConversationStore, "instance", staticmethod(lambda: conversations))
    monkeypatch.setattr(
        ResourceStore, "instance", staticmethod(lambda: resources))

    result = assign_skill_to_agent(
        "assistant", "operate-comfyui", "alice", "conv-a",
        resource_store=resources, notify=False)

    assert result["ok"] is True
    assert result["changed"] is True
    assert conversations.get_extra("conv-a", "conv_agents")[
        "assistant"]["assigned_skills"] == ["operate-comfyui"]
    assert conversations.get_extra("conv-b", "conv_agents")[
        "assistant"].get("assigned_skills", []) == []
    assert resources.agent_updates == []
    assert resources.agent.get("assigned_skills", []) == []
    assert _agent_assigned_skill_entry(
        "operate-comfyui", "alice", "conv-a", "assistant") == (
            "operate-comfyui")
    assert _agent_assigned_skill_entry(
        "operate-comfyui", "alice", "conv-b", "assistant") is None


def test_agent_skill_listing_isolated_between_conversations(monkeypatch):
    from core import FlowFile
    from core.conversation_store import ConversationStore
    from core.resource_store import ResourceStore
    from core.skill_lifecycle import assign_skill_to_agent
    from tasks.ai.actions._agentres_k2 import _handle_agentres_k2

    conversations = _ConversationStore()
    resources = _ResourceStore()
    monkeypatch.setattr(
        ConversationStore, "instance", staticmethod(lambda: conversations))
    monkeypatch.setattr(
        ResourceStore, "instance", staticmethod(lambda: resources))
    assign_skill_to_agent(
        "assistant", "operate-comfyui", "alice", "conv-a",
        resource_store=resources, notify=False)

    listed = {}
    for conversation_id in ("conv-a", "conv-b"):
        flowfile = FlowFile(content=b"")
        result = _handle_agentres_k2(
            object(), "list_agent_skills", {
                "conversation_id": conversation_id,
                "agent_name": "assistant",
            }, conversations, "alice", flowfile)
        assert result == [flowfile]
        listed[conversation_id] = [
            row["name"] for row in __import__("json").loads(
                flowfile.get_content().decode("utf-8"))["skills"]]

    assert listed == {
        "conv-a": ["operate-comfyui"],
        "conv-b": [],
    }

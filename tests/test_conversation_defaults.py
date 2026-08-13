"""What a conversation starts life with.

Two defaults are decided at different places on purpose. The view mode is the
default of the parameter cascade, so an explicit choice at any scope still
wins and existing conversations follow the new default. The permission mode is
written once at creation instead, so conversations that already exist keep the
mode they have been running under.
"""
from core.conversation_creation import create_conversation
from core.conversation_store import ConversationStore
from core.resource_store import ResourceStore


def _agent(name="assistant", user_id="alice@test.com"):
    rs = ResourceStore.instance()
    if rs.get_any("agent", name, user_id) is None:
        rs.create("agent", name, user_id, {
            "name": name,
            "prompt": "You are helpful.",
            "llm_service": "default",
        })


def _payload():
    return {"agents": [{"instance_name": "assistant",
                        "definition": "assistant",
                        "llm_service": "default"}]}


def test_a_new_conversation_starts_in_auto_permission_mode():
    _agent()
    result = create_conversation("alice@test.com", _payload())
    conv_id = result["conversation_id"]
    store = ConversationStore.instance()
    assert store.get_extra(conv_id, "permission_mode") == "auto"


def test_an_existing_conversation_keeps_its_own_permission_mode():
    # The default is written at creation, not read as a fallback: a
    # conversation that never had the extra keeps no mode of its own, and a
    # later creation does not reach back into it.
    _agent()
    store = ConversationStore.instance()
    store.save("legacy", [{"role": "user", "content": "hi"}],
               user_id="alice@test.com")
    assert store.get_extra("legacy", "permission_mode") is None

    create_conversation("alice@test.com", _payload())
    assert store.get_extra("legacy", "permission_mode") is None


def test_agent_definition_skills_are_copied_to_new_conversation_instance():
    name = "skill-default-agent"
    ResourceStore.instance().create("agent", name, "alice@test.com", {
        "name": name,
        "prompt": "You are helpful.",
        "llm_service": "default",
        "assigned_skills": ["operate-comfyui"],
    })
    payload = {"agents": [{
        "instance_name": name,
        "definition": name,
        "llm_service": "default",
    }]}

    result = create_conversation("alice@test.com", payload)
    conv_id = result["conversation_id"]
    configs = ConversationStore.instance().get_extra(conv_id, "conv_agents")

    assert configs[name]["assigned_skills"] == ["operate-comfyui"]
    assert ResourceStore.instance().get_any(
        "agent", name, "alice@test.com")["assigned_skills"] == [
            "operate-comfyui"]

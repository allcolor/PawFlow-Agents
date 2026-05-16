import json

from core import FlowFile
from tasks.ai.actions.command_dispatch import _parse_command
from tasks.ai.actions.agent_resource import _handle_agent_resource


def test_skill_run_slash_command_parses_optional_agent():
    body = _parse_command(
        "/skill run @reviewer review-pr 42", "conv1", "alice", "assistant")

    assert body["action"] == "run_skill"
    assert body["conversation_id"] == "conv1"
    assert body["target_agent"] == "reviewer"
    assert body["skill_name"] == "review-pr"
    assert body["arguments"] == "42"


def test_skill_run_slash_command_defaults_to_selected_agent():
    body = _parse_command(
        "/skill run review-pr 42", "conv1", "alice", "assistant")

    assert body["action"] == "run_skill"
    assert body["target_agent"] == "assistant"
    assert body["skill_name"] == "review-pr"
    assert body["arguments"] == "42"


def test_skill_run_sugar_defaults_to_selected_agent():
    body = _parse_command(
        "//review-pr 42", "conv1", "alice", "assistant")

    assert body["action"] == "run_skill"
    assert body["conversation_id"] == "conv1"
    assert body["target_agent"] == "assistant"
    assert body["skill_name"] == "review-pr"
    assert body["arguments"] == "42"


def test_skill_run_sugar_parses_immediate_agent():
    body = _parse_command(
        "//review-pr @reviewer 42", "conv1", "alice", "assistant")

    assert body["action"] == "run_skill"
    assert body["target_agent"] == "reviewer"
    assert body["skill_name"] == "review-pr"
    assert body["arguments"] == "42"


def test_skill_run_sugar_accepts_at_prefixed_skill_name():
    body = _parse_command(
        "//@review-pr @reviewer 42", "conv1", "alice", "assistant")

    assert body["action"] == "run_skill"
    assert body["target_agent"] == "reviewer"
    assert body["skill_name"] == "review-pr"
    assert body["arguments"] == "42"


def test_skill_run_sugar_only_treats_second_token_as_agent():
    body = _parse_command(
        "//notify email user@example.com", "conv1", "alice", "assistant")

    assert body["action"] == "run_skill"
    assert body["target_agent"] == "assistant"
    assert body["skill_name"] == "notify"
    assert body["arguments"] == "email user@example.com"


def test_resolve_runnable_skill_prompt_renders_args_and_placeholders(monkeypatch):
    from core import skill_resolver

    class Store:
        def get_any(self, rtype, name, user_id, conversation_id=""):
            assert rtype == "skill"
            assert name == "review-pr"
            return {
                "name": "review-pr",
                "_scope": "user",
                "description": "Review pull requests",
                "template_engine": "jinja",
                "parameters": {"pr_number": {}},
                "prompt": (
                    "Review PR {{ params.pr_number }} / {{ args[0] }}.\n"
                    "Raw=$ARGUMENTS.\n"
                    "Dir=${PAWFLOW_SKILL_DIR}."
                ),
            }

    from core.resource_store import ResourceStore
    monkeypatch.setattr(ResourceStore, "instance", staticmethod(lambda: Store()))

    rendered = skill_resolver.resolve_runnable_skill_prompt(
        "review-pr", "alice", "conv1", "assistant", "42")

    assert "## Skill Invocation: review-pr" in rendered
    assert "Review PR 42 / 42." in rendered
    assert "Raw=42." in rendered
    assert "/pawflow/skills/user-review-pr/review-pr" in rendered


def test_run_skill_action_queues_rendered_prompt_for_selected_agent(monkeypatch):
    captured = {"queued": []}

    class Task:
        def _resolve_agent_name(self, name, conv_id):
            return name

    class Store:
        def get_extra(self, cid, key, user_id=""):
            assert cid == "conv1"
            assert key == "active_resources"
            return {"agent": "assistant"}

    class ResourceStore:
        def get_any(self, rtype, name, user_id, conversation_id=""):
            if rtype == "skill" and name == "review-pr":
                return {
                    "name": "review-pr",
                    "description": "Review pull requests",
                    "parameters": {"pr_number": {}},
                    "prompt": "Review ${pr_number} now.",
                }
            return None

    class Writer:
        def enqueue_message(self, msg, agent_name="", user_id="", sse_events=None):
            captured["persisted"] = (msg, agent_name, user_id)

    class Queue:
        def enqueue(self, msg, source=""):
            captured["queued"].append((msg, source))

    from core.resource_store import ResourceStore as RealResourceStore
    from core.conversation_writer import ConversationWriter
    from core.pending_queue import PendingQueue
    from tasks.ai.agent_loop import AgentLoopTask
    import core.conv_agent_config as conv_agent_config

    monkeypatch.setattr(
        RealResourceStore, "instance", staticmethod(lambda: ResourceStore()))
    monkeypatch.setattr(
        conv_agent_config, "require_agent_member",
        lambda conv_id, agent_name, user_id="": "")
    monkeypatch.setattr(
        ConversationWriter, "for_conversation", staticmethod(lambda cid: Writer()))
    monkeypatch.setattr(
        PendingQueue, "for_agent", staticmethod(lambda cid, agent: Queue()))
    monkeypatch.setattr(
        AgentLoopTask, "wake_agent",
        staticmethod(lambda conv_id, agent_name, reason="", user_id="", delay=1.0,
                     even_if_active=False: captured.setdefault(
                         "wake", (conv_id, agent_name, reason, delay))))

    ff = FlowFile(content=b"")
    result = _handle_agent_resource(Task(), "run_skill", {
        "conversation_id": "conv1",
        "skill_name": "review-pr",
        "arguments": "42",
    }, Store(), "alice", ff)

    assert result == [ff]
    payload = json.loads(ff.get_content().decode("utf-8"))
    assert payload["ok"] is True
    assert payload["agent"] == "assistant"
    persisted, agent_name, user_id = captured["persisted"]
    assert agent_name == "assistant"
    assert user_id == "alice"
    assert persisted["source"]["target_agent"] == "assistant"
    assert persisted["source"]["skill_run"]["skill"] == "review-pr"
    assert "Review 42 now." in persisted["content"]
    assert captured["queued"][0][1] == "skill_run"
    assert captured["wake"][:2] == ("conv1", "assistant")

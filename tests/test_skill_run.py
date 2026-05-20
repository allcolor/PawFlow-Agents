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


def test_skill_update_slash_command_parses_explicit_update():
    body = _parse_command(
        "/skill update review-pr new prompt", "conv1", "alice", "assistant")

    assert body["action"] == "update_skill"
    assert body["name"] == "review-pr"
    assert body["instructions"] == "new prompt"


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


def test_skill_run_sugar_empty_skill_uses_run_skill_validation():
    body = _parse_command("//", "conv1", "alice", "assistant")

    assert body["action"] == "run_skill"
    assert body["target_agent"] == "assistant"
    assert body["skill_name"] == ""
    assert body["arguments"] == ""


def test_skill_run_sugar_rejects_triple_slash_as_missing_skill():
    body = _parse_command("///review-pr 42", "conv1", "alice", "assistant")

    assert body["action"] == "run_skill"
    assert body["target_agent"] == "assistant"
    assert body["skill_name"] == ""
    assert body["arguments"] == "42"


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
                "instructions": (
                    "Review PR ${1} / ${1}.\n"
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
    # Skill directory is the stable container mount path (B3 bind mount).
    assert "Dir=/skills/review-pr." in rendered
    assert "/skills/review-pr" in rendered


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
                    "instructions": "Review ${1} now.",
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


def test_skill_add_rejects_existing_skill_and_update_requires_existing(monkeypatch):
    calls = []

    class Task:
        pass

    class Store:
        def get_extra(self, *args, **kwargs):
            return {}

    class ResourceStore:
        def __init__(self, exists):
            self.exists = exists

        def get(self, rtype, name, user_id, **kwargs):
            return {"instructions": "old"} if self.exists else None

        def create(self, *args, **kwargs):
            calls.append(("create", args, kwargs))

        def update(self, *args, **kwargs):
            calls.append(("update", args, kwargs))

    import core.review_bindings as review_bindings
    from core.resource_store import ResourceStore as RealResourceStore

    monkeypatch.setattr(review_bindings, "review_for_write", lambda *a, **k: {})
    monkeypatch.setattr(RealResourceStore, "instance", staticmethod(lambda: ResourceStore(True)))
    ff = FlowFile(content=b"")
    _handle_agent_resource(Task(), "create_skill", {
        "conversation_id": "conv1", "name": "review-pr", "instructions": "new",
        "description": "Review PRs",
    }, Store(), "alice", ff)
    assert ff.get_attribute("http.response.status") == "409"
    assert calls == []

    monkeypatch.setattr(RealResourceStore, "instance", staticmethod(lambda: ResourceStore(False)))
    ff = FlowFile(content=b"")
    _handle_agent_resource(Task(), "update_skill", {
        "conversation_id": "conv1", "name": "review-pr", "instructions": "new",
    }, Store(), "alice", ff)
    assert ff.get_attribute("http.response.status") == "404"
    assert calls == []

    monkeypatch.setattr(RealResourceStore, "instance", staticmethod(lambda: ResourceStore(True)))
    ff = FlowFile(content=b"")
    _handle_agent_resource(Task(), "update_skill", {
        "conversation_id": "conv1", "name": "review-pr", "instructions": "new",
    }, Store(), "alice", ff)
    body = json.loads(ff.get_content().decode("utf-8"))
    assert body["updated"] is True
    assert calls[0][0] == "update"


def test_resource_store_skill_update_is_patch_not_replace(tmp_path, monkeypatch):
    from core import paths
    from core.repository import ScopedRepository
    from core.resource_store import ResourceStore

    monkeypatch.setattr(paths, "REPOSITORY_DIR", tmp_path / "repository")
    ScopedRepository.reset()
    ResourceStore.reset()

    store = ResourceStore.instance()
    store.create("skill", "review-pr", "alice", {
        "description": "old desc",
        "instructions": "old",
        "imported_from": {"source": "test"},
    })

    store.update("skill", "review-pr", "alice", {"instructions": "new"})
    updated = store.get("skill", "review-pr", "alice")

    assert updated["instructions"] == "new"
    assert updated["description"] == "old desc"
    assert updated["imported_from"] == {"source": "test"}


def test_delete_skill_removes_agent_assignments(monkeypatch):
    updated = []
    enqueued = []
    appended = []

    class Task:
        pass

    class Store:
        def append_message(self, conv_id, msg, agent_name="", user_id=""):
            appended.append((conv_id, msg, agent_name, user_id))

    class ResourceStore:
        def get_any(self, rtype, name, user_id, conversation_id=""):
            if rtype == "skill" and name == "review-pr":
                return {"name": name, "_scope": "conversation"}
            return None

        def delete(self, rtype, name, user_id, **kwargs):
            assert kwargs == {"conversation_id": "conv1"}
            return True

        def list_all(self, rtype, user_id, conversation_id=""):
            assert conversation_id == "conv1"
            return [
                {"name": "assistant", "_scope": "conversation", "assigned_skills": ["review-pr", "other"]},
                {"name": "reviewer", "_scope": "user", "assigned_skills": [{"name": "review-pr"}]},
            ]

        def update(self, rtype, name, user_id, data, **kwargs):
            updated.append((rtype, name, user_id, data, kwargs))

    class Queue:
        def enqueue(self, msg, source=""):
            enqueued.append((msg, source))

    from core.resource_store import ResourceStore as RealResourceStore
    from core.pending_queue import PendingQueue
    monkeypatch.setattr(RealResourceStore, "instance", staticmethod(lambda: ResourceStore()))
    monkeypatch.setattr(PendingQueue, "for_agent", staticmethod(lambda cid, agent: Queue()))

    ff = FlowFile(content=b"")
    result = _handle_agent_resource(Task(), "delete_skill", {
        "conversation_id": "conv1", "name": "review-pr",
    }, Store(), "alice", ff)

    assert result == [ff]
    body = json.loads(ff.get_content().decode("utf-8"))
    assert body["deleted"] is True
    assert body["cleaned_agents"] == ["assistant", "reviewer"]
    assert updated[0][3] == {"assigned_skills": ["other"]}
    assert updated[1][3] == {"assigned_skills": []}
    assert [row[1] for row in enqueued] == ["skill_delete", "skill_delete"]
    assert len(appended) == 2

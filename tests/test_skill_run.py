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


def test_skill_add_update_slash_command_parses_force():
    add_body = _parse_command(
        "/skill add --force review-pr new prompt", "conv1", "alice", "assistant")
    update_body = _parse_command(
        "/skill update --force review-pr changed prompt", "conv1", "alice", "assistant")
    add_alias_body = _parse_command(
        "/add-skill --force @review-pr alias prompt", "conv1", "alice", "assistant")
    del_body = _parse_command(
        "/skill del @review-pr", "conv1", "alice", "assistant")

    assert add_body["action"] == "create_skill"
    assert add_body["name"] == "review-pr"
    assert add_body["instructions"] == "new prompt"
    assert add_body["force"] is True
    assert update_body["action"] == "update_skill"
    assert update_body["name"] == "review-pr"
    assert update_body["instructions"] == "changed prompt"
    assert update_body["force"] is True
    assert add_alias_body["action"] == "create_skill"
    assert add_alias_body["name"] == "review-pr"
    assert add_alias_body["instructions"] == "alias prompt"
    assert add_alias_body["force"] is True
    assert del_body["action"] == "delete_skill"
    assert del_body["name"] == "review-pr"


def test_skill_assignment_slash_commands_parse_agent_and_skill():
    assign_body = _parse_command(
        "/skill assign @assistant @review-pr", "conv1", "alice", "assistant")
    unassign_body = _parse_command(
        "/skill unassign assistant review-pr", "conv1", "alice", "assistant")
    assigned_body = _parse_command(
        "/skill assigned @assistant", "conv1", "alice", "assistant")

    assert assign_body["action"] == "assign_skill"
    assert assign_body["agent_name"] == "assistant"
    assert assign_body["skill_name"] == "review-pr"
    assert assign_body["conversation_id"] == "conv1"
    assert unassign_body["action"] == "unassign_skill"
    assert unassign_body["agent_name"] == "assistant"
    assert unassign_body["skill_name"] == "review-pr"
    assert assigned_body["action"] == "list_agent_skills"
    assert assigned_body["agent_name"] == "assistant"


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


def test_resolve_runnable_skill_prompt_delivers_body_verbatim(monkeypatch):
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
    # Arguments and skill directory are explicit lines, not substituted tokens.
    assert "Arguments: 42" in rendered
    assert "Skill directory: /skills/review-pr" in rendered
    # SKILL.md content is delivered verbatim — placeholders are NOT substituted.
    assert "Review PR ${1} / ${1}." in rendered
    assert "Raw=$ARGUMENTS." in rendered
    assert "Dir=${PAWFLOW_SKILL_DIR}." in rendered


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
    # SKILL.md content is verbatim; the argument is the explicit Arguments line.
    assert "Review ${1} now." in persisted["content"]
    assert "Arguments: 42" in persisted["content"]
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


def test_unassign_skill_matches_object_entries(monkeypatch):
    updated = []

    class Task:
        pass

    class Store:
        pass

    class ResourceStore:
        def get_any(self, rtype, name, user_id, conversation_id=""):
            if rtype == "agent" and name == "assistant":
                return {
                    "name": "assistant",
                    "_scope": "user",
                    "assigned_skills": [
                        {"name": "review-pr", "params": {"mode": "fast"}},
                        "other",
                    ],
                }
            return None

        def update(self, rtype, name, user_id, data, **kwargs):
            updated.append((rtype, name, user_id, data, kwargs))

    from core.resource_store import ResourceStore as RealResourceStore
    monkeypatch.setattr(RealResourceStore, "instance", staticmethod(lambda: ResourceStore()))

    ff = FlowFile(content=b"")
    result = _handle_agent_resource(Task(), "unassign_skill", {
        "agent_name": "assistant", "skill_name": "review-pr",
    }, Store(), "alice", ff)

    assert result == [ff]
    body = json.loads(ff.get_content().decode("utf-8"))
    assert body["unassigned"] is True
    assert updated == [("agent", "assistant", "alice", {"assigned_skills": ["other"]}, {})]


def test_assign_skill_does_not_duplicate_object_entries(monkeypatch):
    updated = []

    class Task:
        pass

    class Store:
        pass

    existing_assignment = {"name": "review-pr", "params": {"mode": "fast"}}

    class ResourceStore:
        def get_any(self, rtype, name, user_id, conversation_id=""):
            if rtype == "agent" and name == "assistant":
                return {
                    "name": "assistant",
                    "_scope": "user",
                    "assigned_skills": [existing_assignment],
                }
            if rtype == "skill" and name == "review-pr":
                return {"name": "review-pr", "description": "Review PRs"}
            return None

        def update(self, rtype, name, user_id, data, **kwargs):
            updated.append((rtype, name, user_id, data, kwargs))

    from core.resource_store import ResourceStore as RealResourceStore
    monkeypatch.setattr(RealResourceStore, "instance", staticmethod(lambda: ResourceStore()))

    ff = FlowFile(content=b"")
    result = _handle_agent_resource(Task(), "assign_skill", {
        "agent_name": "assistant", "skill_name": "review-pr",
    }, Store(), "alice", ff)

    assert result == [ff]
    assert updated == [("agent", "assistant", "alice", {"assigned_skills": [existing_assignment]}, {})]


def test_list_resources_normalizes_object_assigned_skills(monkeypatch):
    class Task:
        def _ensure_active_agent(self, conv_id, active, user_id):
            return active

    class Store:
        def get_extra(self, conv_id, key):
            return {"agent": "assistant"} if key == "active_resources" else None

    class ResourceStore:
        def list_all(self, rtype, user_id, conversation_id=""):
            if rtype == "agent":
                return [{
                    "name": "assistant",
                    "_scope": "conversation",
                    "assigned_skills": [
                        {"name": "review-pr", "params": {"mode": "fast"}},
                        "other",
                    ],
                }]
            if rtype == "skill":
                return [
                    {"name": "review-pr", "description": "Review PRs"},
                    {"name": "other", "description": "Other skill"},
                ]
            return []

    from core.resource_store import ResourceStore as RealResourceStore
    import core.conv_agent_config as conv_agent_config
    monkeypatch.setattr(RealResourceStore, "instance", staticmethod(lambda: ResourceStore()))
    monkeypatch.setattr(
        conv_agent_config,
        "get_all_agent_configs",
        lambda conv_id: {"assistant": {"definition": "assistant", "llm_service": "llm"}},
    )

    ff = FlowFile(content=b"")
    result = _handle_agent_resource(Task(), "list_resources", {
        "conversation_id": "conv1",
    }, Store(), "alice", ff)

    assert result == [ff]
    body = json.loads(ff.get_content().decode("utf-8"))
    assert body["agents"][0]["assigned_skills"] == ["review-pr", "other"]
    skills = {row["name"]: row for row in body["skills"]}
    assert skills["review-pr"]["assigned_to"] == ["assistant"]
    assert skills["other"]["assigned_to"] == ["assistant"]


def test_list_skills_marks_current_agent_assignments(monkeypatch):
    class Task:
        pass

    class Store:
        def get_extra(self, conv_id, key):
            return {"agent": "assistant"} if key == "active_resources" else None

    class ResourceStore:
        def list_all(self, rtype, user_id, conversation_id=""):
            if rtype == "skill":
                return [
                    {"name": "review-pr", "description": "Review PRs"},
                    {"name": "deploy", "description": "Deploy"},
                ]
            if rtype == "agent":
                return [{
                    "name": "assistant",
                    "assigned_skills": [
                        {"name": "review-pr", "params": {"mode": "fast"}},
                    ],
                }]
            return []

    from core.resource_store import ResourceStore as RealResourceStore
    import core.conv_agent_config as conv_agent_config
    monkeypatch.setattr(RealResourceStore, "instance", staticmethod(lambda: ResourceStore()))
    monkeypatch.setattr(
        conv_agent_config,
        "get_all_agent_configs",
        lambda conv_id: {"assistant": {"definition": "assistant"}},
    )

    ff = FlowFile(content=b"")
    result = _handle_agent_resource(Task(), "list_skills", {
        "conversation_id": "conv1",
    }, Store(), "alice", ff)

    assert result == [ff]
    body = json.loads(ff.get_content().decode("utf-8"))
    skills = {row["name"]: row for row in body["skills"]}
    assert skills["review-pr"]["assigned_to"] == ["assistant"]
    assert skills["review-pr"]["active"] is True
    assert skills["deploy"]["assigned_to"] == []
    assert skills["deploy"]["active"] is False


def test_resolve_skill_prompts_delivers_body_verbatim(monkeypatch):
    # load_skill delivers SKILL.md verbatim; the directory is an explicit line.
    from core import skill_resolver

    class Store:
        def get_any(self, rtype, name, user_id, conversation_id=""):
            return {
                "name": "deploy",
                "_scope": "user",
                "description": "Deploy things",
                "instructions": "Run ${CLAUDE_SKILL_DIR}/scripts/go.sh now.",
            }

    from core.resource_store import ResourceStore
    monkeypatch.setattr(ResourceStore, "instance", staticmethod(lambda: Store()))

    blocks = skill_resolver.resolve_skill_prompts(["deploy"], "alice", "conv1")
    assert blocks
    # Placeholders are not substituted; the directory is given as a header line.
    assert "${CLAUDE_SKILL_DIR}/scripts/go.sh" in blocks[0]
    assert "Skill directory: /skills/deploy" in blocks[0]
    # No skill_root on disk -> no asset block.
    assert "### Skill assets" not in blocks[0]


def test_resolve_skill_prompts_inlines_and_lists_bundled_assets(tmp_path, monkeypatch):
    from core import skill_resolver

    skill_dir = tmp_path / "deploy"
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: deploy\n---\nbody")
    (skill_dir / "scripts" / "go.sh").write_text("echo deploying\n")
    # A file too large to inline is still enumerated in the listing.
    big = "x" * (skill_resolver._ASSET_INLINE_MAX_BYTES + 1)
    (skill_dir / "scripts" / "big.txt").write_text(big)

    class Store:
        def get_any(self, rtype, name, user_id, conversation_id=""):
            return {
                "name": "deploy",
                "_scope": "user",
                "description": "Deploy things",
                "instructions": "Run scripts/go.sh now.",
                "skill_root": str(skill_dir),
            }

    from core.resource_store import ResourceStore
    monkeypatch.setattr(ResourceStore, "instance", staticmethod(lambda: Store()))

    blocks = skill_resolver.resolve_skill_prompts(["deploy"], "alice", "conv1")
    assert blocks
    block = blocks[0]
    assert "### Skill assets" in block
    # Both assets are enumerated; SKILL.md itself is excluded.
    assert "- scripts/go.sh" in block
    assert "- scripts/big.txt" in block
    assert "- SKILL.md" not in block
    # The small text asset is inlined; the oversized one is not.
    assert "echo deploying" in block
    assert big not in block


def test_skill_assets_block_empty_without_skill_root():
    from core import skill_resolver

    assert skill_resolver._skill_assets_block({}) == ""
    assert skill_resolver._skill_assets_block(
        {"skill_root": "/nonexistent/path/xyz"}) == ""

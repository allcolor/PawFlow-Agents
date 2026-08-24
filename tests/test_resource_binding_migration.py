"""WP4/WP8 exact assigned-skill migration and activation gates."""

from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor

import pytest

from core.resource_binding_migration import (
    RESOURCE_BINDINGS_V2_KEY,
    activate_skill_binding_migration,
    migrate_skill_bindings,
    preflight_skill_binding_migration,
    replace_active_skill_assignments,
    resolve_exact_skill,
    rollback_skill_binding_migration,
    runtime_skill_assignments,
)

NOW = "2026-08-24T15:00:00+00:00"


class _ConversationStore:
    def __init__(self, configs):
        self.extras = {"conv_agents": copy.deepcopy(configs)}
        self.writes = []

    def get_extra(self, _cid, key, default=None):
        return copy.deepcopy(self.extras.get(key, default))

    def set_extra(self, _cid, key, value):
        self.extras[key] = copy.deepcopy(value)
        self.writes.append((key, copy.deepcopy(value)))
        return True

    def get_metadata(self, _cid):
        return {"user_id": "alice"}


class _ResourceStore:
    def __init__(self):
        self.definitions = {
            ("conversation", "review"): {
                "name": "review", "description": "Conversation",
                "instructions": "conv", "_scope": "conversation",
            },
            ("user", "review"): {
                "name": "review", "description": "User",
                "instructions": "user", "_scope": "user",
            },
            ("global", "other"): {
                "name": "other", "description": "Global",
                "instructions": "global", "_scope": "global",
            },
        }

    def get_any(self, _rtype, name, _user_id, conversation_id=""):
        for scope in (("conversation", name), ("user", name), ("global", name)):
            if scope in self.definitions:
                return copy.deepcopy(self.definitions[scope])
        return None

    def get(self, _rtype, name, user_id, conversation_id=""):
        scope = "conversation" if conversation_id else (
            "global" if user_id == "__global__" else "user")
        value = self.definitions.get((scope, name))
        if value is None:
            return None
        result = copy.deepcopy(value)
        result.pop("_scope", None)
        return result


def _configs():
    return {
        "assistant": {
            "definition": "assistant",
            "assigned_skills": [
                {"name": "review", "params": {"mode": "fast"}},
                "other",
            ],
        },
        "reviewer": {"definition": "reviewer", "assigned_skills": []},
    }


def _migrate(store=None, resources=None):
    store = store or _ConversationStore(_configs())
    resources = resources or _ResourceStore()
    result = migrate_skill_bindings(
        "conv-1", "alice", conversation_store=store,
        resource_store=resources, now=lambda: NOW, activated_by="alice")
    return result, store, resources


def test_production_shaped_migration_pins_scope_and_is_idempotent():
    result, store, _resources = _migrate()

    assert result == {
        "ok": True, "state": "active", "activated": True,
        "idempotent": False, "agent_count": 2, "assignment_count": 2,
        "activated_at": NOW, "first_write_at": None,
        "rollback_available": True,
    }
    document = store.extras[RESOURCE_BINDINGS_V2_KEY]
    assignments = document["agents"]["assistant"]
    assert [row["ref"]["scope"] for row in assignments] == [
        "conversation", "global"]
    assert assignments[0]["params"] == {"mode": "fast"}
    assert document["rollback_assignments"] == {
        "assistant": _configs()["assistant"]["assigned_skills"],
        "reviewer": [],
    }

    second = migrate_skill_bindings(
        "conv-1", "alice", conversation_store=store,
        resource_store=_ResourceStore(), now=lambda: "2099-01-01T00:00:00+00:00")
    assert second["idempotent"] is True
    assert len(store.writes) == 1


def test_unresolved_assignment_blocks_the_whole_conversation():
    configs = _configs()
    configs["reviewer"]["assigned_skills"] = ["missing"]
    store = _ConversationStore(configs)
    result, _store, _resources = _migrate(store=store)

    assert result["ok"] is False
    assert result["activated"] is False
    assert result["blockers"] == [{
        "agent": "reviewer", "skill": "missing",
        "reason": "assigned skill is not visible: missing",
    }]
    assert RESOURCE_BINDINGS_V2_KEY not in store.extras
    assert store.writes == []


def test_activation_rejects_roster_change_after_preflight():
    store = _ConversationStore(_configs())
    resources = _ResourceStore()
    plan = preflight_skill_binding_migration(
        "conv-1", "alice", conversation_store=store,
        resource_store=resources, now=lambda: NOW)
    store.extras["conv_agents"]["assistant"]["assigned_skills"] = ["other"]

    result = activate_skill_binding_migration(
        plan, conversation_store=store, resource_store=resources)

    assert result["ok"] is False
    assert "changed after preflight" in result["blockers"][0]["reason"]
    assert store.writes == []


def test_exact_binding_detects_content_change_without_scope_fallback():
    _result, store, resources = _migrate()
    ref = store.extras[RESOURCE_BINDINGS_V2_KEY]["agents"]["assistant"][0]["ref"]
    assert resolve_exact_skill(ref, "conv-1", resource_store=resources)[
        "instructions"] == "conv"

    resources.definitions[("conversation", "review")]["instructions"] = "changed"
    with pytest.raises(ValueError, match="identity changed"):
        resolve_exact_skill(ref, "conv-1", resource_store=resources)


def test_runtime_selection_requires_flag_and_active_marker(monkeypatch):
    _result, store, _resources = _migrate()
    legacy = ["legacy"]
    monkeypatch.delenv("PAWFLOW_RESOURCE_BINDINGS_V2_ENABLED", raising=False)
    assert runtime_skill_assignments(
        "conv-1", "assistant", legacy, conversation_store=store) == legacy

    monkeypatch.setenv("PAWFLOW_RESOURCE_BINDINGS_V2_ENABLED", "1")
    selected = runtime_skill_assignments(
        "conv-1", "assistant", legacy, conversation_store=store)
    assert selected[0]["schema_version"] == 2
    assert selected[0]["ref"]["name"] == "review"


def test_first_v2_write_retires_rollback_and_prevents_rollback(monkeypatch):
    _result, store, resources = _migrate()
    monkeypatch.setenv("PAWFLOW_RESOURCE_BINDINGS_V2_ENABLED", "1")
    current = store.extras[RESOURCE_BINDINGS_V2_KEY]["agents"]["assistant"]

    assert replace_active_skill_assignments(
        "conv-1", "alice", "assistant", current[:1],
        conversation_store=store, resource_store=resources,
        now=lambda: "2026-08-24T16:00:00+00:00") is True
    document = store.extras[RESOURCE_BINDINGS_V2_KEY]
    assert document["first_write_at"] == "2026-08-24T16:00:00+00:00"
    assert document["rollback_assignments"] is None
    assert document["mutation_revision"] == 1
    with pytest.raises(ValueError, match="unavailable after the first v2 write"):
        rollback_skill_binding_migration("conv-1", conversation_store=store)


def test_rollback_before_first_write_restores_legacy_runtime(monkeypatch):
    _result, store, _resources = _migrate()
    monkeypatch.setenv("PAWFLOW_RESOURCE_BINDINGS_V2_ENABLED", "1")

    result = rollback_skill_binding_migration(
        "conv-1", conversation_store=store)

    assert result["state"] == "rolled_back"
    assert runtime_skill_assignments(
        "conv-1", "assistant", ["review"],
        conversation_store=store) == ["review"]


def test_concurrent_migration_activates_exactly_once():
    store = _ConversationStore(_configs())
    resources = _ResourceStore()

    def migrate(_index):
        return migrate_skill_bindings(
            "conv-1", "alice", conversation_store=store,
            resource_store=resources, now=lambda: NOW)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(migrate, range(16)))

    assert len(store.writes) == 1
    assert sum(not row["idempotent"] for row in results) == 1
    assert all(row["activated"] for row in results)


def test_failed_activation_write_leaves_no_partial_marker():
    class BrokenStore(_ConversationStore):
        def set_extra(self, _cid, _key, _value):
            return False

    store = BrokenStore(_configs())
    with pytest.raises(RuntimeError, match="failed to persist"):
        _migrate(store=store)
    assert RESOURCE_BINDINGS_V2_KEY not in store.extras


def test_active_marker_fails_closed_when_legacy_roster_drifted(monkeypatch):
    _result, store, _resources = _migrate()
    store.extras["conv_agents"]["assistant"]["assigned_skills"] = ["other"]
    monkeypatch.setenv("PAWFLOW_RESOURCE_BINDINGS_V2_ENABLED", "1")

    with pytest.raises(ValueError, match="activation is stale"):
        runtime_skill_assignments(
            "conv-1", "assistant", ["other"], conversation_store=store)


def test_manage_resource_exposes_redacted_operator_migration_actions(monkeypatch):
    from core.handlers.manage_resource import ManageResourceHandler

    store = _ConversationStore(_configs())
    resources = _ResourceStore()
    monkeypatch.setattr(
        "core.conversation_store.ConversationStore.instance", lambda: store)
    monkeypatch.setattr(
        "core.resource_store.ResourceStore.instance", lambda: resources)
    handler = ManageResourceHandler()
    handler.set_user_id("alice")
    handler.set_conversation_id("conv-1")

    inspected = handler.execute({
        "action": "inspect_skill_bindings", "resource_type": "skill"})
    migrated = handler.execute({
        "action": "migrate_skill_bindings", "resource_type": "skill"})
    rolled_back = handler.execute({
        "action": "rollback_skill_bindings", "resource_type": "skill"})

    assert '"state": "legacy"' in inspected
    assert '"state": "active"' in migrated
    assert '"assignment_count": 2' in migrated
    assert '"state": "rolled_back"' in rolled_back
    assert "instructions" not in migrated
    assert "params" not in migrated


def test_explicit_only_and_disabled_bindings_require_explicit_invocation(monkeypatch):
    from core.skill_resolver import (
        resolve_runnable_skill_prompt,
        resolve_skill_manifests,
        resolve_skill_prompts,
    )

    configs = _configs()
    configs["assistant"]["assigned_skills"] = [
        {"name": "review", "invocation_policy_override": "explicit_only"},
        {"name": "other", "invocation_policy_override": "disabled"},
    ]
    result, store, resources = _migrate(
        store=_ConversationStore(configs), resources=_ResourceStore())
    assert result["activated"] is True
    assignments = store.extras[RESOURCE_BINDINGS_V2_KEY]["agents"]["assistant"]
    monkeypatch.setattr(
        "core.resource_store.ResourceStore.instance", lambda: resources)

    assert resolve_skill_manifests(
        assignments, "alice", conversation_id="conv-1") == []
    assert resolve_skill_prompts(
        assignments, "alice", conversation_id="conv-1",
        agent_name="assistant") == []

    explicit = resolve_runnable_skill_prompt(
        "review", "alice", "conv-1", "assistant")
    assert "## Skill Invocation: review" in explicit
    assert "conv" in explicit

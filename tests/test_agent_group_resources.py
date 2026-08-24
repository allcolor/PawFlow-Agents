from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.agent_group_resources import (
    bind_agent_group,
    get_bound_agent_group,
    resolve_agent_resource,
    validate_agent_group_data,
)


class _Resources:
    def __init__(self):
        self.items = {}

    def get_any(self, resource_type, name, user_id, conversation_id=""):
        item = self.items.get((resource_type, name))
        return dict(item) if item is not None else None


class _Conversations:
    def __init__(self):
        self.extras = {}

    def get_extra(self, conversation_id, key):
        return self.extras.get((conversation_id, key))

    def set_extra(self, conversation_id, key, value):
        self.extras[(conversation_id, key)] = value


class _Services:
    def __init__(self, services):
        self.services = services

    def resolve_all(self, **_kwargs):
        return self.services


def _fixture(monkeypatch):
    resources = _Resources()
    resources.items[("agent", "architect")] = {
        "name": "architect",
        "prompt": "Review architecture.",
        "_scope": "global",
    }
    resources.items[("agent", "operator")] = {
        "name": "operator",
        "prompt": "Review operations.",
        "_scope": "user",
    }
    architect_ref = resolve_agent_resource(
        "architect", "alice", "conv-1", resource_store=resources
    ).ref
    operator_ref = resolve_agent_resource(
        "operator", "alice", "conv-1", resource_store=resources
    ).ref
    group = {
        "schema_version": 1,
        "name": "review-board",
        "description": "Bounded review group.",
        "members": [
            {
                "member_id": "architecture",
                "member_kind": "conversation_instance",
                "agent_ref": architect_ref.to_dict(),
                "instance_name": "Architecture",
                "required": True,
            },
            {
                "member_id": "operations",
                "member_kind": "conversation_instance",
                "agent_ref": operator_ref.to_dict(),
                "instance_name": "Operations",
                "required": True,
            },
        ],
        "selection": {"mode": "all"},
        "deliberation": {
            "max_rounds": 2,
            "max_messages_per_member_per_round": 1,
            "max_total_participant_calls": 4,
            "max_parallelism": 2,
            "allow_pass": True,
            "rotate_first_speaker": True,
        },
        "context_policy": {
            "private_context": "none",
            "shared_history_limit": 24,
            "attachments": "explicit_only",
        },
        "tool_policy": {"mode": "none", "allowed_effects": []},
        "synthesis": {"mode": "deterministic_concat"},
        "budgets": {"max_tokens": 4096, "timeout_seconds": 120},
        "output": {"include_attributions": True, "include_dissent": True},
    }
    resources.items[("agent_group", "review-board")] = {
        **validate_agent_group_data("review-board", group),
        "_scope": "user",
    }
    configs = {
        "Architecture": {
            "definition": "architect",
            "runtime_kind": "llm",
            "llm_service": "architect-llm",
            "params": {"focus": "boundaries"},
            "model": "",
            "tools": ["read"],
        },
        "Operations": {
            "definition": "operator",
            "runtime_kind": "llm",
            "llm_service": "operator-llm",
            "params": {},
            "model": "",
            "tools": [],
        },
    }

    def resolve_config(conversation_id, name):
        if name not in configs:
            return conversation_id, "", {}
        return conversation_id, name, dict(configs[name])

    monkeypatch.setattr(
        "core.conv_agent_config.resolve_agent_config_entry", resolve_config
    )
    monkeypatch.setattr(
        "core.service_definition_revision.compute_service_definition_revision",
        lambda service: "a" * 64 if service.service_id == "architect-llm" else "b" * 64,
    )
    services = {
        name: SimpleNamespace(
            service_id=name,
            service_type="llmConnection",
            scope="conv",
            scope_id="conv-1",
        )
        for name in ("architect-llm", "operator-llm")
    }
    return resources, configs, _Services(services), _Conversations()


def test_agent_group_is_a_resource_and_pfp_type():
    from core import paths, pfp_package, resource_store

    assert "agent_groups" in paths.REPO_TYPES
    assert resource_store._TYPE_MAP["agent_group"] == "agent_groups"
    assert pfp_package._RESOURCE_TYPES["agent_group"] == "agent_group"
    assert "agent_group" in pfp_package._INSTALLABLE_TYPES


def test_group_contract_rejects_nested_or_declared_tool_members():
    resources = _Resources()
    with pytest.raises(ValueError, match="agent definition is not visible"):
        resolve_agent_resource(
            "missing", "alice", "conv-1", resource_store=resources
        )

    with pytest.raises(ValueError, match="declared"):
        validate_agent_group_data(
            "bad",
            {
                "name": "bad",
                "description": "bad",
                "members": [],
                "tool_policy": {"mode": "declared"},
                "synthesis": {"mode": "deterministic_concat"},
                "budgets": {"max_tokens": 1, "timeout_seconds": 1},
            },
        )


def test_group_binding_is_server_gated(monkeypatch):
    resources, _configs, services, conversations = _fixture(monkeypatch)
    monkeypatch.delenv("PAWFLOW_AGENT_GROUPS_ENABLED", raising=False)

    with pytest.raises(ValueError, match="disabled by the server"):
        bind_agent_group(
            "review-board",
            "alice",
            "conv-1",
            resource_store=resources,
            conversation_store=conversations,
            service_registry=services,
        )


def test_group_binding_snapshots_exact_members_and_rejects_stale_group(
    monkeypatch,
):
    resources, configs, services, conversations = _fixture(monkeypatch)
    monkeypatch.setenv("PAWFLOW_AGENT_GROUPS_ENABLED", "true")

    binding = bind_agent_group(
        "review-board",
        "alice",
        "conv-1",
        resource_store=resources,
        conversation_store=conversations,
        service_registry=services,
    )

    assert [row["member_id"] for row in binding["member_snapshots"]] == [
        "architecture",
        "operations",
    ]
    assert all(len(row["snapshot_digest"]) == 64 for row in binding["member_snapshots"])
    assert get_bound_agent_group(
        "review-board",
        "alice",
        "conv-1",
        conversation_store=conversations,
        resource_store=resources,
    ) == binding

    resources.items[("agent_group", "review-board")]["description"] = "Changed."
    with pytest.raises(ValueError, match="binding is stale"):
        get_bound_agent_group(
            "review-board",
            "alice",
            "conv-1",
            conversation_store=conversations,
            resource_store=resources,
        )

    configs["Operations"]["runtime_kind"] = "workflow"
    with pytest.raises(ValueError, match="runtime_kind llm"):
        bind_agent_group(
            "review-board",
            "alice",
            "conv-1",
            resource_store=resources,
            conversation_store=conversations,
            service_registry=services,
        )


def test_group_bind_action_activates_the_bound_workflow_instance(monkeypatch):
    import json
    from unittest.mock import MagicMock

    from core import FlowFile
    from tasks.ai.actions._agentres_k5 import _handle_agentres_k5

    store = MagicMock()
    store.get_extra.return_value = {}
    monkeypatch.setattr(
        "core.agent_group_resources.bind_agent_group_instance",
        lambda group_name, instance_name, user_id, conversation_id, preempt_policy: {
            "group_name": group_name,
            "instance_name": instance_name,
            "binding": {"binding_digest": "a" * 64},
        },
    )
    flowfile = FlowFile()

    result = _handle_agentres_k5(
        None,
        "bind_agent_group",
        {
            "conversation_id": "conv-1",
            "group_name": "review-board",
            "instance_name": "Review Board",
        },
        store,
        "alice",
        flowfile,
    )

    assert result == [flowfile]
    assert json.loads(flowfile.content) == {
        "ok": True,
        "group_name": "review-board",
        "instance_name": "Review Board",
        "binding_digest": "a" * 64,
    }
    store.set_extra.assert_called_once_with(
        "conv-1", "active_resources", {"agent": "Review Board"}
    )


def test_group_list_action_returns_only_summary_and_binding_state(monkeypatch):
    import json
    from unittest.mock import MagicMock

    from core import FlowFile
    from tasks.ai.actions._agentres_k5 import _handle_agentres_k5

    resources = MagicMock()
    resources.list_all.return_value = [{
        "name": "review-board",
        "description": "Bounded review.",
        "_scope": "user",
        "members": [{"private": "one"}, {"private": "two"}],
        "secret": "must-not-return",
    }]
    monkeypatch.setattr(
        "core.resource_store.ResourceStore.instance",
        classmethod(lambda cls: resources),
    )
    store = MagicMock()
    store.get_extra.return_value = {"review-board": {"private": "binding"}}
    flowfile = FlowFile()

    result = _handle_agentres_k5(
        None,
        "list_agent_groups",
        {"conversation_id": "conv-1"},
        store,
        "alice",
        flowfile,
    )

    assert result == [flowfile]
    assert json.loads(flowfile.content) == {
        "groups": [{
            "name": "review-board",
            "description": "Bounded review.",
            "scope": "user",
            "member_count": 2,
            "bound": True,
        }],
    }

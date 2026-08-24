"""WP2 workflow resource, publish, and binding contracts."""

from __future__ import annotations

from unittest.mock import MagicMock
import json

import pytest

from core._service_defs import ServiceDef
from core.flow_authoring import FlowAuthoringService, FlowValidationFailed
from core.workflow_agent_contracts import WorkflowInstanceConfig
from core.workflow_agent_resources import (
    assert_flow_version_unreferenced,
    bind_agent_workflow,
    list_compatible_agent_workflows,
    resolve_exact_agent_workflow,
    snapshot_agent_workflow_services,
    validate_agent_workflow_definition,
    validate_pfp_workflow_agent_dependency,
)

LIMITS = {
    "max_duration_seconds": 60,
    "max_llm_calls": 2,
    "max_flowfiles": 10,
    "max_fanout": 2,
}
EFFECTS = ["resource.read"]


def _flow(**updates):
    value = {
        "id": "wiki",
        "name": "Wiki",
        "version": "1.0.0",
        "kind": "agent_workflow",
        "agent_contract": {
            "version": 1,
            "input": {"port": "request"},
            "terminal": {"port": "response"},
            "parameters": {"root": {"type": "string", "required": True}},
            "supported_preempt_policies": ["checkpoint"],
            "allowed_effects": EFFECTS,
        },
        "tasks": {
            "request": {"type": "inputPort", "parameters": {"port_name": "request"}},
            "work": {"type": "agentWorkflowInput", "parameters": {}},
            "response": {"type": "outputPort", "parameters": {"port_name": "response"}},
        },
        "relations": [
            {"from": "request", "to": "work", "type": "success"},
            {"from": "work", "to": "response", "type": "success"},
        ],
        "services": {},
        "parameters": {},
    }
    value.update(updates)
    return value


def test_workflow_binding_requires_exact_version_and_limits():
    with pytest.raises(ValueError, match="exact version"):
        WorkflowInstanceConfig.from_dict({
            "flow_fqn": "pawflow.wiki", "input_port": "request",
            "terminal_port": "response", "preempt_policy": "checkpoint",
            "allowed_effects": EFFECTS,
            "limits": LIMITS,
        })
    parsed = WorkflowInstanceConfig.from_dict({
        "flow_fqn": "pawflow.wiki:1.0.0", "input_port": "request",
        "terminal_port": "response", "preempt_policy": "checkpoint",
        "allowed_effects": EFFECTS,
    })
    assert parsed.limits.max_llm_calls == 24


def test_exact_resolver_prefers_conversation_and_pins_digest():
    repo = MagicMock()
    repo.get_flow.side_effect = [_flow(), _flow(name="User"), _flow(name="Global")]
    resolved = resolve_exact_agent_workflow(
        "pawflow.wiki:1.0.0", "alice", "conv-1", repository=repo)
    assert resolved.ref.scope == "conversation"
    assert resolved.ref.owner_id == "alice"
    assert len(resolved.ref.content_digest) == 64
    assert repo.get_flow.call_count == 1


def test_agent_workflow_validator_rejects_forbidden_tasks_cycles_and_bad_ports():
    definition = _flow()
    definition["tasks"]["request"]["type"] = "cronTrigger"
    definition["relations"].append(
        {"from": "response", "to": "request", "type": "success"})
    report = validate_agent_workflow_definition(definition)
    codes = {row["code"] for row in report["problems"]}
    assert "agent_workflow_input_port_invalid" in codes
    assert "workflow_task_forbidden" in codes
    assert "agent_workflow_cycle" in codes


def test_agent_workflow_validator_allows_an_explicitly_bounded_cycle():
    definition = _flow()
    definition["relations"].extend([
        {"from": "work", "to": "request", "type": "retry", "max_visits": 1},
    ])
    report = validate_agent_workflow_definition(definition)
    assert "agent_workflow_cycle" not in {
        row["code"] for row in report["problems"]}


def test_flow_publish_uses_agent_workflow_validator(tmp_path):
    repository = MagicMock()
    repository.get_flow.return_value = None
    repository.list_flow_versions.return_value = []
    repository.create_flow.return_value = {}
    service = FlowAuthoringService(
        repository=repository, drafts_dir=tmp_path / "drafts")
    draft = service.new(
        "pawflow", "wiki", "1.0.0", "user", "alice")
    definition = _flow()
    definition["tasks"]["work"]["type"] = "executeScript"
    service.save_draft(
        draft["draft_id"], "alice", definition,
        base_revision=draft["revision"])
    with pytest.raises(FlowValidationFailed) as exc:
        service.publish(
            draft["draft_id"], "alice", "1.0.0", parse=False)
    assert "workflow_task_forbidden" in {
        row["code"] for row in exc.value.report["problems"]}


def test_binding_validates_contract_and_serializes_resource_ref():
    repo = MagicMock()
    repo.get_flow.return_value = _flow()
    result = bind_agent_workflow({
        "flow_fqn": "pawflow.wiki:1.0.0",
        "input_port": "request",
        "terminal_port": "response",
        "preempt_policy": "checkpoint",
        "allowed_effects": EFFECTS,
        "parameters": {"root": "."},
        "limits": LIMITS,
    }, "alice", "conv-1", repository=repo)
    assert result["flow_scope"] == "conversation"
    assert result["flow_ref"]["name"] == "pawflow.wiki:1.0.0"

    with pytest.raises(ValueError, match="required: root"):
        bind_agent_workflow({
            "flow_fqn": "pawflow.wiki:1.0.0",
            "input_port": "request", "terminal_port": "response",
            "preempt_policy": "checkpoint", "allowed_effects": EFFECTS,
            "limits": LIMITS,
        }, "alice", "conv-1", repository=repo)


def test_binding_omits_empty_optional_service_reference():
    definition = _flow()
    definition["agent_contract"]["parameters"]["reviewer_llm"] = {
        "type": "service_ref", "capability": "llm", "required": False}
    repo = MagicMock()
    repo.get_flow.return_value = definition

    result = bind_agent_workflow({
        "flow_fqn": "pawflow.wiki:1.0.0",
        "input_port": "request",
        "terminal_port": "response",
        "preempt_policy": "checkpoint",
        "allowed_effects": EFFECTS,
        "parameters": {"root": ".", "reviewer_llm": ""},
        "limits": LIMITS,
    }, "alice", "conv-1", repository=repo)

    assert result["parameters"] == {"root": "."}


def test_binding_accepts_a_visible_summarizer_reference(monkeypatch):
    definition = _flow()
    definition["agent_contract"]["parameters"]["extractor"] = {
        "type": "service_ref", "capability": "llm_resolvable", "required": True}
    repo = MagicMock()
    repo.get_flow.return_value = definition
    summarizer = ServiceDef(
        service_id="Summary", service_type="summarizer",
        scope="user", scope_id="alice", created_at=1,
        config={"llm_service": "Writer"})
    writer = ServiceDef(
        service_id="Writer", service_type="llmConnection",
        scope="user", scope_id="alice", created_at=2,
        config={"provider": "openai"})
    registry = MagicMock()
    registry.resolve_all.return_value = {
        "Summary": summarizer, "Writer": writer}
    monkeypatch.setattr(
        "core.service_registry.ServiceRegistry.get_instance",
        lambda: registry)

    result = bind_agent_workflow({
        "flow_fqn": "pawflow.wiki:1.0.0",
        "input_port": "request",
        "terminal_port": "response",
        "preempt_policy": "checkpoint",
        "allowed_effects": EFFECTS,
        "parameters": {"root": ".", "extractor": "Summary"},
        "limits": LIMITS,
    }, "alice", "conv-1", repository=repo)

    assert result["parameters"]["extractor"] == "Summary"

    direct = bind_agent_workflow({
        "flow_fqn": "pawflow.wiki:1.0.0",
        "input_port": "request",
        "terminal_port": "response",
        "preempt_policy": "checkpoint",
        "allowed_effects": EFFECTS,
        "parameters": {"root": ".", "extractor": "Writer"},
        "limits": LIMITS,
    }, "alice", "conv-1", repository=repo)
    assert direct["parameters"]["extractor"] == "Writer"


def test_service_snapshot_contains_scope_and_revision_but_no_secret():
    definition = _flow()
    definition["agent_contract"]["parameters"]["writer"] = {
        "type": "service_ref", "capability": "llm_resolvable", "required": True}
    binding = WorkflowInstanceConfig.from_dict({
        "flow_fqn": "pawflow.wiki:1.0.0",
        "flow_scope": "global",
        "input_port": "request",
        "terminal_port": "response",
        "preempt_policy": "checkpoint",
        "allowed_effects": EFFECTS,
        "parameters": {"root": ".", "writer": "Writer"},
    })
    service = ServiceDef(
        service_id="Writer", service_type="llmConnection",
        scope="user", scope_id="alice", created_at=1,
        config={"provider": "openai", "api_key": "top-secret"})
    registry = MagicMock()
    registry.resolve_all.return_value = {"Writer": service}

    snapshot = snapshot_agent_workflow_services(
        binding, definition, "alice", "conv-1", registry=registry)

    assert snapshot["bindings"] == {"writer": "Writer"}
    assert snapshot["services"]["Writer"]["scope"] == "user"
    assert len(snapshot["services"]["Writer"]["definition_revision"]) == 64
    assert "top-secret" not in str(snapshot)


def test_service_snapshot_includes_direct_agent_llm_call_binding():
    definition = _flow()
    definition["tasks"]["work"] = {
        "type": "agentLLMCall", "parameters": {"service": "Writer"}}
    binding = WorkflowInstanceConfig.from_dict({
        "flow_fqn": "pawflow.wiki:1.0.0",
        "flow_scope": "global",
        "input_port": "request",
        "terminal_port": "response",
        "preempt_policy": "checkpoint",
        "allowed_effects": EFFECTS,
        "parameters": {"root": "."},
    })
    service = ServiceDef(
        service_id="Writer", service_type="llmConnection",
        scope="global", scope_id="__global__", created_at=1,
        config={"provider": "openai"})
    registry = MagicMock()
    registry.resolve_all.return_value = {"Writer": service}

    snapshot = snapshot_agent_workflow_services(
        binding, definition, "alice", "conv-1", registry=registry)

    assert snapshot["bindings"]["task:work"] == "Writer"
    assert snapshot["services"]["Writer"]["service_type"] == "llmConnection"


def test_service_snapshot_resolves_summarizer_to_its_linked_llm():
    definition = _flow()
    definition["agent_contract"]["parameters"]["writer"] = {
        "type": "service_ref", "capability": "llm_resolvable", "required": True}
    definition["tasks"]["work"] = {
        "type": "agentLLMCall", "parameters": {"service": "${writer}"}}
    binding = WorkflowInstanceConfig.from_dict({
        "flow_fqn": "pawflow.wiki:1.0.0",
        "flow_scope": "global",
        "input_port": "request",
        "terminal_port": "response",
        "preempt_policy": "checkpoint",
        "allowed_effects": EFFECTS,
        "parameters": {"root": ".", "writer": "Summary"},
    })
    summarizer = ServiceDef(
        service_id="Summary", service_type="summarizer",
        scope="user", scope_id="alice", created_at=1,
        config={"llm_service": "Writer"})
    writer = ServiceDef(
        service_id="Writer", service_type="llmConnection",
        scope="user", scope_id="alice", created_at=2,
        config={"provider": "openai"})
    registry = MagicMock()
    registry.resolve_all.return_value = {
        "Summary": summarizer, "Writer": writer}

    snapshot = snapshot_agent_workflow_services(
        binding, definition, "alice", "conv-1", registry=registry)

    assert snapshot["bindings"]["writer"] == "Writer"
    assert snapshot["services"]["Summary"]["resolved_llm_service"] == "Writer"
    assert snapshot["services"]["Writer"]["service_type"] == "llmConnection"


def test_flow_reference_protection_matches_scope_and_exact_version():
    store = MagicMock()
    store.list_conversations.return_value = [{
        "conversation_id": "conv-1", "user_id": "alice"}]
    store.get_extra.return_value = {
        "Wiki": {
            "runtime_kind": "workflow",
            "workflow": {
                "flow_fqn": "pawflow.wiki:1.0.0",
                "flow_scope": "user",
            },
        },
    }
    with pytest.raises(ValueError, match="conv-1/Wiki"):
        assert_flow_version_unreferenced(
            "pawflow.wiki:1.0.0", "user", "alice",
            conversation_store=store)
    assert_flow_version_unreferenced(
        "pawflow.wiki:2.0.0", "user", "alice",
        conversation_store=store)


def test_pfp_workflow_agent_requires_and_selects_exact_flow_object(monkeypatch):
    monkeypatch.setenv("PAWFLOW_WORKFLOW_AGENTS_ENABLED", "1")
    data = {
        "prompt": "",
        "runtime_defaults": {
            "kind": "workflow",
            "workflow": {
                "flow_fqn": "example.wiki:1.0.0",
                "input_port": "request",
                "terminal_port": "response",
                "preempt_policy": "checkpoint",
                "allowed_effects": EFFECTS,
            },
        },
    }
    package = {"manifest": {"objects": [{
        "id": "flow:wiki", "type": "flow", "fqn": "example.wiki:1.0.0",
    }]}}
    with pytest.raises(ValueError, match="declare object dependency"):
        validate_pfp_workflow_agent_dependency(
            data, {"id": "agent:wiki", "requires": []}, package)
    assert validate_pfp_workflow_agent_dependency(
        data, {"id": "agent:wiki", "requires": ["flow:wiki"]}, package,
    ) == "flow:wiki"


def test_workflow_catalog_lists_exact_visible_versions_and_redacts_source(
        tmp_path, monkeypatch):
    import core.paths as paths
    from core.repository import ScopedRepository

    monkeypatch.setattr(paths, "REPOSITORY_DIR", tmp_path / "repository")
    monkeypatch.setattr(
        "core.workflow_agent_resources._scope_candidates",
        lambda user_id, conversation_id: iter((
            ("conversation", "conv", user_id, conversation_id),
            ("user", "user", user_id, ""),
            ("global", "global", "", ""),
        )))
    repo = ScopedRepository()
    global_flow = _flow(
        fqn="pawflow.wiki:1.0.0", description="Global",
        services={"secret": {"api_key": "do-not-return"}},
        prompt="hidden source body")
    repo.create_flow(
        "pawflow.wiki:1.0.0", "global", global_flow)
    repo.publish_flow_version(
        "pawflow.wiki:2.0.0", "global",
        _flow(version="2.0.0", fqn="pawflow.wiki:2.0.0"))
    repo.create_flow(
        "pawflow.wiki:1.0.0", "conv",
        _flow(fqn="pawflow.wiki:1.0.0", description="Conversation"),
        user_id="alice", conv_id="conv-1")
    repo.create_flow(
        "pawflow.regular:1.0.0", "user",
        {"id": "regular", "name": "Regular", "version": "1.0.0"},
        user_id="alice")

    rows = list_compatible_agent_workflows("alice", "conv-1")

    assert [row["flow_fqn"] for row in rows] == [
        "pawflow.wiki:1.0.0", "pawflow.wiki:2.0.0"]
    assert rows[0]["scope"] == "conversation"
    assert rows[0]["description"] == "Conversation"
    assert rows[0]["parameters"] == {
        "root": {"type": "string", "required": True,
                 "capability": None, "default": None,
                 "label": None, "description": None}}
    assert "tasks" not in rows[0]
    assert "services" not in rows[0]
    assert "hidden source body" not in str(rows)
    assert "do-not-return" not in str(rows)


def test_workflow_catalog_action_is_gated_and_redacted(monkeypatch):
    from core import FlowFile
    from tasks.ai.actions._agentres_k5 import _handle_agentres_k5

    monkeypatch.setenv("PAWFLOW_WORKFLOW_AGENTS_ENABLED", "1")
    monkeypatch.setattr(
        "core.workflow_agent_resources.list_compatible_agent_workflows",
        lambda user_id, conversation_id: [{"flow_fqn": "pawflow.wiki:1.0.0"}])
    flowfile = FlowFile()
    result = _handle_agentres_k5(
        None, "list_agent_workflow_versions",
        {"conversation_id": "conv-1"}, MagicMock(), "alice", flowfile)
    assert result == [flowfile]
    assert json.loads(flowfile.content) == {
        "enabled": True,
        "workflows": [{"flow_fqn": "pawflow.wiki:1.0.0"}],
    }

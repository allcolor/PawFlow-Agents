"""WP9 deterministic legacy PlanStore flow compilation and publication."""

import copy

import pytest

from tasks import register_all_tasks

register_all_tasks()

from core.flow_authoring import FlowAuthoringService
from core.flow_definition_validator import FlowDefinitionValidator
from core.flow_layout_contracts import relation_id_seed
from core.plan_migration_flow import (
    build_legacy_flow_definition,
    publish_legacy_flow,
)
from core.repository import ScopedRepository


def _agent_ref(name):
    return {
        "schema_version": 1,
        "resource_type": "agent",
        "name": name,
        "scope": "conversation",
        "owner_id": "conv-1",
        "package_id": None,
        "package_version": None,
        "version": None,
        "content_digest": ("a" if name == "builder" else "b") * 64,
        "source_id": f"repository:conversation:{name}",
    }


def _conversion():
    digest = "c" * 64
    return {
        "schema_version": 1,
        "mode": "resume",
        "source_digest": digest,
        "flow": {
            "fqn": f"legacy_plans.plan_{digest[:16]}:1.0.0",
            "scope": "conversation",
            "owner_id": "conv-1",
            "replayable": True,
        },
        "proposal": {
            "proposal_id": f"wp_legacy_{digest[:20]}",
            "status": "running",
        },
        "run": {
            "run_id": f"fr_legacy_{digest[:20]}",
            "status": "waiting",
            "checkpoint": {
                "schema_version": 1,
                "kind": "legacy_plan_verification",
                "plan_id": "p_1234",
                "step": 1,
                "executor": "builder",
                "verifier": "reviewer",
                "schedule": {
                    "conversation_id": "conv-1",
                    "key": "conv-1::plan::p_1234::verify1::reviewer",
                    "recheck_at": 30.0,
                    "user_id": "user-a",
                    "reason": "[plan_verify:p_1234:1:builder] (reviewer)",
                    "created_at": 20.0,
                },
            },
        },
        "steps": [{
            "index": 1,
            "description": "Build the artifact",
            "status": "pending_verification",
            "paused": False,
            "note": "ready",
            "legacy_task_id": "",
            "executor": "builder",
            "executor_adapter": {
                "adapter": "invokeWorkflowAgent",
                "agent_ref": _agent_ref("builder"),
            },
            "verifier": "reviewer",
            "verifier_adapter": {
                "adapter": "invokeWorkflowAgent",
                "agent_ref": _agent_ref("reviewer"),
            },
        }],
        "imported_plan": {
            "source_id": "p_1234",
            "source_path": "user-a/conv-1/p_1234.json",
            "user_id": "user-a",
            "conversation_id": "conv-1",
            "title": "Imported plan",
            "created_by": "planner",
            "created_at": 10.0,
            "updated_at": 20.0,
            "classification": "waiting_verification",
        },
    }


@pytest.fixture
def authoring(tmp_path, monkeypatch):
    import core.paths as paths

    monkeypatch.setattr(paths, "REPOSITORY_DIR", tmp_path / "repository")
    monkeypatch.setattr(paths, "RUNTIME_DIR", tmp_path / "runtime")
    ScopedRepository.reset()
    FlowAuthoringService.reset()
    yield FlowAuthoringService.instance()
    ScopedRepository.reset()
    FlowAuthoringService.reset()


def test_compiler_builds_valid_exact_durable_flow_with_stable_resume_target():
    definition = build_legacy_flow_definition(_conversion())

    assert definition["execution_mode"] == "durable_one_shot"
    assert definition["run_contract"] == {"mode": "durable_one_shot"}
    assert list(definition["tasks"]) == [
        "step_1_execute", "step_1_verify", "complete"]
    assert definition["tasks"]["step_1_execute"]["parameters"]["agent_ref"] == (
        _agent_ref("builder"))
    assert definition["tasks"]["step_1_verify"]["parameters"]["agent_ref"] == (
        _agent_ref("reviewer"))
    expected_relations = [
        {"from": "step_1_execute", "to": "step_1_verify", "type": "completed"},
        {"from": "step_1_verify", "to": "complete", "type": "completed"},
    ]
    assert definition["relations"] == [
        {**relation, "relation_id": relation_id_seed(relation)}
        for relation in expected_relations
    ]
    assert definition["entries"] == ["step_1_execute"]
    assert definition["exits"] == ["complete"]
    assert definition["migration"]["resume_task_id"] == "step_1_verify"
    assert definition["migration"]["source_digest"] == "c" * 64
    assert FlowDefinitionValidator.validate(definition)["ok"]


def test_compiler_rejects_checkpoint_that_does_not_match_step_and_verifier():
    conversion = _conversion()
    conversion["run"]["checkpoint"]["verifier"] = "other"

    with pytest.raises(ValueError, match="checkpoint verifier"):
        build_legacy_flow_definition(conversion)


def test_publication_is_immutable_and_idempotent_for_exact_source(
        authoring):
    conversion = _conversion()

    first = publish_legacy_flow(conversion, authoring=authoring)
    second = publish_legacy_flow(conversion, authoring=authoring)

    assert first == second
    assert first["resource_type"] == "flow"
    assert first["name"] == conversion["flow"]["fqn"]
    assert first["scope"] == "conversation"
    assert first["owner_id"] == "user-a"
    assert len(first["content_digest"]) == 64
    assert authoring.versions(
        conversion["flow"]["fqn"], "conv", "user-a", "conv-1",
    )["versions"] == ["1.0.0"]


def test_publication_rejects_existing_prefix_collision(authoring):
    conversion = _conversion()
    publish_legacy_flow(conversion, authoring=authoring)
    collision = copy.deepcopy(conversion)
    collision["source_digest"] = "c" * 63 + "d"

    with pytest.raises(ValueError, match="different legacy source"):
        publish_legacy_flow(collision, authoring=authoring)

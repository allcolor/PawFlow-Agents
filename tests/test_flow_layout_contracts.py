import copy

from core.flow_authoring import FlowAuthoringService
from core.flow_definition_validator import FlowDefinitionValidator
from core.flow_layout_contracts import (
    ensure_relation_ids,
    migrate_legacy_presentation,
    relation_id_seed,
)


def _legacy():
    return {
        "name": "layout-contract",
        "tasks": {
            "source": {"type": "source", "parameters": {}},
            "sink": {"type": "sink", "parameters": {}},
        },
        "services": {},
        "groups": {},
        "relations": [
            {"from": "source", "to": "sink", "type": "success"},
        ],
        "entries": ["source"],
        "exits": ["sink"],
        "layout": {"nodes": {
            "source": {"x": 10, "y": 20},
            "sink": {"x": 100, "y": 20},
        }},
    }


def test_legacy_migration_is_deterministic_and_non_mutating():
    legacy = _legacy()
    original = copy.deepcopy(legacy)
    first = migrate_legacy_presentation(legacy)
    second = migrate_legacy_presentation(legacy)
    assert legacy == original
    assert first == second
    assert "layout" not in first
    assert first["default_layout_id"] == "technical"
    assert first["layouts"]["technical"]["nodes"] == legacy["layout"]["nodes"]
    relation = first["relations"][0]
    assert relation["relation_id"] == relation_id_seed(relation)


def test_duplicate_legacy_relations_get_stable_collision_suffix():
    flow = _legacy()
    flow["relations"].append(copy.deepcopy(flow["relations"][0]))
    migrated = ensure_relation_ids(flow)
    ids = [item["relation_id"] for item in migrated["relations"]]
    assert ids[1] == ids[0] + "_2"


def test_versioned_layout_and_executor_profiles_validate(monkeypatch):
    definition = migrate_legacy_presentation(_legacy())
    definition["executor_profiles"] = {
        "writer": {
            "id": "writer", "kind": "llm", "service_ref": "llm",
            "model": "model", "context_policy": "block_input_only",
            "limits": {"max_calls": 1},
        },
        "reviewer": {
            "id": "reviewer", "kind": "workflow_agent",
            "agent_ref": {
                "scope": "conversation", "name": "Reviewer",
                "version": "1.0.0", "content_digest": "sha256:value",
            },
        },
    }
    definition["tasks"]["source"]["execution"] = {
        "strategy": "primary_then_review",
        "roles": {
            "primary": {"executor_profile": "inherited:primary"},
            "reviewer": {"executor_profile": "inherited:reviewer"},
        },
        "review_policy": {
            "on_reject": "redo_primary_with_review",
            "max_revisions": 2,
            "feedback_input": "review.feedback",
            "result_input": "review.candidate",
        },
        "validation_criteria": [{
            "id": "scope", "kind": "semantic",
            "description": "Requested scope is respected", "required": True,
        }],
    }
    definition["executor_defaults"] = {
        "primary": "writer", "reviewer": "reviewer"}
    report = FlowDefinitionValidator.validate(
        definition, task_types={"source", "sink"}, service_types=set())
    assert report["ok"], report


def test_layout_rejects_nan_unsafe_annotation_and_unknown_relation():
    definition = migrate_legacy_presentation(_legacy())
    layout = definition["layouts"]["technical"]
    layout["nodes"]["source"]["x"] = float("nan")
    layout["relations"]["rel_missing"] = {"routing": "bezier"}
    layout["annotations"]["ann"] = {
        "id": "ann", "type": "markdown", "x": 0, "y": 0,
        "width": 100, "height": 50, "content": "<script>alert(1)</script>",
    }
    report = FlowDefinitionValidator.validate(
        definition, task_types={"source", "sink"}, service_types=set())
    codes = {item["code"] for item in report["problems"]}
    assert {
        "non_finite_geometry", "unknown_layout_relation",
        "unsafe_annotation_content",
    }.issubset(codes)


def test_executor_profile_rejects_secrets_and_missing_role():
    definition = migrate_legacy_presentation(_legacy())
    definition["executor_profiles"] = {
        "writer": {
            "id": "writer", "kind": "llm", "service_ref": "llm",
            "model": "model", "context_policy": "block_input_only",
            "api_key": "forbidden",
        },
    }
    definition["tasks"]["source"]["execution"] = {
        "strategy": "primary_then_review",
        "roles": {"primary": {"executor_profile": "writer"}},
        "review_policy": {
            "on_reject": "redo_primary_with_review",
            "max_revisions": 2,
            "feedback_input": "review.feedback",
            "result_input": "review.candidate",
        },
        "validation_criteria": [],
    }
    report = FlowDefinitionValidator.validate(
        definition, task_types={"source", "sink"}, service_types=set())
    codes = {item["code"] for item in report["problems"]}
    assert "executor_profile_contains_secret" in codes
    assert "missing_execution_role" in codes


def test_reviewed_step_rejects_duplicate_or_incomplete_criteria():
    definition = migrate_legacy_presentation(_legacy())
    definition["executor_profiles"] = {
        "worker": {
            "id": "worker", "kind": "llm", "service_ref": "llm",
            "model": "m", "context_policy": "block_input_only",
        },
        "reviewer": {
            "id": "reviewer", "kind": "agent",
            "agent_ref": {
                "scope": "conversation", "name": "reviewer",
                "version": "1", "content_digest": "sha256:x",
            },
        },
    }
    definition["tasks"]["source"]["execution"] = {
        "strategy": "primary_then_review",
        "roles": {
            "primary": {"executor_profile": "worker"},
            "reviewer": {"executor_profile": "reviewer"},
        },
        "review_policy": {
            "on_reject": "redo_primary_with_review",
            "max_revisions": 3,
            "feedback_input": "review.feedback",
            "result_input": "review.candidate",
        },
        "validation_criteria": [
            {"id": "tests", "kind": "expression", "description": "Tests pass",
             "required": True},
            {"id": "tests", "kind": "semantic", "description": "",
             "required": "yes"},
        ],
    }
    report = FlowDefinitionValidator.validate(
        definition, task_types={"source", "sink"}, service_types=set())
    codes = {item["code"] for item in report["problems"]}
    assert {
        "missing_validation_expression",
        "invalid_validation_criterion_id",
        "invalid_validation_criterion_description",
        "invalid_validation_criterion_required",
    }.issubset(codes)


def test_layout_only_diff_has_no_runtime_impact():
    before = migrate_legacy_presentation(_legacy())
    after = copy.deepcopy(before)
    after["layouts"]["technical"]["nodes"]["source"]["x"] = 42
    diff = FlowAuthoringService.diff(before, after)
    assert diff["runtime_impact"] is False
    assert any(
        change["kind"] == "layout"
        and change["id"] == "technical"
        and change["runtime_impact"] is False
        for change in diff["changes"]
    )


def test_relation_diff_uses_stable_relation_id_across_endpoint_change():
    before = migrate_legacy_presentation(_legacy())
    after = copy.deepcopy(before)
    after["tasks"]["other"] = {"type": "sink", "parameters": {}}
    after["relations"][0]["to"] = "other"
    relation_id = before["relations"][0]["relation_id"]
    diff = FlowAuthoringService.diff(before, after)
    relation_changes = [
        item for item in diff["changes"] if item["kind"] == "relation"]
    assert relation_changes == [{
        "op": "changed", "kind": "relation", "id": relation_id,
        "runtime_impact": True, "fields": ["to"],
    }]

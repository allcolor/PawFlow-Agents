from core.ui_surface import available_ui_surface_actions
from core.workflow_proposal_surfaces import (
    workflow_proposal_run_surface,
    workflow_proposal_surface,
)


def _proposal(**overrides):
    value = {
        "proposal_id": "wp_123",
        "state_revision": 4,
        "user_id": "alice",
        "conversation_id": "conv",
        "status": "user_review",
        "title": "Release",
        "summary": "Review release flow",
        "draft_id": "draft_1",
        "draft_revision": 7,
        "planner_reviewed_revision": 7,
        "definition_digest": "same",
        "planner_reviewed_digest": "same",
        "review_round": 2,
        "created_by": "Planner",
        "created_at": "2026-08-25T00:00:00+00:00",
        "updated_at": "2026-08-25T00:01:00+00:00",
    }
    value.update(overrides)
    return value


def test_proposal_is_portable_semantic_surface_with_comment_and_actions():
    surface = workflow_proposal_surface(_proposal())
    assert surface["surface_id"] == "uis_wp_123"
    assert surface["revision"] == 4
    assert surface["semantic"]["fields"][0]["id"] == "comment"
    actions = {row["id"]: row for row in surface["semantic"]["actions"]}
    assert actions["send_to_planner"]["input_schema"]["properties"] == {
        "comment": {"type": "string"}}
    assert actions["open_editor"]["requires"] == ["workflow.editor"]
    assert actions["accept"]["terminal"] is True


def test_cli_can_send_but_editor_action_gets_handoff():
    surface = workflow_proposal_surface(_proposal())
    actions = {
        row["id"]: row for row in available_ui_surface_actions(
            surface, {"semantic.form", "workflow.reviewed-revision"})
    }
    assert actions["send_to_planner"]["available"] is True
    assert actions["open_editor"]["available"] is False
    assert actions["open_editor"]["handoff"]["message"]


def test_unreviewed_revision_cannot_be_accepted_on_any_client():
    surface = workflow_proposal_surface(_proposal(
        draft_revision=8, planner_reviewed_revision=7))
    accept = next(
        row for row in surface["semantic"]["actions"] if row["id"] == "accept")
    assert accept["requires"] == ["workflow.reviewed-revision"]
    assert "planner" in accept["handoff"]["message"]


def test_planner_wait_state_remains_semantically_visible():
    surface = workflow_proposal_surface(_proposal(status="planner_review"))
    assert surface["status"] == "waiting_for_compatible_client"
    assert [a["id"] for a in surface["semantic"]["actions"]] == [
        "open_editor", "cancel"]


def test_terminal_proposal_keeps_portable_inspect_and_replay_actions():
    surface = workflow_proposal_surface(_proposal(
        status="completed", run_ids=["fr_1"]))
    assert surface["status"] == "open"
    actions = {row["id"]: row for row in surface["semantic"]["actions"]}
    assert actions["inspect_run"]["dispatch"]["arguments"]["run_id"] == "fr_1"
    assert actions["replay"]["dispatch"]["action"] == "workflow_proposal_replay"


def test_proposal_preview_is_derived_and_contains_no_task_parameters():
    surface = workflow_proposal_surface(_proposal(), definition={
        "tasks": {
            "prepare": {"type": "log", "parameters": {"message": "secret body"}},
            "done": {"type": "completeFlowRun", "parameters": {}},
        },
        "groups": {},
        "relations": [{"from": "prepare", "to": "done", "type": "success"}],
    })
    presentation = surface["presentation"]
    assert presentation["component"] == "pawflow.builtin:workflow-mini-graph"
    assert [row["id"] for row in presentation["props"]["blocks"]] == [
        "done", "prepare"]
    assert presentation["props"]["relations"][0]["from"] == "prepare"
    assert "secret body" not in str(presentation)


def test_run_inspection_surface_is_semantic_and_replayable():
    surface = workflow_proposal_run_surface(_proposal(
        status="completed", run_ids=["fr_1"]), {
            "run_id": "fr_1", "status": "completed", "generation": 2,
            "flow_ref": {"name": "plans.release:1.0.0"},
            "terminal": {"summary": "done"},
        })
    assert surface["producer"] == {"kind": "flow_run", "id": "fr_1"}
    assert "plans.release:1.0.0" in surface["semantic"]["body"]
    assert surface["semantic"]["actions"][0]["id"] == "replay"

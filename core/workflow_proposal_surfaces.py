"""Portable UiSurface projection for workflow-proposal review."""

from __future__ import annotations

from typing import Any

from core.flow_authoring import DraftNotFound
from core.ui_surface import FORMAT, validate_ui_surface

_TERMINAL = {"completed", "failed", "cancelled"}


def workflow_proposal_surface(
    proposal: dict[str, Any], definition: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project durable proposal state into a renderer-independent surface."""
    proposal_id = str(proposal.get("proposal_id") or "")
    status = str(proposal.get("status") or "")
    revision = int(proposal.get("state_revision") or 1)
    common = {
        "proposal_id": proposal_id,
        "state_revision": revision,
    }
    actions: list[dict[str, Any]] = [{
        "id": "open_editor",
        "label": "Open / edit flow",
        "dispatch": {
            "action": "open_client_uri",
            "arguments": {
                "uri": "pawflow://workflow-editor",
                "draft_id": str(proposal.get("draft_id") or ""),
                "proposal_id": proposal_id,
            },
        },
        "requires": ["workflow.editor"],
        "handoff": {
            "message": "Open this review in a client with a workflow editor.",
            "uri": f"/chat?conversation_id={proposal.get('conversation_id', '')}",
        },
    }]
    fields: list[dict[str, Any]] = []
    if status == "user_review":
        fields.append({
            "id": "comment",
            "type": "string",
            "label": "Comment for the planner",
            "placeholder": "Optional",
            "required": False,
        })
        actions.extend([{
            "id": "send_to_planner",
            "label": "Send to planner",
            "kind": "primary",
            "input_schema": {
                "type": "object",
                "properties": {"comment": {"type": "string"}},
            },
            "dispatch": {
                "action": "workflow_proposal_submit_to_planner",
                "arguments": common,
            },
        }, {
            "id": "accept",
            "label": "Accept",
            "kind": "success",
            "dispatch": {
                "action": "workflow_proposal_accept",
                "arguments": common,
            },
            "terminal": True,
        }])
    if status == "accepted":
        actions.append({
            "id": "approve",
            "label": "Approve and run",
            "kind": "success",
            "dispatch": {
                "action": "workflow_proposal_approve",
                "arguments": common,
            },
            "confirm": "Publish this exact revision and start its workflow?",
        })
    run_ids = [str(value) for value in (proposal.get("run_ids") or []) if value]
    if run_ids:
        run_arguments = {**common, "run_id": run_ids[-1]}
        actions.append({
            "id": "inspect_run",
            "label": "Inspect run",
            "dispatch": {
                "action": "workflow_proposal_inspect_run",
                "arguments": run_arguments,
            },
        })
        if status in _TERMINAL:
            actions.append({
                "id": "replay",
                "label": "Replay",
                "kind": "primary",
                "dispatch": {
                    "action": "workflow_proposal_replay",
                    "arguments": run_arguments,
                },
                "confirm": "Replay the same immutable workflow with fresh authorization?",
            })
    if status not in _TERMINAL:
        actions.append({
            "id": "cancel",
            "label": "Cancel",
            "kind": "danger",
            "dispatch": {
                "action": "workflow_proposal_cancel",
                "arguments": common,
            },
            "confirm": "Cancel this workflow proposal?",
            "terminal": True,
        })
    can_accept = (
        status == "user_review"
        and int(proposal.get("draft_revision") or 0)
        == int(proposal.get("planner_reviewed_revision") or -1)
        and str(proposal.get("definition_digest") or "")
        == str(proposal.get("planner_reviewed_digest") or "#")
    )
    for action in actions:
        if action["id"] == "accept" and not can_accept:
            action["requires"] = ["workflow.reviewed-revision"]
            action["handoff"] = {
                "message": "Send the current revision to the planner before accepting.",
            }
    surface = {
        "format": FORMAT,
        "surface_id": f"uis_{proposal_id}",
        "revision": revision,
        "user_id": str(proposal.get("user_id") or ""),
        "conversation_id": str(proposal.get("conversation_id") or ""),
        "status": (
            "cancelled" if status == "cancelled"
            else "open" if status == "user_review"
            or status in {"completed", "failed"}
            else "waiting_for_compatible_client"
        ),
        "producer": {"kind": "workflow_proposal", "id": proposal_id},
        "semantic": {
            "role": "workflow-review",
            "title": str(proposal.get("title") or "Workflow proposal"),
            "summary": str(proposal.get("summary") or ""),
            "body": (
                f"Draft revision {proposal.get('draft_revision', 0)} · "
                f"review round {proposal.get('review_round', 0)} · "
                f"planner {proposal.get('created_by', '')} · state {status}"
            ),
            "fields": fields,
            "actions": actions,
        },
        "required_capabilities": [],
        "fallback": {"mode": "semantic"},
        "created_at": str(proposal.get("created_at") or ""),
        "updated_at": str(proposal.get("updated_at") or ""),
    }
    if definition is not None:
        from core.declarative_flow.projection import project_definition

        projection = project_definition(definition)
        blocks = projection["blocks"][:40]
        block_ids = {str(row["block_id"]) for row in blocks}
        relations = [
            row for row in projection["relations"]
            if str(row["from"]) in block_ids and str(row["to"]) in block_ids
        ][:80]
        surface["presentation"] = {
            "component": "pawflow.builtin:workflow-mini-graph",
            "requires": ["workflow.mini-graph"],
            "props": {
                "blocks": [{
                    "id": str(row["block_id"]),
                    "label": str(row["descriptor"].get("label") or row["block_id"]),
                    "type": str(row["descriptor"].get("type") or ""),
                } for row in blocks],
                "relations": [{
                    "from": str(row["from"]), "to": str(row["to"]),
                    "output": str(row.get("output") or ""),
                } for row in relations],
                "truncated": (
                    len(projection["blocks"]) > len(blocks)
                    or len(projection["relations"]) > len(relations)),
            },
        }
    return validate_ui_surface(surface)


def current_workflow_proposal_surface(
    proposal: dict[str, Any],
) -> dict[str, Any]:
    """Project a proposal with a live, non-persisted preview of its draft."""
    definition = None
    try:
        from core.flow_authoring import FlowAuthoringService
        draft = FlowAuthoringService.instance().load_draft(
            str(proposal.get("draft_id") or ""),
            str(proposal.get("user_id") or ""))
        definition = draft["definition"]
    except (DraftNotFound, OSError, ValueError):
        definition = None
    return workflow_proposal_surface(proposal, definition=definition)


def workflow_proposal_run_surface(
    proposal: dict[str, Any], run: dict[str, Any],
) -> dict[str, Any]:
    """Project a scoped FlowRun inspection into a portable semantic surface."""
    run_id = str(run.get("run_id") or "")
    flow_ref = run.get("flow_ref") or {}
    terminal = run.get("terminal") or {}
    lines = [
        f"Status: {run.get('status', '')}",
        f"Flow: {flow_ref.get('name', '')}",
        f"Generation: {run.get('generation', 0)}",
    ]
    if run.get("replay_of"):
        lines.append(f"Replay of: {run['replay_of']}")
    if terminal.get("summary"):
        lines.append(f"Result: {terminal['summary']}")
    if run.get("error"):
        lines.append(f"Error: {run['error']}")
    status = str(run.get("status") or "")
    surface = {
        "format": FORMAT,
        "surface_id": f"uis_{run_id}",
        "revision": max(1, int(proposal.get("state_revision") or 1)),
        "user_id": str(proposal.get("user_id") or ""),
        "conversation_id": str(proposal.get("conversation_id") or ""),
        "status": "open",
        "producer": {"kind": "flow_run", "id": run_id},
        "semantic": {
            "role": "workflow-run-inspection",
            "title": f"Run · {str(proposal.get('title') or 'Workflow')}",
            "summary": run_id,
            "body": "\n".join(lines),
            "fields": [],
            "actions": [],
        },
        "required_capabilities": [],
        "fallback": {"mode": "semantic"},
        "created_at": str(run.get("created_at") or ""),
        "updated_at": str(run.get("updated_at") or ""),
    }
    if status in {"completed", "failed", "cancelled", "timed_out", "force_stopped"}:
        surface["semantic"]["actions"].append({
            "id": "replay",
            "label": "Replay",
            "kind": "primary",
            "dispatch": {
                "action": "workflow_proposal_replay",
                "arguments": {
                    "proposal_id": str(proposal.get("proposal_id") or ""),
                    "state_revision": int(proposal.get("state_revision") or 1),
                    "run_id": run_id,
                },
            },
            "confirm": "Replay the same immutable workflow with fresh authorization?",
        })
    return validate_ui_surface(surface)


__all__ = [
    "current_workflow_proposal_surface", "workflow_proposal_run_surface",
    "workflow_proposal_surface",
]

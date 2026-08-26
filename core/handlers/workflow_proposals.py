"""Planner-facing tools for canonical workflow proposal review."""

from __future__ import annotations

import json
import logging
from typing import Any

from core.tool_handler import ToolHandler

logger = logging.getLogger(__name__)


class _WorkflowProposalHandler(ToolHandler):
    def __init__(self) -> None:
        self._conversation_id = ""
        self._user_id = ""
        self._agent_name = ""

    def set_conversation_id(self, value: str) -> None:
        self._conversation_id = value or ""

    def set_user_id(self, value: str) -> None:
        self._user_id = value or ""

    def set_agent_name(self, value: str) -> None:
        self._agent_name = value or ""

    def _context(self) -> tuple[str, str, str]:
        if not self._conversation_id or not self._user_id or not self._agent_name:
            raise ValueError(
                "user, conversation, and planner agent context are required")
        return self._user_id, self._conversation_id, self._agent_name


class ProposeWorkflowHandler(_WorkflowProposalHandler):
    @property
    def name(self) -> str:
        return "propose_workflow"

    @property
    def description(self) -> str:
        return (
            "Create a canonical conversation-scoped flow draft and send its "
            "exact revision to the user for graphical review. The user may "
            "edit it and return a newer exact revision for planner review.")

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "package": {"type": "string"},
                "name": {"type": "string"},
                "version": {"type": "string"},
                "title": {"type": "string"},
                "summary": {"type": "string"},
                "definition": {"type": "object"},
            },
            "required": [
                "package", "name", "version", "title", "definition"],
        }

    def execute(self, arguments: dict[str, Any]) -> str:
        try:
            user_id, conversation_id, planner = self._context()
            from core.flow_authoring import FlowAuthoringService
            from core.flow_layout_contracts import migrate_legacy_presentation
            from core.workflow_proposal_store import (
                WorkflowProposalStore,
                definition_digest,
            )
            definition = arguments.get("definition")
            if not isinstance(definition, dict):
                raise TypeError("definition is required")
            definition = migrate_legacy_presentation(definition)
            authoring = FlowAuthoringService.instance()
            validation = authoring.validate(definition)
            if not validation["ok"]:
                return json.dumps({
                    "error": "validation_failed",
                    "validation": validation,
                }, ensure_ascii=False)
            draft = authoring.new_from_definition(
                str(arguments.get("package") or ""),
                str(arguments.get("name") or ""),
                str(arguments.get("version") or ""),
                "conv", user_id, definition, conv_id=conversation_id,
            )
            proposal = WorkflowProposalStore.instance().create(
                user_id=user_id, conversation_id=conversation_id,
                title=str(arguments.get("title") or ""),
                summary=str(arguments.get("summary") or ""),
                draft_id=draft["draft_id"],
                draft_revision=int(draft["revision"]),
                digest=definition_digest(draft["definition"]),
                created_by=planner,
            )
            _publish(conversation_id, "workflow_proposal_created", proposal)
            return json.dumps({
                "proposal": proposal,
                "draft_id": draft["draft_id"],
                "message": (
                    "Workflow proposal created. The user can open the exact "
                    "draft in edit mode, accept it, cancel it, or return an "
                    "edited revision for review."),
            }, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001 - tool boundary serializes failures
            return f"Error: {exc}"


class GetWorkflowProposalHandler(_WorkflowProposalHandler):
    @property
    def name(self) -> str:
        return "get_workflow_proposal"

    @property
    def description(self) -> str:
        return (
            "Load one workflow proposal, its exact draft revision, digest, "
            "actor turn, and append-only review history.")

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"proposal_id": {"type": "string"}},
            "required": ["proposal_id"],
        }

    def execute(self, arguments: dict[str, Any]) -> str:
        try:
            user_id, conversation_id, _ = self._context()
            from core.workflow_proposal_store import WorkflowProposalStore
            proposal = WorkflowProposalStore.instance().get(
                str(arguments.get("proposal_id") or ""),
                user_id=user_id, conversation_id=conversation_id)
            if proposal is None:
                return "Error: workflow proposal not found"
            return json.dumps({"proposal": proposal}, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001 - tool boundary serializes failures
            return f"Error: {exc}"


class ReviewWorkflowProposalHandler(_WorkflowProposalHandler):
    @property
    def name(self) -> str:
        return "review_workflow_proposal"

    @property
    def description(self) -> str:
        return (
            "Review the exact draft revision returned by the user. Accept it, "
            "request changes, record a planner revision, or cancel. Stale "
            "state, revision, or digest fails closed.")

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "proposal_id": {"type": "string"},
                "state_revision": {"type": "integer", "minimum": 1},
                "decision": {
                    "type": "string",
                    "enum": ["accept", "revised", "request_changes", "cancel"],
                },
                "comment": {"type": "string"},
            },
            "required": ["proposal_id", "state_revision", "decision"],
        }

    def execute(self, arguments: dict[str, Any]) -> str:
        try:
            user_id, conversation_id, planner = self._context()
            from core.flow_authoring import FlowAuthoringService
            from core.workflow_proposal_store import (
                WorkflowProposalStore,
                definition_digest,
            )
            proposals = WorkflowProposalStore.instance()
            proposal_id = str(arguments.get("proposal_id") or "")
            proposal = proposals.get(
                proposal_id, user_id=user_id,
                conversation_id=conversation_id)
            if proposal is None:
                return "Error: workflow proposal not found"
            expected = int(arguments.get("state_revision"))
            if proposal["state_revision"] != expected:
                return "Error: workflow proposal state revision changed"
            decision = str(arguments.get("decision") or "")
            comment = str(arguments.get("comment") or "")
            if decision == "cancel":
                result = proposals.cancel(
                    proposal_id, expected_state_revision=expected,
                    actor_type="planner", actor_id=planner, comment=comment)
            else:
                draft = FlowAuthoringService.instance().load_draft(
                    proposal["draft_id"], user_id)
                digest = definition_digest(draft["definition"])
                if (
                    int(draft["revision"]) != proposal["draft_revision"]
                    or digest != proposal["definition_digest"]
                ):
                    proposal = proposals.note_draft_changed(
                        draft_id=proposal["draft_id"],
                        draft_revision=int(draft["revision"]),
                        digest=digest, actor_id="workflow-proposal-guard",
                        actor_type="system",
                    )
                    _publish(
                        conversation_id, "workflow_proposal_updated", proposal)
                    return json.dumps({
                        "error": "draft_changed_after_submission",
                        "message": (
                            "The submitted revision changed before review. "
                            "The proposal returned to user_review and must be "
                            "submitted again explicitly."),
                        "proposal": proposal,
                    }, ensure_ascii=False)
                result = proposals.planner_review(
                    proposal_id,
                    expected_state_revision=proposal["state_revision"],
                    draft_revision=int(draft["revision"]), digest=digest,
                    actor_id=planner, decision=decision, comment=comment,
                )
            _publish(conversation_id, "workflow_proposal_updated", result)
            return json.dumps({"proposal": result}, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001 - tool boundary serializes failures
            return f"Error: {exc}"


def _publish(conversation_id: str, event_type: str, proposal: dict) -> None:
    try:
        from core.conversation_event_bus import ConversationEventBus
        from core.ui_surface_store import publish_ui_surface
        from core.workflow_proposal_surfaces import current_workflow_proposal_surface
        surface = publish_ui_surface(current_workflow_proposal_surface(proposal))
        ConversationEventBus.instance().publish_event(
            conversation_id, event_type, {
                "proposal": proposal, "surface": surface})
    except Exception:
        logger.debug("workflow proposal SSE publish failed", exc_info=True)


__all__ = [
    "GetWorkflowProposalHandler", "ProposeWorkflowHandler",
    "ReviewWorkflowProposalHandler",
]

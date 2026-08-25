"""Canonical targeted ingress for workflow-proposal review turns."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def publish_proposal_update(
    proposal: dict[str, Any], event_type: str = "workflow_proposal_updated",
) -> dict[str, Any]:
    """Persist and broadcast the canonical portable proposal surface."""
    from core.conversation_event_bus import ConversationEventBus
    from core.ui_surface_store import publish_ui_surface
    from core.workflow_proposal_surfaces import current_workflow_proposal_surface

    surface = publish_ui_surface(current_workflow_proposal_surface(proposal))
    ConversationEventBus.instance().publish_event(
        str(proposal.get("conversation_id") or ""), event_type, {
            "proposal": proposal, "surface": surface,
        })
    return surface


def resolve_planner_target(
    conversation_id: str, planner_id: str, user_id: str,
) -> str:
    """Resolve an existing conversation member without implicit registration."""
    conversation_id = str(conversation_id or "").strip()
    planner_id = str(planner_id or "").strip()
    user_id = str(user_id or "").strip()
    if not conversation_id or not planner_id or not user_id:
        raise ValueError(
            "conversation_id, planner_id, and user_id are required")
    from core.conv_agent_config import resolve_agent_config_entry
    _, canonical_name, _ = resolve_agent_config_entry(
        conversation_id, planner_id)
    if not canonical_name:
        raise ValueError(
            f"Planner agent '{planner_id}' is not a member of this conversation")
    return canonical_name


def queue_planner_event(
    proposal: dict[str, Any], *, user_id: str, action: str,
    comment: str = "", planner_target: str = "",
) -> dict[str, Any]:
    """Persist, enqueue, and wake one exact planner for a proposal event."""
    conversation_id = str(proposal.get("conversation_id") or "")
    planner = planner_target or resolve_planner_target(
        conversation_id, str(proposal.get("created_by") or ""), user_id)
    payload = {
        "event": str(action or ""),
        "proposal_id": str(proposal.get("proposal_id") or ""),
        "draft_id": str(proposal.get("draft_id") or ""),
        "draft_revision": int(proposal.get("draft_revision") or 0),
        "definition_digest": str(proposal.get("definition_digest") or ""),
        "state_revision": int(proposal.get("state_revision") or 0),
        "review_round": int(proposal.get("review_round") or 0),
        "status": str(proposal.get("status") or ""),
        "comment": str(comment or ""),
    }
    instruction = {
        "submitted_to_planner": (
            "Review this exact draft revision. Use get_workflow_proposal, then "
            "review_workflow_proposal. Do not review a different revision."),
        "accepted": "The user accepted the planner-reviewed workflow revision.",
        "cancelled": "The user cancelled this workflow proposal.",
    }.get(payload["event"], "Workflow proposal state changed.")
    content = (
        instruction + "\n\nWorkflow proposal event:\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True))

    from core.conversation_writer import ConversationWriter
    from core.llm_client import stamp_message
    from core.pending_queue import PendingQueue
    message = stamp_message({
        "role": "user",
        "content": content,
        "source": {
            "type": "user", "name": user_id,
            "target_agent": planner,
        },
        "channel": "web",
        "workflow_proposal": payload,
    }, conversation_id)
    ConversationWriter.for_conversation(conversation_id).enqueue_message(
        dict(message), agent_name=planner, user_id=user_id)
    queued = PendingQueue.for_agent(conversation_id, planner).enqueue(
        dict(message), source="workflow_proposal")
    if not queued:
        raise RuntimeError("planner proposal message could not be queued")
    try:
        from tasks.ai.agent_loop import AgentLoopTask
        AgentLoopTask.wake_agent(
            conversation_id, planner,
            reason=f"[workflow_proposal] {payload['event']}",
            user_id=user_id, delay=0.0)
    except Exception:
        logger.debug("workflow proposal planner wake failed", exc_info=True)
    return message


__all__ = [
    "publish_proposal_update", "queue_planner_event", "resolve_planner_target",
]

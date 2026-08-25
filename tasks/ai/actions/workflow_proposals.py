"""Authenticated Web actions for durable workflow proposal co-editing."""

from __future__ import annotations

import json
import logging

from core.flow_authoring import DraftNotFound, FlowAuthoringService
from core.workflow_proposal_store import (
    ProposalConflict,
    WorkflowProposalStore,
    definition_digest,
)
from core.workflow_proposal_surfaces import (
    current_workflow_proposal_surface,
    workflow_proposal_run_surface,
)

logger = logging.getLogger(__name__)

_ACTIONS = {
    "workflow_proposal_create",
    "workflow_proposal_get",
    "workflow_proposal_list",
    "workflow_proposal_submit_to_planner",
    "workflow_proposal_accept",
    "workflow_proposal_approve",
    "workflow_proposal_cancel",
    "workflow_proposal_inspect_run",
    "workflow_proposal_replay",
}


def _publish(conversation_id, event_type, proposal):
    from core.conversation_event_bus import ConversationEventBus
    from core.ui_surface_store import publish_ui_surface
    surface = publish_ui_surface(current_workflow_proposal_surface(proposal))
    ConversationEventBus.instance().publish_event(
        conversation_id, event_type, {
            "proposal": proposal,
            "surface": surface,
        })


def _sync_current_draft(proposals, authoring, proposal, user_id):
    draft = authoring.load_draft(proposal["draft_id"], user_id)
    digest = definition_digest(draft["definition"])
    if (
        int(draft["revision"]) != proposal["draft_revision"]
        or digest != proposal["definition_digest"]
    ):
        proposal = proposals.note_draft_changed(
            draft_id=proposal["draft_id"],
            draft_revision=int(draft["revision"]), digest=digest,
            actor_id=user_id,
        )
        _publish(proposal["conversation_id"],
                 "workflow_proposal_updated", proposal)
    return proposal, draft, digest


def _authorization_ref(user_id, conversation_id, flowfile, content):
    from core.authorization_context import AuthorizationContextStore

    message_id = str(flowfile.process_id)
    authority = AuthorizationContextStore.instance().create(
        user_id=user_id, conversation_id=conversation_id,
        root_turn_id=message_id, root_message_id=message_id,
        content=content,
    )
    return {
        "context_id": authority["context_id"],
        "revision": authority["revision"],
        "root_turn_id": authority["root_turn_id"],
    }


def _start_flow_run(run, definition, published_path, flow_fqn, entry_task_id):
    from core.deployment_registry import DeploymentRegistry

    deployment = DeploymentRegistry.get_instance()
    deployed = False
    try:
        deployment.deploy(
            str(published_path), owner=run["user_id"],
            parameters=run["parameters"], source="workflow_proposal",
            conversation_id=run["conversation_id"],
            instance_id=run["deployment_instance_id"],
        )
        deployed = True
        deployment.update_flow_version(run["deployment_instance_id"], flow_fqn)
        from engine import FlowParser
        from engine.continuous_executor import ContinuousFlowExecutor
        executor = ContinuousFlowExecutor(
            FlowParser.parse(definition),
            max_workers=deployment.get(run["deployment_instance_id"]).max_workers,
            enable_checkpoints=True, parameters=run["parameters"],
        )
        from core.flow_run_coordinator import FlowRunCoordinator
        FlowRunCoordinator().attach_and_start(
            run["run_id"], executor, entry_task_id=entry_task_id)
    except Exception:
        if deployed:
            deployment.undeploy(run["deployment_instance_id"])
        raise


def _fail_flow_run(runs, proposals, run_id, error):
    run = runs.get(run_id)
    if run and run["status"] not in {
        "completed", "failed", "cancelled", "timed_out", "force_stopped",
    }:
        runs.transition(run_id, "failed", str(error))
    from core.flow_run_coordinator import FlowRunCoordinator
    FlowRunCoordinator(runs).deliver_pending_events(proposals)


def _flow_run_view(run):
    return {
        key: run.get(key) for key in (
            "run_id", "generation", "status", "flow_ref",
            "deployment_instance_id", "proposal_id", "replay_of", "terminal",
            "error", "recovery_count", "created_at", "updated_at", "terminal_at",
        )
    }


def _handle_workflow_proposals(
    self, action, body, store, user_id, flowfile,
):
    """Handle user-side proposal actions; planner reviews use agent tools."""
    if action not in _ACTIONS:
        return None

    def _reply(payload, status=""):
        flowfile.set_content(json.dumps(
            payload, ensure_ascii=False, default=str).encode())
        if status:
            flowfile.set_attribute("http.response.status", status)
        return [flowfile]

    if not user_id:
        return _reply({"error": "Authentication required"}, "401")
    from core.flow_feature_flags import workflow_proposals_enabled
    if not workflow_proposals_enabled():
        return _reply({"error": "Workflow proposals are disabled"}, "404")

    conversation_id = str(body.get("conversation_id") or "")
    if not conversation_id:
        return _reply({"error": "conversation_id is required"}, "400")
    proposals = WorkflowProposalStore.instance()
    authoring = FlowAuthoringService.instance()
    from core.flow_run_coordinator import FlowRunCoordinator
    from core.flow_run_store import FlowRunStore
    runs = FlowRunStore.instance()

    try:
        FlowRunCoordinator(runs).deliver_pending_events(proposals)
        if action == "workflow_proposal_list":
            rows = proposals.list(
                user_id=user_id, conversation_id=conversation_id)
            return _reply({
                "proposals": rows,
                "surfaces": [current_workflow_proposal_surface(row) for row in rows],
            })
        if action == "workflow_proposal_create":
            draft_id = str(body.get("draft_id") or "")
            draft = authoring.load_draft(draft_id, user_id)
            if draft.get("conv_id") != conversation_id:
                return _reply(
                    {"error": "draft does not belong to this conversation"},
                    "403",
                )
            digest = definition_digest(draft["definition"])
            planner_id = str(body.get("planner_id") or "").strip()
            from core.workflow_proposal_notifications import resolve_planner_target
            planner_id = resolve_planner_target(
                conversation_id, planner_id, user_id)
            proposal = proposals.create(
                user_id=user_id, conversation_id=conversation_id,
                title=str(body.get("title") or ""),
                summary=str(body.get("summary") or ""),
                draft_id=draft_id, draft_revision=int(draft["revision"]),
                digest=digest,
                created_by=planner_id,
            )
            _publish(conversation_id, "workflow_proposal_created", proposal)
            return _reply({
                "proposal": proposal,
                "surface": current_workflow_proposal_surface(proposal),
            })

        proposal_id = str(body.get("proposal_id") or "")
        if not proposal_id:
            return _reply({"error": "proposal_id is required"}, "400")
        proposal = proposals.get(
            proposal_id, user_id=user_id,
            conversation_id=conversation_id)
        if proposal is None:
            return _reply({"error": "Workflow proposal not found"}, "404")
        if action == "workflow_proposal_get":
            if proposal["status"] in {
                "planner_drafting", "user_review", "planner_review", "accepted",
            }:
                proposal, draft, digest = _sync_current_draft(
                    proposals, authoring, proposal, user_id)
                proposal["current_draft_revision"] = int(draft["revision"])
                proposal["current_definition_digest"] = digest
                proposal["can_accept"] = (
                    proposal["status"] == "user_review"
                    and int(draft["revision"])
                    == proposal["planner_reviewed_revision"]
                    and digest == proposal["planner_reviewed_digest"]
                )
            return _reply({
                "proposal": proposal,
                "surface": current_workflow_proposal_surface(proposal),
            })
        if action == "workflow_proposal_inspect_run":
            run_id = str(body.get("run_id") or "")
            run = runs.get(run_id)
            if (
                run is None or run_id not in proposal["run_ids"]
                or run.get("proposal_id") != proposal_id
                or run.get("user_id") != user_id
                or run.get("conversation_id") != conversation_id
            ):
                return _reply({"error": "Flow run not found"}, "404")
            from core.ui_surface_store import publish_ui_surface
            surface = publish_ui_surface(
                workflow_proposal_run_surface(proposal, run))
            return _reply({
                "proposal": proposal, "run": _flow_run_view(run),
                "surface": surface,
            })

        if body.get("state_revision") is None:
            return _reply({"error": "state_revision is required"}, "400")
        expected = int(body["state_revision"])
        if action == "workflow_proposal_submit_to_planner":
            if expected != proposal["state_revision"]:
                raise ProposalConflict("proposal state revision changed")
            proposal, draft, digest = _sync_current_draft(
                proposals, authoring, proposal, user_id)
            from core.workflow_proposal_notifications import (
                queue_planner_event,
                resolve_planner_target,
            )
            planner = resolve_planner_target(
                conversation_id, proposal["created_by"], user_id)
            proposal = proposals.submit_to_planner(
                proposal_id,
                expected_state_revision=proposal["state_revision"],
                draft_revision=int(draft["revision"]), digest=digest,
                actor_id=user_id, comment=str(body.get("comment") or ""),
            )
            queue_planner_event(
                proposal, user_id=user_id, action="submitted_to_planner",
                comment=str(body.get("comment") or ""),
                planner_target=planner)
            _publish(conversation_id, "workflow_proposal_updated", proposal)
            return _reply({
                "proposal": proposal,
                "surface": current_workflow_proposal_surface(proposal),
            })
        if action == "workflow_proposal_accept":
            proposal, draft, digest = _sync_current_draft(
                proposals, authoring, proposal, user_id)
            if (
                int(draft["revision"])
                != proposal["planner_reviewed_revision"]
                or digest != proposal["planner_reviewed_digest"]
            ):
                return _reply({
                    "error": "draft_requires_planner_review",
                    "current_revision": int(draft["revision"]),
                    "planner_reviewed_revision":
                        proposal["planner_reviewed_revision"],
                    "proposal": proposal,
                }, "409")
            from core.workflow_proposal_notifications import (
                queue_planner_event,
                resolve_planner_target,
            )
            planner = resolve_planner_target(
                conversation_id, proposal["created_by"], user_id)
            proposal = proposals.accept(
                proposal_id, expected_state_revision=expected,
                actor_id=user_id)
            queue_planner_event(
                proposal, user_id=user_id, action="accepted",
                planner_target=planner)
            _publish(conversation_id, "workflow_proposal_updated", proposal)
            return _reply({
                "proposal": proposal,
                "surface": current_workflow_proposal_surface(proposal),
            })
        if action == "workflow_proposal_approve":
            if proposal["status"] != "accepted":
                raise ProposalConflict("proposal must be accepted before approval")
            draft = authoring.load_draft(proposal["draft_id"], user_id)
            digest = definition_digest(draft["definition"])
            if (
                int(draft["revision"]) != proposal["draft_revision"]
                or digest != proposal["definition_digest"]
            ):
                raise ProposalConflict(
                    "accepted draft revision or digest changed before approval")
            if len(draft["definition"].get("entries") or []) != 1:
                raise ValueError("durable_one_shot approval requires exactly one entry")
            published = authoring.publish(
                proposal["draft_id"], user_id, keep_draft=True)
            from core.paths import flow_version_file, parse_flow_fqn
            package, flow_name, version = parse_flow_fqn(published["fqn"])
            published_path = flow_version_file(
                package, flow_name, version, "conv", user_id, conversation_id)
            published_definition = json.loads(
                published_path.read_text(encoding="utf-8"))
            published_digest = definition_digest(published_definition)
            from core.resource_identity import ResourceRef
            flow_ref = ResourceRef(
                resource_type="flow", name=published["fqn"],
                scope="conversation", owner_id=user_id, version=version,
                content_digest=published_digest,
                source_id=f"repository:conversation:{published['fqn']}",
            )
            authorization_ref = _authorization_ref(
                user_id, conversation_id, flowfile,
                f"Approve workflow proposal {proposal_id} revision "
                f"{proposal['draft_revision']}")
            flow_run = runs.create(
                user_id=user_id, conversation_id=conversation_id,
                flow_ref=flow_ref.to_dict(),
                authorization_ref=authorization_ref,
                input_snapshot={
                    "content": str(body.get("input") or ""),
                    "attributes": dict(body.get("attributes") or {}),
                },
                parameters=dict(body.get("parameters") or {}),
                proposal_id=proposal_id,
            )
            proposal = proposals.approve(
                proposal_id, expected_state_revision=expected,
                actor_id=user_id, published_flow_ref=flow_ref.to_dict(),
                run_id=flow_run["run_id"],
            )
            try:
                _start_flow_run(
                    flow_run, published_definition, published_path,
                    published["fqn"],
                    str(draft["definition"]["entries"][0]))
            except Exception as exc:
                _fail_flow_run(runs, proposals, flow_run["run_id"], exc)
                raise
            proposal = proposals.mark_run_status(
                proposal_id, run_id=flow_run["run_id"], status="running")
            _publish(conversation_id, "workflow_proposal_updated", proposal)
            return _reply({
                "proposal": proposal,
                "run": runs.get(flow_run["run_id"]),
                "surface": current_workflow_proposal_surface(proposal),
            })
        if action == "workflow_proposal_replay":
            run_id = str(body.get("run_id") or "")
            original = runs.get(run_id)
            if (
                original is None or run_id not in proposal["run_ids"]
                or original.get("proposal_id") != proposal_id
                or original.get("user_id") != user_id
                or original.get("conversation_id") != conversation_id
            ):
                return _reply({"error": "Flow run not found"}, "404")
            authorization_ref = _authorization_ref(
                user_id, conversation_id, flowfile,
                f"Replay workflow proposal {proposal_id} run {run_id}")
            coordinator = FlowRunCoordinator(runs)
            replay = coordinator.replay(
                run_id, authorization_ref=authorization_ref)
            proposal = proposals.start_replay(
                proposal_id, expected_state_revision=expected,
                run_id=replay["run_id"], actor_id=user_id)
            from core.paths import flow_version_file, parse_flow_fqn
            flow_fqn = str(replay["flow_ref"]["name"])
            package, flow_name, version = parse_flow_fqn(flow_fqn)
            published_path = flow_version_file(
                package, flow_name, version, "conv", user_id, conversation_id)
            published_definition = json.loads(
                published_path.read_text(encoding="utf-8"))
            if definition_digest(published_definition) != replay["flow_ref"][
                    "content_digest"]:
                raise ValueError("published flow digest changed before replay")
            entries = published_definition.get("entries") or []
            if len(entries) != 1:
                raise ValueError("durable_one_shot replay requires exactly one entry")
            try:
                _start_flow_run(
                    replay, published_definition, published_path, flow_fqn,
                    str(entries[0]))
            except Exception as exc:
                _fail_flow_run(runs, proposals, replay["run_id"], exc)
                raise
            _publish(conversation_id, "workflow_proposal_updated", proposal)
            return _reply({
                "proposal": proposal, "run": runs.get(replay["run_id"]),
                "surface": current_workflow_proposal_surface(proposal),
            })
        from core.workflow_proposal_notifications import (
            queue_planner_event,
            resolve_planner_target,
        )
        planner = resolve_planner_target(
            conversation_id, proposal["created_by"], user_id)
        if proposal["status"] == "running" and proposal["run_ids"]:
            if expected != proposal["state_revision"]:
                raise ProposalConflict("proposal state revision changed")
            FlowRunCoordinator(runs).cancel(
                str(proposal["run_ids"][-1]), reason="cancelled by user")
            FlowRunCoordinator(runs).deliver_pending_events(proposals)
            proposal = proposals.get(
                proposal_id, user_id=user_id,
                conversation_id=conversation_id)
        else:
            proposal = proposals.cancel(
                proposal_id, expected_state_revision=expected,
                actor_type="user", actor_id=user_id,
                comment=str(body.get("comment") or ""),
            )
        queue_planner_event(
            proposal, user_id=user_id, action="cancelled",
            comment=str(body.get("comment") or ""),
            planner_target=planner)
        _publish(conversation_id, "workflow_proposal_updated", proposal)
        return _reply({
            "proposal": proposal,
            "surface": current_workflow_proposal_surface(proposal),
        })
    except ProposalConflict as exc:
        current = proposals.get(
            str(body.get("proposal_id") or ""),
            user_id=user_id, conversation_id=conversation_id)
        return _reply({
            "error": "workflow_proposal_conflict",
            "message": str(exc),
            "proposal": current,
        }, "409")
    except DraftNotFound:
        return _reply({"error": "Draft not found"}, "404")
    except KeyError as exc:
        return _reply({"error": str(exc.args[0] if exc.args else exc)}, "404")
    except ValueError as exc:
        return _reply({"error": str(exc)}, "400")
    except Exception as exc:
        logger.exception("workflow proposal action '%s' failed", action)
        return _reply({"error": str(exc)}, "500")


__all__ = ["_handle_workflow_proposals"]

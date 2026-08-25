"""Active legacy PlanStore checkpoint transfer to canonical workflow stores."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from core import FlowFile
from core.confirmation_store import ConfirmationStore
from core.flow_run_store import FlowRunStore
from core.workflow_proposal_store import WorkflowProposalStore


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        raise ValueError(f"{name} must be a non-empty object")
    return value


def _text(value: Any, name: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{name} is required")
    return result


def _timestamp(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a numeric timestamp")
    return float(value)


def _utc(value: float) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


class LegacyActiveCheckpointImporter:
    """Transfer one exact verification wake as an idempotent saga."""

    def __init__(
        self,
        *,
        flow_runs: FlowRunStore,
        proposals: WorkflowProposalStore,
        waits: ConfirmationStore,
        scheduler: Any,
    ) -> None:
        self.flow_runs = flow_runs
        self.proposals = proposals
        self.waits = waits
        self.scheduler = scheduler

    def import_checkpoint(
        self,
        conversion: dict[str, Any],
        flow_ref: dict[str, Any],
        *,
        authorization_ref: dict[str, Any],
        cancel_legacy: bool = True,
    ) -> dict[str, Any]:
        """Persist the canonical continuation before releasing its legacy owner."""

        conversion = _object(conversion, "conversion")
        flow_ref = _object(flow_ref, "flow_ref")
        authorization_ref = _object(authorization_ref, "authorization_ref")
        if conversion.get("mode") != "resume":
            raise ValueError("active checkpoint import requires resume mode")

        imported = _object(conversion.get("imported_plan"), "imported_plan")
        proposal_spec = _object(conversion.get("proposal"), "proposal")
        run_spec = _object(conversion.get("run"), "run")
        checkpoint = _object(run_spec.get("checkpoint"), "run.checkpoint")
        schedule = _object(checkpoint.get("schedule"), "run.checkpoint.schedule")
        if run_spec.get("status") != "waiting":
            raise ValueError("active imported run must be waiting")
        if proposal_spec.get("status") != "running":
            raise ValueError("active imported proposal must be running")
        if checkpoint.get("kind") != "legacy_plan_verification":
            raise ValueError("unsupported active checkpoint kind")

        source_digest = _text(conversion.get("source_digest"), "source_digest")
        if (
            len(source_digest) != 64
            or any(value not in "0123456789abcdef" for value in source_digest)
        ):
            raise ValueError("source_digest must be a lowercase SHA-256")
        source_id = _text(imported.get("source_id"), "source_id")
        source_path = _text(imported.get("source_path"), "source_path")
        user_id = _text(imported.get("user_id"), "user_id")
        conversation_id = _text(
            imported.get("conversation_id"), "conversation_id")
        run_id = _text(run_spec.get("run_id"), "run.run_id")
        proposal_id = _text(
            proposal_spec.get("proposal_id"), "proposal.proposal_id")
        created_at = _timestamp(imported.get("created_at"), "created_at")

        step = checkpoint.get("step")
        if isinstance(step, bool) or not isinstance(step, int) or step < 1:
            raise ValueError("checkpoint step must be an integer >= 1")
        plan_id = _text(checkpoint.get("plan_id"), "checkpoint.plan_id")
        executor = _text(checkpoint.get("executor"), "checkpoint.executor")
        verifier = _text(checkpoint.get("verifier"), "checkpoint.verifier")
        if plan_id != source_id:
            raise ValueError("checkpoint plan_id does not match source_id")
        schedule_key = _text(schedule.get("key"), "schedule.key")
        expected_key = (
            f"{conversation_id}::plan::{plan_id}::verify{step}::{verifier}")
        if (
            schedule_key != expected_key
            or schedule.get("conversation_id") != conversation_id
            or schedule.get("user_id") != user_id
            or schedule.get("reason")
            != f"[plan_verify:{plan_id}:{step}:{executor}] ({verifier})"
        ):
            raise ValueError("legacy verification schedule does not match checkpoint")
        deadline_at = _timestamp(schedule.get("recheck_at"), "schedule.recheck_at")
        timer_created_at = _timestamp(
            schedule.get("created_at"), "schedule.created_at")
        if deadline_at < timer_created_at:
            raise ValueError("schedule deadline precedes its creation")

        metadata = {
            "schema_version": 1,
            "source_type": "legacy_plan",
            "source_id": source_id,
            "source_digest": source_digest,
            "source_path": source_path,
        }
        resume_task_id = f"step_{step}_verify"
        process_id = f"ff_legacy_{source_digest[:20]}"
        wait_id = f"timer_legacy_{source_digest[:20]}"
        payload = json.dumps(
            {
                "schema_version": 1,
                "source_type": "legacy_plan",
                "source_id": source_id,
                "steps": conversion.get("steps") or [],
                "checkpoint": checkpoint,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        attributes = {
            "flow.run.id": run_id,
            "migration.source_type": "legacy_plan",
            "migration.source_id": source_id,
        }
        input_snapshot = {
            "content": payload,
            "attributes": attributes,
        }

        run_existed = self.flow_runs.get(run_id) is not None
        proposal_existed = self.proposals.get(proposal_id) is not None
        wait_existed = any(
            value.get("wait_id") == wait_id
            for value in self.waits.list_waits(status="all")
        )
        run = None
        proposal = None
        wait = None
        try:
            run = self.flow_runs.import_active(
                run_id=run_id,
                user_id=user_id,
                conversation_id=conversation_id,
                flow_ref=flow_ref,
                proposal_id=proposal_id,
                status="waiting",
                checkpoint=checkpoint,
                import_metadata=metadata,
                authorization_ref=authorization_ref,
                input_snapshot=input_snapshot,
                parameters={},
                created_at=created_at,
            )
            proposal = self.proposals.import_active(
                proposal_id=proposal_id,
                user_id=user_id,
                conversation_id=conversation_id,
                title=_text(imported.get("title"), "title"),
                summary="Imported active PlanStore checkpoint.",
                draft_id=f"d_legacy_{source_digest[:20]}",
                digest=_text(flow_ref.get("content_digest"), "flow_ref.content_digest"),
                created_by="legacy-plan-migration",
                published_flow_ref=flow_ref,
                run_id=run_id,
                status="running",
                import_metadata=metadata,
                created_at=_utc(created_at),
            )
            flowfile = FlowFile(
                content=payload.encode("utf-8"),
                attributes=attributes,
                process_id=process_id,
                created_at=datetime.fromtimestamp(
                    timer_created_at, tz=timezone.utc),
            )
            wait = self.waits.import_timer(
                wait_id=wait_id,
                instance_id=run["deployment_instance_id"],
                task_id=resume_task_id,
                flowfile=flowfile,
                deadline_at=deadline_at,
                created_at=timer_created_at,
                import_metadata=metadata,
            )
            if cancel_legacy:
                self.scheduler.cancel(schedule_key)
        except Exception:
            if not wait_existed:
                self.waits.delete_imported_wait(
                    wait_id, import_metadata=metadata)
            if not proposal_existed:
                self.proposals.delete_imported(
                    proposal_id, import_metadata=metadata)
            if not run_existed:
                self.flow_runs.delete_imported(
                    run_id, import_metadata=metadata)
            raise

        return {
            "run": run,
            "proposal": proposal,
            "wait": wait,
            "flowfile_process_id": process_id,
            "legacy_schedule": schedule,
            "artifacts": [
                {"kind": "flow_run", "id": run_id},
                {"kind": "workflow_proposal", "id": proposal_id},
                {"kind": "durable_wait", "id": wait_id},
            ],
        }


__all__ = ["LegacyActiveCheckpointImporter"]

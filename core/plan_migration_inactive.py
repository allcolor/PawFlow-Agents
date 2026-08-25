"""Inactive legacy PlanStore import into canonical workflow stores."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

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


class LegacyInactivePlanImporter:
    """Import non-running legacy states without starting live execution."""

    def __init__(
        self,
        *,
        flow_runs: FlowRunStore,
        proposals: WorkflowProposalStore,
    ) -> None:
        self.flow_runs = flow_runs
        self.proposals = proposals

    def import_state(
        self,
        conversion: dict[str, Any],
        flow_ref: dict[str, Any],
        *,
        authorization_ref: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        conversion = _object(conversion, "conversion")
        flow_ref = _object(flow_ref, "flow_ref")
        mode = str(conversion.get("mode") or "")
        if mode not in {"review", "resume"}:
            raise ValueError("inactive import requires review or resume mode")

        imported = _object(conversion.get("imported_plan"), "imported_plan")
        proposal_spec = _object(conversion.get("proposal"), "proposal")
        run_spec = conversion.get("run")
        expected_status = "user_review" if mode == "review" else "accepted"
        if proposal_spec.get("status") != expected_status:
            raise ValueError("proposal status does not match inactive mode")
        if mode == "review":
            if run_spec is not None:
                raise ValueError("review import must not contain a run")
        else:
            run_spec = _object(run_spec, "run")
            if (
                run_spec.get("status") != "created"
                or run_spec.get("checkpoint") is not None
                or run_spec.get("terminal") is not None
            ):
                raise ValueError("inactive resume import requires a fresh created run")
            authorization_ref = _object(
                authorization_ref, "authorization_ref")

        source_digest = _text(conversion.get("source_digest"), "source_digest")
        if (
            len(source_digest) != 64
            or any(value not in "0123456789abcdef" for value in source_digest)
        ):
            raise ValueError("source_digest must be a lowercase SHA-256")
        source_id = _text(imported.get("source_id"), "source_id")
        metadata = {
            "schema_version": 1,
            "source_type": "legacy_plan",
            "source_id": source_id,
            "source_digest": source_digest,
            "source_path": _text(imported.get("source_path"), "source_path"),
        }
        user_id = _text(imported.get("user_id"), "user_id")
        conversation_id = _text(
            imported.get("conversation_id"), "conversation_id")
        proposal_id = _text(
            proposal_spec.get("proposal_id"), "proposal.proposal_id")
        created_at = _timestamp(imported.get("created_at"), "created_at")
        created_at_iso = datetime.fromtimestamp(
            created_at, tz=timezone.utc).isoformat()
        payload = json.dumps(
            {
                "schema_version": 1,
                "source_type": "legacy_plan",
                "source_id": source_id,
                "steps": conversion.get("steps") or [],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        input_snapshot = {
            "content": payload,
            "attributes": {
                "migration.source_type": "legacy_plan",
                "migration.source_id": source_id,
            },
        }

        run_id = (
            _text(run_spec.get("run_id"), "run.run_id")
            if isinstance(run_spec, dict) else "")
        run_existed = bool(run_id and self.flow_runs.get(run_id) is not None)
        proposal_existed = self.proposals.get(proposal_id) is not None
        run = None
        try:
            if run_id:
                input_snapshot["attributes"]["flow.run.id"] = run_id
                run = self.flow_runs.import_pending(
                    run_id=run_id,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    flow_ref=flow_ref,
                    proposal_id=proposal_id,
                    import_metadata=metadata,
                    authorization_ref=authorization_ref,
                    input_snapshot=input_snapshot,
                    parameters={},
                    created_at=created_at,
                )
            proposal = self.proposals.import_inactive(
                proposal_id=proposal_id,
                user_id=user_id,
                conversation_id=conversation_id,
                title=_text(imported.get("title"), "title"),
                summary="Imported inactive PlanStore state.",
                draft_id=f"d_legacy_{source_digest[:20]}",
                digest=_text(flow_ref.get("content_digest"), "flow_ref.content_digest"),
                created_by="legacy-plan-migration",
                published_flow_ref=flow_ref,
                run_id=run_id,
                status=expected_status,
                import_metadata=metadata,
                created_at=created_at_iso,
            )
        except Exception:
            if not proposal_existed:
                self.proposals.delete_imported(
                    proposal_id, import_metadata=metadata)
            if run_id and not run_existed:
                self.flow_runs.delete_imported(
                    run_id, import_metadata=metadata)
            raise

        artifacts = [{"kind": "workflow_proposal", "id": proposal_id}]
        if run_id:
            artifacts.insert(0, {"kind": "flow_run", "id": run_id})
        return {
            "run": run,
            "proposal": proposal,
            "artifacts": artifacts,
        }


__all__ = ["LegacyInactivePlanImporter"]

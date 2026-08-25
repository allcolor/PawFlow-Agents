"""Archived terminal PlanStore import into canonical workflow stores."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.flow_run_store import FlowRunStore
from core.workflow_proposal_store import WorkflowProposalStore


def _required_object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        raise ValueError(f"{name} must be a non-empty object")
    return value


def _timestamp(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a numeric timestamp")
    return float(value)


def _utc(value: float) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


class LegacyTerminalPlanImporter:
    """Import terminal legacy history as an idempotent compensating saga."""

    def __init__(
        self,
        *,
        flow_runs: FlowRunStore,
        proposals: WorkflowProposalStore,
    ) -> None:
        self.flow_runs = flow_runs
        self.proposals = proposals

    def import_history(
        self,
        conversion: dict[str, Any],
        flow_ref: dict[str, Any],
    ) -> dict[str, Any]:
        """Import one archive conversion without emitting live runtime events."""

        conversion = _required_object(conversion, "conversion")
        flow_ref = _required_object(flow_ref, "flow_ref")
        if conversion.get("mode") != "archive":
            raise ValueError("terminal history import requires archive mode")

        imported_plan = _required_object(
            conversion.get("imported_plan"), "imported_plan")
        proposal_spec = _required_object(
            conversion.get("proposal"), "proposal")
        run_spec = _required_object(conversion.get("run"), "run")
        terminal = _required_object(run_spec.get("terminal"), "run.terminal")
        source_digest = str(conversion.get("source_digest") or "").strip()
        if not source_digest:
            raise ValueError("source_digest is required")
        status = str(run_spec.get("status") or "").strip()
        if proposal_spec.get("status") != status:
            raise ValueError("proposal and run terminal statuses must match")

        created_at = _timestamp(imported_plan.get("created_at"), "created_at")
        terminal_at = _timestamp(imported_plan.get("updated_at"), "updated_at")
        metadata = {
            "schema_version": 1,
            "source_type": "legacy_plan",
            "source_id": str(imported_plan.get("source_id") or "").strip(),
            "source_digest": source_digest,
            "source_path": str(imported_plan.get("source_path") or "").strip(),
        }
        if not metadata["source_id"] or not metadata["source_path"]:
            raise ValueError("legacy source identity is required")

        run_id = str(run_spec.get("run_id") or "").strip()
        proposal_id = str(proposal_spec.get("proposal_id") or "").strip()
        previous_run = self.flow_runs.get(run_id) if run_id else None
        run = self.flow_runs.import_terminal(
            run_id=run_id,
            user_id=str(imported_plan.get("user_id") or ""),
            conversation_id=str(imported_plan.get("conversation_id") or ""),
            flow_ref=flow_ref,
            proposal_id=proposal_id,
            status=status,
            terminal=terminal,
            import_metadata=metadata,
            created_at=created_at,
            terminal_at=terminal_at,
        )
        try:
            proposal = self.proposals.import_terminal(
                proposal_id=proposal_id,
                user_id=str(imported_plan.get("user_id") or ""),
                conversation_id=str(imported_plan.get("conversation_id") or ""),
                title=str(imported_plan.get("title") or ""),
                summary="Imported terminal PlanStore history.",
                draft_id=f"d_legacy_{source_digest[:20]}",
                digest=str(flow_ref.get("content_digest") or ""),
                created_by="legacy-plan-migration",
                published_flow_ref=flow_ref,
                run_id=run_id,
                status=status,
                import_metadata=metadata,
                created_at=_utc(created_at),
                terminal_at=_utc(terminal_at),
            )
        except Exception:
            if previous_run is None:
                self.flow_runs.delete_imported(
                    run_id, import_metadata=metadata)
            raise

        return {
            "run": run,
            "proposal": proposal,
            "artifacts": [
                {"kind": "flow_run", "id": run_id},
                {"kind": "workflow_proposal", "id": proposal_id},
            ],
        }


__all__ = ["LegacyTerminalPlanImporter"]

"""Atomic activation saga for a prepared legacy PlanStore migration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from core.confirmation_store import ConfirmationStore
from core.flow_run_store import FlowRunStore
from core.plan_migration import build_legacy_conversion_plan
from core.plan_migration_active import LegacyActiveCheckpointImporter
from core.plan_migration_flow import publish_legacy_flow
from core.plan_migration_import import LegacyTerminalPlanImporter
from core.plan_migration_inactive import LegacyInactivePlanImporter
from core.plan_migration_manifest import PlanMigrationManifestStore
from core.workflow_proposal_store import WorkflowProposalStore


class LegacyPlanMigrationActivator:
    """Publish and import every prepared record before one manifest commit."""

    def __init__(
        self,
        *,
        manifest_store: PlanMigrationManifestStore,
        authoring: Any,
        flow_runs: FlowRunStore,
        proposals: WorkflowProposalStore,
        waits: ConfirmationStore,
        scheduler: Any,
        publisher: Callable[..., dict[str, Any]] = publish_legacy_flow,
    ) -> None:
        self.manifest_store = manifest_store
        self.authoring = authoring
        self.flow_runs = flow_runs
        self.proposals = proposals
        self.waits = waits
        self.scheduler = scheduler
        self.publisher = publisher

    @staticmethod
    def _store_artifact(
        kind: str,
        identifier: str,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "kind": kind,
            "id": identifier,
            "import_metadata": dict(value["import_metadata"]),
        }

    def _flow_existed(
        self,
        conversion: dict[str, Any],
    ) -> bool:
        imported = conversion["imported_plan"]
        versions = self.authoring.versions(
            conversion["flow"]["fqn"],
            "conv",
            imported["user_id"],
            imported["conversation_id"],
        )
        return "1.0.0" in (versions.get("versions") or [])

    def _restore_schedule(self, schedule: dict[str, Any]) -> None:
        key = str(schedule["key"])
        getter = getattr(self.scheduler, "get", None)
        existing = getter(key) if callable(getter) else None
        comparable = (
            "conversation_id", "key", "recheck_at", "user_id", "reason")
        if existing is not None:
            if all(existing.get(name) == schedule.get(name) for name in comparable):
                return
            raise ValueError("legacy schedule key was reused during migration")
        self.scheduler.schedule(
            str(schedule["conversation_id"]),
            float(schedule["recheck_at"]),
            user_id=str(schedule.get("user_id") or ""),
            reason=str(schedule.get("reason") or ""),
            key=key,
        )

    def remove_artifact(self, artifact: dict[str, Any]) -> None:
        """Remove or restore one manifest artifact with provenance fencing."""

        kind = str(artifact.get("kind") or "")
        identifier = str(artifact.get("id") or "")
        if kind == "legacy_schedule":
            schedule = artifact.get("schedule")
            if not isinstance(schedule, dict) or not schedule:
                raise ValueError("legacy schedule artifact is incomplete")
            self._restore_schedule(schedule)
            return
        metadata = artifact.get("import_metadata")
        if kind == "durable_wait":
            self.waits.delete_imported_wait(
                identifier, import_metadata=metadata)
            return
        if kind == "workflow_proposal":
            self.proposals.delete_imported(
                identifier, import_metadata=metadata)
            return
        if kind == "flow_run":
            self.flow_runs.delete_imported(
                identifier, import_metadata=metadata)
            return
        if kind != "flow":
            raise ValueError(f"unknown plan migration artifact kind: {kind}")

        user_id = str(artifact.get("user_id") or "")
        conversation_id = str(artifact.get("conversation_id") or "")
        definition = self.authoring.load(
            identifier, "conv", user_id, conversation_id)
        migration = definition.get("migration")
        if (
            not isinstance(migration, dict)
            or migration.get("source_digest") != artifact.get("source_digest")
        ):
            raise ValueError("published flow provenance does not match migration")
        qualified = identifier.rsplit(":", 1)[0]
        if not self.authoring.repo.delete(
            "flow", qualified, "conv",
            user_id=user_id, conv_id=conversation_id,
        ):
            raise KeyError(identifier)

    def activate(
        self,
        migration_id: str,
        *,
        authorization_ref: dict[str, Any],
    ) -> dict[str, Any]:
        """Import the prepared report and atomically mark it active."""

        manifest = self.manifest_store.validate_activation(migration_id)
        if manifest["state"] == "active":
            result = dict(manifest)
            result["idempotent"] = True
            return result
        report = manifest["report"]
        source_root = Path(manifest["source_root"])
        terminal_importer = LegacyTerminalPlanImporter(
            flow_runs=self.flow_runs, proposals=self.proposals)
        inactive_importer = LegacyInactivePlanImporter(
            flow_runs=self.flow_runs, proposals=self.proposals)
        active_importer = LegacyActiveCheckpointImporter(
            flow_runs=self.flow_runs,
            proposals=self.proposals,
            waits=self.waits,
            scheduler=self.scheduler,
        )
        artifacts: list[dict[str, Any]] = []
        owned: list[dict[str, Any]] = []
        schedules: list[dict[str, Any]] = []
        cancelled: list[dict[str, Any]] = []
        try:
            for record in report.get("records") or []:
                source_path = source_root / str(record["source_path"])
                plan = json.loads(source_path.read_text(encoding="utf-8"))
                conversion = build_legacy_conversion_plan(record, plan)
                imported_plan = conversion["imported_plan"]
                flow_existed = self._flow_existed(conversion)
                flow_ref = self.publisher(conversion, authoring=self.authoring)
                flow_artifact = {
                    "kind": "flow",
                    "id": conversion["flow"]["fqn"],
                    "user_id": imported_plan["user_id"],
                    "conversation_id": imported_plan["conversation_id"],
                    "source_digest": conversion["source_digest"],
                }
                artifacts.append(flow_artifact)
                if not flow_existed:
                    owned.append(flow_artifact)

                run_spec = conversion.get("run") or {}
                run_id = str(run_spec.get("run_id") or "")
                proposal_id = str(
                    conversion["proposal"].get("proposal_id") or "")
                run_existed = bool(
                    run_id and self.flow_runs.get(run_id) is not None)
                proposal_existed = (
                    self.proposals.get(proposal_id) is not None)
                wait_id = f"timer_legacy_{conversion['source_digest'][:20]}"
                wait_existed = any(
                    value.get("wait_id") == wait_id
                    for value in self.waits.list_waits(status="all"))

                if conversion["mode"] == "archive":
                    imported = terminal_importer.import_history(
                        conversion, flow_ref)
                elif (
                    conversion["mode"] == "resume"
                    and run_spec.get("status") == "waiting"
                ):
                    imported = active_importer.import_checkpoint(
                        conversion,
                        flow_ref,
                        authorization_ref=authorization_ref,
                        cancel_legacy=False,
                    )
                    schedules.append(dict(imported["legacy_schedule"]))
                else:
                    imported = inactive_importer.import_state(
                        conversion,
                        flow_ref,
                        authorization_ref=authorization_ref,
                    )

                run = imported.get("run")
                if isinstance(run, dict):
                    artifact = self._store_artifact(
                        "flow_run", run["run_id"], run)
                    artifacts.append(artifact)
                    if not run_existed:
                        owned.append(artifact)
                proposal = imported["proposal"]
                artifact = self._store_artifact(
                    "workflow_proposal", proposal["proposal_id"], proposal)
                artifacts.append(artifact)
                if not proposal_existed:
                    owned.append(artifact)
                wait = imported.get("wait")
                if isinstance(wait, dict):
                    artifact = self._store_artifact(
                        "durable_wait", wait["wait_id"], wait)
                    artifacts.append(artifact)
                    if not wait_existed:
                        owned.append(artifact)

            for schedule in schedules:
                if self.scheduler.cancel(str(schedule["key"])):
                    cancelled.append(schedule)
                artifacts.append({
                    "kind": "legacy_schedule",
                    "id": str(schedule["key"]),
                    "schedule": schedule,
                })
            return self.manifest_store.activate(
                migration_id, artifacts=artifacts)
        except Exception:
            for schedule in reversed(cancelled):
                self._restore_schedule(schedule)
            for artifact in reversed(owned):
                self.remove_artifact(artifact)
            raise


__all__ = ["LegacyPlanMigrationActivator"]

"""Production entry points for the WP9 PlanStore migration lifecycle."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import core.paths as _paths
from core.plan_migration import (
    LegacyPlanMigrationPreflight,
    resolve_legacy_agent_adapter,
    resolve_legacy_plan_checkpoint,
)
from core.plan_migration_manifest import PlanMigrationManifestStore


def _manifest_store(
    root: Path | None,
) -> PlanMigrationManifestStore:
    return PlanMigrationManifestStore(
        Path(root or (_paths.RUNTIME_DIR / "plan_migrations")))


def run_legacy_plan_preflight(
    *,
    plans_dir: Path | None = None,
    resource_store=None,
    scheduler=None,
    workflow_resolver=None,
) -> dict[str, Any]:
    """Inventory live legacy records without writing migration state."""

    if resource_store is None:
        from core.resource_store import ResourceStore

        resource_store = ResourceStore.instance()
    if scheduler is None:
        from core.poll_scheduler import PollScheduler

        scheduler = PollScheduler.instance()
    schedules = scheduler.list_all()
    if not isinstance(schedules, list):
        raise TypeError("PollScheduler.list_all() must return an array")

    def resolve_agent(
        name: str,
        user_id: str,
        conversation_id: str,
    ) -> dict[str, Any] | None:
        return resolve_legacy_agent_adapter(
            name,
            user_id,
            conversation_id,
            resource_store=resource_store,
            workflow_resolver=workflow_resolver,
        )

    def resolve_checkpoint(
        plan: dict[str, Any],
        _user_id: str,
        conversation_id: str,
    ) -> dict[str, Any] | None:
        return resolve_legacy_plan_checkpoint(
            plan, schedules, conversation_id=conversation_id)

    return LegacyPlanMigrationPreflight(
        plans_dir=Path(plans_dir or _paths.PLANS_DIR),
        resolve_agent_adapter=resolve_agent,
        resolve_active_checkpoint=resolve_checkpoint,
    ).run()


def prepare_legacy_plan_migration(
    *,
    manifest_root: Path | None = None,
    plans_dir: Path | None = None,
    resource_store=None,
    scheduler=None,
    workflow_resolver=None,
) -> dict[str, Any]:
    """Run preflight and atomically back up sources; never activate cutover."""

    report = run_legacy_plan_preflight(
        plans_dir=plans_dir,
        resource_store=resource_store,
        scheduler=scheduler,
        workflow_resolver=workflow_resolver,
    )
    return _manifest_store(manifest_root).prepare(report)


def mark_active_plan_migration_write(
    *, manifest_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Fence rollback before a canonical proposal or flow-run mutation."""

    return _manifest_store(manifest_root).mark_active_first_write()


def activate_legacy_plan_migration(
    migration_id: str,
    *,
    authorization_ref: dict[str, Any],
    manifest_root: Path | None = None,
    manifest_store=None,
    authoring=None,
    flow_runs=None,
    proposals=None,
    waits=None,
    scheduler=None,
) -> dict[str, Any]:
    """Activate one prepared migration through the compensating batch saga."""

    from core.confirmation_store import ConfirmationStore
    from core.flow_authoring import FlowAuthoringService
    from core.flow_run_store import FlowRunStore
    from core.plan_migration_activation import LegacyPlanMigrationActivator
    from core.poll_scheduler import PollScheduler
    from core.workflow_proposal_store import WorkflowProposalStore

    return LegacyPlanMigrationActivator(
        manifest_store=manifest_store or _manifest_store(manifest_root),
        authoring=authoring or FlowAuthoringService.instance(),
        flow_runs=flow_runs or FlowRunStore.instance(),
        proposals=proposals or WorkflowProposalStore.instance(),
        waits=waits or ConfirmationStore.instance(),
        scheduler=scheduler or PollScheduler.instance(),
    ).activate(migration_id, authorization_ref=authorization_ref)


def rollback_legacy_plan_migration(
    migration_id: str,
    *,
    manifest_root: Path | None = None,
    manifest_store=None,
    authoring=None,
    flow_runs=None,
    proposals=None,
    waits=None,
    scheduler=None,
) -> dict[str, Any]:
    """Rollback an active migration before its first canonical write."""

    from core.confirmation_store import ConfirmationStore
    from core.flow_authoring import FlowAuthoringService
    from core.flow_run_store import FlowRunStore
    from core.plan_migration_activation import LegacyPlanMigrationActivator
    from core.poll_scheduler import PollScheduler
    from core.workflow_proposal_store import WorkflowProposalStore

    store = manifest_store or _manifest_store(manifest_root)
    activator = LegacyPlanMigrationActivator(
        manifest_store=store,
        authoring=authoring or FlowAuthoringService.instance(),
        flow_runs=flow_runs or FlowRunStore.instance(),
        proposals=proposals or WorkflowProposalStore.instance(),
        waits=waits or ConfirmationStore.instance(),
        scheduler=scheduler or PollScheduler.instance(),
    )
    return store.rollback(
        migration_id, remove_artifact=activator.remove_artifact)


__all__ = [
    "activate_legacy_plan_migration",
    "mark_active_plan_migration_write",
    "prepare_legacy_plan_migration",
    "rollback_legacy_plan_migration",
    "run_legacy_plan_preflight",
]

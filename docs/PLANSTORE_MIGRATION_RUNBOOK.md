# PlanStore to Declarative Workflows Migration Runbook

This runbook covers the one-shot migration from legacy per-record PlanStore JSON
to immutable Flow definitions, WorkflowProposal records, and durable FlowRun
records. The migration is opt-in, server-owned, and fail-closed.

## Safety invariants

- Keep a filesystem/database backup outside the PawFlow runtime before starting.
- Feature flags are process configuration. Request bodies cannot enable them.
- Legacy and canonical writers must never be active together.
- Every preflight record must convert or appear as an explicit blocker.
- Migration imports carry exact source provenance and emit no live terminal event.
- Rollback is available only after activation and before the first canonical live
  proposal or run mutation.
- The first live WorkflowProposal or FlowRun mutation automatically writes
  `first_write_at` to every active migration manifest before changing canonical
  state. After that fence, rollback is intentionally rejected.
- Do not delete PlanStore files or code until the compatibility release and
  production canary evidence are complete.

## Permanent feature state

Multi-view layouts, declarative workflows, durable FlowRuns, canonical workflow
proposals, and migration activation are permanent capabilities. They do not use
server rollout flags. The migration manifest, exact source digests, authenticated
operator authority, and first-write fence remain the safety boundary.

## 1. Establish the baseline

1. Back up the PlanStore directory and PawFlow runtime databases.
2. Confirm the canonical proposal APIs and clients are available.
3. Run the full CI, security, Web template/SSE, PawCode, and VS Code gates.
4. Record the legacy record count and state distribution.
5. Confirm no migration manifest is already active under
   `data/runtime/plan_migrations`.

## 2. Preflight and prepare

The production entry points live in `core.plan_migration_runtime`:

```python
from core.plan_migration_runtime import (
    prepare_legacy_plan_migration,
    run_legacy_plan_preflight,
)

report = run_legacy_plan_preflight()
prepared = prepare_legacy_plan_migration()
```

`run_legacy_plan_preflight()` does not write migration state.
`prepare_legacy_plan_migration()` reruns preflight, verifies every source digest,
copies exact source bytes into the manifest backup, and writes a deterministic
`pm_<digest>` manifest.

Do not continue unless:

- `activation_allowed` is true;
- `blockers` is empty;
- every record has the intended classification and exact agent adapter;
- only a provable `waiting_verification` checkpoint is classified as resumable;
- counts match the baseline.

Fix the source or adapter problem and rerun preparation. Never edit a prepared
manifest by hand.

## 3. Stage and validate the canonical runtime

No feature flag or restart is required. Before migration activation, keep the
legacy source immutable while validating flow publication, durable
interactions, recovery, exact ResourceRef resolution, authorization snapshots,
and one non-Web client.

## 4. Activate once

Call:

```python
from core.plan_migration_runtime import activate_legacy_plan_migration

activated = activate_legacy_plan_migration(
    prepared["migration_id"],
    authorization_ref=authenticated_operator_authorization_ref,
)
```

The authorization reference must come from the authenticated operator context;
do not synthesize or reuse a stale reference. Activation revalidates the source,
publishes immutable converted flows, imports terminal/inactive/active records
through compensating sagas, and commits the manifest only after all artifacts
exist. Exact retries are idempotent.

After activation, the canonical proposal path is exclusive:

- all 18 legacy PlanStore actions return HTTP 404 without opening PlanStore;
- legacy Web panel, menu, scripts, listeners, and OpenSpace projections are absent;
- Web `/plan` reloads UiSurfaces and asks the planner to `propose_workflow`;
- PawCode and VS Code probe `workflow_proposal_list`, use canonical proposal
  actions on success, and use legacy behavior only for an explicit disabled/404
  response. Other errors propagate.

## 5. Canary before the first live write

Before asking a canary user to mutate a proposal, verify:

- imported proposal/run counts and terminal states match preflight;
- imported terminal history produced no live outbox event;
- waiting records have their exact durable timer/checkpoint;
- the old PlanStore writer and surfaces are unreachable;
- proposal list/get and UiSurface hydration work in Web and one non-Web client;
- restart recovery and authorization checks pass;
- logs and projections contain no prompts, secrets, source bodies, or unauthorized
  targets.

At this point rollback is still available.

## 6. Roll back only before the fence

If activation validation fails before any canonical live mutation, call:

```python
from core.plan_migration_runtime import rollback_legacy_plan_migration

rolled_back = rollback_legacy_plan_migration(prepared["migration_id"])
```

Rollback removes only provenance-matching imported artifacts and restores the
exact backed-up PlanStore bytes. No feature flag or restart is involved.

If the manifest has `first_write_at`, stop. Rollback is prohibited because
canonical state now has user-visible writes that cannot be safely merged back
into PlanStore. Diagnose and repair forward.

## 7. Complete the rollout

After representative create, edit, planner review, accept, approve, run,
interaction, recovery, cancellation, terminal, inspect, and replay paths pass:

1. run the full test, syntax, Ruff CI-error, Bandit, JavaScript, and VS Code build
   gates;
2. retain the migration manifest and backups according to the operator retention
   policy;
3. keep legacy reads/code until the declared compatibility release;
4. perform destructive cleanup only in a separately reviewed change;
5. record the canary evidence, migration ID, and `first_write_at`.

See [Declarative Workflows Implementation Plan](DECLARATIVE_WORKFLOWS_IMPLEMENTATION_PLAN.md)
for design rationale and [Security Model](security_model.md) for trust boundaries.

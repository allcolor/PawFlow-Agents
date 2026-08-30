# Post-Audit Feature Implementation Roadmap

Status: planned; Workstream 1 approved for immediate implementation
Date: 2026-08-30
Owner: PawFlow core and first-party packages

## 1. Purpose

This roadmap orders the eight implementation plans currently scheduled: the six
produced from the live PawFlow audit and the review of seven external projects,
plus the two relay/Desktop workstreams carved out of the multi-workspace relay
plan. Each linked document remains the authoritative detailed plan for its
feature.

The order favors immediate user productivity, then correctness of external
effects, then governed integrations and production workflows, followed by
declarative and optional visualization extensions. Relay runtime consolidation
is ordered last because it is the only workstream that reduces a security
boundary.

## 2. Ordered workstreams

### 1. Multi-conversation tiled workspace — approved now

Plan: MULTI_CONVERSATION_TILED_WORKSPACE_IMPLEMENTATION_PLAN.md

Deliver the live multi-conversation cockpit first. Every conversation/tool tile
is bound to a conversation; tile focus routes the shared composer, actions,
agent, and left panel. Simple is the same board at layout 1×1: full-size tiles in
a horizontal strip, insert-after-selected, scroll-to-focus, scroll-left to the
previous tile.

This workstream has no dependency on the other five plans and materially improves
the ability to operate and review all later workflows in parallel.

### 2. Effect receipts and reconciliation

Plan: EFFECT_RECEIPTS_RECONCILIATION_PLAN.md

Add prepared/executed/declared/verified/unknown proof, provider evidence,
reconciliation-before-retry, and dead-letter operations to the authoritative run
stores.

This is the correctness foundation for asynchronous providers and governed
connector mutations.

### 3. Governed connectors

Plan: GOVERNED_CONNECTORS_IMPLEMENTATION_PLAN.md

Add versioned connector/action manifests over PFP/MCP/services with exact auth,
secret bindings, effects, egress, quotas, schemas, receipts, registry, and UI.

Depends on the receipt contract for safe effect recovery.

### 4. Media Studio production and review 1.1

Plan: MEDIA_STUDIO_WORKFLOW_AGENT_IMPLEMENTATION_PLAN.md

Upgrade the installed 1.0 generation workflow with complete revision inheritance,
durable provider jobs, typed media QA, ShotSpec, variants, review decisions,
editorial locks, edit manifests, and a production board.

The prompt_id recovery path should use effect receipts. The production board
benefits from the multi-conversation cockpit but remains its own authoritative
domain projection.

### 5. Declarative matrix and typed reduce

Plan: DECLARATIVE_MATRIX_REDUCE_IMPLEMENTATION_PLAN.md

Compile bounded matrix/reduce macros into the existing DAG with deterministic
IDs, per-item policies, typed reducers, checkpoints, and source mapping.

Effectful retries depend on receipt classification.

### 6. Optional Archify package

Plan: ARCHIFY_INTEGRATION_PLAN.md

Deliver a signed optional PFP package using Project Graph/Wiki evidence, typed
diagram IR, deterministic diagnostics, safe HTML/SVG/PNG rendering, FileStore,
and atomic last-good publication.

This package has no authority over core source or project knowledge stores.

### 7. Desktop lifecycle, inventory, and controls

Plan: MULTI_WORKSPACE_RELAY_DESKTOP_IMPLEMENTATION_PLAN.md, work packages
WP8-WP10 only.

Deliver the canonical Desktop inventory, typed list/open/attach/stop actions with
exact-session confirmation, the Webchat dock button, the slash-command surface,
and the rule that no healthy Desktop is ever stopped by idle time, viewer count,
browser disconnect, or scheduled cleanup.

This slice is independent of runtime consolidation. It applies unchanged to
today's one-container-per-export runtime once the current singleton Desktop is
given a session ID, so it may be pulled forward into any slot. Scheduling it
before workstream 8 also exercises the inventory contract against a live runtime
before the expensive isolation work begins.

The plan's own dependency order places these packages after WP6/WP7; that
ordering is a consequence of session identity alone and must not be read as a
dependency on grouped mode.

### 8. Multi-workspace relay consolidation — gated

Plan: MULTI_WORKSPACE_RELAY_DESKTOP_IMPLEMENTATION_PLAN.md, work packages
WP0-WP7, WP11, WP12.

Run several logical relay exports as isolated worker processes inside one
physical supervisor container, each with its own mount/PID/IPC/network namespace,
`/workspace` root, persistent HOME, and optional virtual Desktop.

This workstream is gated on four conditions that the plan states as requirements
but that current code contradicts:

1. WP2 removes the unrestricted `sudo NOPASSWD:ALL` grant
   (`docker/relay-dev/Dockerfile:27`), but `pawflow_relay/cli.py` and
   `pawflow_relay/_relay_fs_setup.py` both depend on it for mount-root chown and
   for cleanup under a root-owned server mount. Those two operations must move to
   supervisor control-protocol calls before the grant is withdrawn.
2. The plan requires an enforced AppArmor/SELinux profile and a visible
   fail-closed downgrade, while `pawflow_relay/_thread_base.py` currently probes
   and falls back to `apparmor=unconfined` silently, and `docker-compose.yml`
   ships unconfined by default. That resolver stays acceptable for single-export
   mode and is forbidden for grouped mode.
3. `tools/fs_screen.py` auto-starts its own Xvfb on a hardcoded `:99` with `-ac`
   and mutates process-global `DISPLAY`. Grouped mode cannot ship while any code
   path can attach to a display it did not create.
4. Cross-principal grouping claims depend on
   `REMOTE_RELAY_ENROLLMENT_SHARING_PLAN.md`, which is not scheduled in this
   roadmap. Until it is, this workstream ships only for single-owner trust
   groups.

Physical consolidation is an optimization inside one trust domain, never a
substitute for a container boundary between mutually untrusted users.

## 3. Shared invariants

All workstreams must:

- keep documentation and code comments in English;
- use UUID and timestamp identities;
- remain fail-closed;
- preserve exact service/package/flow revisions;
- use existing authoritative stores rather than parallel state;
- avoid secret values in logs, UI, manifests, and artifacts;
- keep all actions asynchronous;
- preserve force-stop semantics;
- include focused unit/integration tests and full relevant regression gates;
- use one-shot migrations with no permanent compatibility layer;
- avoid commit, push, deployment, hotpatch, or release without explicit request.

A workstream that reduces or relies on a privilege boundary must additionally:

- name every existing code path that depends on the privilege being withdrawn,
  and land its replacement in the same change;
- fail closed and visibly when a required kernel/security capability is absent,
  never through an existing silent-fallback helper.

## 4. Dependency map

    Multi-conversation workspace
      -> independent UX foundation

    Effect receipts
      -> governed connector effects
      -> Media Studio provider jobs
      -> matrix per-item effect retry

    Project Graph + Project Wiki + PFP
      -> Archify package

    Desktop session identity
      -> Desktop inventory, dock, and slash commands

    Supervisor namespaces + privilege replacement + enforced MAC profile
      -> grouped relay runtime

    Remote relay enrollment/sharing (unscheduled)
      -> cross-principal grouping claims

Media Studio may begin revision/QA contract work in parallel after the workspace,
but durable provider submission must align with EffectReceiptV1.

Workstream 7 has no upstream dependency. Workstream 8 depends on workstream 7
only for the session-identity contract, not the reverse.

## 5. Licensing ledger

Reviewed snapshots:

- tt-a1i/archify b36d79f — MIT;
- calesthio/OpenMontage cd9f3c1 — AGPL-3.0, clean-room or separate service only;
- rlaope/oh-my-hermes 91aa8a1 — MIT;
- Salomondiei08/oh-my-hermes 7ce4e3c — no declared license, ideas only;
- agentenv/agentflow 09df017 — MIT;
- oomol-lab/open-connector 10a71c5 — Apache-2.0;
- OpenDCAI/GameFactory-3A 6670bb7 — Apache-2.0.

Every implementation maintains file-level attribution when code or tests are
selectively reused. No runtime, catalog, prompt corpus, model, media asset, font,
or generated artifact is imported without provenance and license review.

## 6. Cross-plan validation gate

Before a workstream is declared complete:

1. contracts and ownership are consistent with the other plans;
2. no second source of truth was added;
3. idempotency/effect semantics match CapabilityMetadata and receipts;
4. scope, ACL, redaction, egress, and secret rules are explicit;
5. migrations and rollback/last-good behavior are testable;
6. UI claims match authoritative stored state;
7. operational metrics and failure states are visible;
8. relevant package and Python 3.10–3.13 CI gates pass;
9. documentation and tests ship with code;
10. unrelated worktree changes remain untouched.
11. every plan referenced as a prerequisite is either scheduled here or
    explicitly recorded as an unscheduled gate.

# Temporal-Inspired Durable Execution Patterns for PawFlow

Status: architecture reference and design study
Date: 2026-08-31
Scope: PawFlow-native inspiration; no Temporal dependency or migration
Audience: core runtime, task authors, Workflow Agent authors, operators, and reviewers

## 1. Executive decision

Temporal is useful to PawFlow as a source of durable-execution semantics, not as
a runtime to adopt.

PawFlow should remain built around its existing Python-native abstractions:

- FlowFile as the data unit;
- Task as the processing boundary;
- Service as the injected capability boundary;
- FlowDefinition as the executable graph;
- ContinuousFlowExecutor as the queue and scheduling engine;
- FlowRunStore and WorkflowRunStore as authoritative durable run stores;
- ConfirmationStore as the durable interaction and timer store;
- AgentInboxStore as the durable leased ingress queue.

The valuable Temporal ideas are narrower:

1. keep an append-only, authoritative event history for durable runs;
2. replay orchestration decisions without repeating completed external effects;
3. make task retry, timeout, heartbeat, and idempotency semantics explicit;
4. treat an unknown external outcome as a first-class state;
5. model signals, tracked updates, read-only queries, and timers consistently;
6. pin both the flow definition and the runtime build that interprets it;
7. give parent and child runs explicit cancellation and close policies;
8. compact very long histories by starting a linked successor run;
9. build operator views as projections of authoritative run state;
10. test recovery by replaying histories and injecting crashes at every boundary.

This document does not approve implementation. It defines the concepts, their
PawFlow-shaped form, the value they provide, and a safe adoption order.

## 2. Goals

The design should help PawFlow answer, after any process or relay failure:

- What exact flow and code version was running?
- Which task attempt owned each FlowFile?
- Which orchestration decisions were already made?
- Which effects were definitely not attempted?
- Which effects were accepted or verified by an external provider?
- Which effects have an unknown outcome?
- Which work is safe to retry automatically?
- Which worker or executor currently owns the run?
- Which signal, timer, or user response will resume it?
- Why did a run transition to its current state?
- Can the same history be replayed without emitting different commands?

The design should improve crash recovery without weakening PawFlow's existing
authorization, force-stop, scope, and relay boundaries.

## 3. Non-goals

The following are explicitly outside this design:

- embedding or operating a Temporal cluster;
- rewriting PawFlow tasks as Temporal Activities;
- introducing a Go service or a Temporal SDK dependency;
- replacing FlowFile, Task, Service, FlowDefinition, or ContinuousFlowExecutor;
- making all Python task code deterministic;
- claiming exactly-once execution for arbitrary external providers;
- hiding operational failures behind infinite retries;
- introducing a generic distributed transaction coordinator;
- making transcript rows, SSE events, UI cards, or provenance projections
  authoritative;
- storing secrets, unrestricted payloads, prompts, provider tokens, or large
  media bodies in an event journal;
- distributing generic flow execution before a concrete multi-node requirement
  exists.

## 4. Concept mapping

The mapping is conceptual. It does not imply API or storage compatibility.

| Temporal concept | Closest PawFlow concept | Important difference |
|---|---|---|
| Workflow Definition | Immutable FlowDefinition version | PawFlow uses a graph of Tasks rather than deterministic user code |
| Workflow Execution | FlowRun, WorkflowRun, or deployed flow run epoch | PawFlow currently has more than one durable run model |
| Workflow Task | A replayable orchestration decision | ContinuousFlowExecutor currently executes a Task directly |
| Activity | An effectful Task attempt | Not every PawFlow Task is external or retry-safe |
| Task Queue | Connection queue, AgentInboxStore, or run work queue | Connection queues are local to one executor |
| Worker | ContinuousFlowExecutor thread/process or relay-side worker | PawFlow workers also cross relay and authorization boundaries |
| Event History | WorkflowRun events plus a proposed shared durable journal | Generic provenance is currently bounded in memory |
| Signal | durableNotify or durable inbox event | PawFlow signals should retain authenticated scope and stable IDs |
| Update | Authenticated tracked command with a result | PawFlow currently has several handler-specific CAS commands |
| Query | Read-only run inspector | A query must never mutate authoritative state |
| Timer | durableTimer and ConfirmationStore timer row | Already PawFlow-native and worker-free |
| Search Attribute | Typed run visibility projection | Must not become another state authority |
| Child Workflow | executeFlow, referenced subflow, or Workflow Agent invocation | Parent close and cancellation policies need one shared contract |
| Continue-As-New | Linked successor run carrying a compact state snapshot | PawFlow does not need this until histories become large |
| Worker Build ID | Server build plus task and package digests | Flow versions alone do not pin Python implementation code |

## 5. Verified PawFlow baseline

### 5.1 Generic continuous execution

ContinuousFlowExecutor already provides:

- queued connections with count and byte backpressure;
- relationship routing, fan-out, prioritization, and TTL;
- atomic dequeue at the individual connection level;
- task retries and failure relationships;
- periodic queue checkpoints;
- task-owned checkpoint hooks;
- flow hot-swap and version counters;
- runtime context injection;
- batch and continuously deployed execution;
- idle auto-stop;
- optional provenance events.

Checkpoint schema version 2 stores queue contents, task states, flow version,
and explicit processor-owned state. Small FlowFile bodies are inlined and larger
bodies are written to checkpoint content files.

This is useful snapshot recovery, but it is not an event-sourced execution
history:

- a snapshot says what was present at one point, not why it is present;
- a completed external effect is not represented by the queue snapshot;
- a crash between an effect and downstream enqueue can make retry safety
  ambiguous;
- a checkpoint cannot prove whether a routing decision was already committed;
- a snapshot taken while other state is changing can require conservative
  recovery;
- clearing a restored checkpoint avoids stale restore but does not itself create
  a durable acknowledgement of each recovered item.

The engine's transaction wording must therefore be interpreted as local
queue-processing intent, not as an atomic transaction across queues, run state,
filesystem state, relays, and external providers.

### 5.2 Specialized durable run systems

PawFlow already has stronger semantics for specific run types.

WorkflowRunStore provides SQLite-backed run state with:

- exact request and immutable flow digest;
- run generation and compare-and-swap transitions;
- permission, service, and authorization snapshots;
- renewable leases and recovery count;
- claimed inbox IDs;
- exact retry task and FlowFile checkpoint;
- a run-local step cache;
- ordered lifecycle events;
- structured errors;
- staged terminal payload and durable outbox;
- recoverable committing, waiting, retryable-failed, and cancelling states.

FlowRunStore provides a separate durable one-shot lifecycle for approved
declarative workflows, with immutable authority, terminal staging, outbox
delivery, recovery, and replay identity.

AgentInboxStore provides:

- durable ingress before transcript projection;
- stable message IDs and ordered sequence;
- claimed rows with renewable leases;
- expired-lease and orphaned-claim recovery;
- explicit acknowledge, release, transfer, and discard operations;
- idempotent legacy migration.

ConfirmationStore and the durableWait, durableNotify, and durableTimer tasks
already implement worker-free park-and-reinject behavior.

These are the seams to extend. A new durability layer must not replace them or
copy their state into another database.

### 5.3 Task safety metadata

Workflow-safe tasks already use:

- CapabilityEffect to describe observable capabilities;
- IdempotencyClass with pure, natural, run_cached, keyed_effect, and unsafe;
- stable run, generation, task, FlowFile, tool-call, and authorization IDs;
- fail-closed workflow task validation;
- provider-specific idempotency keys for selected adapters.

This taxonomy should become the basis of generic durable task policies. A second
classification system would create contradictory retry decisions.

### 5.4 Current observability boundary

WorkflowRunStore and FlowRunStore contain durable run events. In contrast, the
generic ProvenanceRepository keeps a bounded in-memory list. Provenance is
valuable for lineage and UI inspection, but it cannot serve as the sole crash
recovery authority.

The intended separation is:

- durable run journal: authoritative execution facts;
- checkpoint snapshot: performance optimization for fast recovery;
- provenance: data-lineage projection;
- transcript and SSE: user-facing projections;
- metrics and logs: operational projections;
- UI surfaces and Kanban: bounded projections.

## 6. Core invariants

Any implementation derived from this document should preserve these invariants.

1. Every durable record has a UUID and a UTC creation timestamp.
2. A run has one stable run ID and a monotonically controlled generation.
3. Every task attempt has a stable attempt ID.
4. Every message, timer, effect, and parent-child edge has its own stable ID.
5. A flow reference includes exact scope, version, digest, and source identity.
6. A runtime build reference includes every implementation artifact required to
   interpret or resume that flow.
7. State transitions use compare-and-swap or an equivalent generation check.
8. Record-before-act is mandatory for effects, timers, child starts, and signals.
9. A provider timeout after dispatch creates unknown, not failed-not-executed.
10. Unknown unsafe effects are never automatically retried.
11. Replay may rebuild decisions but may not repeat a completed or unresolved
    external effect.
12. Checkpoints accelerate replay; they never replace authoritative history.
13. Projections can be rebuilt from authoritative stores and never write state
    back implicitly.
14. Force stop remains an immediate execution stop, is not reported as an error,
    and does not poison the next run.
15. Recovery cannot widen the original authorization or service capability
    snapshot.
16. Redaction occurs before durable persistence and before publication.
17. Missing required identities, policies, or versions fail closed; there are no
    anonymous or default authority fallbacks.

## 7. Pattern A: append-only run history and command replay

### 7.1 Temporal idea

Temporal persists a complete Event History. Workflow code does not execute an
Activity or timer directly; it emits a command. On replay, newly emitted
commands are compared with recorded events. Matching events restore state
without repeating completed work.

### 7.2 Why PawFlow benefits

A queue snapshot can restore FlowFiles but cannot explain all committed
decisions. A durable history makes recovery inspectable and enables assertions
such as:

- task attempt 2 started from FlowFile F;
- its authorization was A;
- it requested external effect E;
- provider reference P was recorded;
- output O was committed to relationship success;
- downstream task D has not yet started.

This reduces both duplicate effects and operator guesswork.

### 7.3 PawFlow-shaped design

Do not create a second journal beside FlowRunStore and WorkflowRunStore. Add a
shared journal repository/mixin used by those stores. Add a
FlowInstanceRunStore only for deployed generic flows that currently have no
authoritative run record.

A minimal event envelope could be:

    {
      "schema_version": 1,
      "event_id": "6ab38cc5-9808-4432-9ba2-f62cf73bd269",
      "created_at": "2026-08-31T10:00:00.000000Z",
      "run_id": "fr_...",
      "generation": 3,
      "sequence": 184,
      "event_type": "task_output_committed",
      "flow_ref": {
        "scope": "conversation",
        "name": "pawflow.media.release",
        "version": "1.2.0",
        "digest": "sha256:..."
      },
      "runtime_build_ref": "rb_...",
      "task_id": "publish_asset",
      "task_type": "httpRequest",
      "attempt_id": "ta_...",
      "flowfile_id": "ff_...",
      "causation_event_id": "ev_...",
      "correlation_id": "turn_...",
      "payload_digest": "sha256:...",
      "payload_ref": null,
      "safe_metadata": {
        "relationship": "success",
        "result_count": 1
      }
    }

Large bodies remain in FileStore or the existing FlowFile content layer. The
journal stores only bounded metadata, content digests, and authorized references.

Recommended command/event pairs include:

| Prepared command | Result event |
|---|---|
| task_attempt_scheduled | task_attempt_started |
| effect_prepared | effect_declared, effect_verified, effect_unknown |
| timer_scheduled | timer_fired or timer_cancelled |
| signal_accepted | signal_consumed |
| child_start_prepared | child_started or child_start_failed |
| output_commit_prepared | task_output_committed |
| run_terminal_staged | run_terminal_committed |
| history_rollover_requested | history_rollover_completed |

### 7.4 Replay algorithm

1. Load the immutable flow and runtime build references.
2. Load the latest verified snapshot, if any.
3. Validate the snapshot history sequence and digest.
4. Read later events in sequence order.
5. Rebuild queue ownership, task state, timers, messages, receipts, and children.
6. For a recorded pure result, restore the cached result.
7. For a declared or verified effect, restore the result projection.
8. For an unknown effect, park the run and schedule reconciliation.
9. For a prepared but never started operation, classify it from the task policy.
10. Resume only when the reconstructed state has one valid owner for every
    FlowFile and attempt.
11. Append a run_recovered event before dispatching new work.

Replay must be idempotent. Replaying the same history twice must produce the
same snapshot digest and no external calls.

### 7.5 What it adds

- precise crash recovery;
- explainable run timelines;
- a foundation for replay tests;
- safe rebuild of UI and provenance projections;
- detection of divergent execution;
- a durable answer to whether work was committed.

### 7.6 Cost and limits

- more storage writes and indexes;
- schema evolution requirements;
- retention and compaction work;
- careful payload redaction;
- sequencing contention if every low-level observation becomes an event.

Only state-changing facts belong in the journal. Debug logs, token streaming,
and high-frequency progress samples remain outside it.

## 8. Pattern B: deterministic orchestration and explicit effects

### 8.1 Temporal idea

Temporal Workflow code must emit the same command sequence when given the same
history. Non-deterministic calls such as network requests, database queries,
randomness, wall-clock reads, and LLM calls are moved into Activities or captured
through replay-safe APIs.

### 8.2 PawFlow adaptation

PawFlow should not require every Task implementation to be deterministic. The
Flow graph is the orchestration definition; Tasks are the computation/effect
boundary.

The replay contract should instead be:

- graph traversal and relationship selection are reproducible from recorded
  inputs and results;
- pure task results may be recomputed and compared;
- non-deterministic task results are recorded and restored;
- effectful tasks are never re-executed solely to reconstruct orchestration;
- time, randomness, model routes, human decisions, and external observations
  become recorded inputs.

A TaskExecutionContractV1 can extend existing metadata:

    task_type: generateVideo
    effects:
      - network.write
      - external.side_effect
    idempotency: keyed_effect
    replay_behavior: restore_result
    result_retention: run
    retry_policy_ref: media-provider-default
    timeout_policy_ref: long-provider-job
    heartbeat_policy_ref: provider-poll-progress
    compensation: none

The values should be validated during flow publication for durable modes.

### 8.3 Routing example

Suppose an LLM classification task routes to approve or reject. Running the LLM
again during recovery could choose a different branch.

The durable behavior is:

1. persist llm_step_prepared with prompt and service digests;
2. execute the call once under the run budget;
3. persist a bounded validated result and result digest;
4. persist route_selected with the chosen relationship;
5. during replay, restore the validated result and route event;
6. never call the LLM merely to rebuild the graph state.

### 8.4 What it adds

- reproducible branch decisions;
- stable budget accounting;
- fewer duplicated LLM/provider calls;
- a clean boundary between orchestration and effects;
- safer flow evolution and replay validation.

### 8.5 What it does not add

It does not make arbitrary Python deterministic. It makes durable orchestration
depend on recorded facts instead of rerunning arbitrary code.

## 9. Pattern C: first-class task execution policies

### 9.1 Temporal idea

Temporal Activities distinguish several time bounds and combine them with retry
policies and heartbeats:

- schedule-to-start: time waiting for a worker;
- start-to-close: time for one attempt;
- schedule-to-close: total time across all attempts;
- heartbeat timeout: maximum silence while an attempt is active.

Retries define initial interval, backoff coefficient, maximum interval, maximum
attempts, and non-retryable failure types.

### 9.2 Current PawFlow limitation

ContinuousFlowExecutor has an executor-wide retry count and short linear wait.
Task-specific workflow hooks exist, but generic Task does not expose one
versioned, validated execution policy. A value of zero also has specialized
meaning in current retry loops, which is easy for authors to misunderstand.

### 9.3 Proposed policy

Define TaskExecutionPolicyV1 and require it for durable effectful tasks:

    {
      "schema_version": 1,
      "policy_id": "media-render-v1",
      "created_at": "2026-08-31T10:00:00.000000Z",
      "schedule_to_start_seconds": 60,
      "start_to_close_seconds": 1800,
      "schedule_to_close_seconds": 7200,
      "heartbeat_timeout_seconds": 45,
      "retry": {
        "maximum_attempts": 4,
        "initial_interval_seconds": 2,
        "backoff_coefficient": 2.0,
        "maximum_interval_seconds": 60,
        "jitter_ratio": 0.2,
        "non_retryable_codes": [
          "invalid_input",
          "authorization_denied",
          "unsupported_format"
        ]
      }
    }

The policy is snapshotted into the run. Updating a named policy later cannot
change an active run.

### 9.4 Failure taxonomy

At minimum, runtime failures should distinguish:

- caller_invalid;
- authorization_denied;
- permanent_provider_rejection;
- transient_provider_failure;
- rate_limited with retry-after;
- local_timeout;
- provider_timeout_unknown;
- worker_lost;
- cancelled;
- force_stopped;
- budget_exceeded;
- non_deterministic_replay;
- internal_invariant_violation.

Each class has an explicit retry decision. Exception text alone never decides
whether a destructive effect is safe to repeat.

### 9.5 Heartbeats

A long-running task heartbeat should carry bounded progress:

- attempt ID;
- monotonic progress sequence;
- observed UTC time;
- stage;
- optional percentage;
- opaque provider reference digest;
- resumable checkpoint reference;
- cancellation acknowledgement.

Heartbeats renew the attempt lease. They are not full event-history entries at
every ping; the current projection is updated, while meaningful progress
milestones may be journaled.

### 9.6 Example

A video-render task submits a provider job and polls for 20 minutes.

- schedule-to-start detects an unavailable worker pool;
- start-to-close bounds one worker attempt;
- schedule-to-close bounds the total run delay;
- heartbeat timeout detects a lost relay;
- heartbeat details retain the provider job reference;
- retry resumes polling instead of submitting a second render;
- provider rejection is non-retryable;
- rate limiting uses the provider retry-after value within the total deadline.

### 9.7 What it adds

- predictable latency;
- bounded retry storms;
- meaningful operator status;
- faster lost-worker detection;
- resumable long tasks;
- a common contract across local, relay, and provider work.

## 10. Pattern D: effect receipts and unknown outcomes

### 10.1 Temporal lesson

Temporal retries Activities, but application authors must make external effects
idempotent. Durable orchestration cannot create a distributed transaction with
an arbitrary email server, filesystem, browser, payment API, or media provider.

### 10.2 PawFlow direction

The detailed design already exists in
[EFFECT_RECEIPTS_RECONCILIATION_PLAN.md](EFFECT_RECEIPTS_RECONCILIATION_PLAN.md).
It should remain authoritative for receipt schema and reconciliation.

The essential lifecycle is:

    prepared -> executing -> executed -> declared -> verified
                             |
                             +-> unknown -> reconcile
                                          -> verified
                                          -> declared
                                          -> rejected
                                          -> not_found_safe_to_retry

Prepared proves intent, not execution. Executed proves that the local adapter
returned, not that the destination visibly changed. Declared means the provider
gave a stable acknowledgement. Verified requires an independent observation.
Unknown remains unresolved until evidence supports a decision.

### 10.3 Crash example

A task sends an email:

1. receipt prepared and committed;
2. provider accepts the email;
3. network response is lost;
4. PawFlow crashes before storing a provider message ID.

The incorrect recovery is to retry because no success row exists. That can send
the email twice.

The correct recovery is:

1. restore the receipt as unknown;
2. query by provider idempotency key or destination evidence;
3. mark verified if delivery is observed;
4. retry only if authoritative not-found plus the idempotency class proves it is
   safe;
5. otherwise keep unknown and escalate to a bounded dead letter.

### 10.4 What it adds

- duplicate prevention;
- honest representation of ambiguous outcomes;
- provider-aware recovery;
- auditable evidence;
- a safe basis for automated retries.

### 10.5 Exactly-once wording

PawFlow may claim:

- exactly-once durable recording inside one SQLite transaction;
- effectively-once external behavior when a provider honors a stable
  idempotency key and reconciliation verifies the result;
- at-least-once attempts for explicitly idempotent operations;
- at-most-once attempts for unsafe operations that cannot be reconciled.

PawFlow must not claim generic exactly-once external effects.

## 11. Pattern E: signals, tracked updates, queries, and timers

### 11.1 Temporal idea

Temporal separates:

- Signal: asynchronous state-changing message;
- Update: tracked state-changing request with acceptance and a result;
- Query: read-only inspection;
- Timer: durable wake-up that consumes no worker while waiting.

### 11.2 Existing PawFlow foundation

PawFlow already has durable wait/notify/timer tasks, user interactions,
AgentInboxStore, run control handlers, and read-only inspectors. The opportunity
is to give them one shared message contract.

### 11.3 Proposed RunMessageV1

Required fields:

- message_id and created_at;
- authenticated user and conversation scope;
- logical run ID and generation;
- message kind: signal, update, or query;
- message name and schema version;
- deduplication key;
- expected state revision for updates;
- correlation and causation IDs;
- bounded redacted payload or payload reference;
- acceptance state and terminal result reference;
- expiry and cancellation metadata.

A signal is persisted before returning accepted. Duplicate signals with the same
deduplication key return the existing receipt.

An update has two phases:

1. validate and durably accept or reject under CAS;
2. process and persist a result or typed failure.

A query reads an immutable snapshot/projection and never appends a state-changing
run event.

### 11.4 Example: change a deadline

A running media workflow waits until a publication deadline.

- query current deadline: read-only and no history mutation;
- signal add-note: durable asynchronous message;
- update change-deadline: validate permission and expected revision, cancel the
  prior timer, create a new timer, and return the accepted deadline;
- timer firing: append timer_fired once and reinject the parked FlowFile.

### 11.5 What it adds

- one mental model across chat, UI, API, CLI, and workflow-to-workflow messages;
- stable deduplication;
- tracked mutations;
- fewer polling loops;
- durable human-in-the-loop interactions;
- clear distinction between state reads and writes.

## 12. Pattern F: leases, heartbeats, and graceful shutdown

### 12.1 Temporal idea

Workers poll for tasks, active work has ownership, long Activities heartbeat,
and graceful shutdown stops new polling before waiting for in-flight work.

### 12.2 PawFlow foundation

AgentInboxStore already uses renewable leases and expired-lease recovery.
WorkflowRunStore records active ownership and generation. This mechanism should
be generalized only where a durable task can outlive its executor process.

### 12.3 Proposed attempt lease

An attempt lease should contain:

- lease ID, attempt ID, run ID, generation, and task ID;
- worker ID and RuntimeBuildRef;
- acquired, renewed, and expiry timestamps;
- last heartbeat sequence and safe progress summary;
- cancellation and force-stop generations;
- optional relay identity and relay session generation.

Acquisition is transactional. Renewal requires the same worker, attempt, run
generation, and lease generation. Completion requires a live lease or a
recovery-specific CAS path.

### 12.4 Recovery

When a lease expires:

1. append worker_lease_expired;
2. classify the task from its execution and idempotency policies;
3. pure or run-cached work may restart or restore;
4. keyed effects inspect their receipt;
5. unsafe unknown work parks for review;
6. resumable work restores the last heartbeat checkpoint;
7. stale workers are fenced and cannot commit late output.

### 12.5 Graceful shutdown

A normal shutdown should:

1. stop accepting or polling new attempts;
2. advertise draining state;
3. request cooperative cancellation for eligible tasks;
4. wait for the configured grace period;
5. persist checkpoints and lease handoff state;
6. fence remaining attempts;
7. exit without turning force-stopped work into a failure.

Force stop remains immediate and bypasses the grace period.

### 12.6 What it adds

- deterministic ownership after relay or process loss;
- no late commit from a stale worker;
- better long-task cancellation;
- safer deploys;
- resumable provider polling and file processing.

## 13. Pattern G: pin runtime builds as well as flows

### 13.1 Temporal idea

Temporal Worker Versioning can pin a Workflow to one worker deployment version
or allow a replay-compatible auto-upgrade. Old workers may drain while pinned
workflows finish.

### 13.2 Current PawFlow strength and gap

PawFlow already publishes immutable flow versions and records ResourceRef
digests. This pins the graph and its visible resource references.

It does not automatically prove that a later server process interprets each
task type with the same Python implementation. A built-in task can change
between PawFlow releases while the flow version stays unchanged. A package can
also be upgraded or removed.

### 13.3 Proposed RuntimeBuildRef

A durable run should freeze a RuntimeBuildRef containing:

- PawFlow version and source/build digest;
- Python contract/runtime schema version;
- built-in task registry digest;
- exact PFP package names, versions, signatures, and content digests;
- exact service adapter revisions;
- flow lowering version;
- relevant feature gates;
- migration epoch;
- creation UUID and UTC timestamp.

Secrets and environment values are not part of the build reference. Their safe
revisions belong in the existing service snapshot.

### 13.4 Two behaviors

Pinned:

- active run resumes only on a compatible worker with the exact build reference;
- old workers drain until no pinned runs remain;
- operator migration creates a reviewed successor run, never silently rewrites
  history.

Compatible upgrade:

- a new build declares the earlier replay contract versions it supports;
- replay tests for representative stored histories must pass;
- an explicit history event records the build transition;
- incompatible histories remain on the older build or pause for migration.

Pinned should be the default for effectful long-running flows. Compatible
upgrade is an optimization, not a fallback.

### 13.5 Example

A flow starts on beta.254, waits two weeks, and resumes after beta.260 changed
the behavior of a routing task.

Without build pinning, the same immutable flow may choose a new route.

With build pinning, PawFlow either:

- runs the old task implementation for that pinned run; or
- executes an explicit, tested migration to a successor run.

### 13.6 What it adds

- safe long-lived workflows across releases;
- reproducible incident investigation;
- package integrity;
- controlled draining and rollback;
- no hidden semantic upgrade.

## 14. Pattern H: explicit parent-child run semantics

### 14.1 Temporal idea

Child Workflows have independent histories and an explicit Parent Close Policy.
They are useful for separate services, independent lifecycle, or partitioning
large work, not merely code organization.

### 14.2 PawFlow adaptation

Referenced subflows used only for graph organization should remain inline
execution. A separate child run is justified when it needs at least one of:

- independent durability or retention;
- separate authorization or service boundary;
- independent retry and timeout budget;
- different worker/build routing;
- separate operator visibility;
- high fan-out partitioning;
- an independently addressable logical resource.

### 14.3 ParentChildRunLinkV1

The link should freeze:

- link ID and timestamp;
- parent run ID, generation, and task attempt;
- child run ID, flow reference, and runtime build reference;
- stable invocation and deduplication key;
- input and expected terminal schema digests;
- wait mode;
- cancellation propagation;
- parent close policy;
- authorization delegation ceiling;
- result projection rule.

Recommended parent close policies:

- wait: parent cannot complete while the child is non-terminal;
- request_cancel: parent closure requests cooperative child cancellation;
- terminate: parent closure immediately fences the child;
- abandon: child continues independently and retains its own authority.

The default for authored child calls should be wait or request_cancel. Abandon
requires explicit validation because it creates work that outlives its caller.

### 14.4 Fan-out example

A parent media workflow must render 50 independent clips.

It creates 50 child runs with:

- one exact media subflow version;
- a distinct clip ID and budget;
- bounded concurrency;
- the same parent correlation ID;
- request_cancel parent policy;
- terminal results joined by stable child IDs.

Each child has its own retries, receipts, and event history. The parent history
stores only child lifecycle summaries rather than every provider polling event.

### 14.5 What it adds

- clear cancellation behavior;
- bounded histories;
- independent recovery;
- better fan-out observability;
- no orphan work created accidentally.

## 15. Pattern I: history rollover instead of unbounded journals

### 15.1 Temporal idea

Continue-As-New starts a fresh run with a new Run ID and a fresh history while
carrying forward the relevant state under the same logical Workflow ID.

### 15.2 When PawFlow needs it

Do not implement rollover merely because Temporal has it. It becomes valuable
when a PawFlow run is intentionally long-lived and its journal or projections
become expensive.

Candidate triggers:

- event count or stored metadata exceeds an explicit threshold;
- history replay exceeds a latency budget;
- the run reaches a reviewed upgrade boundary;
- a periodic entity workflow completes one bounded cycle;
- retention policy requires separating hot and cold history.

### 15.3 PawFlow-shaped rollover

1. Reach a declared safe boundary with no in-flight attempt.
2. Reconcile or retain explicit references to all unresolved effects.
3. Build a canonical compact state object.
4. Store the object or FileStore reference and digest.
5. Append history_rollover_requested to the old run.
6. Create a new run with a new run ID and previous_run_id.
7. Retain the logical workflow ID.
8. Freeze the successor flow and runtime build references.
9. Carry active timers, message cutoffs, parent-child links, budgets, and
   authorization only through explicit versioned fields.
10. Append history_rollover_completed and seal the old run.

The old history is immutable and remains inspectable.

### 15.4 Example

A project-maintenance workflow processes repository changes indefinitely.
Every 10,000 journal events it finishes the current batch, stores the current
source cursor and configuration digest, then starts a successor run. The new run
continues with a small history while the logical maintenance identity remains
stable.

### 15.5 What it adds

- bounded replay time;
- bounded hot indexes;
- controlled upgrade boundaries;
- clearer retention;
- indefinite logical workflows without indefinite physical runs.

## 16. Pattern J: visibility as a typed projection

### 16.1 Temporal idea

Temporal Visibility and Search Attributes expose typed, indexed metadata for
finding Workflow Executions without making that index authoritative.

### 16.2 PawFlow adaptation

PawFlow already exposes runtime consoles, workflow inspectors, Kanban views,
provenance, usage ledgers, and conversation projections. They should be fed by a
shared RunVisibilityProjection rather than each inferring state differently.

Suggested allowlisted fields:

- user and conversation scope;
- logical workflow ID, run ID, and generation;
- flow FQN, flow digest, and runtime build ID;
- run kind and coarse state;
- current safe task label;
- start, update, wait, deadline, and terminal timestamps;
- parent and root run IDs;
- authorization mode;
- effect ceiling;
- unresolved receipt count and oldest unknown age;
- active timer count;
- retry and recovery count;
- bounded cost and usage totals;
- safe tags and artifact count;
- redacted terminal code.

No prompt, secret, raw FlowFile content, unrestricted error, or provider response
belongs in visibility metadata.

### 16.3 Projection rules

- projections are updated from committed authoritative events;
- projection writes are idempotent by event sequence;
- projection lag is observable;
- rebuild starts from authoritative run stores;
- a missing projection never changes run execution;
- API filters are scoped before evaluation;
- custom tags are bounded and typed.

### 16.4 Example queries

Operators should be able to ask:

- waiting runs whose deadline is within one hour;
- runs pinned to a draining runtime build;
- runs with unknown destructive effects older than ten minutes;
- children still active after a parent terminal state;
- retryable failures grouped by safe error code;
- runs whose visibility projection is behind the journal sequence.

### 16.5 What it adds

- one consistent operations view;
- fast incident discovery;
- retention-independent rebuilding;
- safe filtering without exposing payloads;
- metrics derived from the same state model.

## 17. Pattern K: deterministic replay and crash testing

### 17.1 Temporal lesson

Durability claims are credible only when histories can be replayed and code
changes are tested against existing histories.

### 17.2 Required PawFlow test layers

Contract tests:

- event schemas reject missing UUIDs, timestamps, identities, or versions;
- state machines reject illegal transitions;
- task policies have explicit bounded values;
- redaction and size limits hold.

Replay tests:

- a stored history reconstructs the expected snapshot digest;
- replay emits the same command sequence;
- pure recomputation matches the recorded result digest;
- a mismatching command produces non_deterministic_replay;
- an older RuntimeBuildRef is either supported or explicitly rejected.

Crash-boundary tests:

- crash before command persistence;
- crash after command persistence but before dispatch;
- crash during provider call;
- crash after provider acceptance but before response persistence;
- crash after result persistence but before queue commit;
- crash during partial fan-out;
- crash before and after terminal outbox delivery;
- crash during timer creation, signal acceptance, child start, and history
  rollover;
- crash during lease renewal and graceful shutdown.

Concurrency tests:

- two workers compete for one lease;
- a stale worker tries to commit after fencing;
- duplicate updates share one stable result;
- duplicate signals deduplicate by key;
- force stop races with completion;
- a new message arrives after a force-stop cutoff;
- parent and child cancellation cross.

Operational tests:

- restore with missing or corrupted snapshots falls back to journal replay;
- projection rebuild produces the same visibility rows;
- retention preserves unresolved receipts and sealed run lineage;
- one malformed run does not block recovery of other runs.

### 17.3 Fault-injection mechanism

Provide named internal kill points rather than timing-dependent sleeps, for
example:

- after_effect_prepared;
- after_provider_dispatch;
- before_effect_outcome_commit;
- before_output_commit;
- after_output_commit;
- before_terminal_projection;
- after_terminal_projection;
- before_lease_handoff.

Tests terminate the worker at a named point, restart the runtime, and assert the
exact resulting state. Production builds keep the hooks disabled.

### 17.4 What it adds

- reproducible recovery failures;
- safe runtime upgrades;
- proof that retries do not duplicate effects;
- less dependence on thread timing;
- a durable compatibility gate for releases.

## 18. Pattern L: task queues and distributed routing, only when needed

Temporal separates task matching from worker execution. PawFlow should not copy
that architecture prematurely.

Connection queues are appropriate while one ContinuousFlowExecutor owns the
flow. A durable dispatch queue becomes justified only when PawFlow needs:

- multiple server processes competing for the same task type;
- worker pools with different GPU, desktop, relay, or package capabilities;
- server restart without keeping the original executor process;
- per-tenant fairness across many durable runs;
- controlled draining by runtime build.

If that requirement arrives, a PawFlow DurableTaskQueue should use the same
attempt IDs, leases, policies, RuntimeBuildRef, authorization snapshot, and
effect receipts defined above. It should not serialize unrestricted FlowFile
bodies into a broker.

Until then, keep scheduling local and improve the durability of the existing
executor.

## 19. End-to-end worked example

### 19.1 Flow

A user asks PawFlow to generate a campaign video, review it, and publish it.

    inputPort
      -> validateRequest
      -> reserveProject
      -> submitRender
      -> awaitRender
      -> requestUserInput
      -> publishAsset
      -> completeFlowRun

Semantics:

- validateRequest is pure;
- reserveProject is a natural or keyed effect;
- submitRender is a keyed external effect;
- awaitRender is resumable and heartbeats provider progress;
- requestUserInput parks through ConfirmationStore;
- publishAsset is a keyed or unsafe external effect depending on provider support;
- completeFlowRun stages the sole terminal result.

### 19.2 Frozen run inputs

At approval PawFlow stores:

- run ID and generation;
- exact FlowDefinition ResourceRef and digest;
- RuntimeBuildRef;
- immutable parameters;
- service revision snapshots;
- authorization reference and effect ceiling;
- TaskExecutionPolicy for each effectful task;
- total time, cost, iteration, and artifact budgets;
- parent run link, if invoked from another flow.

### 19.3 Nominal event sequence

| Seq | Event | Meaning |
|---:|---|---|
| 1 | run_accepted | Exact definition, build, authority, and input frozen |
| 2 | run_started | Worker lease acquired |
| 3 | task_attempt_started | validateRequest attempt 1 |
| 4 | task_output_committed | Validated FlowFile routed |
| 5 | effect_prepared | Project reservation receipt created |
| 6 | effect_verified | Existing or newly created project observed |
| 7 | effect_prepared | Render submission receipt created |
| 8 | effect_declared | Provider job reference stored |
| 9 | task_waiting | Worker released; provider job remains authoritative |
| 10 | task_heartbeat | Poller observes 60 percent progress |
| 11 | effect_verified | Render artifact and digest observed |
| 12 | interaction_parked | Human review request persisted |
| 13 | update_accepted | Approval accepted under expected revision |
| 14 | effect_prepared | Publication receipt created |
| 15 | effect_verified | Published destination observed |
| 16 | run_terminal_staged | Stable terminal payload and event ID stored |
| 17 | run_terminal_committed | Outbox delivered and run completed |

Every row has its own UUID and UTC timestamp, even though the table omits them
for readability.

### 19.4 Crash after render submission

The provider accepted the job, but the worker crashed before receiving the HTTP
response.

Recovery sees:

- render receipt prepared;
- dispatch started;
- no declared provider reference;
- attempt lease expired.

It transitions the receipt to unknown and runs provider reconciliation. It does
not submit another render. If the provider supports lookup by idempotency key,
PawFlow finds the job, stores the provider reference, and resumes awaitRender.

### 19.5 Crash while waiting for approval

No worker is active. ConfirmationStore owns the parked FlowFile and deadline.
Restart recovery reconstructs the waiting state and UI projection. The user's
later approval is accepted once by update ID and reinjects the FlowFile.

### 19.6 Deploy during the wait

The run is pinned to its RuntimeBuildRef. The new server build may project the
run, but it may resume it only if:

- the matching pinned implementation is available; or
- the new build declares and proves replay compatibility.

Otherwise the run remains safely waiting for an operator-approved migration.

### 19.7 Crash after publication but before terminal delivery

The publication receipt is already verified, so publishAsset is not repeated.
The terminal payload and event ID are stable. The coordinator resumes the
terminal outbox saga, appends the result idempotently, acknowledges the outbox,
and completes the same run.

## 20. Recommended adoption order

### Phase 0: contracts and baseline audit

Deliverables:

- document the exact authority of every existing store;
- define shared event, attempt, policy, build, message, and parent-child schemas;
- map existing WorkflowRunStore and FlowRunStore fields to those contracts;
- inventory task effects and idempotency metadata;
- correct stale transaction wording in user documentation;
- add red tests for the known crash windows.

Value: shared language with no runtime migration.

### Phase 1: shared journal for existing durable run stores

Deliverables:

- add one append-only event envelope implementation reused by WorkflowRunStore
  and FlowRunStore;
- keep their current state projections and outboxes;
- add history sequence and digest;
- replay into an in-memory validation snapshot;
- rebuild existing UI projections from committed events in tests.

Value: durable explainability and replay gates where strong run stores already
exist.

### Phase 2: task execution policies and attempt records

Deliverables:

- define TaskExecutionPolicyV1;
- bind existing CapabilityEffect and IdempotencyClass;
- create stable attempt IDs;
- persist timeout, retry, heartbeat, and failure classification;
- port a small set of representative tasks: pure transform, LLM call, provider
  job, HTTP mutation, and filesystem write.

Value: bounded, consistent execution semantics.

### Phase 3: effect receipts and reconciliation

Execute
[EFFECT_RECEIPTS_RECONCILIATION_PLAN.md](EFFECT_RECEIPTS_RECONCILIATION_PLAN.md)
without creating another state authority.

Value: safe handling of unknown external outcomes.

### Phase 4: unified run messages and timers

Deliverables:

- project existing durable wait/notify/timer and inbox behavior into
  RunMessageV1;
- separate signal, update, and query APIs;
- require stable deduplication and expected revisions;
- keep ConfirmationStore and AgentInboxStore authoritative.

Value: consistent human and machine interaction.

### Phase 5: runtime build pinning and replay compatibility

Deliverables:

- create RuntimeBuildRef;
- persist it on new durable runs;
- add pinned and compatible-upgrade worker admission;
- retain old runtime artifacts for the supported drain window;
- run stored-history replay tests in CI before declaring compatibility.

Value: safe long-lived runs across PawFlow releases.

### Phase 6: generic deployed-flow durability

Only after the preceding contracts are stable:

- add FlowInstanceRunStore for deployed generic flows;
- journal task attempts and queue commits;
- use existing checkpoints as snapshots;
- recover from journal plus snapshot;
- add task attempt leases where work can outlive the executor.

Value: bring generic continuous flows to the same recovery standard.

### Phase 7: visibility and optional history rollover

Deliverables:

- typed RunVisibilityProjection;
- rebuild tooling and lag metrics;
- rollover only for runs that cross measured history thresholds;
- preserve sealed histories and logical run chains.

Value: bounded operations at scale.

### Phase 8: optional distributed task routing

Proceed only with a concrete multi-process worker-pool requirement.

## 21. Priority matrix

| Idea | PawFlow status | Recommendation |
|---|---|---|
| Immutable flow versions | Implemented | Keep |
| Durable run state and CAS generations | Implemented for FlowRun/WorkflowRun | Generalize carefully |
| Durable waits, timers, and reinjection | Implemented | Unify contracts |
| Leased agent inbox | Implemented | Reuse |
| Terminal outbox saga | Implemented | Reuse |
| Task effect/idempotency taxonomy | Implemented for workflow-safe tasks | Extend, do not duplicate |
| Effect receipts and reconciliation | Planned | Highest-value next durability work |
| Append-only shared run journal | Partial | Adopt for durable stores |
| Deterministic command replay | Partial | Adopt at orchestration boundary |
| Per-task retry/timeout/heartbeat policy | Partial | Adopt |
| Runtime build pinning | Missing | Adopt before very long-lived generic runs |
| Parent-child close policy | Partial | Adopt with child run composition |
| Typed visibility projection | Fragmented | Adopt after journal contracts |
| Continue-as-new/history rollover | Missing | Defer until measured need |
| Distributed matching service | Missing | Defer |
| Temporal cluster or SDK | Not used | Reject for this purpose |

## 22. Operational metrics

Recommended metrics include:

- event journal append latency and failures;
- snapshot age and journal distance from snapshot;
- replay duration and reconstructed event count;
- command divergence count;
- active, expired, and fenced attempt leases;
- attempts by failure class;
- retry scheduled, prevented, and exhausted;
- heartbeat age and lost-worker count;
- receipts by state;
- oldest unknown receipt age;
- reconciliation latency and dead-letter backlog;
- duplicate effect prevented count;
- timer lag;
- signal and update deduplication count;
- visibility projection lag;
- active runs by RuntimeBuildRef;
- pinned runs on draining builds;
- child runs by parent close policy;
- history rollover count and carried-state size.

Alerts should be based on age, lag, and invariant failure, not raw log volume.

## 23. Security and privacy

Durability increases the amount of retained metadata, so the storage contract
must be stricter than normal debug logging.

- apply existing user and conversation ACLs to every journal, receipt, query,
  and projection;
- freeze authorization references and capability ceilings per run;
- never let replay or recovery request broader permissions;
- retain only bounded allowlisted metadata;
- hash sensitive targets with a scoped server key when plain identifiers are not
  needed;
- store large evidence in FileStore under scoped references;
- never store provider credentials, secrets, unrestricted prompts, reasoning,
  desktop screenshots, media bodies, or filesystem bodies in the journal;
- treat external text as data and validate it through the owning adapter;
- require expected generation for all operator mutations;
- audit reviewed retry, migration, cancellation, abandon, and compensation;
- make retention explicit for sealed histories and unresolved effects;
- prevent cross-run receipt, timer, message, and child-link attachment.

## 24. Documentation and implementation boundaries

This guide owns the cross-cutting inspiration and vocabulary.

Existing documents remain authoritative for their specialized areas:

- [Architecture](architecture.md): current engine and component structure;
- [Agent System](AGENT_SYSTEM.md): current Workflow Agent runtime;
- [Declarative Workflows Implementation Plan](DECLARATIVE_WORKFLOWS_IMPLEMENTATION_PLAN.md):
  FlowRun and declarative composition;
- [Workflow Agents Implementation Plan](WORKFLOW_AGENTS_IMPLEMENTATION_PLAN.md):
  WorkflowRun lifecycle and agent-specific recovery;
- [Effect Receipts and Reconciliation Plan](EFFECT_RECEIPTS_RECONCILIATION_PLAN.md):
  receipt schema and reconciliation rollout;
- [Durable User Interactions](confirmations.md): user inputs, signals, timers,
  and reinjection;
- [Workflow Agent Operations](WORKFLOW_AGENT_OPERATIONS.md): current operator
  procedures;
- [Provenance](provenance.md): FlowFile lineage projection.

Future implementation plans should link to this guide and explicitly state
which patterns they adopt, defer, or reject.

## 25. Temporal references

The following public sources were reviewed on 2026-08-31. They are conceptual
references only; no Temporal source code is copied into PawFlow.

- [Temporal repository](https://github.com/temporalio/temporal), MIT license;
- [Temporal server architecture](https://github.com/temporalio/temporal/tree/main/docs/architecture);
- [Event History](https://docs.temporal.io/encyclopedia/event-history);
- [Workflow Definition and deterministic constraints](https://docs.temporal.io/workflow-definition);
- [Activity Execution](https://docs.temporal.io/activity-execution);
- [Retry Policies](https://docs.temporal.io/encyclopedia/retry-policies);
- [Activity timeouts and heartbeats](https://docs.temporal.io/develop/python/activities/timeouts);
- [Workflow message passing](https://docs.temporal.io/encyclopedia/workflow-message-passing);
- [Timers and Start Delays](https://docs.temporal.io/workflow-execution/timers-delays);
- [Worker Versioning](https://docs.temporal.io/worker-versioning);
- [Child Workflows](https://docs.temporal.io/child-workflows);
- [Parent Close Policy](https://docs.temporal.io/parent-close-policy);
- [Continue-As-New](https://docs.temporal.io/workflow-execution/continue-as-new);
- [Visibility](https://docs.temporal.io/visibility);
- [Search Attributes](https://docs.temporal.io/search-attribute);
- [Worker Shutdown Behavior](https://docs.temporal.io/encyclopedia/workers/worker-shutdown).

## 26. Final recommendation

PawFlow should copy Temporal's discipline, not its deployment topology.

The highest-value path is:

1. finish effect receipts and unknown-outcome reconciliation;
2. unify durable run events behind one append-only envelope;
3. add explicit per-task execution policies and stable attempt IDs;
4. pin runtime builds for long-lived runs;
5. validate releases by replaying stored histories;
6. extend those contracts to generic continuous flows only after they are proven
   in WorkflowRunStore and FlowRunStore.

This path improves crash safety, replayability, and operations while preserving
PawFlow's existing strengths: visual flows, FlowFiles, scoped resources,
Workflow Agents, relay execution, durable interactions, multimodal tools, and
self-hosted control.

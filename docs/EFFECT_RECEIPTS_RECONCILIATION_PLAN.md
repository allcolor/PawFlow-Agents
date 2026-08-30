# Effect Receipts and Reconciliation Implementation Plan

Status: planned
Priority: platform P0 after the multi-conversation tiled workspace
Date: 2026-08-30
Owner: PawFlow core runtime

## 1. Outcome

Add a durable, verifiable receipt for every externally observable effect without
creating a second run journal. A Workflow Agent must be able to prove whether an
authorized effect was prepared, attempted, accepted by a provider, observed, or
left with an unknown outcome. Recovery must reconcile unknown outcomes before
any retry.

The authoritative store remains WorkflowRunStore for Workflow Agents and
FlowRunStore for declarative one-shot flows. Transcript events, tool lifecycle
events, authorization logs, and UI cards are projections of that state.

## 2. Verified baseline

PawFlow already provides:

- CapabilityEffect and IdempotencyClass contracts in core/agent_contracts.py;
- fail-closed workflow task validation in core/workflow_task_safety.py;
- stable run, generation, task, FlowFile, authorization, and tool-call identities;
- transactional WorkflowRunStore state, step cache, journal, and terminal outbox;
- declarative FlowRunStore lifecycle and terminal outbox;
- ToolLifecycleEvent publication;
- JSONL authorization decision and outcome records;
- provider-specific idempotency keys for selected tasks.

The missing contract is proof of an effect after authorization. The current
authorization JSONL is not transactional with a WorkflowRun, does not model an
unknown outcome, and cannot drive recovery.

## 3. Non-goals

- Do not replace WorkflowRunStore, FlowRunStore, or ConversationStore.
- Do not claim exactly-once delivery for an arbitrary external provider.
- Do not retry an effect merely because its HTTP response was lost.
- Do not store secrets, request bodies, provider tokens, or unrestricted output.
- Do not make transcript messages authoritative.
- Do not add a generic distributed transaction coordinator.

## 4. Invariants

1. Every receipt has a UUID and UTC creation timestamp.
2. One prepared effect has one stable receipt identity across retries and restarts.
3. The operation digest is calculated from canonical, redacted, authorization-
   relevant input before execution.
4. The exact capability, idempotency class, service revision, authorization
   reference, run generation, task, FlowFile, and tool call are frozen.
5. prepared does not prove execution.
6. executed means the local adapter completed the call, not that the remote effect
   is externally visible.
7. declared means the provider returned a stable reference or acknowledgement.
8. verified requires independent provider or destination observation.
9. unknown is an explicit non-terminal recovery state.
10. An unknown keyed, unsafe, or destructive effect is reconciled before retry.
11. Receipt rows are append-only events plus a CAS-controlled current projection.
12. Existing terminal outboxes remain terminal-delivery mechanisms, not effect
    receipts.
13. Redaction occurs before persistence and before UI publication.
14. Force stop terminates execution immediately; reconciliation continues
    asynchronously and is never reported as a new agent error.

## 5. Versioned contracts

### 5.1 EffectReceiptV1

Required identity and causality:

- schema_version;
- receipt_id;
- created_at and updated_at;
- user_id and conversation_id;
- run_kind: workflow_agent, declarative_flow, tool_call, or system;
- run_id and generation;
- flow_version_digest;
- task_id, task_type, flowfile_uuid, and input_hash when applicable;
- tool_call_id and lifecycle_event_id when applicable;
- attempt and effect_sequence.

Required authorization and target data:

- effects as normalized CapabilityEffect values;
- idempotency_class;
- authorization_ref and authorization_digest;
- operation_name and operation_digest;
- target_kind and target_digest;
- service_id, service_scope, service_revision, and service_digest;
- provider_name and adapter_version.

Required state and proof data:

- state;
- provider_reference;
- provider_idempotency_key_digest;
- request_started_at, response_received_at, declared_at, verified_at;
- result_digest and bounded result summary;
- evidence entries;
- verification_method and verification_status;
- safe_retry;
- retry_after;
- failure_class and redacted failure summary;
- superseded_by_receipt_id.

Raw secret values, unrestricted request bodies, filesystem contents, media bytes,
and provider credentials are forbidden.

### 5.2 EffectEvidenceV1

An evidence entry contains:

- evidence_id, kind, observed_at, and observer;
- stable provider or destination reference;
- digest, size, status, and bounded metadata;
- FileStore reference for a larger signed report when required;
- confidence: authoritative, corroborated, or advisory.

Advisory LLM assessment can enrich a receipt but can never establish verified.

### 5.3 ReconciliationDecisionV1

A reconciliation decision contains the receipt ID, observation digest, previous
and next states, decision code, safe-retry decision, actor, timestamps, and any
scheduled successor attempt. Decisions are append-only.

## 6. State machine

Allowed forward transitions:

    prepared -> executing
    executing -> executed
    executing -> unknown
    executed -> declared
    executed -> verified
    executed -> unknown
    declared -> verified
    declared -> rejected
    declared -> unknown
    unknown -> declared
    unknown -> verified
    unknown -> rejected
    unknown -> not_found_safe_to_retry
    not_found_safe_to_retry -> superseded

Terminal states are verified, rejected, cancelled_before_execution, and
superseded. A dead-letter record does not change an unresolved effect into a
successful or failed effect; it records that automated reconciliation exhausted
its bounded policy.

Illegal transitions fail closed and retain the earlier valid state.

## 7. Storage ownership

Extend WorkflowRunStore with:

- workflow_effect_receipts: immutable identity and current projection;
- workflow_effect_receipt_events: append-only transition journal;
- workflow_effect_reconciliation_queue: due work, lease, attempts, and backoff;
- workflow_effect_dead_letters: unresolved terminal operational records.

Extend FlowRunStore with the same contract through a shared storage mixin or
small common repository. Do not duplicate receipt state into ConversationStore.

Use SQLite transactions and compare-and-swap generation checks. Receipt creation
must occur in the same transaction as the prepared run step whenever the effect
is owned by a WorkflowRun. Provider reference persistence must commit before a
long wait begins.

The existing tool authorization JSONL remains a security audit projection during
migration. New records include receipt_id; it is not queried for recovery.

## 8. Runtime integration

### 8.1 Preparation

workflow_task_safety and tool authorization create or resolve the stable receipt
before dispatch. They freeze authorization, target, service, and operation
digests. A repeated preparation with the same run/task/input/effect sequence
returns the same receipt.

### 8.2 Execution adapters

Effectful adapters receive an EffectExecutionContext containing the receipt ID
and stable idempotency key. They report transitions through a narrow repository
API; they never write SQL directly.

Provider adapters implement optional capabilities:

- submit with provider reference;
- query by provider reference;
- query by idempotency key;
- verify destination state;
- compensate or revoke when explicitly supported.

Unsupported verification is declared, not guessed.

### 8.3 Tool lifecycle

ToolLifecycleEvent gains receipt_id, effect_state, operation_digest, and safe_retry
projection fields. Existing event IDs and causal identity remain unchanged.

### 8.4 Workflow recovery

Startup and checkpoint recovery inspect unresolved receipts before re-entering an
effectful task:

- verified returns the stored result projection;
- declared retrieves or verifies;
- unknown schedules reconciliation and pauses the task;
- not_found_safe_to_retry creates one successor attempt;
- rejected fails with a typed terminal result;
- unresolved dead-lettered work remains operator-visible and never auto-retries.

## 9. Reconciler

Add an asynchronous bounded EffectReconciler owned by the runtime, not by an
agent loop. It uses leased queue rows, exponential backoff with jitter, provider
rate limits, and a hard attempt/age budget.

Decision order:

1. query the exact provider reference;
2. query the stable idempotency key when supported;
3. verify the destination state;
4. classify authoritative not-found;
5. retry only when the idempotency contract and observation prove safety;
6. otherwise retain unknown and eventually dead-letter.

A network timeout during reconciliation keeps unknown. Absence of evidence is not
evidence of absence.

## 10. API, UI, and operations

Expose scoped read actions for receipt summary, receipt detail, evidence, and
dead letters. Mutation actions are limited to reconcile-now, acknowledge, and
explicit reviewed retry; all require authenticated actor identity and expected
generation.

Workflow timeline and Kanban project receipt states rather than duplicating them.
Cards show effect, target summary, provider reference, state, age, last
observation, safe-retry reason, and next action. Secret fields never reach the
browser.

Operational metrics:

- receipts by state and effect;
- unknown age and reconciliation latency;
- retry prevented, retry proven safe, and duplicate prevented counters;
- dead-letter backlog;
- provider verification error rate.

## 11. Security and privacy

- Hash canonical sensitive targets with a scoped server key when plain identifiers
  are not required for reconciliation.
- Store only allowlisted provider metadata.
- Apply existing user/conversation ACLs to every query.
- Require the original or stronger authorization ceiling for reviewed retry.
- Reject receipt imports and cross-run attachment.
- Bound evidence count, metadata size, and FileStore report size.
- Preserve audit rows under the configured retention policy.
- Never let provider text alter state without adapter validation.

## 12. Migration

This project uses a one-shot migration:

1. add receipt tables and indexes;
2. deploy read/write support behind an internal compatibility gate;
3. emit receipts for Workflow Agent tasks and direct tool calls;
4. link new authorization audit rows to receipts;
5. enable recovery reads;
6. enable the reconciler;
7. remove compatibility branches after one release boundary.

Historical authorization rows are not fabricated into verified receipts. They
remain legacy audit data.

## 13. Work packages

### WP0 — Contracts and red tests

Add pure contracts, transition validation, canonical digest helpers, and tests
showing that current recovery cannot classify a lost response.

### WP1 — Transactional repository

Add schemas, CAS transitions, indexes, cleanup, retention, and transaction tests
for both run stores.

### WP2 — Authorization and lifecycle integration

Create receipts before dispatch and project receipt identity into authorization
and ToolLifecycleEvent records.

### WP3 — Adapter protocol

Add EffectExecutionContext and verification capability interfaces. Port a small
representative set: FileStore write, HTTP mutation, email, and one provider job.

### WP4 — Recovery and reconciler

Add leased asynchronous reconciliation, backoff, dead letters, restart recovery,
and force-stop behavior.

### WP5 — Workflow Agent integration

Pause and resume exact task/FlowFile checkpoints based on receipt state. Preserve
run ID, generation, and idempotency keys.

### WP6 — APIs and UI

Add scoped actions, timeline/Kanban projections, operator filters, and accessible
state explanations.

### WP7 — Migration and observability

Enable staged production metrics, compatibility removal, runbooks, and alerts.

### WP8 — Documentation and delivery

Update agent, workflow, security, operations, task/service reference, and package
author documentation. Run focused and full CI gates.

## 14. Test matrix

Required tests include:

1. stable receipt reuse after process restart;
2. crash before dispatch remains prepared;
3. crash after provider acceptance persists unknown;
4. provider reference saved before wait;
5. authoritative provider success becomes verified;
6. provider not-found plus keyed idempotency permits one successor;
7. unsafe unknown never retries automatically;
8. timeout during reconciliation remains unknown;
9. duplicate reconciliation workers respect leases and CAS;
10. stale generation cannot mutate a receipt;
11. force stop does not mark the effect failed;
12. dead-letter preserves the unresolved receipt;
13. authorization and service revisions are frozen;
14. result/evidence size and redaction limits hold;
15. cross-user and cross-conversation reads fail;
16. transcript deletion does not erase authoritative run receipts;
17. terminal outbox behavior is unchanged;
18. migration is idempotent and historical rows are not misclassified;
19. chaos tests cover kill points around every transition;
20. full Python 3.10–3.13 CI remains green.

## 15. Definition of done

The feature is complete only when an operator can answer, from one authoritative
run record, what effect was authorized, what was attempted, what the provider
declared, what PawFlow independently verified, and why retry is or is not safe.
No unknown destructive effect can be automatically repeated.

## 16. Upstream influence and license ledger

The prepared/executed/declared/verified distinction and crash/replay test ideas
were evaluated from rlaope/oh-my-hermes at SHA
91aa8a1 under MIT. Any copied contract or test fragment requires attribution.

Salomondiei08/oh-my-hermes had no declared license at the reviewed revision. Its
dead-letter/product-loop ideas may inform clean-room design only; no source,
tests, text, or assets may be copied.

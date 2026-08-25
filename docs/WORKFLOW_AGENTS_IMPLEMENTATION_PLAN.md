# Workflow-Driven Dedicated Agents — Complete Implementation Plan

Status: **implementation in progress; WP0-WP3 experimental vertical slice landed locally on 2026-08-24**

WP0 implementation note: the immutable schemas live in
`core/workflow_agent_contracts.py` and share identity, effect, authorization,
resource, lifecycle, run, and group contracts with the collaboration plan. All
new server-owned feature flags default to false.

WP1 implementation note: `core/agent_runtime_router.py` now resolves the
canonical conversation roster entry and owns a workflow-only adapter registry.
WP2 implementation note: exact scoped flow resolution, digest-pinned
`ResourceRef` bindings, typed runtime defaults, agent-workflow publish/bind
validation, referenced-version protection, and PFP flow-object dependencies are
implemented in `core/workflow_agent_resources.py` and the existing repository,
authoring, resource-action, import, and package boundaries.
WP3 implementation note: `core/workflow_agent_runtime.py` supplies the
process-resident queue-only adapter, isolated exact-version batch execution,
generation-safe coordinator, progress events, and single-terminal transcript
commit. The bootstrap-only tasks live under `tasks/ai/workflow`; the
`pawflow.agents.demo:1.0.0` first-party flow exercises two deterministic
stages and a deterministic fake-LLM stage. Reserved run attributes are
reasserted by the executor at every task boundary.
The server capability is read only from
`PAWFLOW_WORKFLOW_AGENTS_ENABLED` with strict boolean parsing. When disabled,
all create/update/start paths reject workflow. When enabled, only the explicit
WP3 bootstrap catalog and `queue` preemption execute; other tasks and policies
fail closed. The existing three runtimes retain their previous direct paths.

Date: 2026-08-23

Primary outcome: PawFlow can run dedicated agents whose behavior is orchestrated by a versioned flow containing deterministic tasks, branches, joins, tools, and multiple LLM steps. The LLM is a processor inside the workflow, not the component that decides the whole execution plan.

Reference implementation: a Wiki Agent that discovers relevant project sources, normalizes and batches them, extracts and reviews durable knowledge with one or more LLM services, applies only validated source-backed wiki changes, and reports exactly what it changed.

## 1. Decision

Add a fourth agent runtime kind named workflow alongside the existing llm, external_mcp, and external_agui kinds.

A workflow agent:

- is a normal conversation member and uses the existing AgentRuntimeAPI, conversation transcript, SSE event bus, selected-agent routing, transport bridges, and force-stop surface;
- is bound to an exact, immutable flow version;
- has no primary llm_service requirement;
- may use zero, one, or many LLM services through explicit workflow nodes;
- runs one isolated workflow execution per accepted turn;
- receives new user messages through explicit preemption checkpoints;
- exposes progress events but commits only one final assistant response;
- cannot delegate orchestration implicitly to an LLM unless its authored flow explicitly contains an agent-loop node in a future, separately reviewed capability.

This is additive. Existing general-purpose LLM agents keep runtime_kind equal to llm and retain their current behavior.

### 1.1 Additive compatibility boundary

Workflow agents are an opt-in runtime capability. Merely installing a release that contains the new code must not route an existing `llm`, `external_mcp`, or `external_agui` instance through a new execution path.

The first implementation therefore uses an explicit `workflow_agents_enabled` server capability and a narrow dispatch branch after the canonical conversation agent has been resolved:

- when the capability is disabled, `runtime_kind: workflow` cannot be created, imported, bound, or started;
- when the resolved runtime kind is not `workflow`, the currently shipped runtime entry point, acknowledgement, queue, preemption, finalization, and error behavior remain the execution path;
- no existing conversation or agent is rewritten merely because the capability is installed;
- new response and SSE fields are optional additions; existing field meanings and event ordering do not change;
- storage migrations have a separate preflight and activation marker and are never triggered by viewing or enabling the UI;
- the generic router may absorb existing runtimes only after characterization tests prove behavioral equivalence for each runtime separately.

This boundary is a release invariant, not a temporary implementation convenience. A shared abstraction is accepted only when its old-runtime adapter is observably equivalent.

### 1.2 Shared contracts with the collaboration and tool-safety plan

`AGENT_COLLABORATION_AND_TOOL_SAFETY_PLAN.md` owns the cross-runtime identity, effect, tool lifecycle, run projection, and immutable resource contracts. This plan owns workflow execution, inbox leasing, workflow recovery, and workflow terminal commit.

The two plans use one set of primitives:

- workflow `root_turn_id` is the serialized workflow field for `AgentTurnIdentity.turn_id`; it is not a second identifier;
- `run_generation` is the shared generation field; workflow code must not create a separate generation counter;
- the existing `AuthorizationRef` (`context_id`, `revision`, `root_turn_id`) remains the source of truth for user-authority lineage; a workflow snapshots the exact ref at each side-effect boundary;
- exact flow, agent, skill, and group bindings use the shared `ResourceRef` and content digest;
- task and tool declarations use the shared `CapabilityEffect` taxonomy and `IdempotencyClass` enum;
- workflow effects pass through the existing `core/tool_authorization.py` pipeline and the extended approval service; they do not implement a second authorization engine;
- workflow tool/effect events use the shared lifecycle envelope when they represent an agent-visible tool action, while ordinary deterministic workflow-step events remain workflow events;
- `AgentRunRegistry` projects `WorkflowRunStore`; it never copies workflow run state.

If the two plans disagree on one of those cross-cutting types, the shared definition in the collaboration and tool-safety plan wins and this workflow plan must be updated in the same change.

## 2. Product model

PawFlow will support two complementary local agent styles.

| Style | Orchestrator | Best suited to |
|---|---|---|
| General-purpose LLM agent | The model chooses tools and iterations inside AgentLoopTask | Open-ended assistance, coding, research, exploration |
| Dedicated workflow agent | A versioned PawFlow flow chooses the stages; LLM calls are bounded processors | Wiki maintenance, intake, review, document processing, compliance checks, release preparation |

The user still talks to both through the same chat and transport APIs. The difference is operational, visible in the agent configuration and run inspector.

A dedicated agent is not a permanently deployed listener flow. It is a reusable agent definition plus an exact workflow binding. Each turn creates an isolated run from that definition.

## 3. Goals

1. Let an agent turn execute an authored multi-stage flow with multiple LLM calls.
2. Keep deterministic work outside the LLM: discovery, filtering, normalization, validation, persistence, retries, and reporting.
3. Preserve PawFlow conversation semantics: one canonical transcript, UUID and timestamp on every message, selectedAgent never empty, and one correlated terminal result.
4. Allow workflow authors to place explicit checkpoints that retrieve messages arriving while the run is active.
5. Provide crash-safe message delivery, one idempotently persisted terminal assistant message, and replay-safe terminal event delivery.
6. Pin every run to an immutable flow version and record the version in provenance.
7. Enforce user, conversation, relay, service, tool, and policy-gate scopes for every step.
8. Aggregate cost, token, duration, fan-out, and task metrics for the whole turn.
9. Make dedicated agents installable as PFP packages containing their definition, workflow, optional tasks, prompts, and UI metadata.
10. Use the Wiki Agent as the first production migration and reference package.

## 4. Non-goals

- Replacing AgentLoopTask for general-purpose agents.
- Turning every ordinary batch flow into an agent.
- Letting arbitrary deployed listeners, cron sources, or HTTP receivers run inside one conversational turn.
- Persisting every intermediate LLM response as a chat message.
- Allowing source text or a preempt message to mutate authorization or service bindings.
- Providing arbitrary Python or shell execution in conversation-invoked workflows in the first release.
- Resuming in the middle of a non-idempotent task.
- Auto-upgrading a bound agent when a new flow version is published.
- Using publishMessage as the workflow terminal.
- Treating an LLM verdict as sufficient validation for filesystem, wiki, security, or other durable mutations.

## 5. Current PawFlow foundation

The implementation should extend existing seams instead of creating a parallel product.

### 5.1 Agent ingress and result correlation

The default pawflow_agent flow already routes POST /api/agent into AgentLoopTask and publishes conversation events through agentSSEStream. AgentRuntimeAPI normalizes non-HTTP transports into the same FlowFile shape and AgentResultWaiter correlates done or error_event by conversation_id and turn_id.

AgentLoopTask already provides:

- immediate HTTP acknowledgement and background execution;
- conversation and agent selection;
- pre-persistence of incoming user messages;
- active-turn generation tracking;
- provider abort and CLI preemption;
- force-stop handling;
- pending-message draining;
- canonical final assistant persistence;
- SSE lifecycle events.

The workflow runtime must reuse these contracts.

### 5.2 Flow engine

ContinuousFlowExecutor already provides:

- task queues and relationship routing;
- fan-out and merge patterns;
- backpressure;
- bounded parallel task instances;
- failure relationships;
- output collection;
- runtime context injection;
- parameters and service injection;
- provenance hooks;
- subflows through ExecuteFlowTask;
- flow checkpoints for deployed continuous executors.

Its run_batch helper is useful for the first vertical slice, but it disables checkpoints and is not sufficient for the final durable runtime.

### 5.3 LLM task

InferLLMTask already proves that an LLM can be used as a stateless dataflow processor. It accepts one system prompt plus one user payload and returns text or JSON with model and token attributes.

It is suitable for a prototype. The production workflow runtime needs a richer agentLLMCall task with structured messages, runtime cancellation, idempotence, progress, scoped service resolution, and usage-ledger integration.

### 5.4 Pending messages

PendingQueue is disk-backed per conversation and agent, and existing ingress paths persist before enqueueing. Its current drain operation atomically reads and truncates the whole queue. That is safe for the current in-memory agent loop but cannot support workflow claims, leases, retries, or crash recovery after a checkpoint.

### 5.5 Project Wiki

ProjectWiki and ProjectMaintenanceScheduler already implement the Wiki Agent's essential safety rules:

- relay-scoped source hashing;
- high-signal initial seeding;
- dirty-source tracking;
- bounded source batches;
- untrusted-source prompt boundaries;
- structured JSON output;
- exact source citations;
- hash comparison before commit;
- superseded-run rejection;
- fail-closed malformed output;
- atomic page writes;
- source acknowledgement only after valid application;
- wiki lint and status reporting.

The reference workflow must preserve these guarantees. The migration is an orchestration refactor, not a weakening or rewrite of the storage model.

## 6. Architectural invariants

The following are release blockers.

1. Ingress persists each accepted user message exactly once before routing it to a runtime.
2. One active run exists per canonical conversation and agent pair.
3. Every run has immutable run_id, `AgentTurnIdentity`, exact `AuthorizationRef`, flow `ResourceRef`, principal, conversation, and canonical agent identity.
4. A stale generation cannot commit a message, emit a terminal event, acknowledge inbox entries, or clean up its successor.
5. The workflow never writes directly to the canonical transcript.
6. Only WorkflowTurnCoordinator may commit the final assistant row and terminal done event.
7. A successful run produces exactly one valid terminal result. Zero or multiple terminal results are execution errors.
8. Progress and intermediate LLM output are events or run artifacts, never assistant transcript rows.
9. Every preempt message remains durable until a successful terminal commit acknowledges it or an explicit force-stop policy discards it.
10. A workflow run uses one exact flow version for its entire lifetime.
11. New flow versions affect only explicitly upgraded future turns.
12. Task retries never repeat a non-idempotent external effect without an idempotency key.
13. Force stop is an immediate cancellation, not an error, and cannot affect the next generation.
14. User content, project files, existing wiki pages, and intermediate LLM output are untrusted data.
15. Workflow task capabilities can only narrow the conversation and agent permission context.
16. The final answer describes committed work, not merely intended work.
17. An automatic silent invocation and a conversational invocation may share a workflow, but only the conversational mode may write a chat response.

## 7. Target architecture

~~~text
HTTP / Telegram / Google Chat / A2A / flow runtime
                         |
                         v
                 AgentRuntimeAPI
                         |
                         v
                AgentTurnIngress
       auth + hook + stamp + persist + enqueue
                         |
                         v
                AgentRuntimeRouter
          +--------------+--------------+----------------+
          |              |              |                |
          v              v              v                v
      LLM loop      Workflow runtime  External MCP   External AG-UI
          |              |
          |              v
          |      WorkflowTurnCoordinator
          |       - run store
          |       - generation/cancel
          |       - inbox claims
          |       - budgets
          |       - event sink
          |              |
          |              v
          |      AgentWorkflowExecutor
          |       - exact flow version
          |       - isolated task graph
          |       - task capability gate
          |       - idempotency cache
          |              |
          +--------------+-------------------------------+
                         |
                         v
              AgentTurnFinalizer
        CAS commit assistant row + done/error_event
~~~

### 7.1 Compatibility seam

The existing agentRuntime port and AgentLoopTask live instance remain the public submission seam during the first phases. The initial change adds a narrow workflow dispatch after authentication and canonical agent resolution. The `llm`, `external_mcp`, and `external_agui` branches continue to call their currently shipped implementations directly; they are not forced through rewritten ingress, queue, or finalization code merely to enable workflow agents.

AgentTurnIngress and AgentRuntimeRouter are extracted incrementally only after baseline fixtures prove that the extraction preserves persistence, acknowledgement, waiter registration, preemption, event order, errors, and force-stop behavior. Once all local runtimes pass those gates, the live port may target a thin AgentRuntimeTask. This rename is not required for the first release and must not break AgentRuntimeAPI clients.

### 7.2 Runtime adapters

Introduce an internal AgentRuntimeAdapter protocol:

~~~python
class AgentRuntimeAdapter(Protocol):
    runtime_kind: str

    def submit(self, request: PreparedAgentTurn) -> AgentSubmission:
        ...

    def cancel(self, key: AgentRunKey, reason: str, force: bool) -> bool:
        ...

class RecoverableAgentRuntimeAdapter(AgentRuntimeAdapter, Protocol):
    def recover(self, run_id: str) -> RecoveryResult:
        ...
~~~

Adapters may exist for llm, workflow, external_mcp, and external_agui as their parity gates pass. Recovery is a separate capability rather than a method every runtime must pretend to support; the adapter loads and validates its runtime-owned authoritative record from `run_id`. The initial recoverable adapter is workflow and loads `WorkflowRunRecord` from `WorkflowRunStore`. Routing uses the resolved conversation agent instance, never client-supplied runtime fields.

## 8. Resource model

### 8.1 Reusable agent definition

Repository agent definitions may declare optional runtime defaults. Existing prompt-only definitions remain valid.

~~~json
{
  "name": "wiki-agent",
  "description": "Maintains the linked relay project's sourced wiki",
  "prompt": "",
  "parameters": {
    "project_root": {
      "type": "string",
      "default": "."
    }
  },
  "runtime_defaults": {
    "kind": "workflow",
    "workflow": {
      "flow_fqn": "pawflow.agents.wiki:1.0.0",
      "input_port": "agent_request",
      "terminal_port": "agent_response",
      "preempt_policy": "checkpoint",
      "parameters": {
        "project_root": "."
      }
    }
  }
}
~~~

The prompt is optional for workflow definitions. When present, it is exposed to explicitly configured LLM tasks as workflow.agent_prompt; it does not become a hidden global orchestrator prompt.

The resource create/update filters must retain and validate runtime_defaults instead of limiting agent definitions to prompt and description.

### 8.2 Conversation agent instance

Resolved runtime configuration remains stored in conv_agents. For a workflow instance:

~~~json
{
  "definition": "wiki-agent",
  "params": {
    "project_root": "."
  },
  "runtime_kind": "workflow",
  "workflow": {
    "flow_fqn": "pawflow.agents.wiki:1.0.0",
    "flow_scope": "global",
    "input_port": "agent_request",
    "terminal_port": "agent_response",
    "preempt_policy": "checkpoint",
    "parameters": {
      "extractor_llm": "wiki_fast_llm",
      "writer_llm": "wiki_writer_llm",
      "reviewer_llm": "wiki_review_llm",
      "project_root": "."
    },
    "limits": {
      "max_duration_seconds": 900,
      "max_llm_calls": 24,
      "max_flowfiles": 200,
      "max_fanout": 16,
      "max_cost_usd": 2.0
    }
  }
}
~~~

Rules:

- `llm_service` is required only for `runtime_kind: llm`; workflow create/update payloads omit it rather than using an empty value as a fallback.
- workflow is required only for runtime_kind equal to workflow.
- flow_fqn must include an exact version.
- flow_scope is resolved and stored when the instance is created or upgraded.
- the binding is validated against the requesting user and conversation.
- workflow parameters are validated against the flow's agent_contract schema.
- services referenced by parameters are resolved at run start and snapshotted.
- a definition's defaults are copied into the instance; later definition edits do not silently mutate existing conversation instances.
- every runtime-kind allowlist and validator must change in the same gated work package, including `core/conv_agent_config.py`, create/update action handlers, imports, PFP validation, UI forms, and runtime dispatch. A partially updated validator set is a release blocker.

### 8.3 Immutable flow reference

A workflow agent may bind global, user, or conversation-scoped flows according to existing repository visibility. Resolution returns:

- exact FQN;
- repository scope;
- owner;
- conversation scope when applicable;
- content digest;
- package identity and trust metadata.

The run stores one shared `ResourceRef` containing these facts. The resolver must never use latest after accepting a turn. PawFlow flow FQNs retain their existing canonical `package.flow:version` form.

Deleting a referenced flow version is refused while live agent instances or recoverable runs reference it. The UI must offer an explicit upgrade operation that validates a new version before changing the instance.

### 8.4 Flow contract

An agent workflow declares kind and contract metadata:

~~~json
{
  "id": "wiki-agent",
  "name": "Wiki Agent",
  "version": "1.0.0",
  "kind": "agent_workflow",
  "agent_contract": {
    "version": 1,
    "input": {
      "port": "agent_request"
    },
    "terminal": {
      "port": "agent_response"
    },
    "parameters": {
      "extractor_llm": {
        "type": "service_ref",
        "capability": "llm",
        "required": true
      },
      "writer_llm": {
        "type": "service_ref",
        "capability": "llm",
        "required": true
      },
      "reviewer_llm": {
        "type": "service_ref",
        "capability": "llm",
        "required": false
      },
      "project_root": {
        "type": "string",
        "default": "."
      }
    },
    "supported_preempt_policies": [
      "checkpoint",
      "queue",
      "restart"
    ]
  }
}
~~~

Validation requires:

- exactly one declared input port;
- exactly one declared terminal port;
- the port task types match the contract;
- every non-terminal path either reaches the terminal, reaches a typed handled-failure terminal, or has an explicit bounded stop;
- no persistent source tasks;
- no unbounded cycles;
- no nested call to the same agent workflow;
- subflow depth at most the existing platform limit;
- every task type is workflow-agent safe;
- every service reference is explicit or parameterized;
- all fan-out and payload limits are declared;
- version and FQN metadata are consistent.

## 9. Turn and run contracts

### 9.1 PreparedAgentTurn

AgentTurnIngress produces a server-owned object:

~~~json
{
  "conversation_id": "conv-id",
  "agent_name": "Wiki",
  "user_id": "alice",
  "root_turn_id": "web:uuid",
  "request_message_ids": [
    "web:uuid"
  ],
  "channel": "web",
  "message": "Refresh the wiki and focus on authentication changes",
  "attachments": [],
  "source": {
    "type": "user",
    "name": "alice"
  },
  "permission_mode": "default",
  "authorization_ref": {
    "context_id": "auth-context-uuid",
    "revision": 7,
    "root_turn_id": "web:uuid"
  }
}
~~~

`PreparedAgentTurn` also carries the immutable shared `AgentTurnIdentity`. The flat `root_turn_id` remains only as the workflow-schema projection of `turn_identity.turn_id` for existing transport correlation; implementations assert equality at construction and reject a mismatch.

Identity, permission mode, runtime kind, flow selection, authorization reference, and service bindings cannot be overridden by request content or FlowFile attributes.

### 9.2 WorkflowRunContext

The executor receives an immutable runtime context separate from mutable FlowFile content and attributes:

~~~json
{
  "run_id": "wr_uuid",
  "conversation_id": "conv-id",
  "agent_name": "Wiki",
  "user_id": "alice",
  "root_turn_id": "web:uuid",
  "run_generation": 4,
  "flow_fqn": "pawflow.agents.wiki:1.0.0",
  "flow_digest": "sha256",
  "channel": "web",
  "invocation_mode": "conversation",
  "permission_mode": "default",
  "authorization_ref": {
    "context_id": "auth-context-uuid",
    "revision": 7,
    "root_turn_id": "web:uuid"
  },
  "deadline_at": 0,
  "limits": {},
  "service_snapshot": {},
  "cancel_token": "internal",
  "event_sink": "internal"
}
~~~

`run_generation` is the canonical shared field. The existing LLM runtime may continue to project it into its process-resident `_conv_generation` and `_generation` fields until its adapter passes parity; workflow code must not replace or increment those legacy maps. The reserved `workflow.generation` FlowFile attribute is only a compatibility projection of `run_generation`. Tasks cannot replace the context object. Agent-aware tasks receive it through `set_workflow_run_context`. Subflows inherit the same object and add only a bounded subflow stack. Before any side effect, the executor loads the newest valid revision of the same authorization context and records the exact ref used for that task.

### 9.3 Input FlowFile

The input content is a JSON AgentWorkflowRequest:

~~~json
{
  "schema_version": 1,
  "request": {
    "message": "Refresh the wiki and focus on authentication changes",
    "attachments": []
  },
  "conversation": {
    "id": "conv-id",
    "agent": "Wiki"
  },
  "turn": {
    "root_turn_id": "web:uuid",
    "request_message_ids": [
      "web:uuid"
    ]
  },
  "parameters": {
    "project_root": "."
  }
}
~~~

Reserved attributes include workflow.run_id, workflow.generation, workflow.flow_fqn, workflow.root_turn_id, workflow.principal, workflow.authorization_context_id, and workflow.authorization_revision. The executor reasserts them at every task boundary. Authorization never depends solely on mutable attributes.

### 9.4 Terminal result

The terminal task returns an AgentWorkflowResult:

~~~json
{
  "schema_version": 1,
  "status": "completed",
  "response": "Updated 3 wiki pages from 7 changed source files. Two files remained pending because they changed during review.",
  "artifacts": [
    {
      "kind": "wiki_page",
      "id": "authentication-flow",
      "label": "Authentication Flow"
    }
  ],
  "metrics": {
    "sources_discovered": 1432,
    "sources_selected": 8,
    "sources_processed": 7,
    "pages_created": 1,
    "pages_updated": 2,
    "sources_superseded": 1
  },
  "answered_turn_ids": [
    "web:uuid",
    "web:preempt-uuid"
  ]
}
~~~

Allowed successful statuses are completed and no_change. Cancelled, failed, timed_out, superseded, and budget_exceeded are coordinator outcomes, not fabricated successful terminal responses.

The coordinator validates length, UTF-8, artifact structure, answered IDs, and status before finalization.

## 10. Workflow execution lifecycle

1. AgentRuntimeAPI submits the request through the existing live runtime port.
2. AgentTurnIngress authenticates, runs pre-user hooks, stamps the user message, creates a durable ingress receipt, persists it idempotently, and resolves the canonical agent.
3. AgentRuntimeRouter resolves runtime_kind from conv_agents.
4. WorkflowAgentRuntime validates the exact workflow binding.
5. It acquires the active-run lease for conversation plus agent.
6. If no run is active, it creates a WorkflowRunRecord and generation.
7. It returns the same immediate accepted acknowledgement used by the existing streaming runtime.
8. A background worker instantiates the pinned flow version and injects one AgentWorkflowRequest into its declared input port.
9. The executor publishes bounded lifecycle and progress events.
10. Checkpoint tasks may claim messages that arrived after the root request.
11. The workflow produces exactly one AgentWorkflowResult.
12. WorkflowTurnCoordinator atomically transitions the run from running to committing inside WorkflowRunStore.
13. It rechecks generation, the current AuthorizationRef, cancellation, terminal schema, and budget.
14. It commits one assistant message through the idempotent ConversationWriter operation described in section 13.
15. It acknowledges inbox messages listed in answered_turn_ids.
16. It publishes done with root turn ID, answered turn IDs, runtime_kind, run ID, flow FQN, finish reason, and response.
17. It marks the run completed and releases the active-run lease.
18. If unclaimed messages remain, it schedules the next turn without relying on a transcript-tail scan.

Failure projection preserves the currently documented transport contract and produces one logical terminal outcome. Event delivery itself is at-least-once: every terminal event has a stable event ID and clients/waiters deduplicate it. A failed run does not acknowledge pending messages unless a specific message was rejected permanently by policy.

## 11. Agent inbox and preemption

### 11.1 Storage decision

Introduce AgentInboxStore, backed by SQLite transactions. Workflow agents use it from their first durable release. Existing LLM callers remain on the current PendingQueue path until the compatibility facade passes the migration gates in section 24.3; enabling workflow agents alone does not migrate their queues.

After activation, the `PendingQueue` API remains as a behavior-preserving facade over AgentInboxStore until every internal caller has moved. There is never a period in which JSONL and SQLite are both writable sources of truth for the same queue.

Core columns:

| Column | Purpose |
|---|---|
| conversation_id | Canonical conversation |
| agent_key | Case-normalized target agent |
| msg_id | Globally unique stamped message ID |
| sequence | Stable enqueue order |
| payload_json | Stamped message, attachments, source and provenance |
| source | Diagnostic ingress source |
| state | pending, claimed, acknowledged, discarded |
| owner_run_id | Run that owns a claim |
| lease_expires_at | Crash recovery boundary |
| enqueued_at | Diagnostics and force-stop cutoff |
| updated_at | Recovery and audit |

Unique key: conversation_id, agent_key, msg_id.

The same database contains `agent_ingress_receipts`, keyed by conversation, canonical agent, and message ID. A receipt stores the stamped payload and moves through `prepared -> transcript_persisted -> queued`. It closes the otherwise unavoidable crash window between the JSONL transcript and SQLite inbox without pretending that the two stores share a transaction.

A one-shot migration imports every valid pending.jsonl row, deduplicates by msg_id, and renames the old file to a migration marker only after the transaction commits and counts/digests match. The compatibility PendingQueue facade is removed after all internal callers move to AgentInboxStore.

Conversation deletion and retention cleanup must remove matching inbox rows.

### 11.2 Ingress rule

Every ingress follows one sequence:

1. stamp message;
2. insert or load an idempotent `prepared` ingress receipt containing the exact stamped payload;
3. persist the complete stamped transcript row through `ConversationWriter.enqueue_message_if_absent(message, ...)`, keyed internally by its non-empty `msg_id`;
4. atomically mark the receipt `transcript_persisted` and create the pending inbox row;
5. mark the receipt `queued`;
6. wake or preempt the target runtime.

Boot recovery scans non-queued receipts. It repeats the idempotent transcript append when necessary, promotes the inbox row, and only then wakes the agent. A duplicate transport submission with the same message ID returns the existing receipt/result and never creates a second transcript or inbox row.

The inbox is work delivery. The transcript is history. Neither replaces the other.

### 11.3 receiveAgentMessages task

Add receiveAgentMessages with these parameters:

| Parameter | Meaning |
|---|---|
| max_messages | Bounded claim size, default 20 |
| wait_ms | Optional bounded wait, default 0 |
| sources | Optional source filter |
| output_attribute | Attribute containing a compact JSON claim descriptor |
| include_content | Whether the descriptor includes content or only message references |
| empty_relationship | Relationship used when no message is available |

Relationships:

- messages;
- empty;
- cancelled;
- failure.

The task claims messages transactionally for its run and emits a claim_id plus ordered message references. Re-executing the same task for the same run and task ID returns the same claim. It never persists conversation rows and never acknowledges the claim.

The run store retains full claimed payloads. FlowFile attributes carry only bounded descriptors and references, avoiding large duplicated content on every branch.

### 11.4 Claim completion

WorkflowTurnCoordinator acknowledges claims only after the final assistant commit succeeds. On failure or ordinary cancellation, claims are released. On restart preemption, they transfer to the successor generation. On force stop, only entries at or before the force-stop cutoff are discarded; later messages remain pending.

### 11.5 Policies

checkpoint is the default:

- an arriving message is persisted and enqueued;
- in-flight work continues until receiveAgentMessages;
- the workflow decides whether to merge, branch, or restart a logical stage;
- the final result lists every answered message ID.

queue:

- the active run never claims new messages;
- after finalization, remaining messages seed a new run.

restart:

- the active generation is cancelled;
- safe in-flight calls receive cancellation;
- claimed and pending messages are assigned to a fresh generation;
- completed idempotent step results may be reused when their input hash is unchanged.

live_inject is not supported by generic workflow agents in version 1. It remains a provider-specific LLM-loop behavior.

### 11.6 Final drain

Before terminal commit, the coordinator performs a non-destructive inbox check. If messages arrived after the last workflow checkpoint:

- checkpoint policy: do not claim them implicitly; finish the current answered set and schedule a new turn;
- queue policy: schedule a new turn;
- restart policy: cancel terminal commit and start a successor only if the configured finalization cutoff has not passed;
- force stop: follow cutoff semantics.

This avoids silently claiming messages the workflow never saw.

## 12. Dedicated LLM task

Add agentLLMCall rather than growing InferLLMTask into two incompatible roles.

### 12.1 Inputs

The task accepts:

- service: required scoped LLM service reference or workflow parameter;
- model: optional override;
- system_prompt: template treated as trusted flow configuration;
- messages: expression selecting a JSON message array;
- input: expression or FlowFile content fallback;
- response_format: text, json, or json_schema;
- json_schema: optional strict output schema;
- temperature and provider-neutral generation controls;
- output_target: content, attribute, or run artifact;
- progress_label;
- cache_policy: none or run_idempotent;
- timeout bounded by the run deadline;
- visibility: hidden or final_candidate.

### 12.2 Behavior

The task:

1. resolves the service through the run's immutable service snapshot;
2. verifies the service belongs to the permitted resource scope;
3. derives idempotency_key from run ID, task ID, normalized input hash, service snapshot, model, and prompt digest;
4. returns a stored successful result when run_idempotent is enabled;
5. checks run cancellation and budget before opening the provider request;
6. calls the provider with explicit ephemeral conversation scope;
7. records model, provider, tokens, cache tokens, cost, duration, finish reason, and task ID;
8. validates JSON or JSON Schema output before success routing;
9. stores the bounded result and updates aggregate run usage;
10. never writes to ConversationStore;
11. never publishes done;
12. cancels the provider request when the run token is cancelled.

JSON Schema output uses the same provider path as ordinary agent calls: tools
remain available through automatic selection and configured thinking remains
unchanged. The schema is included in the trusted prompt and enforced after the
response; workflow execution never converts it into a forced provider
`tool_choice`.

### 12.3 Retries

The generic executor's default three attempts are unsafe for charged LLM calls. Agent workflow execution uses one attempt unless the task declares run-idempotent caching. Provider retry policies remain inside the LLM service where transport semantics are known.

A failure relationship may route to an authored recovery stage. The same failed provider request is not automatically repeated by the generic task loop.

### 12.4 Intermediate streaming

Version 1 publishes stage progress and usage, not raw intermediate tokens. Raw token streaming from an extraction or review step could expose unvalidated text and mislead the user.

Conversation-invoked workflow runs participate in the same presence surfaces as
ordinary agent turns. `list_active` merges a bounded snapshot from
`WorkflowAgentRuntime`, while `workflow_progress` gives the browser an immediate
hint between polls. This keeps the Active Agents panel, stop control, working
indicator, and rotating typing words visible for the full run. Silent
maintenance remains excluded from conversation presence.

A later final_candidate mode may stream provisional tokens under a distinct workflow_preview event. It must never use the normal assistant token event until terminal validation succeeds.

## 13. Recoverable, idempotent finalization

Add completeAgentTurn as the only legal terminal task type for agent workflows.

The task validates and stages AgentWorkflowResult in WorkflowRunStore using compare-and-swap, then returns it to the executor. It does not append messages or emit done.

ConversationStore JSONL and WorkflowRunStore SQLite do not share a transaction. The coordinator therefore implements a recoverable saga and never claims distributed atomicity. `assistant_msg_id` and `terminal_event_id` are allocated and stored with the staged terminal payload before the first external write.

Reuse the existing `ConversationStore.append_message_if_absent` durable primitive. Add a FIFO `ConversationWriter.enqueue_message_if_absent` operation that invokes it on the writer thread, reports whether the row was newly appended, propagates write failure to the coordinator, and preserves the existing persisted-before-SSE ordering. WorkflowTurnCoordinator owns the irreversible terminal sequence:

1. CAS running to committing;
2. verify current generation;
3. verify exactly one staged terminal;
4. verify response and answered message IDs;
5. persist the stored stamped assistant payload idempotently by `assistant_msg_id`;
6. persist `message_committed` terminal metadata;
7. acknowledge only the claims named by the validated terminal result;
8. enqueue the stable terminal event in the run-store outbox;
9. publish the outbox event at least once and record delivery attempts;
10. CAS committing to completed after the durable message and acknowledgements are proven.

Recovery checks terminal commit metadata:

- if the assistant row exists but the outbox event is pending, publish the same stable event ID;
- if the stored terminal payload exists but the row does not, append it idempotently using the stored message ID;
- if inbox acknowledgement is incomplete, repeat the idempotent acknowledgement set;
- if completed, never commit again;
- if a stale generation reaches finalization, mark superseded and release its claims.

The observable guarantee is one persisted assistant message and one logical terminal event. Physical SSE delivery can repeat across disconnect or crash, so all consumers deduplicate `terminal_event_id`.

The final event retains current compatibility fields and adds:

~~~json
{
  "event_id": "workflow-terminal-uuid",
  "turn_id": "root-turn",
  "answered_turn_ids": [
    "root-turn",
    "preempt-turn"
  ],
  "run_id": "wr_uuid",
  "runtime_kind": "workflow",
  "flow_fqn": "pawflow.agents.wiki:1.0.0",
  "agent_name": "Wiki",
  "response": "...",
  "finish_reason": "workflow_complete"
}
~~~

AgentResultWaiter resolves the root turn as today. It also resolves registered aliases listed in answered_turn_ids so transports that elected to wait on a preempted request cannot hang.

## 14. Durable run store and recovery

Add WorkflowRunStore with SQLite transactional state.

### 14.1 Run record

Store:

- run identity and active-run key;
- conversation, agent, principal, channel;
- root and absorbed turn IDs;
- generation;
- exact flow identity, scope, owner, version, digest;
- invocation and permission modes;
- status and reason;
- timestamps and deadline;
- parameter and service snapshots with secrets redacted or referenced;
- exact AuthorizationRef snapshots and policy snapshot digests used by effects;
- claimed inbox IDs;
- per-step state and idempotent result references;
- aggregate usage and limits;
- staged terminal result;
- staged stamped assistant payload, committed assistant msg_id, and terminal event ID;
- terminal outbox and inbox-acknowledgement state;
- last emitted event sequence;
- recovery count.

### 14.2 States

~~~text
accepted -> running
accepted -> cancelled | superseded | failed
running  -> cancelling | superseded | committing | failed |
            timed_out | budget_exceeded | force_stopped
cancelling -> cancelled | force_stopped | failed
committing -> completed | superseded | recovery_failed
~~~

Only `committing` may become `completed`. Every terminal state is immutable. `AgentRunRegistry` maps workflow-specific terminal states to its normalized `completed`, `failed`, `cancelled`, or `superseded` view without rewriting the workflow record.

### 14.3 Recovery model

Initial durable release uses restart-from-input recovery plus per-step idempotency:

- on process start, find accepted, running, cancelling, and committing records;
- recover committing records first;
- expire inbox leases owned by missing or terminal workflow runs;
- reinstantiate the exact stored flow version;
- rebuild the input request from the run record;
- replay deterministic tasks;
- reuse cached successful external/LLM results when input hashes match;
- require mutation tasks to be idempotent;
- fail closed if the exact flow version or required service snapshot no longer resolves.

Native queue-position resume may be added later, but is not required to ship. Replaying a deterministic graph with idempotent boundaries is easier to audit than reconstructing arbitrary in-flight threads.

### 14.4 Side-effect idempotency

Agent-workflow-safe mutation tasks must accept an idempotency key and store their outcome. For the Wiki Agent, applyWikiPatch uses run ID plus source snapshot digest plus patch digest. Re-execution returns the already committed result.

## 15. Capability and security model

### 15.1 Task metadata

Extend task definitions with:

~~~python
AGENT_WORKFLOW_SAFE = True
EFFECTS = {
    "filesystem.read",
    "wiki.read"
}
IDEMPOTENCY = "pure"
~~~

`IDEMPOTENCY` uses the shared `IdempotencyClass`: `pure`, `natural`, `run_cached`, `keyed_effect`, or `unsafe`. Tool metadata and workflow task metadata use the same enum; a task selects only the class that matches its actual behavior.

The agent workflow validator refuses unsafe tasks. In version 1, executeScript, shell execution, HTTP listeners, cron triggers, arbitrary source tasks, deployment mutation, and unrestricted dynamic task providers are forbidden.

PFP tasks may participate only when their signed or locally trusted package metadata declares capabilities and their runtime handler uses the scoped PawFlow API.

### 15.2 Runtime capability gate

Before each task:

1. intersect task effects with the flow package declaration;
2. intersect with the agent definition and conversation instance policy;
3. apply permission_mode;
4. resolve the current exact `AuthorizationRef` and apply conversation and agent policy-gating bindings through `core/tool_authorization.py`;
5. apply any human approval requirement through the shared approval service;
6. authorize relay, FileStore, conversation, and service targets;
7. emit a redacted audit event tied to the task/effect identity;
8. execute only if all checks pass.

A workflow may narrow permissions per branch. It cannot widen them.

### 15.3 Authorization changes after preempt

A claimed preempt message advances the run's authorization context version. Before the next side-effecting task, the gate reevaluates policy against the root request plus all absorbed corrections. A message cannot directly set a permission mode, service ID, relay, project root, or flow parameter.

### 15.4 Untrusted content

LLM prompts must clearly separate trusted workflow instructions from untrusted user, source, and existing-page sections. Structured outputs are validated mechanically.

For the Wiki Agent:

- sources are read only through the selected relay container surface;
- local true is forbidden;
- paths are normalized relative to the configured project root;
- symlink and traversal behavior follows the relay filesystem boundary;
- generated citations must exist in the captured manifest;
- an LLM cannot acknowledge a source it was not given;
- source hashes are rechecked at apply time;
- page count, page size, slug, link, and total-output limits are enforced;
- no source body appears in progress events.

### 15.5 Service and secret handling

Workflow parameter schemas may declare service_ref. The UI lists only visible compatible services. The run stores resource references and immutable public configuration digests, never decrypted credentials.

Service resolution is snapshotted at run start. Failover inside an llmRouter remains that service's responsibility and is recorded in per-step usage.

## 16. Limits and budgets

Every workflow agent instance has explicit limits. Platform maxima cap instance values.

Required limits:

- run duration;
- task executions;
- FlowFiles created;
- bytes in queues;
- maximum fan-out per node;
- maximum parallel LLM calls;
- total LLM calls;
- input and output tokens;
- cost in USD;
- subflow depth;
- preempt messages per checkpoint;
- progress events per minute;
- terminal response bytes;
- artifact count.

The executor checks limits before scheduling and after committing each task result. Budget exhaustion cancels provider calls, prevents later mutations, records budget_exceeded, releases inbox claims, and produces a compatible terminal error without a fabricated successful response.

UsageLedger receives one row per LLM step plus one workflow-run summary keyed by run_id. Summary aggregation must not double-charge.

## 17. Events and observability

Add event types:

- workflow_run_started;
- workflow_step_started;
- workflow_step_progress;
- workflow_step_completed;
- workflow_step_failed;
- workflow_preempt_available;
- workflow_preempt_claimed;
- workflow_run_restarting;
- workflow_run_recovered;
- workflow_budget;
- workflow_run_cancelled.

Every event includes conversation_id, agent_name, root turn ID, run ID, flow FQN, generation, event sequence, and safe task label. Sensitive task inputs and provider reasoning are excluded.

Existing clients may ignore unknown event types. The normal done and error_event shapes remain authoritative.

Provenance records:

- flow FQN and digest;
- task ID and type;
- input/output FlowFile process IDs;
- run ID;
- duration and relationship;
- model/service route for LLM steps;
- inbox claim IDs;
- mutation idempotency keys;
- final assistant msg_id.

Logs use run ID as the primary correlation key.

## 18. Wiki Agent reference workflow

### 18.1 User-visible behavior

The Wiki Agent accepts requests such as:

- Refresh the project wiki.
- Document the authentication changes.
- Process pending sources and tell me what changed.
- Stop after the current batch.
- Also include the new relay files.

It does not choose arbitrary tools. The authored flow decides the stages and permitted scope.
An intent-classification LLM runs before project access. It accepts only requests
wholly dedicated to project-wiki inspection or maintenance; unsupported and mixed
requests terminate with an orientation response. Its structured output may only
narrow the configured source-batch limit. The original accepted request focuses
later LLM stages but cannot alter authority or `write_mode`.

### 18.2 Flow

~~~text
agent_request
      |
      v
infer_and_validate_wiki_intent
      |
      +---- unsupported ---> format_orientation_response
      |
      v
resolve_project_target
      |
      v
scan_project_graph_and_sources
      |
      v
select_dirty_wiki_batch
      |
      +---- no changes ------------------------------+
      |                                              |
      v                                              v
fetch_source_files                         format_no_change_report
      |
      v
normalize_source_files
      |
      v
split_bounded_batches
      |
      v
extract_architecture_facts_llm
      |
      v
merge_fact_extractions
      |
      v
receive_preempt_messages
      |
      +---- focus changed ---> revise_batch_selection
      |
      v
plan_wiki_patch_llm
      |
      v
validate_patch_schema_and_citations
      |
      v
review_patch_llm (optional)
      |
      v
apply_wiki_patch_compare_and_swap
      |
      +---- superseded ---> format_superseded_report
      |
      v
lint_project_wiki
      |
      v
format_committed_work_report
      |
      v
complete_agent_turn
~~~

### 18.3 Stage contracts

#### resolve_project_target

Deterministic.

- Resolve the relay explicitly linked to the conversation.
- Resolve project_root from validated instance parameters.
- Reject no relay, inaccessible relay, FileStore, and local execution.
- Record relay identity without exposing credentials.
- Relationship: success or failure.

#### scan_project_graph_and_sources

Deterministic and read-only.

- Reuse ProjectGraph.build_from_relay and ProjectWiki.scan_from_relay.
- Preserve high-signal first-scan seeding.
- Return source counts, dirty counts, scan limits, and graph seed paths.
- Do not send file contents to the LLM.

#### select_dirty_wiki_batch

Deterministic and idempotent.

- Snapshot the oldest bounded dirty-source entries.
- Prefer paths related to user focus only within the pending set.
- Capture state, old hash, new hash, and selection digest.
- Include affected current wiki page references.
- Return no_change when nothing is pending.

#### fetch_source_files

Read-only relay task.

- Fetch only snapshotted paths.
- Enforce maximum file bytes and total batch bytes.
- Represent removed files without attempting a read.
- Preserve exact byte hash.
- Never use generated wiki pages as sources.

#### normalize_source_files

Deterministic.

- Detect UTF-8 and replace invalid sequences predictably.
- Normalize CRLF to LF for LLM input without changing source hashes.
- Remove transport-only noise, never semantic code.
- Add language, path, size, line count, truncation, and source hash metadata.
- Use a stable truncation strategy with head, structural excerpts, and tail.
- Mark binary, unreadable, oversized, and unsupported files explicitly.
- Produce no project writes.

#### split_bounded_batches

Deterministic.

- Group related files using project graph edges and path proximity.
- Bound characters, files, and total groups.
- Give every group a stable batch digest.
- Preserve removal events.
- Cap parallelism to the run limit.

#### extract_architecture_facts_llm

One agentLLMCall per batch, potentially parallel.

- Use extractor_llm.
- Return a strict schema containing claims, relationships, decisions, invariants, workflows, candidate page slugs, and exact source paths.
- Treat source text as untrusted.
- Forbid a claim without at least one provided source.
- Store intermediate extraction as a run artifact, not a wiki page.

#### merge_fact_extractions

Deterministic join.

- Correlate branches by fragment identifier and run ID.
- Deduplicate normalized claims.
- Preserve contradictory claims and their sources for the writer.
- Refuse incomplete joins after the bounded wait.
- Emit aggregate extraction metrics.

#### receive_preempt_messages

Explicit checkpoint.

- Claim pending messages for this run.
- Parse only supported intent hints such as focus, include paths, exclude paths, stop_after_batch, and cancel.
- Use deterministic parsing for recognized structured controls.
- If free-form interpretation needs an LLM, route through a strict classifier node and validate its result.
- Never let a message replace project root, relay, flow, services, or permissions.
- If focus changed before mutation, optionally route back to batch selection.
- Bound loops and record absorbed turn IDs.

#### plan_wiki_patch_llm

Uses writer_llm.

- Receive merged facts, affected current pages, and wiki index.
- Return page replacements in a strict schema.
- Limit page count and total body size.
- Require citations from the selected snapshot.
- Prefer durable subsystem pages over one page per file.
- Mark historical claims explicitly.
- Never write directly.

#### validate_patch_schema_and_citations

Deterministic and fail-closed.

- Validate JSON schema.
- Validate slug, title, summary, content, links, page and byte limits.
- Verify every citation was captured and remains in the manifest.
- Derive processed sources exactly from the selected snapshot; never accept
  model-proposed acknowledgement paths.
- Detect uncited factual pages.
- Reject generated wiki pages as sources.
- Route invalid results to handled failure without acknowledgement.

#### review_patch_llm

Optional and non-authoritative.

- Use reviewer_llm.
- Receive the proposed patch and extracted evidence.
- Return issue codes and suggested corrections.
- Cannot approve a mechanically invalid patch.
- A configured severe issue routes back to one bounded writer revision.
- Maximum one review/revision cycle in version 1.

#### apply_wiki_patch_compare_and_swap

Deterministic keyed effect.

- Recheck every selected dirty-source snapshot.
- If any source changed, return superseded without writes or acknowledgements.
- Apply page replacements atomically through ProjectWiki.
- Acknowledge only processed sources that no stale page still requires.
- Use an idempotency key derived from run, snapshot, and patch digests.
- Return committed page IDs and actual acknowledgement results.
- Never report intended pages as committed.

#### lint_project_wiki

Deterministic and read-only.

- Run ProjectWiki.lint after commit.
- Report missing links, missing files, orphans, uncited pages, and stale pages.
- Do not roll back a valid commit for unrelated pre-existing lint issues.
- Separate newly introduced issues from pre-existing issues.

#### format_committed_work_report

Deterministic template task.

- Build the user response from committed result data.
- Include pages created, updated, unchanged, sources processed, remaining, superseded, and lint warnings.
- Include no raw source content.
- A final LLM is unnecessary for the standard report.

#### complete_agent_turn

Stage exactly one typed terminal result for the coordinator.

### 18.4 No-change result

After the mandatory intent-classification call, no extraction, writer, or reviewer
LLM call is needed when there are no dirty sources. A refresh request returns a
deterministic no_change result with only the bounded classifier cost.

### 18.5 Superseded result

If a selected source changes while LLM work is running:

- apply nothing from that snapshot;
- do not acknowledge it;
- report the source count as superseded;
- leave it pending;
- optionally schedule a new run according to policy;
- never claim that the wiki was updated.

### 18.6 Automatic maintenance reuse

ProjectMaintenanceScheduler continues to coalesce filesystem changes. After migration, it invokes the same exact Wiki Agent workflow through WorkflowRunCoordinator with invocation_mode equal to silent_maintenance.

Silent maintenance:

- has owner and relay scope but no assistant transcript output;
- records run status, provenance, usage, and logs;
- uses the same validation and apply tasks;
- does not publish normal chat done unless a transport is explicitly waiting;
- may process one bounded batch and reschedule if work remains.

The interactive Wiki Agent and automatic maintainer therefore share the implementation without forcing background jobs to speak in chat.

## 19. New and refactored components

### 19.1 Core runtime

Add:

- core/agent_turn_ingress.py
- core/agent_runtime_router.py
- core/agent_runtime_adapters.py
- core/workflow_agent_runtime.py
- core/workflow_run_context.py
- core/workflow_run_store.py
- core/agent_inbox_store.py
- core/agent_workflow_validator.py
- core/agent_workflow_repository.py
- core/workflow_capability_gate.py
- core/workflow_idempotency.py

Refactor:

- core/agent_runtime_api.py to target the generic runtime seam while retaining its public request/submission/result contracts;
- core/conv_agent_config.py for workflow fields and validation;
- tasks/ai/agent_streaming.py to call shared ingress/router helpers;
- tasks/ai/agent_emitter.py to use AgentInboxStore;
- core/conversation_store.py cleanup paths for inbox and run records;
- core/usage_ledger.py for step and run correlation;
- core/repository.py to protect referenced flow versions.

### 19.2 Tasks

Add under tasks/ai/workflow or a similarly cohesive package:

- agentWorkflowInput;
- agentLLMCall;
- receiveAgentMessages;
- emitAgentProgress;
- completeAgentTurn;
- formatAgentReport.

Add Wiki-specific tasks under tasks/data or an installable first-party PFP package:

- scanProjectWikiSources;
- selectWikiSourceBatch;
- fetchWikiSources;
- normalizeProjectSources;
- splitWikiSourceBatches;
- mergeWikiExtractions;
- validateWikiPatch;
- applyWikiPatch;
- lintProjectWiki;
- formatWikiWorkReport.

Refactor ProjectWiki.auto_update into reusable preparation, validation, and commit methods. Keep ProjectWiki as the storage and consistency authority.

### 19.3 Engine

Extend ContinuousFlowExecutor or add AgentWorkflowExecutor composition for:

- immutable WorkflowRunContext injection;
- reserved-attribute reassertion;
- per-task capability checks;
- task-specific retry policy;
- run cancellation;
- run limits;
- idempotent result lookup;
- progress callbacks;
- exact input and terminal ports;
- isolated per-run task instances;
- deterministic error collection.

Do not place conversation persistence inside the generic flow executor.

### 19.4 Repository and PFP

Extend:

- flow schema with kind and agent_contract;
- agent resource schema with runtime_defaults;
- PFP dependency validation so an agent definition can require an exact flow FQN and task providers;
- uninstall protection for referenced flows/tasks;
- package permission summary with workflow task effects;
- install preview showing dedicated agents and bound flows.

The Wiki Agent should ship as a first-party package or first-party repository bundle, not as hidden hardcoded configuration.

### 19.5 UI

Update agent creation and configuration dialogs:

- add Workflow to the runtime selector;
- hide primary LLM service for workflow agents;
- show compatible agent_workflow flows only;
- show exact version and scope;
- render agent_contract parameters;
- render service_ref selectors by capability;
- configure preempt policy and bounded limits;
- validate before save;
- show an explicit Upgrade workflow action;
- show runtime badge and bound flow in resource lists.

Update the flow editor:

- agent workflow template;
- special entry, checkpoint, progress, LLM, and terminal nodes;
- contract editor;
- capability and side-effect summary;
- unreachable-terminal, unsafe-task, unbounded-cycle, and multi-terminal diagnostics;
- test-run input panel;
- direct navigation from agent configuration to the pinned flow version.

Update the runtime console:

- run and root turn IDs;
- generation and status;
- pinned flow identity;
- per-step state and timing;
- LLM service/model/cost;
- inbox claims;
- preempt and restart markers;
- terminal commit state;
- safe retry/recovery action;
- exact failure reason.

Add all labels to every supported locale and keep English keys as the source.

## 20. API behavior

Existing AgentRequest, AgentSubmission, and AgentFinalResult remain the client contract.

Add optional fields to final result data rather than breaking dataclasses:

- runtime_kind;
- run_id;
- flow_fqn;
- answered_turn_ids;
- artifacts;
- workflow_metrics.

AgentSubmission.wait_for_done semantics:

- root workflow turn: true;
- a checkpoint preempt absorbed by an active run: false by default, with alias resolution still supported;
- a queued message that will start its own turn: true when submitted through a transport that waits;
- force stop: false and no error.

Add internal or admin actions:

- validate_agent_workflow;
- inspect_workflow_run;
- list_workflow_runs;
- retry_workflow_run when safe;
- upgrade_agent_workflow;
- cancel_workflow_run;
- list_agent_workflow_versions.

Do not expose raw run context, secrets, source bodies, or provider reasoning.

## 21. Validation layers

Validation occurs at four times.

### Publish time

- JSON/schema validity;
- exact ports;
- task safety metadata;
- service parameter types;
- graph reachability;
- bounded cycles and fan-out;
- subflow recursion;
- package capability declaration.

### Bind time

- flow version visibility;
- agent definition visibility;
- parameter completeness;
- compatible services;
- supported preempt policy;
- user-selected limits within platform maxima;
- no missing package dependencies.

### Run start

- exact version and digest available;
- conversation access;
- relay and service access;
- selected agent still present;
- no conflicting active generation;
- authorization and permission snapshot;
- available global and user concurrency budget.

### Task boundary

- current generation;
- cancellation and deadline;
- capability gate;
- authorization version;
- task and run budgets;
- idempotency state;
- reserved runtime identity.

## 22. Concurrency

- Serialize runs per conversation plus canonical agent.
- Permit different agents in one conversation to run concurrently under existing product rules.
- Permit the same reusable workflow across conversations with isolated task instances and run stores.
- Limit concurrent workflow runs globally, per user, and per flow.
- Limit parallel LLM steps per run and per user.
- Never share mutable task instances between runs.
- Cache parsed immutable flow definitions by exact FQN and digest, then instantiate fresh tasks.
- A checkpoint loop has an explicit maximum visit count.
- A join correlates by run ID plus fragment identifier and fails closed on missing or foreign branches.

## 23. Failure taxonomy

Use stable machine codes:

| Code | Meaning |
|---|---|
| workflow_binding_invalid | Missing or inaccessible exact flow |
| workflow_contract_invalid | Published flow violates agent contract |
| workflow_task_forbidden | Task capability not permitted |
| workflow_service_unavailable | Required snapshotted service unavailable |
| workflow_task_failed | Authored task failure |
| workflow_output_missing | No terminal result |
| workflow_output_multiple | More than one terminal result |
| workflow_output_invalid | Terminal schema invalid |
| workflow_cancelled | Ordinary cancellation |
| workflow_force_stopped | Immediate user force stop |
| workflow_superseded | Old generation or stale source snapshot |
| workflow_timed_out | Run deadline reached |
| workflow_budget_exceeded | Limit reached |
| workflow_recovery_failed | Durable run could not resume safely |

User messages remain concise. Full structured diagnostics stay in run inspection and logs.

## 24. Migration strategy

### 24.1 Existing agents

No data migration is needed for existing conv_agents entries and no new defaults are written into them. Existing create/update clients that omit `runtime_kind` retain the currently shipped normalization to `llm`; changing that contract is outside this feature. Creating, importing, binding, or upgrading a workflow agent requires an explicit `runtime_kind: workflow` and the enabled server capability. Existing `llm`, `external_mcp`, and `external_agui` behavior is unchanged.

### 24.2 Agent definitions

Extend the repository schema. Existing prompt-only definitions validate unchanged. New workflow defaults are copied only when an agent is added to a conversation or explicitly upgraded.

### 24.3 Pending queue

Migration is a deliberate expand/validate/activate operation, not an unconditional startup side effect:

1. preflight every JSONL queue and report invalid rows without changing state;
2. stop queue wakeups behind the existing startup barrier;
3. import into a fresh SQLite generation and verify per-queue counts, message IDs, order, payload digests, and force-stop metadata;
4. retain the original files as a rollback snapshot;
5. atomically write the activation marker selecting SQLite as the only writer;
6. start the behavior-compatible PendingQueue facade and reconcile ingress receipts before waking agents.

If any validation fails, no activation marker is written and the existing JSONL implementation continues unchanged. Rollback is supported only before post-activation writes; after activation, recovery/repair operates on SQLite rather than switching between two writable stores.

After a release migration window, remove the old JSONL reader and compatibility facade in accordance with PawFlow's no-backward-compatibility rule.

### 24.4 Project Wiki

Refactor current methods before switching scheduling:

1. characterization tests pin current source scanning, initial seeding, superseded checks, page validation, acknowledgement, and local-surface rejection;
2. extract deterministic preparation and apply operations without changing behavior;
3. wrap them in safe tasks;
4. run the workflow in shadow mode without writes and compare proposed outputs;
5. switch interactive Wiki Agent writes;
6. switch automatic maintenance;
7. remove ProjectWiki.auto_update's embedded LLM orchestration;
8. retain ProjectWiki storage/query/lint APIs.

### 24.5 Default flow

The default pawflow_agent flow retains the same agentRuntime port. In the first release, its agentLoop task contains only the narrow opt-in workflow dispatch; all other resolved runtime kinds continue through their existing implementation. A later, separately gated cleanup may replace it with agentRuntime without changing the port contract.

### 24.6 Cross-plan dependency order

The work-package numbers describe ownership, not permission to ship out of dependency order.

- shared identity, ResourceRef, capability effects, and the extension of the existing authorization pipeline must land before a workflow can execute user-authored or PFP tasks;
- WP3 may execute only first-party `pure` or explicitly enumerated read-only tasks under a bootstrap fail-closed gate;
- WP6 is required before any mutating task, external task provider, or general workflow authoring is enabled;
- durable group deliberation from the collaboration plan requires this plan through WP7 plus the shared authorization/lifecycle substrate;
- AgentRunRegistry may expose a workflow adapter only after WorkflowRunStore is the authoritative run store.

## 25. Implementation work packages

### WP0 — Characterization and architecture contracts

Deliver:

- tests pinning current AgentLoop ingress, persistence, done correlation, preempt, generation, and force-stop behavior;
- tests pinning current ProjectWiki guarantees;
- AgentWorkflowRequest, AgentWorkflowResult, WorkflowRunContext, inbox, run-state, event, and error schemas;
- task safety and idempotency metadata contract;
- flow kind and agent_contract schema;
- architecture decision recorded in this document.

Gate:

- no behavior changes;
- existing focused suites and full unit suite pass;
- schemas reject unknown versions and unsafe defaults.

### WP1 — Opt-in dispatch and incremental runtime router

Deliver:

- narrow `workflow` dispatch after existing authentication and canonical agent resolution;
- AgentRuntimeRouter and adapter protocol for the new runtime;
- characterization-protected extraction of AgentTurnIngress in small steps;
- unchanged direct calls to existing LLM and external runtime paths until each adapter separately passes parity;
- unchanged AgentRuntimeAPI and HTTP acknowledgements;
- routing by resolved conv_agents runtime_kind only.
- one atomic gated update of every runtime-kind validator and serializer; disabling the capability makes each of them reject or hide workflow while preserving the current three-kind behavior.

Gate:

- with the workflow capability disabled, request acknowledgements, transcript rows, queue operations, events, waiter results, and errors for LLM, external MCP, and external AG-UI fixtures are byte-for-byte or semantically identical as appropriate;
- one user row is persisted in every ingress path;
- refusal creates no waiter leak;
- selectedAgent and case-insensitive resolution remain correct.

### WP2 — Resource and flow contracts

Deliver:

- runtime_kind workflow;
- nested workflow instance config;
- runtime_defaults on agent definitions;
- flow kind agent_workflow and agent_contract;
- exact scoped flow resolver with digest;
- publish and bind validators;
- reference protection for flow versions;
- PFP dependency representation.

Gate:

- workflow agent cannot save without a visible exact flow version;
- workflow agent needs no primary llm_service;
- existing definitions and instances still load;
- invalid tasks, ports, cycles, services, and parameters fail before execution.
- existing API/UI create and update fixtures that omit `runtime_kind` still create or retain `llm` instances exactly as before.

### WP3 — Minimal workflow turn vertical slice

Deliver:

- process-resident WorkflowAgentRuntime;
- isolated exact-version execution using the existing executor;
- agentWorkflowInput and completeAgentTurn;
- WorkflowRunContext;
- progress events;
- coordinator finalization;
- queue-only preempt policy;
- one simple first-party workflow with two deterministic steps and one InferLLM or fake LLM step.
- a bootstrap fail-closed task gate that permits only an explicit first-party `pure`/read-only list for this experimental slice.

Gate:

- web, Telegram-style AgentRuntimeAPI, and A2A-style submissions receive one correlated final response;
- zero or multiple terminals fail;
- intermediate content never reaches the transcript;
- stale generation cannot finalize;
- force stop produces no error and next run succeeds.

This slice may use run_batch in a background worker. It is not the durable release.

### WP4 — Agent inbox and checkpoint preemption

Deliver:

- SQLite AgentInboxStore;
- durable ingress receipts and boot reconciliation;
- one-shot PendingQueue migration;
- behavior-compatible PendingQueue facade and caller-by-caller migration;
- receiveAgentMessages;
- claim, lease, release, acknowledge, transfer, and discard operations;
- checkpoint, queue, and restart policies;
- answered_turn_ids alias resolution;
- boot lease recovery.

Gate:

- crash after claim loses no message;
- duplicate enqueue by msg_id creates one work item;
- a crash between transcript persistence and inbox enqueue is repaired from the ingress receipt;
- messages preserve order and attachments;
- final commit acknowledges only messages the workflow saw;
- final-arrival race schedules another turn;
- force-stop cutoff behavior matches current invariant.

### WP5 — Production LLM task and budgets

Deliver:

- agentLLMCall;
- scoped service snapshot;
- strict structured outputs;
- cancellation;
- per-step usage and run aggregation;
- run-idempotent result cache;
- task-specific retry control;
- limits and budget enforcement.

Gate:

- an executor retry cannot double-charge a completed cached call;
- cancellation closes an active provider request when supported;
- malformed structured output routes to failure;
- budget exhaustion prevents later mutations;
- the usage summary equals the sum of step usage without double charging.

### WP6 — Capability gate and safe-task catalog

Deliver:

- task effects, workflow-safe marker, and idempotency metadata;
- runtime task-boundary gate;
- publish-time safe-task validation;
- permission mode and policy-gating integration;
- PFP task capability enforcement;
- audit events;
- first safe catalog needed by Wiki Agent.

Gate:

- executeScript, listeners, cron, and undeclared PFP tasks are rejected;
- a read-only run cannot execute a write task;
- preempt corrections cause policy reevaluation;
- a flow cannot target another user's relay, conversation, file, or service;
- source data cannot mutate reserved runtime identity.

### WP7 — Durable run store and recovery

Deliver:

- WorkflowRunStore;
- active-run leases and generations;
- terminal CAS protocol, stored stamped payload, idempotent ConversationWriter append, and terminal outbox;
- committing-state repair;
- restart-from-input recovery;
- idempotent step cache;
- recovery worker;
- retention and conversation deletion cleanup.

Gate:

- restart during LLM, checkpoint, mutation, assistant persistence, inbox acknowledgement, and terminal-event publication yields one persisted assistant row and one logical terminal outcome;
- exact flow version absence fails closed;
- stale runs never clean successor state;
- completed runs are never replayed;
- inbox claims are reconciled after every recovery path.

### WP8 — Wiki task extraction and reference workflow

Deliver:

- characterization-preserving ProjectWiki refactor;
- Wiki-safe deterministic tasks;
- extraction, writer, and optional reviewer LLM schemas;
- global first-party pawflow.agents.wiki:1.0.0 flow;
- wiki-agent definition with runtime defaults;
- deterministic final work report;
- interactive mode;
- silent maintenance mode.

Gate:

- no-change path makes zero LLM calls;
- invalid LLM output writes nothing and acknowledges nothing;
- changed source during a run yields superseded;
- committed pages cite current hashes;
- exact current scan limits and local=false invariant remain;
- the report reflects actual commits;
- current wiki lint and query behavior stays intact.

### WP9 — Authoring, resource, and runtime UI

Deliver:

- Workflow runtime agent form;
- compatible exact flow selector;
- contract-driven parameter and service forms;
- preempt and limit controls;
- upgrade action;
- workflow badges;
- flow-editor agent workflow template and validators;
- runtime DAG/run inspector;
- recovery and safe retry actions;
- localized labels and accessibility checks.

Gate:

- a non-technical user can install the Wiki Agent, bind services, add it to a conversation, run it, inspect its stages, and upgrade it without editing JSON;
- UI cannot save an invalid binding;
- run inspector exposes no secret or source body;
- existing LLM and external agent dialogs remain functional.

### WP10 — Shadow rollout, migration, and release

Deliver:

- Wiki shadow comparison mode;
- operational metrics and alerts;
- PendingQueue migration tooling;
- run/inbox inspection and repair commands;
- documentation updates to AGENT_SYSTEM, tasks, services, flow editor, PFP guides, usage tracking, security, and observability;
- release notes and operator runbook;
- removal of embedded ProjectWiki LLM orchestration after cutover.

Gate:

- shadow runs match existing wiki safety outcomes on representative repositories;
- no duplicate assistant messages or lost inbox messages in stress tests;
- recovery fault-injection suite passes;
- full test suite passes;
- manual end-to-end validation passes for webchat and one non-HTTP transport;
- automatic maintenance and interactive Wiki Agent both use the same flow version.

## 26. Proposed test matrix

### 26.1 Unit

- Capability-disabled and non-workflow configuration fixtures proving no new runtime/storage path is entered.
- Agent and flow schema parsing.
- Scope and exact-version resolution.
- Run state transitions and invalid transitions.
- Terminal compare-and-swap.
- Inbox enqueue, claim, lease expiry, release, transfer, acknowledge, discard, and deduplication.
- Runtime context immutability.
- Reserved-attribute restoration.
- Capability and permission intersections.
- Task retry policy.
- LLM idempotency key derivation and cache.
- Budget accounting.
- Every new task relationship and schema.
- Wiki normalization, batching, validation, apply idempotency, and reporting.

### 26.2 Runtime integration

- One workflow turn from AgentRuntimeAPI to done.
- Multiple sequential LLM nodes.
- Parallel extraction plus join.
- Subflow context inheritance.
- Missing terminal.
- Multiple terminals.
- Handled failure branch.
- Provider cancellation.
- Queue backpressure and limit.
- Preempt before start, during LLM, during deterministic task, at checkpoint, after final checkpoint, and during commit.
- Restart policy with unchanged and changed input hashes.
- Force stop followed immediately by a new turn.
- Transport waiter aliases.
- Concurrent conversations using the same workflow.
- Concurrent agents in one conversation.
- Workflow version upgrade while an old run is active.
- Existing LLM, external MCP, external AG-UI, voice, background-tool, delegate-reply, skill-lifecycle, and scheduled-task PendingQueue producers before and after inbox activation.
- Existing webchat, PawCode, VS Code, Telegram-style, A2A, and published-MCP acknowledgement and terminal-event fixtures with unknown workflow fields ignored.

### 26.3 Recovery fault injection

Terminate or simulate failure:

- after ingress receipt creation but before transcript persistence;
- after ingress persistence but before enqueue;
- after enqueue but before run creation;
- after run creation but before worker start;
- after inbox claim;
- after LLM provider success but before cache commit;
- after cache commit;
- during keyed mutation;
- after mutation commit;
- after assistant row append;
- after terminal metadata;
- before and after done publication.

Each case asserts no lost request, no duplicate mutation, no duplicate assistant row, and a recoverable or final run state.

### 26.4 Security

- Foreign conversation and relay targeting.
- Service reference outside scope.
- Flow scope escalation.
- Malicious source prompt injection.
- Malicious preempt attempting parameter or permission replacement.
- Path traversal and symlink boundary.
- Unsafe task hidden in a subflow.
- PFP task with undeclared effects.
- Read-only mode.
- Policy gate deny and ask.
- Event and run-inspector redaction.
- Flow version deletion while referenced.

### 26.5 Wiki golden fixtures

Fixtures cover:

- first scan;
- ordinary source edit;
- source removal;
- root change;
- oversized and unreadable files;
- mixed encodings and CRLF;
- cross-file subsystem change;
- malformed extraction;
- invented citation;
- stale existing page;
- source changing during writer call;
- partial page update;
- pre-existing lint issues;
- no durable knowledge change;
- user focus changed by preempt;
- automatic silent maintenance.

Mechanical assertions inspect source hashes, pages, manifest, acknowledgements, run metrics, LLM call count, transcript rows, and final report.

## 27. Operational controls

Operators need:

- list active and recoverable workflow runs;
- inspect one run without content leakage;
- cancel or force-stop by exact run;
- release an orphan inbox lease;
- retry only when the runtime proves it safe;
- quarantine a broken flow version;
- see agents bound to a version before deletion;
- view queue depth, oldest age, failures, recovery count, and cost;
- configure global and per-user concurrency and budget maxima;
- compact completed run and inbox history after retention.

Alerts:

- inbox oldest age above threshold;
- run stuck without events;
- repeated recovery;
- committing state older than threshold;
- multiple terminal attempt;
- budget anomaly;
- repeated workflow output invalid;
- Wiki superseded loop;
- task capability denial in a published first-party flow.

## 28. Documentation deliverables when implementation ships

Update in the same changes:

- docs/AGENT_SYSTEM.md;
- docs/architecture.md;
- docs/tasks.md;
- docs/02_REFERENCE_TASKS_SERVICES.md;
- docs/flow_editor.md;
- docs/flow_runtime_console.md;
- docs/PFP_DEVELOPER_GUIDE.md;
- docs/PFP_PACKAGES.md;
- docs/security_model.md;
- docs/POLICY_GATING.md;
- docs/usage_tracking.md;
- docs/OBSERVABILITY.md;
- docs/COGNITIVE_TOOLS.md for the Wiki migration;
- docs/examples with a minimal workflow agent and the Wiki Agent;
- project wiki pages describing the shipped runtime.

Do not mark this plan implemented until every release gate is satisfied.

## 29. Risks and mitigations

### Duplicate transcript ownership

Risk: workflow tasks publish messages while the coordinator also finalizes.

Mitigation: forbid publishMessage in agent workflows; only the coordinator commits the terminal assistant row.

### Lost preempt after claim

Risk: drain-style queue consumption removes work before downstream processing.

Mitigation: transactional leases and acknowledgement only after terminal commit.

### Repeated paid or mutating task

Risk: generic retries replay LLM calls or writes.

Mitigation: task-specific retry policy, input-hash cache, keyed effects, and recovery tests.

### Hidden general agent inside a dedicated agent

Risk: authors wrap AgentLoopTask and restore uncontrolled orchestration.

Mitigation: AgentLoopTask is not workflow-agent safe in version 1; a future nested-agent capability requires its own bounds and review.

### Workflow version drift

Risk: latest changes during a run.

Mitigation: exact FQN plus digest captured before acknowledgement.

### Prompt injection from project files

Risk: source content tries to alter workflow instructions.

Mitigation: deterministic task graph, isolated data sections, strict output schemas, mechanical citations, compare-and-swap hashes, and capability gating.

### Excessive fan-out and cost

Risk: one source list creates unbounded parallel LLM calls.

Mitigation: publish-time declared limits and runtime global/user/run caps.

### Recovery ambiguity

Risk: process dies around final persistence.

Mitigation: committing state, stored stamped terminal payload, assistant msg_id, and repair-before-replay boot ordering.

### UI complexity

Risk: workflow agents become harder to configure than general agents.

Mitigation: contract-derived forms, a first-party Wiki Agent preset, compatible-service selectors, defaults, and validation before save.

## 30. Release acceptance criteria

The feature is complete only when all statements are true.

1. A user can add a Workflow runtime agent from the UI without selecting one primary LLM.
2. The agent binds to an exact visible agent_workflow flow version.
3. A flow with at least two LLM stages returns one normal conversation response through webchat and AgentRuntimeAPI.
4. Intermediate LLM outputs never appear as assistant transcript rows.
5. A receiveAgentMessages checkpoint incorporates a new prompt and the final event names every answered turn ID.
6. No accepted message is lost across process restart.
7. Force stop is immediate, non-error, generation-safe, and leaves a following turn healthy.
8. A workflow cannot use an unsafe task, inaccessible service, relay, conversation, or flow.
9. LLM and mutation retries are idempotent and cost accounting is exact.
10. A process crash at every terminal boundary produces one assistant row and one logically final result; repeated physical terminal-event delivery is deduplicated by stable event ID.
11. The Wiki Agent discovers, normalizes, processes, validates, applies, lints, and accurately reports source-backed documentation changes.
12. The Wiki Agent no-change path makes zero LLM calls.
13. A source hash change during processing writes nothing stale.
14. Automatic wiki maintenance and the conversational Wiki Agent share the same versioned workflow.
15. Existing llm, external_mcp, and external_agui agents pass their full regression suites.
16. The runtime inspector makes every workflow stage, preempt, cost, failure, and recovery understandable without exposing secrets.
17. All implementation documentation and tests listed above have landed.
18. With `workflow_agents_enabled` disabled, no existing runtime, queue, transcript, approval, client event, or UI configuration path changes behavior.
19. Enabling workflow agents does not migrate existing PendingQueue data; inbox activation is a separately validated operation with no dual-writer state.

## 31. Recommended first delivery slice

The smallest useful end-to-end slice is WP0 through WP3:

- workflow resource kind and exact binding;
- shared runtime router;
- one isolated process-resident flow execution;
- one or more stateless LLM nodes;
- queue-only preempt behavior;
- one terminal coordinator;
- existing SSE and transport correlation.

That slice proves the product model, but it must be labeled experimental because run_batch is not crash durable.

The first production-ready release is WP0 through WP8. WP9 is required for general availability because dedicated agents must be authorable and operable without hand-editing repository JSON. WP10 is the release gate.

Implementation note (2026-08-25): WP0-WP10 are implemented behind server-owned
flags, including exact package bindings, durable typed interactions, invocation
and terminal continuation, inspector/recovery surfaces, declarative
WorkflowProposal/FlowRun composition, and Web/PawCode/VS Code UiSurface
rehydration. The remaining release decision is operational: full green
validation, authenticated Web plus non-HTTP canaries, and staged activation.
Legacy removal remains deferred until those gates and the compatibility release.

## 32. Final product example

After implementation, a user installs the first-party Wiki Agent package, selects three optional service roles, and adds Wiki to a conversation.

The user says:

> Refresh the wiki. Focus on the authentication and relay changes, and tell me exactly what you updated.

PawFlow, not the model, decides to:

1. resolve the linked relay;
2. scan and identify changed sources;
3. normalize and batch them;
4. run bounded extraction calls;
5. merge evidence;
6. check for a newer user instruction;
7. ask a writer model for a structured patch;
8. validate every citation and limit;
9. optionally ask a reviewer model for issues;
10. compare source hashes;
11. atomically apply valid pages;
12. lint the result;
13. generate a deterministic report from committed effects;
14. persist one final assistant response.

That is the defining distinction of a dedicated workflow agent: the LLM contributes judgment inside explicit stages, while PawFlow owns orchestration, permissions, state, validation, recovery, and truth about what actually happened.

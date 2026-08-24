# Agent Collaboration and Tool Safety — Complete Implementation Plan

Status: implementation in progress; WP0-WP7 and the local WP8 hardening slices
are implemented as of 2026-08-24, with all new capabilities disabled by
default. WP8 still requires the manual multi-client/channel rollout gate and
does not yet authorize legacy removal or release cutover.

WP0 implementation note: the side-effect-free schemas live in
`core/agent_contracts.py`, `core/agent_turn_identity.py`,
`core/tool_execution_context.py`, `core/tool_authorization_contracts.py`,
`core/tool_lifecycle.py`, `core/resource_identity.py`,
`core/agent_run_contracts.py`, and `core/agent_group_contracts.py`. Runtime
authorization, lifecycle publication, resource migration, run adapters, and
group execution remain unactivated until their later work-package gates pass.

This document defines a PawFlow-native implementation plan for five related capabilities:

1. turn-scoped tool authorization;
2. ordered and replayable tool lifecycle events;
3. bounded multi-agent group deliberation;
4. a unified agent-run control plane;
5. explicit resource provenance and skill invocation policy.

The plan is informed by an external product architecture review, but it does not copy or depend on external source code, protocols, names, storage layouts, or runtime behavior. Every contract below is defined for PawFlow and must be implemented against PawFlow's own security, flow, agent, package, relay, and conversation abstractions.

## 1. Decision

PawFlow should add these capabilities as extensions of existing subsystems, not as a parallel agent framework.

The architectural decisions are:

- evolve the existing <code>core/tool_authorization.py</code> pipeline and <code>ToolApprovalGate</code> into one turn-aware authorization/approval boundary;
- add a versioned tool-event envelope above the existing transcript and SSE layers;
- implement group deliberation as a versioned workflow-agent package, backed by a small set of reusable runtime primitives;
- normalize existing delegate, flash, task, workflow, A2A, and external-agent runs behind one read/control model without merging their executors;
- make skill and resource origin, immutable identity, assignment, and invocation policy explicit;
- keep the persisted transcript authoritative for conversation content;
- keep live tool events and run events authoritative only for transient execution state;
- fail closed on missing identity, stale grants, stale events, unknown resource versions, and unsupported capabilities.

The first production priority is tool authorization and tool lifecycle integrity. Group deliberation and the unified run UI depend on those primitives.

### 1.1 Additive compatibility boundary

Every capability in this plan is opt-in until its migration and regression gates pass. Installing the code must not change an unconfigured conversation, existing agent runtime, tool decision, transcript row, SSE event meaning, package resolution, or skill prompt.

- The extended authorization pipeline returns the currently shipped decision when no new metadata, grant, or policy is configured.
- Existing runtimes keep their current execution functions; shared services are inserted at proven seams rather than by rewriting each executor at once.
- Lifecycle events are additive. Existing coarse events and transcript rows remain available during the client migration window.
- AgentRunRegistry is a projection over existing stores and registries, never a replacement executor or copied source of truth.
- Group deliberation is unavailable until the workflow runtime and its security dependencies are production-ready.
- Resource binding v2 uses preflight, exact resolution, and an activation marker; a failed migration leaves legacy assignments active rather than breaking agents.
- Irreversible migration cleanup occurs only after a release with successful shadow/canary operation and full regression coverage.

Feature flags and migration markers are server-owned. Request payloads and models cannot enable an incomplete path.

## 2. Relationship to existing plans

This plan complements, and does not replace:

- <code>WORKFLOW_AGENTS_IMPLEMENTATION_PLAN.md</code>;
- <code>POLICY_GATING.md</code> and <code>POLICY_GATING_SERVICE_PLAN.md</code>;
- <code>AGENT_SYSTEM.md</code>;
- <code>PFP_PACKAGES.md</code>;
- <code>PUBLISHED_MCP_SERVER.md</code>;
- <code>LEARNING_LOOP_PLAN.md</code>;
- <code>OBSERVABILITY.md</code>;
- <code>security_model.md</code>.

The Workflow Agents plan remains the source of truth for <code>runtime_kind: workflow</code>, exact flow versions, durable workflow runs, inbox leases, checkpoints, terminal commit, capability gating, and the Wiki Agent.

The implemented policy-gating system remains the source of truth for `AuthorizationContextStore`, `AuthorizationRef`, gating-service composition, structural classification, redaction, and the canonical `core/tool_authorization.py` entry point. This plan extends those types; it does not create another mandate or policy engine.

This plan adds cross-cutting contracts needed by both LLM and workflow agents. In particular:

- a workflow run uses the same turn identity and tool authorization context as an LLM turn;
- group deliberation is a first-party <code>agent_workflow</code>, not a special loop embedded in the chat UI;
- the unified run control plane projects WorkflowRunStore records rather than duplicating them;
- workflow task events use the same ordered lifecycle envelope when they represent an agent-visible tool or effect.

## 3. Goals

### 3.1 Safety goals

- An approval granted to one agent, turn, tool call, target, or resource cannot authorize another.
- A delayed approval response cannot revive an expired tool call.
- A permission widened during a later turn cannot retroactively authorize an earlier in-flight turn.
- A denied action cannot be rephrased or retried indefinitely in the same turn to bypass user intent.
- Reconnects cannot display a result under the wrong tool call.
- Old relay or provider events cannot overwrite a newer execution generation.
- Group participants cannot read private one-to-one context unless the user explicitly chooses a policy that permits it.
- Group fan-out, rounds, messages, tokens, time, and tool effects are all bounded.
- Resource shadowing cannot silently substitute a different skill or package after assignment.

### 3.2 Product goals

- Approval cards explain exactly which agent, turn, action, target, and scope the user is approving.
- Reloading or reconnecting reconstructs an accurate live tool state.
- Users can inspect and control all agent work through one consistent run surface.
- Users can create a group of existing agents and ask it to deliberate with predictable cost and stopping behavior.
- Skill assignment shows origin, trust, version, digest, and invocation policy.
- Existing tools, delegates, agents, conversations, clients, and packages continue to work through an explicit one-shot migration.

### 3.3 Engineering goals

- One source of truth per type of state.
- Immutable identifiers at every asynchronous boundary.
- Bounded in-memory structures.
- Durable state only where restart recovery requires it.
- Idempotent resolution and event ingestion.
- No blocking UI or HTTP worker.
- No model-only enforcement for security or budget invariants.
- Focused unit, integration, reconnect, concurrency, and fault-injection coverage.

## 4. Non-goals

This work does not:

- replace the Flow engine;
- replace AgentLoopTask;
- create a new provider router;
- make all agent runtimes behave identically;
- copy another product's filesystem-per-agent layout;
- infer read-only status from a tool name or description;
- expose private chain-of-thought;
- make group chat an unbounded autonomous society;
- permit nested groups in version 1;
- let a group participant silently inherit another chat's private transcript;
- turn transient SSE replay into durable conversation storage;
- fetch mutable live skill instructions during invocation;
- add backward-compatible parsing forever.

## 5. Current PawFlow foundation

### 5.1 Tool approval

<code>core/tool_approval.py</code> already provides:

- one central <code>ToolApprovalGate</code>;
- explicit exempt and always-ask classifications;
- a fail-closed read-only allowlist;
- command-bearing alias escalation;
- dangerous command and protected-path checks;
- conversation-and-agent-scoped persisted decisions;
- request ownership lookup;
- fail-closed behavior when no approval UI is connected.

`core/tool_authorization.py`, `core/authorization_context.py`, and policy-gating bindings already provide a canonical prepared-call pipeline, versioned user-authority lineage, restrictive conversation/agent gate composition, redacted decision envelopes, hard-deny/hard-confirm classifications, secondary-runtime adapters, and decision auditing.

The missing dimensions are exact execution identity and typed human-approval scope. Session and always grants are keyed primarily by effective tool name. Pending requests do not yet bind all of turn epoch, run generation, tool-call identity, normalized target, exact AuthorizationRef, policy snapshot, and expiry. The implementation extends the existing pipeline at that seam.

### 5.2 Tool execution and cancellation

<code>services/_tool_relay_cache_req.py</code> already tracks in-flight relay requests and supports:

- internal request ids and provider-visible tool-call ids;
- late provider-id binding;
- targeted cancel;
- agent-wide force-stop;
- cooperative cancel events and non-cooperative kill hooks;
- backgrounding;
- a short recently-finished hydration grace;
- root-conversation projection for task and delegate sub-conversations.

The registry is a strong execution control surface, but it is not yet a versioned event ledger. UI clients infer parts of the lifecycle from transcript rows, SSE events, and the in-flight snapshot.

### 5.3 SSE and transcript

<code>core/conversation_event_bus.py</code> and <code>core/sse_writer.py</code> already provide:

- per-conversation subscribers;
- client-id replacement on reconnect;
- bounded replay;
- per-conversation listener ordering;
- stale-writer cleanup;
- required <code>msg_id</code> on token events;
- transcript persistence separate from live delivery.

The general replay buffer does not validate per-tool ordering or generation. That belongs in a tool-specific layer, not in generic SSE serialization.

### 5.4 Multi-agent work

PawFlow already has:

- conversation-scoped agent instances in <code>conv_agents</code>;
- shared and isolated delegate contexts;
- flash agents;
- follow-up and live preemption;
- delegate status and result retention;
- predefined tasks and plans;
- A2A and external MCP/AG-UI agents;
- a planned workflow-agent runtime.

The missing product layer is a uniform run projection and a bounded group-deliberation workflow.

### 5.5 Skills and packages

PawFlow already has:

- ResourceStore global, user, and conversation scopes;
- explicit <code>assigned_skills</code> on agent instances;
- lazy skill loading;
- assignment and lifecycle notifications;
- reviewed imports and PFP package ownership;
- conditions and parameters on skill assignments;
- a skill learning loop.

The missing contract is an immutable assignment target and a normalized origin/invocation policy visible across ResourceStore, PFP, UI, and runtime resolution.

## 6. Architectural invariants

The implementation must preserve these invariants.

1. Every accepted user message has a UUID, timestamp, selected agent, and immutable turn id.
2. Every executable tool call has a non-empty tool-call id before authorization or dispatch.
3. One agent turn has one immutable <code>turn_epoch</code>.
4. A new user-directed turn increments the epoch before any model or workflow work begins.
5. A preempt that becomes part of an existing turn does not silently create a new authorization epoch; its policy is explicit.
6. Force stop retires every pending request and ephemeral grant owned by the stopped generation.
7. Authorization checks use structured tool metadata and normalized arguments, never prose heuristics.
8. Approval resolution validates request ownership from server state, never from client-supplied conversation or agent fields.
9. Tool lifecycle sequence numbers are monotonic within one execution epoch.
10. A terminal tool event is immutable.
11. A result without a known call is quarantined and never rendered as a successful paired result.
12. The persisted transcript remains the source of truth for completed calls and results.
13. Live lifecycle state is bounded and recoverable from the transcript plus the in-flight registry.
14. Group membership is resolved and snapshotted before execution.
15. Group budgets are enforced by the coordinator, never by prompts.
16. Only one coordinator commits a group run's terminal assistant response.
17. Skill assignment resolves to an immutable resource identity and digest.
18. Scope or package precedence cannot change the meaning of an active assignment.
19. Missing or changed resources fail closed and are visible as broken bindings.
20. All new actions are asynchronous from the UI and HTTP worker perspective.
21. When no new feature is configured, existing authorization, runtime, transcript, SSE, package, and skill behavior is unchanged.
22. Policy-gate `deny` or `ask` cannot be bypassed by a reusable approval grant; only an exact call-level user answer may settle a gate-generated `ask`.

## 7. Target architecture

    AgentRuntimeAPI / web / CLI / channels / A2A
                         |
                         v
                 AgentTurnIngress
                         |
                AgentTurnIdentity
                         |
           +-------------+--------------+
           |                            |
           v                            v
    Agent runtime router       ToolAuthorizationService
           |                            |
           v                            v
    LLM / workflow /       ApprovalRequestStore + policy
    external adapters                    |
           |                             v
           +------ ToolExecutionContext--+
                         |
                         v
                 ToolLifecycleService
                         |
          +--------------+----------------+
          |              |                |
          v              v                v
      ToolRelay     Transcript rows   ConversationEventBus
      registry      call + result     ordered live envelope
          |                               |
          v                               v
      relay/PFP/MCP                 web / CLI / VS Code

    AgentRunRegistry
      projects delegate, flash, task, workflow, A2A, external runs
                         |
                         v
                 Agent Runs UI/API

    GroupDeliberation workflow
      resolves agent snapshots -> bounded participant calls -> synthesis
                         |
                         v
                  one terminal response

    ResourceResolver
      assignment ref -> scope/origin/version/digest/policy -> skill manifest
                         |
                         v
                  load_skill / prompts

## 8. Shared identity contracts

### 8.1 AgentTurnIdentity

Add <code>core/agent_turn_identity.py</code>.

    AgentTurnIdentity:
      schema_version: 1
      conversation_id: string
      root_conversation_id: string
      agent_instance: string
      turn_id: non-empty opaque string
      ingress_msg_id: non-empty stamped message id
      turn_epoch: positive integer
      run_generation: positive integer
      authorization_context_id: UUID
      authorization_revision_at_start: positive integer
      source_kind: user | delegate | task | continuation | automation | a2a
      source_id: string or null
      created_at: RFC3339 UTC timestamp

Rules:

- <code>turn_id</code> identifies the logical turn.
- Existing workflow `root_turn_id`, transport `turn_id`, and `agent.request_msg_id` projections refer to this same logical identity; adapters must assert equality instead of allocating a second root ID.
- <code>ingress_msg_id</code> identifies the accepted source message.
- <code>turn_epoch</code> orders user-directed authorization boundaries per root conversation and agent.
- <code>run_generation</code> continues to protect force-stop and replacement behavior.
- task and delegate sub-conversations use their own <code>turn_id</code> but retain the root conversation id.
- every runtime adapter receives the same immutable object.
- `authorization_context_id` identifies the existing `AuthorizationContextStore` lineage. A checkpoint correction advances its `AuthorizationRef.revision` without mutating the turn identity; each effect snapshots the current exact ref.
- the identity is stamped into thread-local or context-local execution state only as a transport convenience; authoritative values come from ingress/run state.

### 8.2 Epoch allocation

Add a durable, atomic epoch allocator to ConversationStore internal metadata or the planned AgentInboxStore database.

Key:

    root_conversation_id + canonical_agent_instance

Operation:

    allocate_next_epoch(expected_previous?) -> integer

Requirements:

- atomic under concurrent ingress;
- monotonic across restart;
- never derived from message count;
- no decrement on delete or retry;
- aliases and case variants resolve before allocation;
- a rejected message allocates no epoch;
- a queued user message receives its epoch when accepted, even if execution starts later.

Workflow checkpoints use the active turn epoch until a policy explicitly starts a successor turn.

### 8.3 ToolExecutionContext

Add <code>core/tool_execution_context.py</code>.

    ToolExecutionContext:
      schema_version: 1
      turn: AgentTurnIdentity
      tool_call_id: string
      tool_name: canonical string
      handler_identity: canonical registry identity
      arguments_digest: sha256
      target_fingerprint: string
      capability_effects: list[string]
      relay_id: string or null
      service_id: string or null
      resource_paths: list[string]
      authorization_ref:
        context_id: UUID
        revision: positive integer
        root_turn_id: non-empty opaque string
      policy_snapshot_digest: sha256
      created_at: RFC3339 UTC timestamp

The tool registry creates this context after alias resolution and argument normalization but before approval and dispatch. Lazy <code>use_tool</code> calls resolve to the final handler identity before policy classification.

## 9. Feature A — Turn-scoped tool authorization

### 9.1 Product behavior

Approval choices become:

- Deny this action.
- Allow once for this exact call.
- Allow matching actions for this turn.
- Allow matching generic-permission actions for this agent in this conversation until revoked or the conversation is deleted.

The UI must not offer broader choices for catastrophic actions, protected targets, secret access, identity linking, executable code creation, or policy-defined always-ask effects.

The approval card displays:

- canonical agent instance;
- tool and action;
- normalized target summary;
- relay/service surface;
- risk/effect badges;
- current turn;
- expiry countdown;
- the exact breadth of every choice.

### 9.2 Authorization service

Evolve the existing <code>core/tool_authorization.py</code> canonical pipeline. Add a stateful <code>ToolAuthorizationService</code> behind its public functions, keeping those functions and <code>ToolApprovalGate</code> as behavior-compatible adapters during migration. `ToolApprovalGate` continues to own legacy permission persistence and interactive response plumbing until the new store is activated; structural classification and policy-gate composition are not duplicated.

Primary interface:

    authorize(context, summary, allow_prompt=True) -> AuthorizationDecision
    resolve(request_id, choice, actor) -> ResolutionResult
    begin_turn(turn_identity) -> None
    retire_turn(turn_identity, reason) -> None
    retire_tool_call(tool_call_id, reason) -> None
    retire_agent(conversation_id, agent_instance, generation, reason) -> None
    get_pending(request_id) -> ApprovalRequest or None
    list_pending(conversation_id, agent_instance=None) -> list[ApprovalRequest]

<code>authorize</code> may await a user decision in an agent worker, but no HTTP or UI worker may block. Existing synchronous handler paths use a worker-safe adapter until the tool loop is fully async.

### 9.3 Approval request schema

    ApprovalRequest:
      schema_version: 1
      request_id: UUID
      conversation_id: string
      root_conversation_id: string
      agent_instance: string
      turn_id: non-empty opaque string
      turn_epoch: integer
      run_generation: integer
      tool_call_id: string
      handler_identity: string
      effective_policy_name: string
      arguments_digest: sha256
      target_fingerprint: string
      capability_effects: list[string]
      resource_paths: list[string]
      relay_id: string or null
      service_id: string or null
      summary: string
      redacted_arguments: object
      allowed_choices: list[string]
      authorization_ref: object
      policy_snapshot_digest: sha256
      request_kind: generic_permission | policy_gate_ask | hard_confirm
      status: pending | allowed | denied | expired | cancelled | retired
      created_at: timestamp
      expires_at: timestamp
      settled_at: timestamp or null
      settled_by: user id or null
      settlement_reason: string or null

### 9.4 Grant schema

    ToolGrant:
      schema_version: 1
      grant_id: UUID
      conversation_id: string
      agent_instance: string
      scope:
        kind: call | turn | conversation
        turn_id: opaque string or null
        turn_epoch: integer or null
        tool_call_id: string or null
      matcher:
        handler_identity: string
        effective_policy_name: string
        target_fingerprint: string or wildcard
        relay_id: string or null
        service_id: string or null
        resource_path_prefixes: list[string]
        capability_effects: list[string]
      policy_snapshot_digest: sha256
      created_at: timestamp
      expires_at: timestamp or null
      created_by: user id
      retired_at: timestamp or null
      retirement_reason: string or null

Call grants are memory-resident and retire at call completion. Turn grants survive multiple matching calls in one turn but retire before the next turn. Conversation grants are durable until revoked or conversation deletion and always remain bound to the exact canonical agent. Version 1 has no user-global or cross-conversation grant.

### 9.5 Matching rules

A grant satisfies only the human-confirmation component of a call when every declared matcher dimension covers it. Structural guards, current permission mode, scope/ownership checks, current policy gates, cancellation, and exact prepared-call validation still run on every call.

- Handler identity is exact.
- Tool aliases never widen a grant.
- Target wildcard is permitted only for policy-approved generic-confirmation classes. This includes a server-tagged compatibility grant migrated from a currently valid `always_allow` entry when needed to reproduce its existing generic-dialog suppression; it never applies to a hard confirmation, policy-gate ask, changed prepared call, or broader handler/effect than the legacy entry covered.
- Relay and service ids are exact when present.
- Resource path matching uses normalized filesystem-service paths, not raw string prefixes.
- Capability effects requested by the call must be a subset of granted effects.
- A changed policy snapshot invalidates or re-evaluates a reusable generic-permission grant before use.
- A turn grant requires exact <code>turn_id</code> and <code>turn_epoch</code>.
- A call grant requires exact <code>tool_call_id</code> and arguments digest.
- Protected and catastrophic classifications force a new exact-call request regardless of broader grants.
- A `policy_gate_ask` can be settled only by an exact call grant tied to its request, call, arguments, target, AuthorizationRef, and policy snapshot. Turn or conversation grants never answer it.
- Unknown tool metadata denies.

### 9.5.1 Canonical decision composition

For every prepared call, in the order already established by `POLICY_GATING_SERVICE_PLAN.md`:

1. canonicalize the handler and immutable effective arguments;
2. apply structural hard-deny and hard-confirm classification;
3. validate ownership, runtime scope, cancellation, generation, and AuthorizationRef;
4. evaluate the current conversation and agent policy gates;
5. evaluate an exact or reusable human grant only for the confirmation class it is permitted to satisfy;
6. revalidate call digest, target, authorization revision, policy snapshot, and cancellation immediately before dispatch;
7. execute the exact prepared call without post-approval mutation.

The final result is the restrictive intersection. A stored `always_allow`-style grant cannot convert a policy-gate `deny` or `ask`, a hard deny, or a hard confirm into execution.

### 9.6 Target fingerprinting

Add structured target extraction to <code>ToolHandler</code> metadata.

Each handler can implement:

    authorization_target(arguments, execution_context) -> AuthorizationTarget

The default is fail-closed for non-exempt tools.

Examples:

- filesystem read/write: filesystem service plus normalized resolved paths;
- bash/Monitor: execution surface, working directory, command digest, classified effects;
- browser: browser service plus origin and action;
- messaging: connector plus destination;
- manage_resource: resource type, scope, name, action;
- PFP tool: package id, package version, exported tool id, declared effects;
- MCP tool: server id, account binding, tool id, declared annotations accepted only after server trust policy validation.

Do not derive safety from descriptions or verbs in tool names.

### 9.7 Refusal memory

Within one turn, remember denied exact action fingerprints:

    agent + turn_epoch + handler + action + target_fingerprint

A semantically identical retry returns the prior denial without creating another card.

Bounds:

- maximum 512 fingerprints per active agent turn;
- retire on turn completion;
- if the bound is exceeded, mark the turn authorization-saturated and deny new non-exempt calls for that turn;
- emit an audit event when saturation occurs.

This prevents prompt loops from wearing down the user or overflowing memory.

### 9.8 Lifecycle behavior

At accepted user ingress:

1. allocate the turn epoch;
2. call <code>begin_turn</code>;
3. expire pending requests and ephemeral grants from older epochs;
4. publish retirement events for visible stale cards;
5. start runtime execution.

At preempt:

- <code>queue</code>: the queued message owns a future epoch;
- <code>checkpoint</code>: authenticated corrections incorporated into the current run keep the current epoch, advance the existing `AuthorizationRef.revision`, and re-evaluate later effects;
- <code>restart</code>: retire the old turn and allocate a successor epoch.

At force stop:

- cancel pending requests;
- retire call and turn grants;
- cancel in-flight tools;
- publish terminal lifecycle events;
- do not persist an assistant error;
- leave conversation grants unchanged unless the user explicitly revoked them.

### 9.9 Storage

Create <code>core/tool_authorization_store.py</code> backed by SQLite and integrate it with the existing `core/tool_authorization.py` pipeline.

Durable tables:

- <code>tool_grants</code> for durable conversation grants;
- <code>approval_audit</code> and policy-decision audit for one bounded correlated security history;
- <code>agent_turn_epochs</code> if not stored with AgentInboxStore.

Pending requests, call grants, and turn grants remain process-resident but are terminally retired on restart. A process restart never restores a waiting approval card or silently approves its call.

The activation migration imports the existing redacted gating-decision JSONL records or archives them with an explicit cutoff marker; it must not leave two active audit sources for new decisions. Conversation deletion removes all conversation grants and audit rows. User deletion removes all records owned by that user. Retention is configurable for audit rows.

### 9.10 API and UI

Replace the old untyped resolve action with:

    POST action resolve_tool_approval
      request_id
      choice
      expected_status: pending

The server derives conversation, agent, turn, and tool from <code>request_id</code>. Client-supplied ownership fields are ignored for authorization.

SSE events:

- <code>tool_approval_requested</code>;
- <code>tool_approval_settled</code>;
- <code>tool_approval_retired</code>.

All use the tool lifecycle envelope defined in Feature B.

Update:

- <code>tasks/io/chat_ui/dialogs.js</code>;
- <code>tasks/io/chat_ui/sse_handlers_b.js</code>;
- VS Code approval UI;
- PawCode approval UI;
- AG-UI interrupt projection where supported;
- voice/no-UI probe behavior.

### 9.11 Compatibility migration

One-shot expand/validate/activate migration:

1. inventory existing <code>tool_permissions</code>, per-agent keys, policy bindings, and gating audit files without changing behavior;
2. preflight every entry and produce a redacted report;
3. convert <code>always_allow</code> into conversation-and-agent compatibility grants that reproduce only the old generic-dialog suppression and never satisfy policy-gate asks or hard confirmations;
4. keep live <code>session_allow</code> behavior until process restart, but never persist or reinterpret it as a turn grant;
5. keep <code>permission_mode</code> unchanged;
6. shadow-evaluate old and new generic decisions on canary conversations;
7. write the activation marker only when counts, ownership, and decision fixtures match;
8. retain a rollback snapshot until the first post-activation write, then use forward repair only;
9. remove legacy permission-map reads after a successful migration release.

If preflight or activation fails, the existing permission path remains active and no partial new grants are consulted. After successful activation, no fallback to the legacy map remains.

## 10. Feature B — Ordered tool lifecycle events

### 10.1 Purpose

The generic SSE bus transports events but should not infer tool state. Add a tool-specific lifecycle service that validates causal ordering and exposes one normalized envelope to web, CLI, VS Code, AG-UI adapters, and reconnect hydration.

### 10.2 Envelope

Add <code>core/tool_lifecycle.py</code>.

    ToolLifecycleEvent:
      schema_version: 1
      event_id: UUID
      conversation_id: string
      root_conversation_id: string
      agent_instance: string
      turn_id: non-empty opaque string
      turn_epoch: integer
      run_generation: integer
      execution_epoch: UUID
      sequence: positive integer
      tool_call_id: string
      request_id: string or null
      tool_name: string
      handler_identity: string
      phase: string
      timestamp: RFC3339 UTC
      data: object

Phases:

- <code>announced</code>;
- <code>approval_requested</code>;
- <code>approved</code>;
- <code>denied</code>;
- <code>dispatched</code>;
- <code>progress</code>;
- <code>backgrounded</code>;
- <code>cancel_requested</code>;
- <code>completed</code>;
- <code>failed</code>;
- <code>cancelled</code>;
- <code>retired</code>;
- <code>reset</code> for an execution-epoch boundary.

### 10.3 State machine

Allowed ordinary transitions:

    announced
      -> approval_requested -> approved -> dispatched
      -> approval_requested -> denied
      -> dispatched
      -> dispatched -> progress
      -> dispatched/progress -> backgrounded
      -> dispatched/progress/backgrounded -> cancel_requested
      -> dispatched/progress/backgrounded/cancel_requested
           -> completed | failed | cancelled
      -> any non-terminal -> retired

Rules:

- terminal phases are immutable;
- duplicate <code>event_id</code> is idempotently ignored;
- duplicate sequence with different content is a protocol violation;
- sequence is allocated centrally and increases across all calls for one root conversation, canonical agent, and execution epoch;
- an event from a retired execution epoch is ignored and audited;
- <code>completed</code> and <code>failed</code> require a known call;
- progress data is bounded and never contains raw secrets;
- a reset retires all non-terminal live calls from the prior epoch.

State transition, sequence allocation, store update, and enqueue into a per-agent lifecycle publication lane occur under one serialized service operation. ConversationEventBus remains the transport, but concurrent tool producers cannot publish sequence N+1 before N. Clients still validate sequence and event identity defensively.

### 10.4 Execution epochs

An execution epoch identifies one process-side producer generation for a root conversation and agent.

Create it when:

- an agent runtime starts after no active generation;
- a process restarts;
- force-stop replaces a generation;
- an external runtime reconnects with a new declared generation.

It is not the same as <code>turn_epoch</code>. Multiple turns may execute under one process execution epoch; each turn still has its own authorization boundary.

### 10.5 Authoritative state boundaries

Persisted transcript:

- completed tool-call row;
- completed tool-result row;
- message ids and parent linkage;
- final result content, subject to current truncation/FileStore rules.

Live lifecycle store:

- pending approval;
- running/backgrounded/cancelling status;
- duration;
- current bounded progress;
- recent terminal hydration until transcript catches up.

Never persist each token or progress event into the conversation transcript.

### 10.6 Lifecycle store

Create a bounded <code>ToolLifecycleStore</code> keyed by:

    root conversation + agent + execution epoch + tool_call_id

Retain:

- one announced event;
- latest non-terminal state;
- one terminal event;
- a bounded progress tail, default 20;
- recently terminal calls for the existing hydration grace or until transcript confirmation.

Bounds:

- maximum 256 live/recent calls per root conversation;
- maximum 2,000 events across one process per conversation;
- TTL for terminal live state;
- metrics and warnings on eviction.

Optional durable recovery is not needed in version 1. After process restart, the relay in-flight registry and transcript reconstruct the safe view; unknown previously running calls become <code>retired</code>, never successful.

### 10.7 Relay integration

Extend <code>services/_tool_relay_cache_req.py</code> entries with:

- execution epoch;
- last lifecycle sequence observed for that call;
- turn identity;
- handler identity;
- approval request id;
- arguments digest;
- terminal state.

`ToolLifecycleService`, not each in-flight entry, owns one allocator keyed by root conversation, canonical agent, and execution epoch. Per-call allocators would produce duplicate sequence values and make the reconnect high watermark invalid.

Changes:

- register the call before dispatch;
- bind provider tool-call ids without changing causal identity;
- emit <code>backgrounded</code> and <code>cancel_requested</code> at the control action;
- emit one terminal event in the same finally path that retires in-flight state;
- make terminal publication idempotent;
- include lifecycle identity in <code>inflight_snapshot</code>;
- keep kill hooks and cancellation semantics unchanged.

### 10.8 Provider and MCP integration

All tool adapters normalize into the same lifecycle service:

- native API model calls;
- Claude Code interactive;
- Codex/Gemini app-server;
- MCP relay;
- PFP runtime host;
- AG-UI frontend tools;
- published conversation MCP;
- workflow safe-task effects when surfaced as tools.

<code>use_tool</code> must expose the final tool lifecycle, not a nested wrapper lifecycle. The wrapper may emit diagnostic metadata, but the UI shows one call.

MCP annotations remain hints. PawFlow's explicit handler metadata and policy decide effects.

### 10.9 Reconnect protocol

Add a read action:

    get_tool_lifecycle_snapshot
      conversation_id
      agent_instance optional
      after_sequence optional
      execution_epoch optional

Response:

    execution_epochs:
      agent_instance -> current epoch
    calls:
      normalized live/recent call projections
    high_watermarks:
      agent_instance -> sequence

Client algorithm:

1. attach or replace the SSE connection first using the existing stable client ID and buffer tool-lifecycle events locally while hydration is active;
2. load the authoritative transcript;
3. request a lifecycle snapshot whose calls and high watermarks are captured under the lifecycle-store lock;
4. discard local live state for mismatched epochs;
5. apply the snapshot in sequence order;
6. apply buffered lifecycle events above the matching epoch high watermark, deduplicating event IDs;
7. switch lifecycle handling from buffered to live;
8. reconcile terminal events with transcript rows by tool-call id.

Attaching SSE after the snapshot is forbidden because an event produced between those operations could be lost. Generic non-tool events retain their existing reconnect behavior; this buffering handshake is added only to lifecycle-aware clients.

A client never guesses completion from disappearance.

### 10.10 UI

A tool row can display:

- waiting for approval;
- approved;
- running;
- backgrounded;
- cancelling;
- completed;
- failed;
- cancelled;
- retired after reload/restart.

The UI uses phase and capability fields, not text parsing.

Update webchat, PawCode, VS Code, multi-client stream JSON, and AG-UI projections from one shared protocol description and golden fixture set.

## 11. Feature C — Bounded multi-agent group deliberation

### 11.1 Product model

A group is a reusable resource with a bounded deliberation policy. In version 1, every executable member binding resolves to a concrete conversation agent instance. A reusable group template or PFP resource may name an exact agent definition requirement, but installation/binding must map it explicitly to an instance in the invoking conversation before the group becomes runnable.

Version 1 is a deliberation workflow, not an open-ended shared chat runtime. It produces one normal assistant response in the caller's conversation. Participant contributions are visible in the run inspector and may be optionally expanded in the UI, but they are not independent assistant transcript rows.

### 11.2 Resource type

Add ResourceStore type <code>agent_group</code> and PFP resource support.

    AgentGroupDefinition:
      schema_version: 1
      name: string
      description: string
      members:
        - member_id: stable string
          member_kind: conversation_instance
          agent_ref: ResourceRef
          instance_name: string
          display_name: string optional
          role: string optional
          required: boolean
      selection:
        mode: all | mentioned | classifier
        classifier_service_role: string optional
      deliberation:
        max_rounds: integer, default 2, maximum 5
        max_messages_per_member_per_round: integer, default 1, maximum 2
        max_total_participant_calls: integer, default 12, maximum 32
        max_parallelism: integer, default 4, maximum 8
        allow_pass: boolean
        rotate_first_speaker: boolean
      context_policy:
        private_context: none
        shared_history_limit: integer, default 24, maximum 100
        attachments: explicit_only
      tool_policy:
        mode: none | read_only | declared
        allowed_effects: list[string]
      synthesis:
        mode: designated_member | dedicated_llm_role | deterministic_concat
        member_id: string optional
        llm_service_role: string optional
      budgets:
        max_tokens: integer
        max_cost: decimal optional
        timeout_seconds: integer
      output:
        include_attributions: boolean
        include_dissent: boolean

Validation:

- two or more distinct individual members;
- no nested group;
- version 1 accepts only concrete conversation instances resolving to `runtime_kind: llm` with an API-backed service compatible with the one-shot structured participant task; definition-only members, workflow, group, external MCP, external AG-UI, and unsupported interactive-only services fail bind validation;
- every exact agent ref is visible at bind time and every conversation instance belongs to the invoking conversation;
- all numeric limits are positive and capped;
- no unrestricted tool mode;
- synthesis target exists;
- a reusable template's definition requirements are resolved to explicit instance names at conversation bind time; the exact definition refs and instance snapshots are captured at bind and again at run start;
- `declared` tool mode is schema-reserved but rejected until the later mutation capability is explicitly shipped.

### 11.3 Conversation binding

An <code>agent_group</code> may be invoked:

- explicitly from the UI;
- by mentioning its assigned name;
- by a <code>deliberate</code> agent tool;
- as a step in another approved workflow;
- through an API action with an exact group reference.

Group invocation is a material side effect because it creates multiple paid runs. It follows normal tool approval and budget policy unless explicitly pre-approved for the current turn.

### 11.4 Workflow package

Ship a first-party PFP package with exact flow id such as:

    pawflow.agents.group-deliberation:1.0.0

The flow stages are:

1. <code>groupDeliberationInput</code>;
2. <code>resolveGroupSnapshot</code>;
3. <code>selectGroupResponders</code>;
4. <code>initializeSharedRoom</code>;
5. round loop:
   - <code>orderRoundParticipants</code>;
   - bounded parallel <code>agentParticipantCall</code>;
   - <code>validateParticipantPost</code>;
   - <code>appendSharedRoomPost</code>;
   - <code>evaluateStopCondition</code>;
6. <code>synthesizeGroupResult</code>;
7. <code>completeAgentTurn</code>.

It uses the Workflow Agents run, inbox, cancellation, budget, terminal, and recovery contracts.

### 11.5 Participant call primitive

Add a workflow-safe task <code>agentParticipantCall</code>.

Version 1 is deliberately narrower than a nested AgentLoop:

- resolve the member's exact agent definition and conversation instance snapshot;
- resolve and snapshot that member's explicitly configured compatible LLM service;
- construct a group-specific system prompt from the public agent definition, parameters, role, current shared room, and task;
- do not load the member's private one-to-one transcript;
- do not write to the member's diary, memory, or conversation;
- do not permit delegation or user messaging;
- default to no tools;
- optional read-only tools pass through the same authorization and capability gates; declared mutations are not executable in version 1;
- return one structured participant post or <code>pass</code>;
- use an idempotency key based on group run, round, member snapshot digest, and input digest.

This is not <code>AgentLoopTask</code> and must not silently restore an unrestricted tool loop.

### 11.6 Structured participant output

    ParticipantPost:
      schema_version: 1
      group_run_id: UUID
      round: integer
      member_id: string
      member_snapshot_digest: sha256
      disposition: post | pass
      content: string
      citations: list[object]
      confidence: number optional
      token_usage: object
      created_at: timestamp

Limits:

- content maximum configured and mechanically clamped;
- at most one accepted post per member call;
- a <code>pass</code> has no content;
- malformed output retries only under the task-specific retry budget;
- after retry exhaustion the required/optional member policy decides failure or omission.

### 11.7 Responder selection

Modes:

- <code>all</code>: every member participates;
- <code>mentioned</code>: parse explicit structured mentions first, then textual handles; if none, all participate;
- <code>classifier</code>: a bounded structured-output LLM selects members from the immutable roster, but cannot invent ids or exceed limits.

Mentions use exact resolved ids, case-insensitive names, and collision diagnostics. An explicit <code>@all</code> is accepted only through the structured rich-text mention model or an unambiguous parser.

### 11.8 Ordering and fairness

For sequential rounds, rotate the first participant using:

    offset = stable_hash(group_run_id) + round
    offset modulo responder_count

For parallel rounds, preserve deterministic presentation ordering separately from completion ordering.

Do not let the fastest provider always frame the discussion first.

### 11.9 Stop conditions

Stop when any condition holds:

- every selected member passed in the round;
- no new normalized fact/proposal hashes were added relative to the prior round;
- a configured consensus threshold over validated structured dispositions is mechanically met;
- maximum rounds reached;
- token, cost, time, or participant-call budget reached;
- user preempt requests stop;
- force stop occurs.

A model may recommend stopping but cannot override a hard bound.

### 11.10 Shared-room context and privacy

The shared room contains only:

- the initiating user request;
- explicitly attached files;
- group policy;
- participant posts created in this group run;
- explicit preempt messages accepted by the workflow.

It never contains:

- private one-to-one history;
- another conversation's transcript;
- hidden provider reasoning;
- raw secrets;
- unrelated memories or diary entries.

A future opt-in context-sharing feature requires a separate threat model and is outside version 1.

### 11.11 Tool policy

Modes:

- <code>none</code>: no participant tools;
- <code>read_only</code>: intersection of group allowlist, member tools, conversation policy, provider capability, and PawFlow read-only allowlist;
- <code>declared</code>: exact declared effects and tools, still subject to authorization.

Any mutating participant tool call must be attributable to:

    group run + round + member + turn identity + tool call

The first release accepts only <code>none</code> and <code>read_only</code> at bind and run time. `declared` remains a reserved schema value and fails closed until a later reviewed capability, fault-injection suite, and approval UX are shipped.

### 11.12 Synthesis

The synthesis input is the bounded shared room, not private member contexts.

Synthesis modes:

- designated member snapshot;
- dedicated LLM service role;
- deterministic concatenation for audit-only use.

The synthesis output must include:

- final answer;
- optional attributed findings;
- optional dissent/unresolved questions;
- participant and round counts;
- budget summary;
- answered turn ids.

Only <code>completeAgentTurn</code> commits the terminal assistant row.

### 11.13 UI

Add:

- group resource editor;
- member picker showing exact definitions and service compatibility;
- bound validation and budget preview;
- group badge and explicit invocation affordance;
- run inspector timeline by round/member;
- pass, failure, cancellation, and budget status;
- expandable participant posts;
- final synthesis with optional attribution.

Do not render participant posts as if multiple assistants independently answered the user.

## 12. Feature D — Unified agent-run control plane

### 12.1 Purpose

PawFlow has multiple valid execution mechanisms. Users should not need separate mental models for status, follow-up, cancel, logs, and artifacts.

Create one normalized projection without forcing all runtimes into one storage implementation.

### 12.2 Core interface

Add <code>core/agent_run_registry.py</code>.

    AgentRunAdapter:
      list_runs(scope, filters) -> list[AgentRunSummary]
      get_run(run_id, scope) -> AgentRunDetail or None
      capabilities(run_id, scope) -> AgentRunCapabilities
      follow_up(run_id, message, interrupt) -> ActionResult
      cancel(run_id) -> ActionResult
      retry(run_id) -> ActionResult
      list_artifacts(run_id) -> list[AgentArtifact]
      get_events(run_id, cursor) -> EventPage

Adapters:

- delegate;
- flash delegate;
- assigned task;
- workflow agent;
- plan step;
- A2A remote task;
- external MCP agent;
- external AG-UI run.

An adapter may return unsupported for operations it cannot safely perform.

### 12.3 Normalized run model

    AgentRunSummary:
      schema_version: 1
      run_id: globally unique namespaced id
      runtime_kind: delegate | flash | task | workflow | plan | a2a | external_mcp | external_agui
      conversation_id: string
      root_conversation_id: string
      owner_agent: string
      target_agent: string optional
      title: string
      status: queued | running | waiting_user | waiting_external |
              cancelling | completed | failed | cancelled | superseded
      created_at: timestamp
      updated_at: timestamp
      terminal_at: timestamp optional
      progress: object
      cost_summary: object optional
      artifact_count: integer
      source_task_id: string optional

    AgentRunCapabilities:
      can_follow_up: boolean
      can_interrupt: boolean
      can_cancel: boolean
      can_retry: boolean
      can_archive: boolean
      can_delete: boolean
      can_list_artifacts: boolean
      reasons: object

### 12.4 Stable run ids

Use namespaced ids:

- <code>delegate:&lt;task_id&gt;</code>;
- <code>flash:&lt;task_id&gt;</code>;
- <code>task:&lt;task_id&gt;:&lt;iteration_id&gt;</code>;
- <code>workflow:&lt;run_id&gt;</code>;
- <code>plan:&lt;plan_id&gt;:&lt;step_id&gt;</code>;
- <code>a2a:&lt;context_id&gt;:&lt;task_id&gt;</code>;
- external adapter-specific ids after strict normalization.

Do not use display names as identities.

### 12.5 Authorization

Every read or mutation:

- resolves the run from the adapter;
- derives its owning root conversation and user;
- checks access to the actual target;
- ignores caller-supplied ownership claims;
- checks adapter capabilities;
- routes destructive operations through policy approval;
- records an audit event.

This follows the same security principle already used for tool cancel and approval resolution.

### 12.6 Tools

Keep existing tools for compatibility during the migration release, but implement them through the registry:

- <code>delegate_status</code>;
- <code>delegate_result</code>;
- follow-up actions;
- task status;
- A2A get/cancel;
- workflow inspection.

Add one optional general tool:

    agent_run
      action: list | get | follow_up | cancel | retry | artifacts
      run_id: string
      message: string optional
      interrupt: boolean optional

Lazy tool exposure may expose only <code>agent_run</code> to models that benefit from a compact surface. Existing named tools remain preferable when their semantics are clearer.

### 12.7 Artifacts

Normalize artifacts without moving their bytes.

    AgentArtifact:
      artifact_id: string
      run_id: string
      name: string
      media_type: string
      size_bytes: integer optional
      source_kind: filestore | workspace | provider | transcript | report
      source_ref: opaque server-side reference
      digest: sha256 optional
      created_at: timestamp
      downloadable: boolean
      expires_at: timestamp optional

The API returns signed or permission-checked access only when requested. It never exposes relay host paths, secrets, or provider credentials.

### 12.8 Retention

Each adapter remains the source of truth for retention. The registry reports retention metadata and never promises a result after its owner has expired it.

Standard minimums:

- live runs always visible;
- last 100 finished delegate/flash results remain as today unless changed explicitly;
- workflow runs follow WorkflowRunStore retention;
- external runs report provider retention or unknown;
- artifact expiry is visible.

### 12.9 UI

Add an Agent Runs panel with:

- filters by conversation, agent, runtime, and status;
- live state;
- exact run id;
- follow-up/interrupt where supported;
- targeted cancel;
- retry only with a proven safe capability;
- event timeline;
- cost/usage;
- artifacts;
- failure taxonomy;
- link to workflow run inspector when applicable.

This panel replaces scattered status presentation gradually; it does not remove the conversation transcript.

## 13. Feature E — Resource provenance and skill invocation policy

### 13.1 Resource identity

Add a normalized resource reference:

    ResourceRef:
      schema_version: 1
      resource_type: skill | agent | agent_group | flow | tool | service
      name: string
      scope: global | user | conversation
      owner_id: string or null
      package_id: string or null
      package_version: string or null
      version: string or null
      content_digest: sha256
      source_id: string

For flows, `name` is the canonical exact `package.flow:version` FQN. Package is origin/ownership metadata, not a repository scope. Internal `conv` scope strings are normalized to `conversation` at this contract boundary.

`owner_id` is null only for the global scope; user and conversation resources require the actual owner id. <code>source_id</code> is an opaque stable storage identity. The tuple plus digest identifies the exact content used at assignment or run start.

### 13.2 Origin metadata

Every resolved resource exposes:

    ResourceOrigin:
      kind: built_in | user_authored | conversation_authored |
            pfp_package | marketplace | learned | imported
      scope: global | user | conversation
      publisher: object or null
      package: object or null
      signature_status: verified | unverified | not_applicable
      review_status: trusted | reviewed | pending | rejected
      installed_at: timestamp optional
      updated_at: timestamp
      content_digest: sha256
      mutable: boolean

Server-owned fields cannot be written by <code>manage_resource</code> input.

### 13.3 Skill assignment v2

Replace ambiguous string assignments with objects:

    AssignedSkill:
      schema_version: 2
      ref: ResourceRef
      params: object
      condition: string optional
      invocation_policy_override: auto | explicit_only | disabled | null
      assigned_at: timestamp
      assigned_by: user id
      assignment_digest: sha256

Migration is expand/validate/activate. It resolves every existing string or legacy object using the exact precedence and resolver behavior active before migration, then stores the resulting <code>ResourceRef</code>. A duplicate name across scopes is not ambiguous when the current resolver deterministically selects one; the selected content and digest are pinned.

If any active assignment is missing, cannot be resolved reproducibly, or changes between preflight and activation, the affected conversation migration unit is not activated and all of that conversation's legacy assignments remain in use. Activation markers are per conversation so legacy and v2 entries are never mixed within one runtime roster. The report and UI show the blocker. New v2 bindings never silently rebind later.

After migration, remove legacy string parsing from runtime resolution.

### 13.4 Invocation policy

Skill definitions declare:

- <code>auto</code>: model may select it when its manifest matches;
- <code>explicit_only</code>: available only after user mention, explicit UI invocation, workflow binding, or a direct <code>load_skill</code> request that carries user intent;
- <code>disabled</code>: assigned for configuration/history but unavailable to the runtime.

Effective policy is the strictest of:

- resource definition;
- package trust policy;
- conversation policy;
- agent assignment override;
- runtime kind;
- current policy gate.

A model cannot loosen the policy.

### 13.5 Resolution precedence

An assignment does not perform name-only precedence at invocation. It resolves its exact ref.

For new interactive assignment selection, display candidates in this order:

1. conversation-authored;
2. user-authored;
3. installed package;
4. global built-in.

This is only UI ordering, not runtime rebinding.

If exact content changes:

- user-authored mutable resource: mark assignment update available and require explicit accept when digest changes materially;
- package resource: package upgrade presents assignment impact before activation;
- built-in resource: release migration updates refs with a changelog;
- active turn: keep its snapshotted digest until the next turn.

### 13.6 Imported and live sources

Do not dereference mutable remote instructions at invocation time.

Imports must:

1. fetch through the approved import/review path;
2. store a reviewed snapshot;
3. record source URL and digest;
4. require an explicit update to fetch a new version;
5. show the diff and trust change;
6. atomically update assignments only after acceptance.

This avoids supply-chain substitution and keeps runs reproducible.

### 13.7 Package integration

Extend PFP manifests and installed records with:

- exported resource refs;
- content digests;
- invocation policy;
- declared helper/runtime assets;
- declared tool effects;
- publisher/signature facts;
- dependency ownership.

On package update:

- compute changed/removed resource refs;
- list affected agent assignments and groups;
- refuse activation if required bindings disappear;
- migrate only through declared package migration steps;
- preserve the previous installed version until validation succeeds.

On uninstall:

- mark dependent assignments broken before deleting bytes;
- require confirmation listing affected agents/groups/flows;
- never silently bind a same-name resource from another scope.

### 13.8 Runtime prompt and load behavior

Available-skill manifests include:

- exact name;
- concise description;
- origin badge;
- invocation policy;
- immutable ref token.

<code>load_skill</code> resolves the ref assigned to the current agent, not a fresh name lookup.

The full prompt records the digest in turn metadata for reproducibility and audit. It does not expose private publisher metadata or local storage paths to the model.

### 13.9 UI

Resource and agent dialogs show:

- origin;
- scope;
- package/version;
- publisher/signature;
- digest short form;
- invocation policy;
- review state;
- agents/groups using the resource;
- update available/broken binding status.

Assignment selection stores an exact ref. Duplicate names are never visually collapsed.

## 14. Cross-cutting capability model

Tool authorization, group participants, workflow tasks, PFP tools, and agent-run mutations need one structured effect taxonomy.

Extend <code>ToolHandler</code> and workflow task metadata:

    effects:
      - filesystem.read
      - filesystem.write
      - process.execute
      - network.read
      - network.write
      - browser.observe
      - browser.control
      - desktop.observe
      - desktop.control
      - messaging.send
      - resource.read
      - resource.write
      - secret.use
      - secret.write
      - agent.spawn
      - agent.control
      - workflow.execute
      - external.side_effect

Metadata:

- <code>read_only</code>;
- <code>destructive</code>;
- <code>idempotency</code>: pure | natural | run_cached | keyed_effect | unsafe;
- <code>open_world</code>;
- <code>authorization_target_kind</code>;
- <code>workflow_safe</code>;
- <code>group_safe</code>.

Unknown effects fail closed. MCP annotations can only tighten display or inform review; they cannot grant capabilities.

`read_only` and `destructive` are validated derived summaries, not independent claims that may contradict `effects`. A mismatch fails schema validation. The shared idempotency enum is used by both ToolHandler and workflow task metadata.

Before structured-effect enforcement can be activated for an existing runtime, a generated coverage manifest must prove that every currently exposed built-in handler, alias, lazy wrapper target, dynamic-tool class, MCP/PFP adapter, and secondary-runtime bridge has explicit metadata or an explicit internal-plumbing classification. Until that activation marker exists, unconfigured conversations use the current ToolApprovalGate behavior. After activation, an unknown or contradictory declaration fails closed.

## 15. Data ownership and storage

| State | Source of truth | Durable |
|---|---|---|
| Conversation messages | ConversationStore transcript | yes |
| Agent roster | ConversationStore <code>conv_agents</code> | yes |
| Turn epoch | AgentInboxStore or authorization SQLite | yes |
| Pending approval | ToolAuthorizationService | no |
| Turn/call grant | ToolAuthorizationService | no |
| Conversation grant | ToolAuthorizationStore | yes |
| Approval audit | ToolAuthorizationStore | bounded yes |
| In-flight execution | ToolRelay registry / runtime adapter | no |
| Live tool lifecycle | ToolLifecycleStore | bounded no |
| Completed tool rows | transcript | yes |
| Delegate/flash result | existing executor registries | current retention |
| Workflow run | WorkflowRunStore | yes |
| Unified run projection | adapter view | no duplicate |
| Group definition | ResourceStore/PFP | yes |
| Group run | WorkflowRunStore | yes |
| Skill content/origin | ResourceStore/PFP installed record | yes |
| Exact assignment | <code>conv_agents.assigned_skills</code> v2 | yes |

## 16. API surface

### 16.1 New internal services

- <code>AgentTurnIdentityService</code>;
- <code>ToolAuthorizationService</code>;
- <code>ToolLifecycleService</code>;
- <code>AgentRunRegistry</code>;
- <code>ResourceIdentityResolver</code>;
- group workflow tasks.

### 16.2 New or changed actions

- <code>resolve_tool_approval</code>;
- <code>list_tool_grants</code>;
- <code>revoke_tool_grant</code>;
- <code>get_tool_lifecycle_snapshot</code>;
- <code>list_agent_runs</code>;
- <code>get_agent_run</code>;
- <code>control_agent_run</code>;
- <code>list_agent_run_artifacts</code>;
- <code>validate_agent_group</code>;
- <code>run_agent_group</code>;
- resource detail and assignment actions returning exact refs.

### 16.3 Compatibility

During one migration release:

- old approval resolution action delegates to the new service only for requests created by that process;
- old delegate status/result tools delegate to AgentRunRegistry;
- old assigned-skill forms are migrated before runtime use;
- clients that do not understand lifecycle envelopes continue to receive existing coarse tool call/result events.

After the migration release:

- remove legacy permission maps;
- remove legacy skill assignment parsing;
- remove duplicate run-status implementations;
- keep stable user-facing tools where they remain useful.

## 17. Event contracts

All new events carry:

- <code>schema_version</code>;
- <code>event_id</code>;
- <code>conversation_id</code>;
- <code>agent_instance</code>;
- <code>timestamp</code>;
- correlation ids appropriate to the event.

For SSE transport, the same `event_id` is assigned to `SSEEvent.id` and retained in the data envelope. Replayed or duplicated delivery therefore has one deduplication identity without changing legacy event IDs.

New event families:

- tool lifecycle;
- approval;
- agent run;
- group run;
- resource binding.

Sensitive fields are redacted at event construction, not in each client.

ConversationEventBus remains a transport. It must not own authorization or tool state.

## 18. Observability

### 18.1 Metrics

Authorization:

- approval requests by effect/tool/outcome;
- decision latency;
- expired and stale resolutions;
- reused grants by scope;
- forced re-approvals;
- denial-loop suppressions;
- authorization saturation;
- missing metadata denials.

Lifecycle:

- invalid transitions;
- stale epoch events;
- duplicate events;
- orphan results;
- reconnect snapshots;
- live-state evictions;
- transcript reconciliation latency;
- terminal publication conflicts.

Groups:

- runs, rounds, participants, passes;
- token/cost/time budgets;
- selection mode;
- stop reason;
- participant failures;
- synthesis failures;
- cancellation latency.

Agent runs:

- active runs by runtime kind;
- follow-up/cancel/retry outcomes;
- artifact counts and expiry;
- adapter errors.

Resources:

- assignments by origin/policy;
- broken bindings;
- digest changes;
- package update impacts;
- explicit-only invocations;
- rejected stale refs.

### 18.2 Tracing

Propagate:

- conversation id;
- agent instance;
- turn id;
- turn epoch;
- run generation;
- run id;
- tool-call id;
- request id;
- execution epoch;
- group run/round/member;
- resource digest.

Never attach raw prompts, secrets, unredacted arguments, provider reasoning, or file contents by default.

### 18.3 Audit events

Security audit records include:

- approval requested/resolved/expired;
- grant created/used/retired/revoked;
- stale resolution attempt;
- invalid lifecycle transition;
- group invocation and budget;
- run control action;
- resource ref changed or broken;
- package update affecting assignments.

## 19. Failure taxonomy

Stable machine-readable error codes:

Authorization:

- <code>approval_required</code>;
- <code>approval_unavailable</code>;
- <code>approval_expired</code>;
- <code>approval_stale_turn</code>;
- <code>approval_stale_generation</code>;
- <code>approval_target_mismatch</code>;
- <code>approval_policy_changed</code>;
- <code>authorization_metadata_missing</code>;
- <code>authorization_saturated</code>;
- <code>tool_denied</code>.

Lifecycle:

- <code>tool_event_stale_epoch</code>;
- <code>tool_event_duplicate_conflict</code>;
- <code>tool_event_out_of_order</code>;
- <code>tool_result_orphan</code>;
- <code>tool_terminal_conflict</code>.

Groups:

- <code>group_invalid_members</code>;
- <code>group_nested_not_supported</code>;
- <code>group_budget_exhausted</code>;
- <code>group_required_member_failed</code>;
- <code>group_synthesis_failed</code>;
- <code>group_cancelled</code>;
- <code>group_superseded</code>.

Runs/resources:

- <code>run_not_found</code>;
- <code>run_action_unsupported</code>;
- <code>run_action_not_authorized</code>;
- <code>resource_ref_missing</code>;
- <code>resource_digest_mismatch</code>;
- <code>resource_binding_broken</code>;
- <code>resource_invocation_disabled</code>.

Errors shown to users remain concise; details go to structured logs and inspectors.

## 20. Concurrency and race handling

Required race tests and behavior:

- approval resolves exactly as timeout fires: one terminal status;
- new turn begins while old approval is pending: old request retires and cannot grant;
- force stop races with tool completion: one terminal lifecycle event, no error transcript;
- provider tool-call id arrives after cancel: binding cannot revive the call;
- reconnect races with terminal transcript persistence: snapshot plus transcript yields one row;
- duplicate result from provider: idempotent if identical, protocol failure if conflicting;
- policy changes while request pending: re-evaluate before settlement;
- package updates while a turn uses a skill: current turn keeps snapshot, next turn sees update/broken state;
- group member definition updates mid-run: active run keeps member snapshot;
- group cancellation during parallel calls: all children receive cancellation and no later post is accepted;
- delegate completion races with follow-up: adapter applies existing executor semantics and reports the resulting run generation.

## 21. Security analysis

### 21.1 Confused deputy

All resolution and control actions derive ownership from server-side ids. Conversation, agent, user, relay, and service identifiers supplied beside a request id are display hints only.

### 21.2 Replay

Approval request ids are single-use. Tool lifecycle events use execution epochs and monotonic sequences. Resource assignments use content digests. Group participant outputs include run/round/member identities.

### 21.3 Prompt injection

Prompts cannot:

- expand budgets;
- add members;
- change tool policy;
- widen invocation policy;
- forge event identity;
- choose an approval outcome;
- commit a terminal transcript row;
- mutate a resource ref.

### 21.4 Supply chain

Remote/live skill instructions are snapshotted and reviewed. PFP resources are tied to installed package version, digest, and signature state. Same-name fallback is forbidden.

### 21.5 Privacy

Group calls receive only explicit shared-room context. Private transcript, memories, diary, and credentials are excluded mechanically. Artifact and run APIs check actual ownership.

### 21.6 Denial of service

Every registry, replay, group, progress stream, refusal memory, audit log, and artifact listing is bounded. Approval spam is suppressed per turn.

## 22. Migration strategy

### 22.1 Database and metadata migration

Use idempotent feature-specific expand/validate/activate commands before accepting traffic for the affected feature. Authorization/grants and resource bindings have separate activation markers so one cannot strand the other.

1. create dormant authorization, identity, and resource metadata tables/fields;
2. inventory and preflight persistent permission maps and assigned skills;
3. preserve live session grants only in the legacy process until restart; never persist them as broader grants;
4. resolve assigned skills to exact v2 refs using current resolver behavior;
5. stop and leave the legacy feature active if any required assignment cannot be reproduced;
6. add resource origin metadata and verify content digests;
7. shadow-evaluate authorization decisions and resource resolution;
8. write the relevant activation marker only after consistency checks pass;
9. retain rollback snapshots until the first post-activation write;
10. only then enable the corresponding new runtime path.

Migration writes a report without secrets.

### 22.2 Client rollout

Order:

1. server accepts both old and new clients;
2. ship lifecycle-aware webchat;
3. ship PawCode and VS Code support;
4. make new events default;
5. remove legacy coarse state inference after client compatibility window.

Old clients may see completed call/result rows but not rich live states. They must not be able to approve a request with the wrong scope.

### 22.3 Group rollout

- first ship resource validation and a fake-provider workflow;
- then tool-free deliberation;
- then read-only tools;
- only later consider declared mutating tools;
- no nested groups in this plan.

### 22.4 Run registry rollout

Adapters land one at a time. The registry returns partial runtime coverage with explicit adapter availability. Do not block existing delegate or task tools on completion of every adapter.

### 22.5 Cross-plan delivery dependencies

- The existing policy-gating pipeline remains live throughout WP1-WP3; WP2 extends it and cannot replace it with an independent evaluator.
- Workflow agents may adopt shared turn identity as defined here, but `WORKFLOW_AGENTS_IMPLEMENTATION_PLAN.md` owns durable inbox and terminal recovery.
- A workflow adapter for AgentRunRegistry requires an authoritative WorkflowRunStore.
- Group WP6 requires the workflow plan through its durable WP7 plus this plan WP0-WP4; group read-only tools additionally require this plan WP7.
- The group flow FQN uses PawFlow's existing `package.flow:version` syntax.
- ResourceRef must land before new workflow/group bindings are generally authorable; temporary experimental bindings may use an internal adapter only if it serializes the same final ResourceRef schema.

## 23. Implementation work packages

### WP0 — Characterization and contracts

Deliver:

- tests pinning current approval, cancellation, background, hydration, SSE replay, delegate status/result, skill assignment, and scope precedence;
- tests pinning current policy-gating decisions, AuthorizationRef revision behavior, and the no-binding `legacy` path;
- schemas for turn identity, authorization request/grant, lifecycle event, agent run, group, participant post, resource ref/origin, and assigned skill v2;
- effect taxonomy and metadata contract;
- architecture decision recorded here.

Gate:

- no behavior change;
- unknown schema versions and missing identities fail closed;
- full focused suites pass.

### WP1 — Turn identity and structured effects

Deliver:

- <code>AgentTurnIdentityService</code> integrated with existing stamped turn IDs and AuthorizationRef;
- durable epoch allocator;
- runtime adapter propagation;
- mandatory tool-call id before authorization;
- <code>ToolExecutionContext</code>;
- handler/task effect metadata;
- complete effect-metadata coverage manifest for every existing exposed handler and adapter;
- lazy-wrapper final-handler resolution integration.

Gate:

- every LLM, delegate, flash, workflow-test, MCP, PFP, and external-tool path has correlation ids;
- concurrent accepted turns allocate distinct monotonic epochs;
- on the activated structured-effect path, no tool dispatch without identity and metadata; before activation, unconfigured conversations retain the characterized `ToolApprovalGate` path.
- enforcement cannot activate while the coverage manifest has an unclassified existing tool path.

### WP2 — Authorization service

Deliver:

- SQLite store;
- extension of existing <code>core/tool_authorization.py</code> plus behavior-compatible ToolApprovalGate/public-function adapters;
- exact matching;
- request expiry and retirement;
- refusal memory and saturation;
- force-stop/new-turn cleanup;
- one-shot permission migration;
- new approval actions and UI cards.

Gate:

- stale and cross-agent approvals fail;
- protected/catastrophic actions never reuse broad grants;
- no UI subscriber fails closed;
- no pending waiter leaks;
- all approval scopes behave as displayed.
- with no new grant/policy metadata configured, every existing tool decision matches the baseline fixture.

### WP3 — Tool lifecycle envelope

Deliver:

- lifecycle service and state machine;
- execution epoch and sequence allocation;
- relay integration;
- normalized terminal publication;
- snapshot action;
- webchat/PawCode/VS Code projections;
- transcript reconciliation.

Gate:

- reconnect at every phase renders one correct state;
- stale epochs and orphan results never pair;
- force-stop/completion races produce one terminal;
- lifecycle buffers remain bounded.

### WP4 — Resource identity and skill policy

Implementation note (2026-08-24): assigned-skill v2 now expands legacy names
to exact `ResourceRef` identities, validates digests and visibility, and writes
one per-conversation activation marker through a serialized, idempotent
preflight/activate path. Runtime selection requires both that marker and the
disabled-by-default `PAWFLOW_RESOURCE_BINDINGS_V2_ENABLED` server flag. Broken
or duplicate assignments block the whole activation; changed rosters and stale
resource identities fail closed. `explicit_only` and `disabled` assignments are
absent from both the model manifest and `load_skill` path, while an
`explicit_only` skill remains available through explicit user invocation.

Deliver:

- ResourceRef and ResourceOrigin;
- assigned skill v2;
- expand/validate/activate assignment migration;
- exact runtime resolution;
- invocation policies;
- ResourceStore and PFP origin metadata;
- package update/uninstall impact checks;
- UI badges and broken-binding repair.

Gate:

- duplicate names cannot substitute content;
- digest changes are visible;
- explicit-only and disabled policies are enforced;
- package removal cannot silently rebind;
- active turns remain reproducible.
- an unresolved active legacy assignment blocks activation instead of breaking the agent.

### WP5 — AgentRunRegistry

Deliver:

- registry protocol;
- delegate and flash adapters first;
- task, workflow, plan, A2A, external adapters;
- normalized artifact model;
- general API/actions;
- Agent Runs UI.

Gate:

- ownership is derived from target run;
- unsupported actions fail explicitly;
- targeted cancel never widens to a bystander;
- existing named tools remain behaviorally compatible;
- no artifact path or secret leakage.

### WP6 — Group resource and tool-free vertical slice

Deliver:

- <code>agent_group</code> resource/PFP schema;
- validators and conversation binding;
- group-deliberation flow;
- participant call without tools/private context;
- deterministic selection, rotation, pass, stop, synthesis;
- run inspector.

Gate:

- hard budgets cannot be exceeded;
- no nested groups;
- no private transcript leakage;
- exactly one terminal assistant row;
- cancellation stops all participants;
- fake-provider golden runs are deterministic.

### WP7 — Group read-only tools and policy integration

Implementation note (2026-08-24): the first reviewed runtime slice exposes only
filesystem observation (`read`, `list_dir`, `stat`, `exists`, `glob`, `grep`,
`search`) and `web_search`. The effective set is intersected with each immutable
member snapshot and the group's declared read-only effects. Private context,
memory, diary, history, messaging, delegation, browser control, process execution,
and mutation remain unavailable. Every attempted call emits a redacted workflow
lifecycle event attributed to group run, workflow run, round, member, turn, and
tool call. Policy decisions use the same structured authorization engine while
keeping the real conversation agent identity stable. Approval requests carry
the structured group attribution separately and are retired promptly when the
owning workflow cancellation event is set.

Deliver:

- group-safe capability intersection;
- tool authorization attribution to group member/run/round;
- read-only tool lifecycle projection;
- budget accounting including tools;
- security tests for target and scope escape.

Gate:

- mutating or unknown tools are rejected;
- participant tool approvals show full group attribution;
- group cancellation retires approvals and tools;
- read-only cannot escape relay, conversation, service, or filesystem scope.

### WP8 — Hardening, migration, and release

Implementation note (2026-08-24): the local migration slice includes redacted
inspect/migrate/rollback operator actions, concurrent activation, failed-write
fault injection, stale-roster detection, exact-content checks, and the
rollback fence at the first v2 write. The focused migration and skill-loader
gate is green. The full manual webchat, PawCode, VS Code, channel, MCP, PFP,
delegate, workflow, and group matrix remains an external rollout gate; legacy
reads must not be removed before that evidence and a compatibility release.

Deliver:

- concurrency and fault-injection suite;
- metrics, traces, dashboards, and alerts;
- operator inspection/repair commands;
- complete client rollout;
- documentation updates;
- migration report and rollback-before-activation procedure;
- removal of legacy reads after the compatibility release.

Gate:

- full CI passes;
- capability-disabled and unconfigured-conversation regression suites match the baseline behavior;
- no duplicate transcript rows in reconnect stress tests;
- no lost cancel/approval terminal state;
- migration is idempotent on production-shaped fixtures;
- manual end-to-end validation covers webchat, PawCode, VS Code, one channel, MCP, PFP, delegate, workflow, and group.

## 24. Proposed test matrix

### 24.1 Authorization unit tests

- epoch allocation and canonical agent resolution;
- exact grant matching;
- alias escalation;
- normalized paths and symlinks;
- relay/service target mismatch;
- AuthorizationRef and policy-snapshot invalidation;
- expiry;
- idempotent resolution;
- stale turn/generation;
- refusal memory and saturation;
- protected/catastrophic forced ask;
- no metadata fail-closed;
- legacy `always_allow` to conversation-grant migration.

### 24.2 Lifecycle unit tests

- every valid and invalid transition;
- event-id idempotency;
- sequence conflicts;
- stale execution epoch;
- reset behavior;
- orphan result quarantine;
- terminal immutability;
- bounded progress and eviction;
- snapshot high watermark.

### 24.3 Runtime integration

- native provider tool loop;
- Claude Code interactive;
- Codex/Gemini app server;
- MCP tool;
- PFP tool;
- lazy <code>use_tool</code>;
- background and result injection;
- targeted cancel;
- force stop;
- external AG-UI tool;
- workflow safe task.
- policy gate absent/allow/deny/ask across primary and secondary runtimes, including proof that reusable grants never bypass deny/ask.

### 24.4 Reconnect and multi-client

Reconnect:

- before call;
- waiting approval;
- after approval;
- during dispatch;
- during progress;
- after background;
- while cancelling;
- after terminal before transcript write;
- after transcript write;
- after process restart.

Run two clients with distinct client ids and replace one stale connection.

### 24.5 Group golden tests

- all members;
- explicit subset mention;
- ambiguous mention;
- every member passes;
- one required member fails;
- optional member fails;
- maximum rounds;
- maximum participant calls;
- token/cost/time exhaustion;
- deterministic ordering;
- parallel completion reordering;
- preempt;
- force stop;
- synthesis failure;
- private context exclusion;
- malformed participant output;
- read-only tool allowed/denied.

### 24.6 Resource and package tests

- legacy assignment migration;
- duplicate names across scopes;
- exact digest resolution;
- mutable update;
- PFP upgrade;
- PFP uninstall;
- missing package;
- signature/trust change;
- explicit-only invocation;
- disabled invocation;
- conditional assignment;
- active-turn snapshot;
- user rejects an update.

### 24.7 Security tests

- foreign approval id;
- foreign run id;
- forged conversation beside valid request id;
- stale provider event;
- target substitution after approval;
- arguments changed after digest;
- resource shadowing;
- group prompt tries to add member or tool;
- malicious skill source update;
- artifact path traversal;
- unknown MCP annotation;
- lifecycle event content leaking secrets.
- capability flags or migration markers forged in client/model arguments.

## 25. Operational controls

Operators need:

- list pending approvals and age;
- retire a stranded request;
- list/revoke grants by user/conversation/agent/scope;
- inspect turn epoch and generation;
- inspect live tool lifecycle by exact ids;
- quarantine a provider/relay producing invalid sequences;
- list active agent runs;
- targeted cancel;
- inspect broken resource assignments;
- preview package upgrade impact;
- inspect group budget and stuck participants;
- compact authorization audit and terminal lifecycle state.

Alerts:

- stale pending approval above TTL;
- repeated stale resolution attempts;
- authorization saturation;
- orphan tool results;
- terminal conflicts;
- invalid event sequence burst;
- tool transcript reconciliation delay;
- group run without progress;
- repeated group budget exhaustion;
- broken required skill binding;
- package update affecting active assignments.

## 26. Documentation deliverables when implementation ships

Update in the same implementation changes:

- <code>AGENT_SYSTEM.md</code>;
- <code>architecture.md</code>;
- <code>tool_catalog.md</code>;
- <code>TOOL_SELECTION.md</code>;
- <code>tasks.md</code>;
- <code>02_REFERENCE_TASKS_SERVICES.md</code>;
- <code>PFP_PACKAGES.md</code>;
- <code>PFP_DEVELOPER_GUIDE.md</code>;
- <code>marketplace.md</code>;
- <code>security_model.md</code>;
- <code>OBSERVABILITY.md</code>;
- <code>WORKFLOW_AGENTS_IMPLEMENTATION_PLAN.md</code>;
- webchat, PawCode, VS Code, MCP, and AG-UI protocol references;
- a group deliberation example and operator runbook;
- project wiki pages for every shipped subsystem.

Do not mark this plan implemented until the acceptance criteria below are met.

## 27. Risks and mitigations

### Authorization complexity

Risk: target matching becomes inconsistent across handlers.

Mitigation: one structured handler contract, shared normalizers, fail-closed defaults, and per-handler golden tests.

### Too many identities

Risk: turn id, epoch, generation, request id, tool-call id, and execution epoch are confused.

Mitigation: one identity module, typed dataclasses, explicit lifecycle diagrams, structured logging, and no overloaded ids.

### Duplicate state

Risk: transcript, lifecycle store, relay registry, and UI each become authoritative.

Mitigation: document ownership table, make lifecycle transient, and reconcile terminal state to transcript.

### Approval fatigue

Risk: exact scoping creates too many dialogs.

Mitigation: safe turn grants, precise target matchers, refusal suppression, useful card text, and policy-configurable low-risk scopes.

### Group cost explosion

Risk: rounds multiplied by members create unbounded spend.

Mitigation: hard participant-call, token, cost, time, parallelism, and message caps enforced by the coordinator.

### Group privacy leakage

Risk: participant agents receive private history.

Mitigation: construct group context from explicit shared data only and test exclusions mechanically.

### Hidden nested agents

Risk: participant calls restore general AgentLoop recursion.

Mitigation: a dedicated structured LLM task with no private context or tools by default; AgentLoopTask remains unsafe for group/workflow version 1.

### Resource lock-in or stale refs

Risk: immutable refs make updates cumbersome.

Mitigation: impact preview, explicit atomic update, broken-binding repair UI, and package migration hooks.

### Large migration blast radius

Risk: permissions and assignments touch live conversations.

Mitigation: idempotent offline migration, production-shaped fixtures, report-only preview, activation marker, and refusal to activate a migration unit that would break a currently usable binding.

## 28. Release acceptance criteria

The feature set is complete only when all statements are true.

1. Every accepted turn has one immutable non-empty logical identity projected consistently into existing transport/workflow fields, a monotonic per-agent epoch, and run generation.
2. Every executable call has a canonical handler identity, tool-call id, arguments digest, structured target, and declared effects.
3. An approval from another conversation, agent, turn, generation, call, target, relay, service, AuthorizationRef, or policy snapshot cannot authorize execution.
4. A new turn, restart, or force stop retires stale pending approvals and ephemeral grants.
5. Protected and catastrophic actions always require an exact fresh confirmation.
6. Approval resolution is idempotent and derives ownership from server state.
7. Every tool lifecycle is ordered by execution epoch and sequence.
8. Orphan, stale, out-of-order, or conflicting events cannot produce a successful UI result.
9. Reload and reconnect at every lifecycle phase produce one accurate tool row.
10. The transcript remains authoritative for completed calls and results.
11. Existing targeted cancel, background, kill-hook, force-stop, and next-turn invariants remain correct.
12. A user can create and run a bounded group of existing agents without editing JSON.
13. Group runs enforce membership, rounds, messages, parallelism, tokens, cost, and time mechanically.
14. Group participants receive no private one-to-one context.
15. A group run creates exactly one terminal assistant response.
16. Tool-free and read-only group modes pass security and cancellation tests.
17. Delegate, flash, task, workflow, plan, A2A, and external runs appear through one normalized read/control API as their adapters land.
18. Run control actions authorize against the actual target run.
19. Artifacts are listed without exposing raw provider or relay paths.
20. Every assigned skill resolves to an exact origin, scope, version/package identity, and digest.
21. Duplicate resource names cannot silently change an assignment.
22. Explicit-only and disabled skill policies are enforced by the runtime.
23. Package update and uninstall show impact and cannot silently rebind resources.
24. All registries and replay structures are bounded and observable.
25. Migration succeeds idempotently on production-shaped fixtures.
26. Existing agent, tool, MCP, PFP, SSE, client, and workflow regressions pass.
27. Documentation, security model, operator controls, and project wiki pages are updated.
28. A reusable grant cannot bypass a policy-gate deny/ask, hard deny, hard confirm, permission mode, ownership check, or changed prepared call.
29. With every new feature disabled or unconfigured, existing runtime dispatch, tool decisions, transcript/SSE behavior, skill loading, package resolution, and clients match baseline fixtures.
30. Failed authorization or resource migration preflight leaves the corresponding legacy path active and unmodified; after activation there is one source of truth.

## 29. Recommended delivery order

The smallest security-complete delivery is WP0 through WP3:

- turn identity;
- structured effects and targets;
- scoped authorization;
- ordered lifecycle;
- reconnect support.

WP4 should follow before expanding marketplace/package automation because immutable resource identity closes a separate supply-chain gap.

WP5 can ship incrementally by adapter.

WP6 provides the first user-visible group deliberation with no participant tools only after Workflow Agents WP7 and this plan WP0-WP4 are production-ready. WP7 adds read-only tools only after the authorization and lifecycle layers are proven in production.

General availability requires WP0 through WP8.

## 30. Final product examples

### Exact approval

The Research agent requests a filesystem write. The card says that the user is allowing one exact write, to one normalized path, through one relay, for the current tool call. A later turn or changed path requires a new decision.

### Reliable reconnect

A browser reconnects while a long tool is backgrounded. It loads the transcript, receives the current execution epoch and lifecycle snapshot, and renders the existing backgrounded call once. When the result is persisted, the live terminal state reconciles with that same tool-call row.

### Group deliberation

The user invokes a Security Review group containing Architecture, Threat Model, and Operations agents. The workflow snapshots all three definitions, runs at most two rounds under a fixed budget, records passes and dissent, then commits one synthesis. No participant receives private direct-message history.

### Skill provenance

An agent is assigned a package skill. The UI shows package id, version, publisher, signature, digest, and explicit-only policy. A package update changes the digest; PawFlow shows affected agents and requires an explicit atomic upgrade instead of silently changing the next turn.

# Prompt-Aware Policy Gating Service Implementation Plan

Status: **approved for implementation** (reviewed 2026-08-21; review amendments
A1–A8 are folded into the sections below and marked `[A#]`).

## 1. Outcome

PawFlow should add an optional policy-gating layer that evaluates every relevant
tool call against the authority granted by the user who initiated the work.

The authority is normally the user's natural-language request:

> Fix the AG-UI issue, run the targeted tests, and push a normal commit to
> `main`. Do not create a release.

That request is a plan in the ordinary sense. It does not need to be a PawFlow
`PlanStore` object. A structured PawFlow plan may be supplied as additional
context, but the gating design must work when the only plan is one or more
authenticated user messages.

A configured gate receives:

1. the immutable policy prompt owned by the gating service;
2. the versioned user-authority context for the current work lineage;
3. the normalized, secret-redacted tool call about to execute;
4. optional deterministic results from associated policy scripts.

It returns exactly one external decision:

- `allow`: the call conforms to the active user mandate;
- `deny`: the call contradicts or exceeds that mandate;
- `ask`: the mandate is ambiguous and the user must confirm.

The gate is not a replacement for PawFlow's structural security controls.
Read-only restrictions, explicit denies, transport constraints, catastrophic
command checks, protected-path checks, ownership checks, and handler-local
validation remain authoritative and cannot be weakened by a gate.

## 2. Decision summary

The following decisions are binding for the first implementation.

1. Introduce a native service type named `gating`.
2. A gating service may use a policy prompt, one or more policy scripts, or
   both. At least one evaluator must be configured.
3. `llm_service` references a direct `llmConnection` and is required whenever
   the gating service has a non-empty policy prompt. A script-only gate does not
   make an LLM call.
4. Installing a gating service has no behavioral effect by itself. A gate is
   active only through an explicit conversation or agent binding.
5. A conversation binding applies to every agent in that conversation. An
   agent binding is additional and may tighten, but never bypass, the
   conversation gate.
6. The dynamic authority source is authenticated user input, not assistant
   prose, tool results, web content, files, or messages created by another
   agent.
7. Every new root user request creates an `AuthorizationContext`. A user
   correction or steering message revises the active context. Delegations,
   sub-agents, scheduled continuations, and external runtimes inherit an
   explicit context identifier and revision.
8. The implementation must never discover authority by scanning for the latest
   conversation messages at tool-execution time. Concurrent work lineages make
   that ambiguous and unsafe.
9. Every relevant operational tool call is gated, including calls that the
   legacy approval system would treat as exempt or that run in `auto` mode.
   A very small explicit internal-plumbing allowlist prevents recursion.
10. The gate runs after aliases, MCP wrappers, pre-tool hooks, expression
    resolution, and preparation have produced the effective call, but before
    execution. Nothing may rewrite the call after it was gated.
11. Real arguments are used by PawFlow's deterministic security checks.
    Gating scripts and the gating LLM receive a separately redacted copy that
    contains no resolved secret values.
12. Associated scripts run in the existing relay-backed sandbox model, never
    inside the PawFlow server process.
13. Script and LLM decisions compose conservatively:
    `deny > ask > allow > abstain`. If no evaluator reaches a decision, the
    external result is `ask`.
14. Failure, timeout, unavailable service, malformed output, stale authority
    revision, or missing authority context is never converted into `allow`.
15. A gate's `allow` can satisfy the ordinary interactive approval that would
    otherwise be requested. It cannot satisfy an incompressible confirmation
    for catastrophic commands or equivalent hard-confirmation categories.
16. Gate decisions do not write `always_allow` or `session_allow` entries.
    Each call is evaluated against its own effective arguments and authority
    revision.
17. Every authorization context, evaluator result, and final decision has a
    UUID and creation timestamp. Audit storage contains redacted previews and
    hashes, never raw secrets.
18. With no binding, PawFlow's existing permission behavior remains unchanged.
19. `[A1]` Interim rule for the migration: a runtime that has not yet been
    wired to the central engine must fail closed — `ask` when it can prompt,
    otherwise `deny` — for any conversation or agent that has a gate bound.
    A bound gate is never silently bypassed by an unmigrated runtime.
20. `[A3]` Cost is a design constraint: a gating service declares an
    `llm_scope` (`mutating` by default) and the engine classifies calls so that
    pure reads are settled by static classification or scripts without an LLM
    round trip; LLM evaluations run with bounded concurrency and a latency
    budget that is part of the test matrix.

## 3. Terminology

### 3.1 User mandate

The natural-language instruction supplied by an authenticated user. It may be
one message or a root request followed by user-authored corrections.

Examples:

- "Inspect the installation; read only."
- "Fix these two files, run tests, and stop before committing."
- "Commit and push when CI is green, but do not tag or release."
- "Deploy to staging only. Production requires another confirmation."

### 3.2 Authorization context

A persisted, versioned snapshot of the user-authored directives governing one
work lineage. It is created at ingress and propagated explicitly.

### 3.3 Policy prompt

The stable instructions configured on a gating service. They define how the
gate interprets a mandate, for example:

> Permit only actions reasonably necessary for the active user request. Treat
> publication, deletion, external communication, privilege escalation, and
> scope expansion as unauthorized unless explicitly requested. Ask when the
> relationship is ambiguous.

### 3.4 Policy script

A sandboxed deterministic evaluator associated with a gating service. It can
recognize exact tools, arguments, paths, commands, hosts, resource scopes, or
other machine-checkable constraints.

### 3.5 Structural guard

A non-negotiable PawFlow security rule enforced independently of the gate:
read-only mode, explicit deny, transport restriction, ownership boundary,
catastrophic command rule, protected path, handler validation, or equivalent.

### 3.6 Relevant tool call

A user-visible operational action. Internal schema discovery, result plumbing,
cancellation bookkeeping, the approval dialog itself, and the gate's own
tool-free LLM completion are not recursively gated.

## 4. Current PawFlow architecture

### 4.1 Existing approval gate

`core/tool_approval.py` contains `ToolApprovalGate`, which currently owns:

- exempt and always-ask classifications;
- command-bearing aliases;
- dangerous and catastrophic command detection;
- protected-path detection;
- conversation-and-agent permission persistence;
- SSE approval requests;
- synchronous waiting for user responses;
- read-only and advisor-read-only allowlists.

The gate is called from several independent runtimes, including:

- `tasks/ai/agent_tool_exec.py`;
- `core/agent_executor.py`;
- `services/_tool_relay_execute.py`;
- `core/agui_client_runtime.py`;
- `services/_realtime_tools.py`.

Some callers implement `permission_mode`, per-tool overrides, and catastrophic
handling around `ToolApprovalGate.check()` themselves. A new policy gate
cannot be added safely by patching only one caller or by inserting logic only
inside the current interactive dialog branch.

### 4.2 Existing tool-call transformation order

The primary AgentLoop path already enforces an important invariant:

1. unwrap the effective MCP/tool alias;
2. run `pre_tool_call` hooks;
3. apply hook replacements;
4. resolve variables and secrets;
5. prepare the effective registry call;
6. authorize;
7. execute without another rewrite.

The relay path currently performs some secret/variable work after its approval
branch. The policy-gating implementation must normalize all runtimes around one
canonical order while preserving secret redaction.

### 4.3 Existing summarizer precedent

`services/summarizer_service.py` and `core/summarizer_bindings.py` provide a
useful service-and-binding precedent:

- a composite service references a direct `llmConnection`;
- conversation extras store an explicit scoped service reference;
- helpers list, resolve, and summarize the effective binding;
- the Resources UI exposes service creation and conversation linking.

Gating must reuse the same scoped-service primitives, but it must not reuse the
summarizer's implicit "first available service" fallback. Merely installing a
global security policy must never silently activate it in every conversation.

### 4.4 Existing agent hooks

`core/agent_hooks.py` already provides:

- conversation-bound hook resources;
- event, agent, and tool filters;
- priority ordering;
- fail-open/fail-closed behavior;
- PFP runtime invocation;
- source-hook execution through `ExecuteScriptHandler(destination="sandbox")`.

The execution substrate is reusable. The control protocol is not:
`agent_hook` defaults to `allow` and supports `allow|block|replace`, whereas
a policy evaluator must default to `abstain`, must never rewrite a tool call,
and must support `allow|deny|ask|abstain`.

### 4.5 Existing message provenance

Messages are stamped with UUIDs, timestamps, source metadata, conversation
identity, target agent, and turn identifiers. Plan orchestration already adds a
`plan_id` to message source metadata. That proves that execution provenance
can be transported, but the current tool authorization API does not carry a
general work-lineage authorization context.

### 4.6 Existing structured plans

`core/plan_store.py` and `core/handlers/plan_handlers.py` persist structured
plans by user and conversation. Their step descriptions may enrich a gating
request, but structured plans are optional inputs. The policy system must not
depend on `PlanStore`, plan status, or plan approval.

## 5. Goals

The implementation must:

1. enforce the authenticated user's actual request at tool-execution time;
2. support natural-language mandates without requiring a structured plan;
3. support deterministic script-only, LLM-only, and combined gates;
4. allow ordinary safe calls automatically when they clearly fit the mandate;
5. deny clear scope violations without interrupting the user;
6. ask the user when intent is ambiguous;
7. preserve PawFlow's existing hard security boundaries;
8. work across every tool-execution runtime;
9. propagate authority through delegation and asynchronous continuation;
10. remain correct with multiple agents and concurrent turns;
11. prevent an agent or tool result from expanding its own authority;
12. avoid sending secrets or unnecessary transcript content to the gating LLM;
13. produce inspectable reasons and a durable redacted audit trail;
14. preserve existing behavior when no gate is explicitly bound;
15. provide complete API, UI, CLI, documentation, and test coverage.

## 6. Non-goals

The first implementation will not:

- prove formal semantic equivalence between prose and actions;
- grant a gating LLM access to tools;
- send the full conversation transcript to the gate;
- replace handler-local authorization or validation;
- replace the current permission modes;
- automatically install or activate a global gate;
- let agents edit the gate that controls their own current execution;
- infer a work lineage by selecting the newest active conversation plan;
- persist an LLM's `allow` as a reusable permission;
- implement argument-level quotas or transactional multi-call budgets in V1;
- use gate confidence scores as security decisions;
- allow policy scripts to run inside the server process.

## 7. Threat model and trust boundaries

### 7.1 Trusted authority sources

The authorization context may include only data with explicit provenance:

- authenticated direct user messages;
- authenticated user steering/follow-up messages;
- the immutable gating-service policy prompt;
- installed policy scripts selected by the binding owner;
- optionally, an explicitly referenced PawFlow plan snapshot;
- optionally, a one-call user response to a gate-generated confirmation.

### 7.2 Untrusted data

The following are evidence or inputs, never authority:

- assistant messages and hidden reasoning;
- messages generated by delegates or reviewers;
- tool results;
- files, URLs, webpages, emails, documents, and retrieved memories;
- tool names and arguments emitted by the model;
- hook output;
- package-provided prose;
- external AG-UI/A2A content unless it entered through an authenticated user
  ingress policy explicitly allowed to create authority.

The LLM prompt must label these fields as untrusted data and must state that
instructions embedded in them do not modify the mandate.

### 7.3 Principal confusion

A shared conversation may contain participants with different rights.
`AuthorizationContext.user_id` is the authenticated principal that initiated
the work. Resolution of services, scripts, files, secrets, and audit records
must use the correct owner/caller identity already enforced by the runtime.

A participant may create a new mandate only through an ingress path on which
they are authorized to submit work. They cannot mutate another principal's
context by writing transcript-like data through a tool.

### 7.4 Self-modifying policy

Calls that install, edit, bind, unbind, enable, disable, or delete:

- the effective gating service;
- one of its policy scripts;
- its referenced LLM service;
- the active authorization context or audit store

must always require explicit user confirmation and must never be auto-approved
by the policy being changed.

### 7.5 Prompt injection

The gating LLM request must use:

- a system-owned protocol instruction;
- the configured policy prompt in a dedicated policy field;
- an immutable authority envelope;
- a separate untrusted tool-call envelope;
- no tools;
- strict JSON output;
- low output budget and deterministic temperature.

Tool arguments must never be concatenated into the policy prompt as executable
instructions.

## 8. Domain model

### 8.1 `GatingService`

Create `services/gating_service.py`:

```python
class GatingService(BaseService):
    TYPE = "gating"
    VERSION = "1.0.0"
    NAME = "Policy Gating Service"
```

Proposed parameter schema:

| Field | Type | Required | Default | Meaning |
|---|---|---:|---|---|
| `llm_service` | `service_ref(llmConnection)` | when prompt is set | `""` | LLM used for policy evaluation |
| `prompt` | multiline string | no | `""` | Stable policy and interpretation rules |
| `scripts` | `resource_ref_list(gating_script)` | no | `[]` | Ordered deterministic evaluators |
| `max_tokens` | integer | no | `256` | Maximum gate response |
| `timeout_seconds` | integer | no | `15` | Per-LLM policy timeout |
| `failure_decision` | select | no | `"ask"` | `ask` or `deny`; never `allow` |
| `llm_scope` | select | no | `"mutating"` | `[A3]` Calls that reach the LLM evaluator: `mutating` (command-bearing, write, delete, network, publish), `all`, or `none` (scripts only) |

Validation rules:

- reject a service with neither prompt nor scripts;
- require `llm_service` when prompt is non-empty;
- require referenced policy scripts to be visible in the service's scope;
- reject `failure_decision=allow`;
- cap `max_tokens` and timeout to bounded values;
- resolve the referenced LLM using the request user and conversation scope;
- never pass tools to the LLM;
- call with `temperature=0` when the provider honours it;
- `[A2]` do not rely on a JSON mode: `response_format="json"` is honoured by
  the OpenAI wire only (`core/_llm_client_driver.py`). The response is parsed
  provider-agnostically (first JSON object, strict schema validation, unknown
  fields rejected); anything else maps to `failure_decision`.

The live service exposes one method:

```python
evaluate(envelope: GatingEnvelope) -> EvaluatorDecision
```

It does not persist permissions and does not publish approval dialogs itself.

### 8.2 `GatingScript` resource

Add a repository resource type named `gating_script`. It is distinct from
`agent_hook` because its semantics are security-sensitive and non-mutating.

Proposed fields:

| Field | Type | Meaning |
|---|---|---|
| `name` | string | Scoped resource identifier |
| `description` | string | Human explanation |
| `source` | multiline string | Python evaluator source |
| `tools` | list | Optional tool-name filter |
| `fail_decision` | select | `ask` or `deny` |
| `package_runtime` | object | Optional signed PFP runtime metadata |
| `installed_from` | object | Provenance |
| `created_at` / `uuid` | required metadata | Creation identity |

Source scripts use a fixed entry point:

```python
def evaluate(event):
    # Return allow, deny, ask, or abstain.
    return {
        "decision": "abstain",
        "reason": "",
        "rule_id": "",
        "metadata": {}
    }
```

Scripts receive only a JSON-safe redacted envelope. They cannot replace the
tool name or arguments. Source scripts execute through the relay sandbox with a
short timeout, bounded output, no implicit secrets, and no host/server-local
execution. PFP scripts use the existing package capability enforcement.

The implementation should extract the low-level sandbox/PFP invocation shared
with agent hooks without sharing the unsafe default-decision normalizer.

### 8.3 `AuthorizationContext`

Add `core/authorization_context.py` with validated dataclasses or typed
records:

```json
{
  "context_id": "uuid",
  "revision": 2,
  "created_at": 1787280000.0,
  "updated_at": 1787280300.0,
  "user_id": "alice",
  "conversation_id": "conv-123",
  "root_turn_id": "turn-456",
  "root_message_id": "msg-789",
  "directives": [
    {
      "message_id": "msg-789",
      "timestamp": 1787280000.0,
      "content": "Fix the bug and run tests. Do not commit.",
      "source_type": "user",
      "operation": "root"
    },
    {
      "message_id": "msg-790",
      "timestamp": 1787280300.0,
      "content": "You may commit, but do not push.",
      "source_type": "user",
      "operation": "revise"
    }
  ],
  "optional_context": {
    "pawflow_plan_id": "",
    "pawflow_plan_snapshot": null
  }
}
```

Invariants:

- `context_id`, every directive ID, and every audit event are UUIDs;
- every record has a creation timestamp;
- only authenticated user ingress may add directives;
- revisions are monotonic;
- earlier directives remain present for audit and conflict interpretation;
- a user correction does not rewrite historical text;
- agent-created messages cannot mutate the context;
- optional plan data is a snapshot, not a live authority lookup;
- raw attachments are not copied; only user-authored text and safe attachment
  metadata are included unless a later design explicitly authorizes more.

### 8.4 `AuthorizationContextStore`

Add a dedicated internal store rather than placing growing context documents in
conversation extras.

Recommended layout:

```text
data/runtime/authorization-contexts/
  <user_id>/
    <conversation_id>/
      <context_id>.json
```

The store must provide:

```python
create(...)
get(user_id, conversation_id, context_id)
append_user_directive(..., expected_revision)
snapshot(...)
delete_for_conversation(...)
```

Writes use atomic replacement under a per-context lock. The API rejects stale
`expected_revision` values. An in-memory bounded cache may hold immutable
snapshots keyed by `(context_id, revision)`.

The context identifier and revision travel through runtime state; the full
document does not need to be copied into every FlowFile.

### 8.5 Work lineage

Introduce a small immutable runtime value:

```python
AuthorizationRef(
    context_id: str,
    revision: int,
    root_turn_id: str,
)
```

`[A6]` Transport: the ref travels in the existing message `source` metadata
(next to `plan_id` / `turn_id`) and in a contextvar set by the AgentLoop for
the duration of a turn; call sites read it from there instead of growing every
signature on the path. Only the explicit hand-offs below copy it.

It must be carried by:

- the initial AgentLoop context;
- every tool-call execution request;
- sub-agent executor contexts;
- `delegate` and `flash_delegate`;
- task sub-conversations;
- plan orchestration messages;
- scheduled continuations and poll wakeups;
- ToolRelay requests;
- external AG-UI client jobs;
- realtime voice sessions;
- AgentRuntimeAPI submissions;
- cross-conversation delegation, with explicit provenance;
- published A2A/AG-UI ingress when that publication is allowed to originate
  user authority.

### 8.6 `GatingEnvelope`

The final evaluator input is a snapshot:

```json
{
  "schema_version": 1,
  "decision_id": "uuid",
  "created_at": 1787280400.0,
  "identity": {
    "user_id": "alice",
    "conversation_id": "conv-123",
    "agent_name": "assistant",
    "turn_id": "turn-456",
    "authorization_context_id": "uuid",
    "authorization_revision": 2
  },
  "authority": {
    "root_request": "Fix the bug and run tests. Do not commit.",
    "followups": ["You may commit, but do not push."],
    "optional_plan": null
  },
  "tool_call": {
    "call_id": "tc-123",
    "canonical_name": "bash",
    "arguments": {"command": "git push origin main"},
    "arguments_sha256": "...",
    "policy_classification": {
      "command_bearing": true,
      "hard_confirmation": false
    }
  }
}
```

The arguments field is redacted. The hash is computed over a canonical
redacted representation for audit correlation; it is not a secret-bearing hash
of the unredacted payload.

### 8.7 Evaluator and final decisions

Internal evaluator output:

```json
{
  "decision_id": "uuid",
  "created_at": 1787280400.1,
  "decision": "allow|deny|ask|abstain",
  "reason": "Short user-visible explanation",
  "rule_id": "optional stable rule identifier",
  "matched_directive_ids": ["uuid"],
  "source": "script|llm|failure",
  "source_id": "service-or-script-id",
  "metadata": {}
}
```

Final policy output never contains `abstain`:

```json
{
  "decision": "allow|deny|ask",
  "reason": "...",
  "evaluators": [...],
  "authorization_context_id": "uuid",
  "authorization_revision": 2,
  "decision_id": "uuid",
  "created_at": 1787280400.2
}
```

Do not use an LLM-provided confidence score as an authorization input.

## 9. Binding and resolution

### 9.1 Conversation binding

Store one explicit scoped service reference in conversation extras:

```json
{
  "gating_binding": {
    "scope": "user",
    "service_id": "default_policy_gate"
  }
}
```

Provide helpers in `core/gating_bindings.py` analogous to the summarizer
helpers:

- `get_conversation_binding`;
- `set_conversation_binding`;
- `clear_conversation_binding`;
- `list_available`;
- `resolve_conversation_service`;
- `summary`.

`[A7]` A broken explicit binding (service deleted, disabled, wrong type, or a
script gate without a linked relay) fails closed with `ask` for every relevant
call and raises a visible error in the UI and the decision log. It must not
fall through to another arbitrary gate.

### 9.2 Agent binding

Add an optional scoped reference to conversation agent configuration:

```json
{
  "gating_service": {
    "scope": "user",
    "service_id": "release_agent_gate"
  }
}
```

The value must be supported by:

- `AGENT_CONFIG_DEFAULTS`;
- add/update/copy/import/export paths;
- agent resource definitions where applicable;
- conversation-agent UI;
- external runtime agent configuration;
- validation against a visible enabled `gating` service.

### 9.3 Composition

Resolution returns an ordered list:

1. conversation gate;
2. agent gate, if configured and not identical.

Both evaluate the same immutable envelope. Composition is:

| Conversation | Agent | Final |
|---|---|---|
| allow | allow/absent | allow |
| allow | ask | ask |
| allow | deny | deny |
| ask | any non-deny | ask |
| deny | any | deny |
| absent | agent result | agent result |

An agent binding cannot turn a conversation `ask` or `deny` into `allow`.

There is no implicit global/user fallback by service type. Scope affects
visibility and resolution of the explicitly named service only.

## 10. Authority lifecycle

### 10.1 New root request

At authenticated message ingress:

1. stamp the user message first;
2. allocate a new authorization context UUID and revision 1;
3. persist the root directive with its message UUID and timestamp;
4. attach `AuthorizationRef` to the submitted turn;
5. persist the context reference in the source metadata needed for restart;
6. start the agent.

A new idle user request creates a new context even in the same conversation.

### 10.2 Steering and follow-ups

`[A4]` Concrete rule for a user message that arrives while work is active:

- addressed (selected agent / explicit target) to an agent whose turn is
  active → it revises that agent's active context (revision + 1);
- addressed to an idle agent or to another work item → a new context;
- an authenticated answer to `ask_user`, `request_confirmation`, or a
  plan-step question asked by agent X → a revising directive of X's active
  context (otherwise the ordinary "shall I push? — yes" flow is denied);
- an answer to a gate-generated `ask` applies to that exact call only (see
  §11.5) and does not revise the context;
- anything ambiguous → a new context, never a silent expansion.

A revision may expand, restrict, replace, or cancel earlier authority. The
gating prompt receives the ordered directives and must honor later explicit
corrections.

### 10.3 Delegation

Delegation inherits the caller's `AuthorizationRef`. The delegate receives the
delegation task as untrusted execution guidance plus the original user
authority as a separate immutable field.

A delegate cannot gain authority from the delegating agent's wording. It may
operate only within the intersection of:

- the user's mandate;
- the delegation's narrower task;
- conversation and delegate-specific gating services;
- structural PawFlow permissions.

Cross-conversation delegation copies an immutable authorization snapshot and
records the source conversation and parent context ID. It does not grant access
to resources that the target conversation or principal could not otherwise
use.

### 10.4 Tasks, scheduled work, and continuations

A scheduled continuation created during active work persists the exact
`AuthorizationRef`. On resume:

- load the referenced context;
- verify the expected revision;
- if the user revised or cancelled it, use the newest revision and re-evaluate;
- if the context is missing or cannot be verified, ask or deny.

A standalone scheduled task must carry an authorization context created when
the task was configured. It cannot borrow the most recent interactive prompt.

### 10.5 Completion and retention

Completing a turn does not immediately delete its context because background
work and audit inspection may still reference it. Retention follows the owning
conversation's retention/deletion policy. Conversation deletion removes its
authorization contexts and decision records.

## 11. Central authorization pipeline

### 11.1 One canonical engine

Add `core/tool_authorization.py` with one public entry point used by every
runtime:

```python
authorize_tool_call(
    *,
    tool_name,
    arguments,
    prepared_call,
    user_id,
    conversation_id,
    agent_name,
    turn_id,
    authorization_ref,
    permission_context,
    allow_prompt,
) -> ToolAuthorizationResult
```

The engine owns orchestration. `ToolApprovalGate` remains the owner of legacy
permission persistence, classification, and interactive user responses.

Do not duplicate gating logic in each executor.

### 11.2 Required order

For every runtime:

1. reject malformed provider tool names;
2. unwrap lazy MCP wrappers and aliases;
3. run pre-tool hooks;
4. apply any hook replacement;
5. resolve expressions and variables;
6. prepare the registry call and obtain the final effective name/arguments;
7. run structural hard-deny checks on real arguments;
8. create a secret-redacted gating envelope;
9. resolve conversation and agent gating bindings;
10. evaluate scripts and LLM policies;
11. merge gate decisions;
12. combine the result with legacy permission/confirmation requirements;
13. if needed, publish exactly one user confirmation request;
14. revalidate the authority revision and cancellation state;
15. execute the exact prepared call without mutation;
16. publish result and append the audit outcome.

The existing invariant test for hook-before-gate-before-execute must expand to
cover preparation, redaction, policy gating, revision validation, and the
absence of post-gate mutation.

### 11.3 Structural checks

The central engine must classify results into:

- `hard_deny`: cannot be overridden by gate or user call-level approval;
- `hard_confirm`: gate may deny or ask but cannot auto-allow;
- `ordinary`: gate may allow, deny, or ask;
- `internal_ungated`: explicit recursion-safe plumbing.

Initial `hard_deny` sources include:

- read-only/advisor-read-only violations;
- explicit per-tool deny;
- transport-scoped deny;
- missing ownership or scope authorization;
- invalid/missing authority for a runtime that requires it;
- handler preparation rejection.

Initial `hard_confirm` sources include:

- catastrophic commands;
- policy/service self-modification;
- `[A8]` dynamic tool creation (`create_tool`) and changes to MCP/A2A/AG-UI
  publications or their keys (a published surface can call back into
  PawFlow and widen the mandate);
- any existing protected operation that PawFlow deliberately requires per-call
  confirmation.

The current broad `ALWAYS_ASK` list should be reviewed item by item. Ordinary
code execution may be safely auto-approved by a gate when the user explicitly
requested it, while truly catastrophic or policy-changing calls retain a hard
confirmation floor.

### 11.4 Internal ungated calls

Define a small named constant rather than reusing the broad legacy exempt list.
Candidates include only operations required for the authorization mechanism to
function:

- internal schema lookup used by lazy dispatch;
- internal result delivery;
- cancellation checks;
- the gate's own tool-free LLM call;
- publishing the approval request and response;
- asking the current user for clarification.

User-visible reads, searches, memory access, notifications to external systems,
and filesystem operations are not automatically internal merely because they
are read-only.

### 11.5 Interaction with legacy permission modes

When a gate is bound:

| Existing state | Gate result | Outcome |
|---|---|---|
| structural hard deny | any | deny |
| ordinary legacy allow/exempt/auto | allow | execute |
| ordinary legacy allow/exempt/auto | deny | deny |
| ordinary legacy allow/exempt/auto | ask | ask user |
| ordinary legacy confirmation | allow | execute |
| ordinary legacy confirmation | deny | deny |
| ordinary legacy confirmation | ask | ask user |
| hard confirmation | allow | ask user |
| hard confirmation | deny | deny |
| hard confirmation | ask | ask user |

An existing `always_allow` suppresses the generic legacy prompt but does not
bypass mandate compliance. A user response to a policy-generated prompt applies
to that exact call. It does not mutate the authorization context and does not
create a reusable policy exemption.

When no gate is bound, use the current behavior byte-for-byte where practical.

### 11.6 UX-less callers

`allow_prompt=False` callers cannot wait for a dialog. Map final `ask` to
`needs_approval`, preserving the current voice/runtime convention. The caller
must report that the action requires confirmation in a capable client; it must
not execute.

## 12. Evaluator execution

### 12.1 Script evaluation

For each configured script, in declared order:

1. resolve the resource through conversation > user > global visibility;
2. validate that the resolved object is still the expected type and enabled;
3. apply its optional tool filter;
4. invoke it in the relay/PFP sandbox;
5. validate strict JSON output;
6. append its result to the decision trace.

Aggregation:

- first `deny` may short-circuit;
- `ask` may short-circuit because no later evaluator can make the result less
  restrictive;
- `allow` is retained;
- `abstain` is neutral;
- script failure maps to the script's configured `ask|deny`.

If a policy prompt is also configured, script `allow` does not skip the LLM.
Both mechanisms must agree. This makes scripts hard deterministic policy and
the LLM the semantic mandate comparator.

`[A7]` Source scripts run through the relay sandbox, so a script gate requires
a relay linked to the conversation: binding a script gate without one is
rejected at bind time with an explicit error, and a relay that disappears
later makes the gate fail closed (§9.1). Sandbox latency per call is part of
the performance budget (§22.7); `llm_scope`/tool filters keep reads out of it.

### 12.2 LLM evaluation

The LLM request contains:

- protocol instructions defining the four internal decisions;
- the service policy prompt;
- the redacted authority envelope;
- the redacted effective tool call;
- script decisions already produced.

Call contract:

- direct resolved `LLMConnectionService`;
- no tools;
- no stream;
- `temperature=0` (best effort; not every provider honours it);
- bounded `max_tokens`;
- `[A2]` provider-agnostic strict JSON extraction — never a JSON-mode flag
  as the only guarantee;
- explicit user/conversation/agent call identity for usage accounting;
- bounded service timeout;
- cancellation propagation.

Strict output schema:

```json
{
  "decision": "allow|deny|ask|abstain",
  "reason": "non-empty concise explanation",
  "matched_directive_ids": [],
  "rule_id": ""
}
```

Reject extra control fields that could mutate runtime behavior. Sanitize the
reason before displaying or logging it.

### 12.3 Service-level aggregation

Within one service:

- any `deny` -> `deny`;
- otherwise any `ask` -> `ask`;
- otherwise at least one `allow` and all configured mandatory evaluators
  completed -> `allow`;
- otherwise use `failure_decision`, default `ask`.

Across conversation and agent services, apply the same restrictive ordering.

## 13. Secret handling and redaction

The evaluator must not receive raw resolved secrets.

Add one shared helper that:

1. walks nested dict/list/string arguments;
2. replaces exact resolved secret values with stable placeholders such as
   `<secret:NAME>`;
3. redacts authorization headers, tokens, cookies, credential fields, and known
   secret parameter names;
4. limits string, collection, and total envelope sizes;
5. creates a canonical redacted JSON representation;
6. computes its audit hash.

Deterministic structural checks still inspect the real call in memory. The
redacted copy is the only form passed to scripts, LLMs, SSE explanations, or
persistent audit storage.

Never use a raw secret's hash as an audit identifier because low-entropy secrets
may be brute-forced.

## 14. Time-of-check/time-of-use and concurrency

### 14.1 Exact call identity

The authorization result binds to:

- canonical tool name;
- canonical redacted-arguments hash;
- prepared call identity;
- authorization context ID and revision;
- conversation, agent, and turn;
- decision UUID.

Immediately before execution, validate that all bound fields still match.
Any mutation or revision change requires a fresh decision.

### 14.2 User revision during evaluation

If the user sends a restrictive follow-up while a gate is evaluating:

- the context revision increments;
- the old evaluation may finish but cannot execute;
- the executor reloads the current revision and re-evaluates.

### 14.3 Parallel tool calls

Parallel calls receive independent decision IDs and immutable envelopes.
Ordinary calls may evaluate concurrently. Audit writes must remain ordered by
timestamp and safe under concurrency.

Argument quotas and cross-call constraints such as "upload at most one file"
require atomic reservations and are deferred from V1. A script may conservatively
return `ask` until a later quota ledger exists.

### 14.4 Cancellation

Cancellation must interrupt:

- pending policy-script execution where supported;
- the gating LLM request;
- a pending user approval wait.

A cancelled authorization returns control-flow cancellation, not `deny`, and
must not affect the next turn.

## 15. API and action surface

Add service-flow actions analogous to summarizer bindings:

- `list_gating_services(conversation_id, agent_name?)`;
- `link_conversation_gating(conversation_id, scope, service_id)`;
- `unlink_conversation_gating(conversation_id)`;
- `get_effective_gating(conversation_id, agent_name)`;
- `get_gating_decisions(conversation_id, limit, cursor)`.

Agent binding remains part of the canonical conversation-agent config update
path rather than introducing a second competing agent configuration store.

Add resource actions for `gating_script` through the existing generic
resource CRUD, scope, copy, import/export, and PFP surfaces.

Validation requirements:

- requester must have access to the conversation;
- referenced service/script must be visible and enabled;
- global writes remain admin-only;
- only a user-authorized write path may change an effective gate;
- actions return redacted definitions;
- LLM credentials and secret values are never returned.

## 16. Web UI

### 16.1 Service form

The generic service form needs a reusable
`resource_ref_list(resource_type="gating_script")` field, analogous to the
existing `service_ref_list`.

The `gating` form shows:

- LLM connection selector;
- policy prompt editor;
- associated policy-script picker;
- max tokens;
- timeout;
- failure decision;
- validation explaining that prompt and/or scripts are required.

### 16.2 Resources panel

Add:

- a Policy Gates service category entry;
- a Policy Scripts repository section;
- create/edit/copy/delete menus for scripts;
- conversation-level "Configure policy gate";
- an agent-level gate selector in conversation-agent configuration;
- an effective-state summary showing conversation and agent composition.

All labels and errors must use the English/French/Spanish i18n catalogs.

### 16.3 Approval dialog

Extend the existing tool-approval dialog rather than adding a second modal.
Show:

- requesting agent;
- canonical tool and redacted arguments;
- gate result `ask`;
- concise gate reason;
- a bounded excerpt of the matching user directive;
- whether a conversation gate, agent gate, or hard-confirm rule requested it.

The dialog response is scoped to the exact decision ID. Existing
`allow_session` and `always_allow` options should not be offered as ways to
bypass a dynamic mandate. For policy-generated asks, V1 should offer:

- allow this call;
- deny this call;
- cancel the current work.

Changing the mandate should happen through a new user message, producing a new
authorization revision.

### 16.4 Decision inspection

Provide a read-only decision drawer or detail view with:

- timestamp and decision UUID;
- agent and turn;
- gate service(s);
- authority revision;
- redacted tool call;
- script/LLM outcomes;
- final reason;
- execution outcome.

## 17. CLI and non-web clients

PawCode and other clients must:

- display policy-generated approval requests;
- show the gate reason and mandate excerpt;
- submit a response bound to the decision ID;
- distinguish policy denial from read-only denial and user denial;
- report `needs_approval` clearly for clients without an interactive approval
  channel.

Add a `/gate` command family:

```text
/gate status
/gate link <scope> <service_id>
/gate unlink
/gate decisions [limit]
```

Agent-level binding remains managed through agent configuration.

## 18. Audit and observability

Add `core/gating_audit_store.py`, preferably SQLite for bounded indexed
queries and concurrent append safety.

One final decision record contains:

- decision UUID and timestamp;
- user, conversation, agent, turn;
- authorization context ID and revision;
- conversation/agent gate service references;
- policy-script IDs and versions/hashes;
- logical/physical gating LLM service identity when available;
- canonical tool name;
- redacted argument preview and canonical redacted hash;
- evaluator decisions and sanitized reasons;
- final decision;
- whether user confirmation was requested and its result;
- duration per evaluator and total duration;
- execution outcome: not-run, started, succeeded, failed, cancelled;
- no raw secrets and no unrestricted transcript text.

Emit structured logs and metrics:

- decisions by `allow|deny|ask`;
- script vs LLM decisions;
- latency percentiles;
- malformed-response count;
- missing/stale-context count;
- user-confirmation rate;
- policy override attempts;
- cost/tokens under a distinct `gating` usage channel.

Audit failure must not permit execution. If the decision itself is otherwise
valid but the audit append fails, default to `ask` or `deny` according to a
documented safe operational rule.

## 19. Integration map

### 19.1 New files

Expected new modules:

- `services/gating_service.py`;
- `core/authorization_context.py`;
- `core/authorization_context_store.py`;
- `core/gating_bindings.py`;
- `core/gating_policy.py`;
- `core/tool_authorization.py`;
- `core/gating_script_runner.py`;
- `core/gating_audit_store.py`;
- focused test modules for each component.

### 19.2 Existing backend files likely affected

At minimum inspect and update:

- `tasks/__init__.py` for service registration;
- `core/tool_approval.py`;
- `tasks/ai/agent_tool_exec.py`;
- `core/agent_executor.py`;
- `services/_tool_relay_execute.py`;
- `core/agui_client_runtime.py`;
- `services/_realtime_tools.py`;
- `core/agent_runtime_api.py`;
- delegation and flash-delegation delivery modules;
- task and continuation schedulers;
- `core/conv_agent_config.py`;
- conversation import/export;
- service/resource action handlers;
- PFP resource/package validators;
- message ingress and streaming context builders.

The exact blast radius must be re-queried with `project_graph` immediately
before implementation because the graph may change before this plan is started.

### 19.3 Frontend files likely affected

At minimum:

- generic schema form support for resource reference lists;
- service install/edit UI;
- Resources rendering and menus;
- conversation-agent configuration;
- approval dialog and SSE handling;
- decision inspection UI;
- English, French, and Spanish catalogs;
- PawCode CLI approval handling and commands.

### 19.4 Documentation to update with implementation

The implementation change must update:

- `docs/02_REFERENCE_TASKS_SERVICES.md`;
- `docs/AGENT_SYSTEM.md`;
- security/approval documentation;
- PFP package documentation for policy scripts;
- CLI documentation;
- API/action reference;
- `CHANGELOG.md` under Unreleased.

## 20. Migration and compatibility

This is an additive feature with explicit activation.

One-shot implementation rules:

1. add empty `gating_service` to agent config defaults;
2. add no conversation binding by default;
3. register the new service and resource types;
4. do not auto-create or auto-bind a gate during installation;
5. preserve legacy permission behavior when no binding exists;
6. include gating bindings and authorization-context references in conversation
   export/import where meaningful;
7. reject broken explicit bindings rather than silently selecting a different
   service;
8. remove any temporary compatibility aliases before release.

Authorization contexts are runtime state, not portable authority by default.
Conversation export may include their redacted history for audit, but importing
a conversation must not automatically reactivate old authority. New execution
requires a new authenticated user mandate.

## 21. Work packages

### Delivery order `[A5]`

V0 ships first and is independently useful: WP0, WP1 (context + store, created
at webchat ingress), WP2 (service, scripts, provider-agnostic LLM evaluation),
WP3 (bindings), WP5 (central engine wired into the primary AgentLoop) with the
interim fail-closed rule (decision 19) for every other runtime, plus the
minimal decision viewer. WP4, WP6, the full WP7 surface and WP8 follow in
later commits. Each WP is a dedicated commit with tests and documentation.

### WP0 — Characterization and invariants

Before production code:

- enumerate every tool-execution entry point with `project_graph`;
- characterize current permission behavior per runtime and mode;
- add source-order tests for hook -> prepare -> authorize -> execute;
- characterize alias/MCP unwrapping and secret resolution;
- inventory internal calls that would recurse if gated;
- record baseline no-binding response behavior.

Exit criteria:

- every execution surface has an owning test;
- the internal ungated allowlist is explicit and reviewed;
- no gate code has been added yet.

### WP1 — Authorization context domain and store

Implement:

- typed context/directive/reference records;
- validation, UUIDs, timestamps, and revision rules;
- atomic store and bounded cache;
- creation at authenticated user ingress;
- steering revision updates;
- deletion/retention integration.

Tests:

- root creation;
- authenticated revision;
- rejection of agent/tool-authored revisions;
- stale revision conflict;
- concurrent append;
- conversation deletion;
- cache isolation by user/conversation.

Exit criteria:

- every agent turn can report its exact authorization reference;
- no transcript scan is required to load authority.

### WP2 — Gating service and policy scripts

Implement:

- `GatingService`;
- parameter schema and validation;
- `gating_script` resource type;
- shared sandbox/PFP invocation substrate;
- strict result normalization;
- LLM JSON request/response handling;
- redaction and bounded envelopes;
- conservative aggregation.

Tests:

- prompt-only, script-only, and combined gates;
- all four internal script decisions;
- malformed JSON;
- timeout and cancellation;
- missing LLM;
- script failure;
- secret redaction;
- no tools sent to gating LLM;
- usage attribution.

Exit criteria:

- a standalone unit-tested service maps an envelope to a safe final decision.

### WP3 — Binding and configuration

Implement:

- conversation binding helpers and actions;
- agent scoped reference;
- strict explicit resolution;
- composition;
- import/export handling;
- service/resource visibility checks.

Tests:

- conv/user/global scoped service resolution;
- broken explicit binding;
- conversation-only, agent-only, and combined cases;
- duplicate service deduplication;
- non-owner access rejection;
- no implicit fallback.

Exit criteria:

- effective gating state is deterministic and inspectable.

### WP4 — Work-lineage propagation

Propagate `AuthorizationRef` through:

- AgentLoop;
- sub-agent executor;
- delegate and flash-delegate;
- task sub-conversations;
- plan orchestration;
- scheduler/poller continuation;
- AgentRuntimeAPI;
- ToolRelay;
- AG-UI;
- realtime voice;
- A2A and cross-conversation paths.

Tests:

- direct turn;
- user steering revision;
- two concurrent agents with different mandates;
- nested delegation;
- scheduled continuation;
- cross-conversation copy;
- missing context fails safely;
- delegate wording cannot expand authority.

Exit criteria:

- every relevant tool call carries an explicit context ID and revision.

### WP5 — Central tool authorization engine

Implement the canonical order and integrate it first into the primary AgentLoop
path.

Refactor, do not duplicate:

- structural classifications;
- legacy interactive approval;
- gate resolution/evaluation;
- hard-confirm behavior;
- exact-call and revision revalidation;
- decision/result audit lifecycle.

Tests:

- ordinary gate allow replaces generic prompt;
- gate deny blocks a legacy-exempt tool;
- gate ask emits one dialog;
- auto mode remains subject to mandate gating;
- read-only and explicit deny win;
- hard confirmation cannot be auto-allowed;
- existing no-binding behavior remains;
- no post-gate mutation;
- cancellation is control flow, not denial.

Exit criteria:

- the primary runtime uses only the central entry point.

### WP6 — All execution runtimes

Replace caller-local policy branches with the central engine in:

- sub-agent executor;
- ToolRelay;
- external AG-UI client runtime;
- realtime voice;
- published/external runtimes that can execute local tools;
- any remaining graph-discovered call site.

Tests must run the same parameterized authorization contract against every
runtime adapter.

Exit criteria:

- no relevant runtime calls `ToolApprovalGate.check()` directly except through
  the central engine;
- no runtime bypasses gating in `auto` mode;
- relay and main-runtime ordering are identical.

### WP7 — API, UI, CLI, and i18n

Implement:

- service and script creation/editing;
- resource-ref-list schema control;
- conversation and agent bindings;
- effective-state display;
- approval dialog integration;
- audit decision viewer;
- PawCode commands and events;
- en/fr/es translations.

Tests:

- backend action permissions;
- service-form schema rendering;
- resource picker serialization;
- conversation/agent binding flows;
- dialog response scoping;
- i18n key parity;
- JS syntax and existing UI invariants.

Exit criteria:

- a user can configure, bind, inspect, and respond to a gate without editing
  files manually.

### WP8 — Audit, telemetry, and operations

Implement:

- SQLite audit store;
- structured events and metrics;
- retention/deletion;
- usage channel;
- sanitized logs;
- health/status reporting.

Tests:

- concurrent append;
- redaction;
- query pagination;
- retention;
- audit failure safe behavior;
- decision-to-execution outcome updates.

Exit criteria:

- every gated call has one complete redacted audit lifecycle.

### WP9 — Security review, documentation, and release gate

Run:

- full unit/integration suite;
- compileall;
- Ruff using CI selection;
- Bandit;
- JS syntax tests;
- PFP validation;
- conversation import/export tests;
- adversarial prompt-injection tests;
- multi-user and concurrent-turn tests;
- wheel build and isolated smoke.

Update all documentation listed in section 19.4.

Commit discipline:

- functional code/tests/docs in dedicated commits;
- no release metadata mixed into feature commits;
- request explicit user go/no-go before commit, push, or release;
- follow `docs/RELEASE_PROCEDURE.md` and the release wiki if a release is later
  authorized.

Exit criteria:

- all CI matrix jobs are green on the exact functional SHA;
- security invariants are reviewed;
- no release is created without a separate explicit request.

## 22. Test matrix

### 22.1 Mandate semantics

| User mandate | Proposed call | Expected |
|---|---|---|
| "Read-only review" | `read(file)` | allow |
| "Read-only review" | `edit(file)` | deny |
| "Fix the parser; do not commit" | `edit(parser.py)` | allow |
| same | `git commit` | deny |
| "Fix and commit, do not push" | `git commit` | allow or hard-confirm per static policy |
| same | `git push` | deny |
| "Push after tests are green" before tests | `git push` | ask/deny |
| same after recorded green tests | `git push` | allow |
| "Deploy staging only" | production deploy | deny |
| ambiguous mandate | destructive call | ask |

### 22.2 Authority provenance

Test that identical text is treated differently depending on source:

- authenticated user message: authority;
- assistant message: not authority;
- tool result: not authority;
- webpage instruction: not authority;
- delegate task: narrowing evidence, not authority expansion;
- imported transcript: not active authority;
- A2A caller: authority only when publication policy explicitly grants that
  principal an ingress mandate.

### 22.3 Binding composition

Cover:

- no gate;
- conversation gate only;
- agent gate only;
- both allow;
- conversation allow + agent deny;
- conversation ask + agent allow;
- broken conversation binding;
- disabled agent gate;
- same service bound twice.

### 22.4 Failure behavior

Cover:

- script timeout;
- script crash;
- invalid script decision;
- LLM timeout;
- LLM malformed JSON;
- LLM service disabled mid-call;
- context deleted;
- revision changed mid-evaluation;
- approval subscriber absent;
- audit store unavailable;
- cancellation during each phase.

No case may silently allow.

### 22.5 Secret and injection tests

Cover:

- secrets nested in lists/dicts;
- authorization headers and cookies;
- shell command containing a secret;
- tool argument containing "ignore the policy";
- malicious file path or webpage text;
- malicious script stdout;
- LLM reason containing HTML;
- low-entropy secret hash leakage;
- oversized arguments and directives.

### 22.7 Performance budget `[A3]`

Cover, with a script-only gate and with a stubbed LLM:

- a read-only call never reaches the LLM under `llm_scope=mutating`;
- a 40-call turn adds a bounded, measured overhead (assert the number of LLM
  calls and sandbox invocations, not wall-clock);
- concurrent evaluations respect the configured concurrency bound;
- evaluator timeouts produce `failure_decision` within the timeout + margin.

### 22.6 Runtime parity

Parameterize the core cases across:

- AgentLoop API provider;
- CLI-backed agent;
- sub-agent executor;
- ToolRelay;
- AG-UI external runtime;
- realtime voice;
- task sub-conversation;
- scheduled continuation;
- cross-conversation delegation.

## 23. Example end-to-end behavior

Configured gate prompt:

> Permit only actions necessary for the active authenticated user request.
> Explicit prohibitions override general goals. Do not infer permission to
> commit, push, publish, deploy, delete, spend money, or contact third parties.
> Ask when the relationship is ambiguous.

User request:

> Correct the AG-UI replay bug, run the relevant tests, then commit and push.
> Do not make a release.

Expected calls:

| Call | Policy decision | Reason |
|---|---|---|
| search AG-UI runtime | allow | Investigation is necessary |
| read relevant tests | allow | Within requested scope |
| edit AG-UI runtime | allow | Directly implements the fix |
| run targeted tests | allow | Explicitly requested |
| edit unrelated OpenSpace code | ask/deny | Scope expansion |
| commit relevant files | allow | Explicitly requested |
| push normal commit | allow, subject to static hard rules | Explicitly requested |
| force-push | ask | Dangerous variant was not requested |
| create beta tag | deny | Release explicitly prohibited |
| publish GitHub release | deny | Release explicitly prohibited |

If the user later writes:

> Go ahead with beta.227 once CI is green.

the active context gains a new authenticated directive and revision. A release
call evaluated under the old revision cannot execute. Under the new revision,
the gate may allow it only after the required CI condition is evidenced by
trusted runtime state or otherwise asks the user.

## 24. Acceptance criteria

The feature is complete only when all of the following are true:

1. A user can create a gate with a prompt, scripts, or both.
2. A gate can be explicitly bound to a conversation and/or agent.
3. A natural-language user request creates a persisted versioned authority
   context without creating a PawFlow plan.
4. Follow-up user directives revise the correct active lineage.
5. Delegates and continuations inherit the exact authority reference.
6. Every relevant execution runtime uses the central authorization engine.
7. A gate can deny a tool that legacy policy would have auto-approved.
8. A gate can auto-allow an ordinary tool that legacy policy would have asked
   about.
9. A gate cannot weaken structural hard denies or confirmations.
10. Scripts and LLMs receive no raw resolved secrets.
11. Agent/tool/web content cannot expand user authority.
12. Concurrent contexts do not leak authority across agents or turns.
13. Stale revisions cannot execute.
14. Every decision is audited with UUID, timestamp, reason, and redacted call.
15. Missing/broken evaluators fail to ask or deny, never allow.
16. No binding preserves current PawFlow behavior.
17. UI, CLI, API, PFP, import/export, documentation, and i18n are complete.
18. Focused and full CI suites are green.

## 25. Deferred extensions

After V1 has production telemetry, consider:

- atomic call/resource/spend quotas;
- signed reusable mandate templates;
- structured condition evidence such as "CI is green";
- user-authored path/domain capability sets;
- policy simulation against proposed tool-call batches;
- dry-run explanations before activating a gate;
- organization-level mandatory baseline gates;
- policy decision caching for provably identical immutable calls;
- formal policy languages such as Cedar or OPA/Rego as additional deterministic
  evaluators;
- cryptographic signatures for exported audit bundles.

None of these should delay the minimal safe implementation above.

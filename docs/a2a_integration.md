# A2A Integration

This document describes PawFlow's implemented Agent2Agent (A2A) server,
client, cross-conversation delegation, configuration UI, and planned protocol
extensions.

Sources used for this design:

- A2A latest specification: https://a2a-protocol.org/latest/specification/
- Agent discovery: https://a2a-protocol.org/latest/topics/agent-discovery/
- Streaming and asynchronous operations: https://a2a-protocol.org/latest/topics/streaming-and-async/
- A2A and MCP comparison: https://a2a-protocol.org/latest/topics/a2a-and-mcp/

## Goal

PawFlow should support three A2A target modes:

1. A local agent in the current PawFlow conversation.
2. A remote PawFlow agent running inside a conversation on another PawFlow instance.
3. A generic remote A2A-compatible agent running on another runtime, framework, or architecture.

The implementation should keep A2A separate from MCP. MCP exposes tools and resources. A2A exposes stateful agents and task lifecycles. PawFlow should support both because they cover different interoperability surfaces.

## Implemented V1

PawFlow currently implements the A2A 1.0 HTTP+JSON core:

- Multiple agents from one PawFlow conversation can be published separately.
- Each publication has its own Agent Card, Bearer keys, context policy, tasks,
  and public label/description.
- `message:send`, task lookup/listing, and cancellation are supported.
- Send requests are asynchronous by default. A client can set
  `configuration.returnImmediately=false` to wait for a terminal result.
- PawFlow agents can call any configured remote HTTP+JSON A2A agent through
  the `a2a` tool (`send`, `get`, and `cancel`).
- `delegate` can target an agent in another writable PawFlow conversation,
  directly or through a named local target.
- A2A context IDs exposed to clients are opaque. Internal PawFlow conversation
  IDs never appear in the protocol response.

The V1 Agent Card advertises `streaming=false` and
`pushNotifications=false`. Raw/base64 parts are rejected; text, structured
JSON data, and URL parts are accepted. SSE streaming, push notifications,
artifacts, and binary transfer remain roadmap items later in this document.

### UI configuration

Open **Resources → A2A → Publish agents and configure targets** in a
conversation. The dialog provides two workflows:

1. **Publish an agent**
   - Select any agent attached to the current conversation.
   - Set a public name and description.
   - Choose `isolated` (recommended) or `shared` context policy.
   - Copy the Agent Card URL or HTTP+JSON endpoint.
   - Create and revoke client keys. A raw key is displayed once only.
2. **Named targets**
   - For another PawFlow conversation, select `Conversation — agent`; no raw
     conversation ID is required. The alias is accepted by `delegate`.
   - For a remote agent, enter an alias, its Agent Card URL, and optionally the
     name of a PawFlow secret containing its Bearer key. The alias is accepted
     by the `a2a` tool.
   - Private/local network access is rejected by default. Enable it only for a
     trusted endpoint or use a PawFlow relay URL.

Only the conversation owner can manage publications, keys, and named targets.
Runtime calls still pass through the normal conversation ACL and tool policy.

## Existing PawFlow Fit

PawFlow already has most of the runtime semantics A2A needs:

- `ConversationStore` persists transcript, shared context, and per-agent context.
- `conv_agents` records the agents attached to a conversation.
- `ConversationWriter.enqueue_message(...)` is the visible, persisted message write path.
- `PendingQueue.for_agent(...).enqueue(...)` wakes an agent with pending user input.
- `AgentLoopTask.wake_agent(...)` schedules work for a specific `conversation_id:agent_name` pair.
- `AgentLoopTask.is_agent_active(...)` tracks foreground agent activity.
- `ConversationEventBus` and existing SSE code stream agent, tool, and status events to clients.
- `agent_msg` already maps a user message to a specific conversation agent through `source.target_agent`.

A2A should therefore be an adapter layer over existing conversation and agent mechanics, not a second agent runtime.

## Data Model Mapping

| A2A concept | PawFlow mapping |
| --- | --- |
| Agent Card | Generated from a PawFlow agent resource plus conversation-scoped metadata. |
| Agent Skill | Agent description, assigned skills, enabled tools, optional published flow capabilities. |
| Context ID | Opaque `ctx_*` identifier mapped server-side to either the parent conversation or an isolated `::a2a::` child context. |
| Task ID | A PawFlow A2A task record linked to an initiating message ID and target agent. |
| Message | PawFlow message rows with `role`, `content`, `source`, `msg_id`, `ts`, and `conversation_id`. |
| Part: text | Plain message content. |
| Part: file | FileStore reference, HTTP URL, or copied artifact depending on trust and size. |
| Part: structured data | JSON attachment or artifact part, not ad hoc string serialization. |
| Artifact | FileStore artifact plus metadata, linked to the A2A task. |
| Task status | Derived from pending/running/completed/error/canceled/input-required/auth-required state. |

The implementation persists A2A state in `data/system/a2a.sqlite3` (through
`core.paths.SYSTEM_DIR`) instead of inferring task state from transcript scans.
The database contains:

```text
a2a_publications  — agent publication and context policy
a2a_api_keys      — hashed Bearer keys (raw values are never persisted)
a2a_contexts      — opaque external-to-internal context mapping per client key
a2a_tasks         — correlated runtime turn and A2A task state
a2a_targets       — named local or remote targets used by the UI/tools
```

Do not create anonymous fallbacks. Missing `conversation_id`, `agent_name`, `user_id`, or authentication scope must fail explicitly.

## Server-Side A2A

### Public Endpoints

Each publication uses the following A2A 1.0 HTTP+JSON routes:

```text
GET  /a2a/{publication_id}/agent-card.json
GET  /a2a/{publication_id}/extendedAgentCard
POST /a2a/{publication_id}/message:send
GET  /a2a/{publication_id}/tasks/{task_id}
GET  /a2a/{publication_id}/tasks?contextId={context_id}
POST /a2a/{publication_id}/tasks/{task_id}:cancel
```

Agent Cards are public discovery metadata. Every task operation and the
extended card require the publication's Bearer key. A listener-level Private
Gateway, when configured, remains an additional outer security layer.

### Agent Card Generation

Generate Agent Cards from:

- `ResourceStore` agent definitions.
- `conv_agents` entries for conversation-scoped agents.
- Enabled skills, published tool filters, and package-provided capabilities.
- Auth policy from API keys, OAuth, or service account tokens.
- Supported modalities: text first, then FileStore-backed files, then structured JSON.

Do not leak private tools, relay paths, internal service IDs, provider session IDs, or full system prompts in public Agent Cards. Use authenticated extended cards for sensitive details.

### Send Message

`SendMessage` validates the publication, Bearer key, target agent, parts, and
context ownership, then reuses the existing local agent runtime:

1. Resolve or create an opaque context owned by the authenticated key.
2. Create an isolated child conversation when the publication requests it.
3. Convert supported A2A parts into PawFlow text/URL attachments.
4. Create the durable A2A task and correlated runtime turn ID.
5. Submit through `AgentRuntimeAPI.submit_message(...)`.
6. Persist provenance as `source.type=a2a` with `visibility=target_only`.
7. Correlate the final `done`/`error_event` through `AgentResultWaiter` and
   update the task to `completed` or `failed`.

The PawFlow message should keep the invariant required by `ConversationStore`:

```json
{
  "role": "user",
  "content": "...",
  "source": {
    "type": "a2a",
    "name": "A2A client",
    "target_agent": "assistant",
    "visibility": "target_only",
    "publication_id": "a2ap_...",
    "task_id": "task_...",
    "context_id": "ctx_..."
  },
  "channel": "a2a"
}
```

### Streaming

`SendStreamingMessage` and `SubscribeToTask` should bridge PawFlow SSE to A2A stream responses:

- Initial task event when the message is accepted.
- Status updates when the agent starts, becomes idle, is interrupted, errors, or is canceled.
- Message updates when final assistant output is persisted.
- Artifact updates when FileStore outputs are created.

The stream should close when the A2A task reaches a terminal state. If the connection drops, clients should use `SubscribeToTask` with the task ID.

### Task State Mapping

| PawFlow state | A2A task state |
| --- | --- |
| queued in `PendingQueue` | submitted or working |
| active in `AgentLoopTask` | working |
| waiting for user input or MCP elicitation | input-required |
| waiting for auth or missing secret | auth-required |
| final assistant message persisted | completed |
| force stop or cancel | canceled |
| provider/tool failure | failed |
| policy rejection | rejected |

PawFlow needs an explicit A2A task finalization hook. Relying only on transcript inspection will be brittle for streaming, cancellation, and disconnected clients.

### Push Notifications

A2A push notifications should be implemented after streaming works. Store push configs per A2A task and validate webhook URLs to avoid SSRF. Use allowlists, HTTPS-only defaults, token validation, and optional signed notifications. Notifications should contain A2A `StreamResponse` payloads and clients can call `GetTask` for full state.

## Client-Side A2A

### Remote Agent Resource

Add a resource type or agent backend for remote A2A agents. Suggested resource shape:

```json
{
  "type": "agent",
  "name": "remote_researcher",
  "backend": "a2a",
  "agent_card_url": "https://example.com/.well-known/agent-card.json",
  "service_url": "https://example.com/a2a",
  "auth_secret": "remote_researcher_token",
  "preferred_binding": "json-rpc",
  "remote_context_id": "optional",
  "input_modes": ["text/plain"],
  "output_modes": ["text/plain", "application/json"]
}
```

The UI should let a user add this as a conversation agent, then target it with the same `/msg`, agent selector, and delegation flows used for local agents.

### Remote PawFlow Conversation Agent

For another PawFlow instance, support a richer config:

```json
{
  "backend": "a2a",
  "agent_card_url": "https://remote.example/a2a/conversations/abc/agents/coder/agent-card.json",
  "remote_conversation_id": "abc",
  "remote_agent_name": "coder",
  "auth_secret": "remote_pawflow_api_key"
}
```

The local conversation treats the remote agent as a participant. Remote responses are persisted locally as messages with `source.type = "agent"`, `source.name = local_alias`, and A2A metadata that links to the remote task.

### Generic Remote A2A Agent

For non-PawFlow A2A agents:

- Fetch and cache the Agent Card.
- Select the best supported binding, starting with JSON-RPC or REST.
- Validate input and output modes before sending.
- Keep a remote task map so local message IDs can be correlated with remote task IDs.
- Convert remote artifacts into FileStore artifacts when possible.
- Treat the remote agent as opaque. Do not assume it has PawFlow concepts, tools, or per-agent context files.

## A2A and Delegate

Local `delegate` should remain the fast internal path for PawFlow agents in the same conversation. A2A should be used when the target is remote, when an external client targets PawFlow, or when protocol-level interoperability matters.

A future improvement can make `delegate` transport-aware:

- Local PawFlow agent: current `SubAgentExecutor` or conversation queue path.
- Remote PawFlow or generic A2A agent: A2A client path.
- Tool-like stateless capability: MCP path.

## Multi-Client Isolated Contexts

A common server-side use case is a single PawFlow agent that exposes a stateful
capability to many external A2A clients at once. Example: a calendar/booking
agent backed by a calendar MCP tool, where external agents request appointments
and receive confirmations. Each external client must get an isolated working
context — one client's negotiation must never leak into another's — while the
human owner still sees all activity inside one conversation in the web chat.

PawFlow already has the primitives for this. The A2A layer only wires them
together; it does not introduce a new context mechanism.

### Context Model Constraint

A PawFlow conversation partitions context by agent name only: `shared.jsonl`
plus one `{agent}.jsonl` per agent (`core/conversation_store.py`). There is no
notion of multiple independent contexts inside a single `conversation_id`.
Therefore per-client isolation must be realized as separate sub-conversations,
not as sub-partitions of one conversation. "One conversation with N client
contexts" is, in implementation terms, one parent conversation plus N isolated
sub-conversations.

### Mapping

- The user-facing "calendar conversation" is a parent conversation that owns the
  calendar agent.
- Each A2A `context_id` (one per external client/session) maps to an isolated
  ephemeral sub-conversation that targets the calendar agent.
- The sub-conversation starts empty and is persisted and resumable, so multi-turn
  booking negotiation (propose slot, counter, confirm) continues in a clean
  per-client context.

This reuses the isolated sub-agent primitive. `delegate context='isolated'`
spawns a separate sub-agent with an empty context (`_resolve_context()` returns
`[]` for isolated; `core/handlers/resource_agent.py`), and `persist=true` keeps
the sub-conversation for later resume. The A2A server invokes the same
`SubAgentExecutor` / isolated sub-conversation path, but the entry point is an
inbound `SendMessage` instead of an agent's delegate tool call. Note the
existing restriction: an agent that is itself running as a delegate can only use
`context='shared'`, so a calendar agent inside an A2A sub-context must not fan
out with nested isolated delegates.

### Lifecycle: empty, persist, TTL

1. First `SendMessage` from a client with no `context_id`: create a fresh
   isolated sub-conversation (empty context), bound to the calendar agent, with
   `persist=true` and a TTL.
2. Subsequent `SendMessage` carrying that `context_id`: resume the same
   sub-conversation.
3. Expiry: conversations carry a TTL. `save(..., ttl=...)` sets
   `_meta_expires_at` and `ConversationStore.cleanup()` deletes expired
   conversations. The A2A context is reclaimed when the TTL elapses or the A2A
   task reaches a terminal state.

Use a sliding TTL — re-stamp `_meta_expires_at` on each inbound message — if the
context should live for the duration of the A2A session rather than a fixed
window. The cleanup scheduler that calls `ConversationStore.cleanup()` must be
running for reclamation to happen.

### Shared Backend, Isolated Contexts: Concurrency

Context isolation does not protect the underlying capability. All clients book
against the same calendar backend, so two isolated contexts can request the same
slot concurrently. Double-booking prevention must live in the calendar MCP tool
or backend (atomic slot check-and-reserve, slot locking), not in the context
model. This is the real concurrency risk of the pattern and must be handled at
the tool layer.

### Web Chat Rendering: Visibility Without Context Leakage

The human owner selects the parent calendar conversation and sees one collapsible
block per client, with sub-blocks per turn. This reuses the existing delegate
block rendering (`delegate-block` / `delegate-sub-block`, grouped by
`delegate_tc_id` / `data-delegate-group`; `tasks/io/chat_ui/messages.js`).

Visibility and isolation are in tension. Projecting per-client activity into the
parent conversation as normal or `agent_delegate` messages would route it into
shared/agent context and break isolation. Resolve this with `display_only`
messages, which `append_message` writes to the transcript only and to no context
file (`core/conversation_store.py`). The A2A adapter projects, per client turn, a
`display_only` summary into the parent conversation keyed by `context_id`, so:

- the web chat groups it as one block per client (UI visibility), and
- nothing enters any agent's prompt context (isolation preserved).

Rendering options:

- Reuse the delegate block path by stamping the projected `display_only` message
  with the group key set to `context_id`, but without the delegate context
  routing (the projection stays transcript-only).
- Or add a dedicated `a2a-block` renderer modeled on `delegate-block`.

Optional drill-down: open the full isolated sub-conversation from its block.
Inline viewing of a sub-conversation inside the parent is not an existing feature
and is additional work; the `display_only` summary block is sufficient for the
common case.

Hide the per-client sub-conversations from the main conversation list with a
parent/child flag so they do not flood the sidebar; they live under the parent
calendar conversation.

### Dependencies

This pattern builds on Phase 1 (local A2A server, `SendMessage`, `GetTask`, task
records) plus Phase 2 (streaming) or Phase 5 (push notifications) for delivering
confirmations. Push notifications fit asynchronous booking confirmations better
than a long-lived SSE subscription, because a confirmation may depend on a freed
slot or human approval that arrives much later.

### Human-in-the-Loop Confirmation

If the conversation owner must approve each appointment rather than letting the
agent confirm autonomously, the A2A task moves to `input-required` (or
`auth-required`) and stays open while the owner approves in the web chat. This is
the `input-required` mapping in "Task State Mapping" and depends on the explicit
A2A task finalization hook; do not infer approval state from transcript scans.

## Multi-Hop Async Confirmation (Saga)

The confirmation flow can span more than two parties and stay open across an
arbitrary human delay. A representative flow with three agents (an external
client, a calendar agent, a confirmation agent) plus the human owner:

```text
1. external agent  --A2A SendMessage-->  calendar agent        (task T_ext)
2. calendar agent  --A2A SendMessage-->  confirmation agent     (task T_cnf)
3. confirmation agent: deposit pending item, reply input-required "received"
4. calendar agent: reply input-required "confirmation requested" on T_ext
5. human opens the confirmation conversation, asks what is pending
6. confirmation agent lists pending items from its store
7. human confirms one item
8. confirmation agent --A2A--> calendar agent: "confirmed" (resolves T_cnf)
9. calendar agent --push--> external agent: "confirmed" (resolves T_ext)
```

This is realizable with the planned A2A surface (Phase 1 server, Phase 3 client,
Phase 5 push) on top of the existing async runtime. It is not a single feature:
it is a small distributed state machine assembled from task records, agent wake,
outbound A2A calls, and push notifications. PawFlow provides the parts, not an
orchestrator/saga manager.

Note that agent wake is per-conversation (`AgentLoopTask.wake_agent(
conversation_id, agent_name)`); there is no direct cross-conversation wake. A
cross-conversation hop is "write a message into the target conversation, then
wake its agent" — exactly what the A2A `SendMessage` server handler does. Each
"call you back later" step (8, 9) is therefore an ordinary agent turn, triggered
by an event (human input or inbound A2A), that emits an outbound A2A call. It is
the async delegate pattern, but cross-conversation over A2A.

### Confirmation Inbox over Task Records

The confirmation side is a shared inbox, not an isolated-context surface (see
"Multi-Client Isolated Contexts" for why isolation fits the calendar side but
not an agent that aggregates toward one human). Inbound confirmation requests
must be readable by the human-facing turn, so they belong in a store the
human-facing agent can query — not in per-request isolated sub-conversations whose
context the human-facing turn cannot see.

The natural store is the A2A task index itself. Each inbound confirmation request
is a task in `input-required` on the confirmation side, and its task record
already carries everything needed:

- `task_id`, `context_id`, `state`: the item to confirm and its status.
- `remote_agent_url` + `metadata`: the actionable callback reference to the exact
  calendar sub-conversation (calendar A2A endpoint plus its `context_id`/
  `task_id`, and by chaining the originating external `task_id`).

So "list what is pending" (step 6) is "list this user's/conversation's tasks in
`state=input-required`", and "confirm" (step 8) reads `remote_agent_url`/
`metadata` and emits the outbound A2A call to that precise target, then
transitions the task to `completed`. The correlation that links a confirmation
back to the right calendar sub-conversation is the task record; a separate store
would duplicate it.

### Deterministic Deposit

Prefer depositing the inbound request deterministically: the A2A server handler
creates the `input-required` task record on disk and notifies the human, with no
LLM turn required to persist it. An agent-mediated deposit (wake the agent, hope
it calls a store tool) risks dropping a confirmation request on a bad turn. The
confirmation agent then only runs for the human interaction — listing pending
items and acting on a confirmation.

### Actionable, Durable Callback Reference

A stored label is not enough. The pending entry must let the confirmation side
re-issue an authenticated A2A call to the calendar side, possibly days later:

- Durable credentials. The token used to call back the calendar agent cannot be
  a short-lived token captured at request time; use a persistent service-account
  credential stored in the secrets system, valid for the pending entry's whole
  lifetime, or the callback at step 8 fails on token expiry.
- Reachable target. If the calendar sub-conversation expired by TTL in the
  meantime, the callback has nowhere to land. Couple the lifetimes: expiry or
  cancellation of a calendar sub-conversation must invalidate the matching
  confirmation entry, so the human is never asked to confirm a dead request.

### State Machine, Idempotency, Scoping

- Per-entry state machine: `pending -> confirmed | rejected -> callback_sent ->
  done`. A confirmation past TTL is marked stale, not silently actioned.
- Idempotency: a human confirming twice triggers exactly one callback (dedup on
  `task_id`); a failed callback is retryable without double-booking the external
  side.
- RBAC scoping: the human sees only their own pending confirmations (filter by
  `user_id`/conversation), consistent with the resource RBAC matrix. Step 6 is a
  scoped query, never a global dump.

### Cross-Cutting Requirements

1. Outbound A2A as a non-blocking agent capability (Phase 3): the calendar and
   confirmation agents must be A2A clients with a send-and-continue tool that
   returns a task immediately, modeled on async delegate.
2. Long-lived `input-required` tasks with event-driven resumption and a timeout:
   `T_ext` and `T_cnf` stay open for an arbitrary human delay, persisted in task
   records (survive restarts), and must expire to a `canceled`/timeout state if
   the human never responds rather than hanging forever. Depends on the explicit
   task finalization hook.
3. The external agent must accept a callback: step 9 only works if it registered
   a push webhook (Phase 5) or polls its task. An external agent that only does
   synchronous request/response cannot receive a deferred confirmation. This is a
   constraint on the third party, outside PawFlow's control.

## Security

Required controls:

- Authenticate every non-public Agent Card and every task operation.
- Authorize by user, conversation, agent, and task ownership.
- Never reveal whether an unauthorized task exists.
- Scope Agent Cards so public cards do not leak private skills, relays, tools, prompts, or service IDs.
- Store remote credentials in the existing secrets system.
- Validate remote file references before copying to FileStore.
- Add SSRF protections for push notification webhooks and remote artifact URLs.
- Record audit events for remote sends, payments if paired with x402, task cancellation, and auth failures.

## Implementation Phases

### Phase 1: Local A2A server

- Add A2A schema models generated or validated from the official proto-derived schema.
- Add Agent Card generation for conversation agents.
- Add `SendMessage`, `GetTask`, `ListTasks`, and `CancelTask` for local agents.
- Persist A2A task records.
- Reuse `ConversationWriter`, `PendingQueue`, and `AgentLoopTask.wake_agent`.
- Add unit tests for auth, target agent routing, task state, and invalid content modes.

### Phase 2: Streaming

- Implement `SendStreamingMessage` and `SubscribeToTask` over SSE.
- Map PawFlow SSE events to A2A `StreamResponse` objects.
- Add terminal-state handling and resubscription tests.

### Phase 3: Remote A2A client

- Add remote A2A agent resources.
- Add Agent Card discovery, caching, and auth.
- Add remote send/stream/poll clients.
- Persist remote task correlations in local conversation metadata.
- Render remote agent messages in the same UI surfaces as local agents.

### Phase 4: Remote PawFlow-to-PawFlow mode

- Add helper UI/API to connect another PawFlow instance.
- Support remote conversation and remote agent selection.
- Preserve local aliases, remote IDs, and provenance in messages.

### Phase 5: Push notifications and advanced modes

- Add push notification config support.
- Add authenticated extended Agent Cards.
- Add structured data and file exchange coverage.
- Evaluate gRPC binding after JSON-RPC/REST behavior is stable.

## Test Plan

- Agent Card generation hides private fields in public mode.
- Authenticated extended cards include only authorized extra details.
- `SendMessage` to a local agent creates one transcript row, one pending queue entry, and one A2A task.
- Missing `target_agent`, invalid conversation, or unauthorized agent returns protocol errors.
- `SubscribeToTask` emits initial task state and closes on terminal state.
- Cancel maps to `AgentLoopTask.force_stop_agent` or the existing cancel path without affecting future turns.
- Remote A2A client handles direct message responses, task responses, streaming responses, and failed tasks.
- File parts are copied or referenced safely with user and conversation scoping.
- Push webhook URLs are rejected for private/internal networks unless explicitly allowlisted.
- A new client `context_id` creates an isolated sub-conversation with an empty context; a repeated `context_id` resumes the same sub-conversation.
- Two clients never see each other's messages: each isolated sub-conversation context contains only its own turns.
- A `display_only` projection into the parent conversation appears in the transcript and web chat but never enters any agent's prompt context.
- An expired context TTL reclaims the sub-conversation via `ConversationStore.cleanup()`; a resumed context past TTL is rejected, not silently recreated with lost history.
- Concurrent slot requests from two contexts cannot double-book: the calendar tool enforces atomic check-and-reserve.
- A multi-hop confirmation keeps `T_ext` and `T_cnf` in `input-required` across the human delay, and a later human confirmation resumes both via outbound A2A callbacks in order.
- Listing pending confirmations returns only the requesting user's `input-required` tasks; another user's pending items are never disclosed.
- A pending confirmation entry holds an actionable callback reference (`remote_agent_url` + `metadata`) that resolves to the exact calendar sub-conversation.
- Confirming the same item twice triggers exactly one outbound callback (idempotent on `task_id`).
- When a calendar sub-conversation expires by TTL, the matching confirmation entry is marked stale and is not actionable.
- A human confirmation that never arrives lets `T_ext`/`T_cnf` time out to `canceled` rather than hanging open.

## Open Questions

- Whether A2A tasks should live in `ConversationStore` extras, a dedicated runtime store, or both.
- Whether one public Agent Card should represent the whole PawFlow instance or only individually published agents.
- How much of assigned skills and tool filters should be reflected in Agent Skills.
- Whether remote A2A agents should be implemented as a new agent backend or a separate resource type linked into `conv_agents`.
- How an external client obtains its target in the multi-client pattern: the well-known Agent Card is instance-level but `SendMessage` is conversation-scoped. Options include an instance-level booking endpoint that allocates a sub-conversation on first contact, or a published conversation-scoped card per service.
- How parent/child sub-conversation links are modeled (a parent-id field in extras, a dedicated index, or both) and how they are hidden from the main conversation list.
- Whether the per-client `display_only` projection should be a summary or a full mirror of the sub-conversation turns, and where the drill-down view loads the full sub-conversation from.

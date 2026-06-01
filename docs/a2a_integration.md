# A2A Integration Design

This document describes how PawFlow should implement Agent2Agent (A2A) support as both an A2A server and an A2A client.

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
| Context ID | PawFlow `conversation_id`, or an opaque remote context ID for remote agents. |
| Task ID | A PawFlow A2A task record linked to an initiating message ID and target agent. |
| Message | PawFlow message rows with `role`, `content`, `source`, `msg_id`, `ts`, and `conversation_id`. |
| Part: text | Plain message content. |
| Part: file | FileStore reference, HTTP URL, or copied artifact depending on trust and size. |
| Part: structured data | JSON attachment or artifact part, not ad hoc string serialization. |
| Artifact | FileStore artifact plus metadata, linked to the A2A task. |
| Task status | Derived from pending/running/completed/error/canceled/input-required/auth-required state. |

The first implementation should persist a small A2A task index instead of inferring everything from transcript scans. Suggested path:

```text
data/runtime/a2a_tasks/{user_id}/{conversation_id}/{task_id}.json
```

Each task record should include:

```json
{
  "task_id": "...",
  "context_id": "conversation-id",
  "conversation_id": "conversation-id",
  "agent_name": "assistant",
  "user_id": "alice",
  "created_msg_id": "...",
  "last_msg_id": "...",
  "state": "working",
  "created_at": "...",
  "updated_at": "...",
  "remote": false,
  "remote_agent_url": "",
  "metadata": {}
}
```

Do not create anonymous fallbacks. Missing `conversation_id`, `agent_name`, `user_id`, or authentication scope must fail explicitly.

## Server-Side A2A

### Public Endpoints

Start with the HTTP+JSON or JSON-RPC binding. gRPC can come later.

Suggested routes:

```text
GET  /.well-known/agent-card.json
GET  /a2a/agents/{agent_name}/agent-card.json
GET  /a2a/conversations/{conversation_id}/agents/{agent_name}/agent-card.json
POST /a2a/conversations/{conversation_id}/agents/{agent_name}
GET  /a2a/conversations/{conversation_id}/tasks/{task_id}
GET  /a2a/conversations/{conversation_id}/tasks
POST /a2a/conversations/{conversation_id}/tasks/{task_id}:cancel
GET  /a2a/conversations/{conversation_id}/tasks/{task_id}:subscribe
POST /a2a/conversations/{conversation_id}/tasks/{task_id}/push-configs
```

The well-known card should only expose public, instance-level agents. Conversation-scoped cards should require auth unless explicitly published.

### Agent Card Generation

Generate Agent Cards from:

- `ResourceStore` agent definitions.
- `conv_agents` entries for conversation-scoped agents.
- Enabled skills, published tool filters, and package-provided capabilities.
- Auth policy from API keys, OAuth, or service account tokens.
- Supported modalities: text first, then FileStore-backed files, then structured JSON.

Do not leak private tools, relay paths, internal service IDs, provider session IDs, or full system prompts in public Agent Cards. Use authenticated extended cards for sensitive details.

### Send Message

`SendMessage` should validate the caller, target conversation, target agent, content modes, and accepted output modes. Then it should reuse the existing local agent path:

1. Resolve and authorize `conversation_id` and `agent_name`.
2. Validate the agent belongs to `conv_agents` with `require_agent_member(...)`.
3. Convert A2A `Message.parts` into a PawFlow message.
4. Stamp the message with `stamp_message(...)`.
5. Persist using `ConversationWriter.for_conversation(conv_id).enqueue_message(...)`.
6. Add to `PendingQueue.for_agent(conv_id, agent_name).enqueue(...)`.
7. Create or update the A2A task record.
8. Call `AgentLoopTask.wake_agent(conv_id, agent_name, delay=0.0)`.
9. Return a `Task` immediately unless the request explicitly asks for blocking behavior.

The PawFlow message should keep the invariant required by `ConversationStore`:

```json
{
  "role": "user",
  "content": "...",
  "source": {
    "type": "user",
    "name": "a2a:<client-id>",
    "target_agent": "assistant",
    "a2a": {
      "task_id": "...",
      "message_id": "...",
      "remote_context_id": "..."
    }
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

## Open Questions

- Whether A2A tasks should live in `ConversationStore` extras, a dedicated runtime store, or both.
- Whether one public Agent Card should represent the whole PawFlow instance or only individually published agents.
- How much of assigned skills and tool filters should be reflected in Agent Skills.
- Whether remote A2A agents should be implemented as a new agent backend or a separate resource type linked into `conv_agents`.

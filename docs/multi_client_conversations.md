# Multi-Client Conversations

PawFlow conversations are server-side state, not local UI state. The web chat, PawCode CLI, VS Code extension, API clients, and channel receivers can all publish to or subscribe to the same conversation.

## Core Idea

A conversation is identified by `conversation_id` and contains:

- persisted user, assistant, tool, plan, and task messages;
- selected agent and active agent list;
- per-agent context usage metadata;
- title and conversation extras;
- file attachments and FileStore references;
- relay bindings and resource scope.

Clients are interchangeable frontends over that state.

## Supported Clients

| Client | Role |
|---|---|
| Web chat | Main browser UI with SSE, file explorer, context editor, slash commands. |
| PawCode CLI | Terminal UI and stream-JSON compatibility. Relay lifecycle is external. |
| VS Code extension | Editor client with resource panel and selection commands. Relay lifecycle is external. |
| API/flows | `publishMessage`, `readConversation`, `spawnAgent`, and HTTP tasks can interact with conversations. |
| Messaging channels | Telegram, Discord, Slack, and WhatsApp receivers/senders can bridge messages into flows and agents. |

## Streaming Model

Agent work is started by an HTTP request that returns an immediate ACK. The actual turn runs in a background thread and publishes events to `ConversationEventBus`. Clients consume the event stream via SSE.

Common event types include:

- `thinking`
- `token`
- `tool_start`
- `tool_result`
- `message_meta`
- `done`
- `error_event`
- `message_queued`
- `btw_*`
- `plan_created`
- `plan_updated`
- `title_generated`

## Concurrent Messages

If a client sends a message while an agent is running:

- CLI-backed providers may support direct injection/preemption;
- API providers queue the message until the active turn completes;
- the scheduler wakes the agent to process queued messages;
- the conversation generation counter prevents stale turns from continuing after force-stop.

## Multi-Agent Context

Messages are differentiated for each agent. An agent sees its own messages normally, while other agents' messages are prefixed as context. This prevents one agent from accidentally treating another agent's instruction as a direct user command.

Examples:

- `[Agent reviewer]: ...`
- `[User to builder]: ...`
- `[Agent worker in Task t_123]: ...`

## Flow Integration

Flows can participate in conversations through tasks:

- `createConversation`
- `publishMessage`
- `readConversation`
- `spawnAgent`
- `assignTaskToAgent`
- `cancelAgentTask`
- `agentSSEStream`

This lets a deterministic workflow create a conversation, ask an agent to handle part of the work, read the answer, and continue the pipeline.

## Operational Notes

- Conversation writes are queued through `ConversationWriter`; shutdown drains the queue to avoid message loss.
- File outputs are stored through FileStore and referenced by `fs://filestore/<id>/<name>` URLs.
- Relay bindings can be per-conversation and per-agent.
- Auto-title, auto-memory, auto-compaction, and task rescheduling run after turns where configured.

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

## Storage Layout

Conversation transcripts and contexts are logical JSONL streams. PawFlow can read legacy flat files such as `transcript.jsonl`, `shared.jsonl`, and `{agent}/context.jsonl`, while new or rewritten streams are stored as bounded segment directories such as `transcript/`, `shared/`, and `{agent}/context/` with an `index.json`. New segments default to 5,000 rows (`PAWFLOW_JSONL_SEGMENT_ROWS`). Existing larger segments remain readable and are not resegmented automatically.

Conversation startup warms metadata from `extras.json`, segment indexes, and the latest transcript row instead of scanning every transcript line. Hot write paths persist `_meta_msg_count`, `_meta_preview`, `_meta_updated_at`, and `_meta_max_seq` so list/count/seq bootstrap stays O(conversations) rather than O(transcript-size).

`list_conversations` reconciles its warm cache against durable conversation
directories before returning. If an edit operation invalidates an in-memory
entry while the process stays loaded, the sidebar list must still re-adopt the
conversation from `extras.json`/transcript metadata on the next list request.

Each conversation keeps a small Git repository for recent rollback history. PawFlow bounds that history with `PAWFLOW_CONV_GIT_RETENTION_DAYS` (default 7) and `PAWFLOW_CONV_GIT_RETENTION_COMMITS` (default 250), then expires reflogs and runs `git gc --prune=now` during retention maintenance so old snapshot objects are actually reclaimable.

Conversation Git snapshots track only durable state: transcript, shared context, extras, and bindings. Per-agent contexts and `summaries/_shared` bucket files are derived caches; snapshots untrack them, and rollback or branch switch deletes them so they are rebuilt from the restored transcript/shared state.

Use `/git-prune` (`/prune-git`) to run that retention immediately for the current conversation. It uses the same context-operation lock/progress channel as `/compact`, so active work is stopped/blocked while Git rewrites history and garbage-collects old objects. The command reports commit and `.git` size before/after when it completes.

Code that needs conversation rows must go through `ConversationStore` or `SegmentedJsonl` instead of opening those files directly. PawFlow exports still write flat `transcript.jsonl` and context JSONL files inside `.pfconv.zip` archives so archives remain portable and easy to inspect.

PawFlow conversation archives are full restore archives by default for conversation state: transcript, shared context, per-agent context files, extras, bindings, and `summaries/_shared` bucket caches are exported together so an imported conversation can resume without recomputing bucket summaries. The export dialog can also include conversation-scoped FileStore objects. When FileStore restore is enabled during import, PawFlow restores those files under the new `(user_id, conversation_id)` scope, preserves file IDs when possible, remaps colliding file IDs, and patches imported JSON/JSONL references so `/files/{file_id}` and `fs://filestore/{file_id}/...` links keep working.

Existing installations can migrate stored conversations offline. First migrate
the logical row format so transcript, shared context, and per-agent contexts all
store the same provider-turn rows:

```bash
python scripts/migrate_transcript_context_format.py --dry-run
python scripts/migrate_transcript_context_format.py --apply
```

The row-format migration rewrites legacy assistant `thinking` fields and
assistant `tool_calls` arrays into linked rows: `assistant` anchor,
`thinking` child, `tool_call` child, and `tool` result child linked by
`parent_message_id` while preserving `tool_call_id`.

Display traces use the same append-only rule as the rest of the transcript:
`sub_agent_trace` is the visible anchor row, and later `trace_update` rows carry
incremental trace entries/content. Readers merge those updates into the anchor
for display; producers must not rewrite the transcript for trace progress.

Then migrate flat conversation logs to segmented storage if needed:

```bash
python scripts/migrate_segmented_jsonl.py --dry-run
python scripts/migrate_segmented_jsonl.py --apply
```

The row-format migration backs up changed streams under
`_transcript_context_migration_backup/`. The segmented-storage migration backs
up each flat file under `_jsonl_migration_backup/` before replacing it with
segments.

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

## UI Action Results

The web chat does not rely on synchronous `/api/ui` responses for action payloads. Each `action$()` call sends a `_call_id` and a `_reply_conversation_id` that points to a per-tab UI SSE bus. The HTTP response only acknowledges acceptance; the action result is published as `command_result` on that reply bus and routed back to the matching subscriber by `_call_id`.

The UI action bus is separate from the active conversation SSE stream because the web chat may close and reopen the conversation stream while rendering history. System clients that do not provide a reply bus, such as relay registration or CLI bootstrap calls, can still receive an inline HTTP result.

UI background actions are bounded by `PAWFLOW_MAX_BG_ACTIONS` (default `32`). Polling actions such as `list_active` also avoid overlapping browser requests; a stale poll is unsubscribed before a replacement starts.

## Concurrent Messages

The agent task/thought watchdog repairs missing scheduler entries after restarts or rare races. It scans the conversation store at most once per `PAWFLOW_AGENT_WATCHDOG_INTERVAL_SECONDS` (default `300`) instead of every poll tick.

If a client sends a message while an agent is running:

- CLI-backed providers may support direct injection/preemption;
- API providers queue the message until the active turn completes;
- the scheduler wakes the agent to process queued messages;
- the conversation generation counter prevents stale turns from continuing after force-stop.

## Multi-Agent Context

Messages are differentiated for each agent. An agent sees its own messages normally, while other agents' messages are prefixed as context. This prevents one agent from accidentally treating another agent's instruction as a direct user command.

Autonomous agent tasks run in isolated sub-conversations named `parent::task::task_id`; verifier turns use `parent::task_verify::task_id`. When a task reaches a terminal state, or is cancelled/deleted, PawFlow deletes those sub-conversations and invalidates any CLI provider sessions bound to them so completed task context does not leak into later runs.

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

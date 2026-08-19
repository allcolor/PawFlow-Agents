# AG-UI Integration

PawFlow exposes published agents through the AG-UI protocol — the open,
event-based Agent-User Interaction Protocol used by CopilotKit and a growing
ecosystem of frontends.

Sources:

- Protocol repository: https://github.com/ag-ui-protocol/ag-ui
- Documentation: https://docs.ag-ui.com/
- Python SDK event/type definitions: `sdks/python/ag_ui/core` in the repo

## Where AG-UI fits

- **MCP** gives agents tools.
- **A2A** lets agents talk to other agents.
- **AG-UI** brings agents into user-facing applications: a client POSTs one
  `RunAgentInput` and reads back an SSE stream of standard events
  (`RUN_STARTED`, `TEXT_MESSAGE_*`, `TOOL_CALL_*`, `THINKING_*`,
  `RUN_FINISHED`, `RUN_ERROR`, ...).

## One publication, two protocols

AG-UI reuses the existing **A2A publications** (`core/a2a_store.py`): the
same publish action, Bearer keys, enable/disable flag, and per-client context
resolution serve both protocols. Publishing an agent through the A2A dialog
in the webchat makes it reachable at BOTH:

- `POST /a2a/{publication_id}/message:send` (A2A 1.0, task-based)
- `POST /agui/{publication_id}` (AG-UI, streaming SSE)

## Endpoints

| Method | Path | Description |
| ------ | ---- | ----------- |
| `GET` | `/agui/{publication_id}` | Small JSON descriptor (name, description, agent, context policy). Bearer auth. |
| `POST` | `/agui/{publication_id}` | Run the agent with a `RunAgentInput` body; responds `text/event-stream`. Bearer auth. |

Authentication, origin checks, and publication resolution are shared with
the A2A endpoint (`services/a2a_server_endpoint.py`). Routes are registered
lazily: at listener startup when publications exist, and immediately when a
publication is created (`services/agui_server_endpoint.py`).

## Run semantics

- `threadId` maps to a per-key A2A context (requested id `agui_<threadId>`).
  With the default **isolated** context policy each AG-UI thread gets its own
  internal PawFlow conversation with durable server-side history; **shared**
  publications run in the conversation the agent lives in.
- AG-UI clients send the full message history on every run; PawFlow keeps its
  own durable context, so only the **last user message** is forwarded as the
  new prompt. Multimodal parts contribute their text; URL-sourced attachments
  are passed as descriptive lines.
- `context` entries are appended to the prompt as an `[AG-UI context]` block.
- `tools` (frontend-declared) are appended as an informational block. PawFlow
  executes its own server-side tools and streams them as
  `TOOL_CALL_START/ARGS/END` + `TOOL_CALL_RESULT`; a structured
  frontend-tool round-trip (a run finishing on a client-executed tool call)
  is not implemented yet.
- Client disconnect mid-run force-stops the turn for isolated contexts
  (private to that client); shared conversations are left running.

## Event mapping

| PawFlow bus event | AG-UI events |
| ----------------- | ------------ |
| turn accepted | `RUN_STARTED` |
| `token` (streamed answer delta, per `msg_id`) | `TEXT_MESSAGE_START` / `TEXT_MESSAGE_CONTENT` / `TEXT_MESSAGE_END` |
| `thinking_delta` | `THINKING_START` + `THINKING_TEXT_MESSAGE_START/CONTENT/END` + `THINKING_END` |
| `thinking` heartbeat (no text) | ignored |
| `tool_call` | `TOOL_CALL_START` + `TOOL_CALL_ARGS` + `TOOL_CALL_END` |
| `tool_result` | `TOOL_CALL_RESULT` |
| `new_message` (persisted assistant row) | deduplicated against streamed deltas by `msg_id`; unstreamed rows are emitted as a full START/CONTENT/END triplet |
| `done` | `RUN_FINISHED` with `result` (final text) and `outcome: {type: "success"}` |
| `error_event` / failures | `RUN_ERROR` with `message` and `code` |

All events are camelCase JSON, one per `data:` SSE frame, `None` fields
omitted — matching the AG-UI Python SDK's `EventEncoder`. While a turn is
silent (long tool runs) the stream carries `: ping` SSE comments every 15s;
SSE parsers ignore comments, so clients see no spurious events.

## Quick test

```bash
curl -N -X POST "$BASE/agui/$PUBLICATION_ID" \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"threadId":"demo","runId":"run1","state":null,"forwardedProps":null,
       "tools":[],"context":[],
       "messages":[{"id":"1","role":"user","content":"Hello!"}]}'
```

## Implementation

- `core/agui_runtime.py` — RunAgentInput validation, bus-event → AG-UI event
  translation (`_TurnTranslator`), and the run generator
  (`run_agent_stream`) built on `AgentRuntimeAPI.submit_message(live_callback)`
  + `wait_for_done`.
- `services/agui_server_endpoint.py` — HTTP routes, auth via the shared A2A
  publication resolver, streaming response via `req.complete_stream`.
- Tests: `tests/test_agui_endpoint.py`.

## Not implemented yet

- Structured frontend tool calls (run pausing on a client-executed tool).
- `STATE_SNAPSHOT` / `STATE_DELTA` shared-state synchronization.
- Interrupt/resume (`RunAgentInput.resume`, `RUN_FINISHED` interrupt outcome).
- Binary/inline (base64) attachment ingestion.
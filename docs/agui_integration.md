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
  `STATE_*`, `RUN_FINISHED`, `RUN_ERROR`, ...).

## One publication, two protocols

AG-UI reuses the existing **A2A publications** (`core/a2a_store.py`): the
same publish action, Bearer keys, enable/disable flag, and per-client context
resolution serve both protocols. Publishing an agent through the A2A dialog
in the webchat makes it reachable at BOTH:

- `POST /a2a/{publication_id}/message:send` (A2A 1.0, task-based)
- `POST /agui/{publication_id}` (AG-UI, streaming SSE)

In the webchat, the A2A panel (sidebar → A2A) lists both URLs for every
publication with a copy button each, so exporting an agent to an AG-UI
frontend is the same gesture as publishing it for A2A. The opposite direction
— importing an external AG-UI agent as a conversation participant — is the
`external_agui` runtime documented in `docs/AGENT_SYSTEM.md` (add an agent
with the runtime "External AG-UI" and an `aguiConnection` service).

## Endpoints

| Method | Path | Description |
| ------ | ---- | ----------- |
| `GET` | `/agui/{publication_id}` | JSON descriptor (name, description, agent, context policy, `capabilities`). Bearer auth. |
| `POST` | `/agui/{publication_id}` | Run the agent with a `RunAgentInput` body; responds `text/event-stream`. Bearer auth. |

Authentication, origin checks, and publication resolution are shared with
the A2A endpoint (`services/a2a_server_endpoint.py`). Routes are registered
lazily: at listener startup when publications exist, and immediately when a
publication is created (`services/agui_server_endpoint.py`).

## Run semantics

- `threadId` is client-chosen: it maps to a deterministic per-key A2A
  context created on first use (`A2AStore.ensure_named_context`; the stored
  context id is a digest of publication + key + thread, so equal thread ids
  from different keys or publications never collide). With the default
  **isolated** context policy each AG-UI thread gets its own internal
  PawFlow conversation with durable server-side history; **shared**
  publications run in the conversation the agent lives in.
- AG-UI clients send the full message history on every run; PawFlow keeps
  its own durable context, so only the **trailing segment after the last
  assistant message** is new input: user text, inline attachments, and
  frontend-tool result (`role:"tool"`) messages.
- Multimodal user content: `text` parts form the prompt; inline base64
  parts (`source.type == "data"`) become PawFlow attachments (vision /
  document ingestion); URL parts are passed as descriptive lines the agent
  can fetch itself.
- `context` entries are appended to the prompt as an `[AG-UI context]`
  block; a non-empty `forwardedProps` is appended as JSON (8 KB cap).
- Client disconnect mid-run force-stops the turn for isolated contexts
  (private to that client); shared conversations are left running.

## Interactive features (isolated publications)

The three interactive AG-UI features require an **isolated** context policy:
on a shared publication the underlying conversation belongs to the owner and
must not grow client-declared tools or state (frontend tools then degrade to
an informational prompt block, state and interrupts are off). The `GET`
descriptor advertises this via `capabilities`.

Per-thread data (declared tools, shared state, pending interrupts) lives in
the `agui` conversation extra, synced by `core/agui_runtime.py` before each
run and served to the agent by handlers in `core/agui_tools.py`, registered
per turn right after the dynamic-tool loader (`tasks/ai/_agentctx_p1.py`).
The handlers carry `_origin="agui"` / `_origin_scope="agui:<conversation>"`
and `core.tool_mcp_filters.is_tool_enabled` makes them visible ONLY inside
their own conversation — client-declared tools can never surface elsewhere.
A frontend tool whose name collides with a builtin or dynamic tool is
skipped (the existing tool wins). Note: an agent instance with a `tools`
allowlist filters frontend tools out like any other tool.

### Frontend tools

`RunAgentInput.tools` are declared to the LLM as real callable tools. When
the agent calls one, the call streams to the client as
`TOOL_CALL_START/ARGS/END` (no `TOOL_CALL_RESULT` — the client executes it),
the server-side handler returns a placeholder telling the agent the result
arrives later, and the agent ends its turn (`RUN_FINISHED`). The client
executes the tool and sends the next `RunAgentInput` whose trailing
`role:"tool"` message (matching `toolCallId`) is forwarded to the agent as
the new input. This is the standard AG-UI tool-based generative UI /
human-in-the-loop loop.

### Shared state

- `RunAgentInput.state` (non-null) seeds or replaces the thread state.
- Every run whose thread has a state opens with a `STATE_SNAPSHOT` right
  after `RUN_STARTED`.
- The agent reads and mutates the state through the `agui_state` tool
  (`get` / `set` / `patch` with RFC 6902 JSON Patch — `add`, `replace`,
  `remove`, `test`). `set` streams a `STATE_SNAPSHOT`, `patch` streams a
  `STATE_DELTA` with the applied operations, live during the run.

### Interrupts / resume

The agent pauses a run with the `agui_interrupt` tool (`reason`, optional
`message` and `response_schema`). The run then finishes with
`outcome: {type: "interrupt", interrupts: [...]}` instead of `success`.
The client answers in the next `RunAgentInput.resume` array
(`interruptId`, `status: resolved|cancelled`, `payload`); each entry is
settled against the thread's pending interrupts and forwarded to the agent
as an `[AG-UI interrupt ...]` block. A resume-only run (no new user
message) is valid input.

## Event mapping

| PawFlow bus event | AG-UI events |
| ----------------- | ------------ |
| turn accepted | `RUN_STARTED` |
| stored thread state | `STATE_SNAPSHOT` (once, after `RUN_STARTED`) |
| `token` (streamed answer delta, per `msg_id`) | `TEXT_MESSAGE_START` / `TEXT_MESSAGE_CONTENT` / `TEXT_MESSAGE_END` |
| `thinking_delta` | `THINKING_START` + `THINKING_TEXT_MESSAGE_START/CONTENT/END` + `THINKING_END` |
| `thinking` heartbeat (no text) | ignored |
| `tool_call` | `TOOL_CALL_START` + `TOOL_CALL_ARGS` + `TOOL_CALL_END` |
| `tool_result` | `TOOL_CALL_RESULT` (suppressed for frontend tools — the client produces the real result) |
| `agui_state_snapshot` (from `agui_state` set) | `STATE_SNAPSHOT` |
| `agui_state_delta` (from `agui_state` patch) | `STATE_DELTA` |
| `agui_interrupt` (from `agui_interrupt`) | collected into the `RUN_FINISHED` interrupt outcome |
| `new_message` (persisted assistant row) | deduplicated against streamed deltas by `msg_id`; unstreamed rows are emitted as a full START/CONTENT/END triplet |
| `done` | `RUN_FINISHED` with `result` (final text) and `outcome` (`success`, or `interrupt` with the collected interrupts) |
| `error_event` / failures | `RUN_ERROR` with `message` and `code` |

All events are camelCase JSON, one per `data:` SSE frame, `None` fields
omitted — matching the AG-UI Python SDK's `EventEncoder`. While a turn is
silent (long tool runs) the stream carries `: ping` SSE comments every 15s;
SSE parsers ignore comments, so clients see no spurious events.

## Quick test

```bash
curl -N -X POST "$BASE/agui/$PUBLICATION_ID" \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"threadId":"demo","runId":"run1","state":{"counter":0},
       "forwardedProps":null,"context":[],
       "tools":[{"name":"confirm","description":"Ask the user to confirm",
                 "parameters":{"type":"object","properties":
                               {"question":{"type":"string"}}}}],
       "messages":[{"id":"1","role":"user","content":"Hello!"}]}'
```

## Implementation

- `core/agui_runtime.py` — RunAgentInput parsing (`parse_run_input`), prompt
  assembly, per-run AG-UI document sync (`_prepare_agui_doc`), bus-event →
  AG-UI event translation (`_TurnTranslator`), and the run generator
  (`run_agent_stream`) built on `AgentRuntimeAPI.submit_message(live_callback)`
  + `wait_for_done`.
- `core/agui_tools.py` — per-conversation handlers (frontend tools,
  `agui_state`, `agui_interrupt`), the `agui` conversation extra, RFC 6902
  JSON Patch, and `register_agui_conversation_tools`.
- `core/a2a_store.py` — `ensure_named_context` (client-chosen AG-UI thread
  ids get-or-create their A2A context).
- `core/tool_mcp_filters.py` — conversation-only visibility of AG-UI
  handlers (`origin == "agui"`).
- `services/agui_server_endpoint.py` — HTTP routes, auth via the shared A2A
  publication resolver, streaming response via `req.complete_stream`.
- Tests: `tests/test_agui_endpoint.py`, `tests/test_agui_tools.py`.

## Deliberately not emitted

Optional protocol surface with no PawFlow source signal (or redundant with
the streamed events): `MESSAGES_SNAPSHOT`, `STEP_STARTED/FINISHED`,
`ACTIVITY_*`, `TEXT_MESSAGE_CHUNK`/`TOOL_CALL_CHUNK`, the newer
`REASONING_*` family (the equivalent `THINKING_*` pair is emitted), `RAW`,
`CUSTOM`, and the protobuf encoding (`application/vnd.ag-ui.event+proto` —
SSE JSON is the baseline every AG-UI client speaks). PawFlow is an AG-UI
**server**; an AG-UI *client* (consuming third-party AG-UI agents as PawFlow
tools) does not exist yet.

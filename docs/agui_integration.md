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
arrives later, and the agent ends its turn (`RUN_FINISHED`). The agent may
emit SEVERAL frontend calls in one turn — they are delivered as one batch
and each result arrives in the next run. The client
executes the tool and sends the next `RunAgentInput` whose trailing
`role:"tool"` message (matching `toolCallId`) is forwarded to the agent as
the new input. This is the standard AG-UI tool-based generative UI /
human-in-the-loop loop.

Tool declarations accept `inputSchema` (WebMCP `registerTool`) or
`parameters` (plain AG-UI) — `inputSchema` wins when both are present —
plus optional `annotations`: unverified client hints (`readOnlyHint`,
`destructiveHint`, ...) surfaced to the agent as presentation only, never
a permission. Only scalar hint values are kept (strings capped at 200
chars). Everything the client declares, and every result it returns, is
treated as untrusted data.

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

## Managed frontend execution (WebMCP widget)

Managed mode (plan `docs/WEBMCP_INTEGRATION_PLAN.md` §B1-X) is a
**publication-fixed** setting: it is enabled in the publish dialog (or
`a2a_publication_configure` with `managed_mode: true`), requires the
isolated context policy, and is announced by the descriptor
(`executionMode: "managed"`, `capabilities.managedBatch`, `actions`).
A request can never select the mode.

- **Acquire before SSE**: a managed `POST` runs the durable admission
  synchronously (`core/_agui_managed_runtime.py`) BEFORE the stream
  opens, so a busy thread, replayed run, ghost/mismatched `parentRunId`
  or incomplete prior batch is a real HTTP `409` (`thread_busy`,
  `parent_mismatch`, `batch_incomplete`, `idempotency_conflict`,
  `idempotency_expired`, `thread_rotated`) instead of an SSE
  `RUN_ERROR`. The admission persists the canonical body
  (`payload_json`) for recovery and opens the run's journal row.
  Retrying the same `runId` with the same body replays.
- **A durable pilot owns the run — never the HTTP subscriber**: the
  endpoint starts a background pilot after the acquire; the pilot
  adopts the run (`reserved → running`, single lease winner), submits
  the agent turn, appends every AG-UI event to the durable journal,
  records frontend calls, heartbeats the lease and terminalizes via
  `finish_agui_turn`. The SSE response generator only TAILS the
  journal (replay from the committed prefix, then follow). A client
  disconnect detaches that subscriber and nothing else: the run keeps
  executing, heartbeating, and journals its terminal without any
  subscriber. Cancellation is explicit, never inferred from a
  disconnect.
- **State-driven retry, no resubmission**: only a `reserved` admission
  may start THE single first submission (initial POST, or recovery
  after a crash between acquire and pilot start). `accepted`/`running`
  admissions are owned (stale ones are reconciled by the heartbeat
  sweep into `orphaned`/`run_lost`); `terminal`/`orphaned` runs only
  replay their journal. After the sweep orphans a run, the pilot's
  lease checks refuse every further effect: no ledger record, no
  journal append, no finish.
- **Catalogue identity is mandatory**: every declared frontend tool must
  carry `catalogueId` + `catalogueVersion`; a managed run that calls a
  tool declared without them fails closed (`RUN_ERROR
  catalogue_incomplete`).
- **Batch freeze**: emitted frontend calls are recorded in the batch
  ledger during the run; a successful terminal freezes the batch and
  `RUN_FINISHED` carries `batchToken` with
  `outcome: {type: "managed_batch"}` (no pending calls →
  `outcome: {type: "success"}`, no token).
- **Widget drive**: the widget claims and executes the batch through
  `POST /agui/{publication_id}?action=claim_batch|begin|deposit|renew`,
  credential in the `X-PawFlow-Exec-Token` header (never a query
  string). `claim_batch` is idempotent per `batchClaimId` and returns
  the `ownerToken` and per-call receipts; `begin` re-checks the live
  catalogue identity; `deposit` follows the closed per-call state matrix
  (duplicates replay, conflicting payloads are `409 receipt_conflict`).
- **Attach & cancel (B1-J)**: every managed admission mints a run
  handle in the same transaction; the journaled `RUN_STARTED` carries
  the derived `attachToken` and `cancelToken` (v8.2 scheme —
  self-addressing, byte-identical re-derivation, `runId` never an
  addressing key, uniform `token_invalid` failures).
  `POST ?action=attach` + the attach token in `X-PawFlow-Exec-Token`
  re-opens the run's SSE with a gapless replay from the caller's
  `afterSeq` watermark. The initial POST and every attach atomically
  advance a durable per-run subscriber epoch; the response exposes it in
  `X-PawFlow-Subscriber-Epoch`, and an older tail detaches before its next
  frame as soon as a newer subscriber takes over. This subscriber change
  never starts, stops, or otherwise owns the pilot. Attach can never admit
  and never starts a pilot. `DELETE /agui/{publication_id}` +
  `X-PawFlow-Cancel-Token`
  cancels explicitly: idempotent and journaled (`RUN_ERROR cancelled`
  terminal, lease cut, fence bumped, pending calls abandoned — the
  pilot's next lease check refuses any further effect); replays return
  the recorded terminal. The descriptor announces both (`actions`
  includes `attach`; `cancel: {method, header}`).
- **Follow-up**: once every call is terminal the batch completes; the
  next run names it via `parentRunId` and consumes it atomically at
  acquire. A `parentRunId` that binds to nothing is rejected
  (`409 parent_mismatch`).
- **Thread TTL**: `thread_ttl_seconds` (publish dialog or
  `a2a_publication_configure`; `0` = no expiry) is announced as
  `threadTtlSeconds` in the descriptor; expiry is armed at batch
  completion (or run end without a batch), never while a batch is open.

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
- `core/a2a_store.py` and `core/_a2a_turn_*.py` — generation-aware thread
  contexts, durable run journal, managed batches/claims/receipts, fencing,
  and TTL lifecycle.
- `core/_agui_managed_runtime.py` — synchronous managed-run admission and
  replay plus the streamed agent runner.
- `core/tool_mcp_filters.py` — conversation-only visibility of AG-UI
  handlers (`origin == "agui"`).
- `services/agui_server_endpoint.py` and `services/_agui_actions.py` — HTTP
  routes, shared publication authentication, managed action dispatch, and
  streaming responses via `req.complete_stream`.
- Tests: `tests/test_agui_endpoint.py`, `tests/test_agui_tools.py`,
  `tests/test_agui_thread_ttl.py`, `tests/test_agui_turn_ledger.py`,
  `tests/test_agui_turn_acquire.py`, `tests/test_agui_managed_batch.py`,
  `tests/test_agui_managed_endpoint.py`, and
  `tests/test_agui_managed_runtime.py`.

## Deliberately not emitted

Optional protocol surface with no PawFlow source signal (or redundant with
the streamed events): `MESSAGES_SNAPSHOT`, `STEP_STARTED/FINISHED`,
`ACTIVITY_*`, `TEXT_MESSAGE_CHUNK`/`TOOL_CALL_CHUNK`, the newer
`REASONING_*` family (the equivalent `THINKING_*` pair is emitted), `RAW`,
`CUSTOM`, and the protobuf encoding (`application/vnd.ag-ui.event+proto` —
SSE JSON is the baseline every AG-UI client speaks). PawFlow is an AG-UI
**server**; an AG-UI *client* (consuming third-party AG-UI agents as PawFlow
tools) does not exist yet.

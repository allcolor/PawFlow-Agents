# ACP Integration — Complete Implementation Plan

Status: reviewed and corrected; implementation is gated on an approved baseline
commit for the pre-existing `allow_refresh` worktree patch
Protocol baseline: ACP stable protocol version 1
SDK baseline: agent-client-protocol 0.12.1
Scope: outbound generic ACP provider, specialized Gemini CLI ACP provider, and inbound PawFlow ACP agent publication
Primary outcome: PawFlow can consume any configured ACP agent as an LLM provider and can expose a published PawFlow agent to ACP clients without maintaining a second JSON-RPC implementation.

## 1. Decision summary

PawFlow will support ACP in three deliberately separate roles.

| Role | Public name | Process owner | ACP side implemented by PawFlow |
|---|---|---|---|
| Generic outbound provider | acp | PawFlow launches a configured ACP agent command | ACP client |
| Specialized Gemini provider | gemini | Existing Gemini pool launches gemini --acp | ACP client with Gemini-specific policy |
| Inbound published agent | pawflow-acp | An IDE launches a local proxy; PawFlow serves the agent remotely | ACP agent |

The official Python SDK is the sole ACP schema, framing, request-routing, and
connection implementation. PawFlow must depend on the pinned SDK release and
delete the hand-written JSON-RPC framing after the migrated Gemini tests pass.

The generic acp provider and gemini provider are not aliases:

- acp accepts an explicit executable, argv, environment, authentication method,
  and working-directory policy. It exposes only protocol-defined capabilities.
- gemini keeps its existing OAuth pool, model/thinking configuration, container
  lifecycle, warm-session registry, image policy, capacity classification,
  usage extraction, and Gemini-specific tool rendering.
- both use the same typed ACP connection/session runner.
- neither provider routes through external_mcp or the published-agent runtime.

Inbound ACP reuses the existing A2A publication, key, isolated-context, agent
runtime, and event-bus authorities. One publication and one one-time pfa2a_ key
can authorize A2A, AG-UI, standard APIs, and ACP. No second publication store or
key family is introduced.

## 2. Non-goals

This change does not:

- invent an ACP dialect or vendor extension;
- expose PawFlow's internal HTTP API directly to an IDE;
- use OpenAI Responses as a hidden ACP tunnel;
- implement a second remote tool protocol;
- make generic acp inherit Gemini OAuth behavior;
- make gemini configurable through arbitrary command execution;
- replace A2A, AG-UI, OpenAI-compatible, Anthropic-compatible, or MCP exports;
- add automatic provider fallback;
- execute shell command strings;
- infer an authentication method when an ACP agent advertises more than one;
- claim support for an optional ACP capability before its implementation and
  conformance tests exist.

## 3. Official protocol and SDK boundary

Pin agent-client-protocol==0.12.1 in the runtime dependency set. Pin
`websockets>=13.0` separately for the local proxy instead of selecting the
SDK's experimental `http` extra: PawFlow does not use the SDK's ASGI HTTP or
WebSocket server, and the dependency must state the runtime it actually owns.
The SDK metadata declares a lower bound of 12.0, but its 0.12.1 WebSocket
client imports `websockets.asyncio`, which is absent from websockets 12.0;
13.0 is therefore PawFlow's tested effective floor.
The
implementation must use the public API exported by acp:

- connect_to_agent for outbound ACP connections;
- run_agent for inbound ACP agents;
- spawn_agent_process or spawn_stdio_transport for argv-based outbound launch;
- generated acp.schema Pydantic models for every request, response, content
  block, capability, update, permission option, and stop reason;
- Agent and Client protocols for the two adapters.

Do not import deprecated AgentSideConnection or ClientSideConnection directly.
Do not copy generated schema classes into PawFlow. Do not parse or construct
JSON-RPC envelopes in provider code.

The stable protocol version is negotiated during initialize. PawFlow sends the
SDK's current PROTOCOL_VERSION and rejects an incompatible response with a
typed LLMClientError or ACP request error. Unstable protocol support is off.

The dependency is exact rather than open-ended because generated discriminator
models and optional methods move with the ACP schema. An upgrade is a dedicated
change: update the pin, regenerate fixtures from the new official models, run
both conformance suites, and update this document and the user reference.

## 4. Existing PawFlow authorities to reuse

### 4.1 Shared agent runtime

core/agent_runtime_api.py remains the only ingress from an external transport
into AgentLoopTask. Inbound ACP converts a prompt into AgentIngressMessage
values, submits AgentStructuredRequest, streams live events through
live_callback, and obtains the terminal through AgentRuntimeAPI.wait_for_done.

The adapter must not call AgentLoopTask.execute directly outside
AgentRuntimeAPI and must not append a second copy of the user prompt.

### 4.2 Publications, keys, and isolated contexts

core/a2a_store.py and its existing a2a_publications, a2a_api_keys, and context
lifecycle remain authoritative. ACP adds transport configuration and ACP session
rows to that store; it does not create an AcpPublicationStore.

Existing owner-only actions in tasks/ai/actions/_agentres_k7.py configure the
transport and return its runtime summary. Existing raw keys remain one-time
only and shared across enabled transports. Revocation and publication deletion
invalidate ACP connections and sessions as well.

Each external ACP session resolves to an isolated PawFlow child conversation
owned by the publication owner. It never writes external traffic into the
owner's source conversation.

### 4.3 Event and approval systems

core/conversation_event_bus.py remains the source of live token, thinking,
tool, progress, usage, done, and error events.

core/tool_approval.py remains the policy authority. ACP supplies a
request-scoped responder for an exact turn; it does not bypass the gate or
invent an independent approval allowlist.

### 4.4 Relay-backed I/O

All filesystem and terminal effects remain relay-routed. For outbound ACP,
client requests from the ACP agent are implemented through PawFlow's existing
filesystem and terminal services. For inbound ACP, request-scoped tool handlers
call the connected ACP client's filesystem and terminal methods. Neither path
accesses the PawFlow server filesystem as a substitute for the advertised
workspace.

### 4.5 Existing Gemini behavior

The following remain Gemini authorities:

- core/llm_providers/gemini.py;
- core/llm_providers/_gemini_stream.py;
- core/llm_providers/gemini_session.py;
- core/gemini_pool.py and core/gemini_live_registry.py;
- Gemini model, OAuth, context, capacity, attachment, preemption, and live
  session tests.

The transport responsibilities currently in
core/llm_providers/_gemini_acp.py move to shared SDK-backed modules. Helpers
that are truly Gemini-specific stay under the Gemini provider.

## 5. Target architecture

### 5.1 Outbound

~~~text
AgentLoopTask
    |
    v
LLMClient provider dispatch
    |
    +-- provider=acp ------> GenericAcpProviderPolicy
    |
    +-- provider=gemini ---> GeminiAcpProviderPolicy
                                 |
                                 v
                    shared AcpProcessSession
                    official SDK connect_to_agent
                                 |
                     stdio to configured ACP agent
                                 |
              ACP session updates and client requests
                                 |
        PawFlow callbacks / relay I/O / ToolApprovalGate
~~~

AcpProcessSession owns exactly one connection, process, event loop, consumer,
and session-state record. It exposes a synchronous iterator/callback facade to
LLMClient while the SDK remains async internally.

### 5.2 Inbound

~~~text
IDE or ACP client
       |
       | ACP JSON-RPC over stdio
       v
local pawflow-acp proxy
       |
       | authenticated ACP text frames over WebSocket
       v
PawFlow ACP WebSocket endpoint
       |
       | PawFlow raw-socket Transport adapter
       | official SDK run_agent
       v
PawFlowAcpAgent
       |
       v
AgentRuntimeAPI -> AgentLoopTask -> ConversationEventBus
       ^
       |
ACP client fs / terminal / permission calls traverse the same connection
~~~

The local proxy is a transparent framing bridge. It authenticates the
WebSocket, then forwards ACP messages in both directions without interpreting
method bodies. WebSocket messages are text frames; binary frames are rejected
or ignored consistently with the official SDK client.

The PawFlow listener is not ASGI: its `ws_handler` receives a raw synchronous
socket. The server therefore must not call
`acp.ws.server.handle_asgi_websocket`, which is coupled to the SDK's
`AcpServer` ASGI registry. Add a small PawFlow-owned implementation of the
public `acp.Transport` protocol over that raw socket, using the same
socket-to-async-loop ownership pattern as
`CCInteractiveEventService._handle_ws`, and pass that transport to
`run_agent`. The server is the ACP agent and therefore retains access to the
client-side methods on the same connection.

This avoids a PawFlow-only intermediate RPC contract and preserves ACP
bidirectionality.

## 6. Shared outbound ACP runtime

Add a focused shared runtime under core/acp/ or equivalently small leaf modules:

- core/acp/process_session.py: SDK connection, loop/thread ownership, process
  lifecycle, request correlation, and shutdown;
- core/acp/client_adapter.py: ACP Client implementation for session updates,
  permissions, relay filesystem, relay terminals, and elicitation policy;
- core/acp/content.py: typed conversion helpers shared by generic ACP and
  Gemini only;
- core/acp/errors.py: PawFlow-facing typed failure classification;
- core/acp/session_state.py: in-memory live state and durable session metadata
  helpers.

The runtime must satisfy these invariants:

1. One reader owns the ACP transport.
2. No provider code reads stdout directly.
3. Every async operation is scheduled on the session's one event loop.
4. The LLM-facing caller receives events through a bounded thread-safe queue.
5. Queue backpressure cannot deadlock the SDK reader; terminal state has a
   reserved delivery path.
6. Process exit, protocol error, cancellation, and force stop wake every waiter.
7. close is idempotent and closes connection, streams, subprocess, relay
   terminals, internal MCP token, and loop in a defined order.
8. A stale event carries the connection generation and cannot complete a newer
   prompt.
9. A prompt has one request id/session id pair and one terminal PromptResponse.
10. PawFlow never retries a prompt after submission unless the protocol proves
    that the original request was not accepted.

The shared runtime may keep a long-lived process when the provider policy says
the process is reusable. The pool/session key is user id, conversation id,
agent name, LLM service id, credential slot identity, provider identity, and
configuration revision.

## 7. Outbound Client adapter

The SDK Client implementation handles the methods an ACP agent can call.

### 7.1 session/update

Map typed updates without inspecting raw dictionaries:

| ACP update | PawFlow signal |
|---|---|
| AgentMessageChunk | token callback and final accumulator |
| AgentThoughtChunk | thinking callback/accumulator |
| ToolCallStart | block_callback tool_use |
| ToolCallProgress or ToolCallUpdate | progress/tool_result update |
| AgentPlanUpdate and plan content/removal | plan/progress UI event |
| UsageUpdate | native observed context and token usage |
| AvailableCommandsUpdate | diagnostic capability snapshot |
| CurrentModeUpdate | session metadata |
| ConfigOptionUpdate | session metadata |
| SessionInfoUpdate | durable display metadata |
| compaction updates | context lifecycle event |
| UserMessageChunk | external/manual input observation only |

Tool events retain the ACP toolCallId plus a PawFlow connection-generation
prefix. Duplicate terminal tool updates are ignored. Tool origin is derived
from typed metadata and configured MCP server identity, not substring matching
over serialized payloads.

### 7.2 Permissions

request_permission delegates to ToolApprovalGate using the current
conversation, agent, turn, normalized tool title, and bounded safe argument
preview. The gate result is mapped only to an option actually supplied by the
ACP agent:

- allow_once selects an option whose exact ACP kind is `allow_once`;
- always_allow selects an option whose exact ACP kind is `allow_always`;
- session_allow selects `allow_once` for this request because stable ACP has
  no session-scoped permission kind; any reusable session grant remains
  PawFlow-owned state in ToolApprovalGate and is never widened to
  `allow_always`;
- denied, cancelled, or timeout selects the least-permissive offered
  `reject_once` or `reject_always` option appropriate to the gate decision.

If the requested semantic choice is not among the supplied options, choose the
least permissive compatible option. If none exists, return cancelled. Never
select an option by substring matching its id or title. The only stable ACP
permission kinds are `allow_once`, `allow_always`, `reject_once`, and
`reject_always`.

### 7.3 Filesystem

read_text_file and write_text_file use the conversation's selected relay and
the same path normalization, ACL, size limits, and audit path as the read/write
tools. line and limit preserve ACP semantics. A missing relay, path escape,
unsupported encoding, oversize content, or write denied by permission mode
returns a typed ACP request error.

### 7.4 Terminals

create_terminal, terminal_output, wait_for_terminal_exit, kill_terminal, and
release_terminal reuse the existing relay terminal owner. Terminal ids are
opaque, scoped to connection plus session, and unusable across users or
sessions. output_byte_limit is enforced before buffering. Connection close
kills or releases remaining terminals according to the existing terminal
policy and never leaks a process.

### 7.5 Elicitation

Stable elicitation is enabled only when PawFlow has an explicit UI/client
mapping for the advertised mode. Unsupported modes return decline, never a
synthetic accepted result. Elicitation state is scoped to the current ACP
session and removed on completion, cancellation, or disconnect.

## 8. Generic outbound provider: acp

### 8.1 Service contract

Add provider value acp as a normal runtime_kind=llm provider.

Required configuration:

- acp_command: one executable path or executable name;
- acp_args: JSON array of argv strings;
- acp_cwd: explicit working-directory policy or path;
- max_context_size: required when the ACP agent cannot report a limit.

Optional configuration:

- acp_env: JSON object of explicit environment entries with normal PawFlow
  expression/secret resolution;
- acp_auth_method_id: exact method id to use after initialize;
- acp_reuse_process: boolean, default true;
- acp_load_session: boolean, default true but effective only when advertised;
- acp_additional_directories: explicit list;
- acp_mcp_mode: none or pawflow, default pawflow;
- acp_use_client_io: boolean, default true;
- acp_title_override for display only.

There is no default acp_command. Missing required configuration is ValueError.
The command is executed as argv without a shell. Environment names are
validated; secret values are never logged or returned by diagnostics.

Generic acp defaults auth mode to none. It does not reuse Gemini, Claude, or
Codex credential families. If initialize advertises authentication methods:

- use the exact configured acp_auth_method_id;
- if exactly one method exists and policy explicitly permits automatic
  single-method selection, it may be selected;
- otherwise fail with a message listing method ids but no credential values.

### 8.2 Session lifecycle

For a cold live process:

1. launch the configured command;
2. initialize and validate protocol/capabilities;
3. authenticate when required;
4. build scoped PawFlow MCP definitions when enabled;
5. call session/new with cwd, additional directories, and MCP servers;
6. persist the returned session id only after the request succeeds;
7. send the prompt and stream typed updates;
8. retain the process only after a successful terminal and clean state.

For a restarted process, call session/load only when advertised and enabled.
If load reports a typed unknown-session result, clear that exact stored id and
perform one session/new without replaying the prompt. Other load errors surface.

resume_session and fork_session are used only when the remote agent advertises
them and PawFlow has a matching user action. They are not substitutes for
session/load.

### 8.3 Content

Map PawFlow messages to ACP content blocks:

- current text to TextContentBlock;
- current image attachments to ImageContentBlock;
- current audio attachments to AudioContentBlock when advertised;
- URL/file references to ResourceContentBlock when the client can resolve the
  URI, otherwise materialize bounded content through FileStore;
- no historical binary is resent on a warm session;
- cold context is serialized once through the existing CLI context policy;
- reuse sends only the current delta.

Unknown or unsupported content types fail before prompt submission.

### 8.4 Capabilities and telemetry

The generic provider reports only observed data:

- model from configured/display session information;
- usage from UsageUpdate or PromptResponse metadata;
- context occupancy only from provider-native usage;
- unavailable usage remains unavailable, not zero presented as measured;
- thinking only from AgentThoughtChunk;
- plans/modes/config options only when received;
- live-preempt only if cancel followed by a new prompt is proven safe by the
  remote agent and the provider capability is enabled.

## 9. Specialized Gemini ACP provider

gemini remains the public provider value and its behavior remains compatible.

The migration separates transport from policy:

- shared AcpProcessSession replaces JSON-RPC send/read/request loops;
- Gemini policy still launches gemini --acp through GeminiPool;
- Gemini policy still writes settings.json and credential files;
- GeminiSessionMixin remains OAuth authority;
- GeminiLiveRegistry remains warm-process authority;
- Gemini-specific model effort, thinking budget, capacity errors, history
  repair, usage metadata, and image conversion remain;
- PawFlow MCP server definitions are produced by the shared scoped builder;
- built-in Gemini tool exclusion remains a Gemini policy;
- cancellation and preemption use typed SDK calls.

Delete core/llm_providers/_gemini_acp.py only after every remaining helper has
either moved to a genuinely shared ACP module or a Gemini-specific module. Do
not leave a compatibility wrapper around the hand-written wire implementation.

The migration must preserve:

- cold/reuse/stale-session behavior;
- pool slot and OAuth token recovery;
- no duplicate prompt on retry;
- native image input;
- tool display and background mapping;
- context gauge semantics;
- capacity cooldown classification;
- force-stop and next-turn recovery.

Gemini live preemption needs an explicit SDK-era correlation design. The
current implementation correlates a raw prompt request id after sending
`session/cancel`; the SDK intentionally hides that JSON-RPC id. The migrated
provider must instead keep a distinct asyncio task for every `prompt` call,
serialize cancellation and successor admission on the owning event loop, and
prove which cancelled response belongs to the predecessor before accepting
updates from the successor. A deterministic test must cover both response
orders and demonstrate that a late cancelled completion cannot finish or
poison the new prompt. Until that test passes, Gemini cannot advertise live
preemption through the migrated SDK path.

## 10. Inbound ACP publication

### 10.1 Publication configuration

Extend the existing publication record/config with:

- acp_enabled, default false;
- acp_permission_mode, required when enabled: read_only or default;
- acp_session_ttl_seconds, positive bounded value;
- acp_max_sessions_per_key, positive bounded value;
- acp_disconnect_policy: cancel or finish_detached;
- acp_client_io_enabled, default true;
- acp_fork_enabled, default false until the checkpoint implementation passes;
- acp_additional_directory_policy: deny or publication_allowlist.

A material publication, agent, tool exposure, permission, or ACP configuration
change increments the existing publication generation. New ACP connections use
the new generation; old sessions cannot accept new prompts after rotation.

### 10.2 Authentication and WebSocket endpoint

Add an authenticated endpoint with a stable URL under the published-agent
surface, for example:

~~~text
wss://HOST/acp/{publication_id}
~~~

Authentication accepts the existing publication key in the Authorization
header and the existing private gateway header when configured. Credentials
never appear in URL query strings. The endpoint authenticates before accepting
ACP data and binds publication id, key id, owner user id, generation, source
conversation, and target agent into immutable connection context.

Rate, connection, session, input-size, and message-size limits are applied
before allocating an AgentRuntimeAPI turn. Unknown/disabled publications,
revoked keys, wrong gateway keys, and cross-publication keys fail uniformly
without revealing which identifier was valid.

### 10.3 Local transparent proxy

Add a console entry point pawflow-acp. It:

1. reads PAWFLOW_ACP_SERVER_URL, PAWFLOW_ACP_PUBLICATION_ID,
   PAWFLOW_ACP_API_KEY, and optional PAWFLOW_GATEWAY_KEY;
2. opens the authenticated WebSocket;
3. switches stdin/stdout to binary-safe ACP framing;
4. forwards each complete ACP message in both directions;
5. sends logs only to stderr;
6. propagates EOF/close and exits nonzero on authentication or protocol
   transport failure;
7. never parses method payloads, stores prompts, or writes credentials.

Command-line flags may provide non-secret URL/publication settings. Secret
values are environment-only. The installer/UI snippet uses placeholders and
never persists or redisplays the raw key.

### 10.4 Server Agent implementation

PawFlowAcpAgent implements the official Agent protocol.

initialize returns:

- protocol version compatibility;
- PawFlow implementation name/version;
- loadSession capability;
- session listing/resume/close only when implemented;
- forkSession only when enabled and implemented;
- prompt capabilities matching accepted content blocks;
- no authentication method because transport authentication already occurred;
- modes/config options only when backed by real owner-configured behavior.

on_connect retains the typed Client handle for the lifetime of the WebSocket.

## 11. Inbound durable session model

Do not add an independent `acp_sessions` lease machine. An ACP session is a
named published-agent thread backed by the existing A2AStore authorities:

- `ensure_named_context` provides the publication/key-scoped isolated
  conversation for the opaque ACP session id;
- the existing AG-UI thread machine owns generation, expiry, rotation, and
  one-writer state;
- the existing acquire/journal/batch machinery owns prompt idempotency,
  replay, fencing, terminal state, and late-result rejection;
- the standard API session/quota machinery owns per-key retained-session and
  concurrent-run limits;
- `force_stop_managed_run` remains the exact-run cancellation authority.

Only minimal ACP transport metadata that has no existing home may be added to
those shared rows or a narrow metadata table: normalized cwd/additional
directories, parent session id, connection id, and the advertised capability
snapshot. It must not duplicate state, busy leases, generations, quotas, or
terminal journals.

Required invariants:

1. A session belongs to one publication key namespace.
2. A key cannot load another key's session.
3. A publication rotation prevents new work on the old generation.
4. One session has at most one active prompt.
5. Duplicate prompt request ids attach/replay; they do not run twice.
6. Cancel targets the exact active run handle.
7. close is idempotent and prevents later prompt.
8. expiry is materialized transactionally before reuse.
9. publication/key deletion cascades or fail-closes without orphan authority.
10. session counts are enforced transactionally per key.

new_session creates an opaque id and materializes it through the shared named
context/thread/session authorities. load_session validates cwd policy and
resolves that same thread generation. list_sessions is key-scoped and
cursor-paginated through the shared session store. resume_session reactivates a
non-busy valid generation. close_session cancels/detaches through the managed
run authority and rotates or closes the shared thread idempotently.

ACP publication settings extend the existing validated publication runtime
configuration path. `normalize_standard_api_update` (or a deliberately
renamed shared successor) validates the complete ACP fieldset, while
`standard_api_material_changed` advances the same publication runtime
generation. ACP is still a distinct wire protocol, not an OpenAI dialect.

fork_session uses the existing exact checkpoint/fork machinery. The new session
must point at a new isolated conversation whose visible and hidden state comes
from the verified checkpoint. If exact state cannot be proven, return an ACP
request error; never degrade a requested fork into a shared or reconstructed
session.

## 12. Inbound prompt mapping

Accepted blocks are converted before ingress:

| ACP content | PawFlow ingress |
|---|---|
| text | user content in original order |
| image | FileStore-backed image_ref attachment |
| audio | FileStore-backed attachment when the selected agent/provider supports it |
| resource with text | bounded text plus source metadata |
| embedded resource | bounded typed attachment/text |
| unsupported binary/resource | typed invalid-request error before submission |

All stored messages receive UUIDs and timestamps through the existing ingress
contract. The ACP session id, publication id, key id, connection id, and target
agent are server-owned source attributes. Client metadata cannot overwrite
reserved identity/run fields.

A prompt containing only unsupported blocks is rejected. Size and MIME limits
are applied before FileStore writes. Temporary upload state is cleaned after a
failed admission.

The prompt method submits in asyncio.to_thread so the ACP event loop remains
available for bidirectional client calls. It waits without an implicit
functional timeout, following AgentRuntimeAPI's live-turn rule.

## 13. Inbound event mapping

The live callback translates ConversationEventBus events to typed
session/update notifications:

| PawFlow event | ACP update |
|---|---|
| token | AgentMessageChunk |
| thinking_delta/thinking_content | AgentThoughtChunk |
| tool_call | ToolCallStart |
| tool_progress | ToolCallProgress |
| tool_result | ToolCallUpdate terminal |
| agent plan events | AgentPlanUpdate/content/removal |
| usage/context update | UsageUpdate |
| status/current agent | SessionInfoUpdate or CurrentModeUpdate |
| done | no duplicate chunk; completes PromptResponse |
| error_event | typed request failure or terminal stop mapping |

Every update uses the exact ACP session id. Stale events are rejected by turn
id plus run handle plus session lease generation. Final text is not sent twice:
streamed chunks are updates; PromptResponse carries only the stop reason and
metadata required by the schema.

Stop mapping is explicit:

- normal done -> end_turn;
- user cancel/force stop -> cancelled;
- model limit -> max_tokens when provable;
- refusal -> refusal when the SDK schema supports it;
- runtime/protocol errors -> RequestError, not a successful end_turn;
- client disconnect -> cancel or finish_detached according to publication
  policy.

## 14. Inbound client capabilities as request-scoped tools

The connected ACP Client can provide filesystem and terminal operations. Expose
them to the selected PawFlow agent as ephemeral request-scoped handlers, not as
global tools and not through the two-request ClientToolHandler pending protocol.

Handlers include:

- acp_read_text_file;
- acp_write_text_file;
- acp_create_terminal;
- acp_terminal_output;
- acp_wait_for_terminal_exit;
- acp_kill_terminal;
- acp_release_terminal.

Generalize `register_client_tools`, the existing turn-local registration seam,
to accept an explicitly supplied handler class/factory. Keep
`ClientToolHandler` and `partition_client_tool_calls` unchanged for standard
API deferred calls; add an executable ACP handler whose `execute` method
schedules the typed client coroutine with `asyncio.run_coroutine_threadsafe`.
Because the tool registry is already an execution-local fork, the handlers
disappear atomically with the turn.

Each handler captures only an opaque broker id. The broker registry binds that
id to connection, ACP session, turn id, event loop, client capabilities, and
lease generation. Execution from the synchronous AgentLoop thread schedules
the typed SDK client coroutine with asyncio.run_coroutine_threadsafe and waits
under the existing tool cancellation contract.

The handlers are registered only when:

- acp_client_io_enabled is true;
- the client advertised the corresponding capability;
- publication tool policy allows the operation;
- the session and connection leases are current.

They are removed after the turn. Terminal handles may survive within the ACP
session only through the broker's scoped terminal table and are cleaned on
close/disconnect.

Add the `acp_*` names to the canonical tool alias/policy tables. Client file
reads inherit the read exemption; writes and terminal effects retain the normal
approval requirements. This is a generalization of `core/client_tools.py`,
not a parallel global registry: the original handler intentionally pauses a
standard API turn and returns calls to a later HTTP request, whereas the ACP
handler executes over the live bidirectional connection.

## 15. Inbound approval bridge

Add a request-scoped approval broker hook to ToolApprovalGate.

The gate remains responsible for classification, read-only denial, reusable
grants, always-ask tools, audit, and persistence. Only the presentation and
exact response channel changes when an active ACP broker owns the turn. Broker
resolution must happen before the current no-SSE-subscriber fail-closed branch,
so a connected ACP client can answer even when no PawFlow browser is watching.

For an ask decision:

1. construct typed PermissionOption values corresponding to deny, allow once,
   allow for this ACP session, and always allow when policy permits;
2. register the normal gate request and call Client.request_permission on the
   ACP event loop;
3. validate that the returned option id was offered;
4. settle the existing request through `ToolApprovalGate.resolve_request`;
5. map it to the existing gate result and persist reusable grants only through
   ToolApprovalGate;
6. fail closed on disconnect, timeout, malformed response, stale turn, or
   unsupported client capability.

`session/cancel` must set the gate's `cancel_event` so a prompt never waits
for the normal 60-second approval timeout after cancellation.

The browser UI and PawCode approval paths remain unchanged when no ACP broker
owns the turn. A response from one ACP session cannot settle another session's
request.

## 16. Cancellation, preemption, and concurrency

### 16.1 Outbound provider

session/cancel targets the active SDK session. Force stop additionally kills the
owned process/container and invalidates the live registry entry. A cancelled
PromptResponse is not treated as an error and cannot affect the next loop.

Generic acp advertises live preemption only after a conformance fixture proves
the remote agent accepts cancel plus a successor prompt without cross-delivery.
Gemini preserves its current user-visible behavior only after the SDK-task
correlation and both-response-order tests in section 9 pass.

### 16.2 Inbound publication

cancel resolves the ACP session's exact active turn/run handle and calls the
existing AgentLoop cancellation path. It is idempotent. A late done after cancel
cannot overwrite cancelled state.

Concurrent prompt calls for one session return busy. Different sessions may run
concurrently within existing user/runtime limits. Reconnect/load does not steal
a busy lease. finish_detached continues the server turn but suppresses client
method calls after disconnect and makes any required such call fail closed.

## 17. Security and privacy

- Authenticate before processing protocol data.
- Use shared publication keys and existing constant-time hash verification.
- Never put secrets in argv, URLs, logs, session rows, diagnostics, or snippets.
- Validate executable argv and environment without shell expansion.
- Bind all session, tool, terminal, approval, and event operations to user,
  publication, key, connection, turn, and generation.
- Enforce relay path policy and publication additional-directory policy.
- Cap ACP message, content block, attachment, update, terminal output, and
  diagnostic sizes.
- Redact prompts, tool arguments, environment values, and file content from
  routine logs.
- Reject unknown fields according to the official generated schema.
- Do not allow ACP client metadata to set http.auth.principal or other reserved
  AgentRuntimeAPI attributes.
- Revocation closes matching live WebSockets and prevents new work even if a
  connection object remains reachable.
- Dependency and wheel license metadata must include the ACP SDK and its
  transitive runtime dependencies.

## 18. Provider and product wiring

### 18.1 Generic provider registry

Update the exact provider gates after the shared runtime exists:

- core/llm_client.py provider list and preempt dispatch;
- core/_llm_client_driver.py complete/stream/abort dispatch;
- core/_llm_types.py no-retry/live-session classification;
- core/llm_auth_modes.py;
- core/llm_failure_classifier.py;
- core/llm_providers/__init__.py;
- core/llm_providers/context_observation.py;
- services/llm_connection.py schema and validation;
- config/default_models.json only if an honest generic default exists; normally
  no generic ACP model default;
- agent-loop callback/capability gates under tasks/ai;
- service form and provider labels;
- usage/status/active-agent paths;
- tests/test_gauge_invariants.py source-text assertions.

Prefer one AcpProviderCapabilities data object over repeating provider-name
conditionals. Do not refactor unrelated provider registries.

### 18.2 Inbound publication/UI

Extend:

- core/a2a_store.py schema/config/session operations;
- core/standard_api_config.py only for shared publication-generation helpers,
  not to pretend ACP is an OpenAI dialect;
- server WebSocket route registration;
- tasks/ai/actions/_agentres_k7.py and action authorization;
- tasks/io/chat_ui/resources_a2a.js or its focused publication modules;
- English, French, and Spanish UI strings;
- publication runtime summaries and key warnings;
- installer/build/release asset configuration for pawflow-acp where applicable.

The owner UI remains Repository > Published agents / APIs. One ACP fieldset
shows enabled state, URL, limits, capability notes, setup snippets, active
sessions, and reset action.

## 19. Code ownership and non-overlapping implementation groups

### ACP group owned by assistant

- pyproject.toml dependency and pawflow-acp entry point;
- new core/acp/* shared SDK runtime and adapters;
- core/llm_providers/acp.py;
- Gemini ACP transport migration files;
- generic provider registry/service/agent-loop wiring;
- inbound ACP agent, WebSocket endpoint, broker, proxy, publication storage/UI;
- ACP tests and ACP user/architecture documentation.

### Managed MCP provider group owned by Claude

Use the already reviewed docs/MANAGED_MCP_CLI_PROVIDERS_PLAN.md file boundary:

- managed hook extension;
- cc_mcp, codex_mcp, and probe-gated agy_mcp;
- their pool/provider/registry/service/UI/test/docs wiring.

Before parallel implementation begins, compare both file lists. Shared hot
files such as pyproject.toml, core/llm_client.py, core/_llm_client_driver.py,
core/_llm_types.py, services/llm_connection.py, config/default_models.json,
provider UI modules, documentation indexes, and gauge tests are assigned to
one owner for final integration. The other group returns a patch plan for those
files rather than editing them concurrently.

Recommended integration ownership: assistant owns shared hot files after
Claude completes isolated MCP-specific modules and tests. Claude reports exact
registry additions required; assistant applies the combined registry edit once.

Implementation may not begin in parallel while the pre-existing 13-file
`allow_refresh` patch is uncommitted. Those files are frozen until Quentin
explicitly authorizes a dedicated baseline commit:
`core/__init__.py`, `core/llm_oauth_credential.py`,
`core/llm_providers/claude_code_session.py`,
`core/llm_providers/codex_session.py`,
`core/llm_providers/gemini_session.py`,
`services/llm_credential_oauth.py`, `tasks/ai/actions/_sf_k1.py`,
`tasks/ai/actions/_sf_k2.py`,
`tasks/io/chat_ui/resources_service_login.js`,
`tasks/io/chat_ui/schema_form.js`, `docs/llm_providers.md`,
`docs/02_REFERENCE_TASKS_SERVICES.md`, and
`tests/test_llm_credential_oauth.py`.

Claude owns the managed-hook, managed-pool, provider-specific turn, terminal
UI, and managed-MCP test files enumerated by the companion plan. Assistant owns
`core/acp/**`, ACP/Gemini transport files, published-agent storage/runtime,
approval/client-handler integration, the ACP proxy/UI/docs/tests, and all
shared provider registry/dispatch/service/config/gauge files. Claude supplies
patch blocks for shared hot files; assistant applies one combined integration
edit after the isolated implementations are reviewed.

## 20. Work packages

### WP0 — Pin and prove the SDK contract

Deliver:

- exact SDK pin without the experimental `http` extra and a separate explicit
  `websockets` dependency for the proxy;
- official-model fixtures for initialize, session/new/load/resume/fork/close,
  prompt, updates, client methods, permissions, elicitation, and cancellation;
- in-memory SDK agent/client round trip;
- raw-listener `Transport` adapter plus a WebSocket/proxy round trip driven by
  the official SDK `create_websocket_stream` client;
- failure proof for incompatible protocol versions.

Exit gate: both ACP sides exchange typed messages through the SDK with no
PawFlow JSON-RPC parser.

### WP1 — Shared outbound session runtime

Deliver process lifecycle, async loop bridge, bounded event channel,
generation fencing, close semantics, and typed Client adapter.

Exit gate: a fake ACP agent streams text/thinking/tools, asks permission,
performs relay I/O, returns usage, cancels, dies, and restarts deterministically.

### WP2 — Generic acp provider

Deliver config validation, launch, session persistence, content mapping, MCP
definitions, callbacks, cancellation, failure classification, and service/UI
registration.

Exit gate: cold, warm, load, stale-load, image, tool, cancel, force-stop, and
invalid-config tests pass with a fake command.

### WP3 — Migrate Gemini to the SDK runtime

Deliver Gemini policy adapter, shared transport adoption, removal of raw
framing, preserved pool/live/OAuth behavior, and explicit predecessor/successor
asyncio-task correlation for cancel-plus-preempt.

Exit gate: current Gemini ACP suite plus new SDK conformance tests pass and no
provider code constructs jsonrpc envelopes.

### WP4 — Inbound connection and publication persistence

Deliver acp_enabled configuration through the shared publication validator,
ACP session projection onto the existing named-context/thread/admission/session
machine, authenticated raw-socket WebSocket `Transport`, generation/revocation
fencing, and the local proxy.

Exit gate: an official SDK client launched through pawflow-acp initializes,
creates/loads/lists/closes a session, and cannot cross key/publication bounds.

### WP5 — Inbound prompt and event bridge

Deliver content mapping, AgentRuntimeAPI submission, live update translation,
terminal stop mapping, duplicate/busy handling, cancel, and disconnect policy.

Exit gate: text, image, tool, thinking, usage, error, cancel, disconnect, and
reconnect scenarios produce one durable turn and protocol-correct updates.

### WP6 — Client I/O and approval bridge

Deliver generalized request-scoped handler registration, executable ACP client
handlers, broker registry, terminal cleanup, and ToolApprovalGate responder
integration before the no-SSE-subscriber branch.

Exit gate: filesystem, terminal, approval once/session/always/deny, stale
response, disconnect, and cross-session attacks are covered.

### WP7 — Optional resume/fork and operability

Deliver resume/list/close fully; deliver fork only with verified exact
checkpoint semantics. Add runtime summaries, session reset, diagnostics, UI,
and setup snippets.

Exit gate: advertised capabilities exactly match behavior and every lifecycle
action is owner-authorized.

### WP8 — Documentation and rollout

Deliver docs/acp_integration.md, provider reference, publication guide,
troubleshooting, security notes, SDK-upgrade procedure, and release packaging
notes.

Exit gate: code, schema, UI, CLI help, and docs use the same names and claims.

## 21. Test plan

### 21.1 Official SDK conformance

- generated model round trips;
- initialize negotiation and incompatible version;
- stdio framing through SDK helpers;
- WebSocket message transport through SDK helpers;
- official `create_websocket_stream` client against the PawFlow raw-socket
  `Transport` server adapter;
- unknown method/request error behavior;
- malformed/oversize message handling;
- EOF and simultaneous close;
- stable protocol only.

### 21.2 Generic provider

- missing command/invalid argv/env/cwd;
- no shell execution;
- auth none, exact method, ambiguous methods;
- cold new and warm prompt;
- process restart plus load;
- stale load to one new session;
- load unsupported to cold;
- MCP server scoping;
- content block matrix;
- update matrix;
- permission choices;
- filesystem and terminal methods;
- cancel/preempt/force stop;
- process death and queue saturation;
- subprocess pipe shutdown performed only by the owning event loop;
- duplicate/out-of-order updates;
- native/unavailable usage;
- zero prompt replay after accepted failure.

### 21.3 Gemini regression

Run all current Gemini, Gemini pool, live registry, OAuth, context gauge,
attachments, active agent, cancellation, force-stop, and provider dispatch
tests. Add negative source tests proving no raw JSON-RPC envelope helpers remain
in Gemini provider modules.

Explicitly replace or update the frozen source assertions in
`tests/test_provider_dispatch_signatures.py` for the old
`_gemini_acp_*` wire helpers, session-persistence location, and permission
heuristic. Preserve the `gemini_acp_session_version` session migration
invariant and keep the provider lists compared by
`test_the_block_gate_and_the_turn_gate_list_the_same_providers` identical.
Add the SDK-task Gemini live-preemption response-order regression.

### 21.4 Inbound storage/auth

- migration and restart;
- key namespace isolation;
- publication generation rotation;
- revoke/delete while connected;
- transactional quota under concurrency;
- one busy prompt per session;
- duplicate attach/replay;
- close/expiry/cascade;
- session cursor/list scoping;
- exact fork or explicit unsupported response.

### 21.5 Inbound streaming

Use the official SDK client against the real PawFlow Agent implementation:

- initialize capabilities;
- new/load/resume/list/close;
- text and multimodal prompt;
- token/thinking/tool/plan/usage updates;
- final end_turn;
- runtime error;
- cancel and late done;
- disconnect cancel;
- finish_detached;
- reconnect;
- no duplicate assistant text;
- every persisted message has UUID and timestamp.

### 21.6 Client I/O and approvals

- each client filesystem method;
- each terminal method and cleanup;
- absent client capability;
- permission allow once/session/always/deny;
- invalid returned option id;
- approval timeout/disconnect;
- approval through a connected ACP client with no browser SSE subscriber;
- stale broker/turn/lease;
- cross-session response;
- read_only enforcement;
- always-ask enforcement.

### 21.7 Regression and packaging

Run targeted suites for AgentRuntimeAPI, ConversationEventBus,
ToolApprovalGate, A2A, AG-UI, standard APIs, published MCP, LLM providers,
service schemas, OAuth, UI resources, gauge invariants, CLI installers, and
wheel/sdist entry-point smoke tests. Run compile, Ruff CI selectors, Bandit on
new Python modules, node syntax checks, JSON validation, and git diff --check.

## 22. Rollout

1. Land the SDK dependency and conformance harness.
2. Land shared outbound runtime behind no registered provider.
3. Register generic acp behind explicit configuration.
4. Migrate gemini and remove hand-written wire code only after parity gates.
5. Land inbound storage and authenticated WebSocket endpoint disabled by
   default.
6. Ship pawflow-acp and run local IDE smoke tests.
7. Enable inbound prompt streaming.
8. Enable client I/O and approvals after adversarial tests.
9. Enable optional fork only after exact checkpoint tests.
10. Keep transport enablement explicit per publication; never silently expose
    existing publications.

There is no automatic migration from a provider to acp and no automatic ACP
publication enablement.

## 23. Acceptance criteria

The ACP work is complete only when:

- the runtime pins and uses agent-client-protocol 0.12.1 without its
  experimental `http` extra and declares the proxy's `websockets`
  dependency explicitly;
- no PawFlow ACP path maintains its own JSON-RPC framing or generated schema;
- acp is a configurable generic LLM provider with explicit command policy;
- gemini remains specialized and preserves its current production behavior;
- both outbound providers use the shared typed session runtime;
- published PawFlow agents can be launched by an ACP IDE through pawflow-acp;
- inbound ACP uses existing publications and shared one-time keys;
- session identity, isolation, generation, quota, and cancellation are durable;
- prompts enter only through AgentRuntimeAPI;
- live output maps to typed ACP updates exactly once;
- client filesystem, terminal, and approval calls remain bidirectional;
- all I/O is relay-routed or explicitly client-routed;
- force stop is immediate, non-error terminal behavior and does not poison the
  next loop;
- optional capabilities are advertised only when fully implemented;
- revocation and publication deletion stop future authority;
- docs, UI, CLI help, runtime summaries, and code agree;
- targeted, adversarial, regression, lint, security, and packaging gates pass.
  The packaging gate includes a PyInstaller smoke test that launches
  `pawflow-acp` and proves its explicit `websockets` dependency is bundled.

## 24. Source-of-truth references

Live PawFlow sources to validate during implementation:

- core/agent_runtime_api.py
- core/conversation_event_bus.py
- core/tool_approval.py
- core/client_tools.py
- core/a2a_store.py
- core/a2a_runtime.py
- core/agui_runtime.py
- core/standard_api_runtime.py
- core/llm_client.py
- core/_llm_client_driver.py
- core/_llm_types.py
- core/llm_auth_modes.py
- core/llm_failure_classifier.py
- core/llm_providers/gemini.py
- core/llm_providers/_gemini_acp.py
- core/llm_providers/_gemini_stream.py
- core/llm_providers/gemini_session.py
- core/gemini_pool.py
- core/gemini_live_registry.py
- services/llm_connection.py
- tasks/ai/actions/_agentres_k7.py
- tasks/ai/actions/tools_exec.py
- tasks/io/chat_ui publication and provider modules
- pawflow_cli/api.py
- pyproject.toml
- tests/test_agent_runtime_api.py
- tests/test_tool_approval.py and approval security suites
- A2A, AG-UI, standard API, Gemini, provider, UI, and gauge test suites

Official sources:

- https://agentclientprotocol.com/
- https://github.com/agentclientprotocol/python-sdk
- https://agentclientprotocol.github.io/python-sdk/
- the pinned SDK's generated acp.schema and public Agent/Client interfaces

The review criterion is architectural as well as behavioral: ACP must be a
typed interoperability layer around PawFlow's existing authorities, not a
parallel agent runtime, key store, tool engine, or hand-written protocol stack.

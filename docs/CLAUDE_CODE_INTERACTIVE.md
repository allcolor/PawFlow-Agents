# Claude Code Interactive Provider

`claude-code-interactive` is a provider that drives Claude Code in
interactive tmux mode while reading model output from a transparent local MITM
proxy. The provider does not read Claude Code transcripts or terminal output.

## Runtime Shape

- PawFlow starts one persistent Docker container per user, conversation, agent,
  and LLM service.
- The container maps the configured Anthropic-compatible host to
  `127.0.0.1` (`api.anthropic.com` by default, or the `llmConnection.base_url`
  host in API-key mode).
- A root-owned local TLS proxy listens on the configured provider port (`443` by
  default). Claude Code always connects to this proxy over HTTPS so the MITM
  observer can keep seeing provider SSE events. The proxy then forwards to the
  real upstream with the original `base_url` scheme, so `https://...` remains TLS
  upstream and `http://...` becomes clear HTTP upstream after the local MITM.
- Claude Code runs as user `pawflow` (uid 1000) inside tmux.
- The session workdir is provisioned host-side by the server under the
  server's own uid with 755 dirs. The tmux launcher therefore pre-creates
  and chowns the CLI-owned state subtrees (`tasks/` for the CLI task store,
  `projects/` for transcripts and the memory directory) to uid 1000 before
  dropping privileges — without this the CLI cannot write its own state
  (TaskCreate fails with ENOENT/EACCES on the task-list lock). Everything
  else in the workdir stays server-owned; the server never writes inside
  those two subtrees.
- The Docker container sees the host session root at `/cc_sessions_host`. Each
  tmux launch creates a private mount namespace and bind-mounts
  `/cc_sessions_host/<user>` over `/cc_sessions`, keeping Claude Code paths stable
  while avoiding a global container bind over `/cc_sessions`.
- Runtime support files under `/opt/pawflow` are copied into the container once at
  spawn time instead of mounted read-only, so host-side development edits require
  a new interactive container before they are visible inside the session.
- MCP tools still go through the existing PawFlow MCP bridge.
- A live tmux/container receives appended turns. If that live instance is gone
  after idle reaping or restart, PawFlow starts a fresh Claude Code interactive
  session and injects the PawFlow initial context file again. Interactive mode
  never starts Claude Code with `--resume <session_id>`.

## TLS Material

PawFlow creates a local CA once under `data/system` and generates a per-session
leaf certificate for `api.anthropic.com`. Only the public CA certificate and the
leaf certificate/key are mounted into the session container. The CA private key
is never mounted.

## Event Flow

The proxy forwards each socket chunk exactly as received in both directions. It
does not rewrite request headers, response headers, bodies, transfer encoding, or
chunk boundaries. Observation is side-channel only: while streaming the upstream
response back to Claude Code, the proxy parses a copy of Anthropic SSE bytes or
non-stream JSON message responses and sends scrubbed events to PawFlow over
`/ws/cc-interactive/events/<service_id>`.

The turn coordinator reads the resolved model from the `message_start` SSE event
(`message.model`). Because the proxy observes the real `/v1/messages` traffic,
this is the model Anthropic actually served for the configured alias (for
example `best` resolves to the latest Opus or Fable). It is exposed as
`LLMResponse.model` (and `raw["effective_model"]`) so conversation metadata
shows the effective model name rather than the alias; when no `message_start` is
observed the coordinator falls back to the configured alias and then the
provider default. The `message_start` usage block also seeds `input_tokens`,
which `message_delta` later completes with `output_tokens`. When a turn issues
several `/v1/messages` requests, the last observed model wins.

For transport debugging, the proxy can emit `wire` events for raw socket chunks
received and sent on both directions (`client_to_upstream` and
`upstream_to_client`). This dump is disabled by default because Claude Code also
sends large telemetry batches on the same keep-alive sockets. Set
`PAWFLOW_CCI_PROXY_WIRE_LOG=1` to enable it. When enabled, the dump is still
limited to model endpoints (`/v1/messages` and `/v1/complete`) unless
`PAWFLOW_CCI_PROXY_WIRE_LOG_ALL=1` or `PAWFLOW_CCI_PROXY_WIRE_LOG_PATHS` expands
the allow-list. The event service logs wire payloads at DEBUG with sanitized
base64, size, SHA-256, and UTF-8 `repr`; they are not queued for the provider.
Sensitive HTTP headers such as `Authorization`, `Cookie`, `Set-Cookie`, and
API-key headers are redacted in the proxy and redacted again by the server before
logging.

The proxy parses HTTP keep-alive traffic as a sequence of request/response
exchanges on the same TLS socket. Each exchange receives its own request id so a
Claude Code startup probe cannot be confused with the real model turn. The known
quota probe (`/v1/messages` with `max_tokens: 1` and user content `quota`) is
observed for diagnostics but its response body is ignored. Interactive sessions
set Claude Code's prompt-suggestion and terminal-title environment toggles off
so UI hints do not become PawFlow transcript messages. They also pass the same
thinking-related CLI flags as the stream-json provider (`--thinking-display
summarized`, plus configured `--effort`) so Claude Code emits observable
thinking blocks. On a cold interactive start, PawFlow stores the full compacted
context in `.pawflow_cci/initial_context.md` and repeats the latest turn in the
tmux prompt itself with XML-sensitive characters escaped, so Claude Code has the
immediate user request while still being instructed to read the full context
file before acting. Hook-side suppression of PawFlow-injected prompts requires the injection
marker; a manual tmux prompt that resembles the sentinel remains a user prompt.
If Anthropic compresses an
observed response (`gzip` or `deflate`), only the side-channel copy is decoded
before SSE/JSON parsing; the proxied bytes sent back to Claude Code remain
unchanged.

### Stream ownership

Exactly one consumer may read a session's event queue. `queue.Queue` hands each
event to a single getter, so two live turn coordinators on the same
`session_token` split the SSE stream between them: text deltas arrive halved
(an answer that starts mid-sentence), and a `tool_use` whose `input_json_delta`
chunks landed in the other reader emits with empty arguments — which leaves an
MCP wrapper un-unwrapped and rendered as a bare `use_tool`.

Ownership is arbitrated by epoch. `claim_consumer()` bumps the epoch and every
`wait_event()` presents the epoch it was granted; a stale holder raises
`CCIConsumerEvicted` instead of stealing events. Claim changes, queue reads,
pushback, overflow state, and wakeups share one condition. A waiter already
blocked when ownership changes is woken before it can take the replacement's
first event; if an evicted reader ever holds an event, it is pushed back ahead of
the queue so order is preserved. A `request` claim always wins because it serves
the turn the user is waiting on. A `capture` claim (the orphan-turn safety net)
refuses while a request coordinator is polling, and when evicted mid-turn it
discards only its partial local text, never a queue event.

### Captured turns and the active-agent marker

Claude Code can start a turn that PawFlow did not send: a human typing in the
tmux, or Claude Code injecting its own background-task notification. Neither
passes through `send_text`, so neither is registered as an injected prompt and
no streaming worker runs. PawFlow attaches to such a turn with a capture rather
than restarting it — a second prompt would duplicate work already in flight.

Because a capture runs outside the streaming worker, it must publish the UI's
active-agent truth itself: it registers an `_active_turns` entry and a
`thinking` event when it starts, and releases both with `active_released` when
it ends (skipping the release when a chained capture continues the same visible
activity). Without this the webchat shows the agent idle while the tmux is
visibly working.

A capture also owns the turn's *inbound* path. It registers `_active_turns`
without an `_active_contexts` entry or an `_active_claude_client`, and
`agent_streaming` reads that combination as "already active but not
preemptable": the message goes to the agent's `PendingQueue`. For a real turn
that window is brief and the turn drains the queue at its end; a captured turn
has no owner to drain it, so messages sent from the webchat used to sit there
until a force stop discarded them — while the UI showed the agent up. Two
rules close that:

- **Type into the live tmux.** `_deliver_to_captured_tmux` sends the text
  through the pool rather than queuing it. Whether there is anywhere to deliver
  is decided by `CCInteractiveEventService.live_session`, which looks for a
  *connected* proxy session. The MITM WebSocket is up exactly while a container
  lives and every observed event arrived through it, so it proves a live tmux
  independently of the turn bookkeeping that went stale. No connected session
  means no container, and the message falls back to the queue.
- **Hand the queue back on release.** When a capture releases the turn it wakes
  the agent if anything is queued, so a message that could not be typed is
  still processed instead of being discarded by the next force stop.

A capture streams like any other turn. It builds its coordinator with the same
`callback` and `block_callback` a PawFlow-driven turn passes, so text deltas
publish as `token` events while they are written and each completed block —
text, thinking, tool call, tool result — is persisted and published as it
arrives. The coordinator's returned content is unaffected by supplying
`block_callback` (only the `turn_callback` payload is suppressed), but the
capture no longer uses it: persisting per block and again at the end would
double the text.

This mirrors the Antigravity observer, whose manual ingest streams out-of-band
tmux activity by default and is *suspended* only while PawFlow drives a turn.
The rule both providers implement: everything the proxy intercepts reaches the
SSE listeners while it happens, whoever started the turn.

A capture also builds its coordinator with **the session's tool-id dedup sets**,
not fresh ones. A live Claude Code session replays its entire context on every
API request, so the proxy re-observes every prior `tool_use` and `tool_result`
block of the session on each turn. PawFlow-driven turns dedup against the
pooled container's sets (`emitted_tool_use_ids` / `emitted_tool_result_ids`);
a capture that starts with empty sets re-emits the whole history — one
transcript row and one `tool_call` event per replayed block. The webchat keys
tool blocks by `tc_id` and absorbs the repeat, so the damage surfaced first on
a channel bridge: a Telegram user received a hundred tool-call messages at once
when a background result resumed the session (2026-07-29). The sets are
resolved by `_capture_dedup_sets` from the pool via
`find_by_session_token`, falling back to a pair carried on the event session so
two chained captures still dedup against each other.

A capture evicted mid-turn by a real coordinator keeps the blocks it already
flushed — they were complete when written — and loses only the block still
being accumulated.

When the streamed tool input is incomplete but the request-body replay that
follows carries the full input, the replay supersedes the emit — but only when
the streamed name stayed an MCP wrapper. Such a call is dropped downstream by
`has_complete_mcp_tool_call`, so nothing was persisted and re-emitting cannot
duplicate it. A call whose name did resolve is left alone.

Timing controls are read once when the provider modules are imported:

- `PAWFLOW_CCI_POST_STOP_IDLE_DRAIN_SECONDS` sets how long PawFlow waits after
  Claude Code's `Stop` hook for late proxy events before closing the turn.
  Default: `2.5` seconds.
- `PAWFLOW_CCI_POST_STOP_IDLE_DRAIN_MS` is the millisecond alias for the same
  value. The seconds variable wins if both are set.
- `PAWFLOW_CCI_NO_PROXY_EVENT_TIMEOUT_SECONDS` sets how long a submitted tmux
  prompt may produce no observed proxy event before PawFlow treats the turn as
  failed. Default: `300` seconds.
- `PAWFLOW_CCI_NO_PROXY_EVENT_TIMEOUT_MS` is the millisecond alias for the same
  value. The seconds variable wins if both are set.
- `PAWFLOW_CCI_SUBMIT_DELAY_SECONDS` sets the delay between tmux paste-buffer and
  the final `Enter` key. Default: `1.0` second, which avoids submitting before
  the pasted prompt is fully present in slower terminal sessions.
- `PAWFLOW_CCI_IDLE_TTL_SECONDS` controls idle container eviction. Default:
  `1800` seconds. A service request timeout can only extend this process-wide
  TTL, never shorten an explicitly configured or already larger value.

The provider assembles responses from those events:

- `content_block_delta` text deltas stream to the UI immediately and are
  persisted as assistant messages when the corresponding content block stops.
- Provider-observed Anthropic `thinking_delta` is forwarded to clients as a
  transient PawFlow `thinking_delta` preview event for live UX. The final
  flushed thinking block is still persisted as a normal assistant message and
  published post-write as `thinking_content`.
- `signature_delta` inside a thinking block produces a redacted "Thought for"
  placeholder when Anthropic exposes only a signed thinking block.
- `tool_use` blocks and `input_json_delta` are emitted as live observed tool
  events for display/persistence only. PawFlow never re-executes them; Claude
  Code already ran those tools inside its own session.
- **No tool call is filtered.** Every observed call is emitted and persisted,
  native ones included — `GetSchema`, `ToolSearch`, and Claude Code's `Read` of
  `.pawflow_cci/initial_context.md` among them. These were once suppressed as
  bootstrap noise; the cost was worse than the noise. A turn that opened by
  reading its own context showed an empty technical-details block, and nothing
  distinguished a deliberately hidden call from a lost one — which is exactly
  the question the transcript exists to answer. What the agent did is what the
  transcript shows.
- Outgoing `/v1/messages` request bodies are observed for both assistant
  `tool_use` blocks and user `tool_result` blocks. This preserves live ordering
  even when a response-side tool block is delayed or missed; provider events are
  deduplicated by tool id. Tool results keep the real result content; only
  diagnostic wire dumps are scrubbed/redacted.
- `message_delta.usage` updates token usage.
- Claude Code command hooks publish `Stop`, `StopFailure`, `PreCompact`,
  `PostCompact`, `SessionEnd`, and `UserPromptSubmit` lifecycle events over the
  same WebSocket.
- Only the Claude Code `Stop` hook closes a PawFlow turn. Anthropic
  `message_stop` events are observed for diagnostics but do not terminate the
  interactive turn, because Claude Code can issue intermediate model requests
  before the tmux turn is complete. Response content still comes only from
  MITM-observed response events.

## Prompt Input

When PawFlow starts a new interactive Claude Code container, the provider writes
the serialized PawFlow system/context/history into
`.pawflow_cci/initial_context.md` inside the session workdir. The first pasted
prompt references that file with `@/cc_sessions/.../.pawflow_cci/initial_context.md`
and instructs Claude Code to read it before answering. Existing live sessions do
not receive the full context or tool instructions again; PawFlow sends only the
latest turn delta, any current attachment references, and a narrow catch-up block
containing new messages from other participants since the agent's last response.

Live interrupt pastes the interrupt message, then sends `Escape`, then `Enter`
as separate tmux key events. If the interrupt carries image attachments, PawFlow
materializes them into `.pawflow_vision/` and includes `@/cc_sessions/...` file
references in the pasted message. Force stop sends `Escape Escape` to the tmux
session and leaves the container lifecycle intact.

If a user attaches to the provider-owned tmux and submits a prompt manually,
Claude Code's `UserPromptSubmit` hook sends that prompt to PawFlow. PawFlow
persists it as a normal user message with `channel="tmux"` and starts a passive
MITM capture for the resulting Claude Code turn, so the assistant response also
lands in the conversation context. Prompts pasted by PawFlow itself are recorded
by SHA-256 in `.pawflow_cci/injected_prompts.jsonl`; the hook consumes that
marker and does not mirror those managed prompts back into the transcript.

The chat UI tmux action lists live Claude Code interactive sessions for the
current conversation. It opens directly when only one tmux exists and shows a
chooser when several agents have live interactive sessions.

OAuth credentials use the same session-local `.credentials.json` path as the
regular `claude-code` provider. The interactive tmux launcher mirrors the
regular provider's private mount namespace from a host-root mount at
`/cc_sessions_host`: it bind-mounts `/cc_sessions_host/<user>` over
`/cc_sessions`, then starts Claude Code with `HOME` and `CLAUDE_CONFIG_DIR` set
to `/cc_sessions/<conversation>/<agent>`. This keeps the credential, MCP config,
prompt file, and attachment paths identical to the working `claude-code`
execution path. Before launch, PawFlow also writes the
session-local Claude settings that mark onboarding complete, trust the generated
session workdir, approve the PawFlow MCP server from `.mcp.json`, accept
bypass-permissions mode inside the isolated container, and add `Agent` plus
`Bash` to `permissions.deny`. CC interactive must not launch Claude Code's
internal sub-agent tool or run its local shell directly; PawFlow owns agent
delegation, shell execution, and records those turns itself. This prevents
first-run interactive prompts from consuming the pasted PawFlow prompt and keeps
multi-agent/tool execution inside PawFlow.

## Live Debugging

The chat UI action menu exposes `CC Interactive Tmux` for the selected agent. It
opens the existing terminal tab UI and starts a `docker exec -i` bridge into the
provider-owned Docker container. The bridge creates the PTY inside that Linux
container, then runs `tmux attach-session -t pawflow`. This is a live debug view
of the same tmux session receiving prompts, interrupts, and force-stop keys;
model output is still assembled only from MITM-observed response events.
The web terminal keeps local scrollback and enables tmux mouse mode for the
attached session, so wheel/trackpad scrolling can enter tmux copy-mode and move
back through Claude Code's interactive history instead of showing only the last
screenful. Before PawFlow injects a chat turn into the live tmux, it also sends a
best-effort `tmux send-keys -X cancel` so a debug attach left in copy-mode cannot
swallow the server-side paste or final `Enter`.

### Proxy capture log

The shell that starts the proxy appends its stderr to `/tmp/cci_proxy.log`
inside the container, which is where interpreter-level tracebacks raised before
logging is configured end up. Nothing reads that file programmatically, so it
can be inspected freely with `docker exec <container> cat /tmp/cci_proxy.log`.

`/tmp` is a 512 MB tmpfs shared with every tool call in the container, so the
proxy guards the file itself: a daemon thread checks it at startup and every
60 s and truncates it to zero once it passes 20 MB. Truncating under the live
writer is safe because the redirect opened the file with `O_APPEND`. The path is
set once in `core/_cci_pool_spawn.py` (`CCI_PROXY_LOG`) and passed to the proxy
as `PAWFLOW_CCI_PROXY_LOG`, so the redirect and the guard cannot drift apart.
Tune with `PAWFLOW_CCI_PROXY_LOG_MAX_BYTES` and
`PAWFLOW_CCI_PROXY_LOG_CHECK_SECONDS`; a max of `0` disables the guard.

## Vision

User image attachments are materialized into `.pawflow_vision/` in the session
workdir and referenced in the pasted prompt with `@/cc_sessions/.../image.png`.
This uses Claude Code's native interactive file read path.

Every materialized file goes through `core.image_resize.write_vision_image()`,
which downscales to the shared vision ceiling (`PAWFLOW_VISION_MAX_DIM`, 1568px
by default) before writing and names the file after the encoding it actually
wrote (a re-encoded PNG lands as `.jpg`). This is required, not an optimization:
the agent opens the file itself, so an oversized image is rejected at *its* read
time with the provider's "exceeds 2000x2000" error. The ingestion-time resize in
`_build_user_content` does not cover this — the live-preempt path (a message sent
while the agent is mid-turn) passes the raw uploaded `file_id` straight to the
provider. The same helper is used by the antigravity provider and by the Codex
app-server tool-result image path.

Tool-side image reads use PawFlow's multimodal marker contract:

- `see()` on an image returns `__image_data__:<mime>:<base64>`.
- `read()` on an image now does the same for FileStore, workdir, and relay
  filesystem reads.
- `ToolRelayService` converts that marker into native MCP image content.

## Current Limitations

The first implementation covers MITM event assembly, persistent tmux input, MCP
tool calls, image materialization, and lifecycle hooks. It still requires a live
Claude subscription session and has not been exercised here against a real
Claude Code Docker session.

## Codex Interactive Provider

`codex-interactive` reuses the persistent tmux and transparent TLS observation
architecture above, but runs the Codex TUI and observes OpenAI Responses API
events. PawFlow never reads terminal output or Codex rollout files to assemble a
response. The tmux is input and lifecycle transport; the MITM side channel is
the output source.

Each live session remains scoped by `(user, conversation, agent, LLM service)`.
The provider shares the OAuth credential pool used by `codex-app-server`:

- one OAuth credential may back any number of concurrent agents and containers;
- no credential slot is reserved for the lifetime of a Codex interactive
  session;
- only the short refresh transaction is serialized per `(service, pool index)`;
- after entering the refresh lock, a waiter reloads the pool and reuses a fresh
  access token written by another agent, even if OpenAI kept the same refresh
  token.

Endpoint selection is symmetric with Claude Code interactive. When
`llmConnection.base_url` is empty, PawFlow does not inject `OPENAI_BASE_URL`: the
Codex CLI uses `chatgpt.com` for subscription OAuth and `api.openai.com` for API
key mode. When `base_url` is set, Codex receives that URL, its host resolves to
the local MITM, and the proxy forwards to the configured host, port, scheme, and
path prefix. The CLI-facing leg remains TLS even when the configured upstream
uses plain HTTP.

The Responses observer handles output text, reasoning text, reasoning summary,
function calls, function-call outputs, model identity, and usage. A single Codex
turn may contain several `/responses` exchanges around MCP calls. Therefore
`response.completed`, `response.incomplete`, and `response.failed` terminate only
one upstream exchange; the documented Codex `Stop` hook terminates the visible
PawFlow turn. `UserPromptSubmit` also mirrors manual tmux prompts into the
conversation and activates the same orphan-turn capture used by Claude Code.

Cold and warm context follow the same binary rule as every CLI provider. A live
container receives only the delta. If no matching live container exists, the
new TUI must receive the complete PawFlow initial context. Context edits and
compaction evict both individual and conversation-wide Codex interactive
sessions so a stale TUI can never receive a delta after its history changed.

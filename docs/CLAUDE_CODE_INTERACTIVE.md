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
- Global and user skill scopes are mounted read-only under `/skills`. In a
  containerized server, their translated Docker-host source paths are outside
  the server namespace and therefore cannot be pre-validated with a local
  existence check; Docker validates those bind mounts when the CLI container is
  created.
- Runtime support files under `/opt/pawflow` are copied into the container once at
  spawn time instead of mounted read-only, so host-side development edits require
  a new interactive container before they are visible inside the session.
- MCP tools still go through the existing PawFlow MCP bridge.
- A live tmux/container receives appended turns. If that live instance is gone
  after idle reaping or restart, PawFlow starts a fresh Claude Code interactive
  session and injects the PawFlow initial context file again. Interactive mode
  never starts Claude Code with `--resume <session_id>`.
- Every interactive tmux window and browser viewer uses the same pinned
  220x50 grid as Codex Interactive. The browser never resizes the shared tmux
  window; xterm renders the fixed grid and letterboxes it when necessary.
- In API-key mode, PawFlow preapproves Claude Code's custom-key confirmation by
  storing only Claude Code's own 20-character key suffix in
  `customApiKeyResponses.approved`. The full API key remains only in the
  process environment, and a cold session cannot stop on the yes/no prompt.

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

API-key providers may prepend their own path to the Anthropic endpoint, for
example `/api/anthropic/v1/messages`. The observer identifies the normalized
endpoint by its `/v1/messages` suffix so request-side tool results and quota
probes remain visible with custom providers such as Z.ai. The server-side turn
coordinator and event service use the same suffix rule when arming per-request
state, clearing stale Stop latches, tracking turn boundaries, and adopting
orphaned turns. Optional wire logging applies the same normalization.

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
unchanged. Unsupported encodings (`br`, `zstd`) make the observer emit
`response_ignored` instead of decoding.

HTTP framing in the observers is isolated from leaf-observer failures: an
exception while decoding or emitting a response body (undecodable JSON,
unsupported encoding, malformed SSE) is logged and swallowed, and the chunked
parser always finishes the current response and hands the leftover bytes to
the next one. A terminating `0\r\n` whose closing CRLF arrives in a later TCP
segment still terminates the body. Without both guarantees a single bad
response desynced request/response pairing for the rest of the connection:
later responses were observed a full turn late under the wrong request id,
the coordinator finalized turns without their final message, and the webchat
only received the final answer (and `done`) when the next user prompt arrived.

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
refuses while a request coordinator holds its explicit lease, and when evicted
mid-turn it discards only its partial local text, never a queue event.

`CCIConsumerEvicted` is propagated as silent `AgentSuperseded` control flow. The
obsolete worker must not call the error emitter or publish `error_event`, `done`,
or `cancelled`; the replacement owns the visible turn. This also covers a
scheduled wake-up that claims the session immediately after a user message.

A claim owns the stream from the moment it is granted, not from the first poll.
The provider claims before it sends anything, and the send blocks on TUI
readiness, paste, submit and verification — up to a minute on a cold TUI —
before `run()` starts polling. Judging liveness on the last `wait_event()` alone
made that whole window look unowned: a `request_start` arriving inside it was
adopted as an orphan turn, and the capture's claim evicted the coordinator that
was about to read the stream. The provider holds an explicit request lease from
the pre-send claim through the complete coordinator run and releases it in a
`finally`, including on errors, aborts, and cancellation.

### What crosses the wire is shown in the webchat

That is the rule, and it holds for every MITM-observed session. Everything that
decides whether a turn is being watched — has a coordinator claimed recently,
did one poll recently, was a prompt injected recently — is a *guess about the
reader*. Each guess has its own way of being wrong, and every time one is wrong
the outcome is identical: the proxy streams a real turn into a queue, nobody
takes it out, and the webchat shows nothing while the tmux visibly works.

So the rule is enforced on a fact instead. `oldest_pending_at` records when the
events currently in a session's queue started waiting; `wait_event()` restarts
it on what is *still* queued (a consumer that takes one event and dies leaves
the rest waiting, and the rest is the point), and a drain clears it. Events
waiting longer than `_UNDELIVERED_ADOPT_SECONDS` (25s) mean nobody is reading
them, whatever the timestamps claim, and the turn is adopted — forced past the
liveness graces, because those graces are exactly the guesses being backstopped.

It stays safe against a live coordinator without asking about one: adoption
goes through a `capture` claim, and that claim is what arbitrates the takeover.
Deciding it on polling alone contradicted the window described above: a
coordinator inside its send has claimed and not polled, so 25 seconds of queued
events is what it looks like *by design*, and the net evicted it — the real
turn then died on its first read with `CCIConsumerEvicted` while the capture
kept writing its rows, so the webchat showed the whole turn with active-agents
and the context gauge dead for it. A capture claim is therefore refused while
`active_request_consumer_epoch` identifies a live provider turn. There is no
polling timeout: the observed failure paused a legitimate coordinator for 26
seconds inside context accounting, which is indistinguishable from death by
timestamps alone. The matching provider `finally` clears the lease as soon as
the turn actually exits, so genuine orphan turns remain adoptable.

Adoption *also* respects that lease, rather than firing on the queue and
letting the claim refuse it downstream. Leaving the two out of step produced a
loop: the rule adopted, `claim_consumer` refused, the refused capture consumed
nothing, so the queue stayed stale and the next sweep repeated it — one capture
every `_PENDING_SWEEP_SECONDS`, each streaming 0 chars and raising then dropping
the active-agent marker, for the entire time a slow TUI took to accept its
prompt. A stream a coordinator provably owns is not a stream nobody is reading,
and there is nothing there to adopt.

Two more facts the rule needs. `wait_event()` stamps the diagnostic freshness
clock only for the consumer that still owns the stream: stamping before the
epoch check let an already-evicted coordinator impersonate current activity.
And a session is marked
*between turns* by its `Stop` hook, because nothing drains the queue when a turn
ends: `drain_session()` runs when the *next* turn claims, so every finished turn
left its post-Stop tail waiting and was re-adopted 25 seconds later by a capture
that raised the marker and waited for a Stop that had already happened. Anything
that starts a turn — a real provider request, a prompt submitted in the tmux —
arms the rule again.

That boundary is decided on the events' own timestamps, not on the order they
arrive in. The two kinds of boundary event do not share a route: the proxy
emits `request_start` over one persistent event socket, while every hook run
opens its own connection, sends a single frame and closes it. A `Stop` delayed
on its way in can therefore be published *after* the next turn's
`request_start`, and taking it at face value marked the new turn as already
over — which disarms the backstop for that whole turn, so its answer waits in
the queue with nobody reading it and no capture is ever spawned. An event older
than the one that set the current boundary describes a turn that is already
history and is ignored. The comparison and boundary-state update are serialized
under the session stream condition because proxy and hook WebSocket handlers
may execute concurrently.

A capture claims before it announces itself. Discovering the refusal inside the
coordinator meant the active-agent marker had already been raised and was blinked
straight back off; a capture that yields the stream now says nothing at all.

A `cci-pending-sweep` thread re-asks every `_PENDING_SWEEP_SECONDS`. Checking
on publish alone would miss the case the rule cares about most: a turn that
streams in five seconds and then goes quiet has every one of its events
waiting, and no further publish will ever come to notice.

That ceiling is a backstop, not the mechanism. A send that fails builds no
coordinator at all, and the claim it left behind muted the net for the rest of
the grace window — which is exactly when the turn happens. The user watches the
send fail, presses `Enter` in the tmux themselves, and the TUI runs the prompt
it was holding all along: a real turn, streamed through the proxy, addressed to
nobody, with the webchat silent throughout. So a provider whose send fails calls
`release_consumer()` before it raises, and the net adopts the next
`request_start` as it should. The release is scoped by epoch: a claim taken
since then belongs to a live turn and must not be cleared by the loser's
cleanup.

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

The marker is ownership-scoped. A streaming worker and an out-of-band capture
share the same conversation/agent key, but each registration carries a unique
`owner_id`. A capture therefore cannot overwrite a worker marker, and either
producer releases the entry only when the stored owner still matches its own.
Generation numbers order streaming workers; they are not used as a substitute
for ownership. This prevents a terminal `active_released` path from leaving a
ghost marker or deleting a replacement turn during a concurrent CCI boundary.

A capture also owns the turn's *inbound* path. It registers `_active_turns`
without an `_active_contexts` entry or an `_active_claude_client`, and
`agent_streaming` reads that combination as "already active but not
preemptable": the message goes to the agent's `PendingQueue`. For a real turn
that window is brief and the turn drains the queue at its end; a captured turn
has no owner to drain it, so messages sent from the webchat used to sit there
until a force stop discarded them — while the UI showed the agent up. Two
rules close that:

- **Preempt the live tmux through its own provider pool.**
  `_deliver_to_captured_tmux` selects the Claude Code or Codex pool from the
  connected event session, then uses `send_interrupt` rather than a normal
  `send_text`. This matters during the transient/captured shape where there is
  an `_active_turns` marker but no published provider client: looking only in
  the Claude pool made Codex messages wait in `PendingQueue` until `done`, which
  made live preemption appear intermittent. Whether there is anywhere to
  deliver is decided by `CCInteractiveEventService.live_session`. The MITM
  WebSocket is up exactly while a container lives and every observed event
  arrived through it, so it proves a live tmux independently of stale turn
  bookkeeping. No connected session means no container, and the message falls
  back to the queue.
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
- `PAWFLOW_CCI_POST_STOP_PENDING_RESPONSE_CAP_SECONDS` bounds how long the
  post-Stop drain is HELD OPEN while a response is still owed: a
  `/v1/messages` request whose start was observed but whose end was not, or
  a follow-up after a response ended on `stop_reason=tool_use`. The `Stop`
  hook travels on its own connection and can outrun lagging SSE delivery;
  finalizing on idle then returned the turn without its final text and left
  the straggler events to be drained by the NEXT turn under the wrong turn
  id. Default `90` seconds for an open request; a merely owed follow-up
  (whose request may never come — the reader pressed `Esc` in the tmux)
  waits at most `20` seconds. When a delayed response completes with
  nothing further owed, the coordinator re-arms the stop latch itself,
  since no second `Stop` hook ever comes for a delayed replay.
  Only genuinely open requests count: the CLI's main loop streams one
  `/v1/messages` request at a time, so a fresh `request_start` supersedes
  every earlier open one — aborted retries whose `request_stop` was lost
  used to pile up (observed `open=5` on a 17-minute turn) and hold the
  drain for the full cap, showing the agent active for 90 seconds after
  the tmux had visibly finished.
- `PAWFLOW_CCI_NO_PROXY_EVENT_TIMEOUT_SECONDS` sets how long a submitted tmux
  prompt may produce no observed proxy event before PawFlow treats the turn as
  failed. Default: `0` (disabled); only a positive configured value enables
  this cutoff.
- `PAWFLOW_CCI_NO_PROXY_EVENT_TIMEOUT_MS` is the millisecond alias for the same
  value. The seconds variable wins if both are set.
- `PAWFLOW_CCI_LIVENESS_PROBE_IDLE_SECONDS` (default `20`; `_MS` alias) arms
  the mid-turn dead-session probe. A tmux server crash mid-turn takes the CLI
  down with it — no `Stop` hook, proxy event or error ever arrives — so the
  coordinator used to wait forever while new messages queued behind an
  "active" turn that could never end. Once the event stream has been silent
  for this long (never post-Stop, where the drain is already bounded), the
  coordinator probes container + tmux liveness (`session_is_live`); two
  consecutive dead probes fail the turn with a clear error so the pending
  queue drains and the next message recreates the session. A probe that
  ERRORS (slow docker daemon) never counts as death, and a live probe clears
  accumulated strikes. Silence itself stays legal: long local tool runs
  produce no wire events, and their probes simply come back alive. Wired on
  the request, interrupt, and manual-capture paths of both Claude Code and
  Codex interactive (the capture derives its probe from the proxy-reported
  `container_id`; a session whose proxy never reported one keeps the old
  behavior).
- `PAWFLOW_CCI_PASTE_SETTLE_SECONDS` sets the delay after `paste-buffer` and
  before the first `Enter`. Claude Code defaults to `0.2` seconds. Codex uses
  at most `0.2` seconds even when a larger inherited override is configured.
- Every prompt is loaded into one tmux buffer and sent with exactly one
  bracketed `paste-buffer -p`. Pane verification may reject an unconfirmed
  transport, but it never replays the paste and risks duplicating the prompt in
  the composer.
- A cold Codex send waits up to 12 seconds for two consecutive structural
  readiness observations. A UUID writer lock newer than the container state
  proves that Codex created the thread for this launch. Tmux must simultaneously
  report a live pane with input enabled, no active tmux mode and the application
  cursor visible; Codex exposes that cursor only for an editable composer. No
  model, version, placeholder, footer or other pane text participates. The wait
  uses a printable `|` delimiter because `display-message` sanitizes literal
  tabs to underscores. The wait remains advisory. After `paste-buffer`, the
  attachment chip, an already-running
  turn or a direct before/after pane reaction proves transport without replaying
  the paste.
- `PAWFLOW_CCI_SUBMIT_DELAY_SECONDS` sets the delay between repeated submit
  keys. Claude Code defaults to `1.0` second. Codex uses at most `0.2`
  seconds. A live Codex preempt waits for the structural editable-composer
  signal after `Escape`, `Escape`; if it never returns, the paste is refused
  and the pending rescue remains queued. Codex then submits both normal prompts
  and live preempts with the fixed sequence `Escape`, `Escape`, paste, 200ms,
  `Enter`, 200ms, `Enter`.
  In production only the exact `UserPromptSubmit` digest or matching MITM
  request confirms submission. If the structurally recognised composer still
  holds the pasted chip, verification may send up to three evidence-gated
  `Enter` retries within the same bounded verification budget. Unknown chrome,
  stale transcript chips and running turns never authorize another key.
- `PAWFLOW_CCI_IDLE_TTL_SECONDS` controls Claude Code idle container eviction;
  `PAWFLOW_CODEX_INTERACTIVE_IDLE_TTL_SECONDS` controls the equivalent Codex
  Interactive pool. **There is no default**: unset, or `0`, means containers
  are never evicted for being idle. Reaping a live agent is destructive, so it
  must be asked for explicitly rather than inherited from a silent fallback. A
  service request `timeout` of `0` means "no timeout" and disables eviction
  outright; a positive one can only extend an already-enabled TTL, never enable
  or shorten one.
- Eviction only ever considers a session that is doing nothing. A turn in
  flight is never evicted: both normal and interrupt paths in both providers
  bracket the whole turn with the shared pool's `begin_turn` / `end_turn`
  contract. Idleness is measured from the last event the MITM proxy observed —
  not from PawFlow's own turn bookkeeping, which stays frozen while a CLI
  resumes on its own (a backgrounded task reporting back, a queued message)
  outside any streaming worker.
- A live CLI session always makes the rebuilt context a resume delta, including
  queued retriggers whose exact user payload is supplied through preloaded
  messages. Message origin never changes the cold-versus-delta marker.

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
- The proxy deduplicates observed tool blocks at the source: every
  `/v1/messages` request re-sends the whole conversation history, and
  re-emitting each historical block on each request made event volume — and
  delivery lag — grow with the square of the turn count. Each `tool_use_id`'s
  use and result are emitted once per proxy process; the server keeps its own
  per-session dedup, so a proxy restart's one-off re-emission burst is
  harmless.
- **No tool call is filtered.** Every observed call is emitted and persisted,
  native ones included — `GetSchema`, `ToolSearch`, and Claude Code's `Read` of
  `.pawflow_cci/initial_context.md` among them. These were once suppressed as
  bootstrap noise; the cost was worse than the noise. A turn that opened by
  reading its own context showed an empty technical-details block, and nothing
  distinguished a deliberately hidden call from a lost one — which is exactly
  the question the transcript exists to answer. What the agent did is what the
  transcript shows.
- **Not filtered is not the same as re-serialized.** The bootstrap `Read` and
  its result stay in the transcript. They are dropped from the *agent context*,
  from *compaction input*, and from the *gauge*: that result body is the
  previous `initial_context.md`, so writing it into the next one nests a copy of
  the file the agent is already reading and the nesting deepens on every cold
  start; summarizing it duplicates the conversation; counting it charges the
  same context twice. `core/llm_providers/cli_shared.py` drops the pair in every
  CLI serializer and in `_compact`, and `tasks/ai/context_usage_cache.py` zeroes
  it for the count. Transcript and context are two surfaces with two rules — the
  earlier fix made the call visible, and visible must not mean fed back in.
- Outgoing `/v1/messages` request bodies are observed for both assistant
  `tool_use` blocks and user `tool_result` blocks. This preserves live ordering
  even when a response-side tool block is delayed or missed; provider events are
  deduplicated by tool id. Tool results keep the real result content; only
  diagnostic wire dumps are scrubbed/redacted.
- `message_delta.usage` updates token usage.
- Claude Code command hooks publish `Stop`, `StopFailure`, `PreCompact`,
  `PostCompact`, `SessionEnd`, and `UserPromptSubmit` lifecycle events over the
  same WebSocket. `PreCompact` and the defensive `PostCompact` fallback are
  terminal signals: PawFlow removes the native session and runs its own forced
  compact before cold-starting the provider from the canonical context.
- Only the Claude Code `Stop` hook closes a PawFlow turn. Anthropic
  `message_stop` events are observed for diagnostics but do not terminate the
  interactive turn, because Claude Code can issue intermediate model requests
  before the tmux turn is complete. Response content still comes only from
  MITM-observed response events.
- A `StopFailure` hook (the CLI gave up on an API error, e.g. an upstream
  `429` usage limit) ends the turn as a **non-retryable** `LLMCallError`
  carrying the CLI's error text. Interactive CLI providers are never re-run by
  the driver or agent-level retry loops: the prompt is already consumed by the
  live session and the CLI performed its own API retries, so a PawFlow retry
  would paste the prompt again or trip the cold/delta context guard. The
  failure is surfaced to the webchat and the agent stops.

## Prompt Input

When PawFlow starts a new interactive Claude Code container, the provider writes
the serialized PawFlow system/context/history into
`.pawflow_cci/initial_context.md` inside the session workdir. The first pasted
prompt references that file with `@/cc_sessions/.../.pawflow_cci/initial_context.md`
and instructs Claude Code to read it before answering. Existing live sessions do
not receive the full context or tool instructions again; PawFlow sends only the
latest turn delta, any current attachment references, and a narrow catch-up block
containing new messages from other participants since the agent's last response.

### Multi-message drain and msg_id dedup

The live-session delta is NOT just the newest user message. A retrigger turn
can carry several drained user messages (e.g. N delegate results preempted
while the previous turn was ending); `_cci_live_text` renders the whole tail
of consecutive user messages after the last assistant reply, in order, in one
paste. Each session tracks the `msg_id`s it has already conveyed
(`InteractiveContainer.submitted_msg_ids`, updated after every successful
paste — cold context, catch-up, and live tail all count): a message is never
pasted twice, and when a spurious retrigger finds the whole tail already
submitted the prompt build sends nothing instead of re-pasting the latest user
text (which used to produce double-delivered delegate results). The Codex
interactive provider shares this contract.

The bootstrap paste is one short physical line and never quotes the current turn
inline. The turn is already in the file under `## Latest User Request`; copying
it into a terminal composer created a second transport and let multiline paste
pieces become independent TUI submissions and false user messages. The paste
only points at the file and names the section containing the current request.

Live interrupt pastes the interrupt message, then sends `Escape`, then `Enter`
as separate tmux key events. If the interrupt carries image attachments, PawFlow
materializes them into `.pawflow_vision/` and includes `@/cc_sessions/...` file
references in the pasted message. Force stop sends `Escape Escape` to the tmux
session and leaves the container lifecycle intact.

A submit is verified, not assumed. An `Enter` that lands inside the TUI's
paste-detection window becomes a pasted newline and the prompt stays in the
input box, so `_verify_submitted` polls the pane and presses `Enter` again if it
is still there. How "still there" is read depends on the TUI: Claude Code echoes
pasted text, so the absence of a tail fragment of the injected prompt means it
was accepted. The Codex TUI never renders pasted text -- it shows
`[Pasted Content N chars]` -- so that fragment is absent from the first poll
onwards and the check reported success while nothing had been sent; six pastes
stacked up in one composer until a human pressed `Enter`. Pools whose TUI
collapses pastes declare their chip in `_PASTE_CHIP_MARKERS`, scoped to the
composer via `_COMPOSER_PROMPT_PREFIX` so a chip left in the transcript by an
already submitted message is not mistaken for an unsent one.

### An empty composer is not proof of a submitted prompt

Enter was the only retry there was, and it answers the wrong failure. When the
**paste itself** never reaches the composer -- a TUI still drawing itself, a
pane not yet focused -- the input box is empty, which is exactly what a
delivered prompt leaves behind. `_verify_submitted` read that as "submitted",
`send_text` returned success, and the turn then waited on a session that had
been asked for nothing: no `UserPromptSubmit` hook, no proxy event, the agent
shown active until the 300-second no-event timeout. The server log carried no
error at all, because nothing had failed as far as any code could tell.

So `send_text` proves the paste landed before pressing anything: capture the
pane, paste, capture it again, and paste **again** only if the screen never
moved -- bounded to `_PASTE_ATTEMPTS`, after which the send fails loudly and the
provider raises. Enter cannot fix an empty composer; only another paste can.
Where the TUI leaves no usable signal (no before-image, no chip declared, text
too short to probe) the answer is "cannot tell", never a refusal, and the
double-Enter path behind it is unchanged.

The before/after comparison leads, and the chip and fragment probes come after
it, because it is the only test that does not model the TUI. The chip probe
assumes the composer can be located by its prompt prefix; the fragment probe
assumes the pasted text is rendered as text and findable across the layout the
TUI wrapped it in. Both are read off a TUI that ships a new version every few
weeks, and each time one of them goes stale the failure is the same: a prompt
sitting in the composer needing nothing but `Enter` is declared missing, pasted
again, and again, until the send fails with the prompt stacked three times in
the input box -- which is not merely a failed turn but a composer a human has
to clear by hand. Whether the screen changed is a fact about the screen: a TUI
that renames its chip, boxes its composer or re-wraps its text still redraws
when 16 kB arrive.

What the comparison costs is a pane that redraws for its own reasons reading as
a landed paste. That direction is the cheap one: a false "landed" presses
`Enter` into a box that may be empty, which is a no-op, and the turn fails
visibly on the no-event timeout with the session left clean. A false "missing"
pastes the prompt into a composer that already holds it. Those are not
symmetrical, and the check must not err toward the second.

The injected text is recorded once, not once per attempt: the hook guard counts
tickets, and a second record would leave one unspent to swallow the next thing
the user really types.

A Claude Code failure on any of these paths logs the pane it happened on. Every check in
the pool reads the screen and each of them used to report only its own verdict
— *TUI prompt not detected ready*, *paste did not reach the composer* — which
says that our reading of the screen failed and never what was drawn. When a TUI
release moves its footer or boxes its composer, that is the whole difference
between a fix and a guess, and the pane ended up being inferred from a
photograph of a terminal. `_pane_diagnostic()` appends the head of the pane
(`_PANE_DIAGNOSTIC_CHARS`, 2000) to the warning; it costs one extra capture on
a path that has already failed, and an unreadable pane degrades to a note. Note
that on a fresh session this puts the beginning of the injected prompt in the
server log — bounded, and only on failure. Codex interactive deliberately
overrides this diagnostic with an empty string: its attachment chips and pane
history can contain user prompt material, and Codex transport warnings never
copy the pane into the server log.

Historically, readiness parsed input-box labels from the pane. That contract is
gone: every such label is release UI copy. In particular, `>_ OpenAI Codex
(v...)` is permanent startup chrome and once caused the wait to return before an
editable composer existed. Current readiness reads only the current launch's
thread-writer lock and tmux's editable-cursor/input state.

That header is also why "the composer is not on screen" cannot mean "landed".
It used to: an unlocatable composer was read as unknowable and answered `True`,
which accepted the exact case the proof exists for -- a pane holding only the
header locates no composer, so a paste into a TUI that has no input box yet
(past the readiness timeout, or during a redraw) was declared landed, `Enter`
went nowhere, and the turn sat on a session nobody had prompted until the
300-second timeout. A missing composer is now not an answer at all: the probe
keeps looking until its window closes, then refuses. A pool that declares no
`_COMPOSER_PROMPT_PREFIX` is untouched -- its whole pane *is* the composer, so
it is always located and its answer is what it always was.

Known and left alone: on a TUI that echoes pasted text, the probe fragment is
searched across the whole pane, and the same prompt still visible in the
transcript can answer for a paste that never landed. Narrowing that search
costs a **false negative** -- a landed paste declared missing is pasted again,
and the composer then holds the prompt twice, which is worse than the
ambiguity. It stays until such a composer can be located outright.

The fragment is searched on the **squeezed** pane: borders and all whitespace
removed from both sides before the comparison. `capture-pane` returns a screen,
not a buffer, and the composer hard-wraps the prompt at the pane width, so a
24-character tail routinely arrives split across two screen lines with a border
and padding between its halves -- every character present, and absent from any
verbatim search. The wrap falls at the same column on every attempt, so the
failure was deterministic: all three pastes declared missing, and a prompt that
was sitting in the composer needing only `Enter` failed the turn with "never
reached the composer". `_verify_submitted` keeps the verbatim test on purpose --
there an absent fragment means "submitted", so a wrap-blinded match errs toward
leaving a running turn alone, while a squeezed one would keep finding the prompt
Claude Code echoes into its own transcript and press `Enter` at it.

If a user attaches to the provider-owned tmux and submits a prompt manually,
Claude Code's `UserPromptSubmit` hook sends that prompt to PawFlow. PawFlow
persists it as a normal user message with `channel="tmux"` and starts a passive
MITM capture for the resulting Claude Code turn, so the assistant response also
lands in the conversation context. Prompts pasted by PawFlow itself are recorded
by SHA-256 in `.pawflow_cci/injected_prompts.jsonl`; the hook consumes that
marker and does not mirror those managed prompts back into the transcript. The
marker also holds a whitespace-normalised, consumable copy of the injected text.
Unlike the event service's in-memory copy, it survives a stop, compact, or
session-state replacement while old paste chips can still be waiting in the TUI.

### Grab: the composer as a terminal input

When the selected agent runs on an interactive CLI provider and its tmux is
live, a grab button (🖥️) appears in the composer row, before the reload
button. Held, the chat composer becomes a direct input to that TUI:
what you type lands in the terminal exactly as if you were attached to it.
Releasing it puts the composer back on the normal `/api/agent` path. Switching
agent or conversation, or the session dying, releases it on its own.

It reuses the terminal transport whole — `open_cc_interactive_terminal` for the
session and token, then `terminal_input` over the terminal WebSocket, raw bytes
into the container PTY. Grab is write-only: the terminal tab is where you watch
the pane.

Two rules make a grabbed prompt a real conversation turn rather than a blind
write into a terminal:

- **Never through `pool.send_text()`.** That path files a SHA-256 ticket in
  `injected_prompts.jsonl` whose whole purpose is to stop the hook from
  mirroring the prompt — correct for a PawFlow-injected prompt, exactly wrong
  for one a human typed. Typed through the PTY, the `UserPromptSubmit` hook
  mirrors it as a `channel="tmux"` user message and the MITM captures the
  answer, so the turn appears in the chat by the same route as a prompt typed
  at the tmux. Nothing is echoed locally, or the hook's copy would double it.
- **A typed newline is created by the grabbed TUI.** Browsers reliably expose
  `Shift+Enter`, so Grab flushes the current line and translates that chord to
  the CSI-u `Ctrl+Enter` sequence (`ESC[13;5u`) understood by Codex, Claude
  Code and Antigravity. The same newline is inserted into the visible webchat
  draft, while a mirrored-prefix cursor ensures final `Enter` sends only the
  remaining suffix instead of duplicating earlier lines. Outside Grab,
  `Shift+Enter` remains a local webchat newline and never submits the prompt.
- **A block that is already multiline was pasted, not typed**, and goes as one
  bracketed paste (`ESC[200~` ... `ESC[201~`). Every non-empty terminal write,
  including a one-line prompt, then gets the bounded settle delay before its
  single `Enter`: WebSocket frame order does not mean the TUI has ingested the
  text frame before the key frame arrives.

Captured answers stream as transient `token` events and are finalized by a
durable `new_message` carrying the same message id. The browser reconciles that
pair in place instead of dropping the final event as a duplicate, and the
capture verifies on exit that the coordinator's final text has a persisted row
before publishing `active_released`.

Keys, grabbed: unmodified `Enter` submits to the TUI; `Shift+Enter` sends
`Ctrl+Enter` to the tmux and therefore creates a newline in the TUI composer
without submitting, while inserting the matching newline in the webchat
composer for readability. `Esc` passes through, which
is what Codex's *Esc to interrupt* needs; `Ctrl+C` passes through as `0x03`
unless there is a selection, where it stays a copy. `Enter` on an empty
webchat composer still submits — that is how lines already held by the TUI are
sent.
Typing while a turn runs is allowed and queues in the TUI, exactly as it would
for a human attached to the same tmux.

Grab covers every provider that owns a tmux: `claude-code-interactive`,
`codex-interactive` and `antigravity-interactive`. The first two attach through
`open_cc_interactive_terminal`, Antigravity through
`open_antigravity_interactive_terminal`; `_GRAB_OPEN_ACTIONS` maps provider to
action and the listing that drives button visibility covers all three pools.
The Antigravity pool reports itself as `antigravity-observer` (what the
container is) and the listing normalises that to the LLM provider name callers
dispatch on.

The browser terminal is only a detachable tmux viewer; it does not own the
provider session. A compact or interruption may legitimately replace the
provider container while the logical turn continues. The registered viewer
therefore keeps its capability identity but resolves the pool's current
container and rebuilds `docker exec` on every WebSocket connection. The xterm
client retries up to twelve times with a delay capped at two seconds, covering
the cold-start gap without looping forever. Closing the tab disables retries.
Exhausting the bound still shows `Process terminated`, so a genuinely dead
container or tmux is not hidden.
Likewise, `Active Agents` represents current work rather than a warm session:
cleanup removes markers from the same or any older generation and preserves
only a strictly newer turn marker.

One paste does not always produce one submit. A TUI that collapses pasted text
into an attachment chip can submit a composer holding several of them as several
`UserPromptSubmit` hooks, and a piece's SHA-256 matches no recorded injection.
PawFlow therefore also keeps the injected *text* in memory and in the local
marker, and treats a prompt that is an unspent slice of it as its own. The
durable hook marker uses a 300-second lifetime and a 180-second fragment burst;
the service retains its exact-match state for 600 seconds. Without that, the
pieces
after the first were filed as messages the user had typed, published under their
name and answered one by one -- the agent replying to fragments of a tool result
it had just been handed. A slice shorter than 12 characters is still treated as
manual: `ok` occurs inside any large paste and is also what a human types. Each
matched slice is removed once, and an exact full-prompt submit clears all slice
state, so later human text cannot be claimed merely because it occurred in an
older PawFlow paste.

The hook can also mark the submit as ours outright (`pawflow_injected_prompt`
on the hook input), and that is the main path -- the digest path only sees
submits the flag did not carry. The flag says "this submit is PawFlow's"; it
does not say how much of the injection it carried. Spending only the ticket left
the recorded text behind: digests 0, tickets 0, texts 1, and for the rest of the
window any twelve-character phrase the user typed that occurred inside it was
claimed as a fragment -- the message neither persisted nor answered, gone
without a trace. The marked path now accounts for the text exactly as the digest
path does: a submit carrying the whole injection drops it, a submit carrying a
piece cuts that piece out and leaves the rest matchable, and only a submit that
identifies nothing falls back to retiring the oldest record -- with its text,
since a digest that is gone can never match again.

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

Credential slots are shared. One Claude login can back any number of
concurrent interactive containers (parent agents, flash delegates, task
sub-agents), so `ensure_started` never refuses a launch because every slot
already has a live session: `_claim_pool_slot_locked` balances new containers
onto the least-loaded slot (live containers + in-flight reservations counted
per service). The only credential error left is the pool being empty —
no `/cls` login configured at all.

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

The Codex interactive viewer uses the same fixed `220x50` grid as its pinned
tmux window for both the bridge PTY and xterm. Tab switches and resize observers
therefore reassert that grid instead of calling the fit addon and changing only
the browser side. Claude Code interactive and ordinary terminals retain their
responsive fit behavior. No viewer resize is propagated to the shared tmux
window, so attaching a browser still cannot deliver `SIGWINCH` to a running TUI.

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

Codex readiness is a short advisory wait on its own clock (12 seconds,
`_PROMPT_READY_SECONDS`), not the inherited 45-second one, and **never** a
gate. It reads no pane text. A thread-writer UUID lock created after the current
container state proves that this Codex launch has created its thread; tmux then
must report a live, input-enabled pane outside copy mode with the application
cursor visible. Codex only asks the terminal to show that cursor while its
composer accepts edits. The tmux format uses `|`, not tabs:
`display-message` rewrites literal tabs to underscores before returning its
output. Two identical observations reject a transient redraw.
The transport proof remains the before/after paste comparison below, which does
not model the TUI and refuses when nothing reached the composer. A successful
paste proof latches the session ready for later turns. Submission verification is also
Codex-specific: absence
of the injected text is never accepted as proof because Codex renders pastes as
attachment chips. If the composer layout is unknown and no running marker is
visible, PawFlow retries `Enter` up to three times; an extra `Enter` in an empty
composer is harmless, while omitting it leaves the prompt waiting for a human.
The same receipt rule applies synchronously to live preemption: Codex does not
report the preempt handled, set `_had_preempts_this_turn`, or suppress its
PendingQueue rescue until an exact `UserPromptSubmit` or a subsequent MITM
request proves submission. A different post-marker submit is tracked separately
from no submit at all; it grants the expected prompt another acknowledgement
window and produces an accurate diagnostic if the expected digest never lands.
If a receipt-verified preempt fails without killing the CLI, the current turn
keeps ownership and the message stays queued for the next drain.

Each live session remains scoped by `(user, conversation, agent, LLM service)`.
PawFlow validates both the Docker container and its `pawflow` tmux session before
reuse. If the CLI or tmux is terminated while the container remains alive (for
example by pressing `Ctrl-C` in an attached terminal), PawFlow evicts that stale
container, rebuilds the turn as a cold start with full context, launches a fresh
interactive session, and submits the same user turn automatically.

PawFlow also owns compaction for normal `codex-interactive` agent sessions.
Codex's `PreCompact` lifecycle hook is a terminal control signal: the turn
coordinator rejects the native compact, removes the pooled Codex container
immediately, and raises `CCCompactDetected`. The common agent loop then flushes
the transcript, runs PawFlow's forced compact, adopts that canonical context,
and cold-starts Codex from it. `PostCompact` is handled by the same path as a
defensive fallback, so a missed or reordered pre-hook cannot make Codex's native
summary authoritative. Claude Code interactive uses the same hook handoff; the
legacy Claude Code stream detects its equivalent `compact_boundary` event.
The detection channel differs, but PawFlow remains the sole owner of the
resulting context.

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

The session slot is declared trusted in the generated `config.toml`, mirroring
the Claude Code settings that trust the CC session workdir:

```toml
[projects."/cc_sessions/<conversation>/<agent>"]
trust_level = "trusted"
```

Without it the TUI opens `Do you trust the contents of this directory?` on a
cold start and waits. That modal does not produce the current launch's editable
composer state, so the structural readiness wait expires and a best-effort paste
cannot submit the bootstrap. The trusted path is the
directory the tmux actually `cd`s into, and the table is written inside the
PawFlow-managed section so a regeneration replaces it instead of appending a
second table for the same path. `codex exec` never asks the question, so the
headless provider does not emit the table.

### The tool set matches `codex app-server`

Both codex providers run the same binary, in a container whose cwd is the same
session workdir, and both bootstrap from the same
`.pawflow_cci/initial_context.md`. Only the transport differs — JSON-RPC over
stdio for app-server, a tmux TUI here — so the tool set must not differ either.
`codex app-server` is launched with no blocklist, and this provider now does the
same.

The blocklist `codex exec` carries (`--disable shell_tool`, `unified_exec`,
`view_image`, ...) exists to push the agent through the MCP bridge rather than
the container's own filesystem. Applied to the TUI it removed the only way
Codex could read the cold-start context PawFlow hands it. Native file tools
remain available for local CLI state and attachments, while project work is
steered through PawFlow MCP. The relay server-fs also merges the Claude, Codex,
and Gemini session roots, so MCP reads of
`/cc_sessions/<conversation>/<agent>/.pawflow_cci/initial_context.md` resolve to
the live Codex workdir instead of an empty same-named Claude directory.

Steering the agent toward PawFlow tools for user work stays a prompt concern,
exactly as it already is for app-server.

### Every `*_call` item is a tool call

The Responses taxonomy does not put every tool call under `function_call`. Codex
runs its own shell as a `local_shell_call` whose arguments are an `action`
object, its freeform tools (`apply_patch`) as a `custom_tool_call` whose
argument is a raw `input` string, and hosted tools as `web_search_call` and
friends; only MCP tools arrive as `function_call`. Matching that one literal —
which is all the observers and the turn coordinator did — showed a turn with no
tool at all while the model was visibly running them, because restoring the
builtins is exactly what made the non-`function_call` types appear.

`observed_call_item` / `observed_call_output` in `tools/cc_interactive_filters.py`
match the `_call` and `_call_output` suffixes instead, and read the arguments
from whichever of `arguments` / `action` / `input` the item carries. A type
nobody has seen yet renders under its own name rather than disappearing. The
proxy logs `item_type=` on each `response.output_item.done`, so what a given
Codex build actually emits is readable from `/tmp/cci_proxy.log`.

### A code-mode body is not a tool call

The GPT-5.x "sol" harness does not call tools directly: it runs JavaScript in
a freeform `exec` tool and calls everything from inside it —
`tools.exec_command({cmd: ...})` for its own shell, and
`tools.mcp__pawflow__use_tool({tool_name: "read", ...})` for PawFlow. Rendered
literally every row reads `exec(const r=await tools...)` under one name and
one native badge: neither which tool ran nor on what is readable, and a
PawFlow call is indistinguishable from a shell command.

Reading the tool names back out of the script does not hold. A first version
parsed the body with a small JS literal reader; real bodies defeat it on the
first try — property shorthand (`{tool_name}`), a table of calls driven by a
loop, `Promise.all(names.map(...))`, a `.filter()` over the tool list. None of
those are corner cases, they are how code-mode is written, and each one left
the row as raw `exec(<javascript>)`.

PawFlow does not have to read the script, because it *runs* those calls: they
arrive at the tool relay one by one, each with its tool name, its arguments
and its result. So the rows come from there.

- `code_mode_body` (`tools/cc_interactive_filters.py`) only recognises such a
  body — a call whose source invokes `tools.<something>(`.
- `_CodexInteractiveTurnCoordinator._emit_observed_tool_use` flags the session
  with `mark_code_mode`, and still renders the item.
- `ToolRelayService._handle_execute` publishes each call it executes for a
  flagged session through `CCInteractiveEventService.publish_agent_event`, as
  an ordinary `tool_use` / `tool_result` pair.

Those events enter by the same door as the MITM's own observations, so they
become rows through the one path every provider shares — real name, real
arguments, MCP badge, real result, background and kill buttons included.

The flag is per turn, not per session: `claim_consumer` clears it when the
next turn takes the stream. And the relay only publishes a call no provider
row was waiting for (`pop_cc_tc` found nothing) — a tool the model called
directly already has its row, and publishing it again would show it twice.

`codex app-server` never sees any of this: it reads codex's own JSON-RPC items
(`mcpToolCall`, `commandExecution`, `fileChange`, `dynamicToolCall`), already
decomposed and typed. The wire's single `custom_tool_call` is a MITM-only
problem, and sourcing the rows from the relay is what restores parity.

The body keeps its own row. Dropping it — which an earlier version did, to
remove the unreadable `exec(<javascript>)` line — hid more than that line: a
script does not have to reach a tool through PawFlow, and what it runs with
Codex's own runtime is executed by no relay and reported by nobody. Reading
the bootstrap context is the first thing such a script does, so the calls that
load a turn's whole context existed in no view at all. The row is coarse, but
it is the only evidence the turn ran anything; the relay's rows name the rest,
and the model's direct native calls render as before.

#### The row is kept, both of its halves are not quoted twice

A call has two halves and both are persisted and replayed: the arguments
(`_displayable_args`) and the result (`_displayable_result`). For a code-mode
group each carries the same duplication.

- **Arguments.** The script source is kilobytes of generated JavaScript
  describing work the rows beside it already name. The row reports its size.
- **Result.** The script's output *quotes those very calls*: the relay hands
  each result to the script, which prints them. So the same bytes reached the
  next context twice — once as each call's own tool result, once more inside
  the script's. Each quotation is replaced by a pointer to the row that holds
  it.

The result elision is **per fragment, not per output**, and that is what makes
it safe. A script does not only relay: it compares what it read, counts it,
concludes. Replacing the whole output because the script happened to make a
call deleted that conclusion, and no row was holding it. So only what a row
already carries verbatim (and is long enough to be worth a marker) is dropped;
everything else is kept exactly as printed, and an output that quotes nothing
is returned untouched. A script that reached no PawFlow tool at all — read
through Codex's own runtime, or a value computed and printed — is described by
nobody else and keeps everything.

The rows a script answers for are the ones **emitted while it ran**, tracked
per row rather than counted per observation. A Responses call item is observed
twice — streamed as it is made, then replayed in the next request's input —
and the coordinator renders the second observation onto the first row; counted
there, a re-read became a call the *next* script had to answer for, and that
script lost the output only it had.

None of this changes the *gauge*, which on codex-interactive is measured on the
wire rather than counted from messages. Codex never sees the relay's rows: its
window holds the script and its output once, so the duplication was invisible
while a session stayed warm. It became real at every cold start — which is to
say at every compaction restart, when PawFlow rebuilds `initial_context.md`
from its own context and hands it back. Exactly when the window is already
full.

The same argument elision also hides the bootstrap path from the ordinary
next-context deduplicator. A persisted code-mode row only says
`<code-mode script, N chars>`, plus a boolean bootstrap-read marker when the
original script accessed `initial_context.md`. The marker survives for every
page of a paginated read. Older unmarked rows retain a narrow fallback: their
linked result is recognized by the exact `# PawFlow Initial Context` header at
the start of a line. The call and result remain visible in the transcript,
while neither is copied into the next cold bootstrap. Prefixing or quoting that
title does not trigger the fallback.

Two things `publish_agent_event` has to get right for any of that to be worth
anything.

**Which session receives it.** A session is never unregistered, so a
conversation accumulates the state of every container it ever had — each one
still flagged `code_mode_open`. Insertion order handed the event to the oldest
of them: the publish reported success while the event sat in a queue nobody
reads, and the live UI drew no row at all. Candidates are now ordered
connected-first, then newest — `connected` being the same evidence
`live_session` trusts, since the proxy WebSocket is up exactly while the
container is alive. It orders rather than filters: a provider whose proxy
never marks the session must not lose its rows over it.

**Whether it is a result or a refusal.** `is_error` was hardcoded false, so a
read-only denial, a rejected approval and a failed hook all drew a green row
under a tool that never ran — the model read the refusal while the user read a
result. It is now derived the same way the MCP bridge derives `isError` for
`tools/call`: the gates in `_handle_execute` all answer with a string, `Error:
…` or `Blocked by hook: …`. A list payload is a block set, never an error.

### One call, two ids, one row

A call item carries both `call_id` — the id its `*_call_output` quotes — and
`id`, the item's own. A call is observed twice: streamed on
`response.output_item.done` as the model makes it, then replayed in the next
request's `input` alongside its output. Nothing guarantees the two
observations quote the same id, and when they differ the same call renders
twice: the first row never receives the result and the turn ends stamping it
`[Stopped]`, the second carries the output.

`observed_call_ids` returns both, the observers ship them as `alias_ids`, and
`_emit_observed_tool_use` keys the block on every id it has seen — so the
second observation finds the first one's block, and a result quoting either id
is reported under the id the row was rendered with. Where both observations
already agree this changes nothing.

Outputs are not always strings either: Codex returns its own tools' output as a
list of content parts (`[{"type": "input_text", "text": ...}, ...]`).
`call_output_text` flattens them, otherwise the user reads the JSON envelope
instead of what the command printed.

### The turn is a WebSocket, not an SSE body

Since Codex 0.146 a turn is not a POST with a streamed body. The CLI opens

```
GET /backend-api/codex/responses
Upgrade: websocket
openai-beta: responses_websockets=2026-02-06
```

and exchanges the *same* Responses events as WebSocket messages, compressed with
`permessage-deflate`. ChatGPT answers the offer with a bare `permessage-deflate`:
no `no_context_takeover`, no window-bits limit, so both peers keep their
compression window across messages. One decompressor per direction per
connection, fed the `00 00 FF FF` trailer RFC 7692 strips from each message —
a per-message reader decodes the first message and then fails.

The proxy forwards bytes untouched either way, so the CLI never noticed the
change. The observers did: a 101 carries neither `Content-Length` nor chunking,
so left on the HTTP path the frames that follow are read as the next response
header and every event of the turn is lost. The coordinator still saw the
`request_start`, so its no-observed-event guard never fired and the turn simply
returned empty — no text, no tool calls, no error. `tools/cc_interactive_ws.py`
decodes the frames; both directions carry plain JSON:

- upstream → client: one Responses event per message, republished under the same
  `sse` envelope SSE used, so no consumer knows which transport carried it.
  Codex adds `codex.rate_limits`, `codex.response.metadata` and
  `responsesapi.websocket_timing`, which the coordinator ignores;
- client → upstream: one `response.create` whose `input` array holds the
  `function_call` and `function_call_output` items the HTTP body used to hold,
  read by the same `_emit_observed_tool_blocks`.

An upgrade negotiating an extension this decoder cannot undo emits
`request_error` rather than nothing: an unreadable stream must fail the turn
where it breaks, not time out five minutes later with no reason.

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

### The Codex context gauge uses the native session counter

Codex owns its context window. PawFlow cannot enumerate what is in it: the
provider's own system prompt and tool schemas are invisible to it, and the
PawFlow context itself is externalized into `initial_context.md`, which Codex
reads with its own tools.

The gauge used to be rebuilt from the messages PawFlow holds, gated on
observing the native read of that file (`_is_cli_bootstrap_read`,
`tasks/ai/context_usage_cache.py`). A code-mode harness reads it from inside
its script, so that call never reaches PawFlow — the gate never opened,
`_context_messages` kept returning `[]`, and the gauge read 0 for the entire
life of the session. A gauge stuck at 0 never trips auto-compaction, and
switching conversation showed 0 because the persisted snapshot said `cold`.

After every `/responses` exchange, PawFlow reads the latest native
`.codex/sessions/**/rollout-*.jsonl` `token_count` event. This is the same
session telemetry Codex owns, and remains authoritative when the proxied
Responses usage describes only the current exchange.

- `codex_rollout_context_usage` reads
  `info.last_token_usage.input_tokens`, never
  `total_token_usage.input_tokens`: the latter is cumulative billing across
  exchanges and can be many times larger than the window. It also reads
  `info.model_context_window` as the native denominator.
- `observed_context_tokens` keeps `usage.input_tokens` from the MITM stream as
  a fallback when the rollout has not published a valid counter yet.
- `_merge_usage` records the **last** proxy exchange as the fallback, while it
  keeps summing for cost. A turn runs several exchanges; summing their prompts
  would report several times the window. Last also means a compaction inside
  Codex moves the fallback gauge down.
- `record_observed_cli_context` stores it per `(conversation_id, agent_name)`
  on `_cli_observed_context_tokens_by_stream`, created in `LLMClient.__init__`
  and shared by reference with call clones — so the clone that runs the turn
  and the resolver client the gauge reads expose one value.
- `compute_context_usage` prefers that number over the reconstructed one and
  reports `cli_context_state="active"`. It is read without an active context
  too, which is what makes the gauge survive a conversation switch.
- `reset_cli_context_usage` drops it: after a session is invalidated the old
  measurement describes a window that no longer exists.

Claude Code interactive measures the same way, from a different source. Its
MITM proxy sees every `message_start`, whose `usage` is the exact prompt size
Anthropic counted: `_cci_record_observed_context`
(`core/llm_providers/claude_code_interactive.py`) sums `input_tokens`,
`cache_read_input_tokens` and `cache_creation_input_tokens` — cached tokens
occupy the window exactly like uncached ones, and for a Claude Code session
they are most of it — and hands the total to the same
`record_observed_cli_context`. Both the turn path and the interrupt path record
it: both coordinators run against the same window.

Stateless API providers use the same native-first gauge contract. Immediately
after each completed request, before any local token fallback, the common LLM
driver records the provider's full input occupancy: uncached input plus cache
read and cache creation tokens. It publishes that observation as live
`message_meta` and increments a per-stream revision even when two successive
requests report the same number. Because this measurement describes one request
rather than a persistent provider session, PawFlow may add messages appended
after it until the next native request measurement replaces the base. A response
with no native usage never promotes the local tokenizer estimate to a measured
gauge.

**When** it is recorded matters as much as that it is. The end-of-turn record
above runs after `coord.run()` returns, and every live gauge update happens
before that: the emitter recomputes on each appended message
(`_publish_context_usage("append")`) and on heartbeats, all inside the turn. So
the measurement existed but arrived too late, and the UI displayed the
reconstruction — 0% for an externalized context past the read ceiling — for the
whole turn, snapping to the real number only after the last token. The
coordinator now takes a `usage_callback` and fires it at each `message_start`
that revises the prompt size (`_publish_usage_observation`, which passes a
snapshot of the accumulated `usage` and never raises), so the turn's first API
exchange already puts a measured number in front of every consumer. Antigravity
carries the same callback on its own coordinator, fired wherever an observed
event updates `usage`.

The emitter's heartbeat gate had to follow: `_context_usage_input_signature`
keyed only on the PawFlow message list, and for an observed CLI provider the
gauge moves with no new message at all — a long stretch of provider-side work
revises the measurement on every `message_start`. The signature now includes
`_observed_context_measurement`, so a measurement that moved on its own
republishes.

The append fast path needs no change: `context_usage_append_delta` already
declines a measured cache (adding PawFlow's own count on top of a number that
already contains the appended message double-counts it) and falls through to the
authoritative recompute, which reads the fresh measurement.

Why it was needed: the reconstruction it falls back on used to be keyed on the
native read landing — see the next section, which replaced that accounting
altogether. The measurement does not care how the file was read.

#### The reconstruction charges PawFlow's messages, never the read body

A cold start does not discard the agent's context. It renders it into
`initial_context.md` and hands the provider a path, so the same conversation
exists twice in the stored context: as messages, and as the body of the reads
that brought the file back. Exactly one of those may be charged.

Until beta.140 the *messages* were the copy that got dropped:
`_context_messages` returned `[]` until the provider was seen reading the file,
and `_strip_for_count` then zeroed everything before that read's boundary
marker, charging the read output instead. Two consequences, both structural:

- The number depended on a native read landing. Before it, a full window read
  0%. That is also why the reconstruction was useless to any provider that
  only reports its usage when a turn ends (`claude-code`, `codex-app-server`)
  or that cannot report mid-turn at all (`gemini` ACP, whose prompt count
  arrives in the `session/prompt` JSON-RPC *result*).
- The boundary marker is set on the FIRST read only. A file past the native
  read ceiling is paginated, and every later page was charged as if it were
  fresh context.

It is now the other way round. PawFlow's messages are always charged — they are
the one account that exists on every provider, at every moment, without anyone
reporting anything — and the read bodies never are. `_strip_for_count` zeroes a
message it recognizes as a bootstrap body, by either of two signals:

- `source.context_usage_bootstrap_body`, stamped on the result when the turn
  produces it (`tasks/ai/_alc_closures2.py`). This is the only signal available
  to `context_usage_append_delta`, which sees one message and has no list to
  correlate a `tool_call_id` against.
- membership in `_bootstrap_body_call_ids`, derived from the calls' own
  arguments over the whole list. Deliberately not gated on "first read seen",
  so every page of a paginated read is covered, and it still answers for
  contexts written before the flag existed.

The suffix delta derives that id set from the FULL list, not the suffix: a read
result can land in the suffix while the assistant call identifying it sits
before the cache boundary. `_CONTEXT_ACCOUNTING_VERSION` moved to 4 — every
cached `used` written under 3 describes a different quantity, and the cache
params carry the version so those entries invalidate rather than mix.

What this number is: the true size of the context PawFlow holds and will
serialize, plus the injected read command as overhead. It is not the provider's
window, which also holds a system prompt and tool schemas PawFlow cannot
enumerate, and which on a paginated bootstrap holds only the pages actually
read. It errs high, which is the safe direction for a gauge that drives
auto-compaction at 95%: under-reporting means never compacting and letting the
CLI drop context silently. When a provider does report a measurement, that
still wins (`context_source_measured`).

#### Compaction drops the same bodies

The context builder has always excluded bootstrap reads from what it
re-serializes (`_cli_context_before_latest_text`, `drop_bootstrap_calls`).
Compaction did not, and it is the other surface that handles the whole stored
context: it was handed the read bodies *and* the messages they are a copy of,
and asked to summarize both — a duplicate that grew by one layer on every cold
start. Phase 0 of `_compact` (`tasks/ai/_agent_compact_core.py`) now applies
the same three helpers, and drops an assistant message left with neither text
nor a surviving call.

`record_observed_cli_context` itself lives on `LLMCliSharedMixin`
(`core/llm_providers/cli_shared.py`): recording a measured prompt size is
provider-neutral, and one implementation keeps every provider on a single
authoritative number. Two copies is how one of them drifts — the Codex mixin
used to define its own, so the gauge depended on which mixin won the MRO.

#### Where each provider's measurement comes from

A provider that can measure its own prompt records it; one that cannot keeps
the reconstruction. `record_observed_wire_usage` sums `input_tokens +
cache_read_input_tokens + cache_creation_input_tokens` for the proxied ones,
and `record_observed_cli_window` stores a native denominator when the provider
publishes one.

| Provider | Source of the measurement |
|---|---|
| `codex-interactive` | rollout `token_count` (`info.last_token_usage.input_tokens`) + `model_context_window` |
| `codex-app-server` | the same rollout — app-server writes it too, so `codex_rollout_context_usage` is reused verbatim, window included |
| `claude-code` | `result.usage` of the stream-json result event |
| `claude-code-interactive` | `message_start.usage` seen by the MITM proxy, turn path and interrupt path |
| `antigravity-interactive` | observer usage, where Gemini's `promptTokenCount` is normalized to `input_tokens` |
| `gemini` (ACP) | `meta.quota.token_count.promptTokenCount`, else `totalTokenCount − candidatesTokenCount` |
| API providers (openai, anthropic, …) | none needed: PawFlow builds the payload, so counting messages + system prompt + tool defs *is* the measurement |

A measurement that is absent records nothing rather than a 0: a stored 0 is
indistinguishable from "measured an empty window" for every consumer, and would
pin the gauge at 0% instead of letting the reconstruction answer.

Before this, `codex-app-server` and `gemini` divided a *character estimate* of
what PawFlow sent by a configured window, seeing neither the provider's system
prompt nor the session history it resumed — the estimate was structurally
unable to be right, however carefully it was computed.

#### A measured gauge is never advanced by counting

The measurement is the numerator, and it is complete: `input_tokens` is Codex's
whole prompt, including the message PawFlow is about to append. The streaming
hot path nevertheless advanced the cached gauge by adding PawFlow's own token
count for each appended message (`context_usage_append_delta`). On a measured
cache that double-counts, and it compounds: the gauge climbed for a whole turn
— observed going from 62% to 92% with no compaction of any kind — until the
next full recompute put the measurement back and it resumed growing correctly.

The drift was not only cosmetic. `_alc_maybe_auto_compact_after_append` arms
`compact_threshold_pct` against that same cached `used`, so auto-compaction
fired early, on an inflated number.

Both incremental paths now refuse a cache carrying `context_source_measured`:
`context_usage_append_delta` returns `None` (the caller falls through to the
authoritative calculation) and `context_usage_from_cache` declines it as a base
for a suffix delta. **A measured gauge can only be moved by a new measurement.**

#### The window the measurement is divided by

The rollout's `model_context_window` supplies the native denominator. PawFlow's
configured `max_context_size` can still impose a lower operational cap through
`effective_context_window`.

Older Codex versions printed `context left 74%` in the TUI. That derivation is
retained only as a compatibility fallback when native rollout telemetry is not
available:

- `context_left_fraction` / `derive_context_window`
  (`core/codex_interactive_pool.py`) parse the status bar and compute
  `window = used / (1 - left)`.
- A reading below 15% occupancy is refused: the TUI rounds to a whole percent,
  so at 5% used half a point of rounding moves the result by 10%. A derivation
  within 5% of the stored one is also refused — a denominator that breathes
  turn to turn is the very defect this area exists to remove.
- `record_codex_context_window` samples the pane once per turn only for that
  fallback and stores the result
  on `_cli_observed_context_window_by_stream`, shared with call clones like the
  token counts.
- `_client_real_window` (`tasks/ai/context_usage.py`) is the single lookup for
  a provider-reported window, used whether or not a turn is running. It checks
  the Codex map first, then `_cc_context_window_by_stream` (Claude Code's own
  `modelUsage[model].contextWindow`).

That last point closed a separate defect. The Claude Code map used to be read
**only** while a turn was active; between turns the code reached for
`client._real_context_size` / `client._context_window`, attributes PawFlow
assigns nowhere, so it always resolved to 0. The denominator was therefore
`min(configured, real)` during a turn and plain `configured` after it — the
gauge moved at the turn boundary with nothing at all behind the move. The same
dead lookup also meant `_agentctx_p3` resolved the turn budget, and
`context_ops` its cap, without ever applying the provider's real window.

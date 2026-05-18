# Claude Code Interactive Provider

`claude-code-interactive` is a provider that drives Claude Code in
interactive tmux mode while reading model output from a transparent local MITM
proxy. The provider does not read Claude Code transcripts or terminal output.

## Runtime Shape

- PawFlow starts one persistent Docker container per user, conversation, agent,
  and LLM service.
- The container maps `api.anthropic.com` to `127.0.0.1`.
- A root-owned local TLS proxy listens on port `443` and forwards requests to
  the real Anthropic endpoint with SNI `api.anthropic.com`.
- Claude Code runs as user `pawflow` inside tmux.
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
- `PAWFLOW_CCI_IDLE_TTL_SECONDS` controls idle container eviction. Default:
  `1800` seconds. A service request timeout can only extend this process-wide
  TTL, never shorten an explicitly configured or already larger value.

The provider assembles responses from those events:

- `content_block_delta` text deltas stream to the UI immediately and are
  persisted as assistant messages when the corresponding content block stops.
- `thinking_delta` streams to the thinking UI immediately and is persisted on
  the flushed assistant block.
- `signature_delta` inside a thinking block produces a redacted "Thought for"
  placeholder when Anthropic exposes only a signed thinking block.
- `tool_use` blocks and `input_json_delta` are emitted as live observed tool
  events for display/persistence only. PawFlow never re-executes them; Claude
  Code already ran those tools inside its own session.
- Bootstrap/discovery native tools are hidden from the PawFlow transcript:
  `GetSchema`, `ToolSearch`, compatible schema-list aliases, and Claude Code's
  `Read` of `.pawflow_cci/initial_context.md`.
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
latest turn delta and any current attachment references.

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
regular `claude-code` provider. The interactive tmux launcher also mirrors the
regular provider's private mount namespace: it bind-mounts `/cc_sessions/<user>`
over `/cc_sessions`, then starts Claude Code with `HOME` and
`CLAUDE_CONFIG_DIR` set to `/cc_sessions/<conversation>/<agent>`. This keeps the
credential, MCP config, prompt file, and attachment paths identical to the
working `claude-code` execution path. Before launch, PawFlow also writes the
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
screenful.

## Vision

User image attachments are materialized into `.pawflow_vision/` in the session
workdir and referenced in the pasted prompt with `@/cc_sessions/.../image.png`.
This uses Claude Code's native interactive file read path.

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

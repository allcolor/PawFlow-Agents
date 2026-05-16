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

For transport debugging, the proxy also emits `wire` events for raw socket
chunks received and sent on both directions (`client_to_upstream` and
`upstream_to_client`). By default this dump is limited to model endpoints
(`/v1/messages` and `/v1/complete`) so Claude Code telemetry batches do not bury
the useful traffic. Set `PAWFLOW_CCI_PROXY_WIRE_LOG_ALL=1` or override
`PAWFLOW_CCI_PROXY_WIRE_LOG_PATHS` to inspect additional paths. These events are
logged by the event service with the complete sanitized bytes as base64 plus
size, SHA-256, and UTF-8 `repr`; they are not queued for the provider. Sensitive
HTTP headers such as `Authorization`, `Cookie`, `Set-Cookie`, and API-key headers
are redacted in the proxy and redacted again by the server before logging. Set
`PAWFLOW_CCI_PROXY_WIRE_LOG=0` in the proxy environment to disable this verbose
wire dump.

The proxy parses HTTP keep-alive traffic as a sequence of request/response
exchanges on the same TLS socket. Each exchange receives its own request id so a
Claude Code startup probe cannot be confused with the real model turn. The known
quota probe (`/v1/messages` with `max_tokens: 1` and user content `quota`) is
observed for diagnostics but its response body is ignored. If Anthropic compresses
an observed response (`gzip` or `deflate`), only the side-channel copy is decoded
before SSE/JSON parsing; the proxied bytes sent back to Claude Code remain
unchanged.

The provider assembles responses from those events:

- `content_block_delta` text deltas become assistant text.
- `thinking_delta` becomes hidden thinking.
- `tool_use` blocks and `input_json_delta` are emitted as live observed tool
  events for display/persistence only. PawFlow never re-executes them; Claude
  Code already ran those tools inside its own session.
- Outgoing `/v1/messages` request bodies are observed for `tool_result` blocks
  so tool results can appear live as soon as Claude Code sends them back to the
  model.
- `message_delta.usage` updates token usage.
- Claude Code command hooks publish `Stop`, `StopFailure`, `PreCompact`,
  `PostCompact`, and `SessionEnd` lifecycle events over the same WebSocket.
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
not receive the full context again; PawFlow sends only the latest turn delta.

Live interrupt sends `Escape`, then pastes the interrupt message, then sends
`Enter`. Force stop sends `Escape Escape` to the tmux session and leaves the
container lifecycle intact.

OAuth credentials use the same session-local `.credentials.json` path as the
regular `claude-code` provider. The interactive tmux launcher also mirrors the
regular provider's private mount namespace: it bind-mounts `/cc_sessions/<user>`
over `/cc_sessions`, then starts Claude Code with `HOME` and
`CLAUDE_CONFIG_DIR` set to `/cc_sessions/<conversation>/<agent>`. This keeps the
credential, MCP config, prompt file, and attachment paths identical to the
working `claude-code` execution path. Before launch, PawFlow also writes the
session-local Claude settings that mark onboarding complete, trust the generated
session workdir, approve the PawFlow MCP server from `.mcp.json`, and accept
bypass-permissions mode inside the isolated container. This prevents first-run
interactive prompts from consuming the pasted PawFlow prompt.

## Live Debugging

The chat UI action menu exposes `CC Interactive Tmux` for the selected agent. It
opens the existing terminal tab UI and starts a `docker exec -i` bridge into the
provider-owned Docker container. The bridge creates the PTY inside that Linux
container, then runs `tmux attach-session -t pawflow`. This is a live debug view
of the same tmux session receiving prompts, interrupts, and force-stop keys;
model output is still assembled only from MITM-observed response events.

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

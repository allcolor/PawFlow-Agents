# Claude Code Interactive Provider

`claude-code-interactive` is an experimental provider that drives Claude Code in
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

## TLS Material

PawFlow creates a local CA once under `data/system` and generates a per-session
leaf certificate for `api.anthropic.com`. Only the public CA certificate and the
leaf certificate/key are mounted into the session container. The CA private key
is never mounted.

## Event Flow

The proxy forwards HTTP request bodies byte-for-byte. It only rewrites
hop-by-hop headers and the upstream `Host` value. While streaming the upstream
response back to Claude Code, it parses a copy of Anthropic SSE chunks and sends
scrubbed events to PawFlow over `/ws/cc-interactive/events/<service_id>`.

The provider assembles responses from those events:

- `content_block_delta` text deltas become assistant text.
- `thinking_delta` becomes hidden thinking.
- `tool_use` blocks and `input_json_delta` become `LLMToolCall` objects.
- `message_delta.usage` updates token usage.
- Claude Code command hooks publish `Stop`, `StopFailure`, `PreCompact`,
  `PostCompact`, and `SessionEnd` lifecycle events over the same WebSocket.
- A `Stop` hook can close a turn when the lifecycle completes; response content
  still comes only from MITM-observed SSE deltas.

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

## Vision

User image attachments are materialized into `.pawflow_vision/` in the session
workdir and referenced in the pasted prompt with `@/cc_sessions/.../image.png`.
This uses Claude Code's native interactive file read path.

Tool-side image reads use PawFlow's multimodal marker contract:

- `see()` on an image returns `__image_data__:<mime>:<base64>`.
- `read()` on an image now does the same for FileStore, workdir, and relay
  filesystem reads.
- `ToolRelayService` converts that marker into native MCP image content.

## Activation

The provider is gated while experimental. Use either:

```json
{
  "provider": "claude-code-interactive",
  "experimental": true
}
```

or set:

```bash
PAWFLOW_CC_INTERACTIVE_ENABLED=1
```

## Current Limitations

The first implementation covers MITM event assembly, persistent tmux input, MCP
tool calls, image materialization, and lifecycle hooks. It still requires a live
Claude subscription session and has not been exercised here against a real
Claude Code Docker session.

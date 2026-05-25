# Antigravity Interactive Provider

PawFlow supports Google Antigravity CLI (`agy`) as an interactive LLM provider:

- `antigravity-interactive`: a full LLM provider that drives the real `agy`
  tmux session and streams normalized proxy events into PawFlow.

It starts `agy` in a tmux session with:

- Gemini OAuth credentials from the existing Gemini credential pool.
- PawFlow MCP configuration under the session `HOME` using Antigravity's
  documented `~/.gemini/antigravity/mcp_config.json` file.
- A local TLS MITM observer for `daily-cloudcode-pa.googleapis.com:443`.
- A browser xterm tab attached to the live provider tmux session through the
  same **Agent Tmux** action used by Claude Code interactive agents.

The provider injects prompts through tmux. On cold start it writes the full
PawFlow context to `.pawflow_ag/initial_context.md` and sends an `@file` prompt,
matching the Claude Code interactive pattern. PawFlow pastes the complete tmux
prompt buffer atomically and then sends a single final `Enter`; it must not
replay compact/bootstrap prompts line by line. When the tmux session is still
alive, follow-up turns send only the latest user text plus catch-up context.
Output is parsed from the observer JSONL, not from terminal text.

The network proxy is an internal provider implementation detail. It writes the
same JSONL format consumed by the provider, including prompts detected in
Antigravity request bodies and normalized model/tool deltas. Real
`antigravity-interactive` provider turns suspend manual log ingestion while they
consume the same log, so provider-driven turns are not double-written. Some
Antigravity SSE responses stop after the last text delta without a final
`finishReason`; PawFlow flushes such turns after a short idle drain so
terminal-visible answers still appear in chat. Antigravity also emits
`finishReason=STOP` after internal tool steps; PawFlow treats those as
intermediate stops while waiting for the follow-up model text.
If a user manually presses `Escape` in the attached tmux session, Antigravity
returns to an interrupted prompt without emitting a final network event. PawFlow
polls the tmux pane for that interrupted prompt and closes the provider turn so
Active Agents is cleared without requiring the UI Stop button.

Unlike Claude Code interactive, Antigravity does not provide a verified
`UserPromptSubmit` hook surface in the settings files PawFlow writes for `agy`.
Manual prompts are therefore detected from observed Antigravity request bodies.
Prompts pasted by PawFlow itself are marked before tmux submission and ignored by
the manual-ingest path, either by exact prompt hash or by the same pending-ignore
fallback used when the observed request text differs from the submitted tmux
prompt.

## Provider

Configure an LLM service with provider `antigravity-interactive`. It reuses the
Gemini OAuth credential pool through `credential_service_id` and starts one
persistent container/tmux session per `(user, conversation, agent, service)`.

The provider handles:

- Assistant text deltas from Antigravity SSE responses.
- Thinking deltas when the upstream response marks thought/reasoning content.
- Tool call and tool result events from the same normalized MITM protocol used
  by Claude Code interactive: `request_start`, `tool_use`, `tool_result`, and a
  synthetic `hook` `Stop` when Antigravity finishes a stream. The AGY proxy reads
  Antigravity/Gemini-specific inputs and adapts them to that protocol; the
  provider does not tail local AGY transcript files as a second live source.
- Tool origin metadata on observed calls: `call_mcp_tool` is normalized to the
  qualified `server/tool` display name and marked `tool_origin=mcp`; other
  Antigravity function calls are marked `tool_origin=native`.
- Live preempt and force stop through tmux key injection.
- Fresh cold sessions whenever the tracked tmux/container is missing.
- Session invalidation after compact, edit, or branch changes kills and evicts
  matching live `agy` containers with `SIGKILL` before removal, so the next
  provider turn cold-starts from the compacted PawFlow context instead of
  reusing Antigravity's stale internal conversation.

## UI

Open the chat action menu and select **Agent Tmux**. PawFlow attaches to the
already-running tmux session for the selected interactive agent. The button never
creates an Antigravity session; provider turns are the only code path that starts
or restarts `agy`.

Live and replayed tool rows show a compact `MCP` or `Native` badge when the
provider supplies `tool_origin`, matching the Codex app-server distinction
between provider-native tool calls and PawFlow MCP tool calls.

The internal proxy log path is typically under:

```text
data/runtime/sessions/antigravity-observer/<user>/<conversation>/<agent>/.pawflow_ag/logs/
```

## Network Observer

`tools/ag_observer_proxy.py` terminates local TLS for
`daily-cloudcode-pa.googleapis.com`, opens a second TLS connection to the real
upstream, and forwards bytes unchanged. The upstream connect timeout is cleared
after TLS setup so long-running SSE model responses are not cut off while idle.
It logs connection metadata, HTTP/1 messages, HTTP/2 events, gRPC message
envelopes, normalized `ag_user_prompt` events for user request text, normalized
`ag_text_delta` events for model text/thinking, and the standard live tool
events consumed by interactive providers: `request_start`, `tool_use`,
`tool_result`, and synthetic `hook` `Stop`.

Set `PAWFLOW_AG_OBSERVER_LOG_B64=1` to include base64 payload samples. This is
off by default because payloads may contain private prompts, tool arguments, or
model output.

## Configuration

- `PAWFLOW_ANTIGRAVITY_IMAGE`: Docker image used for observer sessions.
  Defaults to `PAWFLOW_GEMINI_IMAGE`, then `pawflow-claude-code:latest`.
- `PAWFLOW_ANTIGRAVITY_BIN`: CLI binary to run inside tmux. Defaults to `agy`.
- `PAWFLOW_AG_OBSERVER_LOG_B64`: include base64 payload samples in logs.
- `PAWFLOW_AG_OBSERVER_MAX_B64_BYTES`: max bytes per base64 sample.

The shared CLI image installs the `agy` binary in `/usr/local/bin` and includes
the `h2` package so rebuilt images can decode HTTP/2 headers in the observer
proxy.

## MCP Wiring

Antigravity and Gemini CLI builds do not all read the same MCP file. PawFlow
writes the server into `~/.gemini/settings.json` under `mcpServers`, and also
writes compatibility files for builds that look at separate MCP config paths:

```text
~/.gemini/mcp_config.json
~/.gemini/antigravity/mcp_config.json
~/.gemini/antigravity-cli/mcp_config.json
.agents/mcp_config.json
```

The Gemini-compatible files contain a top-level `mcpServers` object:

```json
{
  "mcpServers": {
    "pawflow": {
      "command": "/usr/bin/python3",
      "type": "stdio",
      "args": ["/opt/pawflow/mcp_bridge.py"],
      "cwd": "/cc_sessions/<conversation>/<agent>",
      "env": {},
      "timeout": 15000,
      "trust": true
    }
  }
}
```

The Antigravity-specific files use the Jetski customization shape instead:

```json
{
  "mcpServers": [
    {
      "serverName": "pawflow",
      "command": "/usr/bin/python3",
      "type": "stdio",
      "args": ["/opt/pawflow/mcp_bridge.py"],
      "cwd": "/cc_sessions/<conversation>/<agent>",
      "env": {},
      "timeout": 15000,
      "trust": true,
      "disabled": false
    }
  ]
}
```

PawFlow also duplicates `mcpServers`, `allowMCPServers`, `mcp.allowed`, and
permissions into `~/.gemini/antigravity-cli/settings.json`, because Antigravity
CLI builds load that settings file separately from Gemini's root settings. The
workspace `.agents/mcp_config.json` is required for the active agent context;
without it, `/mcp` can show `pawflow` as connected while planner steps reject
the server as not allowed in this context. The `agy` process is launched with
`GEMINI_CLI_HOME` pointing at the isolated session root and
`CASCADE_ENABLE_MCP_TOOLS=true` so Antigravity resolves config under the
conversation workdir and enables MCP plugin tooling. After the tmux session is
created, PawFlow primes Antigravity's MCP menu once (`/mcp`, restart selected
server, escape) because Antigravity CLI 1.0.x can show a server as connected
before the planner's model request includes those MCP tools.

The session settings also allow `mcp(pawflow/*)`, `mcp_pawflow_*`, and `mcp_*`
so PawFlow tools can run without an Antigravity approval prompt.

## Terminal Sizing

The web terminal fits xterm to the active tab and sends the measured `cols` and
`rows` back to the server. For server-side tmux attachments, PawFlow also sends a
`tmux resize-window` command so `agy` and the browser tab share the same size.

# PawFlow MCP Client Installer

The PawFlow MCP client package connects one local client instance to one
published PawFlow conversation and agent through a local stdio bridge. It
provides isolated launchers for Claude Code, Codex, Agy/Gemini, OpenCode,
JCode, Pi, and Hermes, plus configuration fragments for any MCP-compatible
stdio client. The bridge also
registers the selected project directory as that conversation's CLI relay
without changing the conversation's default relay.

## Requirements

- Python 3.10 or newer.
- A published conversation URL ending in `/mcp/srv_...`.
- A PawFlow MCP API key created for that publication.
- The Private Gateway key when the PawFlow deployment uses the gateway.
- At least one supported harness already installed on the client workstation:
  Claude Code, Codex, Agy/Gemini, OpenCode, JCode, Pi, or Hermes.

A publication accepts one active client instance. Create another published
conversation when two client instances must run at the same time.

## Download

Each PawFlow release provides two universal archives with identical contents:

- `pawflow-mcp-client-VERSION.zip`
- `pawflow-mcp-client-VERSION.tar.gz`

The package contains only Python source, so either archive works on Windows,
Linux, and macOS. Download the ZIP on Windows or either format on Linux/macOS.

## Guided installation

Extract the archive before running an installer.

### Windows

Command Prompt:

```bat
install.cmd
```

PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

### Linux

```sh
tar -xzf pawflow-mcp-client-VERSION.tar.gz
cd pawflow-mcp-client-VERSION
./install.sh
```

### macOS

```sh
tar -xzf pawflow-mcp-client-VERSION.tar.gz
cd pawflow-mcp-client-VERSION
./install.sh
```

The wizard asks for:

1. The local MCP server name, normally `pawflow`.
2. The published PawFlow MCP URL copied from the conversation Resources panel.
3. The publication API key. Input is hidden.
4. The optional Private Gateway key. Input is hidden.
5. The local project directory shared through the relay.
6. The clients to configure: `cc`, `codex`, `agy`, `opencode`, `jcode`,
   `pi`, `hermes`, or a comma-separated subset.
7. Whether the relay is read-only.
8. Whether shell execution is allowed.
9. Whether explicitly approved FRP service tunnels are allowed.

The default relay is read/write with shell execution disabled. File edits are
therefore available, while commands remain denied until the operator explicitly
enables them.

FRP service tunnels are opt-in. When enabled, the installer downloads the
platform-specific official `frpc` binary, verifies its pinned SHA-256 checksum,
and stores it inside the private MCP runtime. The automatic MCP relay then
advertises the existing `allow_service_tunnels` capability. Local services
still require explicit approval in the relay service catalogue.

The installer prints one launch command for every selected client. Always start
that instance with the printed command. Starting the client normally does not
activate the PawFlow session.

## Installed files

The runtime is installed per user:

| Platform | Default runtime directory |
|---|---|
| Windows | `%LOCALAPPDATA%\PawFlow\MCP` |
| macOS | `~/Library/Application Support/PawFlow/MCP` |
| Linux | `$XDG_DATA_HOME/pawflow/mcp` or `~/.local/share/pawflow/mcp` |

Secrets are written once to `sessions/NAME/profile.json` under that runtime. On
POSIX systems the installer applies mode `0600` to the profile and `0700` to
its directory. Generated MCP configurations contain only the Python executable,
launcher path, private profile path, and project directory; they do not duplicate
API or gateway keys.

Each server name creates one isolated session directory:

| File | Purpose |
|---|---|
| `session.json` | Non-secret manifest used by the client launcher. |
| `profile.json` | Private endpoint, API keys, relay directory, and permissions. |
| `claude.mcp.json` | Strict per-invocation Claude Code MCP configuration. |
| `agy-home/` | Isolated Agy/Gemini home and MCP configuration. |
| `mcp.json` | Generic `{"mcpServers": {...}}` configuration. |
| `entry.json` | One generic stdio server entry for clients with their own envelope format. |
| `claude.settings.json` | Session-only Claude lifecycle hooks. |
| `codex-home/hooks.json` | Session-only Codex lifecycle hooks and approval state. |
| `opencode-home/` | Isolated OpenCode config and conversation plugin. |
| `jcode-home/` | Isolated JCode MCP config, lifecycle hooks, and sessions. |
| `pi-home/extensions/pawflow.js` | Pi extension providing PawFlow tools and conversation synchronization. |
| `hermes-home/` | Isolated Hermes MCP config and PawFlow plugin. |
| `hook-state-CLIENT.json` | Locked per-client conversation cursor. |
| `injected-prompts.jsonl` | Short-lived IDs and hashes used to suppress webchat prompt mirroring. |

The installer never reads or writes the user's global configuration for any
supported harness. Re-running the same server name replaces only that PawFlow
session bundle. Use a different name for a different published conversation or
agent.

## Launch one isolated instance

The generated launcher enforces one session bundle per local process:

- **Claude Code:** launches `claude --mcp-config SESSION/claude.mcp.json
  --strict-mcp-config --settings SESSION/claude.settings.json`, so global MCP
  servers and hooks are excluded from that instance.
- **Codex:** launches with `-C PROJECT` and a per-invocation `-c
  mcp_servers=...` override containing only the selected PawFlow publication.
  `CODEX_HOME` points to `SESSION/codex-home`, so generated hooks cannot affect
  another Codex instance. Complete login and approve the hooks with `/hooks`
  once inside this isolated home when requested.
- **Agy/Gemini:** launches with `HOME` and `USERPROFILE` set to
  `SESSION/agy-home`, isolating both its MCP settings and client state. Complete
  the client login once inside that isolated home when required.
- **OpenCode:** launches the project with `OPENCODE_CONFIG` and
  `OPENCODE_CONFIG_DIR` pointing to `SESSION/opencode-home`. Its native local
  MCP entry provides the PawFlow tools; the generated plugin synchronizes
  prompts, context deltas, and final assistant messages.
- **JCode:** launches with `JCODE_HOME=SESSION/jcode-home`. JCode loads its
  native stdio MCP entry from that home. Observer hooks mirror local prompts and
  final assistant messages from the isolated JCode session file. Its isolated
  `prompt-overlay.md` directs the model to fetch bootstrap and incremental
  context through the native PawFlow conversation tools. JCode observer hooks
  cannot modify an in-flight model request, so this model-directed tool call is
  the closest native equivalent to the mutable pre-model hooks of other clients.
- **Pi:** launches with `PI_CODING_AGENT_DIR=SESSION/pi-home` and the generated
  extension explicitly loaded. Because Pi intentionally has no built-in MCP
  client, the extension discovers the published PawFlow tools and registers
  equivalent native Pi tools. It also injects bootstrap/delta context and mirrors
  prompts and final assistant messages.
- **Hermes:** launches with `HERMES_HOME=SESSION/hermes-home`. Its native MCP
  configuration exposes the PawFlow tools; its generated plugin injects
  bootstrap/delta context at `pre_llm_call` and persists the completed turn at
  `post_llm_call`.

Arguments after the launch command are forwarded to the underlying client. For
example, append `-- --model sonnet` to the generated Claude Code command.

For another MCP-compatible client, create a dedicated client profile or
per-invocation configuration and point it at `SESSION/mcp.json`. If the client
expects only one server object rather than an `mcpServers` map, use
`SESSION/entry.json`. Do not merge that file into a shared global profile:
one running local instance must load exactly one session configuration, and that
session maps to exactly one PawFlow conversation and agent.

### Persistent terminal and webchat routing

The generated launch command owns the terminal rather than starting a throwaway
client process:

- Linux and macOS create or reattach a deterministic `tmux` session. `tmux` is
  required and the client keeps running after the operator detaches.
- Windows keeps the client in the inherited host console and starts an
  authenticated loopback listener. PawFlow injects Unicode console input through
  that listener; its random secret stays in the child environment and is never
  returned by relay status APIs.

After the MCP stdio bridge connects its existing automatic relay, it registers
the terminal under the same client lease. A text prompt sent from webchat is
authorized, processed by `pre_user_message`, and persisted before it is injected
into the TUI. Immediately before `Enter`, the transport writes the prompt's
`message_id` and SHA-256 marker. The prompt hook consumes that marker and does
not call `send_user_message` a second time.

An `external_mcp` agent never falls back to a PawFlow LLM. If its publication is
active but its terminal cannot be reached, webchat submission returns
`external_mcp_terminal_unavailable` with HTTP 503; the canonical user message
remains persisted for recovery.

### Conversation lifecycle hooks

The session-scoped hook program uses the four published conversation tools:

1. The first native pre-turn lifecycle event calls `get_initial_context` and
   injects its document as hidden context where the harness supports mutable
   pre-model hooks.
2. Before later turns, the hook calls `get_context_updates(after_seq)` under a
   local file lock. The cursor advances only after a successful response.
3. A prompt typed directly in the terminal is persisted with
   `send_user_message`. A server-injected webchat prompt is recognized by its
   marker and skipped.
4. The native completed-turn event persists the final assistant response. Agy
   and JCode read their isolated transcript/session because their hook payloads
   do not always contain the full response text.

Hook writes derive stable `message_id` values from client session/turn identity
and content. Server-side idempotence makes retries safe. Hook failures are
fail-open for the local TUI and never print profile contents or keys.

## Non-interactive installation

Automation supplies secrets through environment variables, never command-line
arguments:

```sh
export PAWFLOW_MCP_API_KEY='publication key'
export PAWFLOW_GATEWAY_KEY='gateway key'
./install.sh \
  --non-interactive \
  --name pawflow \
  --url https://pawflow.example/mcp/srv_example \
  --relay-dir /path/to/project \
  --clients cc,codex,agy,opencode,jcode,pi,hermes
```

Omit `PAWFLOW_GATEWAY_KEY` when the deployment has no Private Gateway. Add
`--readonly` or `--allow-exec` when required. `--install-dir`, `--home`,
and `--python` support managed or test installations. Add
`--allow-service-tunnels` to enable the verified FRP integration for the
automatic MCP relay.

## Verify the connection

1. Copy the session-bound launch command printed by the installer.
2. Start one client instance with that command in the configured project.
3. List MCP servers using the client's normal MCP status command and confirm
   that the selected PawFlow server is the only PawFlow publication loaded.
4. Call `pawflow_relay_status`. It reports the local process, published server,
   relay ID, permissions, and `auto_default: false` without returning secrets.
5. Call a read-only PawFlow tool, then test writes or shell execution only if
   those permissions were selected.

The bridge exposes four local lifecycle tools:
`pawflow_relay_connect`, `pawflow_relay_disconnect`,
`pawflow_relay_status`, and `pawflow_relay_reconnect`.

## Reconfigure or update

Run the installer again with the same server name. It replaces the bundled
runtime and that session's private profile and generated configurations. Global
client configuration remains untouched.

To change only the project directory or permissions, run the wizard again.
Close every active instance first so the publication lease can be released.

## Troubleshooting

- `mcp_server_already_in_use`: close the first client, revoke its session from
  the PawFlow publication dialog, or publish a second conversation.
- Client cannot start Python: install Python 3.10+ and rerun with `--python`
  pointing to the stable interpreter.
- Authentication failure: create a new publication API key and rerun the
  installer. Verify the Private Gateway key separately.
- Relay connection failure: confirm the shared directory exists locally and
  that outbound HTTPS/WebSocket traffic can reach PawFlow.
- Agy/Gemini asks for authentication: complete login inside the isolated
  session launched by PawFlow; credentials from the normal global home are
  intentionally not reused.
- Codex reports untrusted hooks: run `/hooks` in the isolated PawFlow Codex
  instance and approve the three generated lifecycle hooks.
- POSIX launcher reports that `tmux` is missing: install `tmux`, then rerun the
  same session-bound launch command.
- Webchat returns `external_mcp_terminal_unavailable`: reattach or restart the
  generated client command and confirm `pawflow_relay_status` reports both the
  relay and terminal as ready.
- A generic client also loads another PawFlow publication: use a dedicated
  profile or per-invocation config rather than merging `mcp.json` globally.

## Build release archives

From the PawFlow repository:

```sh
python scripts/build-mcp-client-installer.py
```

Artifacts are written to `dist/mcp-client-installers/`. The ZIP and tar.gz use
fixed timestamps, ownership, permissions, and sorted entries so the same source
and version produce byte-for-byte reproducible archives. The release-assets
workflow builds both universal packages once and publishes them with the other
PawFlow release downloads.

See [Published Conversation MCP Servers](PUBLISHED_MCP_SERVER.md) for endpoint,
identity, relay selection, lease, and revocation behavior.

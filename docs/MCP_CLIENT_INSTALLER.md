# PawFlow MCP Client Installer

The PawFlow MCP client package connects one local client instance to one
published PawFlow conversation and agent through a local stdio bridge. It
provides isolated launchers for Claude Code, Codex, and Agy/Gemini, plus
configuration fragments for any MCP-compatible stdio client. The bridge also
registers the selected project directory as that conversation's CLI relay
without changing the conversation's default relay.

## Requirements

- Python 3.10 or newer.
- A published conversation URL ending in `/mcp/srv_...`.
- A PawFlow MCP API key created for that publication.
- The Private Gateway key when the PawFlow deployment uses the gateway.
- Claude Code, Codex, Agy/Gemini, or another MCP-compatible client already
  installed on the client workstation.

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
6. The clients to configure: `cc`, `codex`, `agy`, or a comma-separated subset.
7. Whether the relay is read-only.
8. Whether shell execution is allowed.

The default relay is read/write with shell execution disabled. File edits are
therefore available, while commands remain denied until the operator explicitly
enables them.

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

The installer never reads or writes the user's global Claude Code, Codex, Agy,
or Gemini configuration. Re-running the same server name replaces only that
PawFlow session bundle. Use a different name for a different published
conversation or agent.

## Launch one isolated instance

The generated launcher enforces one session bundle per local process:

- **Claude Code:** launches `claude --mcp-config SESSION/claude.mcp.json
  --strict-mcp-config`, so global MCP servers are excluded from that instance.
- **Codex:** launches with `-C PROJECT` and a per-invocation `-c
  mcp_servers=...` override containing only the selected PawFlow publication.
- **Agy/Gemini:** launches with `HOME` and `USERPROFILE` set to
  `SESSION/agy-home`, isolating both its MCP settings and client state. Complete
  the client login once inside that isolated home when required.

Arguments after the launch command are forwarded to the underlying client. For
example, append `-- --model sonnet` to the generated Claude Code command.

For another MCP-compatible client, create a dedicated client profile or
per-invocation configuration and point it at `SESSION/mcp.json`. If the client
expects only one server object rather than an `mcpServers` map, use
`SESSION/entry.json`. Do not merge that file into a shared global profile:
one running local instance must load exactly one session configuration, and that
session maps to exactly one PawFlow conversation and agent.

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
  --clients cc,codex,agy
```

Omit `PAWFLOW_GATEWAY_KEY` when the deployment has no Private Gateway. Add
`--readonly` or `--allow-exec` when required. `--install-dir`, `--home`,
and `--python` support managed or test installations.

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

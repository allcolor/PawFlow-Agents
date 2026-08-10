# PawFlow MCP Client Installer

The PawFlow MCP client package connects Claude Code, Codex, or Agy to one
published PawFlow conversation through a local stdio bridge. The bridge also
registers the selected project directory as that conversation's CLI relay
without changing the conversation's default relay.

## Requirements

- Python 3.10 or newer.
- A published conversation URL ending in `/mcp/srv_...`.
- A PawFlow MCP API key created for that publication.
- The Private Gateway key when the PawFlow deployment uses the gateway.
- Claude Code, Codex, or Agy already installed on the client workstation.

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

Restart every configured client after installation. The MCP server appears
under the name chosen in the wizard.

## Installed files

The runtime is installed per user:

| Platform | Default runtime directory |
|---|---|
| Windows | `%LOCALAPPDATA%\PawFlow\MCP` |
| macOS | `~/Library/Application Support/PawFlow/MCP` |
| Linux | `$XDG_DATA_HOME/pawflow/mcp` or `~/.local/share/pawflow/mcp` |

Secrets are written once to `profiles/NAME.json` under that runtime. On
POSIX systems the installer applies mode `0600` to the profile and `0700` to
its directory. MCP client configurations contain only the Python executable,
launcher path, profile path, and project directory; they do not duplicate API
or gateway keys.

The installer updates these user configurations:

| Client | Configuration |
|---|---|
| Claude Code (`cc`) | `~/.claude.json`, `mcpServers.NAME` |
| Codex | `~/.codex/config.toml`, a marked `mcp_servers.NAME` block |
| Agy | `~/.gemini/antigravity-cli/settings.json` and `mcp_config.json`, plus Gemini-compatible user files |

Existing JSON properties and unrelated MCP servers are preserved. Changed
configuration files receive timestamped `.bak-*` copies. Re-running the same
installation is idempotent and does not create backups when content is
unchanged. The Codex block is bracketed by PawFlow markers; an existing
unmanaged table with the same name must be renamed or removed first to avoid
ambiguous TOML.

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

1. Restart the client.
2. List MCP servers using the client's normal MCP status command.
3. Start one client instance in the configured project.
4. Call `pawflow_relay_status`. It reports the local process, published server,
   relay ID, permissions, and `auto_default: false` without returning secrets.
5. Call a read-only PawFlow tool, then test writes or shell execution only if
   those permissions were selected.

The bridge exposes four local lifecycle tools:
`pawflow_relay_connect`, `pawflow_relay_disconnect`,
`pawflow_relay_status`, and `pawflow_relay_reconnect`.

## Reconfigure or update

Run the installer again with the same server name. It replaces the bundled
runtime and updates the private profile and selected client entries. Unrelated
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
- Codex duplicate table error: remove or rename the unmanaged
  `[mcp_servers.NAME]` table, then reinstall.
- Invalid JSON configuration: restore the timestamped backup or fix the JSON;
  the installer never overwrites a file it cannot parse.

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

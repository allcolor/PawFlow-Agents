# PawFlow Relay Client

PawFlow relay lifecycle is separate from PawFlow clients.

- Webchat, PawCode, VS Code, and API clients open conversations and send messages.
- Server relays are created and started from the webchat resource panel.
- Client relays are started by the standalone PawFlow Relay client on the machine that owns the files or desktop.

This separation keeps PawCode and the VS Code extension equivalent to the webchat: they do not create, start, stop, or own relays.

Server relays do not ask the user for a filesystem path. PawFlow allocates their
workspace from the relay scope and mounts that directory into the relay
container at `/workspace`:

- global scope: `data/runtime/relay/global`
- user scope: `data/runtime/relay/<user_id>`
- conversation scope: `data/runtime/relay/<user_id>/<conversation_id>`

Only one managed server relay is allowed for each global/user/conversation
workspace scope. Relay service ids are also unique across scopes because the
reverse WebSocket route is global (`/ws/relay/<service_id>`). Managed server
relays cannot be moved between scopes; create a new relay in the target scope
and uninstall the old relay explicitly when its workspace is no longer needed.

Server-side relay sessions track in-flight reverse filesystem requests per WebSocket connection. When a relay disconnects or is removed from the pool, those pending request tasks are cancelled so stale connections cannot retain writers, loops, or queued FUSE work.

## Admin-controlled server-local execution

Managed server relays execute in their own isolated workspace by default. An
administrator can open **Server settings → Server Relays** and enable
server-local execution for one managed relay. When enabled, filesystem and shell
tools sent to that relay with `local=true` run inside the PawFlow server
container instead of the relay container. This gives access to server logs and
to the Docker socket mounted in the PawFlow container.

The switch is disabled by default, persists with the relay service definition,
and applies immediately without restarting the relay. Only the dedicated admin
API may change it; normal service updates reject the internal
`server_local_exec` field. Standalone relays keep their existing semantics:
`local=true` uses their authenticated local host-helper path when configured.
The webchat Terminal and Desktop commands offer the same container/local mode
picker for an enabled managed relay. In that case, local means the PawFlow
server container; terminal I/O and the noVNC proxy remain bound to the normal
authenticated browser-session routes.

## A managed container that dies is respawned

The container of a managed server relay is started once, from
`RelayService.connect()`, and runs with `--rm`. Nothing else used to re-create
it: if it crashed, or an operator ran `docker rm -f pawflow-relay-srv-<id>`,
the transport kept retrying against a container that no longer existed and the
relay stayed down until the whole PawFlow server was restarted.

So when a request fails with a disconnect error and is about to be retried, the
service calls `ensure_managed_relay_alive()`:

- **Unmanaged relays are never touched.** An operator-run relay is theirs to
  restart; PawFlow owns no container for it.
- **A live WebSocket is the health signal.** A connected relay is left strictly
  alone, and a container that is actually gone is respawned immediately. A
  running container receives a 15-second grace period for the relay worker's own
  reconnect loop. PawFlow rechecks the WebSocket after Docker inspection and
  again immediately before replacement, so it cannot kill a relay that
  reconnected while recovery was being decided. Only a continuously
  disconnected running process past the grace is treated as wedged.
- **One respawn per cooldown window** (60 s), so a burst of failing tool calls
  asks for one container start rather than one per call.
- **Container-name recovery is idempotent.** Starts for the same deterministic
  Docker name are serialized. A healthy container started by an overlapping
  attempt is reused instead of replaced, while an explicit reconnect or a relay
  that stayed disconnected past its grace requests replacement. PawFlow verifies
  the spawned, server-id, and managed-relay labels before removal; an unlabelled
  container or one owned by another server is never touched.
- **A failed respawn is logged, not raised.** The caller is a transport retry
  that has its own error to report.

The retry window is five attempts, five seconds apart, which is usually enough
for the new container to come up and connect back. If it is not, the request
still fails — but the relay is on its way back, and the next tool call finds it
connected instead of needing a server restart.

An editable managed server relay also exposes **Reconnect** in its webchat relay
details dialog. This explicit action immediately replaces the disposable relay
container even when its WebSocket still appears connected. It never invokes
relay cleanup: the scoped workspace, home volume, service definition, and relay
bindings are preserved. Global relays require an administrator, and standalone
Relay Desktop clients do not expose this action because PawFlow does not own
their process lifecycle.

## CLI

The standalone relay client is exposed as `pawflow-relay` when installed from the Python package, or as `python -m pawflow_relay` from a checkout.

Add a server profile:

```bash
pawflow-relay server add prod https://pawflow.example:PORT --gateway-key RoyBatty
```

Login to the server:

```bash
pawflow-relay server login prod
```

Add a local workspace share:

```bash
pawflow-relay workspace add repo --server prod --path ~/src/project --mode rw
```

Start the relay:

```bash
pawflow-relay start repo
```

The legacy direct mode remains available for low-level scripting:

```bash
python -m pawflow_relay --server https://pawflow.example:PORT --dir ~/src/project
```

## Shell PATH

The shells the relay spawns for `bash` and friends are non-login and
non-interactive, so `.profile`, `.bashrc` and `/etc/environment` are never
read. To keep a tool the relay account installed for itself reachable, every
exec prepends the account's own bin directories to `PATH`:

1. `~/bin`
2. `~/.local/bin` (where pip/pipx put user installs)

Only directories that exist are added, and never twice — an environment that
already exports one of them keeps its own ordering. A `PATH` passed
explicitly in the request's `env` overrides this entirely: an explicit value
is an instruction, not a suggestion.

This is the only PATH manipulation the relay performs. Anything else must be
exported by whatever launches the relay process, since the exec environment
is inherited from it (`os.environ.copy()` in `tools/fs_exec.py`).

## Local State

The relay client stores server and workspace profiles outside the project tree:

- Linux/macOS: `~/.pawflow/relay/`
- Windows: `%APPDATA%\\PawFlow\\relay\\`
- Override: `PAWFLOW_RELAY_HOME=/custom/path`

Profiles are split into `servers.json` and `workspaces.json`. Gateway keys and session tokens are currently stored in this local profile; the desktop client should migrate secrets to the OS keychain before a stable release.

When a server profile has a Private Gateway key, relay HTTP lifecycle calls
(`/api/ui`) send it in `X-PawFlow-Gateway-Key`. The same key already protects
the relay WebSocket handshake. The challenge cookie remains supported for
browser and previously authenticated client sessions.

## Relay Desktop

The Electron Relay Desktop slice lives in `pawflow-relay-desktop/`. It uses the same local state as the CLI and manages:

- server profiles: URL, private gateway key, login status;
- workspace shares: path, read/write mode, relay image/profile, local execution
  permission, and opt-in FRP service-tunnel permission;
- running relay processes and logs;
- Docker relay images and custom image builds.

Enable **Allow service tunnels (FRP)** on each relay that may participate in a
tunnel, then restart that relay so it advertises the capability. Tunnel
creation and lifecycle controls are in the webchat **Resources → Service
Tunnels** section; Relay Desktop controls the local relay permission, not the
user-scoped tunnel records.

Stopping a relay from the desktop UI, or quitting the tray app, stops the
launcher process and also performs relay runtime cleanup: the registered relay
service is uninstalled best-effort and Docker containers whose names belong to
that workspace relay id are removed. This cleanup is independent from Python
signal handling so Windows process termination cannot leave the relay container
running after the desktop app exits.

Run it from a checkout:

```bash
cd pawflow-relay-desktop
npm install
npm start
```

Release builds use `pawflow-relay-desktop/npm run dist:<platform>`. The build
prepares the runtime payload, creates a PyInstaller relay executable under
`runtime/bin/`, and packages it with Electron Builder (`nsis`/`zip` on Windows,
`AppImage`/`deb` on Linux, `dmg`/`zip` on macOS). In packaged mode the desktop
app launches the embedded relay binary and uses the Python fallback only for
source checkouts.

Windows builds collect the complete `winpty` package, including its native DLLs
and helper executables. The release workflow then opens a packaged PTY, writes an
interactive probe through it, and reads the probe back. Importing `PtyProcess`
alone is not a sufficient packaging check because missing helper executables can
leave the module importable while every spawned terminal exits immediately.

Windows builder hosts need symlink creation enabled because Electron Builder's
`winCodeSign` cache contains symlinks. Use Windows Developer Mode or an elevated
PowerShell, clear `%LOCALAPPDATA%\electron-builder\Cache\winCodeSign` after a
failed extraction, and set `CSC_IDENTITY_AUTO_DISCOVERY=false` for unsigned local
installer builds.

PawCode and VS Code should not grow relay management screens. If a conversation has no linked relay, they can show server state, but relay creation and attachment remains a webchat/server-resource or Relay Desktop responsibility.

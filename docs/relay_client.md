# PawFlow Relay Client

PawFlow relay lifecycle is separate from PawFlow clients.

- Webchat, PawCode, VS Code, and API clients open conversations and send messages.
- Server relays are created and started from the webchat resource panel.
- Client relays are started by the standalone PawFlow Relay client on the machine that owns the files or desktop.

This separation keeps PawCode and the VS Code extension equivalent to the webchat: they do not create, start, stop, or own relays.

Server-side relay sessions track in-flight reverse filesystem requests per WebSocket connection. When a relay disconnects or is removed from the pool, those pending request tasks are cancelled so stale connections cannot retain writers, loops, or queued FUSE work.

## CLI

The standalone relay client is exposed as `pawflow-relay` when installed from the Python package, or as `python -m pawflow_relay` from a checkout.

Add a server profile:

```bash
pawflow-relay server add prod https://pawflow.example:PORT --gateway-key RoyBetty
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

## Local State

The relay client stores server and workspace profiles outside the project tree:

- Linux/macOS: `~/.pawflow/relay/`
- Windows: `%APPDATA%\\PawFlow\\relay\\`
- Override: `PAWFLOW_RELAY_HOME=/custom/path`

Profiles are split into `servers.json` and `workspaces.json`. Gateway keys and session tokens are currently stored in this local profile; the desktop client should migrate secrets to the OS keychain before a stable release.

## Relay Desktop

The Electron Relay Desktop slice lives in `pawflow-relay-desktop/`. It uses the same local state as the CLI and manages:

- server profiles: URL, private gateway key, login status;
- workspace shares: path, read/write mode, relay image/profile, local execution permission;
- running relay processes and logs;
- Docker relay images and custom image builds.

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
`runtime/bin/`, and packages it with Electron Builder (`nsis` on Windows,
`AppImage`/`deb` on Linux, `dmg`/`zip` on macOS). In packaged mode the desktop
app launches the embedded relay binary and uses the Python fallback only for
source checkouts.

Windows builder hosts need symlink creation enabled because Electron Builder's
`winCodeSign` cache contains symlinks. Use Windows Developer Mode or an elevated
PowerShell, clear `%LOCALAPPDATA%\electron-builder\Cache\winCodeSign` after a
failed extraction, and set `CSC_IDENTITY_AUTO_DISCOVERY=false` for unsigned local
installer builds.

PawCode and VS Code should not grow relay management screens. If a conversation has no linked relay, they can show server state, but relay creation and attachment remains a webchat/server-resource or Relay Desktop responsibility.

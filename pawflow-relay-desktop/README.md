# PawFlow Relay Desktop

Electron desktop manager for standalone PawFlow client relays.

PawCode and the VS Code extension are PawFlow clients only. This desktop app owns client-side relay lifecycle on a user machine:

- register PawFlow servers;
- login through the configured server/private gateway;
- define local workspace shares;
- start and stop relay processes;
- view relay logs.

## Run From Source

```bash
cd pawflow-relay-desktop
npm install
npm run prepare-runtime
npm start
```

On Windows, do not run `npm start` directly from a `\\wsl$\\...` UNC path:
`cmd.exe` cannot use UNC paths as its current directory, and npm package
scripts go through `cmd.exe`. Use the wrapper instead:

```powershell
.\start-windows.ps1 -Install
```

or, after dependencies are installed:

```powershell
.\start-windows.ps1
```

The wrapper uses WSL cleanup for broken UNC `node_modules` directories and
`cmd.exe pushd` to map the UNC path to a temporary drive letter before starting
Electron.

When Docker Desktop is available only through WSL, the app first tries the
Windows `docker` CLI and then falls back to `wsl.exe docker`. Set the distro
explicitly when needed:

```powershell
$env:PAWFLOW_RELAY_WSL_DISTRO = "Ubuntu-24.04"
.\start-windows.ps1
```

## Portable Runtime

`npm run prepare-runtime` creates `pawflow-relay-desktop/runtime/` with the
Python relay package, relay tool scripts, and PawFlow SDK shim required by the
Docker relay. For a copyable dev bundle, run:

```bash
npm run package:portable
```

This writes `dist/pawflow-relay-desktop/`. Copy that directory to a Windows-local
path, then run:

```powershell
.\start-windows.ps1 -Install
```

A copied desktop directory must contain:

```text
runtime/tools/
runtime/pawflow_relay/
runtime/docker/pawflow_sdk/pawflow.py
```

The app calls the Python module from `runtime/` first, then falls back to the
repository root for source development. On Windows it starts `python` directly
instead of the `py.exe` launcher so the relay does not keep an extra launcher
process alive. Override the Python executable when needed:

```bash
PAWFLOW_RELAY_PYTHON=/path/to/python npm start
```

## Shared State

Relay Desktop uses the same local state as the standalone CLI:

- Linux/macOS: `~/.pawflow/relay/`
- Windows: `%APPDATA%\\PawFlow\\relay\\`
- Override: `PAWFLOW_RELAY_HOME=/custom/path`

That means a server/workspace created with `pawflow-relay` appears in the desktop app, and vice versa.

## Current Scope

This is the first desktop management slice. It intentionally does not replace the webchat resource panel for server relays. Server relays remain a server/webchat resource concern; this app manages client relays on the user's machine.

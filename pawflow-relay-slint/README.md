# PawFlow Relay Slint Desktop

Native Slint implementation target for the PawFlow Relay Desktop control panel.

The goal is feature parity with `pawflow-relay-desktop/` while keeping the project isolated until the native path is proven on Windows, Linux, and macOS.

## Functional scope

The Slint app covers the same relay-manager surfaces as the Electron app:

- server profiles: add/update, delete, login/status refresh;
- relay workspaces: add/update, delete, start, stop;
- relay permissions: read/write mode, exec, remote desktop, local host access;
- Docker image inventory;
- relay image builder using `config/relay_image_catalog.json` and `scripts/generate-relay-image.py`;
- stdout/stderr log streaming for relay and image build processes;
- embedded Python runtime lookup via `PAWFLOW_RELAY_RUNTIME_ROOT` or a local `runtime/` directory.

Tray integration remains the last desktop-shell parity item before replacing the Electron path.

## Run from the repository

```bash
cd pawflow-relay-slint
python scripts/prepare-runtime.py --repo-root .. --out runtime
cargo run
```

Set `PAWFLOW_RELAY_PYTHON` to choose the Python executable. If unset, the app uses `py` on Windows and `python3` elsewhere. Building from source also requires Rust/Cargo on the same OS where you run the package command. On Windows, install Rust from `https://rustup.rs/`, then reopen PowerShell so `cargo` is in `PATH`.

## Build a local portable package

```bash
cd pawflow-relay-slint
python scripts/package-local.py --repo-root ..
```

From the repository root, use the wrapper:

```bash
python scripts/package-relay-slint.py
```

This creates `../dist/pawflow-relay-slint-<os>/` with the native binary and `runtime/` next to it. Launch the binary directly from that folder:

```bash
../dist/pawflow-relay-slint-linux/pawflow-relay-slint
```

On Windows the binary is `pawflow-relay-slint.exe`; on macOS and Linux it is `pawflow-relay-slint`.

## Release artifacts

The `.github/workflows/relay-slint-desktop.yml` workflow builds three native artifacts:

- `pawflow-relay-slint-linux`
- `pawflow-relay-slint-windows`
- `pawflow-relay-slint-macos`

Each artifact contains the native binary plus a `runtime/` directory. Keep `runtime/` next to the binary.

## Runtime packaging

`python scripts/prepare-runtime.py` copies the minimal Python runtime needed by the desktop app:

- `pawflow_relay/`
- `tools/fs_actions.py`
- `scripts/generate-relay-image.py`
- `config/relay_image_catalog.json`

The binary also works from the source tree and falls back to the repository root when no packaged runtime is present.

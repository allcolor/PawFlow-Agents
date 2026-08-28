# PawFlow Universal Installer Implementation Plan

Status: approved for implementation after this plan is documented.

## 1. Objective

Provide one installation engine with two frontends:

- a terminal CLI;
- a native desktop GUI for Windows, Linux, and macOS.

The engine installs or updates a PawFlow server either on the local machine or on
a remote machine reached through SSH. After the server is healthy, it opens the
HTTPS first-run wizard and can install, associate, configure, start, and verify
PawFlow Relay Desktop on the client workstation.

Remote installation proposes Tailscale first. Public exposure is never selected
implicitly.

## 2. Existing contracts to preserve

The universal installer orchestrates existing, tested installation contracts
instead of replacing them:

- `scripts/install-pawflow.sh`;
- `scripts/install-pawflow.ps1`;
- `scripts/doctor-pawflow.sh` and `scripts/doctor-pawflow.ps1`;
- `core/installer_deployment.py`;
- `core/install_bootstrap.py`;
- `docs/installation_bootstrap.md`;
- `config/relay_image_catalog.json`;
- the versioned browser wizard at
  `data/repository/flows/global/default/pawflow_installer/versions/assets/install.html`;
- PawFlow Relay Desktop packaging and its relay manager CLI.

The existing browser wizard remains the only authority for PawFlow application
configuration: admin identity, AuthGateway, Private Gateway, TLS, LLM services,
summarizer, agent, starter conversation, and server-relay opt-in.

## 3. Product outcomes

A user can:

1. launch the same installer from a terminal or GUI;
2. choose local installation or enter an SSH target;
3. run a preflight before any mutation;
4. see exact commands, progress, diagnostics, and resumable state;
5. install from a published version or an explicit source revision;
6. preserve an existing PawFlow data directory during updates;
7. choose a secure reachability mode, with Tailscale recommended for SSH targets;
8. open the correct HTTPS wizard URL automatically;
9. install PawFlow Relay Desktop on the client machine;
10. associate it with the chosen server through the existing browser login flow;
11. choose explicit relay capabilities and shared paths;
12. configure autostart only with user confirmation;
13. verify that the relay is running and visible as connected on the server;
14. rerun the installer safely after interruption.

## 4. Non-goals

- replacing the versioned PawFlow browser wizard;
- silently installing a public reverse proxy;
- storing server or gateway credentials in installer state;
- inventing another server deployment format;
- implicitly creating a user relay during server bootstrap;
- managing conversation or agent configuration outside the wizard;
- embedding release, tag, commit, deployment, or cutover actions into ordinary
  development commands.

## 5. Architecture

Create a Python package `pawflow_installer/` with pure contracts, a shared
orchestration engine, transport adapters, and two thin frontends.

```
pawflow_installer/
  __init__.py
  __main__.py
  models.py
  state.py
  events.py
  commands.py
  preflight.py
  engine.py
  reachability.py
  relay_desktop.py
  transports/
    __init__.py
    base.py
    local.py
    ssh.py
  frontends/
    __init__.py
    cli.py
    gui.py
scripts/
  build-pawflow-universal-installer.py
tests/
  test_universal_installer_models.py
  test_universal_installer_state.py
  test_universal_installer_preflight.py
  test_universal_installer_engine.py
  test_universal_installer_ssh.py
  test_universal_installer_reachability.py
  test_universal_installer_relay_desktop.py
docs/
  UNIVERSAL_INSTALLER.md
```

### 5.1 Shared engine

The engine is an explicit state machine. CLI and GUI send the same typed
`InstallRequest` and consume the same structured `InstallEvent` stream.

Frontends never construct shell commands themselves.

### 5.2 Transport boundary

`InstallTransport` exposes:

- platform discovery;
- command availability;
- non-mutating command execution;
- mutating command execution;
- file upload;
- path and environment queries;
- process output streaming;
- cancellation.

Implementations:

- `LocalTransport`: subprocesses on the current machine;
- `SshTransport`: system OpenSSH client with batch-safe argument construction,
  host-key verification, optional identity file, and remote shell detection.

No command is assembled through string interpolation of user-controlled values.
Commands are argument arrays or validated shell-script invocations with
out-of-band environment data.

## 6. Request model

```json
{
  "version": 1,
  "target": {
    "kind": "local|ssh",
    "host": "server.example.org",
    "port": 22,
    "user": "operator",
    "identity_file": "local path or null"
  },
  "install": {
    "pawflow_home": "target path",
    "port": 9443,
    "version": "explicit release or null",
    "source": "published|source",
    "native": false,
    "keep_old_images": false,
    "skip_apparmor": false
  },
  "reachability": {
    "mode": "local|tailscale|existing_https|public_manual",
    "hostname": "optional explicit host"
  },
  "relay_desktop": {
    "install": true,
    "server_url": "resolved HTTPS URL",
    "capabilities": ["filesystem.read", "filesystem.write"],
    "paths": ["explicit local paths"],
    "autostart": false
  }
}
```

Every required field is validated. Missing values do not become anonymous,
default servers, default shares, or broad capabilities.

CLI convenience defaults may be displayed as suggestions, but the finalized
request records the explicit selected value.

## 7. Durable installer state

State is stored on the client under the platform application-data directory, not
inside a project checkout:

```json
{
  "version": 1,
  "operation_id": "UUID",
  "created_at": "UTC timestamp",
  "updated_at": "UTC timestamp",
  "request_digest": "SHA-256",
  "target_fingerprint": "non-secret stable identifier",
  "phase": "server_preflight",
  "completed_steps": ["local_preflight"],
  "step_results": {
    "local_preflight": {
      "status": "completed",
      "started_at": "UTC timestamp",
      "finished_at": "UTC timestamp",
      "evidence": {}
    }
  },
  "cancelled": false
}
```

State contains no password, gateway key, OAuth token, SSH private key, session
cookie, relay token, or plaintext command containing a secret.

Writes are atomic and fsynced. A restart compares the request digest and target
fingerprint before offering resume. A changed request creates a new operation.

## 8. State machine

1. `request_validated`
2. `local_preflight`
3. `target_discovery`
4. `target_preflight`
5. `reachability_plan`
6. `server_payload_ready`
7. `server_installing`
8. `server_health`
9. `wizard_ready`
10. `relay_desktop_preflight`
11. `relay_desktop_installing`
12. `relay_desktop_pairing`
13. `relay_desktop_configuring`
14. `relay_desktop_starting`
15. `relay_desktop_verifying`
16. `completed`

Each step declares:

- read-only or mutating;
- prerequisites;
- idempotence key;
- evidence needed for completion;
- compensation or safe retry behavior;
- user confirmation requirements.

There is no implicit overall timeout, retry count, iteration quota, or expiration.
If the user configures a positive connection or command limit, it is recorded in
the request and shown in the UI. Otherwise an operation continues until success,
failure from the invoked command, or explicit cancellation.

## 9. Preflight

### 9.1 Client preflight

- supported client OS and architecture;
- Python/runtime availability for source invocation;
- writable application-data and download directories;
- system browser availability;
- OpenSSH availability for remote mode;
- keychain availability for Relay Desktop credentials;
- free disk space needed for downloaded installers;
- existing PawFlow and Relay Desktop installations.

### 9.2 Target preflight

- target OS, architecture, shell, and privilege model;
- Docker Engine or Docker Desktop availability;
- Docker daemon connectivity;
- Compose behavior when required by existing scripts;
- selected PawFlow port availability;
- target data directory ownership and free space;
- Git only when source installation requires it;
- AppArmor detection;
- access to GHCR or the explicit source repository;
- existing `pawflow-server` identity, image, volumes, and health;
- Tailscale availability and current login state when selected.

The report separates:

- pass;
- warning requiring confirmation;
- failure with exact remediation;
- mutation that can be performed by the installer after approval.

Preflight never installs dependencies.

## 10. Local server installation

Select the platform adapter:

- Unix-like target: invoke `scripts/install-pawflow.sh` with explicit flags;
- native Windows target: invoke `scripts/install-pawflow.ps1`;
- Windows through SSH: require a supported PowerShell/OpenSSH environment or
  instruct the user to use WSL2. Do not guess.

The engine:

1. obtains the minimal signed/versioned installer payload;
2. verifies checksums when release metadata provides them;
3. records the exact version and image references;
4. executes the existing doctor;
5. asks for confirmation before the first mutating step;
6. streams installer output as structured log events;
7. records the final deployment descriptor from
   `core/installer_deployment.py` conventions;
8. verifies container/native process health and the bootstrap HTTPS endpoint.

Updates reuse the same scripts and preserve data. The engine never deletes an
existing installation to recover from a failed update.

## 11. SSH installation

### 11.1 Authentication

Use the user's OpenSSH configuration and agent by default. Password entry, if
supported by the platform, remains in a native prompt and is not persisted.
Identity-file paths are local metadata; private key contents are never read into
installer state.

Host-key verification is mandatory. First contact displays the fingerprint and
requires explicit confirmation; changed keys fail closed.

### 11.2 Payload transfer

Upload only the minimal installer bundle and its checksum into an operation-scoped
remote directory. Reuse it on resume when the digest matches. Remove only that
validated temporary directory after completion or explicit cleanup.

### 11.3 Privilege escalation

The engine reports which steps require privilege. It uses an interactive
`sudo`/PowerShell elevation prompt only after confirmation and never persists
the password.

### 11.4 Output

Remote stdout and stderr are streamed separately with step ids and timestamps.
Exit status and target evidence determine completion. Disconnect leaves the
operation resumable; the installer rechecks target state before retrying.

## 12. Reachability and Tailscale

For a remote target the selection order is:

1. Tailscale;
2. already configured HTTPS endpoint;
3. manually managed public HTTPS;
4. local-only access with instructions.

### 12.1 Tailscale flow

The installer:

1. detects Tailscale on client and target;
2. explains the resulting trust boundary;
3. offers installation on either side only after confirmation;
4. uses official platform installation commands or links;
5. asks the user to complete interactive Tailscale login;
6. reads `tailscale status --json` without displaying auth material;
7. identifies the target tailnet DNS name or Tailscale IP;
8. verifies HTTPS reachability from the client;
9. records the selected URL as non-secret operation evidence.

The installer does not create reusable auth keys, enable Tailscale SSH, change
ACLs, or expose Tailscale Funnel unless the user explicitly chooses a separately
documented advanced path.

### 12.2 Existing or public HTTPS

The installer accepts an explicit URL, validates HTTPS, checks certificate and
origin, then probes the PawFlow bootstrap endpoint. It does not provision DNS,
Caddy, router forwarding, cloud firewall rules, or certificates implicitly.
Instead it displays the exact remaining operator actions.

## 13. Opening the PawFlow wizard

After health and reachability checks:

1. resolve the canonical HTTPS install URL;
2. verify `/install/api` returns bootstrap state;
3. display the temporary Private Gateway key only through the existing installer
   script contract or an explicit secure handoff;
4. open `/install` in the system browser;
5. keep the universal installer open and observe only non-secret health state;
6. detect `install_complete=true` through an authenticated or local target
   check selected by the current contract;
7. continue to optional Relay Desktop onboarding.

The universal installer does not duplicate wizard fields.

## 14. PawFlow Relay Desktop installation

### 14.1 Artifact selection

Resolve the current server version and release metadata, then select the matching
Relay Desktop artifact for the client OS and architecture:

- Windows installer/ZIP;
- Linux AppImage/DEB/tar.gz;
- macOS DMG/ZIP.

Verify the published checksum and, when available, platform signature.

### 14.2 Install

Use an explicit platform adapter:

- Windows: NSIS or portable package;
- Linux: AppImage or package-manager install chosen by the user;
- macOS: DMG application copy.

Do not elevate unless the selected installation location requires it.

### 14.3 Secure association

Association reuses Relay Desktop's browser-login flow:

1. create a server profile with URL and display name;
2. open the PawFlow login page;
3. receive the authenticated callback/session through the existing manager;
4. store gateway/session material in the OS keychain before declaring this flow
   stable;
5. never ask the user to copy a long-lived relay token.

If the future remote relay enrollment protocol from
`REMOTE_RELAY_ENROLLMENT_SHARING_PLAN.md` is implemented, the engine may use its
short-lived provisioning token. Until then, browser login remains canonical.

### 14.4 Capability and path configuration

The user explicitly selects:

- shared directories;
- read-only or read/write filesystem;
- shell execution;
- container execution;
- host-local execution;
- automation;
- local screen/desktop;
- service tunnels.

The installer starts with no optional capability selected. Required runtime base
capabilities are displayed separately. Every shared path is normalized, shown,
and confirmed. Broad roots and home directories trigger a warning and require a
second confirmation.

### 14.5 Autostart

Autostart is off unless the user selects it.

Adapters:

- Windows: per-user startup task or approved installer integration;
- Linux: user systemd unit;
- macOS: per-user LaunchAgent.

The unit contains only profile identifiers and executable paths, never gateway,
session, or relay secrets.

### 14.6 Verification

The installer proves all of the following:

1. Relay Desktop executable launches;
2. selected workspace profile exists;
3. relay process remains running;
4. server WebSocket handshake succeeds;
5. server reports the expected relay service id connected;
6. exposed capabilities match the confirmed selection;
7. an authorized read-only health operation succeeds;
8. autostart definition, if selected, points to the installed binary.

A connected relay is not inferred only from a local PID.

## 15. CLI design

Proposed entrypoint:

```
pawflow-install plan
pawflow-install run
pawflow-install resume OPERATION_ID
pawflow-install status OPERATION_ID
pawflow-install cancel OPERATION_ID
pawflow-install cleanup OPERATION_ID
pawflow-install gui
```

Important options are explicit and composable:

```
pawflow-install run --target local --port 9443 --published
pawflow-install run --target ssh --host host --user operator --tailscale
pawflow-install run --target ssh --host host --existing-url https://...
```

`plan` is read-only and prints a redacted JSON plan. `run` asks for
confirmation before mutation unless `--yes` is explicitly supplied.
`--json` emits stable event records for automation.

## 16. GUI design

Use Tkinter from the Python standard library for the first implementation so the
GUI and CLI share one packaged runtime and no web server is introduced.

Screens:

1. welcome and resume-existing-operation;
2. local or SSH target;
3. target credentials and host-key confirmation;
4. preflight results;
5. version and install mode;
6. reachability with Tailscale recommended;
7. review and explicit mutation confirmation;
8. live progress/logs;
9. open wizard;
10. Relay Desktop install;
11. paths and capabilities;
12. autostart;
13. end-to-end verification and exportable redacted report.

Long content is vertically scrollable and usable on small laptop screens.
Closing the window offers cancel, keep running, or return; it never silently
kills a remote installation.

## 17. Error and recovery behavior

- A failed read-only check can be rerun immediately.
- A failed mutating step reruns its evidence probe before any command.
- Existing healthy resources are reused.
- Existing unhealthy resources receive a targeted remediation action.
- A cancelled operation is resumable unless the user explicitly cleans it up.
- Cleanup only removes operation-scoped temporary payloads and local logs.
- Server data, images, profiles, relays, and browser state are not deleted by
  generic cleanup.
- Logs redact secrets at event creation, not only at display time.

## 18. Packaging

Build one PyInstaller executable per OS containing CLI and GUI entrypoints.
Portable archives include:

- executable;
- license and notices;
- schemas;
- platform launcher;
- checksum manifest;
- user documentation.

The executable version derives from `pyproject.toml`. Release workflows add the
artifacts only after platform tests pass.

## 19. Implementation work packages

### I1. Models, events, and state

Typed request, plan, step, result, and event models; atomic state store; redaction.

### I2. Local transport and preflight

Pure command builder; local platform facts; existing script adapter; tests.

### I3. SSH transport

Host verification, command/file transfer, output streaming, reconnect and resume.

### I4. Reachability

Tailscale detection/install guidance, URL resolution, HTTPS probes.

### I5. Server orchestration

Doctor, payload, existing installer invocation, health evidence, wizard opening.

### I6. Relay Desktop onboarding

Artifact selection, install adapters, login association, capabilities, paths,
autostart, server-side connection verification.

### I7. Frontends

CLI, JSON event mode, Tkinter GUI, cancellation, resume.

### I8. Packaging and CI

PyInstaller, platform artifacts, checksum/signature validation, documentation.

## 20. Test strategy

### Unit

- request and state validation;
- no-secret serialization;
- exact command arguments on every platform;
- host-key and URL validation;
- preflight classification;
- Tailscale status parsing;
- idempotence decisions;
- Relay Desktop artifact selection;
- capability/path validation;
- autostart unit rendering.

### Integration

Use fake transports and recorded command results to test every state transition,
disconnect, resume, cancellation, and failure compensation.

Run containerized Linux installation tests against an isolated Docker daemon.
Mock SSH with a disposable test host. No test touches the developer's actual
Docker installation, keychain, Tailscale account, autostart, or browser profile.

### Platform smoke

- clean install;
- interrupted install and resume;
- update preserving data;
- wizard reachability;
- Relay Desktop install and connection;
- uninstall of installer application without removing PawFlow data.

## 21. CI gates

- Python formatting/lint/type and security checks;
- all unit and fake-transport integration tests;
- shellcheck/PSScriptAnalyzer for existing and generated scripts;
- Linux container smoke;
- Windows and macOS packaging smoke;
- artifact checksum verification;
- secret scanner on fixtures and logs;
- documentation and `--help` snapshot tests.

## 22. Acceptance criteria

1. CLI and GUI produce the same redacted plan for the same request.
2. Local Linux, native Windows, and macOS-Docker paths invoke the existing
   installer contracts correctly.
3. SSH installation is resumable after disconnect.
4. Tailscale is offered first and never installed or configured silently.
5. Existing PawFlow data survives install retry and update failure.
6. The correct HTTPS wizard opens and remains the application-configuration
   authority.
7. Relay Desktop is installed on all three client operating systems.
8. Relay paths and capabilities exactly match explicit user selections.
9. Autostart is opt-in and secret-free.
10. Completion requires server-observed relay connectivity.
11. State and logs contain no secret.
12. There is no implicit operation timeout, retry count, or pass limit.
13. Tests, packaging gates, and documentation pass.

## 23. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Existing scripts diverge | Keep them canonical; adapter tests assert arguments and outputs |
| SSH shell differences | Detect supported shell and fail with remediation |
| Interrupted remote mutation | Durable step state plus evidence-first resume |
| Secret leakage in logs | Structured events and allowlist-based redaction at source |
| Public exposure selected accidentally | Tailscale first, explicit public mode, no automatic firewall/DNS |
| Relay reports local success but is disconnected | Require server-side connected evidence |
| GUI and CLI drift | One engine and event protocol, frontend contract tests |
| Implicit destructive cleanup | Operation-scoped validated targets only |
| Linux keychain unavailable | Fail closed before Relay Desktop association |

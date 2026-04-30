# PawFlow Installation Bootstrap

This document defines the intended first-run installation flow for a self-hosted
PawFlow server.

## Goals

- PawFlow runs as a Dockerized server application.
- A first-run installer configures the server safely before normal use.
- No user relay is created implicitly during server installation.
- Users add server or client relays later from the webchat.
- The installer is transactional and recoverable across restarts.

## Server Installation Paths

Before starting either install path, run:

```bash
bash scripts/doctor-pawflow.sh
```

On Windows before WSL is installed, run the native PowerShell doctor instead:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/doctor-pawflow.ps1
```

For source builds, run `bash scripts/doctor-pawflow.sh --source`. The doctor
checks Docker CLI/daemon access, WSL/Docker Desktop integration on Windows,
Git for source installs, Docker socket availability for first-run runtime image
builds, selected port availability, and prints OS-specific remediation steps.
The PowerShell doctor specifically tells users to install WSL2 and Docker
Desktop with WSL integration when those prerequisites are missing.

### Published image

The default path is to run a published Docker image:

```bash
bash scripts/install-pawflow.sh
```

By default this pulls `ghcr.io/allcolor/pawflow:latest`, creates persistent
volumes under `~/pawflow`, starts `pawflow-server`, and exposes port `9090`.

Override values with flags or environment variables:

```bash
bash scripts/install-pawflow.sh --image ghcr.io/allcolor/pawflow:latest --port 9090
PAWFLOW_HOME=/srv/pawflow PAWFLOW_PORT=9443 bash scripts/run-pawflow-docker.sh
```

### Build from source

Advanced users can build locally from GitHub:

```bash
bash scripts/install-pawflow.sh --source
```

This checks out `https://github.com/allcolor/PawFlow-Agents.git`, builds the
server image, then starts it with the same persistent volume layout.

## First Run Contract

On a fresh server data volume, PawFlow should start in bootstrap mode:

1. Build or verify required runtime images:
   - `pawflow-claude-code:latest`
   - `pawflow-relay-dev:latest`
2. Deploy only the `PawFlow Installer` flow.
3. Protect the installer with Private Gateway key `RoyBetty`.
4. Generate and use a bootstrap self-signed TLS certificate.
5. Persist installer progress in a server-side install state file.
6. Never create a default user relay during bootstrap.

The server container receives the host Docker socket when available so the
bootstrap can build those runtime images from inside the PawFlow container. The
install script mounts `/var/run/docker.sock` and adds the socket group ID as a
supplemental group. If the socket is unavailable, bootstrap must surface a
clear blocking error and instruct the user to mount Docker or build the images
manually.

`RoyBetty` is a temporary bootstrap key. The installer must force a replacement
before finalization.

Bootstrap HTTPS is mandatory. A first-run self-signed certificate is generated
under `data/system/ssl/bootstrap.crt` with key
`data/system/ssl/bootstrap.key`. The browser will show a trust warning until the
wizard installs final certificates. If the bootstrap certificate cannot be
generated, startup must fail loudly instead of falling back to plain HTTP.

The installer template is stored at
`data/repository/flows/global/default/pawflow_installer/versions/1.0.0.json`.
It currently defines the first-run routes `/install` and `/install/api` and the
bootstrap checklist. The transactional API that writes final server config is a
separate implementation step.

## Wizard Steps

1. Server endpoint
   - public base URL
   - HTTP/HTTPS port
   - certificate upload, generated certificate, or mounted cert path
   - ACME-compatible certificate generation, starting with Let's Encrypt
     support; ZeroSSL and other ACME CAs can use the same abstraction later
   - self-signed certificate generation for private/local deployments
   - bind and certificate validation

2. Private Gateway
   - configure final gateway key
   - reject `RoyBetty` as a final key
   - validate access through the final gateway

3. Authentication
   - internal auth
   - Google OAuth
   - OAuth secrets stored through the secret store
   - redirect URI validation

4. Admin user
   - create local admin
   - optionally link the admin to Google OAuth
   - validate login

5. LLM services
   - create one or more LLM services
   - store provider secrets through the secret store
   - test each service
   - select the default service for the PawFlow Agent

6. Summarizer service
   - choose the service used by compaction/background summaries
   - restrict choices to configured LLM services that can handle summarization
   - validate the summarizer with a short smoke summary
   - persist the selected service in server/flow parameters

7. Variables and secrets
   - create server/global variables used by deployed flows
   - create required secrets through the secret store
   - support OAuth client secrets, provider API keys, gateway material, and flow-specific secrets
   - store only secret IDs/references in installer state
   - validate references resolve without exposing secret values

8. CLI credential pools
   - Claude Code, Codex, and Gemini login workflows
   - run login inside the CLI container image
   - verify provider auth status
   - store credentials only in the intended runtime/session directories

9. Relay image profiles
   - server relays use the official `server-full` relay image profile
   - client relays are configured later by the user from selectable capabilities
   - expose `client-minimal`, language presets, desktop/browser presets, and advanced per-feature checkboxes
   - always include the required PawFlow relay base with Python runtime, FUSE mounts, and `/workspace`/`/cc_sessions`/`/filestore` mountpoints
   - generate a Dockerfile, build script, run/register script, and manifest from `config/relay_image_catalog.json`

10. Final review and smoke tests
   - gateway final key works
   - login works
   - `/chat` responds
   - `/api/agent` responds
   - SSE responds
   - default LLM service responds
   - summarizer service responds
   - configured variables resolve
   - configured secret references resolve without leaking values

11. Finalize
   - write final config
   - deploy `http_listener`
   - deploy `PawFlow Agent`
   - start both flows
   - mark `install_complete=true`
   - disable the installer flow
   - redirect to gateway -> login -> empty webchat

## Install State

The installer state should be persisted outside the flow runtime so restart and
rollback are possible:

```json
{
  "version": 1,
  "install_complete": false,
  "current_step": "llm_services",
  "completed_steps": ["server", "gateway", "auth"],
  "draft": {
    "server": {},
    "gateway": {},
    "auth": {},
    "admin": {},
    "llm_services": [],
    "summarizer_service": "claude_code_llm_service",
    "variables": {},
    "secrets": [],
    "cli_credentials": []
  },
  "checks": {
    "docker_images_built": true,
    "gateway_tested": true,
    "oauth_tested": false
  }
}
```

Secrets must not be stored in install state. Store only secret IDs.

## Relay Onboarding After Install

The initial server install does not create relays. After the user reaches the
webchat, the Relays panel should offer:

- Install relay client
  - generate a short-lived provisioning token
  - download an installer script/package
  - check Docker and permissions on the client machine
  - optionally check VNC/noVNC prerequisites for local desktop control
  - build or pull the relay image
  - register and start the relay

- Install relay server
  - configure and launch a relay on the server host when explicitly requested

This keeps PawFlow server bootstrap separate from user workspace onboarding.

## Recovery

Do not permanently delete the installer flow. Finalization should disable and
hide it. Recovery should be possible through a local-only mechanism such as:

```bash
PAWFLOW_BOOTSTRAP_RESET=1 docker restart pawflow-server
```

or a future local admin command.

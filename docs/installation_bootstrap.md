# PawFlow Installation Bootstrap

This document defines the first-run installation flow for a self-hosted PawFlow
server.

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

On Windows, PawFlow requires WSL2 plus Docker Desktop with WSL integration. Run
the native PowerShell doctor only to verify or remediate those host
prerequisites before entering WSL:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/doctor-pawflow.ps1
```

For source builds, run `bash scripts/doctor-pawflow.sh --source` from inside the
Linux/WSL environment. The doctor checks Docker CLI/daemon access, WSL/Docker
Desktop integration on Windows, Git for source installs, Docker socket
availability for first-run runtime image builds, selected port availability, and
prints OS-specific remediation steps. The PowerShell doctor specifically tells
users to install WSL2 and Docker Desktop with WSL integration when those
requirements are missing; it is not a native Windows install path.

### Published image

The default path is to run a published Docker image:

```bash
bash scripts/install-pawflow.sh
```

By default this pulls `ghcr.io/allcolor/pawflow:latest`, creates persistent
volumes under `~/pawflow`, starts `pawflow-server`, and exposes port `9090`.
The Docker entrypoint seeds missing repository/config defaults from the image
into the persistent bind mounts before the server starts, so an empty
`~/pawflow/data` directory still contains the installer flow templates after
startup.

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

On a fresh server data volume, PawFlow starts in bootstrap mode:

1. Deploy only the `PawFlow Installer` flow.
2. Store the temporary bootstrap gateway key as an encrypted global secret.
3. Protect the installer with a global `privateGateway` service referenced by
   the installer `httpListener`.
4. Generate and use a bootstrap self-signed TLS certificate.
5. Persist installer progress in a server-side install state file.
6. Never create a default user relay during bootstrap.
7. Use the standalone relay runtime image for server workspaces; local source
   mounts are opt-in development behavior only.

The server container receives the host Docker socket when available so server
workspace relays can be spawned later. The install script mounts
`/var/run/docker.sock` and adds the socket group ID as a supplemental group. If
the socket is unavailable, the server still installs, but server-side workspace
creation remains blocked until Docker socket access is provided.

`RoyBetty` is a temporary bootstrap key. The installer must force a replacement
before finalization. The bootstrap `privateGateway` service is disabled when the
installer finalizes.

Bootstrap HTTPS is mandatory. A first-run self-signed certificate is generated
under `data/system/ssl/bootstrap.crt` with key
`data/system/ssl/bootstrap.key`. The browser will show a trust warning until the
wizard installs final certificates. Final certificate configuration can use
mounted cert/key files, a retained private self-signed certificate, or an
ACME-compatible issuer such as Let's Encrypt; ZeroSSL and other ACME CAs can use
the same abstraction. If the bootstrap certificate cannot be generated, startup
must fail loudly instead of falling back to plain HTTP.

The installer template is stored at
`data/repository/flows/global/default/pawflow_installer/versions/1.0.0.json`.
It defines `/install`, dynamic status at `GET /install/api`, and finalization at
`POST /install/api/finalize`. These routes sit behind the bootstrap
`privateGateway` service. Finalization requires the current bootstrap key,
rejects keeping `RoyBetty`, requires an admin password, stores only a SHA-256
digest of the replacement gateway key, writes the final key as encrypted secret
`privategateway.main`, creates the persistent `_private_gateway`, creates
`_auth_gateway`, creates the selected `llmConnection`, creates
`summarizer_service`, creates `skill_review_service`, deploys `default.pawflow_agent:1.0.0` as
`pawflow-agent`, creates a starter conversation with the `assistant` agent
selected, writes `install_complete=true`, disables `_bootstrap_private_gateway`,
and marks the installer deployment stopped for restart-safe restoration.

## Wizard Steps

1. Private Gateway
   - configure final gateway key
   - reject `RoyBetty` as a final key
   - persist only `privategateway.main` plus a digest in install state

2. Authentication
   - internal auth
   - create or update the local admin user
   - reject admin passwords shorter than 12 characters

3. LLM service
   - create the selected LLM service ID
   - default wizard values are `codex_appserver_llm_service`, provider
     `codex-app-server`, and model `gpt-5.5`
   - store an optional API key as encrypted secret `llm.<service_id>.api_key`
   - assign this explicit service to the starter conversation agent

4. Summarizer and skill review services
   - create `summarizer_service`
   - point it to the selected LLM service
   - create `skill_review_service`
   - point it to the selected LLM service for no-tool skill review

5. Main flow and conversation
   - deploy `default.pawflow_agent:1.0.0` as `pawflow-agent`
   - pass the final Private Gateway service explicitly to the listener
   - create a starter conversation for the admin user
   - add `assistant` to `conv_agents` with the selected LLM service
   - set `active_resources.agent=assistant`

6. Variables and secrets
   - gateway material and optional provider API keys are stored through the
     encrypted secret store
   - installer state stores only secret IDs/references and non-secret digests
   - additional variables and secrets remain configurable from the normal
     system settings and services UI after installation

7. Relay image profiles
   - server relays use the official `server-full` relay image profile
   - client relays are configured later by the user from selectable capabilities
   - expose `client-minimal`, language presets, desktop/browser presets, and advanced per-feature checkboxes
   - always include the required PawFlow relay base with Python runtime, FUSE mounts, and `/workspace`/`/cc_sessions`/`/filestore` mountpoints
   - generate a Dockerfile, build script, run/register script, and manifest from `config/relay_image_catalog.json`

8. Final review and smoke tests
   - gateway final key works
   - login works
   - `/chat` responds
   - `/api/agent` responds
   - SSE responds
   - default LLM service responds
   - summarizer service responds
   - skill review service is installed
   - configured variables resolve
   - configured secret references resolve without leaking values

9. Finalize
   - write final config
   - deploy `PawFlow Agent`
   - mark `install_complete=true`
   - disable the installer flow
   - redirect to gateway -> login -> webchat with a starter conversation

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
    "llm_services": {"primary": "codex_appserver_llm_service"},
    "summarizer_service": {"service_id": "summarizer_service"},
    "skill_review_service": {"service_id": "skill_review_service"},
    "flows": {"main_instance_id": "pawflow-agent"},
    "conversation": {"conversation_id": "...", "agent": "assistant"}
  },
  "checks": {
    "final_private_gateway": true,
    "auth_gateway": true,
    "admin_user": true,
    "llm_service": true,
    "summarizer_service": true,
    "skill_review_service": true,
    "main_flow_deployed": true,
    "first_conversation": true
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

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

On Windows, PawFlow supports two install shells: native Windows Bash backed by
Docker Desktop Linux containers, or WSL2 backed by Docker Desktop WSL
integration. Run the PowerShell doctor to verify the Windows host before using
either path:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/doctor-pawflow.ps1
```

For source builds, run `bash scripts/doctor-pawflow.sh --source` from the shell
that will run the installer. The doctor checks Docker CLI/daemon access, Docker
Desktop/WSL status where applicable, Git for source installs, Docker socket
availability for server-side relay spawning, selected port availability, and
prints OS-specific remediation steps. WSL2 is required for WSL-based installs;
native Windows installs can use Docker Desktop directly.

### Complete from-scratch install

The default path is a complete Docker bootstrap with a prebuilt server image
first:

```bash
bash scripts/install-pawflow.sh
```

When run from a checkout, the installer uses that checkout. When run as a
downloaded standalone script, it clones
`https://github.com/allcolor/PawFlow-Agents.git` into `~/pawflow-src`. Without a
version, it first tries the prebuilt `ghcr.io/allcolor/pawflow:latest` server
image. With `--version VERSION`, it checks out the matching git tag before
building local runtime images, then first tries `ghcr.io/allcolor/pawflow:VERSION`.
If the prebuilt server image is unavailable, the installer builds the server
image from that same source tag. It always builds every
local runtime image required before the first web installer opens:

- `ghcr.io/allcolor/pawflow:latest`, `ghcr.io/allcolor/pawflow:VERSION`, or
  `PAWFLOW_IMAGE` for the server
- `pawflow-claude-code:latest` for Claude Code, Codex, Gemini, and Antigravity
  CLI sessions and OAuth login containers
- `pawflow-relay-minimal:latest` for protected server-side minimal execution
- `pawflow-relay-dev:latest` for full server relay workspaces

After the builds, it creates persistent volumes under `~/pawflow`, starts
`pawflow-server`, and publishes port `19990` on `127.0.0.1` by default. Set
`PAWFLOW_PUBLISH_HOST=0.0.0.0` only when the bootstrap endpoint must be reachable
from another host and `PAWFLOW_BOOTSTRAP_GATEWAY_KEY` has been replaced with a
strong temporary value. The Docker entrypoint seeds missing
repository/config defaults from the image into the persistent bind mounts before
the server starts, so an empty `~/pawflow/data` directory still contains the
installer flow templates after startup.

To keep the PawFlow server native instead of running it inside the server
container, use:

```bash
bash scripts/install-pawflow.sh --native
```

Native mode still prepares the Docker runtime images, then installs PawFlow in a
local virtualenv and starts `cli.py start` with `PAWFLOW_DATA_DIR` under
`~/pawflow/data`.

Override values with flags or environment variables:

```bash
bash scripts/install-pawflow.sh --version 1.0.0
bash scripts/install-pawflow.sh --image ghcr.io/allcolor/pawflow:latest --port 19990
PAWFLOW_HOME=/srv/pawflow PAWFLOW_PORT=9443 bash scripts/run-pawflow-docker.sh
```

### Forced source install

To skip the prebuilt server image lookup and build from source, use:

```bash
bash scripts/install-pawflow.sh --from-source
bash scripts/install-pawflow.sh --from-source --version 1.0.0
```

With `--from-source --version VERSION`, the installer checks out the exact git
tag `VERSION` and fails if that tag does not exist. With `--from-source` and no
version, it checks out branch `main`.

### Require a published server image

To fail instead of falling back to a source-built server image, use:

```bash
bash scripts/install-pawflow.sh --pull-server
```

This still builds the local CLI LLM, minimal relay, and full relay images from
the repository Docker contexts.

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
wizard installs final certificates. Final certificate configuration currently
supports either mounted cert/key files or a retained private self-signed
certificate generated by PawFlow. If the bootstrap certificate cannot be
generated, startup must fail loudly instead of falling back to plain HTTP.

The installer template is stored at
`data/repository/flows/global/default/pawflow_installer/versions/1.0.0.json`.
The browser UI is a separate flow asset at
`data/repository/flows/global/default/pawflow_installer/versions/assets/install.html`,
referenced by the installer flow through `generateFlowFile.content_file`. The
template defines a public `GET /` redirect to `/install`, `/install`, dynamic
status at `GET /install/api`, credential-pool prepare/paste/server-login
endpoints under `POST /install/api/llm-credential/*`, and finalization at
`POST /install/api/finalize`. These routes sit behind the bootstrap
`privateGateway` service. The deployed executor preserves the flow version
directory as the asset source directory so `install.html` resolves after process
restore. Finalization requires the current bootstrap key,
rejects keeping `RoyBetty`, requires matching admin password confirmation and
password complexity, stores only a SHA-256
digest of the replacement gateway key, writes the final key as encrypted secret
`privategateway.main`, creates the persistent `_private_gateway`, resolves the
final listener certificate configuration, creates `_auth_gateway`, creates the selected `llmConnection`, creates
`summarizer_service`, deploys `default.pawflow_agent:1.0.0` as
`pawflow-agent`, creates a starter conversation with the `assistant` agent
selected, writes `install_complete=true`, disables `_bootstrap_private_gateway`,
and marks the installer deployment stopped for restart-safe restoration.
If finalization fails before completion, the installer returns a JSON error and
restores the pre-finalization system user/session/security, final certificate,
and global-secret files after removing runtime artifacts it created.

## Wizard Steps

1. Admin user
   - create or update the local admin user
   - require password confirmation to match
   - reject admin passwords shorter than 12 characters or missing lowercase,
     uppercase, digit, or symbol characters
   - optionally link the admin to an external provider after that provider is configured

2. Authentication and OAuth
   - internal auth
   - optional multi-provider OAuth configuration such as Google, GitHub,
     Microsoft, X, Facebook, Amazon, Telegram, or a generic OAuth/OIDC provider
   - add providers one at a time with the `+ Provider` control; the wizard
     shows only the fields required for the selected provider type
   - the provider list is generated from configured provider rows, is not
     directly editable, and rejects duplicate provider types
   - optional admin pre-linking by explicit provider email or provider user ID

3. Private Gateway
   - configure final gateway key
   - select one of the installed private gateway skins
   - choose the final runtime listener TLS mode: generated private self-signed
     certificate or mounted cert/key files
   - reject `RoyBetty` as a final key
   - persist only `privategateway.main` plus a digest in install state

4. LLM service and CLI credential pools
   - create the selected LLM service ID
   - the wizard requires an explicit service ID, provider, and model
   - choose the scope for the LLM service, credential pool, and summarizer;
     `user` means the admin user created in the first wizard step
   - store an optional API key as encrypted secret `llm.<service_id>.api_key`
   - for CLI-backed providers without an API key, create the matching
     `llmCredentialOAuthProvider` service first and set the LLM service
     `credential_service_id` immediately
   - finalization is blocked until that credential pool contains at least one
     non-expired OAuth credential with an access token and refresh token
   - Gemini credential pools can be populated through either Gemini CLI or
     Agy/Antigravity server login; both write the same Gemini OAuth pool
   - do not create a credential pool for API-backed providers, or for CLI-backed
     providers when the user supplies an API key instead of OAuth login
   - relay login is not available during first install because no user relay
     exists yet; only server-side login and copy/paste login flows are valid
   - server-side login opens noVNC inside the installer dialog after the
     login desktop is ready; it must not open a separate browser tab or depend
     on the final AuthGateway session
   - assign this explicit service to the starter conversation agent

5. Summarizer service
   - create `summarizer_service`
   - point it to the selected LLM service
   - use this summarizer for no-tool package and skill review

6. Main flow and conversation
   - deploy `default.pawflow_agent:1.0.0` as `pawflow-agent`
   - pass the final Private Gateway service explicitly to the listener
   - create a starter conversation for the admin user
   - add `assistant` to `conv_agents` with the selected LLM service
   - set `active_resources.agent=assistant`

7. Variables and secrets
   - gateway material and optional provider API keys are stored through the
     encrypted secret store
   - installer state stores only secret IDs/references and non-secret digests
   - additional variables and secrets remain configurable from the normal
     system settings and services UI after installation

8. Relay image profiles
   - server workspace relays use the official `server-full` relay image profile
   - server execution relays use the official `server-minimal` relay image profile and are selected explicitly as relay parameter values by deployed flows
   - client relays are configured later by the user from selectable capabilities
   - expose `client-minimal`, language presets, desktop/browser presets, and advanced per-feature checkboxes
   - always include the required PawFlow relay base with Python runtime, FUSE mounts, and `/workspace`/`/cc_sessions`/`/filestore` mountpoints
   - generate a Dockerfile, build script, run/register script, and manifest from `config/relay_image_catalog.json`

9. Final review and smoke tests
   - final Private Gateway service is installed, enabled, and points to the
     encrypted final gateway secret
   - AuthGateway is installed with builtin login plus configured OAuth providers
   - admin user exists
   - selected LLM service exists and, for CLI providers, the OAuth pool has a
     usable non-expired credential
   - summarizer service resolves the selected LLM service
   - `pawflow-agent` is deployed, marked running, and has a live executor
   - the starter conversation exists and has `assistant` selected

10. Finalize
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
    "llm_services": {"primary": "selected_llm_service"},
    "summarizer_service": {"service_id": "summarizer_service"},
    "flows": {"main_instance_id": "pawflow-agent"},
    "conversation": {"conversation_id": "...", "agent": "assistant"}
  },
  "checks": {
    "final_private_gateway": true,
    "auth_gateway": true,
    "admin_user": true,
    "llm_service": true,
    "summarizer_service": true,
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

This removes the installer state file on restart and redeploys the bootstrap
installer if the server is not already finalized. It does not delete user data or
secrets; remove corrupted files manually only when recovery requires it.

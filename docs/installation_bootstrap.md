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
bash scripts/doctor-pawflow.sh --port PORT
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
bash scripts/install-pawflow.sh --port PORT
```

On Windows PowerShell with Docker Desktop Linux containers, use:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install-pawflow.ps1 -Port PORT -PullImages
```

On Linux hosts with AppArmor, the Bash installer also installs and loads the
PawFlow AppArmor profiles (`pawflow-mount`, `pawflow-relay`) into
`/etc/apparmor.d/` before starting the server, so pool and relay containers
are confined instead of running `apparmor=unconfined` (sudo may prompt once;
skip with `--skip-apparmor`). Hosts without AppArmor — Windows/macOS Docker
Desktop, WSL2, SELinux distros — are detected and skipped. See
`docs/deployment.md` for what the profiles allow and the manual commands.

When run from a checkout, the installer uses that checkout. When run as a
downloaded standalone script, it clones
`https://github.com/allcolor/PawFlow-Agents.git` into `~/pawflow-src`. Without a
version (and without `--from-source`), the installer resolves the latest
published release from GitHub and pulls `ghcr.io/allcolor/pawflow:VERSION` for
that tag, then uses the extracted `config/relay_image_catalog.json` to select
the relay image tags. Pass `--version VERSION` to pin a specific release;
relay images still use the catalog's independent `relay_image_version` tag
(`YYYY.mm.dd`). Use `--from-source` or
`--build-images` when you want local source builds instead of requiring the
published images. It always builds the local CLI LLM
image required before the first web installer opens:

- `ghcr.io/allcolor/pawflow:latest`, `ghcr.io/allcolor/pawflow:VERSION`, or
  `PAWFLOW_IMAGE` for the server
- `pawflow-claude-code:latest` for Claude Code, Codex, Gemini, and Antigravity
  CLI sessions and OAuth login containers
- `ghcr.io/allcolor/pawflow-relay-minimal:<relay_image_version>` for protected
  server-side minimal execution
- `ghcr.io/allcolor/pawflow-relay-dev:<relay_image_version>` for full server
  relay workspaces

After the builds, it creates persistent volumes under `~/pawflow`, starts
`pawflow-server`, and publishes the explicitly selected `PAWFLOW_PORT` on the same host
interface as `PAWFLOW_HOST`. Override `PAWFLOW_PUBLISH_HOST` only when Docker port
publishing must differ from the server bind host. The Docker entrypoint seeds missing
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
bash scripts/install-pawflow.sh --image ghcr.io/allcolor/pawflow:latest --port PORT
PAWFLOW_HOME=/srv/pawflow PAWFLOW_PORT=9443 bash scripts/run-pawflow-docker.sh
```

For release updates, use the installer itself. `--check-updates` queries GitHub
releases, `--self-update` refreshes the installer scripts from the latest release
zip, and a versioned `--pull-images` run recreates the existing server container
on the requested GHCR tag while preserving persistent data. Relay image tags are
resolved from the requested server image's catalog. Older PawFlow server/relay
image tags are removed after a successful update unless
`--keep-old-images` is set.

```bash
bash scripts/install-pawflow.sh --check-updates
bash scripts/install-pawflow.sh --self-update
bash scripts/install-pawflow.sh --version 1.0.0.prealpha.2 --port PORT --pull-images
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

### Require published server and relay images

To fail instead of falling back to source-built server and relay images, use:

```bash
bash scripts/install-pawflow.sh --pull-images
```

This still builds the local CLI LLM image from the repository Docker context.

## First Run Contract

On a fresh server data volume, PawFlow starts in bootstrap mode:

1. Deploy only the `PawFlow Installer` flow.
2. Store the temporary bootstrap gateway key as an encrypted global secret.
3. Protect the installer with a global `privateGateway` service referenced by
   the installer `httpListener`.
4. Generate and use a bootstrap self-signed TLS certificate.
5. Persist installer progress in a server-side install state file.
6. Never create a default user relay implicitly during bootstrap; the wizard
   exposes an explicit opt-in managed server relay step instead.
7. Expose an optional Voice I/O step that can install Supertonic as a local TTS
   service and Voicebox as a local STT service during first-run setup.
8. Use the standalone relay runtime image for server workspaces; local source
   mounts are opt-in development behavior only.

The server container receives the host Docker socket when available so server
workspace relays can be spawned later. The install script mounts
`/var/run/docker.sock` and adds the socket group ID as a supplemental group. If
the socket is unavailable, the server still installs, but server-side workspace
creation remains blocked until Docker socket access is provided.

`RoyBetty` is a temporary bootstrap key. The installer must force a replacement
before finalization. The bootstrap `privateGateway` service is disabled when the
installer finalizes.

The Voice I/O step is opt-in. When enabled, the installer creates a
`supertonicTTS` service named `supertonic_tts_service` for chat speech playback
and a `voicebox` service named `voicebox_service` for browser microphone
transcription. Both use the services' managed local runtime defaults, so the
webchat TTS/STT controls can discover them immediately after finalization.

Bootstrap HTTPS is mandatory. A first-run self-signed certificate is generated
under `data/system/ssl/bootstrap.crt` with key
`data/system/ssl/bootstrap.key`. The browser will show a trust warning until the
wizard installs final certificates. Final certificate configuration currently
supports either mounted cert/key files or a retained private self-signed
certificate generated by PawFlow. If the bootstrap certificate cannot be
generated, startup must fail loudly instead of falling back to plain HTTP.

The installer template is stored at
`data/repository/flows/global/default/pawflow_installer/versions/1.0.0.json`.
The browser UI is a single versioned flow asset at
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
and marks the installer deployment stopped for restart-safe restoration. Gateway
cookies are bound to the configured gateway secret references, so a cookie issued
for the bootstrap secret is not accepted by the final private gateway after the
secret reference changes.
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
   - provider guidance mirrors `docs/llm_providers.md`: API keys should default
     to direct `openai`/`anthropic` services unless CLI session behavior is
     required; Codex subscriptions use `codex-app-server`; Claude subscriptions
     use `claude-code-interactive`; Gemini subscriptions use
     `antigravity-interactive` by default
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

The initial server install does not create relays implicitly. During first-run
setup, the wizard includes an optional **Relay Server** step. When enabled, it
creates a managed server-side `relay` service in either global or admin-user
scope, generates the relay token on the server, starts the managed workspace
relay, and lets the first conversation select it as the default relay. The
wizard never asks the user to type a relay token for this managed path.

After the user reaches the webchat, the Relays panel should still offer:

- Install relay client
  - generate a short-lived provisioning token
  - download an installer script/package
  - check Docker and permissions on the client machine
  - optionally check VNC/noVNC prerequisites for local desktop control
  - build or pull the relay image
  - register and start the relay

- Install relay server
  - configure and launch a relay on the server host when explicitly requested

This keeps implicit bootstrap separate from user workspace onboarding while
allowing an explicit complete-server install path.

## Recovery

Do not permanently delete the installer flow. Finalization should disable and
hide it. Recovery should be possible through a local-only mechanism such as:

```bash
PAWFLOW_BOOTSTRAP_RESET=1 docker restart pawflow-server
```

This removes the installer state file on restart and redeploys the bootstrap
installer if the server is not already finalized. It does not delete user data or
secrets; remove corrupted files manually only when recovery requires it.

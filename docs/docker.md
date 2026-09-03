# Docker Containerization

PawFlow supports running agent code in Docker containers for isolation and security.

## Prerequisites

- Docker installed and running
- User in the `docker` group (Linux/WSL): `sudo usermod -aG docker $USER && newgrp docker`
- Windows requirements: WSL2 plus Docker Desktop with WSL integration enabled.
  The PawFlow install commands run inside the WSL distro, not in native Windows.
- Docker Desktop (macOS/Windows host daemon) or Docker Engine (Linux/WSL)

## 0. PawFlow Server in Docker

The recommended first install path is to run the PawFlow server from a Docker
image, then complete the bootstrap wizard in the browser.

### Complete from-scratch install

```bash
bash scripts/doctor-pawflow.sh --port PORT
bash scripts/install-pawflow.sh --port PORT
```

This is the recommended Linux, macOS, Windows-native Docker Desktop, and WSL2
install path. It first tries the prebuilt server and redistributable relay
images (`ghcr.io/allcolor/pawflow`, `ghcr.io/allcolor/pawflow-relay-minimal`,
and `ghcr.io/allcolor/pawflow-relay-dev`). Without `--version`, the installer
resolves the latest published release from GitHub and pulls the server image for
that exact tag; pass `--version VERSION` to pin a specific release. Relay images
are tagged independently by the extracted
`config/relay_image_catalog.json` `relay_image_version` (`YYYY.mm.dd`). If the
server image is available, it extracts the run scripts, relay image catalog, CLI
image Docker context, MCP bridge, PawFlow SDK, and relay Python package from
`/app` in that image into `PAWFLOW_RUNTIME_DIR` or `~/.pawflow/runtime/<tag>`,
then pulls the catalog-selected relay images.
If the server image is unavailable, it falls back to a source checkout and builds
from source. It always builds the shared CLI LLM image locally
(`pawflow-claude-code:latest` for Claude Code, Codex, Gemini, and Antigravity),
because Claude Code and Antigravity are not redistributed by PawFlow images. It
then creates persistent directories under `~/pawflow`, starts `pawflow-server`,
and exposes the port selected with `--port` / `PAWFLOW_PORT`.
The local build prefers Docker Buildx and BuildKit, then loads the result into
the local daemon. Published PawFlow server images bundle a pinned Buildx plugin
so an update launched from the admin UI uses the same fast builder as a normal
host-side install. On an older/manual environment without Buildx, the script
prints a warning and falls back to Docker's deprecated legacy builder; repeated
`Running in ...` and `Removed intermediate container ...` lines then describe
builder-internal layers and can make metadata-only Dockerfile steps much slower.
On macOS, the installer defaults Docker builds to `linux/amd64` unless
`PAWFLOW_DOCKER_PLATFORM` or `--platform` is set.
Use `bash scripts/install-pawflow.sh --native` when the PawFlow server itself
should run on the host instead of in the server Docker container. Native mode
still builds the CLI LLM image and prepares relay images with the same
pull/build fallback.
When `/var/run/docker.sock` is available on the host, the run script mounts it
into the PawFlow container so PawFlow can spawn server-side workspace relay
containers after installation. It also exports `PAWFLOW_HOST_APP_DIR` from the
host source checkout or extracted image-artifact directory so child CLI
containers can bind-mount PawFlow's MCP bridge files from host-visible paths
instead of container-only `/app/...` paths.

The server image keeps repository and config defaults outside the mounted
runtime directories. On container start, `docker/server-entrypoint.sh` seeds
missing files into `/app/data/repository` and `/app/config`, fixes ownership for
the persistent bind mounts, then drops privileges to the `pawflow` user before
starting the Python server. The `pawflow` user uses UID/GID `1000`, matching the
default first user on Linux/WSL bind mounts. This makes a fresh empty
`~/pawflow/data` volume usable without masking the installer flow templates
baked into the image.

The installer starts with a self-signed bootstrap certificate generated inside
the persistent data volume. Your browser will warn until the wizard configures
the final certificate, either by using provided cert/key files, generating an
ACME certificate such as Let's Encrypt, or keeping a self-signed certificate for
private deployments. The Compose healthcheck probes HTTPS with self-signed trust
disabled first, then falls back to plain HTTP for non-TLS local runs.

### Public HTTPS with Caddy

For a public VPS, keep PawFlow's container port off the public interface and let
Caddy terminate public HTTPS on ports `80` and `443`. PawFlow serves HTTPS on its
internal Docker/host port, usually `19990`; Caddy proxies to `127.0.0.1:PORT` and
keeps long-lived streaming responses open for SSE and tool output.

Example Caddy site block:

```caddyfile
the.host.name {
    reverse_proxy https://127.0.0.1:PORT {
        header_up Host {host}
        header_up X-Forwarded-Host {host}
        header_up X-Forwarded-Proto https
        header_up X-Forwarded-For {remote_host}
        header_up X-Real-IP {remote_host}
        flush_interval -1

        transport http {
            versions 1.1
            tls_insecure_skip_verify
            read_timeout 0
            write_timeout 0
        }
    }
}
```

Replace `the.host.name` with the public DNS name and `PORT` with the PawFlow
server port selected during install. `tls_insecure_skip_verify` is required when
Caddy talks to PawFlow's self-signed local HTTPS endpoint. If you later configure
PawFlow with a certificate trusted by the host, remove that line.

Firewall rules should expose only Caddy publicly. Allow `80/tcp` and `443/tcp`
from anywhere. Do not expose the PawFlow application port publicly; if Docker
containers need to reach it through the Docker bridge, allow that port only on
`docker0`.

Example UFW setup for PawFlow on port `19990`:

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow in on docker0 to any port 19990 proto tcp
sudo ufw enable
sudo ufw status
```

Expected shape:

```text
Status: active

To                         Action      From
--                         ------      ----
443/tcp                    ALLOW       Anywhere
80/tcp                     ALLOW       Anywhere
19990/tcp on docker0       ALLOW       Anywhere
443/tcp (v6)               ALLOW       Anywhere (v6)
80/tcp (v6)                ALLOW       Anywhere (v6)
19990/tcp (v6) on docker0  ALLOW       Anywhere (v6)
```

If PawFlow was started with a Docker port publication bound to all interfaces,
change the run configuration to bind it to localhost only, or restrict the host
firewall so external clients cannot connect directly to `PORT`. Public users
should access only `https://the.host.name` through Caddy.

The installer is protected by a temporary `privateGateway` service wired to the
bootstrap `httpListener`. The initial Private Gateway bootstrap key is:

```text
RoyBatty
```

The installer wizard forces the user to replace this key before finalizing the
installation. Finalization creates the persistent Private Gateway, builtin auth
gateway, admin user, selected LLM service, `summarizer_service`,
the `pawflow-agent` deployment, and a starter conversation with the `assistant`
agent selected.

### Versioned and source installs

```bash
bash scripts/install-pawflow.sh --version 1.0.0
bash scripts/install-pawflow.sh --from-source --version 1.0.0
bash scripts/install-pawflow.sh --from-source
bash scripts/install-pawflow.sh --check-updates
bash scripts/install-pawflow.sh --self-update
```

`--version VERSION` first tries the prebuilt `ghcr.io/allcolor/pawflow:VERSION`
server image; without it, the installer resolves the latest published release
from GitHub and uses that tag. After the server image is extracted, the installer reads
`config/relay_image_catalog.json` and pulls
`ghcr.io/allcolor/pawflow-relay-minimal:<relay_image_version>` and
`ghcr.io/allcolor/pawflow-relay-dev:<relay_image_version>`. `--from-source
--version VERSION` checks out the exact git tag and fails if it is missing.
`--from-source` without a version checks out `main`. All modes still build the
local CLI LLM image locally; image installs get that Docker context from the
pulled server image, while source installs use the repository checkout.

The doctor script validates host prerequisites before install. It detects
Linux, macOS, Windows shells, and WSL, checks Docker CLI/daemon access, WSL
health where applicable, source-install Git availability, Docker socket access
for first-run image builds, selected port availability, and prints OS-specific
installation instructions for missing prerequisites.

On Windows, PawFlow supports Docker Desktop Linux containers through the
PowerShell installer, and WSL2 with Docker Desktop WSL integration through the
Bash installer. The PowerShell doctor is a host prerequisite checker for both
paths:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/doctor-pawflow.ps1
powershell -ExecutionPolicy Bypass -File scripts/install-pawflow.ps1 -Port PORT -PullImages
```

It validates Docker Desktop, Linux-container mode, optional WSL2/WSL Docker
daemon access, and port availability, then explains how to install or enable the
missing pieces. Use the PowerShell installer from native Windows, or the Bash
installer from inside the WSL distro.

For updates, first check GitHub releases, optionally refresh the installer
scripts, then run the requested version. The update recreates the server
container on the new image while preserving `PAWFLOW_HOME` data and removes older
PawFlow server/relay image tags unless `--keep-old-images` or `-KeepOldImages`
is set:

```bash
bash scripts/install-pawflow.sh --check-updates
bash scripts/install-pawflow.sh --self-update
bash scripts/install-pawflow.sh --version 1.0.0.prealpha.2 --port 19990 --pull-images
```

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install-pawflow.ps1 -CheckUpdates
powershell -ExecutionPolicy Bypass -File scripts/install-pawflow.ps1 -SelfUpdate
powershell -ExecutionPolicy Bypass -File scripts/install-pawflow.ps1 -Version 1.0.0.prealpha.2 -Port 19990 -PullImages
```

### Agent-assisted install prompt

If the target machine already has Codex, Claude Code, Gemini CLI, or another
local coding agent, give it the prompt in:

```text
docs/prompts/install_with_agent.md
```

That prompt gets the machine to a running PawFlow bootstrap wizard. It does not
configure relays; relay onboarding happens later from the webchat.

### Complete install scenarios

These are the supported Docker install scenarios and their expected outcomes.

1. Fresh complete install on Linux, macOS, Windows native, or WSL
   - Run `bash scripts/doctor-pawflow.sh --port PORT`, then `bash scripts/install-pawflow.sh --port PORT`.
   - The installer first tries `ghcr.io/allcolor/pawflow:latest`,
     `ghcr.io/allcolor/pawflow-relay-minimal:latest`, and
     `ghcr.io/allcolor/pawflow-relay-dev:latest`, then builds any missing image
     from source. It always builds `pawflow-claude-code:latest` locally before
     starting the server.
   - The container starts as root only long enough to seed missing defaults and
     fix persistent directory ownership, then runs PawFlow as UID/GID `1000`.
   - An empty `~/pawflow/data` receives `data/repository`, so the installer flow
     is available even though `/app/data` is a bind mount.
   - Open `https://localhost:PORT/install`, accept the self-signed bootstrap
     certificate warning, enter the current gateway key `RoyBatty`, replace it,
     create the admin password, and finalize.
   - Expected result: `_private_gateway`, `_auth_gateway`, the selected
     `llmConnection`, `summarizer_service`, `pawflow-agent`, and a starter
     conversation with `assistant` are created; `_bootstrap_private_gateway` is
     disabled and the installer deployment is stopped.

2. Versioned install
   - Run `bash scripts/install-pawflow.sh --version VERSION`.
   - The script first pulls `ghcr.io/allcolor/pawflow:VERSION`, extracts its
     runtime artifacts and relay image catalog, then pulls the relay images at
     the catalog's `relay_image_version`. Use `--from-source --version VERSION`
     or `--build-images --version VERSION` when you want source builds for that
     tag instead of requiring published images. It always builds the CLI LLM
     image locally.
   - Expected result: the server image matches the requested PawFlow version;
     relay images match the catalog relay image version for that PawFlow build.

3. Versioned image update
   - Run `bash scripts/install-pawflow.sh --version NEW_VERSION --port PORT --pull-images` or the equivalent PowerShell command.
   - The installer pulls the requested server image and the catalog-selected
     relay image tags, extracts the matching runtime artifacts, rebuilds the
     local CLI LLM image, recreates the
     existing `pawflow-server` container, and keeps mounted data/config/certs/logs
     intact.
   - Expected result: `docker inspect pawflow-server` reports the requested
     server image, PawFlow runs on the requested version, and older PawFlow
     server/relay image tags are removed unless image cleanup was disabled.

4. Native server install
   - Run `bash scripts/install-pawflow.sh --native`.
   - The script prepares the same Docker runtime images, creates a local Python
     virtualenv, seeds `~/pawflow/data/repository` when missing, and starts
     PawFlow with `PAWFLOW_DATA_DIR=~/pawflow/data`.
   - Expected result: the web installer and first conversation path are the same
     as the container install, but `pawflow-server` is not a Docker container.

4. Restart before finalization
   - Restart `pawflow-server` while the installer is still incomplete.
   - The entrypoint seeds only missing files and does not overwrite existing
     user data. The install state remains incomplete and the installer flow is
     restored behind the bootstrap `privateGateway`.
   - Expected result: `/install` remains available over bootstrap HTTPS and can
     continue finalization without losing previous installer state.

4. Restart after finalization
   - Finalize the wizard, then restart `pawflow-server`.
   - `install_complete=true` prevents bootstrap redeployment. Normal deployed
     flows are restored, the installer remains stopped, and the bootstrap
     gateway remains disabled.
   - Expected result: the server opens through the final Private Gateway and
     login/webchat uses the configured admin user and starter conversation.

5. Docker socket unavailable
   - Run the install on a host where `/var/run/docker.sock` is missing or not
     writable.
   - The server installation may still complete, but server-side workspace relay
     creation is blocked until Docker socket access is provided.
   - Expected result: the doctor reports the socket issue when asked to require
     it, and relay creation is treated as a post-install host capability issue,
     not as a failed PawFlow server install.

6. Server-side relay after install
   - Provide Docker socket access and use the normal PawFlow UI/API to create a
     server workspace relay.
   - PawFlow uses the standalone relay dependency image selected during
     installation, such as `ghcr.io/allcolor/pawflow-relay-dev:latest`, and
     stages the relay runtime code from the PawFlow server image into the
     server data dir before bind-mounting it at `/opt/pawflow`.
   - The UI does not ask for a server workspace path. PawFlow allocates one under
     `data/runtime/relay/<user-or-global>/<conversation-id>` and mounts it into
     the relay container at `/workspace`.
   - Expected result: relay containers can start even when PawFlow itself runs in
     Docker, and relay image rebuilds are needed only for dependency image
     changes rather than PawFlow relay code changes.

7. Windows host prerequisites
   - Run `powershell -ExecutionPolicy Bypass -File scripts/doctor-pawflow.ps1`.
   - The doctor checks the required WSL2 + Docker Desktop WSL integration path:
     WSL distro availability, Linux-container mode, daemon access from WSL, and
     port availability.
   - Expected result: users fix host prerequisites, then run the normal Linux
     install script inside WSL instead of attempting a native Windows install.

## 1. Claude Code in Docker

Run Claude Code CLI inside a container instead of directly on the host.

### Build the image

```bash
# From the PawFlow repository root
bash docker/claude-code/build.sh
```

This creates `pawflow-claude-code:latest` (~500MB) with:
- Node.js 22 + Claude Code, Codex, Gemini, and Antigravity (`agy`) CLIs
- Python 3 + MCP bridge
- Git

The build resolves the latest published version of each agent CLI (Claude Code, Codex, Gemini) and pins it. The version is part of the npm-install layer's cache key, so a rebuild reinstalls a CLI only when a new version is actually published; otherwise it reuses the cached layer.

The script uses `docker buildx build --load` when Buildx is available. `--load`
places the single-platform BuildKit result in the local Docker image store, where
PawFlow's agent pools expect `pawflow-claude-code:latest`. A warned `docker build`
fallback remains for installations made with an older Docker CLI.

The build also stamps `/opt/pawflow/cli_versions.json` inside the image (`docker/claude-code/stamp_versions.sh`) with the versions the installed binaries actually report, Antigravity included. That file is the server's only way to know what is in the image.

### Rebuild from the admin gear menu

**Server settings (gear) → Updates** shows, per component, the installed version
versus the published one:

| Component | Installed | Published |
|---|---|---|
| PawFlow server | `core.__version__` | latest GitHub release |
| `pawflow-relay-dev` / `pawflow-relay-minimal` | local image tags | newest date tag published on GHCR |
| Claude Code / Codex / Gemini | `/opt/pawflow/cli_versions.json` in the tools image | npm registry `latest` |
| Antigravity | same file | *none* |

The release tag (`1.0.0-beta.35`) and the packaged version (`1.0.0b35`) are
compared under PEP 440, so they read as equal rather than as a permanent
pending update.

**"Published" means published.** The relay row asks the registry — an anonymous
bearer token, then `/v2/<repo>/tags/list` — and keeps only date-shaped tags,
since `latest` names no version. `relay_image_version` from the shipped catalog
is the *fallback*, for a server that is offline or rate-limited, and is also
reported as `expected`. Reading the catalog as the published version answered a
different question — what this server wants — and read as "unknown" whenever the
catalog was stale.

The catalog itself is read from `/app/default-config`, the pristine copy baked
into the image, not from `/app/config`. The latter is a host bind mount that
`docker/server-entrypoint.sh` seeds with `cp -a -n`, so a file that gained a key
after the operator's first install keeps the old copy there for good:
`relay_image_version` did not always exist, and any install predating it
reported an empty published version forever. Shipped, versioned data comes from
the image; `/app/config` stays for what the operator edits.

The same dialog rebuilds the agent CLI tools image. A successful build launches
a detached restart-only helper, then the page waits until a different PawFlow
process answers `/health` before it reloads. This clears every warm CLI
container so the next session cannot keep using the previous image. The
confirmation reports how many agent turns are running because the PawFlow
restart kills them. Two modes:

- **Rebuild** — same resolution as `build.sh`: a CLI is reinstalled only if npm
  published a new version.
- **Force (`--no-cache`)** — full rebuild, several minutes. Antigravity is
  installed from an unversioned `install.sh`, so it has no version signal at
  all: forcing is the *only* way to pick up a new Antigravity build. It also
  recovers from a poisoned layer cache.

Both require the `admin` role, run in a background thread, and stream the full
build → restart progression to a dedicated dialog over the `cli_image_build`
SSE event. One rebuild runs at a time; a concurrent request is refused with HTTP
409 rather than starting a second `docker build` on the same tag. A failed build
never starts the restart helper. Every trigger is logged with the requesting
user, since `docker build` against the host socket is effectively root on the
machine.

Actions: `admin_check_updates` (read-only) and `admin_rebuild_cli_image`
(`force: bool`), implemented in `core/update_manager.py`.

### Enable in service config

In the admin panel, edit your `claude_code_llm_service`:
- **containerize**: `true`
- **docker_image**: `pawflow-claude-code:latest`
- **docker_cpu_limit**: `2` (cores)
- **docker_memory_limit**: `2g`

### Security

When containerized, Claude Code:
- Has NO access to the host filesystem (tools via MCP only)
- Has a read-only root filesystem (`/tmp` writable)
- Cannot escalate privileges
- Has CPU and memory limits enforced
- Network is restricted to MCP relay + Anthropic API

Session data (memories, CLAUDE.md) persists in `data/claude_sessions/`.

## 2. Relay Docker Mode

Run filesystem exec/git commands inside a container on the user's machine.

### Python relay

```bash
python tools/pawflow_relay.py \
  --dir /path/to/project \
  --allow-exec \
  --docker-image python:3.12-slim
```

### PawCode CLI

Docker image is configured programmatically:
```python
relay = RelayThread(server_url, token, username, directory,
                    docker_image="python:3.12-slim")
```

### What happens

- A persistent Docker container starts at relay launch
- The project directory is mounted at `/workspace`
- All `exec` and `git` commands run inside the container
- The container is automatically removed when the relay stops
- The user's machine is protected from arbitrary code execution

### Recommended images

| Image | Size | Use case |
|-------|------|----------|
| `pawflow-relay-dev:latest` | ~3-4GB | Full dev environment (all languages) |
| `python:3.12-slim` | ~150MB | Python-only projects |
| `node:22-slim` | ~200MB | Node.js/TypeScript-only projects |
| `ubuntu:24.04` | ~80MB | General purpose (no dev tools) |

### Build the full dev image

```bash
bash docker/relay-dev/build.sh
```

Includes: Python 3, Node.js 22 + TypeScript, Rust, Go, C/C++ (gcc/g++/cmake),
Java 21, Ruby, PHP, Perl, Lua, desktop automation, Chromium, GIMP/Inkscape,
network tools, git, make, cmake, curl, wget, jq, sqlite, and ssh.

Kotlin, .NET, Zig, golangci-lint and heavier GUI/media applications such as
Blender, LibreOffice, VLC and Audacity stay available as optional relay image
features for manual/profile-based builds instead of the default published
`pawflow-relay-dev` image.

The image does not embed PawFlow relay code. Server-side relays stage the relay
runtime from the PawFlow server image into the server data dir and bind-mount it
at `/opt/pawflow`, while desktop/local relays use their own packaged runtime
mounts.

#### Relay script manifests

Every module that `tools/fs_actions.py` imports must be listed in each relay
script manifest, or an existing relay receives a facade it cannot import:

| Manifest | Role |
|---|---|
| `_RELAY_SCRIPT_FILES` in `services/_relay_ws.py` | files the server pushes to a connected containerized relay when its script hash differs |
| `_RELAY_SCRIPTS` in `pawflow_relay/_relay_actions.py` | files a relay accepts from `update_scripts` and hot-reloads; also the dev-mount list of the `pawflow-relay` CLI |
| `pawflow_relay/_thread_docker.py` | dev mounts of a source checkout into `/opt/pawflow` |
| `scripts/generate-relay-image.py`, `scripts/build-mcp-client-installer.py`, `pawflow-relay-desktop/scripts/prepare-runtime.js` | runtime copies baked into relay images, the MCP client installer and Relay Desktop |

`tests/test_relay_script_manifests.py` fails when a manifest falls behind the
facade. Because an already-installed relay keeps its old accept list until it
is upgraded, `fs_actions.py` treats `fs_http.py` and `fs_archive.py` as
optional: when the sibling file is absent every base action keeps working and
only `http_fetch`, `http_fetch_to_file` and `extract_zip_subtree` fail with an
explicit "upgrade the relay runtime" error. A sibling that exists but fails to
import still raises its own error.

### Rebuild relay images from the admin gear menu

**Server settings (gear) → Updates → Relay images** rebuilds the two relay
images from the sources shipped inside the server image, using the same build
contexts as `.github/workflows/docker-publish.yml`:

| Image | Context | Dockerfile |
|---|---|---|
| workspace relay (`server_relay_image`) | repository root | `docker/relay-dev/Dockerfile` |
| minimal relay (`server_relay_minimal_image`) | `docker/relay-generated/server-minimal` | generated |

The minimal image has no Dockerfile until the catalog is rendered, so the
rebuild runs `scripts/generate-relay-image.py --profile server-minimal` first
and aborts before `docker build` if generation fails. The tag built is the one
`global_parameters.json` names for that relay kind — a rebuild can never land on
a tag nothing spawns.

**Building alone changes nothing for relays already running.** The admin rebuild
therefore continues automatically: each managed relay is replaced in turn by
`ServerRelayManager.recreate()`, then PawFlow itself is restarted by a detached
restart-only helper. The page shows every phase and waits for a different
`/health` instance before reloading. The workspace directory, the kind volume,
the `pawflow_home_<relay_id>` volume, the relay id, the registered relay service
and the conversation bindings all survive — unlike `destroy()`, which deletes
the volume and the workspace and would take the user's work with it. A failed
build or relay recreation stops the workflow before the PawFlow restart, and a
failed respawn puts the previous metadata back instead of dropping the relay
from the store.

The sweep is sequential (each relay can carry gigabytes) and does not stop on a
failure: the remaining relays are still moved and the failures are reported per
relay. Each relay is briefly unavailable while its container is replaced.

The actions require the `admin` role, run in background threads, and stream
progress over the `relay_image_build` and `relay_restart` SSE events. One relay
build and one restart at a time; concurrent requests get HTTP 409. Triggers are
logged with the requesting user. The standalone **Restart server relays** action
remains available for recovery, without rebuilding an image or restarting
PawFlow.

Actions: `admin_rebuild_relay_image` (`image: relay-dev|relay-minimal`,
`force: bool`) and `admin_restart_relays`, implemented in
`core/update_manager.py`.

The workspace relay builds with the repository root as its context, which on a
deployed server is `/app` — including the mounted data dirs. `.dockerignore`
excludes `data/runtime` and `data/system`, so relay workspaces and system state
are not sent to the Docker daemon; a test pins those two exclusions.

Two limits to know: a pip-installed server has neither `docker/` nor `scripts/`,
so the rebuild refuses cleanly ("Build context not found"); and the relay images
are large, so a rebuild without pruning can fill the host disk — PawFlow does not
check free space before starting.

### Update the server itself from the admin gear menu

**Server settings (gear) → Updates → PawFlow server** re-runs this server's own
deployment, which restarts it.

A container cannot replace itself: `docker restart` comes back on the *old*
image, and `docker rm -f <self>` kills the process issuing the command. The work
is therefore handed to a short-lived detached container (`pawflow-updater`)
that has the Docker socket and survives the server's death.

Two deployment shapes can update themselves, and the server detects which one it
is. **A compose stack** runs:

```bash
docker compose version                 # abort here if compose is missing
git pull --ff-only                     # only when asked, and only fast-forward
docker compose pull --ignore-buildable || docker compose pull || true
docker compose up -d --build
```

Everything that can fail harmlessly runs before anything that stops the server.
`--ignore-buildable` covers a deployment that builds from source and has no
image to pull; `up -d --build` then covers both shapes and recreates only what
changed.

**An installer deployment** — what `scripts/install-pawflow.sh` produces, and
the common case — is not a compose stack at all: the installer ends on
`scripts/run-pawflow-docker.sh`, a plain `docker run`. It runs:

```bash
git pull --ff-only     # only when asked, and only fast-forward
PAWFLOW_IMAGE=... bash scripts/install-pawflow.sh --port <port> --pull-images
```

The installer script itself is resolved with a fallback: the copy in the
install directory when it carries one, else the copy the updater image ships
at `/app/scripts/install-pawflow.sh`. Artifact directories extracted by older
installers only received `run-pawflow-docker.sh`, so without the fallback the
updater died on `bash: scripts/install-pawflow.sh: No such file or directory`
(exit 127) after the server was already committed to dying; the preflight now
refuses up front when neither copy exists.

The helper for this path runs in the currently installed PawFlow image, which
is already local and contains Bash plus the static Docker CLI. It therefore has
no package-manager bootstrap and does not depend on Alpine package-repository
DNS before the real image pull. A configured `PAWFLOW_SERVER_UPDATE_IMAGE` (or
`server_update_image`) still overrides that image for custom deployments.

The start script is the source of truth for how a PawFlow container is started
and already recreates an existing container in place
(`PAWFLOW_RECREATE_CONTAINER` defaults to 1), so the update calls it rather than
reproducing its `docker run` from an inspect dump — a second source of truth
would drift. The image to pull is the repository of the running image plus the
latest GitHub release tag, which is exactly how the publish workflow tags it.

**The host-side files are refreshed too.** `install-pawflow.sh` copies a set of
artifacts out of the image onto the host (`extract_image_artifacts`): the start
script itself, `install-pawflow.sh`, `doctor-pawflow.sh`,
`config/relay_image_catalog.json`,
`docker/apparmor`, `docker/claude-code`, `docker/pawflow_sdk`,
`tools/mcp_bridge.py`, `core/tool_json.py` and `pawflow_relay`. These run
*outside* the container, so pulling a new image does not touch them. Until they
were refreshed here, every update from the UI left them frozen at whatever
version had last been installed from the command line — including the start
script the update itself runs. The list lives in
`core/installer_deployment.py` (`IMAGE_ARTIFACTS`), a test asserts the installer
still extracts every entry, and the copy is a `docker cp` out of a throw-away
container, exactly as the installer does it.

**Into the new version's directory.** The installer lays these out per version,
in `~/.pawflow/runtime/<tag>`, so the update extracts into the *new* tag's
directory and runs the start script from there. `PAWFLOW_SOURCE_DIR` points at
it, so `org.pawflow.host-app-dir` moves with it and the next update finds what
this one wrote. The previous version's directory is left intact to fall back
to, and no directory ever claims a version it does not hold. An install that
does not follow that naming — `--runtime-dir` — is refreshed in place instead:
the operator chose that path, and a tag-named sibling would be a directory
PawFlow invented behind their back. A git checkout is never touched at all;
`git pull` is what moves its files, and copying image contents over a tracked
tree would dirty it.

The refresh sits between the pull and the start script — the same window, for
the same reason. **A failed refresh aborts**, leaving a running server on its
old version, because the files it did not manage to write include the start
script about to run. The dialog offers a checkbox to continue anyway; taking it
starts the new server image with the host-side files of the version it is
replacing, and says so on stderr. When the refresh failed on the *first*
artifact — the start script itself — the new directory exists and is empty, so
forcing past the failure hands over to the directory being replaced, which does
carry a working script.

The updater runs as root, so what it extracts is chowned back to the uid/gid the
deployment runs as — otherwise the operator's next command-line install could
not overwrite it. That pair is read from `PAWFLOW_RUN_UID`/`PAWFLOW_RUN_GID`
when both are present, and off the install directory being replaced when they
are not: a container created by an older start script carries only the first,
and requiring both skipped the `chown` silently, on an update whose log
otherwise reads clean. The step runs whatever the refresh did, not only on the
success path. Its absence is what `unlinkat: permission denied` from
`install-pawflow.sh` looks like from the command line; the installer now checks
the runtime directory's ownership up front and prints the `chown` that takes it
back.

The environment of the running container is replayed, so the deployment keeps
its bootstrap gateway key, its uid/gid and its relay images. Two exceptions:
`PAWFLOW_BOOTSTRAP_RESET` is forced empty — a fresh install may have been
started with the reset on, and replaying it would wipe a working server's
installer state — and the in-container paths are dropped, since the new
container sets its own.

**The directory is detected, not configured.** Compose stamps every
container it creates with `com.docker.compose.project.working_dir` (a *host*
path), and `core/compose_deployment.py` finds this container's own id — from
`/proc/self/mountinfo`, falling back to cgroups and only then to the hostname,
which an explicit `hostname:` would poison — then reads that label.

A `docker run` stamps nothing, so `run-pawflow-docker.sh` now writes the
equivalent itself: `org.pawflow.deployment`, `org.pawflow.host-app-dir`,
`org.pawflow.home`, `org.pawflow.port` and `org.pawflow.network-mode`.
`core/installer_deployment.py` reads those first and falls back to
`PAWFLOW_HOST_APP_DIR` and `docker inspect` — which is what makes an install
that is *already running* updatable, instead of only the next one. The port and
any extra arguments come off the command line (`cli.py start --port N`); they
are not in the environment, and reading it alone would silently move the server
to the default port. A container with neither labels nor those variables is
refused cleanly instead of guessed at.

The directory is bind-mounted into the updater **at its own host path**: compose
resolves the relative paths in the compose file (`./data`, `build: .`) against
it and hands the result to the daemon as host paths, and the start script does
the same with `$PAWFLOW_HOME`, so mounting it anywhere else would silently
produce wrong bind mounts.

Before anything is launched, a preflight runs the updater image once: it proves
the image exists, that it carries what the update needs (`docker compose`, or
Bash plus a working Docker CLI and `scripts/run-pawflow-docker.sh` in the install
directory), and that the
directory really is where the container says it is — the server cannot stat a
host path itself. It also reports whether the directory is a git checkout, which
is what gates the optional `git pull` checkbox.

**A restart kills every running agent turn.** That is the same cost as running
`docker compose up -d` by hand, so the dialog names it — including how many
turns are in flight — and lets the operator decide. Nothing refuses on their
behalf.

**The panel waits for a different process, not for an answer.** `/health`
returns `{ok, version, instance}`, where `instance` is a per-process id minted
at startup. The panel reads it *before* the update, then polls until a
different `instance` answers and only then reloads — the page it is running was
served by the old process. Waiting for any answer at all ended on the first
poll whenever the updater failed before stopping anything: the server never went
away, the page reloaded onto the version it started from, and nothing said the
update had not happened. While the original server still answers, the panel also
polls the fixed `pawflow-updater` container; a non-zero exit is shown immediately
with its bounded log instead of being hidden until the deadline. After ten
minutes the panel gives up and names which
of the two failures occurred — the server stopped answering and never came back
(the new container failed to start), or it never stopped answering (the updater
failed before touching it) — and prints `docker logs pawflow-updater`.

**The image is proved usable before the old server is destroyed.** The start
script probes the new image twice — does it carry the Docker CLI, can it reach
the mounted daemon — and both probes used to run *after* `docker rm -f` on the
server container. An image that failed either left the operator with no server
at all and a message about how to rebuild one. The probes are read-only, so
they now run first: the destruction is the last thing that happens before
`docker run`.

**Disk is reclaimed inside PawFlow's own repositories, and nowhere else.**
The last step of every updater script (`_IMAGE_CLEANUP_SH`,
`core/update_manager.py`) removes the PawFlow image tags this install stopped
using — an instance that only ever updated from the UI used to keep every
version it had run, at a couple of gigabytes each. What it may touch is
bounded twice: the ref must match one of `PRUNABLE_REPOSITORIES`, and it must
not be referenced by any container the daemon reports (`docker ps -a`), which
is what spares a relay image no container happens to be running at the moment
an update ends. Untagged layers of those repositories — `repo:<none>`, which
cannot be removed by name — are removed by image id inside the same filter.
There is deliberately no `docker image prune`: that filter is daemon-wide, and
it made updating PawFlow delete the untagged layers of every other project
sharing the host's Docker daemon. Every removal tolerates its own failure: a
cleanup that fails costs disk, and must never turn a successful update into a
failed one.

**Update inputs are fail-fast and confined to the deployment.** A requested git
pull must succeed; its exit status is not hidden behind a later Compose command.
Artifact refresh resolves and validates every destination beneath the deployment
root without following a destination symlink outside it. A failed validation or
copy stops before server replacement.

**Server replacement rolls back on failed health.** The script records the old
image before removal, starts the requested image, and waits for its health check.
If startup or health fails, it removes the failed container and recreates the
old image with the same runtime arguments. The update still exits non-zero and
keeps its logs; rollback restores service, it does not report success.

**Managed relays are removed, best-effort.** Before recreating the server, the
start script drops the `pawflow-relay-srv-*` / `pawflow-relay-min-*` containers
so they come back with the current runtime code; their home volumes and
workspace directories are untouched. Each is removed on its own and a failure
is a loud warning, never fatal. A relay the daemon refuses to kill (`could not
kill container: tried to kill container, but did not receive an exit event`)
once aborted the whole script under `set -e` — after the pull, before
`docker rm -f` on the server — leaving the operator with a server still on the
old image and a half-killed relay. The server recreation is what the update
exists for, and it must be reached. A relay left behind is recovered by the
service itself — see *A managed container that dies is respawned* in
`docs/relay_client.md`.

If the update fails, `pawflow-updater` is kept (not `--rm`), so
`docker logs pawflow-updater` still explains why.

The updater image defaults to `docker:cli` and is overridable with
`server_update_image` in `global_parameters.json` or
`PAWFLOW_SERVER_UPDATE_IMAGE`, for hosts that pull from a local mirror.

Action: `admin_server_update_check` (read-only preflight) and
`admin_update_server` (`pull_source: bool`), both admin-only.

### Building a custom image

```dockerfile
FROM pawflow-relay-dev:latest
# Add project-specific tools
RUN pip install numpy pandas torch
RUN npm install -g @angular/cli
```

## WSL2: Clock Drift and Audio Sync

> **Important for Windows/WSL2 users with desktop audio enabled.**

WSL2's kernel clock can drift significantly from the Windows host clock (up to 10-20%). This causes:
- Desktop audio playing too fast/slow relative to the video stream
- The AudioWorklet rate measurement (`curStep` in browser console) deviating from 1.0

PawFlow's audio pipeline automatically compensates via adaptive rate measurement, but this introduces pitch shift proportional to the drift.

**Fix — install `chrony` in your WSL2 distro (not inside Docker):**

```bash
# In WSL2 terminal (not in a Docker container)
sudo apt install -y chrony
```

Chrony starts automatically and keeps the clock synced with NTP. Verify:

```bash
# Should show ~0 seconds fast/slow
chronyc tracking | grep "System time"

# Should show exactly 10 second difference
date +%s; sleep 10; date +%s
```

After fixing, `curStep` in the browser console audio stats will converge to `1.00000` — no pitch shift, perfect sync.

> Native Linux hosts and macOS (Docker Desktop) are not affected — their clocks are hardware-synced.

## WSL2: Reclaim Docker Build Cache Space

Docker build cache inside WSL2 can grow very large while building PawFlow relay or CLI images. `docker builder prune` frees the space inside the Linux filesystem, but Windows does not automatically shrink the WSL `ext4.vhdx` file. Windows Settings may still show the Ubuntu app using hundreds of GB until the VHDX is compacted.

First clean Docker from inside the WSL distro:

```bash
docker builder prune -a
docker system df
```

Then stop WSL from PowerShell:

```powershell
wsl --shutdown
```

Find the Ubuntu VHDX:

```powershell
Get-ChildItem "$env:LOCALAPPDATA\Packages\CanonicalGroupLimited.Ubuntu24.04LTS_*\LocalState\ext4.vhdx"
```

Compact it with `diskpart`:

```powershell
diskpart
```

Inside `diskpart`:

```text
select vdisk file="C:\Users\<user>\AppData\Local\Packages\CanonicalGroupLimited.Ubuntu24.04LTS_<suffix>\LocalState\ext4.vhdx"
attach vdisk readonly
compact vdisk
detach vdisk
exit
```

Do not use Windows Settings **Reset** for the Ubuntu app: it deletes the distro data. If `attach vdisk readonly` fails, ensure Docker Desktop and all WSL terminals are closed, then run `wsl --shutdown` again.

## WSL2: Launching PawCode with a WSL-Resident Project

When PawCode runs on Windows (`python -m pawflow_cli ...`) but the project lives inside a WSL distro, you may pass the path in any of these forms:

- `\\wsl$\<distro>\home\<user>\<project>` (Explorer/UNC)
- `\\wsl.localhost\<distro>\home\<user>\<project>` (newer Windows builds)
- `C:\...` (Windows drive)

`pawflow_relay.utils.translate_path` normalises all of them before passing the bind-mount to `wsl docker`:

| Input                                   | Bind-mount target       |
|-----------------------------------------|-------------------------|
| `C:\foo\bar`                            | `/mnt/c/foo/bar`        |
| `\\wsl$\Ubuntu-24.04\home\qan\PawFlow`  | `/home/qan/PawFlow`     |
| `\\wsl.localhost\Ubuntu\home\qan`       | `/home/qan`             |

> The `\\wsl$\...` form is a Windows-side network path; it is **not** visible from inside the WSL Docker daemon. Without stripping the `\\wsl$\<distro>\` prefix, Docker silently creates an empty directory for the bind-mount and `/workspace` appears blank to the relay.

### Git: trust the WSL-owned repo once

Since git 2.35.2 (CVE-2022-24765), git on Windows refuses to operate on a repo whose files are owned by a different uid — which is exactly what happens when you run the PawFlow server from `\\wsl$\<distro>\...`. You'll see:

```
fatal: detected dubious ownership in repository at '//wsl$/<distro>/<path>'
```

PawFlow's conversation-snapshot git (`core.conversation_store`) already passes `-c safe.directory=*` so internal snapshots work out of the box. For manual `git` calls and for the project repo itself, add the path to your global safe-directory list **once**:

```powershell
# PowerShell — single-quoted so %(prefix) is passed literally to git
git config --global --add safe.directory '%(prefix)///wsl$/Ubuntu-24.04/home/<user>/<project>'
```

`%(prefix)//` is git's own syntax for UNC paths; don't expand it.

## 3. Exec Shell Selection

The `exec` action supports a `shell` parameter:

| Shell | Description |
|-------|-------------|
| `bash` | Git Bash (Windows) or system bash |
| `powershell` | PowerShell |
| `cmd` | Windows CMD |
| `python` | Python interpreter |
| `node` | Node.js |
| `docker-python` | Python in ephemeral Docker container |
| `docker-node` | Node.js in ephemeral Docker container |
| `docker-bash` | Bash in ephemeral Docker container |

Docker shells (`docker-*`) create a new container per command. For persistent containers, use the relay `--docker-image` flag instead.

### Optional RTK rewrite

The `pawflow-relay-dev` image does not install RTK by default. When
`PAWFLOW_USE_RTK` is truthy (`1`, `true`, `yes`, `on`) and the selected relay target has the
`rtk` binary, PawFlow uses RTK on compatible relay-backed tools: `bash` and
`run_tests` run `rtk rewrite <command>` before execution, while `read` uses
`rtk read`. `grep` and `glob` stay on the native relay implementations because
RTK output does not preserve PawFlow's grep/glob response semantics reliably.
If the variable is not truthy, RTK is unavailable, or RTK cannot handle a
request, the native tool path runs unchanged.

## 4. ExecuteScript Containerization

Run flow scripts in Docker for isolation.

### Config

In the flow task config:
- **containerize**: `true`
- **docker_image**: `pawflow-relay-dev:latest`
- **docker_timeout**: `120` (seconds)

### Script API (containerized)

```python
# Variables available in the script:
content    # FlowFile content (str)
attributes # FlowFile attributes (dict)
fs         # PawFlow filesystem SDK
tools      # PawFlow tools SDK

# Filesystem operations (via MCP → tool relay)
data = fs.read_file("config.json")
fs.write_file("output.txt", "processed")
fs.exec("python process.py")
files = fs.list_dir("src/")

# Any PawFlow tool
schema = tools.get_schema("generate_image")
result = tools.call("generate_image", prompt="a logo", width=256)

# Set result (modifies FlowFile content)
result = json.dumps({"status": "done"})
```

### How it works

1. FlowFile content + attributes serialized to JSON
2. Docker container starts with `pawflow-relay-dev` image
3. PawFlow SDK (`from pawflow import fs, tools`) connects to tool relay
4. User script executes with full tool access but no host access
5. Result written back to FlowFile

## 5. PawFlow SDK

The `pawflow` Python module is pre-installed in all PawFlow containers.
It provides synchronous access to PawFlow tools via the tool relay WebSocket.

```python
from pawflow import fs, tools

# Works identically in:
# - ExecuteScript (containerized)
# - Custom Docker relay scripts
# - Any container with PAWFLOW_TOOL_RELAY_URL set
```

## 6. Security Model

| Mode | Host access | Network | Isolation |
|------|------------|---------|-----------|
| Native (default) | Full | Full | None |
| Relay Docker | Mounted dir only | Full | Container |
| Claude Code Docker | MCP tools only | MCP + API | Container |
| docker-* shells | Mounted dir only | None | Container |

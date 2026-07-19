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

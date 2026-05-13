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

### Pull and run the published image

```bash
bash scripts/doctor-pawflow.sh
bash scripts/install-pawflow.sh
```

This pulls `ghcr.io/allcolor/pawflow:latest`, creates persistent directories
under `~/pawflow`, starts `pawflow-server`, and exposes `https://localhost:9090`.
When `/var/run/docker.sock` is available on the host, the run script mounts it
into the PawFlow container so PawFlow can spawn server-side workspace relay
containers after installation.

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

The installer is protected by a temporary `privateGateway` service wired to the
bootstrap `httpListener`. The initial Private Gateway bootstrap key is:

```text
RoyBetty
```

The installer wizard forces the user to replace this key before finalizing the
installation. Finalization creates the persistent Private Gateway, builtin auth
gateway, admin user, selected LLM service, `summarizer_service`,
the `pawflow-agent` deployment, and a starter conversation with the `assistant`
agent selected.

### Build from source

```bash
bash scripts/doctor-pawflow.sh --source
bash scripts/install-pawflow.sh --source
```

This checks out the PawFlow repository, builds the server image locally, and
starts the same persistent container layout.

The doctor script validates host prerequisites before install. It detects
Linux, macOS, Windows shells, and WSL, checks Docker CLI/daemon access, WSL
health where applicable, source-install Git availability, Docker socket access
for first-run image builds, selected port availability, and prints OS-specific
installation instructions for missing prerequisites.

On Windows, PawFlow requires WSL2 and Docker Desktop with WSL integration. The
PowerShell doctor is only a host prerequisite checker before entering WSL:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/doctor-pawflow.ps1
```

It validates WSL2, Docker Desktop, Linux-container mode, WSL Docker daemon
access, and port availability, then explains how to install or enable the
missing pieces. After those requirements are satisfied, run the Linux install
commands from inside the WSL distro.

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

1. Fresh published-image install on Linux or WSL
   - Run `bash scripts/doctor-pawflow.sh`, then `bash scripts/install-pawflow.sh`.
   - The container starts as root only long enough to seed missing defaults and
     fix persistent directory ownership, then runs PawFlow as UID/GID `1000`.
   - An empty `~/pawflow/data` receives `data/repository`, so the installer flow
     is available even though `/app/data` is a bind mount.
   - Open `https://localhost:9090/install`, accept the self-signed bootstrap
     certificate warning, enter the current gateway key `RoyBetty`, replace it,
     create the admin password, and finalize.
   - Expected result: `_private_gateway`, `_auth_gateway`, the selected
     `llmConnection`, `summarizer_service`, `pawflow-agent`, and a starter
     conversation with `assistant` are created; `_bootstrap_private_gateway` is
     disabled and the installer deployment is stopped.

2. Fresh source build install
   - Run `bash scripts/doctor-pawflow.sh --source`, then
     `bash scripts/install-pawflow.sh --source`.
   - The script clones the repository, builds the PawFlow server image, starts
     the same persistent layout, and follows the same browser finalization flow
     as the published image.
   - Expected result: source-built server behavior matches the published image,
     including repository/config seeding and UID/GID `1000` runtime ownership.

3. Restart before finalization
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
   - PawFlow uses the standalone `pawflow-relay-dev:latest` image with embedded
     `/opt/pawflow/pawflow_relay_launcher.py` and `pawflow_relay` package code by
     default. Live source-code mounts are used only when
     `server_relay_mount_code` is explicitly enabled for local development.
   - Expected result: relay containers can start even when PawFlow itself runs in
     Docker, because the host daemon does not need to bind-mount `/app/tools`
     from inside the server container.

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
- Node.js 22 + Claude Code CLI
- Python 3 + MCP bridge
- Git

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
Java 21 + Kotlin + Scala, C# (.NET 9), Ruby, PHP, Perl, Lua, Zig,
git, make, cmake, curl, wget, jq, sqlite, ssh.

The image also embeds `/opt/pawflow/pawflow_relay_launcher.py` and the
`pawflow_relay` package. Server-side relays use that embedded code by default,
which is required when the PawFlow server itself runs in Docker and talks to the
host daemon through `/var/run/docker.sock`. Local development can opt into live
code mounts by setting the `server_relay_mount_code` system parameter to a
truthy value.

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

The `pawflow-relay-dev` image includes the `rtk` CLI. When `PAWFLOW_USE_RTK`
is truthy (`1`, `true`, `yes`, `on`) and the selected relay target has the
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

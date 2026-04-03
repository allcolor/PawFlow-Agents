# Docker Containerization

PawFlow supports running agent code in Docker containers for isolation and security.

## Prerequisites

- Docker installed and running
- User in the `docker` group (Linux/WSL): `sudo usermod -aG docker $USER && newgrp docker`
- Docker Desktop (Windows/macOS) or Docker Engine (Linux)

## 1. Claude Code in Docker

Run Claude Code CLI inside a container instead of directly on the host.

### Build the image

```bash
# From the PyFi2 root
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

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

| Image | Use case |
|-------|----------|
| `python:3.12-slim` | Python projects |
| `node:22-slim` | Node.js/TypeScript projects |
| `ubuntu:24.04` | General purpose |
| Custom | Build your own with project-specific tools |

### Building a custom image

```dockerfile
FROM python:3.12-slim
RUN pip install numpy pandas requests
RUN apt-get update && apt-get install -y git curl
```

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

## 4. Security Model

| Mode | Host access | Network | Isolation |
|------|------------|---------|-----------|
| Native (default) | Full | Full | None |
| Relay Docker | Mounted dir only | Full | Container |
| Claude Code Docker | MCP tools only | MCP + API | Container |
| docker-* shells | Mounted dir only | None | Container |

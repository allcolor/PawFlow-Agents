# PawFlow Deployment Guide

PawFlow supports three execution modes, auto-detected at startup:

| Mode | Detection | Container spawning | Communication |
|------|-----------|-------------------|---------------|
| `local` | No Docker available | None | Subprocess |
| `docker` | Docker socket available | `docker run` / `docker exec` | Subprocess + pipes |
| `sidecar` | K8s / ECS / container without docker.sock | None (pre-deployed) | WebSocket / network |

Override with `PAWFLOW_EXEC_MODE=local|docker|sidecar`.

## Operational Logs

Server startup keeps stdout/stderr logging for Docker and service managers, and
also mirrors the same records to rotating files under `data/runtime/logs`:

| File | Contents |
|------|----------|
| `server.log` | All server log records at INFO and above. |
| `server.error.log` | ERROR and CRITICAL records only. |

The files rotate by size to avoid unbounded terminal-copy-sized logs. Defaults
are 25 MiB per segment with 10 retained backups (`server.log.1`,
`server.log.2`, ...). Configure with:

| Variable | Default | Description |
|----------|---------|-------------|
| `PAWFLOW_SERVER_LOG_DIR` | `data/runtime/logs` | Directory for server log files. |
| `PAWFLOW_SERVER_LOG_MAX_BYTES` | `26214400` | Maximum bytes per log segment before rollover. |
| `PAWFLOW_SERVER_LOG_BACKUP_COUNT` | `10` | Retained rotated `server.log` segments. |
| `PAWFLOW_SERVER_ERROR_LOG_BACKUP_COUNT` | same as main backup count | Retained rotated `server.error.log` segments. |

## Runtime JSONL Segments

Conversation transcripts and context files are stored as segmented JSONL under
`data/runtime/conversations`. Segments rotate by row count and by byte size so a
single hot append target does not grow into a large file on Windows/WSL mounts.

| Variable | Default | Description |
|----------|---------|-------------|
| `PAWFLOW_JSONL_SEGMENT_ROWS` | `5000` | Maximum rows per JSONL segment. |
| `PAWFLOW_JSONL_SEGMENT_BYTES` | `8388608` | Maximum bytes per JSONL segment before rollover. |

## Auto-detection logic

```
PAWFLOW_EXEC_MODE set?              -> use that value
KUBERNETES_SERVICE_HOST set?        -> sidecar (K8s pod)
ECS_CONTAINER_METADATA_URI set?     -> sidecar (AWS ECS task)
/.dockerenv exists?
  + /var/run/docker.sock exists?    -> docker (DinD)
  + no docker.sock                  -> sidecar (container, no daemon)
Docker available on host?           -> docker
Otherwise                           -> local
```

---

## 1. Local (bare metal / VM)

PawFlow runs directly on the host. No containers.

### Requirements
- Python 3.11+
- Claude CLI (`npm install -g @anthropic-ai/claude-code`) -- optional, for Claude Code provider
- Docker -- optional, for containerized exec

### Start

```bash
python cli.py start --host 0.0.0.0 --port PORT
```

### Claude Code
Runs as a subprocess: `claude -p --input-format stream-json ...`

### Filesystem relay
Runs natively -- no container. The relay process runs Python `fs_actions.py` directly.

---

## 2. Docker (host or Docker-in-Docker)

PawFlow runs on the host (or in a container with docker.sock mounted) and spawns child containers.

For a public VPS behind Caddy, expose only Caddy on `80/tcp` and `443/tcp`, keep
the PawFlow application port private, and proxy to the local PawFlow HTTPS
endpoint. See [Public HTTPS with Caddy](docker.md#public-https-with-caddy) for a
Caddyfile example and UFW rules.

When PawFlow opens code-server through a relay, code-server remains mounted at
`/` upstream and PawFlow strips the public `/code/<session>/<token>/` prefix in
the reverse proxy. code-server does not expose a `--base-path` flag; only
`--abs-proxy-base-path` is passed so `/absproxy/<port>` preview links are
generated under the same public `/code/...` route.

### Requirements
- Docker installed and running
- User in `docker` group (Linux): `sudo usermod -aG docker $USER`

### 2a. PawFlow on host, containers for isolation

```bash
# Build images
bash docker/claude-code/build.sh   # pawflow-claude-code:latest
bash docker/relay-dev/build.sh     # pawflow-relay-dev:latest

# Start PawFlow
python cli.py start --host 0.0.0.0 --port PORT
```

Enable containerization per service in the admin panel:
- `containerize: true`
- `docker_image: pawflow-claude-code:latest`

### 2b. PawFlow in Docker (DinD)

PawFlow itself runs in a container, with docker.sock mounted to spawn children.

```bash
docker run -d \
  --name pawflow \
  -p PORT:PORT \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /path/to/data:/workspace/data \
  -e PAWFLOW_HOST_WORKDIR=/path/to/data \
  -e PAWFLOW_WORKDIR=/workspace/data \
  pawflow:latest
```

**Critical environment variables for DinD:**

| Variable | Required | Description |
|----------|----------|-------------|
| `PAWFLOW_HOST_APP_DIR` | Yes (server Docker with host docker.sock) | The host checkout path that maps to PawFlow's application tree. Used for CLI MCP bridge bind mounts. |
| `PAWFLOW_APP_DIR` | No (default: `/app`) | The PawFlow application path inside the server container. |
| `PAWFLOW_HOST_DATA_DIR` | Yes (server Docker with host docker.sock) | The host data path that maps to `PAWFLOW_DATA_DIR`. Used for CLI session and runtime-data mounts. |
| `PAWFLOW_DATA_DIR` | No (default: `/app/data`) | The data path inside the PawFlow server container. |
| `PAWFLOW_HOST_WORKDIR` | Yes (DinD) | The **host** path that maps to the PawFlow container's workdir. Used for child container volume mounts. |
| `PAWFLOW_WORKDIR` | No (default: `/workspace`) | The path inside the PawFlow container where the workdir is mounted. |

**Why:** When PawFlow spawns a child container (Claude Code, Codex, Gemini, relay), it passes host bind mounts to Docker. In DinD, PawFlow sees paths such as `/app/tools/mcp_bridge.py` and `/app/data/runtime`, but the Docker daemon running on the host needs the actual host paths. The `PAWFLOW_HOST_*` variables enable this translation.

### Volume mount translation example

```
PawFlow container sees: /workspace/data/claude_sessions/abc/
Host path needed:       /path/to/data/claude_sessions/abc/

PAWFLOW_HOST_WORKDIR=/path/to/data
PAWFLOW_WORKDIR=/workspace/data

Translation: /workspace/data/claude_sessions/abc/
           -> relpath from /workspace/data = claude_sessions/abc/
           -> join with /path/to/data = /path/to/data/claude_sessions/abc/
```

### Docker images

| Image | Size | Contents |
|-------|------|----------|
| `pawflow-claude-code:latest` | ~500MB | Node.js 22, Claude, Codex, Gemini, and Antigravity (`agy`) CLIs, Python 3, MCP bridge, Git |
| `pawflow-relay-dev:latest` | large | Python, Node, Rust, Go, C/C++, Java, Ruby, PHP, Perl, Lua, desktop automation, Chromium, GIMP/Inkscape, network tools |
| `python:3.12-slim` | ~150MB | Python only |
| `node:22-slim` | ~200MB | Node.js + npm |

Interactive Claude Code and Antigravity containers need a private mount namespace
for the per-user `/cc_sessions` bind. PawFlow starts those containers as root
with `SYS_ADMIN` plus `apparmor:unconfined` so `unshare` and `mount --bind` are
available inside the provider container. The default seccomp profile stays in
place: it already allows `unshare`/`mount` when `CAP_SYS_ADMIN` is granted (the
blocker is AppArmor's `docker-default` profile, which denies the mount syscall
family even with the capability). Treat
those containers as privileged runtime surfaces: credentials are scoped per
user/conversation/service, and workloads should remain isolated to the generated
session directory.

Build:
```bash
bash docker/claude-code/build.sh
bash docker/relay-dev/build.sh
```

---

## 3. Sidecar (Kubernetes / AWS ECS / Azure ACI)

PawFlow runs as one container in a pod/task. Claude Code and relays run as **sidecar containers** in the same pod/task. Communication is via network (WebSocket), not Docker spawning.

### Requirements
- Shared volume between containers (PVC in K8s, EFS/bind in ECS)
- Network connectivity between containers (localhost in same pod, or service DNS)

### Environment variables

| Variable | Set on | Description |
|----------|--------|-------------|
| `PAWFLOW_EXEC_MODE` | PawFlow | Set to `sidecar` (auto-detected in K8s/ECS) |
| `PAWFLOW_CLAUDE_SIDECAR_URL` | PawFlow | WebSocket URL of Claude Code sidecar (e.g., `ws://localhost:9092`) |
| `PAWFLOW_TOOL_RELAY_URL` | Claude Code sidecar | Tool relay WebSocket URL on the main PawFlow listener (e.g., `ws://pawflow:PORT/ws/tools/_tool_relay`) |
| `PAWFLOW_TOOL_RELAY_TOKEN` | Claude Code sidecar | Auth token for tool relay |

### 3a. Kubernetes (EKS / GKE / AKS)

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: pawflow
spec:
  template:
    spec:
      containers:
        - name: pawflow
          image: pawflow:latest
          ports:
            - containerPort: PORT
          env:
            - name: PAWFLOW_EXEC_MODE
              value: sidecar
            - name: PAWFLOW_CLAUDE_SIDECAR_URL
              value: ws://localhost:9092
          volumeMounts:
            - name: shared-data
              mountPath: /workspace/data

        - name: claude-code
          image: pawflow-claude-code:latest
          command: ["python3", "/opt/pawflow/claude_sidecar.py"]
          ports:
            - containerPort: 9092
          env:
            - name: PAWFLOW_TOOL_RELAY_URL
              value: ws://localhost:PORT/ws/tools/_tool_relay
            - name: PAWFLOW_TOOL_RELAY_TOKEN
              valueFrom:
                secretKeyRef:
                  name: pawflow-secrets
                  key: tool-relay-token
          volumeMounts:
            - name: shared-data
              mountPath: /workspace/data

        - name: relay
          image: pawflow-relay-dev:latest
          command:
            - python3
            - /opt/pawflow/pawflow_relay.py
            - --server
            - ws://localhost:PORT/ws/relay
            - --token
            - $(RELAY_TOKEN)
            - --relay-id
            - relay-sidecar
            - --dir
            - /workspace
            - --allow-exec
          volumeMounts:
            - name: shared-data
              mountPath: /workspace

      volumes:
        - name: shared-data
          persistentVolumeClaim:
            claimName: pawflow-data
```

### 3b. AWS ECS

```json
{
  "family": "pawflow",
  "containerDefinitions": [
    {
      "name": "pawflow",
      "image": "pawflow:latest",
      "portMappings": [{"containerPort": PORT}],
      "environment": [
        {"name": "PAWFLOW_EXEC_MODE", "value": "sidecar"},
        {"name": "PAWFLOW_CLAUDE_SIDECAR_URL", "value": "ws://localhost:9092"}
      ],
      "mountPoints": [{"sourceVolume": "shared-data", "containerPath": "/workspace/data"}]
    },
    {
      "name": "claude-code",
      "image": "pawflow-claude-code:latest",
      "command": ["python3", "/opt/pawflow/claude_sidecar.py"],
      "environment": [
        {"name": "PAWFLOW_TOOL_RELAY_URL", "value": "ws://localhost:PORT/ws/tools/_tool_relay"}
      ],
      "mountPoints": [{"sourceVolume": "shared-data", "containerPath": "/workspace/data"}]
    },
    {
      "name": "relay",
      "image": "pawflow-relay-dev:latest",
      "command": ["python3", "/opt/pawflow/pawflow_relay.py",
                   "--server", "ws://localhost:PORT/ws/relay",
                   "--dir", "/workspace", "--allow-exec"],
      "mountPoints": [{"sourceVolume": "shared-data", "containerPath": "/workspace"}]
    }
  ],
  "volumes": [{"name": "shared-data", "host": {}}]
}
```

### 3c. Docker Compose

```yaml
services:
  pawflow:
    image: pawflow:latest
    ports:
      - "PORT:PORT"
    environment:
      PAWFLOW_EXEC_MODE: sidecar
      PAWFLOW_CLAUDE_SIDECAR_URL: ws://claude-code:9092
    volumes:
      - pawflow-data:/workspace/data

  claude-code:
    image: pawflow-claude-code:latest
    entrypoint: ["python3", "/opt/pawflow/claude_sidecar.py"]
    environment:
      PAWFLOW_TOOL_RELAY_URL: ws://pawflow:PORT/ws/tools/_tool_relay
      PAWFLOW_TOOL_RELAY_TOKEN: ${TOOL_RELAY_TOKEN}
    volumes:
      - pawflow-data:/workspace/data

  relay:
    image: pawflow-relay-dev:latest
    entrypoint:
      - python3
      - /opt/pawflow/pawflow_relay.py
      - --server
      - ws://pawflow:PORT/ws/relay
      - --token
      - ${RELAY_TOKEN}
      - --relay-id
      - relay-sidecar
      - --dir
      - /workspace
      - --allow-exec
    volumes:
      - workspace:/workspace

volumes:
  pawflow-data:
  workspace:
```

---

## 4. Security model

| Mode | Host access | Network | CPU/Memory | Isolation |
|------|------------|---------|------------|-----------|
| Local (native) | Full | Full | Unlimited | None |
| Docker (Claude Code) | MCP tools only | MCP + API | Configurable | Container |
| Docker (relay) | Mounted dir | Full | 2 CPU / 2GB | Container |
| Docker (ephemeral shells) | Mounted dir | None | 2 CPU / 1GB | Container + read-only |
| Sidecar (Claude Code) | Shared volume | Internal | Pod limits | Container |
| Sidecar (relay) | Shared volume | Internal | Pod limits | Container |

---

## 5. Exec shell selection

The `exec` tool supports a `shell` parameter:

| Shell | Description |
|-------|-------------|
| `bash` | System bash (Git Bash on Windows) |
| `powershell` | PowerShell |
| `cmd` | Windows CMD |
| `python` | Python interpreter |
| `node` | Node.js |
| `docker-python` | Python in ephemeral Docker container |
| `docker-node` | Node.js in ephemeral Docker container |
| `docker-bash` | Bash in ephemeral Docker container |

`docker-*` shells are only available in `docker` mode. In `sidecar` mode, exec commands run inside the relay sidecar.

---

## 6. PawFlow SDK

The `pawflow` Python module is pre-installed in all PawFlow containers. It provides synchronous access to PawFlow tools via the tool relay WebSocket.

```python
from pawflow import fs, tools

# Filesystem operations
data = fs.read_file("config.json")
fs.write_file("output.txt", "processed")
files = fs.list_dir("src/")

# Any PawFlow tool
result = tools.call("generate_image", prompt="a logo", width=256)
```

Works in: ExecuteScript (containerized), custom Docker scripts, any container with `PAWFLOW_TOOL_RELAY_URL` set.

---

## 7. Environment variable reference

| Variable | Used by | Description |
|----------|---------|-------------|
| `PAWFLOW_EXEC_MODE` | PawFlow | Override execution mode: `local`, `docker`, `sidecar` |
| `PAWFLOW_HOST_WORKDIR` | PawFlow (DinD) | Host path mapped to container workdir (for child volume mounts) |
| `PAWFLOW_WORKDIR` | PawFlow (DinD) | Container workdir path (default: `/workspace`) |
| `PAWFLOW_CLAUDE_SIDECAR_URL` | PawFlow (sidecar) | WebSocket URL of Claude Code sidecar |
| `PAWFLOW_DOCKER_IMAGE` | Containers | Image name (set in Dockerfile, used for detection) |
| `PAWFLOW_TOOL_RELAY_URL` | MCP bridge / sidecars | Tool relay WebSocket URL |
| `PAWFLOW_TOOL_RELAY_TOKEN` | MCP bridge / sidecars | Auth token for tool relay |
| `PAWFLOW_USER_ID` | MCP bridge | User context for tool execution |
| `PAWFLOW_CONVERSATION_ID` | MCP bridge | Conversation context |
| `PAWFLOW_AGENT_NAME` | MCP bridge | Agent identity |
| `PAWFLOW_FS_ROOT` | Relay containers | Mounted workspace root path |

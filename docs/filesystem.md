# Filesystem Services

## Overview

PawFlow provides a unified filesystem abstraction that allows flows and agents to access files from various sources — the user's local machine, cloud storage (Google Drive, OneDrive), or the browser — through a consistent interface with enforced permissions.

**Core principle:** The PawFlow server does NOT access its own filesystem by default. All file access goes through an explicitly configured filesystem service.

## Architecture

```
┌──────────────────────────────────────────────┐
│              FilesystemBackend (ABC)          │
│  list_dir, read_file, write_file, delete,    │
│  mkdir, stat, exists, search, grep,          │
│  find_replace, git_*                         │
└──────────────┬───────────────────────────────┘
               │
    ┌──────────┼──────────┬──────────┬──────────┐
    │          │          │          │          │
┌───┴───┐ ┌───┴───┐ ┌───┴───┐ ┌───┴───┐ ┌───┴───┐
│ HTTP  │ │  WS   │ │Browser│ │Server │ │ Cloud │
│ Relay │ │ Relay │ │FS API │ │(admin)│ │GDrive/│
│       │ │       │ │       │ │       │ │OneDrv │
└───────┘ └───────┘ └───────┘ └───────┘ └───────┘
```

All backends are wrapped in `PermissionEnforcedFilesystem` which enforces:
- **Mode**: `read` | `readwrite` | `full` (controls which operations are allowed)
- **Allowed paths**: Prefix whitelist (empty = everything)
- **Denied paths**: Prefix blacklist (takes priority over allowed)

## Service Types

| Type | Description | Git Support | Requires |
|------|-------------|-------------|----------|
| `relay` | WebSocket relay to user's machine (exec, git, shell) | Yes | server relay or standalone `pawflow-relay` client |
| `filesystem` | Server disk (admin only) | Yes | Admin role |
| `googleDrive` | Google Drive via REST API | No | OAuth2 authorization |
| `oneDrive` | OneDrive via Graph API | No | OAuth2 authorization |
| `rcloneOAuthCredentials` | Encrypted rclone OAuth credentials for Drive/OneDrive mounts | No | noVNC login or raw rclone config body |
| `rcloneFilesystem` | Rclone remote config for relay-side native mounts | No | user/conversation scope service |

## Quick Start — Local Filesystem (HTTP Relay)

### 1. Start the relay on the user's machine

```bash
# Read/write access to a project directory
python tools/pawflow_relay.py --port 9876 --dir /home/user/project --secret mysecret

# Read-only access
python tools/pawflow_relay.py --port 9876 --dir /data --secret mysecret --readonly

# Bind to all interfaces (for remote access)
python tools/pawflow_relay.py --port 9876 --dir /data --secret mysecret --bind 0.0.0.0
```

### 2. Install the service in PawFlow

```
/service install localFilesystem myfiles host=localhost,port=9876,secret=mysecret,mode=readwrite
```

### 3. Use from the chat agent

The agent's `filesystem` tool automatically detects your service:
- "List files in the src directory"
- "Read the file config.json"
- "Search for all Python files"
- "Find TODO comments in the codebase"

### 4. Use from a flow

Add a `filesystemOps` task with `service_id=myfiles` and configure the action.

## Quick Start — WebSocket Relay

Same as HTTP relay but with persistent connection (faster for frequent operations):

```bash
python tools/pawflow_relay.py --port 9877 --dir /home/user/project --secret mysecret
```

```
/service install wsFilesystem myfiles host=localhost,port=9877,secret=mysecret,mode=readwrite
```

Relay reconnect handling is connection-scoped. When a relay WebSocket dies, PawFlow cancels only the pending requests sent through that socket; requests already sent through a newer reconnect stay alive. If the pool drops to zero, all pending relay requests are failed immediately so UI calls, context sync, and tool requests cannot accumulate blocked threads during network flaps.

On Windows/WSL2 network changes, a relay socket may surface the network failure through the reader before the next payload frame arrives. The relay receiver treats a stored reader exception as a disconnect, not as an idle keepalive timeout; this prevents ping/retry hot loops and releases the relay session cleanly.

When a Docker relay is started with `--allow-local`, `local=true` operations are forwarded to the host helper. For Windows hosts whose project root is a UNC path such as `\\wsl$\...`, cmd-based execution uses `pushd`/`popd` instead of setting the process current directory to the UNC path, because `cmd.exe` cannot run with a UNC cwd.

## Cloud Storage — Google Drive

### 1. Configure OAuth provider

```
/service install oauthProvider gdrive_oauth provider=google_drive,client_id=YOUR_ID,client_secret=YOUR_SECRET,redirect_uri=http://localhost:9090/auth/callback
```

### 2. Authorize (user clicks the OAuth link in the login flow)

### 3. Install the Drive service

```
/service install googleDrive mydrive mode=readwrite
```

The `user_id` is automatically injected from the authenticated session. Tokens are stored encrypted and auto-refresh.

## Cloud Storage — OneDrive

### 1. Configure OAuth provider

```
/service install oauthProvider onedrive_oauth provider=microsoft_onedrive,client_id=YOUR_ID,client_secret=YOUR_SECRET,redirect_uri=http://localhost:9090/auth/callback
```

### 2. Authorize, then install

```
/service install oneDrive mydrive mode=read
```

## Relay-native remote mounts

Remote filesystem services can be linked to a conversation so every relay linked
to that conversation mounts them under `/remote/<service_id>`, where the service
id is sanitized into a directory name. This is intended for shell-native access:

```bash
cat /remote/mydrive/report.txt
ls /remote/sftp_prod/logs
```

Use slash commands from the conversation:

```
/remote-fs list
/remote-fs link mydrive
/remote-fs unlink mydrive
```

The link is conversation-scoped, not relay-scoped. When a relay reconnects,
PawFlow rebuilds the manifest from the conversation bindings and asks the relay
to reconcile `/remote` with `rclone mount`.

Relay images install rclone from the official upstream binary, not the Ubuntu
package. The distro package can lag behind upstream and has produced noisy FUSE
`Input/output error` messages for SELinux/ACL extended-attribute probes during
commands such as `ls -l`, even while normal `stat` and file reads succeed.

Security rules:

- Linking a remote filesystem sends the relay the credentials required by
  rclone. Only link services to relays you trust.
- Global filesystem services are never eligible for relay-native mounts. They
  remain available through PawFlow server-side tools that explicitly select the
  filesystem service.
- Only user and conversation-scoped `rcloneFilesystem` services are eligible.
  Native `googleDrive` and `oneDrive` services stay API-backed and are not
  mounted into relays.

For S3, SFTP, WebDAV, or other non-OAuth rclone backends, create an
`rcloneFilesystem` service, then link that service to the conversation. The
service form is driven by `rclone_type`: selecting `sftp`, `s3`, `webdav`, `ftp`,
`azureblob`, or `gcs` shows only the guided fields that rclone uses for that
backend. Fields that are not visible are not saved into the generated rclone
config.

`rclone_config` on `rcloneFilesystem` is an advanced escape hatch for non-OAuth
backends. If it is set, PawFlow sends that raw rclone config body to the relay
instead of generating config from the guided fields. Paste only the body of the
rclone remote, not the `[remote]` header. The field is sensitive: PawFlow encrypts
it at rest. It may also contain a secret reference such as `${sftp_rclone_config}`;
the service value is decrypted first, then normal expression resolution resolves
the referenced secret.

OAuth-backed rclone backends (`drive` and `onedrive`) use two services:

1. Create a `rcloneOAuthCredentials` service with provider `drive` or `onedrive`.
   This service owns the encrypted rclone OAuth config fragment and exposes
   **Login via server**. PawFlow starts a temporary noVNC desktop, runs
   `rclone config create`, opens the provider browser authorization flow, then
   stores the generated remote body back into the credential service. Closing
   the dialog cleans up the temporary container.
2. Create an `rcloneFilesystem` service with the same `rclone_type` and set
   `credential_service_id` to the credentials service. Link the filesystem
   service to the conversation; the relay manifest combines the filesystem
   mount settings with the referenced credential service to build `rclone.conf`.

## Server Filesystem (Admin Only)

For rare cases where flows need server disk access (exports, logs, staging):

```
/service install filesystem staging root=/var/pawflow/staging,mode=readwrite
```

Only admin users can install this service type.

## Permissions

### Modes

| Mode | Allowed Operations |
|------|-------------------|
| `read` | list_dir, read_file, stat, exists, search, grep, git_status, git_log, git_diff |
| `readwrite` | All read + write_file, mkdir, find_replace, git_commit, git_pull, git_push, git_checkout |
| `full` | All readwrite + delete_file |

### Path Restrictions

- `allowed_paths`: Comma-separated prefixes. Only paths matching at least one prefix are accessible. Empty = everything.
- `denied_paths`: Comma-separated prefixes. Paths matching any denied prefix are blocked (takes priority over allowed).

Example: `allowed_paths=src,docs` + `denied_paths=src/secret` → can read `src/main.py` and `docs/readme.md`, but NOT `src/secret/keys.json`.

## Operations

### Basic
- `list_dir(path, recursive=false, max_entries=0)` — List directory contents. With `recursive=true`, returns descendant paths relative to `path`; `max_entries` caps large recursive listings.
- `read_file(path)` — Read file bytes
- `write_file(path, content)` — Create or overwrite file
- `delete_file(path)` — Delete file (requires `full` mode)
- `mkdir(path)` — Create directory tree
- `stat(path)` — Get file metadata (name, kind, size, modified)
- `exists(path)` — Check if path exists

### Advanced
- `search(path, pattern, recursive)` — Find files by glob pattern. Patterns support `**` and shell-style brace alternatives such as `{core,services}/**/*.py`.
- `grep(path, regex, recursive)` — Search file contents by regex. A space-separated `path` containing multiple existing roots scans each root.
- `find_replace(path, pattern, replacement, multiline=false)` — Regex replace in a file; set `multiline=true` so `^` and `$` match line boundaries.

### Git (relay + server backends only)
- `git_status(path)` — Branch, staged, modified, untracked files
- `git_log(path, count)` — Recent commit history
- `git_diff(path, ref)` — Textual diff
- `git_commit(path, message)` — Stage all and commit
- `git_pull(path)` — Pull from remote
- `git_push(path)` — Push to remote
- `git_checkout(path, ref)` — Switch branch/tag

## Usage in Tasks

### filesystemOps Task

```json
{
  "type": "filesystemOps",
  "config": {
    "service_id": "myfiles",
    "action": "read_file",
    "path": "data/input.csv"
  }
}
```

Action and path can also come from FlowFile attributes (`fs.action`, `fs.path`).

### GetFile / PutFile

- **With `service_id`**: Reads/writes through the filesystem service
- **Without `service_id`**: Sandbox mode — reads/writes from FileStore (in-memory, no disk access)

### ExecuteScript

Add `filesystem_service_id` to inject `fs` into the script namespace:

```python
# In the script:
data = fs.read_file("input/data.csv")
fs.write_file("output/result.json", json.dumps(result).encode())
files = fs.search(".", "*.py")
matches = fs.grep("src", r"TODO|FIXME")
```

## Security

- **Path traversal prevention**: All paths are normalized and `..` above root is blocked
- **Secret validation**: Relay scripts use HMAC constant-time comparison
- **Encryption at rest**: OAuth tokens stored encrypted via SecretsManager
- **Defense in depth**: Relay `--readonly` flag blocks writes server-side even if service is configured as readwrite
- **Admin restriction**: Server `filesystem` type requires admin role to install
- **Sandbox default**: GetFile/PutFile without a service use FileStore (no disk access)

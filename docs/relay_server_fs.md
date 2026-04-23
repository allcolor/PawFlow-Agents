# Relay-initiated FS ops (server-side handler)

The normal direction of the relay protocol is **server → relay**: the
PawFlow server asks the connected relay to read/write a file on the
user's host. This document covers the **inverse direction** (relay →
server) added to give a remote relay's docker container access to a
slice of the server's filesystem (typically `CLAUDE_SESSIONS_DIR/<user>/`)
without opening a second transport.

The motivating use case: when CC SDK spills a large tool result to its
local `/workspace/projects/.../tool-results/<f>.txt`, that file lives
under `CLAUDE_SESSIONS_DIR` on the server. A remote relay needs to
read it back to feed grep/read tools. With this handler, the relay
mounts a FUSE proxy backed by these ops; both the CC docker and the
relay docker see the same canonical paths.

## Wire format

Reuses the existing `/ws/relay/<service_id>` WebSocket. Two new envelope
types are added on top of the existing `result` / `error` / `progress` /
`exec_output` / `http_response` / ... taxonomy.

### Request (relay → server)

```json
{
  "type": "relay_request",
  "request_id": "<unique id>",
  "method": "sfs.<op>",
  "args": { ... }
}
```

### Response (server → relay)

Success:
```json
{
  "type": "relay_response",
  "request_id": "<matching id>",
  "data": { ... op-specific payload ... }
}
```

Failure:
```json
{
  "type": "relay_response",
  "request_id": "<matching id>",
  "error": "ENOENT",     // POSIX errno name
  "errno": 2,             // numeric errno
  "message": "..."        // optional, human-readable
}
```

## Sandbox

Every `RelayService` carries an owner `user_id` (set by the registry on
service attach). The handler resolves all paths under
`CLAUDE_SESSIONS_DIR / <user_id> /` and rejects:

- Absolute or `..`-traversal paths that resolve outside the slot (`EACCES`)
- Symlinks whose target leaves the slot (`EACCES`)
- Calls before `set_user_id` is invoked (`EACCES`)

A relay registered for user A can never see user B's slot, even if the
relay forges a `path` argument: resolution always prepends the slot root
and re-checks containment after `Path.resolve()`.

## Methods

### Read-only

| Method | Args | Success payload |
|---|---|---|
| `sfs.getattr` | `{path}` | `{st_mode, st_size, st_mtime, st_atime, st_ctime, st_uid, st_gid, st_nlink}` |
| `sfs.readdir` | `{path}` | `{entries: [...]}` (sorted) |
| `sfs.open` | `{path, flags}` | `{fh}` — `O_CREAT` is refused (use `sfs.create`); other flags accepted |
| `sfs.read` | `{fh, offset, size}` | `{data_b64}` (size capped at `RelayServerFs.MAX_READ_CHUNK = 1 MiB`) |
| `sfs.release` | `{fh}` | `{}` |
| `sfs.statfs` | `{path}` | `{f_bsize, f_frsize, f_blocks, f_bfree, f_bavail, f_files, f_ffree, f_favail, f_namemax}` |

### Read-write

| Method | Args | Success payload |
|---|---|---|
| `sfs.create` | `{path, mode}` | `{fh}` — mode masked to `0o777` (no setuid/setgid/sticky) |
| `sfs.write` | `{fh, offset, data_b64}` | `{bytes_written}` (chunk capped at `MAX_WRITE_CHUNK = 1 MiB`) |
| `sfs.truncate` | `{path \| fh, length}` | `{}` |
| `sfs.unlink` | `{path}` | `{}` |
| `sfs.mkdir` | `{path, mode}` | `{}` — mode masked to `0o777` |
| `sfs.rmdir` | `{path}` | `{}` |
| `sfs.rename` | `{old, new}` | `{}` — BOTH paths must resolve inside the slot |
| `sfs.chmod` | `{path, mode}` | `{}` — mode masked to `0o777` |
| `sfs.utimens` | `{path, atime, mtime}` | `{}` — omit times to use "now" |

Any other method (e.g. `sfs.chown`, `sfs.symlink`) returns `ENOSYS`.

## Lifecycle

- The handler is lazily instantiated on the first `relay_request`
  (after the relay has registered and `set_user_id` has been called).
- File handles (`fh`) live for the lifetime of the `RelayService`
  instance and are released by `RelayService.disconnect()`.
- Transient WS reconnects do **not** invalidate `fh` values — the
  service instance persists across drops in the relay pool.

## Concurrency

FS ops are dispatched on the asyncio loop's default executor so a slow
disk doesn't block other relay traffic on the same WebSocket. The
handler's `fh` table is guarded by an internal lock; concurrent
`sfs.read` calls on different `fh` values are safe. Concurrent reads on
*the same* `fh` are serialized by the lock around the lseek+read pair.

## Future phases

- **3** (the breaking-but-final piece): drop the `/workspace` symlink
  in CC docker. Set `CLAUDE_CONFIG_DIR=/cc_sessions/<conv>/claude`
  directly so CC's spilled paths (e.g. `/cc_sessions/<conv>/claude/projects/.../tool-results/<f>`)
  are canonical — identical to what the relay sees via its FUSE mount.
  Eliminates the path-translation logic that would otherwise be needed
  in `tool_relay_service`.
- **4**: `sfs.symlink` + `sfs.readlink` (currently rejected; symlinks
  are merely sandbox-validated by `sfs.getattr`).
- **5**: `sfs.chown` if a real use case appears — omitted today since
  CC sessions don't need cross-uid ownership.

## Operator setup

### Relay daemon

New CLI flag (also `PAWFLOW_SERVER_MOUNT` env):

```
pawflow_relay --server-mount /var/lib/pawflow/server-fs
```

The daemon mounts a FUSE filesystem at that path; every syscall is
forwarded to the server's `RelayServerFs` for the user the relay is
registered as.

The flag is wired through automatically in the two wrapper launch
paths, pinned to the canonical mountpoint `/cc_sessions`:

- `pawflow_cli --docker-image ...` → `pawflow_relay/thread.py` builds
  the `docker run` with the FUSE caps + `--server-mount /cc_sessions`.
- Server-spawned per-conversation relays → `core/server_relay_manager.py`
  sets `PAWFLOW_SERVER_MOUNT=/cc_sessions` env + the same caps.

Requires:
- `pyfuse3` + `trio` (Python packages)
- `libfuse3` + `fusermount3` on the host (apt: `fuse3 libfuse3-dev`)
- Permission to mount FUSE: native run = ok if user is in `fuse` group;
  containerized = needs `CAP_SYS_ADMIN`, `/dev/fuse`, and an
  `apparmor:unconfined` profile (see `docker-compose.yml`). Both
  wrappers above pass these flags; `tests/test_relay_fuse_launch.py`
  locks it.

Both container-spawning launch paths wire this automatically:
- `pawflow_cli --docker-image ...` → `pawflow_relay/thread.py` adds the
  three docker flags above and passes `--server-mount /cc_sessions` to
  the in-container launcher.
- Server-side per-conversation relays → `core/server_relay_manager.py`
  adds the same docker flags and sets `PAWFLOW_SERVER_MOUNT=/cc_sessions`
  in the container env (picked up by `pawflow_relay.cli`'s default).
Operators do not need to edit a compose file for these paths.

### Tools docker (where bash/exec runs)

Bind-mount the FUSE point into any container that needs to see the
user's session files:

```yaml
volumes:
  - /var/lib/pawflow/server-fs:/cc_sessions:rshared
```

`rshared` propagation is mandatory — without it, the FUSE mount that
the daemon creates inside its own mount namespace stays invisible to
the tools container.

### Verify the mount

```
$ mount | grep server-fs
pawflow-server-fs on /var/lib/pawflow/server-fs type fuse.pawflow-server-fs ...

$ ls /cc_sessions          # inside tools docker
convA  convB  convC         # the user's conversations

$ cat /cc_sessions/convA/claude/projects/-workspace/<sub>/tool-results/<f>.txt
... (CC's spilled tool output, served from the PawFlow server) ...
```

## See also

- `services/relay_server_fs.py` — server handler
- `tests/test_relay_server_fs.py` — 24 sandbox/op tests
- `services/filesystem_service.py:_handle_relay_request` — dispatch
- `pawflow_relay/server_fs_client.py` — relay-side request/response correlator
- `pawflow_relay/server_fs_mount.py` — FUSE proxy (lazy pyfuse3 import)
- `tests/test_server_fs_client.py` — 8 client tests
- `tests/test_server_fs_roundtrip.py` — 5 end-to-end (no WS) tests

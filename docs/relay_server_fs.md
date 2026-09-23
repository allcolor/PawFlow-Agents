# Relay-initiated FS ops (server-side handler)

The normal direction of the relay protocol is **server → relay**: the
PawFlow server asks the connected relay to read/write a file on the
user's host. This document covers the **inverse direction** (relay →
server) added to give a remote relay's docker container access to a
slice of the server's filesystem (the union of the user's Claude, Codex, and
Gemini CLI session slots) without opening a second transport.

The motivating use case: when a CLI provider stores bootstrap state, local
configuration, or a spilled tool result under its session workdir, a remote
relay needs to read it back through the same canonical path. With this handler,
the relay mounts a FUSE proxy backed by these ops; the provider container and
the relay container both see `/cc_sessions/<conversation>/<agent>/...`.

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
service attach). The handler resolves all paths under that user's slots in
`CLAUDE_SESSIONS_DIR`, `CODEX_SESSIONS_DIR`, and `GEMINI_SESSIONS_DIR`, and
rejects:

- Absolute or `..`-traversal paths that resolve outside the slot (`EACCES`)
- Symlinks whose target leaves the slot (`EACCES`)
- Calls before `set_user_id` is invoked (`EACCES`)

A relay registered for user A can never see user B's slot, even if the
relay forges a `path` argument: resolution always prepends the slot root
and re-checks containment after `Path.resolve()`.

## Multi-provider view

`/cc_sessions` is a union of the three provider-specific runtime roots. This
matters when the same canonical agent directory exists in more than one root:
an empty stale Claude directory must not hide a live Codex
`.pawflow_cci/initial_context.md`, for example.

- `readdir` returns the sorted union of entries from every matching provider
  directory.
- Reads use the provider that owns the requested entry. Provider-specific
  directories such as `.claude`, `.codex`, and `.gemini` take precedence;
  otherwise the newest duplicate entry wins.
- New writes follow a provider-specific directory hint when present, then the
  provider owning the deepest existing ancestor. A completely new path falls
  back to the first configured root.
- Every candidate is resolved and containment-checked inside its own user slot;
  merging the view does not weaken cross-user or symlink isolation.
- Claude memory mirroring runs only for files routed to the Claude root.

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
  three docker flags above and passes `--server-mount /cc_sessions` and
  `--filestore-mount /filestore` to the in-container launcher.
- Server-side per-conversation relays → `core/server_relay_manager.py`
  adds the same docker flags and sets `PAWFLOW_SERVER_MOUNT=/cc_sessions`
  + `PAWFLOW_FILESTORE_MOUNT=/filestore` in the container env (picked
  up by `pawflow_relay.cli`'s default).
Operators do not need to edit a compose file for these paths.

Server-managed relays use a private plain-WebSocket endpoint on
`host.docker.internal`. The listener accepts that endpoint only for private
source addresses carrying a live ephemeral `pawflow_internal` token; the
relay's independent registration token is still required immediately after
the upgrade. External relays continue to use TLS. This keeps high-volume
bidirectional FUSE traffic away from the listener's threaded TLS adapter
without serializing socket reads and writes, so an idle receive cannot block
tool calls.

### FUSE mount lifecycle vs. WS reconnects

The FUSE filesystems are mounted **once** by the relay worker, before
entering the WS reconnect loop, and stay up across drops/reconnects.
The `ServerFsClient` bound to the WS is wrapped in a
`SwappableServerFsClient` (see `pawflow_relay/server_fs_client.py`):
on each reconnect the worker calls `set_inner(new_client)`; on each
disconnect it calls `clear_inner()` and `cancel_all('relay disconnected')`.

During the reconnect gap, FUSE callbacks return EIO transiently. The
kernel-side mount and inode allocations stay stable, so:

- Bind-mounts of `/cc_sessions` / `/filestore` in downstream containers
  (notably the CC docker spawned per agent turn) remain valid — they
  do not need to be recreated.
- The negative-dentry cache problem (deep paths returning ENOENT after
  an unmount/remount cycle) does not occur.

The FUSE is unmounted **only** on relay shutdown. `setup_combined_fs`
registers `CombinedServerFsMount.stop()` with `atexit`, so every exit path
runs it — SIGTERM (`sys.exit(0)` in `worker_main`), SIGINT, an escaping
exception. `stop()` is idempotent.

### Out-of-process FUSE responder

The process that answers the kernel's FUSE requests is **never** the relay
worker. `pawflow_relay/combined_fs.py` (`CombinedServerFsMount`, in the
worker) spawns `python -m pawflow_relay.fuse_responder`, which owns the
`/dev/fuse` session (pyfuse3 + trio) and forwards each backend op over an
inherited `AF_UNIX` socketpair (length-prefixed JSON frames: `ready` /
`failed`, `req` / `rep`, `ping` / `pong`). The worker answers `req` frames
with the `SwappableServerFsClient` for the op's prefix (`sfs.`, `ffs.`,
`skfs.`).

Why: once the responder has read a request, the calling thread waits in
the kernel's `request_wait_answer` uninterruptibly — SIGKILL included —
until the responder replies or the connection is aborted, and the kernel
aborts the connection only when the last reference to the `/dev/fuse` file
is released. When the responder was a thread of the worker, the worker's
own command threads (`list_dir`, `glob`, `grep`, … on `/cc_sessions`,
`/filestore`, `/skills`) could be waiting on it when the process exited.
Those waiting threads kept the fd table — and so the `/dev/fuse` file —
alive, so the connection was never aborted and they were never answered:
the worker stayed `<defunct>` with threads in `D` state, `docker-init`
stayed stuck in `zap_pid_ns_processes`, and the container's PID namespace
outlived the container (observed for a month, 2026-08-23 → 2026-09-23,
`/sys/fs/fuse/connections/63/waiting = 4`).

Guarantees of the split:

| Failure | What ends the waits |
|---|---|
| Worker exits or is SIGKILLed with ops in flight | The responder answers every request it forwarded within `op timeout + REPLY_GRACE` (5 s + 2 s) with EIO; the worker's threads return, its fds close, the responder sees end of stream, stops its loop, unmounts and exits. |
| Responder crashes or is SIGKILLed | Its `/dev/fuse` file is released, the kernel aborts the connection, every waiter gets `ENOTCONN`. The worker lazily detaches the dead mount and restarts the responder (backoff 1 s → 30 s). |
| Responder alive but stuck | The worker pings every 10 s; no pong for 30 s, or a FUSE loop that has not ticked for 30 s, gets the responder SIGKILLed (previous row). |
| Whole container killed | The responder dies with the PID namespace, which releases the connection as above. |

The responder runs in its own session (`start_new_session`), so terminal
signals aimed at the worker cannot end it before the worker detaches the
mount, and it never accesses its own mount.

`stop()` ordering: mark stopping (every backend op now gets EIO) → lazy
unmount (`fusermount3 -u -z`, no new lookups) → close the socket (the
responder stops, unmounts, exits) → SIGKILL after 5 s → as a last resort,
abort the connection through `/sys/fs/fuse/connections/<id>/abort`. The id
is the one recorded from `/proc/self/mountinfo` when this mount came up
(mount point **and** `pawflow-combined-fs` source must match), and it is
used only while its responder is still unreaped, so it cannot name another
filesystem. Unmounting never `stat`s the mountpoint first: that is itself a
FUSE request, and it fails with `ENOTCONN` on a dead mount.

#### Recovering a host hit by the old in-process responder

A relay started before this change can still leave a ghost namespace. On the
host (root), identify the connection **positively** before aborting it —
never abort arbitrary entries:

```bash
# the stuck process's mount of pawflow-combined-fs gives the connection id
grep pawflow-combined-fs /proc/<stuck-pid>/mountinfo   # ... 0:63 / /tmp/pf_combined_fs ...
cat /sys/fs/fuse/connections/63/waiting               # > 0: requests pending
echo 1 > /sys/fs/fuse/connections/63/abort            # releases the D-state threads
```

The threads leave `D`, the process and `docker-init` finish exiting, and the
PID namespace goes away.

#### Regression tests

- `tests/test_combined_fs_lifecycle.py` — framing, EOF/timeout bounds of the
  responder link, prefix routing, refusal once stopping, `stop()` ordering
  (detach → end of stream → kill → abort of the recorded connection only),
  lazy unmount without `stat`, `atexit` registration.
- `tests/test_combined_fs_fuse_integration.py` — real kernel FUSE: a relay
  process SIGKILLed while four of its own threads sit in
  `request_wait_answer` exits and takes its responder and mount with it; a
  responder crash releases callers and the mount recovers; `stop()` with a
  readdir in flight is bounded. Skipped without FUSE. Inside the relay
  container AppArmor allows FUSE mounts only under `/tmp/pf_combined_fs`,
  `/remote` and `/workspace`: set `PAWFLOW_FUSE_TEST_ROOT` to a directory
  under `/remote` owned by the relay user.

### Multi-relay scenarios

- **Spawn_relay child relays** (parent answers `spawn_relay` envelope):
  the child runs in the parent's process/container, so it sees the
  parent's FUSE mounts via the shared mount namespace. The child does
  not mount its own FUSE.
- **Multiple PawCode CLI sessions for the same user**: each runs its
  own relay docker, each has its own FUSE mount. The server-side
  `_relay_pool` round-robins tool calls between them — caveat: each
  CLI's `/workspace` is its own host machine path, so a tool call may
  read different content depending on which relay handled it. This
  is a property of multi-relay routing, not of the FUSE layer.
- **Multiple users**: each user's relays are sandboxed by `user_id`
  on the server side; FUSE mounts in user A's relay can never see
  user B's session slot or FileStore entries.

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

## Sister protocol: FileStore FUSE (`ffs.*`)

The same WebSocket and `relay_request` / `relay_response` envelope is
shared by a second handler that exposes the server FileStore as a
virtualized FUSE hierarchy. Methods come in with the `ffs.` prefix
and `services/filesystem_service.py:_handle_relay_request` dispatches
them to `RelayFileStoreFs` instead of `RelayServerFs`.

Layout (read-only, conv-first):

```
/                                 → dir, lists every conv_id that has
                                    at least one file owned by the
                                    relay's user.
/<conv_id>                        → dir, lists every file_id stored
                                    under that conv (and accessible to
                                    the user).
/<conv_id>/<file_id>              → dir containing one file.
/<conv_id>/<file_id>/<filename>   → the file content.
```

The conv-first layout matches the on-disk FileStore (`data/runtime/
files/<user_id>/<conv_id>/<bucket>/<file_id>_<filename>`). It gives
per-conv isolation while keeping a single FUSE mount per relay — a
relay can be linked to N conversations and each appears as its own
sub-tree without spawning N mountpoints. A file_id from conv A
surfaced through `/<convB>/<file_id>` returns ENOENT, even when both
convs belong to the same user.

Writes (`ffs.create`, `ffs.write`, `ffs.unlink`, ...) all return EROFS
— the `file_id` would have to be assigned by `FileStore.store()`
before the path could exist, so there is no sensible mapping for `cp
foo.txt /filestore/<conv>/<NEW>/`. Use the FileStore HTTP/MCP APIs
for the write path.

The relay-side mount is started by `pawflow_relay/worker.py` when
`--filestore-mount /filestore` (or `PAWFLOW_FILESTORE_MOUNT`) is set,
and reuses `ServerFsMount` with `method_prefix='ffs.'`. The default
relay docker startup in `pawflow_relay/thread.py` already passes
`--filestore-mount /filestore` so the mount is live alongside
`/cc_sessions`.

## Sister protocol: Skills FUSE (`skfs.*`)

A third handler exposes the server Agent Skills repository as a
virtualized read-only FUSE hierarchy. Methods arrive with the `skfs.`
prefix and `services/filesystem_service.py:_handle_relay_request`
dispatches them to `RelaySkillsFs`.

Its purpose is to make a skill's asset files (e.g. the `scripts/` or
`references/` referenced from `SKILL.md` instructions) reachable by
**non-CLI providers**, whose tools execute inside the relay container.
CLI providers (Claude Code) instead get the same files via per-skill
docker bind mounts — see `core/cli_workspace_mounts.build_skill_mount_args`
— and both surfaces expose the identical `/skills/...` paths produced
by `core.skill_resolver.skill_mount_dir`.

Layout (read-only, mirrors `data/repository/skills/`):

```
/                          → dir, lists 'global' and 'users'
/global/<skill>/...        → global skill directories
/users/<uid>/<skill>/...   → this relay user's skill tree
                             (which nests conversation-scoped skills)
```

Only `global` and the relay user's own `users/<uid>` subtree are
reachable; any other `users/<other>` path returns ENOENT. Writes
(`skfs.create`, `skfs.write`, ...) all return EROFS — skills are
managed via the resource APIs, not the FUSE mount.

The relay-side mount is started by `pawflow_relay/worker.py` when
`--skills-mount /skills` (or `PAWFLOW_SKILLS_MOUNT`) is set; it is the
third routed subtree of the single `CombinedServerFsMount`. The
default relay docker startup passes `--skills-mount /skills` so the
mount is live alongside `/cc_sessions` and `/filestore`.

## See also

- `services/relay_server_fs.py` — server handler (sfs.*)
- `services/relay_filestore_fs.py` — server handler (ffs.*, RO virtualized)
- `services/relay_skills_fs.py` — server handler (skfs.*, RO virtualized)
- `tests/test_relay_server_fs.py` — sandbox, union-view, and operation tests
- `tests/test_relay_filestore_fs.py` — 35 path/op/access-scope tests (incl. per-conv isolation)
- `tests/test_relay_skills_fs.py` — 20 path/op/access-scope tests
- `services/filesystem_service.py:_handle_relay_request` — prefix dispatch
- `pawflow_relay/server_fs_client.py` — relay-side request/response correlator
- `pawflow_relay/combined_fs.py` — relay-side supervisor of the combined
  mount (spawn, routing, health, restart, ordered stop)
- `pawflow_relay/fuse_responder.py` — out-of-process FUSE responder and the
  relay ↔ responder link
- `pawflow_relay/server_fs_mount.py` — pyfuse3 Operations routing the three
  subtrees (lazy pyfuse3 import; runs only in the responder)
- `tests/test_server_fs_client.py` — 8 client tests
- `tests/test_server_fs_roundtrip.py` — 5 end-to-end (no WS) tests

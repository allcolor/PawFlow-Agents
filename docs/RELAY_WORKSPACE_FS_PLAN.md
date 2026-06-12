# Relay Workspace FS — Implementation Plan (Option B, rw)

Status: **planned, not implemented**. Decision 2026-06-12: option B (hub-routed
relay-to-relay FUSE), rw mounts, perf explicitly non-critical — this is a
compatibility fallback; the canonical file path for providers remains the MCP
relay tools, which execute next to the files.

## Problem

Pool provider containers (claude_code, claude_code_interactive, codex, gemini,
antigravity_observer) get `/workspace` only when the conversation's default
relay runs **on the server host** (`core/cli_workspace_mounts.py` binds the
relay's `host_root` with `-v`). For a **remote** relay (other Linux box,
Windows/Docker Desktop, laptop behind NAT) `host_root` does not exist on the
server host, the bind is skipped, and CLI providers that fall back to local
filesystem access find nothing. Gap, not design.

## Architecture

Reuse the proven server-fs pattern (`services/relay_server_fs.py` +
`pawflow_relay/server_fs_mount.py`), adding one routing hop:

```
pool container                local hub relay              server                remote relay
/workspace (docker -v) ──> FUSE subtree rfs/<relay_id> ──> rwfs.<op> router ──> posix executor on /workspace
        (host rshared path: /var/lib/pawflow/relay-fs/<relay_id>)
```

All FUSE machinery stays in the local relay container, which already has
pyfuse3, `/dev/fuse`, `SYS_ADMIN` and an rshared host bind. The server only
routes ops between two WebSockets it already terminates. The remote relay
only gains a low-level posix op handler next to its existing tool executor.

## Components

### 1. Remote relay — posix op executor (`pawflow_relay/workspace_fs_server.py`)

Handles `relay_request` messages `{method: "wfs.<op>", args}` against the
relay's own `/workspace`:

- Phase R1 (read): `getattr`, `readdir`, `open`, `read`, `release`, `statfs`,
  `readlink`.
- Phase R2 (write): `write`, `create`, `truncate`, `unlink`, `mkdir`, `rmdir`,
  `rename`, `fsync`, `utimens`, `chmod` (no `chown`; no device/special files).
- Sandbox: same realpath-containment semantics as the hardened
  `core/handlers/_fs_base._sandbox_path` — resolve symlinks, refuse any path
  escaping `/workspace`, for source AND destination of `rename`.
- Per-connection fd table; everything released on WS drop.
- Unknown method → `ENOSYS`; errors map to POSIX errno names exactly like
  `relay_server_fs.py`.

### 2. Server — hub router (`services/relay_workspace_fs.py`)

New method family on the existing `/ws/relay/<id>` socket, modeled on
`RelayServerFs`:

- Local hub relay → server: `{method: "rwfs.<op>", args: {relay_id, ...}}`.
- Server validates, then forwards as `wfs.<op>` over the **target** relay's
  socket (`core/server_relay_manager.py` owns the connection registry) and
  pipes the response back. No payload interpretation server-side beyond
  authorization — stream `read`/`write` chunks as opaque base64.
- Authorization (critical):
  - the requesting socket must be the server-spawned hub relay (flagged at
    registration; a user relay must NOT be able to call `rwfs.*`);
  - `relay_id` must exist, be connected, and the op is tagged with the relay
    owner's `user_id` for audit.
- Failure mapping: target disconnected → `ENOTCONN`; forward timeout → `EIO`
  (timeout budget per op, generous — perf is non-critical); mid-op disconnect
  → cancel pending requests like `ServerFsClient.cancel_all`.

### 3. Local hub relay — FUSE subtree (`pawflow_relay/server_fs_mount.py`)

Extend `CombinedServerFsMount` with a fourth routed tag `relay_fs`:

- Layout inside the one pyfuse3 mount: `<combined_root>/relay_fs/<relay_id>/…`.
- Top level of `relay_fs/` is synthesized from a server-pushed manifest (see
  4); each `<relay_id>` subtree proxies ops as `rwfs.<op>` with the relay_id
  argument injected.
- rw end-to-end: implement the write op set in the combined operations class
  (the current server-fs subtree is read-only by design — the new tag has its
  own op table; do not widen cc_sessions/filestore/skills).
- Host exposure: new compose/thread.py volume
  `/var/lib/pawflow/relay-fs:/relay_fs:rshared`, and the worker bind-mounts
  `<combined_root>/relay_fs` onto `/relay_fs` (a real `mount --bind`, NOT a
  symlink — symlinks do not propagate to the host).
  **Task zero of the implementation: confirm on the VPS how the existing
  cc_sessions subtree reaches `/var/lib/pawflow/server-fs` (bind vs direct
  mount target) and replicate exactly that mechanism.**
- FUSE mount must use `allow_other` (+ `/etc/fuse.conf` `user_allow_other` in
  the relay image if missing): pool containers run uid 1000, the hub relay
  user may differ — same lesson as the earlier server-fs UID fix.

### 4. Manifest & reconciliation

- Server pushes `{type: "relay_fs_manifest", relays: [{relay_id, owner,
  connected}]}` to the hub relay on: relay connect/disconnect, relay
  link/unlink. Modeled on `RemoteMountManager.reconcile` (idempotent,
  full-state).
- Scope of the manifest: **all currently connected non-local relays** (not
  per-conversation — the per-conversation choice happens at pool bind time).
- Disconnected relay: keep the directory entry, ops return `ENOTCONN`
  (cheaper for the pools than a vanishing mountpoint mid-session); prune it
  on unlink.

### 5. Pools — bind branch (`core/cli_workspace_mounts.py`)

In `_docker_source_for_relay`: when `host_root` is empty or not a local dir,
fall back to `<RELAY_FS_HOST_ROOT>/<relay_id>` (default
`/var/lib/pawflow/relay-fs`, env-overridable) if that directory exists and the
relay is connected. Same rw/ro suffix logic as today
(`PAWFLOW_CLI_WORKSPACE_MOUNT`, default rw per decision). `/relay/<id>`
secondary mounts get the same fallback. Log clearly which flavor was mounted
(`local-bind` vs `relay-fs`).

Scoping note: the host tree contains every user's remote relay, but each pool
only receives `-v …/relay-fs/<relay_id>:/workspace` for relays already
authorized for that conversation by `core/relay_bindings` — the existing
authority check. Pools never see the tree root.

### 6. Security review items (gate before merge)

- Remote-side jail: `wfs.*` confined to `/workspace` with symlink-safe
  containment (mirrors the `_sandbox_path` fix shipped in 01e37476).
- Hub-only `rwfs.*`: a malicious user relay must get `EPERM` calling it.
- No fd/inode sharing across relay_ids in the combined mount.
- Audit log line per write-class op (relay_id, owner, path, op).
- AppArmor: the future relay profile must allow the extra bind
  (`mount options=(rw, bind) /tmp/pf_combined_fs/relay_fs/ -> /relay_fs/`)
  plus the existing fuse mount; pools need no profile change (plain `-v`).
- Windows hosts: nothing new — remote relay side is pure file I/O; hub relay
  and pools live on the Linux server host.

### 7. Tests

- Unit: posix executor jail (escape, symlink, rename-out), errno mapping,
  fd lifecycle on disconnect.
- Unit: router authorization (hub-only), ENOTCONN/EIO mapping, manifest
  reconciliation.
- Integration: fake-WS pair exercising read+write through all three layers
  (no docker needed — same style as test_relay_server_fs.py).
- Source-check: pools' mount args fallback branch (extend
  tests/test_cli_workspace_mounts.py).
- VPS validation script: mount a real remote relay, `git status` + write +
  rename from inside a pool container.

### 8. Phasing

| Phase | Content | Ships alone? |
|---|---|---|
| P0 | Verify cc_sessions host-propagation mechanism on the VPS | n/a |
| P1 | wfs read ops + rwfs router + relay_fs read-only subtree + manifest | yes (ro fallback already useful) |
| P2 | write op set end-to-end (rw default) | yes |
| P3 | pools bind branch + docs (deployment.md, tasks.md, security_model.md) | yes |
| P4 | hardening: audit log, AppArmor relay profile covering the new bind, attr/readdir micro-cache (only if CLI scans prove unbearable) | yes |

Rough size: comparable to the original server-fs feature (relay_server_fs.py
+ server_fs_mount.py + tests ≈ 2–3 focused sessions).

## Explicitly out of scope

- Performance tuning beyond a trivial attr cache — fallback path, MCP tools
  remain the canonical, fast route.
- rclone/sshfs direct connections to remote machines (NAT-bound relays).
- Mounting remote relays into the **server** container (option A, rejected:
  widens the most privileged component).

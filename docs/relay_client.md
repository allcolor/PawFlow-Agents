# PawFlow Relay Client

PawFlow relay lifecycle is separate from PawFlow clients.

- Webchat, PawCode, VS Code, and API clients open conversations and send messages.
- Server physical relays are configured and connected from Webchat **Settings > Server relays**.
- Client relays are started by the standalone PawFlow Relay client on the machine that owns the files or desktop.

This separation keeps PawCode and the VS Code extension equivalent to the webchat: they do not create, start, stop, or own relays.

Server relays do not ask the user for a filesystem path. PawFlow allocates their
workspace from the relay scope and mounts that directory into the relay
container at `/workspace`:

- global scope: `data/runtime/relay/global`
- user scope: `data/runtime/relay/<user_id>`
- conversation scope: `data/runtime/relay/<user_id>/<conversation_id>`

These existing workspace locations are retained during migration. Additional
logical directories receive separate storage under
`data/runtime/relay_workspaces/<scope-and-service-digest>`. Physical groups can
contain multiple logical relays, with service IDs unique across scopes because
the reverse WebSocket route is global (`/ws/relay/<service_id>`). Managed groups
cannot change owners or scopes. Their physical names are editable; existing
logical service IDs and conversation bindings remain unchanged.

First-run installation creates a physical parent for its server relay and starts
that group. If connection times out, the group and its generated token remain
available in **Settings > Server relays** for diagnosis and retry.

A WebSocket registration must provide the token and logical relay ID belonging to its `/ws/relay/<service_id>` endpoint. A mismatched or missing identity is refused before connection hooks run. Rejected tokens and incomplete fence handshakes do not trigger relay-disconnect hooks or recovery bookkeeping for an existing logical relay.

The disposable production-server acceptance uses `HTTPListenerService`,
`RelayService`, real session authentication and the actual sessions, FileStore
and skills handlers. Two synthetic users read distinct bytes at the same
session paths; foreign files, forged user arguments and traversal attempts must
be refused across reconnects. Its mounted CI stage repeats the storage checks
through the real grouped workers and FUSE, verifies that raw server data is
hidden, and checks complete supervisor cleanup. It uses fresh private data and
never starts or reconfigures an installed PawFlow server.
Mounted denials are checked using filesystem error numbers in the worker.
This server-storage scenario uses two writable workspaces so both workers can
execute the read probes; the preceding kernel and relay scenarios separately
require the readonly worker to reject writes.
The CI command preserves the process exit status while retaining its log;
a failed probe cannot be reported as a successful validation.

Server-side relay sessions track in-flight reverse filesystem requests per WebSocket connection. When a relay disconnects or is removed from the pool, those pending request tasks are cancelled so stale connections cannot retain writers, loops, or queued FUSE work.

## Reusing a relay and sharing a physical container

A relay can be linked to several conversations. In Webchat, use
**Resources > Relays > Link Relay**, or run `/relay link <relay_id>` and
`/relay default <relay_id>` in each conversation. These links reuse the same
relay endpoint and the same workspace.

The standalone client configuration now represents one physical relay with
1..N logical workspace shares. Relay Desktop displays the physical parent and
its logical children. Select the parent to configure its complete directory
list, connect all children or disconnect all children. Selecting a logical
relay displays its published name, directory and permissions and links back to
its parent's configuration; it has no independent connection controls.
The module entry point (`python -m pawflow_relay`) routes `physical`, `verify`
and `key` to the same manager used by the installed CLI and Relay Desktop.
The disposable Electron configuration acceptance drives the actual renderer,
preload IPC and Python CLI with a private configuration directory. It checks
single-share migration, adding and removing directories, rejected invalid paths,
permissions and identities after reopening. Docker is deliberately unavailable
in this configuration test; grouped runtime acceptance is a separate gate.
The fixture supplies an empty, non-writing credential backend and uses no login
credentials. CLI saves preserve an existing workspace's mode when no mode is
specified. Set `mode` explicitly through `--config-stdin` or Relay Desktop to
change a readonly workspace back to read/write.

Existing single-directory installations retain their logical service ID,
permissions, persistent HOME volume and Chromium profile. New logical names
are the service names published in PawFlow. Published names must be unique
without regard to letter case. Physical managers are not usable relay services
and do not appear in conversation relay inventories.

The [multi-workspace relay plan](MULTI_WORKSPACE_RELAY_DESKTOP_IMPLEMENTATION_PLAN.md)
records the remaining supervisor, isolation, placement, migration, and UI work.
The delivered **Active Desktops** inventory and `/desktop` lifecycle commands
operate on the current runtime and do not enable physical consolidation.

The launch decision of 2026-09-15 defines one physical relay as one common
Docker container with 1..N logical relays, for both server-managed relays and
Relay Desktop. One connect or disconnect operation on the physical relay
connects or disconnects its complete logical-relay group. Logical relays have
no independent connect/disconnect controls. Each must expose its own directory
at literal `/workspace` in the common container. The directory list is fixed
at startup: changing it restarts the Docker container and disconnects then
reconnects every logical relay in the group. There is no hot addition.
`manager.plan_workspaces(physical_id, names)` validates and snapshots the named
shares without starting services, mounting filesystems or changing saved
configuration. Its immutable plan retains logical IDs, permissions and existing
HOME volume names. The standalone `start`, `cleanup` and `verify` commands target
a physical name. Single-directory groups retain the existing worker path;
multi-directory groups use the static namespace supervisor. Real grouped
mount, FUSE, network and desktop acceptance remains a release requirement.

The Webchat admin server-relay API returns both the logical inventory (`relays`)
and its management hierarchy (`physicals`). On load, legacy `MyWorkspace` receives
a separately named `MyWorkspace (physical)` parent while retaining its service
ID, credentials, permissions, workspace path, HOME volume and conversation
bindings. Reading/migrating configuration does not replace a connected worker.
The parent reports connected, partially connected or disconnected children.

**Settings > Server relays** provides the physical name, complete directory list,
per-logical read/write mode and permissions, and group connect/disconnect/reconnect
controls. New server directories are allocated automatically. Saving validates
the complete configuration before stopping an existing group. A connected group
is restarted with all selected children; a stopped group remains stopped. Stops
and directory removal retain stored files and HOME/Chromium. Readding a removed
logical ID to the same parent restores its previous paths and credentials.
The original letter case must be retained when readding that ID. Installing a
managed workspace starts its canonical physical group with the saved permissions.
If startup fails, the group remains available for retry from Server relays settings.

The server stores each complete physical configuration in one atomic document
under `relay_physicals` in the configured runtime directory (by default,
`data/runtime/relay_physicals`), with encrypted credentials including those of
retired members. It follows the central runtime path so separate runtime stores
remain isolated. Per-service files are recoverable logical projections; the
parent wins over stale files after an interrupted save. Revision checks reject
an outdated directory form. Explicit stop intent is persisted before container
cleanup, so a child retry cannot bring the group back after a stop. All children
remain manageable after failed deletion; the group is removed from the inventory
only after container cleanup succeeds. Docker inspection errors fail the operation
instead of being treated as proof that the container is absent. All children
share the 15-second reconnect grace and 60-second spawn cooldown; connections
must remain stable for 5 seconds to reset outage tracking.

Admin actions `admin_server_physical_get`, `save`, `start`, `stop`, `restart`,
`delete` and `operation` take the physical ID and scope. Lifecycle actions return
HTTP 202 with an operation ID; the operation endpoint reports completion or
failure, and Webchat keeps failed forms available for correction. Logical
permission switches persist through the parent. Generic logical edit, rename,
enable, disable, uninstall and reconnect paths cannot bypass the group manager.
Group deletion retains storage and a record preventing stale logical definitions
from reappearing. The grouped bootstrap pivots into the private root and detaches
the original root before application startup. A nested user/mount namespace
retains mapped UID/GID ownership while limiting root capabilities to that
logical view and locking inherited read-only mounts; each worker has a private
init process to reap orphan descendants. Before application startup, the
privileged parent writes both [identity maps](https://man7.org/linux/man-pages/man7/user_namespaces.7.html)
directly into its unreaped child's
proc files. The child waits for explicit confirmation, and a mapping failure
kills and reaps it. This preserves all parent-visible ranges without depending
on `newuidmap`/`newgidmap` or subordinate-ID delegation files. See the Linux
[mount namespace restrictions](https://man7.org/linux/man-pages/man7/mount_namespaces.7.html)
for the kernel rules this design relies on. Grouped Docker launches load the
bundled `pawflow_relay/physical-seccomp.json`: Moby's default seccomp profile pinned
at `61eaf32614c7c71b60bd8927d3e6a4ffc8ff1f31`, with only `pivot_root` added under
`CAP_SYS_ADMIN`. Docker's default profile blocks that call even when `SYS_ADMIN`
allows the preceding mounts. The Docker CLI reads the bundled profile locally;
the Desktop launcher translates its path for WSL. Real grouped mount, FUSE, network and desktop acceptance is
still required before release; source and mocked lifecycle tests alone do not
establish runtime isolation.

The `Physical Runtime Acceptance` workflow runs the first kernel validation stage
on a disposable GitHub-hosted Ubuntu runner. It builds the project's minimal relay
image, including the required `tini`, `slirp4netns` and `util-linux` packages, and
runs `tests/physical_runtime_probe.py` inside a dedicated container. Two synthetic
shares exercise literal `/workspace`, separate namespace identities, readonly and
trusted-code remount refusal, sibling credential visibility, executable files,
symlinks, DNS and HOME/profile sentinels. Worker failure and explicit supervisor
stop must remove every observed descendant, verified with PID and creation time.
The evidence artifact retains the exact image metadata, logs and scenario results.
A second disposable container runs `tests/physical_relay_probe.py` with the real
relay launcher, command dispatcher and combined FUSE implementation. A synthetic
WebSocket server validates the two logical registrations and serves distinct
readonly fixtures for sessions, FileStore and skills. Both workers hash their
own workspace and all three mounted fixtures, reject missing files and local
host access, and repeat fresh FUSE reads after a forced WebSocket reconnect.
The readonly worker must refuse writes. The writable worker must execute a
command and retain its FUSE mount identity and HOME/profile sentinel across the
reconnect. Shutdown must remove all observed worker/helper descendants.
`relay-runtime.log` and `relay-result.json` retain this stage's evidence separately.
The fixture server supplies synthetic protocol replies; this does not validate
the production server's authentication/storage handlers or Windows/WSL.

The workflow's separate `desktop-runtime` job builds the base with the existing
`desktop.runtime`, `desktop.audio` and `browser.chromium` features. Two writable logical workers
start real Xvfb/XFCE/noVNC desktops using identical internal display and port
numbers. Each must return a PNG screenshot and exchange the VNC protocol greeting
through the production WebSocket bridge. Chromium runs visibly against a local
synthetic page in each private network namespace. Before capturing the page, the
probe brings it to the foreground and waits up to five seconds for a visible
first contentful paint; a loaded DOM alone is insufficient screenshot evidence.
The first round writes distinct
persistent cookies and local storage; after a complete group stop and restart,
the second round must recover both values from each worker's original HOME.
In both rounds, the workers play distinct synthetic tones into their private
PulseAudio null sinks. The production audio WebSocket tunnel returns Opus packets;
the fixture decodes three seconds per relay and requires the intended frequency
to exceed the sibling frequency by at least tenfold. Silence and crossed streams
fail. Both shutdowns verify that all observed worker, helper, audio and desktop
descendants are gone. `physical-desktop-evidence` retains decoded WAV recordings,
screenshots, runtime logs and
`desktop-result.json`. This exercises Linux runtime components; the Relay Desktop
application, production server integration and Windows/WSL still require
their own acceptance checks.

Configure a standalone group from the CLI:

```bash
pawflow-relay physical save laptop --server prod \
  --workspace Code ~/src/project --workspace Docs ~/Documents --read-only Docs
pawflow-relay physical list
pawflow-relay start laptop
pawflow-relay verify laptop
pawflow-relay cleanup laptop
```

`physical save` replaces the complete directory list and requires the group to
be stopped. `--config-stdin` accepts a JSON object containing `server`,
`docker_image` and `workspaces`, including each workspace's permissions and
existing `relay_id`. Add `--validate-only` to validate without saving or stopping.
To rename a physical parent, supply its existing `physical_id` in that JSON or
`--physical-id ID` alongside the new name. Relay Desktop retains this ID while
you edit the name. The physical identity, logical identities, permissions and
HOME volumes remain unchanged; an occupied parent name is rejected.
Read commands, prevalidation and `manager.plan_workspaces()` normalize legacy
records in memory. The next successful configuration mutation persists migration;
reading or rejecting a configuration leaves the saved file unchanged.
Workspace mutations hold one exclusive operating-system lock across reading,
validation, the stopped-runtime check and atomic replacement. Group startup takes
its configuration snapshot and runtime lock within the same transaction, so it
cannot launch an obsolete directory list during a save. The lock is shared by
Desktop, physical commands and the single-directory workspace commands.
On Windows, contention waits until the current transaction releases its lock,
including stops that take longer than ten seconds; other lock errors propagate.
An implicit CLI rename that reuses a removed member's directory is rejected.
Supply its existing `relay_id` through `--config-stdin` to retain the identity,
permissions and HOME, or explicitly remove and then add a replacement. An
explicit new `relay_id` also identifies an intentional replacement.
Relay Desktop validates first, stops a running group, saves, then restarts the
physical relay. Failed validation leaves the running group intact; failed
cleanup prevents saving; a failed save restarts the previous saved configuration.
Deleting a physical configuration retains its directories and persistent HOME.
Runtime status includes launcher locks created by the CLI, so Desktop displays
and controls externally started groups as well as its own processes. A running
parent rename stops the old name and starts the new name; a failed save restores
the old name. Disconnecting an idle parent in Desktop performs no cleanup.
CLI cleanup unregisters services only when a runtime was observed, and performs
container cleanup before server unregistration.
Desktop invokes this backend stop while the launcher lock still exists, allowing
the backend to retry unregistration after a graceful child shutdown. If a launcher
is still alive or Docker cannot list or remove its containers, cleanup fails
without removing the runtime lock or unregistering services. Saving and restarting
remain blocked by that failure. If a concurrent launcher already removed a
container, a failed Docker removal is accepted only after a successful fresh
listing confirms that container is gone.
On Windows, launcher liveness uses a zero-timeout process wait with pointer-sized
handles. A terminated launcher is no longer reported as running even if another
handle keeps its process object alive. Access-denied and indeterminate checks
retain the lock; only a signaled process or an absent PID permits stale-lock
removal. This check does not establish that Docker cleanup has finished, and it
does not change the relay's startup or WebSocket health retry timing.
An incomplete cleanup remains visible as `cleanup_pending`, even if the child
already removed its launcher lock. Desktop displays **Retry cleanup**, and
connect/save/delete settle that cleanup first; saving a stopped parent keeps it
stopped. CLI startup also settles pending cleanup before launching. Retry state
alone does not count as evidence that a runtime existed.
Unregistration retries accept an already absent service; other server errors
retain the pending cleanup instead of reporting success. If a runtime was
observed but the server account is logged out, local container cleanup still
runs and the service unregistration remains pending. Log in with
`pawflow-relay server login <server>`, then retry `pawflow-relay cleanup <physical>`.
A server outage also keeps cleanup pending; restore connectivity before retrying.
CLI `physical delete` and `workspace delete` require this explicit cleanup first.
If Docker is unavailable, restore the configured Docker command or daemon before
retrying; removing a lock manually does not establish that cleanup succeeded.
When the local stop is what should win — the launcher is gone and only a
server-side step fails — `pawflow-relay cleanup <physical> --force` completes it:
the runtime lock is released and the failed step is reported in `skipped`, so the
incomplete cleanup stays visible instead of being dropped. Relay Desktop offers
the same action as **Force cleanup**, next to **Retry cleanup**, once a cleanup is
pending. Force is never chosen automatically, and it never releases a launcher
that is genuinely still running.
Relay Desktop appends everything it logs to `<app data>/logs/relay-desktop.log`
(bounded to 5 MB), so a failed disconnect can be read after the fact instead of
surviving only in the window that reported it.

`PAWFLOW_RELAY_DOCKER` selects one Docker executable path for both Relay Desktop
and the Python runtime, including container cleanup. Paths containing spaces
remain one argument. An unavailable explicit executable fails without falling
back to a different Docker installation. Without an override, Python uses
`docker` on Unix and `wsl docker` on Windows. On Windows, bind-mount paths
are still translated from drive paths such as `C:/shared` to `/mnt/c/shared`.
An override must therefore target a Docker engine that accepts these WSL paths;
selecting a native `docker.exe` does not enable native Windows bind-mount paths.

Each logical relay's authenticated host helper checks its own permissions before
dispatch. Local filesystem and HTTP operations require `allow_local`; commands,
terminals, code-server and CLI login additionally require `allow_exec`. Desktop
and screen actions use `allow_remote_desktop`, and service tunnels use
`allow_service_tunnels`, independently of the local shell grant. An explicit host
grant retains access to host paths outside the share; it does not provide sibling
workspace confinement on the host.

For standalone multi-directory groups, failure to start a host helper or Windows
WSL bridge, or its later exit, retires the complete container attempt. All group
helpers, tracked connections and local terminals are cleaned before retry. Retry
delays grow from 1 second to a maximum of 60 seconds, and an explicit stop prevents
another attempt. The single-directory path retains its healthy helper across
container reconnects. Unit tests cover injected failures and real temporary
listener cleanup. The `Physical Windows WSL Acceptance` workflow imports a
dedicated Ubuntu distribution with systemd and Docker on a disposable Windows
runner. It launches the real grouped kernel and relay/FUSE probes through native
Python and `wsl docker`, including translated Windows bind paths containing
spaces. A separate native Python fixture starts the production
`PhysicalRelayThread` against a synthetic HTTP/WebSocket server on that runner.
It requires both logical registrations, per-workspace Windows filesystem
forwarding, host execution permissions, distinct helper capabilities and FUSE
reads. It then stops an exact helper and an exact WSL bridge, requiring complete
group replacement after each failure, followed by an explicit stop/start. Private
HOME/profile sentinels must survive all four connections. The fixture accesses
the owned container's per-member HOME mounts as UID/GID 1000 to inspect these
sentinels; filesystem protocol commands remain confined to their allowed paths.
Execution denial requires the exact host permission error, so a broken connection
cannot count as permission enforcement. Each stopped group must
leave no container, helper thread, bridge process or listening port. The runner
temporarily permits inbound TCP only for its test Python executable and removes
that firewall rule in a finally block. `native-client-result.json` is separate
from the kernel/FUSE report; source checks alone do not satisfy this runtime gate.

## Admin-controlled server-local execution

Managed server relays execute in their own isolated workspace by default. An
administrator can open **Server settings → Server Relays** and enable
server-local execution for one managed relay. When enabled, filesystem and shell
tools sent to that relay with `local=true` run inside the PawFlow server
container instead of the relay container. This gives access to server logs and
to the Docker socket mounted in the PawFlow container.

The switch is disabled by default, persists with the relay service definition,
and applies immediately without restarting the relay. Only the dedicated admin
API may change it; normal service updates reject the internal
`server_local_exec` field. Standalone relays keep their existing semantics:
`local=true` uses their authenticated local host-helper path when configured.
The webchat Terminal and Desktop commands offer the same container/local mode
picker for an enabled managed relay. In that case, local means the PawFlow
server container; terminal I/O and the noVNC proxy remain bound to the normal
authenticated browser-session routes.

Opening a terminal does not queue behind tool commands. The relay runs incoming
commands on a thread pool of a fixed size, and a handful of agents running long
tools keep every worker busy for minutes: an `open_terminal` then waited for a
free worker -- measured 188s and 97s on a live relay, both returning the moment
tool results landed, which is also why the keystrokes of a terminal opened that
way would have queued. Interactive actions (terminal spawn, close and listing;
desktop start/stop/status and audio; code-server start/stop and its WebSocket
open; the noVNC asset and VNC WebSocket open; website browser start/stop) now
run on their own thread, so their latency no longer depends on how much batch
work the relay happens to be doing. Keystrokes and resizes are different: they
must keep their order, so each terminal session has one FIFO that runs them one
after another -- deliberately not inline in the message loop, where an
`os.write` to a full PTY, or a forward to the host helper, would stall every
other command.

How many commands share that pool is the operator's number:
`PAWFLOW_RELAY_COMMAND_WORKERS` sets it, the worker prints the concurrency it
uses when it connects, and a command that waited more than a few seconds for a
worker says so on the relay log. Unset means no ceiling at all: every command
gets its own thread. A fixed number of workers made the relay a bottleneck --
long tool runs kept all of them busy, an `open_terminal` waited 188s behind them,
and other callers hit `Relay timeout for exec` while the relay was in fact
healthy, merely queued. A ceiling is the operator's to set, never an implicit
one.

`close_terminal` takes its place in the session's FIFO instead of running on a
free thread, so it cannot overtake the keystrokes still queued for that session,
and the FIFO's worker retires with the session: nothing used to stop it, so one
thread stayed parked on `get()` for the life of the connection, and because the
per-connection session is rebuilt on every reconnect, a fresh series began each
time.

The relay loop's last hard-coded deadline was `_DEAD_TIMEOUT = 90`, the silent
socket after which it forces a reconnect. `PAWFLOW_RELAY_DEAD_TIMEOUT` sets it,
and the value in force is printed when the worker connects.

`open_terminal` is deliberately sent without a `_request_timeout`: the transport
reads "no timeout" as waiting for the relay to answer, which is the right
contract for an operation a person is watching. The waiting is kept sane by the
lane above, not by capping it.

## A managed container that dies is respawned

The container of a managed server relay is started once, from
`RelayService.connect()`, and runs with `--rm`. Nothing else used to re-create
it: if it crashed, or an operator ran `docker rm -f pawflow-relay-srv-<id>`,
the transport kept retrying against a container that no longer existed and the
relay stayed down until the whole PawFlow server was restarted.

So when a request fails with a disconnect error and is about to be retried, the
service calls `ensure_managed_relay_alive()`:

- **Unmanaged relays are never touched.** An operator-run relay is theirs to
  restart; PawFlow owns no container for it.
- **A live WebSocket is the health signal.** A connected relay is left strictly
  alone, and a container that is actually gone is respawned immediately. A
  running container receives a 15-second grace period for the relay worker's own
  reconnect loop. PawFlow rechecks the WebSocket after Docker inspection and
  again immediately before replacement, so it cannot kill a relay that
  reconnected while recovery was being decided. Only a continuously
  disconnected running process past the grace is treated as wedged.
- **One respawn per cooldown window** (60 s), so a burst of failing tool calls
  asks for one container start rather than one per call.
- **Container-name recovery is idempotent.** Starts for the same deterministic
  Docker name are serialized. A healthy container started by an overlapping
  attempt is reused instead of replaced, while an explicit reconnect or a relay
  that stayed disconnected past its grace requests replacement. PawFlow verifies
  the spawned, server-id, and managed-relay labels before removal; an unlabelled
  container or one owned by another server is never touched.
- **A failed respawn is logged, not raised.** The caller is a transport retry
  that has its own error to report.

The retry window is five attempts, five seconds apart, which is usually enough
for the new container to come up and connect back. If it is not, the request
still fails — but the relay is on its way back, and the next tool call finds it
connected instead of needing a server restart.

An editable managed server relay also exposes **Reconnect** in its webchat relay
details dialog. This explicit action immediately replaces the disposable relay
container even when its WebSocket still appears connected. It never invokes
relay cleanup: the scoped workspace, home volume, service definition, and relay
bindings are preserved. Global relays require an administrator, and standalone
Relay Desktop clients do not expose this action because PawFlow does not own
their process lifecycle.

## CLI

The standalone relay client is exposed as `pawflow-relay` when installed from the Python package, or as `python -m pawflow_relay` from a checkout.

Add a server profile:

```bash
pawflow-relay server add prod https://pawflow.example:PORT --gateway-key RoyBatty
```

Login to the server:

```bash
pawflow-relay server login prod
```

Add a local workspace share:

```bash
pawflow-relay workspace add repo --server prod --path ~/src/project --mode rw
```

Start the relay:

```bash
pawflow-relay start repo
```

The legacy direct mode remains available for low-level scripting:

```bash
python -m pawflow_relay --server https://pawflow.example:PORT --dir ~/src/project
```

## Shell PATH

The shells the relay spawns for `bash` and friends are non-login and
non-interactive, so `.profile`, `.bashrc` and `/etc/environment` are never
read. To keep a tool the relay account installed for itself reachable, every
exec prepends the account's own bin directories to `PATH`:

1. `~/bin`
2. `~/.local/bin` (where pip/pipx put user installs)

Only directories that exist are added, and never twice — an environment that
already exports one of them keeps its own ordering. A `PATH` passed
explicitly in the request's `env` overrides this entirely: an explicit value
is an instruction, not a suggestion.

This is the only PATH manipulation the relay performs. Anything else must be
exported by whatever launches the relay process, since the exec environment
is inherited from it (`os.environ.copy()` in `tools/fs_exec.py`).

## Local State

The relay client stores server and workspace profiles outside the project tree:

- Linux/macOS: `~/.pawflow/relay/`
- Windows: `%APPDATA%\\PawFlow\\relay\\`
- Override: `PAWFLOW_RELAY_HOME=/custom/path`

Profiles are split into `servers.json` and `workspaces.json`. Gateway keys and session tokens are currently stored in this local profile; the desktop client should migrate secrets to the OS keychain before a stable release.

When a server profile has a Private Gateway key, relay HTTP lifecycle calls
(`/api/ui`) send it in `X-PawFlow-Gateway-Key`. The same key already protects
the relay WebSocket handshake. The challenge cookie remains supported for
browser and previously authenticated client sessions.

## Relay Desktop

The Electron Relay Desktop slice lives in `pawflow-relay-desktop/`. It uses the same local state as the CLI and manages:

- server profiles: URL, private gateway key, login status;
- workspace shares: path, read/write mode, relay image/profile, local execution
  permission, and opt-in FRP service-tunnel permission;
- running relay processes and logs;
- Docker relay images and custom image builds.

Enable **Allow service tunnels (FRP)** on each relay that may participate in a
tunnel, then restart that relay so it advertises the capability. Tunnel
creation and lifecycle controls are in the webchat **Resources → Service
Tunnels** section; Relay Desktop controls the local relay permission, not the
user-scoped tunnel records.

Stopping a relay from the desktop UI, or quitting the tray app, stops the
launcher process and also performs relay runtime cleanup: the registered relay
service is uninstalled best-effort and Docker containers whose names belong to
that workspace relay id are removed. This cleanup is independent from Python
signal handling so Windows process termination cannot leave the relay container
running after the desktop app exits.

Client-owned container names use a SHA-256 digest of the complete relay ID.
Cleanup validates the complete generated name before removing a candidate;
relays sharing a username or the first twelve characters of their IDs cannot
remove each other's containers. Historical names based on truncated IDs are
ambiguous and are not selected by this orphan cleanup. A running launcher can
still stop the exact container name it retained. Named HOME volumes, including
Chromium profiles, are not removed by container cleanup.

Run it from a checkout:

```bash
cd pawflow-relay-desktop
npm install
npm start
```

Release builds use `pawflow-relay-desktop/npm run dist:<platform>`. The build
prepares the runtime payload, creates a PyInstaller relay executable under
`runtime/bin/`, and packages it with Electron Builder (`nsis`/`zip` on Windows,
`AppImage`/`deb` on Linux, `dmg`/`zip` on macOS). In packaged mode the desktop
app launches the embedded relay binary and uses the Python fallback only for
source checkouts.

Windows builds collect the complete `winpty` package, including its native DLLs
and helper executables. The release workflow then opens a packaged PTY, writes an
interactive probe through it, and reads the probe back. Importing `PtyProcess`
alone is not a sufficient packaging check because missing helper executables can
leave the module importable while every spawned terminal exits immediately.

Windows builder hosts need symlink creation enabled because Electron Builder's
`winCodeSign` cache contains symlinks. Use Windows Developer Mode or an elevated
PowerShell, clear `%LOCALAPPDATA%\electron-builder\Cache\winCodeSign` after a
failed extraction, and set `CSC_IDENTITY_AUTO_DISCOVERY=false` for unsigned local
installer builds.

PawCode and VS Code should not grow relay management screens. If a conversation has no linked relay, they can show server state, but relay creation and attachment remains a webchat/server-resource or Relay Desktop responsibility.

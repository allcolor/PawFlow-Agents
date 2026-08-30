# Multi-Workspace Relay and Desktop Implementation Plan

Status: **proposed** (architecture and implementation plan only; no runtime
implementation in this change).

This plan defines how PawFlow will expose several isolated logical relay
workspaces from one physical relay container, including workspace-correct
virtual Desktops, server-managed relays, standalone/remote Relay Desktop,
Webchat controls, and slash commands for clients without the Webchat UI.

It complements:

- `docs/REMOTE_RELAY_ENROLLMENT_SHARING_PLAN.md`, which owns relay node and
  endpoint identity, enrollment, ACLs, and cross-user authorization;
- `docs/RELAY_WORKSPACE_FS_PLAN.md`, which owns server-to-relay workspace
  filesystem transport;
- `docs/relay_client.md`, which documents the current standalone and
  server-managed relay lifecycle;
- `docs/desktop_vnc.md`, which documents the current Desktop/VNC transport.

Where those plans overlap, this document owns physical runtime consolidation,
per-export process isolation, Desktop lifecycle, and the related user
surfaces. It does not weaken their authorization requirements.

## 1. Executive decision

The target is:

~~~text
one physical relay instance
└── one supervisor container
    ├── logical export A
    │   ├── one relay worker process
    │   ├── one private mount/PID/IPC/runtime namespace
    │   ├── /workspace -> directory A
    │   └── zero or one virtual Desktop session
    ├── logical export B
    │   ├── one relay worker process
    │   ├── one private mount/PID/IPC/runtime namespace
    │   ├── /workspace -> directory B
    │   └── zero or one virtual Desktop session
    └── logical export N
~~~

The following decisions are non-negotiable:

1. Every logical export remains a distinct PawFlow relay endpoint with its own
   service ID, token, outbound WebSocket connection, root, capabilities, and
   authorization context.
2. A logical export is implemented by a separate worker **process**, not by a
   thread or a mutable root field in one multi-connection worker.
3. Each worker receives a private mount namespace in which its export is
   mounted at `/workspace`. Path rewriting alone is insufficient because shell
   commands, FUSE mounts, Desktop applications, code-server, and child
   processes use literal absolute paths.
4. Every active Docker virtual Desktop belongs to exactly one logical export.
   X11, D-Bus, runtime directories, audio, VNC/noVNC, temporary files, HOME,
   Chromium profile, logs, and child processes are session/export scoped.
5. Closing a Webchat Desktop tab only detaches that viewer. It never stops the
   Desktop.
6. PawFlow never stops a healthy Desktop because it is idle, has no viewer, the
   browser disconnected, or a timer expired. Desktop stop is a deliberate user
   or administrator action.
7. A physical instance cannot be stopped, upgraded, regrouped, or removed while
   it owns an active Desktop without an explicit warning and confirmation.
8. A real host Desktop opened through `/desktop local` is machine-wide and
   cannot honestly be described as workspace-isolated. Only the Docker virtual
   Desktop satisfies the requirement that `/workspace` represents one export.
9. The persistent HOME and Chromium profile remain per logical export and must
   survive image changes, container recreation, cache cleanup, and migration.
10. Physical consolidation is allowed only inside one explicit trust group. It
    is not a secure replacement for a container boundary between mutually
    untrusted users.

## 2. Problem statement

Today, a standalone `WorkspaceShare` owns a path, image, permissions, and
relay ID. Starting that share creates a `RelayThread`, which creates one
Docker container and one host helper for that relay. Ten active paths therefore
normally produce ten relay containers.

Server-managed relays have the same physical assumption:
`ServerRelayManager` and `core/_server_relay_container.py` create and
supervise one container for one managed relay service.

The memory limit passed to Docker is a ceiling, not preallocated RAM, and image
layers are shared. Nevertheless, every container carries its own worker,
Python runtime, FUSE state, threads, `/tmp`, HOME plumbing, code-server
processes, and possibly XFCE/Chromium. The overhead becomes material, especially
when several Desktops are active.

The current Desktop implementation also assumes one Desktop per worker process:

- `pawflow_relay/_relay_desktop.py` stores one set of
  `desktop_procs`, ports, and display values in `RelayWorkerState`;
- the default display is `:99`;
- the code mutates process-global `DISPLAY`;
- Desktop and local-Desktop logs use fixed paths in `/tmp`;
- D-Bus uses `/tmp/dbus-desktop`;
- XDG runtime state uses one user-derived path;
- PulseAudio is killed/restarted globally for the current user;
- Xvfb uses `-ac`;
- the relay image currently grants `pawflow` unrestricted passwordless sudo;
- the candidate relay AppArmor policy is not yet the enforced multi-export
  security boundary.

Those assumptions are safe only while one worker maps to one container. They
must be removed before workers share a container.

The Webchat already distinguishes detach and stop:

- `/desktop close` closes a local tab and leaves the Desktop running;
- `/desktop stop [relay]` sends `close_desktop` and stops the backend.

The new dock control must preserve that distinction and make backend state
visible instead of inferring it from open browser tabs.

## 3. Goals

The implementation must:

1. run ten compatible logical exports in one physical Docker container;
2. keep each logical relay ID, token, WebSocket, root, binding, and capability
   independently addressable;
3. make `/workspace` resolve to the correct directory in filesystem tools,
   shell processes, terminals, code-server, Desktop applications, Chromium,
   screen automation, and every descendant process;
4. allow several virtual Desktops to run concurrently in the shared container;
5. expose active Desktop inventory and deliberate stop controls in:
   - the PawFlow Webchat action dock;
   - the standalone remote Relay Desktop UI and tray;
   - slash commands used by PawCode, VS Code, and other clients;
6. support both server-managed physical relay instances and remote
   user-machine physical relay instances;
7. preserve existing per-export HOME data, especially Chromium profiles;
8. prevent one worker from inspecting, signalling, mounting, or connecting to
   another worker's Desktop/session resources;
9. keep failure, restart, metrics, logs, and resource limits attributable to a
   logical export and a physical instance;
10. provide a one-shot migration with a preflight and recoverable data backup;
11. ship documentation and automated coverage in the same implementation
    changes.

## 4. Non-goals

The first release does not provide:

- secure co-tenancy for mutually untrusted users in one physical container;
- a shared X server or shared XFCE session across logical exports;
- automatic suspension or idle shutdown of healthy Desktops;
- live migration of running terminals, GUI applications, or Desktop processes
  between physical instances;
- workspace isolation for the real host Desktop;
- arbitrary dynamic root selection within one logical export;
- one logical relay endpoint serving several roots;
- container consolidation across different image digests or incompatible
  security/resource profiles;
- Kubernetes or multi-host scheduling;
- automatic merging of existing trust groups without operator/user review;
- backward-compatible dual operation of old and new supervisor protocols.

## 5. Terminology and identity

### 5.1 Physical relay instance

A physical relay instance is one supervisor runtime, normally one Docker
container, hosted by either:

- the PawFlow server for server-managed relays; or
- Relay Desktop/standalone relay manager on a remote machine.

It has a stable `physical_instance_id`, image digest, owner/trust domain,
aggregate resource policy, supervisor protocol version, and desired export set.

### 5.2 Logical export

A logical export is the user-visible relay endpoint. It has:

- stable `export_id`;
- existing globally unique `relay_id` / service ID;
- one canonical root path or host-side opaque root handle;
- one mode (`ro` or `rw`);
- one endpoint token/credential;
- one capability set;
- one owner and authorization policy;
- one persistent HOME identity;
- one worker lifecycle.

The relay ID remains the identifier used in conversation bindings and tool
routing. Physical instance IDs are operational grouping metadata and must not
replace relay IDs in user authorization.

### 5.3 Export worker

An export worker is the unprivileged process that owns one logical relay
connection and executes its requests. It inherits only that export's namespace,
credential file, root, HOME, runtime paths, limits, and capabilities.

### 5.4 Desktop session

A Desktop session is the backend GUI process group for one export. Version 1
allows at most one Docker virtual Desktop per logical export. It has a stable
random `desktop_session_id` for its lifetime.

A browser iframe, VS Code webview, or CLI-returned URL is a **viewer
attachment**, not the Desktop session itself. Many viewers may attach to one
session. Detaching the last viewer does not stop it.

### 5.5 Trust group

A trust group is the explicit statement that its exports may share a container
and root supervisor. The first release permits grouping only when all exports
have the same effective owner and compatible security policy. It never groups
different PawFlow users, global and private ownership domains, or endpoints
whose ACL design treats them as mutually untrusted.

## 6. Isolation and threat model

### 6.1 Security statement

A worker boundary inside one container is weaker than a Docker container
boundary because the root supervisor necessarily controls all exports.
Therefore:

- physical grouping is an optimization inside one trust domain;
- the UI must say "shared container" and show the group membership;
- PawFlow must not advertise grouped exports as cross-tenant container
  isolation;
- cross-user grouping is blocked, even if ACLs would otherwise allow both users
  to discover the endpoints.

### 6.2 Required invariants

Before the feature can be enabled, tests must prove:

1. A worker sees exactly one `/workspace` root.
2. An absolute shell path to `/workspace` cannot reach another export.
3. The hidden export staging tree is absent from the worker namespace.
4. The worker cannot enter another worker's mount, PID, IPC, user, or network
   namespace.
5. The worker cannot read another endpoint token, HOME, profile, logs, temp
   files, X11 cookie, D-Bus socket, PulseAudio socket, VNC secret, or control
   socket.
6. The worker cannot signal or ptrace another worker or its Desktop processes.
7. A read-only export remains read-only at both the filesystem broker and mount
   layers.
8. Stopping one worker/Desktop does not stop or unmount another.
9. A worker crash cannot revoke or overwrite another worker's registration.
10. One endpoint cannot assert a different export ID in a control message.
11. VNC/noVNC/audio sockets are not usable without the export/session
    capability.
12. No root path, endpoint token, gateway credential, or VNC secret appears in
    process arguments, ordinary logs, UI payloads, or metrics labels.
13. Missing required IDs, roots, modes, owners, and protocol versions fail with
    a validation error. There is no anonymous/default export fallback.
14. No healthy Desktop is stopped by an idle timer or viewer count.

### 6.3 Privilege separation

The current unrestricted `sudo NOPASSWD:ALL` grant must be removed from the
multi-export image.

The physical container contains:

1. a minimal root supervisor/namespace launcher;
2. an export filesystem mount component with only the privileges it needs;
3. unprivileged workers with all capabilities dropped;
4. Desktop child processes running under the worker's unprivileged identity.

The supervisor retains only the capabilities required to create and tear down
namespaces, mounts, UIDs, cgroups, and virtual networking. It does not execute
user commands. Its control socket is root-owned and accepts a versioned,
allowlisted protocol.

Workers run with:

- `no_new_privs`;
- a per-export UID/GID or isolated user namespace;
- mount, PID, IPC, and network namespaces;
- a private `/tmp` tmpfs;
- a private `XDG_RUNTIME_DIR`;
- a per-export cgroup;
- a seccomp policy;
- an enforced relay AppArmor/SELinux profile where supported;
- no Docker socket and no supervisor credential.

If a platform cannot establish the required namespace boundary, it must use one
container per export. It must not silently downgrade to a thread-only shared
runtime.

## 7. Target architecture

### 7.1 Remote Relay Desktop

~~~text
Remote machine
├── Relay Desktop / manager (host process)
│   ├── physical instance store
│   ├── export root broker
│   ├── Docker lifecycle
│   └── UI + tray
│
└── supervisor container (one compatible trust group)
    ├── root supervisor
    ├── export A mount endpoint
    │   └── worker A -> WS for relay A
    │       └── optional Desktop A
    ├── export B mount endpoint
    │   └── worker B -> WS for relay B
    │       └── optional Desktop B
    └── export N ...
~~~

The host manager owns canonical host paths. The container receives opaque export
handles and never receives a broad host root merely to make dynamic grouping
easy.

### 7.2 Server-managed relay

~~~text
PawFlow server
├── ServerRelayManager
├── managed relay instance store
├── server export root broker
└── supervisor container
    ├── worker for logical RelayService A
    ├── worker for logical RelayService B
    └── optional Desktop per worker
~~~

Server-managed groups are always scoped to one effective owner/trust domain.
A user-scoped group cannot contain another user's or a global endpoint.
Conversation-scoped endpoints may be grouped only when their effective owner
and explicit group policy match.

### 7.3 Logical network identity

Each export keeps one outbound authenticated WebSocket connection and one
logical `RelayService`. This minimizes changes to tool routing, conversation
bindings, VNC relay transport, and authorization.

The supervisor is not a user-visible filesystem service and does not multiplex
requests for several roots over a mutable worker context. It only supervises
separate workers.

If the central versioned relay connection route from
`REMOTE_RELAY_ENROLLMENT_SHARING_PLAN.md` lands first, each export connection
uses its endpoint identity on that route. Otherwise the implementation may use
the existing globally unique relay service route during development. The
released multi-export protocol must have one documented, version-gated route;
there is no permanent dual-route fallback.

## 8. Dynamic export filesystem design

### 8.1 Why static Docker bind mounts are not the target

Docker cannot add an arbitrary bind mount to a running container. Recreating the
physical container whenever a user adds a directory would interrupt all workers
and destroy every active Desktop/job in that instance.

Mounting an entire drive or common ancestor would expose more host data to the
container than the configured exports and fails for unrelated paths.

Therefore grouped physical instances use a dynamic, export-scoped filesystem
broker rather than an ever-growing Docker bind-mount list.

### 8.2 Export root broker

Relay Desktop (or the PawFlow server manager) opens and validates each configured
root and creates an opaque `export_handle`. The handle is bound to:

- physical instance ID;
- export ID;
- canonical root identity;
- `ro` or `rw` mode;
- effective owner;
- capability revision;
- random mount credential;
- revocation state.

The broker performs root confinement using opened directory handles and
platform-safe path traversal. It never trusts a path sent later by the
container.

The existing host-helper/server-filesystem transport should be extended to
multiplex export handles. A request must carry the handle and worker channel
identity; a path alone is never enough.

### 8.3 Mount sequence

For each logical export:

1. validate the export record and compatibility with the physical instance;
2. mint a short-lived mount credential;
3. create a root-owned staging directory under
   `/run/pawflow/exports/<export_id>/root`;
4. mount the brokered filesystem through the existing FUSE transport;
5. create the worker's private namespaces;
6. bind the staging root to `/workspace` inside the worker mount namespace;
7. mount the export HOME at `/home/pawflow`;
8. mount private `/tmp`, `/run/user/<uid>`, and runtime paths;
9. hide the staging parent and supervisor paths from the worker;
10. apply read-only remounting when required;
11. drop privileges and start the worker with one credential file.

The mount root is private (`MS_PRIVATE`) so worker mounts cannot propagate to
the supervisor or other workers.

### 8.4 Removal and root changes

An export cannot be removed, moved, or changed from `rw` to `ro` while it
has live terminals, code-server, mounts, or a Desktop unless the user explicitly
chooses a force-stop workflow.

Root identity or mode changes create a new export revision. The supervisor:

1. marks the export draining;
2. refuses new sessions;
3. reports blocking sessions;
4. waits for explicit stop/confirmation;
5. stops the worker;
6. revokes the old handle;
7. unmounts;
8. mounts the new revision;
9. starts a fresh worker.

No running process is silently moved to another root.

## 9. Physical supervisor

### 9.1 Responsibilities

The supervisor owns:

- the desired export manifest;
- worker start, stop, restart, and health;
- namespace and cgroup creation;
- dynamic mount attach/detach;
- per-export secret-file delivery;
- resource allocation for Desktop sessions;
- status/event reporting to the host manager;
- aggregate container health.

It does not own user authorization policy; it consumes a signed/validated
manifest produced by the host/server control plane.

### 9.2 Manifest

The physical manifest is revisioned and applied transactionally. Required
fields include:

~~~json
{
  "schema_version": 1,
  "physical_instance_id": "uuid",
  "owner_id": "required-owner",
  "trust_group_id": "uuid",
  "image_digest": "sha256:...",
  "supervisor_protocol": 1,
  "resource_policy_id": "policy-id",
  "exports": [
    {
      "export_id": "uuid",
      "relay_id": "globally-unique-service-id",
      "root_handle": "opaque",
      "mode": "rw",
      "home_id": "stable-home-id",
      "capability_revision": 3,
      "worker_uid": 12001
    }
  ]
}
~~~

Tokens and private credentials are not stored in this manifest. They are
delivered through per-worker files with mode 0600 and removed when the worker
exits.

Unknown fields or unsupported schema/protocol versions fail closed.

### 9.3 Worker lifecycle

Worker states are:

~~~text
configured -> mounting -> starting -> connected -> draining -> stopped
                    -> failed
connected -> disconnected -> reconnecting
~~~

A worker crash restarts only that worker after bounded backoff. Its Desktop
process group dies with its PID namespace/cgroup and is reported stopped; a
blank Desktop is **not** automatically restarted.

A physical container crash necessarily kills all its processes. The host
manager may restart the supervisor and logical workers, but it must report all
previous Desktop sessions as terminated and must not recreate them
automatically.

### 9.4 Aggregate and per-export limits

The physical instance has an aggregate CPU, memory, PID, and I/O limit.
Each worker has a child cgroup with an optional per-export limit, and each
Desktop has a child cgroup under its worker.

Metrics and UI must show both aggregate and per-export usage. A noisy export
must not consume every PID or all memory allowed to the shared container.

### 9.5 Compatibility key

Exports may share a physical instance only when these values match:

- host/server owner and trust group;
- server profile / network destination;
- image digest and architecture;
- supervisor protocol;
- security profile;
- required device set;
- resource policy compatibility;
- filesystem broker type;
- encryption/mount policy.

A different Docker image, GPU/device grant, or security profile means a
different physical instance. The UI explains the incompatible field instead of
silently creating surprising groups.

## 10. Persistent HOME and caches

Every export keeps a stable HOME identity derived from its existing relay ID,
not from a transient worker slot.

The worker namespace mounts:

~~~text
/home/pawflow                       per-export persistent HOME
/home/pawflow/.config/chromium      per-export persistent profile
/run/user/<uid>                     per-worker/session runtime
/tmp                                per-worker tmpfs
/var/cache/pawflow-shared           optional non-sensitive shared cache
~~~

Rules:

1. Chromium profile directories are never shared by two exports.
2. Existing named relay HOME volumes are reused or migrated without deleting
   profile data.
3. Cache cleanup may remove only documented cache paths. It must not remove
   Chromium profile state, browser cookies, extensions, logins, or user files.
4. Shared caches may contain only content-addressed or lock-safe artifacts.
5. Authentication/config directories with mutable state remain per export.
6. Image replacement and supervisor recreation do not delete HOME.
7. Removing an export offers a separate, explicit "delete persistent data"
   action; stopping it never deletes data.

## 11. Desktop session architecture

### 11.1 Cardinality and ownership

Version 1 supports:

- zero or one Docker virtual Desktop per logical export;
- zero or one real host Desktop per physical host;
- many viewer attachments per Desktop session.

The real host Desktop is listed separately as "Host Desktop — shared machine
view" and has no workspace-isolation badge.

### 11.2 Session resources

A virtual Desktop session owns:

- `desktop_session_id`;
- export/relay/physical instance IDs;
- X display and Xauthority cookie;
- private X11 socket directory;
- D-Bus session socket;
- XDG runtime directory;
- PulseAudio/PipeWire runtime and null sink;
- VNC server secret;
- noVNC/websockify listener;
- audio stream listener;
- log directory;
- cgroup and process group;
- start time, initiator, health, and viewer count;
- the export's HOME and mount namespace.

No process mutates global `os.environ["DISPLAY"]`. The Desktop environment is
built for the Desktop process group only.

Xvfb must not use `-ac`. The supervisor creates an Xauthority cookie and
restricts the X11 socket. x11vnc/noVNC uses per-session authentication even
though the browser route also has a PawFlow capability token.

### 11.3 Resource allocation

Each export has a private network namespace. This permits session-local ports
and prevents a worker from connecting to another export's loopback Desktop
services.

The supervisor allocates and records the namespace-local VNC, noVNC, and audio
ports. No Desktop port is published on the Docker host. Browser traffic
continues through the authenticated PawFlow VNC/audio relay path.

If network namespaces cannot be established on a supported platform, grouped
mode is unavailable on that platform until an equally strong socket isolation
mechanism exists.

### 11.4 Start sequence

A Desktop start request is compare-and-create:

1. authorize `desktop.view` and `desktop.control` for the logical relay;
2. verify the worker is connected and permits remote Desktop;
3. return the existing healthy session if one already exists;
4. allocate a random session ID and session resources;
5. create private X11, D-Bus, XDG, audio, temp, and log paths;
6. start Xvfb with Xauthority;
7. start the D-Bus and audio session without killing any global daemon;
8. start XFCE;
9. start x11vnc and websockify with per-session credentials;
10. probe X11, VNC, noVNC HTTP, and audio readiness;
11. register the VNC/audio capability routes;
12. emit a `desktop_state_changed` event;
13. return an attachment URL/token.

All child processes start after entry into the export worker namespace. A
terminal opened inside XFCE therefore sees that export as `/workspace`.

### 11.5 Health and failure

The watchdog remains, but becomes session scoped. It checks Xvfb, x11vnc,
websockify, and the noVNC HTTP probe. An unhealthy session is cleaned up and
reported failed/stopped. This is failure handling, not automatic idle policy.

A failed Desktop is not restarted without a new explicit start action.

### 11.6 Manual stop sequence

A stop request carries both `relay_id` and the observed
`desktop_session_id`. The server rejects a stale session ID with a conflict so
a confirmation for an old Desktop cannot stop a newly started one.

After authorization and confirmation:

1. mark the session `stopping`;
2. revoke new viewer capability issuance;
3. notify connected viewers;
4. send TERM to the Desktop cgroup/process group;
5. wait a bounded grace period;
6. send KILL to remaining Desktop processes;
7. close VNC/audio backend sockets;
8. revoke route tokens;
9. remove X11/D-Bus/XDG/temp runtime files;
10. retain HOME and Chromium profile;
11. mark the session stopped and emit inventory change.

The confirmation warns that GUI applications, terminal processes launched from
the Desktop, and unsaved work may be lost.

### 11.7 Events that must not stop a Desktop

The implementation must never stop a healthy Desktop merely because:

- a noVNC tab closes;
- viewer count reaches zero;
- the browser or SSE connection disconnects;
- a conversation becomes inactive;
- the agent turn ends;
- an idle threshold is reached;
- a scheduled cleanup runs;
- the Webchat is reloaded;
- Relay Desktop's main window is hidden to tray.

## 12. Desktop inventory and server control plane

### 12.1 Canonical inventory

The server keeps a short-lived, reconciled Desktop inventory keyed by logical
relay ID and session ID. It is populated by:

- worker `desktop_state_changed` events;
- connect/reconnect registration state;
- explicit status probes;
- stop/start responses.

A list request does not trust open Webchat tabs. It returns only relays visible
and controllable by the requesting principal.

Each item contains:

~~~json
{
  "desktop_session_id": "uuid",
  "relay_id": "relay-service-id",
  "export_id": "uuid",
  "physical_instance_id": "uuid",
  "label": "project-a",
  "mode": "docker",
  "state": "running",
  "started_at": "ISO-8601",
  "started_by": "display-safe principal label",
  "last_heartbeat_at": "ISO-8601",
  "viewer_count": 1,
  "workspace_isolated": true,
  "can_stop": true
}
~~~

Canonical host paths, tokens, ports, and private usernames are not returned.

### 12.2 State model

~~~text
stopped -> starting -> running -> stopping -> stopped
             |           |
             v           v
           failed      failed

connected worker lost -> unknown/disconnected
reconciled absent      -> stopped
~~~

`unknown` is not treated as stopped and must not disappear immediately from
the UI. The row shows that confirmation cannot currently reach the relay.

### 12.3 Typed actions

Add typed, authorization-checked actions:

- `desktop_list_active`;
- `desktop_open`;
- `desktop_attach`;
- `desktop_stop_request`;
- `desktop_stop_confirm`;
- `physical_relay_list`;
- `physical_relay_stop_request`;
- `physical_relay_stop_confirm`.

Relay worker actions remain export scoped:

- `desktop_status`;
- `start_desktop`;
- `stop_desktop`;
- `desktop_ws_open/send/close`;
- audio open/close.

Responses gain session IDs, timestamps, mode, and supervisor/export identity.
The server validates that returned identities match the addressed connection.

### 12.4 Authorization and audit

Listing requires visibility of the logical relay.
Opening/attaching requires `desktop.view`.
Stopping requires `desktop.control`.
Stopping a physical instance requires ownership/operator authority over every
contained export.

Every start, stop request, confirmation, failure cleanup, physical stop, and
forced regroup operation writes an audit event with:

- actor;
- logical relay/export and physical instance IDs;
- session ID;
- reason;
- requested and final state;
- timestamp;
- source client;
- outcome.

The audit record never contains credentials or canonical host paths.

## 13. PawFlow Webchat UX

### 13.1 Dock button

Add a dedicated compact "Active Desktops" button to the composer action dock,
not only to the Resources panel.

Behavior:

- monitor icon;
- numeric badge for active/starting/stopping sessions visible to the user;
- hidden badge when the count is zero, while the button remains discoverable;
- accessible label and keyboard focus;
- responsive popover on desktop and bottom-sheet presentation on mobile.

The popover lists one row per backend Desktop:

- workspace/relay label;
- Docker-isolated or shared-host badge;
- state;
- start time;
- physical instance label when useful;
- Open/Reattach action;
- Stop action when authorized.

There is no bulk "Stop all" button in the first release.

### 13.2 Stop confirmation

Selecting Stop opens a confirmation dialog that names the exact relay and
session and states:

- the backend Desktop will stop;
- all GUI applications and Desktop-launched jobs will be terminated;
- unsaved work may be lost;
- closing only the tab is available as the safe detach action;
- persistent HOME/Chromium profile will be kept.

The dialog sends the observed session ID. The UI removes the row only after a
successful backend acknowledgement or reconciled stop event.

### 13.3 Tabs and viewer semantics

Existing Desktop tabs remain viewer attachments:

- tab close calls `closeDesktopTab` only;
- tab close unregisters the browser attachment and audio stream;
- it does not call `stop_desktop`;
- reattach creates/replaces the iframe with a current capability URL;
- a backend stop closes every local tab for that session and shows a reason.

The UI must not infer that an open tab means a running Desktop, or that no tab
means a stopped Desktop.

### 13.4 Live updates

Initial page/bootstrap state includes the visible Desktop inventory. SSE emits a
`desktop_inventory_changed` event on state changes. Reconnect performs a full
reconciliation to avoid missed events.

The button also offers a manual refresh when any item is unknown/disconnected.
It does not poll continuously at a high rate.

### 13.5 Expected Webchat files

Implementation is expected to touch, or introduce narrowly scoped modules
beside:

- `tasks/io/chat_ui/templates/header/action_dock.html`;
- `tasks/io/chat_ui/terminal_commands.js`;
- `tasks/io/chat_ui/tabs.js`;
- the SSE handler/state modules;
- action-dock/component CSS;
- i18n dictionaries;
- chat UI server/action registration;
- focused Webchat UI tests.

The Desktop inventory logic should live in a dedicated small module rather than
making the already large command/state files own another subsystem.

## 14. Relay Desktop UX

### 14.1 Physical instances and exports

Relay Desktop changes from a flat map of one launcher process per workspace to
a two-level view:

~~~text
Physical instance "Development"
├── project-a (connected)
├── project-b (connected, Desktop running)
└── project-c (stopped)
~~~

The user can:

- create/rename a physical instance;
- assign compatible exports to it;
- see why an export is incompatible;
- start/stop one logical export;
- start/stop the physical instance;
- inspect aggregate and per-export status;
- move a stopped export between compatible instances;
- see active Desktops.

Changing group membership while an export has active processes uses the same
drain/confirmation workflow as a root change.

### 14.2 Active Desktop controls

Each export row shows a Desktop badge. A physical-instance detail pane and tray
submenu list active Desktops with per-session Open and Stop actions.

Stopping an export or physical instance with an active Desktop presents the
same explicit warning as Webchat. Hiding the app to tray never stops relays or
Desktops.

### 14.3 Application quit and OS shutdown

The current Electron application stops all relay launchers on Quit. After this
feature:

- normal Quit first lists active Desktops and asks for explicit confirmation;
- cancellation leaves the app and physical instances running;
- confirmed Quit stops Desktops/exports and then the supervisor;
- forced OS shutdown performs best-effort cleanup and writes a recovery event,
  but cannot promise interaction;
- next launch reconciles actual Docker/worker state before showing status.

### 14.4 IPC and process model

Replace `runningRelays: Map<workspaceName, process>` with physical-instance
records and export status.

Expected IPC additions include:

- list/create/update/delete physical instance;
- assign/unassign export;
- start/stop physical instance;
- start/stop export;
- list/stop Desktop session;
- subscribe to supervisor state;
- migration preflight/apply.

`preload.js` exposes only typed IPC methods. The renderer never receives
tokens, root broker credentials, or raw Docker command access.

### 14.5 Standalone CLI ownership

The standalone `pawflow-relay` manager gains equivalent commands so the
Electron UI remains a client of the same model:

~~~text
pawflow-relay instance list
pawflow-relay instance create <name> ...
pawflow-relay instance start <name>
pawflow-relay instance stop <name>
pawflow-relay export list
pawflow-relay export assign <export> --instance <instance>
pawflow-relay export start|stop <export>
pawflow-relay desktop list
pawflow-relay desktop stop <export> --session <id>
~~~

CLI output supports structured JSON for Electron. Required identifiers never
fall back to a default instance/export.

## 15. Slash commands for PawCode, VS Code, and other clients

The canonical conversation commands are:

~~~text
/desktop [relay]
/desktop docker [relay]
/desktop local [relay]
/desktop list [relay]
/desktop status [relay]
/desktop attach <relay>
/desktop close [relay]
/desktop stop <relay>
~~~

Semantics:

- `/desktop [relay]` and `docker` open or attach to the export-scoped virtual
  Desktop;
- `local` explicitly opens the shared host Desktop and displays a warning;
- `list` shows active Desktops visible to the caller;
- `status` reports one relay's backend state;
- `attach` returns/opens a current viewer URL without changing lifecycle;
- `close` detaches the client viewer only;
- `stop` initiates confirmation for the backend session.

Interactive clients present a yes/no confirmation. A non-interactive API must
submit a second confirmed action containing the exact session ID. There is no
ambiguous "stop whatever is current" fallback.

PawCode may open the URL in the system browser. VS Code may use an authenticated
webview. Text-only clients print a short-lived attachment link. All lifecycle
operations use the same server actions and authorization checks as Webchat.

Update `docs/SLASH_COMMANDS.md`, PawCode help/completion, VS Code command
registration, and Webchat command help together.

## 16. Real host Desktop rules

The host Desktop is one machine-wide resource, not one resource per export.
It is fundamentally unable to give two simultaneous meanings to
`/workspace` in arbitrary applications running in the user's real OS session.

Therefore:

1. UI labels it "Host Desktop (shared machine view)".
2. It never displays the "workspace isolated" badge.
3. It is listed once per physical host, with the exports/clients currently
   authorized to attach.
4. Starting it through one relay does not claim that applications are confined
   to that relay root.
5. A user who needs `/workspace` isolation selects Docker virtual Desktop.
6. Grouped-mode acceptance tests do not count host Desktop as satisfying the
   workspace-view requirement.
7. Host Desktop stop remains explicit and manual.

## 17. Other long-lived worker resources

Desktop isolation cannot ship on top of an otherwise shared process context.
The same worker namespace and lifecycle must contain:

- shell commands and PTYs;
- interactive terminals;
- code-server;
- screen/CUA backend;
- browser processes;
- user-started background processes;
- per-export FUSE mounts;
- service tunnel endpoints whose capability belongs to the export;
- MCP subprocesses started by that relay;
- temp files and logs.

Required changes include:

- remove process-global `DISPLAY` mutation;
- make code-server port/config/runtime paths export scoped;
- pass worker/session environment explicitly to `fs_screen.py`;
- ensure screen actions target the addressed export's display;
- make process registries worker scoped;
- ensure stop/cleanup never uses broad command-line matching;
- keep service-tunnel authorization attached to the logical export even if
  network plumbing is hosted by the physical supervisor.

## 18. Data model changes

### 18.1 Standalone/Relay Desktop local state

Replace the flat runtime assumption with explicit records.

`PhysicalInstance`:

~~~json
{
  "id": "uuid",
  "name": "Development",
  "server": "prod",
  "owner_id": "local-user-id",
  "trust_group_id": "uuid",
  "docker_image": "image@sha256:digest",
  "security_profile": "multi-export-v1",
  "resource_policy": {
    "memory_bytes": 8589934592,
    "cpu_count": 4,
    "pids": 2048
  },
  "created_at": "ISO-8601",
  "updated_at": "ISO-8601"
}
~~~

`LogicalExport`:

~~~json
{
  "export_id": "uuid",
  "name": "project-a",
  "physical_instance_id": "uuid",
  "server": "prod",
  "path": "host-only canonical path",
  "mode": "rw",
  "relay_id": "stable-relay-id",
  "home_id": "stable-relay-id",
  "allow_exec": true,
  "allow_remote_desktop": true,
  "allow_local": false,
  "allow_service_tunnels": false,
  "created_at": "ISO-8601",
  "updated_at": "ISO-8601"
}
~~~

Canonical paths stay in the host manager store. Container manifests receive
opaque root handles.

### 18.2 Server-side service metadata

A logical RelayService stores or resolves:

- `physical_instance_id`;
- `export_id`;
- `trust_group_id`;
- `supervisor_protocol`;
- `image_digest`;
- `server_managed`;
- capability revision;
- immutable owner;
- managed HOME identity.

Mutable request identity must not be stored on the shared service object.
Operation-time principal context remains request scoped as required by the
remote enrollment/sharing plan.

### 18.3 Runtime state

Runtime state is ephemeral and separate from configuration:

- supervisor/container PID and Docker ID;
- manifest revision;
- worker PIDs/states;
- mount states;
- connection states;
- Desktop states;
- cgroup usage;
- health timestamps and errors.

A restart reconciles this state from the supervisor and live relay
connections; it is never reconstructed from browser tabs.

## 19. Server-managed lifecycle

`ServerRelayManager` evolves from "one managed relay equals one container" to
two layers:

1. logical relay CRUD and service/token/binding ownership;
2. physical instance placement and container supervision.

Required operations:

- select/create compatible physical instance;
- add/remove logical export from desired manifest;
- ensure/restart one worker;
- ensure/restart the physical container;
- list physical instances and contained exports;
- block unsafe physical stop when active Desktops exist;
- preserve service definitions, bindings, workspace data, and HOME;
- garbage-collect an empty physical instance only after explicit policy allows
  it.

A disconnected logical relay does not prove the physical container is dead.
Health checks inspect the specific worker and then the supervisor. Replacement
logic must not kill a healthy physical instance merely because one export is
reconnecting.

Container names and labels include the physical instance ID and server ID.
Worker/export identity appears in supervisor state, not as separate Docker
containers.

## 20. Remote Relay Desktop lifecycle

The manager starts one supervisor process/container per physical instance, then
applies export manifests.

Starting one export:

1. ensures the physical instance is running;
2. creates/reuses its broker handle;
3. applies a new manifest revision;
4. waits for that worker to connect;
5. reports logical status.

Stopping one export:

1. checks active Desktop/terminal/code-server state;
2. asks for confirmation when destructive;
3. drains and stops that worker;
4. uninstalls/disconnects only that logical relay as appropriate;
5. unmounts/revokes only its export handle;
6. leaves the physical container and other workers running.

Stopping the physical instance is an explicit aggregate operation. It cannot be
used as an accidental implementation shortcut for stopping one export.

## 21. Failure and recovery matrix

| Failure/event | Required behavior |
|---|---|
| One worker crashes | Restart only that worker with bounded backoff; mark its Desktop terminated; other exports continue. |
| Desktop Xvfb/VNC fails | Clean only that Desktop session; preserve worker, HOME, and other exports; no auto-restart. |
| Physical container crashes | Mark every worker disconnected and Desktop terminated; restart workers if policy allows; never auto-recreate Desktops. |
| Host export broker disconnects | Freeze/fail affected I/O, mark mounts unhealthy, prevent new jobs, and reconcile; never redirect to another root. |
| One FUSE mount fails | Stop/drain only its worker; do not unmount other exports. |
| Relay WebSocket disconnects | Worker reconnects; a still-running Desktop remains running and is reconciled after connection returns. |
| Webchat/SSE disconnects | No lifecycle change; full inventory reconciliation on reconnect. |
| Browser tab closes | Detach viewer only. |
| Stop acknowledgement is lost | Reconcile by exact session ID; retries are idempotent. |
| Stale stop confirmation | Return conflict; do not stop a newer session. |
| Export added | Attach mount and start worker without recreating physical container. |
| Export root/group changed | Drain and require confirmation for active resources; never live-remap. |
| Physical upgrade requested | List active resources, confirm, stop, preserve HOME, replace container, restart workers only. |
| Relay Desktop hidden to tray | No lifecycle change. |
| Normal Relay Desktop Quit | Require confirmation when active Desktops exist. |
| Forced OS shutdown | Best-effort cleanup and recovery audit; reconcile on next start. |

## 22. Observability

### 22.1 Metrics

Expose bounded-cardinality metrics:

- physical instances configured/running;
- workers configured/connected/restarting/failed;
- active virtual and host Desktops;
- worker/desktop memory, CPU, PID, and I/O usage;
- mount health and latency;
- supervisor manifest revision/apply failures;
- Desktop start/stop/readiness duration;
- unexpected Desktop termination;
- denied cross-export/control requests.

Use IDs internally but avoid unbounded path/user labels in exported metrics.

### 22.2 Logs

Use structured log context:

- `physical_instance_id`;
- `export_id`;
- `relay_id`;
- `desktop_session_id` when applicable;
- operation and state transition.

Logs are stored per export/session under supervisor-controlled directories, not
fixed `/tmp/desktop.log` names. Credentials and host canonical paths are
redacted.

### 22.3 Operator status

Admin/server status reports:

- one row per physical instance;
- image digest and protocol;
- owner/trust group;
- worker counts/states;
- active Desktop count;
- aggregate resource usage;
- last error;
- upgrade/drain state.

Normal users see only their visible logical exports and safe physical grouping
labels.

## 23. Migration and one-shot cutover

### 23.1 Preflight

Before migration:

1. inventory every standalone and server-managed relay;
2. resolve image digests and compatibility keys;
3. verify `/dev/fuse`, namespace, cgroup, AppArmor/SELinux, and virtual network
   support;
4. inventory running processes and active Desktops;
5. inventory named HOME volumes and Chromium profile paths;
6. validate relay IDs, owners, tokens, and scopes;
7. write a versioned backup of local/server relay configuration;
8. refuse migration while active Desktop/jobs exist unless the user confirms
   planned downtime.

### 23.2 Configuration conversion

Each current `WorkspaceShare` becomes one `LogicalExport` with the same:

- name;
- canonical root;
- mode;
- relay ID;
- server association;
- capabilities;
- persistent HOME identity.

Physical instance grouping is explicit. The migration UI proposes compatible
groups (same owner/server/image/security policy), shows the reduced container
count, and requires confirmation. Unreviewed exports remain one-per-instance;
there is no silent security-boundary reduction.

Server-managed migration follows the same owner/trust rule. It never groups
different users.

### 23.3 HOME/profile preservation

The migration maps the existing per-relay HOME volume to the export's stable
`home_id`. Prefer reusing the volume directly. If a copy is required:

1. stop the old worker;
2. copy metadata-preservingly;
3. verify ownership and a Chromium profile sentinel;
4. retain the old volume as a rollback backup;
5. start the new worker;
6. verify profile visibility;
7. delete the old volume only through a later explicit cleanup.

Cache cleanup is not part of migration.

### 23.4 Protocol cutover

Server, relay runtime, Relay Desktop, and clients expose a minimum compatible
supervisor/desktop protocol. Upgrade preflight lists incompatible remote
clients.

The release performs one cutover. Unsupported old runtimes receive a clear
upgrade-required error. There is no indefinite compatibility adapter that could
bypass export identity or session-ID checks.

### 23.5 Rollback

Rollback restores the backed-up configuration and reattaches preserved HOME
volumes to one-container-per-export runtimes. It cannot restore processes that
were stopped for migration, and the UI states that before confirmation.

No migration step deletes workspace content or browser profiles.

## 24. Implementation work packages

Each work package lands as a dedicated implementation/test/documentation commit
or a small coherent series. Release metadata remains separate under the release
procedure.

### WP0. Contract and threat-model tests

- freeze terminology, required invariants, state machines, and protocol schemas;
- add failing tests for one-container/two-export root correctness;
- add negative tests for cross-export path, PID, X11, D-Bus, network, token, and
  control-socket access;
- add tests proving no idle/viewer-driven Desktop stop;
- document supported platform prerequisites.

Exit gate: tests reproduce the current one-container-per-export limitation and
the unsafe shared-global Desktop assumptions.

### WP1. Configuration and stores

- add physical instance and logical export models;
- add strict validation and compatibility-key calculation;
- migrate standalone manager storage;
- add server-managed physical instance records;
- keep immutable owner and stable HOME IDs;
- add migration preflight, backup, and dry-run output;
- cover malformed, missing, duplicate, and incompatible records.

Exit gate: configuration can represent ten exports in one explicit instance
without starting Docker.

### WP2. Export broker and namespace launcher

- extend host/server filesystem transport with opaque export handles;
- bind handles to instance/export/mode/revision;
- implement private dynamic FUSE mounts;
- implement mount/PID/IPC/user/network namespace creation;
- implement private tmp/runtime/HOME mounts;
- drop worker capabilities and enforce cgroups/seccomp/AppArmor;
- remove unrestricted sudo from the grouped image;
- add cleanup and crash-recovery handling.

Exit gate: two test workers concurrently see different `/workspace` roots and
cannot observe each other.

### WP3. Physical supervisor and worker protocol

- add versioned supervisor manifest/control protocol;
- start/stop/restart/drain one worker;
- deliver per-worker secrets via files;
- report worker/mount health and resource usage;
- apply manifest revisions transactionally;
- implement idempotent recovery after supervisor restart;
- add bounded restart/backoff and state events.

Exit gate: adding/removing one stopped export does not recreate the physical
container or interrupt another worker.

### WP4. Standalone/remote runtime integration

- replace one `RelayThread`/container per WorkspaceShare with one supervisor
  runtime per physical instance;
- keep one logical relay connection per export;
- update cleanup to target export or instance precisely;
- update CLI manager commands and JSON output;
- handle Docker Desktop/WSL path and helper transport;
- preserve per-export HOME volumes.

Exit gate: Relay Desktop starts ten compatible exports and Docker reports one
PawFlow supervisor container.

### WP5. Server-managed placement integration

- split `ServerRelayManager` logical and physical lifecycle;
- update managed container labels, health, ensure, reconnect, and orphan
  recovery;
- prevent one disconnected export from replacing a healthy physical instance;
- enforce owner/trust grouping;
- expose admin physical-instance status and guarded stop/recreate;
- preserve logical services, tokens, bindings, roots, and HOME.

Exit gate: two server-managed logical relays share one physical container while
remaining independently routable and restartable.

### WP6. Worker-scoped long-lived resources

- move terminal, PTY, code-server, screen/CUA, browser, process registry, and
  service-tunnel state into the export worker context;
- remove global `DISPLAY`, temp, and fixed-port assumptions;
- ensure every descendant inherits the export namespace;
- add per-export cleanup and collision tests.

Exit gate: simultaneous terminals/code-server sessions operate on correct roots
and stopping one export leaves the other intact.

### WP7. Multi-session Desktop runtime

- replace singleton Desktop paths/environment with a session object;
- implement Xauthority, private X11/D-Bus/XDG/audio/log resources;
- avoid global PulseAudio kill;
- use isolated VNC/noVNC/audio sockets and route capabilities;
- add session ID compare-and-stop;
- preserve health watchdog without auto-restart or idle stop;
- persist HOME/Chromium profile per export.

Exit gate: two concurrent virtual Desktops show their own `/workspace`, audio
and clipboard work independently, and neither can capture/control the other.

### WP8. Server Desktop inventory, authorization, and audit

- add canonical inventory and reconciliation;
- add typed list/open/attach/stop request/confirm actions;
- enforce visibility, `desktop.view`, and `desktop.control`;
- add SSE inventory events;
- add stale-confirmation/idempotency behavior;
- add audit records and tests;
- distinguish virtual/export-isolated and host/shared Desktop modes.

Exit gate: authorized clients see and stop only exact visible sessions.

### WP9. Webchat dock

- add Active Desktops button and count badge;
- add responsive list, reattach, manual refresh, and per-row Stop;
- add explicit confirmation dialog;
- retain tab detach semantics;
- handle unknown/disconnected/stale states;
- add i18n, accessibility, mobile, and UI tests;
- keep logic in a focused Desktop inventory module.

Exit gate: closing a tab leaves the backend running; the dock still lists it;
confirmed Stop removes it only after backend acknowledgement.

### WP10. Slash command clients

- implement canonical `/desktop list/status/attach/close/stop` semantics;
- add confirmation flow and exact session IDs;
- wire PawCode, VS Code, Webchat, and generic API clients to the same typed
  actions;
- update help/completion and `docs/SLASH_COMMANDS.md`;
- test authorization and non-interactive confirmation.

Exit gate: a client without Webchat UI can list, attach to, and deliberately
stop a Desktop without ambiguous targeting.

### WP11. Relay Desktop UI and tray

- render physical instances with child exports;
- add grouping compatibility explanations;
- add active Desktop badges/list/stop;
- guard export/instance stop, regroup, upgrade, and Quit;
- replace flat `runningRelays` process ownership;
- add typed IPC and state subscriptions;
- implement migration review/dry-run/apply;
- add Electron unit/UI tests.

Exit gate: remote Relay Desktop manages one physical container with several
logical relays and never stops an active Desktop without confirmation.

### WP12. Security, platform, soak, and documentation

- run adversarial namespace/X11/VNC/token/process tests;
- validate Linux native Docker, Windows Docker Desktop/WSL, and macOS Docker
  Desktop;
- run ten-export and multi-Desktop memory/CPU/PID soak tests;
- test server/worker/container/network failures;
- validate AppArmor/SELinux behavior and fail-closed fallback;
- update all relay, Desktop, filesystem, slash-command, admin, and operational
  docs;
- add upgrade and rollback runbooks.

Exit gate: every release acceptance criterion below passes on the supported
matrix.

## 25. Dependency order

~~~text
WP0 -> WP1 -> WP2 -> WP3
                    ├── WP4 (remote)
                    └── WP5 (server-managed)
WP3 + WP4/WP5 -> WP6 -> WP7 -> WP8
WP8 -> WP9 + WP10
WP4 + WP7 + WP8 -> WP11
all -> WP12
~~~

WP2 is the security boundary and must not be bypassed to accelerate UI work.
WP9/WP11 may use mocked typed APIs while backend work proceeds, but grouped
runtime cannot ship until the isolation tests are green.

The identity/ACL work from
`REMOTE_RELAY_ENROLLMENT_SHARING_PLAN.md` is a prerequisite before any
cross-principal sharing claim. This feature may initially ship only for
single-owner trust groups.

## 26. File-level implementation map

Likely existing runtime files:

- `pawflow_relay/manager.py`: physical/export configuration and migration;
- `pawflow_relay/thread.py`, `_thread_docker.py`, `_thread_base.py`:
  replace one-container lifecycle with supervisor client lifecycle;
- `pawflow_relay/_relay_state.py`, `_relay_dispatch.py`: worker/session
  identity and Desktop action responses;
- `pawflow_relay/_relay_desktop.py`: session-scoped Desktop runtime;
- `pawflow_relay/host_bridge.py`, `server_fs_client.py`,
  `server_fs_mount.py`, `remote_mounts.py`: export-scoped broker/mounts;
- `tools/_fs_paths.py`, `fs_exec.py`, `fs_screen.py`: verify namespace and
  explicit environment behavior;
- `core/server_relay_manager.py`, `core/_server_relay_container.py`:
  physical placement and managed supervisor lifecycle;
- relay connection/service modules: physical/export registration metadata and
  immutable request identity;
- `services/vnc_proxy.py`, `services/audio_proxy.py`: exact session routing
  and inventory reconciliation;
- `tasks/ai/actions/_sf_k7.py` or a focused adjacent Desktop action module:
  typed open/list/attach/stop actions;
- `pawflow-relay-desktop/src/main.js`, `preload.js`, `renderer.js`, and
  templates/styles: physical/export/Desktop UI and IPC;
- `tasks/io/chat_ui/templates/header/action_dock.html`, focused Desktop UI
  module, SSE state/handlers, `terminal_commands.js`, and `tabs.js`;
- PawCode/VS Code command handlers and help;
- relay image Dockerfile, entrypoint, AppArmor/SELinux/seccomp assets.

Expected new focused modules include:

- physical instance/export store;
- supervisor protocol client/server;
- namespace launcher;
- export broker registry;
- Desktop inventory service;
- Webchat Desktop inventory component.

Exact names should follow the local module split at implementation time; these
components must not be embedded as another large branch in an oversized file.

## 27. Test plan

### 27.1 Unit tests

Cover:

- strict physical/export schema validation;
- compatibility key and trust-group rejection;
- deterministic migration and backup;
- stable relay/HOME IDs;
- manifest revision/idempotency;
- worker and Desktop state machines;
- resource allocation/collision handling;
- session ID compare-and-stop;
- no-auto-stop policy;
- authorization filters;
- slash command parsing and confirmation;
- Webchat/Relay Desktop state reducers;
- audit redaction.

### 27.2 Linux container integration

Start one physical instance with exports A and B.

Verify concurrently:

- `pwd` and literal `/workspace` map correctly;
- symlink, `.. `, bind mount, proc-fd, and race attempts cannot cross roots;
- worker A cannot see B staging paths, PIDs, sockets, secrets, HOME, or logs;
- read-only mode rejects every write path;
- worker restart leaves B alive;
- export add/remove does not recreate the container;
- aggregate and per-worker limits apply;
- ten exports still produce one physical container.

### 27.3 Desktop integration

Start Desktop A and Desktop B concurrently.

Verify:

- distinct session IDs, X displays, namespaces, Xauthority, D-Bus, audio, logs,
  and runtime paths;
- an XFCE terminal in A sees A at `/workspace`;
- an XFCE terminal in B sees B at `/workspace`;
- Chromium profiles remain distinct and survive recreation;
- A cannot screenshot, inject input into, connect to, or read B;
- screen tool routing selects the addressed export;
- stopping A leaves B and both workers healthy;
- tab/viewer detach leaves the Desktop running;
- zero-viewer/idle/SSE disconnect never stops it;
- Desktop crash is cleaned but not automatically restarted.

### 27.4 Webchat tests

Verify:

- initial and SSE inventory state;
- badge count and accessibility;
- backend inventory independent of tabs;
- reattach behavior;
- confirmation text and exact session ID;
- stale confirmation conflict;
- successful and failed stop UX;
- disconnected/unknown relay UX;
- no bulk stop;
- mobile bottom sheet;
- unauthorized sessions never render.

### 27.5 Relay Desktop/Electron tests

Verify:

- physical/export tree rendering;
- compatibility explanations;
- one supervisor process for several exports;
- per-export start/stop;
- active Desktop list and stop confirmation;
- physical stop/regroup/upgrade guards;
- hide-to-tray does nothing;
- Quit cancel/confirm behavior;
- JSON IPC does not expose secrets or canonical roots unnecessarily;
- recovery after Electron and supervisor restart.

### 27.6 Cross-platform matrix

Required:

- Linux host + Docker Engine;
- Windows + Docker Desktop/WSL helper path;
- macOS + Docker Desktop.

For each platform, validate dynamic broker mounts, namespace support inside the
Linux relay container, path semantics, HOME/profile persistence, and remote VNC
transport.

If a platform fails the required namespace checks, the product explicitly
shows "grouped runtime unavailable" and retains one container per export.

### 27.7 Security tests

Attempt:

- sudo/capability escalation;
- `setns`, ptrace, signals, `/proc` inspection;
- mount propagation and hidden staging traversal;
- control socket and manifest spoofing;
- export handle reuse/replay;
- token theft through argv/env/logs;
- VNC/noVNC/audio cross-session connection;
- X11 capture/injection;
- D-Bus and PulseAudio cross-session access;
- cgroup escape/resource starvation;
- stale stop token replay;
- forged physical/export IDs;
- cross-user grouping through API/config manipulation.

Every failure is fail-closed and attributable in audit logs.

### 27.8 Soak and performance

Run:

- ten connected idle exports for 24 hours;
- ten exports with representative shell/tool traffic;
- two, five, and ten concurrent Desktops;
- repeated worker crash/restart;
- repeated add/remove export;
- server and network interruptions;
- image replacement with HOME reuse.

Record container count, RSS/PSS, CPU, PID count, FUSE latency, reconnect time,
Desktop readiness, and resource recovery. Compare against one-container-per-
export baseline.

## 28. Rollout gates

### R0: models only

Configuration/migration code and tests land with no grouped runtime enabled.

### R1: developer-only single-owner grouped runtime

Enable only through explicit developer configuration on Linux after WP2/WP3
security tests pass.

### R2: server-managed and remote parity

Both placement paths pass the same logical export and Desktop contract. No UI
calls one path "complete" while the other silently creates one container per
export.

### R3: user UI and migration preview

Webchat and Relay Desktop expose inventory, grouping, warnings, and dry-run.
No automatic Desktop stop exists.

### R4: supported-platform release

Linux, Windows/WSL, and macOS matrix passes or unsupported platforms explicitly
retain isolated containers with a visible reason.

### R5: default availability

Grouped instances may become the recommended single-owner mode only after soak,
security review, upgrade/rollback rehearsal, and documentation are complete.

## 29. Acceptance criteria

The feature is complete only when all of the following are true:

1. Ten compatible configured directories can be active through ten distinct
   logical relay IDs while Docker shows one physical PawFlow supervisor
   container.
2. Every logical relay retains its own service, token, WebSocket, binding,
   authorization, root, capabilities, HOME, and status.
3. Literal `/workspace` is correct in tools, shells, terminals, code-server,
   Desktop applications, Chromium, screen automation, and descendants.
4. At least two virtual Desktops can run concurrently in one physical container
   and each shows only its export as `/workspace`.
5. X11, D-Bus, audio, VNC/noVNC, network, HOME, temp, logs, PIDs, and credentials
   are isolated per export/session.
6. A worker cannot observe, signal, mount, connect to, or control another
   worker/Desktop in the adversarial test suite.
7. Stopping/restarting one export or Desktop leaves every other export/Desktop
   running.
8. Adding/removing a stopped export does not recreate the physical container.
9. Closing a Desktop tab/viewer never stops the backend.
10. Idle time, zero viewers, browser disconnect, conversation inactivity, and
    scheduled cleanup never stop a healthy Desktop.
11. Webchat has a dock button that lists backend-active Desktops and supports
    explicit per-session stop confirmation.
12. Relay Desktop UI and tray list active Desktops and guard export, instance,
    regroup, upgrade, and Quit operations.
13. PawCode, VS Code, and other clients can list/status/attach/close/stop through
    documented slash commands and exact-session confirmation.
14. The real host Desktop is clearly labelled shared/not workspace-isolated.
15. Existing Chromium profiles and HOME data survive migration, image update,
    container recreation, and cache cleanup.
16. Cross-user or incompatible-policy grouping is rejected.
17. Unsupported namespace/security capability fails closed to one container per
    export with a visible explanation.
18. Server-managed and remote Relay Desktop implementations satisfy the same
    contract.
19. Unit, integration, UI, cross-platform, security, failure, migration, and
    soak tests are green.
20. All affected documentation is updated in the implementation commits.

## 30. Final product behavior

For the user's original ten-directory case, the visible result is:

~~~text
Relay Desktop / PawFlow
└── Physical instance: Development (1 Docker container)
    ├── relay-project-a -> host project A -> /workspace
    ├── relay-project-b -> host project B -> /workspace
    ├── relay-project-c -> host project C -> /workspace
    └── ... ten independent logical relays
~~~

Opening Desktop on project A starts one private X/desktop stack inside worker A.
Opening Desktop on project B starts another inside worker B. Both share the
physical image/container but not their workspace view, HOME, Chromium profile,
runtime sockets, or processes.

The Webchat dock and Relay Desktop UI show both active sessions. Closing either
viewer leaves its Desktop and user-launched jobs running. Stopping a Desktop is
always a named, confirmed action against the exact current session.

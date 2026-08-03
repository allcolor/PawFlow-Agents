# Remote CLI Compute Pool — Complete Implementation Plan

Status: **proposed, implementation-ready architecture plan**
Priority: **P0 / must-have #1**
Scope: remote Docker execution for Claude Code, Codex, and Antigravity/Gemini CLI providers
Primary user outcome: move CLI container load away from the PawFlow server and scale it across one or more VPS compute relays without changing any other PawFlow behavior.

This plan is standalone from remote conversation/resource storage, not
dependency-free. It extracts the CLI execution subset from
`REMOTE_RELAY_STORAGE_AND_CLI_EXECUTION_PLAN.md`, supersedes the CLI-pool and
interactive-provider portions of `AWS_REMOTE_EXEC_PLAN.md`, and names its relay
identity/FUSE prerequisites explicitly in sections 27 and 30.

## 1. Decision summary

PawFlow must support two interchangeable Docker execution locations:

1. **server** — the current Docker daemon on the PawFlow server;
2. **relay compute endpoint** — a Docker daemon controlled by a registered PawFlow
   Relay on another machine.

The only intended behavioral difference is the machine on which the CLI Docker
container runs.

Everything else remains owned and orchestrated exactly as today:

- PawFlow remains the control plane;
- conversations remain in the current ConversationStore;
- agents, prompts, compaction, retries, budgets, queues, and SSE remain server-side;
- CLI tools continue to reach workspaces through PawFlow MCP tools;
- the conversation's linked/default workspace relays remain independent from the
  compute relay;
- existing provider-specific compatibility mounts remain provider-specific;
- credentials remain selected and leased by PawFlow;
- every provider request, tool call, hook event, and response remains visible in
  the webchat;
- force-stop remains immediate and does not poison the next loop;
- no placement failure silently falls back to another Docker host.

Remote compute is registered through the shared `pawflow-relay` manager as a
normal relay with additional compute capabilities. The headless CLI is the
primary VPS path and PawFlow Relay Desktop is its graphical frontend over the
same state and operations. A machine may expose:

- workspace endpoints only;
- compute endpoints only;
- both workspace and compute endpoints.

Compute and workspace placement are independent. A CLI on compute relay B may use
MCP tools against workspace relay A.

## 2. Goals

1. Run Claude Code, Codex, and Antigravity/Gemini CLI containers on one or more
   remote VPS hosts.
2. Preserve byte/event/lifecycle parity with server-local execution.
3. Allow exact endpoint selection and automatic scheduling across a compute pool.
4. Keep the existing local server Docker path as a first-class explicit target.
5. Configure, enroll, start, stop, drain, and diagnose compute relays from the
   headless CLI or PawFlow Relay Desktop without editing JSON or Docker commands.
6. Scale horizontally by adding registered compute relays without modifying the
   PawFlow server host.
7. Support batch and persistent interactive CLI runtimes.
8. Keep MCP PawFlow as the canonical workspace access path.
9. Preserve current compatibility mounts where they exist today.
10. Fail closed and reconcile unknown outcomes without duplicate containers or
    duplicate turns.

## 3. Non-goals

This feature does not:

- move ConversationStore, messages, Git history, memories, or agent contexts;
- move repository resources or make relays authoritative resource stores;
- require a workspace to live on the compute relay;
- copy or synchronize a workspace merely to execute a CLI;
- make direct container filesystem access the canonical tool path;
- expose a Docker socket or arbitrary Docker command API to the PawFlow server;
- create, provision, or destroy VPS instances;
- build arbitrary Docker images during runtime;
- implement silent server fallback;
- migrate a running interactive session between compute nodes;
- add relay sharing or cross-user ACL semantics outside the relay enrollment plan;
- retain direct provider-to-`docker_cmd()` code after migration.

### 3.1 Relationship to AWS_REMOTE_EXEC_PLAN.md

`AWS_REMOTE_EXEC_PLAN.md` remains relevant for AWS-managed ECS/EKS execution,
containerized ExecuteScript, server relays, VNC, and other non-CLI Docker
consumers. This plan is authoritative for CC/Codex/Gemini/AGY CLI pools,
interactive sessions, their live registries, terminal attachment, and remote
Docker compute relays.

The AWS plan's “one seam” premise is not true for the current tree:
`docker_cmd()` is wrapped/redefined in modules including
`core/_cci_pool_spawn.py` and `core/_antigravity_input.py`, while pools,
interactive helpers, registries, and terminal flows depend on container names
and Popen behavior above `core/docker_utils.py`. A low-level command-prefix
swap cannot provide typed leases, scheduling, runtime identity, reconciliation,
or secure multi-VPS control. The narrower `CliRuntimeBackend` in this plan is
therefore the chosen seam for CLI execution.

## 4. Current behavior that must be preserved

### 4.1 Workspace access

`core/cli_workspace_mounts.py` states the current contract:

- MCP relay tools are the canonical filesystem path;
- workspace mounts are compatibility fallbacks;
- `PAWFLOW_CLI_WORKSPACE_MOUNT` accepts `off`, `ro`, or `rw`;
- an unset/empty value defaults to `rw`, while an invalid value fails closed to
  `off`;
- the conversation's default relay is mounted at `/workspace`;
- other linked relays are mounted at `/relay/<relay-id>`;
- a mount is currently emitted only when the relay is connected and its
  `host_root` exists on the Docker host;
- a genuinely remote relay therefore has no direct bind in a server-local CLI
  container and remains accessible through MCP PawFlow.

Remote execution must preserve this provider-specific contract. It must not
silently add mounts to providers that do not have them today or remove mounts
from providers that do.

### 4.2 Provider matrix

| Provider path | Runtime shape | Workspace compatibility mount today | Other direct views |
|---|---|---|---|
| `claude-code` batch | throwaway pool container + `docker exec` | yes, when host-local | session tree, skills, runtime bridge |
| `codex-app-server` | reusable app-server container/process | yes, when host-local | session tree, skills, runtime bridge |
| Gemini CLI batch/app | throwaway or reusable pool container | yes, when host-local | session tree, skills, runtime bridge |
| Claude Code interactive | persistent container + tmux + MITM | no workspace mount | session tree, skills, hooks/proxy bundle |
| Codex interactive | persistent container + tmux + MITM | no workspace mount | session tree, skills, hooks/proxy bundle |
| Antigravity interactive | persistent container + tmux + observer proxy | no workspace mount | session tree, observer/proxy bundle |
| Codex image generation | isolated Codex pool job | no conversation workspace requirement | job session directory and attachments |

The implementation must maintain an executable provider contract test for this
matrix. Documentation prose is not sufficient evidence of parity.

### 4.3 Current local assumptions to remove

Current pool/provider/live-registry code assumes:

- a local container name is a globally usable handle;
- `subprocess.Popen` represents the CLI process;
- `docker run/exec/cp/inspect/rm` are available on the PawFlow server;
- server filesystem paths can be bind-mounted;
- `host.docker.internal` reaches PawFlow;
- bridge scripts can be copied from the server source tree;
- process groups and container PIDs can be inspected locally;
- tmux commands run through local `docker exec`;
- terminal viewers attach through a local subprocess;
- a server shutdown handler can directly delete every container.

All provider code must move behind a runtime interface. Only the local runtime
adapter may retain those local Docker mechanics.

## 5. Non-negotiable invariants

1. Changing `server` to a remote compute target changes only Docker placement.
2. MCP PawFlow remains the canonical workspace/tool path.
3. The compute endpoint and workspace endpoint are independent identities.
4. The selected compute target is pinned before runtime acquisition.
5. A target never changes during a turn or live interactive session.
6. An unavailable target never falls back to server Docker unless the user
   explicitly selected a policy containing the server target.
7. The server never sends raw Docker command lines or raw host bind paths.
8. The relay resolves every logical mount and intersects every requested limit
   with its local operator policy.
9. The relay may narrow or reject a launch; it may never broaden it.
10. Every runtime, process, operation, lease, and emitted message has a UUID and
    creation timestamp.
11. Acquire and start operations are idempotent.
12. Unknown outcomes are reconciled before any retry creates another container.
13. Every remote container has an expiring, fenced runtime lease.
14. Every container carries exact ownership labels; prefix-wide deletion is
    forbidden.
15. A stale server, relay connection, or lease epoch cannot control a newer
    runtime.
16. Runtime credentials are scoped to endpoint, runtime, user, conversation,
    agent, provider, service, credential slot, and expiry.
17. The remote relay cannot assert user identity, roles, ACLs, or conversation
    ownership.
18. Credential-slot exclusivity remains globally enforced across all compute
    endpoints.
19. Runtime stdout/stderr/events are bounded and ordered.
20. All provider traffic that PawFlow sees today remains visible in the webchat.
21. The remote path cannot bypass the existing MITM/hook/event publication path.
22. Force-stop revokes the runtime credential/egress lease and fences the
    runtime immediately, releases the logical credential slot, and is not
    recorded as an agent error; physical container cleanup may complete
    asynchronously without blocking the next loop.
23. Viewer disconnect never kills the underlying tmux or CLI session.
24. Relay disconnect never proves that a container is dead.
25. PawFlow never launches a replacement until reconciliation proves that the old
    runtime is absent or fenced.
26. Local-only installations preserve current behavior and performance.
27. No legacy direct-Docker provider path remains after the one-shot migration.
28. Compute-only relays require no workspace path.
29. Existing workspace compatibility mount modes remain `off|ro|rw` with the
    current default and provider matrix.
30. Compute capacity and placement are observable in the PawFlow UI and Relay
    Desktop without exposing secrets or raw host paths.

## 6. Terminology and domain model

### 6.1 RelayNode

A durable installation/machine identity from
`REMOTE_RELAY_ENROLLMENT_SHARING_PLAN.md`.

A node may expose several endpoints and reconnect without changing `node_id`.

### 6.2 ComputeEndpoint

A relay endpoint with CLI runtime capabilities and no required filesystem root.

~~~json
{
  "endpoint_id": "uuid",
  "node_id": "uuid",
  "endpoint_kind": "cli_compute",
  "display_name": "vps-cli-eu-01",
  "state": "available",
  "drain_state": "accepting",
  "capabilities": [
    "runtime.cli.claude",
    "runtime.cli.codex",
    "runtime.cli.gemini",
    "runtime.cli.antigravity",
    "runtime.cli.batch",
    "runtime.cli.interactive",
    "runtime.pty"
  ],
  "platform": "linux",
  "architecture": "amd64",
  "labels": {
    "region": "eu-west",
    "class": "general"
  },
  "policy_revision": 3,
  "created_at": "ISO-8601",
  "updated_at": "ISO-8601"
}
~~~

Rules:

- IDs are server-issued stable UUIDs;
- labels used for scheduling are server-approved;
- operator-advertised capabilities are untrusted until intersected with enrollment
  and server policy;
- no fake workspace root is created for compute-only endpoints;
- a relay connection may carry both workspace and compute endpoints.

### 6.3 ComputePolicy

Relay-local policy configured from Relay Desktop:

~~~json
{
  "schema_version": 1,
  "profile_id": "uuid",
  "server_profile_id": "uuid",
  "display_name": "vps-cli-eu-01",
  "enabled": true,
  "providers": ["claude", "codex", "gemini", "antigravity"],
  "max_runtimes": 8,
  "max_interactive_runtimes": 4,
  "max_batch_runtimes": 8,
  "cpu_total_reservation": 12.0,
  "memory_total_bytes": 25769803776,
  "per_runtime": {
    "cpu_max": 4.0,
    "memory_max_bytes": 8589934592,
    "pids_max": 512,
    "tmpfs_max_bytes": 536870912
  },
  "approved_image_policy_ids": [
    "pawflow-cli-current"
  ],
  "warm_images": [
    "pawflow-cli-current"
  ],
  "auto_start": true,
  "policy_revision": 1,
  "created_at": "ISO-8601",
  "updated_at": "ISO-8601"
}
~~~

Missing required fields fail validation. There is no anonymous/default compute
profile.

Remote “warm” means that the approved image digest and runtime bundle are already
present in the supervisor cache. A generic remote container is never prewarmed:
Docker mounts are fixed at container creation, and a pre-created container cannot
carry runtime-specific session, identity, credential, or lease mounts safely.
The local backend may retain its existing internal prewarm optimization because
its behavior is covered by local parity tests.

### 6.4 ExecutionTarget

The server-side schedulable view of either:

- the PawFlow server's local Docker adapter; or
- one ComputeEndpoint.

The server target is explicit. It is never implicitly added to a remote pool.

### 6.5 ComputePool

A server-side named selection policy over execution targets.

~~~json
{
  "pool_id": "uuid",
  "name": "cli-general",
  "scope": "global",
  "owner_user_id": "",
  "policy_id": "uuid",
  "membership": {
    "mode": "explicit",
    "target_ids": ["uuid", "uuid"],
    "required_labels": {}
  },
  "allowed_providers": ["claude", "codex", "gemini", "antigravity"],
  "include_server": false,
  "minimum_eligible_targets": 1,
  "queue_policy": {
    "mode": "bounded",
    "max_wait_seconds": 120,
    "max_depth_per_user": 8
  },
  "pool_revision": 1,
  "created_at": "ISO-8601",
  "updated_at": "ISO-8601"
}
~~~

`membership.mode` is exactly one of:

- `explicit`: only `target_ids` are considered and `required_labels` must be
  empty;
- `labels`: any authorized endpoint matching every approved label is considered
  and `target_ids` must be empty;
- `explicit_plus_labels`: the union of both sets, de-duplicated by endpoint ID.

There is no implicit intersection or connection-order behavior. Label keys and
values are selected from the server-approved endpoint label vocabulary; a relay
cannot add itself to a pool by advertising a new label. `include_server` adds the
explicit server execution target as one candidate after the same provider, image,
capacity, and authorization filters. `minimum_eligible_targets` is the capacity
gate referenced by upgrade preflight in section 15.6.

Pools are global/admin-owned or user-owned in v1. A conversation-scoped service
may reference an authorized pool, but endpoints and pools are not themselves
conversation-owned. Saving a global service does not grant its users access to a
target: principal authorization is re-evaluated for every reservation.

Pool updates use compare-and-set on `pool_revision`. Rename changes only the
display name. Deleting a pool that is referenced by a service, queued request, or
live runtime is rejected until references are changed and the pool is drained;
there is no name-based rebinding.

Concrete `llmConnection.provider` values map to scheduling capabilities through
one server-owned table used by schema rules, compatible-target listing, and the
scheduler:

| Service provider | Family | Mode capability | Provider capability |
|---|---|---|---|
| `claude-code` | `claude` | `runtime.cli.batch` | `runtime.cli.claude` |
| `claude-code-interactive` | `claude` | `runtime.cli.interactive` | `runtime.cli.claude` |
| `codex-app-server` | `codex` | `runtime.cli.batch` | `runtime.cli.codex` |
| `codex-interactive` | `codex` | `runtime.cli.interactive` | `runtime.cli.codex` |
| `gemini` | `gemini` | `runtime.cli.batch` | `runtime.cli.gemini` |
| `antigravity-interactive` | `antigravity` | `runtime.cli.interactive` | `runtime.cli.antigravity` |

Unknown CLI provider values fail validation. API-only providers have no runtime
placement capability.

### 6.6 ServerExecutionTargetPolicy

The server-local Docker path is represented by one persisted execution target,
not by a magic fallback branch. Its admin-owned policy defines:

- enabled/disabled state and stable target UUID;
- allowed CLI providers and approved image policies/digests;
- total and per-runtime CPU, memory, pids, and tmpfs limits;
- batch/interactive capacity;
- Docker doctor/health state and policy revision.

Disabling this target prevents new local reservations but does not rewrite remote
services. It is shown in the same compatibility and capacity APIs as relay targets.

### 6.7 RuntimeLease

A server-issued, endpoint-bound claim on capacity and control authority.

Required fields:

- `lease_id`;
- `runtime_id`;
- `execution_target_id`;
- `server_id` and server boot epoch;
- `connection_id` and endpoint policy revision;
- user/conversation/agent/service/provider identity;
- credential slot;
- resource reservation;
- creation, expiry, renewal deadline;
- fencing epoch;
- state.

### 6.8 RuntimeHandle and ProcessHandle

Provider-neutral handles replace local container names and Popen objects.

~~~python
@dataclass(frozen=True)
class RuntimeHandle:
    runtime_id: str
    execution_target_id: str
    lease_id: str
    lease_epoch: int
    provider: str
    created_at: str

@dataclass(frozen=True)
class ProcessHandle:
    process_id: str
    runtime_id: str
    stream_id: str
    io_mode: str
    created_at: str
~~~

Container names, Docker IDs, host PIDs, and host paths remain relay-private.

## 7. Target architecture

~~~text
PawFlow webchat / API / scheduler
              |
              v
Agent loop + CLI provider orchestration
              |
              v
CliRuntimeRouter
    | explicit server          | exact endpoint / compute pool
    v                          v
LocalDockerRuntime       RelayDockerRuntime
    |                          |
server Docker             existing authenticated relay WS
                               |
                               v
                    relay worker runtime channel
                               |
                               v
                    host ComputeSupervisor
                               |
                               v
                       remote Docker daemon
                               |
                               v
                    CC / Codex / AGY container
                               |
                               v
                  relay-local MCP runtime gateway
                               |
                               v
                   PawFlow ToolRelayService
                               |
                               v
              selected workspace relay(s) and tools
~~~

The CLI container never needs to know where its workspace relay runs. PawFlow MCP
routing remains authoritative.

## 8. Operator setup and PawFlow Relay Desktop product flow

The supported production paths are the headless `pawflow-relay` CLI and PawFlow
Relay Desktop. They are two frontends over the same manager, versioned profile,
secure store, doctor, and OS service. A VPS never requires a graphical session.

### 8.1 Headless VPS golden path (primary)

The default documented deployment is one Linux VPS with Docker and outbound
HTTPS/WebSocket access to PawFlow. It requires no public inbound port, workspace
directory, Docker command authoring, JSON editing, endpoint UUID entry, or
provider credential on the VPS.

Prerequisites shown before the user starts:

- one explicitly supported Linux distribution/architecture combination;
- an operator account with `sudo` for installation and an unprivileged dedicated
  service account for steady state;
- a supported Docker Engine/API and enough disk, memory, CPU, pids, cgroup, and
  FUSE/mount-propagation capability for the selected provider preset;
- correct system time and outbound DNS + TLS/WSS to the PawFlow URL and the
  documented signed-package/image registries;
- a disclosure that Docker daemon membership is effectively host-root authority
  and that the compute-host administrator can inspect runtime memory/files.

The primary flow is:

1. In PawFlow, an admin chooses **Add compute VPS**, a server-owned policy preset
   (`Small`, `General`, or `Interactive`), allowed providers, and an ACL. PawFlow
   issues a short-lived, single-use enrollment code and a copyable bootstrap
   command that contains the server URL and public enrollment ID, never the secret.
2. On the VPS, the operator installs a versioned PawFlow Relay package from the
   documented repository after signature/checksum verification. Piping an
   unauthenticated network script into a privileged shell is not a supported path.
3. The operator runs
   `pawflow-relay compute bootstrap --server <url> --enrollment-code-stdin` and
   pastes the code into a hidden prompt. The code is never accepted on `argv`,
   stored in shell history, or written into the compute profile.
4. `bootstrap` creates or selects the server profile, generates an immutable local
   installation/profile ID, runs doctor, shows requested versus locally enforceable
   limits, and asks before pulling only server-approved signed image digests.
5. The command installs and starts the supported OS service, stores the resulting
   machine/runtime credential in the OS secure store, waits for registration, and
   prints one stable success URL/status command. Re-running the command reconciles
   the same installation; it does not create another endpoint.
6. PawFlow shows the endpoint as **Pending approval** with doctor results and trust
   location. An authorized admin approves it; capability increases and trust-domain
   changes always require fresh approval. Single-user deployments may opt into
   issuance-time approval explicitly, never by an implicit default.
7. The endpoint becomes **Available** only after an accepted heartbeat. The admin
   runs **Test placement**, which reserves and releases capacity and performs a
   broker/MCP/network isolation probe without consuming a provider turn.
8. A service editor chooses the endpoint or pool in the typed service form, saves,
   and runs the provider-specific smoke turn. The UI shows the resolved location
   and an actionable remediation if the test cannot run.

On any failure, the CLI and UI preserve the profile and report one stable error
code plus a copy-safe, redacted remediation command. Retrying resumes from the
last completed idempotent step. Setup never silently enables server fallback or
broadens an ACL/capability.

### 8.2 Navigation

Add a third top-level section beside Servers and Relays:

~~~text
Servers
Workspace Relays
Compute Relays
~~~

A physical workspace relay may also enable compute in its detail panel. The UI
still presents the compute endpoint separately because it has independent
capabilities, policy, health, and drain state.

### 8.3 Add Compute Relay wizard

The wizard must collect and validate:

1. PawFlow server profile;
2. login/enrollment status;
3. compute relay display name;
4. Docker daemon availability;
5. provider capabilities: Claude, Codex, Gemini, Antigravity;
6. batch and/or interactive support;
7. approved installed image/digest policy;
8. total concurrent runtime limit;
9. batch and interactive limits;
10. per-runtime CPU, memory, pids, and tmpfs ceilings;
11. optional approved-image/runtime-bundle warm-cache targets;
12. operator-approved labels such as region/class;
13. auto-start on Relay Desktop launch;
14. optional drain timeout for controlled shutdown.

No workspace directory is required.

### 8.4 Local diagnostics before enrollment

The wizard runs an explicit doctor sequence and shows each result:

- Docker CLI/API reachable;
- Docker daemon platform and architecture;
- current user authorized to control Docker;
- required CLI image policy available by digest;
- minimum free memory and disk;
- cgroup/resource limit support;
- FUSE availability when session/compatibility mounts require it;
- mount propagation capability;
- outbound TLS/WebSocket reachability to PawFlow;
- local port/network availability for the runtime gateway;
- runtime bundle signature verifier available;
- clock offset within policy.

A failed mandatory check blocks enabling compute. Warnings require explicit
operator acknowledgement and are sent to PawFlow as diagnostics, not capabilities.

Relay Desktop may pull an approved published image after explicit confirmation.
It does not build arbitrary images as part of runtime scheduling.

### 8.5 Enrollment and registration

From the GUI:

1. select or add a server;
2. authenticate or enter an operator-issued machine enrollment code;
3. request/register a RelayNode;
4. register a `cli_compute` endpoint with its requested capability set;
5. receive the server-approved endpoint ID and capability ceiling;
6. store enrollment/runtime credentials through the existing secure-store path;
7. start the relay connection;
8. wait for a signed server acceptance response;
9. show the endpoint as available only after the first valid capacity heartbeat.

The compute endpoint registers through the normal relay connection. It is not a
parallel unmanaged agent protocol.

For an initial private-only release, current authenticated user enrollment may be
used. Machine credentials and shared/global compute pools require the identity
and lease foundations from `REMOTE_RELAY_ENROLLMENT_SHARING_PLAN.md`.

### 8.6 Compute panel

The detail panel displays:

- connected/offline/draining state;
- endpoint ID and node ID;
- accepted capabilities;
- Docker daemon version/platform;
- approved CLI image digests and CLI versions;
- runtime bundle version;
- active/reserved/available slots;
- CPU/memory reservation and observed usage;
- active runtimes grouped by provider, without prompt or secret content;
- last heartbeat and RTT;
- current policy revision;
- last runtime error;
- buttons: Start, Stop, Drain, Resume, Doctor, View Logs, Reconcile.

### 8.7 Drain and shutdown

`Drain` immediately stops new reservations while existing runtimes continue.

The operator chooses:

- drain indefinitely;
- drain until a deadline, then stop idle runtimes;
- force-stop all runtimes after explicit destructive confirmation.

Closing Relay Desktop follows the configured background/tray behavior. It must
not accidentally terminate compute runtimes merely because the window closes.

### 8.8 Shared state and CLI parity

Extend `pawflow_relay.manager` with compute profiles and matching CLI commands:

~~~text
pawflow-relay compute add
pawflow-relay compute bootstrap
pawflow-relay compute doctor
pawflow-relay compute start
pawflow-relay compute drain
pawflow-relay compute resume
pawflow-relay compute status
pawflow-relay compute stop
pawflow-relay compute delete
~~~

Relay Desktop and the CLI use the same persisted schema and manager functions.
The Electron main process remains a thin IPC adapter.

### 8.9 Profile persistence and migration

The current relay manager stores servers and workspace shares in separate JSON
maps keyed by mutable display name. Compute adds a versioned
`compute_profiles.json` document with an atomic file lock and replace-on-write,
keyed by immutable `profile_id`; display names are mutable and never become
runtime identity. The schema has an explicit version and a one-shot migration
function covered by golden old/new fixtures. Unknown newer versions fail closed
and remain untouched.

Enrollment credentials, runtime-control credentials, and tokens remain in the
existing secure-store path and are referenced by opaque key IDs. They never
appear in the JSON profile, Electron IPC replies, renderer state, logs, export, or
diagnostics. Export/import omits secrets and requires re-enrollment.

A server profile deletion must first enumerate dependent workspace and compute
profiles. With a live or queued runtime, deletion is rejected until the endpoint
is drained, queues are cancelled, server-side enrollment is revoked, and runtime
state is reconciled. The UI shows the exact cascade before confirmation. Local
profile deletion alone never masquerades as server-side revocation.

### 8.10 Connection and process ownership

One authenticated node connection may multiplex workspace and compute endpoints;
enabling both must not create two competing machine identities or duplicate
heartbeats. The manager owns a node connection and independently starts/stops its
endpoint capabilities. The UI links the duplicated workspace/compute rows back to
the same node and server profile.

Window close continues to hide to tray. Explicit application quit is different:

- with no active/reserved runtime, it may stop the node connection normally;
- with active work, the default action is cancel-quit;
- the operator may choose drain-and-quit-when-empty or explicitly force-stop;
- no ordinary quit path silently kills or orphans compute containers.

For unattended VPS operation, the relay worker and `ComputeSupervisor` are
managed by the canonical relay manager/CLI as an OS background service (systemd
on the initial Linux target), not as children whose lifetime depends on an
Electron renderer/window. Relay Desktop controls that same service. `auto_start`
means start after host/service boot once secure storage is unlocked, not merely
after opening a window. Unsupported host/service-manager combinations are shown
as such and cannot claim auto-start.

Policy edits use compare-and-set on `policy_revision`. New limits apply to new
reservations. Tightening policy below live usage puts the endpoint in
`policy_pending_drain`; it does not mutate live Docker limits or kill containers
without an explicit action.

One host may contain profiles for several PawFlow servers. A single supervisor
therefore owns one host-wide capacity ledger and hard ceiling; per-profile limits
are sub-quotas, not separately advertised copies of the machine. Reservation is
atomic across all server profiles. Each PawFlow server sees only its endpoint's
quota/usage plus currently available capacity, never another server's identities,
runtimes, queues, or prompts. Ownership labels include the server ID/boot epoch,
and no server can inspect, signal, reconcile, or reap another server's runtime.

Drain state records its source (`local_operator`, `server_admin`, `update`,
`policy`, or `health`). A server may request drain, but it cannot resume a drain
imposed by the local operator or host policy. Relay Desktop shows the effective
state and every contributing reason.

Relay Desktop loads an initial manager snapshot and then receives structured,
versioned state events over Electron IPC for process, doctor, pull/update, drain,
capacity, runtime, and reconciliation changes. The renderer never infers state by
parsing stdout. Operator logs are redacted, bounded, rotated, and fetched through
an explicit paged IPC action; runtime prompt/output bodies are absent.

### 8.11 Runtime image and bundle lifecycle

CLI runtime images are a separate product surface from the existing relay worker
image builder/catalog. Relay Desktop and the CLI provide allowlisted operations
to list, pull, verify signature/digest, pre-cache, inspect usage, and garbage
collect approved CLI images and runtime bundles.

Rules:

- a policy stores immutable digests; mutable tags are resolved only during an
  explicit update operation;
- in-use or rollback-retained digests cannot be removed;
- pull progress, verification failures, disk pressure, and required free space are
  visible locally and to authorized PawFlow admins;
- updates drain incompatible endpoints before switching the advertised digest or
  bundle version;
- garbage collection has a dry-run preview and never uses broad Docker cleanup;
- C1 diagnostics may report image readiness, but runtime image management cannot
  be confused with building arbitrary user images.

### 8.12 Upgrade, rollback, backup, and decommission

The manager provides one lifecycle rather than leaving operators to manipulate
containers, JSON, or systemd units directly:

- `update --check` reports package, protocol, runtime-bundle, image-policy, disk,
  and minimum-pool-capacity compatibility without changing state;
- `update` downloads and verifies signed artifacts before drain, refuses to reduce
  a pool below `minimum_eligible_targets`, applies one endpoint at a time, runs
  doctor, and resumes only after a valid heartbeat;
- rollback selects only a retained, previously verified package/bundle/image
  tuple compatible with the server protocol; schema downgrades that would lose
  data are refused;
- backup exports only the versioned non-secret profile and public installation
  identity. Restore requires secure-store recovery or fresh enrollment and never
  fabricates an available endpoint;
- normal decommission drains, cancels queues, reconciles exact runtime IDs, revokes
  the server enrollment/runtime credentials, removes server references, stops the
  OS service, then offers local secure-store/journal removal and previewed image
  cleanup in that order;
- a lost or compromised VPS uses **Revoke now** server-side first. Revocation and
  epoch fencing do not wait for the machine to reconnect; server reference cleanup
  remains explicit and auditable.

Removing the local profile, uninstalling the package, deleting a server endpoint,
and revoking its credential are distinct actions. Both UIs show which have
completed and provide the next safe action. No lifecycle command invokes broad
Docker prune or deletes containers without exact PawFlow ownership labels.

### 8.13 Acceptance scenarios

Each scenario is a required product smoke test with the expected user-visible
result and fail-closed guardrail.

| Scenario | User flow and expected result | Required guardrail |
|---|---|---|
| 1. Existing local installation | Upgrade PawFlow without adding a relay; migrated CLI services remain explicitly on `server` and behave as before. | No enrollment prompt, remote dependency, or silent placement change. |
| 2. Solo user, one compute VPS | Run the headless golden path, select one exact endpoint, and execute a batch Codex turn. | No inbound port or provider secret on the VPS; failure never falls back locally. |
| 3. Desktop-assisted workstation | Create the same profile in Relay Desktop, close the window, and continue work through the OS service. | Desktop and CLI show one immutable profile/node; window close cannot kill work. |
| 4. Separate workspace and compute | Run the CLI on compute relay B while tools read/write workspace relay A. | MCP keeps the original principal/approvals; no peer-to-peer or empty-mount shortcut. |
| 5. Multi-VPS pool | Add two differently labelled VPS endpoints and select a named pool. New turns spread deterministically and obey capacity. | Only explicit/approved members are candidates; server is absent unless explicitly included. |
| 6. Mixed provider capability | Put batch-only and interactive-capable endpoints in one pool and run Gemini batch plus Codex interactive. | Capability/image/platform filtering occurs before reservation; incompatible work queues or fails clearly. |
| 7. Shared pool, several users | An admin grants a group pool access; an allowed user runs work while a non-member tries the same global service. | Authorization is re-evaluated per reservation; denial does not enumerate endpoint infrastructure. |
| 8. Saturation and fairness | Fill all slots, enqueue work from two users, cancel one queued turn, then free capacity. | Bounded per-user/service/global depth and deadlines apply; cancellation/reload cannot duplicate a turn. |
| 9. Maintenance or partial outage | Drain/update one node while another serves new work; then partition or restart the active relay. | No new work lands on drain; unknown outcomes reconcile before retry and never duplicate a runtime. |
| 10. Incident and decommission | Revoke a lost VPS, enter emergency server-only mode if explicitly chosen, then decommission the endpoint. | Broker/MCP access is fenced immediately, config is not silently rewritten, and deletion/cleanup is exact and audited. |

## 9. PawFlow service configuration and user selection

CLI-capable `llmConnection` services gain:

~~~json
{
  "cli_execution": {
    "mode": "server",
    "execution_target_id": "",
    "compute_pool_id": "",
    "image_policy_id": "pawflow-cli-current",
    "unavailable_policy": "fail",
    "queue_timeout_seconds": 0
  }
}
~~~

Allowed modes:

- `server`: current local Docker;
- `endpoint`: one exact ComputeEndpoint;
- `pool`: server schedules among a named ComputePool.

Validation rules:

- `endpoint` requires `execution_target_id`;
- `pool` requires `compute_pool_id`;
- `server` forbids both remote selectors;
- every CLI placement requires an approved `image_policy_id` compatible with its
  concrete provider and all candidate targets;
- missing or inconsistent fields raise `ValueError`;
- `unavailable_policy` is `fail` or `queue`;
- local fallback is represented only by explicitly including the server target in
  a pool;
- the resolved target is stored on the turn/runtime marker and UI events.

`queue_timeout_seconds=0` means use the bounded server policy default, not wait
forever and not fail immediately. The effective wait is the minimum positive
ceiling across server policy, service request/default, and selected pool policy.
Exact-endpoint queueing uses the server-wide per-user, per-service, and global
depth ceilings because there is no pool policy. `unavailable_policy=fail` never
enqueues regardless of timeout.

The webchat service configuration UI lists only authorized, capability-compatible
targets. It shows endpoint status and trust location before save.

### 9.1 Global remote-compute kill switch

Add an audited server setting `cli_remote_execution_enabled`. Setting it to
`false`:

- rejects every new remote reservation;
- cancels queued remote placements;
- leaves active remote runtimes under explicit drain/force-stop control;
- does not silently reinterpret configured remote services as local.

Add a separate, destructive admin action “Emergency server-only mode”. It
temporarily routes new CLI runtimes to the server only after explicit
confirmation that the server target is authorized and has capacity. It preserves
the saved service/pool configuration, displays a global banner, records actor,
reason, UUID, and timestamp, and is reversible. This is the requested operational
“bring everything back to the server” switch, not an implicit fallback.

### 9.2 PawFlow service-form integration

The current schema-driven service editor renders `type=object` as a raw JSON
textarea. That is not an acceptable UI for placement. Keep `cli_execution` as the
persisted object, but add a dedicated `execution_placement` editor/field adapter
which serializes that object and is visible only for CLI-backed providers.

The adapter must provide:

- server / exact endpoint / pool radio selection;
- authorized, provider/mode/image-compatible target or pool options from a typed
  server helper, not IDs supplied by the browser;
- online, draining, update-required, capacity, region/class, operator/trust-domain,
  and server-versus-relay labels;
- fail/queue and bounded timeout controls with pool-policy ceilings;
- a read-only resolved preview and “Test placement” action before save;
- preservation of a configured missing/offline UUID as an unavailable reference,
  never silent selection of the first option;
- a warning/confirmation before delegating data or credential use to a newly
  selected trust domain.

Install, edit, read-only view, copy, scope move, provider change, and PFP/service
import paths all use the same adapter and server validation. Changing from a CLI
provider to an API-only provider removes `cli_execution` only after confirmation;
changing back does not guess an old target. Existing CLI services migrate to an
explicit `mode=server`; API-only services have no placement object.

The existing `docker_image` string is not sent to a compute relay. Introduce an
admin-managed image-policy registry mapping stable policy IDs to provider,
platform/architecture, immutable digest, runtime-bundle compatibility, and the
allowed local/remote targets. The one-shot migration maps known shipped image
names/tags to policy IDs. An unknown custom local image remains usable only on an
explicitly approved server policy until an admin creates and verifies a matching
image policy; it cannot be selected remotely by passing its tag through the
service form. Existing CPU/memory fields compile into a typed resource request
and are intersected with server, pool, and relay policy. The placement preview
shows requested versus effective limits.

Save validation and reservation validation are both mandatory. Save catches stale
or incompatible references early; reservation re-evaluates current principal,
service scope, endpoint ACL, pool revision, health, capability, and kill-switch
state. Moving/copying a service never transfers target authorization. A global
service can therefore be visible while its remote placement is unavailable to a
particular caller, producing a non-enumerating fail-closed error.

### 9.3 PawFlow admin surfaces and API lifecycle

Integrate the feature into the existing admin settings gear and Resources /
Services UI rather than assuming a separate admin application. Add admin views
for Compute Endpoints, Compute Pools, Runtime/Queue, and Server Docker. They use
typed, admin-authorized actions for:

- endpoint approve/revoke/quarantine, capability ceiling, drain/resume,
  reconcile, doctor, logs, policy revision, and update-required status;
- pool create/read/update/delete, membership preview, effective candidates,
  minimum capacity, queue limits, and referential-usage preview;
- server target enablement, image policy, limits, capacity, and Docker doctor;
- active runtime summaries, pending/unknown cleanup, queues, operation receipts,
  and stable error codes;
- the global kill switch and reversible emergency server-only mode, including
  actor, reason, UUID, timestamp, current effective state, and explicit exit.

Every mutating action uses compare-and-set revisions where applicable and is
audited. Non-admin service editors see only authorized target summaries; they
cannot enumerate nodes, other users, raw host paths, Docker IDs, IPs, or operator
logs. Deleting/revoking an endpoint is distinct from removing a local Relay
Desktop profile, and the UI explains which side of that boundary is being acted
on.

### 9.4 Live UI events and degraded states

Add scoped SSE events for endpoint health/capacity, pool revision, queue state,
runtime state, placement resolution, drain progress, reconciliation, kill-switch,
and emergency-mode changes. Events carry UUIDs/timestamps and safe stable IDs,
are authorization-filtered, and trigger targeted state updates rather than a full
resource reload on every five-second heartbeat.

Webchat shows `waiting_for_capacity`, position/reason/deadline, resolved execution
location, degraded/offline/update-required, reconciliation, and force-stop state
on the existing turn/agent status surface. Conversation reload restores current
state from a snapshot API; SSE is not the only source of truth. Browser disconnect
does not cancel a queued request or live runtime.

The emergency banner is global and sticky until the server reports exit from the
mode. Normal messages remain free of placement noise. All new strings, dialogs,
keyboard actions, focus handling, status colors/icons, and responsive layouts are
covered by the existing i18n/accessibility conventions.

### 9.5 Referential and configuration invariants

- IDs, not display names, are persisted by services and pools.
- Endpoint/pool deletion is blocked while referenced; a force-detach operation
  first drains/cancels and rewrites nothing automatically.
- Offline, revoked, quarantined, and update-required are distinct persisted/UI
  states with distinct remediation.
- Pool membership preview and the scheduler use the same candidate-filtering
  function and policy revision.
- Queue policy exists once, on the selected service plus pool ceilings; the UI
  displays the effective minimum timeout/depth rather than two contradictory
  values.
- Configuration snapshots expose `configured`, `resolved`, and `effective`
  placement separately so emergency mode never appears to have edited a service.
- Every settings/API response includes a schema/protocol version and rejects
  unknown newer versions.

## 10. Scheduling and horizontal scale

### 10.1 Candidate filtering

Before scoring, remove every target that is:

- offline, stale, revoked, quarantined, or draining;
- unauthorized for the principal/service;
- missing the provider or interactive/batch capability;
- missing the required image digest/runtime bundle/protocol feature;
- at a lower endpoint policy revision than required;
- unable to satisfy CPU/memory/pids/tmpfs reservation;
- incompatible with platform/architecture;
- already using the credential slot for another live runtime;
- outside the compute pool selector.

### 10.2 Scoring

Use deterministic weighted least-load scoring:

1. reuse the endpoint of an existing live interactive session;
2. approved image digest and runtime bundle already cached locally;
3. free reserved capacity ratio;
4. active runtime count;
5. recent launch failure penalty;
6. heartbeat RTT as a low-weight tie breaker;
7. stable hash of runtime request ID for deterministic ties.

Workspace location is not a scheduling requirement because MCP PawFlow is the
canonical access path. Same-node workspace affinity may optimize existing
compatibility mounts but must not alter tool routing or eligibility.

### 10.3 Atomic reservation

Scheduling is a two-step protocol:

1. server creates a pending reservation with an operation UUID;
2. target atomically accepts or rejects it against its current policy/capacity;
3. server commits the target selection and lease;
4. process launch begins only after commit.

Rejected reservations are removed immediately and the scheduler may try another
eligible endpoint within the same explicit pool. This is pool scheduling, not
fallback outside the requested pool.

### 10.4 Queue semantics

If `unavailable_policy=fail`, return a placement/capacity error immediately.

If `queue`:

- enqueue once using the runtime request UUID;
- enforce per-user, per-service, and global depth limits;
- expose queue position and reason in the webchat;
- wake on capacity heartbeat/release, not a polling sleep loop;
- expire at the configured deadline;
- force-stop or conversation generation change cancels the queued request;
- never launch a stale queued turn.

### 10.5 Interactive affinity

A live interactive session is pinned to its ComputeEndpoint and runtime ID.

A later turn for the same live session:

- reuses that exact runtime;
- does not run the scheduler again;
- fails/reconciles if the endpoint is offline;
- never creates a second runtime until the first is proven dead or fenced.

### 10.6 Autoscaling boundary

PawFlow exports capacity and queue metrics for an external VPS autoscaler, but
does not provision infrastructure in this feature.

A new VPS scales the pool by:

1. installing PawFlow Relay Desktop/relay runtime;
2. configuring compute;
3. enrolling;
4. connecting and publishing capacity;
5. becoming eligible automatically through the configured pool membership policy.

## 11. Runtime abstraction

Create a focused package such as `core/cli_runtime/`:

~~~text
types.py
backend.py
local_docker.py
relay_docker.py
router.py
scheduler.py
registry.py
mounts.py
streams.py
errors.py
~~~

### 11.1 Backend interface

~~~python
class CliRuntimeBackend(Protocol):
    async def reserve(self, request: ReservationRequest) -> Reservation: ...
    async def acquire(self, spec: LaunchSpec, reservation: Reservation) -> RuntimeHandle: ...
    async def start(self, runtime: RuntimeHandle, spec: ProcessSpec) -> ProcessHandle: ...
    async def write_stdin(self, process: ProcessHandle, data: bytes) -> None: ...
    async def resize_pty(self, process: ProcessHandle, rows: int, cols: int) -> None: ...
    async def signal(self, process: ProcessHandle, signal: str) -> None: ...
    async def poll(self, process: ProcessHandle) -> ProcessStatus: ...
    async def wait(self, process: ProcessHandle, timeout: float | None) -> ProcessExit: ...
    async def copy_in(self, runtime: RuntimeHandle, artifact: RuntimeArtifact) -> None: ...
    async def read_file(self, runtime: RuntimeHandle, logical_path: str) -> bytes: ...
    async def release(self, runtime: RuntimeHandle, reason: str) -> ReleaseReceipt: ...
    async def reconcile(self, operation_id: str) -> OperationReceipt: ...
~~~

All mutable actions return structured receipts.

### 11.2 Local backend

`LocalDockerRuntime` wraps the current Docker mechanics and is the parity
reference. It uses the same handles/specs as the remote backend.

Do not implement remote support by scattering `if remote` across pools.

### 11.3 Remote backend

`RelayDockerRuntime`:

- resolves one exact connected ComputeEndpoint;
- sends typed runtime RPC over its authenticated relay channel;
- exposes stream-backed process handles;
- renews leases;
- reconciles unknown acquire/start/release outcomes;
- never exposes relay-private container/PID/path values to providers.

### 11.4 Provider migration rule

Provider orchestration may use:

- RuntimeHandle;
- ProcessHandle;
- structured status/exit/event objects.

It may not use:

- container names;
- Docker IDs;
- host PIDs;
- `docker_cmd()`;
- direct `subprocess.run/Popen` for container control.

A source-check test enforces this rule across `core/llm_providers`, pool modules,
live registries, terminal actions, and Codex image generation.

## 12. Typed launch and process specifications

### 12.1 LaunchSpec

Required fields include:

- request, runtime, user, conversation, agent, service UUIDs;
- timestamps;
- provider profile ID;
- exact approved image policy ID and digest;
- runtime bundle digest/version;
- batch or interactive runtime mode;
- resource limits;
- network profile ID;
- logical mount declarations;
- credential/session slot declarations;
- runtime gateway capability token reference;
- lease ID, expiry, and fencing epoch;
- expected endpoint policy revision;
- audit correlation ID.

Unknown fields fail schema validation. The relay never executes raw appended flags.

### 12.2 ProcessSpec

Contains:

- command profile enum, not a shell command;
- validated argument list allowed by that provider profile;
- working-directory logical name;
- environment key/value references from an allowlist;
- stdin/stdout/stderr or PTY mode;
- timeout;
- expected runtime epoch;
- process operation UUID and timestamp.

Command profiles include explicit entries such as:

- `claude.batch`;
- `claude.interactive`;
- `codex.app_server`;
- `codex.interactive`;
- `codex.image_job`;
- `gemini.batch`;
- `antigravity.interactive`;
- `tmux.attach`;
- `runtime.chronyd`.

No generic `bash -c` profile is exposed by the server protocol. Provider-specific
shell wrappers are versioned runtime-bundle content validated by the supervisor.

## 13. Mount and data-view parity

Mounts are logical capabilities, not host paths.

### 13.1 Provider session view

Current provider session state remains authoritative on the PawFlow side so that
local and remote placement can reuse the same conversation/provider state.

The remote runtime receives only the exact authorized session subtree needed by
that runtime. Requirements:

- read/write;
- uid/gid derived from `PAWFLOW_RUN_UID/GID`, never hardcoded;
- mount namespace isolation identical to current pools;
- live token/config writes visible to PawFlow;
- no other user slot;
- no unrelated credential slot;
- disconnect produces explicit I/O failure, never an empty local directory.

For private single-owner compute relays, the existing inverse `sfs.*` FUSE path
may bootstrap the first vertical slice after the fail-closed immutable-owner
mitigation.

Shared/global compute endpoints require runtime-identity-bound inverse channels.
Do not reuse mutable `RelayService._user_id`.

### 13.2 Skills and FileStore

Preserve current provider behavior:

- current CLI mounts expose the entire global skill tree and the current user's
  entire skill tree, not only assigned skills; the runtime-scoped remote view
  must mirror that exact visible tree for parity;
- attachments/materialized images remain available where current providers expect
  session-local files;
- a shared compute endpoint receives no permanent global/user mount: each runtime
  gets an ephemeral view of the current global tree plus only its bound user's
  tree, fenced by the RuntimeLease;
- the compute-host operator can inspect that runtime view and this is disclosed
  as part of the endpoint trust boundary.

### 13.3 Workspace compatibility mounts

MCP tools remain canonical.

For providers that currently request compatibility mounts:

1. the server sends logical workspace endpoint IDs and target paths;
2. the compute endpoint resolves a same-host workspace to a local bind;
3. a different-host workspace uses the routed FUSE design in
   `RELAY_WORKSPACE_FS_PLAN.md`;
4. mode remains the current `off|ro|rw`;
5. default relay maps to `/workspace`;
6. linked relays map to `/relay/<sanitized-id>`;
7. disconnection returns an I/O error and never exposes an empty substitute;
8. providers without workspace mounts today receive none.

The cross-relay FUSE path is compatibility infrastructure, not the canonical
workspace API and not a scheduler affinity requirement.

### 13.4 Runtime bundle

Current source-tree bind mounts and `docker cp` calls are replaced by one of:

- runtime client already present in an approved image; or
- a signed, content-addressed runtime bundle cached by the supervisor and mounted
  read-only.

The bundle contains bridge/proxy/hook/runtime code only. It contains no server
configuration or credentials.

## 14. Relay-side ComputeSupervisor

### 14.1 Placement

A host-side `ComputeSupervisor` owns Docker access. Relay Desktop starts and
monitors it.

The relay worker may run inside a container. It forwards typed runtime frames to
the supervisor over authenticated local IPC. The worker itself does not gain an
unrestricted Docker socket.

Preferred IPC:

- Unix domain socket with owner-only permissions on Linux;
- named pipe with explicit ACL on Windows;
- loopback authenticated socket only where required.

The existing generic host helper must not be widened into an arbitrary Docker
proxy. Compute actions use a separate allowlisted protocol and credential.

### 14.2 Responsibilities

The supervisor:

- validates schemas and signatures;
- intersects requests with ComputePolicy;
- reserves capacity atomically;
- creates approved Docker networks/volumes/mounts;
- runs, execs, inspects, signals, and removes owned containers;
- streams process output with backpressure;
- manages PTYs/tmux attachment;
- maintains operation receipts;
- renews and expires leases;
- persists a minimal runtime journal;
- reaps orphaned owned containers;
- publishes capacity/health metrics;
- redacts secrets from logs/errors;
- refuses raw paths, flags, images, env inheritance, and privileged options.

### 14.3 Docker policy

Mandatory defaults:

- no privileged containers;
- no host PID/network namespace;
- no Docker socket inside CLI containers;
- no arbitrary devices;
- no arbitrary host roots;
- approved image digest;
- exact CPU/memory/pids/tmpfs limits;
- AppArmor/seccomp policy where supported;
- `--init`;
- exact PawFlow labels;
- per-runtime network;
- runtime gateway is the only PawFlow control-plane route;
- provider API egress allowed according to the provider network profile.

`SYS_ADMIN` remains permitted only for profiles that require the current private
mount namespace behavior, with the existing AppArmor containment and an explicit
policy capability.

### 14.4 Labels

Every container includes exact labels for:

- PawFlow server ID and boot epoch;
- node ID and endpoint ID;
- runtime ID and lease ID/epoch;
- provider and runtime mode;
- non-secret user/conversation/agent hashes;
- creation timestamp;
- runtime bundle digest.

Cleanup queries exact labels and validates the recorded Docker ID before removal.

### 14.5 Journal and restart reconciliation

Persist under Relay Desktop's relay home:

- runtime/operation IDs;
- container ID and exact labels;
- lease epoch/expiry;
- provider/mode;
- reserved resources;
- process IDs known to Docker;
- last stream sequence;
- terminal/tmux metadata;
- receipt status.

Do not persist prompts, stdout bodies, tokens, environment values, or credentials.

On supervisor restart:

1. enumerate exact-labeled containers;
2. match journal and Docker state;
3. quarantine unknown/conflicting ownership;
4. reconnect valid live runtimes;
5. report reconciliation state to PawFlow;
6. reap only runtimes whose leases are definitely expired/fenced.

## 15. Runtime relay protocol

Use a versioned protocol family, not the generic filesystem `exec` action.

### 15.1 Control messages

~~~text
runtime.capabilities.v1
runtime.capacity.v1
runtime.reserve.v1
runtime.reserve_result.v1
runtime.acquire.v1
runtime.acquire_result.v1
runtime.start.v1
runtime.start_result.v1
runtime.stdin.v1
runtime.resize.v1
runtime.signal.v1
runtime.wait.v1
runtime.status.v1
runtime.renew.v1
runtime.release.v1
runtime.reconcile.v1
runtime.receipt.v1
runtime.stream.v1
runtime.stream_ack.v1
runtime.error.v1
~~~

Every frame carries:

- message UUID;
- creation timestamp;
- operation UUID;
- endpoint, runtime, lease, and epoch identifiers as applicable;
- protocol version;
- audit correlation ID.

### 15.2 Acquire example

~~~json
{
  "type": "runtime.acquire.v1",
  "message_id": "uuid",
  "created_at": "ISO-8601",
  "operation_id": "uuid",
  "endpoint_id": "uuid",
  "runtime_id": "uuid",
  "lease_id": "uuid",
  "lease_epoch": 4,
  "expected_policy_revision": 7,
  "launch_spec": {},
  "audit_id": "uuid"
}
~~~

### 15.3 Stream frames

~~~text
process.started(process_id, seq=0)
process.stdout(process_id, seq, binary_chunk)
process.stderr(process_id, seq, binary_chunk)
process.event(process_id, seq, structured_event)
process.exited(process_id, final_seq, exit_code, signal, timestamp)
~~~

Requirements:

- binary-safe encoding;
- bounded frame and queue sizes;
- sequence numbers per stream;
- explicit final sequence;
- server acknowledgements;
- bounded replay window;
- coalescing only where provider semantics permit;
- stdout/stderr separation;
- no loss hidden as success;
- backpressure cannot block relay heartbeats or force-stop.

### 15.4 Idempotency and unknown outcomes

The supervisor stores a receipt keyed by `operation_id` and payload digest.

- same ID + same digest returns the prior receipt;
- same ID + different digest is a protocol violation;
- disconnect before receipt yields unknown outcome;
- server calls `runtime.reconcile` after reconnect;
- no retry creates another runtime/process until receipt state is known;
- stale epoch operations are rejected.

### 15.5 Mandatory timing constants

The first release uses concrete defaults, all reported in capabilities and
bounded by server policy:

| Timer/window | Default | Required behavior |
|---|---:|---|
| compute heartbeat interval | 5 s | monotonic sequence, no overlap |
| endpoint stale threshold | 15 s | stop new reservations |
| endpoint offline threshold | 20 s | show offline and begin reconciliation |
| reservation TTL | 15 s | auto-release if acquire is not committed |
| normal control RPC acknowledgement | 10 s | then unknown, never blind retry |
| container acquire/start deadline | 60 s | explicit timeout receipt |
| runtime lease TTL | 30 s | supervisor-enforced |
| lease renewal interval | 10 s | renew before one-third TTL remains |
| supervisor kill grace after lease expiry | 2 s | block egress immediately, then SIGKILL |
| disconnect reconciliation ceiling | 35 s | lease TTL + kill grace + 3 s evidence margin |
| force-stop logical fence/slot-release deadline | 2 s | broker/MCP revocation and slot release complete without waiting for the endpoint |
| stream replay window | 30 s or 8 MiB | whichever is reached first |
| operation receipt retention | 24 h | keyed by operation UUID + payload digest |

An endpoint disconnect may therefore block reuse of a physical runtime handle for
at most 35 seconds, but it does not block the next logical loop after force-stop:
section 17.4 revokes broker access, fences the credential epoch, and releases the
logical slot immediately.

The values are configuration ceilings, not arbitrary per-service knobs. Increasing
lease or reconciliation windows requires an administrator-visible policy revision
because it changes the product's failure promise.

### 15.6 Protocol/version skew policy

The compute runtime protocol has one accepted major/schema digest per PawFlow
release. There is no dual old/new runtime path:

- mismatched endpoints remain connected only for enrollment, update status,
  diagnostics, and drain;
- they enter explicit `update_required`, not silently “offline”;
- they are unschedulable and excluded with a visible reason;
- server upgrade preflight lists incompatible endpoint IDs and calculates
  remaining pool capacity;
- cutover is refused if it would take a pool below its configured minimum
  capacity unless an administrator explicitly accepts the outage;
- operators drain and update Relay Desktop/supervisors as one maintenance
  operation, then cut over the server;
- the old compute protocol implementation is removed in the new release.

This follows the one-shot rule in
`REMOTE_RELAY_ENROLLMENT_SHARING_PLAN.md` section 24. It does not add the
compatibility window proposed by the older AWS plan.

## 16. MCP and tool routing

### 16.1 Required topology

~~~text
CLI container
   -> relay-local runtime gateway
   -> authenticated compute relay channel
   -> PawFlow ToolRelayService
   -> normal tool registry and authorization
   -> selected workspace relay / browser / desktop / services
~~~

The runtime gateway:

- is reachable only from owned runtime networks;
- authenticates a short-lived runtime capability token;
- cannot accept user/conversation identity from the container;
- binds identity server-side;
- forwards the exact existing MCP wire behavior;
- enforces message size/rate/backpressure;
- expires with the runtime lease;
- closes immediately on revocation or force-stop.

No generic `internal_auth` bypass token is exported to a VPS.

### 16.2 Transparency requirements

For an identical prompt and provider state:

- tool definitions are identical;
- MCP tool names and schemas are identical;
- tool calls enter the same PawFlow handler path;
- workspace relay selection is identical;
- permission/approval behavior is identical;
- tool results are published through the same webchat path;
- provider output event normalization is identical.

A contract test compares local and remote tool transcripts after removing only
placement IDs/timestamps.

### 16.3 Network path, bandwidth, and egress budgets

A remote tool call adds the compute-relay leg:

~~~text
CLI container -> compute gateway -> PawFlow -> workspace relay/tool
~~~

For a workspace on another relay, request and result traverse both WAN legs.
This is intentional; it does not authorize peer-to-peer bypass.

Reference acceptance topology: compute-to-server RTT <= 50 ms and
workspace-relay-to-server RTT <= 50 ms.

Budgets:

- runtime-gateway processing overhead: p95 <= 10 ms, excluding network RTT;
- a small MCP call adds no more than one compute/server RTT + 25 ms p95 over the
  same tool invoked by a server-local CLI;
- 1 MiB sequential MCP/FUSE transfer sustains at least 80% of the slower measured
  relay link after protocol overhead on the reference topology;
- stream control frames are prioritized over bulk data;
- default stream chunks are 64 KiB and filesystem chunks remain bounded;
- every endpoint/runtime reports ingress, egress, retransmit, replay, and
  backpressure bytes;
- pool policy may set bandwidth and monthly egress ceilings;
- reaching a ceiling produces an explicit capacity/policy error, never truncated
  success or local fallback.

The C0 baseline records local and relay RTT/throughput. C4 records artifact and
stream results; C5 cannot ship until the MCP/FUSE budgets have measured results.
Cost documentation must include the two-WAN-leg case and an operator formula for
monthly VPS egress.

## 17. Credentials and provider sessions

### 17.1 Server ownership

PawFlow continues to:

- select the configured `llmConnection`;
- select/reserve the credential slot;
- prepare provider configuration;
- enforce one-live-session exclusivity where refresh tokens are single-use;
- recover rotated tokens through the authoritative session view;
- maintain a fenced credential epoch independent from physical container cleanup.

The compute relay never selects a credential or falls through to another slot.

### 17.2 Runtime exposure

Remote runtimes do not receive reusable provider refresh tokens or API keys.
They receive a runtime credential handle understood by the credential/egress
broker described in section 17.4.

This document is authoritative for CLI compute credential custody. The optional
relay-local upstream credential profile described in the broader
`REMOTE_RELAY_STORAGE_AND_CLI_EXECUTION_PLAN.md` is not part of v1 remote CLI
compute and must not appear in Relay Desktop's v1 compute form. Adding such a
mode later requires a separately named execution/ownership mode, provider support
matrix, audit and revocation semantics, and must never be selected as fallback.

Requirements:

- short-lived runtime-bound handle delivery;
- encrypted relay transport;
- exact session subtree or sealed secret injection;
- no full service config;
- no inherited server environment;
- no credential value in logs, labels, receipts, heartbeat, UI, or audit;
- cleanup/revocation on runtime end;
- explicit trust disclosure that the compute-host operator controls the Docker
  host and can inspect runtime memory/files, while reusable upstream credentials
  remain broker-side.

### 17.3 Token rotation

The credential broker performs refresh with the authoritative server-side slot
and writes successful rotations back atomically. Runtime-facing auth files contain
only opaque/runtime-bound material or non-reusable short-lived access material.
The server records a refresh success before advancing the recovered-token memo.

### 17.4 Credential/egress broker and force-stop

Direct reusable credentials plus a partitioned VPS make the three requirements
“immediate force-stop”, “one credential slot”, and “next loop unaffected”
impossible to satisfy simultaneously. This plan therefore requires a broker for
remote execution:

- provider and OAuth/token traffic from remote CLI containers is forced through
  the runtime gateway/approved egress proxy;
- the container has no reusable refresh token or API key that works outside that
  broker;
- the broker binds the credential slot to runtime ID and credential epoch;
- refresh requests are executed server-side and rejected for a fenced epoch;
- direct provider/token-endpoint egress is blocked by the runtime network policy.

Force-stop then:

1. increments/fences the runtime and credential epochs;
2. revokes broker and MCP capability tokens immediately;
3. releases the logical credential slot immediately;
4. permits the next loop to acquire the new credential epoch;
5. sends high-priority remote kill without waiting;
6. lets the supervisor's 30-second lease/2-second kill grace clean a partitioned
   physical container.

The fenced old container cannot call the provider, refresh a token, use MCP, or
race the next loop even while physical cleanup is pending. Remote execution is
not enabled for a provider until its broker/egress profile passes this invariant.

### 17.5 MITM certificate and key material

`core/cc_interactive_certs.py` has two distinct secret classes:

- the PawFlow CA private key `data/system/cc_interactive_ca.key`, which must
  never leave the PawFlow server;
- a per-session leaf key/certificate (for example `api-anthropic.key/.crt`),
  which the in-container MITM necessarily receives through the exact runtime
  session view.

The CA certificate may be installed in the runtime trust store. The CA private
key is never placed in a session tree, FUSE export, artifact, runtime bundle,
launch spec, or relay cache. The leaf key remains mode 0600, runtime-scoped, is
deleted with the session policy, and its exposure to the compute-host operator is
part of the remote-host trust disclosure.

The current `ca_private_key_is_host_only()` string-list check cannot be reused as
the sole guard after logical mount compilation. Replace it with a structural
launch validator that resolves every logical mount/artifact before acquire and
rejects:

- the CA private-key artifact kind;
- the resolved CA key path or inode;
- any parent directory capable of exposing `SYSTEM_DIR`;
- CA private-key bytes in a runtime bundle.

Keep the existing path guard for the local adapter as defense in depth. Contract
tests build local and remote launch specs for CCI, Codex interactive, and AGY and
prove that only CA certificate + leaf material cross the runtime boundary.

## 18. Batch provider migration

Migrate one vertical slice first: Codex image generation. It has no conversation
workspace, uses a job-scoped session directory, and can transfer inputs/outputs as
bounded artifacts. It proves enrollment, reservation, remote Docker/process
control, image/runtime bundles, the credential broker, streaming, and cleanup
without depending on the general session FUSE path.

The slice is complete only when both local and remote adapters pass the same
provider lifecycle suite.

Then migrate:

1. Codex app-server/batch after runtime-scoped session/MCP views;
2. Claude Code batch;
3. Gemini CLI batch/app;
4. compact/memory/auxiliary CLI invocations using those providers.

Each migration removes the old direct-Docker provider code in the same change.
There is no permanent compatibility branch.

## 19. Interactive provider migration

### 19.1 Remote ownership

The persistent container, tmux, MITM/observer proxy, hooks, and provider CLI all
run on the selected compute endpoint.

PawFlow retains:

- conversation/agent orchestration;
- the three provider live registries plus the cross-provider live-session index;
- credential lease;
- context phase;
- response assembly;
- webchat/SSE publication;
- terminal session authorization;
- timeout/sweeper policy.

### 19.2 Live registry key

Use:

~~~text
(execution_target_id, runtime_id, user_id, conversation_id,
 agent_name, service_id, credential_slot)
~~~

A local container name is never a registry identity. Migrate
`cc_live_registry.py`, `codex_live_registry.py`,
`gemini_live_registry.py`, and `cli_live_sessions.py`; in particular,
`codex_live_registry.py` must stop using `container_name` for liveness and
identity.

### 19.3 Prompt injection and tmux

Replace direct `docker exec tmux ...` calls with typed runtime operations:

- load buffer;
- paste buffer;
- send keys;
- capture pane for readiness diagnostics;
- has-session;
- resize;
- attach/detach viewer;
- kill session.

Literal prompt bytes remain binary-safe and preserve current retry/readiness
semantics.

### 19.4 MITM and event visibility

CC/Codex/AGY proxy and hook events flow through the runtime gateway/relay channel
to the existing event services.

Non-negotiable:

- traffic observed locally today is observed remotely;
- every user-visible provider response reaches the webchat;
- no queue silently drops MITM/hook frames;
- sequence/ack metrics prove whether a frame is pending, delivered, or rejected;
- relay congestion cannot reorder turn boundaries;
- compaction and Stop/SessionEnd events retain their current semantics.

### 19.5 Terminal viewer

Browser xterm remains connected to `services/terminal_proxy.py`.

The proxy registers a remote runtime terminal session and multiplexes:

- viewer input;
- resize;
- output;
- attach status;
- process exit.

Closing/reloading a tab only detaches the viewer. It never kills tmux. Reattachment
uses the same runtime/process/session handle and bounded reconnect policy.

### 19.6 Force-stop and normal stop

Force-stop:

1. bumps the conversation generation;
2. cancels queued placement;
3. fences runtime and credential epochs and revokes broker/MCP access;
4. releases the logical credential slot immediately;
5. clears active/live registry state and records physical cleanup separately;
6. sends a high-priority runtime kill outside normal output backpressure;
7. publishes normal stopped state, not an agent error;
8. lets the next loop start without waiting for endpoint reconciliation.

A disconnected endpoint may leave a fenced physical container until lease expiry,
but that container has no provider/MCP egress and owns no usable credential epoch.

Normal completion preserves current live reuse/idle-timeout policy.

### 19.7 Migration order

1. Codex interactive;
2. Claude Code interactive;
3. Antigravity interactive.

Codex first provides the end-to-end reference for tmux, MITM, MCP, token recovery,
terminal viewing, reconnect, and reuse.

## 20. Capacity heartbeat and observability

Compute endpoints publish a signed heartbeat containing:

- endpoint/policy/protocol/runtime bundle versions;
- Docker platform/version;
- approved image digests and provider CLI versions;
- accepting/draining state;
- total/reserved/active capacity;
- active runtime counts by provider/mode;
- queue is server-side, so no prompt/job bodies;
- disk pressure and image availability;
- last launch/reaper error code;
- clock and heartbeat sequence.

PawFlow exports metrics for:

- eligible/online/draining endpoints;
- reservations accepted/rejected;
- scheduling latency;
- queue depth/wait/expiry;
- launch latency;
- warm/cold starts;
- active runtimes;
- lease renewal failures;
- reconciliation outcomes;
- stream bytes/backpressure/replay;
- provider first-event and completion latency;
- force-stop latency;
- orphan cleanup;
- runtime errors by stable code.

Logs include IDs and states, never secrets or prompt/output bodies by default.

## 21. Failure semantics

### 21.1 Target offline before reservation

- remove from candidates;
- try another target only inside the explicitly selected pool;
- otherwise fail or queue according to policy;
- never use server implicitly.

### 21.2 Disconnect after reservation, before acquire

- reservation becomes unknown;
- reconcile operation receipt;
- release only after confirmed absence/expiry;
- do not consume a second credential slot.

### 21.3 Disconnect after container creation

- mark runtime `connection_unknown`;
- keep the live/credential lease;
- reconnect and query exact runtime ID;
- resume streams from bounded acknowledged sequence where supported;
- do not launch a duplicate.

### 21.4 Compute supervisor restart

- rebuild state from journal + exact Docker labels;
- report every runtime as live, exited, missing, or quarantined;
- PawFlow reconciles registries and leases;
- no broad orphan deletion during uncertainty.

### 21.5 PawFlow restart

- current local behavior is the parity reference: server shutdown terminates CLI
  pool/interactive runtimes; v1 does not adopt them after restart;
- server boot creates a new boot/credential epoch and immediately rejects all
  broker traffic from the old epoch;
- reconnecting endpoints receive a release/fence command for the old boot epoch;
- unreachable supervisors self-stop old runtimes at the 30-second lease expiry
  plus 2-second kill grace;
- new loops may launch immediately under the new epoch because old runtimes have
  no usable broker credential;
- saved provider session files may seed a normal cold/restart path, but live
  process/tmux adoption is explicitly out of scope for v1.

This is not covered by C0 characterization alone. Add a separate restart-cleanup
gate that proves local and remote backends both terminate old runtimes, invalidate
old credentials, and start the next loop cold. Live adoption, if ever desired, is
a separate post-v1 feature with its own security and durability plan.

### 21.6 Docker daemon failure

- endpoint advertises unavailable capacity;
- active runtimes become unknown/exited based on Docker evidence;
- no local fallback;
- show a placement-specific error in webchat/admin UI.

### 21.7 Session/FUSE failure

- fail the process with an explicit session filesystem error;
- never mount an empty replacement directory;
- do not treat missing credentials/config as provider logout until storage
  connectivity is proven healthy.

### 21.8 Workspace compatibility FUSE failure

- MCP tools remain available if their workspace relay is online;
- direct fallback mount operations return explicit I/O errors;
- do not change the provider's configured mount policy silently;
- surface degraded compatibility-mount status in runtime diagnostics.

### 21.9 Stream overflow

- apply bounded backpressure;
- preserve control/kill channel priority;
- if loss is unavoidable, terminate with an explicit overflow error and final
  sequence gap;
- never present a truncated stream as successful completion.

## 22. Security model and threat controls

Threats include:

- malicious or over-privileged service editor/conversation user;
- Internet attacker against enrollment, relay transport, or local IPC;
- malicious/compromised compute relay;
- compromised compute-host administrator or another tenant on that host;
- malicious CLI container;
- forged runtime identity;
- replayed acquire/start;
- arbitrary Docker flags/path/env injection;
- cross-user session mount;
- credential theft;
- stale endpoint taking control after reconnect;
- output/event flood;
- label spoofing and cross-runtime cleanup;
- host filesystem escape;
- runtime gateway impersonation;
- direct provider egress that bypasses PawFlow visibility/fencing;
- SSRF, DNS rebinding, redirects, or cloud-metadata access through the egress path;
- compromised package, runtime bundle, image tag, registry, or downgrade;
- local IPC abuse by an unprivileged desktop process;
- audit loss, deletion, tampering, or secret leakage;
- cross-PawFlow-server observation/control on a shared compute host;
- resource, queue, disk, bandwidth, or provider-cost exhaustion.

Controls:

1. endpoint enrollment and capability ceiling;
2. operation-time principal authorization;
3. typed schemas and strict unknown-field rejection;
4. server-issued IDs and immutable identity binding;
5. signed/hashed runtime bundle;
6. approved image digest;
7. relay-side policy intersection;
8. runtime-scoped capability token;
9. runtime lease and fencing epochs;
10. exact labels and journal correlation;
11. runtime-specific mounts;
12. no raw Docker socket/API;
13. network isolation;
14. resource limits;
15. bounded streams;
16. token/secret redaction;
17. audit records for placement, reserve, launch, signal, release, reconcile,
    drain, policy, enrollment, and revocation;
18. immediate active-lease invalidation on endpoint revocation.

### 22.1 Trust boundaries and data classification

PawFlow is the authoritative control plane for principal identity, ACLs, service
configuration, scheduling, credential slots, leases, and audit. A relay node is an
authenticated but untrusted executor: it may report observations and enforce a
narrower local policy, but it cannot grant itself a capability, role, pool
membership, identity, image, mount, or credential.

The compute-host administrator is trusted to observe and alter data inside
runtimes on that host. The product must disclose that boundary before placement.
Isolation protects users and PawFlow servers from accidental or remote cross-scope
access; it does not claim confidentiality against host root. Do not recommend a
compute host shared with mutually untrusted host administrators.

Data classes are explicit:

- **server-only secrets**: provider refresh tokens/API keys, PawFlow CA private
  key, master secret keys, ACL state, and reusable enrollment secrets;
- **runtime ephemeral secrets**: runtime capability handle/token, short-lived
  access material where unavoidable, and per-session MITM leaf key;
- **sensitive content**: prompts, responses, tool payloads/results, attachments,
  session files, workspace compatibility data, and terminal streams;
- **operational metadata**: stable IDs, provider/mode, state, timestamps, bounded
  resource usage, policy revisions, and redacted error codes;
- **public artifacts**: signed package metadata, public certificates, image
  digests, runtime-bundle digests, and protocol versions.

Every protocol field, store, log, UI/API response, metric, backup, and diagnostic
declares which classes it accepts. Unknown or unclassified fields fail validation.

### 22.2 Authorization and role matrix

All actions use the existing `PrincipalContext`/canonical-group policy evaluator
at operation time. Suggested UI labels map to explicit permissions; role names are
not hard-coded authorization shortcuts.

| Actor | Allowed in v1 | Explicitly not implied |
|---|---|---|
| PawFlow server admin | issue/revoke enrollment, approve/quarantine endpoints, manage ACLs/pools/image policies/server target, inspect redacted operations, emergency mode | access to prompt/output bodies unless separately authorized as that conversation principal |
| Compute-host operator | install, bootstrap, doctor, locally narrow policy, drain, update/rollback, inspect host-local redacted logs | PawFlow user identity, endpoint ACL grants, pool membership, provider secrets, another server profile, or remote resume of a local-policy drain |
| Service editor | choose/test an authorized compatible target or pool for an in-scope service | endpoint/pool administration, ACL grants, raw infrastructure discovery, or permission transfer on copy/scope move |
| Conversation user | invoke an authorized service, see own placement state, cancel/force-stop own runtime | other users' queues/runtimes/logs, endpoint details, or changing placement policy |
| Auditor/support permission | query/export authorized redacted durable audit and diagnostics | mutation, secret/content access, or cross-scope enumeration |
| Enrollment credential / relay machine | perform only its typed enrollment/runtime scopes under the server ceiling | human admin authority merely because it created or hosts an endpoint |

Every reserve, queue, attach, signal, log/diagnostic read, reconcile, and release
rechecks the current principal and object revisions. Revocation invalidates caches,
leases, SSE subscriptions, and runtime gateway tokens immediately.

### 22.3 Network isolation and anti-bypass egress

- The relay/supervisor initiates outbound authenticated TLS/WSS to an explicitly
  configured PawFlow origin. Production setup exposes no public inbound relay,
  supervisor, Docker, runtime-gateway, metrics, debug, or terminal port.
- Every runtime receives a dedicated internal network namespace with default-deny
  egress. It can reach only the relay-local runtime gateway through an
  authenticated endpoint; it cannot reach the Docker socket/API, host gateway,
  other containers/networks, PawFlow directly, LAN/private/link-local ranges, or
  cloud metadata services.
- Provider/token traffic is relayed by the broker under a server-owned provider
  profile that allowlists scheme, port, method, and destinations. IP literals,
  CONNECT tunnelling, alternate ports, ambiguous URLs/userinfo, and unsupported
  redirects are rejected.
- DNS is resolved in the trusted broker path. Each resolution and redirect target
  is revalidated against private/link-local/loopback/reserved ranges and the
  provider allowlist; redirect and DNS-rebinding limits are bounded. TLS hostname,
  SNI, certificate, and response-size/time limits are enforced.
- Network policy is created before a runtime receives secrets or starts, survives
  relay reconnect, and is removed only after exact runtime cleanup. A runtime has
  no `NET_ADMIN` capability or privileged mode with which to alter it.
- Provider profiles prove whether streaming, OAuth refresh, artifact, and MITM
  flows remain fully visible and fenceable. A provider is unavailable remotely
  until its direct provider and token-endpoint bypass tests fail as intended.
- Doctor and **Test placement** probe that direct provider access, PawFlow control
  endpoints, a private-address canary, and cloud metadata are unreachable from the
  runtime while the authenticated gateway path succeeds.

### 22.4 Host, container, filesystem, and local IPC hardening

- The supervisor runs as a dedicated non-login service identity with minimum file
  permissions. The documentation calls out that access to a rootful Docker daemon
  is root-equivalent; rootless Docker may be supported only after the same
  mount/FUSE/resource/chaos contract passes and must be reported accurately by
  doctor.
- Runtime containers are never privileged and do not use host PID, IPC, user, or
  network namespaces. Drop all capabilities except an audited provider-specific
  minimum, set `no-new-privileges`, a supported seccomp/LSM profile, pids/CPU/memory
  limits, and a read-only root filesystem with bounded exact tmpfs/write mounts.
- No Docker socket, device, arbitrary host path, supervisor state directory, relay
  secure store, other profile, or parent session directory is mounted. Mounts are
  resolved canonically and revalidated for symlink, hard-link, rename, UID/GID,
  propagation, and time-of-check/time-of-use escapes.
- Docker logs are disabled or bounded according to the stream design; core dumps
  and swapping of runtime secret mounts are disabled where the platform supports
  it. Secret files are mode 0600, scoped to the runtime UID, and unlinked during
  release/reconciliation. The product does not claim secure physical erasure from
  Docker/SSD layers and documents that limitation.
- Supervisor/manager local control uses an owner-only Unix socket (or OS-equivalent)
  with peer-credential validation, restrictive directory permissions, typed
  schemas, request size/rate limits, and no bearer secret returned to Electron.
  Electron keeps context isolation/sandboxing, exposes an allowlisted preload API,
  validates sender/window/origin, and never gives the renderer raw filesystem,
  shell, environment, secure-store, or arbitrary manager-method access.
- A host-wide supervisor separates PawFlow server profiles by immutable server ID,
  boot epoch, credential, journal namespace, labels, network, and local
  authorization. Every inspect/signal/reap operation requires an exact match on
  all ownership dimensions.

### 22.5 Secret and sensitive-data lifecycle

Reusable provider credentials stay in PawFlow's existing encrypted authority and
are never copied to relay storage, runtime configuration, backup, crash report, or
diagnostic bundle. Runtime handles/material have the shortest provider-compatible
TTL, explicit audience, endpoint/runtime/user/conversation/provider/slot binding,
and independent fencing epoch.

Sensitive session/artifact data is transferred only through runtime-scoped views,
is size/TTL bounded, and is removed after the configured recovery window. The UI
states what persists on PawFlow, the compute host, and the workspace relay. Normal
cleanup, forced cleanup, restart reconciliation, backup, restore, and uninstall
have tests proving that secrets and content do not become orphaned. Since host root
can inspect live runtime data, encryption at rest is defense in depth rather than a
claim against the compute-host operator.

### 22.6 Supply chain and update safety

- PawFlow Relay packages, repository metadata, runtime bundles, and CLI images are
  signed by pinned, rotatable trust roots and verified before use; digest checks
  alone do not establish publisher identity.
- Release artifacts publish checksums, SBOM, provenance, supported protocol/schema
  ranges, and vulnerability-scan results. CI tests verification failure, expired or
  revoked signing keys, wrong architecture, registry substitution, and rollback.
- Mutable tags are never stored or launched. Resolution to a digest occurs only in
  an authorized update transaction, and the server and relay independently verify
  the policy ID, digest, signature, provider, architecture, and bundle compatibility.
- Auto-update is opt-in, drain-aware, staged one endpoint at a time, and cannot
  bypass pool minimum capacity. The last known-good compatible tuple is retained
  under bounded disk policy. Forced downgrade across an incompatible schema or
  security minimum is rejected.
- Runtime code cannot invoke image pull/build/update/GC. Those operations use
  separate operator/admin authorization, exact artifact IDs, bounded extraction,
  archive traversal checks, and a dry-run cleanup preview.

### 22.7 Durable audit and incident evidence

`core/audit.py` is currently a bounded in-memory deque; that implementation is not
sufficient for this feature. Before C4 can launch a remote provider runtime,
PawFlow must provide a durable, append-only audit backend with documented
retention, backup/export, access control, restart recovery, pagination, and
integrity/tamper evidence. The in-memory view may remain only as a cache.

Each security/compute event has its own UUID and UTC timestamp plus actor/principal
type, action, result/stable error, authorized object IDs, server/endpoint/runtime/
lease/operation correlation IDs, policy and fencing revisions, source trust domain,
and redacted before/after metadata. It never stores enrollment/provider/runtime
secrets, prompt/output/tool bodies, raw host paths, private IPs for unauthorized
viewers, or arbitrary exception payloads.

New enrollment, approval, policy/image/ACL changes, placement tests, reservations,
launches, attaches, signals, force-stops, reconciliations, emergency-mode changes,
updates/rollbacks, revocations, exports, and decommission steps are audited.
High-volume heartbeats/stream frames remain metrics, not audit events.

If durable audit is unavailable, new enrollment, policy mutations, remote
reservations, and launches fail closed and the admin/webchat UI shows a sticky
security degradation. Safety actions such as revoke, fence, cancel, force-stop,
and emergency drain still execute; their records enter a bounded high-priority
recovery journal and must be flushed before normal remote operation resumes.
Audit clearing is not an ordinary UI/API operation.

### 22.8 Abuse, quota, and cost controls

Server, pool, user, service, endpoint, provider, and credential-slot ceilings cover
concurrent runtimes, queue depth, launch rate, CPU/memory/pids/tmpfs, stream/frame
bytes, artifact/session bytes, disk reserve, bandwidth, monthly egress, wall time,
idle time, and provider request/token budgets where measurable. Limits are checked
before reservation and continuously enforced where applicable. Exceeding one
produces a stable scoped error and audit/metric without leaking another tenant's
usage or falling back. Alerts distinguish user saturation, host pressure, attack
rate limiting, broker/provider cost ceilings, and infrastructure failure.

## 23. Server and Relay Desktop UI

### 23.1 PawFlow admin/resource UI

Add views for:

- Compute Endpoints;
- Compute Pools;
- endpoint authorization/policy;
- online/draining/capacity status;
- active runtime summaries;
- queue status;
- audit/reconciliation diagnostics.

### 23.2 Webchat

Show:

- resolved execution location on agent/service status;
- queued/waiting-for-capacity state;
- remote endpoint offline/degraded state;
- terminal attachability;
- clear failure reason without leaking infrastructure secrets.

Do not add placement noise to every normal message. Placement belongs in runtime
status/diagnostics and errors.

### 23.3 Relay Desktop implementation surfaces

Expected files:

- `pawflow-relay-desktop/src/index.html`;
- `renderer.js`;
- `main.js`;
- `preload.js`;
- `styles.css`;
- `pawflow_relay/manager.py`;
- `manager_cli.py`;
- `thread.py`;
- packaged `runtime/` copies generated by the existing prepare step.

Do not hand-edit generated runtime copies independently. Update canonical sources
and let the existing packaging preparation regenerate them.

### 23.4 PawFlow implementation surfaces

The UI/config work is not confined to the `llmConnection` class. The implementation
inventory must include:

- `services/llm_connection.py` schema, rules, validation, and provider-change
  migration;
- the service registry install/update/copy/scope-move/import validation paths;
- `tasks/io/chat_ui/resources_service_dialogs.js` and
  `resources_service_login.js` for the placement adapter;
- the Resources/Services renderers and `admin_settings.js` for endpoint, pool,
  server-target, kill-switch, and emergency-mode views;
- server actions/helpers for compatible-target listing, CRUD, doctor, placement
  test, drain/reconcile, and referential-usage previews;
- `sse_handlers_*.js`, turn/agent status rendering, conversation snapshot restore,
  and UI action routing;
- i18n catalogs, CSS/responsive/accessibility behavior, and the explicit module
  load list in `tasks/io/serve_chat_ui.py`;
- service/PFP export-import and scope-move tests so placement references cannot
  bypass authorization through a secondary configuration path.

The endpoint/pool option helper is dynamic and principal-scoped. It must not be
stored in the static service-schema cache, whose lifetime is longer than endpoint
health, capacity, policy revisions, and caller authorization.

## 24. One-shot migration and code map

### 24.1 New core components

Expected additions:

- `core/cli_runtime/*`;
- runtime/pool scheduler and registry;
- structured runtime errors/events;
- local and relay backend adapters;
- runtime-scoped credential lease coordinator;
- durable append-only compute audit store, integrity/export API, degraded-state
  gate, and safety-action recovery journal.

### 24.2 Server relay components

Expected additions/changes:

- typed runtime dispatch in RelayService connection handling;
- compute endpoint registry/capability heartbeat;
- runtime gateway routing;
- inverse runtime-scoped session/skill/file views;
- terminal proxy remote-runtime adapter;
- service configuration/UI actions;
- provider egress-policy registry and authorization-filtered compute audit API.

### 24.3 Relay components

Expected additions:

- `pawflow_relay/compute_supervisor.py`;
- `compute_protocol.py`;
- `compute_policy.py`;
- `compute_journal.py`;
- `runtime_gateway.py`;
- host IPC adapter;
- Docker ownership/reaper;
- runtime FUSE/mount propagation integration;
- doctor, bootstrap, lifecycle, and manager commands;
- runtime network isolation/anti-bypass probes and hardened local IPC.

### 24.4 Existing provider/pool components to migrate

The C0 inventory starts from this explicit boundary (measured against the current
tree when this plan was revised):

| Category | In scope | Current measured surface |
|---|---|---:|
| Batch/interactive pools and helpers | `claude_code_pool.py`, `codex_pool.py`, `gemini_pool.py`, `claude_code_interactive_pool.py`, `codex_interactive_pool.py`, `antigravity_observer_pool.py`, `_cci_pool_spawn.py`, `_antigravity_input.py` | 5,633 lines; 74 textual `docker_cmd` references |
| Provider launch/stream/session consumers | `core/llm_providers/claude_code.py`, Codex app-server/stream/session modules, Gemini launch/stream/session modules, compact/memory helpers | 2 additional known `docker_cmd` references in `claude_code.py`; inventory every pool/handle/Popen dependency in C0 |
| Live runtime state | `cc_live_registry.py`, `codex_live_registry.py`, `gemini_live_registry.py`, `cli_live_sessions.py` | 1,452 lines |
| Browser terminal bridge | `services/terminal_proxy.py` and CLI terminal registration branches | 456 lines |
| Provider authentication/service-flow actions | CLI-specific branches in `_sf_k3.py`, `_sf_k6.py`, `_sf_k8.py`, `_sf_k9.py`, `_sf_routes.py`, and `service_flow.py` | containing files: 2,468 lines; 56 textual `docker_cmd` references |
| Auxiliary CLI jobs | Codex image generation and every other direct consumer of a migrated CLI pool | exact call graph in C0 |

The known core migration surface is therefore at least 7,541 existing lines
(pools/helpers + registries + terminal) before provider launchers, selected
service-flow branches, supervisor, protocol, gateway, journal, scheduler, FUSE,
credential broker, GUI, or tests.

Explicitly out of scope for the CLI source-check:

| Category | Files/examples | Reason |
|---|---|---|
| Shared Docker primitive | `core/docker_utils.py` (11 textual references) | remains the local implementation primitive; it is not the remote-runtime seam |
| PawFlow/relay update lifecycle | `core/update_manager.py` (8) | owns product image/update operations |
| Server relay containers | `core/server_relay_manager.py` (8) | relay lifecycle, not CLI provider runtime |
| Generic VNC cleanup | `services/vnc_proxy.py` (2) | non-CLI Docker consumer unless a CLI terminal branch calls it |
| AppArmor probing/loading | `core/apparmor.py` (2) | shared host policy used by the two authorized Docker owners |
| Deployment/realtime stacks | `core/compose_deployment.py` (2), `core/realtime_stack_manager.py` (2) | unrelated Docker features |
| Containerized ExecuteScript | `tasks/system/execute_script.py` | belongs to the AWS/general execution plan |
| Relay Desktop's own relay container | `pawflow_relay/_thread_docker.py` | transports the relay; ComputeSupervisor is a separate owner |

The current tree contains 167 textual `docker_cmd` references: 76 in the nine
known CLI pool/helper/provider modules above, 56 in the six named service-flow
files, 24 in the named non-CLI consumers, and 11 in `core/docker_utils.py` itself.
These are search references, not an assertion that every match is an executable
call. C0 replaces this snapshot with a machine-readable call/reference inventory.

C0 produces a machine-readable allowlist: after migration, a
`docker_cmd/subprocess Docker` source check fails only inside the CLI in-scope
set and permits the named out-of-scope owners. A repository-wide ban would
incorrectly fail unrelated Docker features.

After the final migration, direct Docker ownership belongs only to:

- `LocalDockerRuntime`;
- relay `ComputeSupervisor`;
- unrelated non-CLI Docker features explicitly outside this plan.

### 24.5 No legacy path

The release that completes a provider migration deletes its direct pool control
path. There is one provider-neutral runtime contract with local and remote
implementations, not old/new provider implementations.

### 24.6 Size and effort estimate

This is a large program, not a small transport patch. Initial production-code
estimate beyond the existing migration surface:

| New area | Estimated production lines |
|---|---:|
| runtime types/router/local+relay backends/scheduler | 1,800–2,800 |
| server endpoint registry, leases, gateway, broker, FUSE routing | 2,500–4,000 |
| relay supervisor, protocol, journal, Docker policy, local IPC | 2,500–4,000 |
| Relay Desktop/manager/CLI/admin/webchat UI | 1,500–2,500 |
| durable audit, network/host/supply-chain policy, install/update lifecycle | 800–1,400 |
| total new production code | 9,100–14,700 |
| tests/fixtures/fakes/chaos/usability harness | 8,000–12,500 |

Initial effort range, before C0 recalibration:

| Work | Engineering days |
|---|---:|
| WP-A immutable-owner prerequisite + C0 inventory/contracts | 4–7 |
| headless/Desktop enrollment/doctor/capacity-only milestone | 8–13 |
| runtime abstraction + local Codex image-job parity | 6–10 |
| supervisor/protocol/journal/policy + durable audit | 15–23 |
| credential broker + artifact transport + hardened remote image-job slice | 14–22 |
| runtime session/MCP/FUSE + Codex app-server | 15–24 |
| multi-VPS scheduler/queue/drain | 8–14 |
| remaining batch providers | 12–20 |
| Codex interactive | 12–20 |
| Claude Code interactive | 14–22 |
| Antigravity interactive | 10–18 |
| cleanup, UI/usability hardening, chaos/security rollout | 14–22 |
| total, before parallelism | 132–215 engineering days |

These are sizing ranges, not delivery dates. C1 publishes the measured call graph,
actual first-slice velocity, and revised ranges. No calendar commitment for the
Codex app-server or later phases is allowed before C0/C1 evidence is reviewed.

## 25. Test plan

### 25.1 Baseline characterization

Before refactoring, capture current behavior for every provider:

- launch args and effective limits;
- mount matrix;
- env allowlist;
- stdout/stderr/event ordering;
- timeout/cancel/force-stop;
- credential rotation;
- session reuse;
- compaction lifecycle;
- terminal attach/reconnect;
- cleanup/reaper.

### 25.2 Runtime backend contract suite

Run the same suite against local, fake relay, and real relay adapters:

- reserve/acquire/start;
- stdin/stdout/stderr;
- poll/wait;
- exit code/signal;
- terminate/kill;
- timeout;
- PTY resize;
- release idempotency;
- status/capacity;
- operation receipt reconciliation;
- unknown outcome;
- stale epoch rejection;
- bounded backpressure.

### 25.3 Placement/scheduler tests

- exact server target;
- exact remote endpoint;
- pool with one/many endpoints;
- deterministic scoring;
- capacity race;
- draining exclusion;
- provider/image/platform mismatch;
- bounded queue and expiry;
- fairness limits;
- explicit server pool inclusion;
- no implicit local fallback;
- pinned interactive reuse;
- stale queued turn cancellation.
- explicit, labels, and explicit-plus-label membership semantics;
- server target inclusion only when `include_server=true`;
- minimum eligible target upgrade gate;
- membership preview and scheduler candidate parity;
- pool revision conflict, rename stability, referenced-delete rejection, and
  drain-before-force-detach;

### 25.4 Operator setup and Relay Desktop tests

- headless `compute bootstrap` and Desktop wizard produce the same versioned
  profile, doctor result, enrollment request, and OS-service state;
- enrollment secret is accepted through a hidden stdin/secure-store path and is
  absent from argv, shell history, profile JSON, process listings, logs, IPC, and
  diagnostics;
- interrupted bootstrap resumes idempotently without duplicate node/endpoint IDs;
- pending approval, approval, first-heartbeat availability, and test-placement
  states are distinct and actionable;
- create/edit/delete compute profile;
- no workspace path required;
- doctor failures and warnings;
- enrollment/registration;
- secure credential persistence;
- start/stop/drain/resume;
- auto-start;
- status/metrics rendering;
- CLI/Desktop shared state;
- packaged-runtime parity;
- IPC validation;
- log redaction;
- versioned compute-profile migration and unknown-newer-version rejection;
- immutable profile ID across rename and endpoint reconnect;
- server deletion dependency preview and active-runtime rejection;
- window close versus explicit quit with cancel, drain, and force-stop choices;
- OS background-service ownership and Desktop/CLI shared control;
- policy revision conflicts and `policy_pending_drain` behavior;
- runtime image digest verification, in-use retention, update drain, and GC dry run;
- one node connection multiplexing workspace and compute endpoints without
  duplicate identity or heartbeat;
- host-wide atomic capacity and isolation across profiles connected to different
  PawFlow servers;
- drain-source precedence and refusal to remotely resume a local-policy drain;
- snapshot-plus-structured-IPC convergence without renderer log parsing;

### 25.5 Mount tests

- exact runtime session subtree only;
- UID/GID from `PAWFLOW_RUN_UID/GID`;
- token/config write-through;
- skills/attachments parity;
- batch workspace compatibility mount matrix;
- no workspace mount for interactive providers currently lacking it;
- same-host local bind;
- cross-relay FUSE;
- disconnect returns I/O error;
- symlink/traversal/rename escape rejection;
- no empty fallback.

### 25.6 MCP parity tests

Compare local and remote runs:

- identical tool catalog/schema;
- same workspace relay resolution;
- same approval/authorization;
- same tool result;
- same webchat tool events;
- runtime token cannot change identity;
- expired/revoked runtime loses access;
- malicious output cannot forge control frames.

### 25.7 Provider integration tests

For each CC/Codex/Gemini/AGY provider:

- cold batch turn;
- warm/reused turn where supported;
- tool call;
- large streamed answer;
- error;
- timeout;
- cancel;
- force-stop;
- context compaction;
- credential refresh;
- server vs exact endpoint parity.

Interactive tests also cover:

- tmux start/readiness;
- prompt paste;
- MITM/hook events;
- terminal open/close/reopen;
- browser tab reconnect;
- idle sweeper;
- endpoint disconnect/reconnect;
- PawFlow restart;
- supervisor restart;
- no duplicate live session.

### 25.8 Multi-VPS chaos tests

- two endpoints, simultaneous reservations;
- capacity heartbeat delayed/reordered;
- WS drop at every runtime protocol boundary;
- relay process restart;
- supervisor restart;
- Docker daemon restart;
- PawFlow restart;
- endpoint drain under load;
- disk full;
- image missing;
- FUSE unavailable;
- runtime gateway unavailable;
- credential slot contested across nodes;
- stale endpoint epoch;
- output flood;
- kill during backpressure.

### 25.9 Security tests

- operation-by-operation permission matrix tests cover admin, host operator,
  service editor, conversation user, auditor, machine credential, revoked
  principal, and cross-server principal;
- arbitrary image/flag/path/env rejected;
- raw Docker access unavailable;
- no public inbound listener exists in the supported deployment, and runtime
  namespaces cannot reach host gateway, other containers, private/link-local/
  metadata canaries, PawFlow control endpoints, or provider/token endpoints
  directly;
- provider gateway allowlists reject IP literals, alternate ports, CONNECT,
  ambiguous URLs, private DNS results, DNS rebinding, and disallowed redirects
  while valid streaming/OAuth/MITM flows stay observable and fenceable;
- runtime containers prove non-privileged namespaces, capability drop,
  `no-new-privileges`, seccomp/LSM, read-only root, exact bounded mounts, and no
  Docker socket/device/supervisor/secure-store access;
- local manager IPC rejects wrong OS peers, renderer/origin, unknown fields,
  oversized/rate-limited calls, arbitrary methods/paths, and secure-store reads;
- cross-user/session/runtime mounts rejected;
- forged runtime/lease/epoch rejected;
- replayed operation handled idempotently;
- bundle signature/digest mismatch rejected;
- structural launch validation proves the CA private key/path/parent/artifact never
  crosses either the local or remote runtime boundary, while only the required CA
  certificate and runtime-scoped leaf material do;
- force-stop during an endpoint partition fences broker/MCP access and releases the
  logical credential slot within 2 seconds, so the next loop can acquire a new
  epoch before physical container cleanup;
- broad label cleanup rejected;
- revoked endpoint cannot renew/control;
- secrets absent from logs/frames/UI/audit;
- malicious compute relay cannot assert a principal;
- two PawFlow server profiles on one host cannot inspect, signal, network-reach,
  reconcile, or reap each other's runtimes/journals;
- package/repository/bundle/image signature, digest, provenance, architecture,
  revoked-key, downgrade, archive traversal, staged update, and rollback failures
  all fail closed before execution;
- durable audit survives PawFlow restart, preserves UUID/order/integrity and
  authorization filtering, and excludes every secret/content canary;
- simulated audit-backend failure blocks new remote enrollment/mutations/launches,
  while revoke/fence/force-stop execute through the bounded recovery journal and
  remote operation remains disabled until it flushes;
- quota/cost/output/disk/bandwidth floods remain bounded, preserve control-channel
  priority, and do not reveal another principal's utilization.

### 25.10 Performance gates

Measure against the explicit network budgets in section 16.3 and establish
baseline-specific thresholds for:

- scheduling/reservation latency;
- cold/warm launch latency;
- MCP round-trip overhead through runtime gateway;
- first-token/event latency;
- stream throughput;
- force-stop latency;
- heartbeat overhead;
- maximum stable runtimes per endpoint;
- PawFlow server CPU/memory with 1, 10, and 50 remote runtimes.

The remote path must actually reduce server Docker CPU/memory pressure. A result
that merely moves containers while saturating PawFlow with unbounded relay frame
processing does not meet the goal.

### 25.11 PawFlow configuration and UI tests

- existing CLI services migrate to explicit server placement while API-only
  services remain unchanged;
- CLI-provider rules show the placement adapter and API-provider rules hide it;
- install/edit/read-only/copy/scope-move/import serialize the same validated
  `cli_execution` object;
- provider change requires confirmation and never guesses a target;
- the concrete provider-to-family/capability table includes Gemini batch and is
  identical in schema helpers, compatibility listing, and scheduling;
- known local image names migrate to approved image policy IDs, while an unknown
  custom tag is rejected for remote placement until explicitly verified;
- typed CPU/memory requests and requested-versus-effective limits match server,
  pool, and relay policy intersection;
- exact endpoint and pool selectors contain only principal-authorized compatible
  entries and preserve an unavailable configured UUID;
- save-time and reservation-time checks reject stale ACL, policy, capability,
  image, provider, and scope assumptions;
- global service use does not grant endpoint access to the caller;
- endpoint/pool/server-target CRUD, revision conflicts, dependency previews, and
  destructive confirmations are permission-tested;
- kill-switch and emergency mode retain configured placement, expose effective
  placement separately, audit actor/reason/UUID/timestamp, and render the sticky
  global banner;
- SSE and snapshot paths converge for endpoint, pool, queue, runtime, drain, and
  reconciliation state without heartbeat-triggered full resource reloads;
- queued state survives browser reconnect and cancels on force-stop or generation
  change;
- exact-endpoint and pool queues apply the documented server/service/pool timeout
  and depth ceilings, including the bounded meaning of timeout zero;
- UI payloads and errors do not leak unauthorized endpoint existence, IPs, raw
  host paths, Docker IDs, prompts, output, or secrets;
- keyboard/focus, color-independent status, responsive layout, and i18n checks
  cover every new form, dialog, badge, and banner.

### 25.12 User-journey and operations tests

- execute all ten section 8.13 scenarios as documented smoke tests, including the
  local-only upgrade path and separate workspace/compute relays;
- a new operator can go from a supported clean VPS to an available endpoint and a
  successful test placement using only the PawFlow UI plus documented CLI prompts,
  without entering JSON, UUIDs, Docker commands, provider credentials, or opening
  an inbound firewall port;
- every mandatory doctor failure identifies the failed check, stable code, trust
  impact, and one redacted remediation; retry resumes rather than duplicates;
- signed update preflight, one-at-a-time drain/update/resume, retained rollback,
  backup/restore with re-enrollment, graceful decommission, and lost-node revoke
  all pass with server/local state convergence;
- uninstall/profile deletion/endpoint deletion/revocation remain distinct, show
  dependency previews, and never invoke broad Docker cleanup;
- usability testing records completion rate, median setup time, number of manual
  values entered, failed-step recovery, and accessibility results for both the
  headless and Desktop paths before C11 production rollout.

## 26. Implementation phases and gates

### WP-A — Fail-closed immutable relay ownership prerequisite

Implement the immutable-owner mitigation from
`REMOTE_RELAY_ENROLLMENT_SHARING_PLAN.md` before a compute endpoint is accepted:

- bind the authenticated relay connection to one immutable principal/owner;
- remove lazy or mutable `RelayService._user_id` authority from inverse views;
- fail closed when the owner binding is missing or changes;
- cover reconnect, endpoint registration, and inverse-view authorization.

Gate: the enrollment plan's WP-A tests pass. C0 may run in parallel, but C1 cannot
accept a compute endpoint and no session/skill/workspace inverse view may ship
without this gate.

### C0 — Characterize and freeze contracts

- build provider/mount/lifecycle matrix tests;
- replace the section 24.4 snapshot with a machine-readable in/out call graph and
  enumerate every direct CLI Docker consumer;
- define schemas, errors, IDs, and metrics;
- record local and reference-relay RTT/throughput baselines;
- confirm runtime session/FUSE host-propagation mechanism;
- recalibrate the section 24.6 size/effort ranges from measured inventory.

Gate: characterization suite passes on the current local implementation, the
source allowlist is reviewed, and revised estimates are published. No calendar
commitment for C5 or later is made before this gate.

### C1 — Headless/Desktop compute profile and enrollment

- add versioned compute profile schema/migration, manager, headless bootstrap CLI,
  and Desktop GUI over the same operations;
- Docker doctor;
- compute endpoint registration/capability heartbeat;
- start/stop/drain/resume/status;
- signed package/install documentation, OS-service lifecycle, short-lived
  single-use stdin enrollment, pending approval, and idempotent setup recovery;
- establish immutable profile IDs, secure-store references, dependency-safe
  server/profile deletion, node connection multiplexing, and background-service
  lifecycle without runtime launch capability;
- expose approved CLI image/bundle readiness separately from relay worker images;
- no runtime launches yet.

Gate: a clean supported compute-only VPS can be installed and registered through
the documented no-inbound-port headless golden path, or equivalently through
Desktop, and reports trusted capacity without a workspace path. The enrollment
secret is absent from argv/history/profile/logs. It cannot launch a runtime.

### C2 — Runtime abstraction with local parity

- implement types/router/local backend;
- migrate Codex image generation to handles and job-scoped artifacts;
- retain local Docker behavior byte-for-byte;
- remove direct Docker control from that image-job path.

Gate: Codex image generation passes its characterization, artifact, credential,
and lifecycle suites through `LocalDockerRuntime`.

### C3 — Remote supervisor, leases, and runtime protocol

- host ComputeSupervisor;
- typed relay protocol;
- policy/capacity/reservation;
- labels/journal/reaper/reconciliation;
- signed runtime bundle;
- durable append-only compute audit backend, audit-degraded gate, and bounded
  safety-action recovery journal;
- OS-managed supervisor lifecycle, policy compare-and-set, pending-drain state,
  and safe image/bundle pull/update/GC.

Gate: fake and real relay runtime contract suites pass without provider traffic.

### R1 — Restart cleanup gate

This is a new feature gate, not a C0 parity claim:

- PawFlow restart advances boot/credential epochs and invalidates old broker access;
- local and remote old-epoch runtimes are terminated, not adopted;
- supervisor restart rebuilds journal/label state and reconnects valid same-epoch
  runtimes;
- the next PawFlow loop starts cold without waiting for physical cleanup;
- no test promises live tmux/process survival across a PawFlow restart.

Gate: the section 21.4/21.5 restart-cleanup suite passes before the first remote
provider runtime ships.

### C4 — Remote Codex image-job vertical slice

- exact-endpoint remote Codex image generation;
- job-scoped input/output artifact transfer;
- credential/egress broker and network fencing;
- default-deny runtime networking, provider allowlists with SSRF/DNS/redirect
  validation, hardened local IPC/container profile, and anti-bypass probes;
- image/runtime-bundle cache, streaming, force-stop, and cleanup;
- PawFlow admin endpoint/server-target status plus exact-endpoint placement editor,
  compatibility helper, test-placement action, and placement status events;
- no conversation workspace or general session FUSE dependency.

Gate: local and remote Codex image jobs pass the same lifecycle, artifact,
credential, timing, chaos, and security suites on one VPS. This is the first
user-visible remote-execution milestone.

### C5 — Runtime-scoped session/MCP views and remote Codex app-server

- session/skill/FileStore views;
- mount propagation;
- runtime gateway/token;
- normal PawFlow tool routing;
- security and disconnect behavior;
- remote Codex batch/app-server, streaming, reuse, token recovery, and force-stop;
- exact-endpoint compatibility workspace-mount parity where configured;
- webchat queue/runtime/degraded/reconciliation snapshot and SSE integration.

Gate: a Codex app-server on a compute VPS reads/writes only its authorized session
and skill views, executes MCP tools against an independently placed workspace
relay, and passes local/remote parity, network-budget, chaos, and security suites.
Cross-relay compatibility mounts cannot pass this gate until the routed FUSE work
in `RELAY_WORKSPACE_FS_PLAN.md` has production security, correctness, and
performance gates; its current “performance non-critical” premise is insufficient
for this path.

### C6 — Multi-endpoint scheduler

- revisioned, scoped ComputePool model/API/admin UI with explicit label-membership
  semantics, reference checks, and minimum-capacity upgrade preflight;
- scoring;
- atomic reservation;
- bounded queue/fairness;
- drain-aware selection;
- metrics and external autoscaler signals.

Gate: concurrent load spreads across at least two VPS without duplicate runtimes
or implicit server execution.

### C7 — Remaining batch providers

- Claude Code batch;
- Gemini batch/app;
- auxiliary pool consumers.

Gate: all batch providers pass the shared local/remote contract and no provider
contains direct Docker control.

### C8 — Codex interactive

- remote tmux/MITM/hooks;
- live registry handles;
- terminal proxy;
- reconnect, supervisor-restart reconciliation, and token recovery.

Gate: a long-lived Codex session survives browser and supervisor/relay reconnect,
while PawFlow restart performs the R1 cold cleanup path, without duplicate
execution.

### C9 — Claude Code interactive

- migrate CCI lifecycle and prompt injection;
- preserve MITM traffic/webchat invariants;
- compaction and credential exclusivity.

Gate: CCI parity and all force-stop/reconnect tests pass.

### C10 — Antigravity interactive

- migrate AGY observer/proxy/tmux;
- preserve manual ingest and event normalization.

Gate: AGY parity and provider-specific security tests pass.

### C11 — One-shot cleanup and production rollout

- delete remaining CLI direct-Docker paths;
- remove temporary protocol/schema versions;
- finish admin/webchat/Relay Desktop UX, i18n, accessibility, responsive behavior,
  destructive confirmations, and non-leaking diagnostics;
- document headless/Desktop deployment, host hardening, trust, firewall/egress,
  upgrade/rollback, backup/restore, incident revoke, and decommission;
- run all ten user scenarios and measured setup-usability gates;
- staged canary across one, then several VPS;
- final adversarial review.

Gate: source checks, full unit/integration/chaos suite, manual multi-VPS smoke, and
security review pass.

## 27. Dependency graph and parallel work

~~~text
WP-A enrollment identity ──> C1 capacity-only enrollment
C0 contracts ──> C2 local image-job abstraction ──┐
C0 contracts ──> C3 supervisor/protocol ──────────┼──> C4 remote image job
C3 ──> R1 restart cleanup gate ───────────────────┘

C4 + WP-A ──> C5 session/MCP + Codex app-server
RELAY_WORKSPACE_FS_PLAN routed-FUSE gates ────────> C5 cross-relay compatibility mounts

C5 ──> C6 multi-endpoint scheduler
C5 ──> C7 remaining batch providers

C5 + C6 ──> C8 Codex interactive ──> C9 CCI ──> C10 AGY
C7 + C8 + C9 + C10 ──> C11 cleanup/rollout
~~~

C0, WP-A, and preparatory C1 work may proceed in parallel; C2 and most of C3 may
then proceed in parallel. Remote conversation/resource storage is not on the
critical path, but immutable relay ownership is a hard prerequisite and routed
workspace FUSE is a hard prerequisite for C5 cross-relay compatibility mounts.

## 28. Documentation required with implementation

Update in the same changes:

- `docs/relay_client.md`;
- `docs/docker.md`;
- `docs/security_model.md`;
- `docs/AGENT_SYSTEM.md`;
- provider/service reference docs;
- Relay Desktop README;
- deployment and troubleshooting documentation;
- protocol reference and operator runbook.

Document:

- how to add a compute VPS from Relay Desktop;
- the primary headless quickstart from a clean supported VPS, including signed
  package verification, no inbound ports, stdin enrollment, presets, approval,
  test placement, and stable-error troubleshooting;
- trust implications;
- server vs endpoint vs pool selection;
- drain/reconcile workflows;
- capacity sizing;
- approved image installation/pull;
- common Docker/FUSE/network failures;
- metrics and autoscaler integration;
- incident cleanup without broad container deletion;
- least-privilege host/container/IPC hardening and Docker root-equivalent warning;
- provider egress allowlists, SSRF/metadata protections, and firewall validation;
- upgrade, rollback, profile backup/restore, lost-node revoke, and complete
  decommission in the required safe order;
- durable audit retention/export/recovery and audit-backend outage behavior;
- all ten supported usage scenarios and the capability limits at each stop point.

## 29. Acceptance criteria

The feature is complete when:

1. The headless CLI or Relay Desktop can create the same compute-only relay profile
   without a workspace.
2. The compute relay enrolls through PawFlow's relay system and publishes accepted
   compute capabilities/capacity.
3. A CLI service can explicitly select server Docker, one compute endpoint, or a
   compute pool.
4. One or more VPS can execute CLI containers while PawFlow remains the control
   plane.
5. Workspace tools still resolve through MCP PawFlow to the same workspace relays.
6. Provider-specific compatibility mounts match current local behavior.
7. Local and remote backends pass the same runtime/provider contract suites.
8. Batch Claude Code, Codex, and Gemini work remotely.
9. Interactive Claude Code, Codex, and Antigravity work remotely with tmux,
   MITM/hooks, terminal reconnect, reuse, compaction, and token recovery.
10. Every visible response/tool/event reaches the webchat.
11. No target failure silently launches on the PawFlow server.
12. Multiple compute relays share load through deterministic capacity-aware
    scheduling.
13. Drain removes an endpoint from new placement without killing existing work.
14. Unknown outcomes reconcile without duplicate containers, turns, or credential
    use.
15. Supervisor restart reconciles valid same-epoch live runtimes; PawFlow restart
    fences and terminates old-epoch runtimes and starts the next loop cold.
16. Force-stop is immediate, non-error, and cannot affect the next loop.
17. A compute endpoint cannot access another runtime's session, credentials, or
    identity.
18. The server never sends raw Docker flags, paths, or an unrestricted socket.
19. Direct CLI-provider Docker control is removed after migration.
20. Server Docker CPU/memory pressure measurably falls when workloads target the
    remote pool.
21. Full unit, integration, chaos, security, UI, and manual multi-VPS smoke gates
    pass.
22. Relay Desktop and its CLI share one versioned compute-profile store, immutable
    profile IDs, secure secret references, background-service lifecycle, and safe
    quit/delete semantics.
23. CLI runtime images and bundles have digest verification, drain-before-update,
    in-use retention, disk-pressure reporting, and previewed garbage collection
    distinct from relay worker image management.
24. PawFlow's actual Services editor provides a typed placement control for every
    install/edit/view/copy/scope/import path; users never need to edit placement
    JSON manually.
25. Compute endpoint, pool, server-target, queue/runtime, kill-switch, and emergency
    controls are available through authorized PawFlow admin surfaces with revision
    checks, dependency previews, audit, snapshot restore, and scoped live events.
26. Pool membership, ownership, minimum capacity, rename, deletion, and service
    reference semantics are unambiguous and tested without name-based rebinding.
27. Existing CLI services migrate explicitly to server placement, service/provider
    scope changes revalidate authorization, and global services never grant target
    access.
28. Both UIs distinguish configured, resolved, effective, offline, revoked,
    quarantined, draining, update-required, and reconciliation states without
    exposing infrastructure secrets.
29. Multiple PawFlow server profiles on one compute host share one atomic host
    capacity ledger and remain mutually isolated for status, control, logs,
    reconciliation, and cleanup.
30. A new user can complete the documented clean-VPS headless flow without inbound
    ports, JSON, UUID entry, Docker commands, shell-visible secrets, or provider
    credentials on the VPS; Desktop produces the same state.
31. All ten usage scenarios in section 8.13 pass as product-level smoke tests with
    the documented visible result and guardrail.
32. Runtime egress is default-deny and anti-bypass: direct provider/token, host,
    private/link-local/metadata, peer-container, Docker, and PawFlow-control access
    is blocked while allowlisted broker/MCP traffic remains visible and fenceable.
33. The explicit actor matrix is enforced for every operation and a compute relay,
    host operator, global service, or endpoint creator cannot manufacture human or
    cross-server authority.
34. Compute audit is durable, append-only, restart-safe, integrity-evident,
    redacted, and authorization-filtered; audit outage blocks new risky work but
    never blocks revoke/fence/cancel/force-stop safety actions.
35. Signed package/bundle/image verification, hardened container/local IPC,
    staged update and compatible rollback, backup/restore re-enrollment, and
    ordered incident/decommission flows pass adversarial and operator tests.

## 30. Recommended first commitment

The first product commitment is **WP-A + C1 only**: a compute-only VPS can be
configured through the primary headless bootstrap or Relay Desktop, passes doctor,
enrolls with immutable ownership, and publishes trusted capacity, but cannot launch
a runtime. It is independently
demonstrable and releasable without pretending remote execution is ready. C0 runs
alongside it as required engineering evidence and gates later implementation, but
does not inflate this user-visible commitment.

The second commitment is C2–C4: exact-endpoint Codex image generation on one VPS.
It proves transport, leases, process control, artifact transfer, the credential
broker, fencing, and cleanup without depending on session/workspace FUSE. Do not
date C5 or later until C0 inventory and C4 measured velocity have been reviewed.

### 30.1 Deliverable stop points

| Stop point | User-visible result | Deliberately still missing |
|---|---|---|
| WP-A + C1 (with C0 evidence produced in parallel) | Headless CLI or Relay Desktop enrolls/diagnoses a compute-only VPS and shows trusted capacity; no launch button/path is enabled | all remote CLI execution |
| C2–C4 | Codex image generation runs on one exact VPS with bounded artifacts, brokered credentials, force-stop, and no silent fallback | general session/FUSE, MCP tools from remote CLI, app-server, pools |
| C5 | Codex app-server/batch runs on one VPS with session/skill views, MCP routing, and compatibility mounts | multi-VPS scheduling and other providers |
| C6 | named pools distribute new work across multiple VPS and expose queue/drain controls | non-Codex batch and interactive providers |
| C7 | all batch CLI providers and auxiliary consumers use the shared remote runtime | interactive providers |
| C8 | Codex interactive works remotely with tmux/MITM/terminal and documented restart semantics | Claude Code and AGY interactive |
| C9 | Claude Code interactive reaches remote parity, including webchat traffic and force-stop invariants | AGY interactive and final rollout cleanup |
| C10 | all three interactive families work remotely | one-shot cleanup, full rollout hardening |
| C11 | legacy CLI Docker paths are removed and production rollout/security gates pass | no planned feature gap in this document's scope |

Every row is an honest stopping point: shipped UI and documentation must state the
remaining limitations, and no later capability is implied by an earlier gate.

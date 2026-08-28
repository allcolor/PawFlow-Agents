# Named Relay Execution and Deployment Profiles Implementation Plan

Status: G6 plan complete; implementation has not started.

## 1. Executive decision

PawFlow already has the correct run-plane abstraction: once a relay is connected,
filesystem and tool operations use `RelayService` and do not need to know where the
relay runs.

G6 must add the missing control plane around that relay. It is not only a
configuration picker.

The current code proves both halves of that conclusion:

- `services/filesystem_service.py` and `services/_filesystem_ops.py` provide one
  reverse-WebSocket service for filesystem, shell, HTTP, Git, ScratchDir, desktop,
  automation, and other relay capabilities;
- `core/server_relay_manager.py` owns server-side Docker relay lifecycle;
- `pawflow_relay/manager.py` and Relay Desktop own standalone workspace profiles,
  processes, and local Docker containers;
- `pawflow_installer/transports/ssh.py` can install PawFlow through SSH, but SSH is
  not a runtime deployment provider;
- `config/relay_image_catalog.json` already defines relay image feature profiles;
- `pawflow_relay/thread.py` selects Docker when an image is present, while its
  native `_run_native_relay()` path is currently only a stub;
- the relay registration currently advertises platform/flags and
  `scratchdir_v1`, but there is no versioned deployment contract, persistent
  instance state, provider reconciliation, hibernation, infrastructure cost, or
  deterministic cross-provider teardown.

Therefore:

1. keep `RelayService` authoritative for all execution;
2. add a versioned deployment-provider contract that only creates and manages
   relay environments;
3. resolve a selected deployment profile to one healthy `relay_id` before a flow
   or agent tool begins;
4. never add Docker-, SSH-, Kubernetes-, HPC-, or cloud-specific branches to
   filesystem/tool handlers.

## 2. Objective

Expose understandable named profiles for:

- local native execution;
- Docker execution on the PawFlow server or an enrolled Relay Desktop host;
- an SSH-managed remote host;
- Kubernetes pod and job execution;
- HPC execution through Apptainer/Singularity and an explicit scheduler;
- one qualified scale-to-zero provider after a real-demand and persistence
  evaluation.

Selecting a shipped profile must produce or reuse a healthy relay with declared
capabilities, an explicit workspace/persistence policy, visible trust and cost
metadata, and deterministic cleanup.

## 3. User outcomes

A user or administrator can:

1. create a named profile once and select it by name later;
2. see where execution will occur before confirming;
3. see the exact workspace persistence and destroy behavior;
4. choose required relay capabilities explicitly;
5. choose CPU, memory, disk, GPU, network, lifetime, and budget values explicitly;
6. validate a profile without creating infrastructure;
7. create a relay asynchronously and watch lifecycle progress;
8. reuse, reconnect, hibernate, resume, or destroy an instance when supported;
9. bind the resulting relay to a conversation, agent, flow, or exact task;
10. see observed capabilities and health rather than trusting configuration alone;
11. see infrastructure cost as known, estimated, operator-supplied, or unavailable;
12. audit who requested and completed every create/destroy action;
13. recover safely after a server restart or interrupted provider call;
14. prove that teardown removed every provider-owned resource.

## 4. Non-goals

G6 does not:

- replace `RelayService` or its reverse-WebSocket protocol with provider SDK calls;
- execute user shell commands through SSH, Kubernetes exec, Docker exec, or a
  cloud SDK after bootstrap;
- add backend conditionals to individual tools;
- copy Hermes' terminal-backend architecture;
- create a second workflow engine or scheduler;
- make `local=true` mean “select the local deployment profile”;
- infer a provider, credential, workspace, network, quota, or persistence policy;
- silently grant host-local execution, desktop control, service tunnels, or broad
  egress;
- silently expose an inbound public port;
- copy a workspace between providers without an explicit source and destination
  policy;
- claim support for Modal, Daytona, or Vercel Sandbox before live qualification;
- add an implicit idle timeout, maximum lifetime, retry count, budget, or task
  limit;
- migrate a running process between providers;
- support every HPC scheduler in the first implementation.

## 5. Non-negotiable invariants

1. Tools receive an exact `relay_id` and stay provider-agnostic.
2. A deployment provider manages infrastructure only, never user tool semantics.
3. Every profile and instance has a UUID and UTC creation timestamp.
4. Every lifecycle event has a UUID, UTC timestamp, actor, operation id, and
   redacted before/after evidence.
5. Missing required parameters raise `ValueError`; no anonymous/default provider
   fallback exists.
6. Profile revisions are immutable after use. Editing creates a new revision.
7. An instance records the exact profile revision and image digest it used.
8. Lifecycle mutations require an idempotency key and expected generation.
9. A provider credential is resolved just in time and never copied into a profile,
   instance, event, log, command line, or relay environment.
10. A relay bootstrap credential is short-lived, single-use, and rotated after a
    successful connection.
11. Declared capabilities never imply observed capabilities.
12. A deployment is healthy only when its relay is connected and all required
    capabilities plus the workspace probe are verified.
13. Destroy never reports success until provider discovery proves that owned
    resources are gone or returns a visible leak receipt.
14. Hibernation is allowed only with no active execution lease.
15. Force destroy is explicit, separately authorized, and immediately terminates
    active work.
16. `local=true` remains a separate per-request trust-boundary switch.
17. Infrastructure cost does not contaminate LLM usage accounting.
18. Old and new lifecycle engines do not run in parallel after migration.

## 6. Architecture boundary

~~~text
Authenticated UI / Flow runtime
             |
             v
RelayDeploymentCoordinator
  | profile revision | operation | lease | audit | reconciliation
             |
             v
RelayDeploymentProvider v1
  | create/start/inspect/hibernate/resume/destroy
             |
             v
Local / Docker / SSH / Kubernetes / HPC / selected cloud environment
             |
             | outbound authenticated WebSocket
             v
RelayService vNext
             |
             | exact observed capabilities and relay_id
             v
Filesystem, shell, browser, desktop, MCP, HTTP, Git and other tools
~~~

The provider disappears from the run path after the relay connects. A task that
uses a Kubernetes profile and a task that uses an SSH profile invoke the same
relay methods.

## 7. Terminology

- **Provider type:** implementation such as `docker`, `ssh`, or `kubernetes`.
- **Provider binding:** opaque reference to provider credentials/configuration.
- **Profile:** named, versioned, scoped desired configuration.
- **Profile revision:** immutable snapshot used by an instance.
- **Deployment:** PawFlow control-plane identity for one environment.
- **Provider instance:** provider-native object such as a container, pod, job,
  allocation, or sandbox.
- **Relay instance:** reverse-connected runtime inside that environment.
- **Supervisor:** enrolled Relay Desktop/CLI component allowed to create local or
  Docker instances on its own host.
- **Lease:** proof that a flow/task/conversation currently uses a deployment.
- **Workspace policy:** mapping and persistence semantics for `/workspace`.
- **Teardown receipt:** provider discovery evidence after destroy.

## 8. Versioned profile contract

Create `RelayDeploymentProfileV1` as a strict model.

Every field below is required. An explicitly stored `null` means “not configured”
only where the schema permits it; omission is invalid.

~~~json
{
  "schema_version": 1,
  "profile_id": "UUID",
  "revision": 1,
  "created_at": "UTC timestamp",
  "created_by": "authenticated user id",
  "scope": "global|user|conversation",
  "scope_id": "exact scope id",
  "name": "Human-readable unique name inside scope",
  "description": "Operator description",
  "provider": {
    "type": "local|docker|ssh|kubernetes|hpc_apptainer|qualified_cloud",
    "binding_id": "opaque provider/service binding UUID or null",
    "placement": "server|supervisor UUID|provider-specific placement id"
  },
  "relay": {
    "image": "registry/repository@sha256:digest or null for native",
    "required_capabilities": ["filesystem.read", "shell.exec"],
    "optional_capabilities": ["desktop.control"],
    "access_mode": "readonly|readwrite"
  },
  "workspace": {
    "kind": "host_path|named_volume|persistent_volume|shared_path|provider_snapshot|ephemeral",
    "source": "validated provider-specific reference or null",
    "mount_path": "/workspace",
    "persistence": "ephemeral|retained|external",
    "retain_on_destroy": true
  },
  "resources": {
    "cpu": "explicit provider-neutral quantity or null",
    "memory_bytes": 8589934592,
    "disk_bytes": 53687091200,
    "gpu": "explicit request or null",
    "process_limit": 512
  },
  "network": {
    "egress_policy_id": "UUID",
    "public_ingress": false,
    "tls_policy_id": "UUID or null"
  },
  "lifecycle": {
    "reuse_scope": "none|conversation|user",
    "idle_action": "keep|hibernate|destroy",
    "idle_after_seconds": null,
    "max_lifetime_seconds": null
  },
  "cost": {
    "reporting_currency": "USD",
    "budget_amount": null,
    "budget_period": null,
    "operator_hourly_estimate": null
  }
}
~~~

Validation rules:

- `name` is unique per scope but never used as provider identity;
- `binding_id` is mandatory for providers that need credentials;
- images for container providers use immutable digests, never a mutable tag;
- `public_ingress` is false for v1 because relays reverse-connect;
- automatic idle action requires an explicit positive `idle_after_seconds`;
- a budget requires both amount and period;
- `retain_on_destroy` must agree with the workspace persistence kind;
- provider-specific configuration is validated by a typed provider schema and is
  stored under a versioned `provider.options` object;
- secrets are rejected from every profile field by name and value policy;
- a profile cannot request capabilities the provider declares impossible.

Suggested templates may prefill a draft, but saving materializes every value. The
template is not a runtime fallback.

## 9. Deployment instance contract

Create `RelayDeploymentV1` as durable control-plane state.

~~~json
{
  "schema_version": 1,
  "deployment_id": "UUID",
  "created_at": "UTC timestamp",
  "updated_at": "UTC timestamp",
  "owner_user_id": "authenticated user id",
  "conversation_id": "UUID or null",
  "profile_id": "UUID",
  "profile_revision": 1,
  "profile_digest": "sha256",
  "provider_type": "docker",
  "provider_instance_ref": "opaque non-secret provider id or null",
  "relay_id": "exact service id",
  "state": "requested",
  "generation": 0,
  "desired_state": "healthy",
  "workspace_ref": "opaque non-secret reference or null",
  "declared_capabilities": [],
  "observed_capabilities": [],
  "effective_capabilities": [],
  "last_health": null,
  "last_cost": null,
  "last_error": null
}
~~~

The store also records:

- immutable lifecycle events;
- idempotent operations;
- active leases;
- provider discovery/teardown receipts;
- redacted health and cost snapshots.

Use a transactional store with `BEGIN IMMEDIATE`, generation fencing, and unique
constraints for operation idempotency. Do not store provider credentials, relay
tokens, environment variables, Kubernetes Secret data, SSH private-key paths, or
raw provider responses.

## 10. Lifecycle state machine

Canonical states:

~~~text
requested
  -> validating
  -> provisioning
  -> bootstrapping
  -> connecting
  -> healthy
       -> reconnecting -> healthy
       -> hibernating -> hibernated -> resuming -> connecting -> healthy
       -> destroying -> destroyed
  -> failed
failed -> reconciling -> provisioning|connecting|healthy|destroying|destroyed|failed
~~~

Rules:

- only the coordinator changes canonical state;
- providers return observations, never mutate the store directly;
- terminal `destroyed` is immutable;
- `failed` retains provider references so reconciliation/cleanup can continue;
- interrupted create is not retried blindly: provider discovery first determines
  whether an owned instance already exists;
- one lifecycle mutation is active per deployment generation;
- retries reuse the same provider idempotency/ownership identifiers;
- normal destroy refuses active leases;
- force destroy appends its audit event before interrupting work;
- no automatic hibernate/destroy occurs unless explicitly configured.

## 11. Provider contract v1

Add a small provider-neutral interface. Exact types may be dataclasses or strict
models, but every provider implements the same behavior.

~~~python
class RelayDeploymentProviderV1(Protocol):
    provider_type: str
    contract_version: int

    def schema(self) -> ProviderSchema: ...
    def capabilities(self) -> ProviderCapabilities: ...
    def validate(self, profile_revision) -> ValidationReport: ...
    def plan(self, profile_revision, deployment) -> RedactedProviderPlan: ...
    def preflight(self, context) -> PreflightReport: ...
    def discover(self, ownership) -> list[ProviderObservation]: ...
    def create(self, request) -> ProviderObservation: ...
    def inspect(self, request) -> ProviderObservation: ...
    def start(self, request) -> ProviderObservation: ...
    def hibernate(self, request) -> ProviderObservation: ...
    def resume(self, request) -> ProviderObservation: ...
    def reconnect(self, request) -> ProviderObservation: ...
    def destroy(self, request) -> TeardownReceipt: ...
    def cost(self, request) -> CostObservation: ...
~~~

Required result properties:

- stable provider instance reference;
- ownership labels/tags;
- operation idempotency reference;
- observed lifecycle state;
- redacted diagnostics with stable error code and retry class;
- workspace mapping evidence;
- image/artifact digest;
- resource and network metadata;
- cost source and timestamp;
- teardown resource inventory.

Optional operations are declared in `ProviderCapabilities`. Calling an
unsupported operation returns a stable `unsupported_operation` result, not a
partial fallback.

No method above accepts a user shell command.

## 12. Coordinator and asynchronous operations

Create `RelayDeploymentCoordinator` as the only lifecycle entry point.

### 12.1 Create

1. authenticate the actor and authorize the profile scope;
2. load and freeze the exact profile revision;
3. validate capability, trust, workspace, quota, and credential bindings;
4. append `deployment.create.requested` before provider mutation;
5. create an idempotent operation and deployment generation transactionally;
6. return HTTP 202 / operation id immediately;
7. resolve provider credentials inside the background worker;
8. call `discover` before `create`;
9. create or adopt only an exactly owned instance;
10. mint a single-use relay bootstrap ticket;
11. inject that ticket through the provider's secret channel;
12. wait asynchronously for the exact relay registration;
13. validate profile digest, generation, workspace probe, and capabilities;
14. rotate/revoke the bootstrap credential;
15. mark the deployment healthy;
16. bind the exact `relay_id` only after health succeeds;
17. append a redacted completion or failure event.

### 12.2 Resolve for execution

A flow or agent may reference either:

- an exact existing `relay_id`; or
- an exact `deployment_profile_id` and optional revision.

For a profile reference, runtime resolution:

1. authorizes profile use;
2. finds or creates an instance according to explicit `reuse_scope`;
3. waits on the coordinator operation without blocking an HTTP worker;
4. acquires an execution lease;
5. returns an exact healthy `relay_id`;
6. injects only that id into the existing runtime context;
7. releases the lease when execution ends.

Tools never see the profile or provider.

### 12.3 Reconciliation

On startup and periodically:

- list nonterminal deployments;
- call provider `discover` using ownership labels;
- compare desired, stored, provider, and relay-connection state;
- repair missing store evidence when provider ownership is exact;
- reconnect or resume only when policy permits;
- never adopt an unlabelled/untagged resource;
- mark leaks visibly and keep retryable cleanup state;
- revoke stale bootstrap tickets and tokens;
- record every reconciliation decision.

## 13. Relay bootstrap and capability handshake

Extend the relay registration contract once; do not invent a separate handshake
per provider.

Required registration fields:

- relay protocol version;
- capability schema version;
- deployment id;
- profile id, revision, and digest;
- deployment generation;
- bootstrap ticket id;
- provider type;
- platform and architecture;
- containerized/native flag;
- image/artifact digest;
- workspace kind and fingerprint;
- exact observed capability ids.

The server:

- accepts one supported protocol version after the one-shot migration;
- rejects expired, reused, wrong-generation, or wrong-profile tickets;
- rejects a relay id already owned by another deployment;
- converts current boolean flags into canonical capability ids only during the
  migration, then deletes that compatibility path;
- requires `required_capabilities` to be a subset of observed capabilities;
- computes `effective_capabilities` as the authorized intersection;
- runs a read/write sentinel probe according to access mode;
- does not mark healthy from WebSocket presence alone.

Canonical capability ids should reuse the existing installer vocabulary:

- `filesystem.read`;
- `filesystem.write`;
- `shell.exec`;
- `container.exec`;
- `host.local`;
- `automation`;
- `desktop.control`;
- `service.tunnels`;
- `scratchdir.v1`.

Additional capabilities require an explicit schema revision.

## 14. Workspace and persistence contract

Every provider maps one declared workspace to `/workspace`.

Supported policies:

| Kind | Typical providers | Destroy behavior |
|---|---|---|
| `host_path` | local, Docker, SSH | external path retained |
| `named_volume` | Docker | retain or delete exactly as selected |
| `persistent_volume` | Kubernetes | PVC retain/delete policy explicit |
| `shared_path` | HPC | cluster-owned path retained |
| `provider_snapshot` | qualified cloud | snapshot id retained/deleted explicitly |
| `ephemeral` | Kubernetes job, cloud sandbox | always deleted |

Rules:

1. No provider silently substitutes a different persistence kind.
2. No ephemeral profile is marketed as resumable.
3. Hibernation requires retained workspace semantics.
4. Resume verifies a sentinel created before hibernation.
5. Workspace roots are canonicalized and escape-resistant.
6. Provider-native paths never leak into generic tool configuration.
7. Hydration from Git, FileStore, another relay, or an artifact is a separate
   explicit operation with authorization and digest evidence.
8. Destroying an instance does not delete an external workspace.
9. Deleting retained provider storage is a separate destructive action.
10. FileStore and session/skills mounts retain their existing authorization model.

## 15. Trust boundaries

| Profile | Primary boundary | Important residual risk |
|---|---|---|
| Local native | host user/process permissions | broad host access; weakest isolation |
| Docker | container plus host kernel | Docker daemon and required FUSE capabilities |
| SSH managed | remote OS account plus optional container | remote account/host compromise |
| Kubernetes | namespace, service account, pod security, network policy | cluster/kernel/admin boundary |
| HPC Apptainer | scheduler allocation, account, Apptainer policy | shared filesystem and cluster policy |
| Qualified cloud | provider project/account and sandbox isolation | provider control plane/data residency |

The UI shows this table's profile-specific boundary and residual risk before
creation. “Sandboxed” is never used without naming the enforcement layer.

## 16. Common security requirements

### 16.1 Identity and authorization

- authenticated actor identity comes from the request context;
- global profiles require admin ownership;
- user/conversation profiles require exact scope ownership;
- provider bindings cannot be referenced across unauthorized scopes;
- profile use, profile edit, lifecycle control, and credential administration are
  separate permissions;
- create/destroy requires idempotency and generation fencing;
- force destroy requires a separate explicit confirmation and authorization.

### 16.2 Credentials and secrets

- store only an opaque binding id in the profile;
- resolve credentials just in time in the control worker;
- use OS vault/existing sensitive-service mechanisms for long-lived credentials;
- use provider-native secret channels only for short-lived bootstrap material;
- never pass secrets in argv, labels, annotations, profile JSON, instance JSON,
  events, logs, exceptions, cost metadata, or diagnostic bundles;
- Kubernetes bootstrap Secrets are owner-labelled, minimal, short-lived, and
  deleted immediately after registration;
- SSH uses a configured identity binding; private key contents are never copied;
- cloud credentials must support least-privilege project/sandbox operations;
- disconnect/destroy revokes relay bootstrap and session credentials.

### 16.3 Supply chain

- container images use immutable digests;
- Apptainer SIF artifacts use a pinned digest and optional signature policy;
- generated relay artifacts have a manifest and SHA-256;
- provider SDK versions are pinned and vulnerability-scanned;
- profile revisions record every digest;
- no runtime downloads an unverified relay launcher.

### 16.4 Network

- relays require outbound access only to the PawFlow relay endpoint plus explicitly
  granted destinations;
- provider APIs are control-plane destinations, not relay egress;
- TLS certificate/CA policy is explicit;
- SSH host keys are strict or fingerprint-pinned; `accept-new` is not a silent
  runtime policy;
- Kubernetes NetworkPolicy and cloud egress policy are applied before bootstrap;
- public ingress remains disabled;
- service tunnels remain a separate opt-in capability.

### 16.5 Runtime isolation

- local native profiles warn that they are not isolation;
- Docker runtime does not mount `docker.sock` into the relay;
- Docker ownership labels are verified before removal;
- Kubernetes uses a dedicated namespace/service account policy and never
  cluster-admin;
- pod security, read-only root where possible, seccomp/AppArmor, capabilities,
  UID/GID, FUSE, and desktop requirements are explicit profile facts;
- HPC provider respects scheduler/site policy and never runs long work on a login
  node by default.

## 17. Cost, quota, and lifetime metadata

Infrastructure cost is a separate category from LLM/token usage.

`CostObservation` contains:

- status: `observed|estimated|operator_supplied|unavailable`;
- provider and account/project reference, redacted;
- region;
- currency;
- amount and unit interval;
- CPU, memory, GPU, disk, and network quantities when available;
- source and confidence;
- observed timestamp;
- provider billing/resource ids, hashed or redacted as required.

Rules:

- local, Docker, SSH, and HPC do not report zero unless zero is proven;
- “unavailable” is preferable to false precision;
- operator estimates are labelled, never presented as provider billing;
- cloud estimates and observed cost remain distinguishable;
- budgets and quotas act only when explicitly configured;
- budget enforcement is audited and fail-closed for new creates;
- an active workload is not destroyed on budget uncertainty;
- no implicit maximum lifetime or idle timeout is introduced.

## 18. Existing profiles to formalize

### 18.1 Local native profile

Current reality:

- Relay Desktop/CLI can own standalone workspace definitions;
- `RelayThread` can choose a native branch when no image is supplied;
- `_run_native_relay()` is currently a stub and the manager supplies a Docker
  image by default;
- `local=true` currently means forwarding an individual action to the host helper,
  not selecting a native profile.

Required work:

1. complete a real native relay launcher under Relay Desktop/CLI supervision;
2. keep it reverse-connected through `RelayService`;
3. require explicit host path, access mode, and capabilities;
4. expose a supervisor capability that can start/stop only its owned profiles;
5. keep host-local execution semantics separate from profile selection;
6. support OS-native process ownership, autostart opt-in, reconnect, and cleanup;
7. surface the full-host trust warning;
8. prove process-tree termination and no orphan service registration.

A server cannot remotely start an offline personal machine. Profile creation
therefore fails with `supervisor_offline` unless an enrolled supervisor is
connected; it never pretends success.

### 18.2 Docker profile

Current reality:

- `ServerRelayManager` already spawns, ensures, recreates, stops, and destroys
  server-side Docker relays;
- Relay Desktop already starts client Docker relays with CPU/memory limits,
  workspace mounts, home volume, FUSE mounts, and capability flags;
- server and client ownership/state models are separate.

Required work:

1. implement one `docker` provider with placement `server` or exact supervisor id;
2. adapt existing lifecycle code rather than rewrite container launch;
3. use deterministic deployment ownership labels and generation;
4. require image digest, resource limits, network policy, workspace kind, and
   retention policy;
5. reconcile by labels before create/remove;
6. preserve existing scoped workspaces and home volumes during migration;
7. return a teardown receipt listing container, volume, network, secret file, and
   service registration cleanup;
8. retain the existing rule that unlabelled or foreign containers are never
   removed.

### 18.3 SSH-managed profile

Current reality:

- the universal installer has argv-safe local/SSH transports and explicit host-key
  policy;
- that SSH path installs PawFlow; it is not a persistent execution backend;
- a manually installed remote Relay Desktop/CLI relay can already reverse-connect.

Required work:

1. reuse the installer SSH transport primitives for lifecycle control only;
2. preflight OS, architecture, relay artifact digest, workspace, outbound TLS,
   container/native mode, and service manager;
3. install a minimal supervisor/service on the remote host;
4. create a native or Docker relay there with a single-use bootstrap ticket;
5. leave SSH out of the tool execution path after connection;
6. use a durable remote service so loss of the SSH control connection does not
   kill the relay;
7. verify the exact host key on every lifecycle operation;
8. support reconnect, upgrade, and deterministic uninstall;
9. retain or delete the remote workspace only according to policy;
10. prove no remote process, unit, container, bootstrap file, or token remains.

The first implementation should target Linux systemd. macOS launchd and Windows
service support become separate provider capability increments, not silent
fallbacks.

## 19. Kubernetes provider

Provide two explicit modes.

### 19.1 Interactive pod

Use for a conversation/user-scoped retained workspace:

- one pod or small Deployment managed by the provider;
- PVC or explicit external volume;
- outbound relay WebSocket;
- reconnect after pod restart;
- explicit hibernate behavior supported only when storage and controller semantics
  preserve the workspace;
- optional desktop only when the image and policy explicitly support it.

### 19.2 Per-run job

Use for a finite WorkflowRun:

- one Kubernetes Job associated with one deployment lease;
- `emptyDir`, PVC, or explicit hydration source;
- relay starts as the job's primary runtime or a tightly owned sidecar;
- run completion requests graceful relay shutdown;
- Job success requires both workflow outcome and clean relay/provider teardown;
- a reconnecting pod cannot create a second active generation.

Provider requirements:

1. use the Kubernetes API, not `kubectl` string commands;
2. exact namespace and service-account binding;
3. no cluster-admin requirement;
4. image digest and pull policy;
5. requests/limits, GPU class, ephemeral storage, and quota preflight;
6. Pod Security and NetworkPolicy evidence;
7. owner labels on Pod, Job, PVC, Secret, ServiceAccount additions, and ancillary
   objects;
8. watch-based lifecycle with relist/reconcile after disconnect;
9. Kubernetes Secret deletion immediately after bootstrap;
10. teardown discovery proving all owned objects are absent.

Integration tests use kind or k3d with no developer cluster context.

## 20. HPC Apptainer/Singularity provider

“Singularity” alone is not a complete HPC profile. Real clusters normally require
a scheduler and shared-filesystem policy.

First supported shape:

- provider id `hpc_apptainer`;
- Apptainer preferred, Singularity-compatible executable accepted when explicitly
  validated;
- SSH control to a login node;
- Slurm as the first explicit scheduler adapter;
- scheduler allocation launches the relay inside a pinned SIF;
- compute node makes the outbound relay WebSocket;
- workspace is an explicit shared path or persistent overlay;
- no long-running work executes directly on the login node.

Required preflight:

- executable/version and signature support;
- scheduler commands and account/partition/QOS;
- outbound TLS/WebSocket from compute nodes;
- shared path visibility on login and compute nodes;
- UID/GID and file ownership;
- CPU/memory/GPU/time request validity;
- overlay support and quota;
- site policy forbidding privileged/FUSE behavior;
- cleanup permissions.

Lifecycle mapping:

- create -> submit allocation/job;
- connecting -> wait for scheduler start and relay registration;
- health -> relay plus scheduler allocation healthy;
- hibernate -> supported only if provider/site has a proven retained overlay and
  scheduler release model;
- destroy -> cancel job/allocation, remove owned bootstrap artifacts, verify no
  queued/running job, retain/delete overlay exactly as selected.

PBS, LSF, and other schedulers are future provider capability additions. They are
not aliases for Slurm.

## 21. Scale-to-zero qualification and pilot

Do not add three shallow cloud profiles.

Run a qualification spike against Modal, Daytona, and Vercel Sandbox only when a
real use case identifies:

- workload shape;
- required runtime and reconnect duration;
- workspace size and persistence expectation;
- region/data-residency need;
- CPU/GPU need;
- acceptable cold-start time;
- budget and cost reporting requirement.

Hard gates for a candidate:

1. supports outbound authenticated WebSocket for the required duration;
2. supports deterministic create/inspect/stop/resume/destroy APIs;
3. provides idempotency or exact ownership discovery;
4. preserves the workspace using a documented volume/snapshot mechanism;
5. can inject a single-use bootstrap secret without retaining it;
6. can enforce or clearly report egress policy;
7. exposes resource quota and cost metadata;
8. provides a reliable teardown inventory;
9. supports pinned image/artifact inputs;
10. passes a live hibernate/resume sentinel test;
11. passes a live leak check after interrupted create and force destroy.

The pinned Hermes snapshot suggests useful candidates, not PawFlow support:

- Daytona contains an explicit “ensure stopped sandbox is ready” pattern;
- Modal contains snapshot/restore plus asynchronous SDK execution patterns;
- Vercel Sandbox contains transient-error classification, running-state waits,
  snapshot handling, and stop logic.

Recommended decision process:

1. score all three with the same contract harness;
2. publish the evidence and disqualifying facts;
3. choose exactly one pilot;
4. put it behind an experimental provider flag;
5. graduate only after real workload, persistence, cost, and teardown evidence.

If no provider passes, record a no-go result. Do not ship a profile that violates
the provider contract merely to satisfy catalogue count.

## 22. API and command surface

Use the existing authenticated UI/action boundary and a typed internal API.

Read actions:

- `relay_deployment_provider_catalog`;
- `relay_deployment_profile_list`;
- `relay_deployment_profile_get`;
- `relay_deployment_profile_plan`;
- `relay_deployment_list`;
- `relay_deployment_get`;
- `relay_deployment_events`;
- `relay_deployment_cost`.

Write actions:

- `relay_deployment_profile_create`;
- `relay_deployment_profile_revise`;
- `relay_deployment_profile_delete`;
- `relay_deployment_create`;
- `relay_deployment_reconnect`;
- `relay_deployment_hibernate`;
- `relay_deployment_resume`;
- `relay_deployment_destroy`;
- `relay_deployment_force_destroy`;
- `relay_deployment_storage_delete`.

Mutation request requirements:

- authenticated actor;
- exact scope;
- UUID idempotency key;
- expected generation/revision;
- explicit provider/profile/deployment id;
- confirmation token for destructive actions;
- no client-supplied actor, owner, capability observation, health, or cost fields.

The response returns an operation id immediately. SSE events invalidate the view;
the browser reloads authoritative state rather than applying client-side lifecycle
patches.

## 23. UI and operator experience

Add **Resources -> Deployment profiles** beside the existing relay surface rather
than creating another infrastructure application.

### 23.1 Profile wizard

Steps:

1. name and scope;
2. provider and placement;
3. trust-boundary explanation;
4. provider credential binding;
5. workspace and retention;
6. relay image/artifact;
7. required/optional capabilities;
8. CPU/memory/disk/GPU/process limits;
9. egress/TLS/host-key policy;
10. lifecycle/reuse/idle behavior;
11. cost/budget metadata;
12. redacted plan and confirmation;
13. asynchronous preflight/create progress.

Advanced infrastructure vocabulary may be progressively disclosed, but the saved
profile remains explicit.

### 23.2 Instance view

Show:

- desired and observed state;
- profile revision/digest;
- provider/placement;
- exact relay id and connection;
- declared, observed, and effective capabilities;
- workspace kind, persistence, and retention;
- image/artifact digest;
- active leases;
- last health and stable error code;
- current cost status;
- create/reconnect/hibernate/resume/destroy availability;
- teardown receipt and leak warnings;
- immutable event history.

The UI never labels `connecting` as healthy and never hides an unsupported
operation behind a disabled generic button without an explanation.

## 24. Flow, agent, and relay-binding integration

Extend runtime selection with an exact discriminated reference:

~~~json
{
  "relay_target": {
    "kind": "relay|deployment_profile",
    "id": "exact UUID/service id",
    "revision": 3
  }
}
~~~

Rules:

- existing exact relay bindings remain valid after migration;
- profile resolution occurs before a task is dispatched;
- the resolved relay id is recorded in WorkflowRun evidence;
- a lease ties the deployment to the run/task;
- downstream tools receive only the relay id and existing `local` boolean;
- a provider outage is an infrastructure failure with stable retry advice, not a
  tool-specific error;
- retries do not silently switch providers or profile revisions;
- different tasks may explicitly use different profiles;
- no default profile is selected when the reference is missing.

## 25. Storage, events, and redaction

Suggested modules:

~~~text
core/
  relay_deployment_contracts.py
  relay_deployment_store.py
  relay_deployment_events.py
  relay_deployment_registry.py
  relay_deployment_coordinator.py
  relay_deployment_reconcile.py
  relay_deployment_bootstrap.py
  relay_deployment_providers/
    base.py
    local.py
    docker.py
    ssh.py
    kubernetes.py
    hpc_apptainer.py
    qualified_cloud.py
~~~

Keep each file within the project size convention and split provider-specific
helpers when needed.

The event model records redacted facts, not raw SDK responses. Example:

~~~json
{
  "event_id": "UUID",
  "created_at": "UTC timestamp",
  "event_type": "deployment.destroy.completed",
  "deployment_id": "UUID",
  "operation_id": "UUID",
  "generation": 7,
  "actor_user_id": "authenticated id",
  "data": {
    "profile_revision": 3,
    "provider_type": "kubernetes",
    "before_state": "healthy",
    "after_state": "destroyed",
    "teardown_receipt_id": "UUID"
  }
}
~~~

Redaction is allowlist-based. Provider exception text is mapped to stable codes;
full sensitive exception objects are never persisted.

## 26. Work packages and delivery order

### WP0 - Contract and threat model

Deliver:

- strict profile, deployment, operation, lease, event, health, cost, and teardown
  models;
- state machine and transition validator;
- provider interface and fake provider;
- capability vocabulary;
- trust-boundary matrix;
- secret and redaction rules.

Gate:

- contract tests green;
- no provider implementation required by generic modules;
- security review approves the control/run-plane split.

### WP1 - Store, coordinator, bootstrap, and UI skeleton

Deliver:

- transactional store and migrations;
- coordinator and background operation execution;
- idempotency/generation fencing;
- single-use bootstrap ticket and handshake changes;
- reconciliation loop;
- authenticated API/actions;
- read-only profile/instance/event UI;
- exact profile-to-relay runtime resolver and leases.

Gate:

- fake provider passes full lifecycle, interruption, restart, and leak scenarios;
- old relay protocol is rejected only after the documented one-shot cutover;
- no secret appears in state/log fixtures.

### WP2 - Formalize local, Docker, and SSH

Order inside the package:

1. Docker server placement using `ServerRelayManager` internals;
2. Docker supervisor placement using Relay Desktop/`RelayThread`;
3. local native supervisor after completing the native launcher;
4. SSH-managed Linux provider using the installer transport primitives.

Gate:

- all four placements pass the same provider contract suite;
- local is not marketed before the native path is real;
- SSH loss does not interrupt the reverse relay;
- deterministic cleanup has zero owned-resource leaks.

### WP3 - Kubernetes pod/job

Deliver:

- typed Kubernetes binding and schema;
- namespace/service-account/policy preflight;
- interactive pod and per-run Job modes;
- PVC/ephemeral workspace policies;
- watch/relist reconciliation;
- Kubernetes Secret bootstrap and deletion;
- UI-specific options and documentation.

Gate:

- kind/k3d lifecycle, interrupted create, pod restart, PVC sentinel, network policy,
  quota, and teardown tests green.

### WP4 - Apptainer plus one HPC scheduler

Deliver:

- Apptainer/Singularity artifact preflight;
- Slurm adapter;
- shared path/overlay workspace;
- allocation/job reconciliation;
- outbound-connectivity probe;
- resource/cost metadata appropriate to the site.

Gate:

- fake Slurm contract tests;
- real opt-in cluster smoke before marking supported;
- no login-node execution fallback.

### WP5 - One scale-to-zero pilot

Deliver:

- demand record and qualification scorecard;
- live contract results for Modal, Daytona, and Vercel Sandbox candidates;
- one experimental provider or documented no-go;
- snapshot/hibernate/resume, cost, and teardown evidence;
- provider-specific operations documentation.

Gate:

- all hard qualification gates pass for the selected provider;
- real workload pilot succeeds;
- leaked-resource scan is empty.

### WP6 - Migration and lifecycle cutover

Deliver:

- one-shot server-managed relay migration;
- Relay Desktop workspace-profile migration;
- binding/reference migration;
- removal of old direct lifecycle entry points;
- operator preflight and rollback-by-backup procedure;
- documentation and release checks.

Gate:

- no dual lifecycle engine remains;
- existing workspace data, relay ids where valid, and bindings are preserved;
- secrets are absent from backups;
- every migrated instance is healthy or explicitly quarantined.

### WP7 - Product hardening

Deliver:

- complete create/edit/plan/status/action UI;
- accessibility, keyboard, mobile, and i18n parity;
- diagnostics and teardown receipts;
- infrastructure-cost display;
- operator runbooks;
- full CI/provider compatibility matrix.

Gate:

- global acceptance checklist below is green.

## 27. Test strategy

### 27.1 Provider contract suite

Run the same parametrized suite against every provider:

1. strict schema rejects missing/unknown fields;
2. plan is deterministic and redacted;
3. create is idempotent;
4. concurrent create yields one instance;
5. interrupted create discovers rather than duplicates;
6. stale generation is rejected;
7. health requires exact relay plus workspace probe;
8. capability mismatch fails closed;
9. reconnect preserves identity and workspace;
10. hibernate/resume is correct or explicitly unsupported;
11. active lease blocks normal hibernate/destroy;
12. force destroy interrupts and audits;
13. destroy is idempotent;
14. provider discovery finds every owned resource;
15. teardown receipt is complete;
16. leak scan is empty;
17. secrets never enter persisted/logged data;
18. quota denial is stable and redacted;
19. cost metadata has status/source/timestamp;
20. restart reconciliation converges.

### 27.2 Coordinator/store tests

Cover:

- legal and illegal transitions;
- operation UUID/idempotency conflicts;
- `expected_generation` conflicts;
- crash between audit-before and provider create;
- crash between provider create and persisted instance ref;
- crash after relay connect but before healthy commit;
- bootstrap expiry/replay/wrong generation;
- lease acquire/release and orphan reconciliation;
- profile revision immutability;
- scope and cross-user authorization;
- redacted event serialization;
- SSE invalidation;
- no implicit lifecycle limit.

### 27.3 Workspace tests

For every supported workspace kind:

- correct `/workspace` mapping;
- traversal/symlink escape resistance;
- read-only enforcement;
- write sentinel when permitted;
- hibernate/resume sentinel;
- retain-on-destroy;
- explicit storage delete;
- interrupted hydration;
- no unauthorized FileStore/session/skills access.

### 27.4 Provider integration tests

- **Local:** native process start/reconnect/process-tree stop on Linux, Windows, and
  macOS supervisor smokes.
- **Docker:** isolated daemon, digest pinning, ownership labels, volume retention,
  restart, egress, foreign-container refusal.
- **SSH:** disposable host, strict host key, key rotation failure, control
  disconnect, systemd restart, remote cleanup.
- **Kubernetes:** kind/k3d namespace, Pod/Job, watch reconnect, pod eviction, PVC,
  quota, NetworkPolicy, Secret removal, leak scan.
- **HPC:** fake scheduler plus opt-in Apptainer/Slurm smoke.
- **Cloud:** fake/recorded SDK tests always; live account tests opt-in and isolated.

No default test touches a developer's Docker daemon, SSH host, Kubernetes context,
HPC account, cloud account, keychain, or production relay.

### 27.5 Security tests

- credential-shaped values rejected from profile/state/events;
- log/exception redaction;
- SSRF/TLS/host-key policy;
- foreign resource never adopted or deleted;
- cross-scope provider binding denial;
- old/replayed bootstrap token rejection;
- Kubernetes privilege escalation denial;
- egress policy proof;
- image/artifact digest mismatch;
- destructive storage deletion requires separate confirmation;
- audit-before/audit-after completeness.

### 27.6 UI tests

- plan and create use the same serialized profile;
- provider-specific fields do not leak into another provider;
- trust boundary is visible before confirmation;
- capability declared/observed differences are visible;
- connecting is never rendered healthy;
- destructive labels name retained/deleted resources;
- keyboard and screen-reader lifecycle controls;
- mobile layout;
- en/fr/es key parity.

## 28. Migration

PawFlow has no backward-compatibility requirement. Use a one-shot migration, not
a permanent adapter maze.

### 28.1 Server relays

For each existing managed server relay:

1. read current scope, relay id, kind, workspace/home volume, image, binding, and
   enabled flags;
2. create an immutable Docker profile revision with explicit materialized fields;
3. create a deployment record pointing to the existing exactly owned container or
   stopped volume;
4. preserve conversation/user/global binding;
5. reconcile health through the new handshake;
6. quarantine ambiguity rather than guess;
7. delete the old metadata/lifecycle path after successful cutover.

### 28.2 Relay Desktop/CLI

The upgraded supervisor:

1. reads `servers.json`/`workspaces.json` once;
2. migrates each workspace to a Docker profile revision because current manager
   startup supplies a Docker image;
3. preserves server, path, relay id, read/write mode, image, capabilities, and
   autostart choice;
4. keeps credentials in the OS vault;
5. requires explicit user creation of a local-native revision;
6. writes a redacted backup and deletes old lifecycle code after validation.

### 28.3 SSH

There is no runtime SSH profile to migrate. Installer SSH targets remain installer
history, not execution deployments.

### 28.4 Protocol cutover

- ship updated server and relay/supervisor artifacts;
- preflight all registered relays;
- require compatible protocol/capability schema before store migration;
- stop cutover if incompatible relays remain;
- migrate once;
- reject old registration format after cutover;
- do not maintain a dual capability inference path.

## 29. Observability and operations

Metrics:

- deployment operations by provider/state/result;
- create-to-connected and connected-to-healthy latency;
- reconnect and reconciliation count;
- hibernate/resume latency;
- active leases;
- capability mismatch count;
- bootstrap rejection reason;
- teardown duration and leaked-resource count;
- cost-observation freshness/status;
- provider API error class.

Logs use deployment/operation/profile ids and stable error codes, never secrets.

Operator runbooks:

- provider preflight failure;
- relay never connects;
- stale provider instance;
- interrupted create;
- stuck destroy/leak receipt;
- supervisor offline;
- Kubernetes watch loss;
- HPC queued allocation;
- cloud snapshot failure;
- credential rotation;
- one-shot migration quarantine.

## 30. Acceptance criteria

G6 is complete only when:

1. a user can create and select named local, Docker, SSH, Kubernetes, and HPC
   profiles with explicit scope and trust boundary;
2. every shipped provider implements provider contract v1;
3. selecting a profile asynchronously produces or reuses one healthy relay;
4. health proves relay connection, required capabilities, profile revision, and
   workspace mapping;
5. tools execute unchanged through `RelayService` with an exact `relay_id`;
6. no tool contains provider-specific branching;
7. local native is backed by a complete native launcher, not the current stub;
8. SSH leaves the tool path after bootstrap and survives control disconnect;
9. Kubernetes pod/job passes restart, PVC/ephemeral, policy, quota, and teardown
   tests;
10. HPC uses an explicit scheduler and never falls back to login-node execution;
11. one scale-to-zero provider passes live qualification before it is marked
    experimental/supported, or the phase records a visible no-go;
12. every profile records explicit capability, workspace, resource, network,
    lifecycle, and cost policy;
13. credentials and bootstrap secrets never persist;
14. images/artifacts are digest-pinned;
15. create, reconnect, hibernate, resume, destroy, and force destroy are
    idempotent and audited;
16. interrupted create/restart reconciliation never duplicates infrastructure;
17. normal destroy refuses active leases;
18. teardown receipts and leak scans prove deterministic cleanup;
19. infrastructure cost is labelled and separate from LLM usage;
20. no implicit timeout, retry, quota, budget, or provider fallback exists;
21. existing relay/workspace bindings and retained data survive one-shot migration;
22. old direct lifecycle paths are removed;
23. API/UI/CLI/docs use the same versioned contracts;
24. unit, integration, security, UI, platform, and opt-in live gates pass.

## 31. Selective Hermes MIT reuse ledger

Evidence source:

- pinned Hermes snapshot: `a24c12d14f5f1d37deee8887c6072a1d579e7e98`;
- license: MIT, Copyright (c) 2025 Nous Research.

Potentially reusable patterns:

| Hermes source | Reuse candidate | PawFlow adaptation |
|---|---|---|
| `tools/environments/base.py` | connection-error taxonomy, interruptible lifecycle handles, cleanup contract | provider result/error types only; do not reuse command execution wrapper |
| `tools/environments/docker.py` | ownership labels, resource validation, persistent workspace cleanup, bounded cleanup wait | adapt into Docker provider behind coordinator |
| `tools/environments/ssh.py` | connection preflight, persistent control connection ideas, bulk transfer/error scenarios | use only for bootstrap/lifecycle; PawFlow tools still use relay |
| `tools/environments/singularity.py` | Apptainer discovery, SIF caching/digest, persistent overlay, instance cleanup | combine with explicit scheduler provider |
| `tools/environments/modal.py` | snapshot restore/cleanup and async SDK lifecycle pattern | candidate qualification; relay is runtime |
| `tools/environments/daytona.py` | stopped-sandbox readiness/resume and SDK cancellation patterns | candidate qualification; no terminal coupling |
| `tools/environments/vercel_sandbox.py` | transient error classification, running-state wait, snapshot/stop logic | candidate qualification; no terminal coupling |
| `tools/environments/file_sync.py` | interrupted sync and upload-only secret test scenarios | reuse scenarios selectively; prefer PawFlow workspace/FileStore contracts |

Do not copy:

- `BaseEnvironment.execute()`;
- direct terminal backend selection;
- shell command wrappers;
- Hermes session/CWD/environment snapshot model;
- provider-specific file execution as the runtime transport;
- automatic credential-file propagation;
- Hermes persistence files or backend registry schema.

For every substantial adapted portion:

1. retain the Nous Research MIT notice in the source file or adjacent license
   header;
2. add the exact source path and pinned commit to `THIRD_PARTY_NOTICES.md`;
3. record whether code, structure, or tests were adapted;
4. preserve the MIT permission notice;
5. keep PawFlow-specific security review and tests;
6. never describe copied/adapted code as independently authored.

## 32. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Profiles become cosmetic aliases | Health requires provider lifecycle and observed relay evidence |
| Provider logic leaks into tools | Coordinator resolves to relay id; contract forbids user command execution |
| `local=true` is confused with local profile | Separate names, schemas, warnings, and tests |
| Interrupted create duplicates costly resources | ownership labels, idempotency, discover-before-create |
| Destroy deletes foreign resources | exact ownership proof and refusal on mismatch |
| Workspace disappears after hibernate | explicit persistence kind and sentinel test |
| Cloud cost is misleading | status/source/confidence; unavailable instead of zero |
| Secrets leak through provider SDK errors | just-in-time resolution and allowlist redaction |
| Kubernetes implementation needs cluster-admin | namespace-scoped least privilege contract |
| HPC runs on login node | explicit scheduler required |
| Supervisor is offline | fail visibly; never report healthy |
| Native local is marketed before implemented | acceptance gate against current stub |
| Old/new lifecycle race | one-shot migration and old path deletion |
| Scale-to-zero catalogue outruns evidence | one qualified pilot only |
| FUSE requires elevated container capabilities | visible residual risk and per-profile policy |
| Automatic idle cleanup kills work | active leases plus explicit idle policy only |

## 33. Recommended implementation sequence

The strict sequence is:

1. contract, store, state machine, fake provider, threat model;
2. coordinator, bootstrap, reconciliation, leases, API/UI skeleton;
3. Docker server;
4. Docker supervisor;
5. local native;
6. SSH-managed Linux;
7. Kubernetes pod then Job;
8. Apptainer plus Slurm;
9. scale-to-zero qualification and one pilot;
10. one-shot migration and old-path deletion;
11. full product hardening and compatibility matrix.

Do not start a later provider until the same contract suite is green for the
previous one.

## 34. Final architectural answer

Relays already make execution location-independent after connection. That is the
hardest run-plane foundation and it should remain unchanged.

What remains is not merely easier configuration. Local and Docker can reuse much
of today's code, but G6 still needs a real native launcher, one lifecycle/state
contract, supervisor control, bootstrap credentials, reconciliation, workspace
semantics, capability negotiation, quotas/cost, audit, and teardown evidence.
SSH needs to become lifecycle-only bootstrap rather than installation history.
Kubernetes, scheduler-aware HPC, and scale-to-zero require new provider adapters
and real infrastructure tests.

The correct product model is therefore:

**select profile -> provision/reconcile environment -> connect and verify relay ->
return exact relay id -> run every tool through the existing relay -> release,
hibernate, or destroy through the provider contract.**

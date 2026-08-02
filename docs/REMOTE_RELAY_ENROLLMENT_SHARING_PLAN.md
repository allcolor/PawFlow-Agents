# Remote Relay Enrollment and Sharing Implementation Plan

Status: **proposed** (architecture and implementation plan only; no runtime
implementation yet).

This plan complements `docs/RESOURCE_ACL_SHARING_PLAN.md`. It reuses that
plan's principal, canonical-group, policy-evaluation, audit, and revocation
primitives, but relays remain services with live connections and operational
capabilities. They are not converted into repository resources.

## 1. Goal

Allow an operator to start a remote PawFlow relay with a machine credential so
that the relay can enroll without an interactive browser login and can expose
one or more endpoints that are:

1. administratively global but restricted to selected users, groups, or roles;
2. shared with several principals without copying the relay definition;
3. explicitly bound to conversations or agents before use;
4. constrained by fine-grained capabilities such as filesystem read, filesystem
   write, container execution, host execution, proxying, and desktop control;
5. revocable without restarting PawFlow;
6. safe against cross-user confusion in inverse filesystem mounts, caches,
   connection pools, and concurrent requests.

The design must support two meanings of "the same relay":

- one logical endpoint and one intentionally shared workspace;
- one physical daemon exporting several isolated logical endpoints.

It must not claim that one mutable user-bound service instance is a secure
multi-tenant runtime.

## 2. Scope

The first implementation covers:

- relay-specific enrollment credentials;
- non-interactive enrollment over HTTPS;
- server-controlled creation or reconciliation of global and user relay
  endpoints;
- relay node and endpoint identities independent from users;
- ACL-aware discovery, binding, and use;
- capability-aware authorization;
- short-lived runtime connection credentials;
- shared-workspace endpoints;
- multiple isolated endpoints exported by one daemon;
- authorization and identity propagation for normal relay operations;
- safe restrictions for inverse server filesystem, FileStore, and skills
  mounts;
- audit, revocation, cache invalidation, migration, and compatibility.

## 3. Non-goals for the first release

The first release does not provide:

- anonymous or public-internet relay access;
- arbitrary deny rules in relay ACLs;
- relay-selected ACLs or relay-selected PawFlow roles;
- ownership transfer initiated by a relay;
- arbitrary remote root selection supplied by a PawFlow user;
- automatic access to the relay owner's secrets;
- automatic injection of consumer secrets into a shared relay;
- dynamic per-request filesystem roots inside one logical endpoint;
- multi-user inverse FUSE mounts on one undifferentiated channel;
- transparent live migration of running shell or CLI processes between relay
  nodes;
- conversation or resource storage hosted by the relay; that is a separate
  architecture plan;
- replacement of the existing browser-login flow for personal relays before
  the enrollment path has reached feature parity.

## 4. Existing architecture and gaps

### 4.1 Interactive auto-registration

`pawflow_relay/register.py` opens a browser, receives a user session, creates a
user-scoped `relay` service through `service_install`, generates a static relay
token, and connects to `/ws/relay/<relay_id>`.

Consequences:

- the remote relay is coupled to the user who completed the browser login;
- unattended machines cannot enroll cleanly;
- creating a global relay requires a separate admin UI/API action;
- service ownership, connection identity, and allowed audience are conflated.

### 4.2 Two authentication layers

The WebSocket upgrade accepts either a live user session, an internal token, or
a generic API key. The relay then sends a `register` message whose `token` must
match the token stored in the existing service definition.

The WebSocket metadata contains `auth_user_id`, `auth_role`,
`auth_session_id`, and `auth_is_api_key`, but `RelayService._handle_ws()` does
not currently consume that metadata. Registration ultimately relies on static
token equality.

### 4.3 Generic API keys are not identities

The current API-key store maps a raw bearer token to a description. Keys have
no stable credential ID, principal, role, scopes, resource constraints,
expiry, usage limit, or policy template. Raw keys are persisted in the
security configuration.

These keys must not acquire an implicit `create global relay` superpower.

### 4.4 Relay routes and names are server-global

Every `RelayService` registers `/ws/relay/<service_id>` on the shared HTTP
listener. The service registry therefore prohibits duplicate relay service IDs
across all scopes. This global uniqueness is useful for network routing, but a
display name is not a sufficient durable identity.

### 4.5 Resolution and binding

Service resolution walks:

~~~text
conversation -> parent conversation -> user -> global
~~~

Conversation relay bindings store relay names in conversation extras. Linking
checks that a visible relay service with that name exists, but there is no
relay-specific ACL and no operation-level authorization.

### 4.6 Mutable user identity is unsafe for sharing

`RelayService` has a mutable `_user_id`. Filesystem tool setup calls
`set_user_id(user_id)` on a resolved service instance. Inverse-direction
handlers are then created lazily and capture one user ID:

- `RelayServerFs`;
- `RelayFileStoreFs`;
- `RelaySkillsFs`.

A global live service instance is shared. Concurrent callers can therefore
change `_user_id`, while an already-created handler remains tied to an earlier
user. The remote-FS manifest also derives an owner from this mutable field.

Adding ACL filtering only to relay listings would leave a cross-user race and
possible data exposure. Removing this mutable request identity is a release
blocker for shared relays.

## 5. Design axes

The implementation must keep these properties independent:

~~~text
deployment_mode = server_managed | remote
catalog_scope   = global | user | conversation
sharing_mode    = private | acl_shared
isolation_mode  = shared_workspace | endpoint_isolated | tenant_dynamic
connection_mode = one_endpoint_per_ws | multiplexed
~~~

Definitions:

- **Global** means administratively owned and stored at server scope.
- **Shared** means more than one principal has an ACL grant.
- **Multi-tenant** means request data and server-side views are isolated between
  principals despite sharing infrastructure.
- **Remote** describes where the daemon runs, not who owns or may use it.

## 6. Non-negotiable invariants

1. A valid generic API key does not imply relay enrollment authority.
2. A relay may create or reconcile only objects allowed by its enrollment
   credential.
3. A remote relay never selects its own ACL, groups, roles, or administrative
   owner.
4. A global relay is not automatically visible or usable by all users.
5. Relay node identity, endpoint identity, live connection identity, and user
   identity remain separate.
6. Every endpoint has a stable UUID. Display names and service IDs are aliases.
7. ACL visibility does not bind or auto-select an endpoint.
8. A binding does not replace an operation-time authorization check.
9. Effective permissions can only narrow as a request crosses layers.
10. Capability increases, root changes, or isolation-mode changes require
    explicit server acceptance and endpoint revision handling.
11. The remote daemon cannot assert a user ID, group list, role, conversation,
    or permission set.
12. Request identity is immutable and request-scoped; no shared service object
    stores the current user in a mutable field.
13. Shared endpoints expose no personal inverse filesystem mount in v1.
14. Secrets used to authenticate the relay are never returned in catalog or
    service-detail APIs.
15. Sharing a relay does not share either the publisher's or consumer's PawFlow
    secrets.
16. Revocation affects active sessions, leases, proxy tokens, bindings, mounts,
    and caches.
17. Unauthorized direct lookup is indistinguishable from an unknown endpoint.
18. All creation, policy, binding, session, and revocation events are audited.

## 7. Target domain model

### 7.1 RelayNode

A node represents a durable remote daemon or machine installation.

~~~json
{
  "node_id": "uuid",
  "display_name": "build-host-01",
  "state": "active",
  "created_by": "admin-user-id",
  "enrollment_credential_id": "uuid",
  "agent_version": "x.y.z",
  "protocol_versions": [2, 3],
  "platform": "linux",
  "labels": {"region": "eu-west"},
  "created_at": "ISO-8601",
  "last_seen_at": "ISO-8601",
  "revoked_at": null
}
~~~

Rules:

- `node_id` is generated or accepted only by the server;
- reconnecting the same installation reconciles the node rather than creating
  unlimited duplicates;
- mutable telemetry is separated from security policy;
- labels used for policy or scheduling are admin-approved, not trusted relay
  assertions.

### 7.2 RelayEndpoint

An endpoint is one logical root and capability boundary exported by a node.

~~~json
{
  "endpoint_id": "uuid",
  "node_id": "uuid",
  "alias": "team-project",
  "endpoint_revision": 1,
  "catalog_scope": "global",
  "owner_user_id": "",
  "sharing_mode": "acl_shared",
  "isolation_mode": "shared_workspace",
  "root_profile": "team-project",
  "root_fingerprint": "sha256:...",
  "advertised_capabilities": ["filesystem.read", "filesystem.write"],
  "effective_capabilities": ["filesystem.read", "filesystem.write"],
  "policy_id": "uuid",
  "auto_select": false,
  "state": "available",
  "created_at": "ISO-8601",
  "updated_at": "ISO-8601"
}
~~~

`root_profile` is a relay-local configured identifier. Normal users do not send
raw host paths. `root_fingerprint` detects an unexpected change without
publishing the actual host path to every catalog consumer.

### 7.3 RelayConnection

A connection is ephemeral runtime state:

~~~json
{
  "connection_id": "uuid",
  "node_id": "uuid",
  "endpoint_ids": ["uuid"],
  "runtime_credential_id": "uuid",
  "protocol_version": 3,
  "connected_at": "ISO-8601",
  "last_heartbeat_at": "ISO-8601",
  "remote_address": "redacted-or-admin-only",
  "state": "connected"
}
~~~

Connections and their secrets are not persisted as service configuration.
Operational summaries may be checkpointed for diagnostics.

### 7.4 RelayEnrollmentCredential

~~~json
{
  "credential_id": "uuid",
  "secret_prefix": "pfre_1234",
  "secret_hash": "argon2id-or-HMAC-backed-hash",
  "description": "CI relay enrollment",
  "created_by": "admin-user-id",
  "scopes": ["relay.register", "relay.endpoint.create.global"],
  "allowed_endpoint_ids": [],
  "allowed_alias_prefixes": ["ci-"],
  "capability_ceiling": ["filesystem.read", "exec.container"],
  "policy_template_id": "uuid",
  "max_nodes": 2,
  "max_endpoints_per_node": 4,
  "expires_at": "ISO-8601",
  "last_used_at": null,
  "revoked_at": null
}
~~~

The raw secret is displayed once. Validation uses the credential ID/prefix to
find the record and constant-time verification of the secret hash.

### 7.5 RelayAccessPolicy

Reuse the policy evaluator and canonical principal model from the resource ACL
plan. Use a relay namespace with a relay-specific permission vocabulary.

~~~json
{
  "policy_id": "uuid",
  "object_type": "relay_endpoint",
  "object_id": "endpoint-uuid",
  "mode": "restricted",
  "policy_revision": 3,
  "grants": [
    {
      "grant_id": "uuid",
      "subject": {"type": "group", "id": "canonical-group-id"},
      "permissions": ["discover", "link", "filesystem.read"]
    }
  ]
}
~~~

Policy records remain server-owned. Portable relay profiles cannot contain ACL
grants.

### 7.6 RelayBinding

Upgrade conversation extras from name-only entries to exact references:

~~~json
{
  "binding_id": "uuid",
  "endpoint_id": "uuid",
  "endpoint_revision": 2,
  "alias": "team-project",
  "conversation_id": "uuid",
  "agent_name": "*",
  "default": true,
  "requested_permissions": ["filesystem.read", "filesystem.write"],
  "accepted_root_fingerprint": "sha256:...",
  "created_by": "user-id",
  "created_at": "ISO-8601",
  "state": "active"
}
~~~

Capability or root changes that increase risk mark the binding
`update_pending`. Restrictive changes apply immediately.

### 7.7 RelayLease

A lease is a short-lived, server-issued authorization for a concrete runtime
channel or operation set.

~~~json
{
  "lease_id": "uuid",
  "token_hash": "...",
  "endpoint_id": "uuid",
  "connection_id": "uuid",
  "binding_id": "uuid",
  "user_id": "user-id",
  "conversation_id": "uuid",
  "agent_name": "codex",
  "permissions": ["filesystem.read"],
  "policy_revision": 3,
  "membership_revision": 8,
  "expires_at": "ISO-8601",
  "revoked_at": null
}
~~~

Leases are held in memory by default. Long-running sessions renew them. A policy
or membership revision change invalidates affected leases.

## 8. Permission model

### 8.1 Permissions

Use explicit, operation-oriented permissions:

- `discover`;
- `link`;
- `filesystem.read`;
- `filesystem.write`;
- `exec.container`;
- `exec.host`;
- `network.proxy`;
- `automation`;
- `screen.observe`;
- `screen.control`;
- `endpoint.inspect`;
- `endpoint.manage`.

`endpoint.manage` is not granted to enrollment credentials merely because they
created the endpoint. Human administrative authority remains separate from
machine enrollment authority.

### 8.2 Permission implication

Keep implications minimal and explicit:

~~~text
filesystem.write -> filesystem.read
screen.control    -> screen.observe
link              -> discover
~~~

No filesystem permission implies execution. No container execution permission
implies host execution. `network.proxy`, `automation`, and screen permissions
are independent.

### 8.3 Effective authorization

For every operation:

~~~text
effective permission
  = enrollment credential capability ceiling
  intersect endpoint effective capabilities
  intersect current principal ACL permissions
  intersect conversation binding requested permissions
  intersect conversation approval/permission mode
  intersect active lease permissions
~~~

A missing layer denies. A layer cannot add a permission removed by an earlier
layer.

### 8.4 Suggested UI profiles

The UI may offer profiles but must store fine-grained permissions:

- **Read only**: discover, link, filesystem.read;
- **Workspace editor**: read-only plus filesystem.write;
- **Container operator**: workspace editor plus exec.container;
- **Host operator**: explicit exec.host, never implied;
- **Desktop operator**: explicit observe/control and automation.

## 9. Enrollment workflow

### 9.1 Credential issuance

An administrator creates an enrollment credential and selects:

- allowed catalog scope;
- one existing endpoint or allowed endpoint creation rules;
- capability ceiling;
- policy template;
- maximum nodes and endpoints;
- expiration and optional single-use behavior;
- whether enrollment is immediate or approval-gated.

The raw key is returned once. CLI arguments are discouraged because process
listings and shell history can expose them. Support stdin, an environment
variable, a protected file, and the OS keychain.

### 9.2 Enrollment request

Add a typed endpoint rather than overloading `/api/ui`:

~~~http
POST /api/relay-enrollment/v1/enroll
Authorization: Bearer <enrollment-secret>
Content-Type: application/json
~~~

The request may contain:

~~~json
{
  "installation_id": "locally-generated-uuid",
  "display_name": "build-host-01",
  "agent_version": "x.y.z",
  "protocol_versions": [3, 2],
  "endpoints": [
    {
      "local_profile_id": "team-project",
      "requested_alias": "ci-team-project",
      "root_fingerprint": "sha256:...",
      "capabilities": ["filesystem.read", "exec.container"]
    }
  ]
}
~~~

The server:

1. authenticates and rate-limits the credential;
2. validates expiry, revocation, usage limits, and alias constraints;
3. creates or reconciles the node using `installation_id` and credential
   ownership;
4. intersects advertised capabilities with the credential ceiling;
5. selects scope and policy from server-owned rules;
6. rejects or marks pending unexpected root/capability changes;
7. creates stable endpoint UUIDs;
8. returns a short-lived, narrowly scoped runtime credential.

### 9.3 Global endpoint creation

`relay.endpoint.create.global` is an explicit credential scope that only an
admin can issue. It does not let the relay choose a global ACL.

For a newly created global endpoint:

- require a valid policy template; or
- create it as admin-only/pending when no template exists.

Never default a machine-created global endpoint to all authenticated users.

### 9.4 Idempotency and reconciliation

Enrollment must be idempotent by `(credential_id, installation_id,
local_profile_id)`. Reconnect or daemon restart must not produce duplicate
endpoints.

Changes are classified:

- telemetry-only: accept immediately;
- capability reduction: accept immediately and invalidate incompatible leases;
- capability increase: require server policy approval;
- root fingerprint change: mark endpoint unavailable/update-pending;
- isolation-mode change: require explicit admin approval;
- alias change: update display metadata without changing endpoint identity.

## 10. Runtime connection protocol

### 10.1 Separate enrollment and runtime credentials

The enrollment secret is not sent on every WebSocket reconnect. Enrollment
returns a runtime credential with:

- credential ID;
- node and endpoint constraints;
- short expiry;
- one-time or bounded reconnect usage;
- rotation endpoint;
- server-side revocation state.

### 10.2 Connection route

Introduce one centrally registered versioned route, for example:

~~~text
/ws/relay-connect/v3
~~~

This avoids creating an authentication route before the service definition
exists and separates network identity from a display/service name.

The legacy `/ws/relay/<service_id>` routes remain during migration.

### 10.3 WebSocket authentication

Prefer an `Authorization` header or secure cookie supported by the client
transport. Query-string tokens remain a deprecated compatibility path because
URLs are more likely to be logged.

After upgrade, registration includes only server-issued IDs and a runtime proof:

~~~json
{
  "type": "relay.register.v3",
  "protocol_version": 3,
  "node_id": "uuid",
  "endpoint_ids": ["uuid"],
  "runtime_credential_id": "uuid",
  "nonce_proof": "...",
  "telemetry": {}
}
~~~

The server verifies that WebSocket authentication and registration refer to the
same runtime credential. A relay-supplied user ID or ACL is rejected.

### 10.4 One daemon, multiple endpoints

The initial implementation may open one WebSocket per endpoint while retaining
one daemon process. This already supports isolated roots without protocol-level
multiplexing.

Protocol v3 should nevertheless use endpoint IDs in every message so a later
multiplexed connection does not change authorization semantics.

### 10.5 Request envelope

Server-to-relay requests gain immutable context:

~~~json
{
  "type": "relay.request.v3",
  "request_id": "uuid",
  "endpoint_id": "uuid",
  "lease_token": "opaque-short-lived-token",
  "operation": "filesystem.read",
  "context": {
    "conversation_id": "uuid",
    "agent_name": "codex",
    "audit_id": "uuid"
  },
  "args": {}
}
~~~

Do not send roles, groups, or unnecessary personal metadata to the relay. The
server is the authorization authority; the relay uses the lease as a bounded
channel credential and for local auditing.

### 10.6 Protocol negotiation

- protocol v2 means legacy, single-user semantics;
- protocol v3 is required for ACL-shared use;
- unsupported required capabilities fail closed;
- a v2 connection cannot back an ACL-shared endpoint;
- version and feature flags are visible to admins.

## 11. Relay access service

Create a central `RelayAccessService` with APIs such as:

~~~python
list_endpoints(principal, conversation_id="", include_shared=True)
get_endpoint(endpoint_id, principal, permission="discover")
authorize(endpoint_id, principal, permission, conversation_id="", binding_id="")
create_binding(endpoint_id, principal, conversation_id, agent_name, permissions)
resolve_binding(binding_id, principal, permission)
issue_lease(binding_id, principal, permissions, ttl)
revoke_leases(endpoint_id="", user_id="", binding_id="")
~~~

Public request paths must not call raw `ServiceRegistry.resolve()` as an
authorization shortcut. The registry remains lifecycle and transport storage;
the access service is the policy boundary.

Checks occur at:

- catalog listing;
- direct endpoint detail;
- link and default selection;
- tool schema construction;
- every filesystem, exec, screen, automation, and proxy operation;
- inverse mount/channel creation;
- token or lease renewal.

## 12. Shared workspace mode

`shared_workspace` means every authorized principal operates on the same remote
root and can observe effects permitted by the endpoint capabilities.

Requirements:

- prominent UI label that the workspace and processes are shared;
- endpoint-level concurrency quotas;
- per-request audit identity;
- no assumption that files or process state are private;
- explicit grants for write and execution;
- explicit grants for host-local and desktop operations;
- no personal PawFlow inverse mounts in v1;
- no automatic consumer secret injection.

This is the first supported sharing mode because its data-sharing semantics are
honest and implementable without pretending one root is tenant-isolated.

## 13. Endpoint-isolated mode

One daemon may export several endpoint profiles, each with its own root and
capability set:

~~~text
node workstation-01
|-- endpoint team-project      -> /srv/team/project
|-- endpoint alice-private     -> /srv/users/alice
`-- endpoint read-only-mirror  -> /srv/mirrors/repo
~~~

Requirements:

- roots are configured locally and referred to by opaque profile IDs;
- no request may switch an endpoint to another root;
- file descriptor, process, terminal, browser, and automation registries are
  keyed by endpoint and connection;
- Docker containers spawned by the relay carry node/endpoint labels;
- cleanup operates on exact endpoint labels, never broad name prefixes;
- endpoint ACLs are independent even when the node is the same.

This is the recommended model when the goal is infrastructure sharing without
workspace sharing.

## 14. Tenant-dynamic mode

`tenant_dynamic` is deferred. If implemented later, it requires:

- a server-issued lease for every tenant channel;
- a server-approved root namespace per lease;
- independent fd, process, terminal, browser, and mount namespaces;
- per-principal and per-conversation quotas;
- forced cleanup when a lease expires or is revoked;
- no mutable current-user field on either side;
- adversarial concurrency tests.

The UI and protocol must not label `shared_workspace` as multi-tenant.

## 15. Inverse filesystem and mount isolation

### 15.1 Remove mutable identity

Replace `RelayService.set_user_id()` for relay authorization with an immutable
request/channel context. Do not reset cached handlers when another user arrives;
remove the shared mutable state entirely.

### 15.2 Existing inverse mounts

Current `sfs.*`, `ffs.*`, and `skfs.*` messages contain no tenant channel. The
handler derives identity from the owning service instance.

For shared endpoints in v1:

- reject creation of personal cc-sessions, FileStore, or user-skills mounts;
- allow only endpoint-wide data explicitly designed to be shared;
- return a clear `EACCES`/unsupported-mode error rather than falling back to an
  empty or global identity.

### 15.3 Future tenant channels

A future mount handshake may create:

~~~json
{
  "mount_channel_id": "uuid",
  "lease_token": "opaque",
  "kind": "filestore",
  "conversation_id": "uuid"
}
~~~

Every inverse callback carries `mount_channel_id`; the server maps it to an
immutable principal and rejects expired channels. One user must never be able to
walk another user's synthesized root.

## 16. Discovery, precedence, and defaults

### 16.1 Filter before merge

Apply ACL filtering before any scope merge, deduplication, default selection, or
connection attempt. An invisible endpoint behaves as absent and cannot shadow a
visible candidate.

### 16.2 Stable references

- persistent bindings store `endpoint_id` and `binding_id`;
- service aliases are display and compatibility fields;
- direct tool parameters may accept an alias only after resolving it within the
  principal's authorized catalog;
- ambiguous aliases fail rather than select by scope accident.

### 16.3 No implicit activation

ACL-shared global endpoints:

- appear in a shared/available catalog;
- are not automatically linked;
- are not automatically made default;
- are excluded from "any filesystem service" fallback unless an explicit
  compatibility flag is enabled by an administrator;
- require explicit conversation or agent binding.

Own-user legacy relays retain their current behavior during migration.

## 17. Secrets and variables

### 17.1 Relay credentials

- enrollment secrets are stored hashed and shown once;
- remote runtime tokens are short-lived and stored hashed where only
  verification is required;
- managed-server relay tokens may require recoverable encrypted storage while
  launching a container, but must never be stored as ordinary visible service
  config;
- service-detail and catalog APIs return only credential ID, prefix, state, and
  timestamps;
- logs redact Authorization headers, tokens, proofs, and lease values.

### 17.2 PawFlow user secrets

Granting relay access never grants secret access. Shared-relay operations do not
receive the endpoint creator's secret scope.

If a consumer deliberately injects one of their secrets into a remote command:

- require an explicit secret binding and consent;
- require a dedicated permission such as `secrets.inject` if the feature ships;
- warn that the remote host operator can observe the value;
- inject only named bound values, never the full consumer secret map;
- redact values from results, errors, transcripts, and audit details;
- do not cache resolved values in endpoint or binding records.

Automatic secret injection is disabled for shared endpoints in v1.

## 18. Proxy, screen, automation, and host execution

These operations cross a machine-wide boundary and need extra controls:

- relay proxy tokens remain bound to user, conversation, endpoint, permission,
  and expiry;
- proxy resolution rechecks the ACL and binding, not only endpoint existence;
- `exec.host` is distinct from `exec.container`;
- `allow_local` requires both endpoint capability and ACL permission;
- screen observation and control are separate permissions;
- automation sessions are keyed by endpoint and lease;
- endpoint disconnect closes proxy streams, browser sessions, terminals, and
  desktop-control channels;
- concurrent control sessions may be disabled or serialized by endpoint policy.

## 19. Revocation and lifecycle

### 19.1 Credential revocation

Revoking an enrollment credential prevents future enrollment and renewal. The
admin chooses whether it also revokes nodes and active runtime credentials
created from it.

### 19.2 Node and connection revocation

- revoking a node closes every connection and disables its endpoints;
- revoking one runtime credential closes only connections authenticated by it;
- disconnect marks endpoints unavailable but keeps definitions and bindings for
  diagnostics and future reconnect;
- duplicate live connections follow an explicit replace, reject, or standby
  policy.

### 19.3 ACL and membership revocation

An ACL or group-membership change:

1. increments the relevant revision;
2. invalidates affected leases;
3. closes affected live operation channels;
4. revokes relay proxy tokens;
5. marks or disables bindings;
6. tears down tenant-specific mounts;
7. evicts tool and service-resolution caches;
8. emits UI refresh events.

Disconnect alone is not ACL revocation. Reconnect does not restore access that
was removed while the endpoint was offline.

## 20. Concurrency and quotas

Shared endpoints require limits at multiple levels:

- maximum active leases per user and endpoint;
- maximum concurrent exec operations;
- maximum terminals, browser sessions, and desktop controllers;
- queue length and wait timeout;
- filesystem request and payload limits;
- per-user and per-conversation accounting;
- endpoint-wide emergency circuit breaker.

Fairness keys use stable user IDs, not connection order. A user must not exhaust
all endpoint workers indefinitely.

## 21. Audit and observability

Audit records include:

- audit/event ID;
- timestamp;
- node, endpoint, connection, binding, and lease IDs;
- actor user ID or machine credential ID;
- conversation and agent context where applicable;
- operation class and decision;
- matched policy revision and permission;
- endpoint revision and effective capability set;
- reason code for denial or revocation;
- duration, byte counts, and exit status where safe.

Do not log:

- enrollment or runtime secrets;
- lease tokens;
- secret environment values;
- command output by default;
- sensitive paths in non-admin catalog events.

Metrics:

- enrollments accepted/rejected/pending;
- connected nodes and endpoints;
- authorization denies by reason;
- active leases and renewals;
- operations and latency by capability;
- forced disconnects and revocations;
- endpoint queue saturation;
- protocol-version distribution.

## 22. Persistence

Suggested server-owned layout:

~~~text
data/system/relay_access/nodes/<node_id>.json
data/system/relay_access/endpoints/<endpoint_id>.json
data/system/relay_access/credentials/<credential_id>.json
data/system/relay_access/policies/<endpoint_id>.json
data/system/relay_access/policy_templates/<template_id>.json
data/system/relay_access/index.json
~~~

Bindings remain with conversation metadata after migration but contain exact
IDs. Runtime credentials, live connections, and leases are primarily in-memory.

Writes use atomic replace, schema versions, file permissions, and inter-process
locking consistent with other PawFlow system stores. The index is rebuildable
and is never the policy source of truth.

## 23. API and UI surface

### 23.1 Administrative APIs

- create/list/revoke enrollment credentials;
- create/update policy templates;
- list/inspect/revoke nodes;
- approve or reject pending endpoints;
- update endpoint ACLs and capability ceilings;
- force disconnect or rotate runtime credentials;
- inspect audit history and protocol compatibility.

Only one-time credential creation returns raw secret material.

### 23.2 User APIs

- list ACL-visible endpoints;
- inspect safe endpoint metadata and sharing warnings;
- bind/unbind an endpoint to a conversation or agent;
- select a default among linked endpoints;
- request a subset of granted capabilities;
- review and accept endpoint changes;
- view connection state and their own recent operations.

### 23.3 CLI/Desktop

Suggested commands:

~~~text
pawflow-relay enroll --server <profile> --key-file <path>
pawflow-relay node status
pawflow-relay endpoint add <name> --path <path> --mode ro|rw
pawflow-relay endpoint list
pawflow-relay start [endpoint]
pawflow-relay credentials rotate
~~~

The desktop client stores credentials in the OS keychain before this feature is
declared stable. Local JSON may contain only non-secret metadata and keychain
references.

## 24. Compatibility and migration

### 24.1 Existing relays

For every existing relay definition:

- generate an endpoint UUID;
- preserve the service ID as alias;
- classify it as `legacy_user`, `legacy_global`, or `server_managed`;
- retain the existing connection token during the compatibility window;
- create a policy matching current visibility;
- mark protocol v2 and prohibit cross-user sharing until upgraded;
- migrate bindings from relay name to endpoint ID when unambiguous.

Existing global relays may receive an authenticated-user compatibility policy
to avoid silent breakage, but must be visibly marked legacy/open. New
machine-created global endpoints default to restricted or pending.

### 24.2 Dual protocol

- v2 routes and messages remain for personal relays;
- v3 connection and request envelopes are used for enrolled/shared endpoints;
- the registry exposes a compatibility projection so existing filesystem tool
  handlers can resolve an endpoint transport;
- new bindings always use endpoint IDs;
- old name bindings are rewritten lazily and transactionally;
- rollback leaves v2 personal relay behavior intact.

### 24.3 Service registry

Do not overload `scope_id` with the currently calling user for a shared global
endpoint. The live transport belongs to an endpoint ID. Authorization occurs in
`RelayAccessService`, and request context is passed separately.

## 25. Implementation work packages

### WP0. Authorization dependency

- implement or reuse immutable `PrincipalContext`;
- implement canonical durable groups and membership revisions;
- extract a reusable policy evaluator from the resource ACL work;
- define relay-specific permissions and reason codes;
- add audit primitives and revision-aware cache keys.

Exit gate: no relay sharing code accepts a raw user ID as sufficient proof.

### WP1. Credential store and admin management

- add relay enrollment credential models and persistence;
- hash secrets and show them once;
- validate scopes, limits, expiration, and revocation;
- add admin APIs/UI and audit;
- add rate limits and brute-force protections;
- leave generic API keys unchanged and unauthorized for enrollment.

### WP2. Node and endpoint registry

- add node/endpoint stores and stable UUID indexes;
- implement idempotent enrollment and approval;
- implement capability ceilings and root fingerprint review;
- project enrolled endpoints into service lifecycle where compatibility needs
  it;
- enforce alias and route uniqueness.

### WP3. Protocol v3 and runtime credentials

- add the central v3 WebSocket route;
- implement runtime credential exchange and rotation;
- bind upgrade authentication to registration;
- add protocol negotiation, heartbeats, reconnect policy, and connection IDs;
- support one daemon opening connections for several endpoints;
- update the canonical `pawflow_relay` package first, then regenerate/sync the
  desktop runtime copy through the repository's packaging process.

### WP4. Relay access and bindings

- implement `RelayAccessService`;
- filter discovery before merge;
- migrate binding records to endpoint/binding UUIDs;
- enforce ACL at bind, default selection, schema construction, resolution, and
  every operation;
- disable implicit fallback for shared endpoints;
- propagate policy and membership revisions into caches.

### WP5. Request-scoped identity and leases

- remove relay authorization dependence on mutable `_user_id`;
- add immutable request contexts;
- issue, renew, and revoke operation/channel leases;
- add endpoint ID and audit ID to protocol requests;
- key pending operations and process registries by connection and endpoint;
- rework relay proxy tokens to include endpoint, binding, permission, and policy
  revision.

### WP6. Shared-workspace release

- expose global restricted endpoints to granted principals;
- require explicit binding and sharing acknowledgement;
- enforce read/write/exec capability separation;
- add quotas and audit;
- reject personal inverse mounts;
- ship UI warnings and operational documentation.

### WP7. Multiple isolated endpoints per daemon

- add local endpoint profiles and root confinement;
- support multiple endpoint connections from one daemon;
- isolate fd/process/terminal/browser state;
- add exact-label Docker lifecycle management;
- add per-endpoint health and capacity reporting.

### WP8. Tenant-aware inverse mounts

- design mount-channel leases;
- add channel IDs to every inverse callback;
- create per-principal or per-conversation virtual roots;
- revoke and unmount on policy, membership, connection, or lease changes;
- retain fail-closed behavior for legacy protocol messages.

This work package may ship after the rest and is not required for shared remote
workspace use.

### WP9. Hardening and cleanup

- rotate or migrate legacy static tokens;
- remove plaintext relay credentials from service-detail surfaces;
- remove deprecated query-string auth after telemetry confirms no clients need
  it;
- remove name-only bindings;
- remove v2 cross-user code paths;
- update security, deployment, relay client, and administrator documentation.

## 26. Test plan

### 26.1 Credential tests

- raw secrets are displayed once and never persisted;
- wrong, expired, revoked, exhausted, or rate-limited keys fail;
- a generic API key cannot enroll;
- a user-scoped credential cannot create a global endpoint;
- a global credential cannot exceed alias, endpoint, node, or capability limits;
- credential comparison is constant-time;
- concurrent use respects max-node and max-endpoint limits atomically.

### 26.2 Enrollment tests

- first enrollment creates stable IDs;
- reconnect is idempotent;
- changed installation ID creates or rejects according to policy;
- capability reduction applies immediately;
- capability increase and root changes become pending;
- relay-supplied ACL, owner, role, or user fields are rejected;
- duplicate alias and route conflicts fail deterministically.

### 26.3 ACL tests

- exact user, group, and role grants;
- disabled or deleted subjects do not match;
- invisible endpoints do not appear or shadow another candidate;
- discover without link cannot bind;
- link without operation permission cannot execute;
- admin override is explicit and audited;
- membership revision invalidates leases and caches.

### 26.4 Isolation and concurrency tests

- Alice and Bob concurrently use one shared endpoint without mutable identity;
- no cached server/filestore/skills handler retains another principal;
- personal inverse mount attempts on shared endpoints fail closed;
- process, fd, terminal, browser, and pending-request state is isolated by
  endpoint;
- disconnect cancels only the affected connection's work;
- one endpoint cannot address another endpoint's root;
- root escape and symlink attacks remain rejected.

### 26.5 Capability tests

- read cannot write;
- write does not execute;
- container execution does not imply host execution;
- screen observation cannot control;
- proxy permission is independently enforced;
- conversation permission modes can further restrict endpoint permissions;
- server and relay both reject unknown operations.

### 26.6 Revocation tests

- credential, runtime credential, node, endpoint, policy, group membership, and
  binding revocation each affect the intended scope;
- active leases and proxy streams terminate;
- revoked bindings do not resume after reconnect;
- cache invalidation reaches tool registries and listings;
- UI receives state-change events.

### 26.7 Integration tests

- unattended enrollment through HTTPS and WebSocket v3;
- admin-created restricted global endpoint shared with two users;
- user without a grant receives not-found behavior;
- one daemon exports a shared endpoint and two private endpoints;
- CLI/Desktop restart preserves node identity and reconnects safely;
- legacy browser-authenticated relay remains functional;
- mixed v2/v3 deployments fail closed for cross-user use.

### 26.8 Security tests

- stolen runtime token cannot create endpoints or policies;
- stolen enrollment key cannot exceed its ceiling;
- replayed registration proof fails;
- query/header/log redaction contains no secrets;
- forged user, group, role, conversation, binding, lease, or endpoint IDs fail;
- TOCTOU policy changes during long operations terminate or reauthorize as
  specified;
- a malicious relay cannot request another user's FileStore or skills tree.

## 27. Rollout phases and gates

| Phase | Content | Cross-user enabled? | Gate |
|---|---|---:|---|
| R0 | Principal, groups, policy evaluator, audit | No | Authorization dependency complete |
| R1 | Enrollment credentials, nodes, endpoints, admin approval | No | Machine identity and secret review |
| R2 | Protocol v3, runtime credentials, stable endpoint IDs | No | Replay/reconnect tests pass |
| R3 | RelayAccessService, UUID bindings, leases, mutable-user removal | No | Concurrency security review |
| R4 | Restricted global shared-workspace endpoints | Yes | No personal inverse mounts; capability tests pass |
| R5 | Multiple isolated endpoints per daemon | Yes | Root/process isolation tests pass |
| R6 | Tenant-aware inverse mount channels | Yes | Adversarial FUSE and revocation review |
| R7 | Legacy cleanup and default hardening | Yes | Migration telemetry and rollback window complete |

Do not enable cross-user sharing through a feature flag before R3 is complete.

## 28. Acceptance criteria

The feature is complete when:

1. an admin can issue a scoped, expiring relay enrollment credential whose raw
   value is shown once and not persisted;
2. a remote daemon can enroll non-interactively and obtain stable node and
   endpoint IDs;
3. only a credential with explicit global-create authority can request a global
   endpoint;
4. the server, not the relay, chooses the global endpoint policy;
5. two granted users can bind and use one intentionally shared workspace;
6. an ungranted user cannot discover or address that endpoint;
7. one daemon can expose multiple independently authorized roots;
8. filesystem, container execution, host execution, proxy, automation, and
   screen permissions are independently enforced;
9. no live relay service stores a mutable current user for authorization;
10. personal server, FileStore, and skills mounts cannot cross users;
11. policy or membership revocation invalidates active access without server
    restart;
12. secrets, tokens, and credential material remain absent from listings, logs,
    audit details, and ordinary service configuration;
13. existing personal relays continue to work during the documented migration
    window;
14. all adversarial concurrency, reconnect, revocation, and isolation tests pass.

## 29. Recommended decisions

Adopt these defaults unless implementation evidence requires a change:

1. use a relay-specific enrollment credential rather than extending generic API
   keys;
2. keep machine enrollment authority separate from human endpoint management;
3. create machine-enrolled global endpoints as restricted or pending, never
   public by default;
4. use stable endpoint UUIDs and explicit conversation bindings;
5. ship intentionally shared workspaces before claiming dynamic multi-tenancy;
6. support one daemon with several isolated endpoints before one endpoint with
   dynamic per-user roots;
7. require protocol v3 and request-scoped leases for every ACL-shared endpoint;
8. remove mutable `_user_id` relay authorization before enabling sharing;
9. disable personal inverse mounts and automatic secret injection on shared
   endpoints in the first release;
10. filter ACLs before resolution and disable implicit relay fallback for shared
    global endpoints.


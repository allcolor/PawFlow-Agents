# Remote Relay Storage and CLI Execution Implementation Plan

Status: **proposed** (architecture and implementation plan only; no runtime
implementation yet).

This plan describes how a connected remote relay can become a temporary PawFlow
data-plane provider for:

- authoritative conversation storage;
- repository resource collections;
- Docker-based CLI LLM execution.

Remote conversations and resources are projected into PawFlow's normal catalogs
while their source is attached. They become unavailable and leave active
discovery when the relay disconnects; their authoritative bytes are not copied
into the server's local stores.

The plan depends on:

- `docs/REMOTE_CLI_COMPUTE_POOL_PLAN.md` for the authoritative, standalone v1
  design of remote CLI Docker endpoints, pools, PawFlow/Relay Desktop UI, runtime
  lifecycle, and credential custody; where its CLI details differ from this
  broader plan, the compute-pool plan wins;
- `docs/REMOTE_RELAY_ENROLLMENT_SHARING_PLAN.md` for node/endpoint identity,
  enrollment credentials, ACLs, leases, capabilities, and revocation;
- `docs/RESOURCE_ACL_SHARING_PLAN.md` for stable resource identity,
  `PrincipalContext`, canonical groups, explicit bindings, and secret safety;
- `docs/CONVERSATION_SHARING_PLAN.md` for conversation authorization;
- `docs/RELAY_WORKSPACE_FS_PLAN.md` for the optional remote-to-remote workspace
  fallback;
- `docs/design/encryption-at-rest.md` for conversation and workspace key
  boundaries.

## 1. Goal

Support deployments where the PawFlow server remains the control plane while
selected storage and compute live on one or more remote relay machines.

Concrete target scenarios:

1. A user connects a relay from their workstation and configures a CLI-backed
   `llmConnection` so its Docker container runs on that workstation instead of
   the PawFlow server host.
2. A relay exports conversations stored on its own disk. PawFlow lists and opens
   those conversations while the relay is connected and stops exposing them
   when it disconnects.
3. A relay exports agents, skills, MCP definitions, tools, prompts, or other
   repository resources. PawFlow merges them into an authorized catalog without
   copying their authoritative content to the server.
4. One relay exposes workspace, conversation storage, resource storage, and CLI
   execution together so the data and Docker containers stay co-located.
5. Different relays provide different planes, with explicit routing and no
   accidental fallback to local storage or execution.

### 1.1 Known limitation to state up front

Conversations with encryption at rest enabled cannot use a remote home until
the DEK/storage mode of section 29.2 is implemented and audited, which is phase
D11 — the last one. Every earlier phase delivers remote storage only for
unencrypted conversations.

Raise this with product before D1, not at D11. If the deployments motivating
this work have encryption enabled, the entire conversation-storage half of this
plan delivers nothing to them until its final phase, and the CLI execution half
(D7-D8) may be the only part worth building.

## 2. Meaning of remote, merge, and disappear

### 2.1 Remote storage

Remote storage means the relay-side backend is authoritative. The PawFlow server
may retain minimal control-plane records, revisions, hashes, audit information,
and bounded ephemeral caches, but it does not become a second writable replica.

### 2.2 Merge

Merge means an authorized catalog projection, not a filesystem merge and not
last-write-wins replication.

- Conversation lists are a union of local and attached remote descriptors.
- Resource catalogs are a union of local resources and attached remote resource
  references.
- Content is fetched lazily or operated on through typed RPC.
- Every entry retains stable source provenance.
- Persistent selections use stable IDs, never an unqualified display name.

### 2.3 Disappear

When a source disconnects:

- its conversations leave normal conversation listings;
- its resources leave discovery and tool registries;
- active objects become `source_offline` rather than falling through to a local
  object with the same name;
- writes fail closed;
- remote runtimes are fenced and eventually terminated;
- ephemeral content caches become inaccessible and are purged;
- durable bindings and locators may remain as unavailable references so a
  reconnect restores the same identity.

The server retains no user-visible ghost copy of remote content.

## 3. Scope

The implementation covers:

- relay capabilities for conversation storage, resource storage, and CLI
  execution;
- source attachment and detachment lifecycle;
- dynamic manifests and change feeds;
- a routed conversation backend abstraction;
- a routed resource backend abstraction;
- stable source/object identity and collision handling;
- exactly-once/idempotent remote mutations;
- write fencing and single authoritative homes;
- remote search or non-durable indexing;
- local and remote CLI runtime abstraction;
- Docker pool lifecycle on the relay host;
- batch and, later, interactive CLI providers;
- remote MCP/tool/event bridging;
- workspace, skills, session, and attachment materialization;
- ACL, secrets, encryption, audit, migration, testing, and rollout.

## 4. Non-goals for the first release

The first release does not provide:

- transparent multi-master conversation replication;
- offline writes queued on the PawFlow server;
- conflict-free merging of two writable conversation copies;
- filesystem-level mounting of a remote conversation repository into the
  PawFlow server process;
- automatic migration of all data related to a conversation;
- remote hosting of PawFlow users, sessions, server security config, global
  secret stores, budgets, or audit logs;
- arbitrary Docker-socket proxy access;
- arbitrary server-supplied host bind paths on a relay;
- silent execution fallback from a relay to the PawFlow server;
- silent storage fallback from a remote home to a local same-name object;
- use of a disconnected remote source through stale cached content;
- remote execution for API-only LLM providers;
- interactive CLI providers in the first compute milestone;
- cross-source distributed transactions involving several relays;
- hiding the fact that the relay operator can inspect data and secrets processed
  on their machine;
- remote homes for conversations encrypted at rest (see section 1.1 and 29.2).

## 5. Existing architecture and why a mount is insufficient

### 5.1 ConversationStore is a local transactional subsystem

`ConversationStore` currently owns a local directory tree and combines:

- segmented JSONL transcripts;
- shared and per-agent contexts;
- extras and metadata;
- per-conversation and extras locks;
- append-handle and context caches;
- monotonic transcript generation;
- FIFO `ConversationWriter` queues;
- Git repositories, branches, tags, rollback, fork, and retention;
- encryption codecs and DEK state;
- cache reconciliation by scanning directories.

`ConversationWriter` guarantees that SSE publication follows persistence, but
its current background loop logs write failures and still releases wait events.
Remote storage requires explicit success/error receipts so callers never mistake
a failed remote write for durable persistence.

Confirmed in the current code: `core/conversation_writer.py:419-422` catches
the write exception, logs it, and advances the loop; the batch completion block
at `core/conversation_writer.py:438-447` then calls `evt.set()` on every queued
item unconditionally. A caller waiting on that event cannot distinguish a
durable write from a failed one.

### 5.1.1 Prerequisite fix, independent of this plan

This is a present-day local defect. It silently loses messages on any write
error today, with no relay involved, and it must be fixed on its own schedule
rather than as part of a remote-storage programme.

Minimum standalone correction:

- record per-item success or failure during the batch;
- attach the outcome to the item before releasing its wait event;
- propagate failure to the caller and to the active turn instead of returning
  as if persisted;
- do not publish SSE for an item that failed to persist;
- add a test in which the backend write raises and the caller observes an
  error.

WP3 later extends this into operation IDs, receipts, fencing, and unknown
outcomes. The correction above does not depend on WP3 and should ship first.

### 5.2 Conversation search is a local derived database

`ConversationIndex` maintains a per-user SQLite FTS database. It calls
`list_conversations()`, reads changed transcripts, tracks generation and row
watermarks, and purges data when a source cannot be read.

Persisting a plaintext index for an attached remote source would create a local
copy that can outlive the source and may violate its storage or encryption
policy.

### 5.3 Resources are path-oriented

`ScopedRepository` directly reads and writes files under
`data/repository/<type>/<scope>`. Resources may be JSON, Markdown, or complete
directories containing skill assets, themes, skins, and versioned flows.

`ResourceStore.list_all()` merges by name in global, user, conversation order.
The current facade has no source identity and assumes every item is locally
addressable through a `Path`.

### 5.4 CLI pools execute Docker locally

Claude, Codex, Gemini, and interactive pools call local `docker run`,
`docker exec`, `docker cp`, `docker inspect`, and `docker rm`. They retain local
container names and `subprocess.Popen` objects.

They also assume:

- server paths can be bind-mounted directly;
- CLI session trees exist under local `data/runtime/sessions/*`;
- workspace and skills mounts are valid on the server Docker host;
- bridge scripts can be bind-mounted or copied from the PawFlow source tree;
- `host.docker.internal` reaches the PawFlow listener;
- internal-auth bearer tokens are safe for same-host components;
- live registries can inspect and kill local container PIDs.

None of those assumptions holds automatically on a remote Docker host.

### 5.5 FUSE is the wrong conversation-store abstraction

Mounting a relay directory under `data/runtime/conversations` would preserve the
server's path calls but break the actual consistency model:

- server-side locks do not fence other writers on the relay;
- append handles and directory signatures survive network state changes;
- Git and SQLite behavior over the relay filesystem is fragile;
- disconnects can leave blocked syscalls or half-visible directories;
- retrying an append after an unknown outcome can duplicate a message;
- authorization becomes path-based rather than operation-based;
- protocol upgrades cannot validate schema or storage revision.

Use high-level, versioned storage RPC with explicit revisions, operation IDs,
receipts, and fencing.

## 6. Architectural separation

PawFlow remains the **control plane**:

- authenticates users and machines;
- evaluates conversation, resource, service, and relay ACLs;
- routes conversations and resources to authoritative backends;
- orchestrates agent turns;
- owns budgets, usage accounting, scheduling, SSE, and audit;
- issues storage, content, and execution leases;
- decides placement and fallback policy.

The relay becomes an optional **data plane**:

- persists conversation bundles;
- persists resource collections;
- advertises manifests and change generations;
- executes typed storage operations;
- manages Docker images, containers, processes, PTYs, and cleanup;
- exposes local workspace roots and materialized assets;
- enforces its own capability ceiling in addition to server authorization.

~~~text
browser / API / channel
          |
          v
PawFlow control plane
  |-- authorization + routing + SSE + audit
  |-- local conversation/resource backends
  `-- relay transport
          |
          v
Remote relay data plane
  |-- conversation backend
  |-- resource backend
  |-- workspace endpoint
  `-- CLI Docker runtime
~~~

## 7. Non-negotiable invariants

1. Every conversation has exactly one authoritative writable home.
2. Every writable resource scope/type tuple has at most one authoritative home.
3. A remote catalog attachment is not automatically a writable scope home.
4. Server and relay never write the same conversation store concurrently.
5. Every remote mutation has an idempotency key and a durable receipt.
6. Every writer holds a source-issued fencing epoch; stale epochs are rejected.
7. User-visible events are emitted only after the authoritative backend
   acknowledges persistence.
8. A disconnected backend is unavailable; no local fallback is inferred.
9. Stable references include source and object identity.
10. Resource-name collisions never select by connection order.
11. The relay cannot assert PawFlow ownership, audience, groups, roles, or ACLs.
12. Manifests are untrusted input and are schema-, size-, hash-, and
    authorization-validated.
13. Remote content never acquires implicit access to server or consumer secrets.
14. Remote Docker receives a typed launch specification, never raw access to a
    server-controlled Docker socket.
15. Remote execution placement is explicit and fail-closed.
16. Runtime credentials and leases are scoped to node, endpoint, user,
    conversation, provider, and operation.
17. A remote source regression or identity collision is quarantined, not merged.
18. Cached content is not authoritative and cannot remain usable after source
    detachment.
19. Local and remote implementations obey the same public backend contracts.
20. Server control-plane data remains local unless separately designed and
    authorized.

## 8. Relay capabilities

Extend endpoint capabilities from the relay enrollment plan with:

### 8.1 Conversation storage

- `storage.conversation.list`;
- `storage.conversation.read`;
- `storage.conversation.write`;
- `storage.conversation.git`;
- `storage.conversation.search`;
- `storage.conversation.encrypted`;
- `storage.conversation.files` when FileStore is also hosted remotely.

### 8.2 Resource storage

- `storage.resource.manifest`;
- `storage.resource.read`;
- `storage.resource.write`;
- `storage.resource.directory`;
- `storage.resource.flow_versions`;
- `storage.resource.watch`.

### 8.3 CLI execution

- `runtime.cli.batch`;
- `runtime.cli.interactive`;
- `runtime.cli.claude`;
- `runtime.cli.codex`;
- `runtime.cli.gemini`;
- `runtime.docker.pull`;
- `runtime.workspace.local`;
- `runtime.workspace.remote_mount`;
- `runtime.pty`;
- `runtime.events`.

Capability advertisement is intersected with the enrollment ceiling and the
server attachment policy. It is not authorization by itself.

## 9. Core domain model

### 9.1 DataPlaneSource

One enrolled relay endpoint may expose one or more source services.

~~~json
{
  "source_id": "uuid",
  "endpoint_id": "uuid",
  "source_kind": "conversation_store",
  "protocol_version": 1,
  "storage_format_version": 1,
  "source_generation": 42,
  "root_fingerprint": "sha256:...",
  "capabilities": ["storage.conversation.read", "storage.conversation.write"],
  "state": "attached",
  "connected_at": "ISO-8601"
}
~~~

`source_id` remains stable across reconnects. One endpoint can expose distinct
conversation, resource, and runtime sources.

### 9.2 StorageAttachment

An attachment is the server-controlled projection of a source.

~~~json
{
  "attachment_id": "uuid",
  "source_id": "uuid",
  "endpoint_id": "uuid",
  "kind": "conversation_catalog",
  "target": {
    "scope": "user",
    "owner_user_id": "user-id",
    "conversation_id": ""
  },
  "mode": "catalog",
  "read_only": false,
  "policy_id": "uuid",
  "priority": 100,
  "attachment_revision": 3,
  "state": "attached"
}
~~~

The relay may request attachment, but only the server chooses `target`, `mode`,
policy, and write authority.

### 9.3 ConversationLocator

~~~json
{
  "conversation_id": "globally-stable-uuid",
  "home_kind": "remote",
  "source_id": "uuid",
  "remote_object_id": "opaque-stable-id",
  "owner_user_id": "server-authoritative-user-id",
  "conversation_revision": 18,
  "transcript_generation": 4,
  "last_digest": "sha256:...",
  "home_epoch": 7,
  "policy_id": "uuid",
  "state": "available"
}
~~~

The server persists the locator and last accepted generation/digest but not the
conversation content. Normal listings include it only while the source is
attached and authorized.

### 9.4 RemoteConversationDescriptor

Manifest descriptors contain safe metadata only:

~~~json
{
  "remote_object_id": "opaque-id",
  "conversation_id": "uuid",
  "title": "Project",
  "preview": "optional-policy-controlled-preview",
  "message_count": 125,
  "created_at": 0,
  "updated_at": 0,
  "conversation_revision": 18,
  "transcript_generation": 4,
  "content_digest": "sha256:...",
  "encryption_state": "plaintext-or-encrypted"
}
~~~

Relay-provided owner and ACL fields are ignored. The attachment supplies the
server-side owner/audience mapping.

### 9.5 ResourceCollection

~~~json
{
  "collection_id": "uuid",
  "source_id": "uuid",
  "resource_types": ["agent", "skill", "mcp", "tool", "prompt"],
  "collection_generation": 23,
  "mode": "catalog",
  "target_scope": "user",
  "target_owner_user_id": "user-id",
  "read_only": false,
  "policy_id": "uuid"
}
~~~

### 9.6 RemoteResourceDescriptor

~~~json
{
  "resource_id": "uuid",
  "remote_object_id": "opaque-id",
  "resource_type": "skill",
  "name": "deploy-acme",
  "description": "...",
  "revision": 6,
  "content_hash": "sha256:...",
  "directory": true,
  "size_bytes": 12345,
  "risk_summary": {},
  "requirements": {}
}
~~~

The manifest does not include secrets, resolved variables, literal environment
maps, or arbitrary ACL metadata.

### 9.7 CliExecutionTarget

~~~json
{
  "execution_target_id": "uuid",
  "source_id": "uuid",
  "endpoint_id": "uuid",
  "node_id": "uuid",
  "providers": ["claude-code", "codex-app-server", "gemini-acp"],
  "images": {"claude-code": "sha256:..."},
  "max_runtimes": 8,
  "capabilities": ["runtime.cli.batch"],
  "state": "available"
}
~~~

### 9.8 RemoteRuntimeHandle

~~~json
{
  "runtime_id": "uuid",
  "execution_target_id": "uuid",
  "runtime_epoch": 2,
  "lease_id": "uuid",
  "provider": "codex-app-server",
  "conversation_id": "uuid",
  "agent_name": "codex",
  "container_ref": "relay-private-reference",
  "process_ref": "relay-private-reference",
  "state": "running",
  "created_at": "ISO-8601",
  "lease_expires_at": "ISO-8601"
}
~~~

Container names and host PIDs are relay implementation details. Server live
registries store a provider-neutral runtime handle.

## 10. Attachment lifecycle

### 10.1 Registration

After relay protocol authentication, the endpoint advertises data-plane source
descriptors. The server matches them against enrollment and attachment policy.

The source cannot become visible until:

1. endpoint and source identities are valid;
2. protocol and storage versions are compatible;
3. capabilities fit the credential ceiling;
4. target scope and audience are resolved server-side;
5. a full manifest snapshot validates;
6. generation/digest monotonicity checks pass;
7. ACL and source leases are installed;
8. caches subscribe to the new attachment generation.

### 10.2 Snapshot protocol

Use a bounded, resumable manifest sequence:

~~~text
manifest.begin(source_id, generation, item_count, digest)
manifest.page(cursor, descriptors...)
manifest.end(generation, digest)
~~~

Do not publish partial snapshots. Build a candidate index, validate it, then
atomically swap the attachment into the live catalog.

### 10.3 Change feed

After the snapshot, the source sends ordered changes:

~~~text
change_seq
source_generation
operation = upsert | delete
object descriptor
~~~

Gaps trigger snapshot resynchronization. Duplicate events are idempotent.

### 10.4 Detachment

On disconnect, lease expiry, revocation, or manifest failure:

1. atomically mark the source unavailable;
2. stop accepting new reads, writes, bindings, and runtimes;
3. fail or cancel pending operations;
4. invalidate source-tagged caches and indexes;
5. remove descriptors from normal catalogs;
6. publish source/conversation/resource availability events;
7. fence remote writers and runtimes;
8. retain only locators, last generation/digest, policies, bindings, and audit.

### 10.5 Reattachment

Reconnect uses the same source ID. Before restoring visibility:

- compare root fingerprint and storage format;
- reject generation regression unless an admin explicitly accepts a rollback;
- reconcile mutation receipts with operations whose outcome was unknown;
- acquire a new home/runtime epoch;
- rebuild the manifest atomically;
- revalidate ACLs and persistent bindings.

## 11. Conversation backend architecture

### 11.1 Router and backend contract

Refactor `ConversationStore` into an authorized/routed facade and backend
implementations:

~~~text
ConversationService / ConversationStore facade
                  |
                  v
ConversationHomeRouter
       |                         |
       v                         v
LocalConversationBackend   RelayConversationBackend
       |                         |
current JSONL/Git store       typed relay RPC
~~~

The public facade resolves a `ConversationLocator` once per operation and does
not expose backend paths.

Suggested backend operations:

~~~python
list_descriptors(principal, cursor=None)
exists(locator)
create(locator, initial_state, operation_id, fence)
load_messages(locator, cursor=None, limit=None)
append_messages(locator, messages, expected_revision, operation_id, fence)
patch_message(locator, msg_id, fields, expected_revision, operation_id, fence)
get_extras(locator, keys=None)
compare_and_set_extras(locator, patch, expected_revision, operation_id, fence)
load_context(locator, agent_name, cursor=None)
truncate(locator, boundary, expected_revision, operation_id, fence)
delete(locator, expected_revision, operation_id, fence)
git_operation(locator, command, args, expected_revision, operation_id, fence)
encryption_status(locator)
~~~

Avoid exposing arbitrary filenames or Git commands. `git_operation` uses a
closed operation enum for snapshot, log, diff, rollback, branch, tag, and fork.

### 11.2 Where conversation logic runs

The initial remote backend should run the same versioned conversation-store
implementation on the relay. The server sends high-level operations; the relay
owns on-disk sequencing, segmented JSONL writes, local locks, Git operations,
and fsync.

This avoids reproducing local file semantics over a network filesystem while
keeping one canonical implementation package shared by local and remote
backends.

The server continues to:

- create authenticated request context;
- construct canonical message IDs and timestamps;
- authorize operations;
- serialize its own per-conversation writer queue;
- publish SSE only after a successful remote receipt;
- maintain control-plane locators and audit.

### 11.3 ConversationWriter changes

`ConversationWriter` must return structured completion results:

~~~text
success | backend_unavailable | conflict | rejected | unknown_outcome
conversation_revision
transcript_generation
max_seq
operation_receipt
~~~

Required changes:

- never swallow a storage error and signal success;
- attach an `operation_id` to every queued mutation;
- preserve FIFO order across reconnect attempts;
- publish SSE only after the receipt is durable;
- stop/deactivate the writer when its home detaches;
- on unknown outcome, query the receipt before retrying;
- expose errors to the active turn and UI.

### 11.4 Exactly-once remote writes

Every mutation carries:

- `operation_id` generated by PawFlow;
- source and object IDs;
- expected conversation revision;
- home epoch/fencing token;
- authenticated lease;
- payload hash.

The remote backend persists a bounded deduplication ledger in the same durable
transaction boundary as the mutation. Repeating the same operation ID and hash
returns the original receipt. Reusing an ID with a different hash is rejected.

### 11.5 Fencing and single writer

When a source attaches, it issues a monotonically increasing `home_epoch` for
each writable conversation or collection. Every mutation carries that epoch.

- stale epochs fail;
- only one PawFlow attachment may hold the write lease;
- read-only attachments need no writer epoch;
- reconnect cannot reuse an old epoch;
- a second PawFlow server cannot silently become a concurrent writer.

### 11.6 Imported remote conversations

Conversation IDs should be UUIDs. For existing relay-native IDs, the server
creates a stable mapping from `(source_id, remote_object_id)` to a PawFlow
conversation UUID.

If a source advertises an already-known conversation UUID with a different
source lineage or digest, quarantine it as a collision. Do not merge histories
by UUID alone.

### 11.7 Conversation creation and migration

Creation accepts an explicit `home_attachment_id`. Default remains local unless
the user or administrator configured a remote default.

Moving an existing conversation is an explicit handoff:

1. lock and drain the current writer;
2. snapshot all in-scope conversation data;
3. import into the destination backend under an operation ID;
4. verify counts, hashes, generation, and Git refs;
5. atomically switch the locator/home epoch;
6. rebuild derived caches;
7. retain the old copy quarantined until confirmation or delete it explicitly.

Never implement migration as copy-then-immediate-delete without verification and
rollback metadata.

## 12. Conversation storage closure

"Conversation storage" has several possible boundaries. Define profiles rather
than implying that moving the transcript moves everything.

### 12.1 Core conversation bundle

Required for every remote conversation home:

- transcript segments;
- shared context;
- per-agent contexts;
- extras and conversation metadata;
- summaries and context buckets;
- Git history and retention markers;
- encryption metadata that belongs with stored ciphertext;
- mutation receipts and storage generations.

### 12.2 Conversation-owned companion data

Optional capabilities may also host:

- conversation-scoped resources;
- FileStore attachments;
- CLI provider session directories;
- project graph cache;
- plans or task artifacts.

These have independent stores today and require their own backend contracts. The
UI must state which profile is active:

- `conversation_core`;
- `conversation_with_resources`;
- `conversation_with_files`;
- `conversation_portable` when every supported companion store is included.

### 12.3 Server-retained control data

Remain on the PawFlow server:

- users and login sessions;
- ACL policies and canonical groups;
- relay enrollment and attachment policy;
- budgets and usage ledger;
- audit records;
- schedules and orchestration ownership unless separately migrated;
- secret-store metadata and encryption master key material;
- conversation locators, last generations, and digests.

## 13. Conversation list and UI projection

`list_conversations()` becomes a federated union over authorized attached
sources plus the local backend.

Each row includes safe provenance:

~~~json
{
  "conversation_id": "uuid",
  "storage_location": "remote",
  "source_id": "uuid",
  "endpoint_alias": "laptop",
  "availability": "online",
  "read_only": false
}
~~~

Rules:

- duplicate titles are allowed;
- duplicate conversation identities are not;
- sorting uses source timestamps but never connection order as precedence;
- ACL filtering occurs before rows enter the union;
- disconnect removes rows from the normal list atomically;
- an already-open route receives `conversation_source_offline` and becomes
  read-only/unavailable;
- a direct link may show a source-offline diagnostic without exposing metadata
  to an unauthorized principal.

## 14. Search and derived indexes

Provide two modes:

### 14.1 Remote search, recommended

The conversation source implements `storage.conversation.search` and returns
authorized object IDs, ranks, and safe snippets. The server federates results
from local and attached sources.

Advantages:

- content stays at the source;
- source-local indexing is efficient;
- disconnect naturally removes the result set;
- encrypted/plaintext policy is enforced next to the data.

### 14.2 Ephemeral server index

When a source has no search capability, PawFlow may build an in-memory or
explicitly non-durable encrypted index under a source lease.

Requirements:

- opt-in policy;
- source ID and attachment revision in every cache key;
- no plaintext SQLite file that survives detachment;
- immediate purge on disconnect, revocation, encryption change, or unreadable
  source listing;
- strict size and TTL bounds.

Do not feed remote content into the current persistent per-user FTS database by
default.

## 15. Resource backend architecture

### 15.1 Backend contract

Introduce a provider-neutral resource backend below the authorized resource
facade:

~~~python
list_manifest(collection, resource_type, cursor=None)
get_descriptor(resource_id)
fetch_snapshot(resource_id, revision, content_hash)
create(collection, resource, operation_id, fence)
update(resource_id, patch_or_snapshot, expected_revision, operation_id, fence)
delete(resource_id, expected_revision, operation_id, fence)
watch(collection, after_generation)
~~~

`RawResourceRepository` becomes the local backend. `RelayResourceBackend`
implements the same contract through relay RPC.

### 15.2 Two attachment modes

#### Catalog attachment

Remote resources are visible in an attached/shared catalog and become usable
only through stable-ID bindings. They never enter native name precedence merely
because the relay connected.

This is the safe default and allows several attached sources.

#### Scope home

A remote collection becomes the authoritative backend for a precise tuple:

~~~text
(resource_type or allowed type set, scope, owner_user_id, conversation_id)
~~~

Only one writable scope home may exist for that tuple. Reads and writes route to
it. When it disconnects, that scope becomes unavailable; PawFlow does not expose
a local directory as a fallback.

Global scope homes are admin-only. User and conversation homes require the
owner's authorization and applicable ACL policy.

### 15.3 Precedence and collision rules

Preserve the native local cascade for local resources:

~~~text
conversation -> user -> global
~~~

Then apply these rules:

- catalog attachments remain separate and explicit;
- scope-home resources occupy their configured native scope;
- all persistent references use resource UUIDs;
- two items with the same UUID but different source/hash are quarantined;
- two attached resources with the same name are both listed with source labels;
- unqualified name resolution that is ambiguous fails;
- a disappearing remote resource leaves its binding unavailable and never
  resolves to a same-name local resource.

An optional compatibility overlay may expose a remote item by name only when no
collision exists, but executable resources still require explicit acceptance
under the resource ACL plan.

### 15.4 Directory resources

Skills, themes, skins, and flows may contain directory trees or versions.
Transfer immutable snapshots as bounded archives or chunked content objects.

Validation before materialization:

- declared total size, file count, and content hash;
- no absolute paths, `..`, special devices, sockets, or escaping symlinks;
- normalized Unicode and duplicate-path handling;
- per-file and total extraction limits;
- schema and frontmatter validation;
- executable-resource risk review;
- immutable revision directory.

Never mount the relay's entire repository tree into the PawFlow server or a CLI
container.

### 15.5 Content cache

Remote resource content may be materialized into a source-tagged,
content-addressed runtime cache for execution.

- descriptor/manifests may be cached in memory;
- immutable snapshots are keyed by source, resource ID, revision, and hash;
- cache entries require an active source/binding lease to be opened;
- disconnect removes them from resolution immediately;
- v1 purges bytes on detach;
- persistent encrypted caches, if ever added, are explicitly non-authoritative
  and policy controlled.

## 16. Resource ACL and secret behavior

The server applies resource ACLs after mapping a remote collection into a
server-controlled owner/audience. ACL payloads embedded in remote content are
ignored.

For executable resources:

- visibility does not activate them;
- binding pins source, resource ID, revision, and content hash;
- consumer secret slots are explicit;
- publisher/relay secrets are not exposed;
- ordinary expression lookup cannot fall through into the consumer's entire
  secret cascade;
- materialized assets are read-only unless the resource type explicitly supports
  mutation;
- disconnect invalidates bindings for execution even if bytes remain in memory.

## 17. CLI execution placement model

### 17.1 LLM service configuration

Add explicit placement to CLI-capable `llmConnection` definitions:

~~~json
{
  "provider": "codex-app-server",
  "cli_execution": {
    "mode": "relay",
    "execution_target_id": "uuid",
    "unavailable_policy": "fail",
    "workspace_affinity": "prefer_same_endpoint",
    "conversation_affinity": "prefer_same_source"
  }
}
~~~

Allowed modes:

- `server`: current local Docker behavior;
- `relay`: exact remote execution target;
- `conversation_home`: select the runtime capability co-located with the
  conversation home;
- `workspace_relay`: select the conversation's explicitly bound workspace relay.

The resolved target is recorded at turn start and cannot change mid-turn.

### 17.2 No silent fallback

Default `unavailable_policy` is `fail`.

If a relay target is offline or unauthorized, do not launch locally. Silent
fallback can:

- execute private code on the wrong machine;
- expose secrets to a different trust boundary;
- use different credentials or Docker images;
- violate data residency;
- mutate a different workspace.

An explicit `fallback_targets` list may be added later. Every target requires
independent authorization and the UI must show the selected placement.

### 17.3 Service and endpoint ACL intersection

A principal may use a global LLM service that names a remote execution target
only if they also have the required endpoint permissions. Availability checks
must evaluate both service/resource access and relay endpoint access.

The effective capability set is:

~~~text
LLM service allowed provider/options
intersect execution-target capability ceiling
intersect principal endpoint ACL
intersect conversation binding and permission mode
intersect runtime lease
~~~

## 18. CLI runtime abstraction

### 18.1 Replace direct Docker coupling

Introduce interfaces such as:

~~~python
class CliRuntimeProvider:
    def acquire(self, launch_spec, principal) -> RuntimeHandle: ...
    def start(self, runtime, process_spec) -> ProcessHandle: ...
    def write_stdin(self, process, data): ...
    def resize_pty(self, process, rows, cols): ...
    def signal(self, process, signal): ...
    def wait(self, process, timeout=None): ...
    def release(self, runtime, reason=""): ...
    def status(self, runtime): ...

class ProcessHandle:
    def iter_events(self): ...
    def poll(self): ...
    def wait(self, timeout=None): ...
    def terminate(self): ...
    def kill(self): ...
~~~

Implementations:

- `LocalDockerCliRuntimeProvider` wraps current pools and `subprocess.Popen`;
- `RelayDockerCliRuntimeProvider` sends typed RPC and exposes a stream-backed
  process handle.

Provider code must stop depending on a local container name or Popen internals.

### 18.2 Pool API

Unify common pool operations now duplicated across Claude, Codex, and Gemini:

~~~text
acquire(provider, image_policy, mounts, limits)
start_process(runtime_id, command_profile, args, env_refs, io_mode)
stream_stdin / stream_stdout / stream_stderr
poll / wait / signal
copy_or_materialize_asset
release
status
~~~

Interactive providers add PTY, tmux/session control, event bridge, and long-lived
runtime reuse after the batch path is stable.

### 18.3 Typed launch specification

The server never sends a raw `docker run` command. A launch spec contains:

- provider profile;
- approved image digest or image policy ID;
- CPU, memory, pids, tmpfs, network, and timeout limits;
- logical workspace/session/resource mounts;
- environment slot references, not arbitrary inherited environment;
- runtime bundle version;
- user/conversation/agent audit context;
- lease and fencing epoch.

The relay resolves logical mounts through its own approved local profiles and
intersects limits with local operator policy. It may refuse the launch but may
not broaden it.

### 18.4 Docker ownership and cleanup

Remote containers carry exact labels for:

- PawFlow server ID;
- relay node and endpoint IDs;
- execution target and runtime IDs;
- provider;
- lease epoch;
- non-sensitive conversation/agent hash when needed.

The relay owns `run`, `exec`, `inspect`, `cp`, `signal`, and `rm`. Server cleanup
calls target a runtime UUID. Broad prefix-based deletion is forbidden.

The relay reaper removes:

- expired runtime leases;
- orphaned runtimes from an earlier daemon process;
- containers whose controlling server/node epoch is stale;
- dead processes and abandoned PTYs.

## 19. Runtime protocol

### 19.1 Launch

~~~json
{
  "type": "runtime.acquire.v1",
  "operation_id": "uuid",
  "execution_target_id": "uuid",
  "runtime_lease": "opaque",
  "runtime_epoch": 2,
  "launch_spec": {},
  "audit_id": "uuid"
}
~~~

The relay returns a runtime handle and effective limits/capabilities.

### 19.2 Process events

Use ordered stream frames:

~~~text
process.started(runtime_id, process_id)
process.stdout(seq, bytes)
process.stderr(seq, bytes)
process.event(seq, structured_event)
process.exited(exit_code, signal, final_seq)
~~~

Requirements:

- bounded buffers and backpressure;
- sequence numbers and reconnect diagnostics;
- binary-safe chunks;
- separate stdout/stderr;
- explicit end-of-stream;
- cancellation and timeout messages;
- maximum output and event sizes;
- no unbounded replay after reconnect.

### 19.3 Unknown outcomes

`runtime.acquire` and process start use idempotency IDs. If the connection drops
after creation but before acknowledgement, the server queries the runtime
operation receipt after reconnect instead of launching a duplicate container.

## 20. Runtime bundle and image compatibility

Current pools bind-mount or `docker cp` bridge scripts directly from the PawFlow
server source tree. A remote host cannot resolve those paths.

Required replacement:

- production images contain a versioned PawFlow runtime client; or
- PawFlow sends a signed/content-hashed runtime bundle that the relay caches and
  mounts read-only.

The bundle contains only runtime client code, never server config or secrets.

The relay reports:

- available image digests;
- runtime bundle versions;
- provider CLI versions;
- supported protocol/features;
- platform/architecture;
- capacity and local policy limits.

Image tags alone are insufficient. Pin or verify digests for reproducibility.
Image pulling requires explicit relay policy and `runtime.docker.pull`.

## 21. Tool and MCP bridge for remote containers

The current bridge connects from a server-host container to `/ws/tools/*` using
a broad in-memory internal token and a host-rewritten URL. That token model and
network assumption must not be exported unchanged.

Recommended topology:

~~~text
CLI container
   -> relay-local runtime gateway
   -> existing authenticated relay WebSocket
   -> PawFlow ToolRelayService
~~~

The runtime gateway:

- listens only on a relay-controlled container network or Unix socket;
- authenticates a runtime-scoped capability token;
- binds user, conversation, agent, execution target, runtime, and expiry
  server-side;
- multiplexes tool/MCP frames over the relay connection;
- cannot assert another principal;
- loses access when the runtime lease expires;
- applies flow control and size limits.

Direct connection from the remote container to the public PawFlow listener may
remain an optional mode, but it uses a scoped runtime capability token, TLS, and
the public server URL. It must not use generic `internal_auth` bypass tokens.

## 22. Interactive CLI providers

Interactive Claude/Codex and observer providers require additional work:

- persistent container and tmux/PTY lifecycle;
- MITM/event proxy startup;
- hook event delivery;
- prompt injection and interrupt routing;
- VNC/screen or terminal proxying;
- credential-slot exclusivity;
- token recovery after CLI refresh;
- live-session lookup and replacement;
- context compaction lifecycle.

Refactor live registries so their identity is:

~~~text
(execution_target_id, runtime_id, user_id, conversation_id, agent_name,
 service_id, credential_slot)
~~~

They must call the runtime provider for health, interrupt, token recovery, and
release. They may not call local Docker pools when the runtime is remote.

Ship batch Claude/Codex/Gemini execution first. Add interactive providers only
after event and PTY tunnels pass reconnect and revocation tests.

## 23. Workspace, session, resource, and file placement

Remote CLI execution needs four distinct data views:

1. **workspace**: project files the CLI edits;
2. **provider session**: Claude/Codex/Gemini state and OAuth/config files;
3. **resources**: skills, generated MCP config, hooks, and runtime bundle;
4. **conversation files**: FileStore attachments and generated media when used.

### 23.1 Same relay/node affinity

When workspace, conversation/resource storage, and execution live on the same
relay node, use relay-local bind mounts. This is the preferred fast path.

The server sends logical profile/object IDs; the relay resolves them to local
paths. The server never invents a relay host path.

### 23.2 Server conversation, remote execution

Provider session data may:

- live in an execution-target-owned per-user/conversation directory; or
- use a future tenant-scoped inverse server mount.

The first option is simpler and makes CLI session persistence depend on the
execution relay. The UI must expose that placement.

Conversation messages themselves remain accessed through server orchestration;
the CLI receives prompt/context streams and does not need the raw conversation
store mounted.

### 23.3 Remote conversation home, same relay execution

Provider session and conversation storage can be local to the relay but remain
separate roots and capability domains. A container receives only its authorized
conversation/session subtree.

### 23.4 Workspace on another relay

Use the architecture in `RELAY_WORKSPACE_FS_PLAN.md` only as an explicit
fallback. It is slower and adds a second availability dependency:

~~~text
execution relay -> PawFlow hub -> workspace relay
~~~

Both endpoint ACLs and a cross-relay mount lease must authorize it. No whole
relay root is exposed.

### 23.5 Resource materialization

- co-located remote resource source: materialize from the source locally;
- server-local resource: send an immutable validated snapshot;
- resource on another relay: fetch through the server and verify the pinned
  content hash;
- mount only assigned/bound resource revisions, never a complete user or global
  repository tree.

## 24. Credentials and secrets for remote CLI execution

Support explicit credential placement modes:

### 24.1 Relay-local credential

The relay operator configures a local provider account/profile. PawFlow refers
to an opaque credential profile ID and never receives the secret.

Use when the remote machine owns the subscription/login.

This is a post-v1 option. The v1 remote CLI compute implementation in
`REMOTE_CLI_COMPUTE_POOL_PLAN.md` uses PawFlow-owned credential selection plus a
runtime-bound credential/egress broker and does not expose relay-local credential
profiles in either UI. Adding this mode later requires its own explicitly selected
custody mode and provider/security matrix; it is never a fallback.

### 24.2 PawFlow-delegated credential

PawFlow sends only the selected credential material under a runtime-bound,
short-lived encrypted channel.

Requirements:

- explicit user/admin consent;
- execution endpoint permission for secret injection;
- no full secret-map injection;
- no persistence outside the provider session policy;
- redaction in frames, logs, errors, and audit;
- documented trust warning that the relay host operator can inspect it;
- secure recovery/update if a CLI rotates an OAuth refresh token;
- revocation and cleanup on runtime end.

### 24.3 Credential-slot exclusivity

Interactive subscription credentials with single-use refresh tokens retain the
existing one-login/one-live-container exclusivity rule, but the reservation is
coordinated through a server-side credential lease and remote runtime state.

The relay never selects another user's credential slot.

## 25. Availability and failure semantics

### 25.1 Source disconnect during a read

- return `source_offline` or a retryable transport error;
- do not serve stale cached content as authoritative;
- UI may retain already-rendered content but marks it stale/offline;
- direct subsequent reads require reattachment.

### 25.2 Source disconnect during a write

- classify outcome as definitely failed or unknown;
- do not publish success/SSE for unknown outcome;
- stop accepting later writes for that conversation;
- after reconnect, query the operation receipt;
- retry only if the backend proves the operation was not committed.

### 25.3 Execution disconnect

The remote runtime lease has a short TTL and a default disconnect policy:

~~~text
grace for network flap -> self-terminate process/container when lease expires
~~~

The server marks output incomplete and never assumes the process stopped merely
because the socket closed. Reconnect reconciles runtime IDs and final receipts.

### 25.4 Active turns and scheduled work

- active turns using remote storage or runtime fail/pause according to an
  explicit policy;
- default is fail closed with a clear resumable error;
- scheduled tasks do not migrate to local execution automatically;
- plans and bindings retain unavailable references;
- a reconnect may resume only after storage/runtime reconciliation.

## 26. Consistency, generations, and rollback detection

### 26.1 Generations

Track independently:

- source generation;
- manifest/change-feed sequence;
- collection generation;
- conversation revision;
- transcript rewrite generation;
- resource revision;
- attachment revision;
- home/runtime fencing epoch.

Do not reuse timestamps as consistency tokens.

### 26.2 Digests and receipts

The server persists small integrity checkpoints:

- latest accepted source and object generations;
- descriptor/content digests;
- last mutation receipt ID/hash;
- optional append-only hash-chain head.

These do not reconstruct content but detect accidental or malicious rollback.

### 26.3 Regression handling

If a reconnect reports a lower generation, missing acknowledged mutation, or
different digest at the same revision:

- quarantine the source/object;
- remove it from normal discovery;
- block writes and execution;
- expose an admin diagnostic;
- require explicit accept-rollback, re-import, or detach action.

## 27. Authorization model

Use `PrincipalContext` at every server ingress and derive storage/runtime leases
from current policy.

Authorization layers:

~~~text
source attachment policy
intersect relay endpoint ACL
intersect conversation/resource/service ACL
intersect user ownership/share role
intersect binding/placement selection
intersect requested operation
intersect active source/runtime lease
~~~

The relay receives only opaque leases and minimal audit context. It does not
receive authoritative group/role lists and cannot widen access.

Required operations include:

- attach/detach source;
- list/read/write/delete conversation;
- conversation Git/fork/export;
- list/read/write/delete resource;
- bind/activate resource;
- select execution target;
- launch/interrupt/control runtime;
- inject selected credential;
- mount workspace/resource/file view.

## 28. Security and trust model

### 28.1 Relay host is a data custodian

A remote storage relay can read, alter, delete, withhold, or roll back content on
its disk unless content is end-to-end encrypted against it. A remote execution
relay can inspect process memory, mounted files, environment variables, and CLI
credentials.

The UI must identify the relay operator/trust domain and warn before moving data
or injecting credentials.

### 28.2 Malicious manifests and content

Protect the server against:

- huge manifests and decompression bombs;
- duplicate IDs and Unicode/path confusion;
- invalid timestamps, revisions, and negative sizes;
- archive traversal and escaping symlinks;
- malformed JSON/Markdown/YAML;
- resource definitions containing secret literals;
- change-feed floods;
- rollback or equivocation at one revision;
- executable skills/tools/hooks activated without review.

### 28.3 Remote Docker boundary

- no remote Docker socket exposed to PawFlow users;
- no raw Docker flags from LLM service definitions;
- no privileged or host-root mounts unless a separately named capability and
  local operator policy allow them;
- image allowlists/digests and resource limits;
- rootless Docker/containerd support where practical;
- relay-side jail for all logical roots;
- exact cleanup labels and fencing epochs.

### 28.4 Denial of service

Apply:

- manifest/page/item/byte limits;
- storage RPC concurrency and timeouts;
- per-user/source/runtime quotas;
- output backpressure;
- bounded receipt ledgers;
- source health circuit breakers;
- Docker capacity reservations;
- search result and content-fetch limits.

## 29. Encryption at rest

### 29.1 Plain remote storage

The relay sees plaintext and is responsible for filesystem permissions, disk
encryption, backup, and secure deletion. PawFlow should expose that trust state.

### 29.2 PawFlow encrypted conversations

Do not silently disable existing conversation encryption when moving a
conversation. This limitation is surfaced in section 1.1 because it constrains
who can use this feature at all, not merely how.

A remote conversation source claiming `storage.conversation.encrypted` must
support one reviewed mode:

- store PawFlow ciphertext and perform only operations possible without
  plaintext; or
- receive a short-lived DEK through the trusted relay key mechanism and run the
  canonical encrypted ConversationStore locally.

The latter makes plaintext available in relay RAM and therefore changes the
trust boundary. DEKs must be session/lease-bound, purged on disconnect, and
never written into manifests, logs, or runtime records.

Until this is implemented and audited, encrypted conversations cannot use a
remote home.

### 29.3 Derived content

Search indexes, previews, summaries, resource caches, runtime logs, and
container session files can contain plaintext. Their placement and retention
must match the conversation/source encryption policy.

## 30. API surface

### 30.1 Attachment administration

- create/list/update/delete storage attachments;
- approve source capability or root changes;
- map personal sources to a user;
- map shared/global sources to ACL policies;
- choose catalog versus scope-home mode;
- set read-only state and quotas;
- inspect generation, digest, health, and compatibility;
- accept or reject detected rollback.

### 30.2 Conversation operations

- list local and remote conversations with provenance;
- create with explicit/default home;
- inspect home and portability profile;
- move/export/import with verification;
- reconnect/resync source;
- show source-offline state;
- prevent operations that companion backends do not support.

### 30.3 Resource operations

- list source-labeled remote resources;
- fetch/review immutable snapshots;
- bind exact revisions;
- configure one scope home;
- import/copy to a local/new home explicitly;
- show unavailable and update-pending bindings.

### 30.4 LLM service operations

- select server or relay execution placement;
- list compatible authorized targets;
- test image/runtime compatibility;
- show actual selected target per turn;
- expose target availability and capacity;
- never reveal an unauthorized endpoint through error details.

## 31. CLI and Desktop surface

Suggested relay-side commands:

~~~text
pawflow-relay storage conversation add <profile> --path <path> --mode rw
pawflow-relay storage resource add <profile> --path <path> --types skill,agent,mcp
pawflow-relay runtime docker enable --providers claude,codex,gemini
pawflow-relay runtime images list
pawflow-relay runtime status
pawflow-relay source status
~~~

Local profiles contain:

- opaque profile ID;
- local root selected by the relay operator;
- allowed source types and providers;
- read/write and Docker policies;
- capacity limits;
- server attachment identity;
- no server ACL or user-role assertions.

Desktop UI must clearly separate:

- directories exported for storage;
- workspaces exposed to agents;
- Docker execution permission;
- local credential profiles (post-v1 only; absent from the v1 compute UI);
- connected PawFlow servers and active runtimes.

## 32. Cache and event invalidation

Every cache key involving remote state includes:

~~~text
source_id
attachment_revision
source/collection generation
object_id and content revision/hash
principal membership/policy revision where relevant
~~~

Detach/revoke invalidates:

- conversation metadata and context caches;
- `ConversationWriter` instances;
- search results and ephemeral indexes;
- ResourceStore/repository lists;
- resource bindings and materialization handles;
- tool relay registries and MCP discovery;
- skill manifests and CLI mount plans;
- LLM service availability caches;
- live runtime registries and proxy tokens;
- UI/SSE catalog state.

Do not merely wait for TTL expiry. Detachment is an active invalidation event.

## 33. Persistence layout

Suggested server control-plane layout:

~~~text
data/system/data_plane/sources/<source_id>.json
data/system/data_plane/attachments/<attachment_id>.json
data/system/data_plane/conversation_locators/<conversation_id>.json
data/system/data_plane/resource_collections/<collection_id>.json
data/system/data_plane/execution_targets/<target_id>.json
data/system/data_plane/checkpoints/<source_id>.json
data/system/data_plane/policies/<object_id>.json
~~~

No remote conversation/resource content is stored there.

Relay-side layout is implementation-defined but versioned and root-confined,
for example:

~~~text
<profile-root>/conversations/<remote_object_id>/...
<profile-root>/repository/<type>/...
<runtime-root>/sessions/<provider>/<tenant>/<conversation>/<agent>/...
<runtime-root>/containers-and-receipts/...
~~~

The relay does not expose those raw paths through the server API.

## 34. Migration and compatibility

### 34.1 Local backend first

Introduce backend interfaces with only the local implementation enabled. Keep
existing behavior and tests identical before adding remote routing.

### 34.2 Stable conversation and resource identity

Complete UUID/resource-locator migration from the ACL plans before dynamic
source merging. Name-only resources and conversation IDs discovered by raw
directory scanning cannot safely span sources.

### 34.3 Existing ConversationStore callers

Classify callers:

- public facade calls that can route;
- code requiring local `Path` access;
- Git operations;
- cache/index internals;
- companion-store lifecycle calls;
- test-only raw filesystem access.

Replace local-path assumptions or explicitly reject them for remote homes. Never
return a fake server path for remote content.

### 34.4 Existing CLI providers

Refactor one batch provider at a time to `CliRuntimeProvider` while using the
local adapter. Then enable the relay adapter. Suggested order:

1. Codex app-server batch/runtime;
2. Claude Code batch;
3. Gemini ACP;
4. Codex interactive;
5. Claude Code interactive;
6. Antigravity/observer variants.

### 34.5 Relay protocol cutover

Storage and remote Docker require the v3 protocol and leases from the relay
enrollment plan. Its one-shot migration removes v2 before any data-plane source
is enabled. This plan adds no adapter, compatibility projection, or legacy
fallback: every source and runtime target uses stable v3 endpoint IDs from its
first release.

## 35. Implementation work packages

### WP-A. Standalone prerequisite

- fix the `ConversationWriter` failure-propagation defect of section 5.1.1;
- add the failing-write test.

No dependency on WP0 or on either ACL plan. Ship first, on its own.

### WP0. Dependencies and architecture gates

- finish stable resource identity and `PrincipalContext` foundations;
- implement relay endpoint identity, ACL, capabilities, and leases;
- define source/attachment threat model and operator trust UI;
- inventory all direct `ConversationStore`, `ScopedRepository`, pool, Popen,
  Docker, and local-path callers;
- choose protocol and storage format compatibility policy.

Exit gate: no remote source is enabled before stable source/object identities
exist.

### WP1. Data-plane source registry

- add source, attachment, checkpoint, and locator stores;
- implement capability registration and attachment policy;
- implement atomic manifest snapshot and ordered change feed;
- validate limits, IDs, generations, hashes, and rollback;
- add attach/detach events and cache invalidation bus;
- expose admin diagnostics.

### WP2. Conversation backend extraction

- define `ConversationBackend` and `ConversationHomeRouter`;
- wrap the current implementation in `LocalConversationBackend`;
- remove public dependence on `_conv_dir()` and direct paths;
- route list/read/write/extras/context/Git APIs through locators;
- preserve existing local tests and performance;
- make unsupported remote operations explicit.

Scale: roughly 120 files under `core/`, `tasks/`, and `services/` reference
`ConversationStore` today. This work package is a multi-week refactor of a
central subsystem, not a single item in a list. Produce the caller inventory of
section 34.3 and size WP2 before committing to any date for D2 or later.

### WP3. Reliable ConversationWriter

- add operation IDs and structured futures/results;
- propagate backend errors instead of logging-and-continuing;
- tie SSE publication to durable receipts;
- add unknown-outcome state and receipt reconciliation;
- deactivate/drain writers on source detach;
- add expected revision and fencing epoch.

This is required even before remote writes are enabled.

### WP4. Relay conversation backend

- package the canonical conversation-store engine for relay use;
- implement typed list/read/write/extras/context/Git RPC;
- add durable mutation receipt ledger and fencing;
- implement descriptor/change generation;
- add root confinement, quotas, fsync, and health reporting;
- project remote conversations into federated listings;
- support read-only first, then writable homes.

### WP5. Search and companion data

- implement remote search contract and federated result merge;
- exclude remote content from persistent local FTS by default;
- define/export conversation portability profiles;
- add conversation-scoped resource routing;
- design FileStore backend before claiming full portability;
- expose placement and missing-companion warnings.

### WP6. Resource backend extraction

- split authorized resource facade from raw local backend;
- implement manifest/descriptors/snapshot interfaces;
- preserve local native cascade;
- add catalog attachments and exact source/resource refs;
- implement directory snapshot validation/materialization;
- integrate ACL bindings, revisions, hashes, and secret slots.

### WP7. Remote resource homes

- support one authoritative writable home per scope/type tuple;
- add mutation receipts, expected revisions, and fencing;
- implement ordered change feed and cache invalidation;
- add explicit import/copy/move with verification;
- prevent local fallback while a remote home is offline.

### WP8. CLI runtime abstraction

- define runtime/process interfaces and stream events;
- implement local adapters over current Docker pools and Popen;
- refactor provider and live-registry code away from local container assumptions;
- unify common pool lifecycle and status concepts;
- retain byte-for-byte equivalent local behavior before remote enablement.

Scale: the CLI pools and LLM providers total roughly 20,000 lines across
`core/*_pool.py` and `core/llm_providers/`, each with its own container,
session, and process assumptions. Like WP2, size this separately; refactor one
batch provider end to end first (section 34.4) and re-estimate from the actual
cost of the first one.

### WP9. Relay Docker runtime

- implement typed launch/process/stream/signal/release RPC;
- add relay-side image policy, capacity reservations, exact labels, reaper, and
  runtime leases;
- implement idempotent acquire/start receipts;
- add signed runtime bundle or runtime-in-image support;
- implement batch Codex/Claude/Gemini execution;
- add status, metrics, and audit.

### WP10. Remote tool gateway and data materialization

- create relay-local runtime gateway;
- mint runtime-scoped tool/MCP capability tokens;
- multiplex tool calls through relay transport;
- materialize workspace/session/resources/files by logical IDs;
- enforce source and endpoint ACL intersections;
- add secret-delegation policy and cleanup.

### WP11. Interactive runtimes

- abstract PTY/tmux and event services;
- route hook/proxy events through relay transport;
- refactor live registries to remote runtime handles;
- implement interrupt, resize, force-stop, token recovery, reuse, and sweeper;
- test disconnect/reconnect and credential exclusivity.

### WP12. Migration, UX, and hardening

- add home/attachment/target selectors and availability badges;
- add source-offline and rollback diagnostics;
- migrate eligible IDs and bindings;
- document backup, data residency, trust, and failure behavior;
- run adversarial protocol/content/Docker reviews;
- remove compatibility shortcuts only after telemetry and rollback windows.

## 36. Test plan

### 36.1 Source and manifest tests

- valid snapshot publishes atomically;
- partial/oversized/malformed snapshot publishes nothing;
- duplicate pages/events are idempotent;
- sequence gaps trigger resync;
- disconnect removes all catalog entries atomically;
- reconnect with same generation restores them;
- generation regression/digest equivocation quarantines the source;
- relay-supplied owner/ACL fields have no effect;
- unauthorized users cannot infer source/object existence.

### 36.2 Conversation backend contract tests

Run one shared suite against local and fake-relay backends:

- create/save/load;
- append single and batch messages;
- shared and agent context routing;
- extras compare-and-set;
- patch/delete/truncate;
- list metadata and generations;
- Git snapshot/log/diff/rollback/branch/tag/fork;
- encryption status behavior;
- owner/access checks;
- expiry and delete lifecycle.

### 36.3 Write reliability tests

- visible implies remotely persisted;
- writer errors reach callers;
- disconnect before send is definitely failed;
- disconnect after commit/before acknowledgement reconciles by receipt;
- retry does not duplicate messages;
- same operation ID with different payload fails;
- stale expected revision conflicts;
- stale home epoch is fenced;
- two servers cannot hold concurrent write authority;
- later queued writes stop after an unknown outcome.

### 36.4 Catalog/disconnect tests

- local and remote conversations form an authorized union;
- remote rows disappear on detach;
- open conversations receive offline state;
- remote resources disappear from discovery/tool registries;
- bindings become unavailable, not rebound by name;
- reconnect restores stable IDs and accepted revisions;
- caches and indexes do not expose detached content.

### 36.5 Resource tests

- catalog versus scope-home semantics;
- at most one writable home per tuple;
- ambiguous names fail;
- directory traversal, symlink escape, device, decompression bomb, and file-count
  attacks fail;
- content hash/revision pinning;
- executable resource activation requires ACL and binding;
- secret slots never resolve implicitly;
- remote mutation idempotency and fencing;
- source rollback quarantines bindings.

### 36.6 Runtime provider contract tests

Run the same provider lifecycle suite against local and fake remote adapters:

- acquire/start/stdin/stdout/stderr/wait;
- exit code and signal propagation;
- cancel/terminate/kill;
- timeout and output backpressure;
- release and idempotent cleanup;
- status and capacity;
- unknown acquire/start outcome reconciliation;
- no dependence on local container names in provider code.

### 36.7 Remote Docker security tests

- raw Docker flags and arbitrary host paths are rejected;
- image policy/digest and resource limits enforced;
- no privileged, host-root, or Docker-socket access without explicit policy;
- runtime cannot use another runtime's lease or mounts;
- expired lease self-terminates containers;
- reconnect cannot revive a stale runtime epoch;
- exact labels prevent cross-server/container deletion;
- malicious stdout/event floods are bounded.

### 36.8 Tool and credential tests

- runtime token is bound to endpoint/runtime/user/conversation/agent;
- forged identity fields are ignored/rejected;
- remote container cannot use generic internal-auth bypass;
- token expiry/revocation closes tool access;
- only assigned resource revisions are materialized;
- only explicitly delegated secrets are injected;
- relay-local credentials never reach PawFlow;
- delegated credential rotation returns to the correct slot securely;
- logs and frames redact tokens/secrets.

### 36.9 Placement tests

- `server` runs only on server Docker;
- exact `relay` target runs only on that endpoint;
- offline relay fails without local fallback;
- unauthorized target is unusable even through a global LLM service;
- target selection is pinned for the whole turn;
- same-relay affinity uses local mounts;
- cross-relay workspace uses explicit routed mount and both ACLs;
- placement is included in audit and UI events.

### 36.10 Integration and chaos tests

- connect relay, attach source, list/open/write remote conversation, disconnect,
  verify disappearance, reconnect, verify exact state;
- attach remote skill/agent, bind/use, disconnect, verify no same-name fallback;
- launch Codex/Claude batch container remotely and exercise MCP tools;
- drop WebSocket at every mutation/runtime protocol boundary;
- restart PawFlow while relay/runtime lives;
- restart relay while PawFlow has pending operations;
- slow, reordered, duplicated, and truncated frames;
- source disk full, read-only filesystem, Git failure, Docker daemon failure,
  missing image, and lease expiry;
- two remote sources with colliding IDs/names;
- encrypted-conversation rejection until supported.

## 37. Rollout phases and gates

| Phase | Content | Remote writes | Remote Docker | Gate |
|---|---|---:|---:|---|
| D0 | Relay identity/ACL/lease and stable resource IDs | No | No | Dependency plans complete |
| D1 | Source registry, manifests, read-only conversation catalog | No | No | Attach/detach and rollback review |
| D2 | Conversation backend extraction + reliable writer | Local only | No | Existing behavior parity |
| D3 | Read-only remote conversation backend + remote search | No | No | No durable plaintext cache |
| D4 | Writable remote conversation homes, receipts, fencing | Yes | No | Exactly-once/chaos tests pass |
| D5 | Remote resource catalog attachments | Resource read only | No | ACL/revision/archive review |
| D6 | Writable remote resource scope homes | Yes | No | Single-home/fencing tests pass |
| D7 | CLI runtime abstraction with local adapter | Yes | Local only | Provider parity |
| D8 | Remote batch CLI runtime + tool gateway | Yes | Batch | Docker/secret security review |
| D9 | Co-location/materialization and companion stores | Yes | Batch | Placement matrix tests pass |
| D10 | Interactive CLI runtimes | Yes | Interactive | PTY/event/reconnect review |
| D11 | Encryption and portable conversation profile | Yes | Full | Key-boundary review |

Do not combine D4, D6, and D8 into one release. Each introduces an independent
durability or execution trust boundary.

### 37.1 Dependency reality and recommended commitment

The remote data planes have integration gates across three plans:

~~~text
Resource ACL S2 ──> Relay enrollment R0-R3 ──> D1+ catalog/data-plane enablement

WP2/WP3 local backend + Relay R3 ──> WP4 remote conversation backend
WP8 local CLI runtime + Relay R3 ──> WP9 remote Docker runtime
~~~

D0 is an architecture and dependency gate, so its analysis may start
immediately. Enabling D1 or any remote catalog requires the relevant stable IDs,
`PrincipalContext`, and relay authorization foundations to be complete. D4, the
first writable remote-conversation release, additionally requires the local
backend seam and reliable writer from WP2/WP3 plus relay R3. These are integration
dependencies, not thirty sequential phases; independent local refactors may run
in parallel and converge at their stated release gates.

Recommended commitment, rather than adopting the plan whole:

1. ship WP-A here and the fail-closed WP-A mitigation of the enrollment plan
   immediately; neither enables new sharing or remote storage;
2. ship WP2 and WP3 next. They improve the local product on their own —
   reliable write outcomes and a routable backend seam — and are the only parts
   of this plan that pay off even if remote storage is never built;
3. treat everything from WP4 onward as a separate decision, taken after the
   dependency plans have reached production and after the encryption question
   of section 1.1 has an answer.

The local runtime abstraction in WP8 can proceed independently of the
conversation-storage packages. WP9 then requires WP8 and relay R3, but not the
conversation backend, so remote batch execution may still be prioritized ahead
of remote conversation homes.

## 38. Acceptance criteria

The architecture is complete when:

1. PawFlow can attach a relay conversation source without mounting its raw
   filesystem into the server;
2. authorized remote conversations appear in normal listings only while the
   source is attached;
3. writes are durable exactly once or return an explicit unknown outcome that is
   reconciled before further writes;
4. SSE never reports a remote write as visible before its durable receipt;
5. reconnect restores stable conversation identities and detects rollback;
6. remote resources appear with source provenance and stable IDs;
7. a disconnected remote resource never falls through to a same-name local
   resource;
8. a remote collection can be an explicit authoritative scope home with one
   fenced writer;
9. directory resources are transferred and extracted safely;
10. a CLI LLM service can select an exact remote execution target;
11. its Docker container is created, controlled, and destroyed on the relay
    host, not the PawFlow host;
12. an unavailable remote execution target fails without silent local fallback;
13. batch Claude, Codex, and Gemini providers work through provider-neutral
    runtime/process handles;
14. remote containers reach PawFlow tools through runtime-scoped authorization;
15. workspace, session, resources, and files are materialized according to
    explicit placement rules;
16. only explicit credential material reaches a remote runtime and all involved
    UIs disclose that trust boundary;
17. source detach invalidates catalogs, writers, caches, indexes, bindings, tool
    registries, and runtime leases;
18. local-only deployments retain current behavior and performance;
19. all contract, chaos, collision, rollback, Docker, ACL, and secret tests pass.

## 39. Recommended decisions

Adopt these defaults:

1. use typed high-level storage RPC, not FUSE, for conversations and resources;
2. keep PawFlow as control plane and relays as optional data/compute planes;
3. make catalog attachment and authoritative scope home separate modes;
4. allow only one writable home for each conversation or resource-scope tuple;
5. retain minimal persistent locators/checkpoints but no remote authoritative
   content on the server;
6. hide detached content from normal catalogs while retaining unavailable stable
   references for reconnect;
7. use UUID/source references and reject name-based fallback;
8. add idempotency receipts and fencing before any remote write;
9. fix `ConversationWriter` error propagation before enabling remote storage;
10. perform search at the remote source by default;
11. introduce a provider-neutral CLI runtime/process abstraction before remote
    Docker support;
12. send typed Docker launch specs and logical mounts, never raw Docker flags or
    relay host paths;
13. ship batch CLI execution before interactive providers;
14. route remote container tools through a relay-local, runtime-scoped gateway;
15. fail closed when placement is unavailable and require explicit fallback
    configuration;
16. prefer co-location of conversation home, resources, workspace, and execution
    while keeping each capability and ACL independent;
17. treat FileStore, CLI sessions, and conversation-scoped resources as explicit
    companion backends rather than pretending transcript relocation is complete
    portability;
18. block encrypted remote conversation homes until their DEK/storage mode has a
    dedicated security review.


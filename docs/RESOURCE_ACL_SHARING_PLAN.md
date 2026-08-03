# Resource ACL and Cross-User Sharing Implementation Plan

Status: **proposed** (analysis and implementation plan only; no implementation yet).

## 1. Goal

Add deterministic access-control lists (ACLs) to repository resources so that:

1. an administrator can restrict a global resource to selected users, groups, or
   PawFlow roles;
2. a user can share a user-scoped resource with selected users, groups, or
   PawFlow roles without transferring ownership or copying the resource;
3. shared resources never silently override an existing resource;
4. sharing visibility never implicitly activates executable content;
5. a shared resource never gains implicit access to the publisher's or
   consumer's secrets;
6. ACL revocation affects every runtime path, including the web UI, agent tools,
   MCP relay, scheduled work, Telegram, CLI providers, skill mounts, and cached
   registries.

The first implementation targets resources managed by ResourceStore:

- agent
- skill
- mcp
- task_def
- prompt
- tool
- agent_hook
- theme
- private_gateway_skin

Services, flow templates, deployed flows, memories, knowledge graphs, diaries,
voice clones, FileStore objects, and conversations are not part of the first
implementation. The authorization primitives should be reusable by those
systems later, but this project must not expand their scope implicitly.

## 2. Non-goals

The first release does not provide:

- a new shared storage scope;
- cross-user ownership transfer;
- collaborative editing of another user's resource;
- arbitrary deny rules;
- nested ACL groups;
- public or anonymous resource access;
- automatic execution of resources shared by another user;
- publisher-funded credentials for consumers;
- ACL inheritance from packages, imported SKILL.md frontmatter, or other
  untrusted portable content;
- a generic ACL retrofit for services and flows.

## 3. Existing architecture

### 3.1 Storage scopes

ScopedRepository stores definitions under:

~~~text
global: data/repository/<type>/global/
user:   data/repository/<type>/users/<user_id>/
conv:   data/repository/<type>/users/<owner_user_id>/<conversation_id>/
~~~

Scope currently determines storage, ownership, visibility, and precedence.

ResourceStore.list_all merges global, then user, then conversation resources by
name. Later merges win, producing the effective order:

~~~text
conversation -> user -> global
~~~

ResourceStore.get_any follows the same order.

### 3.2 References are name-based

Several persistent structures select resources by name:

- conversation extras active_resources;
- conv_agent_config definition;
- agent assigned_skills;
- conversation_hooks;
- tool_mcp_filters;
- agent and MCP activation records;
- scheduled task definitions and plan targets;
- package-installed resource dependencies.

The existing share_resource action only writes an agent or MCP name into
active_resources of another conversation owned by the same user. It is
activation-by-name, not an ACL grant.

### 3.3 Caller identity is incomplete

HTTP requests carry:

- http.auth.principal;
- http.auth.roles;
- http.auth.groups;
- http.auth.session_id.

Tool handlers and the relay registry currently receive user_id,
conversation_id, and agent_name. ManageResourceHandler has no role or group
context. Direct ResourceStore callers also pass a user_id that often represents
both the actor and the target owner.

IdP group names are stored on Session and transported for display/audit. They
are not currently a durable authorization identity.

Two existing modules already cover part of this ground and must be treated as
prior art rather than reimplemented:

- `core/auth_groups.py` maps IdP group claims to PawFlow roles under an
  operator-written mapping. Its stated rules are that an unmapped group grants
  nothing, that the locally stored role wins by default
  (`auth.role_precedence`), and that group names never reach
  `http.auth.roles`.
- `core/admin_scope.py` already performs an exact, comma-split check for the
  specific `admin` role, but it is not a general role parser or ACL API.

Roughly 29 call sites still test authority with `"admin" in roles`, a substring
test (`tasks/ai/actions/usage.py`, `tasks/ai/actions/secrets_variables.py:12`,
`tasks/ai/actions/_sf_base.py:22`, `tasks/ai/actions/admin_settings.py:37`, and
others). `core/auth_groups.py` keeps group names out of that attribute
specifically so those sites stay safe without being rewritten.

### 3.4 Secret resolution

Expression resolution currently checks:

~~~text
secrets: conversation -> user -> global
parameters: flow -> conversation -> user -> global -> environment
~~~

Secrets are checked before parameters. A shared definition containing a plain
expression reference can therefore resolve a consumer secret with the same key.

The tool relay can inject all consumer secrets into bash and execute_script and
uses the same values for result redaction. Visibility of third-party executable
resources must therefore remain separate from activation and credential access.

### 3.5 Skill filesystem

CLI containers and RelaySkillsFs expose global skills plus the current user's
skill tree. They deliberately reject another user's tree. A cross-user shared
skill therefore needs a new ACL-filtered virtual path; mounting the publisher's
whole user directory is forbidden.

## 4. Non-negotiable invariants

1. **Scope and ACL remain separate.** Scope defines owner, storage location, and
   native precedence. ACL defines audience.
2. **No shared scope.** A shared user resource stays user-scoped and owned by
   its publisher.
3. **Stable identity.** Cross-user references use resource_id, never an
   unqualified name.
4. **No silent override.** A shared user resource never enters the native
   conversation/user/global name cascade.
5. **Visibility is not activation.** A grant makes a resource discoverable; it
   does not add tools, MCPs, skills, agents, hooks, themes, or skins to a
   runtime.
6. **Explicit acceptance.** A consumer must bind a shared resource before use.
7. **Revision pinning.** Acceptance applies to one reviewed resource revision.
8. **Consumer credentials only.** A shared resource cannot resolve the
   publisher's secrets.
9. **No implicit consumer secrets.** Shared resources use declared secret slots
   and explicit consumer bindings.
10. **Authorization at the core boundary.** UI-only filtering is insufficient.
    Every resolver and mutation path uses the same authorization service.
11. **Fail closed.** Missing principal, unresolved owner, malformed policy,
    unknown group, stale binding, or unavailable revision denies access.
12. **No existence leaks.** Unauthorized direct reads return the same not-found
    result as an unknown resource.
13. **Admin override is explicit and audited.** Admin does not silently add
    restricted resources to normal self-discovery.
14. **selectedAgent remains non-empty.** Losing access marks the selected agent
    unavailable and blocks execution; it does not silently select a different
    agent.
15. **Portable content cannot grant access.** ACLs and consumer bindings are
    server-owned metadata outside resource payloads and package manifests.
16. **All created policy, binding, revision, and audit records receive a UUID
    and timestamp.**

## 5. Terminology

- **Owner**: user who owns a user or conversation resource. Global resources
  are administratively owned.
- **Principal**: authenticated actor performing an operation.
- **Subject**: ACL target: a user, canonical group, or exact PawFlow role.
- **Policy**: server-owned ACL attached to a resource_id.
- **Grant**: allowed permissions for one subject.
- **Binding**: consumer-owned acceptance and activation record for one resource
  revision.
- **Native resource**: global, own-user, or current-conversation resource
  already participating in the existing scope cascade.
- **Shared resource**: another user's resource visible through an ACL grant.
- **Resource reference**: exact resource_id, optionally accompanied by a
  display name and expected type.
- **Revision**: monotonically increasing content revision of a resource.
- **Policy revision**: monotonically increasing revision of its ACL.
- **Membership revision**: revision of a user's durable role/group membership.

## 6. Principal and group model

### 6.1 PrincipalContext

Introduce an immutable core PrincipalContext:

~~~python
@dataclass(frozen=True)
class PrincipalContext:
    user_id: str
    roles: frozenset[str]
    groups: frozenset[str]
    conversation_id: str = ""
    session_id: str = ""
    auth_source: str = ""
    is_system: bool = False
~~~

Rules:

- user_id is required for user-originated operations;
- roles are exact values, never substring-matched;
- groups contain canonical IDs, not display labels;
- conversation_id is request context, not proof of conversation access;
- SystemPrincipal is explicit and limited to bootstrap, migration, and trusted
  maintenance paths;
- there is no anonymous or default principal fallback.

### 6.2 Propagation

Construct PrincipalContext once at authenticated ingress and propagate it
through:

- AgentActionsTask and every resource UI action;
- AgentLoopTask context and direct executor calls;
- ToolRelayService registration and execution;
- ToolHandler context setters;
- manage_resource;
- scheduled jobs and task runners;
- Telegram and other non-HTTP clients;
- PFP runtime host calls;
- skill resolution and RelaySkillsFs.

Internal-auth tokens used by CLI containers should be bound server-side to the
principal identity that minted them. The bridge must not be trusted to assert a
different user_id, role, or group list.

### 6.3 Canonical groups

Do not authorize against the current comma-separated http.auth.groups value.

Add durable PawFlow group identities with:

- immutable group_id;
- display_name;
- source type: local or IdP;
- IdP issuer/provider identifier when applicable;
- external group identifier;
- membership revision;
- created_at and updated_at.

Canonical external IDs must include their issuer/provider namespace, for
example:

~~~text
oidc:<issuer-hash>:<external-group-id>
~~~

Do not use a mutable display name as an ACL subject.

OAuth login updates the durable membership snapshot transactionally. Local
groups are managed by administrators. Background work resolves current durable
membership rather than requiring a live OAuth session.

Document the IdP freshness boundary: an external removal becomes effective when
PawFlow next refreshes or receives that user's claims. Provide an admin
operation to remove membership immediately.

### 6.4 Operator mapping is required for group subjects

Making an external group a first-class ACL subject would otherwise invert the
existing `core/auth_groups.py` invariant: an administrator of the identity
provider could create a group, add themselves to it, and obtain PawFlow
resource access without any operator action inside PawFlow. Group name
squatting would become a privilege escalation path.

Therefore an external group becomes eligible as an ACL subject only after an
operator registers it. Registration is the same act of operator intent that
`core/auth_groups.py` already requires for role mapping, extended to carry an
ACL-subject eligibility flag:

- an unregistered external group is never a valid ACL subject, and a policy
  referencing one fails validation;
- registration is an admin operation, is audited, and records the issuer,
  external ID, canonical `group_id`, and eligibility;
- registering a group for ACL use does not grant it any PawFlow role, and
  mapping a group to a role does not make it ACL-eligible; the two decisions
  stay separate;
- locally defined PawFlow groups are ACL-eligible by construction because an
  operator created them;
- deregistration invalidates matching grants through the normal revocation
  path in section 15.

Reuse the claim parsing and precedence logic in `core/auth_groups.py`. Do not
add a second, parallel group resolution path.

### 6.5 Exact role matching is a prerequisite, not a consequence

The invariant "roles are exact values, never substring-matched" is not true of
the current codebase and does not become true by writing new code alongside the
old. Before any ACL evaluation ships:

1. add one canonical role parser in the authorization layer that converts the
   authenticated role attribute into an immutable set of validated PawFlow role
   IDs;
2. store that set on `PrincipalContext` and make the ACL evaluator compare exact
   set members without depending on `FlowFile` or `core/admin_scope.py`;
3. make `core/admin_scope.is_admin()` delegate to the same parser;
4. migrate every substring-based admin gate to that helper;
5. reconcile `docs/ADMIN_CROSS_USER_SCOPES_PLAN.md`, whose documented `_is_admin`
   gate still uses a substring test.

Tests must cover whitespace, duplicate roles, `admin-readonly`, `non-admin`, an
unknown role, and a real `admin` member. Add a focused source audit for the known
substring gate forms, but do not treat a brittle repository-wide text search as
the authorization proof; runtime tests at every privileged ingress remain the
release gate.

This work is listed in Phase 1 and is a release gate, not cleanup.

## 7. Stable resource identity and revisions

### 7.1 Stored identity

Every ResourceStore definition gains protected metadata:

~~~json
{
  "resource_id": "uuid",
  "revision": 1,
  "created_at": 0,
  "updated_at": 0
}
~~~

resource_id is immutable. revision increments on every content mutation but not
on ACL-only or binding-only changes.

ResourceStore and ScopedRepository must reject attempts to set or modify
resource_id, revision, owner metadata, ACL metadata, or binding metadata through
ordinary resource data.

### 7.2 ResourceLocator

Use an internal ResourceLocator to separate actor from owner:

~~~python
@dataclass(frozen=True)
class ResourceLocator:
    resource_id: str
    resource_type: str
    scope: str
    owner_user_id: str = ""
    conversation_id: str = ""
    name: str = ""
~~~

Paths remain the storage source of truth for scope and owner. The locator index
maps resource_id to the current path.

### 7.3 Copy, move, promotion, and deletion

- copy creates a new resource_id, revision 1, and the target scope's default
  ACL;
- move within the same ownership boundary may preserve resource_id;
- promotion or demotion across ownership semantics requires an explicit policy
  decision and resets ACL/bindings by default;
- promotion to global remains admin-only;
- deletion writes a tombstone before removing content so bindings never fall
  through to a same-name resource;
- rename preserves resource_id and increments revision;
- ownership transfer is not implemented in v1.

## 8. Policy model

### 8.1 Storage

Create AccessPolicyStore as server-owned metadata, separate from portable
resource content. Use one atomically written record per resource_id rather than
one shared JSON file, so unrelated policy updates do not race.

Suggested layout:

~~~text
data/system/resource_access/policies/<resource_id>.json
data/system/resource_access/bindings/users/<user_id>/<binding_id>.json
data/system/resource_access/bindings/conversations/<owner>/<conv>/<binding_id>.json
data/system/resource_access/tombstones/<resource_id>.json
data/system/resource_access/index.json
~~~

The index is rebuildable from resources and policy/binding records. It is a
cache, not the source of truth.

### 8.2 Policy shape

~~~json
{
  "policy_id": "uuid",
  "resource_id": "uuid",
  "mode": "public",
  "grants": [],
  "policy_revision": 1,
  "created_by": "user-id",
  "created_at": "ISO-8601",
  "updated_by": "user-id",
  "updated_at": "ISO-8601"
}
~~~

A grant is:

~~~json
{
  "grant_id": "uuid",
  "subject": {
    "type": "user",
    "id": "stable-subject-id"
  },
  "permissions": ["read", "use"],
  "created_by": "user-id",
  "created_at": "ISO-8601"
}
~~~

Allowed subject types:

- user;
- group;
- role.

Allowed permissions:

- read;
- use.

manage is implicit for the owner and explicit for administrative override; it
is not grantable in v1.

### 8.3 Default policies

- global: mode public, authenticated principals receive read and use;
- user: mode private, owner only;
- conversation: inherited from conversation_access, no external grants in v1.

Changing a global resource from public to restricted requires at least one
valid grant or explicit confirmation that it becomes admin-only.

### 8.4 Matching semantics

- exact user match;
- exact canonical group membership;
- exact PawFlow role membership;
- matching grants union their permissions;
- use implies read;
- no grant ordering;
- no deny entries;
- owner rights are evaluated before grants;
- explicit admin override is evaluated separately and audited;
- disabled/deleted users and groups never match;
- unregistered external groups never match (section 6.4);
- role comparison uses the canonical immutable role set on `PrincipalContext`,
  never a substring test or a `FlowFile`-specific helper (section 6.5).

Policy validation rejects unknown users, groups, roles, permissions, duplicate
grant IDs, malformed UUIDs, and grants on unsupported conversation resources.

## 9. Authorized resource APIs

The current user_id argument often represents both actor and target owner.
Replace this ambiguity with APIs that require PrincipalContext.

Recommended public boundary:

~~~python
list_native(resource_type, principal, conversation_id)
list_shared(resource_type, principal)
list_catalog(resource_type, principal, conversation_id)
get_by_ref(resource_ref, principal, permission="read")
create(resource_type, name, owner_scope, principal, data)
update(resource_ref, principal, patch)
delete(resource_ref, principal)
set_policy(resource_ref, principal, policy_patch)
create_binding(resource_ref, principal, target_scope, options)
remove_binding(binding_id, principal)
resolve_binding(binding_id, principal, permission="use")
~~~

ScopedRepository remains a raw persistence primitive but must not be callable
from request handlers as an authorization shortcut.

ResourceStore should either become the authorized facade or be split into:

- RawResourceRepository: internal storage only;
- ResourceAccessService: policy-aware read and resolution;
- ResourceManager: authorized mutations.

No public API may accept an empty principal and infer global/system access.

## 10. Discovery and precedence

### 10.1 Native cascade

Preserve the existing native name cascade:

~~~text
current conversation resource
-> caller-owned user resource
-> ACL-visible global resource
~~~

Filter each candidate for access before it can mask a lower candidate. An
invisible resource must behave as absent.

### 10.2 Shared catalog

Another user's ACL-shared resources are returned separately as shared_with_me.
They do not enter list_native, list_all-style name deduplication, agent
selection, tool loading, MCP discovery, skill manifests, or hook lookup merely
because a grant exists.

Catalog rows include safe metadata:

- resource_id;
- type;
- name;
- description;
- publisher display identity;
- revision;
- accepted revision, if any;
- binding status;
- review/risk summary;
- required parameter and secret slot names;
- permissions;
- update_pending;
- unavailable/revoked state.

Catalog rows do not include:

- resolved values;
- secret values or secret keys bound by the consumer;
- literal auth/env values;
- private package paths;
- full executable source unless the principal requests detail and has read.

### 10.3 Explicit bindings

A shared resource becomes usable only through a binding:

~~~json
{
  "binding_id": "uuid",
  "resource_id": "uuid",
  "resource_type": "skill",
  "accepted_revision": 3,
  "scope": "conversation",
  "owner_user_id": "consumer",
  "conversation_id": "optional",
  "alias": "deploy-acme",
  "enabled": true,
  "secret_bindings": {},
  "parameter_bindings": {},
  "review": {},
  "created_at": "ISO-8601",
  "updated_at": "ISO-8601"
}
~~~

Binding rules:

- the consumer must currently have use permission;
- the exact revision must have been reviewed and accepted;
- alias is unique within its binding scope;
- a collision with a native effective name is rejected, not resolved by
  precedence;
- bindings store resource_id and accepted_revision;
- a binding never copies ownership;
- revocation disables the binding immediately;
- deletion leaves an unavailable binding/tombstone for diagnostics;
- a publisher update marks update_pending and does not execute the new revision
  until reaccepted.

Runtime records should store binding_id or resource_id, not a publisher name.

## 11. Secret and variable safety

### 11.1 Separate resource parameters from secret slots

Shared resources must declare an interface:

~~~json
{
  "parameter_requirements": [
    {"name": "region", "required": false}
  ],
  "secret_requirements": [
    {"name": "api_key", "required": true}
  ]
}
~~~

Add a strict shared-resource resolver:

- ordinary parameters resolve against the consumer's normal parameter cascade;
- secret slots resolve only through the binding's secret_bindings;
- no plain expression key may fall through into the secret cascade;
- no publisher secret store is consulted;
- forced global/user/conversation secret scope is rejected;
- unresolved required slots fail before process or network startup;
- resolution occurs only at point of use;
- resolved configuration is never cached in ResourceStore or returned by APIs.

Reuse the PFP runtime's declared-secret/binding pattern instead of inventing a
second incompatible manifest concept.

### 11.2 Binding ownership

Secret bindings belong to the consumer user or target conversation. Policy
owners and other recipients cannot list them. APIs return only:

- slot name;
- required/optional;
- bound true/false;
- source scope label if safe.

They never return the selected secret key or value to the publisher.

### 11.3 Sharing validation

Before a user resource can receive a cross-user grant:

- validate its type and schema;
- inspect fields marked sensitive;
- reject literal MCP auth and env credentials;
- reject embedded private key/token fields where detectable;
- extract and validate declared secret slots;
- run the existing review pipeline for skills and executable content;
- compute a content hash;
- attach a shareability/risk report;
- require explicit force only from the receiving user at acceptance, never from
  the publisher on the receiver's behalf.

Prompts and source files can contain undetectable literals. The UI must warn the
publisher and recipient that full content review is required.

### 11.4 Tool relay isolation

Before cross-user tool, MCP, or hook use is enabled:

- stop injecting all consumer secrets into third-party executable resources;
- pass only explicitly bound secret slots to that resource process/call;
- prevent post-tool hooks from receiving internal secret environment maps;
- redact bound values from results, errors, logs, SSE, transcripts, and audit
  details;
- ensure pre/post hooks see sanitized arguments;
- clear secret caches when bindings or secrets change.

This is a release blocker for executable cross-user sharing.

## 12. Type-specific behavior

### 12.1 Agent

- discoverable after read grant;
- add-to-conversation requires use grant and accepted binding;
- conv_agent_config stores definition_ref/resource_id;
- selected instance names remain conversation-local labels;
- loss of access blocks execution and marks the instance unavailable;
- no silent fallback agent.

### 12.2 Skill

- assignment stores resource_id plus optional alias/params/condition;
- load_skill checks binding, permission, revision, and agent assignment;
- one-shot skill run performs the same checks;
- publisher update requires reacceptance;
- skill assets use the shared virtual filesystem described below.

### 12.3 Prompt and task_def

- may be visible in the shared catalog;
- insertion/use requires an accepted binding;
- scheduled jobs recheck use permission at every execution;
- revocation prevents the next scheduled run;
- copied snapshots receive a new resource_id and are no longer linked shares.

### 12.4 MCP

- ACL visibility alone never loads discovered tools;
- accepted, enabled binding is required;
- URL/command/args/auth/env are resolved in the consumer context;
- only explicit secret slots are injected;
- registry cache keys include policy/binding revisions;
- revocation disconnects or evicts resource-specific clients before another
  call.

### 12.5 Tool

- ACL visibility alone never adds the tool to ToolRegistry;
- accepted binding and code review are required;
- resource_id is attached to the handler runtime metadata;
- every invocation rechecks binding status or a monotonic access epoch;
- only declared secret slots enter its sandbox;
- code updates invalidate acceptance.

### 12.6 Agent hook

Agent hooks can inspect or rewrite messages and tool traffic. Cross-user hook
use is deferred until tool/MCP secret isolation is complete.

When enabled later:

- binding by resource_id;
- explicit per-conversation acceptance;
- accepted revision pin;
- fail policy retained in the consumer binding;
- sanitized event payloads only;
- immediate removal from the hook runner on revocation.

### 12.7 Theme and private_gateway_skin

Treat CSS and HTML/template resources as active content, not passive metadata.

- never auto-apply after an ACL grant;
- sanitize/review before acceptance;
- apply by binding/resource_id;
- revoke future delivery immediately;
- document that already delivered browser content cannot be made unseen.

These types may ship after agents/skills/prompts/task_defs.

## 13. Shared skill filesystem

### 13.1 Virtual layout

Extend the server-side skill FUSE with:

~~~text
/skills/shared/<resource_id>/SKILL.md
/skills/shared/<resource_id>/scripts/...
/skills/shared/<resource_id>/references/...
~~~

Do not expose /skills/users/<publisher> to a consumer.

### 13.2 Authorization

RelaySkillsFs receives PrincipalContext or a server-bound principal handle and
conversation context. For every shared path operation it checks:

- resource exists and is a skill;
- principal currently has use permission;
- an enabled user/conversation binding exists;
- accepted_revision equals current or selected immutable revision;
- requested path remains inside that one skill directory.

Directory listings reveal only bound shared skills. Open file descriptors are
closed or invalidated on revocation where practical; at minimum no new opens or
reads are authorized after the access epoch changes.

### 13.3 CLI mounts

CLI providers mount the shared virtual view, not the publisher's host
directory. skill_mount_dir returns /skills/shared/<resource_id> for bound shared
skills. Cold and live sessions use the same path so assignment changes do not
require a provider-specific workaround.

## 14. Revisions and update acceptance

Resource mutation produces revision N+1 and a new content hash.

For native owner/global resources, current update semantics remain immediate.

For cross-user bindings:

1. policy remains valid unless explicitly changed;
2. accepted_revision remains N;
3. binding becomes update_pending;
4. runtime refuses N+1;
5. recipient reviews the diff or full new content;
6. recipient accepts N+1;
7. binding atomically updates accepted_revision and review metadata;
8. affected caches and live registries are invalidated.

Immutable revision snapshots are part of v1, not a later addition. Without
them, a publisher editing a skill or prompt breaks every consumer runtime
mid-conversation: the accepted revision is no longer retrievable, so
`update_pending` degrades from "keep using N until you review N+1" to "stop
working now". That is a publisher unilaterally interrupting other users' work,
which no consumer consented to.

Required v1 behavior:

- accepting revision N retains an immutable, content-addressed snapshot of N;
- the runtime continues to resolve N while the binding is `update_pending`;
- snapshots use reference counts and are released only after the last binding
  advances or is removed;
- capacity is reserved and charged to the consumer scope when a binding accepts
  a revision;
- insufficient capacity rejects that new acceptance before the binding changes;
- an existing accepted snapshot remains readable until its binding advances,
  is removed, or loses authorization;
- publisher updates never depend on a consumer's remaining quota and cannot be
  blocked by a consumer retaining an older accepted revision.

Never silently run N+1.

## 15. Revocation semantics

A committed ACL or membership revocation guarantees:

- no new discovery response exposes the resource;
- direct reads return not found;
- no new activation, assignment, load, or invocation starts;
- existing bindings are marked unavailable;
- tool and MCP registries are evicted;
- skill manifests and FUSE paths disappear;
- hook runners drop the hook;
- scheduled executions fail closed;
- the UI receives a resource_access_changed event;
- denial and administrative cause are audited without resource content.

Define in-flight behavior explicitly. The minimum safe contract is: revocation
blocks new calls immediately and cancels cancellable resource-specific calls.
Long-running uncancellable calls may finish, but their result still passes
redaction and their binding cannot be reused.

## 16. Cache design

Keep raw per-scope repository caches ACL-agnostic. Filter only in the authorized
layer.

Introduce monotonic revisions:

- global policy revision;
- per-resource policy revision;
- per-user membership revision;
- per-binding revision;
- resource content revision.

Policy-aware cache keys include the relevant revisions or an access epoch:

~~~text
(user_id, conversation_id, roles_hash, groups_hash,
 membership_revision, access_epoch, resource_type)
~~~

Update:

- ToolRelayService registry cache;
- dynamic tool loader cache;
- MCP discovery/client cache;
- UI list cache;
- skill manifest/cache;
- hook binding cache;
- resource locator/index cache;
- RelaySkillsFs path/fd authorization state.

Any global ACL change must invalidate affected users, not only the actor's
current conversation. Prefer revisioned cache keys plus targeted eviction to
fragile caller-by-caller invalidation.

## 17. API and tool contracts

### 17.1 UI actions

Add:

- get_resource_policy;
- update_resource_policy;
- list_shared_resources;
- get_shared_resource_detail;
- accept_shared_resource;
- update_shared_binding;
- revoke_shared_binding;
- list_resource_bindings;
- accept_shared_resource_update.

All actions use resource_id. Name may be included only for display.

Unauthorized read/get/detail returns 404. Malformed input returns 400.
Authenticated but non-owner policy mutation returns 404 unless an explicit
admin-management endpoint is used.

### 17.2 Existing actions

Change:

- list_resources: retain native sections and add shared_with_me;
- get_resource_detail: accept resource_id and apply read authorization;
- create/update/delete/copy: separate actor PrincipalContext from target owner;
- activate/deactivate: persist exact bindings/references;
- update_conversation_hooks: validate hook bindings by ID;
- tool/MCP filters: use IDs or binding IDs internally;
- agent add/select/config: persist definition_ref;
- skill assign/unassign: persist skill resource references.

Deprecate and remove the current name-only share_resource behavior in the
one-shot migration. Its replacement manages ACL grants and bindings explicitly.

### 17.3 manage_resource

Add actions only after PrincipalContext reaches the handler:

- policy_get;
- policy_update;
- shared_list;
- shared_get;
- binding_accept;
- binding_remove;
- binding_accept_update.

Agent-originated policy mutations remain conversation-local or forbidden by
default. An agent must not grant another principal access to a user/global
resource merely because it can call manage_resource.

### 17.4 Events

Publish sanitized events:

- resource_policy_changed;
- resource_shared;
- resource_binding_changed;
- resource_update_pending;
- resource_access_revoked.

Events carry IDs, type, display name, revision, and state. They carry no
resource body, secret key, secret value, auth configuration, or executable
source.

## 18. Frontend

Add to the Resources panel:

- native and Shared with me sections;
- visibility badge: public, restricted, private, conversation;
- publisher badge;
- share/manage-access dialog for owners/admins;
- exact user/group/role subject picker;
- permissions read/use;
- risk and review summary;
- required parameter/secret slots;
- Accept and Bind flow;
- target scope: user or current conversation;
- alias conflict handling;
- update pending and re-review flow;
- revoked/unavailable state;
- admin-only cross-user override affordances.

The ACL editor must never accept free-form user/group display text as the
stored subject ID. Pickers resolve stable IDs.

Do not render secret key names in publisher-facing or shared catalog payloads.
The consumer binding dialog may show only that consumer's own secret picker.

Add EN/FR/ES i18n strings and keyboard/accessibility coverage.

## 19. Audit and observability

Audit:

- policy create/update;
- grant add/remove;
- binding accept/remove/update;
- secret binding change without secret name/value;
- admin override reads and writes;
- access denial reason class;
- revocation fanout and cache eviction;
- migration results.

Each audit event includes:

- event UUID and timestamp;
- actor user_id;
- resource_id and type;
- owner user_id where appropriate;
- affected subject type/id;
- permission names;
- old/new policy or binding revision;
- conversation_id if scoped;
- source path: UI, agent, relay, scheduler, Telegram, system migration.

Never log resource bodies, prompts, source code, auth/env dictionaries, secret
keys selected by consumers, or secret values.

Metrics:

- ACL evaluation count/latency;
- allow/deny counts by operation and subject type;
- policy and membership cache hit rate;
- binding update_pending count;
- revocation-to-cache-eviction latency;
- shared runtime invocation counts by resource type;
- failed secret-binding resolution count.

## 20. Concurrency and atomicity

Use atomic temp-file replacement and per-resource locks for policy and binding
writes.

Required ordering:

### Resource update

1. lock resource_id;
2. write new content/revision atomically;
3. update locator/index;
4. mark bindings update_pending;
5. bump access epoch;
6. evict runtime caches;
7. publish event;
8. release lock.

### ACL revocation

1. lock policy/resource;
2. write policy revision atomically;
3. mark affected bindings unavailable;
4. bump access epoch;
5. evict registries/FUSE authorization;
6. cancel resource-specific calls where supported;
7. publish and audit;
8. release lock.

### Delete

1. lock resource_id;
2. write tombstone;
3. revoke policy/bindings;
4. evict runtime state;
5. remove content;
6. update index;
7. publish and audit;
8. release lock.

Never leave a window in which content is removed and an unqualified reference
can fall through to another same-name resource.

## 21. One-shot migration

PawFlow has no backward-compatibility requirement. Perform one explicit,
restart-safe migration.

### 21.1 Preflight

- stop new resource mutations;
- flush conversation writers;
- snapshot repository and conversation metadata;
- validate every resource path and type;
- enumerate duplicate IDs/names and malformed directory resources;
- fail before mutation if identity assignment cannot be deterministic.

### 21.2 Assign IDs and revisions

Enumerate every ResourceStore resource across global, user, and conversation
scopes, including directory-backed skills/themes/skins and extras-backed task
definitions.

For each:

- assign UUID resource_id;
- set revision 1;
- compute content hash;
- write the scope-default policy;
- add locator index entry.

ACL metadata is not written into SKILL.md or package-portable content.

### 21.3 Rewrite persistent references

Rewrite and validate at least:

- conv_agent_config definition -> definition_ref;
- agent assigned_skills -> typed resource references;
- active_resources agent/MCP entries -> instance or binding references;
- conversation_hooks -> hook resource references;
- tool_mcp_filters name entries -> resource/binding IDs where repository-backed;
- scheduled task_def references;
- plan agent/resource references where persisted;
- package install records and runtime metadata;
- conversation import/export payloads;
- first-run/bootstrap conversation metadata;
- Telegram-created conversation agent configs.

Selected conversation agent instance names remain names; their repository
definition becomes an exact reference.

### 21.4 Validate

For every rewritten reference:

- referenced resource exists;
- type matches;
- scope and owner are valid;
- conversation references resolve through its real owner;
- selectedAgent remains non-empty;
- assigned skill references are unique;
- hook and MCP bindings are valid;
- no unresolved legacy resource name remains in protected fields.

### 21.5 Commit or rollback

Write a migration journal with phase UUIDs/timestamps. Each phase is idempotent.
Only switch the schema/version marker after all validation succeeds. On failure,
restore the snapshot and leave the old schema marker unchanged.

Remove legacy name-only readers after the migration; do not keep dual-resolution
fallbacks.

## 22. Implementation phases

### Phase 0 - Threat model and contracts

Deliver:

- final invariants;
- operation/permission matrix;
- per-type risk classification;
- PrincipalContext contract;
- resource reference schema;
- migration fixture inventory.

Exit criteria:

- security review accepts secret and update semantics;
- no unresolved decision affects persistence format.

### Phase 1 - Principal and durable groups

Implement:

- PrincipalContext;
- canonical group records and membership revisions;
- OAuth-to-canonical-group mapping, extending `core/auth_groups.py` rather than
  duplicating it;
- operator registration of ACL-eligible groups (section 6.4);
- canonical immutable role parsing on `PrincipalContext`;
- migration of every substring-based admin gate to the shared exact-role helper,
  including reconciliation with `docs/ADMIN_CROSS_USER_SCOPES_PLAN.md`
  (section 6.5);
- propagation through UI, agent loop, relay, schedulers, Telegram, and PFP;
- identity-bound internal tokens;
- exact role matching.

Tests:

- cross-provider same display name does not collide;
- stale/spoofed bridge identity is rejected;
- scheduled execution receives durable membership;
- membership removal increments revision and invalidates access;
- missing principal fails closed;
- an unregistered IdP group never matches a grant;
- confusing role strings such as `admin-readonly` and `non-admin` never grant
  administrative or ACL authority;
- every privileged ingress uses the canonical role set or its shared helper.

No resource visibility changes in this phase.

### Phase 2 - Resource IDs, locators, and revisions

Implement:

- protected resource_id/revision metadata;
- ResourceLocator index;
- revision increments;
- copy/move/delete/tombstone semantics;
- raw repository API restrictions;
- migration tooling and dry-run report.

Tests cover JSON, Markdown, directory, extras-backed, copy, move, rename,
promotion, deletion, interrupted migration, and duplicate IDs.

### Phase 3 - AccessPolicyStore and authorized reads

Implement:

- policy persistence/validation;
- default policy creation;
- ACL evaluator;
- admin override/audit;
- authorized ResourceAccessService;
- global restricted discovery;
- 404 anti-probing behavior;
- policy-aware cache revisions.

Initially enable ACL editing only for global resources and user-resource
read-only sharing. Shared resources appear only in shared_with_me.

### Phase 4 - Bindings and deterministic resolution

Implement:

- user/conversation binding stores;
- acceptance and alias validation;
- revision pin/update_pending;
- ID-based native and shared resolution;
- migration of active_resources, agent definitions, skills, hooks, MCP filters,
  schedules, and package records;
- removal of name-only share_resource.

Do not enable executable cross-user use yet.

### Phase 5 - Secret requirements and strict resolver

Implement:

- shared-resource parameter/secret requirement schema;
- consumer-owned bindings;
- strict no-implicit-secret resolution;
- PFP-compatible requirement helpers;
- sharing validation and literal-secret blockers;
- sanitized binding APIs;
- cache invalidation and redaction tests.

This phase is a blocker for tools, MCPs, and hooks.

### Phase 6 - Passive and instruction resources

Enable accepted use for:

- prompt;
- task_def;
- agent;
- skill instructions without external assets.

Add re-review on publisher updates and revocation behavior.

Although agents and skills are instructions, treat them as untrusted active
content and require explicit review/acceptance.

### Phase 7 - Shared skill assets

Implement:

- /skills/shared/<resource_id>;
- RelaySkillsFs ACL checks;
- CLI virtual mount integration;
- skill_mount_dir shared paths;
- fd/cache revocation;
- binary and text asset tests;
- traversal/symlink/race security tests.

### Phase 8 - MCP and tool execution

Only after Phase 5 passes its security gates:

- load only accepted bindings;
- bind registry handlers to resource_id/revision;
- inject declared secrets only;
- recheck access epoch at invocation;
- disconnect/evict on revocation;
- prevent hook/event argument secret exposure;
- full relay/CLI/provider test matrix.

### Phase 9 - Hooks, themes, and skins

Enable these types separately with type-specific sanitization and review.
Agent hooks require the strongest event-payload and secret-isolation review.

### Phase 10 - UI, documentation, and operational tooling

Complete:

- ACL/binding UI;
- admin management view;
- migration command and report;
- audit/metrics panels;
- operator documentation;
- API/tool documentation;
- backup/restore handling;
- group lifecycle documentation;
- release notes and rollback procedure.

## 23. Authorization matrix

| Operation | Owner | ACL read | ACL use | Conversation reader | Conversation writer | Admin override |
|---|---:|---:|---:|---:|---:|---:|
| List safe metadata | yes | yes | yes | yes for conv | yes for conv | explicit |
| Read full definition | yes | yes | yes | yes for conv | yes for conv | explicit + audit |
| Accept/bind | yes | no | yes | no | target-owner rules | explicit |
| Activate/use | yes | no | yes + binding | conversation policy | conversation policy | explicit |
| Update content | yes | no | no | no | yes for conv-owned resource | explicit + audit |
| Delete | yes | no | no | no | yes for conv-owned resource | explicit + audit |
| Change ACL | yes | no | no | no | no in v1 | explicit + audit |
| Bind consumer secret | consumer only | no | consumer only | own binding only | own/conv binding | no secret visibility |

Conversation behavior must continue to use conversation_access as the source of
owner/write/read rights. ACLs do not weaken it.

## 24. Test matrix

### Policy evaluation

- global public visible to authenticated users;
- global restricted invisible without a matching grant;
- exact user grant;
- exact role grant;
- exact canonical group grant;
- permissions union across subjects;
- owner implicit management;
- no substring role/group match;
- disabled user/group does not match;
- malformed policy fails closed;
- unauthorized detail returns 404;
- hidden resource does not mask a visible lower-scope resource.

### Precedence and identity

- conversation overrides own user/global by name;
- own user overrides visible global;
- shared user resource never enters native name resolution;
- two shared resources with the same name remain distinct;
- alias collision is rejected;
- exact ID resolves only that resource;
- deleted exact ID never falls through by name;
- copy receives a new ID;
- update retains ID and increments revision.

### Binding lifecycle

- read-only grant cannot bind;
- use grant can accept;
- acceptance pins revision;
- update creates update_pending;
- old acceptance cannot execute new revision;
- ACL revoke disables binding;
- group removal disables binding;
- resource delete leaves useful unavailable state;
- recipient cannot inspect another recipient's binding;
- publisher cannot inspect consumer secret bindings.

### Secrets

- plain shared expression cannot resolve a secret implicitly;
- declared slot resolves only its selected consumer binding;
- publisher secret is never consulted;
- another user's secret is never consulted;
- global secret requires an explicitly permitted consumer binding policy;
- missing required slot fails before startup/network call;
- literal MCP auth/env blocks sharing;
- bound values are redacted from result, error, log, SSE, transcript, hook
  payload, and audit;
- publisher update cannot add a new secret requirement without reacceptance.

### Runtime paths

Exercise the same allow/deny decision through:

- web UI;
- manage_resource;
- direct agent executor;
- ToolRelayService;
- Codex/Claude/Gemini CLI MCP bridge;
- API providers;
- scheduled task;
- plan execution;
- Telegram;
- PFP runtime;
- skill FUSE;
- conversation collaborators.

### Revocation and caches

- warm UI cache;
- warm tool registry;
- warm MCP client;
- warm skill manifest;
- open FUSE handle;
- warm hook runner;
- then revoke and prove no new operation succeeds;
- group membership revision invalidates warm caches;
- global policy change affects users other than the actor;
- concurrent update/revoke/delete produces no stale authorization window.

### Migration

Use fixtures containing:

- every resource type and scope;
- duplicate names across all scopes and owners;
- assigned skills in string and object form;
- multi-agent conversation configs;
- active MCPs;
- hook bindings;
- task definitions in conversation extras;
- package-installed resources;
- malformed and invalid skills;
- copied/imported conversations;
- selected agents whose definitions are global/user/conversation scoped.

Assert no protected legacy name reference remains after migration.

## 25. Release gates

Cross-user sharing is releasable only when:

1. every request/runtime path carries PrincipalContext;
2. all resources have stable IDs and revisions;
3. native behavior remains conversation -> user -> global;
4. shared resources cannot silently enter the native cascade;
5. ACL grants alone cannot activate content;
6. updates require recipient reacceptance;
7. executable resources receive only declared, explicitly bound secrets;
8. another user's skill tree is not exposed;
9. warm-cache revocation tests pass;
10. migration is idempotent, restart-safe, and rollback-tested;
11. full test suite and security scan pass;
12. docs and all affected tools/actions are updated in the same change.

## 26. Expected implementation touchpoints

Core:

- core/resource_store.py
- core/repository.py
- core/paths.py
- core/security.py
- core/auth_groups.py
- core/expression.py
- core/internal_auth.py
- core/conv_agent_config.py
- core/skill_lifecycle.py
- core/skill_resolver.py
- core/tool_loader.py
- core/agent_hooks.py
- core/tool_mcp_filters.py
- core/conversation_creation.py
- core/cli_workspace_mounts.py
- new principal, resource access, policy, binding, and migration modules

Handlers and runtime:

- core/handlers/manage_resource.py
- core/handlers/skills.py
- services/tool_relay_service.py
- services/_tool_relay_registry.py
- services/_tool_relay_execute.py
- services/relay_skills_fs.py
- tasks/ai/actions/_agentres_k1.py through _agentres_k5.py
- tasks/ai/actions/agent_resource.py
- tasks/ai/agent_actions.py
- tasks/ai/agent_loop.py and agent context/executor paths
- scheduling, plans, Telegram, conversation import/export, and PFP paths

Frontend:

- resource catalog/list/detail modules;
- resource editor;
- agent/skill/MCP/hook binding dialogs;
- i18n locale files;
- admin resource view.

Tests:

- new focused policy, binding, identity, secret, migration, FUSE, cache, and
  runtime-path suites;
- updates to existing resource, security scope, conversation sharing, skill,
  MCP, tool, hook, CLI provider, Telegram, PFP, and UI static tests.

## 27. Delivery order and intermediate value

This plan must not be executed as a single all-or-nothing project. Each of the
following stops is a shippable state with stated user-visible value, so the
work can be paused after any of them without leaving a half-built
authorization layer.

| Stop | Phases | What users get | What is still absent |
|---|---|---|---|
| S1 | 0-1 | Exact roles, durable groups, principal at every ingress. No visibility change. | No sharing at all. |
| S2 | 2-3 | An admin can restrict a global resource to selected users, groups, or roles. | No user-to-user sharing. |
| S3 | 4 | Users see another user's shared resources and bind them by ID. | Nothing executable can run. |
| S4 | 5-6 | Prompts, task defs, agents, and asset-free skills usable across users. | No shared skill assets, MCPs, tools, hooks, themes. |
| S5 | 7-9 | Executable and asset-backed sharing. | - |

S2 is the first stop that satisfies goal 1 of section 1 and is the recommended
initial commitment. Do not start Phase 4 before S2 is in production.

Scale note for planning: Phase 1 touches every authenticated ingress path, and
Phase 2 rewrites persistent references across conversations, agent configs,
schedules, and package records. Neither is a single-sprint item; size them
before committing to a date.

## 28. Final architectural rule

The implementation must preserve this separation:

~~~text
Scope:
  ownership + storage + native precedence

ACL:
  who may read or use

Binding:
  explicit consumer acceptance + target scope + alias + accepted revision

Credentials:
  consumer-owned, declared, explicitly bound secret slots
~~~

Any implementation that merges these layers, auto-discovers shared executable
content, resolves shared secrets by name, or falls back from a revoked ID to an
unqualified name violates the design.

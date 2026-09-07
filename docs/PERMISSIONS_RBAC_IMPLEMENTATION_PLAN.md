# Scoped Permissions, Permission Groups, and Roles: Implementation Plan

Status: **proposed**. This document specifies the requested implementation; it does not claim that RBAC has been implemented.
Date: 2026-09-07.
Scope: instance-wide application authorization across PawFlow entry points, resources, and execution paths.

## 1. Agreed contract

The following decisions come from the user's requirements and take precedence over the earlier exploratory proposal:

1. Every exposed operation must have an associated permission contract.
2. A **permission group** contains permission rules.
3. A **role** contains one or more permission groups.
4. A user can have multiple roles.
5. Effective grants are the union of grants from all active roles and their groups.
6. The least restrictive applicable grant wins. A role cannot cancel another role's grant.
7. An administrator is an ordinary role whose group contains `*`. Its name has no special authority.
8. `*` matches every registered permission, including permissions registered in future versions or by subsequently installed trusted extensions.
9. `A.*` matches every registered descendant of namespace `A`, at any depth, including future descendants.
10. For resource operations, the resource's storage scope is part of the permission name.

This replaces the earlier suggestion to expand wildcards into a fixed list at save time. Wildcards remain stored rules with dynamic meaning.

The scope reach vocabulary below is a concrete design choice that makes ownership and sharing explicit. Role-level negative permissions, numeric role rankings, nested roles, nested permission groups, and direct user grants are excluded from the first implementation.

The user requested a plan in `docs/`. Runtime implementation and its release version are separate work.

## 2. Existing code and related plans

The following source areas were inspected while preparing this document. This is a starting map, not a claim that all entry points have already been audited.

| Existing area | Current behavior | Required change |
|---|---|---|
| [core/security.py](../core/security.py) | Fixed `admin`/`user` enum; one role on User and Session; static ROLE_PERMISSIONS; user/session persistence and last-admin checks | Multiple role IDs, authoritative policy lookup, migration, capability-based administrator protection |
| [core/auth_groups.py](../core/auth_groups.py) | Exact operator mappings from IdP groups; highest mapped role; local/remote precedence | Map to role ID sets; preserve explicit source selection; remove ranking |
| [core/admin_scope.py](../core/admin_scope.py) | Exact admin check, cross-user listing and owner overrides | Check the operation's scoped permission; keep actor/owner separation |
| [tasks/ai/actions/admin_settings.py](../tasks/ai/actions/admin_settings.py) | Administrative handlers include substring role checks | Replace role-name checks before arbitrary role names are accepted |
| [tasks/ai/agent_actions.py](../tasks/ai/agent_actions.py) | Action dispatch, slash-command redispatch, inline results, background execution, extension handlers | Register and enforce every action, including early returns and final redispatched actions |
| [core/tool_registry.py](../core/tool_registry.py) | Argument preparation and execution of handlers | Enforce canonical tool/sub-action contracts after normalization and before side effects |
| [core/conversation_access.py](../core/conversation_access.py) | Owner and accepted read/write collaborators; storage owner differs from requester | Reuse relationship facts and ownership routing in scoped permission resolution |
| [core/filesystem.py](../core/filesystem.py) | Filesystem operation/path checks | Combine with user permissions and actual relay/process access limits |
| [core/capability_auth.py](../core/capability_auth.py) | Opaque tokens bound to resource, user, conversation, and session | Preserve binding checks; add current-permission checks at mint/use/revocation boundaries |
| [core/tool_authorization.py](../core/tool_authorization.py) | Tool policy gates and user authorization context | Mandatory RBAC floor independent of optional policy gates |
| [core/flow_run_authorization.py](../core/flow_run_authorization.py) | Frozen authority and service ceilings on FlowRuns | Intersect those ceilings with the initiating principal's current permissions |
| [services/mcp_server_endpoint.py](../services/mcp_server_endpoint.py) | MCP transport surface | Map authenticated calls to the same operation contracts |
| [tasks/io/chat_ui/](../tasks/io/chat_ui/) | Action-driven UI | Consume server permission projections and per-resource decisions |

Reconcile this plan with:

- [RESOURCE_ACL_SHARING_PLAN.md](RESOURCE_ACL_SHARING_PLAN.md): stable resource IDs, ACL audiences, bindings, revision pinning, secret isolation, and shared-resource revocation.
- [ADMIN_CROSS_USER_SCOPES_PLAN.md](ADMIN_CROSS_USER_SCOPES_PLAN.md): explicit cross-user discovery and owner targeting.
- [AGENT_COLLABORATION_AND_TOOL_SAFETY_PLAN.md](AGENT_COLLABORATION_AND_TOOL_SAFETY_PLAN.md): execution authority and agent delegation.

The general resource ACL plan is still marked proposed. Existing conversation sharing is implemented. Do not assume the planned general ACL store is available.

There must be one PrincipalContext and one application authorization evaluator. The existing user-intent AuthorizationContext, opaque route capability tokens, and application permission catalog serve different purposes; their names must not obscure those differences.

The ACL plan's references to permission-bearing roles and administrator-only operations must be reconciled with this document during implementation. Its **membership groups** remain distinct from **permission groups**.

## 3. Permission names and resource scopes

### 3.1 Canonical shape

Resource permissions use:

```text
<domain>[.<resource_kind>].<storage_scope>[.<reach>].<operation>
```

Examples:

```text
resource.agent.user.own.read
resource.agent.user.own.update
resource.agent.user.shared.read
resource.agent.user.any.update
resource.agent.conversation.own.activate
resource.agent.conversation.shared.activate
resource.agent.global.accessible.read
resource.agent.global.any.update

flow.user.own.create
flow.user.own.start
flow.conversation.shared.stop

conversation.user.own.read
conversation.user.shared.message.send
conversation.user.any.delete

iam.instance.role.assign
iam.instance.permission_group.update
system.instance.update
```

Storage scope is always explicit. Reach distinguishes ownership or audience inside that scope. Operations may have multiple segments, such as `message.send`.

A conversation record is a user-owned root object, hence `conversation.user.*`. A resource definition stored inside a conversation has storage scope `conversation`. Domain adapters must declare these classifications; they must not infer scope from a client-supplied string.

For instance-wide operations with no resource owner, use `instance` and omit reach. Do not invent a fictitious user or conversation.

### 3.2 Scope and reach table

| Storage scope | Reach | Matching condition |
|---|---|---|
| `user` | `own` | The authenticated principal owns the target or destination container |
| `user` | `shared` | A current accepted ACL/relationship grants the requested operation on another user's target |
| `user` | `any` | Explicit cross-user authority for the operation, without an ownership/ACL audience requirement |
| `conversation` | `own` | The principal owns the conversation containing the resource |
| `conversation` | `shared` | Accepted conversation access and, where supported, the resource ACL permit the requested operation |
| `conversation` | `any` | Explicit authority for the operation across conversations |
| `global` | `accessible` | The global resource's audience policy permits the requested operation |
| `global` | `any` | Explicit authority to administer the global resource regardless of its audience |
| `instance` | omitted | An operation on the PawFlow instance, authenticated account system, or another declared ownerless target |

Only valid combinations are registered. For example, `global.own` is invalid.

For global resource types without ACL support, the adapter must explicitly describe the current audience behavior per operation. Do not silently treat every global mutation as accessible. Before introducing shared reach for a resource type, implement its relationship resolver; until then, that reach is unavailable for that type.

`any` broadens ownership/audience access. It does not bypass authentication, disabled accounts, token binding, resource existence/type checks, package activation, secret bindings, execution ceilings, or server/relay configuration constraints.

An administrator with `*` therefore has every application-level `any` capability. The evaluator never checks whether the role is named `admin`.

Cross-user discovery stays explicit in the UI/API. Holding a broad grant does not automatically merge other users' resources into the ordinary conversation resource cascade.

### 3.3 Resource identity and target resolution

Resource IDs, usernames, conversation IDs, file paths, and relay IDs are data, not permission-name segments.

Each adapter resolves a stable target containing:

- resource ID and kind;
- actual storage scope;
- owner and containing conversation, if applicable;
- resource/ACL revision or another mutation guard;
- relevant relay, service, path, or credential binding.

Resolve targets on the server, separately from the authenticated actor. Do not authorize using a display name alone, a body-provided owner, or whichever same-name resource happens to win a later lookup.

Creation authorizes the destination container. Copy/import authorize source read plus destination creation. Move/promotion authorize every affected source/destination and their applicable ACL changes. Sharing requires a sharing operation; write access alone does not imply resharing.

Bulk operations declare atomic versus per-item behavior. Every item is authorized before its side effect; a batch-level permission must not bypass target checks.

### 3.4 Domain-specific scopes

The repository scopes above are not the complete set of PawFlow native scopes. Memory/diary resources also use agent/private scopes; project graph/wiki and filesystem/desktop access can be tied to a relay or host surface.

Register these explicitly with domain adapters, for example:

- `memory.agent.own.read` and `memory.private.own.read`;
- `diary.agent.own.read`;
- `project.wiki.relay.accessible.read`;
- `filesystem.relay.accessible.write`;
- `filesystem.host.accessible.execute`;
- `filestore.user.own.read`;
- `scratchdir.agent.own.read`.

For these domains, the scope segment identifies the native data boundary or execution surface, and the adapter defines its trusted owner/audience predicate. Agent-private scope additionally checks the acting agent where the existing privacy contract requires it.

Do not flatten agent/private into global or infer host access from relay access. Register `any` variants only where the product supports deliberate administration, with the same attribution and execution constraints. The exact scope registry and supported combinations are WP0 deliverables.

## 4. Wildcards and union semantics

### 4.1 Grammar

Support only:

- exact permission names;
- the universal rule `*`;
- a terminal namespace wildcard `<namespace>.*`.

Use lowercase canonical ASCII identifiers separated by dots. Validate nonempty segments and bounded input size. Treat names as tokens, not regular expressions.

Reject `**`, partial-segment wildcards, intermediate wildcards, empty segments, percent/question-mark syntax, comma alternatives, and colon modifiers. An authoring UI can let an administrator select multiple rules without inventing a compact expression language.

`A` in the user's specification denotes a namespace; actual registered names use their canonical spelling.

| Rule | Checked permission | Match |
|---|---|---|
| `*` | Any registered permission | yes |
| `resource.agent.*` | `resource.agent.user.own.update` | yes |
| `resource.agent.user.*` | `resource.agent.user.any.delete` | yes |
| `resource.agent.user.own.*` | `resource.agent.user.any.update` | no |
| `a.*` | `a.b.c` | yes, if registered |
| `a.*` | `ab.c` or `a` | no |
| `a.b` | `a.b.c` | no |

Validate the requested operation against the catalog **before** wildcard matching. Even `*` cannot execute an unregistered operation or turn a typo into a valid permission.

Exact grant rules must reference known entries. Namespace rules must reference registered namespaces; `*` is always a valid rule. A missing optional extension leaves its existing rules dormant and visible as unavailable; it never redirects them to another extension. Malformed policy or broken role/group references fail closed.

Namespace wildcards include future descendants. This is intentional, especially for `*`. The editor must show current matches and clearly state that future entries under the namespace are included. Catalog registration is trusted administrative work.

### 4.2 Effective rights

For a valid, enabled principal:

```text
rules(user) = union(
    group.rules
    for each active role assigned to the user
    for each active permission group referenced by that role
)
```

For one resolved operation/target, the adapter emits valid candidate permission names, such as:

```text
resource.agent.user.own.update
resource.agent.user.any.update
```

The first candidate exists only when the owner condition is true. A shared candidate exists only when the current ACL permits that operation. The request passes RBAC if any applicable candidate matches any effective rule.

Evaluate candidates per target and per operation. Never union actions and resource predicates independently.

| Grants from separate roles | Effective result |
|---|---|
| Own-resource read + any-resource read | Any-resource read |
| Own-resource update + any-resource read | Own-resource update and any-resource read |
| Read access to X + update access to Y | Read X and update Y, without update X |
| No delete grant + a valid delete grant | Delete is permitted on that grant's targets |
| The same grant through two groups | One effective grant, with both provenance paths |

Permission union is commutative, associative, idempotent, and monotonic under grant addition. Deleting or disabling a role/group can revoke rights.

There are no negative group rules and no `AUTHORIZED` exception that cancels a denial. Mandatory execution constraints can still refuse an operation after RBAC succeeds; they are not competing role grants.

### 4.3 UI states

The browser receives a projection:

- hidden: the feature or target is not discoverable;
- disabled: discoverable, but the action is not allowed;
- enabled: the action is allowed on the current target.

Do not store `INVISIBLE`, `DISABLED`, or `AUTHORIZED` suffixes in permission rules. Discoverability and execution are separate declared operations where product behavior needs that distinction.

Read/update/delete/start/share remain independent permissions. Convenience groups can bundle them, but the evaluator adds no implicit CRUD hierarchy.


## 5. Catalog and exhaustive operation coverage

### 5.1 PermissionDefinition

The server owns the catalog. Each entry declares:

- canonical permission name and description;
- domain, resource kind, scope, reach, and operation;
- target resolver and supported target type;
- whether it is discovery, read, mutation, execution, credential use, or administration;
- UI label/description translation keys;
- owning built-in module or trusted extension identity;
- declaration version and any required independent permissions.

Catalog names are stable machine identifiers. Display labels can change without changing authority. Do not overload a menu key or frontend component path as the permission name.

Reusable templates can generate supported scope/reach combinations. Reject duplicate definitions with conflicting semantics. Do not automatically register every Cartesian combination of scope, reach, and operation.

### 5.2 EntryPointBinding

Maintain an explicit binding for every route, action, tool sub-action, processor side effect, background job, and callable extension operation.

Each binding declares:

- canonical operation ID;
- ingress aliases (HTTP/action name, slash command, tool plus sub-action);
- authentication class: authenticated, narrowly defined public bootstrap/auth route, or explicit internal service principal;
- normalized argument contract;
- all required target resolutions and permission checks;
- response filtering rules for lists, exports, events, and downloads;
- execution boundary and the owning authorization integration test.

Prefer registration beside the handler/processor declaration so an operation cannot be added without making an explicit authorization choice. Generate a machine-readable coverage report from the registry and compare it to registered ingress surfaces.

A tool such as `manage_resource` needs separate bindings for list/create/update/delete/share. Permission to invoke the container tool does not authorize all its sub-actions. Permission to call `use_tool`, `execute_script`, a flow wrapper, or a generic UI dispatcher does not erase the inner operation's contract.

Public login, OAuth callbacks, and narrowly scoped invitation/bootstrap endpoints are explicitly classified. They do not invent an anonymous principal and do not imply access to private APIs.

Internal maintenance uses a bounded service principal created by trusted server code. User input cannot set an `is_system` flag or select that principal.

### 5.3 Required inventory matrix

Phase 0 must turn this matrix into the exact live list of handlers/routes/commands and tests. No family is an optional future coverage gap.

| Family | Operations to enumerate | Target and boundary requirements |
|---|---|---|
| Accounts and identity | Own profile/password, sessions/API keys, identity linking, invitations, user create/enable/disable/delete | Distinguish self-service from IAM administration; token issuing cannot broaden identity |
| Permission administration | Catalog read, group/role CRUD, assignments, IdP mappings, effective-rights explanation | Delegable authority ceiling and last-administrator invariant |
| Conversations/messages | List/read/create/update/archive/delete/export, send/edit/delete messages, invite/accept/remove collaborators | Actual owner, accepted membership, message author where relevant, every reply/SSE channel |
| Agents | Discover/configure/activate/run/stop, attach/observe interactive sessions, delegate, publish endpoint | Resource access plus operation grant; captured and managed providers share the floor |
| Resources | Agent, skill, MCP, task definition, prompt, tool, hook, theme, private gateway skin | Read/create/update/delete/copy/move/import/export/share/bind/activate and revision acceptance |
| Services and relays | List/configure/create/remove/enable/start/stop/install/link/restart; tunnels/ports/host execution | Service owner, relay audience, explicit host/tunnel configuration and credential bindings |
| Flow templates/editor | Read/create/update/delete/import/export/copy/promote | Source and destination; editing executable definitions is distinct from execution |
| Flow instances/runs | Deploy/start/stop/pause/resume/update/read logs/status/queues; each task's side effects | Initiating principal plus accepted flow/service ceiling; recheck at task dispatch |
| Work scheduling | Todos, workflow proposals/reviews, assigned tasks, continuation, calendar/recurring scheduling, cancellation | Own work records; a schedule never upgrades the eventual job's rights |
| Cognition | Memory, knowledge graph, diary, project graph, wiki, scratchpad/scratchdir | Actual native scope, agent/user privacy, query/export filtering, build/index file access |
| Files/FileStore | List/stat/search/read/write/patch/copy/move/delete/upload/download/share/preview | Canonical paths, stable file ownership, symlinks, source/destination, signed-link audience |
| Tools and code execution | Built-in tools, scripts, notebooks, shell, dynamic tools, package host calls | Normalized concrete handler/sub-action; execution environment is an independent limit |
| Browser/desktop | Navigate/fetch/search, screenshot/observe, click/type/control, VNC/terminal/code-server | Bound relay/session/conversation and actual process/network authority |
| Media | Generate/edit/transcribe/synthesize/clone voice, read/download/publish results | Input/output access, provider/service use and credentials |
| Secrets/variables | Metadata listing, create/update/delete, bind/use, any supported reveal/export action | Secret value handling distinct from metadata and service use; no implicit publisher credentials |
| Packages/extensions | Inspect/install/update/uninstall/build/publish/dev load, UI/host callbacks | Trusted registration namespace and code-execution permissions; no self-grant |
| Monitoring/administration | Usage/logs/audit, fleet view, global parameters, image builds, updates, shutdown/restart | Explicit instance permissions; no role-label or substring checks |
| Protocols/channels | Web, direct API, SSE/WebSocket, MCP, PawCode/CLI, published agents, A2A, Telegram and other enabled channels | Same operation decision and authenticated principal mapping on every ingress |

Only register operations that the product actually provides. This list requires inventory; it does not request adding hypothetical functions such as a new secret-reveal endpoint.

Read-like requests can disclose data or trigger computation: graph builds, previews, search, status queries, attachment fetches, and exports need explicit contracts. Register cancellation separately from creation/execution; revocation cleanup by trusted server code must remain possible.

## 6. Persistence and identity model

### 6.1 Records

Use UUIDs for every new group, role, assignment, change event, and binding record. Include created_at/updated_at timestamps and revisions. Keep existing user identifiers and resource locations; this work does not rename user directories.

| Record | Fields and invariants |
|---|---|
| PermissionGroup | group_id, name, description, enabled, rules, revision, timestamps |
| Role | role_id, name, description, enabled, permission_group_ids, revision, timestamps |
| UserRoleAssignment | assignment_id, user_id, role_id, source, issuer/external mapping reference when applicable, enabled, revision, timestamps |
| TrustedIdentityMapping | mapping_id, issuer, external subject/group ID, target role IDs, enabled, revision, timestamps |
| Catalog revision | Version/digest of registered permission definitions and namespaces |
| PrincipalContext | User/service identity, auth source/session reference, current role/membership revision, acting agent/lineage, execution ceiling |
| AuthorizationDecision | Decision UUID/time, operation and target references, applicable permission, matched rule/group/role provenance, policy revisions, redacted reason |

Roles contain group IDs only. Permission groups contain rule strings only. Assignments are the authoritative user-to-role relation; a User.role_ids property may expose a derived view but must not create a second membership store.

Human membership groups used for ACL subjects/SSO are named and typed separately, for example `identity_group_id` versus `permission_group_id`. Joining a membership group only grants roles through an operator-approved mapping.

Examples below are schematic; production records require UUIDs and timestamps:

```json
{
  "permission_group": {
    "name": "Administrators",
    "rules": ["*"]
  },
  "role": {
    "name": "Administrator",
    "permission_group_ids": ["<administrators-group-uuid>"]
  },
  "user_assignment": {
    "user_id": "<existing-user-id>",
    "role_id": "<administrator-role-uuid>"
  }
}
```

No special-case `if role.name == "admin"` exists in the final engine.

### 6.2 One authoritative security snapshot

Extend the current SecurityManager user-store persistence through a versioned security-state owner. Keep users (including enabled state), groups, roles, assignments, trusted mappings, and authorization revisions in one coherent committed snapshot under the existing protected system storage.

The proposed first implementation is a versioned envelope at the existing `core.paths.USERS_FILE` location. Preserve the existing protection of credentials; never return password hashes through permission APIs. PermissionStore is an API over this owner, not another independently loaded authority file.

Use an atomic temporary-write/flush/replace transaction for the full snapshot, one mutation owner, expected-revision checks, and publication of an immutable read snapshot only after durable commit. Reuse existing atomic persistence utilities where suitable. Implement proper recovery for the selected filesystem; a threading lock alone is not a multi-process transaction.

Every server worker must read the authoritative revision or call the state owner. Remote executors never keep their own writable copy. Any multi-process deployment needs owner IPC/invalidation in the implementation, not a promise that per-process caches eventually converge.

Separate a storage revision from an authorization revision: a last-login timestamp update need not invalidate all permissions; changing an enabled flag, membership, group, role, or mapping must.

This coherent transaction is required to protect the last enabled administrator across user disable/delete, role/group changes, and assignments. A sequence of independent writes to users.json and a separate IAM file is insufficient.

Persist the redacted IAM change event with the security-state transaction or a durable outbox before acknowledging it. Stream the audit event through the existing audit infrastructure asynchronously. Never include credential values or password hashes.

Corruption, an unavailable store, or a broken reference denies authorization and raises an operational error. Do not interpret a broken store as a new empty installation and silently bootstrap an administrator.

### 6.3 Administration and privilege assignment

Declare separate instance operations for reading/managing groups, reading/managing roles, assigning roles, editing mappings, inspecting another user's rights, and reading authorization audit records.

IAM permission alone is not permission to grant arbitrary authority. A limited administrator also needs a server-owned delegation ceiling covering the proposed assignment or group/role expansion.

Compare wildcard coverage symbolically. Having every currently registered child as exact grants is not sufficient to assign `namespace.*`, which includes future children. A holder of `*` can administer all grants.

Check edits transitively: editing a group affects every referencing role and assigned user. Renaming does not change identity; disabling/deleting cannot leave dangling references or silently restore previous authority. Referenced deletes require an explicit authorized replacement/unassignment transaction or fail with a conflict.

Protect at least one enabled, locally recoverable principal with an effective universal `*` grant. Count effective authority, not a role label or the number of role records. Recheck this invariant inside the same transaction for all paths, including SSO changes.

Existing ownership-based resource sharing remains a separate resource permission. It cannot assign instance roles or silently make someone an administrator.

### 6.4 Authentication, SSO, and sessions

Preserve authentication providers and their identity verification. Replace highest-role selection with trusted role sets.

Preserve explicit local/remote source-selection behavior during migration. Define and expose `local`, `remote`, and an explicit opt-in `union` policy for accepting local assignments versus mapped assignments. Once the effective assigned set is selected, **all** roles in that set combine by union without ranking.

Migrate existing local/remote precedence and its documented no-mapping behavior explicitly; do not silently change an operator's source-of-authority choice. New mappings target exact role UUIDs, never arbitrary raw claim strings. Unmapped groups grant nothing; issuer namespaces prevent cross-provider collisions.

Use the current claim parser and operator mapping flow, rather than introducing a second SSO resolver. Keep assignment provenance so removal of a mapped assignment does not accidentally remove a separately granted local assignment.

Sessions and API/CLI tokens carry identity and bounded authentication/delegation information. Cached roles are not authoritative for the lifetime of a token. Resolve current authorization state for protected operations.

Document external IdP freshness honestly: external removal takes effect when claims refresh or a supported event is received. Local disable/revoke operations take effect on the next authorization boundary without waiting for the IdP.

No selectedAgent fallback is introduced. Loss of access marks the selected agent unavailable and blocks execution while preserving its identity.


## 7. Central authorization pipeline

### 7.1 Proposed implementation units

These are proposed modules, not existing implemented APIs:

| Unit | Responsibility |
|---|---|
| `core/permission_catalog.py` | Permission definitions, namespace validation, ingress bindings, catalog digest |
| `core/permission_rules.py` | Exact/terminal-wildcard parser and deterministic matcher; symbolic rule coverage |
| `core/permission_store.py` | Group/role/assignment operations over the authoritative SecurityManager snapshot |
| `core/principal.py` | Immutable authenticated PrincipalContext and explicit bounded service principal |
| `core/access_control.py` | Decision/require/explain APIs, current revision lookup, provenance |
| Domain adapters beside existing stores/services | Trusted target resolution and scope/relationship predicates |
| `tasks/ai/actions/permissions.py` | IAM and permission-projection UI actions |
| `tasks/io/chat_ui/permissions.js` | Shared UI permission consumer and administration interface |

Keep authorization predicates separate from handlers' business effects. Extend existing types/modules when they already own the contract; avoid a duplicate principal or role store merely to preserve these proposed filenames.

A conceptual server API is:

```python
target = resource_store.resolve_target(resource_id)
decision = access_control.require(
    principal=principal,
    operation="resource.agent.update",
    target=target,
)
resource_store.update_authorized(target, changes, decision=decision)
```

The operation is a registered semantic operation. The evaluator derives candidate scoped permission names from the trusted target; it does not accept a caller-chosen `own` or `any` label.

### 7.2 Decision order

1. Authenticate and validate account/session/service-principal state.
2. Resolve the final registered operation, including normalized aliases and wrapper sub-actions.
3. Validate argument shape and resolve every target using read-only resolvers.
4. Load a consistent current authorization snapshot.
5. Derive applicable permission names for each operation/target pair.
6. Match the user's effective role/group rules, retaining provenance.
7. Apply token bounds, parent delegation ceilings, accepted resource revisions, secret/service bindings, relay limits, and other mandatory execution constraints.
8. Run existing user-intent/tool safety policy gates if the operation requires them. Their allow/ask result cannot elevate a failed RBAC decision.
9. Admit dispatch against the checked target/revisions, then perform the effect.
10. Record a redacted decision and emit only data the principal can read.

Missing principal or required target information never selects an anonymous/default owner. Invalid client parameters are validation errors; lack of authority is a denial.

Preserve HTTP behavior appropriate to each surface: unauthenticated requests require authentication; known accessible-but-forbidden operations can return 403; unknown or inaccessible private resources use indistinguishable not-found responses. Do not leak target existence through explanations, counts, or error wording.

Any prepared decision/dispatch ticket is server-owned, bound to principal, operation, normalized arguments, targets, and relevant revisions. A caller cannot reuse it for a different target or mutation. Recheck stale tickets.

### 7.3 Ingress and core integration

Apply checks at both the declared ingress and the shared side-effect boundary. The ingress provides consistent errors and filtering; the core boundary catches direct internal/API/MCP/processor paths.

For `AgentActionsMixin`, cover early returns, direct inline dispatch, background dispatch, extension-first routing, slash-command redispatch, reply-conversation IDs, and action status APIs.

For ToolRegistry, authorize the final normalized/prepared call immediately before handler execution. Preparation hooks must be read-only. Resolve the actual inner tool and action through `use_tool` and equivalent wrappers. Aliases cannot change the selected policy.

For stores and resolvers, propagate requester and storage owner separately. ScopedRepository/ResourceStore/ServiceRegistry callers cannot supply another owner without the relevant `any` operation. Reuse conversation_access to resolve existing accepted relationships, including ownership changes; do not copy its ACL logic into each handler.

For externally addressed resources, adopt the stable IDs and revision/locator requirements in the resource ACL plan. Until a domain supports those requirements, restrict its shared reach rather than authorizing ambiguous names.

### 7.4 Lists, streams, downloads, and compound operations

- Filter authorized resources before pagination and aggregate counts. Use stable continuation semantics so filtering does not expose hidden IDs or counts.
- Use metadata-only target lookup for authorization; do not load secret values or return content before the decision.
- Check every result destination/reply bus, not only the operation's main conversation.
- Authorize SSE/WebSocket subscription and re-evaluate on relevant policy/membership changes. Close or narrow a subscription when access is revoked; cached replay must be filtered too.
- Downloads/previews/source archives/signed URLs require an explicit audience and read contract. A signed token grants only its bound resource/use, subject to its declared revocation model.
- For copying, importing, moving, publishing, and exporting, authorize each participating source/destination and data category.
- Sharing permission cannot grant a recipient arbitrary global actions. Recipient role capability and target ACL must both permit ordinary shared access.
- `any` is an explicit administration path and must be attributable in audit. It does not create an activation binding or select an agent implicitly.

## 8. Agents, automation, and execution surfaces

### 8.1 Principal propagation

Use the initiating principal from trusted ingress for user-originated work. Record its identity on queued jobs, FlowRuns, tool calls, and delegated work. Do not substitute the conversation owner's or publisher's authority for a collaborator's authority.

An automation intentionally running as an owner/service account must have a separately configured, bounded execution identity. Authorize both its definition and triggering contract; merely knowing an endpoint or flow ID cannot invoke a more privileged identity.

Propagate the current authenticated identity and execution lineage through HTTP workers, queues, scheduled wakeups, agent loops, native/managed CLI bridges, MCP endpoints, A2A, package host calls, and remote relay dispatch.

An external A2A principal is mapped through configured trust and an explicit local identity. A remote message cannot choose local role IDs or a system principal.

### 8.2 Attenuation

For delegated execution:

```text
allowed call =
    current principal permission on the concrete target
    AND accepted parent/run authority
    AND child execution ceiling
    AND resource/service/transport constraints
```

This intersection of execution limits does not change the union between the user's roles.

Freeze the delegated/run ceiling to the accepted catalog operations, targets, and service revisions. A later catalog addition matched by the user's dynamic wildcard does not automatically enlarge an already accepted task's ceiling. A new or explicitly reauthorized run can receive the new capability.

Agents cannot edit their role/group assignments through prompts, tool arguments, imported skill text, or resource configuration. Tool discovery may hide unavailable tools, but every attempted invocation is still checked.

Capture/attach paths for interactive providers must enforce the same identities and permissions as ordinary chat turns. Attachments, screenshots, terminal input, and background recaps are not authorization bypasses.

### 8.3 Processor and workflow effects

Authorize each task's declared effects and actual target at execution, including task-to-task calls that do not pass through an LLM tool.

A generic executeScript processor or shell task is an execution capability whose OS/process access must be bounded. It cannot masquerade as a collection of narrowly authorized application operations.

Existing FlowRun authority/service snapshots remain ceilings. Before each new task dispatch, resolve current user permissions and verify the relevant snapshot/bindings. Pending work that loses access becomes explicitly blocked/cancelled according to its existing lifecycle; it does not silently run as another user.

### 8.4 Filesystem, host, browser, and desktop limits

Application permission checks alone cannot restrict arbitrary shell code that can access the same files and credentials as the server.

Specify permissions for observing versus controlling desktops, relay-container execution versus host execution, filesystem reads/writes/deletes, outbound service use, and tunnel creation. Then enforce the corresponding constraints in the execution environment:

- only authorized filesystem mounts and canonical path roots;
- no exposed server IAM/credential storage;
- no privileged Docker/host socket except when explicitly authorized and configured;
- least necessary bound credentials;
- bounded relay/session identity and approved host-helper configuration;
- protected symlink/alias resolution and source/destination checks.

`*` grants application operations but does not override a relay configured with host access disabled. Changing that configuration is itself an explicit permitted administrative operation.

Browser/desktop interaction may access capabilities available in that OS/browser session. Sharing a powerful desktop with a restricted principal is therefore a sharing decision about the session's real authority, not proof that button filtering provides isolation.

Document the supported enforcement boundary per executor. If an executor cannot provide the required isolation for a restricted grant, reject that configuration rather than advertising unsupported fine-grained enforcement.

### 8.5 Packages, MCPs, and dynamic tools

Register extension operations under stable, owner-bound namespaces through trusted installation. A package signature establishes package provenance; it does not assign user roles.

The host validates the extension's permission declarations and required host effects. Extensions cannot register themselves into another namespace, declare arbitrary host operations public/internal, or update IAM state through resource payloads.

Check both invocation permission and each host effect. Calling an MCP method requires permission for its declared operation plus the credential-bound service access. If an external service cannot enforce narrower semantics, treat that integration as a broader execution capability and expose that fact in its declaration.

Installing/upgrading a package changes catalog_revision and invalidates derived permission caches. Exact removed entries become unavailable; existing wildcard rules automatically match new registered descendants, as agreed.

## 9. Revocation, consistency, and audit

### 9.1 Revision model

Invalidate derived rights on user disable/delete, assignment change, group/role content or enabled-state changes, mapping refresh, catalog changes, and resource ACL/ownership/binding changes.

Cache keys include principal identity, authorization/membership revision, catalog revision, execution ceiling, and relevant target/ACL revisions. Do not use only user ID, role name, or conversation ID. Do not cache a privileged view under the same key as an ordinary view.

Use immutable snapshots and indexes for inexpensive checks. Parse rule sets once per revision, not once per control render or tool call. Database/network/file I/O and large list work run through the existing async/worker infrastructure.

### 9.2 Revocation boundary

The authorization admission point defines ordering:

- after a revocation commits, no new dispatch may be admitted using stale authority;
- queued operations admitted only later must recheck;
- an operation already admitted before revocation may have begun or completed an irreversible effect;
- revocation cannot retroactively undo such an effect.

Where target mutation and authority must be linearized, use a bounded authorization admission/target revision guard. Do not hold global policy locks across slow network calls.

For long-running shell/desktop/tunnel sessions, revoke capabilities and terminate/disconnect the owned tracked process/session through existing cancellation mechanisms when their contract requires it. Never kill an unrelated replacement session or a broad process-name match. For a multi-step workflow, the next effect requires a fresh check.

Changes propagate to all server workers and connected clients; disconnected/restarted workers validate the current revision before resuming. No stale-permission grace period silently permits mutations.

### 9.3 Explainability

A decision explanation should identify:

- operation and safely disclosable target scope;
- matching permission/rule;
- source role and permission group;
- whether own/shared/any reach applied;
- current revisions and redacted denial reason.

Users can inspect their own rights. Inspecting other users or IAM policy details requires the corresponding permission. Do not disclose private resource existence, credentials, sensitive arguments, or hidden group membership through an explanation.

Audit IAM changes, cross-user operations, denied mutations/execution, capability issuance/revocation, and consequential execution admissions. Include UUID/time, actor, target reference, matched authority, correlation/lineage ID, and policy revision. Read-event volume can use the existing audit retention policy, but privileged changes must remain attributable.

## 10. Frontend and API contract

Expose operations through the existing action API conventions:

- permission catalog and safe namespace descriptions;
- own effective rules/capabilities with revision;
- per-resource or batched action decisions;
- permission-group/role/assignment CRUD with expected revision;
- authorized explanation and IAM audit lookup.

Do not return an unfiltered instance-wide policy graph to every authenticated browser. Batch per-resource queries with input limits and normal target visibility checks.

Use a shared UI permission service. The server sends normalized effective data and resource decisions; local matching is a convenience projection, never an enforcement layer. Invalidate it on authentication/permission changes, logout, reconnect, and server revision mismatch.

Administration UI:

1. Permission catalog tree with scopes and explanations.
2. Group editor with exact rules and dynamic wildcard preview.
3. Role editor selecting permission groups.
4. User editor assigning multiple roles and showing source provenance.
5. Effective-rights inspector showing why an action is enabled or denied.
6. Impact preview for group/role changes and cross-user scope grants.

Show that `*` and namespace wildcards include future entries. Show that `user.any` crosses user ownership. Preserve accessibility and translated labels.

Menus derive visibility from discoverable functions. Buttons and editable controls depend on the operation and actual selected resource. A batch selection checks every target. On permission changes, previously disabled controls can become enabled and previously enabled controls must become disabled; do not retain stale form state from a one-way disable helper.

A server denial remains authoritative even if the browser was modified or stale. Handle it as a normal permission change, with a safe refresh of the affected view.


### 10.1 Settings navigation and screen ownership

Extend the existing administration area, with an **Access** entry containing Users, Roles, Permission groups, and Onboarding/identity mappings. Visibility and editability of each tab use its own IAM permissions; access to Users does not imply access to role or mapping administration.

Reuse [admin_settings.js](../tasks/io/chat_ui/admin_settings.js) and its existing action flow instead of creating a disconnected administration application. Replace the singular role controls and the `window._userRole` projection in [resources_render.js](../tasks/io/chat_ui/resources_render.js). Shared permission helpers may live in the proposed permissions.js module.

For each screen, provide loading, empty, unavailable, denied, validation-error, and stale-revision states. Keep the edited form after a recoverable conflict and reload authoritative data before retrying. Never optimistically present an IAM mutation as saved before the server commits it.

### 10.2 Permission-group editor

The list shows name, description, enabled state, rule count, and referencing roles. Provide search, create, open/edit, and disable/delete actions according to permissions.

The editor contains:

- editable name/description and enabled state;
- a searchable permission tree organized by domain, native scope, reach, and operation;
- explicit rules plus namespace wildcard selection;
- a preview of current effective matches and the statement that wildcards include future entries;
- read-only indicators for unavailable extension entries;
- affected roles and affected-user count, only where the operator may inspect them;
- a before/after rights preview before saving.

Selecting a tree branch adds its actual wildcard rule only when the operator chooses “include future permissions”; selecting current children stores their exact entries. These are distinct authoring actions with distinct resulting policies.

Removing a group used by a role requires an explicit authorized replacement/removal transaction. The UI explains the dependency and does not silently detach it. A group named “Administrators” is not special; a rule `*` is what grants universal authority.

### 10.3 Role editor

The list shows name, description, enabled state, selected groups, and assigned-user count. The form uses a multi-select of permission groups and displays the read-only union of their rules, with provenance.

Role forms do not edit individual permissions or assign a numeric rank. To change a bundle, navigate to its group with the appropriate permission. A disabled group remains identifiable in the role but contributes no grants.

Before changing group associations, show additions/removals to the role's effective rights and affected assignments. The server rechecks delegable authority, expected revisions, and last-administrator protection even if the preview was allowed.

### 10.4 User list and role assignment

Extend the existing user management screen with:

- account enabled/access status;
- multiple role badges;
- source labels for local, invitation, and SSO-derived assignments;
- effective-rights inspection;
- edit profile, local role assignments, disable, revoke sessions, and delete as independently permitted actions.

The role picker only offers roles the operator can assign. Existing assignments outside that ceiling are shown safely as noneditable; omitting them from a submitted form must not remove them.

Edit local assignments separately from managed SSO assignments. A mapped assignment is accompanied by its provider/mapping source and the appropriate mapping-management action; a local checkbox must not pretend it can permanently remove a role that the next SSO refresh will restore.

Saving an assignment updates the full authorized change set atomically and emits permission invalidation. Display a last-administrator conflict clearly. Provide before/after effective rights without exposing other users' private resources.

There are no direct user-to-permission-group or user-to-permission assignments. The UI follows User -> Roles -> Permission groups.

### 10.5 Manual creation and first administrator

Replace `admin_user_create`'s singular role selector with an explicit multi-role selection, retaining the existing account/profile/enabled controls. The create request carries role IDs, not a role display label. Remove the implicit `role="user"` fallback after migration.

The server checks user creation authority and assignment authority for every selected role, then commits the user and assignments together. Do not create an enabled account with temporary broad defaults and narrow it in a later request.

An operator may explicitly create a zero-role account as “No application access yet”; this is a deliberate empty selection, not a missing-field fallback. Its authenticated landing page only explains the access state and provides allowed account-authentication lifecycle actions. It exposes no conversations, resources, or IAM catalog.

Fresh-install setup creates the first local administrator through the protected bootstrap path with a role/group containing `*`. Normal onboarding cannot invoke that path. A previously initialized but damaged user store must never expose the first-admin wizard.

### 10.6 Invitations, linking, and OAuth onboarding

Extend the existing onboarding token management in [admin_settings.py](../tasks/ai/actions/admin_settings.py), [oauth_invite_tokens.py](../core/oauth_invite_tokens.py), and [auth_gateway_service.py](../services/auth_gateway_service.py).

The invitation form distinguishes **Create a new account** from **Link an identity to an existing account**. It shows the selected initial roles for new-account invitations, issuer/provider restrictions supported by the admission policy, lifetime, and current invitation status.

Store the selected role IDs, their reviewed authorization revisions/digest, issuer actor, assignment source, expiry, and token hash. The raw token remains visible only at issuance. Lists show safe metadata and permit revocation.

At redemption:

1. Validate the provider identity, token audience, expiry, and one-use state.
2. Revalidate that the issuer is still authorized to grant the selected authority, and that roles/groups are available at the reviewed revisions.
3. If the invitation's reviewed rights have changed, require an authorized reissue instead of silently broadening access.
4. Atomically commit account creation and role assignments with invitation consumption, using the security-state transaction or its durable transaction journal.
5. Resume the correct application landing page only after the committed assignments are available.

Concurrent redemption and process failure must not create duplicate accounts, burn a valid token before a failed provisioning transaction, or leave an account with partially assigned rights.

For identity linking, keep the existing account's assignments. Linking a Google/GitHub/etc. identity is not itself a new local role grant. Any mapped-role change follows the explicit provider source policy from section 6.4. Self-service linking in [account_linking.py](../tasks/ai/actions/account_linking.py) must not retain its hardcoded single-role assumption or accept role IDs from the client.

Migrate pending legacy invitations explicitly: map their single role to the reviewed new role reference and capture its revision, or invalidate and reissue when the old grant cannot be mapped safely. Preserve expiry/one-use guarantees. Do not keep the old invitation-role parser as a second authority path.

### 10.7 New-user policy and SSO administration

The Onboarding tab manages separate policies for manual creation, invitations, and each configured identity provider. It does not create a universal hidden default role.

Provide:

- an explicit set of initial role IDs for each supported admission policy;
- a clear distinction between a form preselection and authority the server actually grants;
- issuer-qualified external-group-to-role mappings with multiple target roles;
- provider auto-provisioning enablement and its admission conditions;
- local/remote/explicit-union source selection;
- a safe test/preview using validated claims or operator-entered sample claims, without creating an account or granting access.

Unmapped identities remain in the existing pending/onboarding-denied flow unless an explicit admission policy accepts them. A public login form never lets the new user choose privileged roles. Imported claims, client payloads, email display strings, or a group named “admin” do not establish an assignment.

Changing initial-role policy affects future admissions only. Updating existing users is a separate explicit, previewed assignment operation. If a configured role/group is removed or disabled, the onboarding policy becomes invalid and its admission path fails closed until corrected; it does not substitute another role.

Persist account, assignment, and invitation lifecycle changes coherently. Extend the migration/recovery protocol for the current separate invitation and identity-link stores so a restart cannot replay a consumed invitation into extra authority.


## 11. One-shot migration and rollout

### 11.1 Migration inputs and mapping

Inventory current users, stored roles, trusted IdP mappings, onboarding/initial-role policies, pending creation/linking invitations, sessions/tokens, administrative checks, conversation relationships, active jobs, and all actual operation gates. Do not infer the regular user's current access from ROLE_PERMISSIONS alone: many current actions enforce ownership or inline checks elsewhere.

Produce an explicit old-behavior-to-new-permission matrix:

- existing administrators receive a role referencing the universal `*` group;
- existing regular users receive an explicit reviewed set of capabilities matching their supported access;
- scope/reach restrictions remain attached to each migrated operation;
- existing collaborators retain their accepted relationship and read/write limit;
- no user receives a new cross-user or execution grant merely because a mapper could not classify an old check.

The new universal administrator policy intentionally grants all registered application permissions. Record any expansion compared with previous scattered admin checks; this is an explicit product decision, not an unnoticed migration side effect.

Give migrated groups, roles, and assignments UUIDs and timestamps. Preserve user IDs, credentials, resource IDs where already present, data locations, and conversation membership. Do not copy private user data or alter Chromium profiles.

Onboarding uses configured role IDs. A missing/unknown required role/group reference is an error, never an implicit `user`, `admin`, or empty-principal fallback.

### 11.2 Atomic cutover

1. Build a dry-run report containing counts, names safe for the operator, mappings, unresolved references, and access differences; never print credentials.
2. Verify coverage and migration tests on a representative protected snapshot.
3. Put authorization mutations and new dispatch admission into the controlled migration barrier. Drain or checkpoint in-flight work according to existing lifecycle contracts.
4. Back up the security/assignment configuration to protected storage with a manifest and checksums.
5. Generate and validate the complete versioned security snapshot, including a recoverable universal administrator.
6. Commit it atomically; publish the new revision only after successful persistence.
7. Invalidate/reissue old authorization-bearing sessions and internal capability grants as required by the new contracts.
8. Rehydrate queued work from its real initiating identity and a validated execution ceiling; block unresolved historical jobs for explicit reassignment.
9. Start the new enforcement path on every ingress and worker.
10. Verify administrator login, regular-user behavior, shared access, direct API denial, and queued-work revocation before reopening normal admission.

The migration is idempotent by schema version and transaction marker. Restart during any cutover step resumes or restores a complete state, never a mixture.

After cutover, remove Role enum authority, static ROLE_PERMISSIONS, role ranking, substring role gates, and legacy serialized single-role authorization. Do not leave a runtime fallback where a missing permission definition uses the old admin check.

Dual evaluation is permitted in offline tests or a diagnostic rehearsal with no enforcement claim. A production deployment with uncovered operations is not complete.

### 11.3 Recovery

Before admitting writes under the new schema, a failed cutover can restore its protected preimage and matching application version.

After new-schema writes are accepted, do not downgrade one file or one worker in isolation. Use a complete coordinated restore with documented data implications, or a forward fix through the normal release procedure. Retain sufficient audit/migration evidence to determine the authoritative revision.

A permission-store read error must never unlock setup or create a new administrator. Recovery/bootstrap remains a separate authenticated or operator-controlled local procedure, outside ordinary user tools.

## 12. Implementation work packages

All checkboxes are implementation work remaining after this planning document. Dependencies are explicit; module boundaries may be consolidated when existing code already owns a responsibility.

### WP0 — Inventory and reconciliation

Dependencies: none.

- [ ] Enumerate every live ingress/action/tool sub-action/task effect/extension callback.
- [ ] Map aliases and wrappers to canonical operations and target resolvers.
- [ ] Classify public authentication/bootstrap and bounded internal operations explicitly.
- [ ] Produce the current-access matrix for regular users, administrators, collaborators, and service identities.
- [ ] Register each domain's real scopes, including agent/private and relay/host surfaces.
- [ ] Reconcile this plan with both ACL/cross-user plans and existing user-intent/capability contracts.
- [ ] Record unsupported executor isolation boundaries and required fixes.

Deliverables: reviewed catalog specification, ingress coverage manifest, migration access matrix, source integration map.

Exit criterion: every discovered surface has an owner and a declared authorization contract; unresolved mappings prevent cutover.

### WP1 — Parser, catalog, principal, and pure evaluator

Dependencies: WP0.

- [ ] Implement exact and terminal-wildcard grammar with known-operation validation.
- [ ] Implement dynamic catalog revisions and trusted namespace ownership.
- [ ] Define the shared immutable PrincipalContext and typed target contract.
- [ ] Implement role/group union, per-target candidate matching, and provenance.
- [ ] Implement symbolic rule coverage for delegation/IAM changes.
- [ ] Add the scope adapters for owned/global/instance resources and existing conversation sharing.
- [ ] Reject unsupported scope/reach combinations instead of broadening them.

Deliverables: pure evaluator and unit/property tests; no unguarded production enablement.

Exit criterion: wildcard boundary tests and action/scope union tests pass; ordering and duplicate rules cannot alter a decision.

### WP2 — Security-state persistence, IAM, and migration tooling

Dependencies: WP1.

- [ ] Extend the authoritative security snapshot with groups, roles, assignments, mappings, and revisions.
- [ ] Implement atomic expected-revision mutations and worker-consistent snapshot publication.
- [ ] Implement IAM permission checks plus delegable-authority ceilings.
- [ ] Protect the final locally recoverable universal administrator in every mutation path.
- [ ] Implement dry-run, backup, idempotent migration, restart recovery, and schema validation.
- [ ] Replace single-role source selection with trusted role sets in the authentication flow.
- [ ] Preserve exact issuer/group mapping and documented IdP freshness.

Deliverables: coherent policy storage and migration command, seeded administrator/member definitions, protected administrative API.

Exit criterion: concurrent privilege changes and interrupted migrations preserve a complete valid authority state; unauthorized IAM changes cannot elevate privileges.

### WP3 — Core targets, ACL integration, and server entry points

Dependencies: WP1–WP2.

- [ ] Add required stable resource locators/revisions from the ACL plan where shared resource references need them.
- [ ] Adapt repository/resources/services/flows/conversations/cognition/files to the shared evaluator.
- [ ] Enforce direct and background UI actions, command redispatch, early returns, and result destinations.
- [ ] Enforce web/API/MCP/CLI/channel ingress and tool sub-actions/wrappers.
- [ ] Enforce exports, lists, pagination, replay, streaming subscriptions, previews, and downloads.
- [ ] Check every source/destination in bulk/copy/import/move/publish operations.
- [ ] Replace all role-name/substring authority checks before arbitrary role IDs/names are active.

Deliverables: registered operation contracts and domain-level denial tests.

Exit criterion: the same operation/target/principal has the same decision through all enabled ingress paths; no bypass through direct core calls.

### WP4 — Agents, workflows, relays, and revocation

Dependencies: WP3.

- [ ] Propagate initiating principals through queues, FlowRuns, agent turns, captured/managed sessions, and remote calls.
- [ ] Intersect current rights with frozen task/delegation/service ceilings.
- [ ] Enforce processor side effects outside LLM tool execution.
- [ ] Check capability mint/use and implement permission-change invalidation.
- [ ] Implement host/container/desktop/script access limits and fail closed for unsupported restricted configurations.
- [ ] Recheck new dispatches and close/restrict long-lived streams/sessions after revocation.
- [ ] Preserve exact-session cancellation, force-stop behavior, and nonempty selectedAgent.
- [ ] Make package/MCP/dynamic-tool registration and host effects participate in the same catalog.

Deliverables: bounded runtime execution and revocation integration tests.

Exit criterion: changing rights during queued/running work cannot authorize a new effect from stale authority; no child acquires more authority than its accepted parent ceiling.

### WP5 — UI and administration experience

Dependencies: WP2–WP4 APIs.

- [ ] Implement the common frontend permission projection with revision invalidation.
- [ ] Replace role-based menu/form checks with feature and per-target action decisions.
- [ ] Add the permission-group tree editor, role-to-group editor, user multi-role assignment editor, and effective-rights views from sections 10.1–10.4.
- [ ] Implement manual new-user creation, explicit zero-role state, first-admin bootstrap, invitation management/redemption, identity linking, and SSO onboarding policy screens from sections 10.5–10.7.
- [ ] Migrate existing singular role form fields and pending invitations; validate account/assignment/invitation atomicity.
- [ ] Distinguish locally editable role assignments from provider-managed assignments in every user view.
- [ ] Show wildcard future coverage, scopes/reach, and provenance.
- [ ] Add conflict handling, safe denial refresh, accessibility, and translations.
- [ ] Ensure denied/hidden data is never embedded in page payloads merely to hide it with CSS.

Deliverables: administration UI, user-facing permission behavior, browser tests.

Exit criterion: role changes update controls correctly; a modified/stale client cannot bypass server decisions.

### WP6 — Cutover, cleanup, and release validation

Dependencies: WP0–WP5.

- [ ] Run the coverage manifest gate and the full integration matrix.
- [ ] Rehearse migration and restore on representative multi-user/shared/SSO/job data.
- [ ] Remove legacy authority paths and transitional comparison code.
- [ ] Update authentication, resource, tool, API, relay, administration, and migration documentation.
- [ ] Run focused tests and the repository's full validation gates.
- [ ] Obtain review of security boundaries, migration behavior, and executor isolation.
- [ ] Execute migration with the declared barrier and post-cutover checks.
- [ ] Publish through the repository's release procedure with implementation and metadata commits separated.

Deliverables: complete enforcement, migration evidence, updated documentation, release evidence.

Exit criterion: all Definition of Done items below are met. A frontend-only or partly covered rollout cannot be declared complete.

## 13. Acceptance test matrix

Use behavioral tests at real boundaries in addition to unit tests. A source grep for old patterns is useful cleanup evidence but is not the authorization proof.

| Area | Required cases |
|---|---|
| Parser | Exact match; `a.*` matches `a.b` and `a.b.c`; does not match `a` or `ab.c`; reject invalid segments/modifiers/partial/intermediate wildcards |
| Universal grant | Role through group containing `*` authorizes every registered operation; a non-admin-named role with `*` behaves identically; an admin-named role without grants has no special power |
| Dynamic catalog | New descendant becomes authorized by an existing wildcard after registration; unknown operation denied even to `*`; conflicting namespace registration rejected; removed entries unavailable |
| Algebra | Order independence, associativity, duplicate idempotence, monotonic grant addition, effective revocation on removal |
| Scopes | Own/shared/any/global/instance/domain-specific distinctions; spoofed body owner/scope ignored; own update + any read never becomes any update |
| ACLs | Pending invitation denied; accepted reader cannot write; accepted writer plus needed role can write; revoked sharing denied; general shared resource use needs current binding/revision where required |
| Creation and movement | Parent/destination authorization; source plus destination checks; no promotion to global with only own-user create; name collisions do not redirect access |
| Multiple targets | Every selected resource checked; mixed-owner batches; no partial mutation before validation for declared atomic operations |
| IAM escalation | Role creation/assignment and group edits cannot exceed delegable authority; exact present-day grants cannot delegate a future wildcard; transitive group edits checked |
| Administrator safety | Rename irrelevant; final `*` holder protected across role/group/assignment/user/SSO changes; simultaneous demotions cannot remove the last administrator |
| Persistence | Expected-revision conflicts; atomic publication; malformed/broken state denial; crash recovery; no automatic bootstrap on corrupt/empty-looking state |
| SSO | Multiple mapped roles; unmapped groups grant nothing; issuer separation; local/remote/explicit union policy; independent local/mapped assignments; no role-substring elevation |
| Sessions/tokens | Disabled account and revoked role denied with an old token; wrong user/session/resource/conversation capability denied; bounded API key cannot use the owner's entire authority |
| Ingress parity | Equivalent web/direct API/CLI/MCP/channel calls and wrapper aliases produce identical decisions; direct handler/store paths cannot bypass them |
| Async dispatch | Revoke between queue and execution; between preparation and dispatch; between flow tasks; on worker restart/reconnect; role addition does not enlarge a frozen delegated ceiling |
| Agent lineage | Collaborator not replaced by owner; child cannot elevate; remote agent cannot forge local principal; selectedAgent stays identified but unavailable after revocation |
| Tool policies | RBAC denial cannot become allow via policy gate, confirmation, shared resource, or `use_tool`; arguments cannot select their own required permissions |
| Executor limits | No server IAM/credentials mounted to restricted jobs; denied host/tunnel access; canonical path/symlink checks; shell cannot be represented as safe fine-grained access without enforced isolation |
| Secrets | Metadata/read/use/manage distinctions; no implicit publisher or unrelated consumer secret; secret values omitted from permission projection, audit, and denial |
| Lists and events | Filter before count/pagination; unauthorized target indistinguishable from absent; replay/stream/attachment/download filtering; subscription revoked mid-session |
| Extensions | Plugin cannot self-grant, take another namespace, declare privileged host effects public, or bypass host checks through callback/sub-action |
| UI | Full group-create/edit -> role-group association -> user-role assignment journey; read-only IAM views; source-locked SSO assignments; wildcard exact-versus-future selection; dependency/delete conflicts; stale preview/revision handling; hidden/disabled/enabled controls; no inaccessible payload data; modified localStorage or direct HTTP cannot grant access |
| Onboarding | Manual multi-role account creation is atomic; explicit zero-role account has no data access; first-admin setup unavailable after initialization; creation invitation grants exactly reviewed roles; linking preserves assignments; concurrent/replayed/expired/revoked invitation rejected; failed provisioning recovers without partial rights; changed issuer authority or role revision blocks redemption; unmapped SSO denied; auto-provision uses only trusted mappings/policy; initial-role changes do not rewrite existing users |
| Migration | Real legacy behavior matrix; admin `*` expansion documented; collaborators preserved; stable user IDs/locations; unresolved jobs blocked; idempotent restart and coordinated restore |
| Coverage | New route/action/tool sub-action/processor effect without a declaration fails registration/test gate; all public/internal exceptions explicit and reviewed |

Suggested new test modules, subject to existing test organization:

- `tests/test_permission_rules.py`
- `tests/test_permission_catalog.py`
- `tests/test_permission_store.py`
- `tests/test_access_control_scopes.py`
- `tests/test_permission_ingress_coverage.py`
- `tests/test_permission_revocation.py`
- `tests/test_permission_migration.py`
- focused additions to existing security, conversation-sharing, tool-authorization, FlowRun, relay, and UI suites.

Reuse existing route/security tests, including [test_route_security_matrix.py](../tests/test_route_security_matrix.py) and [test_tool_call_security_ordering.py](../tests/test_tool_call_security_ordering.py). Add genuine bypass/regression scenarios, not tests that merely repeat the matcher implementation.

Performance checks use representative users, roles, rules, resources, and list sizes. Measure policy lookup and list filtering with cold and warm caches; establish budgets from the current endpoint baseline. Avoid an arbitrary latency target without measuring the deployed architecture.

## 14. Definition of Done

- [ ] User -> multiple roles -> permission groups -> rules is the sole application grant model.
- [ ] `*` and `A.*` have the agreed dynamic semantics.
- [ ] Resource scopes and reach are encoded in canonical permission names.
- [ ] Adding roles combines grants without role ranking or negative overrides.
- [ ] Every registered operation has a target-aware authorization contract.
- [ ] Every enabled ingress, processor effect, wrapper, and extension host boundary enforces it.
- [ ] Ownership/ACL checks, token binding, secret bindings, and execution ceilings remain effective.
- [ ] Ordinary roles cannot cross users or acquire new privileges through IAM mutations.
- [ ] Revocation ordering, worker consistency, and long-lived session behavior are tested and documented.
- [ ] The UI reflects server decisions and exposes effective-rights provenance.
- [ ] Groups, role-group associations, and user-role assignments have complete guarded administration screens.
- [ ] Manual creation, invitations, identity linking, SSO auto-provisioning, initial-role policies, and first-admin setup follow the same assignment model and pass end-to-end tests.
- [ ] One-shot migration and crash/restore behavior pass rehearsal.
- [ ] Legacy single-role authorization and substring gates are removed.
- [ ] Relevant tests, documentation, full validation, and review are complete.

## 15. Documentation and source references

During implementation, update the existing guides alongside each changed capability rather than leaving this plan as the only documentation:

- [02_REFERENCE_TASKS_SERVICES.md](02_REFERENCE_TASKS_SERVICES.md): operation declarations and tool/task contracts.
- [AGENT_SYSTEM.md](AGENT_SYSTEM.md): principal propagation and delegated execution.
- [CHAT_UI_TEMPLATES.md](CHAT_UI_TEMPLATES.md): permission projection and extension/UI contracts.
- [RESOURCE_ACL_SHARING_PLAN.md](RESOURCE_ACL_SHARING_PLAN.md): shared principal model, typed group terminology, scope permissions, and capability-based admin overrides.
- [ADMIN_CROSS_USER_SCOPES_PLAN.md](ADMIN_CROSS_USER_SCOPES_PLAN.md): replace administrator-name checks with explicit `any` permissions.
- Authentication/administration and migration documentation located during WP0.
- [RELEASE_PROCEDURE.md](RELEASE_PROCEDURE.md): follow the existing release circuit; this plan does not weaken it.

External design reference: [OWASP Authorization Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html), consulted 2026-09-07. Its guidance on default denial, server-side checks on every request, and authorization testing informs the enforcement and validation requirements above. The role/group/wildcard semantics are PawFlow product decisions specified in section 1.

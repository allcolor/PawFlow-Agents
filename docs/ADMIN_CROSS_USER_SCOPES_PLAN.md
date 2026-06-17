# Admin Cross-User Scopes — View-All + Owner Override Plan

Status: **proposed** (design + phased implementation plan, not yet implemented).

## Goal

Give an **admin** two new, strictly additive capabilities over the scoped
repository surfaces (Services, the Flow repository, and the resource Dépôts —
agents / skills / prompts / themes / voices / tasks / MCP / agent hooks):

1. **View** — an admin can switch a panel between *self view* (exactly what
   they see today: global + their own user scope + the current conversation)
   and *view-all* (the same resources for **every** user and conversation),
   with each row labelled by the **owner** (user, and conversation when
   conv-scoped).
2. **Create** — when an admin creates a `user`- or `conv`-scoped resource they
   may **override the owner** (target a different user / a conversation that is
   not theirs). When no override is supplied, behaviour is unchanged: the owner
   is the caller.

## Invariants (non-negotiable)

- **Zero change for a normal user.** No new param set, or a non-admin caller
  supplying one, must resolve to today's exact code path and today's exact
  response bytes. The admin gate is `_is_admin(flowfile)`
  (`tasks/ai/actions/service_flow.py:279`,
  `\"admin\" in (flowfile.get_attribute(\"http.auth.roles\") or \"\")`).
- **Self view is byte-identical to today.** Owner-labelling fields are added
  **only** in view-all responses, so existing self-mode payloads, caches, and
  client contracts are untouched.
- **Default create path is unchanged.** Owner override is honoured only when
  the caller is admin AND an explicit `target_user_id` (and, for conv scope,
  `target_conversation_id`) is supplied AND it differs from the caller.
- **Global scope stays admin-only** (already enforced at every write site).
- **No new role.** `Role.ADMIN` (`core/security.py:35`) is sufficient.
- **No storage-schema change.** On-disk layout (`core/paths.py`) is reused.

---

## Current architecture (what we build on)

### Identity & roles
- `http.auth.principal` = `user_id` (= `session.username`, set in
  `tasks/io/oauth_callback.py:278`).
- `http.auth.roles` carries `admin`; tested via `_is_admin(flowfile)`.
- `core/security.py` `SecurityManager.list_users()` (`security.py:254`) returns
  `username` + `display_name` (no password hash) — the source for owner display
  names. Conversation titles come from `ConversationStore`.

### Scoped storage (`core/paths.py`)
```
global : data/repository/{rtype}/global/
user   : data/repository/{rtype}/users/<user_id>/
conv   : data/repository/{rtype}/users/<user_id>/<conv_id>/
```
Conv-scoped **services** additionally live in conversation extras
(`core/service_registry.py` `_load_conv`, `CONV_EXTRAS_KEY`); some conv agents /
task defs live in conversation extras too (`ResourceStore.list_all`,
`core/resource_store.py:280-298`).

### The three listing surfaces (all single-user today)
| Panel | Action | Location | Walks |
|---|---|---|---|
| Services | `list_services` | `service_flow.py:1025` | global + user(caller) + conv(current) |
| Dépôt Flows | `list_available_flows` | `service_flow.py:2910` | `flows/global` + `flows/users/<uid>` + `.../<conv>` |
| Dépôt agents/skills/… | `list_resources` | `agent_resource.py:1236` | `ResourceStore.list_all` per rtype (1 user) |

Underlying primitives: `ScopedRepository.list` / `list_available`
(`core/repository.py:197,234`), `ResourceStore.list_all`
(`core/resource_store.py:244`), `ServiceRegistry.get_all`
(`core/service_registry.py:540`, lazy per-scope load).

### Precedent already cross-user
`_admin_list_flows` (`tasks/io/admin_actions.py:106`) already enumerates **all**
deployed flow instances and exposes `inst.owner` (line 118) — the
DeploymentRegistry/ExecutorRegistry carry owner+conv
(`core/executor_registry.py:83,110-111,369`). This validates the owner-labelled
view-all pattern; the *repository/template/service-definition* side is what
lacks enumeration + owner tagging.

### The three create paths
- Services: `service_install` (`service_flow.py:1234`) computes the scope id
  inline from `user_id`/`conv_id` at `service_flow.py:1305-1312`. Other service
  ops use `_service_scope_id(scope, user_id, conv_id)` (`service_flow.py:282`,
  call sites 1198/1591/1618/1643/1667/1764/1790/1823-1824).
- Flows: roots derived from caller via `_template_roots`
  (`service_flow.py:592`); deploy owner via `core/executor_registry.py:83`.
- Resources: `ResourceStore.create(rtype, name, user_id, data, conversation_id)`
  → `_map_scope` (`core/resource_store.py:152,376`); UI create at
  `agent_resource.py:197-227,363-400`. Global already gated by `_is_admin`
  (`agent_resource.py:202,295,377,414`).

---

## Design

### Request contract (additive)
**Listing actions** accept an optional `view`:
- `view` absent or `"self"` → current behaviour.
- `view == "all"` → honoured **iff** `_is_admin`; otherwise silently treated as
  `"self"` (no error, no leak).

**Create actions** accept optional `target_user_id` and
`target_conversation_id`:
- absent → current behaviour (owner = caller).
- present + admin + differs from caller → owner overridden after validation.
- present + non-admin → `403`.

### Owner labelling (view-all only)
Each row gains, **only in view-all responses**:
```
owner_id        # user_id that owns the scope ("" for global)
owner_display   # display_name or username; "" for global
conv_id         # set for conv-scoped rows
conv_title      # best-effort conversation title
```
Self-mode rows are emitted unchanged.

### Shared helper module — `core/admin_scope.py` (new)
One small module so the read gate, the write override, and identity resolution
are defined once and reused by both `service_flow.py` and `agent_resource.py`.
```python
def is_admin(flowfile) -> bool: ...                 # mirror of _is_admin
def wants_view_all(body, flowfile) -> bool:          # view=="all" and is_admin
    ...
def effective_owner(body, caller_user_id, caller_conv_id, flowfile,
                    scope) -> tuple[str, str]:
    """Return (owner_user_id, owner_conv_id) for a create/write.
    Default = caller. Override only when admin + target_* present + differs.
    Raises PermissionError (→403) for non-admin override,
    ValueError (→400) for unknown user or conv-owner mismatch."""
def display_name_for(user_id: str) -> str: ...       # SecurityManager-backed, cached
def conv_title_for(user_id: str, conv_id: str) -> str: ...
```
Validation rules inside `effective_owner`:
- `target_user_id` must exist (`SecurityManager.get_user`).
- For `conv` scope, `target_conversation_id` must belong to the resolved owner
  (checked via `ConversationStore`) — never write
  `users/<owner>/<conv-of-a-third-party>`.

---

## New read primitives (enumeration)

### `core/repository.py`
`ScopedRepository.list_all_owners(rtype) -> List[Dict]` (read-only):
- global: existing `list(rtype, "global")`.
- user: for each `data/repository/{rtype}/users/<uid>` dir → `list(rtype,
  "user", uid)`, tag `_owner_id=uid`.
- conv: for each `users/<uid>/<conv>` dir → `list(rtype, "conv", uid, conv)`,
  tag `_owner_id=uid`, `_conv_id=conv`.
- Reuses the existing per-scope `_list_cache`; add a separate enumeration cache
  keyed by `(rtype, signature-of-users-tree)`.
- Skill/markdown vs directory types already handled by `list`.

### `core/resource_store.py`
`ResourceStore.list_all_global(resource_type) -> List[Dict]`:
- wraps `list_all_owners` for the mapped repo type;
- additionally folds in conv-extras-backed agents / task defs across **all**
  conversations (mirror of `list_all`'s extras handling at
  `resource_store.py:280-298`), iterating `ConversationStore.list_conversations`
  per user. Each entry tagged `_owner_id` / `_conv_id`.

### `core/service_registry.py`
`ServiceRegistry.iter_all_scopes() -> List[tuple[scope, scope_id, owner_id, conv_id]]`:
- `("global", "", "", "")`;
- scan `data/repository/services/users/*` → `("user", uid, uid, "")`;
- conv services live in conv extras → iterate
  `ConversationStore.list_conversations()` across users, yield
  `("conv", conv_id, owner_uid, conv_id)` where the conv carries services.
- Caller then uses the existing `get_all(scope, scope_id)` per tuple.

---

## Handler changes

### Read — `list_services` (`service_flow.py:1025`)
- Compute `all_view = admin_scope.wants_view_all(body, flowfile)`.
- self: unchanged loops.
- all: iterate `reg.iter_all_scopes()`; for each, the existing per-scope row
  builder, plus `owner_id`/`owner_display`/`conv_id`/`conv_title`.
- **Perf**: in all-view, skip `_service_started_for_listing` probes (or make
  them best-effort/async) — they are per-scope and costly across the fleet.
  List *definitions*, not live-start state. Payload already excludes secrets
  (only `service_id/type/enabled/started/description/provider/install_state`).

### Read — `list_available_flows` (`service_flow.py:2910`)
- self: unchanged `roots`.
- all: `roots = [("global", flows/global)] + [("user", flows/users/<uid>) …] +
  [("conv", flows/users/<uid>/<conv>) …]`; derive `owner_id`/`conv_id` from the
  path of each `latest.json` relative to `flows/users/`. Add owner fields to
  each template row.

### Read — `list_resources` (`agent_resource.py:1236`)
- self: unchanged (per-conv membership/active flags preserved).
- all: for each rtype use `rs.list_all_global(rtype)` instead of
  `rs.list_all(uid, conv)`; emit the catalog rows with owner fields and **omit**
  the per-conversation `active`/`in_conversation`/binding flags (they are
  caller-conversation-specific and meaningless in a global catalog). Themes /
  voices: extend `list_themes` / `voice_clone_cache` enumeration similarly, or
  mark out-of-scope for v1 (see Scope cuts).

### Write — owner override
- `service_install` (`service_flow.py:1234`): at the scope-id block
  (`1305-1312`), replace `user_id`/`conv_id` with
  `owner_uid, owner_conv = admin_scope.effective_owner(body, user_id, conv_id,
  flowfile, scope)`; use `owner_uid`/`owner_conv` for `scope_id`. Managed-relay
  token logic unchanged.
- `_service_scope_id` call sites that **mutate** an existing definition
  (`update_service` 1764, enable/disable 1618/1643, uninstall 1591, move
  1823-1824): accept the same override so an admin can edit another user's
  service. Read-only `get_service_detail` (1667) — see Security.
- Flows create/deploy: thread `effective_owner` into `_template_roots` /
  create-flow / deploy owner (`executor_registry` already takes `owner`).
- Resources: `agent_resource.py` create paths (197-227, 363-400) pass
  `effective_owner` as the `user_id`/`conversation_id` to
  `ResourceStore.create`.

---

## Security review points

- **Secret exposure in view-all.** `list_services` payload is safe. But
  `get_service_detail` (`service_flow.py:1657`) returns full `config`. Keep it
  per-scope (caller resolves the scope id) and require admin + matching owner
  resolution for cross-user reads; audit that no service serialises plaintext
  secrets into `config` (services reference secrets by binding). Redact if any
  do.
- **Owner-mismatch writes.** `effective_owner` forbids conv overrides where the
  conversation does not belong to the target user.
- **Non-admin probing.** `view=="all"` and `target_*` are silently downgraded /
  rejected for non-admins; never echo other users' existence to a non-admin.
- **Audit log.** Log admin cross-user writes (target user/conv) at INFO, like
  `create_user` logging in `security.py:194`.

---

## Caching

- `ScopedRepository._list_cache` keys per `(rtype, scope, uid, conv)` — reused.
- New enumeration cache invalidated on the users-tree signature.
- `agent_resource` flow-template cache is keyed by `user_id`
  (`agent_resource.py:127`); add the `view` dimension to the UI list cache key
  (`_ui_list_cache_key`, `tasks/ai/agent_actions.py:141`) so a self-view result
  is never served for an all-view request and vice-versa.

---

## Frontend (`tasks/io/chat_ui/`)

- `services.js`, `resources.js`, and the flow-repository panel: add an
  **admin-only** toggle ("View all / Tout voir") that sets `view:"all"` on the
  list request and renders an owner badge (`👤 owner_display`, `💬 owner ·
  conv_title`) — mirroring how `_admin_list_flows` rows show `owner`.
- Create dialogs (service install, flow deploy/save, resource create): an
  admin-only **user picker** (and conv picker for conv scope) that sets
  `target_user_id` / `target_conversation_id`. Default selection = self, so the
  default request is identical to today.
- Populate the picker from an admin users listing (reuse / expose via
  `tasks/io/admin_actions.py`; `admin_list_*` actions already exist there).
- i18n keys in `tasks/io/chat_ui/i18n.js` + locale files (EN/FR/ES).
- Toggle/picker hidden entirely when the session is not admin.

---

## Phases

1. **Phase 0 — Helpers & identity.** `core/admin_scope.py`
   (`is_admin`/`wants_view_all`/`effective_owner`/`display_name_for`/
   `conv_title_for`), with unit tests. No behaviour change yet.
2. **Phase 1 — Read primitives.** `ScopedRepository.list_all_owners`,
   `ResourceStore.list_all_global`, `ServiceRegistry.iter_all_scopes` + caches
   + tests.
3. **Phase 2 — Wire read.** `view`-gated branches in `list_services`,
   `list_available_flows`, `list_resources`; owner labelling; UI-list-cache key
   gains `view`.
4. **Phase 3 — Wire write.** `effective_owner` into `service_install`, the
   `_service_scope_id` mutation sites, flow create/deploy, and
   `ResourceStore.create` callers; validation + audit logging.
5. **Phase 4 — Frontend.** Toggles, owner badges, user/conv pickers, i18n.
6. **Phase 5 — Tests & docs.** Full matrix (below); update `docs/` and ROADMAP.

---

## Tests (mirror existing suites — e.g. `tests/test_user_services.py`)

Read:
- non-admin + `view:"all"` → identical to self (downgrade).
- admin + self → byte-identical to today.
- admin + all → rows for ≥2 users + a conv-scoped row, each with correct
  `owner_id`/`owner_display`/`conv_id`.
- self-mode payload contains **no** owner fields (contract guard).

Write:
- admin + `target_user_id` (user scope) → definition lands under
  `users/<target>/…`; caller's tree untouched.
- admin + `target_user_id` + `target_conversation_id` (conv scope) → lands under
  `users/<target>/<conv>/…`.
- non-admin + `target_user_id` → `403`, nothing written.
- admin + unknown `target_user_id` → `400`.
- admin + conv not owned by target → `400`.
- no target params → identical to today (owner = caller).

---

## Scope cuts for v1 (call out, don't silently drop)

- Themes / voice clones in view-all may ship in a follow-up if their
  enumeration helpers (`chat_themes.list_themes`, `voice_clone_cache`) need
  cross-user support — `list_resources` would simply omit them from all-view
  until then, logged explicitly.
- Live `started` probing in cross-user `list_services` is intentionally skipped
  for performance; the panel shows definition + enabled state, not live health,
  in view-all.

---

## Risk summary

| Risk | Mitigation |
|---|---|
| Regression for normal users | `view`/`target_*` default to today's path; owner fields only in all-view; cache key gains `view` |
| Secret leak across users | view-all lists definitions only; audit `get_service_detail`; redact plaintext config |
| O(users×convs) cost | definitions-only enumeration; skip live-start probes; enumeration cache |
| Cross-user write to wrong tree | `effective_owner` validates user existence + conv ownership |
| Stale cache serving wrong view | `view` added to `_ui_list_cache_key` + enumeration cache signature |

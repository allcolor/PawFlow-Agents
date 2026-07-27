# Conversation Sharing — Implementation Plan

Status: **in progress** — Phases 1 (core primitive + data model,
`core/conversation_access.py`), 2 (SSE authorization) and 3 (write-path
actions) are implemented; Phases 4-7 are still design only. Sharing is
reachable through the actions, but has no UI yet (Phase 7) and does not
reach the channel bridges yet (Phase 4).

## Goal

Let a conversation's owner invite other PawFlow users as **collaborators**, so
that once accepted, all of them read and write into the **same** conversation
(same messages, same agents, same LLM services) instead of each having their
own private copy. One owner per conversation at all times; collaborators can
be full write participants or read-only observers.

## Non-goals (v1)

- Real-time push notification of a new invite to a user who isn't currently
  looking at the conversation (see "Scope cuts for v1").
- Per-collaborator spend limits or credential isolation — confirmed with the
  feature owner that budgets/credentials stay scoped to
  conversation/agent/service exactly as today, regardless of which
  collaborator is typing.
- Nested/transitive sharing (a collaborator re-sharing to someone else) — only
  the owner can invite or kick.
- Cross-relay identity reconciliation (see "Scope cuts for v1").

## Model (confirmed with feature owner)

- **Roles**: `owner` (exactly one, always), `write` collaborator, `read`
  collaborator (read-only observer).
- **Invite**: owner-only. No cascading invite rights.
- **Kick**: owner-only, effective at the next authorization check (message
  submission or SSE (re)connect) — no mid-stream interruption required, the
  authorization check already sits at exactly that point today.
- **Owner departure**: if the owner's account is deleted (or otherwise stops
  resolving to a valid user), the conversation is not reassigned proactively.
  The **first** `write` collaborator whose message passes the authorization
  check becomes the new owner, lazily, on that request.
- **Budgets/credentials**: unchanged — already scoped by
  `conversation`/`agent`/`llm_service` (`core/budget_store.py:39`,
  `SCOPE_TYPES = ("global", "user", "conversation", "agent", "llm_service")`),
  independent of which human is typing. Tool/service credentials attached to
  the conversation's agents are likewise usable by any authorized writer
  today already (nothing new to build).

## Invariants (non-negotiable)

- **Zero change for an unshared conversation.** No collaborators list, or an
  empty one, must resolve to today's exact code path: same storage location,
  same authorization result, same response bytes. The new resolver (below)
  degrades to today's single-owner check when there is nothing to share.
- **No storage-schema change.** On-disk layout stays
  `{CONVERSATIONS_DIR}/{owner_user_id}/{conv_id}/...`
  (`core/paths.py:121-122`). A shared conversation still physically lives
  under the owner's directory; sharing is purely an authorization + routing
  layer on top, mirroring the precedent set in
  `docs/ADMIN_CROSS_USER_SCOPES_PLAN.md` ("No storage-schema change").
- **Encryption needs no new cryptography.** `KeyVault` unlocks DEKs in
  server-side RAM (`core/key_vault.py:290` `class KeyVault`); decryption
  happens server-side for any authorized request, not per-client. Granting a
  collaborator access to an encrypted conversation is an authorization
  problem, not a re-keying/multi-recipient-wrapping problem. Caveat carried
  into "Risk summary": the DEK still depends on the *owner* (or an admin)
  having unlocked it; if it's locked, collaborators are blocked exactly as
  the owner would be.
- **Single source of truth for "can this requester act on this conversation"**:
  every call site that currently special-cases owner-equality must go through
  the new resolver (below), not re-implement its own ACL check.

---

## Current architecture (what we build on)

### Identity & auth
- `http.auth.principal` = the authenticated `user_id` (set by
  `validateSessionAuth`, `tasks/io/validate_session_auth.py:38`, on cookie or
  bearer session — pure **authentication**, no per-conversation check).
- The default flow (`data/repository/flows/global/default/pawflow_agent/
  versions/1.0.0.json`) routes `GET:/chat`, `GET:/api/agent/events`,
  `GET:/files`, `GET:/fs`, `POST:/api/agent`, `POST:/api/ui` all through
  `validate_auth` before dispatch (lines 330-358) — this only proves *who is
  asking*, never *whether they may see conversation X*.

### Storage layout & ownership resolution
- `core/paths.py:121` — `conversation_dir(user_id, conv_id) → CONVERSATIONS_DIR
  / user_id / conv_id`.
- `core/_conversation_store_paths.py:199` — `_conv_dir(cid, user_id="")`:
  - If `user_id` is passed, resolves directly (today's normal call shape —
    see below) **and caches** `cid → user_id` in `self._cid_user`.
  - If `user_id` is *not* passed, it first checks that same `_cid_user` cache,
    then **falls back to scanning every user directory on disk** for a
    matching `cid` (line 208-215) — i.e. an owner-resolution-by-cid primitive
    already exists, just used defensively/lazily rather than as a first-class
    index. This is the mechanism the new resolver builds on (see Design).
- `core/flow_runtime_access.py:77` — `conversation_owner(cid)` reads the
  authoritative owner from conversation metadata (`get_metadata(cid)
  ["user_id"]`) — the *other* existing way to resolve ownership, used by the
  channel-bridge/flow-runtime path (next section).

### Write path #1 — webchat POST /api/agent actions
- `tasks/ai/actions/_conv_core.py` (`resume_conversation` line 318,
  `delete_conversation` line 299, and siblings) call
  `store.load(conv_id, user_id=user_id)` / `store.delete(conv_id,
  user_id=user_id)` where `user_id` is **the authenticated requester**
  (`http.auth.principal`), not a verified owner. Because `_conv_dir` resolves
  directly from the *given* `user_id` when one is supplied, a non-owner's own
  `user_id` simply can't reach another user's directory — access control today
  is an emergent property of path partitioning, not an explicit check. This is
  exactly what breaks for sharing: a collaborator's own `user_id` will never
  be the conversation's owner directory, so these call sites must resolve and
  pass the **owner's** `user_id` for storage, while keeping the **requester's**
  `user_id` for attribution/ACL purposes (see Design).

### Write path #2 — channel bridges (Telegram/Discord/Slack/WhatsApp)
- `core/agent_runtime_api.py` `AgentRuntimeAPI.submit_message` /
  `AgentResultWaiter` — used by `tasks/io/telegram_agent_client.py` and
  siblings for other channels.
- `core/flow_runtime_access.py` `authorize_conversation_target()` (line 120)
  and `authorize_user_target()` (line 93) do an **explicit** owner-equality
  check today (`if owner != ctx.user_id: raise FlowRuntimeAccessError`) —
  this is the other authorization pattern in the codebase, distinct from
  path-partitioning above, and it must also learn about collaborators.
- Each channel's cross-channel identity resolution
  (`tasks/ai/_agentctx_p1.py:225` `CHANNEL_ATTRS`) and per-channel "active
  conversation" pointer (`core/identity_service.py:200,212`
  `set_active_conv`/`get_active_conv`) are **already keyed per
  `(user_id, channel)`** — nothing to change here: a collaborator can already
  point their own Telegram/webchat session at the shared `conversation_id`
  independently of the owner, once authorization allows it.

### Read path — SSE (the actual gap to close)
- `tasks/io/agent_sse_stream.py:43` `AgentSSEStreamTask.execute()` reads
  `conversation_id` straight from the query string and calls
  `ConversationEventBus.instance().subscribe(conversation_id, ...)` — **it
  never reads `http.auth.principal` or checks it against the conversation's
  owner at all.** Combined with `validate_auth` being authentication-only
  (previous section), **today any logged-in PawFlow user who knows/guesses a
  `conversation_id` can already open its live SSE stream** — a pre-existing
  gap, not introduced by this feature, but one this work should close in the
  same change: locking down writes while leaving reads wide open would be a
  worse mismatch than today, precisely because sharing makes conversation IDs
  a thing users routinely hand to each other.
- `core/conversation_event_bus.py` `ConversationEventBus._subscribers` is
  already a `Set[SSEWriter]` **per conversation_id** (not per-owner) — no
  architecture change needed to support N simultaneous live viewers, only the
  missing authorization check at `subscribe()` time.

### Budgets, credentials, encryption — confirmed no change needed
- `core/budget_store.py:39` `SCOPE_TYPES` includes `"conversation"`,
  `"agent"`, `"llm_service"` — spend tracking/enforcement is already
  independent of which human sent the message.
- Service/tool credentials are attached to the conversation's agents
  (conversation or global scope), not to the requesting human — a
  collaborator's message triggers the *same already-configured* agent/service,
  exactly like the owner's would.
- `core/_conversation_store_encryption.py` — per-conversation DEK held
  server-side in `KeyVault`; `_codec_for(cid)` gates on
  `_is_encryption_enabled` + an unlocked DEK, not on which user is asking.
  Reaching `_codec_for` for a collaborator is therefore purely gated by the
  new authorization resolver, same as plaintext conversations.

---

## Design

### Data model — collaborators ACL

Stored via the existing generic `extra` mechanism
(`ConversationStore.set_extra`/`get_extra`) under the owner's conversation —
no schema migration:

```json
// store.get_extra(cid, "collaborators") -> list
[
  {
    "user_id": "bob",
    "role": "write",            // "write" | "read"
    "status": "pending",        // "pending" | "accepted" | "kicked"
    "invited_by": "alice",
    "invited_at": 1785...,
    "responded_at": 0.0
  }
]
```

`status` distinguishes an invite awaiting acceptance from an active
collaborator, so joining is a two-sided action (owner invites, invitee
accepts) rather than the owner unilaterally CC'ing someone into a private
thread. `kicked` entries are kept (not deleted) for audit/history and so a
re-invite is a status transition, not a duplicate row.

### New primitive — `core/conversation_access.py` (new file)

Mirrors the shape of `core/flow_runtime_access.py` but is the **single**
place every call site (webchat actions, channel bridges, SSE, FileStore)
consults — replacing ad hoc owner-equality checks with membership checks.

```python
@dataclass(frozen=True)
class ConversationAccess:
    owner_user_id: str
    role: str          # "owner" | "write" | "read" | "" (no access)
    storage_user_id: str  # the id to pass to ConversationStore for path resolution
                           # (always the OWNER's id — see "Write path #1")

def resolve_conversation_access(cid: str, requester_user_id: str) -> ConversationAccess:
    """Single source of truth. Empty role => caller must treat as not-found
    (never leak existence of a conversation the requester can't reach).
    """

def require_write(cid: str, requester_user_id: str) -> ConversationAccess:
    """role in {owner, write}; also runs owner-reassignment-on-write
    (see below); raises ConversationAccessError otherwise."""

def require_read(cid: str, requester_user_id: str) -> ConversationAccess:
    """role in {owner, write, read}; raises otherwise."""
```

`resolve_conversation_access` degrades to today's behavior for an unshared
conversation: `owner_user_id == requester_user_id` and there is no
collaborators list → `role="owner"`, `storage_user_id=requester_user_id`,
identical to today's direct path resolution (invariant: zero change when
nothing is shared).

For a collaborator: `owner_user_id` is resolved via the *existing* `_conv_dir`
fallback-scan/cache (`core/_conversation_store_paths.py:199`) exposed as a
new read-only `ConversationStore.resolve_owner(cid) -> str` wrapper, then the
collaborators list (`get_extra(cid, "collaborators")`, loaded via the OWNER's
`user_id`) is checked for a `status="accepted"` row matching
`requester_user_id`.

### Message attribution

`LLMMessage.source` (`core/_llm_types.py:250`) gains an `author_user_id` field
for `role="user"` messages, populated at ingestion from the resolved
`requester_user_id` (not `storage_user_id`, which is always the owner for
storage purposes). Backward compatible: absent on old messages and on
non-shared conversations, defaulting to the owner in the UI when unset.

### Owner reassignment

Implemented inside `require_write`, not as a separate migration job: if
`resolve_conversation_access` finds `owner_user_id` no longer resolves to a
valid user (`SecurityManager`/`IdentityService` lookup fails) **and** the
requester is an accepted `write` collaborator, the conversation's metadata
`user_id` is updated to the requester **and its storage directory is moved**
(`{owner_dir}/{cid}` → `{new_owner_dir}/{cid}`, `os.rename` under the existing
per-conversation lock) before continuing the request — the one storage-layout
exception, required because the old owner's directory could be deleted or
inaccessible. This is the only place the "no storage-schema change" invariant
is intentionally relaxed, and only as a rare recovery path, not the common
case.

### Kick semantics

`kick_collaborator` (owner-only action) sets `status="kicked"`. No
mid-stream interruption: the next `require_write`/`require_read` call for
that user (next message submission, or next SSE reconnect since
long-lived SSE connections aren't proactively torn down in v1 — see Scope
cuts) returns `role=""` and the request is rejected. An in-flight LLM turn
that was already authorized before the kick is not aborted mid-turn.

---

## Handler changes

### New actions (`tasks/ai/actions/_conv_core.py` or a new sibling module)

| Action | Caller | Effect |
|---|---|---|
| `share_conversation` | owner | Adds/updates a `collaborators` row with `status="pending"`, `role` as requested |
| `list_collaborators` | owner, or any accepted collaborator (sees own row only) | Returns the ACL (owner sees all, collaborator sees self + owner) |
| `respond_to_share_invite` | invitee | `accept`/`decline` — flips `status` to `accepted` or removes the row |
| `update_collaborator_role` | owner | `write` ↔ `read` |
| `kick_collaborator` | owner | `status → kicked` |
| `leave_conversation` | any accepted collaborator | Self-service equivalent of being kicked |
| `list_shared_conversations` | any user | Conversations where the caller has an accepted collaborator row — needs the reverse index below |

### Reverse index (new, small)

`list_shared_conversations` cannot afford a disk-wide scan per request.
Maintain a lightweight per-user side index,
`{CONVERSATIONS_DIR}/_shared_index/{user_id}.json` — `[cid, ...]` — updated on
`share_conversation` (add), `respond_to_share_invite`/`kick_collaborator`
(remove). Read-mostly, rebuilt from a full ACL sweep if ever found
inconsistent (same defensive posture as `admin_scope.py`'s
best-effort owner map).

### Modified call sites (owner-equality → `resolve_conversation_access`)

- `tasks/ai/actions/_conv_core.py` — every action taking `conversation_id`
  (`resume_conversation`, `delete_conversation` stays owner-only by design,
  message-send path, encryption enable/unlock actions) swaps its raw
  `user_id` storage argument for `access.storage_user_id`, and gates on
  `access.role` before proceeding.
- `tasks/io/agent_sse_stream.py:43` — add `require_read` before
  `bus.subscribe(...)`, using `flowfile.get_attribute("http.auth.principal")`
  (now actually read here, which it isn't today).
- `core/flow_runtime_access.py` `authorize_conversation_target`/
  `authorize_user_target` — extend the owner-equality branch to accept an
  accepted collaborator of the matching role, via the same
  `resolve_conversation_access` primitive (keeps one source of truth instead
  of two ACL implementations).
- `core/agent_runtime_api.py` / channel bridges — no change beyond the above;
  they already resolve `conversation_id` generically and go through
  `flow_runtime_access`.
- `resource_store.py` / `server_relay_manager.py` — **audit, not blanket
  change**: enumerate every call site that assumes `requester == owner` for
  conversation-scoped resource/relay lookup and confirm each either (a) only
  needs read access (safe to open to collaborators) or (b) is intentionally
  owner-only (e.g. relay *management*, as opposed to a relay a flow task
  merely executes against). Flagged as its own audit pass in Phases, not
  assumed safe to change wholesale.
- FileStore attachment access (`authorize_filestore_target`,
  `core/flow_runtime_access.py:150`) — follows the conversation's access
  automatically since it already delegates to `authorize_conversation_target`.

---

## Security review points

- **Never leak existence.** A `role=""` resolution (not found, or found but
  not authorized) must produce the *same* 404 a nonexistent `conversation_id`
  would — never a distinct "exists but you can't see it" response, in any of
  the modified call sites.
- **Invite is two-sided.** `share_conversation` only ever creates a `pending`
  row; a collaborator only gains access once *they* call
  `respond_to_share_invite(accept)`. The owner cannot force access.
- **The SSE gap must close in the same change**, not be deferred — see
  "Read path" above. Shipping write-side ACLs while `agent_sse_stream.py`
  stays wide open would make sharing (which makes conversation IDs
  routinely shared) actively worse than today's status quo.
- **Encryption unlock stays owner/admin-gated.** Document clearly (UI +
  docs) that a collaborator's access to an encrypted conversation depends on
  the DEK being unlocked by whoever can unlock it today — sharing doesn't
  grant a collaborator their own unlock capability.
- **Owner reassignment directory move** must happen under the same
  `_get_conv_lock(cid)` used elsewhere in `ConversationStore` to avoid a
  concurrent writer mid-`os.rename`.
- **Audit trail**: keep `kicked`/superseded collaborator rows (never hard
  delete) so "who had access when" is reconstructable.

## Frontend (`tasks/io/chat_ui/`)

- **Sidebar**: split the conversation list into "Mine" and "Shared with me"
  sections (backed by `list_shared_conversations`), following the existing
  `conv-item`/`conv-list` structure in `conversations.js`/`template.html`.
- **Pending invites**: a small badge/section (reuse the existing
  `notification-row` pattern from `messages_render.js` for the visual
  language) with Accept/Decline actions calling `respond_to_share_invite`.
- **Share dialog** (owner only): reuses the generic overlay-dialog pattern
  already used throughout (`resources_*.js`, `dialogs.js`) — pick a PawFlow
  user, choose `write`/`read`, list current collaborators with role-change and
  kick controls inline.
- **Message author badges**: `messages_render.js`/`sse_handlers_*.js` — when
  `extra.source.author_user_id` is present and differs from the
  conversation's owner, render a distinct author label on `role="user"`
  bubbles (today all `user` bubbles are visually identical, since there was
  never more than one).
- **Resources sidebar**: a "Collaborators" entry alongside the existing
  Agents/Services/Skills sections for the current conversation, using the
  same `showResourceMenu`-style context menu pattern for role-change/kick.
- i18n: new keys for all of the above, added alphabetically to `en/fr/es.json`
  per the existing convention (see `openInTaskTab` et al. from this session's
  earlier task-tabs work for the exact pattern to follow).

## Phases

1. ~~**Core primitive + data model**~~ — **done**. `core/conversation_access.py`
   (`resolve_conversation_access`/`require_read`/`require_write`/`require_owner`,
   ACL read/write via `extra`, reverse `_shared_index`),
   `ConversationStore.resolve_owner()` + `shared_index_path()`, covered by
   `tests/test_conversation_access.py`. No behavior change (nothing calls it).
   Two deliberate deviations from the design above:
   - the reverse index holds `pending` **and** `accepted` rows (an invite has
     to be discoverable before it can be accepted); it is cleared on
     decline/kick, not on accept;
   - `require_write` does not yet perform owner reassignment — that arrives
     with its directory-move in Phase 6, where it can be tested against a
     deleted-owner fixture.
2. ~~**Close the SSE gap**~~ — **done**. `agent_sse_stream.py` resolves
   `require_read` from the trusted principal before `bus.subscribe(...)` and
   answers a rejection with the same 404 an unknown `conversation_id` gets.
   A request carrying no trusted identity at all (a custom flow wired without
   `validate_auth`) is rejected too — an unauthenticated subscriber is
   precisely what this closes. Covered by
   `tests/test_sse_streaming.py::TestAgentSSEStreamAuthorization`.
3. ~~**Write-path actions**~~ — **done**. `tasks/ai/actions/_conv_sharing.py`
   implements the seven actions above, wired into the `conversation.py`
   dispatcher; every `_conv_core.py` action naming a conversation resolves
   `require_read`/`require_write`/`require_owner` before touching it and then
   addresses storage with `access.storage_user_id`. Covered by
   `tests/test_conversation_sharing.py`. Notes on the shape it landed in:
   - `resolve_conversation_access` and friends take an optional `store=`.
     Action handlers are handed a store by the dispatcher; authorizing
     against the singleton while reading from that store would be a hole,
     not a check. Omitting it keeps the Phase 1 behavior (the singleton).
   - A re-invite of an *accepted* collaborator only updates the role. Forcing
     them back to `pending` (as the table above reads literally) would
     silently revoke a live collaborator's access.
   - `leave_conversation` sets `status="kicked"`, the same terminal state a
     kick produces — there is no separate `left` status, so the audit trail
     records that access ended, not who ended it.
   - Authorization closed several pre-existing holes that had nothing to do
     with sharing: `set_conv_title`, `conv_encrypt_*`, `relay_workspace_*`
     and `poll` all resolved a conversation by id alone, so any logged-in
     user knowing an id could rename someone else's conversation or change
     its encryption state. See the CHANGELOG Security entry.
   - **Still requester-partitioned, not collaborator-aware**: the actions in
     `_conv_ops.py` / `_conv_tags_export.py` / `_conv_import.py` (export,
     fork, tags, import). They pass the requester's `user_id` to storage, so
     they are safe today — a stranger simply resolves to a directory that
     does not exist — but a collaborator cannot export or fork a shared
     conversation either. Folded into the Phase 5 audit rather than done
     blind here.
4. **`flow_runtime_access.py` integration** — channel bridges (Telegram et
   al.) honor collaborators through the same primitive.
5. **`resource_store`/`server_relay_manager` audit** — enumerate and fix any
   remaining owner-only assumption for conversation-scoped reads, plus the
   `_conv_ops`/`_conv_tags_export`/`_conv_import` actions left
   requester-partitioned by Phase 3 (export, fork, tags, import).
6. **Owner reassignment** — directory-move-on-write path, tested with a
   deleted-owner fixture.
7. **Frontend** — sidebar split, invite/share dialogs, author badges,
   collaborators panel, i18n.

Each phase should land as its own reviewable change; 2 has no dependency on
1/3-7 and can ship first as a standalone security fix.

## Tests (mirror existing suites)

- `tests/test_conversation_store.py` — `resolve_owner()`, ACL read/write via
  `extra`, owner-reassignment directory move (including a concurrent-writer
  simulation under the conv lock).
- New `tests/test_conversation_sharing.py` — full lifecycle: invite → pending
  → accept → write access → role change → kick → access revoked; leave;
  reassignment on deleted owner; never-leak-existence for every rejected
  path.
- `tests/test_agent_runtime_api.py` / a new channel-bridge test — a
  collaborator submitting via `AgentRuntimeAPI` (simulating Telegram) reaches
  the shared conversation, a kicked user does not.
- SSE: extend whatever exercises `agent_sse_stream.py` today (or add one) —
  unauthorized `conversation_id` gets rejected before `subscribe()`; an
  accepted `read`-role collaborator receives events; a `read`-role
  collaborator's own message-submit is rejected.
- Frontend: structural assertions in `test_chat_ui_*` style for the new
  dialog markup, i18n key parity across en/fr/es, and author-badge rendering
  gated on `source.author_user_id`.

## Scope cuts for v1 (call out, don't silently drop)

- **No real-time invite push.** A pending invite becomes visible on the
  invitee's next conversation-list load/refresh, not instantly — no new
  cross-conversation, per-user notification channel is built in v1 (would
  require infrastructure beyond `ConversationEventBus`, which is scoped per
  conversation, not per user).
- **No proactive SSE teardown on kick.** A kicked collaborator's already-open
  EventSource keeps receiving events until it naturally reconnects (browser
  tab refresh, network blip) — acceptable since kicking someone whose tab
  stays open forever is an edge case, and the write path is closed
  immediately regardless.
- **No nested sharing / no collaborator-invites-collaborator.**
- **No per-collaborator relay/device reconciliation**: if the conversation's
  agents execute tool calls against a relay, that remains whichever relay is
  already attached to the conversation (owner's), not a collaborator's own
  connected relay — flagged as a real UX question ("whose filesystem does a
  collaborator's tool call touch?") deferred, since resolving it well likely
  needs a per-conversation relay picker independent of this feature.

## Risk summary

- **Biggest single risk**: missing a call site during the
  `resource_store`/`server_relay_manager` audit (Phase 5) that silently
  assumes owner-only access, either over-exposing a resource to a
  collaborator or under-exposing one and breaking a shared conversation's
  agent mid-turn. Mitigated by treating Phase 5 as its own reviewed pass with
  an explicit call-site inventory, not folded into Phase 3.
- **Encryption/unlock dependency**: collaborators inherit the owner's DEK
  unlock state; if the vault idle-locks, ALL participants lose access
  simultaneously, which may look like a bug to a collaborator unaware the
  conversation is encrypted. Needs a clear UI message, not silent 404s.
- **Owner-reassignment race**: two collaborators submitting near-simultaneously
  right after an owner deletion — mitigated by doing the check-then-rename
  under the existing per-conversation lock so exactly one wins.

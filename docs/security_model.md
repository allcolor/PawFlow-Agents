# Security Model

PawFlow is self-hosted, but agents can still perform high-impact actions. Security depends on provider configuration, relay mode, tool permissions, auth, and deployment boundaries.

## Trust Boundaries

| Boundary | What crosses it |
|---|---|
| Browser/client -> PawFlow server | Messages, auth cookies/tokens, file uploads, SSE subscriptions. |
| PawFlow server -> LLM provider | Prompts, selected context, tool schemas/results depending on provider. |
| PawFlow server -> relay | Tool requests for filesystem, shell, screen, desktop, and local resources. |
| Relay -> host or Docker container | File reads/writes, shell commands, screen actions. |
| PawFlow -> media providers | Prompts and source media URLs for image/video/audio/3D/voice operations. |

## Relay Modes

| Mode | Host access | Recommended for |
|---|---|---|
| Native/local relay | Full selected filesystem and optional shell/screen access | Personal trusted workflows. |
| Docker relay | Mounted project directory and container tools | Untrusted code execution and public demos. |
| Standalone client relay | User-selected filesystem/desktop through PawFlow Relay CLI/Desktop | Client machine workflows. |
| Managed server relay | PawFlow-managed runtime directory under `data/runtime/relay/` | Server-side workspaces without exposing arbitrary host paths. |

## Permission Modes

Agents should run with the least privilege needed:

- read-only: inspect but do not edit or run commands;
- approve-edits: require confirmation for modifications;
- auto: allow configured tools to run without repeated prompts;
- full/local desktop access: treat as privileged.

Use approval gates for shell, edit, delete, desktop, VNC, and external network operations.

**A new conversation is created in `auto`.** The mode is written once, at
creation, by the shared creation contract (`core/conversation_creation.py`) —
web chat, Telegram, and the flow API alike — and by the installer for the
first conversation it hands over. Conversations that already exist are
untouched: the mode is not a fallback the readers apply, so anything created
before this default keeps running under `default`. Change it per
conversation from the permission selector in the web chat, or with
`/permission default|approve_edits|read_only|auto`. On a deployment where
agents run against production systems or untrusted input, set the conversation
back to `default` or `read_only` before giving it work — `auto` approves every
tool the mode allows, without asking.

### What the approval decision applies to

An approval is only meaningful if the call that runs is the call that was
approved. Five properties enforce that, each covered by regression tests in
`tests/test_tool_call_security_ordering.py`:

- **Canonical before decided.** Arguments are decoded and wrapper tool names
  (`use_tool`) are resolved to the inner tool *before* the gate is consulted,
  on both the main and the sub-agent path. Arguments delivered as a JSON string
  used to reach the gate as no arguments at all, so the dangerous- and
  catastrophic-command scans inspected nothing while the registry decoded the
  same string and ran the real command.
- **Aliases inherit severity.** `shell`, `exec`, `run`, `terminal`,
  `run_command` and `execute` execute as `bash`, so they are classified as
  `bash` for approval and their command is scanned like a `bash` command.
  Escalation is one-way: an alias whose target is not in `ALWAYS_ASK` keeps its
  own classification, so nothing is tightened as a side effect.
- **Nothing rewrites an approved call.** A `pre_tool_call` hook may replace the
  tool name and arguments (`decision: "replace"`), and `$VAR` resolution
  rewrites values. Both run after the gate, so the call is re-authorized when —
  and only when — its name or arguments actually changed. An unchanged call
  never prompts twice.
- **Undecodable is refused, not emptied.** A payload that cannot be parsed is
  rejected with a diagnostic instead of degrading to `{}` and executing.
- **Every write target is inspected.** Protected-path checks include direct
  `path`/`file_path` values, each `batch_edit.edits[].path`, and every target in
  OpenAI or unified-diff `apply_patch` headers. A session or allow-all grant for
  the outer tool never covers a protected path hidden inside its payload.

One gap remains: filesystem handlers resolve the expression language
(`${scope.key}`) on their own arguments at handler entry, after the gate. A
path approved as literal text can therefore still resolve to a different
concrete target. The test covering this is marked `xfail` until argument
freezing lands.

## Identity, Groups and Roles

Authentication is delegated to the identity provider. PawFlow speaks generic
OAuth2/OIDC with presets for Keycloak, Okta, Auth0 and GitLab
(`services/auth_providers/generic_oauth.py`) -- there is no LDAP client and no
directory sync, on purpose: that is the IdP's job.

Groups from the IdP map to PawFlow roles under three rules, and each exists
because its opposite hands out admin by accident.

**1. An unmapped group grants nothing.** Authority is the mapping table an
operator wrote in PawFlow, never the group's name. Without this, creating a
group called `admin` in the IdP would be a privilege escalation.

```json
{ "field_groups": "realm_access.roles",
  "group_mappings": { "pawflow-admins": "admin" },
  "auto_provision": true }
```

`field_groups` supports dotted paths because the interesting claim usually is
one: Keycloak puts realm roles in `realm_access.roles`.

**2. Local wins by default.** `auth.role_precedence` in
`global_parameters.json` is `local` (default) or `remote`. Making the IdP
authoritative is a deliberate choice. In `remote` mode, an identity that
returns *no* mapped group keeps its stored role rather than being demoted --
otherwise one forgotten scope demotes every user at once. A demotion that would
leave no enabled admin is refused outright and logged: once the last admin is
gone there is no route left in the UI to undo it.

**3. Group names never reach `http.auth.roles`.** That attribute carries
resolved PawFlow roles only (`admin` / `user`). Around 29 call sites test it
with `"admin" in roles` -- a SUBSTRING test, which a group named
`admin-readonly` or `non-admin` would satisfy. IdP group names travel in
`http.auth.groups`, for display and audit, and are never consulted for
authorisation. `core.admin_scope.is_admin` matches exact membership so the gate
stays correct even if that separation is ever broken upstream.

**Auto-provisioning** requires BOTH an explicit `auto_provision` on the
provider AND at least one mapped group. Otherwise an unknown identity still
needs an admin-issued onboarding token.

**Operator note.** Keycloak does NOT emit roles in `userinfo` until a
group/roles mapper is added to the client scope. Without it the feature is
correct and appears to do nothing; PawFlow logs a warning naming the claim it
looked for.

## Desktop and VNC Risk

`/desktop local` and `screen(local=true)` can act on the user's real desktop. This is equivalent to allowing an agent to see the screen and operate mouse/keyboard. Prefer Docker desktop unless local control is specifically required.

## Media and Voice Risk

Media tools may send prompts, source images, videos, audio, or voice samples to external providers. Voice clone tools must only be used with samples the user is allowed to clone. Store provider API keys as secrets and document provider data-retention policies separately if deploying for teams.

### Realtime LiveKit token scoping

Realtime sessions on the `livekit` engine never expose provider or LiveKit API secrets to browsers. `POST /api/realtime/livekit/start` (session-authenticated, conversation-owner-only) returns a LiveKit **room token** only: JWT signed with the LiveKit API secret, scoped to one room/one session, TTL `min(max_session_seconds + 60s, 15 min)`, minimum grants (join, publish own mic — plus camera/screen only when `video_input` is enabled — subscribe; never `roomAdmin`). The sidecar worker gets two separate credentials: an agent room token, and a **worker-control token** (PawFlow-signed via a SecretsManager subkey, audience-bound, scoped to one session/conversation/agent) required to open `/ws/realtime-worker/{session_id}` — the route is public but fails closed on any token mismatch, and a room token can never be used as a control token. Force-stop sends `shutdown` on the control channel, closes it, and removes the session; a leaked room token may stay cryptographically valid until TTL expiry, which is why the TTL is short and stopped sessions reject further server-side work.

## Secrets

Use PawFlow secret storage or environment variables for API keys. Never hard-code secrets in flows, agent prompts, or docs. When writing examples, use `${SECRET_NAME}` placeholders.

The master key encrypts stored secrets with AEAD (AES-GCM). Resolution order: `PAWFLOW_SECRET_KEY_B64` (raw 32-byte key, preferred), `PAWFLOW_SECRET_KEY` (password, derived via scrypt), then the dev-only generated on-disk key file. When a password is used, the scrypt salt is per-install: a fresh install writes a random salt to `data/system/secret.salt` before the first secret is encrypted, so two installs sharing a password never share a key. Existing installs (no salt file) keep the legacy salt so secrets stay decryptable across upgrades. To pin a salt explicitly (e.g. password-based deployments that predate the salt file), set `PAWFLOW_SECRET_SALT_B64` to a base64 value of at least 16 bytes.

## Encryption at Rest

Opt-in, per-conversation encryption at rest, independent of the master key above
(which protects config secrets). Threat model: **T1 — disk at rest**. The
guarantee: with the server stopped, every encrypted conversation/workspace is
ciphertext on disk and no key is in memory.

- **What is encrypted**: conversation content fields (message text, thinking,
  tool arguments and results). Metadata (ids, timestamps, ordering, roles) stays
  clear so the store, restart-from, and git history keep working without the key.
- **Keys**: a random per-conversation DEK encrypts the content; the DEK is
  wrapped by a passphrase (scrypt + AES-GCM), and optionally by a recovery
  (escrow) passphrase and/or a trusted key-relay public key (X25519 sealed-box).
  DEKs live in a RAM-only, session-bound vault — zeroised on lock, purged on
  logout, idle-locked after 15 minutes, and gone on server restart.
- **No recovery**: losing the passphrase with no escrow/relay wrap means the
  data is permanently unrecoverable — surfaced loudly when enabling.
- **Trusted key-relay** (optional): a relay holding an X25519 keypair can
  auto-unlock bound conversations while connected; the server seals the DEK to
  the relay public key and never holds a key that opens that wrap. When the
  relay disconnects, the delivered DEKs are purged (relay-gone = re-locked).
- **Workspace encryption**: a conv-scoped server relay workspace can be stored
  as a CryFS cipher-store, mounted with a DEK delivered over the relay control
  channel. Restricted to conv-scoped relays.
- **Not E2EE / not T2**: the server processes plaintext in RAM to drive the
  models, so it does not defend a live-root attacker on a running server.

Strictly opt-in: conversations without encryption enabled are byte-for-byte
unchanged. Commands: `/encrypt` (conversation) and `/relay encrypt|unlock`
(workspace); relay key provisioning via `pawflow-relay key ...`. See the
[design RFC](design/encryption-at-rest.md).

## Packages (PFP)

A `.pfp` is signed with an ed25519 key whose public half is embedded in the manifest, so the signature proves the package is internally consistent but not who authored it. PawFlow pins the developer key on first install (trust-on-first-use): an update to an already-installed package name signed by a different key is refused unless installed with `force=True`. This blocks a compromised or hijacked registry from shipping a malicious update under an existing package's name.

### Workflow agents

Workflow agents are disabled unless the server sets
`PAWFLOW_WORKFLOW_AGENTS_ENABLED=1`; request data cannot enable the runtime.
Bindings pin an exact flow version, resolved scope and digest plus immutable
service and authorization snapshots. Every task's declared effects are
intersected with the flow contract, conversation permission mode, authenticated
authority revision, and current resource targets before execution. Recovery
reuses that accepted authority and fails closed when its identity or revision is
no longer valid.

The runtime accepts only the closed workflow-safe task catalog and rejects
unbounded cycles, arbitrary scripts/sources, nested agents, undeclared ports,
unreachable terminals, open-world effects, and mismatched package capability
metadata. Inbox messages are leased rather than destructively drained, and only
turn IDs named by the validated terminal can be acknowledged.

Inspector and operations APIs are conversation-scoped and return redacted
projections: no requests, inbox payloads, prompts, source bodies, credentials,
or service snapshots. Safe retry is limited to the current recoverable
generation. The `silent_maintenance` invocation mode and Wiki scheduler
cutover are server-owned; a client cannot suppress transcript or terminal
delivery by setting request fields.

### Declarative workflow and PlanStore cutover

Declarative layouts, lowering, FlowRuns, proposals, and migration are controlled
by independent server environment flags; all default to disabled and reject
invalid boolean values. Enabling WorkflowProposal cutover makes canonical and
legacy writers mutually exclusive: all 18 legacy PlanStore actions fail with
404 before PlanStore is opened, and legacy Web scripts and listeners are not
rendered.

Migration imports are provenance-pinned, idempotent, and never emit live
terminal events. Rollback restores exact backed-up bytes only before the first
canonical live proposal/run mutation. Every live store mutation first marks
`first_write_at` on all active manifests; rollback then fails closed. See the
[PlanStore Migration Runbook](PLANSTORE_MIGRATION_RUNBOOK.md).

## Private Gateway

The private gateway is configured as a `privateGateway` service and enabled for a listener through `httpListener.private_gateway_service_id`. Accepted challenge keys are explicit `secret_refs` on that service. HTTP and WebSocket clients may present a matching key in `X-PawFlow-Gateway-Key`; browser sessions continue to use the challenge cookie. The challenge skin is selected by the service `skin` field and resolved from repository resources under `data/repository/private_gateway_skin`. Each skin lives in a directory containing `skin.json` metadata and `template.html`; templates can use `{{ next_url }}`, `{{ error }}`, and `{{ cooldown }}` placeholders.

## Production Checklist

- Run with `PAWFLOW_PUBLIC_MODE=true` or `PAWFLOW_ENV=production` so unsafe boot settings become fatal.
- Set a strong `PAWFLOW_SECRET_KEY_B64` (preferred) or `PAWFLOW_SECRET_KEY`; do not rely on the dev-only on-disk fallback.
- Put PawFlow behind HTTPS / a trusted reverse proxy for public access.
- Enable the private gateway for internet-facing demos.
- Run untrusted workloads in Docker relay mode.
- Avoid local desktop mode for public demos.
- Configure per-agent tool restrictions.
- Set LLM budget caps.
- Keep workflow agents disabled until their exact flow, service bindings,
  migration, shadow comparison, monitoring, and recovery gates are validated.
- Review OAuth redirect URLs and provider scopes.
- For sensitive conversations, enable opt-in encryption at rest (`/encrypt on`) and store the passphrase safely (no recovery without an escrow/relay wrap).

# Encryption at rest — conversations & server relay workspaces

> Status: **DESIGN / RFC** — no code yet. Decisions are marked **[Decided]**;
> recommendations not yet ratified are marked **[Proposed]**.
> Owner discussion: 2026-06-13.

## 1. Goal & threat model

Opt-in, **per-conversation** at-rest encryption of all conversation-derived
data, plus at-rest encryption of **server-managed relay workspaces**, such that
the plaintext is never recoverable from the disk alone.

### Threat model: **T1 — disk at rest** [Decided]

Defended: an actor who obtains the on-disk bytes (stolen disk, backup snapshot,
cold host, a host admin browsing `data/` while the server is **not** holding the
key) sees only ciphertext.

**Out of scope (accepted risk): T2 — live root.** A root user on the *running*
host can read process memory (`/proc/<pid>/mem`), env (`/proc/<pid>/environ`),
`docker exec` into a relay container, and read the decrypted FUSE view while it
is mounted. No classic at-rest scheme defends plaintext-in-use without a TEE.
The owner explicitly accepts this. The design therefore optimizes for: **the key
is never persisted on the host and lives only in server RAM while actively in
use.**

### Consequence that drives everything

The key must never touch host disk in plaintext. So:
- `data/config/secret.key` (the existing default master-key file) is **not** used
  for this feature.
- The key is supplied at use-time from outside the host and held **RAM-only**:
  - a **passphrase** typed by the user when opening an encrypted conversation, or
  - a **trusted key-relay** (a relay on a machine the host admin does not
    control) that pushes the key over the authenticated channel.

## 2. Key hierarchy [Decided]

Never encrypt data directly with the supplied secret. Two layers:

- **KEK (key-encryption key)** — derived from the passphrase (`scrypt`) or
  delivered by the key-relay. RAM-only, never serialized.
- **DEK (data-encryption key)** — random 32 bytes per conversation (and per
  workspace). Encrypts the actual data via AEAD. Stored on disk **wrapped** by
  the KEK.

```
passphrase --scrypt(salt)--> KEK ┐
trusted relay key --------------> KEK ┼--> unwrap(wrap_*) --> DEK --> AEAD(data)
escrow key (optional) ----------> KEK ┘
```

The DEK may carry **multiple independent wraps** stored side by side:
`wrap_pass`, `wrap_relay`, `wrap_escrow`. Same ciphertext, several doors. This
is what reconciles "passphrase on open" with "relay supplies the key for
background" — both unwrap the same DEK.

Benefits: changing a passphrase re-wraps the DEK only (no re-encrypting 238k
messages); rotation/revocation is cheap; reuses the existing AEAD primitive in
`core/secrets.py` (AES-GCM / ChaCha20-Poly1305, keyring, `enc:v2:` format). We
add **RAM-only custody** and **delivery transports**, not new crypto.

The AEAD auth tag **is** the passphrase verifier: a wrong passphrase fails the
unwrap; we return "wrong password" and never reveal anything.

## 3. Scope — what must be encrypted

Conversation content leaks across the whole derived-runtime tree. Encrypting
only `transcript.jsonl` would be cosmetic. Full per-conversation scope:

| Surface | Path | Reader | Treatment |
|---|---|---|---|
| Transcript / shared / per-agent context | `data/runtime/conversations/<u>/<c>/*.jsonl` | server | per-line field encryption (§4) |
| Conversation git history | `…/<c>/.git` (`_git_snapshot_files`) | server | versions ciphertext rows automatically (§4) |
| Extras / bindings | `…/<c>/extras.json`, `bindings.json` | server | encrypt sensitive values |
| FileStore attachments | `data/runtime/files/<u>/<c>/` | server | encrypt blob bytes with conv DEK |
| **CLI sessions** | `data/runtime/sessions/{claude,codex,gemini}/` | server **+ relay (FUSE `/cc_sessions`)** | **same DEK as the conversation** [Decided] |
| Memories / KG / plans | `runtime/{memories,knowledge_graphs,plans}` | server | encrypt with conv (or user) DEK |
| AST cache / spill | `runtime/{graphs,spill}` | server | encrypt with source DEK, or make cold-purgeable |
| **Relay workspace** | `data/runtime/relay/<u>/<c>/` → `/workspace` | **relay container** | encrypted FS image (§6) |

**CLI sessions are the sneakiest leak** [Decided to include]: they copy the
transcript to disk in cleartext *and* are FUSE-mounted into the relay. They use
the conversation's DEK.

## 4. Conversation encryption mechanics

### Line-level field encryption [Decided]

JSONL rows are stored with **sensitive fields encrypted, metadata in clear**:

- **Encrypted** (`enc:` blobs): `content`, `text`, `thinking`, tool-call
  `arguments`, tool-result payloads, attachment data.
- **Clear**: `ts`, `msg_id`, `role`, `source` (incl. `source.name`),
  `tool_call_id`, structural ids.

Rationale [Decided]: clear metadata leaks *who spoke and when*, not *what* —
acceptable for T1 as long as **content is encrypted**. It preserves cheap
replay, indexing, dedup, and lets git diff/retention keep working (it versions
ciphertext fields). If zero-metadata is ever required, switch to whole-row
encryption + a separate encrypted index (heavier — noted, not chosen).

### Git

Because the sensitive fields are already ciphertext before they are written,
`conversation_git` snapshots and history contain only ciphertext for those
fields. No change to the git mechanism itself.

### Performance note

Replaying a very large conversation decrypts one blob per sensitive field on
load. Measure on the largest conversations; rely on the existing per-agent
context cache to avoid repeated decrypts.

## 5. Lifecycle: lock / unlock, and DEK custody [Decided]

A `KeyVault` (RAM-only, never serialized; `mlock` if available) holds unwrapped
DEKs keyed by `(user_id, conv_id, session_id)`.

| State | KEK/DEK in RAM | Encrypted conv | Workspace |
|---|---|---|---|
| **Unlocked** | yes | read/write | decrypted view mounted for the container |
| **Locked** (no pass entered / relay absent / server restart) | no | inaccessible (no history, no resume, no cron) | ciphertext only; container cannot start |

### DEK is bound to a valid user session [Decided]

Unlock requires a valid `SecurityManager` session (`sm.get_session(token)`).
Eviction at the **earlier** of two clocks:
1. **Session invalidated** (logout / expiry / revocation) → purge that session's
   DEKs immediately.
2. **Idle-lock** — shorter inactivity timer (see open decision; **[Proposed]**
   15 min) independent of session lifetime.

Multi-device: each session unlocks its own DEK entry (re-prompt per session).

### Background / cron on a locked conversation [Decided]

- If a **trusted key-relay** is configured for the conversation → call it to
  obtain the DEK (via `wrap_relay`) and run unattended.
- Otherwise → **pause** background/scheduled work until the next interactive
  unlock.

Clean split: **interactive = passphrase, session-bound + idle-lock**;
**autonomous = relay-bound** (`wrap_relay`).

## 6. Server relay workspace encryption

Today `data/runtime/relay/<u>/<c>/` is bind-mounted cleartext into the container
as `/workspace` (`core/server_relay_manager.py`). The container already runs
with `SYS_ADMIN` + `/dev/fuse` for its existing FUSE sister-mounts.

### Design [Decided shape; format Proposed]

1. The host holds an **opaque encrypted cipher-store** instead of cleartext
   workspace files.
2. At spawn, the cleartext bind-mount is **removed**; the cipher-store
   (ciphertext) is bind-mounted and the **DEK is delivered into the relay
   process over the authenticated WS control channel** — **never via `--env`**
   (env is readable by host root via `/proc` and `docker inspect`; note the
   current `PAWFLOW_RELAY_TOKEN=…` is passed in env — the DEK must not follow
   that path).
3. The relay launcher **mounts the decrypted FS itself** (FUSE) at `/workspace`.
   Tools read/write transparently; the host disk only ever holds ciphertext.
4. Container stopped → nothing mounted → host sees only the blob. **T1 met.**

### Filesystem format — **[Proposed] CryFS**

| Option | Hides structure? | Privilege | Verdict |
|---|---|---|---|
| **CryFS** | **yes** — fixed-size blocks hide names/sizes/count | FUSE only ✅ | **recommended** — matches "admin sees nothing" |
| gocryptfs | no — leaks file count/sizes/tree | FUSE only ✅ | simpler/faster; only if perf demands |
| LUKS/dm-crypt loopback | yes (block) | loop + dm-crypt, host-global ❌ | avoid in a sibling container |

### Key source for the workspace

No "open conversation" event drives it, so:
- **Interactive**: prompt the passphrase when the user opens the terminal /
  desktop for an encrypted workspace.
- **Headless / background build**: `wrap_relay`.

The workspace DEK is wrapped the same way (`wrap_pass` + `wrap_relay`) and the
wraps live on the host beside the cipher-store.

### T2 caveat

A server-managed workspace relay runs **on the host**, so the DEK and decrypted
`/workspace` live in a host container while mounted → live root can read them
(accepted T2). Cold (container stopped) it is inviolable. To close T2 for a
workspace, run it on a **local relay** (user's own machine), not server-managed.

## 7. UX surface — screens, UI functions, slash commands

Mirrors existing conventions: left-panel conversation controls
(Expiration / Theme), the Relays panel + relay info dialog, `fireAction(...)`
server actions, and the `/relay [sub] [id]` command shape.

### 7.1 Conversation encryption

**Screens / dialogs**

1. **Conversation settings** — new "Encryption" control in the left panel, next
   to Expiration / Theme: a toggle **Encrypt this conversation** with current
   state (`Off` / `Locked 🔒` / `Unlocked 🔓`).
2. **Set-passphrase modal** (on enabling): passphrase + confirm, a **mandatory
   "no recovery — if you lose this passphrase the data is unrecoverable"**
   warning, and an optional **"Add recovery (escrow)"** checkbox.
3. **Unlock modal** (on opening a 🔒 conversation, or `/encrypt unlock`):
   single passphrase field; wrong passphrase → inline "wrong password" (AEAD
   tag failure), no lockout reveal.
4. **Conversation-list indicator**: 🔒/🔓 badge on each encrypted conversation
   row (the list already renders per-conv badges).
5. **Locked banner** in the conversation view: "This conversation is encrypted —
   enter passphrase to view history" with an Unlock button. History/compose
   stay disabled until unlocked.
6. **Migration progress** (enabling on an existing conversation): reuse the
   service-install progress pattern (background, resumable, progress bar) while
   rows are rewritten as `enc:` blobs and the git history is re-packed.
7. **Key-relay binding**: a "Trusted key-relay" picker in the encryption
   settings to designate which relay may auto-unlock for background work
   (writes `wrap_relay`).

**UI functions (server actions, `fireAction`)** — handlers in
`tasks/ai/actions/conversation.py` / `service_flow.py`; passphrase travels over
the TLS WS and is **never logged or persisted**:

| Action | Effect |
|---|---|
| `conv_encrypt_status` | return `{state, has_pass_wrap, has_relay_wrap, has_escrow}` |
| `conv_encrypt_enable` | set passphrase, generate DEK, write `wrap_pass`, start migration |
| `conv_encrypt_unlock` | derive KEK, unwrap DEK into the session vault |
| `conv_encrypt_lock` | drop the DEK from RAM now |
| `conv_encrypt_disable` | requires unlock; decrypt in place, remove wraps |
| `conv_encrypt_passwd` | re-wrap DEK under a new passphrase |
| `conv_encrypt_set_relay` | add/remove `wrap_relay` for a chosen relay |
| `conv_encrypt_set_escrow` | add/remove optional recovery wrap |

**Slash commands** — new `/encrypt`, same style as `/relay`:

```
/encrypt                     — show encryption status of this conversation
/encrypt on                  — enable (opens set-passphrase modal)
/encrypt off                 — disable (requires unlock)
/encrypt unlock              — provide passphrase to decrypt this conversation
/encrypt lock                — drop the key from RAM now (re-lock)
/encrypt passwd              — change the passphrase (re-wrap)
/encrypt relay <relay_id>    — designate a trusted key-relay for unattended unlock
/encrypt escrow <on|off>     — manage optional recovery wrap
```

(Passphrase entry from a slash command still routes through the modal rather
than echoing the secret in the chat input.)

### 7.2 Server relay workspace encryption

**Screens / dialogs**

1. **Relay info dialog** (existing `_showRelayInfoDialog`): add an **"Encrypt
   workspace"** toggle + state line (`Encrypted 🔒 / Off`).
2. **Set/Unlock passphrase modal** for the workspace (same component as the
   conversation one), shown when encrypting or when opening the terminal /
   desktop of an encrypted, still-locked workspace.
3. **Relays-panel badge**: extend the tri-state dot with a 🔒 overlay when the
   workspace is encrypted-and-locked (no DEK → container cannot start).

**UI functions (server actions)** — handlers in `service_flow.py` /
`agent_resource.py`:

| Action | Effect |
|---|---|
| `relay_workspace_encrypt` | enable encryption: create cipher-store, migrate current files, write wraps |
| `relay_workspace_unlock` | unwrap workspace DEK into RAM so the container can mount it |
| `relay_workspace_lock` | unmount + drop DEK |
| `relay_workspace_encrypt_off` | decrypt in place, revert to cleartext bind-mount |

**Slash command** — extend `/relay`:

```
/relay encrypt <id> on|off   — turn workspace encryption on/off for a server relay
/relay unlock <id>           — provide passphrase to mount an encrypted workspace
```

### 7.3 i18n

Add keys to `tasks/io/chat_ui/i18n/{en,fr,es}.json`: `encryption`,
`encryptConversation`, `enterPassphrase`, `setPassphrase`, `confirmPassphrase`,
`wrongPassphrase`, `noRecoveryWarning`, `addRecovery`, `locked`, `unlocked`,
`encryptWorkspace`, `trustedKeyRelay`, `encryptionMigrating`. Reuse existing
`starting` etc. where applicable.

## 8. Open decisions

| # | Decision | Recommendation | Status |
|---|---|---|---|
| 1 | Workspace FS format | CryFS (hides metadata, FUSE-only) | **[Proposed]** |
| 2 | Idle-lock duration | 15 min + purge on session invalidation | **[Proposed]** |
| 3 | JSONL granularity | encrypt content fields, metadata clear | **[Decided]** |
| 4 | Recovery escrow | optional, explicit opt-in 3rd wrap | **[Proposed]** |

## 9. Non-goals / explicit caveats

- Not E2EE: the server processes plaintext to drive the models — incompatible
  with the server never seeing content.
- Does not defend T2 (live root) — accepted.
- A lost passphrase with no `wrap_relay`/`wrap_escrow` means **permanent data
  loss** — surfaced loudly in the enable flow.
- Server restart re-locks every encrypted conversation until re-unlocked
  (interactive) or a trusted key-relay reconnects (background).

## 10. Suggested phasing (post-approval)

1. `KeyVault` (RAM, session-bound) + KEK/DEK + multi-wrap, on top of
   `core/secrets.py`.
2. Conversation line-level field encryption + git-compatible read/write +
   migration job.
3. CLI-sessions / FileStore / derived caches under the same DEK.
4. Passphrase UI (modals, indicators, `/encrypt`) + session binding + idle-lock.
5. Key-relay transport (`wrap_relay`) + background/cron gating.
6. Workspace CryFS cipher-store + relay-side mount + DEK over WS control channel
   + relay UI/`/relay encrypt`.
7. Optional escrow.

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

## Desktop and VNC Risk

`/desktop local` and `screen(local=true)` can act on the user's real desktop. This is equivalent to allowing an agent to see the screen and operate mouse/keyboard. Prefer Docker desktop unless local control is specifically required.

## Media and Voice Risk

Media tools may send prompts, source images, videos, audio, or voice samples to external providers. Voice clone tools must only be used with samples the user is allowed to clone. Store provider API keys as secrets and document provider data-retention policies separately if deploying for teams.

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

## Private Gateway

The private gateway is configured as a `privateGateway` service and enabled for a listener through `httpListener.private_gateway_service_id`. Accepted challenge keys are explicit `secret_refs` on that service. The challenge skin is selected by the service `skin` field and resolved from repository resources under `data/repository/private_gateway_skin`. Each skin lives in a directory containing `skin.json` metadata and `template.html`; templates can use `{{ next_url }}`, `{{ error }}`, and `{{ cooldown }}` placeholders.

## Production Checklist

- Run with `PAWFLOW_PUBLIC_MODE=true` or `PAWFLOW_ENV=production` so unsafe boot settings become fatal.
- Set a strong `PAWFLOW_SECRET_KEY_B64` (preferred) or `PAWFLOW_SECRET_KEY`; do not rely on the dev-only on-disk fallback.
- Put PawFlow behind HTTPS / a trusted reverse proxy for public access.
- Enable the private gateway for internet-facing demos.
- Run untrusted workloads in Docker relay mode.
- Avoid local desktop mode for public demos.
- Configure per-agent tool restrictions.
- Set LLM budget caps.
- Review OAuth redirect URLs and provider scopes.
- For sensitive conversations, enable opt-in encryption at rest (`/encrypt on`) and store the passphrase safely (no recovery without an escrow/relay wrap).

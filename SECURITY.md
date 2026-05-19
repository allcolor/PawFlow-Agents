# PawFlow security model

This document describes the security primitives PawFlow relies on for
multi-user / public deployments. Every claim here is backed by code in
`core/` and `services/` and by tests under `tests/test_*_security_*`.
If you find a divergence, the code wins.

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.
Use either of the following private channels:

- **GitHub Security Advisories** — preferred. Open a private advisory at
  <https://github.com/allcolor/PawFlow-Agents/security/advisories/new>.
- **Email** — <pawflow-support@allcolor.org>.

Include reproduction steps, affected version (`pawflow --version`), and any
proof-of-concept. We aim to acknowledge reports within 72 hours.

## Layered defences

```
  Internet  ─→  [private gateway]  ─→  [auth: cookie / API key]  ─→  [capability tokens]  ─→  resource
              public-mode bouncer       SecurityManager session         per-route signed bind
```

Each layer is independent: removing one downgrades the deployment but
does not silently weaken the others.

## HTTP hardening and global rate limits

The shared HTTP listener adds security headers to every normal response:
`Content-Security-Policy`, `X-Content-Type-Options`, `X-Frame-Options`,
`Referrer-Policy`, and `Permissions-Policy`. The CSP remains compatible with
PawFlow's current inline webchat scripts/styles while blocking object embeds
and restricting frame ancestors to same-origin.

A process-local sliding-window limiter protects login/gateway routes and API
routes before route dispatch. This is separate from capability-token failure
limits: capability limits protect guessed resource URLs, while the listener
limit protects global login/API pressure.

## Capability tokens (per-route)

- Module: `core/capability_auth.py` (storage), `core/capability_routes.py` (HTTP/WS adapters).
- Issued at session-register time (`register_session`, `register_terminal`,
  `register_code_server`, `add_forward`, `register_audio_source`).
- Bound to `(resource_type, resource_id, user_id, login_session_id)`, and
  also `conversation_id` when the caller supplies one. Browser routes do
  not rely on the URL carrying a conversation id; their effective boundary
  is user + login session + resource + token. A token minted for `vnc` does
  NOT verify against `terminal`, even with the same resource_id.
- Persisted in `data/runtime/capabilities.json` (atomic JSON store; SQLite
  was avoided because WSL/SMB byte-range locking was unreliable). Active
  VNC / terminal / code-server / port-forward sessions survive a server
  restart — the same URL keeps working.
- TTL bound to the user's login session by default. `SecurityManager.logout`
  calls `revoke_session_capabilities(session_id)` so a leaked URL stops
  working the moment the user logs out.
- Per-IP rate limit (20 verify failures / 60s) on bad tokens.
- All sensitive URLs follow the shape `/<resource>/<resource_id>/<token>/...`.

## Read-only mode (`tool_relay_service`)

- The `read_only` permission mode is an **allowlist**, not a blocklist:
  any tool not classified as read-only safe is denied. New tools are
  denied by default until classified explicitly.
- The classification lives on `ToolApprovalGate.is_read_only_allowed`
  so the relay and other gates share one source of truth.

## Approval gating

- Default: `default` mode — EXEMPT tools auto-approve, ALWAYS_ASK
  tools always prompt, anything in between asks once per session.
- Production: when the SSE dialog cannot be shown,
  `ToolApprovalGate.check` returns `denied` (fail-closed). In development,
  `PAWFLOW_APPROVAL_FAIL_OPEN=true` can be used to allow non-critical tools
  during UI/event-bus debugging. ALWAYS_ASK tools (bash, store_secret,
  screen, ...) stay denied even when fail-open is on.

## Secrets at rest

- Format: `enc:v2:<base64>` (string) or `b"PFSEC2\0" + payload`
  (sidecar bytes). AEAD via `cryptography.AESGCM` (default) or
  `ChaCha20Poly1305`.
- Master key resolution: `PAWFLOW_SECRET_KEY_B64` (raw 32 bytes,
  preferred) → `PAWFLOW_SECRET_KEY` (password, scrypt-derived) →
  generated `data/config/secret.key` (chmod 0600, dev only —
  production refuses to boot with this fallback).
- Key rotation: `add_key(kid, key)` then `set_current(kid)` writes
  subsequent payloads under the selected kid.
- Failure mode: AEAD-auth failure raises `SecretDecryptError`.
  PawFlow never silently returns the ciphertext as a fallback.

## Production mode

Set either `PAWFLOW_ENV=production` or `PAWFLOW_PUBLIC_MODE=true`. At
boot, `core.security_report.enforce` will:

1. Print a security snapshot to the log.
2. Refuse to boot if any of these is true:
   - master key falls back to the on-disk `data/config/secret.key`,
   - `PAWFLOW_APPROVAL_FAIL_OPEN` is set,
3. Emit a warning if the HTTP listener binds to `0.0.0.0` (so the
   operator must confirm the firewall / reverse proxy posture).

## Threat model (summary)

| Attacker | Defended by |
|----------|-------------|
| Anonymous internet peer | private gateway cookie (`_pf_gw`) + auth |
| Authenticated user A snooping user B | per-route capability tokens (resource_type + user_id + login-session/resource binding; conversation binding when available) |
| Authenticated user A re-using their own URL on user B's resource | capability mismatch — verify rejects 403 |
| Brute-force token guesser | 256-bit random token + per-IP rate limit |
| Compromised user session (stolen cookie) | capabilities issued for that login session expire / revoke at logout |
| Compromised secrets file | AEAD authenticated decrypt; tampering is detected |
| Untrusted tool author | `read_only` mode (allowlist), approval gate (always-ask for bash/screen/store_secret/…) |
| Operator misconfiguration in prod | startup security report blocks the boot on weak-key / fail-open |

## Routes covered

| Resource | URL shape | Backing store |
|----------|-----------|---------------|
| VNC | `/vnc/<session_id>/<token>/...` | `services/vnc_proxy.py` |
| Audio | `/audio/<session_id>/<token>/stream` | `services/audio_proxy.py` |
| Terminal | `/terminal/<session_id>/<token>` | `services/terminal_proxy.py` |
| Code-Server | `/code/<session_id>/<token>/...` | `services/code_server_proxy.py` |
| Port-Forward | `/fwd/<forward_id>/<token>/...` | `services/port_forward_proxy.py` |
| Tool relay (WS) | `/ws/tools/_tool_relay` | `services/tool_relay_service.py` |

## Tests

- `tests/test_capability_auth.py` — issue / verify / revoke / persistence / rate limit.
- `tests/test_route_security_matrix.py` — cross-user, expired, revoked, forge-cross-resource.
- `tests/test_tool_approval_phase6.py` — read_only allowlist + fail-closed approval.
- `tests/test_secrets_v2.py` — AEAD roundtrip, key rotation, tamper detection.
- `tests/test_security_report.py` — production-boot policy.
- `tests/test_user_services.py` — end-to-end resource lifecycle.

Run the full security suite:

```
python -m pytest tests/test_capability_auth.py \
                 tests/test_route_security_matrix.py \
                 tests/test_tool_approval_phase6.py \
                 tests/test_secrets_v2.py \
                 tests/test_security_report.py
```

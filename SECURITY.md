# PawFlow security model

This document describes the security primitives PawFlow relies on for
multi-user / public deployments. Every claim here is backed by code in
`core/` and `services/` and by tests under `tests/test_*_security_*`.
If you find a divergence, the code wins.

## Layered defences

```
  Internet  ─→  [private gateway]  ─→  [auth: cookie / API key]  ─→  [capability tokens]  ─→  resource
              public-mode bouncer       SecurityManager session         per-route signed bind
```

Each layer is independent: removing one downgrades the deployment but
does not silently weaken the others.

## Capability tokens (per-route)

- Module: `core/capability_auth.py` (storage), `core/capability_routes.py` (HTTP/WS adapters).
- Issued at session-register time (`register_session`, `register_terminal`,
  `register_code_server`, `add_forward`, `register_audio_source`).
- Bound to `(resource_type, resource_id, user_id, conversation_id, login_session_id)`.
  Verification rejects on any mismatch — a token minted for `vnc` does
  NOT verify against `terminal`, even with the same resource_id.
- Persisted in `data/runtime/capabilities.db` (SQLite). Active VNC /
  terminal / code-server / port-forward sessions survive a server
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
  `ToolApprovalGate.check` returns `denied` (fail-closed). Set
  `PAWFLOW_APPROVAL_FAIL_OPEN=true` in dev to keep the historical
  auto-approve-on-bus-failure behaviour. ALWAYS_ASK tools (bash,
  store_secret, screen, ...) stay denied even when fail-open is on.

## Secrets at rest

- New format: `enc:v2:<base64>` (string) or `b"PFSEC2\0" + payload`
  (sidecar bytes). AEAD via `cryptography.AESGCM` (default) or
  `ChaCha20Poly1305`.
- Master key resolution: `PAWFLOW_SECRET_KEY_B64` (raw 32 bytes,
  preferred) → `PAWFLOW_SECRET_KEY` (password, scrypt-derived) →
  generated `data/config/secret.key` (chmod 0600, dev only —
  production refuses to boot with this fallback).
- Key rotation: `add_key(kid, key)` then `set_current(kid)` rewrites
  every new payload under the new kid; old kids stay readable.
- Legacy compatibility: pre-v2 `enc:<base64>` payloads still decrypt
  (read-only) when the password matches. `encrypt()` always emits v2.
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
| Authenticated user A snooping user B | per-route capability tokens (resource_type + user_id + conversation_id binding) |
| Authenticated user A re-using their own URL on user B's resource | capability mismatch — verify rejects 403 |
| Brute-force token guesser | 256-bit random token + per-IP rate limit |
| Compromised user session (stolen cookie) | capabilities issued for that login session expire / revoke at logout |
| Compromised secrets file | AEAD authenticated decrypt; tampering is detected; legacy payloads still readable |
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
- `tests/test_secrets_v2.py` — AEAD roundtrip, legacy compat, key rotation, tamper detection.
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

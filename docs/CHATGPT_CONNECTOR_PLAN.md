# ChatGPT Connector Plan — Phase A (URL-key) → Phase B (OAuth 2.1)

Status: Phase A implemented on 2026-08-16 (WP-A1..A5, tests green); WP-A6
live validation against ChatGPT pending. Phase B not started.

## 1. Goal

Let ChatGPT web (developer-mode custom connectors) call a published PawFlow
conversation as a remote MCP server, with the smallest possible first
increment (Phase A), then migrate to a spec-compliant OAuth 2.1 authorization
server (Phase B) once real usage is validated. Phase A is explicitly
throw-away at the auth layer: per the zero-backward-compatibility convention,
the URL-key mechanism is deleted in one shot when Phase B ships.

## 2. Constraints (verified 2026-08-16)

- ChatGPT custom connectors (Settings → Apps → Advanced → Developer mode,
  then Settings → Connectors → Create) accept **Streamable HTTP or SSE**
  transports on a **publicly reachable HTTPS** endpoint.
- Authentication choices are **OAuth (with dynamic client registration) or
  none**. There is no API-key/custom-header option, so the existing
  `Authorization: Bearer pfmcp_…` scheme cannot be used as-is.
- Available on Plus/Pro/Business/Enterprise/Edu; managed workspaces need an
  admin to allow custom MCP connectors.

## 3. Existing building blocks

- `services/mcp_server_endpoint.py` — Streamable HTTP endpoint
  `/mcp/{server_id}` (POST/GET/DELETE, `Mcp-Session-Id` negotiation, Origin
  anti-rebinding check, relay sub-routes). Auth: `_authenticate()` →
  `MCPServerStore.validate_key()` on the Bearer header.
- `core/mcp_server_store.py` — SQLite store: `mcp_servers` (one publication
  per conversation) and `mcp_api_keys` (hashed keys, revocation, last-used).
- `tasks/ai/actions/_agentres_k6.py` — owner-only publication management
  actions (`mcp_server_configure`, `mcp_server_create_key`, …).
- `tasks/io/chat_ui/resources_mcp_publish.js` — publish dialog (i18n en/fr/es).
- `docs/PUBLISHED_MCP_SERVER.md` — user-facing documentation.

---

## 4. Phase A — connector key in the URL ("no auth" connector)

### 4.1 Design

1. **New route family** `/mcp/{server_id}/k/{connector_key}` (POST, GET,
   DELETE) dispatching to the same JSON-RPC handlers as `/mcp/{server_id}`.
   The relay sub-routes (`/relay/*`) are NOT mirrored — they are for the
   PawFlow CLI bridge only.
2. **Key kind.** `mcp_api_keys` gains a `kind` column
   (`'bearer'` default, `'connector'`). A connector key is generated with a
   distinct prefix (`pfmcc_`) and is valid **only** in the URL path; bearer
   keys remain valid **only** in the `Authorization` header. No cross-use:
   leaking one kind never unlocks the other surface, and revocation semantics
   stay obvious.
3. **Validation** reuses the hashed-token lookup (`validate_key` gains a
   `kind` filter). Hash comparison is already constant-time by construction.
4. **Opt-in per publication.** The URL-key surface exists only while the
   publication has at least one active `connector` key. Revoking the last
   connector key closes the surface; `enabled=0` closes everything as today.
5. **Log hygiene.** The `{connector_key}` path segment must never reach
   access logs, error payloads, or exception messages — redact to
   `k/pfmcc_…[redacted]` at the endpoint boundary.
6. **Origin / session behavior unchanged.** `_origin_allowed()` and the
   `Mcp-Session-Id` session table apply identically to the new routes.
7. **Per-publication tool allowlist.** `mcp_servers` gains a
   `tool_allowlist` TEXT column (JSON array; empty = all tools). Enforced in
   `tools/list` (filtered) and `tools/call` (unknown/filtered tool → JSON-RPC
   error). Rationale: a third-party cloud client (OpenAI) should not
   implicitly get relay shell/filesystem access; the owner decides.
   Applies to both bearer and connector traffic — one publication, one policy.

### 4.2 Work packages

- **WP-A1 — Store.** `kind` column + idempotent ALTER migration;
  `create_key(server_id, label, kind)`; `validate_key(server_id, raw, kind)`;
  `list_keys` returns `kind`; `tool_allowlist` column + accessors.
  Tests: migration on a pre-existing DB file, cross-kind rejection, listing.
- **WP-A2 — Endpoint.** New routes, path-key authentication, log redaction.
  Tests: full initialize → tools/list → tools/call round trip through
  `/k/{key}`; revoked/unknown key → 401; bearer key used in path → 401;
  connector key used as Bearer header → 401; DELETE session teardown; Origin
  rejection still applies.
- **WP-A3 — Tool allowlist.** Filtering in tools/list + tools/call for both
  route families. Tests: empty list = passthrough, filtered list, call on
  excluded tool → error, allowlist update visible after ChatGPT “Refresh”.
- **WP-A4 — UI.** Publish dialog: “ChatGPT / connector URL” section —
  generate/revoke connector key, one-shot display of the full URL
  (`https://…/mcp/srv_…/k/pfmcc_…`), copy button, tool-allowlist editor.
  i18n en/fr/es. Extend `test_published_mcp_ui_is_loaded_and_translated`.
- **WP-A5 — Actions + docs.** Extend `_agentres_k6.py`
  (`mcp_server_create_key` gains `kind`; `mcp_server_configure` gains
  `tool_allowlist`). Update `docs/PUBLISHED_MCP_SERVER.md` with a ChatGPT
  walkthrough (developer mode, connector creation with “No authentication”,
  refresh, write-confirmation behavior) and the security notes below.
- **WP-A6 — Validation gate (manual).** Checklist executed against a real
  ChatGPT Plus/Pro account through a public HTTPS front (frpc tunnel is fine):
  connector created; tools listed; a read tool round-trips; a write tool
  round-trips with ChatGPT's confirmation prompt; allowlist honored after
  Refresh; key revocation kills access immediately.

### 4.3 Phase A exit criteria

- All WP-A1..A5 tests green in the full pytest gate.
- WP-A6 checklist fully green, captured in the PR description.
- No secret (key material) appears in server logs during the E2E run.

### 4.4 Accepted risks (Phase A only)

- The secret lives in a URL: browser history, proxies, and misconfigured
  logging on intermediaries can leak it. Mitigations: opt-in, distinct kind,
  instant revocation, redaction on our side, documented recommendation to use
  a minimal tool allowlist. This risk is the reason Phase A is throw-away.

---

## 5. Phase B — OAuth 2.1 authorization server

Trigger: Phase A validated by real usage (owner decision), then Phase B is
implemented and Phase A's auth surface is deleted in the same release train.

### 5.1 Design

1. **Protected resource metadata (RFC 9728).**
   `/.well-known/oauth-protected-resource/mcp/{server_id}` describing the
   resource and pointing to the authorization server; 401 responses add the
   `WWW-Authenticate: Bearer resource_metadata=…` pointer.
2. **Authorization server metadata (RFC 8414).** Single AS for the
   deployment at `/.well-known/oauth-authorization-server`; issuer = public
   base URL.
3. **Dynamic client registration (RFC 7591).** Open registration endpoint
   (ChatGPT registers itself); registered clients are stored and listable by
   the admin.
4. **Authorization code + PKCE (S256 only).** The consent page runs inside
   the existing web session (AuthGatewayService login); the resource owner
   must be the publication owner. Consent screen shows: publication label,
   agent, tool allowlist, requesting client.
5. **Tokens.** Opaque access tokens (`pfmat_` prefix, hashed at rest) bound
   to `(server_id, owner_user_id, client_id)`, short TTL + refresh tokens
   with rotation; revocation endpoint (RFC 7009) and per-publication “revoke
   all grants” in the UI.
6. **Endpoint auth.** `_authenticate()` accepts `Bearer pfmcp_…` (CLI bridge,
   unchanged) or `Bearer pfmat_…` (OAuth) with audience check against the
   requested `server_id`.
7. **Storage.** New tables in `mcp_servers.sqlite3`: `oauth_clients`,
   `oauth_codes`, `oauth_tokens` (hashed), with the same thread-safe store
   pattern as `MCPServerStore`.

### 5.2 Work packages

- **WP-B1 — Store + token model.** Tables, hashing, TTL/rotation, revocation.
- **WP-B2 — Metadata + DCR endpoints.** RFC 9728 + 8414 documents, RFC 7591
  registration. Tests against golden JSON.
- **WP-B3 — Authorization + token endpoints.** Code flow with PKCE S256,
  consent page (i18n), error surfaces per RFC 6749/9126 semantics.
- **WP-B4 — Resource auth integration.** Dual-scheme `_authenticate`,
  audience checks, 401 metadata pointer. Tests: scripted OAuth client doing
  the full DCR → authorize → token → tools/call flow in-process.
- **WP-B5 — UI + docs.** Grants panel (clients, last used, revoke);
  `docs/PUBLISHED_MCP_SERVER.md` rewritten for OAuth; ChatGPT walkthrough
  updated (connector auth = OAuth).
- **WP-B6 — E2E validation.** Same checklist as WP-A6 but through the OAuth
  consent flow, plus token expiry/refresh observed live.

### 5.3 Migration A → B (one-shot)

In the Phase B release:

1. Delete the `/mcp/{server_id}/k/…` routes and every connector-key code
   path (endpoint, UI section, action parameter).
2. Startup migration: revoke all `kind='connector'` keys (rows kept, marked
   revoked, for audit); the `kind` column remains for bearer keys.
3. `tool_allowlist` and all Phase A tests for it are kept — they are
   auth-agnostic.
4. Users re-create their ChatGPT connector once, choosing OAuth; documented
   as a breaking change in the release notes.

### 5.4 Phase B exit criteria

- Full pytest gate green including the in-process OAuth flow test.
- WP-B6 live checklist green.
- Zero remaining references to `pfmcc_`/`/k/` outside the migration that
  revokes old keys.

---

## 6. Out of scope

- Deep Research connectors (`search`/`fetch` tool contract) — developer-mode
  connectors do not require them.
- Publishing PawFlow tools to OpenAI's Apps SDK.
- Multi-AS federation or third-party IdP-backed consent (the consent binds to
  the existing PawFlow login).

# LLM Auth Modes — three credential modes for every provider

Status: **implemented** (2026-08-22).

Goal: every LLM provider — API or CLI — supports the same three
authentication modes, and OAuth stops being a CLI-only privilege.

| Mode | Meaning |
|---|---|
| `none` | No credential is sent (local Ollama, an unauthenticated gateway) |
| `api_key` | Static key or key pool, sent in the provider's auth header |
| `oauth` | Access token from a credential pool, refreshed automatically |

A second goal: a **generic** OAuth credential provider, configurable with an
identity provider plus client id/secret, usable by API providers *and* by the
three CLIs — a CLI can be pointed at a different OAuth-authenticated backend,
so it must not be locked to its vendor preset.

## Where we are today

OAuth for LLM credentials exists but is hardwired to three CLI vendors.

- `services/llm_credential_oauth.py:21` — `PROVIDERS = ("claude-code",
  "codex-app-server", "gemini")`. `_create_connection` (line 152) raises for
  anything else, so the service type simply cannot be attached to an API
  provider.
- `services/llm_credential_oauth.py:40` — one hardcoded default credential
  service id per vendor, and `get_service_actions` (line 180) exposes login
  flows gated by `"when": {"provider": ["claude-code"]}` and friends. The
  login flow *is* the vendor.
- `services/llm_connection.py:112` — the fork this plan removes:

  ```python
  elif self.provider in ("claude-code", ..., "gemini"):
      # OAuth pool is the default credential source, api_key optional
      pass
  else:
      # API-based providers (openai, anthropic) need an api_key.
      if not self.api_key:
          raise ServiceError("api_key is required")
  ```

- `core/llm_providers/openai.py:_openai_auth_headers` — builds
  `Authorization: Bearer {self.api_key}`. There is exactly one credential
  source for API providers, and it is `api_key`.

What already exists and must be reused rather than rebuilt:

- `llmConnection.credential_service_id` (`services/llm_connection.py:427`) —
  the link from an LLM service to a credential pool. Already a `service_ref`
  with provider aliasing. **No new link field is needed.**
- `LLMCredentialOAuthProviderService` — the encrypted pool, its resolution
  helpers (`resolve_credential_service_id`,
  `credential_service_id_from_llm_service`) and its secret key convention
  (`credential_pool_secret_key`).
- `OAuthProviderService` (`services/oauth_provider_service.py`) —
  client_id / client_secret / scope / authorize_url / token_url with presets.
- `services/auth_providers/oauth_base.py:83` — `refresh_access_token`, a
  working refresh implementation.
- `services/auth_providers/generic_oauth.py` — presets for Keycloak, Okta,
  Auth0, GitLab, and free-form endpoint overrides.

## Naming collision — settled

`auth_mode` was **already taken**: `services/llm_connection.py:463` defined it
as OmniRoute's gateway mode (`bearer` | `none`), hidden for other providers
(line 766).

**Decision (user, 2026-08-22): take `auth_mode` for the general concept and
rename OmniRoute's field to `omniroute_auth_mode`.** OmniRoute is neither in
use nor tested yet, so the breaking config change costs nothing, and the
project's zero-backward-compatibility rule applies: rename in one shot, delete
the old key, no dual read.

Rename sites: the schema entry at `services/llm_connection.py:463`, the
visibility rule at line 766, the two readers that pass it to OmniRoute's
`auth_headers` (`services/llm_connection.py:108` and
`core/llm_providers/openai.py:_openai_auth_headers`), and
`core/llm_providers/omniroute.py`.

## Target design

### 1. `auth_mode` on `llmConnection`

A select with three options, defaulting by provider family so existing
services keep working without being touched:

- CLI providers → `oauth` when a `credential_service_id` resolves, else
  `api_key` when a key is set, else `none`.
- API providers → `api_key`.

Validation replaces the `services/llm_connection.py:112` fork with one rule
applied to every provider alike:

| `auth_mode` | Requirement |
|---|---|
| `none` | nothing; `api_key` and `credential_service_id` must be empty |
| `api_key` | `api_key` or `api_keys_pool` non-empty |
| `oauth` | `credential_service_id` resolves to a pool holding a valid credential |

No silent fallback between modes: a service in `oauth` mode with an empty pool
is a `ServiceError` at install time, not a quiet downgrade to `api_key`. That
is the existing house rule (`CLAUDE.md`: "No 'anonymous' or 'default'
fallbacks").

### 2. A generic credential provider

Extend `llmCredentialOAuthProvider` with a fourth provider value, `generic`,
whose config carries the identity provider instead of inheriting it:

| Field | Notes |
|---|---|
| `identity_provider` | preset name (`keycloak`, `okta`, `auth0`, `gitlab`, `custom`) |
| `client_id` | required |
| `client_secret` | required, `sensitive: True` |
| `authorize_url` / `token_url` | auto-filled from the preset, overridable |
| `scope` | preset default, overridable |
| `audience` | optional; several IdPs need it to mint an API-usable token |

These are the same fields `OAuthProviderService` already declares. The
implementation should import the preset table and the token exchange from the
existing modules rather than copying them — a second copy of an OAuth client
is how the two drift apart.

Note for implementation: every `token_url` string literal needs `# nosec B105`
or CI's bandit job goes red on a string assigned to a *secret*-looking key —
the existing tables already do this.

### 3. Token injection for API providers

`_openai_auth_headers` (and the Anthropic equivalent) currently read
`self.api_key`. Introduce one resolution point — a `bearer_credential()` on
the client — that returns either the static key or a live access token from
the pool, refreshing it when `expires_at` has passed via
`oauth_base.refresh_access_token`.

The header shape stays the provider's business:
`_openai_auth_headers` keeps choosing `Authorization: Bearer …` vs a dialect
header; only the *value* changes source. This keeps the change out of every
individual dialect.

### 4. The three CLIs may use the generic provider

Today `credential_service_id`'s `service_ref` filters candidate services by
`provider_field: "provider"` with vendor aliases
(`services/llm_connection.py:430`). A `generic` credential service must pass
that filter for *any* LLM provider, CLI included. Concretely:
`is_credential_service_def` (line 87) returns True when the pool's provider is
`generic`, whatever the LLM provider asks for.

The CLI-specific login actions stay as they are; the generic provider adds one
more action (`generic_oauth_login`) driven by its own configured endpoints.

## Work packages

| WP | Content | Verification |
|---|---|---|
| WP0a | Rename OmniRoute's `auth_mode` → `omniroute_auth_mode` (schema, visibility rule, both readers, `omniroute.py`) | existing `tests/test_omniroute_provider.py` green after the rename |
| WP0b | `auth_mode` field (`none`/`api_key`/`oauth`), per-provider defaults, validation rewrite at `llm_connection.py:112` | unit tests for the 3×(API, CLI) matrix |
| WP1 | `generic` provider in `llmCredentialOAuthProvider`: schema, presets reused from `generic_oauth.py`, login action | pool round-trip test |
| WP2 | `bearer_credential()` resolution + refresh, wired into the OpenAI and Anthropic auth headers | test: expired token triggers refresh, header carries the new token |
| WP3 | Accept a `generic` pool for CLI providers (`is_credential_service_def`, `service_ref` filter) | test: CLI service + generic pool resolves |
| WP4 | Docs: `docs/AGENT_SYSTEM.md` / service reference, CHANGELOG | `tests/test_docs_version_consistency.py` |

Each WP is its own commit, none bundled into a release commit
(`AGENTS.md`).

## Decisions

1. **Field naming.** `auth_mode` is the general field; OmniRoute's becomes
   `omniroute_auth_mode` (WP0a). — *user, 2026-08-22*

2. **Refresh is serialised on the pool.** An OAuth access token expires (often
   hourly) and has to be exchanged for a fresh one using the refresh token.
   Several agents can share one credential pool, so two of them can notice the
   expiry at the same instant and both run that exchange. Many identity
   providers invalidate the previous refresh token when they issue a new one
   ("refresh token rotation"), which means the slower of the two writes back a
   token the provider has already revoked — and the pool is then dead until
   someone logs in again. A one-line lock avoids a failure mode that is
   intermittent, load-dependent and miserable to diagnose, so: take a lock per
   pool around "check expiry → refresh → write back", reusing the pattern
   already at `services/llm_connection.py:44` (`_api_key_lock`). The cost is
   that a second agent waits for the first refresh — milliseconds, once an
   hour.

3. **`none` stays an explicit mode.** The alternative was to treat "api_key
   mode with an empty key" as meaning no authentication. That makes a
   forgotten key indistinguishable from a deliberate choice, so a
   misconfigured service fails at the provider with a confusing 401 instead of
   failing at install time with a clear message. An explicit `none` lets
   validation reject an empty `api_key` mode outright, which is the same rule
   the project already applies elsewhere (`CLAUDE.md`: no silent fallbacks,
   missing required params are a `ValueError`). Local Ollama and
   unauthenticated gateways get a mode that says what they are.

## Delivery notes (2026-08-22)

### Backward compatibility without a migration

`auth_mode` defaults to empty, which means *infer*: a service with a
credential pool is `oauth`, one with a key is `api_key`, a bare CLI service is
`none`, and a bare API service is `api_key` so its missing key is still
reported exactly as before. Every service that worked before this field
existed keeps working untouched, and nobody has to edit anything to upgrade.
That is inference for a value that was never stored, not a silent fallback
past an explicit choice: an explicit mode always wins.

### Where the file-size limit forced a split

`tasks/ai/_agentctx_p3.py` is held to 800 lines by
`tests/test_cognitive_panel_files_stay_below_split_limit`. Building the tool
surface pushed it to 840, so that logic moved to
`tasks/ai/_agentctx_tools.py`. Caught by the full suite, not by the focused
runs.

### What shipped

| Piece | Location |
|---|---|
| Mode vocabulary, inference and one validation rule for every provider | `core/llm_auth_modes.py` |
| Provider fork removed | `services/llm_connection.py` `_create_connection` |
| OmniRoute field renamed | `omniroute_auth_mode` across schema, visibility rules, both readers, `omniroute.py`, tests and docs |
| `generic` credential pool | `services/llm_credential_oauth.py` — `identity_provider`, `client_id`/`client_secret`, endpoint overrides, `preset_vars`, `audience` |
| Presets reused, not copied | `services/auth_providers/generic_oauth.py` now exports `PRESETS` |
| Token resolution + refresh, serialised per pool | `core/llm_oauth_credential.py` |
| Single resolution point for auth headers | `LLMClient.bearer_credential()`, used by the OpenAI dialects and Anthropic |
| Generic pool accepted by every provider, CLI included | `is_credential_service_def` |
| Login flow for a generic pool | `generic_oauth_login_url` / `_login_code` in `tasks/ai/actions/_sf_k4.py` |
| Tests | `tests/test_llm_auth_modes.py` (36) |

### Details worth knowing

- `auth_mode=none` now sends **no** `Authorization` header rather than an
  empty bearer, which some gateways reject outright.
- A token with no recorded expiry is treated as long-lived instead of being
  refreshed on every call, which would rotate a working credential for
  nothing.
- An expired token that cannot be refreshed is still sent: the provider's 401
  is the authoritative answer and carries a message worth surfacing, whereas
  failing locally would hide it.
- The refresh margin is 120s, so a request cannot leave with a credential that
  dies in flight.

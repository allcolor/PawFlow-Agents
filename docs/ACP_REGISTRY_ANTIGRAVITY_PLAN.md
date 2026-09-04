# ACP Registry Import and Antigravity ACP Provider — Implementation Plan

Status: analysed and spiked on 2026-09-04; WP-A1, WP-A2, WP-R1 and WP-R2
implemented on 2026-09-04 (uncommitted, pending the combined hotpatch); WP-A3
awaits a real `oauth-personal` login. WP-R2 deviates from §4.3 in one point:
imported agents launch where the generic `acp` provider launches them (the
PawFlow server), not inside `pawflow-claude-code`; running registry agents in
that image needs a generic container pool and is a separate decision.
Depends on: `docs/ACP_INTEGRATION_PLAN.md` (generic outbound `acp` provider, shared `core/acp/` runtime)
Scope: (1) a new `antigravity-acp` LLM provider that drives Google's official
Antigravity ACP server inside the `pawflow-claude-code` image; (2) an import
flow for agents listed in the public ACP registry.

## 1. Decisions (owner: Quentin, 2026-09-04)

- Existing Antigravity providers (`antigravity-interactive`, `agy_mcp`) are
  **not** deprecated. The choice of provider belongs to the user. The new
  provider is documented as Google's sanctioned integration path.
- ACP agent processes run inside the `pawflow-claude-code` image, not in the
  PawFlow server container and not on the relay host. Agents PawFlow supports
  out of the box are baked into that image; Antigravity is the first.
- MCP must work end to end: an Antigravity turn must call PawFlow tools through
  the scoped MCP bridge. Without that the provider has no value.
- A display exists (noVNC in the `pawflow-claude-code` image), so browser-based
  OAuth is acceptable for authentication.

## 2. Measured facts (spike of 2026-09-04, server 1.1.1, linux-x86_64)

Registry entry `antigravity-acp` (Google LLC, proprietary, `license_url`
`https://antigravity.google/terms`):

| Item | Value |
|---|---|
| Archive | `https://dl.google.com/agy-extensions/releases/linux/agy-acp-server-agy_acp_server_1.1.1-linux-x86_64.zip` |
| Archive size / SHA-256 | 681 969 407 bytes / `38f62d01b32deb0907b3d39a71ec301fd36369f6ffd1cf262d4af385177f79df` |
| Contents | `agy_acp_server.par` (1 880 360 328 bytes, ELF x86_64, self-contained Python runtime), `localharness_external` (128 966 920 bytes) |
| Command | `./agy_acp_server.par --uid=` |
| `--uid` | absl flag: "If root, switch to this user id (or empty-string not to switch)", default `nobody`. Keep `--uid=` verbatim. |
| Runs in `pawflow-claude-code` | yes, as user `pawflow`, no extra interpreter |

`initialize` response (protocol 1, `agentInfo.name=antigravity-acp`,
`version=agy_acp_server_1.1.1`):

- `agentCapabilities.loadSession=true`, `sessionCapabilities.list`,
  `sessionCapabilities.resume`, `auth.logout`;
- `promptCapabilities`: image, audio, embeddedContext;
- `mcpCapabilities`: http, sse (stdio is the ACP baseline);
- `authMethods`: `oauth-personal` (Log in with Google), `oauth-business`
  (Gemini Enterprise), `gemini-api-key`, `agent-platform` (ADC or API key).

`session/new` before authentication returns `-32000 Authentication required`.
`session/resume` of an unknown id returns `-32002 Session not found in the
current GEMINI_HOME` (registry protocol matrix, 2026-09-03).

`authenticate` with `oauth-personal` and no browser: the server prints
`Open the following link to authenticate the ACP server: https://accounts.google.com/o/oauth2/v2/auth?...redirect_uri=http://127.0.0.1:<random port>/...&scope=...aicode`
on stderr, starts a local redirect listener on `127.0.0.1:<random port>`, and
calls `webbrowser`. The redirect must therefore be completed by a browser that
can reach the server's loopback: the in-container noVNC browser.

Storage (strings of the embedded `acp_server` package; no keyring on Linux,
only `macos_keychain.py` exists): everything lives under `GEMINI_HOME`, which
`main.py` reads explicitly:

- `antigravity-acp/acp_token.json` (personal OAuth), `acp_business_token.json`;
- `antigravity-acp/settings.json`, `antigravity-acp/conversations/`,
  `antigravity-acp/trusted_workspaces.json`, `antigravity-acp/brain/`;
- `mcp_servers.py` reads `mcpServers` from `session/new` plus the
  `.gemini/antigravity` configuration;
- env recognised: `GEMINI_HOME`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`,
  `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`.

Stderr is very verbose (google3 traces, `Fatal Python error` on SIGTERM) and
must be captured to a per-session log file, never surfaced as diagnostics.

Open point to validate on the first authenticated turn: `workspace_trust.py`
may require `cwd` to be listed in `trusted_workspaces.json`.

## 3. Registry facts

- Index: `https://cdn.agentclientprotocol.com/registry/v1/latest/registry.json`
  (`version` 1.0.0, 32 agents on 2026-09-03), schema `agent.schema.json`.
- Entry fields: `id`, `name`, `version`, `description`, `repository`,
  `website`, `authors`, `license`, `license_url`, `icon`, `distribution`.
- Distribution types: `binary` (per platform: `archive`, `cmd`, `args`, `env`,
  optional `sha256`), `npx` (`package`, `args`, `env`), `uvx` (same).
  Platforms: `darwin-aarch64`, `darwin-x86_64`, `linux-aarch64`,
  `linux-x86_64`, `windows-aarch64`, `windows-x86_64`.
- `.protocol-matrix/latest.json` publishes per agent the advertised auth
  method types and capabilities; `quarantine.json` lists withdrawn entries.
- Listing requires `agent` (browser OAuth run by the agent) or `terminal`
  (interactive TUI) authentication. Neither works headless.
- The registry is community curated. Entries are untrusted download URLs.

## 4. Architecture

### 4.1 Process placement

`core/acp/process_session.py` launches argv through the SDK's
`spawn_agent_process`. No change to `core/acp/`: the provider builds argv as
`docker exec -i <container> <cmd> <args...>` so stdio is bridged into the
`pawflow-claude-code` container. The PawFlow server image already ships the
Docker CLI.

Container lifecycle reuses `AntigravityObserverPool._spawn_container`
(`core/antigravity_observer_pool.py`): same image selection
(`PAWFLOW_ANTIGRAVITY_IMAGE`), `/cc_sessions` bind, AppArmor options, one
container per `(user, conversation, agent, service)`. The observer proxy and
tmux are not started for this provider.

### 4.2 `antigravity-acp` provider

`core/llm_providers/antigravity_acp.py` — `LLMAntigravityAcpMixin` built on
`LLMAcpMixin`:

- fixed command `/opt/pawflow/agy_acp_server.par`, args `--uid=`; no
  `acp_command`/`acp_args` in the service form (`services/llm_connection.py`
  rule hides them);
- `GEMINI_HOME=<session home>/.gemini` per `(user, service)`, created by the
  pool; `acp_cwd` defaults to the conversation workspace inside the container;
- `acp_auth_method_id` is a select with the four measured ids, default
  `oauth-personal`; `gemini-api-key` and `agent-platform` read their key from
  the existing `llm.<service_id>.api_key` secret and pass it as
  `GEMINI_API_KEY`/`GOOGLE_API_KEY` through `acp_env`;
- `acp_mcp_mode=pawflow` is forced; `acp_use_client_io` stays configurable;
- stderr of the process is redirected to
  `data/runtime/sessions/antigravity-acp/<user>/<conversation>/<agent>/logs/`;
- Antigravity `usage` and `UsageUpdate` feed the context gauge only when
  observed (no fabricated numbers), per the generic provider rule.

Authentication action (service action in `tasks/ai/actions/`, same pattern as
`_sf_k1.py` server-side login):

1. start the container with the noVNC display, run
   `agy_acp_server.par --uid=` under the session `GEMINI_HOME`, send
   `initialize` then `authenticate {methodId}` through `AcpProcessSession`;
2. open the printed URL in the in-container browser (noVNC tab in the UI, as
   `docker/claude-code/agy_auth_login.sh` does for `agy`);
3. wait for `authenticate` to return, verify `acp_token.json` exists, stop the
   process;
4. the token file stays in the session `GEMINI_HOME`; PawFlow never copies
   its content into diagnostics or the conversation.

Multi-account rotation (the Gemini OAuth pool) is out of scope for v1: one
`GEMINI_HOME` per service. A later lot may map pool slots to homes.

### 4.3 Registry import

`core/acp/registry.py`:

- fetch the index (HTTPS only) with a 24 h cache under
  `data/runtime/acp_registry/`; validate against the vendored
  `agent.schema.json`; apply `quarantine.json`;
- resolve the platform of the `pawflow-claude-code` container
  (`linux-x86_64` / `linux-aarch64`);
- materialise `binary` distributions into
  `data/runtime/acp_agents/<id>/<version>/<platform>/` inside the container
  (download, verify `sha256` when present, record the observed digest, extract
  zip/tar.gz/tgz/tar.bz2/tbz2, `chmod +x cmd`); never run installer formats;
- `npx` runs in the container (Node 22 is present); `uvx` requires adding `uv`
  to `docker/claude-code/Dockerfile` (separate decision); a missing runtime is
  a `ValueError` naming the missing tool;
- produce an `llmConnection` service (provider `acp`) pre-filled with
  `acp_command`, `acp_args`, `acp_env`, `acp_cwd`, and, from the protocol
  matrix, `acp_auth_method_id` when exactly one method type is advertised and
  `acp_load_session`;
- pin the imported `version`; provide a **Check registry updates** service
  action; never auto-upgrade;
- show `license` / `license_url` before import for `proprietary` entries.

UI: Resources › Services gains **Import ACP agent** (catalogue list with
name, version, license, auth type, capabilities). Strings in EN/FR/ES.

### 4.4 Image

`docker/claude-code/Dockerfile`: download the pinned Antigravity ACP archive at
build time, verify the SHA-256 above, extract to `/opt/pawflow/`, keep
`localharness_external` beside the binary. `stamp_versions.sh` records the
server version. Expected image growth: about +1.9 GB.

## 5. Work packages

### WP-A1 — Antigravity ACP provider core

Files: `core/llm_providers/antigravity_acp.py`, `core/llm_client.py`
(`PROVIDERS`, no-default-model list), `core/_llm_client_driver.py`,
`core/llm_providers/__init__.py`, `services/llm_connection.py` (schema +
provider rule), `core/antigravity_observer_pool.py` (container spawn reuse),
`docker/claude-code/Dockerfile`, `docker/claude-code/stamp_versions.sh`.

Tests: `tests/test_antigravity_acp_provider.py` with a fake agent extending
`tests/fixtures/acp_runtime_agent.py` that reproduces the measured contract
(`-32000` before `authenticate`, four auth ids, `-32002` on unknown resume,
`mcpServers` echo); config validation (fixed argv, forced MCP mode, env
secrets never in diagnostics); stderr redirected; provider registry lists
stay consistent (`test_gauge_invariants.py`, dispatch signature tests).

Exit gate: cold turn, warm turn, load/stale-load, cancel, force stop pass
against the fake agent; the real binary answers `initialize` through
`docker exec` in CI-less manual verification.

### WP-A2 — Authentication flow

Files: `tasks/ai/actions/_sf_k1.py`/`_sf_k2.py` (or a focused new action
module), `tasks/io/chat_ui/resources_service_login.js`, `core/_install_credentials.py`
(installer bootstrap option), locale strings.

Tests: action authorisation, URL capture from stderr, token-file presence
check, no secret material in action results.

Exit gate: a real `oauth-personal` login completes in noVNC and
`acp_token.json` appears under the session `GEMINI_HOME`.

### WP-A3 — End-to-end MCP turn

Validate on the real server: `session/new` with the scoped PawFlow MCP stdio
server, one tool call (`read`) round trip, permission request mapped through
`ToolApprovalGate`, `trusted_workspaces.json` handling if required, usage
reporting. Record findings in `docs/ANTIGRAVITY_ACP.md`.

Exit gate: one persisted turn with a PawFlow tool call visible in the UI.

### WP-R1 — Registry client

Files: `core/acp/registry.py`, vendored `core/acp/registry_schema.json`,
`tests/test_acp_registry.py` (schema validation, platform resolution,
quarantine, sha256 mismatch refusal, installer format refusal, offline cache).

### WP-R2 — Import action and UI

Files: service actions, `tasks/io/chat_ui/` services module, locale strings,
`docs/llm_providers.md`.

Exit gate: importing `codex-acp` (npx) and one `binary` entry produces a
working `acp` service; `uvx` fails with an explicit message until `uv` ships.

### WP-D — Documentation

`docs/llm_providers.md` (new provider row, registry import section),
`docs/ANTIGRAVITY_ACP.md`, `docs/installation_bootstrap.md` (Gemini
subscription guidance lists `antigravity-acp` beside the existing choices),
`docs/deployment.md` (image size), `CHANGELOG.md`.

## 6. Security and policy notes

- Credentials stay in the container session home; never in argv, logs,
  diagnostics, or service records.
- Registry downloads: HTTPS only, digest recorded, pinned versions, quarantine
  honoured, licences shown.
- Google's FAQ forbids third-party software with an Antigravity login; the ACP
  server is Google's own distribution listed by Google in the registry and is
  the surface used by Zed and JetBrains. PawFlow acts as a plain ACP client.
  This is a reading of the public documents, not legal advice; the user
  chooses the provider.

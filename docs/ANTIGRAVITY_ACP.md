# Antigravity ACP Provider

`antigravity-acp` runs Google's official Antigravity ACP server
(`agy_acp_server`, ACP registry entry `antigravity-acp`) as a normal
`llmConnection` provider. It is the same integration surface Zed and JetBrains
use: PawFlow acts as a plain ACP client and never inspects or proxies vendor
traffic. The existing `antigravity-interactive` and `agy_mcp` providers remain
available; the choice of provider belongs to the user.

Plan and measured facts: `docs/ACP_REGISTRY_ANTIGRAVITY_PLAN.md`.

## Runtime

- The server binary is baked into the `pawflow-claude-code` image at
  `/opt/pawflow/antigravity-acp/agy_acp_server.par` (self-contained ELF, no
  interpreter needed). `docker/claude-code/Dockerfile` pins its version and
  archive SHA-256 through the `ANTIGRAVITY_ACP_VERSION` and
  `ANTIGRAVITY_ACP_SHA256` build args; `cli_versions.json` records it as
  `antigravity_acp`.
- `core/antigravity_acp_pool.py` keeps one sleeping container per
  `(user, conversation, agent, service)` and execs the server inside it over
  stdio: `docker exec -i --user <uid:gid> -w <workspace> ... <container>
  agy_acp_server.par --uid=`. `--uid=` (empty) tells the server not to drop
  to `nobody`; the registry passes it the same way.
- The shared ACP runtime (`core/acp/`, `core/llm_providers/acp.py`) does the
  rest: initialize, authenticate, session/new or session/load, prompt,
  updates, permissions, cancellation. `core/llm_providers/antigravity_acp.py`
  only overrides what the container changes, and every override is guarded by
  the provider name so the generic `acp` provider is untouched.
- Server stderr (verbose google3 logging) is redirected to
  `data/runtime/sessions/antigravity-acp/<user>/<conversation>/<agent>/logs/acp-server.stderr.log`.
  An unread pipe would block the server once the pipe buffer fills.

## Directories

Everything the server persists lives under `GEMINI_HOME`, which PawFlow sets
per `(user, service)`:

```text
data/runtime/sessions/antigravity-acp/<user>/homes/<service>/.gemini/antigravity-acp/
    acp_token.json            personal OAuth token
    acp_business_token.json   Gemini Enterprise token
    settings.json, conversations/, trusted_workspaces.json, brain/
```

One login therefore serves every conversation of that service. The per
conversation workspace is
`data/runtime/sessions/antigravity-acp/<user>/<conversation>/<agent>/`, mounted
into the container under `/cc_sessions_host/`.

## Service configuration

| Field | Meaning |
|---|---|
| `antigravity_acp_auth_method` | Exact method id advertised by the server: `oauth-personal` (Google account), `oauth-business` (Gemini Enterprise), `gemini-api-key`, `agent-platform` (Application Default Credentials or a Google API key). |
| `auth_mode` | `none` for the two browser logins and for ADC; `api_key` with `api_key` for `gemini-api-key` (sent as `GEMINI_API_KEY`) or `agent-platform` (sent as `GOOGLE_API_KEY`). A credential pool (`auth_mode=oauth`) is refused: the server owns its login. |
| `cli_environment` | `NAME=value` lines forwarded into the container, for example `GOOGLE_CLOUD_PROJECT`. `HOME`, `GEMINI_HOME`, `GEMINI_API_KEY`, `GOOGLE_API_KEY` and `USER` are PawFlow-owned and rejected. |
| `acp_reuse_process`, `acp_load_session`, `acp_use_client_io`, `acp_title_override` | Same meaning as for the generic `acp` provider. |

Secrets never enter argv: environment entries are forwarded by name
(`docker exec -e NAME`) and take their values from the docker CLI process
environment.

PawFlow's MCP bridge is always exposed (`acp_mcp_mode=pawflow`): `session/new`
receives the scoped `pawflow` stdio server
(`/usr/bin/python3 /opt/pawflow/mcp_bridge.py`) with a per-session internal
token, exactly like the Gemini ACP path.

## Authentication

With `oauth-personal` or `oauth-business`, `authenticate` makes the server
print an OAuth URL on stderr and start a loopback redirect listener on
`127.0.0.1:<random port>`. The redirect must be completed by a browser that can
reach that loopback, which is the in-container noVNC browser used by the other
CLI logins.

The service action **Login via server (Antigravity ACP)** does exactly that.
It reuses the Antigravity noVNC dialog (`agy_server_login` /
`agy_server_login_status` in `tasks/ai/actions/_sf_k8.py` branch to
`tasks/ai/actions/_sf_acp.py` when the service provider is `antigravity-acp`):

1. a `pawflow-claude-code` login container starts
   `docker/claude-code/agy_acp_auth_login.sh`: Xvfb, x11vnc, websockify, an
   xterm, `GEMINI_HOME=/workspace/.gemini`, `BROWSER=/usr/local/bin/open-browser`,
   and the API-key variables unset so the browser method is really used;
2. `docker/claude-code/agy_acp_login.py` runs `agy_acp_server.par --uid=`,
   sends `initialize`, checks that the configured method is advertised, sends
   `authenticate {methodId}` and waits; the server opens the OAuth URL in the
   in-container browser, which you complete in the noVNC tab;
3. the driver writes `/tmp/agy-acp-login.result.json` (`ok`, `method`,
   `advertised`, `error`), never token material; the action copies
   `acp_token.json` / `acp_business_token.json` from the container into the
   service `GEMINI_HOME` shown above and reports success.

`gemini-api-key` and `agent-platform` need no login: configure the key on the
service. Alternatively copy a valid `acp_token.json` into the service
`GEMINI_HOME`.

## Lifecycle

- Warm turns reuse the process and the ACP session; a restart loads the
  persisted session id and falls back once to `session/new` on `-32002`.
- A force stop closes the docker CLI and kills the container, because a
  killed CLI would leave the exec'd server alive; the next turn cold-starts a
  new container.
- Ephemeral streams (compact, memory extraction) run their own process in the
  foreground container and never kill it.
- `acp_reuse_process=false` closes the process after each turn; the container
  stays.

## Tests

`tests/test_antigravity_acp_provider.py` drives the real provider path with
`tests/fixtures/acp_runtime_agent.py` in `PAWFLOW_ACP_FIXTURE_AUTH=antigravity`
mode, which replays the measured server contract: four auth method ids,
`-32000 Authentication required` before `authenticate`, MCP servers accepted at
`session/new`. Docker is replaced by running the fixture directly.

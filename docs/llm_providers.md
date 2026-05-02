# LLM Providers

PawFlow can run agents through direct HTTP APIs and through CLI-backed coding agents. Agents reference an LLM service by id, so different agents in the same conversation can use different backends.

## Provider Types

| Provider | Mode | Typical use | Notes |
|---|---|---|---|
| `openai` | Direct API | OpenAI and OpenAI-compatible endpoints | Set `api_key`, optional `base_url`, and `default_model`. |
| `anthropic` | Direct API | Claude API agents | Set `api_key`, optional `base_url`, and `default_model`. |
| `claude-code` / Claude Code | CLI container or subprocess | Coding agents using Claude Code sessions | Uses Claude credentials, session resume, and PawFlow tool bridge. |
| `codex` / Codex CLI | CLI container or subprocess | Coding agents using Codex sessions | Uses Codex/OpenAI credentials and a Codex pool. |
| `gemini` / Gemini CLI | CLI container or subprocess | Gemini-backed coding agents | Uses Gemini credentials, stream-json output, and Gemini session files. |

Direct API providers are normal HTTP clients. CLI providers launch a provider CLI, keep provider-specific session state, and route tools through PawFlow's relay/MCP bridge.

## Agent Configuration

Agents reference a service id:

```json
{
  "name": "coder",
  "prompt": "You are a pragmatic coding agent.",
  "llm_service": "codex_llm_service",
  "model": "",
  "tools": [],
  "max_depth": 2
}
```

The service id can also be resolved through parameters:

```json
{
  "llm_service": "${llm_default_service}"
}
```

Resolution order is flow -> conversation -> user -> global -> environment.

## LLM Service Fields

Common fields:

| Field | Required | Description |
|---|---:|---|
| `provider` | yes | Provider name: `openai`, `anthropic`, `claude-code`, `codex`, `gemini`, or compatible aliases. |
| `default_model` / `model` | yes | Model used when the agent does not override it. |
| `api_key` | provider-dependent | API key or credential reference for direct API providers and key-based CLI auth. |
| `base_url` | no | Alternate API endpoint. For Codex/OpenAI-compatible providers this maps to `OPENAI_BASE_URL`; for Gemini it maps to `GEMINI_BASE_URL`. |
| `docker_image` | CLI providers | Container image used for server-side CLI sessions and pools. |
| `max_context_size` | yes for CLI providers unless the CLI reports the window | Authoritative context window. PawFlow must not guess a hard default. |
| `compact_target_tokens` | no | Target size after compaction. |
| `compact_threshold_pct` | no | `0` disables proactive compaction. A positive value triggers compaction at that percentage of `max_context_size`. |
| `token_multiplier` | no | Optional conservative multiplier for provider token estimates. |
| `timeout` | no | Request/stall timeout in seconds. `0` or missing means no timeout; only a positive value limits provider calls. |

For context windows, the rule is strict: if the provider API/CLI reports the context window, that value is authoritative. Otherwise `max_context_size` from the LLM service is authoritative. If neither exists, the service is misconfigured and should fail loudly instead of using a hidden default.

## Circuit Breaker

`LLMClient` keeps a process-wide circuit breaker keyed by provider, base URL,
and model. Transient upstream failures such as 429/5xx/529/timeouts increment
the circuit; permanent auth/config failures do not. After the configured failure
threshold, calls fail fast until the cooldown expires, then one half-open call is
allowed. A successful half-open call closes the circuit; a failed one reopens it.

Optional service fields:

| Field | Default | Description |
|---|---:|---|
| `circuit_breaker_failures` | `3` | Consecutive transient failures before opening. |
| `circuit_breaker_cooldown` | `60` | Seconds to fail fast before half-open. |

## Direct API Example

```json
{
  "type": "llmConnection",
  "provider": "openai",
  "api_key": "${OPENAI_API_KEY}",
  "base_url": "https://api.openai.com/v1",
  "default_model": "gpt-5.5",
  "max_context_size": 400000
}
```

For local or compatible endpoints, change `base_url`:

```json
{
  "type": "llmConnection",
  "provider": "openai",
  "api_key": "${LOCAL_OPENAI_API_KEY}",
  "base_url": "http://localhost:8000/v1",
  "default_model": "local-model",
  "max_context_size": 128000
}
```

## Claude Code

Claude Code can authenticate with an Anthropic API key or with its normal CLI login state.

Credential inputs:

- `ANTHROPIC_API_KEY` or service `api_key`
- `ANTHROPIC_BASE_URL` or service `base_url` when using a compatible Anthropic endpoint
- Claude Code OAuth/session files when using CLI login

Login options:

1. `set_credentials`: paste or store an API key/session payload on the LLM service.
2. Server login: PawFlow starts a tokenized VNC login session on the PawFlow server. The resulting URL is capability-protected and stores credentials back on the service.
3. Relay login: a PawFlow relay runs the provider login on the user's relay host and returns the credential payload.

Container notes:

- Build/use an image containing the Claude CLI, Python, Git, and the PawFlow bridge.
- Server-side login containers are named `pawflow-claude-login-*`.
- Pool containers are named `pf-cc-pool-*`.

## Codex CLI

Codex uses OpenAI/Codex credentials and Codex session state.

Credential inputs:

- `CODEX_API_KEY` preferred when available
- `OPENAI_API_KEY` for OpenAI-compatible Codex auth
- `OPENAI_BASE_URL` from service `base_url` for compatible endpoints
- Codex CLI login files when using OAuth/login mode

Login options are the same as Claude Code: `set_credentials`, server login, or relay login. Server login containers are named `pawflow-codex-login-*`; pool containers are named `pf-codex-pool-*`.

Operational notes:

- Configure `max_context_size` on the service unless the Codex CLI reports the model context window.
- `compact_threshold_pct=0` means no proactive compaction; use a positive percentage such as `90` to compact before the provider hard limit.
- Preemption uses kill/resume semantics for long-running Codex turns.

## Compaction Summarizer

`system.summarizer_service` selects both the compaction budget source and the client that executes summary generation. The summarizer re-resolves the configured service for each compaction call and replaces stale in-memory clients when the service provider changed, so switching from a CLI service to an API-compatible service such as DeepSeek cannot silently keep running the old CLI provider.

All summarizer providers use the same `compact_result` tool contract. Provider call scope (`call_user_id`, `call_conversation_id`, and `call_agent_name`) is passed uniformly so API providers and CLI providers receive equivalent identity context.

## Gemini CLI

Gemini uses either API-key auth or Gemini CLI OAuth state.

Credential inputs:

- `GEMINI_API_KEY` or service `api_key`
- `GEMINI_BASE_URL` from service `base_url` when using a compatible endpoint
- Gemini CLI OAuth files such as `settings.json` and `oauth_creds.json`

Login options are the same as other CLI providers: `set_credentials`, server login, or relay login. Server login containers are named `pawflow-gemini-login-*`; pool containers are named `pf-gemini-pool-*`.

Operational notes:

- Gemini sessions live under the Gemini CLI tmp/chat layout, not the Claude/Codex project layout.
- Gemini stream-json is one-shot stdin. Preemption kills the active process and resumes from the provider session; it should not write to a closed stdin.
- Configure `max_context_size` unless the Gemini CLI reports the context window.

## Tooling Differences

| Capability | Direct API providers | CLI providers |
|---|---|---|
| Tool calls | Native PawFlow tool/function calling | Provider CLI plus PawFlow bridge/MCP where applicable |
| Conversation state | PawFlow builds the context | Provider CLI may keep and resume its own session |
| Preemption | Queued or provider-specific | Provider-specific; Claude can stream control, Codex/Gemini use kill/resume where needed |
| Containerization | Optional | Recommended for isolation and reproducibility |
| Context window | API/model metadata or service config | CLI-reported window or mandatory service `max_context_size` |

## Security Notes

- CLI providers are powerful coding agents. Run them in containers for public or multi-user deployments.
- Browser-accessible provider login, code-server, terminal, VNC, and port-forward URLs should be capability-token protected.
- Relays expose only the directory passed to the relay. Execution on a relay must remain an explicit `--allow-exec` decision by the user/operator.
- Secrets should be stored through PawFlow secret storage, not committed in deployment JSON.

## CLI Workspace Fallback Mounts

CLI providers normally access project files only through PawFlow MCP tools. For compatibility with provider-native filesystem tools that cannot be fully disabled, server startup can opt into fallback workspace bind mounts:

```bash
pawflow start --workspace-mount off|ro|rw
```

If the flag is omitted, `PAWFLOW_CLI_WORKSPACE_MOUNT` is used. The default is `off`.

When enabled, new Claude Code, Codex, and Gemini provider containers mount the default relay workspace at `/workspace`. All linked relays with a local `host_root` are also mounted under `/relay/<relay-id>`. Read-only mode appends Docker `:ro`; read-write mode is explicit because writes through provider-native tools bypass PawFlow MCP auditing and edit guards. Changing relay bindings invalidates affected live CLI sessions so the next session receives fresh mounts.

## Documentation Checklist For New Providers

When adding a provider, document:

1. service type and required secrets;
2. supported model names and default model;
3. whether it is direct API or CLI-backed;
4. login modes and credential file locations;
5. streaming and tool-call behavior;
6. preemption/resume behavior;
7. session persistence behavior;
8. container requirements and image names;
9. context-window source and compaction behavior;
10. known limitations.

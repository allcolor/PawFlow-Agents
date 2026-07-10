# LLM Providers

PawFlow can run agents through direct HTTP APIs and through CLI-backed coding agents. Agents reference an LLM service by id, so different agents in the same conversation can use different backends.

## Provider Types

| Provider | Mode | Typical use | Notes |
|---|---|---|---|
| `openai` | Direct API | OpenAI and OpenAI-compatible endpoints | Set `api_key`, optional `base_url`, and `default_model`. This is the generic OpenAI-compatible API surface. |
| `anthropic` | Direct API | Claude API and Anthropic-compatible endpoints | Set `api_key`, optional `base_url`, and `default_model`. |
| `claude-code` | CLI container or subprocess | Non-interactive Claude Code style coding turns | Uses Claude Code credentials or API-key mode, session resume, and the PawFlow MCP bridge. |
| `claude-code-interactive` | Interactive CLI container with observed provider stream | Claude subscription accounts and long-lived Claude Code sessions | Uses the Claude Code OAuth pool by default. API-key mode can also set `api_key` and `base_url` for Anthropic-compatible endpoints. |
| `antigravity-interactive` | Interactive `agy` CLI in tmux with observed provider stream | Default Gemini subscription provider | Uses the Gemini OAuth credential pool, starts the real `agy` CLI, and routes tools through PawFlow MCP. |
| `codex-app-server` | Codex `app-server` in a pooled container | Codex subscription accounts or OpenAI API-key coding agents | Uses Codex/OpenAI credentials, Codex app-server threads, a Codex pool, and the PawFlow MCP bridge. |
| `gemini` | Gemini CLI one-shot stream provider | Secondary Gemini CLI path, mainly when a Gemini Pro account/CLI workflow is required | Uses Gemini credentials, stream-json output, and Gemini session files. Prefer `antigravity-interactive` for normal Gemini subscription use. |

Direct API providers are normal HTTP clients. CLI providers launch a provider CLI, keep provider-specific session state, and route tools through PawFlow's relay/MCP bridge.

## Which Provider To Use

Use the credential source to choose the provider surface:

| Credential source | Preferred provider(s) | Why |
|---|---|---|
| Generic API key for an OpenAI-compatible endpoint | `openai` | Direct HTTP, tool calling, vision when `supports_vision=true`, `base_url` support, and `/v1/embeddings` support when the endpoint exposes it. |
| Anthropic API key | `anthropic`, or `claude-code` / `claude-code-interactive` with `api_key` | Use `anthropic` for direct API agents. Use Claude Code providers when you want the provider CLI/session behavior and PawFlow MCP bridge. |
| Claude subscription login | `claude-code-interactive` | Long-lived interactive Claude Code session with OAuth credentials from the `claude-code` credential pool. |
| OpenAI API key | `openai`, or `codex-app-server` with `api_key` | Use `openai` for direct API agents. Use `codex-app-server` when you want Codex app-server threads and coding-agent behavior. |
| Codex subscription login | `codex-app-server` | Uses Codex OAuth credentials and the app-server protocol; this is PawFlow's Codex agent provider. |
| Gemini subscription login | `antigravity-interactive` | Default Gemini subscription path. It uses the `agy`/Antigravity CLI with the Gemini OAuth pool. |
| Gemini Pro / Gemini CLI account | `gemini` | Use when the account/workflow specifically needs Gemini CLI stream-json behavior. |

`llmCredentialOAuthProvider` services own OAuth pools for three canonical CLI credential providers: `claude-code`, `codex-app-server`, and `gemini`. `claude-code-interactive` reuses the `claude-code` pool. `antigravity-interactive` reuses the `gemini` pool. API-key mode skips the OAuth pool.

Advanced endpoint routing is supported where the underlying CLI honors it. `claude-code` and `claude-code-interactive` can be used against non-Anthropic compatible endpoints by setting `api_key` plus `base_url`; in that mode PawFlow passes `ANTHROPIC_API_KEY` and `ANTHROPIC_BASE_URL` instead of writing OAuth credentials. `codex-app-server` can use an OpenAI API key and passes it as `CODEX_API_KEY`/`OPENAI_API_KEY`; direct OpenAI-compatible endpoints should normally use the `openai` provider.

On a cold CLI session, PawFlow writes the full serialized initial context to a
session-local `.pawflow_cli/initial_context.md` file and sends a short bootstrap
prompt that tells the CLI to read that file first. The bootstrap also repeats the
latest user turn with XML-sensitive characters escaped, so the immediate request
is visible even if the CLI reads the context file selectively. Resume turns keep
the existing delta-only behavior because the provider session already carries the
prior context. Direct API providers do not use this file bootstrap; they receive
their message context directly in the API request.

CLI session invalidation and live-container eviction are separate operations.
Invalidating a session after compact/edit/branch changes clears the provider's
resume pointer and kills any live Docker/tmux runtime tied to that logical
session. Idle sliding-window cleanup only kills the live Docker runtime; it does
not clear the logical provider session, so providers that can resume may do so on
the next turn.

For `claude-code-interactive`, the live context gauge uses provider-observed
Anthropic `usage` from the MITM SSE stream, including cache creation/read input
tokens. PawFlow does not recompute that gauge from its serialized context during
CCI heartbeats, because Claude Code's actual prompt includes provider-managed
state that can differ substantially from the stored PawFlow message list.

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
| `provider` | yes | Provider name: `openai`, `anthropic`, `claude-code`, `claude-code-interactive`, `antigravity-interactive`, `codex-app-server`, or `gemini`. |
| `default_model` / `model` | yes | Model used when the agent does not override it. |
| `api_key` | provider-dependent | Required for direct API providers. Optional for CLI providers: when present, it bypasses OAuth credentials and configures key-based CLI auth. |
| `credential_service_id` | CLI OAuth mode | References an `llmCredentialOAuthProvider` service. Used when `api_key` is empty for CLI-backed providers. |
| `base_url` | no | Alternate API endpoint. For direct `openai`/`anthropic`, this changes the HTTP target. For Claude Code API-key mode, this maps to `ANTHROPIC_BASE_URL`; for Codex it maps to `OPENAI_BASE_URL`; for Gemini/Agy infrastructure it maps to `GEMINI_BASE_URL` where the underlying CLI supports it. |
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

### Ollama cloud (free tier)

Ollama's hosted API is the easiest zero-cost way to get a working `llmConnection` out of the box — no GPU, no local daemon, and the free plan includes cloud-model access (with 5-hour session and weekly usage limits, one concurrent model). Sign up at [ollama.com](https://ollama.com), create an API key at [ollama.com/settings/keys](https://ollama.com/settings/keys), then:

```json
{
  "type": "llmConnection",
  "provider": "openai",
  "api_key": "${OLLAMA_API_KEY}",
  "base_url": "https://ollama.com/v1",
  "default_model": "gpt-oss:120b"
}
```

The `default_model` parameter helper lists the available cloud models live from `https://ollama.com/v1/models` (this listing works even before the API key is filled). Models consume free-tier usage proportionally to their size — small models such as `gpt-oss:20b` stretch the quota furthest. A local Ollama daemon works the same way with `base_url` pointing at it, e.g. `http://localhost:11434/v1` when the PawFlow server can reach it directly, or a relay-routed URL such as `relay://&#36;{conv.relay}/localhost:11434/v1` when Ollama runs on a relay machine.

### Vision fallback for non-vision models

An `llmConnection` whose model cannot process images (`supports_vision: false`) can name a vision-enabled `llmConnection` in `vision_llm_service`. Every image reaching the non-vision model — user uploads, `see`/`read`/browser tool results, screenshots — is then sent to the vision service with a request for an exhaustive description (all visible text, UI elements with approximate pixel coordinates, states), and the description replaces the image in the model's context:

```json
{
  "type": "llmConnection",
  "provider": "openai",
  "base_url": "https://ollama.com/v1",
  "default_model": "glm-5.2",
  "supports_vision": false,
  "vision_llm_service": "vision_llm_service_id"
}
```

In the service editor the `vision_llm_service` picker appears as soon as `supports_vision` is unchecked — for every provider, including CLI-backed ones whose `base_url` points at a non-vision model. Descriptions are cached by image content hash (in memory and in `data/runtime/vision_describe_cache.json`), so each unique image is described once, not once per turn; an unchanged screenshot re-sent by `see` reuses its cached description. The referenced service must have vision enabled and be a different service; when the fallback cannot run (service missing, vision disabled, describe error), images degrade to text links exactly as before. The stored conversation keeps the original image parts, so switching the conversation to a vision-enabled agent restores native vision on the same history.

OpenAI-compatible providers receive the lazy meta-tools `get_tool_schema` and `use_tool`. The provider-facing `use_tool` schema uses `arguments_json`, a JSON object encoded as a string, instead of a nested free-form `arguments` object. The handler still accepts `arguments` internally for compatibility, but the exposed schema avoids the PawFlow bug where compatible backends repeatedly produced `{}` for nested tool arguments. The OpenAI provider logs tool calls whose `arguments` field is omitted or empty so tool-call regressions are visible.

OpenAI-compatible services also support `extra_body`, a JSON object merged into the chat-completions request body after PawFlow builds its standard fields. This is intended for endpoint-specific options such as OpenRouter routing:

```json
{
  "extra_body": {
    "provider": {
      "order": ["Fireworks", "Together", "DeepInfra"],
      "allow_fallbacks": false
    },
    "transforms": ["middle-out"],
    "include_reasoning": true
  }
}
```

Protected request keys such as `model`, `messages`, `tools`, `stream`, token limits, and API credentials are ignored if present in `extra_body`. Docker execution fields (`docker_image`, `docker_cpu_limit`, `docker_memory_limit`) and `effort` are CLI-provider settings and are hidden for direct API providers (`openai`, `anthropic`).

## Anthropic-Compatible Vision

The Anthropic provider accepts direct Anthropic services and compatible endpoints such as DeepSeek. The `supports_vision` setting on OpenAI and Anthropic API services is the user-controlled capability flag: when enabled, PawFlow resolves `image_ref` attachments and multimodal `see` tool results into native image blocks; when disabled, PawFlow sends only a text note and never transmits image bytes to that provider. For Anthropic payloads, PawFlow logs the number of image blocks included.

## Claude Code Providers

PawFlow has two Claude Code provider surfaces:

- `claude-code`: non-interactive CLI turns with session files and MCP bridge.
- `claude-code-interactive`: long-lived interactive Claude Code session with observed provider stream.

Both can authenticate with an Anthropic API key or with Claude Code OAuth/login credentials. Both can target a compatible non-Anthropic endpoint by setting `api_key` and `base_url` instead of using an OAuth credential service.

Credential inputs:

- `ANTHROPIC_API_KEY` or service `api_key`
- `ANTHROPIC_BASE_URL` or service `base_url` when using a compatible Anthropic endpoint
- `credential_service_id` pointing at an `llmCredentialOAuthProvider` whose provider is `claude-code` when using OAuth login

Login options:

1. Set `api_key` on the LLM service for API-key mode.
2. Create/use `claude_code_oauth_credentials` and use server login, relay login, or pasted credentials for OAuth mode.

Container notes:

- Build/use an image containing the Claude CLI, Python, Git, and the PawFlow bridge.
- Server-side login containers are named `pawflow-claude-login-*`.
- Pool containers are named `pf-cc-pool-*`.

## Codex App-Server

Codex agents use `codex-app-server`. PawFlow does not expose a legacy `codex`
agent provider. The image-generation service may run isolated `codex exec`
jobs for `$imagegen`, but that is a media service, not an agent provider.

Codex app-server uses OpenAI/Codex credentials and Codex thread state. Use it for Codex subscription accounts and for OpenAI API-key backed coding-agent sessions. Use the direct `openai` provider when you only need normal HTTP chat/completions behavior.

Credential inputs:

- `CODEX_API_KEY` preferred when available
- `OPENAI_API_KEY` for OpenAI-compatible Codex auth
- `OPENAI_BASE_URL` from service `base_url` for compatible endpoints
- `credential_service_id` pointing at an `llmCredentialOAuthProvider` whose provider is `codex-app-server` when using OAuth/login mode

Login options are the same as other credential pools: set `api_key`, or use `codex_oauth_credentials` with server login, relay login, or pasted credentials. Server login containers are named `pawflow-codex-login-*`; pool containers are named `pf-codex-pool-*`.

Operational notes:

- Configure `max_context_size` on the service unless the Codex app-server runtime reports the model context window.
- `compact_threshold_pct=0` means no proactive compaction; use a positive percentage such as `90` to compact before the provider hard limit.
- The provider prompt instructs Codex to use only the PawFlow MCP bridge for workspace reads, edits, shell commands, browser/screen actions, and web fetches.
- PawFlow keeps Codex app-server's native sandbox policy at `dangerFullAccess`; restricting app-server networking or filesystem policy can break the MCP bridge and provider tool stream. Native provider tool use is constrained by the provider prompt and made auditable in PawFlow's technical stream.
- If Codex still emits native `commandExecution`, `fileChange`, or `dynamicToolCall` items, PawFlow surfaces them in the technical tool stream instead of hiding them.
- Preemption uses app-server `turn/steer` for active turns.

## Compaction Summarizer

`system.summarizer_service` selects both the compaction budget source and the client that executes summary generation. The summarizer re-resolves the configured service for each compaction call and replaces stale in-memory clients when the service provider changed, so switching from a CLI service to an API-compatible service such as DeepSeek cannot silently keep running the old CLI provider.

All summarizer providers use the same `compact_result` tool contract. Provider call scope (`call_user_id`, `call_conversation_id`, and `call_agent_name`) is passed uniformly so API providers and CLI providers receive equivalent identity context.

`embedding_llm_service` is a separate optional parameter for vector embeddings used by memory tools. Point it at an `llmConnection` service whose provider/base URL exposes an OpenAI-compatible `/v1/embeddings` endpoint. The service's optional `embedding_model` field selects the embedding model; when empty, PawFlow uses the client default embedding model. If `embedding_llm_service` is unset or unusable, memory embedding falls back to the local MiniLM embedder when available.

## Background Compaction Settings

Background bucket compaction reads its tuning values from the configured `summarizer` service. The old `pawflow.bg_compact.*` global/user/conversation parameter namespace is not a configuration surface anymore.

Configure these fields on the `summarizer` service:

| Field | Default | Meaning |
|---|---:|---|
| `l1_trigger_msgs` | `150` | Shared-message count used for a normal level-1 bucket and the message-gap trigger. |
| `bucket_target_tokens` | `2000` | Target token size passed to the summarizer for level-1 buckets and rollup summaries. |
| `header_budget_tokens` | `30000` | Nominal pyramid header token budget before rollup pressure is considered. |
| `rollup_trigger_count` | `30` | Object-count ceiling; above this the builder consolidates old buckets. |
| `tail_reserve_msgs` | `10` | Recent shared messages that are never bucketed and remain in the post-compact tail. |
| `tail_token_budget` | `20000` | Estimated transcript-token budget since the last pyramid coverage. |
| `token_trigger_fraction` | `0.7` | Fraction of `tail_token_budget` that triggers async background bucketing. |
| `bulk_catchup_multiplier` | `5` | Empty-pyramid shortcut threshold: `l1_trigger_msgs * multiplier` enables one large catch-up bucket. |
| `partial_min_msgs` | `5` | Minimum bucketable shared messages for a partial bucket. |
| `min_input_multiplier` | `4` | Minimum useful shared input as `bucket_target_tokens * multiplier` before token pressure can submit a job. |
| `chars_per_token` | `3.5` | Estimation ratio for background trigger and overshoot calculations. |
| `overshoot_warn_multiplier` | `1.5` | Warn when a produced bucket/rollup summary exceeds target tokens by this multiplier. |
| `header_char_multiplier` | `3.0` | Converts `header_budget_tokens` into the estimated character threshold used for rollup pressure. |

Invalid service values are ignored with a warning and the default is used for that decision. Integer counts must be non-negative for `tail_reserve_msgs` and positive for the other count/token fields; multipliers and fractions must be positive.

## Gemini And Antigravity

PawFlow has two Gemini-backed CLI provider surfaces:

- `antigravity-interactive`: the default for Gemini subscription accounts. It runs the real Antigravity CLI (`agy`) in tmux, observes the provider stream, and uses PawFlow MCP tools.
- `gemini`: the Gemini CLI stream-json provider. Use it when a Gemini Pro account or workflow specifically requires Gemini CLI behavior.

Both use either API-key auth or the shared Gemini OAuth credential pool. `antigravity-interactive` is not a separate credential provider; it reuses `gemini_oauth_credentials`.

Credential inputs:

- `GEMINI_API_KEY` or service `api_key`
- `GEMINI_BASE_URL` from service `base_url` when using a compatible endpoint
- `credential_service_id` pointing at an `llmCredentialOAuthProvider` whose provider is `gemini` when using OAuth/login mode

Login options are the same as other credential pools: set `api_key`, or use `gemini_oauth_credentials` with server login, relay login, or pasted credentials. The credential service exposes both Gemini CLI server login and Agy/Antigravity server login; both write the same Gemini OAuth pool. Server login containers are named `pawflow-gemini-login-*`; pool containers are named `pf-gemini-pool-*`; Antigravity observer containers are named `pf-*-agyobs-*`.

Operational notes:

- Gemini sessions live under the Gemini CLI tmp/chat layout, not the Claude/Codex project layout.
- Antigravity sessions live under `data/runtime/sessions/antigravity-observer/...` and use `agy --dangerously-skip-permissions` inside the shared CLI image.
- Gemini stream-json is one-shot stdin. Preemption kills the active process and resumes from the provider session; it should not write to a closed stdin.
- Configure `max_context_size` unless the Gemini CLI reports the context window.
- A `gemini` llmConnection also serves as the credential source for realtime voice sessions (`realtimeVoiceConnection` with `protocol: gemini_live`). That path calls the Google Live API directly over WSS — it requires `api_key` to be set on the connection (the OAuth pool is CLI-only). See [Media Tools — Realtime Voice Conversation](media_tools.md#realtime-voice-conversation).

## Tooling Differences

| Capability | Direct API providers | CLI providers |
|---|---|---|
| Tool calls | Native PawFlow tool/function calling | Provider CLI plus PawFlow bridge/MCP where applicable |
| Conversation state | PawFlow builds the context | Provider CLI may keep and resume its own session |
| Preemption | Queued or provider-specific | Provider-specific; Claude interactive and Antigravity interactive can inject/control live tmux sessions, Codex app-server uses `turn/steer`, Gemini CLI uses kill/resume where needed |
| Containerization | Optional | Recommended for isolation and reproducibility |
| Context window | API/model metadata or service config | CLI-reported window or mandatory service `max_context_size` |

## Security Notes

- CLI providers are powerful coding agents. Run them in containers for public or multi-user deployments.
- Browser-accessible provider login, code-server, terminal, VNC, and port-forward URLs should be capability-token protected.
- Relays expose only the directory passed to the relay. Execution on a relay must remain an explicit `--allow-exec` decision by the user/operator.
- Secrets should be stored through PawFlow secret storage, not committed in deployment JSON.

## CLI Workspace Fallback Mounts

CLI providers normally access project files only through PawFlow MCP tools. For compatibility with provider-native filesystem tools that cannot be fully disabled, server startup can control fallback workspace bind mounts:

```bash
pawflow start --workspace-mount off|ro|rw
```

If the flag is omitted, `PAWFLOW_CLI_WORKSPACE_MOUNT` is used. The default is `rw`.

When enabled, new Claude Code, Codex, and Gemini provider containers mount the default relay workspace at `/workspace`. All linked relays with a local `host_root` are also mounted under `/relay/<relay-id>`. Read-only mode appends Docker `:ro`; read-write mode allows provider-native tools to write through the fallback mount. Changing relay bindings invalidates affected live CLI sessions so the next session receives fresh mounts.

## Default Models

When an LLM service has no explicit `default_model`, the per-provider default
comes from the first source found:

1. `PAWFLOW_DEFAULT_MODELS_FILE` (env var, explicit path);
2. `<data dir>/system/default_models.json` — runtime override, survives upgrades;
3. `config/default_models.json` — shipped with the release (seeded into the
   `/app/config` bind mount on first Docker boot);
4. builtin fallback in `core/llm_client.py` (kept in sync with the shipped file).

The file is a flat JSON object mapping provider type to model id, e.g.
`{"anthropic": "claude-fable-5", "claude-code": "best"}`.

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

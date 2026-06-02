# Services Catalog

Services are reusable integrations configured by id and referenced from flows, agents, handlers, and resource definitions. They are registered through `ServiceFactory` and can be installed globally or per user depending on the resource path.

The chat service installer receives service type metadata grouped by category and renders each category as a separate section in the type selector. Services can declare `CATEGORY`; legacy services are mapped by type so the list stays grouped and sorted instead of falling back to a single alphabetical list.

## Core Services

| Type | Purpose |
|---|---|
| `llmConnection` | LLM service configuration for direct API providers and CLI-backed providers (`openai`, `anthropic`, `claude-code`, `claude-code-interactive`, `antigravity-interactive`, `codex-app-server`, `gemini`). |
| `llmCredentialOAuthProvider` | Encrypted OAuth credential pool for CLI-backed LLM providers. Canonical providers are `claude-code`, `codex-app-server`, and `gemini`; `claude-code-interactive` reuses `claude-code`, and `antigravity-interactive` reuses `gemini`. |
| `httpClientService` | Reusable HTTP client. |
| `httpListener` | Shared listener for inbound HTTP/webhook/SSE/VNC routes. |
| `httpAuthValidator` | Bearer/basic/custom auth validator. |
| `authGateway` | Login/session gateway with OAuth and built-in auth. |
| `oauthProvider` | OAuth provider config. |
| `sslContext` | TLS/SSL context for listener services. |
| `privateGateway` | Pre-authentication challenge gate referenced by `httpListener`. |
| `dbConnectionPool` | SQL connection pooling. |
| `cacheService` | Local cache service. |
| `distributedMapCache` | Distributed key/value cache. |
| `fileTracking` | Tracks processed files for list/watch flows. |
| `packageRuntime` | Runtime proxy for PFP `service_provider` objects executed through the relay package runner. |

## Filesystem and Relay Services

| Type | Purpose |
|---|---|
| `relay` | WebSocket relay. Leave `token` empty to create a managed server relay; provide a token for a standalone relay client. |
| `toolRelay` | Tool relay/MCP bridge for containerized scripts and CLI providers. |
| `googleDrive` | Google Drive filesystem backend. |
| `oneDrive` | OneDrive filesystem backend. |
| `browser` | Browser automation/screenshot/fetch support. |

### Tool Relay Parameters

`toolRelay` exposes PawFlow tools to CLI providers through the MCP bridge. Its
required `token` authenticates bridge connections. The optional
`auto_background_after_seconds` parameter defaults to `0`, which disables
implicit backgrounding. Set it to a positive number only when a deployment wants
long-running tool calls to return a background placeholder automatically; agents
can still request background execution explicitly with tool-specific flags such
as `bash(run_in_background=true)`. The relay-side stdio MCP proxy also has no
default initialize or `tools/call` deadline; it waits until the MCP server
responds unless a caller provides an explicit timeout. Provider clients may still
impose their own MCP tool timeout outside PawFlow, so generated provider configs
must not rely on omitting a timeout field to disable a provider default. PawFlow's
generated Codex MCP config pins `tool_timeout_sec` to `3600` seconds to avoid
Codex's short default while keeping an explicit provider-required value.

### Tool Relay Timing Logs

For CLI-provider latency debugging, the MCP bridge and tool relay emit correlated
timing lines. The bridge logs `TIMING tools/call` for MCP stdio handling and
`<- RELAY execute_tool ... bridge_ms=... send_ms=... return_wait_ms=...` for the
round trip to PawFlow. `ToolRelayService` logs `timing do_execute` for server-side
breakdown (`registry_ms`, hooks, approvals, secrets, `exec_ms`), `timing
get_registry` when registry setup is slow enough to matter (default registry,
dynamic tools, MCP discovery, filters, filesystem lookup, handler context,
delegate wiring, media wiring, filesystem list), `timing execute_done` for relay
request lifetime, and `timing ws_send` for response-frame serialization/write
time. Codex app-server also logs `timing mcpToolCall started/completed` with
provider-visible `tc_id`. Use `request_id` to correlate bridge and relay lines,
and `tc_id` to correlate provider/UI events.

Tool registries are cached per `(toolRelay service, user, conversation, agent,
file_base_url)` so a provider turn does not rebuild and refilter every handler on
each tool call. Filter updates and resource/link/package mutation tools clear the
matching cache entries before subsequent calls.

The dispatch hot path keeps read-only tools cheap: if no conversation hooks are
bound, `pre_tool_call`/`post_tool_call` execution is skipped; permission checks
read the in-memory conversation snapshot before falling back to disk; and secret
environment resolution only runs for shell/script tools or arguments that
actually reference `$VARS`. Secret environments and redaction values are kept in stable in-memory caches
after the first resolution and invalidated when secret/resource mutation tools
run, so read/search-style calls do not restat or decrypt secrets repeatedly.

## Media Services

| Type | Purpose |
|---|---|
| `openaiImageGeneration` | OpenAI-backed image generation. |
| `codexImageGeneration` | Codex CLI `$imagegen` generation/editing through a codex-app-server LLM service. |
| `grokImageGeneration` | Grok/xAI-backed image generation. |
| `grokVideoGeneration` | Grok/xAI-backed video generation. |
| `klingVideoGeneration` | Kling video generation. |
| `sunoAudioGeneration` | Suno audio/music generation. |
| `pixazoImageGeneration` | Pixazo image catalog dispatch. |
| `pixazoVideoGeneration` | Pixazo video catalog dispatch. |
| `pixazoAudioGeneration` | Pixazo audio catalog dispatch. |
| `pixazo3DGeneration` | Pixazo 3D generation. |
| `pixazoUpscale` | Pixazo image/video upscaling and background removal where supported. |
| `pixazoTryOn` | Pixazo virtual try-on. |
| `pixazoLipsync` | Pixazo lipsync. |
| `pixazoTrainer` | Pixazo model/LoRA training. |
| `wavespeedImageGeneration` | WaveSpeedAI image catalog dispatch. |
| `wavespeedVideoGeneration` | WaveSpeedAI video catalog dispatch. |
| `wavespeedAudioGeneration` | WaveSpeedAI audio catalog dispatch. |
| `wavespeed3DGeneration` | WaveSpeedAI 3D generation. |
| `wavespeedUpscale` | WaveSpeedAI image/video upscaling and background removal where supported. |
| `wavespeedTryOn` | WaveSpeedAI virtual try-on. |
| `wavespeedLipsync` | WaveSpeedAI lipsync. |
| `wavespeedTrainer` | WaveSpeedAI model/LoRA training. |
| `fishAudioVoiceClone` | Fish Audio zero-shot voice clone/TTS. |
| `elevenLabsVoiceClone` | ElevenLabs voice clone/TTS. |
| `wavespeedVoiceClone` | WaveSpeedAI zero-shot voice clone/TTS. |

See [Media Tools](media_tools.md), [Voice Clone](voice_clone.md), [Pixazo](pixazo.md), and [WaveSpeedAI](wavespeed.md).

## Server Configuration

PawFlow server configuration is service-first. Authentication, OAuth providers,
HTTP listeners, private gateway protection, summarization, LLMs, media, and
filesystem access are configured as services and referenced explicitly by flows
or agents. There is no global `llm.default.service` or `image_default_service`:
agent LLMs come from the active agent configuration, and media tools discover
compatible media services.

Untrusted skills and executable PFP objects are reviewed through the effective
conversation `summarizer` service. The summarizer points to the `llmConnection`
used for no-tool review calls. If no summarizer-backed LLM is available, package
and skill review fails closed.

The chat header admin gear is intentionally limited to objects that are not
naturally service instances: user management, temporary OAuth onboarding tokens,
and a guided view over a small manifest of global system parameters such as
`embedding_llm_service` and `PAWFLOW_USE_RTK`. Fields already owned by a service
stay in that service.

User management includes the explicit identity links used by
`IdentityService`. Admins can add, edit, or delete links such as
`github:<provider-user-id>` or `google:<provider-user-id>` for an existing
PawFlow user. A provider identity cannot be assigned to two users at once.
Users can also start the same OAuth-link flow from the chat header. PawFlow
creates a short-lived onboarding token targeted at the current user, stores it
in an HttpOnly cookie, clears the active session, and sends the browser back to
login. The next unlinked OAuth identity is linked automatically if that cookie
token is still valid, then the temporary cookie is cleared.

External OAuth login fails closed after the provider validates the browser user
unless the provider identity already resolves to an existing PawFlow user. Admins
can open the gear menu and create a one-time OAuth onboarding token with a TTL.
The token either creates a new PawFlow user with the configured role or links the
validated provider identity to a configured existing user. Tokens are stored only
as hashes and are deleted when used, when revoked, or when their TTL expires.
The login page shows the onboarding-token form only while the provider-validated
pending OAuth session still exists and at least one active onboarding token is
available; otherwise it shows only the OAuth error.

### `privateGateway`

Install a `privateGateway` service and set `httpListener.private_gateway_service_id`
to its service id. The service carries `enabled`, `secret_refs`, `skin`,
`cookie_name`, and `cookie_max_age`. `secret_refs` is a comma-separated list of
global secret names accepted by the challenge; gateway keys are no longer
discovered through a global `privategateway.*` convention.

Private gateway skins remain repository resources under
`data/repository/private_gateway_skin` and are selected by the service `skin`
field. Built-in skins include `default`, `google`, `bing`, `wifi`, `terminal`,
`netflix`, `captcha`, `matrix`, and `bladerunner`.

## Messaging Services

| Type | Purpose |
|---|---|
| `telegramBot` | Telegram receive/send integration. |
| `discordBot` | Discord receive/send integration. |
| `slackBot` | Slack receive/send integration. |
| `whatsappCloud` | WhatsApp Cloud API receive/send integration. |

## Provider and Secret Guidance

- Store provider keys through PawFlow secrets or environment variables.
- Document every service id used by examples.
- Prefer user-scoped services for personal credentials.
- Prefer global services only for shared infrastructure with explicit access control.
- Media and messaging services may send user content to external providers.

# Services Catalog

Services are reusable integrations configured by id and referenced from flows, agents, handlers, and resource definitions. They are registered through `ServiceFactory` and can be installed globally or per user depending on the resource path.

The chat service installer receives service type metadata grouped by category and renders each category as a separate section in the type selector. Services can declare `CATEGORY`; legacy services are mapped by type so the list stays grouped and sorted instead of falling back to a single alphabetical list.

## Core Services

| Type | Purpose |
|---|---|
| `llmConnection` | LLM service configuration for direct API providers and CLI-backed providers (`openai`, `openai-responses`, `anthropic`, `claude-code-interactive`, `antigravity-interactive`, `codex-interactive`, `gemini`). The `claude-code` (`cc -p`) and `codex-app-server` agent transports are legacy and remain available for existing configurations. |
| `llmAggregator` | Composite LLM service that runs several `llmConnection` advisors in parallel, then gives their internal plans to a final `llmConnection` which answers or executes the user request. |
| `llmCredentialOAuthProvider` | Encrypted OAuth credential pool for CLI-backed LLM providers. Canonical pool identifiers remain `claude-code`, `codex-app-server`, and `gemini`; the recommended `claude-code-interactive`, `codex-interactive`, and `antigravity-interactive` agent providers reuse those pools respectively. The pool names are not legacy configuration guidance. |
| `realtimeVoiceConnection` | Speech-to-speech voice sessions with an agent (webchat voice mode, Telegram voice notes). Two engines: `legacy` (default — built-in bridge; protocols `openai_realtime` for OpenAI/Azure/compatibles and `gemini_live`; credentials from a referenced `llmConnection`) and `livekit` (media via a LiveKit server + sidecar worker; providers `openai`, `gemini`, `azure_openai`, `xai`, `aws_nova`, `local_pipeline`; supports `video_input` and `modalities: audio,text,video`). With `livekit_url` left empty the stack is MANAGED: PawFlow provisions `pawflow-livekit` + `pawflow-livekit-worker` containers itself via the Docker socket (generated credentials, browser signal proxied same-origin on `/livekit`); set `livekit_url`/`livekit_api_key`/`livekit_api_secret` only for an external LiveKit server. Legacy configs map onto the LiveKit engine deterministically (`protocol`→`provider`, `vad`→`turn_detection`). Session API: `POST /api/realtime/livekit/start` / `stop`; the sidecar attaches on `/ws/realtime-worker/{session_id}` with a PawFlow-signed scoped token. See [Media Tools — Realtime Voice Conversation](media_tools.md#realtime-voice-conversation) and `docs/REALTIME_MULTIMODAL_LIVEKIT_PLAN.md`. |
| `httpClientService` | Reusable HTTP client. |
| `webSearchConnection` | Configures encrypted API keys and provider selection for the bundled paperfoot/search-cli backend. Without a usable configured provider, `web_search` uses PawFlow's concurrent no-key Bing RSS, DuckDuckGo, and static Google fallback. |
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

### LLM Aggregator

`llmAggregator` is selectable anywhere an agent accepts an LLM-capable service.
Its `aggregator_llm_service` is the only provider that streams the visible final
answer. `advisor_llm_services` is a JSON array of `llmConnection` service IDs;
the advisors run concurrently, can use the active conversation's configured
tools, and return internal planning reports. Reports are generated once for a
new user turn and reused during subsequent tool-result iterations.

Advisors are technically read-only by default. `enforce_read_only=true` exposes
only PawFlow's fail-closed read-only tool allowlist and applies the same mode to
CLI providers through their ephemeral MCP context. Set it to false only when
prompt-only guidance with every configured tool is explicitly desired.
Use `failure_policy=best_effort` to continue when one advisor fails, or
`fail_fast` to abort before calling the final LLM.

For setup, operating behavior, cost accounting, and troubleshooting, see the
[Multi-LLM Aggregator guide](llm_aggregator.md).

### Web Search Connection

`webSearchConnection` enables the `web_search` tool to use the `search-cli`
binary bundled in the PawFlow **server image**. The binary is not installed in
the relay image and API keys are never sent to a relay. PawFlow resolves service
definitions by scope (conversation, then user, then global), decrypts the
selected service configuration on the server, and injects only that service's
keys into one isolated `search-cli` process.

#### Configure the service

1. Open the webchat `Services` panel and add a service.
2. Select `Web Search Connection` (`webSearchConnection`) in the Network
   category.
3. Give it a stable id, for example `web-search`, and choose its scope. Use a
   conversation scope for one conversation, user scope for all of one user's
   conversations, or global scope for an administrator-managed shared service.
4. Add at least one provider key from the table below.
5. Optionally set `providers` to a comma-separated allowlist such as
   `brave,serper,exa`. Leaving it empty enables every provider for which the
   service contains a key.
6. Keep `default_mode=general` for ordinary web searches, set `timeout` to the
   per-provider deadline in seconds, and keep `fallback_to_free=true` unless an
   API failure must be returned without using the no-key backend.

All `*_api_key` fields are sensitive service parameters: the UI renders them
as password inputs and PawFlow encrypts them at rest. Do not put provider keys
in global environment variables or in `search-cli` configuration files. The
service removes inherited provider-key variables before each invocation,
disables local search logs, and uses temporary XDG config/cache/data directories
that are deleted after the call.

#### Obtain provider API keys

Create keys in the provider's official account or API console. Only configure
providers that you intend to bill; `search-cli` calls enabled providers in
parallel for modes that support them.

| PawFlow field | Obtain the key | Main uses in `search-cli` |
|---|---|---|
| `parallel_api_key` | Create a Parallel account and follow the official [Search API quickstart](https://docs.parallel.ai/search/search-quickstart). | `general`, `news`, `deep` |
| `brave_api_key` | Subscribe to the Search API and create a key in the [Brave Search API dashboard](https://api-dashboard.search.brave.com/). | Independent web/news index, `general`, `news`, `deep` |
| `serper_api_key` | Sign in at [Serper](https://serper.dev/) and copy the API key shown in the dashboard. | Google web/news results, Scholar, patents, images, places |
| `exa_api_key` | Create a key in the [Exa API dashboard](https://dashboard.exa.ai/api-keys). | Semantic search, papers, people, similar pages |
| `jina_api_key` | Create a token in the [Jina API dashboard](https://jina.ai/api-dashboard/). | General search and URL-to-markdown extraction |
| `linkup_api_key` | Create a free Linkup account from the [Linkup API documentation](https://docs.linkup.so/) and copy the key issued in its dashboard. | Accuracy-focused `general`, `news`, `deep` searches |
| `firecrawl_api_key` | Create a key in the [Firecrawl API Keys page](https://www.firecrawl.dev/app/api-keys). | Search and JavaScript-rendered extraction |
| `tavily_api_key` | Create and manage keys in the [Tavily API platform](https://app.tavily.com/home). | General, news, academic and deep research |
| `serpapi_api_key` | Create an account and copy the key from [SerpApi's API key page](https://serpapi.com/manage-api-key). | Scholar and multi-engine specialist results |
| `perplexity_api_key` | Add API credits and create a key in the [Perplexity API console](https://console.perplexity.ai/); see its [key management guide](https://docs.perplexity.ai/docs/admin/api-key-management). | Web-grounded answers with citations |
| `browserless_api_key` | Create an account and manage the token in the Browserless dashboard; see [API Token Management](https://docs.browserless.io/overview/api-keys). | Cloud-browser fallback for difficult extraction pages |
| `xai_api_key` | Create an account, add credits, and generate a key in the [xAI Console](https://console.x.ai/); see the [xAI quickstart](https://docs.x.ai/developers/quickstart). | Real-time X/Twitter search in `social` and `deep` modes |

Provider signup terms, free credits, quotas, and prices can change. Check the
linked provider console before enabling a key. A single provider key is enough;
multiple keys improve coverage and let `search-cli` rank-fuse results.

#### Modes and tool usage

The service defaults to `general`. Other query modes include `news`,
`academic`, `scholar`, `deep`, `people`, `social`, `patents`, `images`, and
`places`. The `similar`, `extract`, and `scrape` modes expect a URL rather than a
text query. A mode only calls configured providers that support that mode.

Normally the agent calls `web_search` with only a query:

```json
{"query": "PawFlow release notes", "max_results": 8}
```

Use a named service or restrict its paid providers when needed:

```json
{
  "query": "recent agent orchestration research",
  "service": "web-search",
  "mode": "academic",
  "search_cli_providers": "exa,tavily",
  "max_results": 10
}
```

`search_cli_providers` applies only to the server-side service and must name
providers whose keys exist in that service. The separate `provider` parameter
selects the built-in no-key fallback (`bing`, `duckduckgo`, or `google`).

#### Free mode and fallback

`search-cli` 0.9.0 has no keyless general-search provider. Its keyless
`stealth` capability extracts a known URL and is not a search engine; moreover,
the PawFlow Linux build intentionally disables that unsupported feature.
PawFlow therefore retains its own no-key backend:

- Bing RSS, DuckDuckGo HTML, and static Google are launched concurrently;
- one global deadline bounds the fallback (8 seconds by default);
- Chromium is disabled on the normal path and only runs when
  `browser_fallback=true` is explicitly requested;
- when a connected relay is available, only this no-key fallback may execute
  there. The bundled `search-cli` binary and provider keys stay on the PawFlow
  server.

With `fallback_to_free=true`, PawFlow uses this backend when no provider key is
configured, the server binary is unavailable, the selected paid providers fail,
or `search-cli` returns no usable result. The result includes a fallback note so
an API outage is not silent. Set `fallback_to_free=false` to make a
`search-cli` failure explicit instead.

`authGateway` supports standard OAuth providers through `/auth/callback` and
Telegram through the Telegram Login Widget. Telegram requires a BotFather bot
token and bot username; its signed callback data is validated by the gateway and
then follows the same explicit identity link or OAuth onboarding token flow as
the other external providers.

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

### Killing a Single Tool Call

The `kill_tool` action targets one `tc_id`: `ToolRelayService.cancel_request()`
matches the in-flight relay request by its `cc_tc_id`, so killing one tool in a
parallel batch does not stop its siblings.

A targeted miss is ambiguous, and the difference matters. A request dispatched
before the provider stream published its `tool_call` id carries no `cc_tc_id`
(see `bind_pending_cc_tc`), so it is running but unmatchable — widening the kill
to the whole agent is the only way to stop it. A miss because the call already
finished means there is nothing to kill, and widening would cancel unrelated
live work instead. `has_unbound_inflight()` separates the two: the broad
`cancel_agent()` fallback runs only while some in-flight request is still
unbound. Otherwise `kill_tool` returns `ok: false` with `reason:
"not_in_flight"` rather than killing a bystander.

### A Running Call Still Looks Running After a Reload

"Running" used to exist only in the live SSE stream: `live: true` was set at
one place, on the event that announced the call. A view rebuilt from the
transcript — a reload, a conversation switch, a load-more page — renders a
call whose result has not been written yet as an ordinary finished row: no
pending bullet, no BG, no Kill. The call was running the whole time, and the
two buttons that could act on it were the ones missing.

The relay already knew. `_inflight` carries `conv`, `agent`, `tool_name`,
`cc_tc_id`, `bg_tc_id` and `started_at` for every executing request, but the
table was write-only (`background_by_tc_id`, `cancel_*`,
`has_unbound_inflight`). `inflight_snapshot(conversation_id, agent_name="")`
is its read side, and `load_history` consumes it:

- the page carries `active_tool_calls`, one entry per running request;
- every `tool_call` row whose `tc_id` is in flight is stamped `live`, which is
  the flag the renderer already keys on (`messages_render.js`) — so a replayed
  row is drawn exactly like a streamed one, in both views;
- a row that already carries its result is never stamped, whatever the table
  still says: a result written between the page read and the snapshot wins;
- the match is on the **root** conversation id, so a call running in a
  `::task::`/`::delegate::` sub-conversation hydrates in the parent view where
  its row is rendered;
- an entry with an empty `tc_id` is a request whose provider id is not bound
  yet: genuinely in flight, but no row can be addressed for it, so nothing is
  marked. It is still reported, because it is what `has_unbound_inflight`
  reasons about above.

This is provider-agnostic — the defect was in hydration, not in any one
provider's stream.

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
| `comfyUIImageGeneration` | Self-hosted ComfyUI image generation/editing through trusted API-format workflow presets. |
| `grokImageGeneration` | Grok/xAI-backed image generation and editing. |
| `grokVideoGeneration` | Grok/xAI-backed video generation and editing. |
| `comfyUIVideoGeneration` | Self-hosted ComfyUI video operations through trusted API-format workflow presets. |
| `xaiTTS` | xAI-backed text-to-speech. |
| `xaiSTT` | xAI-backed speech-to-text. |
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
| `tripo3DGeneration` | Native Tripo3D API: text/image-to-3D, rigging, animation retargeting, retexture, convert, stylize. |
| `meshy3DGeneration` | Native Meshy AI API: text/image-to-3D, rigging, animation, retexture. |
| `fishAudioVoiceClone` | Fish Audio zero-shot voice clone/TTS. |
| `elevenLabsVoiceClone` | ElevenLabs voice clone/TTS. |
| `wavespeedVoiceClone` | WaveSpeedAI zero-shot voice clone/TTS. |

See [Media Tools](media_tools.md), [ComfyUI](comfyui.md), [Voice Clone](voice_clone.md), [Pixazo](pixazo.md), and [WaveSpeedAI](wavespeed.md).

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
The token either creates a new PawFlow user as `user`/`admin` or links the
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

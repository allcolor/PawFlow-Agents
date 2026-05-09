# Services Catalog

Services are reusable integrations configured by id and referenced from flows, agents, handlers, and resource definitions. They are registered through `ServiceFactory` and can be installed globally or per user depending on the resource path.

The chat service installer receives service type metadata grouped by category and renders each category as a separate section in the type selector. Services can declare `CATEGORY`; legacy services are mapped by type so the list stays grouped and sorted instead of falling back to a single alphabetical list.

## Core Services

| Type | Purpose |
|---|---|
| `llmConnection` | Direct API LLM service configuration. |
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

## Filesystem and Relay Services

| Type | Purpose |
|---|---|
| `relay` | WebSocket relay to a user machine via server relay or standalone relay client. |
| `toolRelay` | Tool relay/MCP bridge for containerized scripts and CLI providers. |
| `filesystem` | Server-side filesystem service. |
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
as `bash(run_in_background=true)`.

## Media Services

| Type | Purpose |
|---|---|
| `openaiImageGeneration` | OpenAI-backed image generation. |
| `codexImageGeneration` | Codex CLI `$imagegen` generation/editing through a codex-app-server LLM service. |
| `grokImageGeneration` | Grok/xAI-backed image generation. |
| `grokVideoGeneration` | Grok/xAI-backed video generation. |
| `klingVideoGeneration` | Kling video generation. |
| `soraVideoGeneration` | Sora video generation. |
| `sunoAudioGeneration` | Suno audio/music generation. |
| `pixazoImageGeneration` | Pixazo image catalog dispatch. |
| `pixazoVideoGeneration` | Pixazo video catalog dispatch. |
| `pixazoAudioGeneration` | Pixazo audio catalog dispatch. |
| `pixazo3DGeneration` | Pixazo 3D generation. |
| `pixazoUpscale` | Pixazo image/video upscaling and background removal where supported. |
| `pixazoTryOn` | Pixazo virtual try-on. |
| `pixazoLipsync` | Pixazo lipsync. |
| `pixazoTrainer` | Pixazo model/LoRA training. |
| `fishAudioVoiceClone` | Fish Audio zero-shot voice clone/TTS. |
| `elevenLabsVoiceClone` | ElevenLabs voice clone/TTS. |

See [Media Tools](media_tools.md), [Voice Clone](voice_clone.md), and [Pixazo](pixazo.md).

## Server Configuration

PawFlow server configuration is service-first. Authentication, OAuth providers,
HTTP listeners, private gateway protection, summarization, LLMs, media, and
filesystem access are configured as services and referenced explicitly by flows
or agents. There is no global `llm.default.service` or `image_default_service`:
agent LLMs come from the active agent configuration, and media tools discover
compatible media services.

The chat header admin gear is intentionally limited to objects that are not
naturally service instances: user management and a guided view over a small
manifest of global system parameters such as `embedding_llm_service` and
`PAWFLOW_USE_RTK`. Fields already owned by a service stay in that service.

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

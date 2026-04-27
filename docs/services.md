# Services Catalog

Services are reusable integrations configured by id and referenced from flows, agents, handlers, and resource definitions. They are registered through `ServiceFactory` and can be installed globally or per user depending on the resource path.

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
| `dbConnectionPool` | SQL connection pooling. |
| `cacheService` | Local cache service. |
| `distributedMapCache` | Distributed key/value cache. |
| `fileTracking` | Tracks processed files for list/watch flows. |

## Filesystem and Relay Services

| Type | Purpose |
|---|---|
| `relay` | WebSocket relay to a user machine, PawCode, VS Code, or relay process. |
| `toolRelay` | Tool relay/MCP bridge for containerized scripts and CLI providers. |
| `filesystem` | Server-side filesystem service. |
| `googleDrive` | Google Drive filesystem backend. |
| `oneDrive` | OneDrive filesystem backend. |
| `browser` | Browser automation/screenshot/fetch support. |

## Media Services

| Type | Purpose |
|---|---|
| `openaiImageGeneration` | OpenAI-backed image generation. |
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

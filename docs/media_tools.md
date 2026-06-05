# Media and Multimodal Tools

PawFlow agents can create, inspect, edit, and transform media through provider-backed tools. Generated files are written to FileStore by default and returned as `fs://filestore/<id>/<name>` URLs.

## Storage Destinations

Most media tools accept:

- `destination`: `filestore` by default, or a relay filesystem service name;
- `path`: output filename/path when writing to a filesystem service;
- provider-specific model overrides such as `model`.

`fs://filestore/...` inputs are rewritten to HTTP URLs when a provider needs to fetch the file.

## Image Tools

| Tool | Purpose |
|---|---|
| `generate_image` | Create a raster image from a prompt. Supports width/height, format, aspect ratio, style, and model override. |
| `edit_image` | Edit one or more source images using an instruction prompt. |
| `describe_image` | Return a natural-language description of an image. |
| `remix_image` | Generate a new image inspired by a source image and prompt. |
| `remove_background` | Produce a transparent-background PNG from an input image. |
| `upscale_image` | Upscale an image by a supported factor/model. |

Example:

```json
{
  "prompt": "A clean product icon for a workflow automation app",
  "width": 512,
  "height": 512,
  "format": "png"
}
```

## Video Tools

| Tool | Purpose |
|---|---|
| `generate_video` | Text-to-video, image-to-video, video edit, or frame-to-video depending on provided inputs. |
| `upscale_video` | Upscale or enhance an existing video. |
| `lipsync` | Drive a face video or image with an audio track. |
| `speech_to_video` | Generate a lip-synced video from a face image and audio track. |

Example chain:

1. `clone_voice` from a permitted reference sample.
2. `speak` to synthesize narration.
3. `generate_image` for a face/character or provide an existing image.
4. `speech_to_video` with the image and synthesized audio.

## Audio and Voice Tools

| Tool | Purpose |
|---|---|
| `generate_audio` | Generate music, sound, or text-to-audio depending on active service/model. |
| `clone_voice` | Register or reuse a provider voice from a reference sample when the selected TTS provider supports voice creation. |
| `speak` | Synthesize speech through the active TTS provider using either a registered PawFlow voice alias or a provider-native voice name/id. |
| `delete_voice` | Remove local voice clone state, cached TTS renders, and provider voice id where applicable. |
| `stt_transcribe` | UI action that transcribes browser microphone audio through the active STT provider. |

`speak` is the single text-to-speech entry point. OpenAI-compatible TTS,
Supertonic, Pixazo, WaveSpeed, VoxCPM, ElevenLabs, Fish Audio, and other
compatible providers all expose speech through the same tool. Use `clone_voice`
only when the provider needs or supports a stored voice resource; only clone
voices when the user has explicit rights to use the speaker's voice.

The webchat header includes a speaker toggle that reads new agent messages as
they stream in. It calls the silent UI action `tts_synthesize`, which delegates
to `speak` and returns an audio URL without adding `tool_call` or `tool_result`
messages to the conversation. The button is hidden until at least one compatible
TTS service is configured. With one service it toggles immediately. With several
services, the conversation default is used when set; otherwise the button opens a
service picker before playback. The picker can also set or reset the conversation
default. Optional advanced overrides for provider-native voice/language remain
available as `pawflow_tts_voice` and `pawflow_tts_language`, otherwise the
selected service's configured defaults are used.

Webchat playback audio is transient. `tts_synthesize` passes an internal storage
TTL to `speak`, and the browser calls `tts_delete` as soon as playback ends,
fails, is skipped, or live speech is stopped. The TTL is only a short fallback
for interrupted browser sessions; it is not the normal cleanup path. Transient
webchat TTS files are hidden from the conversation FileStore list while they wait
for playback cleanup or TTL expiry.
Agent-facing `speak` calls remain durable by default because their URLs are
often reused by media tools such as `lipsync` and `speech_to_video`.

Assistant messages also expose a per-message read button. It uses the same TTS
service picker as live speech when several TTS services are configured, then
synthesizes only that message and deletes the transient audio after playback.

The webchat input also includes a microphone button when an STT provider is
configured. The browser records audio with `MediaRecorder`, sends it to the
silent UI action `stt_transcribe`, writes the returned transcript into the chat
input, and auto-sends only when the input was empty when recording started. With
one STT service it records immediately. With several services, the conversation
default is used when set; otherwise the button opens a service picker. The picker
can also set or reset the conversation default. Optional advanced overrides are
`pawflow_stt_language` and `pawflow_stt_auto_send`.
After service discovery or selection, webchat sends a silent `stt_warmup` action
so providers with a warmup hook can start their local daemon and load speech
models before the first recording is submitted.

Webchat STT audio is not persisted to FileStore. The browser sends the captured
blob directly as base64, and any server-side conversion files are temporary and
unlinked after conversion/transcription. When PawFlow stages the decoded audio for
a provider, it uses a hidden transient FileStore entry and deletes it in the same
`stt_transcribe` request after the provider returns or fails. STT services can
declare that they accept browser-native `MediaRecorder` formats; those services
receive the original `webm`/`ogg`/Opus payload instead of a pre-converted WAV.

`openaiCompatibleSTT` is the generic HTTP transcription provider for OpenAI-style
`POST /audio/transcriptions` endpoints. It supports OpenAI, Groq, local
whisper.cpp/OpenAI-compatible servers, and relay-routed local URLs such as
`http://${conv.relay}/localhost:1234/v1`. `api_key` is optional so trusted local
or relay endpoints can be used without bearer authentication. Direct private,
loopback, link-local, multicast, reserved, or unresolved DNS targets are blocked
by default to avoid server-side request forgery from service configuration. Use
the `${conv.relay}` URL form for local relay endpoints; set
`allow_private_base_url=true` only when the endpoint is trusted and must be
reached directly from the PawFlow server.

`openaiCompatibleImageGeneration` and `openaiCompatibleVideoGeneration` reuse an
existing `llmConnection` whose provider is `openai`. Configure that LLM service
with a bare OpenAI base URL such as `https://api.openai.com/v1`, or an
OpenRouter/OpenAI-compatible base URL such as `https://openrouter.ai/api/v1`.
The direct `openaiImageGeneration` provider supports both `generate_image` and
`edit_image` against OpenAI's images API. `edit_image` sends multipart image
inputs to `POST /images/edits`, accepts `fs://filestore/...` sources directly,
and returns the edited image to the requested PawFlow storage destination.
The image service supports `protocol=auto`, `openai_images`, and
`chat_completions`/`openrouter`: bare OpenAI image models normally use
`POST /images/generations`, while OpenRouter image models use chat completions
with `modalities=["image"]` and provider-specific response parsing. The video
service supports `protocol=auto`, `openai_video`, `openrouter`, and the legacy
`chat_completions` fallback: bare OpenAI-compatible video providers use the
configurable `submit_path` and `status_path_template`, while OpenRouter video
models such as `google/veo-3.1` use `POST /videos` and poll the returned
`polling_url` or `openrouter_generation_path_template`. Both services expose
`max_tokens` and `max_output_tokens` for chat-completions media responses, plus
provider escape hatches through `extra_body` and `extra_headers`.

`grokImageGeneration` and `grokVideoGeneration` call xAI directly at
`https://api.x.ai/v1`. The image service defaults to
`grok-imagine-image-quality`, supports text generation and `edit_image`, and
accepts up to three reference images for edit requests. The video service uses
`grok-imagine-video` for text-to-video, image-to-video, reference-to-video,
video edit, and video extension; `generate_video(..., video_mode="extend",
video_url="...")` selects the extension endpoint. `xaiTTS` and `xaiSTT` expose
xAI's direct `/v1/tts` and `/v1/stt` audio APIs.

## Provider Webhooks

Some media providers can POST the final async result to PawFlow instead of being
polled. PawFlow exposes this as an opt-in service setting only for providers
whose callback field is known and tested. `pixazo*` services support
`use_webhook=true`; PawFlow sends Pixazo an `X-Webhook-URL` header and waits for
the temporary callback route instead of polling the status URL. WaveSpeed media
services also support `use_webhook=true`; PawFlow passes the one-shot route as
WaveSpeedAI's `webhook` query parameter and reads the final URL from the callback
payload's `outputs[]` fields. `openaiCompatibleVideoGeneration` supports
`use_webhook=true` for async video endpoints that accept `callback_url`,
including OpenRouter `POST /videos` and `protocol=openai_video` providers. The
OpenAI-compatible TTS, STT, image, and chat-completions media paths remain
synchronous or streaming request/response APIs; they keep polling or parsing the
immediate response because they do not expose a per-request callback contract.

Webhook mode requires the PawFlow HTTP listener to be reachable from the public
internet through HTTPS. Configure `public_callback_base_url` on the media service
or rely on the agent `file_base_url`; the value should be the public root of the
PawFlow server, for example `https://webchat.example.org`. Reverse proxies must
route the whole host to PawFlow, not only `/chat`, so provider POSTs such as
`https://webchat.example.org/webhooks/media/pixazo/<token>` reach the listener.
`localhost`, `127.0.0.1`, Docker-internal hostnames, and LAN/private IPs cannot
receive callbacks from external providers.

Each webhook URL contains a high-entropy one-shot token and is registered as a
public route only for the lifetime of the media job. The route bypasses session
auth because providers cannot hold a PawFlow browser session, but the token is
unpredictable and the route is removed after success, failure, cancellation, or
timeout.

Relay-aware provider URLs use one standard shape everywhere:
`http(s)://<relay_id>/<host>:<port>/<path>`. The first path segment containing
`host:port` marks the URL as a PawFlow relay URL. `${conv.relay}` is only the
standard expression shortcut for the conversation default relay, so
`http://${conv.relay}/localhost:7788` and
`http://fs_quentin.anciaux_f4a302e1/localhost:7788` follow the same parser and
route creation path. The URL scheme is the protocol used by the relay to reach
the target service; the protocol used to enter PawFlow's `/relay-proxy/...`
listener comes from the HTTP listener configuration.

`voxcpmTTS` is an external VoxCPM client. PawFlow does not install, start, or
stop VoxCPM; the user runs their own VoxCPM runtime on the PawFlow server or on
a relay machine. The default `api_mode=openai` calls vLLM-Omni's
OpenAI-compatible `POST /v1/audio/speech` endpoint with `model`, `input`,
`voice`, and `response_format`. The response may be raw audio bytes
(`audio/wav` recommended) or JSON containing `audio_base64` and optional
`content_type`. Use `api_mode=cli` for VoxCPM voice cloning; it runs the
official `voxcpm design` and `voxcpm clone` commands and returns the generated
audio to the same PawFlow `speak` / `clone_voice` persistence layer.

Heavy local services can implement a `prepare_install(reporter)` hook. During
`/service install`, PawFlow runs this hook before registering the service and
publishes `service_install_progress` events to the webchat so users see the
current phase. Voicebox uses the hook to check `git`, Python `venv`, WSL package
availability when applicable, prepare its checkout/venv, and optionally preload
the configured Whisper STT model. Supertonic uses the same hook to prepare a
managed Python runtime before first use. PawFlow persists the latest install
state for each service (`not_installed`, `installing`, `ready`, `failed`, or
`cancelled`), includes it in `list_services`, writes a JSONL install log, rejects
duplicate concurrent installs for the same service, and exposes
`service_install_status`, `service_install_log`, and best-effort
`service_install_cancel` actions for UI retry/debug flows. Pass `download=true`
to `service_install_log` from a conversation context to export the log as a
FileStore JSON artifact.

## 3D, Try-On, Training

| Tool | Purpose |
|---|---|
| `generate_3d` | Generate a GLB/GLTF/OBJ/USDZ model from an image or prompt. |
| `try_on` | Virtual try-on: dress a person image with a garment image. |
| `train_image_model` | Submit a dataset to train/fine-tune an image model/LoRA where supported. |

## Provider Services

Media tools are provider-agnostic. They resolve the active service at runtime. Every media and capability tool accepts an optional `service` parameter to force a specific service id for that call. Type-specific aliases are also accepted where relevant, such as `image_service`, `video_service`, `audio_service`, and `voice_service`.

When `service` is omitted, selection is deterministic:

1. if exactly one compatible service is deployed, use it;
2. otherwise use the per-agent or wildcard default stored for that media family;
3. otherwise use the first compatible service returned by the registry;
4. if no compatible service exists, return an error.

Supported service families include:

- `openaiImageGeneration`
- `openaiCompatibleImageGeneration`
- `openaiCompatibleVideoGeneration`
- `codexImageGeneration`
- `grokImageGeneration`
- `grokVideoGeneration`
- `xaiTTS`
- `xaiSTT`
- `klingVideoGeneration`
- `sunoAudioGeneration`
- `supertonicTTS`
- `openaiCompatibleTTS`
- `openaiCompatibleSTT`
- `voicebox`
- `luxTTS`
- `pixazoImageGeneration`
- `pixazoVideoGeneration`
- `pixazoAudioGeneration`
- `pixazo3DGeneration`
- `pixazoUpscale`
- `pixazoTryOn`
- `pixazoLipsync`
- `pixazoTrainer`
- `wavespeedImageGeneration`
- `wavespeedVideoGeneration`
- `wavespeedAudioGeneration`
- `wavespeed3DGeneration`
- `wavespeedUpscale`
- `wavespeedTryOn`
- `wavespeedLipsync`
- `wavespeedTrainer`
- `fishAudioVoiceClone`
- `elevenLabsVoiceClone`
- `wavespeedVoiceClone`

For Pixazo model-specific schemas and pricing notes, see [Pixazo](pixazo.md). For WaveSpeedAI model-specific schemas and pricing notes, see [WaveSpeedAI](wavespeed.md). For registered voice internals, see [Voice Clone](voice_clone.md).

### OpenAI-Compatible Text-to-Speech Service

`openaiCompatibleTTS` calls the OpenAI-compatible `POST /audio/speech` API and
returns the generated audio bytes to PawFlow's `speak` pipeline. The default
configuration targets `https://api.openai.com/v1` with model `gpt-4o-mini-tts`,
voice `coral`, and `mp3` output. Configure `api_key` for OpenAI or compatible
hosted providers, or leave it empty for trusted local relay-routed endpoints.
The service supports provider-native `voice`, `instructions`, `response_format`,
`speed`, and per-call overrides passed through `speak`.

For OpenRouter TTS, set `base_url` to `https://openrouter.ai/api/v1`, use an
OpenRouter model slug such as `openai/gpt-4o-mini-tts-2025-12-15`, and set the
OpenRouter API key in `api_key`. OpenRouter provider-specific options can be
passed through `provider_options` as JSON, for example
`{"openai":{"instructions":"Speak in a warm, friendly tone."}}`.

### Suno Audio Service

`sunoAudioGeneration` sends Suno's required `callBackUrl` on generation requests. Configure the service `callback_url` when the PawFlow server has a public webhook URL. If `callback_url` is omitted, the tool derives `callBackUrl` from the runtime `file_base_url` as `/webhooks/suno/callback` and still polls Suno's `GET /api/v1/generate/record-info?taskId=...` endpoint before returning generated audio files.

### Supertonic Local TTS Service

`supertonicTTS` manages a local Supertonic 3 HTTP daemon and is intended for
fast, private, on-device text-to-speech. PawFlow installs Supertonic through its
Python requirements and starts the package's `supertonic serve` entrypoint
automatically when the service connects. If the managed runtime is missing and
`auto_start` plus `auto_install` are enabled, first use prepares the local
virtualenv before starting the daemon. The webchat TTS warmup action starts the
daemon and asks Supertonic for a discarded short WAV before the first audible
response when possible, so the local model is already loaded.

Configure the service with `base_url` (default `http://127.0.0.1:7788`),
`auto_start` (default `true`), `startup_timeout`, `voice` (`M1`-`M5`, `F1`-`F5`,
or an imported style name), `lang` (`fr`, `en`, `ja`, `ko`, `na`, ...), `steps`,
`speed`, and `response_format` (`wav`, `flac`, or `ogg`). Prefer `speak` for
speech, for example `speak(text="Bonjour", service="supertonic", voice="F1",
language="fr")`. `generate_audio` also works with `prompt` as the spoken text
for compatibility with audio-generation flows. Supertonic's open-weight local
package does not create voice styles directly from raw audio; custom voices
require a Voice Builder JSON imported into the managed Supertonic daemon.

### Voicebox Local Voice I/O Service

`voicebox` bridges a managed local Voicebox server (default
`http://127.0.0.1:17493`) as a PawFlow STT, TTS, and voice-clone provider. It
uses Voicebox's `/transcribe` endpoint for browser dictation and Voicebox's TTS
endpoints for speech generation. Configure `client_id`, `stt_model`, and
`default_profile` to match Voicebox's local profile and MCP/client bindings.
Voicebox accepts browser-native microphone payloads, so PawFlow forwards
`MediaRecorder` audio directly instead of transcoding it to WAV first. The
`stt_warmup` action asks Voicebox to transcribe a discarded silent WAV once per
service instance, which loads the configured Whisper model in the live backend.

Like Supertonic, the service starts lazily on first use. With `auto_start=true`
it first probes the local API, then opens the installed macOS Voicebox app when
available, then starts a backend from `install_dir`, and with `auto_install=true`
it can clone/setup Voicebox into `data/runtime/voicebox` before starting
`backend.main:app` through the checkout's virtualenv. Auto-install uses
`repo_url` (default `https://github.com/jamiepine/voicebox.git`) and checks out
the pinned `repo_ref` commit before running dependency setup, so default installs
are reproducible instead of tracking upstream `HEAD`. `start_command` can
override the managed command for packaged deployments. Voicebox voice cloning is
profile-based: PawFlow can speak through Voicebox profiles by name/id. Preset
profiles can be created or updated directly from the service edit form with the
`profile_*` fields and the `Save Voicebox profile` action; if `default_profile`
names a known preset such as Kokoro `Siwis` or its `preset_voice_id` such as
`ff_siwis`, PawFlow resolves or creates the matching Voicebox preset profile
before first speech. Cloned-profile sample management remains in Voicebox. When
the service is disabled or disconnected
and `auto_start=true` targets a loopback endpoint, PawFlow calls Voicebox's
`/shutdown` endpoint so a subsequent enable starts a fresh backend process from
the current managed checkout instead of reusing stale imported code.

### LuxTTS Local Voice Clone Service

`luxTTS` exposes LuxTTS as a local zero-shot TTS and voice-clone service. For
plain `speak`, configure `prompt_audio` as the default voice reference. For
PawFlow `clone_voice`/`speak` flows, LuxTTS receives the registered reference
audio on each synthesis call and returns WAV audio. Configure `model_id`,
`device`, `threads`, `num_steps`, `t_shift`, `speed`, `rms`, and `ref_duration`.
The Python LuxTTS dependencies must be installed in the runtime environment.

### Codex CLI Image Service

`codexImageGeneration` runs a fresh isolated `codex exec` job through PawFlow's server-side Codex CLI Docker pool and asks Codex to use the built-in `$imagegen` skill. It is not tied to a PawFlow agent conversation and does not expose a relay, local, or binary path knob. Authentication and provider settings come from the selected `llmConnection` service.

Recommended config:

```json
{
  "service_type": "codexImageGeneration",
  "config": {
    "llm_service": "codex_appserver_llm_service",
    "timeout": 900
  }
}
```

The `llm_service` field is a service selector filtered to `llmConnection` services whose `provider` is `codex-app-server`. The image job reuses that service's Codex OAuth credential pool or API-key fallback, then runs in `data/runtime/sessions/codex/<user>/_image_generation/<job>/` inside the common CLI Docker image.

For generation, the service runs a prompt equivalent to `codex exec "... $imagegen"`. For editing, source images are copied into the job directory and passed with repeated `-i` / `--image` inputs. `fs://filestore/<id>/<name>` references stay local for this service and are read from FileStore directly instead of being rewritten to HTTP. The installed Codex CLI currently supports image inputs and `$imagegen`; it does not expose a stable `--image-dir` flag, so output collection is handled by reading `output.*` first and falling back to `$CODEX_HOME/generated_images`.

## Flow Usage

Every media tool can be used in flows through `tool.<name>` tasks, for example `tool.generate_image` or `tool.speech_to_video`. See [Task Catalog](tasks.md#tool-tasks-tool-prefix).

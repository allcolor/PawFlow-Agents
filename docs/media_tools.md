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

`speak` is the single text-to-speech entry point. Supertonic, Pixazo, WaveSpeed,
ElevenLabs, Fish Audio, and other compatible providers all expose speech through
the same tool. Use `clone_voice` only when the provider needs or supports a
stored voice resource; only clone voices when the user has explicit rights to use
the speaker's voice.

The webchat header includes a speaker toggle that reads new agent messages as
they stream in. It calls the silent UI action `tts_synthesize`, which delegates
to `speak` and returns an audio URL without adding `tool_call` or `tool_result`
messages to the conversation. The button is hidden until at least one compatible
TTS service is configured. With one service it toggles immediately; with several
services it opens a service picker before playback. The selected service is
remembered in local storage as `pawflow_tts_service`; optional advanced overrides
for provider-native voice/language remain available as `pawflow_tts_voice` and
`pawflow_tts_language`, otherwise the selected service's configured defaults are
used.

The webchat input also includes a microphone button when an STT provider is
configured. The browser records audio with `MediaRecorder`, sends it to the
silent UI action `stt_transcribe`, writes the returned transcript into the chat
input, and auto-sends only when the input was empty when recording started. The
selected STT service is remembered as `pawflow_stt_service`; optional advanced
overrides are `pawflow_stt_language` and `pawflow_stt_auto_send`.

`openaiCompatibleSTT` is the generic HTTP transcription provider for OpenAI-style
`POST /audio/transcriptions` endpoints. It supports OpenAI, Groq, local
whisper.cpp/OpenAI-compatible servers, and relay-routed local URLs such as
`https://${convrelay}/localhost:1234/v1`. `api_key` is optional so trusted local
or relay endpoints can be used without bearer authentication.

Heavy local services can implement a `prepare_install(reporter)` hook. During
`/service install`, PawFlow runs this hook before registering the service and
publishes `service_install_progress` events to the webchat so users see the
current phase. Voicebox uses the hook to check `git`, Python `venv`, WSL package
availability when applicable, prepare its checkout/venv, and optionally preload
the configured Whisper STT model. Supertonic uses the same hook to prepare a
managed Python runtime before first use.

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
- `codexImageGeneration`
- `grokImageGeneration`
- `grokVideoGeneration`
- `klingVideoGeneration`
- `soraVideoGeneration`
- `sunoAudioGeneration`
- `supertonicTTS`
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

### Suno Audio Service

`sunoAudioGeneration` sends Suno's required `callBackUrl` on generation requests. Configure the service `callback_url` when the PawFlow server has a public webhook URL. If `callback_url` is omitted, the tool derives `callBackUrl` from the runtime `file_base_url` as `/webhooks/suno/callback` and still polls Suno's `GET /api/v1/generate/record-info?taskId=...` endpoint before returning generated audio files.

### Supertonic Local TTS Service

`supertonicTTS` manages a local Supertonic 3 HTTP daemon and is intended for
fast, private, on-device text-to-speech. PawFlow installs Supertonic through its
Python requirements and starts the package's `supertonic serve` entrypoint
automatically when the service connects.

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
uses Voicebox's `/transcribe` endpoint for browser dictation and `/speak` for
speech generation. Configure `client_id`, `stt_model`, and `default_profile` to
match Voicebox's local profile and MCP/client bindings.

Like Supertonic, the service starts lazily on first use. With `auto_start=true`
it first probes the local API, then opens the installed macOS Voicebox app when
available, then starts a backend from `install_dir`, and with `auto_install=true`
it can clone/setup Voicebox into `data/runtime/voicebox` before starting
`backend.main:app` through the checkout's virtualenv. Auto-install uses
`repo_url` (default `https://github.com/jamiepine/voicebox.git`) and checks out
the pinned `repo_ref` commit before running dependency setup, so default installs
are reproducible instead of tracking upstream `HEAD`. `start_command` can
override the managed command for packaged deployments. Voicebox voice cloning is
profile-based: PawFlow can speak through existing Voicebox profiles by name/id,
while profile creation and sample management remain in Voicebox.

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

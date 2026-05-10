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
| `clone_voice` | Register or reuse a voice clone from a reference sample. |
| `speak` | Synthesize speech using a registered voice clone. |
| `delete_voice` | Remove local voice clone state, cached TTS renders, and provider voice id where applicable. |

Only clone voices when the user has explicit rights to use the speaker's voice.

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
- `pixazoImageGeneration`
- `pixazoVideoGeneration`
- `pixazoAudioGeneration`
- `pixazo3DGeneration`
- `pixazoUpscale`
- `pixazoTryOn`
- `pixazoLipsync`
- `pixazoTrainer`
- `fishAudioVoiceClone`
- `elevenLabsVoiceClone`

For Pixazo model-specific schemas and pricing notes, see [Pixazo](pixazo.md). For voice clone internals, see [Voice Clone](voice_clone.md).

### Suno Audio Service

`sunoAudioGeneration` sends Suno's required `callBackUrl` on generation requests. Configure the service `callback_url` when the PawFlow server has a public webhook URL. If `callback_url` is omitted, the tool derives `callBackUrl` from the runtime `file_base_url` as `/webhooks/suno/callback` and still polls Suno's `GET /api/v1/generate/record-info?taskId=...` endpoint before returning generated audio files.

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

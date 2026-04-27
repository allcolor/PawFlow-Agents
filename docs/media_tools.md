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

Media tools are provider-agnostic. They resolve the active service at runtime. Supported service families include:

- `openaiImageGeneration`
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

## Flow Usage

Every media tool can be used in flows through `tool.<name>` tasks, for example `tool.generate_image` or `tool.speech_to_video`. See [Task Catalog](tasks.md#tool-tasks-tool-prefix).

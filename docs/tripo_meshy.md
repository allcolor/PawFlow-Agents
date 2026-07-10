# Tripo3D & Meshy — Native 3D Providers

PawFlow ships two native 3D services that talk directly to the vendor APIs
(both offer free-tier credits on their own platforms, unlike the aggregator
routes through Pixazo/WaveSpeed):

| Service type | Vendor API | Capabilities |
|---|---|---|
| `tripo3DGeneration` | `api.tripo3d.ai` | text-to-3D, image-to-3D, rigging, animation retargeting, retexture, convert, stylize |
| `meshy3DGeneration` | `api.meshy.ai` | text-to-3D (preview + refine), image-to-3D, rigging, animation, retexture |

Both are `BaseImage3DService` providers, so the `generate_3d` tool picks them
up automatically. The extra capabilities are exposed through three dedicated
tools: `rig_3d_model`, `animate_3d_model` and `retexture_3d_model`. Those
tools resolve only services that implement the matching method — the
aggregator 3D services (Pixazo/WaveSpeed) return a clear error instead.

## Service parameters

### `tripo3DGeneration`

| Param | Default | Description |
|---|---|---|
| `api_key` | — | Tripo3D API key (https://platform.tripo3d.ai). |
| `model_version` | `""` | Default generation model version (e.g. `v2.5-20250123`). Empty uses Tripo's current default. |
| `poll_interval` | 5 | Seconds between task status checks. |
| `timeout` | 120 | HTTP timeout per request. |
| `max_retries` | 3 | Retries for transient 5xx responses. |

### `meshy3DGeneration`

| Param | Default | Description |
|---|---|---|
| `api_key` | — | Meshy API key (https://www.meshy.ai/api). |
| `ai_model` | `latest` | Default Meshy model: `meshy-5`, `meshy-6` or `latest`. |
| `poll_interval` | 5 | Seconds between task status checks. |
| `timeout` | 120 | HTTP timeout per request. |
| `max_retries` | 3 | Retries for transient 5xx responses. |

Both APIs are asynchronous task APIs: the service submits a task, polls until
completion (cancellable through the normal force-stop path), downloads the
resulting model file and persists it to FileStore (or a filesystem service
via `destination` + `path`).

## Task chaining with `task_id`

Rigging, animation and retexture operate on a **previous task of the same
vendor**. Every 3D tool response from these services therefore includes a
`task_id:` line. The chain is:

1. `generate_3d` → model file + `task_id` (generation task)
2. `rig_3d_model` with that `task_id` → rigged model + rig `task_id`
3. `animate_3d_model` with `rig_task_id` + `animation` → animated model

`retexture_3d_model` takes the *generation* `task_id` (step 1), not the rig id.

Provider differences:

- **Meshy** rigging/retexture also accept a `model_url` (public HTTP or
  `fs://filestore/...` GLB), so models from any source can be rigged or
  retextured. Rigging only works on textured humanoid models (≤300k faces).
- **Tripo** rigging/animation/retexture require a Tripo `task_id`; external
  model URLs are not supported by the vendor API.
- **Animations**: Meshy uses numeric `action_id`s from its animation library
  (https://docs.meshy.ai/en/api/animation), e.g. `"92"`. Tripo uses preset
  strings: `preset:idle`, `preset:walk`, `preset:run`, `preset:dive`,
  `preset:climb`, `preset:jump`, `preset:slash`, `preset:shoot`,
  `preset:hurt`, `preset:fall`, `preset:turn`, plus non-biped variants
  (`preset:quadruped:walk`, `preset:hexapod:walk`, `preset:octopod:walk`,
  `preset:serpentine:march`, `preset:aquatic:march`). Several Tripo presets
  can be comma-separated in one call.
- Meshy `rig_3d_model` responses may include basic walking/running animation
  URLs for free alongside the rigged character.

## Generation options (passthrough kwargs)

Extra tool arguments are forwarded to the vendor request and filtered
against each endpoint's accepted fields (unknown keys are dropped, not
errored). Highlights:

- **Common**: `format` (`glb` default; `fbx`, `obj`, `usdz`, `stl` where the
  vendor supports it), `model` (Meshy `ai_model` / Tripo `model_version`
  override).
- **Meshy text-to-3D**: `refine` (default `true`; set `false` for the cheap
  untextured preview only), `topology` (`quad`/`triangle`),
  `target_polycount`, `pose_mode` (`a-pose`/`t-pose` — useful before
  rigging), `enable_pbr`, `hd_texture`, `texture_prompt`.
- **Meshy image-to-3D**: `should_texture`, `enable_pbr`, `topology`,
  `target_polycount`, `pose_mode`, `image_enhancement`.
- **Tripo**: `texture`, `pbr`, `face_limit`, `quad`, `style`
  (e.g. `person:person2cartoon`, `object:clay`), `auto_size`,
  `texture_quality` (`standard`/`detailed`), `smart_low_poly`,
  `generate_parts`, `negative_prompt` (text mode).
- **Tripo extras** (service methods, callable from flows/PFP): `convert_3d`
  (export to GLTF/USDZ/FBX/OBJ/STL/3MF) and `stylize_3d`
  (lego/voxel/voronoi/minecraft).

## Example agent flows

Text → rigged, walking character (Tripo):

1. `generate_3d` `{"prompt": "a cartoon knight, T pose", "service": "tripo3d"}` → `task_id: trip-123`
2. `rig_3d_model` `{"task_id": "trip-123", "service": "tripo3d"}` → `task_id: trip-456`
3. `animate_3d_model` `{"rig_task_id": "trip-456", "animation": "preset:walk", "service": "tripo3d"}`

Image → 3D → new texture (Meshy):

1. `generate_3d` `{"image_url": "fs://filestore/<id>/photo.png", "service": "meshy"}` → `task_id: mesh-abc`
2. `retexture_3d_model` `{"task_id": "mesh-abc", "prompt": "weathered bronze statue", "service": "meshy"}`

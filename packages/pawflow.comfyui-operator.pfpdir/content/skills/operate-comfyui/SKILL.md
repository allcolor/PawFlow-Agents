---
description: Install, configure, operate, validate, and troubleshoot self-hosted ComfyUI
  through PawFlow relays, including host execution with local=true and service routing
  with relay_local=true. Use for ComfyUI installation or repair, adding models or
  custom nodes, API workflow creation and submission, image/video/audio generation,
  authorized voice continuity, multiple reference assets, long-running jobs, media
  QA, and FileStore delivery.
metadata:
  version: 1.0.0
  source: Crystallized from verified PawFlow ComfyUI installation and production workflows
  references:
  - /workspace/docs/comfyui.md
  - /workspace/docs/COMFYUI_LOCAL_SETUP.md
name: operate-comfyui
---

# Operate ComfyUI through PawFlow

Use this skill for every self-hosted ComfyUI task: installation, relay setup, models, custom nodes, API workflows, reference-driven image/video/audio generation, authorized voice continuity, monitoring, QA, troubleshooting, and delivery.

## Package orchestration contract

This skill is the intelligent entry point for the package. Keep user-facing
requests simple: infer the operation, bootstrap missing preferences, select the
versioned flow, and translate the final result. Users do not need to know
ComfyUI nodes, graph formats, model folders, relay routing, or PawFlow flow IDs.

The package provides these flow templates:

- `pawflow.comfyui.ensure-ready:1.0.0` probes the selected target and durably
  asks for automatic, manual, or cancelled recovery when it is not ready.
- `pawflow.comfyui.provision-assets:1.0.0` validates a model, LoRA, or custom
  node plan and durably waits for explicit approval.
- `pawflow.comfyui.generate-video:1.0.0` normalizes and validates a video
  request, gates estimated partner cost, calls the active video service, and
  checks that a result reference exists.
- `pawflow.comfyui.validate-video:1.0.0` is the reusable structural validation
  stage. It does not replace the technical and visual QA required below.

Flows containing `durableWait` must be deployed, started, and invoked through
`manage_flow.invoke`; never run them with the synchronous batch action. Reuse
an existing compatible running instance when possible. Persist its instance ID
and the invocation/job correlation in working state. If it parks for a user
answer or a long job, schedule a passive continuation and end the turn; never
poll in a loop.

### First-use bootstrap

Run this idempotently whenever the skill is first used in a conversation:

1. Resolve the relay in this order: agent binding, conversation binding,
   user variable `comfyui.default_relay`, then ask the user.
2. Verify that the relay exists and is connected before storing it.
3. Read `comfyui.config_version` and the target entry in
   `comfyui.targets`. Ask only for values that are missing for the requested
   operation.
4. Store durable preferences with `manage_variable` at user scope. Store
   conversation-only choices at conversation scope. Never put secrets in
   variables.
5. Deploy the selected flow on that relay, start it, and invoke its `input`
   port with one UTF-8 JSON object.

Supported user variables:

- `comfyui.config_version`
- `comfyui.default_relay`
- `comfyui.targets` (object keyed by relay ID)
- `comfyui.install_mode` (`automatic`, `manual`, or `ask`)
- `comfyui.default_video_preset`
- `comfyui.video_service`
- `comfyui.allow_partner_api`
- `comfyui.max_partner_cost_usd`
- `comfyui.qa_enabled`

Target entries may contain `workspace`, `base_url`, `output_dir`, and
`install_mode`. The relay passed to the installed package is authoritative;
do not copy `MyWorkspace` or `MyWorkspace` into a variable as live
state. Keep `COMFY_API_KEY`, `HF_TOKEN`, and `CIVITAI_TOKEN` in
SecretStore.

### Official Comfy MCP

The package installs the official `Comfy-Org/comfy-mcp` stdio resource as
`comfy-mcp`. It is deliberately not enabled for a conversation or agent by
package installation. Explain the elevated stdio risk and enable it only after
the user opts in.

The selected relay host must provide `comfy-mcp` 0.10.0 and
`comfy-cli>=1.14.0` on PATH. Configure `COMFY_BIN` when the `comfy`
executable is outside the MCP process PATH. Use `COMFYUI_URL` only for a
deliberate remote target. Never set `COMFY_MCP_ASSUME_CONSENT` on the user's
behalf; MCP elicitation and PawFlow durable confirmations must remain effective.

Use the MCP for live discovery, lifecycle operations, reviewed workflow
execution, unusual diagnosis, and asset provisioning. Use the versioned flows
for durable state, deterministic validation, cost/approval policy, retries, and
result correlation. Do not duplicate all of `comfy-cli` in package flow tasks.

### Flow hand-off rules

- `ensure-ready`: on `ready`, continue. On `automatic`, construct a
  bounded JSON plan, validate it with `provision-assets` when it changes the
  host, execute approved steps through the official MCP or PawFlow tools, then
  invoke `ensure-ready` again. On `manual`, give host-specific instructions
  and resume only after the user says they are done. On `cancelled`, stop.
- `provision-assets`: plans contain declarative actions only. Never include
  shell, command, script, secrets, passwords, or tokens. Model/LoRA downloads
  require HTTPS source, license, and SHA-256. Custom nodes require HTTPS source
  and a pinned revision. Execute only the exact approved plan.
- `generate-video`: pass prompt, negative prompt, dimensions, duration, seed,
  model/preset, references, destination, service override, and any partner cost
  estimate as JSON. A cost above the configured cap fails closed; any positive
  estimate requires durable approval.
- A structurally valid generation result is not delivery-ready. Perform the
  full video/audio/image QA in this skill, copy the exact history-declared
  artifact to FileStore, and show it to the user.

## Non-negotiable operating rules

- Use PawFlow tools for project and host actions. To run on a relay host, select the relay and pass local=true. On Windows use shell=powershell.
- Keep these switches distinct:
  - Tool local=true means execute on the relay host.
  - Service relay_local=true means make ComfyUI HTTP requests from the relay host helper.
- Prefer base_url=relay://RELAY_NAME/localhost:8188 with relay_local=true for host-local ComfyUI.
- Keep ComfyUI bound to loopback. Never expose port 8188 directly to the public internet.
- Treat workflow JSON and custom nodes as executable code. Review sources and accept only trusted graphs and nodes.
- Check /queue before submit, restart, or maintenance. Never interrupt active work without explicit approval.
- Preserve prior outputs. Use unique seeds and filename prefixes.
- Discover actual paths, classes, model names, and outputs. Do not hardcode a username or guess.
- Put multi-step work in todolist. Run jobs over one minute in the background with stable log/status/PID files and schedule a passive continuation. Do not poll aggressively.
- When present, consult /workspace/docs/comfyui.md for PawFlow bindings and /workspace/docs/COMFYUI_LOCAL_SETUP.md for setup. Check current official ComfyUI and PyTorch guidance when versions matter.

## Choose the route

Use configured PawFlow services for trusted presets:
- comfyUIImageGeneration: generate_image and optional edit_image.
- comfyUIVideoGeneration: generate, image_to_video, frame_to_video, reference_to_video, video_edit, video_extend.

Use the direct legacy ComfyUI API for preset administration, a reviewed bespoke graph, or a controlled production sequence. Direct host-local API operations must still execute through the relay with local=true.

Never POST an ordinary UI workflow to /prompt. Export File > Export Workflow (API). Prefer a reviewed, versioned API graph. Recovering a graph from /history or an MP4 prompt metadata tag is a fallback; validate it and save it as a versioned source immediately.

## Install or repair automatically

Only change the host when installation or repair is in scope.

### Preflight

Inspect on the relay host:
- OS and architecture.
- GPU, driver/runtime, VRAM, RAM, and free disk.
- Python, Git, and existing environments.
- GET http://127.0.0.1:8188/system_stats.
- Existing ComfyUI/Desktop roots, model roots, input/output roots, process, port, and logs.
- /queue and configured PawFlow service route.

On Windows, discover ComfyUI Desktop beneath LocalAppData and its settings beneath AppData. Read shared_model_paths.yaml when present; do not casually edit Desktop-generated configuration. Do not change GPU drivers automatically.

### Installation mode

Prefer official ComfyUI Desktop when the user wants its UI and shared-model management. Fetch only from current official ComfyUI sources, verify publisher or published checksum, and use unattended flags only when documented by the actual installer. If it needs one interactive confirmation, report that fact; never invent silent switches.

Use manual/portable mode for deterministic unattended installation:
1. Choose an explicit root with enough free disk.
2. Clone the official repository, preserving any existing local changes.
3. Create a dedicated virtual environment.
4. Install the correct hardware-specific PyTorch build from current official guidance, then ComfyUI requirements with that environment's Python.
5. Start on 127.0.0.1:8188 under a recoverable background process with durable logs.
6. Verify /system_stats and record root, environment Python, model/input/output roots, port, PID, and log.

For Desktop, always use ComfyUI's own environment Python, commonly .venv/Scripts/python.exe, not system Python.

### PawFlow connection

Configure:
- base_url=relay://RELAY_NAME/localhost:8188
- relay_local=true when ComfyUI lives in the relay host namespace.
- relay_local=false only when the URL is intentionally reachable from the PawFlow container/network.

Verify the host helper and /system_stats through the same route the service will use. Run a tiny fixed-seed smoke test before production resolution or duration.

## Add a model

1. Identify the authoritative source and the exact workflow loader. Do not infer the model category from extension alone.
2. Discover model roots and map the asset to the documented category: checkpoints, diffusion_models or unet, text_encoders or clip, vae, loras, controlnet, upscale_models, audio models, or a node-specific folder.
3. Confirm license/access, expected filename, size, checksum if published, and enough disk for partial plus final files.
4. Download into a .part file with redirects, HTTP failure checking, retries, and durable log/status paths.
5. Verify size and SHA-256 when available, then atomically rename. Never overwrite a known-good file blindly.
6. Restart only on an empty queue.
7. Confirm the loader sees the model through /object_info and run the smallest representative test.
8. Record source, revision, license, destination, size, hash, install time, and test result.

Use an existing authorized credential for gated sources without printing or persisting it in scripts, graphs, logs, or manifests.

## Add a custom node

1. Review repository ownership, README, license, install instructions, maintenance, and code involving downloads, subprocesses, network, and paths.
2. Prefer an official/trusted source and pin a commit or release.
3. Install into the discovered custom_nodes root without replacing unrelated content.
4. Install dependencies with ComfyUI's environment Python, never globally.
5. Check dependency conflicts. Restart only on an empty queue.
6. Verify expected classes in /object_info and execute a minimal graph.
7. Record source and revision. If validation fails, preserve logs and revert only the introduced node/dependency change.

A manager UI does not replace source review and revision recording.

## Build a workflow or PawFlow preset

An API graph is keyed by node IDs and contains class_type and inputs. Validate all three.

A PawFlow preset contains:
- workflow: reviewed API graph.
- bindings: external parameters mapped to node inputs.
- output: exact output node and slot.

Bindings may target one or several inputs and may use index, multiply, and coerce. Null preserves a graph default. Expose model selection only intentionally.

Common image bindings: prompt, negative_prompt, width, height, seed, steps, cfg, model. Edit graphs may bind image and indexed images.

Video service inputs:
- generate: prompt, negative_prompt, duration, width, height, seed, model.
- image_to_video: above plus image.
- frame_to_video: above plus image and end_image.
- reference_to_video: above plus image and images.
- video_edit/video_extend: above plus video.

If duration maps to frames, use actual FPS and an explicit multiplier; verify the node's inclusive/exclusive convention.

Before submit:
- Validate required node IDs, class types, inputs, models, and custom-node classes with /object_info.
- Validate every asset path inside ComfyUI.
- Identify output node/key, subfolder, and unique prefix.
- Save a manifest: graph source/revision, bindings, assets, seed, steps, dimensions, duration/FPS, models/nodes, expected output.

## Reference assets and multiple inputs

Inspect each asset first. Record dimensions, orientation, codec, duration, audio presence, and intended role.

Upload direct-API images through /upload/image and validate returned name/subfolder/type. For video/audio use a documented upload path, FileStore URL, or a validated copy into the discovered input root. Never invent an endpoint.

Preserve identity when changing aspect ratio. Extend a portrait into landscape with a blurred/neutral background instead of cropping the face, and reserve composition space deliberately.

Assign non-overlapping prompt roles:
- <Picture 1>: primary identity/main subject.
- <Picture 2>, <Picture 3>, etc.: secondary identity, group, costume, style, or environment.
- <Video 1>: motion/scene/continuity.
- <Audio 1>: authorized voice, cadence, soundtrack, or timing.

State immutable traits and permitted changes. Do not ask several references to control the same identity ambiguously.

Use indexed service bindings for multiple assets. In direct graphs use distinct loaders and explicit indexed inputs such as ref_images.ref_image_0 and ref_images.ref_image_1 only when /object_info confirms those names.

## Reference-driven video prompt

Structure prompts as:
1. Explicit role for every reference slot.
2. Identity invariants: face, hair, body proportions, apparent age, voice, and continuity-critical wardrobe.
3. Intended action/change.
4. Timed blocks that fit inside the requested duration.
5. Exact counts, spatial roles, camera motion, and transitions.
6. Negative constraints: duplicate subjects, morphing, drift, extra limbs, text/logos, unsafe content, unintended age changes.
7. Short dialogue that fits its time window.

For children, require age-appropriate clothing, motion, framing, and language. Keep exact subject counts and no-duplicate constraints explicit.

## Voice and audio cases

Use only voices the user owns or is authorized to use. Avoid deceptive impersonation.

### Voice continuity from prior video

Load the prior trusted video and split it with a supported node such as GetVideoComponents. Route video and audio outputs to the model's reference inputs. A MiniMax H3 graph may expose ref_videos.ref_video_0 and ref_video_audios.ref_video_audio_0; confirm actual names in /object_info.

Prompt <Video 1> for visual continuity and <Audio 1> for the same timbre, accent, pitch, cadence, and style. Add the prior last frame or a strong still identity reference where useful.

### Standalone audio reference

Use a documented audio loader and supported audio-reference field. Convert only to required sample rate/channels, preserving the original and recording the conversion.

### Exact dialogue

Video models may imitate tone without reliable wording. When exact words matter, use an authorized dedicated speech/TTS workflow and synchronize/mux it, or generate video without dialogue and add the validated voice track in post.

### No dialogue

Explicitly request instrumental/ambient/silent audio and no speech. Validate that no unintended intelligible speech exists.

Never claim exact speech from low-confidence STT. Listen in the media viewer when wording, identity, synchronization, or artifacts matter.

## Submit safely through the direct API

1. GET /queue and preserve running/pending work.
2. Upload/validate assets.
3. Resolve and validate the reviewed API graph.
4. Patch only intended values.
5. POST to /prompt.
6. Require prompt_id and persist a submission manifest immediately.
7. Record prompt ID and expected prefix.
8. For long work, use a passive background watcher or scheduled continuation.

A watcher may check /history/PROMPT_ID at bounded intervals and write status, but must never submit another graph.

## Retrieve the correct result

Treat /history/PROMPT_ID as authoritative. Inspect all output keys, including images, gifs, videos, and audio. Use the exact filename, subfolder, and type declared by history with /view or the validated output path.

Never guess an extension or choose the newest directory entry.

## Validate before delivery

Images:
- Verify decoding, dimensions, orientation, corruption, identity, requested counts, composition, text, anatomy, and safety.

Video:
- Use ffprobe for container, duration, resolution, FPS, codecs, audio, and streams.
- Extract 8-12 frames across the clip plus dense samples at transitions/joins.
- Inspect identity stability, exact counts, morphing, duplicates, continuity, intended action, costume, safety, and ending.
- For multipart work, compare prior end/new start and produce a seam contact sheet.

Audio/voice:
- Preserve original; extract a mono analysis copy if needed.
- Measure clipping/loudness.
- Transcribe bounded segments when available and state confidence.
- Human-listen when exact wording, speaker identity, sync, or artifacts matter.

Edits/extensions:
- Confirm unchanged regions stayed unchanged.
- Confirm change is localized.
- Recheck duration and A/V sync after concat/mux.

On failure, preserve artifact and manifest, change one causal variable, and use a new prefix/seed.

## Deliver

Copy or stream the exact validated result into PawFlow FileStore and call show_file so the correct media viewer and fs://filestore reference are returned. Keep the main artifact obvious. Add manifests, QA reports, contact sheets, or extracted audio only when useful.

Revalidate an assembled final after concatenation/muxing. Report duration, resolution, codecs, and whether voice/audio was human-verified or machine-checked only.

## Troubleshooting order

1. Relay connected and local host execution allowed.
2. Correct local=true versus relay_local=true.
3. Host-local /system_stats.
4. Process and /queue.
5. Required classes/options in /object_info.
6. Model file paths, integrity, and shared roots.
7. API graph format and real binding targets.
8. Asset paths/names/subfolders.
9. /prompt response and prompt_id.
10. /history node error or declared outputs.
11. Exact /view retrieval.
12. FileStore extension/MIME and viewer.

Common resolutions:
- Connection refused: start ComfyUI; verify loopback port and host route.
- Wrong namespace: fix relay_local for the selected base URL.
- Missing class: review/install node; restart on empty queue; check /object_info.
- Missing model: fix category/shared root/integrity; restart/rescan.
- Prompt validation failure: compare graph inputs with /object_info.
- Missing output: inspect every history output node/key.
- OOM: reduce resolution, duration, batch, or model size; use only documented offload/quantization.
- Slow first run: distinguish download/compile/warmup from hang via logs/device use.
- Bad audio: inspect stream, sample rate/channels, routing, and mux.
- Poor voice match: use a clean authorized reference, shorter dialogue, or dedicated voice workflow.
- Identity drift with many references: simplify roles and strengthen primary identity constraints.

## Completion standard

Complete only when the route is verified if setup was in scope, required models/classes are discoverable, graph and inputs are recorded, the prompt completed without disturbing queue work, the exact history-declared output passed QA, FileStore opens it with the right viewer, and durable tasks/manifests/logs/hashes/caveats are updated.

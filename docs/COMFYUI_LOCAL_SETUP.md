# How to: local ComfyUI + PawFlow `generate_video`

This guide walks through installing ComfyUI on a local machine and connecting it
as a **ComfyUI Video Generation** service in PawFlow, then running a real
`generate_video` call from an agent.

The concrete topology covered here: PawFlow runs on a remote VPS
(`vps-fc685050-vps-ovh-net`), ComfyUI runs on the local machine (GPU), and a
reverse SSH tunnel makes ComfyUI reachable on `localhost:8188` of the VPS.
ComfyUI stays bound to loopback; no port is exposed to the internet.

The general PawFlow/ComfyUI reference (preset schema, binding reference, all
service parameters, troubleshooting) lives in `docs/comfyui.md`. This document
is the step-by-step companion for the local-GPU + VPS case.

## Overview

```
Local machine (GPU)                        VPS (PawFlow server + MyWorkspace relay)
+-------------------------+                +----------------------------------------------+
| ComfyUI :8188           |<-- SSH -R ---->| relay host helper -> localhost:8188          |
| LTX-Video workflow      |  reverse tunnel|        ^                                     |
|                         |                |   base_url = relay://MyWorkspace/localhost:8188 |
+-------------------------+                |   comfyUIVideoGeneration service            |
                                           |   agent -> generate_video -> fs://filestore |
                                           +----------------------------------------------+
```

## Part A — Install ComfyUI on the local machine

### A1. Prerequisites

- NVIDIA GPU with at least 8 GB VRAM (recommended for video; 6 GB can run
  LTX-Video at reduced resolution), or Apple Silicon / AMD depending on the
  PyTorch build.
- Python 3.10-3.12 (`python --version`).
- ~20 GB free disk space (models included).
- git.

### A2. Installation

```bash
git clone https://github.com/Comfy-Org/ComfyUI.git
cd ComfyUI
python -m venv .venv
# Windows: .venv\Scripts\activate   |   Linux/macOS: source .venv/bin/activate
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
pip install -r requirements.txt
```

> For other hardware (AMD ROCm, Apple MPS, CPU), replace the torch line with
> the command from the official
> [installation guide](https://docs.comfy.org/installation/manual_install).
> On Windows the [portable build](https://github.com/Comfy-Org/ComfyUI/releases)
> also works (7z with embedded Python); you can then skip to A3.

### A3. Custom node: ComfyUI-LTXVideo

The lightest, fastest text-to-video / image-to-video path today is
**LTX-Video 2B** (Lightricks, ~2 GB, Apache 2.0). Install its nodes with:

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/Lightricks/ComfyUI-LTXVideo.git
cd ComfyUI-LTXVideo
pip install -r requirements.txt
```

(Alternative: install [ComfyUI-Manager](https://github.com/ltdrdata/ComfyUI-Manager)
and use Manager -> Install Custom Nodes -> "LTXV".)

### A4. Models (download into `ComfyUI/models/`)

| File | Destination | Size |
|---|---|---|
| `ltx-video-2b-v0.9.5.safetensors` — [HF Lightricks/LTX-Video](https://huggingface.co/Lightricks/LTX-Video/resolve/main/ltx-video-2b-v0.9.5.safetensors) | `models/checkpoints/` | ~2 GB |
| `t5xxl_fp8_e4m3fn.safetensors` — [HF comfyanonymous/flux_text_encoders](https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp8_e4m3fn.safetensors) | `models/text_encoders/` | ~4.9 GB |

### A5. Start and verify

```bash
python main.py --listen 127.0.0.1 --port 8188
```

Open `http://127.0.0.1:8188` (the UI must load), then from a terminal:

```bash
curl http://127.0.0.1:8188/system_stats
```

A JSON response means the control API is reachable. **Leave it running** for the
rest of the guide.

## Part B — Build and export the video workflow

### B1. Load a template

In the ComfyUI UI: **Templates -> LTX Video** -> **Text to Video** (or
**Image to Video** to later test animating an image).

### B2. Configure and test

- Make sure the checkpoint points at `ltx-video-2b-v0.9.5.safetensors` and the
  CLIP at `t5xxl_fp8_e4m3fn.safetensors`.
- Use a simple prompt (e.g. "a cat walking in a garden, cinematic"),
  768x512, ~97 frames (~4 s), any seed.
- Hit **Queue** (play button). Wait for completion (tens of seconds to a few
  minutes depending on the GPU).
- Verify the video appears in the output area and plays.

### B3. Export in API format — the critical step

In the ComfyUI menu: **File -> Export Workflow (API)**. Save the JSON (e.g.
`ltxv_t2v_api.json`).

> **Not** "Save" and not the normal workflow copy: the API format is keyed by
> node ID with `class_type`/`inputs`. PawFlow rejects the UI format.

### B4. Identify the nodes for the PawFlow preset

Open the exported JSON and note:

- the ID of the positive **CLIPTextEncode** node (input `text` <- prompt);
- the ID of the **LTXVConditioning** / **LTXVScheduler** node (inputs `width`,
  `height`, `frames`);
- the ID of the **LTXVSampler** node (input `seed`);
- the ID of the **VHS_VideoCombine** (or equivalent video save) node — it
  records the artifact in history.

To find the history key of the output (`gifs`, `videos` or `images`): run one
generation, then `curl http://127.0.0.1:8188/history/<prompt_id>` and look at
that node's entry. `gifs` is the most common key for VHS_VideoCombine.

## Part C — Connect the local machine to the VPS

### C1. Reverse SSH tunnel (recommended — no port opening)

From the **local machine**:

```bash
ssh -N -R 8188:127.0.0.1:8188 <user>@vps-fc685050-vps-ovh-net
```

`localhost:8188` **on the VPS** now points at your local ComfyUI. Keep the
terminal open; use `autossh` or a systemd/Windows service to make it durable.

### C2. Alternative — Tailscale VPN

Install [Tailscale](https://tailscale.com/download) on both the PC and the VPS.
ComfyUI must then listen on all interfaces
(`python main.py --listen 0.0.0.0 --port 8188`), and the Tailscale IP of the
local machine is used as `base_url` (see D2).

### C3. Not recommended

Opening port 8188 on the router — requires a reverse proxy with auth
(`api_key` in the service). Avoid for a test.

## Part D — Configure the ComfyUI service in PawFlow

### D1. Create the service

In PawFlow: **Resources -> Services -> create -> ComfyUI Video Generation**.

### D2. Network parameters

| Parameter | Value (tunnel C1) | Value (Tailscale C2) |
|---|---|---|
| `base_url` | `relay://MyWorkspace/localhost:8188` | `relay://MyWorkspace/<tailscale-ip>:8188` |
| `relay_local` | `true` (relay host helper) | `false` first, `true` if it fails |
| `allow_private_base_url` | leave `false` (`relay://` handles private) | same |
| `timeout` / `poll_interval` | defaults (unlimited / 2 s) | same |

> `relay_local=true` = the relay calls ComfyUI from the VPS **host** (required
> for the SSH tunnel: the relay container's `localhost` is not the VPS's
> `localhost`). If the relay host helper is not enabled, the call fails at
> `/system_stats` — check the relay settings (host-helper flag) or switch to
> `relay_local=false` with the Tailscale IP.

### D3. The `workflows` field — the essential part

Take the JSON exported in B3 and wrap it in a `generate` preset with `bindings`
and `output` (node IDs = those of **your** export):

```json
{
  "generate": {
    "workflow": {
      "...": { "class_type": "...", "inputs": { ... } },
      "...": { "class_type": "LTXVConditioning", "inputs": { ... } },
      "...": { "class_type": "LTXVScheduler", "inputs": { "width": 768, "height": 512, "frames": 97, ... } },
      "...": { "class_type": "LTXVSampler", "inputs": { "seed": 0, ... } },
      "...": { "class_type": "VHS_VideoCombine", "inputs": { "filename_prefix": "PawFlow", ... } }
    },
    "bindings": {
      "prompt":          {"node": "<positive CLIPTextEncode id>", "input": "text"},
      "negative_prompt": {"node": "<negative CLIPTextEncode id>", "input": "text"},
      "width":           {"node": "<LTXVScheduler id>", "input": "width", "coerce": "int"},
      "height":          {"node": "<LTXVScheduler id>", "input": "height", "coerce": "int"},
      "duration":        {"node": "<LTXVScheduler id>", "input": "frames", "multiply": 24, "coerce": "int"},
      "seed":            {"node": "<LTXVSampler id>", "input": "seed", "coerce": "int"}
    },
    "output": {"node": "<VHS_VideoCombine id>", "key": "gifs", "index": 0}
  }
}
```

Golden rules:

- `duration` converts **seconds** to frames via `multiply` (24 = fps). Adjust
  if your template expresses something else.
- `output.key` must be the real history key: `gifs` (VHS) most often, otherwise
  `videos`/`images` (verified in B4).
- You can add a second preset `image_to_video` (same bindings plus
  `"image": {"node": "<LoadImage id>", "input": "image"}`) to test
  animating an image.

### D4. Verify

- Save. The service must appear in the **Video** category.
- If several video services exist, set this one as the conversation's media
  preference (or tell the agent to use its name).
- Make sure the **MyWorkspace** relay is linked to the conversation.

## Part E — Test `generate_video`

### E1. First test (through the agent)

In a PawFlow conversation linked to MyWorkspace, send:

> "Use the ComfyUI service to generate a 4-second 768x512 video: an orange cat
> walking in a garden, morning light, cinematic style."

### E2. What you should see

1. The agent calls the **`generate_video`** tool (visible in the logs:
   `Agent calling tool 'generate_video'`).
2. Status events while waiting (the service **polls `/history`** until the
   prompt finishes in ComfyUI).
3. The result: an **`fs://filestore/...mp4`** URL (never base64 in the
   conversation).
4. A playable video preview in the webchat; the file persisted in FileStore.

### E3. Image-to-video test (bonus)

Paste an image into the conversation and ask: "Animate this image into a
3-second video using the image_to_video preset of the ComfyUI service." The
agent will upload the image (`upload_source`) to ComfyUI before submitting the
workflow.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Connection failed at /system_stats` | Namespace: `relay_local=true` is required for the SSH tunnel; is the tunnel up? (`curl localhost:8188/system_stats` **on the VPS** must answer); is the relay host helper enabled? |
| `workflow is UI format; export API format` | Wrong export format (B3). |
| `targets missing input` / `output.node is not in the workflow` | Node IDs/inputs differ from your export — use the IDs of **your** JSON. |
| `ComfyUI reports node errors` | Custom node not installed (A3), model missing (A4), or out-of-range values. Test the workflow in the frontend first. |
| Generation completes but no artifact | Wrong `output.key` — inspect `/history/<prompt_id>` and set the right key (`gifs`/`videos`/`images`). |
| Operation is not selected | The preset must use the exact operation name: `generate`, `image_to_video`, ... |
| `Output exceeds the configured limit` | Lower resolution/frames, or raise `max_output_bytes` (check disk). |
| Not enough VRAM | Reduce to 512x320, 49 frames, or switch to a lighter model. |

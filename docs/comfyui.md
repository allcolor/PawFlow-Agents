# ComfyUI

PawFlow can use a self-hosted ComfyUI Server as an image or video provider.
Two built-in services are available:

| Service type | PawFlow tools |
|---|---|
| `comfyUIImageGeneration` | `generate_image`, and `edit_image` when an `edit_image` workflow is configured |
| `comfyUIVideoGeneration` | `generate_video` plus the configured text/image/frame/reference/video operations |

The integration uses administrator-configured workflows only. An agent selects an
operation and supplies values for declared bindings; it cannot submit or replace a
workflow at call time. PawFlow uploads configured media inputs, submits the workflow
to `POST /prompt`, polls `/history/{prompt_id}`, selects one declared output, and
streams `/view` to a temporary file. The normal media handler copies that file to
FileStore or the requested relay filesystem and removes the temporary file.

This integration targets the self-hosted ComfyUI Server API. It is not the Comfy
Cloud API v2.

## Optional Comfy Cloud MCP package

PawFlow can also connect to the official hosted
[Comfy Cloud MCP](https://docs.comfy.org/agent-tools/cloud). This is separate from
the self-hosted workflow services documented below: Comfy Cloud supplies and
updates its own image, video, audio, and 3D workflows, and exposes them as MCP
tools at `https://cloud.comfy.org/mcp`.

PawFlow releases include the signed `pawflow.comfy-cloud-mcp` package in the
local package catalog. It is available for installation but is not installed or
enabled automatically.

To connect it:

1. Create a Comfy Cloud account and generate an API key in the Comfy Cloud API
   settings.
2. Store the API key in **Resources -> Secrets** at the intended user or
   conversation scope.
3. Open **Resources -> PawFlow Packages**, select **Install package**, search for
   `Comfy Cloud`, and inspect `pawflow.comfy-cloud-mcp@1.0.0`.
4. Choose the installation scope and bind the package's `comfy_api_key` requirement
   to the stored secret name created in the previous step, then install it.
5. Open **Configure availability** in the MCP Repository if the conversation or a
   specific agent restricts which MCP servers or tools are available.

The package contains no API key. Installation rewrites its logical
`${comfy_api_key}` placeholder to the selected stored-secret name, and the MCP
runtime resolves that expression into the `X-API-Key` request header. User and
conversation secrets override a same-named global secret according to the normal
expression-resolution cascade.

The connector uses direct Streamable HTTP from the PawFlow server. It does not
require a relay, a local ComfyUI installation, or a PawFlow ComfyUI generation
service. The account's Comfy Cloud usage and billing rules still apply.

## 1. Install and start ComfyUI

Choose one of the installation methods in the
[official ComfyUI installation guide](https://docs.comfy.org/installation/manual_install).
A minimal manual installation looks like this:

```bash
git clone https://github.com/Comfy-Org/ComfyUI.git
cd ComfyUI
python -m venv .venv
source .venv/bin/activate
# Install the PyTorch build appropriate for this machine first.
pip install -r requirements.txt
python main.py --listen 127.0.0.1 --port 8188
```

On Windows, activate the environment with
`.venv\Scripts\activate`. Follow the official hardware-specific instructions for
the current NVIDIA, AMD, Intel, Apple Silicon, or CPU PyTorch command.

ComfyUI does not publish an official Docker image. A community image can work, but
its image, updates, model volumes, custom nodes, and network policy remain the
operator's responsibility. Persist at least the model, input, output, and custom-node
data that the selected image documents.

Verify the server before configuring PawFlow:

```bash
curl http://127.0.0.1:8188/system_stats
```

A JSON response means the control API is reachable.

## 2. Choose the network topology

The recommended topology is to run ComfyUI on the machine that owns the selected
PawFlow relay. No public ComfyUI port is required.

| ComfyUI location | `base_url` | `relay_local` | Requirement |
|---|---|---:|---|
| Relay host | `relay://MyWorkspace/localhost:8188` | `true` | The relay must allow its host helper; ComfyUI may stay bound to `127.0.0.1`. |
| Relay container | `relay://MyWorkspace/localhost:8188` | `false` | ComfyUI must run in the same relay container/network namespace. |
| LAN host reachable from the relay | `relay://MyWorkspace/192.168.1.50:8188` | Usually `true` | Start ComfyUI on a reachable interface and firewall port 8188 to the relay host. |
| PawFlow server/container network | `http://comfyui:8188` | Ignored | Set `allow_private_base_url=true`; the name/address must be reachable from the PawFlow server. |
| Authenticated public reverse proxy | `https://comfy.example.com` | Ignored | Keep `allow_private_base_url=false`; configure the API-key fields if required. |

For a LAN endpoint, ComfyUI normally needs `--listen 0.0.0.0`. Do not expose port
8188 to the internet. Prefer a firewall, private overlay network, or authenticated
TLS reverse proxy.

The `relay://` form is converted at call time into a short-lived, conversation-bound
PawFlow relay-proxy URL. The generated route is private-network-only. `relay_local`
makes the execution namespace explicit: `true` uses the relay host helper and
`false` uses the relay container.

## 3. Build and export a workflow

Build and test the workflow in the ComfyUI frontend first. It must end in a node
that records an artifact in prompt history, such as `SaveImage` or a video combine/
save node.

Export it with **File -> Export Workflow (API)**. Do not use the ordinary
**Save** JSON. The two formats are different; the API format is keyed by node IDs
and each node has `class_type` and `inputs`. See the official
[Workflow API Format](https://docs.comfy.org/development/api-development/workflow-api-format)
reference.

A PawFlow preset has three required parts:

- `workflow`: the complete exported API-format object.
- `bindings`: allowed PawFlow argument names mapped to exact node inputs.
- `output`: the node, history list key, and zero-based artifact index to download.

PawFlow validates every node, binding target, output node, index, coercion, and
multiplier when the service is created. UI-format workflows and missing targets
fail immediately.

## 4. Configure an image service

Open **Resources -> Services**, create a **ComfyUI Image Generation** service, and
set:

- `base_url` and `relay_local` for the topology above;
- `workflows` to the JSON object described below;
- optional authentication fields when a reverse proxy protects ComfyUI;
- time and size limits appropriate for the model.

The following is a complete illustrative `workflows` value for a standard
text-to-image graph. The checkpoint name must exist in this ComfyUI installation.

```json
{
  "generate": {
    "workflow": {
      "3": {
        "class_type": "KSampler",
        "inputs": {
          "seed": 1,
          "steps": 20,
          "cfg": 8,
          "sampler_name": "euler",
          "scheduler": "normal",
          "denoise": 1,
          "model": ["4", 0],
          "positive": ["6", 0],
          "negative": ["7", 0],
          "latent_image": ["5", 0]
        }
      },
      "4": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {
          "ckpt_name": "v1-5-pruned-emaonly-fp16.safetensors"
        }
      },
      "5": {
        "class_type": "EmptyLatentImage",
        "inputs": {
          "width": 512,
          "height": 512,
          "batch_size": 1
        }
      },
      "6": {
        "class_type": "CLIPTextEncode",
        "inputs": {
          "text": "template positive prompt",
          "clip": ["4", 1]
        }
      },
      "7": {
        "class_type": "CLIPTextEncode",
        "inputs": {
          "text": "template negative prompt",
          "clip": ["4", 1]
        }
      },
      "8": {
        "class_type": "VAEDecode",
        "inputs": {
          "samples": ["3", 0],
          "vae": ["4", 2]
        }
      },
      "9": {
        "class_type": "SaveImage",
        "inputs": {
          "filename_prefix": "PawFlow",
          "images": ["8", 0]
        }
      }
    },
    "bindings": {
      "prompt": {"node": "6", "input": "text"},
      "negative_prompt": {"node": "7", "input": "text"},
      "width": {"node": "5", "input": "width", "coerce": "int"},
      "height": {"node": "5", "input": "height", "coerce": "int"},
      "seed": {"node": "3", "input": "seed", "coerce": "int"},
      "num_inference_steps": {"node": "3", "input": "steps", "coerce": "int"},
      "guidance_scale": {"node": "3", "input": "cfg", "coerce": "float"},
      "model": {"node": "4", "input": "ckpt_name", "coerce": "string"}
    },
    "output": {
      "node": "9",
      "key": "images",
      "index": 0
    }
  }
}
```

Do not bind `model` unless callers are intentionally allowed to choose any
checkpoint name accepted by that loader node. Removing a binding keeps the value
fixed in the trusted workflow.

To support `edit_image`, export and add a second preset named `edit_image`.
Bind `image` to the filename input of the workflow's load-image node. For multiple
inputs, bind `images` with explicit indices:

```json
{
  "bindings": {
    "prompt": {"node": "12", "input": "text"},
    "image": {"node": "20", "input": "image"},
    "images": [
      {"node": "20", "input": "image", "index": 0},
      {"node": "21", "input": "image", "index": 1}
    ]
  },
  "output": {"node": "30", "key": "images", "index": 0}
}
```

This fragment shows only `bindings` and `output`; place them beside the complete
exported `workflow` in the `edit_image` preset. PawFlow uploads each FileStore or
HTTP(S) source to ComfyUI first, then places the returned ComfyUI filename in the
bound input.

Direct HTTP(S) media inputs are restricted to public addresses. PawFlow validates
the original host and every redirect target before downloading, so loopback,
private, link-local, metadata-service, reserved, and DNS-resolved private
addresses cannot be used as media sources. Use FileStore for private media.

## 5. Configure a video service

Create a **ComfyUI Video Generation** service. Every configured operation is a
separate trusted preset, so text-to-video and image-to-video can use completely
different graphs and custom nodes.

Supported operation names and their binding values are:

| Preset | Values available to bindings |
|---|---|
| `generate` | `prompt`, `negative_prompt`, `duration`, `width`, `height`, `seed`, `model` |
| `image_to_video` | The generate values plus uploaded `image` |
| `frame_to_video` | The generate values plus uploaded `image` and `end_image` |
| `reference_to_video` | The generate values plus uploaded `image` and `images` |
| `video_edit` | The generate values plus uploaded `video` |
| `video_extend` | The generate values plus uploaded `video` |

A generic video preset has this shape:

```json
{
  "image_to_video": {
    "workflow": {
      "10": {
        "class_type": "LoadImage",
        "inputs": {"image": "placeholder.png"}
      },
      "40": {
        "class_type": "YourVideoNode",
        "inputs": {
          "positive": "template prompt",
          "width": 832,
          "height": 480,
          "frames": 81,
          "source": ["10", 0]
        }
      },
      "90": {
        "class_type": "YourVideoSaveNode",
        "inputs": {"frames": ["40", 0], "filename_prefix": "PawFlow"}
      }
    },
    "bindings": {
      "image": {"node": "10", "input": "image"},
      "prompt": {"node": "40", "input": "positive"},
      "width": {"node": "40", "input": "width", "coerce": "int"},
      "height": {"node": "40", "input": "height", "coerce": "int"},
      "duration": {
        "node": "40",
        "input": "frames",
        "multiply": 16,
        "coerce": "int"
      }
    },
    "output": {"node": "90", "key": "gifs", "index": 0}
  }
}
```

Replace `YourVideoNode`, `YourVideoSaveNode`, their inputs, and the history key
with the exact API export from the installed custom-node stack. Many ComfyUI video
save nodes report MP4 artifacts under the history key `gifs`; others use `videos`
or `images`. Run the graph once and inspect `GET /history/{prompt_id}` if unsure.

The `multiply` example converts seconds to frames at 16 fps. Use the actual frame
rate and duration semantics of the selected graph.

## 6. Binding reference

One source value can target one node input or a list of node inputs:

```json
{
  "prompt": [
    {"node": "6", "input": "text"},
    {"node": "18", "input": "caption"}
  ]
}
```

Each target supports:

| Field | Meaning |
|---|---|
| `node` | Required exported node ID. |
| `input` | Required existing key in that node's `inputs`. |
| `index` | Select an item from a list value such as `images`. |
| `multiply` | Multiply a numeric value before assignment. |
| `coerce` | Optional `int`, `float`, `string`, or `bool`. |

A value of `null` is not applied, so an optional PawFlow argument can leave the
trusted workflow default unchanged.

## 7. Service parameters

| Parameter | Default | Notes |
|---|---:|---|
| `base_url` | `relay://MyWorkspace/localhost:8188` | ComfyUI Server root URL. |
| `relay_local` | `true` | Host helper for relay URLs; set false for the relay container. |
| `allow_private_base_url` | `false` | Required only for a direct private/loopback URL, not `relay://`. |
| `api_key` | Empty | Optional reverse-proxy credential. |
| `api_key_header` | `Authorization` | For example `Authorization` or `X-API-Key`. |
| `api_key_prefix` | `Bearer` | Leave empty when the header expects the raw key. |
| `workflows` | Required | Presets keyed by operation name. |
| `timeout` | Image: 1800 s; video: 3600 s | Overall prompt-history wait. |
| `request_timeout` | 60 s | Individual control/upload request timeout. |
| `poll_interval` | Image: 1 s; video: 2 s | History polling interval. |
| `max_input_bytes` | Image: 100 MiB; video: 512 MiB | Per uploaded input. |
| `max_output_bytes` | Image: 4 GiB; video: 8 GiB | Download limit; output is streamed to disk. |

## 8. Test from an agent

After saving and connecting the service:

1. Link the relay that can reach ComfyUI to the conversation.
2. Confirm the service appears in the Image or Video category.
3. If several compatible services exist, choose this service as the conversation or
   agent media preference.
4. Start with a small output and fixed dimensions.
5. Call `get_image_model_info` for the image service, then `generate_image`; or
   call `generate_video` with only the arguments bound by the selected preset.
6. Confirm the result is an `fs://filestore/...` URL or the requested relay file,
   not base64 in the conversation.

Only operations present in `workflows` are advertised to automatic media-service
selection. A service that contains only `generate` will not be selected for
`edit_image` or image-to-video.

## 9. Security and operations

- Treat every workflow and custom node as executable administrator configuration.
  Install custom nodes only from reviewed sources.
- Do not accept workflow JSON from agent arguments, prompts, uploads, or untrusted
  users. PawFlow intentionally has no per-call workflow override.
- Keep local ComfyUI bound to loopback when the relay host is the only caller.
- Put authentication and TLS in front of any non-private deployment.
- Store reverse-proxy keys in PawFlow secrets; do not paste them into prompts.
- Set input/output limits below available disk and memory capacity.
- ComfyUI retains its own input/output artifacts according to its configuration.
  PawFlow removes only its temporary downloaded output after persistence.
- The relay response and PawFlow proxy are chunked end to end, and ComfyUI outputs
  are written to disk in bounded chunks. Large output files are not accumulated in
  server RAM.

## 10. Troubleshooting

**Connection failed at `/system_stats`**

Check the execution namespace first. For host ComfyUI use `relay_local=true` and
ensure the relay was started with host-helper access. For container ComfyUI use
`relay_local=false`. Confirm the conversation has a selected, connected relay.

**“workflow is UI format; export API format”**

Load the graph in ComfyUI and use **File -> Export Workflow (API)**. Ordinary
workflow-save JSON contains root `nodes` and `links` arrays and is rejected.

**“targets missing input” or “output.node is not in the workflow”**

Node IDs and input names changed after the workflow was re-exported. Update the
bindings/output to match the new API JSON.

**ComfyUI reports node errors**

The graph reached ComfyUI but a model, custom node, input type, or value is invalid.
Run the same exported graph in ComfyUI, verify installed models/custom nodes, and
inspect the node error returned by PawFlow.

**Generation completes but no artifact is found**

Inspect `/history/{prompt_id}`. Set `output.node` to the save/combine node and
`output.key` to the list containing the desired artifact, commonly `images`,
`gifs`, or `videos`.

**An image/video operation is not selected**

Add a preset with the exact PawFlow operation name. Merely having a Python method
is not enough; automatic selection uses the configured operation list.

**Output exceeds the configured limit**

Increase `max_output_bytes` only after checking available disk space, or reduce
resolution, frame count, duration, codec quality, or batch size. A partial PawFlow
temporary file is deleted when the limit is crossed.

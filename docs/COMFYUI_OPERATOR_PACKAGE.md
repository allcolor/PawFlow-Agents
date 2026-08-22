# ComfyUI Operator PFP

`pawflow.comfyui-operator` is the all-in-one package for operating a
self-hosted ComfyUI through an agent without requiring the user to understand
ComfyUI graphs, nodes, model directories, relay namespaces, or PawFlow flow
internals.

The package source is
`packages/pawflow.comfyui-operator.pfpdir`. Version 1.1.0 contains:

- the `operate-comfyui` orchestration and QA skill;
- three deterministic PFP flow tasks;
- four versioned flow templates;
- the official `Comfy-Org/comfy-mcp` stdio resource.

## Responsibilities

The skill interprets the request, bootstraps preferences, selects and invokes a
flow, performs expert MCP operations, resumes long-running work, and validates
media before delivery.

The flows own durable state and deterministic gates:

| Flow | Purpose |
|---|---|
| `pawflow.comfyui.ensure-ready:1.0.0` | Probe `/system_stats`, `/queue`, and optionally `/object_info`; durably ask how to recover |
| `pawflow.comfyui.provision-assets:1.0.0` | Validate a declarative asset plan and wait for explicit approval |
| `pawflow.comfyui.generate-video:1.0.0` | Normalize/validate a request, gate partner cost, call the active video service, and validate the result shape |
| `pawflow.comfyui.validate-video:1.0.0` | Reusable structural result check before full media QA |

The package does not reimplement `comfy-cli`. The official MCP handles live
discovery, lifecycle operations, model and node operations, and reviewed
workflow execution. Flow tasks provide only request normalization, host-routed
read-only probing, and deterministic document validation.

## Installation and activation

Inspect the PFP before installing it. The plan reports high risk because the
package contains a stdio MCP and one brokered `bash` grant. The grant belongs
only to `pawflowComfyuiProbe`; its entrypoint submits a fixed Python HTTP probe
through the selected relay host with `local=true`. It rejects non-loopback
targets unless `allow_remote=true`.

`allow_remote` widens where the *operator* may point the probe, and only that.
A non-loopback `base_url` must come from the task's own configuration: one
arriving in the FlowFile payload is refused even when `allow_remote=true`.
The probe runs on the relay host, so accepting a target chosen by flow content
would turn it into a server-side request forgery primitive against whatever
that host can reach. A loopback URL from the payload stays allowed, since that
is the ordinary case of a flow naming its local ComfyUI port.

The relay host must provide:

- Python 3.10 or newer;
- `comfy-cli>=1.14.0`;
- official `comfy-mcp==0.10.0`;
- ComfyUI itself, or enough capacity for the approved install path.

Installing the package creates the MCP resource but does not enable it for a
conversation or agent. Enable `comfy-mcp` explicitly after reviewing its
stdio access. The resource runs on the relay host and expects the
`comfy-mcp` executable on PATH. Set `COMFY_BIN` in the MCP resource when the
`comfy` executable is outside that PATH.

Do not set `COMFY_MCP_ASSUME_CONSENT` automatically. Comfy MCP elicitation and
PawFlow durable confirmation remain separate safety gates.

## First-use bootstrap

The skill resolves a target in this order:

1. agent relay binding;
2. conversation relay binding;
3. user variable `comfyui.default_relay`;
4. a direct user choice.

Durable configuration belongs in user variables:

- `comfyui.config_version`
- `comfyui.default_relay`
- `comfyui.targets`
- `comfyui.install_mode`
- `comfyui.default_video_preset`
- `comfyui.video_service`
- `comfyui.allow_partner_api`
- `comfyui.max_partner_cost_usd`
- `comfyui.qa_enabled`

`comfyui.targets` is a JSON object keyed by relay ID. Target values may
contain `workspace`, `base_url`, `output_dir`, and `install_mode`.
Secrets such as `COMFY_API_KEY`, `HF_TOKEN`, and `CIVITAI_TOKEN` always
remain in SecretStore.

## Durable invocation

Flows that contain `durableWait` must run as deployed continuous instances.
The agent finds or deploys the exact FQN, starts it, then calls
`manage_flow.invoke` on the `input` port with a UTF-8 JSON object. It keeps
the instance and invocation identifiers in working state.

If a flow parks for confirmation or a media job exceeds one minute, the agent
schedules a passive continuation and ends its turn. It does not poll.

An approved provisioning plan is still declarative output. The skill executes
only its exact approved actions through the official MCP or bounded PawFlow
operations, then probes readiness again. The validator rejects embedded shell,
commands, scripts, credentials, unpinned custom nodes, and model/LoRA downloads
without HTTPS, license, and SHA-256.

## Video execution and QA

`generate-video` passes its normalized JSON body to
`tool.generate_video`. The active or explicitly named video service must be a
configured trusted service, normally `comfyUIVideoGeneration`. A positive
partner cost estimate requires durable approval, and an estimate above
`comfyui.max_partner_cost_usd` fails closed.

The flow only validates that the media tool returned an output reference.
Before delivery, the skill must still inspect the exact history-declared
artifact, run ffprobe, sample frames, check duration/resolution/codecs/audio,
inspect continuity and identity, and copy the validated artifact to FileStore.

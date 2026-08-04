# PFP Developer Guide

This guide explains how to develop, load, test, unload, and release PawFlow Package (`.pfp`) runtime objects. It focuses on the local development loop before packaging, especially service providers such as a new image generation provider.

For package format and security details, see [PawFlow Packages](PFP_PACKAGES.md). For publishing signed artifacts and registries, see [PFP Publisher Guide](PFP_PUBLISHER_GUIDE.md).

## Development Loop

Use a `.pfpdir` source directory while developing. It is not signed and is not copied into the package content store when loaded in dev mode.

```text
my-image-provider.pfpdir/
  pfp.json
  content/
    service-providers/
      image/
        provider.py
```

Typical loop:

```text
/pfp dev-load ./my-image-provider.pfpdir --include service_provider:image --secret api_key=my_provider_key
generate_image(prompt="a cyberpunk cat", image_service="my-image-provider", width=1024, height=1024)
/pfp dev-unload dev.my-image-provider
```

`dev-load` defaults to conversation scope when a conversation id is available. It records the package as `dev: true`, registers the selected runtime objects, and points their `content_dir` directly at the `.pfpdir` source tree. The relay Python runner reads the entrypoint from that source tree on every invocation, so code edits are picked up without rebuilding.

Re-run `dev-load --replace` when you change manifest-level data: `service_id`, `operations`, `provides`, `secrets`, `allowed_tools`, `allowed_services`, `requires`, object ids, or paths.

## Manifest Example

```json
{
  "format": "pawflow.package.v1",
  "package": "dev.my-image-provider",
  "version": "0.1.0",
  "description": "Development image generation provider",
  "developer": {
    "email": "dev@example.com",
    "public_key": "ed25519:REPLACE_FOR_RELEASE"
  },
  "objects": [
    {
      "id": "service_provider:image",
      "type": "service_provider",
      "name": "my-image-provider",
      "service_id": "my-image-provider",
      "path": "content/service-providers/image/provider.py",
      "runner": "python",
      "provides": ["media.image_generation"],
      "operations": {
        "generate": {"description": "Generate an image from a prompt"}
      },
      "secrets": [
        {
          "name": "api_key",
          "env": "MY_PROVIDER_API_KEY",
          "required": true
        }
      ],
      "allowed_tools": [],
      "allowed_services": []
    }
  ]
}
```

Rules:

- `package` is the durable package id used by install, update, and unload.
- Object ids must stay stable, for example `service_provider:image`.
- `service_id` is the service name users pass to media tools, for example `image_service="my-image-provider"`.
- `operations` must declare every callable service-provider operation; an empty or missing map is not a wildcard. Automatic media resolution selects providers by the exact operation required by the current tool call, for example text-to-video requires `generate` while image-to-video requires `image_to_video` or `reference_to_video`.
- `runner` must be explicit for executable objects. Use `python`; the entrypoint runs in the selected relay, so it can use relay-local filesystem paths and relay-local binaries directly. Tool and service-provider calls use the agent-specific default relay when present, otherwise the conversation default relay; task, task-verification, and delegate sub-conversations inherit the parent conversation relay bindings, conversation-scoped package services, conversation-scoped package tools, tool/MCP filters, and installed package dependency records unless the exact sub-conversation defines its own values. Flow tasks use their required per-task `relay` parameter. A flow can define multiple relay parameters and point different imported tasks at different relays, for example `relay: "${relay_extract}"` on one task and `relay: "${relay_publish}"` on another. For protected server-side execution, pass the provisioned `srv_min_*` server execution relay id through a normal flow parameter. Calls back into PawFlow tools/services are brokered through `pfp.call_tool(...)` and `pfp.call_service(...)` and require matching grants. Package-qualified calls such as `pfp.call_tool("other.pkg/tool:shared")` resolve by package, optional version or version constraint, and object id even when another scope has a tool with the same name; they still obey the conversation and per-agent tool availability filters.
- PFP flows deployed from the agent flow actions may use either their repository FQN or their flow `id`. PawFlow stores the canonical `fqn`, repository scope, owner, conversation id, and agent name on the deployed instance, then reuses those fields for later `start_flow` calls and restart restore so package flow tasks receive the same runtime context.
- Manual starts for one-shot flows can pass `entry_task_ids` to run only selected root one-shot triggers. The chat UI exposes this as a checkbox list for flows with root one-shot triggers and no persistent sources; omitted `entry_task_ids` keeps the legacy behavior and arms every one-shot root. `executeFlow` subflow invocations suppress unrelated one-shot roots and inject only into the mapped input port, when one is configured.
- Required secrets are declared by logical package-local name and injected as environment variables at runtime. Secret values never go into `pfp.json`.

## SDK Surface For PFP Entrypoints

The `pawflow` SDK module shipped with package runtimes exposes three symbols: `pfp`, `tools`, and `fs`. Only `pfp` is available to PFP entrypoints. The `tools` and `fs` surfaces are reserved for non-PFP container scripts (PawCode SDK, ad-hoc relay scripts) and are blocked at runtime when called from a PFP package.

| Symbol | Available in a PFP entrypoint? | How a PFP must reach the same capability |
|---|---|---|
| `pfp.input()`, `pfp.payload`, `pfp.package`, `pfp.context` | Yes | n/a |
| `pfp.result(value)`, `pfp.error(message)` | Yes | n/a |
| `pfp.flowfile(...)`, `pfp.artifact(...)` | Yes | n/a |
| `pfp.call_tool(name, **args)` | Yes (broker-authorized) | n/a |
| `pfp.call_service(name, op, **args)` | Yes (broker-authorized) | n/a |
| `pfp.browser.semantic.list/get/invoke(...)` | Yes (broker-authorized) | Declare `permissions.browser.semantic` for the target package, operations, and nodes |
| `tools.call(...)`, `tools.get_schema(...)` | **No** — `_ensure_connected()` raises because the relay env scrubs `PAWFLOW_TOOL_RELAY_URL`/`_TOKEN` for PFP runs | Use `pfp.call_tool(...)` with a declared `allowed_tools` grant |
| `fs.read_file`, `fs.write_file`, `fs.exec`, `fs.list_dir`, `fs.grep`, `fs.stat`, `fs.exists`, `fs.delete_file`, `fs.mkdir`, `fs.edit`, `fs.git_status`, `fs.git_commit` | **No** — same scrubbed-env block | Either open files/spawn binaries directly inside the relay container (no broker needed for relay-local I/O) or use `pfp.call_tool("read", path=...)`, `pfp.call_tool("write", ...)`, `pfp.call_tool("bash", ...)`, etc. with the matching grant |

Two separate trust boundaries are at play here. A PFP entrypoint may freely read/write/exec inside its relay sandbox using the Python standard library because that surface is already constrained by the relay container, not by the broker. The broker only authorizes calls that re-enter PawFlow tools or services through `pfp.call_tool(...)` / `pfp.call_service(...)`. Going through `tools.*` or `fs.*` would bypass the broker entirely and is therefore blocked at the env layer: the PFP relay runner removes `PAWFLOW_TOOL_RELAY_URL`, `PAWFLOW_TOOL_RELAY_TOKEN`, and `PAWFLOW_PFP_RELAY_RUNNER` from the child process environment, so `_ensure_connected()` in the SDK raises a `ConnectionError` immediately.

If you find yourself wanting `fs.read_file("/etc/passwd")` from a PFP entrypoint, open it with `open(...)` instead. If you want a PawFlow-side `read` tool call (for example to read a FileStore artifact through the same allowlist the rest of PawFlow uses), declare it in `allowed_tools` and use `pfp.call_tool("read", ...)`.

## Agent Hook Entrypoint

Packages can install `agent_hook` runtime objects. They are stored as repository resources and must be enabled from conversation hook bindings before they run.

Manifest object example:

```json
{
  "id": "agent_hook:bash_guard",
  "type": "agent_hook",
  "name": "bash_guard",
  "path": "content/hooks/bash_guard.py",
  "runner": "python",
  "events": ["pre_tool_call"],
  "allowed_tools": [],
  "allowed_services": []
}
```

Entrypoint example:

```python
from pawflow import pfp

event = pfp.payload.get("event", {})
payload = event.get("payload", {})

if event.get("event") == "pre_tool_call" and payload.get("tool_name") == "bash":
    command = (payload.get("arguments") or {}).get("command", "")
    if "rm -rf" in command:
        pfp.result({"decision": "block", "reason": "destructive command"})
        raise SystemExit(0)

pfp.result({"decision": "allow", "payload": payload})
```

Hook decisions are `allow`, `block`, or `replace`. For `replace`, return the modified `payload` object expected by the event. Hooks can request broker-authorized host calls with `pfp.call_tool` or `pfp.call_service` when the manifest declares the matching grants.

## Image Provider Entrypoint

PFP media providers should not return image, video, or audio bytes in JSON. Large media should be written to the controlled output directory and returned by reference.

```python
from pathlib import Path
import os

from pawflow import pfp

payload = pfp.payload
operation = payload.get("operation", "")
args = payload.get("arguments", {})

if operation != "generate":
    pfp.error(f"unsupported operation: {operation}")
    raise SystemExit(1)

api_key = os.environ.get("MY_PROVIDER_API_KEY", "")
if not api_key:
    pfp.error("MY_PROVIDER_API_KEY is missing")
    raise SystemExit(1)

out_dir = Path(pfp.context["output_dir"])
out_path = out_dir / "image.png"

# Replace this with the provider SDK/API call.
# The provider should write the final PNG/JPEG/WebP directly to out_path.
# call_provider(
#     api_key=api_key,
#     prompt=args.get("prompt", ""),
#     width=int(args.get("width", 1024)),
#     height=int(args.get("height", 1024)),
#     output_path=out_path,
# )

out_path.write_bytes(b"...real image bytes...")

pfp.result(pfp.artifact(
    "image",
    "image.png",
    "image/png",
    filename="image.png",
))
```

The artifact path must be relative to `pfp.context["output_dir"]`. PawFlow rejects absolute paths, `..`, missing files, and paths that escape the output directory. The runtime records artifact size and SHA-256, then hands a file path to the media handler. FileStore destinations copy the generated file in chunks instead of carrying it as JSON/base64.

Use the same artifact pattern for other media kinds:

```python
pfp.result(pfp.artifact("video", "video.mp4", "video/mp4", filename="video.mp4"))
pfp.result(pfp.artifact("audio", "track.mp3", "audio/mpeg", filename="track.mp3"))
```

## Local Entrypoint Test

Before loading into PawFlow, test the script envelope locally from the `.pfpdir` root:

```bash
PYTHONPATH=/workspace/docker/pawflow_sdk python content/service-providers/image/provider.py <<'JSON'
{
  "format": "pawflow.package.runtime.invoke.v1",
  "kind": "service",
  "package": {
    "package": "dev.my-image-provider",
    "version": "0.1.0",
    "object_id": "service_provider:image"
  },
  "context": {
    "output_dir": "/tmp/pawflow-provider-test"
  },
  "payload": {
    "operation": "generate",
    "arguments": {
      "prompt": "a cat",
      "width": 512,
      "height": 512
    }
  }
}
JSON
```

Create `/tmp/pawflow-provider-test` first if your local script expects it. This verifies envelope parsing, SDK import, secret environment variables, and the JSON result format. Runtime host calls require PawFlow and should be tested through `dev-load`.

## Load And Test In PawFlow

Store the provider API key as a PawFlow secret, then bind the package-local secret name to that stored key:

```text
/pfp dev-load ./my-image-provider.pfpdir \
  --include service_provider:image \
  --secret api_key=my_provider_key
```

Then test the service through the builtin media tool:

```text
generate_image(
  prompt="a cyberpunk cat",
  image_service="my-image-provider",
  width=1024,
  height=1024
)
```

Execution path:

```text
generate_image
  -> ServiceRegistry.resolve("my-image-provider")
  -> PackageRuntimeService.generate(...)
  -> invoke("generate", args)
  -> provider.py receives pfp.context["output_dir"]
  -> provider.py writes image.png
  -> provider.py returns artifact.path
  -> PawFlow validates and stores the artifact
  -> response returns fs://filestore/...
```

If you modify only `provider.py`, run the media tool again. If you modify `pfp.json`, reload the dev package:

```text
/pfp dev-load ./my-image-provider.pfpdir \
  --include service_provider:image \
  --secret api_key=my_provider_key \
  --replace
```

Unload the dev package when finished:

```text
/pfp dev-unload dev.my-image-provider
```

`dev-unload` removes installed runtime objects from the selected scope. It does not delete the source directory and does not delete PawFlow secrets.

## Relay Binary Tools

Runtime code executes in the relay, not on the PawFlow server. If the relay image already contains the binary you need, call it directly. For example, a tool that wraps `tail` needs only an entrypoint:

```text
tail-tool.pfpdir/
  pfp.json
  content/
    tools/
      tail_file/
        main.py
```

```json
{
  "id": "tool:tail_file",
  "type": "tool",
  "name": "tail_file",
  "path": "content/tools/tail_file/main.py",
  "runner": "python",
  "parameters": {
    "path": {"type": "string", "required": true},
    "lines": {"type": "integer", "default": 20}
  }
}
```

```python
import subprocess

from pawflow import pfp

args = pfp.payload["arguments"]
path = str(args["path"])
lines = int(args.get("lines") or 20)

proc = subprocess.run(
    ["tail", "-n", str(lines), path],
    text=True,
    capture_output=True,
)

if proc.returncode != 0:
    pfp.error(proc.stderr.strip() or "tail failed")
    raise SystemExit(1)

pfp.result(proc.stdout)
```

If the binary is not part of the relay image, ship a Linux relay build inside `content/bin/linux-amd64/` and call it by package-relative path. Inspect exposes the package size and content size before install; PawFlow does not reject a package just because it carries a large binary.

## Tool Or Service Dependencies

If a package runtime object calls PawFlow tools or services through the runtime SDK, use `python` and declare every grant. These grants are not required for direct relay-local filesystem or binary access.

```json
{
  "id": "tool:normalize-and-generate",
  "type": "tool",
  "name": "normalize-and-generate",
  "path": "content/tools/normalize-and-generate/main.py",
  "runner": "python",
  "allowed_services": [
    {"name": "my-image-provider"}
  ],
  "allowed_tools": [
    {"name": "read"}
  ]
}
```

Code:

```python
from pawflow import pfp

result = pfp.call_service(
    "my-image-provider",
    "generate",
    prompt=pfp.payload["arguments"]["prompt"],
)

pfp.result(result)
```

For PFP service providers, `operation` is dispatched through the provider's
declared `operations` map; providers with no declared operations reject every
runtime operation. For built-in PawFlow services, `operation` is
dispatched to a public service method with keyword arguments and must return a
JSON-serializable value. Lifecycle, context, destructive reset, and
introspection methods such as `connect`, `disconnect`, `ensure_connected`,
`reset`, `status`, `validate`, and `get_parameter_schema` are not callable
through `pfp.call_service()`.

Automatic media-provider resolution applies the same scope priority to native
services and PFP providers: conversation scope wins over user scope, which wins
over global scope. A PFP provider is selectable only for the exact operation
requested by the tool, for example `remove_background` does not satisfy
`upscale_image` and `speech_to_video` does not satisfy `generate_video`.

Package-qualified grants are also supported for inter-PFP dependencies. The referenced package and object must already be installed before the dependent object can be selected. Native grants such as `{ "name": "read" }` authorize only unqualified host calls like `pfp.call_tool("read")`; they do not authorize `other.pkg/tool:read`. If a grant contains a package version or version constraint, that constraint is checked against the installed object and carried into the final tool/service dispatch so an older installed object with the same name cannot satisfy the call.

## Extension-defined repositories

Use `repository_type` and `repository_resource` when a feature needs its own
repository without becoming a built-in PawFlow resource type.

Owner manifest objects:

```json
[
  {
    "id": "repository_type:avatar",
    "type": "repository_type",
    "name": "avatar",
    "resource_type": "example.avatar",
    "schema_version": "1",
    "schema": "content/repository/avatar.schema.json",
    "contributions": "dependencies",
    "mutable": true,
    "asset_extensions": [".vrm", ".webp"]
  },
  {
    "id": "repository_resource:luna",
    "type": "repository_resource",
    "name": "luna",
    "resource_type": "example.avatar",
    "schema_version": "1",
    "path": "content/avatars/luna.json",
    "assets": [
      {"id": "model", "path": "content/avatars/luna.vrm"},
      {"id": "preview", "path": "content/avatars/luna.webp"}
    ]
  }
]
```

`resource_type` must be a lowercase dotted or dashed identifier and is owned
by one accessible installed package. `schema_version` is required on both
objects and must match exactly. `schema` must be a valid, self-contained Draft
2020-12 JSON Schema; `$ref` values may use only local `#` fragments. The schema
and each resource document must be JSON objects.

`contributions` is required:

- `owner` accepts resources only from the package declaring the type;
- `dependencies` also accepts a resource from a package that explicitly
  depends on the owner package.

`mutable` is required. When true, Python runtime objects from the owner package
may mutate scoped JSON resources. It does not grant contributor packages
write access. The declared `asset_extensions` must be a subset of PawFlow's
inert repository allow-list. Scripts, HTML, Python, WebAssembly, and external
URLs do not become repository assets through this object.

A pack that contributes resources declares the normal package dependency:

```json
{
  "package": "example.avatar-pack",
  "version": "1.0.0",
  "dependencies": [
    {"package": "example.avatar-runtime", "version": "^1.0.0"}
  ],
  "objects": [
    {
      "id": "repository_resource:nova",
      "type": "repository_resource",
      "name": "nova",
      "resource_type": "example.avatar",
      "schema_version": "1",
      "path": "content/avatars/nova.json",
      "assets": [{"id": "model", "path": "content/avatars/nova.vrm"}]
    }
  ]
}
```

Selecting a new `repository_resource` from the same owner package without also
selecting its new `repository_type` is reported as a missing dependency.
Contributors cannot satisfy ownership merely by spelling the same logical type
in their own package.

Owner runtime code uses the brokered SDK:

```python
from pawflow import pfp

rows = pfp.repository.list("example.avatar")
current = pfp.repository.get("example.avatar", "luna")
created = pfp.repository.create(
    "example.avatar",
    "custom-luna",
    {"format": "example.avatar.v1", "title": "Custom Luna"},
)
updated = pfp.repository.update(
    "example.avatar",
    "custom-luna",
    {"format": "example.avatar.v1", "title": "Renamed Luna"},
)
deleted = pfp.repository.delete("example.avatar", "custom-luna")
```

The host accepts no missing parameter and supplies no implicit type, name, or
document. It derives package/user/conversation/scope from the verified runtime
request, verifies owner and `mutable`, validates every document against the
installed schema, and audit-logs the operation. Runtime CRUD is JSON-only;
packaged binary assets stay immutable and hash-addressed in their PFP content
store. `list` and `get` return each packaged asset with a stable
`pfp-asset:<type>/<scope>/<name>/<id>` reference and a hash-addressed `url`.
The URL is served through the authenticated extension-asset route, which
revalidates the user/conversation scope, contributing package, stored path,
content hash, package kill switch, and per-conversation enable state. Assets
from dependent packs use the pack's content store; the owner runtime never
needs direct filesystem access.

The reference implementation is
`packages/pawflow.avatar-runtime.pfpdir`. It demonstrates a repository owner,
a package-owned Python repository handler, authenticated model URLs, a lazy
browser renderer, generic realtime-media subscriptions, semantic nodes, and
complete shutdown/GPU cleanup. Avatar packs depend on
`pawflow.avatar-runtime` and contribute only `repository_resource` objects;
they do not patch core or bundle renderer code again. The package README records
the authoring shape, model licensing rule, and reproducible vendor build.

## UI Extensions (ui.v1)

A package can ship a `ui_extension` object that injects JS / CSS into the
chat web UI through the versioned `ui.v1` slot and hook contract. PawFlow
serves the assets at `/chat/ext/<package>/<short_sha256>/<file>` with
per-file SHA-256 integrity verification, and the install plan blocks the
extension if it declares an incompatible `version_compat`.

Source layout:

```text
my-ui.pfpdir/
  pfp.json
  content/
    ui/
      extension.js
      extension.css       (optional)
      i18n/en.json        (optional, served on demand)
      models/avatar.glb   (optional inert file, served on demand)
```

Manifest:

```json
{
  "id": "ui_extension:hello",
  "type": "ui_extension",
  "name": "hello",
  "version_compat": "ui.v1",
  "assets": {
    "scripts": ["content/ui/extension.js"],
    "styles":  ["content/ui/extension.css"],
    "files": [
      {"id": "avatar-model", "path": "content/ui/models/avatar.glb"}
    ],
    "worklets": [
      {"id": "audio-processor", "path": "content/ui/audio-processor.js"}
    ]
  },
  "slots": [
    {"slot": "action_menu",     "id": "hello.open", "icon": "👋", "label_key": "hello.menu"},
    {"slot": "resources_collection", "id": "hello.section"},
    {"slot": "conversation_stage", "id": "hello.stage"}
  ],
  "hooks": ["boot", "conversation_changed", "resource_changed"]
}
```

Slots accepted in `ui.v1`: `action_menu`, `gear_menu`, `resources_panel`,
`sidebar_top`, `sidebar_bottom`, `header_actions`, `tab_bar`,
`conversation_stage`, `resources_collection`, and `composer_accessory`. The
last three hosts are hidden unless an enabled installed extension declares a
contribution, so a base install has no empty extension UI.

Hooks accepted in `ui.v1`: `boot`, `shutdown`, `conversation_changed`,
`conversation_created`, `conversation_deleted`, `message_appended`,
`message_streaming`, `tool_call_started`, `tool_call_completed`,
`command_submitted`, `command_result`, `before_send`, `agent_changed`,
`theme_changed`, `tab_switched`, `permission_mode_changed`, `sse_event`, and
`resource_changed`, plus the realtime media hooks `realtime_state_changed`,
`media_track_subscribed`, `media_track_unsubscribed`, and
`media_audio_frame`. Successful built-in resource create/update/copy/delete
operations emit `resource_changed` with `resource_type`, `operation`, `name`,
and scope metadata.

`scripts` and `worklets` accept only `.js`, `styles` only `.css`, and
i18n catalogs only `.json`. Each `worklets` entry requires a logical `id`
and a `path`; it is reviewed as executable code but is never auto-loaded.
`files` accepts inert JSON, SVG, images, fonts, WebAssembly, model,
texture, binary-buffer, and audio formats: `.json .svg .png .jpg .jpeg .webp
.woff .woff2 .wasm .glb .gltf .vrm .bin .ktx2 .basis .fbx .mp3 .wav .ogg
.m4a .aac .flac`. `.html` is always refused because same-origin HTML served
from `/chat/ext/...` could execute inline scripts under the user's session.

Executable/catalog assets are limited to 2 MiB each, inert files to 256 MiB
each, one UI object to 256 assets and 512 MiB total. Duplicate paths and
duplicate logical file IDs are rejected. Inert binaries remain opaque to the
reviewer and therefore require explicit human confirmation (`force`) at
install. A logical ID must match `^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$`.

Each declared script is loaded into the page same-origin from `/chat/ext/...`;
scripts keep their manifest insertion order. Inert `files` and executable
`worklets` are listed in the boot manifest but never auto-loaded.
`pfp.asset(idOrPath)` returns their hash-addressed authenticated URL, suitable
for an explicit call such as `audioContext.audioWorklet.addModule(url)`. Asset
responses recompute SHA-256, enforce
content-directory containment, set `nosniff` and immutable caching, expose an
explicit MIME type, and support one RFC byte range (`206`, `Content-Range`,
and `Accept-Ranges: bytes`). Malformed, multipart, or unsatisfiable ranges
return `416`. Undeclared, tampered, disabled, uninstalled, or unauthenticated
assets return `404`.

The extension JS calls `pawflow.register("<package_id>", function (pfp) {
... })` at top level. The `pfp` object exposes:

```javascript
pfp.id                   // your package id
pfp.asset(idOrPath)      // URL for a declared assets.files/worklets entry
pfp.context()            // frozen snapshot: user/conversation/agent/locale/theme/permission_mode
pfp.t(key, vars)         // i18n lookup, namespaced to your package
pfp.ui.slot(slot, id, render)
pfp.ui.openDialog(title, contentNode, opts?)
pfp.ui.closeDialog()
pfp.ui.openPanel(id, render)
pfp.ui.closePanel()
const off = pfp.on(hook, cb) // subscribe; returns an unsubscribe function
pfp.off(hook, cb)
pfp.publish(local, data) // inter-extension bus
const unsub = pfp.subscribe(local, cb) // also returns an unsubscribe function
pfp.call(action, body)   // POST to /api/ui with _ext: <package_id>
pfp.command(name, spec)  // register a slash command
```

`pfp.context()` returns a new frozen snapshot on every call; it never exposes
mutable references to PawFlow state. Package teardown fires `shutdown`, removes
hook/local-bus subscriptions, slots and commands, and closes package-owned
panels/dialogs. `window.pawflow.unregister(packageId)` exposes the same cleanup
path to the host runtime.

### Realtime media observations

The media hooks expose only the agent downlink; microphone capture and desktop
relay audio are not part of this contract.

- `realtime_state_changed` carries `state`, `transport`, `conversation`, and a
  millisecond `timestamp`. States include `connecting`, `listening`,
  `thinking`, `speaking`, `tool`, and `idle`.
- `media_track_subscribed` carries a frozen source descriptor. Legacy
  websocket audio uses `transport: "pcm"`, `format: "pcm16le"`, a sample
  rate, and channel count. LiveKit uses `transport: "livekit"` and also
  exposes the remote `track` and attached `element`.
- `media_audio_frame` is available for the legacy PCM transport and carries a
  frozen descriptor with copied `Float32Array` samples, `sample_rate`,
  `channels`, `frame_count`, and `duration_ms`. Mutating the copy cannot alter
  PawFlow playback.
- `media_track_unsubscribed` carries the prior frozen `source`, its `id`, and a
  reason such as `track_unsubscribed`, `reconnecting`,
  `conversation_changed`, or `session_stopped`.

Descriptors are shallow-frozen. LiveKit's embedded track and media element are
shared same-origin browser references, so extensions must treat them as
read-only observations. This is an API contract, not a browser security
boundary; the shared trust-domain rules below still apply.

Every media hook uses the normal `pfp.on(...)` unsubscribe contract. Disabling
an extension removes its listeners before queued callbacks run. Session stop,
LiveKit track unsubscribe/reconnect, and conversation switch detach sources
deterministically before the next conversation context is delivered.

### Semantic browser nodes

An extension can expose bounded JSON state and local actions without adding a
built-in agent tool:

```javascript
const id = pfp.semantic.register({
  id: 'stage.status',
  role: 'status',
  label: 'Stage status',
  parent: 'conversation',
  state: () => ({ selected: currentSelection }),
  actions: {
    select: {
      parameters: {
        name: { type: 'string', required: true }
      },
      run: args => select(args.name)
    }
  }
});
```

Node IDs are qualified as `<package>:<local-id>`. The package API can
`list()`, `get(id)`, `invoke(id, action, arguments)`, and
`unregister(id)` only for its own nodes. Roles, labels, schemas, arguments,
state snapshots, and results are validated and bounded; snapshots are deeply
frozen and may not contain functions, DOM nodes, cycles, or non-JSON values.
All nodes are removed when the package is disabled or unregistered.

A PFP runtime object reaches an active authorized tab through the SDK:

```python
nodes = pfp.browser.semantic.list("my.semantic-ui")
node = pfp.browser.semantic.get(
    "my.semantic-ui", "my.semantic-ui:stage.status")
result = pfp.browser.semantic.invoke(
    "my.semantic-ui",
    "my.semantic-ui:stage.status",
    "select",
    {"name": "primary"},
)
```

The runtime object must declare the signed grant below. A target package grant
also becomes an install dependency; use the caller's own package ID when the UI
extension and tool ship together.

```json
{
  "permissions": {
    "browser": {
      "semantic": [
        {
          "package": "my.semantic-ui",
          "operations": ["list", "get", "invoke"],
          "nodes": ["my.semantic-ui:stage.status"]
        }
      ]
    }
  }
}
```

PawFlow correlates requests through the authenticated per-tab SSE channel. The
server derives user, conversation, caller package, and grant from the runtime
envelope; the browser cannot override them. One eligible tab is selected
directly, or the unique active tab when several are registered. No tab, several
equally active tabs, a stale/disconnected tab, a disabled or missing extension,
a mismatched result context, timeout, and payloads above 64 KiB all fail
explicitly. Requests and results are audit logged.

The complete installable example is
`docs/examples/pfp/semantic_ui_tool.pfpdir`. It ships one UI node and one PFP
tool in the same package; no semantic agent tool is built into core.

UI extensions live in the user's own browser tab, same origin as the page.
They have full DOM access; PawFlow scans the JS files at install time for
known exfiltration patterns and surfaces findings in the install plan, but
the trust boundary is browser-side and the install consent is the real
gate. Server-side handlers triggered by `pfp.call(...)` execute inside the
relay subprocess sandbox; they cannot exfiltrate or escalate without an
`allowed_tools` / `allowed_services` grant accepted at install.

**All installed UI extensions share one trust domain.** Because every
extension runs as plain JavaScript in the user's tab, an installed
extension A can redefine `window.pawflow`, read B's DOM, and call
`/api/ui` with `_ext: "victim.pkg"` to invoke B's handlers with B's
`allowed_tools` grants. The `_ext` request field is self-declared by the
browser caller, not a server-enforced binding to a specific extension.
This is the same trust model as Chrome extensions, VS Code extensions,
or any plug-in system that runs in a shared address space: install
consent is the gate, not runtime isolation. PawFlow logs every UI
handler invocation with its `_ext` value so a human reviewer can spot
abuse; the kill switch (`PAWFLOW_UI_EXTENSIONS_DISABLED=1`) and
per-conversation `disabled_extensions` blacklist let a user contain a
misbehaving extension without uninstalling. Real per-extension
isolation would require sandboxed iframes plus a postMessage broker; it
is not implemented today.

### Server handlers

A `ui_extension` may declare server handlers triggered by
`pfp.call(action, body)` in the browser. They run in the same relay
subprocess sandbox as PFP tools — the entrypoint hash is verified on
every call, the relay child sees a scrubbed env, and host-side
`pfp.call_tool` / `pfp.call_service` requests are re-authorized through
`PackageCapabilityBroker` before running.

```json
{
  "id": "ui_extension:hello",
  "type": "ui_extension",
  "version_compat": "ui.v1",
  "assets": {"scripts": ["content/ui/extension.js"]},
  "slots": [...],
  "hooks": [...],
  "handlers": [
    {
      "action": "hello.ping",
      "path": "content/handlers/ping.py",
      "runner": "python",
      "description": "Echo a value back to the UI extension",
      "allowed_tools": [{"name": "read"}],
      "allowed_services": [],
      "secrets": [{"name": "api_key", "env": "PROVIDER_API_KEY", "required": true}]
    }
  ]
}
```

Action names must match `^[a-z0-9][a-z0-9_.-]{0,127}$` and be unique
within the extension; the runner must be `python`. Each handler entry's
entrypoint is hash-locked at install time, so a tampered file on disk
refuses to run.

Handler implementation — mirrors the PFP tool/service pattern:

```python
# content/handlers/ping.py
from pawflow import pfp

payload = pfp.payload or {}
args = payload.get("arguments", {}) if isinstance(payload, dict) else {}
pfp.result({
    "echo": str(args.get("message") or ""),
    "action": payload.get("action", ""),
})
```

From the browser:

```javascript
pfp.call("hello.ping", { message: "world" })
   .then(function (resp) { console.log(resp.result); });
```

PawFlow routes `pfp.call(...)` through `/api/ui` with `_ext: "<package_id>"`
automatically set. The action dispatcher (`_handle_pfp_ui`) sits at the
top of the action-handler chain so any `_ext`-tagged body is captured
before the built-in dispatchers.

Dev loop:

```text
/pfp dev-load ./my-ui.pfpdir --include ui_extension:hello
# Reload /chat to load the new assets. Edit content/ui/extension.js, the
# next page reload picks up the changes (the URL hash changes when the
# file changes; the browser fetches the new version).
/pfp dev-unload my.ui-package
```

A starter template lives at `docs/examples/pfp/ui_extension_hello.pfpdir/`.

## Standalone Pages (web_app / webapp.v1)

A `ui_extension` only injects JS/CSS into the existing chat page — it never
ships `.html`, and it never gets its own URL. A `web_app` object is the
opposite: a standalone page (html/js/css) served at its own authenticated
route, separate from `/chat`. Use it when a package needs a dedicated UI
(a purpose-built dashboard, an operator console) rather than a slot/panel
inside the chat shell.

Source layout:

```text
my-app.pfpdir/
  pfp.json
  content/
    webapp/
      index.html
      app.js
      style.css
```

Manifest:

```json
{
  "id": "web_app:dashboard",
  "type": "web_app",
  "name": "dashboard",
  "version_compat": "webapp.v1",
  "entry": "content/webapp/index.html",
  "assets": [
    "content/webapp/index.html",
    "content/webapp/app.js",
    "content/webapp/style.css"
  ]
}
```

Rules:

- `assets` is a flat list of every file the page needs; `entry` must be one
  of them. Allowed extensions: `.html .js .css .json .svg .png .jpg .jpeg
  .webp .woff .woff2` — `.html` is allowed here, unlike `ui_extension`,
  because this route is not injected into the chat page DOM.
- A package may declare more than one `web_app` object; each gets its own
  route keyed by its object `name`.
- Once installed, the page is served at `/apps/<package>/<name>/` (the
  `entry` file) and `/apps/<package>/<name>/<sha256>/<file>` (every other
  asset, immutably cached like `/chat/ext/...`). Both routes require the
  same authenticated session as `/chat` — there is no anonymous access to
  an installed web_app.
- **Trust model**: the page runs on the same origin and under the same
  session as `/chat`. It can read/write same-origin browser state and call
  PawFlow APIs with the user's ambient session cookie. This is the same
  shared-trust-domain model already documented for `ui_extension` — install
  consent is the security gate, not runtime isolation. A `web_app` does
  *not* share the chat page's DOM/`window` with other extensions (it is a
  separate page load), but it is not sandboxed from the rest of PawFlow
  either. Do not install a `web_app` package you have not reviewed.
- To reach PawFlow tools/services from the page's own JS, call the normal
  `/api/agent` or `/api/ui` endpoints directly (same session cookie) —
  there is no `pfp.call(...)` broker for `web_app` pages in this version;
  that pattern is currently only wired for `ui_extension` server handlers.
- The Resources sidebar's Packages section shows a ↗ link next to any
  installed package that has a `web_app` object, opening it in a new tab —
  this is the "button in the main PawFlow interface" a package needs, with
  no extra `ui_extension` object required.

Dev loop:

```text
/pfp dev-load ./my-app.pfpdir --include web_app:dashboard
# Open /apps/dev.my-app/dashboard/ directly, or use the ↗ link in the
# Packages sidebar. Re-run dev-load --replace after editing pfp.json;
# asset content edits take effect on next request (dev packages skip the
# install-time hash check).
/pfp dev-unload dev.my-app
```

## MCP Servers (mcp_server)

A package that depends on an MCP server no longer needs a manual reconnection
step after install. Declare a `mcp_server` object pointing at a JSON file with
the same fields the `mcp` resource type already understands (`url`/`transport`
for http, `command`/`args`/`env` for stdio, `auth` for headers) and it installs
as a ready-to-use `mcp` resource. See "MCP Servers (mcp_server)" in
[PFP_PACKAGES.md](PFP_PACKAGES.md) for the manifest shape, secret binding, and
risk model. As with any MCP server, the connection still has to be explicitly
enabled at conversation or agent level before an agent can use its tools —
install wires up the connection, it does not silently activate it.

## Release

When the dev package works, create a signed release artifact:

```text
/pfp key-create
/pfp build ./my-image-provider.pfpdir --key-env PAWFLOW_PFP_SIGNING_KEY
/pfp inspect ./my-image-provider.pfpdir/dist/dev.my-image-provider-0.1.0.pfp
/pfp install ./my-image-provider.pfpdir/dist/dev.my-image-provider-0.1.0.pfp \
  --include service_provider:image \
  --secret api_key=my_provider_key \
  --force
```

Release mode differs from dev mode:

- install requires a valid `.pfp` signature;
- package contents are copied into the scoped content store;
- runtime entrypoint hashes are enforced;
- source edits no longer affect the installed package;
- update/uninstall use the signed package install record.

## Troubleshooting

- `PFP service operation is not declared`: add the operation to `operations` in `pfp.json` or call an existing operation.
- `PFP media artifact.path is required`: return `pfp.artifact(...)` with a non-empty relative path.
- `PFP media artifact escapes output_dir`: do not use absolute paths or `..` in artifact paths.
- `PFP runtime must emit exactly one JSON result line`: send debug output to stderr, not stdout.
- `PFP secret binding is missing`: add `--secret logical_name=stored_secret_key` to `dev-load` or install.
- Code edits are not visible: verify you used `dev-load` on a `.pfpdir`; signed installs use copied content and hash checks.

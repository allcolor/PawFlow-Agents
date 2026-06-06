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
    "styles":  ["content/ui/extension.css"]
  },
  "slots": [
    {"slot": "action_menu",     "id": "hello.open", "icon": "👋", "label_key": "hello.menu"},
    {"slot": "resources_panel", "id": "hello.section"}
  ],
  "hooks": ["boot", "conversation_changed"]
}
```

Slots accepted in `ui.v1`: `action_menu`, `gear_menu`, `resources_panel`,
`sidebar_top`, `sidebar_bottom`, `header_actions`, `tab_bar`.

Hooks accepted in `ui.v1`: `boot`, `shutdown`, `conversation_changed`,
`conversation_created`, `conversation_deleted`, `message_appended`,
`message_streaming`, `tool_call_started`, `tool_call_completed`,
`command_submitted`, `command_result`, `before_send`, `agent_changed`,
`theme_changed`, `tab_switched`, `permission_mode_changed`, `sse_event`.

Assets are restricted to `.js .css .json .svg .png .jpg .jpeg .webp
.woff .woff2`. `.html` assets are refused because same-origin HTML served
from `/chat/ext/...` could execute inline scripts under the user's session.
Each declared script is loaded into the page same-origin from `/chat/ext/...`;
scripts keep their manifest insertion order, but all installed UI extensions
share the same browser trust domain.

The extension JS calls `pawflow.register("<package_id>", function (pfp) {
... })` at top level. The `pfp` object exposes:

```javascript
pfp.id                   // your package id
pfp.t(key, vars)         // i18n lookup, namespaced to your package
pfp.ui.slot(slot, id, render)
pfp.ui.openDialog(title, contentNode, opts?)
pfp.ui.closeDialog()
pfp.ui.openPanel(id, render)
pfp.ui.closePanel()
pfp.on(hook, cb)         // subscribe to a ui.v1 hook
pfp.off(hook, cb)
pfp.publish(local, data) // inter-extension bus
pfp.subscribe(local, cb)
pfp.call(action, body)   // POST to /api/ui with _ext: <package_id>
pfp.command(name, spec)  // register a slash command
```

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

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
- `runner` must be explicit for executable objects. Use `python`; the entrypoint runs in the conversation's default relay, so it can use relay-local filesystem paths and relay-local binaries directly. Calls back into PawFlow tools/services are brokered through `pfp.call_tool(...)` and `pfp.call_service(...)` and require matching grants.
- Required secrets are declared by logical package-local name and injected as environment variables at runtime. Secret values never go into `pfp.json`.

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

Package-qualified grants are also supported for inter-PFP dependencies. The referenced package and object must already be installed before the dependent object can be selected.

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

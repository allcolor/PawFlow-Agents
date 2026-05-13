# PawFlow Packages (.pfp)

PawFlow Package files are signed zip artifacts for distributing PawFlow resources. A package can contain multiple objects, and install always goes through inspection plus a selectable install plan. The trust boundary is pre-install review: after a package object is installed, it is expected to behave like an installed PawFlow tool, service, or flow task, constrained by the capabilities the user accepted during installation.

For local package development workflows such as `dev-load`, service provider testing, and file-backed media artifacts, see [PFP Developer Guide](PFP_DEVELOPER_GUIDE.md). For publisher operations such as registry hosting, artifact release, versioning, and key rotation, see [PFP Publisher Guide](PFP_PUBLISHER_GUIDE.md).

## Artifact Layout

Developers work in a source directory:

```text
my-package.pfpdir/
  pfp.json
  content/
    agents/
    prompts/
    skills/
    themes/
    tasks/
    flows/
    services/
    tools/
```

`/pfp build` produces a signed artifact:

```text
my-package-1.0.0.pfp
  pfp.json
  pfp.lock.json
  signature.ed25519
  content/...
```

`pfp.lock.json` records the SHA-256 hash of every package file. `signature.ed25519` signs the canonical manifest plus lock. Install requires a verified `.pfp`; unsigned `.pfpdir` directories are only for development inspection/build workflows. Inspect returns both per-object details and an aggregate `capabilities` summary so UI and CLI clients can show package size, content size, file count, runtime objects, brokered PawFlow tool/service grants, package dependencies, provided capabilities, and required secrets before install. When the package is already installed, inspect also returns `update_diff` with version movement and per-object add/update/remove/unchanged status for update review. The slash/action layer also adds a compact `display` review for text clients.

## Manifest

```json
{
  "format": "pawflow.package.v1",
  "package": "community.wavespeed",
  "version": "1.0.0",
  "developer": {
    "email": "dev@example.com",
    "public_key": "ed25519:..."
  },
  "description": "WaveSpeed media provider package",
  "origin": {"source": "https://github.com/example/pawflow-wavespeed"},
  "dependencies": [
    {"package": "community.media-core", "version": "1.0.0"}
  ],
  "objects": [
    {
      "id": "skill:community.wavespeed.help",
      "type": "skill",
      "name": "community.wavespeed.help",
      "path": "content/skills/help/SKILL.md"
    },
    {
      "id": "service:community.wavespeed.image",
      "type": "service_definition",
      "name": "community.wavespeed.image",
      "path": "content/services/wavespeed-image/service.json",
      "requires": ["secret:WAVESPEED_API_KEY"],
      "provides": ["media.image_generation"],
      "allowed_tools": [
        {"name": "read"},
        {"package": "community.media-core", "object": "tool:normalize_image"}
      ],
      "allowed_services": [
        {"package": "community.media-core", "object": "service:asset_store"}
      ]
    }
  ]
}
```

Supported installable object types in the first implementation are `agent`, `prompt`, `skill`, `theme`, `task_def`, `flow`, `service_definition`, `tool`, `service_provider`, `flow_task`, and `task_provider`. `task_def` is a PawFlow agent/task definition resource. `flow_task`/`task_provider` are processor types for flows: install registers a `TaskFactory` proxy so flows can parse, validate, and execute the new task type when a runtime runner is declared. PFP `tool` objects are installed as runtime proxies with provenance and declared capabilities. PFP `service_provider` objects are installed as `packageRuntime` service proxies and keep their declared `provides`, dependencies, operations, and allowed tool/service grants in service config.

`dependencies` declares package-level dependencies. Object-level `requires` can also reference another package with `"package:community.pkg@1.0.0"` or `{"package": "community.pkg", "version": "1.0.0"}`. `allowed_tools` and `allowed_services` accept builtin names, such as `{"name": "read"}`, and package-qualified grants, such as `{"package": "community.media-core", "object": "tool:normalize_image"}` or `"community.media-core/tool:normalize_image"`. These grants are only for brokered calls back into PawFlow through `pfp.call_tool(...)` and `pfp.call_service(...)`; they do not gate normal relay-local filesystem or binary access by the package process. Package-qualified grants are treated as dependencies: the referenced package, and the referenced object when one is named, must already be installed in the target scope or in the user scope before the dependent object can be selected for install.

Dependency `version` accepts exact versions and simple ranges: `>=1.0.0,<2.0.0`, `^1.2.0`, `~1.2.3`, comparison operators (`>`, `>=`, `<`, `<=`, `==`, `!=`), or `*`. Install and runtime checks require the installed package to satisfy the constraint. Updating a package is blocked when an installed dependent would no longer satisfy its declared constraint, unless `force` is explicit.

## Commands

```text
/pfp key-create
/pfp build ./my-package.pfpdir --key-env PAWFLOW_PFP_SIGNING_KEY [--out dist/pkg.pfp]
/pfp inspect ./dist/pkg.pfp
/pfp install ./dist/pkg.pfp --include skill:x,service:y --exclude flow:z --secret api_key=my_provider_key --force
/pfp dev-load ./my-package.pfpdir --include service_provider:image --secret api_key=my_provider_key
/pfp dev-unload community.wavespeed
/pfp update ./dist/pkg-1.1.0.pfp --force
/pfp registry add https://example.com/pawflow/index.json --name example --trusted
/pfp search wavespeed image
/pfp install community.wavespeed@1.0.0 --include skill:x --force
/pfp install community.wavespeed@1.0.0 --include skill:x --force --confirm-download
/pfp list
/pfp reload-tasks --scope user
/pfp uninstall community.wavespeed
/pfp export --package my.bundle --version 0.1.0 --include skill:a,agent:b --out ./my.bundle.pfpdir
```

The `manage_package` agent tool exposes the same actions: `key_create`, `build`, `inspect`, `install`, `update`, `uninstall`, `list_installed`, `export`, `dev_load`, `dev_unload`, `registry_add`, `registry_remove`, `registry_list`, `search`, and `reload_tasks`. `reload_tasks` rebuilds `TaskFactory` proxies from installed package records after a process restart or explicit runtime reset.

`dev_load`/`/pfp dev-load` is the unsigned development loop for local `.pfpdir` sources. It accepts the same `include`, `exclude`, `scope`, and `--secret name=stored_key` bindings as install, defaults to conversation scope when a conversation id is available, marks the package record as `dev: true`, and points runtime proxies directly at the source directory instead of copying to the signed content store. Use it while editing provider/tool/task code; the relay Python runner reads the entrypoint from that source directory on every invocation, so code edits take effect immediately. Re-run dev-load only when package metadata changes.

The web Resources sidebar exposes installed packages in a dedicated Packages section. Its install dialog calls the same inspect/install/update actions, shows selectable package objects, aggregate capabilities, required secret bindings, and update diffs before applying the selected plan. The same dialog can list/add/remove the user's configured registries, search them, show each result's source URL, package size, SHA-256 pin, and developer key metadata, then ask for explicit download confirmation before fetching a selected remote `.pfp`. Installed package rows can be uninstalled from the sidebar; regular uninstall keeps dependency protection, while force uninstall uses the same explicit override as `/pfp uninstall --force`.

Runtime objects can declare required secrets with `secrets`, for example `[{"name": "api_key", "env": "PROVIDER_API_KEY", "required": true}]`. Install requires an explicit binding from package-local secret name to an existing PawFlow secret key via repeated `--secret name=stored_key` flags or `manage_package(..., secret_bindings={"name": "stored_key"})`. PawFlow stores only the binding in package runtime metadata. Secret values are resolved at invocation time and injected into the relay runner environment under the declared `env` name; they are not added to runtime envelopes or install records.
Bindings are validated during install: a required package secret must be bound, and the referenced PawFlow secret key must already exist in conversation, user, or global scope.

Use `private_key_env`/`--key-env` for signing in normal workflows so private key material does not appear in chat history. `private_key` exists for direct programmatic tests and local automation only.

## Decentralized Registries

A registry is a static JSON index hosted by any developer or community:

```json
{
  "format": "pawflow.package.registry.v1",
  "registry": "example",
  "packages": [
    {
      "package": "community.wavespeed",
      "version": "1.0.0",
      "description": "WaveSpeed media provider",
      "pfp_url": "https://example.com/community.wavespeed-1.0.0.pfp",
      "package_size": 7340032,
      "sha256": "sha256:...",
      "developer_key": "ed25519:...",
      "tags": ["media", "image"],
      "objects": ["service:community.wavespeed.image"]
    }
  ]
}
```

Registry metadata is not trusted as executable authority. It is used for discovery, pre-download size disclosure, and optional SHA-256 pinning only. `package_size` is required so PawFlow can show the user the artifact size before downloading; the first remote inspect/install/update returns `requires_confirmation` with the size, URL, and hash, and the caller must repeat the action with `confirm_download=true` or `--confirm-download` to fetch the `.pfp`. The downloaded `.pfp` must still pass size match, registry SHA-256 match when present, signature verification, and file-hash validation before install. Marking a registry as `trusted` is user-facing provenance metadata for review surfaces; it does not bypass package verification or install consent.

## Security Model

- `.pfp` install requires a valid Ed25519 signature.
- Every archive path is normalized and rejected if it is absolute, escapes the package, or contains unsafe characters.
- Registry refs and direct URLs show package size before download and require explicit confirmation before fetching. Local inspect shows package size, uncompressed content size, and file count before install; there is no arbitrary PFP size cap. Users decide whether a package is acceptable before installing it.
- Installation writes only selected objects from the install plan.
- When at least one object is installed, the verified package payload is copied into a scoped local content store under the package repository. Runtime proxies reference that stable `content_dir` plus their signed entrypoint path; they never depend on the original `.pfp` file remaining on disk.
- Installed resources receive `installed_from` provenance with package id, version, object id, file hash, package hash, and developer public key.
- PFP runtime proxies validate their installed entrypoint before invocation: the file must still live under the scoped package content directory and its SHA-256 must match the signed install provenance. Dev-loaded `.pfpdir` packages still enforce path containment, but skip hash mismatch failures so source edits take effect immediately.
- Skills still pass the existing skill review pipeline. Review-required skills need an explicit `--force` after inspection; blocked skills cannot be installed.
- Third-party code-bearing objects execute only through a declared runtime runner in the conversation's default relay. Package code is not imported into the PawFlow server process and does not execute directly on the server.
- Package code can use relay-local filesystem paths and relay-local binaries directly. Package tools/services may only call PawFlow brokered tools/services through `pfp.call_tool(...)` or `pfp.call_service(...)` when those calls were declared in `allowed_tools`/`allowed_services` and accepted during install; package-qualified grants require the referenced package and object dependency to be installed.
- Required PFP secrets must be declared and explicitly bound during install. Runtime envelopes carry binding names only; secret values are resolved at invocation time and injected into the runner environment.
- `PackageCapabilityBroker` centralizes runtime authorization for future package execution. It authorizes builtin grants such as `{"name": "read"}` and package-qualified grants such as `{"package": "community.media-core", "object": "tool:normalize_image"}`, then verifies the referenced package and object are installed before allowing the call.
- Registry downloads verify the package SHA-256 when the registry provides one.
- Uninstall uses the local install registry and does not remove secrets.

## Developer Checklist

1. Create a `.pfpdir` with `pfp.json` and package files under `content/`.
2. Use stable object ids in the form `type:name`; update/uninstall records use those ids.
3. Put code-bearing package entrypoints under the package content tree and declare `runner: "python"` explicitly.
4. Declare every host call in `allowed_tools` or `allowed_services`; runtime code cannot expand its grants after install.
5. Declare required secrets with package-local names and environment variable names. Do not put secret values in package files.
6. Build with `/pfp build ... --key-env ENV_NAME` so the private signing key stays outside chat and shell history.
7. Inspect the signed `.pfp`, verify capabilities and update diff, then install selected objects.

Python entrypoints can import the lightweight SDK with `from pawflow import pfp`. The SDK exposes `pfp.input()` plus cached `pfp.payload`, `pfp.package`, and `pfp.context`; `pfp.result(value)` and `pfp.error(message)` emit `result.v1`; `pfp.call_tool(name, **arguments)` and `pfp.call_service(name, operation, **arguments)` emit brokered `host_call.v1` requests; `pfp.flowfile(content, attributes)` builds task result descriptors for `pfp.result(flowfiles=[...])`; and `pfp.artifact(kind, path, content_type, filename)` builds file artifact descriptors for large media results.

Media service providers should not return image/video/audio bytes or base64 in JSON. For `media.image_generation`, `media.video_generation`, and `media.audio_generation`, PawFlow passes a controlled `pfp.context["output_dir"]` to the subprocess. The provider writes large output files under that directory and returns a relative artifact path:

```python
from pathlib import Path
from pawflow import pfp

out = Path(pfp.context["output_dir"]) / "image.png"
# call_provider(..., output_path=out)

pfp.result(pfp.artifact(
    "image",
    "image.png",
    "image/png",
    filename="image.png",
))
```

The package runtime resolves the relative path, rejects escapes outside `output_dir`, records size and SHA-256 metadata, and returns an `image_path`, `video_path`, or `audio_path` to PawFlow media handlers. FileStore destinations copy that file in chunks, avoiding JSON/base64 expansion and avoiding an extra full-size media buffer in memory.

Example inter-PFP grant:

```json
{
  "id": "service_provider:renderer",
  "type": "service_provider",
  "name": "renderer",
  "path": "content/service-providers/renderer/provider.py",
  "runner": "python",
  "allowed_tools": [
    {"name": "read"},
    {"package": "community.media-core", "version": "^1.2.0", "object": "tool:normalize_image"}
  ],
  "allowed_services": [
    {"package": "community.asset-store", "version": ">=1.0.0,<2.0.0", "object": "service:assets"}
  ]
}
```

The referenced packages and objects must already be installed in the target scope or inherited user scope before this object can be selected for install.

## Update and Uninstall

`/pfp update` requires the package to already be installed in the selected scope. By default it updates only objects recorded from the previous install. New objects from the package can be selected explicitly with `--include`. Objects that were previously installed but no longer exist in the new package are removed during update, unless they were locally modified and `--force` is not provided. If a resource was modified locally after install, update skips it unless `--force` is provided. Secret bindings recorded on updated runtime objects are preserved automatically; pass `--secret name=stored_key` again to override a binding during update. Updating to a version that would violate an installed dependent's exact package version constraint is blocked unless `--force` is provided. Uninstall uses the same local install registry, refuses to remove a package that another installed package depends on unless `--force` is provided, including dependencies created by package-qualified `allowed_tools` or `allowed_services` grants. Conversation-scoped packages can also block uninstall of a user-scoped package they resolve through the inherited user scope. Uninstall removes the package content store when no installed object remains, and keeps secrets.

## Runtime Availability

After installation PawFlow refreshes the relevant resource and service registries. ResourceStore objects are immediately visible to agents and slash commands. Config-only services are registered through `ServiceRegistry` and connect using the normal service lifecycle. Installed flow objects are written to the scoped flow repository and are visible through the Resources flow-template catalog on the next UI refresh, including the first cold catalog load after install.

PFP `flow_task`/`task_provider` proxies are also refreshed into `TaskFactory` immediately after install. On process startup, `register_all_tasks()` reloads installed package task proxies from package install records after builtin tasks are registered. Use `manage_package(action="reload_tasks")` or `/pfp reload-tasks` only after an explicit runtime reset where you need to rebuild proxies without restarting.

The relay runtime bridge uses deterministic JSON envelopes. PawFlow prepares package invocations as `pawflow.package.runtime.invoke.v1` after verifying the installed entrypoint and provenance hash. Each invocation carries a `context` object with `user_id`, `conversation_id`, and `scope` so the bridge can resolve the conversation default relay and broker host tools/services in the same scope as the caller. Media service invocations also carry `output_dir` when the service is called through the media adapter. The invocation package section carries the signed runtime metadata needed by the bridge: dependencies, `allowed_tools`, `allowed_services`, and provided capabilities. Runtime results use `pawflow.package.runtime.result.v1`; task results rebuild `FlowFile` objects from base64 content plus attributes, while large media service results should use artifact file descriptors. Package code must request host tool/service calls through `pawflow.package.runtime.host_call.v1`; PawFlow reconstructs and authorizes those calls with `PackageCapabilityBroker` before executing a host tool or service, so package-supplied `grant` fields are never trusted.

`runner: "python"` is the only executable runner for Python entrypoints. PawFlow deploys the signed package content and lightweight SDK to the conversation default relay, starts the entrypoint outside the server process, sends the `invoke.v1` envelope on stdin, brokers any `host_call.v1` lines through the server, and accepts exactly one final `result.v1` envelope. Debug output must go to stderr or into a structured `ok: false` result envelope.

Starter `.pfpdir` templates live under `docs/examples/pfp/` for a tool, service provider, flow task, flow bundle, and inter-PFP dependency pair. Replace the placeholder developer public key with `/pfp key-create` output before building them.

`service_provider` proxies implement the normal service lifecycle: `connect()`, `disconnect()`, `is_connected()`, `status()`, `get_operations()`, `get_model_info()`, and `invoke(operation, arguments)`. If the package declares an `operations` object or list, `invoke` accepts only those operation names and reports unsupported operations as `ServiceError`. Packages that need open-ended operation dispatch can omit `operations`.


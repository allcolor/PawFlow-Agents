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

`pfp.lock.json` records the SHA-256 hash of every package file. `signature.ed25519` signs the canonical manifest plus lock. Package builds exclude generated output under `dist`, `__pycache__`, and `graphify-out`, as well as Python bytecode, so local analysis caches cannot make a signed artifact differ from a clean-checkout build. Install requires a verified `.pfp`; unsigned `.pfpdir` directories are only for development inspection/build workflows. Inspect returns both per-object details and an aggregate `capabilities` summary so UI and CLI clients can show package size, content size, file count, runtime objects, brokered PawFlow tool/service grants, package dependencies, provided capabilities, and required secrets before install. When the package is already installed, inspect also returns `update_diff` with version movement and per-object add/update/remove/unchanged status for update review. The slash/action layer also adds a compact `display` review for text clients.

## Manifest

```json
{
  "format": "pawflow.package.v1",
  "package": "community.wavespeed",
  "version": "1.0.0",
  "category": "Media & AI",
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

Supported installable object types are `agent`, `agent_group`, `prompt`, `skill`, `theme`, `task_def`, `flow`, `service_definition`, `tool`, `service_provider`, `flow_task`, `task_provider`, `ui_extension`, `web_app`, `mcp_server`, `repository_type`, and `repository_resource`. `agent_group` is a validated bounded-deliberation definition stored under `content/agent_groups/<name>.json`; installation does not bind its member requirements to conversation instances or enable group execution. `task_def` is a PawFlow agent/task definition resource. `flow_task`/`task_provider` are processor types for flows: install registers a `TaskFactory` proxy so flows can parse, validate, and execute the new task type when a runtime runner is declared. PFP `tool` objects are installed as runtime proxies with provenance and declared capabilities. A PFP `service_provider` registers its declared `service_type` in the normal `ServiceFactory` catalogue with its own schema, rules, actions, operations, category, and runtime implementation. It does not create an instance: instances are ordinary `service_definition` resources or services created through the normal service UI/API, and multiple instances may use the same PFP type with different configuration. `ui_extension` objects ship JS/CSS assets that hook into the chat web UI via the versioned `ui.v1` slot/hook contract; assets are served by `servePfpExtensionAssets` at `/chat/ext/<package>/<short_sha256>/<file>` with per-file SHA-256 integrity verification, and the install plan rejects packages declaring an incompatible `version_compat`. `web_app` objects ship a standalone page (html/js/css) served at its own authenticated route instead of being injected into the chat page; see [PFP Developer Guide](PFP_DEVELOPER_GUIDE.md) for the manifest shape and trust model. `mcp_server` objects install directly as an `mcp` resource (the same resource type the Resources sidebar's MCP section manages) — no manual reconnection step after install; see "MCP Servers (mcp_server)" below. `repository_type` and `repository_resource` let a package add schema-validated repository features without adding feature-specific entries to PawFlow's built-in `ResourceStore`.

`dependencies` declares package-level dependencies. Object-level `requires` can also reference another package with `"package:community.pkg@1.0.0"` or `{"package": "community.pkg", "version": "1.0.0"}`. `allowed_tools` and `allowed_services` accept builtin names, such as `{"name": "read"}`, and package-qualified grants, such as `{"package": "community.media-core", "object": "tool:normalize_image"}` or `"community.media-core/tool:normalize_image"`. These grants are only for brokered calls back into PawFlow through `pfp.call_tool(...)` and `pfp.call_service(...)`; they do not gate normal relay-local filesystem or binary access by the package process. Package-qualified grants are treated as dependencies: the referenced package, and the referenced object when one is named, must already be installed in the target scope or in the user scope before the dependent object can be selected for install.

Dependency `version` accepts exact versions and simple ranges: `>=1.0.0,<2.0.0`, `^1.2.0`, `~1.2.3`, comparison operators (`>`, `>=`, `<`, `<=`, `==`, `!=`), or `*`. Install and runtime checks require the installed package to satisfy the constraint. Updating a package is blocked when an installed dependent would no longer satisfy its declared constraint, unless `force` is explicit.

### Workflow agent bundles

A package may ship a workflow agent only with the exact flow it binds. The
`agent` resource declares `runtime_defaults.kind: workflow` and an immutable
`flow_fqn`; the manifest contains a `flow` object with that same FQN, and the
agent object's `requires` names the flow object ID:

```json
{
  "objects": [
    {
      "id": "flow:wiki",
      "type": "flow",
      "name": "example.wiki:1.0.0",
      "fqn": "example.wiki:1.0.0",
      "path": "content/flows/wiki.json"
    },
    {
      "id": "agent:wiki",
      "type": "agent",
      "name": "wiki",
      "path": "content/agents/wiki.md",
      "requires": ["flow:wiki"]
    }
  ]
}
```

Inspection rejects a missing, non-exact, mismatched, or undeclared flow.
Selective installation also refuses the agent if its flow object was not
selected. Installed bindings resolve within the target conversation/user scope,
record the resolved scope and digest, and do not follow a later package update
until the user explicitly upgrades the conversation agent.

### Agent group resources

An `agent_group` manifest object points to
`content/agent_groups/<name>.json`. Inspection validates the versioned group
schema, distinct concrete member requirements, capped rounds/calls/parallelism,
private-context policy, tool policy, synthesis target, and positive budgets.
The installed resource remains inert until an operator enables both workflow
agents and agent groups and a user explicitly binds every member requirement to
a compatible LLM conversation instance. Bind and run-start snapshots pin exact
agent and service revisions; package updates never retarget an active binding.

### UI extension on-demand assets

In addition to auto-loaded `scripts` and `styles`, a `ui_extension` may declare
an `assets.files` array of inert package files. An entry is either a relative
path or `{"id": "logical-name", "path": "content/ui/model.glb"}`. The signed
install record stores kind, logical ID, path, SHA-256, and size; the chat boot
manifest supplies a hash-addressed URL. Extension code resolves it with
`pfp.asset("logical-name")` or `pfp.asset("content/ui/model.glb")`. Files are
never auto-executed. Reviewed executable AudioWorklet modules use
`assets.worklets` entries with required `id` and `.js` `path` fields.
They resolve through the same `pfp.asset()` API and are never auto-loaded;
extension code must pass the URL explicitly to
`audioContext.audioWorklet.addModule()`. The authenticated `/chat/ext/...`
route verifies the installed whitelist, enablement, path containment, and full
file hash during the initial streaming copy, and supports immutable caching
plus single byte ranges. Runtime hook subscriptions are limited to the hooks
declared in the signed UI-extension manifest. `assets.templates` entries
(`{"slot": "conversation_stage", "path": "content/ui/stage.html"}`) are inert
HTML fragments (≤ 64 KiB, reviewed like scripts) that the server renders into
the chat page slot before JS boot; they are hash-verified per request,
never evaluated as templates and never served as URLs. See the
[PFP Developer Guide](PFP_DEVELOPER_GUIDE.md#ui-extensions-uiv1) for allowed
formats, limits, slots, context snapshots, events, and teardown semantics.

## Extension-defined repositories

A `repository_type` object owns a lowercase dotted logical type and a
self-contained JSON Schema. A `repository_resource` object installs one JSON
document plus optional inert package assets into that type. These resources
live under the dedicated extension repository; they never add a value to the
closed built-in resource maps.

The type descriptor must explicitly declare its schema version, whether its
own runtime may mutate JSON resources, whether dependent packages may
contribute, and the inert asset extensions it accepts. External JSON Schema
references are refused. A contributing package must declare an explicit
package dependency on the owner. Inspection validates the document and all
asset paths, extensions, hashes, counts, and sizes before selection.

The owner package's Python runtime can use `pfp.repository.list/get/create/
update/delete`. Host-side authorization derives package identity and scope from
the signed invocation envelope. It does not accept caller-supplied user,
conversation, scope, schema, or owner values. Runtime-created resources are
JSON-only in this contract; authenticated binary upload and browser asset
delivery are separate contracts.

Repository `list`/`get` responses decorate packaged assets with a stable
`pfp-asset:<type>/<scope>/<name>/<id>` reference and an authenticated immutable
URL. The asset route resolves the exact scoped repository entry and verifies
the contributor package, declared path, and SHA-256 before serving, including
for resources installed by a dependent pack.

Runtime objects that only need selected public fields from a built-in scoped
repository can declare `permissions.resources.read` entries with a built-in
`type` and a unique non-empty `fields` list, then call
`pfp.resources.list(type)`. The host reads the invoking user's scope and returns
only the declared fields. The grant provides no `get`, mutation, cross-user,
secret, or extension-repository access.

Package resources participate in conflict, update-diff, local-modification,
dependency, and uninstall handling. A type descriptor is retained when a
user-created resource still uses it unless force is explicit. Forced removal
of the descriptor still does not delete unrelated user-created resources.
See the [PFP Developer Guide](PFP_DEVELOPER_GUIDE.md#extension-defined-repositories)
for manifest examples and exact rules.

## MCP Servers (mcp_server)

Before `mcp_server`, connecting a `.pfp` to an MCP server was a two-step affair: the package could ship everything else (agents, skills, flows) but the MCP connection itself had to be added by hand afterwards through the Resources sidebar or `manage_resource`. `mcp_server` objects close that gap: they install directly as an `mcp` resource, so the connection is live as soon as the package is installed and enabled.

```json
{
  "id": "mcp_server:justicelibre",
  "type": "mcp_server",
  "name": "justicelibre",
  "path": "content/mcp/justicelibre.json",
  "secrets": [{"name": "justicelibre_api_key", "env": "JUSTICELIBRE_API_KEY"}]
}
```

`content/mcp/justicelibre.json` holds the same fields the `mcp` resource type already supports:

```json
{
  "url": "https://justicelibre.org/mcp",
  "transport": "http",
  "auth": {"Authorization": "Bearer ${justicelibre_api_key}"}
}
```

An http server requires `url`; a stdio server (`"transport": "stdio"`) requires `command` and runs on a relay, exactly like a manually-configured MCP resource — `local: true` runs it on the relay host helper instead of inside the relay container. Install rejects an `mcp_server` object missing the field its declared transport needs. `auth`/`env`/`command` values may reference `${secret_name}` placeholders bound the same way as any other PFP secret (`--secret name=stored_key`); values are resolved at connection time, never written into the install record.

Because a stdio `command` runs an arbitrary relay-local executable and an http server can reach any URL, `mcp_server` objects are always shown at elevated risk in the install plan (`high` for stdio, `medium` for http-only, matching `tool`/`service_provider` and `service_definition` respectively) so the user sees exactly what they're granting before confirming. Installing the resource does not by itself make the server usable in a conversation: MCP servers remain opt-in — nothing is enabled until it is checked at conversation level or in an agent override, per [tool_catalog.md](tool_catalog.md#tool-and-mcp-availability).

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

The permanent **PFP Depot** section in the left sidebar is rendered after the other repositories. It lists the validated bundled catalog together with the current user's uploaded artifacts, grouped into collapsible categories. An artifact whose exact package id and version are already installed in the visible user or conversation scope is labelled **Installed** instead of offering the install action; a different version remains available for inspection and update. Linked identities are managed from the chain icon in the chat header rather than duplicated beneath the resource repositories. Package authors may set the optional top-level `category` string in `pfp.json`; older packages are categorized from their object types. Uploads first pass through the authenticated FileStore endpoint, then the server verifies the `.pfp` signature and lock before atomically adding the artifact to the user's repository depot. Depot references are opaque (`depot:<id>`) and resolve only for their owner. Bundled entries are read-only; uploaded entries can be inspected/installed or deleted. Deleting a depot artifact does not uninstall resources that were previously installed from it.

### Service templates

A `service_template` object installs preset values for the existing service
creation form. Its JSON file uses format `pawflow.service-template.v1` and
requires `service_type` plus a `config` object; optional catalog metadata
includes `title`, `description`, `category`, `tags`, and
`service_description`. Installing the package stores only the template. It
never creates, connects, or enables a service.

The Resources service creator offers a blank form or the installed template
catalog. Selecting a template preselects its service type and injects its config
into the normal form; submission still uses the canonical `service_install`
action.

An existing service can also seed the same creation form through **Copy** in its
right-click menu. PawFlow reads the visible service definition and prefills its
type, description, and configuration. The new service name is deliberately left
empty, and nothing is created until the user submits the normal form.

Runtime objects can declare required secrets with `secrets`, for example `[{"name": "api_key", "env": "PROVIDER_API_KEY", "required": true}]`. Install requires an explicit binding from package-local secret name to an existing PawFlow secret key via repeated `--secret name=stored_key` flags or `manage_package(..., secret_bindings={"name": "stored_key"})`. PawFlow stores only the binding in package runtime metadata. Secret values are resolved at invocation time and injected into the relay runner environment under the declared `env` name; they are not added to runtime envelopes or install records. For `mcp_server` objects, install recursively rewrites expressions such as `${api_key}` in the MCP definition to the bound stored-secret expression, for example `${provider_key}`; package updates preserve that binding.
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

## Bundled Package Catalog

A PawFlow release may ship optional signed packages under
`data/repository/packages/bundled/`. These packages are discoverable in the same
Packages search as remote-registry entries, but they are not installed or enabled
automatically. The directory contains the `.pfp` artifacts and an `index.json`
with format `pawflow.bundled-packages.v1`; each entry declares `package`,
`version`, `artifact`, `package_size`, and `sha256`, plus optional description,
developer key, tags, and object ids.

Bundled refs resolve directly to the local artifact and therefore require no
download confirmation. PawFlow still verifies the indexed size and SHA-256 before
inspection, then applies the normal Ed25519 signature, lock-file, risk review,
object selection, secret binding, and explicit install checks. Docker and the
standalone installer synchronize this directory as a managed release default so
new optional packages appear after upgrades without touching installed-package
records.

Official bundled artifacts are built by
`python scripts/build-bundled-pfps.py --build --key-env PAWFLOW_PFP_SIGNING_KEY`.
The private Ed25519 key belongs in the `PAWFLOW_PFP_SIGNING_KEY` GitHub Actions
secret and never in the repository. The release workflow reconstructs the
packages with that secret and compares them byte-for-byte with the committed
artifacts. If the secret has not been provisioned yet, the workflow still
checks the committed signatures, indexed sizes, SHA-256 values, developer keys,
and object lists, but emits a warning that reproducibility was not authenticated.
PFP archives use fixed ZIP metadata so identical sources and signing key produce
identical bytes. Their lock timestamp follows the standard `SOURCE_DATE_EPOCH`
environment variable and defaults to zero; release builders that set it must use
the same value for generation and verification.

Create the publisher key without exposing its private half by calling
`manage_package` with `action=key_create` and
`name=PAWFLOW_PFP_SIGNING_KEY`. PawFlow stores it in the current user's encrypted
secret store, returns only the public key, and refuses to overwrite that secret.
The unnamed `key_create` form still returns a development keypair and must not be
used for an official release identity.

## Security Model

- `.pfp` install requires a valid Ed25519 signature.
- Every archive path is normalized and rejected if it is absolute, escapes the package, or contains unsafe characters.
- Registry refs and direct URLs show package size before download and require explicit confirmation before fetching. Local inspect shows package size, uncompressed content size, and file count before install; there is no arbitrary PFP size cap. Users decide whether a package is acceptable before installing it.
- Installation writes only selected objects from the install plan. Agent definitions with default `assigned_skills` can be installed only when every referenced skill is either already visible in the target scope or selected in the same install operation. Those defaults are copied into each new conversation instance rather than remaining a mutable cross-conversation assignment.
- Workflow-agent definitions must bundle and depend on their exact flow object;
  partial install cannot leave an agent pointing at an absent or mutable flow.
- When at least one object is installed, the verified package payload is copied into a scoped local content store under the package repository. Runtime proxies reference that stable `content_dir` plus their signed entrypoint path; they never depend on the original `.pfp` file remaining on disk.
- Installed resources receive `installed_from` provenance with package id, version, object id, file hash, package hash, and developer public key.
- PFP runtime proxies validate their installed entrypoint before invocation: the file must still live under the scoped package content directory and its SHA-256 must match the signed install provenance. Dev-loaded `.pfpdir` packages still enforce path containment, but skip hash mismatch failures so source edits take effect immediately.
- Skills still pass the existing skill review pipeline. Review-required skills need an explicit `--force` after inspection; blocked skills cannot be installed.
- Official bundled artifacts skip the per-object static+LLM install review: when the `.pfp` being installed is byte-identical to its entry in the version-controlled bundled catalog (same package/version, SHA-256, and developer key as `data/repository/packages/bundled/index.json`, which CI rebuilds and compares byte-for-byte), the content is exactly what was already reviewed before release, and the install stamps `reviewer: bundled-catalog` provenance instead of re-reviewing. Any mismatch — different hash, different key, unknown package — falls through to the full review pipeline.
- Third-party code-bearing objects execute only through a declared runtime runner in a relay. Tool and service-provider invocations use the agent-specific default relay when present, otherwise the conversation default relay; flow task invocations use the task's explicit `relay` parameter. `relay` is per task and can be an expression over flow parameters, so a single flow can run different imported PFP tasks on different relays, for example `relay: "${relay_extract}"`, `relay: "${relay_resize}"`, and `relay: "${relay_publish}"`. Package code is not imported into the PawFlow server process and does not execute directly on the server.
- Package code can use relay-local filesystem paths and relay-local binaries directly. Package tools/services may only call PawFlow brokered tools/services through `pfp.call_tool(...)` or `pfp.call_service(...)` when those calls were declared in `allowed_tools`/`allowed_services` and accepted during install; package-qualified grants require the referenced package and object dependency to be installed.
- Required PFP secrets must be declared and explicitly bound during install. Runtime envelopes carry binding names only; secret values are resolved at invocation time and injected into the runner environment.
- `PackageCapabilityBroker` centralizes runtime authorization for future package execution. It authorizes builtin grants such as `{"name": "read"}` and package-qualified grants such as `{"package": "community.media-core", "object": "tool:normalize_image"}`, then verifies the referenced package and object are installed before allowing the call.
- Registry downloads verify the package SHA-256 when the registry provides one.
- Uninstall uses the local install registry and does not remove secrets. When uninstall removes a skill from a conversation scope, PawFlow also removes that skill from that conversation's agent instances and queues the normal skill-removal context message.

## Developer Checklist

1. Create a `.pfpdir` with `pfp.json` and package files under `content/`.
2. Use stable object ids in the form `type:name`; update/uninstall records use those ids.
3. Put code-bearing package entrypoints under the package content tree and declare `runner: "python"` explicitly.
4. Declare every host call in `allowed_tools` or `allowed_services`; runtime code cannot expand its grants after install.
5. Declare required secrets with package-local names and environment variable names. Do not put secret values in package files.
6. Build with `/pfp build ... --key-env ENV_NAME` so the private signing key stays outside chat and shell history.
7. Inspect the signed `.pfp`, verify capabilities and update diff, then install selected objects. If you export agents, include their assigned skills or let `/pfp export` add those skill objects automatically.

Python entrypoints can import the lightweight SDK with `from pawflow import pfp`. The SDK exposes `pfp.input()` plus cached `pfp.payload`, `pfp.package`, and `pfp.context`; `pfp.result(value)` and `pfp.error(message)` emit `result.v1`; `pfp.call_tool(name, **arguments)` and `pfp.call_service(name, operation, **arguments)` emit brokered `host_call.v1` requests; `pfp.flowfile(content, attributes)` builds task result descriptors for `pfp.result(flowfiles=[...])`; and `pfp.artifact(kind, path, content_type, filename)` builds file artifact descriptors for large media results.

PFP flow tasks receive input FlowFile bytes through a relay-local `payload["flowfile"]["content_path"]` instead of inline base64. The path is relative to the package working directory. Task results may still use `pfp.flowfile(...)`; PawFlow stages those result bytes as relay files and copies them back through the relay chunk API before rebuilding server-side FlowFiles. Large FlowFiles must not be transported as JSON/base64 payloads.

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

The package runtime resolves the relative path, rejects escapes outside `output_dir`, records size and SHA-256 metadata, and returns an `image_path`, `video_path`, or `audio_path` to PawFlow media handlers. FileStore destinations copy that file in chunks, avoiding JSON/base64 expansion and avoiding an extra full-size media buffer in memory. When the package process writes the artifact inside the relay, PawFlow copies the relay file to the server-side temporary artifact path through the relay chunk API; it must not use `read_file()` to materialize the whole media file as server bytes.

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

`/pfp update` requires the package to already be installed in the selected scope. By default it updates only objects recorded from the previous install. New objects from the package can be selected explicitly with `--include`. Objects that were previously installed but no longer exist in the new package are removed during update, unless they were locally modified and `--force` is not provided. If a resource was modified locally after install, update skips it unless `--force` is provided. Skill updates notify currently assigned conversation agents to call `load_skill` again when the changed instructions are relevant. Secret bindings recorded on updated runtime objects are preserved automatically; pass `--secret name=stored_key` again to override a binding during update. Updating to a version that would violate an installed dependent's exact package version constraint is blocked unless `--force` is provided. Uninstall uses the same local install registry, refuses to remove a package that another installed package depends on unless `--force` is provided, including dependencies created by package-qualified `allowed_tools` or `allowed_services` grants. Conversation-scoped packages can also block uninstall of a user-scoped package they resolve through the inherited user scope. Uninstall removes the package content store when no installed object remains, removes uninstalled skills from visible agents' `assigned_skills`, and keeps secrets.

## Runtime Availability

After installation PawFlow refreshes the relevant resource and service registries. ResourceStore objects are immediately visible to agents and slash commands. Config-only `service_definition` objects are registered through `ServiceRegistry` and connect using the normal service lifecycle. A `service_provider` instead registers its declared `service_type` in `ServiceFactory` for the package's user or conversation scope; it creates no implicit instance. Users create one or more ordinary `ServiceRegistry` instances of that type, with independent ids and configuration, exactly as for a core service. Installed flow objects are written to the scoped flow repository and are visible through the Resources flow-template catalog on the next UI refresh, including the first cold catalog load after install.

PFP `flow_task`/`task_provider` proxies are also refreshed into `TaskFactory` immediately after install. On process startup, `register_all_tasks()` reloads installed package task proxies from package install records after builtin tasks are registered. `TaskFactory` remains a global parser/catalog registry, so the proxy resolves the installed runtime by `task_type`, `user_id`, and `conversation_id` at execution time. Conversation scope wins over user scope; ambiguous duplicate `task_type` records in the same scope are rejected. Use `manage_package(action="reload_tasks")` or `/pfp reload-tasks` only after an explicit runtime reset where you need to rebuild proxies without restarting.

Running a PFP flow task requires an explicit `relay` parameter on the task and a deployment runtime context from the executor. Real flow starts pass the deployment owner and conversation id into `ContinuousFlowExecutor`, which injects `_user_id`, `_conversation_id`, and `_scope` into package proxies before execution. A package flow task invoked without that runtime context fails instead of guessing a user, conversation, or relay. The `relay` value is per task, so a flow can define several relay parameters and point each imported task at the execution target it needs. For server-side protected execution, provision the conversation's `srv_min_*` server execution relay and pass that id through a normal flow parameter such as `relay_secure`.

The relay runtime bridge uses deterministic JSON envelopes. PawFlow prepares package invocations as `pawflow.package.runtime.invoke.v1` after verifying the installed entrypoint and provenance hash. Tool and service-provider invocations carry a `context` object with `user_id`, `conversation_id`, `scope`, and the current agent name so the bridge can resolve the agent or conversation default relay and broker host tools/services in the same scope as the caller. PFP flow task proxies instead require an explicit `relay` task parameter; flows imported from a conversation are prefilled with that conversation's default relay when one is available. Media service invocations also carry `output_dir` when the service is called through the media adapter. The invocation package section carries the signed runtime metadata needed by the bridge: dependencies, `allowed_tools`, `allowed_services`, and provided capabilities. Runtime results use `pawflow.package.runtime.result.v1`; task results rebuild `FlowFile` objects from relay-local `content_path` files plus attributes. Package code must request host tool/service calls through `pawflow.package.runtime.host_call.v1`; PawFlow reconstructs and authorizes those calls with `PackageCapabilityBroker` before executing a host tool or service, so package-supplied `grant` fields are never trusted.

`runner: "python"` is the only executable runner for Python entrypoints. PawFlow deploys the signed package content and lightweight SDK to the selected relay, starts the entrypoint outside the server process, sends the `invoke.v1` envelope on stdin, brokers any `host_call.v1` lines through the server, and accepts exactly one final `result.v1` envelope. Debug output must go to stderr or into a structured `ok: false` result envelope.

Starter `.pfpdir` templates live under `docs/examples/pfp/` for a tool, service provider, flow task, flow bundle, and inter-PFP dependency pair. Replace the placeholder developer public key with `/pfp key-create` output before building them.

`service_provider` proxies implement the normal service lifecycle: `connect()`, `disconnect()`, `is_connected()`, `status()`, `get_operations()`, `get_model_info()`, and `invoke(operation, arguments)`. The manifest must declare a non-empty `operations` object or list; `invoke` accepts only those operation names and reports unsupported operations as `ServiceError`. A package cannot be uninstalled normally while service instances still use one of its provided types. Remove or migrate those instances first; forced uninstall preserves their definitions as orphaned instances.


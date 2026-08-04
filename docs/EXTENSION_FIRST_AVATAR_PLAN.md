# Extension-first avatar, voice, and semantic UI implementation plan

Status: accepted implementation direction for the 1.0 line  
Scope: reusable PFP platform contracts plus installable avatar packages  
Non-goal: adding an `avatar` resource, renderer, repository screen, or semantic-agent feature directly to PawFlow core

## 1. Outcome

PawFlow will provide only the stable contracts that an extension cannot safely provide for itself: signed package lifecycle, scoped namespaced storage, integrity-checked package assets, permission-brokered host calls, browser extension slots/events, realtime media taps, and a browser action bridge.

Everything visible as an avatar feature will be delivered through `.pfp` packages:

- `pawflow.avatar-runtime.pfp`: avatar repository UI, renderer integration, realtime animation, avatar/voice bindings, and semantic UI contributions;
- `pawflow.avatar-pack.*.pfp`: independently installable avatar definitions and model assets;
- optional renderer or host adapters as separate packages when they have different dependencies or trust requirements;
- optional semantic-agent tools as package runtime objects, not built-in tools.

Installing no avatar package must leave PawFlow with no avatar repository screen, no avatar renderer code, no renderer dependency, no model assets, and no avatar-specific background work.

## 2. Architectural boundary

### Core may contain

- generic PFP object contracts (`repository_type`, `repository_resource`, and package assets);
- strict namespace, scope, schema, ownership, and lifecycle enforcement;
- generic PFP runtime storage host calls authorized from the signed install record;
- integrity-checked serving of assets declared by installed packages;
- additive UI slots and generic browser event/media/action APIs;
- a transport that lets an authorized PFP tool query or invoke semantic nodes in an active browser session;
- audit records, limits, kill switches, and install/update/uninstall cleanup.

### Core must not contain

- an `avatar` entry in `ResourceStore.VALID_TYPES` or `paths.REPO_TYPES`;
- TalkingHead, HeadAudio, MotionEngine, three-vrm, model files, phoneme maps, avatar screens, or avatar CSS/JavaScript;
- avatar-specific API routes, handlers, service types, tools, SSE event names, or database tables;
- assumptions about VRM, GLB, FBX, 2D/3D rendering, a particular lip-sync engine, or a particular TTS provider;
- a built-in semantic UI tool or a hard-coded inventory of avatar UI nodes.

### Meaning of host support

Extension-first does not mean browser-only and does not remove host support. A package may ship Python runtime objects and execute them through the existing relay sandbox, or call explicitly granted host tools/services. The core work is the generic broker and lifecycle contract; renderer-specific host code remains inside an installable package. A browser renderer is the initial default because it can consume the browser's realtime audio without server-side video rendering, but a future host renderer can implement the same package capabilities.

## 3. Package topology

### 3.1 `pawflow.avatar-runtime.pfp`

Provides:

- capability `repository.type:pawflow.avatar.v1`;
- capability `avatar.renderer:talkinghead.v1`;
- capability `avatar.voice-binding:v1`;
- capability `semantic.contributor:pawflow.avatar.v1`;
- a `ui_extension` contributing the avatar stage and repository controls;
- one or more package runtime handlers for validation, import, repository mutations, and voice lookup;
- an optional PFP tool that exposes avatar-related semantic actions to agents;
- vendored, pinned MIT renderer libraries and their license notices.

### 3.2 Avatar packs

Each pack depends on `pawflow.avatar-runtime` and contains only declarative `repository_resource` objects plus their immutable assets. Packs can be installed, updated, disabled, or uninstalled independently. Removing a pack removes only resources still matching their installed hashes; locally modified or user-created resources follow the normal force/conflict rules.

### 3.3 Optional adapters

Adapters are separate packages when they add a substantial dependency or permission surface, for example:

- a host/relay renderer;
- a MuseTalk-style video renderer;
- a provider-specific realtime voice bridge;
- extra motion libraries or model codecs.

The base avatar package depends only on generic PawFlow contracts and its vendored browser runtime.

## 4. Generic PFP repository contracts

### 4.1 `repository_type` object

A package declares a logical repository type before it or dependent packages contribute resources:

```json
{
  "id": "repository_type:avatar",
  "type": "repository_type",
  "name": "avatar",
  "resource_type": "pawflow.avatar",
  "schema_version": "1",
  "schema": "content/repository/avatar.schema.json",
  "title_key": "avatar.repository.title",
  "contributions": "dependencies",
  "mutable": true,
  "asset_extensions": [".vrm", ".glb", ".gltf", ".bin", ".png", ".jpg", ".webp"]
}
```

Rules:

- `resource_type` is a lowercase dotted identifier and is globally owned by one installed package in a scope;
- it cannot shadow any built-in repository type;
- `schema_version` is required and has no implicit default;
- the schema is JSON Schema, stored and hash-checked with the package;
- `contributions: dependencies` permits only packages with an explicit dependency on the owner package to install resources of that type;
- a runtime handler may mutate the type only when its own package owns it and the descriptor declares `mutable: true`;
- user and conversation scopes remain distinct; there is no silent fallback to global or another user.

### 4.2 `repository_resource` object

```json
{
  "id": "repository_resource:luna",
  "type": "repository_resource",
  "name": "luna",
  "resource_type": "pawflow.avatar",
  "schema_version": "1",
  "path": "content/avatars/luna.json",
  "assets": [
    {"id": "model", "path": "content/avatars/luna.vrm"},
    {"id": "preview", "path": "content/avatars/luna.webp"}
  ]
}
```

Install behavior:

- validate the JSON document against the installed owner schema;
- validate asset IDs, extensions, paths, file hashes, counts, and total size;
- record the contributing package, owner package, schema version, object hash, and immutable asset descriptors;
- persist metadata in a dedicated extension repository, never in `ResourceStore` or the built-in repository directories;
- reject undeclared types, missing owner dependencies, schema mismatches, collisions, and unsafe paths;
- expose stable `pfp-asset:` references in stored documents and resolve them only through the authenticated asset route.

Update/uninstall behavior:

- include document and asset hashes in update diff and local-modification detection;
- install new content before switching the install record;
- remove obsolete resources during update using the same force rules as other PFP objects;
- remove a type descriptor only when no installed dependent package or retained resource still uses it;
- remove package assets only after no retained install record references the content directory;
- never silently delete user-created resources.

### 4.3 Mutable extension storage

PFP runtime code receives package-scoped methods in the SDK:

```python
pfp.repository.list("pawflow.avatar")
pfp.repository.get("pawflow.avatar", "luna")
pfp.repository.create("pawflow.avatar", "custom-luna", document, assets=[])
pfp.repository.update("pawflow.avatar", "custom-luna", document)
pfp.repository.delete("pawflow.avatar", "custom-luna")
```

These calls use a new host-call kind rather than filesystem access. The host derives package identity, user, conversation, and scope from the signed invocation envelope; callers cannot override them. The broker checks ownership, `mutable`, schema version, object size, and requested operation. Results are JSON-only and audit logged. Binary imports use a bounded authenticated upload/token flow, not base64 in `pfp.call`.

## 5. Generic package asset contract

UI extensions need large non-executable assets without pretending they are scripts or styles.

The `ui_extension.assets` object gains a `files` array. Files are listed in the boot manifest but are never auto-executed. `pfp.asset(pathOrId)` resolves an installed, hash-addressed URL owned by the calling package.

The server:

- serves only install-record-whitelisted files;
- recomputes SHA-256 before serving;
- enforces package/content-directory containment and rejects symlinks escaping the package;
- emits explicit MIME types for models, audio, WebAssembly, compressed textures, and binary buffers;
- keeps `X-Content-Type-Options: nosniff`;
- supports immutable cache keys and byte ranges for large model/audio files;
- applies separate per-file, per-object, and package limits;
- never permits `.html` in same-origin injected extension assets.

Executable scripts/styles keep the current security review. Data files remain inert unless extension code explicitly fetches them.

## 6. Generic UI extension additions

### 6.1 Slots

Add additive `ui.v1` slots:

- `conversation_stage`: renderer surface associated with the current conversation;
- `resources_collection`: optional collection-specific repository panel contribution;
- `composer_accessory`: compact controls adjacent to the message composer.

The avatar package chooses which slots to declare. Core renders empty slot containers only when at least one enabled installed extension contributes to them, so an installation without such a package has no visible empty avatar UI.

### 6.2 Context and lifecycle

The package API gains read-only context snapshots and additive hooks:

- active user, conversation, agent, locale, theme, and permission mode;
- `resource_changed` with a logical type and operation;
- `realtime_state_changed`;
- `media_track_subscribed`, `media_track_unsubscribed`, and `media_audio_frame` where supported;
- existing `sse_event` remains the forward-compatible escape hatch.

Every subscription returns an unsubscribe function and is removed on extension shutdown. Conversation changes detach media and renderer state before a new context is delivered.

### 6.3 Realtime media tap

The chat client publishes a generic media source descriptor to enabled extensions:

- LiveKit remote agent audio track and attached media element;
- legacy PCM playback frames with sample rate/channel metadata;
- speaking/listening/idle state derived from existing realtime events.

The API does not mention avatars or lip-sync. It provides read-only media observations; extensions cannot replace microphone permissions or hijack the core audio sink. The avatar runtime feeds those observations to HeadAudio/MotionEngine and uses transcript/SSE timing only as supplemental signals.

## 7. Semantic UI contract

### 7.1 Browser registry

Each extension may register semantic nodes under its package namespace:

```javascript
pfp.semantic.register({
  id: 'stage.avatar',
  role: 'figure',
  label: 'Current avatar',
  parent: 'conversation.stage',
  state: () => ({ avatar: currentAvatar, speaking: speaking }),
  actions: {
    select: { parameters: { name: { type: 'string', required: true } }, run: selectAvatar },
    showRepository: { parameters: {}, run: openRepository }
  }
});
```

Rules:

- IDs are automatically qualified with the package ID;
- state must be JSON-serializable, bounded, and free of DOM nodes/functions;
- action schemas are validated before registration and arguments before invocation;
- nodes disappear on package disable, shutdown, navigation teardown, or tab disconnect;
- one package cannot mutate another package's nodes;
- sensitive values and raw authentication/session data are forbidden.

### 7.2 Agent-to-browser bridge

The core provides only an authenticated correlation transport:

1. an installed PFP tool requests `semantic.list`, `semantic.get`, or `semantic.invoke` for a conversation;
2. the server selects an active authorized browser session for the same user/conversation;
3. a request with a random correlation ID is delivered over the existing realtime channel;
4. the browser validates the package grant and action schema, invokes the node action, and returns a bounded JSON result;
5. the server audits the request and result and returns a clear unavailable/timeout error when no eligible tab answers.

There is no built-in semantic-agent tool. `pawflow.avatar-runtime.pfp` (or a separate semantic package) supplies the tool definition and declares the browser capability it needs. Invocation of nodes owned by another package requires an explicit installed capability grant.

## 8. Avatar repository document

The initial `pawflow.avatar` schema contains:

```json
{
  "format": "pawflow.avatar.v1",
  "title": "Luna",
  "description": "",
  "renderer": "talkinghead",
  "model": {"asset": "model", "format": "vrm"},
  "preview": {"asset": "preview"},
  "voice": {"ref": "voice:luna-en"},
  "animation": {
    "idle": "natural",
    "lip_sync": "audio",
    "motion_profile": "default"
  },
  "metadata": {"author": "", "license": "", "source": ""}
}
```

Required fields have no defaults unless the schema explicitly defines them. Renderer-specific options live under a renderer-namespaced object so alternative renderers do not pollute the common schema.

The repository screen delivered by the package supports list/search, preview, import, select per conversation/agent, edit metadata/binding, export as `.pfp`, and delete for mutable resources. It clearly labels packaged versus user-created resources and shows the owning/contributing package.

## 9. Voice repository integration

Avatar documents store logical voice references such as `voice:luna-en`; they never embed provider-native voice IDs, cloning samples, access tokens, or decrypted secrets.

The avatar extension obtains visible voice entries through a generic, scope-authorized resource query. Resolution uses the existing voice repository and `SpeakHandler` alias semantics. Selection is stored per avatar and may be overridden per conversation/agent by extension state.

Supported path:

- text-first/realtime orchestration produces text;
- the configured TTS service resolves the PawFlow voice alias;
- generated audio is played through the existing audio/LiveKit path;
- the generic media tap drives lip sync and motion.

Native speech-to-speech providers can use a repository voice only when their provider adapter explicitly supports that alias or accepts synthesized replacement audio. The UI must report this compatibility honestly; it must not silently substitute another voice.

Voice create/update/delete events invalidate avatar selectors. A missing binding is a visible validation error with a rebind action, never an automatic fallback voice.

## 10. Renderer packaging and licenses

Initial renderer stack:

- TalkingHead for Three.js-based avatar rendering;
- HeadAudio for audio-driven lip synchronization;
- MotionEngine for conversational motion.

All are vendored at pinned commits inside the `.pfp`, with upstream LICENSE files and a package `THIRD_PARTY_NOTICES.md`. The build is reproducible and does not fetch CDN/npm content at runtime. Source maps and development-only artifacts are excluded from the release package.

The package validates WebGL/WebGPU and required browser APIs at boot. Unsupported clients show a package-owned diagnostic and keep normal chat operational. Renderer crashes are contained to the extension surface and cleaned up on shutdown/conversation switch.

## 11. Delivery phases

### Phase 0 — baseline safety (complete)

- verify the pre-existing worktree;
- fix Bandit B112 without `#nosec`;
- run focused PFP/UI/apply-patch tests;
- commit the two coherent pre-existing lots separately;
- confirm a clean worktree before architecture changes.

### Phase 1 — extension repository foundation

- implement `repository_type` and `repository_resource` validation, inspection, risk/capability display, install, update diff, conflict detection, local-modification detection, uninstall, and dependency blocking;
- add the namespaced scoped extension repository and JSON Schema validation;
- add package runtime repository host calls and SDK facade;
- document manifest examples and security rules;
- add unit, lifecycle, traversal, scope-isolation, dependency, and schema tests.

Exit gate: a signed test package can define a new logical repository type and a dependent signed pack can install/list/update/uninstall resources without adding that type anywhere in core lists.

### Phase 2 — inert assets and browser API (complete)

- add `ui_extension.assets.files`, `pfp.asset()`, MIME mappings, byte ranges, size limits, and boot-manifest URLs;
- add the new conditional slots and extension context/lifecycle APIs;
- add generic resource-change events;
- document and test integrity, tampering, caching, traversal, kill switch, disable, and cleanup.

Exit gate: a test extension loads a large inert model asset by logical ID while undeclared/tampered/disabled assets remain inaccessible.

### Phase 3 — realtime media contract (complete)

- emit generic LiveKit and PCM media lifecycle events;
- expose read-only track/element/frame descriptors;
- guarantee detach/cleanup on stop, reconnect, conversation switch, disable, and shutdown;
- test both LiveKit and legacy PCM paths without an avatar dependency.

Exit gate: a minimal test extension receives agent audio activity and frames/tracks, then stops receiving them immediately when disabled.

### Phase 4 — semantic UI transport

- implement the browser registry, schema validation, qualified IDs, snapshots, and local invocation;
- implement authenticated server correlation and active-tab selection;
- add a generic PFP runtime browser host call gated by signed permissions;
- add audit records, result limits, disconnect, ambiguity, and timeout behavior;
- provide a sample installable semantic tool package.

Exit gate: an installed PFP tool can list and invoke a test extension node in the correct conversation, while cross-user, cross-conversation, undeclared-package, disabled-extension, and stale-tab attempts fail.

### Phase 5 — avatar runtime package

- create the avatar schema and repository UI in `pawflow.avatar-runtime.pfp`;
- vendor and pin TalkingHead, HeadAudio, and MotionEngine with licenses;
- implement stage lifecycle, model loading, preview, selection, error isolation, and GPU cleanup;
- bind transcript/realtime state and generic media input to lip sync/motion;
- expose semantic nodes/actions;
- add a small redistributable test avatar or synthetic fixture with explicit license.

Exit gate: installing the package adds the entire feature; selecting an avatar animates it in text/TTS realtime; disabling or uninstalling removes the feature and releases media/GPU resources.

### Phase 6 — voice bindings and avatar packs

- add repository voice selection, compatibility checks, missing-binding UI, and change invalidation;
- publish at least one independent avatar pack package;
- validate install order, dependency errors, pack updates, pack uninstall, and export/re-import;
- document creation of third-party renderer and avatar-pack packages.

Exit gate: a voice alias used by `SpeakHandler` also drives the selected avatar, and avatar packs require no PawFlow core change.

### Phase 7 — release hardening

- full tests, Bandit, package static review, tamper tests, browser tests, and performance profiling;
- cold-start test with no optional package installed;
- package size/memory/GPU budgets and degraded-browser behavior;
- backup/restore and upgrade/uninstall recovery tests;
- update public PFP developer, package, security, and 1.0 migration documentation.

## 12. Test matrix

Every phase includes positive and negative tests for:

- user versus conversation scope and conversation ownership;
- two packages declaring the same repository type;
- contributor with and without an explicit dependency;
- valid, invalid, missing, and changed schemas;
- path traversal, symlink escape, undeclared extension, MIME confusion, tampered hashes, and oversized files;
- install, partial selection, same-version reinstall, upgrade, downgrade, conflict, force, local edit, obsolete-object removal, and uninstall;
- disabled extension, global kill switch, missing active browser, two active tabs, reconnect, timeout, and stale correlation IDs;
- LiveKit track replacement, PCM playback, audio stop, conversation switch, and renderer teardown;
- voice alias present, removed, incompatible, and rebound;
- absence: no avatar package means no avatar code is bootstrapped and no avatar UI is rendered.

## 13. Performance and limits

Initial limits are explicit constants covered by tests and surfaced during PFP inspection:

- repository schema and document JSON sizes;
- resources and assets per object;
- individual asset and aggregate object/package size;
- semantic nodes/actions/state/result sizes;
- pending browser requests per conversation and request lifetime;
- decoded PCM queue duration and renderer frame budget.

Large assets are streamed or range-served, cached by immutable hash, and loaded lazily only after the user enables/selects the extension. No avatar library or model participates in a core cold start.

## 14. Documentation deliverables

- extend `PFP_PACKAGES.md` with the new object contracts and lifecycle semantics;
- extend `PFP_DEVELOPER_GUIDE.md` with repository, assets, media, semantic APIs, permissions, examples, and packaging recipes;
- add an avatar package README and authoring guide;
- record every vendored dependency and license;
- explain voice alias compatibility and native speech-to-speech limitations;
- document that 1.0 additions should prefer `.pfp` implementations and justify any future core contract addition as feature-neutral infrastructure.

## 15. Definition of done

The work is complete only when:

1. a clean PawFlow installation contains generic contracts but no avatar implementation or renderer dependency;
2. the full avatar feature appears after installing signed `.pfp` artifacts through the normal inspect/install flow;
3. avatar resources and packs are managed by the generic extension repository and survive supported restart/backup flows;
4. PawFlow voice aliases are selectable and actually used by the TTS path that feeds avatar animation;
5. semantic avatar state/actions are supplied by the package and callable only through the authorized browser bridge;
6. browser and host/relay renderer implementations can coexist behind package capabilities;
7. disable and uninstall remove UI/runtime behavior immediately and clean package-owned data according to explicit retention/force rules;
8. focused and full tests, Bandit, package review, and documentation checks pass;
9. all implementation work is split into reviewable commits with a clean final worktree.

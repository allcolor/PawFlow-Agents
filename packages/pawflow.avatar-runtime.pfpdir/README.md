# PawFlow avatar runtime package

This package adds the PawFlow avatar repository and browser runtime without
putting an avatar implementation in PawFlow core. Install it at user scope to
make the avatar controls available from every conversation. Removing or
disabling the package removes its UI, semantic nodes, media listeners, and GPU
renderer.

The initial package includes:

- a schema-owned `pawflow.avatar` extension repository;
- a procedural MIT test avatar with no external model;
- lazy TalkingHead rendering for installed GLB, GLTF, or VRM resources;
- audio-driven lip sync through HeadAudio for LiveKit and legacy PCM media;
- MotionEngine gestures;
- package-owned semantic browser actions and an optional PFP tool.

No renderer or model is loaded until the user opens the avatar stage. Avatar
selection is stored in the browser per user, conversation, and selected agent.
Packaged model assets remain integrity checked and are fetched through their
authenticated repository URLs.

## Voice bindings

Each avatar may declare a default `voice:<alias>` reference. The repository UI
lists visible PawFlow voice aliases and stores an override per user,
conversation, selected agent, and avatar. The alias used by `SpeakHandler`
therefore drives the same outgoing audio observed by the renderer. Voice
resource changes refresh bindings immediately; missing aliases and native
speech transports reporting a different alias are shown as degraded states,
with no anonymous or default fallback.

## Avatar packs

A model pack depends explicitly on `pawflow.avatar-runtime` and contributes
one or more `repository_resource` objects for `pawflow.avatar`. Each
TalkingHead document names its model and optional preview by logical asset ID.
The common document shape is defined in
`content/repository/avatar.schema.json`.

`packages/pawflow.avatar-pack.starter.pfpdir` is the complete independent
example. It contains only a repository resource, an original MIT-licensed GLB,
and an explicit dependency on this runtime. Its deterministic builder is
`scripts/build-starter-avatar.py`.

The package deliberately does not redistribute the upstream TalkingHead demo
avatars because their model-specific provenance and redistribution terms are
not stated clearly enough. Avatar packs must include explicit author, license,
and source metadata for every model they distribute.

## Browser requirements

The synthetic fixture works in any modern browser. Three-dimensional rendering
requires WebGL, AudioWorklet, and the browser APIs needed by TalkingHead.
Unsupported clients show a package-owned diagnostic and leave normal chat
operation untouched.

## Rebuilding the browser vendor bundle

From the PawFlow repository root, run:

```bash
scripts/build-avatar-vendor.sh \
  packages/pawflow.avatar-runtime.pfpdir/content/ui
```

The recipe clones exact upstream commits, applies the two recorded PawFlow
adapter patches, uses esbuild 0.25.9 and Three.js 0.180.0, and refuses output
whose hashes differ from the reviewed release artifacts. FaceMirror and its
MediaPipe dependency are intentionally excluded; they belong in an optional
adapter package.

Alternative renderers are separate executable PFP packages with their own
reviewed UI/runtime assets and lifecycle. The v1 avatar schema accepts only
`talkinghead` and `synthetic`; other document shapes require a namespaced
repository type or a reviewed versioned owner contract.

# Media Studio Workflow Agent Implementation Plan

Status: implementation complete; validation and authorized hotpatch pending
Date: 2026-08-25
Owner: PawFlow core and first-party media packages

Implemented delivery:

- immutable intent, brief, proposal, capability, ComfyUI revision,
  provisioning, project/revision, and FFmpeg recipe contracts;
- scoped append-only MediaProjectStore and deterministic service discovery,
  filtering, scoring, alternatives, and stable rejection reasons;
- exact-service image/video/audio/speech/clone execution plus safe shell-free
  FFmpeg composition with durable provider-job replay boundaries;
- durable grouped questions, scenario/voice approval, capability choice,
  revision lineage, artifact QA, and typed terminal formatting;
- the installable `pawflow.media-studio:1.0.0` package and exact
  `pawflow.agents.media-studio:1.0.0` flow/agent resources with nine colored
  English functional frames;
- generalized immutable ComfyUI image/video/audio preset revisions and the
  `pawflow.comfyui-operator:1.2.0` provisioning-validation contract.

Activation remains governed by the existing Workflow Agent rollout gate. This
delivery does not introduce a separate Media Studio feature flag.

## 1. Outcome

Deliver one user-facing Media Studio Workflow Agent that understands multimedia requests, rejects unrelated work, discovers the actual PawFlow and ComfyUI capabilities available to the conversation, asks durable questions when material information is missing, proposes a creative scenario before composite or expensive production, generates or edits image, video, audio and speech, supports authorized voice cloning, performs deterministic post-production through FFmpeg, preserves every revision and artifact, and can safely evolve reviewed ComfyUI workflows, models, LoRAs and custom nodes.

The agent is ComfyUI-first, not ComfyUI-only. It selects the best installed and configured media capability. ComfyUI presets, built-in PawFlow media services, PocketTTS or another voice provider, third-party audio services, and FFmpeg are peers behind one capability catalog.

## 2. Architectural decisions

1. Media Studio is one visible conversation agent with runtime kind agent_workflow.
2. FlowDefinition remains the only executable workflow format.
3. Creative briefs, storyboards and production proposals are versioned input artifacts, never an alternative executable plan format.
4. Every agent turn is an isolated WorkflowRun pinned to an immutable flow version and service snapshot.
5. Cross-turn creative continuity is stored in MediaProjectStore, not in model memory.
6. Media bytes live only in FileStore or an explicitly selected relay filesystem. MediaProjectStore stores references and lineage, not duplicate bytes.
7. Generation is idempotent at the PawFlow run boundary. Every provider submission has a stable key and a durable provider job reference before waiting.
8. Existing installed and enabled services are preferred over provisioning new software when they satisfy the brief.
9. ComfyUI workflows are reviewed API-format presets. The agent may create a new revision but never overwrite an active preset in place.
10. Host mutation, model or LoRA download, custom-node installation and ComfyUI restart require a durable approved provisioning proposal.
11. Voice cloning requires explicit durable confirmation that the user is authorized to use the reference voice.
12. FFmpeg receives a closed typed recipe. The model never supplies a raw shell command or unrestricted filter expression.
13. Every output is append-only. A modification creates a child MediaRevision and never destroys the parent.
14. Every flow ships with an English functional presentation: named layout, colored frames, labels and descriptions.
15. No commit, push, live flag activation or hotpatch is part of this implementation unless separately requested.

## 3. Product model

### 3.1 Visible agent

Resource name: media-studio
Display name: Media Studio
Flow FQN: pawflow.agents.media-studio:1.0.0
Input port: media_request
Terminal port: media_terminal
Preempt policies: checkpoint and queue

The agent accepts:

- image generation and editing;
- video generation, animation, extension and editing;
- audio generation, music and sound effects;
- speech synthesis and authorized voice cloning;
- subtitles, transcoding and FFmpeg composition;
- multi-shot or multi-engine productions;
- modification of an earlier project revision;
- ComfyUI workflow, model, LoRA and custom-node evolution required by an accepted production.

It rejects unrelated work before media services, files or project sources are accessed.

### 3.2 Internal authority boundary

The visible agent performs discovery, creative design, generation and composition with the exact effects declared by its flow.

A privileged internal Process Group owns provisioning actions. It may execute only after a typed approval containing the exact sources, revisions, checksums, licenses, destinations, changes, expected restart and rollback notes. The user still interacts with one visible agent.

### 3.3 Media project

A project is conversation-scoped and may later be promoted explicitly. It contains:

- project UUID, title, user and conversation;
- state revision for optimistic concurrency;
- current revision ID;
- append-only revisions;
- named asset references;
- created and updated UTC timestamps.

A revision contains:

- UUID and UTC timestamp;
- parent revision ID;
- root turn and WorkflowRun IDs;
- original request;
- normalized intent;
- CreativeBrief;
- approved MediaProductionProposal when required;
- exact engine, service, preset, model and seed;
- reference roles and FileStore IDs;
- provider job IDs;
- FFmpeg recipe when used;
- output artifacts;
- QA report;
- status and supersession reason.

## 4. Versioned contracts

### 4.1 MediaIntent

Required fields:

- schema_version;
- kind: unsupported, image, video, audio, speech, voice_clone, compose or composite;
- operation;
- confidence;
- explanation;
- requires_references;
- requires_scenario;
- missing_fields;
- requested_project_id and revision selector.

### 4.2 MediaReference

Required fields:

- role;
- file_id;
- filename;
- content_type;
- source_message_id;
- optional selected revision ID.

Allowed roles include subject_reference, style_reference, composition_reference, source_image, start_frame, end_frame, source_video, source_audio, voice_reference, music_bed, sound_effect and subtitle_source.

### 4.3 CreativeBrief

The brief preserves the original request and adds normalized medium, operation, prompt, refined prompt, negative prompt, style, composition, motion, timing, dimensions, aspect ratio, references, audio intent, delivery target, quality preference, local preference, budget ceiling and assumptions.

Prompt refinement never discards the original prompt. A user can require exact-prompt mode.

### 4.4 MediaProductionProposal

A proposal is mandatory for multi-shot, multi-engine, composite, montage, costly or long-running work. It contains:

- proposal UUID and UTC timestamp;
- project and parent revision;
- title and creative direction;
- ordered shots or segments with duration and references;
- narration, music and sound design;
- post-production recipe;
- proposed engines, services, presets and models;
- missing assets;
- estimated cost, duration and resource needs;
- warnings and approvals;
- proposal digest and state revision.

Approval choices are produce, revise and cancel. The exact approved digest is recorded on the resulting revision.

### 4.5 MediaCapability

A catalog entry declares:

- stable capability ID;
- engine and service ID;
- scope and exact service revision;
- media kinds and operations;
- accepted reference roles;
- output content types;
- preset and model identifiers;
- quality, speed, local and privacy tags;
- duration, dimension and count limits;
- cost information when known;
- required nodes, models, LoRAs and custom nodes;
- availability and failure reason.

### 4.6 FFmpegRecipe

The V1 closed operation catalog is:

- probe;
- trim;
- concat;
- resize;
- crop;
- pad;
- transcode;
- change_fps;
- extract_frame;
- extract_audio;
- replace_audio;
- mix_audio;
- duck_audio;
- normalize_loudness;
- fade;
- crossfade;
- overlay_image;
- overlay_text;
- burn_subtitles;
- loop_image_with_audio.

Paths, protocols, codecs, dimensions, durations, gains and collection sizes are validated. No shell, command, script, arbitrary arguments or unrestricted filter graph is accepted.

## 5. Capability discovery and selection

MediaCapabilityCatalog builds a bounded snapshot from:

1. visible enabled service definitions;
2. ComfyUI media presets and their declared bindings;
3. live ComfyUI readiness and object inventory when requested;
4. PocketTTS and other speech or voice-clone providers;
5. generic image, video and audio generation services;
6. FFmpeg availability and codecs on the selected relay;
7. user and conversation preferences.

Selection is deterministic:

1. filter by requested kind and operation;
2. require compatible references and output;
3. enforce hard duration, dimension, budget and privacy constraints;
4. require live availability;
5. score explicit user model or preset;
6. score local-first, quality, speed and cost preferences;
7. select a unique dominant candidate;
8. request a user choice when alternatives imply a material trade-off.

The result includes reason codes and rejected-candidate reason codes. LLM prose never decides authorization or availability.

## 6. Questions and scenarios

Questions use the canonical typed interaction store and durable wait. The agent asks only for missing information that changes feasibility, cost or creative intent.

Typical fields:

- target media or composite intent;
- required references and their roles;
- duration;
- aspect ratio and target platform;
- fidelity versus style freedom;
- speed versus quality;
- local versus remote provider;
- model choice when trade-offs are meaningful;
- language, voice and clone authorization;
- budget.

Related fields are grouped into one form.

Simple one-shot work can execute from safe defaults. Composite work must produce and receive approval for a MediaProductionProposal before provider submission.

## 7. ComfyUI control plane

### 7.1 Generalized media presets

The ComfyUI integration accepts image, video and audio media kinds. A preset declares operations, bindings, output node/key/index/content types, capabilities, limits and required inventory.

Existing image and video service definitions remain resolvable during migration, but normalize into the same MediaCapability representation.

### 7.2 Knowledge loading

A bounded deterministic task assembles only relevant references:

- installed operate-comfyui skill;
- PawFlow ComfyUI documentation;
- official ComfyUI documentation selected for the operation;
- live object_info summaries;
- installed workflow and preset metadata;
- model and LoRA cards;
- custom-node README and pinned revision metadata.

External and project content is untrusted data and cannot expand effects or approvals.

### 7.3 Workflow evolution

When no existing preset satisfies the brief:

1. report existing alternatives first;
2. draft a ComfyWorkflowRevision;
3. identify exact required nodes, models, LoRAs and custom nodes;
4. validate sources, licenses, hashes, size, VRAM and compatibility;
5. create a provisioning proposal;
6. await durable approval;
7. install only approved items;
8. restart only with an empty queue;
9. validate object inventory;
10. run a bounded low-cost smoke test;
11. publish a new immutable preset revision;
12. retain the previous active revision for rollback.

## 8. Audio and voice

Audio routing distinguishes:

- music or SFX: ComfyUI audio preset or generic audio generation service;
- ordinary speech: speak through PocketTTS or another TTS provider;
- cloned speech: clone_voice then speak;
- audio edit or mix: FFmpeg.

PocketTTS is a zero-shot clone provider. The stored user voice resource contains the owner-scoped reference, normalized hash and consent record. Identical synthesis requests use the existing content-addressed cache.

The design remains provider-independent so future voice and audio services enter through capabilities.

## 9. FFmpeg media composition

Add a first-party FFmpegMediaService and workflow-safe task.

The service:

- resolves only owner-authorized FileStore or selected-relay inputs;
- probes every input before compilation;
- compiles FFmpegRecipe into argv without a shell;
- runs in a bounded working directory on the selected relay;
- writes a unique output;
- probes and validates the result;
- stores it in FileStore or the approved destination;
- returns typed metadata and provenance;
- cleans only its own temporary files.

The workflow task declares filesystem.read, filesystem.write and process.execute effects and keyed_effect idempotency. Its authorization target includes relay and resource paths.

## 10. Workflow graph

The Media Studio flow has one English functional layout with the following colored frames:

1. Request gate — validate and classify before media access.
2. Project context — freeze or durably choose the authorized relay, then load
   project, revisions and references.
3. Capability discovery — snapshot services, ComfyUI and FFmpeg.
4. Creative direction — refine the brief and ask durable questions.
5. Scenario approval — create and approve composite production proposals.
6. Technical preparation — select engines and provision approved dependencies.
7. Production — execute image, video, audio, speech and composition branches.
8. Quality assurance — structural and modality-specific QA with bounded correction.
9. Revision and delivery — append lineage, publish artifacts and complete the turn.

Every task has a concise English label and an English description. Every frame has a distinct accessible fill and border color, a numbered label, a descriptive block, explicit membership and enough spacing to avoid edge crossings. The default layout is functional. Presentation validation and tests fail when labels, descriptions, frames or membership are missing.

## 11. Task inventory

Core workflow tasks:

- prepareMediaIntent;
- routeMediaIntent;
- prepareMediaRelay;
- applyMediaRelay;
- loadMediaProject;
- resolveMediaReferences;
- snapshotMediaCapabilities;
- prepareMediaBrief;
- validateMediaBrief;
- prepareMediaQuestions;
- prepareMediaScenario;
- validateMediaScenario;
- selectMediaCapability;
- prepareMediaProvisioning;
- splitMediaGeneration;
- submitMediaGeneration;
- joinMediaGeneration;
- validateMediaArtifact;
- composeMedia;
- appendMediaRevision;
- formatMediaStudioResult.

Reuse:

- agentWorkflowInput;
- agentLLMCall;
- receiveAgentMessages;
- requestUserInput;
- durableWait;
- requestConfirmation;
- emitAgentProgress;
- completeAgentTurn;
- inputPort and outputPort.

Package tasks receive workflow capability metadata and narrow allowed tool/service grants.

## 12. Storage and concurrency

MediaProjectStore uses SQLite transactions and optimistic state_revision checks.

Multi-shot production emits correlated FlowFiles only after scenario approval.
The shot count is bounded by `WorkflowLimits.max_fanout`, provider submission is
limited to four task instances, and the checkpointable join restores shot order
while combining unique durable jobs and artifacts before QA.

Invariants:

- every record has UUID and UTC creation timestamp;
- conversation and user are required;
- current revision belongs to the project;
- parent revision belongs to the same project;
- revisions are immutable;
- one idempotency key creates at most one revision;
- artifact references are owner-accessible;
- provider job IDs cannot be attached to another user or project;
- concurrent updates fail with an explicit conflict;
- deleting a conversation removes its media projects but follows FileStore ownership rules.

## 13. Security

- Reject unsupported intent before file or service access.
- Pin exact service definitions in WorkflowRunContext.
- Use normal AuthorizationRef lineage at every task.
- Never expose secrets in briefs, presets, reports or logs.
- Treat ComfyUI graphs and custom nodes as executable code.
- Require HTTPS, license and checksum for model or LoRA downloads.
- Require HTTPS and pinned revision for custom nodes.
- Preserve the ComfyUI queue; no restart while jobs are active.
- Require explicit clone authorization.
- Never allow arbitrary FFmpeg arguments or paths.
- Keep public media shares temporary and revoke them after provider use.
- Preserve all prior outputs.

## 14. UI

The conversation renders:

- intent and current project;
- resolved references with role and preview;
- grouped durable question forms;
- scenario cards with Produce, Revise and Cancel;
- selected engine/model with human-readable reasons;
- progress by functional stage;
- generated image, video and audio artifacts;
- revision tree and active revision;
- Retry, Modify, Compare and Return to revision actions;
- provisioning proposal with exact changes;
- QA warnings.

Agent resource UI shows Workflow badge, exact flow version, service parameters, preempt policy and limits.

## 15. Documentation

Update in the same delivery:

- docs/media_tools.md;
- docs/comfyui.md;
- docs/voice_clone.md;
- docs/02_REFERENCE_TASKS_SERVICES.md;
- docs/AGENT_SYSTEM.md;
- docs/WORKFLOW_AGENT_OPERATIONS.md;
- package skill and manifest descriptions.

All documentation, source comments, UI labels and descriptions are English.

## 16. Work packages

### WP0 — Contracts and red gates

- Add pure contracts for intent, references, briefs, proposals, capabilities, recipes, projects and revisions.
- Add schema, UUID, timestamp, size and cross-reference tests.
- Add presentation contract tests for English labels, descriptions and colored frames.

### WP1 — MediaProjectStore

- Add SQLite schema and transactional API.
- Add append-only revision and optimistic concurrency tests.
- Add cleanup integration.

### WP2 — Capability catalog

- Normalize installed PawFlow media services and ComfyUI presets.
- Add deterministic filtering, scoring, alternatives and reason codes.
- Add service-snapshot and scope tests.

### WP3 — FFmpeg service

- Add typed recipe validator and safe argv compiler.
- Add relay execution, ffprobe validation and FileStore output.
- Add injection, path, limit, idempotency and fixture-based media tests.

### WP4 — Media workflow tasks

- Add deterministic preparation, routing, validation, selection, project and formatting tasks.
- Make canonical typed interactions admissible in Workflow Agents with explicit effects and idempotency.
- Add task-unit tests.

### WP5 — Agent flow and resource

- Publish pawflow.agents.media-studio:1.0.0.
- Add one agent resource bound to the exact flow.
- Add the complete functional layout, colored frames, labels and descriptions.
- Validate through the normal Workflow Agent validator.

### WP6 — Generation adapters

- Add workflow-safe, exact-service media submission.
- Support image, video, ComfyUI audio, generic audio, speech and clone paths.
- Persist stable job and result correlation.
- Add retry and recovery tests. Explicitly retryable task failures pause the same
  WorkflowRun with an exact task/FlowFile checkpoint; retry preserves the run ID
  and provider idempotency key. Submitted jobs without a durable recovery result
  remain non-retryable and fail closed instead of being submitted twice.

### WP7 — ComfyUI evolution

- Generalize preset metadata to audio.
- Add inventory and knowledge preparation.
- Add immutable workflow revision and provisioning proposal contracts.
- Add queue-safe smoke and promotion gates.

### WP8 — UX

- Add scenario, reference, engine-choice, progress, artifact and revision surfaces.
- Support restore after reconnect in Web, PawCode and VS Code.
- Add accessibility and locale coverage.

### WP9 — Integration and migration

- Import or normalize current ComfyUI image/video presets.
- Build bundled PFP artifacts.
- Test installation, update and uninstall protection.
- Keep feature activation gated.

### WP10 — Validation and delivery

- Focused unit and integration matrix.
- Full pytest suite.
- Ruff blocking gate, syntax compile, package build and security scan.
- Manual canary for unsupported, image, video, audio, clone, scenario, modification and FFmpeg.
- No hotpatch or release action without explicit authorization.

## 17. Test matrix

Required scenarios:

1. unrelated request stops before project or service access;
2. text-to-image with safe defaults;
3. ambiguous video duration asks one durable form;
4. attached image is assigned source_image;
5. ambiguous multiple references require role selection;
6. multi-shot video produces a proposal and waits;
7. stale proposal approval fails closed;
8. installed ComfyUI preset wins when dominant;
9. meaningful local versus paid trade-off asks the user;
10. missing model creates a provisioning proposal, not a download;
11. denied provisioning leaves the installation untouched;
12. approved pinned LoRA install publishes a new preset revision;
13. ComfyUI audio preset is selectable;
14. generic audio fallback works;
15. PocketTTS ordinary speech works;
16. clone without authorization is blocked;
17. authorized clone persists and cached speech is reused;
18. FFmpeg recipe rejects shell and traversal input;
19. composite video plus voice plus music creates one lineage;
20. modification creates a child revision and preserves the parent;
21. correction before submission updates the brief;
22. correction after submission supersedes without losing output;
23. crash recovery does not duplicate provider submission;
24. service definition change cannot alter an active run;
25. every flow frame, group and task has an English label and description;
26. every functional frame has a distinct color and valid membership;
27. layout has no ungrouped functional tasks;
28. terminal result contains exact artifacts and answered turn IDs.

## 18. Definition of done

The feature is done only when:

- the plan and owned documentation are current;
- contracts, store, catalog, FFmpeg service, workflow tasks, flow and agent resource are implemented;
- image, video, audio, speech, clone, composite and modification paths are covered;
- ComfyUI can expose audio presets;
- questions and scenario approval are durable;
- every graph is visually grouped with English colored labels and descriptions;
- focused and global tests are green;
- package build and security gates are green;
- no unrelated file was changed;
- no commit, push, flag activation, hotpatch or release occurred without authorization.

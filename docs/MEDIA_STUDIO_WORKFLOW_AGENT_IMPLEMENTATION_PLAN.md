# Media Studio Workflow Agent Evolution Plan

Status: Media Studio 1.0.0 installed; production/review evolution 1.1 planned
Date: 2026-08-30
Owner: PawFlow core and first-party media packages
Priority: P0 after the multi-conversation tiled workspace

## 1. Reviewed verdict

The installed Media Studio 1.0.0 has a solid secure generation foundation, but it
is currently closer to a governed media-generation workflow than a complete
production and review studio.

It can classify, plan, select a deterministic capability, generate, store, and
append revisions. It does not yet support the real production cycle observed on
Tiles and Soup Palm: compare several attempts, approve per shot, regenerate only
one element with continuity, assemble approved choices, and approve a reproducible
master.

This plan is an evolution of the existing backend, not a rewrite.

## 2. Installed baseline to preserve

The 1.0 delivery includes:

- intent filtering before project, file, or service access;
- immutable capability snapshots and deterministic selection;
- durable forms and confirmations across reconnect;
- exact service revision pinning;
- idempotent fail-closed behavior and retention of old artifacts;
- bounded multi-shot fan-out with at most four concurrent generations and a join;
- FileStore as the exact deliverable source;
- explicit voice-clone consent;
- closed FFmpeg recipes without raw shell arguments;
- append-only MediaProjectStore with parentage and optimistic concurrency;
- image, video, audio, speech, clone, and composition adapters;
- immutable ComfyUI preset revisions and reviewed provisioning proposals;
- the installed pawflow.media-studio:1.0.0 package and
  pawflow.agents.media-studio:1.0.0 workflow.

These invariants remain mandatory in 1.1.

## 3. Verified live gaps

### 3.1 Revision resume is incomplete

PrepareMediaIntent emits revision_selector, but LoadMediaProjectTask in
tasks/ai/workflow/media_tasks.py loads only the current project. The selector is
not consumed.

PrepareMediaBriefTask rebuilds context from the new message and new attachments.
It does not inherit the selected parent brief, references, scenario, technical
parameters, or artifacts.

MediaProjectStore already has get_revision and list_revisions, but no workflow/UI
handler exposes the complete revision operations.

Result: a request such as regenerate only shot 11 with exactly the same character
requires the user to repeat context and often reattach references.

### 3.2 Revise ends instead of looping

In the installed flow, scenario decision revise routes to format_result. An
invalid artifact also routes to a terminal unavailable result. Both paths force a
new message and new WorkflowRun.

Target behavior:

    scenario -> revise -> corrected brief/scenario -> approval
    invalid QA -> diagnosis -> targeted correction -> QA

Both loops are bounded. Creative approval remains human.

### 3.3 The real ComfyUI job identity is lost

Media Studio stores a synthetic sync:<internal job> reference and calls the
handler synchronously. ComfyUIClient.run in services/_comfyui_client.py creates
the real prompt_id immediately before blocking on history.

If PawFlow restarts after submit, ComfyUI may finish, but Media Studio cannot
retrieve the output. Replay correctly refuses duplicate submission and leaves the
job submitted, but no recovery path exists.

The integration must split submit, durable wait, and retrieve and persist the
real prompt_id before waiting.

### 3.4 Current QA is reference validation, not media QA

ValidateMediaArtifactTask verifies that an artifact exists and that file_id
matches its FileStore URL. It does not verify decode, duration, dimensions, FPS,
codecs, audio streams, clipping, loudness, transitions, synchronization,
identity continuity, or content requirements.

### 3.5 Multi-shot ShotSpec is too permissive

The scenario requires only shot ID and duration. Fan-out uses shot.prompt when
present and otherwise reuses the global prompt for every shot. Distinct shot
intent and continuity are therefore not contractual.

### 3.6 No variant review or editorial lock

A generic immutable revision cannot express generation attempts A/B/C,
per-candidate review, an approved artifact, editorial lock, supersession, and
replacement independently.

### 3.7 Montage is not an editorial manifest

FFmpeg recipes describe execution, but the project does not retain a
human-readable independent edit decision: shot order, exact files, trims,
transitions, black frames, sound layers, subtitles, ducking, loudness, and master
settings.

### 3.8 Terminal result is too weak

FormatMediaStudioResultTask returns a generic project/revision/artifact summary.
The UI does not expose batch review, remaining decisions, per-shot QA,
continuity, approved revision, edit manifest, or targeted actions.

## 4. 1.1 outcome

Deliver a production and review layer in the existing Media Studio:

1. resume any authorized revision with complete inherited context;
2. apply explicit localized deltas;
3. submit, wait, and retrieve provider jobs durably;
4. validate media with typed automatic QA;
5. generate and compare bounded candidate sets;
6. record human decisions and editorial locks per shot/segment;
7. regenerate only rejected/unlocked elements;
8. compile an explicit edit manifest through safe FFmpeg recipes;
9. run master QA and final human approval;
10. expose the entire state as a structured production board.

## 5. Non-goals

- Do not create a separate Media Studio application.
- Do not build a Premiere-style timeline editor.
- Do not expose raw ComfyUI graphs to ordinary users.
- Do not let an LLM auto-approve aesthetic quality.
- Do not automatically repeat an expensive generation after a warning.
- Do not overwrite or delete rejected variants.
- Do not require confirmation at every step.
- Do not let Media Studio install or modify models/custom nodes automatically.
- Do not copy AGPL OpenMontage code into the MIT core.

## 6. Revised domain model

### 6.1 MediaProject

Add:

- current_revision_id;
- approved_revision_id;
- active_edit_manifest_id;
- final_master_artifact_id;
- production status;
- optimistic state_revision.

Current and approved are distinct. Returning to a historical revision does not
silently change the approved baseline.

### 6.2 MediaRevisionV2

Contains:

- UUID/timestamp, parent, project, user, conversation, turn, and run IDs;
- complete inherited context snapshot;
- original request and normalized delta;
- brief and proposal digests;
- exact references and continuity anchors;
- ShotSpec collection;
- candidate/review/lock references;
- job and artifact references;
- QA reports;
- edit manifest reference;
- status and supersession.

Revisions remain immutable.

### 6.3 RevisionDeltaV1

Required fields:

- base_revision_id;
- change_scope: project, sequence, shot:<id>, audio, subtitles, transition, or
  master;
- change instructions;
- keep anchors such as identity, wardrobe, voice, camera, palette, seed policy,
  timing, or approved media;
- replace references;
- expected project state_revision;
- reason and requesting turn.

The resolver produces a full inherited context plus an explicit delta report.
Omitted fields inherit; explicit null/reset is typed and auditable.

### 6.4 ShotSpecV1

Every produced shot requires:

- shot_id and objective;
- prompt and negative constraints;
- duration, framing, aspect, resolution, and expected output;
- action, subject motion, camera movement, and dialogue;
- references and the role of each;
- identity, wardrobe, environment, palette, camera, and temporal continuity
  anchors;
- engine, service, preset, model, seed policy, and parameters;
- dependency on previous/next shots;
- acceptance criteria and QA profile;
- candidate count and cost ceiling.

A missing prompt or acceptance contract is an error; global prompt fallback is
removed.

### 6.5 MediaGenerationJobV1

Contains stable job ID, receipt/idempotency data, shot/candidate identity, exact
service snapshot, provider, real provider reference, submitted/observed
timestamps, state, retry safety, output selection, and failure projection.

States:

    prepared -> submitted -> waiting -> available -> retrieved -> verified
                                  -> unknown -> reconciled
                         -> failed or cancelled

Provider reference persistence is transactional before any wait.

### 6.6 MediaCandidateV1

Separates a technical generation from an editorial candidate:

- candidate_id and label such as A, B, C;
- shot/segment and generation job;
- exact FileStore artifact;
- automatic QA;
- continuity report;
- review state;
- supersession/replacement;
- created revision.

### 6.7 ReviewDecisionV1 and EditorialLockV1

Review decisions are approved, changes_requested, or rejected and include actor,
timestamp, comment, criteria, candidate, expected project generation, and
decision digest.

An editorial lock identifies the approved candidate for a shot/segment, the
decision that authorized it, and the revision in which it became active. Replacing
a lock creates a new lock that supersedes the old one. It never mutates or deletes
the old candidate.

### 6.8 EditManifestV1

The manifest is the readable editorial decision, independent of FFmpeg argv:

- ordered approved shots with exact FileStore IDs and digests;
- trims, speed, transforms, and transitions;
- black frames and exact durations;
- voice, music, SFX, ambience, and subtitle tracks;
- time positions and channel mapping;
- ducking rules, loudness target, and true-peak ceiling;
- master resolution, FPS, codecs, bitrate/quality, color, and audio settings;
- expected duration and hash;
- source project/revision/lock digests.

FFmpegRecipe is compiled from this manifest. Arbitrary filter graphs remain
forbidden.

### 6.9 Typed QA reports

MediaQAReportV1 records profile, artifact, probe data, checks, severity, result,
diagnostics, correction eligibility, and evidence artifacts.

Profiles:

- image: decode, dimensions, orientation, colorspace, alpha;
- video: ffprobe, full decode, frame count, duration, FPS, codecs, dimensions,
  color, audio presence, frozen/black-frame bounds;
- audio: decode, duration, channels, sample rate, integrated loudness, true peak,
  clipping, silence bounds;
- montage: expected/actual timeline, transition contact sheets, join boundaries,
  subtitle bounds, A/V synchronization;
- multi-shot: contact sheet per shot and continuity report;
- human review: approved, changes_requested, rejected, and comment.

Semantic/aesthetic model observations are advisory. Only humans approve creative
quality.

## 7. Revision operations

Add scoped workflow/UI handlers:

- list_media_revisions;
- get_media_revision;
- compare_media_revisions;
- fork_media_revision;
- return_to_media_revision;
- set_approved_media_revision;
- load_revision_context.

Return selects a historical snapshot as the current working base by creating a
new child pointer revision; it never mutates history. Fork creates a child with a
new delta. Compare reports structural and artifact differences.

Every mutation requires authenticated identity, project scope, expected
state_revision, and an idempotency key.

## 8. Durable provider boundary

Replace synchronous generation with three workflow-safe tasks:

### submitMediaGeneration

- validates frozen ShotSpec and service snapshot;
- creates/resolves MediaGenerationJobV1;
- prepares an effect receipt;
- calls a submit-only provider method;
- persists the real prompt_id/provider reference immediately;
- returns without polling.

### waitMediaGeneration

- uses durable wait or webhook notification;
- never occupies an HTTP worker or active agent loop;
- records bounded progress;
- on timeout/restart retains submitted/waiting/unknown;
- never resubmits.

### retrieveMediaGeneration

- queries exact history/reference;
- selects the declared provider output;
- downloads once through bounded adapter code;
- stores the artifact in FileStore;
- records digest/provenance;
- advances the effect receipt and job state.

ComfyUIClient gains submit, status/history, and retrieve methods. run may remain a
compatibility composition only until all callers migrate, then is removed in the
one-shot migration.

## 9. Flow graph 1.1

Functional stages:

1. Intent gate.
2. Project and selected revision context.
3. Full inheritance and RevisionDelta validation.
4. Capability snapshot.
5. Brief and ShotSpec preparation.
6. Scenario approval loop.
7. Bounded candidate planning.
8. submit -> durable wait/webhook -> retrieve.
9. Typed automatic QA.
10. Bounded technical correction loop.
11. Batch A/B/C human review.
12. Regenerate only rejected or unlocked elements.
13. Editorial lock and approved revision.
14. EditManifest approval.
15. FFmpeg compilation and master generation.
16. Master QA and final human approval.
17. Structured project result.

Scenario revise loops to brief/scenario. Invalid QA loops to a typed correction
planner only when correction is safe, bounded, and non-creative. Maximum attempts
are explicit per project/profile. Exhaustion becomes a human decision, not an
automatic expensive retry.

## 10. Production board and UI

Extend existing durable cards; do not build a separate NLE.

Required surfaces:

- project summary with current and approved revision;
- scenario card with Produce, Revise, and Cancel;
- shot list with ShotSpec, dependencies, QA, and continuity;
- synchronized A/B/C candidate grid;
- Approve, Reject, Request changes, Regenerate this shot, Compare, and Lock;
- revision tree with Current, Approved, Fork, Return, and Compare;
- job card with real provider reference and recovery state;
- outstanding decision table;
- edit manifest summary and master settings;
- master QA and final approval;
- exact FileStore artifact previews/downloads.

A representative board:

    Shot 11
      A — rejected: identity unstable
      B — approved and locked
      C — pending

    Bumper
      A — rejected
      B — approved and locked
      C — rejected

Reconnect reconstructs cards from authoritative stores. UI state is never the
source of review or lock decisions.

## 11. Fewer round trips

Current real workflow:

    request -> scenario -> render -> chat links
    -> user comment -> new run without full inheritance
    -> render -> separate QA -> choice by message
    -> manual edit -> separate QA -> delivery

Target:

    locked brief -> candidate batch -> automatic QA
    -> one structured batch review
    -> regenerate only rejected elements
    -> lock approved shots
    -> compile edit manifest -> master QA -> final approval

Tiles V3 through V7 corrections become localized deltas for voice, subtitle
order, opening, ending, and mix. Soup Palm retains all eighteen shots, bumper B,
and transition decisions as explicit locks and a reproducible manifest.

## 12. Storage and concurrency

Extend MediaProjectStore through one-shot schema migration with tables for jobs,
candidates, decisions, locks, manifests, and QA reports.

Rules:

- append-only domain records;
- one idempotency key creates one record;
- CAS project generation for decisions and pointers;
- one active lock per shot projection, with immutable supersession history;
- FileStore access checked at creation and read;
- provider references cannot cross user/project;
- exact run and service revisions retained;
- cleanup follows conversation and FileStore ownership;
- rejected artifacts remain available;
- large reports/contact sheets live in FileStore.

Do not create a second production-board store; the board projects these tables.

## 13. Security and cost

- Preserve intent gate before all project/service/file reads.
- Pin exact service/preset/model revisions.
- Use effect receipts for provider submit and FFmpeg compilation.
- Never expose ComfyUI raw graphs to ordinary users.
- Preserve closed FFmpeg operations and path validation.
- Require voice consent and reference ownership.
- Treat probes, model observations, provider metadata, and media as untrusted.
- Bound candidates, shots, correction attempts, output bytes, duration, and cost.
- Require human approval before expensive regeneration after warnings.
- Never install models/nodes without the existing exact provisioning proposal.
- Redact provider IDs only where they carry secrets; retain safe prompt_id needed
  for recovery.
- Use separate technical and creative decision roles.

## 14. Migration

1. add V2-compatible tables and contracts;
2. map each existing revision to a legacy single-candidate projection without
   fabricating review approval;
3. preserve current_revision_id;
4. leave approved_revision_id empty until explicit review;
5. migrate submitted jobs that lack real provider references to
   unrecoverable_legacy with no resubmit;
6. publish flow/package version 1.1.0 alongside 1.0.0;
7. validate canaries and move new runs to 1.1.0;
8. remove 1.0 compatibility code in the next breaking package revision.

Existing artifacts and revisions are never overwritten.

## 15. Work packages and order

### WP0 — Red tests and contract corrections

Capture the verified gaps: revision_selector ignored, full parent context absent,
revise terminal route, invalid QA terminal route, synthetic ComfyUI job ID,
structural-only QA, global prompt fallback, and generic terminal result.

### WP1 — Full revision context

Add RevisionDeltaV1, selected revision loading, inheritance, compare/fork/return,
current versus approved pointers, handlers, and CAS tests.

### WP2 — Durable ComfyUI jobs

Split submit/wait/retrieve, persist real prompt_id before wait, add recovery and
webhook/status polling, and migrate adapters.

### WP3 — Typed QA

Add probe/decode profiles, reports/evidence, thresholds, contact sheets, and
bounded technical correction decisions.

### WP4 — ShotSpec and candidate production

Require full ShotSpec, remove prompt fallback, generate bounded A/B/C candidates,
and retain exact lineage/cost.

### WP5 — Review and editorial locks

Add decisions, notes, approval/rejection, lock/supersession, batch review, and
approved revision projection.

### WP6 — EditManifest and master

Add manifest contracts, validation, FFmpeg compilation, reproducibility digest,
master QA, and final approval.

### WP7 — Production board and result

Add specialized cards, revision tree, candidate grid, outstanding decisions,
targeted actions, and structured terminal result.

### WP8 — Package migration and operations

Publish 1.1 flow/package/resources, migration, metrics, runbook, canary, rollback,
and compatibility removal.

### WP9 — Documentation and delivery

Update media, ComfyUI, voice, FFmpeg, Workflow Agent operations, task/service
reference, package skill, and user guidance. Run focused and full CI.

## 16. Test matrix

Required scenarios include:

1. unrelated request stops before project/service/file access;
2. selected historical revision loads full brief/references/scenario/artifacts;
3. localized shot delta preserves declared continuity anchors;
4. stale project generation rejects mutation;
5. compare/fork/return preserve immutable lineage;
6. current and approved revision remain distinct;
7. scenario revise loops and requests a new approval;
8. correction loop is bounded and never makes creative approval;
9. submit persists real ComfyUI prompt_id before wait;
10. restart after submit retrieves without resubmission;
11. unknown job reconciles before retry;
12. image/video/audio probes detect corrupt fixtures;
13. loudness, peak, clipping, duration, FPS, codec, and A/V checks work;
14. ShotSpec missing prompt/acceptance fails;
15. no global-prompt fallback exists;
16. candidate A/B/C jobs and artifacts remain distinct;
17. review decisions require actor, comment policy, and expected generation;
18. locks supersede without deleting old candidates;
19. regenerate-this-shot leaves other locked shots unchanged;
20. manifest references exact approved FileStore IDs;
21. FFmpeg output is reproducible from the manifest;
22. transition/contact-sheet and master QA evidence is retained;
23. rejected artifacts remain accessible;
24. final approval pins master hash;
25. reconnect reconstructs outstanding decisions;
26. legacy synthetic jobs never auto-resubmit;
27. package install/update/uninstall and exact flow validation pass;
28. full security and Python 3.10–3.13 CI pass.

## 17. Definition of done

Media Studio 1.1 is complete when a user can reopen any production revision,
change only one declared element, recover every submitted provider job, review
several candidates per shot, lock approved choices, compile a readable edit
manifest, validate the exact master, and reproduce the final artifact without
reconstructing decisions from chat history.

## 18. External influence and license boundaries

OpenMontage was reviewed at SHA cd9f3c1 under AGPL-3.0. Its storyboard approval,
provider scoring, phased budgets, checkpoints, archived replacements, and
production-board concepts may inform clean-room behavior or a separately
deployed service. No AGPL source, tests, prompts, text, or assets enter PawFlow
MIT core or first-party MIT packages.

GameFactory-3A was reviewed at SHA 6670bb7 under Apache-2.0. Selective QA,
artifact-promotion, and package-adapter patterns require attribution and
qualification; upstream evaluation coverage was incomplete at the reviewed
revision.

Salomondiei08/oh-my-hermes had no declared license at the reviewed revision.
Product/dead-letter loop concepts are ideas only; no content may be copied.

# Archify Integration Plan

Status: planned
Priority: P1 optional package after the multi-conversation workspace
Date: 2026-08-30
Owner: PawFlow package ecosystem, Project Graph, Project Wiki, and FileStore

## 1. Outcome

Deliver an optional signed PFP package that generates verifiable architecture,
workflow, sequence, data-flow, and lifecycle diagrams from PawFlow Project Graph,
Project Wiki, selected source evidence, and explicit user descriptions.

The package must use a typed diagram intermediate representation, stable
diagnostics, deterministic validation, and atomic last-good publication. It
exports self-contained HTML plus SVG and PNG artifacts to FileStore without
making a diagram renderer part of the PawFlow core runtime.

## 2. Verified baseline

PawFlow already provides:

- relay-scoped Project Graph AST entities and edges;
- sourced Project Wiki pages with freshness state;
- FileStore ownership and artifact URLs;
- signed PFP package lifecycle and provenance;
- workflow-safe filesystem and browser tooling through Relay;
- agent skills and package resources;
- flow/task capability metadata and authorization;
- optional image/browser rendering services.

Missing features are a diagram-specific IR, evidence linkage, deterministic
diagram diagnostics, render/export tasks, and atomic last-good artifact history.

## 3. Product boundary

The integration is a package, proposed identity:

- package: pawflow.archify;
- initial version: 1.0.0;
- optional skill: architecture-diagrams;
- flow tasks: prepareDiagramEvidence, buildDiagramIR, validateDiagramIR,
  renderDiagram, publishDiagramArtifacts;
- optional agent workflow or Process Group for reviewed generation.

PawFlow core owns generic Project Graph/Wiki/FileStore/PFP contracts. The package
owns diagram vocabulary, layout, rendering, templates, and presentation.

## 4. Non-goals

- Do not replace Project Graph or Project Wiki.
- Do not parse the repository again when the graph has current evidence.
- Do not install a browser or renderer into the base image.
- Do not treat generated diagrams as authoritative source architecture.
- Do not publish invalid output over the last valid revision.
- Do not embed source files, secrets, credentials, or arbitrary HTML.
- Do not execute user JavaScript from labels, themes, or templates.
- Do not copy the entire upstream skill, site, examples, or branding.

## 5. Architectural decisions

1. DiagramIRV1 is the only renderer input.
2. Evidence selection is separate from LLM-assisted IR authoring.
3. Validation is deterministic and runs before rendering.
4. Every node and edge can carry source evidence.
5. Layout hints are advisory within bounded deterministic rules.
6. Rendering occurs in the package/relay environment, never through direct
   server filesystem access.
7. FileStore is the source of published artifacts.
8. Publication is atomic: a revision becomes current only after every required
   artifact validates.
9. Last-good remains available after any failed generation.
10. User edits create child revisions and preserve parents.

## 6. DiagramRequestV1

Required fields:

- schema_version;
- request_id and created_at;
- user_id and conversation_id;
- diagram_kind;
- title, audience, and purpose;
- project relay ID and root;
- requested entities or question;
- output formats;
- detail and motion preferences;
- theme ID;
- maximum nodes and edges;
- source freshness requirements.

Kinds are architecture, workflow, sequence, data_flow, lifecycle, dependency,
and deployment.

## 7. EvidenceSnapshotV1

The evidence snapshot freezes:

- Project Graph build identity, timestamp, source hashes, and selected nodes/edges;
- Project Wiki page slugs, freshness, source citations, and content digests;
- explicit user-provided facts with message/attachment references;
- optional selected source excerpts with path, lines, and digest;
- exclusions and truncation diagnostics;
- snapshot digest.

Stale wiki claims are never silently treated as current. The plan either refreshes
the source, labels the evidence stale, or excludes it.

External text and repository content are untrusted data and cannot modify tool
effects, output paths, templates, or rendering code.

## 8. DiagramIRV1

Top-level fields:

- schema_version;
- diagram_id, revision_id, and parent_revision_id;
- request and evidence digests;
- kind, title, subtitle, and legend;
- nodes, edges, groups, lanes, notes, and views;
- theme and layout hints;
- source map;
- accessibility metadata;
- generator and renderer revisions.

Node fields include stable ID, type, label, concise description, group, ports,
status, style token, evidence IDs, and optional URL restricted to safe FileStore
or source references.

Edge fields include stable ID, source, target, relation type, label, direction,
cardinality, protocol/data classification, style token, and evidence IDs.

Free-form CSS, HTML, JavaScript, SVG fragments, external scripts, remote fonts,
and data URLs are forbidden.

## 9. Validation and diagnostics

DiagramDiagnosticV1 contains code, severity, message, IR path, related IDs,
evidence IDs, and optional safe fix hint.

Stable V1 diagnostics cover:

- invalid schema/version/UUID/timestamp;
- duplicate or missing IDs;
- dangling edges and ports;
- containment cycles;
- unsupported relation/type/style token;
- missing evidence for asserted architecture;
- stale or unavailable source;
- node/edge/detail limit overflow;
- unreadable contrast or missing accessible label;
- layout collision and clipped content;
- unsafe URL or forbidden markup;
- renderer capability mismatch;
- output digest or decode failure.

Errors block rendering or publication. Warnings remain visible in the report and
artifact metadata. An LLM cannot suppress a deterministic error.

## 10. Authoring flow

1. classify the request and reject unrelated work before repository access;
2. resolve the authorized relay and project;
3. query Project Graph and Project Wiki narrowly;
4. freeze EvidenceSnapshotV1;
5. produce DiagramIRV1 through deterministic templates and optional bounded LLM
   assistance;
6. validate and return structured diagnostics;
7. apply at most a bounded number of automatic structural corrections;
8. request human input for semantic ambiguity;
9. render exact formats;
10. validate artifacts;
11. publish one immutable revision atomically;
12. return artifacts, evidence report, diagnostics, and revision actions.

Semantic claims remain human-reviewable. The package never rewrites project
source to make a diagram pass.

## 11. Rendering

The renderer accepts only validated IR and a bundled allowlisted theme.

Required outputs:

- self-contained HTML with accessible static content;
- SVG with sanitized text and deterministic viewBox;
- PNG produced from the exact HTML/SVG revision when a qualified renderer is
  available;
- JSON IR and validation report;
- provenance manifest with SHA-256 digests.

Motion is progressive enhancement and disabled under prefers-reduced-motion.
The static diagram remains complete without JavaScript. External network access
during render is disabled.

Layout must be deterministic for the same IR, renderer, fonts, viewport, and
theme. Renderer and font revisions are included in provenance.

## 12. Atomic last-good publication

DiagramRevisionStore is package-owned and conversation/user scoped. It stores
metadata and FileStore references, not duplicate artifact bytes.

Publication transaction:

1. create candidate revision;
2. store all required artifacts in FileStore;
3. verify content type, decode, dimensions, links, and digests;
4. write the immutable revision;
5. CAS-update current_revision_id;
6. retain last_good_revision_id;
7. publish a UI event.

Failure before step 5 leaves current and last-good untouched. Orphan candidate
artifacts follow FileStore cleanup policy and are never presented as current.

Actions: compare, return to revision, fork, rerender with the same IR, and rebuild
from new evidence.

## 13. Project Graph and Wiki integration

Use project_graph query/node/report before free-text code search for structural
questions. Use project_wiki query/page for sourced architecture context and
validate stale claims against live files.

The package records exact evidence IDs, not a prose-only bibliography. A UI
evidence drawer links diagram elements to graph entities, wiki pages, and source
paths. Missing access after creation hides the underlying source while preserving
the artifact and redacted provenance.

## 14. Security

- Require explicit relay/project scope.
- Apply existing graph/wiki and FileStore ACLs.
- Bound evidence, IR, labels, groups, views, and artifact sizes.
- Escape every label and sanitize all generated SVG/HTML.
- Use a strict CSP with no remote scripts, styles, fonts, or frames.
- Disable renderer network access and arbitrary file URLs.
- Resolve output only through package scratch space and FileStore.
- Reject path traversal and symlink escape.
- Never place repository secrets or raw environment data in evidence.
- Treat diagram links as untrusted and allowlist schemes.
- Verify package signature, provenance, license, and dependency revisions.

## 15. UI

Add a diagram artifact card with:

- title, kind, current/last-good revision, and freshness;
- HTML preview and SVG/PNG downloads;
- validation summary and warning count;
- evidence/provenance drawer;
- Compare, Refresh evidence, Rerender, Fork, and Return actions;
- accessible static fallback;
- explicit failed-candidate state that does not replace last-good.

Project Graph and Wiki panels may launch the package with selected entities/pages.
They do not gain renderer logic themselves.

## 16. Package and service dependencies

The PFP manifest declares:

- package tasks and skill;
- optional LLM binding for IR assistance;
- Project Graph/Wiki read capabilities;
- FileStore write capability;
- optional browser/image renderer service;
- exact secret-free configuration;
- bundled themes/fonts with license inventory.

Install succeeds without PNG capability; HTML/SVG remain available and PNG is
reported unavailable with a stable reason.

## 17. Migration and rollout

There is no core data migration.

1. implement package contracts and validators;
2. qualify HTML/SVG rendering;
3. add FileStore publication and revision store;
4. add Project Graph/Wiki evidence adapters;
5. add optional PNG renderer;
6. add UI cards and launch actions;
7. canary on representative Python/JS/mixed repositories;
8. publish signed package after license and security review.

Package uninstall preserves FileStore artifacts under normal ownership rules and
removes package runtime/resources.

## 18. Work packages

### WP0 — Contract and license ledger

Define requests, evidence, IR, diagnostics, provenance, revisions, and bundled
asset licenses.

### WP1 — Evidence adapters

Add bounded Project Graph/Wiki/source selection and freshness checks.

### WP2 — IR builder and validator

Implement deterministic templates, optional LLM boundary, validation codes, and
bounded correction.

### WP3 — HTML/SVG renderer

Implement safe themes, deterministic layout, accessibility, CSP, and artifact
validation.

### WP4 — PNG and export

Add qualified optional renderer, exact viewport/font provenance, and decode tests.

### WP5 — Atomic publication

Add revision store, FileStore transaction boundary, last-good CAS, compare/fork,
and cleanup.

### WP6 — PFP resources and UI

Build/install package, skill, tasks, optional workflow, artifact card, evidence
drawer, and actions.

### WP7 — Documentation and delivery

Document package use, authoring, security, evidence semantics, and operations.
Run package and full CI.

## 19. Test matrix

Required tests include:

1. deterministic IR normalization and IDs;
2. duplicate/dangling/cyclic structures fail with stable codes;
3. stale wiki evidence is labelled or excluded;
4. unauthorized relay/source access fails;
5. prompt injection in source text cannot change effects/templates;
6. HTML/SVG labels escape active content;
7. CSP forbids remote and inline executable content;
8. renderer cannot reach network or arbitrary files;
9. node/edge/size limits hold;
10. same IR produces stable SVG/HTML digests;
11. PNG provenance matches exact source revision;
12. failed candidate preserves current and last-good;
13. concurrent publication uses CAS;
14. compare/fork/return preserve lineage;
15. FileStore ACLs and cleanup work;
16. package install/update/uninstall and signature checks pass;
17. missing PNG provider degrades honestly;
18. prefers-reduced-motion and accessibility checks pass;
19. representative diagrams remain readable at supported viewports;
20. full PFP, security, and Python CI pass.

## 20. Definition of done

A user can select current project evidence, produce a readable diagram whose
claims link to sources, receive stable diagnostics, export deterministic
artifacts, and recover the last valid revision after any failed update. Core
PawFlow remains usable without the package.

## 21. Upstream influence and license ledger

Typed IR, stable diagnostics, source proof, and atomic last-good patterns were
evaluated from tt-a1i/archify at SHA
b36d79fdbc3aec3728744341485a7e79f03c0071 under MIT. Selective reuse requires
copyright and license attribution.

The PawFlow package must maintain a file-level reuse ledger. Upstream branding,
website, examples, generated galleries, dependencies, fonts, and assets are not
copied without separate provenance and license review.

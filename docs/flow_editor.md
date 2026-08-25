# Flow Editor

The Flow Editor turns the existing flow viewer (`flow_graph.html`,
ReactFlow + dagre) into a NiFi-style graphical editor. There is ONE canvas
with three modes — `view` (templates / subflows), `runtime` (the
[Flow Runtime Console](flow_runtime_console.md)) and `edit` — never three
divergent implementations.

This document covers the **authoring foundation** and the complete canvas
editing loop through process groups, version-pinned subflows and safe runtime
hot-swap editing.

## Principles

- **The PawFlow JSON definition is the source of truth.** ReactFlow
  nodes/edges are a projection; the editor patches the document and never
  rebuilds a minimal JSON from the graph. A `load → save` round-trip without
  changes preserves every field — `runtime_links`, `ports`, `scope`,
  metadata, package fields, and unknown/future fields.
- **Published versions are immutable.** Editing `1.0.0` never overwrites
  `versions/1.0.0.json`: it opens a draft, and publishing creates `1.1.0`
  through `ScopedRepository.publish_flow_version()` (or `create_flow()` for
  a brand-new flow). The repository refuses an existing version.
  A version is only ever *added* (publish) or *deleted* (the Versions
  dialog 🗑 / `delete_version`), never modified; the last remaining version
  cannot be deleted (delete the flow instead) and deleting the latest
  re-points `latest.json` to the highest remaining version.
- **Drafts live outside the repository**, in
  `data/runtime/flow_editor_drafts/<user_id>/<draft_id>.json`:

  ```json
  {"draft_id": "d_3f9a1c2b7d4e", "user_id": "alice",
   "flow": "media.video_generation", "scope": "user", "conv_id": "",
   "base_version": "1.2.0", "revision": 14,
   "created_at": 1787212000.1, "updated_at": 1787212300.7,
   "definition": {"...": "the full flow JSON"}}
  ```

  Drafts are private to their user.
- **Optimistic locking, never last-writer-wins.** Every save carries the
  `base_revision` the client loaded; the server accepts only if its
  revision still matches, otherwise it answers `409 draft_changed_elsewhere`
  (two tabs editing the same draft).
- **One validator.** `FlowDefinitionValidator` (`core/flow_definition_validator.py`)
  is shared by the Web editor, agent tools, CLI, PFP validation, publish and
  tests. It is *static*: it never resolves `${...}` expressions (secrets stay
  references), never opens connections or starts services. Publish adds a
  real `FlowParser.parse` on top (publish validation ≠ static validation).
- **No `admin_*` prefix** for normal user authoring; permissions are
  enforced server-side by scope (global writes require the admin role).

## `FlowAuthoringService` (`core/flow_authoring.py`)

```text
Web UI ------\
Agent tools --+--> FlowAuthoringService --> ScopedRepository
CLI ---------/
```

| Method | Purpose |
| ------ | ------- |
| `load(fqn, scope)` / `versions(fqn, scope)` | read a published version (latest when unversioned) / list versions |
| `delete_version(fqn, scope, user_id)` | delete one published version (`package.name:version`); refuses the last one, re-points `latest` when the latest goes |
| `new(package, name, version, scope, user_id)` | draft of a flow that does not exist yet (nothing published until `publish`) |
| `fork(source_fqn, source_scope, package, name, version, scope, user_id)` | copy a read-only flow (global/package) into a draft the user owns (`forked_from` recorded) |
| `create_draft(fqn, scope, user_id, reuse_existing=True)` | open a draft of a published version; an existing draft of the same flow/scope/conversation is reused (`reused: true`) |
| `load_draft` / `list_drafts` / `discard_draft` | draft lifecycle |
| `save_draft(draft_id, user_id, definition, base_revision)` | store the definition verbatim; raises `DraftConflict` on a stale revision |
| `validate(definition)` / `validate_draft` | static validation report |
| `diff(base, definition)` / `diff_draft` | structured changes (see below) |
| `publish(draft_id, user_id, version)` | static validation + full parse, then a NEW immutable version; the draft is discarded (`keep_draft=True` to keep it) |
| `task_catalog()` / `task_schema(type, parameters)` | palette entries (`TaskFactory` + `TASK_CATEGORIES`) and the schema for the **current** parameters (schemas may depend on the configuration) |
| `service_catalog()` / `service_schema(type)` | service types and schemas (same helpers as the service dialogs) |

### Validation report

```json
{"ok": false, "errors": 2, "warnings": 1, "problems": [
  {"severity": "error", "code": "missing_required_parameter",
   "message": "Required parameter 'service' is missing",
   "entity_type": "task", "entity_id": "infer_ai", "field": "service"},
  {"severity": "error", "code": "unknown_relation_target",
   "message": "Relation points to unknown task 'format_mail2'",
   "entity_type": "relation", "entity_id": "conn_send_email__success__format_mail2", "field": "to"},
  {"severity": "warning", "code": "task_disconnected",
   "message": "Task 'transform' has no incoming or outgoing relation",
   "entity_type": "task", "entity_id": "transform", "field": ""}
]}
```

Codes: `invalid_definition`, `invalid_field`, `missing_flow_name`,
`duplicate_task_id` (task ids are unique flow-wide, groups included),
`invalid_task`, `missing_task_type`, `unknown_task_type`,
`invalid_parameters`, `missing_required_parameter`, `unknown_service_ref`,
`invalid_service`, `missing_service_type`, `unknown_service_type`,
`invalid_relation`, `unknown_relation_source`, `unknown_relation_target`,
`duplicate_relation`, `unknown_entry`, `unknown_exit`, `task_disconnected`,
and `parse_error` (publish only).

Relations are identified exactly like runtime queues:
`connection_id = conn_<source>__<relationship>__<target>` —
`A --success--> B` and `A --failure--> B` are two different connections.

### Diff

```json
{"count": 4, "runtime_impact": true, "changes": [
  {"op": "added",   "kind": "task",     "id": "validate_output", "runtime_impact": true},
  {"op": "changed", "kind": "task",     "id": "infer_ai", "fields": ["parameters.model"], "runtime_impact": true},
  {"op": "removed", "kind": "relation", "id": "conn_fetch__success__parse", "runtime_impact": true},
  {"op": "changed", "kind": "layout",   "id": "layout", "runtime_impact": false}
]}
```

Layout-only and name/description changes carry `runtime_impact: false`:
they never require a hot-swap of a running instance.

## Actions (`tasks/ai/actions/flow_editor.py`)

All actions require a session user. `scope` accepts `global`, `user`,
`conversation`/`conv` (+ `conversation_id`). Global writes (`new`, `fork`,
`create_draft`, `publish`, `delete_version`) require the admin role.

| Action | Body | Answer |
| ------ | ---- | ------ |
| `flow_editor_get` | `fqn`, `scope` | `{flow, scope}` |
| `flow_editor_versions` | `fqn`, `scope` | `{flow, scope, versions, latest}` |
| `flow_editor_delete_version` | `fqn` (with version), `scope` | `{ok, fqn, flow, version, scope, latest, versions}`; **400** for the last remaining version |
| `flow_editor_new` | `package`, `name`, `version`, `scope`, `description` | `{draft}` |
| `flow_editor_fork` | `source_fqn`, `source_scope`, `package`, `name`, `version`, `scope` | `{draft}` |
| `flow_editor_create_draft` | `fqn`, `scope`, `reuse_existing` | `{draft}` (`draft.reused`) |
| `flow_editor_load_draft` / `flow_editor_list_drafts` | `draft_id` / — | `{draft}` / `{drafts}` |
| `flow_editor_save_draft` | `draft_id`, `definition`, `base_revision` | `{ok, revision}` or **409** `{error: "draft_changed_elsewhere", current_revision}` |
| `flow_editor_discard_draft` | `draft_id` | `{ok}` |
| `flow_editor_validate` | `definition` | validation report |
| `flow_editor_diff` | `draft_id` | diff vs `base_version` |
| `flow_editor_publish` | `draft_id`, `version`, `keep_draft` | `{ok, fqn, version, ...}` or **422** `{error: "validation_failed", validation}` |
| `flow_editor_task_catalog` / `flow_editor_task_schema` | — / `task_type`, `parameters` | `{tasks}` / `{type, schema}` |
| `flow_editor_service_catalog` / `flow_editor_service_schema` | — / `service_type` | `{services}` / `{type, schema}` |

## Removed legacy scaffold

`tasks/io/admin_editor_actions.py` (`admin_list_task_types`,
`admin_get_task_schema`, `admin_list_service_types`,
`admin_get_service_schema`, `admin_save_flow_json`, `admin_validate_flow`,
`admin_auto_layout`) and the `adminAction` task (`POST /admin/api`,
`admin_*_flow` / template handlers) were removed: nothing in the product
called them, `admin_save_flow_json` was a second file-based persistence
system next to `ScopedRepository`, validation belongs to
`FlowDefinitionValidator`, and auto-layout belongs to the canvas
(ReactFlow + dagre). Deployment/runtime operations use `deploy_flow`,
`start_flow`, `stop_flow`, `get_flow_instance` and `flow_runtime_*`.

## Canvas edit mode (Phase 2)

`flow_graph.html?draft_id=<d_id>` (or `window.__PAWFLOW_FLOW_DRAFT_ID`; the
repository menu entry **Edit (draft)** calls `flow_editor_create_draft` then
opens the tab) switches the same canvas into edit mode:

- Autosave (800 ms after an edit, undo and redo included) only ever writes
  the draft file; no published version is created or modified until
  **Publish**. **Discard draft** (status bar) deletes the working copy through
  `flow_editor_discard_draft` and locks the canvas. The repository dialogs
  (Versions, Diff, New, Fork) always close through ✕, Escape or Close, and the
  Versions/Diff dialogs close themselves when they open the graph or the
  editor.
- `draftRef` holds the definition; `flowToReactFlow()` projects it into
  nodes/edges (edge id = `connection_id`). Interactions patch the document
  (`patchLayoutNode`, `removeFromDraft` — removing a task atomically drops
  its relations, entries, exits, layout and `flow_ref` group), never the
  reverse.
- Positions come from `flow.layout.nodes`; dagre only places nodes without
  a stored position and is otherwise the explicit **Auto Layout** button.
- Undo/redo (Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y) over whole-document history;
  a drag records one entry on drop.
- Autosave 800 ms after the last change through `flow_editor_save_draft`
  with `base_revision`; a `409 draft_changed_elsewhere` locks the canvas
  and offers **Reload**.
- **Validate** opens the Problems drawer (click selects the entity);
  **Publish** flushes the autosave, shows the diff count, asks the version
  and publishes (a 422 report lands in the Problems drawer). Runtime
  polling is off; subflow drill-downs stay read-only.

## Task palette and properties (Phase 3)

Edit mode loads `flow_editor_task_catalog` into a searchable, category-grouped
processor palette. Dropping a processor uses ReactFlow's
`screenToFlowPosition()` so placement remains correct at every zoom/pan level,
creates a deterministic technical id (`log`, `log_2`, …), stores the position
in `flow.layout.nodes`, and opens the Properties drawer immediately. The
technical id is stable; the optional human label is edited separately.

The drawer requests `flow_editor_task_schema` with the task's **current**
parameters because some schemas depend on the selected configuration. It uses
the same `schema_form.js` renderer as service dialogs, including existing
service selectors. Saving merges rendered values into the existing parameter
map and preserves unknown/future parameters and every unrelated task field.
Double-click and the edit-mode context menu open this same drawer; runtime
Start/Stop/Restart commands never appear in edit mode.

## Relation wiring (Phase 4)

In edit mode, ReactFlow handles are connectable. Drawing a connection does not
write an edge immediately: it opens one connection drawer whose relationship
choices come from the source task's `get_output_relationships()` using its
current parameters. Existing edges open the same drawer. Saving, changing the
relationship or deleting the edge is one undoable document operation keyed by
the stable runtime identity
`conn_<source>__<relationship>__<target>`; a duplicate identity is refused.

`Task.get_output_relationships()` defaults to `success`, honors legacy
`RELATIONSHIPS` / `OUTPUTS` declarations and can be overridden for dynamic
routes (`routeOnAttribute` derives its named routes plus the configured default
relationship). `flow_editor_task_schema` returns both `schema` and
`relationships` for the current configuration.

A relation can carry queue settings that are validated statically and consumed
by `ConnectionManager` at runtime:

```json
{"from": "fetch", "to": "parse", "type": "success",
 "max_queue_size": 10000, "max_queue_bytes": 104857600,
 "flowfile_ttl_seconds": 0, "prioritizer": "priority_attribute",
 "priority_attribute": "priority"}
```

Supported prioritizers are `fifo`, `oldest_first`, `newest_first` and
`priority_attribute`. Count and byte thresholds must be positive integers;
TTL is a non-negative integer (`0` disables expiration).

## Flow resources (Phase 5)

The edit toolbar exposes the remaining top-level resources without introducing
a second model:

- **Metadata** edits `id`, `name`, `version` and `description` in place while
  preserving all repository and future fields.
- **Parameters** is a key + JSON-value editor. Both simple values and typed
  parameter definitions round-trip exactly.
- **Services** creates, edits and deletes `flow.services` entries. The drawer
  uses `flow_editor_service_catalog`, requests the schema using the service's
  current parameters, and renders it through the shared `schema_form.js`.
  Embedded flow services are merged into processor `service_ref` selectors
  ahead of user/global services. Unknown service fields and parameters survive
  edits. Required embedded-service parameters are checked by the shared static
  validator without resolving expressions or connecting the service.
- **Ports** selects explicit `entries` and `exits` from root tasks and subflow
  endpoints. Removing a task still removes stale entry/exit references in the
  same atomic operation.
- **`${…}`** lists flow parameter references and the conversation/user/global
  scopes as copyable runtime expressions. Expressions remain raw references in
  drafts, validation and read-only configuration views.

Every drawer Save is one undoable document operation and follows the same
debounced, revision-locked autosave path as tasks and relations. **Auto Layout**
persists `layout.nodes`; **Validate** refreshes the structured Problems drawer,
and **Problems** reopens its latest report. Clicking a task/relation problem
selects the corresponding canvas entity.

## Repository integration (Phase 6)

The Flows Repository `+` button creates a new private draft instead of opening
the deployment dialog. Its form selects package, technical name, initial
version, description and target scope; successful creation opens that draft in
the same canvas. The template context menu provides:

- **Edit (draft)** for writable scopes (an existing draft is reused);
- **Fork** to copy any readable immutable version into a newly named flow and
  target scope;
- **Versions** to list the immutable version family, identify `latest`, view
  any version, or open a writable version as a draft;
- **Diff** to show structured base-version/draft changes and jump into the
  reused draft;
- **Publish** in the editor toolbar, after autosave, diff summary and static +
  parser validation; a new version is always created;
- the existing **Deploy** action, so a published V1 immediately returns to the
  normal parameter/service-override deployment loop.

The UI normalizes `global`, `user` and `conversation` scopes and carries the
conversation id when either a source or target needs it. Read-only scopes hide
Edit/Diff but remain viewable, versionable and forkable. These affordances are
only convenience: the action handler enforces authentication, private draft
ownership, conversation scope requirements and admin-only global writes.

## Process groups (Phase 7)

Inline Process Groups are part of the canonical flow document, not a second
runtime model. **Group selection** moves selected root tasks and the relations
whose two endpoints are selected into one group without changing technical
task ids. Group metadata, variables, typed input/output ports and deletion are
edited as single undoable document operations. Deleting a group also removes
its root relations, entries, exits and layout references atomically.

`FlowParser` recursively flattens inline groups into the runtime DAG. Group
variables are inherited by nested groups, each materialized task keeps its
`group_id` provenance, internal relations join the normal connection graph,
and duplicate task ids across root and nested scopes fail fast. The shared
static validator walks the same nested structure, including tasks, relations,
ports and both canonical dictionary and tolerated historical list forms of
`child_groups`.

ReactFlow remains a projection of that one document. A group appears as one
node at its parent level; double-click opens its contents on the same canvas,
and the breadcrumb / up control returns to the parent. Drill-down is read-only
for topology so boundary connections cannot accidentally be rewritten from an
aggregated edge. Their data retains `originalSource` and `originalTarget` for
inspection and stable `connection_id` lookup.

## Versioned subflows (Phase 8)

A Process Group with `flow_ref` is rendered as a subflow node and is edited by
the Subflow drawer. The reference pins both an explicit JSON `path` and
`version`; the parser refuses a version mismatch. The drawer also round-trips
`parameter_mapping`, input/output `port_mapping` and `pass_attributes` while
preserving unknown fields.

At runtime the parser synthesizes the existing `executeFlow` processor for the
reference, validates mapped ports against the child definition and retains the
existing recursion guard. Subflows and inline groups share the normal canvas,
repository draft, validation, diff, autosave, undo/redo and publish paths.

## Workflow agent authoring

The New Flow dialog includes an **Agent workflow** starter. It creates one
`kind: "agent_workflow"` document with an `agent_contract`, exact input and
terminal ports, safe request/response stages, bounded preemption policies, and
normal editable layout. There is no second workflow editor.

The contract editor defines typed parameters, service references, allowed
effects, and supported preemption policies. Draft **Validate** and **Publish**
both call the shared server validator. Publication fails when ports disagree
with their `inputPort`/`outputPort` tasks, a task cannot reach the terminal,
a cycle is unbounded, or a task falls outside the closed workflow-safe catalog.

Conversation agents bind an immutable published FQN such as
`pawflow.agents.wiki:1.0.0`. Their configuration dialog shows that exact
identity and exposes **Upgrade workflow** only when another compatible version
is visible. Upgrading validates and stores a new binding; it never mutates an
active run or silently follows `latest`.

The workflow badge opens the redacted run inspector. Operators can view stages,
status, generation, aggregate usage, terminal state, and authorization decisions
without seeing prompts, source bodies, requests, credentials, or service
snapshots. **Retry** appears only for the current recoverable generation, and a
lost durable acquisition race returns a conflict rather than claiming success.

## Safe runtime editing (Phase 9)

The context menu of a running, repository-backed instance exposes **Edit
running flow** without replacing **Edit params**. It creates or reuses a private
draft of the instance's exact deployed FQN and opens that draft in the same
canvas with both `draft_id` and `instance_id`. Legacy file-backed deployments
remain operational but do not expose runtime topology editing.

Publishing from an instance-linked draft creates an immutable repository
version and keeps the draft long enough to complete the application workflow.
The canvas must call `flow_runtime_update_preview` before Apply. The preview
contains the structured definition diff, removed queues and their current
FlowFile count/bytes, tasks in flight, executor version and an anti-TOCTOU
`preview_token`.

Apply calls `flow_runtime_update_apply` with the exact published FQN and preview
token. A non-empty removed queue requires the explicit `drop` policy; tasks in
flight require the explicit `wait` policy. Any runtime/candidate change after
preview returns HTTP 409 with a fresh impact instead of applying stale consent.
The executor stops scheduling, waits up to ten seconds when requested, rebuilds
the flow, and restores surviving queues and pause state strictly by
`connection_id` — never by `(source, target)`, so two relationships between the
same tasks cannot merge. Crash recovery from checkpoints follows the same rule:
each saved queue carries its relationship and is restored only into that exact
connection. After success the deployment registry persists the new FQN, flow
metadata and layout before checkpoint/provenance recording, ensuring restart
uses the applied immutable version.

The preflight (non-empty removed queues, tasks in flight) runs again once the
scheduler is stopped, because FlowFiles can arrive and tasks can start between
the first check and the stop. A violation under a `reject` policy, or a `wait`
that times out, resumes the scheduler thread in place — the worker pools, tasks
and services are untouched, so nothing leaks and no checkpoint is replayed —
and the apply answers HTTP 409 so the canvas re-previews. If the rebuild itself
fails, the executor stays stopped and raises `FlowUpdateError`; the API answers
HTTP 500 `runtime_update_failed` with `executor_running: false` and persists
nothing, instead of disguising the failure as a stale preview.

Runtime actions are owner-scoped (admin-only for global instances):

| Action | Body | Answer |
| ------ | ---- | ------ |
| `flow_runtime_create_draft` | `instance_id` | `{draft, instance_id}` |
| `flow_runtime_update_preview` | `instance_id`, published `fqn` | diff + live impact + `preview_token` |
| `flow_runtime_update_apply` | preview fields + explicit risk policies when required | `{ok, updated, fqn, impact}`, HTTP 409 with fresh impact, or HTTP 500 `runtime_update_failed` when the rebuild itself failed |

## Declarative authoring, views, and proposals

When `PAWFLOW_DECLARATIVE_WORKFLOWS_ENABLED` is enabled, the editor can lower
supported semantic blocks into the same technical `FlowDefinition` shown by
the graph. Declarative and technical views never maintain separate executable
graphs: edits round-trip through one draft revision and unsupported structures
fall back to a Custom Group instead of being dropped.

With `PAWFLOW_MULTI_VIEW_LAYOUTS_ENABLED`, named layouts, geometry, routing,
frames, annotations, and viewport state are versioned presentation metadata.
Layout-only changes do not rebuild a running executor.

The template and runtime viewers project the selected layout as well as the
executable graph. Task `label` and `description` fields are shown on processor
cards, while layout `frames` render non-executable functional sections with a
title and description behind their member nodes. Stored node positions win over
automatic Dagre placement; frames never become tasks, queue endpoints, or
runtime controls.

Planner output is saved as a `WorkflowProposal` tied to the exact draft
revision and definition digest. User edits invalidate stale planner review;
acceptance and approval fail on revision drift. Approval publishes the
immutable flow and starts a durable one-shot run. Current proposal and run
UiSurfaces are rehydrated from server state after reconnect.

# Flow Editor

The Flow Editor turns the existing flow viewer (`flow_graph.html`,
ReactFlow + dagre) into a NiFi-style graphical editor. There is ONE canvas
with three modes — `view` (templates / subflows), `runtime` (the
[Flow Runtime Console](flow_runtime_console.md)) and `edit` — never three
divergent implementations.

This document covers the **authoring foundation** (Phase 0/1). The canvas
edit mode, task palette, properties drawer, relations wiring, process
groups, subflows and runtime editing are the following phases.

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
`create_draft`, `publish`) require the admin role.

| Action | Body | Answer |
| ------ | ---- | ------ |
| `flow_editor_get` | `fqn`, `scope` | `{flow, scope}` |
| `flow_editor_versions` | `fqn`, `scope` | `{flow, scope, versions, latest}` |
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

## Roadmap (next phases)

1. canvas `edit` mode (`flowDraft` projection, drag/drop, selection,
   undo/redo with one entry per drag, autosave, `flow.layout`);
2. task palette + properties drawer (`schema_form.js` extracted from
   `resources_service_dialogs.js`), deterministic task ids, stable technical
   id + human label;
3. relations wiring with a relationship chooser
   (`Task.get_output_relationships`), per-relation queue configuration;
4. flow parameters, services, `${...}` assistance, Problems panel;
5. repository UI (New Flow, Edit, Fork, Versions, Diff, Publish);
6. inline process groups (runtime flatten first) and subflows;
7. runtime editing (`update_flow()` keyed by `connection_id`, runtime impact
   before Apply).

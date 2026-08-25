# Declarative Workflows and Multi-View Flow Authoring — Complete Implementation Plan

Status: **implemented and validated behind disabled-by-default feature flags**

Date: 2026-08-25

Primary outcome: PawFlow has one canonical executable <code>FlowDefinition</code> and
one authoring canvas with technical, declarative, read-only, and runtime
projections. A user can build a complete workflow using semantic blocks such as
steps, conditions, questions, confirmations, notifications, waits, branches,
parallel work, joins, retries, bounded loops, subflows, and Workflow Agents.
Those blocks are stored and executed as ordinary PawFlow tasks, groups,
relations, ports, and parameters. There is no PlanRunner and no second
executable plan format.

The same flow definition also owns one or more named visual layouts. Each layout
persists node positions and sizes, viewport, collapsed state, editable relation
routing, annotations, visual frames, and per-element styles. Edit, view, and
runtime modes project the selected stored layout instead of independently
reconstructing positions.

## 1. Decision

PawFlow will implement declarative authoring as a complete alternative view of
normal workflows, not as a separate engine and not as a task that interprets a
nested plan document.

The product may expose a palette item named **Declarative Flow**. Internally it
creates an inline Process Group whose preferred editor view is declarative. The
group contains canonical tasks and relations. The parser continues to flatten
inline groups into the ordinary runtime graph.

The architectural decisions are:

- <code>FlowDefinition</code> remains the only executable format.
- The declarative editor sends versioned semantic edit operations to the server;
  the browser is not an independent compiler.
- Atomic declarative blocks map one-to-one to normal PawFlow tasks.
- Composite blocks are canonical inline groups containing normal tasks,
  relations, and typed ports.
- Every normal task remains reachable through a generic Processor block, so the
  declarative view can represent the entire PawFlow task catalog.
- A workflow proposal is approval metadata referencing an exact draft revision
  and, after approval, an immutable flow version. It is not executable by
  itself.
- Durable one-shot execution uses the existing continuous flow engine plus a
  persisted run lifecycle. It does not introduce PlanRunner.
- A Workflow Agent invoked from a normal flow runs through
  <code>WorkflowAgentRuntime</code> and <code>WorkflowRunStore</code>; embedding
  its underlying flow as <code>executeFlow</code> is not equivalent.
- Layouts are versioned presentation data inside the flow definition and have no
  runtime effect.
- Published flow versions remain immutable. Editing topology or presentation
  always occurs through a draft.
- Legacy plans and legacy single-layout definitions are converted through
  explicit one-shot migrations. There is no permanent dual writer.

## 2. Relationship to existing sources of truth

This plan extends, and must remain consistent with:

- <code>flow_editor.md</code>, which owns the current single-canvas draft,
  validation, versioning, Process Group, subflow, and hot-swap behavior;
- <code>WORKFLOW_AGENTS_IMPLEMENTATION_PLAN.md</code>, which owns dedicated
  Workflow Agent binding, inbox, run, recovery, limits, and terminal commit;
- <code>AGENT_COLLABORATION_AND_TOOL_SAFETY_PLAN.md</code>, which owns shared
  turn identity, authorization, effects, lifecycle events, run projection, and
  immutable resource references;
- <code>architecture.md</code>, which owns the FlowFile, task, relation,
  executor, checkpoint, and deployment model;
- <code>POLICY_GATING.md</code> and <code>security_model.md</code>, which own
  authorization and approval boundaries.

When contracts overlap:

- workflow-agent security and terminal guarantees remain authoritative in the
  Workflow Agents plan;
- generic tool and agent invocation authorization remains authoritative in the
  Collaboration and Tool Safety plan;
- this plan owns declarative projection, semantic editing, multi-view layout,
  visual metadata, workflow proposals, flow-native user interaction blocks, and
  durable one-shot flow composition.

## 3. Current PawFlow foundation

The implementation must extend proven seams instead of replacing them.

### 3.1 Flow authoring

<code>FlowAuthoringService</code> already provides:

- private drafts;
- optimistic revisions;
- immutable publication;
- static validation followed by full parse validation;
- structural diffs;
- task and service catalogs;
- exact repository scopes and versions.

The full JSON definition is already preserved verbatim. Unknown fields survive
load, save, and publication. This is the correct storage boundary for
declarative and visual metadata.

### 3.2 Canvas

The existing ReactFlow canvas already provides:

- view, edit, and runtime modes;
- node dragging;
- stored node positions in <code>flow.layout.nodes</code>;
- explicit dagre auto-layout;
- task and relation drawers;
- undo and redo;
- Process Group drill-down;
- subflow drill-down;
- runtime overlays.

It currently has one layout, fixed smooth-step relations, no persisted edge
geometry, no annotations, no presentation-only frames, and no per-element style
schema.

### 3.3 Flow engine

<code>ContinuousFlowExecutor</code> already provides queues, relationship
routing, fan-out, joins, backpressure, checkpointing, restart recovery, runtime
context injection, output collection, and idle auto-stop.

Batch execution is not sufficient for workflows that may wait for user input,
a timer, an event, or a child agent. Those workflows require a deployed
continuous instance or a durable one-shot instance backed by the same executor.

### 3.4 Durable interaction

<code>ConfirmationStore</code> and the tasks
<code>requestConfirmation</code>, <code>durableWait</code>, and
<code>durableNotify</code> already prove the durable park-and-reinject pattern.

Missing capabilities are:

- free-text, number, date, file, and structured form answers;
- one flow-native user interaction contract shared by questions and
  confirmations;
- a flow-native <code>notifyUser</code> task;
- a first-class durable timer;
- consistent declarative outputs and failure relationships.

### 3.5 Workflow Agents

<code>WorkflowAgentRuntime.submit_bound</code> already accepts exact server-owned
bindings for automation and maintenance. It owns queueing, preemption,
authorization, limits, durable runs, and one terminal result.

Missing capabilities are:

- a flow invocation mode;
- a parent-flow continuation sink;
- a task-facing invocation contract;
- result projection back into the parent FlowFile;
- parent and child cancellation propagation;
- recursive invocation guards.

### 3.6 Legacy plans

The current plan subsystem stores a linear list of descriptions and statuses in
<code>PlanStore</code>. Handlers and <code>orchestrate_next_step</code> wake
agents, force-stop completed steps, and schedule verifiers.

It duplicates flow orchestration and cannot natively express arbitrary routes,
parallel branches, joins, durable interaction, ordinary processors, subflows,
or runtime provenance. It is the migration source, not the target architecture.

## 4. Goals

### 4.1 Product goals

1. A non-technical user can author a complete PawFlow workflow without seeing
   FlowFile plumbing.
2. Every normal PawFlow task is available in declarative authoring.
3. Common patterns are concise semantic blocks rather than manually wired
   processor sequences.
4. A user can switch between declarative and technical views without converting
   or duplicating executable data.
5. A user can invoke a Workflow Agent from a normal flow as naturally as a
   subflow while preserving Workflow Agent runtime guarantees.
6. A workflow can ask a question, request confirmation, notify the user, wait
   durably, branch on the answer, and resume after restart.
7. A workflow proposal supports a durable planner/user co-editing loop: it can
   be reviewed graphically, edited in the normal Flow Editor, returned to the
   planner for exact-revision review any number of times, accepted or cancelled,
   executed once, inspected, and replayed.
8. A flow can contain multiple named visual layouts for different audiences or
   purposes.
9. Manual node, edge, annotation, frame, and style edits persist in drafts and
   immutable published versions.
10. Runtime rendering uses the selected stored layout and overlays live state
    without changing that layout.
11. Every work block visibly and explicitly selects the LLM, agent, Workflow
    Agent, human role, or PawFlow engine that executes it; a block may expose
    several named executor roles when its canonical subgraph contains several
    execution stages.

### 4.2 Engineering goals

- One executable source of truth.
- One server-authoritative semantic lowering implementation.
- Stable identifiers for tasks, semantic blocks, relations, layouts,
  annotations, and visual frames.
- Optimistic concurrency for every authoring mutation.
- Layout-only changes marked as no runtime impact.
- Bounded loops, fan-out, waits, costs, and nested invocation depth.
- Durable asynchronous boundaries with idempotent continuation delivery.
- No blocked HTTP worker and no task thread polling a child run.
- No authorization derived from mutable FlowFile attributes.
- Focused unit, integration, recovery, migration, UI, and accessibility tests.
- New Python and JavaScript modules kept below the project size target; do not
  add another large implementation block to <code>flow_graph.html</code>.

## 5. Non-goals

This work does not:

- create a general-purpose textual programming language;
- store a declarative DSL beside the flow JSON;
- permit unbounded graph cycles;
- make presentation metadata influence execution;
- let a browser decide authorization or compile trusted runtime structures;
- make a draft executable without freezing the exact reviewed revision;
- silently upgrade Workflow Agent or subflow references;
- silently choose an LLM or agent when a work block has no valid executor
  binding;
- make runtime node movement mutate an immutable published flow;
- replace Process Groups, subflows, or the normal task catalog;
- allow arbitrary HTML or executable scripts in annotations;
- preserve the old PlanStore runtime indefinitely.

## 6. Mandatory invariants

### 6.1 Canonical execution

For every declarative block, the executable behavior is fully represented by
normal <code>tasks</code>, <code>groups</code>, <code>relations</code>,
<code>entries</code>, <code>exits</code>, parameters, and services.

Deleting all presentation fields from a published definition changes only how
the flow is displayed. It must not change what the executor runs.

### 6.2 Canonical presentation

Published definitions own their named layouts. View and runtime clients load
those layouts from the exact flow version used by the instance.

The current mutable <code>DeployedInstance.layout</code> copy must stop acting as
a competing source of truth. An instance-specific override, if retained, must
be explicitly named <code>layout_override</code>, reference a base layout ID,
and never be confused with the published layout.

### 6.3 No silent regeneration

Opening, closing, switching views, or loading a runtime must never run
auto-layout or regenerate macros automatically.

Auto-layout and macro replacement are explicit undoable edits. A no-op
load-save round trip preserves the document byte-for-byte apart from repository
metadata intentionally rewritten by publication.

### 6.4 Stable identity

- Task IDs are stable and unique flow-wide.
- Inline group IDs are stable.
- Every relation has a stable <code>relation_id</code>.
- Runtime queue identity remains derived from source, relationship, and target.
- Changing a relation route or label preserves <code>relation_id</code>.
- Changing its source, target, or runtime relationship creates runtime impact
  but may preserve <code>relation_id</code> as an authoring object.
- Layouts, annotations, and frames have stable IDs.
- Generated macro internals use deterministic IDs derived from the semantic
  block ID and role, with collision checks.

### 6.5 Runtime overlays do not overwrite style

User colors are the base presentation. Runtime states add badges, outlines,
animation, or overlays with accessible contrast. Running, failed, paused, and
backpressured states must remain distinguishable regardless of the chosen base
color.

## 7. Product terminology

| Term | Meaning |
|---|---|
| Workflow | Any canonical PawFlow <code>FlowDefinition</code> |
| Declarative view | Semantic projection and editor over that definition |
| Technical view | Processor, port, parameter, and relation projection |
| Runtime view | Stored layout plus live execution overlays |
| Declarative Flow | Palette affordance creating a declarative inline Process Group |
| Atomic block | One semantic block backed by one task |
| Composite block | One semantic block backed by an inline group |
| Visual frame | Presentation-only rectangle grouping elements visually |
| Process Group | Runtime-significant inline or referenced flow container |
| Workflow Proposal | Durable planner/user review record pinned to exact draft revisions and digests |
| Flow Run | One durable one-shot execution of an immutable flow version |
| Plan view | Optional user-facing label for a simplified declarative projection; not a data type |

## 8. Canonical multi-view layout model

### 8.1 Top-level schema

Replace the legacy singular <code>layout</code> field with a versioned
<code>layouts</code> document.

~~~json
{
  "layout_schema_version": 1,
  "default_layout_id": "technical",
  "layouts": {
    "technical": {
      "id": "technical",
      "name": "Technical",
      "kind": "technical",
      "root_group_id": "",
      "viewport": {"x": 0, "y": 0, "zoom": 1},
      "direction": "LR",
      "nodes": {},
      "relations": {},
      "annotations": {},
      "frames": {},
      "visibility": {},
      "created_at": "UTC timestamp",
      "updated_at": "UTC timestamp"
    },
    "business": {
      "id": "business",
      "name": "Business overview",
      "kind": "declarative",
      "root_group_id": "",
      "viewport": {"x": 40, "y": 20, "zoom": 0.85},
      "direction": "TB",
      "nodes": {},
      "relations": {},
      "annotations": {},
      "frames": {},
      "visibility": {}
    }
  }
}
~~~

The schema is presentation-only and static validation never opens services or
resolves expressions.

### 8.2 Layout views

A flow may contain any number of named views. Initial kinds are:

- <code>technical</code>: complete processor topology;
- <code>declarative</code>: semantic blocks and collapsed macro groups;
- <code>operations</code>: runtime-oriented topology and annotations;
- <code>custom</code>: user-defined selection, grouping, and positioning.

A view may select one root Process Group and may store collapsed or hidden
presentation state. Hiding an element affects only that view. The editor must
show a warning when a custom view omits executable nodes so that omission is
never mistaken for deletion.

Users can create, rename, duplicate, reorder, and delete views. At least one view
is required. Deleting the default requires selecting another default in the same
atomic edit.

### 8.3 Node geometry

Each view stores:

~~~json
{
  "nodes": {
    "review_output": {
      "x": 720,
      "y": 180,
      "width": 220,
      "height": 96,
      "rotation": 0,
      "locked": false,
      "collapsed": false,
      "z_index": 20,
      "style": {
        "fill": "#172033",
        "border": "#5b8cff",
        "text": "#f2f5ff",
        "accent": "#8db2ff",
        "border_width": 2,
        "border_style": "solid",
        "opacity": 1
      }
    }
  }
}
~~~

Position and size are stored in flow-canvas coordinates. Dragging, resizing,
aligning, distributing, or moving a selected set is one undoable draft
operation. Rounded finite values are persisted. Invalid numbers, excessive
sizes, and unsupported style keys fail static validation.

### 8.4 Stable relation identity and routing

Every canonical relation gains a required stable authoring ID:

~~~json
{
  "relation_id": "rel_review_approved",
  "from": "review",
  "to": "publish",
  "type": "approved"
}
~~~

The runtime <code>connection_id</code> remains the queue identity derived from
<code>from</code>, <code>type</code>, and <code>to</code>. The layout references
<code>relation_id</code>, not the derived queue identity.

Each view may store:

~~~json
{
  "relations": {
    "rel_review_approved": {
      "routing": "bezier",
      "source_handle": "right",
      "target_handle": "left",
      "source_control": {"dx": 110, "dy": 0},
      "target_control": {"dx": -90, "dy": 30},
      "label_t": 0.52,
      "label_offset": {"x": 0, "y": -12},
      "z_index": 10,
      "style": {
        "stroke": "#42c97a",
        "stroke_width": 2,
        "stroke_style": "solid",
        "animated": false,
        "arrow": "closed"
      }
    }
  }
}
~~~

Control points are stored as endpoint-relative deltas so moving either node
preserves the intended curve. Supported routing modes are
<code>bezier</code>, <code>smoothstep</code>, <code>straight</code>, and
<code>auto</code>.

The editor supplies draggable source and target control handles, reconnectable
endpoints, selectable port handles, a movable label, reset-route, reverse-route
when semantically valid, and route-style controls. Reconnecting an edge is one
atomic topology plus layout edit.

### 8.5 Annotations

Annotations are presentation-only objects:

~~~json
{
  "annotations": {
    "ann_security": {
      "id": "ann_security",
      "type": "markdown",
      "x": 400,
      "y": 40,
      "width": 320,
      "height": 140,
      "title": "Security boundary",
      "content": "Every effect is re-authorized here.",
      "locked": false,
      "z_index": 40,
      "style": {
        "fill": "#2b2114",
        "border": "#d69e3a",
        "text": "#fff3d6"
      }
    }
  }
}
~~~

Initial annotation types are plain text, sanitized Markdown note, callout, and
basic shape. Raw HTML, scripts, external embeds, event handlers, and remote CSS
are forbidden.

Annotations can be moved, resized, colored, duplicated, locked, brought forward,
sent backward, and copied between views. They never become tasks and never
appear in parser output.

### 8.6 Visual frames

A visual frame is distinct from a Process Group:

~~~json
{
  "frames": {
    "frame_review": {
      "id": "frame_review",
      "title": "Human review",
      "description": "Approval and correction path",
      "x": 620,
      "y": 100,
      "width": 620,
      "height": 380,
      "member_ids": ["review", "ask_changes", "publish"],
      "move_members": true,
      "locked": false,
      "z_index": 1,
      "style": {
        "fill": "#18241c",
        "border": "#4ba96b",
        "text": "#dff7e6",
        "opacity": 0.42
      }
    }
  }
}
~~~

Frames render behind nodes. Membership is visual and optional; it does not
change task ownership, scope, ports, flattening, or runtime behavior. Moving a
frame with <code>move_members</code> enabled applies one delta to its members in
the same undoable edit.

The UI must use distinct terminology and icons for **Visual Frame** and
**Process Group**.

### 8.7 Auto-layout

Auto-layout is explicit and previewable. It supports:

- current view or current Process Group;
- all visible nodes or current selection;
- left-to-right and top-to-bottom direction;
- configurable rank, node, and group spacing;
- compound layout around Process Groups and visual frames;
- port-aware relation routing;
- locked nodes and frames;
- keep-manual-routes or reroute-selected-edges;
- apply or cancel after preview.

Use an algorithm capable of compound graphs and ports for the final
implementation. Dagre may remain as the initial fallback, but ELK-style
compound layout is required before general availability.

Opening a flow only auto-places nodes missing geometry and does not persist that
fallback. The user must explicitly choose **Save generated positions** or run
Auto Layout.

### 8.8 View, edit, and runtime behavior

- View mode loads the selected layout from the exact published version.
- Edit mode mutates the selected layout inside the draft using optimistic
  locking and whole-document undo/redo.
- Runtime mode loads the layout from the exact version pinned by the deployed
  instance and adds live state.
- A layout-only published version has <code>runtime_impact: false</code>.
- Applying a layout-only version to a live instance updates the instance's exact
  FQN and display projection without rebuilding tasks, services, or queues.
- Pan and zoom are transient until the user explicitly saves the viewport.
- A per-user last-selected view is UI preference state, not flow state.
- Runtime status must never be written back into layouts.

### 8.9 Layout migration

The one-shot migration performs:

1. Convert <code>layout.nodes</code> to
   <code>layouts.technical.nodes</code>.
2. Generate deterministic <code>relation_id</code> values from the legacy
   connection identity, adding a collision suffix when required.
3. Create default automatic relation layout records only when legacy custom
   data exists; otherwise absence means <code>routing: auto</code>.
4. Copy legacy deployment layout only when it differs from the exact published
   flow layout, storing it as an explicit instance override.
5. Mark the migration version in each definition or repository migration
   manifest.
6. Validate every migrated version before activation.
7. Remove legacy singular layout reads and writes after the migration gate.
8. Never rewrite an immutable published file in place outside the explicit
   repository migration command and its backup/rollback protocol.

## 9. Declarative block architecture

### 9.1 Server-side registry

Add a first-party <code>DeclarativeBlockRegistry</code>. It is an authoring
service, not a runtime registry.

A versioned descriptor declares:

~~~json
{
  "type": "ask_user",
  "version": 1,
  "label": "Ask User",
  "category": "Interaction",
  "shape": "composite",
  "config_schema": {},
  "inputs": ["input"],
  "outputs": ["answered", "timeout", "cancelled", "failure"],
  "lowering_version": 1,
  "recognizer_version": 1
}
~~~

Each descriptor provides pure functions to:

- validate semantic configuration;
- create canonical tasks, groups, ports, and relations;
- recognize whether an existing canonical region still matches the block;
- update only fields owned by that semantic block;
- delete the owned region atomically;
- project redacted declarative configuration;
- enumerate runtime effects and dependencies.

Lowering is deterministic and side-effect-free. It cannot resolve secrets,
connect services, execute tasks, or infer missing authorization.

### 9.2 Atomic blocks

Every registered TaskFactory task appears as an atomic Processor block. Its
configuration uses the existing task parameter schema and relationships.

First-class friendly aliases include:

- LLM Call;
- Invoke Agent;
- Invoke Workflow Agent;
- HTTP Request;
- Read File;
- Write File;
- Transform;
- Update Value;
- Route;
- Split;
- Merge;
- Publish Message;
- Notify User;
- Input;
- Output;
- Subflow.

Unknown package tasks remain available as generic processors with their
package-provided name, icon, schema, effects, and trust badge.

### 9.3 Composite blocks

Composite blocks are inline Process Groups. Their internal tasks are canonical
and inspectable in technical view.

Initial composites:

- If / Else;
- Switch;
- Parallel;
- Join;
- Try / Catch;
- Retry;
- Ask User;
- Request Confirmation;
- Wait for Duration;
- Wait Until Time;
- Wait for Event;
- Notify Event;
- For Each;
- Repeat N Times;
- Repeat Until;
- Invoke Workflow Agent with await;
- Human Review;
- Complete Workflow Run.

Each composite exposes typed input and output ports. Parent-level connections
target those ports. Internal implementation can evolve only through an explicit
lowering-version upgrade that previews a structural diff.

### 9.4 Declarative variables

The UI exposes friendly outputs such as:

- <code>ask_environment.answer</code>;
- <code>confirmation.approved</code>;
- <code>review.response</code>;
- <code>agent.artifacts</code>;
- <code>join.items</code>.

The underlying tasks write reserved, block-scoped FlowFile attributes or
structured content fields. A projection service translates friendly references
to the existing expression language. The stored task parameters contain the
canonical expression, while the layout/block presentation stores only the
friendly label.

Block IDs must be valid stable namespace components. Renaming a display label
does not rename the block ID. Technical rename is a separate refactor operation
that updates references atomically.

### 9.5 Advanced edits and recognizability

When technical editing changes a composite block:

- if its canonical shape still matches the descriptor, declarative editing
  remains available;
- if the region is valid but no longer recognizable, it becomes a
  **Custom Group**;
- the UI shows the exact reason and offers no destructive automatic repair;
- **Reset to template** requires an explicit diff and confirmation;
- user-created technical fields are never silently discarded.

### 9.6 Per-block executor bindings

Every declarative block displays its effective executor. Deterministic control
and data blocks use <code>kind: pawflow</code>. Work blocks can explicitly use:

- <code>llm</code>: one bounded direct LLM call;
- <code>agent</code>: one exact agent resource and runtime instance;
- <code>workflow_agent</code>: one exact Workflow Agent binding;
- <code>human</code>: one durable user interaction or manual-review role;
- <code>pawflow</code>: one deterministic task or composite implemented by the
  engine.

There is no empty, anonymous, or guessed executor. A missing or inaccessible
binding is a publish error.

Reusable executor profiles live in the canonical flow definition:

~~~json
{
  "executor_profiles": {
    "writer": {
      "id": "writer",
      "kind": "llm",
      "service_ref": "writer_llm_service",
      "model": "configured-model",
      "limits": {
        "max_calls": 1,
        "max_tokens": 12000,
        "max_cost_usd": 0.5,
        "timeout_seconds": 180
      },
      "tool_policy": "none",
      "context_policy": "block_input_only"
    },
    "reviewer": {
      "id": "reviewer",
      "kind": "workflow_agent",
      "agent_ref": {
        "resource_type": "agent",
        "scope": "conversation",
        "name": "Reviewer",
        "version": "exact",
        "content_digest": "sha256"
      },
      "limits": {
        "max_duration_seconds": 900,
        "max_cost_usd": 1.0
      }
    }
  }
}
~~~

Profiles contain references and limits, never credentials. Services and
resources are resolved through their normal scope rules and snapshotted at run
start. Exact agent and Workflow Agent resources never follow latest.

A plan/flow may define visible role defaults without copying profile contents:

~~~json
{
  "executor_defaults": {
    "primary": "writer",
    "reviewer": "reviewer"
  }
}
~~~

Each step either references these defaults explicitly with
<code>inherited:primary</code> and <code>inherited:reviewer</code>, or selects
local profiles. Primary and reviewer independently support direct LLM, normal
agent, and Workflow Agent profiles. A local binding overrides only that role for
that step; changing a global default updates every inheriting step and no
locally-bound step.

A single-executor block stores:

~~~json
{
  "execution": {
    "strategy": "single",
    "roles": {
      "primary": {"executor_profile": "writer"}
    }
  }
}
~~~

A block that intentionally uses an LLM and an agent stores visible roles:

~~~json
{
  "execution": {
    "strategy": "primary_then_review",
    "roles": {
      "primary": {"executor_profile": "inherited:primary"},
      "reviewer": {"executor_profile": "inherited:reviewer"}
    },
    "review_policy": {
      "on_reject": "redo_primary_with_review",
      "max_revisions": 2,
      "feedback_input": "review.feedback",
      "result_input": "review.candidate"
    },
    "validation_criteria": [
      {
        "id": "tests_green",
        "kind": "expression",
        "description": "Every required test passes",
        "required": true,
        "expression": "${step.result.failed_tests:eq(0)}"
      },
      {
        "id": "scope_respected",
        "kind": "semantic",
        "description": "The result changes only the requested scope",
        "required": true
      }
    ]
  }
}
~~~

Supported initial strategies are <code>single</code>,
<code>sequence</code>, <code>parallel</code>, and
<code>primary_then_review</code>. They lower to explicit tasks, relations,
joins, and bounded revision paths inside the composite block. Multiple
executors are never an opaque model-side collaboration loop.

A flow or Process Group may declare executor defaults for named semantic roles,
but inheritance must be explicit in the block as
<code>executor_profile: inherited:primary</code>. Static validation resolves
the default to exactly one visible profile. Publication stores the
resolution path, and run start snapshots the effective service, model, agent
resource, limits, context policy, and authorization inputs.

When a reviewer accepts, the candidate exits through the step's success path.
When it rejects, its structured result contains at least
<code>{decision, criteria_results, feedback, candidate}</code>. Each
<code>criteria_results</code> item carries the stable criterion ID,
<code>passed</code>, bounded evidence, and criterion-specific feedback. The
step is accepted only when every required criterion passes.

Initial criterion kinds are <code>semantic</code>, <code>expression</code>,
<code>json_schema</code>, and <code>artifact</code>. Deterministic criteria are
evaluated by PawFlow and their evidence is supplied to the reviewer; a reviewer
cannot override a deterministic failure. Semantic criteria are stated in plain
language and included explicitly in the reviewer input and structured output
schema. Criteria have stable IDs, preserve order, and cannot be empty when a
reviewer is configured.

The next primary attempt receives the previous candidate plus only the failed
criteria, their evidence, and reviewer feedback through the configured input
mapping. This repeats until accepted or <code>max_revisions</code> is reached;
exhaustion follows an explicit <code>review_exhausted</code> relationship.
Reviewer feedback is never appended only to hidden prompt text: it is persisted
as bounded step provenance and visible in the run inspector.

Direct LLM work in an ordinary flow uses a new flow-native
<code>llmStep</code> task backed by a shared bounded LLM call service.
<code>agentLLMCall</code> remains the Workflow Agent specialization and is
refactored to use the same call, usage, cancellation, schema-validation, and
idempotency primitives. A direct LLM block has no tools unless its explicit
profile and task contract allow a reviewed bounded tool surface. Choosing an
agent means using the agent runtime rather than treating the agent prompt as a
direct LLM call.

The editor provides:

- an executor badge on every block;
- separate Primary and Reviewer badges on every reviewed step;
- Primary and optional Reviewer selectors in the block drawer, each offering
  **Use flow default** or an exact LLM, normal-agent, or Workflow-Agent profile;
- a flow-level Executor Profiles drawer;
- a flow-level Default Primary and Default Reviewer selector;
- filters by executor, model, agent, cost, and missing binding;
- bulk assignment for selected compatible blocks;
- a visible distinction between direct LLM, general agent, Workflow Agent,
  human, and deterministic engine execution;
- per-role prompt, input mapping, output schema, limits, and context-policy
  editors;
- a visible bounded review loop showing rejection feedback mapping, maximum
  revisions, current attempt, and <code>review_exhausted</code> output;
- an ordered Validation Criteria editor on every reviewed step, with add,
  remove, reorder, required/optional, criterion kind, description, deterministic
  configuration, and per-criterion runtime result/evidence;
- runtime display of the effective executor and model without exposing
  credentials or private configuration.

Changing an executor profile is a runtime-impacting definition change and
requires a new immutable version. Moving or recoloring its badge is
presentation-only.

## 10. Semantic control lowering

### 10.1 If and Switch

An If block lowers to <code>routeOnAttribute</code> with named
<code>true</code>, <code>false</code>, and optional
<code>evaluation_failure</code> relationships.

A Switch block lowers to one route per case plus a required default. The UI
offers a condition builder and an advanced raw-expression mode. Both store the
same canonical route parameters.

### 10.2 Parallel and Join

Parallel creates explicit fan-out. Each branch receives an independent
FlowFile copy and a shared correlation identifier.

Join lowers to <code>mergeContent</code> or a typed join processor with:

- correlation key;
- expected branches;
- merge strategy;
- missing-branch timeout;
- deterministic ordering;
- partial-result policy;
- maximum accumulated bytes and FlowFiles.

There is no hidden global accumulator. State travels through FlowFiles or a
declared scoped service.

### 10.3 Retry and error handling

Retry is bounded and requires:

- maximum attempts;
- backoff;
- retryable relationships or error codes;
- exhausted output;
- idempotency policy;
- optional budget contribution.

Implementation note: Retry lowers to a forward-only sequence of at most eight
attempt copies. Nonzero backoff uses `durableTimer`; no worker sleeps between
attempts. Each attempt stamps `retry.attempt` and clears stale routing metadata.
Unsafe or unknown task idempotency is rejected unless a key is present or the
definition records the explicit reviewed policy.

The declarative editor refuses retry around a non-idempotent effect unless the
task declares an idempotency key or the user chooses an explicit reviewed
policy.

Try / Catch is presentation over normal failure relationships and a join or
terminal path. It does not swallow errors by default.

### 10.4 Loops

Ordinary PawFlow definitions are DAGs. Declarative loops must not create raw
back edges.

Supported forms:

- **Repeat N Times**: statically unroll only below a small configured threshold;
- **For Each**: split a bounded collection, execute a child group, then join;
- **Repeat Until**: use a new bounded loop control task owning an isolated child
  group execution.

Every dynamic loop requires:

- <code>max_iterations</code>;
- <code>max_duration</code>;
- <code>max_flowfiles</code>;
- a stop condition;
- an exhausted output;
- cancellation propagation;
- checkpoint-safe iteration state;
- an explicit accumulation strategy.

A loop body is visible as a child group. Runtime provenance records iteration
number without duplicating task definitions.

## 11. Flow-native user interaction

### 11.1 Unified request contract

Generalize the durable confirmation model into a versioned user interaction
contract with kinds:

- confirm;
- single choice;
- multiple choice;
- text;
- multiline text;
- integer or decimal;
- date or datetime;
- file reference;
- structured form.

The request stores immutable requester identity, user and conversation scope,
prompt, safe options/schema, creation and expiry times, response state, and a
continuation reference.

No user ID or conversation ID from FlowFile content may override the injected
runtime context.

### 11.2 Store migration

Evolve <code>ConfirmationStore</code> into one
<code>UserInteractionStore</code>, or rename it through a one-shot SQLite
migration. Do not keep two writable stores.

Existing confirmation requests and durable waits are migrated transactionally.
The activation marker is written only after row counts, foreign keys, pending
continuations, and response compatibility pass.

### 11.3 Tasks

Add or normalize these tasks:

| Task | Purpose |
|---|---|
| <code>requestUserInput</code> | Create a durable typed interaction request |
| <code>notifyUser</code> | Push a user notification without parking the FlowFile |
| <code>durableWait</code> | Park until a correlated signal or interaction response |
| <code>durableNotify</code> | Resolve a durable signal |
| <code>durableTimer</code> | Park until a duration or absolute UTC time |
| <code>completeFlowRun</code> | Stage one terminal result for a durable one-shot run |

The declarative Ask User and Confirmation blocks contain
<code>requestUserInput</code> plus <code>durableWait</code>. A single visual
block therefore survives restart without pretending that an ephemeral agent
tool call is durable.

### 11.4 Relationships

- Ask User: <code>answered</code>, <code>timeout</code>,
  <code>cancelled</code>, <code>failure</code>.
- Confirm: <code>yes</code>, <code>no</code>, <code>timeout</code>,
  <code>cancelled</code>, <code>failure</code>.
- Notify User: <code>sent</code>, <code>queued</code>,
  <code>failure</code>.
- Durable Timer: <code>elapsed</code>, <code>cancelled</code>,
  <code>failure</code>.
- Wait for Event: <code>signaled</code>, <code>timeout</code>,
  <code>cancelled</code>, <code>failure</code>.

Answers are validated server-side before the waiter is resumed. Invalid or late
answers cannot revive a terminal run.

## 12. Invoking Workflow Agents from normal flows

### 12.1 Why this is not executeFlow

<code>executeFlow</code> runs a child flow and returns output FlowFiles. It does
not provide Workflow Agent inbox leasing, active generations, preemption,
authorization snapshots, budgets, exact agent binding, one terminal, or
conversation commit.

The new component must invoke the agent runtime, not bypass it.

### 12.2 Task contract

Add <code>invokeWorkflowAgent</code> with required configuration:

~~~json
{
  "agent_ref": {
    "resource_type": "agent",
    "scope": "conversation",
    "name": "Reviewer",
    "version": "exact",
    "content_digest": "sha256"
  },
  "message": "expression",
  "attachments": "expression",
  "parameters": {},
  "await_terminal": true,
  "publish_to_conversation": false,
  "terminal_timeout": "15m",
  "cancellation_policy": "propagate",
  "result_content": "response",
  "artifact_attribute": "review.artifacts"
}
~~~

The first production version supports exact Workflow Agent resources only.
A later <code>invokeAgent</code> facade may route other runtime kinds after
their adapters pass the Collaboration plan parity gates.

### 12.3 Invocation lifecycle

1. Resolve the exact visible agent resource and exact workflow binding.
2. Verify the parent runtime carries user, conversation, authorization, and
   deployment identity.
3. Intersect parent authority, task-declared effects, agent effects, flow
   effects, relay scope, and policy decisions.
4. Create an immutable parent invocation reference.
5. Submit through <code>WorkflowAgentRuntime</code> with
   <code>invocation_mode: flow</code>.
6. Persist the child run ID before acknowledging task acceptance.
7. If awaiting, park the parent FlowFile through the existing durable
   continuation mechanism.
8. The child terminal coordinator writes a stable outbox event addressed to the
   parent continuation.
9. Delivery reinjects the parent FlowFile exactly once.
10. The task projects response, artifacts, metrics, answered turn IDs, and
    terminal status.
11. Route to the matching relationship.
12. A replayed physical terminal event is deduplicated by event ID.

No worker polls <code>WorkflowRunStore</code> and no HTTP request waits for the
child.

### 12.4 Terminal relationships

Initial relationships are:

- <code>completed</code>;
- <code>no_change</code>;
- <code>failed</code>;
- <code>cancelled</code>;
- <code>timed_out</code>;
- <code>superseded</code>;
- <code>budget_exceeded</code>;
- <code>force_stopped</code>.

Unknown future terminal statuses fail closed to <code>failure</code> while
preserving the original status in a redacted attribute.

### 12.5 Identity, recursion, and cancellation

The child records:

- parent flow run ID;
- parent deployment instance ID;
- parent task ID;
- parent FlowFile process ID;
- parent authorization reference;
- invocation depth;
- ancestor agent and flow refs.

A repeated ancestor ref or depth above the configured maximum is rejected.
Parent force-stop cancels the child when policy is propagate. Child failure does
not force-stop the whole parent unless its failure relationship is unhandled.

<code>publish_to_conversation</code> is false by default to avoid a child
assistant message plus a parent terminal message. When true, the exact
conversation side effect is visible in validation and policy review.

## 13. Durable one-shot workflows

### 13.1 Execution mode

Add <code>durable_one_shot</code> as a deployment/run lifecycle over
<code>ContinuousFlowExecutor</code>.

It:

- creates one unique instance per run;
- injects one root FlowFile;
- supports durable waits and child runs;
- checkpoints queues and parked continuation ownership;
- prevents idle auto-stop while a durable waiter or child continuation exists;
- requires exactly one <code>completeFlowRun</code> terminal;
- auto-stops after terminal commit and empty in-flight work;
- retains run metadata and provenance for inspection and replay.

### 13.2 FlowRunStore

Add a generic <code>FlowRunStore</code> for one-shot run lifecycle only.
It does not execute flows and does not copy queue state.

States:

~~~text
created -> starting -> running -> waiting -> running
running -> committing -> completed
created|starting|running|waiting -> cancelling -> cancelled
created|starting|running|waiting -> failed|timed_out|force_stopped
~~~

Terminal states are immutable. The store owns:

- run ID and generation;
- exact flow ResourceRef and digest;
- deployment instance ID;
- proposal ID when present;
- parent invocation reference;
- authorization reference;
- status and timestamps;
- terminal summary and artifact references;
- stable terminal event outbox;
- recovery count and redacted error;
- replay ancestry.

<code>DeploymentRegistry</code> remains authoritative for deployment
configuration. <code>ExecutorRegistry</code> remains authoritative for the live
process object. <code>FlowRunStore</code> is authoritative only for the
one-shot lifecycle and terminal record.

### 13.3 Terminal commit

<code>completeFlowRun</code> stages one typed result. The coordinator requires
exactly one logical terminal per input run, validates size and artifact
references, transitions through committing, persists the result, publishes the
stable event, and then allows instance auto-stop.

A normal deployed continuous flow may contain the task but it is invalid unless
the definition declares a compatible run contract.

### 13.4 Replay

Replay creates a new run ID against the same immutable flow version and copies
only explicitly replayable input and parameter values. It does not reuse:

- approvals;
- authorization decisions;
- consumed user answers;
- child run IDs;
- task idempotency keys;
- terminal events.

Current authorization and resource visibility are checked again.

## 14. Workflow proposals replacing plans

### 14.1 Proposal model

Add <code>WorkflowProposalStore</code>. A proposal contains metadata, never an
independent executable step list.

~~~json
{
  "proposal_id": "wp_uuid",
  "user_id": "alice",
  "conversation_id": "conv",
  "title": "Prepare release",
  "summary": "Review, test, approve, and publish",
  "status": "draft",
  "draft_id": "d_uuid",
  "draft_revision": 7,
  "definition_digest": "sha256",
  "review_round": 3,
  "planner_reviewed_revision": 7,
  "planner_reviewed_digest": "sha256",
  "review_history": [
    {
      "event_id": "uuid",
      "created_at": "UTC timestamp",
      "actor_type": "planner",
      "actor_id": "assistant",
      "action": "accepted_revision",
      "draft_revision": 7,
      "definition_digest": "sha256",
      "comment": "The edited failure branch remains bounded."
    }
  ],
  "created_by": "assistant",
  "created_at": "UTC timestamp",
  "submitted_at": null,
  "approved_at": null,
  "approved_by": "",
  "published_flow_ref": null,
  "run_ids": []
}
~~~

States:

~~~text
planner_drafting -> user_review
user_review -> accepted
user_review -- edited revision submitted --> planner_review
planner_review -- accepted exact revision --> user_review
planner_review -- planner revision submitted --> user_review
planner_review -- changes requested --> user_review
planner_drafting|user_review|planner_review|accepted -> cancelled
accepted -> approved -> running -> completed
running -> failed|cancelled
~~~

### 14.2 Durable planner/user co-editing loop

The planner creates a canonical flow draft and submits one exact revision for
user review. The proposal card opens that same draft in the normal Flow Editor;
the user is never asked to edit a second plan format.

For each round:

1. The proposal records the exact revision and digest reviewed by the planner.
2. The user may keep the flow unchanged, edit it freely, or cancel.
3. Keeping it unchanged enables final acceptance because the current revision
   still equals the planner-reviewed revision.
4. Saving edits atomically invalidates the previous planner review and any
   acceptance, but does not discard either actor's review history.
5. **Send to planner** submits the new exact revision, digest, optional user
   comment, and a structured diff; the state becomes
   <code>planner_review</code>.
6. The planner must review that exact revision. It may accept it, request
   changes with comments, cancel, or apply its own semantic edits.
7. Planner edits create another revision and return the proposal to
   <code>user_review</code>. Planner acceptance also returns it to
   <code>user_review</code>, now with the edited revision marked as reviewed.
8. The cycle can repeat until the user accepts a planner-reviewed exact
   revision or either authorized actor cancels.

Every review event has a UUID and creation timestamp. Comments are append-only;
they reference an exact revision and digest. A stale browser, planner response,
or review event fails with a revision conflict and cannot approve, overwrite, or
silently merge newer work. The loop is user/planner driven and has no autonomous
retry cycle or hidden execution.

The optional **Send to planner** comment is part of the same stamped proposal
event as <code>proposal_id</code>, <code>draft_revision</code>,
<code>definition_digest</code>, and <code>state_revision</code>. The server writes
that event to the conversation transcript, appends it to the exact planner's
durable pending queue, and wakes that planner instance. It is never sent as an
unscoped chat message and cannot be reassociated with another draft revision.

The proposal UI shows the current actor turn, revision, digest prefix, diff from
the previous reviewed revision, review comments, **Open in editor**, **Send to
planner**, **Accept**, and **Cancel**. Closing or reopening the editor does not
change state. A draft save updates edit metadata; only the explicit
**Send to planner** action transfers the turn.

### 14.3 Acceptance and approval boundary

Final acceptance is allowed only when current draft revision and digest equal
the planner-reviewed revision and digest. Acceptance pins them immutably.
Approval:

1. authorizes the approver;
2. verifies proposal status;
3. loads the same draft revision;
4. recomputes the digest;
5. runs static and full parse validation;
6. publishes a new immutable conversation-scoped flow version;
7. stores the exact ResourceRef;
8. creates a durable one-shot run.

Any draft change after planner review invalidates acceptance and returns the
proposal to the co-editing loop. There is no approval of mutable latest content.

### 14.4 Authoring tools

Replace plan execution tools with workflow-native tools:

- <code>propose_workflow</code>;
- <code>get_workflow_proposal</code>;
- <code>revise_workflow_proposal</code>;
- <code>submit_workflow_proposal</code>;
- <code>open_workflow_proposal_draft</code>;
- <code>submit_workflow_proposal_to_planner</code>;
- <code>review_workflow_proposal_revision</code>;
- <code>request_workflow_proposal_changes</code>;
- <code>accept_workflow_proposal</code>;
- <code>approve_workflow_proposal</code>;
- <code>reject_workflow_proposal</code>;
- <code>cancel_workflow_proposal</code>;
- <code>run_workflow</code>;
- <code>inspect_flow_run</code>;
- <code>replay_flow_run</code>.

Agents edit through the same server semantic operations as the UI. They do not
emit an unchecked nested plan JSON.

Progress is derived from flow runs, task state, queues, waits, child runs, and
provenance. There is no <code>update_plan</code> equivalent.

## 15. Authoring service and APIs

### 15.1 Modules

Add focused modules:

- <code>core/declarative_flow/contracts.py</code>;
- <code>core/declarative_flow/registry.py</code>;
- <code>core/declarative_flow/projection.py</code>;
- <code>core/declarative_flow/operations.py</code>;
- <code>core/declarative_flow/validation.py</code>;
- <code>core/declarative_flow/macros/</code>;
- <code>core/flow_layout_contracts.py</code>;
- <code>core/workflow_proposal_store.py</code>;
- <code>core/flow_run_store.py</code>;
- <code>core/flow_run_coordinator.py</code>;
- <code>tasks/ai/actions/declarative_flow.py</code>;
- <code>tasks/ai/actions/workflow_proposals.py</code>.

No new module should exceed the project size target. Shared contracts must not
depend on UI code.

### 15.2 Semantic edit operations

Initial operations:

- create, rename, duplicate, reorder, and delete layout;
- set default layout;
- set viewport;
- move, resize, align, distribute, lock, and style nodes;
- create, update, move, style, lock, and delete annotations;
- create, update, move, style, lock, and delete frames;
- route, reconnect, style, and reset relations;
- auto-layout preview and apply;
- add, update, remove, and replace declarative block;
- connect block output to block input;
- wrap selection in composite block or Process Group;
- expose composite as Custom Group;
- upgrade lowering version with diff;
- rename technical ID with reference rewrite.

Every mutation requires <code>draft_id</code>,
<code>base_revision</code>, an operation schema version, and exact target IDs.
The server returns the new revision, changed entity IDs, and structured
problems.

### 15.3 Projection endpoints

Add actions for:

- declarative block catalog;
- project one group or whole flow;
- validate declarative recognizability;
- preview semantic mutation;
- apply semantic mutation;
- list and select layouts;
- preview auto-layout;
- apply auto-layout;
- proposal lifecycle;
- one-shot run lifecycle.

Read-only projections may operate on a published exact FQN. Writes require a
user-owned draft and normal scope authorization.

## 16. Flow Editor UX

### 16.1 Module split

Do not extend the already large inline implementation in
<code>flow_graph.html</code>. Extract or add modules such as:

- <code>flow_canvas_core.js</code>;
- <code>flow_layout_views.js</code>;
- <code>flow_edge_editor.js</code>;
- <code>flow_annotations.js</code>;
- <code>flow_visual_frames.js</code>;
- <code>declarative_editor.js</code>;
- <code>declarative_block_palette.js</code>;
- <code>workflow_proposals.js</code>.

The same canvas component is reused in every mode.

### 16.2 View switcher

The toolbar exposes:

- current layout dropdown;
- New View;
- Duplicate View;
- Rename View;
- Set Default;
- Delete View;
- Declarative / Technical projection where compatible;
- save viewport;
- fit view;
- auto-layout preview;
- layout/style inspector.

Switching a view never mutates the draft.

### 16.3 Free editing

In edit mode users can:

- drag any unlocked node;
- resize supported nodes, annotations, and frames;
- multi-select;
- move selection;
- align left, center, right, top, middle, bottom;
- distribute horizontally or vertically;
- snap to optional grid and guides;
- reorder z-index;
- reconnect relations;
- edit Bézier handles;
- choose source and target handles;
- move relation labels;
- style tasks, relations, annotations, and frames;
- copy and paste style;
- reset to theme defaults;
- undo and redo each compound operation.

Keyboard operation and accessible alternatives are required for all drag-only
features.

### 16.4 Declarative palette

Categories:

- Steps;
- Agents and LLM;
- Decisions;
- Parallel and Join;
- Loops;
- User Interaction;
- Time and Events;
- Data and Transform;
- Files and Network;
- Messaging;
- Subflows and Groups;
- Completion and Errors;
- Advanced Processors;
- Presentation.

Dropping a semantic block opens a concise domain-specific editor. An Advanced
section exposes the underlying task schema without requiring a different
canvas.

### 16.5 Runtime projection

Runtime view uses the exact stored layout and adds:

- running and in-flight state;
- queue depth and backpressure;
- branch counts;
- loop iteration;
- waiting reason and expiry;
- child Workflow Agent status;
- retry count;
- terminal state;
- errors and recovery badges.

Annotations and frames remain visible. A runtime filter may hide them
temporarily but does not alter the flow.

## 17. Validation

Extend <code>FlowDefinitionValidator</code> with codes for:

- unsupported layout schema version;
- missing or duplicate layout ID;
- missing default layout;
- invalid viewport;
- non-finite geometry;
- unknown node or relation layout target;
- duplicate or missing relation ID;
- invalid route mode or control point;
- invalid style token or color;
- annotation content or size violation;
- frame member missing;
- declarative block unrecognized;
- missing, unknown, ambiguous, or incompatible executor profile;
- executor inheritance that does not resolve to exactly one profile;
- direct LLM block without explicit service, model resolution, limits, output
  contract, or context policy;
- agent block with a non-exact or inaccessible resource;
- multi-executor strategy without all required roles, joins, or revision
  bounds;
- macro-owned task missing;
- macro relationship mismatch;
- friendly variable unresolved;
- branch without output;
- join without correlation or bounded timeout;
- loop without bounds;
- interaction without runtime conversation context;
- child agent reference not exact;
- one-shot flow without exactly one reachable terminal;
- durable wait reachable only from batch execution;
- visual-only fields incorrectly placed in runtime task parameters.

Presentation errors block publication when they make a stored view invalid.
Pure accessibility warnings may remain warnings initially, but unsupported or
unsafe content is an error.

## 18. Security and authorization

### 18.1 Declarative authoring

- Semantic operations are authorized exactly like technical draft edits.
- The server lowers blocks; clients cannot smuggle unvalidated tasks through a
  friendly block type.
- Projection redacts secrets and sensitive task parameters.
- Package task effects and trust origin remain visible.
- Annotation Markdown is sanitized and size-bounded.
- Colors and dimensions are validated against bounded schemas.

### 18.2 User interaction

- The runtime context selects the user and conversation.
- A flow cannot notify or ask a foreign user by setting attributes.
- Responses require the authorized participant and exact pending request.
- Expired, cancelled, superseded, or terminal requests cannot resume work.
- File answers are FileStore references authorized at consumption time.
- Notification channels are selected by the existing notification service and
  user preferences, not arbitrary task endpoints.

### 18.3 Child agents

- Exact resource and digest pinning.
- Authority is intersection, never union.
- Parent authorization is revalidated before child submission and before any
  child effect.
- Parent and child event content is redacted.
- Depth, fan-out, duration, cost, and concurrent children are bounded.
- Conversation publication is an explicit effect.

### 18.4 Layouts

Layouts cannot:

- change task type or parameters;
- change relation endpoints or runtime relationship names;
- hide validation errors;
- execute URLs or scripts;
- inject CSS;
- override runtime security colors or approval indicators.

## 19. Observability and provenance

Add identifiers to relevant events:

- flow run ID;
- proposal ID;
- layout ID used for display;
- semantic block ID;
- canonical task ID;
- relation ID and runtime connection ID;
- child run ID;
- interaction request ID;
- continuation event ID;
- loop iteration;
- lowering version.

Runtime metrics include:

- proposal approval latency;
- one-shot run duration;
- time waiting for user;
- child agent duration and outcome;
- retries and loop iterations;
- branch and join counts;
- continuation redeliveries;
- orphan waits;
- invalid terminal attempts;
- layout validation and migration failures.

Presentation changes enter the authoring diff and audit trail with
<code>runtime_impact: false</code>. They do not create execution provenance
events.

## 20. Packages and external clients

### 20.1 PFP packages

Packages may include:

- canonical tasks and relations;
- named layouts;
- annotations and frames;
- declarative presentation metadata;
- first-party block descriptors only when a future signed extension contract is
  approved.

Version 1 does not execute package-supplied lowering code. Package tasks appear
as generic atomic blocks using their existing schemas.

Package validation rejects unsafe annotation content, invalid styles, missing
layout targets, unbounded loops, and undeclared child resources.

### 20.2 Web, PawCode, and VS Code

The server owns projection and mutation contracts. Web is the first full
graphical implementation. PawCode can list proposals, approve, run, inspect,
and open the browser editor. VS Code uses the same actions and may add a native
canvas later.

All clients preserve unknown fields and tolerate unknown future layout or block
metadata when reading. They must not save a downgraded partial document.

## 21. Legacy plan migration and cutover

### 21.1 Preflight

Inventory every PlanStore record and classify:

- completed;
- cancelled;
- pending approval;
- approved but not started;
- in progress;
- waiting for verification;
- failed.

Resolve every assigned agent and verifier. The migration refuses activation if
an active record cannot be represented by an available exact agent invocation
adapter.

### 21.2 Conversion

For each plan:

1. Create a conversation-scoped flow draft.
2. Create input and completion nodes.
3. Convert sequential steps into semantic agent or manual-action blocks.
4. Convert assignment into exact agent invocation configuration.
5. Convert verifier metadata into an explicit review branch.
6. Connect steps in order.
7. Create a declarative layout approximating the old list.
8. Add an imported-plan annotation with original ID and timestamps.
9. Validate and publish an immutable imported version.
10. Create proposal and run-history records matching the old terminal state.

Active plans require generic agent invocation parity and a checkpoint mapping.
If safe resume cannot be proven, cutover is blocked; the migration does not
guess.

### 21.3 Activation

- Stop plan mutations.
- Take a backup.
- Run migration transactionally by conversation.
- Verify counts, statuses, ownership, assignments, and visible UI records.
- Write one activation marker.
- Switch tools, prompt directives, API actions, SSE events, and panels.
- Remove old plan orchestration only after the new path passes canary and full
  regression gates.
- No legacy and new writer may be active for the same conversation.

### 21.4 Removal

Delete after cutover:

- PlanStore runtime reads and writes;
- plan step orchestrator;
- plan-specific PollScheduler keys;
- forced stop triggered solely by <code>update_plan</code>;
- plan SSE cards and panel;
- plan mode directive requiring <code>create_plan</code>;
- legacy create, approve, assign, update, verify, cancel, and delete plan tools.

Replace the plan UI with Workflow Proposals and Flow Runs.

## 22. Implementation work packages

### WP0 — Characterization and immutable contracts

Deliver:

- characterization tests for current flow editor, layouts, Process Groups,
  relations, deployment layout copies, durable waits, Workflow Agent terminals,
  and plans;
- versioned contracts for layouts, relation IDs, declarative operations,
  interaction requests, proposals, flow runs, and parent continuations;
- strict feature flags disabled by default;
- dependency map against the Workflow Agents and Collaboration plans.

Gate:

- existing tests pass unchanged with flags disabled;
- schemas reject unknown versions, non-finite geometry, unsafe content,
  unbounded constructs, and missing identity;
- no store or definition is migrated by importing code or viewing the UI.

### WP1 — Stable relation IDs and multi-view layout foundation

Deliver:

- required stable <code>relation_id</code>;
- <code>layouts</code> schema and validator;
- migration from singular layout;
- projection helpers shared by view, edit, and runtime;
- layout-aware diff with no runtime impact;
- removal plan for deployment layout duplication.

Gate:

- every legacy definition migrates deterministically;
- two relationships between the same tasks but different runtime relationships
  keep independent styles;
- layout-only publication does not rebuild an executor;
- view, edit, and runtime render identical stored positions.

### WP2 — Advanced canvas geometry and presentation objects

Deliver:

- named view management;
- free node movement and resize;
- editable Bézier and alternative routes;
- relation label placement;
- annotations;
- visual frames;
- per-node, relation, annotation, and frame styles;
- z-order, locking, align, distribute, grid, guides;
- compound auto-layout preview and apply;
- extracted JavaScript modules.

Gate:

- every operation is undoable, revision-locked, persisted, and reload-stable;
- moving nodes preserves relative Bézier handles;
- runtime overlays remain visible with arbitrary allowed colors;
- annotations and frames never reach parser/runtime objects;
- keyboard and screen-reader alternatives pass accessibility checks.

### WP3 — Declarative projection and atomic task coverage

Deliver:

- DeclarativeBlockRegistry;
- server projection and semantic operations;
- generic Processor block for every TaskFactory task;
- friendly aliases for common tasks;
- stable semantic IDs and friendly variable projection;
- executor profile contracts and editor;
- explicit per-block PawFlow, LLM, agent, Workflow Agent, and human bindings;
- flow-native llmStep backed by shared bounded LLM call primitives;
- single, sequence, parallel, and primary-then-review lowering;
- Custom Group fallback for unrecognized regions.

Gate:

- every built-in and installed package task appears;
- technical and declarative edits round-trip without losing fields;
- browser and agent mutations produce byte-equivalent canonical definitions;
- every work block resolves exactly one executor for each declared role;
- changing a profile changes all referencing blocks without copying secrets or
  silently changing unrelated blocks;
- a broken macro never gets silently regenerated.

### WP4 — Branch, parallel, join, retry, and bounded loop blocks

Implementation status (2026-08-25): the first acyclic lowering slice is active.
If, Switch, Parallel, and Join compile server-side into deterministic inline
Process Groups containing only ordinary ports, `routeOnAttribute`, relations,
fan-out, and `mergeContent`. Semantic connections resolve stable composite port
IDs and the read-only projection collapses those internals back to one block.
Incomplete Join bins use the generic versioned processor-state checkpoint hook;
buffered FlowFiles, their UUIDs, timestamps, attributes, and content survive
executor restart before queue recovery resumes scheduling. Checkpoints are
written only at a quiescent executor boundary, so no FlowFile can be omitted in
the interval between queue dequeue and processor-state capture.
Repeat N statically unrolls a single-entry/single-exit acyclic body at most eight
times with deterministic forward-only task IDs and relations. Try/Catch lowers
single-entry/single-exit acyclic bodies into ordinary success/failure relations;
catch failure has its own explicit output and authored failure edges inside a
body are rejected rather than shadowed. Retry, For Each, and Repeat Until remain gated on
their dedicated validation and checkpoint-safe runtime slices; they are not
silently approximated by raw cycles.
The For Each substrate now uses standard `fragment.identifier`,
`fragment.index`, and `fragment.count` attributes, an explicit empty-collection
relationship, a hard split cap, dynamic per-wave Join counts, and persisted hard
limits on accumulated FlowFiles and bytes. The composite remains disabled until
its aggregate duration/cancellation contract is enforced by the durable run.

Deliver:

- If, Switch, Parallel, Join, Try/Catch, Retry;
- bounded For Each, Repeat N, and Repeat Until;
- correlation and accumulation contracts;
- loop task and checkpoint-safe state where required;
- validation and runtime metrics.

Gate:

- no raw graph cycle is emitted;
- fan-out, joins, retries, and loops are bounded;
- restart during each construct yields no duplicate terminal effect;
- exhausted and timeout relationships are testable and visible.

### WP5 — Flow-native interaction, notification, and timers

Implementation status (2026-08-25): complete behind the existing disabled
feature flags. The single confirmation database now stores versioned typed
interactions and preserves legacy confirmation rows through an additive guarded
migration. `requestUserInput`, `notifyUser`, and `durableTimer` use only injected
runtime scope; semantic Ask/Confirm/Notify/Wait blocks lower to ordinary tasks.
Webchat, PawCode, and VS Code restore pending requests after reconnect, render
their typed schemas, and submit through the authenticated generic action. Server
validation is canonical, response transitions are atomic, foreign IDs fail
closed, and durable waits remain explicitly invalid in batch execution.

Deliver:

- UserInteractionStore migration;
- requestUserInput;
- notifyUser;
- durableTimer;
- normalized confirmation and wait contracts;
- declarative Ask User, Confirm, Notify User, Wait Duration, Wait Until,
  Wait Event, and Notify Event blocks;
- pending-interaction UI integration.

Gate:

- free-text, choice, multi-choice, number, date, file, and form responses
  validate and resume exactly once;
- reload and server restart lose no pending request or FlowFile;
- late and foreign answers fail closed;
- batch-only definitions containing durable waits fail validation.

### WP6 — Workflow Agent invocation from flows

Deliver:

- flow invocation mode;
- invokeWorkflowAgent task;
- parent continuation record and terminal outbox;
- result and artifact projection;
- authorization intersection;
- cancellation and recursion guards;
- declarative Workflow Agent block and drill-down.

Gate:

- normal flow to child Workflow Agent to parent continuation succeeds;
- crash at every submit/park/terminal/deliver boundary gives one child run and
  one parent continuation;
- child publication is absent by default;
- force-stop and timeout propagate according to policy;
- direct executeFlow cannot masquerade as agent invocation.

This WP also connects the executor-profile resolver to
<code>invokeWorkflowAgent</code>. General <code>kind: agent</code> profiles
remain unavailable until the adapter parity gate in WP9; validation fails
closed instead of falling back to a direct LLM call.

### WP7 — Durable one-shot flow runs

Deliver:

- FlowRunStore and coordinator;
- durable_one_shot deployment lifecycle;
- completeFlowRun;
- idle-stop awareness of parked work;
- recovery and terminal outbox;
- inspection, cancellation, and replay.

Gate:

- a one-shot flow can wait days in simulated time and resume after restart;
- exactly one terminal is committed;
- missing or multiple terminals fail closed;
- terminal run auto-stops without deleting history;
- replay re-authorizes and creates new identity.

### WP8 — Workflow proposals and authoring UX

Deliver:

- WorkflowProposalStore;
- proposal tools and actions;
- exact draft-revision submission;
- approval-to-publication transaction;
- proposal cards with mini declarative graph;
- editor entry from proposal;
- durable planner/user revision-review loop with comments and exact turn
  ownership;
- run status projection.

Gate:

- the user approves exactly what was reviewed;
- editing after planner review invalidates acceptance and transfers no turn
  until explicitly submitted;
- user edits return to the planner, planner edits return to the user, and every
  round remains pinned to an exact revision and digest;
- stale planner or browser responses fail with a revision conflict;
- either authorized actor can cancel without publishing or running the draft;
- a non-technical user can create, review, approve, run, inspect, and replay
  without editing JSON;
- proposal state never duplicates task progress.

### WP9 — Legacy plan migration and generic agent parity

Deliver:

- generic invokeAgent only after adapter parity gates;
- PlanStore migration preflight and converter;
- archived terminal run import;
- active checkpoint conversion;
- tool, prompt, SSE, UI, CLI, and API cutover;
- rollback tooling before destructive cleanup.

Archived terminal import uses deterministic proposal and run identities from the
preflight source digest. It writes explicit import provenance into both canonical
stores, creates no live terminal outbox event, and is idempotent on exact retry.
Because the stores are separate SQLite authorities, the importer is a
compensating saga: a proposal conflict removes only the run created by that
attempt, and deletion is rejected unless the stored source provenance matches
exactly. A pre-existing idempotent run is never compensation-owned.

The immutable imported Flow is compiled before any active checkpoint transfer.
Legacy step indexes produce stable execution and verification task IDs, every
runnable agent task carries its exact ResourceRef, and the definition contains
one completeFlowRun terminal plus the exact resume task derived from the
verification checkpoint. Publication goes through FlowAuthoringService so static
validation, parser validation, conversation scoping, and immutable repository
versioning remain canonical. An exact retry reuses the published version; a
truncated-FQN collision with a different full source digest fails closed.

A waiting-verification checkpoint is transferred as a compensating saga. The
importer first persists a provenance-pinned waiting FlowRun, its running
proposal projection, and a deterministic durable timer carrying the original
FlowFile identity. Only after all three writes succeed does it cancel the exact
legacy PollScheduler key. A failure compensates in reverse order and removes
only artifacts created by that attempt; an exact retry never owns or deletes
pre-existing canonical artifacts.

Gate:

- every legacy record is converted or explicitly blocks activation;
- counts and terminal states match;
- no plan and proposal writer overlap;
- old tools and panels are absent after activation;
- full agent-runtime regression suite passes.

### WP10 — Packages, operations, rollout, and cleanup

Deliver:

- PFP validation and examples;
- Web, PawCode, and VS Code integration;
- metrics, alerts, retention, repair commands;
- security review;
- staged feature activation;
- documentation updates;
- legacy layout and plan code deletion;
- release notes and operator runbook.

Gate:

- canary users complete representative automation, interaction, agent, layout,
  and proposal workflows;
- recovery fault-injection and full CI are green;
- manual Web plus one non-Web client test passes;
- no secret, prompt body, or unauthorized target appears in projections;
- feature flags disabled preserve current behavior until explicit activation.

## 23. Proposed test matrix

### 23.1 Unit

- Layout schema version, default selection, CRUD, geometry, style, annotation,
  frame, and viewport validation.
- Deterministic legacy layout and relation ID migration.
- Layout diff classified as no runtime impact.
- Relation routing serialization and control-point math.
- Declarative descriptor validation, lowering, recognition, update, and delete.
- Stable generated IDs and collision handling.
- Friendly variable resolution and technical rename.
- Executor profile parsing, inheritance, exact resolution, role requirements,
  limits, snapshots, and invalid fallback rejection.
- Direct LLM versus agent versus Workflow Agent dispatch.
- Multi-executor strategy lowering and bounded reviewer revision.
- Every semantic block schema and relationships.
- Interaction request validation for every input kind.
- Proposal co-editing, exact-revision review, cancellation, acceptance, and
  FlowRun state transitions.
- Parent continuation deduplication.
- Agent authority intersection and recursion detection.
- Loop bounds and join correlation.

### 23.2 Editor integration

- Create, duplicate, rename, switch, and delete views.
- Drag, resize, align, distribute, lock, style, undo, redo, reload.
- Bézier control handles, endpoint reconnect, label movement, route reset.
- Annotation Markdown sanitization.
- Visual frame membership and move-members behavior.
- Auto-layout preview cancel and apply.
- Concurrent draft conflict.
- Declarative to technical to declarative round trip.
- Custom Group fallback.
- Runtime layout equality.
- Layout-only publish and apply without executor rebuild.

### 23.3 Runtime integration

- Question then durable answer.
- Confirmation yes, no, timeout, cancel.
- User notification sent, queued, and failure.
- Duration, absolute time, event wait, and signal-before-wait.
- Parallel fan-out and join.
- Retry exhaustion.
- Bounded loop completion and exhaustion.
- Two blocks using different explicit LLM services and models.
- One block using a direct LLM and another using a general or Workflow Agent.
- Primary LLM followed by agent review, including rejection and bounded
  revision.
- Executor profile change between immutable versions while an old run keeps
  its original snapshot.
- Normal flow invoking a Workflow Agent.
- Child success, no-change, failure, timeout, cancellation, and budget limit.
- One-shot wait, restart, resume, terminal, auto-stop, and replay.
- Proposal user edit, planner re-review, planner edit, repeated round trips,
  final acceptance, approval, and execution.
- Concurrent runs of one immutable flow version.

### 23.4 Recovery fault injection

Terminate or simulate failure:

- after interaction request creation before park;
- after park before task return;
- after answer before signal;
- after signal before reinjection;
- after child run creation before parent park;
- after parent park before child terminal;
- after child terminal outbox before delivery;
- after delivery before deduplication commit;
- after FlowRun creation before executor registration;
- after terminal staging before committing;
- after terminal persistence before event publication;
- after event publication before auto-stop;
- during plan-to-proposal migration.

Every case asserts no lost FlowFile, no duplicate child, no duplicate user
request, no duplicate terminal, and a recoverable or immutable final state.

### 23.5 Security

- Foreign draft, flow, layout, user, conversation, agent, relay, service, and
  FileStore targeting.
- Request content attempting to replace runtime identity.
- Annotation script, HTML, URL, CSS, and oversized payload attacks.
- Invalid colors, NaN/Infinity geometry, and huge canvas coordinates.
- Package task with undeclared effects.
- Child agent widening parent authority.
- Stale approval after draft revision.
- Replay using expired authorization.
- Hidden task in declarative or custom view.
- Runtime overlay obscured by hostile style.
- Recursive flow-agent invocation and fan-out exhaustion.

### 23.6 Migration

Fixtures cover:

- empty and populated legacy layout;
- deployment layout equal to and different from published layout;
- nested Process Groups and subflows;
- multiple relationships and dynamic route names;
- every PlanStore state;
- assigned Workflow Agent, LLM agent, user, and missing agent;
- verifier pending and rejected;
- migration interruption and rerun;
- activation marker and rollback backup;
- post-cutover absence of dual writers.

## 24. Operational controls

Operators need to:

- list pending and expired user interactions;
- inspect and redeliver a safe continuation;
- list active, waiting, recoverable, and terminal FlowRuns;
- cancel or force-stop by exact run;
- see child run ancestry;
- quarantine an invalid exact flow version;
- inspect proposal revision and approval lineage;
- run layout and plan migration preflight;
- verify migration counts and digests;
- clear only terminal retained instances according to policy;
- view queue, wait, child, retry, loop, duration, and failure metrics.

Alerts:

- waiting continuation without owner instance;
- child terminal outbox not delivered;
- FlowRun committing beyond threshold;
- more than one terminal attempt;
- interaction expired but not routed;
- one-shot executor idle with parked work missing;
- repeated recovery;
- migration count or digest mismatch;
- layout migration failure;
- invalid declarative projection in a published flow.

## 25. Documentation deliverables when implementation ships

Update in the same work packages:

- <code>docs/architecture.md</code>;
- <code>docs/flow_editor.md</code>;
- <code>docs/flow_runtime_console.md</code>;
- <code>docs/AGENT_SYSTEM.md</code>;
- <code>docs/tasks.md</code>;
- <code>docs/02_REFERENCE_TASKS_SERVICES.md</code>;
- <code>docs/WORKFLOW_AGENTS_IMPLEMENTATION_PLAN.md</code>;
- <code>docs/AGENT_COLLABORATION_AND_TOOL_SAFETY_PLAN.md</code>;
- <code>docs/PFP_PACKAGES.md</code>;
- <code>docs/PFP_DEVELOPER_GUIDE.md</code>;
- <code>docs/security_model.md</code>;
- <code>docs/POLICY_GATING.md</code>;
- <code>docs/OBSERVABILITY.md</code>;
- Web, PawCode, and VS Code help;
- examples for declarative automation, human review, Workflow Agent
  composition, multi-view layout, annotations, and one-shot proposals;
- project wiki pages sourced from the shipped code and docs.

The implementation remains opt-in. Do not activate the proposal cutover until
the release gates and the operator sequence in
<code>PLANSTORE_MIGRATION_RUNBOOK.md</code> are satisfied.

## 26. Risks and mitigations

### Dual declarative and technical truth

Risk: semantic metadata becomes another executable document.

Mitigation: tasks, groups, relations, and parameters are canonical. Declarative
metadata contains labels and recognition hints only. Composite blocks are real
inline groups.

### Browser-specific compilation

Risk: Web, VS Code, and agents create different graphs.

Mitigation: server-owned semantic operations and deterministic lowering.

### Layout drift

Risk: draft, published flow, deployment, and runtime each show different
positions.

Mitigation: published layouts are canonical; runtime pins the exact version;
instance overrides are explicit and separately labeled; legacy copies are
migrated once.

### Lost edge styling

Risk: a relation type or endpoint edit changes derived connection identity.

Mitigation: stable <code>relation_id</code> for authoring and layout, separate
from runtime queue identity.

### Visual group confused with runtime group

Risk: users believe a colored frame changes execution scope.

Mitigation: distinct names, icons, schema locations, drawers, and validation.
Frames never enter parser output.

### Auto-layout destroys manual work

Risk: automatic placement overwrites a curated diagram.

Mitigation: explicit preview, locked nodes, selection scope, undo, and no
automatic persistence on open.

### Runtime state unreadable with custom colors

Risk: user styles hide failure or approval status.

Mitigation: accessible overlay layer with enforced contrast and non-color
indicators.

### Unbounded loops or agents

Risk: friendly blocks hide expensive recursion.

Mitigation: mandatory bounds, visible cost indicators, static validation, and
runtime counters.

### Duplicate child or continuation

Risk: restart repeats a Workflow Agent call or resumes a parent twice.

Mitigation: persisted idempotency keys, parent references, terminal outbox, and
fault-injection tests.

### Approval of mutable content

Risk: the user approves one graph and another revision executes.

Mitigation: proposal pins draft revision and digest; approval atomically
validates and publishes that exact revision.

### Plan migration ambiguity

Risk: an active legacy step cannot map safely to an exact agent run.

Mitigation: preflight blocks activation. No guessed resume and no dual writer.

### UI size and maintainability

Risk: the canvas becomes another oversized monolith.

Mitigation: mandatory module extraction and per-file size target in WP2 and WP3.

## 27. Release acceptance criteria

The feature is complete only when all statements are true.

1. One <code>FlowDefinition</code> fully determines runtime behavior.
2. Declarative editing covers every task through friendly or generic blocks.
3. If, Switch, Parallel, Join, Retry, bounded loops, user interaction, waits,
   notifications, subflows, and Workflow Agents are authorable without JSON.
4. Every work block visibly selects an exact direct LLM, agent, Workflow Agent,
   human role, or deterministic PawFlow executor, and multi-executor blocks
   expose every role and bounded strategy.
5. Technical and declarative views round-trip without hidden conversion.
6. Multiple named layouts persist in drafts and immutable versions.
7. Edit, view, and runtime render the same selected stored positions.
8. Users can freely move and resize elements and edit Bézier relations.
9. Auto-layout is explicit, previewable, scoped, undoable, and respects locks.
10. Annotations and visual frames support titles, descriptions, colors, movement,
   resize, and z-order without affecting runtime.
11. Tasks and relations can be individually styled while runtime state remains
    accessible.
12. Layout-only versions do not rebuild live executors.
13. A normal flow can invoke a Workflow Agent and resume durably from its exact
    terminal.
14. A workflow can ask, confirm, notify, wait, restart, and resume exactly once.
15. A durable one-shot flow can be inspected and replayed.
16. A proposal can round-trip between planner and user edits until acceptance
    or cancellation, and executes exactly the planner-reviewed graph revision
    the user accepted.
17. PlanStore is no longer an execution source after one-shot migration.
18. No active migration relies on two writable stores.
19. Every asynchronous crash boundary has a recovery test.
20. Every new task, handler, store, parser rule, migration, and UI operation has
    unit coverage.
21. Full CI plus manual Web and one non-Web end-to-end gate are green.
22. With feature flags disabled, existing flows, agents, layouts, deployments,
    clients, transcripts, and approvals retain their current behavior.

## 28. Recommended delivery sequence

The smallest useful authoring slice is WP0 through WP3:

- stable relations;
- named stored layouts;
- free visual editing;
- annotations and frames;
- generic declarative task coverage.

It proves the unified editor without changing runtime behavior.

The first useful orchestration slice is WP4 through WP6:

- semantic control blocks;
- durable user interaction;
- Workflow Agent invocation.

The first production-ready proposal slice is WP7 and WP8:

- durable one-shot runs;
- approval pinned to an exact flow revision.

WP9 is the destructive migration gate. WP10 is required for general
availability and cleanup.

## 29. Final product example

A user creates a workflow proposal and chooses a declarative business view.

They place:

1. Ask User: choose the target environment.
2. If production: request confirmation.
3. Parallel:
   - Workflow Agent Reviewer using the Reviewer profile;
   - run tests with the PawFlow engine;
   - generate release notes with the Writer LLM profile.
4. Join all results.
5. Ask an Auditor agent to review the Writer LLM output.
6. If review failed: notify the user and stop.
7. Publish.
8. Notify the user.
9. Complete run.

They arrange those blocks freely, curve the approval and failure relations,
color the production path, add a warning annotation, and place the review nodes
inside a titled green visual frame. They also keep a separate technical layout
showing every processor and an operations layout optimized for runtime queues.

PawFlow stores one executable flow and three presentation layouts. Approval
pins the exact draft revision, publishes an immutable conversation-scoped
version, starts one durable continuous run, waits safely for the user and child
agent, commits one terminal outcome, and can replay the same exact workflow
later.

That is the target: accessible declarative authoring, complete PawFlow power,
durable execution, and rich diagrams without a second engine or a second truth.

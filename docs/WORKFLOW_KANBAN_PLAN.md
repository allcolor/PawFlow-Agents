# WorkflowRun Kanban Projection Implementation Plan

Status: base projection implemented on 2026-08-27; G5 collaboration depth
implemented on 2026-08-28.

## 1. Objective

Add an operational Kanban view for PawFlow Workflow Agents without creating a
second task engine or an independent Kanban database.

The canonical model remains:

- versioned FlowDefinition for structure;
- WorkflowRun for execution identity and state;
- FlowFile/task events for progress;
- ConfirmationStore and conversation events for human interaction;
- the flow graph as the canonical editor.

Kanban is a projection plus a validated command surface over those sources.

## 2. User outcomes

A user can:

1. open a Kanban view from a Workflow Agent or its run inspector;
2. see current and recent WorkflowRuns grouped by operational state;
3. switch to a task/stage view for one run;
4. identify queued, active, waiting-for-human, retryable, failed, and completed
   work;
5. see branches, joins, dependencies, assignee, usage, artifacts, and alerts;
6. add comments correlated to a run or flow task;
7. assign operational ownership without changing agent execution identity;
8. drag a card only when the target lane maps to a valid engine command;
9. retry, cancel, stop, or satisfy a human wait through existing runtime APIs;
10. follow every action in the immutable run event timeline.

## 3. Non-negotiable invariants

1. No `kanban.sqlite3`, task-board store, or duplicated workflow state.
2. A card status is derived from WorkflowRun state and events.
3. A drag never writes a status field directly.
4. Terminal WorkflowRun states remain immutable.
5. Flow branches, joins, and dependencies are not flattened into false linear
   order.
6. The graph remains the canonical flow-definition editor.
7. Comments, assignments, and commands have a UUID and creation timestamp.
8. Authorization is conversation- and user-scoped through existing UI action
   boundaries.
9. Payloads are redacted through the existing run inspector policy.
10. No implicit timeout, deadline, retry quota, or number of passes is added.

## 4. Existing architecture to extend

- `core/workflow_run_store.py`: transactional runs and immutable run events.
- `core/workflow_run_inspector.py`: redacted summaries, events, and flow graph.
- `core/workflow_agent_contracts.py`: legal WorkflowRun state transitions.
- `core/workflow_agent_runtime.py`: live-run ownership and recovery.
- `tasks/ai/actions/_agentres_k8.py`: scoped UI actions for run snapshots,
  inspection, retry, and deletion.
- `tasks/ai/actions/agent_resource.py`: read/write authorization map.
- `tasks/io/chat_ui/workflow_run_inspector.js`: existing modal and graph.
- `ConversationEventBus`: live workflow progress delivery.
- `ConfirmationStore`: durable waits and user responses.

## 5. Two projections

### 5.1 Run board

One card represents one WorkflowRun.

Default lanes:

| Lane | Derived states |
|---|---|
| Queued | `accepted` |
| Running | `running`, `committing`, `cancelling` |
| Waiting | `waiting` |
| Needs attention | `retryable_failed` |
| Failed | `failed`, `timed_out`, `budget_exceeded`, `recovery_failed` |
| Done | `completed`, `cancelled`, `superseded`, `force_stopped` |

Lane labels are presentation, not stored states. Filters can hide terminal cards
but never delete them.

### 5.2 Run task board

One card represents a task node from the exact run's immutable FlowDefinition
snapshot. Its state is derived from run events:

| Task lane | Evidence |
|---|---|
| Not started | task exists in graph; no start event |
| Ready/queued | predecessor evidence complete or explicit queued event |
| Running | latest task event is start/progress/tool activity |
| Waiting for human | durable confirmation/user-input event for the task |
| Blocked | retryable failure or dependency evidence |
| Failed | latest task terminal event is failed |
| Done | task completion/output event |

If evidence is insufficient, the card is `unknown`, not guessed. Unknown cards
remain visible with a diagnostic badge.

## 6. Data projection contract

Add pure functions in `core/workflow_kanban.py`:

```python
workflow_kanban_snapshot(
    conversation_id: str,
    agent_name: str = "",
    run_id: str = "",
    limit: int = 100,
    *,
    store=None,
    live_run_ids=(),
) -> dict
```

Response shape:

```json
{
  "version": 1,
  "generated_at": "UTC timestamp",
  "conversation_id": "UUID",
  "agent_name": "assistant",
  "mode": "runs|tasks",
  "lanes": [
    {"id": "running", "label": "Running", "order": 20}
  ],
  "cards": [
    {
      "id": "run UUID or run:task",
      "run_id": "UUID",
      "task_id": "optional",
      "lane": "running",
      "title": "Flow/task label",
      "status": "canonical status",
      "live": true,
      "assignee": "operator label or null",
      "comments_count": 2,
      "created_at": "UTC timestamp",
      "updated_at": "UTC timestamp",
      "badges": [],
      "relations": {"parents": [], "children": []},
      "allowed_commands": ["cancel"],
      "summary": {}
    }
  ],
  "relations": [],
  "filters": {},
  "cursor": null
}
```

The projection exposes only redacted fields already allowed by
`workflow_run_inspector`.

## 7. Comments and assignments

Use WorkflowRun events, not a separate table.

### 7.1 Comment event

```json
{
  "event_type": "kanban_comment",
  "data": {
    "comment_id": "UUID",
    "task_id": "optional flow task id",
    "author_user_id": "authenticated user id",
    "author_label": "display label",
    "body": "redacted/validated text",
    "created_at": "UTC timestamp"
  }
}
```

### 7.2 Assignment event

```json
{
  "event_type": "kanban_assignment",
  "data": {
    "assignment_id": "UUID",
    "task_id": "optional",
    "assignee": "explicit operator or agent label",
    "assigned_by_user_id": "authenticated user id",
    "created_at": "UTC timestamp"
  }
}
```

The current assignee is the latest valid assignment event for the same run/task.
Assignment expresses operational ownership only. It never changes
`WorkflowRun.agent_name`, authorization snapshots, service bindings, or task
execution routing.

Comment bodies are length-validated and treated as untrusted text. HTML is
escaped in the browser.

## 8. Command model

Add a pure command planner:

```python
plan_workflow_kanban_command(run, task_id, target_lane, events) -> CommandPlan
```

A plan is either:

- executable through an existing runtime action;
- requires an existing human interaction;
- informational only;
- rejected with a stable reason.

### 8.1 Run-card mappings

| Source | Target | Runtime action |
|---|---|---|
| `retryable_failed` | Running | existing explicit `retry_workflow_run` |
| active non-terminal | Done/cancelled | existing graceful cancel/interrupt path |
| active non-terminal | Force stopped | existing force-stop action, only from explicit menu confirmation |
| Waiting | Running | open and satisfy the correlated ConfirmationStore interaction |
| terminal | any other lane | reject; terminal state immutable |
| any | arbitrary status | reject; no direct status write |

Drag to Done never claims that a run completed. For an active card it is displayed
as a cancel request and requires confirmation.

### 8.2 Task-card mappings

- waiting task to Running: open the exact pending human interaction;
- retryable failed task to Running: retry the exact checkpoint when
  `safe_retry=true`;
- a task with unmet parents to Running: reject and display blocking parents;
- a completed task to another lane: reject;
- a not-started task to Running: informational only unless the runtime exposes a
  reviewed task-signal contract;
- reorder within a lane: UI preference only and not persisted in v1.

No generic `set_status` endpoint exists.

### 8.3 Command audit events

Before and after a command, append:

- `kanban_command_requested`;
- `kanban_command_succeeded`; or
- `kanban_command_rejected`.

Each includes command UUID, actor, source/target lanes, task id, UTC timestamp,
and stable result code. It contains no secret payload.

## 9. API actions

Extend the existing `/api/ui` action path:

- `workflow_kanban_snapshot` (read);
- `workflow_kanban_comment` (write);
- `workflow_kanban_assign` (write);
- `workflow_kanban_plan_command` (read);
- `workflow_kanban_execute_command` (write).

Every action requires `conversation_id`; run ids must belong to that
conversation. Agent filters are optional but, when supplied, must match the run.
Task ids must exist in the exact projected graph.

Write actions require an authenticated user identity passed by the canonical UI
action context. The client cannot supply an arbitrary author user id.

## 10. Live updates

After a comment, assignment, or command event:

1. append the durable WorkflowRun event;
2. publish `workflow.kanban.updated` through
   `ConversationEventBus.instance()`;
3. include conversation id, run id, optional task id, event id, and timestamp;
4. let every visible board refresh from a fresh snapshot.

The event is an invalidation signal, not a state patch. This prevents clients
from reconstructing divergent state.

Existing `workflow_progress` events also invalidate affected cards.

## 11. UI architecture

Add separate modules:

```
tasks/io/chat_ui/
  workflow_kanban.js
  css/52_workflow_kanban.css
```

Keep the existing inspector file focused on detail/graph view.

The WorkflowRun inspector gains a `Graph | Timeline | Kanban` view switch.
The agent resource menu also exposes `Kanban`.

### 11.1 Board layout

- horizontal lanes on desktop;
- vertically stacked, horizontally scrollable lanes on narrow mobile screens;
- sticky lane header with card count;
- keyboard-accessible card actions;
- no information hidden behind hover only;
- a detail drawer with run/task summary, relations, comments, assignment, and
  allowed actions;
- badges for branches, joins, dependency count, human wait, retry safety,
  artifacts, usage, and alerts.

All content remains visible or vertically scrollable on mobile.

### 11.2 Drag and drop

HTML drag-and-drop is progressive enhancement. Every operation is also available
from a keyboard/menu `Move/Action` control.

Flow:

1. ask `workflow_kanban_plan_command`;
2. show the exact semantic action;
3. ask for confirmation when the action mutates execution;
4. execute with a UUID idempotency key;
5. refresh from snapshot;
6. show the durable outcome.

The card does not move optimistically before the server confirms.

### 11.3 Relations

The task board keeps graph semantics:

- parent/child badges on cards;
- hover/focus relation highlighting;
- optional SVG dependency connectors;
- branch and join icons derived from graph degrees;
- `Open in graph` focuses the exact node in the existing flow graph.

Kanban never becomes the flow editor.

## 12. Internationalization and accessibility

Add French strings for every user-facing label and the existing supported
translation structure for other locales.

Accessibility requirements:

- lanes and cards have list/listitem semantics;
- status is not conveyed only by color;
- actions have visible focus;
- drag alternatives work by keyboard;
- live update announcements are polite and summarized;
- modal/drawer focus is trapped and restored;
- reduced-motion setting disables animated card movement.

## 13. Retention and scale

V1 uses the existing `list_runs` limit with an explicit user-selectable filter.
No hidden pagination ceiling becomes a product quota. The API returns a cursor
when more runs exist, and the UI offers `Load more`.

Comments and assignment events follow WorkflowRun event retention. They are not
retained in a detached board after the run is pruned.

Projection queries use indexed run id, conversation id, agent name, status, and
event sequence. If necessary, add indexes to the existing WorkflowRun database;
do not add denormalized board tables.

## 14. Security

- conversation-scoped run lookup on every request;
- canonical actor from authenticated context;
- authorization classification in `agent_resource.py`;
- inspector redaction reused for all payloads;
- comment HTML escaped;
- stable action allowlist;
- no arbitrary state transition;
- no arbitrary task id;
- terminal immutability;
- command idempotency;
- event timestamps and UUIDs assigned server-side;
- no secrets in audit events or SSE invalidations.

## 15. Implementation work packages

### K1. Projection core

- lane mapping;
- run cards;
- task cards;
- relation projection;
- event-derived comments and assignments;
- unit tests.

### K2. Command planner

- legal mappings;
- rejection codes;
- idempotency;
- runtime adapters;
- audit events;
- tests for every source/target pair.

### K3. API actions

- snapshot;
- comment;
- assign;
- plan and execute;
- authorization and scoping tests.

### K4. Browser board

- view switch;
- board rendering;
- detail drawer;
- comment and assignment UI;
- drag and keyboard actions;
- mobile layout.

### K5. Live updates

- event-bus invalidation;
- visibility handling;
- refresh coalescing without dropping updates;
- UI tests.

### K6. Documentation and operations

- user guide;
- API/action reference;
- metrics and alerts;
- retention notes.

## 16. Tests

### Pure projection tests

- every WorkflowRun state maps to one lane;
- live flags do not override canonical state;
- task state derives from ordered events;
- unknown evidence remains unknown;
- branch/join relations survive projection;
- latest assignment wins per run/task;
- comments are ordered and escaped;
- redacted inspector fields remain redacted.

### Command tests

- retryable failure can plan explicit retry;
- unsafe retry is rejected;
- waiting work returns the exact human interaction;
- unmet dependency blocks task start;
- terminal card movement is rejected;
- cancel/force-stop require correct runtime path;
- duplicate idempotency key returns the original result;
- no direct transition helper is reachable from the UI action.

### API tests

- missing conversation id fails;
- cross-conversation run id returns not found;
- spoofed author is ignored/rejected;
- unknown task id fails;
- read/write authorization classification is correct;
- every appended event has UUID and timestamp.

### UI tests

- resource menu and inspector expose Kanban;
- snapshot action names are used;
- no legacy direct list/inspect sequence is introduced;
- card text is escaped;
- drag calls plan before execute;
- keyboard alternative exists;
- mobile CSS scrolls;
- live invalidation refreshes;
- terminal card has no invalid actions.

### End-to-end

Run a branching workflow that:

1. creates parallel tasks;
2. joins them;
3. pauses for human input;
4. receives a comment and assignment;
5. resumes;
6. experiences one retryable failure;
7. retries safely;
8. completes.

Verify the board and graph agree at every stage.

## 17. CI gates

- focused workflow store/inspector/Kanban tests;
- all Workflow Agent runtime tests;
- chat UI static-resource tests;
- JavaScript syntax checks;
- CSS/mobile regression assertions;
- Python lint and security scan;
- database migration/reopen test;
- documentation reference checks.

## 18. Acceptance criteria

1. The run board shows all canonical run states without storing board status.
2. The task board represents the exact immutable flow snapshot.
3. Branches, joins, and blockers are visible.
4. Comments and assignments are durable run events with UUIDs and timestamps.
5. Every drag maps to a reviewed runtime command or is rejected.
6. Terminal states cannot be changed.
7. Waiting human work opens the existing durable interaction.
8. Cross-conversation access fails.
9. Live updates converge by reloading a server snapshot.
10. Mobile content remains visible or scrollable.
11. No second database, scheduler, worker, or execution engine exists.
12. Graph editing remains canonical.
13. Tests and documentation pass.

## 19. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Board becomes a second truth | Pure projection, no board state table |
| Drag implies invalid execution semantics | Plan endpoint, explicit confirmation, no optimistic move |
| Task state cannot be proven | Display Unknown with evidence diagnostics |
| Event history grows | Existing retention plus indexed projection |
| Comments leak secrets | Treat as untrusted, redact display, scoped access |
| Assignee is confused with runtime agent | Label as operational owner and never modify agent binding |
| Branches look linear | Relation badges/connectors and graph link |
| Mobile lanes become unusable | stacked/scrollable layout and keyboard/menu actions |

## 20. Implemented operator and API reference

The implementation keeps the sources of truth described above and adds no
Kanban database, scheduler, worker, or direct status mutation.

### 20.1 Entry points

- From a Workflow Agent resource menu, choose `Workflow Kanban` for the run
  board.
- From the WorkflowRun inspector, use the `Graph`, `Timeline`, and `Kanban`
  tabs. The embedded Kanban opens the task projection for the selected run.
- Select a run card to open its exact task board. Select a task card to inspect
  dependencies, comments, operational ownership, evidence, and allowed actions.
- `Open in graph` returns to the canonical flow graph and focuses the exact task
  node.

The browser module is `tasks/io/chat_ui/workflow_kanban.js`; its responsive
styles are in `tasks/io/chat_ui/css/52_workflow_kanban.css`. Desktop lanes are
horizontal. On narrow screens, lanes stack vertically and their cards remain
horizontally scrollable. Every drag operation has the `Move / action` keyboard
alternative.

### 20.2 UI actions

All actions use the existing authenticated `/api/ui` dispatch boundary:

| Action | Role | Result |
|---|---|---|
| `workflow_kanban_snapshot` | read | Run or exact-run task projection |
| `workflow_kanban_comment` | write | Durable redacted `kanban_comment` event |
| `workflow_kanban_assign` | write | Durable `kanban_assignment` event |
| `workflow_kanban_attach` | write | Authorized FileStore reference event |
| `workflow_kanban_review` | write | Immutable review/reopen annotation |
| `workflow_kanban_plan_command` | read | Semantic plan or stable rejection code |
| `workflow_kanban_execute_command` | write | Idempotent audited runtime command |

Every request requires `conversation_id`. Run ids are resolved inside that
conversation; an optional agent filter must match; a task id must exist in the
resolved exact flow graph. Comment authors and command actors come from the
authenticated request context, never from client-supplied identity fields.

Run snapshots page through the existing WorkflowRun store with an explicit
positive `limit` and an opaque numeric `cursor`. The UI offers `Load more` and
does not treat its initial page size as retention or a product quota.

### 20.3 Commands and audit

The browser always calls `workflow_kanban_plan_command` before execute. It shows
the semantic action and asks for confirmation before execution mutations. Cards
do not move optimistically.

Implemented runtime mappings are:

- retryable failure to running: existing durable checkpoint retry;
- active run to done: existing graceful cancellation path, explicitly described
  as cancellation rather than completion;
- explicit force stop: existing immediate force-stop path;
- waiting work to running: open the exact existing durable interaction;
- all direct/arbitrary lane writes: rejected.

Terminal runs and completed tasks are immutable. Tasks with unfinished parents
are rejected with their blocking parent ids. A task without a reviewed manual
start signal is informational only.

Each execute request requires a UUID idempotency key. The existing immutable run
event journal records `kanban_command_requested` followed by
`kanban_command_succeeded` or `kanban_command_rejected`. A completed duplicate
returns the original recorded result and does not repeat the runtime mutation.

### 20.4 Live convergence, retention, and redaction

Comment, assignment, and command outcomes append a run event, then publish
`workflow.kanban.updated` through `ConversationEventBus`. The SSE payload contains
only conversation/run/task/event ids and a timestamp. Visible boards treat it as
an invalidation and reload a fresh projection; they never apply it as a state
patch. Existing `workflow_progress` events do the same.

Comments, assignments, and audit records follow WorkflowRun event retention and
are deleted with the run. Comment text is bounded, treated as untrusted, passed
through the run-inspector redaction policy, and escaped by the browser. Artifact
projection exposes only kind, id, and label.

Human waits are selected by their exact `workflow:<run_id>` instance ids through
the indexed `ConfirmationStore.list_waits_for_instances` query. The projection
does not scan or truncate a global wait inbox before correlating a run.

### 20.5 Verification

Focused coverage lives in `tests/test_workflow_kanban.py`,
`tests/test_workflow_run_store.py`, and `tests/test_workflow_agent_ui.py`. It
covers every canonical run state, task evidence, branch/join relations, human
waits, comments and assignment, terminal immutability, dependency blocking,
idempotence, conversation scoping, authenticated actor identity, static browser
contracts, live invalidation, JavaScript syntax, and mobile scrolling.

## 21. G5 collaboration depth

G5 keeps the same projection-first architecture. It does not add a board table,
dispatcher, worker, attachment store, or alternate workflow definition.

### 21.1 Artifact-backed attachments

`workflow_kanban_attach` accepts a FileStore `file_id` only after
`FileStore.get_metadata_required()` validates the authenticated user and exact
conversation. The immutable `kanban_attachment_added` event stores the reference,
label, actor, task scope, UUID, and timestamp, but never copies file bytes.

Authorization is checked again whenever a board is projected. A deleted,
expired, moved, or no-longer-authorized file silently disappears from that
actor's projection even though the audit event remains. Browser links use the
normal authenticated `/files/<file_id>` boundary.

### 21.2 Dependency and review/reopen visibility

`kanban_review` records `approved`, `changes_requested`, or `reopened` with an
authenticated reviewer, optional redacted comment, UUID, and timestamp. Reopen
is legal only after an approved review. The full history is visible on the card.

Review state is collaboration evidence, not execution state. It never changes a
lane, rewinds a completed task, mutates a FlowDefinition, or reopens a terminal
WorkflowRun. A reopened or changes-requested parent is exposed to its children as
a review dependency warning while runtime dependency state remains derived from
the graph and task events. Actual re-execution uses an existing safe retry or a
new reviewed proposal/replay.

### 21.3 Worker and agent diagnostics

Run and task cards join the process-resident
`WorkflowAgentRuntime.active_snapshot()` by `workflow_run_id`. Only redacted
operational fields are exposed: agent, turn, runtime kind, status, duration, live
ownership, current/stale generation, and the existing `force_stop` command. The
message preview is deliberately excluded. Force stop still goes through the
plan/execute allowlist and before/after command audit.

Every write now requires both a UUID idempotency key and the run generation from
the snapshot. If the generation changed between display and action, the server
returns `stale_generation` before appending or executing anything.

### 21.4 Projected projects and saved views

A project is derived from the exact `flow_ref.scope` plus `flow_ref.name`. It is
not stored as a board entity. The server returns the projects represented by the
current projection; browser filters can combine project, free text, and hidden
terminal lanes.

Named views are presentation preferences stored in browser `localStorage`,
scoped by conversation and workflow agent. They contain only filter values and
never card state, ordering, assignments, or execution status.

### 21.5 Reviewed specification and decomposition

The detail drawer offers `Decompose into reviewed proposal`. It sends an explicit
`/plan` request through the existing proposal path. The result is a canonical
FlowDefinition draft governed by `WorkflowProposalStore`, planner/user review,
revision digests, Flow Editor approval, and the existing publish/run lifecycle.
Kanban does not persist a specification or create ad-hoc tasks itself.

### 21.6 G5 acceptance evidence

- Every lane still derives from WorkflowRun, graph, wait, or immutable run-event
  evidence.
- Attachments are re-authorized per actor and conversation on every projection.
- Review and reopen are auditable annotations and cannot mutate runtime state.
- Runtime diagnostics are snapshots, not copied worker state.
- Unsupported lane transitions remain absent/disabled and the UI explains why.
- Comments, assignments, attachments, reviews, and execution commands are
  authenticated, generation-fenced, idempotent, redacted, and SSE-invalidated.
- Tests cover attachment denial, review dependency invalidation, diagnostic
  redaction, stale generation, projects, paging, live refresh, mobile layout, and
  keyboard-equivalent actions.

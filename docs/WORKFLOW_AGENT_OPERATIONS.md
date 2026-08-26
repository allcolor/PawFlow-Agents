# Workflow Agent Operations

This runbook covers staged enablement, migration, inspection, recovery, and the
Wiki Agent maintenance cutover. Workflow agents and automatic Wiki cutover are
separate server-owned capabilities.

## Preconditions

- Back up the PawFlow data directory, including conversation PendingQueue JSONL,
  the workflow run/inbox SQLite stores, and project wiki data.
- Confirm the exact flow and agent definition are installed and visible in the
  intended scope. Production Wiki maintenance pins
  `pawflow.agents.wiki:1.0.0`.
- Bind concrete LLM services and relay authority; do not use `latest` or
  request-supplied service snapshots.
- Run the focused workflow-agent, inbox, Wiki, authorization, and UI gates, then
  the full suite before production cutover.

## Configure workflow agents

Workflow agents are a permanent runtime capability and require no feature flag
or restart. Existing LLM and external agents retain their own runtime paths.

Add the agent through Resources or the conversation agent dialog. Select an
exact compatible flow version, bind every required parameter/service, select a
supported preemption policy, and set only the budgets the workflow actually
needs. Workflow runs have no implicit timeout, deadline, cost budget, call
budget, FlowFile budget, fanout budget, or pass count. For
`max_duration_seconds`, `max_llm_calls`, `max_flowfiles`, `max_fanout`,
and `max_cost_usd`, an omitted value or `0` means unlimited. Only an explicit
positive value configured by the user activates that limit. An unlimited
`max_duration_seconds` is passed to the executor as `None`, never as an
immediate timeout. The run otherwise continues until success, an explicit Stop,
or a real error. Save invokes the same
strict server validator used by Flow Editor publication.

`repeat_until` follows the same rule for `max_iterations`,
`max_duration_seconds`, and `iteration_timeout_seconds`: omitted or zero is
unlimited, while a positive authored value activates that one bound. Its child
task attempts are unlimited by default and remain interruptible through the
workflow cancellation event.

Declarative `for_each` is naturally finite from its input collection. Its
`max_iterations`/`max_flowfiles`, `max_duration_seconds`, and
`max_accumulated_bytes` fields therefore also default to zero (unlimited); only
positive authored values activate rejection or exhaustion. The underlying
Split JSON, loop guard, and Merge Content processors preserve the same rule.

An optional workflow service shown as `Disabled` has no binding. The empty
reference is omitted from the saved binding, so the workflow must explicitly
skip the corresponding optional stage. For example, the Wiki Agent skips its
review stage when `reviewer_llm` is disabled. Required service references never
offer an automatic empty choice.

When the Wiki reviewer is enabled, any validated issue or suggested correction
routes directly back to the writer. Only a clean review can reach the apply
stage; revision passes are unlimited unless the user configured a positive
workflow limit.
Retryable Workflow Agent task failures likewise retry until success or explicit
Stop by default. A positive `retry_attempts` value is the only per-task retry
ceiling; `0` means unlimited.

The Wiki Agent's extractor, writer, and optional reviewer can select either a
direct LLM service or a Summarizer. At run acceptance, a direct LLM is frozen as
selected; for a Summarizer, PawFlow resolves its linked `llm_service` and freezes
both service revisions in the run snapshot. Workflow LLM tasks always call the
effective LLM directly; they never call the Summarizer wrapper.

Each task boundary also re-evaluates user follow-ups recorded in the workflow
run's authorization lineage. A newer independent turn has its own lineage and
does not replace the immutable authority accepted for an already-running turn.

At the start of a new conversation run, PawFlow revalidates the saved workflow
configuration against the currently visible exact flow resource. If an
operator replaced that version in place, the new run receives the new digest
and a fresh validated binding. Existing, waiting, retrying, and recovering runs
keep their durable binding and still fail closed if their pinned resource
changes.

Tool-enabled Workflow tasks execute through ephemeral provider conversations.
Those internal `::workflow::` conversations inherit the parent conversation's
conversation-wide and agent-specific relay bindings. An explicit workflow relay
therefore remains available to filesystem and Desktop tools without enabling
host-local execution; the relay's configured `local` mode is unchanged.

## Migrate one PendingQueue

Migration is explicit and per conversation/agent:

```python
from core.pending_queue import PendingQueue

result = PendingQueue.migrate_agent_to_inbox("conversation-id", "agent-name")
print({key: result[key] for key in ("migrated", "count", "sha256")})
```

The migration hashes and imports the legacy JSONL transactionally, validates the
item count, records a durable migration receipt, and renames the source to
`*.jsonl.migrated` only after success. Repeating it returns the existing
receipt and imports nothing. A changed legacy file after a recorded migration is
an error; stop and investigate instead of deleting either copy.

After migration, verify the expected pending count and send one canary message.
Do not bulk-migrate all agents until the representative canary completes,
acknowledges only its answered turn IDs, and survives a restart.

Conversation turns are deduplicated by their root message ID. While one run is
active, the process keeps at most 20 distinct successors per agent as a fast
cache; any further conversation turns remain only in the durable inbox and are
drained in sequence after cached work. Bound automation and child-flow
submissions are not backed by that inbox, so a full cache rejects them explicitly
instead of dropping work. A growing process-resident pending list therefore
indicates an old runtime or a broken deployment, not normal backlog behavior.

## Observe and inspect

The conversation-scoped agent-resource actions are:

| Action | Access | Required body | Purpose |
|---|---|---|---|
| `workflow_operations` | read | `conversation_id` | Health, redacted counters, usage, inbox states, and stable alerts; optional `agent_name`/`backlog_alert`. |
| `list_workflow_runs` | read | `conversation_id` | Newest redacted runs; optional `agent_name`/`limit` (maximum 200). |
| `inspect_workflow_run` | read | `conversation_id`, `run_id` | Redacted run state and ordered lifecycle events. |
| `retry_workflow_run` | write | `conversation_id`, `run_id` | Reacquire a crashed live run or resume the current `retryable_failed` generation from its exact task checkpoint. |
| `delete_workflow_run` | write | `conversation_id`, `run_id` | Delete one terminal run and its cascaded technical state. Active and non-terminal runs are rejected. |

The UI exposes list/inspect/retry/delete from a workflow agent's context menu.
Its history column requests the 25 newest runs; older durable runs remain stored
until an explicit terminal-run deletion or an operator-configured retention
cleanup. The
inspector refreshes immediately on matching `workflow_progress` events and uses
a bounded visibility-aware polling fallback. It follows the newest run until an
operator selects an older one and highlights the selected row. The graph marks
the latest authorized flow task as current even when task-internal telemetry uses
a runtime class name instead of the flow task ID. Its primary view shows the
current step and total, a progress bar, current activity, latest redacted return,
and any error. Flow blocks, usage, terminal commit state, and the full ordered
event history remain available in collapsed technical details. Opening those
details survives background refreshes of the selected run. Treat a retry HTTP 409 as
authoritative: the run is terminal, superseded, unsafe, already has a live
worker, or another worker won the recovery race. Never create a replacement run
merely to hide that conflict; inspect the durable run and inbox state first.

The primary inspector also shows a live execution timeline for redacted
assistant messages, tool-call names and arguments, tool results, and errors.
Values are bounded before durable event storage; sensitive-named fields and
resolved secret values are redacted, while image/base64 payloads are omitted.
System prompts, hidden reasoning, and reasoning signatures are never recorded.

Durable interaction tasks in an exact-version Workflow Agent use the task ID
injected by that run's executor for their idempotency identity. The global
continuous-flow registry remains only a fallback for conventionally deployed
flows; an exact-version task must not depend on registry membership to ask for
confirmation or typed user input.

### Error and retry semantics

`inspect_workflow_run` exposes a redacted structured `error` with `code`,
`message`, `retryable`, `task_id`, and timestamp. The runtime handles failures as
follows:

- `retryable_failed` is non-terminal. The run still owns its generation and inbox
  claim, consumes no worker, and survives server restart. `safe_retry=true` means
  an exact pre-attempt FlowFile and task ID are durably available.
- Retry resumes the same `run_id` at the failed task. Run-cached and keyed-effect
  tasks therefore reuse the same cache/idempotency boundary. A second concurrent
  retry receives HTTP 409 and cannot start another worker.
- `failed`, `timed_out`, `budget_exceeded`, `recovery_failed`, unsafe-task,
  authorization, invalid-binding, and uncheckpointed errors are terminal or not
  automatically replayable. Correct the cause and submit a new user turn only
  when the operator intentionally wants a new generation.
- A conversation run that reaches a terminal failure discards its claimed or
  still-pending ingress message before releasing the worker. The poller must
  never turn one deterministic error into successive run generations.
- A provider job already marked `submitted` without a durable provider recovery
  result remains non-retryable. PawFlow fails closed rather than submitting it a
  second time.
- Cancel and force-stop work from `waiting` and `retryable_failed` exactly as they
  do from a running generation. When an explicit deadline exists, time paused for
  user input or operator retry is excluded from it; runs without one remain
  unlimited.

After retry, inspect ordered `error` and `retrying` events and verify that the
recovery count increased once, the run ID and generation did not change, and no
duplicate provider job or terminal transcript row appeared.

Alert meanings:

| Code | Severity | First response |
|---|---|---|
| `workflow_failed_runs` | warning | Inspect the newest failed run and its redacted authorization/progress events. |
| `workflow_overdue_runs` | critical | An explicitly bounded run exceeded its deadline; inspect worker ownership and recovery eligibility. Unlimited runs never raise this alert. |
| `workflow_recovery_churn` | warning | Investigate repeated process loss or deterministic task failure before retrying. |
| `workflow_inbox_backlog` | warning | Compare ingress rate, active runs, and configured backlog threshold. |
| `workflow_expired_claims` | critical | Verify startup reconciliation and recover/release only through the durable runtime. |

## Media Studio canary

Install `pawflow.media-studio:1.0.0`, bind the exact
`pawflow.agents.media-studio:1.0.0` flow, select a concrete `creative_llm`, and
leave optional media preferences empty unless the canary requires one. The flow
has no activation flag.

Validate these branches before broad use:

1. An unrelated request terminates before project, FileStore, or service access.
2. Image, video, ComfyUI audio, generic audio, and ordinary speech each select an
   exact frozen capability and return owner-scoped FileStore artifacts.
3. Missing material fields produce one grouped durable form and resume after a
   reconnect without duplicating the question.
4. Composite work shows the exact scenario digest and cannot produce after
   Revise or Cancel.
5. Voice cloning does not call a provider without explicit durable authorization.
6. FFmpeg composition accepts only a closed recipe and rejects shell, arbitrary
   arguments, traversal, or a changed service definition.
7. Retrying a completed run reuses its durable provider result; an orphaned
   submitted job fails closed instead of resubmitting.
8. Modification appends a child revision and leaves the prior artifact intact.
9. An explicit/default/unique relay is frozen before capability discovery;
   several linked relays produce one durable choice, and zero linked relays stop
   before project or provider access.
10. A multi-shot scenario never exceeds `max_fanout`, runs no more than four
    provider submissions concurrently, and joins every correlated artifact
    before validation and revision commit.

Inspect the run through `workflow_operations` and `inspect_workflow_run`. Record
the exact flow digest, service revisions, package versions, artifacts, and test
results. A ComfyUI model, LoRA, custom-node, or workflow change must go through
the operator package's reviewed provisioning plan; publish a new immutable
preset/service revision and never mutate the active revision in place.

## Website Creator canary

Install `pawflow.website-creator:1.0.0`, bind the exact
`pawflow.agents.website-creator:1.0.0` flow, select a vision-capable
`creator_llm`, and bind a concrete/default/sole linked relay that provides the
Chromium desktop and filesystem access. Requests must provide two
public HTTP(S) URLs, either through `source_url` and `template_url` parameters
or in the user message.

Managed Desktop relays always enable automation inside their relay container.
This is independent from server-local execution and host-screen access, which
remain disabled unless separately and explicitly authorized.

The first version supports self-contained static HTML/CSS/JavaScript sites. It
does not expose shell, test-code execution, arbitrary patch paths, package
installation, Git, deployment, Playwright/headless navigation, or private/local
URLs. Every run writes only below
`/workspace/pawflow-sites/<run_id>` (or the configured absolute
`workspace_root`).

Validate these branches before broad use:

1. Localhost, private IP literals, credentials in URLs, and hostnames resolving to
   any private address fail before desktop, filesystem, or network access.
2. Exploration calls both `screen` and `see` for the source and template;
   `fetch` is supplementary and cannot satisfy the visual-observation gate.
3. The proposed source-to-template mapping is shown in one durable form. Reject
   stops without project writes; approve continues after reconnect without
   duplicating the request.
4. Generation stays inside the stable run workspace; relative traversal and
   non-workspace `file://` previews fail closed.
5. Final review uses the visible desktop and vision. A reviewer verdict with
   `passed=false` returns directly to correction without asking the user to
   approve known-bad work. A passed review opens the user decision; Accept
   terminates and Revise may repeat correction and review as many times as the
   user requests. No implicit pass count, timeout, or deadline applies; only an
   explicitly configured limit or explicit Stop may interrupt the loop.
6. The terminal result contains one workspace artifact and reports tool-call and
   correction-pass metrics.

Inspect the exact run, confirm both durable waits survive a server restart, and
verify that cancellation, deadline, service-revision, authorization, and
workspace-boundary failures remain fail-closed.

See [Website Creator Workflow Agent](WEBSITE_CREATOR_WORKFLOW_AGENT.md) for the
supported template catalogs, license constraints, version 1 scope, and chat
test procedure.

## Wiki shadow and cutover

Interactive Wiki Agent turns first run one strict intent-classification LLM call.
Only requests entirely dedicated to inspecting, auditing, documenting, or
maintaining the project wiki reach the relay scan. General coding, UI, debug,
deployment, and mixed requests stop before project access with a short response
directing the user to a general-purpose agent. The accepted user request can
focus the extraction/writer stages and reduce the configured batch size, but it
cannot change relay/root/service bindings, permissions, or `write_mode`.
Wiki source scans, extraction batches, message checkpoints, LLM output, and
review revisions are unlimited by default. A positive `max_files` or
`batch_files` value is enforced only when the user configured it; `0` means
unlimited.
Media Studio provider-cost selection follows the same rule: its default is `0`
(no ceiling), while a positive user-configured `max_cost_usd` remains a hard
selection policy.

Automatic routing is independent of whether a Wiki Agent happens to be a member
of the conversation. With `PAWFLOW_WIKI_WORKFLOW_CUTOVER=1`, the scheduler binds
the exact Wiki flow and submits the explicit maintenance request `Refresh the
project wiki from pending relay changes.` as `silent_maintenance`. Without the
flag, it runs the embedded legacy maintainer as before. The workflow path uses
separate intent, extraction, writer, and optional reviewer steps plus durable run
records; the legacy path uses one embedded auto-update LLM orchestration and has
no interactive intent contract.

1. Keep `PAWFLOW_WIKI_WORKFLOW_CUTOVER` unset. Run representative repositories
   with a Wiki binding whose `write_mode` is `shadow`.
2. Compare safety outcomes with the legacy maintainer: selected dirty sources,
   validation/rejection outcome, superseded detection, candidate page set,
   citations/current hashes, and no-change behavior.
3. Prove shadow invariants: no page write, dirty-source acknowledgement, patch
   receipt, assistant transcript row, or unsolicited `done` event.
4. Repeat across no-change, valid change, invalid model output, source mutation
   during the run, optional reviewer rejection, restart, and force-stop cases.
5. Record the exact flow FQN/digest, service revisions, repository fixtures,
   outcomes, and full-suite result as cutover evidence.
6. Set `PAWFLOW_WIKI_WORKFLOW_CUTOVER=1` and restart/redeploy. Automatic
   maintenance now submits that exact flow as `silent_maintenance`; interactive
   Wiki Agent turns use the same flow version.
7. Watch `workflow_operations`, dirty-source counts, logs, and transcript/SSE
   canaries through at least one complete maintenance window.

The writer proposes pages only. `processed_sources` is derived exactly from the
validated selected snapshot before patch validation, preventing invented paths
from being acknowledged while retaining strict citation checks for every page.

The scheduler fails closed when the authenticated authority, selected agent,
relay, summarizer service, or exact binding is absent or changed. Do not work
around that check by weakening the binding.

## Rollback and incident response

Remove `PAWFLOW_WIKI_WORKFLOW_CUTOVER` and restart/redeploy to route future
automatic maintenance back to the legacy scheduler. This temporary cutover
flag does not control interactive workflow agents and removing it does not
rewrite completed workflow records or wiki data.

For an incident:

1. Preserve run/inbox databases, logs, exact flow/service revisions, and any
   `*.jsonl.migrated` source.
2. Stop new automatic cutover submissions.
3. Inspect the scoped operational projection, then the specific run.
4. Retry only when `safe_retry` is true; otherwise let startup reconciliation
   or the normal successor-generation path resolve ownership.
5. Verify there is exactly one assistant terminal row for interactive runs,
   none for `silent_maintenance`, no duplicate usage event, and no lost or
   over-acknowledged inbox message.
6. Re-run the recovery fault-injection and full-suite gates before re-enabling
   cutover.

Do not remove the embedded legacy Wiki orchestration until representative shadow
evidence, stress/fault tests, full-suite CI, and manual webchat plus non-HTTP
transport validation all pass.

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

## Enable workflow agents

Set `PAWFLOW_WORKFLOW_AGENTS_ENABLED=1` in the server environment and restart
or redeploy. Invalid boolean values fail closed. Leaving it unset keeps
workflow-agent definitions and existing LLM/external agents on the legacy path.

Add the agent through Resources or the conversation agent dialog. Select an
exact compatible flow version, bind every required parameter/service, select a
supported preemption policy, and set finite run limits. Save invokes the same
strict server validator used by Flow Editor publication.

An optional workflow service shown as `Disabled` has no binding. The empty
reference is omitted from the saved binding, so the workflow must explicitly
skip the corresponding optional stage. For example, the Wiki Agent skips its
review stage when `reviewer_llm` is disabled. Required service references never
offer an automatic empty choice.

The Wiki Agent's extractor, writer, and optional reviewer can select either a
direct LLM service or a Summarizer. At run acceptance, a direct LLM is frozen as
selected; for a Summarizer, PawFlow resolves its linked `llm_service` and freezes
both service revisions in the run snapshot. Workflow LLM tasks always call the
effective LLM directly; they never call the Summarizer wrapper.

Each task boundary also re-evaluates user follow-ups recorded in the workflow
run's authorization lineage. A newer independent turn has its own lineage and
does not replace the immutable authority accepted for an already-running turn.

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

## Observe and inspect

The conversation-scoped agent-resource actions are:

| Action | Access | Required body | Purpose |
|---|---|---|---|
| `workflow_operations` | read | `conversation_id` | Health, redacted counters, usage, inbox states, and stable alerts; optional `agent_name`/`backlog_alert`. |
| `list_workflow_runs` | read | `conversation_id` | Newest redacted runs; optional `agent_name`/`limit` (maximum 200). |
| `inspect_workflow_run` | read | `conversation_id`, `run_id` | Redacted run state and ordered lifecycle events. |
| `retry_workflow_run` | write | `conversation_id`, `run_id` | Reacquire only the current safely recoverable generation. |

The UI exposes list/inspect/retry from a workflow agent's context menu. Treat a
retry HTTP 409 as authoritative: the run is terminal, superseded, unsafe, or
another worker won the recovery race. Never create a replacement run merely to
hide that conflict; inspect the durable run and inbox state first.

Alert meanings:

| Code | Severity | First response |
|---|---|---|
| `workflow_failed_runs` | warning | Inspect the newest failed run and its redacted authorization/progress events. |
| `workflow_overdue_runs` | critical | Stop new cutover work; inspect deadline, worker ownership, and recovery eligibility. |
| `workflow_recovery_churn` | warning | Investigate repeated process loss or deterministic task failure before retrying. |
| `workflow_inbox_backlog` | warning | Compare ingress rate, active runs, and configured backlog threshold. |
| `workflow_expired_claims` | critical | Verify startup reconciliation and recover/release only through the durable runtime. |

## Wiki shadow and cutover

Interactive Wiki Agent turns first run one strict intent-classification LLM call.
Only requests entirely dedicated to inspecting, auditing, documenting, or
maintaining the project wiki reach the relay scan. General coding, UI, debug,
deployment, and mixed requests stop before project access with a short response
directing the user to a general-purpose agent. The accepted user request can
focus the extraction/writer stages and reduce the configured batch size, but it
cannot change relay/root/service bindings, permissions, or `write_mode`.

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
automatic maintenance back to the legacy scheduler. This does not rewrite
completed workflow records or wiki data. Keep
`PAWFLOW_WORKFLOW_AGENTS_ENABLED` enabled if interactive workflow agents still
need to run; disable it separately only when that broader rollback is intended.

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

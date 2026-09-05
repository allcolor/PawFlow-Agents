# Server-side performance implementation (after the beta.269 audit)

This document records the server (Python) changes that implement the server
items of `docs/PERFORMANCE_AUDIT_BETA269.md`. The UI items (streaming
coalescing, startup resources, OpenSpace visibility, retained DOM) are a
separate change set owned by the UI work and are not described here.

Baseline: `b6229fe687b1507effb2f72b968b31e4682ed624` (beta.269). No storage
format, API shape or durable data changed. Every change is reversible by
reverting the listed files.

## Summary

| Area | Change | Measured effect (isolated bench, medians of 5) |
| --- | --- | --- |
| Deep history cursor | `load_page(before_msg_id=...)` resolves the cursor and reads the page in one reverse pass | 12,000-row transcript, cursor 10,000 back, 50 rows: **164.8 ms to 75.6 ms**; rows decoded **20,070 to 10,070** |
| SSE replay buffer | Global expiry sweep runs at most once per second instead of on every buffered publish | 5,000 idle buffered conversations, 200 publishes: **102.2 ms to 0.95 ms** |
| Conversation search index | Full-table `UPDATE messages SET title` skipped when the stored title is unchanged | 20,000 indexed rows: **10.5 ms per refreshed conversation to 0** (the statement is a `SCAN messages VIRTUAL TABLE`) |
| Project maintenance | Graph rebuild and source hashing scan run on the lazy cadence only; a ready wiki backlog gets process-only runs | Removes one full-tree hash per turn while a backlog persists (path confirmed in code; live CPU share not measured) |
| Tool batches | Worker threads per batch capped (default 8, `PAWFLOW_TOOL_BATCH_MAX_WORKERS`) | Bounded thread fan-out per turn; ordering, cancellation and backgrounding preserved |
| Conversation writer | Backlog instrumentation: queue depth, in-flight batch, oldest pending age, enqueue-to-persist latency, threshold warnings | Measurement only; queue stays unbounded and non-blocking |

The benchmark numbers come from `PERFORMANCE_SERVER_IMPLEMENTATION_EVIDENCE.json`
(script and raw output). They compare the current code with an inline copy of
the former algorithm on the same fixture, on the MyWorkspace relay container
(Python 3.12.3). They are not end-to-end latencies.

## 1. Single-pass deep history

File: `core/_conversation_store_transcript.py`.

Before, a cursor page ran two reverse scans over the transcript:
`_offset_after_msg_id` decoded every row newer than the cursor to compute the
offset, then `_read_tail` decoded those same rows again plus the page. The new
`_read_tail_before_msg_id` does both in one pass:

- rows at or after the cursor are only counted (the count is the resolved
  `offset` in the response, identical to the former value);
- rows before the cursor are collected exactly as `_read_tail` collects a tail
  (`limit + 20` display rows, then technical child rows are kept with their
  assistant anchor);
- `trace_update` rows are append-only and therefore newer than their anchor.
  Updates met before the cursor is found are kept until their anchor is seen:
  if the anchor is inside the page they are merged into it, if the anchor is
  itself newer than the cursor they are discarded. A discarded update can
  never keep the scan open, so an anchor newer than the cursor does not force
  a full-file read.

When the cursor is not in the transcript (deleted, truncated, foreign id) the
method returns `None` and `load_page` falls back to the numeric `offset`, as
before. `_offset_after_msg_id` was removed; nothing else used it.

Correctness coverage (`tests/test_performance_server_paths.py`):
equivalence with the former two-pass result for every cursor and several page
sizes on a transcript mixing user/assistant rows, tool rows, trace anchors and
trace updates; trace updates newer than the cursor reaching an older anchor;
unknown cursor fallback; `patch_message` and `truncate_after_msg_id` rewrites;
`has_more` at the start of the transcript; a decoded-row bound of
`offset + limit + 20` with exactly one reverse iteration.

Not done (audit step two): a msg-id to byte-position index. The single pass
still decodes the rows newer than the cursor once. The latest page path is
unchanged and was already fast (1.2 ms in the bench).

## 2. Amortized replay-buffer expiry

File: `core/conversation_event_bus.py`.

`publish()` buffers an event when a conversation has no live subscriber and
used to call `_cleanup_expired_buffers()` under the bus lock on every such
publish. That sweep walks every buffered conversation, so publish cost grew
with the number of idle buffered conversations. Now `_maybe_cleanup_expired_buffers`
runs the sweep at most once per `_BUFFER_SWEEP_INTERVAL` (1 s). The cadence
is measured on `time.monotonic()`, so a wall-clock correction can neither
suppress nor multiply sweeps; event ages and the TTL keep using wall time.

Invariants kept:

- `_fresh_buffered` still filters by age at subscribe time, so an expired
  event is never replayed regardless of when the sweep last ran (existing
  tests `test_expired_buffered_event_is_never_replayed` and siblings).
- `_MAX_BUFFER` still caps the buffer being appended to.
- Memory for conversations nobody publishes to any more is reclaimed at most
  one interval later than before.

`buffer_stats()` reports buffered conversations, buffered events and the age
of the last sweep for capacity measurement.

## 3. Project maintenance: scan cadence separated from backlog processing

File: `core/project_maintenance.py`.

Before, any pending wiki source let `schedule()` bypass the 60 s lazy
interval, so every turn re-ran the AST graph build and the full source hash
scan for as long as a backlog existed (including a backlog that was blocked or
deferred and could not be processed at all).

Now:

- A scan (graph rebuild plus `scan_from_relay`) is due when a write forces it
  (`force` or `changed_path`) or when `last_scan_at` is older than
  `_LAZY_REFRESH_SECONDS`.
- Otherwise a run is scheduled only when the wiki backlog is ready: dirty
  sources minus blocked minus deferred is positive (`_wiki_backlog_ready`),
  and at least `_BACKLOG_PROCESS_SECONDS` (10 s) have passed since the last
  run. Such a run is process-only: `job.scan` is false, `_run` skips the graph
  and the scan and goes straight to the wiki update (legacy `auto_update` or
  the workflow submission, unchanged).
- A pending timer that owes a scan is never downgraded by a later
  backlog-only request (`scan_pending` is sticky until the run starts).
- `snapshot()` exposes `last_scan_at`, `runs`, `scans` and `scan_pending`.

The rerun path after a run that saw new writes still schedules a forced scan.

Not done: passing known `(size, mtime)` metadata into the relay scan script to
skip rehashing unchanged bytes. The script receives its input through
environment variables, which cannot carry a manifest of thousands of entries;
that needs a different transport and is left as a follow-up. With the cadence
change a full hash happens at most once per lazy interval per active relay.

## 4. Search index: skip the unchanged-title UPDATE

File: `core/conversation_index.py`.

`_index_conversation` ran `UPDATE messages SET title = ? WHERE
conversation_id = ? AND title != ?` for every refreshed conversation with a
title. `messages` is an FTS5 table and `conversation_id` is `UNINDEXED`, so
the planner reports `SCAN messages VIRTUAL TABLE INDEX 0:`: a full scan of
every indexed row of every conversation, under the index lock, per refreshed
conversation. `refresh()` now passes the stored title and the statement runs
only when the title actually changed and the rows were not purged in the same
refresh. Rows inserted by a refresh already carry the current title, and the
rename path is unchanged (existing test
`test_a_renamed_conversation_reports_its_new_title` plus a statement-count
test).

## 5. Capacity controls and measurement

### Tool batch worker ceiling

File: `tasks/ai/agent_tool_exec.py`.

The per-batch `ThreadPoolExecutor` was sized to the whole batch. It is now
sized to `tool_batch_max_workers(len(tool_calls))` = `min(batch, ceiling)`
with ceiling `PAWFLOW_TOOL_BATCH_MAX_WORKERS` (positive integer; any other
value raises `ValueError`) and 8 by default. Calls past the ceiling queue
inside the pool and start as workers free up. Preserved: result order,
independent parallelism up to the ceiling, user and automatic backgrounding
(a queued call that is backgrounded keeps its future and delivers later),
cancellation. One fix that the ceiling made necessary: a cancelled future that
never started does not run the worker's `finally`, so its background-owner
reservation is released explicitly.

Known consequence: the automatic 5-minute backgrounding is measured from
submission, so a call that waited in the queue can be backgrounded earlier in
its own execution than before. This is the same outcome the user would get by
backgrounding it manually and it is logged.

Not done: fair admission for expensive tools and a shared cross-conversation
limit. Both need cost classes measured under load; the batch ceiling is the
bound this evidence supports.

### Writer backlog instrumentation

File: `core/conversation_writer.py`.

Every queued item is stamped with `_enqueued_at`. After each batch the writer
records items persisted, batch count, last and maximum enqueue-to-persist
latency, and logs `latency_ms=` on its existing batch line. It warns (rate
limited to one warning per 10 s per writer) when the queue depth reaches
`PAWFLOW_WRITER_BACKLOG_WARN_ITEMS` (256) or when a batch's latency reaches
`PAWFLOW_WRITER_BACKLOG_WARN_SECONDS` (5). `backlog_state()` and
`ConversationWriter.backlog_snapshot()` report queue depth, the in-flight
batch (dequeued but not yet durable), oldest pending age and the stats.

The queue remains unbounded and enqueue remains non-blocking: durable messages
are never dropped and HTTP workers are never blocked. Applying pressure
upstream is deliberately not wired: the snapshot is the measurement the audit
asked for before choosing a policy.

The stats are diagnostic approximations, not an admission proof: the snapshot
reads queue depth and the in-flight batch without a lock across writers, so
a producer can enqueue between the read and any action taken on it. An empty
snapshot is a good sign before a restart, not a race-free quiescence guard;
the shutdown handler's writer drain (`ConversationWriter.shutdown_all`) is
what guarantees persistence.

### Retry policy (no change)

`engine/continuous_executor.py` keeps `max_retries=0` (unlimited) by default
and `engine/_continuous_exec_run.py` keeps its capped retry wait. Changing the
default alters durable flow semantics (exhaustion rolls the FlowFile back to
its queue and later scheduling cycles re-run it) for every deployed flow, and
the audit found no measurement showing retry storms in live load. Errors that
set `retryable = False` already stop immediately. This is left for a change
with its own flow-level tests.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `PAWFLOW_TOOL_BATCH_MAX_WORKERS` | 8 | Worker threads per tool-call batch; invalid values raise at dispatch |
| `PAWFLOW_WRITER_BACKLOG_WARN_ITEMS` | 256 | Queue depth that triggers a writer backlog warning |
| `PAWFLOW_WRITER_BACKLOG_WARN_SECONDS` | 5 | Enqueue-to-persist latency that triggers a writer backlog warning |

Module constants without an environment override: `_BUFFER_SWEEP_INTERVAL`
(1 s, event bus), `_BACKLOG_PROCESS_SECONDS` (10 s, maintenance).

## Verification

- New: `tests/test_performance_server_paths.py` (36 tests, including wall-clock corrections and queued-call backgrounding). The final SSE and tool-execution regression run passed 146 tests.
- Existing suites run after the change: `test_conversation_history`,
  `test_sse_streaming`, `test_event_bus_dispatch`, `test_sse_reconnect_contract`,
  `test_read_history_handler`, `test_read_history_is_bounded`,
  `test_conversation_store`, `test_conversation_index`,
  `test_project_maintenance`, `test_project_wiki`,
  `test_conversation_writer_drain`, `test_agent_tool_exec_images`,
  `test_wiki_agent_workflow`, `test_agent_loop`, `test_perf_caches`:
  573 passed.

## Deployment notes

All changed files are Python modules loaded at process start. A hot patch
requires a server restart after the files are replaced; there is no
reload-without-restart path for them. Restart behaviour is unchanged
(`ConversationWriter.shutdown_all` drains the queues first).

Rollback is a file-level revert; no migration in either direction.

## Hotpatch and restart assessment (read-only analysis)

This section assesses a controlled restart of the `pawflow-server` container
after the Python files above (and the UI files of the companion change) are
copied under `/app`. Nothing here was executed against production.

### Shutdown sequence (cli.py `_shutdown`)

On SIGTERM or SIGINT the server: stops every flow executor (3 s budget),
shuts down the Claude Code, Codex and Gemini pools and reaps every container
it spawned (by label), drains every `ConversationWriter` queue (20 s budget),
then calls `os._exit(0)`. A guardian thread force-exits 45 s after shutdown
starts. A second signal force-kills immediately.

**Grace period risk.** `docker-compose.yml` sets no `stop_grace_period`, so
`docker stop` / `docker restart` send SIGKILL after the default 10 s, before
the 20 s writer drain can finish under load. The code comments acknowledge
this. For a controlled restart use an explicit timeout that covers the whole
sequence: `docker restart -t 60 <server container>` (or `docker stop -t 60`
then start). Confirm in the log that `ConversationWriter drain INCOMPLETE`
does not appear.

### What survives a restart

| State | Durability | Recovery at boot |
| --- | --- | --- |
| Persisted transcript rows, extras, memories, KG, diary | On disk | Nothing to do |
| Queued writer items | Drained at shutdown (needs the grace period above) | Boot-recovery wakes agents whose `pending.jsonl` is non-empty |
| Pending user messages not yet drained into a turn | `pending.jsonl` on disk | Woken by boot recovery with a 2 s delay |
| Scheduled wake-ups / continuations | `POLL_SCHEDULE_FILE` JSON | Loaded at start; expired entries are pruned |
| Assigned tasks (`agent_tasks` extras) | On disk | `_reschedule_active_tasks` at poller start, plus a periodic watchdog |
| Workflow-agent runs and inbox leases | `WorkflowRunStore` / `AgentInboxStore` | `_recover_durable_runs` and `_resume_durable_pending` at first runtime access |
| Flow executors | Checkpoints every 30 s when enabled | Flows restart from their stored definition; in-flight FlowFiles roll back to their queues |
| Relays | External processes | Reconnect with exponential backoff (1 s to 60 s); FUSE mounts survive reconnects |
| Browser tabs | SSE `EventSource` | Auto-reconnect; replay buffer is in memory, so events published during the outage are gone, the client reconciles from the transcript |

### What does not survive

- **An LLM turn in flight.** The worker thread dies with the process. The
  user message is already persisted; the partial answer is not. Its pending
  entry was drained when the turn started, so boot recovery does not re-run
  it: the user must send the next message (or a wake-up) to continue. Choose
  a restart moment when no turn is streaming; `ConversationEventBus.active_conversations()`
  and the agent loop's active-turn registry show live turns, and the
  writer's `backlog_snapshot()` shows unpersisted work.
- **Background tools.** `core/background_tool.py` keeps futures in memory
  only. A backgrounded relay command keeps running on the relay, but its
  result can no longer be delivered to the conversation.
- **Spawned provider containers** (Claude Code interactive, Codex, Gemini)
  are reaped on purpose. Interactive sessions restart cold with a rebuilt
  initial context file on the next turn (see `docs/CLAUDE_CODE_INTERACTIVE.md`).
- **In-memory replay buffers, listener lanes and per-process caches.**

### Recommended controlled path

1. Copy the verified files under `/app` and check their SHA-256 against the
   reviewed hashes; keep a copy of the replaced files for a file-level
   rollback.
2. Wait for a quiet point: no streaming turn, no background tool the user is
   waiting on, writer backlog empty.
3. Restart with an explicit grace period of at least 60 s. The quiet-point
   check is advisory; only the shutdown drain guarantees that queued writes
   reach disk.
4. Verify: container `StartedAt`, `/health`, the boot-recovery and
   poll-scheduler log lines, the served chat assets, and one
   `load_page(before_msg_id=...)` request through the UI (deep history).
5. Watch the log for `persistence backlog` warnings and
   `[agent-tool] batch of N tool call(s) admitted through M worker(s)` to
   confirm the new modules are the ones running.

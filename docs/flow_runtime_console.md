# Flow Runtime Console

The Flow Runtime Viewer (`flow_graph.html`, ReactFlow + dagre, 3 s poll) is
also a NiFi-style operations console for RUNNING deployed instances.
Templates and `flow_ref` drill-downs stay read-only (topology + parameters);
runtime controls appear only on the root graph of a running instance.

## Engine primitives (Phase 1)

- Every `Connection` carries a stable identity:
  `connection_id = conn_<source>__<relationship>__<target>` —
  `A --success--> B` and `A --failure--> B` are different queues and every
  runtime operation addresses `(instance_id, connection_id)`.
- `Connection.pause()/resume()/is_paused()`: a paused queue keeps ACCEPTING
  upstream FlowFiles but blocks downstream consumption (it may reach
  backpressure — the point is: pause → observe/inspect → resume).
- The scheduler is pause-aware end to end: `has_processable()` drives
  `has_input`, the pending count skips paused queues (no useless workers),
  and queue-aware tasks (`select_processable`) only see non-paused
  connections, so `peek`/`remove` cannot bypass a pause.
- FlowFiles are addressed by `process_id`, never by index
  (`get_flowfile(process_id)`, `remove_by_process_id`).

## Runtime API (`tasks/ai/actions/flow_runtime.py`)

Same authorization gate as `start/stop/undeploy_flow` (instance owner;
admin for global instances). All actions require the instance to be
running (409 otherwise).

| Action | Purpose |
| ------ | ------- |
| `flow_runtime_task_control` | `operation=start\|stop\|restart\|disable` on one task |
| `flow_runtime_task_details` | state + stats + RAW config (expressions and `${secret}` references NEVER resolved) + parameter schema |
| `flow_runtime_queue_list` | queue stats + server-side page (`offset`, `limit` ≤ 100) of `{process_id, size, created_at, attr_count}` |
| `flow_runtime_queue_control` | `pause` / `resume` / `clear` — clear drops FlowFiles ONLY (no implicit task reset), checkpoints immediately |
| `flow_runtime_queue_item` | attributes + content preview (UTF-8 ≤ 32 KB; binary/large → metadata) ; a consumed item answers `no_longer_queued` (normal, not an error) |
| `flow_runtime_flowfile_drop` | remove one FlowFile by `process_id`, checkpoint immediately |

Content download is a dedicated streaming route (session-authenticated,
ownership re-checked):

```text
GET /api/flow-runtime/{instance_id}/queues/{connection_id}/flowfiles/{process_id}/content
```

backed by `FlowFile.get_content_stream()` — a large FlowFile never transits
through JSON/base64 or server RAM.

**Safety**: every mutation (`clear`, `drop`) writes a checkpoint
immediately so a crash cannot resurrect dropped FlowFiles from a stale
checkpoint, and every manual operation (`task_start/stop/restart`,
`queue_pause/resume/clear`, `flowfile_drop`) is recorded in the flow's
provenance repository with the acting user. Checkpoints key every queue by
`(source, target, relationship)` and recovery restores it only into that exact
`connection_id`, so `A --success--> B` and `A --failure--> B` never merge after
a crash.

## Enriched graph (`flow_runtime_graph`)

Edges now carry `connection_id`, `queue_bytes`, `max_queue_bytes`,
`paused`, `flowfiles_in/out`, `ttl`; running-instance nodes carry
`controllable: true`.

## Viewer UI

- Edges read as queues: `relationship · N · size` always; grey when empty,
  green/orange/red as they fill; `⏸` + dashed grey + NO animated current
  when paused; `🔴 (n/max)` under backpressure.
- Right-click on a task: Start / Stop / Restart (on error) /
  ⚙ Configuration (read-only drawer: state, stats, raw parameters).
- Click on an edge: the Queue Inspector drawer — stats, Pause/Resume,
  Empty queue (explicit `cannot be undone` confirmation with count and
  size), paginated FlowFile list, per-FlowFile attributes + content
  preview + streaming Download + Drop.
- Openspace mirrors the state on the 3D flow stage: paused links grey out
  and their dots stop; backpressure stays red.

Runtime control of tasks/queues inside nested process groups
(`group_path`) is a later phase; hot-swap task editing (`update_task`)
stays out until the read-only drawer has mileage.

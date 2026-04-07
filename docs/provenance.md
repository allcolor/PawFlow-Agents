# Provenance System - PawFlow

PawFlow's provenance system allows tracing the complete lifecycle of each FlowFile through the processing pipeline. It records all significant events to enable auditing, debugging, and data lineage reconstruction.

---

## Objectives

1. **Complete traceability**: Know where each FlowFile has been and what happened to it
2. **Audit**: Retain processing history for compliance
3. **Debugging**: Trace errors back to their source
4. **Lineage**: Understand parent/child relationships between FlowFiles
5. **Performance**: Statistics per task and per flow

---

## Provenance Event Types

PawFlow defines **7 event types**:

| Type | When | Context |
|------|------|---------|
| **CREATE** | Initial creation of a FlowFile | Flow entry |
| **RECEIVE** | A task begins processing a FlowFile | Start of task execution |
| **SEND** | FlowFile forwarded without modification | Task output (content unchanged) |
| **MODIFY** | Content or attributes modified | Task output (content changed) |
| **CLONE** | FlowFile duplicated for branching | DAG with multiple successors |
| **DROP** | FlowFile discarded after error | Failure after max_retries |
| **ROUTE** | FlowFile routed to a specific output | RouteOnAttribute |

---

## ProvenanceEvent: Structure

```python
@dataclass
class ProvenanceEvent:
    event_id: str          # Unique event UUID
    event_type: ProvenanceEventType
    timestamp: datetime

    # Identifiers
    flowfile_id: str                    # ID of the concerned FlowFile
    parent_flowfile_ids: List[str]      # Parents (for CLONE, MODIFY)
    child_flowfile_ids: List[str]       # Children
    task_id: str                        # Task that generated the event
    task_type: str                      # Task type
    flow_id: str                        # Concerned flow

    # Data
    content_size: int                   # Content size in bytes
    attributes: Dict[str, str]          # Copy of attributes at the time of the event
    details: str                        # Text description
    duration_ms: float                  # Processing duration (ms)

    def to_dict(self) -> Dict[str, Any]:
        """Serialization to dictionary."""
```

---

## ProvenanceRepository: Storage and Queries

### Initialization

```python
from engine.provenance import ProvenanceRepository

# Create a repository (max 100,000 events by default)
repo = ProvenanceRepository(max_events=100000)
```

### Recording (thread-safe, FIFO)

```python
repo.record(ProvenanceEvent(
    event_type=ProvenanceEventType.CREATE,
    flowfile_id="ff-123",
    flow_id="my-flow",
    details="Input FlowFile"
))
```

When `max_events` is exceeded, the oldest events are automatically removed (FIFO eviction).

### Filtering

```python
# By FlowFile
events = repo.get_events(flowfile_id="ff-123")

# By task
events = repo.get_events(task_id="log-task")

# By event type
events = repo.get_events(event_type=ProvenanceEventType.MODIFY)

# By flow
events = repo.get_events(flow_id="my-flow")

# Combined with limit
events = repo.get_events(flowfile_id="ff-123", event_type=ProvenanceEventType.MODIFY, limit=10)
```

### Lineage reconstruction

```python
# Complete lineage of a FlowFile (recursive parents + children)
lineage = repo.get_lineage("ff-123")

for event in lineage:
    print(f"{event.event_type.value}: {event.flowfile_id[:8]}... → {event.details}")
```

The lineage follows `parent_flowfile_ids` and `child_flowfile_ids` relationships recursively to reconstruct the complete history.

### Events by flow

```python
events = repo.get_flow_events("my-flow")
# Returns all events sorted by timestamp
```

### Statistics

```python
stats = repo.to_dict()
# {
#   "total_events": 1500,
#   "max_events": 100000,
#   "events_by_type": {"CREATE": 100, "RECEIVE": 500, "MODIFY": 400, ...},
#   "events_by_task": {"log": 300, "transformJSON": 200, ...}
# }
```

### Cleanup

```python
repo.clear()           # Empty the repository
repo.size()            # Number of events
```

---

## Integration with FlowExecutor

### Activation

```python
from engine.provenance import ProvenanceRepository
from engine.executor import FlowExecutor

repo = ProvenanceRepository()
executor = FlowExecutor(provenance=repo)

# Provenance is optional: passing None (default) disables it
executor_no_prov = FlowExecutor(provenance=None)
```

### When each event is emitted

#### CREATE -- Flow entry

```python
# In execute_flow(), after creating input FlowFiles
for ff in flowfiles:
    self._record_event(ProvenanceEventType.CREATE, ff, flow.id,
                       details="Input FlowFile")
```

#### RECEIVE -- Start of processing by a task

```python
# In _execute_task_with_retry(), before task.execute()
self._record_event(ProvenanceEventType.RECEIVE, flowfile, flow_id,
                   task_id=task_id, task_type=task_type)
```

#### MODIFY / SEND -- Task output

```python
# In _execute_task_with_retry(), after task.execute()
# MODIFY if content or attributes changed, otherwise SEND
for out_ff in result:
    modified = (out_ff.content != original_content or
                dict(out_ff.attributes) != original_attrs)
    evt_type = ProvenanceEventType.MODIFY if modified else ProvenanceEventType.SEND
    self._record_event(evt_type, out_ff, flow_id, ...)
```

#### CLONE -- DAG branching

```python
# In _execute_dag(), when a result must go to multiple successors
# The first successor receives the original, the rest receive clones
for i, successor in enumerate(successors):
    if i == 0:
        task_queue[successor].extend(result)
    else:
        for r_ff in result:
            cloned = r_ff.clone()
            self._record_event(ProvenanceEventType.CLONE, cloned, flow.id,
                               parent_ids=[r_ff.process_id],
                               details=f"Clone for branch {successor}")
```

#### DROP -- Failure after retries

```python
# In _execute_task_with_retry(), after exhausting retries
self._record_event(ProvenanceEventType.DROP, flowfile, flow_id,
                   task_id=task_id, task_type=task_type,
                   details=f"Error after {self.max_retries} retries: {last_error}")
```

### Provenance in results

When provenance is enabled, statistics are included in `ExecutionResult`:

```python
result = executor.execute_flow(flow, input_flowfiles=[ff])

if result.success and 'provenance' in result.statistics:
    prov_stats = result.statistics['provenance']
    print(f"Total events: {prov_stats['total_events']}")
    print(f"By type: {prov_stats['events_by_type']}")
```

---

## Complete example

```python
from core import Flow, FlowFile, Task
from engine.executor import FlowExecutor
from engine.provenance import ProvenanceRepository, ProvenanceEventType
from tasks.system.log_task import LogTask
from tasks.system.update_attribute import UpdateAttributeTask

# 1. Create the repository
repo = ProvenanceRepository()

# 2. Create a flow with branching
flow = Flow({'name': 'Provenance Demo'})
flow.tasks = {
    'update': UpdateAttributeTask({'attributes': {'processed': 'true'}}),
    'log_a': LogTask({'message': 'Branch A'}),
    'log_b': LogTask({'message': 'Branch B'}),
}
flow.relations = [
    {'from': 'update', 'to': 'log_a'},
    {'from': 'update', 'to': 'log_b'},
]

# 3. Execute with provenance
executor = FlowExecutor(max_retries=1, provenance=repo)
ff = FlowFile(content=b'hello world', attributes={'source': 'test'})
result = executor.execute_flow(flow, input_flowfiles=[ff])

# 4. Analyze
print(f"Success: {result.success}")
print(f"Events: {repo.size()}")

for event in repo.get_flow_events(flow.id):
    print(f"  {event.event_type.value:8s} | ff={event.flowfile_id[:8]}... "
          f"| task={event.task_id or '-':10s} | {event.details}")
```

---

## Thread-Safety

The `ProvenanceRepository` uses a `threading.Lock` to guarantee safety under concurrent access. All public methods (`record`, `get_events`, `get_lineage`, `get_flow_events`, `clear`, `size`, `to_dict`) are thread-safe.

This allows safe usage with the `FlowExecutor` which executes tasks in parallel via `ThreadPoolExecutor`.

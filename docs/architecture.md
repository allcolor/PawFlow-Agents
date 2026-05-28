# Detailed Architecture - PawFlow

This document describes PawFlow's internal architecture, its core components, and their interactions.

---

## Overview

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                              PawFlow Architecture                              │
├───────���──────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────────┐  ┌───────────────────┐  ┌──────────────┐  ┌────────────┐ │
│  │    Core      │  │      Engine       │  │ Listener/UI  │  │  Clients   │ │
│  │ FlowFile     │  │ FlowExecutor      │  │ /chat /admin │  │ Web / CLI  │ │
│  │ Task/Service │  │ ContinuousExec.   │  │ WS relay     │  │ VS Code    │ │
│  │ Flow         │  │ Scheduler (CRON)  │  │ Tool relay   │  │ Relays     │ │
│  │ Connection   │  │ CheckpointMgr     │  │ Capabilities │  │ Providers  │ │
│  │ Security     │  │ Provenance        │  │ Proxies      │  │            │ │
│  │ Plugin       │  │ VersionManager    │  │              │  │            │ │
│  │ SpillTracker │  │ WorkerCoordinator │  │              │  │            │ │
│  └──────────────┘  └───────────────────┘  └──────────────┘  └────────────┘ │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                     Tasks (68) + Services (5)                        │   │
│  │  System(10) │ IO(20) │ Data(27) │ Control(11) │ AI(1) │ 5 Services │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## FlowFile: Structure and Lifecycle

### Definition

The **FlowFile** is the fundamental data unit in PawFlow. It holds binary content and metadata attributes, with transparent streaming and disk-spill support.

### Structure

```python
class FlowFile:
    _content_ref: ContentReference  # In-memory or disk-backed (transparent)
    attributes: Dict[str, str]      # Key-value metadata
    process_id: str                 # Unique UUID
    created_at: datetime            # Creation timestamp
```

### API

```python
# Attributes
flowfile.get_attribute('key', 'default')
flowfile.set_attribute('key', 'value')
flowfile.delete_attribute('key')
flowfile.get_attributes()          # copy of the dict

# Content (backward-compatible)
content = flowfile.get_content()   # bytes (loads into memory if spilled)
flowfile.set_content(b'data')      # auto-spill if > SPILL_THRESHOLD

# Streaming (new — for large files)
stream = flowfile.get_content_stream()   # BinaryIO (BytesIO or file handle)
flowfile.set_content_from_stream(stream, size_hint=10_000_000)

# Size and state
flowfile.size()                    # int (without loading content)
flowfile.is_empty()                # bool
flowfile.is_content_on_disk        # bool

# Cloning
clone = flowfile.clone(deep=True)  # deep=True: independent copy
clone = flowfile.clone(deep=False) # deep=False: shared via ref-counting
```

### Streaming and Disk-Spill (ContentReference + SpillTracker)

FlowFiles support transparent streaming:
- **Content < SPILL_THRESHOLD** (10 MB): stored in memory
- **Content ≥ SPILL_THRESHOLD**: automatically spilled to disk

The `SpillTracker` handles ref-counting and temporary file cleanup:
```python
from core.stream import get_spill_tracker
stats = get_spill_tracker().get_stats()
# {active_spill_files, total_bytes_on_disk, total_spill_count, total_cleaned, ...}
```

---

## Task: Interface and Injected Services

```python
class Task:
    TYPE: str           # Unique identifier
    VERSION: str        # Semantic version
    NAME: str           # Display name
    DESCRIPTION: str    # Description
    ICON: str           # GUI icon

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        """Execute the task. Returns 0, 1, or N FlowFiles."""

    def get_parameter_schema(self) -> Dict[str, Any]:
        """Parameter schema for validation and UI."""

    # Services injected by the executor
    def get_service(self, service_id: str) -> Any:
        """Access a shared service."""

    def set_services(self, services: Dict[str, Any]):
        """Called by the executor to inject services."""
```

### Configuration

Tasks receive a flat dict:
```python
task = LogTask({"message": "hello", "level": "INFO"})
# Access: self.config.get("message")
```

---

## Connection: Queues with Backpressure

`Connection` objects link tasks in continuous mode. Each connection is a FIFO queue.

```python
class Connection:
    source_id: str
    target_id: str
    relationship: str            # "success", "failure", "matched", etc.
    max_queue_size: int = 10000  # Backpressure by count
    max_queue_bytes: int = ...   # Backpressure by size
    flowfile_ttl_seconds: float  # FlowFile TTL (0 = no TTL)

    def enqueue(ff) -> bool      # False if backpressure
    def dequeue() -> FlowFile
    def peek() -> FlowFile       # Without removing
    def is_empty() -> bool
    def queue_size() -> int
    def drain_expired() -> list  # Expired FlowFiles (TTL)
```

---

## Two Execution Modes

### 1. FlowExecutor (Batch)

Executes the DAG level by level with parallelism:

```python
executor = FlowExecutor(
    max_workers=10,        # Parallel threads
    max_retries=3,         # Retries per task
    flow_timeout=300,      # Global timeout (s)
    provenance=repo,       # ProvenanceRepository (optional)
)
result = executor.execute_flow(flow, input_flowfiles=[ff])
```

Sequence: topological sort → levels → parallel execution → clone if branching → result.

### 2. ContinuousFlowExecutor (NiFi-style)

Continuous execution with queues and transactions:

```python
executor = ContinuousFlowExecutor(
    flow,
    max_workers=8,
    max_retries=3,
    enable_checkpoints=True,
    checkpoint_interval=30.0,
)
executor.start()
executor.inject(FlowFile(content=b"data"))
executor.get_status()
executor.stop()
```

**Transaction model:**
1. **Peek**: FlowFile read from the input queue (without removing)
2. **Execute**: task executed
3. **Commit**: FlowFile removed from input, results sent to output
4. **Rollback**: FlowFile stays in the queue, task transitions to ERROR

**Relationship routing:**
- FlowFiles with `route.relationship` attribute → matching connection
- Fallback → all output connections

**Failure routing (penalty box):**
- If a "failure" connection exists → FlowFile dequeued and routed there
- Otherwise → FlowFile stays in the queue, task in ERROR, backpressure cascades

**Hot-swap:**
```python
executor.update_task("task_id", new_config)    # Change config without loss
executor.update_flow(new_flow)                  # Structural update
```

---

## Checkpointing and Crash Recovery

The `CheckpointManager` periodically saves queue state:

```python
mgr = CheckpointManager(flow_id="my_flow", max_checkpoints=5)
mgr.save_checkpoint(connections, task_states, flow_version)
data = mgr.load_latest_checkpoint()
flowfiles = mgr.restore_flowfiles(data)
```

Format: JSON with FlowFile content as base64 (small) or files (> 256 KB).

---

## Remote Workers

### WorkerCoordinator

Distributes tasks across local or remote workers:

```python
coord = WorkerCoordinator(
    heartbeat_timeout_seconds=60,
    max_consecutive_failures=5,
)
coord.register_worker("remote-1", "192.168.1.10", 9000)
coord.get_health_summary()
```

**Circuit breaker**: after N consecutive failures, worker → OFFLINE.

### WorkerServer / WorkerClient

HTTP communication with binary streaming protocol and API key auth:

```python
server = WorkerServer(port=9000, api_key="secret")
server.start()

client = WorkerClient("192.168.1.10", 9000, api_key="secret")
result = client.execute_task("log", config, content, attributes)
```

---

## Security (RBAC)

### Roles and Permissions

| Role | Permissions |
|------|-------------|
| **admin** | All: users, plugins, settings, flows, execute, monitor |
| **editor** | flows CRUD, execute, monitor, services |
| **operator** | execute, monitor |
| **viewer** | monitor (read-only) |

### SecurityManager

```python
security = SecurityManager.get_instance()
security.enable_auth(True)
session = security.authenticate("admin", "password")
security.check_permission(session, "flow.edit")
security.generate_api_key("Description")
security.set_oauth_config("google", {...})
```

---

## Plugin System (.pfp)

Plugins are ZIP archives containing tasks, services, and flows:

```
plugin.json, tasks/, services/, flows/, requirements.txt
```

```python
pm = PluginManager()
pm.install("plugin.pfp")
pm.load_all()
pm.list_plugins()
pm.uninstall("plugin-id")
```

---

## Runtime HTTP Listener

The runtime exposes a listener/UI server:

```bash
python cli.py start --host 0.0.0.0 --port 19990
```

Important routes:

| Route | Description |
|---------|-------------|
| `/chat` | Web chat UI |
| `/admin` | Admin UI |
| `/ws/relay` | PawFlow relay WebSocket |
| `/ws/tools/_tool_relay` | Internal tool relay WebSocket |
| `/vnc/<session>/<token>/...` | Capability-protected VNC/noVNC proxy |
| `/terminal/<session>/<token>/...` | Capability-protected terminal proxy |
| `/code/<session>/<token>/...` | Capability-protected code-server proxy |
| `/fwd/<forward>/<token>/...` | Capability-protected port-forward proxy |

---

## Scheduler (CRON)

```python
scheduler = FlowScheduler()
scheduler.add_job("daily", "flows/pipeline.json", "0 6 * * *")
scheduler.start()
scheduler.save_jobs()
```

Standard CRON format: `minute hour day month weekday`

---

## Provenance

The `ProvenanceRepository` tracks the lifecycle of each FlowFile:

```python
repo = get_provenance_repository()
events = repo.get_events(flowfile_id="abc", limit=100)
lineage = repo.get_lineage("abc")  # Full lineage
stats = repo.to_dict()
```

Event types: CREATE, RECEIVE, SEND, MODIFY, CLONE, DROP, ROUTE.

---

## Cluster Mode

The `engine/cluster.py` module provides cluster mode for multi-node coordination:

- Leader election to avoid execution conflicts
- State synchronization between nodes
- Automatic flow distribution across available workers
- Inter-node health monitoring

---

## API Client

UI/runtime integrations use the listener routes above and the PawFlow relay/client APIs shipped in this repository.

---

## Docker Deployment

PawFlow provides a `Dockerfile` and a `docker-compose.yml` to run the listener/UI on port `19990`, with persistence under `data/` and support for provider/relay containers when Docker is available.

See **[deployment.md](deployment.md)** for the full guide.

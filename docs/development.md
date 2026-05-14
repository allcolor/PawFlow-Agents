# Development Guide - PawFlow

This guide is intended for developers who want to extend PawFlow by creating new tasks, services, or contributing to the source code.

---

## How to Create a New Task

### Step 1: File structure

Create a new file in the `tasks/` directory corresponding to the category:

```
tasks/
├── system/       # System tasks (log, wait, fail, etc.)
├── io/           # I/O tasks (files, HTTP)
├── data/         # Data transformation tasks
└── control/      # Flow control tasks
```

### Step 2: Implement the class

```python
# tasks/data/my_transform.py
from typing import Dict, Any, List
from core import FlowFile, Task


class MyTransformTask(Task):
    """Transforms the content to uppercase."""

    TYPE = "myTransform"       # Unique type (identifier)
    VERSION = "1.0.0"
    NAME = "My Transform"      # Name displayed in the UI
    DESCRIPTION = "Converts content to uppercase"
    ICON = "🔠"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.encoding = self.config.get('encoding', 'utf-8')

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        content = flowfile.get_content().decode(self.encoding)
        flowfile.set_content(content.upper().encode(self.encoding))
        flowfile.set_attribute('transformed', 'true')
        return [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'encoding': {
                'type': 'string',
                'required': False,
                'description': 'Content encoding',
                'default': 'utf-8'
            }
        }
```

### Step 3: Register the task

Add the import in `tasks/__init__.py` inside the `register_all_tasks()` function:

```python
from tasks.data.my_transform import MyTransformTask
TaskFactory.register(MyTransformTask)
```

### Step 4: Use in a flow

```python
from core import Flow, FlowFile
from engine.executor import FlowExecutor
from tasks.data.my_transform import MyTransformTask

flow = Flow({'name': 'test'})
flow.tasks = {'transform': MyTransformTask({'encoding': 'utf-8'})}
flow.relations = []

executor = FlowExecutor()
result = executor.execute_flow(flow, input_flowfiles=[FlowFile(content=b'hello')])
# result.output_flowfiles[0].get_content() == b'HELLO'
```

### Step 5: Write the tests

```python
import pytest
from core import FlowFile
from tasks.data.my_transform import MyTransformTask


def test_uppercase():
    task = MyTransformTask({'encoding': 'utf-8'})
    ff = FlowFile(content=b'hello world')
    results = task.execute(ff)
    assert results[0].get_content() == b'HELLO WORLD'
    assert results[0].get_attribute('transformed') == 'true'

def test_empty_content():
    task = MyTransformTask({})
    ff = FlowFile(content=b'')
    results = task.execute(ff)
    assert results[0].get_content() == b''
```

### Important points

- **TYPE must be unique**: it is the identifier for the TaskFactory
- **execute() always returns a List[FlowFile]**: even if empty or with a single element
- **get_parameter_schema()** is used by the UI and API for configuration forms
- **Flat config**: tasks receive a flat dict `{"key": "val"}`, NOT `{"parameters": {"key": "val"}}`
- **Use `get_content()`/`set_content()`** instead of `.content` for streaming support
- **Injected services**: access shared services via `self.get_service("service_id")`

---

## How to Create a New Service

```python
# services/my_database.py
from typing import Dict, Any
from core import Service


class MyDatabaseService(Service):
    """PostgreSQL connection service."""

    TYPE = "myDatabase"
    NAME = "My Database"
    DESCRIPTION = "PostgreSQL connection"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.host = self.config.get('host', 'localhost')
        self.port = self.config.get('port', 5432)

    def connect(self):
        import psycopg2
        self._connection = psycopg2.connect(
            host=self.host, port=self.port,
            database=self.config.get('database'),
            user=self.config.get('user'),
            password=self.config.get('password'),
        )

    def disconnect(self):
        if self._connection:
            self._connection.close()

    def execute_query(self, query: str, params=()):
        cursor = self._connection.cursor()
        cursor.execute(query, params)
        return cursor.fetchall()

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'host': {'type': 'string', 'required': True},
            'port': {'type': 'integer', 'required': False, 'default': 5432},
            'database': {'type': 'string', 'required': True},
            'user': {'type': 'string', 'required': True},
            'password': {'type': 'secret', 'required': True},
        }
```

Services are automatically connected at startup by the FlowExecutor and ContinuousFlowExecutor. Tasks can access them via `self.get_service("service_id")`.

---

## How to Create a Plugin (.pfp)

### Plugin structure

```
my-plugin/
├── plugin.json          # Descriptor (required)
├── requirements.txt     # pip dependencies (optional)
├── tasks/
│   └── my_task.py       # Custom tasks
├── services/
│   └── my_service.py    # Custom services
└── flows/
    └── my_flow.json     # Pre-configured flows
```

### plugin.json

```json
{
    "id": "com.example.my-plugin",
    "name": "My Plugin",
    "version": "1.0.0",
    "author": "Author",
    "description": "Plugin description",
    "min_pawflow_version": "1.0.0",
    "tasks": ["tasks/my_task.py:MyTaskClass"],
    "services": ["services/my_service.py:MyServiceClass"],
    "flows": ["flows/my_flow.json"]
}
```

### Package as .pfp

```python
from core.plugin import create_plugin_archive
create_plugin_archive("my-plugin/", "my-plugin-1.0.0.pfp")
```

### Install

```python
from core.plugin import PluginManager
pm = PluginManager()
pm.install("my-plugin-1.0.0.pfp")
pm.load_all()
```

Plugin upload is currently managed through the Python plugin APIs above. Add a documented HTTP endpoint only when it is implemented in the listener runtime.

---

## Runtime Server

The public server entrypoint is the PawFlow listener/UI process:

```bash
python cli.py start --host 0.0.0.0 --port 9090
```

Useful local URLs:

| URL | Description |
|---|---|
| `http://localhost:9090/chat` | Web chat UI |
| `http://localhost:9090/admin` | Admin UI |
| `ws://localhost:9090/ws/relay` | PawFlow relay WebSocket |
| `ws://localhost:9090/ws/tools/_tool_relay` | Internal tool relay WebSocket |

Conversation persistence uses `ConversationWriter` as an asynchronous FIFO per
conversation. Provider callbacks must only enqueue work and return; writer lag
must never throttle tool calls, streaming callbacks, or message production. The
writer drains ready queue items in batches, publishes SSE only after successful
disk writes, and `ConversationStore.append_message()` updates hot metadata in
memory instead of rescanning `transcript.jsonl` after each append.

---

## Running the Tests

Tests must never write to the repository's real `data/` tree. The global
pytest fixture redirects PawFlow storage paths to a temporary data directory,
and relay-executed Python snippets receive an isolated `PAWFLOW_DATA_DIR` by
default so reproduction scripts cannot pollute `data/runtime`.

```bash
# All tests (758)
pytest tests/ -v

# REST API
pytest tests/test_api.py -v                  # 39 tests

# Continuous execution
pytest tests/test_continuous_executor.py -v  # 22 tests

# Security + checkpoint
pytest tests/test_security_checkpoint.py -v  # 29 tests

# With coverage
pytest tests/ --cov=core --cov=engine --cov=tasks --cov=api --cov-report=term-missing
```

---

## Code Conventions

### Naming

- **Classes**: PascalCase (`LogTask`, `FlowExecutor`)
- **Functions/methods**: snake_case (`execute_flow`, `get_attribute`)
- **Variables**: snake_case (`input_directory`, `max_retries`)
- **Class constants**: UPPER_CASE (`TYPE`, `VERSION`, `NAME`)
- **Files**: snake_case (`log_task.py`, `flow_executor.py`)

### Style

- PEP 8
- Type hints on public signatures
- Docstrings for public classes and methods
- Grouped imports: standard, third-party, local

### Task config

```python
# CORRECT: flat dict
task = MyTask({"key": "value", "other": "val"})

# INCORRECT: do not wrap in "parameters"
task = MyTask({"parameters": {"key": "value"}})  # NO
```

The FlowParser handles the `parameters` wrapping for JSON files, but tasks always read `self.config.get("key")` directly.

---

## Operator Repair Scripts

`scripts/repair_contexts.py` rebuilds derived conversation context files from
`transcript.jsonl`, which is the canonical record. Use it when shared or
per-agent context files have been corrupted by routing or projection bugs.

Examples:

```bash
# Inspect what would change.
python scripts/repair_contexts.py <conversation_id> --shared --agent assistant

# Apply with automatic .bak-<timestamp> backups beside each repaired file.
python scripts/repair_contexts.py <conversation_id> --shared --agent assistant --apply
```

The script rebuilds `shared.jsonl` from the transcript shared projection and
agent contexts from `ConversationStore.load_transcript_for_agent()`, preserving
provider-independent PawFlow context semantics.

---

## Implementation Areas

| Area | Description |
|-------|-------------|
| Core | FlowFile, Task, Service, Flow, Executor |
| Tasks | Built-in data, filesystem, media, browser, and AI tasks |
| Expression Language | `${...}` syntax, scope selection, operators, defaults |
| Services | DB, cache, HTTP listener, LLM providers, relays |
| Runtime | Continuous execution, scheduler, connections, backpressure |
| UI and clients | Web chat, admin UI, PawCode CLI, VS Code extension |
| Security | Auth, sessions, API keys, approvals, capabilities, encrypted secrets |
| Deployment | Dockerfile, docker-compose, sidecar/local modes |
| Observability | Logs, security report, runtime status, targeted tests |

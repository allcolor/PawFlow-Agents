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

Or via the REST API:
```bash
curl -X POST http://localhost:8000/api/v1/plugins/upload \
  -F "file=@my-plugin-1.0.0.pfp" \
  -H "Authorization: Bearer <token>"
```

---

## REST API

The API is accessible at `http://localhost:8000` with Swagger documentation at `/docs`.

### Start the API

```bash
python -m api.app                    # port 8000
python -m api.app --port 9000        # custom port
python -m api.app --reload           # dev mode
```

### Authentication

```bash
# Login (if auth is enabled)
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin"}' | jq -r .session_id)

# Use the token
curl http://localhost:8000/api/v1/flows/ -H "Authorization: Bearer $TOKEN"

# Or use an API key
curl http://localhost:8000/api/v1/flows/ -H "Authorization: Bearer <api_key>"
```

### Main endpoints

| Prefix | Description |
|--------|-------------|
| `/api/v1/auth` | Login, logout, users, API keys, OAuth2, roles |
| `/api/v1/flows` | CRUD flows, validate, import/export |
| `/api/v1/execution` | Batch, continuous (start/stop/inject), task actions |
| `/api/v1/monitoring` | Bulletins, provenance, streaming stats |
| `/api/v1/scheduler` | CRUD CRON jobs, start/stop scheduler |
| `/api/v1/tasks` | Task/service types and parameter schemas |
| `/api/v1/workers` | Remote workers, health, register/unregister |
| `/api/v1/plugins` | Install/uninstall/upload plugins |
| `/api/v1/system` | Health, info, security status |

---

## Running the Tests

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

## Completed phases

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Core (FlowFile, Task, Service, Flow, Executor) | ✅ Done |
| 2 | Base tasks (log, replaceText, getFile, putFile, etc.) | ✅ Done |
| 3 | Expression Language (`${...}`, Jinja2) | ✅ Done |
| 4 | +30 tasks (SQL, JSON, CSV, cache, compress, etc.) | ✅ Done |
| 5 | Services (DB, Cache, HTTP, LLM) | ✅ Done |
| 6 | Runtime (continuous, scheduler, connections, backpressure) | ✅ Done |
| 7 | Streamlit GUI (5 pages), CLI | ✅ Done |
| 8 | Remote workers, streaming, plugins | ✅ Done |
| 9 | Security (RBAC, OAuth2, sessions, API keys) | ✅ Done |
| 10 | REST API (FastAPI, 85+ endpoints, auth middleware) | ✅ Done |
| 10b | API Client, cluster mode, storage backends, NiFi converter | ✅ Done |
| 11 | Docker deployment (Dockerfile, docker-compose, documentation) | ✅ Done |
| 12 | Production hardening, observability, scaling | Planned |

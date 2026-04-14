# Technical Documentation - Continued (Sections 11-17)

## 11. Complete Task Reference

### 11.1. Base Tasks (System)

#### 11.1.1. Log Task (`log`)
**Description**: Log a message with formatting

**Parameters**:
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `message` | string | Yes | - | Message to log (supports variables) |
| `level` | select | No | INFO | Log level (DEBUG, INFO, WARNING, ERROR) |
| `logger_name` | string | No | - | Logger name (default: task name) |
| `include_attributes` | boolean | No | false | Include FlowFile attributes in the log |

**Example**:
```json
{
  "type": "log",
  "parameters": {
    "message": "Processing ${filename}, size: ${fileSize}",
    "level": "INFO",
    "include_attributes": true
  }
}
```

#### 11.1.2. Replace Text Task (`replace_text`)
**Description**: Replace text in FlowFile content

**Parameters**:
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `search_pattern` | string | Yes | - | Search pattern (regex or text) |
| `replacement` | string | Yes | - | Replacement text |
| `regex` | boolean | No | false | Use regex (true) or plain text (false) |
| `case_sensitive` | boolean | No | true | Case sensitive |
| `multiline` | boolean | No | false | Multiline |

**Example**:
```json
{
  "type": "replace_text",
  "parameters": {
    "search_pattern": "\\bold\\b",
    "replacement": "new",
    "regex": true,
    "case_sensitive": false
  }
}
```

#### 11.1.3. Wait Task (`wait`)
**Description**: Wait for a duration before continuing

**Parameters**:
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `duration` | integer | Yes | - | Duration in milliseconds |
| `duration_unit` | select | No | MS | Unit (MS, SEC, MIN, HOUR) |

**Example**:
```json
{
  "type": "wait",
  "parameters": {
    "duration": 1000,
    "duration_unit": "MS"
  }
}
```

#### 11.1.4. Notify Task (`notify`)
**Description**: Send a notification (email, webhook, etc.)

**Parameters**:
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `notification_type` | select | Yes | - | Type (email, webhook, slack) |
| `service_ref` | reference | Yes | - | Reference to the notification service |
| `subject` | string | No | - | Subject (for email) |
| `body` | string | No | - | Message body |
| `recipients` | array | No | [] | List of recipients |
| `on_success` | boolean | No | true | Send only on success |
| `on_failure` | boolean | No | true | Send only on failure |

**Example**:
```json
{
  "type": "notify",
  "parameters": {
    "notification_type": "email",
    "service_ref": "${email_service}",
    "subject": "Pipeline completed",
    "body": "Flow ${flow_name} completed successfully.",
    "recipients": ["admin@example.com"]
  }
}
```

#### 11.1.5. Route Task (`route`)
**Description**: Route FlowFile to different outputs based on criteria

**Parameters**:
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `route_definitions` | json | Yes | - | Route definitions |
| `default_route` | string | No | "unmatched" | Default route |

**route_definitions Schema**:
```json
{
  "route_1": "${attribute} == 'value1'",
  "route_2": "${attribute} == 'value2'",
  "default": "unmatched"
}
```

#### 11.1.6. Split Task (`split`)
**Description**: Split a FlowFile into multiple FlowFiles

**Parameters**:
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `split_strategy` | select | Yes | - | Strategy (line, record, size) |
| `split_count` | integer | No | - | Number of splits (for size) |

**Line example**:
```json
{
  "type": "split",
  "parameters": {
    "split_strategy": "line"
  }
}
```

#### 11.1.7. Merge Task (`merge`)
**Description**: Merge multiple FlowFiles into one

**Parameters**:
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `merge_strategy` | select | Yes | - | Strategy (time, count, batch) |
| `merge_timeout` | integer | No | 30 | Timeout in seconds |
| `merge_count` | integer | No | 10 | Number of FlowFiles to merge |

### 11.2. Data Processing Tasks

#### 11.2.1. Script Task (`script`)
**Description**: Execute a custom Python script

**Parameters**:
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `script` | textarea | Yes | - | Python script code |
| `script_type` | select | No | inline | Type (inline, file) |
| `input_var_name` | string | No | flowfile | Input variable name |
| `output_var_name` | string | No | result | Output variable name |
| `variables` | json | No | {} | Additional variables |

**Script Template**:
```python
def process(input_var_name):
    # input_var_name is a FlowFile
    # return a FlowFile or a list of FlowFiles
    return input_var_name
```

#### 11.2.2. Shell Task (`shell`)
**Description**: Execute a shell command

**Parameters**:
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `command` | string | Yes | - | Command to execute |
| `working_directory` | string | No | - | Working directory |
| `environment` | json | No | {} | Environment variables |
| `timeout` | integer | No | 300 | Timeout in seconds |
| `capture_output` | boolean | No | true | Capture stdout/stderr |

#### 11.2.3. Convert Task (`convert`)
**Description**: Convert data format

**Parameters**:
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `input_format` | select | Yes | - | Input format (json, csv, xml, avro, parquet) |
| `output_format` | select | Yes | - | Output format |
| `schema` | json | No | - | Schema (for structured formats) |
| `options` | json | No | {} | Format-specific options |

#### 11.2.4. Filter Task (`filter`)
**Description**: Filter FlowFiles based on a criterion

**Parameters**:
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `condition` | string | Yes | - | Condition (Python or JEXL expression) |
| `match` | select | No | true | true = keep match, false = exclude match |

**Example**:
```json
{
  "type": "filter",
  "parameters": {
    "condition": "${fileSize} > 1000",
    "match": true
  }
}
```

#### 11.2.5. Validate Task (`validate`)
**Description**: Validate a FlowFile against a schema

**Parameters**:
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `schema` | json | Yes | - | Validation schema (JSON Schema, Avro, etc.) |
| `schema_format` | select | No | json | Schema format |
| `on_invalid` | select | No | fail | Action (fail, route, skip) |
| `route_invalid_to` | string | No | - | Route for invalid items |

### 11.3. Input/Output Tasks

#### 11.3.1. HTTP Task (`http`)
**Description**: Call an HTTP API

**Parameters**:
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `url` | string | Yes | - | Endpoint URL |
| `method` | select | No | GET | Method (GET, POST, PUT, DELETE, PATCH) |
| `headers` | json | No | {} | HTTP headers |
| `body` | string | No | - | Request body |
| `auth_service` | reference | No | - | Authentication service |
| `timeout` | integer | No | 30 | Timeout in seconds |
| `follow_redirects` | boolean | No | true | Follow redirects |
| `response_handling` | select | No | content | Action (content, status, both) |

**Example**:
```json
{
  "type": "http",
  "parameters": {
    "url": "https://api.example.com/data",
    "method": "POST",
    "headers": {
      "Content-Type": "application/json"
    },
    "body": "${content}",
    "auth_service": "${oauth_service}"
  }
}
```

#### 11.3.2. HTTP Source Task (`http_source`)
**Description**: HTTP source (polling or webhook)

**Parameters**:
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `url` | string | Yes | - | URL to poll |
| `method` | select | No | GET | Method |
| `polling_interval` | integer | No | 60 | Interval in seconds |
| `headers` | json | No | {} | Headers |

#### 11.3.3. SFTP Task (`sftp`)
**Description**: SFTP operations

**Parameters**:
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `operation` | select | Yes | - | Operation (get, put, list, delete, rename) |
| `service_ref` | reference | Yes | - | SFTP service |
| `remote_path` | string | Yes | - | Remote path |
| `local_path` | string | No | - | Local path (for put/get) |
| `filename_pattern` | string | No | * | File pattern |
| `overwrite` | boolean | No | false | Overwrite existing |

#### 11.3.4. S3 Task (`s3`)
**Description**: AWS S3 operations

**Parameters**:
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `operation` | select | Yes | - | Operation (get, put, delete, list) |
| `service_ref` | reference | Yes | - | S3 service |
| `bucket` | string | Yes | - | Bucket name |
| `key` | string | No | - | S3 key |
| `prefix` | string | No | - | Prefix (for list) |
| `max_keys` | integer | No | 1000 | Max keys (for list) |
| `version_id` | string | No | - | Version (for get) |

#### 11.3.5. Database Task (`db`)
**Description**: Database operations

**Parameters**:
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `operation` | select | Yes | - | Operation (query, update, insert, delete, bulk) |
| `service_ref` | reference | Yes | - | DB service |
| `query` | textarea | Yes | - | SQL query |
| `parameters` | json | No | {} | Query parameters |
| `batch_size` | integer | No | 1000 | Batch size |

#### 11.3.6. File Task (`file`)
**Description**: Local file operations

**Parameters**:
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `operation` | select | Yes | - | Operation (read, write, delete, rename) |
| `path` | string | Yes | - | File path |
| `path_type` | select | No | absolute | Type (absolute, relative, home) |
| `encoding` | select | No | utf-8 | Encoding |
| `create_dirs` | boolean | No | true | Create directories |

#### 11.3.7. Kafka Task (`kafka`)
**Description**: Publish/consume Kafka

**Parameters**:
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `operation` | select | Yes | - | Operation (publish, consume) |
| `service_ref` | reference | Yes | - | Kafka service |
| `topic` | string | Yes | - | Topic |
| `key` | string | No | - | Message key |
| `partition` | integer | No | - | Partition |
| `headers` | json | No | {} | Kafka headers |

#### 11.3.8. Serve Relay File Task (`serveRelayFile`)
**Description**: Stream a file from a relay/filesystem service over HTTP, with the matching `Content-Type` set from the file extension. Used by the chat UI to inline-render media (images, audio, video) stored on the user's relay — `<img src="/fs/<service>/<path>">` works the same as `<img src="/files/<id>">` for FileStore. Auth: the user must be the HTTP session principal AND have access to the named service (resolution: conv > user > global scope).

**Parameters**:
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `service_attribute` | string | No | `http.path.service_name` | FlowFile attribute that holds the service name extracted from the URL pattern. |
| `path_attribute` | string | No | `http.path.rest` | FlowFile attribute that holds the file path relative to the service root. |

**Wiring**: in `pawflow_agent` the route is `GET /fs/{service_name}/{rest+}` → `validate_auth` → `route_after_auth` (relationship `fs`) → `serveRelayFile` → `handleHTTPResponse`.

**Status codes**: `400` missing service/path, `401` no auth principal, `403` permission denied on the service, `404` service or file not found, `502` relay read error, `200` on success.

### 11.4. Control Tasks

#### 11.4.1. Execute Flow Task (`executeFlow`)
**Description**: Run an external flow as a sub-flow and pass the FlowFile through it.

**Parameters**:
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `flow_path` | string | Yes | - | Path to the sub-flow JSON file |
| `parameter_mapping` | object | No | {} | `{<child_param>: "${<parent_expr>}"}` — resolves expressions in the parent's ParameterContext, then injects the result as the child's parameters. |
| `port_mapping` | object | No | {} | `{input: {port_task_id: <id>}, output: {<output_port_id>: <relationship>}}` — routes the input FlowFile to a specific `inputPort` task and tags outputs with relationships from the matching `outputPort`. |
| `pass_attributes` | boolean | No | true | Copy parent FlowFile attributes onto the sub-flow's outputs. |

**Recursion guard**: each invocation pushes its `flow_path` onto a `_subflow_stack` attribute on the FlowFile. If the same path appears twice, or the stack exceeds `MAX_SUBFLOW_DEPTH` (10), execution aborts with a `TaskError` — cycles and unbounded recursion fail fast.

**Synthesis from ProcessGroups**: a `ProcessGroup` with `flow_ref: {path, version}` is automatically synthesized into an `executeFlow` task by the parser (`engine/parser.py`). The parser also validates `flow_ref.version` against the loaded child's `version` field and checks that every `port_mapping.input.port_task_id` / `port_mapping.output` key exists in the child as the right port type — typos fail at parse, not at runtime.

**Agent shortcut**: agents can invoke any deployed flow once and get the result inline via `manage_flow(action="run", template_id="<package>.<flow>:<version>", parameters={...}, input="...")` — no deployment, no background instance.

#### 11.4.2. Sleep Task (`sleep`)
**Description**: Pause execution

**Parameters**:
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `duration` | integer | Yes | - | Duration in milliseconds |

#### 11.4.3. Fail Task (`fail`)
**Description**: Explicitly fail the FlowFile

**Parameters**:
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `message` | string | No | - | Error message |
| `terminate` | boolean | No | true | Terminate the entire flow |

#### 11.4.4. Choose Task (`choose`)
**Description**: Choose between multiple branches (switch)

**Parameters**:
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `expression` | string | Yes | - | Expression to evaluate |
| `branches` | json | Yes | - | Conditional branches |

**branches Schema**:
```json
{
  "branch_1": "${expression} == 'value1'",
  "branch_2": "${expression} == 'value2'",
  "default": "branch_default"
}
```

#### 11.4.5. Join Task (`join`)
**Description**: Join multiple FlowFiles

**Parameters**:
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `join_strategy` | select | Yes | - | Strategy (time, count, batch) |
| `join_timeout` | integer | No | 60 | Timeout in seconds |
| `join_count` | integer | No | 10 | Number of FlowFiles |

### 11.5. Analysis Tasks

#### 11.5.1. Aggregate Task (`aggregate`)
**Description**: Aggregate multiple FlowFiles

**Parameters**:
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `aggregation_type` | select | Yes | - | Type (sum, count, avg, min, max, collect) |
| `field` | string | No | - | Field to aggregate |
| `group_by` | array | No | [] | Grouping fields |

#### 11.5.2. Sort Task (`sort`)
**Description**: Sort FlowFiles

**Parameters**:
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `sort_criteria` | json | Yes | - | Sort criteria |
| `order` | select | No | ASC | Order (ASC, DESC) |

**sort_criteria Schema**:
```json
{
  "attribute1": "ASC",
  "attribute2": "DESC"
}
```

#### 11.5.3. Distinct Task (`distinct`)
**Description**: Remove duplicates

**Parameters**:
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `distinct_by` | array | Yes | [] | Attributes for distinction |
| `keep_first` | boolean | No | true | Keep first or last |

### 11.6. Transformation Tasks

#### 11.6.1. JSON Task (`json`)
**Description**: Transform/validate JSON

**Parameters**:
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `operation` | select | Yes | - | Operation (parse, validate, transform) |
| `transform_script` | textarea | No | - | Transformation script |
| `schema` | json | No | - | JSON Schema |

#### 11.6.2. XML Task (`xml`)
**Description**: Transform/validate XML

**Parameters**:
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `operation` | select | Yes | - | Operation (parse, validate, transform, xpath) |
| `xpath` | string | No | - | XPath expression |
| `schema` | xml | No | - | XSD schema |

#### 11.6.3. CSV Task (`csv`)
**Description**: Transform CSV

**Parameters**:
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `operation` | select | Yes | - | Operation (parse, format, convert) |
| `delimiter` | string | No | , | Delimiter |
| `has_header` | boolean | No | true | First row is header |
| `quote_char` | string | No | " | Quote character |

#### 11.6.4. Base64 Task (`base64`)
**Description**: Encode/Decode Base64

**Parameters**:
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `operation` | select | Yes | - | Operation (encode, decode) |

---

## 12. Complete Service Reference

PawFlow provides 5 shared services, accessible in tasks via `self.get_service("service_id")`.

### 12.1. Database Connection Pool (`dbConnectionPool`)

**File**: `services/db_connection_pool.py`
**Description**: Database connection pool (SQLite, PostgreSQL, MySQL via DB-API 2.0)

**Parameters**:
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `db_type` | string | Yes | sqlite | Type (sqlite, postgresql, mysql) |
| `database` | string | Yes | - | DB path (SQLite) or database name |
| `host` | string | No | localhost | Host (PostgreSQL/MySQL) |
| `port` | integer | No | - | Port |
| `user` | string | No | - | User |
| `password` | secret | No | - | Password |
| `pool_size` | integer | No | 5 | Pool size |

**Usage in a task**:
```python
db = self.get_service("my_db")
conn = db.get_connection()
cursor = conn.cursor()
cursor.execute("SELECT * FROM users")
```

### 12.2. Cache Service (`cacheService`)

**File**: `services/cache_service.py`
**Description**: In-memory cache with TTL and max size

**Parameters**:
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `max_size` | integer | No | 10000 | Max number of entries |
| `ttl` | integer | No | 3600 | TTL in seconds |

**Usage**:
```python
cache = self.get_service("my_cache")
cache.put("key", "value")
val = cache.get("key")
```

### 12.3. HTTP Client Service (`httpClientService`)

**File**: `services/http_client_service.py`
**Description**: Shared HTTP client with base configuration

**Parameters**:
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `base_url` | string | No | - | Base URL for requests |
| `timeout` | integer | No | 30 | Timeout in seconds |
| `headers` | object | No | {} | Default headers |

### 12.4. LLM Connection (`llmConnection`)

**File**: `services/llm_connection.py`
**Description**: Connection to LLMs (OpenAI, Anthropic) via native HTTP (zero-dependency)

**Parameters**:
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `provider` | string | Yes | openai | Provider (openai, anthropic) |
| `api_key` | secret | Yes | - | API key |
| `model` | string | No | gpt-4 | Model to use |
| `base_url` | string | No | - | Custom base URL |
| `max_tokens` | integer | No | 1024 | Max tokens per response |
| `temperature` | float | No | 0.7 | Temperature |

**Usage with InferLLM**:
```python
# In a JSON flow
"services": {
    "llm": {
        "type": "llmConnection",
        "provider": "openai",
        "api_key": "${LLM_API_KEY}",
        "model": "gpt-4"
    }
}
```

### 12.5. Distributed Map Cache Client (`distributedMapCache`)

**File**: `services/distributed_cache.py`
**Description**: Distributed cache compatible with NiFi DistributedMapCacheClient

**Parameters**:
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `max_size` | integer | No | 100000 | Max size |
| `ttl` | integer | No | 0 | TTL in seconds (0 = no TTL) |

**Usage**: Used by the `fetchDistributedMapCache` and `putDistributedMapCache` tasks.

---

## 13. Execution Engine API

### 13.1. Flow Executor (Batch)

```python
from engine import FlowExecutor

executor = FlowExecutor(
    max_workers=10,        # Parallel threads
    max_retries=3,         # Retries per task
    flow_timeout=300,      # Global timeout (s)
    provenance=repo,       # ProvenanceRepository (optional)
)
result = executor.execute_flow(flow, input_flowfiles=[ff], variables={"key": "val"})
# result.success, result.duration_ms, result.statistics, result.errors
```

Sequence: topological sort -> levels -> parallel execution -> clone if branching -> result.

### 13.2. ContinuousFlowExecutor (NiFi-style)

Continuous execution with queues, backpressure, and transactions:

```python
from engine.continuous_executor import ContinuousFlowExecutor

executor = ContinuousFlowExecutor(
    flow,
    max_workers=8,
    max_retries=3,
    enable_checkpoints=True,
    checkpoint_interval=30.0,
)
executor.start()
executor.inject(FlowFile(content=b"data"))
status = executor.get_status()   # task states, queue sizes
executor.stop()
```

**Transactional model**:
1. **Peek**: FlowFile read from the queue (without removing)
2. **Execute**: task executed
3. **Commit**: FF removed from input, results sent to output
4. **Rollback**: FF stays in the queue, task -> ERROR

**Routing**: FlowFiles with `route.relationship` attribute -> corresponding connection.
**Failure routing**: if "failure" connection exists -> FF dequeued and routed there.

**Hot-swap**:
```python
executor.update_task("task_id", new_config)    # Change config without loss
executor.update_flow(new_flow)                  # Structural update
```

### 13.3. Scheduler (CRON)

```python
from engine.scheduler import FlowScheduler

scheduler = FlowScheduler()
scheduler.add_job("daily-etl", "flows/pipeline.json", "0 6 * * *")
scheduler.start()
scheduler.save_jobs()  # Persist jobs
scheduler.load_jobs()  # Restore jobs
```

### 13.4. REST API (FastAPI)

10 routers, 85+ endpoints. OpenAPI documentation at `/docs`.

```bash
python -m api.app --port 8000
```

| Router | Prefix | Description |
|--------|--------|-------------|
| auth | `/api/v1/auth` | Login, users, API keys, OAuth2, roles |
| flows | `/api/v1/flows` | CRUD flows, validate, import/export |
| execution | `/api/v1/execution` | Batch, continuous, inject, task actions |
| monitoring | `/api/v1/monitoring` | Bulletins, provenance, streaming |
| scheduler | `/api/v1/scheduler` | CRUD CRON jobs |
| tasks | `/api/v1/tasks` | Types, parameter schemas |
| workers | `/api/v1/workers` | Remote workers, health |
| plugins | `/api/v1/plugins` | Install/uninstall/upload/export |
| system | `/api/v1/system` | Health, info, security status |

**Auth**: Bearer token (session or API key). If auth is disabled, open access.

```bash
# Login
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin"}' | jq -r .session_id)

# Use
curl http://localhost:8000/api/v1/flows/ -H "Authorization: Bearer $TOKEN"
```

---

## 14. GUI - Technical Specifications

### 14.1. GUI Architecture (Streamlit)

```
gui/
├── __init__.py
├── app.py                 # Main entry point
├── config.py             # Streamlit configuration
├── editor/               # Creation GUI
│   ├── __init__.py
│   ├── app.py           # Editor application
│   ├── canvas.py        # Flow canvas
│   ├── properties.py    # Properties panel
│   └── components/
│       ├── task_palette.py
│       ├── flow_editor.py
│       └── relation_editor.py
└── runtime/              # Runtime GUI
    ├── app.py           # Runtime application
    ├── dashboard.py     # Main dashboard
    ├── flow_viewer.py   # Flow visualization
    ├── logs.py          # Log visualization
    └── metrics.py       # Real-time metrics
```

### 14.2. Editor Screens

#### 14.2.1. Main Page
- List of existing flows
- Create/Import/Export buttons
- Search and filters

#### 14.2.2. Flow Canvas
- Graphical visualization of tasks
- Drag & drop tasks from the palette
- Connect tasks via relations
- Zoom and navigation

#### 14.2.3. Properties Panel
- Edit parameters of the selected task
- Real-time validation
- Data preview

#### 14.2.4. Service Manager
- List of available services
- Create/Edit services
- Connection testing

### 14.3. Runtime Screens

#### 14.3.1. Main Dashboard
- Overview of executions
- Global statistics
- Alerts and errors

#### 14.3.2. Flow Visualization
- Real-time task state
- Data flow
- Per-task metrics

#### 14.3.3. Logs Viewer
- Real-time logs
- Filters and search
- Log export

#### 14.3.4. Runtime Configuration
- Variable overrides
- Parameter configuration
- Flow deployment

---

## 15. Security and Authentication (RBAC)

### 15.1. SecurityManager

```python
from core.security import SecurityManager

security = SecurityManager.get_instance()
security.enable_auth(True)

# Authentication
session = security.authenticate("admin", "password")
security.check_permission(session, "flow.edit")  # raises if denied

# API Keys
key = security.generate_api_key("My integration")

# OAuth2
security.set_oauth_config("google", {
    "client_id": "...", "client_secret": "...",
    "authorization_url": "...", "token_url": "..."
})
```

### 15.2. Roles and Permissions

| Role | Permissions |
|------|-------------|
| **admin** | Everything: users, plugins, settings, flows, execute, monitor |
| **editor** | Flows CRUD, execute, monitor, services |
| **operator** | Execute, monitor |
| **viewer** | Monitor (read-only) |

### 15.3. REST API Auth

The REST API uses middleware that supports:
- **Bearer session token**: obtained via POST /api/v1/auth/login
- **API key**: generated via the GUI or the API, grants admin access
- **Disabled mode**: if auth is disabled, all endpoints are accessible

---

## 16. Tests and Quality

**758 tests**, all green.

```bash
pytest tests/ -v                    # All tests
pytest tests/ --cov=core --cov=engine --cov=tasks --cov=api --cov-report=term-missing
```

### 16.1. Test Files

| File | Tests | Domain |
|------|-------|--------|
| test_executor.py | 23 | FlowExecutor batch |
| test_continuous_executor.py | 22 | ContinuousFlowExecutor |
| test_api.py | 39 | REST API |
| test_security_checkpoint.py | 29 | RBAC + Checkpoint |
| test_storage_backends.py | 30 | Git, SQLite, Filesystem, StorageManager |
| test_plugin_system.py | 21 | Plugins + .pfp export |
| test_streaming.py | 27 | FlowFile streaming + spill |
| test_new_io_tasks.py | 21 | XML, Email, Slack, SFTP |
| test_tasks.py | 15 | Base tasks |
| ... | ... | ... |

### 16.2. Tools

- **pytest** for tests
- **pytest-cov** for coverage
- **FastAPI TestClient** for API tests

---

## 17. Deployment and Production

### 17.1. Production Configuration

```yaml
# config/production.yaml
storage:
  type: postgres
  host: db.example.com
  port: 5432
  database: pawflow
  
execution:
  max_workers: 50
  max_retries: 5
  timeout: 600
  
monitoring:
  enable_metrics: true
  enable_tracing: true
  log_level: INFO
```

### 17.2. Docker Deployment

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY . .

RUN pip install -r requirements.txt

CMD ["streamlit", "run", "gui/runtime/app.py"]
```

---

## 18. Filesystem Services

PawFlow provides a unified filesystem abstraction layer. See `docs/filesystem.md` for the full guide.

### 18.1. Service Types

| Type | Description | Git | Required |
|------|-------------|-----|----------|
| `relay` | WebSocket relay to user machine (exec, git, shell) | Yes | pawcode, vscode plugin, `pawflow_relay.py` |
| `filesystem` | Server disk (admin only) | Yes | Admin role |
| `googleDrive` | Google Drive REST API v3 | No | OAuth2 |
| `oneDrive` | OneDrive Graph API | No | OAuth2 |

### 18.2. `filesystemOps` Task

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `service_id` | string | Yes | Filesystem service ID |
| `action` | string | Yes | list_dir, read_file, write_file, delete_file, mkdir, stat, exists, search, grep, find_replace, git_* |
| `path` | string | No | Relative path (default: ".") |
| `pattern` | string | No | Glob pattern (search) or regex (find_replace) |
| `regex` | string | No | Regex pattern (grep) |
| `replacement` | string | No | Replacement text (find_replace) |
| `recursive` | boolean | No | Recursive (search/grep, default: true) |

### 18.3. Permissions

- **Modes**: `read` (read-only), `readwrite` (read + write), `full` (+ deletion)
- **allowed_paths**: Allowed prefixes (empty = all)
- **denied_paths**: Denied prefixes (takes priority over allowed)

### 18.4. OAuth Token Storage

`core/oauth_token_store.py` -- Encrypted storage of OAuth tokens per user/provider. Auto-refresh of expired access tokens. Persistence in `config/users/{user_id}/oauth_tokens.json`.

---

**End of Technical Documentation**

*Version: 2.1.0*
*Date: 2026-03-14*
*70+ tasks, 11 services, 76+ filesystem tests, REST API, RBAC, Plugins, Docker*

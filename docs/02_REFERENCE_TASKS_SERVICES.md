# Technical Documentation - Continued (Sections 11-17)

## 11. Complete Task Reference

PawFlow Package (`.pfp`) files can add flow processor types through `flow_task`
or `task_provider` objects. Installed package tasks are registered in
`TaskFactory` as runtime proxies: flows can parse and validate the new task
type immediately, and execution runs the package entrypoint through the relay
named by the task's required `relay` parameter. `relay` is per task and may be
an expression backed by flow parameters, so one flow can run three imported PFP
tasks on three different relays with `relay: "${relay_a}"`,
`relay: "${relay_b}"`, and `relay: "${relay_c}"`. PFP flow imports from a
conversation prefill that parameter from the conversation default relay when
one is available. Use `task_def` only for agent/task-definition resources; use
`flow_task`/`task_provider` for flow processors.

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

#### 11.1.8. Install Bootstrap Task (`installBootstrap`)
**Description**: Serve the first-run installer status and finalization API. It is intended for the bundled `PawFlow Installer` flow, not for user flows.

**Behavior**:
- `GET /install/api` returns the persisted install state without exposing secret values.
- `POST /install/api/finalize` requires the current bootstrap gateway key and a replacement gateway key.
- The replacement gateway key is stored only as a SHA-256 digest in `install_state.json`.
- Finalization installs the final runtime listener TLS config from either generated self-signed certificates or mounted cert/key files.
- Finalization installs builtin auth plus any configured external AuthGateway providers, and can pre-bind the admin account to matching OAuth identities.
- Successful finalization writes `install_complete=true` and marks the installer deployment stopped for the next restart.

**Parameters**: none.

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

**Deployment configuration**: chat UI flow deployment and edit dialogs use the flow template schema instead of a free-form JSON box. `get_flow_deploy_schema` exposes typed flow parameters plus each declared controller service. The Flow Repository sidebar groups templates by package and sorts packages and flows alphabetically. Deployments persist flow parameter values, local service configs, and service bindings (`global:<service_id>` / `user:<user_id>:<service_id>`). Starting or restoring a deployed flow applies those bindings before services connect, so a flow-local service can be replaced by an existing user/global service at runtime.

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

#### 11.5.4. Skill Curator Task (`skillCurator`)
**Description**: Flag stale/unused agent skills and propose curation actions (report only)

Crosses the skill repository with `load_skill` usage statistics (`data/runtime/skill_stats.json`), classifies each skill as active, stale, or never-loaded, optionally runs an LLM review (keep/archive/merge), and writes a JSON report to the FlowFile content. The task never applies an action — changes go through the resource UI or `manage_resource` after review. Schedule it with a cron trigger for a recurring curation loop.

**Parameters**:
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `user_id` | string | Yes | - | User whose skill library is curated |
| `stale_days` | integer | No | 90 | Days without a load before a skill is flagged stale |
| `include_global` | boolean | No | false | Also flag global-scope skills |
| `provider` | string | No | - | Optional LLM provider for the review pass (empty = heuristic report only) |
| `api_key` | string | No | - | API key for the review LLM |
| `base_url` | string | No | - | API base URL |
| `model` | string | No | - | Model name |

**Output attributes**: `skill.curator.total`, `skill.curator.flagged`

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

Service schemas may expose parameter fill helpers through `fill_helper`
metadata. The chat resource editor renders those helpers beside eligible
fields and calls `get_service_parameter_helper` to fetch suggestions. Helpers
cover LLM providers, OpenAI-compatible media services, voice/audio services,
Pixazo and WaveSpeed catalogs, OAuth/Auth Gateway templates, rclone backends,
HTTP callback URLs, and certificate/path fields. Live provider model lookup is
attempted only when required context such as `api_key` is already filled;
otherwise the UI shows bundled fallback values and an explicit warning. Secret
helpers list secret names only and fill `${secret_name}` references, never raw
secret values.

### 12.0.1. Pocket TTS Local (`pocketTTS`)

**File**: `services/pocket_tts_service.py`
**Description**: Managed Kyutai Pocket TTS daemon for CPU-friendly local TTS.
PawFlow starts `pocket-tts serve` lazily, calls `POST /tts`, and returns WAV
audio bytes for `speak` and `generate_audio`.

**Parameters**:
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `base_url` | string | No | `http://127.0.0.1:8000` | Pocket TTS server URL; relay URLs are supported for user-local endpoints. |
| `allow_remote_voice_urls` | boolean | No | false | Allow Pocket TTS to fetch HTTP(S) voice URLs; disabled by default to avoid local-daemon SSRF. |
| `auto_start` | boolean | No | true | Start the local daemon when first used. |
| `auto_install` | boolean | No | true | Prepare a managed Python runtime during service installation. |
| `install_dir` | string | No | `data/runtime/pocket-tts` | Managed runtime directory. |
| `package_spec` | string | No | `pocket-tts[audio]>=2.1.0` | pip package spec installed into the runtime. |
| `language` | select | No | `english` | Model language loaded by the daemon. |
| `voice` | string | No | `alba` | Built-in voice, `hf://` voice URL, HTTP(S) voice URL, or local voice file. |
| `quantize` | boolean | No | false | Enable Pocket TTS int8 quantization. |
| `timeout` | integer | No | 180 | HTTP timeout in seconds. |

`speak(text, voice=...)` sends `voice` as Pocket TTS `voice_url`. Pass
`reference_audio_bytes` or a local `reference_audio_url` to upload a one-shot
`voice_wav` prompt for voice cloning.

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

### 12.5. LLM Aggregator (`llmAggregator`)

**File**: `services/llm_aggregator.py`
**Description**: Parallel advisor fan-out followed by synthesis or execution through a final LLM connection.

**Parameters**:
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `aggregator_llm_service` | service reference | Yes | - | Final `llmConnection` used for the visible answer and tool-loop |
| `advisor_llm_services` | JSON array | Yes | `[]` | `llmConnection` IDs consulted concurrently |
| `max_parallel_advisors` | integer | No | 4 | Maximum concurrent advisor calls |
| `advisor_max_iterations` | integer | No | 20 | Maximum tool-loop iterations per advisor |
| `failure_policy` | select | No | `best_effort` | Continue with partial reports or fail on any advisor error |
| `enforce_read_only` | boolean | No | true | Enforce PawFlow's fail-closed read-only tool allowlist for every advisor |

Advisor traces and sub-conversations are silent and ephemeral. Their reports
are generated once on the first LLM call for a user turn, then cached while the
final LLM consumes tool results. Only final-LLM tokens populate the main
`LLMResponse` counters; advisor usage is attached separately to internal raw
response metadata and remains tracked by each underlying service.

### 12.6. Distributed Map Cache Client (`distributedMapCache`)

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

### 13.4. Runtime HTTP Listener

The current supported server entrypoint is the PawFlow listener/UI process:

```bash
python cli.py start --host 0.0.0.0 --port PORT
```

| Route | Description |
|--------|-------------|
| `/chat` | Web chat UI |
| `/admin` | Admin UI |
| `/ws/relay` | PawFlow relay WebSocket |
| `/ws/tools/_tool_relay` | Internal tool relay WebSocket |
| `/vnc/<session>/<token>/...` | Capability-protected VNC/noVNC proxy |
| `/terminal/<session>/<token>/...` | Capability-protected terminal proxy |
| `/code/<session>/<token>/...` | Capability-protected code-server proxy |
| `/fwd/<forward>/<token>/...` | Capability-protected port-forward proxy |

---

## 14. GUI - Technical Specifications

### 14.1. Runtime UI Architecture

PawFlow exposes the runtime through the listener/UI server and client integrations:

| Surface | Description |
|---|---|
| Web chat | Main conversation UI at `/chat` |
| Admin UI | Service, runtime, and configuration UI at `/admin` |
| PawCode CLI | Terminal client using the same conversation runtime |
| VS Code extension | Editor client with resources and approvals |
| Relay WebSocket | `/ws/relay` for filesystem/exec relay connections |
| Tool relay WebSocket | `/ws/tools/_tool_relay` for internal tool execution plumbing |

### 14.2. Main Screens

- Conversation view with streaming assistant output, tool calls, tool results, approvals, background tools, and active-agent controls. The web chat can collapse consecutive technical rows between visible messages when the expression variable `chat.group_technical_messages` resolves to a truthy value (`true`, `1`, `yes`, `on`); the default is `true`. Tool-call groups keep a stable `tc_id` boundary so a reload does not merge unrelated tools into one technical details block. The header `Group tech` toggle writes this parameter at conversation scope and reloads the conversation so rendering follows the server-resolved value. The floating scroll controls use explicit top/bottom navigation; the top button does not trigger history lazy-loading by itself.
- Admin/resource views for LLM services, relays, provider login, runtime status, and user-scoped resources.
- Desktop/VNC, terminal, code-server, and port-forward views exposed through capability-protected routes.

### 14.3. Runtime Configuration

- LLM provider services and credentials
- Relay configuration and exposed workspace directories
- Approval mode and per-tool permissions
- Capability-protected browser routes
- Flow deployment and conversation-scoped parameters

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
| **admin** | User management, settings, and create/update/delete access for global resources |
| **user** | Own conversations and create/update/delete access for user- and conversation-scoped resources |

### 15.3. Listener Auth

The listener authenticates users through PawFlow session cookies/API keys and applies route-level capability tokens for browser-accessible runtime resources such as VNC, terminal, code-server, and port-forward routes.

---

## 16. Tests and Quality

```bash
pytest tests/ -v
pytest tests/ --cov=core --cov=engine --cov=tasks --cov=services --cov-report=term-missing
```

### 16.1. Test Areas

| Area | Domain |
|------|--------|
| Engine | FlowExecutor, continuous execution, checkpoints |
| Services | User services, listener, relay, provider connections |
| Security | Auth, capabilities, approvals, encrypted secrets |
| Agents | Compaction, provider dispatch, streaming, tools |
| Storage | Filesystem, SQLite, Git-backed stores |
| Media/tools | Image, video, audio, browser, filesystem tools |

### 16.2. Tools

- **pytest** for tests
- **pytest-cov** for coverage
- **ruff** for fatal syntax/import checks

---

## 17. Deployment and Production

### 17.1. Production Configuration

Set production-critical configuration through environment variables and service definitions:

```bash
PAWFLOW_ENV=production
PAWFLOW_PUBLIC_MODE=true
PAWFLOW_SECRET_KEY_B64=<base64-32-byte-key>
PAWFLOW_AUTH_ENABLED=true
```

### 17.2. Docker Deployment

```dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY . .
RUN pip install -r requirements.txt
CMD ["python", "cli.py", "start", "--host", "0.0.0.0", "--port", "PORT"]
```
```

---

## 18. Filesystem Services

PawFlow provides a unified filesystem abstraction layer. See `docs/filesystem.md` for the full guide.

### 18.1. Service Types

| Type | Description | Git | Required |
|------|-------------|-----|----------|
| `relay` | WebSocket relay to server-managed storage or a standalone client (exec, git, shell) | Yes | Empty token for server relay; token for standalone `pawflow-relay` client |
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
| `multiline` | boolean | No | Enable regex line-boundary mode for find_replace |
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

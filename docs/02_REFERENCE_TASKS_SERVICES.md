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

**Agent shortcuts**: agents can execute a template once and get the result inline via `manage_flow(action="run", template_id="<package>.<flow>:<version>", parameters={...}, input="...")` — no deployment, no background instance. For a durable continuous flow, deploy and start it first, then inject work with `manage_flow(action="invoke", flow_id="<instance>", input="...", attributes={...}, entry_task_id="<optional-entry>")`; this preserves the running instance required by `durableWait`.

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

### 11.7. Workflow Agent Tasks

Workflow-agent flows use `kind: "agent_workflow"` and a closed first-party
processor catalog. They are validated by the same strict validator in the Flow
Editor and at publish/bind time; arbitrary source, script, nested-agent, or
open-world package tasks are rejected.

| Type | Purpose |
|---|---|
| `agentWorkflowInput` | Validate the immutable server-owned request and expose its bounded fields. |
| `emitAgentProgress` | Publish a bounded stage label without adding transcript content. |
| `agentLLMCall` | Run one recoverable LLM step against a snapshotted service, with strict output validation and idempotent usage accounting. |
| `receiveAgentMessages` | Lease ordered messages from the durable agent inbox at a checkpoint. |
| `groupDeliberationInput` | Validate the server-owned request for the exact first-party group flow. |
| `resolveGroupSnapshot` | Require the group feature flag and attach the immutable run-start group/member snapshot. |
| `selectGroupResponders` | Select only members from the immutable roster by all, mention, or bounded classifier policy. |
| `initializeSharedRoom` | Create the bounded room from the explicit request and attachments, without private context. |
| `agentParticipantCall` | Run one structured, idempotent, tool-free member call against its pinned API LLM service. |
| `synthesizeGroupResult` | Produce one bounded terminal candidate by deterministic concatenation or a pinned synthesis LLM. |
| `completeAgentTurn` | Stage the sole validated terminal result for the exactly-once commit saga. |
| `inputPort` / `outputPort` | Declare the contract entry and terminal ports. |

`agentLLMCall` requires a service from the run's accepted service snapshot.
Its main parameters are `service`, `messages` or an input source,
`response_format`/`json_schema`, model controls, `timeout`,
`cache_policy`, `retry_attempts`, output target, progress label, and
visibility. Retries greater than one require `run_idempotent` caching. Each
`json_schema` call exposes one internal structured-output tool and forces that
tool on Anthropic- and OpenAI-compatible transports; its arguments become the
validated task result. Other transports retain the same schema prompt plus
post-response validation and fail closed on mismatches. Each
committed step is charged once to the usage ledger under `channel=workflow`
with durable run/task dimensions.

The six group processors are reserved for
`pawflow.agents.group-deliberation:1.0.0`. They accept no task parameters. Their authority comes from the injected
run context and exact group snapshot. WP6 participant calls have no tools or
private context; token and cost allocations are enforced before durable step
commit, cancellation aborts active provider clients, and only
`completeAgentTurn` stages the single assistant result.

The Wiki Agent adds deterministic workflow-only processors for bounded source
scan/fetch/normalization, extraction merge, patch/review validation, source-byte
CAS apply or shadow preview, wiki lint, and receipt-backed reporting.
`prepareWikiIntent` builds a bounded classifier prompt before project access;
`routeWikiIntent` validates `wiki_maintenance` versus `unsupported`, permits only
a reduction of the configured batch limit, and terminates non-Wiki requests.
The original accepted request focuses extractor/writer prompts but cannot expand
the snapshot or change `write_mode`. `validateWikiPatch` derives
`processed_sources` from the selected snapshot rather than accepting model paths.
Their
contract and production flow are documented in
[Agent System](AGENT_SYSTEM.md) and
[Workflow Agent Operations](WORKFLOW_AGENT_OPERATIONS.md).

The Media Studio Agent adds the following workflow-safe processors:

| Type | Purpose |
|---|---|
| `prepareMediaIntent` / `routeMediaIntent` | Classify and reject unrelated requests before file or service access. |
| `prepareMediaRelay` / `applyMediaRelay` | Freeze an explicit/default/unique linked relay or durably choose one frozen authorized candidate before media access. |
| `loadMediaProject` / `resolveMediaReferences` | Load scoped append-only project lineage and validate explicit FileStore reference roles. |
| `snapshotMediaCapabilities` / `selectMediaCapability` | Freeze visible media services and choose deterministically with stable rejection reasons. |
| `prepareMediaBrief` / `validateMediaBrief` | Build and validate the immutable creative brief. |
| `prepareMediaQuestions` / `applyMediaQuestionAnswers` | Create one bounded dynamic form and merge only validated durable answers. |
| `prepareMediaScenario` / `validateMediaScenario` / `applyMediaScenarioDecision` | Digest the exact production proposal and enforce Produce, Revise, or Cancel. |
| `prepareMediaProvisioning` | Stop for review when no installed capability is usable; it never mutates the host. |
| `prepareMediaVoiceConsent` / `applyMediaVoiceConsent` | Require explicit durable authorization before voice cloning. |
| `splitMediaGeneration` / `submitMediaGeneration` / `joinMediaGeneration` | Bound and correlate independent scenario jobs, execute at most four exact-service submissions concurrently, and checkpointably join their artifacts before QA. |
| `validateMediaCompositionRecipe` / `composeMedia` | Validate a closed FFmpeg recipe and execute it through the exact FFmpeg service. |
| `validateMediaArtifact` / `appendMediaRevision` / `formatMediaStudioResult` | Validate FileStore outputs, append immutable lineage, and emit the typed terminal. |

The exact production resource is `pawflow.agents.media-studio:1.0.0`, shipped by
`pawflow.media-studio:1.0.0`. ComfyUI audio uses
`comfyUIAudioGeneration`; immutable preset metadata and approved provisioning are
documented in [ComfyUI](comfyui.md).

---

## 12. Complete Service Reference

### Policy Gating Service (`gating`)

Decides `allow` / `deny` / `ask` for agent tool calls against the
authenticated user's mandate (`docs/POLICY_GATING.md`). Parameters:
`llm_service` (API-backed `llmConnection`, required with a prompt), `prompt`
(policy text), `scripts` (ordered `gating_script` resource names, run in the
relay sandbox), `llm_scope` (`mutating` | `all` | `none`), `failure_decision`
(`ask` | `deny`), `max_tokens`, `timeout_seconds`, `script_timeout_seconds`.
Active only through a conversation binding (`gating_link` /
`gating_unlink` / `gating_list_available`) or an agent `gating_service`
reference. Related resource type: `gating_script` (`source` with
`evaluate(event)`, optional `tools` filter, `fail_decision`).

PawFlow provides 5 shared services, accessible in tasks via `self.get_service("service_id")`.

Service schemas may expose parameter fill helpers through `fill_helper`
metadata. The chat resource editor renders those helpers beside eligible
fields and calls `get_service_parameter_helper` to fetch suggestions. Helpers
cover LLM providers, OpenAI-compatible media services, voice/audio services,
OAuth/Auth Gateway templates, rclone backends,
HTTP callback URLs, and certificate/path fields. Live provider model lookup is
attempted only when required context such as `api_key` is already filled;
otherwise the UI shows bundled fallback values and an explicit warning. Secret
helpers list secret names only and fill `${secret_name}` references, never raw
secret values.

Provider-specific model choices shipped by a PFP, including the bundled Pixazo
and WaveSpeed providers, live in that package's service schema. PawFlow renders
those options without retaining a second provider catalog in core.

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
| `provider` | string | Yes | openai | Provider (openai, openai-responses, azure-openai, copilot, anthropic, plus the CLI providers) |
| `api_key` | secret | Yes | - | API key |
| `model` | string | No | gpt-4 | Model to use |
| `base_url` | string | No | - | Custom base URL |
| `azure_deployment` | string | No | - | Azure only: deployment name (empty = use the model name) |
| `azure_api_version` | string | No | - | Azure only: `api-version` query parameter |
| `max_tokens` | integer | No | 1024 | Max tokens per response |
| `temperature` | float | No | 0.7 | Temperature |
| `cli_environment` | multiline string | No | - | CLI providers only: one `NAME=value` assignment per line; PawFlow expressions are resolved when the process starts. |
| `codex_config_toml` | multiline string | No | - | Codex providers only: additional `config.toml` merged structurally into PawFlow's generated configuration. |
| `codex_models_json` | multiline JSON | No | - | Codex providers only: a model catalog object containing a `models` array, written as `.codex/models.json`. |

`cli_environment` is available for `claude-code`,
`claude-code-interactive`, `antigravity-interactive`,
`codex-app-server`, `codex-interactive`, and `gemini`. Empty values are
preserved. Process-isolation variables such as `HOME`, provider home
directories, `PAWFLOW_*`, and managed endpoint or credential values remain
authoritative and cannot be replaced by this block.

For Codex custom providers such as DeepSeek, put the provider/model selection in
`codex_config_toml` and the corresponding model descriptors in
`codex_models_json`. PawFlow writes the catalog beside the generated
`config.toml` and supplies the container-visible `model_catalog_json` path.
The custom TOML can add providers and models, while PawFlow's MCP bridge,
internal authentication, trust policy, and context-management settings win on
conflicting keys.

**OpenAI-dialect providers** (`core/llm_providers/openai_dialects.py`): Azure
OpenAI and GitHub Copilot send OpenAI chat-completions bodies, so they reuse
the whole OpenAI path. Only the envelope differs.

OpenAI-compatible SSE responses must end with a specified `finish_reason` or
the `data: [DONE]` sentinel. If a gateway closes a successful HTTP response
before either signal, or reports a non-standard error finish reason, PawFlow
does not commit the partial answer. It immediately retries the same completion
without streaming; if that request also fails, the normal bounded retry and
configured fallback-model policy still applies. PawFlow stops reading as soon
as `[DONE]` arrives, recognizes gateway safety aliases such as `sensitive` as
`content_filter`, and rejects known in-band transport failures from the
non-streaming recovery response as retryable errors. Successful streams and
unknown provider-specific non-streaming finish reasons retain their existing
behavior.

*OpenAI Responses* (`openai-responses`, `core/llm_providers/openai_responses.py`)
is NOT a dialect: it is a different wire format on a different endpoint
(`/responses`), with typed `input` items instead of `messages`, `instructions`
instead of a system message, flat tool declarations, a semantic SSE stream with
no `data: [DONE]` sentinel, and `input_tokens`/`output_tokens` usage. It has
its own mixin and its own dispatch branch (`RESPONSES_WIRE_PROVIDERS`) rather
than joining `OPENAI_WIRE_PROVIDERS`. Configured like `openai`: `api_key`,
optional `base_url`, `default_model`. See `docs/llm_providers.md`.

*Azure OpenAI* needs three things a plain OpenAI-compatible `base_url` cannot
express: the key travels in an `api-key` header rather than `Authorization`,
the request addresses a **deployment** (`/openai/deployments/<name>/chat/completions`),
and an `api-version` query parameter is mandatory. `base_url` is required —
every Azure resource has its own host, so there is no default to fall back on.
Embeddings still go through the `openai` provider only.

*GitHub Copilot* is a two-token provider. **Sign in with GitHub** on the service
form runs a device flow: GitHub shows a field, PawFlow shows the code to type
into it — no callback URL and no browser needed on the server. The resulting
GitHub token lands in `api_key` and is saved like any other key. It is not what
the chat endpoint accepts: each session exchanges it for a short-lived Copilot
token, cached in memory and renewed before expiry. `PAWFLOW_COPILOT_CLIENT_ID`
overrides the editor client id if you register your own GitHub OAuth app.
Using a Copilot subscription outside GitHub's own editors is a grey area in
their terms — the account and the risk are the operator's.

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

### 12.6. Adaptive LLM Router (`llmRouter`)

**File**: `services/llm_router.py`
**Description**: Deterministic, turn-affine routing across direct LLM connections.
Every turn receives an immutable plan of exact `(scope, scope_id, service_id,
definition_revision)` references. Selection can be ordered, round-robin,
sticky round-robin, or least recently used. Provider failure preserves the
existing cold-start handoff and never replays completed work.

**Parameters**:
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `candidates` | ordered service-reference list | Yes | `[]` | Unique direct `llmConnection` candidates; at least two must be enabled when saved |
| `strategy` | select | Yes | `ordered` | `ordered`, `round_robin`, `sticky_round_robin`, or `least_recently_used` |
| `sticky_successful_turns` | integer | No | `1` | Successful terminal turns retained by sticky round robin |
| `affinity_ttl_seconds` | integer | No | `86400` | Expiry for per-user/conversation/agent affinity |

Route handoff is control flow, not a user-visible LLM error. Persisted text,
tool calls, and tool results remain visible and enter the next provider's cold
context. A tool call that has no persisted result is represented to the next
provider as an unknown outcome and must be inspected before it is retried.
Cancellation and force stop never advance to another candidate. If all
candidates fail, PawFlow returns one sanitized exhaustion error while retaining
the work already completed in the conversation. `least_recently_used` orders
eligible candidates by the timestamp of their latest recorded route selection
for this router, oldest (or never selected) first, with priority and position
as tie-breakers. Candidate health is keyed on the service's resolved default
model (same resolution as the client). A provider Retry-After sets the
cooldown deadline; without one, crossing the transient-failure threshold
applies an exponential backoff (30s doubling per extra failure, capped at
30 minutes). The Health, Explain last decision, and Reset health service
actions expose bounded, secret-free state; Explain last decision only reads
events recorded by this router's own scope and identity.

Legacy `llmFailover` definitions migrate before service connection. Valid
definitions become ordered routers while preserving identity and enabled state.
Invalid global definitions abort startup. Invalid user/conversation definitions
are backed up under `data/system/migrations/llm-router-v1/backups` (config
keys with secret-like names are stripped from the backup), replaced by
disabled owner-visible quarantine placeholders, and never block unrelated
tenants. To roll back before repair, stop PawFlow and restore the protected JSON
backup to its original scope; do not run old and new runtime types together.

### 12.7. Distributed Map Cache Client (`distributedMapCache`)

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

- OpenSpace windows and doors occupy real wall openings instead of overlapping solid wall geometry. The resource panels form a compact, obstruction-free gallery on the office face of the meeting-room partition. A persistent camera toolbar provides frontal close-up views of the conversation screen, roster board, FileStore TV, and resources plus a general reset view; a separate Webchat button eases the camera into the live main screen and then hands that same transcript DOM back to simplified view (reduced-motion switches immediately). Manual camera controls can also reach level and wider side views. Human visitors start outside the screen's optical axis, the screen bezel captures its own clicks, and floor walking is accepted only when the floor is the nearest raycast hit inside the room's navigable bounds, with a reserved buffer in front of the projection wall. The environment module loads before the scene module that consumes its layout constants, and reload initialization waits until every ordered deferred OpenSpace module is available, preventing partial scenes without pointer or touch controls. Each conversation id deterministically seeds a visibly distinct room palette, including its walls.

- Conversation view with streaming assistant output, tool calls, tool results, approvals, background tools, and active-agent controls. The View menu stores `chat.view_mode` at conversation scope: `simplified` is the default and renders each user turn as the user message, one expandable live activity block with Messages, Thinking, Tool calls, and Artifacts tabs, then that turn's last message below the block; `classic` retains the flat transcript; `openspace` renders a playful 3D office (three.js, lazily imported) where every attached conversation agent gets a desk immediately — including idle or rate-limited members that emitted no live event — while speech/thought bubbles mirror the live stream, the PC screen lights while a tool runs, a delegating agent walks to its delegate's desk, the selected agent wears a halo, and clicking a configured agent avatar only selects it through the canonical conversation-agent path; clicking that agent's PC opens its recent activity as stacked expandable blocks (bounded per-agent ring, newest first), while clicking a human visitor opens that visitor's activity without changing the selected agent. Configured agents are selectable only from their avatar; their PC and human visitors expose activity without changing selection, and temporary out-of-roster delegate guests remain visual participants that never trigger an invalid agent-selection request. Each `tool_call` additionally drops an emoji tool onto the working agent's desk, shows the tool's name in the agent's thought bubble, and the prop fades away on its `tool_result` or when the agent goes idle; at most 4 props per desk. State animations combine readable whole-body lean/bounce with a procedural chibi rig: deterministic breathing and blinking at idle, a drifting gaze while thinking, a pulsing mouth while talking, alternating arm work for tools, a raised hand while waiting, and alternating arms/feet while walking; the PC screen flickers while busy, thought bubbles carry a cloud tail, and the status chip pulses. The floor ring around each agent is a status carousel: brains (🧠) orbit and zoom in/out while the agent thinks, tools (🔧🛠️⚙️) spin around it while a tool runs, and Zzz (💤) drift around an idle agent — derived from the live state every frame, sprite textures disposed on every swap and on desk retirement. The active-agents tracker (server `list_active` poll plus SSE hints) is the liveness reference for that state: an agent it lists as running is never put to sleep by the quiet-timeout fallback (a long tool run or an unstreamed thinking pass — flash delegates only forward `tool_call`/`tool_result`/`thinking_content` — outlasts the bubble linger window), and an idle avatar the tracker still reports with a fresher entry wakes back up (tool if one is in flight, thinking otherwise); the timeout fallback only applies to agents the tracker omits. The composer mirrors the sender's own message into the scene directly (`openspaceUserMessage` — the SSE stream never echoes it back), and sending attachments makes the user's avatar walk to the target agent's desk, drop one folder prop per file, and walk back. Walking avatars face their destination and use distance-based duration at a stable world-space speed. Clicking empty floor walks the viewer's own avatar there (the spot becomes its new home for delivery returns), the camera pans with right-drag or shift-drag in addition to orbit (drag) and zoom (wheel), and the office carries low-poly decor (plants, rug, couch facing the wall screen, water cooler). Agent and sub-agent thinking streams into the thought bubbles (`text`/`thinking` SSE fields). Agents render as chibi mascots (per-agent silhouette — round ears, horns, antennae, or smooth — hashed from the name), each with a battery gauge above its head mirroring the header context gauge (% remaining — every gauge displays 100 − used %, orange under 20% left; display only — same colors, shared `window._contextUsage` cache), and a chalk blackboard on the left of the office lists the active agents (name, live avatar state or current tool, battery), projected with the same quad transform as the wall screen; each roster row also carries per-agent ⏸ interrupt and ■ stop buttons (the projected board accepts pointer events and reuses the active-agents tracker actions). Resource posters hang on the right wall in rows of 9 — flows, resources, the cognitive panels (memories, knowledge graph, diary, project graph, wiki, scratchpad) plus todo, cost, context editor, plans, scheduled tasks, file explorer, desktop, terminal, and live tmux terminal — and clicking one opens the matching regular panel/dialog (the transient resource-section boards pop above however many poster rows exist). A FileStore TV stands by the left wall: clicking it (or its idle screen) lists the conversation's FileStore files (`list_conv_files`); picking one shows it on the TV screen — a projected DOM panel, so video and audio keep their native controls — with images displayed, video/audio auto-playing, and unknown formats answered on-screen with a pointer to the Files menu; the ✕ on the TV stops playback, and a room switch or view deactivation turns the TV off. Clicking the Resources poster pops one small labeled screen per sidebar-resources sub-section above the poster row (title + item count; clicking the poster again puts them away); clicking one of those screens opens that sub-menu as a live interactive dialog — a clone of the section's sidebar DOM (single renderer, ids stripped, collapse toggle removed, inline +/↻/context-menu handlers intact) refreshed every 2s while open, so every left-menu action works from the scene. Bubbles carry a ✕ to dismiss them (the next message shows them again), turn-end events flush the pending stream coalesce so thoughts never freeze mid-sentence, the camera follows the viewer's avatar unless panned manually (floor-click walking or ⌂ re-engages the follow); on touch, the D-pad buttons raise/lower the camera and strafe left/right — rotation stays on one-finger drag. The flows poster opens a chooser of deployed flows (same `list_resources` source as the sidebar Flows section, so all scopes appear with a scope letter); picking one projects it on a 3D stage past the poster wall: one block per task colored by state (green running, grey stopped, red error), links between blocks, and an animated current of dots whose density follows the queue size and which turns red under backpressure, refreshed from `flow_runtime_graph` every 2.5s (geometry is built once per level; polls only recolor). Process-group/subflow blocks render blue: clicking one drills into that subflow's graph (the poll follows a `flow_ref` stack), a green 3D up-arrow pops one level, and a red 3D ✕ inside the stage — plus the DOM ✕ button and Escape — closes it, restoring the previous framing, stopping the poll, and disposing the stage. An office door stands by the wall screen: clicking it opens a conversation picker dialog (own + shared, current highlighted, rows call `resumeConv`), and each conversation is its own room — background, fog, floor, rug and couch colors derive deterministically from the conversation id, so the same conversation always has the same palette. The frame above the wall screen shows the conversation title (projected DOM strip, refreshed on change). Flash/out-of-roster delegate guests get a desk when the delegation starts and the desk is dismantled after their `sub_agent_done` (seat slots return to a pool for reuse); an in-conversation delegation walks the delegating agent to the delegate's desk, announces it, and walks straight home; an `a2a` tool call walks the agent to the door, announces the target, and returns. Projected panels backface/edge-on cull (a quad seen from behind hides instead of smearing a mirrored image) and stack by camera distance via a bounded z-index. Because the live wall transcript is DOM rather than WebGL, a transparent foreground pass is clipped to its projected quad and world plane so real scene geometry in front of the screen — including ceiling lights — occludes it correctly without sacrificing scrolling or interaction. On touch devices: pinch zooms, two-finger drag pans, and a D-pad overlay (▲▼ height, ◀▶ orbit, ＋－ zoom, ⌂ reset) shows on coarse pointers; WebGL DPR adapts between 0.75 and the device cap from smoothed frame time; detected software renderers stay at DPR 1 with antialiasing disabled. Resizes are debounced so mobile keyboard animations no longer blink the scene, and the composer clears its inline height when empty so it returns to its default size after a send. A controls hint sits bottom-left of the scene (orbit/pan/zoom/click-to-walk), and a ResizeObserver keeps the canvas in sync with the wrap so projected panels never drift off their meshes. Every human author gets a standing visitor avatar in a row facing the desks — shared conversations render one avatar per distinct user (`source.name`), each with speech bubbles for their messages. Agent bubbles are persistent: after the linger delay the most recent bubble only dims (`osv-stale`) instead of disappearing, and an idle (Zzz) agent always shows its last *message* — its thought bubble is put away and the last speech comes back dimmed unless the viewer dismissed it with ✕. Live user bubbles are transient and fade out 10 s after they appear; the user bubble restored from history at load stays until a live one replaces it. A full history render seeds the last bubble, avatars, and activity logs from the transcript (`openspaceSeedHistory`, deduplicated by `msg_id`, reset on conversation switch). A cinema wall screen stands behind the visitor row facing the desks, and the live simplified view is projected onto it: the real `#messages` element is reparented (never copied) into a DOM panel that a projective `matrix3d` transform glues to the wall quad every frame (same camera projection as the bubbles), so the projected transcript keeps streaming, scrolling, and expanding; deactivation puts the element back where it was. Openspace therefore runs simplified rendering underneath, so switching back is instant and the 3D layer never owns conversation state. A conversation that never chose follows the default, and an explicit choice at any scope of the cascade still wins. Turn boundaries are **positional**, not correlated, and the layout is one rule repeated: top level holds a user row, that turn's block, and the block's last message — nothing else. A user message opens a turn and closes the previous one; every row rendered after it is filed in that block; a `done` freezes the block's status without orphaning what follows. `turn_id` names a turn but routes nothing, so it can no longer be missing. Activity with no user row above it — a history window that opens mid-turn, work resumed after a turn the provider already closed — opens a turn of its own, and a turn outlives the row it was anchored on. `turnViewReconcile` enforces the rule against the DOM after every history render and every page of older history: a stray row is filed into the turn it falls in, a replayed turn is given its last message under its block instead of buried inside it, and older user boundaries cannot stop the newest live block. The spot under the block belongs to the last message row of the turn and changes hands as newer rows arrive, but only ever to a row of that turn: the promotion is refused unless the row is already filed inside the block or sits after it at top level, so a `final_msg_id` naming a message from an earlier turn — which a `done` can still carry when the turn produced nothing of its own — names it without moving it. Replayed history is exempt from the positional rule, since an older page arrives after rows that precede it: there the durable `turn_final` marker decides, and when no row of a turn carries it the display classifier marks the last visible assistant row and flags it `turn_final_derived`, so conversations recorded before the feature still render their answer outside the block. A turn that produced no visible answer is left without a final row; the view never manufactures one. Delegate boxes are required activity in simplified mode regardless of the classic `chat.group_delegate_messages` preference, and persisted `sub_agent_trace` rows reload in Tool calls. While a turn runs, the block header counts its seconds and freezes on what it took, and its activity surface is a themed glyph rain out of which each cue — message text or a full copy of the tool-call row — condenses character by character, newest cue on top of a fading column; a cue holds its place until a newer one arrives. Successful `show_file` results appear once in Artifacts; actionable approvals, questions, and durable turn errors remain top-level. In classic mode, `chat.group_technical_messages` can collapse consecutive technical rows when it resolves to a truthy value (`true`, `1`, `yes`, `on`); the default is `true`. Tool-call groups keep a stable `tc_id` boundary so a reload does not merge unrelated tools into one technical details block. View options write conversation-scoped parameters and reload through the canonical history path. The floating scroll controls use explicit top/bottom navigation; the top button does not trigger history lazy-loading by itself.
- The Appearance panel stores a 75–150% UI scale plus an optional image/video background and atmosphere controls (dark overlay, blur, saturation, panel opacity and slow motion). Each authenticated user owns a server-backed global appearance that conversations inherit, while the scope selector can create or remove a server-backed conversation override. Uploads are private, non-expiring FileStore media (80 MiB maximum), synchronized across the user's devices and deleted after their last appearance reference is replaced; localStorage and IndexedDB remain an instant/offline cache and support a one-shot migration of older browser-only settings. Remote URLs require HTTPS, and reduced-motion plus page visibility pause animation/video. The conversation-controls strip provides direct compact access to Refresh, permissions, conversation theme, conversation appearance, and OpenSpace; Refresh is the first icon action, while permission/theme controls keep full menus and accessible current-value tooltips behind compact closed states. With atmosphere media active, the transcript surfaces, action dock, input area and unified composer become translucent so the background continues behind the full lower chrome instead of ending at an opaque footer. The composer is one responsive component containing attachment, search, slash command, agent mention, speech-to-text, terminal grab and Send. Typing or pressing `/` opens a filtered command catalog and `@` opens a filtered conversation-agent catalog; both menus support keyboard navigation. `Ctrl/Cmd+K` and `/search` share an overlay over the latest 500 messages; fenced code blocks have a language/copy header and the memory panel renders theme-token records.
- Runtime notifications are separate from `#messages` and therefore never participate in turn grouping or history reconciliation. A notification produces a temporary toast and an unread badge; the header bell opens the tab-local notification center with full details. Entries survive conversation switches in the same tab but are cleared by page reload, are not stored in browser persistence, and are never written to the conversation or LLM context. Progress operations such as compaction update one keyed notification in place. Blocking questions and approvals remain actionable dialogs rather than expiring notifications.
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

The listener authenticates users through PawFlow session cookies/API keys and applies route-level capability tokens for browser-accessible runtime resources such as VNC, terminal, code-server, and port-forward routes. Browser sessions use a sliding expiry: every authenticated cookie request refreshes the browser cookie and the in-memory session, while the renewed server-side expiry is persisted at most once every five minutes so active logins survive a server restart without writing the session file on every request. Explicit logout still revokes the session and its bound capability tokens immediately.

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

## 19. Declarative Workflow Runtime Tasks

| Type | Purpose | Relationships |
|------|---------|---------------|
| `boundedLoopGuard` | Enforce aggregate duration, FlowFile count, and cancellation bounds before a declarative collection iteration. | `continue`, `exhausted`, `cancelled`, `failure` |
| `repeatUntil` | Run one isolated child iteration, test a condition, and durably queue or time the bounded continuation. | `success`, `exhausted`, `cancelled`, `failure` |
| `invokeWorkflowAgent` | Invoke an exact Workflow Agent resource with pinned authority and optionally park the parent until one terminal result. | `submitted` plus typed child terminal relationships and `failure` |
| `completeFlowRun` | Stage and commit the sole typed terminal result of a durable one-shot FlowRun. | `completed`, `failure` |

These tasks require injected runtime context; request parameters cannot invent a
run, authority, or conversation scope. Declarative lowering also reuses the
durable interaction tasks documented in
[confirmations.md](confirmations.md). Only the closed workflow-safe task
catalog is accepted inside Workflow Agent definitions.

---

**End of Technical Documentation**

*Version: 2.1.0*
*Date: 2026-03-14*
*70+ tasks, 11 services, 76+ filesystem tests, REST API, RBAC, Plugins, Docker*

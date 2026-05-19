# Task Catalog - PawFlow

PawFlow ships **100+ built-in tasks** organized into **5 categories**. The exact count can change as handlers and adapters are registered. Each task is a processing node in a flow: it receives a FlowFile, transforms or routes it, and emits one or more FlowFiles downstream.

Additionally, every agent tool is automatically exposed as a `tool.*` flow task
via the ToolTaskAdapter, giving flows access to the full agent toolbox without
code duplication.

---

## Categories at a Glance

| Category | Count | Purpose |
|----------|------:|---------|
| **System** | 11+ | Core utilities: logging, attribute manipulation, scripting, scheduling |
| **IO** | 50+ | External I/O: files, HTTP, messaging, email, relays, auth, admin UI |
| **Data** | 25+ | Content transformation: JSON, CSV, XML, SQL, caching, LLM inference |
| **Control** | 10+ | Flow logic: routing, splitting, merging, throttling, signaling, subflows |
| **AI** | 2+ | Agent loop and agent action runtime |

---

## System Tasks (11)

| Type | Description |
|------|-------------|
| `cronTrigger` | Generate a FlowFile on a CRON schedule |
| `executeScript` | Execute a Python script on FlowFile content |
| `fail` | Explicitly fail a FlowFile |
| `generateFlowFile` | Generate new FlowFiles with configurable content |
| `hashContent` | Hash the content of a FlowFile |
| `listFiles` | List files in a directory with filtering and tracking |
| `log` | Log a message with formatting |
| `replace_text` | Replace text in FlowFile content |
| `reporting` | Collect and report execution metrics as FlowFile content |
| `updateAttribute` | Modify FlowFile attributes (add, update, delete) |
| `wait` | Wait for a configured duration before continuing |

---

## IO Tasks (51)

| Type | Description |
|------|-------------|
| `adminAction` | Handle PawFlow admin API requests |
| `agentSSEStream` | Stream agent events to the client via SSE |
| `assignTaskToAgent` | Assign a recurring task to an agent in a linked conversation |
| `cancelAgentTask` | Cancel a recurring task assigned to an agent |
| `consumeKafka` | Consume messages from a Kafka topic |
| `consumeMQTT` | Consume messages from an MQTT topic |
| `createConversation` | Create a new conversation for publishing messages or spawning agents |
| `discordReceiver` | Receive messages from a Discord bot |
| `discordSend` | Send a message to a Discord channel |
| `fetchHTTP` | HTTP request with intelligent scraping (anti-bot, JS rendering) |
| `filesystemOps` | Perform filesystem operations via a filesystem service |
| `getAzureBlob` | Download a blob from Azure Blob Storage |
| `getFile` | Read a file from the filesystem |
| `getGCS` | Download an object from Google Cloud Storage |
| `getS3` | Download an object from AWS S3 or compatible storage |
| `getSFTP` | Download a file from an SFTP server |
| `handleHTTPResponse` | Send an HTTP response back through the HTTP listener |
| `httpReceiver` | Receive HTTP requests from a shared HTTP listener service |
| `listSFTP` | List files on an SFTP server with filtering and tracking |
| `listenHTTP` | Generate a FlowFile from HTTP request data (simulated for pipeline use) |
| `notifySlack` | Send a message to Slack via Incoming Webhook |
| `oauthCallback` | Handle OAuth2 provider callback and create user session |
| `oauthLogout` | Invalidate session and clear authentication cookie |
| `oauthRedirect` | Redirect user to OAuth2 provider for login |
| `publishKafka` | Publish FlowFile content to a Kafka topic |
| `publishMQTT` | Publish FlowFile content to an MQTT topic |
| `publishMessage` | Publish a message into a linked conversation |
| `putAzureBlob` | Upload a blob to Azure Blob Storage |
| `putFile` | Write a FlowFile to the filesystem |
| `putGCS` | Upload an object to Google Cloud Storage |
| `putS3` | Upload an object to AWS S3 or compatible storage |
| `putSFTP` | Upload a file to an SFTP server |
| `readConversation` | Read messages from a linked conversation |
| `scraplingFetch` | Fetch web pages with anti-bot handling, JS rendering, and CSS selectors |
| `sendEmail` | Send an email via SMTP (password or OAuth2 for Gmail/Microsoft) |
| `serveAdminUI` | Serve the native PawFlow administration interface |
| `serveAssets` | Serve static assets (JS, CSS, images) from the flow directory |
| `serveChatUI` | Serve an HTML chat interface for the agent |
| `serveFile` | Serve a file from the temporary file store |
| `serveLogin` | Dynamic login page with multi-provider support |
| `slackReceiver` | Receive messages from a Slack bot |
| `slackSend` | Send a message to Slack via Incoming Webhook |
| `spawnAgent` | Spawn an agent in a linked conversation (sync or async) |
| `telegramReceiver` | Receive messages from a Telegram bot |
| `telegramSend` | Send a message to a Telegram chat |
| `validateHTTPAuth` | Validate Bearer/Basic authentication on HTTP requests |
| `validateSessionAuth` | Validate cookie/bearer session authentication |
| `whatsappReceiver` | Receive messages from WhatsApp |
| `whatsappSend` | Send a message via WhatsApp |

---

## Data Tasks (27)

| Type | Description |
|------|-------------|
| `attributesToJSON` | Convert FlowFile attributes to JSON content |
| `base64Encode` | Encode or decode FlowFile content in Base64 |
| `compressContent` | Compress or decompress FlowFile content |
| `convertAvroToJSON` | Convert binary Avro content to JSON |
| `convertCSVToJSON` | Convert CSV content to JSON |
| `convertCharset` | Convert content character encoding |
| `convertJSONToAvro` | Convert JSON content to binary Avro |
| `convertJSONToCSV` | Convert JSON content to CSV |
| `convertJSONToParquet` | Convert JSON content to Parquet |
| `convertParquetToJSON` | Convert Parquet content to JSON |
| `countText` | Count lines, words, and characters in FlowFile content |
| `detectDuplicate` | Detect duplicate FlowFiles based on content hash or attribute |
| `evaluateJSONPath` | Evaluate simple JSONPath expressions on JSON content |
| `executeSQL` | Execute a SQL query and return results as JSON |
| `extractText` | Extract text from FlowFile content using regex |
| `fetchDistributedMapCache` | Retrieve a value from the distributed map cache by key |
| `filterContent` | Filter content lines by a regex pattern |
| `getCache` | Retrieve FlowFile content from cache |
| `inferLLM` | Send content to an LLM and get the response |
| `parseXML` | Convert XML content to JSON |
| `putCache` | Store FlowFile content in cache |
| `putDistributedMapCache` | Store a value in the distributed map cache by key |
| `putSQL` | Execute a SQL statement (INSERT/UPDATE/DELETE) |
| `splitJSON` | Split a JSON array into individual FlowFiles, one per element |
| `transformJSON` | Transform JSON content (extract, modify, filter) |
| `transformXML` | Transform XML content using XSLT or operations |
| `validateJSON` | Validate that FlowFile content is well-formed JSON |

---

## Control Tasks (11)

| Type | Description |
|------|-------------|
| `controlRate` | Throttle FlowFile throughput by adding delay |
| `duplicateContent` | Create copies of FlowFile content |
| `funnel` | Merge multiple connections into a single output |
| `inputPort` | Input port for sub-flow or process-group entry |
| `mergeContent` | Merge multiple FlowFiles into one (supports correlation) |
| `notify` | Send a signal to the SignalRegistry |
| `outputPort` | Output port for sub-flow or process-group exit |
| `routeOnAttribute` | Route FlowFiles based on attribute values |
| `splitContent` | Split a FlowFile by a separator |
| `stopFlow` | Stop the current flow execution |
| `waitForSignal` | Wait for a signal from the SignalRegistry before continuing |

---

## AI Tasks (1)

| Type | Description |
|------|-------------|
| `agentLoop` | LLM agent with tool-use loop (function calling) |

The `agentLoop` task is the core AI processor. It runs a full agent loop: sends
messages to an LLM, receives tool-call requests, executes tools, and returns the
final response as FlowFile content. Supports streaming, multi-agent delegation,
and context compaction.

---

## How Tasks Work

Every task follows the same contract:

1. **FlowFile in** -- The task receives a FlowFile containing content (bytes) and
   attributes (string key-value metadata).
2. **Process** -- The task reads configuration parameters, inspects or transforms
   the FlowFile content and attributes, and performs its work (I/O, computation,
   routing, etc.).
3. **FlowFile out** -- The task returns a list of FlowFiles. Most tasks return a
   single FlowFile; splitters return many; filters may return none.

Tasks can also set attributes on outgoing FlowFiles (e.g., `http.status.code`
after an HTTP fetch, `fragment.index` after a split).

---

## Referencing Tasks in Flows

Flows are defined as JSON DAGs. Each node references a task by its **type string**:

```json
{
  "id": "fetch-data",
  "type": "fetchHTTP",
  "config": {
    "url": "https://api.example.com/data",
    "method": "GET"
  },
  "connections": {
    "success": ["transform-step"]
  }
}
```

The `type` field must match the task's `TYPE` class attribute exactly (case-sensitive).

---

## Tool Tasks (`tool.*` prefix)

PawFlow automatically wraps every agent tool handler as a flow task via the
`ToolTaskAdapter`. This means any tool an agent can call is also available as a
flow node, with the type `tool.<handler_name>`.

**How it works:**
- At startup, `register_tool_tasks()` iterates all registered `ToolHandler`
  instances and creates a dynamic `Task` subclass for each one.
- Arguments are resolved from three sources (in priority order): FlowFile content
  (JSON), FlowFile attributes, and static task configuration.
- The output FlowFile contains the tool result as content, plus `tool.name` and
  `tool.status` attributes.

**Excluded tools** (agent-internal, meta-tools, or control-plane actions):
`get_tool_schema`, `use_tool`, `ScheduleWakeup`, `PushNotification`,
`complete_task`, `verify_task`, `manage_resource`, `create_tool`,
`pawflow_help`, `update_plan`, `create_plan`, `link_identity`,
`browser_action`.

For a fuller agent-facing catalog, including internal/control tools, see
[Agent Tool Catalog](tool_catalog.md).

**Available tool tasks include:**

| Type | Description |
|------|-------------|
| `tool.execute_script` | Run a script (bash, Python, etc.) |
| `tool.web_search` | Search the web |
| `tool.fetch` | Fetch a web page with anti-bot handling |
| `tool.share_file` | Create and share a file |
| `tool.schedule_continuation` | Persist a delayed continuation wake-up for the current conversation |
| `tool.local_files` | Manage local files on the filesystem |
| `tool.generate_image` | Generate an image via an image model |
| `tool.edit_image` | Edit one or more existing images via the image model (requires a model that declares an `edit_image` operation in `pixazo_catalog.json`, e.g. `nano-banana`). |
| `tool.generate_video` | Generate a video from text, image, video, or start+end frames. Supports text-to-video, image-to-video, video-edit, and frame-to-video modes via `image_url`, `video_url`, `end_image_url` params. |
| `tool.generate_audio` | Generate audio via an audio model |
| `tool.generate_3d` | Generate a 3D model from a prompt or image. |
| `tool.upscale_image` | Upscale an image. |
| `tool.upscale_video` | Upscale a video (SeedVR, Topaz). Requires `video_url`. |
| `tool.describe_image` | Describe an image in natural language (Ideogram). Requires `image_url`. Returns `{description}`. |
| `tool.remix_image` | Remix an image with a text prompt (Ideogram). Requires `prompt` + `image_url`. |
| `tool.remove_background` | Remove background from an image (Bria RMBG 2.0). Requires `image_url`. |
| `tool.try_on` | Virtual try-on from person and garment images. |
| `tool.lipsync` | Lip-sync face media to an audio track. |
| `tool.speech_to_video` | Speech-to-video from image + audio (Wan 2.2 S2V). Requires `image_url` + `audio_url`. |
| `tool.train_image_model` | Train/fine-tune an image model or LoRA where supported. |
| `tool.clone_voice` | Register or reuse a voice clone from a reference audio sample. |
| `tool.speak` | Synthesize speech with a registered voice clone. |
| `tool.delete_voice` | Delete a voice clone and cached renderings. |
| `tool.get_image_model_info` | Get info about available image models |
| `tool.remember` | Store a memory in the agent's memory |
| `tool.recall` | Recall memories by keyword search |
| `tool.semantic_recall` | Recall memories by semantic similarity |
| `tool.forget` | Delete a memory |
| `tool.check_duplicate` | Check if a memory already exists |
| `tool.memory_navigate` | Navigate and browse agent memory |
| `tool.diary_write` | Write a diary entry |
| `tool.diary_read` | Read diary entries |
| `tool.assign_task` | Assign a recurring task to an agent |
| `tool.notify_user` | Send a notification to the user |
| `tool.ask_user` | Ask the user a question and wait for a reply |
| `tool.delegate` | Spawn or delegate work to another agent |
| `tool.show_file` | Display a file to the user |
| `tool.manage_flow` | Manage flows (start, stop, status) |
| `tool.store_secret` | Store a secret securely |
| `tool.list_secrets` | List stored secrets |
| `tool.read_history` | Read conversation history |
| `tool.compact_result` | Compact a long tool result |
| `tool.read_parent_context` | Read the parent agent's context |
| `tool.run_tests` | Run project tests |
| `tool.security_scan` | Run a security scan |
| `tool.screen` | Capture a screenshot |
| `tool.project_graph` | Query or update the project knowledge graph |
| `tool.kg_add` | Add nodes/edges to the knowledge graph |
| `tool.kg_query` | Query the knowledge graph |
| `tool.kg_invalidate` | Invalidate knowledge graph entries |
| `tool.kg_timeline` | View knowledge graph timeline |
| `tool.kg_stats` | Get knowledge graph statistics |
| `tool.query_graph` | Run a graph query |
| `tool.kg_god_nodes` | Find highly connected nodes in the knowledge graph |
| `tool.read` | Read a file |
| `tool.write` | Write a file |
| `tool.edit` | Edit a file (find and replace) |
| `tool.batch_edit` | Batch edit multiple files |
| `tool.apply_patch` | Apply a unified diff patch |
| `tool.find_replace` | Find and replace text in files |
| `tool.delete` | Delete a file |
| `tool.mkdir` | Create a directory |
| `tool.stat` | Get file metadata |
| `tool.exists` | Check if a file exists |
| `tool.list_dir` | List directory contents |
| `tool.glob` | Find files matching a glob pattern |
| `tool.grep` | Search file contents with regex |
| `tool.bash` | Run a bash command |
| `tool.Monitor` | Run a command and return early on exit or regex match |
| `tool.browser` | Run browser automation actions through the browser service |
| `tool.notebook_edit` | Edit a Jupyter notebook cell |
| `tool.copy` | Copy a file |
| `tool.see` | View an image or screenshot |
| `tool.delete_tool` | Delete a user-created dynamic tool |
| `tool.learn` | Extract useful learnings from conversation history |
| `tool.link_resource` | Link or unlink a relay/resource binding |
| `tool.approve_plan` | Approve a plan for execution |
| `tool.assign_plan` | Assign a plan to an agent |
| `tool.cancel_plan` | Cancel an active plan |
| `tool.delete_plan` | Delete a plan |
| `tool.verify_plan_step` | Mark a plan step as verified |

**Example -- using an image generation tool in a flow:**

```json
{
  "id": "make-image",
  "type": "tool.generate_image",
  "config": {
    "prompt": "A sunset over the ocean",
    "model": "dall-e-3"
  },
  "connections": {
    "success": ["save-result"]
  }
}
```

---

## Creating Custom Tasks

See [development.md](development.md) for the full guide.

```python
from core import FlowFile, Task

class MyTask(Task):
    TYPE = "myCustom"
    DESCRIPTION = "Does something useful"

    def get_parameter_schema(self):
        return {'param': {'type': 'string', 'required': True}}

    def execute(self, flowfile):
        value = self.config.get('param', '')
        flowfile.set_attribute('processed', 'true')
        return [flowfile]
```

Register the task by placing the file under `tasks/<category>/` and it will be
auto-discovered at startup.

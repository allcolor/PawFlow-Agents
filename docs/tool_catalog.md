# Agent Tool Catalog

PawFlow exposes tools to agents through `ToolHandler` classes. Most tools are also available inside flows as `tool.<name>` tasks through `ToolTaskAdapter`.

This catalog is grouped by purpose. Use `get_tool_schema(tool_name)` at runtime for the exact JSON schema of a tool.

## Filesystem and Editing

Filesystem-backed tools accept two routing controls in their runtime schema:

- `relay`: select the relay/filesystem service id for the operation. It is an alias for the tool's native selector (`source`, `destination`, `filesystem`, or `service`) depending on the tool.
- `local`: when `false` or omitted, execute inside the relay Docker container. When `true`, forward the operation through the relay host helper and execute against the host filesystem/process namespace. This requires the relay to run with `--allow-local`.

Use `get_tool_schema(tool_name)` for the exact native selector names and required fields.

| Tool | Purpose |
|---|---|
| `read` | Read a file through the active filesystem/relay. |
| `write` | Write a file. |
| `edit` | Exact string or line-based file edit. |
| `batch_edit` | Apply multiple exact replacements. |
| `apply_patch` | Apply a unified diff patch. |
| `find_replace` | Regex find/replace. |
| `delete` | Delete a file or directory. |
| `mkdir` | Create a directory. |
| `stat` | Get file metadata. |
| `exists` | Check existence. |
| `list_dir` | List directory contents. |
| `glob` | Find files by glob. |
| `grep` | Search file contents. |
| `copy` | Copy files between filesystem services/FileStore. |
| `notebook_edit` | Edit a Jupyter notebook cell. |

## Execution, DevOps, and Desktop

| Tool | Purpose |
|---|---|
| `bash` | Run a shell command through the relay. |
| `Monitor` | Run a command and return early on exit or regex match. |
| `execute_script` | Execute a script/tool-backed snippet. |
| `run_tests` | Run tests through the project environment. |
| `security_scan` | Run security checks. |
| `screen` | Screenshot/click/type/key/scroll/mouse-position against local or Docker desktop. |
| `browser` | Browser automation action through the browser service. |
| `see` | Analyze an image, video, or audio artifact. |
| `project_graph` | Build/query the code structure graph. |

## Web and Search

| Tool | Purpose |
|---|---|
| `web_search` | Search the web. |
| `fetch` | Fetch/extract a web page. |
| `share_file` | Share a generated file with the user. |
| `show_file` | Open a file in the user's chat viewer. |

## Media

| Tool | Purpose |
|---|---|
| `generate_image` | Generate an image. |
| `edit_image` | Edit one or more images. |
| `get_image_model_info` | Inspect image model capabilities. |
| `describe_image` | Describe image content. |
| `remix_image` | Remix an image with a prompt. |
| `remove_background` | Remove an image background. |
| `generate_video` | Generate or edit video. |
| `generate_audio` | Generate audio or music. |
| `generate_3d` | Generate a 3D model. |
| `upscale_image` | Upscale an image. |
| `upscale_video` | Upscale a video. |
| `try_on` | Virtual try-on from person + garment images. |
| `lipsync` | Lip-sync face video/image to audio. |
| `speech_to_video` | Generate speaking video from face image + audio. |
| `train_image_model` | Train/fine-tune an image model/LoRA. |
| `clone_voice` | Register/reuse a voice clone. |
| `speak` | Synthesize speech using a voice clone. |
| `delete_voice` | Delete voice clone state and cached renders. |

## Memory and Cognitive Tools

| Tool | Purpose |
|---|---|
| `remember` | Store a memory. |
| `recall` | Keyword memory recall. |
| `semantic_recall` | Semantic memory recall. |
| `forget` | Delete a memory. |
| `check_duplicate` | Detect duplicate memories. |
| `memory_navigate` | Browse memory taxonomy. |
| `learn` | Extract learnings from conversation. |
| `diary_write` | Write an agent diary entry. |
| `diary_read` | Read diary entries. |
| `kg_add` | Add knowledge graph triples. |
| `kg_query` | Query graph facts. |
| `kg_invalidate` | Expire graph facts. |
| `kg_timeline` | View graph timeline. |
| `kg_stats` | Graph statistics. |
| `query_graph` | Traverse graph connections. |
| `kg_god_nodes` | Find highly connected entities. |

## Multi-Agent, Plans, and Tasks

| Tool | Purpose |
|---|---|
| `delegate` | Spawn/delegate work to another agent. |
| `manage_resource` | Create/update/delete/list agents, skills, tools, services, resources. |
| `assign_task` | Assign a recurring autonomous task. |
| `complete_task` | Report task progress/completion. |
| `verify_task` | Verify a completed task. |
| `create_plan` | Create a structured plan. |
| `update_plan` | Update plan/step state. |
| `approve_plan` | Approve a plan. |
| `assign_plan` | Assign plan steps to agents. |
| `cancel_plan` | Cancel a plan. |
| `delete_plan` | Delete a plan. |
| `verify_plan_step` | Verify a completed step. |
| `EnterPlanMode` | Force plan-first behavior. |
| `ExitPlanMode` | Exit plan mode. |
| `ask_user` | Ask the user a blocking question. |
| `notify_user` | Notify the user. |
| `PushNotification` | Send a push notification event. |
| `ScheduleWakeup` | Schedule an agent wakeup. |
| `schedule_continuation` | Persist a delayed continuation wake-up for the current conversation. |
| `read_parent_context` | Read parent task/agent context. |
| `read_history` | Read conversation history. |
| `compact_result` | Return a compaction result. |

## Resources, Secrets, Identity, and Meta Tools

| Tool | Purpose |
|---|---|
| `store_secret` | Store an encrypted secret. |
| `list_secrets` | List secret names. |
| `link_identity` | Link cross-channel identity. |
| `link_resource` | Link/unlink relay/resource binding. |
| `create_tool` | Register a dynamic tool. |
| `delete_tool` | Delete a dynamic tool. |
| `get_tool_schema` | Inspect a tool schema. |
| `use_tool` | Execute a tool by name. |
| `pawflow_help` | Get platform help. |

## Flow Task Availability

`ToolTaskAdapter` registers most tools as `tool.<name>` tasks. Some tools are intentionally skipped because they are agent-internal, meta-tools, or resource/control actions that do not make sense as flow nodes.

Skipped by default:

```text
get_tool_schema, use_tool, ScheduleWakeup, PushNotification,
complete_task, verify_task, manage_resource, create_tool,
pawflow_help, update_plan, create_plan, link_identity,
browser_action
```

Even skipped tools should still be documented here because agents can call them directly.

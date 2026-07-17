# Agent Tool Catalog

PawFlow exposes tools to agents through `ToolHandler` classes. Most tools are also available inside flows as `tool.<name>` tasks through `ToolTaskAdapter`.

This catalog is grouped by purpose. Use `get_tool_schema(tool_name)` at runtime for the exact JSON schema of a tool.

## Filesystem and Editing

Filesystem-backed tools accept two routing controls in their runtime schema:

- `relay`: select the relay/filesystem service id for the operation. It is an alias for the tool's native selector (`source`, `destination`, `filesystem`, or `service`) depending on the tool.
- `local`: when `false` or omitted, execute inside the relay Docker container. When `true`, forward the operation through the relay host helper and execute against the host filesystem/process namespace. This requires the relay to run with `--allow-local`.

Use `get_tool_schema(tool_name)` for the exact native selector names and required fields.

Conversation-linked `rcloneFilesystem` services are mounted by linked relays
under `/remote/<service_id>` when the relay image has `rclone`. Tools and shell
commands can use those paths like normal files. Global and native API-backed
filesystem services are not exported into relays; select them explicitly
through PawFlow filesystem tool parameters instead.

Discovery preference: use `search` when you need glob filtering, regex matching, and contextual snippets in one call. Use `glob` for file lists and `grep` for simple content matches.

Editing preference: use `apply_patch` for patch-shaped changes and `batch_edit` for coordinated replacements, then `edit` for small targeted changes, then `write` only when creating or fully replacing a file.

| Tool | Purpose |
|---|---|
| `read` | Read a file through the active filesystem/relay; use `mode="outline"` for compact code structure with bodies stubbed. |
| `write` | Write a file. |
| `edit` | Exact string or line-based file edit. Exact unique replacements no longer require a prior read; whitespace drift is tolerated, and `fuzzy=true` enables one high-confidence fuzzy match. |
| `batch_edit` | Apply multiple replacements atomically across files, with aggregate replacement totals. |
| `apply_patch` | Apply a unified diff or `*** Begin Patch` block. `path` is optional when the patch contains file paths. |
| `find_replace` | Regex find/replace. `multiline=true` enables `^`/`$` line-boundary matching. |
| `delete` | Delete a file or directory. |
| `mkdir` | Create a directory. |
| `stat` | Get file metadata. |
| `exists` | Check existence. |
| `list_dir` | List directory contents. |
| `glob` | Find files by glob. |
| `grep` | Search file contents. |
| `search` | Combined glob + regex + ranked snippets for fewer discovery calls. |
| `copy` | Copy files between filesystem services/FileStore. |
| `notebook_edit` | Edit a Jupyter notebook cell. |

## Execution, DevOps, and Desktop

| Tool | Purpose |
|---|---|
| `bash` | Run a shell command through the relay. Accepts `command` or `cmd`. |
| `Monitor` | Run a command and return early on exit or regex match. |
| `execute_script` | Execute a script/tool-backed snippet. |
| `run_tests` | Run tests through the project environment; accepts `max_output` to cap returned output. |
| `security_scan` | Run security checks. |
| `screen` | Screenshot/click/type/key/scroll/mouse-position against local or Docker desktop. |
| `browser` | Browser automation action through the browser service. |
| `see` | Analyze an image, video, or audio artifact. |
| `project_graph` | Build/query the code structure graph. |

When `PAWFLOW_USE_RTK` is set to a truthy value (`1`, `true`, `yes`, `on`) and
the selected relay target has the `rtk` binary, PawFlow uses RTK for compatible
relay-backed calls: `bash` and `run_tests` run `rtk rewrite <command>` before
execution, while `read` uses `rtk read`. `grep` and `glob` stay native because
RTK output does not preserve PawFlow's grep/glob response semantics reliably.
If the variable is not truthy, RTK is missing, or RTK cannot handle a request,
PawFlow falls back to the native tool behavior unchanged.

## Web and Search

| Tool | Purpose |
|---|---|
| `web_search` | Search the web across configurable providers, aggregate results, and deduplicate URLs. |
| `fetch` | Fetch/extract a web page. |
| `share_file` | Share a generated file with the user. |
| `show_file` | Open a file in the user's chat viewer. |

`web_search` accepts `query` (or `q`), `max_results` (or `maxResults`), and
`provider` / `search_provider` as a single provider or a comma-separated chain.
Supported no-key providers are `google`, `bing`, and `duckduckgo`; the default
chain is `google,bing`. The same default can be set with the PawFlow variable
`web_search_providers` (conversation → user → global, with OS env fallback only
after PawFlow variables). Google and Bing use static HTML when available and
browser stealth fallbacks when needed; the browser fallback uses
`PAWFLOW_CHROMIUM_EXECUTABLE` when set, then common system Chromium binaries.
Bing falls back to RSS only after browser search fails. When a relay is
connected, `web_search` runs inside that relay so browser/search dependencies
match the user's execution environment; if no relay is available it runs on the
PawFlow server host. Both
runtimes must have the declared scraping dependencies and managed browser binary
installed. Results are interleaved across contributing providers, duplicate URLs
merge provider labels, and ranking is generic: query-term relevance plus text
pages before image results before video results.

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
| `speak` | Synthesize speech through the active TTS provider using a registered voice alias or provider-native voice. |
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
| `flash_delegate` | Create temporary task-specific agents for independent parallel work; they use the caller's LLM service and disappear after completion. Background results are delivered to the caller (preempt/wake) — and when the caller is on a live realtime voice session, the result is ALSO injected into the session and spoken (out-of-band `context` message). |
| `consult_agent` | One-shot delegation to the conversation agent's own model: resolves the agent's system prompt and `llm_service`, sends the task with bounded conversation context, returns the answer as the tool result. Approval-exempt (the delegate gets no tools). Built for realtime voice sessions (`tool_profile=consult_agent`) where the realtime model is only the spoken interface and routes substantial work to the agent's brain; works from text sessions too. |
| `manage_resource` | Create/update/delete/list agents, skills, tools, services, resources; review/import marketplace skills; assign/unassign skills to agents with live context notifications. Creates resources in conversation scope when called from an active conversation. |
| `manage_package` | Build, inspect, install, export, list, and uninstall signed PawFlow Package (`.pfp`) artifacts with selectable objects and provenance. |
| `load_skill` | Load the full prompt for a skill assigned to the current agent. Records per-skill usage statistics, appends a self-improvement footer, and suggests promoting a repeatedly-loaded conversation-scoped skill to user scope. |
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

The web chat Resource Panel persists the expanded/collapsed tree state in the browser. On a first visit only `Agents` is open; after toggling sections, reloads restore the exact opened and closed sections.

## Tool and MCP Availability

Conversation tool filters keep built-in tools enabled by default. Dynamic tools
from conversation scope are also enabled by default; dynamic global/user tools
must be explicitly checked. MCP servers are opt-in: none are enabled until they
are checked at conversation level or in an agent override. Each agent can
optionally override conversation defaults; without an override it inherits the
conversation filter. HTTP MCP resources can target a user-local service through
the relay-proxy URL form `relay://&#36;{conv.relay}/localhost:<port>/<path>`. Stdio
MCP resources run via a relay, and `local=true` runs the command on the relay
host helper instead of inside the relay container.

## Chat Themes

The web chat ships with built-in themes (`PawFlow Dark`, `Matrix`, `Mr.Robot`,
`Light`, `Paper`, `Nord Light`, `Sage Light`, `Rose Light`, `Claude`,
`ChatGPT`, `Qwen`, `DeepSeek`, `Grok`, `Gemini`, `OpenClaw`, `Hermes Agent`,
`Solarized Dark`, `Dracula`, `Midnight Blue`, `High Contrast`, `Commodore 64`,
`Amstrad CPC`, `Amstrad CPC Monochrome`, `Amstrad CPC Amber Monochrome`,
`Amstrad CPC Blue Monochrome`, `ZX Spectrum`, `EGA`, `Nintendo`, `Sega`,
`Ubuntu Linux`, `Steam`, `Blade Runner`, `Hell`, `Heaven`) and two selectors. The header
selector controls the browser-global theme and stores its ref in a cookie. The
conversation selector below the expiry control stores per-conversation theme
refs in a cookie map, with `Use global theme` as the default. When switching
conversation, the UI applies the conversation theme if one is linked; otherwise
it falls back to the global theme. Themes are repository resources stored as
directories under `data/repository/theme` using the normal scope hierarchy:
`global/<name>/`, `users/<user>/<name>/`, or `users/<user>/<conversation>/<name>/`.
Each theme directory contains `theme.json`, one or more CSS files, and optional
image/font assets referenced by the CSS. Shipped themes are global theme
resources; their CSS defines palette variables (`--pf-*`) and the chat
stylesheet consumes those variables. Custom themes can be created from raw CSS
or from a ZIP containing CSS plus image/font assets; ZIP asset URLs are inlined
when the theme CSS is loaded for the browser.

## Private Gateway Skins

The private gateway challenge page is selected by the `skin` field on the
`privateGateway` service referenced by an `httpListener`. Skins are repository
resources stored under `data/repository/private_gateway_skin` using the normal
scope hierarchy. Each skin directory contains `skin.json` metadata and
`template.html`; templates can use `{{ next_url }}`, `{{ error }}`, and
`{{ cooldown }}` placeholders. Shipped global skins are `default`, `google`,
`bing`, `wifi`, `terminal`, `netflix`, `captcha`, `matrix`, and `bladerunner`.
Plugin-provided skins can add directories with the same layout without changing
`services/private_gateway.py`.

## Flow Task Availability

`ToolTaskAdapter` registers most tools as `tool.<name>` tasks. Some tools are intentionally skipped because they are agent-internal, meta-tools, or resource/control actions that do not make sense as flow nodes.

Skipped by default:

```text
get_tool_schema, use_tool, ScheduleWakeup, PushNotification,
complete_task, verify_task, flash_delegate, manage_resource, manage_package, create_tool,
pawflow_help, update_plan, create_plan, link_identity,
browser_action
```

Even skipped tools should still be documented here because agents can call them directly.

# Agent Tool Catalog

PawFlow exposes tools to agents through `ToolHandler` classes. Most tools are also available inside flows as `tool.<name>` tasks through `ToolTaskAdapter`.

This catalog is grouped by purpose. Use
`get_tool_schema(tool_name="<name>")` at runtime for one exact JSON schema,
or `get_tool_schema(family="<family>")` to compare the tools actually
available to the current agent.

## Argument Handling

Every tool call is decoded and checked at one place, `core/tool_json.py`, before
a handler sees it. No module decodes tool arguments on its own — that property
is enforced by a test (`tests/test_tool_json_single_seam.py`), because private
copies drift and the same payload then succeeds on one route and fails on
another.

**Decoding.** `parse_tool_arguments` decides whether the argument blob is JSON
at all. It unwraps double encoding, recovers a payload truncated at EOF, and
repairs an invalid escape or a raw control character inside a string — but only
after strict parsing has already failed, so a valid payload is never rewritten.
When a payload genuinely cannot be read, the call is refused with the parse
error and a window of characters around the failure. It is never silently
replaced with empty arguments.

**Type coercion.** Values are then aligned with the type each property declares.
The rules are deliberately narrow — unambiguous fixes only:

| Sent | Declared | Result |
|---|---|---|
| `"[{...}]"` | `array` / `object` | decoded; refused if it decodes to another type |
| `"true"`, `"1"`, `"false"`, `"0"` | `boolean` | converted |
| `"50"`, `50.0` | `integer` | converted |
| `"1.5"` | `number` | converted (finite only) |
| `null` on an optional property | any | dropped, as if omitted |
| `null` on a required property | any | refused |
| a value already of the declared type | any | untouched |

Anything else is refused with a message naming the property. In particular:

- A **bare** string is never wrapped or split into an array. Deciding what
  `tags="a,b"` means belongs to the handler
  (`core/handlers/_arg_normalize.py`), not to a guess made here.
- A boolean never satisfies `integer` or `number`, so `limit=true` is an error
  rather than `1`.
- An unrecognized boolean string (`"maybe"`) is an error, not `false`.

**Names and aliases.** Claude Code spellings (`file_path` → `path`,
`include` → `glob`) and common aliases (`cmd` → `command`) are mapped to the
PawFlow names. The caller's argument object is never modified in place: the
call recorded in the transcript stays the call that was approved.

**Unknown arguments** are rejected with the list of valid ones rather than
ignored, so the next attempt can be correct.

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
| `edit` | Exact string or line-based file edit. Exact unique replacements no longer require a prior read; whitespace drift is tolerated, and `fuzzy=true` enables one high-confidence fuzzy match. The returned diff is computed from the file before/after the write — it reports what was written, so a successful edit never needs a verification read. |
| `batch_edit` | Apply multiple replacements atomically across files, with aggregate replacement totals. |
| `apply_patch` | Apply a unified diff or `*** Begin Patch` block. `path` is optional when the patch contains file paths. |
| `find_replace` | Regex find/replace. `multiline=true` enables `^`/`$` line-boundary matching. |
| `delete` | Delete a file or directory. |
| `mkdir` | Create a directory. |
| `stat` | Get file metadata. |
| `exists` | Check existence. |
| `list_dir` | List directory contents. |
| `glob` | Find files by glob. |
| `grep` | Search file contents. Relay-backed searches use bounded `ripgrep --json` output when the binary is available and transparently fall back to the Python scanner when it is not or cannot parse the requested regex. |
| `search` | Combined glob + regex + ranked snippets for fewer discovery calls. |
| `copy` | Copy files between filesystem services/FileStore. |
| `notebook_edit` | Edit a Jupyter notebook cell. |

### How `apply_patch` places a hunk

`*** Begin Patch` blocks have their own applier. A unified diff goes to `git
apply` first, which verifies context and names the offending line. Only if git
is missing, or refuses to *parse* the diff, does a built-in fallback take over —
and when git refused, its diagnostic is carried into any error the fallback
raises, so the real reason is never lost.

A unified diff carries two independent kinds of evidence for where a hunk goes,
and the fallback uses whichever the hunk actually has.

**Context**, when the hunk has any. The `@@` number is then a **hint, not an
address**: the hunk is placed by searching for its context, starting at the
hinted line and working outward, so wrong or missing numbers do not matter. A
hunk whose context is nowhere in the file is refused, never applied to whatever
happened to sit at the stated offset.

**The header's own arithmetic**, when the hunk has no context — a pure
insertion, as `diff -U0` emits. A `@@ -a,b +c,d @@` header is redundant three
times over: `b` restates the old side's line total, `d` the new side's, and `c`
restates `a` shifted by every hunk already applied. If all three agree, the
stated position is corroborated by the diff's own numbers; if any disagrees,
the header was not produced by a diff tool and the hunk is refused rather than
placed on trust. A bare `@@` on a contextless hunk carries no arithmetic at all
and is likewise refused.

So every hunk is checked — by context where context exists, by arithmetic where
it does not. Nothing is written until all of them have been placed, so a bad
hunk at the end cannot leave the earlier files half-rewritten.

This matters because hand-counted `@@` headers are the common failure: git
rejects a header whose line counts are wrong, even when the context and the
edits are perfectly correct. Such a patch now applies correctly through the
fallback. It previously did not — the fallback indexed the old-side `@@` number
into a buffer the preceding hunks had already grown, so every hunk after the
first landed off by the net line delta before it, silently. A bare `@@` with no
numbers at all is likewise located by context rather than rejected.

On the git path, zero-context diffs need `--unidiff-zero`, which `apply_patch`
now always passes. Without it git demands one line of context, skips a
context-free hunk **and still exits 0** — a silent no-op this tool used to
report as a successful patch over an untouched file. The flag only relaxes
hunks that carry no context; a patch that has context applies identically
either way.

## Execution, DevOps, and Desktop

| Tool | Purpose |
|---|---|
| `bash` | Run a shell command through the relay. Accepts `command` or `cmd`. |
| `Monitor` | Run a command and return early on exit or regex match. |
| `execute_script` | Execute a script/tool-backed snippet. |
| `run_tests` | Run tests through the project environment; accepts `maxfail` (default 1, fail fast — pass `0` to run the whole selection and report every failure in one call) and `max_output` to cap returned output. |
| `security_scan` | Run security checks. |
| `screen` | Screenshot/click/type/key/scroll/mouse-position against local or Docker desktop. |
| `browser` | Browser automation action through the browser service. |
| `see` | Analyze an image, video, or audio artifact. |
| `project_graph` | Query or manually rebuild the automatically maintained relay-scoped AST graph. |
| `project_wiki` | Query, inspect, lint, refresh, or repair the automatically maintained relay-scoped project wiki. |

When `PAWFLOW_USE_RTK` is set to a truthy value (`1`, `true`, `yes`, `on`) and
the selected relay target has the `rtk` binary, PawFlow uses RTK for compatible
relay-backed calls: `bash` and `run_tests` run `rtk rewrite <command>` before
execution, while `read` uses `rtk read`. `grep` and `glob` stay native because
RTK output does not preserve PawFlow's grep/glob response semantics reliably.
The native relay `grep` path independently uses `ripgrep --json` when
available, while preserving PawFlow's structured paths, line numbers, context,
exclusions, and global result limit. It falls back to the Python scanner if
`rg` is missing or rejects the expression.
If the variable is not truthy, RTK is missing, or RTK cannot handle a request,
PawFlow falls back to the native tool behavior unchanged. A generated multi-line
script opts out entirely (`_skip_rtk`): the rewrite keeps only the last line of
RTK's output as the command, which would silently truncate the script to its
final statement. `Monitor` uses that opt-out.

### How `Monitor` waits

`Monitor` runs the command in its own session, captures stdout and stderr to a
file, and polls that file until the pattern has matched `limit` times, the
command exits, or `timeout_ms` elapses. When it stops early it kills the
command's whole process group, so the command and its children (the `sleep` in
a retry loop, for instance) stop with it.

It does not pipe the command through `grep | head`, and the reason is worth
recording. A downstream stage exiting never stops a producer — the shell waits
for every member of a pipeline — and `grep` only discovers `head` is gone when
it next writes. For a pattern that matches once, that write never comes: the
command ran to completion and `Monitor` returned at its timeout. Measured on a
60-second command, a pattern matching once took the full timeout while the same
pipeline with a pattern matching every line returned in two seconds. The tool
failed exactly in the case it advertises (`FAILED`, `listening on port`).

The result header states what actually happened rather than guessing from the
output text: `reason=match|exit|timeout|unknown`, plus `exit_code=` when the
command exited on its own. A timeout still returns the lines captured so far,
and when a pattern never matched, the tail of the raw output is returned so the
run is not a blank.

### What `line_limit` drops, and why it says so

`line_limit` (default 200) caps the raw output kept. Only the two raw-output
branches can drop lines: no pattern at all, which keeps the *first*
`line_limit` lines, and the never-matched fallback, which keeps the *last*. A
hit list is capped by `limit` instead, and the header already states `limit=`,
so that cap was never silent.

The cap used to be. A body that stops at line 200 is indistinguishable from a
command that had nothing more to say, so a caller reading a truncated result
would conclude the output simply ended there. The shell now reports the true
line count alongside the reason, and a truncated result says so twice: the
header carries `truncated=N`, and the body ends with how many lines were
dropped, how many were produced, and which end was kept:

```
[monitor] reason=exit elapsed_ms=225 lines=200 exit_code=0 truncated=300
...
[monitor] 300 more line(s) not shown: 500 produced, kept the first 200
(line_limit=200). Raise line_limit to see them.
```

Note that `line_limit` never costs you a match: the hit search greps the whole
capture file, so a `FAILED` on line 400 is found under any `line_limit`.

## Web and Search

| Tool | Purpose |
|---|---|
| `web_search` | Search the web across configurable providers, aggregate results, and deduplicate URLs. |
| `fetch` | Fetch/extract a web page. |
| `share_file` | Share a generated file with the user. |
| `show_file` | Open a file in the user's chat viewer. |

`web_search` accepts `query` (or `q`), `max_results` (or `maxResults`), and
`provider` / `search_provider` as a single no-key provider or a comma-separated
chain. `service` selects a `webSearchConnection`, `search_cli_providers`
optionally restricts its paid providers, and `mode` selects the search-cli mode.
When a scoped `webSearchConnection` exists, the tool first uses the bundled
`search-cli` binary in the PawFlow server image with that service's encrypted
provider keys. The binary and keys are never sent to a relay. It otherwise
uses the built-in no-key providers `bing`, `duckduckgo`, and `google`; the
default chain is `bing,duckduckgo,google`. The same default can be set with the PawFlow variable
`web_search_providers` (conversation → user → global, with OS env fallback only
after PawFlow variables). The no-key providers run concurrently under one global
deadline. Bing tries RSS first and Google uses static HTML. Slow browser
fallback is disabled unless `browser_fallback=true`; when enabled it uses
`PAWFLOW_CHROMIUM_EXECUTABLE` when set, then common system Chromium binaries.
Search credentials, cache, and logs are isolated per invocation; search-cli
logging and its local cache are disabled. When a relay is connected, only the
no-key fallback may run there so its network surface matches the user's
environment; without a relay the fallback runs on the PawFlow server. Both
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
| `learn` | Extract learnings from conversation. |
| `conversation_search` | Full-text search across the user's past conversations (raw messages, not extracted memories). Read-only, approval-exempt, allowed in read-only mode. Encrypted conversations are never indexed and never appear in results. See [Searching past conversations](#searching-past-conversations). |
| `diary_write` | Write an agent diary entry. |
| `diary_read` | Read diary entries. |
| `todolist` | Manage authoritative unfinished work scoped to the current conversation agent. |
| `scratchpad` | Manage expiring pull-only working notes scoped to the current conversation agent. |
| `kg_add` | Add knowledge graph triples. |
| `kg_query` | Query graph facts. |
| `kg_invalidate` | Expire graph facts. |
| `kg_timeline` | View graph timeline. |
| `kg_stats` | Graph statistics. |
| `query_graph` | Traverse graph connections. |
| `kg_god_nodes` | Find highly connected entities. |

### Searching past conversations

`conversation_search` answers "we solved this before, in which conversation?".
`recall` cannot: it searches memories, which exist only where an agent decided
at the time that something was worth keeping. This searches the transcripts
themselves.

The index (`core/conversation_index.py`) is one SQLite FTS5 database per user
under `data/runtime/conversation_index/`, and it is derived data — deleting a
file costs the next search one rebuild and nothing else.

Four properties worth knowing before relying on it:

- **Encrypted conversations are never indexed.** An FTS index is plaintext by
  construction, so indexing one would copy its content back out of the
  encrypted store. A conversation encrypted after being indexed is purged on
  the next refresh, and an unreadable encryption state counts as encrypted —
  the check fails closed. Results say how many conversations were skipped.
- **Unreadable source data purges derived plaintext.** If the conversation list
  cannot be read, the user's derived index is cleared; if one transcript cannot
  be read, that conversation is purged. Refresh never serves old text merely
  because it could not prove whether the source was deleted or redacted. Every
  transcript rewrite increments `transcript_generation` inside the same
  conversation lock, forcing purge-and-reindex even when ids and timestamps stay
  stable.
- **The index refreshes when you search, not when a message is appended.** The
  refresh is incremental twice over — a conversation whose `updated_at` has not
  moved since it was indexed is never opened, and one that has moved is read
  only past its row watermark — so the difference is only *when* the cost
  lands; putting it on append would make the chat UI wait for a feature that
  turn may never use.
- **Only `user` and `assistant` rows are indexed**, and only conversations the
  searching user owns. Tool output is machine text that would dominate every
  ranking, and a shared conversation is searchable by its owner, not yet by
  its collaborators.

### Reading the current conversation

`read_history` reads the conversation it is called in, a bounded window at a
time — it never loads a transcript whole, which is what a conversation of a
few hundred thousand messages makes fatal.

Ownership is therefore checked once, up front, in `_owns_conversation`
(`core/handlers/history.py`): `recent` pages through `load_page`, which is
given the user id and scopes itself, but every windowed reader — `search`,
`oldest`, `range`, `around` — walks `iter_display_windows` with the
conversation id alone. That check is the only thing between another user's
transcript and whoever knows its id, so it **fails closed**: a metadata read
that raises denies the call and logs a warning. It used to grant access, which
turned any corrupted or unreadable `extras.json` into a disclosure to any
authenticated user who knew the id. A conversation with no recorded owner is
still readable — that is a conversation nobody claimed, not one belonging to
someone else.

## Multi-Agent, Plans, and Tasks

| Tool | Purpose |
|---|---|
| `delegate` | Spawn/delegate work to another agent. |
| `flash_delegate` | Create temporary task-specific agents for independent parallel work; they use the caller's LLM service and disappear after completion. Background results are delivered to the caller (preempt/wake) — and when the caller is on a live realtime voice session, the result is ALSO injected into the session and spoken (out-of-band `context` message). |
| `flash_status` | Report the caller's flash agents: live ones (name, task_id, age, queued follow-ups) from the live-delegate registry, and recently finished ones (status, error, duration) from a bounded ring buffer. Lets the calling agent verify delegated work is actually running instead of inferring liveness from silence; results are still delivered asynchronously. |
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
| `PushNotification` | Send a runtime-only notification event. Web clients accumulate it in their tab-local notification center; it is not persisted in the transcript or agent context. |
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
| `get_tool_schema` | Inspect one tool schema, compare an availability-filtered family, or list available tools/families. |
| `use_tool` | Execute a tool by name. |
| `pawflow_help` | Get platform help. |

The web chat left sidebar is a two-block vertical accordion. `Conversations` is
active by default and owns the available height while the `Resources` header
stays at the bottom. Selecting `Resources` slides the conversation list closed,
keeps its header at the top, and gives the complete remaining height to the
resource tree. Exactly one block is active. Mouse, Enter, Space, `/flows`, and
semantic UI helpers all use the same controller.

Inside the active Resource Panel, the expanded/collapsed tree state persists in
the browser. On a first visit only `Agents` is open; after toggling sections,
reloads restore the exact opened and closed sections. This inner tree state is
independent from the outer Conversations/Resources accordion.

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
conversation selector below the expiry control stores its ref in the
conversation metadata on the server, with `Use global theme` as the default.
The selection therefore follows the conversation across desktop and mobile
browsers. When switching conversation, the UI applies the conversation theme if
one is linked; otherwise it falls back to the browser-global theme. Themes are repository resources stored as
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
complete_task, verify_task, flash_delegate, flash_status, manage_resource, manage_package, create_tool,
pawflow_help, update_plan, create_plan, link_identity,
browser_action
```

Even skipped tools should still be documented here because agents can call them directly.

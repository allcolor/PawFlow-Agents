# PawFlow Webchat Slash Commands Reference

All commands are typed in the chat input, prefixed with `/`. Domain commands use the same server-side parser and result renderer in webchat, PawCode, Telegram, and VS Code. Clients keep only transport-specific UI operations local, such as opening a terminal or selecting a file. Machine-readable action results remain intact and also include a human-readable `display` field for consistent output across clients. A double slash is reserved as skill-run sugar: `//<skill> [@agent] [args...]` is equivalent to `/skill run [@agent] <skill> [args...]`.

## Quick Reference

| Command | Description |
|---------|-------------|
| `/help` | List commands or get detailed help |
| `/agent` | Manage AI agents |
| `/msg` | Send message to a specific agent or task |
| `/btw` | Side-channel question (no interruption) |
| `/call` | Call a tool directly |
| `/audio` | Generate audio or music |
| `/relay-audio` | Stream audio from a relay in webchat |
| `/tool-metrics` (`/toolmetrics`) | Show tool execution metrics |
| `/task` | Create, assign, and manage agent tasks |
| `/goal` | Create and assign a conversation-scoped goal task |
| `/skill` | Manage skills |
| `/pfp` | Build, inspect, install, export, and uninstall PawFlow packages |
| `/memory` | Manage agent memories |
| `/relay` | Manage relay bindings |
| `/encrypt` | Encrypt this conversation at rest (opt-in) |
| `/flow` | Manage data flows |
| `/run` | Execute shell command via relay |
| `/cost` | Show token usage and cost |
| `/clear` | Clear the chat display |
| `/new` | Start a new conversation |

---

## Help and Information

### /help

```
/help [command]
```

Without arguments, lists all available commands. With a command name, shows detailed documentation.

```
/help
/help agent
/help call fetch
```

When used as `/help call`, lists all available tools. `/help call <toolname>` shows that tool's parameter schema.

### /cost

```
/cost [@agent|ALL]
```

Shows token usage and estimated cost per agent and per model, for both the current conversation and user-level totals.

```
/cost @ALL        -- all agents
/cost @grok       -- specific agent
```

### /usage

```
/usage
```

Deprecated. Use `/cost` instead.

### /debug

```
/debug [description]
```

Runs the `debug` skill to diagnose session issues -- analyzes context state, recent errors, agent loops, and service health. Optionally describe the problem.

---

## Agent Management

### /agent

```
/agent list | create | create-conv | select | delete | msg | btw | resume | setname | disable | enable | promote
```

**Subcommands:**

| Subcommand | Syntax | Description |
|------------|--------|-------------|
| `list` | `/agent list` | List all agents (user + global) with scope icons |
| `create` | `/agent create` | Open interactive agent creator dialog |
| `create-conv` | `/agent create-conv @name "prompt"` | Create a conversation-scoped agent |
| `select` | `/agent select @name` | Activate an agent (messages go to it) |
| `delete` | `/agent delete @name` | Delete an agent |
| `msg` | `/agent msg @name text` | Send message to a specific agent |
| `msg @ALL` | `/agent msg @ALL text` | Broadcast to all agents in parallel |
| `btw` | `/agent btw @name question` | Side-channel question (no interruption) |
| `resume` | `/agent resume @name` | Tell agent to continue |
| `setname` | `/agent setname @real [nickname]` | Set or reset display name |
| `disable` | `/agent disable @name` | Disable an agent |
| `enable` | `/agent enable @name` | Re-enable a disabled agent |
| `promote` | `/agent promote @name scope` | Promote agent to a broader scope |

```
/agent list
/agent create
/agent select @grok
/agent select assistant
/agent msg @grok Explain this code
/agent msg @ALL What do you think?
/agent btw @claude What is the time complexity?
/agent setname @claude_sonnet_4 Claude
```

### /msg

```
/msg <agent|ALL|task_id> <message>
```

Send a message to a specific agent or running task without changing the active agent. Agent targets accept either `reviewer` or `@reviewer`.

```
/msg @grok Explain this code
/msg grok Explain this code
/msg @ALL What do you think?
/msg @t_8953b308 Check the latest post
/msg @"Agent With Spaces" Hello
```

### /btw

```
/btw <agent|ALL> <question>
```

Side-channel question. Asks a quick question to an agent without interrupting its current work.

```
/btw @claude What is the time complexity?
/btw claude What is the time complexity?
/btw @ALL Any thoughts on this?
```

### /setname

```
/setname <agent> [nickname]
```

Set a display nickname for an agent. The optional `@` target prefix is accepted. Omit the nickname to reset to the real name.

```
/setname @claude_sonnet_4 Claude
```

---

## Interruption and Control

### /interrupt

```
/interrupt [@agent|@agent::taskid|ALL]
```

Gracefully interrupt an agent -- asks it to wrap up and give its best answer now.

```
/interrupt                -- interrupt active agent
/interrupt @grok          -- interrupt only grok
/interrupt @grok::t_abc   -- interrupt a specific task
/interrupt @ALL           -- interrupt all agents
```

Alias: `/int`

### /stop

```
/stop [@agent|@agent::taskid|ALL]
```

Force stop an agent -- immediate cancel, no response generated.

```
/stop
/stop @grok
/stop @grok::t_abc
/stop @ALL
```

### /resume

```
/resume @<agent|ALL>
```

Tell an agent to continue from where it stopped.

```
/resume @grok
/resume @ALL
```

### Escape Key Behavior

Pressing **Escape** in the chat input provides two levels of interruption:

1. **First press** -- graceful interrupt. Asks the targeted agent to respond immediately with its best answer so far.
2. **Second press within 5 seconds** -- force stop. Immediately kills the agent with no response.

After a force stop, the next Escape press starts the cycle over (graceful again).

---

## Context Management

### /compact

```
/compact [@agent|ALL]
```

Summarize older messages to reduce context size while preserving key information. A compact is a hard context replacement: PawFlow stops any active loop for the target agent, writes the compacted agent context to disk, invalidates CLI runtime sessions, and restarts the next loop from that saved compacted context. `/compact` uses the same compaction procedure as automatic provider-triggered compaction: shared bucket header plus a bounded raw tail, then the canonical `_compact` writer.

```
/compact               -- compact current agent's context
/compact shared        -- compact the shared context
/compact @grok         -- compact grok's context only
/compact @ALL          -- compact all agents' contexts
```

### /rebuild

```
/rebuild
```

Rebuild the derived context state from the canonical transcript. The command rebuilds `shared.jsonl`, wipes and rebuilds the shared background bucket pyramid, deletes stale private agent contexts, then runs the equivalent of `/compact @agent` for every configured conversation agent.

`/rebuild` no longer accepts an agent argument; it is always a whole-conversation repair/rebuild operation.

### /restart_from

```
/restart_from <index|msg_id>
```

Truncate the current conversation transcript at an absolute message index or at a message id. Matching shared and private agent contexts are truncated or cleared, and CLI sessions are invalidated. Existing background summary buckets are kept; the next compacted summary context includes the restart msg_id so agents treat any older bucket details after that point as pre-restart history. Use 0 to empty the transcript and contexts.

```
/restart_from 0            -- empty transcript, shared context, and agent contexts
/restart_from 10           -- keep the first 10 transcript messages
/restart_from abc123def456 -- keep messages through msg_id abc123def456
```

Alias: `/restart`

### /summary

```
/summary [@agent|ALL] [tokens]
```

Ask the LLM to summarize the context to approximately N tokens (default 500), then restart from that summary.

```
/summary                -- summarize to ~500 tokens
/summary 1000           -- summarize to ~1000 tokens
/summary @grok          -- summarize grok's context
/summary @ALL           -- summarize all agents
/summary @qwen 2000     -- summarize qwen's context to ~2000 tokens
```

### /context

```
/context [@agent]
```

View what the LLM actually sees: messages, token estimate, divergence status. The overlay includes an agent dropdown to switch between contexts.

```
/context
/context @grok
```

---

## Conversation Management

### /new

```
/new
```

Start a fresh conversation, disconnecting from the current one.
In Telegram, `/new` opens the same guided creation flow as `/conv new`.

### /conv

```
/conv
/conv list
```

Show the conversation list. Select from the sidebar in webchat or VS Code,
use `/conv <id>` in PawCode, or `/conv select <n|id>` in Telegram.

### /history

```
/history [N] [offset]
```

Display messages from the current conversation.

```
/history             -- show last 50 messages
/history 100         -- show last 100
/history 50 100      -- show 50 messages starting from offset 100
```

### /search

```
/search <query>
```

Search for text in all messages of the current conversation.

### /export

```
/export [json|md]
```

Export the current conversation as JSON or Markdown. Downloads a file.

### /rename

```
/rename <title>
```

Set a title for the current conversation.

### /delete

```
/delete <conversation_id>
```

Permanently delete a conversation by ID.

### /delete-msg

```
/delete-msg <index>
```

Remove a specific message from the conversation by its index.

### /clear

```
/clear
```

Clear the visible chat display only. History is preserved server-side and the view leaves a load-more button so the current conversation can be reloaded on demand. It does not create a new conversation.

### /clear-store

```
/clear-store [@agent|ALL]
```

Delete FileStore files for the current conversation.

```
/clear-store           -- delete all files
/clear-store @grok     -- delete grok's tool results
/clear-store @ALL      -- delete all agents' tool results
```

### /clear-files

```
/clear-files
```

Remove all queued file attachments (pending uploads). Alias: `/detach`

---

## Task Management

### /task

```
/task create | assign | list | edit | delete | pause | resume | cancel
```

**Library (reusable definitions):**

```
/task create <name> "<prompt>" [--criteria "..."] [--interval XX] [--interactive]
/task delete <name>
/task list
```

**Assignment (from library or inline):**

```
/task assign @<agent> <taskname>
/task assign @<agent> <taskname> --var nbr_images=20 --var style=cyberpunk
/task assign @<agent> <taskname> --interval XX
/task assign @<agent> "<inline task>" [--criteria "..."] [--interval XX] [--verifier @<agent>] [--interactive]
```

**Limits (on assign or edit):**

| Flag | Description |
|------|-------------|
| `--budget $5` | Cancel if cost exceeds |
| `--turn-time 5m` | Interrupt if a single turn takes too long |
| `--total-time 1h` | Cancel if total elapsed time exceeds |
| `--max-reschedules 20` | Cancel after N reschedules |
| `--max N` | Maximum iterations |
| `--verifier @agent` | Agent that verifies completion |
| `--var key=val` | Variable substitution in task prompt |
| `--auto-allow` | Auto-approve plan steps |
| `--interactive` | Mark scheduled wake-ups as system-prefixed context instead of bare user `continue` |

**Control:**

```
/task edit <task_id> [--budget $X] [--turn-time Xm] [--total-time Xh] [--max-reschedules N]
/task pause <task_id|@agent>
/task resume <task_id|@agent>
/task cancel <task_id|@agent>
```

Task IDs look like `t_xxxxxxxx`. Tasks survive server restarts and reschedule automatically.

```
/task assign @grok "Scrape the top 100 HN posts" --verifier @claude --interval 120 --criteria "all 100 posts summarized"
```

### /goal

```
/goal [@agent] "<objective>" [task options]
```

Creates a conversation-scoped task definition with a generated name and assigns it immediately. If `@agent` is omitted, PawFlow uses the selected agent for the current conversation. `/goal` is an alias over tasks: the objective is stored as the task prompt, and unless `--criteria` is provided, the same objective is also used as the task criteria.

Supported task options mirror `/task assign`: `--criteria`, `--interval`, `--verifier`, `--budget`, `--turn-time`, `--total-time`, `--max-reschedules`, `--max`, `--context`, `--var`, `--auto-allow`, and `--interactive`.

```
/goal @grok "Migrate X until tests pass and final audit is done" --interval 120 --verifier @claude
/goal "Keep checking the deployment and report when it is healthy" --interval 60
```

---

## Skills

### /skill

```
/skill list | search [--source codex|claude|hermes|openclaw|all] <query> | import [--source src] [--review-only] [--force] [--scope user|conversation] [--name name] <ref> | add [--force] @name <prompt> | update [--force] @name <prompt> | del @name | assign @agent @skill | unassign @agent @skill | assigned @agent | run [@agent] <skill> [args...] | //<skill> [@agent] [args...]
```

| Subcommand | Description |
|------------|-------------|
| `list` | List all skills and their agent assignments |
| `search [--source src] <query>` | Search supported external skill marketplaces |
| `import [--source src] [--review-only] [--force] [--scope user\|conversation] [--name name] <ref>` | Review and import an external Agent Skill |
| `add [--force] @name <prompt>` | Create a skill |
| `update [--force] @name <prompt>` | Update a skill |
| `del @name` | Delete a skill |
| `assign @agent @skill` | Assign a skill to an agent |
| `unassign @agent @skill` | Remove a skill from an agent |
| `assigned @agent` | List skills assigned to an agent |
| `run [@agent] <skill> [args...]` | Invoke a visible skill immediately in the current conversation |
| `//<skill> [@agent] [args...]` | Shortcut for `/skill run [@agent] <skill> [args...]` |

Skills are assigned only through an agent's `assigned_skills`. The old generic resource activation path is not used for skills; use `assign` and `unassign` instead. Assigning a skill advertises it to the agent with a lightweight context message; agents can perform the same operation with `manage_resource(action="assign_skill"|"unassign_skill", resource_type="skill", agent_name="...", skill_name="...")`. The full prompt is loaded only when the agent calls `load_skill(name="skill-name")`. Updating a skill notifies currently assigned conversation agents to reload it when needed; deleting a skill removes it from visible agents' `assigned_skills` lists and queues the normal removal context message. `/skill run [@agent] <skill> [args...]` is a one-shot invocation: it renders a visible skill immediately and queues it as a user message for the target agent, defaulting to the selected conversation agent when `@agent` is omitted. The `//<skill> [@agent] [args...]` shortcut invokes the same path; in this shortcut, `@agent` is recognized only when it appears immediately after the skill name, so `@` inside later arguments is preserved. Imported or untrusted skill content can be checked with `manage_resource(action="review", resource_type="skill", data={"prompt": "..."})` before creating or assigning it.

Marketplace import supports Codex (`openai/skills`), Claude/Anthropic plugin marketplaces, HermesHub, and OpenClaw GitHub tree URLs. Imports fetch the complete bounded skill directory, including binary assets, with a UTF-8 root `SKILL.md`; oversized packages and unsafe paths are rejected. Package scripts and `allowed-tools` declarations are treated as untrusted content: they are reviewed and stored as package data, but never executed automatically and never grant tool approval. Skills that require human review or receive blocked review findings are not created unless `--force` is provided after review.

### /pfp

```
/pfp key-create | build <pfpdir> --key-env VAR [--out file.pfp] | inspect <file.pfp|pfpdir|url|package@version> [--confirm-download] | install <file.pfp|url|package@version> [--confirm-download] [--scope user|conversation] [--include ids] [--exclude ids] [--force] [--replace] | dev-load <pfpdir> [--scope user|conversation] [--include ids] [--exclude ids] [--secret logical=stored_key] [--replace] | dev-unload <package> [--scope user|conversation] | update <file.pfp|url|package@version> [--confirm-download] [--include ids] [--exclude ids] [--force] | search <query> | registry add|list|remove | list | reload-tasks [--scope user|conversation] | uninstall <package> | export --package id --version v --include type:name[,type:name] --out dir
```

PawFlow Package files are signed `.pfp` zip artifacts. `inspect` verifies the signature and returns an object-by-object install plan with risk, status, dependencies, and selectable IDs. `install` only writes selected objects and records provenance under the local package install registry. Agents that reference `assigned_skills` require those skills to be already visible or selected in the same install. Runtime `tool`, `service_provider`, `flow_task`, and `task_provider` objects execute only through the relay Python runner with declared grants; config-only `service_definition` objects install through `ServiceRegistry`.

Use `/pfp dev-load <pfpdir>` while developing package runtime objects. It loads an unsigned source directory directly, defaults to conversation scope, and lets edited source files take effect on the next runtime call. Use `/pfp dev-unload <package>` to remove the dev package without deleting the source directory. See [PFP Developer Guide](PFP_DEVELOPER_GUIDE.md) for the full local development workflow and file-backed media provider pattern.

For signing, prefer `--key-env VAR` so private key material is read from an environment variable and is not pasted into chat history.

Decentralized registries are static JSON indexes. Add one with `/pfp registry add https://example.com/pawflow/index.json --name example`, search with `/pfp search media provider`, then inspect/install/update a result by `package@version`. The first remote inspect/install/update shows the package size and returns a confirmation request; repeat the same command with `--confirm-download` to fetch the artifact. Registry-provided SHA-256 values pin downloads, but every install still verifies the `.pfp` signature and file lock. `/pfp update` updates previously installed objects and skips local modifications unless `--force` is provided. `/pfp reload-tasks` rebuilds installed package task proxies after a process restart or explicit runtime reset.

### /add-skill

```
/add-skill [--force] <name> <prompt>
```

Shortcut for `/skill add`.

---

## Memory Management

### /memory

```
/memory [list [@agent] | add | edit | del | search | panel]
```

| Subcommand | Syntax | Description |
|------------|--------|-------------|
| (none) or `panel` | `/memory` | Open the visual memory editor panel |
| `list` | `/memory list` | List all memories |
| `list @agent` | `/memory list @grok` | List memories for a specific agent |
| `add` | `/memory add <text> [#tag1 #tag2] [@agent]` | Add a memory manually |
| `edit` | `/memory edit <id> <new text>` | Edit a memory |
| `del` | `/memory del <id>` | Delete a memory by ID |
| `search` | `/memory search <query>` | Search memories by text or tags |

```
/memory list
/memory list @grok
/memory add Important fact #note #priority @grok
/memory edit abc123 Updated fact text
/memory del abc123
/memory search "API key"
```

---

## Relay Management

### /relay

```
/relay [status|list|link|unlink|default|local] [relay_id]
```

| Subcommand | Syntax | Description |
|------------|--------|-------------|
| (none) or `status` | `/relay` | Show linked relays for this conversation |
| `list` | `/relay list` | List all available relays |
| `link` | `/relay link <id>` | Link a relay to this conversation |
| `unlink` | `/relay unlink <id>` | Unlink a relay |
| `default` | `/relay default <id>` | Set default relay for this conversation |
| `local` | `/relay local <id> true\|false [@agent]` | Set execution mode (true=host, false=docker) |
| `encrypt` | `/relay encrypt <id> on\|off` | Encrypt this conv-scoped relay workspace (CryFS) |
| `unlock` | `/relay unlock <id>` | Provide the passphrase to mount an encrypted workspace |

```
/relay
/relay list
/relay link my_relay
/relay unlink my_relay
/relay default my_relay
/relay local my_relay true
/relay local my_relay false @grok
```

---

### /encrypt

Opt-in, per-conversation encryption at rest. When enabled, all conversation
content (messages, thinking, tool arguments and results) is stored as
ciphertext on disk; metadata (ids, timestamps, ordering) stays clear so the
store keeps working. The key lives in server RAM only while unlocked: a server
restart, logout, or 15-minute idle re-locks the conversation. **No recovery** —
lose the passphrase (with no recovery/relay wrap) and the data is unrecoverable.

```
/encrypt [status|on|off|unlock|lock|passwd|escrow on/off|recover|relay <pubkey>/off]
```

| Subcommand | Description |
|------------|-------------|
| (none) or `status` | Show the encryption state of this conversation |
| `on` | Enable encryption (prompts to set a passphrase) |
| `off` | Disable and decrypt back to clear text (requires unlock) |
| `unlock` | Provide the passphrase to read/write this conversation |
| `lock` | Drop the key from RAM now |
| `passwd` | Change the passphrase (re-wraps the key) |
| `escrow on\|off` | Add/remove an optional recovery passphrase |
| `recover` | Unlock using the recovery passphrase |
| `relay <pubkey>` | Bind a trusted key-relay (paste `pawflow-relay key export-pubkey`) for unattended unlock |
| `relay off` | Unbind the trusted key-relay |

Encryption is strictly opt-in and only affects conversations where it is turned
on; everything else is unchanged. See
[Security Model](security_model.md#encryption-at-rest) and the
[design RFC](design/encryption-at-rest.md).

---

## Remote Execution

### /run

```
/run <command>
```

Execute a shell command on the filesystem relay. Requires an active relay connection. Timeout: 30 seconds.

```
/run ls -la
/run git status
/run python script.py
```

### /diff

```
/diff [file|ref]
```

Show git diff via the filesystem relay. Displays color-coded output.

```
/diff
/diff HEAD~1
/diff src/main.py
```

### /terminal

```
/terminal [relay_name] | /terminal close
```

Open an xterm.js terminal tab connected to a PTY on the relay. Multiple terminals can be open simultaneously.

```
/terminal              -- open on first relay
/terminal my_relay     -- open on specific relay
/terminal close        -- close active terminal tab
```

Alias: `/term`

### /code

```
/code [relay_name] | /code close
```

Open VS Code (code-server) in a tab on a relay.

```
/code
/code my_relay
/code close
```

### /desktop

```
/desktop [relay_name] | /desktop local [relay] | /desktop docker [relay] | /desktop close
```

Open a noVNC virtual desktop on a relay.

```
/desktop               -- open on first relay
/desktop my_relay      -- open on specific relay
/desktop local         -- open user's local screen via VNC
/desktop docker        -- open Docker virtual desktop
/desktop close         -- close desktop tab
```

### /relay-audio

```
/relay-audio [relay_name] | /relay-audio stop
```

Stream audio from a relay in webchat without opening the full desktop.

```
/relay-audio
/relay-audio my_relay
/relay-audio stop
```

### /port-forward

```
/port-forward <add|remove|list|open> [relay_id] [port]
```

Forward a relay's local port through PawFlow.

The generated URL is served by the PawFlow HTTP listener on `/fwd/...`; in the
chat UI it is displayed as an absolute URL on the current PawFlow origin.

```
/port-forward add my_relay 8080
/port-forward remove my_relay 8080
/port-forward list
/port-forward open my_relay 8080
```

Alias: `/fwd`

---

## Tool Execution

### /audio

```
/audio [@service] <prompt> [--duration N] [--style S] [--instrumental] [--lyrics TEXT]
```

Generate audio or music with the configured audio service. This is a server-side
media command shared by webchat, PawCode, Telegram, and VS Code.

### /call

```
/call tool_name(key=value, ...)
/call tool_name {"key": "value"}
```

Execute any agent tool directly from the chat.

```
/call web_search(query="quantum computing")
/call fetch_http(url="https://example.com")
/call remember(text="important fact", tags=["note"])
/call web_search {"query": "quantum computing"}
```

Use `/help call` to list all tools, or `/help call <toolname>` to see parameter details.

---

## Flow Management

### /flow

```
/flow list | templates | deploy | start | stop | params | undeploy | promote
```

| Subcommand | Syntax | Description |
|------------|--------|-------------|
| `list` | `/flow list` | List deployed flows |
| `templates` | `/flow templates` | List available flow templates |
| `deploy` | `/flow deploy <id> [scope]` | Deploy a flow (scope: `user`, `conversation`, or `global`; global requires admin) |
| `start` | `/flow start <id> [key=val ...]` | Start a flow with optional parameter overrides |
| `stop` | `/flow stop <id>` | Stop a running flow |
| `params` | `/flow params <id>` | View flow parameters |
| `undeploy` | `/flow undeploy <id>` | Remove a deployed flow |
| `promote` | `/flow promote <id> [scope]` | Move a flow to `user`, `conversation`, or `global` scope; global requires admin |

---

## Resource Management

### /resources

```
/resources
```

List all defined resources (agents, skills, MCP servers) grouped by type, with activation status where applicable.

### /activate

```
/activate <agent|mcp> <name>
```

Activate an agent or MCP resource for the current conversation. Skills are injected only through `/skill assign @agent @skill`.

```
/activate agent researcher
/activate mcp my_server
```

### /deactivate

```
/deactivate <agent|mcp> <name>
```

Deactivate an agent or MCP resource from the current conversation. Use `/skill unassign @agent @skill` for skills.

### /share

```
/share <agent|skill|mcp> <name> <conversation_id>
```

Share (activate) a resource in another conversation.

---

## Service Management

### /service

```
/service list | install <type> <name> [config] | uninstall <name> | enable <name> | disable <name>
```

Manage LLM and external services.

```
/service list
/service install llmConnection my_llm provider=openai,api_key=${key}
/service uninstall my_llm
/service enable my_llm
/service disable my_llm
```

### /llm

```
/llm @<agent> <service|restore>
/llm rotate @<service>
```

Override the LLM service for an agent (per-conversation, persists across restarts).

```
/llm @assistant grok_llm_service
/llm @grok qwen_llm_service
/llm @grok restore                  -- restore default
/llm rotate @claude_code_service    -- force rotate API key
```

### /model

```
/model [@agent] <name>
```

Change the LLM model for the current or specified agent.

```
/model gpt-4o
/model @grok gpt-4o
/model reset
```

### /imgservice

```
/imgservice [list | select <name> @<agent|ALL> | clear [@agent]]
```

Choose which image generation service to use.

```
/imgservice list
/imgservice select dall-e @ALL
/imgservice select flux @grok
/imgservice clear
/imgservice clear @grok
```

### /vidservice

```
/vidservice [list | select <name> @<agent|ALL> | clear [@agent]]
```

Choose which video generation service to use. Same syntax as `/imgservice`.

---

## Secrets and Variables

### /add-secret

```
/add-secret <name> <value>
```

Store an encrypted secret. Available as `${name}` in expressions.

### /secrets

```
/secrets
```

List stored secret names (values are never shown). Alias: `/list-secrets`

### /add-variable

```
/add-variable <name> <value>
```

Store a plaintext variable. Available as `${name}` in expressions. Alias: `/add-var`

### /variables

```
/variables
```

List stored variables with their values. Aliases: `/vars`, `/list-variables`

---

## Autonomous Conversation

### /autoconv

```
/autoconv <on|off|status|now> @<agent|ALL> [freq]
```

Enable agents to contribute to the conversation autonomously on a schedule.

```
/autoconv on @ALL                -- all agents, default 6/1m
/autoconv on @grok 2-3/h        -- grok, 2-3 times per hour
/autoconv on @ALL 1/2h          -- all agents, once per 2h
/autoconv off @ALL               -- disable for all
/autoconv off @grok              -- disable for grok
/autoconv status @ALL            -- show config for all agents
/autoconv now @ALL               -- trigger all immediately
```

Frequency format: `<min>[-<max>]/<duration>`. Units: `s`, `m`, `h`, `d`. Only fires when the conversation is idle.

### /loop

```
/loop <interval> <prompt>
/loop list
/loop stop <key>
```

Run a prompt or command on a recurring interval.

```
/loop 5m "check build status"       -- every 5 minutes
/loop 30s /compact                   -- every 30 seconds
/loop 2-3/h "check deploy"          -- 2-3 times per hour
/loop list                           -- show active loops
/loop stop abc123                    -- stop a loop
```

Minimum interval: 5 seconds.

---

## Plans

### /plan

```
/plan [list | approve <id> | cancel <id> | delete <id> | reset <id> | <description>]
```

| Subcommand | Description |
|------------|-------------|
| (none) | Open the plans panel |
| `list` | List all plans with status |
| `approve <id>` | Approve a pending plan |
| `cancel <id>` | Cancel a plan |
| `delete <id>` | Delete a plan |
| `reset <id>` | Reset a plan |
| `<description>` | Ask the agent to create a plan for the given description |

```
/plan list
/plan approve abc123
/plan Build a REST API for user management
```

---

## Scheduling

### /schedules

```
/schedules list | del | add <YYYYMMDDHHmmss> [reason]
```

Manage scheduled poll rechecks.

```
/schedules list
/schedules add 20260408140000 Check deployment status
/schedules del
```

---

## File Operations

### /view

```
/view <filename>
```

Open the file viewer overlay to preview a file (images, PDF, text, code).

### /upload

```
/upload
```

Open the file picker to upload a file as an attachment.

### /copy

```
/copy [N]
```

Copy the last (or Nth) assistant response to clipboard.

### /paste

```
/paste
```

Paste image or text from clipboard as an attachment.

### /files

```
/files
```

Toggle the files panel.

### /flows

```
/flows
```

Toggle the flows panel.

### /tasks

```
/tasks
```

Toggle the scheduled tasks panel.

---

## File Attachments

### Drag and Drop

Drag files from your file system onto the chat area. They are queued as pending attachments and sent with your next message.

### Clipboard Paste

Press **Ctrl+V** in the input field to paste images from the clipboard. Images are automatically queued as attachments.

### Supported Types

- Images (displayed inline as thumbnails)
- PDF files
- Plain text, HTML, Markdown
- Other file types (shown with a paperclip icon)

### Managing Attachments

- Pending attachments appear in a preview bar above the input
- Click the x button on an attachment to remove it
- Use `/clear-files` (or `/detach`) to remove all pending attachments

---

## Miscellaneous

### /batch

```
/batch <instruction> [--files <glob>]
```

Apply changes across multiple files in parallel using the delegate system.

```
/batch "add JSDoc to all functions" --files src/**/*.js
/batch "convert to async/await" --files *.ts
```

### /link

```
/link <provider> <id> [bot_token]
/link unlink <provider>
/link status
```

Link or unlink external accounts for cross-platform messaging.

### /tools

```
/tools
```

List all dynamic tools installed.

### /tool-metrics

```
/tool-metrics
/toolmetrics
```

Show per-tool execution counts, success/error totals, latency, and the most recent tool error recorded by the server.

### /install / /uninstall

```
/install <filename.py>
/uninstall <tool_name>
```

Install or remove custom tools.

### /prompt

```
/prompt list
/prompt use <name>
```

List available prompts or view a specific prompt's content.

### /login

```
/login
```

Redirect to the login page for re-authentication.

### /vm

```
/vm <list|kill|killall> [container_id]
```

Manage PawFlow Docker containers.

```
/vm list              -- list all PawFlow containers
/vm kill <id>         -- kill a specific container
/vm killall           -- kill all PawFlow containers
```

### /claude-login-server

```
/claude-login-server <service_name>
```

Login to Claude Code via server (opens browser in Docker container). Alias: `/cls`

### /claude-login-relay

```
/claude-login-relay <service_name> [relay_name]
```

Login to Claude Code via relay machine. Alias: `/clr`

### /claude-login-credentials

```
/claude-login-credentials <service_name> <credentials_json>
```

Set Claude Code credentials from `.credentials.json`. Alias: `/clc`

---

## Command Aliases

| Alias | Resolves to |
|-------|-------------|
| `/restart` | `/restart_from` |
| `/set_llm_service` | `/llm` |
| `/detach` | `/clear-files` |
| `/add-var` | `/add-variable` |
| `/list-secrets` | `/secrets` |
| `/list-variables` | `/variables` |
| `/vars` | `/variables` |
| `/int` | `/interrupt` |
| `/cls` | `/claude-login-server` |
| `/clr` | `/claude-login-relay` |
| `/clc` | `/claude-login-credentials` |
| `/term` | `/terminal` |
| `/fwd` | `/port-forward` |

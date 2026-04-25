# PawFlow Agent System

## 1. Overview

An **agent** in PawFlow is an LLM with a tool-use loop. It is not an abstract framework concept -- it is a concrete runtime: the agent receives a user message, builds a context (system prompt + conversation history), calls the LLM, executes any tool calls the LLM requests, feeds the results back, and repeats until the LLM produces a final text response with no further tool calls.

The core implementation is `AgentLoopTask`, a composite task assembled from mixins:

- **AgentCoreMixin** -- the execution loop itself
- **AgentContextMixin** -- context building, system prompt injection, tool configuration
- **AgentStreamingMixin** -- background thread execution, SSE streaming
- **AgentCompactionMixin** -- context size management, progressive clearing, summarization
- **AgentIdentityMixin** -- agent identity, multi-agent message differentiation
- **AgentSideChannelsMixin** -- BTW queries, broadcast to all agents
- **AgentActionsMixin** -- server-side command dispatch (slash commands)
- **AgentPollerMixin** -- scheduled/deferred message processing
- **AgentSerializationMixin** -- message serialization/deserialization
- **AgentUtilsMixin** -- shared helpers

The flow pattern is: `httpReceiver -> agentLoop -> handleHTTPResponse`. The agent returns an immediate ACK to the HTTP caller, then runs the LLM loop in a background thread, publishing results via SSE (Server-Sent Events).

---

## 2. Agent Configuration

### Agent Definitions (`config/agents.json` and ResourceStore)

Agents are stored as resources in the `ResourceStore`. Global agents are defined in `config/agents.json`; users can also create per-user agents. Each agent definition has this structure:

```json
{
  "name": "assistant",
  "description": "Default general-purpose assistant",
  "prompt": "You are a helpful assistant.",
  "llm_service": "${llm_default_service}",
  "model": "",
  "tools": [],
  "max_depth": 1,
  "timeout": 120,
  "_scope": "global"
}
```

**Fields:**

| Field | Description |
|-------|-------------|
| `name` | Unique agent identifier (case-sensitive). Used in routing and multi-agent conversations. |
| `prompt` | The system prompt. This is the agent's personality and instructions. |
| `llm_service` | Reference to an LLM service (supports expression language: `${var_name}`). Determines which LLM provider and model the agent uses. |
| `model` | Optional model override. If empty, uses the service's default model. |
| `tools` | Optional list of tool names to restrict the agent's toolset. Empty = all tools available. |
| `max_depth` | Maximum sub-agent delegation depth. |
| `timeout` | Request timeout in seconds for LLM calls. |
| `description` | Human-readable description. |
| `_scope` | `"global"` (available to all users) or `"user"` (private to one user). |
| `assigned_skills` | Optional list of skill definitions assigned to this agent. |

**Scoping:** Agent keys in `agents.json` use the format `__global__:name` for global agents or `userid:name` for user-scoped agents. The system resolves agents by checking user-scoped first, then global.

### Creating Agents

Agents can be created through:
- Editing `config/agents.json` directly (global agents)
- The `/agent create` chat command
- The `manage_resource` tool (the agent can create other agents)
- The admin UI

### LLM Service Reference

The `llm_service` field points to a configured LLM service (OpenAI-compatible, Anthropic, Claude Code, Grok, local models, etc.). Expression language references like `${llm_default_service}` are resolved at runtime from the expression cascade: flow -> conversation -> user -> global.

---

## 3. Agent Loop

The execution cycle follows this pattern:

```
User message
    |
    v
_prepare_agent_context()       -- Build full context (system prompt, history, tools)
    |
    v
_run_agent_loop()              -- The core loop
    |
    +---> LLM call (with tools + messages)
    |         |
    |         v
    |     Response has tool_calls?
    |         |
    |    YES  |  NO
    |    |    |    |
    |    v    |    v
    |  Execute tools   Final text response
    |    |              |
    |    v              v
    |  Append results   Publish "done" event
    |    |              Return
    |    v
    +--- Loop back to LLM call
```

### Key loop behaviors:

1. **Iteration limit**: `max_iterations` (default: 1000) prevents runaway loops.
2. **Consecutive tool limit**: `max_consecutive_tool_calls` caps repeated calls to the same tool (configurable per resilience style: cautious=10, balanced=100, aggressive=50+).
3. **Budget check**: If `max_budget_usd` is set on the LLM service, the loop stops when estimated cost exceeds the budget.
4. **Generation tracking**: Each conversation+agent pair has a generation counter. If a new message arrives (bumping the generation), the current loop detects staleness and can yield.
5. **Queue-based messaging**: New user messages do not cancel the running agent. They are queued and processed after the current turn completes. For Claude Code providers, messages can be injected directly into the active session (preemption).
6. **Multi-round**: `max_rounds` allows the agent to run multiple consecutive turns before yielding (useful for autonomous tasks).

### Message persistence

Every assistant message and tool result is persisted to the conversation store via `ConversationWriter` as it is produced. SSE events are published in parallel so the UI updates in real time. Context-internal messages (compaction acknowledgments) are never persisted to the transcript.

`ConversationWriter` runs one daemon thread per conversation behind a FIFO queue. `enqueue()` is non-blocking for throughput, so on process exit the queue may still hold items. The signal handler in `cli.py` calls `ConversationWriter.shutdown_all(wait_timeout=...)` **before** `os._exit(0)` to drain every queue - without this, in-flight writes die with the daemon thread and messages are lost. `shutdown_all` returns `False` if any queue times out; the caller logs this as data loss.

---

## 4. Context Management

### System Prompt Construction

The system prompt is assembled in layers during `_prepare_agent_context()`:

1. **Identity block** -- `[SYSTEM IDENTITY]` prefix with agent_id, model, provider, nickname, and multi-agent differentiation rules.
2. **Agent prompt** -- The `prompt` field from the agent definition.
3. **Security directive** -- Anti-injection rules for tool output content.
4. **Secrets directive** -- Rules about never leaking secret values.
5. **Behavior rules** -- Narration requirement, read_history hint, resilience style.
6. **Relay context** -- Connected relay services, filesystem roots, docker/local modes.
7. **Identity suffix** -- Ephemeral model/provider/service metadata (injected at call time, never persisted).
8. **Memory digest** -- Persistent memories relevant to this user+agent, built by `build_memory_digest()`.
9. **Diary digest** -- Past diary entries (observations, decisions, learnings) from `AgentDiary`.
10. **Cognitive tools hint** -- Summary of available cognitive tools (memory, knowledge graph, diary, project graph) so the agent knows what is available.
11. **Plan mode directive** -- If plan mode is active, forces the agent to call `create_plan` before executing tools.
12. **Claude Code rules** -- For CC providers, rules about using MCP tools exclusively.

### Project Instructions (`{agent_name}.md`)

If a file named `{agent_name}.md` exists in the relay filesystem root, its content is injected into the context as project instructions (after the system prompt, or after a conversation summary if one exists). This allows per-project, per-agent customization without modifying the agent definition.

### Context Loading

Messages are loaded from the conversation store with these strategies:

- **Shared context**: The default -- all messages from the conversation, filtered for the active agent.
- **Diverged context**: Per-agent context that has been manually edited or diverged from shared history.
- **Preloaded messages**: For task sub-conversations that have their own isolated message store.
- **Claude Code session resume**: If the CC provider has an active session, context loading is skipped (CC manages its own context).

### Context Compaction

When the context approaches the LLM's context window limit, PawFlow compacts it automatically. The compaction pipeline has multiple stages, from least to most destructive:

#### Stage 1: Time-based micro-compaction
After a conversation idle gap (default: 60 minutes), old tool results are replaced with `"[Old tool result content cleared]"`, keeping only the most recent results intact.

#### Stage 2: Progressive tool result clearing
Old tool results are deterministically truncated in passes:
- Pass 1: Results > 500 chars truncated to 200 chars
- Pass 2: Results > 100 chars truncated to 50 chars
- Pass 3: All remaining old results replaced with `"[result cleared]"`

This deterministic approach preserves the message prefix across calls, maximizing KV cache reuse.

#### Stage 3: Summarization
If progressive clearing is not enough, older messages are summarized:
1. The system selects a split point (keeping at least 25 recent conversation messages).
2. Old messages are converted to text and written to FileStore.
3. A summarizer LLM reads the file via a paginated tool loop, then calls `compact_result` to return the summary.
4. The summary replaces old messages as a `[Conversation summary]` user message, followed by an `"Understood."` assistant acknowledgment.

#### Stage 4: Force fit
As a last resort, messages are brute-force truncated: per-message character budgets (recent messages get more budget), then middle messages are dropped entirely, keeping only system prompt + last N.

### Auto-compact trigger

Auto-compaction runs when messages exceed 90% of `max_context_size`. It is skipped when a Claude Code session is active (CC manages its own context).

### Context-usage gauge (per-agent)

At the end of each turn, `agent_core` receives the provider's real `usage` (input/output tokens) and derives `context_used / context_max / context_pct`. These fields are:

1. **Emitted** on the `message_meta` SSE event (and `done`), so the chat UI's active-agents panel, header badge, and Resource Panel can update in real time.
2. **Persisted** on the conversation under the `context_usage` extra as a dict keyed by agent instance name: `{"<agent>": {"used": int, "max": int, "pct": float, "updated_at": int}}`.

Persistence is per-agent and keyed on the instance name (not the definition), which means each agent card in the Resource Panel shows its own gauge and the header badge shows the gauge for `selectedAgent`. The value is written from the final `message_meta` of a turn -- so it reflects the provider's real post-turn usage, and survives idle periods until the next turn updates it.

`list_resources` surfaces this value as `agents[i].context_usage` so the UI can hydrate its cache at load time without waiting for an SSE event.

---

## 5. Multi-Agent

PawFlow supports multiple agents in a single conversation. Each conversation tracks:

- `agents`: List of agents participating in the conversation.
- `agent`: The currently selected (primary) agent.
- `agent_nicknames`: User-assigned display names for agents.

### Message Differentiation

In multi-agent conversations, messages are prefixed so each agent can distinguish who said what:

- **Own messages**: `role=assistant` with no prefix -- the agent's own past responses.
- **Other agents**: `[Agent X]: ...` -- context from another agent, not instructions.
- **Task results**: `[Agent X in Task t_xxx]: ...` -- results from task sub-contexts, context only.
- **User to self**: No prefix -- the agent MUST respond to these.
- **User to others**: `[User to agent X]: ...` -- context only, the agent must NOT act on these.

### Agent Selection

The user can:
- Send a message to a specific agent: `/agent msg grok "What do you think?"`
- Use `target_agent` in the request body to route a single message.
- Switch the active agent for subsequent messages.
- Give agents nicknames for friendlier interaction.

### Agent Name Resolution

Agent names go through a resolution pipeline:
1. Check the nickname map (reverse lookup: nickname -> real name).
2. Check nickname map keys (case-insensitive match).
3. Return the original name if no mapping found.

---

## 6. Plan System

Plans are structured multi-step tasks with orchestrated execution. They are stored as individual JSON files via `PlanStore`.

### Lifecycle

```
create_plan          User or agent creates a plan
    |
    v
pending_approval     Plan is shown to the user for review
    |
    v
approve_plan         User approves (or the plan auto-approves if agents are assigned)
    |
    v
in_progress          Orchestrator drives step-by-step execution
    |
    v
completed            All steps done/skipped
```

### Plan Structure

```json
{
  "id": "p_abc12345",
  "title": "Refactor authentication module",
  "status": "in_progress",
  "created_by": "claude",
  "assigned_to": ["claude"],
  "steps": [
    {
      "index": 1,
      "description": "Audit current auth code",
      "status": "done",
      "assigned_to": "claude",
      "verifier": "",
      "note": "Found 3 issues"
    },
    {
      "index": 2,
      "description": "Implement fixes",
      "status": "in_progress",
      "assigned_to": "claude",
      "note": ""
    }
  ]
}
```

**Step statuses**: `pending`, `in_progress`, `done`, `skipped`, `error`, `pending_verification`.

### Orchestrator

The orchestrator (`orchestrate_next_step`) is NOT an LLM call -- it is pure logic:

1. Find the first pending (non-paused) step.
2. Validate the assigned agent exists.
3. Mark the step as `in_progress`.
4. Send a user message to the agent: `"Execute step N/total: description"` with instructions to call `update_plan` when done.
5. Schedule the agent via `PollScheduler`.

When the agent calls `update_plan(status="done")`:
1. The step is marked as done (or `pending_verification` if a verifier is assigned).
2. The agent is **force-stopped** -- it must not continue to other steps.
3. The orchestrator is called again for the next step.

### Plan Tools

| Tool | Description |
|------|-------------|
| `create_plan` | Create a plan with title and steps. Requires user approval. |
| `update_plan` | Mark steps as done or error. Agents can only update the current in_progress step. |
| `approve_plan` | User approves the plan (also available from UI). |
| `assign_plan` | Assign agents to plan steps. |
| `cancel_plan` | Cancel a plan. |
| `delete_plan` | Delete a plan. |
| `verify_plan_step` | Verify a completed step (when a verifier agent is assigned). |
| `EnterPlanMode` | Enable plan-mode for the current conversation. While active, agent_context appends a directive forcing `create_plan` before any other tool. Pawflow replacement for the Claude Code built-in. |
| `ExitPlanMode` | Disable plan-mode for the current conversation and return to normal operation. Pawflow replacement for the Claude Code built-in. |

### Force Stop

When a step completes, the executing agent is force-stopped to prevent it from running ahead. This is done by:
1. Bumping the conversation generation counter (agent loop detects staleness).
2. Setting the interrupt flag.
3. Killing any Claude Code subprocess if applicable.

---

## 7. Sub-tasks and BTW

### BTW (Side-Channel Queries)

BTW ("by the way") is a lightweight side-channel: the user asks a quick question while the agent is busy working. It does NOT interrupt the running task.

How it works:
1. A separate LLM call is made with a lightweight context (system prompt + last 6 messages, truncated to 200 chars each).
2. No tools are available -- the response is a single text answer.
3. The response is streamed via SSE events (`btw_thinking`, `btw_token`, `btw_done`).
4. The Q&A is persisted in the conversation history with a `btw: true` flag.
5. For Claude Code providers, a transient sub-conversation is created and destroyed after the call.

### Broadcast

`/btw @ALL "question"` sends the question to every defined agent in parallel. Each response is published as an SSE event. A per-client lock serializes concurrent BTW calls to the same CC provider.

### Task Sub-conversations

Tasks (`assign_task`, `complete_task`, `verify_task`) run in isolated sub-conversations with the format `{conversation_id}::task::{task_id}`. These have their own message store and context, allowing an agent to work on a background task without polluting the main conversation. Tasks auto-reschedule with configurable delays and support error backoff.

---

## 8. Actions

Actions are server-side command handlers organized into modules under `tasks/ai/actions/`. They handle slash commands and UI interactions.

| Module | Actions |
|--------|---------|
| **plans.py** | `get_plans`, `get_plan`, `create_plan_user`, `approve_plan`, `reject_plan`, `cancel_plan`, `delete_plan`, `update_plan_step`, `assign_plan_step`, `pause_step`, `resume_step`. Orchestrator logic for step-by-step execution. |
| **conversation.py** | `list_conversations`, conversation management, agent switching. |
| **memory_prompts.py** | `list_memories`, memory browsing and management. |
| **context_ops.py** | Context viewing and editing, Claude Code session management, `/compact` command. |
| **tools_exec.py** | `exec_inline` -- execute shell commands on relay (`!cmd` shortcut). |
| **agent_resource.py** | `set_agent_nickname`, agent resource management. |
| **cancel_interrupt.py** | `cancel` -- stop a running agent, with generation bump and subprocess kill. |
| **command_dispatch.py** | Unified `/command` parser -- the single source of truth for all slash commands (webchat, VS Code, CLI all use it). |
| **files_fs.py** | `list_conv_files`, file management for conversations. |
| **media.py** | `list_image_services`, image/video/audio generation service discovery. |
| **misc.py** | `model` override, theme, effort, fast mode, plan mode, fork, doctor. |
| **secrets_variables.py** | `add_secret`, secret and variable management. |
| **service_flow.py** | Service and flow management commands. |
| **usage.py** | `cost` -- token usage and cost tracking via TokenTracker. |
| **scheduling.py** | Task scheduling, agent thread management, kill running task agents. |
| **account_linking.py** | `link_account` -- cross-platform identity linking (Telegram, Discord, etc.). |

---

## 9. Tool Wiring

Tools are configured per-request in `_configure_tool_handlers()`. Every tool handler receives the runtime context it needs:

### Configuration Parameters

Each handler type receives different parameters:

- **Filesystem tools** (`BaseFsHandler`): `user_id`, `conversation_id`, relay service (resolved from conversation bindings, with per-agent scope), available filesystem services list.
- **Memory tools** (`RememberHandler`, `RecallHandler`, etc.): `user_id`, `agent_name`, `conversation_id`, optional memory LLM client for relevance filtering.
- **Plan tools** (`CreatePlanHandler`, `UpdatePlanHandler`, etc.): `conversation_id`, `agent_name`, `user_id`.
- **Image/video/audio generation**: `base_url`, `user_id`, service resolver for per-agent routing.
- **Sub-agent delegation** (`SpawnAgentsHandler`): LLM client, client resolver, SSE event callback, available agent names list, source agent identity.
- **Script execution** (`ExecuteScriptHandler`): `base_url`, filesystem service resolver for `fs://` URLs.
- **Identity tools** (`LinkIdentityHandler`): `user_id`.
- **Knowledge graph, diary, project graph**: `user_id`, `agent_name`, `conversation_id`, filesystem service.

### Relay Resolution

For filesystem tools, the relay service is resolved in this order:
1. Per-agent relay binding for the conversation (`get_default(conversation_id, agent=agent_name)`).
2. Global relay bindings for the conversation.
3. Fallback: any filesystem service available to the user.

### Meta-tools (Lazy Tools)

Instead of sending all tool schemas to the LLM (which can consume thousands of tokens), PawFlow uses two meta-tools:
- `get_tool_schema()` -- The agent calls this to discover available tools.
- `use_tool(tool_name, arguments)` -- The agent calls this to execute any tool.

This reduces the constant token overhead from ~7000 tokens to ~200 tokens, making it practical for smaller context LLMs.

### Tool Result Size Limit

Tool results are capped at `tool_result_max_chars` (default: 50,000 chars), configurable per LLM service or agent. This prevents a single large tool result from blowing up the context.

### Long-running Command Watch (`Monitor`)

Pawflow replacement for the Claude Code built-in `Monitor`. Runs a relay bash command and returns early on the first of: command exit, regex pattern matched `limit` lines, or `timeout_ms` elapsed (capped at 10 minutes). Use it instead of polling via `ScheduleWakeup` when you need to react as soon as a marker appears in the output (`FAILED`, `listening on port`, etc.). For watches longer than 10 minutes, use `bash(run_in_background=true)` plus output-file polling — `Monitor` is intentionally bounded so it never holds a turn open indefinitely.

---

## 10. Streaming

PawFlow uses Server-Sent Events (SSE) for real-time communication between the agent and the client.

### Execution Flow

1. `_execute_streaming()` receives the HTTP request.
2. It immediately returns an ACK response: `{"status": "accepted", "conversation_id": "..."}`.
3. A background thread is spawned to run `_prepare_agent_context()` + `_run_agent_loop()`.
4. All events are published via `ConversationEventBus`.

### SSE Event Types

| Event | Description |
|-------|-------------|
| `thinking` | Agent is starting to process (includes `agent_name`). |
| `token` | A token of the response (streamed incrementally). |
| `tool_start` | Agent is calling a tool (name + arguments). |
| `tool_result` | Tool execution completed (result summary). |
| `done` | Agent turn is complete. Includes response, model, tokens_in/out, tools_called, duration_ms. |
| `error_event` | An error occurred. |
| `message_queued` | A new message was queued because the agent is busy. |
| `btw_thinking` | BTW side-channel query started. |
| `btw_token` | BTW response token. |
| `btw_done` | BTW response complete. |
| `plan_created` | A new plan was created. |
| `plan_updated` | Plan status or step status changed. |
| `thought_scheduled` | Random thought scheduled for later. |
| `title_generated` | Conversation title was auto-generated. |

### Queue Behavior

If a user sends a message while the agent is already running:
- For Claude Code providers: the message is injected directly into the active session (preemption).
- For API providers: the message is queued in memory (`_pending_user_msgs`). After the current turn completes, a `PollScheduler` delay triggers processing of queued messages.

---

## 11. Auto-triggers

Several behaviors run automatically after an agent turn completes.

### Auto-title Generation

After the first successful agent turn, if no title exists for the conversation and a `title_llm_service` is configured:
1. A background thread extracts the last ~1000 chars of context.
2. The title LLM generates a short title.
3. The title is published via SSE (`title_generated`) and stored in conversation extras.

### Auto-save Memories

Every ~15 user messages (configurable via `_AUTO_SAVE_INTERVAL`):
1. The system loads the most recent messages.
2. A summarizer LLM extracts memorable facts.
3. Extracted memories are saved to the `MemoryStore` for future recall.

This ensures important information is captured even if the user never explicitly asks the agent to remember something.

### Auto-compact

Context compaction runs automatically before each agent turn when messages exceed 90% of `max_context_size`. See [Context Compaction](#context-compaction) for details.

### Auto-reschedule Tasks

Active tasks (sub-conversations) are automatically rescheduled after each turn:
- On success: rescheduled with normal delay.
- On error: rescheduled with exponential backoff (delay doubles each failure, capped at 5 minutes).
- The error counter resets on success.
- Tasks respect `max_iterations` and stop when the limit is reached.

### Random Thoughts

If enabled per-agent (`random_thought::agent_name` config), the agent is scheduled for spontaneous turns at random intervals between `min_interval` and `max_interval` seconds. After each random thought turn, the next one is automatically rescheduled.

### Pending Message Detection

After a turn completes, the system checks if there are unanswered user messages at the tail of the conversation. If found, a short-delay reschedule is triggered to process them. This handles edge cases where messages arrive during the brief window between loop iterations.

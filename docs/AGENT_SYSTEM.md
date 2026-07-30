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

Non-HTTP clients use the same runtime contract through `core.agent_runtime_api`.
For example, the Telegram client flow normalizes Telegram updates into an
`AgentRequest`, submits it to the live `AgentLoopTask`, and waits for the
correlated `done` event using the request `turn_id`. The conversation event bus
still broadcasts every event by `conversation_id` to all connected clients; the
`turn_id` is only reply correlation for transports that need to answer a
specific inbound message.

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

The `llm_service` field points to an LLM-capable service: either a direct
`llmConnection` or a composite `llmAggregator`. Built-in direct providers are
`openai`, `anthropic`, `claude-code`, `claude-code-interactive`,
`antigravity-interactive`, `codex-app-server`, and `gemini`; OpenAI-compatible
and Anthropic-compatible endpoints use `base_url` on the corresponding direct
API provider. An `llmAggregator` consults its configured advisor connections in
parallel and passes their internal plans to its final connection. Expression
language references like `${llm_default_service}` are resolved at runtime from
the expression cascade: flow -> conversation -> user -> global.

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

### Agent hooks

Agent hooks are repository resources of type `agent_hook`, scoped like other repository resources (`global`, `user`, or `conversation`). The Resource Panel exposes an Agent Hooks repository section for create/edit/delete and PFP-imported hooks. Conversation configuration only stores bindings in `conversation_hooks`: hook name, enabled flag, priority, optional event filters, optional agent/tool filters, and fail policy. It does not embed hook code.

Runtime hook events are:

| Event | Timing |
|---|---|
| `pre_tool_call` | Before a provider/API/relay tool call is executed. Can block the call or replace `tool_name`/`arguments`. |
| `post_tool_call` | After tool execution and secret redaction. Can replace the result returned to the LLM. |
| `pre_user_message` | Before a user message is persisted or queued. Can block or rewrite the message content. |
| `post_llm_message` | Before an assistant message is persisted. Can block or rewrite content/thinking. |
| `post_llm_thinking` | Before assistant thinking is persisted. Can rewrite thinking text. |
| `pre_compact` | Before context compaction. Can block compaction or add compact instructions. |
| `post_compact` | After compaction has produced the replacement context. The payload includes `compacted_messages` and a compatibility alias `compacted`, both as serialized message dictionaries. |

Hook code returns a JSON object shaped as `{"decision":"allow|block|replace","reason":"optional","payload":{...}}`. Empty event/agent/tool filters mean all. `fail_policy: "closed"` blocks the triggering operation when the hook fails; the default is fail-open. PFP hooks are installed as `agent_hook` runtime objects and run through the same signed package runtime bridge as package tools. Inline source hooks run through the restricted `execute_script` sandbox path, not through auto-detected user relays.

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
8. **Cognitive digests** -- memory, diary, knowledge graph and project structure. On CLI providers they are appended here; on API providers they are **not** part of the system prompt at all (see *Prompt cache prefix* below).
9. **Cognitive tools hint** -- Summary of available cognitive tools (memory, knowledge graph, diary, project graph) so the agent knows what is available.
10. **Plan mode directive** -- If plan mode is active, forces the agent to call `create_plan` before executing tools.
11. **Claude Code rules** -- For CC providers, rules about using MCP tools exclusively.

### Prompt cache prefix

Provider caching is prefix-based: a single changed byte in the system block
invalidates the system block, the tool definitions **and every message behind
them**. On a long conversation that is the difference between paying for a few
hundred tokens and re-reading the whole history.

The rule is therefore: **anything that can change between two turns of the same
conversation must not be in the prefix.** Two such things exist, and both are
merged into the *last user message* by `_alc_inject_dynamic_metadata()`, after
all cache breakpoints:

- the current date/time and the context-usage gauge;
- the cognitive digests (memory, diary, KG, project structure), which are
  rebuilt from live stores and therefore move on any `remember`,
  `diary_write`, `kg_add` or graph rebuild.

CLI providers keep the digests in the system prompt: their prompt goes through
the cold-start bootstrap file, the same text would also be echoed in the prompt
handed to the CLI binary, and those runtimes manage their own caching.

What stays stable, deliberately: the tool list is exactly two meta-tools
(`get_tool_schema`, `use_tool`) regardless of what is installed, so tool
definitions never move mid-conversation.

`core/cache_diagnostics.py` watches for breaks — a significant drop in
`cache_read` tokens — and names the cause (system prompt, tools, model, or
prefix restructuring). Its state is keyed **per conversation**: one
`LLMConnection` service owns a single `LLMClient` shared by every conversation
using it, so a single slot would compare one conversation's turn against
another's and report a break on every switch. Observed hit rates are visible in
the usage dashboard (`cache_read / (tokens_in + cache_read)`).

### Project Instructions (`{agent_name}.md`)

If a file named `{agent_name}.md` exists in the relay filesystem root, its content is injected into the context as project instructions (after the system prompt, or after a conversation summary if one exists). This allows per-project, per-agent customization without modifying the agent definition.

### Programmable Skills

Skills are effective only when they are assigned to an agent definition through `assigned_skills`. PawFlow does not inject a separate conversation-level `active_resources.skills` list; legacy activation paths must be treated as inactive UI/API compatibility only. Assign or remove skills with `/skill assign @agent @skill` and `/skill unassign @agent @skill`.

Assigned skills are lazy-loaded. Assigning a skill writes a lightweight context message to the target agent and rebuilt system prompts include only an availability manifest with the skill name and description. The full skill prompt is returned only when the agent calls `load_skill(name="skill-name")`, and `load_skill` refuses skills that are not assigned to the current agent. Updating a skill writes a lightweight context message to assigned conversation agents telling them to reload the skill if needed; deleting or uninstalling a skill removes it from visible agents' `assigned_skills` and writes the normal removal context message. Users can also invoke a visible skill immediately with `/skill run [@agent] <skill> [args...]` or the shortcut `//<skill> [@agent] [args...]`; this does not persist assignment, and queues the rendered skill prompt as a user message for the selected or explicit target agent.

Skill directories are bind-mounted read-only into CLI provider containers under `/skills`, mirroring the server repository layout (`/skills/global/<name>`, `/skills/users/<uid>/<name>`, `/skills/users/<uid>/<conv>/<name>`). The scope parents are mounted once, so a skill assigned mid-session — or a skill run one-shot while unassigned — becomes visible without recreating the container. `SKILL.md` content is delivered to the agent verbatim — PawFlow does not substitute placeholders. The skill's mounted directory is given as an explicit `Skill directory:` line, and `/skill run` adds an explicit `Arguments:` line; asset references such as `${CLAUDE_SKILL_DIR}/scripts/foo.py` are read by the agent against that stated directory. The loaded skill block also appends a `### Skill assets` section that enumerates every file bundled with the skill and inlines small text assets (≤12 KB each, ≤48 KB total) — a context-only fallback so the assets remain usable when the skill directory is not mounted (e.g. an agent with no connected relay).

Skill names follow the Agent Skills spec: lowercase letters, digits and single hyphens, at most 64 characters, and must not contain the reserved words `anthropic` or `claude`; descriptions are capped at 1024 characters. Skill directories, `SKILL.md`, and bundled assets are written world-readable (`0o755`/`0o644`) so the CLI provider container (uid 1000) can read the mounted skill tree. The optional `allowed-tools` frontmatter field is surfaced to the agent as advisory guidance — a preferred-tools hint — not an enforced restriction: PawFlow does not filter the tool registry while a skill is active.

Untrusted or imported skills are reviewed before create, update, and import through the configured `summarizer` service. The summarizer points to the selected `llmConnection`; PawFlow passes package content as data and calls the reviewer with `tools=None`. If no effective summarizer LLM is configured, skill writes and imports fail closed instead of persisting unreviewed content. `manage_resource(action="review", resource_type="skill", data={...})` returns the same review report without writing the skill. Agents can use `manage_resource(action="assign_skill"|"unassign_skill", resource_type="skill", agent_name="...", skill_name="...")` to change `assigned_skills` through the same live notification path as `/skill assign` and `/skill unassign`. Executable package content is reviewed through the same path and never becomes server-side Python code.

External Agent Skills can be discovered with `/skill search` or `manage_resource(action="search_marketplace", resource_type="skill", source="codex|claude|hermes|openclaw|all", query="...")`. Import uses `/skill import` or `manage_resource(action="import_marketplace", resource_type="skill", source="...", ref="...")`. The importer accepts known marketplace refs and GitHub tree URLs, fetches the complete bounded skill directory including binary assets, requires a UTF-8 root `SKILL.md`, stores provenance and package hashes, and reviews the full package before writing. `allowed-tools` and bundled scripts are never trusted as permissions; review-blocked packages and packages requiring human review need an explicit force flag after inspection.

PawFlow Packages (`.pfp`) extend import/export beyond single skills. A signed `.pfp` contains `pfp.json`, `pfp.lock.json`, `signature.ed25519`, and content objects such as agents, prompts, skills, themes, task definitions, flows, service definitions, tools, service providers, and flow task providers. `/pfp inspect` and `manage_package(action="inspect")` return a selectable install plan; `/pfp install` writes only selected objects and records per-object provenance so update/uninstall can reason about ownership. Agents with `assigned_skills` require those skills to be installed already or selected from the same package. `/pfp export` automatically includes skills referenced by exported agents. `/pfp update` updates previously installed objects from the same package and skips locally modified resources unless forced. Decentralized package registries are static JSON indexes managed with `/pfp registry add|list|remove`; `/pfp search` searches configured registries and registry SHA-256 values pin downloaded artifacts. Code-bearing package objects are never imported as Python server code: they execute through the relay Python runner, and host tool/service calls are brokered through declared grants and `PackageCapabilityBroker`.

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

Proactive PawFlow compaction is controlled by the selected LLM service's `compact_threshold_pct`. `0` disables proactive PawFlow compaction; a positive value compacts when the estimated context reaches that percentage of `max_context_size`. Claude Code's own compact boundary can still fire independently, and PawFlow handles provider-triggered compact events by compacting PawFlow context and restarting the provider session.

Manual `/compact` is not a separate compaction implementation. It calls the same store-backed procedure as provider-triggered compaction: `_compact()` assembles the shared pyramid header and a bounded raw tail, persists the replacement context, and the caller only differs in how the trigger was initiated.

Background pyramid buckets are different from hot-path context compaction. They run asynchronously and only submit a summarizer call when the bucket input is useful: at least four times the L1 summary target (`BUCKET_OUTPUT_TARGET`, currently 2000 tokens). This prevents the background worker from spending an LLM call to summarize tiny slices after every few chat messages.

Independent contexts, such as task and isolated delegate sub-conversations, do not use the parent conversation's shared pyramid. When they cross the compact threshold, PawFlow summarizes the older head of that isolated context directly, keeps a raw recent tail, and writes the compacted result to the sub-conversation's private agent context. The transcript remains the faithful audit log; later task iterations resume from the compacted private context when it exists.

### Context-usage gauge (per-agent)

`tasks.ai.context_usage.compute_context_usage(conversation_id, agent_name, user_id=...)` is the single server-side calculation point for the gauge. It resolves the agent's LLM service, computes the effective context window, and returns `used / max / pct`. Direct API providers count the PawFlow messages sent in the request. Stateful CLI providers count their provider-side phase instead: zero after session invalidation, only the short injected bootstrap prompt before `initial_context.md` is read, then the observed native read result and subsequent deltas. The serialized file body is never counted before the CLI actually reads it.

Gauge updates are emitted as `message_meta` SSE events carrying `conversation_id` and `agent_name`, so the chat UI updates only the matching conversation and agent surfaces. `compact_progress`, `list_active`, and `list_resources` do not compute alternate live gauge values.
The bootstrap token count is stored as an integer-only map shared by reference
across all `LLMClient` call clones for a stream; the clone that writes the cold
session file and the resolver clone that renders the gauge therefore observe the
same baseline. Both `message_meta` and `done` preserve `cli_context_state` through
their SSE handlers, including authoritative `cold` zero after compaction.

The latest value is persisted under the conversation `context_usage` extra as a dict keyed by agent instance name: `{"<agent>": {"used": int, "max": int, "pct": float, "updated_at": float}}`. Persistence is per-agent and keyed on the instance name (not the definition), which means each Resource Panel agent card shows its own gauge and the header badge shows the gauge for `selectedAgent`.

The persisted entry is what `compute_context_usage` returns while no agent is running, so it must be invalidated whenever the thing it describes disappears. A CLI session dies with the server: `_prepare_agent_context` therefore calls `reset_cli_context_usage` as soon as it finds a CLI provider with no live session, persists the zeroed entry, and publishes it. Without that, a restart redisplays the dead session's percentage against a provider window nothing has filled yet.

**Two different quantities drive compaction, at two different moments, and both are correct where they apply.** While a CLI session is live, `_alc_maybe_auto_compact_after_append` evaluates `compact_threshold_pct` against `compute_context_usage` — the gauge itself. The provider window is what can overflow, so the provider window is what is watched, and a gauge reading below the threshold means no compaction will fire.

The whole stored agent context is measured at one place only: the cold-session branch of `_prepare_agent_context` (`_agentctx_p2`, gated on `not _cli_has_session`). That is the bootstrap moment, when the provider window is empty by definition and the quantity that matters is the size of what is about to be serialized into `initial_context.md`. A store above the threshold is squeezed before it is written.

A cold start is held to a lower bar than the live threshold, `CLI_COLD_START_TRIGGER_FRACTION` (0.40 of `max_context_size`), rather than to `compact_threshold_pct`. The live threshold guards against overflow, and 95% is the right bar for that; but a cold session receives its whole context at once — serialized into `initial_context.md` and read back as one tool result — so the same 95% would open a session at 94% of the window with nothing done yet. The cold-start bar applies whatever `compact_threshold_pct` says, including when it is 0: the provider's own compaction mechanism cannot help here, because the file is written before the provider sees anything. A stricter configured value is never loosened.

Both `_should_proactive_compact` and the `_compact` call it guards read this fraction through `st._cold_start_trigger_fraction()`. They must agree: `_compact` re-checks the threshold itself and returns the messages untouched when it is not met, so a decision taken at 40% and handed a 95% bar is reversed silently.

When the gauge and the compaction threshold appear to disagree, measure rather than reason: `python3 tools/gauge_probe.py <conversation_dir> [agent]` runs both counters over a stored agent context and reports how much of the difference the boundary accounts for. `UNEXPLAINED` must be 0; anything else means the gauge is losing messages. It also lists every *structural* bootstrap marker — a plain grep for the marker string is unusable, because it matches any message that merely quotes it, including tool output from reading this repository's own source. The probe is read-only, needs no network, and falls back to approximate counting when `tiktoken` is absent (the ratio between the two counters stays valid either way). A bare `.jsonl` path is accepted, which is how a version recovered from the conversation's git history is inspected.

### Cold start or delta — two cases, no third one

1. **No process running** → we launch one → that is a cold start → it receives the FULL context.
2. **A process is running** → it already holds the conversation → it receives the delta.

The context phase decides which case applies (`_agentctx_p1`, gated on the live registry), but the *provider* is what actually launches. Only the provider can find the process gone — it crashed, or its container was stopped — after the context was already built as a delta. Launching then would be case 1 carrying case 2's context: a fresh process handed a bare question, with no transcript, persona, skills or tool configuration.

So it does not launch. `_cli_require_cold_context` raises `ColdStartRequired`, `_alc_llm_turn` catches it and rebuilds the turn with `_prepare_agent_context(..., force_cold=True)` — case 1 through the ordinary cold path, nothing reassembled by hand — then runs the turn again. Nothing has reached the model at that point, so the restart costs no tokens. It happens at most once per turn: twice would mean the process dies as fast as we start it, and that must surface rather than spin.

`force_cold` is a third *caller*, not a third state: the turn already knows it is going to launch, so probing again could only answer "warm" and strip the context that launch needs.

Two invariants keep this honest. The delta marker lives on the client, which outlives the turn, so `_agentctx_p1` clears it on every build — a resume must never mark the cold turn that follows it. And the cancel checkpoint is consumed on injection and is *not* cold-gated, so the rebuild is handed back what the first pass ate; otherwise a rebuilt turn silently loses its "continue where you left off" instruction.

**A lookup is a use.** The idle sweeper reaps containers nobody asks for, and `last_used` is its only evidence, so every registry lookup that hands a container to a caller refreshes it (`CodexLiveRegistry`/`GeminiLiveRegistry.get`/`get_compatible`, `LiveSessionRegistry.get`/`find_for_agent`, and the CCI and Antigravity pools' `find_session`). Without that, a session at the end of its TTL could be found alive by the context phase and swept a tick later, before the provider claimed it. The TTL is unchanged: a session nobody asks for is still reaped on schedule, and an active turn is never reaped at all.

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

### Delegate Reply Context

Shared `delegate` calls keep a full audit trail in the transcript while keeping
the caller's LLM context compact. The delegate target keeps its complete private
context, including intermediate assistant messages, tool calls, and tool
results. The caller receives the original delegate request and only the final
synthesized reply from the target; the target's intermediate assistant blocks,
tool calls, and tool results are not appended to the caller's private context.
Delegate replies are never projected into shared context.

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
3. Killing any CLI provider subprocess or live app-server container if applicable.
4. Clearing the stopped agent's pending queue, pending PollScheduler wakeups, and cancel checkpoint so force stop cannot replay queued work or relaunch the agent.

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

Tasks (`assign_task`, `complete_task`, `verify_task`) run in isolated sub-conversations with the format `{conversation_id}::task::{task_id}`. These have their own message store and context, allowing an agent to work on a background task without polluting the main conversation. A newly assigned or resumed task wakes immediately; subsequent runs auto-reschedule with the configured interval and support error backoff.

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
| **usage.py** | `cost` / `get_cost` / `get_usage` -- token usage and cost from the persistent usage ledger (`core/usage_ledger.py`); `usage_summary` / `usage_timeseries` / `usage_top` / `usage_export` -- filtered ledger queries (period, agent, service, channel; admin can query all users). |
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

Agents can override the relay per call with `relay="<service-id>"`. The meta-tool layer maps this to the tool's native selector (`source`, `destination`, `filesystem`, or `service`) before validation and execution. Filesystem-backed tools also expose `local`: omitted/false runs in the relay Docker container; true forwards through the relay host helper and requires the relay to run with `--allow-local`.

### Meta-tools (Lazy Tools)

Instead of sending all tool schemas to the LLM (which can consume thousands of tokens), PawFlow uses two meta-tools:
- `get_tool_schema()` -- The agent calls this to discover available tools.
- `use_tool(tool_name, arguments_json)` -- The provider-facing execution contract. `arguments_json` is a JSON object string matching the target tool schema.

`UseToolHandler` still accepts legacy/internal `arguments` objects for compatibility, but the exposed schema deliberately avoids nested free-form objects because some OpenAI-compatible backends drop them and repeatedly call tools with `{}`.

This reduces the constant token overhead from ~7000 tokens to ~200 tokens, making it practical for smaller context LLMs.

### Tool Metrics

`ToolRegistry` records process-local metrics for every dispatch, including unknown
or blocked tools: call count, successes, errors, total duration, average/min/max
and last duration, last status, last timestamp, and the latest error text. Returned
`Error:` tool results are counted as metric errors even when no Python exception is
raised. Sub-agent tool execution also goes through the registry so it is counted
with normal agent, `/call`, and MCP bridge executions. `/tool-metrics` exposes the
process-local snapshot for operators. These counters are intentionally in-memory
operational metrics; they do not alter conversation history or tool results.

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
| `token` | A token of the response (streamed incrementally). **Must carry a non-empty `msg_id`** — see below; `publish_event` raises `ValueError` otherwise. |
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

#### Why `token` requires a `msg_id`

The client accumulates `token` text into a streaming bubble, and that bubble
is reconciled against the transcript by `msg_id`. Without an id the bubble is
anonymous: the turn-ending event is then the only thing that can pair it with
the line persisted by `ConversationWriter`, and if that event is lost, gap
reconciliation after a reconnect renders the stored message next to the bubble
already on screen — the same answer, twice. `ConversationEventBus.publish_event`
therefore refuses an untagged `token`. The cost of a refusal is the live
preview only; the message itself still reaches the client through the
persisted `new_message` event.

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

### Passive Memory Recall

`recall` and `semantic_recall` are tools: they fire only when the agent decides
to look, which requires it to already suspect that something relevant exists.
The memory digest is the opposite — top-N per category, identical whatever the
user just said.

`core/passive_recall.py` closes the gap. Each turn:

1. the block computed during the **previous** turn is injected (via the dynamic
   channel, so it costs nothing in prompt-cache terms);
2. the text the user just sent is embedded and matched against the memory store
   **in a daemon thread**, and the result is stored for the next turn.

One turn of latency is the price of never delaying a turn: a slow or missing
embedding provider degrades to "no passive memories", never to a stall. In a
conversation the delay is nearly free — the topic of turn N is almost always
still the topic of turn N+1.

Hits below `MIN_SCORE` (0.4) are dropped: with a small store something always
scores above zero, and an unrelated memory is worse than none. Memories already
quoted in the static digest are skipped. `passive_recall_limit` in
`global_parameters.json` (or `PAWFLOW_PASSIVE_RECALL_LIMIT`) caps the number of
entries; `0` disables the feature.

### Auto-poke

The plan orchestrator advances on one signal: the agent calling `update_plan`.
A turn that ends without it leaves the step `in_progress` and **nothing ever
wakes the agent again** — the plan stalls until a human notices. The common
failure is not a wrong answer, it is an early exit.

After a turn ends, `core/auto_poke.py` decides whether it left such a step
behind and hands the turn back with a message naming the two acceptable exits:
finish the step, or report the blocker. Delivery goes through the same
`deliver_agent_message()` the orchestrator uses, so the poke is an ordinary
persisted user message — visible in the transcript, auditable.

Guardrails:

- only a step this agent owns, `in_progress`, unpaused, on a plan that is
  itself running — never a plan awaiting approval, never `pending_verification`;
- at most `auto_poke_limit` consecutive pokes (default 2) per step; the counter
  resets as soon as the step or its note changes, so progress buys patience and
  a stuck agent is never poked forever;
- never after an error, an interruption or a force stop — a force stop is a
  decision, not a failure, and must never affect the next loop;
- never when messages are already queued: those wake the agent anyway.

`auto_poke_limit = 0` (or `PAWFLOW_AUTO_POKE_LIMIT=0`) disables it.

### Cross-agent read conflicts

Several agents in one conversation share the same relay, and therefore the same
files. Agent B reads `service.py`, reasons about it for a few turns, and
meanwhile agent A rewrites it. Nothing used to tell B: it kept editing against a
view that no longer existed, and the collision surfaced only as a failed
`old_string` match — or, worse, as a silently clobbered change.

`core/read_conflict.py` closes that hole using state the edit guard already
keeps. `core/handlers/_edit_guard.py` records, per agent, the hash of every file
that agent has read; `readers_of()` answers the question that matters when a
write lands: *which other agents have read this path, and does what they saw
still match what is on disk?* Every stale reader gets a pending notice, titled
**Files that changed under you**.

The mutation tools report through `BaseFsHandler._note_write()`: `write`,
`edit`, `apply_patch`, `batch_edit`, `find_replace` and `delete`, on both the
workdir and the relay path.

#### Two delivery channels

The notice reaches the agent by whichever of these comes first. `pending_block()`
clears on read, so exactly one of them ever delivers it — it can never arrive
twice.

1. **Mid-turn**, riding the last tool result of a batch (`_alc_iteration.py`).
   A long tool loop is exactly when the collision window is widest, so waiting
   for the next turn would be waiting through the dangerous part. The notice is
   appended *after* the `_wrap_tool_output` envelope via
   `AgentCoreMixin._attach_platform_note()`: that envelope marks content as
   untrusted external data, and burying a PawFlow-generated warning inside it
   would teach the agent to distrust our own warnings. The block is only taken
   when the batch has a result to carry it — taken with nowhere to put it, it
   would be dropped.
2. **Next turn**, through the dynamic-metadata channel at context build
   (`_agentctx_p3.py`), so it costs nothing in prompt-cache terms. This covers
   the turn that ended without tool calls.

Properties that keep it cheap and quiet:

- **Zero cost when alone.** A single-agent conversation has no other readers, so
  a write does one dict scan and stops. The common case pays nothing.
- **Silent when nothing changed.** When the writer already holds the new bytes
  they are hashed and compared: rewriting a file with identical content notifies
  nobody. The relay path, where fetching them back would cost a round trip,
  invalidates unconditionally — a successful edit there did change the content.
- **Cleared by a re-read.** `track_read()` drops the notice: an agent whose view
  is current again is told nothing. It is told once, not every turn — and by one
  channel only, since taking the block clears it.
- **Advisory, never blocking.** The notice asks the agent to re-read; it never
  refuses an operation. Identity is the canonical path without the filesystem
  service, so two agents on *different* relays holding the same absolute path
  would produce a spurious notice — the cost of that case is one wasted read.

State is bounded: `MAX_PATHS` (10) paths per agent, `MAX_TRACKED` (256) agents,
oldest evicted first. `clear_conversation()` and `clear_agent()` on the edit
guard drop the matching notices too.

### Auto-compact

Context compaction can run before or during agent turns when the selected LLM service sets `compact_threshold_pct`, or when a stateful provider reports its own compact boundary. See [Context Compaction](#context-compaction) for details.

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

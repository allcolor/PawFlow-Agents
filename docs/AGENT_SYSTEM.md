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
`openai`, `openai-responses`, `anthropic`, `claude-code`,
`claude-code-interactive`, `antigravity-interactive`, `codex-app-server`,
`codex-interactive`, and `gemini`; OpenAI-compatible
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
7. **One iteration owns one heartbeat**: the heartbeat is a thread started per iteration, covering the LLM call and the tools. `_alc_iteration` starts it and stops it in a `finally`, because the body leaves by five different returns — a compact restart, a cold restart, an overflow retry, a break, the normal end — and by any exception the turn raises. Stopping it at each return is how threads were left behind, one per attempt, all publishing for the same conversation. The body still stops it early on purpose before the end-of-iteration bookkeeping; the handle is cleared on stop, so the `finally` then finds nothing to do and it is never stopped twice.

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

**The rule has two directions, and both are guarded.** The paragraph above is one of them. The mirror is a turn built as a *cold start* whose provider then finds the process ALIVE, and it went unguarded for a long time: the provider simply sent a delta carved out of a context assembled for a launch that never happened. Nothing crashed, so nothing was noticed — but every message paid for the whole transcript being loaded and compacted for nothing, the gauge was zeroed against a session that never restarted, and the persisted session pointers were cleared and rewritten each turn. That turn was in neither case: cold start by its context, reuse by its execution.

`_cli_require_delta_context` raises `DeltaContextRequired` at every provider's reuse site, `_alc_llm_turn` rebuilds with `_prepare_agent_context(..., force_delta=True)` — case 2 through the ordinary path — and runs the turn again. Same shape as its mirror: the marker is flipped so the rebuilt turn is not bounced straight back, the caller's `release` hook gives back the live turn lock before the raise, and it fires at most once per turn.

`force_cold` and `force_delta` are callers, not extra states. Each is a turn that already knows which case it is in *because the provider observed it at launch time*, which outranks anything the context phase's probe concluded a second earlier. That is also why neither one re-runs the probe: it is precisely the probe's answer that was wrong.

**This is one rule, and every CLI obeys it — there is no per-provider variation.** Each provider asks at its own launch site, because that is where "we are about to start a process" is known, but the question and the answer are the same everywhere:

| provider | where it asks | what it gives back on refusal |
|---|---|---|
| codex app-server | before minting its MCP token, on the not-reuse path | the live session's `turn_lock` |
| gemini ACP | on the not-reuse path | the live session's `turn_lock` |
| claude-code interactive | `InteractiveClaudeCodePool.ensure_started(before_launch=…)`, before a credential slot is claimed | nothing is taken yet |
| antigravity interactive | `AntigravityObserverPool.ensure_started(before_launch=…)`, before the stale session is killed | nothing is taken yet |
| claude-code (`-p`) | on the not-reuse path, before spawning | nothing is taken yet |

claude-code (`-p`) used to have a *third* path, and it was the last provider that did: no live process, but a persisted session id, so it launched with `--resume` and a delta and let CC replay its own jsonl. That path is gone. Whether the jsonl still meant anything was decided by re-deriving CC's project-key algorithm and trusting a file only CC can validate; when CC declined it and started a fresh session instead, the `SESSION MISMATCH` check downstream merely logged it while the agent silently lost its history — and the stored id was then persisted as though it were sound, so every later turn resumed an empty session. There are two cases, and replaying a transcript from disk is not one of them: no live process means launch, and launch means the full PawFlow context, which is compacted, is ours, and does not depend on a file we cannot validate. `_cc_project_key` survives only to LOCATE a live session's jsonl for the preempt check.

The pools take a `before_launch` callback rather than asking themselves: they manage containers, not context policy, and they call it only when they are really going to launch — never on the reuse path, because a reused session's delta is correct and restarting that turn would be gratuitous.

An ephemeral call (compact, memory extraction) is exempt everywhere: it builds its own full text, but it clones a client that may carry the marker.

`force_cold` is a third *caller*, not a third state: the turn already knows it is going to launch, so probing again could only answer "warm" and strip the context that launch needs.

Five invariants keep this honest.

**The delta marker belongs to the context, not to the service.** The client the context phase holds comes from the service registry and is shared by every conversation using that service, so a marker written there is read — or cleared — by whichever turn reaches it next, and the isolated clone is only made later, in `_alc_setup`. `_mark_context_as_delta` therefore records it on the build's state, `_prepare_agent_context` returns it as `_context_is_delta`, and `_alc_setup` stamps it on this turn's own clone. Nothing writes it on the shared client, so nothing has to remember to clear it either.

**The refusal gives back what the turn already took.** Both providers hold the live session's `turn_lock` when they ask, and their own `try`/`finally` has not started yet, so `_cli_require_cold_context` takes a `release` callback and calls it just before raising. The lock is an `RLock` and the retry runs on the same thread, which is what makes a leak invisible: the second acquisition succeeds, one `finally` releases one level, and the next turn on that session — on another thread — waits for a turn that ended long ago. Codex asks *before* minting its MCP token for the same reason. An ephemeral call is exempt: it builds its own full text, but it clones a client that may carry the marker, and bouncing it would restart a compact or a memory extraction as if it were the agent's own turn.

**The rebuild is adopted whole.** A rebuilt context brings its own client, tool registry, tool definitions and message list, so `_alc_rebind_context` rebinds all of them — including the clone boundary and the cancel/preempt registration — rather than patching a few fields. A loop left half on the old context executes tools through a registry the new context never configured, and force-stop reaches the clone of the context that was abandoned. The message list is replaced *in place*: `ctx`, the emitter and every closure built at setup hold that exact object. The turn keeps its own identity through the restart — iteration count, token totals, tools already called, its `/rewind` checkpoint.

**The restart is control flow, not work.** The iteration is counted before the provider is called, so the restart gives it back. Otherwise a turn with `max_iterations=1` ends there, having never called the model — and CLI providers deliberately synthesize no empty answer.

**The cancel checkpoint survives.** It is consumed on injection and is *not* cold-gated, so the rebuild is handed back what the first pass ate; otherwise a rebuilt turn silently loses its "continue where you left off" instruction.

The gauge reset sits *outside* the live-probe block for the same reason `force_cold` skips that probe: the pass that knows it is launching is exactly the pass whose stored gauge describes a session that no longer exists.

**A lookup is a use.** The idle sweeper reaps containers nobody asks for, and `last_used` is its only evidence, so every registry lookup that hands a container to a caller refreshes it (`CodexLiveRegistry`/`GeminiLiveRegistry.get`/`get_compatible`, `LiveSessionRegistry.get`/`find_for_agent`, and the CCI and Antigravity pools' `find_session`). Without that, a session at the end of its TTL could be found alive by the context phase and swept a tick later, before the provider claimed it. The TTL is unchanged: a session nobody asks for is still reaped on schedule, and an active turn is never reaped at all.

**The serialization is decided by the lookup, never by a stored id.** In `_cc_stream`, the full-context / delta branch runs *after* the live lookup and reads `st._is_reuse` only. `claude_session:<agent>` outlives the process it describes — a server restart leaves the extras behind — so a branch taken on that id built a delta, and the launch two blocks later handed it to a process holding nothing. The stored id still picks the credential slot to resume on; it decides nothing about what is sent.

**A credential slot is taken by a launch, never by a lookup.** `_setup_credentials` is a write — `.credentials.json` in the workdir, `self._current_pool_index`, and the persisted `claude_pool_idx:<agent>` — so it runs in the launch branch only, after the live lookup, exactly as `_codex_setup_credentials` and `_gemini_setup_credentials` do. The lookup asks about the *stored* slot; a reuse then adopts the live session's slot onto the client and writes nothing, because that process authenticated at spawn time and is still running on that account. Taking a slot first meant an adoption on another slot realigned the key, the local index and the extra while the client and the file stayed behind: `_recover_tokens` wrote one account's rotated tokens into another account's slot, an auth error refreshed or excluded the wrong credential, and the registry sweeper attributed the workdir to a third. On a multi-account pool that disconnects accounts the turn never touched.

**Both phases ask with the same inputs.** The context phase cannot know which credential slot `_setup_credentials` will pick, so it asks without one (`find_for_agent`). The provider therefore falls back to the same pool-agnostic lookup (`LiveSessionRegistry.get_compatible`) when its exact slot misses, and adopts the session *with the key it lives under* so `touch`/`evict`/`register` address the entry that exists. A provider that only ever asked for its own slot answered "cold" where the context phase had answered "warm", orphaned the live process and paid a cold retry every turn.

**A refusal still destroys what it refused to reuse.** `AntigravityObserverPool.ensure_started` drops an unusable session from the registry before it calls `before_launch`, so a refusal that skipped the kill left a live container nobody tracked and the cold retry started a second one beside it. `find_session` calls a session warm on the container alone while `ensure_started` also wants the proxy journal ready, so "unusable but alive" is an ordinary state, not a corner case. The kill is in a `finally`.

**A kill only unregisters what it is still holding.** That eviction-then-kill order leaves the key free while `before_launch` and the relaunch run, and the cold retry is precisely what fills it — so `AntigravityObserverPool.kill` pops the registry entry only when it is still identical to the session being killed. An unconditional pop dropped the *replacement* instead: a container running, no longer tracked, and therefore never reaped — the same orphan the `finally` above exists to prevent, reintroduced at the other end.

**A rotated token is rescued on a clock, not on a teardown.** Every CLI container refreshes its own OAuth token in-place, and Anthropic's `refresh_token` is single-use: the copy back to the pool slot is the only thing standing between a rotation and a logout on the next turn. Teardown used to be the only moment it happened, which made it depend on the server being alive to perform it — a hard kill, or an update whose Docker stop grace (10s by default) expires while `docker rm -f` works through the containers at up to 15s each, and the token dies with the container. So every live pool now also copies back on each sweeper tick, for the sessions it is *not* evicting, and `shutdown_all` takes every token before it kills anything. `recover_tokens_from_workdir` is idempotent per (workdir, slot) through the shared memo in `cli_shared` (`token_recovery_is_stale` / `note_token_recovered`), so a tick where nothing rotated writes nothing — codex keys its signature on the three values `auth.json` carries, never on the expiry it stamps fresh at each call.

**One lock owns the whole load/mutate/save cycle, and the memo means "it landed".** A pool update reads the entire pool, edits one slot and writes the whole pool back — into `GLOBAL_SECRETS_FILE`, itself read and rewritten whole, key by key. The writers are genuinely concurrent: the three sweepers tick independently, a refresh lands mid-tick, a login writes from another thread. Interleaved, the loser's snapshot predates the winner's edit, so saving it restores the *other* slot's previous token — already dead, for Anthropic, and the account is logged out for good once the container is gone. `cli_shared.credentials_pool_lock()` is therefore taken across load, mutate and save by every writer in all three providers (`_persist_tokens_to_service`, `recover_tokens_from_workdir`, `add_credential_to_pool`, `remove_credential_from_pool`). It is one lock for all of them on purpose — they collide on the secrets file, not on their own pool key — and reentrant, because a persist can fall through to `add_credential_to_pool`. `_persist_tokens_to_service` returns whether it wrote, and `note_token_recovered` is called only on `True`: a memo recorded for a write that never happened makes every later tick skip the slot, so the one failed copy is never retried.

**A container still up at boot is a zombie, and boot reaps it.** Nothing PawFlow starts may outlive it, so nothing is ever adopted back: the pools track sessions in memory only, and a surviving container would be invisible — never swept, never reaped, never recovered from — while it holds a credential slot and keeps writing into a session workdir the new process believes it owns. `core.docker_utils.reap_spawned_containers` is therefore called at *both* ends of the process, and it is deliberately the same function: shutdown keeps the promise, and boot keeps it on shutdown's behalf when the shutdown never ran (SIGKILL, a crash, an update whose stop grace expired). It runs before task registration, flow restore and boot-recovery — before anything can spawn a container or claim a slot.

**The label decides, and nothing else does.** Selection is one `docker ps --filter label=org.pawflow.server-id=<own>`, stamped at the spawn by `pawflow_container_labels`. Two wrong answers were tried before this one. Reaping by `pf-<server-id>-*` missed whole families, because the batch pools are named `pf-cc-pool-*` and the relays and logins `pawflow-*`. Widening to those generic prefixes was worse: they carry no server id, so on a shared Docker daemon the name match also selects another **live** PawFlow server's pools, relays and logins — and an unlabelled container is not an old build of ours to clean up, it is something this server did not start. Every family that spawns goes through `pawflow_container_labels`, so the label pass alone loses no coverage. Removals are counted from what `docker rm` echoes back, not from what was requested: a refusal is a non-zero exit and a line on stderr, never an exception, and counting the request would report a clean sweep while the zombie is still up holding its slot.

The residual window is what a zombie rotated between the last sweeper tick and its death — at most one tick, since the periodic copy-back above no longer waits for teardown.

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

#### What the user sees

The meta-tools are a transport, not a display: every tool call the model makes
gets its own row, under the name of the tool that actually ran. `use_tool` and
`get_tool_schema` wrappers are unwrapped on both the live SSE path
(`tasks/ai/_alc_closures1.py`) and the reload path
(`tasks/ai/agent_serialization.py`), so the wrapper name never reaches the
chat — and no call is hidden. `get_tool_schema` used to be filtered out of
both: the call row vanished and its result row lost its name, leaving an
anonymous output attached to nothing.

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

#### Who emits `token`

`StreamEmitter.get_token_callback` — the callback every provider already
feeds, so the answer appears while it is written whatever is behind it. The
granularity is the provider's, not a setting: the API providers (anthropic,
openai, openai-responses, gemini, codex-app-server) deliver real deltas, while
the CLI ones deliver whole blocks (Claude Code 1.0+ sends complete `assistant`
events, with no `content_block_delta` to forward). Progressive either way,
word-by-word only for the first group.

The id it publishes is `_current_msg_id` — the same one `_alc_append` stamps
on the durable message, which is what lets the client replace its preview
instead of showing the answer twice. `_alc_append` rotates that id at every
persisted block, so one turn can carry several: the client starts a new bubble
when the id changes, or the blocks would merge live and split apart again on
reload.

A silent poll (`poll_silent`) publishes nothing. Its text may end in
`NO_PENDING_WORK` and never be persisted, and a preview of that is an answer
on screen belonging to nothing.

#### `message_meta` is an update, not an announcement

The client looks the message up by `msg_id` and **replaces** its meta line.
That matters for turns whose numbers are not known when the text is written —
a captured tmux turn (see `CLAUDE_CODE_INTERACTIVE.md`) persists its answer as
it arrives and only learns the model and the token counts when the coordinator
returns, so it sends `message_meta` then to complete what it already wrote.
A meta line left half-empty is worse than absent: it reads as a turn that cost
nothing.

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

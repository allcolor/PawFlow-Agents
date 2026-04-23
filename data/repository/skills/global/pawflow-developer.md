---
description: Complete development guide for contributing to PawFlow — architecture,
  patterns, conventions, and key subsystems.
---

# PawFlow Developer Guide

You are working on PawFlow, a self-hosted AI agent orchestration platform (~144K lines Python). This skill gives you all the context needed to make correct, consistent changes.

## Architecture Overview

PawFlow has two major subsystems:
1. **Pipeline engine** — NiFi-inspired flow-based processing (FlowFile, Task, Service, Flow)
2. **Multi-agent system** — LLM agents with tool-use loops, streaming SSE, memory, and multi-provider support

Both share core/ primitives but operate independently.

## Directory Layout

```
core/                       # Shared engine: FlowFile, abstractions, stores, LLM client
  handlers/                 # 35+ tool handlers (one class per tool)
  llm_providers/            # Provider mixins: anthropic, openai, claude_code, gemini_cli
  __init__.py               # FlowFile, Task, Service, Flow, TaskFactory, ServiceFactory
  agent_executor.py         # SubAgentExecutor + resolve_agent_task (delegate/sub-agents)
  conversation_store.py     # JSONL append-only persistence (per-conversation files)
  resource_store.py         # CRUD for agents, skills, MCP servers, prompts, task_defs
  tool_registry.py          # ToolHandler interface, ToolRegistry, tool discovery
  llm_client.py             # Unified LLM HTTP client (all providers), zero deps
  expression.py             # ${scope.key} expression language (40+ operations)
  poll_scheduler.py         # Persistent scheduler for agent rechecks (survives restart)
  conversation_event_bus.py # Pub/sub SSE events by conversation_id
  cost_tracker.py           # Per-model token/cost accounting
  config_store.py           # Encrypted secrets, variables, config
  file_store.py             # Binary attachment storage (SHA-based dedup)
tasks/
  ai/                       # Agent system (mixin-based decomposition)
    agent_loop.py           # AgentLoopTask — main composite task
    agent_context.py        # System prompt building, skill injection, MCP loading
    agent_core.py           # Unified execution loop (_run_agent_loop)
    agent_streaming.py      # Thread spawning, SSE streaming
    agent_compaction.py     # Context window management, summarization
    agent_poller.py         # Background poller for scheduled tasks/rechecks
    agent_tool_config.py    # Tool filtering, handler wiring
    agent_tool_exec.py      # Tool execution dispatch
    agent_actions.py        # Action router to actions/ sub-modules
    agent_identity.py       # Nickname resolution, agent identity
    actions/                # 17 action modules (conversation, resources, scheduling...)
  io/                       # HTTP receiver, Telegram, Discord, WhatsApp
  system/                   # Log, wait, script, route, merge
services/                   # 41 services (filesystem relay, auth gateway, browser, media...)
engine/                     # Pipeline executor (batch, continuous, CRON, debugger)
api/                        # FastAPI REST API (10 routers, 100+ endpoints)
gui/                        # Streamlit admin UI
config/                     # JSON config files (agents, skills, secrets, sessions...)
```

## Key Patterns

### Singletons
Most stores use thread-safe singletons: ConversationStore.instance(), ResourceStore.instance(), PollScheduler.instance(), ConversationEventBus.instance(), CostTracker.instance(). Always use .instance(), never instantiate directly.

### ConversationStore (JSONL)
Conversations are .jsonl files in data/conversations/. Line types:
- `{"t":"meta", ...}` — metadata (user_id, status, created_at)
- `{"t":"msg", "role":"...", "content":"...", ...}` — messages
- `{"t":"ctx", "agent":"name", "op":"replace|append", "data":[...]}` — diverged agent context
- `{"t":"extra", "key":"...", "value":...}` — arbitrary key-value (active_resources, agent_tasks, etc.)

get_extra(cid, key) / set_extra(cid, key, value) — the escape hatch for per-conversation state.

### ResourceStore
5 resource types: agent, skill, mcp, prompt, task_def. Each stored in its own JSON file under config/. Keys namespaced by user_id ("uid.name"). Global resources use __global__ as uid. Resolution order: conversation-scoped → user-scoped → global.

### Agent system (mixin architecture)
AgentLoopTask is composed of ~12 mixins in tasks/ai/:
- AgentContextMixin — builds system prompt, resolves agent def, injects skills/MCP tools
- AgentCoreLoopMixin — the actual LLM ↔ tool execution loop
- AgentStreamingMixin — thread management, SSE emission
- AgentCompactionMixin — context window summarization
- AgentPollerMixin — background scheduled execution
- AgentToolConfigMixin — handler wiring (set_user_id, set_conversation_id on each handler)
- AgentToolExecMixin — tool call dispatch and result handling
- AgentActionsMixin — routes /command and UI actions to actions/ modules

### Tool handlers
Each tool is a ToolHandler subclass in core/handlers/. Pattern:
- name property returns the tool name
- description property returns the description
- parameters_schema property returns JSON Schema
- execute(arguments) method returns str

Handlers can receive context via set_user_id(), set_conversation_id(), set_agent_name(). These are called by AgentToolConfigMixin before each agent run. Register in core/tool_registry.py → create_default_registry().

### LLM providers
LLMClient in core/llm_client.py unifies all providers via mixins in core/llm_providers/:
- LLMAnthropicMixin — direct Anthropic API (streaming, tools, vision, thinking, cache)
- LLMOpenaiMixin — OpenAI-compatible API
- LLMClaudeCodeMixin — Claude Code subprocess with MCP bridge
- LLMGeminiCliMixin — Gemini CLI subprocess

Provider is selected by LLM service config, not hardcoded. Services are resolved via _resolve_llm_service(svc_id, uid).

### Expression language
`${scope.key}` — resolved by core/expression.py. Scopes: secrets, variables, env, flow params. Used in agent configs, service configs, MCP definitions. Always resolve via resolve_value().

### Skills model
- Skills are assigned to agents via agent_def["assigned_skills"]
- In main conv: agent_context.py reads agent_def.assigned_skills and injects skill prompts into system prompt
- In task sub-conv: reads task_data["skills"] from the task_def (not agent's own)
- In delegate: only explicitly passed extra_skills (no fallback)
- Skills are NOT on conversations

### Task system (autonomous agent tasks)
assign_task creates a recurring autonomous task:
- Stored in agent_tasks extra on parent conversation
- Runs in isolated sub-conversation ({parent_cid}::task::{task_id})
- Scheduled via PollScheduler with configurable interval
- Has its own skills, timeout, budget limits
- Agent reports progress via complete_task tool

### SSE event flow
Agent loop calls ConversationEventBus.publish_event(cid, type, data) which forwards to SSEWriter → HTTP SSE stream → webchat UI. Event types: thinking, token, tool_use, tool_result, message, error, task_progress, etc.

### Relay system (Docker sandboxing)
Tool execution (bash, file ops, screen) can run on the host or in Docker containers via relay:
- services/filesystem_service.py — WS listener, relays connect to it
- tools/pawflow_relay.py — relay script running in Docker or on host
- Each relay has an ID (e.g. fs_project_abc), linked to conversations
- Tool handlers use relay parameter to route execution

### Actions system
UI actions (from webchat buttons, commands) are dispatched through agent_actions.py → actions/ modules. Each module handles a domain: conversations, resources, scheduling, media, etc. Actions receive (self, flowfile, body, store, user_id) and return [FlowFile].

## Development Rules

1. All stores are singletons — use .instance(), never __init__()
2. JSONL is append-only — never rewrite conversation files, only append
3. Thread safety — agent loops run in threads, use locks for shared state
4. No circular imports — handler base class is in core/tool_handler.py, not tool_registry.py
5. Expression resolution — always use resolve_value() for config values that might contain ${...}
6. LLM service routing — never hardcode provider/model, always go through service resolution
7. SSE events on parent conv — task sub-convs publish events on the parent conversation_id so the UI sees them
8. Tool results are strings — execute() returns str, complex data is JSON-serialized
9. Handlers are stateful per-run — set_user_id/set_conversation_id are called before each agent loop iteration
---
name: pawflow-developer
description: "Complete development guide for contributing to PawFlow: architecture, patterns, conventions, and key subsystems."
---

# PawFlow Developer Guide

You are working on PawFlow, a self-hosted AI agent orchestration platform (~144K lines Python). This skill gives you the context needed to make correct, consistent changes.

## Architecture Overview

PawFlow has two major subsystems:

1. Pipeline engine: NiFi-inspired flow-based processing with FlowFile, Task, Service, and Flow.
2. Multi-agent system: LLM agents with tool-use loops, streaming SSE, memory, and multi-provider support.

Both share `core/` primitives but operate independently.

## Directory Layout

```text
core/                       # Shared engine: FlowFile, abstractions, stores, LLM client
  handlers/                 # Tool handlers, one class per tool
  llm_providers/            # Anthropic, OpenAI, Claude Code, Gemini CLI provider mixins
  agent_executor.py         # SubAgentExecutor and delegate/sub-agent resolution
  conversation_store.py     # JSONL append-only persistence
  resource_store.py         # CRUD facade for repository resources
  tool_registry.py          # ToolHandler interface, ToolRegistry, tool discovery
  llm_client.py             # Unified LLM HTTP client
  expression.py             # ${scope.key} expression language
tasks/
  ai/                       # Agent system mixins and actions
  io/                       # HTTP receiver and chat UI
  system/                   # System tasks
services/                   # Filesystem relay, auth, browser, media, and provider services
engine/                     # Pipeline executor, scheduler, validator, debugger
docs/                       # Project documentation
tests/                      # Unit and static tests
```

## Key Patterns

Most stores use thread-safe singletons: `ConversationStore.instance()`, `ResourceStore.instance()`, `PollScheduler.instance()`, `ConversationEventBus.instance()`, and `UsageLedger.instance()`. Use `.instance()`, not direct construction.

Conversations are JSONL append-only files. Append new records instead of rewriting history. Every message must have a UUID and timestamp when created.

Tool execution goes through relay-backed handlers. Filesystem, shell, browser, desktop, and media actions must route through the relay/tool layer, not direct server filesystem access.

LLM providers are selected by service config. Do not hardcode provider/model behavior when a service resolution path exists.

Expression values use `resolve_value()` or `resolve_expression()` when a field may contain `${...}`.

## Agent System

`AgentLoopTask` is composed from mixins in `tasks/ai/`:

- `AgentContextMixin` builds system prompts and injects available skill manifests.
- `AgentCoreLoopMixin` runs the LLM/tool loop.
- `AgentStreamingMixin` manages threads and SSE.
- `AgentCompactionMixin` handles context-window summarization.
- `AgentPollerMixin` handles scheduled wake-ups.
- `AgentToolConfigMixin` wires handlers and context.
- `AgentToolExecMixin` dispatches tool calls.
- `AgentActionsMixin` routes UI and command actions.

## Skills

Skills are standard Agent Skills directories. Each skill is a directory containing `SKILL.md` with YAML frontmatter and Markdown instructions. PawFlow advertises assigned skills as lightweight manifests and loads full instructions only through `load_skill` or explicit `//skill-name` invocation.

Keep skills portable. Do not depend on PawFlow-specific templating or parameter interpolation inside `SKILL.md`. Put UI-only hints under `metadata.pawflow.*` if needed, and let the agent interpret user arguments from the request.

## Development Rules

1. All documentation and code comments must be in English.
2. Add or update focused tests for new behavior.
3. Update relevant docs for new handlers, tasks, services, tools, commands, or user-visible behavior.
4. Keep changes surgical and match local style.
5. No anonymous/default fallbacks for required params; missing required params should fail clearly.
6. Force stop is immediate kill, not an error, and must not poison the next loop.
7. Actions are async and must not block UI or HTTP workers.

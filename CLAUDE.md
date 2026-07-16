# PawFlow — Development Context

## What is PawFlow

PawFlow is a self-hosted AI agent orchestration platform inspired by Apache NiFi.
It combines a data flow engine (FlowFile/Task/Service/Flow) with an LLM agent system
that supports tool-use loops, multi-agent conversations, and streaming SSE.

## Architecture

- **Core**: FlowFile (data unit), Task (processor), Service (connection), Flow (DAG)
- **Engine**: FlowExecutor (batch) + ContinuousFlowExecutor (queues/backpressure)
- **Agents**: AgentLoopTask with tool-use loop, multi-agent, streaming SSE
- **Auth**: AuthGatewayService with 9 OAuth providers
- **Storage**: ConversationStore (directory-based), MemoryStore, KnowledgeGraph, AgentDiary
- **Relay**: WebSocket reverse-tunnel for remote filesystem + tool execution
- **CLI**: PawCode (Claude Code drop-in replacement)

## Key Conventions

- All documentation and code comments MUST be in English
- ALWAYS document new features, tools, tasks, services, and handlers — update the relevant file under `docs/` in the same change
- ALWAYS write unit tests for new code — handlers, tasks, services, parsers; nothing ships without coverage
- Zero backward compatibility — migration is one-shot, delete old code
- No "anonymous" or "default" fallbacks — missing required params = ValueError
- Every message MUST have UUID + timestamp at creation
- selectedAgent is NEVER empty in a conversation
- Force stop = immediate kill, NOT an error, NEVER affects the next loop
- All actions are async — nothing blocks UI or HTTP worker
- Bugs are always our code — never blame cache/OS/libs first

## Expression Language

`${scope.key:op1:op2("arg")}` — 40+ chainable operations.
Resolution cascade: flow → conversation → user → global.

## Tool Architecture

All filesystem tools extend BaseFsHandler and route through the relay service.
Tools execute in the relay Docker container by default, on the host if `local=true`.
No tool accesses the server filesystem directly (except internal storage: memories, KG, diary, graphs).

## Cognitive Tools (4 systems)

- **Memory**: category taxonomy, scopes, temporal validity
- **Knowledge Graph**: temporal entity-relationship triples, BFS/DFS, communities
- **Agent Diary**: per-agent personal journal
- **Project Graph**: AST extraction via tree-sitter (17 languages), built via relay
- **Skill loop** (procedural layer): agents crystallize procedures into skills via `manage_resource`; drafts proposed post-compaction (`core/skill_loop.py`), usage stats (`core/skill_stats.py`), `skillCurator` report task (see `docs/LEARNING_LOOP_PLAN.md`)

## Key Directories

- `core/` — Core abstractions (FlowFile, Task, Service, stores)
- `tasks/` — Task implementations (system, io, data, control, ai)
- `services/` — Service implementations (filesystem, LLM, relay, auth)
- `config/` — Configuration files (agents, parameters, task definitions)
- `data/` — Runtime data (conversations, memories, KG, graphs)
- `docs/` — Documentation
- `tests/` — Unit tests
- `docker/` — Docker configurations (relay, Claude Code)
- `pawflow_cli/` — PawCode CLI
- `tools/` — Relay implementations

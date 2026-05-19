# PawFlow Project Summary — Current State

**Last updated**: 2026-04-27  
**Package version**: `1.0.0a1`  
**Status**: functional alpha, APIs may still evolve

## Overview

PawFlow is no longer a simple workflow-engine MVP. The repository now hosts a self-hosted AI agent and pipeline orchestration platform positioned as **"Apache NiFi meets Claude Code"**: a PawFlow server, a DAG flow engine, a multi-provider agent system, a local relay for filesystem/tool access, a web UI, a PawCode terminal client, a VS Code extension, documentation, and a substantial test suite.

The current core value is twofold:

1. **Tool-equipped autonomous agents**: multi-agent conversations, multiple LLM providers, tool-use loop, persistent memory, knowledge graph, agent diary, project graph, plans, delegation, streaming.
2. **Pipeline engine**: DAG execution over FlowFiles, task catalog, triggers, backpressure, checkpoints, crash recovery, provenance, and IO/data/control integrations.

## What lives in the repository

### Python core and runtime

- `core/`: agent runtime and main primitives.
  - agent execution and tool-use loops;
  - LLM providers (`Claude Code`, `Codex CLI`, `Gemini CLI`, Anthropic API, OpenAI API, OpenAI-compatible endpoints);
  - memory, knowledge graph, diary, project graph;
  - conversation, plan, token, file, relay, and tool-handler management;
  - storage backends and security/context helpers.

- `engine/`: flow engine.
  - JSON flow parsing and validation;
  - DAG execution;
  - checkpoints, crash recovery, triggers, provenance;
  - workers, scheduler, debugger, NiFi import, cluster support.

- `tasks/`: PawFlow task catalog.
  - `system/`: log, wait, fail, replace text, hash, scripts, cron trigger, FlowFile generation/listing, reporting;
  - `io/`: HTTP, files, SFTP/FTP, S3, GCS, Azure, Kafka, MQTT, email, Slack, Discord, Telegram, WhatsApp, web UI, relay, auth/session;
  - `data/`: JSON, XML, CSV, SQL, text extraction, transformations, compression, Avro/Parquet, base64, cache, deduplication;
  - `control/`: routing, split/merge, rate limiting, ports, stop flow, execute flow, wait/notify;
  - `ai/`: agent loop and agent-execution modules.

- `services/`: integration services and proxies.
  - authentication and OAuth providers;
  - filesystem, terminal, browser, relay, gateway;
  - media/image/audio/video, voice, 3D, desktop/browser, and Pixazo services;
  - messaging and storage integrations.

### Interfaces and clients

- `cli.py`: historical CLI and `pawflow` entry point declared in `pyproject.toml`.
  - run/validate/list/info commands;
  - API/UI startup;
  - import, triggers, cluster, memory re-embedding.

- `pawflow_cli/`: **PawCode**, a Claude Code-style terminal client.
  - interactive mode;
  - stream-JSON compatibility;
  - automatic working-directory relay;
  - terminal, context, file, and agent commands.

- `pawflow_relay/`: local/host relay.
  - exposes files, shell commands, and tools to the server via WebSocket;
  - lets the server act on the user's machine without direct filesystem access.

- `pawflow-vscode/`: TypeScript VS Code extension.
  - PawFlow chat inside VS Code;
  - embedded relay;
  - selection-aware commands and project context.

- `static/`, `pawflow-website/`, and `serve_*` tasks: web UI, assets, and static presentation site.

### Documentation

`docs/` covers:

- internal architecture;
- agent system;
- cognitive tools: memory, KG, diary, project graph;
- expression language;
- slash commands;
- task catalog;
- Docker/local deployment;
- relay filesystem;
- HTTP listener, provenance, Pixazo, voice clone;
- task/service development.

The `README.md` is now a better reflection of the vision and current state than the previous project summary.

## Repository figures

These numbers describe the repository state as of 2026-04-27 without deeper functional interpretation:

| Area | Observed volume |
|---|---:|
| Python files in `core/` | 159 |
| Python files in `engine/` | 20 |
| Python files in `tasks/` | 131 |
| Python files in `services/` | 63 |
| Test files `tests/test_*.py` | 128 |
| Documents in `docs/` | 19 |

The README also advertises:

- 100+ task types in the catalog;
- 90+ built-in tools;
- 60+ slash commands in the web chat;
- 9 OAuth providers;
- 4000+ tests.

## Key implemented or present features

### AI Agents

- Agent conversations with streaming.
- Tool-use loop and tool execution via the relay.
- Multi-agent and delegation.
- Structured plans with steps, assignment, and verification.
- Persistent memory, semantic recall, knowledge graph, agent diary.
- AST/tree-sitter-based project graph.
- Multiple LLM providers and an OpenAI-compatible endpoint.
- Permission modes and tool-access control per configuration.

### Pipelines

- JSON flows executed as DAGs.
- FlowFiles, relations, parameters, and runtime context.
- Backpressure, checkpoints, crash recovery.
- CRON, file watcher, webhook/polling/event triggers per available modules.
- Subflows, parameter mapping, and NiFi import.
- Flow debugger, provenance, versioning, and cluster mode.

### Tools and relay

- File read/write/edit.
- Bash/terminal via relay.
- File/content search.
- Web fetch/scraping.
- Image, video, audio, voice, 3D, upscale, try-on, and lipsync generation per configured providers.
- Desktop/screen/browser automation via relay/VNC per configuration.
- Security scanning and script execution.
- Secret, resource, memory, KG, and plan management.

### User interfaces

- Web chat with SSE, files, context, slash commands, `/desktop`, and conversation management.
- PawCode CLI for terminal use.
- VS Code extension.
- Conversations shared across web, CLI, VS Code, API/channels, and flows.
- Static presentation site.

### Authentication and deployment

- Username/password and OAuth authentication.
- JWT/API keys/RBAC per available modules.
- Local and Docker deployment.
- Docker or native relay.

## Strengths

1. **Coherent product ambition**: PawFlow combines autonomous agents and a pipeline engine instead of staying a thin LLM wrapper.
2. **Modular architecture**: clear separation between agent core, engine, tasks, services, relay, and clients.
3. **Broad integration surface**: files, shell, web, messaging, cloud storage, databases, media, OAuth.
4. **Credible self-hosted approach**: the relay avoids granting the server permanent direct access to the user's filesystem.
5. **Agent continuity tooling**: memory, KG, diary, plans, and project graph go beyond stateless chat.
6. **Meaningful test coverage**: the repository ships a real pytest suite, not just a demo script.

## Watch-outs

- The project is explicitly in **alpha**: the public API, JSON formats, and internal contracts may still shift.
- Documentation freshness is uneven. Some older documents still describe an MVP-era state.
- The functional surface is very wide: distinguish between modules that are present, paths that are tested, and integrations actually validated in production.
- Some capabilities depend on secrets, external providers, an active relay, or a properly configured Docker/local environment.
- High-level README counts are useful but must stay in sync with the real catalog and tests.

## Current roadmap

Per `ROADMAP.md`, the next major directions are:

- push-to-talk voice input;
- Git worktree isolation for parallel agents;
- additional LLM providers: Ollama, Mistral, vLLM, LM Studio, Together.ai;
- MCP elicitation and exposing PawFlow as an MCP server;
- mobile PWA client;
- full visual flow editor;
- installation wizard;
- headless JSON mode;
- marketplace for agents/skills/tools/MCP/tasks/flows.

## Conclusion

PawFlow has moved from a base architecture to a complete agentic platform in alpha. The project summary therefore must present it as an integrated system: **server + agents + flow engine + relay + clients + documentation + tests**.

The old "4 implemented tasks / 0 service / 1 test script" framing is obsolete. The current accurate reading is: an already substantial product with a rich architecture and many modules in place, but one that still has to stabilize its contracts, clarify what is production-ready, and keep its documentation in sync with the code.

# PawFlow Project Summary — Current State

**Last updated**: 2026-09-03
**Package version**: `1.0.0b263` (beta.263)

**Status**: functional beta, remaining API changes before 1.0.0 expected to be minor

## Overview

PawFlow is no longer a simple workflow-engine MVP. The repository now hosts a self-hosted AI agent and pipeline orchestration platform positioned as **"Apache NiFi meets Claude Code"**: a PawFlow server, a DAG flow engine, a multi-provider agent system, a local relay for filesystem/tool access, a web UI, a PawCode terminal client, a VS Code extension, documentation, and a substantial test suite.

The current core value is twofold:

1. **Tool-equipped autonomous agents**: multi-agent conversations, multiple LLM
   providers, tool-use loop, persistent memory, knowledge graph, agent diary,
   relay-scoped project graph/wiki, todo and scratchpad work state, plans,
   delegation, streaming.
2. **Pipeline engine**: DAG execution over FlowFiles, task catalog, triggers, backpressure, checkpoints, crash recovery, provenance, and IO/data/control integrations.

## beta.263 implementation highlights

- Added the outbound `acp` provider for explicitly configured Agent Client
  Protocol v1 processes, including warm-session reuse, cancellation, policy
  checks, and opt-in PawFlow MCP/client filesystem capabilities.
- Added managed native-hook CLI providers `cc_mcp` and `codex_mcp`, then after
  beta.263 completed `agy_mcp` using Agy's native
  `StopHookArgs.finalModelOutput` field and the existing Antigravity managed
  pool; it is now selectable without vendor-traffic interception.
- Made delegate observability restart-durable, fixed delegate-reply routing and
  late preempt work after force stop, and kept terminal provider failures visible
  in webchat.
- Made over-quota ScratchDirs recoverable, completed the relay filesystem module
  manifests, and accelerated large plaintext history search and incremental
  conversation indexing.

## What lives in the repository

### Python core and runtime

- `core/`: agent runtime and main primitives.
  - agent execution and tool-use loops;
  - LLM providers: Anthropic API, OpenAI Chat Completions and Responses,
    OpenAI-compatible endpoints, OmniRoute, Gemini CLI, outbound ACP v1 agents,
    the CLI-backed subscription providers `claude-code-interactive`,
    `codex-interactive`, and `antigravity-interactive`, and the managed
    native-hook variants `cc_mcp`, `codex_mcp`, and `agy_mcp`, while legacy
    `claude-code` (`cc -p`) and
    `codex-app-server` remain only for existing configurations;
  - memory, knowledge graph, diary, project graph/wiki, todo, and scratchpad;
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
- cognitive/work-state tools: memory, KG, diary, todo, scratchpad, project
  graph, and project wiki;
- expression language;
- slash commands;
- task catalog;
- Docker/local deployment;
- relay filesystem;
- HTTP listener, provenance, Pixazo, voice clone;
- task/service development.

The `README.md` is now a better reflection of the vision and current state than the previous project summary.

## Repository figures

These numbers describe the repository state as of 2026-07-31 without deeper functional interpretation:

| Area | Observed volume |
|---|---:|
| Python files in `core/` | 318 |
| Python files in `engine/` | 20 |
| Python files in `tasks/` | 199 |
| Python files in `services/` | 117 |
| Test files `tests/test_*.py` | 361 |
| Documents in `docs/` | 68 |

The README also advertises:

- 100+ task types in the catalog;
- 90+ built-in tools;
- 60+ slash commands in the web chat;
- 9 OAuth providers;
- 7000+ tests.

## Key implemented or present features

### AI Agents

- Agent conversations with streaming.
- Tool-use loop and tool execution via the relay.
- Multi-agent and delegation.
- Structured plans with steps, assignment, and verification.
- Persistent memory, semantic recall, knowledge graph, and agent diary.
- Durable todo state and expiring pull-only scratchpad notes.
- Relay-scoped AST/tree-sitter project graph and sourced project wiki.
- Direct API, interactive CLI, managed native-hook CLI, OmniRoute, and outbound
  ACP providers, plus OpenAI-compatible endpoints.
- Restart-durable delegate status and results, with visible terminal provider
  failures and force-stop fencing for late queued work.
- Permission modes and tool-access control per configuration.
- Optional policy gating: a gate service decides allow / deny / ask for each
  tool call against the authenticated user's versioned mandate, on top of the
  structural security controls.
- External interoperability: published MCP endpoints, A2A 1.0 server/client,
  and an AG-UI protocol server on the same publications (streaming runs,
  frontend tools, shared state, interrupts) for CopilotKit-style frontends.
- External AG-UI agents as first-class conversation participants through direct
  endpoints or scoped `aguiConnection` services, with multimodal prompts,
  durable protocol state, reasoning/activities/steps/usage, resumable
  interrupts, allowlisted approved PawFlow tool round-trips, lifecycle
  enforcement, cancellation, and WebChat/OpenSpace rendering.

### Pipelines

- JSON flows executed as DAGs.
- FlowFiles, relations, parameters, and runtime context.
- Backpressure, checkpoints, crash recovery.
- CRON, file watcher, webhook/polling/event triggers per available modules.
- Subflows, parameter mapping, and NiFi import.
- Flow debugger, provenance, versioning, and cluster mode.
- Manual Flow Editor: one canvas with view / runtime / edit modes, task
  palette, schema-driven property inspector, connection wiring, process
  groups, version-pinned subflows, drafts with optimistic locking, static
  validation, structured diff, and immutable published versions.
- Flow Runtime Console: task control, queue inspect/empty, FlowFile download,
  and previewed hot-swap of a running instance.

### Tools and relay

- File read/write/edit.
- Bash/terminal via relay.
- File/content search.
- Web fetch/scraping.
- Image, video, audio, voice, 3D, upscale, try-on, and lipsync generation per configured providers.
- Desktop/screen/browser automation via relay/VNC per configuration.
- Security scanning and script execution.
- Secret, resource, memory, KG, and plan management.
- Relay-owned ScratchDir lifecycle and quota enforcement, including confined
  in-tree symbolic links with fail-closed handling for unsafe links and recovery
  operations even when the directory is already above quota.
- Incremental conversation indexing and candidate-prefiltered plaintext history
  search for large transcripts.

### User interfaces

- Web chat with SSE, files, context, slash commands, `/desktop`, and conversation management; three conversation views (simplified turn view, classic transcript, and the Openspace live 3D office with per-agent desks, bubbles, status orbiters, and projected panels).
- PawCode CLI for terminal use.
- VS Code extension.
- Native Android app with encrypted server profiles, native/OAuth2 login, and
  parallel webchat tabs.
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
5. **Agent continuity tooling**: memory, KG, diary, todo, scratchpad, project
   graph/wiki, and plans go beyond stateless chat without collapsing different
   scopes and lifetimes into one store.
6. **Meaningful test coverage**: the repository ships a real pytest suite, not just a demo script.

## Watch-outs

- The project is in **beta**: the public API, JSON formats, and internal contracts are stabilizing; changes before 1.0.0 should be minor and noted in the CHANGELOG.
- Documentation freshness is uneven. Some older documents still describe an MVP-era state.
- The functional surface is very wide: distinguish between modules that are present, paths that are tested, and integrations actually validated in production.
- Some capabilities depend on secrets, external providers, an active relay, or a properly configured Docker/local environment.
- High-level README counts are useful but must stay in sync with the real catalog and tests.

## Current roadmap

Per `ROADMAP.md`, the next major directions are:

- stabilization and release hardening;
- Git worktree isolation for parallel agents;
- an iOS client and PWA offline caching (the native Android app is shipped);
- MCP elicitation;
- x402 payment policies for published endpoints and outbound calls;
- filesystem hooks;
- additional LLM providers: Ollama, Mistral, vLLM, LM Studio, Together.ai;
- a full AWS-native remote execution mode;
- first-class Discord, Slack, and WhatsApp conversation clients.

The manual flow editor, package-backed media providers, the installation
wizard, headless JSON mode, the marketplace, published MCP servers, and
push-to-talk voice input listed here in earlier revisions have shipped; see
the *Recently Completed* section of `ROADMAP.md`.

## Conclusion

PawFlow has moved from a base architecture to a complete agentic platform in beta. The project summary therefore must present it as an integrated system: **server + agents + flow engine + relay + clients + documentation + tests**.

The old "4 implemented tasks / 0 service / 1 test script" framing is obsolete. The current accurate reading is: an already substantial product with a rich architecture and many modules in place, but one that still has to stabilize its contracts, clarify what is production-ready, and keep its documentation in sync with the code.

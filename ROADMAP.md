# PawFlow Roadmap

This document outlines the direction for PawFlow. Items are grouped by priority and roughly ordered within each group. Completed items are listed at the bottom for transparency.

> **Status**: PawFlow is in **alpha**. The core platform is functional and tested, but APIs may change between releases.

---

## What's shipping now (v1.0.0-alpha)

The alpha release includes:

- **AI Agent Orchestration** — Multi-agent conversations with Claude Code, OpenAI, Anthropic, and Gemini. Tool-use loops, delegation, plans, streaming.
- **Pipeline Engine** — 101 task types, DAG execution, backpressure, checkpointing, crash recovery, CRON scheduling.
- **80+ Built-in Tools** — Filesystem, bash, code editing, web fetch, image/video/audio generation, security scanning, memory, knowledge graph.
- **Persistent Memory** — Semantic memory, knowledge graphs, agent diaries, project graphs that survive across conversations.
- **Web Chat** — SSE streaming, file explorer, context editor, 60+ slash commands, @file mentions, multi-agent switching.
- **NiFi Import** — Import Apache NiFi flows with automatic processor mapping and LLM-assisted Groovy-to-Python conversion.
- **Authentication** — 9 OAuth providers, JWT tokens, API keys, RBAC.
- **Docker Support** — Containerized deployment with relay for isolated tool execution.
- **PawCode CLI** — Claude Code-compatible terminal client.

---

## High Priority

### Voice input
Push-to-talk in the web chat and PawCode CLI. Audio is transcribed server-side and injected as a text message. Browser-native speech recognition as a fallback.

### Git worktree isolation for agents
Each sub-agent works in its own git worktree so parallel coding tasks don't collide. Changes are merged on completion. A `/batch` command to fan out N tasks across N isolated agents.

### More LLM providers
Ollama, Mistral, vLLM, LM Studio, Together.ai — most work via the OpenAI-compatible endpoint with a `base_url` override. Auto-discovery for local Ollama instances.

### MCP elicitation
MCP servers can request user input during tool execution. The web chat shows a dialog, the user responds, and the tool continues.

### PawFlow as MCP server
Expose PawFlow's tools via the Model Context Protocol so other agents (Claude Code, other PawFlow instances) can use them. `pawflow mcp serve --port 8765`.

### Mobile client (PWA)
Progressive Web App installable on iOS and Android. Offline caching, push notifications when agents respond, mobile-optimized layout.

### Headless JSON mode
Single-shot API endpoint that runs an agent and returns structured JSON. No SSE, no streaming — designed for CI/CD, scripts, and webhooks. Optional JSON schema for structured output.

---

## Medium Priority

### Agent YAML definitions
Define agents as `.yaml` files in your repo (`.pawflow/agents/agent_name.yaml`). Versioned in git, auto-discovered on relay connect.

### Interactive diff viewer
Side-by-side diff viewer in the web chat for file changes made by agents. Click any edit to see what changed.

### More search providers
Brave, Perplexity, and Google search APIs alongside the built-in DuckDuckGo.

### Sparse checkout for large repos
Checkout only specific paths on relay connect (`--sparse-paths src/,tests/`) to reduce noise in monorepos.

### Filesystem hooks
React to file changes automatically — run tests, lint, or trigger agents when files are modified. Configured via `.pawflow/hooks.yaml`.

---

## Future

### Skill marketplace
Community repository for sharing skills, tools, and flow templates. Browse, search, install with `/install skill_name`.

### Additional messaging channels
Microsoft Teams, Matrix, Signal, IRC — building on the existing Telegram/Discord/Slack/WhatsApp channel framework.

### Themes
User-selectable color themes for the web chat (dark, light, solarized, etc.).

### Text-to-speech
Play agent responses as audio in the web chat.

### OpenTelemetry tracing
Spans for each task execution in the pipeline engine, exportable to Jaeger, Zipkin, etc.

---

## Recently Completed

These were shipped as part of the alpha development cycle:

- Hard cost cap per conversation (budget limits with 80% warning threshold)
- @file mention with autocomplete in the web chat
- Agent instructions file (`.md`) that survives context compaction
- Permission modes (read-only / approve-edits / auto) with quick toggle
- `!cmd` inline bash execution in PawCode CLI
- Ctrl+R history search in PawCode CLI
- HTTP listener service (request/response flows)
- Flow debugger with breakpoints, stepping, and FlowFile inspection
- Data preview and flow diff
- Event triggers (file watcher, webhook, event-driven, polling)
- NiFi import with process group support
- Parameter context injection and subflow mapping
- Plugin versioning (semver, upgrade/downgrade, dependencies)
- Crash recovery and flow versioning
- i18n (English, French, Spanish)
- Cluster mode with leader election
- 15 built-in flow templates

---

Have a feature request? [Open an issue](https://github.com/allcolor/PawFlow-Agents/issues).

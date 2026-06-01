# PawFlow Roadmap

This document outlines the direction for PawFlow. Items are grouped by priority and roughly ordered within each group. Completed items are listed at the bottom for transparency.

> **Status**: PawFlow is in **alpha**. The core platform is functional and tested, but APIs may change between releases.

---

## What's shipping now (v1.0.0-alpha)

The alpha release includes:

- **AI Agent Orchestration** — Multi-agent conversations with Claude Code, Codex CLI, Gemini CLI, Anthropic API, OpenAI API, and OpenAI-compatible endpoints. Tool-use loops, delegation, plans, streaming, and provider-specific sessions.
- **Pipeline Engine** — 100+ task types, DAG execution, backpressure, checkpointing, crash recovery, CRON scheduling, triggers, debugger, and flow versioning.
- **90+ Built-in Tools** — Filesystem, bash, code editing, web fetch/search, desktop screen interaction, browser automation, image/video/audio/voice/3D generation, security scanning, memory, knowledge graph, plans, and resources.
- **Shared Multi-Client Conversations** — Web chat, PawCode CLI, VS Code, API/channel clients, and flows can attach to the same conversation stream and state.
- **Persistent Memory** — Semantic memory, knowledge graphs, agent diaries, project graphs that survive across conversations.
- **Web Chat & Desktop Control** — SSE streaming, file explorer, context editor, 60+ slash commands, @file mentions, multi-agent switching, `/desktop`, VNC-style desktop sessions, screenshots, and audio-capable remote desktop notes.
- **Authentication** — 9 OAuth providers, JWT tokens, API keys, RBAC.
- **Docker Support** — Containerized deployment with relay for isolated tool execution.
- **PawCode CLI & VS Code** — Terminal and editor clients connected to the same PawFlow runtime.
- **Package Ecosystem** — Signed `.pfp` packages, package registries, package runtime proxies, Resources sidebar package workflows, and external skill marketplace import.

---

## High Priority

### Stabilization and release hardening
Tighten the alpha runtime around the paths that now exist: relay/local execution, package runtime, import/export, streaming, auth, media artifacts, and long-running flows. Prioritize regression tests, failure diagnostics, and documentation that matches shipped behavior.

### Manual flow editor
Practical web UI for creating and editing flows without hand-writing JSON. First target is a reliable manual editor: task palette, property inspector, connection wiring, validation, and deploy/start controls. A richer full visual editor can grow from this after the core edit loop is stable.

### New media service providers
Add package-backed media providers for image, video, audio, 3D, lipsync, and upscaling services. Providers should use the PFP service-provider runtime, declared secret bindings, and file-backed artifact output instead of JSON/base64 media payloads.

### Git worktree isolation for agents
Each sub-agent works in its own git worktree so parallel coding tasks don't collide. Changes are merged on completion. A `/batch` command to fan out N tasks across N isolated agents.

### MCP elicitation
MCP servers can request user input during tool execution. The web chat shows a dialog, the user responds, and the tool continues.

### PawFlow as MCP server
Expose PawFlow's tools via the Model Context Protocol so other agents (Claude Code, other PawFlow instances) can use them. `pawflow mcp serve --port 8765`.

### Filesystem hooks
React to file changes automatically — run tests, lint, trigger flows, or ask agents to review modified files. Configured via `.pawflow/hooks.yaml`.

### Full cost tracking dashboard
Cost caps and usage tracking exist; the remaining work is an operator dashboard with per-user, per-conversation, per-agent, per-provider, and per-flow breakdowns, plus exportable usage history.

### More LLM providers
Ollama, Mistral, vLLM, LM Studio, Together.ai — most work via the OpenAI-compatible endpoint with a `base_url` override. Auto-discovery for local Ollama instances.

### Mobile client (PWA)
Progressive Web App installable on iOS and Android. Offline caching, push notifications when agents respond, mobile-optimized layout.

---

## Medium Priority

### Package and marketplace UX hardening
The PFP package system, decentralized registries, package search/install/update, and external skill imports exist. Continue polishing review surfaces, provenance display, registry management, package dependency explanations, and Resources sidebar workflows.

### Voice UX polish
Text-to-speech and media voice tooling exist; remaining work is tighter web chat playback controls, browser capture polish, and consistent voice UX across web, CLI, and desktop clients.

---

## Future

### Public package catalog
A hosted/community catalog on top of decentralized PFP registries, with better discovery for agents, skills, tools, service providers, flow tasks, flows, UI extensions, and MCP integrations.

### OpenTelemetry tracing
Spans for each task execution in the pipeline engine, exportable to Jaeger, Zipkin, etc.

---

## Recently Completed

These were shipped as part of the alpha development cycle:

- Hard cost cap per conversation (budget limits with 80% warning threshold)
- Agent instructions file (`.md`) that survives context compaction
- Permission modes (read-only / approve-edits / auto) with quick toggle
- `/call` (direct tool invocation) and `/terminal` (shell commands) in PawCode CLI
- Ctrl+R history search in PawCode CLI
- HTTP listener service (request/response flows)
- Flow debugger with breakpoints, stepping, and FlowFile inspection
- Data preview and flow diff
- Event triggers (file watcher, webhook, event-driven, polling)
- Parameter context injection and subflow mapping
- Plugin versioning (semver, upgrade/downgrade, dependencies)
- Crash recovery and flow versioning
- i18n (English, French, Spanish)
- Cluster mode with leader election
- 13 built-in flow templates
- Text-to-speech tool support via configured speech services
- First-run installation wizard
- PFP packages: signed `.pfp` artifacts, selectable install plans, export/build/dev-load, update/uninstall, and decentralized registries
- External skill marketplace search/import with package review and provenance
- Resource sidebar package install/update/uninstall workflows
- User-selectable web chat themes

---

Have a feature request? [Open an issue](https://github.com/allcolor/PawFlow-Agents/issues).

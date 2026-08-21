# PawFlow Roadmap

This document outlines the direction for PawFlow. Items are grouped by priority and roughly ordered within each group. Completed items are listed at the bottom for transparency.

> **Status**: PawFlow is in **beta**. The core platform is functional and tested; remaining API changes before 1.0.0 are expected to be minor.

---

## What's shipping now (v1.0.0-beta)

The beta release includes:

- **AI Agent Orchestration** — Multi-agent conversations with native Claude Code, Codex, Antigravity/Agy, and Gemini CLI sessions; Anthropic, OpenAI, and OpenAI-compatible APIs; first-class external MCP agents; and remote A2A agents. Tool-use loops, delegation, plans, streaming, and provider-specific sessions.
- **Pipeline Engine** — 100+ task types, DAG execution, backpressure, checkpointing, crash recovery, CRON scheduling, triggers, debugger, and flow versioning.
- **90+ Built-in Tools** — Filesystem, bash, code editing, web fetch/search, desktop screen interaction, browser automation, image/video/audio/voice/3D generation, security scanning, memory, knowledge graph, plans, and resources.
- **Shared Multi-Client Conversations** — Web chat, PawCode CLI, VS Code, API/channel clients, flows, authenticated MCP clients, and A2A agents can attach to the same conversation stream and state.
- **Persistent Cognition and Work State** — Semantic memory, knowledge graphs, agent diaries, relay-scoped project graphs and wikis, durable todo lists, expiring scratchpads, and learned skills survive across conversations and restarts.
- **Web Chat & Desktop Control** — SSE streaming, file explorer, context editor, 60+ slash commands, @file mentions, multi-agent switching, `/desktop`, VNC-style desktop sessions, screenshots, audio-capable remote desktop notes, voice in/out (STT/TTS), realtime speech-to-speech voice conversations, and a built-in IDE (code-server on the relay workspace via `/code`). Three conversation views: the simplified live turn view (default), the classic transcript, and **Openspace**, a live 3D office.
- **Flow Authoring and Operations** — a manual Flow Editor (task palette, property inspector, connection wiring, process groups, version-pinned subflows, static validation, structured diff, immutable published versions) and a NiFi-style Flow Runtime Console (task control, queue inspect/empty, FlowFile download, previewed hot-swap of a running instance).
- **Agent Interoperability** — published MCP servers, A2A 1.0 publication and delegation, an AG-UI protocol server on the same publications, and external AG-UI agents as first-class conversation participants.
- **Policy Gating (V0)** — an optional gate service decides allow / deny / ask for each tool call against the authenticated user's versioned mandate, on top of (never instead of) the structural security controls.
- **Authentication & Private Gateway** — 9 OAuth providers, JWT tokens, API keys, RBAC, and a private gateway that keeps the server invisible until sign-in: camouflage skins, multi-provider sign-in (Google, GitHub, X, Telegram, Microsoft, Facebook, Amazon), and `trusted_proxies` support for reverse-proxy deployments.
- **Telegram Agent Client** — Talk to your agents from Telegram with shared conversation history, streaming updates, consolidated thinking, voice messages (STT), and identity linking.
- **Docker Support** — Containerized deployment with relay for isolated tool execution.
- **Relay Service Tunnels** — Owner-scoped, relay-to-relay access to approved TCP services through loopback-only listeners, short-lived signed grants, and FRPS authorization.
- **PawCode CLI & VS Code** — Terminal and editor clients connected to the same PawFlow runtime.
- **Android App** — Native client with encrypted multi-server profiles, native built-in/OAuth2 login (PKCE handoff), parallel webchat tabs, system-managed downloads, and an APK published with every release.
- **Package Ecosystem** — Signed `.pfp` packages, package registries, package runtime proxies, Resources sidebar package workflows, and external skill marketplace import.

---

## High Priority

### Stabilization and release hardening
Tighten the beta runtime around the paths that now exist: relay/local execution, package runtime, import/export, streaming, auth, media artifacts, and long-running flows. Prioritize regression tests, failure diagnostics, and documentation that matches shipped behavior.

### Git worktree isolation for agents
Each sub-agent works in its own git worktree so parallel coding tasks don't collide. Changes are merged on completion. A `/batch` command to fan out N tasks across N isolated agents. See [docs/GIT_WORKTREE_ISOLATION_PLAN.md](docs/GIT_WORKTREE_ISOLATION_PLAN.md).

### MCP elicitation
MCP servers can request user input during tool execution. The web chat shows a dialog, the user responds, and the tool continues.

### x402 payments
Support x402 for payment-gated HTTP, tool, flow, package, and A2A agent endpoints. Start with server-side `402 Payment Required` policies for published APIs, then add client-side payment handling so PawFlow agents can pay for external x402-protected resources under explicit budgets and approval policies. See [docs/x402_integration.md](docs/x402_integration.md).

### Filesystem hooks
React to file changes automatically — run tests, lint, trigger flows, or ask agents to review modified files. Configured via `.pawflow/hooks.yaml`.

### More LLM providers
Ollama, Mistral, vLLM, LM Studio, Together.ai — most work via the OpenAI-compatible endpoint with a `base_url` override. Auto-discovery for local Ollama instances.

### Full AWS-native deployment (remote execution mode)
Add a new, additive `remote` execution mode so PawFlow runs on AWS managed compute (ECS Fargate, EKS) — and, by generalization, plain EC2 and ECS-on-EC2 — without a local Docker socket, shared host filesystem, or host-gateway networking. Today PawFlow spawns sibling containers on the host Docker daemon via `docker.sock`; the new mode introduces an `ExecBackend` abstraction (Docker backend preserves current behavior bit-for-bit; a remote backend dispatches execution to a WS-reachable worker fleet over the existing relay protocol, with ECS RunTask / K8s Job orchestration for elasticity), a `RemoteProcess` Popen-compatible shim for stream/kill parity, network-shared session storage (EFS or the server-fs FUSE relay), RDS/Aurora Postgres, ECR images, and Secrets Manager/SSM. The remote backend is strictly more general, so supporting Fargate/EKS transitively covers EC2 and ECS-on-EC2. The existing Docker mode stays the default and unchanged. See [docs/AWS_REMOTE_EXEC_PLAN.md](docs/AWS_REMOTE_EXEC_PLAN.md).

### Mobile clients — iOS and PWA
The native Android app is shipped (server profiles, native login, tabbed webchat, per-release APK — see [docs/ANDROID_APP.md](docs/ANDROID_APP.md)). Remaining work: an **iOS client**, PWA offline caching for browsers without the native app, a release-signed/Play Store build, and push notifications when agents respond.

### External webchat clients
Telegram is shipped as a first-class agent client (shared history, streaming, voice messages, identity linking). Remaining work: bring Discord, Slack, and WhatsApp to the same level — the bot services and flow-level receiver/send tasks exist, but not the full conversation-client experience.

---

## Medium Priority

### Package and marketplace UX hardening
The PFP package system, decentralized registries, package search/install/update, and external skill imports exist. Continue polishing review surfaces, provenance display, registry management, package dependency explanations, and Resources sidebar workflows.

### Voice UX polish
Realtime speech-to-speech conversation shipped (OpenAI Realtime and Gemini Live adapters, webchat voice mode with barge-in and push-to-talk, tool use, Telegram voice-note replies, session resumption). Remaining work: live-endpoint validation of the Gemini adapter, more realtime providers (Nova Sonic, WebRTC transport), voice approval UX for gated tools, tighter web chat playback controls, and consistent voice UX across web, CLI, and desktop clients.

---

## Future

### Public package catalog
A hosted/community catalog on top of decentralized PFP registries, with better discovery for agents, skills, tools, service providers, flow tasks, flows, UI extensions, and MCP integrations.

### OpenTelemetry tracing
Spans for each task execution in the pipeline engine, exportable to Jaeger, Zipkin, etc.

---

## Recently Completed

These were shipped as part of the beta development cycle:

- Manual Flow Editor: one canvas with view / runtime / edit modes, task palette
  and schema-driven property inspector, connection wiring with queue settings,
  process groups and version-pinned subflows, drafts with optimistic locking,
  static validation, structured diff, and immutable published versions
  (add or delete a version, never edit one)
- Flow Runtime Console: task start/stop/restart, queue pause/inspect/empty,
  FlowFile download, and previewed hot-swap of a running instance
- Openspace: a live 3D office view of the conversation (per-agent desks, chibi
  avatars, speech/thought bubbles, status orbiters, context batteries, wall
  screen transcript, resource posters, FileStore TV, and a 3D stage for
  deployed flows), alongside the simplified live turn view that is now the
  default reading of a conversation
- Policy gating (V0): a `gating` service (policy prompt on an API-backed LLM
  and/or sandboxed policy scripts) bound to a conversation and/or an agent,
  versioned user-authority contexts, a central engine in the main agent
  runtime, fail-closed behaviour for other runtimes, and an audit trail
- AG-UI: protocol server on isolated publications (streaming runs, frontend
  tools, shared state, interrupts) and external AG-UI agents as first-class
  conversation participants through direct endpoints or scoped
  `aguiConnection` services
- Package-backed media service providers: Kling, Pixazo, and Wavespeed ship as
  signed `.pfp` packages on the PFP service-provider runtime, with declared
  secret bindings and file-backed artifacts instead of base64 payloads
- Native Android app: encrypted multi-server profiles, native built-in/OAuth2
  login (PKCE handoff), parallel webchat tabs, system-managed downloads, and an
  APK published with every release
- Durable confirmations and durable flow wait/notify: agents and flows ask
  yes/no or single/multi-choice questions answered whenever, and resume with
  the answer
- Opt-in encryption at rest for conversations and conv-scoped relay workspaces
- External secret providers: pluggable provider services with scoped,
  allowlisted secret access
- Published conversation MCP servers: authenticated Streamable HTTP endpoints,
  isolated per-session stdio bridges and cross-platform client installers, plus
  first-class external MCP agents for Claude Code, Codex, Agy/Gemini, OpenCode,
  JCode, Pi, and Hermes
- A2A 1.0 interoperability: public Agent Cards, authenticated HTTP+JSON agent
  publication, durable task operations, cross-conversation delegation, and the
  built-in `a2a` client for remote agents
- Relay Service Tunnels: approved relay-to-relay TCP access with owner isolation,
  loopback-only listeners, short-lived signed grants, FRPS authorization, and
  Relay Desktop/admin controls
- Relay-scoped Project Wiki and Scratchpad systems, connected webchat panels,
  source provenance and stale-page protection, bounded refreshes and TTLs, and
  recurring skill-draft promotion through the reviewed resource path
- Scoped web search services backed by search-cli, with 12 configurable
  providers, bounded fallbacks, encrypted API keys, and visible diagnostics
- Zero-shot Pocket TTS voice cloning through the normal `clone_voice` and
  `speak` tool path, with owner-scoped private FileStore voice references
- Full usage/cost tracking: a persistent event-level ledger (SQLite,
  every LLM call recorded with user/conversation/agent/service/model/
  channel dimensions and cost frozen at the rates in effect when it ran)
  replacing the old JSON aggregate trackers; a live per-conversation cost
  gauge (header badge + breakdown panel, updated over SSE after every
  turn); a global "Usage & Costs" dashboard (KPI cards, a stacked daily
  chart selectable by service/agent/model/channel, top conversations and
  agents, admin all-users view); a `subscription` flag on flat-rate LLM
  services so their usage shows as virtual ("what this would have cost
  via API") instead of real spend; and cumulative spend budgets — daily
  or monthly caps scoped to a user, conversation, agent, service, or
  globally, with `warn` or `block` policy and 50/80/100% notifications —
  layered on top of (and distinct from) the per-agent-loop-turn budget cap
  further below.
- Telegram as a first-class agent client: shared conversations, streaming updates, consolidated thinking blocks, voice messages via STT, command mirroring, and identity linking
- Private gateway: server invisible until sign-in, camouflage skins, multi-provider sign-in (Google, GitHub, X, Telegram, Microsoft, Facebook, Amazon), and opt-in `trusted_proxies` for reverse-proxy deployments
- Security hardening pass over gateway/OAuth: constant-time token compares, state-keyed PKCE verifiers, and auth gap fixes
- Built-in IDE: code-server served on the relay workspace via `/code`
- Media generation hardening: hybrid webhook+poll completion (validated end-to-end with Pixazo), temporary public reference URLs for provider fetches, and public file share links from the chat
- Voice input (STT) in web chat and Telegram, with TTS voice replies
- Realtime voice conversation (`realtimeVoiceConnection`): speech-to-speech sessions over OpenAI Realtime or Gemini Live — webchat voice-mode overlay (live captions, barge-in, push-to-talk, settings panel), silent-approval tool use, conversation context injection, voice-native agents with Telegram voice-note replies, transparent session resumption
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
- User-selectable workspace themes, global or per conversation

---

Have a feature request? [Open an issue](https://github.com/allcolor/PawFlow-Agents/issues).

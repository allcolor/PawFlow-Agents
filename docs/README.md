# PawFlow Documentation

## Start Here

| Document | Description |
|----------|-------------|
| [Architecture](architecture.md) | Internal architecture: FlowFile, Task, Service, Flow, engine |
| [Agent System](AGENT_SYSTEM.md) | Agent loop, context management, plans, multi-agent, streaming |
| [LLM Providers](llm_providers.md) | Provider selection, API/subscription credentials, compatible endpoints, and delegated vision for text-only models |
| [Multi-LLM Aggregator](llm_aggregator.md) | Configure parallel read-only advisors and a final synthesis/execution LLM |
| [Claude Code Interactive](CLAUDE_CODE_INTERACTIVE.md) | Recommended Claude Code provider: observable, tmux-backed interactive sessions |
| [PawCode CLI](pawcode.md) | Terminal client, stream-JSON mode, shared conversations |
| [VS Code Extension](vscode.md) | Editor client, resource panel, approvals |
| [Multi-Client Conversations](multi_client_conversations.md) | Web, CLI, VS Code, API, and channels sharing one conversation runtime |
| [Desktop, VNC, Screen, and Audio](desktop_vnc.md) | noVNC desktop, screen automation, local vs Docker desktop, audio sync |
| [Media and Multimodal Tools](media_tools.md) | Image, video, audio, 3D, try-on, lipsync, voice clone, speech-to-video |
| [PawFlow Avatar Helper](HELPER_AVATAR.md) | Installable avatar-guided navigation, fixed semantic UI targets, and safety boundary |
| [ComfyUI](comfyui.md) | Install ComfyUI, export API workflows, configure relay routing and image/video bindings |
| [ComfyUI local setup](COMFYUI_LOCAL_SETUP.md) | Step-by-step: local GPU + VPS relay, SSH tunnel, LTX-Video workflow, `generate_video` end-to-end |
| [Realtime Voice Plan](REALTIME_VOICE_PLAN.md) | Speech-to-speech voice sessions: architecture, adapters, phasing |

## Runtime Reference

| Document | Description |
|----------|-------------|
| [Task Catalog](tasks.md) | Built-in flow tasks and `tool.*` flow task adapter |
| [Agent Tool Catalog](tool_catalog.md) | Agent-facing tools grouped by purpose |
| [Agent Tool Selection](TOOL_SELECTION.md) | Decision guide for overlapping tool families: files, delegation, work orchestration, continuations, and state |
| [Services Catalog](services.md) | Service types: LLM, relay, media, messaging, auth, storage |
| [PawFlow Packages](PFP_PACKAGES.md) | Signed `.pfp` package format, install plan, export/build workflow, and security model |
| [PFP Developer Guide](PFP_DEVELOPER_GUIDE.md) | Build and test package tools/services locally with `dev-load`, media artifacts, and runtime SDK patterns |
| [PFP Publisher Guide](PFP_PUBLISHER_GUIDE.md) | Registry publishing, release versioning, SHA pinning, and key rotation |
| [Marketplace and Package Registries](marketplace.md) | PFP registries, skill marketplace import, review model, and UI/CLI entry points |
| [Cognitive Tools](COGNITIVE_TOOLS.md) | Memory, Knowledge Graph, Diary, Todo, Scratchpad, Project Graph, and Project Wiki |
| [Usage & Cost Tracking](usage_tracking.md) | Persistent per-event token/cost ledger, channels, query actions, exports |
| [Skill Learning Loop](LEARNING_LOOP_PLAN.md) | Agent-created skills, drafts from compaction, usage stats, curator task |
| [Expression Language](EXPRESSION_LANGUAGE.md) | `${scope.key}` syntax, operators, cascade |
| [Slash Commands](SLASH_COMMANDS.md) | Webchat/CLI/VS Code command surface |
| [Filesystem](filesystem.md) | Filesystem abstraction, relay backends, permissions |
| [HTTP Listener](http_listener.md) | Shared HTTP listener architecture |
| [Provenance](provenance.md) | Data lineage and traceability |
| [Voice Clone](voice_clone.md) | Voice clone cache, provider paradigms, deletion semantics |
| [Pixazo](pixazo.md) | Raw Pixazo model/provider reference |
| [Tripo3D & Meshy](tripo_meshy.md) | Native 3D providers: text/image-to-3D, rigging, animation, retexture |

## Deployment and Development

| Document | Description |
|----------|-------------|
| [Deployment](deployment.md) | Local, Docker, sidecar, and production deployment |
| [Docker](docker.md) | Containerization, relay Docker mode, desktop audio notes |
| [Relay Client](relay_client.md) | Standalone client relay CLI/Desktop contract |
| [Service Tunnels](service_tunnels.md) | Relay-approved loopback TCP tunnels, FRP deployment, security, and troubleshooting |
| [Relay Image Profiles](relay_images.md) | Server full relay image and configurable client relay image profiles |
| [Security Model](security_model.md) | Trust boundaries, relay risk, desktop/VNC, provider egress, production checklist |
| [Observability](OBSERVABILITY.md) | Session correlation in logs (always on) and optional OpenTelemetry tracing |
| [Development](development.md) | Creating custom tasks and services |
| [Relay Server Filesystem](relay_server_fs.md) | Relay filesystem server details |
| [Example: Agent-Created Flow](examples/first_agent_flow.md) | Minimal daily digest flow pattern |

## Planning and Deep References

| Document | Description |
|----------|-------------|
| [Technical Reference](01_DOCUMENTATION_TECHNIQUE.md) | Detailed technical reference |
| [Tasks & Services Reference](02_REFERENCE_TASKS_SERVICES.md) | Task/service schema reference |
| [Roadmap Gaps](ROADMAP_GAPS.md) | Release-readiness gaps and planned improvements |
| [Eval Harness Plan](EVAL_HARNESS_PLAN.md) | Scored agent evaluation: case format, scorers, suites, scorecard, phasing |
| [Model Harness Profiles Plan](MODEL_HARNESS_PROFILES_PLAN.md) | Per-model prompt/tool/limit tuning behind one resolution point |
| [FFF Search Integration Plan](FFF_SEARCH_INTEGRATION_PLAN.md) | Optional relay-side indexed search, packaging choices, lifecycle boundaries, rollout phases, and acceptance gates |
| [Maestro PFP Integration Plan](MAESTRO_PFP_INTEGRATION_PLAN.md) | Deferred standalone connector architecture, license gates, security boundary, and phased delivery |
| [Threat Models Plan](THREAT_MODELS_PLAN.md) | Per-surface attacker models with mandatory residual risk |
| [Code Signing Plan](CODE_SIGNING_PLAN.md) | Signing Windows/macOS/Linux artifacts: certificates, registrations, costs, rollout phases |

## Quick Links

- **Root README**: [../README.md](../README.md)
- **Latest downloads**: [GitHub Releases](https://github.com/allcolor/PawFlow-Agents/releases/latest) -- installer zip, PawCode, Relay CLI, Relay Desktop, source archives
- **CHANGELOG**: [../CHANGELOG.md](../CHANGELOG.md)
- **Project summary**: [../PROJECT_SUMMARY.md](../PROJECT_SUMMARY.md)
- **CLAUDE.md**: [../CLAUDE.md](../CLAUDE.md) -- development context for AI assistants

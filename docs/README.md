# PawFlow Documentation

## Start Here

| Document | Description |
|----------|-------------|
| [Architecture](architecture.md) | Internal architecture: FlowFile, Task, Service, Flow, engine |
| [Agent System](AGENT_SYSTEM.md) | Agent loop, context management, plans, multi-agent, streaming |
| [LLM Providers](llm_providers.md) | Provider selection matrix for API keys and subscriptions: OpenAI/Anthropic APIs, Claude Code, Codex app-server, Antigravity/Agy, Gemini CLI, and compatible endpoints |
| [Claude Code Interactive](CLAUDE_CODE_INTERACTIVE.md) | Experimental MITM-backed Claude Code interactive provider |
| [PawCode CLI](pawcode.md) | Terminal client, stream-JSON mode, shared conversations |
| [VS Code Extension](vscode.md) | Editor client, resource panel, approvals |
| [Multi-Client Conversations](multi_client_conversations.md) | Web, CLI, VS Code, API, and channels sharing one conversation runtime |
| [Desktop, VNC, Screen, and Audio](desktop_vnc.md) | noVNC desktop, screen automation, local vs Docker desktop, audio sync |
| [Media and Multimodal Tools](media_tools.md) | Image, video, audio, 3D, try-on, lipsync, voice clone, speech-to-video |
| [Realtime Voice Plan](REALTIME_VOICE_PLAN.md) | Speech-to-speech voice sessions: architecture, adapters, phasing |

## Runtime Reference

| Document | Description |
|----------|-------------|
| [Task Catalog](tasks.md) | Built-in flow tasks and `tool.*` flow task adapter |
| [Agent Tool Catalog](tool_catalog.md) | Agent-facing tools grouped by purpose |
| [Services Catalog](services.md) | Service types: LLM, relay, media, messaging, auth, storage |
| [PawFlow Packages](PFP_PACKAGES.md) | Signed `.pfp` package format, install plan, export/build workflow, and security model |
| [PFP Developer Guide](PFP_DEVELOPER_GUIDE.md) | Build and test package tools/services locally with `dev-load`, media artifacts, and runtime SDK patterns |
| [PFP Publisher Guide](PFP_PUBLISHER_GUIDE.md) | Registry publishing, release versioning, SHA pinning, and key rotation |
| [Marketplace and Package Registries](marketplace.md) | PFP registries, skill marketplace import, review model, and UI/CLI entry points |
| [Cognitive Tools](COGNITIVE_TOOLS.md) | Memory, Knowledge Graph, Diary, Project Graph |
| [Expression Language](EXPRESSION_LANGUAGE.md) | `${scope.key}` syntax, operators, cascade |
| [Slash Commands](SLASH_COMMANDS.md) | Webchat/CLI/VS Code command surface |
| [Filesystem](filesystem.md) | Filesystem abstraction, relay backends, permissions |
| [HTTP Listener](http_listener.md) | Shared HTTP listener architecture |
| [Provenance](provenance.md) | Data lineage and traceability |
| [Voice Clone](voice_clone.md) | Voice clone cache, provider paradigms, deletion semantics |
| [Pixazo](pixazo.md) | Raw Pixazo model/provider reference |

## Deployment and Development

| Document | Description |
|----------|-------------|
| [Deployment](deployment.md) | Local, Docker, sidecar, and production deployment |
| [Docker](docker.md) | Containerization, relay Docker mode, desktop audio notes |
| [Relay Client](relay_client.md) | Standalone client relay CLI/Desktop contract |
| [Relay Image Profiles](relay_images.md) | Server full relay image and configurable client relay image profiles |
| [Security Model](security_model.md) | Trust boundaries, relay risk, desktop/VNC, provider egress, production checklist |
| [Development](development.md) | Creating custom tasks and services |
| [Relay Server Filesystem](relay_server_fs.md) | Relay filesystem server details |
| [Example: Agent-Created Flow](examples/first_agent_flow.md) | Minimal daily digest flow pattern |

## Planning and Deep References

| Document | Description |
|----------|-------------|
| [Technical Reference](01_DOCUMENTATION_TECHNIQUE.md) | Detailed technical reference |
| [Tasks & Services Reference](02_REFERENCE_TASKS_SERVICES.md) | Task/service schema reference |
| [Roadmap Gaps](ROADMAP_GAPS.md) | Release-readiness gaps and planned improvements |

## Quick Links

- **Root README**: [../README.md](../README.md)
- **Latest downloads**: [GitHub Releases](https://github.com/allcolor/PawFlow-Agents/releases/latest) -- installer zip, PawCode, Relay CLI, Relay Desktop, source archives
- **CHANGELOG**: [../CHANGELOG.md](../CHANGELOG.md)
- **Project summary**: [../PROJECT_SUMMARY.md](../PROJECT_SUMMARY.md)
- **CLAUDE.md**: [../CLAUDE.md](../CLAUDE.md) -- development context for AI assistants

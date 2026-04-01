# PawFlow

PawFlow is a self-hosted AI agent orchestration platform. It combines a NiFi-inspired pipeline engine with a multi-agent system that supports tool use, streaming, multi-provider LLMs, and a real-time web chat UI.

## Key Features

- **Multi-agent system** with tool-use loop, streaming SSE, context compaction, and memory
- **Multiple LLM providers**: Claude Code (subprocess + MCP), Anthropic API, OpenAI API, Gemini CLI
- **40+ built-in tools**: filesystem, bash, code editing, web fetch, screen capture, grep, glob, notebooks, media generation, and more
- **Pipeline engine**: NiFi-style flow-based processing with FlowFiles, backpressure, and DAG execution
- **Web chat UI**: real-time SSE, file explorer, context editor, conversation management, attachments, RxJS action bus
- **Authentication gateway**: 9 providers (builtin, Google, GitHub, Microsoft, X/Twitter, Facebook, Amazon, Telegram, generic OAuth)
- **Docker sandboxing**: isolated execution via relay containers with tool permissions
- **PawCode CLI**: terminal client for interacting with PawFlow agents (interactive + stream-json modes)
- **VS Code extension**: IDE integration
- **Expression language**: `${scope.key:op1:op2("arg")}` with 40+ chainable operations
- **Resource system**: agents, skills, MCP servers as managed resources with per-user/per-conversation scoping
- **Cost tracking**: per-model, per-session token and cost accounting
- **Identity system**: multi-provider account linking

## Architecture

```
pawflow/
├── core/                  # Engine: FlowFile, Task, Service, Flow abstractions
│   ├── handlers/          # 35+ tool handlers (bash, edit, glob, grep, web, screen...)
│   ├── llm_providers/     # Claude Code, Anthropic, OpenAI, Gemini
│   ├── conversation_store.py   # JSONL conversation persistence
│   ├── resource_store.py       # Agents, skills, MCP servers
│   ├── file_store.py           # Binary attachment storage
│   └── tool_registry.py        # Tool discovery and execution
│
├── tasks/
│   ├── ai/                # Agent system (context, compaction, streaming, actions, tools)
│   ├── io/                # HTTP receiver, chat UI (JS), Telegram, Discord
│   └── system/            # Log, wait, script, route, merge
│
├── services/              # 40+ services
│   ├── auth_gateway_service.py  # Multi-provider auth (9 providers)
│   ├── tool_relay_service.py    # MCP bridge for Claude Code
│   ├── *_service.py             # Browser, VNC, terminal, filesystem, messaging...
│   └── auth_providers/          # OAuth provider implementations
│
├── engine/                # Pipeline executor (batch + continuous with backpressure)
├── pawflow_cli/           # PawCode CLI (interactive + stream-json)
├── pawflow-vscode/        # VS Code extension
├── gui/                   # Admin web UI + service registries
├── config/                # JSON configuration (flows, agents, services, parameters)
└── flows/                 # Flow definitions (JSON)
```

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Start the server
python cli.py run --flow data/deployments/global/pawflow-agent.json

# Open the web chat
# http://localhost:9090
```

### PawCode CLI

```bash
# Interactive mode
pawcode --server http://localhost:9090

# Stream-JSON mode (Claude Code compatible)
echo '{"type":"user","message":{"role":"user","content":"hello"}}' | \
  pawcode --input-format stream-json --output-format stream-json
```

## LLM Providers

| Provider | Mode | Features |
|---|---|---|
| Claude Code | Subprocess + MCP | Full tool use via relay, session persistence |
| Anthropic API | Direct API | Streaming, tool use, vision, thinking |
| OpenAI API | Direct API | Streaming, tool use, vision |
| Gemini CLI | Subprocess | Streaming |

## Configuration

Agents, services, and flows are configured via JSON files in `config/`. Parameters cascade: flow → conversation → user → global.

```json
{
  "llm_service": "${claude_code_llm_service}",
  "summarizer_service": "${summarizer_service}",
  "permission_mode": "auto",
  "max_iterations": 200,
  "max_rounds": 5
}
```

## License

MIT

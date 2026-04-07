# PawFlow

**Your AI agents, your infrastructure, your data.** PawFlow is a self-hosted AI agent orchestration platform that puts you in control. No vendor lock-in, no cloud dependency — deploy on your hardware and connect any LLM.

Think of it as "Apache NiFi meets Claude Code" — a visual pipeline engine that also runs autonomous AI agents with 80+ built-in tools, persistent memory, knowledge graphs, and multi-provider LLM support.

## Why PawFlow?

- **Self-hosted**: Your conversations, memories, and code stay on your machines
- **Multi-LLM**: Claude Code, Anthropic API, OpenAI, Gemini — switch freely
- **80+ tools**: Filesystem, bash, code editing, web scraping, image/video/audio generation, security scanning
- **Persistent memory**: Agents remember across conversations — facts, preferences, decisions
- **Knowledge graph**: Entity-relationship triples with temporal validity, BFS/DFS traversal, community detection
- **Pipeline engine**: NiFi-style data flows with 101 tasks, backpressure, DAG execution
- **Real-time UI**: Web chat with streaming, file explorer, context editor, conversation management
- **Docker sandboxing**: Tools execute in isolated relay containers, not on your server

## Quick Start

```bash
# Clone and install
git clone https://github.com/allcolor/PawFlow-Agents.git
cd PawFlow-Agents
pip install -r requirements.txt

# Start the server
python cli.py run --flow data/deployments/global/pawflow-agent.json

# Open the web chat → http://localhost:9090
```

### PawCode CLI (Claude Code replacement)

```bash
# Interactive mode
pawcode --server http://localhost:9090

# Stream-JSON mode (drop-in replacement for Claude Code)
echo '{"type":"user","message":{"role":"user","content":"hello"}}' | \
  pawcode --input-format stream-json --output-format stream-json
```

## Architecture at a Glance

```
┌─────────────────────────────────────────────────────────────────┐
│                        PawFlow Server                           │
│                                                                 │
│  ┌──────────┐  ┌──────────┐  ┌─────────┐  ┌────────────────┐  │
│  │  Agents  │  │ Pipeline │  │   Auth   │  │  Web Chat UI   │  │
│  │  (LLM +  │  │  Engine  │  │ Gateway  │  │  (SSE, files,  │  │
│  │  tools)  │  │ (101     │  │ (9 OAuth │  │   context,     │  │
│  │          │  │  tasks)  │  │ provid.) │  │   commands)    │  │
│  └────┬─────┘  └──────────┘  └──────────┘  └────────────────┘  │
│       │                                                         │
│  ┌────┴─────────────────────────────────────────────────────┐  │
│  │              80+ Tool Handlers (via relay)                │  │
│  │  bash, read, write, edit, glob, grep, web_fetch,         │  │
│  │  generate_image, generate_video, security_scan,          │  │
│  │  remember, recall, kg_add, kg_query, diary_write,        │  │
│  │  project_graph, execute_script, delegate, ...            │  │
│  └──────────────────────────┬───────────────────────────────┘  │
│                             │ WebSocket                        │
└─────────────────────────────┼──────────────────────────────────┘
                              │
                    ┌─────────┴─────────┐
                    │   Relay (Docker)   │  ← runs on user's machine
                    │   or native host   │
                    └───────────────────┘
```

## LLM Providers

| Provider | Mode | Features |
|---|---|---|
| **Claude Code** | Subprocess + MCP | Full tool use via relay, session persistence, thinking |
| **Anthropic API** | Direct HTTP | Streaming, tool use, vision, extended thinking |
| **OpenAI API** | Direct HTTP | Streaming, tool use, vision, JSON mode |
| **Gemini CLI** | Subprocess | Streaming |

## Cognitive Systems

PawFlow agents have persistent memory that survives across conversations:

| System | Purpose | Storage |
|--------|---------|---------|
| **Memory** | Facts, preferences, events organized in wing/hall/room taxonomy | `data/memories/{user}.json` |
| **Knowledge Graph** | Entity-relationship triples with temporal validity | `data/knowledge_graphs/{user}.json` |
| **Agent Diary** | Personal observations, decisions, learnings per agent | `data/memories/{user}/diary_{agent}.jsonl` |
| **Project Graph** | AST-based code structure graph (17 languages via tree-sitter) | `data/graphs/{user}/{conv}/graph.json` |

Memory digests and diary entries are automatically injected into the system prompt at each conversation start.

## Pipeline Engine

101 tasks across 5 categories for data processing workflows:

| Category | Count | Examples |
|----------|-------|---------|
| **System** | 11 | log, wait, executeScript, cronTrigger, listFiles |
| **IO** | 51 | HTTP, Telegram, Discord, Slack, WhatsApp, S3, GCS, Azure, SFTP, Kafka, MQTT, email |
| **Data** | 27 | transformJSON, inferLLM, executeSQL, compressContent, validateJSON |
| **Control** | 11 | routeOnAttribute, splitContent, mergeContent, controlRate |
| **AI** | 1 | agentLoop (the full LLM agent with tool-use cycle) |

## Expression Language

40+ chainable operations for dynamic configuration:

```
${user.name:upper}                    → "QUENTIN"
${api_key:default("none")}            → uses fallback if empty
${content:split(","):index(0):trim}   → first CSV field, trimmed
${:uuid}                              → generates a UUID
${date:format_date("yyyy-MM-dd")}     → "2026-04-07"
```

## Web Chat Features

- Real-time streaming via SSE
- File explorer with relay filesystem access
- Context editor (view/edit agent context)
- Conversation management with auto-titles
- Drag & drop file attachments
- 60+ slash commands (`/agent`, `/memory`, `/relay`, `/run`, `/plan`, ...)
- Escape key: 1x = graceful interrupt, 2x = force stop
- Multi-agent support with agent switching

## Authentication

9 OAuth providers out of the box:

| Provider | Status |
|----------|--------|
| Built-in (username/password) | Ready |
| Google | Ready |
| GitHub | Ready |
| Microsoft | Ready |
| X (Twitter) | Ready |
| Facebook | Ready |
| Amazon | Ready |
| Telegram | Ready |
| Generic OAuth2 | Ready |

## Documentation

| Document | Description |
|----------|-------------|
| [Architecture](docs/architecture.md) | Internal architecture, FlowFile, components |
| [Agent System](docs/AGENT_SYSTEM.md) | Agent loop, context, plans, multi-agent, streaming |
| [Cognitive Tools](docs/COGNITIVE_TOOLS.md) | Memory, KG, diary, project graph (21 tools) |
| [Expression Language](docs/EXPRESSION_LANGUAGE.md) | 40+ operators, scopes, cascade |
| [Slash Commands](docs/SLASH_COMMANDS.md) | All webchat commands |
| [Task Catalog](docs/tasks.md) | 101 tasks with descriptions |
| [Deployment](docs/deployment.md) | Local, Docker, production |
| [Docker](docs/docker.md) | Docker setup, relay mode |
| [Filesystem](docs/filesystem.md) | Relay, backends, permissions |
| [Development](docs/development.md) | Creating custom tasks/services |

## Configuration

Agents, services, and flows are configured via JSON. Parameters cascade: flow → conversation → user → global.

```json
{
  "llm_service": "${claude_code_llm_service}",
  "summarizer_service": "${summarizer_service}",
  "permission_mode": "auto",
  "max_iterations": 200
}
```

## Tests

```bash
pytest tests/ -v    # 284+ tests
```

## License

MIT

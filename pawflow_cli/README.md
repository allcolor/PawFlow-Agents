# PawCode — Terminal Frontend for PawFlow

PawCode is a CLI application that connects to PawFlow as a terminal-based chat frontend, like Claude Code is to Claude. It auto-mounts your working directory as a filesystem relay.

## Quick Start

```bash
# Install dependencies
pip install prompt_toolkit rich tiktoken

# Run (opens browser for OAuth login, auto-mounts CWD)
python -m pawflow_cli --dir .

# With custom server
PAWFLOW_SERVER=http://myserver:9090 python -m pawflow_cli
```

## Features

- **Streaming chat** with Rich markdown rendering
- **Auto-relay** — mounts your directory as a filesystem service
- **70+ slash commands** — full feature parity with web chat
- **Tab completion** for all commands
- **Multiline input** — Alt+Enter for newline, Enter to send
- **File operations** — /upload, /paste (clipboard image), drag & drop
- **Colored diffs** when agent edits files
- **Fun thinking verbs** with animated status bar
- **Multi-agent** streaming with concurrent agent tracking
- **Approval dialogs** for tool/exec permissions
- **File explorer** — /explore for interactive file browsing
- **Security scanning** — /call security_scan
- **Git integration** — /diff, /run, all git operations via agent

## Key Commands

### Conversation
- `/new` — New conversation
- `/conv` — List conversations
- `/resume <id> [N]` — Resume (show last N messages)
- `/history [N] [offset]` — Browse message history
- `/rename <title>` — Rename conversation
- `/export [json|md]` — Export conversation

### Agents
- `/agent list|create|delete|select` — Agent management
- `/msg <agent|ALL> <text>` — Send to specific agent
- `/btw <agent> <question>` — Side question
- `/stop <agent> [-f]` — Interrupt/cancel

### Development
- `/run <command>` — Shell command on relay
- `/diff [ref]` — Colored git diff
- `/plan <description>` — Read-only strategy mode
- `/watch <file>` — Monitor file for changes
- `/view <path|url>` — Open in browser

### Resources
- `/resources` — List all resources
- `/skill list|add|del` — Skill management
- `/task list|create|assign|log` — Task management
- `/service list|install|uninstall` — Service management

### Other
- `/compact` — Compact conversation context
- `/model <name>` — Switch model
- `/cost` — Token usage/cost
- `/copy [N]` — Copy last response to clipboard
- `/search <query>` — Search messages

## Environment Variables

- `PAWFLOW_SERVER` — Server URL (default: http://localhost:9090)

## Architecture

PawCode is a pure HTTP/SSE client — it connects to the same PawFlow backend as the web chat. The relay runs in-process, mounting your directory via WebSocket.

```
┌─────────────────────────┐
│     PawCode Terminal     │
│  prompt_toolkit + Rich   │
│  SSE thread + Relay      │
└───────────┬─────────────┘
            │ HTTP POST + SSE GET + WS
            ▼
┌─────────────────────────┐
│    PawFlow Server        │
│  (same as web chat)      │
└─────────────────────────┘
```

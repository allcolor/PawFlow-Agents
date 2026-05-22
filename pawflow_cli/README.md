# PawCode — Terminal Frontend for PawFlow

PawCode is a CLI application that connects to PawFlow as a terminal-based chat frontend, like Claude Code is to Claude. It does not manage relay lifecycle; use webchat resources or the standalone `pawflow-relay` client for filesystem and desktop access.

## Quick Start

```bash
# Install dependencies
pip install prompt_toolkit rich tiktoken

# Run (opens browser for OAuth login when needed)
python -m pawflow_cli --dir .

# With custom server
PAWFLOW_SERVER=http://myserver:9090 python -m pawflow_cli
```

## Standalone Installer

```bash
python -m pip install pyinstaller
python scripts/build-pawcode-installer.py
```

Artifacts are written to `dist/pawcode-installers/`. The builder always creates portable archives with install scripts, and creates native packages when the host toolchain is available (`dpkg-deb`, `pkgbuild`, or `makensis`). The packaged CLI remains chat-only; use `pawflow-relay` separately for filesystem and desktop access.

## Features

- **Streaming chat** with Rich markdown rendering
- **Shared relay bindings** — uses relays already linked to the conversation
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
- **Git integration** — /diff, /run via linked relays where available

## Key Commands

### Conversation
- `/new [agent] [--llm svc] [--relay rid] [--title text]` — Create a conversation with an agent, LLM service, optional relay binding, and optional title
- `/conv` — List conversations
- `/resume <id> [N]` — Resume (show last N messages)
- `/history [N] [offset]` — Browse message history
- `/rename <title>` — Rename conversation
- `/export [json|md]` — Export conversation
- `/interrupt [agent]` — Interrupt an agent without force-stopping it
- `/stop [agent]` — Force-stop an agent immediately

### Agents
- `/agent list|create|delete|select` — Agent management
- `/msg <agent|ALL> <text>` — Send to specific agent
- `/btw <agent> <question>` — Side question
- `/interrupt [agent]` — Interrupt without force-stopping
- `/stop [agent]` — Force-stop immediately

### Development
- `/run <command>` — Shell command through a linked relay
- `/diff [ref]` — Colored git diff through a linked relay
- `/plan <description>` — Read-only strategy mode
- `/watch <file>` — Monitor file for changes through a linked relay
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

PawCode is a pure HTTP/SSE client. Relay-backed tools operate through relay services linked to the conversation on the PawFlow server.

```
┌─────────────────────────┐
│     PawCode Terminal     │
│  prompt_toolkit + Rich   │
│  SSE client              │
└───────────┬─────────────┘
            │ HTTP POST + SSE GET
            ▼
┌─────────────────────────┐
│    PawFlow Server        │
│  (same as web chat)      │
└─────────────────────────┘
```

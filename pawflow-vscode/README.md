# PawFlow for VS Code

AI agent chat and code assistant powered by PawFlow. Chat with agents, send editor selections, and manage resources from VS Code.

## Installation

1. Open the `pawflow-vscode` folder in VS Code
2. Press F5 to launch Extension Development Host
3. The PawFlow icon appears in the activity bar

## Features

- **Chat sidebar** with streaming responses and markdown rendering
- **OAuth login** via browser (same as web chat)
- **Resource panel** — agents, skills, tasks, services, variables, secrets
- **Context menus** — edit, delete, activate, assign (right-click)
- **Create forms** — full forms for all resource types
- **Active agents** — real-time status display
- **Editor integration** — Explain, Fix, Add Tests (right-click selection)
- **Inline images** — rendered in chat webview
- **Approval dialogs** — native VS Code notifications
- **Diff coloring** — green/red for file edits
- **Session recovery** — auto-detect expired tokens

## Commands

- `PawFlow: Open Chat` — Focus chat panel
- `PawFlow: Login` — Authenticate via browser
- `PawFlow: New Conversation` — Start fresh
- `PawFlow: Explain This Code` — Explain selected code
- `PawFlow: Fix This Code` — Fix selected code
- `PawFlow: Add Tests` — Generate tests for selection
- `PawFlow: Plan` — Read-only strategy planning
- `PawFlow: Run Command` — Execute through a relay already linked to the conversation

## Settings

- `pawflow.serverUrl` — PawFlow server URL (default: http://localhost:9090)
- `pawflow.gatewayKey` — Private gateway access key
- `pawflow.pythonPath` — Optional Python interpreter path for helper commands

## Relay Lifecycle

VS Code is a PawFlow client, like webchat and PawCode. It does not create, start, stop, or own relays. Use the webchat resource panel for server relays, or PawFlow Relay Desktop/CLI for client relays.

## Architecture

The extension connects to the same PawFlow backend as the web chat and PawCode CLI. Relay-backed tools operate through server-side conversation relay bindings.

```
┌─────────────────────────┐
│   VS Code Extension      │
│  Webview + Node.js API   │
└───────────┬─────────────┘
            │ HTTP POST + SSE GET
            ▼
┌─────────────────────────┐
│    PawFlow Server        │
│  (same backend)          │
└─────────────────────────┘
```

# PawFlow for VS Code

AI agent chat and code assistant powered by PawFlow. Chat with agents, edit code, manage resources — all from VS Code.

## Installation

1. Open the `pawflow-vscode` folder in VS Code
2. Press F5 to launch Extension Development Host
3. The PawFlow icon appears in the activity bar

## Features

- **Chat sidebar** with streaming responses and markdown rendering
- **Native TypeScript relay** — no Python dependency for filesystem access
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
- `PawFlow: Toggle Relay` — Start/stop filesystem relay
- `PawFlow: Explain This Code` — Explain selected code
- `PawFlow: Fix This Code` — Fix selected code
- `PawFlow: Add Tests` — Generate tests for selection
- `PawFlow: Plan` — Read-only strategy planning
- `PawFlow: Run Command` — Execute shell command on relay

## Settings

- `pawflow.serverUrl` — PawFlow server URL (default: http://localhost:9090)
- `pawflow.autoRelay` — Auto-start relay on activation
- `pawflow.allowExec` — Allow shell execution
- `pawflow.pythonPath` — Python interpreter (unused — relay is native TS)

## Architecture

The extension connects to the same PawFlow backend as the web chat and PawCode CLI. The relay is implemented in native TypeScript (no Python needed).

```
┌─────────────────────────┐
│   VS Code Extension      │
│  Webview + Node.js API   │
│  TS Relay (native)       │
└───────────┬─────────────┘
            │ HTTP POST + SSE GET + WS
            ▼
┌─────────────────────────┐
│    PawFlow Server        │
│  (same backend)          │
└─────────────────────────┘
```

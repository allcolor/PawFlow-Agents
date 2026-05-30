# PawFlow for VS Code

AI agent chat and code assistant powered by PawFlow. Chat with agents, send editor selections, and manage resources from VS Code.

## Installation

### Release VSIX

1. Download `pawflow-vscode-<version>.vsix` from the PawFlow GitHub release.
2. In VS Code, run **Extensions: Install from VSIX...** from the command palette.
3. Select the downloaded file and reload VS Code when prompted.
4. Set `pawflow.serverUrl` and, when Private Gateway is enabled, `pawflow.gatewayKey`.
5. Run **PawFlow: Login** and open the PawFlow activity bar view.

### Development Host

1. Open the `pawflow-vscode` folder in VS Code.
2. Run `npm ci` and `npm run compile`.
3. Press F5 to launch Extension Development Host.
4. The PawFlow icon appears in the activity bar.

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

## Release Packaging

The release workflow packages the extension as a `.vsix` with `vsce` and uploads it beside PawCode, Relay CLI, Relay Desktop, and the server installer assets. To build locally:

```bash
cd pawflow-vscode
npm ci
npm run compile
npx vsce package --out ../dist/vscode-installers/pawflow-vscode-local.vsix
```

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

# VS Code Extension

The PawFlow VS Code extension brings the shared PawFlow agent runtime into the editor. It connects to the same backend as the web UI and PawCode, and includes a native TypeScript relay for workspace file access.

## Features

- chat sidebar with streaming markdown responses;
- OAuth login through the browser;
- native TypeScript relay, no Python relay required for workspace file access;
- resource panel for agents, skills, tasks, services, variables, and secrets;
- context menus for edit/delete/activate/assign actions;
- forms for creating resources;
- active-agent status display;
- editor selection commands: Explain, Fix, Add Tests, Plan;
- inline image rendering in chat;
- approval dialogs through VS Code notifications;
- diff coloring for file edits;
- session recovery after token expiry.

## Development Install

```bash
cd pawflow-vscode
npm install
npm run compile
```

Open the `pawflow-vscode` folder in VS Code and press `F5` to launch an Extension Development Host.

## Settings

| Setting | Purpose |
|---|---|
| `pawflow.serverUrl` | PawFlow server URL, default `http://localhost:9090`. |
| `pawflow.autoRelay` | Start the workspace relay automatically. |
| `pawflow.allowExec` | Allow shell execution through the relay. |
| `pawflow.pythonPath` | Legacy setting; the current relay is TypeScript-native. |

## Shared Runtime

The extension is a client, not a separate agent implementation. It shares:

- conversations;
- agent definitions;
- resource store;
- auth/session state;
- file store outputs;
- SSE event stream;
- relay-backed tools.

A conversation started in the browser can be resumed from VS Code, and files edited by an agent are visible in the workspace.

## Security Notes

The VS Code relay can expose workspace files and shell execution to PawFlow agents. Keep `pawflow.allowExec` disabled unless you trust the server, the active conversation, and the selected agent/tool permissions.

# VS Code Extension

The PawFlow VS Code extension brings the shared PawFlow agent runtime into the editor. It connects to the same backend as the web UI and PawCode. Relay lifecycle is external: use webchat server resources or the standalone PawFlow Relay client to expose files/desktops.

## Features

- chat sidebar with streaming markdown responses;
- OAuth login through the browser;
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
| `pawflow.serverUrl` | PawFlow server URL, default `http://localhost:PORT`. |
| `pawflow.pythonPath` | Optional Python interpreter path for Python-backed helper commands. |

## Shared Runtime

The extension is a client, not a separate agent implementation. It shares:

- conversations;
- agent definitions;
- resource store;
- auth/session state;
- file store outputs;
- SSE event stream;
- relay-backed tools already linked to the conversation.

A conversation started in the browser can be resumed from VS Code. If that conversation has a linked relay, relay-backed file and shell tools operate through the server-side relay binding, not a VS Code-owned relay process.

## Security Notes

VS Code does not start workspace relays. Expose local files or desktops through the standalone PawFlow Relay client only when you trust the server, the active conversation, and the selected agent/tool permissions.

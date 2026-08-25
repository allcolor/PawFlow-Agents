# PawFlow VS Code Extension

The PawFlow VS Code extension is a first-class PawFlow client, like webchat and PawCode. It connects to the same PawFlow server, conversations, agents, relays, approvals, and resource definitions.

## Install From Release

1. Download `pawflow-vscode-<version>.vsix` from the matching PawFlow GitHub release.
2. In VS Code, open the command palette and run **Extensions: Install from VSIX...**.
3. Select the `.vsix` file and reload VS Code when prompted.
4. Configure `pawflow.serverUrl` in VS Code settings.
5. If Private Gateway is enabled, configure `pawflow.gatewayKey` before logging in.
6. Run **PawFlow: Login**, then open the PawFlow activity bar view.

## Settings

| Setting | Purpose |
|---|---|
| `pawflow.serverUrl` | PawFlow server URL, for example `https://localhost:19990` or your deployed server. |
| `pawflow.gatewayKey` | Private Gateway key used before normal API/auth requests. Leave empty only when the gateway is disabled or not required for the route. |
| `pawflow.pythonPath` | Optional Python interpreter path for helper commands. |
| `pawflow.showCodeLens` | Enables inline Ask PawFlow CodeLens actions. |

## What It Does

- Opens a PawFlow chat sidebar in VS Code.
- Logs in through the same server auth flow as webchat.
- Sends editor selections to PawFlow for explain/fix/add-tests workflows.
- Shows PawFlow resource panels and active agent state.
- Uses server-side relay bindings already attached to the conversation.

The extension does not own relay lifecycle. Install Relay Desktop or Relay CLI separately when a workspace needs filesystem, terminal, screen, desktop, or browser access.

## Workflow proposals

The `/plan` command probes the server with `workflow_proposal_list`. When
enabled, the extension hydrates canonical proposal UiSurface cards and routes
their actions through the shared authenticated action pipeline. New free-form
requests instruct the planner to `propose_workflow`; the legacy PlanStore
history panel is not activated.

Only an explicit disabled/404 response enables the legacy panel and commands.
Other errors remain visible and never trigger a second writer. Proposal and
durable-interaction cards are restored from server state after reload or SSE
reconnect.

## Build The VSIX Locally

```bash
cd pawflow-vscode
npm ci
npm run compile
npx vsce package --out ../dist/vscode-installers/pawflow-vscode-local.vsix
```

The release workflow sets the package version from the release tag and uploads `dist/vscode-installers/*.vsix` as a GitHub release asset.

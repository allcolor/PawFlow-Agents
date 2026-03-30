import * as vscode from 'vscode';
import { AgentAPIClient, acquireGatewayCookie } from './api/client';
import { SSEClient } from './api/sse';
import { PawFlowAuth } from './auth/provider';
import { RelayManager } from './relay/manager';
import { ChatPanelProvider } from './webview/chatPanel';
import { StatusBarProvider } from './statusBar/provider';

let apiClient: AgentAPIClient | undefined;
let sseClient: SSEClient | undefined;
let auth: PawFlowAuth;
let relay: RelayManager;
let statusBar: StatusBarProvider;
let chatProvider: ChatPanelProvider;

export async function activate(context: vscode.ExtensionContext) {
  const config = vscode.workspace.getConfiguration('pawflow');
  const serverUrl = config.get<string>('serverUrl', 'http://localhost:9090');
  const gatewayKey = config.get<string>('gatewayKey', '');

  // Acquire gateway cookie if key is configured
  let gatewayCookie = '';
  if (gatewayKey) {
    try {
      gatewayCookie = await acquireGatewayCookie(serverUrl, gatewayKey);
      if (gatewayCookie) {
        console.log('[PawFlow] Gateway cookie acquired.');
      } else {
        console.warn('[PawFlow] Gateway POST returned no cookie.');
      }
    } catch (e: any) {
      console.warn(`[PawFlow] Gateway cookie acquisition failed: ${e.message}`);
    }
  }

  // Initialize components
  auth = new PawFlowAuth(context);
  relay = new RelayManager(context);
  statusBar = new StatusBarProvider();

  // Try cached auth — validate token before using it
  let session = await auth.getSession(serverUrl);
  if (session) {
    apiClient = new AgentAPIClient(serverUrl, session.token, gatewayCookie);
    apiClient.onAuthExpired(() => { vscode.commands.executeCommand('pawflow.login'); });

    // Validate token with a lightweight API call
    const check = await apiClient.sendAction('get_usage');
    if ((check as any)._auth_expired) {
      // Cached token expired — force re-login before continuing
      console.log('[PawFlow] Cached token expired, triggering login...');
      try {
        session = await auth.login(serverUrl);
        apiClient.setToken(session.token);
      } catch {
        // Login failed or cancelled — continue without auth
        apiClient = undefined;
        session = null;
      }
    }

    if (session && apiClient) {
      sseClient = new SSEClient(serverUrl, session.token, gatewayCookie);
      statusBar.setConnected(session.username);

      // Auto-start relay
      if (config.get<boolean>('autoRelay', true)) {
        const workspaceDir = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
        if (workspaceDir) {
          try {
            await relay.start(apiClient, session.username, workspaceDir,
                              config.get<boolean>('allowExec', true),
                              config.get<string>('dockerImage', ''));
          } catch (e: any) {
            vscode.window.showWarningMessage(`PawFlow relay failed: ${e.message}`);
          }
        }
      }
    }
  }

  // Chat panel
  chatProvider = new ChatPanelProvider(context, () => apiClient, () => sseClient, relay);
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider('pawflow.chatView', chatProvider)
  );

  // Register commands
  context.subscriptions.push(
    vscode.commands.registerCommand('pawflow.openChat', () => {
      vscode.commands.executeCommand('pawflow.chatView.focus');
    }),

    vscode.commands.registerCommand('pawflow.login', async () => {
      try {
        const session = await auth.login(serverUrl);
        if (apiClient) {
          apiClient.setToken(session.token);
        } else {
          apiClient = new AgentAPIClient(serverUrl, session.token, gatewayCookie);
          apiClient.onAuthExpired(() => { vscode.commands.executeCommand('pawflow.login'); });
        }
        if (sseClient) {
          sseClient.disconnect();
        }
        sseClient = new SSEClient(serverUrl, session.token, gatewayCookie);
        statusBar.setConnected(session.username);
        vscode.window.showInformationMessage(`PawFlow: Logged in as ${session.username}`);

        // Start relay after login
        if (config.get<boolean>('autoRelay', true)) {
          const workspaceDir = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
          if (workspaceDir && apiClient) {
            await relay.start(apiClient, session.username, workspaceDir,
                              config.get<boolean>('allowExec', true),
                              config.get<string>('dockerImage', ''));
          }
        }
      } catch (e: any) {
        vscode.window.showErrorMessage(`PawFlow login failed: ${e.message}`);
      }
    }),

    vscode.commands.registerCommand('pawflow.newConversation', () => {
      chatProvider.newConversation();
    }),

    vscode.commands.registerCommand('pawflow.toggleRelay', async () => {
      if (!apiClient) {
        vscode.window.showWarningMessage('PawFlow: Not logged in');
        return;
      }
      if (relay.isRunning) {
        await relay.stop(apiClient);
        vscode.window.showInformationMessage('PawFlow: Relay stopped');
      } else {
        const workspaceDir = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
        if (workspaceDir) {
          await relay.start(apiClient, auth.getUsername(), workspaceDir,
                            config.get<boolean>('allowExec', true),
                            config.get<string>('dockerImage', ''));
          vscode.window.showInformationMessage('PawFlow: Relay started');
        }
      }
    }),

    vscode.commands.registerCommand('pawflow.connectRelay', async (path?: string) => {
      if (!apiClient) {
        vscode.window.showWarningMessage('PawFlow: Not logged in');
        return;
      }
      const dir = path || vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
      if (!dir) {
        vscode.window.showWarningMessage('PawFlow: No workspace folder and no path specified');
        return;
      }
      try {
        await relay.start(apiClient, auth.getUsername(), dir,
                          config.get<boolean>('allowExec', true),
                          config.get<string>('dockerImage', ''));
        vscode.window.showInformationMessage(`PawFlow: Relay connected to ${dir}`);
      } catch (e: any) {
        vscode.window.showErrorMessage(`PawFlow relay failed: ${e.message}`);
      }
    }),

    vscode.commands.registerCommand('pawflow.disconnectRelay', async (path?: string) => {
      if (!apiClient) { return; }
      // If path is specified, only disconnect if current relay matches
      // For now, we disconnect the current relay regardless
      if (relay.isRunning) {
        await relay.stop(apiClient);
        vscode.window.showInformationMessage('PawFlow: Relay disconnected');
      }
    }),

    vscode.commands.registerCommand('pawflow.explainSelection', () => {
      sendSelectionToChat('Explain this code in detail:');
    }),
    vscode.commands.registerCommand('pawflow.fixSelection', () => {
      sendSelectionToChat('Fix any bugs or issues in this code:');
    }),
    vscode.commands.registerCommand('pawflow.addTestsForSelection', () => {
      sendSelectionToChat('Write comprehensive tests for this code:');
    }),
    vscode.commands.registerCommand('pawflow.askAboutSelection', async () => {
      const question = await vscode.window.showInputBox({ prompt: 'Ask about the selected code' });
      if (question) {
        sendSelectionToChat(question);
      }
    }),

    vscode.commands.registerCommand('pawflow.sendFileToChat', (uri: vscode.Uri) => {
      if (uri) {
        chatProvider.attachFile(uri.fsPath);
      }
    }),

    vscode.commands.registerCommand('pawflow.compact', () => {
      chatProvider.sendCommand('compact');
    }),
    vscode.commands.registerCommand('pawflow.plan', async () => {
      const desc = await vscode.window.showInputBox({ prompt: 'Describe what to plan' });
      if (desc) { chatProvider.sendPlan(desc); }
    }),
    vscode.commands.registerCommand('pawflow.runCommand', async () => {
      const cmd = await vscode.window.showInputBox({ prompt: 'Shell command to run on relay' });
      if (cmd) { chatProvider.sendCommand('run', cmd); }
    }),
    vscode.commands.registerCommand('pawflow.switchAgent', async () => {
      if (!apiClient) { return; }
      const data = await apiClient.sendAction('list_agents', {
        conversation_id: chatProvider.getConversationId() || '',
      });
      const agents = (data.agents || []).map((a: any) => a.name);
      const pick = await vscode.window.showQuickPick(agents, { title: 'Select Agent' });
      if (pick) { chatProvider.selectAgent(pick); }
    }),
    vscode.commands.registerCommand('pawflow.switchModel', async () => {
      const model = await vscode.window.showInputBox({ prompt: 'Model name (or "reset")' });
      if (model) { chatProvider.sendCommand('model', model); }
    }),
  );

  // Disposables
  // Forward relay status to chat panel
  relay.onDidChangeStatus((status) => {
    chatProvider.postRelayStatus(status);
    if (status === 'running' || status === 'running-docker') {
      const label = status === 'running-docker'
        ? auth.getUsername() + ' [relay 🐳 ✓]'
        : auth.getUsername() + ' [relay ✓]';
      statusBar.setConnected(label);
    } else {
      statusBar.setError(auth.getUsername() + ' [relay ✗]');
    }
  });

  context.subscriptions.push(statusBar, relay);

  // Show login prompt if not authenticated
  if (!apiClient) {
    const choice = await vscode.window.showInformationMessage(
      'PawFlow: Not logged in. Login now?', 'Login', 'Later'
    );
    if (choice === 'Login') {
      vscode.commands.executeCommand('pawflow.login');
    }
  }
}

function sendSelectionToChat(prefix: string) {
  const editor = vscode.window.activeTextEditor;
  if (!editor) { return; }
  const selection = editor.document.getText(editor.selection);
  const fileName = editor.document.fileName;
  const lang = editor.document.languageId;
  const message = `${prefix}\n\nFile: ${fileName}\n\`\`\`${lang}\n${selection}\n\`\`\``;
  chatProvider.sendMessage(message);
}

export function deactivate() {
  if (relay) { relay.dispose(); }
}

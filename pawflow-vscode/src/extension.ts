import * as vscode from 'vscode';
import { AgentAPIClient, acquireGatewayCookie } from './api/client';
import { SSEClient } from './api/sse';
import { PawFlowAuth } from './auth/provider';
import { ChatPanelProvider } from './webview/chatPanel';
import { StatusBarProvider } from './statusBar/provider';

let apiClient: AgentAPIClient | undefined;
let sseClient: SSEClient | undefined;
let auth: PawFlowAuth;
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
    }
  }

  // Chat panel
  chatProvider = new ChatPanelProvider(context, () => apiClient, () => sseClient);
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
      } catch (e: any) {
        vscode.window.showErrorMessage(`PawFlow login failed: ${e.message}`);
      }
    }),

    vscode.commands.registerCommand('pawflow.newConversation', () => {
      chatProvider.newConversation();
    }),

    vscode.commands.registerCommand('pawflow.toggleRelay', async () => {
      vscode.window.showInformationMessage(
        'PawFlow relays are managed from webchat resources or PawFlow Relay Desktop/CLI.'
      );
    }),

    vscode.commands.registerCommand('pawflow.connectRelay', async () => {
      vscode.window.showInformationMessage(
        'PawFlow relays are managed from webchat resources or PawFlow Relay Desktop/CLI.'
      );
    }),

    vscode.commands.registerCommand('pawflow.disconnectRelay', async () => {
      vscode.window.showInformationMessage('VS Code has no managed PawFlow relay to disconnect.');
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

  context.subscriptions.push(statusBar);

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

export function deactivate() {}

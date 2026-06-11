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
let gatewayCookie = '';

function getSettings(): { serverUrl: string; gatewayKey: string; configured: boolean } {
  const config = vscode.workspace.getConfiguration('pawflow');
  const inspected = config.inspect<string>('serverUrl');
  return {
    serverUrl: config.get<string>('serverUrl', 'http://localhost:9090'),
    gatewayKey: config.get<string>('gatewayKey', ''),
    configured: Boolean(inspected?.globalValue || inspected?.workspaceValue),
  };
}

async function configureServer(): Promise<boolean> {
  const settings = getSettings();
  const serverUrl = await vscode.window.showInputBox({
    title: 'PawFlow server URL',
    prompt: 'Base URL of your PawFlow server',
    value: settings.serverUrl,
    placeHolder: 'https://pawflow.example.org:19990',
    ignoreFocusOut: true,
  });
  if (!serverUrl) { return false; }
  const gatewayKey = await vscode.window.showInputBox({
    title: 'Private gateway key (optional)',
    prompt: 'Leave empty if the private gateway is disabled',
    value: settings.gatewayKey,
    password: true,
    ignoreFocusOut: true,
  });
  if (gatewayKey === undefined) { return false; }
  const config = vscode.workspace.getConfiguration('pawflow');
  await config.update('serverUrl', serverUrl.replace(/\/+$/, ''), vscode.ConfigurationTarget.Global);
  await config.update('gatewayKey', gatewayKey, vscode.ConfigurationTarget.Global);
  return true;
}

export function activate(context: vscode.ExtensionContext) {
  // Initialize components. Commands and views are registered synchronously
  // below — no network call may run before that, so a dead/unreachable
  // server can never leave the extension without its commands.
  auth = new PawFlowAuth(context);
  statusBar = new StatusBarProvider();

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

    vscode.commands.registerCommand('pawflow.configure', async () => {
      if (await configureServer()) {
        void initializeSession();
      }
    }),

    vscode.commands.registerCommand('pawflow.login', async () => {
      try {
        // First login on an unconfigured install: ask for the server first
        // instead of silently sending the user to the localhost default.
        if (!getSettings().configured && !(await configureServer())) { return; }
        const { serverUrl, gatewayKey } = getSettings();
        if (gatewayKey && !gatewayCookie) {
          try { gatewayCookie = await acquireGatewayCookie(serverUrl, gatewayKey); } catch { /* login may still work */ }
        }
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
      const raw = data.agents || {};
      const agents = Array.isArray(raw) ? raw.map((a: any) => a.name || a) : Object.keys(raw);
      const pick = await vscode.window.showQuickPick(agents, { title: 'Select Agent' });
      if (pick) { chatProvider.selectAgent(pick); }
    }),
    vscode.commands.registerCommand('pawflow.switchModel', async () => {
      const model = await vscode.window.showInputBox({ prompt: 'Model name (or "reset")' });
      if (model) { chatProvider.sendCommand('model', model); }
    }),
  );

  context.subscriptions.push(statusBar);

  // Session/network bring-up runs after registration and is fully guarded.
  void initializeSession();
}

async function initializeSession() {
  const { serverUrl, gatewayKey, configured } = getSettings();
  try {
    // Acquire gateway cookie if key is configured
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
  } catch (e: any) {
    // Server unreachable, TLS error, ... — stay usable, login can retry.
    console.warn(`[PawFlow] Session init failed: ${e?.message || e}`);
    apiClient = undefined;
  }

  // Show login prompt if not authenticated
  if (!apiClient) {
    const message = configured
      ? 'PawFlow: Not logged in. Login now?'
      : 'PawFlow: No server configured yet.';
    const choice = await vscode.window.showInformationMessage(
      message, configured ? 'Login' : 'Configure', 'Later'
    );
    if (choice === 'Login') {
      vscode.commands.executeCommand('pawflow.login');
    } else if (choice === 'Configure') {
      vscode.commands.executeCommand('pawflow.configure');
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

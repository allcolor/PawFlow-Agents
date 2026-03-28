import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';
import { AgentAPIClient } from '../api/client';
import { SSEClient } from '../api/sse';
import { RelayManager } from '../relay/manager';
import { SSEEvent, Attachment, ReplyTo } from '../api/types';

export class ChatPanelProvider implements vscode.WebviewViewProvider {
  private view: vscode.WebviewView | undefined;
  private conversationId: string | null = null;
  private selectedAgent: string = '';
  private pendingAttachments: Attachment[] = [];

  constructor(
    private context: vscode.ExtensionContext,
    private getApi: () => AgentAPIClient | undefined,
    private getSse: () => SSEClient | undefined,
    private relay: RelayManager,
  ) {}

  getConversationId(): string | null { return this.conversationId; }

  resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    view.webview.options = {
      enableScripts: true,
      localResourceRoots: [
        vscode.Uri.joinPath(this.context.extensionUri, 'src', 'webview'),
        this.context.extensionUri,
      ],
    };
    view.webview.html = this.getHtml();

    view.webview.onDidReceiveMessage(async (msg) => {
      switch (msg.type) {
        case 'sendMessage':
          await this.sendMessage(msg.text, msg.attachments, msg.reply_to);
          break;
        case 'newConversation':
          this.newConversation();
          break;
        case 'loadConversations':
          await this.loadConversations();
          break;
        case 'resumeConversation':
          await this.resumeConversation(msg.conversationId, msg.offset);
          break;
        case 'approval':
          await this.handleApproval(msg.requestId, msg.result, msg.approvalType);
          break;
        case 'backgroundTool':
          await this.handleBackgroundTool(msg.tcId);
          break;
        case 'reconnectRelay':
          await vscode.commands.executeCommand('pawflow.toggleRelay');
          break;
        case 'relayConnect':
          await vscode.commands.executeCommand('pawflow.connectRelay', msg.path || '');
          break;
        case 'relayDisconnect':
          await vscode.commands.executeCommand('pawflow.disconnectRelay', msg.path || '');
          break;
        case 'openFile':
          // Open a file in VS Code editor from the webview
          try {
            const relayRoot = this.relay?.getRootDir?.() || '';
            let filePath = msg.path || '';
            // Resolve fs://service/path → absolute path
            if (filePath.startsWith('fs://')) {
              const rest = filePath.slice(5);
              const sep = rest.indexOf('/');
              filePath = sep > 0 ? rest.slice(sep + 1) : rest;
            }
            // Make absolute if relay root is known
            if (relayRoot && !require('path').isAbsolute(filePath)) {
              filePath = require('path').join(relayRoot, filePath);
            }
            const uri = vscode.Uri.file(filePath);
            await vscode.window.showTextDocument(uri, { preview: true });
          } catch (e: any) {
            vscode.window.showWarningMessage(`Cannot open file: ${e.message}`);
          }
          break;
        case 'command':
          if (msg.command === 'clipboard_write') {
            await vscode.env.clipboard.writeText(msg.arg || '');
            this.postMessage({ type: 'actionResult', action: 'clipboard_write', data: { ok: true } });
          } else if (msg.command === 'clipboard_read') {
            const text = await vscode.env.clipboard.readText();
            this.postMessage({ type: 'clipboardContent', text });
          } else if (msg.command === 'clear_attachments') {
            this.pendingAttachments = [];
            this.postMessage({ type: 'actionResult', action: 'clear_attachments', data: { ok: true } });
          } else if (msg.command === 'assign_plan_dialog') {
            await this.showAssignPlanDialog(msg.arg);
          } else if (msg.command === 'assign_step_dialog') {
            const parsed = JSON.parse(msg.arg || '{}');
            await this.showAssignStepDialog(parsed.plan_id, parsed.step);
          } else if (msg.command === 'create_plan_dialog') {
            await this.showCreatePlanDialog();
          } else {
            await this.sendCommand(msg.command, msg.arg);
          }
          break;
      }
    });

    this.resumeLastConversation();
    this.setupSSE();
  }

  postRelayStatus(status: string): void {
    this.postMessage({ type: 'relayStatus', status });
  }

  async sendMessage(text: string, attachments?: Attachment[], replyTo?: ReplyTo): Promise<void> {
    const api = this.getApi();
    if (!api) {
      this.postMessage({ type: 'error', message: 'Not logged in. Run PawFlow: Login.' });
      return;
    }

    try {
      const allAttachments = [...this.pendingAttachments, ...(attachments || [])];
      this.pendingAttachments = [];

      const resp = await api.sendMessage({
        message: text,
        conversation_id: this.conversationId || undefined,
        target_agent: this.selectedAgent || undefined,
        attachments: allAttachments.length ? allAttachments : undefined,
        reply_to: replyTo || undefined,
      });
      console.log('[PawFlow] sendMessage response:', JSON.stringify(resp).slice(0, 500));

      if ((resp as any)._auth_expired) {
        this.postMessage({ type: 'error', message: 'Session expired. Use PawFlow: Login command to re-authenticate.' });
        return;
      }
      if (resp.error) {
        this.postMessage({ type: 'error', message: resp.error });
        return;
      }

      if (resp.conversation_id) {
        this.conversationId = resp.conversation_id;
        this.saveLastConversation(resp.conversation_id);
        this.setupSSE();
      }

      this.postMessage({ type: 'messageSent', conversationId: this.conversationId });
    } catch (e: any) {
      this.postMessage({ type: 'error', message: e.message });
    }
  }

  newConversation(): void {
    this.conversationId = null;
    this.selectedAgent = '';
    const sse = this.getSse();
    if (sse) { sse.disconnect(); }
    this.postMessage({ type: 'newConversation' });
  }

  selectAgent(name: string): void {
    this.selectedAgent = name;
    this.postMessage({ type: 'agentSelected', agent: name });
  }

  sendPlan(description: string): void {
    const msg = `[Create a structured plan using the create_plan tool. Analyze the request, identify steps, then call create_plan.]\n\n${description}`;
    this.sendMessage(msg);
  }

  async sendCommand(command: string, arg?: string): Promise<void> {
    const api = this.getApi();
    if (!api) { return; }
    try {
      let params: Record<string, any> = { conversation_id: this.conversationId || '' };
      if (arg) {
        try {
          const parsed = JSON.parse(arg);
          params = { ...params, ...parsed };
        } catch {
          params.agent_name = arg;
        }
      }
      const resp = await api.sendAction(command, params);
      if ((resp as any)._auth_expired) {
        this.postMessage({ type: 'error', message: 'Session expired. Use PawFlow: Login command to re-authenticate.' });
        return;
      }
      this.postMessage({ type: 'actionResult', action: command, data: resp });
    } catch (e: any) {
      this.postMessage({ type: 'error', message: e.message });
    }
  }

  private async showAssignPlanDialog(planId: string): Promise<void> {
    const api = this.getApi();
    if (!api) { return; }

    let agentNames: string[] = [];
    try {
      const data = await api.sendAction('list_agents', { conversation_id: this.conversationId || '' });
      agentNames = Object.keys(data.agents || {});
    } catch {}

    const agent = await vscode.window.showQuickPick(agentNames, {
      title: 'Assign plan to agent',
      placeHolder: 'Select agent',
    });
    if (!agent) { return; }

    const stepRange = await vscode.window.showInputBox({
      title: 'Step range (optional)',
      prompt: 'e.g. 1-3, remaining, or leave empty for full plan',
      placeHolder: 'empty = full plan',
    });
    if (stepRange === undefined) { return; }

    try {
      const resp = await api.sendAction('assign_plan', {
        conversation_id: this.conversationId || '',
        plan_id: planId,
        agent,
        step_range: stepRange || '',
      });
      if (resp.error) {
        vscode.window.showErrorMessage(`Assign failed: ${resp.error}`);
      } else {
        vscode.window.showInformationMessage(`Plan assigned to ${agent}`);
        this.postMessage({ type: 'actionResult', action: 'assign_plan', data: resp });
      }
    } catch (e: any) {
      vscode.window.showErrorMessage(`Assign failed: ${e.message}`);
    }
  }

  private async showAssignStepDialog(planId: string, stepIndex: number): Promise<void> {
    const api = this.getApi();
    if (!api) { return; }

    let agentNames: string[] = [];
    try {
      const data = await api.sendAction('list_agents', { conversation_id: this.conversationId || '' });
      agentNames = Object.keys(data.agents || {});
    } catch {}

    const agent = await vscode.window.showQuickPick(agentNames, {
      title: `Assign step ${stepIndex} to agent`,
      placeHolder: 'Select agent',
    });
    if (!agent) { return; }

    try {
      const resp = await api.sendAction('assign_plan', {
        conversation_id: this.conversationId || '',
        plan_id: planId,
        agent,
        step_range: String(stepIndex),
      });
      if (resp.error) {
        vscode.window.showErrorMessage(`Assign failed: ${resp.error}`);
      } else {
        vscode.window.showInformationMessage(`Step ${stepIndex} assigned to ${agent}`);
        this.postMessage({ type: 'actionResult', action: 'assign_plan', data: resp });
      }
    } catch (e: any) {
      vscode.window.showErrorMessage(`Assign failed: ${e.message}`);
    }
  }

  private async showCreatePlanDialog(): Promise<void> {
    const api = this.getApi();
    if (!api) { return; }

    const title = await vscode.window.showInputBox({
      title: 'Create Plan — Title',
      prompt: 'Plan title',
    });
    if (!title) { return; }

    const stepsText = await vscode.window.showInputBox({
      title: 'Create Plan — Steps',
      prompt: 'Steps separated by semicolons (;)',
      placeHolder: 'Step 1; Step 2; Step 3',
    });
    if (!stepsText) { return; }

    const steps = stepsText.split(';').map(s => s.trim()).filter(Boolean);

    try {
      const resp = await api.sendAction('create_plan_user', {
        conversation_id: this.conversationId || '',
        title,
        steps,
      });
      if (resp.error) {
        vscode.window.showErrorMessage(`Create plan failed: ${resp.error}`);
      } else {
        vscode.window.showInformationMessage(`Plan created: ${title}`);
        this.postMessage({ type: 'actionResult', action: 'create_plan_user', data: resp });
      }
    } catch (e: any) {
      vscode.window.showErrorMessage(`Create plan failed: ${e.message}`);
    }
  }

  attachFile(filePath: string): void {
    try {
      const data = fs.readFileSync(filePath);
      const b64 = data.toString('base64');
      const fileName = path.basename(filePath);
      const mimeTypes: Record<string, string> = {
        '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
        '.gif': 'image/gif', '.svg': 'image/svg+xml', '.pdf': 'application/pdf',
        '.json': 'application/json', '.txt': 'text/plain',
      };
      const ext = path.extname(filePath).toLowerCase();
      const mime = mimeTypes[ext] || 'application/octet-stream';

      this.pendingAttachments.push({ filename: fileName, mime_type: mime, data: b64 });
      this.postMessage({ type: 'fileAttached', filename: fileName, count: this.pendingAttachments.length });
    } catch (e: any) {
      vscode.window.showErrorMessage(`Failed to attach: ${e.message}`);
    }
  }

  private async resumeLastConversation(): Promise<void> {
    const lastCid = this.context.globalState.get<string>('pawflow.lastConversationId');
    if (!lastCid) { return; }
    let api = this.getApi();
    for (let attempt = 0; !api && attempt < 10; attempt++) {
      await new Promise(r => setTimeout(r, 500));
      api = this.getApi();
    }
    if (!api) {
      console.log('[PawFlow] No API client after waiting — skipping resume');
      return;
    }
    try {
      const data = await api.sendAction('load_history', {
        conversation_id: lastCid, limit: 50, offset: 0,
      });
      if (!data.error) {
        this.conversationId = lastCid;
        this.saveLastConversation(lastCid);
        this.setupSSE();
        this.postMessage({ type: 'history', data });
        console.log(`[PawFlow] Resumed last conversation: ${lastCid.slice(0, 8)}`);
      }
    } catch (e) {
      console.log('[PawFlow] Could not resume last conversation:', e);
    }
  }

  private saveLastConversation(cid: string): void {
    if (cid) {
      this.context.globalState.update('pawflow.lastConversationId', cid);
    }
  }

  private async loadConversations(): Promise<void> {
    const api = this.getApi();
    if (!api) {
      this.postMessage({ type: 'error', message: 'Not logged in' });
      return;
    }
    try {
      const data = await api.sendAction('list_conversations');
      console.log('[PawFlow] list_conversations response:', JSON.stringify(data).slice(0, 500));
      this.postMessage({ type: 'conversationList', conversations: data.conversations || [] });
    } catch (e: any) {
      console.error('[PawFlow] list_conversations error:', e);
      this.postMessage({ type: 'error', message: `Failed to load conversations: ${e.message}` });
    }
  }

  private async resumeConversation(cid: string, offset?: number): Promise<void> {
    const api = this.getApi();
    if (!api) { return; }
    try {
      const data = await api.sendAction('load_history', {
        conversation_id: cid, limit: 50, offset: offset || 0,
      });
      if (data.error) {
        console.error('[PawFlow] load_history error:', data.error);
        this.postMessage({ type: 'error', message: data.error });
      } else {
        this.conversationId = cid;
        this.saveLastConversation(cid);
        this.setupSSE();
        const isLoadMore = (offset || 0) > 0;
        this.postMessage({ type: 'history', data, append: isLoadMore });
      }
    } catch (e) {
      console.error('[PawFlow] resumeConversation failed:', e);
    }
  }

  private async handleApproval(requestId: string, result: string, type: string): Promise<void> {
    const api = this.getApi();
    if (!api) { return; }
    const action = type === 'exec' ? 'exec_result' : 'tool_approval_result';
    await api.sendAction(action, {
      request_id: requestId,
      result: { choice: result },
      conversation_id: this.conversationId || '',
    });
  }

  private _sseConversationId: string | null = null;

  private setupSSE(): void {
    if (!this.conversationId) { return; }
    const sse = this.getSse();
    if (!sse) { return; }

    if (this._sseConversationId === this.conversationId && sse.isConnected()) { return; }

    sse.disconnect();
    sse.removeAllListeners();
    this._sseConversationId = this.conversationId;
    sse.on('event', (event: SSEEvent) => {
      this.postMessage({ type: 'sseEvent', event });

      if (event.event === 'exec_approval_request' || event.event === 'tool_approval_request') {
        this.showApprovalNotification(event);
      }

      if (event.event === 'tool_result' && ['edit', 'write', 'bash'].includes(event.data.tool)) {
        const result = (event.data.result || '') as string;
        if (result.includes('replacement') || result.includes('Edited ') || result.includes('Written ')) {
          const pathMatch = result.match(/(?:to |in |path=)(\S+)/);
          if (pathMatch) {
            const filePath = pathMatch[1];
            const uri = vscode.Uri.file(filePath);
            vscode.workspace.textDocuments.forEach(doc => {
              if (doc.uri.fsPath === uri.fsPath) {
                vscode.commands.executeCommand('workbench.action.files.revert');
              }
            });
          }
        }
      }
    });
    sse.connect(this.conversationId);
  }

  private async showAssignTaskDialog(taskName: string): Promise<void> {
    const api = this.getApi();
    if (!api) { return; }

    let agentNames: string[] = [];
    try {
      const data = await api.sendAction('list_agents', { conversation_id: this.conversationId || '' });
      agentNames = (data.agents || []).map((a: any) => a.name || a);
    } catch {}

    const agent = await vscode.window.showQuickPick(agentNames, {
      title: `Assign "${taskName}" — Step 1/4: Agent`,
      placeHolder: 'Select agent',
    });
    if (!agent) { return; }

    const contextPick = await vscode.window.showQuickPick([
      { label: 'isolated', description: 'Only task prompt (default)' },
      { label: 'last:10', description: 'Last 10 messages from conversation' },
      { label: 'last:20', description: 'Last 20 messages' },
      { label: 'last:50', description: 'Last 50 messages' },
      { label: 'summary:2000', description: 'Summary ~2000 tokens' },
      { label: 'summary:4000', description: 'Summary ~4000 tokens' },
      { label: 'full', description: 'Entire conversation context' },
    ], {
      title: `Assign "${taskName}" — Step 2/4: Context`,
      placeHolder: 'What context should the agent receive?',
    });
    if (!contextPick) { return; }

    const interval = await vscode.window.showInputBox({
      title: `Assign "${taskName}" — Step 3/4: Interval`,
      prompt: 'Repeat interval (e.g. 6/1m = 6 times every 1min, 2/1h, 60 = every 60s). Leave empty for one-shot.',
      placeHolder: 'e.g. 6/1m, 2/1h, 60',
    });
    if (interval === undefined) { return; }

    const varsInput = await vscode.window.showInputBox({
      title: `Assign "${taskName}" — Step 4/4: Variables`,
      prompt: 'Variables as key=value pairs separated by commas. Leave empty for none.',
      placeHolder: 'e.g. nbr_images=20, style=cyberpunk',
    });
    if (varsInput === undefined) { return; }

    const variables: Record<string, string> = {};
    if (varsInput) {
      for (const pair of varsInput.split(',')) {
        const eq = pair.indexOf('=');
        if (eq > 0) {
          variables[pair.slice(0, eq).trim()] = pair.slice(eq + 1).trim();
        }
      }
    }

    try {
      const params: Record<string, any> = {
        conversation_id: this.conversationId || '',
        agent_name: agent,
        task_name: taskName,
        context: contextPick.label,
      };
      if (interval) { params.interval = interval; }
      if (Object.keys(variables).length) { params.variables = variables; }

      const resp = await api.sendAction('assign_task', params);
      if (resp.error) {
        vscode.window.showErrorMessage(`Assign failed: ${resp.error}`);
      } else {
        const details = [agent, contextPick.label, interval || 'one-shot'].filter(Boolean).join(', ');
        vscode.window.showInformationMessage(`Task "${taskName}" assigned: ${details}`);
      }
    } catch (e: any) {
      vscode.window.showErrorMessage(`Assign failed: ${e.message}`);
    }
  }

  private async handleBackgroundTool(tcId: string): Promise<void> {
    const api = this.getApi();
    if (!api || !tcId) { return; }
    try {
      await api.sendAction('background_tool', {
        tc_id: tcId,
        conversation_id: this.conversationId || '',
      });
    } catch (e: any) {
      this.postMessage({ type: 'error', message: `Background tool failed: ${e.message}` });
    }
  }

  private async showApprovalNotification(event: SSEEvent): Promise<void> {
    const isExec = event.event === 'exec_approval_request';
    const title = isExec
      ? `Execute: ${event.data.command}`
      : `Tool: ${event.data.tool_name} — ${event.data.action_summary}`;

    const choice = await vscode.window.showWarningMessage(
      `PawFlow Approval: ${title}`,
      'Allow', 'Deny', 'Always Allow'
    );

    const resultMap: Record<string, string> = {
      'Allow': isExec ? 'approved' : 'allow_once',
      'Deny': 'denied',
      'Always Allow': 'always_allow',
    };
    const result = resultMap[choice || 'Deny'] || 'denied';
    await this.handleApproval(event.data.request_id, result, isExec ? 'exec' : 'tool');
  }

  private postMessage(msg: any): void {
    this.view?.webview.postMessage(msg);
  }

  private getHtml(): string {
    const webview = this.view!.webview;
    const extensionUri = this.context.extensionUri;
    // Cache-buster forces service worker to re-fetch on each reload (dev mode)
    const v = `?v=${Date.now()}`;

    const styleUri = webview.asWebviewUri(
      vscode.Uri.joinPath(extensionUri, 'src', 'webview', 'styles.css')
    ) + v;
    const chatUri = webview.asWebviewUri(
      vscode.Uri.joinPath(extensionUri, 'src', 'webview', 'chat.js')
    ) + v;
    const commandsUri = webview.asWebviewUri(
      vscode.Uri.joinPath(extensionUri, 'src', 'webview', 'commands.js')
    ) + v;
    const panelsUri = webview.asWebviewUri(
      vscode.Uri.joinPath(extensionUri, 'src', 'webview', 'panels.js')
    ) + v;
    const formsUri = webview.asWebviewUri(
      vscode.Uri.joinPath(extensionUri, 'src', 'webview', 'forms.js')
    ) + v;

    return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="stylesheet" href="${styleUri}">
</head>
<body>
<div class="toolbar">
  <button id="tbChat" class="active" onclick="backToChat()">Chat</button>
  <button id="tbConvs" onclick="loadConvs()">Conversations</button>
  <button onclick="newChat()">+ New</button>
</div>
<div class="toolbar-row2">
  <button onclick="showPanel('resources')" title="Resources">&#128218; Resources</button>
  <button onclick="showPanel('context')" title="LLM Context">&#128065; Context</button>
  <button onclick="showPanel('files')" title="Files">&#128196; Files</button>
  <button onclick="showPanel('tools')" title="Tools">&#128295; Tools</button>
  <button onclick="showPanel('accounts')" title="Linked Accounts">&#128279; Accounts</button>
  <button onclick="showPanel('plans')" title="Plans">&#128203; Plans</button>
  <span class="relay-badge" id="relayBadge" oncontextmenu="relayContextMenu(event)"><span class="relay-dot off" id="relayDot"></span> <span id="relayLabel">Relay</span></span>
</div>
<div id="activeAgents" style="display:none;padding:2px 8px;border-bottom:1px solid var(--vscode-panel-border);font-size:10px;color:var(--vscode-descriptionForeground)"></div>
<div style="position:relative;flex:1;display:flex;flex-direction:column;overflow:hidden;min-height:0">
  <div class="messages" id="messages">
    <div class="msg system">PawFlow — Type a message to start</div>
  </div>
  <div class="panel-overlay" id="panelOverlay"></div>
</div>
<div id="replyBar" class="reply-bar" style="display:none"></div>
<div id="status" class="status"></div>
<div class="input-area">
  <textarea id="input" rows="1" placeholder="Type a message... (Enter to send, Shift+Enter for newline)"
    onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();send()}else if(event.key==='Escape'){event.preventDefault();sendCmd('interrupt','')}"></textarea>
  <button onclick="send()">Send</button>
</div>
<script src="${chatUri}"></script>
<script src="${commandsUri}"></script>
<script src="${panelsUri}"></script>
<script src="${formsUri}"></script>
</body></html>`;
  }
}

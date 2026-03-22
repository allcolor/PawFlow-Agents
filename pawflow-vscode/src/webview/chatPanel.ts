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
      localResourceRoots: [this.context.extensionUri],
    };
    view.webview.html = this.getHtml();

    // Handle messages from webview
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
        // assignTaskDialog handled in webview JS now (showAssignForm)
      }
    });

    // Resume last conversation on startup
    this.resumeLastConversation();

    // Connect SSE if we have a conversation
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
      // arg can be a JSON string with extra params
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
    // Wait for API client to be ready (login is async, may not be done yet)
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
        this.postMessage({ type: 'history', data });
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
      result,
      conversation_id: this.conversationId || '',
    });
  }

  private _sseConversationId: string | null = null;

  private setupSSE(): void {
    if (!this.conversationId) { return; }
    const sse = this.getSse();
    if (!sse) { return; }

    // Skip if already connected to this conversation
    if (this._sseConversationId === this.conversationId && sse.isConnected()) { return; }

    // Disconnect old connection first
    sse.disconnect();
    sse.removeAllListeners();
    this._sseConversationId = this.conversationId;
    sse.on('event', (event: SSEEvent) => {
      this.postMessage({ type: 'sseEvent', event });

      // Show approval as VSCode notification (visible even if chat is hidden)
      if (event.event === 'exec_approval_request' || event.event === 'tool_approval_request') {
        this.showApprovalNotification(event);
      }

      // Detect file edits and show inline diff / refresh open editors
      if (event.event === 'tool_result' && event.data.tool === 'filesystem') {
        const result = (event.data.result || '') as string;
        if (result.includes('replacement') || result.includes('Edited ') || result.includes('Written ')) {
          const pathMatch = result.match(/(?:to |in |path=)(\S+)/);
          if (pathMatch) {
            const filePath = pathMatch[1];
            const uri = vscode.Uri.file(filePath);
            // Refresh the file in editor if it's open
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

    // Step 1: Pick agent
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

    // Step 2: Pick context mode
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

    // Step 3: Interval (optional)
    const interval = await vscode.window.showInputBox({
      title: `Assign "${taskName}" — Step 3/4: Interval`,
      prompt: 'Repeat interval (e.g. 6/1m = 6 times every 1min, 2/1h, 60 = every 60s). Leave empty for one-shot.',
      placeHolder: 'e.g. 6/1m, 2/1h, 60',
    });
    if (interval === undefined) { return; } // cancelled

    // Step 4: Variables (optional)
    const varsInput = await vscode.window.showInputBox({
      title: `Assign "${taskName}" — Step 4/4: Variables`,
      prompt: 'Variables as key=value pairs separated by commas. Leave empty for none.',
      placeHolder: 'e.g. nbr_images=20, style=cyberpunk',
    });
    if (varsInput === undefined) { return; } // cancelled

    // Parse variables
    const variables: Record<string, string> = {};
    if (varsInput) {
      for (const pair of varsInput.split(',')) {
        const eq = pair.indexOf('=');
        if (eq > 0) {
          variables[pair.slice(0, eq).trim()] = pair.slice(eq + 1).trim();
        }
      }
    }

    // Assign
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
    return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
html, body { height: 100%; overflow: hidden; }
body { font-family: var(--vscode-font-family); background: var(--vscode-editor-background); color: var(--vscode-editor-foreground); display: flex; flex-direction: column; }
.toolbar { display: flex; gap: 4px; padding: 4px; border-bottom: 1px solid var(--vscode-panel-border); }
.toolbar button { background: var(--vscode-button-secondaryBackground); color: var(--vscode-button-secondaryForeground); border: none; padding: 3px 8px; border-radius: 3px; cursor: pointer; font-size: 11px; border-bottom: 2px solid transparent; }
.toolbar button:hover { background: var(--vscode-button-secondaryHoverBackground); }
.toolbar button.active { border-bottom-color: var(--vscode-textLink-foreground); color: var(--vscode-textLink-foreground); }
.messages { flex: 1; overflow-y: auto; padding: 8px; min-height: 0; }
.msg { margin-bottom: 8px; padding: 6px 8px; border-radius: 6px; font-size: 13px; line-height: 1.5; }
.msg.user { background: var(--vscode-input-background); border: 1px solid var(--vscode-input-border); }
.msg.assistant { background: var(--vscode-textBlockQuote-background); border-left: 3px solid var(--vscode-textLink-foreground); }
.msg.tool { font-size: 11px; color: var(--vscode-descriptionForeground); padding: 3px 8px; }
.msg.tool_call { font-size: 11px; color: var(--vscode-descriptionForeground); padding: 3px 8px; border-left: 2px solid #f4a261; }
.msg.tool_result { font-size: 11px; color: var(--vscode-descriptionForeground); padding: 3px 8px; border-left: 2px solid #3fb950; }
.msg.system { font-size: 11px; color: var(--vscode-descriptionForeground); text-align: center; }
.msg.error { color: var(--vscode-errorForeground); }
.agent-badge { display: inline-block; padding: 1px 6px; border-radius: 4px; font-size: 10px; font-weight: 600; margin-right: 4px; color: white; }
.status { font-size: 11px; color: var(--vscode-descriptionForeground); padding: 4px 8px; text-align: center; }
.input-area { display: flex; gap: 4px; padding: 4px; border-top: 1px solid var(--vscode-panel-border); }
.input-area textarea { flex: 1; background: var(--vscode-input-background); color: var(--vscode-input-foreground); border: 1px solid var(--vscode-input-border); border-radius: 4px; padding: 6px; font-family: var(--vscode-font-family); font-size: 13px; resize: none; min-height: 36px; max-height: 120px; }
.input-area button { background: var(--vscode-button-background); color: var(--vscode-button-foreground); border: none; padding: 6px 12px; border-radius: 4px; cursor: pointer; font-size: 12px; }
.approval { background: var(--vscode-inputValidation-warningBackground); border: 1px solid var(--vscode-inputValidation-warningBorder); padding: 8px; border-radius: 6px; margin: 4px 0; }
.approval button { margin: 2px; padding: 3px 10px; border: none; border-radius: 3px; cursor: pointer; font-size: 11px; }
pre { background: var(--vscode-textCodeBlock-background); padding: 8px; border-radius: 4px; overflow-x: auto; font-size: 12px; }
code { font-family: var(--vscode-editor-font-family); }
.diff { font-size: 11px; background: var(--vscode-textCodeBlock-background); padding: 6px; border-radius: 4px; overflow-x: auto; }
.diff-add { color: #3fb950; }
.diff-del { color: #f85149; }
.diff-hunk { color: #58a6ff; }
.diff-ctx { color: var(--vscode-descriptionForeground); }
.thinking { color: var(--vscode-descriptionForeground); font-style: italic; animation: pulse 2s infinite; }
.msg-actions { float: right; display: none; gap: 2px; }
.msg:hover .msg-actions { display: inline-flex; }
.msg-actions button { background: none; border: none; cursor: pointer; font-size: 11px; color: var(--vscode-descriptionForeground); padding: 0 3px; }
.msg-actions button:hover { color: var(--vscode-editor-foreground); }
.reply-bar { background: var(--vscode-textBlockQuote-background); border-top: 1px solid var(--vscode-panel-border); padding: 4px 12px; display: flex; align-items: center; gap: 8px; font-size: 11px; color: var(--vscode-descriptionForeground); }
.reply-bar .reply-close { cursor: pointer; margin-left: auto; color: var(--vscode-errorForeground); font-size: 14px; background: none; border: none; }
.reply-quote { font-size: 10px; color: var(--vscode-descriptionForeground); border-left: 2px solid var(--vscode-textLink-foreground); padding: 2px 6px; margin-bottom: 4px; cursor: pointer; }
.reply-quote:hover { background: var(--vscode-list-hoverBackground); }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
.load-more { text-align: center; padding: 8px; color: var(--vscode-textLink-foreground); cursor: pointer; font-size: 12px; }
.load-more:hover { text-decoration: underline; }
.token-footer { font-size: 10px; color: var(--vscode-descriptionForeground); margin-top: 4px; }
.toolbar-row2 { display: flex; gap: 3px; padding: 2px 4px; border-bottom: 1px solid var(--vscode-panel-border); flex-wrap: wrap; }
.toolbar-row2 button { background: none; color: var(--vscode-descriptionForeground); border: none; padding: 2px 6px; cursor: pointer; font-size: 10px; border-radius: 3px; }
.toolbar-row2 button:hover { background: var(--vscode-button-secondaryHoverBackground); color: var(--vscode-editor-foreground); }
.toolbar-row2 .active { color: var(--vscode-textLink-foreground); }
.relay-badge { display: inline-flex; align-items: center; gap: 3px; font-size: 10px; margin-left: auto; }
.relay-dot { width: 6px; height: 6px; border-radius: 50%; display: inline-block; }
.relay-dot.on { background: #3fb950; }
.relay-dot.off { background: #f85149; }
.panel-overlay { display: none; position: absolute; top: 0; left: 0; right: 0; bottom: 0; background: var(--vscode-editor-background); z-index: 10; overflow-y: auto; padding: 8px; }
.panel-overlay.visible { display: block; }
.panel-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.panel-header h4 { margin: 0; font-size: 13px; }
.panel-close { background: none; border: none; color: var(--vscode-descriptionForeground); cursor: pointer; font-size: 16px; }
.panel-item { padding: 4px 6px; font-size: 11px; border-bottom: 1px solid var(--vscode-panel-border); cursor: context-menu; }
.panel-item:hover { background: var(--vscode-list-hoverBackground); }
.res-section { padding: 6px; cursor: pointer; font-size: 12px; user-select: none; border-bottom: 1px solid var(--vscode-panel-border); }
.res-section:hover { background: var(--vscode-list-hoverBackground); }
.res-arrow { font-size: 10px; transition: transform 0.2s; display: inline-block; }
.res-section.collapsed .res-arrow { transform: rotate(-90deg); }
.res-section.collapsed + .res-items { display: none; }
.res-items { }
.res-ctx { position: fixed; z-index: 100; background: var(--vscode-menu-background); border: 1px solid var(--vscode-menu-border); border-radius: 4px; padding: 2px 0; min-width: 140px; box-shadow: 0 2px 8px rgba(0,0,0,0.3); }
.res-ctx div { padding: 4px 12px; font-size: 11px; cursor: pointer; color: var(--vscode-menu-foreground); }
.res-ctx div:hover { background: var(--vscode-menu-selectionBackground); color: var(--vscode-menu-selectionForeground); }
.res-ctx hr { border: none; border-top: 1px solid var(--vscode-menu-separatorBackground); margin: 2px 0; }
.sub-trace { border-left: 2px solid #f4a261; margin: 4px 0; border-radius: 4px; font-size: 11px; }
.sub-trace-header { padding: 4px 8px; cursor: pointer; color: var(--vscode-descriptionForeground); display: flex; align-items: center; gap: 4px; }
.sub-trace-header:hover { background: var(--vscode-list-hoverBackground); }
.sub-trace-body { display: none; padding: 2px 8px 4px 16px; color: var(--vscode-descriptionForeground); font-size: 10px; }
.sub-trace-body.open { display: block; }
.trace-entry { padding: 1px 0; }
.trace-entry.tool { color: #f4a261; }
.trace-entry.done { color: #3fb950; }
.trace-content { margin-top: 4px; padding: 4px; background: var(--vscode-textBlockQuote-background); border-radius: 3px; white-space: pre-wrap; font-size: 11px; color: var(--vscode-editor-foreground); }
</style>
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
  <span class="relay-badge"><span class="relay-dot off" id="relayDot"></span> <span id="relayLabel">Relay</span></span>
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
    onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();send()}"></textarea>
  <button onclick="send()">Send</button>
</div>
<script>
const vscode = acquireVsCodeApi();
const messagesEl = document.getElementById('messages');
const inputEl = document.getElementById('input');
const statusEl = document.getElementById('status');
let streaming = {};
let currentHistoryConvId = null;
let currentHistoryOffset = 0;
var _hadToolCalls = false;
var _lastToolCall = '';
var activeAgents = {};
var _resData = null;
var _replyTo = null; // {raw_index, role, agent, text_preview}
var _msgRawIndex = 0; // tracks raw message index for reply-to

function setReplyTo(btn) {
  var msgEl = btn.closest('.msg');
  if (!msgEl) return;
  var rawIndex = parseInt(msgEl.dataset.rawIndex || '-1');
  var rawText = msgEl.dataset.rawText || '';
  var isUser = msgEl.classList.contains('user');
  var badge = msgEl.querySelector('.agent-badge');
  var agent = badge ? badge.textContent.trim() : (isUser ? 'User' : 'assistant');
  _replyTo = { raw_index: rawIndex, role: isUser ? 'user' : 'assistant', agent: agent, text_preview: rawText.substring(0, 200) };
  var bar = document.getElementById('replyBar');
  bar.innerHTML = '\\u21a9 <strong>' + esc(agent) + '</strong>: "' + esc(rawText.substring(0, 80)) + '..."'
    + '<button class="reply-close" onclick="cancelReply()">\\u2715</button>';
  bar.style.display = 'flex';
  inputEl.focus();
}

function cancelReply() {
  _replyTo = null;
  var bar = document.getElementById('replyBar');
  if (bar) bar.style.display = 'none';
}

function scrollToMsg(rawIndex) {
  var msgs = document.querySelectorAll('.msg[data-raw-index]');
  for (var i = 0; i < msgs.length; i++) {
    if (msgs[i].dataset.rawIndex === String(rawIndex)) {
      msgs[i].scrollIntoView({ behavior: 'smooth', block: 'center' });
      msgs[i].style.outline = '2px solid var(--vscode-textLink-foreground)';
      setTimeout(function() { msgs[i].style.outline = ''; }, 2000);
      return;
    }
  }
}

function updateActiveAgents(agent, status) {
  if (!agent) { console.error('[BUG] updateActiveAgents called with empty agent name, status=' + status); return; }
  if (status === 'done' || status === 'cancelled') {
    delete activeAgents[agent];
  } else {
    activeAgents[agent] = { status: status, ts: Date.now() };
  }
  _renderActiveAgents();
}
function _renderActiveAgents() {
  // Purge stale entries (>5 min without update)
  var now = Date.now();
  for (var k in activeAgents) {
    if (now - (activeAgents[k].ts || 0) > 300000) delete activeAgents[k];
  }
  var el = document.getElementById('activeAgents');
  var keys = Object.keys(activeAgents);
  if (keys.length === 0) {
    el.style.display = 'none';
  } else {
    el.style.display = 'flex';
    el.innerHTML = keys.map(function(a) {
      var color = agentColor(a);
      var s = activeAgents[a].status || '';
      return '<span style="margin-right:10px;display:inline-flex;align-items:center;gap:3px">'
        + '<span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:' + color + '"></span>'
        + '<strong>' + esc(a) + '</strong> ' + esc(s)
        + '</span>';
    }).join('');
  }
}

function deleteMsg(btn) {
  var msgEl = btn.closest('.msg');
  var rawIndex = msgEl.dataset.rawIndex;
  var index = rawIndex !== undefined ? parseInt(rawIndex) : Array.from(messagesEl.children).indexOf(msgEl);
  vscode.postMessage({ type: 'command', command: 'delete_message', arg: JSON.stringify({ index: index }) });
  msgEl.remove();
}

const FUN_VERBS = ['Refactoring','Compiling','Debugging','Contemplating','Bamboozling',
  'Rickrolling','Skedaddling','Philosophizing','Defenestrating','Hocus-pocusing'];
function randomVerb() { return FUN_VERBS[Math.floor(Math.random() * FUN_VERBS.length)]; }

const AGENT_COLORS = ['#4ecdc4','#4fc3f7','#ab47bc','#f4a261','#e94560','#3fb950','#58a6ff','#d4a373'];
function agentColor(name) {
  let h = 0;
  for (let i = 0; i < name.length; i++) h += name.charCodeAt(i);
  return AGENT_COLORS[h % AGENT_COLORS.length];
}

var COMMANDS = {
  // === Client-side commands ===
  '/new':         { handler: 'newChat' },
  '/conv':        { handler: 'loadConvs' },
  '/clear':       { handler: 'clearChat' },
  '/help':        { handler: 'showHelp' },

  // === Action-based commands (call sendCmd) ===
  // Context
  '/compact':       { action: 'compact', argName: 'agent_name' },
  '/rebuild':       { action: 'rebuild', argName: 'agent_name' },
  '/rebuild-full':  { action: 'rebuild_full', argName: 'agent_name' },
  '/rebuild_clean': { action: 'rebuild_full', argName: 'agent_name' },
  '/restart':       { action: 'restart_from', parser: 'restartParser' },
  '/restart_from':  { action: 'restart_from', parser: 'restartParser' },
  '/summary':       { action: 'resume_conversation', parser: 'summaryParser' },
  '/context':       { action: 'get_context', argName: 'agent_name' },

  // Model / LLM
  '/model':       { action: 'model', argName: 'model' },
  '/llm':         { action: 'set_llm_service', parser: 'llmParser' },
  '/set_llm_service': { action: 'set_llm_service', parser: 'llmParser' },

  // Resources (simple action)
  '/resources':   { action: 'list_resources' },
  '/tools':       { action: 'list_tools' },
  '/secrets':     { action: 'list_secrets' },
  '/list-secrets': { action: 'list_secrets' },
  '/variables':   { action: 'list_variables' },
  '/list-variables': { action: 'list_variables' },
  '/vars':        { action: 'list_variables' },
  '/cost':        { action: 'cost', argName: 'agent' },
  '/usage':       { action: 'cost', argName: 'agent' },
  '/files':       { action: 'list_conv_files' },

  // Agent
  '/agent':       { parser: 'agentParser' },
  '/msg':         { parser: 'msgParser' },
  '/btw':         { parser: 'btwParser' },
  '/stop':        { parser: 'stopParser' },
  '/resume':      { parser: 'resumeParser' },
  '/setname':     { parser: 'setnameParser' },

  // Resources with subcommands
  '/skill':       { parser: 'skillParser' },
  '/task':        { parser: 'taskParser' },
  '/service':     { parser: 'serviceParser' },
  '/flow':        { parser: 'flowParser' },
  '/prompt':      { parser: 'promptParser' },
  '/memory':      { parser: 'memoryParser' },
  '/schedules':   { parser: 'schedulesParser' },
  '/link':        { parser: 'linkParser' },
  '/autoconv':    { parser: 'autoconvParser' },
  '/vidservice':  { parser: 'mediaServiceParser', mediaType: 'video' },
  '/imgservice':  { parser: 'mediaServiceParser', mediaType: 'image' },

  // Activation
  '/activate':    { action: 'activate_resource', parser: 'activateParser' },
  '/deactivate':  { action: 'deactivate_resource', parser: 'activateParser' },
  '/share':       { parser: 'shareParser' },

  // Secrets & Variables
  '/add-secret':  { parser: 'addSecretParser' },
  '/add-variable': { parser: 'addVariableParser' },
  '/add-var':     { parser: 'addVariableParser' },

  // File / Dev tools
  '/upload':      { handler: 'triggerUpload' },
  '/copy':        { handler: 'copyLastMsg' },
  '/paste':       { handler: 'pasteClipboard' },
  '/view':        { parser: 'viewParser' },
  '/call':        { parser: 'callParser' },
  '/install':     { handler: 'showInstallHelp' },
  '/uninstall':   { parser: 'uninstallParser' },
  '/run':         { parser: 'runParser' },
  '/diff':        { parser: 'diffParser' },
  '/plan':        { parser: 'planParser' },
  '/watch':       { handler: 'showWatchNotAvailable' },
  '/clear-files': { handler: 'clearAttachments' },
  '/detach':      { handler: 'clearAttachments' },

  // Conversation
  '/history':     { parser: 'historyParser' },
  '/export':      { parser: 'exportParser' },
  '/rename':      { parser: 'renameParser' },
  '/delete':      { parser: 'deleteParser' },
  '/delete-msg':  { parser: 'deleteMsgParser' },
  '/search':      { parser: 'searchParser' },

  // Session
  '/login':       { handler: 'showLoginMsg' },
  '/quit':        { handler: 'showQuitMsg' },
  '/exit':        { handler: 'showQuitMsg' },
};

// Client-side handler functions
function clearChat() { messagesEl.innerHTML = ''; }
function showHelp() {
  var cmds = Object.keys(COMMANDS).filter(function(c) { return c.charAt(1) !== '_' && !c.includes('_clean'); }).sort();
  var lines = ['<b>Available commands:</b><br>'];
  for (var i = 0; i < cmds.length; i++) {
    lines.push('<code>' + esc(cmds[i]) + '</code>');
  }
  var el = addMsg('system', '');
  el.innerHTML = lines.join(' ');
}
function triggerUpload() { addMsg('system', 'Drag & drop files into the chat or use the file attach button.'); }
function copyLastMsg(arg) {
  var msgs = document.querySelectorAll('.msg.assistant');
  if (!msgs.length) { addMsg('system', 'No responses to copy.'); return; }
  var n = parseInt(arg) || 1;
  var target = msgs[msgs.length - n];
  if (!target) { addMsg('system', 'Only ' + msgs.length + ' responses available.'); return; }
  var txt = target.textContent || '';
  vscode.postMessage({ type: 'command', command: 'clipboard_write', arg: txt });
  addMsg('system', 'Copied ' + txt.length + ' chars.');
}
function pasteClipboard() { vscode.postMessage({ type: 'command', command: 'clipboard_read' }); }
function showInstallHelp() { addMsg('system', 'To install a tool, drag & drop a .py file into the chat.'); }
function showWatchNotAvailable() { addMsg('system', '/watch is not available in VSCode. Use the CLI.'); }
function clearAttachments() { vscode.postMessage({ type: 'command', command: 'clear_attachments' }); addMsg('system', 'Attachments cleared.'); }
function showLoginMsg() { addMsg('system', 'Use the PawFlow: Login command from the command palette (Ctrl+Shift+P).'); }
function showQuitMsg() { addMsg('system', '/quit is not applicable in VSCode.'); }

// Parsers for complex commands
function restartParser(parts, text) {
  var agent = '', keep = 5;
  for (var i = 1; i < parts.length; i++) {
    var v = parseInt(parts[i]);
    if (!isNaN(v)) keep = v;
    else agent = parts[i];
  }
  var p = { keep_last: keep };
  if (agent) p.agent_name = agent;
  return p;
}
function summaryParser(parts, text) {
  var agent = '', tokens = 500;
  for (var i = 1; i < parts.length; i++) {
    var v = parseInt(parts[i]);
    if (!isNaN(v)) tokens = v;
    else agent = parts[i];
  }
  var p = { max_tokens: tokens };
  if (agent) p.agent_name = agent;
  return p;
}
function llmParser(parts, text) {
  if (parts.length < 3) { addMsg('system', 'Usage: /llm <agent> <service>'); return null; }
  return { agent_name: parts[1], llm_service: parts.slice(2).join(' ') };
}
function agentParser(parts, text) {
  var sub = (parts[1] || 'list').toLowerCase();
  if (sub === 'list') return { _action: 'list_agents' };
  if (sub === 'select' || (sub !== 'create' && sub !== 'delete' && sub !== 'msg' && sub !== 'btw' && sub !== 'interrupt' && sub !== 'setname' && sub !== 'enable' && sub !== 'disable' && sub !== 'promote' && sub !== 'resume'))
    return { _action: 'select_agent', name: parts[2] || sub };
  if (sub === 'create') return { _action: 'create_agent', name: parts[2] || '', prompt: parts.slice(3).join(' ') };
  if (sub === 'delete') return { _action: 'delete_agent', name: parts[2] || '' };
  if (sub === 'setname') return { _action: 'set_agent_nickname', real_name: parts[2] || '', nickname: parts[3] || '' };
  if (sub === 'enable') return { _action: 'agent_enable', agent_name: parts[2] || '' };
  if (sub === 'disable') return { _action: 'agent_disable', agent_name: parts[2] || '' };
  if (sub === 'promote') return { _action: 'agent_promote', agent_name: parts[2] || '', target_scope: parts[3] || 'user' };
  if (sub === 'msg' || sub === 'message') {
    var target = parts[2] || '';
    var msg = parts.slice(3).join(' ');
    if (target.toUpperCase() === 'ALL') return { _action: 'broadcast_agents', message: msg };
    return { _sendMessage: true, target: target, text: msg };
  }
  if (sub === 'btw') return { _action: 'btw', agent_name: parts[2] || '', message: parts.slice(3).join(' ') };
  if (sub === 'interrupt') {
    var t = parts[2] || '';
    return { _action: 'interrupt', target: t, agent_name: t };
  }
  if (sub === 'resume') {
    var t = parts[2] || '';
    return { _sendMessage: true, target: t, text: parts.slice(3).join(' ') || 'Continue from where you left off.' };
  }
  return { _action: 'list_agents' };
}
function msgParser(parts, text) {
  var target = parts[1] || '';
  var msg = parts.slice(2).join(' ');
  if (!target || !msg) { addMsg('system', 'Usage: /msg <agent|ALL> <text>'); return null; }
  if (target.toUpperCase() === 'ALL') return { _action: 'broadcast_agents', message: msg };
  return { _sendMessage: true, target: target, text: msg };
}
function btwParser(parts, text) {
  if (parts.length < 3) { addMsg('system', 'Usage: /btw <agent|ALL> <text>'); return null; }
  return { _action: 'btw', agent_name: parts[1], message: parts.slice(2).join(' ') };
}
function stopParser(parts, text) {
  var force = parts.includes('-f');
  var target = parts.filter(function(p) { return p !== '-f' && p !== parts[0]; })[0] || '';
  if (!target) { addMsg('system', 'Usage: /stop <agent|ALL> [-f]'); return null; }
  return { _action: force ? 'cancel' : 'interrupt', target: target, agent_name: target };
}
function resumeParser(parts, text) {
  var target = parts[1] || '';
  if (!target) { addMsg('system', 'Usage: /resume <agent|ALL>'); return null; }
  return { _sendMessage: true, target: target, text: parts.slice(2).join(' ') || 'Continue from where you left off.' };
}
function setnameParser(parts, text) {
  if (!parts[1]) { addMsg('system', 'Usage: /setname <agent> [nickname]'); return null; }
  return { _action: 'set_agent_nickname', real_name: parts[1], nickname: parts[2] || '' };
}
function activateParser(parts, text) {
  if (parts.length < 3) { addMsg('system', 'Usage: ' + parts[0] + ' <type> <name>'); return null; }
  return { resource_type: parts[1], name: parts[2] };
}
function shareParser(parts, text) {
  if (parts.length < 4) { addMsg('system', 'Usage: /share <type> <name> <conv_id>'); return null; }
  return { _action: 'share_resource', resource_type: parts[1], name: parts[2], target_conversation_id: parts[3] };
}
function skillParser(parts, text) {
  var sub = (parts[1] || 'list').toLowerCase();
  if (sub === 'list') return { _action: 'list_resources' };
  if (sub === 'add' || sub === 'create') return { _action: 'create_resource', resource_type: 'skill', name: parts[2] || '', prompt: parts.slice(3).join(' ') };
  if (sub === 'del' || sub === 'delete') return { _action: 'delete_resource', resource_type: 'skill', name: parts[2] || '' };
  addMsg('system', 'Usage: /skill list | add <name> <prompt> | del <name>');
  return null;
}
function taskParser(parts, text) {
  var sub = (parts[1] || 'list').toLowerCase();
  if (sub === 'list' || sub === 'status') return { _action: 'task_status', include_library: true };
  if (sub === 'create') return { _action: 'create_task_def', name: parts[2] || '', prompt: parts.slice(3).join(' ') };
  if (sub === 'assign') return { _action: 'assign_task', agent_name: parts[2] || '', task_name: parts.slice(3).join(' ') };
  if (sub === 'del' || sub === 'delete') return { _action: 'delete_task_def', name: parts[2] || '' };
  if (sub === 'pause' || sub === 'resume' || sub === 'cancel') return { _action: sub + '_task', task_id: parts[2] || '' };
  if (sub === 'log') return { _action: 'task_log', name: parts[2] || '' };
  addMsg('system', 'Usage: /task list | create | assign | del | pause | resume | cancel | log');
  return null;
}
function serviceParser(parts, text) {
  var sub = (parts[1] || 'list').toLowerCase();
  if (sub === 'list') return { _action: 'service_list' };
  if (sub === 'install') return { _action: 'service_install', service_type: parts[2] || '', service_name: parts[3] || '', config_str: parts.slice(4).join(' ') };
  if (sub === 'uninstall') return { _action: 'service_uninstall', service_id: parts[2] || '' };
  if (sub === 'enable') return { _action: 'service_enable', service_id: parts[2] || '' };
  if (sub === 'disable') return { _action: 'service_disable', service_id: parts[2] || '' };
  addMsg('system', 'Usage: /service list | install | uninstall | enable | disable');
  return null;
}
function flowParser(parts, text) {
  var sub = (parts[1] || 'list').toLowerCase();
  if (sub === 'list') return { _action: 'list_conv_flows' };
  if (sub === 'templates') return { _action: 'list_available_flows' };
  if (sub === 'deploy') return { _action: 'deploy_flow', template_id: parts[2] || '', scope: parts[3] || 'user' };
  if (sub === 'start') return { _action: 'start_flow', instance_id: parts[2] || '' };
  if (sub === 'stop') return { _action: 'stop_flow', instance_id: parts[2] || '' };
  if (sub === 'params') return { _action: 'get_flow_instance', instance_id: parts[2] || '' };
  if (sub === 'undeploy') return { _action: 'undeploy_flow', instance_id: parts[2] || '' };
  if (sub === 'promote') return { _action: 'promote_flow', instance_id: parts[2] || '', target_scope: 'user' };
  addMsg('system', 'Usage: /flow list | templates | deploy | start | stop | params | undeploy | promote');
  return null;
}
function promptParser(parts, text) {
  var sub = (parts[1] || 'list').toLowerCase();
  if (sub === 'list') return { _action: 'list_prompts' };
  if (sub === 'use') return { _action: 'get_prompt', name: parts[2] || '' };
  addMsg('system', 'Usage: /prompt list | use <name>');
  return null;
}
function memoryParser(parts, text) {
  var sub = (parts[1] || 'list').toLowerCase();
  if (sub === 'list') return { _action: 'list_memories', agent_name: parts[2] || '' };
  if (sub === 'add') return { _action: 'add_memory', content: parts.slice(2).join(' ') };
  if (sub === 'del' || sub === 'delete') return { _action: 'delete_memory', memory_id: parts[2] || '' };
  if (sub === 'edit') return { _action: 'edit_memory', memory_id: parts[2] || '', content: parts.slice(3).join(' ') };
  if (sub === 'search') return { _action: 'search_memories', query: parts.slice(2).join(' ') };
  addMsg('system', 'Usage: /memory list | add | del | edit | search');
  return null;
}
function schedulesParser(parts, text) {
  var sub = (parts[1] || 'list').toLowerCase();
  if (sub === 'list') return { _action: 'list_schedules' };
  if (sub === 'add') return { _action: 'add_schedule', when: parts[2] || '', reason: parts.slice(3).join(' ') };
  if (sub === 'del' || sub === 'delete' || sub === 'clear') return { _action: 'delete_schedule' };
  addMsg('system', 'Usage: /schedules list | add <when> | del');
  return null;
}
function linkParser(parts, text) {
  var sub = (parts[1] || '').toLowerCase();
  if (!sub || sub === 'status') return { _action: 'list_linked_accounts' };
  if (sub === 'unlink') return { _action: 'unlink_account', provider: parts[2] || '' };
  return { _action: 'link_account', provider: parts[1], provider_id: parts[2] || '', bot_token: parts[3] || '' };
}
function autoconvParser(parts, text) {
  var sub = (parts[1] || '').toLowerCase();
  if (!sub) { addMsg('system', 'Usage: /autoconv <on|off|status|now> <agent|ALL> [freq]'); return null; }
  var p = { _action: 'random_thought', sub: sub, agent: parts[2] || '' };
  if (sub === 'on') p.frequency = parts[3] || '6/1m';
  return p;
}
function mediaServiceParser(parts, text) {
  var isVideo = COMMANDS[parts[0]] && COMMANDS[parts[0]].mediaType === 'video';
  var prefix = isVideo ? 'video' : 'image';
  var sub = (parts[1] || 'list').toLowerCase();
  if (sub === 'list') return { _action: 'list_' + prefix + '_services' };
  if (sub === 'select') return { _action: 'set_' + prefix + '_service', service_name: parts[2] || '', agent_name: parts[3] || '*' };
  if (sub === 'clear') return { _action: 'clear_' + prefix + '_service', agent_name: parts[2] || '' };
  addMsg('system', 'Usage: /' + prefix + 'service list | select <name> [agent] | clear [agent]');
  return null;
}
function addSecretParser(parts, text) {
  if (parts.length < 3) { addMsg('system', 'Usage: /add-secret <name> <value>'); return null; }
  return { _action: 'add_secret', name: parts[1], value: parts.slice(2).join(' ') };
}
function addVariableParser(parts, text) {
  if (parts.length < 3) { addMsg('system', 'Usage: /add-variable <name> <value>'); return null; }
  return { _action: 'add_variable', name: parts[1], value: parts.slice(2).join(' ') };
}
function viewParser(parts, text) {
  if (!parts[1]) { addMsg('system', 'Usage: /view <filename>'); return null; }
  return { _action: 'view_file', filename: parts.slice(1).join(' ') };
}
function callParser(parts, text) {
  if (!parts[1]) { addMsg('system', 'Usage: /call <tool> {json}'); return null; }
  var toolName = parts[1];
  var argsJson = parts.slice(2).join(' ');
  var args = {};
  try { if (argsJson) args = JSON.parse(argsJson); } catch(e) { addMsg('system', 'Invalid JSON: ' + e.message); return null; }
  return { _action: 'call_tool', tool_name: toolName, arguments: args };
}
function uninstallParser(parts, text) {
  if (!parts[1]) { addMsg('system', 'Usage: /uninstall <tool_name>'); return null; }
  return { _action: 'uninstall_tool', name: parts[1] };
}
function runParser(parts, text) {
  var cmd = text.replace(/^\/run\s+/, '');
  if (!cmd) { addMsg('system', 'Usage: /run <command>'); return null; }
  return { _action: 'fs_exec', command: cmd, timeout: 30 };
}
function diffParser(parts, text) {
  var ref = parts.slice(1).join(' ') || '.';
  return { _action: 'fs_exec', command: 'git diff ' + ref, timeout: 15 };
}
function planParser(parts, text) {
  var arg = text.replace(/^\/plan\s*/, '').trim();
  // No args — open plans panel
  if (!arg) { showPanel('plans'); return null; }
  // Subcommands
  var sub = arg.split(/\s+/);
  if (sub[0] === 'list') { showPanel('plans'); return null; }
  if (sub[0] === 'approve' && sub[1]) { return { _action: 'approve_plan', plan_id: sub[1] }; }
  if (sub[0] === 'cancel' && sub[1]) { return { _action: 'cancel_plan', plan_id: sub[1] }; }
  if (sub[0] === 'delete' && sub[1]) { return { _action: 'delete_plan', plan_id: sub[1] }; }
  // Default: send as plan creation request
  return { _sendPlan: true, text: arg };
}
function historyParser(parts, text) {
  var n = parseInt(parts[1]) || 50;
  var offset = parseInt(parts[2]) || 0;
  return { _action: 'load_history', limit: n, offset: offset };
}
function exportParser(parts, text) {
  return { _action: 'export', format: parts[1] || 'markdown' };
}
function renameParser(parts, text) {
  var title = text.replace(/^\/rename\s+/, '');
  if (!title) { addMsg('system', 'Usage: /rename <title>'); return null; }
  return { _action: 'set_conv_title', title: title };
}
function deleteParser(parts, text) {
  if (!parts[1]) { addMsg('system', 'Usage: /delete <conversation_id>'); return null; }
  return { _action: 'delete_conversation', conversation_id: parts[1] };
}
function deleteMsgParser(parts, text) {
  var idx = parseInt(parts[1]);
  if (isNaN(idx)) { addMsg('system', 'Usage: /delete-msg <index>'); return null; }
  return { _action: 'delete_message', index: idx };
}
function searchParser(parts, text) {
  var query = text.replace(/^\/search\s+/, '');
  if (!query) { addMsg('system', 'Usage: /search <query>'); return null; }
  return { _action: 'search_messages', query: query };
}

function dispatchCommand(text) {
  var parts = text.split(/\s+/);
  var cmd = parts[0].toLowerCase();
  var arg = text.slice(cmd.length).trim();
  var def = COMMANDS[cmd];
  if (!def) return false;

  // Client-side handler
  if (def.handler) {
    window[def.handler](arg);
    return true;
  }

  // Parse params
  var params = null;
  if (def.parser) {
    var parserFn = window[def.parser];
    if (parserFn) params = parserFn(parts, text);
    if (params === null) return true; // parser showed error
  } else if (def.action) {
    params = {};
    if (def.argName && arg) params[def.argName] = arg;
  }

  if (!params) params = {};

  // Handle special parser results
  if (params._sendMessage) {
    vscode.postMessage({ type: 'sendMessage', text: params.text, target: params.target });
    addMsg('user', '/msg ' + params.target + ' ' + params.text);
    return true;
  }
  if (params._sendPlan) {
    vscode.postMessage({ type: 'sendMessage', text: '[Create a structured plan using the create_plan tool. Analyze the request, identify steps, then call create_plan.]\\n\\n' + params.text });
    addMsg('user', '/plan ' + params.text);
    return true;
  }

  // Determine action
  var action = params._action || def.action;
  if (!action) return false;
  delete params._action;

  // Send command to extension host
  sendCmd(action, JSON.stringify(params));
  return true;
}

function send() {
  var text = inputEl.value.trim();
  if (!text) return;

  if (text.startsWith('/')) {
    if (dispatchCommand(text)) {
      inputEl.value = '';
      inputEl.style.height = '36px';
      return;
    }
  }

  addMsg('user', text, _replyTo ? { source: { reply_to: _replyTo } } : undefined);
  var msg = { type: 'sendMessage', text: text };
  if (_replyTo) msg.reply_to = _replyTo;
  vscode.postMessage(msg);
  cancelReply();
  inputEl.value = '';
  inputEl.style.height = '36px';
}

function setActiveTab(id) {
  var btns = document.querySelectorAll('.toolbar button[id]');
  for (var i = 0; i < btns.length; i++) btns[i].classList.remove('active');
  var el = document.getElementById(id);
  if (el) el.classList.add('active');
}

function backToChat() { closePanel(); setActiveTab('tbChat'); }

function newChat() { closePanel();
  vscode.postMessage({ type: 'newConversation' });
  messagesEl.innerHTML = '<div class="msg system">New conversation</div>';
  currentHistoryConvId = null;
  currentHistoryOffset = 0;
  setActiveTab('tbChat');
}
function loadConvs() { closePanel(); setActiveTab('tbConvs'); vscode.postMessage({ type: 'loadConversations' }); }
function sendCmd(cmd, arg) { vscode.postMessage({ type: 'command', command: cmd, arg }); }

function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

function renderMd(text) {
  // Basic markdown: **bold**, *italic*, \`code\`, \`\`\`blocks\`\`\`
  return text
    .replace(/\`\`\`(\w*)\n([\s\S]*?)\`\`\`/g, '<pre><code>$2</code></pre>')
    .replace(/\`([^\`]+)\`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\*([^*]+)\*/g, '<em>$1</em>')
    .replace(/^- (.+)$/gm, '\u2022 $1')
    .replace(/^#{1,3} (.+)$/gm, '<strong>$1</strong>')
    // Detect image URLs and render inline
    .replace(/(https?:\/\/[^\s]+\/files\/[^\s]+\.(png|jpg|jpeg|gif|webp|svg))/gi, '<img src="$1" style="max-width:100%;max-height:300px;border-radius:4px;margin:4px 0" />')
    .replace(/\n/g, '<br>');
}

function renderToolResult(content) {
  // Strip TOOL OUTPUT wrapper
  let text = content.replace(/\[TOOL OUTPUT[^\]]*\]\n?/g, '').replace(/\n\[\/TOOL OUTPUT\]/g, '');
  // Detect diffs
  const lines = text.split('\n');
  const hasDiff = lines.some(function(l) { return l.trimStart().startsWith('+ ') || l.trimStart().startsWith('- '); });
  if (hasDiff && (text.includes('replacement') || text.includes('Edited ') || text.includes('Written '))) {
    return '<pre class="diff">' + lines.map(function(l) {
      const s = l.trimStart();
      if (s.startsWith('+ ') || s.match(/^\d+\s+\+ /)) return '<span class="diff-add">' + esc(l) + '</span>';
      if (s.startsWith('- ') || s.match(/^\d+\s+- /)) return '<span class="diff-del">' + esc(l) + '</span>';
      if (s.startsWith('@@')) return '<span class="diff-hunk">' + esc(l) + '</span>';
      return '<span class="diff-ctx">' + esc(l) + '</span>';
    }).join('\\n') + '</pre>';
  }
  return esc(text.slice(0, 300));
}

function addMsg(type, content, meta) {
  const div = document.createElement('div');
  div.className = 'msg ' + type;

  // Track raw index for reply-to
  var rawIdx = meta?.raw_index !== undefined ? meta.raw_index : _msgRawIndex++;
  div.dataset.rawIndex = rawIdx;
  div.dataset.rawText = (content || '').substring(0, 200);

  // Action buttons for user/assistant messages
  var actionsHtml = '';
  if (type === 'user' || type === 'assistant') {
    actionsHtml = '<span class="msg-actions">'
      + '<button onclick="setReplyTo(this)" title="Reply">\\u21a9</button>'
      + '<button onclick="deleteMsg(this)" title="Delete">&times;</button>'
      + '</span>';
  }

  // Reply-to quote
  var replyQuoteHtml = '';
  var replySource = meta?.source?.reply_to || meta?.reply_to;
  if (replySource && replySource.text_preview) {
    var rtAgent = replySource.agent || replySource.role || '';
    var rtPreview = replySource.text_preview.substring(0, 100);
    var rtIdx = replySource.raw_index !== undefined ? replySource.raw_index : -1;
    replyQuoteHtml = '<div class="reply-quote"' + (rtIdx >= 0 ? ' onclick="scrollToMsg(' + rtIdx + ')"' : '') + '>'
      + '\\u21a9 ' + esc(rtAgent) + ': "' + esc(rtPreview) + '"</div>';
  }

  if (type === 'user') {
    div.innerHTML = actionsHtml + replyQuoteHtml + esc(content);
  } else if (type === 'assistant') {
    const agent = meta?.agent_name || meta?.source?.name || '';
    const svc = meta?.source?.llm_service || '';
    const color = agentColor(agent);
    div.innerHTML = actionsHtml + replyQuoteHtml + '<span class="agent-badge" style="background:' + color + '">'
      + esc(agent) + (svc ? ' via ' + esc(svc) : '') + '</span>' + renderMd(content);
  } else if (type === 'tool_call') {
    div.innerHTML = '&#9889; ' + esc(content);
  } else if (type === 'tool_result') {
    div.innerHTML = '&#10003; ' + renderToolResult(content);
  } else if (type === 'sub_agent_trace') {
    var src = meta?.source || {};
    var trace = meta?.trace || [];
    var traceId = meta?.trace_id || '';
    var agent = src.name || '?';
    var parent = src.parent_agent || '?';
    var depth = src.depth || 0;
    var doneEntry = null;
    for (var ti = trace.length - 1; ti >= 0; ti--) {
      if (trace[ti].type === 'done') { doneEntry = trace[ti]; break; }
    }
    var toolCount = (doneEntry?.tools_called || []).length;
    var tokIn = doneEntry?.tokens_in || 0;
    var tokOut = doneEntry?.tokens_out || 0;
    var summary = parent + ' \\u2192 ' + agent + ' (' + toolCount + ' tools, ' + tokIn + '\\u2191 ' + tokOut + '\\u2193)';
    var bodyLines = trace.map(function(e) {
      if (e.type === 'iteration') return '<div class="trace-entry">iter ' + e.iteration + ' \\u00b7 ' + (e.total_tools || 0) + ' tools</div>';
      if (e.type === 'tool_call') return '<div class="trace-entry tool">\\u26a1 ' + esc(e.tool || '?') + '</div>';
      if (e.type === 'done') return '<div class="trace-entry done">\\u2713 ' + esc(e.status || '?') + ' (' + (e.tokens_in || 0) + '\\u2191 ' + (e.tokens_out || 0) + '\\u2193)</div>';
      return '';
    }).join('');
    var contentHtml = content ? '<div class="trace-content">' + renderMd(content) + '</div>' : '';
    div.className = 'sub-trace';
    div.dataset.traceId = traceId;
    div.style.marginLeft = (depth * 12) + 'px';
    div.innerHTML = '<div class="sub-trace-header" onclick="this.nextElementSibling.classList.toggle(\\\'open\\\')">'
      + '\\u25b6 ' + esc(summary) + '</div>'
      + '<div class="sub-trace-body">' + bodyLines + contentHtml + '</div>';
  } else {
    div.textContent = content;
  }
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function addToolResult(tool, result) {
  const div = document.createElement('div');
  div.className = 'msg tool_result';
  div.innerHTML = renderToolResult(result);
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

// Handle messages from extension
window.addEventListener('message', function(e) {
  const msg = e.data;
  console.log('[PawFlow webview] received:', msg.type, msg);
  switch (msg.type) {
    case 'sseEvent':
      handleSSE(msg.event);
      break;
    case 'conversationList':
      console.log('[PawFlow webview] convs:', msg.conversations?.length);
      showConvList(msg.conversations);
      break;
    case 'history':
      replayHistory(msg.data);
      break;
    case 'newConversation':
      messagesEl.innerHTML = '<div class="msg system">New conversation</div>';
      currentHistoryConvId = null;
      currentHistoryOffset = 0;
      break;
    case 'error':
      addMsg('error', msg.message);
      break;
    case 'fileAttached':
      statusEl.textContent = msg.count + ' file(s) attached';
      break;
    case 'agentSelected':
      statusEl.textContent = 'Agent: ' + msg.agent;
      break;
    case 'actionResult':
      if (renderPanelResult(msg.action, msg.data)) break;
      if (msg.data && msg.data.error) { addMsg('error', msg.data.error); break; }
      var d = msg.data || {};
      if (msg.action === 'model') statusEl.textContent = 'Model: ' + (d.model || d.message || '?');
      else if (msg.action === 'select_agent') { statusEl.textContent = 'Agent: ' + (d.agent || d.name || '?'); }
      else if (msg.action === 'list_tools') {
        var tools = d.tools || [];
        if (!tools.length) addMsg('system', 'No tools.');
        else addMsg('system', 'Tools (' + tools.length + '):\\n' + tools.map(function(t) { return '  ' + t.name + ': ' + (t.description || '').slice(0, 60); }).join('\\n'));
      }
      else if (msg.action === 'list_secrets') {
        var secrets = d.secrets || [];
        addMsg('system', secrets.length ? 'Secrets: ' + secrets.join(', ') : 'No secrets.');
      }
      else if (msg.action === 'list_variables') {
        var vars = d.variables || {};
        var vlines = Object.entries(vars).map(function(e) { return '  ' + e[0] + ' = ' + e[1]; });
        addMsg('system', vlines.length ? 'Variables:\\n' + vlines.join('\\n') : 'No variables.');
      }
      else if (msg.action === 'cost') {
        var svcs = d.services || [];
        if (!svcs.length) addMsg('system', 'No usage data.');
        else {
          var clines = svcs.map(function(s) { return (s.llm_service || '?') + ': ' + (s.tokens_in || 0) + ' in / ' + (s.tokens_out || 0) + ' out' + (s.cost !== undefined ? ' $' + s.cost.toFixed(4) : ''); });
          addMsg('system', clines.join('\\n'));
        }
      }
      else if (msg.action === 'approve_plan') { addMsg('system', '\\u2705 Plan approved'); loadPlansPanel(); }
      else if (msg.action === 'reject_plan') { addMsg('system', '\\u274C Plan rejected'); loadPlansPanel(); }
      else if (msg.action === 'cancel_plan') { addMsg('system', '\\u23F9 Plan cancelled'); loadPlansPanel(); }
      else if (msg.action === 'delete_plan') { addMsg('system', '\\u2705 Plan deleted'); loadPlansPanel(); }
      else if (msg.action === 'update_plan_step') { loadPlansPanel(); }
      else if (msg.action === 'assign_plan') { addMsg('system', '\\u2705 Plan assigned'); loadPlansPanel(); }
      else if (msg.action === 'create_plan_user') { addMsg('system', '\\u2705 Plan created: ' + (d.plan ? d.plan.title : '')); loadPlansPanel(); }
      else if (d.result || d.message) addMsg('system', d.result || d.message);
      else if (typeof d === 'string') addMsg('system', d);
      else addMsg('system', JSON.stringify(d).slice(0, 500));
      break;
    case 'clipboardContent':
      if (msg.text) { inputEl.value += msg.text; addMsg('system', 'Pasted from clipboard.'); }
      break;
    case 'relayStatus':
      updateRelayStatus(msg.status);
      break;
  }
});

function handleSSE(event) {
  const { event: evType, data } = event;
  const agent = data.agent_name || '';
  if (!agent && ['thinking', 'token', 'tool_call', 'done', 'cancelled'].indexOf(evType) >= 0) {
    console.error('[BUG] SSE event "' + evType + '" has no agent_name', JSON.stringify(data).slice(0, 200));
  }

  switch (evType) {
    case 'thinking':
    case 'thinking_content':
      statusEl.innerHTML = '<span class="thinking">' + randomVerb() + '...</span>';
      updateActiveAgents(agent, 'thinking');
      break;

    case 'token':
      streaming[agent] = (streaming[agent] || '') + (data.text || '');
      statusEl.textContent = agent + ' writing... (' + streaming[agent].split(' ').length + 'w)';
      updateActiveAgents(agent, 'writing');
      break;

    case 'tool_call':
      _lastToolCall = agent + ' ' + (data.tool || '') + '(' +
        JSON.stringify(data.arguments || {}).slice(0, 100) + ')';
      addMsg('tool_call', _lastToolCall, data);
      _hadToolCalls = true;
      updateActiveAgents(agent, data.tool || 'tool');
      break;

    case 'tool_result':
      addToolResult(data.tool || '', data.result || '');
      break;

    case 'done': {
      const text = data.response || streaming[agent] || '';
      if (text) addMsg('assistant', text, data);
      streaming[agent] = '';
      _hadToolCalls = false;
      updateActiveAgents(agent, 'done');
      const tin = data.tokens_in || 0;
      const tout = data.tokens_out || 0;
      const model = data.model || '';
      statusEl.innerHTML = '<span class="token-footer">' + tin + '\\u2191 ' + tout + '\\u2193' + (model ? ' \\u00b7 ' + model : '') + '</span>';
      break;
    }

    case 'error_event':
      addMsg('error', data.message || 'Error');
      statusEl.textContent = '';
      break;

    case 'cancelled':
      statusEl.textContent = agent + ' cancelled';
      updateActiveAgents(agent, 'cancelled');
      break;

    case 'iteration_status':
      statusEl.innerHTML = '<span class="thinking">' + randomVerb() + '... iter ' +
        data.iteration + ' \\u00b7 ' + data.total_tools + ' tools</span>';
      break;

    case 'exec_approval_request':
      showApproval('exec', data);
      break;

    case 'tool_approval_request':
      showApproval('tool', data);
      break;

    case 'ask_user':
      showAskUser(data);
      break;

    case 'sub_agent_start':
      addMsg('system', 'Sub-agent [' + agent + '] started');
      break;

    case 'sub_agent_done': {
      const resp = data.response || '';
      if (resp) addMsg('assistant', resp, data);
      break;
    }

    case 'compact_progress':
      if (data.stage === 'done') {
        statusEl.textContent = 'Compacted: ' + (data.before || 0) + ' \\u2192 ' + (data.after || 0) + ' messages';
      } else {
        statusEl.textContent = 'Compacting... ' + (data.stage || '');
      }
      break;

    case 'notification':
      addMsg('system', data.message || '');
      break;

    case 'btw_token':
      streaming['btw:' + agent] = (streaming['btw:' + agent] || '') + (data.text || '');
      break;

    case 'btw_done': {
      const btwText = data.response || streaming['btw:' + agent] || '';
      if (btwText) addMsg('assistant', '[btw] ' + btwText, data);
      streaming['btw:' + agent] = '';
      break;
    }

    case 'plan_created': {
      const plan = data.plan || data;
      const title = plan.title || data.title || '';
      const stepCount = (plan.steps && plan.steps.length) || data.steps || 0;
      addMsg('system', '\\u{1F4CB} Plan created: ' + title + ' (' + stepCount + ' steps)');
      break;
    }

    case 'plan_updated':
      // Refresh plans panel if open
      if (document.getElementById('panelOverlay')?.className === 'panel-overlay visible' && _pendingPanel === '') {
        loadPlansPanel();
      }
      break;

    case 'plan_deleted':
      if (document.getElementById('panelOverlay')?.className === 'panel-overlay visible' && _pendingPanel === '') {
        loadPlansPanel();
      }
      break;

    default:
      // Silently ignore unknown events
      break;
  }
}

function showApproval(type, data) {
  const div = document.createElement('div');
  div.className = 'approval';
  const label = type === 'exec' ? 'Execute: ' + esc(data.command) : 'Tool: ' + esc(data.tool_name);
  div.innerHTML = label + '<br>'
    + '<button onclick="approve(this,\\'' + data.request_id + '\\',\\'' + type + '\\','
    + (type === 'exec' ? '\\'approved\\'' : '\\'allow_once\\'') + ')">Allow</button>'
    + '<button onclick="approve(this,\\'' + data.request_id + '\\',\\'' + type + '\\',\\'denied\\')">Deny</button>'
    + '<button onclick="approve(this,\\'' + data.request_id + '\\',\\'' + type + '\\',\\'always_allow\\')">Always</button>';
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function approve(btn, reqId, type, result) {
  vscode.postMessage({ type: 'approval', requestId: reqId, result, approvalType: type });
  btn.parentElement.remove();
}

function showAskUser(data) {
  const div = document.createElement('div');
  div.className = 'approval';
  let html = '<strong>Agent question:</strong> ' + esc(data.question || '');
  if (data.options && data.options.length) {
    html += '<br>';
    for (const opt of data.options) {
      html += '<button onclick="answerAgent(this, \\'' + esc(opt).replace(/'/g, "\\\\'") + '\\')" style="margin:2px;padding:3px 10px;border:none;border-radius:3px;cursor:pointer">' + esc(opt) + '</button>';
    }
  }
  div.innerHTML = html;
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function answerAgent(btn, answer) {
  vscode.postMessage({ type: 'sendMessage', text: answer });
  btn.parentElement.remove();
}

function showConvList(convs) {
  messagesEl.innerHTML = '<div style="padding:4px 8px;font-size:12px;color:var(--vscode-descriptionForeground);border-bottom:1px solid var(--vscode-panel-border);margin-bottom:4px">Conversations (' + convs.length + ')</div>';
  for (const c of convs) {
    const div = document.createElement('div');
    div.style.cssText = 'padding:8px 10px;cursor:pointer;border-bottom:1px solid var(--vscode-panel-border);font-size:12px;transition:background 0.1s';
    div.onmouseenter = function() { div.style.background = 'var(--vscode-list-hoverBackground)'; };
    div.onmouseleave = function() { div.style.background = ''; };
    var preview = (c.preview || '').slice(0, 70);
    var count = c.message_count || '?';
    var date = c.updated_at ? new Date(c.updated_at * 1000).toLocaleString() : '';
    div.innerHTML = '<div style="font-weight:500;color:var(--vscode-editor-foreground)">' + esc(preview || '(new conversation)') + '</div>'
      + '<div style="font-size:10px;color:var(--vscode-descriptionForeground);margin-top:2px">'
      + esc(c.conversation_id.slice(0, 8)) + ' \\u2022 ' + count + ' msgs'
      + (date ? ' \\u2022 ' + date : '')
      + '</div>';
    div.onclick = function() { setActiveTab('tbChat'); vscode.postMessage({ type: 'resumeConversation', conversationId: c.conversation_id }); };
    messagesEl.appendChild(div);
  }
  if (!convs.length) {
    messagesEl.innerHTML += '<div style="padding:16px;text-align:center;color:var(--vscode-descriptionForeground);font-size:12px">No conversations yet</div>';
  }
}

function replayHistory(data) {
  messagesEl.innerHTML = '';
  _msgRawIndex = 0;
  currentHistoryConvId = data.conversation_id || currentHistoryConvId;
  currentHistoryOffset = (data.messages || []).length;

  if (data.has_more) {
    const more = document.createElement('div');
    more.className = 'load-more';
    more.textContent = '\\u25b2 Load more messages (' + (data.message_count || '?') + ' total)';
    more.onclick = function() {
      vscode.postMessage({
        type: 'resumeConversation',
        conversationId: currentHistoryConvId,
        offset: currentHistoryOffset,
      });
    };
    messagesEl.appendChild(more);
  }
  for (const m of (data.messages || [])) {
    addMsg(m.type || m.role, m.content || '', m);
  }
  statusEl.textContent = (data.messages || []).length + ' of ' + (data.message_count || '?') + ' messages';
}

// ── Panels (Resources, Context, Files, Tools) ──
function showPanel(name) {
  const overlay = document.getElementById('panelOverlay');
  overlay.className = 'panel-overlay visible';
  overlay.innerHTML = '<div class="panel-header"><h4>' + name.charAt(0).toUpperCase() + name.slice(1) + '</h4><button class="panel-close" onclick="closePanel()">\\u2715</button></div><div class="msg system">Loading...</div>';

  if (name === 'resources') loadResourcesPanel();
  else if (name === 'context') loadContextPanel();
  else if (name === 'files') loadFilesPanel();
  else if (name === 'tools') loadToolsPanel();
  else if (name === 'accounts') loadAccountsPanel();
  else if (name === 'plans') loadPlansPanel();
}

var _resMenuRtype = '';
var _resMenuName = '';

function showResMenu(e, rtype, name) {
  e.preventDefault();
  e.stopPropagation();
  var old = document.querySelector('.res-ctx');
  if (old) old.remove();

  _resMenuRtype = rtype;
  _resMenuName = name;

  var menu = document.createElement('div');
  menu.className = 'res-ctx';
  menu.style.left = e.clientX + 'px';
  menu.style.top = e.clientY + 'px';

  function addItem(label, action) {
    var d = document.createElement('div');
    d.textContent = label;
    d.onclick = function() { menu.remove(); doResAction(action); };
    menu.appendChild(d);
  }
  function addSep() {
    var hr = document.createElement('hr');
    menu.appendChild(hr);
  }

  if (rtype === 'agents' || rtype === 'skills' || rtype === 'mcp' || rtype === 'prompts' || rtype === 'task_defs') {
    addItem('Edit...', 'edit_resource');
    addSep();
  }
  if (rtype === 'agents' || rtype === 'skills' || rtype === 'mcp' || rtype === 'prompts') {
    addItem('Activate', 'activate');
    addItem('Deactivate', 'deactivate');
  }
  if (rtype === 'agents') {
    addSep();
    addItem('Enable agent', 'agent_enable');
    addItem('Disable agent', 'agent_disable');
  }
  if (rtype === 'task_defs') {
    addItem('Assign to agent...', 'assign_task');
  }
  if (rtype === 'services') {
    addItem('Edit...', 'edit_service');
    addSep();
    addItem('Enable', 'svc_enable');
    addItem('Disable', 'svc_disable');
  }
  if (rtype === 'parameters' || rtype === 'secrets') {
    addItem('Edit...', 'edit_param');
  }
  if (rtype === 'flows') {
    // Flow-specific actions — find the item data to know status
    var flowItem = null;
    try {
      var allFlows = _resData && _resData.flows ? _resData.flows : [];
      for (var fi = 0; fi < allFlows.length; fi++) {
        if ((allFlows[fi].instance_id || allFlows[fi].id || allFlows[fi].name) === name) { flowItem = allFlows[fi]; break; }
      }
    } catch(e) {}
    if (flowItem && flowItem.status === 'running') {
      addItem('\u23f9 Stop', 'flow_stop');
    } else {
      addItem('\u25b6 Start...', 'flow_start');
    }
    addItem('\u270f Edit params...', 'flow_edit_params');
    if (flowItem && flowItem.scope === 'conversation') {
      addItem('\u2b06 Promote to user', 'flow_promote');
    }
    addSep();
    addItem('\ud83d\uddd1 Undeploy', 'flow_undeploy');
  }
  // Delete for all user-scoped
  if (rtype !== 'flows') {
    addSep();
    if (rtype === 'services') addItem('Uninstall', 'svc_uninstall');
    else if (rtype === 'parameters' || rtype === 'secrets') addItem('Delete', 'del_param');
    else if (rtype === 'task_defs') addItem('Delete', 'del_task');
    else addItem('Delete', 'delete_res');
  }

  document.body.appendChild(menu);
  setTimeout(function() {
    document.addEventListener('click', function rm() { menu.remove(); document.removeEventListener('click', rm); });
  }, 0);
}

function doResAction(action) {
  var rtype = _resMenuRtype;
  var name = _resMenuName;
  var singularType = rtype.replace(/s$/, '');
  var cmd = '';
  var params = {};

  if (action === 'activate') { cmd = 'activate_resource'; params = { resource_type: singularType, name: name }; }
  else if (action === 'deactivate') { cmd = 'deactivate_resource'; params = { resource_type: singularType, name: name }; }
  else if (action === 'delete_res') { cmd = 'delete_resource'; params = { resource_type: singularType, name: name }; }
  else if (action === 'svc_enable') { cmd = 'service_enable'; params = { service_id: name }; }
  else if (action === 'svc_disable') { cmd = 'service_disable'; params = { service_id: name }; }
  else if (action === 'svc_uninstall') { cmd = 'service_uninstall'; params = { service_id: name }; }
  else if (action === 'agent_enable') { cmd = 'agent_enable'; params = { agent_name: name }; }
  else if (action === 'agent_disable') { cmd = 'agent_disable'; params = { agent_name: name }; }
  else if (action === 'del_task') { cmd = 'delete_task_def'; params = { name: name }; }
  else if (action === 'edit_param') {
    showCreateForm(rtype === 'secrets' ? 'secrets' : 'variables');
    // Pre-fill the key field after the form is rendered
    setTimeout(function() {
      var keyEl = document.getElementById('cf-key');
      if (keyEl) keyEl.value = name;
    }, 0);
    return;
  }
  else if (action === 'del_param') {
    cmd = rtype === 'secrets' ? 'delete_secret' : 'delete_param';
    params = { key: name, scope: 'user' };
  }
  else if (action === 'assign_task') {
    showAssignForm(name);
    return;
  }
  else if (action === 'flow_start') { showFlowStartForm(name); return; }
  else if (action === 'flow_edit_params') { showFlowStartForm(name, true); return; }
  else if (action === 'flow_promote') { cmd = 'promote_flow'; params = { instance_id: name, target_scope: 'user' }; }
  else if (action === 'flow_stop') { cmd = 'stop_flow'; params = { instance_id: name }; }
  else if (action === 'flow_undeploy') {
    if (!confirm('Undeploy flow \\'' + name + '\\'?')) return;
    cmd = 'undeploy_flow'; params = { instance_id: name };
  }
  else if (action === 'edit_resource') {
    showEditResourceForm(singularType, name);
    return;
  }
  else if (action === 'edit_service') {
    showEditServiceForm(name);
    return;
  }

  if (cmd) {
    vscode.postMessage({ type: 'command', command: cmd, arg: JSON.stringify(params) });
    setTimeout(function() { loadResourcesPanel(); }, 500);
  }
}

// ── Resource Edit Form ──
var _resFieldDefs = {
  agent:    [['prompt','textarea'],['description','text'],['llm_service','text'],['model','text'],['tools','text'],['max_depth','number'],['timeout','number']],
  skill:    [['prompt','textarea'],['description','text']],
  mcp:      [['url','text'],['auth','text'],['description','text']],
  task_def: [['prompt','textarea'],['criteria','textarea'],['default_interval','text'],['verifier','text'],['description','text']],
  prompt:   [['content','textarea'],['title','text'],['category','text'],['description','text']],
};

function showEditResourceForm(rtype, name) {
  // Fetch current data then show form
  vscode.postMessage({ type: 'command', command: 'get_resource_detail', arg: JSON.stringify({ resource_type: rtype, name: name }) });
  _pendingEdit = { rtype: rtype, name: name };
}

var _pendingEdit = null;

function _renderEditForm(rtype, name, data) {
  var overlay = document.getElementById('panelOverlay');
  overlay.className = 'panel-overlay visible';
  var fields = _resFieldDefs[rtype] || [];
  var scope = data._scope || data.scope || 'user';

  var html = '<div class="panel-header"><h4>Edit ' + rtype + ': ' + esc(name) + ' [' + scope + ']</h4><button class="panel-close" onclick="closePanel()">\\u2715</button></div>';
  html += '<div style="padding:4px">';
  for (var i = 0; i < fields.length; i++) {
    var key = fields[i][0];
    var type = fields[i][1];
    var val = (data[key] != null) ? String(data[key]) : '';
    html += '<label style="' + _cfLabelStyle + '">' + esc(key) + '</label>';
    if (type === 'textarea') {
      html += '<textarea id="ef-' + key + '" style="' + _cfTextareaStyle + '">' + esc(val) + '</textarea>';
    } else if (type === 'number') {
      html += '<input id="ef-' + key + '" type="number" value="' + esc(val) + '" style="' + _cfInputStyle + '">';
    } else {
      html += '<input id="ef-' + key + '" value="' + esc(val) + '" style="' + _cfInputStyle + '">';
    }
  }
  html += '<div style="display:flex;gap:6px;justify-content:flex-end;margin-top:8px">'
    + '<button onclick="closePanel()" style="background:var(--vscode-button-secondaryBackground);color:var(--vscode-button-secondaryForeground);border:none;padding:4px 12px;border-radius:3px;cursor:pointer;font-size:12px">Cancel</button>'
    + '<button onclick="submitEditForm(\\'' + esc(rtype) + '\\',\\'' + esc(name) + '\\',\\'' + scope + '\\')" style="background:var(--vscode-button-background);color:var(--vscode-button-foreground);border:none;padding:4px 12px;border-radius:3px;cursor:pointer;font-size:12px">Save</button>'
    + '</div></div>';
  overlay.innerHTML = html;
}

function submitEditForm(rtype, name, scope) {
  var fields = _resFieldDefs[rtype] || [];
  var data = {};
  for (var i = 0; i < fields.length; i++) {
    var key = fields[i][0];
    var type = fields[i][1];
    var el = document.getElementById('ef-' + key);
    if (el) data[key] = type === 'number' ? parseInt(el.value) || 0 : el.value;
  }
  vscode.postMessage({ type: 'command', command: 'update_resource',
    arg: JSON.stringify({ resource_type: rtype, name: name, scope: scope, data: data }) });
  closePanel();
  statusEl.textContent = rtype + ' "' + name + '" updated';
  setTimeout(function() { statusEl.textContent = ''; }, 3000);
  setTimeout(function() { loadResourcesPanel(); }, 500);
}

// ── Service Edit Form ──
function showEditServiceForm(serviceId) {
  vscode.postMessage({ type: 'command', command: 'get_service_detail', arg: JSON.stringify({ service_id: serviceId }) });
  _pendingEdit = { rtype: '_service', name: serviceId };
}

var _editSvcId = '';

function _renderServiceEditForm(serviceId, data) {
  _editSvcId = serviceId;
  var config = data.config || data;
  var svcType = data.service_type || '';
  var overlay = document.getElementById('panelOverlay');
  overlay.className = 'panel-overlay visible';

  var html = '<div class="panel-header"><h4>Edit: ' + esc(serviceId) + (svcType ? ' (' + esc(svcType) + ')' : '') + '</h4><button class="panel-close" onclick="closePanel()">\\u2715</button></div>';
  html += '<div style="padding:4px"><div id="cf-svc-params"><div style="color:var(--vscode-descriptionForeground);font-size:11px">Loading schema...</div></div>';
  html += '<div style="display:flex;gap:6px;justify-content:flex-end;margin-top:8px">'
    + '<button onclick="closePanel()" style="background:var(--vscode-button-secondaryBackground);color:var(--vscode-button-secondaryForeground);border:none;padding:4px 12px;border-radius:3px;cursor:pointer;font-size:12px">Cancel</button>'
    + '<button onclick="submitServiceEdit()" style="background:var(--vscode-button-background);color:var(--vscode-button-foreground);border:none;padding:4px 12px;border-radius:3px;cursor:pointer;font-size:12px">Save</button>'
    + '</div></div>';
  overlay.innerHTML = html;

  // Load schema then render with current values
  if (svcType) {
    // Stash config for when schema arrives
    (window as any)._editSvcConfig = config;
    vscode.postMessage({ type: 'command', command: 'get_service_schema', arg: JSON.stringify({ service_type: svcType }) });
  } else {
    // No type known — fall back to raw key/value
    _renderSvcSchemaParams({}, config);
  }
}

function submitServiceEdit() {
  if (!_editSvcId) return;
  var config: Record<string, any> = {};
  if (_cachedSvcSchema) {
    for (var pname in _cachedSvcSchema) {
      var el = document.getElementById('cf-sp-' + pname);
      if (!el) continue;
      var pdef = _cachedSvcSchema[pname];
      if (pdef.type === 'boolean') config[pname] = (el as HTMLInputElement).checked;
      else if (pdef.type === 'integer') config[pname] = parseInt((el as HTMLInputElement).value) || 0;
      else if (pdef.type === 'float') config[pname] = parseFloat((el as HTMLInputElement).value) || 0;
      else if (pdef.type === 'map' || pdef.type === 'object') {
        try { config[pname] = JSON.parse((el as HTMLTextAreaElement).value || '{}'); } catch { config[pname] = {}; }
      } else config[pname] = (el as HTMLInputElement).value || '';
    }
  }
  vscode.postMessage({ type: 'command', command: 'update_service',
    arg: JSON.stringify({ service_id: _editSvcId, config: config }) });
  closePanel();
  statusEl.textContent = 'Service "' + _editSvcId + '" updated';
  _editSvcId = '';
  setTimeout(function() { statusEl.textContent = ''; }, 3000);
  setTimeout(function() { loadResourcesPanel(); }, 500);
}

function showAssignForm(taskName) {
  var overlay = document.getElementById('panelOverlay');
  overlay.className = 'panel-overlay visible';
  overlay.innerHTML = '<div class="panel-header"><h4>Assign: ' + esc(taskName) + '</h4><button class="panel-close" onclick="closePanel()">\\u2715</button></div>'
    + '<div style="padding:4px">'
    + '<label style="font-size:11px;color:var(--vscode-descriptionForeground)">Agent</label>'
    + '<input id="af-agent" value="" style="width:100%;background:var(--vscode-input-background);color:var(--vscode-input-foreground);border:1px solid var(--vscode-input-border);padding:4px 6px;border-radius:3px;margin:2px 0 8px;font-size:12px">'
    + '<label style="font-size:11px;color:var(--vscode-descriptionForeground)">Context mode</label>'
    + '<select id="af-context" style="width:100%;background:var(--vscode-input-background);color:var(--vscode-input-foreground);border:1px solid var(--vscode-input-border);padding:4px 6px;border-radius:3px;margin:2px 0 8px;font-size:12px">'
    + '<option value="isolated">isolated (default)</option>'
    + '<option value="last:10">last:10</option>'
    + '<option value="last:20">last:20</option>'
    + '<option value="last:50">last:50</option>'
    + '<option value="summary:2000">summary:2000</option>'
    + '<option value="summary:4000">summary:4000</option>'
    + '<option value="full">full (entire context)</option>'
    + '</select>'
    + '<label style="font-size:11px;color:var(--vscode-descriptionForeground)">Interval (optional)</label>'
    + '<input id="af-interval" placeholder="e.g. 6/1m, 2/1h, 60" style="width:100%;background:var(--vscode-input-background);color:var(--vscode-input-foreground);border:1px solid var(--vscode-input-border);padding:4px 6px;border-radius:3px;margin:2px 0 8px;font-size:12px">'
    + '<label style="font-size:11px;color:var(--vscode-descriptionForeground)">Variables (key=value, one per line)</label>'
    + '<textarea id="af-vars" placeholder="nbr_images=20\\nstyle=cyberpunk" style="width:100%;min-height:50px;background:var(--vscode-input-background);color:var(--vscode-input-foreground);border:1px solid var(--vscode-input-border);padding:4px 6px;border-radius:3px;margin:2px 0 8px;font-size:12px;font-family:var(--vscode-editor-font-family);resize:vertical"></textarea>'
    + '<div style="display:flex;gap:6px;justify-content:flex-end;margin-top:6px">'
    + '<button onclick="closePanel()" style="background:var(--vscode-button-secondaryBackground);color:var(--vscode-button-secondaryForeground);border:none;padding:4px 12px;border-radius:3px;cursor:pointer;font-size:12px">Cancel</button>'
    + '<button onclick="submitAssignForm(\\'' + esc(taskName).replace(/'/g, "\\\\\\'") + '\\')" style="background:var(--vscode-button-background);color:var(--vscode-button-foreground);border:none;padding:4px 12px;border-radius:3px;cursor:pointer;font-size:12px">Assign</button>'
    + '</div></div>';
  document.getElementById('af-agent').focus();
}

function submitAssignForm(taskName) {
  var agent = document.getElementById('af-agent').value.trim();
  var context = document.getElementById('af-context').value;
  var interval = document.getElementById('af-interval').value.trim();
  var varsText = document.getElementById('af-vars').value.trim();
  if (!agent) { return; }

  var params = { agent_name: agent, task_name: taskName, context: context };
  if (interval) params.interval = interval;
  if (varsText) {
    var variables = {};
    varsText.split('\\n').forEach(function(line) {
      var eq = line.indexOf('=');
      if (eq > 0) variables[line.slice(0,eq).trim()] = line.slice(eq+1).trim();
    });
    if (Object.keys(variables).length) params.variables = variables;
  }
  vscode.postMessage({ type: 'command', command: 'assign_task', arg: JSON.stringify(params) });
  closePanel();
}

var _flowStartInstanceId = '';
var _flowStartEditOnly = false;
function showFlowStartForm(instanceId, editOnly) {
  _flowStartInstanceId = instanceId;
  _flowStartEditOnly = !!editOnly;
  var overlay = document.getElementById('panelOverlay');
  overlay.className = 'panel-overlay visible';
  overlay.innerHTML = '<div class="panel-header"><h4>' + (editOnly ? 'Edit Flow Params' : 'Start Flow') + ': ' + esc(instanceId) + '</h4><button class="panel-close" onclick="closePanel()">\\u2715</button></div>'
    + '<div id="flowParamsContent" style="padding:4px;color:var(--vscode-descriptionForeground)">Loading parameters...</div>';
  vscode.postMessage({ type: 'command', command: 'get_flow_instance', arg: JSON.stringify({ instance_id: instanceId }) });
}
function _renderFlowStartParams(data) {
  var el = document.getElementById('flowParamsContent');
  if (!el) return;
  if (data.error) { el.innerHTML = '<span style="color:#f85149">' + esc(data.error) + '</span>'; return; }
  var tplParams = data.template_parameters || {};
  var instParams = data.parameters || {};
  var merged = Object.assign({}, tplParams, instParams);
  var keys = Object.keys(merged);
  var html = '';
  for (var i = 0; i < keys.length; i++) {
    var k = keys[i];
    var v = typeof merged[k] === 'object' ? JSON.stringify(merged[k]) : String(merged[k]);
    html += '<label style="' + _cfLabelStyle + '">' + esc(k) + '</label>'
      + '<input class="fp-input" data-key="' + esc(k) + '" value="' + esc(v) + '" style="' + _cfInputStyle + '">';
  }
  if (!html) html = '<div style="color:var(--vscode-descriptionForeground)">No parameters</div>';
  var btnLabel = _flowStartEditOnly ? 'Save' : 'Start';
  html += '<div style="display:flex;gap:6px;justify-content:flex-end;margin-top:8px">'
    + '<button onclick="closePanel()" style="background:var(--vscode-button-secondaryBackground);color:var(--vscode-button-secondaryForeground);border:none;padding:4px 12px;border-radius:3px;cursor:pointer;font-size:12px">Cancel</button>'
    + '<button onclick="submitFlowStart()" style="background:var(--vscode-button-background);color:var(--vscode-button-foreground);border:none;padding:4px 12px;border-radius:3px;cursor:pointer;font-size:12px">' + btnLabel + '</button>'
    + '</div>';
  el.innerHTML = html;
}
function submitFlowStart() {
  var params = {};
  document.querySelectorAll('.fp-input').forEach(function(el) {
    params[el.dataset.key] = el.value;
  });
  // Save params first
  vscode.postMessage({ type: 'command', command: 'update_flow_params', arg: JSON.stringify({ instance_id: _flowStartInstanceId, parameters: params }) });
  if (!_flowStartEditOnly) {
    // Then start
    setTimeout(function() {
      vscode.postMessage({ type: 'command', command: 'start_flow', arg: JSON.stringify({ instance_id: _flowStartInstanceId }) });
    }, 300);
  }
  closePanel();
  statusEl.textContent = _flowStartEditOnly ? 'Parameters saved' : 'Flow starting...';
  setTimeout(function() { statusEl.textContent = ''; loadResourcesPanel(); }, 2000);
}

var _cfInputStyle = 'width:100%;background:var(--vscode-input-background);color:var(--vscode-input-foreground);border:1px solid var(--vscode-input-border);padding:4px 6px;border-radius:3px;margin:2px 0 8px;font-size:12px';
var _cfTextareaStyle = 'width:100%;min-height:60px;background:var(--vscode-input-background);color:var(--vscode-input-foreground);border:1px solid var(--vscode-input-border);padding:4px 6px;border-radius:3px;margin:2px 0 8px;font-size:12px;font-family:var(--vscode-editor-font-family);resize:vertical';
var _cfLabelStyle = 'font-size:11px;color:var(--vscode-descriptionForeground)';

function showCreateForm(rtype) {
  var overlay = document.getElementById('panelOverlay');
  overlay.className = 'panel-overlay visible';
  var title = {agents:'Create Agent',skills:'Create Skill',task_defs:'Create Task',prompts:'Create Prompt',variables:'Create Variable',secrets:'Create Secret',services:'Install Service'}[rtype] || 'Create';

  var fields = '';
  if (rtype === 'agents') {
    fields = '<label style="' + _cfLabelStyle + '">Name</label>'
      + '<input id="cf-name" style="' + _cfInputStyle + '" placeholder="my_agent">'
      + '<label style="' + _cfLabelStyle + '">System prompt</label>'
      + '<textarea id="cf-prompt" style="' + _cfTextareaStyle + '" placeholder="You are a helpful assistant..."></textarea>'
      + '<label style="' + _cfLabelStyle + '">Model (optional)</label>'
      + '<input id="cf-model" style="' + _cfInputStyle + '" placeholder="gpt-4o">'
      + '<label style="' + _cfLabelStyle + '">LLM Service (optional)</label>'
      + '<input id="cf-llm" style="' + _cfInputStyle + '" placeholder="default">'
      + '<label style="' + _cfLabelStyle + '">Description (optional)</label>'
      + '<input id="cf-desc" style="' + _cfInputStyle + '">';
  } else if (rtype === 'skills') {
    fields = '<label style="' + _cfLabelStyle + '">Name</label>'
      + '<input id="cf-name" style="' + _cfInputStyle + '" placeholder="my_skill">'
      + '<label style="' + _cfLabelStyle + '">Prompt</label>'
      + '<textarea id="cf-prompt" style="' + _cfTextareaStyle + '" placeholder="Skill instructions..."></textarea>'
      + '<label style="' + _cfLabelStyle + '">Description (optional)</label>'
      + '<input id="cf-desc" style="' + _cfInputStyle + '">';
  } else if (rtype === 'task_defs') {
    fields = '<label style="' + _cfLabelStyle + '">Name</label>'
      + '<input id="cf-name" style="' + _cfInputStyle + '" placeholder="my_task">'
      + '<label style="' + _cfLabelStyle + '">Task prompt</label>'
      + '<textarea id="cf-prompt" style="' + _cfTextareaStyle + '" placeholder="What the task should do..."></textarea>'
      + '<label style="' + _cfLabelStyle + '">Criteria (optional)</label>'
      + '<input id="cf-criteria" style="' + _cfInputStyle + '">'
      + '<label style="' + _cfLabelStyle + '">Interval (optional)</label>'
      + '<input id="cf-interval" style="' + _cfInputStyle + '" placeholder="6/1m">'
      + '<label style="' + _cfLabelStyle + '">Verifier agent (optional)</label>'
      + '<input id="cf-verifier" style="' + _cfInputStyle + '">';
  } else if (rtype === 'prompts') {
    fields = '<label style="' + _cfLabelStyle + '">Name</label>'
      + '<input id="cf-name" style="' + _cfInputStyle + '" placeholder="my_prompt">'
      + '<label style="' + _cfLabelStyle + '">Content</label>'
      + '<textarea id="cf-prompt" style="' + _cfTextareaStyle + '" placeholder="Prompt content..."></textarea>'
      + '<label style="' + _cfLabelStyle + '">Description (optional)</label>'
      + '<input id="cf-desc" style="' + _cfInputStyle + '">';
  } else if (rtype === 'variables') {
    fields = '<label style="' + _cfLabelStyle + '">Key</label>'
      + '<input id="cf-key" style="' + _cfInputStyle + '" placeholder="my_variable">'
      + '<label style="' + _cfLabelStyle + '">Value</label>'
      + '<input id="cf-value" style="' + _cfInputStyle + '">'
      + '<label style="' + _cfLabelStyle + '">Scope</label>'
      + '<select id="cf-scope" style="' + _cfInputStyle + '"><option value="user">User</option><option value="conversation">Conversation</option></select>';
  } else if (rtype === 'secrets') {
    fields = '<label style="' + _cfLabelStyle + '">Key</label>'
      + '<input id="cf-key" style="' + _cfInputStyle + '" placeholder="my_secret">'
      + '<label style="' + _cfLabelStyle + '">Value</label>'
      + '<input id="cf-value" type="password" style="' + _cfInputStyle + '">'
      + '<label style="' + _cfLabelStyle + '">Scope</label>'
      + '<select id="cf-scope" style="' + _cfInputStyle + '"><option value="user">User</option><option value="conversation">Conversation</option></select>';
  } else if (rtype === 'services') {
    title = 'Install Service';
    fields = '<label style="' + _cfLabelStyle + '">Service type (loading...)</label>'
      + '<select id="cf-svctype" onchange="_onSvcTypeChange()" style="' + _cfInputStyle + '"><option value="">Loading...</option></select>'
      + '<label style="' + _cfLabelStyle + '">Service name</label>'
      + '<input id="cf-name" style="' + _cfInputStyle + '" placeholder="my_service">'
      + '<label style="' + _cfLabelStyle + '">Description (optional)</label>'
      + '<input id="cf-desc" style="' + _cfInputStyle + '">'
      + '<div id="cf-svc-params"></div>';
    // Load service types async
    setTimeout(function() {
      vscode.postMessage({ type: 'command', command: 'list_service_types' });
    }, 50);
  } else if (rtype === 'flows') {
    title = 'Deploy Flow';
    fields = '<label style="' + _cfLabelStyle + '">Template (loading...)</label>'
      + '<select id="cf-template" style="' + _cfInputStyle + '"><option>Loading...</option></select>'
      + '<label style="' + _cfLabelStyle + '">Scope</label>'
      + '<select id="cf-scope" style="' + _cfInputStyle + '"><option value="user">User</option><option value="conversation">Conversation</option></select>'
      + '<label style="' + _cfLabelStyle + '">Parameters (JSON, optional)</label>'
      + '<textarea id="cf-params" style="' + _cfTextareaStyle + '" placeholder="&#123;&quot;key&quot;: &quot;value&quot;&#125;"></textarea>';
    // Load templates async after render
    setTimeout(function() {
      vscode.postMessage({ type: 'command', command: 'list_available_flows' });
    }, 50);
  }

  overlay.innerHTML = '<div class="panel-header"><h4>' + title + '</h4><button class="panel-close" onclick="closePanel()">\\u2715</button></div>'
    + '<div style="padding:4px">' + fields
    + '<div style="display:flex;gap:6px;justify-content:flex-end;margin-top:8px">'
    + '<button onclick="closePanel()" style="background:var(--vscode-button-secondaryBackground);color:var(--vscode-button-secondaryForeground);border:none;padding:4px 12px;border-radius:3px;cursor:pointer;font-size:12px">Cancel</button>'
    + '<button onclick="submitCreateForm(\\'' + rtype + '\\')" style="background:var(--vscode-button-background);color:var(--vscode-button-foreground);border:none;padding:4px 12px;border-radius:3px;cursor:pointer;font-size:12px">Create</button>'
    + '</div></div>';
  var nameEl = document.getElementById('cf-name') || document.getElementById('cf-key') || document.getElementById('cf-svctype');
  if (nameEl) nameEl.focus();
}

function submitCreateForm(rtype) {
  var name = (document.getElementById('cf-name')?.value || '').trim();
  var prompt = (document.getElementById('cf-prompt')?.value || '').trim();
  if (!name && rtype !== 'variables' && rtype !== 'secrets') return;

  var cmd = '';
  var params = {};

  if (rtype === 'agents') {
    cmd = 'create_agent';
    params = { name: name, prompt: prompt };
    var model = (document.getElementById('cf-model')?.value || '').trim();
    var llm = (document.getElementById('cf-llm')?.value || '').trim();
    var desc = (document.getElementById('cf-desc')?.value || '').trim();
    if (model) params.model = model;
    if (llm) params.llm_service = llm;
    if (desc) params.description = desc;
  } else if (rtype === 'skills') {
    cmd = 'create_resource';
    params = { resource_type: 'skill', name: name, prompt: prompt };
    var desc = (document.getElementById('cf-desc')?.value || '').trim();
    if (desc) params.description = desc;
  } else if (rtype === 'task_defs') {
    cmd = 'create_task_def';
    params = { name: name, prompt: prompt };
    var criteria = (document.getElementById('cf-criteria')?.value || '').trim();
    var interval = (document.getElementById('cf-interval')?.value || '').trim();
    var verifier = (document.getElementById('cf-verifier')?.value || '').trim();
    if (criteria) params.criteria = criteria;
    if (interval) params.interval = interval;
    if (verifier) params.verifier = verifier;
  } else if (rtype === 'prompts') {
    cmd = 'create_resource';
    params = { resource_type: 'prompt', name: name, content: prompt };
    var desc = (document.getElementById('cf-desc')?.value || '').trim();
    if (desc) params.description = desc;
  } else if (rtype === 'variables') {
    var vkey = (document.getElementById('cf-key')?.value || '').trim();
    var vvalue = (document.getElementById('cf-value')?.value || '');
    var vscope = (document.getElementById('cf-scope')?.value || 'user');
    if (!vkey) return;
    cmd = 'set_param';
    params = { key: vkey, value: vvalue, scope: vscope };
  } else if (rtype === 'secrets') {
    var skey = (document.getElementById('cf-key')?.value || '').trim();
    var svalue = (document.getElementById('cf-value')?.value || '');
    var sscope = (document.getElementById('cf-scope')?.value || 'user');
    if (!skey) return;
    cmd = 'set_secret';
    params = { key: skey, value: svalue, scope: sscope };
  } else if (rtype === 'services') {
    var svcType = (document.getElementById('cf-svctype') as HTMLSelectElement)?.value || '';
    var svcName = (document.getElementById('cf-name') as HTMLInputElement)?.value?.trim() || '';
    var svcDesc = (document.getElementById('cf-desc') as HTMLInputElement)?.value?.trim() || '';
    if (!svcType || !svcName) return;
    var config: Record<string, any> = {};
    // Collect schema-based params
    var paramsDiv = document.getElementById('cf-svc-params');
    if (paramsDiv && _cachedSvcSchema) {
      for (var pname in _cachedSvcSchema) {
        var el = document.getElementById('cf-sp-' + pname);
        if (!el) continue;
        var pdef = _cachedSvcSchema[pname];
        if (pdef.type === 'boolean') config[pname] = (el as HTMLInputElement).checked;
        else if (pdef.type === 'integer') config[pname] = parseInt((el as HTMLInputElement).value) || 0;
        else if (pdef.type === 'float') config[pname] = parseFloat((el as HTMLInputElement).value) || 0;
        else if (pdef.type === 'map' || pdef.type === 'object') {
          try { config[pname] = JSON.parse((el as HTMLTextAreaElement).value || '{}'); } catch { config[pname] = {}; }
        } else config[pname] = (el as HTMLInputElement).value || '';
      }
    }
    cmd = 'service_install';
    params = { service_type: svcType, service_name: svcName, description: svcDesc, config: config };
  } else if (rtype === 'flows') {
    var templateId = (document.getElementById('cf-template')?.value || '').trim();
    var flowScope = (document.getElementById('cf-scope')?.value || 'user');
    var flowParams = (document.getElementById('cf-params')?.value || '').trim();
    if (!templateId) return;
    cmd = 'deploy_flow';
    params = { template_id: templateId, scope: flowScope };
    if (flowParams) {
      try { params.parameters = JSON.parse(flowParams); } catch(e) { statusEl.textContent = 'Invalid JSON'; return; }
    }
  }

  if (cmd) {
    vscode.postMessage({ type: 'command', command: cmd, arg: JSON.stringify(params) });
  }
  closePanel();
  statusEl.textContent = rtype.replace(/s$/, '') + ' "' + name + '" created';
  setTimeout(function() { statusEl.textContent = ''; }, 3000);
  setTimeout(function() { loadResourcesPanel(); }, 500);
}

function closePanel() {
  document.getElementById('panelOverlay').className = 'panel-overlay';
}

// ── Service schema-based form ─────────────────────────────────────
var _cachedSvcSchema: Record<string, any> | null = null;

function _onSvcTypeChange() {
  var sel = document.getElementById('cf-svctype') as HTMLSelectElement;
  var svcType = sel ? sel.value : '';
  var paramsDiv = document.getElementById('cf-svc-params');
  if (!paramsDiv || !svcType) { if (paramsDiv) paramsDiv.innerHTML = ''; return; }
  paramsDiv.innerHTML = '<div style="color:var(--vscode-descriptionForeground);font-size:11px;padding:4px">Loading schema...</div>';
  vscode.postMessage({ type: 'command', command: 'get_service_schema', arg: JSON.stringify({ service_type: svcType }) });
}

function _renderSvcSchemaParams(schema: Record<string, any>, values: Record<string, any> = {}) {
  _cachedSvcSchema = schema;
  var paramsDiv = document.getElementById('cf-svc-params');
  if (!paramsDiv) return;
  var html = '';
  for (var pname in schema) {
    var p = schema[pname];
    var val = values[pname] !== undefined ? values[pname] : (p.default !== undefined ? p.default : '');
    var req = p.required === true ? ' *' : '';
    var desc = p.description ? '<div style="font-size:10px;color:var(--vscode-descriptionForeground)">' + p.description + '</div>' : '';
    html += '<label style="' + _cfLabelStyle + '">' + pname + req + '</label>' + desc;
    if (p.type === 'boolean') {
      html += '<div style="margin:2px 0 8px"><input type="checkbox" id="cf-sp-' + pname + '"' + (val ? ' checked' : '') + '></div>';
    } else if (p.type === 'select' && p.options) {
      html += '<select id="cf-sp-' + pname + '" style="' + _cfInputStyle + '">';
      for (var opt of p.options) {
        html += '<option value="' + opt + '"' + (opt === val ? ' selected' : '') + '>' + opt + '</option>';
      }
      html += '</select>';
    } else if (p.type === 'integer' || p.type === 'float') {
      html += '<input type="number" id="cf-sp-' + pname + '" value="' + val + '" style="' + _cfInputStyle + '">';
    } else if (p.type === 'map' || p.type === 'object' || p.type === 'textarea') {
      var textVal = typeof val === 'object' ? JSON.stringify(val, null, 2) : String(val);
      html += '<textarea id="cf-sp-' + pname + '" style="' + _cfTextareaStyle + '">' + textVal + '</textarea>';
    } else if (p.sensitive) {
      html += '<input type="password" id="cf-sp-' + pname + '" value="' + val + '" style="' + _cfInputStyle + '">';
    } else {
      html += '<input id="cf-sp-' + pname + '" value="' + val + '" style="' + _cfInputStyle + '">';
    }
  }
  paramsDiv.innerHTML = html;
}

function loadResourcesPanel() {
  vscode.postMessage({ type: 'command', command: 'list_resources' });
  // Result handled in actionResult handler below
  _pendingPanel = 'resources';
}

function loadContextPanel() {
  vscode.postMessage({ type: 'command', command: 'get_context' });
  _pendingPanel = 'context';
}

function loadFilesPanel() {
  vscode.postMessage({ type: 'command', command: 'list_conv_files' });
  _pendingPanel = 'files';
}

function loadToolsPanel() {
  vscode.postMessage({ type: 'command', command: 'list_tools' });
  _pendingPanel = 'tools';
}

function loadAccountsPanel() {
  vscode.postMessage({ type: 'command', command: 'list_linked_accounts' });
  _pendingPanel = 'accounts';
}

function loadPlansPanel() {
  vscode.postMessage({ type: 'command', command: 'get_plans' });
  _pendingPanel = 'plans';
}

function unlinkAccount(provider) {
  if (!confirm('Unlink ' + provider + ' account?')) return;
  vscode.postMessage({ type: 'command', command: 'unlink_account', arg: JSON.stringify({ provider: provider }) });
  setTimeout(function() { loadAccountsPanel(); }, 500);
}

var _pendingPanel = '';

function renderPanelResult(action, data) {
  // Handle edit form data responses
  if (_pendingEdit && action === 'get_resource_detail') {
    _renderEditForm(_pendingEdit.rtype, _pendingEdit.name, data);
    _pendingEdit = null;
    return true;
  }
  if (_pendingEdit && _pendingEdit.rtype === '_service' && action === 'get_service_detail') {
    _renderServiceEditForm(_pendingEdit.name, data.config || data);
    _pendingEdit = null;
    return true;
  }

  // Populate flow start params form
  if (action === 'get_flow_instance') {
    _renderFlowStartParams(data);
    return true;
  }

  // Populate deploy flow template dropdown
  if (action === 'list_available_flows') {
    var sel = document.getElementById('cf-template');
    if (sel) {
      var templates = data.templates || [];
      sel.innerHTML = templates.map(function(t) {
        return '<option value="' + esc(t.id) + '">' + esc(t.name) + ' (' + t.tasks_count + ' tasks)' + (t.version ? ' v' + t.version : '') + '</option>';
      }).join('') || '<option>(no templates)</option>';
      var lbl = sel.previousElementSibling;
      if (lbl) lbl.textContent = 'Template';
    }
    return true;
  }

  // Populate service type dropdown
  if (action === 'list_service_types') {
    var svcSel = document.getElementById('cf-svctype') as HTMLSelectElement;
    if (svcSel) {
      var types = data.service_types || [];
      svcSel.innerHTML = types.map(function(t: any) {
        return '<option value="' + esc(t.type) + '">' + esc(t.name || t.type) + '</option>';
      }).join('') || '<option>(no types)</option>';
      var svcLbl = svcSel.previousElementSibling;
      if (svcLbl) svcLbl.textContent = 'Service type';
      // Auto-load schema for first type
      if (types.length) _onSvcTypeChange();
    }
    return true;
  }

  // Render service schema params (install or edit)
  if (action === 'get_service_schema') {
    var editConfig = (window as any)._editSvcConfig || {};
    _renderSvcSchemaParams(data.parameters || {}, editConfig);
    (window as any)._editSvcConfig = null;
    return true;
  }

  const overlay = document.getElementById('panelOverlay');
  if (!overlay || overlay.className !== 'panel-overlay visible') return false;

  if (action === 'list_resources' && _pendingPanel === 'resources') {
    _resData = data;
    let html = '<div class="panel-header"><h4>Resources</h4><button class="panel-close" onclick="closePanel()">\\u2715</button></div>';

    var sectionOrder = ['agents','skills','mcp','prompts','task_defs','flows','services','parameters','secrets'];
    var sectionLabels = {agents:'Agents',skills:'Skills',mcp:'MCP Servers',prompts:'Prompts',task_defs:'Tasks',flows:'Flows',services:'Services',parameters:'Variables',secrets:'Secrets'};

    for (var si = 0; si < sectionOrder.length; si++) {
      var rtype = sectionOrder[si];
      var items = data[rtype];
      if (!items) continue;
      if (!Array.isArray(items)) {
        // services/flows may be objects
        if (typeof items === 'object') {
          items = Object.entries(items).map(function(e) {
            var v = typeof e[1] === 'object' ? e[1] : {};
            v.id = v.id || e[0];
            v.name = v.name || v.id || e[0];
            return v;
          });
        } else continue;
      }
      if (!items.length) continue;

      var label = sectionLabels[rtype] || rtype;
      var canCreate = ['agents','skills','task_defs','prompts','services','parameters','secrets','flows'].indexOf(rtype) >= 0;
      var createType = rtype === 'parameters' ? 'variables' : rtype;
      var addBtn = canCreate ? ' <button style="background:none;border:none;color:var(--vscode-textLink-foreground);cursor:pointer;font-size:11px" onclick="event.stopPropagation();showCreateForm(\\'' + createType + '\\')">[+]</button>' : '';
      html += '<div class="res-section" onclick="this.classList.toggle(\\'collapsed\\')">'
        + '<span class="res-arrow">\\u25BC</span> <strong>' + esc(label) + '</strong> ' + addBtn + ' <span style="color:var(--vscode-descriptionForeground)">(' + items.length + ')</span></div>';
      html += '<div class="res-items">';
      for (var ii = 0; ii < items.length; ii++) {
        var item = items[ii];
        var name = item.name || item.id || item.service_id || item.instance_id || item.flow_name || item.key || '?';
        var scope = item.scope || item._scope || 'user';
        var scopeBadge = scope === 'global' ? ' <span style="color:var(--vscode-descriptionForeground);font-size:9px">[global]</span>' : (scope === 'conversation' ? ' <span style="color:var(--vscode-descriptionForeground);font-size:9px">[conv]</span>' : '');
        var active = item.active ? ' <span style="color:#3fb950">\\u2713</span>' : '';
        var enabled = item.enabled === false ? ' <span style="color:#f85149">(disabled)</span>' : '';
        var connected = item.connected ? ' <span style="color:#3fb950">(connected)</span>' : '';
        var desc = item.description || item.prompt || item.type || item.service_type || '';
        if (rtype === 'flows') {
          name = item.flow_name || item.instance_id || item.name || '?';
          var flowStatus = item.status || 'stopped';
          desc = flowStatus === 'running' ? '\\u25b6 running' : flowStatus === 'error' ? '\\u26a0 error' : '\\u23f9 stopped';
          if (item.template) desc += ' (' + item.template + ')';
          // Use instance_id for context menu
          name = item.instance_id || name;
        }
        if (rtype === 'parameters' && item.value != null) {
          desc = '= ' + String(item.value).slice(0, 40);
        }
        if (rtype === 'secrets') {
          desc = '(encrypted)';
        }
        if (desc.length > 60) desc = desc.slice(0, 60) + '...';
        var statusBadge = active || enabled || connected;

        var ctxAttr = scope !== 'global' ? 'oncontextmenu="showResMenu(event,\\'' + esc(rtype) + '\\',\\'' + esc(name).replace(/'/g, "\\\\\\'") + '\\')"' : '';
        html += '<div class="panel-item" ' + ctxAttr + '>'
          + '<span style="font-weight:500">' + esc(name) + '</span>' + scopeBadge + statusBadge
          + (desc ? '<br><span style="color:var(--vscode-descriptionForeground);font-size:10px">' + esc(desc) + '</span>' : '')
          + '</div>';
      }
      html += '</div>';
    }

    overlay.innerHTML = html;
    _pendingPanel = '';
    return true;
  }

  if (action === 'get_context' && _pendingPanel === 'context') {
    const msgs = data.context || data.messages || [];
    const tokens = data.token_estimate || 0;
    const ctxs = data.agent_contexts || {};
    let html = '<div class="panel-header"><h4>LLM Context (' + msgs.length + ' msgs, ~' + tokens + ' tokens)</h4><button class="panel-close" onclick="closePanel()">\\u2715</button></div>';
    if (Object.keys(ctxs).length) {
      html += '<div style="font-size:10px;color:var(--vscode-descriptionForeground);margin-bottom:6px">Contexts: '
        + Object.entries(ctxs).filter(function(e){return e[0]!=="*"}).map(function(e){return e[0]+" ("+e[1]+")"}).join(", ") + '</div>';
    }
    for (const m of msgs.slice(-30)) {
      const role = m.role || '?';
      const content = (m.content || '').slice(0, 150);
      html += '<div class="panel-item"><span style="color:' + ({system:"#6c6c8a",user:"#4fc3f7",assistant:"#4ecdc4",tool:"#f4a261"}[role]||"#808090") + '">' + role + '</span> ' + esc(content) + '</div>';
    }
    overlay.innerHTML = html;
    _pendingPanel = '';
    return true;
  }

  if (action === 'list_conv_files' && _pendingPanel === 'files') {
    const files = data.files || [];
    let html = '<div class="panel-header"><h4>Files (' + files.length + ')</h4><button class="panel-close" onclick="closePanel()">\\u2715</button></div>';
    if (!files.length) html += '<div class="msg system">No files</div>';
    for (const f of files) {
      html += '<div class="panel-item">' + esc(f.file_id?.slice(0,8) || '?') + ' ' + esc(f.filename || '?') + ' (' + (f.size||0).toLocaleString() + ' bytes)</div>';
    }
    overlay.innerHTML = html;
    _pendingPanel = '';
    return true;
  }

  if (action === 'list_linked_accounts' && _pendingPanel === 'accounts') {
    var links = data.links || {};
    var providers = Object.keys(links);
    let html = '<div class="panel-header"><h4>Linked Accounts (' + providers.length + ')</h4><button class="panel-close" onclick="closePanel()">\\u2715</button></div>';
    if (!providers.length) {
      html += '<div class="msg system">No linked accounts. Use /link &lt;provider&gt; &lt;id&gt; to link one.</div>';
    }
    for (var pi = 0; pi < providers.length; pi++) {
      var provider = providers[pi];
      var channelId = links[provider];
      html += '<div class="panel-item" style="display:flex;align-items:center;justify-content:space-between">'
        + '<span><strong>' + esc(provider) + '</strong> \\u2014 ' + esc(String(channelId)) + '</span>'
        + '<button onclick="unlinkAccount(\\'' + esc(provider) + '\\')" style="background:none;border:none;color:var(--vscode-errorForeground);cursor:pointer;font-size:11px">\\u2715 Unlink</button>'
        + '</div>';
    }
    overlay.innerHTML = html;
    _pendingPanel = '';
    return true;
  }

  if (action === 'list_tools' && _pendingPanel === 'tools') {
    const tools = data.tools || [];
    let html = '<div class="panel-header"><h4>Tools (' + tools.length + ')</h4><button class="panel-close" onclick="closePanel()">\\u2715</button></div>';
    for (const t of tools) {
      html += '<div class="panel-item"><strong>' + esc(t.name || '?') + '</strong> <span style="color:var(--vscode-descriptionForeground)">' + esc((t.description||'').slice(0,80)) + '</span></div>';
    }
    overlay.innerHTML = html;
    _pendingPanel = '';
    return true;
  }

  if (action === 'get_plans' && _pendingPanel === 'plans') {
    var planArr = Array.isArray(data.plans) ? data.plans : Object.values(data.plans || {});
    let html = '<div class="panel-header"><h4>Plans (' + planArr.length + ')</h4><button class="panel-close" onclick="closePanel()">\\u2715</button></div>';
    html += '<div style="padding:4px 8px"><button onclick="createPlanDialog()" style="padding:4px 10px;background:var(--vscode-button-background);color:var(--vscode-button-foreground);border:none;border-radius:4px;cursor:pointer;font-size:11px">+ Create Plan</button></div>';
    if (!planArr.length) {
      html += '<div class="msg system">No active plans. Use /plan &lt;description&gt; to ask the agent to create one.</div>';
    }
    for (var pi = 0; pi < planArr.length; pi++) {
      var plan = planArr[pi];
      var pid = plan.id || ('plan_' + pi);
      if (!plan || !plan.title) continue;
      var steps = plan.steps || [];
      var doneCount = steps.filter(function(s: any) { return s.status === 'done'; }).length;
      var total = steps.length;
      var pct = total > 0 ? Math.round((doneCount / total) * 100) : 0;
      var planStatus = plan.status || 'unknown';
      var statusColors: any = {'pending_approval':'#f0ad4e','approved':'#6c5ce7','in_progress':'#3498db','completed':'#4ecdc4','cancelled':'#e94560'};
      var sColor = statusColors[planStatus] || '#808090';

      html += '<div class="panel-item" style="border-left:3px solid ' + sColor + ';padding:6px 8px;margin:4px 0" oncontextmenu="showPlanCtx(event,\\'' + esc(pid) + '\\',\\'' + esc(planStatus) + '\\');return false">';
      html += '<div style="display:flex;justify-content:space-between;align-items:center">';
      html += '<strong>' + esc(plan.title) + '</strong>';
      html += '<span style="font-size:9px;padding:1px 5px;border-radius:3px;color:' + sColor + '">' + esc(planStatus) + '</span>';
      html += '</div>';
      // Progress bar
      var barColor = pct === 100 ? '#4ecdc4' : pct > 50 ? '#6c5ce7' : '#f0ad4e';
      html += '<div style="height:3px;background:var(--vscode-panel-border);border-radius:2px;margin:4px 0;overflow:hidden">';
      html += '<div style="height:100%;width:' + pct + '%;background:' + barColor + ';border-radius:2px"></div></div>';
      html += '<div style="font-size:10px;color:var(--vscode-descriptionForeground)">' + doneCount + '/' + total + ' steps done (' + pct + '%)</div>';
      // Steps
      for (var si = 0; si < steps.length; si++) {
        var step = steps[si];
        var stepIcons: any = {'pending':'\\u25cb','in_progress':'\\u25d4','done':'\\u2713','skipped':'\\u2013','error':'\\u2717'};
        var stepColors: any = {'pending':'var(--vscode-descriptionForeground)','in_progress':'#6c5ce7','done':'#4ecdc4','skipped':'#555','error':'#e94560'};
        var sIcon = stepIcons[step.status] || '\\u25cb';
        var sSColor = stepColors[step.status] || 'var(--vscode-descriptionForeground)';
        var sDeco = step.status === 'skipped' ? 'line-through' : 'none';
        var assignee = step.assigned_to ? ' [' + esc(step.assigned_to) + ']' : '';
        html += '<div style="font-size:11px;color:' + sSColor + ';text-decoration:' + sDeco + ';margin:1px 0;padding-left:8px" oncontextmenu="showPlanStepCtx(event,\\'' + esc(pid) + '\\',' + step.index + ',\\'' + esc(step.status) + '\\');return false">';
        html += sIcon + ' ' + step.index + '. ' + esc(step.description) + assignee;
        if (step.note) html += ' <span style="color:#555;font-style:italic">' + esc(step.note) + '</span>';
        html += '</div>';
      }
      html += '</div>';
    }
    overlay.innerHTML = html;
    _pendingPanel = '';
    return true;
  }

  return false;
}

// ── Plan context menus ──
function showPlanCtx(e: any, planId: string, planStatus: string) {
  e.preventDefault();
  e.stopPropagation();
  var old = document.querySelector('.res-ctx');
  if (old) old.remove();
  var menu = document.createElement('div');
  menu.className = 'res-ctx';
  menu.style.left = e.clientX + 'px';
  menu.style.top = e.clientY + 'px';
  function addItem(label: string, fn: () => void) {
    var d = document.createElement('div');
    d.textContent = label;
    d.onclick = function() { menu.remove(); fn(); };
    menu.appendChild(d);
  }
  if (planStatus === 'pending_approval') {
    addItem('\\u2705 Approve', function() { sendCmd('approve_plan', JSON.stringify({plan_id: planId})); setTimeout(loadPlansPanel, 500); });
  }
  if (planStatus !== 'cancelled' && planStatus !== 'completed') {
    addItem('\\u27A4 Assign to...', function() { assignPlanDialog(planId); });
  }
  if (planStatus !== 'cancelled' && planStatus !== 'completed') {
    addItem('\\u23F9 Cancel', function() { sendCmd('cancel_plan', JSON.stringify({plan_id: planId})); setTimeout(loadPlansPanel, 500); });
  }
  addItem('\\u2716 Delete', function() { sendCmd('delete_plan', JSON.stringify({plan_id: planId})); setTimeout(loadPlansPanel, 500); });
  document.body.appendChild(menu);
  setTimeout(function() { document.addEventListener('click', function() { menu.remove(); }, {once: true}); }, 0);
}

function showPlanStepCtx(e: any, planId: string, stepIndex: number, currentStatus: string) {
  e.preventDefault();
  e.stopPropagation();
  var old = document.querySelector('.res-ctx');
  if (old) old.remove();
  var menu = document.createElement('div');
  menu.className = 'res-ctx';
  menu.style.left = e.clientX + 'px';
  menu.style.top = e.clientY + 'px';
  function addItem(label: string, fn: () => void) {
    var d = document.createElement('div');
    d.textContent = label;
    d.onclick = function() { menu.remove(); fn(); };
    menu.appendChild(d);
  }
  if (currentStatus !== 'done') {
    addItem('\\u2713 Mark Done', function() { sendCmd('update_plan_step', JSON.stringify({plan_id: planId, step: stepIndex, status: 'done'})); setTimeout(loadPlansPanel, 500); });
  }
  if (currentStatus !== 'in_progress') {
    addItem('\\u25d4 In Progress', function() { sendCmd('update_plan_step', JSON.stringify({plan_id: planId, step: stepIndex, status: 'in_progress'})); setTimeout(loadPlansPanel, 500); });
  }
  if (currentStatus !== 'skipped') {
    addItem('\\u2013 Skip', function() { sendCmd('update_plan_step', JSON.stringify({plan_id: planId, step: stepIndex, status: 'skipped'})); setTimeout(loadPlansPanel, 500); });
  }
  if (currentStatus !== 'pending') {
    addItem('\\u25cb Reset', function() { sendCmd('update_plan_step', JSON.stringify({plan_id: planId, step: stepIndex, status: 'pending'})); setTimeout(loadPlansPanel, 500); });
  }
  if (currentStatus === 'pending' || currentStatus === 'in_progress' || currentStatus === 'error') {
    addItem('\\u27A4 Assign to...', function() { assignStepDialog(planId, stepIndex); });
  }
  document.body.appendChild(menu);
  setTimeout(function() { document.addEventListener('click', function() { menu.remove(); }, {once: true}); }, 0);
}

function assignPlanDialog(planId: string) {
  var old = document.querySelector('.res-ctx');
  if (old) old.remove();
  vscode.postMessage({ type: 'command', command: 'assign_plan_dialog', arg: planId });
}

function assignStepDialog(planId: string, stepIndex: number) {
  var old = document.querySelector('.res-ctx');
  if (old) old.remove();
  vscode.postMessage({ type: 'command', command: 'assign_step_dialog', arg: JSON.stringify({ plan_id: planId, step: stepIndex }) });
}

function createPlanDialog() {
  vscode.postMessage({ type: 'command', command: 'create_plan_dialog' });
}

// Update relay status
function updateRelayStatus(status) {
  const dot = document.getElementById('relayDot');
  const label = document.getElementById('relayLabel');
  if (status === 'running') {
    dot.className = 'relay-dot on';
    label.textContent = 'Relay \\u2713';
  } else {
    dot.className = 'relay-dot off';
    label.textContent = 'Relay \\u2717';
  }
}

// Auto-resize textarea
inputEl.addEventListener('input', function() {
  inputEl.style.height = '36px';
  inputEl.style.height = Math.min(inputEl.scrollHeight, 120) + 'px';
});
</script>
</body></html>`;
  }
}

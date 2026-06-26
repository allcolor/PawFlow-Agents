import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';
import { AgentAPIClient } from '../api/client';
import { SSEClient } from '../api/sse';
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
  ) {}

  getConversationId(): string | null { return this.conversationId; }

  resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    view.webview.options = {
      enableScripts: true,
      localResourceRoots: [
        vscode.Uri.joinPath(this.context.extensionUri, 'media', 'webview'),
        this.context.extensionUri,
      ],
    };
    view.webview.html = this.getHtml();

    view.webview.onDidReceiveMessage((msg) => {
      // All handlers fire-and-forget: webview posts, extension dispatches,
      // results come back via SSE or postMessage. No awaiting network calls
      // in the message loop — that would block subsequent message processing.
      switch (msg.type) {
        case 'sendMessage':
          this.sendMessage(msg.text, msg.attachments, msg.reply_to, msg.msg_id);
          break;
        case 'newConversation':
        case 'openNewConversation':
          this.openNewConversation();
          break;
        case 'createConversation':
          this.createConversation({
            agent: msg.agent, llm_service: msg.llm_service,
            relays: msg.relays, title: msg.title,
          });
          break;
        case 'loadConversations':
          this.loadConversations();
          break;
        case 'resumeConversation':
          this.resumeConversation(msg.conversationId, msg.offset);
          break;
        case 'approval':
          this.handleApproval(msg.requestId, msg.result, msg.approvalType);
          break;
        case 'backgroundTool':
          this.handleBackgroundTool(msg.tcId);
          break;
        case 'killTool':
          this.handleKillTool(msg.tcId);
          break;
        case 'attachImage':
          if (msg.data && msg.mime_type) {
            this.pendingAttachments.push({
              filename: msg.filename || 'pasted.png',
              mime_type: msg.mime_type,
              data: msg.data,
            });
            this.postMessage({ type: 'fileAttached', filename: msg.filename, count: this.pendingAttachments.length });
          }
          break;
        case 'openFile':
          // Open a local file in VS Code. Relay-backed fs:// paths are owned
          // by server relay bindings and cannot be mapped by the extension.
          try {
            const filePath = msg.path || '';
            if (filePath.startsWith('fs://')) {
              vscode.window.showWarningMessage(
                'Relay files are managed by PawFlow; open them from webchat or FileStore.'
              );
              break;
            }
            const uri = vscode.Uri.file(filePath);
            vscode.window.showTextDocument(uri, { preview: true });
          } catch (e: any) {
            vscode.window.showWarningMessage(`Cannot open file: ${e.message}`);
          }
          break;
        case 'command':
          if (msg.command === 'clipboard_write') {
            vscode.env.clipboard.writeText(msg.arg || '').then(() => {
              this.postMessage({ type: 'actionResult', action: 'clipboard_write', data: { ok: true } });
            });
          } else if (msg.command === 'clipboard_read') {
            vscode.env.clipboard.readText().then((text) => {
              this.postMessage({ type: 'clipboardContent', text });
            });
          } else if (msg.command === 'clear_attachments') {
            this.pendingAttachments = [];
            this.postMessage({ type: 'actionResult', action: 'clear_attachments', data: { ok: true } });
          } else if (msg.command === 'assign_plan_dialog') {
            this.showAssignPlanDialog(msg.arg);
          } else if (msg.command === 'assign_step_dialog') {
            const parsed = JSON.parse(msg.arg || '{}');
            this.showAssignStepDialog(parsed.plan_id, parsed.step);
          } else if (msg.command === 'create_plan_dialog') {
            this.showCreatePlanDialog();
          } else {
            this.sendCommand(msg.command, msg.arg);
          }
          break;
      }
    });

    this.resumeLastConversation();
    this.setupSSE();
  }

 
  async sendMessage(text: string, attachments?: Attachment[], replyTo?: ReplyTo, msgId?: string): Promise<void> {
    const api = this.getApi();
    if (!api) {
      this.postMessage({ type: 'error', message: 'Not logged in. Run PawFlow: Login.' });
      return;
    }

    // A message only exists inside a conversation. Never create one
    // implicitly on send — require an explicit New conversation or a
    // selection from the Conversations tab first (same rule as the webchat).
    if (!this.conversationId) {
      this.postMessage({ type: 'error',
        message: 'No conversation selected. Click “+ New” to create one (pick an agent and relays), or open one from the Conversations tab.' });
      this.postMessage({ type: 'requireConversation' });
      return;
    }

    try {
      const allAttachments = [...this.pendingAttachments, ...(attachments || [])];
      this.pendingAttachments = [];

      // The conversation's active agent is the target; fall back to resolving
      // it once if the panel hasn't captured it yet.
      if (!this.selectedAgent) {
        await this.resolveDefaultAgent();
      }

      // Upload attachments to the FileStore and send file_id references —
      // the webchat renders user attachments from file_id, raw base64
      // payloads display as broken images there. Base64 is the fallback
      // when the upload fails (the agent still receives the image).
      const sendAttachments: any[] = [];
      for (const att of allAttachments) {
        if ((att as any).file_id || !att.data) { sendAttachments.push(att); continue; }
        try {
          const info = await api.uploadFile(
            att.filename, att.mime_type, att.data, this.conversationId || undefined);
          if (info && info.file_id) {
            sendAttachments.push({ filename: att.filename, mime_type: att.mime_type, file_id: info.file_id });
            continue;
          }
        } catch (e) {
          console.warn('[PawFlow] attachment upload failed, sending inline:', e);
        }
        sendAttachments.push(att);
      }

      const resp = await api.sendMessage({
        message: text,
        conversation_id: this.conversationId || undefined,
        target_agent: this.selectedAgent || undefined,
        attachments: sendAttachments.length ? sendAttachments : undefined,
        reply_to: replyTo || undefined,
        msg_id: msgId || undefined,
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

  // "+ New" opens a creation panel (agent / LLM / relays / title) like the
  // webchat, then creates the conversation server-side. A bare clear is not
  // enough — a message can only exist inside a real conversation.
  async openNewConversation(): Promise<void> {
    const api = this.getApi();
    if (!api) {
      this.postMessage({ type: 'error', message: 'Not logged in. Run PawFlow: Login.' });
      return;
    }
    try {
      const [agentsResp, svcResp, relayResp] = await Promise.all([
        api.sendAction('list_repo_agents', { conversation_id: '' }),
        api.sendAction('list_services', { service_type: 'llmConnection', conversation_id: '' }),
        api.sendAction('relay_list_available', {}),
      ]);
      const agents = ((agentsResp as any).agents || [])
        .map((a: any) => ({ name: a.name || a.id || '', description: a.description || '' }))
        .filter((a: any) => a.name);
      const services = ((svcResp as any).services || [])
        .filter((s: any) => s.enabled !== false)
        .map((s: any) => s.service_id || s.id || '').filter(Boolean);
      const relays = ((relayResp as any).relays || [])
        .map((r: any) => ({ id: r.relay_id || r.id || '', connected: r.connected !== false }))
        .filter((r: any) => r.id);
      if (!agents.length) {
        this.postMessage({ type: 'error',
          message: 'No agent definitions available. Create an agent in the webchat first.' });
        return;
      }
      this.postMessage({ type: 'newConversationForm', agents, services, relays });
    } catch (e: any) {
      this.postMessage({ type: 'error', message: `Could not load creation options: ${e?.message || e}` });
    }
  }

  async createConversation(opts: { agent: string; llm_service?: string;
                                   relays?: string[]; title?: string }): Promise<void> {
    const api = this.getApi();
    if (!api) { return; }
    const agent = (opts.agent || '').trim();
    if (!agent) {
      this.postMessage({ type: 'error', message: 'Pick an agent to create a conversation.' });
      return;
    }
    const agentEntry: any = {
      instance_name: agent, definition: agent, params: { name: agent },
    };
    if (opts.llm_service) { agentEntry.llm_service = opts.llm_service; }
    const payload: any = { agents: [agentEntry] };
    if (opts.title && opts.title.trim()) { payload.title = opts.title.trim(); }
    const relays = (opts.relays || []).filter(Boolean);
    if (relays.length) { payload.relays = relays; payload.default_relay = relays[0]; }
    try {
      const data = await api.sendAction('create_conversation', payload);
      if ((data as any).error) {
        this.postMessage({ type: 'error', message: (data as any).error });
        return;
      }
      const cid = (data as any).conversation_id || '';
      if (!cid) {
        this.postMessage({ type: 'error', message: 'Conversation creation returned no id.' });
        return;
      }
      const sse = this.getSse();
      if (sse) { sse.disconnect(); }
      this.conversationId = cid;
      this.selectedAgent = agent;
      this.saveLastConversation(cid);
      this._sseConversationId = null;
      this.setupSSE();
      this.postMessage({ type: 'conversationCreated', conversationId: cid, agent });
      this.postMessage({ type: 'newConversation' });
      this.postMessage({ type: 'agentSelected', agent });
    } catch (e: any) {
      this.postMessage({ type: 'error', message: `Create failed: ${e?.message || e}` });
    }
  }

  selectAgent(name: string): void {
    this.selectedAgent = name;
    this.postMessage({ type: 'agentSelected', agent: name });
  }

  private async resolveDefaultAgent(): Promise<void> {
    const api = this.getApi();
    if (!api) { return; }
    try {
      const data = await api.sendAction('list_agents', {
        conversation_id: this.conversationId || '',
      });
      // list_agents returns {agents: {name: {...}}, selected: '...'}
      const agents = data.agents || {};
      const names = Array.isArray(agents)
        ? agents.map((a: any) => a.name || a)
        : Object.keys(agents);
      const name = (data.selected as string)
        || names.find(n => n === 'assistant')
        || names[0] || '';
      if (name) { this.selectAgent(name); }
    } catch (e) {
      console.warn('[PawFlow] resolveDefaultAgent failed:', e);
    }
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
      // Silent for polling commands — network errors are expected
      const silentCommands = ['list_active', 'poll', 'ping'];
      if (!silentCommands.includes(command)) {
        this.postMessage({ type: 'error', message: e.message });
      }
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

  async resumeLastConversation(): Promise<void> {
    // Re-entrant: also called by the session bring-up once the API client
    // is ready (the view usually resolves before login completes).
    if (this.conversationId) { return; }
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
        if (data.active_agent) { this.selectedAgent = data.active_agent; }
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

  private async _catchUpHistory(): Promise<void> {
    // Reload the latest history for the live conversation after an SSE
    // reconnect, without re-arming SSE (it's already connected). The webview
    // dedups by msg_id, so already-shown messages are not duplicated.
    const api = this.getApi();
    const cid = this.conversationId;
    if (!api || !cid) { return; }
    try {
      const data = await api.sendAction('load_history', {
        conversation_id: cid, limit: 50, offset: 0,
      });
      if (!data.error) {
        if (data.active_agent) { this.selectedAgent = data.active_agent; }
        this.postMessage({ type: 'history', data, append: false });
      }
    } catch (e: any) {
      console.warn('[PawFlow] catch-up history failed:', e?.message || e);
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
        if (data.active_agent) { this.selectedAgent = data.active_agent; }
        this.setupSSE();
        const isLoadMore = (offset || 0) > 0;
        this.postMessage({ type: 'history', data, append: isLoadMore });
      }
    } catch (e: any) {
      console.error('[PawFlow] resumeConversation failed:', e);
      this.postMessage({ type: 'error', message: `Failed to load conversation: ${e?.message || e}` });
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
    // EventEmitter 'error' with no listener throws in the extension host —
    // a dropped SSE connection must never take the extension down.
    sse.on('error', (e: Error) => {
      console.warn('[PawFlow] SSE error:', e.message);
    });
    // On reconnect (after a stale stream / long sleep / proxy idle-kill),
    // events that fired while disconnected were missed. Reload history so
    // the panel catches up — the same effect as re-selecting the
    // conversation, but automatic, like the webchat.
    sse.on('reconnected', () => {
      void this._catchUpHistory();
    });
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
      const rawAgents = data.agents || {};
      agentNames = Array.isArray(rawAgents)
        ? rawAgents.map((a: any) => a.name || a)
        : Object.keys(rawAgents);
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

  private async handleKillTool(tcId: string): Promise<void> {
    const api = this.getApi();
    if (!api || !tcId) { return; }
    try {
      await api.sendAction('cancel_bg_tool', {
        tc_id: tcId,
        conversation_id: this.conversationId || '',
      });
    } catch (e: any) {
      this.postMessage({ type: 'error', message: `Kill tool failed: ${e.message}` });
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
      vscode.Uri.joinPath(extensionUri, 'media', 'webview', 'styles.css')
    ) + v;
    const chatUri = webview.asWebviewUri(
      vscode.Uri.joinPath(extensionUri, 'media', 'webview', 'chat.js')
    ) + v;
    // chat_handlers.js = SSE/approval/history message handlers split out of
    // chat.js (<=800 lines); must load right after chat.js (shares globals).
    const chatHandlersUri = webview.asWebviewUri(
      vscode.Uri.joinPath(extensionUri, 'media', 'webview', 'chat_handlers.js')
    ) + v;
    const commandsUri = webview.asWebviewUri(
      vscode.Uri.joinPath(extensionUri, 'media', 'webview', 'commands.js')
    ) + v;
    const panelsUri = webview.asWebviewUri(
      vscode.Uri.joinPath(extensionUri, 'media', 'webview', 'panels.js')
    ) + v;
    const formsUri = webview.asWebviewUri(
      vscode.Uri.joinPath(extensionUri, 'media', 'webview', 'forms.js')
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
<script src="${chatHandlersUri}"></script>
<script src="${commandsUri}"></script>
<script src="${panelsUri}"></script>
<script src="${formsUri}"></script>
</body></html>`;
  }
}

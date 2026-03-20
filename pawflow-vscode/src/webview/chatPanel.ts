import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';
import { AgentAPIClient } from '../api/client';
import { SSEClient } from '../api/sse';
import { RelayManager } from '../relay/manager';
import { SSEEvent, Attachment } from '../api/types';

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
          await this.sendMessage(msg.text, msg.attachments);
          break;
        case 'newConversation':
          this.newConversation();
          break;
        case 'loadConversations':
          await this.loadConversations();
          break;
        case 'resumeConversation':
          await this.resumeConversation(msg.conversationId);
          break;
        case 'approval':
          await this.handleApproval(msg.requestId, msg.result, msg.approvalType);
          break;
        case 'command':
          await this.sendCommand(msg.command, msg.arg);
          break;
      }
    });

    // Connect SSE if we have a conversation
    this.setupSSE();
  }

  async sendMessage(text: string, attachments?: Attachment[]): Promise<void> {
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
      });
      console.log('[PawFlow] sendMessage response:', JSON.stringify(resp).slice(0, 500));

      if (resp.error) {
        this.postMessage({ type: 'error', message: resp.error });
        return;
      }

      if (resp.conversation_id) {
        this.conversationId = resp.conversation_id;
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
    const msg = `[PLAN MODE — Read-only strategy. Analyze the request, outline the approach step by step. Do NOT make any changes yet.]\n\n${description}`;
    this.sendMessage(msg);
  }

  async sendCommand(command: string, arg?: string): Promise<void> {
    const api = this.getApi();
    if (!api) { return; }
    try {
      const resp = await api.sendAction(command, {
        conversation_id: this.conversationId || '',
        agent_name: arg || '',
      });
      this.postMessage({ type: 'actionResult', action: command, data: resp });
    } catch (e: any) {
      this.postMessage({ type: 'error', message: e.message });
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

  private async resumeConversation(cid: string): Promise<void> {
    const api = this.getApi();
    if (!api) { return; }
    try {
      const data = await api.sendAction('load_history', {
        conversation_id: cid, limit: 50, offset: 0,
      });
      if (!data.error) {
        this.conversationId = cid;
        this.setupSSE();
        this.postMessage({ type: 'history', data });
      }
    } catch {}
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

  private setupSSE(): void {
    if (!this.conversationId) { return; }
    const sse = this.getSse();
    if (!sse) { return; }

    sse.removeAllListeners();
    sse.on('event', (event: SSEEvent) => {
      this.postMessage({ type: 'sseEvent', event });

      // Show approval as VSCode notification (visible even if chat is hidden)
      if (event.event === 'exec_approval_request' || event.event === 'tool_approval_request') {
        this.showApprovalNotification(event);
      }
    });
    sse.connect(this.conversationId);
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
body { font-family: var(--vscode-font-family); background: var(--vscode-editor-background); color: var(--vscode-editor-foreground); display: flex; flex-direction: column; height: 100vh; }
.toolbar { display: flex; gap: 4px; padding: 4px; border-bottom: 1px solid var(--vscode-panel-border); }
.toolbar button { background: var(--vscode-button-secondaryBackground); color: var(--vscode-button-secondaryForeground); border: none; padding: 3px 8px; border-radius: 3px; cursor: pointer; font-size: 11px; }
.toolbar button:hover { background: var(--vscode-button-secondaryHoverBackground); }
.messages { flex: 1; overflow-y: auto; padding: 8px; }
.msg { margin-bottom: 8px; padding: 6px 8px; border-radius: 6px; font-size: 13px; line-height: 1.5; }
.msg.user { background: var(--vscode-input-background); border: 1px solid var(--vscode-input-border); }
.msg.assistant { background: var(--vscode-textBlockQuote-background); border-left: 3px solid var(--vscode-textLink-foreground); }
.msg.tool { font-size: 11px; color: var(--vscode-descriptionForeground); padding: 3px 8px; }
.msg.system { font-size: 11px; color: var(--vscode-descriptionForeground); text-align: center; }
.msg.error { color: var(--vscode-errorForeground); }
.badge { font-size: 10px; font-weight: 600; padding: 1px 6px; border-radius: 4px; margin-right: 4px; }
.status { font-size: 11px; color: var(--vscode-descriptionForeground); padding: 4px 8px; text-align: center; }
.input-area { display: flex; gap: 4px; padding: 4px; border-top: 1px solid var(--vscode-panel-border); }
.input-area textarea { flex: 1; background: var(--vscode-input-background); color: var(--vscode-input-foreground); border: 1px solid var(--vscode-input-border); border-radius: 4px; padding: 6px; font-family: var(--vscode-font-family); font-size: 13px; resize: none; min-height: 36px; max-height: 120px; }
.input-area button { background: var(--vscode-button-background); color: var(--vscode-button-foreground); border: none; padding: 6px 12px; border-radius: 4px; cursor: pointer; font-size: 12px; }
.approval { background: var(--vscode-inputValidation-warningBackground); border: 1px solid var(--vscode-inputValidation-warningBorder); padding: 8px; border-radius: 6px; margin: 4px 0; }
.approval button { margin: 2px; padding: 3px 10px; border: none; border-radius: 3px; cursor: pointer; font-size: 11px; }
pre { background: var(--vscode-textCodeBlock-background); padding: 8px; border-radius: 4px; overflow-x: auto; font-size: 12px; }
code { font-family: var(--vscode-editor-font-family); }
</style>
</head>
<body>
<div class="toolbar">
  <button onclick="newChat()">+ New</button>
  <button onclick="loadConvs()">Conversations</button>
  <button onclick="sendCmd('compact')">Compact</button>
</div>
<div class="messages" id="messages">
  <div class="msg system">PawFlow — Type a message to start</div>
</div>
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

function send() {
  const text = inputEl.value.trim();
  if (!text) return;
  addMsg('user', text);
  vscode.postMessage({ type: 'sendMessage', text });
  inputEl.value = '';
  inputEl.style.height = '36px';
}

function newChat() { vscode.postMessage({ type: 'newConversation' }); messagesEl.innerHTML = '<div class="msg system">New conversation</div>'; }
function loadConvs() { vscode.postMessage({ type: 'loadConversations' }); }
function sendCmd(cmd, arg) { vscode.postMessage({ type: 'command', command: cmd, arg }); }

function addMsg(type, content, meta) {
  const div = document.createElement('div');
  div.className = 'msg ' + type;
  if (type === 'user') {
    div.textContent = content;
  } else if (type === 'assistant') {
    const agent = meta?.agent_name || meta?.source?.name || 'assistant';
    const svc = meta?.source?.llm_service || '';
    div.innerHTML = '<span class="badge" style="background:var(--vscode-textLink-foreground);color:white">' + esc(agent) + (svc ? ' via ' + esc(svc) : '') + '</span>' + renderMd(content);
  } else if (type === 'tool_call') {
    div.innerHTML = '&#9889; ' + esc(content);
  } else if (type === 'tool_result') {
    const clean = content.replace(/\\[TOOL OUTPUT[^\\]]*\\]\\n?/g, '').replace(/\\n\\[\\/TOOL OUTPUT\\]/g, '');
    div.innerHTML = '&#10003; ' + esc(clean.slice(0, 200));
  } else {
    div.textContent = content;
  }
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
function renderMd(text) {
  // Basic markdown: **bold**, *italic*, \`code\`, \`\`\`blocks\`\`\`
  return text
    .replace(/\`\`\`(\\w*)\\n([\\s\\S]*?)\`\`\`/g, '<pre><code>$2</code></pre>')
    .replace(/\`([^\`]+)\`/g, '<code>$1</code>')
    .replace(/\\*\\*([^*]+)\\*\\*/g, '<strong>$1</strong>')
    .replace(/\\*([^*]+)\\*/g, '<em>$1</em>')
    .replace(/^- (.+)$/gm, '\\u2022 $1')
    .replace(/^#{1,3} (.+)$/gm, '<strong>$1</strong>')
    .replace(/\\n/g, '<br>');
}

// Handle messages from extension
window.addEventListener('message', (e) => {
  const msg = e.data;
  switch (msg.type) {
    case 'sseEvent':
      handleSSE(msg.event);
      break;
    case 'conversationList':
      showConvList(msg.conversations);
      break;
    case 'history':
      replayHistory(msg.data);
      break;
    case 'newConversation':
      messagesEl.innerHTML = '<div class="msg system">New conversation</div>';
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
  }
});

function handleSSE(event) {
  const { event: evType, data } = event;
  const agent = data.agent_name || 'assistant';

  if (evType === 'thinking' || evType === 'thinking_content') {
    statusEl.textContent = agent + ' thinking...';
  } else if (evType === 'token') {
    streaming[agent] = (streaming[agent] || '') + (data.text || '');
    statusEl.textContent = agent + ' writing... (' + streaming[agent].split(' ').length + 'w)';
  } else if (evType === 'tool_call') {
    const args = JSON.stringify(data.arguments || {}).slice(0, 100);
    addMsg('tool_call', agent + ' ' + (data.tool || '?') + '(' + args + ')');
  } else if (evType === 'tool_result') {
    addMsg('tool_result', data.result || '');
  } else if (evType === 'done') {
    const text = data.response || streaming[agent] || '';
    if (text) addMsg('assistant', text, data);
    streaming[agent] = '';
    const tin = data.tokens_in || 0;
    const tout = data.tokens_out || 0;
    statusEl.textContent = tin + ' in ' + tout + ' out' + (data.model ? ' | ' + data.model : '');
  } else if (evType === 'error_event') {
    addMsg('error', data.message || 'Error');
    statusEl.textContent = '';
  } else if (evType === 'cancelled') {
    statusEl.textContent = agent + ' cancelled';
  } else if (evType === 'iteration_status') {
    statusEl.textContent = agent + ' iter ' + data.iteration + ' | ' + data.total_tools + ' tools';
  } else if (evType === 'exec_approval_request') {
    showApproval('exec', data);
  } else if (evType === 'tool_approval_request') {
    showApproval('tool', data);
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

function showConvList(convs) {
  messagesEl.innerHTML = '<div class="msg system">Conversations:</div>';
  for (const c of convs) {
    const div = document.createElement('div');
    div.className = 'msg system';
    div.style.cursor = 'pointer';
    div.style.textAlign = 'left';
    div.textContent = c.conversation_id.slice(0, 8) + ' — ' + (c.preview || '(empty)').slice(0, 60);
    div.onclick = () => { vscode.postMessage({ type: 'resumeConversation', conversationId: c.conversation_id }); };
    messagesEl.appendChild(div);
  }
}

function replayHistory(data) {
  messagesEl.innerHTML = '';
  for (const m of (data.messages || [])) {
    addMsg(m.type || m.role, m.content || '', m);
  }
  statusEl.textContent = data.message_count + ' messages' + (data.has_more ? ' (more available)' : '');
}

// Auto-resize textarea
inputEl.addEventListener('input', () => {
  inputEl.style.height = '36px';
  inputEl.style.height = Math.min(inputEl.scrollHeight, 120) + 'px';
});
</script>
</body></html>`;
  }
}

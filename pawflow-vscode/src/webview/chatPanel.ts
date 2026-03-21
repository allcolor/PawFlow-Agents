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
          await this.resumeConversation(msg.conversationId, msg.offset);
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

  postRelayStatus(status: string): void {
    this.postMessage({ type: 'relayStatus', status });
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

  private async resumeConversation(cid: string, offset?: number): Promise<void> {
    const api = this.getApi();
    if (!api) { return; }
    try {
      const data = await api.sendAction('load_history', {
        conversation_id: cid, limit: 50, offset: offset || 0,
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
.toolbar button { background: var(--vscode-button-secondaryBackground); color: var(--vscode-button-secondaryForeground); border: none; padding: 3px 8px; border-radius: 3px; cursor: pointer; font-size: 11px; }
.toolbar button:hover { background: var(--vscode-button-secondaryHoverBackground); }
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
</style>
</head>
<body>
<div class="toolbar">
  <button onclick="backToChat()">Chat</button>
  <button onclick="newChat()">+ New</button>
  <button onclick="loadConvs()">Conversations</button>
  <button onclick="sendCmd('compact')">Compact</button>
</div>
<div class="toolbar-row2">
  <button onclick="showPanel('resources')" title="Resources">&#128218; Resources</button>
  <button onclick="showPanel('context')" title="LLM Context">&#128065; Context</button>
  <button onclick="showPanel('files')" title="Files">&#128196; Files</button>
  <button onclick="showPanel('tools')" title="Tools">&#128295; Tools</button>
  <span class="relay-badge"><span class="relay-dot off" id="relayDot"></span> <span id="relayLabel">Relay</span></span>
</div>
<div style="position:relative;flex:1;display:flex;flex-direction:column;overflow:hidden;min-height:0">
  <div class="messages" id="messages">
    <div class="msg system">PawFlow — Type a message to start</div>
  </div>
  <div class="panel-overlay" id="panelOverlay"></div>
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
let currentHistoryConvId = null;
let currentHistoryOffset = 0;
var _hadToolCalls = false;
var _lastToolCall = '';

const FUN_VERBS = ['Refactoring','Compiling','Debugging','Contemplating','Bamboozling',
  'Rickrolling','Skedaddling','Philosophizing','Defenestrating','Hocus-pocusing'];
function randomVerb() { return FUN_VERBS[Math.floor(Math.random() * FUN_VERBS.length)]; }

const AGENT_COLORS = ['#4ecdc4','#4fc3f7','#ab47bc','#f4a261','#e94560','#3fb950','#58a6ff','#d4a373'];
function agentColor(name) {
  let h = 0;
  for (let i = 0; i < name.length; i++) h += name.charCodeAt(i);
  return AGENT_COLORS[h % AGENT_COLORS.length];
}

function send() {
  const text = inputEl.value.trim();
  if (!text) return;

  // Handle slash commands locally
  if (text.startsWith('/')) {
    if (text === '/new') { newChat(); inputEl.value = ''; return; }
    if (text === '/conv') { loadConvs(); inputEl.value = ''; return; }
    if (text === '/compact') { sendCmd('compact'); inputEl.value = ''; return; }
    if (text.startsWith('/model ')) { sendCmd('model', text.slice(7)); inputEl.value = ''; return; }
    if (text.startsWith('/agent ')) { sendCmd('select_agent', text.slice(7)); inputEl.value = ''; return; }
    // Other slash commands: send as message (the server handles /review, /plan, etc.)
  }

  addMsg('user', text);
  vscode.postMessage({ type: 'sendMessage', text });
  inputEl.value = '';
  inputEl.style.height = '36px';
}

function backToChat() { closePanel(); }

function newChat() { closePanel();
  vscode.postMessage({ type: 'newConversation' });
  messagesEl.innerHTML = '<div class="msg system">New conversation</div>';
  currentHistoryConvId = null;
  currentHistoryOffset = 0;
}
function loadConvs() { closePanel(); vscode.postMessage({ type: 'loadConversations' }); }
function sendCmd(cmd, arg) { vscode.postMessage({ type: 'command', command: cmd, arg }); }

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

function renderToolResult(content) {
  // Strip TOOL OUTPUT wrapper
  let text = content.replace(/\\[TOOL OUTPUT[^\\]]*\\]\\n?/g, '').replace(/\\n\\[\\/TOOL OUTPUT\\]/g, '');
  // Detect diffs
  const lines = text.split('\\n');
  const hasDiff = lines.some(function(l) { return l.trimStart().startsWith('+ ') || l.trimStart().startsWith('- '); });
  if (hasDiff && (text.includes('replacement') || text.includes('Edited ') || text.includes('Written '))) {
    return '<pre class="diff">' + lines.map(function(l) {
      const s = l.trimStart();
      if (s.startsWith('+ ') || s.match(/^\\d+\\s+\\+ /)) return '<span class="diff-add">' + esc(l) + '</span>';
      if (s.startsWith('- ') || s.match(/^\\d+\\s+- /)) return '<span class="diff-del">' + esc(l) + '</span>';
      if (s.startsWith('@@')) return '<span class="diff-hunk">' + esc(l) + '</span>';
      return '<span class="diff-ctx">' + esc(l) + '</span>';
    }).join('\\n') + '</pre>';
  }
  return esc(text.slice(0, 300));
}

function addMsg(type, content, meta) {
  const div = document.createElement('div');
  div.className = 'msg ' + type;
  if (type === 'user') {
    div.textContent = content;
  } else if (type === 'assistant') {
    const agent = meta?.agent_name || meta?.source?.name || 'assistant';
    const svc = meta?.source?.llm_service || '';
    const color = agentColor(agent);
    div.innerHTML = '<span class="agent-badge" style="background:' + color + '">'
      + esc(agent) + (svc ? ' via ' + esc(svc) : '') + '</span>' + renderMd(content);
  } else if (type === 'tool_call') {
    div.innerHTML = '&#9889; ' + esc(content);
  } else if (type === 'tool_result') {
    div.innerHTML = '&#10003; ' + renderToolResult(content);
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
      if (msg.action === 'model') statusEl.textContent = 'Model: ' + (msg.data?.model || '?');
      else if (msg.action === 'select_agent') statusEl.textContent = 'Agent: ' + (msg.data?.agent || '?');
      break;
    case 'relayStatus':
      updateRelayStatus(msg.status);
      break;
  }
});

function handleSSE(event) {
  const { event: evType, data } = event;
  const agent = data.agent_name || 'assistant';

  switch (evType) {
    case 'thinking':
    case 'thinking_content':
      statusEl.innerHTML = '<span class="thinking">' + randomVerb() + '...</span>';
      break;

    case 'token':
      streaming[agent] = (streaming[agent] || '') + (data.text || '');
      statusEl.textContent = agent + ' writing... (' + streaming[agent].split(' ').length + 'w)';
      break;

    case 'tool_call':
      _lastToolCall = agent + ' ' + (data.tool || '') + '(' +
        JSON.stringify(data.arguments || {}).slice(0, 100) + ')';
      addMsg('tool_call', _lastToolCall, data);
      _hadToolCalls = true;
      break;

    case 'tool_result':
      addToolResult(data.tool || '', data.result || '');
      break;

    case 'done': {
      const text = data.response || streaming[agent] || '';
      if (text) addMsg('assistant', text, data);
      streaming[agent] = '';
      _hadToolCalls = false;
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
  messagesEl.innerHTML = '<div class="msg system">Conversations:</div>';
  for (const c of convs) {
    const div = document.createElement('div');
    div.className = 'msg system';
    div.style.cursor = 'pointer';
    div.style.textAlign = 'left';
    div.textContent = c.conversation_id.slice(0, 8) + ' \\u2014 ' + (c.preview || '(empty)').slice(0, 60);
    div.onclick = function() { vscode.postMessage({ type: 'resumeConversation', conversationId: c.conversation_id }); };
    messagesEl.appendChild(div);
  }
}

function replayHistory(data) {
  messagesEl.innerHTML = '';
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

  if (rtype === 'agents' || rtype === 'skills' || rtype === 'mcp' || rtype === 'prompts') {
    addItem('Activate', 'activate');
    addItem('Deactivate', 'deactivate');
    addSep();
    addItem('Delete', 'delete_res');
  }
  if (rtype === 'services') {
    addItem('Enable', 'svc_enable');
    addItem('Disable', 'svc_disable');
    addSep();
    addItem('Uninstall', 'svc_uninstall');
  }
  if (rtype === 'task_defs') {
    addItem('Assign to agent...', 'assign_task');
    addSep();
    addItem('Delete', 'del_task');
  }
  if (rtype === 'agents') {
    addSep();
    addItem('Enable agent', 'agent_enable');
    addItem('Disable agent', 'agent_disable');
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
  else if (action === 'assign_task') {
    var agent = prompt('Assign to which agent?', 'assistant');
    if (!agent) return;
    cmd = 'assign_task';
    params = { agent_name: agent, task_name: name, context: 'isolated' };
  }

  if (cmd) {
    vscode.postMessage({ type: 'command', command: cmd, arg: JSON.stringify(params) });
    setTimeout(function() { loadResourcesPanel(); }, 500);
  }
}

function closePanel() {
  document.getElementById('panelOverlay').className = 'panel-overlay';
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

var _pendingPanel = '';

function renderPanelResult(action, data) {
  const overlay = document.getElementById('panelOverlay');
  if (!overlay || overlay.className !== 'panel-overlay visible') return false;

  if (action === 'list_resources' && _pendingPanel === 'resources') {
    var _resData = data;
    let html = '<div class="panel-header"><h4>Resources</h4><button class="panel-close" onclick="closePanel()">\\u2715</button></div>';

    var sectionOrder = ['agents','skills','mcp','prompts','task_defs','flows','services'];
    var sectionLabels = {agents:'Agents',skills:'Skills',mcp:'MCP Servers',prompts:'Prompts',task_defs:'Tasks',flows:'Flows',services:'Services'};

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
      html += '<div class="res-section" onclick="this.classList.toggle(\\'collapsed\\')">'
        + '<span class="res-arrow">\\u25BC</span> <strong>' + esc(label) + '</strong> <span style="color:var(--vscode-descriptionForeground)">(' + items.length + ')</span></div>';
      html += '<div class="res-items">';
      for (var ii = 0; ii < items.length; ii++) {
        var item = items[ii];
        var name = item.name || item.id || item.service_id || '?';
        var active = item.active ? ' <span style="color:#3fb950">\\u2713</span>' : '';
        var enabled = item.enabled === false ? ' <span style="color:#f85149">(disabled)</span>' : '';
        var connected = item.connected ? ' <span style="color:#3fb950">(connected)</span>' : '';
        var desc = item.description || item.prompt || item.type || item.service_type || '';
        if (desc.length > 60) desc = desc.slice(0, 60) + '...';
        var statusBadge = active || enabled || connected;

        html += '<div class="panel-item" oncontextmenu="showResMenu(event,\\'' + esc(rtype) + '\\',\\'' + esc(name).replace(/'/g, "\\\\\\'") + '\\')">'
          + '<span style="font-weight:500">' + esc(name) + '</span>' + statusBadge
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

  return false;
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

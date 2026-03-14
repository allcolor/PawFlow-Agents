"""ServeChatUI Task — Serve a self-contained chat HTML interface.

Returns a complete HTML page with embedded CSS and JavaScript that provides
a chat interface for the agentLoop. The UI handles conversation_id tracking,
message history, file download links, and markdown rendering.

Flow pattern:
    httpReceiver (GET /chat) → serveChatUI → handleHTTPResponse
"""

import logging
from typing import Dict, Any, List

from core import FlowFile, TaskFactory
from core.base_task import BaseTask

logger = logging.getLogger(__name__)

_CHAT_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PyFi2 Agent Chat</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
       background: #1a1a2e; color: #e0e0e0; height: 100vh; display: flex; }
.sidebar { width: 260px; background: #0f1629; border-right: 1px solid #0f3460;
           display: flex; flex-direction: column; height: 100vh; flex-shrink: 0; }
.sidebar-header { padding: 12px 14px; border-bottom: 1px solid #0f3460;
                   display: flex; align-items: center; justify-content: space-between; }
.sidebar-header h2 { font-size: 14px; color: #8888aa; font-weight: 600; }
.sidebar-header .btn-new { background: #e94560; color: white; border: none; border-radius: 6px;
                            padding: 4px 12px; cursor: pointer; font-size: 12px; font-weight: 600; }
.sidebar-header .btn-new:hover { background: #c73a52; }
.conv-list { flex: 1; overflow-y: auto; padding: 6px; }
.conv-item { padding: 10px 12px; border-radius: 8px; cursor: pointer; margin-bottom: 4px;
             border: 1px solid transparent; position: relative; }
.conv-item:hover { background: #16213e; border-color: #0f3460; }
.conv-item.active { background: #16213e; border-color: #e94560; }
.conv-item .conv-preview { font-size: 13px; color: #c0c0d0; white-space: nowrap;
                            overflow: hidden; text-overflow: ellipsis; }
.conv-item .conv-meta { font-size: 11px; color: #6c6c8a; margin-top: 3px; }
.conv-item .conv-delete { position: absolute; right: 8px; top: 8px; background: none;
                           border: none; color: #6c6c8a; cursor: pointer; font-size: 14px;
                           display: none; padding: 2px 4px; }
.conv-item:hover .conv-delete { display: block; }
.conv-item .conv-delete:hover { color: #e94560; }
.conv-status { display: inline-block; width: 8px; height: 8px; border-radius: 50%;
               margin-right: 6px; vertical-align: middle; }
.conv-status.active { background: #4ecdc4; animation: pulse 1.5s ease-in-out infinite; }
.conv-status.blocked { background: #f0a500; }
@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
.sidebar-settings { padding: 8px 14px; border-top: 1px solid #0f3460; }
.sidebar-settings label { font-size: 11px; color: #6c6c8a; display: block; margin-bottom: 3px; }
.sidebar-settings select { width: 100%; background: #1a1a2e; color: #c0c0d0; border: 1px solid #0f3460;
                            border-radius: 4px; padding: 4px 6px; font-size: 12px; cursor: pointer; }
.sidebar-settings select:focus { outline: none; border-color: #e94560; }
.sidebar-toggle { display: none; position: fixed; top: 12px; left: 12px; z-index: 100;
                   background: #0f3460; color: #e0e0e0; border: 1px solid #e94560;
                   border-radius: 6px; padding: 6px 10px; cursor: pointer; font-size: 16px; }
@media (max-width: 700px) {
  .sidebar { position: fixed; left: -270px; z-index: 99; transition: left 0.2s; }
  .sidebar.open { left: 0; }
  .sidebar-toggle { display: block; }
}
.main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.header { background: #16213e; padding: 12px 20px; border-bottom: 1px solid #0f3460;
           display: flex; align-items: center; gap: 12px; }
.header h1 { font-size: 18px; color: #e94560; }
.header .status { font-size: 12px; color: #6c6c8a; }
.header .btn { background: #0f3460; color: #e0e0e0; border: 1px solid #e94560;
                     padding: 6px 14px; border-radius: 6px; cursor: pointer; font-size: 13px; }
.header .btn:hover { background: #e94560; color: white; }
.header .actions { margin-left: auto; display: flex; gap: 8px; align-items: center; }
.header .user-info { font-size: 12px; color: #8888aa; }
.messages-wrap { flex: 1; position: relative; overflow: hidden; display: flex; flex-direction: column; }
.messages { flex: 1; overflow-y: auto; padding: 20px; display: flex; flex-direction: column; gap: 12px; }
.msg { max-width: 80%; padding: 10px 14px; border-radius: 12px; line-height: 1.5; font-size: 14px;
       white-space: pre-wrap; word-wrap: break-word; }
.msg a { color: #4fc3f7; text-decoration: underline; }
.msg code { background: rgba(0,0,0,0.3); padding: 1px 5px; border-radius: 3px; font-size: 13px; }
.msg pre { background: rgba(0,0,0,0.4); padding: 10px; border-radius: 6px; overflow-x: auto;
           margin: 8px 0; }
.msg pre code { background: none; padding: 0; }
.msg.user { align-self: flex-end; background: #0f3460; color: white; border-bottom-right-radius: 4px; }
.source-badge { display: inline-block; font-size: 10px; padding: 1px 6px; border-radius: 8px; margin-right: 4px; vertical-align: middle; font-weight: 600; letter-spacing: 0.3px; }
.msg.assistant { align-self: flex-start; background: #16213e; border: 1px solid #0f3460;
                  border-bottom-left-radius: 4px; }
.msg.error { align-self: center; background: #5c1a1a; color: #ff8a80; font-size: 13px; }
.msg.system { align-self: center; color: #6c6c8a; font-size: 12px; background: none; }
.msg.system-compact { align-self: center; color: #555570; font-size: 11px; background: none; padding: 1px 8px; margin: 1px 0; opacity: 0.8; }
.msg.agent-result { align-self: flex-start; background: #1a1a2e; color: #a0a0c0; font-size: 12px; border-left: 2px solid #6c5ce7; padding: 6px 10px; }
.msg.tool { align-self: flex-start; background: #0f1629; color: #808090; font-size: 12px;
            border-left: 2px solid #0f3460; padding: 4px 10px; max-width: 85%; }
.msg-meta { margin-top: 6px; padding-top: 4px; border-top: 1px solid rgba(255,255,255,0.06);
            font-size: 11px; color: #6c6c8a; cursor: pointer; user-select: none; line-height: 1.6; }
.msg-meta:hover { color: #8888aa; }
.msg-meta .meta-summary::before { content: '\u25B8 '; font-size: 9px; }
.msg-meta.expanded .meta-summary::before { content: '\u25BE '; }
.msg-meta .meta-details { display: none; margin-top: 2px; color: #555570; font-size: 10px; }
.msg-meta.expanded .meta-details { display: block; }
.msg.btw { align-self: flex-start; background: #0d1b2a; color: #c0c0d0; font-size: 13px;
           border-left: 3px solid #60a5fa; border-radius: 8px; padding: 8px 14px;
           max-width: 85%; font-style: italic; opacity: 0.9; }
.msg { position: relative; }
.msg-actions { position: sticky; top: 0; float: right; display: none; gap: 2px; margin: -4px -6px 0 8px;
               z-index: 2; background: inherit; border-radius: 6px; padding: 2px; }
.msg:hover .msg-actions { display: flex; }
.msg-actions button { background: rgba(255,255,255,0.1); border: none; color: #aaa; cursor: pointer;
                      font-size: 12px; padding: 2px 6px; border-radius: 4px; line-height: 1; }
.msg-actions button:hover { background: rgba(255,255,255,0.2); color: #fff; }
.active-panel { display: none; background: #0f1629; border: 1px solid #0f3460; border-radius: 8px;
                margin: 0 20px 8px; padding: 8px 12px; font-size: 12px; color: #a0a0c0; }
.active-panel.visible { display: block; }
.active-panel-title { font-size: 11px; color: #6c6c8a; margin-bottom: 4px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }
.active-row { display: flex; align-items: center; gap: 8px; padding: 4px 0; border-top: 1px solid rgba(255,255,255,0.05); }
.active-row:first-of-type { border-top: none; }
.active-row .a-spinner { animation: spin 1.2s linear infinite; font-style: normal; }
.active-row .a-name { font-weight: 600; color: #e0e0f0; min-width: 80px; }
.active-row .a-msg { flex: 1; color: #6c6c8a; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 200px; }
.active-row .a-status { color: #4ecdc4; min-width: 80px; }
.active-row .a-time { color: #808090; min-width: 35px; text-align: right; }
.active-row .a-actions { display: flex; gap: 4px; }
.active-row .a-actions button { background: none; border: 1px solid #333; color: #aaa; cursor: pointer;
                                 border-radius: 4px; font-size: 11px; padding: 1px 6px; line-height: 1.4; }
.active-row .a-actions button:hover { background: rgba(255,255,255,0.1); color: #fff; }
.active-row .a-actions button.btn-stop { border-color: #993333; color: #ff6b6b; }
.active-row .a-actions button.btn-stop:hover { background: #993333; color: #fff; }
.typing { align-self: flex-start; font-size: 14px; padding: 10px 14px;
         font-style: italic; display: flex; align-items: center; gap: 8px; }
.typing .spinner { display: inline-block; animation: spin 1.2s linear infinite;
                   font-size: 18px; font-style: normal; }
.typing .verb { animation: fadeIn 0.4s ease-in; }
@keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
@keyframes fadeIn { 0% { opacity: 0; transform: translateY(2px); } 100% { opacity: 1; transform: translateY(0); } }
.scroll-nav { position: absolute; right: 20px; bottom: 10px; display: flex; flex-direction: column;
              gap: 6px; z-index: 10; opacity: 0; pointer-events: none; transition: opacity 0.25s ease; }
.scroll-nav.visible { opacity: 1; pointer-events: auto; }
.scroll-nav button { width: 36px; height: 36px; border-radius: 50%; border: 1px solid #0f3460;
                     background: #16213e; color: #a0a0c0; font-size: 16px; cursor: pointer;
                     display: flex; align-items: center; justify-content: center;
                     box-shadow: 0 2px 8px rgba(0,0,0,0.4); transition: background 0.2s, color 0.2s; }
.scroll-nav button:hover { background: #0f3460; color: #fff; }
.input-area { background: #16213e; padding: 14px 20px; border-top: 1px solid #0f3460;
               display: flex; gap: 10px; flex-direction: column; }
.input-row { display: flex; gap: 10px; align-items: flex-end; }
.input-row textarea { flex: 1; background: #1a1a2e; color: #e0e0e0; border: 1px solid #0f3460;
                        border-radius: 8px; padding: 10px; font-size: 14px; resize: none;
                        font-family: inherit; outline: none; min-height: 44px; max-height: 120px; }
.input-row textarea:focus { border-color: #e94560; }
.input-row button { background: #e94560; color: white; border: none; border-radius: 8px;
                      padding: 10px 20px; cursor: pointer; font-size: 14px; font-weight: 600;
                      white-space: nowrap; height: 44px; }
.input-row button:hover { background: #c73a52; }
.input-row button:disabled { background: #3a3a5a; cursor: not-allowed; }
#stopBtn { background: #cc3333; padding: 10px 14px; font-size: 16px; min-width: 44px; }
#stopBtn:hover { background: #aa2222; }
.btn-attach { background: #0f3460 !important; padding: 10px 12px !important; font-size: 18px !important; }
.btn-attach:hover { background: #1a4a8a !important; }
.btn-folder { background: #0f3460 !important; padding: 10px 12px !important; font-size: 18px !important; }
.btn-folder:hover { background: #1a4a8a !important; }
.btn-folder.active { background: #1a5a2a !important; }
.btn-folder.active:hover { background: #2a7a3a !important; }
.files-panel { background: #0f1629; border-bottom: 1px solid #0f3460; padding: 8px 16px;
               max-height: 180px; overflow-y: auto; }
.files-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;
                color: #c0c0d0; font-size: 13px; }
.btn-close-panel { background: none; border: none; color: #c0c0d0; cursor: pointer; font-size: 18px; padding: 0 4px; }
.btn-close-panel:hover { color: #e94560; }
.files-list { display: flex; flex-wrap: wrap; gap: 6px; }
.file-chip { display: inline-flex; align-items: center; gap: 4px; padding: 3px 8px; border-radius: 4px;
             font-size: 12px; background: #1a1a2e; border: 1px solid #0f3460; color: #c0c0d0; }
.file-chip a { color: #4ecdc4; text-decoration: none; }
.file-chip a:hover { text-decoration: underline; }
.file-chip .file-status { display: inline-block; width: 6px; height: 6px; border-radius: 50%; }
.file-chip .file-status.available { background: #4ecdc4; }
.file-chip .file-status.expired { background: #e94560; }
.flow-chip { display: inline-flex; align-items: center; gap: 4px; padding: 3px 8px; border-radius: 4px;
             font-size: 12px; background: #1a1a2e; border: 1px solid #0f3460; color: #c0c0d0; }
.flow-chip .flow-status { display: inline-block; width: 6px; height: 6px; border-radius: 50%; }
.flow-chip .flow-status.running { background: #4ecdc4; }
.flow-chip .flow-status.stopped { background: #e94560; }
.flow-chip .flow-status.scheduled { background: #f9a825; }
.sched-chip { display: inline-flex; align-items: center; gap: 4px; padding: 3px 8px; border-radius: 4px;
              font-size: 12px; background: #1a1a2e; border: 1px solid #0f3460; color: #c0c0d0; }
.sched-chip .sched-icon { color: #f9a825; }
.flow-chip { cursor: context-menu; }
.ctx-menu { position: fixed; z-index: 9999; background: #16213e; border: 1px solid #0f3460;
            border-radius: 6px; padding: 4px 0; min-width: 120px; box-shadow: 0 4px 12px rgba(0,0,0,.5); }
.ctx-menu-item { padding: 6px 14px; font-size: 13px; color: #c0c0d0; cursor: pointer; }
.ctx-menu-item:hover { background: #0f3460; color: #e0e0f0; }
.ctx-menu-item.danger { color: #e94560; }
.ctx-menu-item.danger:hover { background: #e94560; color: #fff; }
.attachments-preview { display: flex; flex-wrap: wrap; gap: 8px; }
.attachments-preview:empty { display: none; }
.att-item { display: flex; align-items: center; gap: 6px; background: #1a1a2e; border: 1px solid #0f3460;
            border-radius: 6px; padding: 4px 8px; font-size: 12px; color: #c0c0d0; }
.att-item img { height: 32px; width: 32px; object-fit: cover; border-radius: 4px; }
.att-item .att-icon { font-size: 16px; }
.att-item .att-remove { background: none; border: none; color: #e94560; cursor: pointer;
                         font-size: 14px; padding: 0 2px; }
.att-item .att-remove:hover { color: #ff6b6b; }
.msg img.chat-image { max-width: 300px; max-height: 200px; border-radius: 8px; margin: 6px 0;
                       cursor: pointer; }
.msg .doc-badge { display: inline-block; background: #0f3460; padding: 2px 8px; border-radius: 4px;
                   font-size: 11px; color: #8888aa; margin: 2px 0; }
/* Exec approval dialog */
.exec-overlay { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.7);
                 z-index: 1000; display: flex; align-items: center; justify-content: center; }
.exec-dialog { background: #1a1a2e; border: 1px solid #333; border-radius: 12px; padding: 24px;
                max-width: 600px; width: 90%; box-shadow: 0 8px 32px rgba(0,0,0,0.5); }
.exec-dialog h3 { margin: 0 0 16px 0; color: #e0e0e0; font-size: 16px; }
.exec-risk { display: inline-block; padding: 2px 10px; border-radius: 12px; font-size: 12px;
              font-weight: 600; margin-left: 8px; }
.exec-risk.low { background: #1b4332; color: #52b788; }
.exec-risk.medium { background: #5a3e00; color: #f4a261; }
.exec-risk.high { background: #5c1a1a; color: #e94560; }
.exec-cmd { background: #0d1117; border: 1px solid #30363d; border-radius: 8px; padding: 12px;
             font-family: 'Consolas', 'Monaco', monospace; font-size: 13px; color: #c9d1d9;
             margin: 12px 0; white-space: pre-wrap; word-break: break-all; }
.exec-cmd textarea { width: 100%; min-height: 60px; background: #0d1117; border: 1px solid #30363d;
                      color: #c9d1d9; font-family: inherit; font-size: inherit; resize: vertical;
                      border-radius: 4px; padding: 8px; }
.exec-cwd { color: #8b949e; font-size: 12px; margin-bottom: 12px; }
.exec-btns { display: flex; gap: 12px; justify-content: flex-end; }
.exec-btns button { padding: 8px 20px; border: none; border-radius: 6px; font-size: 14px;
                     cursor: pointer; font-weight: 600; }
.exec-approve { background: #238636; color: white; }
.exec-approve:hover { background: #2ea043; }
.exec-deny { background: #da3633; color: white; }
.exec-deny:hover { background: #e5534b; }
/* Terminal output in chat */
.terminal-output { background: #0d1117; border: 1px solid #30363d; border-radius: 8px;
                    padding: 12px; margin: 8px 0; font-family: 'Consolas', 'Monaco', monospace;
                    font-size: 12px; max-height: 300px; overflow-y: auto; }
.terminal-output .term-stdout { color: #c9d1d9; white-space: pre-wrap; }
.terminal-output .term-stderr { color: #f85149; white-space: pre-wrap; }
.terminal-output .term-header { color: #8b949e; font-size: 11px; margin-bottom: 4px; }
.terminal-output .term-exit { margin-top: 4px; font-size: 11px; }
.terminal-output .term-exit.ok { color: #3fb950; }
.terminal-output .term-exit.fail { color: #f85149; }
</style>
</head>
<body>
<button class="sidebar-toggle" id="sidebarToggle" onclick="toggleSidebar()">&#9776;</button>
<div class="sidebar" id="sidebar">
  <div class="sidebar-header">
    <h2>Conversations</h2>
    <button class="btn-new" onclick="newChat()">+ New</button>
  </div>
  <div class="conv-list" id="convList"></div>
  <div class="sidebar-settings">
    <label id="ttlLabel">Expiry</label>
    <select id="ttlSelect">
      <option value="0">Unlimited</option>
      <option value="3600">1 hour</option>
      <option value="21600">6 hours</option>
      <option value="86400">24 hours</option>
      <option value="604800">7 days</option>
    </select>
  </div>
  <div class="sidebar-settings" id="resourcesPanel" style="display:none">
    <label style="cursor:pointer;user-select:none;" onclick="toggleResourcesSection()">&#x25BC; Resources</label>
    <div id="resourcesContent" style="margin-top:4px;font-size:12px;color:#8888aa;"></div>
  </div>
</div>
<div class="main">
<div class="header">
  <h1>PyFi2 Agent</h1>
  <span class="status" id="status">Ready</span>
  <div class="actions">
    <span class="user-info" id="userInfo"></span>
    <button class="btn" id="schedsBtn" onclick="toggleSchedsPanel()" style="display:none" title="Scheduled tasks">&#x23F0;</button>
    <button class="btn" id="flowsBtn" onclick="toggleFlowsPanel()" title="My Flows">&#x26A1;</button>
    <button class="btn" id="filesBtn" onclick="toggleFilesPanel()" style="display:none" title="Conversation files">&#x1F4C4;</button>
    <button class="btn" id="contextBtn" onclick="cmdShowContext()" style="display:none" title="View LLM context">&#x1F441;</button>
    <button class="btn" id="refreshConvBtn" onclick="refreshCurrentConv()" style="display:none" title="Refresh conversation">&#x21BB;</button>
    <button class="btn" id="deleteConvBtn" onclick="deleteCurrentConv()" style="display:none" title="Delete conversation">&#x1F5D1;</button>
    <button class="btn" id="logoutBtn" onclick="doLogout()" style="display:none">Logout</button>
  </div>
</div>
<div class="files-panel" id="schedsPanel" style="display:none">
  <div class="files-header"><strong>Scheduled Tasks</strong><button class="btn-close-panel" onclick="toggleSchedsPanel()">&times;</button></div>
  <div class="files-list" id="schedsList"></div>
</div>
<div class="files-panel" id="flowsPanel" style="display:none">
  <div class="files-header"><strong>Flows</strong><button class="btn-close-panel" onclick="toggleFlowsPanel()">&times;</button></div>
  <div class="files-list" id="flowsList"></div>
</div>
<div class="files-panel" id="filesPanel" style="display:none">
  <div class="files-header"><strong>Files</strong><button class="btn-close-panel" onclick="toggleFilesPanel()">&times;</button></div>
  <div class="files-list" id="filesList"></div>
</div>
<div class="messages-wrap">
  <div class="messages" id="messages"></div>
  <div class="scroll-nav" id="scrollNav">
    <button onclick="document.getElementById('messages').scrollTop=0" title="Scroll to top">&#x2191;</button>
    <button onclick="scrollBottom(true)" title="Scroll to bottom">&#x2193;</button>
  </div>
</div>
<div class="active-panel" id="activePanel">
  <div class="active-panel-title">Active agents</div>
  <div id="activeRows"></div>
</div>
<div class="input-area">
  <div class="attachments-preview" id="attachPreview"></div>
  <div class="input-row">
    <button class="btn-attach" id="promptsBtn" onclick="showPrompts()" title="Prompt library" style="font-size:16px !important">&#x1F4DD;</button>
    <button class="btn-attach btn-folder" id="folderBtn" onclick="openLocalFolder()" title="Open local folder">&#x1F4C1;</button>
    <button class="btn-attach" onclick="document.getElementById('fileInput').click()" title="Attach files">&#x1F4CE;</button>
    <input type="file" id="fileInput" multiple accept=".pdf,.txt,.html,.md,.csv,.json,.png,.jpg,.jpeg,.gif,.webp,.py" style="display:none" onchange="handleFiles(this.files)">
    <textarea id="input" placeholder="Type a message... (Enter to send, Shift+Enter for newline)"
              rows="1" onkeydown="handleKey(event)"></textarea>
    <button id="sendBtn" onclick="send()">Send</button>
    <button id="stopBtn" onclick="cancelAgent()" style="display:none" title="Stop generation">&#9632;</button>
  </div>
</div>
</div>
<script>
// ── i18n ──
const _i18n = {
  en: {
    ready: 'Ready', sending: 'Sending...', streaming: 'Streaming...',
    thinking: 'Thinking', thinkingRound: 'Thinking (round {round})',
    thinkingWait: 'Thinking ({sec}s)', error: 'Error',
    loading: 'Loading...', reconnecting: 'Reconnecting...',
    continuing: 'Continuing research...', usingTool: 'Using {tool}...',
    callingTool: '\u{1F527} Calling tool: {tool}',
    toolResult: '\u{2705} {tool}: {result}',
    newConv: 'New conversation started.',
    welcome: 'Welcome! Type a message to start chatting.',
    connError: 'Connection error: {msg}',
    loadError: 'Failed to load conversation',
    sessionExpired: 'Session expired. Please log in again.',
    unknownError: 'Unknown error',
    fileTooLarge: 'File too large: {name} ({size}MB, max 10MB)',
    send: 'Send', newChat: '+ New', logout: 'Logout', deleteConv: 'Delete conversation',
    placeholder: 'Type a message... (Enter to send, Shift+Enter for newline)',
    attachTitle: 'Attach files', conversations: 'Conversations',
    folderOpen: 'Open local folder', folderActive: 'Local folder: {name}',
    folderUnsupported: 'Your browser does not support the File System Access API (use Chrome or Edge)',
    ttlLabel: 'Expiry', fileTtlLabel: 'Files', ttlNone: 'Unlimited', ttl1h: '1 hour', ttl6h: '6 hours',
    ttl24h: '24 hours', ttl7d: '7 days', emptyResponse: '(Agent finished without producing a response)',
    secretAdded: 'Secret "{name}" stored securely. Use ${secrets.{ref}} in flows, or get_secret("{short}") in scripts.',
    secretAddUsage: 'Usage: /add-secret &lt;name&gt; &lt;value&gt;',
    secretListEmpty: 'No secrets stored.',
    secretListTitle: 'Your secrets:',
    variableAdded: 'Variable "{name}" stored. Use ${var.{ref}} in flows, or get_variable("{short}") in scripts.',
    variableAddUsage: 'Usage: /add-variable &lt;name&gt; &lt;value&gt;',
    variableListEmpty: 'No variables stored.',
    variableListTitle: 'Your variables:',
    cancelled: '[Cancelled]', stop: 'Stop', cancelling: 'Cancelling...',
    noConv: 'No active conversation.', restartFrom: 'Context restarted — keeping last {n} messages.',
    resuming: 'Summarizing conversation to ~{n} tokens...', resumed: 'Conversation summarized ({n} messages \u2192 {len} chars). Next message starts from the summary.',
    rebuilding: 'Rebuilding context from full conversation...', rebuilt: 'Context rebuilt: {action} ({before} \u2192 {after} messages, ~{tokens} tokens)',
    contextTitle: 'LLM Context', contextDiverged: 'diverged', contextSynced: 'synced',
    contextTokens: '~{n} tokens', contextMessages: '{n} messages', noContext: 'No context available.',
    contextEdit: 'Edit', contextDelete: 'Delete', contextAdd: 'Add message',
    contextReplaceAll: 'Replace all (JSON)', contextSave: 'Save', contextCancel: 'Cancel',
    contextDeleteConfirm: 'Delete this message?', contextReplaceConfirm: 'Replace entire context?',
    contextSaved: 'Context saved ({n} messages, ~{tokens} tokens)', contextInvalidJson: 'Invalid JSON',
    contextRole: 'Role', contextContent: 'Content',
    thoughtEnabled: 'Random thought enabled for {agent}: {freq} (next in ~{delay}s)',
    thoughtDisabled: 'Random thought disabled for {agent}.',
    thoughtStatus: 'Random thought for {agent}: enabled — {freq}, next in ~{delay}s',
    thoughtStatusOff: 'Random thought for {agent}: disabled',
    thoughtTriggered: 'Random thought triggered for {agent}.',
    thoughtNoConv: 'No active conversation.',
    thoughtScheduled: '[{agent}] next thought in ~{delay}s',
    thoughtFiring: '[{agent}] thinking...',
    iterStatus: '[{agent}] iter {i} \u00b7 round {r}/{mr} \u00b7 {t} tools',
    subAgentStarted: 'Sub-agent [{agent}] started',
    subAgentDone: '[{agent}] finished ({dur}s, {tok} tokens)',
    iterProgress: '\u21bb [{agent}] iter {i} \u00b7 round {r}/{mr} \u00b7 {t} tools',
  },
  fr: {
    ready: 'Pr\u00eat', sending: 'Envoi...', streaming: 'R\u00e9ception...',
    thinking: 'R\u00e9flexion', thinkingRound: 'R\u00e9flexion (tour {round})',
    thinkingWait: 'R\u00e9flexion ({sec}s)', error: 'Erreur',
    loading: 'Chargement...', reconnecting: 'Reconnexion...',
    continuing: 'Recherche en cours...', usingTool: 'Utilisation de {tool}...',
    callingTool: '\u{1F527} Appel outil : {tool}',
    toolResult: '\u{2705} {tool}\u00a0: {result}',
    newConv: 'Nouvelle conversation.',
    welcome: 'Bienvenue\u00a0! \u00c9crivez un message pour commencer.',
    connError: 'Erreur de connexion\u00a0: {msg}',
    loadError: '\u00c9chec du chargement de la conversation',
    sessionExpired: 'Session expir\u00e9e. Veuillez vous reconnecter.',
    unknownError: 'Erreur inconnue',
    fileTooLarge: 'Fichier trop volumineux\u00a0: {name} ({size}\u00a0Mo, max 10\u00a0Mo)',
    send: 'Envoyer', newChat: '+ Nouveau', logout: 'D\u00e9connexion', deleteConv: 'Supprimer la conversation',
    placeholder: '\u00c9crivez un message... (Entr\u00e9e pour envoyer, Maj+Entr\u00e9e pour retour \u00e0 la ligne)',
    attachTitle: 'Joindre des fichiers', conversations: 'Conversations',
    folderOpen: 'Ouvrir un dossier local', folderActive: 'Dossier local\u00a0: {name}',
    folderUnsupported: 'Votre navigateur ne supporte pas le File System Access API (utilisez Chrome ou Edge)',
    ttlLabel: 'Expiration', fileTtlLabel: 'Fichiers', ttlNone: 'Illimit\u00e9', ttl1h: '1 heure', ttl6h: '6 heures',
    ttl24h: '24 heures', ttl7d: '7 jours', emptyResponse: '(L\'agent a termin\u00e9 sans produire de r\u00e9ponse)',
    secretAdded: 'Secret "{name}" stock\u00e9 de mani\u00e8re s\u00e9curis\u00e9e. Utilisez ${secrets.{ref}} dans les flux, ou get_secret("{short}") dans les scripts.',
    secretAddUsage: 'Usage: /add-secret &lt;nom&gt; &lt;valeur&gt;',
    secretListEmpty: 'Aucun secret stock\u00e9.',
    secretListTitle: 'Vos secrets :',
    variableAdded: 'Variable "{name}" stock\u00e9e. Utilisez ${var.{ref}} dans les flux, ou get_variable("{short}") dans les scripts.',
    variableAddUsage: 'Usage: /add-variable &lt;nom&gt; &lt;valeur&gt;',
    variableListEmpty: 'Aucune variable stock\u00e9e.',
    variableListTitle: 'Vos variables :',
    cancelled: '[Annul\u00e9]', stop: 'Stop', cancelling: 'Annulation...',
    noConv: 'Aucune conversation active.', restartFrom: 'Contexte red\u00e9marr\u00e9 \u2014 {n} derniers messages conserv\u00e9s.',
    resuming: 'R\u00e9sum\u00e9 de la conversation en ~{n} tokens...', resumed: 'Conversation r\u00e9sum\u00e9e ({n} messages \u2192 {len} caract\u00e8res). Le prochain message repart du r\u00e9sum\u00e9.',
    rebuilding: 'Reconstruction du contexte depuis la conversation compl\u00e8te...', rebuilt: 'Contexte reconstruit\u00a0: {action} ({before} \u2192 {after} messages, ~{tokens} tokens)',
    contextTitle: 'Contexte LLM', contextDiverged: 'diverg\u00e9', contextSynced: 'synchronis\u00e9',
    contextTokens: '~{n} tokens', contextMessages: '{n} messages', noContext: 'Aucun contexte disponible.',
    contextEdit: 'Modifier', contextDelete: 'Supprimer', contextAdd: 'Ajouter un message',
    contextReplaceAll: 'Remplacer tout (JSON)', contextSave: 'Enregistrer', contextCancel: 'Annuler',
    contextDeleteConfirm: 'Supprimer ce message ?', contextReplaceConfirm: 'Remplacer tout le contexte ?',
    contextSaved: 'Contexte sauvegardé ({n} messages, ~{tokens} tokens)', contextInvalidJson: 'JSON invalide',
    contextRole: 'Rôle', contextContent: 'Contenu',
    thoughtEnabled: 'Pens\u00e9e al\u00e9atoire activ\u00e9e pour {agent}\u00a0: {freq} (prochaine dans ~{delay}s)',
    thoughtDisabled: 'Pens\u00e9e al\u00e9atoire d\u00e9sactiv\u00e9e pour {agent}.',
    thoughtStatus: 'Pens\u00e9e al\u00e9atoire pour {agent}\u00a0: activ\u00e9e \u2014 {freq}, prochaine dans ~{delay}s',
    thoughtStatusOff: 'Pens\u00e9e al\u00e9atoire pour {agent}\u00a0: d\u00e9sactiv\u00e9e',
    thoughtTriggered: 'Pensée aléatoire déclenchée pour {agent}.',
    thoughtNoConv: 'Aucune conversation active.',
    thoughtScheduled: '[{agent}] prochaine pensée dans ~{delay}s',
    thoughtFiring: '[{agent}] réfléchit...',
    iterStatus: '[{agent}] iter {i} \u00b7 tour {r}/{mr} \u00b7 {t} outils',
    subAgentStarted: 'Sous-agent [{agent}] d\u00e9marr\u00e9',
    subAgentDone: '[{agent}] termin\u00e9 ({dur}s, {tok} tokens)',
    iterProgress: '\u21bb [{agent}] iter {i} \u00b7 tour {r}/{mr} \u00b7 {t} outils',
  },
  es: {
    ready: 'Listo', sending: 'Enviando...', streaming: 'Recibiendo...',
    thinking: 'Pensando', thinkingRound: 'Pensando (ronda {round})',
    thinkingWait: 'Pensando ({sec}s)', error: 'Error',
    loading: 'Cargando...', reconnecting: 'Reconectando...',
    continuing: 'Continuando investigaci\u00f3n...', usingTool: 'Usando {tool}...',
    callingTool: '\u{1F527} Llamando herramienta: {tool}',
    toolResult: '\u{2705} {tool}: {result}',
    newConv: 'Nueva conversaci\u00f3n iniciada.',
    welcome: '\u00a1Bienvenido! Escribe un mensaje para comenzar.',
    connError: 'Error de conexi\u00f3n: {msg}',
    loadError: 'Error al cargar la conversaci\u00f3n',
    sessionExpired: 'Sesi\u00f3n expirada. Inicia sesi\u00f3n de nuevo.',
    unknownError: 'Error desconocido',
    fileTooLarge: 'Archivo muy grande: {name} ({size}MB, m\u00e1x 10MB)',
    send: 'Enviar', newChat: '+ Nuevo', logout: 'Cerrar sesi\u00f3n', deleteConv: 'Eliminar conversaci\u00f3n',
    placeholder: 'Escribe un mensaje... (Enter para enviar, Shift+Enter para nueva l\u00ednea)',
    attachTitle: 'Adjuntar archivos', conversations: 'Conversaciones',
    folderOpen: 'Abrir carpeta local', folderActive: 'Carpeta local: {name}',
    folderUnsupported: 'Su navegador no soporta la File System Access API (use Chrome o Edge)',
    ttlLabel: 'Expiraci\u00f3n', fileTtlLabel: 'Archivos', ttlNone: 'Ilimitado', ttl1h: '1 hora', ttl6h: '6 horas',
    ttl24h: '24 horas', ttl7d: '7 d\u00edas', emptyResponse: '(El agente termin\u00f3 sin producir una respuesta)',
    secretAdded: 'Secreto "{name}" almacenado de forma segura. Use ${secrets.{ref}} en flujos, o get_secret("{short}") en scripts.',
    secretAddUsage: 'Uso: /add-secret &lt;nombre&gt; &lt;valor&gt;',
    secretListEmpty: 'No hay secretos almacenados.',
    secretListTitle: 'Sus secretos:',
    variableAdded: 'Variable "{name}" almacenada. Use ${var.{ref}} en flujos, o get_variable("{short}") en scripts.',
    variableAddUsage: 'Uso: /add-variable &lt;nombre&gt; &lt;valor&gt;',
    variableListEmpty: 'No hay variables almacenadas.',
    variableListTitle: 'Sus variables:',
    cancelled: '[Cancelado]', stop: 'Detener', cancelling: 'Cancelando...',
    noConv: 'No hay conversaci\u00f3n activa.', restartFrom: 'Contexto reiniciado \u2014 {n} \u00faltimos mensajes conservados.',
    resuming: 'Resumiendo conversaci\u00f3n a ~{n} tokens...', resumed: 'Conversaci\u00f3n resumida ({n} mensajes \u2192 {len} caracteres). El pr\u00f3ximo mensaje parte del resumen.',
    rebuilding: 'Reconstruyendo contexto desde la conversaci\u00f3n completa...', rebuilt: 'Contexto reconstruido: {action} ({before} \u2192 {after} mensajes, ~{tokens} tokens)',
    contextTitle: 'Contexto LLM', contextDiverged: 'divergido', contextSynced: 'sincronizado',
    contextTokens: '~{n} tokens', contextMessages: '{n} mensajes', noContext: 'Sin contexto disponible.',
    contextEdit: 'Editar', contextDelete: 'Eliminar', contextAdd: 'Añadir mensaje',
    contextReplaceAll: 'Reemplazar todo (JSON)', contextSave: 'Guardar', contextCancel: 'Cancelar',
    contextDeleteConfirm: '¿Eliminar este mensaje?', contextReplaceConfirm: '¿Reemplazar todo el contexto?',
    contextSaved: 'Contexto guardado ({n} mensajes, ~{tokens} tokens)', contextInvalidJson: 'JSON inválido',
    contextRole: 'Rol', contextContent: 'Contenido',
    thoughtEnabled: 'Pensamiento aleatorio activado para {agent}: {freq} (pr\u00f3ximo en ~{delay}s)',
    thoughtDisabled: 'Pensamiento aleatorio desactivado para {agent}.',
    thoughtStatus: 'Pensamiento aleatorio para {agent}: activado \u2014 {freq}, pr\u00f3ximo en ~{delay}s',
    thoughtStatusOff: 'Pensamiento aleatorio para {agent}: desactivado',
    thoughtTriggered: 'Pensamiento aleatorio activado para {agent}.',
    thoughtNoConv: 'No hay conversación activa.',
    thoughtScheduled: '[{agent}] próximo pensamiento en ~{delay}s',
    thoughtFiring: '[{agent}] pensando...',
    iterStatus: '[{agent}] iter {i} \u00b7 ronda {r}/{mr} \u00b7 {t} herram.',
    subAgentStarted: 'Sub-agente [{agent}] iniciado',
    subAgentDone: '[{agent}] terminado ({dur}s, {tok} tokens)',
    iterProgress: '\u21bb [{agent}] iter {i} \u00b7 ronda {r}/{mr} \u00b7 {t} herram.',
  },
};
const _lang = (navigator.language || 'en').slice(0, 2);
const _t = _i18n[_lang] || _i18n.en;
function t(key, vars) {
  let s = _t[key] || _i18n.en[key] || key;
  if (vars) Object.keys(vars).forEach(k => { s = s.replace('{' + k + '}', vars[k]); });
  return s;
}

// Apply i18n to static HTML elements
document.getElementById('status').textContent = t('ready');
document.getElementById('sendBtn').textContent = t('send');
document.getElementById('logoutBtn').textContent = t('logout');
document.getElementById('deleteConvBtn').title = t('deleteConv');
document.getElementById('input').placeholder = t('placeholder');
document.querySelector('.btn-attach').title = t('attachTitle');
document.getElementById('folderBtn').title = t('folderOpen');
document.querySelector('.sidebar-header h2').textContent = t('conversations');
document.querySelector('.btn-new').textContent = t('newChat');
// TTL selector i18n
document.getElementById('ttlLabel').textContent = t('ttlLabel');
const ttlOpts = document.getElementById('ttlSelect').options;
ttlOpts[0].textContent = t('ttlNone');
ttlOpts[1].textContent = t('ttl1h');
ttlOpts[2].textContent = t('ttl6h');
ttlOpts[3].textContent = t('ttl24h');
ttlOpts[4].textContent = t('ttl7d');

const API = window.location.origin + '{{AGENT_PATH}}';
const SSE_URL = window.location.origin + '{{SSE_PATH}}';
const LOGIN_URL = '{{LOGIN_URL}}';
let conversationId = null;
let sending = false;
let eventSource = null;
let pendingAgent = null;  // agent to select when first message creates a conversation
let sseRetryCount = 0;     // for exponential backoff on reconnect
let sseReconnectTimer = null;
let streamingEl = null;  // current assistant message being streamed
let streamingText = '';
let streamingChunks = [];  // all intermediate streaming bubbles (removed on done)
let pendingFiles = [];  // [{file, dataUrl, base64, mime_type, filename}]
let lastSSEActivity = 0;  // timestamp of last SSE event received
let serverMsgCount = 0;    // last known message_count from server (for poll delta)
let pollTimer = null;      // 30s fallback poll interval

// ── Message history (arrow key navigation) ──
let messageHistory = JSON.parse(localStorage.getItem('pyfi2_msg_history') || '[]');
let historyIndex = -1;    // -1 = not navigating, 0 = most recent
let savedDraft = '';      // text being typed before navigating

// ── Watchdog: if sending and no SSE activity for 15s, try recovery ──
setInterval(() => {
  if (!sending || !conversationId) return;
  const now = Date.now();
  if (lastSSEActivity > 0 && (now - lastSSEActivity) > 15000) {
    console.log('[watchdog] no SSE activity for 15s while sending — recovering');
    lastSSEActivity = now;  // reset to avoid re-triggering immediately
    _recoverConversation(conversationId);
  }
}, 5000);

// ── Keep-alive: ping every 4 min to renew sliding session ──
// Note: cookie is HttpOnly so getToken() returns null — use conversationId as auth indicator
setInterval(() => {
  fetch(API, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action: 'ping' }),
    credentials: 'same-origin',
  }).catch(() => {});
}, 4 * 60 * 1000);

// Auth
function getToken() {
  const m = document.cookie.match(/(?:^|;\s*)pyfi2_token=([^;]+)/);
  return m ? m[1] : null;
}
function getAuthHeaders() {
  const token = getToken();
  const h = { 'Content-Type': 'application/json' };
  if (token) h['Authorization'] = 'Bearer ' + token;
  return h;
}
// Page is behind validateSessionAuth, so if we're here, we're logged in
if (LOGIN_URL) {
  document.getElementById('logoutBtn').style.display = '';
}
function doLogout() {
  if (eventSource) { eventSource.close(); eventSource = null; }
  fetch(window.location.origin + '/auth/logout', { method: 'POST', credentials: 'same-origin' })
    .finally(() => { window.location.href = LOGIN_URL || '/auth/login'; });
}

function toggleSidebar() {
  document.getElementById('sidebar').classList.toggle('open');
}

function newChat() {
  if (eventSource) { eventSource.close(); eventSource = null; }
  stopPollTimer();
  conversationId = null;
  pendingAgent = null;
  serverMsgCount = 0;
  streamingEl = null;
  streamingText = '';
  streamingChunks = [];
  sending = false;
  document.getElementById('sendBtn').disabled = false;
  document.getElementById('messages').innerHTML = '';
  addMsg('system', t('newConv'));
  document.getElementById('status').textContent = t('ready');
  document.getElementById('deleteConvBtn').style.display = 'none';
  document.getElementById('contextBtn').style.display = 'none';
  document.getElementById('filesBtn').style.display = 'none';
  document.getElementById('filesPanel').style.display = 'none';
  document.getElementById('flowsPanel').style.display = 'none';
  document.getElementById('schedsBtn').style.display = 'none';
  document.getElementById('schedsPanel').style.display = 'none';
  highlightConv(null);
  // Close sidebar on mobile
  document.getElementById('sidebar').classList.remove('open');
}

function updateDeleteBtn() {
  const show = conversationId ? '' : 'none';
  document.getElementById('deleteConvBtn').style.display = show;
  document.getElementById('contextBtn').style.display = show;
  document.getElementById('refreshConvBtn').style.display = show;
  document.getElementById('filesBtn').style.display = show;
  document.getElementById('schedsBtn').style.display = show;
}

// Conversation sidebar
async function loadConversations() {
  try {
    const resp = await fetch(API, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'list_conversations' }),
      credentials: 'same-origin',
    });
    if (resp.status === 401 || resp.status === 403) return;
    if (!resp.ok) return;
    const data = await resp.json();
    renderConvList(data.conversations || []);
  } catch (e) { /* silent */ }
}

function renderConvList(convs) {
  const list = document.getElementById('convList');
  list.innerHTML = '';
  if (convs.length === 0) {
    list.innerHTML = '<div style="padding:20px;text-align:center;color:#6c6c8a;font-size:13px;">No conversations yet</div>';
    return;
  }
  for (const c of convs) {
    const el = document.createElement('div');
    el.className = 'conv-item' + (c.conversation_id === conversationId ? ' active' : '');
    el.dataset.cid = c.conversation_id;
    const preview = c.preview || 'Empty conversation';
    const date = new Date(c.updated_at * 1000);
    const timeStr = date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
    const statusDot = c.status === 'active' ? '<span class="conv-status active" title="Working"></span>'
      : c.status === 'blocked' ? '<span class="conv-status blocked" title="Blocked"></span>' : '';
    el.innerHTML = '<div class="conv-preview">' + statusDot + escapeHtml(preview) + '</div>'
      + '<div class="conv-meta">' + c.message_count + ' messages \u00b7 ' + timeStr + '</div>'
      + '<button class="conv-delete" title="Delete" onclick="deleteConv(event,\'' + c.conversation_id + '\')">\u00d7</button>';
    el.onclick = () => resumeConv(c.conversation_id);
    list.appendChild(el);
  }
}

function escapeHtml(s) {
  const d = document.createElement('div'); d.textContent = s; return d.innerHTML;
}

function highlightConv(cid) {
  document.querySelectorAll('.conv-item').forEach(el => {
    el.classList.toggle('active', el.dataset.cid === cid);
  });
}

async function resumeConv(cid) {
  if (cid === conversationId) return;  // already viewing this one
  document.getElementById('status').textContent = t('loading');
  try {
    const resp = await fetch(API, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'load_history', conversation_id: cid }),
      credentials: 'same-origin',
    });
    if (!resp.ok) {
      addMsg('error', t('loadError'));
      return;
    }
    const data = await resp.json();
    if (data.error) {
      addMsg('error', data.error);
      return;
    }
    // Switch to this conversation (previous agent thread keeps running server-side)
    if (eventSource) { eventSource.close(); eventSource = null; }
    conversationId = cid;
    streamingEl = null;
    streamingText = '';
    streamingChunks = [];
    sending = false;
    document.getElementById('sendBtn').disabled = false;
    document.getElementById('messages').innerHTML = '';
    // Replay messages (using classified types: user/assistant/tool_call/tool_result)
    for (const m of (data.messages || [])) {
      addMsg(m.type || m.role, m.content, m);
    }
    serverMsgCount = data.message_count || 0;
    highlightConv(cid);
    connectSSE(cid);  // subscribe to SSE — will pick up events if agent is still running
    startPollTimer();
    updateDeleteBtn();
    loadResources();
    document.getElementById('status').textContent = t('ready');
    document.getElementById('sidebar').classList.remove('open');
    scrollBottom(true);  // Auto-scroll to bottom when loading conversation
  } catch (e) {
    addMsg('error', t('connError', {msg: e.message}));
    document.getElementById('status').textContent = t('error');
  }
}

async function _recoverConversation(cid) {
  // After SSE reconnect or poll, check for new messages via efficient poll action.
  try {
    if (cid !== conversationId) return;  // conversation changed during recovery
    const resp = await fetch(API, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify({
        action: 'poll',
        conversation_id: cid,
        last_count: serverMsgCount,
      }),
      credentials: 'same-origin',
    });
    if (!resp.ok) return;
    const data = await resp.json();
    const newMsgs = data.new_messages || [];
    if (newMsgs.length === 0) return;

    console.log('[poll] recovering', newMsgs.length, 'new messages');
    serverMsgCount = data.message_count || serverMsgCount;

    // Clean up any stale streaming state
    for (const chunk of streamingChunks) {
      if (chunk && chunk.parentNode) chunk.remove();
    }
    streamingEl = null;
    streamingText = '';
    streamingChunks = [];
    hideTyping();

    // Display the new messages, skipping messages already shown locally
    const msgContainer = document.getElementById('messages');
    for (const m of newMsgs) {
      const mType = m.type || m.role;
      if (mType === 'user') {
        // Check if this user message is already displayed (sent locally by send())
        const existing = msgContainer.querySelectorAll('.msg.user');
        const lastUserEl = existing.length > 0 ? existing[existing.length - 1] : null;
        if (lastUserEl && lastUserEl.textContent.trim() === (m.content || '').trim()) {
          console.log('[poll] skipping duplicate user message');
          continue;
        }
      }
      if (mType === 'assistant') {
        // Check if this assistant message was already shown via SSE done event
        const existing = msgContainer.querySelectorAll('.msg.assistant');
        const lastEl = existing.length > 0 ? existing[existing.length - 1] : null;
        if (lastEl && lastEl.dataset.rawText) {
          const newText = (m.content || '').substring(0, 500);
          if (lastEl.dataset.rawText === newText) {
            console.log('[poll] skipping duplicate assistant message');
            continue;
          }
        }
      }
      addMsg(mType, m.content, m);
    }

    // Check if agent is still working
    const last = newMsgs[newMsgs.length - 1];
    const lastType = last ? (last.type || last.role) : '';
    if (lastType === 'user' || lastType === 'tool_call' || lastType === 'tool_result') {
      showTyping();
      document.getElementById('status').textContent = t('thinking');
    } else {
      sending = false;
      document.getElementById('sendBtn').disabled = false;
      document.getElementById('status').textContent = t('ready');
    }
    scrollBottom();
  } catch (e) {
    console.warn('[poll] recovery failed:', e);
  }
}

async function deleteConv(event, cid) {
  event.stopPropagation();
  try {
    const resp = await fetch(API, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'delete_conversation', conversation_id: cid }),
      credentials: 'same-origin',
    });
    if (!resp.ok) { console.error('Delete failed:', resp.status); return; }
    if (cid === conversationId) newChat();
    loadConversations();
  } catch (e) { console.error('Delete error:', e); }
}

async function deleteCurrentConv() {
  if (!conversationId) return;
  const cid = conversationId;
  try {
    const resp = await fetch(API, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'delete_conversation', conversation_id: cid }),
      credentials: 'same-origin',
    });
    if (!resp.ok) { console.error('Delete failed:', resp.status); return; }
    newChat();
    loadConversations();
  } catch (e) { console.error('Delete error:', e); }
}

async function refreshCurrentConv() {
  if (!conversationId) return;
  const cid = conversationId;
  document.getElementById('status').textContent = t('loading');
  try {
    const resp = await fetch(API, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'load_history', conversation_id: cid }),
      credentials: 'same-origin',
    });
    if (!resp.ok) { document.getElementById('status').textContent = t('error'); return; }
    const data = await resp.json();
    if (data.error) { document.getElementById('status').textContent = t('error'); return; }
    // Clear and replay
    document.getElementById('messages').innerHTML = '';
    streamingEl = null;
    streamingText = '';
    streamingChunks = [];
    for (const m of (data.messages || [])) {
      addMsg(m.type || m.role, m.content, m);
    }
    serverMsgCount = data.message_count || 0;
    scrollBottom();
    // Check if agent is still working (last msg is not assistant → still processing)
    const msgs = data.messages || [];
    const lastRole = msgs.length > 0 ? (msgs[msgs.length - 1].type || msgs[msgs.length - 1].role) : '';
    if (lastRole !== 'assistant' && lastRole !== 'user') {
      sending = true;
      showTyping();
      document.getElementById('status').textContent = t('thinking');
    } else {
      sending = false;
      document.getElementById('sendBtn').disabled = false;
      document.getElementById('status').textContent = t('ready');
    }
    loadConversations();
  } catch (e) {
    document.getElementById('status').textContent = t('error');
  }
}

function sourceBadge(source) {
  if (!source) return '';
  const name = source.name || '';
  const svc = source.llm_service || '';
  if (source.type === 'agent') {
    // Hash name to color
    let h = 0;
    for (let i = 0; i < name.length; i++) h = ((h << 5) - h + name.charCodeAt(i)) | 0;
    const hue = Math.abs(h) % 360;
    const label = svc ? name + ' via ' + svc : name;
    return '<span class="source-badge" style="background:hsl(' + hue + ',60%,25%);color:hsl(' + hue + ',80%,80%)">' + escapeHtml(label) + '</span> ';
  }
  if (source.type === 'user' && name && name !== 'anonymous') {
    return '<span class="source-badge" style="background:#1a3a2a;color:#4ecdc4">' + escapeHtml(name) + '</span> ';
  }
  return '';
}

function buildMetaLine(extra) {
  if (!extra) return '';
  // Collect metadata parts: model, provider, base_url, tokens, duration
  // Also check source object for provider/base_url (from persisted messages)
  const src = extra.source || {};
  const model = extra.model || src.model || '';
  const provider = extra.provider || src.provider || '';
  const baseUrl = extra.base_url || src.base_url || '';
  const tokIn = extra.tokens_in || 0;
  const tokOut = extra.tokens_out || 0;
  const dur = extra.duration_ms || 0;
  const parts = [];
  if (model) parts.push(model);
  if (provider) parts.push(provider);
  if (!model && !provider) return '';  // nothing interesting to show
  // Compact line
  let line = '<span class="meta-summary">' + parts.join(' \u00b7 ') + '</span>';
  // Build expandable details
  const details = [];
  if (baseUrl) details.push('endpoint: ' + escapeHtml(baseUrl));
  if (tokIn || tokOut) details.push('tokens: ' + tokIn + ' in / ' + tokOut + ' out (' + (tokIn + tokOut) + ' total)');
  if (dur) details.push('duration: ' + (dur / 1000).toFixed(1) + 's');
  if (details.length) {
    line += '<span class="meta-details">' + details.join(' \u00b7 ') + '</span>';
  }
  return '<div class="msg-meta" onclick="this.classList.toggle(\'expanded\')">' + line + '</div>';
}

function addMsg(role, text, extra) {
  const el = document.createElement('div');
  // Support classified types: tool_call, tool_result map to CSS class "tool"
  const cssClass = (role === 'tool_call' || role === 'tool_result') ? 'tool' : role;
  el.className = 'msg ' + cssClass;
  el.dataset.rawText = (text || '').substring(0, 500);  // for dedup comparison
  if (extra && extra.raw_index !== undefined) el.dataset.rawIndex = extra.raw_index;
  const badge = (extra && extra.source) ? sourceBadge(extra.source) : '';

  // Action buttons (copy + delete) for user/assistant messages
  let actionsHtml = '';
  if (role === 'user' || role === 'assistant') {
    actionsHtml = '<span class="msg-actions">'
      + '<button onclick="copyMsg(this)" title="Copy">\u{1F4CB}</button>'
      + '<button onclick="deleteMsg(this)" title="Delete">\u{1F5D1}</button>'
      + '</span>';
  }

  if (role === 'assistant') {
    el.innerHTML = actionsHtml + badge + renderMarkdown(text) + buildMetaLine(extra);
  } else if (role === 'tool' || role === 'tool_call') {
    el.innerHTML = '<span style="color:#e94560;font-size:12px">' + escapeHtml(text) + '</span>';
  } else if (role === 'tool_result') {
    const toolId = (extra && extra.tool_call_id) ? extra.tool_call_id : '';
    el.innerHTML = '<span style="color:#4ecdc4;font-size:11px">↳ ' + escapeHtml(text) + '</span>';
  } else if (role === 'user') {
    el.innerHTML = actionsHtml + badge + escapeHtml(text);
  } else if (role === 'agent-result') {
    const agentName = (extra && typeof extra === 'string') ? extra : '';
    el.innerHTML = (agentName ? '<strong>' + escapeHtml(agentName) + ':</strong> ' : '') + renderMarkdown(text);
  } else {
    el.textContent = text;
  }
  document.getElementById('messages').appendChild(el);
  scrollBottom();
  return el;
}

function escapeHtml(t) {
  const d = document.createElement('div');
  d.textContent = t;
  return d.innerHTML;
}

function renderMarkdown(text) {
  // Detect __show_file__ markers from show_file tool
  try {
    if (text.includes('__show_file__')) {
      const parsed = JSON.parse(text);
      if (parsed && parsed.__show_file__) {
        setTimeout(() => openFileViewer(parsed.url), 100);
        return `<span style="cursor:pointer;color:#6c5ce7;" onclick="openFileViewer('${parsed.url}')">\uD83D\uDCC4 ${parsed.filename} (${parsed.size_kb} KB) — Click to view</span>`;
      }
    }
  } catch(e) {}
  // Replace file URLs with clickable preview links
  text = text.replace(/(https?:\/\/[^\s<]*\/files\/[a-f0-9]+\/([^\s<"]+))/g,
    '<a href="$1" style="color:#6c5ce7;cursor:pointer;" onclick="event.preventDefault();openFileViewer(\'$1\')">\uD83D\uDCC4 $2</a>');
  text = text.replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>');
  text = text.replace(/`([^`]+)`/g, '<code>$1</code>');
  text = text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  text = text.replace(/\*(.+?)\*/g, '<em>$1</em>');
  text = text.replace(/(https?:\/\/[^\s<]+)(?!<\/a>)/g, '<a href="$1" target="_blank">$1</a>');
  return text;
}

function isNearBottom() {
  const m = document.getElementById('messages');
  // Consider "near bottom" if within 150px of the bottom
  return m.scrollHeight - m.scrollTop - m.clientHeight < 150;
}

function scrollBottom(force) {
  if (force || isNearBottom()) {
    const m = document.getElementById('messages');
    m.scrollTop = m.scrollHeight;
  }
  updateScrollNav();
}

function updateScrollNav() {
  const nav = document.getElementById('scrollNav');
  if (!nav) return;
  const m = document.getElementById('messages');
  const hasScroll = m.scrollHeight > m.clientHeight + 100;
  const atBottom = m.scrollHeight - m.scrollTop - m.clientHeight < 150;
  // Show buttons when there's scrollable content and user is not at the bottom
  nav.classList.toggle('visible', hasScroll && !atBottom);
}

// Listen for scroll events on the messages container
document.getElementById('messages').addEventListener('scroll', updateScrollNav);

// ── Active interactions tracking ──────────────────────────────────
let activeInteractions = {};  // agent_name → { startedAt, lastTool, status, msgPreview }
let activeTimer = null;

let _agentDoneAt = {};  // agentName → timestamp of last done (prevents ghost re-register)
function trackAgentStart(agentName, msgPreview) {
  // Ignore thinking events that arrive within 500ms after a done (race condition guard)
  const doneTs = _agentDoneAt[agentName];
  if (doneTs && Date.now() - doneTs < 500) {
    console.log('[trackAgentStart] IGNORED (too close to done)', agentName);
    return;
  }
  if (activeInteractions[agentName]) {
    // Already tracked — just update status (don't reset startedAt/preview)
    activeInteractions[agentName].status = 'thinking';
  } else {
    activeInteractions[agentName] = {
      startedAt: Date.now(), lastTool: '', status: 'thinking', msgPreview: msgPreview || '',
    };
  }
  updateActivePanel();
  if (!activeTimer) activeTimer = setInterval(updateActivePanel, 1000);
}
function trackAgentTool(agentName, toolName) {
  if (activeInteractions[agentName]) {
    activeInteractions[agentName].lastTool = toolName;
    activeInteractions[agentName].status = toolName;
  }
  updateActivePanel();
}
function trackAgentDone(agentName) {
  console.log('[trackAgentDone]', agentName, 'keys before:', Object.keys(activeInteractions));
  _agentDoneAt[agentName] = Date.now();
  delete activeInteractions[agentName];
  updateActivePanel();
  if (Object.keys(activeInteractions).length === 0 && activeTimer) {
    clearInterval(activeTimer); activeTimer = null;
  }
}
function updateActivePanel() {
  const panel = document.getElementById('activePanel');
  const rows = document.getElementById('activeRows');
  const names = Object.keys(activeInteractions);
  if (names.length === 0) {
    panel.classList.remove('visible');
    return;
  }
  panel.classList.add('visible');
  const now = Date.now();
  rows.innerHTML = names.map(name => {
    const info = activeInteractions[name];
    const secs = Math.round((now - info.startedAt) / 1000);
    const timeStr = secs < 60 ? secs + 's' : Math.floor(secs/60) + 'm' + (secs%60) + 's';
    // Build rich status: iter N · round N/M · N tools · [last_tool]
    let statusParts = [];
    if (info.iteration) statusParts.push('iter ' + info.iteration);
    if (info.round && info.maxRounds > 1) statusParts.push('round ' + info.round + '/' + info.maxRounds);
    if (info.totalTools > 0) statusParts.push(info.totalTools + ' tools');
    if (info.lastTool) statusParts.push('[' + info.lastTool + ']');
    const statusText = statusParts.length > 0 ? statusParts.join(' \u00b7 ') : 'thinking...';
    const preview = (!info.iteration && info.msgPreview) ? escapeHtml(info.msgPreview.substring(0, 40)) : '';
    const hue = Math.abs([...name].reduce((h,c) => (h * 31 + c.charCodeAt(0)) | 0, 0)) % 360;
    const color = 'hsl(' + hue + ',70%,65%)';
    return '<div class="active-row">'
      + '<span class="a-spinner" style="color:' + color + '">\u2733</span>'
      + '<span class="a-name" style="color:' + color + '">' + escapeHtml(name) + '</span>'
      + '<span class="a-msg">' + preview + '</span>'
      + '<span class="a-status">' + escapeHtml(statusText) + '</span>'
      + '<span class="a-time">' + timeStr + '</span>'
      + '<span class="a-actions">'
      + '<button title="Interrupt (force answer)" onclick="interruptSingle(\'' + escapeHtml(name) + '\')">&#x23F8;</button>'
      + '<button class="btn-stop" title="Stop" onclick="stopSingle(\'' + escapeHtml(name) + '\')">&#x25A0;</button>'
      + '</span></div>';
  }).join('');
}

async function interruptSingle(agentName) {
  if (!conversationId) return;
  try {
    await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'interrupt', conversation_id: conversationId, agent_name: agentName }),
      credentials: 'same-origin',
    });
  } catch(e) { console.warn('Interrupt failed:', e); }
}
async function stopSingle(agentName) {
  if (!conversationId) return;
  try {
    await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'cancel', conversation_id: conversationId, agent_name: agentName }),
      credentials: 'same-origin',
    });
    trackAgentDone(agentName);
  } catch(e) { console.warn('Stop failed:', e); }
}

const FUN_VERBS = [
  // Tech / Dev
  'Refactoring','Compiling','Debugging','Deploying','Optimizing','Linting','Minifying',
  'Transpiling','Dockerizing','Kuberneting','Microservicing','Rebasing','Merging',
  'Cherry-picking','Hotfixing','Monkey-patching','Sharding','Indexing','Caching',
  'Serializing','Deserializing','Tokenizing','Lexing','Parsing','Hashing','Encrypting',
  'Decrypting','Handshaking','Pipelining','Webhooking','Load-balancing','Auto-scaling',
  'Containerizing','Orchestrating','Provisioning','Terraforming','Ansibilizing',
  'GitOpsing','CI/CDing','Blue-greening','Canary-deploying','Feature-flagging',
  'A/B-testing','Stress-testing','Fuzz-testing','Benchmarking','Profiling',
  'Flame-graphing','Heap-dumping','Thread-pooling','Garbage-collecting','JITting',
  'AOT-compiling','Tree-shaking','Code-splitting','Lazy-loading','Prefetching',
  'Service-meshing','API-gatewaying','Rate-limiting','Circuit-breaking','Bulkheading',
  'Backpressuring','Dead-lettering','Event-sourcing','CQRS-ing','Saga-patterning',
  // Science
  'Hypothesizing','Experimenting','Calibrating','Quantifying','Synthesizing',
  'Centrifuging','Titrating','Distilling','Crystallizing','Polymerizing',
  'Sequencing','Splicing','Cloning','Mutating','Evolving','Spectrographing',
  'Electron-microscoping','Carbon-dating','Peer-reviewing','Replicating',
  'Correlating','Extrapolating','Interpolating','Normalizing','Standardizing',
  'Ionizing','Magnetizing','Polarizing','Oscillating','Resonating',
  'Diffracting','Refracting','Superposing','Entangling','Tunneling',
  'Annihilating','Fissioning','Fusioning','Plasma-confining','Supercooling',
  // Space
  'Launching','Orbiting','Docking','Spacewalking','Terraforming','Warp-driving',
  'Hyperjumping','Lightspeed-calculating','Asteroid-mining','Stargazing',
  'Nebula-surfing','Black-holing','Graviton-emitting','Solar-sailing',
  'Cryo-sleeping','Planet-scanning','Exoplanet-hunting','Comet-chasing',
  'Satellite-deploying','Moon-landing','Mars-colonizing','Ring-surfing',
  'Supernova-watching','Pulsar-timing','Quasar-mapping','Dark-mattering',
  'Cosmic-raying','Redshift-measuring','Singularity-approaching','Dyson-sphering',
  // Gaming
  'Respawning','Looting','Crafting','Speed-running','Combo-breaking',
  'Boss-fighting','Level-grinding','Rage-quitting','Tea-bagging','No-scoping',
  'Wall-running','Double-jumping','Rocket-jumping','Bunny-hopping','Strafing',
  'Camping','Ganking','Kiting','Aggro-pulling','Mana-regenerating',
  'Buff-stacking','Debuffing','Critical-hitting','Parrying','Dodge-rolling',
  'Inventory-managing','Quest-logging','Achievement-unlocking','Leaderboarding',
  'Speedhacking','Glitch-exploiting','Sequence-breaking','Any-percenting',
  'Frame-perfecting','Pixel-walking','Clipping','Noclipping','God-moding',
  // Cuisine
  'Sautéing','Flambéing','Caramelizing','Blanching','Braising','Julienning',
  'Deglazing','Reducing','Emulsifying','Fermenting','Proofing','Kneading',
  'Tempering','Sous-viding','Smoking','Curing','Pickling','Brining',
  'Marinating','Basting','Glazing','Torching','Dehydrating','Infusing',
  'Zesting','Chiffonading','Brunoise-cutting','Folding','Whipping',
  'Meringue-piping','Ganache-pouring','Crème-brûlée-ing','Sourdough-feeding',
  'Umami-boosting','Mise-en-placing','Knife-sharpening','Wok-haying',
  // Animals
  'Catifying','Doggoing','Penguin-waddling','Chameleon-blending','Dolphin-clicking',
  'Owl-hooting','Squirrel-stashing','Bee-pollinating','Spider-webbing',
  'Flamingo-posing','Sloth-hanging','Cheetah-sprinting','Whale-singing',
  'Parrot-mimicking','Octopus-camouflaging','Peacock-displaying','Beaver-damming',
  'Ant-marching','Butterfly-morphing','Gecko-climbing','Otter-floating',
  'Pangolin-curling','Axolotl-regenerating','Tardigrade-surviving',
  'Narwhal-jousting','Platypus-confusing','Capybara-chilling','Red-panda-ing',
  // Music
  'Beatboxing','Harmonizing','Riffing','Improvising','Crescendo-ing',
  'Syncopating','Arpeggiating','Tremolo-picking','Shredding','Djent-ing',
  'Dubstepping','Drum-rolling','Bass-dropping','Vinyl-scratching',
  'Auto-tuning','Looping','Sampling','Remixing','Mastering','EQ-ing',
  'Side-chaining','Reverb-drenching','Pitch-bending','Vocoding',
  'Theremin-waving','Yodeling','Beatmatching','Crossfading',
  // Magic / Fantasy
  'Enchanting','Conjuring','Transmuting','Summoning','Banishing','Scrying',
  'Wand-waving','Potion-brewing','Spell-casting','Rune-carving','Hexing',
  'Shapeshifting','Teleporting','Levitating','Astral-projecting',
  'Crystal-gazing','Alchemy-ing','Elixir-mixing','Grimoire-reading',
  'Familiar-bonding','Mana-channeling','Portal-opening','Illusion-weaving',
  'Necromancy-ing','Divination-ing','Abjuring','Evoking','Invoking',
  // Sports
  'Slam-dunking','Bicep-curling','Parkour-ing','Bouldering','Skateboarding',
  'Snowboarding','Surfing','Hang-gliding','Base-jumping','Free-running',
  'Cartwheeling','Backflipping','Pole-vaulting','Javelin-throwing',
  'Hurdle-clearing','Sprint-finishing','Marathon-pacing','Triathlon-ing',
  'CrossFit-ing','Deadlifting','Kettlebell-swinging','Yoga-posing',
  // Absurd / Inventés
  'Combobulating','Discombobulating','Recombobulating','Confuzzling',
  'Flibbergibbeting','Lollygagging','Dillydallying','Shillyshallying',
  'Skedaddling','Bamboozling','Cattywampusing','Gobsmacking','Wibble-wobbling',
  'Fluffernuttying','Kerfuffling','Hullabaloo-ing','Rigmarole-ing',
  'Bumblebee-ing','Malarkey-detecting','Shenanigan-foiling','Tomfoolery-ing',
  'Razzle-dazzling','Higgledy-piggling','Topsy-turvying','Wishy-washying',
  'Namby-pambying','Mumbo-jumboing','Hanky-pankying','Hocus-pocusing',
  'Abracadabra-ing','Supercalifragilisting','Whatchamacalliting',
  'Thingamajiggling','Doohickey-ing','Gizmo-fiddling','Widget-twiddling',
  'Doodad-adjusting','Contraption-ing','Rigamarole-ing','Brouhaha-ing',
  'Snafu-resolving','Fubar-unfubaring','Defenestrating','Discountenance-ing',
  'Flibberflabbering','Jibberjabbering','Gobbledygooking','Bibblebopping',
  'Rumpelstiltskin-ing','Serendipity-ing','Onomatopoeia-ing',
  'Antidisestablishmentarian-izing','Floccinaucinihilipilificating',
  'Pneumonoultramicroscopicsilico-ing','Hippopotomonstrosesquipedalian-ing',
  'Llanfairpwllgwyngyll-ing','Superdupering','Mega-ultra-ing',
  'Hyper-turbo-charging','Quantum-fluctuating','Nano-assembling',
  'Cyber-synergizing','Techno-babbling','Retro-encabulating','Turbo-encabulating',
  'Reverse-polarity-ing','Flux-capacitoring','Dilithium-crystaling',
  'Unobtainium-mining','Handwavium-applying','Plotholeum-patching',
  'Deux-ex-machina-ing','McGuffin-locating','Plot-armoring','Mary-Sue-ing',
  'Timey-wimey-ing','Wibbly-wobbly-ing','Ding-donging','Zigzagging',
  'Roly-polying','Teeter-tottering','Pitter-pattering','Clip-clopping',
  'Tick-tocking','Flip-flopping','Ping-ponging','Zig-zagging',
  'Shilly-shallying','Willy-nillying','Hokey-pokeying','Okey-dokeying',
  'Artsy-fartsying','Boogie-woogieing','Heebie-jeebieing','Lovey-doveying',
  'Itsy-bitsying','Teeny-weenying','Oopsie-daisy-ing','Easy-peasy-ing',
  // Pop culture
  'Jedi-mind-tricking','Force-pushing','Lightsaber-dueling','Kessel-running',
  'Pokémon-catching','Pikachu-thunderbolting','Hadouken-ing','Kamehameha-ing',
  'Falcon-punching','Shoryuken-ing','Fatality-performing','Mortal-Kombat-ing',
  'Mario-jumping','Sonic-spinning','Zelda-puzzle-solving','Master-sword-pulling',
  'Triforce-assembling','Portal-thinking','Cake-lying','Weighted-cube-loving',
  'Skyrim-sweetrolling','Arrow-to-the-kneeing','Minecraft-crafting',
  'Creeper-avoiding','Enderman-staring','Nether-portaling','Among-Us-venting',
  'Impostor-detecting','Rickrolling','Gandalf-passing','Hobbit-walking',
  'Precious-hunting','Infinity-stone-snapping','Vibranium-forging',
  'Wakanda-forevering','Avengers-assembling','Bat-signaling','Kryptonite-avoiding',
  'Web-slinging','Groot-growing','Baby-Yoda-sipping','Mandalorian-waying',
  'Allons-y-ing','Exterminating','Regenerating','TARDIS-materializing',
  // Philosophy / Abstract
  'Contemplating','Ruminating','Philosophizing','Pontificating','Cogitating',
  'Deliberating','Meditating','Introspecting','Existential-crisis-ing',
  'Nihilism-overcoming','Absurdism-embracing','Trolley-problem-solving',
  'Ship-of-Theseus-ing','Brain-in-a-vat-ing','Cogito-ergo-summing',
  'Categorical-imperative-ing','Virtue-ethic-ing','Utilitarian-calculating',
  'Dialectic-synthesizing','Phenomenology-reducing','Epistemology-ing',
  'Ontology-questioning','Hermeneutic-circling','Deconstructing',
  // Weather / Nature
  'Photosynthesizing','Cloud-seeding','Lightning-conducting','Tornado-chasing',
  'Tsunami-surfing','Earthquake-shaking','Volcano-erupting','Geyser-timing',
  'Aurora-borealis-ing','Tidal-waving','Monsoon-weathering','Blizzard-braving',
  'Rainbow-chasing','Dewdrop-collecting','Snowflake-crystallizing',
  'Tectonic-shifting','Continental-drifting','Erosion-sculpting',
  // Math
  'Differentiating','Integrating','Fourier-transforming','Eigenvalue-decomposing',
  'Matrix-multiplying','Gradient-descending','Backpropagating','Bayesian-updating',
  'Monte-Carlo-simulating','Regression-fitting','Clustering','Dimensionality-reducing',
  'Fibonacci-spiraling','Pi-calculating','Prime-sieving','Mandelbrot-zooming',
  'Fractal-iterating','Topology-bending','Riemann-hypothesizing',
  'P-vs-NP-wondering','Halting-problem-halting','Turing-completing',
  // Art / Creative
  'Watercoloring','Oil-painting','Sculpting','Chiseling','Pottery-wheeling',
  'Glaze-firing','Origami-folding','Calligraphy-ing','Cross-hatching',
  'Stippling','Impasto-layering','Glazing','Wet-on-wetting','Bob-Ross-ing',
  'Happy-little-treeing','Beat-the-devil-out-of-iting','Pixel-arting',
  'Voxel-modeling','UV-unwrapping','Rigging','Mocap-performing',
  'Rotoscoping','Compositing','Color-grading','Storyboarding',
  // Office / Corporate
  'Synergizing','Leveraging','Circling-back','Touching-base','Ping-ing',
  'Action-iteming','Deliverable-delivering','KPI-tracking','OKR-setting',
  'Standup-standing','Retro-specting','Sprint-planning','Backlog-grooming',
  'Story-pointing','Velocity-calculating','Burn-down-charting','Kanban-boarding',
  'Jira-ticketing','Confluence-documenting','Slack-threading','Zoom-fatiguing',
  'Calendar-tetris-ing','Meeting-about-meetings-ing','Email-cc-ing',
  'Reply-all-apologizing','Out-of-office-autoreplying','TPS-reporting',
  'Cover-sheet-attaching','Paradigm-shifting','Moving-the-needle',
  'Boiling-the-ocean','Low-hanging-fruiting','Value-adding',
  // AI / ML
  'Neural-networking','Deep-learning','Attention-paying','Transformer-attending',
  'Tokenizing','Embedding','Fine-tuning','RLHF-ing','Hallucination-avoiding',
  'Prompt-engineering','Chain-of-thoughting','Few-shot-learning',
  'Zero-shot-guessing','Gradient-clipping','Dropout-regularizing',
  'Batch-normalizing','Softmax-squishing','ReLU-activating',
  'Convolution-sliding','Pooling','Upsampling','GAN-generating',
  'Discriminator-fooling','Diffusion-denoising','LoRA-adapting',
  'Quantizing','Distilling','Pruning','Knowledge-graphing',
  'Retrieval-augmenting','Vector-searching','Cosine-similaritying',
  'Attention-is-all-you-needing','GPT-ing','BERT-masking','LLM-inferring',
  // Construction / Craft
  'Hammering','Nailing','Sawing','Sanding','Varnishing','Welding',
  'Soldering','Riveting','Plumbing','Wiring','Drywalling','Tiling',
  'Grouting','Caulking','Spackling','Priming','Basecoating','Topcoating',
  'Dovetail-joining','Mortise-tenoning','Lathe-turning','Bandsaw-cutting',
  // Dance
  'Moonwalking','Breakdancing','Waltzing','Tangoing','Salsa-ing',
  'Cha-cha-ing','Foxtrotting','Robot-dancing','Macarena-ing',
  'Flossing','Dabbing','Nae-nae-ing','Electric-sliding',
  'Riverdancing','Pirouetting','Voguing','Krumping','Tutting',
  // Household
  'Vacuum-cleaning','Dish-washing','Laundry-folding','Dust-bunnying',
  'Decluttering','Marie-Kondo-ing','Sparking-joy','Sock-pairing',
  'Tupperware-lid-matching','Remote-control-finding','Junk-drawer-organizing',
  'Fridge-tetris-ing','Couch-cushion-mining','Lint-rolling',
  // Internet
  'Doomscrolling','Meme-crafting','Copypasta-ing','Emoji-translating',
  'Hashtag-optimizing','Influencer-ing','Vlogging','Unboxing',
  'Click-baiting','SEO-optimizing','Cookie-accepting','CAPTCHA-solving',
  'Two-factor-authenticating','Password-resetting','Incognito-tabbing',
  'Tab-hoarding','Bookmark-organizing','Cache-clearing','Ad-blocking',
  'Dark-mode-enabling','Notification-silencing','Read-receipting',
  // Time-related
  'Procrastinating','Speedrunning','Time-traveling','Chrono-shifting',
  'Temporal-looping','Groundhog-daying','Déjà-vu-ing','Future-proofing',
  'Retro-grading','Nostalgia-tripping','Yesterday-remembering',
  'Tomorrow-planning','Deadline-approaching','Timezone-converting',
  // Emotions
  'Vibing','Manifesting','Zen-achieving','Chakra-aligning',
  'Aura-cleansing','Energy-matching','Good-vibes-only-ing',
  'Serotonin-boosting','Dopamine-hitting','Endorphin-rushing',
  'ASMR-tingling','Hygge-cozying','Wanderlust-ing',
  // Misc fun
  'Bubble-wrapping','Tetris-fitting','Rubik-cubing','Sudoku-solving',
  'Crossword-puzzling','Jenga-pulling','Domino-toppling','Rube-Goldberging',
  'Swiss-army-knifing','Duct-taping','Zip-tying','Bungee-cording',
  'MacGyver-ing','Life-hacking','Percussive-maintaining','Turning-it-off-and-on-again-ing',
  'Blowing-on-the-cartridge','Have-you-tried-restarting','Stack-overflowing',
  'Copy-pasting-from-SO','Works-on-my-machining','RTFM-ing','LGTM-ing',
  'Ship-it-ing','YOLO-deploying','Friday-deploying','Hotfix-on-prod-ing',
  'Git-blame-ing','Rubber-duck-debugging','Rage-coding','Caffeine-loading',
  'Coffee-brewing','Energy-drink-chugging','Snack-refueling','Pizza-ordering',
  'Nap-recharging','Cat-on-keyboard-handling','Tab-explosion-managing',
  'Infinite-loop-escaping','Segfault-investigating','Null-pointer-dereferencing',
  'Off-by-one-correcting','Semicolon-hunting','Bracket-matching',
  'Indentation-warring','Bikeshedding','Yak-shaving','Nerd-sniping',
  'Scope-creeping','Feature-creeping','Gold-plating','Over-engineering',
  'Premature-optimizing','Cargo-culting','Spaghetti-untangling',
  'Technical-debt-paying','Legacy-code-archeology-ing','Dependency-hell-escaping',
  'Node-modules-downloading','Left-pad-replacing','Is-it-DNS-checking',
  'Blame-the-network-ing','Firewall-blaming','Cloud-yelling-at',
  'Serverless-servering','NoSQL-not-only-SQLing','Blockchain-ing',
  'Web3-pivoting','NFT-minting','Metaverse-entering','AI-bubble-riding',
  'Buzzword-generating','Jargon-deploying','Acronym-expanding',
  'TLA-decoding','FYI-forwarding','Per-my-last-emailing',
  'New-phone-who-dis-ing','Rubber-stamping','Green-lighting'
];

let typingInterval = null;
const TYPING_COLORS = [
  '#a78bfa','#f472b6','#34d399','#fbbf24','#60a5fa',
  '#fb923c','#e879f9','#2dd4bf','#f87171','#a3e635',
  '#818cf8','#fb7185','#4ade80','#facc15','#38bdf8',
  '#f97316','#c084fc','#22d3ee','#ef4444','#84cc16',
];
let typingColorIdx = 0;

function randomVerb() {
  return FUN_VERBS[Math.floor(Math.random() * FUN_VERBS.length)];
}

function randomColor() {
  typingColorIdx = (typingColorIdx + 1) % TYPING_COLORS.length;
  return TYPING_COLORS[typingColorIdx];
}

function showTyping() {
  hideTyping();
  const el = document.createElement('div');
  el.className = 'typing';
  el.id = 'typing';
  const color = randomColor();
  el.innerHTML = '<span class="spinner" style="color:' + color + '">✻</span>'
    + '<span class="verb" style="color:' + color + '">' + randomVerb() + '...</span>';
  document.getElementById('messages').appendChild(el);
  scrollBottom();
  typingInterval = setInterval(() => {
    const t = document.getElementById('typing');
    if (t) {
      const c = randomColor();
      t.innerHTML = '<span class="spinner" style="color:' + c + '">✻</span>'
        + '<span class="verb" style="color:' + c + '">' + randomVerb() + '...</span>';
    }
  }, 3000);
}

function hideTyping() {
  if (typingInterval) { clearInterval(typingInterval); typingInterval = null; }
  const el = document.getElementById('typing');
  if (el) el.remove();
}

// Connect SSE for a conversation
function connectSSE(cid) {
  if (eventSource) eventSource.close();
  if (sseReconnectTimer) { clearTimeout(sseReconnectTimer); sseReconnectTimer = null; }
  sseRetryCount = 0;  // reset so onopen doesn't think we're reconnecting
  const token = getToken();
  const url = SSE_URL + '?conversation_id=' + encodeURIComponent(cid)
    + (token ? '&token=' + encodeURIComponent(token) : '');
  eventSource = new EventSource(url);

  eventSource.addEventListener('thinking', (e) => {
    lastSSEActivity = Date.now();
    // New iteration starting — finalize any previous streaming state
    if (streamingEl) {
      streamingEl = null;
      streamingText = '';
    }
    showTyping();
    const data = e.data ? JSON.parse(e.data) : {};
    const agentName = data.agent_name || 'assistant';
    trackAgentStart(agentName);
    const wait = data.waiting_seconds || 0;
    const verb = randomVerb();
    let status = wait > 5 ? verb + '... (' + wait + 's)' : (data.round > 1 ? verb + '... (round ' + data.round + ')' : verb + '...');
    document.getElementById('status').textContent = status;
  });

  eventSource.addEventListener('token', (e) => {
    lastSSEActivity = Date.now();
    hideTyping();
    const data = JSON.parse(e.data);
    streamingText += data.text;
    if (!streamingEl) {
      streamingEl = addMsg('assistant', '');
      streamingChunks.push(streamingEl);
    }
    streamingEl.innerHTML = renderMarkdown(streamingText);
    scrollBottom();
    document.getElementById('status').textContent = t('streaming');
  });

  eventSource.addEventListener('iteration_status', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    const agentName = data.agent_name || 'assistant';
    if (activeInteractions[agentName]) {
      activeInteractions[agentName].iteration = data.iteration;
      activeInteractions[agentName].maxIterations = data.max_iterations;
      activeInteractions[agentName].round = data.round;
      activeInteractions[agentName].maxRounds = data.max_rounds;
      activeInteractions[agentName].totalTools = data.total_tools;
      if (data.tools_called && data.tools_called.length > 0) {
        activeInteractions[agentName].lastTool = data.tools_called[data.tools_called.length - 1];
      }
    }
    updateActivePanel();
    document.getElementById('status').textContent =
      t('iterStatus', {agent: agentName, i: data.iteration, r: data.round, mr: data.max_rounds, t: data.total_tools});
    // Multi-tour: show compact progress message in chat when iteration advances
    if (data.iteration > 1 || data.round > 1) {
      const lastShown = activeInteractions[agentName] ? activeInteractions[agentName]._lastShownIter : undefined;
      if (data.iteration !== lastShown) {
        addMsg('system-compact', t('iterProgress', {
          agent: agentName, i: data.iteration, r: data.round,
          mr: data.max_rounds, t: data.total_tools
        }));
        if (activeInteractions[agentName]) {
          activeInteractions[agentName]._lastShownIter = data.iteration;
        }
      }
    }
  });

  // FlowFile incoming indicator
  eventSource.addEventListener('flowfile_in', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    const parts = [];
    if (data.agent) parts.push(data.agent);
    if (data.reason) parts.push(data.reason);
    else if (data.path) parts.push(data.source + ' ' + data.path);
    if (data.size > 0) parts.push((data.size / 1024).toFixed(1) + ' KB');
    if (parts.length) {
      addMsg('system-compact', '\u25b6 ' + parts.join(' \u00b7 '));
      scrollBottom();
    }
  });

  // Sub-agent visibility
  eventSource.addEventListener('sub_agent_start', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    trackAgentStart(data.agent_name, data.message ? data.message.substring(0, 40) : '');
    addMsg('system', t('subAgentStarted', {agent: data.agent_name}));
    scrollBottom();
  });

  eventSource.addEventListener('sub_agent_iteration', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    const agentName = data.agent_name || 'sub-agent';
    if (activeInteractions[agentName]) {
      activeInteractions[agentName].iteration = data.iteration;
      activeInteractions[agentName].maxIterations = data.max_iterations;
      activeInteractions[agentName].totalTools = data.total_tools;
      if (data.tools_called && data.tools_called.length > 0) {
        activeInteractions[agentName].lastTool = data.tools_called[data.tools_called.length - 1];
      }
    }
    updateActivePanel();
  });

  eventSource.addEventListener('sub_agent_tool', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    const agentName = data.agent_name || 'sub-agent';
    trackAgentTool(agentName, data.tool);
  });

  eventSource.addEventListener('sub_agent_done', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    const agent = data.agent_name || 'sub-agent';
    trackAgentDone(agent);
    // Display like btw: badge + markdown response
    if (data.response) {
      const el = addMsg('btw', '');
      const dur = data.duration_s || '?';
      const tok = (data.tokens_in || 0) + (data.tokens_out || 0);
      el.innerHTML = '<span style="color:#60a5fa;font-size:11px;">[' + escapeHtml(agent) + ' \u00b7 sub] </span>'
        + renderMarkdown(data.response)
        + '<span style="color:#555;font-size:10px;margin-left:8px;">(' + dur + 's, ' + tok + ' tok)</span>';
    } else if (data.error) {
      const el = addMsg('btw', '');
      el.innerHTML = '<span style="color:#f87171;font-size:11px;">[' + escapeHtml(agent) + ' \u00b7 sub] Error: ' + escapeHtml(data.error) + '</span>';
    }
    scrollBottom();
  });

  eventSource.addEventListener('tool_call', (e) => {
    lastSSEActivity = Date.now();
    hideTyping();
    // Finalize any in-progress streaming bubble before tool calls
    if (streamingEl) {
      streamingEl = null;
      streamingText = '';
    }
    const data = JSON.parse(e.data);
    trackAgentTool(data.agent_name || 'assistant', data.tool);
    addMsg('tool', t('callingTool', {tool: data.tool}));
    scrollBottom();
    document.getElementById('status').textContent = t('usingTool', {tool: data.tool});
  });

  eventSource.addEventListener('tool_result', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    // spawn_agents: skip verbose JSON — sub_agent_done events show the responses
    if (data.tool === 'spawn_agents' && data.result) {
      try {
        const agents = JSON.parse(data.result);
        if (Array.isArray(agents)) {
          const summary = agents.map(a => (a.agent || '?') + ': ' + a.status).join(', ');
          addMsg('tool', t('toolResult', {tool: 'spawn_agents', result: summary}));
          scrollBottom();
          showTyping();
          return;
        }
      } catch(ex) { /* fall through to default */ }
    }
    const preview = (data.result || '').substring(0, 200);
    addMsg('tool', t('toolResult', {tool: data.tool, result: preview + (data.result && data.result.length > 200 ? '...' : '')}));
    scrollBottom();
    showTyping();
  });

  eventSource.addEventListener('notification', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    const urgencyIcon = data.urgency === 'high' ? '\u{1F534}' : data.urgency === 'low' ? '\u{26AA}' : '\u{1F535}';
    addMsg('system', urgencyIcon + ' ' + (data.message || ''));
    scrollBottom();
    // Browser notification if page is not visible
    if (document.hidden && Notification.permission === 'granted') {
      new Notification('PyFi2 Agent', { body: data.message });
    }
  });

  eventSource.addEventListener('done', (e) => {
    lastSSEActivity = Date.now();
    hideTyping();
    const data = JSON.parse(e.data);
    const doneAgent = data.agent_name || data.source?.name || 'assistant';
    trackAgentDone(doneAgent);
    console.log('[SSE done]', data.response ? data.response.substring(0, 100) : '(empty)');
    // Sync message count to prevent poll from re-fetching these messages
    if (data.message_count) serverMsgCount = data.message_count;
    // Remove all intermediate streaming chunks (keep tool messages)
    for (const chunk of streamingChunks) {
      if (chunk && chunk.parentNode) chunk.remove();
    }
    // Strip internal tags that may leak into the response
    let resp = data.response || '';
    resp = resp.replace(/\s*\[NO_PENDING_WORK\]/g, '').replace(/\s*\[RECHECK_IN:\d+\]/g, '').trimEnd();
    // Show the final response (or fallback if empty), with source badge + metadata
    const extra = {};
    if (data.source) extra.source = data.source;
    if (data.model) extra.model = data.model;
    if (data.provider) extra.provider = data.provider;
    if (data.base_url) extra.base_url = data.base_url;
    if (data.tokens_in || data.tokens_out) { extra.tokens_in = data.tokens_in || 0; extra.tokens_out = data.tokens_out || 0; }
    if (data.duration_ms) extra.duration_ms = data.duration_ms;
    if (resp) {
      addMsg('assistant', resp, extra);
    } else if (streamingText) {
      addMsg('assistant', streamingText, extra);
    }
    streamingEl = null;
    streamingText = '';
    streamingChunks = [];
    scrollBottom();

    if (data.continuing) {
      // Intermediate round — agent will continue autonomously
      document.getElementById('status').textContent = t('continuing');
      showTyping();
    } else {
      // Final response — ensure active panel is cleaned up
      sending = false;
      document.getElementById('sendBtn').disabled = false;
      document.getElementById('stopBtn').style.display = 'none';
      document.getElementById('status').textContent = t('ready');
      // Belt-and-suspenders: if no more active interactions, clear timer
      if (Object.keys(activeInteractions).length === 0 && activeTimer) {
        clearInterval(activeTimer); activeTimer = null;
      }
    }
    // Refresh conversation list
    loadConversations();
    // Don't close SSE — keep listening for timer-triggered events
  });

  eventSource.addEventListener('cancelled', (e) => {
    lastSSEActivity = Date.now();
    const cancelData = e.data ? JSON.parse(e.data) : {};
    const cancelAgent = cancelData.agent_name || 'all';
    if (cancelAgent === 'all') {
      // Clear all interactions
      activeInteractions = {};
      updateActivePanel();
    } else {
      trackAgentDone(cancelAgent);
    }
    hideTyping();
    // Remove intermediate streaming chunks
    for (const chunk of streamingChunks) {
      if (chunk && chunk.parentNode) chunk.remove();
    }
    streamingEl = null;
    streamingText = '';
    streamingChunks = [];
    addMsg('system', cancelAgent !== 'all' ? '[' + cancelAgent + '] ' + t('cancelled') : t('cancelled'));
    scrollBottom();
    sending = false;
    document.getElementById('sendBtn').disabled = false;
    document.getElementById('stopBtn').style.display = 'none';
    document.getElementById('status').textContent = t('ready');
  });

  // --- BTW (side-channel) events ---
  let btwElements = {};  // agent_name → streaming element
  let btwTexts = {};

  eventSource.addEventListener('btw_thinking', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    const agent = data.agent_name || 'assistant';
    const el = addMsg('btw', '');
    el.innerHTML = '<span style="color:#60a5fa;font-size:11px;">[' + agent + ' · btw] </span><em style="color:#888;">thinking...</em>';
    btwElements[agent] = el;
    btwTexts[agent] = '';
    scrollBottom();
  });

  eventSource.addEventListener('btw_token', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    const agent = data.agent_name || 'assistant';
    btwTexts[agent] = (btwTexts[agent] || '') + data.text;
    const el = btwElements[agent];
    if (el) {
      el.innerHTML = '<span style="color:#60a5fa;font-size:11px;">[' + agent + ' · btw] </span>' + renderMarkdown(btwTexts[agent]);
      scrollBottom();
    }
  });

  eventSource.addEventListener('btw_done', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    const agent = data.agent_name || 'assistant';
    if (data.error) {
      const el = btwElements[agent];
      if (el) { el.innerHTML = '<span style="color:#f87171;font-size:11px;">[' + agent + ' · btw] Error: ' + data.error + '</span>'; }
      else { addMsg('error', '[' + agent + ' · btw] ' + data.error); }
    } else if (data.response && !btwTexts[agent]) {
      // Non-streaming fallback
      const el = btwElements[agent] || addMsg('btw', '');
      el.innerHTML = '<span style="color:#60a5fa;font-size:11px;">[' + agent + ' · btw] </span>' + renderMarkdown(data.response);
    }
    delete btwElements[agent];
    delete btwTexts[agent];
    scrollBottom();
  });

  eventSource.addEventListener('interrupting', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    addMsg('system', 'Interrupting ' + (data.agent || 'assistant') + ' — requesting immediate response...');
    scrollBottom();
  });

  eventSource.addEventListener('discard', (e) => {
    lastSSEActivity = Date.now();
    // Poll check-in returned [NO_PENDING_WORK] — discard any streamed tokens
    hideTyping();
    for (const chunk of streamingChunks) {
      if (chunk && chunk.parentNode) chunk.remove();
    }
    if (streamingEl && !streamingChunks.includes(streamingEl)) {
      streamingEl.remove();
    }
    streamingEl = null;
    streamingText = '';
    streamingChunks = [];
    sending = false;
    document.getElementById('status').textContent = '';
  });

  eventSource.addEventListener('file_request', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    handleFileRequest(data);
  });

  eventSource.addEventListener('exec_approval_request', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    showExecApprovalDialog(data);
  });

  eventSource.addEventListener('exec_output', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    appendExecOutput(data);
  });

  eventSource.addEventListener('tool_approval_request', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    showToolApprovalDialog(data);
  });

  eventSource.addEventListener('notification', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    showNotification(data);
  });

  eventSource.addEventListener('error_event', (e) => {
    lastSSEActivity = Date.now();
    hideTyping();
    const data = JSON.parse(e.data);
    addMsg('error', data.message || t('unknownError'));
    streamingEl = null;
    streamingText = '';
    streamingChunks = [];
    sending = false;
    document.getElementById('sendBtn').disabled = false;
    document.getElementById('stopBtn').style.display = 'none';
    document.getElementById('status').textContent = t('error');
  });

  eventSource.addEventListener('agent_response', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    const extra = {};
    if (data.source) extra.source = data.source;
    if (data.model) extra.model = data.model;
    if (data.provider) extra.provider = data.provider;
    if (data.base_url) extra.base_url = data.base_url;
    if (data.tokens_in || data.tokens_out) { extra.tokens_in = data.tokens_in || 0; extra.tokens_out = data.tokens_out || 0; }
    if (data.duration_ms) extra.duration_ms = data.duration_ms;
    addMsg('assistant', data.response || '', extra);
    scrollBottom();
  });

  eventSource.addEventListener('broadcast_done', (e) => {
    lastSSEActivity = Date.now();
    hideTyping();
    const data = JSON.parse(e.data);
    if (data.message_count) serverMsgCount = data.message_count;
    sending = false;
    document.getElementById('sendBtn').disabled = false;
    document.getElementById('stopBtn').style.display = 'none';
    document.getElementById('status').textContent = t('ready');
    addMsg('system', `Broadcast complete — ${data.agent_count} agent(s) responded.`);
    scrollBottom();
    loadConversations();
  });

  eventSource.addEventListener('thought_scheduled', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    addMsg('system', t('thoughtScheduled', { agent: data.agent || 'default', delay: data.delay || '?' }));
    scrollBottom();
  });

  eventSource.addEventListener('thought_firing', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    addMsg('system', t('thoughtFiring', { agent: data.agent || 'default' }));
    scrollBottom();
  });

  let sseHadError = false;  // track any error on this EventSource
  let sseEverConnected = false;  // only recover after a real disconnect (not initial connect hiccup)

  eventSource.onerror = (err) => {
    console.warn('[SSE] error, readyState:', eventSource.readyState, err);
    sseHadError = true;
    document.getElementById('status').textContent = t('reconnecting');
    if (eventSource.readyState === EventSource.CLOSED) {
      // Connection permanently closed — schedule reconnect with backoff
      _scheduleSSEReconnect(cid);
    }
    // readyState === CONNECTING: browser is auto-retrying, we just update status
  };

  eventSource.onopen = () => {
    console.log('[SSE] connected for', cid, sseHadError ? '(reconnect)' : '(initial)');
    // Only recover if we were previously connected and then lost the connection.
    // This avoids re-fetching the user message on the initial connection hiccup.
    const wasDisconnected = sseEverConnected && sseHadError;
    sseEverConnected = true;
    sseRetryCount = 0;
    sseHadError = false;
    if (wasDisconnected) {
      // We just reconnected (browser auto-retry or manual) — recover missed messages
      console.log('[SSE] recovering after reconnect...');
      _recoverConversation(cid);
    }
  };
}

function _scheduleSSEReconnect(cid) {
  if (sseReconnectTimer) clearTimeout(sseReconnectTimer);
  // Exponential backoff: 1s, 2s, 4s, 8s, max 15s
  const delay = Math.min(1000 * Math.pow(2, sseRetryCount), 15000);
  sseRetryCount++;
  console.log('[SSE] reconnecting in', delay, 'ms (attempt', sseRetryCount, ')');
  sseReconnectTimer = setTimeout(() => {
    sseReconnectTimer = null;
    if (!cid || cid !== conversationId) return;  // conversation changed, skip
    // Recover missed messages first, then reconnect SSE
    _recoverConversation(cid).then(() => {
      if (cid === conversationId) connectSSE(cid);
    });
  }, delay);
}

// ── Fallback Poll (30s) ──────────────────────────────────────────
function startPollTimer() {
  stopPollTimer();
  pollTimer = setInterval(() => {
    if (!conversationId) return;
    _recoverConversation(conversationId);
  }, 30000);
}
function stopPollTimer() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

// ── Local Files (File System Access API) ─────────────────────────
let localDirHandle = null;
let localDirName = '';

async function showPrompts() {
  try {
    const r = await fetch(AGENT_PATH, {
      method: 'POST', headers: {'Content-Type':'application/json', ...authHeaders()},
      body: JSON.stringify({action:'list_prompts', conversation_id: conversationId})
    });
    const data = await r.json();
    const prompts = data.prompts || [];
    if (!prompts.length) { addMsg('system', 'No prompts available. Create prompts via /prompt or manage_resource.'); return; }
    // Build a simple selection overlay
    let overlay = document.getElementById('promptOverlay');
    if (overlay) overlay.remove();
    overlay = document.createElement('div');
    overlay.id = 'promptOverlay';
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;z-index:9999';
    let html = '<div style="background:#1a1a2e;border:1px solid #0f3460;border-radius:12px;max-width:500px;width:90%;max-height:70vh;overflow-y:auto;padding:20px">';
    html += '<h3 style="margin:0 0 12px;color:#e94560">Prompt Library</h3>';
    for (const p of prompts) {
      html += '<div class="prompt-item" data-name="' + escapeHtml(p.name) + '" style="padding:10px;margin:4px 0;background:#16213e;border-radius:8px;cursor:pointer;border:1px solid transparent" onmouseenter="this.style.borderColor=\'#e94560\'" onmouseleave="this.style.borderColor=\'transparent\'">';
      html += '<div style="font-weight:600;color:#fff">' + escapeHtml(p.title || p.name) + '</div>';
      if (p.category) html += '<span style="font-size:11px;color:#888;margin-right:8px">' + escapeHtml(p.category) + '</span>';
      if (p.description) html += '<span style="font-size:11px;color:#aaa">' + escapeHtml(p.description) + '</span>';
      if (p.preview) html += '<div style="font-size:11px;color:#666;margin-top:4px">' + escapeHtml(p.preview) + '...</div>';
      html += '</div>';
    }
    html += '<button onclick="document.getElementById(\'promptOverlay\').remove()" style="margin-top:12px;padding:6px 16px;background:#0f3460;color:#fff;border:none;border-radius:6px;cursor:pointer">Close</button>';
    html += '</div>';
    overlay.innerHTML = html;
    overlay.querySelectorAll('.prompt-item').forEach(item => {
      item.addEventListener('click', async () => {
        const name = item.dataset.name;
        try {
          const r2 = await fetch(AGENT_PATH, {
            method: 'POST', headers: {'Content-Type':'application/json', ...authHeaders()},
            body: JSON.stringify({action:'get_prompt', name: name, conversation_id: conversationId})
          });
          const d2 = await r2.json();
          if (d2.content) {
            document.getElementById('input').value = d2.content;
            document.getElementById('input').focus();
          }
        } catch(e) { addMsg('error', 'Failed to load prompt: ' + e.message); }
        overlay.remove();
      });
    });
    document.body.appendChild(overlay);
  } catch (e) { addMsg('error', 'Failed to list prompts: ' + e.message); }
}

async function openLocalFolder() {
  if (!window.showDirectoryPicker) {
    alert(t('folderUnsupported'));
    return;
  }
  try {
    localDirHandle = await window.showDirectoryPicker({ mode: 'readwrite' });
    localDirName = localDirHandle.name;
    const btn = document.getElementById('folderBtn');
    btn.classList.add('active');
    btn.title = t('folderActive', {name: localDirName});
  } catch (e) {
    if (e.name !== 'AbortError') console.error('Directory picker error:', e);
  }
}

async function resolvePathHandle(dirHandle, pathStr, create) {
  const parts = pathStr.replace(/\\/g, '/').split('/').filter(Boolean);
  let current = dirHandle;
  for (let i = 0; i < parts.length - 1; i++) {
    current = await current.getDirectoryHandle(parts[i], { create: !!create });
  }
  return { parent: current, name: parts[parts.length - 1] || '' };
}

async function listLocalDir(path) {
  let target = localDirHandle;
  if (path && path !== '.' && path !== '/') {
    const parts = path.replace(/\\/g, '/').split('/').filter(Boolean);
    for (const part of parts) { target = await target.getDirectoryHandle(part); }
  }
  const entries = [];
  for await (const [name, handle] of target) {
    if (handle.kind === 'file') {
      try {
        const f = await handle.getFile();
        entries.push({ name, kind: 'file', size: f.size });
      } catch { entries.push({ name, kind: 'file' }); }
    } else {
      entries.push({ name, kind: 'directory' });
    }
  }
  entries.sort((a, b) => (a.kind === b.kind ? a.name.localeCompare(b.name) : a.kind === 'directory' ? -1 : 1));
  return { path: path || '.', entries };
}

async function readLocalFile(path) {
  const { parent, name } = await resolvePathHandle(localDirHandle, path, false);
  const fileHandle = await parent.getFileHandle(name);
  const file = await fileHandle.getFile();
  const text = await file.text();
  if (text.length > 100000) {
    return { content: text.substring(0, 100000), truncated: true, total_size: text.length };
  }
  return { content: text, size: text.length };
}

async function writeLocalFile(path, content) {
  const { parent, name } = await resolvePathHandle(localDirHandle, path, true);
  const fileHandle = await parent.getFileHandle(name, { create: true });
  const writable = await fileHandle.createWritable();
  await writable.write(content);
  await writable.close();
  return { written: true, path, size: content.length };
}

async function handleFileRequest(data) {
  const { request_id, action, path, content } = data;
  let result;
  try {
    if (!localDirHandle) {
      result = { error: 'No local directory open. Ask the user to click the folder button.' };
    } else if (action === 'list_dir') {
      result = await listLocalDir(path);
    } else if (action === 'read_file') {
      result = await readLocalFile(path);
    } else if (action === 'write_file') {
      result = await writeLocalFile(path, content || '');
    } else {
      result = { error: 'Unknown action: ' + action };
    }
  } catch (e) {
    result = { error: e.message || String(e) };
  }
  // POST result back to agent
  try {
    await fetch(API, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify({
        action: 'file_result',
        request_id: request_id,
        result: result,
        conversation_id: conversationId,
      }),
    });
  } catch (e) { console.error('Failed to send file result:', e); }
}

// ── Exec approval dialog ─────────────────────────────────────────
function showExecApprovalDialog(data) {
  const { request_id, action, command, risk_level, cwd, editable } = data;
  const overlay = document.createElement('div');
  overlay.className = 'exec-overlay';
  const riskLabel = risk_level.charAt(0).toUpperCase() + risk_level.slice(1);
  const cmdHtml = editable
    ? '<textarea id="execCmdEdit">' + escapeHtml(command) + '</textarea>'
    : '<code>' + escapeHtml(command) + '</code>';
  overlay.innerHTML = `
    <div class="exec-dialog">
      <h3>${escapeHtml(t('exec.approval_title') || 'Command Approval')}
        <span class="exec-risk ${risk_level}">${riskLabel}</span></h3>
      <div class="exec-cwd">${escapeHtml(t('exec.working_dir') || 'Working directory')}: ${escapeHtml(cwd || '.')}</div>
      <div class="exec-cmd">${cmdHtml}</div>
      <div class="exec-btns">
        <button class="exec-deny" onclick="resolveExec('${request_id}', false, this)">${escapeHtml(t('exec.deny') || 'Deny')}</button>
        <button class="exec-approve" onclick="resolveExec('${request_id}', true, this)">${escapeHtml(t('exec.approve') || 'Approve')}</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
}

async function resolveExec(requestId, approved, btn) {
  const overlay = btn.closest('.exec-overlay');
  const textarea = overlay.querySelector('#execCmdEdit');
  const editedCommand = textarea ? textarea.value : '';
  const result = { approved };
  if (editedCommand) result.edited_command = editedCommand;
  try {
    await fetch(API, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify({
        action: 'exec_result',
        request_id: requestId,
        result: result,
        conversation_id: conversationId,
      }),
    });
  } catch (e) { console.error('Failed to send exec result:', e); }
  overlay.remove();
}

// ── Tool Approval Dialog (Plan A) ─────────────────────────────────
function showToolApprovalDialog(data) {
  const { request_id, tool_name, action_summary } = data;
  const overlay = document.createElement('div');
  overlay.className = 'exec-overlay';
  overlay.innerHTML = `
    <div class="exec-dialog">
      <h3>${escapeHtml(t('tool_approval.title') || 'Tool Permission')}
        <span class="exec-risk medium">${escapeHtml(tool_name)}</span></h3>
      <div class="exec-cmd"><code>${escapeHtml(action_summary)}</code></div>
      <div class="exec-btns" style="display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end;">
        <button class="exec-deny" onclick="resolveToolApproval('${request_id}', 'deny', this)">${escapeHtml(t('tool_approval.deny') || 'Deny')}</button>
        <button class="exec-approve" onclick="resolveToolApproval('${request_id}', 'allow_once', this)">${escapeHtml(t('tool_approval.allow_once') || 'Allow Once')}</button>
        <button class="exec-approve" style="background:#1a7f37" onclick="resolveToolApproval('${request_id}', 'allow_session', this)">${escapeHtml(t('tool_approval.allow_session') || 'Allow for Session')}</button>
        <button class="exec-approve" style="background:#0d5d20" onclick="resolveToolApproval('${request_id}', 'always_allow', this)">${escapeHtml(t('tool_approval.always_allow') || 'Always Allow')}</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
}

async function resolveToolApproval(requestId, choice, btn) {
  const overlay = btn.closest('.exec-overlay');
  try {
    await fetch(API, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify({
        action: 'tool_approval_result',
        request_id: requestId,
        result: { choice },
        conversation_id: conversationId,
      }),
    });
  } catch (e) { console.error('Failed to send tool approval:', e); }
  overlay.remove();
}

// ── Notification Toast ────────────────────────────────────────────
function showNotification(data) {
  const { message, urgency } = data;
  const el = document.createElement('div');
  el.style.cssText = 'position:fixed;top:16px;right:16px;background:#da3633;color:#fff;padding:12px 20px;border-radius:8px;z-index:10001;font-size:14px;max-width:400px;box-shadow:0 4px 12px rgba(0,0,0,0.4);cursor:pointer;';
  if (urgency !== 'high') el.style.background = '#f0883e';
  el.textContent = message;
  el.onclick = () => el.remove();
  document.body.appendChild(el);
  setTimeout(() => { if (el.parentNode) el.remove(); }, 8000);
}

function appendExecOutput(data) {
  const { action, command, exit_code, stdout, stderr, duration_ms } = data;
  const el = document.createElement('div');
  el.className = 'terminal-output';
  let html = '<div class="term-header">$ ' + escapeHtml(command) + '</div>';
  if (stdout) html += '<div class="term-stdout">' + escapeHtml(stdout) + '</div>';
  if (stderr) html += '<div class="term-stderr">' + escapeHtml(stderr) + '</div>';
  const exitClass = exit_code === 0 ? 'ok' : 'fail';
  html += '<div class="term-exit ' + exitClass + '">exit ' + exit_code + ' (' + duration_ms + 'ms)</div>';
  el.innerHTML = html;
  document.getElementById('messages').appendChild(el);
  scrollBottom();
}

// ── Slash commands ───────────────────────────────────────────────
const HELP_DATA = {
  '/help': {
    usage: '/help [command]',
    short: 'Show available commands or detailed help for a command',
    detail: 'Without arguments, lists all commands. With a command name, shows detailed documentation.\nExample: /help agent',
  },
  '/agent': {
    usage: '/agent list | create | select | default | delete | msg | interrupt | btw | resume',
    short: 'Manage AI agents',
    detail: 'Create, list, select, message, or control AI agents.\n\n'
      + '  /agent list                   — List all agents (user + global)\n'
      + '  /agent create                 — Create a new agent (interactive)\n'
      + '  /agent select <name>          — Activate an agent for this conversation\n'
      + '  /agent default                — Switch back to the default agent\n'
      + '  /agent delete <name>          — Delete an agent by name\n'
      + '  /agent msg <name> <text>      — Send a message to a specific agent\n'
      + '  /agent msg ALL <text>         — Broadcast to all agents in parallel\n'
      + '  /agent interrupt <name|ALL>   — Force agent to stop and respond immediately\n'
      + '  /agent btw <name|ALL> <text>  — Side-channel question (no interruption)\n'
      + '  /agent resume <name>          — Tell agent to continue from where it stopped\n\n'
      + 'Agents define a system prompt, tools, model, and LLM service. '
      + 'The active agent shapes the AI\'s behavior for the conversation.',
  },
  '/skill': {
    usage: '/skill list | add <name> <prompt> | del <name>',
    short: 'Manage skills (single-shot prompt templates)',
    detail: 'Create, list, or delete skills.\n\n'
      + '  /skill list              — List all skills with active status\n'
      + '  /skill add <name> <prompt> — Create a skill with given prompt\n'
      + '  /skill del <name>        — Delete a skill\n\n'
      + 'Skills are prompt-only resources injected into the system prompt when active.',
  },
  '/add-skill': {
    usage: '/add-skill <name> <prompt>',
    short: 'Shortcut to create a skill',
    detail: 'Same as /skill add <name> <prompt>.',
  },
  '/resources': {
    usage: '/resources',
    short: 'List all resources (agents, skills, MCP servers)',
    detail: 'Shows all defined resources grouped by type, with activation status for the current conversation.',
  },
  '/activate': {
    usage: '/activate <agent|skill|mcp> <name>',
    short: 'Activate a resource for this conversation',
    detail: 'Activates an agent, skill, or MCP server.\n\n'
      + '  /activate agent researcher  — Activate the "researcher" agent\n'
      + '  /activate skill summarizer  — Activate the "summarizer" skill\n'
      + '  /activate mcp my_server     — Activate an MCP server',
  },
  '/deactivate': {
    usage: '/deactivate <agent|skill|mcp> <name>',
    short: 'Deactivate a resource from this conversation',
    detail: 'Deactivates an agent, skill, or MCP server for the current conversation.',
  },
  '/share': {
    usage: '/share <agent|skill|mcp> <name> <conversation_id>',
    short: 'Share a resource to another conversation',
    detail: 'Copies a resource activation to another conversation by ID.',
  },
  '/service': {
    usage: '/service list | install <type> <name> [config] | uninstall <name> | enable <name> | disable <name>',
    short: 'Manage LLM and external services',
    detail: 'Install, list, enable/disable, or uninstall services.\n\n'
      + '  /service list                    — List installed services\n'
      + '  /service install <type> <name> [key=val,...] — Install a service\n'
      + '  /service uninstall <name>        — Remove a service\n'
      + '  /service enable <name>           — Enable a service\n'
      + '  /service disable <name>          — Disable a service',
  },
  '/schedules': {
    usage: '/schedules list | del | add <YYYYMMDDHHmmss> [reason]',
    short: 'Manage scheduled poll rechecks',
    detail: 'List, add, or delete scheduled recheck times.\n\n'
      + '  /schedules list           — List pending schedules\n'
      + '  /schedules add <datetime> — Add a recheck (format: YYYYMMDDHHmmss)\n'
      + '  /schedules del            — Delete all schedules',
  },
  '/stop': {
    usage: '/stop',
    short: 'Stop the current agent generation',
    detail: 'Interrupts the running agent. The agent stops gracefully and shows [Cancelled].',
  },
  '/restart_from': {
    usage: '/restart_from [N]',
    short: 'Restart context from last N messages (default 5)',
    detail: 'Keeps only the last N messages as LLM context. Earlier messages stay in history but are ignored by the agent.\n\n'
      + '  /restart_from      — Keep last 5 messages\n'
      + '  /restart_from 10   — Keep last 10 messages\n\n'
      + 'Useful when the conversation gets too long or the agent loses focus.',
  },
  '/resume': {
    usage: '/resume [tokens]',
    short: 'Summarize conversation to N tokens and restart from summary',
    detail: 'Asks the LLM to summarize the entire conversation to approximately N tokens (default 500), then restarts from that summary.\n\n'
      + '  /resume       — Summarize to ~500 tokens\n'
      + '  /resume 1000  — Summarize to ~1000 tokens\n\n'
      + 'The summary replaces all previous context. New messages build on top of it.',
  },
  '/compact': {
    usage: '/compact',
    short: 'Compact conversation (summarize old messages)',
    detail: 'Summarizes older messages in the conversation to reduce context size while preserving key information.',
  },
  '/rebuild': {
    usage: '/rebuild',
    short: 'Rebuild context from full conversation history',
    detail: 'Reconstructs the LLM context from the complete conversation. If everything fits in the context window, restores fully; otherwise compacts.\n\nUseful after /compact or /resume to get back more context.',
  },
  '/context': {
    usage: '/context',
    short: 'View the current LLM context',
    detail: 'Shows what the LLM actually sees: the list of messages in the current context, token estimate, and whether the context has diverged from the conversation.',
  },
  '/files': {
    usage: '/files',
    short: 'Toggle the files panel',
    detail: 'Shows or hides the file browser panel for viewing and managing uploaded files.',
  },
  '/flows': {
    usage: '/flows',
    short: 'Toggle the flows panel',
    detail: 'Shows or hides the flows panel for monitoring active data flows.',
  },
  '/tasks': {
    usage: '/tasks',
    short: 'Toggle the scheduled tasks panel',
    detail: 'Shows or hides the panel listing scheduled background tasks.',
  },
  '/tools': {
    usage: '/tools',
    short: 'List available tools',
    detail: 'Shows all tools available to the AI agent in the current conversation, including builtins and custom tools.',
  },
  '/usage': {
    usage: '/usage',
    short: 'Show token usage statistics',
    detail: 'Displays token usage for the current conversation (prompt tokens, completion tokens, total).',
  },
  '/memory': {
    usage: '/memory list | del <id>',
    short: 'Manage conversation memories',
    detail: 'List or delete persistent memories stored by the agent.\n\n'
      + '  /memory list     — List all stored memories\n'
      + '  /memory del <id> — Delete a memory by ID',
  },
  '/install': {
    usage: '/install <filename.py>',
    short: 'Install a custom tool',
    detail: 'Install a custom tool from a Python file. Drag & drop a .py file into the chat or paste code.',
  },
  '/uninstall': {
    usage: '/uninstall <tool_name>',
    short: 'Uninstall a custom tool',
    detail: 'Remove a previously installed custom tool by name.',
  },
  '/link': {
    usage: '/link telegram <id> [bot_token] | unlink | status',
    short: 'Link/unlink external accounts (Telegram)',
    detail: 'Link your account to a Telegram user ID for cross-platform messaging.\n\n'
      + '  /link telegram <user_id> [bot_token] — Link Telegram account\n'
      + '  /link unlink                          — Unlink Telegram\n'
      + '  /link status                          — Show link status',
  },
  '/add-secret': {
    usage: '/add-secret <name> <value>',
    short: 'Store an encrypted secret',
    detail: 'Stores a secret value encrypted at rest. Available as ${secrets.key} in expressions.',
  },
  '/secrets': {
    usage: '/secrets',
    short: 'List stored secrets',
    detail: 'Lists all stored secret names (values are not shown). Also accessible as /list-secrets.',
  },
  '/add-variable': {
    usage: '/add-variable <name> <value>',
    short: 'Store a plaintext variable',
    detail: 'Stores a plaintext variable. Available as ${var.key} in expressions. Also: /add-var.',
  },
  '/variables': {
    usage: '/variables',
    short: 'List stored variables',
    detail: 'Lists all stored variables with their values. Also: /vars, /list-variables.',
  },
  '/view': {
    usage: '/view <filename>',
    short: 'Preview a file (image, PDF, text, code)',
    detail: 'Opens the file viewer overlay to preview a file by name. Supports images, PDF, text, and code files.',
  },
  '/thought': {
    usage: '/thought on [agent] [freq] | off [agent] | status [agent] | now [agent]',
    short: 'Random thought — agent thinks spontaneously',
    detail: 'Enable random spontaneous thoughts from an agent.\n\n'
      + '  /thought on 2-3/h         — Default agent, 2-3 times per hour\n'
      + '  /thought on researcher 1/2h — "researcher" agent, once per 2h\n'
      + '  /thought on 5-10/d        — 5-10 times per day\n'
      + '  /thought off              — Disable for default agent\n'
      + '  /thought off researcher   — Disable for "researcher"\n'
      + '  /thought status           — Show config and next trigger\n'
      + '  /thought now              — Trigger a thought immediately\n\n'
      + 'Frequency format: <min>[-<max>]/<duration>. Units: s, m, h, d.\n'
      + 'Thoughts only fire when the conversation is idle (no active interaction).',
  },
};

function cmdHelp(topic) {
  if (!topic) {
    let lines = ['<b>Available commands:</b>', ''];
    const cmds = Object.keys(HELP_DATA).sort();
    for (const cmd of cmds) {
      const h = HELP_DATA[cmd];
      lines.push('<code>' + cmd + '</code> — ' + escapeHtml(h.short));
    }
    lines.push('', 'Type <code>/help &lt;command&gt;</code> for detailed documentation.');
    const el = addMsg('system', '');
    el.innerHTML = lines.join('<br>');
  } else {
    const key = topic.startsWith('/') ? topic : '/' + topic;
    const h = HELP_DATA[key];
    if (!h) {
      addMsg('system', 'Unknown command: ' + key + '. Type /help to see available commands.');
      return;
    }
    let lines = [
      '<b>' + escapeHtml(key) + '</b>',
      '',
      '<b>Usage:</b> <code>' + escapeHtml(h.usage) + '</code>',
      '',
      '<pre style="margin:8px 0;white-space:pre-wrap;font-size:12px;background:rgba(255,255,255,0.05);padding:8px;border-radius:4px;">' + escapeHtml(h.detail) + '</pre>',
    ];
    const el = addMsg('system', '');
    el.innerHTML = lines.join('<br>');
  }
}

async function handleSlashCommand(text) {
  const parts = text.split(/\s+/);
  const cmd = parts[0].toLowerCase();

  if (cmd === '/stop') {
    await cancelAgent();
    return true;
  }

  if (cmd === '/restart_from' || cmd === '/restart') {
    const n = parseInt(parts[1]) || 5;
    if (!conversationId) { addMsg('system', t('noConv')); return true; }
    try {
      const resp = await fetch(API, {
        method: 'POST', headers: getAuthHeaders(),
        body: JSON.stringify({ action: 'restart_from', conversation_id: conversationId, keep_last: n }),
        credentials: 'same-origin',
      });
      const data = await resp.json();
      if (data.ok) {
        addMsg('system', t('restartFrom', {n: data.kept_messages}));
      } else {
        addMsg('error', data.error || 'Failed');
      }
    } catch (e) { addMsg('error', e.message); }
    return true;
  }

  if (cmd === '/resume') {
    const n = parseInt(parts[1]) || 500;
    if (!conversationId) { addMsg('system', t('noConv')); return true; }
    addMsg('system', t('resuming', {n: n}));
    // Fire and forget — don't block the UI during LLM summarization
    fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'resume_conversation', conversation_id: conversationId, max_tokens: n }),
      credentials: 'same-origin',
    }).then(r => r.json()).then(data => {
      if (data.ok) {
        addMsg('system', t('resumed', {n: data.messages_summarized, len: data.summary_length}));
      } else {
        addMsg('error', data.error || 'Failed');
      }
    }).catch(e => addMsg('error', e.message));
    return true;
  }

  if (cmd === '/help') {
    cmdHelp(parts[1] || '');
    return true;
  }

  if (cmd === '/schedules') {
    const sub = (parts[1] || 'list').toLowerCase();
    if (sub === 'list') {
      await cmdSchedulesList();
    } else if (sub === 'del' || sub === 'delete') {
      await cmdSchedulesDel();
    } else if (sub === 'add' && parts[2]) {
      await cmdSchedulesAdd(parts[2], parts.slice(3).join(' '));
    } else {
      addMsg('system', 'Usage: /schedules list | /schedules del | /schedules add YYYYMMDDHHmmss [reason]');
    }
    return true;
  }

  if (cmd === '/compact') {
    await cmdCompact();
    return true;
  }

  if (cmd === '/rebuild') {
    await cmdRebuild();
    return true;
  }

  if (cmd === '/context') {
    await cmdShowContext();
    return true;
  }

  if (cmd === '/files') {
    toggleFilesPanel();
    return true;
  }

  if (cmd === '/flows') {
    toggleFlowsPanel();
    return true;
  }

  if (cmd === '/tasks') {
    toggleSchedsPanel();
    return true;
  }

  if (cmd === '/tools') {
    await cmdToolsList();
    return true;
  }

  if (cmd === '/usage') {
    await cmdUsage();
    return true;
  }

  if (cmd === '/agent') {
    const sub = (parts[1] || 'list').toLowerCase();
    if (sub === 'list') {
      await cmdAgentList();
    } else if (sub === 'create') {
      await cmdAgentCreate();
    } else if (sub === 'select') {
      const name = parts[2] || '';
      await cmdAgentSelect(name);
    } else if (sub === 'default') {
      await cmdAgentSelect('');
    } else if (sub === 'delete' || sub === 'del') {
      const name = parts[2];
      if (!name) { addMsg('system', 'Usage: /agent delete <name>'); }
      else { await cmdAgentDelete(name); }
    } else if (sub === 'msg' || sub === 'message') {
      const target = parts[2] || '';
      const msgText = parts.slice(3).join(' ');
      if (!target) { addMsg('system', 'Usage: /agent msg <name|ALL> <message>'); }
      else if (!msgText) { addMsg('system', 'Usage: /agent msg ' + target + ' <message>'); }
      else if (target.toUpperCase() === 'ALL') { await cmdAgentMsgAll(msgText); }
      else { await cmdAgentMsg(target, msgText); }
    } else if (sub === 'interrupt' || sub === 'int') {
      const target = parts[2] || '';
      await cmdAgentInterrupt(target);
    } else if (sub === 'btw') {
      const target = parts[2] || '';
      const btwText = parts.slice(3).join(' ');
      if (!btwText && !target) { addMsg('system', 'Usage: /agent btw <name|ALL> <question>'); }
      else if (!btwText) {
        // No agent name given — treat target as message, send to default
        await cmdAgentBtw('', target + ' ' + parts.slice(3).join(' '));
      } else {
        await cmdAgentBtw(target, btwText);
      }
    } else if (sub === 'resume') {
      const target = parts[2] || '';
      const resumeMsg = parts.slice(3).join(' ') || 'Continue from where you left off.';
      if (target.toUpperCase() === 'ALL') { await cmdAgentMsgAll(resumeMsg); }
      else if (target) { await cmdAgentMsg(target, resumeMsg); }
      else {
        // Resume default assistant
        sending = true;
        const body = { message: resumeMsg };
        if (conversationId) body.conversation_id = conversationId;
        addMsg('user', resumeMsg);
        showTyping();
        try {
          const resp = await fetch(API, { method: 'POST', headers: getAuthHeaders(), body: JSON.stringify(body) });
          const data = await resp.json();
          if (data.conversation_id && !conversationId) { conversationId = data.conversation_id; connectSSE(conversationId); }
        } catch(e) { addMsg('error', e.message); hideTyping(); }
        sending = false;
      }
    } else {
      addMsg('system', 'Usage: /agent list | create | select | default | delete | msg | interrupt | btw | resume');
    }
    return true;
  }

  if (cmd === '/memory') {
    const sub = (parts[1] || 'list').toLowerCase();
    if (sub === 'list') {
      await cmdMemoryList();
    } else if (sub === 'del' || sub === 'delete') {
      const memId = parts[2];
      if (!memId) { addMsg('system', 'Usage: /memory del <memory_id>'); }
      else { await cmdMemoryDel(memId); }
    } else {
      addMsg('system', 'Usage: /memory list | /memory del <id>');
    }
    return true;
  }

  if (cmd === '/install') {
    addMsg('system', 'To install a tool, drag & drop a .py file into the chat or paste the code with:\n/install filename.py\n```python\n# your code here\n```');
    return true;
  }

  if (cmd === '/uninstall') {
    const toolName = parts[1];
    if (!toolName) { addMsg('system', 'Usage: /uninstall <tool_name>'); return true; }
    await cmdUninstallTool(toolName);
    return true;
  }

  if (cmd === '/link') {
    const sub = (parts[1] || '').toLowerCase();
    if (sub === 'telegram') {
      const tgId = parts[2];
      const botToken = parts[3] || '';
      if (!tgId) { addMsg('system', 'Usage: /link telegram <telegram_user_id> [bot_token]'); return true; }
      await cmdLinkTelegram(tgId, botToken);
    } else if (sub === 'unlink') {
      await cmdUnlinkTelegram();
    } else if (sub === 'status') {
      await cmdLinkStatus();
    } else {
      addMsg('system', 'Usage: /link telegram <id> | /link unlink | /link status');
    }
    return true;
  }

  if (cmd === '/add-secret') {
    const name = parts[1];
    const value = parts.slice(2).join(' ');
    if (!name || !value) { addMsg('system', t('secretAddUsage')); return true; }
    await cmdAddSecret(name, value);
    return true;
  }

  if (cmd === '/list-secrets' || cmd === '/secrets') {
    await cmdListSecrets();
    return true;
  }

  if (cmd === '/add-variable' || cmd === '/add-var') {
    const name = parts[1];
    const value = parts.slice(2).join(' ');
    if (!name || !value) { addMsg('system', t('variableAddUsage')); return true; }
    await cmdAddVariable(name, value);
    return true;
  }

  if (cmd === '/list-variables' || cmd === '/variables' || cmd === '/vars') {
    await cmdListVariables();
    return true;
  }

  if (cmd === '/skill') {
    const sub = (parts[1] || 'list').toLowerCase();
    if (sub === 'list') {
      await cmdSkillList();
    } else if (sub === 'add' || sub === 'create') {
      const name = parts[2];
      const prompt = parts.slice(3).join(' ');
      if (!name || !prompt) { addMsg('system', 'Usage: /skill add <name> <prompt>'); return true; }
      await cmdResourceAction('create_skill', {name, prompt});
    } else if (sub === 'del' || sub === 'delete') {
      const name = parts[2];
      if (!name) { addMsg('system', 'Usage: /skill del <name>'); return true; }
      await cmdResourceAction('delete_skill', {name});
    } else {
      addMsg('system', 'Usage: /skill list | add <name> <prompt> | del <name>');
    }
    return true;
  }

  if (cmd === '/add-skill') {
    const name = parts[1];
    const prompt = parts.slice(2).join(' ');
    if (!name || !prompt) { addMsg('system', 'Usage: /add-skill <name> <prompt>'); return true; }
    await cmdResourceAction('create_skill', {name, prompt});
    return true;
  }

  if (cmd === '/resources') {
    await cmdListResources();
    return true;
  }

  if (cmd === '/activate') {
    const rtype = parts[1];
    const rname = parts[2];
    if (!rtype || !rname) { addMsg('system', 'Usage: /activate <agent|skill|mcp> <name>'); return true; }
    await cmdResourceAction('activate_resource', {resource_type: rtype, name: rname});
    return true;
  }

  if (cmd === '/deactivate') {
    const rtype = parts[1];
    const rname = parts[2];
    if (!rtype || !rname) { addMsg('system', 'Usage: /deactivate <agent|skill|mcp> <name>'); return true; }
    await cmdResourceAction('deactivate_resource', {resource_type: rtype, name: rname});
    return true;
  }

  if (cmd === '/share') {
    const rtype = parts[1];
    const rname = parts[2];
    const targetConv = parts[3];
    if (!rtype || !rname || !targetConv) {
      addMsg('system', 'Usage: /share <agent|skill|mcp> <name> <conversation_id>');
      return true;
    }
    await cmdResourceAction('share_resource', {
      resource_type: rtype, name: rname, target_conversation_id: targetConv
    });
    return true;
  }

  if (cmd === '/view') {
    const filename = parts.slice(1).join(' ');
    if (!filename) { addMsg('system', 'Usage: /view <filename>'); return true; }
    openFileViewer(filename);
    return true;
  }

  if (cmd === '/service') {
    const sub = (parts[1] || 'list').toLowerCase();
    if (sub === 'list') {
      await cmdServiceList();
    } else if (sub === 'install') {
      const svcType = parts[2];
      const svcName = parts[3];
      const configStr = parts.slice(4).join(' ');
      if (!svcType || !svcName) {
        addMsg('system', 'Usage: /service install <type> <name> [key=val,key2=val2,...]');
        return true;
      }
      await cmdServiceAction('service_install', {
        service_type: svcType, service_name: svcName, config_str: configStr
      });
    } else if (sub === 'uninstall') {
      const svcName = parts[2];
      if (!svcName) { addMsg('system', 'Usage: /service uninstall <name>'); return true; }
      await cmdServiceAction('service_uninstall', {service_id: svcName});
    } else if (sub === 'enable') {
      const svcName = parts[2];
      if (!svcName) { addMsg('system', 'Usage: /service enable <name>'); return true; }
      await cmdServiceAction('service_enable', {service_id: svcName});
    } else if (sub === 'disable') {
      const svcName = parts[2];
      if (!svcName) { addMsg('system', 'Usage: /service disable <name>'); return true; }
      await cmdServiceAction('service_disable', {service_id: svcName});
    } else {
      addMsg('system', 'Usage: /service list | install <type> <name> [config] | uninstall <name> | enable <name> | disable <name>');
    }
    return true;
  }

  if (cmd === '/thought') {
    if (!conversationId) { addMsg('system', t('thoughtNoConv')); return true; }
    const sub = (parts[1] || 'status').toLowerCase();
    const body = { action: 'random_thought', conversation_id: conversationId, sub };
    const freqPattern = /^\d+(-\d+)?\/\d*[smhd]$/;
    if (sub === 'on') {
      if (parts[2] && !freqPattern.test(parts[2])) {
        body.agent = parts[2];
        body.frequency = parts[3] || '2-3/h';
      } else {
        body.frequency = parts[2] || '2-3/h';
      }
    } else if (sub === 'off' || sub === 'status' || sub === 'now') {
      if (parts[2]) body.agent = parts[2];
    }
    try {
      const resp = await fetch(API, { method: 'POST', headers: getAuthHeaders(), body: JSON.stringify(body) });
      const data = await resp.json();
      if (data.error) { addMsg('error', data.error); }
      else if (sub === 'on') { addMsg('system', t('thoughtEnabled', { agent: data.agent, freq: data.frequency, delay: data.next_in_seconds })); }
      else if (sub === 'off') { addMsg('system', t('thoughtDisabled', { agent: data.agent })); }
      else if (sub === 'now') { addMsg('system', t('thoughtTriggered', { agent: data.agent })); }
      else { addMsg('system', data.enabled ? t('thoughtStatus', { agent: data.agent, freq: data.frequency, delay: data.next_in_seconds }) : t('thoughtStatusOff', { agent: data.agent })); }
    } catch (e) { addMsg('error', 'Failed: ' + e.message); }
    return true;
  }

  return false; // not a known command — send as normal message
}

async function cmdSchedulesList() {
  if (!conversationId) { addMsg('system', 'No active conversation'); return; }
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'list_schedules', conversation_id: conversationId }),
    });
    const data = await resp.json();
    const scheds = data.schedules || [];
    if (scheds.length === 0) {
      addMsg('system', 'No scheduled rechecks for this conversation.');
    } else {
      const lines = scheds.map(s => {
        const dt = new Date(s.recheck_at * 1000).toLocaleString();
        return `\u2022 ${dt} \u2014 ${s.reason || '(no reason)'}`;
      });
      addMsg('system', 'Scheduled rechecks:\n' + lines.join('\n'));
    }
  } catch (e) { addMsg('error', 'Failed to list schedules: ' + e.message); }
}

async function cmdSchedulesDel() {
  if (!conversationId) { addMsg('system', 'No active conversation'); return; }
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'delete_schedule', conversation_id: conversationId }),
    });
    const data = await resp.json();
    addMsg('system', data.cancelled ? 'Schedule cancelled.' : 'No schedule to cancel.');
  } catch (e) { addMsg('error', 'Failed to delete schedule: ' + e.message); }
}

async function cmdSchedulesAdd(dateStr, reason) {
  if (!conversationId) { addMsg('system', 'No active conversation'); return; }
  if (!/^\d{14}$/.test(dateStr)) {
    addMsg('system', 'Invalid date format. Use YYYYMMDDHHmmss (e.g. 20260312140000)');
    return;
  }
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({
        action: 'add_schedule', conversation_id: conversationId,
        at: dateStr, reason: reason || 'manual schedule',
      }),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); return; }
    const dt = new Date(data.at * 1000).toLocaleString();
    addMsg('system', 'Schedule added: ' + dt);
  } catch (e) { addMsg('error', 'Failed to add schedule: ' + e.message); }
}

async function cmdUsage() {
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'get_usage' }),
    });
    const data = await resp.json();
    const usage = data.usage || {};
    const lines = [];
    for (const [uid, u] of Object.entries(usage)) {
      const totalIn = (u.total_in || 0).toLocaleString();
      const totalOut = (u.total_out || 0).toLocaleString();
      lines.push(`**${uid}**: ${totalIn} in / ${totalOut} out`);
      const models = u.models || {};
      for (const [model, m] of Object.entries(models)) {
        lines.push(`  \u2022 ${model}: ${m.in.toLocaleString()} in / ${m.out.toLocaleString()} out`);
      }
    }
    if (lines.length === 0) { addMsg('system', 'No token usage recorded yet.'); }
    else { addMsg('system', 'Token usage:\n' + lines.join('\n')); }
  } catch (e) { addMsg('error', 'Failed to get usage: ' + e.message); }
}

async function cmdLinkTelegram(tgId, botToken) {
  try {
    const payload = { action: 'link_telegram', telegram_user_id: tgId };
    if (botToken) { payload.bot_token = botToken; }
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify(payload),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); }
    else {
      let msg = `Telegram user ${tgId} linked successfully!`;
      if (data.bot_username) { msg += ` Personal bot: @${data.bot_username}`; }
      if (data.bot_warning) { msg += `\n\u26a0\ufe0f ${data.bot_warning}`; }
      msg += '\nYou can now use /conv commands on Telegram to access your conversations.';
      addMsg('system', msg);
    }
  } catch (e) { addMsg('error', 'Failed to link: ' + e.message); }
}

async function cmdUnlinkTelegram() {
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'unlink_telegram' }),
    });
    const data = await resp.json();
    if (data.unlinked) { addMsg('system', 'Telegram account unlinked.'); }
    else { addMsg('system', 'No Telegram link found.'); }
  } catch (e) { addMsg('error', 'Failed to unlink: ' + e.message); }
}

async function cmdLinkStatus() {
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'get_links' }),
    });
    const data = await resp.json();
    const links = data.links || {};
    if (Object.keys(links).length === 0) {
      addMsg('system', 'No linked accounts. Use /link telegram <id> to link.');
    } else {
      const lines = Object.entries(links).map(([ch, id]) => `\u2022 ${ch}: ${id}`);
      const active = data.active_telegram_conv || 'none';
      addMsg('system', 'Linked accounts:\n' + lines.join('\n') + '\n\nActive Telegram conversation: ' + active);
    }
  } catch (e) { addMsg('error', 'Failed to get links: ' + e.message); }
}

async function cmdAgentList() {
  if (!conversationId) { addMsg('system', 'No active conversation'); return; }
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'list_agents', conversation_id: conversationId }),
    });
    const data = await resp.json();
    const agents = data.agents || {};
    const selected = data.selected || '';
    const names = Object.keys(agents);
    if (names.length === 0) {
      addMsg('system', 'No agents defined. Use /agent create to add one.');
    } else {
      const lines = names.map(n => {
        const marker = n === selected ? ' \u2705' : '';
        const prompt = agents[n].prompt.substring(0, 80);
        return `\u2022 **${n}**${marker} \u2014 ${prompt}...`;
      });
      addMsg('system', `Agents (${selected ? 'active: ' + selected : 'none selected'}):\n` + lines.join('\n'));
    }
  } catch (e) { addMsg('error', 'Failed to list agents: ' + e.message); }
}

async function cmdAgentCreate() {
  if (!conversationId) { addMsg('system', 'No active conversation'); return; }
  const name = prompt('Agent name:');
  if (!name) return;
  const agentPrompt = prompt('System prompt for this agent:');
  if (!agentPrompt) return;
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({
        action: 'create_agent', conversation_id: conversationId,
        name: name, prompt: agentPrompt,
      }),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); return; }
    addMsg('system', `Agent '${name}' created. Use /agent select ${name} to activate.`);
  } catch (e) { addMsg('error', 'Failed to create agent: ' + e.message); }
}

async function cmdAgentSelect(name) {
  if (!conversationId) {
    // No conversation yet — store pending selection, will be applied on first message
    pendingAgent = name || null;
    addMsg('system', name ? `Agent '${name}' selected (will activate on first message).` : 'Switched to default agent.');
    return;
  }
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({
        action: 'select_agent', conversation_id: conversationId,
        name: name,
      }),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); return; }
    addMsg('system', name ? `Agent '${name}' selected.` : 'Switched to default agent.');
  } catch (e) { addMsg('error', 'Failed to select agent: ' + e.message); }
}

async function cmdAgentDelete(name) {
  if (!conversationId) { addMsg('system', 'No active conversation'); return; }
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({
        action: 'delete_agent', conversation_id: conversationId,
        name: name,
      }),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); return; }
    addMsg('system', data.deleted ? `Agent '${name}' deleted.` : `Agent '${name}' not found.`);
  } catch (e) { addMsg('error', 'Failed to delete agent: ' + e.message); }
}

async function cmdAgentMsg(agentName, text) {
  // Send a message to a specific agent without changing the active agent
  addMsg('user', '[→ ' + agentName + '] ' + text);
  streamingEl = null;
  streamingText = '';
  streamingChunks = [];
  showTyping();
  sending = true;
  lastSSEActivity = Date.now();
  document.getElementById('status').textContent = t('sending');

  try {
    const body = { message: text, target_agent: agentName };
    if (conversationId) body.conversation_id = conversationId;
    const ttlVal = parseInt(document.getElementById('ttlSelect').value, 10);
    if (ttlVal > 0) body.ttl = ttlVal;

    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify(body),
      credentials: 'same-origin',
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); hideTyping(); sending = false; return; }
    if (data.conversation_id && !conversationId) {
      conversationId = data.conversation_id;
      connectSSE(conversationId);
    }
    if (data.message_count) serverMsgCount = data.message_count;
  } catch (e) {
    addMsg('error', 'Failed to send to agent: ' + e.message);
    hideTyping();
    sending = false;
  }
}

async function cmdAgentMsgAll(text) {
  // Broadcast a message to ALL agents in parallel
  if (!conversationId) {
    // Need a conversation first — send a dummy to create one
    addMsg('system', 'Start a conversation first before broadcasting.');
    return;
  }
  addMsg('user', '[→ ALL] ' + text);
  showTyping();
  sending = true;
  lastSSEActivity = Date.now();
  document.getElementById('status').textContent = 'Broadcasting...';

  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({
        action: 'broadcast_agents',
        conversation_id: conversationId,
        message: text,
      }),
      credentials: 'same-origin',
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); hideTyping(); sending = false; return; }
    // Responses will come via SSE agent_response events
  } catch (e) {
    addMsg('error', 'Broadcast failed: ' + e.message);
    hideTyping();
    sending = false;
  }
}

async function cmdAgentInterrupt(target) {
  if (!conversationId) { addMsg('system', 'No active conversation.'); return; }
  const isAll = target.toUpperCase() === 'ALL';
  addMsg('system', isAll ? 'Interrupting all agents...' : ('Interrupting ' + (target || 'assistant') + '...'));
  try {
    if (isAll) {
      // Interrupt all: fetch agent list then interrupt each
      const listResp = await fetch(API, {
        method: 'POST', headers: getAuthHeaders(),
        body: JSON.stringify({ action: 'list_resources', conversation_id: conversationId }),
      });
      const listData = await listResp.json();
      const agents = (listData.agents || []).map(a => a.name);
      // Interrupt default + each agent
      await fetch(API, { method: 'POST', headers: getAuthHeaders(),
        body: JSON.stringify({ action: 'interrupt', conversation_id: conversationId, agent_name: '' }) });
      for (const name of agents) {
        await fetch(API, { method: 'POST', headers: getAuthHeaders(),
          body: JSON.stringify({ action: 'interrupt', conversation_id: conversationId, agent_name: name }) });
      }
    } else {
      await fetch(API, { method: 'POST', headers: getAuthHeaders(),
        body: JSON.stringify({ action: 'interrupt', conversation_id: conversationId, agent_name: target || '' }),
      });
    }
  } catch (e) { addMsg('error', 'Interrupt failed: ' + e.message); }
}

async function cmdAgentBtw(target, question) {
  if (!conversationId) { addMsg('system', 'No active conversation.'); return; }
  const agent = target || '';
  const isAll = agent.toUpperCase() === 'ALL';
  addMsg('user', '[btw' + (agent ? ' → ' + agent : '') + '] ' + question);
  try {
    await fetch(API, { method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({
        action: 'btw', conversation_id: conversationId,
        agent_name: isAll ? 'ALL' : agent, message: question,
      }),
    });
    // Response comes via SSE btw_token/btw_done events
  } catch (e) { addMsg('error', 'BTW failed: ' + e.message); }
}

async function cmdMemoryList() {
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'list_memories' }),
    });
    const data = await resp.json();
    const mems = data.memories || [];
    if (mems.length === 0) {
      addMsg('system', 'No memories stored. The agent can use the "remember" tool to store facts.');
    } else {
      const lines = mems.map(m => {
        const tags = m.tags.length ? ` [${m.tags.join(', ')}]` : '';
        return `\u2022 \`${m.id}\`${tags} \u2014 ${m.text}`;
      });
      addMsg('system', `${mems.length} memories:\n` + lines.join('\n'));
    }
  } catch (e) { addMsg('error', 'Failed to list memories: ' + e.message); }
}

async function cmdMemoryDel(memId) {
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'delete_memory', memory_id: memId }),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); return; }
    addMsg('system', data.deleted ? `Memory ${memId} deleted.` : `Memory ${memId} not found.`);
  } catch (e) { addMsg('error', 'Failed to delete memory: ' + e.message); }
}

async function cmdToolsList() {
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'list_tools' }),
    });
    const data = await resp.json();
    const tools = data.tools || [];
    if (tools.length === 0) {
      addMsg('system', 'No dynamic tools installed. Use /install to add one.');
    } else {
      const lines = tools.map(t =>
        `\u2022 **${t.tool_name}** \u2014 ${t.description} (by ${t.owner})`
      );
      addMsg('system', 'Dynamic tools:\n' + lines.join('\n'));
    }
  } catch (e) { addMsg('error', 'Failed to list tools: ' + e.message); }
}

async function cmdUninstallTool(toolName) {
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'uninstall_tool', tool_name: toolName }),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); return; }
    addMsg('system', data.uninstalled ? `Tool '${toolName}' uninstalled.` : `Tool '${toolName}' not found.`);
  } catch (e) { addMsg('error', 'Failed to uninstall tool: ' + e.message); }
}

async function cmdCompact() {
  if (!conversationId) { addMsg('system', 'No active conversation'); return; }
  addMsg('system', 'Compacting conversation...');
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'compact', conversation_id: conversationId }),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', 'Compaction failed: ' + data.error); return; }
    addMsg('system', `Compacted: ${data.before} messages \u2192 ${data.after} messages`);
  } catch (e) { addMsg('error', 'Compaction failed: ' + e.message); }
}

async function cmdRebuild() {
  if (!conversationId) { addMsg('system', t('noConv')); return; }
  addMsg('system', t('rebuilding'));
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'rebuild', conversation_id: conversationId }),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', 'Rebuild failed: ' + data.error); return; }
    addMsg('system', t('rebuilt', {action: data.action, before: data.before, after: data.after, tokens: data.token_estimate}));
  } catch (e) { addMsg('error', 'Rebuild failed: ' + e.message); }
}

async function cmdShowContext() {
  if (!conversationId) { addMsg('system', t('noConv')); return; }
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'get_context', conversation_id: conversationId }),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); return; }
    showContextOverlay(data);
  } catch (e) { addMsg('error', 'Failed to load context: ' + e.message); }
}

let _ctxFullData = null;

async function ctxLoadFull() {
  if (_ctxFullData) return _ctxFullData;
  const resp = await fetch(API, {
    method: 'POST', headers: getAuthHeaders(),
    body: JSON.stringify({ action: 'get_context_full', conversation_id: conversationId }),
  });
  _ctxFullData = await resp.json();
  return _ctxFullData;
}

async function ctxRefresh() {
  _ctxFullData = null;
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'get_context', conversation_id: conversationId }),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); return; }
    showContextOverlay(data);
  } catch (e) { addMsg('error', 'Failed: ' + e.message); }
}

async function ctxEditMessage(index) {
  const full = await ctxLoadFull();
  if (full.error) { addMsg('error', full.error); return; }
  const msg = full.context[index];
  if (!msg) return;
  const row = document.getElementById('ctx-row-' + index);
  if (!row) return;
  const content = typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content);
  row.innerHTML = '<div style="padding:8px">'
    + '<div style="margin-bottom:6px"><label style="color:#808090;font-size:11px;margin-right:6px">' + t('contextRole') + ':</label>'
    + '<select id="ctx-edit-role-' + index + '" style="background:#0d1117;color:#e0e0e0;border:1px solid #333;border-radius:4px;padding:2px 6px;font-size:12px">'
    + '<option value="system"' + (msg.role==='system'?' selected':'') + '>system</option>'
    + '<option value="user"' + (msg.role==='user'?' selected':'') + '>user</option>'
    + '<option value="assistant"' + (msg.role==='assistant'?' selected':'') + '>assistant</option>'
    + '<option value="tool"' + (msg.role==='tool'?' selected':'') + '>tool</option>'
    + '</select></div>'
    + '<textarea id="ctx-edit-ta-' + index + '" style="width:100%;min-height:120px;background:#0d1117;color:#c0c0d0;border:1px solid #333;border-radius:6px;padding:8px;font-size:12px;font-family:monospace;resize:vertical">' + content.replace(/</g,'&lt;').replace(/>/g,'&gt;') + '</textarea>'
    + '<div style="display:flex;gap:6px;margin-top:6px">'
    + '<button onclick="ctxSaveEdit(' + index + ')" style="background:#2563eb;color:#fff;border:none;border-radius:6px;padding:4px 12px;cursor:pointer;font-size:12px">' + t('contextSave') + '</button>'
    + '<button onclick="ctxRefresh()" style="background:#333;color:#ccc;border:none;border-radius:6px;padding:4px 12px;cursor:pointer;font-size:12px">' + t('contextCancel') + '</button>'
    + '</div></div>';
}

async function ctxSaveEdit(index) {
  const ta = document.getElementById('ctx-edit-ta-' + index);
  const roleEl = document.getElementById('ctx-edit-role-' + index);
  if (!ta) return;
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'edit_context', conversation_id: conversationId, index: index, content: ta.value, role: roleEl ? roleEl.value : undefined }),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); return; }
    addMsg('system', t('contextSaved', { n: data.message_count, tokens: data.token_estimate }));
    _ctxFullData = null;
    ctxRefresh();
  } catch (e) { addMsg('error', 'Failed: ' + e.message); }
}

async function ctxDeleteMessage(index) {
  if (!confirm(t('contextDeleteConfirm'))) return;
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'delete_context_message', conversation_id: conversationId, index: index }),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); return; }
    addMsg('system', t('contextSaved', { n: data.message_count, tokens: data.token_estimate }));
    _ctxFullData = null;
    ctxRefresh();
  } catch (e) { addMsg('error', 'Failed: ' + e.message); }
}

async function ctxAddMessage() {
  const container = document.getElementById('ctx-add-form');
  if (container) { container.remove(); return; }
  const list = document.getElementById('ctx-msg-list');
  if (!list) return;
  const form = document.createElement('div');
  form.id = 'ctx-add-form';
  form.style.cssText = 'padding:10px;border-top:1px solid #333';
  form.innerHTML = '<div style="margin-bottom:6px"><label style="color:#808090;font-size:11px;margin-right:6px">' + t('contextRole') + ':</label>'
    + '<select id="ctx-add-role" style="background:#0d1117;color:#e0e0e0;border:1px solid #333;border-radius:4px;padding:2px 6px;font-size:12px">'
    + '<option value="system">system</option><option value="user" selected>user</option><option value="assistant">assistant</option></select></div>'
    + '<textarea id="ctx-add-content" style="width:100%;min-height:80px;background:#0d1117;color:#c0c0d0;border:1px solid #333;border-radius:6px;padding:8px;font-size:12px;font-family:monospace;resize:vertical" placeholder="' + t('contextContent') + '..."></textarea>'
    + '<div style="display:flex;gap:6px;margin-top:6px">'
    + '<button onclick="ctxSaveNewMessage()" style="background:#2563eb;color:#fff;border:none;border-radius:6px;padding:4px 12px;cursor:pointer;font-size:12px">' + t('contextSave') + '</button>'
    + '<button onclick="document.getElementById(\'ctx-add-form\').remove()" style="background:#333;color:#ccc;border:none;border-radius:6px;padding:4px 12px;cursor:pointer;font-size:12px">' + t('contextCancel') + '</button>'
    + '</div>';
  list.parentNode.appendChild(form);
}

async function ctxSaveNewMessage() {
  const role = document.getElementById('ctx-add-role')?.value || 'user';
  const content = document.getElementById('ctx-add-content')?.value || '';
  if (!content.trim()) return;
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'add_context_message', conversation_id: conversationId, role: role, content: content }),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); return; }
    addMsg('system', t('contextSaved', { n: data.message_count, tokens: data.token_estimate }));
    _ctxFullData = null;
    ctxRefresh();
  } catch (e) { addMsg('error', 'Failed: ' + e.message); }
}

async function ctxReplaceAll() {
  const full = await ctxLoadFull();
  if (full.error) { addMsg('error', full.error); return; }
  const overlay = document.getElementById('contextOverlay');
  if (!overlay) return;
  const inner = overlay.querySelector('div');
  inner.innerHTML = '<div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">'
    + '<h3 style="margin:0;color:#e0e0e0;font-size:16px">' + t('contextReplaceAll') + '</h3>'
    + '<button onclick="ctxRefresh()" style="background:none;border:none;color:#aaa;cursor:pointer;font-size:18px;margin-left:auto">&times;</button>'
    + '</div>'
    + '<textarea id="ctx-replace-ta" style="flex:1;width:100%;background:#0d1117;color:#c0c0d0;border:1px solid #333;border-radius:6px;padding:10px;font-size:12px;font-family:monospace;resize:none">' + JSON.stringify(full.context, null, 2).replace(/</g,'&lt;').replace(/>/g,'&gt;') + '</textarea>'
    + '<div style="display:flex;gap:6px;margin-top:10px">'
    + '<button onclick="ctxSaveReplaceAll()" style="background:#dc2626;color:#fff;border:none;border-radius:6px;padding:6px 16px;cursor:pointer;font-size:13px">' + t('contextSave') + '</button>'
    + '<button onclick="ctxRefresh()" style="background:#333;color:#ccc;border:none;border-radius:6px;padding:6px 16px;cursor:pointer;font-size:13px">' + t('contextCancel') + '</button>'
    + '</div>';
}

async function ctxSaveReplaceAll() {
  const ta = document.getElementById('ctx-replace-ta');
  if (!ta) return;
  let parsed;
  try { parsed = JSON.parse(ta.value); } catch (e) { addMsg('error', t('contextInvalidJson') + ': ' + e.message); return; }
  if (!Array.isArray(parsed)) { addMsg('error', t('contextInvalidJson') + ': expected array'); return; }
  if (!confirm(t('contextReplaceConfirm'))) return;
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'replace_context', conversation_id: conversationId, context: parsed }),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); return; }
    addMsg('system', t('contextSaved', { n: data.message_count, tokens: data.token_estimate }));
    _ctxFullData = null;
    ctxRefresh();
  } catch (e) { addMsg('error', 'Failed: ' + e.message); }
}

function showContextOverlay(data) {
  let overlay = document.getElementById('contextOverlay');
  if (overlay) overlay.remove();
  overlay = document.createElement('div');
  overlay.id = 'contextOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;z-index:9999';
  const statusBadge = data.diverged
    ? '<span style="background:#5a3e00;color:#f4a261;padding:2px 8px;border-radius:8px;font-size:11px;font-weight:600">' + t('contextDiverged') + '</span>'
    : '<span style="background:#1b4332;color:#52b788;padding:2px 8px;border-radius:8px;font-size:11px;font-weight:600">' + t('contextSynced') + '</span>';
  const roleColors = {system:'#6c6c8a',user:'#4fc3f7',assistant:'#4ecdc4',tool:'#f4a261'};
  let msgsHtml = '';
  if (!data.context || data.context.length === 0) {
    msgsHtml = '<div style="color:#6c6c8a;text-align:center;padding:20px">' + t('noContext') + '</div>';
  } else {
    data.context.forEach((m, i) => {
      const color = roleColors[m.role] || '#808090';
      const badge = '<span style="display:inline-block;background:' + color + '22;color:' + color + ';padding:1px 6px;border-radius:6px;font-size:11px;font-weight:600;margin-right:6px">' + m.role + '</span>';
      const tcTag = m.has_tool_calls ? '<span style="color:#f4a261;font-size:10px;margin-left:4px">[tool_calls]</span>' : '';
      const src = m.source ? '<span style="color:#808090;font-size:10px;margin-left:4px">[' + (m.source.name||'') + ']</span>' : '';
      const content = (m.content || '').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      const editBtn = '<button onclick="event.stopPropagation();ctxEditMessage(' + i + ')" style="background:none;border:none;color:#4fc3f7;cursor:pointer;font-size:13px;padding:0 3px" title="' + t('contextEdit') + '">&#9998;</button>';
      const delBtn = '<button onclick="event.stopPropagation();ctxDeleteMessage(' + i + ')" style="background:none;border:none;color:#e74c3c;cursor:pointer;font-size:13px;padding:0 3px" title="' + t('contextDelete') + '">&#128465;</button>';
      msgsHtml += '<div id="ctx-row-' + i + '" style="padding:6px 8px;border-bottom:1px solid #222;cursor:pointer" onclick="this.querySelector(\'.ctx-full\')&&(this.querySelector(\'.ctx-full\').style.display=this.querySelector(\'.ctx-full\').style.display===\'block\'?\'none\':\'block\')">'
        + '<div style="display:flex;align-items:center">' + badge + tcTag + src + '<span style="margin-left:auto">' + editBtn + delBtn + '</span></div>'
        + '<div style="color:#c0c0d0;font-size:12px;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">' + content.slice(0,200) + '</div>'
        + '<div class="ctx-full" style="display:none;color:#a0a0c0;font-size:12px;margin-top:4px;white-space:pre-wrap;word-break:break-word;max-height:300px;overflow-y:auto">' + content + '</div>'
        + '</div>';
    });
  }
  overlay.innerHTML = '<div style="background:#1a1a2e;border:1px solid #333;border-radius:12px;padding:20px;max-width:700px;width:90%;max-height:80vh;display:flex;flex-direction:column">'
    + '<div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">'
    + '<h3 style="margin:0;color:#e0e0e0;font-size:16px">' + t('contextTitle') + '</h3>'
    + statusBadge
    + '<span style="color:#6c6c8a;font-size:12px;margin-left:auto">' + t('contextMessages', {n:data.message_count}) + ' &middot; ' + t('contextTokens', {n:data.token_estimate}) + '</span>'
    + '<button onclick="ctxReplaceAll()" style="background:#1e3a5f;color:#4fc3f7;border:none;border-radius:6px;padding:3px 10px;cursor:pointer;font-size:11px;font-weight:600" title="' + t('contextReplaceAll') + '">JSON</button>'
    + '<button onclick="document.getElementById(\'contextOverlay\').remove()" style="background:none;border:none;color:#aaa;cursor:pointer;font-size:18px;margin-left:4px">&times;</button>'
    + '</div>'
    + '<div id="ctx-msg-list" style="flex:1;overflow-y:auto;border:1px solid #222;border-radius:8px;background:#0d1117">' + msgsHtml + '</div>'
    + '<div style="padding:8px 0 0 0;text-align:center">'
    + '<button onclick="ctxAddMessage()" style="background:#1e3a5f;color:#4fc3f7;border:none;border-radius:8px;padding:6px 18px;cursor:pointer;font-size:13px">+ ' + t('contextAdd') + '</button>'
    + '</div>'
    + '</div>';
  document.body.appendChild(overlay);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
}

// ── Secrets & Variables ──────────────────────────────────────────
async function cmdAddSecret(name, value) {
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'add_secret', key: name, value: value,
                             conversation_id: conversationId }),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); return; }
    addMsg('system', t('secretAdded', { name, ref: data.key || name, short: name }));
  } catch (e) { addMsg('error', 'Failed: ' + e.message); }
}

async function cmdListSecrets() {
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'list_secrets' }),
    });
    const data = await resp.json();
    const result = data.result || '';
    if (!result || result.includes('No secrets')) {
      addMsg('system', t('secretListEmpty'));
    } else {
      addMsg('system', result);
    }
  } catch (e) { addMsg('error', 'Failed: ' + e.message); }
}

async function cmdAddVariable(name, value) {
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'add_variable', key: name, value: value }),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); return; }
    addMsg('system', t('variableAdded', { name, ref: data.key || name, short: name }));
  } catch (e) { addMsg('error', 'Failed: ' + e.message); }
}

async function cmdListVariables() {
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'list_variables' }),
    });
    const data = await resp.json();
    const result = data.result || '';
    if (!result || result.includes('No variables')) {
      addMsg('system', t('variableListEmpty'));
    } else {
      addMsg('system', result);
    }
  } catch (e) { addMsg('error', 'Failed: ' + e.message); }
}

// ── Files panel ─────────────────────────────────────────────────
async function toggleFilesPanel() {
  const panel = document.getElementById('filesPanel');
  if (panel.style.display === 'none') {
    panel.style.display = 'block';
    await loadConvFiles();
  } else {
    panel.style.display = 'none';
  }
}

async function loadConvFiles() {
  if (!conversationId) return;
  const list = document.getElementById('filesList');
  list.innerHTML = '<span style="color:#808090;font-size:12px">Loading...</span>';
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'list_conv_files', conversation_id: conversationId }),
    });
    const data = await resp.json();
    const files = data.files || [];
    if (files.length === 0) {
      list.innerHTML = '<span style="color:#808090;font-size:12px">No files in this conversation.</span>';
      return;
    }
    list.innerHTML = files.map(f => {
      const statusCls = f.available ? 'available' : 'expired';
      const statusTip = f.available ? 'Available' : 'Expired/cleaned';
      const href = window.location.origin + '/files/' + f.file_id + '/' + f.filename;
      const nameHtml = f.available
        ? `<a href="${href}" target="_blank" title="Download">${escapeHtml(f.filename)}</a>`
        : `<span style="text-decoration:line-through;color:#808090" title="${statusTip}">${escapeHtml(f.filename)}</span>`;
      return `<span class="file-chip"><span class="file-status ${statusCls}" title="${statusTip}"></span>${nameHtml}</span>`;
    }).join('');
  } catch (e) {
    list.innerHTML = '<span style="color:#e94560;font-size:12px">Failed to load files</span>';
  }
}

// ── Flow context menu ──────────────────────────────────────────
function showFlowMenu(e, flowId, flowStatus) {
  e.preventDefault();
  closeFlowMenu();
  const menu = document.createElement('div');
  menu.className = 'ctx-menu';
  menu.id = 'flowCtxMenu';
  menu.style.left = e.clientX + 'px';
  menu.style.top = e.clientY + 'px';

  if (flowStatus === 'running') {
    menu.innerHTML = '<div class="ctx-menu-item" onclick="flowAction(\'' + flowId + '\', \'stop\')">&#x23F9; Stop</div>' +
      '<div class="ctx-menu-item danger" onclick="flowAction(\'' + flowId + '\', \'delete\')">&#x1F5D1; Delete</div>';
  } else {
    menu.innerHTML = '<div class="ctx-menu-item" onclick="flowAction(\'' + flowId + '\', \'start\')">&#x25B6; Start</div>' +
      '<div class="ctx-menu-item danger" onclick="flowAction(\'' + flowId + '\', \'delete\')">&#x1F5D1; Delete</div>';
  }
  document.body.appendChild(menu);
  setTimeout(() => document.addEventListener('click', closeFlowMenu, {once: true}), 0);
}

function closeFlowMenu() {
  const m = document.getElementById('flowCtxMenu');
  if (m) m.remove();
}

async function flowAction(flowId, action) {
  closeFlowMenu();
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({
        action: 'manage_conv_flow',
        conversation_id: conversationId,
        flow_id: flowId,
        flow_action: action,
      }),
    });
    const data = await resp.json();
    if (data.error) {
      addMsg('system', '\\u274C ' + data.error);
    } else {
      addMsg('system', '\\u2705 ' + (data.message || action + ' done'));
    }
    await loadConvFlows();
  } catch (e) {
    addMsg('error', 'Flow action failed: ' + e.message);
  }
}

// ── Scheduled Tasks panel ──────────────────────────────────────
async function toggleSchedsPanel() {
  const panel = document.getElementById('schedsPanel');
  if (panel.style.display === 'none') {
    panel.style.display = 'block';
    await loadConvScheds();
  } else {
    panel.style.display = 'none';
  }
}

async function loadConvScheds() {
  if (!conversationId) return;
  const list = document.getElementById('schedsList');
  list.innerHTML = '<span style="color:#808090;font-size:12px">Loading...</span>';
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'list_schedules', conversation_id: conversationId }),
    });
    const data = await resp.json();
    const scheds = data.schedules || [];
    if (scheds.length === 0) {
      list.innerHTML = '<span style="color:#808090;font-size:12px">No scheduled tasks.</span>';
      return;
    }
    list.innerHTML = scheds.map(s => {
      const at = new Date(s.recheck_at * 1000);
      const now = Date.now();
      const isPast = at.getTime() < now;
      const timeStr = at.toLocaleString();
      const relative = isPast ? 'overdue' : formatRelative(at.getTime() - now);
      const reason = s.reason ? escapeHtml(s.reason) : 'recheck';
      return '<span class="sched-chip">' +
        '<span class="sched-icon">&#x23F0;</span> ' +
        escapeHtml(reason) +
        ' <span style="color:#808090;font-size:11px">(' + timeStr + ', ' + relative + ')</span>' +
        '</span>';
    }).join('');
  } catch (e) {
    list.innerHTML = '<span style="color:#e94560;font-size:12px">Failed to load schedules</span>';
  }
}

function formatRelative(ms) {
  if (ms < 0) return 'overdue';
  const secs = Math.floor(ms / 1000);
  if (secs < 60) return secs + 's';
  const mins = Math.floor(secs / 60);
  if (mins < 60) return mins + 'min';
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return hrs + 'h ' + (mins % 60) + 'min';
  const days = Math.floor(hrs / 24);
  return days + 'd ' + (hrs % 24) + 'h';
}

// ── Flows panel ────────────────────────────────────────────────
async function toggleFlowsPanel() {
  const panel = document.getElementById('flowsPanel');
  if (panel.style.display === 'none') {
    panel.style.display = 'block';
    await loadConvFlows();
  } else {
    panel.style.display = 'none';
  }
}

async function loadConvFlows() {
  const list = document.getElementById('flowsList');
  list.innerHTML = '<span style="color:#808090;font-size:12px">Loading...</span>';
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'list_conv_flows' }),
    });
    const data = await resp.json();
    const flows = data.flows || [];
    if (flows.length === 0) {
      list.innerHTML = '<span style="color:#808090;font-size:12px">No flows deployed.</span>';
      return;
    }
    list.innerHTML = flows.map(f => {
      let statusCls = f.status || 'stopped';
      const statusTip = escapeHtml(f.status || 'stopped');
      const taskInfo = f.tasks_count ? f.tasks_count + ' task(s)' : '';
      const templateInfo = f.template ? ' from ' + escapeHtml(f.template) : '';
      const fid = escapeHtml(f.id);
      const fstatus = escapeHtml(f.status || 'stopped');
      return '<span class="flow-chip" data-flow-id="' + fid + '" data-flow-status="' + fstatus + '" ' +
        'oncontextmenu="showFlowMenu(event, \'' + fid + '\', \'' + fstatus + '\')">' +
        '<span class="flow-status ' + statusCls + '" title="' + statusTip + '"></span>' +
        escapeHtml(f.name || f.id) +
        (taskInfo ? ' <span style="color:#808090;font-size:11px">(' + taskInfo + templateInfo + ')</span>' : '') +
        '</span>';
    }).join('');
  } catch (e) {
    list.innerHTML = '<span style="color:#e94560;font-size:12px">Failed to load flows</span>';
  }
}

// File upload handling
function handleFiles(fileList) {
  const MAX_SIZE = 10 * 1024 * 1024; // 10MB per file
  for (const file of fileList) {
    if (file.size > MAX_SIZE) {
      addMsg('error', t('fileTooLarge', {name: file.name, size: (file.size / 1024 / 1024).toFixed(1)}));
      continue;
    }
    // .py files → offer to install as dynamic tool
    if (file.name.endsWith('.py')) {
      const textReader = new FileReader();
      textReader.onload = async (e) => {
        const source = e.target.result;
        addMsg('system', `Installing tool from ${file.name}...`);
        try {
          const resp = await fetch(API, {
            method: 'POST', headers: getAuthHeaders(),
            body: JSON.stringify({ action: 'install_tool', filename: file.name, source }),
          });
          const data = await resp.json();
          if (data.error) { addMsg('error', 'Install failed: ' + data.error); }
          else { addMsg('system', `Tool **${data.tool_name}** installed: ${data.description}`); }
        } catch (err) { addMsg('error', 'Install failed: ' + err.message); }
      };
      textReader.readAsText(file);
      continue;
    }
    const reader = new FileReader();
    reader.onload = (e) => {
      const dataUrl = e.target.result;
      const base64 = dataUrl.split(',')[1];
      const entry = {
        file: file,
        filename: file.name,
        mime_type: file.type || 'application/octet-stream',
        data: base64,
        dataUrl: dataUrl,
      };
      pendingFiles.push(entry);
      renderAttachments();
    };
    reader.readAsDataURL(file);
  }
  // Reset file input so same file can be re-selected
  document.getElementById('fileInput').value = '';
}

function removeFile(idx) {
  pendingFiles.splice(idx, 1);
  renderAttachments();
}

function renderAttachments() {
  const preview = document.getElementById('attachPreview');
  preview.innerHTML = '';
  pendingFiles.forEach((f, i) => {
    const el = document.createElement('div');
    el.className = 'att-item';
    const isImage = f.mime_type.startsWith('image/');
    if (isImage) {
      el.innerHTML = '<img src="' + f.dataUrl + '" alt="' + escapeHtml(f.filename) + '">';
    } else {
      const icons = {'application/pdf': '\u{1F4C4}', 'text/plain': '\u{1F4DD}', 'text/html': '\u{1F310}', 'text/markdown': '\u{1F4DD}'};
      el.innerHTML = '<span class="att-icon">' + (icons[f.mime_type] || '\u{1F4CE}') + '</span>';
    }
    el.innerHTML += '<span>' + escapeHtml(f.filename) + '</span>'
      + '<button class="att-remove" onclick="removeFile(' + i + ')">\u00d7</button>';
    preview.appendChild(el);
  });
}

function renderUserAttachments(attachments) {
  // Render attachment badges in user message
  let html = '';
  for (const att of attachments) {
    if (att.mime_type && att.mime_type.startsWith('image/')) {
      html += '<img class="chat-image" src="data:' + att.mime_type + ';base64,' + att.data + '">';
    } else {
      html += '<span class="doc-badge">\u{1F4CE} ' + escapeHtml(att.filename) + '</span> ';
    }
  }
  return html;
}

// Drag and drop support
document.addEventListener('DOMContentLoaded', () => {
  const main = document.querySelector('.main');
  main.addEventListener('dragover', (e) => { e.preventDefault(); e.stopPropagation(); });
  main.addEventListener('drop', (e) => {
    e.preventDefault(); e.stopPropagation();
    if (e.dataTransfer.files.length) handleFiles(e.dataTransfer.files);
  });
});

// Clipboard paste support (Ctrl+V images)
document.getElementById('input').addEventListener('paste', (e) => {
  const items = e.clipboardData && e.clipboardData.items;
  if (!items) return;
  for (const item of items) {
    if (item.type.startsWith('image/')) {
      e.preventDefault();
      const file = item.getAsFile();
      if (file) handleFiles([file]);
      return;
    }
  }
});

function copyMsg(btn) {
  const msg = btn.closest('.msg');
  if (!msg) return;
  // Get text content (strip action buttons text)
  const clone = msg.cloneNode(true);
  const actions = clone.querySelector('.msg-actions');
  if (actions) actions.remove();
  const text = clone.textContent || clone.innerText;
  navigator.clipboard.writeText(text).then(() => {
    btn.textContent = '\u2705';
    setTimeout(() => { btn.textContent = '\u{1F4CB}'; }, 1500);
  });
}

async function deleteMsg(btn) {
  const msg = btn.closest('.msg');
  if (!msg || !conversationId) return;
  const rawIdx = msg.dataset.rawIndex;
  if (rawIdx === undefined) {
    // No raw_index — message was added live (not from history), just remove from DOM
    msg.remove();
    return;
  }
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({
        action: 'delete_message', conversation_id: conversationId,
        index: parseInt(rawIdx),
      }),
      credentials: 'same-origin',
    });
    const data = await resp.json();
    if (data.deleted) {
      msg.style.transition = 'opacity 0.3s';
      msg.style.opacity = '0';
      setTimeout(() => msg.remove(), 300);
      if (data.message_count !== undefined) serverMsgCount = data.message_count;
    }
  } catch (e) {
    console.error('Delete message failed:', e);
  }
}

async function cancelAgent() {
  if (!conversationId) return;
  document.getElementById('stopBtn').style.display = 'none';
  document.getElementById('status').textContent = t('cancelling');
  try {
    await fetch(API, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'cancel', conversation_id: conversationId }),
      credentials: 'same-origin',
    });
  } catch (e) {
    console.warn('Cancel request failed:', e);
  }
  // SSE "cancelled" event will handle the rest
}

async function send() {
  const input = document.getElementById('input');
  const text = input.value.trim();
  if (!text && pendingFiles.length === 0) return;

  // Intercept slash commands
  if (text.startsWith('/')) {
    const handled = await handleSlashCommand(text);
    if (handled) { input.value = ''; input.style.height = 'auto'; return; }
  }

  // Capture and clear attachments
  const attachments = pendingFiles.map(f => ({
    filename: f.filename, mime_type: f.mime_type, data: f.data,
  }));
  const attachmentsForDisplay = [...pendingFiles];
  pendingFiles = [];
  renderAttachments();

  // Save to message history
  if (text) {
    messageHistory.unshift(text);
    if (messageHistory.length > 50) messageHistory.pop();
    localStorage.setItem('pyfi2_msg_history', JSON.stringify(messageHistory.slice(0, 50)));
  }
  historyIndex = -1;
  savedDraft = '';

  // Allow stacking: don't block on 'sending', just track pending count
  sending = true;
  lastSSEActivity = Date.now();
  document.getElementById('status').textContent = t('sending');
  input.value = '';
  input.style.height = 'auto';

  // Show user message with attachments
  const msgEl = addMsg('user', text || '');
  if (attachmentsForDisplay.length > 0) {
    msgEl.innerHTML = (text ? escapeHtml(text) : '') + renderUserAttachments(attachmentsForDisplay);
  }
  scrollBottom(true);  // Force scroll when user sends
  streamingEl = null;
  streamingText = '';
  streamingChunks = [];
  showTyping();

  try {
    const body = { message: text };
    if (conversationId) body.conversation_id = conversationId;
    if (attachments.length > 0) body.attachments = attachments;
    if (pendingAgent) { body.pending_agent = pendingAgent; pendingAgent = null; }
    const ttlVal = parseInt(document.getElementById('ttlSelect').value, 10);
    if (ttlVal > 0) body.ttl = ttlVal;

    let resp;
    const jsonBody = JSON.stringify(body);
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        resp = await fetch(API, {
          method: 'POST',
          headers: getAuthHeaders(),
          body: jsonBody,
          credentials: 'same-origin',
          redirect: 'manual',
        });
        break;  // success
      } catch (fetchErr) {
        if (attempt < 2) {
          console.warn('Fetch attempt ' + (attempt+1) + ' failed, retrying...', fetchErr);
          await new Promise(r => setTimeout(r, 500));
        } else {
          throw fetchErr;
        }
      }
    }

    // Session expired → 401 JSON or opaque redirect (302 to OAuth)
    if (resp.type === 'opaqueredirect' || resp.status === 401 || resp.status === 403) {
      hideTyping();
      if (LOGIN_URL) { window.location.href = LOGIN_URL; return; }
      addMsg('error', t('sessionExpired'));
      sending = false;
      document.getElementById('status').textContent = t('ready');
      return;
    }

    if (!resp.ok) {
      hideTyping();
      const errText = await resp.text();
      addMsg('error', 'Error ' + resp.status + ': ' + errText);
      sending = false;
      document.getElementById('status').textContent = t('error');
      return;
    }

    const data = await resp.json();
    const cid = data.conversation_id || conversationId;
    if (cid && cid !== conversationId) {
      conversationId = cid;
      // Sync message count from server to prevent poll from re-fetching the user message
      serverMsgCount = data.message_count || 1;
      connectSSE(cid);  // Start/reconnect SSE for this conversation
      startPollTimer();
      updateDeleteBtn();
      loadConversations();  // Show new conversation in sidebar immediately
    }

    // If streaming mode: events come via SSE, don't show response here
    if (data.status === 'accepted') {
      if (data.message_count) serverMsgCount = data.message_count;
      document.getElementById('status').textContent = t('thinking');
      document.getElementById('stopBtn').style.display = '';
      // SSE will handle the rest
      return;
    }

    // Non-streaming mode: show response directly
    hideTyping();
    conversationId = data.conversation_id || conversationId;
    const nsExtra = data.source ? { source: data.source } : undefined;
    addMsg('assistant', data.response || data.content || JSON.stringify(data), nsExtra);
    sending = false;
    document.getElementById('status').textContent = t('ready');
    loadConversations();
    loadResources();

  } catch (e) {
    hideTyping();
    console.error('send() failed:', e);
    addMsg('error', t('connError', {msg: e.message + ' (check console)'}));
    sending = false;
    document.getElementById('status').textContent = t('error');
  }
}

function handleKey(e) {
  const input = e.target;
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    send();
    return;
  }
  // Arrow up: navigate message history (only when cursor is at position 0)
  if (e.key === 'ArrowUp' && input.selectionStart === 0 && messageHistory.length > 0) {
    e.preventDefault();
    if (historyIndex === -1) savedDraft = input.value;
    if (historyIndex < messageHistory.length - 1) {
      historyIndex++;
      input.value = messageHistory[historyIndex];
      input.setSelectionRange(0, 0);
    }
    return;
  }
  // Arrow down: navigate back toward current draft
  if (e.key === 'ArrowDown' && historyIndex >= 0) {
    e.preventDefault();
    historyIndex--;
    if (historyIndex < 0) {
      input.value = savedDraft;
    } else {
      input.value = messageHistory[historyIndex];
    }
    input.setSelectionRange(input.value.length, input.value.length);
    return;
  }
  setTimeout(() => {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 120) + 'px';
  }, 0);
}

// ── Resources (agents, skills, mcp) ─────────────────────────────
async function cmdResourceAction(action, extra) {
  try {
    const payload = { action, conversation_id: conversationId, ...extra };
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify(payload),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); return; }
    if (data.created) addMsg('system', `Created: ${extra.name || ''}`);
    else if (data.deleted) addMsg('system', `Deleted: ${extra.name || ''}`);
    else if (data.activated) addMsg('system', `Activated ${data.type} "${data.name}" in this conversation`);
    else if (data.deactivated) addMsg('system', `Deactivated ${data.type} "${data.name}"`);
    else if (data.shared) addMsg('system', `Shared ${data.type} "${data.name}" to conversation ${data.target.substring(0,8)}...`);
    else addMsg('system', JSON.stringify(data, null, 2));
  } catch (e) { addMsg('error', 'Failed: ' + e.message); }
}

async function cmdServiceList() {
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'service_list', conversation_id: conversationId }),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); return; }
    const svcs = data.services || [];
    if (!svcs.length) { addMsg('system', 'No services installed. Use /service install <type> <name> [key=val,...] to add one.'); return; }
    let lines = ['**Your services:**'];
    svcs.forEach(s => {
      const icon = s.connected ? '\u{1F7E2}' : (s.enabled ? '\u{1F534}' : '\u26AB');
      lines.push(`  ${icon} **${s.id}** (\`${s.type}\`) ${s.description || ''}`);
    });
    addMsg('system', lines.join('\n'));
  } catch (e) { addMsg('error', 'Failed: ' + e.message); }
}

async function cmdServiceAction(action, extra) {
  try {
    const payload = { action, conversation_id: conversationId, ...extra };
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify(payload),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); return; }
    if (data.installed) addMsg('system', `Service '${data.id}' installed (${data.type}).`);
    else if (data.uninstalled) addMsg('system', `Service '${data.id}' uninstalled.`);
    else if (data.enabled) addMsg('system', `Service '${data.id}' enabled.`);
    else if (data.disabled) addMsg('system', `Service '${data.id}' disabled.`);
    else addMsg('system', JSON.stringify(data, null, 2));
  } catch (e) { addMsg('error', 'Failed: ' + e.message); }
}

async function cmdSkillList() {
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'list_skills', conversation_id: conversationId }),
    });
    const data = await resp.json();
    const skills = data.skills || [];
    if (!skills.length) { addMsg('system', 'No skills defined. Use /add-skill <name> <prompt>'); return; }
    let lines = ['**Your skills:**'];
    skills.forEach(s => {
      const mark = s.active ? '\\u2705' : '\\u2B1C';
      lines.push(`${mark} **${s.name}** — ${s.description || s.prompt}`);
    });
    addMsg('system', lines.join('\\n'));
  } catch (e) { addMsg('error', 'Failed: ' + e.message); }
}

async function cmdListResources() {
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'list_resources', conversation_id: conversationId }),
    });
    const data = await resp.json();
    let lines = [];
    if (data.agents && data.agents.length) {
      lines.push('**Agents:**');
      data.agents.forEach(a => {
        const mark = a.active ? '\\u2705' : '\\u2B1C';
        lines.push(`  ${mark} ${a.name} ${a.description ? '— ' + a.description : ''}`);
      });
    }
    if (data.skills && data.skills.length) {
      lines.push('**Skills:**');
      data.skills.forEach(s => {
        const mark = s.active ? '\\u2705' : '\\u2B1C';
        lines.push(`  ${mark} ${s.name} ${s.description ? '— ' + s.description : ''}`);
      });
    }
    if (data.mcp_servers && data.mcp_servers.length) {
      lines.push('**MCP Servers:**');
      data.mcp_servers.forEach(m => {
        const mark = m.active ? '\\u2705' : '\\u2B1C';
        lines.push(`  ${mark} ${m.name} (${m.url})`);
      });
    }
    if (!lines.length) lines.push('No resources defined. Use /agent create, /add-skill, etc.');
    addMsg('system', lines.join('\\n'));
  } catch (e) { addMsg('error', 'Failed: ' + e.message); }
}

// ── Sidebar Resources ───────────────────────────────────────────
async function loadResources() {
  if (!conversationId) { document.getElementById('resourcesPanel').style.display = 'none'; return; }
  document.getElementById('resourcesPanel').style.display = 'block';
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'list_resources', conversation_id: conversationId }),
    });
    const data = await resp.json();
    const el = document.getElementById('resourcesContent');
    let html = '';
    // Agents
    if (data.agents && data.agents.length) {
      html += '<div style="margin-bottom:4px;color:#6c5ce7;font-weight:600;">Agents</div>';
      data.agents.forEach(a => {
        const active = a.active;
        html += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;">
          <span style="cursor:pointer;font-size:11px;" onclick="cmdResourceAction('${active ? 'deactivate_resource' : 'activate_resource'}',{resource_type:'agent',name:'${a.name}'}).then(loadResources)">${active ? '\u2705' : '\u2B1C'}</span>
          <span style="color:${active ? '#e0e0e0' : '#666'};font-size:12px;">${a.name}</span>
        </div>`;
      });
    }
    // Skills
    if (data.skills && data.skills.length) {
      html += '<div style="margin-bottom:4px;color:#6c5ce7;font-weight:600;">Skills</div>';
      data.skills.forEach(s => {
        const active = s.active;
        html += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;">
          <span style="cursor:pointer;font-size:11px;" onclick="cmdResourceAction('${active ? 'deactivate_resource' : 'activate_resource'}',{resource_type:'skill',name:'${s.name}'}).then(loadResources)">${active ? '\u2705' : '\u2B1C'}</span>
          <span style="color:${active ? '#e0e0e0' : '#666'};font-size:12px;">${s.name}</span>
        </div>`;
      });
    }
    // MCP
    if (data.mcp_servers && data.mcp_servers.length) {
      html += '<div style="margin-bottom:4px;color:#6c5ce7;font-weight:600;">MCP</div>';
      data.mcp_servers.forEach(m => {
        const active = m.active;
        html += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;">
          <span style="cursor:pointer;font-size:11px;" onclick="cmdResourceAction('${active ? 'deactivate_resource' : 'activate_resource'}',{resource_type:'mcp',name:'${m.name}'}).then(loadResources)">${active ? '\u2705' : '\u2B1C'}</span>
          <span style="color:${active ? '#e0e0e0' : '#666'};font-size:12px;">${m.name}</span>
        </div>`;
      });
    }
    if (!html) html = '<div style="color:#555;font-size:11px;">No resources. Use /agent create, /add-skill</div>';
    el.innerHTML = html;
  } catch (e) {
    document.getElementById('resourcesContent').innerHTML = '';
  }
}

function toggleResourcesSection() {
  const el = document.getElementById('resourcesContent');
  el.style.display = el.style.display === 'none' ? 'block' : 'none';
}

// ── File Viewer ─────────────────────────────────────────────────
function openFileViewer(filenameOrUrl) {
  let viewer = document.getElementById('fileViewer');
  if (!viewer) {
    viewer = document.createElement('div');
    viewer.id = 'fileViewer';
    viewer.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:9999;background:#1e1e2e;border-bottom:2px solid #6c5ce7;max-height:50vh;display:flex;flex-direction:column;';
    viewer.innerHTML = `
      <div style="display:flex;align-items:center;padding:8px 16px;gap:12px;background:#2d2d44;">
        <span id="viewerFileName" style="flex:1;color:#ccc;font-size:14px;"></span>
        <span id="viewerFileSize" style="color:#888;font-size:12px;"></span>
        <a id="viewerDownload" download style="color:#6c5ce7;text-decoration:none;font-size:14px;cursor:pointer;">\\u2B07 Download</a>
        <button onclick="closeFileViewer()" style="background:none;border:none;color:#ff6b6b;font-size:18px;cursor:pointer;">\\u2715</button>
      </div>
      <div id="viewerContent" style="flex:1;overflow:auto;padding:16px;"></div>
    `;
    document.body.prepend(viewer);
  }
  viewer.style.display = 'flex';
  const contentEl = document.getElementById('viewerContent');
  const nameEl = document.getElementById('viewerFileName');
  const sizeEl = document.getElementById('viewerFileSize');
  const dlEl = document.getElementById('viewerDownload');

  // Determine if it's a URL or filename
  let url = filenameOrUrl;
  if (!filenameOrUrl.startsWith('http')) {
    // Search in conversation files
    url = API.replace(/\/[^\/]*$/, '') + '/files/' + encodeURIComponent(filenameOrUrl);
  }
  const fname = filenameOrUrl.split('/').pop();
  const ext = fname.split('.').pop().toLowerCase();
  nameEl.textContent = fname;
  dlEl.href = url;
  dlEl.download = fname;

  if (['png','jpg','jpeg','gif','svg','webp','bmp'].includes(ext)) {
    contentEl.innerHTML = `<img src="${url}" style="max-width:100%;max-height:40vh;object-fit:contain;">`;
  } else if (ext === 'pdf') {
    contentEl.innerHTML = `<iframe src="${url}" style="width:100%;height:40vh;border:none;"></iframe>`;
  } else if (ext === 'html') {
    contentEl.innerHTML = `<iframe src="${url}" sandbox="allow-same-origin" style="width:100%;height:40vh;border:none;background:#fff;"></iframe>`;
  } else {
    // Text/code: fetch and display
    fetch(url).then(r => r.text()).then(text => {
      sizeEl.textContent = (text.length / 1024).toFixed(1) + ' KB';
      contentEl.innerHTML = `<pre style="margin:0;white-space:pre-wrap;word-break:break-all;color:#ddd;font-size:13px;font-family:monospace;">${text.replace(/</g,'&lt;').replace(/>/g,'&gt;')}</pre>`;
    }).catch(() => {
      contentEl.innerHTML = '<p style="color:#ff6b6b;">Could not load file preview.</p>';
    });
    return;
  }
  sizeEl.textContent = '';
}

function closeFileViewer() {
  const v = document.getElementById('fileViewer');
  if (v) v.style.display = 'none';
}

// Intercept file links in messages to open viewer
document.addEventListener('click', (e) => {
  const a = e.target.closest('a[href*="/files/"]');
  if (a) {
    e.preventDefault();
    openFileViewer(a.href);
  }
});

addMsg('system', t('welcome'));
document.getElementById('input').focus();
loadConversations();
</script>
</body>
</html>"""


class ServeChatUITask(BaseTask):
    """Serve a self-contained chat HTML interface."""

    TYPE = "serveChatUI"
    VERSION = "1.0.0"
    NAME = "Serve Chat UI"
    DESCRIPTION = "Serve an HTML chat interface for the agent"
    ICON = "chat"

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "agent_path": {
                "type": "string",
                "required": False,
                "default": "/api/agent",
                "description": "Path of the agent POST endpoint (for the chat JS to call)",
            },
            "login_url": {
                "type": "string",
                "required": False,
                "default": "",
                "description": "Login URL for OAuth2 redirect (empty = no auth required)",
            },
            "sse_path": {
                "type": "string",
                "required": False,
                "default": "/api/agent/events",
                "description": "Path of the SSE events endpoint",
            },
        }

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        agent_path = self.config.get("agent_path", "/api/agent")
        login_url = self.config.get("login_url", "")
        sse_path = self.config.get("sse_path", "/api/agent/events")
        html = _CHAT_HTML.replace("{{AGENT_PATH}}", agent_path)
        html = html.replace("{{LOGIN_URL}}", login_url)
        html = html.replace("{{SSE_PATH}}", sse_path)

        flowfile.set_content(html.encode("utf-8"))
        flowfile.set_attribute("http.response.status", "200")
        flowfile.set_attribute("http.response.header.Content-Type", "text/html; charset=utf-8")
        flowfile.set_attribute("http.response.header.Cache-Control", "no-cache")

        return [flowfile]


TaskFactory.register(ServeChatUITask)

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
.active-agent-badge { font-size: 11px; padding: 2px 10px; border-radius: 12px; cursor: pointer; margin-left: 8px; font-weight: 600; white-space: nowrap; }
.active-agent-badge:hover { filter: brightness(1.3); }
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
.msg.user { align-self: flex-end; background: #0f3460; color: white; border-bottom-right-radius: 4px;
            border-left: 3px solid #4ecdc4; }
.source-badge { display: inline-block; font-size: 10px; padding: 1px 6px; border-radius: 8px; margin-right: 4px; vertical-align: middle; font-weight: 600; letter-spacing: 0.3px; }
.msg-time { float: right; font-size: 10px; color: #555570; margin-left: 8px; font-weight: normal; }
.msg.assistant { align-self: flex-start; background: #16213e; border: 1px solid #0f3460;
                  border-left: 3px solid #e94560; border-bottom-left-radius: 4px; }
.msg.subagent { align-self: flex-start; background: #0d1b2a; border: 1px solid #1a3a5c;
                border-left: 3px solid #6c5ce7; border-bottom-left-radius: 4px; }
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
.active-panel { display: none; background: rgba(15,22,41,0.9); border: 1px solid #0f3460; border-radius: 8px;
                padding: 6px 10px; font-size: 11px; color: #a0a0c0;
                position: fixed; bottom: 70px; right: 20px; z-index: 50; max-width: 350px;
                max-height: 150px; overflow-y: auto; backdrop-filter: blur(4px); }
.active-panel.visible { display: block; }
.active-panel-title { font-size: 10px; color: #6c6c8a; margin-bottom: 2px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }
.active-row { display: flex; align-items: center; gap: 6px; padding: 2px 0; }
.active-row .a-spinner { animation: spin 1.2s linear infinite; font-style: normal; font-size: 10px; }
.active-row .a-name { font-weight: 600; color: #e0e0f0; }
.active-row .a-msg { display: none; }
.active-row .a-status { color: #4ecdc4; font-size: 10px; }
.active-row .a-time { color: #808090; font-size: 10px; }
.active-row .a-actions { display: flex; gap: 2px; }
.active-row .a-actions button { background: none; border: 1px solid #333; color: #aaa; cursor: pointer;
                                 border-radius: 3px; font-size: 10px; padding: 0 4px; line-height: 1.4; }
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
.scroll-nav { position: fixed; right: 24px; bottom: 75px; display: flex; flex-direction: column; z-index: 51;
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
  <span class="active-agent-badge" id="activeAgentBadge" onclick="cmdAgentSelect('')" style="display:none" title="Click to switch back to assistant"></span>
  <div class="actions">
    <span class="user-info" id="userInfo"></span>
    <button class="btn" id="schedsBtn" onclick="toggleSchedsPanel()" style="display:none" title="Scheduled tasks">&#x23F0;</button>
    <button class="btn" id="flowsBtn" onclick="toggleFlowsPanel()" title="My Flows">&#x26A1;</button>
    <button class="btn" id="filesBtn" onclick="toggleFilesPanel()" style="display:none" title="Conversation files">&#x1F4C4;</button>
    <button class="btn" id="contextBtn" onclick="cmdShowContext()" style="display:none" title="View LLM context">&#x1F441;</button>
    <button class="btn" id="memoryBtn" onclick="cmdShowMemories()" style="display:none" title="View agent memories">&#x1F9E0;</button>
    <button class="btn" id="exportConvBtn" onclick="exportConversation()" style="display:none" title="Export conversation">&#x1F4E5;</button>
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
    <button onclick="document.getElementById('messages').scrollTop=0;document.getElementById('input').focus()" title="Scroll to top">&#x2191;</button>
    <button onclick="scrollBottom(true);document.getElementById('input').focus()" title="Scroll to bottom">&#x2193;</button>
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
    rebuildingClean: 'Setting context = full conversation (no compaction)...', rebuiltClean: 'Context set to full conversation: {messages} messages, ~{tokens} tokens.',
    restartEmpty: 'Context cleared — fresh start (system prompt only).',
    contextOpBusy: 'A context operation is in progress, please wait...',
    contextTitle: 'LLM Context', contextDiverged: 'diverged', contextSynced: 'synced',
    contextTokens: '~{n} tokens', contextMessages: '{n} messages', noContext: 'No context available.',
    contextEdit: 'Edit', contextDelete: 'Delete', contextAdd: 'Add message',
    contextReplaceAll: 'Replace all (JSON)', contextSave: 'Save', contextCancel: 'Cancel',
    contextDeleteConfirm: 'Delete this message?', contextReplaceConfirm: 'Replace entire context?',
    contextSaved: 'Context saved ({n} messages, ~{tokens} tokens)', contextInvalidJson: 'Invalid JSON',
    contextRole: 'Role', contextContent: 'Content',
    thoughtEnabled: 'Auto-conversation enabled for {agent}: {freq} (next in ~{delay}s)',
    thoughtDisabled: 'Auto-conversation disabled for {agent}.',
    thoughtStatus: 'Auto-conversation for {agent}: enabled — {freq}, next in ~{delay}s',
    thoughtStatusOff: 'Auto-conversation for {agent}: disabled',
    thoughtTriggered: 'Auto-conversation triggered for {agent}.',
    thoughtNoConv: 'No active conversation.',
    thoughtScheduled: '[{agent}] next auto-message in ~{delay}s',
    thoughtFiring: '[{agent}] thinking...',
    iterStatus: '[{agent}] iter {i} \u00b7 round {r}/{mr} \u00b7 {t} tools',
    subAgentStarted: 'Sub-agent [{agent}] started',
    subAgentDone: '[{agent}] finished ({dur}s, {tok} tokens)',
    iterProgress: '\u21bb [{agent}] iter {i} \u00b7 round {r}/{mr} \u00b7 {t} tools',
    agentRenamed: 'Agent "{real}" will now display as "{nick}".',
    confirmDelete: 'Delete this conversation? This cannot be undone.',
    exporting: 'Exporting...', exportingWithImages: 'Exporting with images...',
    exported: 'Conversation exported.',
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
    rebuildingClean: 'Contexte = conversation compl\u00e8te (sans compaction)...', rebuiltClean: 'Contexte mis \u00e0 la conversation compl\u00e8te\u00a0: {messages} messages, ~{tokens} tokens.',
    restartEmpty: 'Contexte vid\u00e9 \u2014 red\u00e9marrage \u00e0 z\u00e9ro (system prompt uniquement).',
    contextOpBusy: 'Op\u00e9ration de contexte en cours, veuillez patienter...',
    contextTitle: 'Contexte LLM', contextDiverged: 'diverg\u00e9', contextSynced: 'synchronis\u00e9',
    contextTokens: '~{n} tokens', contextMessages: '{n} messages', noContext: 'Aucun contexte disponible.',
    contextEdit: 'Modifier', contextDelete: 'Supprimer', contextAdd: 'Ajouter un message',
    contextReplaceAll: 'Remplacer tout (JSON)', contextSave: 'Enregistrer', contextCancel: 'Annuler',
    contextDeleteConfirm: 'Supprimer ce message ?', contextReplaceConfirm: 'Remplacer tout le contexte ?',
    contextSaved: 'Contexte sauvegardé ({n} messages, ~{tokens} tokens)', contextInvalidJson: 'JSON invalide',
    contextRole: 'Rôle', contextContent: 'Contenu',
    thoughtEnabled: 'Auto-conversation activ\u00e9e pour {agent}\u00a0: {freq} (prochaine dans ~{delay}s)',
    thoughtDisabled: 'Auto-conversation d\u00e9sactiv\u00e9e pour {agent}.',
    thoughtStatus: 'Auto-conversation pour {agent}\u00a0: activ\u00e9e \u2014 {freq}, prochaine dans ~{delay}s',
    thoughtStatusOff: 'Auto-conversation pour {agent}\u00a0: d\u00e9sactiv\u00e9e',
    thoughtTriggered: 'Auto-conversation d\u00e9clench\u00e9e pour {agent}.',
    thoughtNoConv: 'Aucune conversation active.',
    thoughtScheduled: '[{agent}] prochain message auto dans ~{delay}s',
    thoughtFiring: '[{agent}] r\u00e9fl\u00e9chit...',
    iterStatus: '[{agent}] iter {i} \u00b7 tour {r}/{mr} \u00b7 {t} outils',
    subAgentStarted: 'Sous-agent [{agent}] d\u00e9marr\u00e9',
    subAgentDone: '[{agent}] termin\u00e9 ({dur}s, {tok} tokens)',
    iterProgress: '\u21bb [{agent}] iter {i} \u00b7 tour {r}/{mr} \u00b7 {t} outils',
    agentRenamed: 'L\'agent "{real}" s\'affichera d\u00e9sormais comme "{nick}".',
    confirmDelete: 'Supprimer cette conversation\u00a0? Cette action est irr\u00e9versible.',
    exporting: 'Export en cours...', exportingWithImages: 'Export avec images en cours...',
    exported: 'Conversation export\u00e9e.',
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
    rebuildingClean: 'Contexto = conversaci\u00f3n completa (sin compactaci\u00f3n)...', rebuiltClean: 'Contexto con conversaci\u00f3n completa: {messages} mensajes, ~{tokens} tokens.',
    restartEmpty: 'Contexto vaciado \u2014 inicio limpio (solo system prompt).',
    contextOpBusy: 'Operaci\u00f3n de contexto en curso, por favor espere...',
    contextTitle: 'Contexto LLM', contextDiverged: 'divergido', contextSynced: 'sincronizado',
    contextTokens: '~{n} tokens', contextMessages: '{n} mensajes', noContext: 'Sin contexto disponible.',
    contextEdit: 'Editar', contextDelete: 'Eliminar', contextAdd: 'Añadir mensaje',
    contextReplaceAll: 'Reemplazar todo (JSON)', contextSave: 'Guardar', contextCancel: 'Cancelar',
    contextDeleteConfirm: '¿Eliminar este mensaje?', contextReplaceConfirm: '¿Reemplazar todo el contexto?',
    contextSaved: 'Contexto guardado ({n} mensajes, ~{tokens} tokens)', contextInvalidJson: 'JSON inválido',
    contextRole: 'Rol', contextContent: 'Contenido',
    thoughtEnabled: 'Auto-conversaci\u00f3n activada para {agent}: {freq} (pr\u00f3ximo en ~{delay}s)',
    thoughtDisabled: 'Auto-conversaci\u00f3n desactivada para {agent}.',
    thoughtStatus: 'Auto-conversaci\u00f3n para {agent}: activada \u2014 {freq}, pr\u00f3ximo en ~{delay}s',
    thoughtStatusOff: 'Auto-conversaci\u00f3n para {agent}: desactivada',
    thoughtTriggered: 'Auto-conversaci\u00f3n activada para {agent}.',
    thoughtNoConv: 'No hay conversaci\u00f3n activa.',
    thoughtScheduled: '[{agent}] pr\u00f3ximo mensaje auto en ~{delay}s',
    thoughtFiring: '[{agent}] pensando...',
    iterStatus: '[{agent}] iter {i} \u00b7 ronda {r}/{mr} \u00b7 {t} herram.',
    subAgentStarted: 'Sub-agente [{agent}] iniciado',
    subAgentDone: '[{agent}] terminado ({dur}s, {tok} tokens)',
    iterProgress: '\u21bb [{agent}] iter {i} \u00b7 ronda {r}/{mr} \u00b7 {t} herram.',
    agentRenamed: 'El agente "{real}" se mostrar\u00e1 como "{nick}".',
    confirmDelete: '\u00bfEliminar esta conversaci\u00f3n? No se puede deshacer.',
    exporting: 'Exportando...', exportingWithImages: 'Exportando con im\u00e1genes...',
    exported: 'Conversaci\u00f3n exportada.',
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
let contextOpInProgress = false;  // true while rebuild/resume/compact/restart_from is running
let eventSource = null;
let pendingAgent = null;  // agent to select when first message creates a conversation
let selectedAgent = '';   // currently active agent ('' or 'assistant' = default)
let sseRetryCount = 0;     // for exponential backoff on reconnect
let sseReconnectTimer = null;
// Per-agent streaming state — prevents cross-agent clobbering when multiple
// agents (random thoughts, sub-agents) stream concurrently.
let streams = {};  // agentName → { el, text, chunks }
// Legacy aliases for backward compat with code that reads these globals
let streamingEl = null;
let streamingText = '';
let streamingChunks = [];
let streamingAgent = '';

function getStream(agent) {
  const key = (agent || 'assistant').toLowerCase();
  if (!streams[key]) streams[key] = { el: null, text: '', chunks: [] };
  return streams[key];
}
function clearStream(agent) {
  const key = (agent || 'assistant').toLowerCase();
  delete streams[key];
  // Sync legacy globals if this was the active stream
  if (!streamingAgent || streamingAgent.toLowerCase() === key) {
    streamingEl = null; streamingText = ''; streamingChunks = []; streamingAgent = '';
  }
}
function clearAllStreams() {
  for (const a of Object.keys(streams)) {
    const s = streams[a];
    for (const c of s.chunks) { if (c && c.parentNode) c.remove(); }
  }
  streams = {};
  streamingEl = null; streamingText = ''; streamingChunks = []; streamingAgent = '';
}
let nicknameMap = {};      // { realName: displayName } — agent display names
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
  selectedAgent = '';
  updateActiveAgentBadge();
  serverMsgCount = 0;
  clearAllStreams();
  sending = false;
  document.getElementById('sendBtn').disabled = false;
  document.getElementById('messages').innerHTML = '';
  addMsg('system', t('newConv'));
  document.getElementById('status').textContent = t('ready');
  document.getElementById('deleteConvBtn').style.display = 'none';
  document.getElementById('exportConvBtn').style.display = 'none';
  document.getElementById('contextBtn').style.display = 'none';
  document.getElementById('memoryBtn').style.display = '';
  document.getElementById('filesBtn').style.display = 'none';
  document.getElementById('filesPanel').style.display = 'none';
  document.getElementById('flowsPanel').style.display = 'none';
  document.getElementById('schedsBtn').style.display = 'none';
  document.getElementById('schedsPanel').style.display = 'none';
  highlightConv(null);
  // Close sidebar on mobile
  document.getElementById('sidebar').classList.remove('open');
  document.getElementById('input').focus();
}

function updateDeleteBtn() {
  const show = conversationId ? '' : 'none';
  document.getElementById('deleteConvBtn').style.display = show;
  document.getElementById('exportConvBtn').style.display = show;
  document.getElementById('contextBtn').style.display = show;
  document.getElementById('memoryBtn').style.display = '';
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
    clearAllStreams();
    sending = false;
    document.getElementById('sendBtn').disabled = false;
    document.getElementById('messages').innerHTML = '';
    // Load nicknames BEFORE replay so displayAgentName() works on old messages
    nicknameMap = data.nicknames || {};
    // Replay messages (using classified types: user/assistant/tool_call/tool_result)
    for (const m of (data.messages || [])) {
      let content = m.content || '';
      // Strip identity prefixes that may have been persisted (e.g. "[AgentName]: text")
      if ((m.type === 'assistant' || m.role === 'assistant') && typeof content === 'string') {
        content = content.replace(/^\[[^\]]+\]:\s*/, '');
      }
      addMsg(m.type || m.role, content, m);
    }
    serverMsgCount = data.message_count || 0;
    selectedAgent = data.active_agent || '';
    updateActiveAgentBadge();
    highlightConv(cid);
    connectSSE(cid);  // subscribe to SSE — will pick up events if agent is still running
    startPollTimer();
    updateDeleteBtn();
    loadResources();
    document.getElementById('status').textContent = t('ready');
    document.getElementById('sidebar').classList.remove('open');
    scrollBottom(true);
    document.getElementById('input').focus();
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
    clearAllStreams();
    hideTyping();

    // Display the new messages, skipping messages already shown locally
    const msgContainer = document.getElementById('messages');
    for (const m of newMsgs) {
      const mType = m.type || m.role;
      if (mType === 'user') {
        // Check if this user message is already displayed (sent locally by send())
        const existing = msgContainer.querySelectorAll('.msg.user');
        const lastUserEl = existing.length > 0 ? existing[existing.length - 1] : null;
        if (lastUserEl) {
          // Compare raw text (without badges/prefixes) for dedup
          const stripPrefix = (s) => s.replace(/^\[(?:btw\s*)?(?:\u2192\s+\w+)?\]\s*/, '');
          const localRaw = stripPrefix(lastUserEl.dataset.rawText || lastUserEl.textContent.trim());
          const serverRaw = stripPrefix((m.content || '').trim());
          if (localRaw === serverRaw) {
            console.log('[poll] skipping duplicate user message');
            continue;
          }
        }
      }
      if (mType === 'assistant') {
        // Skip btw messages that were already shown via btw_done SSE event
        if (m.source && m.source.btw) {
          console.log('[poll] skipping btw message (already shown via btw_done)');
          continue;
        }
        // Check if this assistant message was already shown via SSE done event
        const existing = msgContainer.querySelectorAll('.msg.assistant, .msg.subagent');
        const lastEl = existing.length > 0 ? existing[existing.length - 1] : null;
        if (lastEl && lastEl.dataset.rawText) {
          const newText = (m.content || '').replace(/^\[[^\]]+\]:\s*/, '').substring(0, 500);
          if (lastEl.dataset.rawText === newText) {
            console.log('[poll] skipping duplicate assistant message');
            continue;
          }
        }
      }
      let pollContent = m.content || '';
      // Strip identity prefixes (same as history replay)
      if (mType === 'assistant' && typeof pollContent === 'string') {
        pollContent = pollContent.replace(/^\[[^\]]+\]:\s*/, '');
      }
      addMsg(mType, pollContent, m);
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
  if (!confirm(t('confirmDelete'))) return;
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

async function exportConversation() {
  if (!conversationId) return;
  document.getElementById('status').textContent = t('exporting');
  try {
    // Fetch conversation messages
    const resp = await fetch(API, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'load_history', conversation_id: conversationId }),
      credentials: 'same-origin',
    });
    if (!resp.ok) { addMsg('error', 'Export failed'); return; }
    const data = await resp.json();
    const messages = data.messages || [];

    // Collect file URLs from messages
    const fileUrls = [];
    const fileUrlRe = /(https?:\/\/[^\s<"']*\/files\/[a-f0-9]+\/([^\s<"')]+))/g;
    for (const m of messages) {
      const content = m.content || '';
      let match;
      while ((match = fileUrlRe.exec(content)) !== null) {
        fileUrls.push({ url: match[1], name: match[2] });
      }
      fileUrlRe.lastIndex = 0;
    }

    // Check if we have images — if so, create a ZIP
    const hasImages = fileUrls.some(f => isImageFile(f.name));

    // Build HTML
    const htmlContent = buildExportHtml(messages, data.nicknames || {}, fileUrls);

    if (hasImages) {
      // Use JSZip-like approach: simple ZIP with stored entries
      addMsg('system', t('exportingWithImages'));
      const files = [{ name: 'conversation.html', content: new TextEncoder().encode(htmlContent) }];
      // Fetch images
      const token = getToken();
      const headers = {};
      if (token) headers['Authorization'] = 'Bearer ' + token;
      for (const f of fileUrls) {
        if (isImageFile(f.name)) {
          try {
            const imgResp = await fetch(f.url, { headers, credentials: 'same-origin' });
            if (imgResp.ok) {
              const blob = await imgResp.blob();
              const buf = await blob.arrayBuffer();
              files.push({ name: 'images/' + f.name, content: new Uint8Array(buf) });
            }
          } catch(e) { console.warn('Failed to fetch image for export:', f.name); }
        }
      }
      // Build a simple ZIP (store method, no compression)
      const zipBlob = buildSimpleZip(files);
      const a = document.createElement('a');
      a.href = URL.createObjectURL(zipBlob);
      a.download = 'conversation_' + conversationId.substring(0, 8) + '.zip';
      a.click();
      URL.revokeObjectURL(a.href);
    } else {
      // Plain HTML download
      const blob = new Blob([htmlContent], { type: 'text/html;charset=utf-8' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'conversation_' + conversationId.substring(0, 8) + '.html';
      a.click();
      URL.revokeObjectURL(a.href);
    }
    addMsg('system', t('exported'));
    document.getElementById('status').textContent = t('ready');
  } catch (e) {
    console.error('Export error:', e);
    addMsg('error', 'Export failed: ' + e.message);
    document.getElementById('status').textContent = t('ready');
  }
}

function buildExportHtml(messages, nicknames, fileUrls) {
  const nicks = nicknames || {};
  function nickLookup(name) {
    const lk = (name || '').toLowerCase();
    for (const k of Object.keys(nicks)) { if (k.toLowerCase() === lk) return nicks[k]; }
    return name || '';
  }
  let body = '';
  for (const m of messages) {
    const type = m.type || m.role;
    if (type === 'system') continue;
    let cssClass = type;
    let content = m.content || '';
    let badge = '';
    if (type === 'assistant' || type === 'user') {
      const src = m.source || {};
      const srcName = nickLookup(src.name);
      if (srcName) {
        const h = [...srcName].reduce((a, c) => ((a << 5) - a + c.charCodeAt(0)) | 0, 0);
        const hue = Math.abs(h) % 360;
        badge = '<span style="display:inline-block;font-size:10px;padding:1px 6px;border-radius:8px;margin-right:4px;font-weight:600;background:hsl(' + hue + ',60%,25%);color:hsl(' + hue + ',80%,80%)">' + escapeHtml(srcName) + '</span>';
      }
      if (type === 'assistant' && src.type === 'agent' && src.name && src.name !== 'assistant') {
        cssClass = 'subagent';
      }
      // Strip identity prefix
      content = content.replace(/^\[[^\]]+\]:\s*/, '');
    }
    if (type === 'tool_call' || type === 'tool_result') cssClass = 'tool';
    // Convert markdown-like formatting
    let html = escapeHtml(content);
    html = html.replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>');
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');
    // Replace file URLs with image tags or links
    for (const f of fileUrls) {
      if (isImageFile(f.name)) {
        html = html.split(escapeHtml(f.url)).join('<br><img src="images/' + f.name + '" style="max-width:512px;max-height:512px;border-radius:8px;"><br>');
      }
    }
    body += '<div class="msg ' + cssClass + '">' + badge + html + '</div>\n';
  }
  return '<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">'
    + '<title>PyFi2 Conversation Export</title>'
    + '<style>'
    + 'body { font-family: -apple-system, sans-serif; background: #1a1a2e; color: #e0e0e0; padding: 20px; max-width: 900px; margin: 0 auto; }'
    + '.msg { padding: 10px 14px; border-radius: 12px; margin-bottom: 12px; line-height: 1.5; font-size: 14px; white-space: pre-wrap; word-wrap: break-word; }'
    + '.msg a { color: #4fc3f7; }'
    + '.msg code { background: rgba(0,0,0,0.3); padding: 1px 5px; border-radius: 3px; }'
    + '.msg pre { background: rgba(0,0,0,0.4); padding: 10px; border-radius: 6px; overflow-x: auto; }'
    + '.msg.user { background: #0f3460; color: white; margin-left: 20%; border-left: 3px solid #4ecdc4; }'
    + '.msg.assistant { background: #16213e; border: 1px solid #0f3460; margin-right: 20%; border-left: 3px solid #e94560; }'
    + '.msg.subagent { background: #0d1b2a; border: 1px solid #1a3a5c; margin-right: 20%; border-left: 3px solid #6c5ce7; }'
    + '.msg.tool { background: #0f1629; color: #808090; font-size: 12px; border-left: 2px solid #0f3460; margin-right: 30%; }'
    + '.msg.btw { background: #0d1b2a; font-size: 13px; border-left: 3px solid #60a5fa; margin-right: 20%; font-style: italic; }'
    + 'img { display: block; margin: 8px 0; }'
    + '</style></head><body>'
    + '<h1 style="color:#e94560;margin-bottom:20px;">PyFi2 Conversation Export</h1>'
    + '<p style="color:#6c6c8a;margin-bottom:20px;">Exported: ' + new Date().toLocaleString() + '</p>'
    + body
    + '</body></html>';
}

function buildSimpleZip(files) {
  // Minimal ZIP builder (Store method, no compression)
  const parts = [];
  const directory = [];
  let offset = 0;
  for (const f of files) {
    const nameBytes = new TextEncoder().encode(f.name);
    const data = f.content;
    // Local file header (30 bytes + name)
    const header = new Uint8Array(30 + nameBytes.length);
    const hv = new DataView(header.buffer);
    hv.setUint32(0, 0x04034b50, true); // signature
    hv.setUint16(4, 20, true);  // version needed
    hv.setUint16(6, 0, true);   // flags
    hv.setUint16(8, 0, true);   // compression (store)
    hv.setUint16(10, 0, true);  // mod time
    hv.setUint16(12, 0, true);  // mod date
    // CRC-32
    const crc = crc32(data);
    hv.setUint32(14, crc, true);
    hv.setUint32(18, data.length, true);  // compressed size
    hv.setUint32(22, data.length, true);  // uncompressed size
    hv.setUint16(26, nameBytes.length, true);
    hv.setUint16(28, 0, true);  // extra field length
    header.set(nameBytes, 30);
    parts.push(header);
    parts.push(data);
    // Central directory entry
    const cdEntry = new Uint8Array(46 + nameBytes.length);
    const cv = new DataView(cdEntry.buffer);
    cv.setUint32(0, 0x02014b50, true);
    cv.setUint16(4, 20, true);
    cv.setUint16(6, 20, true);
    cv.setUint16(8, 0, true);
    cv.setUint16(10, 0, true);
    cv.setUint16(12, 0, true);
    cv.setUint16(14, 0, true);
    cv.setUint32(16, crc, true);
    cv.setUint32(20, data.length, true);
    cv.setUint32(24, data.length, true);
    cv.setUint16(28, nameBytes.length, true);
    cv.setUint16(30, 0, true);
    cv.setUint16(32, 0, true);
    cv.setUint16(34, 0, true);
    cv.setUint16(36, 0, true);
    cv.setUint32(38, 0, true);
    cv.setUint32(42, offset, true);
    cdEntry.set(nameBytes, 46);
    directory.push(cdEntry);
    offset += header.length + data.length;
  }
  // Central directory
  const cdOffset = offset;
  let cdSize = 0;
  for (const d of directory) { parts.push(d); cdSize += d.length; }
  // End of central directory (22 bytes)
  const eocd = new Uint8Array(22);
  const ev = new DataView(eocd.buffer);
  ev.setUint32(0, 0x06054b50, true);
  ev.setUint16(4, 0, true);
  ev.setUint16(6, 0, true);
  ev.setUint16(8, files.length, true);
  ev.setUint16(10, files.length, true);
  ev.setUint32(12, cdSize, true);
  ev.setUint32(16, cdOffset, true);
  ev.setUint16(20, 0, true);
  parts.push(eocd);
  return new Blob(parts, { type: 'application/zip' });
}

function crc32(data) {
  let crc = 0xFFFFFFFF;
  for (let i = 0; i < data.length; i++) {
    crc ^= data[i];
    for (let j = 0; j < 8; j++) {
      crc = (crc >>> 1) ^ (crc & 1 ? 0xEDB88320 : 0);
    }
  }
  return (crc ^ 0xFFFFFFFF) >>> 0;
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
    clearAllStreams();
    for (const m of (data.messages || [])) {
      let content = m.content || '';
      if ((m.type === 'assistant' || m.role === 'assistant') && typeof content === 'string') {
        content = content.replace(/^\[[^\]]+\]:\s*/, '');
      }
      addMsg(m.type || m.role, content, m);
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
  const name = source.name ? displayAgentName(source.name) : '';
  const svc = source.llm_service || '';
  if (source.type === 'agent') {
    // Hash name to color
    let h = 0;
    for (let i = 0; i < name.length; i++) h = ((h << 5) - h + name.charCodeAt(i)) | 0;
    const hue = Math.abs(h) % 360;
    let label = svc ? name + ' via ' + svc : name;
    if (source.reply_to) label += ' \u2192 ' + displayAgentName(source.reply_to);
    return '<span class="source-badge" style="background:hsl(' + hue + ',60%,25%);color:hsl(' + hue + ',80%,80%)">' + escapeHtml(label) + '</span> ';
  }
  if (source.type === 'user') {
    let userLabel = (name && name !== 'anonymous') ? name : '';
    const target = source.target_agent;
    const isBtw = source.btw;
    if (target) {
      const prefix = isBtw ? '[btw \u2192 ' : '[\u2192 ';
      userLabel = (userLabel ? userLabel + ' ' : '') + prefix + displayAgentName(target) + ']';
    } else if (isBtw) {
      userLabel = (userLabel ? userLabel + ' ' : '') + 'btw';
    }
    if (userLabel) {
      return '<span class="source-badge" style="background:#1a3a2a;color:#4ecdc4">' + escapeHtml(userLabel) + '</span> ';
    }
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
  if (provider && provider !== model) parts.push(provider);
  if (tokIn || tokOut) parts.push('\u2191' + tokIn + ' \u2193' + tokOut);
  if (dur) parts.push((dur / 1000).toFixed(1) + 's');
  if (!parts.length) return '';
  // Compact summary line (always visible)
  let line = '<span class="meta-summary">' + parts.join(' \u00b7 ') + '</span>';
  // Expandable details
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
  let cssClass = (role === 'tool_call' || role === 'tool_result') ? 'tool' : role;
  // Differentiate sub-agent responses from main assistant visually
  if (role === 'assistant' && extra && extra.source && extra.source.type === 'agent') {
    const srcName = (extra.source.name || 'assistant').toLowerCase();
    if (srcName !== 'assistant') cssClass = 'subagent';
  }
  el.className = 'msg ' + cssClass;
  el.dataset.rawText = (text || '').substring(0, 500);  // for dedup comparison
  if (extra && extra.raw_index !== undefined) el.dataset.rawIndex = extra.raw_index;
  const badge = (extra && extra.source) ? sourceBadge(extra.source) : '';
  // Timestamp — use provided timestamp or current time
  const msgTime = (extra && extra.timestamp) ? new Date(extra.timestamp * 1000) : new Date();
  const timeStr = msgTime.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
  const timeHtml = '<span class="msg-time">' + timeStr + '</span>';

  // Action buttons (copy + delete) for user/assistant/subagent messages
  let actionsHtml = '';
  if (role === 'user' || role === 'assistant') {
    actionsHtml = '<span class="msg-actions">'
      + '<button onclick="copyMsg(this)" title="Copy">\u{1F4CB}</button>'
      + '<button onclick="deleteMsg(this)" title="Delete">\u{1F5D1}</button>'
      + '</span>';
  }

  if (role === 'assistant') {
    el.innerHTML = actionsHtml + timeHtml + badge + renderMarkdown(text) + buildMetaLine(extra);
  } else if (role === 'tool' || role === 'tool_call') {
    el.innerHTML = '<span style="color:#e94560;font-size:12px">' + escapeHtml(text) + '</span>';
  } else if (role === 'tool_result') {
    const toolId = (extra && extra.tool_call_id) ? extra.tool_call_id : '';
    el.innerHTML = '<span style="color:#4ecdc4;font-size:11px">\u21b3 ' + escapeHtml(text) + '</span>';
  } else if (role === 'user') {
    el.innerHTML = actionsHtml + timeHtml + badge + escapeHtml(text);
  } else if (role === 'agent-result') {
    const agentName = (extra && typeof extra === 'string') ? extra : '';
    el.innerHTML = (agentName ? '<strong>' + escapeHtml(agentName) + ':</strong> ' : '') + renderMarkdown(text);
  } else {
    el.textContent = text;
  }
  // Check near-bottom BEFORE appending so new element doesn't shift the threshold
  const shouldScroll = isNearBottom();
  const container = document.getElementById('messages');
  // Insert before typing indicator so it always stays at the bottom
  const typingEl = document.getElementById('typing');
  if (typingEl) {
    container.insertBefore(el, typingEl);
  } else {
    container.appendChild(el);
  }
  scrollBottom(shouldScroll);
  // Re-scroll when images finish loading (they change height after initial render)
  if (shouldScroll) {
    for (const img of el.querySelectorAll('img')) {
      img.addEventListener('load', () => scrollBottom(true), { once: true });
    }
  }
  return el;
}

function escapeHtml(t) {
  const d = document.createElement('div');
  d.textContent = t;
  return d.innerHTML;
}

function isImageFile(name) {
  return /\.(png|jpe?g|gif|svg|webp|bmp)$/i.test(name || '');
}

function inlineImageHtml(url, filename, sizeInfo) {
  // Render authenticated inline image (max 512px) with click-to-view
  const token = getToken();
  const imgId = 'img_' + Math.random().toString(36).substring(2, 8);
  // We need to fetch with auth then create blob URL
  setTimeout(() => {
    const el = document.getElementById(imgId);
    if (!el) return;
    const headers = {};
    if (token) headers['Authorization'] = 'Bearer ' + token;
    fetch(url, { headers, credentials: 'same-origin' }).then(r => {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.blob();
    }).then(blob => {
      el.src = URL.createObjectURL(blob);
      el.style.display = 'block';
    }).catch((err) => { el.alt = 'Failed to load image: ' + err.message; el.style.display = 'block'; });
  }, 50);
  return '<div style="margin:6px 0;">'
    + '<img id="' + imgId + '" style="display:none;max-width:512px;max-height:512px;border-radius:8px;cursor:pointer;border:1px solid #0f3460;" '
    + 'onclick="openFileViewer(\'' + url + '\')" title="Click to view full size" />'
    + '<div style="font-size:11px;color:#6c6c8a;margin-top:2px;">'
    + '\uD83D\uDCC4 ' + escapeHtml(filename || 'image') + (sizeInfo ? ' (' + sizeInfo + ')' : '')
    + '</div></div>';
}

function renderMarkdown(text) {
  // Detect __show_file__ markers from show_file tool
  try {
    if (text.includes('__show_file__')) {
      const parsed = JSON.parse(text);
      if (parsed && parsed.__show_file__) {
        if (isImageFile(parsed.filename)) {
          return inlineImageHtml(parsed.url, parsed.filename, parsed.size_kb + ' KB');
        }
        setTimeout(() => openFileViewer(parsed.url), 100);
        return '<span style="cursor:pointer;color:#6c5ce7;" onclick="openFileViewer(\'' + parsed.url + '\')">\uD83D\uDCC4 ' + parsed.filename + ' (' + parsed.size_kb + ' KB) \u2014 Click to view</span>';
      }
    }
  } catch(e) {}
  // Code blocks first (protect from other replacements)
  text = text.replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>');
  text = text.replace(/`([^`]+)`/g, '<code>$1</code>');
  // Markdown links: [text](url) — must run BEFORE bare URL detection
  text = text.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, function(_, label, url) {
    if (url.match(/\/files\/[a-f0-9]+\//)) {
      if (isImageFile(label) || isImageFile(url)) {
        return inlineImageHtml(url, label, '');
      }
      return '<a class="flink" href="' + url + '" style="color:#6c5ce7;cursor:pointer;" onclick="event.preventDefault();openFileViewer(\'' + url + '\')">\uD83D\uDCC4 ' + label + '</a>';
    }
    return '<a href="' + url + '" target="_blank">' + label + '</a>';
  });
  // Bare file URLs (not already inside a tag attribute)
  text = text.replace(/(^|[\s>])(https?:\/\/[^\s<"']*\/files\/[a-f0-9]+\/([^\s<"')]+))/g, function(_, pre, url, fname) {
    if (isImageFile(fname)) {
      return pre + inlineImageHtml(url, fname, '');
    }
    return pre + '<a class="flink" href="' + url + '" style="color:#6c5ce7;cursor:pointer;" onclick="event.preventDefault();openFileViewer(\'' + url + '\')">\uD83D\uDCC4 ' + fname + '</a>';
  });
  text = text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  text = text.replace(/\*(.+?)\*/g, '<em>$1</em>');
  // Bare URLs (skip those already inside HTML tags or attributes)
  // Split on existing tags (<a>, <img>, <div> with onclick, etc.) to avoid double-linking
  const parts = text.split(/(<[^>]+>)/gi);
  for (let i = 0; i < parts.length; i++) {
    // Only process text nodes (not inside any HTML tag)
    if (!parts[i].startsWith('<')) {
      parts[i] = parts[i].replace(/(https?:\/\/[^\s<"']+)/g, '<a href="$1" target="_blank">$1</a>');
    }
  }
  return parts.join('');
}

// Auto-scroll state: true by default, turned off when user scrolls up manually
let _autoScroll = true;
function isNearBottom() { return _autoScroll; }

// Detect manual scroll-up by user
(function() {
  const m = document.getElementById('messages');
  if (!m) return;
  let _lastScrollTop = 0;
  m.addEventListener('scroll', () => {
    const atBottom = m.scrollHeight - m.scrollTop - m.clientHeight <= 5;
    if (atBottom) {
      _autoScroll = true;
    } else if (m.scrollTop < _lastScrollTop) {
      // User scrolled UP → disable auto-scroll
      _autoScroll = false;
    }
    _lastScrollTop = m.scrollTop;
  });
})();

function scrollBottom(force) {
  if (force) _autoScroll = true;
  if (_autoScroll) {
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
let activeInteractions = {};  // agentKey (lowercase) → { name, startedAt, lastTool, activeTools, status, msgPreview }
let activeTimer = null;

function agentKey(name) { return (name || 'assistant').toLowerCase(); }

let _agentDoneAt = {};  // agentKey → timestamp of last done (prevents ghost re-register)
function trackAgentStart(agentName, msgPreview) {
  const key = agentKey(agentName);
  // Ignore thinking events that arrive within 500ms after a done (race condition guard)
  const doneTs = _agentDoneAt[key];
  if (doneTs && Date.now() - doneTs < 500) {
    console.log('[trackAgentStart] IGNORED (too close to done)', agentName);
    return;
  }
  if (activeInteractions[key]) {
    // Already tracked — just update status (don't reset startedAt/preview)
    activeInteractions[key].status = 'thinking';
    activeInteractions[key].activeTools = [];
  } else {
    activeInteractions[key] = {
      name: agentName || 'assistant',
      startedAt: Date.now(), lastTool: '', activeTools: [], status: 'thinking', msgPreview: msgPreview || '',
      updatedAt: Date.now(),
    };
  }
  updateActivePanel();
  if (!activeTimer) activeTimer = setInterval(updateActivePanel, 1000);
}
function _ensureInteraction(agentName) {
  // Ensure an activeInteractions entry exists (creates one if done event cleared it)
  const key = agentKey(agentName);
  if (!activeInteractions[key]) {
    activeInteractions[key] = {
      name: agentName || 'assistant',
      startedAt: Date.now(), lastTool: '', activeTools: [], status: 'thinking', msgPreview: '',
      updatedAt: Date.now(),
    };
    if (!activeTimer) activeTimer = setInterval(updateActivePanel, 1000);
  }
  // Ensure activeTools exists (backward compat)
  if (!activeInteractions[key].activeTools) activeInteractions[key].activeTools = [];
  return key;
}
function trackAgentTool(agentName, toolName) {
  const key = _ensureInteraction(agentName);
  activeInteractions[key].lastTool = toolName;
  activeInteractions[key].status = toolName;
  const at = activeInteractions[key].activeTools;
  if (at.indexOf(toolName) === -1) at.push(toolName);
  updateActivePanel();
}
function trackAgentToolDone(agentName, toolName) {
  const key = _ensureInteraction(agentName);
  if (activeInteractions[key]) {
    const at = activeInteractions[key].activeTools;
    const idx = at.indexOf(toolName);
    if (idx !== -1) at.splice(idx, 1);
    // Update status to remaining tool or thinking
    if (at.length > 0) {
      activeInteractions[key].status = at[at.length - 1];
    } else {
      activeInteractions[key].status = 'thinking';
    }
  }
  updateActivePanel();
}
function trackAgentDone(agentName) {
  const key = agentKey(agentName);
  _agentDoneAt[key] = Date.now();
  delete activeInteractions[key];
  updateActivePanel();
  if (Object.keys(activeInteractions).length === 0 && activeTimer) {
    clearInterval(activeTimer); activeTimer = null;
  }
}
function updateActivePanel() {
  const panel = document.getElementById('activePanel');
  const rows = document.getElementById('activeRows');
  const names = Object.keys(activeInteractions);
  const wasVisible = panel.classList.contains('visible');
  const wasAtBottom = isNearBottom();
  const scrollNav = document.getElementById('scrollNav');
  if (names.length === 0) {
    if (wasVisible) {
      panel.classList.remove('visible');
      hideTyping();
      if (scrollNav) scrollNav.style.bottom = '75px';
      if (wasAtBottom) scrollBottom(true);
    }
    return;
  }
  // Active agents → ensure thinking indicator is visible
  if (!document.getElementById('typing')) showTyping();
  panel.classList.add('visible');
  const now = Date.now();
  rows.innerHTML = names.map(key => {
    const info = activeInteractions[key];
    const displayName = displayAgentName(info.name);
    const secs = Math.round((now - info.startedAt) / 1000);
    const timeStr = secs < 60 ? secs + 's' : Math.floor(secs/60) + 'm' + (secs%60) + 's';
    // Build rich status: iter N · round N/M · N tools · [active tools]
    let statusParts = [];
    if (info.iteration) statusParts.push('iter ' + info.iteration);
    if (info.round && info.maxRounds > 1) statusParts.push('round ' + info.round + '/' + info.maxRounds);
    if (info.totalTools > 0) statusParts.push(info.totalTools + ' tools');
    // Show all concurrent active tools, not just the last one
    if (info.activeTools && info.activeTools.length > 1) {
      statusParts.push('[' + info.activeTools.join(', ') + ']');
    } else if (info.lastTool) {
      statusParts.push('[' + info.lastTool + ']');
    }
    const statusText = statusParts.length > 0 ? statusParts.join(' \u00b7 ') : 'thinking...';
    const preview = (!info.iteration && info.msgPreview) ? escapeHtml(info.msgPreview.substring(0, 40)) : '';
    const hue = Math.abs([...displayName].reduce((h,c) => (h * 31 + c.charCodeAt(0)) | 0, 0)) % 360;
    const color = 'hsl(' + hue + ',70%,65%)';
    // Use info.name (original casing) for API calls like interrupt/stop
    const apiName = info.name;
    return '<div class="active-row">'
      + '<span class="a-spinner" style="color:' + color + '">\u2733</span>'
      + '<span class="a-name" style="color:' + color + '">' + escapeHtml(displayName) + '</span>'
      + '<span class="a-msg">' + preview + '</span>'
      + '<span class="a-status">' + escapeHtml(statusText) + '</span>'
      + '<span class="a-time">' + timeStr + '</span>'
      + '<span class="a-actions">'
      + '<button title="Interrupt (force answer)" onclick="interruptSingle(\'' + escapeHtml(apiName) + '\')">&#x23F8;</button>'
      + '<button class="btn-stop" title="Stop" onclick="stopSingle(\'' + escapeHtml(apiName) + '\')">&#x25A0;</button>'
      + '</span></div>';
  }).join('');
  // Push scroll-nav above the active panel
  if (scrollNav) {
    const panelHeight = panel.offsetHeight || 60;
    scrollNav.style.bottom = (75 + panelHeight + 8) + 'px';
  }
  if (!wasVisible && wasAtBottom) scrollBottom(true);
}

// Sync active agents from server (source of truth)
let _syncActiveTimer = null;
function startActiveSync() {
  if (_syncActiveTimer) return;
  _syncActiveTimer = setInterval(syncActiveFromServer, 2000);
}
function stopActiveSync() {
  if (_syncActiveTimer) { clearInterval(_syncActiveTimer); _syncActiveTimer = null; }
}
async function syncActiveFromServer() {
  if (!conversationId) return;
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'list_active', conversation_id: conversationId }),
      credentials: 'same-origin',
    });
    if (!resp.ok) return;
    const data = await resp.json();
    const serverActive = data.active || [];
    const serverKeys = new Set(serverActive.map(a => agentKey(a.agent_name)));
    const now = Date.now();

    // Remove entries the server no longer knows about
    // BUT keep entries added by SSE less than 5s ago (race condition guard)
    for (const key of Object.keys(activeInteractions)) {
      if (!serverKeys.has(key) && (now - (activeInteractions[key].updatedAt || 0)) > 5000) {
        delete activeInteractions[key];
      }
    }
    // Add/update from server
    for (const a of serverActive) {
      const key = agentKey(a.agent_name);
      const existing = activeInteractions[key];
      activeInteractions[key] = {
        name: a.agent_name,
        startedAt: existing ? existing.startedAt : now - (a.duration_s * 1000),
        iteration: a.iteration || (existing ? existing.iteration : 0),
        lastTool: a.last_tool || (existing ? existing.lastTool : ''),
        totalTools: existing ? (existing.totalTools || 0) : 0,
        msgPreview: a.message_preview || '',
        updatedAt: now,
      };
    }
    updateActivePanel();
    // Thinking: show if agents active, hide if none
    if (Object.keys(activeInteractions).length > 0) {
      if (!document.getElementById('typing')) showTyping();
    } else {
      hideTyping();
    }
  } catch(e) { /* silent */ }
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
  // Stop button in active panel = force stop (no response, immediate kill)
  if (!conversationId) return;
  try {
    await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'cancel', conversation_id: conversationId, agent_name: agentName, force: true }),
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
  // If already showing, don't recreate (avoids layout thrashing)
  if (document.getElementById('typing')) return;
  if (typingInterval) { clearInterval(typingInterval); typingInterval = null; }
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

let contextOpInterval = null;
function showContextOp(label) {
  hideContextOp();
  const el = document.createElement('div');
  el.className = 'typing';
  el.id = 'contextOpTyping';
  const c = randomColor();
  el.innerHTML = '<span class="spinner" style="color:' + c + '">✻</span>'
    + '<em style="color:' + c + '">' + label + '</em> '
    + '<span class="verb" style="color:' + c + '">' + randomVerb() + '...</span>';
  document.getElementById('messages').appendChild(el);
  scrollBottom();
  contextOpInterval = setInterval(() => {
    const t = document.getElementById('contextOpTyping');
    if (t) {
      const c2 = randomColor();
      t.innerHTML = '<span class="spinner" style="color:' + c2 + '">✻</span>'
        + '<em style="color:' + c2 + '">' + label + '</em> '
        + '<span class="verb" style="color:' + c2 + '">' + randomVerb() + '...</span>';
    }
  }, 3000);
}

function hideContextOp() {
  if (contextOpInterval) { clearInterval(contextOpInterval); contextOpInterval = null; }
  const el = document.getElementById('contextOpTyping');
  if (el) el.remove();
}

// Connect SSE for a conversation
function connectSSE(cid) {
  if (eventSource) eventSource.close();
  if (sseReconnectTimer) { clearTimeout(sseReconnectTimer); sseReconnectTimer = null; }
  startActiveSync();
  sseRetryCount = 0;  // reset so onopen doesn't think we're reconnecting
  const token = getToken();
  const url = SSE_URL + '?conversation_id=' + encodeURIComponent(cid)
    + (token ? '&token=' + encodeURIComponent(token) : '');
  eventSource = new EventSource(url);

  eventSource.addEventListener('thinking', (e) => {
    lastSSEActivity = Date.now();
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
    const agent = data.agent_name || streamingAgent || 'assistant';
    streamingAgent = agent;  // legacy global
    const s = getStream(agent);
    s.text += data.text;
    streamingText = s.text;  // legacy global
    // Always have a source — every response comes from an agent
    const src = data.source || {type: 'agent', name: agent};
    if (!s.el) {
      s.el = addMsg('assistant', '', {source: src});
      // Apply subagent class if not main assistant
      const srcName = (src.name || 'assistant').toLowerCase();
      if (srcName !== 'assistant') {
        s.el.className = 'msg subagent';
      }
      s.chunks.push(s.el);
      streamingEl = s.el;  // legacy global
      streamingChunks = s.chunks;
    }
    // Update content with badge — strip identity prefix if LLM echoed it
    const badge = sourceBadge(src);
    const displayText = s.text.replace(/^\[[^\]]+\]:\s*/, '');
    const shouldScroll = isNearBottom();
    s.el.innerHTML = badge + renderMarkdown(displayText);
    scrollBottom(shouldScroll);
    document.getElementById('status').textContent = t('streaming');
  });

  eventSource.addEventListener('iteration_status', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    const agentName = data.agent_name || 'assistant';
    const aKey = agentKey(agentName);
    if (activeInteractions[aKey]) {
      activeInteractions[aKey].iteration = data.iteration;
      activeInteractions[aKey].maxIterations = data.max_iterations;
      activeInteractions[aKey].round = data.round;
      activeInteractions[aKey].maxRounds = data.max_rounds;
      activeInteractions[aKey].totalTools = data.total_tools;
      if (data.tools_called && data.tools_called.length > 0) {
        activeInteractions[aKey].lastTool = data.tools_called[data.tools_called.length - 1];
      }
    }
    updateActivePanel();
    document.getElementById('status').textContent =
      t('iterStatus', {agent: displayAgentName(agentName), i: data.iteration, r: data.round, mr: data.max_rounds, t: data.total_tools});
    // Multi-tour: show compact progress message in chat when iteration advances
    if (data.iteration > 1 || data.round > 1) {
      const lastShown = activeInteractions[aKey] ? activeInteractions[aKey]._lastShownIter : undefined;
      if (data.iteration !== lastShown) {
        addMsg('system-compact', t('iterProgress', {
          agent: displayAgentName(agentName), i: data.iteration, r: data.round,
          mr: data.max_rounds, t: data.total_tools
        }));
        if (activeInteractions[aKey]) {
          activeInteractions[aKey]._lastShownIter = data.iteration;
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
    showTyping();
  });

  eventSource.addEventListener('sub_agent_iteration', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    const agentName = data.agent_name || 'sub-agent';
    const aKey = agentKey(agentName);
    if (activeInteractions[aKey]) {
      activeInteractions[aKey].iteration = data.iteration;
      activeInteractions[aKey].maxIterations = data.max_iterations;
      activeInteractions[aKey].totalTools = data.total_tools;
      if (data.tools_called && data.tools_called.length > 0) {
        activeInteractions[aKey].lastTool = data.tools_called[data.tools_called.length - 1];
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
    hideTyping();
    const svcInfo = data.llm_service ? ' via ' + data.llm_service : '';
    const srcInfo = data.source_agent ? displayAgentName(data.source_agent) + ' \u2192 ' : '';
    const header = srcInfo + displayAgentName(agent) + svcInfo;
    if (data.response) {
      const extra = { source: { type: 'agent', name: agent, llm_service: data.llm_service || '' } };
      if (data.source_agent) extra.source.reply_to = data.source_agent;
      if (data.model) extra.model = data.model;
      if (data.provider) extra.provider = data.provider;
      if (data.tokens_in || data.tokens_out) { extra.tokens_in = data.tokens_in || 0; extra.tokens_out = data.tokens_out || 0; }
      if (data.duration_s) extra.duration_ms = data.duration_s * 1000;
      addMsg('assistant', data.response, extra);
    } else if (data.error) {
      addMsg('agent-result', 'Error: ' + data.error, agent);
    }
    scrollBottom();
  });

  eventSource.addEventListener('tool_call', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    console.log('[SSE] tool_call received:', data.tool, data.agent_name, data.llm_service, JSON.stringify(data.arguments || {}).substring(0, 200));
    // Finalize streaming for THIS agent before showing tool call
    const tcAgent = data.agent_name || 'assistant';
    const tcs = streams[tcAgent.toLowerCase()];
    if (tcs && tcs.el) {
      // Detach the current streaming element so new tokens create a fresh one,
      // but KEEP it in chunks so the done handler can clean it up later.
      tcs.el = null; tcs.text = '';
      // Do NOT clear tcs.chunks — done handler needs them to remove DOM elements
    }
    trackAgentTool(tcAgent, data.tool);
    const srcLabel = displayAgentName(tcAgent) + (data.llm_service ? ' via ' + data.llm_service : '');
    // Parse arguments if string (some LLM providers return args as JSON string)
    let args = data.arguments || {};
    if (typeof args === 'string') { try { args = JSON.parse(args); } catch(e) {} }
    if (data.tool === 'spawn_agents' && args && args.tasks) {
      const lines = args.tasks.map(task => {
        const dst = displayAgentName(task.agent || '?');
        const preview = (task.message || '').substring(0, 80);
        return '\u27A1 ' + srcLabel + ' \u2192 ' + dst + (preview ? ': ' + preview : '');
      });
      addMsg('tool', lines.join('\n'));
    } else {
      // Show agent source + tool name + arguments preview
      const argKeys = Object.keys(args);
      let argPreview = '';
      if (argKeys.length > 0) {
        argPreview = argKeys.map(k => {
          const v = typeof args[k] === 'string' ? args[k].substring(0, 60) : JSON.stringify(args[k]).substring(0, 60);
          return k + '=' + v;
        }).join(', ');
        if (argPreview.length > 120) argPreview = argPreview.substring(0, 120) + '...';
      }
      addMsg('tool', '\u{1F527} [' + srcLabel + '] ' + data.tool + (argPreview ? '(' + argPreview + ')' : ''));
    }
    document.getElementById('status').textContent = t('usingTool', {tool: data.tool});
  });

  eventSource.addEventListener('tool_result', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    console.log('[SSE] tool_result received:', data.tool, (data.result || '').substring(0, 100));
    // spawn_agents: responses are shown via sub_agent_done events in real-time
    // tool_result just shows a compact summary (don't duplicate responses)
    if (data.tool === 'spawn_agents' && data.result) {
      const srcAgent = displayAgentName(data.agent_name || 'assistant');
      const srcSvc = data.llm_service ? ' via ' + data.llm_service : '';
      try {
        const agents = JSON.parse(data.result);
        if (Array.isArray(agents)) {
          const summary = agents.map(a => displayAgentName(a.agent || '?') + ': ' + a.status).join(', ');
          addMsg('tool', '\u2705 [' + srcAgent + srcSvc + '] spawn_agents: ' + summary);
        } else {
          addMsg('tool', '\u2705 [' + srcAgent + srcSvc + '] spawn_agents: ' + data.result.substring(0, 200));
        }
      } catch(ex) {
        // Result is an error string (e.g. self-call), not JSON
        addMsg('tool', '\u2705 [' + srcAgent + srcSvc + '] spawn_agents: ' + data.result.substring(0, 200));
      }
      showTyping();
      return;
    }
    if (data.agent_name) trackAgentToolDone(data.agent_name, data.tool);
    const resultAgent = displayAgentName(data.agent_name || 'assistant');
    const resultSvc = data.llm_service ? ' via ' + data.llm_service : '';
    const preview = (data.result || '').substring(0, 200);
    addMsg('tool', '\u2705 [' + resultAgent + resultSvc + '] ' + data.tool + ': ' + preview + (data.result && data.result.length > 200 ? '...' : ''));
    // User /call has no agent loop following — don't show typing
    if (data.agent_name === 'user') {
      hideTyping();
    } else {
      showTyping();
    }
  });

  eventSource.addEventListener('compact_progress', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    if (data.stage === 'start') {
      showContextOp('Compacting ' + (data.agent || '') + ' (' + data.messages + ' messages, ~' + data.tokens + ' tokens)');
    } else if (data.stage === 'chunking' || data.stage === 'summarizing') {
      showContextOp('Compacting: ' + (data.detail || data.stage));
    } else if (data.stage === 'done') {
      hideContextOp();
      contextOpInProgress = false;
      const agent = data.agent || 'shared';
      addMsg('system', 'Compacted (' + agent + '): ' + data.before + ' messages \u2192 ' + data.after + ' messages (~' + data.tokens_after + ' tokens)');
    } else if (data.stage === 'error') {
      hideContextOp();
      contextOpInProgress = false;
      addMsg('error', 'Compaction failed: ' + data.error);
    }
  });

  eventSource.addEventListener('task_progress', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    const agent = displayAgentName(data.agent || '?');
    if (data.stage === 'assigned') {
      const v = data.verifier ? ' (verifier: ' + displayAgentName(data.verifier) + ')' : '';
      addMsg('system', '\u{1F4CB} Task assigned to ' + agent + v + ': ' + (data.task || '').substring(0, 150));
    } else if (data.stage === 'verified') {
      const icon = data.approved ? '\u2705' : '\u274C';
      const verifier = displayAgentName(data.verifier || '?');
      addMsg('system', icon + ' Task for ' + agent + (data.approved ? ' approved' : ' rejected') + ' by ' + verifier + (data.reason ? ': ' + data.reason : ''));
    } else if (data.done) {
      addMsg('system', '\u2705 Task complete (' + agent + '): ' + (data.result || data.progress || ''));
    } else if (data.progress) {
      addMsg('system', '\u{1F4CA} Task progress (' + agent + ', iter ' + (data.iterations || '?') + '): ' + data.progress);
    }
    scrollBottom();
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

  eventSource.addEventListener('discard', (e) => {
    lastSSEActivity = Date.now();
    hideTyping();
    const data = JSON.parse(e.data);
    const agentName = data.agent_name || 'assistant';
    trackAgentDone(agentName);
    // Remove any streamed tokens for this agent
    const ds = streams[agentName.toLowerCase()];
    if (ds) {
      for (const c of ds.chunks) { if (c && c.parentNode) c.remove(); }
      clearStream(agentName);
    }
    if (Object.keys(activeInteractions).length === 0) {
      sending = false;
      document.getElementById('status').textContent = t('ready');
    }
  });

  eventSource.addEventListener('done', (e) => {
    lastSSEActivity = Date.now();
    hideTyping();
    const data = JSON.parse(e.data);
    const doneAgent = data.agent_name || data.source?.name || 'assistant';
    trackAgentDone(doneAgent);
    console.log('[SSE done]', doneAgent, data.response ? data.response.substring(0, 100) : '(empty)');
    // Sync message count to prevent poll from re-fetching these messages
    if (data.message_count) serverMsgCount = data.message_count;
    // Remove ONLY this agent's streaming chunks (not other agents').
    // Use both the tracked chunks AND a DOM scan, because tool_call
    // events may have cleared the JS references while leaving DOM elements.
    const s = streams[doneAgent.toLowerCase()] || { el: null, text: '', chunks: [] };
    const removedEls = new Set();
    for (const chunk of s.chunks) {
      if (chunk && chunk.parentNode) { chunk.remove(); removedEls.add(chunk); }
    }
    // Also remove any streaming element that was detached from tracking
    // (happens when tool_call clears tcs.el/chunks mid-stream)
    if (s.el && !removedEls.has(s.el) && s.el.parentNode) {
      s.el.remove();
    }
    // Strip internal tags that may leak into the response
    let resp = data.response || '';
    resp = resp.replace(/\s*\[NO_PENDING_WORK\]/g, '').replace(/\s*\[RECHECK_IN:\d+\]/g, '').trim();
    // Strip identity prefix if LLM echoed it back (e.g. "[AgentName]: text")
    resp = resp.replace(/^\[[^\]]+\]:\s*/, '');
    // Show the final response (or fallback if empty), with source badge + metadata
    // Every response always has a source — fallback to agent_name or 'assistant'
    const extra = {};
    extra.source = data.source || {type: 'agent', name: doneAgent};
    if (data.model) extra.model = data.model;
    if (data.provider) extra.provider = data.provider;
    if (data.base_url) extra.base_url = data.base_url;
    if (data.tokens_in || data.tokens_out) { extra.tokens_in = data.tokens_in || 0; extra.tokens_out = data.tokens_out || 0; }
    if (data.duration_ms) extra.duration_ms = data.duration_ms;
    if (resp) {
      addMsg('assistant', resp, extra);
    } else if (s.text) {
      addMsg('assistant', s.text.replace(/^\[[^\]]+\]:\s*/, ''), extra);
    }
    clearStream(doneAgent);
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
    // Remove streaming chunks for the cancelled agent(s)
    if (cancelAgent === 'all') {
      clearAllStreams();
    } else {
      const cs = streams[cancelAgent.toLowerCase()];
      if (cs) {
        for (const c of cs.chunks) { if (c && c.parentNode) c.remove(); }
        clearStream(cancelAgent);
      }
    }
    addMsg('system', cancelAgent !== 'all' ? '[' + displayAgentName(cancelAgent) + '] ' + t('cancelled') : t('cancelled'));
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
    const bKey = agent.toLowerCase();
    const dName = displayAgentName(agent);
    const el = addMsg('btw', '');
    el.innerHTML = '<span style="color:#60a5fa;font-size:11px;">[' + escapeHtml(dName) + ' \u00b7 btw] </span><em style="color:#888;">thinking...</em>';
    btwElements[bKey] = el;
    btwTexts[bKey] = '';
    scrollBottom();
  });

  eventSource.addEventListener('btw_token', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    const agent = data.agent_name || 'assistant';
    const bKey = agent.toLowerCase();
    const dName = displayAgentName(agent);
    btwTexts[bKey] = (btwTexts[bKey] || '') + data.text;
    const el = btwElements[bKey];
    if (el) {
      el.innerHTML = '<span style="color:#60a5fa;font-size:11px;">[' + escapeHtml(dName) + ' \u00b7 btw] </span>' + renderMarkdown(btwTexts[bKey]);
      scrollBottom();
    }
  });

  eventSource.addEventListener('btw_done', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    const agent = data.agent_name || 'assistant';
    const bKey = agent.toLowerCase();
    const dName = displayAgentName(agent);
    if (data.error) {
      const el = btwElements[bKey];
      if (el) { el.innerHTML = '<span style="color:#f87171;font-size:11px;">[' + escapeHtml(dName) + ' \u00b7 btw] Error: ' + escapeHtml(data.error) + '</span>'; }
      else { addMsg('error', '[' + dName + ' \u00b7 btw] ' + data.error); }
    } else if (data.response && !btwTexts[bKey]) {
      // Non-streaming fallback
      const el = btwElements[bKey] || addMsg('btw', '');
      el.innerHTML = '<span style="color:#60a5fa;font-size:11px;">[' + escapeHtml(dName) + ' \u00b7 btw] </span>' + renderMarkdown(data.response);
    }
    delete btwElements[bKey];
    delete btwTexts[bKey];
    scrollBottom();
  });

  eventSource.addEventListener('interrupting', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    addMsg('system', 'Interrupting ' + displayAgentName(data.agent) + ' — requesting immediate response...');
    scrollBottom();
  });

  // NOTE: duplicate 'discard' listener removed — handled by the first one above

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
    // Error could be from any agent — clear the agent's stream if specified
    const errAgent = data.agent_name || 'assistant';
    clearStream(errAgent);
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
    addMsg('system', t('thoughtScheduled', { agent: displayAgentName(data.agent), delay: data.delay || '?' }));
    scrollBottom();
  });

  eventSource.addEventListener('thought_firing', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    trackAgentStart(data.agent || 'assistant');
    addMsg('system', t('thoughtFiring', { agent: displayAgentName(data.agent) }));
    showTyping();
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
  '/msg': {
    usage: '/msg <name|ALL> <message>',
    short: 'Send a message to a specific agent (shortcut for /agent msg)',
    detail: 'Send a message to a specific agent without changing the active agent.\n\nExamples:\n  /msg grok Explain this code\n  /msg ALL What do you think?',
  },
  '/btw': {
    usage: '/btw <name|ALL> <question>',
    short: 'Side-channel question to an agent (shortcut for /agent btw)',
    detail: 'Ask a quick question to an agent without interrupting its current work.\n\nExamples:\n  /btw claude What is the time complexity?\n  /btw ALL Any thoughts on this?',
  },
  '/call': {
    usage: '/call tool_name(key=value, ...) or /call tool_name {"key": "value"}',
    short: 'Call a tool directly',
    detail: 'Execute any agent tool from the chat.\n\n'
      + 'Syntax:\n'
      + '  /call web_search(query="quantum computing")         \u2014 function-call style\n'
      + '  /call fetch_http(url="https://example.com")         \u2014 named params\n'
      + '  /call remember(text="important fact", tags=["note"]) \u2014 with array param\n'
      + '  /call web_search {"query": "quantum computing"}     \u2014 JSON style\n\n'
      + 'Help:\n'
      + '  /help call              \u2014 this help\n'
      + '  /help call <toolname>   \u2014 show tool parameters and description\n',
  },
  '/vidservice': {
    usage: '/vidservice [list | select <name> [agent] | clear [agent]]',
    short: 'Manage video generation service',
    detail: 'Choose which video generation service to use in this conversation.\n\n'
      + '  /vidservice list                  \u2014 Show available video services\n'
      + '  /vidservice select <name>         \u2014 Set default for all agents\n'
      + '  /vidservice select <name> <agent> \u2014 Set for a specific agent\n'
      + '  /vidservice clear                 \u2014 Remove all preferences (auto-select)\n'
      + '  /vidservice clear <agent>         \u2014 Remove preference for one agent\n',
  },
  '/task': {
    usage: '/task create | assign | list | delete | pause | resume | cancel',
    short: 'Create, assign and manage agent tasks',
    detail: 'Task library + autonomous task assignment. Tasks can be reusable definitions or inline.\n\n'
      + '**Library (reusable definitions):**\n'
      + '  /task create <name> "<prompt>" [--criteria "..."] [--interval XX]\n'
      + '  /task delete <name>           \u2014 Delete a task definition\n'
      + '  /task list                    \u2014 Show library + running tasks\n\n'
      + '**Assignment (from library or inline):**\n'
      + '  /task assign <agent> <taskname>              \u2014 From library\n'
      + '  /task assign <agent> <taskname> --var nbr_images=20 --var style=cyberpunk\n'
      + '  /task assign <agent> <taskname> --interval XX \u2014 Override interval\n'
      + '  /task assign <agent> "<inline task>" [--criteria "..."] [--interval XX] [--verifier <agent>]\n\n'
      + 'Variables: use ${name} in task definitions, resolved at assign time.\n'
      + 'Use \\${...} to keep literal ${...}. ${global.*} and ${secrets.*} also resolved.\n\n'
      + '**Control:**\n'
      + '  /task pause <task_id|agent>   \u2014 Pause a task or all tasks of an agent\n'
      + '  /task resume <task_id|agent>  \u2014 Resume a paused task or all of an agent\n'
      + '  /task cancel <task_id|agent>  \u2014 Cancel a task or all of an agent\n\n'
      + 'Task IDs look like t_xxxxxxxx. Use /task list to see them.\n'
      + 'Tasks survive server restarts and reschedule automatically.\n\n'
      + 'Example: /task assign grok "Scrape the top 100 HN posts" --verifier claude --interval 120 --criteria "all 100 posts summarized"',
  },
  '/imgservice': {
    usage: '/imgservice [list | select <name> [agent] | clear [agent]]',
    short: 'Manage image generation service',
    detail: 'Choose which image generation service to use in this conversation.\n\n'
      + '  /imgservice list                  \u2014 Show available image services\n'
      + '  /imgservice select <name>         \u2014 Set default for all agents\n'
      + '  /imgservice select <name> <agent> \u2014 Set for a specific agent\n'
      + '  /imgservice clear                 \u2014 Remove all preferences (auto-select)\n'
      + '  /imgservice clear <agent>         \u2014 Remove preference for one agent\n',
  },
  '/agent': {
    usage: '/agent list | create | select | delete | msg | interrupt | btw | resume | setname',
    short: 'Manage AI agents',
    detail: 'Create, list, select, message, or control AI agents.\n\n'
      + '  /agent list                       — List all agents (user + global)\n'
      + '  /agent create                     — Create a new agent (interactive)\n'
      + '  /agent select <name>              — Activate an agent (use real name or nickname)\n'
      + '  /agent select assistant            — Switch back to the default assistant\n'
      + '  /agent delete <name>              — Delete an agent by name\n'
      + '  /agent msg <name> <text>          — Send a message to a specific agent\n'
      + '  /agent msg ALL <text>             — Broadcast to all agents in parallel\n'
      + '  /agent interrupt <name|ALL>       — Force agent to stop and respond immediately\n'
      + '  /agent btw <name|ALL> <text>      — Side-channel question (no interruption)\n'
      + '  /agent resume <name>              — Tell agent to continue from where it stopped\n'
      + '  /agent setname <real> [nickname]  — Set or reset display name (omit to reset)\n\n'
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
  '/llm': {
    usage: '/llm <agent|assistant> <service|${variable}|restore>',
    short: 'Change LLM service for an agent in this conversation',
    detail: 'Override the LLM service for any agent in the current conversation.\n\n'
      + '  /llm assistant grok_llm_service    \u2014 Switch assistant to grok\n'
      + '  /llm grok qwen_llm_service         \u2014 Switch grok to local qwen\n'
      + '  /llm assistant ${user.my_service}   \u2014 Use a variable reference\n'
      + '  /llm grok restore                   \u2014 Restore grok\'s default service\n\n'
      + 'The override is per-conversation and persists across restarts.',
  },
  '/stop': {
    usage: '/stop <agent|ALL> [-f]',
    short: 'Stop an agent — asks it to respond immediately',
    detail: 'Interrupts the agent and asks it to give its best answer now.\n\n'
      + '  /stop ALL          — Stop all agents (they respond with what they have)\n'
      + '  /stop grok         — Stop only grok\n'
      + '  /stop ALL -f       — Force stop all (immediate cancel, no response)\n'
      + '  /stop grok -f      — Force stop grok (immediate cancel)',
  },
  '/restart_from': {
    usage: '/restart_from [agent|ALL] [N]',
    short: 'Restart context from last N messages (default 5, 0 = empty)',
    detail: 'Keeps only the last N messages as LLM context. Earlier messages stay in history but are ignored by the agent.\n\n'
      + '  /restart_from          \u2014 Keep last 5 messages (shared)\n'
      + '  /restart_from 10       \u2014 Keep last 10 messages\n'
      + '  /restart_from grok 3   \u2014 Keep last 3 for grok\'s context\n'
      + '  /restart_from ALL 5    \u2014 Restart all agents\n'
      + '  /restart_from 0    — Empty context (fresh start, keeps system prompt)\n\n'
      + 'Useful when the conversation gets too long or the agent loses focus.',
  },
  '/summary': {
    usage: '/summary [agent|ALL] [tokens]',
    short: 'Summarize context to N tokens and restart from summary',
    detail: 'Asks the LLM to summarize the context to approximately N tokens (default 500), then restarts from that summary.\n\n'
      + '  /summary              \u2014 Summarize shared context to ~500 tokens\n'
      + '  /summary 1000         \u2014 Summarize to ~1000 tokens\n'
      + '  /summary grok         \u2014 Summarize grok\'s context\n'
      + '  /summary ALL          \u2014 Summarize all agents\' contexts\n'
      + '  /summary qwen 2000    \u2014 Summarize qwen\'s context to ~2000 tokens\n\n'
      + 'The summary replaces previous context for that agent. New messages build on top.',
  },
  '/resume': {
    usage: '/resume <agent|ALL>',
    short: 'Tell an agent to continue from where it stopped',
    detail: 'Resumes an agent that was interrupted or stopped.\n\nExamples:\n  /resume grok\n  /resume ALL',
  },
  '/compact': {
    usage: '/compact [agent|ALL]',
    short: 'Compact context (summarize old messages)',
    detail: 'Summarizes older messages to reduce context size while preserving key information.\n\n'
      + '  /compact        \u2014 Compact the shared context\n'
      + '  /compact grok   \u2014 Compact grok\'s context only\n'
      + '  /compact ALL    \u2014 Compact all agents\' contexts',
  },
  '/rebuild': {
    usage: '/rebuild [agent|ALL]',
    short: 'Rebuild context from full conversation history',
    detail: 'Reconstructs the LLM context from the complete conversation. If everything fits, restores fully; otherwise compacts.\n\n'
      + '  /rebuild        \u2014 Rebuild shared context\n'
      + '  /rebuild grok   \u2014 Rebuild grok\'s context\n'
      + '  /rebuild ALL    \u2014 Rebuild all agents',
  },
  '/rebuild_clean': {
    usage: '/rebuild_clean',
    short: 'Set context = full conversation (no compaction, deprecated — use /rebuild-full)',
    detail: 'Deprecated. Use /rebuild-full instead.',
  },
  '/rebuild-full': {
    usage: '/rebuild-full [agent|ALL]',
    short: 'Set context = full conversation (no compaction)',
    detail: 'Copies the entire conversation history into the LLM context as-is, without any compaction or summarization. Use when you want the agent to see everything.\n\n'
      + '  /rebuild-full        \u2014 Rebuild shared context\n'
      + '  /rebuild-full grok   \u2014 Rebuild grok\'s context\n'
      + '  /rebuild-full ALL    \u2014 Rebuild all agents\' contexts',
  },
  '/context': {
    usage: '/context [agent]',
    short: 'View the LLM context',
    detail: 'Shows what the LLM actually sees: messages, token estimate, divergence status.\n\n'
      + '  /context        \u2014 View shared context\n'
      + '  /context grok   \u2014 View grok\'s context\n\n'
      + 'The overlay includes an agent dropdown to switch between agent contexts.',
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
    usage: '/memory [list [agent] | add | edit | del | search | panel]',
    short: 'Manage agent memories',
    detail: 'View, add, edit and delete persistent agent memories.\n\n'
      + '  /memory                              \u2014 Open memory panel (visual editor)\n'
      + '  /memory list                         \u2014 List all memories\n'
      + '  /memory list <agent>                 \u2014 List memories for an agent\n'
      + '  /memory add <text> [#tag1] [@agent]  \u2014 Add a memory manually\n'
      + '  /memory edit <id> <new text>         \u2014 Edit a memory\n'
      + '  /memory del <id>                     \u2014 Delete a memory\n'
      + '  /memory search <query>               \u2014 Search memories by text',
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
  '/cost': {
    usage: '/cost <agent|ALL>',
    short: 'Show token usage and estimated cost per agent',
    detail: 'Displays input/output tokens, call count, and estimated cost per agent.\n\n'
      + '  /cost ALL     — All agents\n'
      + '  /cost grok    — Specific agent\n\n'
      + 'Cost is calculated from cost_per_1m_input/output ($ per million tokens) on the LLM service.\n'
      + 'If not configured, shows "not configured".',
  },
  '/autoconv': {
    usage: '/autoconv <on|off|status|now> <agent|ALL> [freq]',
    short: 'Auto-conversation — agents contribute to the conversation autonomously',
    detail: 'Enable autonomous conversation contributions from an agent.\n\n'
      + '  /autoconv on ALL              — All agents, default 6/1m\n'
      + '  /autoconv on grok 2-3/h       — Grok, 2-3 times per hour\n'
      + '  /autoconv on ALL 1/2h         — All agents, once per 2h\n'
      + '  /autoconv off ALL             — Disable for all agents\n'
      + '  /autoconv off grok            — Disable for grok\n'
      + '  /autoconv status ALL          — Show config for all agents\n'
      + '  /autoconv now ALL             — Trigger all immediately\n\n'
      + 'Frequency format: <min>[-<max>]/<duration>. Units: s, m, h, d.\n'
      + 'Only one schedule per agent — re-running /autoconv on replaces the previous.\n'
      + 'Only fires when the conversation is idle (no active interaction).',
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
    // Handle /help call [toolname] — show tool schema or list
    const helpParts = topic.split(/\s+/);
    if (helpParts[0] === 'call') {
      if (helpParts[1]) {
        cmdHelpTool(helpParts[1]);
      } else {
        cmdHelpToolList();
      }
      return;
    }
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

async function cmdHelpToolList() {
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'get_tool_schemas' }),
    });
    const data = await resp.json();
    const tools = (data.tools || []).sort((a, b) => a.name.localeCompare(b.name));
    let lines = ['<b>Available tools for /call:</b>', ''];
    for (const t of tools) {
      const params = t.parameters?.properties ? Object.keys(t.parameters.properties) : [];
      const paramStr = params.length ? '(' + params.join(', ') + ')' : '()';
      lines.push('  <code>' + t.name + paramStr + '</code> — ' + escapeHtml((t.description || '').substring(0, 80)));
    }
    lines.push('', 'Type <code>/help call &lt;toolname&gt;</code> for detailed parameter info.');
    const el = addMsg('system', '');
    el.innerHTML = lines.join('<br>');
  } catch (e) { addMsg('error', 'Failed: ' + e.message); }
}

async function cmdHelpTool(toolName) {
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'get_tool_schemas' }),
    });
    const data = await resp.json();
    const tools = data.tools || [];
    const tool = tools.find(t => t.name === toolName);
    if (!tool) {
      // Show all available tools
      const names = tools.map(t => t.name).sort();
      addMsg('system', 'Tool "' + toolName + '" not found. Available tools:\n' + names.map(n => '  \u2022 ' + n).join('\n'));
      return;
    }
    const params = tool.parameters || {};
    const props = params.properties || {};
    const required = params.required || [];
    let lines = [
      '<b>/call ' + tool.name + '</b>',
      '',
      '<span style="color:#a0a0c0">' + escapeHtml(tool.description) + '</span>',
      '',
      '<b>Parameters:</b>',
    ];
    for (const [key, schema] of Object.entries(props)) {
      const req = required.includes(key) ? '<span style="color:#e74c3c">*</span>' : '';
      const type = schema.type || '?';
      const desc = schema.description || '';
      lines.push('  <code>' + key + '</code> (' + type + ')' + req + ' — ' + escapeHtml(desc));
    }
    if (Object.keys(props).length === 0) {
      lines.push('  <i>(no parameters)</i>');
    }
    lines.push('', '<b>Example:</b>');
    // Build example call
    const exArgs = [];
    for (const [key, schema] of Object.entries(props)) {
      if (required.includes(key)) {
        const ex = schema.type === 'string' ? '"..."' : schema.type === 'integer' ? '0' : schema.type === 'boolean' ? 'true' : '...';
        exArgs.push(key + '=' + ex);
      }
    }
    lines.push('  <code>/call ' + tool.name + '(' + exArgs.join(', ') + ')</code>');
    const el = addMsg('system', '');
    el.innerHTML = lines.join('<br>');
  } catch (e) { addMsg('error', 'Failed to load tool schema: ' + e.message); }
}

function resolveAgentName(nameOrNick) {
  // Resolve a nickname to the real agent name, or return as-is
  if (!nameOrNick) return nameOrNick;
  for (const [real, nick] of Object.entries(nicknameMap)) {
    if (nick.toLowerCase() === nameOrNick.toLowerCase()) return real;
  }
  return nameOrNick;
}

function displayAgentName(realName) {
  // Return nickname if set, otherwise real name (case-insensitive lookup)
  const key = (realName || '').toLowerCase();
  for (const k of Object.keys(nicknameMap)) {
    if (k.toLowerCase() === key) return nicknameMap[k];
  }
  return realName || 'assistant';
}

function parseQuotedArgs(text) {
  // Parse command arguments supporting quoted strings: /cmd "arg one" "arg two" plain
  const args = [];
  const re = /"([^"]*)"|\S+/g;
  let m;
  while ((m = re.exec(text)) !== null) {
    args.push(m[1] !== undefined ? m[1] : m[0]);
  }
  return args;
}

async function handleSlashCommand(text) {
  const parts = text.split(/\s+/);
  const cmd = parts[0].toLowerCase();

  if (cmd === '/llm' || cmd === '/set_llm_service') {
    // /llm <agent> <service_or_variable>   or   /llm <agent> restore
    const agent = parts[1] || '';
    const svc = parts.slice(2).join(' ') || '';
    if (!agent || !svc) {
      addMsg('system', 'Usage: /llm <agent|assistant> <service_name|${variable}|restore>');
      return true;
    }
    try {
      const resp = await fetch(API, {
        method: 'POST', headers: getAuthHeaders(),
        body: JSON.stringify({
          action: 'set_llm_service', conversation_id: conversationId,
          agent_name: agent, llm_service: svc,
        }),
      });
      const data = await resp.json();
      addMsg('system', data.result || data.error || 'Done.');
    } catch (e) { addMsg('error', e.message); }
    return true;
  }

  if (cmd === '/stop') {
    const force = parts.includes('-f') || parts.includes('--force');
    const targetParts = parts.slice(1).filter(p => p !== '-f' && p !== '--force');
    if (targetParts.length === 0) { addMsg('system', 'Usage: /stop <agent|ALL> [-f]'); return true; }
    const target = resolveAgentName(targetParts[0]);
    if (force) {
      await cancelAgent(target);
    } else {
      await cmdAgentInterrupt(target);
    }
    return true;
  }

  if (cmd === '/restart_from' || cmd === '/restart') {
    // Parse: /restart_from [agent|ALL] [N]
    let restartAgent = '';
    let restartN = 5;
    for (let i = 1; i < parts.length; i++) {
      const v = parseInt(parts[i]);
      if (!isNaN(v)) { restartN = v; }
      else { restartAgent = parts[i]; }
    }
    if (!conversationId) { addMsg('system', t('noConv')); return true; }
    if (contextOpInProgress) { addMsg('system', t('contextOpBusy')); return true; }
    contextOpInProgress = true;
    showContextOp('Restarting');
    const restartBody = { action: 'restart_from', conversation_id: conversationId, keep_last: restartN };
    if (restartAgent) restartBody.agent_name = restartAgent;
    fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify(restartBody),
      credentials: 'same-origin',
    }).then(r => r.json()).then(data => {
      if (data.error) { addMsg('error', data.error); hideContextOp(); contextOpInProgress = false; }
      // SSE compact_progress events handle the display
    }).catch(e => { addMsg('error', e.message); hideContextOp(); contextOpInProgress = false; })
      .finally(() => { hideContextOp(); contextOpInProgress = false; });
    return true;
  }

  if (cmd === '/resume') {
    const rargs = parseQuotedArgs(text);
    const target = resolveAgentName(rargs[1] || '');
    if (!target) { addMsg('system', 'Usage: /resume <agent|ALL>'); return true; }
    const resumeMsg = rargs.slice(2).join(' ') || 'Continue from where you left off.';
    if (target.toUpperCase() === 'ALL') { await cmdAgentMsgAll(resumeMsg); }
    else { await cmdAgentMsg(target, resumeMsg); }
    return true;
  }

  if (cmd === '/summary') {
    // Parse: /summary [agent|ALL] [tokens]
    let summaryAgent = '';
    let summaryTokens = 500;
    for (let i = 1; i < parts.length; i++) {
      const v = parseInt(parts[i]);
      if (!isNaN(v)) { summaryTokens = v; }
      else { summaryAgent = parts[i]; }
    }
    if (!conversationId) { addMsg('system', t('noConv')); return true; }
    if (contextOpInProgress) { addMsg('system', t('contextOpBusy')); return true; }
    contextOpInProgress = true;
    const label = summaryAgent ? 'Summarizing (' + summaryAgent + ')' : 'Summarizing';
    showContextOp(label);
    const summaryBody = { action: 'resume_conversation', conversation_id: conversationId, max_tokens: summaryTokens };
    if (summaryAgent) summaryBody.agent_name = summaryAgent;
    fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify(summaryBody),
      credentials: 'same-origin',
    }).then(r => r.json()).then(data => {
      if (data.error) { addMsg('error', data.error); hideContextOp(); contextOpInProgress = false; }
    }).catch(e => { addMsg('error', e.message); hideContextOp(); contextOpInProgress = false; });
    return true;
  }

  if (cmd === '/help') {
    cmdHelp(parts.slice(1).join(' '));
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
    if (contextOpInProgress) { addMsg('system', t('contextOpBusy')); return true; }
    cmdCompact(parts[1] || '');
    return true;
  }

  if (cmd === '/rebuild') {
    if (contextOpInProgress) { addMsg('system', t('contextOpBusy')); return true; }
    cmdRebuild(parts[1] || '');
    return true;
  }

  if (cmd === '/cost') {
    const cargs = parseQuotedArgs(text);
    const target = cargs[1] || '';
    if (!target) { addMsg('system', 'Usage: /cost <agent|ALL>'); return true; }
    try {
      const resp = await fetch(API, {
        method: 'POST', headers: getAuthHeaders(),
        body: JSON.stringify({ action: 'cost', agent: target }),
        credentials: 'same-origin',
      });
      const data = await resp.json();
      const services = data.services || [];
      if (services.length === 0) {
        addMsg('system', 'No usage data found.');
      } else {
        const lines = services.map(s => {
          const svc = s.llm_service || '?';
          const model = s.model || '';
          const provider = s.provider || '';
          const tokIn = (s.tokens_in || 0).toLocaleString();
          const tokOut = (s.tokens_out || 0).toLocaleString();
          const calls = s.calls || 0;
          let line = svc + (model ? ' (' + model + ')' : '') + ': ' + tokIn + ' in / ' + tokOut + ' out (' + calls + ' calls)';
          if (s.cost !== undefined) {
            line += ' — $' + s.cost.toFixed(6);
          } else {
            line += ' — cost: not configured';
          }
          return line;
        });
        const totalIn = services.reduce((sum, s) => sum + (s.tokens_in || 0), 0);
        const totalOut = services.reduce((sum, s) => sum + (s.tokens_out || 0), 0);
        const totalCost = services.reduce((sum, s) => sum + (s.cost || 0), 0);
        lines.push('---');
        lines.push('Total: ' + totalIn.toLocaleString() + ' in / ' + totalOut.toLocaleString() + ' out'
          + (totalCost > 0 ? ' — $' + totalCost.toFixed(6) : ''));
        addMsg('system', lines.join('\n'));
      }
    } catch (e) { addMsg('error', 'Failed: ' + e.message); }
    return true;
  }

  if (cmd === '/rebuild_clean' || cmd === '/rebuild-full') {
    if (contextOpInProgress) { addMsg('system', t('contextOpBusy')); return true; }
    const rfAgent = parts[1] || '';
    if (!conversationId) { addMsg('system', t('noConv')); return true; }
    contextOpInProgress = true;
    const rfLabel = rfAgent ? 'Rebuilding full (' + rfAgent + ')' : 'Rebuilding full';
    showContextOp(rfLabel);
    const rfBody = { action: 'rebuild_full', conversation_id: conversationId };
    if (rfAgent) rfBody.agent_name = rfAgent;
    fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify(rfBody),
    }).then(r => r.json()).then(data => {
      if (data.error) { addMsg('error', 'Rebuild full failed: ' + data.error); hideContextOp(); contextOpInProgress = false; }
    }).catch(e => { addMsg('error', 'Rebuild full failed: ' + e.message); hideContextOp(); contextOpInProgress = false; });
    return true;
  }

  if (cmd === '/context') {
    await cmdShowContext(parts[1] || '');
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


  if (cmd === '/setname') {
    const sargs = parseQuotedArgs(text);
    const realName = sargs[1] || '';
    const nickname = sargs[2] || '';
    if (!realName) { addMsg('system', 'Usage: /setname <agent> [nickname]  (omit nickname to reset)'); return true; }
    await cmdAgentSetname(realName, nickname || realName);
    return true;
  }

  if (cmd === '/msg') {
    const margs = parseQuotedArgs(text);
    const target = resolveAgentName(margs[1] || '');
    const msgText = margs.slice(2).join(' ');
    if (!target) { addMsg('system', 'Usage: /msg <name|ALL> <message>'); }
    else if (!msgText) { addMsg('system', 'Usage: /msg ' + target + ' <message>'); }
    else if (target.toUpperCase() === 'ALL') { await cmdAgentMsgAll(msgText); }
    else { await cmdAgentMsg(target, msgText); }
    return true;
  }

  if (cmd === '/btw') {
    const bargs = parseQuotedArgs(text);
    const target = resolveAgentName(bargs[1] || '');
    const btwText = bargs.slice(2).join(' ');
    if (!btwText && !target) { addMsg('system', 'Usage: /btw <name|ALL> <question>'); }
    else if (!btwText) {
      await cmdAgentBtw('', target + ' ' + bargs.slice(2).join(' '));
    } else {
      await cmdAgentBtw(target, btwText);
    }
    return true;
  }

  if (cmd === '/task') {
    const sub = (parts[1] || 'status').toLowerCase();
    if (sub === 'create') {
      // /task create <name> "<prompt>" [--criteria "..."] [--interval XX]
      const qargs = parseQuotedArgs(text);
      const taskName = qargs[2] || '';
      const taskPrompt = qargs[3] || '';
      if (!taskName || !taskPrompt) {
        addMsg('system', 'Usage: /task create <name> "<prompt>" [--criteria "..."] [--interval XX]');
        return true;
      }
      let criteria = '', interval = '';
      for (let i = 4; i < qargs.length; i++) {
        if (qargs[i] === '--criteria' && qargs[i+1]) criteria = qargs[++i];
        else if (qargs[i] === '--interval' && qargs[i+1]) interval = qargs[++i];
      }
      fetch(API, {
        method: 'POST', headers: getAuthHeaders(),
        body: JSON.stringify({
          action: 'create_task_def',
          name: taskName,
          data: { prompt: taskPrompt, criteria, default_interval: interval || '6/1m' },
        }),
      }).then(r => r.json()).then(data => {
        if (data.error) addMsg('error', data.error);
        else addMsg('system', `Task definition '${taskName}' created.`);
      }).catch(e => addMsg('error', e.message));
    } else if (sub === 'assign') {
      // /task assign <agent> <taskname_or_"description"> [--interval N] [--max N] [--verifier <agent>] [--criteria "<text>"]
      const qargs = parseQuotedArgs(text);
      const taskAgent = qargs[2] || '';
      const taskArg = qargs[3] || '';
      if (!taskAgent || !taskArg) {
        addMsg('system', 'Usage: /task assign <agent> <taskname> [--interval N]\n       /task assign <agent> "<inline description>" [--criteria "..."] [--interval N]');
        return true;
      }
      let interval = null, maxIter = 50, verifier = '', criteria = '';
      const variables = {};
      for (let i = 4; i < qargs.length; i++) {
        if (qargs[i] === '--interval' && qargs[i+1]) { interval = qargs[++i]; }
        else if (qargs[i] === '--max' && qargs[i+1]) { maxIter = parseInt(qargs[++i]) || 50; }
        else if (qargs[i] === '--verifier' && qargs[i+1]) { verifier = qargs[++i]; }
        else if (qargs[i] === '--criteria' && qargs[i+1]) { criteria = qargs[++i]; }
        else if (qargs[i] === '--var' && qargs[i+1]) {
          const kv = qargs[++i];
          const eq = kv.indexOf('=');
          if (eq > 0) variables[kv.substring(0, eq)] = kv.substring(eq + 1);
        }
      }
      // Detect library name vs inline description:
      // If taskArg has no spaces and no --criteria was given → library lookup
      const isLibrary = !taskArg.includes(' ') && !criteria;
      const body = {
        action: 'assign_task', conversation_id: conversationId,
        agent_name: taskAgent, max_iterations: maxIter, verifier,
        ...(interval != null ? { interval } : {}),
        ...(Object.keys(variables).length ? { variables } : {}),
      };
      if (isLibrary) {
        body.task_def_name = taskArg;
      } else {
        body.task = taskArg;
        body.completion_criteria = criteria;
      }
      fetch(API, {
        method: 'POST', headers: getAuthHeaders(),
        body: JSON.stringify(body),
      }).then(r => r.json()).then(data => {
        if (data.error) { addMsg('error', data.error); }
        else { addMsg('system', data.result || 'Task assigned.'); }
      }).catch(e => addMsg('error', e.message));
    } else if (sub === 'delete' || sub === 'del') {
      // /task delete <taskname> — delete a task definition from library
      const taskName = parts[2] || '';
      if (!taskName) { addMsg('system', 'Usage: /task delete <taskname>'); return true; }
      fetch(API, {
        method: 'POST', headers: getAuthHeaders(),
        body: JSON.stringify({
          action: 'delete_task_def',
          name: taskName,
        }),
      }).then(r => r.json()).then(data => {
        if (data.error) addMsg('error', data.error);
        else addMsg('system', `Task definition '${taskName}' deleted.`);
      }).catch(e => addMsg('error', e.message));
    } else if (sub === 'status' || sub === 'list') {
      const listAgent = parts[2] || '';
      // Show both library definitions and running instances
      const listBody = { action: 'task_status', conversation_id: conversationId, include_library: true };
      if (listAgent) listBody.agent_name = listAgent;
      fetch(API, {
        method: 'POST', headers: getAuthHeaders(),
        body: JSON.stringify(listBody),
      }).then(r => r.json()).then(data => {
        const defs = data.definitions || [];
        const tasks = data.tasks || [];
        const lines = [];
        if (defs.length) {
          lines.push('**Library:**');
          for (const d of defs) {
            lines.push('\u2022 `' + d.name + '` — ' + (d.description || d.prompt.substring(0, 60)) + ' [' + (d.default_interval || '6/1m') + ']');
          }
        }
        if (tasks.length) {
          if (lines.length) lines.push('');
          lines.push('**Running:**');
          for (const t of tasks) {
            let line = '\u2022 `' + (t.task_id || '?') + '` ' + t.agent + ': ' + t.task.substring(0, 80);
            const ivLabel = typeof t.interval === 'object' ? (t.interval.spec || t.interval.min + '-' + t.interval.max + 's') : t.interval + 's';
            line += ' [' + t.status + ', iter ' + t.iterations + '/' + t.max_iterations + ', ' + ivLabel + ']';
            if (t.task_def_name) line += ' (def: ' + t.task_def_name + ')';
            if (t.verifier) line += ' (verifier: ' + t.verifier + ')';
            if (t.last_result) line += '\n  Last: ' + t.last_result.substring(0, 100);
            lines.push(line);
          }
        }
        if (!lines.length) addMsg('system', 'No task definitions or running tasks.');
        else addMsg('system', lines.join('\n'));
      }).catch(e => addMsg('error', e.message));
    } else if (sub === 'pause' || sub === 'resume' || sub === 'cancel') {
      const taskAgent = parts[2];
      if (!taskAgent) { addMsg('system', 'Usage: /task ' + sub + ' <task_id|agent>'); return true; }
      fetch(API, {
        method: 'POST', headers: getAuthHeaders(),
        body: JSON.stringify({
          action: sub + '_task', conversation_id: conversationId,
          task_id: taskAgent.startsWith('t_') ? taskAgent : '',
          agent_name: taskAgent.startsWith('t_') ? '' : taskAgent,
        }),
      }).then(r => r.json()).then(data => {
        if (data.error) { addMsg('error', data.error); }
        else { addMsg('system', 'Task ' + sub + 'd for ' + taskAgent + '.'); }
      }).catch(e => addMsg('error', e.message));
    } else {
      addMsg('system', 'Usage: /task create | assign | list | delete | pause | resume | cancel');
    }
    return true;
  }

  if (cmd === '/call') {
    const callText = text.replace(/^\/call\s+/, '').trim();
    if (!callText) {
      addMsg('system', 'Usage: /call tool_name(key=value, ...) or /call tool_name {"key": "value"}\nType /help call for details.');
      return true;
    }
    const parsed = _parseToolCall(callText);
    if (parsed.error) {
      addMsg('system', 'Parse error: ' + parsed.error + '\nType /help call <toolname> for parameter info.');
      return true;
    }
    // Submit — tool_call + tool_result will arrive via SSE events
    // (same display path as agent tool calls)
    showTyping();
    fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({
        action: 'call_tool',
        tool_name: parsed.name,
        arguments: parsed.args,
        positional_args: parsed.positional || [],
        conversation_id: conversationId,
      }),
    }).then(r => r.json()).then(data => {
      if (data.error) {
        hideTyping();
        addMsg('error', data.error);
      }
      // No display here — SSE tool_call + tool_result events handle it
    }).catch(e => { hideTyping(); addMsg('error', 'Tool call failed: ' + e.message); });
    return true;
  }

  if (cmd === '/vidservice') {
    const sub = (parts[1] || 'list').toLowerCase();
    if (sub === 'list') {
      try {
        const resp = await fetch(API, {
          method: 'POST', headers: getAuthHeaders(),
          body: JSON.stringify({ action: 'list_video_services', conversation_id: conversationId }),
        });
        const services = await resp.json();
        if (!Array.isArray(services) || services.length === 0) {
          addMsg('system', 'No video generation services deployed.');
        } else {
          const lines = services.map(s => {
            let line = '  \u2022 ' + s.id + ' (' + s.type + ', ' + s.scope + ')';
            if (s.selected_for && s.selected_for.length > 0) {
              line += ' \u2190 selected for: ' + s.selected_for.join(', ');
            }
            return line;
          });
          addMsg('system', 'Video services available:\n' + lines.join('\n'));
        }
      } catch (e) { addMsg('error', e.message); }
    } else if (sub === 'select' && parts[2]) {
      const serviceName = parts[2];
      const agentName = parts[3] || '*';
      try {
        const resp = await fetch(API, {
          method: 'POST', headers: getAuthHeaders(),
          body: JSON.stringify({
            action: 'set_video_service', conversation_id: conversationId,
            service_name: serviceName, agent_name: agentName,
          }),
        });
        const data = await resp.json();
        if (data.ok) {
          const target = agentName === '*' ? 'all agents' : agentName;
          addMsg('system', 'Video service set to "' + serviceName + '" for ' + target + '.');
        } else {
          addMsg('error', data.error || 'Failed to set video service');
        }
      } catch (e) { addMsg('error', e.message); }
    } else if (sub === 'clear') {
      const agentName = parts[2] || '';
      try {
        const resp = await fetch(API, {
          method: 'POST', headers: getAuthHeaders(),
          body: JSON.stringify({
            action: 'clear_video_service', conversation_id: conversationId,
            agent_name: agentName,
          }),
        });
        const data = await resp.json();
        if (data.ok) {
          addMsg('system', agentName
            ? 'Video service preference cleared for ' + agentName + '.'
            : 'All video service preferences cleared.');
        } else {
          addMsg('error', data.error || 'Failed to clear');
        }
      } catch (e) { addMsg('error', e.message); }
    } else {
      addMsg('system', 'Usage: /vidservice list | select <name> [agent] | clear [agent]');
    }
    return true;
  }

  if (cmd === '/imgservice') {
    const sub = (parts[1] || 'list').toLowerCase();
    if (sub === 'list') {
      try {
        const resp = await fetch(API, {
          method: 'POST', headers: getAuthHeaders(),
          body: JSON.stringify({ action: 'list_image_services', conversation_id: conversationId }),
        });
        const services = await resp.json();
        if (!Array.isArray(services) || services.length === 0) {
          addMsg('system', 'No image generation services deployed.');
        } else {
          const lines = services.map(s => {
            let line = '  \u2022 ' + s.id + ' (' + s.type + ', ' + s.scope + ')';
            if (s.selected_for && s.selected_for.length > 0) {
              line += ' \u2190 selected for: ' + s.selected_for.join(', ');
            }
            return line;
          });
          addMsg('system', 'Image services available:\n' + lines.join('\n'));
        }
      } catch (e) { addMsg('error', e.message); }
    } else if (sub === 'select' && parts[2]) {
      const serviceName = parts[2];
      const agentName = parts[3] || '*';
      try {
        const resp = await fetch(API, {
          method: 'POST', headers: getAuthHeaders(),
          body: JSON.stringify({
            action: 'set_image_service', conversation_id: conversationId,
            service_name: serviceName, agent_name: agentName,
          }),
        });
        const data = await resp.json();
        if (data.ok) {
          const target = agentName === '*' ? 'all agents' : agentName;
          addMsg('system', 'Image service set to "' + serviceName + '" for ' + target + '.');
        } else {
          addMsg('error', data.error || 'Failed to set image service');
        }
      } catch (e) { addMsg('error', e.message); }
    } else if (sub === 'clear') {
      const agentName = parts[2] || '';
      try {
        const resp = await fetch(API, {
          method: 'POST', headers: getAuthHeaders(),
          body: JSON.stringify({
            action: 'clear_image_service', conversation_id: conversationId,
            agent_name: agentName,
          }),
        });
        const data = await resp.json();
        if (data.ok) {
          addMsg('system', agentName
            ? 'Image service preference cleared for ' + agentName + '.'
            : 'All image service preferences cleared.');
        } else {
          addMsg('error', data.error || 'Failed to clear');
        }
      } catch (e) { addMsg('error', e.message); }
    } else {
      addMsg('system', 'Usage: /imgservice list | select <name> [agent] | clear [agent]');
    }
    return true;
  }

  if (cmd === '/agent') {
    const qargs = parseQuotedArgs(text);  // handles "quoted agent names"
    const sub = (qargs[1] || 'list').toLowerCase();
    if (sub === 'list') {
      await cmdAgentList();
    } else if (sub === 'create') {
      await cmdAgentCreate();
    } else if (sub === 'select') {
      const name = resolveAgentName(qargs[2] || '');
      await cmdAgentSelect(name);
    } else if (sub === 'delete' || sub === 'del') {
      const name = resolveAgentName(qargs[2]);
      if (!name) { addMsg('system', 'Usage: /agent delete <name>'); }
      else { await cmdAgentDelete(name); }
    } else if (sub === 'msg' || sub === 'message') {
      const target = resolveAgentName(qargs[2] || '');
      const msgText = qargs.slice(3).join(' ');
      if (!target) { addMsg('system', 'Usage: /agent msg <name|ALL> <message>'); }
      else if (!msgText) { addMsg('system', 'Usage: /agent msg ' + target + ' <message>'); }
      else if (target.toUpperCase() === 'ALL') { await cmdAgentMsgAll(msgText); }
      else { await cmdAgentMsg(target, msgText); }
    } else if (sub === 'interrupt' || sub === 'int') {
      const target = resolveAgentName(qargs[2] || '');
      await cmdAgentInterrupt(target);
    } else if (sub === 'btw') {
      const target = resolveAgentName(qargs[2] || '');
      const btwText = qargs.slice(3).join(' ');
      if (!btwText && !target) { addMsg('system', 'Usage: /agent btw <name|ALL> <question>'); }
      else if (!btwText) {
        // No agent name given — treat target as message, send to assistant
        await cmdAgentBtw('', target + ' ' + qargs.slice(3).join(' '));
      } else {
        await cmdAgentBtw(target, btwText);
      }
    } else if (sub === 'resume') {
      const target = resolveAgentName(qargs[2] || '');
      const resumeMsg = qargs.slice(3).join(' ') || 'Continue from where you left off.';
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
    } else if (sub === 'setname' || sub === 'rename') {
      const qargs = parseQuotedArgs(text);  // ['/agent', 'setname', 'realname', 'nickname']
      const realName = qargs[2] || '';
      const nickname = qargs[3] || '';
      if (!realName) {
        addMsg('system', 'Usage: /agent setname <realname> [nickname]  (omit nickname to reset)');
      } else {
        await cmdAgentSetname(realName, nickname || realName);
      }
    } else if (sub === 'disable' && parts[2]) {
      try {
        const resp = await fetch(API, { method: 'POST', headers: getAuthHeaders(),
          body: JSON.stringify({ action: 'manage_resource', resource_type: 'agent', name: parts[2],
            data: {}, conversation_id: conversationId, _action: 'disable' }),
        });
        // manage_resource doesn't have direct disable — use dedicated action
        const resp2 = await fetch(API, { method: 'POST', headers: getAuthHeaders(),
          body: JSON.stringify({ action: 'agent_disable', agent_name: parts[2], conversation_id: conversationId }),
        });
        const data = await resp2.json();
        addMsg('system', data.result || data.error || 'Agent disabled.');
      } catch (e) { addMsg('error', e.message); }
    } else if (sub === 'enable' && parts[2]) {
      try {
        const resp = await fetch(API, { method: 'POST', headers: getAuthHeaders(),
          body: JSON.stringify({ action: 'agent_enable', agent_name: parts[2], conversation_id: conversationId }),
        });
        const data = await resp.json();
        addMsg('system', data.result || data.error || 'Agent enabled.');
      } catch (e) { addMsg('error', e.message); }
    } else if (sub === 'promote' && parts[2] && parts[3]) {
      try {
        const resp = await fetch(API, { method: 'POST', headers: getAuthHeaders(),
          body: JSON.stringify({ action: 'agent_promote', agent_name: parts[2], target_scope: parts[3],
            conversation_id: conversationId }),
        });
        const data = await resp.json();
        addMsg('system', data.result || data.error || 'Agent promoted.');
      } catch (e) { addMsg('error', e.message); }
    } else if (sub === 'create-conv') {
      const qargs = parseQuotedArgs(text);
      const cname = qargs[2] || '';
      const cprompt = qargs[3] || '';
      if (!cname || !cprompt) { addMsg('system', 'Usage: /agent create-conv <name> "<prompt>"'); return true; }
      try {
        const resp = await fetch(API, { method: 'POST', headers: getAuthHeaders(),
          body: JSON.stringify({ action: 'create_agent', conversation_id: conversationId,
            name: cname, prompt: cprompt, scope: 'conversation' }),
        });
        const data = await resp.json();
        addMsg('system', data.result || data.error || 'Agent created.');
      } catch (e) { addMsg('error', e.message); }
    } else {
      addMsg('system', 'Usage: /agent list | create | create-conv | select | delete | msg | disable | enable | promote | setname');
    }
    return true;
  }

  if (cmd === '/memory') {
    const sub = (parts[1] || '').toLowerCase();
    if (!sub || sub === 'panel') {
      // No subcommand or /memory panel → open overlay
      await cmdShowMemories();
    } else if (sub === 'list') {
      const agentFilter = parts[2] || null;
      await cmdMemoryList(agentFilter);
    } else if (sub === 'del' || sub === 'delete') {
      const memId = parts[2];
      if (!memId) { addMsg('system', 'Usage: /memory del <memory_id>'); }
      else { await cmdMemoryDel(memId); }
    } else if (sub === 'add') {
      // /memory add text here #tag1 #tag2 @agent
      const rest = text.replace(/^\/memory\s+add\s*/i, '');
      if (!rest.trim()) { addMsg('system', 'Usage: /memory add <text> [#tag1 #tag2] [@agent]'); return true; }
      // Extract @agent from end
      const agentMatch = rest.match(/@(\S+)\s*$/);
      let agent = '';
      let memText = rest;
      if (agentMatch) { agent = agentMatch[1]; memText = rest.slice(0, agentMatch.index).trim(); }
      // Extract #tags
      const tagMatches = memText.match(/#(\S+)/g) || [];
      const tags = tagMatches.map(t => t.slice(1));
      memText = memText.replace(/#\S+/g, '').trim();
      if (!memText) { addMsg('system', 'Usage: /memory add <text> [#tag1 #tag2] [@agent]'); return true; }
      try {
        const resp = await fetch(API, {
          method: 'POST', headers: getAuthHeaders(),
          body: JSON.stringify({ action: 'add_memory', text: memText, tags, agent }),
        });
        const data = await resp.json();
        addMsg('system', 'Memory added (id: ' + (data.id || '?') + ', agent: ' + (data.agent || 'global') + ')');
      } catch (e) { addMsg('error', e.message); }
    } else if (sub === 'edit') {
      const memId = parts[2];
      const newText = parts.slice(3).join(' ');
      if (!memId || !newText) { addMsg('system', 'Usage: /memory edit <id> <new text>'); return true; }
      try {
        const resp = await fetch(API, {
          method: 'POST', headers: getAuthHeaders(),
          body: JSON.stringify({ action: 'edit_memory', memory_id: memId, text: newText }),
        });
        const data = await resp.json();
        addMsg('system', data.updated ? 'Memory updated.' : 'Memory not found.');
      } catch (e) { addMsg('error', e.message); }
    } else if (sub === 'search') {
      const query = parts.slice(2).join(' ');
      if (!query) { addMsg('system', 'Usage: /memory search <query>'); return true; }
      await cmdMemoryList(null, query);
    } else {
      addMsg('system', 'Usage: /memory [list [agent] | add | edit | del | search | panel]');
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

  if (cmd === '/autoconv') {
    if (!conversationId) { addMsg('system', t('thoughtNoConv')); return true; }
    const qargs = parseQuotedArgs(text);  // ['/autoconv', sub, agent, freq]
    const sub = (qargs[1] || '').toLowerCase();
    if (!sub || !['on', 'off', 'status', 'now'].includes(sub)) {
      addMsg('system', 'Usage: /autoconv <on|off|status|now> <agent|ALL> [freq]');
      return true;
    }
    const body = { action: 'random_thought', conversation_id: conversationId, sub };
    const freqPattern = /^\d+(-\d+)?\/\d*[smhd]$/;
    if (sub === 'on') {
      // /autoconv on <agent> [freq] OR /autoconv on ALL [freq]
      if (!qargs[2]) { addMsg('system', 'Usage: /autoconv on <agent|ALL> [freq]'); return true; }
      if (freqPattern.test(qargs[2])) {
        // /autoconv on 3/h — missing agent
        addMsg('system', 'Usage: /autoconv on <agent|ALL> [freq]');
        return true;
      }
      body.agent = resolveAgentName(qargs[2]);
      body.frequency = qargs[3] || '6/1m';
    } else {
      // off, status, now — require agent
      if (!qargs[2]) { addMsg('system', 'Usage: /autoconv ' + sub + ' <agent|ALL>'); return true; }
      body.agent = resolveAgentName(qargs[2]);
    }
    try {
      const resp = await fetch(API, { method: 'POST', headers: getAuthHeaders(), body: JSON.stringify(body) });
      const data = await resp.json();
      if (data.error) { addMsg('error', data.error); }
      else if (sub === 'on') {
        const agents = data.agents || [data.agent];
        addMsg('system', t('thoughtEnabled', { agent: agents.map(displayAgentName).join(', '), freq: data.frequency, delay: data.next_in_seconds }));
      }
      else if (sub === 'off') {
        const agents = data.agents || [data.agent];
        addMsg('system', t('thoughtDisabled', { agent: agents.map(displayAgentName).join(', ') }));
      }
      else if (sub === 'now') { addMsg('system', t('thoughtTriggered', { agent: displayAgentName(data.agent) })); }
      else {
        if (data.agents && Array.isArray(data.agents)) {
          const lines = data.agents.map(a =>
            a.enabled
              ? t('thoughtStatus', { agent: displayAgentName(a.agent), freq: a.frequency, delay: a.next_in_seconds })
              : t('thoughtStatusOff', { agent: displayAgentName(a.agent) })
          );
          addMsg('system', lines.join('\n'));
        } else {
          addMsg('system', data.enabled ? t('thoughtStatus', { agent: displayAgentName(data.agent), freq: data.frequency, delay: data.next_in_seconds }) : t('thoughtStatusOff', { agent: displayAgentName(data.agent) }));
        }
      }
    } catch (e) { addMsg('error', 'Failed: ' + e.message); }
    return true;
  }

  // Unknown slash command — show error, don't send as message
  addMsg('system', 'Unknown command: ' + cmd + '. Type /help for available commands.');
  return true;
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
  fetch(API, {
    method: 'POST', headers: getAuthHeaders(),
    body: JSON.stringify({ action: 'list_agents', conversation_id: conversationId }),
  }).then(r => r.json()).then(data => {
    const agents = data.agents || {};
    const selected = data.selected || '';
    const names = Object.keys(agents);
    if (names.length === 0) {
      addMsg('system', 'No agents defined. Use /agent create to add one.');
    } else {
      const scopeIcons = {'global': '\u{1F310}', 'user': '\u{1F464}', 'conversation': '\u{1F4AC}'};
      const lines = names.map(n => {
        const a = agents[n];
        const marker = n === selected ? ' \u2705' : '';
        const scope = scopeIcons[a._scope || ''] || '';
        const pr = (a.prompt || '').substring(0, 80);
        return '\u2022 ' + scope + ' **' + n + '**' + marker + ' \u2014 ' + pr + '...';
      });
      addMsg('system', 'Agents (' + (selected ? 'active: ' + selected : 'none selected') + '):\n' + lines.join('\n'));
    }
  }).catch(e => addMsg('error', 'Failed to list agents: ' + e.message));
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

function updateActiveAgentBadge() {
  const badge = document.getElementById('activeAgentBadge');
  const agent = selectedAgent || 'assistant';
  // Color from agent name hash (same algo as source badges)
  let h = 0;
  for (let i = 0; i < agent.length; i++) h = ((h << 5) - h + agent.charCodeAt(i)) | 0;
  const hue = Math.abs(h) % 360;
  badge.style.background = 'hsl(' + hue + ',60%,25%)';
  badge.style.color = 'hsl(' + hue + ',80%,80%)';
  badge.textContent = '\u2192 ' + displayAgentName(agent);
  badge.title = agent === 'assistant' ? 'Default agent (assistant)' : 'Active: ' + agent + ' — click to switch back to assistant';
  badge.style.display = '';
}

async function cmdAgentSelect(name) {
  const isDefault = !name || name.toLowerCase() === 'assistant';
  if (!conversationId) {
    // No conversation yet — store pending selection, will be applied on first message
    pendingAgent = isDefault ? null : name;
    selectedAgent = isDefault ? '' : name;
    updateActiveAgentBadge();
    addMsg('system', isDefault ? 'Switched to default agent (assistant).' : `Agent '${name}' selected (will activate on first message).`);
    return;
  }
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({
        action: 'select_agent', conversation_id: conversationId,
        name: isDefault ? '' : name,
      }),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); return; }
    selectedAgent = isDefault ? '' : name;
    updateActiveAgentBadge();
    addMsg('system', isDefault ? 'Switched to default agent (assistant).' : `Agent '${name}' selected. Messages now go to ${name}.`);
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

async function cmdAgentSetname(realName, nickname) {
  if (!conversationId) { addMsg('system', 'No active conversation'); return; }
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({
        action: 'set_agent_nickname', conversation_id: conversationId,
        agent_name: realName, nickname: nickname,
      }),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); return; }
    nicknameMap[realName] = nickname;
    addMsg('system', t('agentRenamed', { real: realName, nick: nickname }));
  } catch (e) { addMsg('error', 'Failed: ' + e.message); }
}

function cmdAgentMsg(agentName, text) {
  // Send a message to a specific agent without changing the active agent
  // Capture and include any pending attachments
  const attachments = pendingFiles.map(f => ({
    filename: f.filename, mime_type: f.mime_type, data: f.data,
  }));
  const attachmentsForDisplay = [...pendingFiles];
  pendingFiles = [];
  renderAttachments();

  const userSource = { type: 'user', name: '', target_agent: agentName };
  const msgEl = addMsg('user', text, { source: userSource });
  if (attachmentsForDisplay.length > 0) {
    msgEl.innerHTML = sourceBadge(userSource) + escapeHtml(text) + renderUserAttachments(attachmentsForDisplay);
  }
  clearStream(agentName);
  showTyping();
  sending = true;
  lastSSEActivity = Date.now();
  document.getElementById('status').textContent = t('sending');

  const body = { message: text, target_agent: agentName };
  if (conversationId) body.conversation_id = conversationId;
  if (attachments.length > 0) body.attachments = attachments;
  const ttlVal = parseInt(document.getElementById('ttlSelect').value, 10);
  if (ttlVal > 0) body.ttl = ttlVal;

  fetch(API, {
    method: 'POST', headers: getAuthHeaders(),
    body: JSON.stringify(body),
    credentials: 'same-origin',
  }).then(r => r.json()).then(data => {
    if (data.error) { addMsg('error', data.error); hideTyping(); sending = false; return; }
    if (data.conversation_id && !conversationId) {
      conversationId = data.conversation_id;
      connectSSE(conversationId);
    }
    if (data.message_count) serverMsgCount = data.message_count;
  }).catch(e => {
    addMsg('error', 'Failed to send to agent: ' + e.message);
    hideTyping();
    sending = false;
  });
}

function cmdAgentMsgAll(text) {
  // Broadcast a message to ALL agents in parallel
  if (!conversationId) {
    // Need a conversation first — send a dummy to create one
    addMsg('system', 'Start a conversation first before broadcasting.');
    return;
  }
  addMsg('user', text, { source: { type: 'user', name: '', target_agent: 'ALL' } });
  showTyping();
  sending = true;
  lastSSEActivity = Date.now();
  document.getElementById('status').textContent = 'Broadcasting...';

  fetch(API, {
    method: 'POST', headers: getAuthHeaders(),
    body: JSON.stringify({
      action: 'broadcast_agents',
      conversation_id: conversationId,
      message: text,
    }),
    credentials: 'same-origin',
  }).then(r => r.json()).then(data => {
    if (data.error) { addMsg('error', data.error); hideTyping(); sending = false; }
  }).catch(e => {
    addMsg('error', 'Broadcast failed: ' + e.message);
    hideTyping();
    sending = false;
  });
}

function cmdAgentInterrupt(target) {
  if (!conversationId) { addMsg('system', 'No active conversation.'); return; }
  const isAll = target.toUpperCase() === 'ALL';
  addMsg('system', isAll ? 'Interrupting all agents...' : ('Interrupting ' + (target || 'assistant') + '...'));
  if (isAll) {
    // Interrupt default + all agents
    fetch(API, { method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'interrupt', conversation_id: conversationId, agent_name: '' }),
    }).catch(e => addMsg('error', 'Interrupt failed: ' + e.message));
    // Also interrupt each known agent
    fetch(API, { method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'list_agents', conversation_id: conversationId }),
    }).then(r => r.json()).then(data => {
      for (const name of Object.keys(data.agents || {})) {
        fetch(API, { method: 'POST', headers: getAuthHeaders(),
          body: JSON.stringify({ action: 'interrupt', conversation_id: conversationId, agent_name: name }),
        }).catch(() => {});
      }
    }).catch(() => {});
  } else {
    fetch(API, { method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'interrupt', conversation_id: conversationId, agent_name: target || '' }),
    }).catch(e => addMsg('error', 'Interrupt failed: ' + e.message));
  }
}

function cmdAgentBtw(target, question) {
  if (!conversationId) { addMsg('system', 'No active conversation.'); return; }
  const agent = target || '';
  const isAll = agent.toUpperCase() === 'ALL';
  addMsg('user', question, { source: { type: 'user', name: '', target_agent: agent || '', btw: true } });
  fetch(API, { method: 'POST', headers: getAuthHeaders(),
    body: JSON.stringify({
      action: 'btw', conversation_id: conversationId,
      agent_name: isAll ? 'ALL' : agent, message: question,
    }),
  }).catch(e => addMsg('error', 'BTW failed: ' + e.message));
  // Response comes via SSE btw_token/btw_done events
}

async function cmdMemoryList(agentFilter, searchQuery) {
  try {
    const body = { action: 'list_memories' };
    if (agentFilter !== undefined && agentFilter !== null) body.agent_name = agentFilter;
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    let mems = data.memories || [];
    // Client-side text search if query provided
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      mems = mems.filter(m => m.text.toLowerCase().includes(q) || (m.tags || []).some(t => t.includes(q)));
    }
    if (mems.length === 0) {
      addMsg('system', 'No memories found.' + (searchQuery ? ' Try a different query.' : ''));
    } else {
      const lines = mems.map(m => {
        const agent = m.agent ? '\u{1F916} ' + m.agent : '\u{1F310} global';
        const tags = m.tags && m.tags.length ? ' [' + m.tags.join(', ') + ']' : '';
        return '\u2022 `' + m.id + '` ' + agent + tags + ' \u2014 ' + m.text;
      });
      const title = searchQuery ? 'Search results' : (agentFilter !== null && agentFilter !== undefined ? 'Memories for ' + (agentFilter || 'global') : 'All memories');
      addMsg('system', title + ' (' + mems.length + '):\n' + lines.join('\n'));
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

function cmdCompact(agentName) {
  if (!conversationId) { addMsg('system', 'No active conversation'); return; }
  contextOpInProgress = true;
  const label = agentName ? 'Compacting (' + agentName + ')' : 'Compacting';
  showContextOp(label);
  const body = { action: 'compact', conversation_id: conversationId };
  if (agentName) body.agent_name = agentName;
  fetch(API, {
    method: 'POST', headers: getAuthHeaders(),
    body: JSON.stringify(body),
  }).then(r => r.json()).then(data => {
    if (data.error) {
      addMsg('error', 'Compaction failed: ' + data.error);
      hideContextOp(); contextOpInProgress = false;
    }
    // status=accepted → compaction runs in background, SSE events will report progress
    // contextOpInProgress stays true until compact_progress done event arrives
  }).catch(e => {
    addMsg('error', 'Compaction failed: ' + e.message);
    hideContextOp(); contextOpInProgress = false;
  });
}

function cmdRebuild(agentName) {
  if (!conversationId) { addMsg('system', t('noConv')); return; }
  contextOpInProgress = true;
  const label = agentName ? 'Rebuilding (' + agentName + ')' : 'Rebuilding';
  showContextOp(label);
  const body = { action: 'rebuild', conversation_id: conversationId };
  if (agentName) body.agent_name = agentName;
  fetch(API, {
    method: 'POST', headers: getAuthHeaders(),
    body: JSON.stringify(body),
  }).then(r => r.json()).then(data => {
    if (data.error) { addMsg('error', 'Rebuild failed: ' + data.error); hideContextOp(); contextOpInProgress = false; }
  }).catch(e => { addMsg('error', 'Rebuild failed: ' + e.message); hideContextOp(); contextOpInProgress = false; });
}

function cmdRebuildClean() {
  if (!conversationId) { addMsg('system', t('noConv')); return; }
  contextOpInProgress = true;
  showContextOp('Rebuilding');
  fetch(API, {
    method: 'POST', headers: getAuthHeaders(),
    body: JSON.stringify({ action: 'rebuild_clean', conversation_id: conversationId }),
  }).then(r => r.json()).then(data => {
    if (data.error) { addMsg('error', 'Rebuild clean failed: ' + data.error); return; }
    addMsg('system', t('rebuiltClean', {messages: data.messages, tokens: data.token_estimate}));
  }).catch(e => addMsg('error', 'Rebuild clean failed: ' + e.message))
    .finally(() => { hideContextOp(); contextOpInProgress = false; });
}

let _ctxAgentFilter = '';  // '' = shared/default, 'grok' = per-agent

async function cmdShowContext(agentName) {
  if (!conversationId) { addMsg('system', t('noConv')); return; }
  if (agentName) _ctxAgentFilter = agentName;
  try {
    const body = { action: 'get_context', conversation_id: conversationId };
    if (_ctxAgentFilter) body.agent_name = _ctxAgentFilter;
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); return; }
    data._agent_filter = _ctxAgentFilter;
    showContextOverlay(data);
  } catch (e) { addMsg('error', 'Failed to load context: ' + e.message); }
}

let _ctxFullData = null;

async function ctxLoadFull() {
  const body = { action: 'get_context_full', conversation_id: conversationId };
  if (_ctxAgentFilter) body.agent_name = _ctxAgentFilter;
  const resp = await fetch(API, {
    method: 'POST', headers: getAuthHeaders(),
    body: JSON.stringify(body),
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

function _buildCtxAgentDropdown(data) {
  const agents = data.agent_contexts || {};
  const names = Object.keys(agents).filter(n => n !== '*').sort();
  console.log('[ctx-dropdown] agents:', JSON.stringify(agents), 'names:', names, 'filter:', _ctxAgentFilter);
  // Always show dropdown if there are per-agent contexts or if a filter is active
  if (names.length === 0 && !_ctxAgentFilter) return '';
  // Ensure current filter is in the list (may not be diverged yet)
  if (_ctxAgentFilter && !names.includes(_ctxAgentFilter)) {
    names.push(_ctxAgentFilter);
    names.sort();
  }
  const sharedStatus = agents['*'] || 'messages';
  const sharedLabel = 'Shared' + (sharedStatus === 'diverged' ? ' \u2733' : '');
  let html = '<select id="ctxAgentFilter" onchange="ctxAgentChanged()" style="background:#1e1e3a;color:#c0c0d0;border:1px solid #444;border-radius:6px;padding:3px 8px;font-size:12px">';
  html += '<option value=""' + (!_ctxAgentFilter ? ' selected' : '') + '>' + sharedLabel + '</option>';
  for (const n of names) {
    const status = agents[n] || 'messages';
    const label = n + (status === 'diverged' ? ' \u2733' : '');
    html += '<option value="' + n + '"' + (_ctxAgentFilter === n ? ' selected' : '') + '>' + label + '</option>';
  }
  html += '</select>';
  return html;
}
async function ctxAgentChanged() {
  _ctxAgentFilter = document.getElementById('ctxAgentFilter').value;
  _ctxFullData = null;
  await cmdShowContext(_ctxAgentFilter);
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
    + _buildCtxAgentDropdown(data)
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

// ── Tool Call Parser ────────────────────────────────────────────
function _parseToolCall(text) {
  // Formats supported:
  //   tool(val1, val2, val3)              — positional (mapped to schema server-side)
  //   tool(key=val, key2=val2)            — named
  //   tool(val1, key2=val2)               — mixed (positional first, then named)
  //   tool {"key": "value"}               — JSON
  //   tool()                              — no args
  const nameMatch = text.match(/^(\w+)/);
  if (!nameMatch) return { error: 'No tool name found' };
  const name = nameMatch[1];
  let rest = text.slice(name.length).trim();

  if (!rest || rest === '()') return { name, args: {}, positional: [] };

  // JSON object format
  if (rest.startsWith('{')) {
    try { return { name, args: JSON.parse(rest), positional: [] }; }
    catch (e) { return { error: 'Invalid JSON: ' + e.message }; }
  }

  // Strip outer parens
  if (rest.startsWith('(') && rest.endsWith(')')) {
    rest = rest.slice(1, -1).trim();
  }
  if (!rest) return { name, args: {}, positional: [] };

  // Tokenize: split on commas respecting quotes and brackets
  const tokens = _splitArgs(rest);
  const args = {};
  const positional = [];

  for (const token of tokens) {
    const eqMatch = token.match(/^(\w+)\s*=\s*([\s\S]*)$/);
    if (eqMatch) {
      // Named: key=value
      args[eqMatch[1]] = _parseValue(eqMatch[2].trim());
    } else {
      // Positional
      positional.push(_parseValue(token.trim()));
    }
  }
  return { name, args, positional };
}

function _splitArgs(s) {
  // Split on commas, respecting quotes, brackets, braces
  const result = [];
  let current = '';
  let depth = 0;  // [] {} depth
  let inStr = null;  // null, '"', "'"
  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    if (inStr) {
      current += c;
      if (c === inStr && s[i - 1] !== '\\') inStr = null;
    } else if (c === '"' || c === "'") {
      current += c;
      inStr = c;
    } else if (c === '[' || c === '{') {
      current += c;
      depth++;
    } else if (c === ']' || c === '}') {
      current += c;
      depth--;
    } else if (c === ',' && depth === 0) {
      result.push(current);
      current = '';
    } else {
      current += c;
    }
  }
  if (current.trim()) result.push(current);
  return result;
}

function _parseValue(v) {
  if (!v) return '';
  // Quoted string
  if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) {
    return v.slice(1, -1).replace(/\\"/g, '"').replace(/\\'/g, "'");
  }
  // JSON array or object
  if (v.startsWith('[') || v.startsWith('{')) {
    try { return JSON.parse(v); } catch(e) { return v; }
  }
  // Booleans / null
  if (v === 'true') return true;
  if (v === 'false') return false;
  if (v === 'null') return null;
  // Numbers
  if (/^\d+$/.test(v)) return parseInt(v);
  if (/^\d+\.\d+$/.test(v)) return parseFloat(v);
  // Bare string (unquoted)
  return v;
}

// ── Agent Memories ──────────────────────────────────────────────
let _memoryCache = [];
let _memoryAgentFilter = null;  // null = all

async function cmdShowMemories() {
  try {
    const body = { action: 'list_memories' };
    if (_memoryAgentFilter !== null) body.agent_name = _memoryAgentFilter;
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    _memoryCache = data.memories || [];
    showMemoryOverlay(_memoryCache);
  } catch (e) { addMsg('error', 'Failed to load memories: ' + e.message); }
}

function showMemoryOverlay(memories) {
  let overlay = document.getElementById('memoryOverlay');
  if (overlay) overlay.remove();
  overlay = document.createElement('div');
  overlay.id = 'memoryOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;z-index:9999';

  // Collect unique agent names for filter
  const agents = [...new Set(memories.map(m => m.agent || ''))].sort();

  // Filter dropdown
  let filterHtml = '<select id="memAgentFilter" onchange="memFilterChanged()" style="background:#1e1e3a;color:#c0c0d0;border:1px solid #444;border-radius:6px;padding:3px 8px;font-size:12px">';
  filterHtml += '<option value="__all__"' + (_memoryAgentFilter === null ? ' selected' : '') + '>All</option>';
  filterHtml += '<option value=""' + (_memoryAgentFilter === '' ? ' selected' : '') + '>Global only</option>';
  for (const a of agents) {
    if (a) filterHtml += '<option value="' + a + '"' + (_memoryAgentFilter === a ? ' selected' : '') + '>' + a + '</option>';
  }
  filterHtml += '</select>';

  // Build memory rows
  let msgsHtml = '';
  if (memories.length === 0) {
    msgsHtml = '<div style="color:#6c6c8a;text-align:center;padding:20px">No memories stored.</div>';
  } else {
    memories.forEach((m, i) => {
      // Scope badge: private (agent+conv), conversation, agent, global
      let scopeBadge;
      if (m.agent && m.conversation_id) {
        scopeBadge = '<span style="background:#5a1a1a;color:#ff6b6b;padding:1px 6px;border-radius:6px;font-size:10px;font-weight:600">\u{1F512} ' + m.agent + '</span>';
      } else if (m.conversation_id) {
        scopeBadge = '<span style="background:#1a3a5a;color:#74b9ff;padding:1px 6px;border-radius:6px;font-size:10px;font-weight:600">\u{1F4AC} conv</span>';
      } else if (m.agent) {
        scopeBadge = '<span style="background:#1e3a5f;color:#4fc3f7;padding:1px 6px;border-radius:6px;font-size:10px;font-weight:600">\u{1F916} ' + m.agent + '</span>';
      } else {
        scopeBadge = '<span style="background:#1b4332;color:#52b788;padding:1px 6px;border-radius:6px;font-size:10px;font-weight:600">\u{1F310} global</span>';
      }
      const tagsHtml = (m.tags || []).map(t =>
        '<span style="background:#2a2a4a;color:#a0a0c0;padding:1px 5px;border-radius:4px;font-size:10px;margin-left:3px">' + t + '</span>'
      ).join('');
      const age = _formatAge(m.updated_at || m.created_at);
      const editBtn = '<button onclick="event.stopPropagation();memEdit(' + i + ')" style="background:none;border:none;color:#4fc3f7;cursor:pointer;font-size:13px;padding:0 3px" title="Edit">&#9998;</button>';
      const delBtn = '<button onclick="event.stopPropagation();memDelete(\'' + m.id + '\')" style="background:none;border:none;color:#e74c3c;cursor:pointer;font-size:13px;padding:0 3px" title="Delete">&#128465;</button>';
      const text = (m.text || '').replace(/</g, '&lt;').replace(/>/g, '&gt;');
      msgsHtml += '<div id="mem-row-' + i + '" style="padding:6px 8px;border-bottom:1px solid #222;cursor:pointer" onclick="this.querySelector(\'.mem-full\')&&(this.querySelector(\'.mem-full\').style.display=this.querySelector(\'.mem-full\').style.display===\'block\'?\'none\':\'block\')">'
        + '<div style="display:flex;align-items:center;gap:4px">' + scopeBadge + tagsHtml
        + '<span style="color:#6c6c8a;font-size:10px;margin-left:auto">' + age + '</span>'
        + editBtn + delBtn + '</div>'
        + '<div style="color:#c0c0d0;font-size:12px;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">' + text.slice(0, 200) + '</div>'
        + '<div class="mem-full" style="display:none;color:#a0a0c0;font-size:12px;margin-top:4px;white-space:pre-wrap;word-break:break-word;max-height:200px;overflow-y:auto">' + text + '</div>'
        + '</div>';
    });
  }

  overlay.innerHTML = '<div style="background:#1a1a2e;border:1px solid #333;border-radius:12px;padding:20px;max-width:700px;width:90%;max-height:80vh;display:flex;flex-direction:column">'
    + '<div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">'
    + '<h3 style="margin:0;color:#e0e0e0;font-size:16px">Agent Memories</h3>'
    + '<span style="color:#6c6c8a;font-size:12px">' + memories.length + ' entries</span>'
    + filterHtml
    + '<button onclick="memAddNew()" style="background:#1e3a5f;color:#4fc3f7;border:none;border-radius:6px;padding:3px 10px;cursor:pointer;font-size:11px;font-weight:600;margin-left:auto">+ Add</button>'
    + '<button onclick="document.getElementById(\'memoryOverlay\').remove()" style="background:none;border:none;color:#aaa;cursor:pointer;font-size:18px">&times;</button>'
    + '</div>'
    + '<div id="mem-list" style="flex:1;overflow-y:auto;border:1px solid #222;border-radius:8px;background:#0d1117">' + msgsHtml + '</div>'
    + '</div>';
  document.body.appendChild(overlay);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
}

function _formatAge(ts) {
  const s = Math.floor(Date.now() / 1000 - ts);
  if (s < 60) return 'just now';
  if (s < 3600) return Math.floor(s / 60) + 'm ago';
  if (s < 86400) return Math.floor(s / 3600) + 'h ago';
  return Math.floor(s / 86400) + 'd ago';
}

async function memFilterChanged() {
  const val = document.getElementById('memAgentFilter').value;
  _memoryAgentFilter = val === '__all__' ? null : val;
  await cmdShowMemories();
}

async function memDelete(memId) {
  if (!confirm('Delete this memory?')) return;
  try {
    await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'delete_memory', memory_id: memId }),
    });
    await cmdShowMemories();
  } catch (e) { addMsg('error', e.message); }
}

function memEdit(idx) {
  const m = _memoryCache[idx];
  if (!m) return;
  const row = document.getElementById('mem-row-' + idx);
  if (!row) return;
  row.innerHTML = '<div style="padding:4px">'
    + '<textarea id="mem-edit-text" style="width:100%;min-height:60px;background:#0d1117;color:#c0c0d0;border:1px solid #444;border-radius:4px;padding:4px;font-size:12px;resize:vertical">' + (m.text || '').replace(/</g, '&lt;').replace(/>/g, '&gt;') + '</textarea>'
    + '<div style="display:flex;gap:6px;margin-top:4px;align-items:center">'
    + '<label style="color:#6c6c8a;font-size:11px">Tags:</label>'
    + '<input id="mem-edit-tags" value="' + (m.tags || []).join(', ') + '" style="flex:1;background:#0d1117;color:#c0c0d0;border:1px solid #444;border-radius:4px;padding:2px 6px;font-size:11px">'
    + '<label style="color:#6c6c8a;font-size:11px">Agent:</label>'
    + '<input id="mem-edit-agent" value="' + (m.agent || '') + '" placeholder="(global)" style="width:80px;background:#0d1117;color:#c0c0d0;border:1px solid #444;border-radius:4px;padding:2px 6px;font-size:11px">'
    + '<button onclick="memSaveEdit(\'' + m.id + '\')" style="background:#1b4332;color:#52b788;border:none;border-radius:4px;padding:3px 10px;cursor:pointer;font-size:11px">Save</button>'
    + '<button onclick="cmdShowMemories()" style="background:#333;color:#aaa;border:none;border-radius:4px;padding:3px 10px;cursor:pointer;font-size:11px">Cancel</button>'
    + '</div></div>';
}

async function memSaveEdit(memId) {
  const text = document.getElementById('mem-edit-text').value.trim();
  const tagsRaw = document.getElementById('mem-edit-tags').value;
  const agent = document.getElementById('mem-edit-agent').value.trim();
  const tags = tagsRaw.split(',').map(t => t.trim()).filter(t => t);
  try {
    await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'edit_memory', memory_id: memId, text, tags, agent }),
    });
    await cmdShowMemories();
  } catch (e) { addMsg('error', e.message); }
}

function memAddNew() {
  const list = document.getElementById('mem-list');
  if (!list) return;
  const form = document.createElement('div');
  form.style.cssText = 'padding:8px;border-bottom:1px solid #444;background:#1a1a2e';
  form.innerHTML = '<textarea id="mem-new-text" placeholder="Memory text..." style="width:100%;min-height:50px;background:#0d1117;color:#c0c0d0;border:1px solid #444;border-radius:4px;padding:4px;font-size:12px;resize:vertical"></textarea>'
    + '<div style="display:flex;gap:6px;margin-top:4px;align-items:center">'
    + '<label style="color:#6c6c8a;font-size:11px">Tags:</label>'
    + '<input id="mem-new-tags" placeholder="tag1, tag2" style="flex:1;background:#0d1117;color:#c0c0d0;border:1px solid #444;border-radius:4px;padding:2px 6px;font-size:11px">'
    + '<label style="color:#6c6c8a;font-size:11px">Agent:</label>'
    + '<input id="mem-new-agent" placeholder="(global)" style="width:80px;background:#0d1117;color:#c0c0d0;border:1px solid #444;border-radius:4px;padding:2px 6px;font-size:11px">'
    + '<button onclick="memSaveNew()" style="background:#1b4332;color:#52b788;border:none;border-radius:4px;padding:3px 10px;cursor:pointer;font-size:11px">Add</button>'
    + '</div>';
  list.insertBefore(form, list.firstChild);
  document.getElementById('mem-new-text').focus();
}

async function memSaveNew() {
  const text = document.getElementById('mem-new-text').value.trim();
  if (!text) return;
  const tagsRaw = document.getElementById('mem-new-tags').value;
  const agent = document.getElementById('mem-new-agent').value.trim();
  const tags = tagsRaw.split(',').map(t => t.trim()).filter(t => t);
  try {
    await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'add_memory', text, tags, agent }),
    });
    await cmdShowMemories();
  } catch (e) { addMsg('error', e.message); }
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
    const available = files.filter(f => f.available);
    if (!available.length) {
      list.innerHTML = '<span style="color:#555;font-size:12px">No files</span>';
      return;
    }
    list.innerHTML = '';
    for (const f of available) {
      const href = window.location.origin + '/files/' + f.file_id + '/' + f.filename;
      const chip = document.createElement('span');
      chip.className = 'file-chip';
      chip.innerHTML = `<span class="file-status available" title="Available"></span><a href="${href}" target="_blank" title="Download">${escapeHtml(f.filename)}</a>`;
      chip.addEventListener('contextmenu', (e) => showFileMenu(e, f.file_id, f.filename));
      list.appendChild(chip);
    }
  } catch (e) {
    list.innerHTML = '<span style="color:#e94560;font-size:12px">Failed to load files</span>';
  }
}

// ── File context menu ──────────────────────────────────────────
function showFileMenu(e, fileId, filename) {
  e.preventDefault();
  closeFileMenu();
  const menu = document.createElement('div');
  menu.className = 'ctx-menu';
  menu.id = 'fileCtxMenu';
  menu.style.left = e.clientX + 'px';
  menu.style.top = e.clientY + 'px';
  const href = window.location.origin + '/files/' + fileId + '/' + filename;
  menu.innerHTML =
    '<div class="ctx-menu-item" onclick="event.stopPropagation();openFileViewer(\'' + href + '\');closeFileMenu();">&#x1F441; View</div>' +
    '<div class="ctx-menu-item" onclick="event.stopPropagation();window.open(\'' + href + '\',\'_blank\');closeFileMenu();">&#x2B07; Download</div>' +
    '<div class="ctx-menu-item danger" onclick="event.stopPropagation();deleteFile(\'' + fileId + '\');closeFileMenu();">&#x1F5D1; Delete</div>';
  document.body.appendChild(menu);
  setTimeout(() => document.addEventListener('click', closeFileMenu, {once: true}), 0);
}

function closeFileMenu() {
  const m = document.getElementById('fileCtxMenu');
  if (m) m.remove();
}

async function deleteFile(fileId) {
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'delete_file', file_id: fileId, conversation_id: conversationId }),
      credentials: 'same-origin',
    });
    const data = await resp.json();
    if (data.ok) {
      loadConvFiles();
    } else {
      addMsg('system', 'Delete failed: ' + (data.error || 'unknown'));
    }
  } catch (e) {
    addMsg('system', 'Delete failed: ' + e.message);
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
  // Get text content only (strip badges, time, actions, meta)
  const clone = msg.cloneNode(true);
  for (const sel of ['.msg-actions', '.source-badge', '.msg-time', '.msg-meta']) {
    const el = clone.querySelector(sel);
    if (el) el.remove();
  }
  let text = (clone.textContent || clone.innerText).trim();
  // Strip target badge prefix like "[→ assistant] " or "[btw → agent] "
  text = text.replace(/^\[(btw\s*)?\u2192\s*[^\]]+\]\s*/, '');
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

async function cancelAgent(target) {
  if (!conversationId) return;
  document.getElementById('stopBtn').style.display = 'none';
  document.getElementById('status').textContent = t('cancelling');
  const body = { action: 'cancel', conversation_id: conversationId };
  if (target && target !== 'ALL') body.agent_name = target;
  try {
    await fetch(API, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify(body),
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

  // Block sends while context operation is in progress
  if (contextOpInProgress) {
    addMsg('system', t('contextOpBusy'));
    return;
  }

  // Save to message history (before slash command intercept so commands are in history too)
  if (text) {
    messageHistory.unshift(text);
    if (messageHistory.length > 50) messageHistory.pop();
    localStorage.setItem('pyfi2_msg_history', JSON.stringify(messageHistory.slice(0, 50)));
  }
  historyIndex = -1;
  savedDraft = '';

  // Intercept slash commands
  if (text.startsWith('/')) {
    const handled = await handleSlashCommand(text);
    if (handled) { input.value = ''; input.style.height = 'auto'; input.focus(); return; }
  }

  // Capture and clear attachments
  const attachments = pendingFiles.map(f => ({
    filename: f.filename, mime_type: f.mime_type, data: f.data,
  }));
  const attachmentsForDisplay = [...pendingFiles];
  pendingFiles = [];
  renderAttachments();

  // Allow stacking: don't block on 'sending', just track pending count
  sending = true;
  lastSSEActivity = Date.now();
  document.getElementById('status').textContent = t('sending');
  input.value = '';
  input.style.height = 'auto';

  // Show user message with target badge (all messages explicitly show who they go to)
  const targetAgent = selectedAgent || 'assistant';
  const userSource = { type: 'user', name: '', target_agent: targetAgent };
  const msgEl = addMsg('user', text || '', { source: userSource });
  if (attachmentsForDisplay.length > 0) {
    msgEl.innerHTML = sourceBadge(userSource) + escapeHtml(text || '') + renderUserAttachments(attachmentsForDisplay);
  }
  scrollBottom(true);  // Force scroll when user sends
  clearStream(targetAgent);
  showTyping();

  try {
    const body = { message: text, target_agent: targetAgent };
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
    const scopeBadge = (s) => {
      if (!s) return '';
      const colors = { global: '#2d5a8e', user: '#5a2d8e', conversation: '#8e5a2d' };
      const labels = { global: 'G', user: 'U', conversation: 'C' };
      return `<span style="font-size:9px;padding:0 3px;border-radius:3px;background:${colors[s]||'#444'};color:#ccc;margin-right:3px;" title="${s}">${labels[s]||s[0]}</span>`;
    };
    let html = '';
    // Agents
    if (data.agents && data.agents.length) {
      html += '<div style="margin-bottom:4px;color:#6c5ce7;font-weight:600;">Agents</div>';
      data.agents.forEach(a => {
        const active = a.active;
        html += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;">
          <span style="cursor:pointer;font-size:11px;" onclick="cmdResourceAction('${active ? 'deactivate_resource' : 'activate_resource'}',{resource_type:'agent',name:'${a.name}'}).then(loadResources)">${active ? '\u2705' : '\u2B1C'}</span>
          ${scopeBadge(a.scope)}<span style="color:${active ? '#e0e0e0' : '#666'};font-size:12px;">${a.name}</span>
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
          ${scopeBadge(s.scope)}<span style="color:${active ? '#e0e0e0' : '#666'};font-size:12px;">${s.name}</span>
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
          ${scopeBadge(m.scope)}<span style="color:${active ? '#e0e0e0' : '#666'};font-size:12px;">${m.name}</span>
        </div>`;
      });
    }
    // Task definitions
    if (data.task_defs && data.task_defs.length) {
      html += '<div style="margin-bottom:4px;color:#6c5ce7;font-weight:600;">Tasks</div>';
      data.task_defs.forEach(t => {
        html += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;">
          ${scopeBadge(t.scope)}<span style="color:#8888aa;font-size:12px;" title="${escapeHtml(t.description)}">${t.name}</span>
          <span style="color:#555;font-size:10px;">[${t.default_interval}]</span>
        </div>`;
      });
    }
    if (!html) html = '<div style="color:#555;font-size:11px;">No resources. Use /agent create, /add-skill, /task create</div>';
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
        <a id="viewerDownload" download style="background:#6c5ce7;color:#fff;text-decoration:none;font-size:13px;padding:4px 12px;border-radius:4px;cursor:pointer;display:inline-block;">\u2B07 Download</a>
        <button onclick="closeFileViewer()" style="background:#ff6b6b;border:none;color:#fff;font-size:13px;padding:4px 10px;border-radius:4px;cursor:pointer;">\u2715</button>
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
  dlEl.download = fname;
  contentEl.innerHTML = '<p style="color:#888;">Loading...</p>';

  // All file fetches go through authenticated fetch to avoid auth redirects
  const authHeaders = {};
  const token = getToken();
  if (token) authHeaders['Authorization'] = 'Bearer ' + token;

  fetch(url, { headers: authHeaders, credentials: 'same-origin' }).then(r => {
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const ct = r.headers.get('content-type') || '';
    sizeEl.textContent = r.headers.get('content-length')
      ? (parseInt(r.headers.get('content-length')) / 1024).toFixed(1) + ' KB' : '';
    // Create blob for download button
    return r.blob().then(blob => {
      const blobUrl = URL.createObjectURL(blob);
      dlEl.href = blobUrl;
      // Render based on type
      if (['png','jpg','jpeg','gif','svg','webp','bmp'].includes(ext) || ct.startsWith('image/')) {
        contentEl.innerHTML = '<img src="' + blobUrl + '" style="max-width:100%;max-height:40vh;object-fit:contain;">';
      } else if (ext === 'pdf' || ct === 'application/pdf') {
        contentEl.innerHTML = '<iframe src="' + blobUrl + '" style="width:100%;height:40vh;border:none;"></iframe>';
      } else if (ext === 'html' || ct === 'text/html') {
        contentEl.innerHTML = '<iframe src="' + blobUrl + '" sandbox="allow-same-origin" style="width:100%;height:40vh;border:none;background:#fff;"></iframe>';
      } else {
        // Text/code preview
        blob.text().then(text => {
          sizeEl.textContent = (text.length / 1024).toFixed(1) + ' KB';
          contentEl.innerHTML = '<pre style="margin:0;white-space:pre-wrap;word-break:break-all;color:#ddd;font-size:13px;font-family:monospace;">' + text.replace(/</g,'&lt;').replace(/>/g,'&gt;') + '</pre>';
        });
      }
    });
  }).catch((err) => {
    contentEl.innerHTML = '<p style="color:#ff6b6b;">Could not load file: ' + escapeHtml(err.message) + '</p>';
    dlEl.href = '#';
  });
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
updateActiveAgentBadge();
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

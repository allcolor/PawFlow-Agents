/* chat.js — Core chat functionality */

var vscode = acquireVsCodeApi();
var messagesEl = document.getElementById('messages');
var inputEl = document.getElementById('input');
var statusEl = document.getElementById('status');
var streaming = {};
var currentHistoryConvId = null;
var currentHistoryOffset = 0;
var _hadToolCalls = false;
var _lastToolCall = '';
var activeAgents = {};
var _resData = null;
var _replyTo = null; // {raw_index, role, agent, text_preview}
var _msgRawIndex = 0; // tracks raw message index for reply-to

function openFileInEditor(filePath) {
  vscode.postMessage({ type: 'openFile', path: filePath });
}
function fileLink(path) {
  var fname = path.split('/').pop() || path;
  return '<a href="#" style="color:#6c5ce7;text-decoration:underline;cursor:pointer" onclick="event.preventDefault();openFileInEditor(\'' + esc(path).replace(/'/g, "\\'") + '\')">' + esc(fname) + '</a>';
}
var thinkingBlocks = {}; // agentKey → {el, content, summary, text, start}
var streamEls = {};      // agentKey → live stream element

function finalizeThinking(agentName) {
  var aKey = (agentName || '').toLowerCase();
  var tb = thinkingBlocks[aKey];
  if (tb) {
    var elapsed = (Date.now() - tb.start) / 1000;
    // Remove empty thinking blocks — keep all that have content
    if (!tb.text.trim()) {
      tb.el.remove();
    } else {
      tb.summary.textContent = 'Thought for ' + elapsed.toFixed(1) + 's';
      tb.el.removeAttribute('open');
    }
    delete thinkingBlocks[aKey];
  }
}

function getOrCreateStream(agentName) {
  var aKey = (agentName || '').toLowerCase();
  if (!streamEls[aKey]) {
    var el = document.createElement('div');
    el.className = 'msg assistant streaming-live';
    el.style.cssText = 'white-space:pre-wrap;opacity:0.85;';
    messagesEl.appendChild(el);
    streamEls[aKey] = el;
  }
  return streamEls[aKey];
}

function removeStreamEl(agentName) {
  var aKey = (agentName || '').toLowerCase();
  var el = streamEls[aKey];
  if (el) {
    el.remove();
    delete streamEls[aKey];
  }
}

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
  bar.innerHTML = '\u21a9 <strong>' + esc(agent) + '</strong>: "' + esc(rawText.substring(0, 80)) + '..."'
    + '<button class="reply-close" onclick="cancelReply()">\u2715</button>';
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

// ── Active agents — server poll is single source of truth ──
// SSE events provide optimistic removal on done/cancelled only.
// All agent additions come from list_active polling (like webchat).
var _activeSyncTimer = null;

function _agentKey(name) { return (name || '').toLowerCase(); }

function updateActiveAgents(agent, status) {
  if (!agent) return;
  if (status === 'done' || status === 'cancelled') {
    // Optimistic removal — server confirms on next poll
    var key = _agentKey(agent);
    delete activeAgents[key];
    _renderActiveAgents();
  }
  // Other statuses are NO-OPs — server poll is the source of truth
}

function startActiveSync() {
  if (_activeSyncTimer) return;
  _activeSyncTimer = setInterval(_syncActiveFromServer, 3000);
}
function stopActiveSync() {
  if (_activeSyncTimer) { clearInterval(_activeSyncTimer); _activeSyncTimer = null; }
  activeAgents = {};
  _renderActiveAgents();
}

function _syncActiveFromServer() {
  if (!currentHistoryConvId) return;
  sendCmd('list_active', '');
}

function _handleListActiveResult(data) {
  var serverActive = (data && data.active) || [];
  var serverKeys = {};
  for (var i = 0; i < serverActive.length; i++) {
    var a = serverActive[i];
    var k = a.task_id ? _agentKey(a.agent_name + '::' + a.task_id) : _agentKey(a.agent_name);
    serverKeys[k] = true;
  }
  // Remove agents server doesn't know about
  for (var key in activeAgents) {
    if (!serverKeys[key]) delete activeAgents[key];
  }
  // Add/update from server
  var now = Date.now();
  for (var j = 0; j < serverActive.length; j++) {
    var sa = serverActive[j];
    var sk = sa.task_id ? _agentKey(sa.agent_name + '::' + sa.task_id) : _agentKey(sa.agent_name);
    var existing = activeAgents[sk] || {};
    activeAgents[sk] = {
      name: sa.agent_name,
      taskId: sa.task_id || '',
      startedAt: existing.startedAt || now - ((sa.duration_s || 0) * 1000),
      iteration: sa.iteration || (existing.iteration || 0),
      round: sa.round || 0,
      maxRounds: sa.max_rounds || 0,
      lastTool: sa.last_tool || (existing.lastTool || ''),
      totalTools: sa.total_tools || (existing.totalTools || 0),
      status: sa.status || (existing.status || 'thinking'),
      msgPreview: sa.message_preview || '',
    };
  }
  _renderActiveAgents();
}

function _renderActiveAgents() {
  var el = document.getElementById('activeAgents');
  var keys = Object.keys(activeAgents);
  if (keys.length === 0) {
    el.style.display = 'none';
    // Don't clear statusEl — other handlers (done, compact) set their own status
    return;
  }
  el.style.display = 'block';
  var now = Date.now();
  el.innerHTML = keys.map(function(key) {
    var info = activeAgents[key];
    var displayName = info.name;
    if (info.taskId) displayName += ' [task:' + info.taskId + ']';
    var color = agentColor(info.name);
    // Elapsed time
    var secs = Math.round((now - (info.startedAt || now)) / 1000);
    var timeStr = secs < 60 ? secs + 's' : Math.floor(secs / 60) + 'm' + (secs % 60) + 's';
    // Status details
    var parts = [];
    if (info.iteration) parts.push('iter ' + info.iteration);
    if (info.round && info.maxRounds > 1) parts.push('round ' + info.round + '/' + info.maxRounds);
    if (info.totalTools > 0) parts.push(info.totalTools + ' tools');
    if (info.lastTool) parts.push('[' + info.lastTool + ']');
    var statusText = parts.length > 0 ? parts.join(' \u00b7 ') : 'thinking...';
    return '<div style="display:flex;align-items:center;gap:4px;padding:1px 0">'
      + '<span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:' + color + ';animation:pulse 1.2s ease-in-out infinite"></span>'
      + '<strong style="color:' + color + '">' + esc(displayName) + '</strong>'
      + '<span style="color:var(--vscode-descriptionForeground)">' + esc(statusText) + '</span>'
      + '<span style="color:var(--vscode-descriptionForeground);margin-left:auto">' + timeStr + '</span>'
      + '<button title="Interrupt" onclick="sendCmd(\'interrupt\',JSON.stringify({agent_name:\'' + esc(info.name).replace(/'/g, "\\'") + '\'' + (info.taskId ? ',task_id:\'' + esc(info.taskId).replace(/'/g, "\\'") + '\'' : '') + '}))" style="background:none;border:none;color:var(--vscode-descriptionForeground);cursor:pointer;font-size:10px;padding:0 2px" title="Interrupt">\u23F8</button>'
      + '<button title="Stop" onclick="_stopAgent(\'' + esc(info.name).replace(/'/g, "\\'") + '\',\'' + esc(info.taskId || '').replace(/'/g, "\\'") + '\')" style="background:none;border:none;color:#e94560;cursor:pointer;font-size:10px;padding:0 2px">\u25A0</button>'
      + '</div>';
  }).join('');
  // Update status bar
  var agentParts = keys.map(function(key) {
    var info = activeAgents[key];
    return info.name + (info.lastTool ? ' [' + info.lastTool + ']' : '');
  });
  statusEl.innerHTML = '<span class="thinking">' + randomVerb() + '... ' + esc(agentParts.join(', ')) + '</span>';
}

function _stopAgent(agentName, taskId) {
  sendCmd('cancel', JSON.stringify({ agent_name: agentName, force: true, task_id: taskId || undefined }));
  var key = taskId ? _agentKey(agentName + '::' + taskId) : _agentKey(agentName);
  delete activeAgents[key];
  _renderActiveAgents();
}

function deleteMsg(btn) {
  var msgEl = btn.closest('.msg');
  var rawIndex = msgEl.dataset.rawIndex;
  var index = rawIndex !== undefined ? parseInt(rawIndex) : Array.from(messagesEl.children).indexOf(msgEl);
  vscode.postMessage({ type: 'command', command: 'delete_message', arg: JSON.stringify({ index: index }) });
  msgEl.remove();
}

var FUN_VERBS = ['Refactoring','Compiling','Debugging','Contemplating','Bamboozling',
  'Rickrolling','Skedaddling','Philosophizing','Defenestrating','Hocus-pocusing'];
function randomVerb() { return FUN_VERBS[Math.floor(Math.random() * FUN_VERBS.length)]; }

var AGENT_COLORS = ['#4ecdc4','#4fc3f7','#ab47bc','#f4a261','#e94560','#3fb950','#58a6ff','#d4a373'];
function agentColor(name) {
  var h = 0;
  for (var i = 0; i < name.length; i++) h += name.charCodeAt(i);
  return AGENT_COLORS[h % AGENT_COLORS.length];
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

  // Client-side msg_id so the SSE new_message echo dedups against this
  // locally rendered copy (same scheme as the webchat).
  var msgId = 'vsc' + Date.now().toString(16) + Math.random().toString(16).slice(2, 10);
  var meta = _replyTo ? { source: { reply_to: _replyTo }, msg_id: msgId } : { msg_id: msgId };
  addMsg('user', text, meta);
  var msg = { type: 'sendMessage', text: text, msg_id: msgId };
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

function newChat() {
  closePanel();
  stopActiveSync();
  vscode.postMessage({ type: 'newConversation' });
  messagesEl.innerHTML = '<div class="msg system">New conversation</div>';
  currentHistoryConvId = null;
  currentHistoryOffset = 0;
  setActiveTab('tbChat');
}
function loadConvs() { closePanel(); setActiveTab('tbConvs'); vscode.postMessage({ type: 'loadConversations' }); }
function sendCmd(cmd, arg) { vscode.postMessage({ type: 'command', command: cmd, arg: arg }); }

function esc(s) { var d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

function renderMd(text) {
  return text
    .replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\*([^*]+)\*/g, '<em>$1</em>')
    .replace(/^- (.+)$/gm, '\u2022 $1')
    .replace(/^#{1,3} (.+)$/gm, '<strong>$1</strong>')
    .replace(/(https?:\/\/[^\s]+\/files\/[^\s]+\.(png|jpg|jpeg|gif|webp|svg))/gi, '<img src="$1" style="max-width:100%;max-height:300px;border-radius:4px;margin:4px 0" />')
    .replace(/\n/g, '<br>');
}

function renderToolResult(content, toolHint, pathHint) {
  var text = content.replace(/\[TOOL OUTPUT[^\]]*\]\n?/g, '').replace(/\n\[\/TOOL OUTPUT\]/g, '');
  var lines = text.split('\n');

  // Detect diff (has +/- lines with context)
  var hasDiff = lines.some(function(l) { return l.trimStart().startsWith('+ ') || l.trimStart().startsWith('- '); });
  if (hasDiff && (text.includes('replacement') || text.includes('Edited ') || text.includes('Written ')
      || text.includes('@@') || text.includes('diff ') || lines.some(function(l) { return l.startsWith('---') || l.startsWith('+++'); }))) {
    return '<pre class="diff">' + lines.map(function(l) {
      var s = l.trimStart();
      if (s.startsWith('+ ') || s.match(/^\d+\s+\+ /)) return '<span class="diff-add">' + esc(l) + '</span>';
      if (s.startsWith('- ') || s.match(/^\d+\s+- /)) return '<span class="diff-del">' + esc(l) + '</span>';
      if (s.startsWith('@@')) return '<span class="diff-hunk">' + esc(l) + '</span>';
      return '<span class="diff-ctx">' + esc(l) + '</span>';
    }).join('\n') + '</pre>';
  }

  // Detect file read (line numbers: "  42\tcontent")
  var lnCount = lines.filter(function(l) { return /^\s*\d+\t/.test(l); }).length;
  if (lines.length > 3 && lnCount > lines.length * 0.5) {
    return '<pre style="font-size:11px;white-space:pre-wrap;max-height:300px;overflow-y:auto">' + lines.map(function(l) {
      var m = l.match(/^(\s*\d+)\t(.*)$/);
      if (m) return '<span style="color:#6e7681;user-select:none">' + esc(m[1]) + '</span>\t' + esc(m[2]);
      return esc(l);
    }).join('\n') + '</pre>';
  }

  // Detect JSON
  var trimmed = text.trim();
  if ((trimmed.charAt(0) === '{' && trimmed.charAt(trimmed.length - 1) === '}')
      || (trimmed.charAt(0) === '[' && trimmed.charAt(trimmed.length - 1) === ']')) {
    return '<pre style="font-size:11px;white-space:pre-wrap;max-height:300px;overflow-y:auto">' + esc(text) + '</pre>';
  }

  // Grep/glob results: color file:line: locations
  if (toolHint === 'grep' || toolHint === 'glob') {
    return '<pre style="font-size:11px;white-space:pre-wrap;max-height:300px;overflow-y:auto">' + lines.map(function(l) {
      var m = l.match(/^([^:]+:\d+:)\s*(.*)$/);
      if (m) return '<span style="color:#58a6ff">' + esc(m[1]) + '</span> ' + esc(m[2]);
      return esc(l);
    }).join('\n') + '</pre>';
  }

  if (text.length > 300) {
    var firstLine = esc(text.split('\n')[0].slice(0, 200));
    return '<details><summary style="cursor:pointer">' + firstLine + '</summary><pre style="font-size:11px;color:#8b949e;white-space:pre-wrap;max-height:300px;overflow-y:auto">' + esc(text) + '</pre></details>';
  }
  return esc(text);
}

var _seenMsgIds = {};
function addMsg(type, content, meta) {
  var msgId = (meta && meta.msg_id) || '';
  if (msgId) {
    if (_seenMsgIds[msgId]) return null;
    _seenMsgIds[msgId] = true;
  }
  var div = document.createElement('div');
  div.className = 'msg ' + type;
  // dataset.msgid makes the element findable for DOM-based dedup
  // (new_message / done) and message_meta footer updates.
  if (msgId) div.dataset.msgid = msgId;

  var rawIdx = meta && meta.raw_index !== undefined ? meta.raw_index : _msgRawIndex++;
  div.dataset.rawIndex = rawIdx;
  div.dataset.rawText = (content || '').substring(0, 200);

  var actionsHtml = '';
  if (type === 'user' || type === 'assistant') {
    actionsHtml = '<span class="msg-actions">'
      + '<button onclick="setReplyTo(this)" title="Reply">\u21a9</button>'
      + '<button onclick="deleteMsg(this)" title="Delete">&times;</button>'
      + '</span>';
  }

  var replyQuoteHtml = '';
  var replySource = (meta && meta.source && meta.source.reply_to) || (meta && meta.reply_to);
  if (replySource && replySource.text_preview) {
    var rtAgent = replySource.agent || replySource.role || '';
    var rtPreview = replySource.text_preview.substring(0, 100);
    var rtIdx = replySource.raw_index !== undefined ? replySource.raw_index : -1;
    replyQuoteHtml = '<div class="reply-quote"' + (rtIdx >= 0 ? ' onclick="scrollToMsg(' + rtIdx + ')"' : '') + '>'
      + '\u21a9 ' + esc(rtAgent) + ': "' + esc(rtPreview) + '"</div>';
  }

  if (type === 'user') {
    div.innerHTML = actionsHtml + replyQuoteHtml + esc(content);
  } else if (type === 'assistant') {
    var agent = (meta && meta.agent_name) || (meta && meta.source && meta.source.name) || '';
    var svc = (meta && meta.source && meta.source.llm_service) || '';
    var color = agentColor(agent);
    div.innerHTML = actionsHtml + replyQuoteHtml + '<span class="agent-badge" style="background:' + color + '">'
      + esc(agent) + (svc ? ' via ' + esc(svc) : '') + '</span>' + renderMd(content);
  } else if (type === 'tool_call') {
    // Check if content has diff lines (edit preview)
    if (content.indexOf('\n- ') !== -1 || content.indexOf('\n+ ') !== -1) {
      var tcLines = content.split('\n');
      var tcHeader = esc(tcLines[0]);
      var tcDiff = tcLines.slice(1).map(function(l) {
        if (l.startsWith('+ ')) return '<span style="color:#3fb950">' + esc(l) + '</span>';
        if (l.startsWith('- ')) return '<span style="color:#f85149">' + esc(l) + '</span>';
        return '<span style="color:#8b949e">' + esc(l) + '</span>';
      }).join('\n');
      div.innerHTML = '&#9998; ' + tcHeader + (tcDiff ? '<pre style="margin:2px 0 0 0;font-size:11px">' + tcDiff + '</pre>' : '');
    } else {
      div.innerHTML = '&#9889; ' + esc(content);
    }
  } else if (type === 'tool_result') {
    div.innerHTML = '&#10003; ' + renderToolResult(content);
  } else if (type === 'thinking') {
    div.className = 'msg thinking-block';
    div.innerHTML = '<details><summary>Thought</summary><pre>' + esc(content) + '</pre></details>';
  } else if (type === 'error') {
    var errAgent = (meta && meta.agent_name) || (meta && meta.source && meta.source.name) || '';
    var errBadge = errAgent ? '<span class="agent-badge" style="background:#e94560">' + esc(errAgent) + '</span>' : '';
    div.innerHTML = errBadge + renderMd(content);
  } else if (type === 'sub_agent_trace') {
    var src = (meta && meta.source) || {};
    var trace = (meta && meta.trace) || [];
    var traceId = (meta && meta.trace_id) || '';
    var trAgent = src.name || '?';
    var parent = src.parent_agent || '?';
    var depth = src.depth || 0;
    var doneEntry = null;
    for (var ti = trace.length - 1; ti >= 0; ti--) {
      if (trace[ti].type === 'done') { doneEntry = trace[ti]; break; }
    }
    var toolCount = (doneEntry && doneEntry.tools_called ? doneEntry.tools_called : []).length;
    var tokIn = (doneEntry && doneEntry.tokens_in) || 0;
    var tokOut = (doneEntry && doneEntry.tokens_out) || 0;
    var summary = parent + ' \u2192 ' + trAgent + ' (' + toolCount + ' tools, ' + tokIn + '\u2191 ' + tokOut + '\u2193)';
    var bodyLines = trace.map(function(e) {
      if (e.type === 'iteration') return '<div class="trace-entry">iter ' + e.iteration + ' \u00b7 ' + (e.total_tools || 0) + ' tools</div>';
      if (e.type === 'tool_call') return '<div class="trace-entry tool">\u26a1 ' + esc(e.tool || '?') + '</div>';
      if (e.type === 'done') return '<div class="trace-entry done">\u2713 ' + esc(e.status || '?') + ' (' + (e.tokens_in || 0) + '\u2191 ' + (e.tokens_out || 0) + '\u2193)</div>';
      return '';
    }).join('');
    var contentHtml = content ? '<div class="trace-content">' + renderMd(content) + '</div>' : '';
    div.className = 'sub-trace';
    div.dataset.traceId = traceId;
    div.style.marginLeft = (depth * 12) + 'px';
    div.innerHTML = '<div class="sub-trace-header" onclick="this.nextElementSibling.classList.toggle(\'open\')">'
      + '\u25b6 ' + esc(summary) + '</div>'
      + '<div class="sub-trace-body">' + bodyLines + contentHtml + '</div>';
  } else {
    div.textContent = content;
  }
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return div;
}

var _TOOL_DISPLAY = {
  bash:'Bash',read:'Read',write:'Write',edit:'Update',glob:'Glob',grep:'Grep',
  delete:'Delete',mkdir:'Mkdir',stat:'Stat',exists:'Exists',list_dir:'ListDir',
  batch_edit:'BatchEdit',apply_patch:'ApplyPatch',find_replace:'FindReplace',
  notebook_edit:'NotebookEdit',copy:'Copy',execute_script:'Script',
  web_search:'WebSearch',fetch:'Fetch',
  generate_image:'ImageGen',remember:'Remember',recall:'Recall',
  delegate:'Delegate',show_file:'ShowFile',get_tool_schema:'GetToolSchema',
};

function _toolSummary(name, args) {
  var display = _TOOL_DISPLAY[name] || name;
  var s = '';
  if (name === 'bash' || name === 'execute_script') s = args.command || args.code || '';
  else if (['read','write','edit','delete','stat','exists','mkdir','list_dir'].indexOf(name) >= 0) s = args.path || '';
  else if (name === 'glob') s = args.pattern || '';
  else if (name === 'grep') s = (args.pattern || '') + (args.path ? ', ' + args.path : '');
  else if (name === 'web_search') s = args.query || '';
  else if (name === 'fetch') s = args.url || '';
  else s = JSON.stringify(args || {}).substring(0, 100);
  if (s.length > 120) s = s.substring(0, 120) + '\u2026';
  return display + '(' + s + ')';
}

function _attachResult(tcEl, result) {
  var bullet = tcEl.querySelector('.tc-bullet');
  if (bullet) { bullet.className = 'tc-bullet done'; }
  // Remove BG/KL buttons (tool is done)
  var bgBtn = tcEl.querySelector('.tc-bg-btn');
  if (bgBtn) bgBtn.remove();
  var klBtn = tcEl.querySelector('.tc-kl-btn');
  if (klBtn) klBtn.remove();
  var toolHint = tcEl.dataset.tool || '';
  var pathHint = tcEl.dataset.path || '';
  var rd = document.createElement('div');
  rd.className = 'tc-result';
  rd.innerHTML = '\u23bf ' + renderToolResult(result, toolHint, pathHint);
  tcEl.appendChild(rd);
}

function backgroundTool(tcId) {
  if (!tcId) return;
  vscode.postMessage({ type: 'backgroundTool', tcId: tcId });
  // Optimistic UI: mark bullet as bg, swap BG button for KL button
  var tcEl = document.querySelector('[data-tc-id="' + tcId + '"]');
  if (tcEl) {
    var btn = tcEl.querySelector('.tc-bg-btn');
    if (btn) btn.remove();
    var bullet = tcEl.querySelector('.tc-bullet');
    if (bullet) { bullet.style.color = '#f0ad4e'; bullet.title = 'Running in background'; }
    // Add Kill button
    var klBtn = document.createElement('button');
    klBtn.className = 'tc-kl-btn';
    klBtn.onclick = function() { killTool(tcId); };
    klBtn.title = 'Kill background task';
    klBtn.style.cssText = 'font-size:10px;padding:1px 6px;margin-left:8px;background:transparent;border:1px solid #e94560;color:#e94560;border-radius:3px;cursor:pointer;vertical-align:middle';
    klBtn.textContent = '\u2717 KL';
    // Insert KL button where BG button was (after summary text)
    var preEl = tcEl.querySelector('pre');
    if (preEl) tcEl.insertBefore(klBtn, preEl);
    else tcEl.appendChild(klBtn);
  }
}

function killTool(tcId) {
  if (!tcId) return;
  vscode.postMessage({ type: 'killTool', tcId: tcId });
  // Optimistic UI: mark as cancelled
  var tcEl = document.querySelector('[data-tc-id="' + tcId + '"]');
  if (tcEl) {
    var btn = tcEl.querySelector('.tc-kl-btn');
    if (btn) btn.remove();
    var bullet = tcEl.querySelector('.tc-bullet');
    if (bullet) { bullet.style.color = '#e94560'; bullet.title = 'Killed'; bullet.className = 'tc-bullet done'; }
  }
}

function addToolResult(tool, result, filePath, tcId) {
  // Try to attach to matching tool_call
  if (tcId) {
    var tcEl = document.querySelector('[data-tc-id="' + tcId + '"]');
    if (tcEl) {
      _attachResult(tcEl, result);
      return;
    }
  }
  // Fallback: standalone
  var div = document.createElement('div');
  div.className = 'msg tool_result';
  var display = _TOOL_DISPLAY[tool] || tool;
  div.innerHTML = '<span class="tc-bullet done">\u25cf</span> ' + esc(display) + '<div class="tc-result">\u23bf ' + esc(result.substring(0, 200)) + '</div>';
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

// Handle messages from extension
window.addEventListener('message', function(e) {
  var msg = e.data;
  switch (msg.type) {
    case 'sseEvent':
      handleSSE(msg.event);
      break;
    case 'conversationList':
      showConvList(msg.conversations);
      break;
    case 'history':
      if (msg.append) {
        prependHistory(msg.data);
      } else {
        replayHistory(msg.data);
      }
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
      addMsg('system', '📎 ' + (msg.filename || 'file') + ' attached (' + msg.count + ' pending) — sent with your next message');
      scrollBottom();
      break;
    case 'agentSelected':
      statusEl.textContent = 'Agent: ' + msg.agent;
      break;
    case 'actionResult':
      if (msg.action === 'list_active') { _handleListActiveResult(msg.data || {}); break; }
      if (renderPanelResult(msg.action, msg.data)) break;
      if (msg.data && msg.data.error) { addMsg('error', msg.data.error); break; }
      var d = msg.data || {};
      if (msg.action === 'model') statusEl.textContent = 'Model: ' + (d.model || d.message || '?');
      else if (msg.action === 'select_agent') { statusEl.textContent = 'Agent: ' + (d.agent || d.name || '?'); }
      else if (msg.action === 'list_tools') {
        var tools = d.tools || [];
        if (!tools.length) addMsg('system', 'No tools.');
        else addMsg('system', 'Tools (' + tools.length + '):\n' + tools.map(function(t) { return '  ' + t.name + ': ' + (t.description || '').slice(0, 60); }).join('\n'));
      }
      else if (msg.action === 'list_secrets') {
        var secrets = d.secrets || [];
        addMsg('system', secrets.length ? 'Secrets: ' + secrets.join(', ') : 'No secrets.');
      }
      else if (msg.action === 'list_variables') {
        var vars = d.variables || {};
        var vlines = Object.entries(vars).map(function(e) { return '  ' + e[0] + ' = ' + e[1]; });
        addMsg('system', vlines.length ? 'Variables:\n' + vlines.join('\n') : 'No variables.');
      }
      else if (msg.action === 'cost') {
        var svcs = d.services || [];
        if (!svcs.length) addMsg('system', 'No usage data.');
        else {
          var clines = svcs.map(function(s) { return (s.llm_service || '?') + ': ' + (s.tokens_in || 0) + ' in / ' + (s.tokens_out || 0) + ' out' + (s.cost !== undefined ? ' $' + s.cost.toFixed(4) : ''); });
          addMsg('system', clines.join('\n'));
        }
      }
      else if (msg.action === 'approve_plan') { addMsg('system', '\u2705 Plan approved'); loadPlansPanel(); }
      else if (msg.action === 'reject_plan') { addMsg('system', '\u274C Plan rejected'); loadPlansPanel(); }
      else if (msg.action === 'cancel_plan') { addMsg('system', '\u23F9 Plan cancelled'); loadPlansPanel(); }
      else if (msg.action === 'delete_plan') { addMsg('system', '\u2705 Plan deleted'); loadPlansPanel(); }
      else if (msg.action === 'update_plan_step') { loadPlansPanel(); }
      else if (msg.action === 'assign_plan') { addMsg('system', '\u2705 Plan assigned'); loadPlansPanel(); }
      else if (msg.action === 'create_plan_user') { addMsg('system', '\u2705 Plan created: ' + (d.plan ? d.plan.title : '')); loadPlansPanel(); }
      else if (d.result || d.message) addMsg('system', d.result || d.message);
      else if (typeof d === 'string') addMsg('system', d);
      else addMsg('system', JSON.stringify(d).slice(0, 500));
      break;
    case 'clipboardContent':
      if (msg.text) { inputEl.value += msg.text; addMsg('system', 'Pasted from clipboard.'); }
      break;
  }
});

function scrollBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function handleSSE(event) {
  var evType = event.event;
  var data = event.data;
  var agent = data.agent_name || '';

  switch (evType) {
    case 'new_message':
      // Messages appended by any client (webchat, Telegram, flows) and
      // assistant text. Dedup by DOM presence — NOT _seenMsgIds, which
      // message_meta pre-registers for footer updates before the text
      // event arrives (that registration must not eat the message).
      if (data.role && data.content) {
        if (data.msg_id && document.querySelector('[data-msgid="' + data.msg_id + '"]')) break;
        if (data.msg_id) delete _seenMsgIds[data.msg_id];
        addMsg(data.role, data.content, data);
        scrollBottom();
      }
      break;

    case 'thinking':
      updateActiveAgents(agent, 'thinking');
      break;

    case 'thinking_content':
      updateActiveAgents(agent, 'thinking');
      // Display reasoning content in a collapsible block
      if (data.text) {
        var aKey = (agent || '').toLowerCase();
        if (!thinkingBlocks[aKey]) {
          var details = document.createElement('details');
          details.className = 'msg thinking-block';
          details.setAttribute('open', '');
          var summary = document.createElement('summary');
          summary.textContent = (agent || 'Agent') + ' thinking...';
          summary.style.cssText = 'cursor:pointer;font-size:11px;color:var(--vscode-descriptionForeground);';
          details.appendChild(summary);
          var content = document.createElement('pre');
          content.style.cssText = 'font-size:11px;color:var(--vscode-descriptionForeground);font-style:italic;white-space:pre-wrap;max-height:200px;overflow-y:auto;margin:2px 0;';
          details.appendChild(content);
          messagesEl.appendChild(details);
          thinkingBlocks[aKey] = {el: details, content: content, summary: summary, text: '', start: Date.now()};
          messagesEl.scrollTop = messagesEl.scrollHeight;
        }
        var tb = thinkingBlocks[aKey];
        tb.text += data.text;
        tb.content.textContent = tb.text;
        messagesEl.scrollTop = messagesEl.scrollHeight;
      }
      break;

    case 'token':
      // Finalize any thinking block for this agent
      finalizeThinking(agent);
      streaming[agent] = (streaming[agent] || '') + (data.text || '');
      // Live-render streaming tokens
      var streamEl = getOrCreateStream(agent);
      if (data.msg_id) streamEl.dataset.msgid = data.msg_id;
      streamEl.textContent = streaming[agent];
      messagesEl.scrollTop = messagesEl.scrollHeight;
      updateActiveAgents(agent, 'writing');
      break;

    case 'tool_call':
      finalizeThinking(agent);
      var tcArgs = data.arguments || {};
      var tcId = data.tc_id || '';
      var tcDiv = document.createElement('div');
      tcDiv.className = 'msg tool_call';
      if (tcId) tcDiv.dataset.tcId = tcId;
      tcDiv.dataset.tool = data.tool || '';
      if (tcArgs.path) tcDiv.dataset.path = tcArgs.path;

      var bgBtn = tcId ? ' <button class="tc-bg-btn" onclick="backgroundTool(\'' + tcId + '\')" title="Run in background" style="font-size:10px;padding:1px 6px;margin-left:8px;background:transparent;border:1px solid #555;color:#888;border-radius:3px;cursor:pointer;vertical-align:middle">\u2192 BG</button>' : '';
      if (data.tool === 'edit' && tcArgs.path) {
        var editPath = tcArgs.path || '?';
        var editHeader = '<span class="tc-bullet pending">\u25cf</span> Edit(' + fileLink(editPath) + ')';
        if (tcArgs.start_line && tcArgs.end_line) editHeader += ' <span style="color:#8b949e">lines ' + tcArgs.start_line + '-' + tcArgs.end_line + '</span>';
        var diffLines = [];
        if (tcArgs.old_string) tcArgs.old_string.split('\n').slice(0, 6).forEach(function(l) {
          diffLines.push('<span style="color:#f85149">- ' + esc(l) + '</span>');
        });
        if (tcArgs.new_string) tcArgs.new_string.split('\n').slice(0, 6).forEach(function(l) {
          diffLines.push('<span style="color:#3fb950">+ ' + esc(l) + '</span>');
        });
        tcDiv.innerHTML = editHeader + bgBtn + (diffLines.length ? '<pre style="margin:2px 0 0 0;font-size:11px">' + diffLines.join('\n') + '</pre>' : '');
      } else {
        tcDiv.innerHTML = '<span class="tc-bullet pending">\u25cf</span> ' + esc(_toolSummary(data.tool || '', tcArgs)) + bgBtn;
      }
      messagesEl.appendChild(tcDiv);
      messagesEl.scrollTop = messagesEl.scrollHeight;
      _lastToolCall = (_TOOL_DISPLAY[data.tool] || data.tool) + '(...)';
      _hadToolCalls = true;
      updateActiveAgents(agent, data.tool || 'tool');
      break;

    case 'tool_result':
      addToolResult(data.tool || '', data.result || '', data.path || '', data.tc_id || '');
      break;

    case 'done': {
      finalizeThinking(agent);
      var doneText = data.response || streaming[agent] || '';
      var aKey = (agent || '').toLowerCase();
      var existingEl = streamEls[aKey];
      // Register all msg_ids from this turn
      var allIds = data.all_msg_ids || [];
      if (data.msg_id) allIds.push(data.msg_id);
      for (var ii = 0; ii < allIds.length; ii++) {
        if (allIds[ii]) _seenMsgIds[allIds[ii]] = true;
      }
      // Find existing element (streaming or finalized by turn_complete)
      if (!existingEl) {
        for (var mi = 0; mi < allIds.length; mi++) {
          if (allIds[mi]) {
            var found = document.querySelector('[data-msgid="' + allIds[mi] + '"]');
            if (found) { existingEl = found; break; }
          }
        }
      }
      if (existingEl) {
        // Convert streaming element to permanent with metadata
        existingEl.classList.remove('streaming-live');
        existingEl.style.opacity = '';
        if (existingEl.className.indexOf('assistant') < 0) existingEl.className = 'msg assistant';
        existingEl.dataset.rawText = (doneText || '').substring(0, 200);
        // Update metadata (replace if exists from turn_complete estimate)
        var tin = data.tokens_in || 0;
        var tout = data.tokens_out || 0;
        var mdl = data.model || '';
        if (tin || tout || mdl) {
          var existingMeta = existingEl.querySelector('.token-footer');
          var metaSpan = existingMeta || document.createElement('div');
          metaSpan.className = 'token-footer';
          metaSpan.style.cssText = 'font-size:10px;color:var(--vscode-descriptionForeground);margin-top:4px;';
          metaSpan.textContent = (mdl || '?') + ' \u00b7 ' + tin + '\u2191 ' + tout + '\u2193';
          if (!existingMeta) existingEl.appendChild(metaSpan);
        }
        delete streamEls[aKey];
      } else if (doneText) {
        // No element found — check msg_id dedup before adding
        if (!data.msg_id || !_seenMsgIds[data.msg_id]) {
          addMsg('assistant', doneText, data);
        }
      }
      streaming[agent] = '';
      _hadToolCalls = false;
      updateActiveAgents(agent, 'done');
      _syncActiveFromServer();  // immediate sync on done
      var tin2 = data.tokens_in || 0;
      var tout2 = data.tokens_out || 0;
      var model2 = data.model || '';
      statusEl.innerHTML = '<span class="token-footer">' + tin2 + '\u2191 ' + tout2 + '\u2193' + (model2 ? ' \u00b7 ' + model2 : '') + '</span>';
      break;
    }

    case 'turn_complete':
      // Finalize streaming element between Claude Code turns
      finalizeThinking(agent);
      var tcAKey = (agent || '').toLowerCase();
      var tcEl = streamEls[tcAKey];
      if (tcEl) {
        tcEl.classList.remove('streaming-live');
        tcEl.style.opacity = '';
        tcEl.className = 'msg assistant';
        // Add metainfo if available
        if (data.source && data.model) {
          var metaParts = [data.model];
          if (data.tokens_out) metaParts.push('\u2193' + data.tokens_out);
          var metaSpan = document.createElement('div');
          metaSpan.className = 'token-footer';
          metaSpan.style.cssText = 'font-size:10px;color:var(--vscode-descriptionForeground);margin-top:2px;';
          metaSpan.textContent = metaParts.join(' \u00b7 ');
          tcEl.appendChild(metaSpan);
        }
        delete streamEls[tcAKey];
        delete streaming[agent];
      }
      break;

    case 'message_meta':
      // Per-message metadata — find element by msg_id and update
      if (data.msg_id) _seenMsgIds[data.msg_id] = true;
      if (data.msg_id) {
        var metaEl = document.querySelector('[data-msgid="' + data.msg_id + '"]');
        if (metaEl) {
          var parts = [];
          var mm = data.source || data;
          if (mm.model) parts.push(mm.model);
          if (data.tokens_in || data.tokens_out) parts.push('\u2191' + (data.tokens_in || 0) + ' \u2193' + (data.tokens_out || 0));
          if (data.duration_ms) parts.push((data.duration_ms / 1000).toFixed(1) + 's');
          if (parts.length) {
            var existing = metaEl.querySelector('.token-footer');
            var span = existing || document.createElement('div');
            span.className = 'token-footer';
            span.style.cssText = 'font-size:10px;color:var(--vscode-descriptionForeground);margin-top:2px;';
            span.textContent = parts.join(' \u00b7 ');
            if (!existing) metaEl.appendChild(span);
          }
        }
      }
      break;

    case 'error_event':
      addMsg('error', data.message || 'Error');
      statusEl.textContent = '';
      break;

    case 'cancelled':
      updateActiveAgents(agent, 'cancelled');
      break;

    case 'iteration_status': {
      // Update existing agent data between polls (fast hint)
      var itKey = _agentKey(agent);
      if (activeAgents[itKey]) {
        activeAgents[itKey].iteration = data.iteration || 0;
        activeAgents[itKey].totalTools = data.total_tools || 0;
        if (data.tools_called && data.tools_called.length > 0) {
          activeAgents[itKey].lastTool = data.tools_called[data.tools_called.length - 1];
        }
        _renderActiveAgents();
      }
      break;
    }

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
      var resp = data.response || '';
      if (resp) addMsg('assistant', resp, data);
      break;
    }

    case 'compact_progress':
      if (data.stage === 'done') {
        statusEl.textContent = 'Compacted: ' + (data.before || 0) + ' \u2192 ' + (data.after || 0) + ' messages';
      } else {
        statusEl.textContent = 'Compacting... ' + (data.stage || '');
      }
      break;

    case 'notification':
      addMsg('system', data.message || '');
      break;

    case 'command_result': {
      var crAction = data.action || '';
      if (data.error && crAction !== 'list_active') { addMsg('error', data.error); break; }
      var crParsed = null;
      try { crParsed = typeof data.result === 'string' ? JSON.parse(data.result) : data.result; } catch(e) {}
      // Route list_active results to active agents handler
      if (crAction === 'list_active') { _handleListActiveResult(crParsed || {}); break; }
      // Silent data actions
      var crSilent = ['list_params_secrets','list_links','list_conversations',
        'list_resources','list_agents','list_tools','list_skills','get_tool_schemas',
        'get_permission_mode','get_context','get_plan','get_plans','get_cost','get_usage',
        'poll','ping','list_repo_agents','list_secrets','list_variables','list_schedules',
        'task_status','task_log','stats','check_files','port_forward_list','service_list'];
      if (crSilent.indexOf(crAction) >= 0) break;
      if (crParsed && crParsed.error) { addMsg('error', crParsed.error); }
      else if (crParsed && crParsed.message) { addMsg('system', crParsed.message); }
      else if (crParsed && crParsed.status === 'ok') { addMsg('system', crAction + ': OK'); }
      break;
    }

    case 'btw_token':
      streaming['btw:' + agent] = (streaming['btw:' + agent] || '') + (data.text || '');
      break;

    case 'btw_done': {
      var btwText = data.response || streaming['btw:' + agent] || '';
      if (btwText) addMsg('assistant', '[btw] ' + btwText, data);
      streaming['btw:' + agent] = '';
      break;
    }

    case 'plan_created': {
      var plan = data.plan || data;
      var title = plan.title || data.title || '';
      var stepCount = (plan.steps && plan.steps.length) || data.steps || 0;
      addMsg('system', '\ud83d\udccb Plan created: ' + title + ' (' + stepCount + ' steps)');
      break;
    }

    case 'plan_updated':
      if (document.getElementById('panelOverlay') && document.getElementById('panelOverlay').className === 'panel-overlay visible' && _pendingPanel === '') {
        loadPlansPanel();
      }
      break;

    case 'plan_deleted':
      if (document.getElementById('panelOverlay') && document.getElementById('panelOverlay').className === 'panel-overlay visible' && _pendingPanel === '') {
        loadPlansPanel();
      }
      break;

    case 'bg_task_update': {
      var bgTcId = data.tc_id || '';
      if (bgTcId) {
        var bgTcEl = document.querySelector('[data-tc-id="' + bgTcId + '"]');
        if (bgTcEl) {
          if (data.status === 'done' || data.status === 'cancelled') {
            _attachResult(bgTcEl, data.result || (data.status === 'cancelled' ? '[Cancelled]' : '[Done]'));
          }
        }
      }
      break;
    }

    default:
      break;
  }
}

function showApproval(type, data) {
  var div = document.createElement('div');
  div.className = 'approval';
  var label = type === 'exec' ? 'Execute: ' + esc(data.command) : 'Tool: ' + esc(data.tool_name);
  div.innerHTML = label + '<br>'
    + '<button onclick="approve(this,\'' + data.request_id + '\',\'' + type + '\','
    + (type === 'exec' ? '\'approved\'' : '\'allow_once\'') + ')">Allow</button>'
    + '<button onclick="approve(this,\'' + data.request_id + '\',\'' + type + '\',\'denied\')">Deny</button>'
    + '<button onclick="approve(this,\'' + data.request_id + '\',\'' + type + '\',\'always_allow\')">Always</button>';
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function approve(btn, reqId, type, result) {
  vscode.postMessage({ type: 'approval', requestId: reqId, result: result, approvalType: type });
  btn.parentElement.remove();
}

function showAskUser(data) {
  var div = document.createElement('div');
  div.className = 'approval';
  var html = '<strong>Agent question:</strong> ' + esc(data.question || '');
  if (data.options && data.options.length) {
    html += '<br>';
    for (var i = 0; i < data.options.length; i++) {
      var opt = data.options[i];
      html += '<button onclick="answerAgent(this, \'' + esc(opt).replace(/'/g, "\\'") + '\')" style="margin:2px;padding:3px 10px;border:none;border-radius:3px;cursor:pointer">' + esc(opt) + '</button>';
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
  for (var ci = 0; ci < convs.length; ci++) {
    var c = convs[ci];
    var div = document.createElement('div');
    div.style.cssText = 'padding:8px 10px;cursor:pointer;border-bottom:1px solid var(--vscode-panel-border);font-size:12px;transition:background 0.1s';
    div.onmouseenter = function() { this.style.background = 'var(--vscode-list-hoverBackground)'; };
    div.onmouseleave = function() { this.style.background = ''; };
    var title = (c.title || '').slice(0, 70);
    var preview = (c.preview || '').slice(0, 70);
    var count = c.message_count || '?';
    var date = c.updated_at ? new Date(c.updated_at * 1000).toLocaleString() : '';
    div.innerHTML = '<div style="font-weight:500;color:var(--vscode-editor-foreground)">' + esc(title || preview || '(new conversation)') + '</div>'
      + (title && preview ? '<div style="font-size:11px;color:var(--vscode-descriptionForeground);margin-top:1px">' + esc(preview) + '</div>' : '')
      + '<div style="font-size:10px;color:var(--vscode-descriptionForeground);margin-top:2px">'
      + esc(c.conversation_id.slice(0, 8)) + ' \u2022 ' + count + ' msgs'
      + (date ? ' \u2022 ' + date : '')
      + '</div>';
    div.onclick = (function(cid) { return function() { setActiveTab('tbChat'); vscode.postMessage({ type: 'resumeConversation', conversationId: cid }); }; })(c.conversation_id);
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
  currentHistoryOffset = data.raw_count || (data.messages || []).length;
  startActiveSync();

  _addLoadMoreBanner(data);
  var msgs = data.messages || [];
  for (var i = 0; i < msgs.length; i++) {
    addMsg(msgs[i].type || msgs[i].role, msgs[i].content || '', msgs[i]);
  }
  statusEl.textContent = currentHistoryOffset + ' of ' + (data.message_count || '?') + ' messages';
}

function prependHistory(data) {
  currentHistoryOffset += data.raw_count || (data.messages || []).length;
  var prevHeight = messagesEl.scrollHeight;

  // Remove old load-more banner
  var oldBanner = messagesEl.querySelector('.load-more');
  if (oldBanner) oldBanner.remove();

  // Add new banner if more messages exist
  _addLoadMoreBanner(data);

  // Render new messages, collect them, then prepend
  var beforeCount = messagesEl.children.length;
  var msgs = data.messages || [];
  for (var i = 0; i < msgs.length; i++) {
    addMsg(msgs[i].type || msgs[i].role, msgs[i].content || '', msgs[i]);
  }
  // Move newly added (appended at end) to after banner
  var insertPoint = messagesEl.querySelector('.load-more');
  insertPoint = insertPoint ? insertPoint.nextSibling : messagesEl.firstChild;
  var newEls = [];
  while (messagesEl.children.length > beforeCount) {
    newEls.push(messagesEl.lastChild);
    messagesEl.removeChild(messagesEl.lastChild);
  }
  for (var i = newEls.length - 1; i >= 0; i--) {
    messagesEl.insertBefore(newEls[i], insertPoint);
  }
  // Preserve scroll position
  messagesEl.scrollTop = messagesEl.scrollHeight - prevHeight;
  statusEl.textContent = currentHistoryOffset + ' of ' + (data.message_count || '?') + ' messages';
}

function _addLoadMoreBanner(data) {
  if (!data.has_more) return;
  var more = document.createElement('div');
  more.className = 'load-more';
  more.textContent = '\u25b2 Load more messages (' + (data.message_count || '?') + ' total)';
  more.onclick = function() {
    vscode.postMessage({
      type: 'resumeConversation',
      conversationId: currentHistoryConvId,
      offset: currentHistoryOffset,
    });
  };
  messagesEl.insertBefore(more, messagesEl.firstChild);
}

// Paste images from the clipboard as message attachments
inputEl.addEventListener('paste', function(e) {
  var items = (e.clipboardData && e.clipboardData.items) || [];
  for (var i = 0; i < items.length; i++) {
    var it = items[i];
    if (it.kind !== 'file' || it.type.indexOf('image/') !== 0) continue;
    e.preventDefault();
    var file = it.getAsFile();
    if (!file) continue;
    var mime = it.type;
    var reader = new FileReader();
    reader.onload = function(ev) {
      var dataUrl = String(ev.target.result || '');
      var b64 = dataUrl.split(',')[1] || '';
      if (!b64) return;
      var ext = (mime.split('/')[1] || 'png').replace('jpeg', 'jpg');
      vscode.postMessage({
        type: 'attachImage',
        filename: 'pasted_' + Date.now() + '.' + ext,
        mime_type: mime,
        data: b64,
      });
    };
    reader.readAsDataURL(file);
  }
});

// Auto-resize textarea
inputEl.addEventListener('input', function() {
  inputEl.style.height = '36px';
  inputEl.style.height = Math.min(inputEl.scrollHeight, 120) + 'px';
});

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

function updateActiveAgents(agent, status) {
  if (!agent) return;
  if (status === 'done' || status === 'cancelled') {
    delete activeAgents[agent];
  } else {
    activeAgents[agent] = { status: status, ts: Date.now() };
  }
  _renderActiveAgents();
}
function _renderActiveAgents() {
  var now = Date.now();
  for (var k in activeAgents) {
    if (now - (activeAgents[k].ts || 0) > 300000) delete activeAgents[k];
  }
  var el = document.getElementById('activeAgents');
  var keys = Object.keys(activeAgents);
  if (keys.length === 0) {
    el.style.display = 'none';
    statusEl.textContent = '';
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
    // Typing indicator driven solely by active agents
    var parts = keys.map(function(a) { return a + ' ' + (activeAgents[a].status || ''); });
    statusEl.innerHTML = '<span class="thinking">' + randomVerb() + '... ' + esc(parts.join(', ')) + '</span>';
  }
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

function newChat() {
  closePanel();
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
  web_search:'WebSearch',web_fetch:'WebFetch',scrape_url:'Scrape',
  generate_image:'ImageGen',remember:'Remember',recall:'Recall',
  spawn_agents:'SpawnAgents',show_file:'ShowFile',get_tool_schema:'GetToolSchema',
};

function _toolSummary(name, args) {
  var display = _TOOL_DISPLAY[name] || name;
  var s = '';
  if (name === 'bash' || name === 'execute_script') s = args.command || args.code || '';
  else if (['read','write','edit','delete','stat','exists','mkdir','list_dir'].indexOf(name) >= 0) s = args.path || '';
  else if (name === 'glob') s = args.pattern || '';
  else if (name === 'grep') s = (args.pattern || '') + (args.path ? ', ' + args.path : '');
  else if (name === 'web_search') s = args.query || '';
  else if (name === 'web_fetch' || name === 'scrape_url') s = args.url || '';
  else s = JSON.stringify(args || {}).substring(0, 100);
  if (s.length > 120) s = s.substring(0, 120) + '\u2026';
  return display + '(' + s + ')';
}

function _attachResult(tcEl, result) {
  var bullet = tcEl.querySelector('.tc-bullet');
  if (bullet) { bullet.className = 'tc-bullet done'; }
  // Remove BG button (tool is done)
  var bgBtn = tcEl.querySelector('.tc-bg-btn');
  if (bgBtn) bgBtn.remove();
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
  // Optimistic UI: mark bullet as bg, remove button
  var tcEl = document.querySelector('[data-tc-id="' + tcId + '"]');
  if (tcEl) {
    var btn = tcEl.querySelector('.tc-bg-btn');
    if (btn) btn.remove();
    var bullet = tcEl.querySelector('.tc-bullet');
    if (bullet) { bullet.style.color = '#f0ad4e'; bullet.title = 'Running in background'; }
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
    case 'relayStatus':
      updateRelayStatus(msg.status);
      break;
  }
});

function handleSSE(event) {
  var evType = event.event;
  var data = event.data;
  var agent = data.agent_name || '';

  switch (evType) {
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

    case 'narration':
      // Display-only narration (not persisted in context)
      if (data.text) {
        var narEl = document.createElement('div');
        narEl.className = 'msg narration';
        narEl.style.cssText = 'font-style:italic;opacity:0.7;font-size:12px;';
        narEl.textContent = data.text;
        narEl.dataset.agent = (agent || '').toLowerCase();
        messagesEl.appendChild(narEl);
        messagesEl.scrollTop = messagesEl.scrollHeight;
      }
      break;

    case 'error_event':
      addMsg('error', data.message || 'Error');
      statusEl.textContent = '';
      break;

    case 'cancelled':
      updateActiveAgents(agent, 'cancelled');
      break;

    case 'iteration_status':
      updateActiveAgents(agent, 'iter ' + data.iteration + ' \u00b7 ' + data.total_tools + ' tools');
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

    case 'command_result':
      if (data.error) { addMsg('error', data.error); }
      else {
        try { var cr = JSON.parse(data.result); addMsg('system', cr.error || cr.message || data.result); }
        catch(e) { addMsg('system', data.result); }
      }
      break;

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
    var preview = (c.preview || '').slice(0, 70);
    var count = c.message_count || '?';
    var date = c.updated_at ? new Date(c.updated_at * 1000).toLocaleString() : '';
    div.innerHTML = '<div style="font-weight:500;color:var(--vscode-editor-foreground)">' + esc(preview || '(new conversation)') + '</div>'
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

function updateRelayStatus(status) {
  var dot = document.getElementById('relayDot');
  var label = document.getElementById('relayLabel');
  if (status === 'running') {
    dot.className = 'relay-dot on';
    label.textContent = 'Relay \u2713';
  } else {
    dot.className = 'relay-dot off';
    label.textContent = 'Relay \u2717';
  }
}

function relayContextMenu(e) {
  e.preventDefault();
  e.stopPropagation();
  // Remove any existing context menu
  var old = document.getElementById('relayCtxMenu');
  if (old) old.remove();

  var dot = document.getElementById('relayDot');
  var isOn = dot && dot.classList.contains('on');

  var menu = document.createElement('div');
  menu.id = 'relayCtxMenu';
  menu.className = 'ctx-menu';
  menu.style.position = 'fixed';
  menu.style.left = e.clientX + 'px';
  menu.style.top = e.clientY + 'px';

  var item = document.createElement('div');
  item.className = 'ctx-menu-item';
  item.textContent = isOn ? 'Disconnect relay' : 'Reconnect relay';
  item.onclick = function() {
    menu.remove();
    vscode.postMessage({ type: 'reconnectRelay' });
  };
  menu.appendChild(item);

  document.body.appendChild(menu);
  setTimeout(function() {
    document.addEventListener('click', function removeCtx() {
      menu.remove();
      document.removeEventListener('click', removeCtx);
    });
  }, 0);
}

// Auto-resize textarea
inputEl.addEventListener('input', function() {
  inputEl.style.height = '36px';
  inputEl.style.height = Math.min(inputEl.scrollHeight, 120) + 'px';
});

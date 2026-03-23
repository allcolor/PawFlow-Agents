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
var thinkingBlocks = {}; // agentKey → {el, content, summary, text, start}
var streamEls = {};      // agentKey → live stream element

function finalizeThinking(agentName) {
  var aKey = (agentName || '').toLowerCase();
  var tb = thinkingBlocks[aKey];
  if (tb) {
    var elapsed = ((Date.now() - tb.start) / 1000).toFixed(1);
    tb.summary.textContent = 'Thought for ' + elapsed + 's';
    tb.el.removeAttribute('open');
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

function renderToolResult(content) {
  var text = content.replace(/\[TOOL OUTPUT[^\]]*\]\n?/g, '').replace(/\n\[\/TOOL OUTPUT\]/g, '');
  var lines = text.split('\n');
  var hasDiff = lines.some(function(l) { return l.trimStart().startsWith('+ ') || l.trimStart().startsWith('- '); });
  if (hasDiff && (text.includes('replacement') || text.includes('Edited ') || text.includes('Written '))) {
    return '<pre class="diff">' + lines.map(function(l) {
      var s = l.trimStart();
      if (s.startsWith('+ ') || s.match(/^\d+\s+\+ /)) return '<span class="diff-add">' + esc(l) + '</span>';
      if (s.startsWith('- ') || s.match(/^\d+\s+- /)) return '<span class="diff-del">' + esc(l) + '</span>';
      if (s.startsWith('@@')) return '<span class="diff-hunk">' + esc(l) + '</span>';
      return '<span class="diff-ctx">' + esc(l) + '</span>';
    }).join('\n') + '</pre>';
  }
  return esc(text.slice(0, 300));
}

function addMsg(type, content, meta) {
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
    div.innerHTML = '&#9889; ' + esc(content);
  } else if (type === 'tool_result') {
    div.innerHTML = '&#10003; ' + renderToolResult(content);
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

function addToolResult(tool, result) {
  var div = document.createElement('div');
  div.className = 'msg tool_result';
  div.innerHTML = renderToolResult(result);
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
      statusEl.innerHTML = '<span class="thinking">' + randomVerb() + '...</span>';
      updateActiveAgents(agent, 'thinking');
      break;

    case 'thinking_content':
      statusEl.innerHTML = '<span class="thinking">' + randomVerb() + '...</span>';
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
      streamEl.textContent = streaming[agent];
      messagesEl.scrollTop = messagesEl.scrollHeight;
      statusEl.textContent = agent + ' writing...';
      updateActiveAgents(agent, 'writing');
      break;

    case 'tool_call':
      finalizeThinking(agent);
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
      finalizeThinking(agent);
      removeStreamEl(agent);
      var text = data.response || streaming[agent] || '';
      if (text) addMsg('assistant', text, data);
      streaming[agent] = '';
      _hadToolCalls = false;
      updateActiveAgents(agent, 'done');
      var tin = data.tokens_in || 0;
      var tout = data.tokens_out || 0;
      var model = data.model || '';
      statusEl.innerHTML = '<span class="token-footer">' + tin + '\u2191 ' + tout + '\u2193' + (model ? ' \u00b7 ' + model : '') + '</span>';
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
        data.iteration + ' \u00b7 ' + data.total_tools + ' tools</span>';
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
  currentHistoryOffset = (data.messages || []).length;

  if (data.has_more) {
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
    messagesEl.appendChild(more);
  }
  var msgs = data.messages || [];
  for (var i = 0; i < msgs.length; i++) {
    addMsg(msgs[i].type || msgs[i].role, msgs[i].content || '', msgs[i]);
  }
  statusEl.textContent = msgs.length + ' of ' + (data.message_count || '?') + ' messages';
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

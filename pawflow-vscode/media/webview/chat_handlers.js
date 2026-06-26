// \xe2\x94\x80\xe2\x94\x80 Webview message-type handlers: SSE stream + approval/ask/history \xe2\x94\x80\xe2\x94\x80
// Split from chat.js (<=800 lines). Loaded right after chat.js; all globals
// (no modules). The message dispatch listener in chat.js calls these.

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
      // Find an existing element for THIS final message only: the live
      // streaming element, or a rendered message with the final msg_id.
      // Matching any id of the turn used to hit intermediate messages and
      // skip rendering the final response entirely.
      if (!existingEl && data.msg_id) {
        existingEl = document.querySelector('[data-msgid="' + data.msg_id + '"]');
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
        // No element for the final message — render it. Clear any
        // pre-registration (message_meta) so addMsg doesn't drop it.
        if (data.msg_id) delete _seenMsgIds[data.msg_id];
        addMsg('assistant', doneText, data);
        scrollBottom();
      }
      // Register the turn's msg_ids AFTER the render decision so late
      // duplicate events (history echo) dedup against what is on screen.
      var allIds = data.all_msg_ids || [];
      if (data.msg_id) allIds.push(data.msg_id);
      for (var ii = 0; ii < allIds.length; ii++) {
        if (allIds[ii]) _seenMsgIds[allIds[ii]] = true;
      }
      streaming[agent] = '';
      _hadToolCalls = false;
      updateActiveAgents(agent, 'done');
      _syncActiveFromServer();  // immediate sync on done
      var tin2 = data.tokens_in || 0;
      var tout2 = data.tokens_out || 0;
      var model2 = data.model || '';
      statusEl.innerHTML = '<span class="token-footer">' + tin2 + '\u2191 ' + tout2 + '\u2193' + (model2 ? ' \u00b7 ' + model2 : '') + '</span>';
      _statusFromActive = false;
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
  // Full re-render: the DOM is wiped, so the msg_id dedup registry must be
  // wiped too — otherwise re-opening a conversation drops every message
  // already seen and the panel renders near-empty.
  _seenMsgIds = {};
  _msgRawIndex = 0;
  currentHistoryConvId = data.conversation_id || currentHistoryConvId;
  currentHistoryOffset = data.raw_count || (data.messages || []).length;
  startActiveSync();

  _addLoadMoreBanner(data);
  var msgs = data.messages || [];
  for (var i = 0; i < msgs.length; i++) {
    // One malformed message must not truncate the rest of the history.
    try { addMsg(msgs[i].type || msgs[i].role, msgs[i].content || '', msgs[i]); }
    catch (e) { console.error('addMsg failed for message', i, e); }
  }
  statusEl.textContent = currentHistoryOffset + ' of ' + (data.message_count || '?') + ' messages';
  messagesEl.scrollTop = messagesEl.scrollHeight;
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
    try { addMsg(msgs[i].type || msgs[i].role, msgs[i].content || '', msgs[i]); }
    catch (e) { console.error('addMsg failed for message', i, e); }
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


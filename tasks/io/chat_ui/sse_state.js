// Auto-split from sse.js: per-connection SSE state + shared helpers.
// Loaded before sse.js; connectSSE() resets the mutable maps per
// connection. All chat-ui scripts share one global scope, so these
// helpers/handlers (in sse_handlers_*.js) reference them by bare name.

// ── Task block grouping ─────────────────────────────────────────
var _taskBlocks = {};
var _pendingToolResults = {};
var _serviceInstallProgress = {};
// ── Extended thinking ──
var thinkingElements = {};            // agentKey → {el, text, startTime}
var delegateThinkingElements = {};    // taskId → {el, content, summary, text, startTime}
// ── Delegate blocks (grouped) ─────────────────────────────────
// _delegateGroups[delegateTcId] = { el, content, summary, total, doneCount, subBlocks: {} }
// _delegateSubBlocks[taskId] = { el, content, summary, agent, taskId }
var _delegateGroups = {};             // delegateTcId → { el, content, summary, total, doneCount, subBlocks }
var _delegateSubBlocks = {};          // taskId → { el, content, summary, agent, taskId }
// --- BTW (side-channel) events ---
var btwElements = {};                 // agent_name → streaming element
var btwTexts = {};
var _sseCid = null;                   // current conversation id (set per connectSSE)


// Turn complete: finalize streaming element between Claude Code turns
// so each turn looks like a proper message (badge, background, border)
// Context-ack patterns that should never be displayed (LLM echoing pre-filled context)
const _CONTEXT_ACKS = new Set([
  "Understood. I'll continue from where I left off.",
  "Understood. I'll read the conversation history file to get full context, then continue from the recent messages.",
  "Understood. I have the summary and will continue from the recent messages.",
  "Understood, continuing.",
  "Understood.",
  "No response requested.",
]);

function _resultText(value) {
  if (typeof value === 'string') return value;
  try { return JSON.stringify(value, null, 2); }
  catch (_err) { return String(value || ''); }
}


function _serviceInstallLabel(data) {
  const status = data.status || 'running';
  const icon = status === 'ready' ? '\u2713' : status === 'failed' ? '\u2715' : '\u23f3';
  const name = data.service_id || data.service_type || 'service';
  const type = data.service_type && data.service_type !== name ? ' (' + data.service_type + ')' : '';
  const phase = data.phase ? ' - ' + data.phase.replace(/_/g, ' ') : '';
  const pct = typeof data.progress === 'number' ? ' [' + Math.round(data.progress * 100) + '%]' : '';
  const msg = data.message ? ': ' + data.message : '';
  return icon + ' Installing ' + name + type + phase + pct + msg;
}


function _upsertServiceInstallProgress(data) {
  const key = (data.service_type || 'service') + ':' + (data.service_id || 'default');
  let row = _serviceInstallProgress[key];
  const text = _serviceInstallLabel(data);
  if (!row || !row.isConnected) {
    row = addMsg('system', text, { source: { type: 'system', name: 'service-install' } });
    _serviceInstallProgress[key] = row;
  } else {
    row.textContent = text;
  }
  if (data.status === 'ready' || data.status === 'failed') {
    setTimeout(() => { delete _serviceInstallProgress[key]; }, 5000);
  }
  scrollBottom();
}


function _attachPendingToolResult(tcEl, tcId) {
  if (!tcEl || !tcId || !_pendingToolResults[tcId]) return false;
  const pending = _pendingToolResults[tcId];
  if (pending.timer) clearTimeout(pending.timer);
  delete _pendingToolResults[tcId];
  _attachToolResult(tcEl, _resultText((pending.data || {}).result || ''));
  if ((pending.data || {}).msg_id && typeof _seenMsgIds !== 'undefined') {
    _seenMsgIds.add(pending.data.msg_id);
  }
  return true;
}


function _queueUnmatchedToolResult(tcId, data) {
  if (!tcId) return false;
  if (_pendingToolResults[tcId] && _pendingToolResults[tcId].timer) {
    clearTimeout(_pendingToolResults[tcId].timer);
  }
  _pendingToolResults[tcId] = { data: data || {}, timer: null };
  _pendingToolResults[tcId].timer = setTimeout(() => {
    const pending = _pendingToolResults[tcId];
    if (!pending) return;
    const tcEl = (typeof findToolCallElement === 'function')
      ? findToolCallElement(tcId)
      : document.querySelector('[data-message-role="tool_call"][data-tc-id="' + tcId + '"]');
    if (tcEl) {
      _attachPendingToolResult(tcEl, tcId);
      return;
    }
    delete _pendingToolResults[tcId];
    const row = addMsg('tool_result', _resultText((pending.data || {}).result || ''), {
      tool_name: pending.data.tool,
      tool: pending.data.tool,
      source: pending.data.source || {type: 'agent', name: pending.data.agent_name || '', llm_service: pending.data.llm_service || ''},
      agent_name: pending.data.agent_name || '',
      llm_service: pending.data.llm_service || '',
      path: pending.data.path || '',
      ts: pending.data.ts,
      msg_id: pending.data.msg_id || '',
      tc_id: tcId,
    });
    if (pending.data.task_id && row) {
      const tb = _getTaskBlock(pending.data.task_id, pending.data.task_iteration, pending.data.agent_name || '');
      if (tb) tb.content.appendChild(row);
    }
    if (typeof applyTechnicalMessageGrouping === 'function') applyTechnicalMessageGrouping();
    scrollBottom();
  }, 750);
  return true;
}


function _finalizeLiveToolCalls(agentName, resultText) {
  const targetAgent = (agentName || '').toLowerCase();
  let changed = false;
  document.querySelectorAll('#messages .tc-bullet.pending').forEach(bullet => {
    const tcEl = bullet.closest('[data-message-role="tool_call"]')
      || bullet.closest('[data-tc-id]')
      || bullet.closest('.msg');
    if (!tcEl) return;
    const rowAgent = tcEl.dataset ? (tcEl.dataset.agent || '').toLowerCase() : '';
    if (targetAgent && rowAgent && rowAgent !== targetAgent) return;
    if (!tcEl.querySelector('.tc-result')) {
      try { _attachToolResult(tcEl, resultText || '[Interrupted]'); }
      catch (_err) {
        bullet.classList.remove('pending');
        bullet.classList.add('done');
        tcEl.querySelectorAll('.tc-bg-btn, .tc-kl-btn').forEach(btn => btn.remove());
      }
    } else {
      bullet.classList.remove('pending');
      bullet.classList.add('done');
      tcEl.querySelectorAll('.tc-bg-btn, .tc-kl-btn').forEach(btn => btn.remove());
    }
    if (tcEl.dataset) delete tcEl.dataset.live;
    changed = true;
  });
  if (changed && typeof applyTechnicalMessageGrouping === 'function') applyTechnicalMessageGrouping();
  return changed;
}


// Expose a reset hook so resumeConv (which clears #messages but
// keeps the SSE socket open) can drop stale DOM references — without
// it, subsequent live events keep targeting detached nodes and the
// freshly-reloaded transcript ends up out of order or truncated.
window._sseClearLiveBlocks = function() {
  for (const k in _taskBlocks) delete _taskBlocks[k];
  for (const k in _delegateGroups) delete _delegateGroups[k];
  for (const k in _delegateSubBlocks) delete _delegateSubBlocks[k];
  for (const k in _pendingToolResults) {
    if (_pendingToolResults[k] && _pendingToolResults[k].timer) clearTimeout(_pendingToolResults[k].timer);
    delete _pendingToolResults[k];
  }
  for (const k in delegateThinkingElements) delete delegateThinkingElements[k];
};


function _getTaskBlock(taskId, iteration, agentName) {
  if (!taskId) return null;
  if (!window.PAWFLOW_GROUP_TASK_MESSAGES) return null;
  const blockKey = taskId + '::iter' + (iteration || 0);
  if (_taskBlocks[blockKey]) return _taskBlocks[blockKey];
  // First event for this iteration — create the block
  const details = document.createElement('details');
  details.className = 'msg task-block';
  details.setAttribute('open', '');
  details.style.cssText = 'margin:6px 0;border:1px solid #333;border-radius:8px;padding:0;background:#1a1a2e;';
  const summary = document.createElement('summary');
  summary.style.cssText = 'cursor:pointer;padding:8px 12px;font-size:12px;color:#6c5ce7;user-select:none;font-weight:600;display:flex;align-items:center;gap:6px;';
  const iterLabel = (iteration || 0) > 1 ? ' iter ' + iteration : '';
  summary.innerHTML = '\u{1F4CB} Task <span style="color:#e0e0e0;font-weight:normal">' + escapeHtml(taskId) + '</span>'
    + (agentName ? ' <span style="color:#888;font-weight:normal">(' + escapeHtml(displayAgentName(agentName)) + iterLabel + ')</span>' : '')
    + ' <span class="task-block-status" style="margin-left:auto;font-size:11px;color:#888">\u25cf running</span>';
  details.appendChild(summary);
  const content = document.createElement('div');
  content.style.cssText = 'padding:4px 12px 8px;max-height:60vh;overflow-y:auto;';
  details.appendChild(content);
  const container = document.getElementById('messages');
  const typingEl = document.getElementById('typing');
  if (typingEl) container.insertBefore(details, typingEl);
  else container.appendChild(details);
  scrollBottom();
  _taskBlocks[blockKey] = {el: details, content: content, summary: summary, agent: agentName, taskId: taskId};
  return _taskBlocks[blockKey];
}


function _taskBlockAppend(taskId, iteration, childEl) {
  const blockKey = taskId + '::iter' + (iteration || 0);
  const block = _taskBlocks[blockKey];
  if (block && childEl) {
    block.content.appendChild(childEl);
    scrollBottom();
  }
}


function _finalizeTaskBlock(taskId, iteration, status, color) {
  const blockKey = taskId + '::iter' + (iteration || 0);
  const block = _taskBlocks[blockKey];
  if (block) {
    const statusEl = block.summary.querySelector('.task-block-status');
    if (statusEl) { statusEl.textContent = status || '\u2713 done'; statusEl.style.color = color || '#4ecdc4'; }
    block.el.removeAttribute('open');
  }
}

function renderThinkingContent(data, reconcileFinal) {
  const agent = data.agent_name || '';
  const aKey = agentKey(agent);
  const textDelta = data.text || '';
  const msgId = data.msg_id || '';
  if (!textDelta && !thinkingElements[aKey]) return;
  const current = thinkingElements[aKey];
  if (current && msgId && current.msgId && current.msgId !== msgId) {
    finalizeThinking(agent, 'thinking-message');
  }
  if (!thinkingElements[aKey]) {
    // Create collapsible details element
    const details = document.createElement('details');
    details.className = 'msg thinking-block';
    details.dataset.messageRole = 'thinking';
    details.dataset.live = '1';
    details.dataset.sortTs = String((typeof _messageSortTs === 'function') ? _messageSortTs(data) : Date.now() / 1000);
    details.setAttribute('open', '');
    details.style.cssText = 'margin:4px 0;border-left:3px solid #6b7280;padding:4px 8px;opacity:0.7;';
    const summary = document.createElement('summary');
    summary.style.cssText = 'cursor:pointer;font-size:12px;color:#9ca3af;font-style:italic;user-select:none;';
    summary.textContent = t('thinking') + '...';
    details.appendChild(summary);
    const content = document.createElement('div');
    content.style.cssText = 'font-size:12px;color:#9ca3af;font-style:italic;white-space:pre-wrap;max-height:300px;overflow-y:auto;';
    details.appendChild(content);
    // If this thinking belongs to a delegate-reply turn, place the
    // block inside the shared delegate frame for (from→to).
    let _placed = false;
    const _dsrc = data.source || {};
    if (_dsrc.type === 'agent_delegate' && _dsrc.from && _dsrc.to) {
      // MUST match messages.js bidirectional key: sorted pair so
      // both A→B and B→A land in the same shared delegate block.
      const _dpair = [_dsrc.from, _dsrc.to].map(s => String(s).toLowerCase()).sort();
      const _dkey = 'delegate-shared::' + _dpair[0] + '::' + _dpair[1];
      const _dblock = document.querySelector('[data-delegate-key="' + CSS.escape(_dkey) + '"]');
      const _dbody = _dblock && _dblock.querySelector('.delegate-body');
      if (_dbody) { _dbody.appendChild(details); _placed = true; }
    }
    if (!_placed && data.task_id) {
      const tb = _getTaskBlock(data.task_id, data.task_iteration, agent);
      if (tb) { tb.content.appendChild(details); scrollBottom(); _placed = true; }
    }
    if (!_placed) {
      const _msgContainer = document.getElementById('messages');
      const _sortTs = (typeof _messageSortTs === 'function') ? _messageSortTs(data) : Date.now() / 1000;
      if (typeof _insertMessageChronologically === 'function') {
        _insertMessageChronologically(_msgContainer, details, _sortTs);
      } else {
        const _typingEl = document.getElementById('typing');
        if (_typingEl) _msgContainer.insertBefore(details, _typingEl);
        else _msgContainer.appendChild(details);
      }
    }
    if (typeof applyTechnicalMessageGrouping === 'function') applyTechnicalMessageGrouping();
    thinkingElements[aKey] = {el: details, content: content, summary: summary, text: '', msgId: msgId, startTime: Date.now()};
    scrollBottom();
  }
  const te = thinkingElements[aKey];
  if (msgId && !te.msgId) te.msgId = msgId;
  if (reconcileFinal) {
    te.text = textDelta.startsWith(te.text)
      ? te.text + textDelta.slice(te.text.length)
      : textDelta;
  } else {
    te.text += textDelta;
  }
  te.content.textContent = te.text;
  if (typeof applyTechnicalMessageGrouping === 'function') applyTechnicalMessageGrouping();
  scrollBottom();
}


// Finalize a thinking block when any non-thinking event arrives for that
// agent. Consecutive thinking_content chunks append to the same block; once
// a tool call, message, token, result, or done event arrives, the next
// thinking_content must create a new block.
function finalizeThinking(agentName, reason) {
  const aKey = agentKey(agentName || '');
  const te = thinkingElements[aKey];
  if (!te) return;
  const elapsed = (Date.now() - te.startTime) / 1000;
  const group = te.el.closest && te.el.closest('.technical-group');
  if (!te.text.trim()) {
    te.el.remove();
    if (group && typeof _updateTechnicalGroupSummary === 'function') _updateTechnicalGroupSummary(group);
    delete thinkingElements[aKey];
  } else {
    te.summary.textContent = t('thoughtFor', { sec: elapsed.toFixed(1) });
    te.el.setAttribute('open', '');
    if (te.el.dataset) delete te.el.dataset.live;
    delete thinkingElements[aKey];
  }
  if (typeof applyTechnicalMessageGrouping === 'function') applyTechnicalMessageGrouping();
}


function finalizeThinkingFromEvent(data, reason) {
  const agent = (data && (data.agent_name || (data.source && data.source.name))) || '';
  if (agent) finalizeThinking(agent, reason);
}


function finalizeDelegateThinking(taskId) {
  const te = taskId ? delegateThinkingElements[taskId] : null;
  if (!te) return;
  if (!String(te.text || '').trim()) {
    te.el.remove();
  } else {
    const elapsed = (Date.now() - te.startTime) / 1000;
    te.summary.textContent = t('thoughtFor', { sec: elapsed.toFixed(1) });
    te.el.setAttribute('open', '');
    if (te.el.dataset) delete te.el.dataset.live;
  }
  delete delegateThinkingElements[taskId];
}


function _getOrCreateGroup(delegateTcId, srcAgent, total, sourceTaskId) {
  if (!window.PAWFLOW_GROUP_DELEGATE_MESSAGES) return null;
  if (!delegateTcId) return null;
  if (_delegateGroups[delegateTcId]) return _delegateGroups[delegateTcId];
  const details = document.createElement('details');
  details.className = 'msg delegate-block delegate-group';
  details.dataset.messageRole = 'sub_agent_trace';
  details.dataset.sortTs = String(Date.now() / 1000);
  details.setAttribute('open', '');
  const summary = document.createElement('summary');
  summary.className = 'delegate-header';
  const label = total > 1
    ? '\u{1F500} ' + escapeHtml(displayAgentName(srcAgent)) + ' \u2192 Delegate (' + total + ' agents)'
    : '\u{1F500} ' + escapeHtml(displayAgentName(srcAgent));
  summary.innerHTML = label;
  details.appendChild(summary);
  const content = document.createElement('div');
  content.className = 'delegate-body';
  details.appendChild(content);
  // If spawned from a task, nest inside the task block
  let parentFound = false;
  if (sourceTaskId) {
    for (const bk of Object.keys(_taskBlocks).reverse()) {
      if (bk.startsWith(sourceTaskId + '::iter')) {
        _taskBlocks[bk].content.appendChild(details);
        parentFound = true;
        break;
      }
    }
  }
  if (!parentFound) {
    const container = document.getElementById('messages');
    const typingEl = document.getElementById('typing');
    if (typingEl) container.insertBefore(details, typingEl);
    else container.appendChild(details);
  }
  if (typeof applyTechnicalMessageGrouping === 'function') applyTechnicalMessageGrouping();
  scrollBottom();
  _delegateGroups[delegateTcId] = { el: details, content, summary, total: total || 1, doneCount: 0, subBlocks: {} };
  return _delegateGroups[delegateTcId];
}


function _getOrCreateSubBlock(delegateTcId, taskId, dstAgent, llmService, message) {
  if (!window.PAWFLOW_GROUP_DELEGATE_MESSAGES) return null;
  if (_delegateSubBlocks[taskId]) return _delegateSubBlocks[taskId];
  // Ensure group exists (fallback for missing delegate_group_start)
  let group = _delegateGroups[delegateTcId];
  if (!group) group = _getOrCreateGroup(delegateTcId, '', 1);
  const isMulti = group.total > 1;
  // For single-agent, update the group header with arrow
  if (!isMulti && dstAgent) {
    const svcLabel = llmService ? ' via ' + escapeHtml(llmService) : '';
    group.summary.innerHTML = '\u{1F500} <span class="delegate-src">' + escapeHtml(displayAgentName(group.summary.dataset.src || ''))
      + '</span> \u2192 <span class="delegate-dst">' + escapeHtml(displayAgentName(dstAgent)) + '</span>'
      + svcLabel;
  }
  // Create sub-block (details for multi, div for single)
  let subEl, subContent, subSummary;
  if (isMulti) {
    subEl = document.createElement('details');
    subEl.className = 'delegate-sub-block';
    subEl.setAttribute('open', '');
    subSummary = document.createElement('summary');
    subSummary.className = 'delegate-sub-header';
    const svcLabel = llmService ? ' via ' + escapeHtml(llmService) : '';
    subSummary.innerHTML = '\u25b8 <span class="delegate-dst">' + escapeHtml(displayAgentName(dstAgent)) + '</span>'
      + svcLabel
      + ' <button class="delegate-cancel-btn" data-task-id="' + escapeHtml(taskId) + '" title="' + escapeHtml(t('cancelThisAgent')) + '">\u2715</button>';
    subEl.appendChild(subSummary);
    subContent = document.createElement('div');
    subContent.className = 'delegate-sub-body';
    subEl.appendChild(subContent);
  } else {
    // Single agent — content goes directly in the group body
    subEl = group.el;
    subContent = group.content;
    subSummary = group.summary;
    // Add cancel button to single-agent header
    if (!subSummary.querySelector('.delegate-cancel-btn')) {
      const btn = document.createElement('button');
      btn.className = 'delegate-cancel-btn';
      btn.dataset.taskId = taskId;
      btn.title = t('cancelThisAgent');
      btn.textContent = '\u2715';
      subSummary.appendChild(btn);
    }
  }
  // Show the message from parent agent
  if (message) {
    const msgEl = document.createElement('div');
    msgEl.className = 'delegate-message';
    msgEl.innerHTML = '\u{1F4E9} ' + renderMarkdown(message);
    subContent.appendChild(msgEl);
  }
  if (isMulti) {
    group.content.appendChild(subEl);
    scrollBottom();
  }
  // Tag for cross-rendering dedupe (messages.js looks up this attribute
  // when classifying a sub_agent_trace from the store and skips it if a
  // live SSE block for the same sub-agent task already exists).
  if (subEl && taskId) subEl.dataset.delegateTaskId = taskId;
  const block = { el: subEl, content: subContent, summary: subSummary, agent: dstAgent, taskId };
  _delegateSubBlocks[taskId] = block;
  group.subBlocks[taskId] = block;
  return block;
}


function _subBlockAppend(taskId, childEl) {
  const block = _delegateSubBlocks[taskId];
  if (block && childEl) {
    block.content.appendChild(childEl);
    scrollBottom();
  }
}


const _TOOL_DISPLAY = {
  bash: 'Bash', read: 'Read', write: 'Write', edit: 'Update',
  glob: 'Glob', grep: 'Grep', delete: 'Delete', mkdir: 'Mkdir',
  stat: 'Stat', exists: 'Exists', list_dir: 'ListDir',
  batch_edit: 'BatchEdit', apply_patch: 'ApplyPatch',
  find_replace: 'FindReplace', notebook_edit: 'NotebookEdit',
  copy: 'Copy', execute_script: 'Script',
  web_search: 'WebSearch', fetch: 'Fetch',
  generate_image: 'ImageGen', generate_video: 'VideoGen',
  remember: 'Remember', recall: 'Recall', semantic_recall: 'SemanticRecall',
  forget: 'Forget', delegate: 'Delegate',
  show_file: 'ShowFile', compact_result: 'CompactResult',
  get_tool_schema: 'GetToolSchema',
};

const _MCP_USE_TOOL_WRAPPERS = new Set([
  'use_tool', 'mcp_pawflow_use_tool', 'mcp_pawflow.use_tool',
  'mcp__pawflow__use_tool', 'mcp__pawflow__.use_tool', 'pawflow.use_tool', 'pawflow/use_tool'
]);

const _MCP_SCHEMA_WRAPPERS = new Set([
  'get_tool_schema', 'mcp_pawflow_get_tool_schema', 'mcp_pawflow.get_tool_schema',
  'mcp__pawflow__get_tool_schema', 'mcp__pawflow__.get_tool_schema', 'pawflow.get_tool_schema', 'pawflow/get_tool_schema'
]);

function _isMcpDisplayedToolCallName(name) {
  const raw = String(name || '');
  return raw === 'call_mcp_tool' || raw.startsWith('pawflow/')
      || _MCP_USE_TOOL_WRAPPERS.has(raw) || _MCP_SCHEMA_WRAPPERS.has(raw);
}

function _hasCompleteMcpDisplayedToolCall(name, args) {
  const rawName = String(name || '');
  let payload = args || {};
  if (typeof payload === 'string') {
    try { payload = JSON.parse(payload); } catch(e) { return false; }
  }
  if (rawName === 'call_mcp_tool') {
    return !!(payload && typeof payload === 'object'
      && (payload.ToolName || payload.toolName || payload.tool_name));
  }
  if (_MCP_USE_TOOL_WRAPPERS.has(rawName)) {
    if (!payload || typeof payload !== 'object') return false;
    if (!payload.tool_name && payload.parameters && typeof payload.parameters === 'object') {
      payload = payload.parameters;
    }
    return !!(payload && typeof payload === 'object' && payload.tool_name);
  }
  return true;
}

function _unwrapDisplayedToolCall(name, args) {
  let toolName = name || '?';
  let toolArgs = args || {};
  if (typeof toolArgs === 'string') {
    try { toolArgs = JSON.parse(toolArgs); } catch(e) {}
  }
  if (toolName === 'call_mcp_tool' && toolArgs && typeof toolArgs === 'object') {
    toolName = toolArgs.ToolName || toolArgs.toolName || toolArgs.tool_name || toolName;
    toolArgs = toolArgs.Arguments || toolArgs.arguments || toolArgs.Parameters || toolArgs.parameters || {};
    if (typeof toolArgs === 'string') {
      try { toolArgs = JSON.parse(toolArgs); } catch(e) {}
    }
  }
  if (typeof toolName === 'string' && toolName.startsWith('pawflow/')) {
    toolName = toolName.substring('pawflow/'.length);
  }
  if (_MCP_SCHEMA_WRAPPERS.has(toolName)) {
    toolName = 'get_tool_schema';
  }
  if (_MCP_USE_TOOL_WRAPPERS.has(toolName) && toolArgs && typeof toolArgs === 'object') {
    const payload = (!toolArgs.tool_name && toolArgs.parameters && typeof toolArgs.parameters === 'object')
      ? toolArgs.parameters
      : toolArgs;
    if (!payload.tool_name) return { toolName, toolArgs };
    toolName = payload.tool_name;
    toolArgs = payload.arguments || payload.parameters || {};
    if (typeof toolArgs === 'string') {
      try { toolArgs = JSON.parse(toolArgs); } catch(e) {}
    }
  }
  return { toolName, toolArgs };
}

function _toolCallSummary(name, args) {
  const normalized = _unwrapDisplayedToolCall(name, args);
  name = normalized.toolName;
  args = normalized.toolArgs;
  const display = _TOOL_DISPLAY[name] || name;
  // Build summary from actual args sent (not hardcoded param names)
  let summary = '';
  if (args && typeof args === 'object') {
    const keys = Object.keys(args);
    if (keys.length === 0) {
      summary = '';
    } else if (keys.length === 1) {
      // Single arg: show value directly (truncated)
      const val = String(args[keys[0]]);
      summary = val.length > 200 ? val.substring(0, 200) + '...' : val;
    } else {
      // Multiple args: show key=value pairs (truncated)
      const parts = [];
      let total = 0;
      for (const k of keys) {
        const val = String(args[k]);
        const short = val.length > 80 ? val.substring(0, 80) + '...' : val;
        const part = k + '=' + short;
        if (total + part.length > 200) { parts.push('...'); break; }
        parts.push(part);
        total += part.length;
      }
      summary = parts.join(', ');
    }
  }
  return display + '(' + summary + ')';
}

function _toolOriginValue(extra, toolName) {
  const raw = (extra && (extra.tool_origin || extra.tool_source)) || '';
  const value = String(raw || '').toLowerCase();
  if (value === 'mcp' || value === 'native') return value;
  const rawName = (extra && (extra.tool_name || extra.tool)) || toolName || '';
  return _isMcpDisplayedToolCallName(rawName) ? 'mcp' : '';
}

function _toolOriginBadge(extra, toolName) {
  const origin = _toolOriginValue(extra, toolName);
  if (!origin) return '';
  const label = origin === 'mcp' ? 'MCP' : 'Native';
  const bg = origin === 'mcp' ? '#123b5d' : '#4a2b16';
  const fg = origin === 'mcp' ? '#8bd3ff' : '#ffbf80';
  return '<span class="tc-origin tc-origin-' + origin + '" style="font-size:10px;border:1px solid ' + fg + ';color:' + fg + ';background:' + bg + ';border-radius:3px;padding:1px 3px;margin-right:4px;vertical-align:middle">' + label + '</span>';
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
    if (source.containerized) label += ' \uD83D\uDC33';  // whale emoji = Docker
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
  const tokIn = extra.tokens_in || src.tokens_in || 0;
  const tokOut = extra.tokens_out || src.tokens_out || 0;
  const dur = extra.duration_ms || 0;
  const costUsd = extra.cost_usd || 0;
  const parts = [];
  if (model) parts.push(model);
  if (provider && provider !== model) parts.push(provider);
  if (tokIn || tokOut) parts.push('\u2191' + tokIn + ' \u2193' + tokOut);
  if (costUsd) parts.push('$' + costUsd.toFixed(4));
  if (dur) parts.push((dur / 1000).toFixed(1) + 's');
  if (!parts.length) return '';
  // Compact summary line (always visible)
  let line = '<span class="meta-summary">' + parts.join(' \u00b7 ') + '</span>';
  // Expandable details
  const details = [];
  if (baseUrl) details.push('endpoint: ' + escapeHtml(baseUrl));
  if (tokIn || tokOut) details.push('tokens: ' + tokIn + ' in / ' + tokOut + ' out (' + (tokIn + tokOut) + ' total)');
  if (costUsd) details.push('cost: $' + costUsd.toFixed(6));
  if (dur) details.push('duration: ' + (dur / 1000).toFixed(1) + 's');
  if (details.length) {
    line += '<span class="meta-details">' + details.join(' \u00b7 ') + '</span>';
  }
  return '<div class="msg-meta" onclick="this.classList.toggle(\'expanded\')">' + line + '</div>';
}


function makeTimeHtml(tsEpoch) {
  const msgTime = tsEpoch ? new Date(tsEpoch * 1000) : new Date();
  const _today = new Date();
  const _sameDay = msgTime.toDateString() === _today.toDateString();
  const timeStr = _sameDay
    ? msgTime.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'})
    : msgTime.toLocaleDateString([], {day: '2-digit', month: '2-digit'}) + ' '
      + msgTime.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'});
  return '<span class="msg-time">' + timeStr + '</span>';
}

function _messageSortTs(extra) {
  const raw = extra && (extra.timestamp || extra.ts);
  const n = Number(raw);
  return Number.isFinite(n) && n > 0 ? n : Date.now() / 1000;
}

function _insertMessageChronologically(container, el, sortTs) {
  if (!container) return;
  el.dataset.sortTs = String(sortTs);
  const typingEl = document.getElementById('typing');
  const fallback = typingEl && typingEl.parentNode === container ? typingEl : null;
  for (const child of Array.from(container.children)) {
    if (child === fallback) break;
    const childTs = Number(child.dataset && child.dataset.sortTs);
    if (Number.isFinite(childTs) && childTs > sortTs) {
      container.insertBefore(el, child);
      return;
    }
  }
  if (fallback) container.insertBefore(el, fallback);
  else container.appendChild(el);
}

window.PAWFLOW_GROUP_TECHNICAL_MESSAGES = true;
window.PAWFLOW_GROUP_TASK_MESSAGES = true;
window.PAWFLOW_GROUP_DELEGATE_MESSAGES = true;
window.PAWFLOW_SUSPEND_TECHNICAL_GROUPING = 0;

function suspendTechnicalMessageGrouping() {
  window.PAWFLOW_SUSPEND_TECHNICAL_GROUPING = (window.PAWFLOW_SUSPEND_TECHNICAL_GROUPING || 0) + 1;
}

function resumeTechnicalMessageGrouping(applyNow) {
  window.PAWFLOW_SUSPEND_TECHNICAL_GROUPING = Math.max(0, (window.PAWFLOW_SUSPEND_TECHNICAL_GROUPING || 0) - 1);
  if (applyNow && !window.PAWFLOW_SUSPEND_TECHNICAL_GROUPING) applyTechnicalMessageGrouping();
}

function setTechnicalMessageGrouping(enabled) {
  window.PAWFLOW_GROUP_TECHNICAL_MESSAGES = !!enabled;
  applyTechnicalMessageGrouping();
}

function setTaskMessageGrouping(enabled) {
  window.PAWFLOW_GROUP_TASK_MESSAGES = !!enabled;
}

function setDelegateMessageGrouping(enabled) {
  window.PAWFLOW_GROUP_DELEGATE_MESSAGES = !!enabled;
}

function _visibleTextWithoutMessageChrome(el) {
  if (!el) return '';
  const clone = el.cloneNode(true);
  clone.querySelectorAll('.msg-actions, .msg-meta').forEach(n => n.remove());
  return String(clone.textContent || '').replace(/[\u200b-\u200d\ufeff]/g, '').replace(/\s+/g, ' ').trim();
}

function _isAgentChromeOnlyElement(el) {
  if (!el) return false;
  const text = _visibleTextWithoutMessageChrome(el);
  return /^(assistant|user)(\s+\d{1,2}:\d{2}(:\d{2})?)?$/i.test(text);
}

function _isAssistantPlaceholderElement(el) {
  if (!el) return false;
  const role = el.dataset ? (el.dataset.messageRole || '') : '';
  const messageChrome = role === 'assistant' || role === 'user'
    || el.classList.contains('assistant') || el.classList.contains('subagent');
  const text = _visibleTextWithoutMessageChrome(el);
  if (!text) return messageChrome;
  if (_isAgentChromeOnlyElement(el)) return true;
  return messageChrome && (text === 'assistant' || text === 'user');
}

function _isTechnicalMessageElement(el) {
  if (!el || !el.classList || el.classList.contains('technical-group')) return false;
  if (el.id === 'typing' || el.id === 'contextOpTyping' || el.id === 'loadMoreBanner') return false;
  if (el.dataset && (el.dataset.transientUi === '1' || el.dataset.technicalBoundary === '1')) return false;
  if (el.classList.contains('typing') || el.classList.contains('active-row') || el.classList.contains('active-panel')) return false;
  if (_isAssistantPlaceholderElement(el)) return false;
  // task-block / delegate-block are containers, not technical events — their
  // inner tool_calls are grouped via the nested-scope pass in
  // applyTechnicalMessageGrouping, so they must stay at top level themselves.
  if (el.classList.contains('task-block') || el.classList.contains('delegate-block')) return false;
  const role = el.dataset ? (el.dataset.messageRole || '') : '';
  if (role === 'sub_agent_trace') return false;
  if ([
    'tool_call', 'tool', 'tool_result', 'thinking',
    'agent-result', 'flowfile_in',
  ].includes(role)) return true;
  return el.classList.contains('tool')
    || el.classList.contains('thinking-block')
    || el.classList.contains('agent-result');
}

function _unwrapTechnicalGroups(container) {
  for (const group of Array.from(container.querySelectorAll(':scope > .technical-group'))) {
    const body = group.querySelector('.technical-group-body');
    if (body) {
      while (body.firstChild) container.insertBefore(body.firstChild, group);
    }
    group.remove();
  }
}

function _technicalGroupSummary(count) {
  const eventLabel = t(count === 1 ? 'technicalDetailsEventSingular' : 'technicalDetailsEventPlural');
  return t('technicalDetailsSummary', { count, eventLabel });
}

function _extractNonTechnicalChildren(group) {
  if (!group || !group.parentNode) return;
  const body = group.querySelector('.technical-group-body');
  if (!body) return;
  for (const child of Array.from(body.children)) {
    if (!_isTechnicalMessageElement(child)) {
      group.parentNode.insertBefore(child, group);
    }
  }
}

function _updateTechnicalGroupSummary(group) {
  if (!group) return;
  _extractNonTechnicalChildren(group);
  const body = group.querySelector('.technical-group-body');
  const summary = group.querySelector('summary');
  if (!body || !summary) return;
  const count = body.children.length;
  if (!count) {
    group.remove();
    return;
  }
  summary.textContent = _technicalGroupSummary(count);
}

function _isLiveTechnicalElement(el) {
  if (!el || !el.dataset) return false;
  return el.dataset.live === '1'
    || !!(el.querySelector && el.querySelector('.tc-bullet.pending'));
}

function _hasVisibleTechnicalContent(el) {
  if (!el) return false;
  if (_isAgentChromeOnlyElement(el)) return false;
  if (_isLiveTechnicalElement(el)) return true;
  if (el.classList && el.classList.contains('thinking-block')) {
    const parts = Array.from(el.children).filter(child => child.tagName !== 'SUMMARY');
    return parts.some(child => String(child.textContent || '').trim());
  }
  if (el.classList && (el.classList.contains('task-block') || el.classList.contains('delegate-block'))) {
    return !!String(el.textContent || '').replace(/\s+/g, ' ').trim();
  }
  if (el.classList && el.classList.contains('tool')) {
    return !!(el.dataset && (el.dataset.tool || el.dataset.path || el.dataset.command))
      || !!el.querySelector('.tc-result, .tc-output, .tool-result, pre');
  }
  const clone = el.cloneNode(true);
  clone.querySelectorAll('.msg-time, .source-badge, .msg-meta, .msg-actions').forEach(n => n.remove());
  return !!String(clone.textContent || '').replace(/\s+/g, ' ').trim();
}

function _openLiveTechnicalElement(el) {
  if (!el) return;
  if (el.tagName === 'DETAILS') el.setAttribute('open', '');
  if (el.classList && el.classList.contains('thinking-block')) el.setAttribute('open', '');
  if (el.querySelectorAll) {
    el.querySelectorAll('.tc-bullet.pending').forEach(bullet => {
      const details = bullet.closest && bullet.closest('details');
      if (details && el.contains(details)) details.setAttribute('open', '');
    });
    el.querySelectorAll('[data-live="1"]').forEach(liveEl => {
      if (liveEl.tagName === 'DETAILS') liveEl.setAttribute('open', '');
      if (liveEl.classList && liveEl.classList.contains('thinking-block')) liveEl.setAttribute('open', '');
    });
  }
}

function _markTechnicalGroupSettled(group) {
  if (!group || !group.querySelectorAll) return;
  group.querySelectorAll('[data-live="1"]').forEach(liveEl => {
    if (liveEl.dataset) delete liveEl.dataset.live;
    if (liveEl.classList && liveEl.classList.contains('thinking-block')) liveEl.removeAttribute('open');
  });
}

function _markTechnicalGroupUserIntent(group) {
  if (!group) return;
  setTimeout(() => {
    if (!group.isConnected) return;
    if (group.hasAttribute('open')) group.dataset.userOpen = '1';
    else if (group.dataset) delete group.dataset.userOpen;
  }, 0);
}

function collapseTechnicalGroups() {
  const container = document.getElementById('messages');
  if (!container) return;
  container.querySelectorAll(':scope > .technical-group').forEach(group => {
    const keepOpen = group.dataset.userOpen === '1' || _isLiveTechnicalElement(group);
    _markTechnicalGroupSettled(group);
    if (keepOpen) group.setAttribute('open', '');
    else group.removeAttribute('open');
  });
}

function _createTechnicalGroupBefore(container, anchor) {
  const group = document.createElement('details');
  group.className = 'msg technical-group';
  group.dataset.sortTs = anchor && anchor.dataset ? (anchor.dataset.sortTs || String(Date.now() / 1000)) : String(Date.now() / 1000);
  const summary = document.createElement('summary');
  summary.className = 'technical-group-header';
  summary.textContent = _technicalGroupSummary(0);
  summary.addEventListener('click', () => _markTechnicalGroupUserIntent(group));
  const body = document.createElement('div');
  body.className = 'technical-group-body';
  group.appendChild(summary);
  group.appendChild(body);
  const safeAnchor = anchor && anchor.parentNode === container ? anchor : null;
  container.insertBefore(group, safeAnchor);
  return group;
}

function _groupTechnicalIn(container) {
  let group = null;
  for (const child of Array.from(container.children)) {
    if (child.id === 'typing' || child.id === 'loadMoreBanner') {
      if (group) _updateTechnicalGroupSummary(group);
      if (group && !group.isConnected) group = null;
      group = null;
      continue;
    }
    if (child.classList && child.classList.contains('technical-group')) {
      if (group && group !== child) {
        const body = group.querySelector('.technical-group-body');
        const childBody = child.querySelector('.technical-group-body');
        if (body && childBody) {
          while (childBody.firstChild) body.appendChild(childBody.firstChild);
          child.remove();
          _updateTechnicalGroupSummary(group);
          continue;
        }
      }
      _updateTechnicalGroupSummary(child);
      group = child.isConnected ? child : null;
      continue;
    }
    if (!_isTechnicalMessageElement(child)) {
      if (group) _updateTechnicalGroupSummary(group);
      if (group && !group.isConnected) group = null;
      group = null;
      continue;
    }
    if (!_hasVisibleTechnicalContent(child)) {
      if (window.DEBUG_TECHNICAL_GROUPING) console.debug('[technical-grouping] drop empty technical element', {
        role: child.dataset && child.dataset.messageRole,
        className: child.className,
        text: String(child.textContent || '').trim().substring(0, 120),
      });
      child.remove();
      continue;
    }
    if (!group) group = _createTechnicalGroupBefore(container, child);
    const body = group.querySelector('.technical-group-body');
    const liveChild = _isLiveTechnicalElement(child);
    if (body) body.appendChild(child);
    if (liveChild) {
      _openLiveTechnicalElement(child);
      group.setAttribute('open', '');
    }
    _updateTechnicalGroupSummary(group);
  }
  if (group) _updateTechnicalGroupSummary(group);
}

const _NESTED_TECHNICAL_SCOPE_SELECTOR = '.task-block > div:not(summary), .delegate-body, .delegate-sub-body';

function _nestedTechnicalScopes(container) {
  if (!container || !container.querySelectorAll) return [];
  return Array.from(container.querySelectorAll(_NESTED_TECHNICAL_SCOPE_SELECTOR));
}

function applyTechnicalMessageGrouping() {
  if (window.PAWFLOW_SUSPEND_TECHNICAL_GROUPING) return;
  const container = document.getElementById('messages');
  if (!container) return;
  if (!window.PAWFLOW_GROUP_TECHNICAL_MESSAGES) {
    _unwrapTechnicalGroups(container);
    for (const inner of _nestedTechnicalScopes(container)) _unwrapTechnicalGroups(inner);
    return;
  }
  _groupTechnicalIn(container);
  for (const inner of _nestedTechnicalScopes(container)) _groupTechnicalIn(inner);
}

function _toolCallSelector(tcId) {
  return '[data-tc-id="' + (window.CSS && CSS.escape ? CSS.escape(tcId) : String(tcId).replace(/"/g, '\\"')) + '"]';
}

function findToolCallElement(tcId, root) {
  if (!tcId) return null;
  const base = root || document;
  let matches;
  try { matches = base.querySelectorAll(_toolCallSelector(tcId)); }
  catch (_err) { matches = base.querySelectorAll('[data-tc-id="' + tcId + '"]'); }
  for (const el of matches) {
    if (el.dataset && el.dataset.messageRole === 'tool_call') return el;
  }
  return null;
}

const LIVE_DISPLAY_WINDOW_MULTIPLIER = 4;

function trimLiveDisplayWindowIfAutoscrolling(wasAutoscroll) {
  if (!wasAutoscroll) return;
  const container = document.getElementById('messages');
  if (!container) return;
  const maxVisible = Math.max(displayWindow || 50, 50) * LIVE_DISPLAY_WINDOW_MULTIPLIER;
  const rows = Array.from(container.children).filter(el => {
    if (!el.classList || !el.classList.contains('msg')) return false;
    if (el.dataset && el.dataset.live === '1') return false;
    if (el.querySelector && el.querySelector('[data-live="1"]')) return false;
    return true;
  });
  let excess = rows.length - maxVisible;
  if (excess <= 0) return;
  for (const el of rows) {
    if (excess <= 0) break;
    const mid = el.dataset && el.dataset.msgid;
    if (mid && typeof _selectedMsgIds !== 'undefined' && _selectedMsgIds.has(mid)) continue;
    el.remove();
    excess--;
  }
  hasMoreMessages = true;
  if (typeof _updateLoadMoreBanner === 'function') _updateLoadMoreBanner();
}

function addMsg(role, text, extra) {
  // Dedup by msg_id — if we've already displayed this message, skip
  const msgId = (extra && extra.msg_id) || '';
  if (msgId) {
    if (_seenMsgIds.has(msgId)) {
      return null;
    }
    _seenMsgIds.add(msgId);
  }
  if (role === 'tool_call' && extra && extra.tc_id
      && findToolCallElement(extra.tc_id)) {
    return null;
  }
  // Background-tool results are written to transcript as role=user (that
  // is what CC's wire protocol requires for tool_result replies), but in
  // the chat UI they should render as a collapsible tool_result block,
  // not as the user's own voice. The payload sent to the LLM stays
  // role=user; this is a display-only relabel. Source tag
  // `{type:'system', name:'background'}` is set by
  // core.background_tool.py:_write_cc_result.
  //
  // role='tool_result' (not 'tool') so the renderer at the tool_result
  // branch below produces <details>…</details> — otherwise we fall
  // into the tool_call compact-header branch and the long output is
  // rendered as a single wall of text without any collapse.
  if (role === 'user' && extra && extra.source
      && extra.source.type === 'system'
      && extra.source.name === 'background') {
    role = 'tool_result';
  }
  // PushNotification bell row: proactive attention signal from an agent.
  // Rendered as a compact 🔔 line (not a full user bubble) so it doesn't
  // clutter the transcript but stays visible on history reload. Append
  // + return here — bypassing the generic role branches below.
  if (role === 'user' && extra && extra.source
      && extra.source.type === 'system'
      && extra.source.name === 'notification') {
    const notifEl = document.createElement('div');
    notifEl.className = 'msg notification-row';
    notifEl.style.cssText = (
      'background:#1a3a2a;color:#4ecdc4;border-left:3px solid #4ecdc4;'
      + 'padding:6px 12px;margin:4px 0;border-radius:4px;'
      + 'font-size:12.5px;display:flex;align-items:baseline;gap:8px;'
    );
    const fromAgent = displayAgentName(extra.source.agent || 'agent');
    const notifSortTs = _messageSortTs(extra);
    const timeHtml = makeTimeHtml(notifSortTs);
    if (extra && extra.msg_id) notifEl.dataset.msgid = extra.msg_id;
    notifEl.innerHTML = (
      '<span style="font-size:14px;">🔔</span>'
      + '<span style="font-weight:600;">' + escapeHtml(fromAgent) + ':</span>'
      + '<span style="flex:1;">' + escapeHtml(text) + '</span>'
      + timeHtml
    );
    const notifContainer = document.getElementById('messages');
    const notifShouldScroll = isNearBottom();
    _insertMessageChronologically(notifContainer, notifEl, notifSortTs);
    trimLiveDisplayWindowIfAutoscrolling(notifShouldScroll);
    scrollBottom(notifShouldScroll);
    return notifEl;
  }
  if (role === 'tool_call' || role === 'tool') {
    const rawToolName = (extra && (extra.tool_name || extra.tool)) || text || '?';
    const rawToolArgs = (extra && extra.arguments !== undefined)
      ? extra.arguments
      : ((extra && extra.tool_args) || {});
    if (!_hasCompleteMcpDisplayedToolCall(rawToolName, rawToolArgs)) return null;
  }
  const el = document.createElement('div');
  // Map roles to CSS classes
  let cssClass = role;
  if (role === 'tool_call' || role === 'tool_result') cssClass = 'tool';
  else if (role === 'assistant' && extra && extra.source && extra.source.type === 'agent') {
    const srcName = (extra.source.name || '').toLowerCase();
    if (srcName) cssClass = 'subagent';
  }
  el.className = 'msg ' + cssClass;
  el.dataset.messageRole = role;
  if (role === 'assistant' || role === 'user') el.dataset.technicalBoundary = '1';
  if ((role === 'assistant' || role === 'user') && !String(text || '').trim()) el.dataset.transientUi = '1';
  if (extra && extra.live) el.dataset.live = '1';
  if ((role === 'user' || role === 'assistant') && String(text || '').trim()) collapseTechnicalGroups();
  if (msgId) el.dataset.msgid = msgId;
  el.dataset.insertedAt = String(Date.now());
  el.addEventListener('click', function(e) {
    if (e.ctrlKey || e.shiftKey) { e.preventDefault(); toggleMsgSelect(this, e); }
  });
  // Multi-part content (user messages with attachments after reload)
  let _attachHtml = '';
  if (Array.isArray(text)) {
    const _textParts = [];
    for (const _p of text) {
      if (_p.type === 'text') _textParts.push(_p.text || '');
      else if (_p.type === 'image_ref' && _p.file_id)
        _attachHtml += '<img class="chat-image" src="/files/' + encodeURIComponent(_p.file_id) + '/' + encodeURIComponent(_p.filename || 'image') + '">';
      else if (_p.type === 'file_ref' && _p.file_id)
        _attachHtml += '<span class="doc-badge">\u{1F4CE} ' + escapeHtml(_p.filename || 'file') + '</span> ';
    }
    text = _textParts.join('\n');
  }
  el.dataset.rawText = (text || '').substring(0, 500);
  if (extra && extra.raw_index !== undefined) el.dataset.rawIndex = extra.raw_index;
  const badge = (extra && extra.source) ? sourceBadge(extra.source) : '';
  // Timestamp — use provided timestamp or current time
  const _ts = _messageSortTs(extra);
  const timeHtml = makeTimeHtml(_ts);

  // Action buttons (copy + delete + reply) for all user-visible messages
  let actionsHtml = '';
  if (role === 'user' || role === 'assistant') {
    actionsHtml = '<span class="msg-actions">'
      + '<button onclick="setReplyTo(this)" title="' + escapeHtml(t('reply')) + '">\u21A9</button>'
      + (role === 'assistant' ? '<button onclick="speakMsg(this)" title="' + escapeHtml(t('readMessage')) + '">\u{1F50A}</button>' : '')
      + '<button onclick="copyMsg(this)" title="' + escapeHtml(t('copy')) + '">\u{1F4CB}</button>'
      + '<button onclick="copyMsgId(this)" title="' + escapeHtml(t('copyMsgId')) + '">ID</button>'
      + '<button onclick="restartFromMsg(this)" title="' + escapeHtml(t('restartFromHere')) + '">\u21BA</button>'
      + '<button onclick="deleteMsg(this)" title="' + escapeHtml(t('delete')) + '">\u{1F5D1}</button>'
      + '</span>';
  }

  // Reply-to quote (if this message is a reply)
  let replyQuoteHtml = '';
  if (extra && extra.source && extra.source.reply_to) {
    const rt = extra.source.reply_to;
    const rtAgent = rt.agent || rt.role || '';
    const rtPreview = (rt.text_preview || '').substring(0, 100);
    if (rtPreview) {
      const rtIdx = rt.raw_index !== undefined ? rt.raw_index : -1;
      replyQuoteHtml = '<div class="reply-quote" ' + (rtIdx >= 0 ? 'onclick="scrollToMessage(' + rtIdx + ')"' : '') + '>'
        + '\u21A9 ' + escapeHtml(rtAgent) + ': "' + escapeHtml(rtPreview) + '"</div>';
    }
  }

  // agent_delegate source = private channel between two agents.
  // Render as a compact delegate block regardless of the underlying
  // message role (delegate request is user-role to ingest into target,
  // delegate reply is assistant-role from the target).
  const _isDelegateMsg = extra && extra.source
      && (extra.source.type === 'agent_delegate')
      && window.PAWFLOW_GROUP_DELEGATE_MESSAGES !== false;
  pawflowDebugLog('[delegate-render]', role, 'isDelegate=', _isDelegateMsg, 'source=', extra && extra.source);
  if (_isDelegateMsg) {
    const _from = extra.source.from || '?';
    const _to = extra.source.to || '?';
    // Bidirectional key: both A→B and B→A land in the same block.
    const _pair = [_from, _to].map(s => s.toLowerCase()).sort();
    const _key = 'delegate-shared::' + _pair[0] + '::' + _pair[1];
    let _existing = document.querySelector('[data-delegate-key="' + CSS.escape(_key) + '"]');
    const _inner = document.createElement('div');
    _inner.className = 'delegate-message msg-inner-' + role;
    if (role === 'tool_call' || role === 'tool') {
      let toolName = (extra && (extra.tool_name || extra.tool)) || text || '?';
      // Prefer `arguments` (full dict) over `tool_args` (500-char JSON
      // string — invalid parse on long commands → `Bash()`). Same fix
      // as the non-delegate branch below.
      const toolArgs = (extra && extra.arguments) || (extra && extra.tool_args) || {};
      let args = toolArgs;
      const normalized = _unwrapDisplayedToolCall(toolName, args);
      toolName = normalized.toolName;
      args = normalized.toolArgs;
      const tcId = (extra && extra.tc_id) || '';
      if (tcId) _inner.dataset.tcId = tcId;
      _inner.dataset.tool = toolName;
      const origin = _toolOriginValue(extra, (extra && (extra.tool_name || extra.tool)) || toolName);
      if (origin) _inner.dataset.toolOrigin = origin;
      // Tag the agent that owns this tool so the `done` handler can
      // scope its pending-bullet cleanup correctly.
      const _ownerAgent = (extra && (extra.agent_name
          || (extra.source && extra.source.from))) || '';
      if (_ownerAgent) _inner.dataset.agent = String(_ownerAgent).toLowerCase();
      if (args && args.path) _inner.dataset.path = args.path;
      if (args && args.command) _inner.dataset.command = args.command.substring(0, 200);
      const isLive = extra && extra.live;
      const originBadge = _toolOriginBadge(extra, (extra && (extra.tool_name || extra.tool)) || toolName);
      const bulletClass = isLive ? 'pending' : 'done';
      const bgBtn = (tcId && isLive) ? ' <button class="tc-bg-btn" onclick="backgroundTool(\'' + tcId + '\')" title="' + escapeHtml(t('runInBackground')) + '">\u2192 BG</button>' : '';
      const klBtn = (tcId && isLive) ? ' <button class="tc-kl-btn" onclick="killTool(\'' + tcId + '\')" title="' + escapeHtml(t('kill')) + '">\u2718</button>' : '';
      if (toolName === 'edit' && args && args.path) {
        _inner.innerHTML = timeHtml + '<span class="tc-bullet ' + bulletClass + '">\u25cf</span> ' + originBadge + _renderToolCallEdit('', args) + bgBtn + klBtn;
      } else if (toolName === 'apply_patch' && args && args.path) {
        _inner.innerHTML = timeHtml + '<span class="tc-bullet ' + bulletClass + '">\u25cf</span> ' + originBadge + _renderToolCallPatch('', args) + bgBtn + klBtn;
      } else {
        _inner.innerHTML = timeHtml + '<span class="tc-bullet ' + bulletClass + '">\u25cf</span> ' + originBadge + escapeHtml(_toolCallSummary(toolName, args || {})) + bgBtn + klBtn;
      }
    } else if (role === 'tool_result') {
      const tcId = (extra && extra.tc_id) || '';
      if (tcId) _inner.dataset.tcId = tcId;
      if (tcId) {
        const tcEl = findToolCallElement(tcId, _existing || document);
        if (tcEl) { _attachToolResult(tcEl, text || ''); el.style.display = 'none'; return el; }
      }
      _inner.innerHTML = timeHtml + '<pre class="tool-result">' + escapeHtml((text || '').substring(0, 2000)) + '</pre>';
    } else {
      // Append buildMetaLine so delegate replies show provider/model/
      // tokens/duration (fields preserved by agent_core._append when
      // re-stamping the source as agent_delegate/kind=reply).
      _inner.innerHTML = timeHtml + renderMarkdown(text) + buildMetaLine(extra);
    }
    if (_existing) {
      const _body = _existing.querySelector('.delegate-body');
      if (_body) _body.appendChild(_inner);
      el.style.display = 'none';
      el._delegateInner = _inner;
      return _inner;
    }
    const _arrow = '\u{1F500} <span class="delegate-src">'
        + escapeHtml(displayAgentName(_from)) + '</span> \u2192 '
        + '<span class="delegate-dst">'
        + escapeHtml(displayAgentName(_to)) + '</span>';
    el.className = 'msg delegate-block delegate-shared';
    el.dataset.delegateKey = _key;
    const _body = document.createElement('div');
    _body.className = 'delegate-body';
    _body.appendChild(_inner);
    const _details = document.createElement('details');
    _details.open = true;
    const _summary = document.createElement('summary');
    _summary.className = 'delegate-header';
    _summary.innerHTML = _arrow + timeHtml;
    _details.appendChild(_summary);
    _details.appendChild(_body);
    el.appendChild(_details);
    el._delegateInner = _inner;
  } else if (role === 'assistant') {
    el.innerHTML = replyQuoteHtml + actionsHtml + timeHtml + badge + renderMarkdown(text) + buildMetaLine(extra);
  } else if (role === 'tool_call' || role === 'tool') {
    let toolName = (extra && (extra.tool_name || extra.tool)) || text || '?';
    // Prefer `arguments` (untouched dict from classify) over `tool_args`
    // (JSON string truncated to 500 chars — invalid on long bash commands,
    // JSON.parse throws, summary shows `Bash()`). Fall back to the string
    // form only when `arguments` is missing.
    const toolArgs = (extra && extra.arguments) || (extra && extra.tool_args) || {};
    let args = toolArgs;
    const normalized = _unwrapDisplayedToolCall(toolName, args);
    toolName = normalized.toolName;
    args = normalized.toolArgs;
    const tcId = (extra && extra.tc_id) || '';
    if (tcId) el.dataset.tcId = tcId;
    el.dataset.tool = toolName;
    const origin = _toolOriginValue(extra, (extra && (extra.tool_name || extra.tool)) || toolName);
    if (origin) el.dataset.toolOrigin = origin;
    if (args && args.path) el.dataset.path = args.path;
    if (args && args.command) el.dataset.command = args.command.substring(0, 200);

    const isLive = extra && extra.live;
    const originBadge = _toolOriginBadge(extra, (extra && (extra.tool_name || extra.tool)) || toolName);
    const bulletClass = isLive ? 'pending' : 'done';
    const bgBtn = (tcId && isLive) ? ' <button class="tc-bg-btn" onclick="backgroundTool(\'' + tcId + '\')" title="' + escapeHtml(t('runInBackground')) + '">\u2192 BG</button>' : '';
    const klBtn = (tcId && isLive) ? ' <button class="tc-kl-btn" onclick="killTool(\'' + tcId + '\')" title="' + escapeHtml(t('kill')) + '">\u2718</button>' : '';
    if (toolName === 'edit' && args && args.path) {
      el.innerHTML = timeHtml + '<span class="tc-bullet ' + bulletClass + '">\u25cf</span> ' + originBadge + _renderToolCallEdit('', args) + bgBtn + klBtn;
    } else if (toolName === 'apply_patch' && args && args.path) {
      el.innerHTML = timeHtml + '<span class="tc-bullet ' + bulletClass + '">\u25cf</span> ' + originBadge + _renderToolCallPatch('', args) + bgBtn + klBtn;
    } else {
      el.innerHTML = timeHtml + '<span class="tc-bullet ' + bulletClass + '">\u25cf</span> ' + originBadge + escapeHtml(_toolCallSummary(toolName, args || {})) + bgBtn + klBtn;
    }
  } else if (role === 'tool_result') {
    const tcId = (extra && extra.tc_id) || '';
    if (tcId) el.dataset.tcId = tcId;
    let resultText = text || '';
    if (typeof resultText !== 'string') {
      try { resultText = JSON.stringify(resultText, null, 2); }
      catch (_err) { resultText = String(resultText || ''); }
    }
    // Try to attach to the matching tool_call element
    if (tcId) {
      const tcEl = findToolCallElement(tcId);
      if (tcEl) {
        _attachToolResult(tcEl, resultText);
        el.style.display = 'none';  // hide this standalone element
        return el;
      }
    }
    // Fallback: standalone tool_result (no matching tool_call found)
    const toolName = (extra && extra.tool_name) || (extra && extra.tool) || '';
    const display = _TOOL_DISPLAY[toolName] || toolName;
    const firstLine = resultText.split('\n')[0].substring(0, 120);
    const rendered = _renderToolOutput(resultText);
    const inlineMedia = _extractInlineMedia(resultText);
    // Reload: always collapsed, but pull out inline media so it stays visible
    el.innerHTML = timeHtml + '<span class="tc-bullet done">\u25cf</span> ' + escapeHtml(display)
      + '<div class="tc-result">' + inlineMedia
      + '<details><summary>\u23bf ' + escapeHtml(firstLine) + '</summary>'
      + rendered + '</details></div>';
  } else if (role === 'thinking') {
    // Collapsible thinking block (same as SSE thinking_content)
    el.className = 'msg thinking-block';
    el.style.cssText = 'margin:4px 0;border-left:3px solid #6b7280;padding:4px 8px;opacity:0.7;';
    const details = document.createElement('details');
    const summary = document.createElement('summary');
    summary.style.cssText = 'cursor:pointer;font-size:12px;color:#6b7280;user-select:none;';
    summary.textContent = t('thought');
    details.appendChild(summary);
    const content = document.createElement('div');
    content.style.cssText = 'font-size:12px;color:#9ca3af;font-style:italic;white-space:pre-wrap;max-height:300px;overflow-y:auto;';
    content.textContent = text;
    details.appendChild(content);
    el.innerHTML = '';
    el.appendChild(details);
  } else if (role === 'user') {
    el.innerHTML = replyQuoteHtml + actionsHtml + timeHtml + badge + escapeHtml(text) + _attachHtml;
  } else if (role === 'sub_agent_trace') {
    if (window.PAWFLOW_GROUP_DELEGATE_MESSAGES === false) {
      el.innerHTML = replyQuoteHtml + actionsHtml + timeHtml + badge + renderMarkdown(text) + buildMetaLine(extra);
      return el;
    }
    const dtcId = (extra && extra.source && extra.source.delegate_tc_id) || '';
    const taskId = (extra && extra.source && extra.source.task_id) || '';
    // Dedupe: if the live SSE already rendered a sub-block for this
    // sub-agent task, skip — we'd otherwise render a stale duplicate
    // (e.g. the "running" snapshot side-by-side with the "done" one).
    if (taskId && document.querySelector('[data-delegate-task-id="' + taskId + '"]')) {
      return null;
    }
    const existingGroup = dtcId ? document.querySelector('[data-delegate-group="' + dtcId + '"]') : null;
    if (existingGroup) {
      const groupBody = existingGroup.querySelector('.delegate-body');
      // On second trace, convert the inline content of the first trace into a sub-block
      if (groupBody && !existingGroup.querySelector('.delegate-sub-block')) {
        const firstSubEl = document.createElement('details');
        firstSubEl.className = 'delegate-sub-block';
        firstSubEl.setAttribute('open', '');
        const firstAgent = existingGroup.dataset.firstAgent || 'sub-agent';
        const firstSvc = existingGroup.dataset.firstSvc || '';
        const svcL = firstSvc ? ' via ' + escapeHtml(firstSvc) : '';
        firstSubEl.innerHTML = '<summary class="delegate-sub-header">\u25b8 '
          + '<span class="delegate-dst">' + escapeHtml(displayAgentName(firstAgent)) + '</span>'
          + svcL + '</summary>'
          + '<div class="delegate-sub-body">' + groupBody.innerHTML + '</div>';
        groupBody.innerHTML = '';
        groupBody.appendChild(firstSubEl);
        // Update group header to show "Delegate (N agents)"
        const parentAgent = (extra && extra.source && extra.source.parent_agent) || existingGroup.dataset.parentAgent || '';
        const summaryEl = existingGroup.querySelector('.delegate-header');
        if (summaryEl) {
          summaryEl.innerHTML = '\u{1F500} ' + escapeHtml(displayAgentName(parentAgent))
            + ' \u2192 Delegate (<span class="delegate-group-count">2 agents</span>)';
        }
      }
      // Add new sub-block
      const subHtml = renderDelegateSubBlock(text, extra);
      const subEl = document.createElement('details');
      subEl.className = 'delegate-sub-block';
      subEl.innerHTML = subHtml;
      if (groupBody) groupBody.appendChild(subEl);
      const countSpan = existingGroup.querySelector('.delegate-group-count');
      if (countSpan) {
        const n = existingGroup.querySelectorAll('.delegate-sub-block').length;
        countSpan.textContent = n + ' agents';
      }
      return existingGroup;
    }
    // First trace for this delegate_tc_id — create group
    el.className = 'msg delegate-block delegate-group';
    if (dtcId) el.dataset.delegateGroup = dtcId;
    if (taskId) el.dataset.delegateTaskId = taskId;
    const src = (extra && extra.source) || {};
    el.dataset.firstAgent = src.name || 'sub-agent';
    el.dataset.firstSvc = src.llm_service || '';
    el.dataset.parentAgent = src.parent_agent || '';
    el.innerHTML = renderDelegateBlock(text, extra);
  } else if (role === 'error') {
    el.innerHTML = timeHtml + badge + renderMarkdown(text);
  } else if (role === 'agent-result') {
    const agentName = (extra && typeof extra === 'string') ? extra : '';
    el.innerHTML = timeHtml + (agentName ? '<strong>' + escapeHtml(agentName) + ':</strong> ' : '') + renderMarkdown(text);
  } else if (extra && extra.html) {
    el.innerHTML = timeHtml + text;
  } else {
    el.innerHTML = timeHtml + escapeHtml(text);
  }
  // Check near-bottom BEFORE appending so new element doesn't shift the threshold
  const shouldScroll = isNearBottom();
  const container = document.getElementById('messages');
  _insertMessageChronologically(container, el, _ts);
  trimLiveDisplayWindowIfAutoscrolling(shouldScroll);
  scrollBottom(shouldScroll);
  // Syntax highlighting via highlight.js (if loaded)
  if (typeof hljs !== 'undefined') {
    el.querySelectorAll('pre code').forEach(function(block) { hljs.highlightElement(block); });
  }
  // Re-scroll when images finish loading (they change height after initial render)
  if (shouldScroll) {
    for (const img of el.querySelectorAll('img')) {
      img.addEventListener('load', () => scrollBottom(true), { once: true });
    }
  }
  if (window.PAWFLOW_GROUP_TECHNICAL_MESSAGES) applyTechnicalMessageGrouping();
  if (window._pawflowExtRuntime) {
    window._pawflowExtRuntime.fireHook('message_appended', {
      role: role,
      conversationId: (typeof conversationId !== 'undefined' ? conversationId : null),
      msgId: (extra && extra.msg_id) || '',
      ts: (extra && extra.ts) || 0,
      source: (extra && extra.source) || null,
    });
  }
  return el;
}

function escapeHtml(t) {
  return String(t == null ? '' : t)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function escapeAttr(t) {
  return escapeHtml(t);
}

function jsStringArg(t) {
  return escapeAttr(JSON.stringify(String(t == null ? '' : t)));
}


var _diffLangMap = {js:'javascript',ts:'typescript',py:'python',rb:'ruby',rs:'rust',go:'go',java:'java',cpp:'cpp',c:'c',cs:'csharp',php:'php',sh:'bash',json:'json',html:'xml',xml:'xml',css:'css',sql:'sql',yaml:'yaml',yml:'yaml',jsx:'javascript',tsx:'typescript',vue:'xml',svelte:'xml'};

function _synLine(code, lang) {
  // Syntax-highlight a single line of code (returns HTML)
  if (!lang || typeof hljs === 'undefined') return escapeHtml(code);
  try { return hljs.highlight(code, {language: lang, ignoreIllegals: true}).value; }
  catch(e) { return escapeHtml(code); }
}

function _renderToolOutput(text, toolHint, pathHint) {
  // Smart rendering with highlight.js for syntax highlighting
  const lines = text.split('\n');
  const _ext = pathHint ? (pathHint.split('.').pop() || '').toLowerCase() : '';
  const _hljs = typeof hljs !== 'undefined';

  // Extension → highlight.js language mapping
  const _langMap = {
    py:'python', js:'javascript', ts:'typescript', jsx:'javascript', tsx:'typescript',
    java:'java', go:'go', rs:'rust', rb:'ruby', c:'c', cpp:'cpp', cs:'csharp',
    php:'php', sh:'bash', bash:'bash', yaml:'yaml', yml:'yaml', toml:'ini',
    sql:'sql', html:'html', css:'css', xml:'xml', json:'json', md:'markdown',
    dockerfile:'dockerfile', makefile:'makefile',
  };

  // Detect diff
  const diffLines = lines.filter(l => /^\s*\d*\s*[+-] /.test(l) || l.startsWith('+ ') || l.startsWith('- '));
  const isDiff = diffLines.length >= 2 && (
    text.includes('replacement') || text.includes('Edited ') || text.includes('Written ')
    || text.includes('@@') || text.includes('diff ') || lines.some(l => l.startsWith('---') || l.startsWith('+++')));
  if (isDiff) {
    return '<pre class="tc-output"><code class="language-diff hljs">' + lines.map(l => {
      const s = l.trimStart();
      if (s.startsWith('+ ') || /^\s*\d+\s+\+ /.test(l)) return '<span class="hljs-addition">' + escapeHtml(l) + '</span>';
      if (s.startsWith('- ') || /^\s*\d+\s+- /.test(l)) return '<span class="hljs-deletion">' + escapeHtml(l) + '</span>';
      if (s.startsWith('@@')) return '<span class="hljs-meta">' + escapeHtml(l) + '</span>';
      return escapeHtml(l);
    }).join('\n') + '</code></pre>';
  }

  // Detect markdown
  if (/^```|^\#{1,3} |^\*\*|^\- \[/.test(text) || text.includes('\n```')) {
    return '<div class="tc-md">' + renderMarkdown(text) + '</div>';
  }

  // Detect file read with line numbers → extract code, detect language, highlight
  const hasLineNumbers = lines.length > 3 && lines.filter(l => /^\s*\d+\t/.test(l)).length > lines.length * 0.5;
  if (hasLineNumbers && _hljs) {
    const lang = _langMap[_ext] || '';
    // Separate line numbers from code
    const codeLines = lines.map(l => {
      const m = l.match(/^(\s*\d+)\t(.*)$/);
      return m ? m[2] : l;
    });
    const nums = lines.map(l => {
      const m = l.match(/^(\s*\d+)\t/);
      return m ? m[1] : '';
    });
    let highlighted;
    try {
      highlighted = lang
        ? hljs.highlight(codeLines.join('\n'), {language: lang, ignoreIllegals: true}).value
        : hljs.highlightAuto(codeLines.join('\n')).value;
    } catch(e) { highlighted = escapeHtml(codeLines.join('\n')); }
    // Re-inject line numbers
    const hLines = highlighted.split('\n');
    const final = hLines.map((hl, i) => '<span class="ln">' + (nums[i] || '') + '</span>\t' + hl).join('\n');
    return '<pre class="tc-output"><code class="hljs">' + final + '</code></pre>';
  }

  // JSON
  const trimmed = text.trim();
  if ((trimmed.startsWith('{') && trimmed.endsWith('}')) || (trimmed.startsWith('[') && trimmed.endsWith(']'))) {
    if (_hljs) {
      try {
        const h = hljs.highlight(text, {language: 'json', ignoreIllegals: true}).value;
        return '<pre class="tc-output"><code class="hljs language-json">' + h + '</code></pre>';
      } catch(e) {}
    }
    return '<pre class="tc-output">' + escapeHtml(text) + '</pre>';
  }

  // Code file by extension
  if (_ext && _langMap[_ext] && _hljs) {
    try {
      const h = hljs.highlight(text, {language: _langMap[_ext], ignoreIllegals: true}).value;
      return '<pre class="tc-output"><code class="hljs language-' + _langMap[_ext] + '">' + h + '</code></pre>';
    } catch(e) {}
  }

  // Grep results
  if (toolHint === 'grep' || toolHint === 'glob') {
    return '<pre class="tc-output">' + lines.map(l => {
      const m = l.match(/^([^:]+:\d+:)\s*(.*)$/);
      if (m) return '<span class="grep-loc">' + escapeHtml(m[1]) + '</span> ' + escapeHtml(m[2]);
      return escapeHtml(l);
    }).join('\n') + '</pre>';
  }

  // Git output (commit, log, show) — auto-detect
  if (_hljs && (toolHint === 'bash' || !toolHint)) {
    const isGit = lines.some(l => /^commit [0-9a-f]{40}/.test(l) || /^Author:/.test(l));
    if (isGit) {
      return '<pre class="tc-output">' + lines.map(l => {
        if (/^commit [0-9a-f]/.test(l)) return '<span class="hljs-string">' + escapeHtml(l) + '</span>';
        if (/^Author:/.test(l)) return '<span class="hljs-attr">' + escapeHtml(l) + '</span>';
        if (/^Date:/.test(l)) return '<span class="hljs-comment">' + escapeHtml(l) + '</span>';
        if (l.startsWith('    ')) return '<span class="hljs-title">' + escapeHtml(l) + '</span>';
        return escapeHtml(l);
      }).join('\n') + '</pre>';
    }
    // Shell output: try auto-detect
    try {
      const h = hljs.highlightAuto(text, ['bash', 'shell', 'plaintext']).value;
      if (h !== escapeHtml(text)) {
        return '<pre class="tc-output"><code class="hljs">' + h + '</code></pre>';
      }
    } catch(e) {}
  }

  // Default — escape but auto-inline any fs://filestore or /files/<id> media URLs
  // so generate_image / screen / see / edit_image results show the image
  // directly in the tool-result bubble instead of printing the raw URL.
  return '<pre class="tc-output">' + renderTextWithInlineMedia(text) + '</pre>';
}

function _attachToolResult(tcEl, resultText) {
  // Guard: don't attach if result already present
  if (tcEl.querySelector('.tc-result')) return;
  if (typeof resultText !== 'string') {
    try { resultText = JSON.stringify(resultText, null, 2); }
    catch (_err) { resultText = String(resultText || ''); }
  }
  const bullet = tcEl.querySelector('.tc-bullet');
  if (bullet) { bullet.classList.remove('pending'); bullet.classList.add('done'); }
  // Remove BG/KL buttons (tool is done)
  const bgBtn = tcEl.querySelector('.tc-bg-btn');
  if (bgBtn) bgBtn.remove();
  const klBtn = tcEl.querySelector('.tc-kl-btn');
  if (klBtn) klBtn.remove();
  const toolHint = tcEl.dataset.tool || '';
  const pathHint = tcEl.dataset.path || '';
  const resultDiv = document.createElement('div');
  resultDiv.className = 'tc-result';
  const firstLine = resultText.split('\n')[0].substring(0, 120);
  const rendered = _renderToolOutput(resultText, toolHint, pathHint);
  // see sends the image to the LLM, not the user — don't render it twice
  const inlineMedia = toolHint === 'see' ? '' : _extractInlineMedia(resultText);
  // Open while streaming, auto-collapse after 1.5s
  resultDiv.innerHTML = inlineMedia
    + '<details open><summary>\u23bf ' + escapeHtml(firstLine)
    + '</summary>' + rendered + '</details>';
  tcEl.appendChild(resultDiv);
  // Auto-collapse after brief display
  const _det = resultDiv.querySelector('details');
  if (_det) setTimeout(() => { _det.removeAttribute('open'); }, 1500);
  // Auto-scroll
  if (isNearBottom()) scrollBottom();
}

function backgroundTool(tcId) {
  if (!conversationId || !tcId) return;
  action$('background_tool', { tc_id: tcId }).subscribe(d => {
    if (d.ok) {
      const tcEl = findToolCallElement(tcId);
      if (tcEl) {
        const btn = tcEl.querySelector('.tc-bg-btn');
        if (btn) btn.remove();
        const bullet = tcEl.querySelector('.tc-bullet');
        if (bullet) { bullet.classList.add('bg'); bullet.title = t('runningInBackground'); }
        // Add Kill button
        const klBtn = document.createElement('button');
        klBtn.className = 'tc-kl-btn';
        klBtn.onclick = () => killTool(tcId);
        klBtn.title = t('killBackgroundTask');
        klBtn.textContent = '\u2717 KL';
        const preEl = tcEl.querySelector('pre');
        if (preEl) tcEl.insertBefore(klBtn, preEl);
        else tcEl.appendChild(klBtn);
      }
    }
  });
}

function killTool(tcId) {
  if (!conversationId || !tcId) return;
  // Optimistic UI: mark as killed
  const tcEl = findToolCallElement(tcId);
  if (tcEl) {
    tcEl.querySelectorAll('.tc-kl-btn, .tc-bg-btn').forEach(b => b.remove());
    const bullet = tcEl.querySelector('.tc-bullet');
    if (bullet) { bullet.classList.remove('bg', 'pending'); bullet.classList.add('done'); bullet.style.color = '#e94560'; bullet.title = t('killed'); }
  }
  // Kill via tool relay (in-flight tools) AND background system
  fireAction('kill_tool', { tc_id: tcId });
  cancelBgTool(tcId);
}

function cancelBgTool(tcId) {
  if (!conversationId || !tcId) return;
  fireAction('cancel_bg_tool', { tc_id: tcId });
}

function _renderToolCallEdit(srcLabel, args) {
  const fpath = args.path || '?';
  const oldStr = args.old_string || '';
  const newStr = args.new_string || '';
  const startLn = args.start_line || '';
  const endLn = args.end_line || '';
  let editHtml = '<span style="color:#4ecdc4;font-size:11px">\u270E [' + escapeHtml(srcLabel) + '] Edit(' + escapeHtml(fpath) + ')</span>';
  if (startLn && endLn) {
    editHtml += '<span style="color:#8b949e;font-size:11px"> lines ' + startLn + '-' + endLn + '</span>';
  }
  const _ext = fpath.split('.').pop().toLowerCase();
  const _langMap = {js:'javascript',ts:'typescript',py:'python',rb:'ruby',rs:'rust',go:'go',java:'java',cpp:'cpp',c:'c',cs:'csharp',php:'php',sh:'bash',bash:'bash',json:'json',html:'xml',xml:'xml',css:'css',sql:'sql',yaml:'yaml',yml:'yaml',md:'markdown',jsx:'javascript',tsx:'typescript',vue:'xml',svelte:'xml'};
  const _lang = _langMap[_ext] || '';
  const oldLines = oldStr ? oldStr.split('\n') : [];
  const newLines = newStr ? newStr.split('\n') : [];
  let cpx = 0;
  while (cpx < oldLines.length && cpx < newLines.length && oldLines[cpx] === newLines[cpx]) cpx++;
  let csx = 0;
  while (csx < (oldLines.length - cpx) && csx < (newLines.length - cpx) && oldLines[oldLines.length - 1 - csx] === newLines[newLines.length - 1 - csx]) csx++;
  const diffLines = [];
  const ctxPrefix = oldLines.slice(Math.max(0, cpx - 2), cpx);
  ctxPrefix.forEach(l => { diffLines.push('<div><span style="color:#8b949e;user-select:none">  </span>' + _synLine(l, _lang) + '</div>'); });
  const removed = oldLines.slice(cpx, oldLines.length - csx);
  removed.slice(0, 6).forEach(l => { diffLines.push('<div style="background:rgba(248,81,73,0.15)"><span style="color:#f85149;user-select:none">- </span>' + _synLine(l, _lang) + '</div>'); });
  if (removed.length > 6) diffLines.push('<div style="color:#8b949e">  ... +' + (removed.length - 6) + ' lines removed</div>');
  const added = newLines.slice(cpx, newLines.length - csx);
  added.slice(0, 6).forEach(l => { diffLines.push('<div style="background:rgba(63,185,80,0.15)"><span style="color:#3fb950;user-select:none">+ </span>' + _synLine(l, _lang) + '</div>'); });
  if (added.length > 6) diffLines.push('<div style="color:#8b949e">  ... +' + (added.length - 6) + ' lines added</div>');
  const ctxSuffix = oldLines.slice(oldLines.length - csx, oldLines.length - csx + 2);
  ctxSuffix.forEach(l => { diffLines.push('<div><span style="color:#8b949e;user-select:none">  </span>' + _synLine(l, _lang) + '</div>'); });
  const _addedCount = added.length, _removedCount = removed.length;
  if (_addedCount || _removedCount) {
    const parts = [];
    if (_addedCount) parts.push(_addedCount + ' added');
    if (_removedCount) parts.push(_removedCount + ' removed');
    editHtml += '<span style="color:#8b949e;font-size:10px;margin-left:8px">(' + parts.join(', ') + ')</span>';
  }
  if (diffLines.length > 0) {
    editHtml += '<pre class="diff-output' + (_lang ? ' language-' + _lang : '') + '" style="margin:2px 0 0 0;font-size:11px">' + diffLines.join('') + '</pre>';
  }
  return editHtml;
}

function _renderToolCallPatch(srcLabel, args) {
  const fpath = args.path || '?';
  const patch = args.patch || '';
  let patchHtml = '<span style="color:#4ecdc4;font-size:11px">\u270E [' + escapeHtml(srcLabel) + '] ApplyPatch(' + escapeHtml(fpath) + ')</span>';
  if (!patch) return patchHtml;
  const lines = patch.split('\n');
  let added = 0, removed = 0;
  lines.forEach(function(line) {
    if (line.startsWith('+') && !line.startsWith('+++')) added++;
    else if (line.startsWith('-') && !line.startsWith('---')) removed++;
  });
  const parts = [];
  if (added) parts.push(added + ' added');
  if (removed) parts.push(removed + ' removed');
  if (parts.length) patchHtml += '<span style="color:#8b949e;font-size:10px;margin-left:8px">(' + parts.join(', ') + ')</span>';
  patchHtml += '<pre class="diff-output" style="margin:2px 0 0 0;font-size:11px"><code class="language-diff hljs">' + lines.map(function(line) {
    if (line.startsWith('+') && !line.startsWith('+++')) return '<span class="hljs-addition">' + escapeHtml(line) + '</span>';
    if (line.startsWith('-') && !line.startsWith('---')) return '<span class="hljs-deletion">' + escapeHtml(line) + '</span>';
    if (line.startsWith('@@') || line.startsWith('***')) return '<span class="hljs-meta">' + escapeHtml(line) + '</span>';
    return escapeHtml(line);
  }).join('\n') + '</code></pre>';
  return patchHtml;
}

function _renderDiff(text, filePath) {
  var lines = text.split('\n');
  var hasDiffLines = lines.some(function(l) {
    var s = l.trimStart();
    return s.startsWith('+ ') || s.startsWith('- ') || s.startsWith('@@');
  });
  var hasDiffContext = /replacement|edited |written |hunks/i.test(text);
  if (!hasDiffLines || !hasDiffContext) return null;

  // Detect language from file path for syntax coloring within diff lines
  var ext = (filePath || '').split('.').pop().toLowerCase();
  var lang = _diffLangMap[ext] || '';

  return '<pre class="diff-output">' + lines.map(function(line) {
    var s = line.trimStart();
    // Extract line number prefix and +/- marker, highlight only the code part
    var m = s.match(/^(\d+\s+)?([+-] )(.*)/);
    if (m) {
      var prefix = (m[1] || '') + m[2];
      var code = m[3];
      var bg = m[2].startsWith('+') ? 'rgba(63,185,80,0.1)' : 'rgba(248,81,73,0.1)';
      var markerColor = m[2].startsWith('+') ? '#3fb950' : '#f85149';
      return '<div style="background:' + bg + '"><span style="color:' + markerColor + ';user-select:none">' + escapeHtml(prefix) + '</span>' + _synLine(code, lang) + '</div>';
    }
    if (s.startsWith('+ ')) {
      return '<div style="background:rgba(63,185,80,0.1)"><span style="color:#3fb950;user-select:none">+ </span>' + _synLine(s.slice(2), lang) + '</div>';
    }
    if (s.startsWith('- ')) {
      return '<div style="background:rgba(248,81,73,0.1)"><span style="color:#f85149;user-select:none">- </span>' + _synLine(s.slice(2), lang) + '</div>';
    }
    if (s.startsWith('@@')) {
      return '<div><span style="color:#58a6ff">' + escapeHtml(line) + '</span></div>';
    }
    if (/^(Edited |Written |replacement)/i.test(s)) {
      return '<div><strong>' + escapeHtml(line) + '</strong></div>';
    }
    return '<div><span style="color:#8b949e">' + _synLine(line, lang) + '</span></div>';
  }).join('') + '</pre>';
}

function isImageFile(name) {
  return /\.(png|jpe?g|gif|svg|webp|bmp)$/i.test(name || '');
}

function normalizePawFlowFileUrl(url) {
  const raw = String(url || '');
  const m = raw.match(/^https?:\/\/[^/]+(\/files\/[a-f0-9]+\/[^\s<"'`]+)$/i);
  return m ? m[1] : raw;
}


function _extractInlineMedia(text) {
  if (!text) return '';
  const urlRe = /(fs:\/\/[^\s<"'`]+|https?:\/\/[^\s<"'`]*\/files\/[a-f0-9]+\/[^\s<"'`]+|\/files\/[a-f0-9]+\/[^\s<"'`]+)/g;
  const seen = new Set();
  let out = '';
  let m;
  while ((m = urlRe.exec(text)) !== null) {
    const url = m[0];
    if (seen.has(url)) continue;
    seen.add(url);
    let fname = '';
    let httpUrl = url;
    const fsMatch = url.match(/^fs:\/\/([^/]+)\/(.+)$/);
    if (fsMatch) {
      const service = fsMatch[1];
      const fpath = fsMatch[2];
      fname = fpath.split('/').pop() || fpath;
      if (service === 'filestore') {
        const fidMatch = fpath.match(/^([a-f0-9]+)(?:\/|$)/);
        // /files/<id>/<name> — the trailing "/<name>" matters:
        // _flushPendingImages matches /\/files\/([a-f0-9]+)\// and
        // won't recognize a /files/<id> without the slash, leaving the
        // <img> permanently hidden (display:none).
        httpUrl = fidMatch ? '/files/' + fidMatch[1] + '/' + encodeURIComponent(fname) : '';
      } else {
        httpUrl = '/fs/' + encodeURIComponent(service) + '/'
          + fpath.split('/').map(encodeURIComponent).join('/');
      }
    } else {
      const fm = url.match(/\/files\/[a-f0-9]+\/([^?#]+)/);
      fname = fm ? fm[1] : '';
      httpUrl = normalizePawFlowFileUrl(url);
    }
    if (!httpUrl || !fname) continue;
    if (isImageFile(fname)) out += inlineImageHtml(httpUrl, fname, '');
    else if (isAudioFile(fname)) out += inlineAudioHtml(httpUrl, fname);
    else if (isVideoFile(fname)) out += inlineVideoHtml(httpUrl, fname);
  }
  return out;
}

/** Escape text for HTML but render fs://filestore/<id>/<name>.ext,
 * fs://<relay>/<path>/<name>.ext, and /files/<id>/<name>.ext media URLs
 * as inline <img>/<audio>/<video>. Used in tool-result default renderer
 * so generate_image / screen / see outputs show the image directly
 * instead of printing the raw URL. */
function renderTextWithInlineMedia(text) {
  if (!text) return '';
  // Single regex that matches either a full fs:// URL or an HTTP /files/<id>/<name> URL.
  const urlRe = /(fs:\/\/[^\s<"'`]+|https?:\/\/[^\s<"'`]*\/files\/[a-f0-9]+\/[^\s<"'`]+|\/files\/[a-f0-9]+\/[^\s<"'`]+)/g;
  let out = '';
  let last = 0;
  let m;
  while ((m = urlRe.exec(text)) !== null) {
    // Escaped text before the match
    if (m.index > last) out += escapeHtml(text.slice(last, m.index));
    const url = m[0];
    let fname = '';
    let httpUrl = url;
    const fsMatch = url.match(/^fs:\/\/([^/]+)\/(.+)$/);
    if (fsMatch) {
      const service = fsMatch[1];
      const fpath = fsMatch[2];
      fname = fpath.split('/').pop() || fpath;
      if (service === 'filestore') {
        const fidMatch = fpath.match(/^([a-f0-9]+)(?:\/|$)/);
        httpUrl = fidMatch ? '/files/' + fidMatch[1] + '/' + encodeURIComponent(fname) : '';
      } else {
        httpUrl = '/fs/' + encodeURIComponent(service) + '/'
          + fpath.split('/').map(encodeURIComponent).join('/');
      }
    } else {
      // /files/... URL (absolute or relative)
      const fm = url.match(/\/files\/[a-f0-9]+\/([^?#]+)/);
      fname = fm ? fm[1] : '';
      httpUrl = normalizePawFlowFileUrl(url);
    }
    if (httpUrl && fname && isImageFile(fname)) {
      out += inlineImageHtml(httpUrl, fname, '');
    } else if (httpUrl && fname && isAudioFile(fname)) {
      out += inlineAudioHtml(httpUrl, fname);
    } else if (httpUrl && fname && isVideoFile(fname)) {
      out += inlineVideoHtml(httpUrl, fname);
    } else {
      out += escapeHtml(url);
    }
    last = m.index + url.length;
  }
  if (last < text.length) out += escapeHtml(text.slice(last));
  return out;
}

function isAudioFile(name) {
  return /\.(mp3|wav|ogg|m4a|flac|opus)$/i.test(name || '');
}

function isVideoFile(name) {
  return /\.(mp4|webm|mov|m4v)$/i.test(name || '');
}

var _inlineAudioEl = null;
var _inlineAudioUrl = '';
var _inlineAudioTimer = null;

function _inlineAudioFormat(seconds) {
  const n = Number(seconds);
  if (!Number.isFinite(n) || n < 0) return '--:--';
  const total = Math.floor(n);
  const m = Math.floor(total / 60);
  const s = String(total % 60).padStart(2, '0');
  return m + ':' + s;
}

function _inlineAudioWrappers(url) {
  return Array.from(document.querySelectorAll('.inline-audio'))
    .filter(el => !url || el.dataset.audioUrl === url);
}

function _inlineAudioSync(url) {
  const audio = _inlineAudioEl;
  const isCurrent = !!audio && _inlineAudioUrl === url;
  const duration = isCurrent && Number.isFinite(audio.duration) ? audio.duration : 0;
  const current = isCurrent ? audio.currentTime || 0 : 0;
  const playing = isCurrent && !audio.paused && !audio.ended;
  for (const wrapper of _inlineAudioWrappers(url)) {
    const btn = wrapper.querySelector('.inline-audio-play');
    const range = wrapper.querySelector('.inline-audio-progress');
    const time = wrapper.querySelector('.inline-audio-time');
    if (btn) {
      btn.innerHTML = playing ? '&#10074;&#10074;' : '&#9658;';
      btn.title = playing ? 'Pause' : 'Play';
    }
    if (range) {
      range.value = duration > 0 ? String(Math.min(1000, Math.round((current / duration) * 1000))) : '0';
      range.disabled = duration <= 0;
    }
    if (time) time.textContent = _inlineAudioFormat(current) + ' / ' + _inlineAudioFormat(duration);
  }
}

function _inlineAudioStartTimer() {
  if (_inlineAudioTimer) return;
  _inlineAudioTimer = setInterval(function() {
    if (!_inlineAudioEl || _inlineAudioEl.paused || _inlineAudioEl.ended) {
      clearInterval(_inlineAudioTimer);
      _inlineAudioTimer = null;
    }
    if (_inlineAudioUrl) _inlineAudioSync(_inlineAudioUrl);
  }, 250);
}

function _inlineAudioFor(url) {
  if (_inlineAudioEl && _inlineAudioUrl === url) return _inlineAudioEl;
  if (_inlineAudioEl) {
    try { _inlineAudioEl.pause(); } catch(e) {}
    if (_inlineAudioUrl) _inlineAudioSync(_inlineAudioUrl);
  }
  const audio = new Audio(url);
  audio.preload = 'metadata';
  _inlineAudioEl = audio;
  _inlineAudioUrl = url;
  ['loadedmetadata', 'durationchange', 'timeupdate', 'play', 'pause', 'ended', 'error'].forEach(function(ev) {
    audio.addEventListener(ev, function() { _inlineAudioSync(url); });
  });
  return audio;
}

function pawflowInlineAudioToggle(btn) {
  const wrapper = btn && btn.closest ? btn.closest('.inline-audio') : null;
  const url = wrapper && wrapper.dataset ? wrapper.dataset.audioUrl : '';
  if (!url) return;
  const audio = _inlineAudioFor(url);
  if (!audio.paused && !audio.ended) {
    audio.pause();
    _inlineAudioSync(url);
    return;
  }
  audio.play().then(function() {
    _inlineAudioStartTimer();
    _inlineAudioSync(url);
  }).catch(function(err) {
    console.warn('[inline-audio] play failed', err);
    _inlineAudioSync(url);
  });
}

function pawflowInlineAudioSeek(input) {
  const wrapper = input && input.closest ? input.closest('.inline-audio') : null;
  const url = wrapper && wrapper.dataset ? wrapper.dataset.audioUrl : '';
  if (!url) return;
  const audio = _inlineAudioFor(url);
  if (!Number.isFinite(audio.duration) || audio.duration <= 0) return;
  audio.currentTime = (Number(input.value) / 1000) * audio.duration;
  _inlineAudioSync(url);
}

function inlineAudioHtml(url, filename) {
  const safeUrl = escapeHtml(url || '');
  return '<div class="audio-wrapper inline-audio" data-audio-url="' + safeUrl + '" style="margin:6px 0;max-width:512px;">'
    + '<div style="display:flex;align-items:center;gap:8px;background:rgba(255,255,255,0.7);border-radius:999px;padding:8px 10px;">'
    + '<button class="inline-audio-play" type="button" onclick="pawflowInlineAudioToggle(this)" title="Play" style="width:28px;height:28px;border:0;border-radius:50%;background:#eef4ff;color:#1f2937;cursor:pointer;">&#9658;</button>'
    + '<span class="inline-audio-time" style="font-size:12px;color:#374151;min-width:72px;text-align:center;">0:00 / --:--</span>'
    + '<input class="inline-audio-progress" type="range" min="0" max="1000" value="0" disabled oninput="pawflowInlineAudioSeek(this)" style="flex:1;min-width:90px;">'
    + '</div>'
    + '<div style="font-size:11px;color:#6c6c8a;margin-top:2px;">'
    + '\uD83D\uDD0A ' + escapeHtml(filename || 'audio')
    + ' <a class="flink" href="#" onclick="event.preventDefault();openFileViewer(this.closest(\'.inline-audio\').dataset.audioUrl)" style="color:#6c5ce7;">open</a>'
    + '</div></div>';
}

function inlineVideoHtml(url, filename) {
  return '<div class="video-wrapper" style="margin:6px 0;">'
    + '<video controls preload="metadata" src="' + url + '" '
    + 'style="max-width:512px;max-height:512px;border-radius:8px;border:1px solid #0f3460;"></video>'
    + '<div style="font-size:11px;color:#6c6c8a;margin-top:2px;">'
    + '\uD83C\uDFAC ' + escapeHtml(filename || 'video')
    + ' <a class="flink" href="#" onclick="event.preventDefault();openFileViewer(\'' + url + '\')" style="color:#6c5ce7;">open</a>'
    + '</div></div>';
}

// Batch image loading: collect pending images, check availability in one call,
// then fetch only existing ones. Avoids 50+ sequential 404s blocking the page.
let _pendingImages = [];  // [{imgId, url}]
let _imageFlushTimer = null;

function _flushPendingImages() {
  _imageFlushTimer = null;
  const batch = _pendingImages.splice(0);
  if (!batch.length) return;
  const token = getToken();
  const headers = {};
  if (token) headers['Authorization'] = 'Bearer ' + token;
  // Extract file_ids from URLs: /files/{file_id}
  const fileIds = [];
  const byId = {};
  for (const item of batch) {
    const m = item.url.match(/\/files\/([a-f0-9]+)\//);
    if (m) { fileIds.push(m[1]); byId[m[1]] = item; }
    else { byId[item.imgId] = item; fileIds.push(item.imgId); }
  }
  // Batch check: ask server which file_ids exist
  action$('check_files', { file_ids: fileIds }).subscribe({
    next: data => {
      const available = new Set(data.available || []);
      for (const fid of fileIds) {
        const item = byId[fid];
        if (!item) continue;
        const el = document.getElementById(item.imgId);
        if (!el) continue;
        const wrapper = el.closest('.img-wrapper');
        if (!available.has(fid)) {
          // File doesn't exist — hide entirely
          if (wrapper) wrapper.style.display = 'none';
          continue;
        }
        // File exists — fetch the blob
        fetch(item.url, { headers, credentials: 'same-origin' }).then(r => {
          if (!r.ok) throw new Error(r.status);
          return r.blob();
        }).then(blob => {
          el.src = URL.createObjectURL(blob);
          el.style.display = 'block';
        }).catch(() => { if (wrapper) wrapper.style.display = 'none'; });
      }
    },
    error: () => {
      // Fallback: try each individually
      for (const item of batch) {
        const el = document.getElementById(item.imgId);
        if (!el) continue;
        const wrapper = el.closest('.img-wrapper');
        fetch(item.url, { headers, credentials: 'same-origin' }).then(r => {
          if (!r.ok) throw new Error(r.status);
          return r.blob();
        }).then(blob => {
          el.src = URL.createObjectURL(blob);
          el.style.display = 'block';
        }).catch(() => { if (wrapper) wrapper.style.display = 'none'; });
      }
    },
  });
}

function inlineImageHtml(url, filename, sizeInfo) {
  // Render authenticated inline image (max 512px) with click-to-view
  const imgId = 'img_' + Math.random().toString(36).substring(2, 8);
  // Queue for batch loading (flushed after 100ms of no new images)
  _pendingImages.push({ imgId, url });
  if (_imageFlushTimer) clearTimeout(_imageFlushTimer);
  _imageFlushTimer = setTimeout(_flushPendingImages, 100);
  return '<div class="img-wrapper" style="margin:6px 0;">'
    + '<img id="' + imgId + '" style="display:none;max-width:512px;max-height:512px;border-radius:8px;cursor:pointer;border:1px solid #0f3460;" '
    + 'onclick="openFileViewer(\'' + url + '\')" title="' + t('clickFullSize') + '" />'
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
        // Convert fs://filestore/<id>/<name> to /files/<id> for the
        // native <img>/<audio>/<video> tags (which need a real HTTP URL,
        // same-origin so the auth cookie applies).
        let _httpUrl = parsed.url;
        const _fsm = String(parsed.url || '').match(/^fs:\/\/filestore\/([a-f0-9]+)\//);
        if (_fsm) _httpUrl = '/files/' + _fsm[1] + '/' + encodeURIComponent(parsed.filename || 'file');
        if (isImageFile(parsed.filename)) {
          return inlineImageHtml(_httpUrl, parsed.filename, parsed.size_kb + ' KB');
        }
        if (isAudioFile(parsed.filename)) {
          return inlineAudioHtml(_httpUrl, parsed.filename);
        }
        if (isVideoFile(parsed.filename)) {
          return inlineVideoHtml(_httpUrl, parsed.filename);
        }
        setTimeout(() => openFileViewer(parsed.url), 100);
        return '<span style="cursor:pointer;color:#6c5ce7;" onclick="openFileViewer(\'' + parsed.url + '\')">\uD83D\uDCC4 ' + parsed.filename + ' (' + parsed.size_kb + ' KB) \u2014 Click to view</span>';
      }
    }
  } catch(e) {}
  // 1. Extract code blocks BEFORE escaping (preserve their content as-is)
  const _codeBlocks = [];
  text = text.replace(/```(\w*)\n([\s\S]*?)```/g, function(_, lang, code) {
    var cls = lang ? ' class="language-' + lang + '"' : '';
    _codeBlocks.push('<pre><code' + cls + '>' + escapeHtml(code) + '</code></pre>');
    return '\x00CB' + (_codeBlocks.length - 1) + '\x00';
  });
  const _inlineCodes = [];
  text = text.replace(/`([^`]+)`/g, function(_, code) {
    _inlineCodes.push('<code>' + escapeHtml(code) + '</code>');
    return '\x00IC' + (_inlineCodes.length - 1) + '\x00';
  });
  // 2. Escape ALL remaining HTML (prevents XSS from any source)
  text = escapeHtml(text);
  // 3. Restore code blocks (already escaped internally)
  text = text.replace(/\x00CB(\d+)\x00/g, function(_, i) { return _codeBlocks[parseInt(i)]; });
  text = text.replace(/\x00IC(\d+)\x00/g, function(_, i) { return _inlineCodes[parseInt(i)]; });
  // Markdown links: [text](url) — must run BEFORE bare URL detection
  text = text.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, function(_, label, url) {
    const fileUrl = normalizePawFlowFileUrl(url);
    if (fileUrl.match(/\/files\/[a-f0-9]+\//)) {
      if (isImageFile(label) || isImageFile(url)) {
        return inlineImageHtml(fileUrl, label, '');
      }
      if (isAudioFile(label) || isAudioFile(url)) {
        return inlineAudioHtml(fileUrl, label);
      }
      if (isVideoFile(label) || isVideoFile(url)) {
        return inlineVideoHtml(fileUrl, label);
      }
      return '<a class="flink" href="' + fileUrl + '" style="color:#6c5ce7;cursor:pointer;" onclick="event.preventDefault();openFileViewer(\'' + fileUrl + '\')">\uD83D\uDCC4 ' + label + '</a>';
    }
    return '<a href="' + url + '" target="_blank">' + label + '</a>';
  });
  // Bare file URLs (not already inside a tag attribute)
  text = text.replace(/(^|[\s>])(https?:\/\/[^\s<"']*\/files\/[a-f0-9]+\/([^\s<"')]+))/g, function(_, pre, url, fname) {
    const fileUrl = normalizePawFlowFileUrl(url);
    if (isImageFile(fname)) {
      return pre + inlineImageHtml(fileUrl, fname, '');
    }
    if (isAudioFile(fname)) {
      return pre + inlineAudioHtml(fileUrl, fname);
    }
    if (isVideoFile(fname)) {
      return pre + inlineVideoHtml(fileUrl, fname);
    }
    return pre + '<a class="flink" href="' + fileUrl + '" style="color:#6c5ce7;cursor:pointer;" onclick="event.preventDefault();openFileViewer(\'' + fileUrl + '\')">\uD83D\uDCC4 ' + fname + '</a>';
  });
  text = text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  text = text.replace(/\*(.+?)\*/g, '<em>$1</em>');
  // fs:// URLs — clickable links to filesystem files. For media files
  // stored in FileStore (`fs://filestore/<id>/<name>.png` etc.) render
  // an inline player/image so generate_image / generate_audio /
  // generate_video / screen results display directly in chat without
  // an extra click.
  text = text.replace(/(fs:\/\/([^\s&<"']+))/g, function(_, url, rest) {
    const parts = rest.split('/');
    const service = parts[0];
    const fpath = parts.slice(1).join('/');
    const fname = parts[parts.length - 1] || fpath;
    const isDir = url.endsWith('/');
    if (!isDir && service === 'filestore') {
      const fidMatch = fpath.match(/^([a-f0-9]+)(?:\/|$)/);
      const fid = fidMatch ? fidMatch[1] : '';
      if (fid) {
        const httpUrl = '/files/' + fid + '/' + encodeURIComponent(fname);
        if (isImageFile(fname)) return inlineImageHtml(httpUrl, fname, '');
        if (isAudioFile(fname)) return inlineAudioHtml(httpUrl, fname);
        if (isVideoFile(fname)) return inlineVideoHtml(httpUrl, fname);
      }
    } else if (!isDir && (isImageFile(fname) || isAudioFile(fname) || isVideoFile(fname))) {
      // Non-filestore service (a user relay) — proxy through the
      // /fs/<service>/<path> route registered in pawflow_agent.json.
      // Same-origin URL so the auth cookie applies and the browser
      // can stream / cache like any normal HTTP media.
      const httpUrl = '/fs/' + encodeURIComponent(service) + '/'
          + fpath.split('/').map(encodeURIComponent).join('/');
      if (isImageFile(fname)) return inlineImageHtml(httpUrl, fname, '');
      if (isAudioFile(fname)) return inlineAudioHtml(httpUrl, fname);
      if (isVideoFile(fname)) return inlineVideoHtml(httpUrl, fname);
    }
    const icon = isDir ? '\uD83D\uDCC1' : '\uD83D\uDCC4';
    return '<a class="flink" href="#" style="color:#6c5ce7;cursor:pointer;" onclick="event.preventDefault();fetchFsFile(\'' + service + '\',\'' + fpath + '\')">'
      + icon + ' ' + fname + '</a>';
  });
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

function renderSubAgentTrace(content, extra) {
  const source = (extra && extra.source) || {};
  const trace = (extra && extra.trace) || [];
  const traceId = (extra && extra.trace_id) || '';
  const agentName = source.name || 'sub-agent';
  // Summarize trace
  let totalTools = 0;
  let tokensIn = 0;
  let tokensOut = 0;
  for (const entry of trace) {
    if (entry.type === 'tool_call') totalTools++;
    if (entry.type === 'done') {
      tokensIn = entry.tokens_in || 0;
      tokensOut = entry.tokens_out || 0;
    }
  }
  const tokensK = ((tokensIn + tokensOut) / 1000).toFixed(1);
  const header = escapeHtml(displayAgentName(agentName))
    + ' \u00b7 ' + totalTools + ' tool use' + (totalTools !== 1 ? 's' : '')
    + ' \u00b7 ' + tokensK + 'k tokens';
  // Tool call list (first 3 shown, rest collapsed)
  const toolCalls = trace.filter(e => e.type === 'tool_call');
  let traceHtml = '';
  const showN = 3;
  for (let i = 0; i < Math.min(showN, toolCalls.length); i++) {
    const tc = toolCalls[i];
    const display = (_TOOL_DISPLAY[tc.tool] || tc.tool || '?');
    traceHtml += '<div class="trace-entry">' + escapeHtml(display) + '(' + escapeHtml((tc.path || tc.query || '').substring(0, 60)) + ')</div>';
  }
  if (toolCalls.length > showN) {
    traceHtml += '<div class="trace-entry" style="color:#6c6c8a">+' + (toolCalls.length - showN) + ' more tool uses</div>';
  }
  // Done status
  const doneEntry = trace.find(e => e.type === 'done');
  if (doneEntry) {
    traceHtml += '<div class="trace-entry done">\u23bf  Done</div>';
  }
  // Content
  const contentText = content || '';
  if (contentText) {
    traceHtml += '<div class="trace-content">' + renderMarkdown(contentText) + '</div>';
  }
  return '<div class="sub-agent-trace"' + (traceId ? ' data-trace-id="' + escapeHtml(traceId) + '"' : '') + '>'
    + '<div class="sub-trace-header" onclick="toggleTrace(this)">\u25b6 ' + header + '</div>'
    + '<div class="sub-trace-body" style="display:none">' + traceHtml + '</div>'
    + '</div>';
}

// Render multiple sub-agent traces as a tree (Claude Code style)
function renderMultiAgentTree(traces) {
  if (!traces || traces.length === 0) return '';
  const count = traces.length;
  let html = '<div class="multi-agent-tree">';
  html += '<div class="tree-header" onclick="toggleTrace(this)">\u25b6 '
    + count + ' agent' + (count > 1 ? 's' : '') + ' finished</div>';
  html += '<div class="tree-body" style="display:none">';
  for (let i = 0; i < traces.length; i++) {
    const t = traces[i];
    const isLast = i === traces.length - 1;
    const connector = isLast ? '\u2514\u2500 ' : '\u251c\u2500 ';
    const pipe = isLast ? '   ' : '\u2502  ';
    const name = escapeHtml(t.name || 'agent');
    const tools = t.totalTools || 0;
    const tokensK = ((t.tokensTotal || 0) / 1000).toFixed(1);
    html += '<div class="tree-agent">'
      + '<span style="color:#555">' + connector + '</span>'
      + '<span style="color:#c0c0d0">' + name + '</span>'
      + ' <span style="color:#6c6c8a">\u00b7 ' + tools + ' tool uses \u00b7 ' + tokensK + 'k tokens</span>'
      + '</div>';
    html += '<div class="tree-result"><span style="color:#555">' + pipe + '</span>\u23bf  '
      + '<span style="color:#4ecdc4">' + escapeHtml(t.status || 'Done') + '</span></div>';
  }
  html += '</div></div>';
  return html;
}

function _renderDelegateTraceContent(content, trace, message) {
  let html = '';
  if (message) {
    html += '<div class="delegate-message">\u{1F4E9} ' + renderMarkdown(message) + '</div>';
  }
  const toolCalls = trace.filter(e => e.type === 'tool_call');
  for (const tc of toolCalls) {
    const display = (_TOOL_DISPLAY[tc.tool] || tc.tool || '?');
    let argSummary = '';
    if (tc.arguments && typeof tc.arguments === 'object') {
      const keys = Object.keys(tc.arguments);
      if (keys.length === 1) {
        argSummary = String(tc.arguments[keys[0]]).substring(0, 120);
      } else if (keys.length > 1) {
        argSummary = keys.map(k => k + '=' + String(tc.arguments[k]).substring(0, 60)).join(', ').substring(0, 120);
      }
    }
    html += '<div class="delegate-tool"><span class="tc-bullet done">\u25cf</span> '
      + escapeHtml(display) + '(' + escapeHtml(argSummary) + ')</div>';
  }
  const doneEntry = trace.find(e => e.type === 'done');
  if (doneEntry && doneEntry.status === 'needs_input' && doneEntry.question) {
    html += '<div class="delegate-question">\u{1F4AC} ' + renderMarkdown(doneEntry.question) + '</div>';
  } else if (content) {
    html += '<div class="delegate-response">\u{1F4E8} ' + renderMarkdown(content) + '</div>';
  } else if (doneEntry && doneEntry.error) {
    html += '<div class="delegate-error">\u274C ' + escapeHtml(doneEntry.error) + '</div>';
  }
  const tokensIn = doneEntry ? (doneEntry.tokens_in || 0) : 0;
  const tokensOut = doneEntry ? (doneEntry.tokens_out || 0) : 0;
  const parts = [];
  if (doneEntry && doneEntry.model) parts.push(doneEntry.model);
  parts.push('\u2191' + tokensIn + ' \u2193' + tokensOut);
  parts.push(trace.filter(e => e.type === 'tool_call').length + ' tools');
  html += '<div class="delegate-stats">' + parts.join(' \u00b7 ') + '</div>';
  return html;
}

function renderDelegateBlock(content, extra) {
  const source = (extra && extra.source) || {};
  const trace = (extra && extra.trace) || [];
  const agentName = source.name || 'sub-agent';
  const parentAgent = source.parent_agent || '';
  const llmService = source.llm_service || '';
  const message = source.message || '';
  const svcLabel = llmService ? ' via ' + escapeHtml(llmService) : '';
  // Group header (first agent) — delegate is not a task, no status badge
  let html = '<summary class="delegate-header">\u{1F500} '
    + '<span class="delegate-src">' + escapeHtml(displayAgentName(parentAgent)) + '</span> \u2192 '
    + '<span class="delegate-dst">' + escapeHtml(displayAgentName(agentName)) + '</span>'
    + svcLabel
    + ' <span class="delegate-group-count"></span>'
    + '</summary>';
  html += '<div class="delegate-body">';
  html += _renderDelegateTraceContent(content, trace, message);
  html += '</div>';
  return html;
}

function renderDelegateSubBlock(content, extra) {
  const source = (extra && extra.source) || {};
  const trace = (extra && extra.trace) || [];
  const agentName = source.name || 'sub-agent';
  const llmService = source.llm_service || '';
  const message = source.message || '';
  const svcLabel = llmService ? ' via ' + escapeHtml(llmService) : '';
  let html = '<summary class="delegate-sub-header">\u25b8 '
    + '<span class="delegate-dst">' + escapeHtml(displayAgentName(agentName)) + '</span>'
    + svcLabel
    + '</summary>';
  html += '<div class="delegate-sub-body">';
  html += _renderDelegateTraceContent(content, trace, message);
  html += '</div>';
  return html;
}

function toggleTrace(headerEl) {
  const body = headerEl.nextElementSibling;
  if (!body) return;
  const isHidden = body.style.display === 'none';
  body.style.display = isHidden ? '' : 'none';
  headerEl.textContent = (isHidden ? '\u25bc ' : '\u25b6 ') + headerEl.textContent.substring(2);
}

// Auto-scroll state: true by default. Only explicit user scroll input may turn
// it off; DOM growth/reflow must not be interpreted as the user scrolling up.
let _autoScroll = true;
let _suppressTopLoadUntil = 0;
function isNearBottom() { return _autoScroll; }

(function() {
  const m = document.getElementById('messages');
  if (!m) return;
  let userScrollIntentUntil = 0;
  let scrollbarDragActive = false;

  function atBottom() {
    return m.scrollHeight - m.scrollTop - m.clientHeight <= 5;
  }

  function markUserScrollIntent() {
    userScrollIntentUntil = Date.now() + 700;
  }

  function hasUserScrollIntent() {
    return scrollbarDragActive || Date.now() <= userScrollIntentUntil;
  }

  function isScrollbarPointerEvent(e) {
    const rect = m.getBoundingClientRect();
    const scrollbarWidth = Math.max(12, m.offsetWidth - m.clientWidth);
    return e.clientX >= rect.right - scrollbarWidth - 2;
  }

  m.addEventListener('wheel', markUserScrollIntent, { passive: true });
  m.addEventListener('touchstart', markUserScrollIntent, { passive: true });
  m.addEventListener('pointerdown', (e) => {
    if (isScrollbarPointerEvent(e)) {
      scrollbarDragActive = true;
      markUserScrollIntent();
    }
  });
  window.addEventListener('pointerup', () => {
    if (scrollbarDragActive) markUserScrollIntent();
    scrollbarDragActive = false;
  });
  m.addEventListener('keydown', (e) => {
    if (['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', 'Space'].includes(e.key)) {
      markUserScrollIntent();
    }
  });

  m.addEventListener('scroll', () => {
    if (atBottom()) {
      _autoScroll = true;
    } else if (hasUserScrollIntent()) {
      _autoScroll = false;
    }
  });
})();

function setMessagesScrollTop(value) {
  const m = document.getElementById('messages');
  if (m) m.scrollTop = value;
}

function refreshMessagesScrollMetrics(forceBottom) {
  const m = document.getElementById('messages');
  if (!m) return;
  if (forceBottom) _autoScroll = true;
  const settle = () => {
    if (forceBottom || _autoScroll) setMessagesScrollTop(m.scrollHeight);
    updateScrollNav();
  };
  settle();
  window.requestAnimationFrame(() => {
    settle();
    window.requestAnimationFrame(settle);
  });
}

function scrollMessagesTop() {
  _autoScroll = false;
  _suppressTopLoadUntil = Date.now() + 700;
  setMessagesScrollTop(0);
  updateScrollNav();
}

function scrollBottom(force) {
  refreshMessagesScrollMetrics(!!force);
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

// Auto-load older messages when user scrolls to top
document.getElementById('messages').addEventListener('scroll', function() {
  if (this.scrollTop === 0 && Date.now() > _suppressTopLoadUntil && hasMoreMessages && !loadingMore) {
    loadMoreMessages();
  }
});

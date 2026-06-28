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
  // Unwrap when EITHER the name is a use_tool wrapper, OR the args themselves
  // are a use_tool wrapper (`{tool_name, arguments_json|arguments|parameters}`)
  // even though the name has already been unwrapped upstream. The server emits
  // and persists tool calls in this half-wrapped shape (name `read`, args still
  // `{tool_name, arguments_json}`); without the args-shape check the client
  // renders the raw wrapper, e.g. `Read(tool_name=read, arguments_json={...})`.
  // Mirrors the server-side unwrap_mcp_tool `args.tool_name == name` branch.
  const _argsIsWrapper = toolArgs && typeof toolArgs === 'object' && toolArgs.tool_name
    && (toolArgs.arguments_json !== undefined
        || toolArgs.arguments !== undefined
        || toolArgs.parameters !== undefined);
  if ((_MCP_USE_TOOL_WRAPPERS.has(toolName) || _argsIsWrapper) && toolArgs && typeof toolArgs === 'object') {
    const payload = (!toolArgs.tool_name && toolArgs.parameters && typeof toolArgs.parameters === 'object')
      ? toolArgs.parameters
      : toolArgs;
    if (!payload.tool_name) return { toolName, toolArgs };
    toolName = payload.tool_name;
    // Mirror the server's normalize_observed_tool source order: the advertised
    // string `arguments_json` first (CCI sends args this way), then a legacy
    // `arguments`/`parameters` object. Without this the client renders empty
    // parens for raw use_tool wrappers carrying arguments_json.
    let inner = payload.arguments_json;
    if (inner === undefined || inner === null || inner === '') {
      inner = payload.arguments || payload.parameters || {};
    }
    toolArgs = inner;
    if (typeof toolArgs === 'string') {
      try { toolArgs = JSON.parse(toolArgs); } catch(e) {}
    }
  }
  return { toolName, toolArgs };
}

function _argText(v) {
  if (v !== null && typeof v === 'object') {
    try { return JSON.stringify(v); } catch(e) { return String(v); }
  }
  return String(v);
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
      const val = _argText(args[keys[0]]);
      summary = val.length > 200 ? val.substring(0, 200) + '...' : val;
    } else {
      // Multiple args: show key=value pairs (truncated)
      const parts = [];
      let total = 0;
      for (const k of keys) {
        const val = _argText(args[k]);
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
  // Grouping reparents tool-result media; re-wire any lazy <video> that a
  // deferred per-element pass may have missed (orphaned id after re-render).
  if (typeof hydrateLazyVideos === 'function') hydrateLazyVideos(container);
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


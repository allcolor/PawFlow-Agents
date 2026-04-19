// ── Active agents tracking ──────────────────────────────────────
// Server poll (list_active) is the SINGLE source of truth.
// SSE events (thinking, tool_use, done) provide fast UI hints
// between polls but never override the server state.
let activeInteractions = {};  // agentKey → { name, startedAt, ... }
let activeTimer = null;

function agentKey(name) { return (name || '').toLowerCase(); }

// ── Persistent context-window usage cache ──
// Shared across the header badge + Resource Panel agents. Populated from
// list_resources (load-time) and from SSE message_meta/done (real-time).
// Keyed by lowercase agent instance name.
window._contextUsage = window._contextUsage || {};

// Unified gauge renderer — used by active-agents panel, header badge, and
// Resource Panel Agents section. `usage` is {used, max, pct} (pct 0..1).
function renderCtxGauge(usage, opts) {
  if (!usage || !usage.max) return '';
  opts = opts || {};
  const width = opts.width || 60;
  const pct = Math.max(0, Math.min(1, usage.pct || (usage.used / usage.max)));
  const pctInt = Math.round(pct * 100);
  const usedK = Math.round((usage.used || 0) / 1000);
  const maxK = Math.round(usage.max / 1000);
  const color = (pct >= 0.80) ? '#f0ad4e' : '#4ecdc4';
  const barPx = Math.round(pct * width);
  return '<span class="ctx-gauge" title="Context ' + usedK + 'k/' + maxK + 'k (' + pctInt + '%)" style="display:inline-flex;align-items:center;gap:4px;vertical-align:middle;">'
    + '<span style="display:inline-block;width:' + width + 'px;height:6px;background:#222;border-radius:3px;overflow:hidden;">'
    + '<span style="display:block;width:' + barPx + 'px;height:100%;background:' + color + ';"></span>'
    + '</span>'
    + '<span style="font-size:10px;color:' + color + ';">' + pctInt + '%</span>'
    + '</span>';
}

// Update cache and refresh all UI surfaces that display the gauge.
function setContextUsage(agentName, usage) {
  if (!agentName || !usage) return;
  const key = agentKey(agentName);
  window._contextUsage[key] = {
    used: usage.used || usage.context_used || 0,
    max: usage.max || usage.context_max || 0,
    pct: usage.pct || usage.context_pct || 0,
  };
  if (typeof updateActiveAgentBadge === 'function'
      && typeof selectedAgent !== 'undefined'
      && agentKey(selectedAgent) === key) {
    updateActiveAgentBadge();
  }
  const row = document.querySelector('#res-section-agent [data-ctx-agent="' + key + '"]');
  if (row) row.innerHTML = renderCtxGauge(window._contextUsage[key]);
}

// ── SSE hints (fast UI update between polls) ──
// Active agents come ONLY from syncActiveFromServer (list_active).
// SSE events do NOT add agents to activeInteractions.
function trackAgentStart(agentName, msgPreview) { /* no-op */ }
function trackAgentTool(agentName, toolName) { /* no-op */ }
function trackAgentToolDone(agentName, toolName) { /* no-op */ }

function trackAgentDone(agentName) {
  const key = agentKey(agentName);
  if (!key) return;
  delete activeInteractions[key];
  updateActivePanel();
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
  if (!document.getElementById('typing')) showTyping();
  panel.classList.add('visible');
  const now = Date.now();
  rows.innerHTML = names.map(key => {
    const info = activeInteractions[key];
    let displayName = displayAgentName(info.name);
    if (info.taskId) displayName += ' [task:' + info.taskId + ']';
    const secs = Math.round((now - info.startedAt) / 1000);
    const timeStr = secs < 60 ? secs + 's' : Math.floor(secs/60) + 'm' + (secs%60) + 's';
    let statusParts = [];
    if (info.iteration) statusParts.push('iter ' + info.iteration);
    if (info.round && info.maxRounds > 1) statusParts.push('round ' + info.round + '/' + info.maxRounds);
    if (info.totalTools > 0) statusParts.push(info.totalTools + ' tools');
    if (info.activeTools && info.activeTools.length > 1) {
      statusParts.push('[' + info.activeTools.join(', ') + ']');
    } else if (info.lastTool) {
      statusParts.push('[' + info.lastTool + ']');
    }
    const statusText = statusParts.length > 0 ? statusParts.join(' \u00b7 ') : 'thinking...';
    const preview = (!info.iteration && info.msgPreview) ? escapeHtml(info.msgPreview.substring(0, 40)) : '';
    const hue = Math.abs([...displayName].reduce((h,c) => (h * 31 + c.charCodeAt(0)) | 0, 0)) % 360;
    const color = 'hsl(' + hue + ',70%,65%)';
    const apiName = info.name;
    // Context-fill gauge (from message_meta: context_used/max/pct).
    // Orange warning >=80% (approaching auto-compact / window limit).
    let ctxHtml = '';
    if (info.contextMax && info.contextMax > 0) {
      const pct = Math.max(0, Math.min(1, info.contextPct || (info.contextUsed / info.contextMax)));
      const pctInt = Math.round(pct * 100);
      const usedK = Math.round((info.contextUsed || 0) / 1000);
      const maxK = Math.round(info.contextMax / 1000);
      const gColor = (pct >= 0.80) ? '#f0ad4e' : '#4ecdc4';
      const barPx = Math.round(pct * 60);
      ctxHtml = '<span class="a-ctx" title="Context ' + usedK + 'k/' + maxK + 'k (' + pctInt + '%)">'
        + '<span class="a-ctx-bar" style="display:inline-block;width:60px;height:6px;background:#222;border-radius:3px;vertical-align:middle;overflow:hidden;">'
        + '<span style="display:inline-block;width:' + barPx + 'px;height:100%;background:' + gColor + ';"></span>'
        + '</span>'
        + '<span style="font-size:10px;color:' + gColor + ';margin-left:4px;">' + pctInt + '%</span>'
        + '</span>';
    }
    return '<div class="active-row">'
      + '<span class="a-spinner" style="color:' + color + '">\u2733</span>'
      + '<span class="a-name" style="color:' + color + '">' + escapeHtml(displayName) + '</span>'
      + '<span class="a-msg">' + preview + '</span>'
      + '<span class="a-status">' + escapeHtml(statusText) + '</span>'
      + ctxHtml
      + '<span class="a-time">' + timeStr + '</span>'
      + '<span class="a-actions">'
      + '<button title="Interrupt (force answer)" onclick="interruptSingle(\'' + escapeHtml(apiName) + '\',\'' + escapeHtml(info.taskId || '') + '\')">&#x23F8;</button>'
      + '<button class="btn-stop" title="Stop" onclick="stopSingle(\'' + escapeHtml(apiName) + '\',\'' + escapeHtml(info.taskId || '') + '\')">&#x25A0;</button>'
      + '</span></div>';
  }).join('');
  if (scrollNav) {
    const panelHeight = panel.offsetHeight || 60;
    scrollNav.style.bottom = (75 + panelHeight + 8) + 'px';
  }
  if (!wasVisible && wasAtBottom) scrollBottom(true);
}

// ── Server poll — single source of truth ──
let _syncActiveTimer = null;
function startActiveSync() {
  if (_syncActiveTimer) return;
  _syncActiveTimer = setInterval(syncActiveFromServer, 3000);
}
function stopActiveSync() {
  if (_syncActiveTimer) { clearInterval(_syncActiveTimer); _syncActiveTimer = null; }
}
function syncActiveFromServer() {
  if (!conversationId) return;
  action$('list_active').subscribe(data => {
    if (data.error) return;  // silent — network may be down
    const serverActive = data.active || [];
    const serverKeys = new Set(serverActive.map(a => a.task_id ? agentKey(a.agent_name + '::' + a.task_id) : agentKey(a.agent_name)));

    // Server is the truth — remove anything server doesn't know about
    for (const key of Object.keys(activeInteractions)) {
      if (!serverKeys.has(key)) {
        delete activeInteractions[key];
      }
    }
    const now = Date.now();
    for (const a of serverActive) {
      const key = a.task_id ? agentKey(a.agent_name + '::' + a.task_id) : agentKey(a.agent_name);
      const existing = activeInteractions[key];
      activeInteractions[key] = {
        name: a.agent_name,
        taskId: a.task_id || '',
        startedAt: existing ? existing.startedAt : now - ((a.duration_s || 0) * 1000),
        iteration: a.iteration || (existing ? existing.iteration : 0),
        round: a.round || 0,
        maxRounds: a.max_rounds || 0,
        lastTool: a.last_tool || (existing ? existing.lastTool : ''),
        activeTools: existing ? (existing.activeTools || []) : [],
        totalTools: a.total_tools || (existing ? (existing.totalTools || 0) : 0),
        status: a.status || (existing ? existing.status : 'thinking'),
        msgPreview: a.message_preview || '',
        contextUsed: existing ? existing.contextUsed : 0,
        contextMax: existing ? existing.contextMax : 0,
        contextPct: existing ? existing.contextPct : 0,
        updatedAt: now,
      };
    }
    updateActivePanel();
    if (Object.keys(activeInteractions).length > 0) {
      if (!document.getElementById('typing')) showTyping();
    } else {
      hideTyping();
      if (!sending) document.getElementById('status').textContent = t('ready');
    }
  });
}

function interruptSingle(agentName, taskId) {
  if (!conversationId) return;
  const body = { agent_name: agentName };
  if (taskId) body.task_id = taskId;
  fireAction('interrupt', body);
}
function interruptCurrent() {
  const target = typeof selectedAgent !== 'undefined' && selectedAgent ? selectedAgent : 'ALL';
  cmdAgentInterrupt(target);
}
function stopSingle(agentName, taskId) {
  if (!conversationId) return;
  const body = { agent_name: agentName, force: true };
  if (taskId) body.task_id = taskId;
  fireAction('cancel', body);
  // Optimistic removal — server will confirm on next poll
  const key = taskId ? agentKey(agentName + '::' + taskId) : agentKey(agentName);
  delete activeInteractions[key];
  updateActivePanel();
}

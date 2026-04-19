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
// Resource Panel Agents section. `usage` is {used, max, pct, estimated?}.
// When estimated=true the value comes from client-side accumulation between
// two real `message_meta` updates and is prefixed with '~'.
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
  const tilde = usage.estimated ? '~' : '';
  const title = 'Context ' + tilde + usedK + 'k/' + maxK + 'k ('
    + tilde + pctInt + '%)' + (usage.estimated ? ' (estimated)' : '');
  return '<span class="ctx-gauge" title="' + title + '" style="display:inline-flex;align-items:center;gap:4px;vertical-align:middle;">'
    + '<span style="display:inline-block;width:' + width + 'px;height:6px;background:#222;border-radius:3px;overflow:hidden;">'
    + '<span style="display:block;width:' + barPx + 'px;height:100%;background:' + color
    + (usage.estimated ? ';opacity:0.55' : '') + ';"></span>'
    + '</span>'
    + '<span style="font-size:10px;color:' + color
    + (usage.estimated ? ';opacity:0.7' : '') + ';">' + tilde + pctInt + '%</span>'
    + '</span>';
}

// Update cache and refresh all UI surfaces that display the gauge.
// Real values from `message_meta` always clear the `estimated` flag and
// reset the per-agent estimation accumulator (next chunks restart from 0).
function setContextUsage(agentName, usage) {
  if (!agentName || !usage) return;
  const key = agentKey(agentName);
  const realUsed = usage.used || usage.context_used || 0;
  window._contextUsage[key] = {
    used: realUsed,
    max: usage.max || usage.context_max || 0,
    pct: usage.pct || usage.context_pct || 0,
    estimated: false,
    // Pin the real baseline so subsequent estimation bumps compute
    // baseline + cumulative_chars/4 (NOT used + chunk_chars/4, which
    // would double-count history and runaway to 100%).
    _baselineUsed: realUsed,
  };
  // Reset client-side estimation buffer — the next bumps will accumulate
  // on top of this fresh real value, not on top of stale chunks.
  window._contextEstChars = window._contextEstChars || {};
  window._contextEstChars[key] = 0;
  _refreshGaugeSurfaces(key);
}

// Bump the cached gauge by an estimated `chars/4` tokens. Marks the entry
// as `estimated=true`; the next real `message_meta` will clear that flag.
// Used between two real updates so the gauge animates with each text chunk
// / tool_call / tool_result instead of waiting for the provider's usage.
function bumpContextEstimate(agentName, chars) {
  if (!agentName || !chars) return;
  const key = agentKey(agentName);
  const cached = window._contextUsage[key];
  // Need a real baseline (max + used) to estimate against. If we never
  // received a message_meta yet we have nothing to extrapolate from.
  if (!cached || !cached.max) return;
  window._contextEstChars = window._contextEstChars || {};
  window._contextEstChars[key] = (window._contextEstChars[key] || 0) + chars;
  // Compute against the pinned baseline (last real value), NOT the
  // currently-displayed `cached.used` which itself already includes a
  // previous bump — that would compound exponentially.
  const baseline = (cached._baselineUsed != null) ? cached._baselineUsed : (cached.used || 0);
  const estTokens = Math.round(window._contextEstChars[key] / 4);
  const newUsed = baseline + estTokens;
  window._contextUsage[key] = {
    used: newUsed,
    max: cached.max,
    pct: cached.max > 0 ? newUsed / cached.max : 0,
    estimated: true,
    _baselineUsed: baseline,
  };
  _refreshGaugeSurfaces(key);
}

function _refreshGaugeSurfaces(key) {
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
    // Context-fill gauge. Source priority:
    //  1. activeInteractions[key] fields (set by message_meta SSE handler)
    //  2. window._contextUsage cache (persistent, hydrated by list_resources
    //     at conv load + updated on message_meta). Falling back to the cache
    //     is essential: on a fresh agent row created by syncActiveFromServer
    //     before any message_meta has fired, the instance-local fields are 0
    //     but the persistent cache already has the last known value.
    let ctxUsed = info.contextUsed || 0;
    let ctxMax = info.contextMax || 0;
    let ctxPct = info.contextPct || 0;
    if (!ctxMax && window._contextUsage) {
      const cached = window._contextUsage[agentKey(apiName)];
      if (cached && cached.max) {
        ctxUsed = cached.used || 0;
        ctxMax = cached.max;
        ctxPct = cached.pct || 0;
      }
    }
    let ctxHtml = '';
    if (ctxMax > 0) {
      const pct = Math.max(0, Math.min(1, ctxPct || (ctxUsed / ctxMax)));
      const pctInt = Math.round(pct * 100);
      const usedK = Math.round(ctxUsed / 1000);
      const maxK = Math.round(ctxMax / 1000);
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
      // Hydrate persistent cache from server's persisted context_usage so the
      // gauge renders on page reload before loadResources() finishes.
      if (a.context_usage && a.context_usage.max) {
        const _ck = agentKey(a.agent_name);
        const _cu = a.context_usage;
        // Only hydrate if cache is empty for this agent OR the server value
        // is more recent — never overwrite a fresher value already in cache.
        const _cached = window._contextUsage[_ck];
        if (!_cached || !_cached.max) {
          window._contextUsage[_ck] = {
            used: _cu.used || 0, max: _cu.max, pct: _cu.pct || 0,
            estimated: false, _baselineUsed: _cu.used || 0,
          };
        }
      }
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

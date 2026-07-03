// ── Active agents tracking ──────────────────────────────────────
// Server poll (list_active) is the SINGLE source of truth.
// SSE events (thinking, tool_use, done) provide fast UI hints
// between polls but never override the server state.
let activeInteractions = {};  // agentKey → { name, startedAt, ... }
let activeTimer = null;
let _activeDoneAt = {};  // agentKey → Date.now() from local done/discard hint

function agentKey(name) { return (name || '').toLowerCase(); }
function activeAgentKey(agentName, taskId) {
  return taskId ? agentKey(agentName + '::' + taskId) : agentKey(agentName);
}

// ── Persistent context-window usage cache ──
// Shared across the header badge + Resource Panel agents. Populated from
// list_resources (load-time) and from SSE message_meta/done (real-time).
// Keyed by lowercase agent instance name.
window._contextUsage = window._contextUsage || {};
window._contextUsageHydrating = window._contextUsageHydrating || false;

// Unified gauge renderer — used by active-agents panel, header badge, and
// Resource Panel Agents section. `usage` is {used, max, pct} from the server.
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
  const title = t('contextGaugeTitle', { used: usedK, max: maxK, pct: pctInt });
  return '<span class="ctx-gauge" title="' + escapeAttr(title) + '" style="display:inline-flex;align-items:center;gap:4px;vertical-align:middle;">'
    + '<span style="display:inline-block;width:' + width + 'px;height:6px;background:#222;border-radius:3px;overflow:hidden;">'
    + '<span style="display:block;width:' + barPx + 'px;height:100%;background:' + color + ';"></span>'
    + '</span>'
    + '<span style="font-size:10px;color:' + color + ';">' + pctInt + '%</span>'
    + '</span>';
}

// Per-agent marker kept for compact UI events. It no longer gates real
// server updates: a persisted/message_meta gauge is authoritative and may
// decrease after compaction, rebuild, provider restart, or context repair.
window._compactPending = window._compactPending || {};

function markCompactJustHappened(agentName) {
  if (!agentName) return;
  window._compactPending[agentKey(agentName)] = true;
}

// Update cache and refresh all UI surfaces that display the gauge.
//
// `usage` is a real server value (message_meta, done, compact_progress,
// list_active, or list_resources), not a client estimate. Real values must
// be allowed to move both up and down; otherwise a post-compact gauge can
// get stuck forever on a stale pre-compact percentage. Stale server polls
// are rejected by updated_at/ts when both sides provide a timestamp.
function setContextUsage(agentName, usage) {
  if (!agentName || !usage) return;
  if (usage.conversation_id && typeof conversationId !== 'undefined'
      && usage.conversation_id !== conversationId) return;
  const key = agentKey(agentName);
  const realUsed = usage.used !== undefined ? usage.used : (usage.context_used || 0);
  const newMax = usage.max !== undefined ? usage.max : (usage.context_max || 0);
  const cached = window._contextUsage[key];
  const cachedUsed = (cached && cached.used) || 0;
  const incomingAt = Number(usage.updated_at || usage.ts || 0) || 0;
  const cachedAt = Number(cached && cached._updatedAt || 0) || 0;

  // Never demote a non-zero gauge back to zero. A zero update on an already
  // populated context usually means the provider had no usage data yet.
  if (realUsed === 0 && cachedUsed > 0) {
    return;
  }
  // Reject older persisted/polled values when we have timestamps on both
  // sides. This replaces the old "never decrease without compact" guard,
  // which froze the gauge after legitimate compactions.
  if (!incomingAt) {
    throw new Error('BUG: context_usage update missing updated_at/ts for ' + agentName);
  }
  if (incomingAt && cachedAt && incomingAt < cachedAt) {
    return;
  }
  if (window._compactPending[key]) {
    delete window._compactPending[key];
  }
  const pct = newMax > 0 ? realUsed / newMax : 0;
  window._contextUsage[key] = {
    used: realUsed,
    max: newMax,
    pct: pct,
    _updatedAt: incomingAt || (Date.now() / 1000),
  };
  if (typeof activeInteractions !== 'undefined' && activeInteractions[key]) {
    activeInteractions[key].contextUsed = realUsed;
    activeInteractions[key].contextMax = newMax;
    activeInteractions[key].contextPct = pct;
  }
  _refreshGaugeSurfaces(key);
}

function hydrateContextUsage() {
  if (!conversationId || typeof action$ !== 'function') return;
  if (window._contextUsageHydrating) return;
  window._contextUsageHydrating = true;
  action$('list_context_usage', { conversation_id: conversationId }).subscribe(data => {
    window._contextUsageHydrating = false;
    const usageMap = (data && data.context_usage) || {};
    Object.keys(usageMap).forEach(agentName => {
      setContextUsage(agentName, usageMap[agentName]);
    });
  }, () => { window._contextUsageHydrating = false; });
}

function _refreshGaugeSurfaces(key) {
  if (typeof updateActiveAgentBadge === 'function'
      && typeof selectedAgent !== 'undefined'
      && agentKey(selectedAgent) === key) {
    updateActiveAgentBadge();
  }
  const safeKey = window.CSS && CSS.escape ? CSS.escape(key) : String(key).replace(/"/g, '\\"');
  const row = document.querySelector('#res-section-agent [data-ctx-agent="' + safeKey + '"]');
  if (row) row.innerHTML = renderCtxGauge(window._contextUsage[key]);
}

// ── SSE hints (fast UI update between polls) ──
// list_active remains the authoritative source, but a fresh turn can spend
// several seconds preparing context before the next poll. SSE hints keep the
// Active Agents panel visible immediately; syncActiveFromServer removes rows
// that the server no longer reports.
function trackAgentStart(agentName, msgPreview, taskId) {
  const key = activeAgentKey(agentName, taskId || '');
  if (!key) return;
  const existing = activeInteractions[key] || {};
  activeInteractions[key] = {
    name: agentName,
    taskId: taskId || existing.taskId || '',
    startedAt: existing.startedAt || Date.now(),
    iteration: existing.iteration || 0,
    round: existing.round || 0,
    maxRounds: existing.maxRounds || 0,
    lastTool: existing.lastTool || '',
    activeTools: existing.activeTools || [],
    totalTools: existing.totalTools || 0,
    status: existing.status || t('thinking') + '...',
    msgPreview: msgPreview || existing.msgPreview || '',
    contextUsed: existing.contextUsed || 0,
    contextMax: existing.contextMax || 0,
    contextPct: existing.contextPct || 0,
    ccLive: !!existing.ccLive,
    ccReuseCount: existing.ccReuseCount || 0,
    ccLivedSeconds: existing.ccLivedSeconds || 0,
    ccIdleSeconds: existing.ccIdleSeconds || 0,
    codexLive: !!existing.codexLive,
    codexReuseCount: existing.codexReuseCount || 0,
    codexLivedSeconds: existing.codexLivedSeconds || 0,
    codexIdleSeconds: existing.codexIdleSeconds || 0,
    geminiLive: !!existing.geminiLive,
    geminiReuseCount: existing.geminiReuseCount || 0,
    geminiLivedSeconds: existing.geminiLivedSeconds || 0,
    geminiIdleSeconds: existing.geminiIdleSeconds || 0,
    updatedAt: Date.now(),
  };
  if (typeof setConversationWorking === 'function') {
    setConversationWorking(conversationId, true);
  }
  updateActivePanel();
}
function trackAgentTool(agentName, toolName, taskId) {
  const key = activeAgentKey(agentName, taskId || '');
  if (!key) return;
  if (!activeInteractions[key]) trackAgentStart(agentName, '', taskId || '');
  const info = activeInteractions[key];
  info.lastTool = toolName || info.lastTool || '';
  info.status = toolName ? t('usingTool', { tool: toolName }) : t('usingTool', { tool: t('tool') });
  info.activeTools = toolName ? [toolName] : (info.activeTools || []);
  info.totalTools = (info.totalTools || 0) + 1;
  info.updatedAt = Date.now();
  updateActivePanel();
}
function trackAgentToolDone(agentName, toolName, taskId) {
  const key = activeAgentKey(agentName, taskId || '');
  if (!key || !activeInteractions[key]) return;
  const info = activeInteractions[key];
  info.activeTools = [];
  info.status = t('thinking') + '...';
  info.updatedAt = Date.now();
  updateActivePanel();
}

function trackAgentDone(agentName, taskId) {
  const key = activeAgentKey(agentName, taskId || '');
  if (!key) return;
  const now = Date.now();
  _activeDoneAt[key] = now;
  delete activeInteractions[key];
  updateActivePanel();
  if (Object.keys(activeInteractions).length === 0
      && typeof setConversationWorking === 'function') {
    setConversationWorking(conversationId, false);
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
    if (info.totalTools > 0) statusParts.push(info.totalTools + ' tools');
    if (info.activeTools && info.activeTools.length > 1) {
      statusParts.push('[' + info.activeTools.join(', ') + ']');
    } else if (info.lastTool) {
      statusParts.push('[' + info.lastTool + ']');
    }
    const statusText = statusParts.length > 0
      ? statusParts.join(' \u00b7 ')
      : (info.status || t('thinking') + '...');
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
    if (window._contextUsage) {
      const cached = window._contextUsage[agentKey(apiName)];
      if (cached && cached.max) {
        ctxUsed = cached.used || 0;
        ctxMax = cached.max;
        ctxPct = cached.pct || 0;
      }
    }
    // Use the unified renderCtxGauge — it uses display:block on the bar fill
    // which renders correctly. The previous inline markup used inline-block +
    // height:100% on the fill, a browser quirk where inline-block does not
    // establish a reliable containing block for percentage heights, so the
    // colored bar collapsed to 0px (only the % number was visible).
    let ctxHtml = '';
    if (ctxMax > 0) {
      ctxHtml = renderCtxGauge({ used: ctxUsed, max: ctxMax, pct: ctxPct }, { width: 60 });
    }
    // Live-session badge + restart button: shown when ANY supported CLI
    // (claude code / codex / gemini) is reusing a warm container for this
    // agent. The first match wins for the badge label/tooltip; the restart
    // button targets the matching CLI's restart action.
    let liveBadge = '';
    let restartBtn = '';
    const liveCli = info.ccLive ? 'cc'
                  : info.codexLive ? 'codex'
                  : info.geminiLive ? 'gemini' : '';
    if (liveCli) {
      const lived = Math.round(
        liveCli === 'cc' ? (info.ccLivedSeconds || 0)
        : liveCli === 'codex' ? (info.codexLivedSeconds || 0)
        : (info.geminiLivedSeconds || 0));
      const reuseCount =
        liveCli === 'cc' ? (info.ccReuseCount || 0)
        : liveCli === 'codex' ? (info.codexReuseCount || 0)
        : (info.geminiReuseCount || 0);
      const livedStr = lived < 60 ? lived + 's'
        : Math.floor(lived / 60) + 'm' + (lived % 60) + 's';
      const cliLabel = liveCli === 'cc' ? 'Claude Code'
                     : liveCli === 'codex' ? 'Codex' : 'Gemini';
      const liveTitle = t('cliSessionReusedTitle', {
        cli: cliLabel,
        lived: livedStr,
        count: reuseCount,
      });
      liveBadge = '<span class="a-cc-live" title="' + escapeAttr(liveTitle)
        + '" style="display:inline-block;padding:1px 5px;margin-right:4px;'
        + 'font-size:9px;font-weight:bold;color:#0f0;border:1px solid #0f0;'
        + 'border-radius:3px;vertical-align:middle;">LIVE</span>';
      const restartFn = liveCli === 'cc' ? 'ccRestartSingle'
                      : liveCli === 'codex' ? 'codexRestartSingle'
                      : 'geminiRestartSingle';
      restartBtn = '<button class="btn-cc-restart" title="' + escapeAttr(t('restartCliTitle', { cli: cliLabel })) + '"'
        + ' onclick="' + restartFn + '(' + jsStringArg(apiName) + ')"'
        + '>&#x21BB;</button>';
    }
    return '<div class="active-row">'
      + '<span class="a-spinner" style="color:' + color + '">\u2733</span>'
      + liveBadge
      + '<span class="a-name" style="color:' + color + '">' + escapeHtml(displayName) + '</span>'
      + '<span class="a-msg">' + preview + '</span>'
      + '<span class="a-status">' + escapeHtml(statusText) + '</span>'
      + ctxHtml
      + '<span class="a-time">' + timeStr + '</span>'
      + '<span class="a-actions">'
      + '<button title="' + escapeAttr(t('stopTitle')) + '" onclick="interruptSingle(' + jsStringArg(apiName) + ',' + jsStringArg(info.taskId || '') + ')">&#x23F8;</button>'
      + restartBtn
      + '<button class="btn-stop" title="' + escapeAttr(t('stop')) + '" onclick="stopSingle(' + jsStringArg(apiName) + ',' + jsStringArg(info.taskId || '') + ')">&#x25A0;</button>'
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
let _syncActiveSub = null;
let _syncActiveStartedAt = 0;
const _SYNC_ACTIVE_STALE_MS = 10000;
function startActiveSync() {
  if (_syncActiveTimer) return;
  _syncActiveTimer = setInterval(syncActiveFromServer, 10000);
}
function stopActiveSync() {
  if (_syncActiveTimer) { clearInterval(_syncActiveTimer); _syncActiveTimer = null; }
  if (_syncActiveSub) { try { _syncActiveSub.unsubscribe(); } catch (_) {} }
  _syncActiveSub = null;
  _syncActiveStartedAt = 0;
}
function syncActiveFromServer(force) {
  if (!conversationId) return;
  if (!force && typeof document !== 'undefined' && document.hidden) return;
  const requestedConversationId = conversationId;
  const now = Date.now();
  if (_syncActiveSub) {
    if (!force && now - _syncActiveStartedAt < _SYNC_ACTIVE_STALE_MS) return;
    try { _syncActiveSub.unsubscribe(); } catch (_) {}
    _syncActiveSub = null;
  }
  _syncActiveStartedAt = now;
  const requestStartedAt = now;
  _syncActiveSub = action$('list_active', { conversation_id: requestedConversationId }, { silent: true }).subscribe(data => {
    _syncActiveSub = null;
    _syncActiveStartedAt = 0;
    if (data.error) return;  // silent — network may be down
    if (data.conversation_id && data.conversation_id !== conversationId) return;
    if (requestedConversationId !== conversationId) return;
    const serverActive = (data.active || []).filter(a => {
      const key = activeAgentKey(a.agent_name, a.task_id || '');
      const doneAt = _activeDoneAt[key] || 0;
      if (doneAt && requestStartedAt <= doneAt) return false;
      if (doneAt) delete _activeDoneAt[key];
      return true;
    });
    const hasActiveForCurrentConv = serverActive.length > 0;
    if (typeof setConversationWorking === 'function') {
      setConversationWorking(conversationId, hasActiveForCurrentConv);
    }
    const serverKeys = new Set(serverActive.map(a => activeAgentKey(a.agent_name, a.task_id || '')));

    // Server is the truth — remove anything server doesn't know about
    for (const key of Object.keys(activeInteractions)) {
      if (!serverKeys.has(key)) {
        delete activeInteractions[key];
      }
    }
    const now = Date.now();
    for (const a of serverActive) {
      const key = activeAgentKey(a.agent_name, a.task_id || '');
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
        // Per-CLI live-session reuse telemetry. Server enriches the
        // matching block when the warm container for (conv, agent) is
        // alive in the corresponding registry; absence means no live
        // session. Only one CLI can be alive at a time for a given agent.
        ccLive: !!a.cc_live,
        ccReuseCount: a.cc_reuse_count || 0,
        ccLivedSeconds: a.cc_lived_seconds || 0,
        ccIdleSeconds: a.cc_idle_seconds || 0,
        codexLive: !!a.codex_live,
        codexReuseCount: a.codex_reuse_count || 0,
        codexLivedSeconds: a.codex_lived_seconds || 0,
        codexIdleSeconds: a.codex_idle_seconds || 0,
        geminiLive: !!a.gemini_live,
        geminiReuseCount: a.gemini_reuse_count || 0,
        geminiLivedSeconds: a.gemini_lived_seconds || 0,
        geminiIdleSeconds: a.gemini_idle_seconds || 0,
        updatedAt: now,
      };
      // list_active is status-only. Context gauge hydration is handled by
      // hydrateContextUsage() on load/switch, then live message_meta events.
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
  const key = activeAgentKey(agentName, taskId || '');
  delete activeInteractions[key];
  updateActivePanel();
}

// Kill the warm CLI container for a specific agent in this conv. Next
// turn will spawn fresh. _restartCliSingle is the per-CLI factory; the
// public ccRestartSingle/codexRestartSingle/geminiRestartSingle wrappers
// keep the inline onclick contract stable.
function _restartCliSingle(cli, agentName) {
  if (!conversationId || !agentName) return;
  const action = cli + '_restart';
  fireAction(action, { agent_name: agentName });
  // Optimistically clear the live badge — the server poll will
  // authoritatively refresh it (to absent) within a few seconds.
  const key = agentKey(agentName);
  const info = activeInteractions[key];
  if (info) {
    if (cli === 'cc') {
      info.ccLive = false; info.ccReuseCount = 0; info.ccLivedSeconds = 0;
    } else if (cli === 'codex') {
      info.codexLive = false; info.codexReuseCount = 0; info.codexLivedSeconds = 0;
    } else if (cli === 'gemini') {
      info.geminiLive = false; info.geminiReuseCount = 0; info.geminiLivedSeconds = 0;
    }
  }
  updateActivePanel();
}
function ccRestartSingle(agentName)     { _restartCliSingle('cc', agentName); }
function codexRestartSingle(agentName)  { _restartCliSingle('codex', agentName); }
function geminiRestartSingle(agentName) { _restartCliSingle('gemini', agentName); }

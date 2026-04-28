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

// Per-agent flag set by `markCompactJustHappened(agentName)` (wired to
// the SSE `compact_progress stage=done` event). `setContextUsage`
// requires it to be true to accept a decrease, and clears it after
// consuming the drop. Without this, a stray late event from before a
// turn-end could reduce the gauge by mistake.
window._compactPending = window._compactPending || {};

function markCompactJustHappened(agentName) {
  if (!agentName) return;
  window._compactPending[agentKey(agentName)] = true;
}

// Update cache and refresh all UI surfaces that display the gauge.
//
// Invariants enforced here, in line with the conversation-size physics:
//   1. The gauge can only land on 0% on a brand-new empty conversation.
//      A 0 update on an agent that already had a non-zero reading is a
//      provider quirk (fallback / pre-prompt / cleared usage) — ignore.
//   2. The gauge can only DECREASE when a compact has just happened
//      for this agent. Otherwise an unsolicited drop (e.g. tool result
//      cleanup that doesn't shrink the prompt, or a stale event) is
//      ignored. The compact path explicitly opts in via
//      `markCompactJustHappened(agent)` before its post-compact usage
//      arrives.
//
// Real values from `message_meta` always clear the `estimated` flag and
// reset the per-agent estimation accumulator (next chunks restart from 0).
function setContextUsage(agentName, usage) {
  if (!agentName || !usage) return;
  const key = agentKey(agentName);
  const realUsed = usage.used || usage.context_used || 0;
  const newMax = usage.max || usage.context_max || 0;
  const cached = window._contextUsage[key];
  const cachedUsed = (cached && cached.used) || 0;

  // Rule 1: never demote a non-zero gauge back to zero.
  if (realUsed === 0 && cachedUsed > 0) {
    return;
  }
  // Rule 2: a strict decrease is only allowed when a compact for this
  // agent has been signalled since the last accepted update. Any other
  // drop is a UI artefact and is ignored.
  if (realUsed < cachedUsed && !window._compactPending[key]) {
    return;
  }
  // Accepted update — if a compact was pending, this is the post-compact
  // baseline and the flag is consumed.
  if (window._compactPending[key]) {
    delete window._compactPending[key];
  }
  window._contextUsage[key] = {
    used: realUsed,
    max: newMax,
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
  // Keep the active-panel mirror (`activeInteractions`) in sync so the
  // panel's per-agent row uses the same monotonic value. Direct
  // mutations from elsewhere are gone — setContextUsage is the single
  // entry point that enforces the invariants.
  if (typeof activeInteractions !== 'undefined' && activeInteractions[key]) {
    activeInteractions[key].contextUsed = realUsed;
    activeInteractions[key].contextMax = newMax;
    activeInteractions[key].contextPct =
      usage.pct || usage.context_pct || (newMax > 0 ? realUsed / newMax : 0);
  }
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
      const liveTitle = cliLabel + ' reused (lived ' + livedStr
        + ', reuse #' + reuseCount + ')';
      liveBadge = '<span class="a-cc-live" title="' + liveTitle
        + '" style="display:inline-block;padding:1px 5px;margin-right:4px;'
        + 'font-size:9px;font-weight:bold;color:#0f0;border:1px solid #0f0;'
        + 'border-radius:3px;vertical-align:middle;">LIVE</span>';
      const restartFn = liveCli === 'cc' ? 'ccRestartSingle'
                      : liveCli === 'codex' ? 'codexRestartSingle'
                      : 'geminiRestartSingle';
      restartBtn = '<button class="btn-cc-restart" title="Restart ' + cliLabel + ' (kill warm session)"'
        + ' onclick="' + restartFn + '(\'' + escapeHtml(apiName) + '\')"'
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
      + '<button title="Interrupt (force answer)" onclick="interruptSingle(\'' + escapeHtml(apiName) + '\',\'' + escapeHtml(info.taskId || '') + '\')">&#x23F8;</button>'
      + restartBtn
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
      // Hydrate persistent cache from server's persisted context_usage so the
      // gauge renders on page reload before loadResources() finishes. Route
      // through setContextUsage so stale list_active payloads cannot demote a
      // fresher SSE/resource-panel gauge.
      if (a.context_usage && a.context_usage.max) {
        setContextUsage(a.agent_name, {
          used: a.context_usage.used || 0,
          max: a.context_usage.max || 0,
          pct: a.context_usage.pct || 0,
        });
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

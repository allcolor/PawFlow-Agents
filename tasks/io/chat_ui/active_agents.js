// ── Active agents tracking ──────────────────────────────────────
// Server poll (list_active) is the SINGLE source of truth.
// SSE events (thinking, tool_use, done) provide fast UI hints
// between polls but never override the server state.
let activeInteractions = {};  // agentKey → { name, startedAt, ... }
let activeTimer = null;

function agentKey(name) { return (name || '').toLowerCase(); }

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
    const displayName = displayAgentName(info.name);
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
    return '<div class="active-row">'
      + '<span class="a-spinner" style="color:' + color + '">\u2733</span>'
      + '<span class="a-name" style="color:' + color + '">' + escapeHtml(displayName) + '</span>'
      + '<span class="a-msg">' + preview + '</span>'
      + '<span class="a-status">' + escapeHtml(statusText) + '</span>'
      + '<span class="a-time">' + timeStr + '</span>'
      + '<span class="a-actions">'
      + '<button title="Interrupt (force answer)" onclick="interruptSingle(\'' + escapeHtml(apiName) + '\')">&#x23F8;</button>'
      + '<button class="btn-stop" title="Stop" onclick="stopSingle(\'' + escapeHtml(apiName) + '\')">&#x25A0;</button>'
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
async function syncActiveFromServer() {
  if (!conversationId) return;
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'list_active', conversation_id: conversationId }),
      credentials: 'same-origin',
    });
    if (!resp.ok) return;
    const data = await resp.json();
    const serverActive = data.active || [];
    const serverKeys = new Set(serverActive.map(a => agentKey(a.agent_name)));

    // Server is the truth — remove anything server doesn't know about
    for (const key of Object.keys(activeInteractions)) {
      if (!serverKeys.has(key)) {
        delete activeInteractions[key];
      }
    }
    // Add/update from server
    const now = Date.now();
    for (const a of serverActive) {
      const key = agentKey(a.agent_name);
      const existing = activeInteractions[key];
      activeInteractions[key] = {
        name: a.agent_name,
        startedAt: existing ? existing.startedAt : now - ((a.duration_s || 0) * 1000),
        iteration: a.iteration || (existing ? existing.iteration : 0),
        round: a.round || 0,
        maxRounds: a.max_rounds || 0,
        lastTool: a.last_tool || (existing ? existing.lastTool : ''),
        activeTools: existing ? (existing.activeTools || []) : [],
        totalTools: a.total_tools || (existing ? (existing.totalTools || 0) : 0),
        status: a.status || (existing ? existing.status : 'thinking'),
        msgPreview: a.message_preview || '',
        updatedAt: now,
      };
    }
    updateActivePanel();
    if (Object.keys(activeInteractions).length > 0) {
      if (!document.getElementById('typing')) showTyping();
    } else {
      hideTyping();
    }
  } catch(e) { /* silent — network may be down */ }
}

async function interruptSingle(agentName) {
  if (!conversationId) return;
  try {
    await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'interrupt', conversation_id: conversationId, agent_name: agentName }),
      credentials: 'same-origin',
    });
  } catch(e) { console.warn('Interrupt failed:', e); }
}
function interruptCurrent() {
  const target = typeof selectedAgent !== 'undefined' && selectedAgent ? selectedAgent : 'ALL';
  cmdAgentInterrupt(target);
}
async function stopSingle(agentName) {
  if (!conversationId) return;
  try {
    await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'cancel', conversation_id: conversationId, agent_name: agentName, force: true }),
      credentials: 'same-origin',
    });
    // Optimistic removal — server will confirm on next poll
    trackAgentDone(agentName);
  } catch(e) { console.warn('Stop failed:', e); }
}

// ── Active interactions tracking ──────────────────────────────────
let activeInteractions = {};  // agentKey (lowercase) → { name, startedAt, lastTool, activeTools, status, msgPreview }
let activeTimer = null;

function agentKey(name) { return (name || '').toLowerCase(); }

let _agentDoneAt = {};  // agentKey → timestamp of last done (prevents ghost re-register)
function trackAgentStart(agentName, msgPreview) {
  if (!agentName) return;  // ignore empty agent names (compact, internal)
  const key = agentKey(agentName);
  // Ignore thinking events that arrive within 500ms after a done (race condition guard)
  const doneTs = _agentDoneAt[key];
  if (doneTs && Date.now() - doneTs < 500) {
    console.log('[trackAgentStart] IGNORED (too close to done)', agentName);
    return;
  }
  if (activeInteractions[key]) {
    // Already tracked — just update status (don't reset startedAt/preview)
    activeInteractions[key].status = 'thinking';
    activeInteractions[key].activeTools = [];
  } else {
    activeInteractions[key] = {
      name: agentName || '',
      startedAt: Date.now(), lastTool: '', activeTools: [], status: 'thinking', msgPreview: msgPreview || '',
      updatedAt: Date.now(),
    };
  }
  updateActivePanel();
  if (!activeTimer) activeTimer = setInterval(updateActivePanel, 1000);
}
function _ensureInteraction(agentName) {
  if (!agentName) return '';
  const key = agentKey(agentName);
  if (!activeInteractions[key]) {
    activeInteractions[key] = {
      name: agentName || '',
      startedAt: Date.now(), lastTool: '', activeTools: [], status: 'thinking', msgPreview: '',
      updatedAt: Date.now(),
    };
    if (!activeTimer) activeTimer = setInterval(updateActivePanel, 1000);
  }
  // Ensure activeTools exists (backward compat)
  if (!activeInteractions[key].activeTools) activeInteractions[key].activeTools = [];
  return key;
}
function trackAgentTool(agentName, toolName) {
  const key = _ensureInteraction(agentName);
  if (!key) return;
  activeInteractions[key].lastTool = toolName;
  activeInteractions[key].status = toolName;
  const at = activeInteractions[key].activeTools;
  if (at.indexOf(toolName) === -1) at.push(toolName);
  updateActivePanel();
}
function trackAgentToolDone(agentName, toolName) {
  const key = _ensureInteraction(agentName);
  if (!key) return;
  if (activeInteractions[key]) {
    const at = activeInteractions[key].activeTools;
    const idx = at.indexOf(toolName);
    if (idx !== -1) at.splice(idx, 1);
    // Update status to remaining tool or thinking
    if (at.length > 0) {
      activeInteractions[key].status = at[at.length - 1];
    } else {
      activeInteractions[key].status = 'thinking';
    }
  }
  updateActivePanel();
}
function trackAgentDone(agentName) {
  const key = agentKey(agentName);
  _agentDoneAt[key] = Date.now();
  delete activeInteractions[key];
  updateActivePanel();
  if (Object.keys(activeInteractions).length === 0 && activeTimer) {
    clearInterval(activeTimer); activeTimer = null;
  }
}
function updateActivePanel() {
  const panel = document.getElementById('activePanel');
  const rows = document.getElementById('activeRows');
  const now0 = Date.now();
  for (const k of Object.keys(activeInteractions)) {
    if (now0 - (activeInteractions[k].updatedAt || activeInteractions[k].startedAt) > 120000) delete activeInteractions[k];
  }
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
  // Active agents → ensure thinking indicator is visible
  if (!document.getElementById('typing')) showTyping();
  panel.classList.add('visible');
  const now = Date.now();
  rows.innerHTML = names.map(key => {
    const info = activeInteractions[key];
    const displayName = displayAgentName(info.name);
    const secs = Math.round((now - info.startedAt) / 1000);
    const timeStr = secs < 60 ? secs + 's' : Math.floor(secs/60) + 'm' + (secs%60) + 's';
    // Build rich status: iter N · round N/M · N tools · [active tools]
    let statusParts = [];
    if (info.iteration) statusParts.push('iter ' + info.iteration);
    if (info.round && info.maxRounds > 1) statusParts.push('round ' + info.round + '/' + info.maxRounds);
    if (info.totalTools > 0) statusParts.push(info.totalTools + ' tools');
    // Show all concurrent active tools, not just the last one
    if (info.activeTools && info.activeTools.length > 1) {
      statusParts.push('[' + info.activeTools.join(', ') + ']');
    } else if (info.lastTool) {
      statusParts.push('[' + info.lastTool + ']');
    }
    const statusText = statusParts.length > 0 ? statusParts.join(' \u00b7 ') : 'thinking...';
    const preview = (!info.iteration && info.msgPreview) ? escapeHtml(info.msgPreview.substring(0, 40)) : '';
    const hue = Math.abs([...displayName].reduce((h,c) => (h * 31 + c.charCodeAt(0)) | 0, 0)) % 360;
    const color = 'hsl(' + hue + ',70%,65%)';
    // Use info.name (original casing) for API calls like interrupt/stop
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
  // Push scroll-nav above the active panel
  if (scrollNav) {
    const panelHeight = panel.offsetHeight || 60;
    scrollNav.style.bottom = (75 + panelHeight + 8) + 'px';
  }
  if (!wasVisible && wasAtBottom) scrollBottom(true);
}

// Sync active agents from server (source of truth)
let _syncActiveTimer = null;
function startActiveSync() {
  if (_syncActiveTimer) return;
  _syncActiveTimer = setInterval(syncActiveFromServer, 2000);
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
    const now = Date.now();

    // Remove entries the server no longer knows about
    // BUT keep entries added by SSE less than 5s ago (race condition guard)
    for (const key of Object.keys(activeInteractions)) {
      if (!serverKeys.has(key) && (now - (activeInteractions[key].updatedAt || 0)) > 5000) {
        delete activeInteractions[key];
      }
    }
    // Add/update from server
    for (const a of serverActive) {
      const key = agentKey(a.agent_name);
      const existing = activeInteractions[key];
      activeInteractions[key] = {
        name: a.agent_name,
        startedAt: existing ? existing.startedAt : now - (a.duration_s * 1000),
        iteration: a.iteration || (existing ? existing.iteration : 0),
        lastTool: a.last_tool || (existing ? existing.lastTool : ''),
        totalTools: existing ? (existing.totalTools || 0) : 0,
        msgPreview: a.message_preview || '',
        updatedAt: now,
      };
    }
    updateActivePanel();
    // Thinking: show if agents active, hide if none
    if (Object.keys(activeInteractions).length > 0) {
      if (!document.getElementById('typing')) showTyping();
    } else {
      hideTyping();
    }
  } catch(e) { /* silent */ }
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
async function stopSingle(agentName) {
  // Stop button in active panel = force stop (no response, immediate kill)
  if (!conversationId) return;
  try {
    await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'cancel', conversation_id: conversationId, agent_name: agentName, force: true }),
      credentials: 'same-origin',
    });
    trackAgentDone(agentName);
  } catch(e) { console.warn('Stop failed:', e); }
}


// ── Global app state ──
// These are shared across all JS modules via the global scope.
const _seenMsgIds = new Set();  // dedup msg_ids across SSE + poll + replay
const _selectedMsgIds = new Set();  // multiselect for batch delete
let conversationId = null;
let sending = false;
// contextOpInProgress removed — all ops are async, nothing blocks UI
let eventSource = null;
let pendingAgent = null;  // agent to select when first message creates a conversation
let selectedAgent = '';   // currently active agent ('' = default)
let sseRetryCount = 0;    // for exponential backoff on reconnect
let sseReconnectTimer = null;

// ── Permission helpers ──
// window._userRole is set by loadResources() from the server response
function _isAdmin() { return (window._userRole || '') === 'admin'; }
function _canEditScope(scope) {
  // Non-global scopes: always editable by the owner
  if (scope !== 'global') return true;
  // Global scope: only admin can edit
  return _isAdmin();
}

// ── Password visibility toggle ──
function _togglePwdVis(inputId, btn) {
  const el = document.getElementById(inputId);
  if (!el) return;
  if (el.type === 'password') { el.type = 'text'; btn.textContent = '\u{1F648}'; }
  else { el.type = 'password'; btn.textContent = '\u{1F441}'; }
}

// Per-agent streaming state — prevents cross-agent clobbering when multiple
// agents (random thoughts, sub-agents) stream concurrently.
let streams = {};  // agentName → { el, text, chunks }
// Legacy aliases for backward compat with code that reads these globals
let streamingEl = null;
let streamingText = '';
let streamingChunks = [];
let streamingAgent = '';

function getStream(agent) {
  const key = (agent || '').toLowerCase();
  if (!streams[key]) streams[key] = { el: null, text: '', chunks: [] };
  return streams[key];
}
function clearStream(agent) {
  const key = (agent || '').toLowerCase();
  delete streams[key];
  // Sync legacy globals if this was the active stream
  if (!streamingAgent || streamingAgent.toLowerCase() === key) {
    streamingEl = null; streamingText = ''; streamingChunks = []; streamingAgent = '';
  }
}
function clearAllStreams() {
  for (const a of Object.keys(streams)) {
    const s = streams[a];
    for (const c of s.chunks) { if (c && c.parentNode) c.remove(); }
  }
  streams = {};
  streamingEl = null; streamingText = ''; streamingChunks = []; streamingAgent = '';
}
function clearAllStreamsKeepDOM() {
  streams = {};
  streamingEl = null; streamingText = ''; streamingChunks = []; streamingAgent = '';
}
let permissionMode = 'default';  // current tool permission mode

function setPermissionMode(mode) {
  permissionMode = mode;
  fireAction('set_permission_mode', { conversation_id: conversationId, mode });
  updatePermissionBadge();
}

function loadPermissionMode() {
  if (!conversationId) { updatePermissionBadge(); return; }
  action$('get_permission_mode', { conversation_id: conversationId })
    .subscribe(d => {
      permissionMode = d.permission_mode || 'default';
      const sel = document.getElementById('permissionMode');
      if (sel) sel.value = permissionMode;
      updatePermissionBadge();
    });
}

function updatePermissionBadge() {
  const sel = document.getElementById('permissionMode');
  if (!sel) return;
  sel.style.display = conversationId ? '' : 'none';
  sel.value = permissionMode;
  // Visual hint: color the border based on mode
  const colors = {default: '#e94560', approve_edits: '#e94560', read_only: '#f0a500', auto: '#4ecdc4'};
  sel.style.borderColor = colors[permissionMode] || '#e94560';
}

let nicknameMap = {};      // { realName: displayName } — agent display names
let pendingFiles = [];  // [{file, dataUrl, base64, mime_type, filename}]
let lastSSEActivity = 0;  // timestamp of last SSE event received
let serverMsgCount = 0;    // last known message_count from server (for poll delta)
let pollTimer = null;      // 30s fallback poll interval
let resourcesTimer = null; // 10s resources panel refresh
let displayWindow = 50;          // messages per page
let currentOffset = 0;           // how many older messages already loaded
let hasMoreMessages = false;     // server says there are older messages
let loadingMore = false;         // prevent concurrent load-more

// ── Message history (arrow key navigation) ──
let messageHistory = JSON.parse(localStorage.getItem('pawflow_msg_history') || '[]');
let historyIndex = -1;    // -1 = not navigating, 0 = most recent
let savedDraft = '';      // text being typed before navigating


// ── Keep-alive: ping every 4 min to renew sliding session ──
// Note: cookie is HttpOnly so getToken() returns null — use conversationId as auth indicator
setInterval(() => {
  fetch(API, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action: 'ping' }),
    credentials: 'same-origin',
  }).catch(() => {});
}, 4 * 60 * 1000);

// Auth
function getToken() {
  const m = document.cookie.match(/(?:^|;\s*)pawflow_token=([^;]+)/);
  return m ? m[1] : null;
}
function getAuthHeaders() {
  const token = getToken();
  const h = { 'Content-Type': 'application/json' };
  if (token) h['Authorization'] = 'Bearer ' + token;
  return h;
}
// Page is behind validateSessionAuth, so if we're here, we're logged in
if (LOGIN_URL) {
  document.getElementById('logoutBtn').style.display = '';
}
function doLogout() {
  if (eventSource) { eventSource.close(); eventSource = null; }
  fetch(window.location.origin + '/auth/logout', { method: 'POST', credentials: 'same-origin' })
    .finally(() => { window.location.href = LOGIN_URL || '/auth/login'; });
}

function _syncToggleBtn() {
  const sb = document.getElementById('sidebar');
  const btn = document.getElementById('sidebarToggle');
  if (sb && btn) btn.style.left = sb.classList.contains('collapsed') ? '12px' : '268px';
}
function toggleSidebar() {
  document.getElementById('sidebar').classList.toggle('collapsed');
  _syncToggleBtn();
}



function _setInputEnabled(enabled) {
  var inp = document.getElementById('input');
  var btn = document.getElementById('sendBtn');
  if (inp) {
    inp.disabled = !enabled;
    inp.placeholder = enabled
      ? 'Type a message... (Enter to send, Shift+Enter for newline)'
      : 'Create a conversation first (click + Nouveau)';
    inp.style.opacity = enabled ? '1' : '0.4';
  }
  if (btn) { btn.disabled = !enabled; btn.style.opacity = enabled ? '1' : '0.4'; }
}

function _doNewChat() {
  if (eventSource) { eventSource.close(); eventSource = null; }
  stopPollTimer();
  conversationId = null;
  pendingAgent = null;
  selectedAgent = '';
  updateActiveAgentBadge();
  serverMsgCount = 0;
  clearAllStreams();
  sending = false;
  document.getElementById('sendBtn').disabled = false;
  _expectingClear = true;
  document.getElementById('messages').innerHTML = '';
  _expectingClear = false;
  addMsg('system', t('newConv'));
  document.getElementById('status').textContent = t('ready');
  document.getElementById('filesPanel').style.display = 'none';
  document.getElementById('schedsPanel').style.display = 'none';
  document.getElementById('plansPanel').style.display = 'none';
  permissionMode = 'default';
  updatePermissionBadge();
  highlightConv(null);
  // Close sidebar on mobile
  document.getElementById('sidebar').classList.add('collapsed');
  _syncToggleBtn();
  document.getElementById('input').focus();
}

async function newChat() {
  var result = await _pickAgentsForNewConv();
  if (!result || !result.agents || result.agents.length === 0) return;
  // Don't close SSE yet — we need it to receive the create_conversation result
  var params = { agents: result.agents };
  if (result.title) params.title = result.title;
  if (result.relays && result.relays.length) params.relays = result.relays;
  if (result.default_relay) params.default_relay = result.default_relay;
  action$('create_conversation', params).subscribe(data => {
    console.log('[newChat] create_conversation result:', JSON.stringify(data));
    if (data.conversation_id) {
      // Now switch to the new conversation
      _doNewChat();
      conversationId = data.conversation_id;
      _setInputEnabled(true);
      connectSSE(conversationId, () => {
        console.log('[newChat] SSE ready, loading conversations...');
        loadConversations();
        highlightConv(conversationId);
        loadResources();
        loadPermissionMode();
      });
    } else {
      addMsg('error', data.error || 'Failed to create conversation');
    }
  });
}

// Fetch agents + relays, then show the new conversation dialog.
// Returns {agents: [...], relays: [...], default_relay: "...", title: "..."} or null.
async function _pickAgentsForNewConv() {
  return new Promise((resolve) => {
    // Fetch agents and relays in parallel
    var agents = [], relays = [];
    var done = 0;
    function check() {
      if (++done < 2) return;
      if (agents.length === 0) { resolve(null); return; }
      _showNewConvDialog(agents, relays, resolve);
    }
    action$('list_repo_agents', { conversation_id: '' }).subscribe(d => {
      agents = d.agents || [];
      check();
    });
    action$('relay_list_available').subscribe(d => {
      relays = (d.relays || []).filter(r => r.connected);
      check();
    });
  });
}

function _showNewConvDialog(repoAgents, availableRelays, resolve) {
  var overlay = document.createElement('div');
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.5);z-index:9999;display:flex;align-items:center;justify-content:center';

  var box = document.createElement('div');
  box.style.cssText = 'background:var(--bg2,#1e1e2e);border:1px solid var(--border,#444);border-radius:8px;padding:20px;min-width:580px;max-width:700px;max-height:85vh;display:flex;flex-direction:column;gap:14px;overflow-y:auto';

  var _css = 'style="width:100%;min-height:80px;max-height:192px;overflow-y:auto;border:1px solid var(--border,#444);border-radius:4px;padding:4px;background:var(--bg,#141420);"';
  var _itemCss = 'style="padding:3px 6px;cursor:pointer;border-radius:3px;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;"';
  var _btnCss = 'style="padding:4px 10px;border:1px solid var(--border,#444);border-radius:4px;background:var(--bg2,#1e1e2e);color:inherit;cursor:pointer;font-size:16px;font-weight:600;"';

  box.innerHTML =
    '<div style="font-weight:600;font-size:1.1em;">New Conversation</div>'
    // Title
    + '<div><label style="font-size:11px;color:#888;">Title (optional)</label>'
    + '<input id="_ncTitle" type="text" placeholder="Auto-generated if empty" style="width:100%;padding:6px 10px;border-radius:5px;border:1px solid var(--border,#444);background:var(--bg,#141420);color:inherit;font-size:0.95em;box-sizing:border-box;"></div>'
    // Agents
    + '<div style="font-size:12px;font-weight:600;color:#6c5ce7;">Agents</div>'
    + '<div style="display:flex;gap:8px;align-items:stretch;">'
    +   '<div style="flex:1;"><div style="font-size:10px;color:#888;margin-bottom:2px;">Available</div><div id="_ncAgentsAvail" ' + _css + '></div></div>'
    +   '<div style="display:flex;flex-direction:column;justify-content:center;gap:4px;">'
    +     '<button id="_ncAgentAdd" ' + _btnCss + ' title="Add">\u25B6</button>'
    +     '<button id="_ncAgentRem" ' + _btnCss + ' title="Remove">\u25C0</button>'
    +   '</div>'
    +   '<div style="flex:1;"><div style="font-size:10px;color:#888;margin-bottom:2px;">Selected</div><div id="_ncAgentsSel" ' + _css + '></div></div>'
    + '</div>'
    // Relays
    + '<div style="font-size:12px;font-weight:600;color:#6c5ce7;">Relays</div>'
    + '<div style="display:flex;gap:8px;align-items:stretch;">'
    +   '<div style="flex:1;"><div style="font-size:10px;color:#888;margin-bottom:2px;">Available</div><div id="_ncRelaysAvail" ' + _css + '></div></div>'
    +   '<div style="display:flex;flex-direction:column;justify-content:center;gap:4px;">'
    +     '<button id="_ncRelayAdd" ' + _btnCss + ' title="Link">\u25B6</button>'
    +     '<button id="_ncRelayRem" ' + _btnCss + ' title="Unlink">\u25C0</button>'
    +   '</div>'
    +   '<div style="flex:1;"><div style="font-size:10px;color:#888;margin-bottom:2px;">Linked <span style="font-size:9px;color:#4ecdc4;">\u2605 = default</span></div><div id="_ncRelaysSel" ' + _css + '></div></div>'
    + '</div>'
    // Buttons
    + '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:4px;">'
    +   '<button id="_ncCancelBtn" style="padding:6px 14px;border-radius:5px;border:1px solid var(--border,#444);background:transparent;color:inherit;cursor:pointer;">Cancel</button>'
    +   '<button id="_ncCreateBtn" style="padding:6px 14px;border-radius:5px;border:none;background:var(--accent,#7c6af7);color:#fff;cursor:pointer;font-weight:600;opacity:0.4;" disabled>Create</button>'
    + '</div>';

  overlay.appendChild(box);
  document.body.appendChild(overlay);

  // State
  var selAgents = [], selRelays = [], defaultRelay = '';

  function _makeItem(text, id, extra) {
    var d = document.createElement('div');
    d.textContent = text;
    d.dataset.id = id;
    if (extra) d.title = extra;
    d.style.cssText = 'padding:3px 6px;cursor:pointer;border-radius:3px;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;';
    d.onmouseenter = function() { d.style.background = 'rgba(124,106,247,0.15)'; };
    d.onmouseleave = function() { if (!d.classList.contains('_sel')) d.style.background = ''; };
    d.onclick = function() {
      d.parentNode.querySelectorAll('div').forEach(function(x) { x.classList.remove('_sel'); x.style.background = ''; });
      d.classList.add('_sel'); d.style.background = 'rgba(124,106,247,0.3)';
    };
    return d;
  }

  function _renderAgents() {
    var avail = document.getElementById('_ncAgentsAvail');
    var sel = document.getElementById('_ncAgentsSel');
    avail.innerHTML = ''; sel.innerHTML = '';
    repoAgents.forEach(function(a) {
      if (selAgents.indexOf(a.name) >= 0) return;
      var label = a.name + (a.description ? ' \u2014 ' + a.description : '');
      avail.appendChild(_makeItem(label, a.name));
    });
    selAgents.forEach(function(name) {
      sel.appendChild(_makeItem(name, name));
    });
    // Enable/disable create button
    document.getElementById('_ncCreateBtn').disabled = selAgents.length === 0;
    document.getElementById('_ncCreateBtn').style.opacity = selAgents.length === 0 ? '0.4' : '1';
  }

  function _renderRelays() {
    var avail = document.getElementById('_ncRelaysAvail');
    var sel = document.getElementById('_ncRelaysSel');
    avail.innerHTML = ''; sel.innerHTML = '';
    availableRelays.forEach(function(r) {
      if (selRelays.indexOf(r.relay_id) >= 0) return;
      var label = r.relay_id + (r.host_root ? ' (' + r.host_root + ')' : r.root ? ' (' + r.root + ')' : '');
      avail.appendChild(_makeItem(label, r.relay_id));
    });
    selRelays.forEach(function(rid) {
      var d = _makeItem(rid, rid);
      var isDefault = rid === defaultRelay;
      var radio = document.createElement('span');
      radio.innerHTML = isDefault ? '\u2605' : '\u2606';
      radio.style.cssText = 'cursor:pointer;color:' + (isDefault ? '#4ecdc4' : '#555') + ';margin-right:4px;font-size:14px;';
      radio.title = 'Set as default';
      radio.onclick = function(e) { e.stopPropagation(); defaultRelay = rid; _renderRelays(); };
      d.insertBefore(radio, d.firstChild);
      sel.appendChild(d);
    });
  }

  _renderAgents();
  _renderRelays();

  // Arrow buttons
  document.getElementById('_ncAgentAdd').onclick = function() {
    var s = document.querySelector('#_ncAgentsAvail ._sel');
    if (s) { selAgents.push(s.dataset.id); _renderAgents(); }
  };
  document.getElementById('_ncAgentRem').onclick = function() {
    var s = document.querySelector('#_ncAgentsSel ._sel');
    if (s) { selAgents = selAgents.filter(function(x) { return x !== s.dataset.id; }); _renderAgents(); }
  };
  document.getElementById('_ncRelayAdd').onclick = function() {
    var s = document.querySelector('#_ncRelaysAvail ._sel');
    if (s) {
      selRelays.push(s.dataset.id);
      if (selRelays.length === 1) defaultRelay = s.dataset.id;
      _renderRelays();
    }
  };
  document.getElementById('_ncRelayRem').onclick = function() {
    var s = document.querySelector('#_ncRelaysSel ._sel');
    if (s) {
      selRelays = selRelays.filter(function(x) { return x !== s.dataset.id; });
      if (defaultRelay === s.dataset.id) defaultRelay = selRelays[0] || '';
      _renderRelays();
    }
  };

  // Double-click to transfer
  document.getElementById('_ncAgentsAvail').ondblclick = function(e) {
    var t = e.target.closest('[data-id]');
    if (t) { selAgents.push(t.dataset.id); _renderAgents(); }
  };
  document.getElementById('_ncAgentsSel').ondblclick = function(e) {
    var t = e.target.closest('[data-id]');
    if (t) { selAgents = selAgents.filter(function(x) { return x !== t.dataset.id; }); _renderAgents(); }
  };
  document.getElementById('_ncRelaysAvail').ondblclick = function(e) {
    var t = e.target.closest('[data-id]');
    if (t) { selRelays.push(t.dataset.id); if (selRelays.length === 1) defaultRelay = t.dataset.id; _renderRelays(); }
  };
  document.getElementById('_ncRelaysSel').ondblclick = function(e) {
    var t = e.target.closest('[data-id]');
    if (t) { selRelays = selRelays.filter(function(x) { return x !== t.dataset.id; }); if (defaultRelay === t.dataset.id) defaultRelay = selRelays[0] || ''; _renderRelays(); }
  };

  var cleanup = function(val) { overlay.remove(); resolve(val); };
  document.getElementById('_ncCancelBtn').onclick = function() { cleanup(null); };
  overlay.addEventListener('click', function(e) { if (e.target === overlay) cleanup(null); });
  document.getElementById('_ncCreateBtn').onclick = function() {
    if (selAgents.length === 0) return;
    cleanup({
      agents: selAgents,
      relays: selRelays,
      default_relay: defaultRelay,
      title: (document.getElementById('_ncTitle').value || '').trim(),
    });
  };
}

function updateDeleteBtn() {
  const show = conversationId ? '' : 'none';
  document.getElementById('permissionMode').style.display = show;
  document.getElementById('actionMenuWrap').style.display = show;
}
// ── Reply-to state ──
let _replyTo = null;  // {raw_index, role, agent, text_preview}

function setReplyTo(btn) {
  const msgEl = btn.closest('.msg');
  if (!msgEl) return;
  const rawIndex = parseInt(msgEl.dataset.rawIndex || '-1');
  const rawText = msgEl.dataset.rawText || '';
  const isUser = msgEl.classList.contains('user');
  const badge = msgEl.querySelector('.source-badge');
  const agent = badge ? badge.textContent.trim() : (isUser ? 'User' : 'assistant');
  _replyTo = { raw_index: rawIndex, role: isUser ? 'user' : 'assistant', agent, text_preview: rawText.substring(0, 200) };
  // Show reply bar
  let bar = document.getElementById('replyBar');
  if (!bar) {
    bar = document.createElement('div');
    bar.id = 'replyBar';
    bar.style.cssText = 'background:#1a1a2e;border-top:1px solid #333;padding:4px 12px;display:flex;align-items:center;gap:8px;font-size:11px;color:#8888aa;';
    document.querySelector('.input-area').parentNode.insertBefore(bar, document.querySelector('.input-area'));
  }
  bar.innerHTML = '\u21A9 <span style="color:#6c5ce7">' + escapeHtml(agent) + '</span>: "'
    + escapeHtml(rawText.substring(0, 80)) + '..."'
    + '<span onclick="cancelReply()" style="cursor:pointer;margin-left:auto;color:#e94560;font-size:14px">\u2715</span>';
  bar.style.display = 'flex';
  document.getElementById('input').focus();
}

function cancelReply() {
  _replyTo = null;
  const bar = document.getElementById('replyBar');
  if (bar) bar.style.display = 'none';
}

function scrollToMessage(rawIndex) {
  const msgs = document.querySelectorAll('.msg[data-raw-index]');
  for (const m of msgs) {
    if (parseInt(m.dataset.rawIndex) === rawIndex) {
      m.scrollIntoView({ behavior: 'smooth', block: 'center' });
      m.style.outline = '2px solid #6c5ce7';
      setTimeout(() => { m.style.outline = ''; }, 2000);
      return;
    }
  }
}

// ── Debug: detect unexpected message removal ──
let _expectingClear = false;
const _msgObserver = new MutationObserver((mutations) => {
  if (_expectingClear) return;
  for (const m of mutations) {
    for (const node of m.removedNodes) {
      if (node.nodeType === 1 && node.classList && node.classList.contains('msg')) {
        const role = node.className.replace('msg ', '');
        const text = (node.dataset.rawText || node.textContent || '').substring(0, 80);
        console.warn('[MSG REMOVED]', role, text);
        console.trace('[MSG REMOVED STACK]');
      }
    }
  }
});

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
  var params = { agents: result.agents };
  if (result.agent_configs) params.agent_configs = result.agent_configs;
  if (result.title) params.title = result.title;
  if (result.relays && result.relays.length) params.relays = result.relays;
  if (result.default_relay) params.default_relay = result.default_relay;
  action$('create_conversation', params).subscribe(data => {
    if (data.conversation_id) {
      _doNewChat();
      conversationId = data.conversation_id;
      _setInputEnabled(true);
      connectSSE(conversationId, () => {
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

async function _pickAgentsForNewConv() {
  return new Promise((resolve) => {
    var agents = [], llmServices = [], relays = [];
    var done = 0;
    function check() {
      if (++done < 2) return;
      if (agents.length === 0) { resolve(null); return; }
      _showNewConvDialog(agents, llmServices, relays, resolve);
    }
    action$('list_repo_agents', { conversation_id: '' }).subscribe(d => {
      agents = d.agents || [];
      llmServices = d.llm_services || [];
      check();
    });
    action$('relay_list_available').subscribe(d => {
      relays = (d.relays || []).filter(r => r.connected);
      check();
    });
  });
}

function _showNewConvDialog(repoAgents, llmServices, availableRelays, resolve) {
  var overlay = document.createElement('div');
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.5);z-index:9999;display:flex;align-items:center;justify-content:center';
  var box = document.createElement('div');
  box.style.cssText = 'background:var(--bg2,#1e1e2e);border:1px solid var(--border,#444);border-radius:8px;padding:20px;min-width:640px;max-width:780px;max-height:85vh;display:flex;flex-direction:column;gap:12px;overflow-y:auto';

  // Build LLM service options HTML
  var svcOpts = llmServices.map(function(s) {
    return '<option value="' + escapeHtml(s.service_id) + '">' + escapeHtml(s.service_id) + (s.description ? ' \u2014 ' + escapeHtml(s.description) : '') + '</option>';
  }).join('');

  var _listCss = 'width:100%;min-height:100px;max-height:240px;overflow-y:auto;border:1px solid var(--border,#444);border-radius:4px;padding:4px;background:var(--bg,#141420);';
  var _relCss = 'width:100%;min-height:60px;max-height:120px;overflow-y:auto;border:1px solid var(--border,#444);border-radius:4px;padding:4px;background:var(--bg,#141420);';
  var _btnCss = 'padding:4px 10px;border:1px solid var(--border,#444);border-radius:4px;background:var(--bg2,#1e1e2e);color:inherit;cursor:pointer;font-size:16px;font-weight:600;';

  box.innerHTML =
    '<div style="font-weight:600;font-size:1.1em;">New Conversation</div>'
    + '<div><label style="font-size:11px;color:#888;">Title (optional)</label>'
    + '<input id="_ncTitle" type="text" placeholder="Auto-generated if empty" style="width:100%;padding:6px 10px;border-radius:5px;border:1px solid var(--border,#444);background:var(--bg,#141420);color:inherit;font-size:0.95em;box-sizing:border-box;"></div>'
    // Agent selection: left = treeview checkboxes, right = detail panel
    + '<div style="font-size:12px;font-weight:600;color:#6c5ce7;">Agents</div>'
    + '<div style="display:flex;gap:12px;align-items:stretch;">'
    +   '<div id="_ncAgentTree" style="' + _listCss + 'flex:1;"></div>'
    +   '<div id="_ncAgentDetail" style="flex:1;border:1px solid var(--border,#444);border-radius:4px;padding:10px;background:var(--bg,#141420);min-height:100px;max-height:240px;overflow-y:auto;font-size:12px;color:#aaa;display:flex;align-items:center;justify-content:center;">Select an agent to see details</div>'
    + '</div>'
    // Relays
    + '<div style="font-size:12px;font-weight:600;color:#6c5ce7;">Relays</div>'
    + '<div style="display:flex;gap:8px;align-items:stretch;">'
    +   '<div style="flex:1;"><div style="font-size:10px;color:#888;margin-bottom:2px;">Available</div><div id="_ncRelaysAvail" style="' + _relCss + '"></div></div>'
    +   '<div style="display:flex;flex-direction:column;justify-content:center;gap:4px;">'
    +     '<button id="_ncRelayAdd" style="' + _btnCss + '" title="Link">\u25B6</button>'
    +     '<button id="_ncRelayRem" style="' + _btnCss + '" title="Unlink">\u25C0</button>'
    +   '</div>'
    +   '<div style="flex:1;"><div style="font-size:10px;color:#888;margin-bottom:2px;">Linked <span style="font-size:9px;color:#4ecdc4;">\u2605 = default</span></div><div id="_ncRelaysSel" style="' + _relCss + '"></div></div>'
    + '</div>'
    // Buttons
    + '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:4px;">'
    +   '<button id="_ncCancelBtn" style="padding:6px 14px;border-radius:5px;border:1px solid var(--border,#444);background:transparent;color:inherit;cursor:pointer;">Cancel</button>'
    +   '<button id="_ncCreateBtn" style="padding:6px 14px;border-radius:5px;border:none;background:var(--accent,#7c6af7);color:#fff;cursor:pointer;font-weight:600;opacity:0.4;" disabled>Create</button>'
    + '</div>';

  overlay.appendChild(box);
  document.body.appendChild(overlay);

  // State: agent configs keyed by name
  var agentConfigs = {};  // {name: {llm_service: "..."}}
  var checkedAgents = new Set();
  var focusedAgent = '';
  var selRelays = [], defaultRelay = '';

  // Guess LLM service for an agent: try {name}_llm_service, else first service
  function _guessLlm(agentName) {
    var candidate = agentName + '_llm_service';
    for (var i = 0; i < llmServices.length; i++) {
      if (llmServices[i].service_id === candidate) return candidate;
    }
    // Try {name}_llm
    candidate = agentName + '_llm';
    for (var i = 0; i < llmServices.length; i++) {
      if (llmServices[i].service_id === candidate) return candidate;
    }
    return llmServices.length ? llmServices[0].service_id : '';
  }

  function _renderTree() {
    var tree = document.getElementById('_ncAgentTree');
    tree.innerHTML = '';
    // Group by scope
    var scopes = {};
    repoAgents.forEach(function(a) {
      var s = a.scope || 'global';
      if (!scopes[s]) scopes[s] = [];
      scopes[s].push(a);
    });
    var scopeOrder = ['global', 'user'];
    var scopeLabels = { global: '\uD83C\uDF10 Global', user: '\uD83D\uDC64 User' };
    scopeOrder.forEach(function(scope) {
      var items = scopes[scope];
      if (!items || !items.length) return;
      // Scope header
      var hdr = document.createElement('div');
      hdr.style.cssText = 'font-size:10px;color:#666;padding:2px 4px;margin-top:4px;';
      hdr.textContent = scopeLabels[scope] || scope;
      tree.appendChild(hdr);
      // Agent rows
      items.forEach(function(a) {
        var row = document.createElement('label');
        row.style.cssText = 'display:flex;align-items:center;gap:6px;padding:4px 6px;border-radius:4px;cursor:pointer;font-size:12px;';
        row.dataset.agent = a.name;
        var cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.checked = checkedAgents.has(a.name);
        cb.style.cssText = 'accent-color:#7c6af7;margin:0;';
        cb.onchange = function() {
          if (cb.checked) {
            checkedAgents.add(a.name);
            if (!agentConfigs[a.name]) agentConfigs[a.name] = { llm_service: _guessLlm(a.name) };
          } else {
            checkedAgents.delete(a.name);
          }
          _updateCreateBtn();
          if (focusedAgent === a.name) _renderDetail();
        };
        var label = document.createElement('span');
        label.style.cssText = 'flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;';
        label.textContent = a.name;
        if (a.description) label.title = a.description;
        row.appendChild(cb);
        row.appendChild(label);
        // Click row (not checkbox) to focus
        row.onclick = function(e) {
          if (e.target === cb) return;
          focusedAgent = a.name;
          _highlightFocused();
          _renderDetail();
        };
        tree.appendChild(row);
      });
    });
  }

  function _highlightFocused() {
    var tree = document.getElementById('_ncAgentTree');
    tree.querySelectorAll('label').forEach(function(row) {
      row.style.background = row.dataset.agent === focusedAgent ? 'rgba(124,106,247,0.15)' : '';
    });
  }

  function _renderDetail() {
    var panel = document.getElementById('_ncAgentDetail');
    if (!focusedAgent) {
      panel.innerHTML = '<span style="color:#666;">Select an agent to see details</span>';
      panel.style.display = 'flex'; panel.style.alignItems = 'center'; panel.style.justifyContent = 'center';
      return;
    }
    panel.style.display = 'block'; panel.style.alignItems = ''; panel.style.justifyContent = '';
    var agent = repoAgents.find(function(a) { return a.name === focusedAgent; });
    if (!agent) return;
    var isChecked = checkedAgents.has(focusedAgent);
    var cfg = agentConfigs[focusedAgent] || { llm_service: _guessLlm(focusedAgent) };
    var html = '<div style="font-weight:600;font-size:13px;color:#fff;margin-bottom:6px;">' + escapeHtml(agent.name) + '</div>';
    if (agent.description) {
      html += '<div style="color:#aaa;margin-bottom:8px;font-size:11px;">' + escapeHtml(agent.description) + '</div>';
    }
    html += '<div style="margin-bottom:4px;"><label style="font-size:10px;color:#888;">LLM Service</label>';
    html += '<select id="_ncLlmSelect" style="width:100%;padding:4px 6px;border-radius:4px;border:1px solid var(--border,#444);background:var(--bg2,#1e1e2e);color:inherit;font-size:12px;"' + (isChecked ? '' : ' disabled') + '>';
    html += svcOpts;
    html += '</select></div>';
    panel.innerHTML = html;
    // Set selected value
    var sel = document.getElementById('_ncLlmSelect');
    if (sel) {
      sel.value = cfg.llm_service || '';
      sel.onchange = function() {
        if (!agentConfigs[focusedAgent]) agentConfigs[focusedAgent] = {};
        agentConfigs[focusedAgent].llm_service = sel.value;
      };
    }
  }

  function _updateCreateBtn() {
    var btn = document.getElementById('_ncCreateBtn');
    btn.disabled = checkedAgents.size === 0;
    btn.style.opacity = checkedAgents.size === 0 ? '0.4' : '1';
  }

  function _makeRelayItem(text, id) {
    var d = document.createElement('div');
    d.textContent = text; d.dataset.id = id;
    d.style.cssText = 'padding:3px 6px;cursor:pointer;border-radius:3px;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;';
    d.onmouseenter = function() { d.style.background = 'rgba(124,106,247,0.15)'; };
    d.onmouseleave = function() { if (!d.classList.contains('_sel')) d.style.background = ''; };
    d.onclick = function() {
      d.parentNode.querySelectorAll('div').forEach(function(x) { x.classList.remove('_sel'); x.style.background = ''; });
      d.classList.add('_sel'); d.style.background = 'rgba(124,106,247,0.3)';
    };
    return d;
  }

  function _renderRelays() {
    var avail = document.getElementById('_ncRelaysAvail');
    var sel = document.getElementById('_ncRelaysSel');
    avail.innerHTML = ''; sel.innerHTML = '';
    availableRelays.forEach(function(r) {
      if (selRelays.indexOf(r.relay_id) >= 0) return;
      var label = r.relay_id + (r.host_root ? ' (' + r.host_root + ')' : r.root ? ' (' + r.root + ')' : '');
      avail.appendChild(_makeRelayItem(label, r.relay_id));
    });
    selRelays.forEach(function(rid) {
      var d = _makeRelayItem(rid, rid);
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

  _renderTree();
  _renderRelays();

  // Relay arrow buttons
  document.getElementById('_ncRelayAdd').onclick = function() {
    var s = document.querySelector('#_ncRelaysAvail ._sel');
    if (s) { selRelays.push(s.dataset.id); if (selRelays.length === 1) defaultRelay = s.dataset.id; _renderRelays(); }
  };
  document.getElementById('_ncRelayRem').onclick = function() {
    var s = document.querySelector('#_ncRelaysSel ._sel');
    if (s) { selRelays = selRelays.filter(function(x) { return x !== s.dataset.id; }); if (defaultRelay === s.dataset.id) defaultRelay = selRelays[0] || ''; _renderRelays(); }
  };
  document.getElementById('_ncRelaysAvail').ondblclick = function(e) {
    var t = e.target.closest('[data-id]'); if (t) { selRelays.push(t.dataset.id); if (selRelays.length === 1) defaultRelay = t.dataset.id; _renderRelays(); }
  };
  document.getElementById('_ncRelaysSel').ondblclick = function(e) {
    var t = e.target.closest('[data-id]'); if (t) { selRelays = selRelays.filter(function(x) { return x !== t.dataset.id; }); if (defaultRelay === t.dataset.id) defaultRelay = selRelays[0] || ''; _renderRelays(); }
  };

  var cleanup = function(val) { overlay.remove(); resolve(val); };
  document.getElementById('_ncCancelBtn').onclick = function() { cleanup(null); };
  overlay.addEventListener('click', function(e) { if (e.target === overlay) cleanup(null); });
  document.getElementById('_ncCreateBtn').onclick = function() {
    if (checkedAgents.size === 0) return;
    var agents = Array.from(checkedAgents);
    var configs = {};
    agents.forEach(function(name) {
      configs[name] = agentConfigs[name] || { llm_service: _guessLlm(name) };
    });
    cleanup({
      agents: agents,
      agent_configs: configs,
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

// ── Global app state ──
// These are shared across all JS modules via the global scope.
const _seenMsgIds = new Set();  // dedup msg_ids across SSE + poll + replay
const _selectedMsgIds = new Set();  // multiselect for batch delete
let conversationId = null;
let sending = false;
let contextOpInProgress = false;  // true while rebuild/resume/compact/restart_from is running
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

function toggleSidebar() {
  const sb = document.getElementById('sidebar');
  sb.classList.toggle('collapsed');
  const btn = document.getElementById('sidebarToggle');
  btn.style.left = sb.classList.contains('collapsed') ? '12px' : '268px';
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
  document.getElementById('input').focus();
}

async function newChat() {
  // Show agent picker to pre-assign agents to the new conversation
  const agents = await _pickAgentsForNewConv();
  if (!agents || agents.length === 0) return;  // cancelled
  _doNewChat();
  action$('create_conversation', { agents: agents }).subscribe(data => {
    if (data.conversation_id) {
      conversationId = data.conversation_id;
      connectSSE(conversationId);
      loadResources();
      loadPermissionMode();
      loadConversations();
      highlightConv(conversationId);
    } else {
      addMsg('error', data.error || 'Failed to create conversation');
    }
  });
}

// Show a dialog to pick 1+ agents from repo for a new conversation.
// Returns array of agent names, or null if skipped/cancelled.
async function _pickAgentsForNewConv() {
  return new Promise((resolve) => {
    action$('list_repo_agents', { conversation_id: '' }).subscribe(d => {
      const repoAgents = d.agents || [];
      if (repoAgents.length === 0) { resolve(null); return; }
      _showAgentPickerDialog(repoAgents, resolve);
    });
  });
}
function _showAgentPickerDialog(repoAgents, resolve) {

    const overlay = document.createElement('div');
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.5);z-index:9999;display:flex;align-items:center;justify-content:center';

    const box = document.createElement('div');
    box.style.cssText = 'background:var(--bg2,#1e1e2e);border:1px solid var(--border,#444);border-radius:8px;padding:20px;min-width:320px;max-width:480px;max-height:70vh;display:flex;flex-direction:column;gap:12px';

    const listHtml = repoAgents.map(a => {
      const desc = a.description ? ` — ${a.description}` : '';
      const scope = a.scope ? `<span style="font-size:0.8em;opacity:0.5;margin-left:4px">${a.scope}</span>` : '';
      return `<label style="display:flex;align-items:center;gap:8px;cursor:pointer;padding:4px 6px;border-radius:4px" data-name="${a.name.toLowerCase()}"><input type="checkbox" value="${a.name}" style="accent-color:var(--accent,#7c6af7)"><span><strong>${a.name}</strong>${desc}${scope}</span></label>`;
    }).join('');

    box.innerHTML = `
      <div style="font-weight:600;font-size:1.05em">Choose agents for this conversation</div>
      <input id="_ncAgentSearch" type="text" placeholder="Search agents..." style="padding:6px 10px;border-radius:5px;border:1px solid var(--border,#444);background:var(--bg,#141420);color:inherit;font-size:0.95em">
      <div id="_ncAgentList" style="overflow-y:auto;max-height:300px;display:flex;flex-direction:column;gap:6px">${listHtml}</div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:4px">
        <button id="_ncSkipBtn" style="padding:6px 14px;border-radius:5px;border:1px solid var(--border,#444);background:transparent;color:inherit;cursor:pointer">Skip</button>
        <button id="_ncOkBtn" style="padding:6px 14px;border-radius:5px;border:none;background:var(--accent,#7c6af7);color:#fff;cursor:pointer;font-weight:600">Start conversation</button>
      </div>`;

    overlay.appendChild(box);
    document.body.appendChild(overlay);

    document.getElementById('_ncAgentSearch').addEventListener('input', function() {
      const q = this.value.toLowerCase();
      document.querySelectorAll('#_ncAgentList label').forEach(lbl => {
        lbl.style.display = !q || lbl.dataset.name.includes(q) ? '' : 'none';
      });
    });

    const cleanup = (val) => { overlay.remove(); resolve(val); };
    document.getElementById('_ncSkipBtn').onclick = () => cleanup(null);
    document.getElementById('_ncOkBtn').onclick = () => {
      const checked = [...document.querySelectorAll('#_ncAgentList input[type=checkbox]:checked')].map(c => c.value);
      cleanup(checked.length > 0 ? checked : null);
    };
    overlay.addEventListener('click', e => { if (e.target === overlay) cleanup(null); });
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

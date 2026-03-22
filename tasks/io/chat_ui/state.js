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

// ── Watchdog: if sending and no SSE activity for 15s, try recovery ──
setInterval(() => {
  if (!sending || !conversationId) return;
  const now = Date.now();
  if (lastSSEActivity > 0 && (now - lastSSEActivity) > 15000) {
    console.log('[watchdog] no SSE activity for 15s while sending — recovering');
    lastSSEActivity = now;  // reset to avoid re-triggering immediately
    _recoverConversation(conversationId);
  }
}, 5000);

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
  document.getElementById('sidebar').classList.toggle('open');
}

function newChat() {
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
  document.getElementById('deleteConvBtn').style.display = 'none';
  document.getElementById('exportConvBtn').style.display = 'none';
  document.getElementById('contextBtn').style.display = 'none';
  document.getElementById('memoryBtn').style.display = '';
  document.getElementById('filesBtn').style.display = 'none';
  document.getElementById('filesPanel').style.display = 'none';
  document.getElementById('schedsBtn').style.display = 'none';
  document.getElementById('schedsPanel').style.display = 'none';
  highlightConv(null);
  // Close sidebar on mobile
  document.getElementById('sidebar').classList.remove('open');
  document.getElementById('input').focus();
}

function updateDeleteBtn() {
  const show = conversationId ? '' : 'none';
  document.getElementById('deleteConvBtn').style.display = show;
  document.getElementById('exportConvBtn').style.display = show;
  document.getElementById('contextBtn').style.display = show;
  document.getElementById('memoryBtn').style.display = '';
  document.getElementById('refreshConvBtn').style.display = show;
  document.getElementById('filesBtn').style.display = show;
  document.getElementById('schedsBtn').style.display = show;
}
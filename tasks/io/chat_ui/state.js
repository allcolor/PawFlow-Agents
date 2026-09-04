// ── Global app state ──
// These are shared across all JS modules via the global scope.
let _seenMsgIds = new Set();  // dedup msg_ids across SSE + replay, per conversation session
let _liveCountedMsgIds = new Set();  // msg_ids already counted into currentOffset from SSE
let _selectedMsgIds = new Set();  // multiselect for batch delete, per conversation session
let conversationId = null;
let sending = false;

// Keep the action dock beside the prompt and the active-agents box inside its
// header popover without duplicating their long-lived IDs or handlers. Both
// nodes are declared in their legacy source positions so extensions can still
// discover them during HTML assembly, then mounted once the DOM exists.
function mountComposerChrome() {
  const actionMount = document.getElementById('composerActionMount');
  const actionDock = document.getElementById('actionMenuWrap');
  const activePop = document.getElementById('activeAgentsPop');
  const activePanel = document.getElementById('activePanel');
  if (actionMount && actionDock && actionDock.parentNode !== actionMount) {
    actionMount.appendChild(actionDock);
  }
  if (activePop && activePanel && activePanel.parentNode !== activePop) {
    activePop.appendChild(activePanel);
  }
}

mountComposerChrome();

// Canonical HTML escaper. Defined here (loads early, before any module that
// renders user/agent-controlled text) so there is a single source of truth.
function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function pawflowDebugEnabled(topic) {
  try {
    if (window.PAWFLOW_DEBUG_UI === true) return true;
    if (topic === 'technical' && window.DEBUG_TECHNICAL_GROUPING === true) return true;
    const stored = window.localStorage && window.localStorage.getItem('pawflow.debug');
    return stored === '1' || stored === 'true' || stored === 'ui';
  } catch (_) {
    return false;
  }
}

function pawflowDebugLog() {
  if (!pawflowDebugEnabled()) return;
  console.debug.apply(console, arguments);
}

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
function _resourceWritableScopes() {
  const scopes = _isAdmin() ? ['global', 'user', 'conversation'] : ['user', 'conversation'];
  return scopes;
}
function _resourceScopeOptions() {
  const labels = { global: t('global'), user: t('user'), conversation: t('conversation') };
  return _resourceWritableScopes()
    .map(scope => '<option value="' + scope + '">' + labels[scope] + '</option>')
    .join('');
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

function getStream(agent) {
  const key = (agent || '').toLowerCase();
  if (!streams[key]) streams[key] = { el: null, text: '', chunks: [] };
  return streams[key];
}
function clearStream(agent) {
  const key = (agent || '').toLowerCase();
  delete streams[key];
}
function clearAllStreams() {
  for (const a of Object.keys(streams)) {
    const s = streams[a];
    for (const c of s.chunks) { if (c && c.parentNode) c.remove(); }
  }
  streams = {};
}
function clearAllStreamsKeepDOM() {
  streams = {};
}
let permissionMode = 'default';  // current tool permission mode
const PERMISSION_MODE_UI = {
  default: { label: 'permissionDefault', icon: '\u{1F512}' },
  approve_edits: { label: 'permissionApproveEdits', icon: '\u270F' },
  read_only: { label: 'permissionReadOnly', icon: '\u{1F4D6}' },
  auto: { label: 'permissionAuto', icon: '\u26A1' },
};

function setPermissionMode(mode) {
  closePermissionModeMenu();
  permissionMode = mode;
  fireAction('set_permission_mode', { conversation_id: conversationId, mode });
  updatePermissionBadge();
  if (window._pawflowExtRuntime) {
    window._pawflowExtRuntime.fireHook('permission_mode_changed', { mode: mode });
  }
}

function loadPermissionMode() {
  if (!conversationId) { updatePermissionBadge(); return; }
  action$('get_permission_mode', { conversation_id: conversationId })
    .subscribe(d => {
      permissionMode = d.permission_mode || 'default';
      updatePermissionBadge();
    });
}

function updatePermissionBadge() {
  const wrap = document.getElementById('permissionModeWrap');
  const button = document.getElementById('permissionModeBtn');
  const icon = document.getElementById('permissionModeIcon');
  if (!wrap || !button || !icon) return;
  wrap.style.display = conversationId ? 'inline-flex' : 'none';
  const meta = PERMISSION_MODE_UI[permissionMode] || PERMISSION_MODE_UI.default;
  const modeLabel = t(meta.label);
  const accessibleLabel = t('permissionModeTitle') + ' — ' + modeLabel;
  icon.textContent = meta.icon;
  button.title = accessibleLabel;
  button.setAttribute('aria-label', accessibleLabel);
  document.querySelectorAll('#permissionModeMenu [data-permission-mode]').forEach(item => {
    item.setAttribute('aria-checked', item.dataset.permissionMode === permissionMode ? 'true' : 'false');
  });
  if (!conversationId) closePermissionModeMenu();
}

function togglePermissionModeMenu(force) {
  const menu = document.getElementById('permissionModeMenu');
  const button = document.getElementById('permissionModeBtn');
  if (!menu || !button || !conversationId) return;
  const open = typeof force === 'boolean' ? force : menu.hidden;
  menu.hidden = !open;
  button.setAttribute('aria-expanded', open ? 'true' : 'false');
}

function closePermissionModeMenu() {
  const menu = document.getElementById('permissionModeMenu');
  const button = document.getElementById('permissionModeBtn');
  if (menu) menu.hidden = true;
  if (button) button.setAttribute('aria-expanded', 'false');
}

document.addEventListener('click', event => {
  const wrap = document.getElementById('permissionModeWrap');
  const menu = document.getElementById('permissionModeMenu');
  if (wrap && menu && !menu.hidden && !wrap.contains(event.target)) closePermissionModeMenu();
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape') closePermissionModeMenu();
});

let nicknameMap = {};      // { realName: displayName } — agent display names
let pendingFiles = [];  // [{file, dataUrl, base64, mime_type, filename}]
let lastSSEActivity = 0;  // timestamp of last SSE event received
let serverMsgCount = 0;    // last known message_count from server
let sseHealthTimer = null; // SSE health reconnect interval
let resourcesTimer = null; // 10s resources panel refresh
let displayWindow = 50;          // messages per page
let currentOffset = 0;           // how many older messages already loaded
let historyCursor = { offset: 0, before_msg_id: '' }; // backend-issued transcript cursor
let hasMoreMessages = false;     // server says there are older messages
let loadingMore = false;         // prevent concurrent load-more

// ── Message history (arrow key navigation) ──
let messageHistory = [];
try {
  messageHistory = JSON.parse(localStorage.getItem('pawflow_msg_history') || '[]');
} catch (_e) {
  // Corrupt/old-schema storage must not kill the whole UI at load (this
  // module defines escapeHtml and other singletons downstream modules need).
  messageHistory = [];
}
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
  document.getElementById('linkAccountBtn').style.display = '';
  const activeUser = String((window.PAWFLOW_EXTENSION_CONTEXT || {}).user || '').trim();
  const userInfo = document.getElementById('userInfo');
  userInfo.textContent = activeUser;
  userInfo.title = activeUser;
  userInfo.style.display = activeUser ? '' : 'none';
  document.getElementById('logoutBtn').style.display = '';
}
function beginOAuthAccountLink() {
  if (!confirm('You will be signed out, then asked to sign in with the account to link. Continue?')) return;
  if (typeof closeAllConversationSessions === 'function') closeAllConversationSessions();
  else if (eventSource) { eventSource.close(); eventSource = null; }
  const _uiUrl = API.replace(/\/api\/agent$/, '/api/ui');
  fetch(_uiUrl, {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify({ action: 'begin_oauth_account_link' }),
    credentials: 'same-origin',
  }).then(resp => resp.json()).then(data => {
    if (data && data.error) { addMsg('error', data.error); return; }
    window.location.href = (data && data.login_url) || '/auth/login';
  }).catch(err => addMsg('error', err.message || 'Failed to start account linking'));
}
function showLinkedAccountsDialog() {
  if (typeof closeActionMenu === 'function') closeActionMenu();
  const previous = document.getElementById('linkedAccountsDialog');
  if (previous) previous.remove();

  const overlay = document.createElement('div');
  overlay.id = 'linkedAccountsDialog';
  overlay.className = 'dialog-bg';
  const dialog = document.createElement('div');
  dialog.className = 'dialog';
  dialog.setAttribute('role', 'dialog');
  dialog.setAttribute('aria-modal', 'true');
  const title = document.createElement('div');
  title.className = 'dialog-title';
  title.textContent = t('linkedAccounts');
  // The authenticated principal is no longer shown on the header icon: it
  // appears here and in the icon's hover tooltip.
  const me = String((window.PAWFLOW_EXTENSION_CONTEXT || {}).user || '').trim();
  const meLine = document.createElement('div');
  meLine.className = 'linked-account-me';
  meLine.textContent = me;
  meLine.style.display = me ? '' : 'none';
  const body = document.createElement('div');
  body.className = 'dialog-body';
  const actions = document.createElement('div');
  actions.className = 'dialog-actions';
  const link = document.createElement('button');
  link.className = 'btn btn-primary';
  link.type = 'button';
  link.textContent = t('linkAccount');
  link.onclick = () => { overlay.remove(); beginOAuthAccountLink(); };
  const close = document.createElement('button');
  close.className = 'btn';
  close.type = 'button';
  close.textContent = t('close');
  close.onclick = () => overlay.remove();
  actions.append(link, close);
  dialog.append(title, meLine, body, actions);
  overlay.appendChild(dialog);
  overlay.onclick = (event) => { if (event.target === overlay) overlay.remove(); };
  document.body.appendChild(overlay);

  function loadLinks() {
    body.textContent = t('loading');
    action$('list_linked_accounts', { conversation_id: conversationId || '' }).subscribe(data => {
      body.replaceChildren();
      if (data.error) {
        body.textContent = data.error;
        return;
      }
      const links = data.links || {};
      const providers = Object.keys(links).sort();
      if (!providers.length) {
        body.textContent = t('noLinkedAccounts');
        return;
      }
      body.classList.add('linked-account-list');
      providers.forEach(provider => {
        const row = document.createElement('div');
        row.className = 'linked-account-row';
        const identity = document.createElement('div');
        identity.className = 'linked-account-identity';
        const providerLabel = document.createElement('div');
        providerLabel.className = 'linked-account-provider';
        providerLabel.textContent = provider;
        const identityValue = document.createElement('div');
        identityValue.className = 'linked-account-value';
        identityValue.textContent = String(links[provider]);
        identity.append(providerLabel, identityValue);
        const unlink = document.createElement('button');
        unlink.className = 'btn linked-account-unlink';
        unlink.type = 'button';
        unlink.textContent = t('unlink');
        unlink.setAttribute('aria-label', t('unlink') + ' ' + provider + ' ' + links[provider]);
        unlink.onclick = () => {
          unlink.disabled = true;
          action$('unlink_account', { provider: provider }).subscribe(result => {
            if (result.error) {
              unlink.disabled = false;
              addMsg('error', result.error);
              return;
            }
            loadLinks();
          });
        };
        row.append(identity, unlink);
        body.appendChild(row);
      });
    });
  }
  loadLinks();
}
function doLogout() {
  if (typeof closeAllConversationSessions === 'function') closeAllConversationSessions();
  else if (eventSource) { eventSource.close(); eventSource = null; }
  fetch(window.location.origin + '/auth/logout', { method: 'POST', credentials: 'same-origin' })
    .finally(() => { window.location.href = '/'; });
}

function _syncToggleBtn(collapsedOverride) {
  const sb = document.getElementById('sidebar');
  const btn = document.getElementById('sidebarToggle');
  if (!sb || !btn) return;
  const collapsed = collapsedOverride === undefined
    ? sb.classList.contains('collapsed') : !!collapsedOverride;
  const tabBar = document.getElementById('tabBar');
  const narrow = window.matchMedia('(max-width: 768px)').matches;
  const tabBarWidth = narrow && tabBar ? tabBar.offsetWidth : 0;
  // Desktop owns an independent edge-hover rail. On narrow layouts the rail
  // remains coupled to the overlay drawer so the two layers cannot compete.
  if (tabBar) tabBar.classList.toggle('collapsed', narrow && collapsed);
  const boundary = narrow && !collapsed ? Math.max(0, tabBarWidth - 8) : 0;
  btn.style.setProperty('--pf-sidebar-toggle-x', boundary + 'px');
  btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
}
let _sidebarMotionGeneration = 0;
let _sidebarTargetCollapsed = null;
const _SIDEBAR_RAIL_DURATION = 900;
const _SIDEBAR_RAIL_EASING = 'cubic-bezier(.4, 0, .2, 1)';
function _setSidebarCollapsed(collapsed, animate) {
  const sidebar = document.getElementById('sidebar');
  if (!sidebar) return Promise.resolve({status: 'missing'});
  const shell = document.getElementById('sidebarShell') || sidebar;
  collapsed = !!collapsed;
  _sidebarTargetCollapsed = collapsed;
  const generation = ++_sidebarMotionGeneration;
  const narrow = window.matchMedia('(max-width: 768px)').matches;
  const apply = function() {
    if (generation !== _sidebarMotionGeneration) return false;
    shell.classList.toggle('collapsed', collapsed);
    sidebar.classList.toggle('collapsed', collapsed);
    sidebar.setAttribute('aria-hidden', collapsed ? 'true' : 'false');
    if (collapsed) sidebar.setAttribute('inert', '');
    else sidebar.removeAttribute('inert');
    _syncToggleBtn(collapsed);
    return true;
  };

  if (animate === false || narrow || !window.pfMotion || window.pfMotion.reduced()) {
    if (window.pfMotion) window.pfMotion.cancel(shell, 'sidebar-rail');
    apply();
    return Promise.resolve({status: 'finished'});
  }
  const rect = shell.getBoundingClientRect();
  const startX = Number(rect.left || 0);
  const endX = collapsed ? -Number(rect.width || sidebar.offsetWidth || 260) : 0;
  apply();
  return window.pfMotion.replace(shell, 'sidebar-rail', [
    {transform: 'translateX(' + startX + 'px)'},
    {transform: 'translateX(' + endX + 'px)'},
  ], {
    duration: _SIDEBAR_RAIL_DURATION,
    easing: _SIDEBAR_RAIL_EASING,
    fill: 'both',
  }).then(function(result) {
    if (generation === _sidebarMotionGeneration && result && result.animation
        && typeof result.animation.cancel === 'function') result.animation.cancel();
    return result;
  });
}
function toggleSidebar() {
  const sidebar = document.getElementById('sidebar');
  if (!sidebar) return Promise.resolve({status: 'missing'});
  const shell = document.getElementById('sidebarShell') || sidebar;
  const current = _sidebarTargetCollapsed === null
    ? shell.classList.contains('collapsed') : _sidebarTargetCollapsed;
  return _setSidebarCollapsed(!current, true);
}
document.addEventListener('DOMContentLoaded', _syncToggleBtn);
window.addEventListener('resize', _syncToggleBtn);

const _CHROME_EXPANDER_DURATION = 500;
const _CHROME_EXPANDER_EASING = 'cubic-bezier(.4, 0, .2, 1)';
const _chromeExpanderStates = new WeakMap();

function _chromeExpanderNaturalHeight(element) {
  const style = window.getComputedStyle(element);
  const borders = (parseFloat(style.borderTopWidth) || 0)
    + (parseFloat(style.borderBottomWidth) || 0);
  return Math.max(0, Number(element.scrollHeight || 0) + borders);
}

function _setChromeExpanderOpen(
  element, owner, collapsedClass, open, channel, animate, fade
) {
  if (!element || !owner) return Promise.resolve({status: 'missing'});
  open = !!open;
  let state = _chromeExpanderStates.get(element);
  if (!state) {
    state = {generation: 0, targetOpen: !owner.classList.contains(collapsedClass)};
    _chromeExpanderStates.set(element, state);
  }
  state.targetOpen = open;
  const generation = ++state.generation;
  const rect = element.getBoundingClientRect();
  const startHeight = Number(rect.height || 0);
  const shouldFade = fade !== false;
  const computedOpacity = shouldFade
    ? Number(window.getComputedStyle(element).opacity) : 1;
  const startOpacity = shouldFade && startHeight > 0 && Number.isFinite(computedOpacity)
    ? computedOpacity : (shouldFade ? 0 : 1);

  if (window.pfMotion) window.pfMotion.cancel(element, channel);
  element.style.boxSizing = 'border-box';
  element.style.overflow = 'clip';
  element.style.height = startHeight + 'px';
  if (shouldFade) element.style.opacity = String(startOpacity);
  if (open) owner.classList.remove(collapsedClass);

  function terminal(result) {
    if (generation !== state.generation || open !== state.targetOpen) {
      return {status: 'stale'};
    }
    owner.classList.toggle(collapsedClass, !open);
    element.style.boxSizing = '';
    element.style.overflow = '';
    element.style.height = '';
    if (shouldFade) element.style.opacity = '';
    if (result && result.animation && typeof result.animation.cancel === 'function') {
      result.animation.cancel();
    }
    return {status: open ? 'open' : 'closed'};
  }

  if (animate !== true || !window.pfMotion || window.pfMotion.reduced()) {
    return Promise.resolve(terminal(null));
  }
  let endHeight = null;
  const measured = window.pfMotion.read(function() {
    if (generation !== state.generation || open !== state.targetOpen) return null;
    endHeight = open ? _chromeExpanderNaturalHeight(element) : 0;
    return endHeight;
  });
  const animated = window.pfMotion.write(function() {
    if (endHeight === null || generation !== state.generation || open !== state.targetOpen) {
      return {status: 'stale'};
    }
    const startFrame = {height: startHeight + 'px'};
    const endFrame = {height: endHeight + 'px'};
    if (shouldFade) {
      startFrame.opacity = startOpacity;
      endFrame.opacity = open ? 1 : 0;
    }
    return window.pfMotion.replace(element, channel, [startFrame, endFrame], {
      duration: _CHROME_EXPANDER_DURATION,
      easing: _CHROME_EXPANDER_EASING,
      fill: 'both',
    });
  });
  return Promise.all([measured, animated]).then(function(results) {
    return terminal(results[1]);
  });
}

// Composer drawer: the whole zone above the prompt (conversation controls +
// action dock) folds completely behind a small centered grip. CLOSED by
// default; the reader's choice persists across reloads.
const _COMPOSER_DRAWER_KEY = 'pawflow.composerDrawerOpen';
function _composerDrawerOpen() {
  try { return localStorage.getItem(_COMPOSER_DRAWER_KEY) === '1'; }
  catch (_) { return false; }
}
function _applyComposerDrawer(animate) {
  const area = document.querySelector('.input-area');
  if (!area) return Promise.resolve({status: 'missing'});
  const open = _composerDrawerOpen();
  const handle = document.getElementById('composerDrawerHandle');
  if (handle) handle.setAttribute('aria-expanded', open ? 'true' : 'false');
  return _setChromeExpanderOpen(
    area.querySelector('.composer-context-row'), area,
    'composer-drawer-collapsed', open, 'composer-drawer', animate === true);
}
function toggleComposerDrawer() {
  try {
    localStorage.setItem(_COMPOSER_DRAWER_KEY, _composerDrawerOpen() ? '0' : '1');
  } catch (_) {}
  return _applyComposerDrawer(true);
}
document.addEventListener('DOMContentLoaded', _applyComposerDrawer);

// Header bar: same fold-behind-a-grip pattern, but OPEN by default —
// folding it is the reader's explicit choice and persists.
const _HEADER_BAR_KEY = 'pawflow.headerBarOpen';
function _headerBarOpen() {
  try { return localStorage.getItem(_HEADER_BAR_KEY) !== '0'; }
  catch (_) { return true; }
}
function _applyHeaderBar(animate) {
  const bar = document.getElementById('headerBar');
  const shell = bar && bar.closest('.header-shell');
  if (!bar || !shell) return Promise.resolve({status: 'missing'});
  const open = _headerBarOpen();
  const grip = document.getElementById('headerGrip');
  if (grip) grip.setAttribute('aria-expanded', open ? 'true' : 'false');
  return _setChromeExpanderOpen(
    shell, bar, 'collapsed', open, 'header-bar', animate === true, false);
}
function toggleHeaderBar() {
  try {
    localStorage.setItem(_HEADER_BAR_KEY, _headerBarOpen() ? '0' : '1');
  } catch (_) {}
  return _applyHeaderBar(true);
}
document.addEventListener('DOMContentLoaded', _applyHeaderBar);
window.addEventListener('resize', _applyHeaderBar);

// Header popovers: an icon click shows the widget's full content in a
// tooltip-like popover; a second click hides it. Opening one closes the
// others so at most one popover is on screen.
function toggleHeaderPop(popId, btn) {
  const pop = document.getElementById(popId);
  if (!pop) return;
  const willOpen = !pop.classList.contains('open');
  document.querySelectorAll('.hdr-pop.open').forEach(p => {
    p.classList.remove('open');
    const b = p.parentElement && p.parentElement.querySelector('.hdr-icon-btn');
    if (b) b.setAttribute('aria-expanded', 'false');
  });
  if (willOpen) {
    pop.classList.add('open');
    if (btn) btn.setAttribute('aria-expanded', 'true');
  }
}



function _setInputEnabled(enabled) {
  var inp = document.getElementById('input');
  var btn = document.getElementById('sendBtn');
  if (inp) {
    inp.disabled = !enabled;
    inp.placeholder = enabled
      ? t('placeholder')
      : t('placeholderDisabled');
    inp.style.opacity = enabled ? '1' : '0.4';
  }
  if (btn) { btn.disabled = !enabled; btn.style.opacity = enabled ? '1' : '0.4'; }
}

async function newChat() {
  var result = await _pickAgentsForNewConv();
  if (!result || !result.agents || result.agents.length === 0) return;
  var params = { agents: result.agents };
  if (result.title) params.title = result.title;
  if (result.relays && result.relays.length) params.relays = result.relays;
  if (result.default_relay) params.default_relay = result.default_relay;
  action$('create_conversation', params).subscribe(data => {
    if (data.conversation_id) {
      // Unified path: refresh sidebar + route through resumeConv like switch/reload.
      // resumeConv handles clear + load_history(50) + render (0 messages for a fresh conv).
      loadConversations();
      resumeConv(data.conversation_id, true);
    } else {
      addMsg('error', data.error || t('failedToCreateConversation'));
    }
  });
}

async function _pickAgentsForNewConv() {
  return new Promise((resolve) => {
    var agents = [], llmServices = [], relays = [];
    var done = 0;
    function check() {
      if (++done < 3) return;
      if (agents.length === 0) { resolve(null); return; }
      _showNewConvDialog(agents, llmServices, relays, resolve);
    }
    action$('list_repo_agents', { conversation_id: '' }).subscribe(d => {
      agents = d.agents || [];
      check();
    });
    listServices$('llm').subscribe(d => {
      llmServices = (d.services || []).filter(s => s.enabled);
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
  box.style.cssText = 'background:var(--pf-panel,#1e1e2e);border:1px solid var(--pf-border,#444);border-radius:8px;padding:20px;min-width:min(640px, calc(100vw - 32px));max-width:min(780px, calc(100vw - 32px));max-height:85vh;display:flex;flex-direction:column;gap:12px;overflow-y:auto';

  // Build LLM service options HTML
  var svcOpts = llmServices.map(function(s) {
    return '<option value="' + escapeHtml(s.service_id) + '">' + escapeHtml(s.service_id) + (s.description ? ' \u2014 ' + escapeHtml(s.description) : '') + '</option>';
  }).join('');

  var _listCss = 'width:100%;min-height:100px;max-height:240px;overflow-y:auto;border:1px solid var(--pf-border,#444);border-radius:4px;padding:4px;background:var(--pf-bg,#141420);';
  var _relCss = 'width:100%;min-height:60px;max-height:120px;overflow-y:auto;border:1px solid var(--pf-border,#444);border-radius:4px;padding:4px;background:var(--pf-bg,#141420);';
  var _btnCss = 'padding:4px 10px;border:1px solid var(--pf-border,#444);border-radius:4px;background:var(--pf-panel,#1e1e2e);color:inherit;cursor:pointer;font-size:16px;font-weight:600;';

  box.innerHTML =
    '<div style="font-weight:600;font-size:1.1em;">' + escapeHtml(t('newConversation')) + '</div>'
    + '<div><label style="font-size:11px;color:var(--pf-muted,#888);">' + escapeHtml(t('titleOptional')) + '</label>'
    + '<input id="_ncTitle" type="text" placeholder="' + escapeHtml(t('autoGeneratedIfEmpty')) + '" style="width:100%;padding:6px 10px;border-radius:5px;border:1px solid var(--pf-border,#444);background:var(--pf-bg,#141420);color:inherit;font-size:0.95em;box-sizing:border-box;"></div>'
    // Agent selection: left = treeview checkboxes, right = detail panel
    + '<div style="font-size:12px;font-weight:600;color:var(--pf-accent,#6c5ce7);">' + escapeHtml(t('agents')) + '</div>'
    + '<div style="display:flex;gap:12px;align-items:stretch;">'
    +   '<div id="_ncAgentTree" style="' + _listCss + 'flex:1;"></div>'
    +   '<div id="_ncAgentDetail" style="flex:1;border:1px solid var(--pf-border,#444);border-radius:4px;padding:10px;background:var(--pf-bg,#141420);min-height:100px;max-height:240px;overflow-y:auto;font-size:12px;color:var(--pf-muted,#aaa);display:flex;align-items:center;justify-content:center;">' + escapeHtml(t('selectAgentDetails')) + '</div>'
    + '</div>'
    // Relays
    + '<div style="font-size:12px;font-weight:600;color:var(--pf-accent,#6c5ce7);">' + escapeHtml(t('relays')) + '</div>'
    + '<div style="display:flex;gap:8px;align-items:stretch;">'
    +   '<div style="flex:1;"><div style="font-size:10px;color:var(--pf-muted,#888);margin-bottom:2px;">' + escapeHtml(t('available')) + '</div><div id="_ncRelaysAvail" style="' + _relCss + '"></div></div>'
    +   '<div style="display:flex;flex-direction:column;justify-content:center;gap:4px;">'
    +     '<button id="_ncRelayAdd" style="' + _btnCss + '" title="' + escapeHtml(t('link')) + '">\u25B6</button>'
    +     '<button id="_ncRelayRem" style="' + _btnCss + '" title="' + escapeHtml(t('unlink')) + '">\u25C0</button>'
    +   '</div>'
    +   '<div style="flex:1;"><div style="font-size:10px;color:var(--pf-muted,#888);margin-bottom:2px;">' + escapeHtml(t('linkedRelaysDefaultHint')) + '</div><div id="_ncRelaysSel" style="' + _relCss + '"></div></div>'
    + '</div>'
    // Buttons
    + '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:4px;">'
    +   '<button id="_ncCancelBtn" style="padding:6px 14px;border-radius:5px;border:1px solid var(--pf-border,#444);background:transparent;color:inherit;cursor:pointer;">' + escapeHtml(t('contextCancel')) + '</button>'
    +   '<button id="_ncCreateBtn" style="padding:6px 14px;border-radius:5px;border:none;background:var(--pf-accent,#7c6af7);color:#fff;cursor:pointer;font-weight:600;opacity:0.4;" disabled>' + escapeHtml(t('create')) + '</button>'
    + '</div>';

  overlay.appendChild(box);
  document.body.appendChild(overlay);

  // State: agent instances keyed by instance_name
  // Each: {definition, llm_service, params: {key: val}}
  var agentInstances = {};  // {instance_name: {definition, llm_service, params}}
  var focusedDef = '';
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

  function _instanceCount() { return Object.keys(agentInstances).length; }

  function _renderTree() {
    var tree = document.getElementById('_ncAgentTree');
    tree.innerHTML = '';
    // Group definitions by scope
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
      var hdr = document.createElement('div');
      hdr.style.cssText = 'font-size:10px;color:var(--pf-muted,#666);padding:2px 4px;margin-top:4px;';
      hdr.textContent = scopeLabels[scope] || scope;
      tree.appendChild(hdr);
      items.forEach(function(a) {
        var row = document.createElement('div');
        row.style.cssText = 'display:flex;align-items:center;gap:6px;padding:4px 6px;border-radius:4px;cursor:pointer;font-size:12px;';
        row.dataset.def = a.name;
        var label = document.createElement('span');
        label.style.cssText = 'flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;';
        label.textContent = a.name;
        if (a.description) label.title = a.description;
        // Count instances from this definition
        var count = Object.values(agentInstances).filter(function(i) { return i.definition === a.name; }).length;
        var badge = document.createElement('span');
        badge.style.cssText = 'font-size:10px;color:var(--pf-accent,#6c5ce7);min-width:16px;text-align:center;';
        badge.textContent = count ? '(' + count + ')' : '';
        row.appendChild(label);
        row.appendChild(badge);
        row.onclick = function() {
          focusedDef = a.name;
          _highlightFocused();
          _renderDetail();
        };
        tree.appendChild(row);
      });
    });
  }

  function _highlightFocused() {
    var tree = document.getElementById('_ncAgentTree');
    tree.querySelectorAll('[data-def]').forEach(function(row) {
      row.style.background = row.dataset.def === focusedDef ? 'rgba(124,106,247,0.15)' : '';
    });
  }

  function _nextInstanceName(defName) {
    var base = defName;
    if (!agentInstances[base]) return base;
    var n = 2;
    while (agentInstances[base + '_' + n]) n++;
    return base + '_' + n;
  }

  function _renderDetail() {
    var panel = document.getElementById('_ncAgentDetail');
    if (!focusedDef) {
      panel.innerHTML = '<span style="color:var(--pf-muted,#666);">' + escapeHtml(t('selectDefinitionAddAgents')) + '</span>';
      panel.style.display = 'flex'; panel.style.alignItems = 'center'; panel.style.justifyContent = 'center';
      return;
    }
    panel.style.display = 'block'; panel.style.alignItems = ''; panel.style.justifyContent = '';
    var agent = repoAgents.find(function(a) { return a.name === focusedDef; });
    if (!agent) return;
    var paramSchema = agent.parameters || {};
    var paramKeys = Object.keys(paramSchema);

    var html = '<div style="font-weight:600;font-size:13px;color:#fff;margin-bottom:4px;">' + escapeHtml(agent.name) + '</div>';
    if (agent.description) {
      html += '<div style="color:var(--pf-muted,#aaa);margin-bottom:8px;font-size:11px;">' + escapeHtml(agent.description) + '</div>';
    }

    // Existing instances for this definition
    var defInstances = [];
    Object.keys(agentInstances).forEach(function(k) {
      if (agentInstances[k].definition === focusedDef) defInstances.push(k);
    });
    if (defInstances.length) {
      html += '<div style="margin-bottom:8px;">';
      defInstances.forEach(function(iname) {
        html += '<div style="display:flex;align-items:center;gap:4px;padding:3px 6px;background:rgba(124,106,247,0.1);border-radius:3px;margin-bottom:3px;font-size:11px;">';
        html += '<span style="flex:1;color:var(--pf-text,#e0e0e0);">' + escapeHtml(iname) + '</span>';
        html += '<span style="color:var(--pf-muted,#888);font-size:10px;">' + escapeHtml(agentInstances[iname].llm_service) + '</span>';
        html += '<span data-remove-inst="' + escapeHtml(iname) + '" style="cursor:pointer;color:#e94560;font-size:13px;" title="' + escapeHtml(t('remove')) + '">\u2715</span>';
        html += '</div>';
      });
      html += '</div>';
    }

    // Add-instance form
    html += '<div style="border-top:1px solid var(--pf-border,#444);padding-top:8px;margin-top:4px;">';
    html += '<div style="font-size:10px;color:var(--pf-accent,#6c5ce7);margin-bottom:6px;font-weight:600;">' + escapeHtml(t('addInstance')) + '</div>';
    html += '<div style="margin-bottom:6px;"><label style="font-size:10px;color:var(--pf-muted,#888);">' + escapeHtml(t('instanceNameRequired')) + '</label>';
    html += '<input id="_ncInstName" value="' + escapeHtml(_nextInstanceName(focusedDef)) + '" style="width:100%;padding:4px 6px;border-radius:4px;border:1px solid var(--pf-border,#444);background:var(--pf-panel,#1e1e2e);color:inherit;font-size:12px;box-sizing:border-box;"/></div>';
    html += '<div style="margin-bottom:6px;"><label style="font-size:10px;color:var(--pf-muted,#888);">' + escapeHtml(t('llmServiceRequired')) + '</label>';
    html += '<select id="_ncLlmSelect" style="width:100%;padding:4px 6px;border-radius:4px;border:1px solid var(--pf-border,#444);background:var(--pf-panel,#1e1e2e);color:inherit;font-size:12px;">' + svcOpts + '</select></div>';
    // Params — skip 'name' (always synced from instance_name)
    var visibleParamKeys = paramKeys.filter(function(k) { return k !== 'name'; });
    if (visibleParamKeys.length) {
      html += '<div style="margin-bottom:6px;"><div style="font-size:10px;color:var(--pf-muted,#888);margin-bottom:4px;">' + escapeHtml(t('parameters')) + '</div>';
      visibleParamKeys.forEach(function(k) {
        var spec = paramSchema[k] || {};
        var defVal = spec.default || '';
        html += '<div style="margin-bottom:4px;"><label style="font-size:10px;color:var(--pf-muted,#888);">' + escapeHtml(k + (spec.required ? ' *' : '')) + '</label>';
        html += '<input data-param="' + escapeHtml(k) + '" value="' + escapeHtml(String(defVal)) + '" style="width:100%;padding:4px 6px;border-radius:4px;border:1px solid var(--pf-border,#444);background:var(--pf-panel,#1e1e2e);color:inherit;font-size:12px;box-sizing:border-box;"/></div>';
      });
      html += '</div>';
    }
    html += '<button id="_ncAddInstBtn" style="width:100%;padding:5px;border-radius:4px;border:1px solid var(--pf-accent,#6c5ce7);background:transparent;color:var(--pf-accent,#6c5ce7);cursor:pointer;font-size:11px;font-weight:600;">+ ' + escapeHtml(t('addInstance')) + '</button>';
    html += '</div>';

    panel.innerHTML = html;

    // Set LLM select default
    var sel = document.getElementById('_ncLlmSelect');
    if (sel) sel.value = _guessLlm(focusedDef);

    // Remove instance buttons
    panel.querySelectorAll('[data-remove-inst]').forEach(function(btn) {
      btn.onclick = function() {
        delete agentInstances[btn.dataset.removeInst];
        _renderTree(); _highlightFocused(); _renderDetail(); _updateCreateBtn();
      };
    });

    // Add instance button
    document.getElementById('_ncAddInstBtn').onclick = function() {
      var iname = (document.getElementById('_ncInstName').value || '').trim();
      var llm = (document.getElementById('_ncLlmSelect') || {}).value || '';
      if (!iname) { alert(t('instanceNameRequiredMessage')); return; }
      if (agentInstances[iname]) { alert(t('instanceAlreadyExists', { name: iname })); return; }
      if (!llm) { alert(t('llmServiceRequiredMessage')); return; }
      var params = { name: iname };
      panel.querySelectorAll('[data-param]').forEach(function(inp) {
        params[inp.dataset.param] = inp.value;
      });
      agentInstances[iname] = { definition: focusedDef, llm_service: llm, params: params };
      _renderTree(); _highlightFocused(); _renderDetail(); _updateCreateBtn();
    };
  }

  function _updateCreateBtn() {
    var btn = document.getElementById('_ncCreateBtn');
    var count = _instanceCount();
    btn.disabled = count === 0;
    btn.style.opacity = count === 0 ? '0.4' : '1';
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
      radio.title = t('setDefaultRelay');
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

  document.getElementById('_ncCreateBtn').onclick = function() {
    if (_instanceCount() === 0) return;
    var agents = Object.keys(agentInstances).map(function(iname) {
      var inst = agentInstances[iname];
      return {
        instance_name: iname,
        definition: inst.definition,
        llm_service: inst.llm_service,
        params: inst.params || {},
      };
    });
    cleanup({
      agents: agents,
      relays: selRelays,
      default_relay: defaultRelay,
      title: (document.getElementById('_ncTitle').value || '').trim(),
    });
  };
}

function updateDeleteBtn() {
  const show = conversationId ? '' : 'none';
  const permissionControl = document.getElementById('permissionModeWrap');
  if (permissionControl) permissionControl.style.display = show ? 'inline-flex' : 'none';
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
        if (pawflowDebugEnabled('messages')) {
          console.debug('[MSG REMOVED]', role, text);
          console.trace('[MSG REMOVED STACK]');
        }
      }
    }
  }
});

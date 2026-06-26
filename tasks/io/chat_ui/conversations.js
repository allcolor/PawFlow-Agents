
// ── Conversation sidebar & history ──────────────────────────────
// All server calls use action$() from rxbus.js (fire-and-forget + SSE result).

window._convActiveOverride = window._convActiveOverride || {};

function loadConversations() {
  action$('list_conversations', {}).subscribe(data => {
    const convs = data.conversations || [];
    renderConvList(convs);
  });
}

function _convRuntimeStatus(cid, serverStatus) {
  if (cid === conversationId
      && window._convActiveOverride
      && Object.prototype.hasOwnProperty.call(window._convActiveOverride, cid)) {
    return window._convActiveOverride[cid] ? 'active' : 'idle';
  }
  return serverStatus || 'idle';
}

function setConversationWorking(cid, isWorking) {
  if (!cid) return;
  window._convActiveOverride[cid] = !!isWorking;
  const el = document.querySelector('.conv-item[data-cid="' + CSS.escape(cid) + '"]');
  if (!el) return;
  const preview = el.querySelector('.conv-preview');
  if (!preview) return;
  let dot = preview.querySelector('.conv-status');
  if (isWorking) {
    if (!dot) {
      dot = document.createElement('span');
      dot.className = 'conv-status active';
      dot.title = t('working');
      preview.insertBefore(dot, preview.firstChild);
    } else {
      dot.className = 'conv-status active';
      dot.title = t('working');
    }
  } else if (dot) {
    dot.remove();
  }
}

function renderConvList(convs) {
  const list = document.getElementById('convList');
  list.innerHTML = '';
  if (convs.length === 0) {
    list.innerHTML = '<div style="padding:20px;text-align:center;color:#6c6c8a;font-size:13px;">' + escapeHtml(t('noConversationsHint')) + '</div>';
    if (!conversationId) _setInputEnabled(false);
  }
  for (const c of convs) {
    const el = document.createElement('div');
    el.className = 'conv-item' + (c.conversation_id === conversationId ? ' active' : '');
    el.dataset.cid = c.conversation_id;
    const title = c.title || c.preview || t('newConversation');
    const date = new Date(c.updated_at * 1000);
    const timeStr = date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
    const runtimeStatus = _convRuntimeStatus(c.conversation_id, c.status);
    const statusDot = runtimeStatus === 'active' ? '<span class="conv-status active" title="' + escapeHtml(t('working')) + '"></span>'
      : runtimeStatus === 'blocked' ? '<span class="conv-status blocked" title="' + escapeHtml(t('blocked')) + '"></span>' : '';
    const branchBadge = c.branch ? '<span class="conv-branch" title="' + escapeHtml(t('branchTitle', { branch: c.branch })) + '">\u{1F33F} ' + escapeHtml(c.branch) + '</span>' : '';
    const encBadge = (c.encryption === 'locked' || c.encryption === 'unlocked')
      ? '<span class="conv-encrypt" title="' + escapeHtml(t(c.encryption)) + '">'
        + (c.encryption === 'locked' ? '\u{1F512}' : '\u{1F513}') + '</span>'
      : '';
    el.innerHTML = '<div class="conv-preview" ondblclick="renameConvInline(event,\'' + c.conversation_id + '\')">' 
      + statusDot + '<span class="conv-title">' + escapeHtml(title) + '</span>' + branchBadge + encBadge + '</div>'
      + '<div class="conv-meta">' + escapeHtml(t('contextMessages', { n: c.message_count })) + ' \u00b7 ' + timeStr + '</div>'
      + '<button class="conv-delete" title="' + escapeHtml(t('delete')) + '" onclick="deleteConv(event,\'' + c.conversation_id + '\')">\u00d7</button>';
    el.onclick = () => resumeConv(c.conversation_id);
    el.oncontextmenu = (function(cid, status) { return function(ev) { ev.preventDefault(); showConvMenu(ev, cid, status); }; })(c.conversation_id, runtimeStatus);
    list.appendChild(el);
  }
}

function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
function escapeAttr(s) { return escapeHtml(s); }
function jsStringArg(s) { return escapeAttr(JSON.stringify(String(s == null ? '' : s))); }


function highlightConv(cid) {
  document.querySelectorAll('.conv-item').forEach(el => {
    el.classList.toggle('active', el.dataset.cid === cid);
  });
}

function renameConvInline(e, cid) {
  e.stopPropagation();
  const previewEl = e.target.closest('.conv-preview');
  if (!previewEl) return;
  const currentTitle = previewEl.textContent.trim();
  const input = document.createElement('input');
  input.type = 'text';
  input.value = currentTitle;
  input.style.cssText = 'width:100%;background:#1a1a2e;color:#eee;border:1px solid #6c5ce7;border-radius:3px;padding:2px 4px;font-size:12px;';
  previewEl.innerHTML = '';
  previewEl.appendChild(input);
  input.focus();
  input.select();
  const finish = () => {
    const newTitle = input.value.trim();
    if (newTitle && newTitle !== currentTitle) {
      fireAction('set_conv_title', { conversation_id: cid, title: newTitle });
    }
    loadConversations();
  };
  input.onblur = finish;
  input.onkeydown = (ev) => { if (ev.key === 'Enter') finish(); if (ev.key === 'Escape') loadConversations(); };
}

// ─────────────────────────────────────────────────────────────────
// SINGLE canonical load path.
//
// User's mental model (and the only one implemented):
//     new | switch | reload  ->  1) clear webchat + state
//                                 2) load_history(cid, 50)  // [] if fresh
//                                 3) render
//
// resumeConv(cid) is THE entry point. Every caller goes through it:
// sidebar click, Refresh menu, post-delete switch, post-import,
// context-editor close, newChat post-create, attachments changes, etc.
// renderEmptyState() handles the "no conv selected" view (after deleting
// the last conv). No duplicate clear/load logic anywhere.
// ─────────────────────────────────────────────────────────────────

// Clears the webchat DOM and every conv-scoped global. Caller sets
// `conversationId` afterwards (to a cid or null).
function _clearConvState() {
  // Resource panel: reset all sections to initial state (only Agents
  // open). See resources.js — user-facing rule: switching conv must
  // always reopen the panel in the same predictable layout.
  if (typeof _resetCollapsedSectionsToInitial === 'function') {
    _resetCollapsedSectionsToInitial();
  }
  if (eventSource) { eventSource.close(); eventSource = null; }
  if (typeof sseReconnectTimer !== 'undefined' && sseReconnectTimer) {
    clearTimeout(sseReconnectTimer); sseReconnectTimer = null;
  }
  stopSSEHealthTimer();
  _expectingClear = true;
  document.getElementById('messages').innerHTML = '';
  _expectingClear = false;
  _seenMsgIds.clear();
  if (typeof _liveCountedMsgIds !== 'undefined' && _liveCountedMsgIds.clear) _liveCountedMsgIds.clear();
  if (typeof _selectedMsgIds !== 'undefined' && _selectedMsgIds.clear) _selectedMsgIds.clear();
  serverMsgCount = 0;
  _histTaskBlocks = {};
  clearAllStreams();
  sending = false;
  var _sendBtn = document.getElementById('sendBtn');
  if (_sendBtn) _sendBtn.disabled = false;
  var _stopBtn = document.getElementById('stopBtn');
  if (_stopBtn) _stopBtn.style.display = 'none';
  if (typeof window._sseClearLiveBlocks === 'function') window._sseClearLiveBlocks();
  if (typeof activeInteractions !== 'undefined') {
    for (const k of Object.keys(activeInteractions)) delete activeInteractions[k];
    if (typeof updateActivePanel === 'function') updateActivePanel();
  }
  if (typeof hideTyping === 'function') hideTyping();
  if (typeof _pendingImages !== 'undefined') {
    _pendingImages.length = 0;
    if (typeof _imageFlushTimer !== 'undefined' && _imageFlushTimer) {
      clearTimeout(_imageFlushTimer); _imageFlushTimer = null;
    }
  }
  pendingAgent = null;
  selectedAgent = '';
  if (typeof updateActiveAgentBadge === 'function') updateActiveAgentBadge();
  if (typeof nicknameMap !== 'undefined') nicknameMap = {};
  if (typeof _autoScroll !== 'undefined') _autoScroll = true;
  sseEverConnected = false;
  sseHadError = false;
  document.getElementById('sidebar').classList.add('collapsed');
  if (typeof _syncToggleBtn === 'function') _syncToggleBtn();
}

const VIEW_TOGGLES = {
  technical: {
    paramKey: 'chat.group_technical_messages',
    itemId: 'viewItemTechnical',
    flag: 'PAWFLOW_GROUP_TECHNICAL_MESSAGES',
    setter: 'setTechnicalMessageGrouping',
  },
  task: {
    paramKey: 'chat.group_task_messages',
    itemId: 'viewItemTask',
    flag: 'PAWFLOW_GROUP_TASK_MESSAGES',
    setter: 'setTaskMessageGrouping',
  },
  delegate: {
    paramKey: 'chat.group_delegate_messages',
    itemId: 'viewItemDelegate',
    flag: 'PAWFLOW_GROUP_DELEGATE_MESSAGES',
    setter: 'setDelegateMessageGrouping',
  },
};

function updateViewMenuItem(kind, enabled) {
  const cfg = VIEW_TOGGLES[kind];
  if (!cfg) return;
  const item = document.getElementById(cfg.itemId);
  if (!item) return;
  const active = !!enabled;
  item.classList.toggle('active', active);
  item.setAttribute('aria-checked', active ? 'true' : 'false');
}

function updateViewMenuVisibility() {
  const wrap = document.getElementById('viewMenuWrap');
  if (!wrap) return;
  wrap.style.display = conversationId ? 'inline-flex' : 'none';
  if (!conversationId) closeViewMenu();
}

function toggleViewMenu() {
  const menu = document.getElementById('viewMenu');
  const btn = document.getElementById('viewMenuToggle');
  if (!menu) return;
  const willOpen = !menu.classList.contains('open');
  menu.classList.toggle('open', willOpen);
  if (btn) btn.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
  if (willOpen) {
    setTimeout(() => document.addEventListener('click', _viewMenuOutsideClick, { capture: true }), 0);
  } else {
    document.removeEventListener('click', _viewMenuOutsideClick, { capture: true });
  }
}

function closeViewMenu() {
  const menu = document.getElementById('viewMenu');
  const btn = document.getElementById('viewMenuToggle');
  if (menu) menu.classList.remove('open');
  if (btn) btn.setAttribute('aria-expanded', 'false');
  document.removeEventListener('click', _viewMenuOutsideClick, { capture: true });
}

function _viewMenuOutsideClick(ev) {
  const wrap = document.getElementById('viewMenuWrap');
  if (wrap && !wrap.contains(ev.target)) closeViewMenu();
}

function onViewGroupingToggle(kind) {
  if (!conversationId) return;
  const cfg = VIEW_TOGGLES[kind];
  if (!cfg) return;
  const item = document.getElementById(cfg.itemId);
  const next = !window[cfg.flag];
  if (item) item.classList.add('disabled');
  action$('set_param', {
    conversation_id: conversationId,
    scope: 'conversation',
    key: cfg.paramKey,
    value: next ? 'true' : 'false',
  }).subscribe({
    next: data => {
      if (data && data.error) {
        addMsg('error', data.error);
        return;
      }
      const setter = typeof window[cfg.setter] === 'function' ? window[cfg.setter] : null;
      if (setter) setter(next);
      else window[cfg.flag] = next;
      updateViewMenuItem(kind, next);
      resumeConv(conversationId, true);
    },
    error: e => addMsg('error', t('failedToUpdateTechnicalGrouping', { error: e.message })),
    complete: () => { if (item) item.classList.remove('disabled'); },
  });
}

// Kept for backwards-compatible callers (e.g. resetConv, _renderHistory).
function updateTechnicalGroupingToggle(enabled) {
  updateViewMenuVisibility();
  updateViewMenuItem('technical', enabled);
}

// THE single canonical "load this conv" path. ONE function, period.
//
// What "loading a conv" means (user's mental model, verbatim):
//   1. CLEAR webchat  -> chat is completely empty
//   2. LOAD histo(50) -> we have at most 50 messages in hand
//   3. DISPLAY them   -> rendered on screen
//   4. THEN open SSE  -> live stream of FUTURE events only
//
// Order matters. SSE is opened LAST, after the history is on screen.
// If SSE were opened first, live events arriving during the load_history
// RTT would populate _seenMsgIds and addMsg() would dedup legitimate
// history rows out of the render (transcript truncation). With load-
// first, _seenMsgIds is seeded by the rendered history, so any later
// SSE event whose msg_id matches an already-rendered message is
// correctly deduped, and brand-new msg_ids render fresh.
function resumeConv(cid, force) {
  if (!cid) { renderEmptyState(); return; }
  if (cid === conversationId && !force) return;
  document.getElementById('status').textContent = t('loading');

  var _prevCid = conversationId;
  // 1. CLEAR -- DOM empty, every conv-scoped global reset, SSE closed.
  _clearConvState();
  conversationId = cid;
  _setInputEnabled(true);
  highlightConv(cid);
  updateDeleteBtn();
  if (window._pawflowExtRuntime) {
    window._pawflowExtRuntime.fireHook('conversation_changed', {
      oldCid: _prevCid || null, newCid: cid, force: !!force,
    });
  }

  // Visible loading state in the (now empty) message area. load_history
  // results arrive via the /api/ui task slot + UI-action SSE bus; if that
  // slot is busy (e.g. first post-restart request while the backend loads
  // services / spawns the relay), the response can take tens of seconds.
  // Without this the chat is a silent blank during that wait.
  _showConvLoadingPlaceholder();

  // 2. LOAD histo(50). No SSE is open yet: nothing can pollute
  //    _seenMsgIds before render.
  action$('load_history', { conversation_id: cid, limit: displayWindow, offset: 0 })
    .subscribe(data => {
      // Stale guard: a late response from a prior switch (rapid A->B
      // click) must not render into the current conv's DOM.
      if (cid !== conversationId) return;
      // 3. DISPLAY.
      _renderHistory(data);
      // 4. Hydrate context gauges from current agent contexts, then open SSE
      //    for live future events. noReplay=true: we just refetched the
      //    authoritative transcript from disk; buffered bus events would be
      //    duplicates of what we already rendered.
      if (typeof hydrateContextUsage === 'function') hydrateContextUsage();
      connectSSE(cid, () => startSSEHealthTimer(), { noReplay: true });
    });
}

function refreshCurrentConversation() {
  if (!conversationId) return;
  resumeConv(conversationId, true);
  const input = document.getElementById('input');
  if (input) input.focus();
}

// Empty-state view: no conv selected. Reached after deleting the last
// conv or when create_conversation fails. Single place owning this UI.
function renderEmptyState() {
  _clearConvState();
  conversationId = null;
  addMsg('system', t('newConv'));
  document.getElementById('status').textContent = t('ready');
  var fp = document.getElementById('filesPanel'); if (fp) fp.style.display = 'none';
  var sp = document.getElementById('schedsPanel'); if (sp) sp.style.display = 'none';
  var pp = document.getElementById('plansPanel'); if (pp) pp.style.display = 'none';
  if (typeof permissionMode !== 'undefined') permissionMode = 'default';
  if (typeof updatePermissionBadge === 'function') updatePermissionBadge();
  updateTechnicalGroupingToggle(false);
  highlightConv(null);
  _setInputEnabled(false);
  // Re-render the resource panel for the no-conversation state (scope-
  // independent sections only) instead of leaving the deleted conv's content.
  if (typeof loadResources === 'function') loadResources();
  var inp = document.getElementById('input'); if (inp) inp.focus();
}

// Shared across render + loadMore so task blocks persist
let _histTaskBlocks = {};

function _getHistTaskBlock(taskId, iteration, agentName) {
  if (!window.PAWFLOW_GROUP_TASK_MESSAGES) return null;
  const iter = Number(iteration || 0) || 0;
  const blockKey = taskId + '::iter' + iter;
  if (_histTaskBlocks[blockKey]) return _histTaskBlocks[blockKey];
  const details = document.createElement('details');
  details.className = 'msg task-block';
  details.style.cssText = 'margin:6px 0;border:1px solid #333;border-radius:8px;padding:0;background:#1a1a2e;';
  const summary = document.createElement('summary');
  summary.style.cssText = 'cursor:pointer;padding:8px 12px;font-size:12px;color:#6c5ce7;user-select:none;font-weight:600;display:flex;align-items:center;gap:6px;';
  const iterLabel = iter > 1 ? ' iter ' + iter : '';
  summary.innerHTML = '\u{1F4CB} Task <span style="color:#e0e0e0;font-weight:normal">' + escapeHtml(taskId) + '</span>'
    + (agentName ? ' <span style="color:#888;font-weight:normal">(' + escapeHtml(displayAgentName(agentName)) + iterLabel + ')</span>' : '')
    + ' <span style="margin-left:auto;font-size:11px;color:#888">\u2714 done</span>';
  details.appendChild(summary);
  const content = document.createElement('div');
  content.style.cssText = 'padding:4px 12px 8px;max-height:60vh;overflow-y:auto;';
  details.appendChild(content);
  document.getElementById('messages').appendChild(details);
  _histTaskBlocks[blockKey] = {el: details, content: content};
  return _histTaskBlocks[blockKey];
}

// Transient "loading conversation" row shown in #messages between the
// clear and the load_history render. Cleared by _renderHistory on any
// outcome. Idempotent.
function _showConvLoadingPlaceholder() {
  var box = document.getElementById('messages');
  if (!box) return;
  if (document.getElementById('convLoadingPlaceholder')) return;
  var el = document.createElement('div');
  el.id = 'convLoadingPlaceholder';
  el.style.cssText = 'display:flex;align-items:center;justify-content:center;gap:8px;'
    + 'padding:40px 16px;color:var(--pf-muted);font-size:13px;';
  el.innerHTML = '<span class="pf-spin" style="width:14px;height:14px;border:2px solid var(--pf-border);'
    + 'border-top-color:var(--pf-accent);border-radius:50%;display:inline-block;'
    + 'animation:spin 0.8s linear infinite;"></span>'
    + '<span>' + escapeHtml(t('loading')) + '</span>';
  box.appendChild(el);
}

function _clearConvLoadingPlaceholder() {
  var el = document.getElementById('convLoadingPlaceholder');
  if (el) el.remove();
}

function _renderHistory(data) {
  _clearConvLoadingPlaceholder();
  if (!data || data.error) {
    addMsg('error', (data && data.error) || t('loadError'));
    document.getElementById('status').textContent = t('error');
    return;
  }
  // Stale guard: a late load_history response from a prior switch must
  // NOT render into the current conv's DOM. Without this, a rapid A->B
  // click leaves the slow load_history(A) response rendering A's
  // messages into B's view.
  if (data.conversation_id && data.conversation_id !== conversationId) {
    return;
  }
  // Encrypted-and-locked: the server withholds ciphertext rows and flags this.
  // Show an unlock banner instead of history; compose stays usable but writes
  // are refused server-side until unlocked.
  if (data.encrypted_locked) {
    const box = document.getElementById('messages');
    if (box) {
      box.innerHTML = '<div class="enc-locked-banner" style="margin:24px;padding:16px;'
        + 'border:1px solid #6c5ce7;border-radius:8px;text-align:center;color:#c8c8e0;">'
        + '\u{1F512} ' + escapeHtml(t('lockedBannerText'))
        + '<br><button onclick="encryptUnlockCurrent()" style="margin-top:12px;padding:6px 16px;'
        + 'background:#6c5ce7;color:#fff;border:none;border-radius:6px;cursor:pointer;">'
        + escapeHtml(t('unlock')) + '</button></div>';
    }
    return;
  }
  const groupTechnicalMessages = !!data.group_technical_messages;
  const groupTaskMessages = data.group_task_messages === undefined ? true : !!data.group_task_messages;
  const groupDelegateMessages = data.group_delegate_messages === undefined ? true : !!data.group_delegate_messages;
  if (typeof setTechnicalMessageGrouping === 'function') {
    setTechnicalMessageGrouping(groupTechnicalMessages);
  }
  if (typeof setTaskMessageGrouping === 'function') setTaskMessageGrouping(groupTaskMessages);
  if (typeof setDelegateMessageGrouping === 'function') setDelegateMessageGrouping(groupDelegateMessages);
  updateViewMenuVisibility();
  updateViewMenuItem('technical', groupTechnicalMessages);
  updateViewMenuItem('task', groupTaskMessages);
  updateViewMenuItem('delegate', groupDelegateMessages);
  _histTaskBlocks = {};  // reset on full render
  nicknameMap = data.nicknames || {};
  if (typeof suspendTechnicalMessageGrouping === 'function') suspendTechnicalMessageGrouping();
  try {
    for (const m of (data.messages || [])) {
      let content = m.content || '';
      if ((m.type === 'assistant' || m.role === 'assistant') && typeof content === 'string') {
        content = content.replace(/^\[[^\]]+\]:\s*/, '');
      }
      const el = addMsg(m.type || m.role, content, m);
      // task_id can be top-level (SSE) or in source (stored messages)
      // Use task_iteration to create separate blocks per iteration.
      // Delegate traces are their own top-level block — never wrap them
      // in a generic task-block (delegate is not a task).
      const _isDelegateTrace = (m.type === 'sub_agent_trace' || m.role === 'sub_agent_trace');
      const _taskId = _isDelegateTrace ? '' : (m.task_id || (m.source && m.source.task_id) || '');
      if (_taskId && el) {
        const agentName = (m.source && m.source.name) || '';
        const _iter = m.task_iteration || (m.source && m.source.task_iteration) || 0;
        const tb = _getHistTaskBlock(_taskId, _iter, agentName);
        if (tb) tb.content.appendChild(el);
      }
    }
  } finally {
    if (typeof resumeTechnicalMessageGrouping === 'function') resumeTechnicalMessageGrouping(false);
  }
  if (typeof applyTechnicalMessageGrouping === 'function') applyTechnicalMessageGrouping();
  serverMsgCount = data.message_count || 0;
  currentOffset = data.raw_count || (data.messages || []).length;
  hasMoreMessages = data.has_more || false;
  _updateLoadMoreBanner();
  if (!data.active_agent) {
    console.error('BUG: server returned empty active_agent — conversation must always have an agent');
  }
  selectedAgent = data.active_agent || selectedAgent;
  const themeLoad = typeof loadThemeSelector === 'function' ? loadThemeSelector() : null;
  updateActiveAgentBadge();
  loadResources();
  loadPermissionMode();
  document.getElementById('status').textContent = t('ready');
  scrollBottom(true);
  if (themeLoad && typeof themeLoad.then === 'function') {
    themeLoad.then(
      () => { if (typeof refreshMessagesScrollMetrics === 'function') refreshMessagesScrollMetrics(true); },
      () => { if (typeof refreshMessagesScrollMetrics === 'function') refreshMessagesScrollMetrics(true); }
    );
  }
  document.getElementById('input').focus();
}

function _noteLiveHistoryAppend(messageCount, rawDelta, msgId) {
  if (msgId && typeof _liveCountedMsgIds !== 'undefined') {
    if (_liveCountedMsgIds.has(msgId)) return;
    _liveCountedMsgIds.add(msgId);
  }
  const delta = rawDelta === undefined ? 1 : (Number(rawDelta) || 0);
  const nextCount = Number(messageCount || 0) || 0;
  if (nextCount > 0) {
    if (serverMsgCount > 0 && nextCount > serverMsgCount) {
      currentOffset += nextCount - serverMsgCount;
    } else if (!serverMsgCount) {
      currentOffset += delta;
    }
    serverMsgCount = Math.max(serverMsgCount || 0, nextCount);
  } else {
    currentOffset += delta;
    if (serverMsgCount) serverMsgCount += delta;
  }
  if (hasMoreMessages) _updateLoadMoreBanner();
}

function _placeLoadMoreBanner(banner) {
  const container = document.getElementById('messages');
  if (!container || !banner) return;
  if (container.firstChild !== banner) {
    container.insertBefore(banner, container.firstChild);
  }
}

function _updateLoadMoreBanner() {
  let banner = document.getElementById('loadMoreBanner');
  if (hasMoreMessages) {
    if (!banner) {
      banner = document.createElement('div');
      banner.id = 'loadMoreBanner';
      banner.className = 'load-more-banner';
      banner.onclick = loadMoreMessages;
    }
    _placeLoadMoreBanner(banner);
    const shown = document.querySelectorAll('#messages > .msg').length;
    const total = serverMsgCount || '?';
    banner.innerHTML = '&#x25B2; Load more messages (showing ' + shown + ' of ' + total + ')';
  } else if (banner) {
    banner.remove();
  }
}

function loadMoreMessages() {
  if (loadingMore || !conversationId || !hasMoreMessages) return;
  _updateLoadMoreBanner();
  loadingMore = true;
  const container = document.getElementById('messages');
  const banner = document.getElementById('loadMoreBanner');
  if (banner) banner.innerHTML = 'Loading...';
  const prevHeight = container.scrollHeight;
  const nextOffset = currentOffset;
  const requestConversationId = conversationId;

  action$('load_history', { conversation_id: requestConversationId, limit: displayWindow, offset: nextOffset })
    .subscribe(data => {
      if (requestConversationId !== conversationId) { loadingMore = false; return; }
      if (data.error) { loadingMore = false; _updateLoadMoreBanner(); return; }
      hasMoreMessages = data.has_more || false;
      currentOffset += data.raw_count || (data.messages || []).length;
      const insertPoint = banner && banner.parentNode === container ? banner.nextSibling : container.firstChild;
      // Build elements in a fragment, then insert at the right position.
      // Task messages go into their task block (existing or new).
      // Non-task messages go into the fragment for insertion.
      const frag = document.createDocumentFragment();
      // Build elements first, then insert — prepending one-by-one reverses order
      const _taskEls = {};  // taskId → [elements in order]
      const _fragEls = [];
      if (typeof suspendTechnicalMessageGrouping === 'function') suspendTechnicalMessageGrouping();
      try {
        for (const m of (data.messages || [])) {
          let content = m.content || '';
          if ((m.type === 'assistant' || m.role === 'assistant') && typeof content === 'string') {
            content = content.replace(/^\[[^\]]+\]:\s*/, '');
          }
          const el = addMsg(m.type || m.role, content, m);
          const _isDelegateTrace = (m.type === 'sub_agent_trace' || m.role === 'sub_agent_trace');
          const _taskId = _isDelegateTrace ? '' : (m.task_id || (m.source && m.source.task_id) || '');
          if (!el) continue;
          if (el.parentNode) el.parentNode.removeChild(el);
          if (_taskId) {
            const _iter = (m.source && m.source.task_iteration) || 1;
            const _blockKey = _taskId + '::iter' + _iter;
            if (!_taskEls[_blockKey]) _taskEls[_blockKey] = [];
            _taskEls[_blockKey].push({el, agentName: (m.source && m.source.name) || ''});
          } else {
            _fragEls.push(el);
          }
        }
      } finally {
        if (typeof resumeTechnicalMessageGrouping === 'function') resumeTechnicalMessageGrouping(false);
      }
      // Prepend task elements in correct order (as a batch)
      for (const [tid, entries] of Object.entries(_taskEls)) {
        let tb = _histTaskBlocks[tid];
        if (!tb) {
          tb = _getHistTaskBlock(tid, entries[0].agentName);
          if (tb && tb.el.parentNode) tb.el.parentNode.removeChild(tb.el);
          if (tb) frag.appendChild(tb.el);
        }
        if (tb) {
          // Insert all entries before the current first child (in order)
          const anchor = tb.content.firstChild;
          for (const entry of entries) {
            tb.content.insertBefore(entry.el, anchor);
          }
        } else {
          // Ungrouped: keep entries in the fragment as-is (chronological)
          for (const entry of entries) frag.appendChild(entry.el);
        }
      }
      // Insert non-task elements
      for (const el of _fragEls) {
        frag.appendChild(el);
      }
      container.insertBefore(frag, insertPoint);
      if (typeof applyTechnicalMessageGrouping === 'function') applyTechnicalMessageGrouping();
      setMessagesScrollTop(container.scrollHeight - prevHeight);
      _updateLoadMoreBanner();
      loadingMore = false;
    });
}





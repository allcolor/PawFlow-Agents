// Central conversation workspace: one canonical transcript, many persistent surfaces.
// Layouts 1-6 share one horizontally extensible tile board. Layout 1 shows one
// full-size tile at a time; higher layouts only change the visible grid geometry.
// Surfaces stay mounted while off-screen, so terminals and iframes do not reconnect
// merely because focus changes.
var _workspaceLayout = 1;
var _workspaceSelectedTab = 'chat';
var _workspaceTargetTab = '';
var _workspaceSurfaces = {};
var _workspaceResizeObserver = null;
var _workspaceRestoreLayout = 0;
var _workspaceStorageKey = 'pawflow.workspace.state.v2';
var _workspaceLegacyStorageKey = 'pawflow.workspace.layout.v1';
var _workspaceRestoredOrder = [];
var _workspaceRestoredSelection = '';
var _workspaceRestoredSurfaces = {};
var _workspaceHydrating = false;
var _workspaceMaxStoredSurfaces = 64;

function _workspaceLabel(key, fallback) {
  if (typeof t !== 'function') return fallback;
  const value = t(key);
  return value && value !== key ? value : fallback;
}

function _workspaceEscapeTab(tabId) {
  return typeof CSS !== 'undefined' && CSS.escape
    ? CSS.escape(String(tabId || ''))
    : String(tabId || '').replace(/"/g, '\\"');
}

function _workspacePanel(tabId) {
  return document.getElementById('tabContent_' + tabId)
    || (tabId === 'chat' ? document.getElementById('tabContentChat') : null);
}

function workspaceIsTiled() {
  return _workspaceLayout > 1;
}

function workspaceLayout() {
  return _workspaceLayout;
}

function workspaceSelectedTab() {
  return _workspaceSelectedTab;
}

function _workspaceSurfaceOrder() {
  const board = document.getElementById('workspaceBoard');
  if (!board) return Object.keys(_workspaceSurfaces);
  return Array.from(board.children || []).map(function(panel) {
    return panel && panel.dataset ? panel.dataset.tab : '';
  }).filter(function(tabId) { return !!_workspaceSurfaces[tabId]; });
}

function _workspaceSaveState() {
  if (_workspaceHydrating) return;
  const surfaces = _workspaceSurfaceOrder().slice(0, _workspaceMaxStoredSurfaces)
    .map(function(tabId) {
      const entry = _workspaceSurfaces[tabId] || {};
      return {
        surfaceId: tabId,
        type: entry.type || '',
        conversationId: entry.conversationId || '',
        title: entry.title || '',
        conversationTitle: entry.conversationTitle || '',
      };
    });
  const state = {
    version: 2,
    layout: _workspaceLayout,
    selectedSurfaceId: _workspaceSelectedTab,
    surfaces: surfaces,
  };
  try { localStorage.setItem(_workspaceStorageKey, JSON.stringify(state)); }
  catch (_error) {}
}

function _workspaceLoadState() {
  let state = null;
  try { state = JSON.parse(localStorage.getItem(_workspaceStorageKey) || 'null'); }
  catch (_error) {}
  if (state && state.version === 2 && Array.isArray(state.surfaces)) {
    _workspaceRestoredOrder = [];
    _workspaceRestoredSurfaces = {};
    state.surfaces.slice(0, _workspaceMaxStoredSurfaces).forEach(function(surface) {
      const surfaceId = surface && typeof surface.surfaceId === 'string'
        && surface.surfaceId.length <= 256 ? surface.surfaceId : '';
      if (!surfaceId) return;
      _workspaceRestoredOrder.push(surfaceId);
      _workspaceRestoredSurfaces[surfaceId] = {
        title: typeof surface.title === 'string' && surface.title.length <= 512
          ? surface.title : '',
        conversationTitle: typeof surface.conversationTitle === 'string'
          && surface.conversationTitle.length <= 512 ? surface.conversationTitle : '',
      };
    });
    _workspaceRestoredSelection = typeof state.selectedSurfaceId === 'string'
      && state.selectedSurfaceId.length <= 256 ? state.selectedSurfaceId : '';
    const layout = parseInt(state.layout, 10);
    return Number.isFinite(layout) && layout >= 1 && layout <= 6 ? layout : 1;
  }
  let legacyLayout = 1;
  try { legacyLayout = parseInt(localStorage.getItem(_workspaceLegacyStorageKey) || '1', 10); }
  catch (_error) {}
  return Number.isFinite(legacyLayout) && legacyLayout >= 1 && legacyLayout <= 6
    ? legacyLayout : 1;
}

function workspaceRestoredSurfaceTitle(tabId) {
  const restored = _workspaceRestoredSurfaces[String(tabId || '')];
  return restored ? restored.title : '';
}

function _workspaceInsertAfter(board, panel, anchor) {
  if (!board || !panel) return;
  const siblings = Array.from(board.children || []).filter(function(candidate) {
    return candidate !== panel;
  });
  const anchorIndex = siblings.indexOf(anchor);
  const reference = anchorIndex === -1 ? null : (siblings[anchorIndex + 1] || null);
  board.insertBefore(panel, reference);
}

function _workspaceRestoreSurfacePosition(board, panel, tabId) {
  const restoredIndex = _workspaceRestoredOrder.indexOf(tabId);
  if (restoredIndex === -1) return false;
  for (let index = restoredIndex + 1; index < _workspaceRestoredOrder.length; index++) {
    const next = _workspaceSurfaces[_workspaceRestoredOrder[index]];
    if (next && next.panel !== panel && next.panel.parentNode === board) {
      board.insertBefore(panel, next.panel);
      return true;
    }
  }
  for (let index = restoredIndex - 1; index >= 0; index--) {
    const previous = _workspaceSurfaces[_workspaceRestoredOrder[index]];
    if (previous && previous.panel !== panel && previous.panel.parentNode === board) {
      _workspaceInsertAfter(board, panel, previous.panel);
      return true;
    }
  }
  return false;
}

function _workspaceSurfaceBody(panel) {
  let body = panel.querySelector(':scope > .workspace-surface-body');
  if (body) return body;
  body = document.createElement('div');
  body.className = 'workspace-surface-body';
  while (panel.firstChild) body.appendChild(panel.firstChild);
  panel.appendChild(body);
  return body;
}

function _workspaceConversationTitle(conversationId) {
  const cid = String(conversationId || '');
  if (!cid) return '';
  const session = typeof getConversationSession === 'function'
    ? getConversationSession(cid) : null;
  if (session && session.title) return String(session.title);
  const rows = (window._ownConvs || []).concat(window._sharedConvs || []);
  const row = rows.find(item => item && item.conversation_id === cid);
  return String((row && (row.title || row.preview)) || cid.slice(0, 8));
}

function _workspaceHeader(tabId, options) {
  const header = document.createElement('div');
  header.className = 'workspace-surface-header';

  const identity = document.createElement('span');
  identity.className = 'workspace-surface-identity';
  const title = document.createElement('span');
  title.className = 'workspace-surface-title';
  title.textContent = options.title || tabId;
  title.title = options.title || tabId;
  identity.appendChild(title);
  if (options.conversationId && options.type !== 'webchat') {
    const conversation = document.createElement('span');
    conversation.className = 'workspace-surface-conversation';
    conversation.textContent = options.conversationTitle
      || _workspaceConversationTitle(options.conversationId);
    conversation.title = conversation.textContent;
    identity.appendChild(conversation);
  }
  header.appendChild(identity);

  const actions = document.createElement('span');
  actions.className = 'workspace-surface-actions';

  const maximize = document.createElement('button');
  maximize.type = 'button';
  maximize.className = 'workspace-maximize-btn';
  maximize.innerHTML = '&#x2922;';
  maximize.title = _workspaceLabel('workspaceMaximizeTitle', 'Maximize surface');
  maximize.setAttribute('aria-label', maximize.title);
  maximize.onclick = function(event) {
    event.stopPropagation();
    workspaceMaximizeSurface(tabId);
  };
  actions.appendChild(maximize);

  const target = document.createElement('button');
  target.type = 'button';
  target.className = 'workspace-target-btn';
  target.innerHTML = '&#9678;';
  target.title = _workspaceLabel('workspaceTargetTitle', 'Use this tile for the next surface');
  target.setAttribute('aria-label', target.title);
  target.onclick = function(event) {
    event.stopPropagation();
    workspaceArmTarget(tabId);
  };
  actions.appendChild(target);

  if (options.closable !== false) {
    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'workspace-close-btn';
    close.innerHTML = '&times;';
    close.title = _workspaceLabel('workspaceCloseTitle', 'Close surface');
    close.setAttribute('aria-label', close.title);
    close.onclick = function(event) {
      event.stopPropagation();
      workspaceCloseSurface(tabId);
    };
    actions.appendChild(close);
  }

  header.appendChild(actions);
  return header;
}

function workspaceRegisterSurface(panel, options) {
  if (!panel) return null;
  options = options || {};
  const tabId = String(options.tabId || panel.dataset.tab || '').trim();
  if (!tabId) throw new Error('BUG: workspace surface missing tab id');

  const board = document.getElementById('workspaceBoard');
  if (!board) return panel;
  const firstRegistration = !_workspaceSurfaces[tabId];
  const previous = _workspaceSurfaces[tabId] || {};
  const restored = _workspaceRestoredSurfaces[tabId] || {};
  _workspaceSurfaces[tabId] = {
    panel: panel,
    title: options.title || previous.title || restored.title || tabId,
    icon: options.icon || previous.icon || '',
    close: options.close || previous.close || null,
    closable: options.closable !== undefined ? options.closable : previous.closable,
    type: options.type || previous.type || tabId.split('-')[0],
    conversationId: options.conversationId !== undefined
      ? String(options.conversationId || '')
      : (previous.conversationId || panel.dataset.conversationId
        || (typeof focusedConversationId === 'function' ? focusedConversationId() : '')
        || (typeof conversationId !== 'undefined' ? conversationId : '') || ''),
  };
  _workspaceSurfaces[tabId].conversationTitle = options.conversationTitle
    || previous.conversationTitle
    || restored.conversationTitle
    || _workspaceConversationTitle(_workspaceSurfaces[tabId].conversationId);

  panel.dataset.tab = tabId;
  panel.dataset.surfaceType = _workspaceSurfaces[tabId].type;
  panel.dataset.surfaceLabel = _workspaceSurfaces[tabId].title;
  panel.dataset.conversationId = _workspaceSurfaces[tabId].conversationId;
  panel.dataset.conversationTitle = _workspaceSurfaces[tabId].conversationTitle;
  panel.classList.add('workspace-surface');
  _workspaceSurfaceBody(panel);

  let header = panel.querySelector(':scope > .workspace-surface-header');
  if (!header) {
    header = _workspaceHeader(tabId, _workspaceSurfaces[tabId]);
    panel.insertBefore(header, panel.firstChild);
  } else {
    const title = header.querySelector('.workspace-surface-title');
    if (title) {
      title.textContent = _workspaceSurfaces[tabId].title;
      title.title = _workspaceSurfaces[tabId].title;
    }
    const conversation = header.querySelector('.workspace-surface-conversation');
    if (conversation) {
      conversation.textContent = _workspaceSurfaces[tabId].conversationTitle;
      conversation.title = _workspaceSurfaces[tabId].conversationTitle;
    }
  }

  if (firstRegistration) {
    const restored = _workspaceRestoreSurfacePosition(board, panel, tabId);
    if (!restored) {
      const anchorTab = _workspaceTargetTab || _workspaceSelectedTab;
      const anchor = anchorTab !== tabId ? _workspacePanel(anchorTab) : null;
      if (anchor && anchor.parentNode === board) _workspaceInsertAfter(board, panel, anchor);
      else if (panel.parentNode !== board) board.appendChild(panel);
    }
  } else if (panel.parentNode !== board) {
    board.appendChild(panel);
  }

  if (firstRegistration && _workspaceTargetTab && _workspaceTargetTab !== tabId) {
    workspaceClearTarget();
  }
  _workspaceUpdateMaximizeState();
  _workspaceResize();
  _workspaceSaveState();
  return panel;
}

function workspaceSetSurfaceTitle(tabId, value) {
  const entry = _workspaceSurfaces[tabId];
  const title = String(value || '').trim();
  if (!entry || !title) return false;
  entry.title = title;
  entry.panel.dataset.surfaceLabel = title;
  const titleEl = entry.panel.querySelector(
    ':scope > .workspace-surface-header .workspace-surface-title'
  );
  if (titleEl) {
    titleEl.textContent = title;
    titleEl.title = title;
  }
  const railButton = document.querySelector(
    '.tab-btn[data-tab="' + _workspaceEscapeTab(tabId) + '"]');
  if (railButton) railButton.title = title;
  _workspaceUpdateTargetState();
  _workspaceSaveState();
  return true;
}

function workspaceSetConversationTitle(conversationId, value) {
  const cid = String(conversationId || '');
  const title = String(value || '').trim();
  if (!cid || !title) return false;
  let updated = false;
  Object.keys(_workspaceSurfaces).forEach(function(tabId) {
    const entry = _workspaceSurfaces[tabId];
    if (!entry || entry.conversationId !== cid) return;
    updated = true;
    entry.conversationTitle = title;
    entry.panel.dataset.conversationTitle = title;
    const conversation = entry.panel.querySelector(
      ':scope > .workspace-surface-header .workspace-surface-conversation'
    );
    if (conversation) {
      conversation.textContent = title;
      conversation.title = title;
    }
    if (entry.type === 'webchat') {
      entry.title = title;
      entry.panel.dataset.surfaceLabel = title;
      const surfaceTitle = entry.panel.querySelector(
        ':scope > .workspace-surface-header .workspace-surface-title'
      );
      if (surfaceTitle) {
        surfaceTitle.textContent = title;
        surfaceTitle.title = title;
      }
      const railButton = document.querySelector(
        '.tab-btn[data-tab="' + _workspaceEscapeTab(tabId) + '"]');
      if (railButton) railButton.title = title;
    }
  });
  if (updated) {
    _workspaceUpdateTargetState();
    _workspaceSaveState();
  }
  return updated;
}

function workspaceUnregisterSurface(tabId, keepPanel) {
  const entry = _workspaceSurfaces[tabId];
  if (!entry) return;
  const panel = entry.panel;
  const order = _workspaceSurfaceOrder();
  const closedIndex = order.indexOf(tabId);
  const nextTab = closedIndex === -1 ? ''
    : (order[closedIndex + 1] || order[closedIndex - 1] || '');
  delete _workspaceSurfaces[tabId];
  if (_workspaceTargetTab === tabId) workspaceClearTarget();
  if (keepPanel && panel) {
    panel.classList.remove('workspace-surface', 'active', 'workspace-selected');
    const header = panel.querySelector(':scope > .workspace-surface-header');
    if (header) header.remove();
  }
  if (_workspaceSelectedTab === tabId) {
    _workspaceSelectedTab = nextTab || (_workspaceSurfaces.chat ? 'chat'
      : (Object.keys(_workspaceSurfaces)[0] || 'chat'));
  }
  _workspaceApplySelection();
  _workspaceResize();
  _workspaceSaveState();
  if (!keepPanel && entry.conversationId
      && typeof releaseConversationSessionIfUnused === 'function') {
    releaseConversationSessionIfUnused(entry.conversationId);
  }
  return _workspaceSelectedTab;
}

function workspaceEnsureTabButton(tabId, options) {
  options = options || {};
  let button = document.querySelector('.tab-btn[data-tab="' + CSS.escape(tabId) + '"]');
  if (button) return button;
  const spacer = document.querySelector('.tab-spacer');
  if (!spacer || !spacer.parentNode) return null;

  button = document.createElement('button');
  button.type = 'button';
  button.className = 'tab-btn';
  button.dataset.tab = tabId;
  button.title = options.title || tabId;
  const icon = document.createElement('span');
  icon.className = 'workspace-tab-icon';
  icon.textContent = options.icon || '[]';
  button.appendChild(icon);
  button.onclick = function(event) {
    if (event.target.closest('.tab-close')) return;
    switchTab(tabId);
  };

  if (options.closable !== false) {
    const close = document.createElement('span');
    close.className = 'tab-close';
    close.textContent = '×';
    close.onclick = function(event) {
      event.stopPropagation();
      workspaceCloseSurface(tabId);
    };
    button.appendChild(close);
  }
  spacer.parentNode.insertBefore(button, spacer);
  return button;
}

function workspaceRemoveTabButton(tabId) {
  const button = document.querySelector('.tab-btn[data-tab="' + CSS.escape(tabId) + '"]');
  if (button) button.remove();
}

function workspaceCloseSurface(tabId) {
  const entry = _workspaceSurfaces[tabId];
  if (!entry || entry.closable === false) return;
  if (typeof entry.close === 'function') {
    entry.close();
    return;
  }
  const wasSelected = _workspaceSelectedTab === tabId;
  workspaceRemoveTabButton(tabId);
  workspaceUnregisterSurface(tabId);
  if (entry.panel) entry.panel.remove();
  if (wasSelected && typeof switchTab === 'function') switchTab(_workspaceSelectedTab);
}

function workspaceArmTarget(tabId) {
  if (!_workspaceSurfaces[tabId]) return;
  _workspaceTargetTab = _workspaceTargetTab === tabId ? '' : tabId;
  _workspaceUpdateTargetState();
  if (typeof switchTab === 'function') switchTab(tabId);
  else {
    _workspaceSelectedTab = tabId;
    _workspaceApplySelection();
  }
}

function workspaceClearTarget() {
  _workspaceTargetTab = '';
  _workspaceUpdateTargetState();
}

function workspaceMaximizeSurface(tabId) {
  if (!_workspaceSurfaces[tabId]) return false;
  if (_workspaceRestoreLayout > 1 && _workspaceLayout === 1) {
    const restoreLayout = _workspaceRestoreLayout;
    _workspaceRestoreLayout = 0;
    workspaceSetLayout(restoreLayout);
    return true;
  }
  if (_workspaceLayout === 1) return false;
  _workspaceRestoreLayout = _workspaceLayout;
  if (typeof switchTab === 'function') switchTab(tabId);
  else workspaceFocusSurface(tabId);
  workspaceSetLayout(1);
  return true;
}

function _workspaceUpdateMaximizeState() {
  const canRestore = _workspaceRestoreLayout > 1 && _workspaceLayout === 1;
  const shell = document.getElementById('workspaceShell');
  if (shell) shell.classList.toggle('workspace-maximized', canRestore);
  document.querySelectorAll('.workspace-maximize-btn').forEach(function(button) {
    button.innerHTML = canRestore ? '&#x2921;' : '&#x2922;';
    button.title = canRestore
      ? _workspaceLabel('workspaceRestoreLayoutTitle', 'Restore tiled layout')
      : _workspaceLabel('workspaceMaximizeTitle', 'Maximize surface');
    button.setAttribute('aria-label', button.title);
    button.setAttribute('aria-pressed', canRestore ? 'true' : 'false');
  });
}

function _workspaceUpdateTargetState() {
  document.querySelectorAll('.workspace-surface').forEach(function(panel) {
    panel.classList.toggle('workspace-target', panel.dataset.tab === _workspaceTargetTab);
    const button = panel.querySelector(':scope > .workspace-surface-header .workspace-target-btn');
    if (button) button.setAttribute('aria-pressed', panel.dataset.tab === _workspaceTargetTab ? 'true' : 'false');
  });
  const hint = document.getElementById('workspaceTargetHint');
  if (hint) {
    const entry = _workspaceSurfaces[_workspaceTargetTab];
    hint.hidden = !entry;
    hint.textContent = entry
      ? _workspaceLabel('workspaceTargetArmed', 'Next surface:') + ' ' + entry.title
      : '';
  }
}

function _workspaceApplySelection() {
  Object.keys(_workspaceSurfaces).forEach(function(tabId) {
    const panel = _workspaceSurfaces[tabId].panel;
    const selected = tabId === _workspaceSelectedTab;
    panel.classList.toggle('active', selected);
    panel.classList.toggle('workspace-selected', selected);
    panel.setAttribute('aria-current', selected ? 'true' : 'false');
  });
  document.querySelectorAll('.tab-btn').forEach(function(button) {
    button.classList.toggle('active', button.dataset.tab === _workspaceSelectedTab);
  });
  _workspaceSyncOpenSpace();
}

function workspaceFocusSurface(tabId, options) {
  if (!_workspaceSurfaces[tabId]) {
    const panel = _workspacePanel(tabId);
    if (!panel) return false;
    workspaceRegisterSurface(panel, { tabId: tabId, title: panel.dataset.surfaceLabel || tabId });
  }
  _workspaceSelectedTab = tabId;
  _workspaceApplySelection();

  const panel = _workspaceSurfaces[tabId].panel;
  const boundConversationId = _workspaceSurfaces[tabId].conversationId;
  if (boundConversationId && !(options && options.noConversationFocus)
      && typeof focusConversationSession === 'function') {
    focusConversationSession(boundConversationId);
  }
  if (panel && !(options && options.noScroll)) {
    const scroller = document.getElementById('workspaceScroller');
    if (scroller) {
      const left = Math.max(0, panel.offsetLeft);
      const right = panel.offsetLeft + panel.offsetWidth;
      if (left < scroller.scrollLeft || right > scroller.scrollLeft + scroller.clientWidth) {
        if (typeof scroller.scrollTo === 'function') {
          scroller.scrollTo({ left: left, behavior: 'smooth' });
        } else {
          scroller.scrollLeft = left;
        }
      }
    }
  }
  _workspaceSaveState();
  setTimeout(function() { _workspaceFitSurface(panel); }, 40);
  return true;
}

function _workspaceFitSurface(panel) {
  if (!panel) return;
  const terminal = panel.querySelector('.xterm-container');
  if (terminal && terminal._xterm) {
    if (typeof _fitAndNotifyTerminal === 'function') _fitAndNotifyTerminal(terminal);
    else if (terminal._fitAddon) {
      try { terminal._fitAddon.fit(); } catch (_error) {}
    }
  }
  if (panel.dataset.tab === 'openspace' && typeof _osResize === 'function') _osResize();
}

function _workspaceSyncOpenSpace() {
  if (typeof openspaceSetActive !== 'function') return;
  const registered = !!_workspaceSurfaces.openspace;
  openspaceSetActive(registered);
}

function _workspaceResize() {
  const shell = document.getElementById('workspaceShell');
  const scroller = document.getElementById('workspaceScroller');
  const board = document.getElementById('workspaceBoard');
  if (!shell || !scroller || !board) return;

  let columns = 1;
  let rows = 1;
  const layouts = {
    2: [2, 1],
    3: [3, 1],
    4: [2, 2],
    5: [3, 2],
    6: [3, 2],
  };
  if (_workspaceLayout > 1) {
    const layout = layouts[_workspaceLayout] || layouts[2];
    columns = layout[0];
    rows = layout[1];
    if (window.matchMedia && window.matchMedia('(max-width: 700px)').matches) {
      columns = 1;
      rows = 1;
    }
  }

  const gap = 8;
  const width = Math.max(240, (scroller.clientWidth - gap * (columns - 1)) / columns);
  const height = Math.max(180, (scroller.clientHeight - gap * (rows - 1)) / rows);
  const overflowing = Object.keys(_workspaceSurfaces).length > columns * rows;
  if (scroller.classList) {
    scroller.classList.toggle('workspace-overflowing', overflowing);
  }
  if (!overflowing && scroller.scrollLeft) scroller.scrollLeft = 0;
  board.style.setProperty('--workspace-columns', String(columns));
  board.style.setProperty('--workspace-rows', String(rows));
  board.style.setProperty('--workspace-tile-width', width + 'px');
  board.style.setProperty('--workspace-tile-height', height + 'px');
  Object.keys(_workspaceSurfaces).forEach(function(tabId) {
    _workspaceFitSurface(_workspaceSurfaces[tabId].panel);
  });
}

function workspaceSetLayout(value) {
  let next = parseInt(value, 10);
  if (!Number.isFinite(next) || next < 1 || next > 6) next = 1;
  if (next > 1 && _workspaceRestoreLayout > 1) _workspaceRestoreLayout = 0;
  _workspaceLayout = next;
  const shell = document.getElementById('workspaceShell');
  if (shell) {
    shell.dataset.layout = String(next);
    shell.classList.toggle('workspace-tiled', true);
  }
  const select = document.getElementById('workspaceLayoutSelect');
  if (select && select.value !== String(next)) select.value = String(next);
  _workspaceApplySelection();
  _workspaceUpdateMaximizeState();
  _workspaceResize();
  _workspaceSaveState();
}

function workspaceOpenOpenspace() {
  const panel = document.getElementById('tabContentOpenspace');
  if (!panel) return;
  if (!_workspaceSurfaces.openspace) {
    const title = _workspaceLabel('openspaceView', 'OpenSpace');
    workspaceRegisterSurface(panel, {
      tabId: 'openspace',
      type: 'openspace',
      title: title,
      icon: 'OS',
      close: workspaceCloseOpenspace,
      closable: true,
    });
    workspaceEnsureTabButton('openspace', {
      title: title,
      icon: 'OS',
      closable: true,
    });
  }
  if (typeof switchTab === 'function') switchTab('openspace');
  else workspaceFocusSurface('openspace');
}

function workspaceCloseOpenspace() {
  const wasSelected = workspaceSelectedTab() === 'openspace';
  if (typeof openspaceSetActive === 'function') openspaceSetActive(false);
  workspaceRemoveTabButton('openspace');
  workspaceUnregisterSurface('openspace', true);
  const panel = document.getElementById('tabContentOpenspace');
  if (panel) panel.classList.remove('active');
  if (wasSelected && typeof switchTab === 'function') switchTab(_workspaceSelectedTab);
}

function workspaceOpenWebchat() {
  const session = typeof focusedConversationSession === 'function'
    ? focusedConversationSession() : null;
  const tabId = session ? session.surfaceId : 'chat';
  if (typeof switchTab === 'function') switchTab(tabId);
  else workspaceFocusSurface(tabId);
}

function workspaceInit() {
  _workspaceHydrating = true;
  const saved = _workspaceLoadState();
  const chat = document.getElementById('tabContentChat');
  if (!chat) return;
  workspaceRegisterSurface(chat, {
    tabId: 'chat',
    type: 'webchat',
    title: _workspaceLabel('chatTitle', 'Webchat'),
    icon: 'WC',
    closable: false,
  });

  const board = document.getElementById('workspaceBoard');
  if (board) {
    board.addEventListener('pointerdown', function(event) {
      if (event.target.closest('button, input, select, textarea, a')) return;
      const panel = event.target.closest('.workspace-surface');
      if (panel && panel.dataset.tab) {
        if (typeof switchTab === 'function') switchTab(panel.dataset.tab);
        else {
          _workspaceSelectedTab = panel.dataset.tab;
          _workspaceApplySelection();
        }
      }
    });
  }

  workspaceSetLayout(saved);
  if (_workspaceRestoredSelection && _workspaceSurfaces[_workspaceRestoredSelection]) {
    _workspaceSelectedTab = _workspaceRestoredSelection;
    _workspaceApplySelection();
  }
  _workspaceHydrating = false;
  _workspaceSaveState();

  const scroller = document.getElementById('workspaceScroller');
  if (scroller && typeof ResizeObserver !== 'undefined') {
    _workspaceResizeObserver = new ResizeObserver(_workspaceResize);
    _workspaceResizeObserver.observe(scroller);
  } else {
    window.addEventListener('resize', _workspaceResize);
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', workspaceInit, { once: true });
} else {
  workspaceInit();
}

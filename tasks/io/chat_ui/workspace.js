// Central conversation workspace: one canonical transcript, many persistent surfaces.
//
// Layout 1 keeps the historical single-surface behaviour inside the space between
// the header and composer. Layouts 2-6 expose a horizontally extensible tile board.
// Surfaces stay mounted while hidden or off-screen, so terminals and iframes do not
// reconnect merely because focus changes.
var _workspaceLayout = 1;
var _workspaceSelectedTab = 'chat';
var _workspaceTargetTab = '';
var _workspaceSurfaces = {};
var _workspaceResizeObserver = null;
var _workspaceRestoreLayout = 0;
var _workspaceStorageKey = 'pawflow.workspace.layout.v1';

function _workspaceLabel(key, fallback) {
  if (typeof t !== 'function') return fallback;
  const value = t(key);
  return value && value !== key ? value : fallback;
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

function _workspaceSurfaceBody(panel) {
  let body = panel.querySelector(':scope > .workspace-surface-body');
  if (body) return body;
  body = document.createElement('div');
  body.className = 'workspace-surface-body';
  while (panel.firstChild) body.appendChild(panel.firstChild);
  panel.appendChild(body);
  return body;
}

function _workspaceHeader(tabId, options) {
  const header = document.createElement('div');
  header.className = 'workspace-surface-header';

  const title = document.createElement('span');
  title.className = 'workspace-surface-title';
  title.textContent = options.title || tabId;
  header.appendChild(title);

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
  _workspaceSurfaces[tabId] = {
    panel: panel,
    title: options.title || previous.title || tabId,
    icon: options.icon || previous.icon || '',
    close: options.close || previous.close || null,
    closable: options.closable !== undefined ? options.closable : previous.closable,
    type: options.type || previous.type || tabId.split('-')[0],
  };

  panel.dataset.tab = tabId;
  panel.dataset.surfaceType = _workspaceSurfaces[tabId].type;
  panel.dataset.surfaceLabel = _workspaceSurfaces[tabId].title;
  panel.classList.add('workspace-surface');
  _workspaceSurfaceBody(panel);

  let header = panel.querySelector(':scope > .workspace-surface-header');
  if (!header) {
    header = _workspaceHeader(tabId, _workspaceSurfaces[tabId]);
    panel.insertBefore(header, panel.firstChild);
  } else {
    const title = header.querySelector('.workspace-surface-title');
    if (title) title.textContent = _workspaceSurfaces[tabId].title;
  }

  if (panel.parentNode !== board) {
    const target = _workspaceTargetTab && _workspacePanel(_workspaceTargetTab);
    if (target && target.parentNode === board) board.insertBefore(panel, target);
    else board.appendChild(panel);
  } else if (firstRegistration && _workspaceTargetTab && _workspaceTargetTab !== tabId) {
    const target = _workspacePanel(_workspaceTargetTab);
    if (target && target.parentNode === board) board.insertBefore(panel, target);
  }

  if (firstRegistration && _workspaceTargetTab && _workspaceTargetTab !== tabId) {
    workspaceClearTarget();
  }
  _workspaceUpdateMaximizeState();
  _workspaceResize();
  return panel;
}

function workspaceUnregisterSurface(tabId, keepPanel) {
  const entry = _workspaceSurfaces[tabId];
  if (!entry) return;
  const panel = entry.panel;
  delete _workspaceSurfaces[tabId];
  if (_workspaceTargetTab === tabId) workspaceClearTarget();
  if (keepPanel && panel) {
    panel.classList.remove('workspace-surface', 'active', 'workspace-selected');
    const header = panel.querySelector(':scope > .workspace-surface-header');
    if (header) header.remove();
  }
  if (_workspaceSelectedTab === tabId) {
    _workspaceSelectedTab = _workspaceSurfaces.chat ? 'chat'
      : (Object.keys(_workspaceSurfaces)[0] || 'chat');
  }
  _workspaceApplySelection();
  _workspaceResize();
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
  workspaceRemoveTabButton(tabId);
  workspaceUnregisterSurface(tabId);
  if (entry.panel) entry.panel.remove();
  if (_workspaceSelectedTab === tabId && typeof switchTab === 'function') switchTab('chat');
}

function workspaceArmTarget(tabId) {
  if (!workspaceIsTiled() || !_workspaceSurfaces[tabId]) return;
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
  if (!workspaceIsTiled()) return false;
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
  if (workspaceIsTiled() && panel && !(options && options.noScroll)) {
    const scroller = document.getElementById('workspaceScroller');
    if (scroller) {
      const left = Math.max(0, panel.offsetLeft - 8);
      const right = panel.offsetLeft + panel.offsetWidth + 8;
      if (left < scroller.scrollLeft || right > scroller.scrollLeft + scroller.clientWidth) {
        if (typeof scroller.scrollTo === 'function') {
          scroller.scrollTo({ left: left, behavior: 'smooth' });
        } else {
          scroller.scrollLeft = left;
        }
      }
    }
  }
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
  const visible = registered && (workspaceIsTiled() || _workspaceSelectedTab === 'openspace');
  openspaceSetActive(visible);
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
  if (workspaceIsTiled()) {
    const layout = layouts[_workspaceLayout] || layouts[2];
    columns = layout[0];
    rows = layout[1];
    if (window.matchMedia && window.matchMedia('(max-width: 700px)').matches) {
      columns = 1;
      rows = 1;
    }
  }

  const gap = workspaceIsTiled() ? 8 : 0;
  const width = Math.max(240, (scroller.clientWidth - gap * (columns - 1)) / columns);
  const height = Math.max(180, (scroller.clientHeight - gap * (rows - 1)) / rows);
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
    shell.classList.toggle('workspace-tiled', next > 1);
  }
  const select = document.getElementById('workspaceLayoutSelect');
  if (select && select.value !== String(next)) select.value = String(next);
  try { localStorage.setItem(_workspaceStorageKey, String(next)); } catch (_error) {}
  if (next === 1) workspaceClearTarget();
  _workspaceApplySelection();
  _workspaceUpdateMaximizeState();
  _workspaceResize();
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
  if (wasSelected && typeof switchTab === 'function') switchTab('chat');
}

function workspaceOpenWebchat() {
  if (typeof switchTab === 'function') switchTab('chat');
  else workspaceFocusSurface('chat');
}

function workspaceInit() {
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

  let saved = 1;
  try { saved = parseInt(localStorage.getItem(_workspaceStorageKey) || '1', 10); }
  catch (_error) {}
  workspaceSetLayout(saved);

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

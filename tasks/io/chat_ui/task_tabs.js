// Filtered Webchat workspace surfaces.
//
// The canonical #messages tree remains the only render target for history and
// SSE. Filtered views are read-only projections maintained by one observer, so
// opening several agent/task tiles never creates another connection or changes
// message reconciliation.
var _taskTabRegistry = {};    // tabId -> filter descriptor
var _openTaskTabOrder = [];   // tabId[], creation order
var _activeTaskTabId = null;  // active filtered tab id
var _taskTabObserverStarted = false;
var _taskTabObserver = null;
var _taskTabRenderRaf = 0;

function _filteredViewToken(value) {
  return encodeURIComponent(String(value || ''))
    .replace(/%/g, '_')
    .replace(/[^a-zA-Z0-9_.-]/g, '_')
    .substring(0, 180);
}

function _filteredViewTabId(agentName, taskId) {
  return taskId
    ? 'filter-task-' + _filteredViewToken(taskId)
    : 'filter-agent-' + _filteredViewToken(String(agentName || '').toLowerCase());
}

function _filteredViewLabel(agentName, taskId) {
  const display = agentName ? displayAgentName(agentName) : t('agent');
  if (taskId) return display + ' / ' + taskId;
  return display;
}

function _filteredViewEscape(value) {
  return window.CSS && CSS.escape ? CSS.escape(String(value || ''))
    : String(value || '').replace(/"/g, '\\"');
}

function _filteredViewStripIds(root) {
  if (!root) return root;
  if (root.id) root.removeAttribute('id');
  root.querySelectorAll('[id]').forEach(function(node) { node.removeAttribute('id'); });
  root.querySelectorAll(
    '.delegate-cancel-btn, .btn-stop, .task-tab-open-btn, .msg-actions'
  ).forEach(function(node) { node.remove(); });
  return root;
}

function _filteredViewTaskMatch(node, taskId) {
  if (!node || !node.dataset) return false;
  if (node.dataset.taskId === taskId || node.dataset.delegateTaskId === taskId) return true;
  const safe = _filteredViewEscape(taskId);
  return !!node.querySelector(
    '[data-task-id="' + safe + '"], [data-delegate-task-id="' + safe + '"]'
  );
}

function _filteredViewAgentMatch(node, agentName) {
  if (!node || !node.dataset) return false;
  const wanted = String(agentName || '').toLowerCase();
  const direct = String(
    node.dataset.agentName || node.dataset.agent || node.dataset.targetAgent || ''
  ).toLowerCase();
  if (direct === wanted) return true;
  return Array.from(node.querySelectorAll(
    '[data-agent-name], [data-agent], [data-target-agent]'
  )).some(function(child) {
    const value = String(
      child.dataset.agentName || child.dataset.agent || child.dataset.targetAgent || ''
    ).toLowerCase();
    return value === wanted;
  });
}

function _filteredViewPrune(clone, info) {
  if (info.taskId) {
    clone.querySelectorAll('[data-delegate-task-id]').forEach(function(node) {
      if (node.dataset.delegateTaskId !== info.taskId) node.remove();
    });
    clone.querySelectorAll('[data-task-id]').forEach(function(node) {
      if (node.dataset.taskId !== info.taskId
          && !node.querySelector('[data-task-id="' + _filteredViewEscape(info.taskId) + '"]')) {
        node.remove();
      }
    });
  } else if (info.agentName) {
    const wanted = String(info.agentName).toLowerCase();
    clone.querySelectorAll(
      '[data-agent-name], [data-agent], [data-target-agent]'
    ).forEach(function(node) {
      const value = String(
        node.dataset.agentName || node.dataset.agent || node.dataset.targetAgent || ''
      ).toLowerCase();
      if (value && value !== wanted
          && !node.querySelector(
            '[data-agent-name="' + _filteredViewEscape(info.agentName) + '"], '
            + '[data-target-agent="' + _filteredViewEscape(info.agentName) + '"]'
          )) {
        node.remove();
      }
    });
  }
  return clone;
}

function _filteredViewClone(node, info) {
  const matches = info.taskId
    ? _filteredViewTaskMatch(node, info.taskId)
    : _filteredViewAgentMatch(node, info.agentName);
  if (!matches) return null;
  const clone = _filteredViewStripIds(node.cloneNode(true));
  return _filteredViewPrune(clone, info);
}

function _renderFilteredView(tabId) {
  const info = _taskTabRegistry[tabId];
  if (!info || !info.body) return;
  const source = document.getElementById('messages');
  if (!source) return;

  const body = info.body;
  const wasAtBottom = body.scrollHeight - body.scrollTop - body.clientHeight < 40;
  body.innerHTML = '';
  Array.from(source.children).forEach(function(node) {
    const clone = _filteredViewClone(node, info);
    if (clone) body.appendChild(clone);
  });

  if (!body.children.length) {
    const empty = document.createElement('div');
    empty.className = 'workspace-filter-empty';
    empty.textContent = t('workspaceFilterEmpty');
    body.appendChild(empty);
  }
  if (wasAtBottom) body.scrollTop = body.scrollHeight;
}

function _renderAllFilteredViews() {
  _taskTabRenderRaf = 0;
  Object.keys(_taskTabRegistry).forEach(_renderFilteredView);
}

function _queueFilteredViewRender() {
  if (_taskTabRenderRaf) return;
  _taskTabRenderRaf = requestAnimationFrame(_renderAllFilteredViews);
}

function _startTaskTabObserver() {
  if (_taskTabObserverStarted) return;
  const source = document.getElementById('messages');
  if (!source || typeof MutationObserver === 'undefined') return;
  _taskTabObserverStarted = true;
  _taskTabObserver = new MutationObserver(_queueFilteredViewRender);
  _taskTabObserver.observe(source, {
    childList: true, subtree: true, characterData: true, attributes: true,
  });
}

function _stopTaskTabObserverIfIdle() {
  if (Object.keys(_taskTabRegistry).length) return;
  if (_taskTabObserver) _taskTabObserver.disconnect();
  _taskTabObserver = null;
  _taskTabObserverStarted = false;
  if (_taskTabRenderRaf) cancelAnimationFrame(_taskTabRenderRaf);
  _taskTabRenderRaf = 0;
}

function filteredViewRoute(tabId) {
  return _taskTabRegistry[String(tabId || '')] || null;
}

function activeFilteredViewRoute() {
  const tabId = typeof workspaceSelectedTab === 'function'
    ? workspaceSelectedTab() : _activeTaskTabId;
  return filteredViewRoute(tabId);
}

function activeFilteredViewTargetAgent() {
  const route = activeFilteredViewRoute();
  return route && route.agentName ? route.agentName : '';
}

function activateFilteredView(tabId) {
  const route = filteredViewRoute(tabId);
  if (!route) return Promise.resolve(false);
  _activeTaskTabId = tabId;
  if (!route.agentName || typeof cmdAgentSelect !== 'function') {
    return Promise.resolve(false);
  }
  const current = typeof selectedAgent !== 'undefined' ? selectedAgent : '';
  if (String(current).toLowerCase() === String(route.agentName).toLowerCase()) {
    return Promise.resolve(true);
  }
  return Promise.resolve(cmdAgentSelect(route.agentName)).catch(function(error) {
    console.error('filtered webchat: agent selection failed', error);
    return false;
  });
}

function openAgentView(agentName, taskId) {
  agentName = String(agentName || '');
  taskId = String(taskId || '');
  if (!agentName && !taskId) return null;

  const tabId = _filteredViewTabId(agentName, taskId);
  const existing = _taskTabRegistry[tabId];
  if (existing) {
    _activeTaskTabId = tabId;
    if (typeof switchTab === 'function') switchTab(tabId);
    else if (typeof workspaceFocusSurface === 'function') workspaceFocusSurface(tabId);
    return tabId;
  }

  const label = _filteredViewLabel(agentName, taskId);
  const panel = document.createElement('div');
  panel.className = 'tab-content workspace-filter-surface';
  panel.id = 'tabContent_' + tabId;
  panel.dataset.tab = tabId;
  panel.dataset.filterAgent = agentName;
  panel.dataset.filterTaskId = taskId;

  const body = document.createElement('div');
  body.className = 'messages workspace-filter-messages';
  body.setAttribute('aria-label', label);
  panel.appendChild(body);

  _taskTabRegistry[tabId] = {
    tabId: tabId,
    agentName: agentName,
    taskId: taskId,
    body: body,
    panel: panel,
  };
  _openTaskTabOrder.push(tabId);
  _activeTaskTabId = tabId;

  const close = function() { closeFilteredView(tabId); };
  if (typeof workspaceRegisterSurface === 'function') {
    workspaceRegisterSurface(panel, {
      tabId: tabId,
      type: 'webchat-filter',
      title: label,
      icon: 'F',
      close: close,
      closable: true,
    });
    workspaceEnsureTabButton(tabId, {
      title: label,
      icon: 'F',
      closable: true,
    });
  } else {
    document.querySelector('.main').appendChild(panel);
  }

  _startTaskTabObserver();
  _renderFilteredView(tabId);
  if (typeof switchTab === 'function') switchTab(tabId);
  return tabId;
}

/** Open a filtered Webchat for one task. Kept as the public entry point used
 * by active-agent rows and inline task/delegate headers. */
function openTaskTab(taskId, agentName) {
  return openAgentView(agentName || '', taskId || '');
}

function closeFilteredView(tabId) {
  const info = _taskTabRegistry[tabId];
  if (!info) return;
  const wasSelected = typeof workspaceSelectedTab === 'function'
    ? workspaceSelectedTab() === tabId : _activeTaskTabId === tabId;
  delete _taskTabRegistry[tabId];
  const index = _openTaskTabOrder.indexOf(tabId);
  if (index !== -1) _openTaskTabOrder.splice(index, 1);
  if (_activeTaskTabId === tabId) {
    _activeTaskTabId = _openTaskTabOrder.length
      ? _openTaskTabOrder[_openTaskTabOrder.length - 1] : null;
  }
  if (typeof workspaceRemoveTabButton === 'function') workspaceRemoveTabButton(tabId);
  if (typeof workspaceUnregisterSurface === 'function') workspaceUnregisterSurface(tabId);
  if (info.panel) info.panel.remove();
  _stopTaskTabObserverIfIdle();

  const next = _activeTaskTabId || 'chat';
  if (wasSelected && typeof switchTab === 'function') switchTab(next);
}

function closeTaskTab(taskId) {
  const tabId = Object.keys(_taskTabRegistry).find(function(candidate) {
    return _taskTabRegistry[candidate].taskId === taskId;
  });
  if (tabId) closeFilteredView(tabId);
}

function closeActiveTaskTab() {
  if (_activeTaskTabId) closeFilteredView(_activeTaskTabId);
}

function switchTaskTab(taskId) {
  const tabId = _taskTabRegistry[taskId] ? taskId
    : Object.keys(_taskTabRegistry).find(function(candidate) {
      return _taskTabRegistry[candidate].taskId === taskId;
    });
  if (!tabId) return;
  _activeTaskTabId = tabId;
  if (typeof switchTab === 'function') switchTab(tabId);
  else if (typeof workspaceFocusSurface === 'function') workspaceFocusSurface(tabId);
}

// Conversation changes clear projections rather than leaving a filtered tile
// populated with rows from the previous canonical transcript.
window._taskTabsReset = function() {
  const selectedTab = typeof workspaceSelectedTab === 'function'
    ? workspaceSelectedTab() : _activeTaskTabId;
  const selectedWasFiltered = !!_taskTabRegistry[selectedTab];
  Object.keys(_taskTabRegistry).forEach(function(tabId) {
    const info = _taskTabRegistry[tabId];
    if (typeof workspaceRemoveTabButton === 'function') workspaceRemoveTabButton(tabId);
    if (typeof workspaceUnregisterSurface === 'function') workspaceUnregisterSurface(tabId);
    if (info.panel) info.panel.remove();
  });
  _taskTabRegistry = {};
  _openTaskTabOrder = [];
  _activeTaskTabId = null;
  _stopTaskTabObserverIfIdle();
  if (selectedWasFiltered && typeof switchTab === 'function') switchTab('chat');
};

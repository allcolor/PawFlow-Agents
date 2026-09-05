// Filtered Webchat workspace surfaces.
//
// Each ConversationSession transcript remains the only render target for its
// history and SSE. Filtered views are read-only projections of that owning
// transcript, so they never create another connection or change reconciliation.
var _taskTabRegistry = {};    // tabId -> filter descriptor
var _openTaskTabOrder = [];   // tabId[], creation order
var _activeTaskTabId = null;  // active filtered tab id

function _filteredViewToken(value) {
  return encodeURIComponent(String(value || ''))
    .replace(/%/g, '_')
    .replace(/[^a-zA-Z0-9_.-]/g, '_')
    .substring(0, 180);
}

function _filteredViewTabId(conversationId, agentName, taskId) {
  const conversation = _filteredViewToken(conversationId);
  return taskId
    ? 'filter-' + conversation + '-task-' + _filteredViewToken(taskId)
    : 'filter-' + conversation + '-agent-'
      + _filteredViewToken(String(agentName || '').toLowerCase());
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

function _filteredViewAgentValue(node) {
  if (!node || !node.dataset) return '';
  return String(
    node.dataset.agentName || node.dataset.agent || node.dataset.targetAgent || ''
  ).toLowerCase();
}

function _filteredViewClearAgentValue(node) {
  if (!node || !node.removeAttribute) return;
  node.removeAttribute('data-agent-name');
  node.removeAttribute('data-agent');
  node.removeAttribute('data-target-agent');
}

function _filteredViewRemoveChild(parent, child) {
  if (child && child.remove) child.remove();
  else if (parent && parent.removeChild) parent.removeChild(child);
}

// A foreign event can be the structural parent of a selected agent's nested
// event (a parent tool call with sub-agent calls is the common case). Keep only
// the DOM path to selected descendants; retaining the foreign row's own chrome
// would still leak its tool/message into a supposedly strict projection.
function _filteredViewKeepMatchingPaths(node, agentName) {
  const children = Array.from((node && (node.childNodes || node.children)) || []);
  children.forEach(function(child) {
    if (!child || !child.dataset) {
      _filteredViewRemoveChild(node, child);
      return;
    }
    const direct = _filteredViewAgentValue(child);
    if (direct === agentName) return;
    if (_filteredViewAgentMatch(child, agentName)) {
      _filteredViewKeepMatchingPaths(child, agentName);
      return;
    }
    _filteredViewRemoveChild(node, child);
  });
}

function _filteredViewAgentDescendantMatch(node, agentName) {
  return Array.from((node && node.children) || []).some(function(child) {
    return _filteredViewAgentMatch(child, agentName);
  });
}

function _filteredViewAgentMatch(node, agentName) {
  if (!node || !node.dataset) return false;
  const wanted = String(agentName || '').toLowerCase();
  const direct = _filteredViewAgentValue(node);
  if (direct === wanted) return true;
  // Aggregate roots are commonly stamped with the first agent that created
  // them. That identity must not hide a later matching child.
  return _filteredViewAgentDescendantMatch(node, wanted);
}

function _filteredViewWalk(root, callback) {
  Array.from((root && root.children) || []).forEach(function(child) {
    _filteredViewWalk(child, callback);
    callback(child);
  });
}

function _filteredViewIsAggregateBody(node) {
  if (!node || !node.classList) return false;
  return node.classList.contains('task-block-body')
    || node.classList.contains('delegate-body')
    || node.classList.contains('delegate-sub-body')
    || node.classList.contains('technical-group-body')
    || node.classList.contains('tc-children')
    || node.classList.contains('simple-turn-panel-scroll')
    || node.classList.contains('simple-turn-ephemeral');
}

function _filteredViewInheritedAgent(node, root) {
  let current = node && node.parentElement;
  while (current) {
    const value = _filteredViewAgentValue(current);
    if (value) return value;
    if (current === root) break;
    current = current.parentElement;
  }
  return '';
}

function _filteredViewPruneAgents(clone, agentName) {
  const wanted = String(agentName || '').toLowerCase();
  _filteredViewWalk(clone, function(node) {
    const value = _filteredViewAgentValue(node);
    if (!value || value === wanted) return;
    if (_filteredViewAgentDescendantMatch(node, wanted)) {
      // The node is a structural mixed-agent wrapper. Its own first-agent tag
      // cannot be inherited by the matching descendants kept below it.
      _filteredViewClearAgentValue(node);
      _filteredViewKeepMatchingPaths(node, wanted);
      return;
    }
    node.remove();
  });
  const rootValue = _filteredViewAgentValue(clone);
  if (rootValue && rootValue !== wanted
      && _filteredViewAgentDescendantMatch(clone, wanted)) {
    _filteredViewClearAgentValue(clone);
    _filteredViewKeepMatchingPaths(clone, wanted);
  }
  _filteredViewWalk(clone, function(body) {
    if (!_filteredViewIsAggregateBody(body)) return;
    const inherited = _filteredViewInheritedAgent(body, clone);
    Array.from(body.children || []).forEach(function(child) {
      const direct = _filteredViewAgentValue(child);
      const allowed = direct === wanted
        || (!direct && inherited === wanted)
        || _filteredViewAgentMatch(child, wanted);
      if (!allowed) child.remove();
    });
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
    _filteredViewPruneAgents(clone, info.agentName);
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

function _filteredViewTurnKey(block) {
  const key = String((block && block.dataset && block.dataset.turnId) || '');
  if (!key) throw new Error('filtered turn is missing data-turn-id');
  return key;
}

function _filteredViewRememberTurnState(info, block, index) {
  if (!info || !block) return;
  if (!info.turnStates) info.turnStates = {};
  const selected = Array.from(block.querySelectorAll('.simple-turn-tab')).find(function(tab) {
    return tab.getAttribute('aria-selected') === 'true';
  });
  info.turnStates[_filteredViewTurnKey(block)] = {
    expanded: block.classList.contains('expanded'),
    activeTab: selected ? String(selected.dataset.filteredTurnTab || '') : '',
  };
}

function _filteredViewSetTurnExpanded(info, block, index, expanded) {
  const value = !!expanded;
  block.classList.toggle('expanded', value);
  if (block._pfDisclosure) block._pfDisclosure.set(value);
  _filteredViewRememberTurnState(info, block, index);
}

function _filteredViewActivateTurnTab(info, block, index, tabKey, focus) {
  const tabs = Array.from(block.querySelectorAll('.simple-turn-tab'));
  const panels = Array.from(block.querySelectorAll('.simple-turn-panel'));
  let activeIndex = tabs.findIndex(function(tab) {
    return tab.dataset.filteredTurnTab === tabKey;
  });
  if (activeIndex < 0) activeIndex = 0;
  tabs.forEach(function(tab, tabIndex) {
    const active = tabIndex === activeIndex;
    tab.setAttribute('aria-selected', active ? 'true' : 'false');
    tab.setAttribute('tabindex', active ? '0' : '-1');
    if (active) tab.classList.remove('has-unread');
  });
  panels.forEach(function(panel, panelIndex) { panel.hidden = panelIndex !== activeIndex; });
  if (focus && tabs[activeIndex] && tabs[activeIndex].focus) tabs[activeIndex].focus();
  _filteredViewRememberTurnState(info, block, index);
}

function _filteredViewHydrateTurn(info, block, index) {
  const key = _filteredViewTurnKey(block);
  const saved = info.turnStates && info.turnStates[key];
  const header = block.querySelector('.simple-turn-header');
  const tabs = Array.from(block.querySelectorAll('.simple-turn-tab'));
  const panels = Array.from(block.querySelectorAll('.simple-turn-panel'));
  const idPrefix = 'filtered-' + _filteredViewToken(info.tabId) + '-turn-'
    + _filteredViewToken(key);

  if (info.agentName) {
    const title = block.querySelector('.simple-turn-title');
    if (title) title.textContent = displayAgentName(info.agentName);
  }
  tabs.forEach(function(tab, tabIndex) {
    const controls = String(tab.getAttribute('aria-controls') || '');
    const tabKey = tab.dataset.filteredTurnTab
      || controls.split('-').pop() || String(tabIndex);
    tab.dataset.filteredTurnTab = tabKey;
    tab.id = idPrefix + '-tab-' + _filteredViewToken(tabKey);
    const panel = panels[tabIndex];
    if (panel) {
      panel.id = idPrefix + '-panel-' + _filteredViewToken(tabKey);
      panel.setAttribute('aria-labelledby', tab.id);
      tab.setAttribute('aria-controls', panel.id);
    }
    tab.addEventListener('click', function() {
      _filteredViewActivateTurnTab(info, block, index, tabKey, true);
    });
    tab.addEventListener('keydown', function(event) {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      let next = tabIndex;
      if (event.key === 'Home') next = 0;
      else if (event.key === 'End') next = tabs.length - 1;
      else if (event.key === 'ArrowLeft') next = (tabIndex - 1 + tabs.length) % tabs.length;
      else next = (tabIndex + 1) % tabs.length;
      _filteredViewActivateTurnTab(
        info, block, index, tabs[next].dataset.filteredTurnTab, true);
    });
  });
  if (header) {
    header.addEventListener('click', function() {
      _filteredViewSetTurnExpanded(
        info, block, index, !block.classList.contains('expanded'));
    });
  }
  const details = block.querySelector('.simple-turn-details');
  if (header && details) {
    block._pfDisclosure = pfDisclosure.create({
      trigger: header,
      panel: details,
      open: saved ? saved.expanded : block.classList.contains('expanded'),
    });
  }
  const initialTab = saved && saved.activeTab
    ? saved.activeTab
    : ((tabs.find(function(tab) {
      return tab.getAttribute('aria-selected') === 'true';
    }) || tabs[0] || {}).dataset || {}).filteredTurnTab;
  _filteredViewSetTurnExpanded(
    info, block, index, saved ? saved.expanded : block.classList.contains('expanded'));
  if (tabs.length) _filteredViewActivateTurnTab(info, block, index, initialTab || '', false);
}

function _filteredViewHydrateClone(info, clone) {
  const turns = [];
  if (clone.classList && clone.classList.contains('simple-turn-block')) turns.push(clone);
  turns.push.apply(turns, Array.from(clone.querySelectorAll('.simple-turn-block')));
  turns.forEach(function(block, index) { _filteredViewHydrateTurn(info, block, index); });
  clone.querySelectorAll('.delegate-group-count').forEach(function(count) {
    const group = count.parentElement && count.parentElement.parentElement;
    const total = group ? group.querySelectorAll('.delegate-sub-block').length : 0;
    if (total) count.textContent = total + (total === 1 ? ' agent' : ' agents');
  });
  return clone;
}

function _filteredViewIsLoadMoreBanner(node) {
  return !!node && (node.id === 'loadMoreBanner'
    || (node.dataset && node.dataset.conversationLocalId === 'loadMoreBanner')
    || (node.classList && node.classList.contains('load-more-banner')));
}

function _filteredViewLoadMore(info) {
  if (typeof withConversationSession !== 'function') {
    throw new Error('BUG: filtered pagination requires ConversationSession');
  }
  return withConversationSession(info.conversationId, function() {
    return loadMoreMessages();
  });
}

function _filteredViewLoadMoreProxy(info, sourceBanner) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'load-more-banner workspace-filter-load-more';
  button.textContent = sourceBanner.textContent || 'Load more messages';
  button.addEventListener('click', function() { _filteredViewLoadMore(info); });
  return button;
}

function _filteredViewDisposeClone(node) {
  const turns = [];
  if (node && node.classList && node.classList.contains('simple-turn-block')) turns.push(node);
  if (node && node.querySelectorAll) {
    turns.push.apply(turns, Array.from(node.querySelectorAll('.simple-turn-block')));
  }
  turns.forEach(function(block) {
    if (block._pfDisclosure) block._pfDisclosure.destroy();
    block._pfDisclosure = null;
  });
}

function _filteredViewBeforePatch(info, existing) {
  const turns = [];
  if (existing.classList && existing.classList.contains('simple-turn-block')) turns.push(existing);
  turns.push.apply(turns, Array.from(existing.querySelectorAll('.simple-turn-block')));
  turns.forEach(function(block, index) {
    _filteredViewRememberTurnState(info, block, index);
  });
}

function _filteredViewRenderEmpty(info, body, projectedCount) {
  const current = body.querySelector('.workspace-filter-empty');
  if (projectedCount) {
    if (current) current.remove();
    return;
  }
  if (current) return;
  const empty = document.createElement('div');
  empty.className = 'workspace-filter-empty';
  empty.textContent = t('workspaceFilterEmpty');
  body.appendChild(empty);
}

function _filteredViewCreateProjection(info) {
  return pfProjection.create({
    source: info.source,
    destination: info.body,
    key: function(node) {
      return _filteredViewIsLoadMoreBanner(node) ? 'load-more' : pfProjection.key(node);
    },
    project: function(node) {
      if (_filteredViewIsLoadMoreBanner(node)) {
        return _filteredViewLoadMoreProxy(info, node);
      }
      const clone = _filteredViewClone(node, info);
      return clone ? _filteredViewHydrateClone(info, clone) : null;
    },
    beforePatch: function(existing) {
      _filteredViewBeforePatch(info, existing);
    },
    dispose: _filteredViewDisposeClone,
    renderEmpty: function(body, count) {
      _filteredViewRenderEmpty(info, body, count);
    },
    isActive: function() {
      return !!info.panel && !info.panel.hidden
        && info.panel.getAttribute('aria-hidden') !== 'true';
    },
    stickToBottom: true,
  });
}

function _renderFilteredView(tabId) {
  const info = _taskTabRegistry[tabId];
  if (!info || !info.body || !info.source) return;
  if (!info.projection) info.projection = _filteredViewCreateProjection(info);
  else info.projection.reconcileAll();
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
  // Flash identities belong to a delegated task, not the conversation roster.
  return route && route.agentName && !route.agentName.includes('::flash::')
    ? route.agentName : '';
}

function activateFilteredView(tabId) {
  const route = filteredViewRoute(tabId);
  if (!route) return Promise.resolve(false);
  _activeTaskTabId = tabId;
  if (!route.agentName || route.agentName.includes('::flash::')
      || typeof cmdAgentSelect !== 'function') {
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

  const sourceSession = captureConversationSession();
  if (!sourceSession || !sourceSession.messagesRoot) return null;
  const sourceConversationId = sourceSession.conversationId;
  const tabId = _filteredViewTabId(sourceConversationId, agentName, taskId);
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
  panel.dataset.conversationId = sourceConversationId;

  const body = document.createElement('div');
  body.className = 'messages workspace-filter-messages';
  body.setAttribute('aria-label', label);
  panel.appendChild(body);

  _taskTabRegistry[tabId] = {
    tabId: tabId,
    agentName: agentName,
    taskId: taskId,
    conversationId: sourceConversationId,
    source: sourceSession.messagesRoot,
    body: body,
    panel: panel,
    turnStates: {},
    projection: null,
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
      conversationId: sourceConversationId,
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
  if (info.projection) info.projection.destroy();
  if (typeof workspaceRemoveTabButton === 'function') workspaceRemoveTabButton(tabId);
  if (typeof workspaceUnregisterSurface === 'function') workspaceUnregisterSurface(tabId);
  if (info.panel) info.panel.remove();

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
    if (info.projection) info.projection.destroy();
    if (typeof workspaceRemoveTabButton === 'function') workspaceRemoveTabButton(tabId);
    if (typeof workspaceUnregisterSurface === 'function') workspaceUnregisterSurface(tabId);
    if (info.panel) info.panel.remove();
  });
  _taskTabRegistry = {};
  _openTaskTabOrder = [];
  _activeTaskTabId = null;
  if (selectedWasFiltered && typeof switchTab === 'function') switchTab('chat');
};

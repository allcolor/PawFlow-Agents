// ── Task Tabs: right-side sliding panel showing only one task's messages ──
// A task runs in its own sub-conversation (core/task_lifecycle.py) and every
// message carries source.task_id (see core/_conversation_store_ctxio.py).
// addMsg() tags the rendered element with dataset.taskId whenever that's
// present (messages_render.js), and _getTaskBlock() does the same for the
// inline collapsible group (sse_state.js). This module never re-fetches or
// re-renders messages: it clones the already-rendered top-level nodes that
// carry a matching data-task-id into a dedicated panel, so it stays in sync
// with whatever the main feed already knows how to draw.
//
// Plans do NOT get the same treatment yet: a plan step's instruction is
// tagged with source.plan_id, but the agent's response and its tool calls
// are not (tasks/ai/actions/plans.py sends the instruction as a plain user
// message, and core/llm_providers/_cc_stream*.py does not propagate
// plan_id the way it propagates task_id). A "Plan tab" opened the same way
// would show the instruction and nothing else, so it isn't wired here.

var _taskTabRegistry = {};    // taskId -> { agentName }
var _openTaskTabOrder = [];   // taskId[], dock display order
var _activeTaskTabId = null;
var _taskTabObserverStarted = false;

/** Open (or focus) the sliding tab for a task. Called from the Active
 *  Agents panel and from the inline task-block header. */
function openTaskTab(taskId, agentName) {
  if (!taskId) return;
  if (!_taskTabRegistry[taskId]) _taskTabRegistry[taskId] = { agentName: agentName || '' };
  else if (agentName) _taskTabRegistry[taskId].agentName = agentName;
  if (_openTaskTabOrder.indexOf(taskId) === -1) _openTaskTabOrder.push(taskId);
  _startTaskTabObserver();
  switchTaskTab(taskId);
}

/** Close one tab. If it was the active one, slide the panel shut. */
function closeTaskTab(taskId) {
  const idx = _openTaskTabOrder.indexOf(taskId);
  if (idx !== -1) _openTaskTabOrder.splice(idx, 1);
  delete _taskTabRegistry[taskId];
  if (_activeTaskTabId === taskId) {
    _activeTaskTabId = _openTaskTabOrder.length
      ? _openTaskTabOrder[_openTaskTabOrder.length - 1] : null;
    if (_activeTaskTabId) {
      _renderTaskTabPanel(_activeTaskTabId);
    } else {
      const panel = document.getElementById('taskTabPanel');
      if (panel) panel.classList.remove('open');
    }
  }
  _renderTaskTabDock();
}

function closeActiveTaskTab() {
  if (_activeTaskTabId) closeTaskTab(_activeTaskTabId);
}

/** Switch the single sliding panel to show a different (already-open) tab. */
function switchTaskTab(taskId) {
  if (!_taskTabRegistry[taskId]) return;
  _activeTaskTabId = taskId;
  _renderTaskTabDock();
  _renderTaskTabPanel(taskId);
  const panel = document.getElementById('taskTabPanel');
  if (panel) panel.classList.add('open');
}

function _renderTaskTabDock() {
  const dock = document.getElementById('taskTabDock');
  if (!dock) return;
  dock.innerHTML = '';
  _openTaskTabOrder.forEach(function(taskId) {
    const info = _taskTabRegistry[taskId] || {};
    const btn = document.createElement('button');
    btn.className = 'task-tab-dock-btn' + (taskId === _activeTaskTabId ? ' active' : '');
    btn.title = (info.agentName ? displayAgentName(info.agentName) + ' — ' : '') + taskId;
    btn.type = 'button';
    btn.onclick = function() { switchTaskTab(taskId); };
    btn.innerHTML = '📋';
    const closeBtn = document.createElement('span');
    closeBtn.className = 'task-tab-close';
    closeBtn.title = t('closeTaskTabTitle');
    closeBtn.textContent = '×';
    closeBtn.onclick = function(e) { e.stopPropagation(); closeTaskTab(taskId); };
    btn.appendChild(closeBtn);
    dock.appendChild(btn);
  });
}

function _renderTaskTabPanel(taskId) {
  const titleEl = document.getElementById('taskTabPanelTitle');
  const body = document.getElementById('taskTabPanelBody');
  if (!body) return;
  const info = _taskTabRegistry[taskId] || {};
  if (titleEl) {
    titleEl.textContent = t('taskTabPanelTitle', { id: taskId })
      + (info.agentName ? ' · ' + displayAgentName(info.agentName) : '');
  }
  const wasAtBottom = body.scrollHeight - body.scrollTop - body.clientHeight < 40;
  body.innerHTML = '';
  const container = document.getElementById('messages');
  if (!container) return;
  const safeId = window.CSS && CSS.escape ? CSS.escape(taskId) : taskId.replace(/"/g, '\\"');
  // Direct children only: _getTaskBlock() wraps a whole task's messages in
  // one <details data-task-id>, individual messages carry the same
  // data-task-id when grouping is off. Either way the top-level match is
  // the complete, non-duplicated set.
  const nodes = container.querySelectorAll(':scope > [data-task-id="' + safeId + '"]');
  if (!nodes.length) {
    const empty = document.createElement('div');
    empty.className = 'task-tab-empty';
    empty.textContent = t('taskTabEmpty');
    body.appendChild(empty);
    return;
  }
  nodes.forEach(function(node) {
    const clone = node.cloneNode(true);
    // Clones are read-only snapshots; strip ids so they don't collide with
    // the live originals still in #messages (findToolCallElement etc. must
    // keep resolving to the real nodes, not these display-only copies).
    if (clone.id) clone.removeAttribute('id');
    clone.querySelectorAll('[id]').forEach(function(n) { n.removeAttribute('id'); });
    body.appendChild(clone);
  });
  if (wasAtBottom) body.scrollTop = body.scrollHeight;
}

// Live updates: rather than hooking every SSE handler that can touch a
// task's messages, observe #messages once and re-render the open panel
// whenever it changes. Cheap: only runs while at least one tab is open,
// and only re-clones the currently visible tab.
function _startTaskTabObserver() {
  if (_taskTabObserverStarted) return;
  const container = document.getElementById('messages');
  if (!container || typeof MutationObserver === 'undefined') return;
  _taskTabObserverStarted = true;
  const observer = new MutationObserver(function() {
    if (_activeTaskTabId) _renderTaskTabPanel(_activeTaskTabId);
  });
  observer.observe(container, { childList: true, subtree: true, characterData: true });
}

// Reset on conversation switch/reload (called from conversations.js
// alongside window._sseClearLiveBlocks) — stale clones would otherwise
// keep showing a previous conversation's task messages.
window._taskTabsReset = function() {
  _taskTabRegistry = {};
  _openTaskTabOrder = [];
  _activeTaskTabId = null;
  const dock = document.getElementById('taskTabDock');
  if (dock) dock.innerHTML = '';
  const panel = document.getElementById('taskTabPanel');
  if (panel) panel.classList.remove('open');
};

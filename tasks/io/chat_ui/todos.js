// Read-only durable todo list dialog for the selected conversation agent.
const TODO_PAGE_SIZE = 20;
const TODO_STATUSES = ['in_progress', 'pending', 'completed'];
let _todosDialogAgent = '';
let _todosState = null;
let _todosRequestVersion = 0;
let _todosSearchTimer = null;

function _todoNode(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function closeTodosDialog() {
  _todosDialogAgent = '';
  _todosState = null;
  _todosRequestVersion += 1;
  if (_todosSearchTimer) clearTimeout(_todosSearchTimer);
  _todosSearchTimer = null;
  const dialog = document.getElementById('todosDialog');
  if (dialog) dialog.remove();
}

function _todoStatusLabel(status) {
  if (status === 'in_progress') return t('todoStatusInProgress');
  if (status === 'completed') return t('todoStatusCompleted');
  return t('todoStatusPending');
}

function _todoDate(timestamp) {
  const seconds = Number(timestamp || 0);
  if (!Number.isFinite(seconds) || seconds <= 0) return '';
  return new Date(seconds * 1000).toLocaleString();
}

function _todoDetail(card, label, value) {
  if (!value) return;
  const row = _todoNode('div', 'todo-detail');
  row.appendChild(_todoNode('span', 'todo-detail-label', label));
  row.appendChild(_todoNode('span', 'todo-detail-value', value));
  card.appendChild(row);
}

function _todoTaskCard(task) {
  const status = task.status || 'pending';
  const card = _todoNode('article', 'todo-card todo-card-' + status);
  const head = _todoNode('div', 'todo-card-head');
  head.appendChild(_todoNode(
    'span', 'todo-status todo-status-' + status, _todoStatusLabel(status)));
  head.appendChild(_todoNode(
    'strong', 'todo-subject', task.subject || task.id || '?'));
  card.appendChild(head);

  if (task.active_form) {
    _todoDetail(card, t('todoActiveWork'), task.active_form);
  }
  if (task.description) {
    card.appendChild(_todoNode(
      'div', 'todo-description', task.description));
  }
  _todoDetail(card, 'ID', task.id || '');
  _todoDetail(card, t('todoOwner'), task.owner || '');
  _todoDetail(
    card, t('todoBlocks'),
    Array.isArray(task.blocks) ? task.blocks.join(', ') : '');
  _todoDetail(
    card, t('todoBlockedBy'),
    Array.isArray(task.blocked_by) ? task.blocked_by.join(', ') : '');
  _todoDetail(card, t('todoCreated'), _todoDate(task.created_at));
  _todoDetail(card, t('todoUpdated'), _todoDate(task.updated_at));
  return card;
}

function _todoCountTotal() {
  if (!_todosState) return 0;
  return TODO_STATUSES.reduce(
    (total, status) => total + Number(_todosState.counts[status] || 0), 0);
}

function _renderTodoTabs() {
  const tabs = document.getElementById('todoTabs');
  if (!tabs || !_todosState) return;
  tabs.replaceChildren();
  TODO_STATUSES.forEach((status) => {
    const count = Number(_todosState.counts[status] || 0);
    const button = _todoNode(
      'button', 'todo-tab',
      _todoStatusLabel(status) + ' (' + count + ')');
    button.type = 'button';
    button.dataset.status = status;
    button.setAttribute('role', 'tab');
    button.setAttribute(
      'aria-selected', status === _todosState.status ? 'true' : 'false');
    if (status === _todosState.status) button.classList.add('active');
    button.addEventListener('click', () => {
      if (!_todosState || status === _todosState.status) return;
      _todosState.status = status;
      _loadTodosPage(true);
    });
    tabs.appendChild(button);
  });
}

function _renderTodosContent() {
  if (!_todosState) return;
  const count = document.getElementById('todoCount');
  if (count) {
    count.textContent = t('todoListCount', { n: _todoCountTotal() });
  }
  _renderTodoTabs();

  const body = document.getElementById('todoDialogBody');
  if (!body) return;
  body.replaceChildren();
  if (_todosState.loading) {
    body.appendChild(_todoNode('div', 'todo-empty', t('loading')));
    return;
  }
  if (_todosState.error) {
    body.appendChild(_todoNode(
      'div', 'todo-error',
      t('todoListLoadFailed', { error: _todosState.error })));
    return;
  }
  if (!_todosState.tasks.length) {
    body.appendChild(_todoNode('div', 'todo-empty', t('todoListEmpty')));
    return;
  }
  const list = _todoNode('div', 'todo-list');
  _todosState.tasks.forEach(
    (task) => list.appendChild(_todoTaskCard(task)));
  body.appendChild(list);

  if (_todosState.loadingMore) {
    body.appendChild(_todoNode(
      'div', 'todo-loading-more', t('loading')));
  } else if (_todosState.hasMore) {
    const loadMore = _todoNode(
      'button', 'btn todo-load-more', t('todoLoadMore'));
    loadMore.type = 'button';
    loadMore.addEventListener('click', () => _loadTodosPage(false));
    body.appendChild(loadMore);
  }
}

function _loadTodosPage(reset) {
  if (!_todosState || !_todosDialogAgent) return;
  const agent = _todosDialogAgent;
  if (reset) {
    _todosState.tasks = [];
    _todosState.total = 0;
    _todosState.hasMore = false;
    _todosState.loading = true;
    _todosState.loadingMore = false;
  } else {
    if (_todosState.loading || _todosState.loadingMore
        || !_todosState.hasMore) return;
    _todosState.loadingMore = true;
  }
  _todosState.error = '';
  const requestVersion = ++_todosRequestVersion;
  _renderTodosContent();

  action$('list_todos', {
    agent_name: agent,
    status: _todosState.status,
    query: _todosState.query,
    limit: TODO_PAGE_SIZE,
    offset: _todosState.tasks.length,
  }).subscribe({
    next: (data) => {
      if (!_todosState || _todosDialogAgent !== agent
          || requestVersion !== _todosRequestVersion) return;
      if (!data || data.error) {
        _todosState.loading = false;
        _todosState.loadingMore = false;
        _todosState.error = (data && data.error) || 'error';
        _renderTodosContent();
        return;
      }
      const rows = Array.isArray(data.tasks) ? data.tasks : [];
      _todosState.tasks = reset ? rows : _todosState.tasks.concat(rows);
      _todosState.total = Number(data.total || 0);
      _todosState.counts = data.counts || {
        pending: 0, in_progress: 0, completed: 0,
      };
      _todosState.hasMore = Boolean(data.has_more);
      _todosState.loading = false;
      _todosState.loadingMore = false;
      _renderTodosContent();
    },
    error: (error) => {
      if (!_todosState || _todosDialogAgent !== agent
          || requestVersion !== _todosRequestVersion) return;
      _todosState.loading = false;
      _todosState.loadingMore = false;
      _todosState.error = error.message || String(error);
      _renderTodosContent();
    },
  });
}

function _buildTodosDialog(agent) {
  const overlay = _todoNode('div', 'dialog-bg');
  overlay.id = 'todosDialog';
  const panel = _todoNode('div', 'dialog todo-dialog');
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-modal', 'true');
  panel.setAttribute('aria-labelledby', 'todosDialogTitle');

  const header = _todoNode('div', 'todo-dialog-header');
  const title = _todoNode(
    'div', 'dialog-title', t('todoListTitle', { agent: agent }));
  title.id = 'todosDialogTitle';
  header.appendChild(title);
  const count = _todoNode('span', 'todo-count', '');
  count.id = 'todoCount';
  header.appendChild(count);
  panel.appendChild(header);

  const search = _todoNode('input', 'todo-search');
  search.type = 'search';
  search.placeholder = t('todoSearchPlaceholder');
  search.setAttribute('aria-label', t('todoSearchPlaceholder'));
  search.addEventListener('input', () => {
    if (!_todosState) return;
    _todosState.query = search.value.trim();
    _todosRequestVersion += 1;
    if (_todosSearchTimer) clearTimeout(_todosSearchTimer);
    _todosSearchTimer = setTimeout(() => {
      _todosSearchTimer = null;
      _loadTodosPage(true);
    }, 250);
  });
  panel.appendChild(search);

  const tabs = _todoNode('div', 'todo-tabs');
  tabs.id = 'todoTabs';
  tabs.setAttribute('role', 'tablist');
  panel.appendChild(tabs);

  const body = _todoNode('div', 'dialog-body todo-dialog-body');
  body.id = 'todoDialogBody';
  panel.appendChild(body);

  const actions = _todoNode('div', 'dialog-actions');
  const close = _todoNode('button', 'btn', t('close'));
  close.type = 'button';
  close.addEventListener('click', closeTodosDialog);
  actions.appendChild(close);
  panel.appendChild(actions);
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
}

function showTodosDialog() {
  const agent = selectedAgent || '';
  if (!conversationId || !agent) {
    addMsg('error', t('noAgentSelectedSelectFirst'));
    return;
  }
  closeTodosDialog();
  _todosDialogAgent = agent;
  _todosState = {
    status: 'in_progress',
    query: '',
    tasks: [],
    total: 0,
    counts: { pending: 0, in_progress: 0, completed: 0 },
    hasMore: false,
    loading: true,
    loadingMore: false,
    error: '',
  };
  _buildTodosDialog(agent);
  _loadTodosPage(true);
}

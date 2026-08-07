// Read-only durable todo list dialog for the selected conversation agent.
let _todosDialogAgent = '';

function _todoNode(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function closeTodosDialog() {
  _todosDialogAgent = '';
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
  head.appendChild(_todoNode('span', 'todo-status todo-status-' + status, _todoStatusLabel(status)));
  head.appendChild(_todoNode('strong', 'todo-subject', task.subject || task.id || '?'));
  card.appendChild(head);

  if (task.active_form) {
    _todoDetail(card, t('todoActiveWork'), task.active_form);
  }
  if (task.description) {
    card.appendChild(_todoNode('div', 'todo-description', task.description));
  }
  _todoDetail(card, 'ID', task.id || '');
  _todoDetail(card, t('todoOwner'), task.owner || '');
  _todoDetail(card, t('todoBlocks'), Array.isArray(task.blocks) ? task.blocks.join(', ') : '');
  _todoDetail(card, t('todoBlockedBy'), Array.isArray(task.blocked_by) ? task.blocked_by.join(', ') : '');
  _todoDetail(card, t('todoUpdated'), _todoDate(task.updated_at));
  return card;
}

function _renderTodosDialog(agent, tasks, state) {
  closeTodosDialog();
  _todosDialogAgent = agent;

  const overlay = _todoNode('div', 'dialog-bg');
  overlay.id = 'todosDialog';
  const panel = _todoNode('div', 'dialog todo-dialog');
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-modal', 'true');
  panel.setAttribute('aria-labelledby', 'todosDialogTitle');

  const header = _todoNode('div', 'todo-dialog-header');
  const title = _todoNode('div', 'dialog-title', t('todoListTitle', { agent: agent }));
  title.id = 'todosDialogTitle';
  header.appendChild(title);
  if (!state.loading && !state.error) {
    header.appendChild(_todoNode('span', 'todo-count', t('todoListCount', { n: tasks.length })));
  }
  panel.appendChild(header);

  const body = _todoNode('div', 'dialog-body todo-dialog-body');
  if (state.loading) {
    body.appendChild(_todoNode('div', 'todo-empty', t('loading')));
  } else if (state.error) {
    body.appendChild(_todoNode('div', 'todo-error', t('todoListLoadFailed', { error: state.error })));
  } else if (!tasks.length) {
    body.appendChild(_todoNode('div', 'todo-empty', t('todoListEmpty')));
  } else {
    ['in_progress', 'pending', 'completed'].forEach((status) => {
      const rows = tasks.filter((task) => (task.status || 'pending') === status);
      if (!rows.length) return;
      const section = _todoNode('section', 'todo-section');
      section.appendChild(_todoNode(
        'h4', 'todo-section-title',
        _todoStatusLabel(status) + ' (' + rows.length + ')',
      ));
      rows.forEach((task) => section.appendChild(_todoTaskCard(task)));
      body.appendChild(section);
    });
  }
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
  _renderTodosDialog(agent, [], { loading: true, error: '' });
  action$('list_todos', { agent_name: agent }).subscribe({
    next: (data) => {
      if (_todosDialogAgent !== agent) return;
      if (!data || data.error) {
        _renderTodosDialog(agent, [], { loading: false, error: (data && data.error) || 'error' });
        return;
      }
      _renderTodosDialog(agent, Array.isArray(data.tasks) ? data.tasks : [], {
        loading: false,
        error: '',
      });
    },
    error: (error) => {
      if (_todosDialogAgent !== agent) return;
      _renderTodosDialog(agent, [], { loading: false, error: error.message || String(error) });
    },
  });
}

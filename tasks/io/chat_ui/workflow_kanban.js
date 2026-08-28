// WorkflowRun Kanban projection. The server remains the only source of truth.

const _workflowKanbanLaneKeys = {
  queued: 'workflowKanbanLaneQueued',
  running: 'workflowKanbanLaneRunning',
  waiting: 'workflowKanbanLaneWaiting',
  attention: 'workflowKanbanLaneAttention',
  failed: 'workflowKanbanLaneFailed',
  done: 'workflowKanbanLaneDone',
  not_started: 'workflowKanbanLaneNotStarted',
  ready: 'workflowKanbanLaneReady',
  blocked: 'workflowKanbanLaneBlocked',
  unknown: 'workflowKanbanLaneUnknown',
};

function _workflowKanbanUuid() {
  if (window.crypto && typeof window.crypto.randomUUID === 'function') {
    return window.crypto.randomUUID();
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(char) {
    const value = Math.floor(Math.random() * 16);
    return (char === 'x' ? value : ((value & 3) | 8)).toString(16);
  });
}

function _workflowKanbanApi(action, payload) {
  return rxjs.firstValueFrom(action$(action, payload)).then(function(data) {
    if (data && data.error) throw new Error(data.error);
    return data || {};
  });
}

function _workflowKanbanLaneLabel(lane) {
  return t(_workflowKanbanLaneKeys[lane.id] || '') || lane.label || lane.id;
}

function _workflowKanbanCommandTarget(command) {
  if (command === 'retry' || command === 'open_interaction') return 'running';
  if (command === 'cancel') return 'done';
  if (command === 'force_stop') return 'force_stopped';
  return '';
}

function _workflowKanbanTargets(card) {
  return Array.from(new Set((card.allowed_commands || [])
    .map(_workflowKanbanCommandTarget).filter(Boolean)));
}

function _workflowKanbanBadgesHtml(badges) {
  return (badges || []).map(function(badge) {
    const label = badge.label || badge.id || '';
    const value = badge.value === undefined ? '' : ' ' + String(badge.value);
    return '<span class="workflow-kanban-badge" data-badge="' + _pfpAttr(badge.id || '')
      + '">' + escapeHtml(label + value) + '</span>';
  }).join('');
}

function _workflowKanbanRelationsHtml(relations) {
  const parents = (relations && relations.parents) || [];
  const children = (relations && relations.children) || [];
  if (!parents.length && !children.length) return '';
  return '<div class="workflow-kanban-relations">'
    + (parents.length ? '<span><strong>' + escapeHtml(t('workflowKanbanParents'))
      + ':</strong> ' + parents.map(escapeHtml).join(', ') + '</span>' : '')
    + (children.length ? '<span><strong>' + escapeHtml(t('workflowKanbanChildren'))
      + ':</strong> ' + children.map(escapeHtml).join(', ') + '</span>' : '')
    + '</div>';
}

function _workflowKanbanCardHtml(card) {
  const targets = _workflowKanbanTargets(card);
  const summary = card.summary || {};
  const relationCount = ((card.relations || {}).parents || []).length
    + ((card.relations || {}).children || []).length;
  return '<article class="workflow-kanban-card" role="listitem" tabindex="0" draggable="'
    + (targets.length ? 'true' : 'false') + '" data-card-id="' + _pfpAttr(card.id)
    + '" data-targets="' + _pfpAttr(targets.join(',')) + '">'
    + '<button type="button" class="workflow-kanban-card-open" data-card-open="'
    + _pfpAttr(card.id) + '"><strong>' + escapeHtml(card.title || card.id)
    + '</strong><span class="workflow-kanban-status">' + escapeHtml(card.status || card.lane)
    + '</span></button>'
    + '<div class="workflow-kanban-card-meta">'
    + (card.assignee ? '<span>' + escapeHtml(t('workflowKanbanOwner')) + ': '
      + escapeHtml(card.assignee) + '</span>' : '')
    + (card.comments_count ? '<span>' + escapeHtml(t('workflowKanbanComments'))
      + ': ' + escapeHtml(String(card.comments_count)) + '</span>' : '')
    + (relationCount ? '<span>' + escapeHtml(t('workflowKanbanRelations'))
      + ': ' + escapeHtml(String(relationCount)) + '</span>' : '')
    + (summary.generation ? '<span>#' + escapeHtml(String(summary.generation)) + '</span>' : '')
    + '</div><div class="workflow-kanban-badges">'
    + _workflowKanbanBadgesHtml(card.badges) + '</div></article>';
}

function _workflowKanbanCommentsHtml(card) {
  const comments = card.comments || [];
  if (!comments.length) {
    return '<p class="workflow-kanban-empty">' + escapeHtml(t('workflowKanbanNoComments'))
      + '</p>';
  }
  return '<ol class="workflow-kanban-comments">' + comments.map(function(comment) {
    return '<li><div><strong>' + escapeHtml(comment.author_label || '—') + '</strong>'
      + '<time>' + escapeHtml(_workflowRunDate(comment.created_at)) + '</time></div>'
      + '<p>' + escapeHtml(comment.body || '') + '</p></li>';
  }).join('') + '</ol>';
}

function _workflowKanbanAttachmentsHtml(card) {
  const attachments = card.attachments || [];
  if (!attachments.length) {
    return '<p class="workflow-kanban-empty">'
      + escapeHtml(t('workflowKanbanNoAttachments')) + '</p>';
  }
  return '<ol class="workflow-kanban-attachments">' + attachments.map(function(item) {
    return '<li><a href="' + _pfpAttr(item.url || '') + '" target="_blank" rel="noopener">'
      + escapeHtml(item.label || item.filename || item.file_id) + '</a><span>'
      + escapeHtml(item.content_type || '') + (item.size ? ' · ' + escapeHtml(String(item.size))
        + ' B' : '') + '</span></li>';
  }).join('') + '</ol>';
}

function _workflowKanbanReviewsHtml(card) {
  const reviews = card.review_history || [];
  if (!reviews.length) {
    return '<p class="workflow-kanban-empty">' + escapeHtml(t('workflowKanbanNoReviews'))
      + '</p>';
  }
  return '<ol class="workflow-kanban-reviews">' + reviews.map(function(review) {
    return '<li><div><strong>' + escapeHtml(review.decision || '') + '</strong><time>'
      + escapeHtml(_workflowRunDate(review.created_at)) + '</time></div>'
      + (review.comment ? '<p>' + escapeHtml(review.comment) + '</p>' : '') + '</li>';
  }).join('') + '</ol>';
}

function _workflowKanbanDiagnosticsHtml(summary) {
  const diagnostics = summary.diagnostics || {};
  const worker = diagnostics.worker || null;
  return '<dl class="workflow-kanban-summary workflow-kanban-diagnostics"><dt>'
    + escapeHtml(t('workflowKanbanGeneration')) + '</dt><dd>'
    + escapeHtml(String(summary.generation || '—')) + '</dd><dt>'
    + escapeHtml(t('workflowKanbanGenerationState')) + '</dt><dd>'
    + escapeHtml(diagnostics.stale_generation
      ? t('workflowKanbanGenerationStale') : t('workflowKanbanGenerationCurrent')) + '</dd><dt>'
    + escapeHtml(t('workflowKanbanWorker')) + '</dt><dd>'
    + escapeHtml(worker ? (worker.status || t('workflowKanbanWorkerLive'))
      + ' · ' + Math.round(worker.duration_s || 0) + 's' : t('workflowKanbanWorkerNone'))
    + '</dd></dl>';
}

function _workflowKanbanViewsKey(agentName) {
  return 'pawflow.workflowKanban.views.' + String(conversationId || '') + '.'
    + String(agentName || '').toLowerCase();
}

function _workflowKanbanReadViews(agentName) {
  try {
    const value = JSON.parse(localStorage.getItem(_workflowKanbanViewsKey(agentName)) || '{}');
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  } catch (_error) {
    return {};
  }
}

function _workflowKanbanDetailHtml(card, snapshot) {
  if (!card) return '';
  const summary = card.summary || {};
  const commands = card.allowed_commands || [];
  const isTask = !!card.task_id;
  const allowedTargets = _workflowKanbanTargets(card);
  const laneOptions = (snapshot.lanes || []).filter(function(lane) {
    return allowedTargets.includes(lane.id);
  }).map(function(lane) {
    return '<option value="' + _pfpAttr(lane.id) + '">'
      + escapeHtml(_workflowKanbanLaneLabel(lane)) + '</option>';
  }).join('');
  const forceOption = commands.includes('force_stop')
    ? '<option value="force_stopped">' + escapeHtml(t('workflowKanbanForceStop'))
      + '</option>' : '';
  const usage = Object.entries(summary.usage || {}).map(function(entry) {
    return '<span class="workflow-kanban-badge">' + escapeHtml(
      entry[0].replaceAll('_', ' ') + ': ' + String(entry[1])) + '</span>';
  }).join('');
  const project = card.project || {};
  return '<aside class="workflow-kanban-drawer" tabindex="-1" aria-label="'
    + _pfpAttr(t('workflowKanbanDetails')) + '">'
    + '<div class="workflow-kanban-drawer-head"><div><h4>'
    + escapeHtml(card.title || card.id) + '</h4><span>'
    + escapeHtml(card.status || card.lane) + '</span></div>'
    + '<button type="button" data-kanban-detail-close aria-label="' + _pfpAttr(t('close'))
    + '">&times;</button></div>'
    + '<dl class="workflow-kanban-summary"><dt>' + escapeHtml(t('workflowKanbanOwner'))
    + '</dt><dd>' + escapeHtml(card.assignee || t('workflowKanbanUnassigned'))
    + '</dd><dt>' + escapeHtml(t('workflowRunUpdated')) + '</dt><dd>'
    + escapeHtml(_workflowRunDate(card.updated_at)) + '</dd>'
    + (project.label ? '<dt>' + escapeHtml(t('workflowKanbanProject')) + '</dt><dd>'
      + escapeHtml(project.label) + '</dd>' : '')
    + (summary.evidence ? '<dt>' + escapeHtml(t('workflowKanbanEvidence'))
      + '</dt><dd>' + escapeHtml(summary.evidence) + '</dd>' : '') + '</dl>'
    + '<div class="workflow-kanban-badges">' + _workflowKanbanBadgesHtml(card.badges)
    + usage + '</div>' + _workflowKanbanRelationsHtml(card.relations)
    + _workflowKanbanDiagnosticsHtml(summary)
    + '<div class="workflow-kanban-detail-actions">'
    + (!isTask ? '<button type="button" data-kanban-open-tasks>'
      + escapeHtml(t('workflowKanbanOpenTasks')) + '</button>' : '')
    + (isTask ? '<button type="button" data-kanban-open-graph>'
      + escapeHtml(t('workflowKanbanOpenGraph')) + '</button>' : '')
    + (commands.includes('open_interaction') ? '<button type="button" data-kanban-command="running">'
      + escapeHtml(t('workflowKanbanOpenInteraction')) + '</button>' : '')
    + (commands.includes('retry') ? '<button type="button" data-kanban-command="running">'
      + escapeHtml(t('workflowRunRetry')) + '</button>' : '')
    + (commands.includes('cancel') ? '<button type="button" data-kanban-command="done">'
      + escapeHtml(t('workflowKanbanCancel')) + '</button>' : '')
    + (commands.includes('force_stop') ? '<button type="button" class="danger" data-kanban-command="force_stopped">'
      + escapeHtml(t('workflowKanbanForceStop')) + '</button>' : '')
    + '<button type="button" data-kanban-propose>'
      + escapeHtml(t('workflowKanbanCreateProposal')) + '</button>'
    + '</div>' + (allowedTargets.length ? '<label class="workflow-kanban-move"><span>'
    + escapeHtml(t('workflowKanbanMoveAction')) + '</span><select data-kanban-target>'
    + laneOptions + forceOption + '</select><button type="button" data-kanban-apply>'
    + escapeHtml(t('workflowKanbanApply')) + '</button></label>' : '')
    + '<form data-kanban-assignment class="workflow-kanban-form"><label><span>'
    + escapeHtml(t('workflowKanbanOwner')) + '</span><input name="assignee" maxlength="160" value="'
    + _pfpAttr(card.assignee || '') + '"></label><button type="submit">'
    + escapeHtml(t('workflowKanbanAssign')) + '</button></form>'
    + '<p class="workflow-kanban-disabled-reason">'
      + escapeHtml(t('workflowKanbanUnsupportedTransitions')) + '</p>'
    + '<section><h5>' + escapeHtml(t('workflowKanbanAttachments')) + '</h5>'
    + _workflowKanbanAttachmentsHtml(card)
    + '<form data-kanban-attachment class="workflow-kanban-form"><label><span>'
    + escapeHtml(t('workflowKanbanAddAttachment')) + '</span><input name="file" type="file" required>'
    + '</label><button type="submit">' + escapeHtml(t('workflowKanbanAttach'))
    + '</button></form></section>'
    + '<section><h5>' + escapeHtml(t('workflowKanbanReviews')) + '</h5>'
    + _workflowKanbanReviewsHtml(card)
    + '<form data-kanban-review class="workflow-kanban-form"><label><span>'
    + escapeHtml(t('workflowKanbanReviewDecision')) + '</span><select name="decision">'
    + '<option value="approved">' + escapeHtml(t('workflowKanbanReviewApproved')) + '</option>'
    + '<option value="changes_requested">' + escapeHtml(t('workflowKanbanReviewChanges')) + '</option>'
    + (card.review && card.review.decision === 'approved'
      ? '<option value="reopened">' + escapeHtml(t('workflowKanbanReviewReopen')) + '</option>' : '')
    + '</select></label><label><span>' + escapeHtml(t('workflowKanbanReviewComment'))
    + '</span><textarea name="comment" maxlength="4000"></textarea></label><button type="submit">'
    + escapeHtml(t('workflowKanbanReviewSubmit')) + '</button></form></section>'
    + '<section><h5>' + escapeHtml(t('workflowKanbanComments')) + '</h5>'
    + _workflowKanbanCommentsHtml(card)
    + '<form data-kanban-comment class="workflow-kanban-form"><label><span>'
    + escapeHtml(t('workflowKanbanAddComment')) + '</span><textarea name="body" maxlength="4000" required></textarea>'
    + '</label><button type="submit">' + escapeHtml(t('workflowKanbanComment'))
    + '</button></form></section></aside>';
}

function mountWorkflowKanban(host, agentName, runId, options) {
  options = options || {};
  const state = {
    destroyed: false,
    runId: String(runId || ''),
    pageSize: 25,
    visibleLimit: 25,
    snapshot: null,
    selectedCardId: '',
    refreshTimer: null,
    refreshing: false,
    refreshPending: false,
    announcement: '',
    filterText: '',
    projectId: '',
    hideDone: false,
    savedView: '',
  };

  host.classList.add('workflow-kanban-host');
  host.innerHTML = '<div class="workflow-kanban-loading" role="status">'
    + escapeHtml(t('loading')) + '</div>';

  function selectedCard() {
    return state.snapshot && (state.snapshot.cards || []).find(function(card) {
      return card.id === state.selectedCardId;
    });
  }

  function announce(message) {
    state.announcement = String(message || '');
    const live = host.querySelector('[data-kanban-live]');
    if (live) live.textContent = state.announcement;
  }

  function scheduleRefresh(delay) {
    if (state.destroyed) return;
    if (state.refreshTimer) clearTimeout(state.refreshTimer);
    state.refreshTimer = setTimeout(function() {
      state.refreshTimer = null;
      refresh().catch(function(error) { addMsg('error', error.message); });
    }, delay === undefined ? (document.visibilityState === 'hidden' ? 15000 : 8000) : delay);
  }

  function render() {
    const snapshot = state.snapshot;
    if (!snapshot) return;
    const allCards = snapshot.cards || [];
    const needle = state.filterText.toLowerCase();
    const cards = allCards.filter(function(card) {
      if (state.hideDone && card.lane === 'done') return false;
      if (state.projectId && ((card.project || {}).id || '') !== state.projectId) return false;
      if (!needle) return true;
      return [card.title, card.status, card.assignee, (card.project || {}).label]
        .some(function(value) { return String(value || '').toLowerCase().includes(needle); });
    });
    if (state.selectedCardId && !allCards.some(function(card) {
      return card.id === state.selectedCardId;
    })) state.selectedCardId = '';
    const savedViews = _workflowKanbanReadViews(agentName);
    const projects = snapshot.projects || [];
    const toolbar = '<div class="workflow-kanban-toolbar">'
      + '<div><strong>' + escapeHtml(t('workflowKanbanTitle', { agent: agentName }))
      + '</strong><span>' + escapeHtml(snapshot.mode === 'tasks'
        ? t('workflowKanbanTasks') : t('workflowKanbanRuns')) + '</span></div>'
      + (snapshot.mode === 'tasks' ? '<button type="button" data-kanban-runs>'
        + escapeHtml(t('workflowKanbanBackRuns')) + '</button>' : '')
      + '<label><span>' + escapeHtml(t('workflowKanbanFilter'))
      + '</span><input data-kanban-filter type="search" value="' + _pfpAttr(state.filterText)
      + '"></label><label><span>' + escapeHtml(t('workflowKanbanProject'))
      + '</span><select data-kanban-project><option value="">'
      + escapeHtml(t('workflowKanbanAllProjects')) + '</option>' + projects.map(function(project) {
        return '<option value="' + _pfpAttr(project.id) + '"'
          + (state.projectId === project.id ? ' selected' : '') + '>'
          + escapeHtml(project.label || project.id) + '</option>';
      }).join('') + '</select></label><label class="workflow-kanban-check"><input '
      + 'data-kanban-hide-done type="checkbox"' + (state.hideDone ? ' checked' : '') + '><span>'
      + escapeHtml(t('workflowKanbanHideDone')) + '</span></label><label><span>'
      + escapeHtml(t('workflowKanbanSavedView')) + '</span><select data-kanban-saved-view>'
      + '<option value="">—</option>' + Object.keys(savedViews).sort().map(function(name) {
        return '<option value="' + _pfpAttr(name) + '"' + (state.savedView === name ? ' selected' : '')
          + '>' + escapeHtml(name) + '</option>';
      }).join('') + '</select></label><button type="button" data-kanban-save-view>'
      + escapeHtml(t('workflowKanbanSaveView')) + '</button>'
      + '<label><span>' + escapeHtml(t('workflowKanbanPageSize'))
      + '</span><select data-kanban-page-size>'
      + [25, 50, 100].map(function(value) {
        return '<option value="' + value + '"' + (state.pageSize === value ? ' selected' : '')
          + '>' + value + '</option>';
      }).join('') + '</select></label></div>';
    const board = '<div class="workflow-kanban-board" role="list" aria-label="'
      + _pfpAttr(t('workflowKanbanTitle', { agent: agentName })) + '">'
      + (snapshot.lanes || []).map(function(lane) {
        const laneCards = cards.filter(function(card) { return card.lane === lane.id; });
        return '<section class="workflow-kanban-lane" data-lane="' + _pfpAttr(lane.id)
          + '" aria-label="' + _pfpAttr(_workflowKanbanLaneLabel(lane)) + '">'
          + '<header><h4>' + escapeHtml(_workflowKanbanLaneLabel(lane))
          + '</h4><span>' + laneCards.length + '</span></header>'
          + '<div class="workflow-kanban-lane-cards" role="list">'
          + (laneCards.length ? laneCards.map(_workflowKanbanCardHtml).join('')
            : '<p class="workflow-kanban-empty">' + escapeHtml(t('workflowKanbanEmptyLane'))
              + '</p>') + '</div></section>';
      }).join('') + '</div>';
    host.innerHTML = toolbar + '<div data-kanban-live class="sr-only" aria-live="polite">'
      + escapeHtml(state.announcement) + '</div>' + board
      + (snapshot.cursor ? '<button type="button" class="workflow-kanban-load-more" data-kanban-more>'
        + escapeHtml(t('workflowKanbanLoadMore')) + '</button>' : '')
      + _workflowKanbanDetailHtml(selectedCard(), snapshot);
    bind();
  }

  function clearHighlights() {
    host.querySelectorAll('.workflow-kanban-card.related').forEach(function(card) {
      card.classList.remove('related');
    });
  }

  function highlightRelations(card) {
    clearHighlights();
    const data = (state.snapshot.cards || []).find(function(value) {
      return value.id === card.dataset.cardId;
    });
    if (!data) return;
    const ids = [].concat((data.relations || {}).parents || [])
      .concat((data.relations || {}).children || [])
      .map(function(taskId) { return data.run_id + ':' + taskId; });
    host.querySelectorAll('.workflow-kanban-card').forEach(function(candidate) {
      if (ids.includes(candidate.dataset.cardId)) candidate.classList.add('related');
    });
  }

  async function requestAction(card, targetLane) {
    const generation = Number((card.summary || {}).generation
      || ((state.snapshot || {}).run || {}).generation || 0);
    const request = {
      conversation_id: conversationId,
      agent_name: agentName,
      run_id: card.run_id,
      task_id: card.task_id || '',
      target_lane: targetLane,
      expected_generation: generation,
    };
    const planned = await _workflowKanbanApi('workflow_kanban_plan_command', request);
    const plan = planned.plan || {};
    if (!plan.executable) {
      throw new Error(plan.message || t('workflowKanbanPlanRejected'));
    }
    if (plan.requires_confirmation
        && !confirm(t('workflowKanbanConfirmAction', { action: plan.message || plan.command }))) {
      return;
    }
    const result = await _workflowKanbanApi('workflow_kanban_execute_command', Object.assign(
      {}, request, { idempotency_key: _workflowKanbanUuid() },
    ));
    if (plan.command === 'open_interaction'
        && typeof toggleConfirmationsPanel === 'function') {
      const panel = document.getElementById('confirmationsPanel');
      if (!panel || panel.style.display === 'none') await toggleConfirmationsPanel();
      else if (typeof loadInteractions === 'function') loadInteractions();
    }
    announce(t('workflowKanbanUpdated'));
    await refresh();
    return result;
  }

  function bind() {
    const snapshot = state.snapshot;
    host.querySelector('[data-kanban-runs]')?.addEventListener('click', function() {
      state.runId = '';
      state.selectedCardId = '';
      state.visibleLimit = state.pageSize;
      refresh().catch(function(error) { addMsg('error', error.message); });
    });
    host.querySelector('[data-kanban-page-size]')?.addEventListener('change', function(event) {
      state.pageSize = Number(event.target.value) || 25;
      state.visibleLimit = state.pageSize;
      refresh().catch(function(error) { addMsg('error', error.message); });
    });
    host.querySelector('[data-kanban-filter]')?.addEventListener('change', function(event) {
      state.filterText = event.target.value || '';
      render();
    });
    host.querySelector('[data-kanban-project]')?.addEventListener('change', function(event) {
      state.projectId = event.target.value || '';
      render();
    });
    host.querySelector('[data-kanban-hide-done]')?.addEventListener('change', function(event) {
      state.hideDone = !!event.target.checked;
      render();
    });
    host.querySelector('[data-kanban-saved-view]')?.addEventListener('change', function(event) {
      const name = event.target.value || '';
      const view = _workflowKanbanReadViews(agentName)[name];
      state.savedView = name;
      if (view) {
        state.filterText = String(view.filterText || '');
        state.projectId = String(view.projectId || '');
        state.hideDone = !!view.hideDone;
      }
      render();
    });
    host.querySelector('[data-kanban-save-view]')?.addEventListener('click', function() {
      const name = String(prompt(t('workflowKanbanSavedViewName')) || '').trim();
      if (!name) return;
      const views = _workflowKanbanReadViews(agentName);
      views[name.slice(0, 80)] = {
        filterText: state.filterText, projectId: state.projectId, hideDone: state.hideDone,
      };
      localStorage.setItem(_workflowKanbanViewsKey(agentName), JSON.stringify(views));
      state.savedView = name.slice(0, 80);
      render();
    });
    host.querySelector('[data-kanban-more]')?.addEventListener('click', function() {
      state.visibleLimit += state.pageSize;
      refresh().catch(function(error) { addMsg('error', error.message); });
    });
    host.querySelectorAll('.workflow-kanban-card').forEach(function(element) {
      const open = function() {
        state.selectedCardId = element.dataset.cardId;
        render();
        host.querySelector('.workflow-kanban-drawer')?.focus();
      };
      element.querySelector('[data-card-open]').addEventListener('click', open);
      element.addEventListener('keydown', function(event) {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault(); open();
        }
      });
      element.addEventListener('focusin', function() { highlightRelations(element); });
      element.addEventListener('mouseenter', function() { highlightRelations(element); });
      element.addEventListener('mouseleave', clearHighlights);
      element.addEventListener('dragstart', function(event) {
        event.dataTransfer.setData('text/plain', element.dataset.cardId);
        event.dataTransfer.effectAllowed = 'move';
      });
    });
    host.querySelectorAll('.workflow-kanban-lane').forEach(function(lane) {
      lane.addEventListener('dragover', function(event) {
        const cardId = event.dataTransfer.getData('text/plain');
        const card = (snapshot.cards || []).find(function(value) { return value.id === cardId; });
        if (card && _workflowKanbanTargets(card).includes(lane.dataset.lane)) {
          event.preventDefault();
          event.dataTransfer.dropEffect = 'move';
        }
      });
      lane.addEventListener('drop', function(event) {
        event.preventDefault();
        const cardId = event.dataTransfer.getData('text/plain');
        const card = (snapshot.cards || []).find(function(value) { return value.id === cardId; });
        if (!card) return;
        requestAction(card, lane.dataset.lane).catch(function(error) {
          addMsg('error', error.message);
        });
      });
    });
    const card = selectedCard();
    if (!card) return;
    host.querySelector('[data-kanban-detail-close]')?.addEventListener('click', function() {
      state.selectedCardId = '';
      render();
    });
    host.querySelector('[data-kanban-open-tasks]')?.addEventListener('click', function() {
      state.runId = card.run_id;
      state.selectedCardId = '';
      refresh().catch(function(error) { addMsg('error', error.message); });
    });
    host.querySelector('[data-kanban-open-graph]')?.addEventListener('click', function() {
      if (typeof options.openGraph === 'function') {
        options.openGraph(card.run_id, card.task_id);
      } else {
        window.dispatchEvent(new CustomEvent('pawflow:workflow-kanban-open-graph', {
          detail: { agent_name: agentName, run_id: card.run_id, task_id: card.task_id },
        }));
      }
    });
    host.querySelector('[data-kanban-propose]')?.addEventListener('click', function() {
      const specification = String(prompt(
        t('workflowKanbanProposalPrompt'), (card.summary || {}).description || card.title || '',
      ) || '').trim();
      if (!specification || typeof cmdPlan !== 'function') return;
      const request = t('workflowKanbanProposalRequest', {
        title: card.title || card.id,
        run: card.run_id,
        task: card.task_id || 'run',
        specification: specification,
      });
      cmdPlan('/plan ' + request, [], '/plan');
    });
    host.querySelectorAll('[data-kanban-command]').forEach(function(button) {
      button.addEventListener('click', function() {
        requestAction(card, button.dataset.kanbanCommand).catch(function(error) {
          addMsg('error', error.message);
        });
      });
    });
    host.querySelector('[data-kanban-apply]')?.addEventListener('click', function() {
      const target = host.querySelector('[data-kanban-target]').value;
      requestAction(card, target).catch(function(error) { addMsg('error', error.message); });
    });
    host.querySelector('[data-kanban-assignment]')?.addEventListener('submit', function(event) {
      event.preventDefault();
      const assignee = event.currentTarget.elements.assignee.value.trim();
      _workflowKanbanApi('workflow_kanban_assign', {
        conversation_id: conversationId, agent_name: agentName,
        run_id: card.run_id, task_id: card.task_id || '', assignee: assignee,
        expected_generation: Number((card.summary || {}).generation || 0),
        idempotency_key: _workflowKanbanUuid(),
      }).then(refresh).catch(function(error) { addMsg('error', error.message); });
    });
    host.querySelector('[data-kanban-comment]')?.addEventListener('submit', function(event) {
      event.preventDefault();
      const body = event.currentTarget.elements.body.value.trim();
      _workflowKanbanApi('workflow_kanban_comment', {
        conversation_id: conversationId, agent_name: agentName,
        run_id: card.run_id, task_id: card.task_id || '', body: body,
        expected_generation: Number((card.summary || {}).generation || 0),
        idempotency_key: _workflowKanbanUuid(),
      }).then(refresh).catch(function(error) { addMsg('error', error.message); });
    });
    host.querySelector('[data-kanban-attachment]')?.addEventListener('submit', async function(event) {
      event.preventDefault();
      const file = event.currentTarget.elements.file.files[0];
      if (!file || typeof uploadFileToStore !== 'function') return;
      try {
        const uploaded = await uploadFileToStore(file);
        await _workflowKanbanApi('workflow_kanban_attach', {
          conversation_id: conversationId, agent_name: agentName,
          run_id: card.run_id, task_id: card.task_id || '', file_id: uploaded.file_id,
          label: file.name, expected_generation: Number((card.summary || {}).generation || 0),
          idempotency_key: _workflowKanbanUuid(),
        });
        await refresh();
      } catch (error) { addMsg('error', error.message); }
    });
    host.querySelector('[data-kanban-review]')?.addEventListener('submit', function(event) {
      event.preventDefault();
      _workflowKanbanApi('workflow_kanban_review', {
        conversation_id: conversationId, agent_name: agentName,
        run_id: card.run_id, task_id: card.task_id || '',
        decision: event.currentTarget.elements.decision.value,
        comment: event.currentTarget.elements.comment.value.trim(),
        expected_generation: Number((card.summary || {}).generation || 0),
        idempotency_key: _workflowKanbanUuid(),
      }).then(refresh).catch(function(error) { addMsg('error', error.message); });
    });
  }

  async function refresh() {
    if (state.destroyed) return;
    if (state.refreshing) {
      state.refreshPending = true;
      return;
    }
    state.refreshing = true;
    state.refreshPending = false;
    try {
      state.snapshot = await _workflowKanbanApi('workflow_kanban_snapshot', {
        conversation_id: conversationId,
        agent_name: agentName,
        run_id: state.runId,
        limit: state.visibleLimit,
      });
      render();
    } finally {
      state.refreshing = false;
      if (state.refreshPending) scheduleRefresh(0);
      else scheduleRefresh();
    }
  }

  function relevantEvent(event) {
    const data = (event && event.detail) || {};
    if (data.conversation_id && data.conversation_id !== conversationId) return false;
    if (data.agent_name && String(data.agent_name).toLowerCase()
        !== String(agentName).toLowerCase()) return false;
    if (state.runId && data.run_id && data.run_id !== state.runId) return false;
    return true;
  }

  function onInvalidation(event) {
    if (relevantEvent(event)) scheduleRefresh(0);
  }

  function onVisibility() {
    if (document.visibilityState !== 'hidden') scheduleRefresh(0);
  }

  window.addEventListener('pawflow:workflow-progress', onInvalidation);
  window.addEventListener('pawflow:workflow-kanban-updated', onInvalidation);
  document.addEventListener('visibilitychange', onVisibility);
  refresh().catch(function(error) {
    host.innerHTML = '<p class="workflow-kanban-error">' + escapeHtml(error.message) + '</p>';
  });

  return {
    refresh: refresh,
    destroy: function() {
      state.destroyed = true;
      if (state.refreshTimer) clearTimeout(state.refreshTimer);
      window.removeEventListener('pawflow:workflow-progress', onInvalidation);
      window.removeEventListener('pawflow:workflow-kanban-updated', onInvalidation);
      document.removeEventListener('visibilitychange', onVisibility);
    },
  };
}

function showWorkflowKanban(agentName, runId) {
  if (!conversationId) { addMsg('error', t('noConv')); return; }
  const existing = document.getElementById('workflowKanbanOverlay');
  if (existing && typeof existing._workflowKanbanClose === 'function') {
    existing._workflowKanbanClose();
  } else if (existing) existing.remove();
  const previousFocus = document.activeElement;
  const overlay = document.createElement('div');
  overlay.id = 'workflowKanbanOverlay';
  overlay.className = 'exec-overlay';
  overlay.innerHTML = '<div class="exec-dialog workflow-kanban-dialog" role="dialog"'
    + ' aria-modal="true" aria-labelledby="workflow-kanban-title" tabindex="-1">'
    + '<header class="workflow-kanban-dialog-head"><h3 id="workflow-kanban-title">'
    + escapeHtml(t('workflowKanbanTitle', { agent: agentName })) + '</h3>'
    + '<button type="button" data-close aria-label="' + _pfpAttr(t('close'))
    + '">&times;</button></header><div data-kanban-host></div></div>';
  document.body.appendChild(overlay);
  const dialog = overlay.querySelector('[role="dialog"]');
  let controller;
  const close = function() {
    if (controller) controller.destroy();
    document.removeEventListener('keydown', onKey);
    overlay.remove();
    if (previousFocus && typeof previousFocus.focus === 'function') previousFocus.focus();
  };
  overlay._workflowKanbanClose = close;
  const openGraph = function(selectedRunId, taskId) {
    close();
    if (typeof showWorkflowRunInspector === 'function') {
      showWorkflowRunInspector(agentName, {
        runId: selectedRunId, view: 'graph', taskId: taskId,
      });
    }
  };
  const onKey = function(event) {
    if (event.key === 'Escape') { close(); return; }
    if (event.key !== 'Tab') return;
    const focusable = Array.from(dialog.querySelectorAll(
      'button:not([disabled]),select:not([disabled]),input:not([disabled]),textarea:not([disabled]),[tabindex="0"]',
    )).filter(function(element) { return element.offsetParent !== null; });
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault(); last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault(); first.focus();
    }
  };
  overlay.querySelector('[data-close]').addEventListener('click', close);
  document.addEventListener('keydown', onKey);
  controller = mountWorkflowKanban(
    overlay.querySelector('[data-kanban-host]'), agentName, runId, { openGraph: openGraph },
  );
  dialog.focus();
}

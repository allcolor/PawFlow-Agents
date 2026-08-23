// ── Agent Memories ──────────────────────────────────────────────
let _memoryCache = [];
let _memoryAgentFilter = null;  // null = all
let _memoryDraftFilter = false;
let _memoryVisibleCache = [];
let _memoryAgents = [];
let _memoryPromotionInFlight = new Set();

function cmdShowMemories() {
  _cognitiveLoadResources(function(resources) {
    _memoryAgents = _cognitiveAgentNames(resources);
    if (_memoryAgentFilter && _memoryAgents.indexOf(_memoryAgentFilter) < 0) {
      _memoryAgentFilter = null;
    }
    _loadMemoriesForPanel();
  }, function(error) { addMsg('error', t('failedLoadMemories', { error: error.message })); });
}

function _loadMemoriesForPanel() {
  const body = {};
  if (_memoryAgentFilter !== null) body.agent_name = _memoryAgentFilter;
  action$('list_memories', body).subscribe({
    next: (data) => {
      _memoryCache = data.memories || [];
      showMemoryOverlay(_memoryCache);
    },
    error: (e) => addMsg('error', t('failedLoadMemories', { error: e.message })),
  });
}

function showMemoryOverlay(memories) {
  let overlay = document.getElementById('memoryOverlay');
  if (overlay) overlay.remove();
  overlay = document.createElement('div');
  overlay.id = 'memoryOverlay';
  overlay.className = 'memory-overlay';

  const draftCount = memories.filter(m => !!m.skill_draft).length;
  const visibleMemories = _memoryDraftFilter
    ? memories.filter(m => !!m.skill_draft) : memories;
  _memoryVisibleCache = visibleMemories;

  // Filter dropdown
  let filterHtml = '<select class="memory-filter" id="memAgentFilter" onchange="memFilterChanged()">';
  filterHtml += '<option value="__all__"' + (_memoryAgentFilter === null ? ' selected' : '') + '>' + t('all') + '</option>';
  filterHtml += '<option value=""' + (_memoryAgentFilter === '' ? ' selected' : '') + '>' + t('globalOnly') + '</option>';
  for (const a of _memoryAgents) {
    if (a) filterHtml += '<option value="' + escapeHtml(a) + '"' + (_memoryAgentFilter === a ? ' selected' : '') + '>' + escapeHtml(a) + '</option>';
  }
  filterHtml += '</select>';

  // Build memory rows
  let msgsHtml = '';
  if (visibleMemories.length === 0) {
    msgsHtml = '<div class="memory-empty">'
      + t(_memoryDraftFilter ? 'noSkillDrafts' : 'noMemoriesStored') + '</div>';
  } else {
    visibleMemories.forEach((m, i) => {
      // Scope badge: private (agent+conv), conversation, agent, global
      let scopeBadge;
      if (m.agent && m.conversation_id) {
        scopeBadge = '<span class="memory-scope private">\u{1F512} ' + escapeHtml(m.agent) + '</span>';
      } else if (m.conversation_id) {
        scopeBadge = '<span class="memory-scope conversation">\u{1F4AC} ' + escapeHtml(t('conversationShort')) + '</span>';
      } else if (m.agent) {
        scopeBadge = '<span class="memory-scope agent">\u{1F916} ' + escapeHtml(m.agent) + '</span>';
      } else {
        scopeBadge = '<span class="memory-scope global">\u{1F310} ' + escapeHtml(t('globalLower')) + '</span>';
      }
      const tagsHtml = (m.tags || []).map(tag =>
        '<span class="memory-tag">' + escapeHtml(tag) + '</span>'
      ).join('');
      const age = _formatAge(m.updated_at || m.created_at);
      const editBtn = '<button class="memory-icon-button edit" onclick="event.stopPropagation();memEdit(' + i + ')" title="' + escapeHtml(t('contextEdit')) + '">&#9998;</button>';
      const delBtn = '<button class="memory-icon-button delete" onclick="event.stopPropagation();memDelete(\'' + m.id + '\')" title="' + escapeHtml(t('delete')) + '">&#128465;</button>';
      const promoteBtn = m.skill_draft
        ? '<button class="memory-icon-button promote" onclick="event.stopPropagation();memPromoteDraft(' + i + ', false, this)" title="' + escapeHtml(t('promoteSkillDraft')) + '">' + escapeHtml(t('promote')) + '</button>'
        : '';
      const text = escapeHtml(m.text || '');
      msgsHtml += '<div id="mem-row-' + i + '" class="memory-card' + (m.skill_draft ? ' skill-draft' : '') + '" onclick="this.querySelector(\'.memory-full\')&&(this.querySelector(\'.memory-full\').style.display=this.querySelector(\'.memory-full\').style.display===\'block\'?\'none\':\'block\')">'
        + '<div class="memory-card-head">' + scopeBadge + tagsHtml
        + '<span class="memory-age">' + escapeHtml(age) + '</span>'
        + promoteBtn + editBtn + delBtn + '</div>'
        + '<div class="memory-preview">' + text.slice(0, 200) + '</div>'
        + '<div class="memory-full">' + text + '</div>'
        + '</div>';
    });
  }

  overlay.innerHTML = '<div class="cog-dialog memory-dialog">'
    + '<div class="cog-head">'
    + '<h3 class="memory-dialog-title">' + escapeHtml(t('memories')) + '</h3>'
    + '<span class="memory-count">' + escapeHtml(t('entriesCount', { n: visibleMemories.length })) + '</span>'
    + filterHtml
    + '<button class="memory-toolbar-button' + (_memoryDraftFilter ? ' active' : '') + '" onclick="memToggleDraftFilter()">' + escapeHtml(t('skillDrafts')) + ' (' + draftCount + ')</button>'
    + '<button class="memory-toolbar-button primary" onclick="memAddNew()">+ ' + escapeHtml(t('add')) + '</button>'
    + '<button class="cog-close" onclick="document.getElementById(\'memoryOverlay\').remove()">&times;</button>'
    + '</div>'
    + '<div class="memory-list" id="mem-list">' + msgsHtml + '</div>'
    + '</div>';
  document.body.appendChild(overlay);

}

function _memoryAgentOptions(selected) {
  return '<option value=""' + (!selected ? ' selected' : '') + '>' + escapeHtml(t('globalOnly')) + '</option>'
    + _memoryAgents.map(function(agent) {
      return '<option value="' + escapeHtml(agent) + '"' + (agent === selected ? ' selected' : '')
        + '>' + escapeHtml(agent) + '</option>';
    }).join('');
}

function _formatAge(ts) {
  const s = Math.floor(Date.now() / 1000 - ts);
  if (s < 60) return 'just now';
  if (s < 3600) return Math.floor(s / 60) + 'm ago';
  if (s < 86400) return Math.floor(s / 3600) + 'h ago';
  return Math.floor(s / 86400) + 'd ago';
}

function memFilterChanged() {
  const val = document.getElementById('memAgentFilter').value;
  _memoryAgentFilter = val === '__all__' ? null : val;
  cmdShowMemories();
}

function memToggleDraftFilter() {
  _memoryDraftFilter = !_memoryDraftFilter;
  showMemoryOverlay(_memoryCache);
}

function memDelete(memId) {
  if (!confirm(t('memoryDeleteConfirm'))) return;
  action$('delete_memory', { memory_id: memId }).subscribe({
    next: (data) => {
      if (data.error) { addMsg('error', data.error); return; }
      cmdShowMemories();
    },
    error: (e) => addMsg('error', e.message),
  });
}

function _skillDraftInstructions(draft) {
  let body = '# ' + draft.description + '\n\n';
  if (draft.trigger) body += '## Trigger\n\n' + draft.trigger + '\n\n';
  body += '## Procedure\n\n';
  (draft.steps || []).forEach((step, i) => { body += (i + 1) + '. ' + step + '\n'; });
  return body;
}

function memPromoteDraft(idx, force, sourceButton) {
  const memory = _memoryVisibleCache[idx];
  const draft = memory && memory.skill_draft;
  if (!memory || !draft || !memory.conversation_id) {
    addMsg('error', t('skillDraftInvalid'));
    return;
  }
  if (_memoryPromotionInFlight.has(memory.id)) return;
  const progress = showOperationProgress({
    title: t('promotingSkill', { name: draft.name }),
    phase: t('reviewingSkillDraft'),
    detail: t('operationPleaseWait'),
  });
  if (!progress) return;
  _memoryPromotionInFlight.add(memory.id);
  if (sourceButton) {
    sourceButton.disabled = true;
    sourceButton.setAttribute('aria-busy', 'true');
  }
  const release = function() {
    _memoryPromotionInFlight.delete(memory.id);
    if (sourceButton && sourceButton.isConnected) {
      sourceButton.disabled = false;
      sourceButton.removeAttribute('aria-busy');
    }
  };
  const fail = function(message, keepLocked) {
    if (!keepLocked) release();
    progress.fail(message);
    addMsg('error', message);
  };
  const payload = {
    resource_type: 'skill',
    name: draft.name,
    scope: 'conversation',
    conversation_id: memory.conversation_id,
    data: {
      description: draft.description,
      instructions: _skillDraftInstructions(draft),
      metadata: { created_from: 'skill-draft', memory_id: memory.id },
    },
  };
  if (force) payload.force = true;
  action$('create_resource', payload).subscribe({
    next: (data) => {
      if (data && data.requires_confirmation) {
        progress.close();
        release();
        _showSkillReviewConfirm(data.review, data.message,
          function() { memPromoteDraft(idx, true); });
        return;
      }
      if (data.error) { fail(data.error); return; }
      progress.setPhase(t('cleaningSkillDraft'), t('operationPleaseWait'));
      action$('delete_memory', { memory_id: memory.id }).subscribe({
        next: (deleted) => {
          if (deleted.error || !deleted.deleted) {
            fail(deleted.error || t('skillDraftCleanupFailed'), true);
            return;
          }
          release();
          progress.close();
          addMsg('system', t('skillDraftPromoted', { name: draft.name }));
          notifyResourceChanged('skill', 'create', {
            name: draft.name, scope: 'conversation',
          });
          loadResources();
          cmdShowMemories();
        },
        error: (e) => fail(e.message, true),
      });
    },
    error: (e) => fail(e.message),
  });
}

function memEdit(idx) {
  const m = _memoryVisibleCache[idx];
  if (!m) return;
  const row = document.getElementById('mem-row-' + idx);
  if (!row) return;
  row.innerHTML = '<div class="memory-edit">'
    + '<textarea class="memory-edit-field" id="mem-edit-text">' + escapeHtml(m.text || '') + '</textarea>'
    + '<div class="memory-edit-row">'
    + '<label>' + escapeHtml(t('tags')) + ':</label>'
    + '<input class="memory-edit-field" id="mem-edit-tags" value="' + escapeHtml((m.tags || []).join(', ')) + '">'
    + '<label>' + escapeHtml(t('agent')) + ':</label>'
    + '<select class="memory-edit-field" id="mem-edit-agent">'
    + _memoryAgentOptions(m.agent || '') + '</select>'
    + '<button class="memory-toolbar-button active" onclick="memSaveEdit(\'' + m.id + '\')">' + escapeHtml(t('contextSave')) + '</button>'
    + '<button class="memory-toolbar-button" onclick="cmdShowMemories()">' + escapeHtml(t('contextCancel')) + '</button>'
    + '</div></div>';
}

function memSaveEdit(memId) {
  const text = document.getElementById('mem-edit-text').value.trim();
  const tagsRaw = document.getElementById('mem-edit-tags').value;
  const agent = document.getElementById('mem-edit-agent').value.trim();
  const tags = tagsRaw.split(',').map(t => t.trim()).filter(t => t);
  action$('edit_memory', { memory_id: memId, text, tags, agent }).subscribe({
    next: () => cmdShowMemories(),
    error: (e) => addMsg('error', e.message),
  });
}

function memAddNew() {
  const list = document.getElementById('mem-list');
  if (!list) return;
  const form = document.createElement('div');
  form.className = 'memory-card memory-edit';
  form.innerHTML = '<textarea class="memory-edit-field" id="mem-new-text" placeholder="' + escapeHtml(t('memoryTextPlaceholder')) + '"></textarea>'
    + '<div class="memory-edit-row">'
    + '<label>' + escapeHtml(t('tags')) + ':</label>'
    + '<input class="memory-edit-field" id="mem-new-tags" placeholder="tag1, tag2">'
    + '<label>' + escapeHtml(t('agent')) + ':</label>'
    + '<select class="memory-edit-field" id="mem-new-agent">'
    + _memoryAgentOptions(_memoryAgentFilter !== null ? _memoryAgentFilter
      : (_memoryAgents.indexOf(selectedAgent) >= 0 ? selectedAgent : '')) + '</select>'
    + '<button class="memory-toolbar-button active" onclick="memSaveNew()">' + escapeHtml(t('add')) + '</button>'
    + '</div>';
  list.insertBefore(form, list.firstChild);
  document.getElementById('mem-new-text').focus();
}

function memSaveNew() {
  const text = document.getElementById('mem-new-text').value.trim();
  if (!text) return;
  const tagsRaw = document.getElementById('mem-new-tags').value;
  const agent = document.getElementById('mem-new-agent').value.trim();
  const tags = tagsRaw.split(',').map(t => t.trim()).filter(t => t);
  action$('add_memory', { text, tags, agent }).subscribe({
    next: () => cmdShowMemories(),
    error: (e) => addMsg('error', e.message),
  });
}

// ── Secrets & Variables ──────────────────────────────────────────
function cmdAddSecret(name, value) {
  action$('add_secret', { key: name, value: value, conversation_id: conversationId }).subscribe({
    next: (data) => {
      if (data.error) { addMsg('error', data.error); return; }
      addMsg('system', t('secretAdded', { name, ref: data.key || name, short: name }));
    },
    error: (e) => addMsg('error', t('failed', { error: e.message })),
  });
}

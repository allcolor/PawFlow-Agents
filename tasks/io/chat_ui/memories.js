// ── Agent Memories ──────────────────────────────────────────────
let _memoryCache = [];
let _memoryAgentFilter = null;  // null = all
let _memoryDraftFilter = false;
let _memoryVisibleCache = [];
let _memoryAgents = [];

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
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;z-index:9999';

  const draftCount = memories.filter(m => !!m.skill_draft).length;
  const visibleMemories = _memoryDraftFilter
    ? memories.filter(m => !!m.skill_draft) : memories;
  _memoryVisibleCache = visibleMemories;

  // Filter dropdown
  let filterHtml = '<select id="memAgentFilter" onchange="memFilterChanged()" style="background:#1e1e3a;color:#c0c0d0;border:1px solid #444;border-radius:6px;padding:3px 8px;font-size:12px">';
  filterHtml += '<option value="__all__"' + (_memoryAgentFilter === null ? ' selected' : '') + '>' + t('all') + '</option>';
  filterHtml += '<option value=""' + (_memoryAgentFilter === '' ? ' selected' : '') + '>' + t('globalOnly') + '</option>';
  for (const a of _memoryAgents) {
    if (a) filterHtml += '<option value="' + a + '"' + (_memoryAgentFilter === a ? ' selected' : '') + '>' + a + '</option>';
  }
  filterHtml += '</select>';

  // Build memory rows
  let msgsHtml = '';
  if (visibleMemories.length === 0) {
    msgsHtml = '<div style="color:#6c6c8a;text-align:center;padding:20px">'
      + t(_memoryDraftFilter ? 'noSkillDrafts' : 'noMemoriesStored') + '</div>';
  } else {
    visibleMemories.forEach((m, i) => {
      // Scope badge: private (agent+conv), conversation, agent, global
      let scopeBadge;
      if (m.agent && m.conversation_id) {
        scopeBadge = '<span style="background:#5a1a1a;color:#ff6b6b;padding:1px 6px;border-radius:6px;font-size:10px;font-weight:600">\u{1F512} ' + m.agent + '</span>';
      } else if (m.conversation_id) {
        scopeBadge = '<span style="background:#1a3a5a;color:#74b9ff;padding:1px 6px;border-radius:6px;font-size:10px;font-weight:600">\u{1F4AC} ' + t('conversationShort') + '</span>';
      } else if (m.agent) {
        scopeBadge = '<span style="background:#1e3a5f;color:#4fc3f7;padding:1px 6px;border-radius:6px;font-size:10px;font-weight:600">\u{1F916} ' + m.agent + '</span>';
      } else {
        scopeBadge = '<span style="background:#1b4332;color:#52b788;padding:1px 6px;border-radius:6px;font-size:10px;font-weight:600">\u{1F310} ' + t('globalLower') + '</span>';
      }
      const tagsHtml = (m.tags || []).map(t =>
        '<span style="background:#2a2a4a;color:#a0a0c0;padding:1px 5px;border-radius:4px;font-size:10px;margin-left:3px">' + t + '</span>'
      ).join('');
      const age = _formatAge(m.updated_at || m.created_at);
      const editBtn = '<button onclick="event.stopPropagation();memEdit(' + i + ')" style="background:none;border:none;color:#4fc3f7;cursor:pointer;font-size:13px;padding:0 3px" title="' + escapeHtml(t('contextEdit')) + '">&#9998;</button>';
      const delBtn = '<button onclick="event.stopPropagation();memDelete(\'' + m.id + '\')" style="background:none;border:none;color:#e74c3c;cursor:pointer;font-size:13px;padding:0 3px" title="' + escapeHtml(t('delete')) + '">&#128465;</button>';
      const promoteBtn = m.skill_draft
        ? '<button onclick="event.stopPropagation();memPromoteDraft(' + i + ')" style="background:#1b4332;color:#52b788;border:1px solid #2d6a4f;border-radius:4px;cursor:pointer;font-size:10px;padding:2px 7px" title="' + escapeHtml(t('promoteSkillDraft')) + '">' + escapeHtml(t('promote')) + '</button>'
        : '';
      const text = (m.text || '').replace(/</g, '&lt;').replace(/>/g, '&gt;');
      msgsHtml += '<div id="mem-row-' + i + '" style="padding:6px 8px;border-bottom:1px solid #222;cursor:pointer' + (m.skill_draft ? ';background:#10271f' : '') + '" onclick="this.querySelector(\'.mem-full\')&&(this.querySelector(\'.mem-full\').style.display=this.querySelector(\'.mem-full\').style.display===\'block\'?\'none\':\'block\')">'
        + '<div style="display:flex;align-items:center;gap:4px">' + scopeBadge + tagsHtml
        + '<span style="color:#6c6c8a;font-size:10px;margin-left:auto">' + age + '</span>'
        + promoteBtn + editBtn + delBtn + '</div>'
        + '<div style="color:#c0c0d0;font-size:12px;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">' + text.slice(0, 200) + '</div>'
        + '<div class="mem-full" style="display:none;color:#a0a0c0;font-size:12px;margin-top:4px;white-space:pre-wrap;word-break:break-word;max-height:200px;overflow-y:auto">' + text + '</div>'
        + '</div>';
    });
  }

  overlay.innerHTML = '<div class="cog-dialog" style="background:#1a1a2e;border:1px solid #333;border-radius:12px;padding:20px;max-width:700px;width:90%;max-height:80vh;display:flex;flex-direction:column">'
    + '<div class="cog-head">'
    + '<h3 style="margin:0;color:#e0e0e0;font-size:16px">' + escapeHtml(t('memories')) + '</h3>'
    + '<span style="color:#6c6c8a;font-size:12px">' + escapeHtml(t('entriesCount', { n: visibleMemories.length })) + '</span>'
    + filterHtml
    + '<button onclick="memToggleDraftFilter()" style="background:' + (_memoryDraftFilter ? '#1b4332' : '#2a2a4a') + ';color:' + (_memoryDraftFilter ? '#52b788' : '#a0a0c0') + ';border:1px solid ' + (_memoryDraftFilter ? '#2d6a4f' : '#444') + ';border-radius:6px;padding:3px 8px;cursor:pointer;font-size:11px">' + escapeHtml(t('skillDrafts')) + ' (' + draftCount + ')</button>'
    + '<button onclick="memAddNew()" style="background:#1e3a5f;color:#4fc3f7;border:none;border-radius:6px;padding:3px 10px;cursor:pointer;font-size:11px;font-weight:600;margin-left:auto">+ ' + escapeHtml(t('add')) + '</button>'
    + '<button class="cog-close" onclick="document.getElementById(\'memoryOverlay\').remove()">&times;</button>'
    + '</div>'
    + '<div id="mem-list" style="flex:1;overflow-y:auto;border:1px solid #222;border-radius:8px;background:#0d1117">' + msgsHtml + '</div>'
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

function memPromoteDraft(idx, force) {
  const memory = _memoryVisibleCache[idx];
  const draft = memory && memory.skill_draft;
  if (!memory || !draft || !memory.conversation_id) {
    addMsg('error', t('skillDraftInvalid'));
    return;
  }
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
        _showSkillReviewConfirm(data.review, data.message,
          function() { memPromoteDraft(idx, true); });
        return;
      }
      if (data.error) { addMsg('error', data.error); return; }
      action$('delete_memory', { memory_id: memory.id }).subscribe({
        next: (deleted) => {
          if (deleted.error || !deleted.deleted) {
            addMsg('error', deleted.error || t('skillDraftCleanupFailed'));
            return;
          }
          addMsg('system', t('skillDraftPromoted', { name: draft.name }));
          notifyResourceChanged('skill', 'create', {
            name: draft.name, scope: 'conversation',
          });
          loadResources();
          cmdShowMemories();
        },
        error: (e) => addMsg('error', e.message),
      });
    },
    error: (e) => addMsg('error', e.message),
  });
}

function memEdit(idx) {
  const m = _memoryVisibleCache[idx];
  if (!m) return;
  const row = document.getElementById('mem-row-' + idx);
  if (!row) return;
  row.innerHTML = '<div style="padding:4px">'
    + '<textarea id="mem-edit-text" style="width:100%;min-height:60px;background:#0d1117;color:#c0c0d0;border:1px solid #444;border-radius:4px;padding:4px;font-size:12px;resize:vertical">' + (m.text || '').replace(/</g, '&lt;').replace(/>/g, '&gt;') + '</textarea>'
    + '<div style="display:flex;gap:6px;margin-top:4px;align-items:center">'
    + '<label style="color:#6c6c8a;font-size:11px">' + escapeHtml(t('tags')) + ':</label>'
    + '<input id="mem-edit-tags" value="' + (m.tags || []).join(', ') + '" style="flex:1;background:#0d1117;color:#c0c0d0;border:1px solid #444;border-radius:4px;padding:2px 6px;font-size:11px">'
    + '<label style="color:#6c6c8a;font-size:11px">' + escapeHtml(t('agent')) + ':</label>'
    + '<select id="mem-edit-agent" style="background:#0d1117;color:#c0c0d0;border:1px solid #444;border-radius:4px;padding:2px 6px;font-size:11px">'
    + _memoryAgentOptions(m.agent || '') + '</select>'
    + '<button onclick="memSaveEdit(\'' + m.id + '\')" style="background:#1b4332;color:#52b788;border:none;border-radius:4px;padding:3px 10px;cursor:pointer;font-size:11px">' + escapeHtml(t('contextSave')) + '</button>'
    + '<button onclick="cmdShowMemories()" style="background:#333;color:#aaa;border:none;border-radius:4px;padding:3px 10px;cursor:pointer;font-size:11px">' + escapeHtml(t('contextCancel')) + '</button>'
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
  form.style.cssText = 'padding:8px;border-bottom:1px solid #444;background:#1a1a2e';
  form.innerHTML = '<textarea id="mem-new-text" placeholder="' + t('memoryTextPlaceholder') + '" style="width:100%;min-height:50px;background:#0d1117;color:#c0c0d0;border:1px solid #444;border-radius:4px;padding:4px;font-size:12px;resize:vertical"></textarea>'
    + '<div style="display:flex;gap:6px;margin-top:4px;align-items:center">'
    + '<label style="color:#6c6c8a;font-size:11px">' + t('tags') + ':</label>'
    + '<input id="mem-new-tags" placeholder="tag1, tag2" style="flex:1;background:#0d1117;color:#c0c0d0;border:1px solid #444;border-radius:4px;padding:2px 6px;font-size:11px">'
    + '<label style="color:#6c6c8a;font-size:11px">' + t('agent') + ':</label>'
    + '<select id="mem-new-agent" style="background:#0d1117;color:#c0c0d0;border:1px solid #444;border-radius:4px;padding:2px 6px;font-size:11px">'
    + _memoryAgentOptions(_memoryAgentFilter !== null ? _memoryAgentFilter
      : (_memoryAgents.indexOf(selectedAgent) >= 0 ? selectedAgent : '')) + '</select>'
    + '<button onclick="memSaveNew()" style="background:#1b4332;color:#52b788;border:none;border-radius:4px;padding:3px 10px;cursor:pointer;font-size:11px">' + t('add') + '</button>'
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

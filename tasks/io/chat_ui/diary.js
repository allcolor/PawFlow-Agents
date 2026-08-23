// ── Agent Diary ─────────────────────────────────────────────────
let _diaryCache = [];
let _diaryTypeFilter = null;  // null = all
let _diaryAgent = '';

function cmdShowDiary() {
  _cognitiveLoadResources(function(data) {
    const agents = _cognitiveAgentNames(data);
    if (!agents.length) { addMsg('error', t('noAgents')); return; }
    if (agents.indexOf(_diaryAgent) < 0) {
      _diaryAgent = agents.indexOf(selectedAgent) >= 0 ? selectedAgent : agents[0];
    }
    const args = { agent_name: _diaryAgent, limit: 50 };
    if (_diaryTypeFilter) args.type = _diaryTypeFilter;
    action$('diary_list', args).subscribe({
      next: function(result) {
        if (result.error) { addMsg('error', result.error); return; }
        _diaryCache = result.entries || [];
        showDiaryOverlay(_diaryCache, _diaryAgent, agents);
      },
      error: function(error) { addMsg('error', t('failedLoadDiary', { error: error.message })); },
    });
  }, function(error) {
    addMsg('error', t('failedLoadDiary', { error: error.message }));
  });
}

function showDiaryOverlay(entries, agentName, agents) {
  let overlay = document.getElementById('diaryOverlay');
  if (overlay) overlay.remove();
  overlay = document.createElement('div');
  overlay.id = 'diaryOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:color-mix(in srgb, var(--pf-shadow) 70%, transparent);display:flex;align-items:center;justify-content:center;z-index:9999';

  // Type filter dropdown
  const types = ['observation', 'decision', 'learning', 'reflection'];
  let filterHtml = '<select id="diaryTypeFilter" onchange="diaryFilterChanged()" style="background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);border-radius:6px;padding:3px 8px;font-size:12px">';
  filterHtml += '<option value="__all__"' + (_diaryTypeFilter === null ? ' selected' : '') + '>' + t('allTypes') + '</option>';
  for (const t of types) {
    filterHtml += '<option value="' + t + '"' + (_diaryTypeFilter === t ? ' selected' : '') + '>' + t + '</option>';
  }
  filterHtml += '</select>';

  // Type badge colors
  function typeBadge(type) {
    const colors = {
      observation: { bg: 'color-mix(in srgb, var(--pf-success) 18%, var(--pf-panel))', fg: 'var(--pf-success)' },
      decision:    { bg: 'color-mix(in srgb, var(--pf-danger) 18%, var(--pf-panel))', fg: 'var(--pf-danger)' },
      learning:    { bg: 'color-mix(in srgb, var(--pf-accent) 18%, var(--pf-panel))', fg: 'var(--pf-accent)' },
      reflection:  { bg: 'color-mix(in srgb, var(--pf-warning) 18%, var(--pf-panel))', fg: 'var(--pf-warning)' },
    };
    const c = colors[type] || { bg: 'var(--pf-sidebar)', fg: 'var(--pf-muted)' };
    return '<span style="background:' + c.bg + ';color:' + c.fg + ';padding:1px 6px;border-radius:6px;font-size:10px;font-weight:600">' + (type || '?') + '</span>';
  }

  // Build entry rows
  let rowsHtml = '';
  if (entries.length === 0) {
    rowsHtml = '<div style="color:var(--pf-muted);text-align:center;padding:20px">' + t('noDiaryEntries') + '</div>';
  } else {
    entries.forEach((e, i) => {
      const text = escapeHtml(e.text || '');
      rowsHtml += '<div id="diary-row-' + i + '" style="padding:6px 8px;border-bottom:1px solid var(--pf-border);cursor:pointer" onclick="this.querySelector(\'.diary-full\')&&(this.querySelector(\'.diary-full\').style.display=this.querySelector(\'.diary-full\').style.display===\'block\'?\'none\':\'block\')">'
        + '<div style="display:flex;align-items:center;gap:4px">' + typeBadge(e.type)
        + '<span style="color:var(--pf-muted);font-size:10px;margin-left:auto">' + escapeHtml(e.ts ? new Date(e.ts * 1000).toLocaleString() : '') + '</span>'
        + '</div>'
        + '<div style="color:var(--pf-text);font-size:12px;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">' + text.slice(0, 200) + '</div>'
        + '<div class="diary-full" style="display:none;color:var(--pf-muted);font-size:12px;margin-top:4px;white-space:pre-wrap;word-break:break-word;max-height:200px;overflow-y:auto">' + text + '</div>'
        + '</div>';
    });
  }

  overlay.innerHTML = '<div class="cog-dialog" style="background:var(--pf-panel);border:1px solid var(--pf-border);border-radius:12px;padding:20px;max-width:700px;width:90%;max-height:80vh;display:flex;flex-direction:column">'
    + '<div class="cog-head">'
    + '<h3 style="margin:0;color:var(--pf-text);font-size:16px">' + escapeHtml(t('diaryTitle', { agent: agentName || '?' })) + '</h3>'
    + '<select id="diaryAgent" onchange="diaryAgentChanged()" style="background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);border-radius:6px;padding:3px 7px">'
    + agents.map(function(agent) { return '<option value="' + escapeHtml(agent) + '"'
      + (agent === agentName ? ' selected' : '') + '>' + escapeHtml(agent) + '</option>'; }).join('') + '</select>'
    + '<span style="color:var(--pf-muted);font-size:12px">' + entries.length + ' entries</span>'
    + filterHtml
    + '<button onclick="diaryAddNew()" style="background:color-mix(in srgb, var(--pf-accent) 18%, var(--pf-panel));color:var(--pf-accent);border:none;border-radius:6px;padding:3px 10px;cursor:pointer;font-size:11px;font-weight:600;margin-left:auto">+ ' + escapeHtml(t('add')) + '</button>'
    + '<button class="cog-close" onclick="document.getElementById(\'diaryOverlay\').remove()">&times;</button>'
    + '</div>'
    + '<div id="diary-list" style="flex:1;overflow-y:auto;border:1px solid var(--pf-border);border-radius:8px;background:var(--pf-code-bg)">' + rowsHtml + '</div>'
    + '</div>';
  document.body.appendChild(overlay);

}

function diaryAgentChanged() {
  _diaryAgent = document.getElementById('diaryAgent').value;
  cmdShowDiary();
}

function diaryFilterChanged() {
  const val = document.getElementById('diaryTypeFilter').value;
  _diaryTypeFilter = val === '__all__' ? null : val;
  cmdShowDiary();
}

function diaryAddNew() {
  const list = document.getElementById('diary-list');
  if (!list) return;
  const form = document.createElement('div');
  form.style.cssText = 'padding:8px;border-bottom:1px solid var(--pf-border);background:var(--pf-panel)';
  form.innerHTML = '<textarea id="diary-new-text" placeholder="' + t('diaryEntryPlaceholder') + '" style="width:100%;min-height:50px;background:var(--pf-code-bg);color:var(--pf-text);border:1px solid var(--pf-border);border-radius:4px;padding:4px;font-size:12px;resize:vertical"></textarea>'
    + '<div style="display:flex;gap:6px;margin-top:4px;align-items:center">'
    + '<label style="color:var(--pf-muted);font-size:11px">' + t('type') + ':</label>'
    + '<select id="diary-new-type" style="background:var(--pf-code-bg);color:var(--pf-text);border:1px solid var(--pf-border);border-radius:4px;padding:2px 6px;font-size:11px">'
    + '<option value="observation">observation</option>'
    + '<option value="decision">decision</option>'
    + '<option value="learning">learning</option>'
    + '<option value="reflection">reflection</option>'
    + '</select>'
    + '<label style="color:var(--pf-muted);font-size:11px">' + t('tags') + ':</label>'
    + '<input id="diary-new-tags" placeholder="tag1, tag2" style="flex:1;background:var(--pf-code-bg);color:var(--pf-text);border:1px solid var(--pf-border);border-radius:4px;padding:2px 6px;font-size:11px">'
    + '<button onclick="diarySaveNew()" style="background:color-mix(in srgb, var(--pf-success) 18%, var(--pf-panel));color:var(--pf-success);border:none;border-radius:4px;padding:3px 10px;cursor:pointer;font-size:11px">' + t('add') + '</button>'
    + '</div>';
  list.insertBefore(form, list.firstChild);
  document.getElementById('diary-new-text').focus();
}

function diarySaveNew() {
  const text = document.getElementById('diary-new-text').value.trim();
  if (!text) return;
  const entryType = document.getElementById('diary-new-type').value;
  const tagsRaw = document.getElementById('diary-new-tags').value;
  const tags = tagsRaw.split(',').map(t => t.trim()).filter(t => t);
  const args = { agent_name: _diaryAgent, text: text, type: entryType };
  if (tags.length) args.tags = tags;
  action$('diary_add', args).subscribe({
    next: () => cmdShowDiary(),
    error: (e) => addMsg('error', e.message),
  });
}

function cmdDiaryList(typeFilter) {
  const agent = selectedAgent || '';
  if (!agent) { addMsg('system', t('noAgentSelected')); return; }
  const args = { agent_name: agent, limit: 20 };
  if (typeFilter) args.type = typeFilter;
  action$('diary_list', args).subscribe({
    next: (data) => {
      const entries = data.entries || [];
      if (entries.length === 0) {
        addMsg('system', t('noDiaryEntriesFor', { agent: agent }));
      } else {
        const lines = entries.map(e => {
          return '\u2022 [' + e.type + '] ' + (e.ts ? new Date(e.ts * 1000).toLocaleString() : '') + ' \u2014 ' + e.text;
        });
        addMsg('system', t('diaryFor', { agent: agent, n: entries.length, lines: lines.join('\n') }));
      }
    },
    error: (e) => addMsg('error', t('failedLoadDiary', { error: e.message })),
  });
}

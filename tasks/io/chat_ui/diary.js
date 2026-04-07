// ── Agent Diary ─────────────────────────────────────────────────
let _diaryCache = [];
let _diaryTypeFilter = null;  // null = all

function cmdShowDiary() {
  const agent = selectedAgent || '';
  if (!agent) { addMsg('error', 'No agent selected. Select an agent first.'); return; }
  const args = { limit: 50 };
  if (_diaryTypeFilter) args.type = _diaryTypeFilter;
  action$('call_tool', { tool_name: 'diary_read', arguments: args }).subscribe({
    next: (data) => {
      _diaryCache = _parseDiaryResult(data);
      showDiaryOverlay(_diaryCache, agent);
    },
    error: (e) => addMsg('error', 'Failed to load diary: ' + e.message),
  });
}

function _parseDiaryResult(data) {
  // diary_read returns a text blob; parse lines into structured entries
  // Format: "  [type] YYYY-MM-DD HH:MM: text..."
  const raw = (data.result || data.text || '').toString();
  const entries = [];
  const lines = raw.split('\n');
  for (const line of lines) {
    const m = line.match(/^\s*\[(\w+)\]\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}):\s*(.*)$/);
    if (m) {
      entries.push({
        type: m[1],
        timestamp: m[2],
        text: m[3],
      });
    }
  }
  return entries;
}

function showDiaryOverlay(entries, agentName) {
  let overlay = document.getElementById('diaryOverlay');
  if (overlay) overlay.remove();
  overlay = document.createElement('div');
  overlay.id = 'diaryOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;z-index:9999';

  // Type filter dropdown
  const types = ['observation', 'decision', 'learning', 'reflection'];
  let filterHtml = '<select id="diaryTypeFilter" onchange="diaryFilterChanged()" style="background:#1e1e3a;color:#c0c0d0;border:1px solid #444;border-radius:6px;padding:3px 8px;font-size:12px">';
  filterHtml += '<option value="__all__"' + (_diaryTypeFilter === null ? ' selected' : '') + '>All types</option>';
  for (const t of types) {
    filterHtml += '<option value="' + t + '"' + (_diaryTypeFilter === t ? ' selected' : '') + '>' + t + '</option>';
  }
  filterHtml += '</select>';

  // Type badge colors
  function typeBadge(type) {
    const colors = {
      observation: { bg: '#1b4332', fg: '#52b788' },
      decision:    { bg: '#5a1a1a', fg: '#ff6b6b' },
      learning:    { bg: '#1e3a5f', fg: '#4fc3f7' },
      reflection:  { bg: '#3a2a1a', fg: '#f0c040' },
    };
    const c = colors[type] || { bg: '#2a2a4a', fg: '#a0a0c0' };
    return '<span style="background:' + c.bg + ';color:' + c.fg + ';padding:1px 6px;border-radius:6px;font-size:10px;font-weight:600">' + (type || '?') + '</span>';
  }

  // Build entry rows
  let rowsHtml = '';
  if (entries.length === 0) {
    rowsHtml = '<div style="color:#6c6c8a;text-align:center;padding:20px">No diary entries.</div>';
  } else {
    entries.forEach((e, i) => {
      const text = (e.text || '').replace(/</g, '&lt;').replace(/>/g, '&gt;');
      rowsHtml += '<div id="diary-row-' + i + '" style="padding:6px 8px;border-bottom:1px solid #222;cursor:pointer" onclick="this.querySelector(\'.diary-full\')&&(this.querySelector(\'.diary-full\').style.display=this.querySelector(\'.diary-full\').style.display===\'block\'?\'none\':\'block\')">'
        + '<div style="display:flex;align-items:center;gap:4px">' + typeBadge(e.type)
        + '<span style="color:#6c6c8a;font-size:10px;margin-left:auto">' + (e.timestamp || '') + '</span>'
        + '</div>'
        + '<div style="color:#c0c0d0;font-size:12px;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">' + text.slice(0, 200) + '</div>'
        + '<div class="diary-full" style="display:none;color:#a0a0c0;font-size:12px;margin-top:4px;white-space:pre-wrap;word-break:break-word;max-height:200px;overflow-y:auto">' + text + '</div>'
        + '</div>';
    });
  }

  overlay.innerHTML = '<div style="background:#1a1a2e;border:1px solid #333;border-radius:12px;padding:20px;max-width:700px;width:90%;max-height:80vh;display:flex;flex-direction:column">'
    + '<div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">'
    + '<h3 style="margin:0;color:#e0e0e0;font-size:16px">Diary — ' + (agentName || '?').replace(/</g, '&lt;') + '</h3>'
    + '<span style="color:#6c6c8a;font-size:12px">' + entries.length + ' entries</span>'
    + filterHtml
    + '<button onclick="diaryAddNew()" style="background:#1e3a5f;color:#4fc3f7;border:none;border-radius:6px;padding:3px 10px;cursor:pointer;font-size:11px;font-weight:600;margin-left:auto">+ Add</button>'
    + '<button onclick="document.getElementById(\'diaryOverlay\').remove()" style="background:none;border:none;color:#aaa;cursor:pointer;font-size:18px">&times;</button>'
    + '</div>'
    + '<div id="diary-list" style="flex:1;overflow-y:auto;border:1px solid #222;border-radius:8px;background:#0d1117">' + rowsHtml + '</div>'
    + '</div>';
  document.body.appendChild(overlay);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
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
  form.style.cssText = 'padding:8px;border-bottom:1px solid #444;background:#1a1a2e';
  form.innerHTML = '<textarea id="diary-new-text" placeholder="Diary entry..." style="width:100%;min-height:50px;background:#0d1117;color:#c0c0d0;border:1px solid #444;border-radius:4px;padding:4px;font-size:12px;resize:vertical"></textarea>'
    + '<div style="display:flex;gap:6px;margin-top:4px;align-items:center">'
    + '<label style="color:#6c6c8a;font-size:11px">Type:</label>'
    + '<select id="diary-new-type" style="background:#0d1117;color:#c0c0d0;border:1px solid #444;border-radius:4px;padding:2px 6px;font-size:11px">'
    + '<option value="observation">observation</option>'
    + '<option value="decision">decision</option>'
    + '<option value="learning">learning</option>'
    + '<option value="reflection">reflection</option>'
    + '</select>'
    + '<label style="color:#6c6c8a;font-size:11px">Tags:</label>'
    + '<input id="diary-new-tags" placeholder="tag1, tag2" style="flex:1;background:#0d1117;color:#c0c0d0;border:1px solid #444;border-radius:4px;padding:2px 6px;font-size:11px">'
    + '<button onclick="diarySaveNew()" style="background:#1b4332;color:#52b788;border:none;border-radius:4px;padding:3px 10px;cursor:pointer;font-size:11px">Add</button>'
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
  const args = { entry: text, type: entryType };
  if (tags.length) args.tags = tags;
  action$('call_tool', { tool_name: 'diary_write', arguments: args }).subscribe({
    next: () => cmdShowDiary(),
    error: (e) => addMsg('error', e.message),
  });
}

function cmdDiaryList(typeFilter) {
  const agent = selectedAgent || '';
  if (!agent) { addMsg('system', 'No agent selected.'); return; }
  const args = { limit: 20 };
  if (typeFilter) args.type = typeFilter;
  action$('call_tool', { tool_name: 'diary_read', arguments: args }).subscribe({
    next: (data) => {
      const entries = _parseDiaryResult(data);
      if (entries.length === 0) {
        addMsg('system', 'No diary entries for ' + agent + '.');
      } else {
        const lines = entries.map(e => {
          return '\u2022 [' + e.type + '] ' + e.timestamp + ' \u2014 ' + e.text;
        });
        addMsg('system', 'Diary for ' + agent + ' (' + entries.length + ' entries):\n' + lines.join('\n'));
      }
    },
    error: (e) => addMsg('error', 'Failed to load diary: ' + e.message),
  });
}

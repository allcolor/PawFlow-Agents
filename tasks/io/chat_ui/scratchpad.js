// Searchable TTL-bound scratchpad UI for the selected conversation agent.
let _spState = { notes: [], current: null, query: '' };
let _spSearchTimer = null;

function closeScratchpadOverlay() {
  const overlay = document.getElementById('scratchpadOverlay');
  if (overlay) overlay.remove();
}

function cmdShowScratchpad() {
  showScratchpadOverlay();
}

function showScratchpadOverlay() {
  if (!conversationId || !selectedAgent) {
    addMsg('error', t('noAgentSelectedSelectFirst'));
    return;
  }
  closeScratchpadOverlay();
  const overlay = document.createElement('div');
  overlay.id = 'scratchpadOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.72);display:flex;align-items:center;justify-content:center;z-index:9999';
  overlay.innerHTML = '<div style="background:#1a1a2e;border:1px solid #333;border-radius:12px;padding:16px;width:min(980px,94vw);height:min(700px,86vh);display:flex;flex-direction:column">'
    + '<div style="display:flex;align-items:center;gap:7px;margin-bottom:10px">'
    + '<h3 style="margin:0;color:#e0e0e0;font-size:16px">Scratchpad: ' + escapeHtml(selectedAgent) + '</h3>'
    + '<button type="button" onclick="closeScratchpadOverlay();showProjectGraphOverlay()" class="btn">Graph</button>'
    + '<button type="button" onclick="closeScratchpadOverlay();showProjectWikiOverlay()" class="btn">Wiki</button>'
    + '<button type="button" onclick="spEditNote()" class="btn">New note</button>'
    + '<button type="button" onclick="spClear()" class="btn">Clear all</button>'
    + '<button type="button" onclick="closeScratchpadOverlay()" style="margin-left:auto;background:none;border:none;color:#aaa;cursor:pointer;font-size:20px">&times;</button>'
    + '</div>'
    + '<div style="display:grid;grid-template-columns:minmax(220px,32%) 1fr;gap:10px;min-height:0;flex:1">'
    + '<aside style="display:flex;flex-direction:column;min-height:0;border:1px solid #292944;border-radius:8px;background:#111124">'
    + '<input id="spSearch" type="search" placeholder="Search topics, contents and tags" style="margin:9px;background:#1e1e3a;color:#ddd;border:1px solid #444;border-radius:6px;padding:7px" oninput="spScheduleSearch(this.value)">'
    + '<div id="spNotes" style="overflow:auto;padding:0 8px 8px;flex:1"></div>'
    + '</aside>'
    + '<main id="spContent" style="overflow:auto;border:1px solid #292944;border-radius:8px;background:#0d1117;padding:14px;color:#d0d0dc">'
    + '<div style="color:#777;text-align:center;padding:30px">Select a note or create one.</div>'
    + '</main></div></div>';
  document.body.appendChild(overlay);
  _spState = { notes: [], current: null, query: '' };
  spLoadNotes('');
}

function _spDate(seconds) {
  const value = Number(seconds || 0);
  return value > 0 ? new Date(value * 1000).toLocaleString() : '';
}

function _spError(error) {
  const content = document.getElementById('spContent');
  if (content) content.innerHTML = '<div style="color:#e74c3c">'
    + escapeHtml((error && error.message) || String(error || 'Unknown error')) + '</div>';
}

function spScheduleSearch(value) {
  _spState.query = String(value || '').trim();
  if (_spSearchTimer) clearTimeout(_spSearchTimer);
  _spSearchTimer = setTimeout(function() {
    _spSearchTimer = null;
    spLoadNotes(_spState.query);
  }, 250);
}

function spLoadNotes(query) {
  const list = document.getElementById('spNotes');
  if (list) list.innerHTML = '<div style="color:#777;padding:10px">Loading...</div>';
  action$('scratchpad_list', { query: query || '', limit: 100 }).subscribe({
    next: function(data) {
      if (!data || data.error) { _spError((data && data.error) || 'Cannot load scratchpad'); return; }
      _spState.notes = Array.isArray(data.notes) ? data.notes : [];
      spRenderNotes();
    },
    error: _spError,
  });
}

function spRenderNotes() {
  const list = document.getElementById('spNotes');
  if (!list) return;
  if (!_spState.notes.length) {
    list.innerHTML = '<div style="color:#777;padding:10px">No active notes.</div>';
    return;
  }
  list.innerHTML = _spState.notes.map(function(note) {
    const id = encodeURIComponent(note.id || '');
    const selected = _spState.current && _spState.current.id === note.id;
    return '<button type="button" onclick="spOpenNote(decodeURIComponent(\''
      + id + '\'))" style="display:block;width:100%;text-align:left;background:'
      + (selected ? '#242447' : 'transparent')
      + ';border:0;border-bottom:1px solid #292944;color:#ddd;padding:9px 6px;cursor:pointer">'
      + '<strong style="font-size:12px">' + escapeHtml(note.topic || note.id || '?') + '</strong>'
      + '<div style="font-size:10px;color:#888;margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">'
      + escapeHtml(String(note.content || '').replace(/\s+/g, ' ')) + '</div>'
      + '<div style="font-size:9px;color:#666;margin-top:3px">expires '
      + escapeHtml(_spDate(note.expires_at)) + '</div></button>';
  }).join('');
}

function spOpenNote(noteId) {
  const cached = _spState.notes.find(function(note) { return note.id === noteId; });
  if (cached) {
    _spState.current = cached;
    spRenderNotes();
    spRenderNote(cached);
    return;
  }
  action$('scratchpad_get', { note_id: noteId }).subscribe({
    next: function(data) {
      if (!data || data.error) { _spError((data && data.error) || 'Cannot load note'); return; }
      _spState.current = data;
      spRenderNote(data);
    },
    error: _spError,
  });
}

function spRenderNote(note) {
  const content = document.getElementById('spContent');
  if (!content) return;
  content.innerHTML = '<div style="display:flex;align-items:flex-start;gap:8px">'
    + '<div><h2 style="margin:0;color:#eee;font-size:18px">' + escapeHtml(note.topic || '') + '</h2>'
    + '<div style="font-size:10px;color:#777;margin-top:4px">Updated ' + escapeHtml(_spDate(note.updated_at))
    + ' · expires ' + escapeHtml(_spDate(note.expires_at)) + '</div></div>'
    + '<div style="margin-left:auto;display:flex;gap:6px">'
    + '<button type="button" class="btn" onclick="spEditNote()">Edit</button>'
    + '<button type="button" class="btn" onclick="spDeleteNote()">Delete</button></div></div>'
    + '<div style="margin-top:14px;white-space:pre-wrap;line-height:1.5;color:#d0d0dc">'
    + escapeHtml(note.content || '') + '</div>'
    + '<div style="margin-top:15px;border-top:1px solid #292944;padding-top:8px;color:#777;font-size:10px">Tags: '
    + escapeHtml((note.tags || []).join(', ') || 'none') + '<br>ID: ' + escapeHtml(note.id || '') + '</div>';
}

function spEditNote() {
  const note = _spState.current || {
    id: '', topic: '', content: '', tags: [], expires_at: 0,
  };
  const content = document.getElementById('spContent');
  if (!content) return;
  let ttl = 168;
  if (note.expires_at) {
    ttl = Math.max(1, Math.min(720, Math.ceil((Number(note.expires_at) - Date.now() / 1000) / 3600)));
  }
  content.innerHTML = '<form id="spEditor" onsubmit="spSaveNote(event)" style="display:flex;flex-direction:column;gap:9px;height:100%">'
    + '<input id="spNoteId" type="hidden" value="' + escapeHtml(note.id || '') + '">'
    + '<label style="font-size:11px;color:#999">Topic<input id="spTopic" required maxlength="160" value="'
    + escapeHtml(note.topic || '') + '" style="display:block;width:100%;margin-top:3px;background:#1e1e3a;color:#ddd;border:1px solid #444;border-radius:5px;padding:7px"></label>'
    + '<label style="font-size:11px;color:#999">Tags (comma-separated)<input id="spTags" value="'
    + escapeHtml((note.tags || []).join(', ')) + '" style="display:block;width:100%;margin-top:3px;background:#1e1e3a;color:#ddd;border:1px solid #444;border-radius:5px;padding:7px"></label>'
    + '<label style="font-size:11px;color:#999">Lifetime in hours (1-720)<input id="spTtl" type="number" min="1" max="720" required value="'
    + ttl + '" style="display:block;width:140px;margin-top:3px;background:#1e1e3a;color:#ddd;border:1px solid #444;border-radius:5px;padding:7px"></label>'
    + '<label style="font-size:11px;color:#999;display:flex;flex-direction:column;flex:1">Working note<textarea id="spBody" required maxlength="16000" style="margin-top:3px;flex:1;min-height:240px;background:#111124;color:#ddd;border:1px solid #444;border-radius:5px;padding:9px;resize:vertical">'
    + escapeHtml(note.content || '') + '</textarea></label>'
    + '<div style="display:flex;gap:7px;justify-content:flex-end">'
    + (note.id ? '<button type="button" class="btn" onclick="spRenderNote(_spState.current)">Cancel</button>' : '')
    + '<button type="submit" class="btn primary">Save</button></div></form>';
}

function spSaveNote(event) {
  event.preventDefault();
  const payload = {
    note_id: document.getElementById('spNoteId').value,
    topic: document.getElementById('spTopic').value.trim(),
    content: document.getElementById('spBody').value,
    tags: document.getElementById('spTags').value.split(',').map(function(v) { return v.trim(); }).filter(Boolean),
    ttl_hours: Number(document.getElementById('spTtl').value),
  };
  action$('scratchpad_save', payload).subscribe({
    next: function(data) {
      if (!data || data.error) { _spError((data && data.error) || 'Cannot save note'); return; }
      _spState.current = data;
      spLoadNotes(_spState.query);
      spRenderNote(data);
    },
    error: _spError,
  });
}

function spDeleteNote() {
  if (!_spState.current || !confirm('Delete scratchpad note "' + _spState.current.topic + '"?')) return;
  action$('scratchpad_delete', { note_id: _spState.current.id }).subscribe({
    next: function(data) {
      if (!data || data.error) { _spError((data && data.error) || 'Cannot delete note'); return; }
      _spState.current = null;
      const content = document.getElementById('spContent');
      if (content) content.innerHTML = '<div style="color:#777;text-align:center;padding:30px">Note deleted.</div>';
      spLoadNotes(_spState.query);
    },
    error: _spError,
  });
}

function spClear() {
  if (!confirm('Delete every active scratchpad note for ' + selectedAgent + '?')) return;
  action$('scratchpad_clear', {}).subscribe({
    next: function(data) {
      if (!data || data.error) { _spError((data && data.error) || 'Cannot clear scratchpad'); return; }
      _spState.current = null;
      spLoadNotes('');
      const content = document.getElementById('spContent');
      if (content) content.innerHTML = '<div style="color:#777;text-align:center;padding:30px">Scratchpad cleared.</div>';
    },
    error: _spError,
  });
}

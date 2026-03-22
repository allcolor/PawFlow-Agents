    showMemoryOverlay(_memoryCache);
  } catch (e) { addMsg('error', 'Failed to load memories: ' + e.message); }
}

function showMemoryOverlay(memories) {
  let overlay = document.getElementById('memoryOverlay');
  if (overlay) overlay.remove();
  overlay = document.createElement('div');
  overlay.id = 'memoryOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;z-index:9999';

  // Collect unique agent names for filter
  const agents = [...new Set(memories.map(m => m.agent || ''))].sort();

  // Filter dropdown
  let filterHtml = '<select id="memAgentFilter" onchange="memFilterChanged()" style="background:#1e1e3a;color:#c0c0d0;border:1px solid #444;border-radius:6px;padding:3px 8px;font-size:12px">';
  filterHtml += '<option value="__all__"' + (_memoryAgentFilter === null ? ' selected' : '') + '>All</option>';
  filterHtml += '<option value=""' + (_memoryAgentFilter === '' ? ' selected' : '') + '>Global only</option>';
  for (const a of agents) {
    if (a) filterHtml += '<option value="' + a + '"' + (_memoryAgentFilter === a ? ' selected' : '') + '>' + a + '</option>';
  }
  filterHtml += '</select>';

  // Build memory rows
  let msgsHtml = '';
  if (memories.length === 0) {
    msgsHtml = '<div style="color:#6c6c8a;text-align:center;padding:20px">No memories stored.</div>';
  } else {
    memories.forEach((m, i) => {
      // Scope badge: private (agent+conv), conversation, agent, global
      let scopeBadge;
      if (m.agent && m.conversation_id) {
        scopeBadge = '<span style="background:#5a1a1a;color:#ff6b6b;padding:1px 6px;border-radius:6px;font-size:10px;font-weight:600">\u{1F512} ' + m.agent + '</span>';
      } else if (m.conversation_id) {
        scopeBadge = '<span style="background:#1a3a5a;color:#74b9ff;padding:1px 6px;border-radius:6px;font-size:10px;font-weight:600">\u{1F4AC} conv</span>';
      } else if (m.agent) {
        scopeBadge = '<span style="background:#1e3a5f;color:#4fc3f7;padding:1px 6px;border-radius:6px;font-size:10px;font-weight:600">\u{1F916} ' + m.agent + '</span>';
      } else {
        scopeBadge = '<span style="background:#1b4332;color:#52b788;padding:1px 6px;border-radius:6px;font-size:10px;font-weight:600">\u{1F310} global</span>';
      }
      const tagsHtml = (m.tags || []).map(t =>
        '<span style="background:#2a2a4a;color:#a0a0c0;padding:1px 5px;border-radius:4px;font-size:10px;margin-left:3px">' + t + '</span>'
      ).join('');
      const age = _formatAge(m.updated_at || m.created_at);
      const editBtn = '<button onclick="event.stopPropagation();memEdit(' + i + ')" style="background:none;border:none;color:#4fc3f7;cursor:pointer;font-size:13px;padding:0 3px" title="Edit">&#9998;</button>';
      const delBtn = '<button onclick="event.stopPropagation();memDelete(\'' + m.id + '\')" style="background:none;border:none;color:#e74c3c;cursor:pointer;font-size:13px;padding:0 3px" title="Delete">&#128465;</button>';
      const text = (m.text || '').replace(/</g, '&lt;').replace(/>/g, '&gt;');
      msgsHtml += '<div id="mem-row-' + i + '" style="padding:6px 8px;border-bottom:1px solid #222;cursor:pointer" onclick="this.querySelector(\'.mem-full\')&&(this.querySelector(\'.mem-full\').style.display=this.querySelector(\'.mem-full\').style.display===\'block\'?\'none\':\'block\')">'
        + '<div style="display:flex;align-items:center;gap:4px">' + scopeBadge + tagsHtml
        + '<span style="color:#6c6c8a;font-size:10px;margin-left:auto">' + age + '</span>'
        + editBtn + delBtn + '</div>'
        + '<div style="color:#c0c0d0;font-size:12px;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">' + text.slice(0, 200) + '</div>'
        + '<div class="mem-full" style="display:none;color:#a0a0c0;font-size:12px;margin-top:4px;white-space:pre-wrap;word-break:break-word;max-height:200px;overflow-y:auto">' + text + '</div>'
        + '</div>';
    });
  }

  overlay.innerHTML = '<div style="background:#1a1a2e;border:1px solid #333;border-radius:12px;padding:20px;max-width:700px;width:90%;max-height:80vh;display:flex;flex-direction:column">'
    + '<div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">'
    + '<h3 style="margin:0;color:#e0e0e0;font-size:16px">Agent Memories</h3>'
    + '<span style="color:#6c6c8a;font-size:12px">' + memories.length + ' entries</span>'
    + filterHtml
    + '<button onclick="memAddNew()" style="background:#1e3a5f;color:#4fc3f7;border:none;border-radius:6px;padding:3px 10px;cursor:pointer;font-size:11px;font-weight:600;margin-left:auto">+ Add</button>'
    + '<button onclick="document.getElementById(\'memoryOverlay\').remove()" style="background:none;border:none;color:#aaa;cursor:pointer;font-size:18px">&times;</button>'
    + '</div>'
    + '<div id="mem-list" style="flex:1;overflow-y:auto;border:1px solid #222;border-radius:8px;background:#0d1117">' + msgsHtml + '</div>'
    + '</div>';
  document.body.appendChild(overlay);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
}

function _formatAge(ts) {
  const s = Math.floor(Date.now() / 1000 - ts);
  if (s < 60) return 'just now';
  if (s < 3600) return Math.floor(s / 60) + 'm ago';
  if (s < 86400) return Math.floor(s / 3600) + 'h ago';
  return Math.floor(s / 86400) + 'd ago';
}

async function memFilterChanged() {
  const val = document.getElementById('memAgentFilter').value;
  _memoryAgentFilter = val === '__all__' ? null : val;
  await cmdShowMemories();
}

async function memDelete(memId) {
  if (!confirm('Delete this memory?')) return;
  try {
    await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'delete_memory', memory_id: memId }),
    });
    await cmdShowMemories();
  } catch (e) { addMsg('error', e.message); }
}

function memEdit(idx) {
  const m = _memoryCache[idx];
  if (!m) return;
  const row = document.getElementById('mem-row-' + idx);
  if (!row) return;
  row.innerHTML = '<div style="padding:4px">'
    + '<textarea id="mem-edit-text" style="width:100%;min-height:60px;background:#0d1117;color:#c0c0d0;border:1px solid #444;border-radius:4px;padding:4px;font-size:12px;resize:vertical">' + (m.text || '').replace(/</g, '&lt;').replace(/>/g, '&gt;') + '</textarea>'
    + '<div style="display:flex;gap:6px;margin-top:4px;align-items:center">'
    + '<label style="color:#6c6c8a;font-size:11px">Tags:</label>'
    + '<input id="mem-edit-tags" value="' + (m.tags || []).join(', ') + '" style="flex:1;background:#0d1117;color:#c0c0d0;border:1px solid #444;border-radius:4px;padding:2px 6px;font-size:11px">'
    + '<label style="color:#6c6c8a;font-size:11px">Agent:</label>'
    + '<input id="mem-edit-agent" value="' + (m.agent || '') + '" placeholder="(global)" style="width:80px;background:#0d1117;color:#c0c0d0;border:1px solid #444;border-radius:4px;padding:2px 6px;font-size:11px">'
    + '<button onclick="memSaveEdit(\'' + m.id + '\')" style="background:#1b4332;color:#52b788;border:none;border-radius:4px;padding:3px 10px;cursor:pointer;font-size:11px">Save</button>'
    + '<button onclick="cmdShowMemories()" style="background:#333;color:#aaa;border:none;border-radius:4px;padding:3px 10px;cursor:pointer;font-size:11px">Cancel</button>'
    + '</div></div>';
}

async function memSaveEdit(memId) {
  const text = document.getElementById('mem-edit-text').value.trim();
  const tagsRaw = document.getElementById('mem-edit-tags').value;
  const agent = document.getElementById('mem-edit-agent').value.trim();
  const tags = tagsRaw.split(',').map(t => t.trim()).filter(t => t);
  try {
    await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'edit_memory', memory_id: memId, text, tags, agent }),
    });
    await cmdShowMemories();
  } catch (e) { addMsg('error', e.message); }
}

function memAddNew() {
  const list = document.getElementById('mem-list');
  if (!list) return;
  const form = document.createElement('div');
  form.style.cssText = 'padding:8px;border-bottom:1px solid #444;background:#1a1a2e';
  form.innerHTML = '<textarea id="mem-new-text" placeholder="Memory text..." style="width:100%;min-height:50px;background:#0d1117;color:#c0c0d0;border:1px solid #444;border-radius:4px;padding:4px;font-size:12px;resize:vertical"></textarea>'
    + '<div style="display:flex;gap:6px;margin-top:4px;align-items:center">'
    + '<label style="color:#6c6c8a;font-size:11px">Tags:</label>'
    + '<input id="mem-new-tags" placeholder="tag1, tag2" style="flex:1;background:#0d1117;color:#c0c0d0;border:1px solid #444;border-radius:4px;padding:2px 6px;font-size:11px">'
    + '<label style="color:#6c6c8a;font-size:11px">Agent:</label>'
    + '<input id="mem-new-agent" placeholder="(global)" style="width:80px;background:#0d1117;color:#c0c0d0;border:1px solid #444;border-radius:4px;padding:2px 6px;font-size:11px">'
    + '<button onclick="memSaveNew()" style="background:#1b4332;color:#52b788;border:none;border-radius:4px;padding:3px 10px;cursor:pointer;font-size:11px">Add</button>'
    + '</div>';
  list.insertBefore(form, list.firstChild);
  document.getElementById('mem-new-text').focus();
}

async function memSaveNew() {
  const text = document.getElementById('mem-new-text').value.trim();
  if (!text) return;
  const tagsRaw = document.getElementById('mem-new-tags').value;
  const agent = document.getElementById('mem-new-agent').value.trim();
  const tags = tagsRaw.split(',').map(t => t.trim()).filter(t => t);
  try {
    await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'add_memory', text, tags, agent }),
    });
    await cmdShowMemories();
  } catch (e) { addMsg('error', e.message); }
}

// ── Secrets & Variables ──────────────────────────────────────────
async function cmdAddSecret(name, value) {
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'add_secret', key: name, value: value,
                             conversation_id: conversationId }),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); return; }
    addMsg('system', t('secretAdded', { name, ref: data.key || name, short: name }));
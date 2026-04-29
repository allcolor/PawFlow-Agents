// ── Context editor ────────────────────────────────────────────────
let _ctxAgentFilter = 'transcript';

let _ctxCurrentOffset = 0;
let _ctxHasMore = false;
let _ctxTotalCount = 0;

function cmdShowContext(agentName) {
  if (!conversationId) { addMsg('system', t('noConv')); return; }
  if (agentName !== undefined) _ctxAgentFilter = agentName;
  _ctxCurrentOffset = 0;
  const body = { limit: 50, offset: 0 };
  if (_ctxAgentFilter) body.agent_name = _ctxAgentFilter;
  action$('get_context', body).subscribe(data => {
    if (data.error) { addMsg('error', data.error); return; }
    data._agent_filter = _ctxAgentFilter;
    _ctxHasMore = data.has_more || false;
    _ctxTotalCount = data.message_count || 0;
    _ctxCurrentOffset = (data.context || []).length;
    showContextOverlay(data);
  });
}

let _ctxFullData = null;

function _ctxScopedAgentName() {
  return (_ctxAgentFilter && _ctxAgentFilter !== 'transcript') ? _ctxAgentFilter : '';
}

function _ctxScopedMutation(body) {
  const scoped = Object.assign({}, body);
  const agent = _ctxScopedAgentName();
  if (agent) scoped.agent_name = agent;
  return scoped;
}

function ctxLoadFull() {
  const body = {};
  if (_ctxAgentFilter) body.agent_name = _ctxAgentFilter;
  return new Promise(resolve => {
    action$('get_context_full', body).subscribe(data => {
      _ctxFullData = data;
      resolve(_ctxFullData);
    });
  });
}

function ctxRefresh() {
  _ctxFullData = null;
  _ctxCurrentOffset = 0;
  const body = { limit: 50, offset: 0 };
  if (_ctxAgentFilter) body.agent_name = _ctxAgentFilter;
  action$('get_context', body).subscribe(data => {
    if (data.error) { addMsg('error', data.error); return; }
    _ctxHasMore = data.has_more || false;
    _ctxTotalCount = data.message_count || 0;
    _ctxCurrentOffset = (data.context || []).length;
    showContextOverlay(data);
  });
}

async function ctxEditMessage(msgId) {
  const full = await ctxLoadFull();
  if (full.error) { addMsg('error', full.error); return; }
  const msg = (full.context || []).find(m => (m.msg_id || m.trace_id) === msgId);
  if (!msg) { addMsg('error', 'Message not found — refresh the context'); return; }
  // Scope the row lookup to the context overlay — the chat timeline
  // also uses data-msgid on its message rows, and querySelector would
  // otherwise pick the chat row and render the edit form there.
  const overlay = document.getElementById('contextOverlay');
  if (!overlay) return;
  const row = overlay.querySelector('[data-msgid="' + msgId + '"]');
  if (!row) return;
  const content = typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content);
  row.innerHTML = '<div style="padding:8px">'
    + '<div style="margin-bottom:6px"><label style="color:#808090;font-size:11px;margin-right:6px">' + t('contextRole') + ':</label>'
    + '<select id="ctx-edit-role-' + msgId + '" style="background:#0d1117;color:#e0e0e0;border:1px solid #333;border-radius:4px;padding:2px 6px;font-size:12px">'
    + '<option value="system"' + (msg.role==='system'?' selected':'') + '>system</option>'
    + '<option value="user"' + (msg.role==='user'?' selected':'') + '>user</option>'
    + '<option value="assistant"' + (msg.role==='assistant'?' selected':'') + '>assistant</option>'
    + '<option value="tool"' + (msg.role==='tool'?' selected':'') + '>tool</option>'
    + '</select></div>'
    + '<textarea id="ctx-edit-ta-' + msgId + '" style="width:100%;min-height:120px;background:#0d1117;color:#c0c0d0;border:1px solid #333;border-radius:6px;padding:8px;font-size:12px;font-family:monospace;resize:vertical">' + content.replace(/</g,'&lt;').replace(/>/g,'&gt;') + '</textarea>'
    + '<div style="display:flex;gap:6px;margin-top:6px">'
    + '<button onclick="ctxSaveEdit(\'' + msgId + '\')" style="background:#2563eb;color:#fff;border:none;border-radius:6px;padding:4px 12px;cursor:pointer;font-size:12px">' + t('contextSave') + '</button>'
    + '<button onclick="ctxRefresh()" style="background:#333;color:#ccc;border:none;border-radius:6px;padding:4px 12px;cursor:pointer;font-size:12px">' + t('contextCancel') + '</button>'
    + '</div></div>';
}

let _ctxDirty = false;
function _ctxMutate(body, successMsg) {
  const _action = body.action;
  const _params = Object.fromEntries(Object.entries(body).filter(([k]) => k !== 'action'));
  return new Promise(resolve => {
    action$(_action, _params).subscribe(data => {
      if (data.error) { addMsg('error', data.error); resolve(false); return; }
      if (successMsg) addMsg('system', typeof successMsg === 'function' ? successMsg(data) : successMsg);
      _ctxFullData = null;
      _ctxDirty = true;
      ctxRefresh();
      resolve(true);
    });
  });
}
function ctxLoadMore() {
  const btn = document.getElementById('ctxLoadMore');
  if (btn) btn.innerHTML = '<span style="color:#888">Loading...</span>';
  const body = { limit: 50, offset: _ctxCurrentOffset };
  if (_ctxAgentFilter) body.agent_name = _ctxAgentFilter;
  action$('get_context', body).subscribe(data => {
    if (data.error) return;
    _ctxHasMore = data.has_more || false;
    _ctxCurrentOffset += (data.context || []).length;
    const list = document.getElementById('ctx-msg-list');
    if (!list) return;
    // Remove old load-more button
    if (btn) btn.remove();
    const roleColors = {system:'#6c6c8a',user:'#4fc3f7',assistant:'#4ecdc4',tool:'#f4a261'};
    // Append older messages at the BOTTOM (list is newest-first, older = further down)
    // Server returns chronological (oldest first) — reverse for newest-first display
    const olderMsgs = data.context || [];
    const reversed = [...olderMsgs].reverse();
    reversed.forEach((m) => {
      const mid = m.msg_id || m.trace_id || '';
      if (!mid) return;
      const color = roleColors[m.role] || '#808090';
      const badge = '<span style="display:inline-block;background:' + color + '22;color:' + color + ';padding:1px 6px;border-radius:6px;font-size:11px;font-weight:600;margin-right:6px">' + m.role + '</span>';
      const tcTag = m.has_tool_calls ? '<span style="color:#f4a261;font-size:10px;margin-left:4px">[tool_calls]</span>' : '';
      const content = (m.content || '').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      const editBtn = '<button onclick="event.stopPropagation();ctxEditMessage(\'' + mid + '\')" style="background:none;border:none;color:#4fc3f7;cursor:pointer;font-size:13px;padding:0 3px" title="Edit">&#9998;</button>';
      const delBtn = '<button onclick="event.stopPropagation();ctxDeleteMessage(\'' + mid + '\')" style="background:none;border:none;color:#e74c3c;cursor:pointer;font-size:13px;padding:0 3px" title="Delete">&#128465;</button>';
      const row = document.createElement('div');
      row.dataset.msgid = mid;
      row.style.cssText = 'padding:6px 8px;border-bottom:1px solid #222;cursor:pointer';
      row.innerHTML = '<div style="display:flex;align-items:center">' + badge + tcTag + '<span style="margin-left:auto">' + editBtn + delBtn + '</span></div>'
        + '<div style="color:#c0c0d0;font-size:12px;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">' + content.slice(0,200) + '</div>';
      list.appendChild(row);
    });
    // New load-more button
    if (_ctxHasMore) {
      const more = document.createElement('div');
      more.id = 'ctxLoadMore';
      more.style.cssText = 'text-align:center;padding:12px';
      more.innerHTML = '<button onclick="ctxLoadMore()" style="background:#1e3a5f;color:#4fc3f7;border:none;border-radius:6px;padding:6px 16px;cursor:pointer;font-size:12px">'
        + '\u25BC Load older (' + _ctxCurrentOffset + ' of ' + _ctxTotalCount + ')</button>';
      list.appendChild(more);
    }
  });
}

function ctxClose() {
  const overlay = document.getElementById('contextOverlay');
  if (overlay) overlay.remove();
  if (_ctxDirty) { _ctxDirty = false; resumeConv(conversationId, true); }
}

async function ctxSaveEdit(msgId) {
  const ta = document.getElementById('ctx-edit-ta-' + msgId);
  const roleEl = document.getElementById('ctx-edit-role-' + msgId);
  if (!ta) return;
  const body = {
    action: _ctxAgentFilter === 'transcript' ? 'edit_message' : 'edit_context',
    msg_id: msgId,
    content: ta.value,
    role: roleEl ? roleEl.value : undefined
  };
  _ctxMutate(
    _ctxAgentFilter === 'transcript' ? body : _ctxScopedMutation(body),
    (d) => t('contextSaved', {n: d.message_count, tokens: d.token_estimate}));
}

async function ctxDeleteMessage(msgId) {
  if (!confirm(t('contextDeleteConfirm'))) return;
  if (_ctxAgentFilter === 'transcript') {
    await _ctxMutate({action: 'delete_message', msg_id: msgId});
  } else {
    await _ctxMutate(
      {action: 'delete_context_message', msg_id: msgId, agent_name: _ctxAgentFilter},
      (d) => t('contextSaved', {n: d.message_count, tokens: d.token_estimate}));
  }
}

async function ctxAddMessage() {
  const container = document.getElementById('ctx-add-form');
  if (container) { container.remove(); return; }
  const list = document.getElementById('ctx-msg-list');
  if (!list) return;
  const form = document.createElement('div');
  form.id = 'ctx-add-form';
  form.style.cssText = 'padding:10px;border-top:1px solid #333';
  form.innerHTML = '<div style="margin-bottom:6px"><label style="color:#808090;font-size:11px;margin-right:6px">' + t('contextRole') + ':</label>'
    + '<select id="ctx-add-role" style="background:#0d1117;color:#e0e0e0;border:1px solid #333;border-radius:4px;padding:2px 6px;font-size:12px">'
    + '<option value="system">system</option><option value="user" selected>user</option><option value="assistant">assistant</option></select></div>'
    + '<textarea id="ctx-add-content" style="width:100%;min-height:80px;background:#0d1117;color:#c0c0d0;border:1px solid #333;border-radius:6px;padding:8px;font-size:12px;font-family:monospace;resize:vertical" placeholder="' + t('contextContent') + '..."></textarea>'
    + '<div style="display:flex;gap:6px;margin-top:6px">'
    + '<button onclick="ctxSaveNewMessage()" style="background:#2563eb;color:#fff;border:none;border-radius:6px;padding:4px 12px;cursor:pointer;font-size:12px">' + t('contextSave') + '</button>'
    + '<button onclick="document.getElementById(\'ctx-add-form\').remove()" style="background:#333;color:#ccc;border:none;border-radius:6px;padding:4px 12px;cursor:pointer;font-size:12px">' + t('contextCancel') + '</button>'
    + '</div>';
  list.parentNode.appendChild(form);
}

async function ctxSaveNewMessage() {
  if (_ctxAgentFilter === 'transcript') {
    addMsg('error', 'Switch to Shared or an agent context before adding context messages.');
    return;
  }
  const role = document.getElementById('ctx-add-role')?.value || 'user';
  const content = document.getElementById('ctx-add-content')?.value || '';
  if (!content.trim()) return;
  _ctxMutate(
    _ctxScopedMutation({action: 'add_context_message', role, content}),
    (d) => t('contextSaved', {n: d.message_count, tokens: d.token_estimate}));
}

async function ctxReplaceAll() {
  if (_ctxAgentFilter === 'transcript') {
    addMsg('error', 'Transcript cannot be replaced from the context editor.');
    return;
  }
  const full = await ctxLoadFull();
  if (full.error) { addMsg('error', full.error); return; }
  const overlay = document.getElementById('contextOverlay');
  if (!overlay) return;
  const inner = overlay.querySelector('div');
  inner.innerHTML = '<div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">'
    + '<h3 style="margin:0;color:#e0e0e0;font-size:16px">' + t('contextReplaceAll') + '</h3>'
    + '<button onclick="ctxRefresh()" style="background:none;border:none;color:#aaa;cursor:pointer;font-size:18px;margin-left:auto">&times;</button>'
    + '</div>'
    + '<textarea id="ctx-replace-ta" style="flex:1;width:100%;background:#0d1117;color:#c0c0d0;border:1px solid #333;border-radius:6px;padding:10px;font-size:12px;font-family:monospace;resize:none">' + JSON.stringify(full.context, null, 2).replace(/</g,'&lt;').replace(/>/g,'&gt;') + '</textarea>'
    + '<div style="display:flex;gap:6px;margin-top:10px">'
    + '<button onclick="ctxSaveReplaceAll()" style="background:#dc2626;color:#fff;border:none;border-radius:6px;padding:6px 16px;cursor:pointer;font-size:13px">' + t('contextSave') + '</button>'
    + '<button onclick="ctxRefresh()" style="background:#333;color:#ccc;border:none;border-radius:6px;padding:6px 16px;cursor:pointer;font-size:13px">' + t('contextCancel') + '</button>'
    + '</div>';
}

async function ctxSaveReplaceAll() {
  const ta = document.getElementById('ctx-replace-ta');
  if (!ta) return;
  let parsed;
  try { parsed = JSON.parse(ta.value); } catch (e) { addMsg('error', t('contextInvalidJson') + ': ' + e.message); return; }
  if (!Array.isArray(parsed)) { addMsg('error', t('contextInvalidJson') + ': expected array'); return; }
  if (!confirm(t('contextReplaceConfirm'))) return;
  _ctxMutate(
    _ctxScopedMutation({action: 'replace_context', context: parsed}),
    (d) => t('contextSaved', {n: d.message_count, tokens: d.token_estimate}));
}

function _buildCtxAgentDropdown(data) {
  const agents = data.agent_contexts || {};
  const names = Object.keys(agents).filter(n => n !== '*').sort();
  if (_ctxAgentFilter && _ctxAgentFilter !== 'transcript' && !names.includes(_ctxAgentFilter)) {
    names.push(_ctxAgentFilter);
    names.sort();
  }
  const sharedStatus = agents['*'] || 'messages';
  const sharedLabel = 'Shared' + (sharedStatus === 'diverged' ? ' \u2733' : '');
  let html = '<select id="ctxAgentFilter" onchange="ctxAgentChanged()" style="background:#1e1e3a;color:#c0c0d0;border:1px solid #444;border-radius:6px;padding:3px 8px;font-size:12px">';
  html += '<option value="transcript"' + (_ctxAgentFilter === 'transcript' ? ' selected' : '') + '>Transcript</option>';
  html += '<option value=""' + (!_ctxAgentFilter ? ' selected' : '') + '>' + sharedLabel + '</option>';
  for (const n of names) {
    const status = agents[n] || 'messages';
    let label;
    if (n.startsWith('cc_session:')) {
      label = n.replace('cc_session:', '') + ' (CC session \uD83D\uDC33)';
    } else {
      label = n + (status === 'diverged' ? ' \u2733' : '');
    }
    html += '<option value="' + n + '"' + (_ctxAgentFilter === n ? ' selected' : '') + '>' + label + '</option>';
  }
  html += '</select>';
  return html;
}
async function ctxAgentChanged() {
  _ctxAgentFilter = document.getElementById('ctxAgentFilter').value;
  _ctxFullData = null;
  await cmdShowContext(_ctxAgentFilter);
}
async function ctxDeleteContext() {
  if (!_ctxAgentFilter) return;
  if (!confirm('Delete the entire "' + _ctxAgentFilter + '" context? This cannot be undone.')) return;
  const name = _ctxAgentFilter;
  _ctxAgentFilter = 'transcript';  // switch to transcript after delete
  _ctxMutate({action: 'delete_agent_context', agent_name: name}, 'Context "' + name + '" deleted.');
}

const _ctxSelected = new Set();
function ctxToggleSelect(row, event) {
  const mid = row.dataset.msgid;
  if (!mid) return;
  // Prevent the browser's native text selection that mousedown already
  // started — the inline onclick handler fires too late for that, but
  // clearing the live Selection here undoes the highlight.
  try { window.getSelection().removeAllRanges(); } catch (e) {}
  if (event.shiftKey && _ctxSelected.size > 0) {
    const overlay = document.getElementById('contextOverlay');
    const rows = Array.from((overlay || document).querySelectorAll('[data-msgid]'));
    const lastSel = rows.find(r => r.classList.contains('ctx-selected'));
    const lastIdx = lastSel ? rows.indexOf(lastSel) : -1;
    const curIdx = rows.indexOf(row);
    if (lastIdx >= 0 && curIdx >= 0) {
      const [from, to] = lastIdx < curIdx ? [lastIdx, curIdx] : [curIdx, lastIdx];
      for (let i = from; i <= to; i++) {
        rows[i].classList.add('ctx-selected');
        rows[i].style.outline = '2px solid #6c5ce7';
        _ctxSelected.add(rows[i].dataset.msgid);
      }
    }
  } else if (event.ctrlKey) {
    if (_ctxSelected.has(mid)) {
      _ctxSelected.delete(mid);
      row.classList.remove('ctx-selected');
      row.style.outline = '';
    } else {
      _ctxSelected.add(mid);
      row.classList.add('ctx-selected');
      row.style.outline = '2px solid #6c5ce7';
    }
  } else {
    const overlay = document.getElementById('contextOverlay');
    (overlay || document).querySelectorAll('.ctx-selected').forEach(r => { r.classList.remove('ctx-selected'); r.style.outline = ''; });
    _ctxSelected.clear();
    _ctxSelected.add(mid);
    row.classList.add('ctx-selected');
    row.style.outline = '2px solid #6c5ce7';
  }
  const bar = document.getElementById('ctxSelectBar');
  if (bar) bar.style.display = _ctxSelected.size > 0 ? 'flex' : 'none';
  if (bar) bar.querySelector('span').textContent = _ctxSelected.size + ' selected';
}
async function ctxDeleteSelected() {
  if (!_ctxSelected.size) return;
  const mids = Array.from(_ctxSelected);
  _ctxSelected.clear();
  if (_ctxAgentFilter === 'transcript') {
    await _ctxMutate({action: 'delete_message', msg_ids: mids});
  } else {
    await _ctxMutate(
      _ctxScopedMutation({action: 'delete_context_messages', msg_ids: mids}),
      (d) => t('contextSaved', {n: d.message_count, tokens: d.token_estimate}));
  }
}

function showContextOverlay(data) {
  let overlay = document.getElementById('contextOverlay');
  if (overlay) overlay.remove();
  overlay = document.createElement('div');
  overlay.id = 'contextOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;z-index:9999';
  const _isTranscript = _ctxAgentFilter === 'transcript';
  const statusBadge = _isTranscript ? ''
    : data.diverged
      ? '<span style="background:#5a3e00;color:#f4a261;padding:2px 8px;border-radius:8px;font-size:11px;font-weight:600">' + t('contextDiverged') + '</span>'
      : '<span style="background:#1b4332;color:#52b788;padding:2px 8px;border-radius:8px;font-size:11px;font-weight:600">' + t('contextSynced') + '</span>';
  const roleColors = {system:'#6c6c8a',user:'#4fc3f7',assistant:'#4ecdc4',tool:'#f4a261'};
  let msgsHtml = '';
  if (!data.context || data.context.length === 0) {
    msgsHtml = '<div style="color:#6c6c8a;text-align:center;padding:20px">' + t('noContext') + '</div>';
  } else {
    // Reverse: newest first
    const reversed = [...data.context].reverse();
    reversed.forEach((m) => {
      const mid = m.msg_id || m.trace_id || '';
      if (!mid) return;  // cannot edit/delete a message without a stable id
      const color = roleColors[m.role] || '#808090';
      const badge = '<span style="display:inline-block;background:' + color + '22;color:' + color + ';padding:1px 6px;border-radius:6px;font-size:11px;font-weight:600;margin-right:6px">' + m.role + '</span>';
      const tcTag = m.has_tool_calls ? '<span style="color:#f4a261;font-size:10px;margin-left:4px">[tool_calls]</span>' : '';
      const src = m.source ? '<span style="color:#808090;font-size:10px;margin-left:4px">[' + (m.source.name||'') + ']</span>' : '';
      const content = (m.content || '').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      const editBtn = '<button onclick="event.stopPropagation();ctxEditMessage(\'' + mid + '\')" style="background:none;border:none;color:#4fc3f7;cursor:pointer;font-size:13px;padding:0 3px" title="' + t('contextEdit') + '">&#9998;</button>';
      const delBtn = '<button onclick="event.stopPropagation();ctxDeleteMessage(\'' + mid + '\')" style="background:none;border:none;color:#e74c3c;cursor:pointer;font-size:13px;padding:0 3px" title="' + t('contextDelete') + '">&#128465;</button>';
      msgsHtml += '<div data-msgid="' + mid + '" style="padding:6px 8px;border-bottom:1px solid #222;cursor:pointer" onmousedown="if(event.shiftKey||event.ctrlKey)event.preventDefault()" onclick="if(event.ctrlKey||event.shiftKey){event.preventDefault();ctxToggleSelect(this,event)}else{this.querySelector(\'.ctx-full\')&&(this.querySelector(\'.ctx-full\').style.display=this.querySelector(\'.ctx-full\').style.display===\'block\'?\'none\':\'block\')}">'
        + '<div style="display:flex;align-items:center">' + badge + tcTag + src + '<span style="margin-left:auto">' + editBtn + delBtn + '</span></div>'
        + '<div style="color:#c0c0d0;font-size:12px;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">' + content.slice(0,200) + '</div>'
        + '<div class="ctx-full" style="display:none;color:#a0a0c0;font-size:12px;margin-top:4px;white-space:pre-wrap;word-break:break-word;max-height:300px;overflow-y:auto">' + content + '</div>'
        + '</div>';
    });
    // "Load more" button if there are older messages
    if (_ctxHasMore) {
      msgsHtml += '<div id="ctxLoadMore" style="text-align:center;padding:12px">'
        + '<button onclick="ctxLoadMore()" style="background:#1e3a5f;color:#4fc3f7;border:none;border-radius:6px;padding:6px 16px;cursor:pointer;font-size:12px">'
        + '\u25BC Load older (' + _ctxCurrentOffset + ' of ' + _ctxTotalCount + ')</button></div>';
    }
  }
  overlay.innerHTML = '<div style="background:#1a1a2e;border:1px solid #333;border-radius:12px;padding:20px;max-width:700px;width:90%;max-height:80vh;display:flex;flex-direction:column">'
    + '<div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">'
    + '<h3 style="margin:0;color:#e0e0e0;font-size:16px">' + t('contextTitle') + '</h3>'
    + statusBadge
    + _buildCtxAgentDropdown(data)
    + '<span style="color:#6c6c8a;font-size:12px;margin-left:auto">' + t('contextMessages', {n:data.message_count}) + ' &middot; ' + t('contextTokens', {n:data.token_estimate}) + '</span>'
    + (_isTranscript ? '' : '<button onclick="ctxReplaceAll()" style="background:#1e3a5f;color:#4fc3f7;border:none;border-radius:6px;padding:3px 10px;cursor:pointer;font-size:11px;font-weight:600" title="' + t('contextReplaceAll') + '">JSON</button>')
    + (_ctxAgentFilter && !_isTranscript ? '<button onclick="ctxDeleteContext()" style="background:#5a1a1a;color:#e74c3c;border:none;border-radius:6px;padding:3px 10px;cursor:pointer;font-size:11px;font-weight:600" title="Delete this context entirely">\u{1F5D1} Delete</button>' : '')
    + '<button onclick="ctxClose()" style="background:none;border:none;color:#aaa;cursor:pointer;font-size:18px;margin-left:4px">&times;</button>'
    + '</div>'
    + '<div id="ctx-msg-list" style="flex:1;overflow-y:auto;border:1px solid #222;border-radius:8px;background:#0d1117">' + msgsHtml + '</div>'
    + '<div id="ctxSelectBar" style="display:none;padding:6px 0;align-items:center;gap:8px;justify-content:center">'
    + '<span style="color:#6c5ce7;font-size:12px">0 selected</span>'
    + '<button onclick="ctxDeleteSelected()" style="background:#e94560;color:#fff;border:none;border-radius:4px;padding:3px 10px;cursor:pointer;font-size:12px">Delete selected</button>'
    + '<button onclick="document.querySelectorAll(\'.ctx-selected\').forEach(r=>{r.classList.remove(\'ctx-selected\');r.style.outline=\'\'});_ctxSelected.clear();document.getElementById(\'ctxSelectBar\').style.display=\'none\'" style="background:transparent;color:#aaa;border:1px solid #555;border-radius:4px;padding:3px 8px;cursor:pointer;font-size:12px">Cancel</button>'
    + '</div>'
    + (_isTranscript ? '' : '<div style="padding:8px 0 0 0;text-align:center"><button onclick="ctxAddMessage()" style="background:#1e3a5f;color:#4fc3f7;border:none;border-radius:8px;padding:6px 18px;cursor:pointer;font-size:13px">+ ' + t('contextAdd') + '</button></div>')
    + '</div>';
  document.body.appendChild(overlay);
  const _sel = document.getElementById('ctxAgentFilter');
  if (_sel) _sel.value = _ctxAgentFilter;
}

// ── Tool Call Parser ────────────────────────────────────────────
function _parseToolCall(text) {
  // Formats supported:
  //   tool(val1, val2, val3)              — positional (mapped to schema server-side)
  //   tool(key=val, key2=val2)            — named
  //   tool(val1, key2=val2)               — mixed (positional first, then named)
  //   tool {"key": "value"}               — JSON
  //   tool()                              — no args
  const nameMatch = text.match(/^(\w+)/);
  if (!nameMatch) return { error: 'No tool name found' };
  const name = nameMatch[1];
  let rest = text.slice(name.length).trim();

  if (!rest || rest === '()') return { name, args: {}, positional: [] };

  // JSON object format
  if (rest.startsWith('{')) {
    try { return { name, args: JSON.parse(rest), positional: [] }; }
    catch (e) { return { error: 'Invalid JSON: ' + e.message }; }
  }

  // Strip outer parens
  if (rest.startsWith('(') && rest.endsWith(')')) {
    rest = rest.slice(1, -1).trim();
  }
  if (!rest) return { name, args: {}, positional: [] };

  // Tokenize: split on commas respecting quotes and brackets
  const tokens = _splitArgs(rest);
  const args = {};
  const positional = [];

  for (const token of tokens) {
    const eqMatch = token.match(/^(\w+)\s*=\s*([\s\S]*)$/);
    if (eqMatch) {
      // Named: key=value
      args[eqMatch[1]] = _parseValue(eqMatch[2].trim());
    } else {
      // Positional
      positional.push(_parseValue(token.trim()));
    }
  }
  return { name, args, positional };
}

function _splitArgs(s) {
  // Split on commas, respecting quotes, brackets, braces
  const result = [];
  let current = '';
  let depth = 0;  // [] {} depth
  let inStr = null;  // null, '"', "'"
  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    if (inStr) {
      current += c;
      if (c === inStr && s[i - 1] !== '\\') inStr = null;
    } else if (c === '"' || c === "'") {
      current += c;
      inStr = c;
    } else if (c === '[' || c === '{') {
      current += c;
      depth++;
    } else if (c === ']' || c === '}') {
      current += c;
      depth--;
    } else if (c === ',' && depth === 0) {
      result.push(current);
      current = '';
    } else {
      current += c;
    }
  }
  if (current.trim()) result.push(current);
  return result;
}

function _parseValue(v) {
  if (!v) return '';
  // Quoted string
  if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) {
    return v.slice(1, -1).replace(/\\"/g, '"').replace(/\\'/g, "'");
  }
  // JSON array or object
  if (v.startsWith('[') || v.startsWith('{')) {
    try { return JSON.parse(v); } catch(e) { return v; }
  }
  // Booleans / null
  if (v === 'true') return true;
  if (v === 'false') return false;
  if (v === 'null') return null;
  // Numbers
  if (/^\d+$/.test(v)) return parseInt(v);
  if (/^\d+\.\d+$/.test(v)) return parseFloat(v);
  // Bare string (unquoted)
  return v;
}

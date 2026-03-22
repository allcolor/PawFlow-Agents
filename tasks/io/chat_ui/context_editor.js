    .finally(() => { hideContextOp(); contextOpInProgress = false; });
}

let _ctxAgentFilter = '';  // '' = shared/default, 'grok' = per-agent

async function cmdShowContext(agentName) {
  if (!conversationId) { addMsg('system', t('noConv')); return; }
  if (agentName) _ctxAgentFilter = agentName;
  try {
    const body = { action: 'get_context', conversation_id: conversationId };
    if (_ctxAgentFilter) body.agent_name = _ctxAgentFilter;
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); return; }
    data._agent_filter = _ctxAgentFilter;
    showContextOverlay(data);
  } catch (e) { addMsg('error', 'Failed to load context: ' + e.message); }
}

let _ctxFullData = null;

async function ctxLoadFull() {
  const body = { action: 'get_context_full', conversation_id: conversationId };
  if (_ctxAgentFilter) body.agent_name = _ctxAgentFilter;
  const resp = await fetch(API, {
    method: 'POST', headers: getAuthHeaders(),
    body: JSON.stringify(body),
  });
  _ctxFullData = await resp.json();
  return _ctxFullData;
}

async function ctxRefresh() {
  _ctxFullData = null;
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'get_context', conversation_id: conversationId }),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); return; }
    showContextOverlay(data);
  } catch (e) { addMsg('error', 'Failed: ' + e.message); }
}

async function ctxEditMessage(index) {
  const full = await ctxLoadFull();
  if (full.error) { addMsg('error', full.error); return; }
  const msg = full.context[index];
  if (!msg) return;
  const row = document.getElementById('ctx-row-' + index);
  if (!row) return;
  const content = typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content);
  row.innerHTML = '<div style="padding:8px">'
    + '<div style="margin-bottom:6px"><label style="color:#808090;font-size:11px;margin-right:6px">' + t('contextRole') + ':</label>'
    + '<select id="ctx-edit-role-' + index + '" style="background:#0d1117;color:#e0e0e0;border:1px solid #333;border-radius:4px;padding:2px 6px;font-size:12px">'
    + '<option value="system"' + (msg.role==='system'?' selected':'') + '>system</option>'
    + '<option value="user"' + (msg.role==='user'?' selected':'') + '>user</option>'
    + '<option value="assistant"' + (msg.role==='assistant'?' selected':'') + '>assistant</option>'
    + '<option value="tool"' + (msg.role==='tool'?' selected':'') + '>tool</option>'
    + '</select></div>'
    + '<textarea id="ctx-edit-ta-' + index + '" style="width:100%;min-height:120px;background:#0d1117;color:#c0c0d0;border:1px solid #333;border-radius:6px;padding:8px;font-size:12px;font-family:monospace;resize:vertical">' + content.replace(/</g,'&lt;').replace(/>/g,'&gt;') + '</textarea>'
    + '<div style="display:flex;gap:6px;margin-top:6px">'
    + '<button onclick="ctxSaveEdit(' + index + ')" style="background:#2563eb;color:#fff;border:none;border-radius:6px;padding:4px 12px;cursor:pointer;font-size:12px">' + t('contextSave') + '</button>'
    + '<button onclick="ctxRefresh()" style="background:#333;color:#ccc;border:none;border-radius:6px;padding:4px 12px;cursor:pointer;font-size:12px">' + t('contextCancel') + '</button>'
    + '</div></div>';
}

async function ctxSaveEdit(index) {
  const ta = document.getElementById('ctx-edit-ta-' + index);
  const roleEl = document.getElementById('ctx-edit-role-' + index);
  if (!ta) return;
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'edit_context', conversation_id: conversationId, index: index, content: ta.value, role: roleEl ? roleEl.value : undefined }),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); return; }
    addMsg('system', t('contextSaved', { n: data.message_count, tokens: data.token_estimate }));
    _ctxFullData = null;
    ctxRefresh();
  } catch (e) { addMsg('error', 'Failed: ' + e.message); }
}

async function ctxDeleteMessage(index) {
  if (!confirm(t('contextDeleteConfirm'))) return;
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'delete_context_message', conversation_id: conversationId, index: index }),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); return; }
    addMsg('system', t('contextSaved', { n: data.message_count, tokens: data.token_estimate }));
    _ctxFullData = null;
    ctxRefresh();
  } catch (e) { addMsg('error', 'Failed: ' + e.message); }
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
  const role = document.getElementById('ctx-add-role')?.value || 'user';
  const content = document.getElementById('ctx-add-content')?.value || '';
  if (!content.trim()) return;
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'add_context_message', conversation_id: conversationId, role: role, content: content }),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); return; }
    addMsg('system', t('contextSaved', { n: data.message_count, tokens: data.token_estimate }));
    _ctxFullData = null;
    ctxRefresh();
  } catch (e) { addMsg('error', 'Failed: ' + e.message); }
}

async function ctxReplaceAll() {
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
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'replace_context', conversation_id: conversationId, context: parsed }),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); return; }
    addMsg('system', t('contextSaved', { n: data.message_count, tokens: data.token_estimate }));
    _ctxFullData = null;
    ctxRefresh();
  } catch (e) { addMsg('error', 'Failed: ' + e.message); }
}

function _buildCtxAgentDropdown(data) {
  const agents = data.agent_contexts || {};
  const names = Object.keys(agents).filter(n => n !== '*').sort();
  console.log('[ctx-dropdown] agents:', JSON.stringify(agents), 'names:', names, 'filter:', _ctxAgentFilter);
  // Always show dropdown if there are per-agent contexts or if a filter is active
  if (names.length === 0 && !_ctxAgentFilter) return '';
  // Ensure current filter is in the list (may not be diverged yet)
  if (_ctxAgentFilter && !names.includes(_ctxAgentFilter)) {
    names.push(_ctxAgentFilter);
    names.sort();
  }
  const sharedStatus = agents['*'] || 'messages';
  const sharedLabel = 'Shared' + (sharedStatus === 'diverged' ? ' \u2733' : '');
  let html = '<select id="ctxAgentFilter" onchange="ctxAgentChanged()" style="background:#1e1e3a;color:#c0c0d0;border:1px solid #444;border-radius:6px;padding:3px 8px;font-size:12px">';
  html += '<option value=""' + (!_ctxAgentFilter ? ' selected' : '') + '>' + sharedLabel + '</option>';
  for (const n of names) {
    const status = agents[n] || 'messages';
    const label = n + (status === 'diverged' ? ' \u2733' : '');
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
async function ctxDeleteSubConv() {
  if (!_ctxAgentFilter || !_ctxAgentFilter.startsWith('task:')) return;
  if (!confirm('Delete this task sub-context? This cannot be undone.')) return;
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'delete_sub_context', conversation_id: conversationId, agent_name: _ctxAgentFilter }),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); return; }
    addMsg('system', 'Sub-context deleted.');
    _ctxAgentFilter = '';
    _ctxFullData = null;
    await cmdShowContext('');
  } catch (e) { addMsg('error', 'Failed: ' + e.message); }
}

function showContextOverlay(data) {
  let overlay = document.getElementById('contextOverlay');
  if (overlay) overlay.remove();
  overlay = document.createElement('div');
  overlay.id = 'contextOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;z-index:9999';
  const statusBadge = data.diverged
    ? '<span style="background:#5a3e00;color:#f4a261;padding:2px 8px;border-radius:8px;font-size:11px;font-weight:600">' + t('contextDiverged') + '</span>'
    : '<span style="background:#1b4332;color:#52b788;padding:2px 8px;border-radius:8px;font-size:11px;font-weight:600">' + t('contextSynced') + '</span>';
  const roleColors = {system:'#6c6c8a',user:'#4fc3f7',assistant:'#4ecdc4',tool:'#f4a261'};
  let msgsHtml = '';
  if (!data.context || data.context.length === 0) {
    msgsHtml = '<div style="color:#6c6c8a;text-align:center;padding:20px">' + t('noContext') + '</div>';
  } else {
    data.context.forEach((m, i) => {
      const color = roleColors[m.role] || '#808090';
      const badge = '<span style="display:inline-block;background:' + color + '22;color:' + color + ';padding:1px 6px;border-radius:6px;font-size:11px;font-weight:600;margin-right:6px">' + m.role + '</span>';
      const tcTag = m.has_tool_calls ? '<span style="color:#f4a261;font-size:10px;margin-left:4px">[tool_calls]</span>' : '';
      const src = m.source ? '<span style="color:#808090;font-size:10px;margin-left:4px">[' + (m.source.name||'') + ']</span>' : '';
      const content = (m.content || '').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      const editBtn = '<button onclick="event.stopPropagation();ctxEditMessage(' + i + ')" style="background:none;border:none;color:#4fc3f7;cursor:pointer;font-size:13px;padding:0 3px" title="' + t('contextEdit') + '">&#9998;</button>';
      const delBtn = '<button onclick="event.stopPropagation();ctxDeleteMessage(' + i + ')" style="background:none;border:none;color:#e74c3c;cursor:pointer;font-size:13px;padding:0 3px" title="' + t('contextDelete') + '">&#128465;</button>';
      msgsHtml += '<div id="ctx-row-' + i + '" style="padding:6px 8px;border-bottom:1px solid #222;cursor:pointer" onclick="this.querySelector(\'.ctx-full\')&&(this.querySelector(\'.ctx-full\').style.display=this.querySelector(\'.ctx-full\').style.display===\'block\'?\'none\':\'block\')">'
        + '<div style="display:flex;align-items:center">' + badge + tcTag + src + '<span style="margin-left:auto">' + editBtn + delBtn + '</span></div>'
        + '<div style="color:#c0c0d0;font-size:12px;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">' + content.slice(0,200) + '</div>'
        + '<div class="ctx-full" style="display:none;color:#a0a0c0;font-size:12px;margin-top:4px;white-space:pre-wrap;word-break:break-word;max-height:300px;overflow-y:auto">' + content + '</div>'
        + '</div>';
    });
  }
  overlay.innerHTML = '<div style="background:#1a1a2e;border:1px solid #333;border-radius:12px;padding:20px;max-width:700px;width:90%;max-height:80vh;display:flex;flex-direction:column">'
    + '<div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">'
    + '<h3 style="margin:0;color:#e0e0e0;font-size:16px">' + t('contextTitle') + '</h3>'
    + statusBadge
    + _buildCtxAgentDropdown(data)
    + '<span style="color:#6c6c8a;font-size:12px;margin-left:auto">' + t('contextMessages', {n:data.message_count}) + ' &middot; ' + t('contextTokens', {n:data.token_estimate}) + '</span>'
    + '<button onclick="ctxReplaceAll()" style="background:#1e3a5f;color:#4fc3f7;border:none;border-radius:6px;padding:3px 10px;cursor:pointer;font-size:11px;font-weight:600" title="' + t('contextReplaceAll') + '">JSON</button>'
    + (_ctxAgentFilter && _ctxAgentFilter.startsWith('task:') ? '<button onclick="ctxDeleteSubConv()" style="background:#5a1a1a;color:#e74c3c;border:none;border-radius:6px;padding:3px 10px;cursor:pointer;font-size:11px;font-weight:600" title="Delete this sub-context">Delete</button>' : '')
    + '<button onclick="document.getElementById(\'contextOverlay\').remove()" style="background:none;border:none;color:#aaa;cursor:pointer;font-size:18px;margin-left:4px">&times;</button>'
    + '</div>'
    + '<div id="ctx-msg-list" style="flex:1;overflow-y:auto;border:1px solid #222;border-radius:8px;background:#0d1117">' + msgsHtml + '</div>'
    + '<div style="padding:8px 0 0 0;text-align:center">'
    + '<button onclick="ctxAddMessage()" style="background:#1e3a5f;color:#4fc3f7;border:none;border-radius:8px;padding:6px 18px;cursor:pointer;font-size:13px">+ ' + t('contextAdd') + '</button>'
    + '</div>'
    + '</div>';
  document.body.appendChild(overlay);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
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

// ── Agent Memories ──────────────────────────────────────────────
let _memoryCache = [];
let _memoryAgentFilter = null;  // null = all

async function cmdShowMemories() {
  try {
    const body = { action: 'list_memories' };
    if (_memoryAgentFilter !== null) body.agent_name = _memoryAgentFilter;
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    _memoryCache = data.memories || [];
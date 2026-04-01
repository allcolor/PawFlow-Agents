// ── Exec approval dialog ─────────────────────────────────────────
function showExecApprovalDialog(data) {
  const { request_id, action, command, risk_level, cwd, editable } = data;
  const overlay = document.createElement('div');
  overlay.className = 'exec-overlay';
  const riskLabel = risk_level.charAt(0).toUpperCase() + risk_level.slice(1);
  const cmdHtml = editable
    ? '<textarea id="execCmdEdit">' + escapeHtml(command) + '</textarea>'
    : '<code>' + escapeHtml(command) + '</code>';
  overlay.innerHTML = `
    <div class="exec-dialog">
      <h3>${escapeHtml(t('exec.approval_title') || 'Command Approval')}
        <span class="exec-risk ${risk_level}">${riskLabel}</span></h3>
      <div class="exec-cwd">${escapeHtml(t('exec.working_dir') || 'Working directory')}: ${escapeHtml(cwd || '.')}</div>
      <div class="exec-cmd">${cmdHtml}</div>
      <div class="exec-btns">
        <button class="exec-deny" onclick="resolveExec('${request_id}', false, this)">${escapeHtml(t('exec.deny') || 'Deny')}</button>
        <button class="exec-approve" onclick="resolveExec('${request_id}', true, this)">${escapeHtml(t('exec.approve') || 'Approve')}</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
}

async function resolveExec(requestId, approved, btn) {
  const overlay = btn.closest('.exec-overlay');
  const textarea = overlay.querySelector('#execCmdEdit');
  const editedCommand = textarea ? textarea.value : '';
  const result = { approved };
  if (editedCommand) result.edited_command = editedCommand;
  try {
    await fetch(API, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify({
        action: 'exec_result',
        request_id: requestId,
        result: result,
        conversation_id: conversationId,
      }),
    });
  } catch (e) { console.error('Failed to send exec result:', e); }
  overlay.remove();
}

// ── Tool Approval Dialog (Plan A) ─────────────────────────────────
function _formatToolArgs(args) {
  if (!args || typeof args !== 'object') return '';
  const entries = Object.entries(args);
  if (entries.length === 0) return '';
  return entries.map(([k, v]) => {
    let val = typeof v === 'string' ? v : JSON.stringify(v, null, 2);
    return '<div class="tool-arg"><span class="tool-arg-key">' + escapeHtml(k) + ':</span> '
      + '<pre class="tool-arg-val">' + escapeHtml(val) + '</pre></div>';
  }).join('');
}

function showToolApprovalDialog(data) {
  const { request_id, tool_name, arguments: args } = data;
  const argsHtml = _formatToolArgs(args);
  const overlay = document.createElement('div');
  overlay.className = 'exec-overlay';
  overlay.innerHTML = `
    <div class="exec-dialog">
      <h3>${escapeHtml(t('tool_approval.title') || 'Tool Permission')}
        <span class="exec-risk medium">${escapeHtml(tool_name)}</span></h3>
      <div class="exec-cmd">${argsHtml || '<code>No arguments</code>'}</div>
      <div class="exec-btns" style="display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end;">
        <button class="exec-deny" onclick="resolveToolApproval('${request_id}', 'deny', this)">${escapeHtml(t('tool_approval.deny') || 'Deny')}</button>
        <button class="exec-approve" onclick="resolveToolApproval('${request_id}', 'allow_once', this)">${escapeHtml(t('tool_approval.allow_once') || 'Allow Once')}</button>
        <button class="exec-approve" style="background:#1a7f37" onclick="resolveToolApproval('${request_id}', 'allow_session', this)">${escapeHtml(t('tool_approval.allow_session') || 'Allow for Session')}</button>
        <button class="exec-approve" style="background:#0d5d20" onclick="resolveToolApproval('${request_id}', 'always_allow', this)">${escapeHtml(t('tool_approval.always_allow') || 'Always Allow')}</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
}

async function resolveToolApproval(requestId, choice, btn) {
  const overlay = btn.closest('.exec-overlay');
  try {
    await fetch(API, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify({
        action: 'tool_approval_result',
        request_id: requestId,
        result: { choice },
        conversation_id: conversationId,
      }),
    });
  } catch (e) { console.error('Failed to send tool approval:', e); }
  overlay.remove();
}

// ── Notification Toast ────────────────────────────────────────────
function showNotification(data) {
  const { message, urgency } = data;
  const el = document.createElement('div');
  el.style.cssText = 'position:fixed;top:16px;right:16px;background:#da3633;color:#fff;padding:12px 20px;border-radius:8px;z-index:10001;font-size:14px;max-width:400px;box-shadow:0 4px 12px rgba(0,0,0,0.4);cursor:pointer;';
  if (urgency !== 'high') el.style.background = '#f0883e';
  el.textContent = message;
  el.onclick = () => el.remove();
  document.body.appendChild(el);
  setTimeout(() => { if (el.parentNode) el.remove(); }, 8000);
}

function appendExecOutput(data) {
  const { action, command, exit_code, stdout, stderr, duration_ms } = data;
  const el = document.createElement('div');
  el.className = 'terminal-output';
  let html = '<div class="term-header">$ ' + escapeHtml(command) + '</div>';
  if (stdout) html += '<div class="term-stdout">' + escapeHtml(stdout) + '</div>';
  if (stderr) html += '<div class="term-stderr">' + escapeHtml(stderr) + '</div>';
  const exitClass = exit_code === 0 ? 'ok' : 'fail';
  html += '<div class="term-exit ' + exitClass + '">exit ' + exit_code + ' (' + duration_ms + 'ms)</div>';
  el.innerHTML = html;
  document.getElementById('messages').appendChild(el);
  scrollBottom();
}

// ── Tool call dialog ────────────────────────────────────────────

function showToolCallDialog(toolName) {
  const tools = window._cachedTools || [];
  const tool = tools.find(t => t.name === toolName);
  if (!tool) { addMsg('system', 'Tool not found: ' + toolName); return; }

  const schema = tool.parameters || {};
  const props = schema.properties || {};
  const required = new Set(schema.required || []);

  function _field(label, inputHtml, desc) {
    return '<div style="margin-bottom:8px;">'
      + '<label style="color:#aaa;font-size:11px;">' + label + '</label>'
      + '<div style="margin-top:2px">' + inputHtml + '</div>'
      + (desc ? '<div style="color:#666;font-size:10px;margin-top:1px">' + escapeHtml(desc) + '</div>' : '')
      + '</div>';
  }
  const inputStyle = 'width:100%;background:#0f0f23;color:#e0e0e0;border:1px solid #333;padding:6px;border-radius:4px;font-size:12px;';

  let formHtml = '';
  const propKeys = Object.keys(props);
  for (const key of propKeys) {
    const prop = props[key];
    const isReq = required.has(key);
    const label = escapeHtml(key) + (isReq ? ' <span style="color:#e74c3c">*</span>' : '');
    const desc = prop.description || '';
    if (prop.enum) {
      const opts = prop.enum.map(v => '<option value="' + escapeHtml(v) + '">' + escapeHtml(v) + '</option>').join('');
      formHtml += _field(label, '<select id="tc-' + key + '" style="' + inputStyle + '">' + opts + '</select>', desc);
    } else if (prop.type === 'boolean') {
      formHtml += _field(label, '<label style="cursor:pointer"><input type="checkbox" id="tc-' + key + '"> enabled</label>', desc);
    } else if (prop.type === 'integer' || prop.type === 'number') {
      formHtml += _field(label, '<input type="number" id="tc-' + key + '" style="' + inputStyle + '">', desc);
    } else if (prop.type === 'object' || prop.type === 'array') {
      formHtml += _field(label, '<textarea id="tc-' + key + '" rows="3" style="' + inputStyle + '">{}</textarea>', desc);
    } else {
      const isLong = /content|text|code|prompt|command|body|script|old_string|new_string/i.test(key);
      if (isLong) {
        formHtml += _field(label, '<textarea id="tc-' + key + '" rows="4" style="' + inputStyle + '"></textarea>', desc);
      } else {
        formHtml += _field(label, '<input type="text" id="tc-' + key + '" style="' + inputStyle + '">', desc);
      }
    }
  }

  // Build modal
  const overlay = document.createElement('div');
  overlay.id = 'toolCallOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;z-index:9999;';
  overlay.onclick = function(e) { if (e.target === overlay) overlay.remove(); };
  const panel = document.createElement('div');
  panel.style.cssText = 'background:#16213e;border-radius:8px;padding:20px;width:550px;max-height:80vh;overflow-y:auto;border:1px solid #333;';
  panel.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">'
    + '<h3 style="margin:0;color:#e0e0e0;font-size:14px;">\u26A1 ' + escapeHtml(toolName) + '</h3>'
    + '<button onclick="document.getElementById(\'toolCallOverlay\').remove()" style="background:none;border:none;color:#888;font-size:18px;cursor:pointer;">&times;</button>'
    + '</div>'
    + '<div style="color:#888;font-size:11px;margin-bottom:12px;">' + escapeHtml(tool.description || '').substring(0, 200) + '</div>'
    + formHtml
    + '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">'
    + '<button onclick="document.getElementById(\'toolCallOverlay\').remove()" style="background:#333;color:#ccc;border:none;padding:6px 16px;border-radius:4px;cursor:pointer;">Cancel</button>'
    + '<button id="tcExecuteBtn" style="background:#6c5ce7;color:white;border:none;padding:6px 16px;border-radius:4px;cursor:pointer;">Execute</button>'
    + '</div>';
  overlay.appendChild(panel);
  document.body.appendChild(overlay);

  // Execute button handler
  document.getElementById('tcExecuteBtn').onclick = function() {
    const args = {};
    for (const key of propKeys) {
      const el = document.getElementById('tc-' + key);
      if (!el) continue;
      let val = el.type === 'checkbox' ? el.checked : el.value;
      if (val === '' || val === undefined || val === false) continue;
      if (el.type === 'number' && val !== '') val = Number(val);
      args[key] = val;
    }
    const argStr = Object.entries(args).map(function(pair) {
      var k = pair[0], v = pair[1];
      if (typeof v === 'string') return k + '="' + v.replace(/"/g, '\\"') + '"';
      return k + '=' + JSON.stringify(v);
    }).join(', ');
    overlay.remove();
    sendMessage('/call ' + toolName + '(' + argStr + ')');
  };
}

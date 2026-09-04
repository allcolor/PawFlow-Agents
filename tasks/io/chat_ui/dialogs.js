// ── Shared blocking operation progress ───────────────────────────
// Long server actions use one modal transaction surface. The absence of a
// numeric backend progress signal is represented honestly with an indeterminate
// bar while the phase text describes the work that is actually happening.
var _operationProgressHandle = null;

function showOperationProgress(options) {
  if (_operationProgressHandle) return null;
  options = options || {};
  const overlay = document.createElement('div');
  overlay.id = 'operationProgressOverlay';
  overlay.className = 'operation-progress-overlay';
  overlay.setAttribute('role', 'dialog');
  overlay.setAttribute('aria-modal', 'true');
  overlay.setAttribute('aria-busy', 'true');
  overlay.setAttribute('aria-labelledby', 'operationProgressTitle');
  overlay.setAttribute('aria-describedby', 'operationProgressPhase');

  const card = document.createElement('div');
  card.className = 'operation-progress-card';
  const mark = document.createElement('div');
  mark.className = 'operation-progress-mark';
  mark.setAttribute('aria-hidden', 'true');
  const content = document.createElement('div');
  content.className = 'operation-progress-content';
  const title = document.createElement('h3');
  title.id = 'operationProgressTitle';
  title.textContent = options.title || t('operationInProgress');
  const phase = document.createElement('div');
  phase.id = 'operationProgressPhase';
  phase.className = 'operation-progress-phase';
  phase.textContent = options.phase || t('turnWorking');
  const detail = document.createElement('div');
  detail.id = 'operationProgressDetail';
  detail.className = 'operation-progress-detail';
  detail.textContent = options.detail || t('operationPleaseWait');
  const track = document.createElement('div');
  track.className = 'operation-progress-track';
  track.setAttribute('aria-hidden', 'true');
  const bar = document.createElement('span');
  track.appendChild(bar);
  const close = document.createElement('button');
  close.id = 'operationProgressClose';
  close.className = 'operation-progress-close';
  close.type = 'button';
  close.textContent = t('close');
  close.hidden = true;

  content.appendChild(title);
  content.appendChild(phase);
  content.appendChild(detail);
  content.appendChild(track);
  content.appendChild(close);
  card.appendChild(mark);
  card.appendChild(content);
  overlay.appendChild(card);
  document.body.appendChild(overlay);

  const handle = {
    setPhase: function(nextPhase, nextDetail) {
      if (nextPhase) phase.textContent = nextPhase;
      if (nextDetail !== undefined) detail.textContent = nextDetail || '';
    },
    // Compatibility for the former import-only progress handle.
    setLabel: function(nextPhase) { this.setPhase(nextPhase); },
    fail: function(message) {
      overlay.setAttribute('aria-busy', 'false');
      card.classList.add('error');
      phase.textContent = t('operationFailed');
      detail.textContent = message || t('actionFailed', { error: '' });
      close.hidden = false;
      close.focus();
    },
    close: function() {
      if (_operationProgressHandle !== handle) return;
      _operationProgressHandle = null;
      overlay.remove();
    },
  };
  close.onclick = handle.close;
  _operationProgressHandle = handle;
  return handle;
}

// ── Exec approval dialog ─────────────────────────────────────────
function showExecApprovalDialog(data) {
  const { request_id, action, command, risk_level, cwd, editable } = data;
  const overlay = document.createElement('div');
  overlay.className = 'exec-overlay';
  const riskKey = 'risk.' + risk_level;
  const riskLabel = t(riskKey) === riskKey ? risk_level.charAt(0).toUpperCase() + risk_level.slice(1) : t(riskKey);
  const cmdHtml = editable
    ? '<textarea id="execCmdEdit">' + escapeHtml(command) + '</textarea>'
    : '<code>' + escapeHtml(command) + '</code>';
  overlay.innerHTML = `
    <div class="exec-dialog">
      <h3>${escapeHtml(t('exec.approval_title'))}
        <span class="exec-risk ${risk_level}">${riskLabel}</span></h3>
      <div class="exec-cwd">${escapeHtml(t('exec.working_dir'))}: ${escapeHtml(cwd || '.')}</div>
      <div class="exec-cmd">${cmdHtml}</div>
      <div class="exec-btns">
        <button class="exec-deny" onclick="resolveExec('${request_id}', false, this)">${escapeHtml(t('exec.deny'))}</button>
        <button class="exec-approve" onclick="resolveExec('${request_id}', true, this)">${escapeHtml(t('exec.approve'))}</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
}

function resolveExec(requestId, approved, btn) {
  const overlay = btn.closest('.exec-overlay');
  const textarea = overlay.querySelector('#execCmdEdit');
  const editedCommand = textarea ? textarea.value : '';
  const result = { approved };
  if (editedCommand) result.edited_command = editedCommand;
  fireAction('exec_result', {
    request_id: requestId,
    result: result,
    conversation_id: conversationId,
  });
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
      <h3>${escapeHtml(t('tool_approval.title'))}
        <span class="exec-risk medium">${escapeHtml(tool_name)}</span></h3>
      <div class="exec-cmd">${argsHtml || '<code>' + escapeHtml(t('toolNoArguments')) + '</code>'}</div>
      <div class="exec-btns" style="display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end;">
        <button class="exec-deny" onclick="resolveToolApproval('${request_id}', 'deny', this)">${escapeHtml(t('tool_approval.deny'))}</button>
        <button class="exec-approve" onclick="resolveToolApproval('${request_id}', 'allow_once', this)">${escapeHtml(t('tool_approval.allow_once'))}</button>
        <button class="exec-approve" style="background:color-mix(in srgb, var(--pf-success) 24%, var(--pf-panel))" onclick="resolveToolApproval('${request_id}', 'allow_session', this)">${escapeHtml(t('tool_approval.allow_session'))}</button>
        <button class="exec-approve" style="background:color-mix(in srgb, var(--pf-success) 22%, var(--pf-panel))" onclick="resolveToolApproval('${request_id}', 'always_allow', this)">${escapeHtml(t('tool_approval.always_allow'))}</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
}

function resolveToolApproval(requestId, choice, btn) {
  const overlay = btn.closest('.exec-overlay');
  fireAction('tool_approval_result', {
    request_id: requestId,
    result: { choice },
    conversation_id: conversationId,
  });
  overlay.remove();
}

function appendExecOutput(data) {
  const { action, command, exit_code, stdout, stderr, duration_ms } = data;
  const el = document.createElement('div');
  el.className = 'terminal-output';
  const eventId = data.msg_id || data.message_id || data.event_id || data.request_id || data.id;
  if (!appendExecOutput._localSequence) appendExecOutput._localSequence = 0;
  appendExecOutput._localSequence += 1;
  el.dataset.projectionKey = eventId
    ? 'exec:' + String(eventId)
    : 'exec-local:' + String(Date.now()) + ':' + String(appendExecOutput._localSequence);
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
  if (!tool) { addMsg('system', t('toolNotFound', { tool: toolName })); return; }

  const schema = tool.parameters || {};
  const props = schema.properties || {};
  const required = new Set(schema.required || []);

  function _field(label, inputHtml, desc) {
    return '<div style="margin-bottom:8px;">'
      + '<label style="color:var(--pf-muted);font-size:11px;">' + label + '</label>'
      + '<div style="margin-top:2px">' + inputHtml + '</div>'
      + (desc ? '<div style="color:var(--pf-muted);font-size:10px;margin-top:1px">' + escapeHtml(desc) + '</div>' : '')
      + '</div>';
  }
  const inputStyle = 'width:100%;background:var(--pf-code-bg);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;font-size:12px;';

  let formHtml = '';
  const propKeys = Object.keys(props);
  for (const key of propKeys) {
    const prop = props[key];
    const isReq = required.has(key);
    const label = escapeHtml(key) + (isReq ? ' <span style="color:var(--pf-danger)">*</span>' : '');
    const desc = prop.description || '';
    if (prop.enum) {
      const opts = prop.enum.map(v => '<option value="' + escapeHtml(v) + '">' + escapeHtml(v) + '</option>').join('');
      formHtml += _field(label, '<select id="tc-' + key + '" style="' + inputStyle + '">' + opts + '</select>', desc);
    } else if (prop.type === 'boolean') {
      formHtml += _field(label, '<label style="cursor:pointer"><input type="checkbox" id="tc-' + key + '"> ' + escapeHtml(t('enabled')) + '</label>', desc);
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
  overlay.style.cssText = 'position:fixed;inset:0;background:color-mix(in srgb, var(--pf-shadow) 70%, transparent);display:flex;align-items:center;justify-content:center;z-index:9999;';

  const panel = document.createElement('div');
  panel.style.cssText = 'background:var(--pf-panel);border-radius:8px;padding:20px;width:550px;max-width:calc(100vw - 32px);max-height:80vh;overflow-y:auto;border:1px solid var(--pf-border);';
  panel.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">'
    + '<h3 style="margin:0;color:var(--pf-text);font-size:14px;">\u26A1 ' + escapeHtml(toolName) + '</h3>'
    + '<button onclick="document.getElementById(\'toolCallOverlay\').remove()" style="background:none;border:none;color:var(--pf-muted);font-size:18px;cursor:pointer;">&times;</button>'
    + '</div>'
    + '<div style="color:var(--pf-muted);font-size:11px;margin-bottom:12px;">' + escapeHtml(tool.description || '').substring(0, 200) + '</div>'
    + formHtml
    + '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">'
    + '<button onclick="document.getElementById(\'toolCallOverlay\').remove()" style="background:var(--pf-border);color:var(--pf-text);border:none;padding:6px 16px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('contextCancel')) + '</button>'
    + '<button id="tcExecuteBtn" style="background:var(--pf-accent-2);color:white;border:none;padding:6px 16px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('execute')) + '</button>'
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

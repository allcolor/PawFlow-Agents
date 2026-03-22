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
function showToolApprovalDialog(data) {
  const { request_id, tool_name, action_summary } = data;
  const overlay = document.createElement('div');
  overlay.className = 'exec-overlay';
  overlay.innerHTML = `
    <div class="exec-dialog">
      <h3>${escapeHtml(t('tool_approval.title') || 'Tool Permission')}
        <span class="exec-risk medium">${escapeHtml(tool_name)}</span></h3>
      <div class="exec-cmd"><code>${escapeHtml(action_summary)}</code></div>
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

// ── Slash commands ───────────────────────────────────────────────
const HELP_DATA = {
  '/help': {
    usage: '/help [command]',
    short: 'Show available commands or detailed help for a command',
    detail: 'Without arguments, lists all commands. With a command name, shows detailed documentation.\nExample: /help agent',
  },
  '/msg': {
    usage: '/msg <name|ALL> <message>',
    short: 'Send a message to a specific agent (shortcut for /agent msg)',
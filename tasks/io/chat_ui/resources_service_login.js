// Part of the resources sidebar, split from resources.js (<=800 lines/file).
// Load order matters: see _JS_MODULES in tasks/io/serve_chat_ui.py.

function cmdClaudeLoginServer(parts) {
  const sub = (parts[1] || '').toLowerCase();

  // /cls pool [@service] — list credentials
  if (sub === 'pool') {
    var svcId = stripTarget(parts[2]) || 'claude_code_llm_service';
    action$('claude_pool_list', { service_id: svcId }).subscribe(data => {
      if (!data.pool || !data.pool.length) { addMsg('system', t('noCredentialsInPool')); return; }
      var lines = ['**' + t('credentialsPoolHeader', { count: data.count }) + '**'];
      data.pool.forEach(function(c) {
        lines.push('  ' + c.index + '. ' + (c.account || t('unknown')) + ' — ' + t('expiresLabel', { value: c.expires_in }));
      });
      addMsg('system', lines.join('\n'));
    });
    return true;
  }

  // /cls reset [@service] — clear all credentials
  if (sub === 'reset') {
    var svcId2 = stripTarget(parts[2]) || 'claude_code_llm_service';
    action$('claude_pool_reset', { service_id: svcId2 }).subscribe(data => {
      addMsg('system', data.message || data.error || t('done'));
    });
    return true;
  }

  // /cls remove <index> [@service] — remove one credential
  if (sub === 'remove') {
    var idx = parseInt(parts[2] || '-1', 10);
    var svcId3 = stripTarget(parts[3]) || 'claude_code_llm_service';
    if (idx < 0) { addMsg('error', t('claudeRemoveUsage')); return true; }
    action$('claude_pool_remove', { service_id: svcId3, index: idx }).subscribe(data => {
      addMsg('system', data.message || data.error || t('done'));
    });
    return true;
  }

  // /cls <service> — login (add credential to pool)
  var serviceId = stripTarget(sub);
  if (!serviceId) { addMsg('error', t('claudeUsage')); return true; }
  if (window._clsLoginPending) { addMsg('system', t('loginAlreadyInProgress')); return true; }
  window._clsLoginPending = true;
  addMsg('system', t('startingClaudeLogin', { service: serviceId }));
  fireAction('claude_code_server_login', { service_id: serviceId });
  // Reset after 60s (container timeout)
  setTimeout(function() { window._clsLoginPending = false; }, 60000);
  return true;
}

function cmdClaudeLoginRelay(parts) {
  const serviceId = stripTarget(parts[1]);
  const relayId = stripTarget(parts[2]);
  if (!serviceId) { addMsg('error', t('claudeRelayUsage')); return true; }

  if (relayId) {
    _startRelayLogin(serviceId, relayId);
    return true;
  }

  // No relay specified — list and auto-select if single
  action$('claude_code_list_relays', { service_id: serviceId }).subscribe(resp => {
    const relays = resp.relays || [];
    if (relays.length === 0) { addMsg('error', t('noRelayConnected')); return; }
    if (relays.length === 1) {
      _startRelayLogin(serviceId, relays[0].relay_id);
    } else {
      addMsg('system', t('multipleRelaysAvailable', { relays: relays.map(r => '  ' + r.relay_id + ' (' + r.platform + ')').join('\n') }));
    }
  });
  return true;
}

function cmdClaudeLoginCredentials(text, parts) {
  const serviceId = stripTarget(parts[1]);
  if (!serviceId) { addMsg('error', t('claudeCredentialsUsage')); return true; }
  const jsonStart = text.indexOf(parts[1]) + parts[1].length;
  const credsJson = text.substring(jsonStart).trim();
  if (!credsJson) { addMsg('error', t('missingCredentialsJson')); return true; }
  try {
    JSON.parse(credsJson);
  } catch (e) {
    addMsg('error', t('invalidJsonMessage', { error: e.message }));
    return true;
  }
  fireAction('claude_code_login_code', { service_id: serviceId, credentials: credsJson });
  return true;
}

// `cli` is one of: 'claude' | 'codex' | 'gemini' | 'agy' | 'rclone' — picks the right server
// status/cleanup actions (each CLI has its own dedicated namespace).
// `token` is the capability token issued by the backend at session
// register time; without it the iframe URL will 401/403 — leaving it
// empty is only valid in legacy-tooling test paths.
function _openVncLoginDialog(sessionId, serviceId, token, triggerBtn, cli, scope) {
  cli = cli || 'claude';
  scope = scope || '';
  const _statusAction = {
    'claude': 'claude_code_server_login_status',
    'codex':  'codex_server_login_status',
    'gemini': 'gemini_server_login_status',
    'agy':    'agy_server_login_status',
    'rclone': 'rclone_server_login_status',
  }[cli] || 'claude_code_server_login_status';
  const _cleanupAction = {
    'claude': 'claude_code_server_login_cleanup',
    'codex':  'codex_server_login_cleanup',
    'gemini': 'gemini_server_login_cleanup',
    'agy':    'agy_server_login_cleanup',
    'rclone': 'rclone_server_login_cleanup',
  }[cli] || 'claude_code_server_login_cleanup';
  const _title = {
    'claude': 'Claude Code Login',
    'codex':  'Codex Login',
    'gemini': 'Gemini Login',
    'agy':    'Antigravity Login',
    'rclone': 'Rclone Login',
  }[cli] || t('loginTitle');

  window._clsLoginPending = false;
  // Create overlay dialog 80%x80%
  const overlay = document.createElement('div');
  overlay.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:var(--pf-shadow);z-index:10000;display:flex;align-items:center;justify-content:center;';
  const dialog = document.createElement('div');
  dialog.style.cssText = 'width:80%;height:80%;background:var(--pf-panel);border-radius:8px;display:flex;flex-direction:column;overflow:hidden;';
  const header = document.createElement('div');
  header.style.cssText = 'display:flex;justify-content:space-between;align-items:center;padding:8px 16px;background:var(--pf-panel);';
  header.innerHTML = '<span style="color:var(--pf-muted);font-size:13px;">' + _title + '</span>'
    + '<button id="vnc-dialog-close" style="background:none;border:none;color:var(--pf-danger);font-size:18px;cursor:pointer;">&times;</button>';
  const vncUrl = '/vnc/' + sessionId + '/' + token + '/vnc.html?autoconnect=true&resize=scale'
    + '&path=vnc/' + sessionId + '/' + token + '/websockify';
  const iframe = document.createElement('iframe');
  iframe.src = vncUrl;
  iframe.style.cssText = 'flex:1;border:none;background:var(--pf-code-bg);';
  iframe.allow = 'clipboard-read; clipboard-write';
  const status = document.createElement('div');
  status.style.cssText = 'padding:6px 16px;color:var(--pf-muted);font-size:11px;background:var(--pf-panel);';
  status.textContent = t('waitingAuthorization');

  dialog.appendChild(header);
  dialog.appendChild(iframe);
  dialog.appendChild(status);
  overlay.appendChild(dialog);
  document.body.appendChild(overlay);

  function closeDialog(msg) {
    clearInterval(pollInterval);
    overlay.remove();
    if (triggerBtn) {
      triggerBtn.textContent = t('loginViaServer');
      triggerBtn.disabled = false;
      triggerBtn.style.display = '';
    }
    if (msg) addMsg('system', msg);
    // Tell server to cleanup the Docker container (per-CLI action namespace)
    fireAction(_cleanupAction, { session_id: sessionId });
  }

  document.getElementById('vnc-dialog-close').onclick = () => closeDialog(null);


  // Poll for completion (per-CLI status action)
  const pollInterval = setInterval(async () => {
    try {
      const st = await rxjs.firstValueFrom(action$(_statusAction, {
        session_id: sessionId, service_id: serviceId, scope }));
      if (st.ok) { closeDialog(st.message || t('loginSuccessful', { title: _title })); }
      else if (st.error) { closeDialog(t('loginError', { error: st.error })); }
      else if (st.status === 'starting') { status.textContent = t('startingContainer'); }
      else if (st.status === 'pending') { status.textContent = t('waitingAuthorization'); }
    } catch (e) { /* ignore */ }
  }, 3000);
}

// Per-CLI relay-login action namespace.
function _resolveRelayLoginAction(cli) {
  return ({
    'claude': 'claude_code_relay_login',
    'codex':  'codex_relay_login',
    'gemini': 'gemini_relay_login',
  })[cli] || 'claude_code_relay_login';
}

function _startRelayLogin(serviceId, relayId, cli) {
  cli = cli || 'claude';
  const _label = ({ 'claude': 'Claude Code', 'codex': 'Codex', 'gemini': 'Gemini' })[cli] || 'Claude Code';
  addMsg('system', t('startingRelayLogin', { label: _label }));
  fireAction(_resolveRelayLoginAction(cli), { service_id: serviceId, relay_id: relayId });
}

// Map a flow id (set in services/llm_connection.py get_service_actions) to the
// CLI label used by the dialog + per-CLI action selectors. Keeps the code
// shape identical across CLIs (claude/codex/gemini) while routing each one to
// its dedicated server action namespace.
function _flowToCli(flow) {
  if (flow.indexOf('codex_') === 0) return 'codex';
  if (flow.indexOf('gemini_') === 0) return 'gemini';
  if (flow.indexOf('rclone_') === 0) return 'rclone';
  return 'claude';
}

async function _renderCredentialPoolTable(serviceId, anchorBtn) {
  const container = anchorBtn ? anchorBtn.parentElement : null;
  if (!container) return;
  let panel = container.querySelector('[data-credential-pool-panel="1"]');
  if (!panel) {
    panel = document.createElement('div');
    panel.dataset.credentialPoolPanel = '1';
    panel.style.cssText = 'margin-top:8px;border:1px solid var(--pf-border);border-radius:6px;padding:8px;background:var(--pf-panel);';
    container.appendChild(panel);
  }
  panel.innerHTML = '<div style="color:var(--pf-muted);font-size:11px;">' + t('loadingCredentials') + '</div>';

  const load = async () => {
    const resp = await rxjs.firstValueFrom(action$('llm_credential_pool_list', { service_id: serviceId }));
    if (resp.error) {
      panel.innerHTML = '<div style="color:var(--pf-danger);font-size:11px;">' + escapeHtml(resp.error) + '</div>';
      return;
    }
    const rows = resp.pool || [];
    let html = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">'
      + '<strong style="color:var(--pf-text);font-size:12px;">' + escapeHtml(t('providerCredentials', { provider: resp.provider || 'OAuth' })) + '</strong>'
      + '<span style="color:var(--pf-muted);font-size:11px;">' + t('loginCount', { n: rows.length }) + '</span></div>';
    if (!rows.length) {
      html += '<div style="color:var(--pf-muted);font-size:11px;">' + t('noCredentialsSaved') + '</div>';
    } else {
      html += '<table style="width:100%;border-collapse:collapse;font-size:11px;color:var(--pf-text);">'
        + '<thead><tr style="color:var(--pf-muted);text-align:left;"><th>#</th><th>' + t('account') + '</th><th>' + t('status') + '</th><th>' + t('expires') + '</th><th></th></tr></thead><tbody>';
      for (const r of rows) {
        const idx = Number(r.index || 0);
        const status = r.valid ? t('valid') : t('expired');
        const statusColor = r.valid ? 'var(--pf-success)' : 'var(--pf-danger)';
        html += '<tr data-cred-index="' + idx + '" style="border-top:1px solid var(--pf-border);">'
          + '<td style="padding:5px 4px;">' + idx + '</td>'
          + '<td style="padding:5px 4px;">' + escapeHtml(r.account || t('unknownAccount')) + '</td>'
          + '<td style="padding:5px 4px;color:' + statusColor + ';">' + status + '</td>'
          + '<td style="padding:5px 4px;">' + escapeHtml(r.expires_in || '') + '</td>'
          + '<td style="padding:5px 4px;text-align:right;white-space:nowrap;">'
          + '<button type="button" data-cred-refresh="' + idx + '" style="background:color-mix(in srgb, var(--pf-accent-2) 18%, var(--pf-panel));color:var(--pf-text);border:1px solid var(--pf-accent-2);border-radius:4px;padding:3px 7px;margin-right:4px;cursor:pointer;font-size:11px;">' + t('refresh') + '</button>'
          + '<button type="button" data-cred-delete="' + idx + '" style="background:color-mix(in srgb, var(--pf-danger) 16%, var(--pf-panel));color:var(--pf-danger);border:1px solid var(--pf-danger);border-radius:4px;padding:3px 7px;cursor:pointer;font-size:11px;">' + t('delete') + '</button>'
          + '</td></tr>';
      }
      html += '</tbody></table>';
    }
    panel.innerHTML = html;
    panel.querySelectorAll('[data-cred-refresh]').forEach(b => b.addEventListener('click', async () => {
      b.disabled = true;
      b.textContent = t('refreshing');
      const res = await rxjs.firstValueFrom(action$('llm_credential_pool_refresh', { service_id: serviceId, index: Number(b.dataset.credRefresh) }));
      if (res.error) addMsg('error', res.error);
      await load();
    }));
    panel.querySelectorAll('[data-cred-delete]').forEach(b => b.addEventListener('click', async () => {
      const idx = Number(b.dataset.credDelete);
      if (!confirm(t('deleteCredentialConfirm', { index: idx }))) return;
      b.disabled = true;
      const res = await rxjs.firstValueFrom(action$('llm_credential_pool_remove', { service_id: serviceId, index: idx }));
      if (res.error) addMsg('error', res.error);
      await load();
    }));
  };
  await load();
}

async function _executeServiceAction(actionId, serviceId, flow, serverAction, scope) {
  const btn = event && event.target ? event.target : null;
  const _cli = _flowToCli(flow);
  const payload = { service_id: serviceId };
  if (scope) payload.scope = scope;
  const panel = document.querySelector('#resourceEditorOverlay > div');
  if (panel && panel.dataset && panel.dataset.schema) {
    try {
      payload.config = _collectSchemaValues(JSON.parse(panel.dataset.schema || '{}'));
    } catch (_) {}
  }
  if (flow === 'credential_table') {
    try { await _renderCredentialPoolTable(serviceId, btn); }
    catch (e) { addMsg('error', t('actionFailed', { error: e.message })); }
  } else if (flow === 'claude_login_server' || flow === 'codex_login_server' || flow === 'gemini_login_server' || flow === 'rclone_login_server') {
    try {
      if (btn) { btn.disabled = true; btn.textContent = t('starting'); }
      fireAction(serverAction, payload);
      // Dialog opens when SSE vnc_login_ready arrives (with `cli` field)
    } catch (e) { addMsg('error', t('actionFailed', { error: e.message })); }
  } else if (flow === 'claude_login_relay' || flow === 'codex_login_relay' || flow === 'gemini_login_relay') {
    try {
      // Step 1: list relays
      const resp = await rxjs.firstValueFrom(action$(serverAction, payload));
      if (resp.error) { addMsg('error', resp.error); return; }
      const relays = resp.relays || [];
      if (relays.length === 0) {
        addMsg('system', t('noRelayUseCredentials'));
        return;
      }
      // Single relay → skip selector, start directly
      if (relays.length === 1) {
        if (btn) { btn.disabled = true; btn.textContent = t('waitingForAuthorization'); }
        await _startRelayLogin(serviceId, relays[0].relay_id, _cli);
        if (btn) { btn.disabled = false; btn.textContent = t('loginViaRelay'); }
        return;
      }
      // Multiple relays → show selector
      const container = btn ? btn.parentElement : null;
      if (!container) return;
      const div = document.createElement('div');
      div.style.cssText = 'margin-top:8px;';
      let selectHtml = '<select id="svc-relay-select" style="' + _svcInputStyle + 'margin-bottom:4px;">';
      relays.forEach(r => {
        const label = r.relay_id + ' (' + r.platform + ')';
        selectHtml += '<option value="' + escapeHtml(r.relay_id) + '">' + escapeHtml(label) + '</option>';
      });
      selectHtml += '</select>';
      div.innerHTML = selectHtml
        + '<button type="button" id="svc-relay-login-btn" style="background:var(--pf-accent);color:var(--pf-bg);border:none;'
        + 'padding:6px 12px;border-radius:4px;cursor:pointer;font-size:12px;">' + escapeHtml(t('startLogin')) + '</button>'
        + '<div id="svc-relay-status" style="color:var(--pf-muted);font-size:11px;margin-top:4px;"></div>';
      container.appendChild(div);

      document.getElementById('svc-relay-login-btn').addEventListener('click', () => {
        const relayId = document.getElementById('svc-relay-select').value;
        const statusEl = document.getElementById('svc-relay-status');
        const loginBtn = document.getElementById('svc-relay-login-btn');
        loginBtn.disabled = true;
        loginBtn.textContent = t('waitingForAuthorization');
        statusEl.textContent = t('relayBrowserAuthorizeHint');

        fireAction(_resolveRelayLoginAction(_cli), {
          service_id: serviceId,
          relay_id: relayId,
        });
        statusEl.textContent = t('authorizeInRelayBrowser');
        // Result arrives via SSE command_result
      });
    } catch (e) { addMsg('error', t('actionFailed', { error: e.message })); }
  } else if (flow === 'oauth_code') {
    try {
      // Step 1: get instructions
      const resp = await rxjs.firstValueFrom(action$(serverAction, payload));
      if (resp.error) { addMsg('error', resp.error); return; }

      // Step 2: show instructions + textarea for credentials
      const container = btn ? btn.parentElement : null;
      if (container) {
        const loginDiv = document.createElement('div');
        loginDiv.style.cssText = 'margin-top:8px;';
        loginDiv.innerHTML = '<div style="color:var(--pf-muted);font-size:11px;white-space:pre-line;margin-bottom:6px;">' + escapeHtml(resp.message) + '</div>'
          + '<textarea id="svc-creds-input" placeholder="' + escapeHtml(t('pasteCredentialsJson')) + '" '
          + 'style="' + _svcInputStyle + 'min-height:80px;font-family:monospace;font-size:11px;"></textarea>'
          + '<button type="button" id="svc-creds-submit" style="background:var(--pf-accent);color:var(--pf-bg);border:none;padding:6px 12px;border-radius:4px;cursor:pointer;font-size:12px;margin-top:4px;">' + escapeHtml(t('saveCredentials')) + '</button>';
        container.appendChild(loginDiv);

        const submitBtn = document.getElementById('svc-creds-submit');
        submitBtn.addEventListener('click', async () => {
          const creds = document.getElementById('svc-creds-input').value.trim();
          if (!creds) return;
          submitBtn.textContent = '...';
          submitBtn.disabled = true;
          try {
            const result = await rxjs.firstValueFrom(action$(serverAction.replace('_url', '_code'), { ...payload, credentials: creds }));
            if (result.ok) {
              loginDiv.innerHTML = '<span style="color:var(--pf-success);font-size:12px;">\u2714 ' + escapeHtml(result.message || t('saved')) + '</span>';
            } else {
              submitBtn.textContent = t('saveCredentials');
              submitBtn.disabled = false;
              loginDiv.insertAdjacentHTML('beforeend',
                '<div style="color:var(--pf-danger);font-size:11px;margin-top:4px;">' + escapeHtml(result.error) + '</div>');
            }
          } catch (e) {
            loginDiv.innerHTML = '<span style="color:var(--pf-danger);font-size:12px;">\u2718 ' + e.message + '</span>';
          }
        });
      }
    } catch (e) { addMsg('error', t('actionFailed', { error: e.message })); }
  } else {
    if (flow === 'confirm' && !confirm(t('executeActionConfirm', { action: actionId }))) return;
    try {
      const resp = await rxjs.firstValueFrom(action$(serverAction, payload));
      addMsg('system', resp.message || resp.error || JSON.stringify(resp));
    } catch (e) { addMsg('error', e.message); }
  }
}

// Legacy compat
function _applyShowWhen(container) { /* replaced by _applyRules */ }

let _svcSchemaCache = {};

async function _fetchServiceSchema(serviceType) {
  if (_svcSchemaCache[serviceType]) return _svcSchemaCache[serviceType];
  try {
    const data = await rxjs.firstValueFrom(action$('get_service_schema', { service_type: serviceType }));
    if (data.error) { addMsg('error', data.error); return {parameters: {}, rules: [], actions: []}; }
    _svcSchemaCache[serviceType] = {
      parameters: data.parameters || {},
      rules: data.rules || [],
      actions: data.actions || [],
    };
    return _svcSchemaCache[serviceType];
  } catch (e) { addMsg('error', e.message); return {parameters: {}, rules: [], actions: []}; }
}

async function showServiceInstallForm() {
  let serviceTypes = [];
  try {
    const data = await rxjs.firstValueFrom(action$('list_service_types', {}));
    if (data.error) { addMsg('error', data.error); return; }
    serviceTypes = data.service_types || [];
  } catch (e) { addMsg('error', e.message); return; }
  if (!serviceTypes.length) { addMsg('error', t('noServiceTypesAvailable')); return; }

  let overlay = document.getElementById('resourceEditorOverlay');
  if (overlay) overlay.remove();
  overlay = document.createElement('div');
  overlay.id = 'resourceEditorOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:var(--pf-shadow);display:flex;align-items:center;justify-content:center;z-index:9999;';
  const panel = document.createElement('div');
  panel.style.cssText = 'background:var(--pf-panel);border-radius:8px;padding:20px;width:540px;max-height:85vh;overflow-y:auto;border:1px solid var(--pf-border);';

  const typeOpts = _renderServiceTypeOptions(serviceTypes);

  panel.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">'
    + '<h3 style="margin:0;color:var(--pf-text);font-size:14px;">' + escapeHtml(t('installService')) + '</h3>'
    + '<button onclick="document.getElementById(\'resourceEditorOverlay\').remove()" style="background:none;border:none;color:var(--pf-muted);cursor:pointer;font-size:18px;">&times;</button>'
    + '</div>'
    + '<div style="margin-bottom:8px;"><label style="' + _svcLabelStyle + '">' + escapeHtml(t('name')) + ' <span style="color:var(--pf-danger);">*</span></label>'
    + '<input id="svc-install-name" style="' + _svcInputStyle + '" placeholder="my_service"/></div>'
    + '<div style="margin-bottom:8px;"><label style="' + _svcLabelStyle + '">' + escapeHtml(t('type')) + ' <span style="color:var(--pf-danger);">*</span></label>'
    + '<select id="svc-install-type" style="' + _svcInputStyle + '">' + typeOpts + '</select></div>'
    + '<div style="margin-bottom:8px;"><label style="' + _svcLabelStyle + '">' + escapeHtml(t('description')) + '</label>'
    + '<input id="svc-install-desc" style="' + _svcInputStyle + '" placeholder="' + escapeHtml(t('optionalDescription')) + '"/></div>'
    + '<div style="margin-bottom:8px;"><label style="' + _svcLabelStyle + '">' + escapeHtml(t('scope')) + '</label>'
    + '<select id="svc-install-scope" style="' + _svcInputStyle + '">'
    + (typeof conversationId !== 'undefined' && conversationId ? '<option value="conversation">' + escapeHtml(t('conversation')) + '</option>' : '')
    + (_isAdmin() ? '<option value="global">' + t('global') + '</option>' : '')
    + '<option value="user">' + escapeHtml(t('user')) + '</option></select></div>'
    + _targetOwnerFieldHtml('svc-install-target-owner')
    + '<div id="svc-install-params" style="border-top:1px solid var(--pf-border);padding-top:8px;margin-top:8px;"></div>'
    + '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">'
    + '<button onclick="document.getElementById(\'resourceEditorOverlay\').remove()" style="background:var(--pf-border);color:var(--pf-text);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('cancel')) + '</button>'
    + '<button id="svc-install-login-btn" onclick="_submitServiceInstall(true)" style="display:none;background:color-mix(in srgb, var(--pf-accent) 16%, var(--pf-panel));color:var(--pf-accent);border:1px solid var(--pf-accent);padding:8px 16px;border-radius:4px;cursor:pointer;">Installer + login</button>'
    + '<button id="svc-install-btn" onclick="_submitServiceInstall()" style="background:var(--pf-accent);color:var(--pf-bg);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('install')) + '</button>'
    + '</div>';
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
  _populateTargetOwnerField('svc-install-target-owner');

  const typeSelect = document.getElementById('svc-install-type');
  const loadParams = async () => {
    const paramsDiv = document.getElementById('svc-install-params');
    paramsDiv.innerHTML = '<div style="color:var(--pf-muted);font-size:11px;">' + escapeHtml(t('loadingParameters')) + '</div>';
    const schemaData = await _fetchServiceSchema(typeSelect.value);
    const installParams = _installSchemaForServiceType(typeSelect.value, schemaData.parameters || {});
    panel.dataset.schema = JSON.stringify(installParams);
    panel.dataset.rules = JSON.stringify(schemaData.rules || []);
    panel.dataset.actions = JSON.stringify(schemaData.actions || []);
    const params = installParams;
    if (Object.keys(params).length === 0) {
      paramsDiv.innerHTML = '<div style="color:var(--pf-muted);font-size:11px;">' + escapeHtml(t('noConfigurableParametersForServiceType')) + '</div>';
    } else {
      paramsDiv.innerHTML = '<div style="color:var(--pf-muted);font-size:11px;margin-bottom:6px;font-weight:600;">' + escapeHtml(t('parameters')) + '</div>'
        + _renderSchemaFields(params, {});
      _applyRules(paramsDiv, schemaData.rules || [], schemaData.actions || [], '');
      _populateServiceRefs(paramsDiv);
      paramsDiv.addEventListener('change', _updateServiceInstallLoginButton);
    }
    _updateServiceInstallLoginButton();
  };
  typeSelect.addEventListener('change', async () => { await loadParams(); _updateServiceInstallLoginButton(); });
  await loadParams();
  document.getElementById('svc-install-name').focus();
}

function _updateServiceInstallLoginButton() {
  const typeEl = document.getElementById('svc-install-type');
  const btn = document.getElementById('svc-install-login-btn');
  if (!typeEl || !btn) return;
  const panel = document.querySelector('#resourceEditorOverlay > div');
  let schema = {};
  try { schema = JSON.parse(panel.dataset.schema || '{}'); } catch (_) { schema = {}; }
  const config = _collectSchemaValues(schema);
  const provider = String(config.provider || '').trim();
  const eligible = typeEl.value === 'rcloneOAuthCredentials' && (provider === 'drive' || provider === 'onedrive');
  btn.style.display = eligible ? '' : 'none';
}

async function _submitServiceInstall(loginAfterInstall) {
  const name = (document.getElementById('svc-install-name').value || '').trim();
  const svcType = document.getElementById('svc-install-type').value;
  const desc = (document.getElementById('svc-install-desc').value || '').trim();
  const scope = document.getElementById('svc-install-scope').value;
  if (!name) { alert(t('serviceNameRequired')); return; }
  const panel = document.querySelector('#resourceEditorOverlay > div');
  const schema = JSON.parse(panel.dataset.schema || '{}');
  const config = _collectSchemaValues(schema);
  const btn = document.getElementById('svc-install-btn');
  const loginBtn = document.getElementById('svc-install-login-btn');
  btn.disabled = true; btn.textContent = t('installing');
  if (loginBtn) { loginBtn.disabled = true; loginBtn.textContent = t('installing'); }
  try {
    const _instPayload = { service_name: name, service_type: svcType, description: desc, config, scope, conversation_id: conversationId };
    if (scope === 'user') { const _tgt = _targetOwnerValue('svc-install-target-owner'); if (_tgt) _instPayload.target_user_id = _tgt; }
    const data = await rxjs.firstValueFrom(action$('service_install', _instPayload));
    if (data.error) {
      addMsg('error', data.error);
      btn.disabled = false; btn.textContent = t('install');
      if (loginBtn) { loginBtn.disabled = false; loginBtn.textContent = 'Installer + login'; }
      return;
    }
    addMsg('system', t('serviceInstalledSuccessfully', { service: name }));
    document.getElementById('resourceEditorOverlay').remove();
    notifyServiceConfigurationChanged();
    loadResources();
    if (loginAfterInstall) {
      fireAction('rclone_server_login', { service_id: name, scope });
    }
  } catch (e) {
    addMsg('error', e.message);
    btn.disabled = false; btn.textContent = t('install');
    if (loginBtn) { loginBtn.disabled = false; loginBtn.textContent = 'Installer + login'; }
  }
}

async function showServiceEditForm(serviceId, scope, readonly) {
  try {
    const data = await rxjs.firstValueFrom(action$('get_service_detail', { service_id: serviceId, scope, conversation_id: conversationId }));
    if (data.error) { addMsg('error', data.error); return; }

    const svcType = data.service_type || '';
    const config = data.config || {};
    const schemaData = await _fetchServiceSchema(svcType);
    const schema = schemaData.parameters || schemaData;  // compat: old format was just params
    const rules = schemaData.rules || [];
    const actions = schemaData.actions || [];
    const ro = !!readonly;
    const disabledAttr = ro ? ' disabled' : '';
    const roStyle = ro ? 'opacity:0.7;cursor:not-allowed;' : '';

    let overlay = document.getElementById('resourceEditorOverlay');
    if (overlay) overlay.remove();
    overlay = document.createElement('div');
    overlay.id = 'resourceEditorOverlay';
    overlay.style.cssText = 'position:fixed;inset:0;background:var(--pf-shadow);display:flex;align-items:center;justify-content:center;z-index:9999;';
    const panel = document.createElement('div');
    panel.style.cssText = 'background:var(--pf-panel);border-radius:8px;padding:20px;width:540px;max-height:85vh;overflow-y:auto;border:1px solid var(--pf-border);';

    const title = ro ? t('viewServiceTitle') : t('editServiceTitle');
    let formHtml = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">'
      + '<h3 style="margin:0;color:var(--pf-text);font-size:14px;">' + title + serviceId + ' ' + _scopeBadge(scope) + '</h3>'
      + '<button onclick="document.getElementById(\'resourceEditorOverlay\').remove()" style="background:none;border:none;color:var(--pf-muted);cursor:pointer;font-size:18px;">&times;</button>'
      + '</div>';
    formHtml += '<div style="margin-bottom:8px;"><label style="' + _svcLabelStyle + '">' + escapeHtml(t('type')) + '</label>'
      + '<input value="' + svcType + '" disabled style="' + _svcInputStyle + 'opacity:0.6;cursor:not-allowed;"/></div>';

    if (Object.keys(schema).length > 0) {
      formHtml += '<div style="border-top:1px solid var(--pf-border);padding-top:8px;margin-top:8px;">'
        + '<div style="color:var(--pf-muted);font-size:11px;margin-bottom:6px;font-weight:600;">' + escapeHtml(t('parameters')) + '</div>'
        + _renderSchemaFields(schema, config, ro)
        + _renderServiceActions(actions, serviceId, scope)
        + '</div>';
    } else {
      for (const [k, v] of Object.entries(config)) {
        const isSecret = k.toLowerCase().includes('key') || k.toLowerCase().includes('secret') || k.toLowerCase().includes('token');
        const inputType = isSecret ? 'password' : 'text';
        const val = String(v).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');
        if (isSecret) {
          formHtml += '<div style="margin-bottom:6px;"><label style="' + _svcLabelStyle + '">' + k + '</label>'
            + '<div style="display:flex;gap:4px;align-items:center;">'
            + '<input id="svc-p-' + k + '" type="password" value="' + val + '"' + disabledAttr + ' style="' + _svcInputStyle + roStyle + 'flex:1;"/>'
            + '<button type="button" onclick="_togglePwdVis(\'svc-p-' + k + '\',this)" style="background:none;border:1px solid var(--pf-border);color:var(--pf-muted);border-radius:4px;padding:4px 8px;cursor:pointer;font-size:12px;" title="' + t('showHide') + '">\u{1F441}</button>'
            + '</div></div>';
        } else {
          formHtml += '<div style="margin-bottom:6px;"><label style="' + _svcLabelStyle + '">' + k + '</label>'
            + '<input id="svc-p-' + k + '" type="text" value="' + val + '"' + disabledAttr + ' style="' + _svcInputStyle + roStyle + '"/></div>';
        }
      }
    }

    if (!ro) {
      formHtml += '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">'
        + '<button onclick="document.getElementById(\'resourceEditorOverlay\').remove()" style="background:var(--pf-border);color:var(--pf-text);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">' + t('contextCancel') + '</button>'
        + '<button id="svc-save-btn" onclick="_submitServiceEdit(\'_SVC_ID_\',\'_SVC_SCOPE_\')" style="background:var(--pf-accent);color:var(--pf-bg);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">' + t('contextSave') + '</button>'
        + '</div>';
      formHtml = formHtml.replace('_SVC_ID_', serviceId).replace('_SVC_SCOPE_', scope);
    } else {
      formHtml += '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">'
        + '<button onclick="document.getElementById(\'resourceEditorOverlay\').remove()" style="background:var(--pf-border);color:var(--pf-text);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">' + t('close') + '</button>'
        + '</div>';
    }

    panel.innerHTML = formHtml;
    const effectiveSchema = Object.keys(schema).length > 0 ? schema : Object.fromEntries(Object.keys(config).map(k => [k, {type: 'string'}]));
    panel.dataset.schema = JSON.stringify(effectiveSchema);
    overlay.appendChild(panel);
    document.body.appendChild(overlay);
    panel.dataset.rules = JSON.stringify(rules);
    panel.dataset.actions = JSON.stringify(actions);
    _applyRules(panel, rules, actions, serviceId);
    _populateServiceRefs(panel);
  } catch (e) { addMsg('error', e.message); }
}

async function _submitServiceEdit(serviceId, scope) {
  const panel = document.querySelector('#resourceEditorOverlay > div');
  const schema = JSON.parse(panel.dataset.schema || '{}');
  const config = _collectSchemaValues(schema);
  const btn = document.getElementById('svc-save-btn');
  btn.disabled = true; btn.textContent = t('saving');
  try {
    const data = await rxjs.firstValueFrom(action$('update_service', { service_id: serviceId, scope, config, conversation_id: conversationId }));
    if (data.error) { addMsg('error', data.error); btn.disabled = false; btn.textContent = t('contextSave'); return; }
    addMsg('system', t('serviceUpdated', { service: serviceId }));
    document.getElementById('resourceEditorOverlay').remove();
    notifyServiceConfigurationChanged();
    loadResources();
  } catch (e) { addMsg('error', e.message); btn.disabled = false; btn.textContent = t('contextSave'); }
}

// Admin settings: minimal server gear for users and system parameters.

function adminEsc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, function(c) {
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
  });
}

function adminJsArg(s) {
  return JSON.stringify(String(s == null ? '' : s))
    .replace(/</g, '\\u003c')
    .replace(/>/g, '\\u003e')
    .replace(/&/g, '\\u0026')
    .replace(/'/g, '\\u0027');
}

function updateAdminSettingsButton() {
  var wrap = document.getElementById('adminSettingsWrap');
  if (wrap) wrap.style.display = _isAdmin() ? '' : 'none';
}

function toggleAdminSettingsMenu() {
  var menu = document.getElementById('adminSettingsMenu');
  if (menu) menu.classList.toggle('open');
}

function closeAdminSettingsMenu() {
  var menu = document.getElementById('adminSettingsMenu');
  if (menu) menu.classList.remove('open');
}

document.addEventListener('click', function(e) {
  var wrap = document.getElementById('adminSettingsWrap');
  if (wrap && !wrap.contains(e.target)) closeAdminSettingsMenu();
});

function _adminOverlay(title, bodyHtml, buttonsHtml) {
  var bg = document.createElement('div');
  bg.className = 'exec-overlay';
  bg.innerHTML = '<div class="exec-dialog" style="max-width:960px;max-height:88vh;overflow:auto;">'
    + '<h3>' + adminEsc(title) + '</h3>'
    + '<div style="display:flex;flex-direction:column;gap:12px;">' + bodyHtml + '</div>'
    + '<div class="exec-btns" style="margin-top:16px;">'
    + (buttonsHtml || '')
    + '<button class="exec-deny" onclick="this.closest(\'.exec-overlay\').remove()">Close</button>'
    + '</div></div>';
  document.body.appendChild(bg);
  return bg;
}

function openAdminUsersDialog() {
  if (!_isAdmin()) return;
  action$('admin_users_list').subscribe(function(data) {
    if (data.error) { addMsg('error', data.error); return; }
    var users = data.users || [];
    var rows = users.map(function(u) {
      var roles = ['admin', 'user'].map(function(r) {
        return '<option value="' + r + '"' + (u.role === r ? ' selected' : '') + '>' + r + '</option>';
      }).join('');
      var links = Object.entries(u.identities || {}).map(function(pair) {
        var ch = pair[0], id = pair[1];
        return '<div class="adm-identity-link" data-channel="' + adminEsc(ch) + '" style="display:grid;grid-template-columns:80px minmax(140px,1fr) auto auto;gap:4px;align-items:center;margin-bottom:4px;">'
          + '<input class="adm-id-channel" value="' + adminEsc(ch) + '" placeholder="provider">'
          + '<input class="adm-id-value" value="' + adminEsc(id) + '" placeholder="identity id">'
          + '<button style="padding:1px 5px;font-size:11px;" onclick=\'adminSaveIdentity(this,' + adminJsArg(u.username) + ',' + adminJsArg(ch) + ')\'>Save</button>'
          + '<button style="padding:1px 5px;font-size:11px;" onclick=\'adminUnlinkIdentity(' + adminJsArg(u.username) + ',' + adminJsArg(ch) + ')\'>Delete</button></div>';
      }).join('') || '<div style="color:var(--pf-muted);margin-bottom:4px;">none</div>';
      links += '<div style="display:grid;grid-template-columns:80px minmax(140px,1fr) auto;gap:4px;align-items:center;">'
        + '<input class="adm-new-id-channel" placeholder="provider">'
        + '<input class="adm-new-id-value" placeholder="identity id">'
        + '<button style="padding:1px 5px;font-size:11px;" onclick=\'adminAddIdentity(this,' + adminJsArg(u.username) + ')\'>Add</button></div>';
      return '<tr data-user="' + adminEsc(u.username) + '">'
        + '<td>' + adminEsc(u.username) + '</td>'
        + '<td><input class="adm-display" value="' + adminEsc(u.display_name || '') + '"></td>'
        + '<td><input class="adm-email" value="' + adminEsc(u.email || '') + '"></td>'
        + '<td><select class="adm-role">' + roles + '</select></td>'
        + '<td style="text-align:center"><input class="adm-enabled" type="checkbox"' + (u.enabled ? ' checked' : '') + '></td>'
        + '<td style="font-size:12px;color:var(--pf-muted);">' + adminEsc(u.created_at || '') + '</td>'
        + '<td style="font-size:12px;color:var(--pf-muted);">' + adminEsc(u.last_login || '') + '</td>'
        + '<td>' + links + '</td>'
        + '<td style="white-space:nowrap;display:flex;gap:6px;">'
        + '<button onclick=\'adminSaveUser(' + adminJsArg(u.username) + ')\'>Save</button>'
        + '<button onclick=\'adminResetPassword(' + adminJsArg(u.username) + ')\'>Password</button>'
        + '<button onclick=\'adminDeleteUser(' + adminJsArg(u.username) + ')\'>Delete</button>'
        + '</td></tr>';
    }).join('');
    var body = '<div style="display:grid;grid-template-columns:repeat(6,1fr);gap:8px;align-items:end;">'
      + '<input id="adm-new-username" placeholder="username">'
      + '<input id="adm-new-password" type="password" placeholder="password">'
      + '<input id="adm-new-display" placeholder="display name">'
      + '<input id="adm-new-email" placeholder="email">'
      + '<select id="adm-new-role"><option value="user">user</option><option value="admin">admin</option></select>'
      + '<button onclick="adminCreateUser()">Create</button></div>'
      + '<table style="width:100%;border-collapse:collapse;font-size:12px;"><thead><tr>'
      + '<th>Username</th><th>Name</th><th>Email</th><th>Role</th><th>Enabled</th><th>Created</th><th>Last login</th><th>Identities</th><th></th>'
      + '</tr></thead><tbody>' + rows + '</tbody></table>';
    _adminOverlay('User Management', body, '');
  });
}

function adminCreateUser() {
  action$('admin_user_create', {
    username: document.getElementById('adm-new-username').value,
    password: document.getElementById('adm-new-password').value,
    display_name: document.getElementById('adm-new-display').value,
    email: document.getElementById('adm-new-email').value,
    role: document.getElementById('adm-new-role').value,
  }).subscribe(function(d) { if (d.error) addMsg('error', d.error); else { document.querySelector('.exec-overlay').remove(); openAdminUsersDialog(); } });
}

function _adminUserRow(username) {
  return document.querySelector('tr[data-user="' + CSS.escape(username) + '"]');
}

function adminSaveUser(username) {
  var row = _adminUserRow(username);
  action$('admin_user_update', {
    username: username,
    display_name: row.querySelector('.adm-display').value,
    email: row.querySelector('.adm-email').value,
    role: row.querySelector('.adm-role').value,
    enabled: row.querySelector('.adm-enabled').checked,
  }).subscribe(function(d) { if (d.error) addMsg('error', d.error); else addMsg('system', 'User saved.'); });
}

function adminResetPassword(username) {
  var password = prompt('New password for ' + username);
  if (!password) return;
  action$('admin_user_reset_password', { username: username, password: password })
    .subscribe(function(d) { if (d.error) addMsg('error', d.error); else addMsg('system', 'Password reset.'); });
}

function adminDeleteUser(username) {
  if (!confirm('Delete user ' + username + '?')) return;
  action$('admin_user_delete', { username: username })
    .subscribe(function(d) { if (d.error) addMsg('error', d.error); else { document.querySelector('.exec-overlay').remove(); openAdminUsersDialog(); } });
}

function adminSaveIdentity(btn, username, oldChannel) {
  var wrap = btn.closest('.adm-identity-link');
  var channel = wrap.querySelector('.adm-id-channel').value.trim();
  var channelId = wrap.querySelector('.adm-id-value').value.trim();
  action$('admin_identity_link', {
    username: username,
    old_channel: oldChannel,
    channel: channel,
    channel_id: channelId,
  }).subscribe(function(d) { if (d.error) addMsg('error', d.error); else { document.querySelector('.exec-overlay').remove(); openAdminUsersDialog(); } });
}

function adminAddIdentity(btn, username) {
  var wrap = btn.parentElement;
  var channel = wrap.querySelector('.adm-new-id-channel').value.trim();
  var channelId = wrap.querySelector('.adm-new-id-value').value.trim();
  action$('admin_identity_link', {
    username: username,
    channel: channel,
    channel_id: channelId,
  }).subscribe(function(d) { if (d.error) addMsg('error', d.error); else { document.querySelector('.exec-overlay').remove(); openAdminUsersDialog(); } });
}

function adminUnlinkIdentity(username, channel) {
  action$('admin_identity_unlink', { username: username, channel: channel })
    .subscribe(function(d) { if (d.error) addMsg('error', d.error); else { document.querySelector('.exec-overlay').remove(); openAdminUsersDialog(); } });
}

function openOAuthTokensDialog() {
  if (!_isAdmin()) return;
  action$('admin_oauth_tokens_list').subscribe(function(data) {
    if (data.error) { addMsg('error', data.error); return; }
    var rows = (data.tokens || []).map(function(tok) {
      var ttl = Math.max(0, Math.floor(((tok.expires_at || 0) * 1000 - Date.now()) / 1000));
      return '<tr>'
        + '<td>' + adminEsc(tok.prefix || '') + '...</td>'
        + '<td>' + adminEsc(tok.link_username || '') + '</td>'
        + '<td>' + adminEsc(tok.role || '') + '</td>'
        + '<td>' + adminEsc(tok.created_by || '') + '</td>'
        + '<td>' + ttl + 's</td>'
        + '<td><button onclick=\'adminRevokeOAuthToken(' + adminJsArg(tok.id) + ')\'>Delete</button></td>'
        + '</tr>';
    }).join('') || '<tr><td colspan="6" style="color:var(--pf-muted);">No active OAuth onboarding tokens</td></tr>';
    var body = '<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:8px;align-items:end;">'
      + '<select id="adm-oauth-role"><option value="user">user</option><option value="admin">admin</option></select>'
      + '<input id="adm-oauth-link" placeholder="link existing user (optional)">'
      + '<input id="adm-oauth-ttl" type="number" min="60" value="3600" placeholder="TTL seconds">'
      + '<button onclick="adminCreateOAuthToken()">Create token</button>'
      + '<div style="color:var(--pf-muted);font-size:11px;">Tokens are one-time and disappear when used, expired, or deleted.</div>'
      + '</div><div id="adm-oauth-created" style="display:none;padding:8px;border:1px solid var(--pf-border);border-radius:6px;background:var(--pf-sidebar);"></div>'
      + '<table style="width:100%;border-collapse:collapse;font-size:12px;"><thead><tr>'
      + '<th>Prefix</th><th>Link user</th><th>Create role</th><th>Created by</th><th>TTL</th><th></th>'
      + '</tr></thead><tbody>' + rows + '</tbody></table>';
    _adminOverlay('OAuth Onboarding Tokens', body, '');
  });
}

function adminCreateOAuthToken() {
  action$('admin_oauth_token_create', {
    role: document.getElementById('adm-oauth-role').value,
    link_username: document.getElementById('adm-oauth-link').value,
    ttl_seconds: parseInt(document.getElementById('adm-oauth-ttl').value || '3600', 10),
  }).subscribe(function(d) {
    if (d.error) { addMsg('error', d.error); return; }
    var box = document.getElementById('adm-oauth-created');
    if (box && d.token && d.token.token) {
      box.style.display = '';
      box.innerHTML = '<strong>New token, copy it now:</strong><br><code style="word-break:break-all;">'
        + adminEsc(d.token.token) + '</code>';
    }
  });
}

function adminRevokeOAuthToken(tokenId) {
  action$('admin_oauth_token_revoke', { token_id: tokenId })
    .subscribe(function(d) { if (d.error) addMsg('error', d.error); else { document.querySelector('.exec-overlay').remove(); openOAuthTokensDialog(); } });
}

function openSystemParamsDialog() {
  if (!_isAdmin()) return;
  action$('system_params_get').subscribe(function(data) {
    if (data.error) { addMsg('error', data.error); return; }
    var values = data.values || {};
    var rows = (data.manifest || []).map(function(item) {
      var val = values[item.key] || '';
      var input = item.type === 'boolean'
        ? '<select data-key="' + adminEsc(item.key) + '"><option value="false"' + (val !== 'true' ? ' selected' : '') + '>false</option><option value="true"' + (val === 'true' ? ' selected' : '') + '>true</option></select>'
        : '<input data-key="' + adminEsc(item.key) + '" value="' + adminEsc(val) + '">';
      return '<tr><td>' + adminEsc(item.section) + '</td><td><strong>' + adminEsc(item.key) + '</strong><div style="color:var(--pf-muted);font-size:11px;">' + adminEsc(item.description) + '</div></td><td>' + input + '</td><td>' + adminEsc(item.apply) + '</td></tr>';
    }).join('');
    var body = '<table style="width:100%;border-collapse:collapse;font-size:12px;"><thead><tr><th>Section</th><th>Parameter</th><th>Value</th><th>Apply</th></tr></thead><tbody>' + rows + '</tbody></table>';
    _adminOverlay('System Parameters', body, '<button class="exec-approve" onclick="adminSaveSystemParams()">Save</button>');
  });
}

function _admUpdateStateCell(comp) {
  if (comp.unpinned) {
    return '<span style="color:var(--pf-muted);">no published version &mdash; force a rebuild to refresh</span>';
  }
  if (!comp.current) return '<span style="color:var(--pf-muted);">not installed</span>';
  if (!comp.available) return '<span style="color:var(--pf-muted);">unknown</span>';
  if (comp.update_available) return '<strong style="color:var(--pf-warn,#c90);">update available</strong>';
  return 'up to date';
}

function openUpdatesDialog() {
  if (!_isAdmin()) return;
  action$('admin_check_updates').subscribe(function(data) {
    if (data.error) { addMsg('error', data.error); return; }
    var comps = data.components || [];
    var rows = comps.map(function(c) {
      return '<tr><td style="color:var(--pf-muted);">' + adminEsc(c.group || '') + '</td>'
        + '<td><strong>' + adminEsc(c.label || c.key) + '</strong></td>'
        + '<td>' + adminEsc(c.current || '\u2014')
        + (c.configured_image ? '<div style="color:var(--pf-muted);font-size:10px;">runs <code>'
            + adminEsc(c.configured_image) + '</code></div>' : '') + '</td>'
        + '<td>' + adminEsc(c.available || '\u2014') + '</td>'
        + '<td>' + _admUpdateStateCell(c) + '</td></tr>';
    }).join('') || '<tr><td colspan="5" style="color:var(--pf-muted);">No components reported</td></tr>';
    var body = '<table style="width:100%;border-collapse:collapse;font-size:12px;"><thead><tr>'
      + '<th>Group</th><th>Component</th><th>Installed</th><th>Published</th><th>State</th>'
      + '</tr></thead><tbody>' + rows + '</tbody></table>'
      + '<div style="border-top:1px solid var(--pf-border);padding-top:10px;">'
      + '<strong>Agent CLI tools image</strong> &mdash; <code>' + adminEsc(data.cli_image || '') + '</code>'
      + '<div style="color:var(--pf-muted);font-size:11px;margin:4px 0 8px;">Rebuilding affects only the next CLI pool spawn. '
      + 'Force ignores the Docker layer cache: it is the only way to pick up a new Antigravity build, and it takes several minutes.</div>'
      + '<label style="font-size:12px;"><input type="checkbox" id="adm-upd-force"> Force (--no-cache)</label> '
      + '<button id="adm-upd-rebuild" onclick="adminRebuildCliImage()"' + (data.build_running ? ' disabled' : '') + '>Rebuild image</button>'
      + '<pre id="adm-upd-log" style="display:none;margin-top:8px;max-height:180px;overflow:auto;font-size:11px;white-space:pre-wrap;"></pre>'
      + '</div>'
      + _admRelaySection(data)
      + _admServerSection();
    _adminOverlay('Updates', body, '');
    if (data.build_running) adminBuildProgress({ status: 'progress', line: 'A rebuild is already running\u2026' });
    if (data.relay_build_running) adminRelayBuildProgress({ status: 'progress', line: 'A relay image rebuild is already running\u2026' });
    if (data.relay_restart_running) adminRelayRestartProgress({ status: 'progress', index: 0, total: 0 });
  });
}

function _admRelaySection(data) {
  var busy = data.relay_build_running;
  var buttons = (data.relay_images || []).map(function(img) {
    return '<button class="adm-relay-build" data-image="' + adminEsc(img.key) + '"'
      + ' onclick="adminRebuildRelayImage(\'' + adminEsc(img.key) + '\')"' + (busy ? ' disabled' : '') + '>'
      + 'Rebuild ' + adminEsc(img.label) + '</button> <code style="font-size:11px;">'
      + adminEsc(img.image) + '</code><br>';
  }).join('');
  return '<div style="border-top:1px solid var(--pf-border);padding-top:10px;margin-top:10px;">'
    + '<strong>Relay images</strong>'
    + '<div style="color:var(--pf-muted);font-size:11px;margin:4px 0 8px;">Rebuilding changes nothing for the relays already running: '
    + 'they keep the image they were started from until you restart them. Restarting replaces each container in turn &mdash; '
    + 'workspaces, volumes and relay identities are preserved, but every relay is briefly unavailable.</div>'
    + '<label style="font-size:12px;"><input type="checkbox" id="adm-relay-force"> Force (--no-cache)</label><br>'
    + buttons
    + '<pre id="adm-relay-log" style="display:none;margin-top:8px;max-height:180px;overflow:auto;font-size:11px;white-space:pre-wrap;"></pre>'
    + '<div style="margin-top:10px;">'
    + '<button id="adm-relay-restart" onclick="adminRestartRelays()"' + (data.relay_restart_running ? ' disabled' : '') + '>Restart server relays</button>'
    + '<pre id="adm-relay-restart-log" style="display:none;margin-top:8px;max-height:140px;overflow:auto;font-size:11px;white-space:pre-wrap;"></pre>'
    + '</div></div>';
}

function _admServerSection() {
  return '<div style="border-top:1px solid var(--pf-border);padding-top:10px;margin-top:10px;">'
    + '<strong>PawFlow server</strong>'
    + '<div style="color:var(--pf-muted);font-size:11px;margin:4px 0 8px;">Updating restarts the whole stack: '
    + 'every running agent turn is killed, exactly as if you re-ran the deployment yourself. '
    + 'A compose stack is updated with <code>docker compose up -d</code>; an installer deployment '
    + 'pulls the published image and re-runs <code>run-pawflow-docker.sh</code>, the same script '
    + 'the installer used. Either way the directory comes from the container itself.</div>'
    + '<button id="adm-server-update" onclick="adminUpdateServer()">Update server\u2026</button>'
    + '<pre id="adm-server-log" style="display:none;margin-top:8px;max-height:160px;overflow:auto;font-size:11px;white-space:pre-wrap;"></pre>'
    + '</div>';
}

// Step 1: ask the server whether it *can* update, and what it would cost.
function adminUpdateServer() {
  var btn = document.getElementById('adm-server-update');
  var log = document.getElementById('adm-server-log');
  if (btn) btn.disabled = true;
  action$('admin_server_update_check').subscribe(function(d) {
    if (btn) btn.disabled = false;
    if (d.error || !d.ok) {
      if (log) {
        log.style.display = '';
        log.textContent = d.error || d.reason || 'Server update is unavailable.';
      }
      return;
    }
    _admConfirmServerUpdate(d);
  });
}

function _admConfirmServerUpdate(info) {
  var agents = info.running_agents || 0;
  var installer = info.deployment === 'installer';
  var body = '<div style="font-size:12px;line-height:1.6;">'
    + '<p>' + (installer
        ? 'This will pull the published server image and re-run the installer\'s start script, '
          + 'recreating this container in place.'
        : 'This will pull and recreate the compose project, then restart this server.') + '</p>'
    + '<table style="width:100%;font-size:12px;border-collapse:collapse;">'
    + (installer
        ? '<tr><td style="color:var(--pf-muted);">Container</td><td><code>' + adminEsc(info.container || '') + '</code></td></tr>'
          + '<tr><td style="color:var(--pf-muted);">New image</td><td><code>' + adminEsc(info.target_image || '') + '</code></td></tr>'
        : '<tr><td style="color:var(--pf-muted);">Project</td><td><code>' + adminEsc(info.compose && info.compose.project || '') + '</code></td></tr>')
    + '<tr><td style="color:var(--pf-muted);">Directory (host)</td><td><code>' + adminEsc(info.working_dir || '') + '</code></td></tr>'
    + (info.artifact_dir && info.artifact_dir !== info.working_dir
        ? '<tr><td style="color:var(--pf-muted);">New host files</td><td><code>' + adminEsc(info.artifact_dir) + '</code></td></tr>'
        : '')
    + '<tr><td style="color:var(--pf-muted);">Updater image</td><td><code>' + adminEsc(info.updater_image || '') + '</code></td></tr>'
    + '<tr><td style="color:var(--pf-muted);">Agent turns running</td><td><strong>' + agents + '</strong>'
    + (agents ? ' &mdash; they will be killed' : '') + '</td></tr>'
    + '</table>'
    + (info.is_git_checkout
        ? '<label style="display:block;margin-top:10px;"><input type="checkbox" id="adm-server-git"> '
          + 'Also <code>git pull --ff-only</code> first (aborts on a dirty or diverged tree)</label>'
        : '<div style="color:var(--pf-muted);margin-top:10px;">The project directory is not a git checkout &mdash; '
          + (info.artifact_dir
              ? 'the image and the host files it carries are refreshed.'
              : 'only images are refreshed.') + '</div>')
    + (info.artifact_dir
        ? '<label style="display:block;margin-top:6px;"><input type="checkbox" id="adm-server-force"> '
          + 'Continue even if the host files cannot be refreshed (the server would start '
          + 'with the previous version\'s start script)</label>'
        : '')
    + '<p style="margin-top:12px;">The interface will go dark for a minute or two while the server restarts.</p>'
    + '<button onclick="adminUpdateServerConfirm()" style="background:#c0392b;color:#fff;border:none;padding:6px 12px;border-radius:4px;cursor:pointer;">Update and restart</button>'
    + '</div>';
  _adminOverlay('Update server', body, '');
}

function adminUpdateServerConfirm() {
  var pull = !!(document.getElementById('adm-server-git') || {}).checked;
  var force = !!(document.getElementById('adm-server-force') || {}).checked;
  // Identify the process we are about to kill *before* killing it: an update
  // that dies before touching the container leaves this exact server
  // answering, and without a baseline that is indistinguishable from a server
  // that has already come back.
  fetch('/health', { cache: 'no-store' })
    .then(function(r) { return r.json(); })
    .catch(function() { return {}; })
    .then(function(before) {
      action$('admin_update_server', { pull_source: pull, force_artifacts: force }).subscribe(function(d) {
        if (d.error) { addMsg('error', d.error); return; }
        _admWaitForServer(d, before || {});
      });
    });
}

//: How long a restart may take before the panel calls it a failure. Pulling a
//: server image on a slow line is the long case.
var ADM_UPDATE_TIMEOUT_S = 600;

// Step 3: the server is about to stop answering. Poll /health until a
// *different* process answers, then reload — the page we are running was
// served by the old one. Waiting for any answer at all ended on the first poll
// whenever the updater failed before stopping anything: the page reloaded
// immediately, onto the version it started from, reporting success.
function _admWaitForServer(info, before) {
  var started = Date.now();
  var wasInstance = (before || {}).instance || '';
  var wentDown = false;
  var body = '<div style="font-size:12px;line-height:1.7;">'
    + '<p><strong>Restarting.</strong> Updater container: <code>' + adminEsc(info.container || '') + '</code></p>'
    + '<p id="adm-server-wait" style="color:var(--pf-muted);">Waiting for the server to restart\u2026</p>'
    + '<p style="color:var(--pf-muted);font-size:11px;">If it does not return, the updater kept its logs: '
    + '<code>docker logs ' + adminEsc(info.container || '') + '</code></p></div>';
  _adminOverlay('Update server', body, '');

  var poll = setInterval(function() {
    var waited = Math.round((Date.now() - started) / 1000);
    var note = document.getElementById('adm-server-wait');
    if (note) {
      note.textContent = (wentDown ? 'Server stopped; waiting for it to come back\u2026 ('
                                   : 'Waiting for the server to restart\u2026 (') + waited + 's)';
    }
    if (waited > ADM_UPDATE_TIMEOUT_S) {
      clearInterval(poll);
      _admUpdateStalled(info, wentDown);
      return;
    }
    fetch('/health', { cache: 'no-store' }).then(function(r) {
      if (!r.ok) return null;
      return r.json();
    }).then(function(now) {
      // The same process answering means nothing has restarted yet, however
      // healthy it is.
      if (!now || (wasInstance && now.instance === wasInstance)) return;
      clearInterval(poll);
      if (note) note.textContent = 'Back up on ' + (now.version || '?') + '. Reloading\u2026';
      setTimeout(function() { location.reload(); }, 800);
    }).catch(function() { wentDown = true; /* still down — expected */ });
  }, 2000);
}

// The server never restarted. Say so, and name the one command that explains
// why: the updater keeps its logs until the next update replaces it.
function _admUpdateStalled(info, wentDown) {
  _adminOverlay('Update server',
    '<div style="font-size:12px;line-height:1.7;">'
    + '<p><strong>The server did not restart.</strong> '
    + (wentDown
        ? 'It stopped answering and never came back, so the new container failed to start.'
        : 'It never stopped answering, so the updater failed before it touched the container '
          + '&mdash; you are still on the version you started from.')
    + '</p><p>The updater kept its logs:</p>'
    + '<pre style="user-select:text;">docker logs ' + adminEsc(info.container || 'pawflow-updater') + '</pre>'
    + '<button onclick="location.reload()" style="padding:6px 12px;border-radius:4px;cursor:pointer;">Reload anyway</button>'
    + '</div>', '');
}

function _admRelayButtons() {
  return Array.from(document.querySelectorAll('.adm-relay-build'));
}

function adminRebuildRelayImage(key) {
  var forced = !!(document.getElementById('adm-relay-force') || {}).checked;
  _admRelayButtons().forEach(function(b) { b.disabled = true; });
  action$('admin_rebuild_relay_image', { image: key, force: forced }).subscribe(function(d) {
    if (d.error) {
      addMsg('error', d.error);
      _admRelayButtons().forEach(function(b) { b.disabled = false; });
      return;
    }
    adminRelayBuildProgress({ status: 'started', image_key: key, forced: forced });
  });
}

function adminRestartRelays() {
  var btn = document.getElementById('adm-relay-restart');
  if (btn) btn.disabled = true;
  action$('admin_restart_relays').subscribe(function(d) {
    if (d.error) {
      addMsg('error', d.error);
      if (btn) btn.disabled = false;
      return;
    }
    adminRelayRestartProgress({ status: 'started' });
  });
}

// Fed by the `relay_image_build` SSE event while a relay image builds.
function adminRelayBuildProgress(data) {
  var log = document.getElementById('adm-relay-log');
  if (!log) return;
  log.style.display = '';
  var status = (data || {}).status || '';
  var key = (data || {}).image_key || '';
  if (status === 'started') {
    log.textContent = (data.forced ? 'Forced rebuild' : 'Rebuild') + ' of ' + key + ' started\u2026';
    return;
  }
  if (status === 'progress') { log.textContent = String(data.line || ''); return; }
  _admRelayButtons().forEach(function(b) { b.disabled = false; });
  if (status === 'done') {
    log.textContent = 'Rebuild finished: ' + (data.image || '') + '\n\n' + String(data.output || '');
    addMsg('system', 'Relay image ' + key + ' rebuilt. Restart the relays to use it.');
    return;
  }
  log.textContent = 'Rebuild failed (exit ' + (data.exit_code == null ? '?' : data.exit_code) + ')\n\n'
    + String(data.error || data.output || '');
}

// Fed by the `relay_restart` SSE event, one progress entry per relay.
function adminRelayRestartProgress(data) {
  var log = document.getElementById('adm-relay-restart-log');
  if (!log) return;
  log.style.display = '';
  var status = (data || {}).status || '';
  if (status === 'started') { log.textContent = 'Restarting server relays\u2026'; return; }
  if (status === 'progress' && !data.total) {
    // Dialog opened while a restart was already running: no per-relay context yet.
    log.textContent = 'A relay restart is already running\u2026';
    return;
  }
  if (status === 'progress') {
    log.textContent = 'Relay ' + data.index + '/' + data.total + ' \u2014 ' + (data.kind || '') + ' '
      + (data.conv_id || '') + (data.ok ? ' recreated' : ' FAILED: ' + String(data.error || ''));
    return;
  }
  var btn = document.getElementById('adm-relay-restart');
  if (btn) btn.disabled = false;
  if (status === 'done') {
    var failed = data.failed || [];
    log.textContent = data.restarted + '/' + data.total + ' relays recreated'
      + (failed.length ? '\n\nFailed:\n' + failed.map(function(f) {
        return f.kind + ' ' + f.conv_id + ': ' + f.error;
      }).join('\n') : '');
    addMsg('system', 'Server relays restarted: ' + data.restarted + '/' + data.total + '.');
    return;
  }
  log.textContent = 'Restart failed: ' + String(data.error || '');
}

function adminRebuildCliImage() {
  var forced = !!(document.getElementById('adm-upd-force') || {}).checked;
  var btn = document.getElementById('adm-upd-rebuild');
  if (btn) btn.disabled = true;
  action$('admin_rebuild_cli_image', { force: forced }).subscribe(function(d) {
    if (d.error) {
      addMsg('error', d.error);
      if (btn) btn.disabled = false;
      return;
    }
    adminBuildProgress({ status: 'started', forced: forced });
  });
}

// Fed by the `cli_image_build` SSE event while a rebuild runs.
function adminBuildProgress(data) {
  var log = document.getElementById('adm-upd-log');
  if (!log) return;
  log.style.display = '';
  var status = (data || {}).status || '';
  if (status === 'started') {
    log.textContent = (data.forced ? 'Forced rebuild' : 'Rebuild') + ' started\u2026';
    return;
  }
  if (status === 'progress') { log.textContent = String(data.line || ''); return; }
  var btn = document.getElementById('adm-upd-rebuild');
  if (btn) btn.disabled = false;
  if (status === 'done') {
    log.textContent = 'Rebuild finished: ' + (data.image || '') + '\n\n' + String(data.output || '');
    addMsg('system', 'CLI tools image rebuilt.');
    return;
  }
  log.textContent = 'Rebuild failed (exit ' + (data.exit_code == null ? '?' : data.exit_code) + ')\n\n'
    + String(data.error || data.output || '');
}

function adminSaveSystemParams() {
  var inputs = Array.from(document.querySelectorAll('.exec-overlay [data-key]'));
  var remaining = inputs.length;
  if (!remaining) return;
  inputs.forEach(function(input) {
    action$('system_param_set', { key: input.getAttribute('data-key'), value: input.value })
      .subscribe(function(d) {
        if (d.error) addMsg('error', d.error);
        remaining -= 1;
        if (remaining === 0) { document.querySelector('.exec-overlay').remove(); addMsg('system', 'System parameters saved.'); }
      });
  });
}

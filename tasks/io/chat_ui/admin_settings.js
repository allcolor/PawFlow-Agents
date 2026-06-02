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
      var roles = ['admin', 'editor', 'operator', 'viewer'].map(function(r) {
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
      + '<select id="adm-new-role"><option value="viewer">viewer</option><option value="operator">operator</option><option value="editor">editor</option><option value="admin">admin</option></select>'
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
      + '<select id="adm-oauth-role"><option value="viewer">viewer</option><option value="operator">operator</option><option value="editor">editor</option><option value="admin">admin</option></select>'
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

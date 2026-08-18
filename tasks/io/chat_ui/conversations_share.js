// ── Conversation sharing: shared list, invites, share dialog ─────
// Phase 7 of docs/CONVERSATION_SHARING_PLAN.md. All globals, same style as
// conversations_menu.js. Every server call goes through action$().
//
// The sidebar shows three groups: the user's own conversations, pending
// invites (which grant nothing until accepted), and conversations shared
// with them. Invites appear on the next list refresh, not instantly --
// there is no per-user notification channel in v1.

window._sharedConvs = window._sharedConvs || [];

function loadSharedConversations(cb) {
  action$('list_shared_conversations', {}).subscribe(data => {
    window._sharedConvs = (data && data.conversations) || [];
    if (cb) cb(window._sharedConvs);
  });
}

function _sharedConvEntry(cid) {
  return (window._sharedConvs || []).find(c => c.conversation_id === cid) || null;
}

// True when the current user is a collaborator on `cid` rather than its
// owner. Drives which controls the UI offers: a collaborator leaves, an
// owner shares and kicks.
function isSharedWithMe(cid) {
  const entry = _sharedConvEntry(cid);
  return !!(entry && entry.status === 'accepted');
}

function sharedConvRole(cid) {
  const entry = _sharedConvEntry(cid);
  return entry && entry.status === 'accepted' ? entry.role : '';
}

// ── Message authorship ───────────────────────────────────────────

// A user bubble written by somebody else. Until sharing there was never
// more than one human in a conversation, so every user bubble looked the
// same; on a shared one, "who said this" is the difference between a note
// to self and a message from a colleague.
//
// Compared against the viewer rather than the conversation's owner: what
// matters is "not mine", and it is the only comparison that works the same
// way in an owned conversation and in a shared one.
function _authorBadgeHtml(extra) {
  const author = extra && extra.source && extra.source.type === 'user'
    ? String(extra.source.name || '') : '';
  if (!author || !window._userId || author === window._userId) return '';
  return '<span class="msg-author">'
    + escapeHtml(t('sharedMessageFrom', { user: author })) + '</span>';
}

// ── Sidebar rendering ────────────────────────────────────────────

function _convGroupHeader(label, count) {
  return '<div class="conv-group">' + escapeHtml(label)
    + '<span class="conv-group-count">' + count + '</span></div>';
}

function renderSharedSections(listEl) {
  const rows = window._sharedConvs || [];
  const pending = rows.filter(r => r.status === 'pending');
  const accepted = rows.filter(r => r.status === 'accepted');
  if (pending.length) {
    listEl.insertAdjacentHTML('beforeend',
      _convGroupHeader(t('pendingInvites'), pending.length));
    for (const r of pending) listEl.appendChild(_renderInviteRow(r));
  }
  if (accepted.length) {
    listEl.insertAdjacentHTML('beforeend',
      _convGroupHeader(t('sharedWithMe'), accepted.length));
    for (const r of accepted) listEl.appendChild(_renderSharedRow(r));
  }
}

function _renderInviteRow(row) {
  const el = document.createElement('div');
  el.className = 'conv-item conv-invite';
  el.dataset.cid = row.conversation_id;
  const title = row.title || t('newConversation');
  el.innerHTML = '<div class="conv-preview"><span class="conv-title">'
    + escapeHtml(title) + '</span></div>'
    + '<div class="conv-meta">'
    + escapeHtml(t('invitedBy', { user: row.invited_by || row.owner }))
    + ' \u00b7 ' + escapeHtml(t('role.' + row.role)) + '</div>'
    + '<div class="conv-invite-actions">'
    + '<button class="conv-invite-accept">' + escapeHtml(t('acceptInvite')) + '</button>'
    + '<button class="conv-invite-decline">' + escapeHtml(t('declineInvite')) + '</button>'
    + '</div>';
  el.querySelector('.conv-invite-accept').onclick = (ev) => {
    ev.stopPropagation();
    respondToShareInvite(row.conversation_id, 'accept');
  };
  el.querySelector('.conv-invite-decline').onclick = (ev) => {
    ev.stopPropagation();
    respondToShareInvite(row.conversation_id, 'decline');
  };
  return el;
}

function _renderSharedRow(row) {
  const el = document.createElement('div');
  el.className = 'conv-item conv-shared'
    + (row.conversation_id === conversationId ? ' active' : '');
  el.dataset.cid = row.conversation_id;
  const title = row.title || t('newConversation');
  const date = new Date((row.updated_at || 0) * 1000);
  const timeStr = date.toLocaleDateString() + ' '
    + date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  const readOnly = row.role === 'read'
    ? '<span class="conv-role-badge" title="' + escapeHtml(t('role.read')) + '">\u{1F441}</span>'
    : '';
  el.innerHTML = '<div class="conv-preview"><span class="conv-title">'
    + escapeHtml(title) + '</span>' + readOnly + '</div>'
    + '<div class="conv-meta">' + escapeHtml(t('ownedBy', { user: row.owner }))
    + ' \u00b7 ' + escapeHtml(t('contextMessages', { n: row.message_count || 0 }))
    + ' \u00b7 ' + timeStr + '</div>';
  el.onclick = () => resumeConv(row.conversation_id);
  el.oncontextmenu = (ev) => {
    ev.preventDefault();
    showSharedConvMenu(ev, row.conversation_id);
  };
  return el;
}

function showSharedConvMenu(e, cid) {
  const old = document.querySelector('.ctx-menu');
  if (old) old.remove();
  const menu = document.createElement('div');
  menu.className = 'ctx-menu';
  menu.style.minWidth = '180px';
  _positionMenu(menu, e);
  const item = (label, fn, danger) => {
    const d = document.createElement('div');
    d.className = 'ctx-menu-item' + (danger ? ' danger' : '');
    d.textContent = label;
    d.onclick = () => { menu.remove(); fn(); };
    menu.appendChild(d);
  };
  item('\u{21BB} ' + t('refresh'), () => resumeConv(cid, true));
  item('\u{1F6AA} ' + t('leaveConversation'), () => leaveSharedConv(cid), true);
  setTimeout(() => document.addEventListener('click', function _close() {
    menu.remove();
    document.removeEventListener('click', _close);
  }), 0);
}

// ── Invite responses ─────────────────────────────────────────────

function respondToShareInvite(cid, response) {
  action$('respond_to_share_invite', {
    conversation_id: cid, response: response,
  }).subscribe(data => {
    if (data.error) { addMsg('system', '\u26a0 ' + data.error); return; }
    addMsg('system', response === 'accept'
      ? t('inviteAccepted') : t('inviteDeclined'));
    loadConversations();
    if (response === 'accept') resumeConv(cid);
  });
}

function leaveSharedConv(cid) {
  if (!confirm(t('leaveConversationConfirm'))) return;
  action$('leave_conversation', { conversation_id: cid }).subscribe(data => {
    if (data.error) { addMsg('system', '\u26a0 ' + data.error); return; }
    addMsg('system', t('leftConversation'));
    if (cid === conversationId) newChat();
    loadConversations();
  });
}

// ── Share dialog (owner only) ────────────────────────────────────

function showShareDialog(cid) {
  action$('list_collaborators', { conversation_id: cid }).subscribe(data => {
    if (data.error) { addMsg('system', '\u26a0 ' + data.error); return; }
    if (data.role !== 'owner') {
      addMsg('system', '\u26a0 ' + t('shareOwnerOnly'));
      return;
    }
    _renderShareDialog(cid, data);
  });
}

function _collaboratorRow(cid, row) {
  const roleSelect = '<select class="share-role" data-user="'
    + escapeHtml(row.user_id) + '">'
    + '<option value="write"' + (row.role === 'write' ? ' selected' : '') + '>'
    + escapeHtml(t('role.write')) + '</option>'
    + '<option value="read"' + (row.role === 'read' ? ' selected' : '') + '>'
    + escapeHtml(t('role.read')) + '</option></select>';
  return '<div class="share-row" style="display:flex;align-items:center;gap:8px;padding:8px 0;border-bottom:1px solid #222;">'
    + '<span style="flex:1;color:#e0e0e0;font-size:13px;">' + escapeHtml(row.user_id)
    + ' <span style="color:#888;font-size:11px;">' + escapeHtml(t('status.' + row.status)) + '</span></span>'
    + roleSelect
    + '<button class="share-kick" data-user="' + escapeHtml(row.user_id)
    + '" style="padding:4px 10px;background:#333;color:#e94560;border:none;border-radius:4px;cursor:pointer;font-size:12px;">'
    + escapeHtml(t('kickCollaborator')) + '</button></div>';
}

function _renderShareDialog(cid, data) {
  const old = document.querySelector('.share-dialog-overlay');
  if (old) old.remove();
  const overlay = document.createElement('div');
  overlay.className = 'share-dialog-overlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:10000;display:flex;align-items:center;justify-content:center;';
  const dialog = document.createElement('div');
  dialog.style.cssText = 'background:#1a1a2e;border:1px solid #333;border-radius:8px;padding:20px;min-width:min(440px, calc(100vw - 32px));max-width:min(600px, calc(100vw - 32px));max-height:70vh;display:flex;flex-direction:column;';

  // Kicked rows stay in the ACL for the audit trail; they are history, not
  // access, so the dialog lists only live ones.
  const live = (data.collaborators || []).filter(r => r.status !== 'kicked');
  let html = '<div style="font-size:14px;font-weight:600;color:#e0e0e0;margin-bottom:4px;">'
    + escapeHtml(t('shareConversation')) + '</div>'
    + '<div style="font-size:11px;color:#888;margin-bottom:12px;">'
    + escapeHtml(t('shareInviteHint')) + '</div>'
    + '<div style="display:flex;gap:8px;margin-bottom:12px;">'
    + '<input id="shareUserInput" type="text" placeholder="' + escapeHtml(t('shareUserPlaceholder'))
    + '" style="flex:1;padding:6px 10px;background:#0f0f1e;border:1px solid #333;border-radius:4px;color:#e0e0e0;font-size:13px;">'
    + '<select id="shareRoleInput" style="padding:6px;background:#0f0f1e;border:1px solid #333;border-radius:4px;color:#e0e0e0;font-size:13px;">'
    + '<option value="write">' + escapeHtml(t('role.write')) + '</option>'
    + '<option value="read">' + escapeHtml(t('role.read')) + '</option></select>'
    + '<button id="shareInviteBtn" style="padding:6px 14px;background:#6c5ce7;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:13px;">'
    + escapeHtml(t('invite')) + '</button></div>';

  html += '<div style="overflow-y:auto;flex:1;">';
  html += live.length
    ? live.map(r => _collaboratorRow(cid, r)).join('')
    : '<div style="color:#6c6c8a;font-size:12px;padding:8px 0;">'
      + escapeHtml(t('noCollaborators')) + '</div>';
  html += '</div>';

  // The encryption caveat is worth stating plainly: sharing does not give a
  // collaborator their own unlock capability, so an idle-locked vault takes
  // the conversation away from everyone at once.
  const own = (window._ownConvs || []).find(c => c.conversation_id === cid);
  if (own && (own.encryption === 'locked' || own.encryption === 'unlocked')) {
    html += '<div style="font-size:11px;color:#e6b800;margin-top:10px;">\u26a0 '
      + escapeHtml(t('shareEncryptedWarning')) + '</div>';
  }
  html += '<div style="margin-top:14px;text-align:right;">'
    + '<button onclick="this.closest(\'.share-dialog-overlay\').remove()" '
    + 'style="padding:6px 16px;background:#333;color:#e0e0e0;border:none;border-radius:4px;cursor:pointer;">'
    + escapeHtml(t('close')) + '</button></div>';

  dialog.innerHTML = html;
  overlay.appendChild(dialog);
  document.body.appendChild(overlay);

  dialog.querySelector('#shareInviteBtn').onclick = () => {
    const input = dialog.querySelector('#shareUserInput');
    const target = (input.value || '').trim();
    if (!target) return;
    action$('share_conversation', {
      conversation_id: cid,
      collaborator_id: target,
      role: dialog.querySelector('#shareRoleInput').value,
    }).subscribe(res => {
      if (res.error) { addMsg('system', '\u26a0 ' + res.error); return; }
      input.value = '';
      showShareDialog(cid);
    });
  };
  dialog.querySelectorAll('.share-role').forEach(sel => {
    sel.onchange = () => {
      action$('update_collaborator_role', {
        conversation_id: cid,
        collaborator_id: sel.dataset.user,
        role: sel.value,
      }).subscribe(res => {
        if (res.error) addMsg('system', '\u26a0 ' + res.error);
      });
    };
  });
  dialog.querySelectorAll('.share-kick').forEach(btn => {
    btn.onclick = () => {
      if (!confirm(t('kickCollaboratorConfirm', { user: btn.dataset.user }))) return;
      action$('kick_collaborator', {
        conversation_id: cid,
        collaborator_id: btn.dataset.user,
      }).subscribe(res => {
        if (res.error) { addMsg('system', '\u26a0 ' + res.error); return; }
        showShareDialog(cid);
      });
    };
  });
}

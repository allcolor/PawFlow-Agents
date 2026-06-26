// \xe2\x94\x80\xe2\x94\x80 Conversation context menu + git/branch dialogs \xe2\x94\x80\xe2\x94\x80
// Split out of conversations.js (<=800 lines). All globals.

// ── Git versioning context menu ──────────────────────────────────

function showConvMenu(e, cid, status) {
  const old = document.querySelector('.ctx-menu');
  if (old) old.remove();
  const menu = document.createElement('div');
  menu.className = 'ctx-menu';
  menu.style.cssText = 'position:fixed;z-index:10000;background:#1a1a2e;border:1px solid #333;border-radius:6px;padding:4px 0;min-width:180px;box-shadow:0 4px 12px rgba(0,0,0,0.5);';
  menu.style.left = e.clientX + 'px';
  menu.style.top = e.clientY + 'px';
  document.body.appendChild(menu);
  requestAnimationFrame(() => {
    const rect = menu.getBoundingClientRect();
    if (rect.bottom > window.innerHeight) menu.style.top = Math.max(0, e.clientY - rect.height) + 'px';
    if (rect.right > window.innerWidth) menu.style.left = Math.max(0, e.clientX - rect.width) + 'px';
  });

  const idle = !status || status === 'idle';
  const item = (label, fn, opts) => {
    const d = document.createElement('div');
    d.textContent = label;
    const disabled = opts && opts.disabled;
    d.style.cssText = 'padding:6px 16px;cursor:' + (disabled ? 'default' : 'pointer') + ';font-size:12px;color:' + (disabled ? '#555' : (opts && opts.danger ? '#e94560' : '#e0e0e0'));
    if (!disabled) {
      d.onmouseenter = () => d.style.background = '#2a2a4a';
      d.onmouseleave = () => d.style.background = '';
      d.onclick = () => { menu.remove(); fn(); };
    }
    menu.appendChild(d);
  };
  const sep = () => {
    const s = document.createElement('div');
    s.style.cssText = 'height:1px;background:#333;margin:4px 0;';
    menu.appendChild(s);
  };

  item('\u{1F4E5} ' + t('export'), () => showExportDialog(cid));
  item('\u{21BB} ' + t('refresh'), () => resumeConv(cid, true));
  item('\u{1F5D1} ' + t('delete'), () => deleteConversationById(cid), { danger: true });
  sep();
  item('\u{1F500} Fork', () => convFork(cid), { disabled: !idle });
  item('\u{1F33F} Branch...', () => convBranchPrompt(cid), { disabled: !idle });
  item('\u{21C4} Switch branch...', () => convSwitchBranchDialog(cid), { disabled: !idle });
  item('\u{23EA} Rollback to...', () => convRollbackDialog(cid), { disabled: !idle });
  sep();
  item('\u{1F3F7} Tag...', () => convTagPrompt(cid));
  item('\u{1F4CB} Compare branches...', () => convCompareBranchesDialog(cid));
  sep();
  item('\u{1F5D1} Delete branch...', () => convDeleteBranchDialog(cid), { danger: true, disabled: !idle });

  setTimeout(() => document.addEventListener('click', function _close() { menu.remove(); document.removeEventListener('click', _close); }), 0);
}

function convFork(cid) {
  action$('conv_fork', { conversation_id: cid }).subscribe(data => {
    if (data.error) { addMsg('system', '\u26a0 ' + t('forkFailed', { error: data.error })); return; }
    addMsg('system', t('forkedConversation', { id: data.conversation_id.slice(0, 8) }));
    loadConversations();
    resumeConv(data.conversation_id);
  });
}

function convBranchPrompt(cid) {
  const name = prompt(t('branchNamePrompt'));
  if (!name || !name.trim()) return;
  action$('conv_branch', { conversation_id: cid, branch_name: name.trim() }).subscribe(data => {
    if (data.error) { addMsg('system', '\u26a0 ' + data.error); return; }
    addMsg('system', t('branchCreated', { name: name.trim() }));
    loadConversations();
    if (cid === conversationId) resumeConv(conversationId, true);
  });
}

function convTagPrompt(cid) {
  const name = prompt(t('tagNamePrompt'));
  if (!name || !name.trim()) return;
  action$('conv_tag', { conversation_id: cid, tag_name: name.trim() }).subscribe(data => {
    if (data.error) { addMsg('system', '\u26a0 ' + data.error); return; }
    addMsg('system', t('taggedConversation', { name: name.trim() }));
  });
}

function convSwitchBranchDialog(cid) {
  action$('conv_list_branches', { conversation_id: cid }).subscribe(data => {
    if (data.error) { addMsg('system', '\u26a0 ' + data.error); return; }
    const branches = data.branches || [];
    if (branches.length <= 1) { addMsg('system', t('noOtherBranches')); return; }
    _showGitDialog(t('switchBranch'), branches.map(b => {
      const current = b.current ? ' \u2190 ' + t('currentBranchLabel') : '';
      return { label: b.name + current, value: b.name, disabled: b.current };
    }), (selected) => {
      action$('conv_switch_branch', { conversation_id: cid, branch_name: selected }).subscribe(res => {
        if (res.error) { addMsg('system', '\u26a0 ' + res.error); return; }
        addMsg('system', t('switchedToBranch', { name: selected }));
        loadConversations();
        if (cid === conversationId) resumeConv(conversationId, true);
      });
    });
  });
}

function convDeleteBranchDialog(cid) {
  action$('conv_list_branches', { conversation_id: cid }).subscribe(data => {
    if (data.error) { addMsg('system', '\u26a0 ' + data.error); return; }
    const branches = (data.branches || []).filter(b => !b.current);
    if (branches.length === 0) { addMsg('system', t('noBranchesToDelete')); return; }
    _showGitDialog(t('deleteBranch'), branches.map(b => {
      return { label: b.name, value: b.name };
    }), (selected) => {
      if (!confirm(t('deleteBranchConfirm', { name: selected }))) return;
      action$('conv_delete_branch', { conversation_id: cid, branch_name: selected }).subscribe(res => {
        if (res.error) { addMsg('system', '\u26a0 ' + res.error); return; }
        addMsg('system', t('branchDeleted', { name: selected }));
        loadConversations();
      });
    });
  });
}

function convCompareBranchesDialog(cid) {
  action$('conv_list_branches', { conversation_id: cid }).subscribe(data => {
    if (data.error) { addMsg('system', '\u26a0 ' + data.error); return; }
    const branches = data.branches || [];
    if (branches.length < 2) { addMsg('system', t('needTwoBranchesToCompare')); return; }
    const current = data.current || branches[0].name;
    const other = branches.find(b => b.name !== current);
    const a = prompt(t('branchAPrompt'), current);
    if (!a) return;
    const b = prompt(t('branchBPrompt'), other ? other.name : '');
    if (!b) return;
    action$('conv_compare_branches', { conversation_id: cid, branch_a: a, branch_b: b }).subscribe(res => {
      if (res.error) { addMsg('system', '\u26a0 ' + res.error); return; }
      const lines = [
        '**' + t('branchComparisonHeader', { a: a, b: b }) + '**',
        t('commitsAhead', { count: res.commits_ahead || 0 }),
        t('commitsBehind', { count: res.commits_behind || 0 }),
        t('messagesInBranch', { branch: a, count: res.messages_a || 0 }),
        t('messagesInBranch', { branch: b, count: res.messages_b || 0 }),
      ];
      addMsg('system', lines.join('\n'));
    });
  });
}

function convRollbackDialog(cid) {
  action$('conv_git_log', { conversation_id: cid, limit: 30 }).subscribe(data => {
    if (data.error) { addMsg('system', '\u26a0 ' + data.error); return; }
    const commits = data.commits || [];
    if (commits.length === 0) { addMsg('system', t('noCommitsFound')); return; }

    const old = document.querySelector('.git-dialog-overlay');
    if (old) old.remove();
    const overlay = document.createElement('div');
    overlay.className = 'git-dialog-overlay';
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:10000;display:flex;align-items:center;justify-content:center;';
    const dialog = document.createElement('div');
    dialog.style.cssText = 'background:#1a1a2e;border:1px solid #333;border-radius:8px;padding:20px;min-width:500px;max-width:700px;max-height:70vh;display:flex;flex-direction:column;';

    let html = '<div style="font-size:14px;font-weight:600;color:#e0e0e0;margin-bottom:12px;">' + escapeHtml(t('rollbackConversation')) + (data.branch ? ' (' + escapeHtml(data.branch) + ')' : '') + '</div>';
    html += '<div style="overflow-y:auto;flex:1;margin-bottom:12px;">';
    for (let i = 0; i < commits.length; i++) {
      const c = commits[i];
      const ts = new Date(c.timestamp * 1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'});
      const date = new Date(c.timestamp * 1000).toLocaleDateString();
      const tag = c.tag ? ' <span style="color:#6c5ce7;">[' + escapeHtml(c.tag) + ']</span>' : '';
      html += '<div class="git-commit-row" data-hash="' + c.hash + '" style="padding:8px 12px;border-bottom:1px solid #222;cursor:pointer;font-size:12px;"'
        + ' onmouseenter="this.style.background=\'#2a2a4a\'" onmouseleave="this.style.background=\'\'">'
        + '<span style="color:#6c5ce7;font-family:monospace;">' + c.hash.slice(0, 7) + '</span>'
        + ' <span style="color:#888;">' + date + ' ' + ts + '</span>' + tag
        + '<br><span style="color:#ccc;">' + escapeHtml(c.message) + '</span></div>';
    }
    html += '</div>';
    html += '<div style="margin-bottom:12px;"><label style="font-size:12px;color:#e0e0e0;cursor:pointer;">'
      + '<input type="checkbox" id="gitRollbackFiles" style="margin-right:6px;">'
      + escapeHtml(t('alsoRewindUserFiles')) + '</label>'
      + '<div style="font-size:11px;color:#e94560;margin-top:4px;padding-left:20px;">'
      + '\u26a0 ' + escapeHtml(t('rewindFilesRiskWarning')) + '</div></div>';
    html += '<div style="display:flex;gap:8px;justify-content:flex-end;">'
      + '<button onclick="this.closest(\'.git-dialog-overlay\').remove()" style="padding:6px 16px;background:#333;color:#e0e0e0;border:none;border-radius:4px;cursor:pointer;">' + escapeHtml(t('cancel')) + '</button>'
      + '<button id="gitRollbackBtn" disabled style="padding:6px 16px;background:#6c5ce7;color:#fff;border:none;border-radius:4px;cursor:pointer;opacity:0.5;">' + escapeHtml(t('rollback')) + '</button></div>';

    dialog.innerHTML = html;
    overlay.appendChild(dialog);
    document.body.appendChild(overlay);


    let selectedHash = null;
    dialog.querySelectorAll('.git-commit-row').forEach(row => {
      row.onclick = () => {
        dialog.querySelectorAll('.git-commit-row').forEach(r => r.style.border = '');
        row.style.border = '1px solid #6c5ce7';
        selectedHash = row.dataset.hash;
        const btn = document.getElementById('gitRollbackBtn');
        btn.disabled = false;
        btn.style.opacity = '1';
      };
    });

    document.getElementById('gitRollbackBtn').onclick = () => {
      if (!selectedHash) return;
      const rewindFiles = document.getElementById('gitRollbackFiles').checked;
      overlay.remove();
      addMsg('system', t('rollingBackTo', { hash: selectedHash.slice(0, 7) }));
      action$('conv_rollback', { conversation_id: cid, commit_hash: selectedHash, rewind_files: rewindFiles }).subscribe(res => {
        if (res.error) { addMsg('system', '\u26a0 ' + res.error); return; }
        let msg = t('rolledBackTo', { hash: selectedHash.slice(0, 7) });
        if (res.files) {
          if (res.files.error) msg += '\n' + t('fileRewindError', { error: res.files.error });
          else msg += '\n' + t('filesRewindSummary', { restored: res.files.restored || 0, deleted: res.files.deleted || 0 });
        }
        addMsg('system', msg);
        loadConversations();
        if (cid === conversationId) resumeConv(conversationId, true);
      });
    };
  });
}

function _showGitDialog(title, items, onSelect) {
  const old = document.querySelector('.git-dialog-overlay');
  if (old) old.remove();
  const overlay = document.createElement('div');
  overlay.className = 'git-dialog-overlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:10000;display:flex;align-items:center;justify-content:center;';
  const dialog = document.createElement('div');
  dialog.style.cssText = 'background:#1a1a2e;border:1px solid #333;border-radius:8px;padding:20px;min-width:300px;max-width:400px;';
  let html = '<div style="font-size:14px;font-weight:600;color:#e0e0e0;margin-bottom:12px;">' + escapeHtml(title) + '</div>';
  for (const it of items) {
    const dis = it.disabled ? ' style="color:#555;cursor:default;"' : '';
    html += '<div class="git-list-item" data-value="' + escapeHtml(it.value) + '"' + (it.disabled ? ' data-disabled="1"' : '')
      + ' style="padding:8px 12px;cursor:' + (it.disabled ? 'default' : 'pointer') + ';font-size:13px;color:' + (it.disabled ? '#555' : '#e0e0e0')
      + ';border-bottom:1px solid #222;"'
      + (!it.disabled ? ' onmouseenter="this.style.background=\'#2a2a4a\'" onmouseleave="this.style.background=\'\'"' : '')
      + '>' + escapeHtml(it.label) + '</div>';
  }
  html += '<div style="margin-top:12px;text-align:right;"><button onclick="this.closest(\'.git-dialog-overlay\').remove()" style="padding:6px 16px;background:#333;color:#e0e0e0;border:none;border-radius:4px;cursor:pointer;">Cancel</button></div>';
  dialog.innerHTML = html;
  overlay.appendChild(dialog);
  document.body.appendChild(overlay);

  dialog.querySelectorAll('.git-list-item').forEach(row => {
    if (row.dataset.disabled) return;
    row.onclick = () => { overlay.remove(); onSelect(row.dataset.value); };
  });
}

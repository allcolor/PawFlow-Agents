// ── Attachments ──────────────────────────────────────────────────
function removeFile(idx) {
  pendingFiles.splice(idx, 1);
  renderAttachments();
}

function renderAttachments() {
  const preview = document.getElementById('attachPreview');
  preview.innerHTML = '';
  pendingFiles.forEach((f, i) => {
    const el = document.createElement('div');
    el.className = 'att-item';
    if (f.uploading) el.style.opacity = '0.5';
    const isImage = f.mime_type.startsWith('image/');
    if (isImage && f.dataUrl) {
      el.innerHTML = '<img src="' + f.dataUrl + '" alt="' + escapeHtml(f.filename) + '">';
    } else {
      const icons = {'application/pdf': '\u{1F4C4}', 'text/plain': '\u{1F4DD}', 'text/html': '\u{1F310}', 'text/markdown': '\u{1F4DD}'};
      el.innerHTML = '<span class="att-icon">' + (icons[f.mime_type] || '\u{1F4CE}') + '</span>';
    }
    el.innerHTML += '<span>' + escapeHtml(f.filename) + (f.uploading ? ' ⏳' : '') + '</span>'
      + '<button class="att-remove" onclick="removeFile(' + i + ')">\u00d7</button>';
    preview.appendChild(el);
  });
}

function renderUserAttachments(attachments) {
  // Render attachment badges in user message
  let html = '';
  for (const att of attachments) {
    if (att.mime_type && att.mime_type.startsWith('image/')) {
      const rawImgSrc = att.url || att.dataUrl || (att.file_id ? '/files/' + encodeURIComponent(att.file_id) + '/' + encodeURIComponent(att.filename) : '');
      const imgSrc = (typeof normalizePawFlowFileUrl === 'function') ? normalizePawFlowFileUrl(rawImgSrc) : rawImgSrc;
      html += '<img class="chat-image" src="' + imgSrc + '">';
    } else {
      html += '<span class="doc-badge">\u{1F4CE} ' + escapeHtml(att.filename) + '</span> ';
    }
  }
  return html;
}

// Drag and drop support
document.addEventListener('DOMContentLoaded', () => {
  const main = document.querySelector('.main');
  main.addEventListener('dragover', (e) => { e.preventDefault(); e.stopPropagation(); });
  main.addEventListener('drop', (e) => {
    e.preventDefault(); e.stopPropagation();
    if (e.dataTransfer.files.length) handleFiles(e.dataTransfer.files);
  });
});

// Clipboard paste support (Ctrl+V images)
document.getElementById('input').addEventListener('paste', (e) => {
  const items = e.clipboardData && e.clipboardData.items;
  if (!items) return;
  for (const item of items) {
    if (item.type.startsWith('image/')) {
      e.preventDefault();
      const file = item.getAsFile();
      if (file) handleFiles([file]);
      return;
    }
  }
});

function copyMsg(btn) {
  const msg = btn.closest('.msg');
  if (!msg) return;
  const text = messageTextForAction(msg);
  navigator.clipboard.writeText(text).then(() => {
    btn.textContent = '\u2705';
    setTimeout(() => { btn.textContent = '\u{1F4CB}'; }, 1500);
  });
}

function messageTextForAction(msg) {
  if (!msg) return '';
  const clone = msg.cloneNode(true);
  for (const sel of ['.msg-actions', '.source-badge', '.msg-time', '.msg-meta']) {
    const el = clone.querySelector(sel);
    if (el) el.remove();
  }
  let text = (clone.textContent || clone.innerText).trim();
  // Strip target badge prefix like "[→ assistant] " or "[btw → agent] "
  return text.replace(/^\[(btw\s*)?\u2192\s*[^\]]+\]\s*/, '');
}

function speakMsg(btn) {
  const msg = btn.closest('.msg');
  if (!msg || typeof conversationTTSSpeakText !== 'function') return;
  conversationTTSSpeakText(messageTextForAction(msg));
}

function copyMsgId(btn) {
  const msg = btn.closest('.msg');
  if (!msg || !msg.dataset.msgid) return;
  navigator.clipboard.writeText(msg.dataset.msgid).then(() => {
    btn.textContent = '\u2705';
    setTimeout(() => { btn.textContent = 'ID'; }, 1500);
  });
}

function setPromptTextForRestart(text) {
  const input = document.getElementById('input');
  if (!input) return;
  input.value = text || '';
  savedDraft = input.value;
  input.dispatchEvent(new Event('input', { bubbles: true }));
  input.focus();
}

function restartTargetForUserMessage(msg) {
  const messages = Array.from(document.querySelectorAll('#messages .msg[data-msgid]'));
  const idx = messages.indexOf(msg);
  if (idx <= 0) return { restart_index: 0 };
  const prev = messages[idx - 1];
  return prev && prev.dataset.msgid ? { msg_id: prev.dataset.msgid } : { restart_index: 0 };
}

function restartParamsForMessage(msg) {
  if (!msg || !msg.dataset.msgid) return null;
  return msg.dataset.messageRole === 'user'
    ? restartTargetForUserMessage(msg)
    : { msg_id: msg.dataset.msgid };
}

function restartFromMsg(btn) {
  const msg = btn.closest('.msg');
  if (!msg || !msg.dataset.msgid || !conversationId) return;
  if (!confirm(t('restartFromHereConfirm'))) return;
  const isUserMessage = msg.dataset.messageRole === 'user';
  const restartParams = restartParamsForMessage(msg);
  if (!restartParams) return;
  if (isUserMessage) setPromptTextForRestart(messageTextForAction(msg));
  showContextOp(t('contextRestarting'));
  action$('restart_from', restartParams).subscribe(data => {
    if (data.error) {
      hideContextOp();
      addMsg('error', data.error);
      return;
    }
    hideContextOp();
    if (conversationId) resumeConv(conversationId, true);
    const promptText = data.restart_prompt_text || data.prompt_text || '';
    if (promptText && typeof setPromptTextForRestart === 'function') {
      setTimeout(() => setPromptTextForRestart(promptText), 100);
    }
  });
}

function deleteMsg(btn) {
  const msg = btn.closest('.msg');
  if (!msg || !conversationId) return;
  // If there are selected messages, delete all selected
  if (_selectedMsgIds.size > 0) {
    deleteSelectedMessages();
    return;
  }
  const mid = msg.dataset.msgid;
  if (!mid) { msg.remove(); return; }
  action$('delete_message', { msg_id: mid }).subscribe(data => {
    if (data.deleted) {
      resumeConv(conversationId, true);
    }
  });
}

function toggleMsgSelect(el, event) {
  if (!el || !el.dataset.msgid) return;
  const mid = el.dataset.msgid;
  if (event && event.shiftKey && _selectedMsgIds.size > 0) {
    // Range select: select all between last selected and this one
    const msgs = Array.from(document.querySelectorAll('.msg[data-msgid]'));
    const lastIdx = msgs.findIndex(m => m.classList.contains('msg-selected'));
    const curIdx = msgs.indexOf(el);
    if (lastIdx >= 0 && curIdx >= 0) {
      const [from, to] = lastIdx < curIdx ? [lastIdx, curIdx] : [curIdx, lastIdx];
      for (let i = from; i <= to; i++) {
        msgs[i].classList.add('msg-selected');
        if (msgs[i].dataset.msgid) _selectedMsgIds.add(msgs[i].dataset.msgid);
      }
    }
  } else if (event && event.ctrlKey) {
    // Toggle individual
    if (_selectedMsgIds.has(mid)) {
      _selectedMsgIds.delete(mid);
      el.classList.remove('msg-selected');
    } else {
      _selectedMsgIds.add(mid);
      el.classList.add('msg-selected');
    }
  } else {
    // Clear all and select this one
    clearMsgSelection();
    _selectedMsgIds.add(mid);
    el.classList.add('msg-selected');
  }
  updateDeleteSelectedBar();
}

function clearMsgSelection() {
  _selectedMsgIds.clear();
  document.querySelectorAll('.msg-selected').forEach(m => m.classList.remove('msg-selected'));
  updateDeleteSelectedBar();
}

function updateDeleteSelectedBar() {
  let bar = document.getElementById('deleteSelectedBar');
  if (_selectedMsgIds.size === 0) {
    if (bar) bar.style.display = 'none';
    return;
  }
  if (!bar) {
    bar = document.createElement('div');
    bar.id = 'deleteSelectedBar';
    bar.style.cssText = 'position:fixed;bottom:80px;left:50%;transform:translateX(-50%);background:#e94560;color:#fff;padding:6px 16px;border-radius:6px;font-size:13px;z-index:1000;display:flex;align-items:center;gap:10px;box-shadow:0 2px 8px rgba(0,0,0,0.3);';
    document.body.appendChild(bar);
  }
  bar.innerHTML = '<span>' + _selectedMsgIds.size + ' selected</span>'
    + '<button onclick="deleteSelectedMessages()" style="background:#fff;color:#e94560;border:none;padding:3px 10px;border-radius:4px;cursor:pointer;font-weight:bold;">Delete</button>'
    + '<button onclick="clearMsgSelection()" style="background:transparent;color:#fff;border:1px solid #fff;padding:3px 8px;border-radius:4px;cursor:pointer;">Cancel</button>';
  bar.style.display = 'flex';
}

function deleteSelectedMessages() {
  if (!_selectedMsgIds.size || !conversationId) return;
  const ids = Array.from(_selectedMsgIds);
  action$('delete_message', { msg_ids: ids }).subscribe(data => {
    if (data.deleted) {
      clearMsgSelection();
      resumeConv(conversationId, true);
      return;
    }
    clearMsgSelection();
  });
}

function cancelAgent(target) {
  if (!conversationId) return;
  document.getElementById('stopBtn').style.display = 'none';
  document.getElementById('status').textContent = t('cancelling');
  const params = { force: true };
  if (target && target !== 'ALL') params.agent_name = target;
  fireAction('cancel', params);
  // SSE "cancelled" event will handle the rest
}

async function send() {
  const input = document.getElementById('input');
  let text = input.value.trim();
  if (!text && pendingFiles.length === 0) return;

  // before_send filter — extensions can mutate (text, attachments).
  if (window._pawflowExtRuntime) {
    var _bsPayload = window._pawflowExtRuntime.fireFilter('before_send', {
      text: text, attachmentsCount: pendingFiles.length,
    });
    if (_bsPayload && typeof _bsPayload.text === 'string') {
      text = _bsPayload.text;
      input.value = text;
    }
    if (_bsPayload && _bsPayload.cancel === true) return;
  }

  // Save to message history (before slash command intercept so commands are in history too)
  if (text) {
    messageHistory.unshift(text);
    if (messageHistory.length > 50) messageHistory.pop();
    localStorage.setItem('pawflow_msg_history', JSON.stringify(messageHistory.slice(0, 50)));
  }
  historyIndex = -1;
  savedDraft = '';

  // Intercept slash commands
  if (text.startsWith('/')) {
    const handled = await handleSlashCommand(text);
    if (handled) { input.value = ''; input.style.height = 'auto'; input.focus(); return; }
  }


  // Capture and clear attachments
  // Wait for any uploads still in progress
  if (pendingFiles.some(f => f.uploading)) {
    addMsg('system', t('filesStillUploading'));
    return;
  }
  const attachments = pendingFiles.map(f => ({
    filename: f.filename, mime_type: f.mime_type, file_id: f.file_id,
  }));
  const attachmentsForDisplay = [...pendingFiles];
  pendingFiles = [];
  renderAttachments();

  // Allow stacking: don't block on 'sending', just track pending count
  if (typeof _ensureSSEBeforeUserAction === 'function') _ensureSSEBeforeUserAction();
  sending = true;
  document.getElementById('status').textContent = t('sending');
  input.value = '';
  input.style.height = 'auto';

  // Generate msg_id client-side so dedup works across SSE + replay
  const userMsgId = (crypto.randomUUID ? crypto.randomUUID() : ([1e7]+-1e3+-4e3+-8e3+-1e11).replace(/[018]/g, c => (c ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> c / 4).toString(16))).replace(/-/g, '').slice(0, 12);

  // Show user message with target badge (all messages explicitly show who they go to)
  const targetAgent = selectedAgent || '';
  const userSource = { type: 'user', name: '', target_agent: targetAgent };
  const msgEl = addMsg('user', text || '', { source: userSource, msg_id: userMsgId });
  if (attachmentsForDisplay.length > 0) {
    msgEl.innerHTML = sourceBadge(userSource) + escapeHtml(text || '') + renderUserAttachments(attachmentsForDisplay);
  }
  scrollBottom(true);  // Force scroll when user sends
  // Finalize all active streaming elements so the user message
  // appears BELOW them (not interleaved above ongoing text)
  for (const key of Object.keys(streams)) {
    const s = streams[key];
    if (s && s.el) {
      s.el.classList.add('finalized');
      s.el.dataset.finalizedAgent = key;
      s.lastEl = s.el;
      s.el = null; s.text = '';
    }
  }
  try {
    const body = { message: text, target_agent: targetAgent, msg_id: userMsgId };
    if (conversationId) body.conversation_id = conversationId;
    if (attachments.length > 0) body.attachments = attachments;
    if (pendingAgent) { body.pending_agent = pendingAgent; pendingAgent = null; }
    if (_replyTo) { body.reply_to = _replyTo; cancelReply(); }
    const ttlVal = parseInt(document.getElementById('ttlSelect').value, 10);
    if (ttlVal > 0) body.ttl = ttlVal;

    let resp;
    const jsonBody = JSON.stringify(body);
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        resp = await fetch(API, {
          method: 'POST',
          headers: getAuthHeaders(),
          body: jsonBody,
          credentials: 'same-origin',
          redirect: 'manual',
        });
        break;  // success
      } catch (fetchErr) {
        if (attempt < 2) {
          console.warn('Fetch attempt ' + (attempt+1) + ' failed, retrying...', fetchErr);
          await new Promise(r => setTimeout(r, 500));
        } else {
          throw fetchErr;
        }
      }
    }

    // Session expired → 401 JSON or opaque redirect (302 to OAuth)
    if (resp.type === 'opaqueredirect' || resp.status === 401 || resp.status === 403) {
      if (LOGIN_URL) { window.location.href = LOGIN_URL; return; }
      addMsg('error', t('sessionExpired'));
      sending = false;
      document.getElementById('status').textContent = t('ready');
      return;
    }

    if (!resp.ok) {
      const errText = await resp.text();
      addMsg('error', t('httpErrorWithStatus', { status: resp.status, error: errText }));
      sending = false;
      document.getElementById('status').textContent = t('error');
      return;
    }

    const data = await resp.json();
    if (typeof _checkServerRestart === 'function') _checkServerRestart(data);
    const cid = data.conversation_id || conversationId;
    if (cid && cid !== conversationId) {
      conversationId = cid;
      // Sync message count/offset from server to prevent load-more overlap.
      if (typeof _noteLiveHistoryAppend === 'function') {
        _noteLiveHistoryAppend(data.message_count, 1);
      } else {
        serverMsgCount = data.message_count || 1;
      }
      connectSSE(cid);  // Start/reconnect SSE for this conversation
      startSSEHealthTimer();
      updateDeleteBtn();
      loadConversations();  // Show new conversation in sidebar immediately
    }

    // If streaming mode: events come via SSE, don't show response here
    if (data.status === 'accepted') {
      if (typeof _noteLiveHistoryAppend === 'function') {
        _noteLiveHistoryAppend(data.message_count, 1);
      } else if (data.message_count) serverMsgCount = data.message_count;
      document.getElementById('status').textContent = t('thinking');
      document.getElementById('stopBtn').style.display = '';
      // SSE will handle the rest
      return;
    }

    // Message queued — agent is busy, message will be picked up at next checkpoint
    if (data.status === 'queued') {
      if (typeof _noteLiveHistoryAppend === 'function') {
        _noteLiveHistoryAppend(data.message_count, 1);
      } else if (data.message_count) serverMsgCount = data.message_count;
      sending = false;
      // Agent is already working — the message is persisted and will be injected
      return;
    }

    // Non-streaming mode: show response directly
    conversationId = data.conversation_id || conversationId;
    const nsExtra = data.source ? { source: data.source } : undefined;
    addMsg('assistant', data.response || data.content || JSON.stringify(data), nsExtra);
    sending = false;
    document.getElementById('status').textContent = t('ready');
    loadConversations();
    loadResources();

  } catch (e) {
    console.error('send() failed:', e);
    addMsg('error', t('connError', {msg: e.message + ' (check console)'}));
    sending = false;
    document.getElementById('status').textContent = t('error');
  }
}

let _lastEscapeTime = 0;
let _lastEscapeTarget = '';
let _skillAutocomplete = { open: false, items: [], filtered: [], index: 0, query: '', loading: null };

function _skillAutocompleteToken(input) {
  const pos = input.selectionStart || 0;
  const before = input.value.slice(0, pos);
  if (!before.startsWith('//')) return null;
  if (/\s/.test(before.slice(2))) return null;
  return { query: before.slice(2), start: 0, end: pos };
}

async function _loadSkillAutocompleteItems() {
  if (_skillAutocomplete.items.length) return _skillAutocomplete.items;
  if (_skillAutocomplete.loading) return _skillAutocomplete.loading;
  _skillAutocomplete.loading = rxjs.firstValueFrom(action$('list_skills', _convScope(), { silent: true }))
    .then(data => {
      _skillAutocomplete.items = (data.skills || [])
        .filter(s => s && s.name)
        .sort((a, b) => String(a.name).localeCompare(String(b.name)));
      return _skillAutocomplete.items;
    })
    .finally(() => { _skillAutocomplete.loading = null; });
  return _skillAutocomplete.loading;
}

function _hideSkillAutocomplete() {
  const el = document.getElementById('skillAutocomplete');
  if (el) el.remove();
  _skillAutocomplete.open = false;
}

function _renderSkillAutocomplete(input, query) {
  const q = String(query || '').toLowerCase();
  _skillAutocomplete.query = query || '';
  _skillAutocomplete.filtered = _skillAutocomplete.items.filter(s => {
    const hay = (String(s.name || '') + ' ' + String(s.description || '')).toLowerCase();
    return !q || hay.includes(q);
  }).slice(0, 12);
  if (_skillAutocomplete.index >= _skillAutocomplete.filtered.length) _skillAutocomplete.index = 0;
  let box = document.getElementById('skillAutocomplete');
  if (!box) {
    box = document.createElement('div');
    box.id = 'skillAutocomplete';
    box.style.cssText = 'position:fixed;z-index:10001;min-width:260px;max-width:520px;max-height:280px;overflow-y:auto;background:var(--pf-panel);border:1px solid var(--pf-border);border-radius:6px;box-shadow:0 8px 24px var(--pf-shadow);padding:4px;';
    document.body.appendChild(box);
  }
  const rect = input.getBoundingClientRect();
  box.style.left = rect.left + 'px';
  box.style.bottom = (window.innerHeight - rect.top + 6) + 'px';
  if (!_skillAutocomplete.filtered.length) {
    box.innerHTML = '<div style="padding:8px 10px;color:var(--pf-muted);font-size:12px;">No skills</div>';
    _skillAutocomplete.open = true;
    return;
  }
  box.innerHTML = _skillAutocomplete.filtered.map((s, i) => {
    const active = i === _skillAutocomplete.index;
    return '<div class="skill-ac-item" data-index="' + i + '" style="padding:7px 9px;border-radius:4px;cursor:pointer;background:' + (active ? 'color-mix(in srgb, var(--pf-accent) 18%, transparent)' : 'transparent') + ';">'
      + '<div style="font-size:12px;color:var(--pf-text);font-weight:600;">//' + escapeHtml(s.name) + '</div>'
      + (s.description ? '<div style="font-size:11px;color:var(--pf-muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">' + escapeHtml(s.description) + '</div>' : '')
      + '</div>';
  }).join('');
  box.querySelectorAll('.skill-ac-item').forEach(item => {
    item.addEventListener('mouseenter', () => {
      _skillAutocomplete.index = parseInt(item.dataset.index || '0', 10) || 0;
      _renderSkillAutocomplete(input, _skillAutocomplete.query);
    });
    item.addEventListener('mousedown', e => {
      e.preventDefault();
      _applySkillAutocomplete(input);
    });
  });
  _skillAutocomplete.open = true;
}

function _applySkillAutocomplete(input) {
  const token = _skillAutocompleteToken(input);
  const item = _skillAutocomplete.filtered[_skillAutocomplete.index];
  if (!token || !item) return false;
  input.value = '//' + item.name + ' ' + input.value.slice(token.end);
  const pos = item.name.length + 3;
  input.setSelectionRange(pos, pos);
  _hideSkillAutocomplete();
  return true;
}

async function _openSkillAutocomplete(input) {
  const token = _skillAutocompleteToken(input);
  if (!token) return false;
  await _loadSkillAutocompleteItems();
  _renderSkillAutocomplete(input, token.query);
  return true;
}

function handleKey(e) {
  const input = e.target;
  if (_skillAutocomplete.open) {
    if (e.key === 'Escape') { e.preventDefault(); _hideSkillAutocomplete(); return; }
    if (e.key === 'Enter') {
      if (_applySkillAutocomplete(input)) { e.preventDefault(); return; }
    }
    if (e.key === 'ArrowDown' || e.key === 'Tab') {
      e.preventDefault();
      const n = _skillAutocomplete.filtered.length || 1;
      _skillAutocomplete.index = (_skillAutocomplete.index + 1) % n;
      _renderSkillAutocomplete(input, _skillAutocomplete.query);
      return;
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      const n = _skillAutocomplete.filtered.length || 1;
      _skillAutocomplete.index = (_skillAutocomplete.index + n - 1) % n;
      _renderSkillAutocomplete(input, _skillAutocomplete.query);
      return;
    }
  }
  if (e.key === 'Tab' && _skillAutocompleteToken(input)) {
    e.preventDefault();
    _openSkillAutocomplete(input);
    return;
  }
  // Escape: 1st = graceful interrupt, 2nd (within 5s) = force stop
  if (e.key === 'Escape') {
    e.preventDefault();
    if (!selectedAgent) {
      console.error('BUG: selectedAgent is empty — this should never happen');
      addMsg('error', t('bugNoAgentSelected'));
      return;
    }
    const target = selectedAgent;
    const now = Date.now();
    const isRepeat = _lastEscapeTarget === target && (now - _lastEscapeTime) < 5000;
    _lastEscapeTarget = target;
    _lastEscapeTime = now;
    if (isRepeat) {
      addMsg('system', t('forceStopping', { agent: target }));
      fireAction('cancel', { agent_name: target, force: true });
      _lastEscapeTarget = '';  // reset so next Escape is graceful again
    } else {
      addMsg('system', t('interruptEscape', { agent: target }));
      fireAction('interrupt', { agent_name: target });
    }
    return;
  }
  if (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey) {
    e.preventDefault();
    _hideSkillAutocomplete();
    send();
    return;
  }
  // Arrow up: navigate message history (only when cursor is at position 0)
  if (e.key === 'ArrowUp' && input.selectionStart === 0 && messageHistory.length > 0) {
    e.preventDefault();
    if (historyIndex === -1) savedDraft = input.value;
    if (historyIndex < messageHistory.length - 1) {
      historyIndex++;
      input.value = messageHistory[historyIndex];
      input.setSelectionRange(0, 0);
    }
    return;
  }
  // Arrow down: navigate back toward current draft (only when cursor is at the end)
  if (e.key === 'ArrowDown' && historyIndex >= 0 && input.selectionStart === input.value.length) {
    e.preventDefault();
    historyIndex--;
    if (historyIndex < 0) {
      input.value = savedDraft;
    } else {
      input.value = messageHistory[historyIndex];
    }
    input.setSelectionRange(input.value.length, input.value.length);
    return;
  }
  setTimeout(() => {
    const token = _skillAutocompleteToken(input);
    if (_skillAutocomplete.open && token) _renderSkillAutocomplete(input, token.query);
    else if (_skillAutocomplete.open) _hideSkillAutocomplete();
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 120) + 'px';
  }, 0);
}

// ── Resources (agents, skills, mcp) ─────────────────────────────
function cmdResourceAction(action, extra) {
  const payload = { ...extra };
  // Carry conversation scope so conversation-scoped skills/agents resolve
  // (assign/unassign/run) and listings include them.
  if (payload.conversation_id === undefined
      && typeof conversationId !== 'undefined' && conversationId) {
    payload.conversation_id = conversationId;
  }
  return rxjs.firstValueFrom(action$(action, payload)).then(data => {
    // The user has the final word: a blocked skill review comes back as
    // requires_confirmation — show the findings and offer a forced rerun.
    if (data && data.requires_confirmation) {
      if (typeof _showSkillReviewConfirm === 'function') {
        _showSkillReviewConfirm(data.review, data.message, function() {
          cmdResourceAction(action, Object.assign({}, extra, { force: true }));
        });
      } else {
        addMsg('error', data.message || 'Skill review requires confirmation.');
      }
      return data;
    }
    if (data.error) { addMsg('error', data.error); return data; }
    if (data.created) addMsg('system', `Created: ${extra.name || ''}`);
    else if (data.deleted) addMsg('system', `Deleted: ${extra.name || ''}`);
    else if (data.activated) addMsg('system', `Activated ${data.type} "${data.name}" in this conversation`);
    else if (data.deactivated) addMsg('system', `Deactivated ${data.type} "${data.name}"`);
    else if (data.shared) addMsg('system', `Shared ${data.type} "${data.name}" to conversation ${data.target.substring(0,8)}...`);
    else if (data.message) addMsg('system', data.message);
    else addMsg('system', JSON.stringify(data, null, 2));
    return data;
  });
}

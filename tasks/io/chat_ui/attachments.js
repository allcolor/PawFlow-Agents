// ── Attachments ──────────────────────────────────────────────────
function removeFile(idx) {
  pendingFiles.splice(idx, 1);
  renderAttachments();
}

function toggleAttachmentPreview() {
  const preview = document.getElementById('attachPreview');
  if (!preview) return;
  preview.classList.toggle('expanded');
  renderAttachments();
}

function renderAttachments() {
  const preview = document.getElementById('attachPreview');
  preview.innerHTML = '';
  if (pendingFiles.length <= 3) preview.classList.remove('expanded');
  const expanded = preview.classList.contains('expanded');
  const visibleFiles = expanded ? pendingFiles : pendingFiles.slice(0, 3);
  visibleFiles.forEach((f, i) => {
    const el = document.createElement('div');
    el.className = 'att-item';
    el.title = f.filename;
    if (f.uploading) el.style.opacity = '0.5';
    const isImage = f.mime_type.startsWith('image/');
    if (isImage && f.dataUrl) {
      el.innerHTML = '<img src="' + f.dataUrl + '" alt="' + escapeHtml(f.filename)
        + '" onclick="window.open(this.src, \'_blank\', \'noopener\')">';
    } else {
      const icons = {'application/pdf': '\u{1F4C4}', 'text/plain': '\u{1F4DD}', 'text/html': '\u{1F310}', 'text/markdown': '\u{1F4DD}'};
      el.innerHTML = '<span class="att-icon">' + (icons[f.mime_type] || '\u{1F4CE}') + '</span>';
    }
    if (f.uploading) {
      const progress = Number.isFinite(f.progress) ? f.progress : 0;
      el.innerHTML += '<span class="att-upload-progress">' + progress + '%</span>';
    }
    el.innerHTML += '<span class="att-name">' + escapeHtml(f.filename) + (f.uploading ? ' ⏳' : '') + '</span>'
      + '<button class="att-remove" type="button" aria-label="Remove ' + escapeHtml(f.filename)
      + '" onclick="removeFile(' + i + ')">\u00d7</button>';
    preview.appendChild(el);
  });
  if (pendingFiles.length > 3) {
    const overflow = document.createElement('button');
    overflow.type = 'button';
    overflow.className = 'attachment-overflow-count';
    overflow.textContent = expanded ? '\u2212' : '+' + (pendingFiles.length - 3);
    overflow.setAttribute(
      'aria-label',
      expanded ? 'Collapse attachment previews' : 'Show all attachment previews'
    );
    overflow.onclick = toggleAttachmentPreview;
    preview.appendChild(overflow);
  }
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

// Upload the native File as the raw request body. XHR streams the Blob and
// reports network progress; no FileReader, multipart copy, or whole-file base64
// representation is created in browser memory.
function _uploadRawFile(file, params, onProgress) {
  return new Promise((resolve, reject) => {
    const query = new URLSearchParams(params || {});
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/upload?' + query.toString());
    xhr.withCredentials = true;
    xhr.setRequestHeader('Content-Type', file.type || 'application/octet-stream');
    const auth = getAuthHeaders()['Authorization'] || '';
    if (auth) xhr.setRequestHeader('Authorization', auth);
    xhr.upload.onprogress = event => {
      if (event.lengthComputable && typeof onProgress === 'function') {
        onProgress(Math.min(100, Math.round(event.loaded * 100 / event.total)));
      }
    };
    xhr.onerror = () => reject(new Error(t('uploadFailed')));
    xhr.onabort = () => reject(new Error(t('uploadFailed')));
    xhr.onload = () => {
      let data = {};
      try { data = JSON.parse(xhr.responseText || '{}'); }
      catch (_err) { reject(new Error('HTTP ' + xhr.status)); return; }
      if (xhr.status < 200 || xhr.status >= 300 || !data.ok
          || !data.files || !data.files.length) {
        reject(new Error(data.error || 'HTTP ' + xhr.status));
        return;
      }
      resolve(data.files[0]);
    };
    xhr.send(file);
  });
}

async function uploadFileToStore(file, onProgress) {
  const params = { filename: file.name };
  if (typeof currentConvId !== 'undefined' && currentConvId) params.conversation_id = currentConvId;
  const ttlSelect = document.getElementById('ttlSelect');
  const ttlVal = ttlSelect ? parseInt(ttlSelect.value, 10) : 0;
  if (ttlVal > 0) params.ttl = String(ttlVal);
  return _uploadRawFile(file, params, onProgress);
}

function uploadFileToRelay(file, service, path, onProgress) {
  return _uploadRawFile(file, { service, path }, onProgress);
}

function handleFiles(fileList) {
  for (const file of fileList) {
    // .py files → offer to install as dynamic tool
    if (file.name.endsWith('.py')) {
      const textReader = new FileReader();
      textReader.onload = (e) => {
        const source = e.target.result;
        addMsg('system', t('installingToolFrom', { file: file.name }));
        action$('install_tool', { filename: file.name, source }).subscribe(data => {
          if (data.error) { addMsg('error', t('installFailed', { error: data.error })); }
          else { addMsg('system', t('toolInstalled', { tool: data.tool_name, description: data.description })); }
        });
      };
      textReader.readAsText(file);
      continue;
    }
    // Stream the attachment without a whole-file browser representation.
    const mime = file.type || 'application/octet-stream';
    const isImage = mime.startsWith('image/');
    const placeholder = { filename: file.name, mime_type: mime, uploading: true, progress: 0 };
    if (isImage) placeholder.dataUrl = URL.createObjectURL(file);
    pendingFiles.push(placeholder);
    renderAttachments();
    uploadFileToStore(file, percent => {
      placeholder.progress = percent;
      renderAttachments();
    }).then(info => {
      const entry = pendingFiles.find(f => f === placeholder);
      if (entry) {
        entry.file_id = info.file_id;
        entry.url = info.url;
        entry.size = info.size;
        entry.uploading = false;
        if (isImage && !entry.dataUrl) entry.dataUrl = info.url;
        renderAttachments();
      }
    }).catch(err => {
      addMsg('error', t('uploadFailedFor', { file: file.name, error: err.message }));
      const i = pendingFiles.indexOf(placeholder);
      if (i >= 0) { pendingFiles.splice(i, 1); renderAttachments(); }
    });
  }
  // Reset file input so same file can be re-selected
  document.getElementById('fileInput').value = '';
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
  for (const sel of ['.msg-actions', '.source-badge', '.msg-time', '.msg-meta', '.code-block-copy']) {
    clone.querySelectorAll(sel).forEach(el => el.remove());
  }
  let text = (clone.textContent || clone.innerText).trim();
  // Strip target badge prefix like "[→ assistant] " or "[btw → agent] "
  return text.replace(/^\[(btw\s*)?\u2192\s*[^\]]+\]\s*/, '');
}

function copyCodeBlock(btn, event) {
  if (event) event.stopPropagation();
  const block = btn && btn.closest ? btn.closest('.code-block') : null;
  const code = block ? block.querySelector('pre code') : null;
  if (!code) return;
  navigator.clipboard.writeText(code.textContent || '').then(() => {
    btn.textContent = '\u2705';
    setTimeout(() => { btn.textContent = '\u{1F4CB}'; }, 1500);
  });
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
  document.getElementById('status').textContent = t('cancelling');
  const params = { force: true };
  if (target && target !== 'ALL') params.agent_name = target;
  fireAction('cancel', params);
  // SSE "cancelled" event will handle the rest
}

async function send() {
  const input = document.getElementById('input');
  // Grabbed, the composer is an input to the agent's tmux, not to /api/agent.
  // Nothing else in this function applies: no slash commands, no attachments,
  // and no local echo (the UserPromptSubmit hook files the message).
  if (typeof grabActive === 'function' && grabActive()) { grabSend(); return; }
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
    if (handled) { input.value = ''; input.style.height = ''; input.focus(); return; }
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
  if (typeof _ensureSSEBeforeUserAction === 'function') await _ensureSSEBeforeUserAction();
  sending = true;
  document.getElementById('status').textContent = t('sending');
  input.value = '';
  // Empty composer goes back to its stylesheet size — clearing the
  // inline height beats 'auto', which mobile keyboards can re-measure
  // against a stale scrollHeight and leave the box tall after a send.
  input.style.height = '';

  // Generate msg_id client-side so dedup works across SSE + replay
  const userMsgId = (crypto.randomUUID ? crypto.randomUUID() : ([1e7]+-1e3+-4e3+-8e3+-1e11).replace(/[018]/g, c => (c ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> c / 4).toString(16))).replace(/-/g, '').slice(0, 12);

  // Show user message with target badge (all messages explicitly show who they go to)
  const targetAgent = selectedAgent || '';
  const userSource = { type: 'user', name: '', target_agent: targetAgent };
  const msgEl = addMsg('user', text || '', { source: userSource, msg_id: userMsgId });
  if (typeof turnViewRegisterUser === 'function') {
    turnViewRegisterUser({ source: userSource, msg_id: userMsgId, turn_id: userMsgId }, msgEl);
  }
  if (attachmentsForDisplay.length > 0) {
    msgEl.innerHTML = sourceBadge(userSource) + escapeHtml(text || '') + renderUserAttachments(attachmentsForDisplay);
  }
  // Openspace mirrors the composer directly: the sender's own message
  // never comes back on the SSE stream.
  if (typeof openspaceUserMessage === 'function') {
    openspaceUserMessage(text || '', attachmentsForDisplay, targetAgent, userMsgId);
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

/** Insert a newline at the caret, as Shift+Enter does natively. */
function _composerInsertNewline(input) {
  const start = input.selectionStart;
  const end = input.selectionEnd;
  input.value = input.value.slice(0, start) + '\n' + input.value.slice(end);
  input.setSelectionRange(start + 1, start + 1);
  input.style.height = 'auto';
  input.style.height = Math.min(input.scrollHeight, 120) + 'px';
}

function handleKey(e) {
  const input = e.target;
  // Grab gets first refusal: there Shift+Enter is translated to the TUI's
  // Ctrl+Enter. When Grab is inactive, modified Enter stays a local newline.
  if (typeof grabHandleKey === 'function' && grabHandleKey(e)) return;
  if (e.key === 'Enter' && (e.shiftKey || e.ctrlKey || e.metaKey || e.altKey)) {
    e.preventDefault();
    _composerInsertNewline(input);
    return;
  }
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
  if (e.key === 'Enter') {
    e.preventDefault();
    _hideSkillAutocomplete();
    if (composerEnterCreatesNewline()) {
      _composerInsertNewline(input);
      return;
    }
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
  // Android IMEs compose text (keyCode 229 / isComposing): forcing a
  // height reflow on every keystroke mid-composition makes the composed
  // text blink and can drop the IME buffer — the "prompt erases itself"
  // mobile bug. Resize only outside composition.
  if (e.isComposing || e.keyCode === 229) return;
  setTimeout(() => {
    const token = _skillAutocompleteToken(input);
    if (_skillAutocomplete.open && token) _renderSkillAutocomplete(input, token.query);
    else if (_skillAutocomplete.open) _hideSkillAutocomplete();
    if (!input.value) { input.style.height = ''; return; }
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
        addMsg('error', data.message || t('skillReviewRequiresConfirmation'));
      }
      return data;
    }
    if (data.error) { addMsg('error', data.error); return data; }
    if (data.created) addMsg('system', t('resourceCreated', { type: data.type || '', name: extra.name || data.name || '' }));
    else if (data.deleted) addMsg('system', t('resourceDeleted', { type: data.type || '', name: extra.name || data.name || '' }));
    else if (data.activated) addMsg('system', t('resourceActivated', { type: data.type || '', name: data.name || '' }));
    else if (data.deactivated) addMsg('system', t('resourceDeactivated', { type: data.type || '', name: data.name || '' }));
    else if (data.shared) addMsg('system', `Shared ${data.type} "${data.name}" to conversation ${data.target.substring(0,8)}...`);
    else if (data.message) addMsg('system', data.message);
    else addMsg('system', JSON.stringify(data, null, 2));
    return data;
  });
}

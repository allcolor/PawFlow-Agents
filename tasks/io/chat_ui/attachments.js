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
    const isImage = f.mime_type.startsWith('image/');
    if (isImage) {
      el.innerHTML = '<img src="' + f.dataUrl + '" alt="' + escapeHtml(f.filename) + '">';
    } else {
      const icons = {'application/pdf': '\u{1F4C4}', 'text/plain': '\u{1F4DD}', 'text/html': '\u{1F310}', 'text/markdown': '\u{1F4DD}'};
      el.innerHTML = '<span class="att-icon">' + (icons[f.mime_type] || '\u{1F4CE}') + '</span>';
    }
    el.innerHTML += '<span>' + escapeHtml(f.filename) + '</span>'
      + '<button class="att-remove" onclick="removeFile(' + i + ')">\u00d7</button>';
    preview.appendChild(el);
  });
}

function renderUserAttachments(attachments) {
  // Render attachment badges in user message
  let html = '';
  for (const att of attachments) {
    if (att.mime_type && att.mime_type.startsWith('image/')) {
      html += '<img class="chat-image" src="data:' + att.mime_type + ';base64,' + att.data + '">';
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
  // Get text content only (strip badges, time, actions, meta)
  const clone = msg.cloneNode(true);
  for (const sel of ['.msg-actions', '.source-badge', '.msg-time', '.msg-meta']) {
    const el = clone.querySelector(sel);
    if (el) el.remove();
  }
  let text = (clone.textContent || clone.innerText).trim();
  // Strip target badge prefix like "[→ assistant] " or "[btw → agent] "
  text = text.replace(/^\[(btw\s*)?\u2192\s*[^\]]+\]\s*/, '');
  navigator.clipboard.writeText(text).then(() => {
    btn.textContent = '\u2705';
    setTimeout(() => { btn.textContent = '\u{1F4CB}'; }, 1500);
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
      reloadConv();
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
      reloadConv();
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
  const text = input.value.trim();
  if (!text && pendingFiles.length === 0) return;


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
  const attachments = pendingFiles.map(f => ({
    filename: f.filename, mime_type: f.mime_type, data: f.data,
  }));
  const attachmentsForDisplay = [...pendingFiles];
  pendingFiles = [];
  renderAttachments();

  // Allow stacking: don't block on 'sending', just track pending count
  sending = true;
  lastSSEActivity = Date.now();
  document.getElementById('status').textContent = t('sending');
  input.value = '';
  input.style.height = 'auto';

  // Generate msg_id client-side so dedup works across SSE + poll recovery
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
      addMsg('error', 'Error ' + resp.status + ': ' + errText);
      sending = false;
      document.getElementById('status').textContent = t('error');
      return;
    }

    const data = await resp.json();
    const cid = data.conversation_id || conversationId;
    if (cid && cid !== conversationId) {
      conversationId = cid;
      // Sync message count from server to prevent poll from re-fetching the user message
      serverMsgCount = data.message_count || 1;
      connectSSE(cid);  // Start/reconnect SSE for this conversation
      startPollTimer();
      updateDeleteBtn();
      loadConversations();  // Show new conversation in sidebar immediately
    }

    // If streaming mode: events come via SSE, don't show response here
    if (data.status === 'accepted') {
      if (data.message_count) serverMsgCount = data.message_count;
      document.getElementById('status').textContent = t('thinking');
      document.getElementById('stopBtn').style.display = '';
      // SSE will handle the rest
      return;
    }

    // Message queued — agent is busy, message will be picked up at next checkpoint
    if (data.status === 'queued') {
      if (data.message_count) serverMsgCount = data.message_count;
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

function handleKey(e) {
  const input = e.target;
  // Escape: 1st = graceful interrupt, 2nd (within 5s) = force stop
  if (e.key === 'Escape') {
    e.preventDefault();
    let target = selectedAgent;
    if (!target) {
      const activeNames = Object.values(activeInteractions || {}).map(a => a.name || a.apiName);
      target = activeNames.length === 1 ? activeNames[0] : '';
    }
    if (!target) return;
    const now = Date.now();
    const isRepeat = _lastEscapeTarget === target && (now - _lastEscapeTime) < 5000;
    _lastEscapeTarget = target;
    _lastEscapeTime = now;
    if (isRepeat) {
      addMsg('system', 'Force stopping ' + target + '...');
      fireAction('cancel', { agent_name: target, force: true });
      _lastEscapeTarget = '';  // reset so next Escape is graceful again
    } else {
      addMsg('system', 'Interrupting ' + target + '... (press Escape again to force stop)');
      fireAction('interrupt', { agent_name: target });
    }
    return;
  }
  if (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey) {
    e.preventDefault();
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
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 120) + 'px';
  }, 0);
}

// ── Resources (agents, skills, mcp) ─────────────────────────────
function cmdResourceAction(action, extra) {
  action$(action, { ...extra }).subscribe(data => {
    if (data.error) { addMsg('error', data.error); return; }
    if (data.created) addMsg('system', `Created: ${extra.name || ''}`);
    else if (data.deleted) addMsg('system', `Deleted: ${extra.name || ''}`);
    else if (data.activated) addMsg('system', `Activated ${data.type} "${data.name}" in this conversation`);
    else if (data.deactivated) addMsg('system', `Deactivated ${data.type} "${data.name}"`);
    else if (data.shared) addMsg('system', `Shared ${data.type} "${data.name}" to conversation ${data.target.substring(0,8)}...`);
    else if (data.message) addMsg('system', data.message);
    else addMsg('system', JSON.stringify(data, null, 2));
  });
}

          const resp = await fetch(API, {
            method: 'POST', headers: getAuthHeaders(),
            body: JSON.stringify({ action: 'install_tool', filename: file.name, source }),
          });
          const data = await resp.json();
          if (data.error) { addMsg('error', 'Install failed: ' + data.error); }
          else { addMsg('system', `Tool **${data.tool_name}** installed: ${data.description}`); }
        } catch (err) { addMsg('error', 'Install failed: ' + err.message); }
      };
      textReader.readAsText(file);
      continue;
    }
    const reader = new FileReader();
    reader.onload = (e) => {
      const dataUrl = e.target.result;
      const base64 = dataUrl.split(',')[1];
      const entry = {
        file: file,
        filename: file.name,
        mime_type: file.type || 'application/octet-stream',
        data: base64,
        dataUrl: dataUrl,
      };
      pendingFiles.push(entry);
      renderAttachments();
    };
    reader.readAsDataURL(file);
  }
  // Reset file input so same file can be re-selected
  document.getElementById('fileInput').value = '';
}

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

async function deleteMsg(btn) {
  const msg = btn.closest('.msg');
  if (!msg || !conversationId) return;
  const rawIdx = msg.dataset.rawIndex;
  if (rawIdx === undefined) {
    // No raw_index — message was added live (not from history), just remove from DOM
    msg.remove();
    return;
  }
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({
        action: 'delete_message', conversation_id: conversationId,
        index: parseInt(rawIdx),
      }),
      credentials: 'same-origin',
    });
    const data = await resp.json();
    if (data.deleted) {
      msg.style.transition = 'opacity 0.3s';
      msg.style.opacity = '0';
      setTimeout(() => msg.remove(), 300);
      if (data.message_count !== undefined) serverMsgCount = data.message_count;
    }
  } catch (e) {
    console.error('Delete message failed:', e);
  }
}

async function cancelAgent(target) {
  if (!conversationId) return;
  document.getElementById('stopBtn').style.display = 'none';
  document.getElementById('status').textContent = t('cancelling');
  const body = { action: 'cancel', conversation_id: conversationId };
  if (target && target !== 'ALL') body.agent_name = target;
  try {
    await fetch(API, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify(body),
      credentials: 'same-origin',
    });
  } catch (e) {
    console.warn('Cancel request failed:', e);
  }
  // SSE "cancelled" event will handle the rest
}

async function send() {
  const input = document.getElementById('input');
  const text = input.value.trim();
  if (!text && pendingFiles.length === 0) return;

  // Block sends while context operation is in progress
  if (contextOpInProgress) {
    addMsg('system', t('contextOpBusy'));
    return;
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

  // Show user message with target badge (all messages explicitly show who they go to)
  const targetAgent = selectedAgent || '';
  const userSource = { type: 'user', name: '', target_agent: targetAgent };
  const msgEl = addMsg('user', text || '', { source: userSource });
  if (attachmentsForDisplay.length > 0) {
    msgEl.innerHTML = sourceBadge(userSource) + escapeHtml(text || '') + renderUserAttachments(attachmentsForDisplay);
  }
  scrollBottom(true);  // Force scroll when user sends
  clearStream(targetAgent);
  showTyping();

  try {
    const body = { message: text, target_agent: targetAgent };
    if (conversationId) body.conversation_id = conversationId;
    if (attachments.length > 0) body.attachments = attachments;
    if (pendingAgent) { body.pending_agent = pendingAgent; pendingAgent = null; }
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
      hideTyping();
      if (LOGIN_URL) { window.location.href = LOGIN_URL; return; }
      addMsg('error', t('sessionExpired'));
      sending = false;
      document.getElementById('status').textContent = t('ready');
      return;
    }

    if (!resp.ok) {
      hideTyping();
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
    hideTyping();
    conversationId = data.conversation_id || conversationId;
    const nsExtra = data.source ? { source: data.source } : undefined;
    addMsg('assistant', data.response || data.content || JSON.stringify(data), nsExtra);
    sending = false;
    document.getElementById('status').textContent = t('ready');
    loadConversations();
    loadResources();

  } catch (e) {
    hideTyping();
    console.error('send() failed:', e);
    addMsg('error', t('connError', {msg: e.message + ' (check console)'}));
    sending = false;
    document.getElementById('status').textContent = t('error');
  }
}

function handleKey(e) {
  const input = e.target;
  if (e.key === 'Enter' && !e.shiftKey) {
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
async function cmdResourceAction(action, extra) {
  try {
    const payload = { action, conversation_id: conversationId, ...extra };
    const resp = await fetch(API, {
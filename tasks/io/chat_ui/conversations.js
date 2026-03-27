
// Conversation sidebar
async function loadConversations() {
  try {
    const resp = await fetch(API, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'list_conversations' }),
      credentials: 'same-origin',
    });
    if (resp.status === 401 || resp.status === 403) return;
    if (!resp.ok) return;
    const data = await resp.json();
    renderConvList(data.conversations || []);
  } catch (e) { /* silent */ }
}

function renderConvList(convs) {
  const list = document.getElementById('convList');
  list.innerHTML = '';
  if (convs.length === 0) {
    list.innerHTML = '<div style="padding:20px;text-align:center;color:#6c6c8a;font-size:13px;">No conversations yet</div>';
    return;
  }
  for (const c of convs) {
    const el = document.createElement('div');
    el.className = 'conv-item' + (c.conversation_id === conversationId ? ' active' : '');
    el.dataset.cid = c.conversation_id;
    const preview = c.preview || 'Empty conversation';
    const date = new Date(c.updated_at * 1000);
    const timeStr = date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
    const statusDot = c.status === 'active' ? '<span class="conv-status active" title="Working"></span>'
      : c.status === 'blocked' ? '<span class="conv-status blocked" title="Blocked"></span>' : '';
    el.innerHTML = '<div class="conv-preview">' + statusDot + escapeHtml(preview) + '</div>'
      + '<div class="conv-meta">' + c.message_count + ' messages \u00b7 ' + timeStr + '</div>'
      + '<button class="conv-delete" title="Delete" onclick="deleteConv(event,\'' + c.conversation_id + '\')">\u00d7</button>';
    el.onclick = () => resumeConv(c.conversation_id);
    list.appendChild(el);
  }
}

function escapeHtml(s) {
  const d = document.createElement('div'); d.textContent = s; return d.innerHTML;
}

function highlightConv(cid) {
  document.querySelectorAll('.conv-item').forEach(el => {
    el.classList.toggle('active', el.dataset.cid === cid);
  });
}

async function resumeConv(cid) {
  if (cid === conversationId) return;  // already viewing this one
  document.getElementById('status').textContent = t('loading');
  try {
    const resp = await fetch(API, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'load_history', conversation_id: cid, limit: displayWindow, offset: 0 }),
      credentials: 'same-origin',
    });
    if (!resp.ok) {
      addMsg('error', t('loadError'));
      return;
    }
    const data = await resp.json();
    if (data.error) {
      addMsg('error', data.error);
      return;
    }
    // Switch to this conversation (previous agent thread keeps running server-side)
    if (eventSource) { eventSource.close(); eventSource = null; }
    conversationId = cid;
    clearAllStreams();
    sending = false;
    document.getElementById('sendBtn').disabled = false;
    _expectingClear = true;
    document.getElementById('messages').innerHTML = '';
    _expectingClear = false;
    _seenMsgIds.clear();
    nicknameMap = data.nicknames || {};
    for (const m of (data.messages || [])) {
      let content = m.content || '';
      if ((m.type === 'assistant' || m.role === 'assistant') && typeof content === 'string') {
        content = content.replace(/^\[[^\]]+\]:\s*/, '');
      }
      addMsg(m.type || m.role, content, m);
    }
    serverMsgCount = data.message_count || 0;
    currentOffset = 0;
    hasMoreMessages = data.has_more || false;
    _updateLoadMoreBanner();
    selectedAgent = data.active_agent || '';
    // Apply per-conversation custom CSS theme
    let themeEl = document.getElementById('custom-theme');
    if (data.custom_css) {
      if (!themeEl) {
        themeEl = document.createElement('style');
        themeEl.id = 'custom-theme';
        document.head.appendChild(themeEl);
      }
      themeEl.textContent = data.custom_css;
    } else if (themeEl) {
      themeEl.textContent = '';
    }
    updateActiveAgentBadge();
    highlightConv(cid);
    connectSSE(cid);  // subscribe to SSE — will pick up events if agent is still running
    startPollTimer();
    updateDeleteBtn();
    loadResources();
    document.getElementById('status').textContent = t('ready');
    document.getElementById('sidebar').classList.remove('open');
    scrollBottom(true);
    document.getElementById('input').focus();
  } catch (e) {
    addMsg('error', t('connError', {msg: e.message}));
    document.getElementById('status').textContent = t('error');
  }
}

function _updateLoadMoreBanner() {
  let banner = document.getElementById('loadMoreBanner');
  if (hasMoreMessages) {
    if (!banner) {
      banner = document.createElement('div');
      banner.id = 'loadMoreBanner';
      banner.className = 'load-more-banner';
      banner.onclick = loadMoreMessages;
      const container = document.getElementById('messages');
      container.insertBefore(banner, container.firstChild);
    }
    const shown = document.querySelectorAll('#messages > .msg').length;
    const total = serverMsgCount || '?';
    banner.innerHTML = '&#x25B2; Load more messages (showing ' + shown + ' of ' + total + ')';
  } else if (banner) {
    banner.remove();
  }
}

async function loadMoreMessages() {
  if (loadingMore || !conversationId || !hasMoreMessages) return;
  loadingMore = true;
  const container = document.getElementById('messages');
  const banner = document.getElementById('loadMoreBanner');
  if (banner) banner.innerHTML = 'Loading...';

  // Save scroll state
  const prevHeight = container.scrollHeight;

  // Calculate next offset: current loaded messages count
  const loadedCount = document.querySelectorAll('#messages > .msg').length;
  const nextOffset = loadedCount;

  try {
    const resp = await fetch(API, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify({
        action: 'load_history',
        conversation_id: conversationId,
        limit: displayWindow,
        offset: nextOffset,
      }),
      credentials: 'same-origin',
    });
    const data = await resp.json();
    if (data.error) { loadingMore = false; return; }

    hasMoreMessages = data.has_more || false;

    // Prepend older messages before existing ones (after banner)
    const insertPoint = banner ? banner.nextSibling : container.firstChild;
    const beforeCount = container.children.length;
    for (const m of (data.messages || [])) {
      let content = m.content || '';
      if ((m.type === 'assistant' || m.role === 'assistant') && typeof content === 'string') {
        content = content.replace(/^\[[^\]]+\]:\s*/, '');
      }
      addMsg(m.type || m.role, content, m);
    }
    // Move newly added elements (appended at end) to before insertPoint
    const newElements = [];
    while (container.children.length > beforeCount) {
      newElements.push(container.lastChild);
      container.removeChild(container.lastChild);
    }
    // Insert in correct order (they were collected in reverse)
    for (let i = newElements.length - 1; i >= 0; i--) {
      container.insertBefore(newElements[i], insertPoint);
    }

    // Preserve scroll position
    container.scrollTop = container.scrollHeight - prevHeight;

    _updateLoadMoreBanner();
  } catch (e) {
    console.error('Load more failed:', e);
  }
  loadingMore = false;
}

async function _recoverConversation(cid) {
  // After SSE reconnect or poll, check for new messages via efficient poll action.
  try {
    if (cid !== conversationId) return;  // conversation changed during recovery
    const resp = await fetch(API, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify({
        action: 'poll',
        conversation_id: cid,
        last_count: serverMsgCount,
      }),
      credentials: 'same-origin',
    });
    if (!resp.ok) return;
    const data = await resp.json();
    const newMsgs = data.new_messages || [];
    if (newMsgs.length === 0) return;

    console.log('[poll] recovering', newMsgs.length, 'new messages');
    serverMsgCount = data.message_count || serverMsgCount;

    // Do NOT clearAllStreams — agents may be actively streaming.
    // Only clear streams that have no active chunks being appended.

    // Display the new messages, skipping messages already shown locally
    // Skip ALL assistant messages while an agent is actively streaming
    // (SSE handles display, poll would create duplicates)
    const _anyAgentActive = Object.keys(activeInteractions || {}).length > 0;
    const msgContainer = document.getElementById('messages');
    for (const m of newMsgs) {
      const mType = m.type || m.role;
      // Skip display_only messages (tool_call, tool_result, thinking) —
      // these are rendered by SSE in real-time, transcript has them for reload only.
      // Also skip assistant messages during active streaming.
      if (mType === 'tool_call' || mType === 'tool_result' || mType === 'thinking') {
        continue;
      }
      if (_anyAgentActive && mType === 'assistant') {
        continue;
      }
      if (mType === 'user') {
        // Check if this user message is already displayed (sent locally by send())
        const existing = msgContainer.querySelectorAll('.msg.user');
        const lastUserEl = existing.length > 0 ? existing[existing.length - 1] : null;
        if (lastUserEl) {
          const stripPrefix = (s) => s.replace(/^\[(?:btw\s*)?(?:\u2192\s+\w+)?\]\s*/, '');
          const stripDeflated = (s) => s
            .replace(/\n?\[\d+ image\(s\) were shown[^\]]*\]/g, '')
            .replace(/\n?\[\d+ image\(s\) — saved to FileStore:[\s\S]*?Use show_file to view again\]/g, '')
            .replace(/\n?\[images deflated\]/g, '')
            .trim();
          const localRaw = stripPrefix(stripDeflated(lastUserEl.dataset.rawText || lastUserEl.textContent.trim()));
          const serverRaw = stripPrefix(stripDeflated((m.content || '').trim()));
          if (localRaw === serverRaw || localRaw.startsWith(serverRaw) || serverRaw.startsWith(localRaw)) {
            console.log('[poll] skipping duplicate user message (dedup match)');
            continue;
          }
        }
      }
      if (mType === 'assistant') {
        // Skip btw messages that were already shown via btw_done SSE event
        if (m.source && m.source.btw) {
          console.log('[poll] skipping btw message (already shown via btw_done)');
          continue;
        }
        // Check if this assistant message was already shown via SSE done event
        const existing = msgContainer.querySelectorAll('.msg.assistant, .msg.subagent');
        const lastEl = existing.length > 0 ? existing[existing.length - 1] : null;
        if (lastEl && lastEl.dataset.rawText) {
          const newText = (m.content || '').replace(/^\[[^\]]+\]:\s*/, '').substring(0, 500);
          if (lastEl.dataset.rawText === newText) {
            console.log('[poll] skipping duplicate assistant message');
            continue;
          }
        }
      }
      let pollContent = m.content || '';
      // Strip identity prefixes (same as history replay)
      if (mType === 'assistant' && typeof pollContent === 'string') {
        pollContent = pollContent.replace(/^\[[^\]]+\]:\s*/, '');
      }
      addMsg(mType, pollContent, m);
    }

    // Check if agent is still working
    const last = newMsgs[newMsgs.length - 1];
    const lastType = last ? (last.type || last.role) : '';
    if (lastType === 'user' || lastType === 'tool_call' || lastType === 'tool_result') {
      showTyping();
      document.getElementById('status').textContent = t('thinking');
    } else {
      sending = false;
      document.getElementById('sendBtn').disabled = false;
      document.getElementById('status').textContent = t('ready');
    }
    scrollBottom();
  } catch (e) {
    console.warn('[poll] recovery failed:', e);
  }
}

async function deleteConv(event, cid) {
  event.stopPropagation();
  try {
    const resp = await fetch(API, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'delete_conversation', conversation_id: cid }),
      credentials: 'same-origin',
    });
    if (!resp.ok) { console.error('Delete failed:', resp.status); return; }
    if (cid === conversationId) newChat();
    loadConversations();
  } catch (e) { console.error('Delete error:', e); }
}

async function deleteCurrentConv() {
  if (!conversationId) return;
  if (!confirm(t('confirmDelete'))) return;
  const cid = conversationId;
  try {
    const resp = await fetch(API, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'delete_conversation', conversation_id: cid }),
      credentials: 'same-origin',
    });
    if (!resp.ok) { console.error('Delete failed:', resp.status); return; }
    newChat();
    loadConversations();
  } catch (e) { console.error('Delete error:', e); }
}

async function exportConversation() {
  if (!conversationId) return;
  document.getElementById('status').textContent = t('exporting');
  try {
    // Fetch conversation messages
    const resp = await fetch(API, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'load_history', conversation_id: conversationId }),
      credentials: 'same-origin',
    });
    if (!resp.ok) { addMsg('error', 'Export failed'); return; }
    const data = await resp.json();
    const messages = data.messages || [];

    // Collect file URLs from messages
    const fileUrls = [];
    const fileUrlRe = /(https?:\/\/[^\s<"']*\/files\/[a-f0-9]+\/([^\s<"')]+))/g;
    for (const m of messages) {
      const content = m.content || '';
      let match;
      while ((match = fileUrlRe.exec(content)) !== null) {
        fileUrls.push({ url: match[1], name: match[2] });
      }
      fileUrlRe.lastIndex = 0;
    }

    // Check if we have images — if so, create a ZIP
    const hasImages = fileUrls.some(f => isImageFile(f.name));

    // Build HTML
    const htmlContent = buildExportHtml(messages, data.nicknames || {}, fileUrls);

    if (hasImages) {
      // Use JSZip-like approach: simple ZIP with stored entries
      addMsg('system', t('exportingWithImages'));
      const files = [{ name: 'conversation.html', content: new TextEncoder().encode(htmlContent) }];
      // Fetch images
      const token = getToken();
      const headers = {};
      if (token) headers['Authorization'] = 'Bearer ' + token;
      for (const f of fileUrls) {
        if (isImageFile(f.name)) {
          try {
            const imgResp = await fetch(f.url, { headers, credentials: 'same-origin' });
            if (imgResp.ok) {
              const blob = await imgResp.blob();
              const buf = await blob.arrayBuffer();
              files.push({ name: 'images/' + f.name, content: new Uint8Array(buf) });
            }
          } catch(e) { console.warn('Failed to fetch image for export:', f.name); }
        }
      }
      // Build a simple ZIP (store method, no compression)
      const zipBlob = buildSimpleZip(files);
      const a = document.createElement('a');
      a.href = URL.createObjectURL(zipBlob);
      a.download = 'conversation_' + conversationId.substring(0, 8) + '.zip';
      a.click();
      URL.revokeObjectURL(a.href);
    } else {
      // Plain HTML download
      const blob = new Blob([htmlContent], { type: 'text/html;charset=utf-8' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'conversation_' + conversationId.substring(0, 8) + '.html';
      a.click();
      URL.revokeObjectURL(a.href);
    }
    addMsg('system', t('exported'));
    document.getElementById('status').textContent = t('ready');
  } catch (e) {
    console.error('Export error:', e);
    addMsg('error', 'Export failed: ' + e.message);
    document.getElementById('status').textContent = t('ready');
  }
}

function buildExportHtml(messages, nicknames, fileUrls) {
  const nicks = nicknames || {};
  function nickLookup(name) {
    const lk = (name || '').toLowerCase();
    for (const k of Object.keys(nicks)) { if (k.toLowerCase() === lk) return nicks[k]; }
    return name || '';
  }
  let body = '';
  for (const m of messages) {
    const type = m.type || m.role;
    if (type === 'system') continue;
    let cssClass = type;
    let content = m.content || '';
    let badge = '';
    if (type === 'assistant' || type === 'user') {
      const src = m.source || {};
      const srcName = nickLookup(src.name);
      if (srcName) {
        const h = [...srcName].reduce((a, c) => ((a << 5) - a + c.charCodeAt(0)) | 0, 0);
        const hue = Math.abs(h) % 360;
        badge = '<span style="display:inline-block;font-size:10px;padding:1px 6px;border-radius:8px;margin-right:4px;font-weight:600;background:hsl(' + hue + ',60%,25%);color:hsl(' + hue + ',80%,80%)">' + escapeHtml(srcName) + '</span>';
      }
      if (type === 'assistant' && src.type === 'agent' && src.name) {
        cssClass = 'subagent';
      }
      // Strip identity prefix
      content = content.replace(/^\[[^\]]+\]:\s*/, '');
    }
    if (type === 'tool_call' || type === 'tool_result') cssClass = 'tool';
    // Convert markdown-like formatting
    let html = escapeHtml(content);
    html = html.replace(/```(\w*)\n([\s\S]*?)```/g, function(_, lang, code) {
      var cls = lang ? ' class="language-' + lang + '"' : '';
      return '<pre><code' + cls + '>' + code + '</code></pre>';
    });
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');
    // Replace file URLs with image tags or links
    for (const f of fileUrls) {
      if (isImageFile(f.name)) {
        html = html.split(escapeHtml(f.url)).join('<br><img src="images/' + f.name + '" style="max-width:512px;max-height:512px;border-radius:8px;"><br>');
      }
    }
    body += '<div class="msg ' + cssClass + '">' + badge + html + '</div>\n';
  }
  return '<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">'
    + '<title>PawFlow Conversation Export</title>'
    + '<style>'
    + 'body { font-family: -apple-system, sans-serif; background: #1a1a2e; color: #e0e0e0; padding: 20px; max-width: 900px; margin: 0 auto; }'
    + '.msg { padding: 10px 14px; border-radius: 12px; margin-bottom: 12px; line-height: 1.5; font-size: 14px; white-space: pre-wrap; word-wrap: break-word; }'
    + '.msg a { color: #4fc3f7; }'
    + '.msg code { background: rgba(0,0,0,0.3); padding: 1px 5px; border-radius: 3px; }'
    + '.msg pre { background: rgba(0,0,0,0.4); padding: 10px; border-radius: 6px; overflow-x: auto; }'
    + '.msg.user { background: #0f3460; color: white; margin-left: 20%; border-left: 3px solid #4ecdc4; }'
    + '.msg.assistant { background: #16213e; border: 1px solid #0f3460; margin-right: 20%; border-left: 3px solid #e94560; }'
    + '.msg.subagent { background: #0d1b2a; border: 1px solid #1a3a5c; margin-right: 20%; border-left: 3px solid #6c5ce7; }'
    + '.msg.tool { background: #0f1629; color: #808090; font-size: 12px; border-left: 2px solid #0f3460; margin-right: 30%; }'
    + '.msg.btw { background: #0d1b2a; font-size: 13px; border-left: 3px solid #60a5fa; margin-right: 20%; font-style: italic; }'
    + 'img { display: block; margin: 8px 0; }'
    + '</style></head><body>'
    + '<h1 style="color:#e94560;margin-bottom:20px;">PawFlow Conversation Export</h1>'
    + '<p style="color:#6c6c8a;margin-bottom:20px;">Exported: ' + new Date().toLocaleString() + '</p>'
    + body
    + '</body></html>';
}

function buildSimpleZip(files) {
  // Minimal ZIP builder (Store method, no compression)
  const parts = [];
  const directory = [];
  let offset = 0;
  for (const f of files) {
    const nameBytes = new TextEncoder().encode(f.name);
    const data = f.content;
    // Local file header (30 bytes + name)
    const header = new Uint8Array(30 + nameBytes.length);
    const hv = new DataView(header.buffer);
    hv.setUint32(0, 0x04034b50, true); // signature
    hv.setUint16(4, 20, true);  // version needed
    hv.setUint16(6, 0, true);   // flags
    hv.setUint16(8, 0, true);   // compression (store)
    hv.setUint16(10, 0, true);  // mod time
    hv.setUint16(12, 0, true);  // mod date
    // CRC-32
    const crc = crc32(data);
    hv.setUint32(14, crc, true);
    hv.setUint32(18, data.length, true);  // compressed size
    hv.setUint32(22, data.length, true);  // uncompressed size
    hv.setUint16(26, nameBytes.length, true);
    hv.setUint16(28, 0, true);  // extra field length
    header.set(nameBytes, 30);
    parts.push(header);
    parts.push(data);
    // Central directory entry
    const cdEntry = new Uint8Array(46 + nameBytes.length);
    const cv = new DataView(cdEntry.buffer);
    cv.setUint32(0, 0x02014b50, true);
    cv.setUint16(4, 20, true);
    cv.setUint16(6, 20, true);
    cv.setUint16(8, 0, true);
    cv.setUint16(10, 0, true);
    cv.setUint16(12, 0, true);
    cv.setUint16(14, 0, true);
    cv.setUint32(16, crc, true);
    cv.setUint32(20, data.length, true);
    cv.setUint32(24, data.length, true);
    cv.setUint16(28, nameBytes.length, true);
    cv.setUint16(30, 0, true);
    cv.setUint16(32, 0, true);
    cv.setUint16(34, 0, true);
    cv.setUint16(36, 0, true);
    cv.setUint32(38, 0, true);
    cv.setUint32(42, offset, true);
    cdEntry.set(nameBytes, 46);
    directory.push(cdEntry);
    offset += header.length + data.length;
  }
  // Central directory
  const cdOffset = offset;
  let cdSize = 0;
  for (const d of directory) { parts.push(d); cdSize += d.length; }
  // End of central directory (22 bytes)
  const eocd = new Uint8Array(22);
  const ev = new DataView(eocd.buffer);
  ev.setUint32(0, 0x06054b50, true);
  ev.setUint16(4, 0, true);
  ev.setUint16(6, 0, true);
  ev.setUint16(8, files.length, true);
  ev.setUint16(10, files.length, true);
  ev.setUint32(12, cdSize, true);
  ev.setUint32(16, cdOffset, true);
  ev.setUint16(20, 0, true);
  parts.push(eocd);
  return new Blob(parts, { type: 'application/zip' });
}

function crc32(data) {
  let crc = 0xFFFFFFFF;
  for (let i = 0; i < data.length; i++) {
    crc ^= data[i];
    for (let j = 0; j < 8; j++) {
      crc = (crc >>> 1) ^ (crc & 1 ? 0xEDB88320 : 0);
    }
  }
  return (crc ^ 0xFFFFFFFF) >>> 0;
}

async function refreshCurrentConv() {
  if (!conversationId) return;
  const cid = conversationId;
  document.getElementById('status').textContent = t('loading');
  try {
    const resp = await fetch(API, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'load_history', conversation_id: cid }),
      credentials: 'same-origin',
    });
    if (!resp.ok) { document.getElementById('status').textContent = t('error'); return; }
    const data = await resp.json();
    if (data.error) { document.getElementById('status').textContent = t('error'); return; }
    // Clear and replay
    _expectingClear = true;
    document.getElementById('messages').innerHTML = '';
    _expectingClear = false;
    _seenMsgIds.clear();
    clearAllStreams();
    for (const m of (data.messages || [])) {
      let content = m.content || '';
      if ((m.type === 'assistant' || m.role === 'assistant') && typeof content === 'string') {
        content = content.replace(/^\[[^\]]+\]:\s*/, '');
      }
      addMsg(m.type || m.role, content, m);
    }
    serverMsgCount = data.message_count || 0;
    scrollBottom();
    // Check if agent is still working (last msg is not assistant → still processing)
    const msgs = data.messages || [];
    const lastRole = msgs.length > 0 ? (msgs[msgs.length - 1].type || msgs[msgs.length - 1].role) : '';
    if (lastRole !== 'assistant' && lastRole !== 'user') {
      sending = true;
      showTyping();
      document.getElementById('status').textContent = t('thinking');
    } else {
      sending = false;
      document.getElementById('sendBtn').disabled = false;
      document.getElementById('status').textContent = t('ready');
    }
    loadConversations();
  } catch (e) {
    document.getElementById('status').textContent = t('error');
  }
}

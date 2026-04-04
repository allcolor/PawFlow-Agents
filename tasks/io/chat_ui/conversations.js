
// ── Conversation sidebar & history ──────────────────────────────
// All server calls use action$() from rxbus.js (fire-and-forget + SSE result).

function loadConversations() {
  // list_conversations has no conversation_id — server runs sync for these
  action$('list_conversations', {}).subscribe(data => {
    const convs = data.conversations || [];
    renderConvList(convs);
  });
}

function renderConvList(convs) {
  const list = document.getElementById('convList');
  list.innerHTML = '';
  if (convs.length === 0) {
    list.innerHTML = '<div style="padding:20px;text-align:center;color:#6c6c8a;font-size:13px;">No conversations yet.<br>Click <b>+ Nouveau</b> to start.</div>';
    if (!conversationId) _setInputEnabled(false);
  }
  for (const c of convs) {
    const el = document.createElement('div');
    el.className = 'conv-item' + (c.conversation_id === conversationId ? ' active' : '');
    el.dataset.cid = c.conversation_id;
    const title = c.title || c.preview || 'New conversation';
    const date = new Date(c.updated_at * 1000);
    const timeStr = date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
    const statusDot = c.status === 'active' ? '<span class="conv-status active" title="Working"></span>'
      : c.status === 'blocked' ? '<span class="conv-status blocked" title="Blocked"></span>' : '';
    el.innerHTML = '<div class="conv-preview" ondblclick="renameConvInline(event,\'' + c.conversation_id + '\')">'
      + statusDot + escapeHtml(title) + '</div>'
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

function renameConvInline(e, cid) {
  e.stopPropagation();
  const previewEl = e.target.closest('.conv-preview');
  if (!previewEl) return;
  const currentTitle = previewEl.textContent.trim();
  const input = document.createElement('input');
  input.type = 'text';
  input.value = currentTitle;
  input.style.cssText = 'width:100%;background:#1a1a2e;color:#eee;border:1px solid #6c5ce7;border-radius:3px;padding:2px 4px;font-size:12px;';
  previewEl.innerHTML = '';
  previewEl.appendChild(input);
  input.focus();
  input.select();
  const finish = () => {
    const newTitle = input.value.trim();
    if (newTitle && newTitle !== currentTitle) {
      fireAction('set_conv_title', { conversation_id: cid, title: newTitle });
    }
    loadConversations();
  };
  input.onblur = finish;
  input.onkeydown = (ev) => { if (ev.key === 'Enter') finish(); if (ev.key === 'Escape') loadConversations(); };
}

function reloadConv() {
  if (!conversationId) return;
  // Don't close/reopen SSE — just reload the messages
  _expectingClear = true;
  document.getElementById('messages').innerHTML = '';
  _expectingClear = false;
  _seenMsgIds.clear();
  serverMsgCount = 0;
  action$('load_history', { conversation_id: conversationId, limit: displayWindow, offset: 0 })
    .subscribe(data => _renderHistory(data));
}

function resumeConv(cid, force) {
  if (cid === conversationId && !force) return;
  document.getElementById('status').textContent = t('loading');
  // Prepare UI
  if (eventSource) { eventSource.close(); eventSource = null; }
  conversationId = cid;
  _setInputEnabled(true);
  clearAllStreams();
  sending = false;
  _expectingClear = true;
  document.getElementById('messages').innerHTML = '';
  _expectingClear = false;
  _seenMsgIds.clear();
  highlightConv(cid);
  // Reset SSE state so the new connection doesn't trigger false recovery
  sseEverConnected = false;
  sseHadError = false;
  stopPollTimer();
  // Connect SSE
  connectSSE(cid);
  updateDeleteBtn();
  document.getElementById('sidebar').classList.add('collapsed');
  _syncToggleBtn();
  // Load history AFTER SSE is connected (result comes via SSE command_result)
  function _loadWhenReady() {
    if (eventSource && eventSource.readyState === EventSource.OPEN) {
      action$('load_history', { conversation_id: cid, limit: displayWindow, offset: 0 })
        .subscribe(data => {
          _renderHistory(data);
          startPollTimer();
        });
    } else {
      setTimeout(_loadWhenReady, 100);
    }
  }
  _loadWhenReady();
}

// Shared across render + loadMore so task blocks persist
let _histTaskBlocks = {};

function _getHistTaskBlock(taskId, agentName) {
  if (_histTaskBlocks[taskId]) return _histTaskBlocks[taskId];
  const details = document.createElement('details');
  details.className = 'msg task-block';
  details.style.cssText = 'margin:6px 0;border:1px solid #333;border-radius:8px;padding:0;background:#1a1a2e;';
  const summary = document.createElement('summary');
  summary.style.cssText = 'cursor:pointer;padding:8px 12px;font-size:12px;color:#6c5ce7;user-select:none;font-weight:600;display:flex;align-items:center;gap:6px;';
  summary.innerHTML = '\u{1F4CB} Task <span style="color:#e0e0e0;font-weight:normal">' + escapeHtml(taskId) + '</span>'
    + (agentName ? ' <span style="color:#888;font-weight:normal">(' + escapeHtml(displayAgentName(agentName)) + ')</span>' : '')
    + ' <span style="margin-left:auto;font-size:11px;color:#888">\u2714 done</span>';
  details.appendChild(summary);
  const content = document.createElement('div');
  content.style.cssText = 'padding:4px 12px 8px;max-height:60vh;overflow-y:auto;';
  details.appendChild(content);
  document.getElementById('messages').appendChild(details);
  _histTaskBlocks[taskId] = {el: details, content: content};
  return _histTaskBlocks[taskId];
}

function _renderHistory(data) {
  if (!data || data.error) {
    addMsg('error', (data && data.error) || t('loadError'));
    document.getElementById('status').textContent = t('error');
    return;
  }
  _histTaskBlocks = {};  // reset on full render
  nicknameMap = data.nicknames || {};
  for (const m of (data.messages || [])) {
    let content = m.content || '';
    if ((m.type === 'assistant' || m.role === 'assistant') && typeof content === 'string') {
      content = content.replace(/^\[[^\]]+\]:\s*/, '');
    }
    const el = addMsg(m.type || m.role, content, m);
    // task_id can be top-level (SSE) or in source (stored messages)
    // Use task_iteration to create separate blocks per iteration
    const _taskId = m.task_id || (m.source && m.source.task_id) || '';
    if (_taskId && el) {
      const agentName = (m.source && m.source.name) || '';
      const _iter = (m.source && m.source.task_iteration) || 1;
      const _blockKey = _taskId + '::iter' + _iter;
      const tb = _getHistTaskBlock(_blockKey, agentName);
      tb.content.appendChild(el);
    }
  }
  serverMsgCount = data.message_count || 0;
  currentOffset = data.raw_count || (data.messages || []).length;
  hasMoreMessages = data.has_more || false;
  _updateLoadMoreBanner();
  selectedAgent = data.active_agent || '';
  // Custom CSS theme
  let themeEl = document.getElementById('custom-theme');
  if (data.custom_css) {
    if (!themeEl) { themeEl = document.createElement('style'); themeEl.id = 'custom-theme'; document.head.appendChild(themeEl); }
    themeEl.textContent = data.custom_css;
  } else if (themeEl) { themeEl.textContent = ''; }
  updateActiveAgentBadge();
  loadResources();
  loadPermissionMode();
  document.getElementById('status').textContent = t('ready');
  scrollBottom(true);
  document.getElementById('input').focus();
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

function loadMoreMessages() {
  if (loadingMore || !conversationId || !hasMoreMessages) return;
  loadingMore = true;
  const container = document.getElementById('messages');
  const banner = document.getElementById('loadMoreBanner');
  if (banner) banner.innerHTML = 'Loading...';
  const prevHeight = container.scrollHeight;
  const nextOffset = currentOffset;

  action$('load_history', { conversation_id: conversationId, limit: displayWindow, offset: nextOffset })
    .subscribe(data => {
      if (data.error) { loadingMore = false; return; }
      hasMoreMessages = data.has_more || false;
      currentOffset += data.raw_count || (data.messages || []).length;
      const insertPoint = banner ? banner.nextSibling : container.firstChild;
      // Build elements in a fragment, then insert at the right position.
      // Task messages go into their task block (existing or new).
      // Non-task messages go into the fragment for insertion.
      const frag = document.createDocumentFragment();
      // Build elements first, then insert — prepending one-by-one reverses order
      const _taskEls = {};  // taskId → [elements in order]
      const _fragEls = [];
      for (const m of (data.messages || [])) {
        let content = m.content || '';
        if ((m.type === 'assistant' || m.role === 'assistant') && typeof content === 'string') {
          content = content.replace(/^\[[^\]]+\]:\s*/, '');
        }
        const el = addMsg(m.type || m.role, content, m);
        const _taskId = m.task_id || (m.source && m.source.task_id) || '';
        if (!el) continue;
        if (el.parentNode) el.parentNode.removeChild(el);
        if (_taskId) {
          const _iter = (m.source && m.source.task_iteration) || 1;
          const _blockKey = _taskId + '::iter' + _iter;
          if (!_taskEls[_blockKey]) _taskEls[_blockKey] = [];
          _taskEls[_blockKey].push({el, agentName: (m.source && m.source.name) || ''});
        } else {
          _fragEls.push(el);
        }
      }
      // Prepend task elements in correct order (as a batch)
      for (const [tid, entries] of Object.entries(_taskEls)) {
        let tb = _histTaskBlocks[tid];
        if (!tb) {
          tb = _getHistTaskBlock(tid, entries[0].agentName);
          if (tb.el.parentNode) tb.el.parentNode.removeChild(tb.el);
          frag.appendChild(tb.el);
        }
        // Insert all entries before the current first child (in order)
        const anchor = tb.content.firstChild;
        for (const entry of entries) {
          tb.content.insertBefore(entry.el, anchor);
        }
      }
      // Insert non-task elements
      for (const el of _fragEls) {
        frag.appendChild(el);
      }
      container.insertBefore(frag, insertPoint);
      container.scrollTop = container.scrollHeight - prevHeight;
      _updateLoadMoreBanner();
      loadingMore = false;
    });
}

function _recoverConversation(cid) {
  if (cid !== conversationId) return;
  action$('poll', { conversation_id: cid, last_count: serverMsgCount })
    .subscribe(data => {
      if (data.error) return;
      const newMsgs = data.new_messages || [];
      if (newMsgs.length === 0) return;
      console.log('[poll] recovering', newMsgs.length, 'new messages');
      if (newMsgs.length > 50) {
        // Too many missed — just update count, don't try to render them all
        serverMsgCount = data.message_count || serverMsgCount;
        return;
      }
      serverMsgCount = data.message_count || serverMsgCount;
      const msgContainer = document.getElementById('messages');
      let rendered = 0;
      for (const m of newMsgs) {
        const mType = m.type || m.role;
        if (mType === 'tool_call' || mType === 'tool_result' || mType === 'thinking') continue;
        // Use msg_id dedup — if already rendered via SSE, skip
        if (m.msg_id && _seenMsgIds.has(m.msg_id)) continue;
        if (mType === 'user') {
          const existing = msgContainer.querySelectorAll('.msg.user');
          const lastUserEl = existing.length > 0 ? existing[existing.length - 1] : null;
          if (lastUserEl) {
            const stripPrefix = (s) => s.replace(/^\[(?:btw\s*)?(?:\u2192\s+\w+)?\]\s*/, '');
            const stripDeflated = (s) => s
              .replace(/\n?\[\d+ image\(s\) were shown[^\]]*\]/g, '')
              .replace(/\n?\[\d+ image\(s\) — saved to FileStore:[\s\S]*?Use show_file to view again\]/g, '')
              .replace(/\n?\[images deflated\]/g, '').trim();
            const localRaw = stripPrefix(stripDeflated(lastUserEl.dataset.rawText || lastUserEl.textContent.trim()));
            const serverRaw = stripPrefix(stripDeflated((m.content || '').trim()));
            if (localRaw === serverRaw || localRaw.startsWith(serverRaw) || serverRaw.startsWith(localRaw)) continue;
          }
        }
        if (mType === 'assistant') {
          if (m.source && m.source.btw) continue;
          // Content-based dedup: check if the last displayed assistant message matches
          const existing = msgContainer.querySelectorAll('.msg.assistant, .msg.subagent');
          const lastEl = existing.length > 0 ? existing[existing.length - 1] : null;
          if (lastEl && lastEl.dataset.rawText) {
            const newText = (m.content || '').replace(/^\[[^\]]+\]:\s*/, '').substring(0, 500);
            if (lastEl.dataset.rawText === newText) continue;
          }
        }
        let pollContent = m.content || '';
        if (mType === 'assistant' && typeof pollContent === 'string') {
          pollContent = pollContent.replace(/^\[[^\]]+\]:\s*/, '');
        }
        addMsg(mType, pollContent, m);
        rendered++;
      }
      if (rendered > 0) console.log('[poll] rendered', rendered, 'recovered messages');
      const last = newMsgs[newMsgs.length - 1];
      const lastType = last ? (last.type || last.role) : '';
      if (lastType === 'user' || lastType === 'tool_call' || lastType === 'tool_result') {
        document.getElementById('status').textContent = t('thinking');
      } else {
        sending = false;
        document.getElementById('sendBtn').disabled = false;
        document.getElementById('status').textContent = t('ready');
      }
      scrollBottom();
    });
}

function deleteConv(event, cid) {
  event.stopPropagation();
  fireAction('delete_conversation', { conversation_id: cid });
  if (cid === conversationId) newChat();
  loadConversations();
}

function deleteCurrentConv() {
  if (!conversationId) return;
  if (!confirm(t('confirmDelete'))) return;
  fireAction('delete_conversation', { conversation_id: conversationId });
  newChat();
  loadConversations();
}

function exportConversation() {
  if (!conversationId) return;
  document.getElementById('status').textContent = t('exporting');
  // Export needs the full history — subscribe to load_history result
  action$('load_history', { conversation_id: conversationId, limit: 99999, offset: 0 })
    .subscribe(async data => {
      try {
        const messages = data.messages || [];
        const fileUrls = [];
        const fileUrlRe = /(https?:\/\/[^\s<"']*\/files\/[a-f0-9]+\/([^\s<"')]+))/g;
        for (const m of messages) {
          const content = m.content || '';
          let match;
          while ((match = fileUrlRe.exec(content)) !== null) fileUrls.push({ url: match[1], name: match[2] });
          fileUrlRe.lastIndex = 0;
        }
        const hasImages = fileUrls.some(f => isImageFile(f.name));
        const htmlContent = buildExportHtml(messages, data.nicknames || {}, fileUrls);
        if (hasImages) {
          addMsg('system', t('exportingWithImages'));
          const files = [{ name: 'conversation.html', content: new TextEncoder().encode(htmlContent) }];
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
          const zipBlob = buildSimpleZip(files);
          const a = document.createElement('a');
          a.href = URL.createObjectURL(zipBlob);
          a.download = 'conversation_' + conversationId.substring(0, 8) + '.zip';
          a.click();
          URL.revokeObjectURL(a.href);
        } else {
          const blob = new Blob([htmlContent], { type: 'text/html;charset=utf-8' });
          const a = document.createElement('a');
          a.href = URL.createObjectURL(blob);
          a.download = 'conversation_' + conversationId.substring(0, 8) + '.html';
          a.click();
          URL.revokeObjectURL(a.href);
        }
        addMsg('system', t('exported'));
      } catch (e) {
        addMsg('error', 'Export failed: ' + e.message);
      }
      document.getElementById('status').textContent = t('ready');
    });
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
      if (type === 'assistant' && src.type === 'agent' && src.name) cssClass = 'subagent';
      content = content.replace(/^\[[^\]]+\]:\s*/, '');
    }
    if (type === 'tool_call' || type === 'tool_result') cssClass = 'tool';
    let html = escapeHtml(content);
    html = html.replace(/```(\w*)\n([\s\S]*?)```/g, function(_, lang, code) {
      var cls = lang ? ' class="language-' + lang + '"' : '';
      return '<pre><code' + cls + '>' + code + '</code></pre>';
    });
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');
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
    + body + '</body></html>';
}

function buildSimpleZip(files) {
  const parts = [];
  const directory = [];
  let offset = 0;
  for (const f of files) {
    const nameBytes = new TextEncoder().encode(f.name);
    const data = f.content;
    const header = new Uint8Array(30 + nameBytes.length);
    const hv = new DataView(header.buffer);
    hv.setUint32(0, 0x04034b50, true);
    hv.setUint16(4, 20, true);
    hv.setUint16(8, 0, true);
    const crc = crc32(data);
    hv.setUint32(14, crc, true);
    hv.setUint32(18, data.length, true);
    hv.setUint32(22, data.length, true);
    hv.setUint16(26, nameBytes.length, true);
    header.set(nameBytes, 30);
    parts.push(header);
    parts.push(data);
    const cdEntry = new Uint8Array(46 + nameBytes.length);
    const cv = new DataView(cdEntry.buffer);
    cv.setUint32(0, 0x02014b50, true);
    cv.setUint16(4, 20, true);
    cv.setUint16(6, 20, true);
    cv.setUint32(16, crc, true);
    cv.setUint32(20, data.length, true);
    cv.setUint32(24, data.length, true);
    cv.setUint16(28, nameBytes.length, true);
    cv.setUint32(42, offset, true);
    cdEntry.set(nameBytes, 46);
    directory.push(cdEntry);
    offset += header.length + data.length;
  }
  const cdOffset = offset;
  let cdSize = 0;
  for (const d of directory) { parts.push(d); cdSize += d.length; }
  const eocd = new Uint8Array(22);
  const ev = new DataView(eocd.buffer);
  ev.setUint32(0, 0x06054b50, true);
  ev.setUint16(8, files.length, true);
  ev.setUint16(10, files.length, true);
  ev.setUint32(12, cdSize, true);
  ev.setUint32(16, cdOffset, true);
  parts.push(eocd);
  return new Blob(parts, { type: 'application/zip' });
}

function crc32(data) {
  let crc = 0xFFFFFFFF;
  for (let i = 0; i < data.length; i++) {
    crc ^= data[i];
    for (let j = 0; j < 8; j++) crc = (crc >>> 1) ^ (crc & 1 ? 0xEDB88320 : 0);
  }
  return (crc ^ 0xFFFFFFFF) >>> 0;
}

function refreshCurrentConv() {
  if (!conversationId) return;
  document.getElementById('status').textContent = t('loading');
  action$('load_history', { conversation_id: conversationId, limit: displayWindow, offset: 0 })
    .subscribe(data => {
      if (data.error) { document.getElementById('status').textContent = t('error'); return; }
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
      hasMoreMessages = data.has_more || false;
      currentOffset = data.raw_count || (data.messages || []).length;
      _updateLoadMoreBanner();
      scrollBottom();
      const msgs = data.messages || [];
      const lastRole = msgs.length > 0 ? (msgs[msgs.length - 1].type || msgs[msgs.length - 1].role) : '';
      if (lastRole !== 'assistant' && lastRole !== 'user') {
        sending = true;
        document.getElementById('status').textContent = t('thinking');
      } else {
        sending = false;
        document.getElementById('sendBtn').disabled = false;
        document.getElementById('status').textContent = t('ready');
      }
      loadConversations();
    });
}

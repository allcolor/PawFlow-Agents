
// ── Conversation sidebar & history ──────────────────────────────
// All server calls use action$() from rxbus.js (fire-and-forget + SSE result).

function loadConversations() {
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
    const branchBadge = c.branch ? '<span class="conv-branch" title="Branch: ' + escapeHtml(c.branch) + '">\u{1F33F} ' + escapeHtml(c.branch) + '</span>' : '';
    el.innerHTML = '<div class="conv-preview" ondblclick="renameConvInline(event,\'' + c.conversation_id + '\')">' 
      + statusDot + '<span class="conv-title">' + escapeHtml(title) + '</span>' + branchBadge + '</div>'
      + '<div class="conv-meta">' + c.message_count + ' messages \u00b7 ' + timeStr + '</div>'
      + '<button class="conv-delete" title="Delete" onclick="deleteConv(event,\'' + c.conversation_id + '\')">\u00d7</button>';
    el.onclick = () => resumeConv(c.conversation_id);
    el.oncontextmenu = (function(cid, status) { return function(ev) { ev.preventDefault(); showConvMenu(ev, cid, status); }; })(c.conversation_id, c.status);
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
  // Race-free reload: fetch first, then clear + repopulate atomically.
  // The previous version cleared _seenMsgIds *before* the network round
  // trip, so any SSE event arriving in that window registered its
  // msg_id, and _renderHistory then deduped (skipped) those same
  // messages from the history payload — the visible transcript was
  // truncated to whatever happened to arrive between the two calls.
  action$('load_history', { conversation_id: conversationId, limit: displayWindow, offset: 0 })
    .subscribe(data => {
      _expectingClear = true;
      document.getElementById('messages').innerHTML = '';
      _expectingClear = false;
      _seenMsgIds.clear();
      serverMsgCount = 0;
      // Drop stale SSE-side DOM references (task/delegate blocks).
      if (typeof window._sseClearLiveBlocks === 'function') {
        window._sseClearLiveBlocks();
      }
      _renderHistory(data);
    });
}

function resumeConv(cid, force) {
  if (cid === conversationId && !force) return;
  document.getElementById('status').textContent = t('loading');
  // Switch SSE to the new conversation. DO NOT clear the DOM yet — we
  // keep the previous transcript visible until the new history arrives,
  // then clear + render atomically in the load_history callback. This
  // mirrors reloadConv() and avoids the race where SSE events landing
  // between innerHTML='' and _renderHistory pollute _seenMsgIds and
  // cause the history to be deduped out.
  if (eventSource) { eventSource.close(); eventSource = null; }
  conversationId = cid;
  _setInputEnabled(true);
  highlightConv(cid);
  updateDeleteBtn();
  document.getElementById('sidebar').classList.add('collapsed');
  _syncToggleBtn();
  // Reset SSE state so the new connection doesn't trigger false recovery
  sseEverConnected = false;
  sseHadError = false;
  stopPollTimer();
  connectSSE(cid);
  // Load history AFTER SSE is connected (result comes via SSE command_result)
  function _loadWhenReady() {
    // Bail out if the user has since switched to a different conv — we
    // don't want an in-flight load_history for the previous cid to land
    // inside the DOM of the now-active one.
    if (cid !== conversationId) return;
    if (eventSource && eventSource.readyState === EventSource.OPEN) {
      action$('load_history', { conversation_id: cid, limit: displayWindow, offset: 0 })
        .subscribe(data => {
          if (cid !== conversationId) return;
          // Atomic clear + reset + render — all conversation-scoped
          // state is wiped here, then the fresh history is drawn. No
          // SSE event can land between clear and render because this
          // is a single synchronous block.
          _expectingClear = true;
          document.getElementById('messages').innerHTML = '';
          _expectingClear = false;
          _seenMsgIds.clear();
          if (typeof _selectedMsgIds !== 'undefined' && _selectedMsgIds.clear) _selectedMsgIds.clear();
          serverMsgCount = 0;
          _histTaskBlocks = {};
          clearAllStreams();
          sending = false;
          if (typeof window._sseClearLiveBlocks === 'function') window._sseClearLiveBlocks();
          if (typeof activeInteractions !== 'undefined') {
            for (const k of Object.keys(activeInteractions)) delete activeInteractions[k];
            if (typeof updateActivePanel === 'function') updateActivePanel();
          }
          if (typeof hideTyping === 'function') hideTyping();
          if (typeof _pendingImages !== 'undefined') {
            _pendingImages.length = 0;
            if (typeof _imageFlushTimer !== 'undefined' && _imageFlushTimer) {
              clearTimeout(_imageFlushTimer); _imageFlushTimer = null;
            }
          }
          selectedAgent = '';
          if (typeof nicknameMap !== 'undefined') nicknameMap = {};
          if (typeof _autoScroll !== 'undefined') _autoScroll = true;
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
    // Use task_iteration to create separate blocks per iteration.
    // Delegate traces are their own top-level block — never wrap them
    // in a generic task-block (delegate is not a task).
    const _isDelegateTrace = (m.type === 'sub_agent_trace' || m.role === 'sub_agent_trace');
    const _taskId = _isDelegateTrace ? '' : (m.task_id || (m.source && m.source.task_id) || '');
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
  if (!data.active_agent) {
    console.error('BUG: server returned empty active_agent — conversation must always have an agent');
  }
  selectedAgent = data.active_agent || selectedAgent;
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
        const _isDelegateTrace = (m.type === 'sub_agent_trace' || m.role === 'sub_agent_trace');
        const _taskId = _isDelegateTrace ? '' : (m.task_id || (m.source && m.source.task_id) || '');
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
  if (!confirm(t('confirmDelete'))) return;
  var wasActive = (cid === conversationId);
  action$('delete_conversation', { conversation_id: cid }).subscribe(() => {
    if (wasActive) _switchAfterDelete(cid);
    else loadConversations();
  });
}

function deleteCurrentConv() {
  if (!conversationId) return;
  if (!confirm(t('confirmDelete'))) return;
  var cid = conversationId;
  action$('delete_conversation', { conversation_id: cid }).subscribe(() => {
    _switchAfterDelete(cid);
  });
}

function _switchAfterDelete(deletedCid) {
  // Find the conversation list and pick the next one
  var items = document.querySelectorAll('#convList .conv-item');
  var nextCid = null;
  var foundDeleted = false;
  for (var i = 0; i < items.length; i++) {
    if (items[i].dataset.cid === deletedCid) {
      foundDeleted = true;
      // Pick the previous conv, or the next one if this was the first
      if (i > 0) nextCid = items[i - 1].dataset.cid;
      else if (i + 1 < items.length) nextCid = items[i + 1].dataset.cid;
      break;
    }
  }
  if (nextCid) {
    resumeConv(nextCid);
  } else {
    // No other conv — clear chat and disable input
    _doNewChat();
    _setInputEnabled(false);
  }
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

function importConversation() {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = '.zip,.jsonl';
  input.onchange = async () => {
    const file = input.files[0];
    if (!file) return;
    const ext = file.name.split('.').pop().toLowerCase();
    const fmt = ext === 'zip' ? 'pawflow' : ext === 'jsonl' ? 'claude_code' : null;
    if (!fmt) { addMsg('error', 'Unsupported format. Use .zip (PawFlow) or .jsonl (Claude Code)'); return; }
    document.getElementById('status').textContent = 'Uploading...';
    try {
      const info = await uploadFileToStore(file);
      document.getElementById('status').textContent = 'Analyzing...';
      action$('conv_import_analyze', { file_id: info.file_id, format: fmt }).subscribe(result => {
        document.getElementById('status').textContent = t('ready');
        if (result.error) { addMsg('error', 'Import failed: ' + result.error); return; }
        _showImportConvDialog(result, fmt);
      });
    } catch(e) {
      document.getElementById('status').textContent = t('ready');
      addMsg('error', 'Upload failed: ' + e.message);
    }
  };
  input.click();
}

function _showImportConvDialog(info, fmt) {
  // info: {temp_id, agents: [{name, definition},...], message_count, format}
  Promise.all([
    rxjs.firstValueFrom(action$('list_repo_agents', {})),
    rxjs.firstValueFrom(listServices$('llmConnection')),
  ]).then(results => {
    var repoAgents = results[0].agents || [];
    var llmServices = (results[1].services || []).filter(s => s.enabled);
    var svcOpts = llmServices.map(s =>
      '<option value="' + escapeHtml(s.service_id) + '">' + escapeHtml(s.service_id) + (s.description ? ' \u2014 ' + escapeHtml(s.description) : '') + '</option>'
    ).join('');

    function _guessLlm(name) {
      for (var i = 0; i < llmServices.length; i++) {
        if (llmServices[i].service_id === name + '_llm_service') return name + '_llm_service';
      }
      for (var i = 0; i < llmServices.length; i++) {
        if (llmServices[i].service_id === name + '_llm') return name + '_llm';
      }
      return llmServices.length ? llmServices[0].service_id : '';
    }

    var overlay = document.createElement('div');
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.5);z-index:9999;display:flex;align-items:center;justify-content:center';
    var box = document.createElement('div');
    box.style.cssText = 'background:var(--bg2,#1e1e2e);border:1px solid var(--border,#444);border-radius:8px;padding:20px;min-width:640px;max-width:780px;max-height:85vh;display:flex;flex-direction:column;gap:12px;overflow-y:auto;position:relative;';
    box.onclick = e => e.stopPropagation();

    var _listCss = 'width:100%;min-height:100px;max-height:240px;overflow-y:auto;border:1px solid var(--border,#444);border-radius:4px;padding:4px;background:var(--bg,#141420);';

    box.innerHTML =
      '<span id="_impCloseX" style="position:absolute;top:8px;right:12px;cursor:pointer;color:#888;font-size:18px;" title="Cancel">\u2715</span>'
      + '<div style="font-weight:600;font-size:1.1em;">Import Conversation</div>'
      + '<div style="color:#888;font-size:11px;">' + info.message_count + ' messages \u2014 format: ' + escapeHtml(fmt) + '</div>'
      + '<div><label style="font-size:11px;color:#888;">Title</label>'
      + '<input id="_impTitle" type="text" value="Imported conversation" style="width:100%;padding:6px 10px;border-radius:5px;border:1px solid var(--border,#444);background:var(--bg,#141420);color:inherit;font-size:0.95em;box-sizing:border-box;"></div>'
      + '<div style="font-size:12px;font-weight:600;color:#6c5ce7;">Agent Mapping</div>'
      + '<div style="display:flex;gap:12px;align-items:stretch;">'
      +   '<div id="_impAgentTree" style="' + _listCss + 'flex:1;"></div>'
      +   '<div id="_impAgentDetail" style="flex:1;border:1px solid var(--border,#444);border-radius:4px;padding:10px;background:var(--bg,#141420);min-height:100px;max-height:240px;overflow-y:auto;font-size:12px;color:#aaa;display:flex;align-items:center;justify-content:center;">Select an agent to configure</div>'
      + '</div>'
      + '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:4px;">'
      +   '<button id="_impCancelBtn" style="padding:6px 14px;border-radius:5px;border:1px solid var(--border,#444);background:transparent;color:inherit;cursor:pointer;">Cancel</button>'
      +   '<button id="_impGoBtn" style="padding:6px 14px;border-radius:5px;border:none;background:var(--accent,#7c6af7);color:#fff;cursor:pointer;font-weight:600;">Import</button>'
      + '</div>';

    overlay.appendChild(box);
    document.body.appendChild(overlay);

    function _cancelImport() {
      overlay.remove();
      action$('conv_import_cleanup', { temp_id: info.temp_id }).subscribe(() => {});
    }

    var agentInstances = {};
    var focusedAgent = '';

    info.agents.forEach(a => {
      var bestDef = repoAgents.find(r => r.name === a.definition) ? a.definition
                  : repoAgents.find(r => r.name === a.name) ? a.name
                  : repoAgents.length ? repoAgents[0].name : '';
      agentInstances[a.name] = {
        definition: bestDef,
        llm_service: _guessLlm(bestDef || a.name),
        params: { name: a.name },
      };
    });

    function _renderTree() {
      var tree = document.getElementById('_impAgentTree');
      tree.innerHTML = '';
      var hdr = document.createElement('div');
      hdr.style.cssText = 'font-size:10px;color:#666;padding:2px 4px;';
      hdr.textContent = 'Imported agents (' + Object.keys(agentInstances).length + ')';
      tree.appendChild(hdr);
      Object.keys(agentInstances).forEach(iname => {
        var inst = agentInstances[iname];
        var row = document.createElement('div');
        row.style.cssText = 'display:flex;align-items:center;gap:6px;padding:4px 6px;border-radius:4px;cursor:pointer;font-size:12px;'
          + (focusedAgent === iname ? 'background:rgba(124,106,247,0.15);' : '');
        var label = document.createElement('span');
        label.style.cssText = 'flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;';
        label.textContent = iname;
        var badge = document.createElement('span');
        badge.style.cssText = 'font-size:10px;color:#888;';
        badge.textContent = inst.definition ? '\u2192 ' + inst.definition : '\u26A0 unmapped';
        row.appendChild(label);
        row.appendChild(badge);
        row.onclick = () => { focusedAgent = iname; _renderTree(); _renderDetail(); };
        tree.appendChild(row);
      });
    }

    function _renderDetail() {
      var panel = document.getElementById('_impAgentDetail');
      if (!focusedAgent || !agentInstances[focusedAgent]) {
        panel.innerHTML = '<span style="color:#666;">Select an agent to configure</span>';
        panel.style.display = 'flex'; panel.style.alignItems = 'center'; panel.style.justifyContent = 'center';
        return;
      }
      panel.style.display = 'block'; panel.style.alignItems = ''; panel.style.justifyContent = '';
      var inst = agentInstances[focusedAgent];
      var defAgent = repoAgents.find(a => a.name === inst.definition);
      var paramSchema = defAgent && defAgent.parameters ? defAgent.parameters : {};
      var paramKeys = Object.keys(paramSchema).filter(k => k !== 'name');

      var defOptions = repoAgents.map(a =>
        '<option value="' + escapeHtml(a.name) + '"' + (a.name === inst.definition ? ' selected' : '') + '>'
        + escapeHtml(a.name) + (a.description ? ' \u2014 ' + escapeHtml(a.description) : '') + '</option>'
      ).join('');

      var html = '<div style="font-weight:600;font-size:13px;color:#fff;margin-bottom:8px;">' + escapeHtml(focusedAgent) + '</div>';
      html += '<div style="margin-bottom:6px;"><label style="font-size:10px;color:#888;">Instance Name</label>';
      html += '<input id="_impInstName" value="' + escapeHtml(focusedAgent) + '" style="width:100%;padding:4px 6px;border-radius:4px;border:1px solid var(--border,#444);background:var(--bg2,#1e1e2e);color:inherit;font-size:12px;box-sizing:border-box;"/></div>';
      html += '<div style="margin-bottom:6px;"><label style="font-size:10px;color:#888;">Definition *</label>';
      html += '<select id="_impDefSelect" style="width:100%;padding:4px 6px;border-radius:4px;border:1px solid var(--border,#444);background:var(--bg2,#1e1e2e);color:inherit;font-size:12px;">' + defOptions + '</select></div>';
      html += '<div style="margin-bottom:6px;"><label style="font-size:10px;color:#888;">LLM Service *</label>';
      html += '<select id="_impLlmSelect" style="width:100%;padding:4px 6px;border-radius:4px;border:1px solid var(--border,#444);background:var(--bg2,#1e1e2e);color:inherit;font-size:12px;">' + svcOpts + '</select></div>';
      if (paramKeys.length) {
        html += '<div style="margin-bottom:6px;"><div style="font-size:10px;color:#888;margin-bottom:4px;">Parameters</div>';
        paramKeys.forEach(k => {
          var spec = paramSchema[k] || {};
          var val = inst.params[k] || spec.default || '';
          html += '<div style="margin-bottom:4px;"><label style="font-size:10px;color:#888;">' + escapeHtml(k + (spec.required ? ' *' : '')) + '</label>';
          html += '<input data-param="' + escapeHtml(k) + '" value="' + escapeHtml(String(val)) + '" style="width:100%;padding:4px 6px;border-radius:4px;border:1px solid var(--border,#444);background:var(--bg2,#1e1e2e);color:inherit;font-size:12px;box-sizing:border-box;"/></div>';
        });
        html += '</div>';
      }
      html += '<button id="_impApplyBtn" style="width:100%;padding:5px;border-radius:4px;border:1px solid #6c5ce7;background:transparent;color:#6c5ce7;cursor:pointer;font-size:11px;font-weight:600;">Apply Changes</button>';

      panel.innerHTML = html;
      var llmSel = document.getElementById('_impLlmSelect');
      if (llmSel) llmSel.value = inst.llm_service || _guessLlm(inst.definition);
      var defSel = document.getElementById('_impDefSelect');
      if (defSel) defSel.onchange = () => {
        inst.definition = defSel.value;
        inst.llm_service = _guessLlm(defSel.value);
        _renderDetail();
      };
      document.getElementById('_impApplyBtn').onclick = () => {
        var newName = (document.getElementById('_impInstName').value || '').trim();
        if (!newName) { alert('Instance name is required.'); return; }
        var newDef = defSel.value;
        var newLlm = llmSel.value;
        var params = { name: newName };
        panel.querySelectorAll('[data-param]').forEach(inp => { params[inp.dataset.param] = inp.value; });
        if (newName !== focusedAgent) delete agentInstances[focusedAgent];
        agentInstances[newName] = { definition: newDef, llm_service: newLlm, params: params };
        focusedAgent = newName;
        _renderTree(); _renderDetail();
      };
    }

    _renderTree();
    if (info.agents.length) { focusedAgent = info.agents[0].name; _renderTree(); _renderDetail(); }

    document.getElementById('_impCloseX').onclick = _cancelImport;
    document.getElementById('_impCancelBtn').onclick = _cancelImport;
    document.getElementById('_impGoBtn').onclick = () => {
      if (!Object.keys(agentInstances).length) { alert('At least one agent is required.'); return; }
      var title = (document.getElementById('_impTitle').value || '').trim() || 'Imported';
      overlay.remove();
      document.getElementById('status').textContent = 'Importing...';
      action$('conv_import_execute', {
        temp_id: info.temp_id, format: fmt,
        agent_mapping: agentInstances, title,
      }).subscribe(result => {
        if (result.error) { addMsg('error', 'Import failed: ' + result.error); }
        else {
          addMsg('system', 'Conversation imported successfully');
          loadConversations();
          resumeConv(result.conversation_id);
        }
        document.getElementById('status').textContent = t('ready');
      });
    };
  });
}

function showExportDialog() {
  if (!conversationId) return;
  const overlay = document.createElement('div');
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;z-index:9999;';
  const panel = document.createElement('div');
  panel.style.cssText = 'background:#16213e;border-radius:8px;padding:20px;width:340px;border:1px solid #333;';
  panel.innerHTML = '<h3 style="margin:0 0 16px;color:#e0e0e0;font-size:14px;">Export Conversation</h3>'
    + '<div style="display:flex;flex-direction:column;gap:8px;">'
    + '<button onclick="this.closest(\'div[style*=fixed]\').remove();exportConversation()" style="background:#0f3460;color:#e0e0e0;border:1px solid #333;padding:10px;border-radius:6px;cursor:pointer;text-align:left;"><b>HTML</b><br><span style=font-size:11px;color:#888>Standalone HTML file for viewing/sharing</span></button>'
    + '<button onclick="this.closest(\'div[style*=fixed]\').remove();exportPawflow()" style="background:#0f3460;color:#e0e0e0;border:1px solid #333;padding:10px;border-radius:6px;cursor:pointer;text-align:left;"><b>PawFlow (.pfconv.zip)</b><br><span style=font-size:11px;color:#888>Full conversation archive, re-importable</span></button>'
    + '<button onclick="this.closest(\'div[style*=fixed]\').remove();exportClaudeCode()" style="background:#0f3460;color:#e0e0e0;border:1px solid #333;padding:10px;border-radius:6px;cursor:pointer;text-align:left;"><b>Claude Code (.jsonl)</b><br><span style=font-size:11px;color:#888>Claude Code compatible format</span></button>'
    + '</div>'
    + '<div style="margin-top:12px;text-align:right;"><button onclick="this.closest(\'div[style*=fixed]\').remove()" style="background:#333;color:#ccc;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">Cancel</button></div>';
  overlay.appendChild(panel);
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
  document.body.appendChild(overlay);
}

function exportPawflow() {
  if (!conversationId) return;
  document.getElementById('status').textContent = 'Exporting...';
  action$('conv_export_pawflow', { conversation_id: conversationId }).subscribe(data => {
    if (data.error) { addMsg('error', 'Export failed: ' + data.error); }
    else { const a = document.createElement('a'); a.href = data.url; a.download = data.filename; a.click(); addMsg('system', 'Exported: ' + data.filename); }
    document.getElementById('status').textContent = t('ready');
  });
}

function exportClaudeCode() {
  if (!conversationId) return;
  document.getElementById('status').textContent = 'Exporting...';
  action$('conv_export_claude_code', { conversation_id: conversationId }).subscribe(data => {
    if (data.error) { addMsg('error', 'Export failed: ' + data.error); }
    else { const a = document.createElement('a'); a.href = data.url; a.download = data.filename; a.click(); addMsg('system', 'Exported: ' + data.filename); }
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
    if (Array.isArray(content)) content = content.filter(c => c.type === 'text').map(c => c.text).join('\n');
    if (typeof content !== 'string') content = JSON.stringify(content);
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
    if (data.error) { addMsg('system', '\u26a0 Fork failed: ' + data.error); return; }
    addMsg('system', 'Forked \u2192 ' + data.conversation_id.slice(0, 8));
    loadConversations();
    resumeConv(data.conversation_id);
  });
}

function convBranchPrompt(cid) {
  const name = prompt('Branch name:');
  if (!name || !name.trim()) return;
  action$('conv_branch', { conversation_id: cid, branch_name: name.trim() }).subscribe(data => {
    if (data.error) { addMsg('system', '\u26a0 ' + data.error); return; }
    addMsg('system', 'Branch created: ' + name.trim());
    loadConversations();
    if (cid === conversationId) reloadConv();
  });
}

function convTagPrompt(cid) {
  const name = prompt('Tag name:');
  if (!name || !name.trim()) return;
  action$('conv_tag', { conversation_id: cid, tag_name: name.trim() }).subscribe(data => {
    if (data.error) { addMsg('system', '\u26a0 ' + data.error); return; }
    addMsg('system', 'Tagged: ' + name.trim());
  });
}

function convSwitchBranchDialog(cid) {
  action$('conv_list_branches', { conversation_id: cid }).subscribe(data => {
    if (data.error) { addMsg('system', '\u26a0 ' + data.error); return; }
    const branches = data.branches || [];
    if (branches.length <= 1) { addMsg('system', 'No other branches.'); return; }
    _showGitDialog('Switch Branch', branches.map(b => {
      const current = b.current ? ' \u2190 current' : '';
      return { label: b.name + current, value: b.name, disabled: b.current };
    }), (selected) => {
      action$('conv_switch_branch', { conversation_id: cid, branch_name: selected }).subscribe(res => {
        if (res.error) { addMsg('system', '\u26a0 ' + res.error); return; }
        addMsg('system', 'Switched to branch: ' + selected);
        loadConversations();
        if (cid === conversationId) reloadConv();
      });
    });
  });
}

function convDeleteBranchDialog(cid) {
  action$('conv_list_branches', { conversation_id: cid }).subscribe(data => {
    if (data.error) { addMsg('system', '\u26a0 ' + data.error); return; }
    const branches = (data.branches || []).filter(b => !b.current);
    if (branches.length === 0) { addMsg('system', 'No branches to delete (only current branch exists).'); return; }
    _showGitDialog('Delete Branch', branches.map(b => {
      return { label: b.name, value: b.name };
    }), (selected) => {
      if (!confirm('Delete branch "' + selected + '"? This cannot be undone.')) return;
      action$('conv_delete_branch', { conversation_id: cid, branch_name: selected }).subscribe(res => {
        if (res.error) { addMsg('system', '\u26a0 ' + res.error); return; }
        addMsg('system', 'Branch deleted: ' + selected);
        loadConversations();
      });
    });
  });
}

function convCompareBranchesDialog(cid) {
  action$('conv_list_branches', { conversation_id: cid }).subscribe(data => {
    if (data.error) { addMsg('system', '\u26a0 ' + data.error); return; }
    const branches = data.branches || [];
    if (branches.length < 2) { addMsg('system', 'Need at least 2 branches to compare.'); return; }
    const current = data.current || branches[0].name;
    const other = branches.find(b => b.name !== current);
    const a = prompt('Branch A:', current);
    if (!a) return;
    const b = prompt('Branch B:', other ? other.name : '');
    if (!b) return;
    action$('conv_compare_branches', { conversation_id: cid, branch_a: a, branch_b: b }).subscribe(res => {
      if (res.error) { addMsg('system', '\u26a0 ' + res.error); return; }
      const lines = [
        '**Branch comparison: ' + a + ' vs ' + b + '**',
        'Commits ahead: ' + (res.commits_ahead || 0),
        'Commits behind: ' + (res.commits_behind || 0),
        'Messages in ' + a + ': ' + (res.messages_a || 0),
        'Messages in ' + b + ': ' + (res.messages_b || 0),
      ];
      addMsg('system', lines.join('\n'));
    });
  });
}

function convRollbackDialog(cid) {
  action$('conv_git_log', { conversation_id: cid, limit: 30 }).subscribe(data => {
    if (data.error) { addMsg('system', '\u26a0 ' + data.error); return; }
    const commits = data.commits || [];
    if (commits.length === 0) { addMsg('system', 'No commits found.'); return; }

    const old = document.querySelector('.git-dialog-overlay');
    if (old) old.remove();
    const overlay = document.createElement('div');
    overlay.className = 'git-dialog-overlay';
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:10000;display:flex;align-items:center;justify-content:center;';
    const dialog = document.createElement('div');
    dialog.style.cssText = 'background:#1a1a2e;border:1px solid #333;border-radius:8px;padding:20px;min-width:500px;max-width:700px;max-height:70vh;display:flex;flex-direction:column;';

    let html = '<div style="font-size:14px;font-weight:600;color:#e0e0e0;margin-bottom:12px;">Rollback Conversation' + (data.branch ? ' (' + escapeHtml(data.branch) + ')' : '') + '</div>';
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
      + 'Also rewind user files (via checkpoints)</label>'
      + '<div style="font-size:11px;color:#e94560;margin-top:4px;padding-left:20px;">'
      + '\u26a0 Risky: will attempt to restore files modified by agents on the relay. May fail if files were changed externally.</div></div>';
    html += '<div style="display:flex;gap:8px;justify-content:flex-end;">'
      + '<button onclick="this.closest(\'.git-dialog-overlay\').remove()" style="padding:6px 16px;background:#333;color:#e0e0e0;border:none;border-radius:4px;cursor:pointer;">Cancel</button>'
      + '<button id="gitRollbackBtn" disabled style="padding:6px 16px;background:#6c5ce7;color:#fff;border:none;border-radius:4px;cursor:pointer;opacity:0.5;">Rollback</button></div>';

    dialog.innerHTML = html;
    overlay.appendChild(dialog);
    document.body.appendChild(overlay);
    overlay.onclick = (ev) => { if (ev.target === overlay) overlay.remove(); };

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
      addMsg('system', 'Rolling back to ' + selectedHash.slice(0, 7) + '...');
      action$('conv_rollback', { conversation_id: cid, commit_hash: selectedHash, rewind_files: rewindFiles }).subscribe(res => {
        if (res.error) { addMsg('system', '\u26a0 ' + res.error); return; }
        let msg = 'Rolled back to ' + selectedHash.slice(0, 7);
        if (res.files) {
          if (res.files.error) msg += '\nFile rewind: ' + res.files.error;
          else msg += '\nFiles: ' + (res.files.restored || 0) + ' restored, ' + (res.files.deleted || 0) + ' deleted';
        }
        addMsg('system', msg);
        loadConversations();
        if (cid === conversationId) reloadConv();
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
  overlay.onclick = (ev) => { if (ev.target === overlay) overlay.remove(); };
  dialog.querySelectorAll('.git-list-item').forEach(row => {
    if (row.dataset.disabled) return;
    row.onclick = () => { overlay.remove(); onSelect(row.dataset.value); };
  });
}

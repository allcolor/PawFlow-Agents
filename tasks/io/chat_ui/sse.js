// Connect SSE for a conversation
var _sseOnReadyCallback = null;
var _sseClientId = null;
var _sseCreatedAt = 0;

function getSSEClientId() {
  if (_sseClientId) return _sseClientId;
  try {
    _sseClientId = sessionStorage.getItem('pawflow_sse_client_id');
    if (!_sseClientId) {
      _sseClientId = 'tab-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
      sessionStorage.setItem('pawflow_sse_client_id', _sseClientId);
    }
  } catch (_err) {
    _sseClientId = 'tab-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
  }
  return _sseClientId;
}

function connectSSE(cid, onReady, opts) {
  if (eventSource) eventSource.close();
  if (sseReconnectTimer) { clearTimeout(sseReconnectTimer); sseReconnectTimer = null; }
  _sseOnReadyCallback = onReady || null;
  startActiveSync();
  sseRetryCount = 0;  // reset so onopen doesn't think we're reconnecting
  const token = getToken();

  // Fan-out every named SSE event to UI extensions. Wraps addEventListener
  // on the new EventSource only so extensions see all events without each
  // native listener having to call _pawflowExtRuntime explicitly.
  function _wrapSseForExtensions(es) {
    if (!window._pawflowExtRuntime) return;
    var _orig = es.addEventListener.bind(es);
    es.addEventListener = function (type, listener, opts) {
      function wrapped(e) {
        try { listener(e); }
        finally {
          try {
            var data = null;
            if (e && typeof e.data === 'string' && e.data.length) {
              try { data = JSON.parse(e.data); } catch (_p) { data = e.data; }
            }
            window._pawflowExtRuntime.fireHook('sse_event',
              { event: type, data: data, conversationId: cid });
            if (type === 'tool_call') {
              window._pawflowExtRuntime.fireHook('tool_call_started', data || {});
            } else if (type === 'tool_result') {
              window._pawflowExtRuntime.fireHook('tool_call_completed', data || {});
            }
          } catch (_ext) { /* never let an extension hook break SSE */ }
        }
      }
      return _orig(type, wrapped, opts);
    };
  }
  // noReplay=true: caller is an explicit reload/switch that just refetched
  // the authoritative history from disk. The server must skip replaying
  // buffered events to this socket -- otherwise
  // the client _seenMsgIds gets populated with ids from the replayed
  // message_meta/done events before _renderHistory runs, and addMsg() dedups
  // legitimate history entries out of the render (transcript truncation).
  // A reload means reload, not replay.
  const _noReplay = !!(opts && opts.noReplay);
  const url = SSE_URL + '?conversation_id=' + encodeURIComponent(cid)
    + '&client_id=' + encodeURIComponent(getSSEClientId())
    + (token ? '&token=' + encodeURIComponent(token) : '')
    + (_noReplay ? '&replay=false' : '');
  _sseCreatedAt = Date.now();
  eventSource = new EventSource(url);
  _wrapSseForExtensions(eventSource);

  // ── Task block grouping ─────────────────────────────────────────
  const _taskBlocks = {};
  const _pendingToolResults = {};
  const _serviceInstallProgress = {};

  function _resultText(value) {
    if (typeof value === 'string') return value;
    try { return JSON.stringify(value, null, 2); }
    catch (_err) { return String(value || ''); }
  }

  function _serviceInstallLabel(data) {
    const status = data.status || 'running';
    const icon = status === 'ready' ? '\u2713' : status === 'failed' ? '\u2715' : '\u23f3';
    const name = data.service_id || data.service_type || 'service';
    const type = data.service_type && data.service_type !== name ? ' (' + data.service_type + ')' : '';
    const phase = data.phase ? ' - ' + data.phase.replace(/_/g, ' ') : '';
    const pct = typeof data.progress === 'number' ? ' [' + Math.round(data.progress * 100) + '%]' : '';
    const msg = data.message ? ': ' + data.message : '';
    return icon + ' Installing ' + name + type + phase + pct + msg;
  }

  function _upsertServiceInstallProgress(data) {
    const key = (data.service_type || 'service') + ':' + (data.service_id || 'default');
    let row = _serviceInstallProgress[key];
    const text = _serviceInstallLabel(data);
    if (!row || !row.isConnected) {
      row = addMsg('system', text, { source: { type: 'system', name: 'service-install' } });
      _serviceInstallProgress[key] = row;
    } else {
      row.textContent = text;
    }
    if (data.status === 'ready' || data.status === 'failed') {
      setTimeout(() => { delete _serviceInstallProgress[key]; }, 5000);
    }
    scrollBottom();
  }

  function _attachPendingToolResult(tcEl, tcId) {
    if (!tcEl || !tcId || !_pendingToolResults[tcId]) return false;
    const pending = _pendingToolResults[tcId];
    if (pending.timer) clearTimeout(pending.timer);
    delete _pendingToolResults[tcId];
    _attachToolResult(tcEl, _resultText((pending.data || {}).result || ''));
    if ((pending.data || {}).msg_id && typeof _seenMsgIds !== 'undefined') {
      _seenMsgIds.add(pending.data.msg_id);
    }
    return true;
  }

  function _queueUnmatchedToolResult(tcId, data) {
    if (!tcId) return false;
    if (_pendingToolResults[tcId] && _pendingToolResults[tcId].timer) {
      clearTimeout(_pendingToolResults[tcId].timer);
    }
    _pendingToolResults[tcId] = { data: data || {}, timer: null };
    _pendingToolResults[tcId].timer = setTimeout(() => {
      const pending = _pendingToolResults[tcId];
      if (!pending) return;
      const tcEl = (typeof findToolCallElement === 'function')
        ? findToolCallElement(tcId)
        : document.querySelector('[data-message-role="tool_call"][data-tc-id="' + tcId + '"]');
      if (tcEl) {
        _attachPendingToolResult(tcEl, tcId);
        return;
      }
      delete _pendingToolResults[tcId];
      const row = addMsg('tool_result', _resultText((pending.data || {}).result || ''), {
        tool_name: pending.data.tool,
        tool: pending.data.tool,
        source: pending.data.source || {type: 'agent', name: pending.data.agent_name || '', llm_service: pending.data.llm_service || ''},
        agent_name: pending.data.agent_name || '',
        llm_service: pending.data.llm_service || '',
        path: pending.data.path || '',
        ts: pending.data.ts,
        msg_id: pending.data.msg_id || '',
        tc_id: tcId,
      });
      if (pending.data.task_id && row) {
        const tb = _getTaskBlock(pending.data.task_id, pending.data.task_iteration, pending.data.agent_name || '');
        if (tb) tb.content.appendChild(row);
      }
      if (typeof applyTechnicalMessageGrouping === 'function') applyTechnicalMessageGrouping();
      scrollBottom();
    }, 750);
    return true;
  }

  function _finalizeLiveToolCalls(agentName, resultText) {
    const targetAgent = (agentName || '').toLowerCase();
    let changed = false;
    document.querySelectorAll('#messages .tc-bullet.pending').forEach(bullet => {
      const tcEl = bullet.closest('[data-message-role="tool_call"]')
        || bullet.closest('[data-tc-id]')
        || bullet.closest('.msg');
      if (!tcEl) return;
      const rowAgent = tcEl.dataset ? (tcEl.dataset.agent || '').toLowerCase() : '';
      if (targetAgent && rowAgent && rowAgent !== targetAgent) return;
      if (!tcEl.querySelector('.tc-result')) {
        try { _attachToolResult(tcEl, resultText || '[Interrupted]'); }
        catch (_err) {
          bullet.classList.remove('pending');
          bullet.classList.add('done');
          tcEl.querySelectorAll('.tc-bg-btn, .tc-kl-btn').forEach(btn => btn.remove());
        }
      } else {
        bullet.classList.remove('pending');
        bullet.classList.add('done');
        tcEl.querySelectorAll('.tc-bg-btn, .tc-kl-btn').forEach(btn => btn.remove());
      }
      if (tcEl.dataset) delete tcEl.dataset.live;
      changed = true;
    });
    if (changed && typeof applyTechnicalMessageGrouping === 'function') applyTechnicalMessageGrouping();
    return changed;
  }

  // Expose a reset hook so resumeConv (which clears #messages but
  // keeps the SSE socket open) can drop stale DOM references — without
  // it, subsequent live events keep targeting detached nodes and the
  // freshly-reloaded transcript ends up out of order or truncated.
  window._sseClearLiveBlocks = function() {
    for (const k in _taskBlocks) delete _taskBlocks[k];
    for (const k in _delegateGroups) delete _delegateGroups[k];
    for (const k in _delegateSubBlocks) delete _delegateSubBlocks[k];
    for (const k in _pendingToolResults) {
      if (_pendingToolResults[k] && _pendingToolResults[k].timer) clearTimeout(_pendingToolResults[k].timer);
      delete _pendingToolResults[k];
    }
    for (const k in delegateThinkingElements) delete delegateThinkingElements[k];
  };

  function _getTaskBlock(taskId, iteration, agentName) {
    if (!taskId) return null;
    if (!window.PAWFLOW_GROUP_TASK_MESSAGES) return null;
    const blockKey = taskId + '::iter' + (iteration || 0);
    if (_taskBlocks[blockKey]) return _taskBlocks[blockKey];
    // First event for this iteration — create the block
    const details = document.createElement('details');
    details.className = 'msg task-block';
    details.setAttribute('open', '');
    details.style.cssText = 'margin:6px 0;border:1px solid #333;border-radius:8px;padding:0;background:#1a1a2e;';
    const summary = document.createElement('summary');
    summary.style.cssText = 'cursor:pointer;padding:8px 12px;font-size:12px;color:#6c5ce7;user-select:none;font-weight:600;display:flex;align-items:center;gap:6px;';
    const iterLabel = (iteration || 0) > 1 ? ' iter ' + iteration : '';
    summary.innerHTML = '\u{1F4CB} Task <span style="color:#e0e0e0;font-weight:normal">' + escapeHtml(taskId) + '</span>'
      + (agentName ? ' <span style="color:#888;font-weight:normal">(' + escapeHtml(displayAgentName(agentName)) + iterLabel + ')</span>' : '')
      + ' <span class="task-block-status" style="margin-left:auto;font-size:11px;color:#888">\u25cf running</span>';
    details.appendChild(summary);
    const content = document.createElement('div');
    content.style.cssText = 'padding:4px 12px 8px;max-height:60vh;overflow-y:auto;';
    details.appendChild(content);
    const container = document.getElementById('messages');
    const typingEl = document.getElementById('typing');
    if (typingEl) container.insertBefore(details, typingEl);
    else container.appendChild(details);
    scrollBottom();
    _taskBlocks[blockKey] = {el: details, content: content, summary: summary, agent: agentName, taskId: taskId};
    return _taskBlocks[blockKey];
  }

  function _taskBlockAppend(taskId, iteration, childEl) {
    const blockKey = taskId + '::iter' + (iteration || 0);
    const block = _taskBlocks[blockKey];
    if (block && childEl) {
      block.content.appendChild(childEl);
      scrollBottom();
    }
  }

  function _finalizeTaskBlock(taskId, iteration, status, color) {
    const blockKey = taskId + '::iter' + (iteration || 0);
    const block = _taskBlocks[blockKey];
    if (block) {
      const statusEl = block.summary.querySelector('.task-block-status');
      if (statusEl) { statusEl.textContent = status || '\u2713 done'; statusEl.style.color = color || '#4ecdc4'; }
      block.el.removeAttribute('open');
    }
  }

  // Plan step instructions — render BEFORE agent starts thinking
  eventSource.addEventListener('new_message', (e) => {
    lastSSEActivity = Date.now();
    const data = e.data ? JSON.parse(e.data) : {};
    if (data.role && data.content) {
      finalizeThinkingFromEvent(data, 'message');
      // Dedup by msg_id — don't render if already in DOM
      if (data.msg_id && document.querySelector('[data-msgid="' + data.msg_id + '"]')) return;
      const el = addMsg(data.role, data.content, {
        source: data.source, msg_id: data.msg_id, ts: data.ts,
        attachments: data.attachments || [],
        task_id: data.task_id || '', task_iteration: data.task_iteration,
      });
      if (typeof conversationTTSOnMessage === 'function') {
        try { conversationTTSOnMessage(data); } catch (_ttsErr) {}
      }
      if (data.task_id && el) {
        const tb = _getTaskBlock(data.task_id, data.task_iteration, data.agent_name || (data.source && data.source.name) || '');
        if (tb) tb.content.appendChild(el);
      }
      if (typeof _noteLiveHistoryAppend === 'function') {
        _noteLiveHistoryAppend(data.message_count, 1, data.msg_id || '');
      }
      scrollBottom();
    }
  });

  // ── Proactive notifications (PushNotification MCP tool) ──────────
  // The backend publishes TWO events per notification:
  //   - `new_message` — already handled above, renders the bell row.
  //   - `notification` — handled here, transient side-channel: bell
  //     sound, toast banner, tab-title flash, browser Notification API
  //     when the tab is backgrounded.
  // Rate-limiting lives server-side; we fire every event we receive.
  eventSource.addEventListener('notification', (e) => {
    lastSSEActivity = Date.now();
    let data = {};
    try { data = e.data ? JSON.parse(e.data) : {}; } catch (_err) { return; }
    const message = data.content || '';
    const fromAgent = data.agent || 'assistant';
    if (!message) return;
    if (!isNotificationsMuted()) {
      try { playNotificationBell(); } catch (_err) { /* no AudioContext in old browsers */ }
    }
    showNotificationToast(fromAgent, message);
    flashTabTitle('🔔 ' + fromAgent + ': ' + message.slice(0, 40));
    if (document.hidden && typeof Notification !== 'undefined'
        && Notification.permission === 'granted') {
      try {
        const n = new Notification(fromAgent + ' → you', {
          body: message,
          tag: 'pawflow-notif-' + (cid || ''),
          silent: isNotificationsMuted(),
        });
        n.onclick = () => { window.focus(); n.close(); };
      } catch (_err) { /* Notification quota or API unavailable */ }
    }
  });

  eventSource.addEventListener('service_install_progress', (e) => {
    lastSSEActivity = Date.now();
    let data = {};
    try { data = e.data ? JSON.parse(e.data) : {}; } catch (_err) { return; }
    _upsertServiceInstallProgress(data);
    document.getElementById('status').textContent = _serviceInstallLabel(data);
  });

  eventSource.addEventListener('thinking', (e) => {
    lastSSEActivity = Date.now();
    const data = e.data ? JSON.parse(e.data) : {};
    const agentName = data.agent_name || '';
    // Task events: just ensure the block exists, don't create new iterations
    // New iterations are triggered by task_progress with iteration number
    if (data.task_id) {
      _getTaskBlock(data.task_id, data.task_iteration, agentName);
      return;
    }
    // New turn starting — clear cancel suppression so tool events show again
    trackAgentStart(agentName);
  });

  // ── Extended thinking ──
  let thinkingElements = {};  // agentKey → {el, text, startTime}
  const delegateThinkingElements = {};  // taskId → {el, content, summary, text, startTime}
  function renderThinkingContent(data, reconcileFinal) {
    const agent = data.agent_name || '';
    const aKey = agentKey(agent);
    const textDelta = data.text || '';
    const msgId = data.msg_id || '';
    if (!textDelta && !thinkingElements[aKey]) return;
    const current = thinkingElements[aKey];
    if (current && msgId && current.msgId && current.msgId !== msgId) {
      finalizeThinking(agent, 'thinking-message');
    }
    if (!thinkingElements[aKey]) {
      // Create collapsible details element
      const details = document.createElement('details');
      details.className = 'msg thinking-block';
      details.dataset.messageRole = 'thinking';
      details.dataset.live = '1';
      details.dataset.sortTs = String((typeof _messageSortTs === 'function') ? _messageSortTs(data) : Date.now() / 1000);
      details.setAttribute('open', '');
      details.style.cssText = 'margin:4px 0;border-left:3px solid #6b7280;padding:4px 8px;opacity:0.7;';
      const summary = document.createElement('summary');
      summary.style.cssText = 'cursor:pointer;font-size:12px;color:#9ca3af;font-style:italic;user-select:none;';
      summary.textContent = t('thinking') + '...';
      details.appendChild(summary);
      const content = document.createElement('div');
      content.style.cssText = 'font-size:12px;color:#9ca3af;font-style:italic;white-space:pre-wrap;max-height:300px;overflow-y:auto;';
      details.appendChild(content);
      // If this thinking belongs to a delegate-reply turn, place the
      // block inside the shared delegate frame for (from→to).
      let _placed = false;
      const _dsrc = data.source || {};
      if (_dsrc.type === 'agent_delegate' && _dsrc.from && _dsrc.to) {
        // MUST match messages.js bidirectional key: sorted pair so
        // both A→B and B→A land in the same shared delegate block.
        const _dpair = [_dsrc.from, _dsrc.to].map(s => String(s).toLowerCase()).sort();
        const _dkey = 'delegate-shared::' + _dpair[0] + '::' + _dpair[1];
        const _dblock = document.querySelector('[data-delegate-key="' + CSS.escape(_dkey) + '"]');
        const _dbody = _dblock && _dblock.querySelector('.delegate-body');
        if (_dbody) { _dbody.appendChild(details); _placed = true; }
      }
      if (!_placed && data.task_id) {
        const tb = _getTaskBlock(data.task_id, data.task_iteration, agent);
        if (tb) { tb.content.appendChild(details); scrollBottom(); _placed = true; }
      }
      if (!_placed) {
        const _msgContainer = document.getElementById('messages');
        const _sortTs = (typeof _messageSortTs === 'function') ? _messageSortTs(data) : Date.now() / 1000;
        if (typeof _insertMessageChronologically === 'function') {
          _insertMessageChronologically(_msgContainer, details, _sortTs);
        } else {
          const _typingEl = document.getElementById('typing');
          if (_typingEl) _msgContainer.insertBefore(details, _typingEl);
          else _msgContainer.appendChild(details);
        }
      }
      if (typeof applyTechnicalMessageGrouping === 'function') applyTechnicalMessageGrouping();
      thinkingElements[aKey] = {el: details, content: content, summary: summary, text: '', msgId: msgId, startTime: Date.now()};
      scrollBottom();
    }
    const te = thinkingElements[aKey];
    if (msgId && !te.msgId) te.msgId = msgId;
    if (reconcileFinal) {
      te.text = textDelta.startsWith(te.text)
        ? te.text + textDelta.slice(te.text.length)
        : textDelta;
    } else {
      te.text += textDelta;
    }
    te.content.textContent = te.text;
    if (typeof applyTechnicalMessageGrouping === 'function') applyTechnicalMessageGrouping();
    scrollBottom();
  }

  eventSource.addEventListener('thinking_delta', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    renderThinkingContent(data, false);
  });

  eventSource.addEventListener('thinking_content', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    renderThinkingContent(data, !!data.msg_id);
  });

  // Finalize a thinking block when any non-thinking event arrives for that
  // agent. Consecutive thinking_content chunks append to the same block; once
  // a tool call, message, token, result, or done event arrives, the next
  // thinking_content must create a new block.
  function finalizeThinking(agentName, reason) {
    const aKey = agentKey(agentName || '');
    const te = thinkingElements[aKey];
    if (!te) return;
    const elapsed = (Date.now() - te.startTime) / 1000;
    const group = te.el.closest && te.el.closest('.technical-group');
    if (!te.text.trim()) {
      te.el.remove();
      if (group && typeof _updateTechnicalGroupSummary === 'function') _updateTechnicalGroupSummary(group);
      delete thinkingElements[aKey];
    } else {
      te.summary.textContent = t('thoughtFor', { sec: elapsed.toFixed(1) });
      te.el.setAttribute('open', '');
      if (te.el.dataset) delete te.el.dataset.live;
      delete thinkingElements[aKey];
    }
    if (typeof applyTechnicalMessageGrouping === 'function') applyTechnicalMessageGrouping();
  }

  function finalizeThinkingFromEvent(data, reason) {
    const agent = (data && (data.agent_name || (data.source && data.source.name))) || '';
    if (agent) finalizeThinking(agent, reason);
  }

  function finalizeDelegateThinking(taskId) {
    const te = taskId ? delegateThinkingElements[taskId] : null;
    if (!te) return;
    if (!String(te.text || '').trim()) {
      te.el.remove();
    } else {
      const elapsed = (Date.now() - te.startTime) / 1000;
      te.summary.textContent = t('thoughtFor', { sec: elapsed.toFixed(1) });
      te.el.setAttribute('open', '');
      if (te.el.dataset) delete te.el.dataset.live;
    }
    delete delegateThinkingElements[taskId];
  }

  eventSource.addEventListener('token', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    const agent = data.agent_name || '';
    if (typeof conversationTTSOnToken === 'function') {
      try { conversationTTSOnToken(data); } catch (_ttsErr) {}
    }
    // Finalize thinking block when first text token arrives
    finalizeThinking(agent, 'token');
    const s = getStream(agent);
    s.text += data.text;
    s.msg_id = data.msg_id || s.msg_id || '';  // track msg_id from tokens
    // Always have a source — every response comes from an agent
    const src = data.source || {type: 'agent', name: agent};
    if (!s.el) {
      s.el = addMsg('assistant', '', {source: src, msg_id: s.msg_id});
      // If this is a delegate reply, route updates into the inner node
      // inside the delegate block instead of the outer wrapper.
      if (s.el && s.el._delegateInner) s.el = s.el._delegateInner;
      // Tag with agent name and msg_id for done/meta lookup
      if (s.el) {
        s.el.dataset.agent = (agent || '').toLowerCase();
        if (s.msg_id) s.el.dataset.msgid = s.msg_id;
        // Move into task block if this is a task event
        if (data.task_id) {
          const tb = _getTaskBlock(data.task_id, data.task_iteration, agent);
          if (tb) { tb.content.appendChild(s.el); scrollBottom(); }
        }
      }
      s.chunks.push(s.el);
    }
    // Update content with badge — strip identity prefix if LLM echoed it
    const badge = sourceBadge(src);
    const displayText = s.text.replace(/^\[[^\]]+\]:\s*/, '');
    const shouldScroll = isNearBottom();
    // Update content area only — preserve action buttons and meta
    let contentEl = s.el.querySelector('.msg-content');
    if (!contentEl) {
      // First update: restructure into content + actions + time + meta
      const actions = s.el.querySelector('.msg-actions');
      const timeEl = s.el.querySelector('.msg-time');
      const meta = s.el.querySelector('.msg-meta');
      contentEl = document.createElement('span');
      contentEl.className = 'msg-content';
      s.el.innerHTML = '';
      if (timeEl) s.el.appendChild(timeEl);
      s.el.appendChild(contentEl);
      if (actions) s.el.appendChild(actions);
      else s.el.insertAdjacentHTML('beforeend',
          '<span class="msg-actions">'
          + '<button onclick="setReplyTo(this)" title="' + escapeHtml(t('reply')) + '">\u21A9</button>'
          + '<button onclick="speakMsg(this)" title="' + escapeHtml(t('readMessage')) + '">\uD83D\uDD0A</button>'
          + '<button onclick="copyMsg(this)" title="' + escapeHtml(t('copy')) + '">\uD83D\uDCCB</button>'
          + '<button onclick="deleteMsg(this)" title="' + escapeHtml(t('delete')) + '">\uD83D\uDDD1</button>'
          + '</span>');
      if (meta) s.el.appendChild(meta);
    }
    contentEl.innerHTML = badge + renderMarkdown(displayText);
    if (displayText.trim() && s.el && s.el.dataset) delete s.el.dataset.transientUi;
    if (displayText.trim() && s.el && !s.el.dataset.technicalGroupsCollapsed) {
      collapseTechnicalGroups();
      s.el.dataset.technicalGroupsCollapsed = '1';
    }
    scrollBottom(shouldScroll);
    document.getElementById('status').textContent = t('streaming');
  });

  // Turn complete: finalize streaming element between Claude Code turns
  // so each turn looks like a proper message (badge, background, border)
  // Context-ack patterns that should never be displayed (LLM echoing pre-filled context)
  const _CONTEXT_ACKS = new Set([
    "Understood. I'll continue from where I left off.",
    "Understood. I'll read the conversation history file to get full context, then continue from the recent messages.",
    "Understood. I have the summary and will continue from the recent messages.",
    "Understood, continuing.",
    "Understood.",
    "No response requested.",
  ]);

  eventSource.addEventListener('turn_complete', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    const agent = data.agent_name || '';
    const s = streams[agent.toLowerCase()];
    if (s && s.el) {
      // Suppress: context-ack echo stripped server-side OR detected client-side
      const _streamedText = (s.text || '').trim();
      if (data.suppress || _CONTEXT_ACKS.has(_streamedText)) {
        s.el.remove();
        s.el = null;
        s.text = '';
        s.chunks = [];
        return;
      }
      // Finalize: proper message class + keep as permanent element
      s.el.classList.remove('streaming');
      s.el.classList.add('finalized');
      s.el.dataset.finalizedAgent = agent.toLowerCase();
      if (data.msg_id) s.el.dataset.msgid = data.msg_id;
      s.lastEl = s.el;
      // Update metainfo with estimated tokens (real values come in done)
      if (data.source) {
        const existingMeta = s.el.querySelector('.msg-meta');
        const meta = buildMetaLine(data);
        if (existingMeta && meta) {
          existingMeta.outerHTML = meta;
        } else if (meta) {
          s.el.insertAdjacentHTML('beforeend', meta);
        }
      }
      // Reset stream so next tokens create a NEW element
      s.el = null;
      s.text = '';
    }
  });

  // Per-message metadata: attaches model/tokens to the correct element by msg_id
  eventSource.addEventListener('message_meta', (e) => {
    const data = JSON.parse(e.data);
    // Single update path — setContextUsage enforces the monotonic
    // invariants (no demote-to-zero, no decrease without compact) and
    // mirrors the value to both `_contextUsage` (header / Resource
    // Panel) and `activeInteractions` (active-agents panel).
    if (data.agent_name && (data.context_max || 0) > 0
        && typeof setContextUsage === 'function') {
      setContextUsage(data.agent_name, {
        conversation_id: data.conversation_id || cid,
        used: data.context_used,
        max: data.context_max,
        pct: data.context_pct,
        updated_at: data.updated_at || data.ts,
      });
      if (typeof updateActivePanel === 'function') updateActivePanel();
    }
    if (!data.msg_id) return;
    // Find the element by data-msgid and update metadata (replace if exists).
    // DO NOT preemptively add to _seenMsgIds here — message_meta arrives
    // live, before the actual message SSE (e.g. new_message with the text
    // content) gets a chance to create the DOM element. Adding to
    // _seenMsgIds at this point would make addMsg reject the subsequent
    // new_message payload (see addMsg dedup at messages.js:125). addMsg
    // itself adds to _seenMsgIds on successful creation, which is the
    // authoritative "we've rendered this" marker.
    const el = document.querySelector('#messages [data-msgid="' + data.msg_id + '"]');
    if (el) {
      // DOM element exists — safe to mark as seen so replay won't
      // duplicate it. (addMsg would do this on creation; message_meta is
      // the non-creation path.)
      if (typeof _seenMsgIds !== 'undefined') _seenMsgIds.add(data.msg_id);
      if (Object.prototype.hasOwnProperty.call(data, 'is_error')) {
        el.classList.toggle('error', !!data.is_error);
      }
      const meta = buildMetaLine(data);
      if (meta) {
        const existing = el.querySelector('.msg-meta');
        if (existing) existing.outerHTML = meta;
        else el.insertAdjacentHTML('beforeend', meta);
      }
    }
  });

  eventSource.addEventListener('iteration_status', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    const agentName = data.agent_name || '';
    // Task events: skip entirely — task agents are not in activeInteractions
    if (data.task_id) return;
    const aKey = agentKey(agentName);
    if (activeInteractions[aKey]) {
      activeInteractions[aKey].iteration = data.iteration;
      activeInteractions[aKey].maxIterations = data.max_iterations;
      activeInteractions[aKey].round = data.round;
      activeInteractions[aKey].maxRounds = data.max_rounds;
      activeInteractions[aKey].totalTools = data.total_tools;
      activeInteractions[aKey].updatedAt = Date.now();
      if (data.tools_called && data.tools_called.length > 0) {
        activeInteractions[aKey].lastTool = data.tools_called[data.tools_called.length - 1];
      }
    }
    updateActivePanel();
    document.getElementById('status').textContent =
      t('iterStatus', {agent: displayAgentName(agentName), i: data.iteration, r: data.round, mr: data.max_rounds, t: data.total_tools});
  });

  eventSource.addEventListener('flowfile_in', () => {
    lastSSEActivity = Date.now();
  });

  // ── Delegate blocks (grouped) ─────────────────────────────────
  // _delegateGroups[delegateTcId] = { el, content, summary, total, doneCount, subBlocks: {} }
  // _delegateSubBlocks[taskId] = { el, content, summary, agent, taskId }
  const _delegateGroups = {};
  const _delegateSubBlocks = {};

  function _getOrCreateGroup(delegateTcId, srcAgent, total, sourceTaskId) {
    if (!window.PAWFLOW_GROUP_DELEGATE_MESSAGES) return null;
    if (!delegateTcId) return null;
    if (_delegateGroups[delegateTcId]) return _delegateGroups[delegateTcId];
    const details = document.createElement('details');
    details.className = 'msg delegate-block delegate-group';
    details.dataset.messageRole = 'sub_agent_trace';
    details.dataset.sortTs = String(Date.now() / 1000);
    details.setAttribute('open', '');
    const summary = document.createElement('summary');
    summary.className = 'delegate-header';
    const label = total > 1
      ? '\u{1F500} ' + escapeHtml(displayAgentName(srcAgent)) + ' \u2192 Delegate (' + total + ' agents)'
      : '\u{1F500} ' + escapeHtml(displayAgentName(srcAgent));
    summary.innerHTML = label;
    details.appendChild(summary);
    const content = document.createElement('div');
    content.className = 'delegate-body';
    details.appendChild(content);
    // If spawned from a task, nest inside the task block
    let parentFound = false;
    if (sourceTaskId) {
      for (const bk of Object.keys(_taskBlocks).reverse()) {
        if (bk.startsWith(sourceTaskId + '::iter')) {
          _taskBlocks[bk].content.appendChild(details);
          parentFound = true;
          break;
        }
      }
    }
    if (!parentFound) {
      const container = document.getElementById('messages');
      const typingEl = document.getElementById('typing');
      if (typingEl) container.insertBefore(details, typingEl);
      else container.appendChild(details);
    }
    if (typeof applyTechnicalMessageGrouping === 'function') applyTechnicalMessageGrouping();
    scrollBottom();
    _delegateGroups[delegateTcId] = { el: details, content, summary, total: total || 1, doneCount: 0, subBlocks: {} };
    return _delegateGroups[delegateTcId];
  }

  function _getOrCreateSubBlock(delegateTcId, taskId, dstAgent, llmService, message) {
    if (!window.PAWFLOW_GROUP_DELEGATE_MESSAGES) return null;
    if (_delegateSubBlocks[taskId]) return _delegateSubBlocks[taskId];
    // Ensure group exists (fallback for missing delegate_group_start)
    let group = _delegateGroups[delegateTcId];
    if (!group) group = _getOrCreateGroup(delegateTcId, '', 1);
    const isMulti = group.total > 1;
    // For single-agent, update the group header with arrow
    if (!isMulti && dstAgent) {
      const svcLabel = llmService ? ' via ' + escapeHtml(llmService) : '';
      group.summary.innerHTML = '\u{1F500} <span class="delegate-src">' + escapeHtml(displayAgentName(group.summary.dataset.src || ''))
        + '</span> \u2192 <span class="delegate-dst">' + escapeHtml(displayAgentName(dstAgent)) + '</span>'
        + svcLabel;
    }
    // Create sub-block (details for multi, div for single)
    let subEl, subContent, subSummary;
    if (isMulti) {
      subEl = document.createElement('details');
      subEl.className = 'delegate-sub-block';
      subEl.setAttribute('open', '');
      subSummary = document.createElement('summary');
      subSummary.className = 'delegate-sub-header';
      const svcLabel = llmService ? ' via ' + escapeHtml(llmService) : '';
      subSummary.innerHTML = '\u25b8 <span class="delegate-dst">' + escapeHtml(displayAgentName(dstAgent)) + '</span>'
        + svcLabel
        + ' <button class="delegate-cancel-btn" data-task-id="' + escapeHtml(taskId) + '" title="' + escapeHtml(t('cancelThisAgent')) + '">\u2715</button>';
      subEl.appendChild(subSummary);
      subContent = document.createElement('div');
      subContent.className = 'delegate-sub-body';
      subEl.appendChild(subContent);
    } else {
      // Single agent — content goes directly in the group body
      subEl = group.el;
      subContent = group.content;
      subSummary = group.summary;
      // Add cancel button to single-agent header
      if (!subSummary.querySelector('.delegate-cancel-btn')) {
        const btn = document.createElement('button');
        btn.className = 'delegate-cancel-btn';
        btn.dataset.taskId = taskId;
        btn.title = t('cancelThisAgent');
        btn.textContent = '\u2715';
        subSummary.appendChild(btn);
      }
    }
    // Show the message from parent agent
    if (message) {
      const msgEl = document.createElement('div');
      msgEl.className = 'delegate-message';
      msgEl.innerHTML = '\u{1F4E9} ' + renderMarkdown(message);
      subContent.appendChild(msgEl);
    }
    if (isMulti) {
      group.content.appendChild(subEl);
      scrollBottom();
    }
    // Tag for cross-rendering dedupe (messages.js looks up this attribute
    // when classifying a sub_agent_trace from the store and skips it if a
    // live SSE block for the same sub-agent task already exists).
    if (subEl && taskId) subEl.dataset.delegateTaskId = taskId;
    const block = { el: subEl, content: subContent, summary: subSummary, agent: dstAgent, taskId };
    _delegateSubBlocks[taskId] = block;
    group.subBlocks[taskId] = block;
    return block;
  }

  function _subBlockAppend(taskId, childEl) {
    const block = _delegateSubBlocks[taskId];
    if (block && childEl) {
      block.content.appendChild(childEl);
      scrollBottom();
    }
  }

  // Cancel button handler (event delegation)
  document.addEventListener('click', (e) => {
    const btn = e.target.closest('.delegate-cancel-btn');
    if (!btn) return;
    e.preventDefault();
    e.stopPropagation();
    const taskId = btn.dataset.taskId;
    if (taskId) {
      fireAction('cancel_sub_agent', { task_id: taskId });
      btn.disabled = true;
      btn.textContent = '\u23f3';
    }
  });

  // Group start: server tells us how many agents are being spawned
  eventSource.addEventListener('delegate_group_start', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    if (data.delegate_tc_id) {
      const group = _getOrCreateGroup(data.delegate_tc_id, data.source_agent || '', data.total || 1, data.source_task_id || '');
      if (group) group.summary.dataset.src = data.source_agent || '';
    }
  });

  // Sub-agent visibility
  eventSource.addEventListener('sub_agent_start', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    trackAgentStart(data.agent_name, data.message ? data.message.substring(0, 40) : '', data.task_id || '');
    if (data.delegate_tc_id && data.task_id) {
      // Ensure group exists (handles case where delegate_group_start wasn't received)
      const group = _getOrCreateGroup(data.delegate_tc_id, data.source_agent || '', 1, data.source_task_id || '');
      if (group) group.summary.dataset.src = data.source_agent || '';
      _getOrCreateSubBlock(data.delegate_tc_id, data.task_id, data.agent_name || '', data.llm_service || '', data.message || '');
    }
  });

  eventSource.addEventListener('sub_agent_iteration', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    const agentName = data.agent_name || 'sub-agent';
    const aKey = agentKey(agentName);
    if (activeInteractions[aKey]) {
      activeInteractions[aKey].iteration = data.iteration;
      activeInteractions[aKey].maxIterations = data.max_iterations;
      activeInteractions[aKey].totalTools = data.total_tools;
      if (data.tools_called && data.tools_called.length > 0) {
        activeInteractions[aKey].lastTool = data.tools_called[data.tools_called.length - 1];
      }
    }
    updateActivePanel();
  });

  eventSource.addEventListener('sub_agent_thinking', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    if (data.task_id && _delegateSubBlocks[data.task_id]) {
      let te = delegateThinkingElements[data.task_id];
      if (!te) {
        const el = document.createElement('details');
        el.className = 'delegate-thinking';
        el.dataset.messageRole = 'thinking';
        el.dataset.live = '1';
        el.setAttribute('open', '');
        const summary = document.createElement('summary');
        summary.textContent = '\u{1F4AD} ' + t('thinking') + '...';
        el.appendChild(summary);
        const content = document.createElement('div');
        content.className = 'delegate-thinking-content';
        el.appendChild(content);
        _subBlockAppend(data.task_id, el);
        te = delegateThinkingElements[data.task_id] = {el, content, summary, text: '', startTime: Date.now()};
      }
      te.text += data.thinking || '';
      te.content.textContent = te.text;
    }
  });

  eventSource.addEventListener('sub_agent_text', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    if (data.task_id) finalizeDelegateThinking(data.task_id);
    if (data.task_id && _delegateSubBlocks[data.task_id]) {
      const el = document.createElement('div');
      el.className = 'delegate-text';
      el.innerHTML = renderMarkdown(data.text || '');
      _subBlockAppend(data.task_id, el);
    }
  });

  eventSource.addEventListener('sub_agent_tool', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    if (data.task_id) finalizeDelegateThinking(data.task_id);
    const agentName = data.agent_name || 'sub-agent';
    trackAgentTool(agentName, data.tool, data.task_id || '');
    if (data.task_id && _delegateSubBlocks[data.task_id]) {
      const el = document.createElement('div');
      el.className = 'delegate-tool';
      if (data.tc_id) el.dataset.tcId = data.tc_id;
      const display = (_TOOL_DISPLAY[data.tool] || data.tool || '?');
      let argSummary = '';
      if (data.arguments && typeof data.arguments === 'object') {
        const keys = Object.keys(data.arguments);
        if (keys.length === 1) {
          argSummary = String(data.arguments[keys[0]]).substring(0, 120);
        } else if (keys.length > 1) {
          argSummary = keys.map(k => k + '=' + String(data.arguments[k]).substring(0, 60)).join(', ').substring(0, 120);
        }
      }
      el.innerHTML = '<span class="tc-bullet pending">\u25cf</span> ' + escapeHtml(display) + '(' + escapeHtml(argSummary) + ')';
      _subBlockAppend(data.task_id, el);
    }
  });

  eventSource.addEventListener('sub_agent_tool_result', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    if (data.task_id) finalizeDelegateThinking(data.task_id);
    if (data.task_id && data.tc_id && _delegateSubBlocks[data.task_id]) {
      const block = _delegateSubBlocks[data.task_id];
      const tcEl = block.content.querySelector('[data-tc-id="' + data.tc_id + '"]');
      if (tcEl) {
        const bullet = tcEl.querySelector('.tc-bullet');
        if (bullet) { bullet.classList.remove('pending'); bullet.classList.add('done'); }
        if (data.result) {
          const resDiv = document.createElement('div');
          resDiv.className = 'delegate-tool-result';
          const firstLine = data.result.split('\n')[0].substring(0, 120);
          resDiv.innerHTML = '<details><summary>\u23bf ' + escapeHtml(firstLine) + '</summary>'
            + '<pre class="tc-output">' + renderTextWithInlineMedia(data.result) + '</pre></details>';
          tcEl.appendChild(resDiv);
        }
      }
    }
  });

  eventSource.addEventListener('sub_agent_done', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    if (data.task_id) finalizeDelegateThinking(data.task_id);
    const agent = data.agent_name || 'sub-agent';
    trackAgentDone(agent, data.task_id || '');
    const taskId = data.task_id;
    const delegateTcId = data.delegate_tc_id;
    // Finalize sub-block — a delegate is not a task, no done/error
    // status badge. Just remove the cancel button and append the
    // response (or error/question) inline.
    if (taskId && _delegateSubBlocks[taskId]) {
      const block = _delegateSubBlocks[taskId];
      const cancelBtn = block.summary.querySelector('.delegate-cancel-btn');
      if (cancelBtn) cancelBtn.remove();
      // Add question (ask_parent), response, or error
      if (data.status === 'needs_input' && data.question) {
        const qEl = document.createElement('div');
        qEl.className = 'delegate-question';
        qEl.innerHTML = '\u{1F4AC} ' + renderMarkdown(data.question);
        block.content.appendChild(qEl);
      } else if (data.response) {
        const respEl = document.createElement('div');
        respEl.className = 'delegate-response';
        respEl.innerHTML = '\u{1F4E8} ' + renderMarkdown(data.response);
        block.content.appendChild(respEl);
      } else if (data.error) {
        const errEl = document.createElement('div');
        errEl.className = 'delegate-error';
        errEl.textContent = '\u274C ' + data.error;
        block.content.appendChild(errEl);
      }
      // Add stats line
      const statsEl = document.createElement('div');
      statsEl.className = 'delegate-stats';
      const parts = [];
      if (data.model) parts.push(data.model);
      parts.push('\u2191' + (data.tokens_in || 0) + ' \u2193' + (data.tokens_out || 0));
      if (data.duration_s) parts.push(data.duration_s + 's');
      parts.push((data.tools_called || []).length + ' tools');
      statsEl.textContent = parts.join(' \u00b7 ');
      block.content.appendChild(statsEl);
      // Auto-collapse (but not for needs_input — keep visible)
      const group = delegateTcId && _delegateGroups[delegateTcId];
      if (data.status !== 'needs_input') {
        if (group && group.total > 1) {
          setTimeout(() => { block.el.removeAttribute('open'); }, 1500);
        }
        // Auto-collapse the group when all sub-blocks have finished.
        if (group) {
          group.doneCount++;
          if (group.doneCount >= group.total) {
            setTimeout(() => { group.el.removeAttribute('open'); }, 2000);
          }
        }
      }
      scrollBottom();
    } else {
      // Fallback: no delegate block — render as standalone message.
      const svcInfo = data.llm_service ? ' via ' + data.llm_service : '';
      if (data.response && !_CONTEXT_ACKS.has((data.response || '').trim())) {
        const extra = { source: { type: 'agent', name: agent, llm_service: data.llm_service || '' } };
        if (data.source_agent) extra.source.reply_to = data.source_agent;
        extra.model = data.model || '';
        extra.provider = data.provider || '';
        extra.tokens_in = data.tokens_in || 0;
        extra.tokens_out = data.tokens_out || 0;
        extra.duration_ms = (data.duration_s || 0) * 1000;
        extra.ts = data.ts;
        addMsg('assistant', data.response, extra);
      } else if (data.error) {
        addMsg('agent-result', t('errorWithMessage', { error: data.error }), agent);
      }
      scrollBottom();
    }
  });

  // Track cancelled agents for status cleanup only. Transcript events already
  // mean persisted messages; the chat must render them unless msg_id dedupes.

  eventSource.addEventListener('tool_call', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    if (typeof _hasCompleteMcpDisplayedToolCall === 'function'
        && !_hasCompleteMcpDisplayedToolCall(data.tool, data.arguments || {})) {
      pawflowDebugLog('[SSE] ignoring incomplete MCP tool_call:', data.tool);
      return;
    }
    finalizeThinkingFromEvent(data, 'tool_call');
    if (typeof _noteLiveHistoryAppend === 'function') {
      _noteLiveHistoryAppend(data.message_count, 1, data.msg_id || '');
    }
    pawflowDebugLog('[SSE] tool_call received:', data.tool, data.agent_name, data.llm_service, JSON.stringify(data.arguments || {}).substring(0, 200));
    // Finalize streaming for THIS agent before showing tool call
    const tcAgent = data.agent_name || '';
    const tcs = streams[tcAgent.toLowerCase()];
    if (tcs && tcs.el) {
      // Finalize: keep reference for done handler to add metadata later
      tcs.el.classList.add('finalized');
      tcs.el.dataset.finalizedAgent = tcAgent.toLowerCase();
      tcs.lastEl = tcs.el;  // preserve for done handler
      tcs.el = null; tcs.text = '';
    }
    trackAgentTool(tcAgent, data.tool, data.task_id || '');
    // Hide delegate tool_call — the delegate block replaces it.
    // When delegate grouping is disabled, render the call normally so the
    // user still sees the live launch + result in the main timeline.
    if (data.tool === 'delegate' && window.PAWFLOW_GROUP_DELEGATE_MESSAGES) {
      // Store tc_id so we can suppress the tool_result too
      if (data.tc_id) _delegateGroups['__tc__' + data.tc_id] = true;
      if (!data.task_id) document.getElementById('status').textContent = t('usingTool', {tool: (_TOOL_DISPLAY[data.tool] || data.tool)});
      return;
    }
    // Single rendering path: addMsg handles ALL tool_call rendering
    const tcExtra = {
      tool_name: data.tool,
      arguments: data.arguments || {},
      tool_args: data.arguments || {},
      tc_id: data.tc_id || '',
      source: data.source || {type: 'agent', name: tcAgent, llm_service: data.llm_service || ''},
      agent_name: tcAgent,
      llm_service: data.llm_service || '',
      tool_origin: data.tool_origin || '',
      ts: data.ts,
      live: true,
    };
    if (data.parent_tc_id) tcExtra.parent_tc_id = data.parent_tc_id;
    const tcEl = addMsg('tool_call', data.tool, tcExtra);
    // Tag owning agent so the `done` handler can scope its cleanup
    // (otherwise agent A's done closes agent B's still-live tools).
    if (tcEl) tcEl.dataset.agent = (tcAgent || '').toLowerCase();
    // Move into task block if this is a task event
    if (data.task_id && tcEl && !data.parent_tc_id) {
      const tb = _getTaskBlock(data.task_id, data.task_iteration, tcAgent);
      if (tb) { tb.content.appendChild(tcEl); scrollBottom(); }
    }
    // Group under parent agent tool_call if this is a sub-agent tool
    if (data.parent_tc_id && tcEl) {
      const parentEl = (typeof findToolCallElement === 'function')
        ? findToolCallElement(data.parent_tc_id)
        : document.querySelector('[data-tc-id="' + data.parent_tc_id + '"]');
      if (parentEl) {
        let childContainer = parentEl.querySelector('.tc-children');
        if (!childContainer) {
          childContainer = document.createElement('div');
          childContainer.className = 'tc-children';
          childContainer.style.cssText = 'margin-left:16px;border-left:2px solid #333;padding-left:8px;';
          parentEl.appendChild(childContainer);
        }
        childContainer.appendChild(tcEl);
      }
    }
    if (tcEl && data.tc_id) _attachPendingToolResult(tcEl, data.tc_id);
    if (typeof applyTechnicalMessageGrouping === 'function') applyTechnicalMessageGrouping();
    scrollBottom();
    if (!data.task_id) document.getElementById('status').textContent = t('usingTool', {tool: (_TOOL_DISPLAY[data.tool] || data.tool)});
  });

  eventSource.addEventListener('tool_result', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    finalizeThinkingFromEvent(data, 'tool_result');
    if (data.agent_name) trackAgentToolDone(data.agent_name, data.tool, data.task_id || '');
    if (typeof _noteLiveHistoryAppend === 'function') {
      _noteLiveHistoryAppend(data.message_count, 1, data.msg_id || '');
    }
    // Suppress delegate tool_result — the delegate block shows the response
    const tcId = data.tc_id || '';
    if (tcId && _delegateGroups['__tc__' + tcId]) return;
    // Try to attach to matching tool_call element
    if (tcId) {
      const tcEl = (typeof findToolCallElement === 'function')
        ? findToolCallElement(tcId)
        : document.querySelector('[data-tc-id="' + tcId + '"]');
      if (tcEl) {
        _attachToolResult(tcEl, _resultText(data.result || ''));
        if (tcEl.dataset) delete tcEl.dataset.live;
        if (data.msg_id && typeof _seenMsgIds !== 'undefined') _seenMsgIds.add(data.msg_id);
        if (typeof applyTechnicalMessageGrouping === 'function') applyTechnicalMessageGrouping();
        scrollBottom();
        return;
      }
    }
    if (tcId && _queueUnmatchedToolResult(tcId, data)) return;
    // Fallback: standalone element
    const trEl = addMsg('tool_result', _resultText(data.result || ''), {
      tool_name: data.tool,
      tool: data.tool,
      source: data.source || {type: 'agent', name: data.agent_name || '', llm_service: data.llm_service || ''},
      agent_name: data.agent_name || '',
      llm_service: data.llm_service || '',
      tool_origin: data.tool_origin || '',
      path: data.path || '',
      ts: data.ts,
      msg_id: data.msg_id || '',
      tc_id: tcId,
    });
    // Route into task block if this is a task event
    if (data.task_id && trEl) {
      const tb = _getTaskBlock(data.task_id, data.task_iteration, data.agent_name || '');
      if (tb) { tb.content.appendChild(trEl); scrollBottom(); }
    }
    if (typeof applyTechnicalMessageGrouping === 'function') applyTechnicalMessageGrouping();
    scrollBottom();
  });

  eventSource.addEventListener('bg_task_update', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    const tcId = data.tc_id || '';
    if (tcId) {
      const tcEl = (typeof findToolCallElement === 'function')
        ? findToolCallElement(tcId)
        : document.querySelector('[data-tc-id="' + tcId + '"]');
      if (tcEl) {
        if (data.status === 'done' || data.status === 'cancelled' || data.status === 'error') {
          const fallback = data.status === 'cancelled' ? '[Cancelled]' : data.status === 'error' ? '[Error]' : '[Done]';
          _attachToolResult(tcEl, data.result || fallback);
        }
      }
    }
  });

  eventSource.addEventListener('compact_progress', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    if (data.stage === 'start') {
      _finalizeLiveToolCalls(data.agent || '', '[Interrupted by compact]');
      var opLabel = data.detail || 'compact';
      opLabel = String(opLabel).replace(/_/g, ' ');
      showContextOp(opLabel.charAt(0).toUpperCase() + opLabel.slice(1) + ' ' + (data.agent || '') + '...');
    } else if (data.stage === 'chunking' || data.stage === 'summarizing') {
      showContextOp((data.detail || data.stage) + '...');
    } else if (data.stage === 'git_prune') {
      showContextOp('Pruning conversation Git history: ' + (data.detail || 'working') + '...');
    } else if (data.stage === 'done') {
      hideContextOp();
      if (data.operation === 'restart_from') {
        const restartPromptText = data.restart_prompt_text || data.prompt_text || '';
        if (conversationId) resumeConv(conversationId, true);
        if (restartPromptText && typeof setPromptTextForRestart === 'function') {
          setTimeout(() => setPromptTextForRestart(restartPromptText), 100);
        }
        return;
      }
      if (data.operation === 'git_prune') {
        const beforeMb = data.size_before !== undefined ? (data.size_before / 1048576).toFixed(1) : '?';
        const afterMb = data.size_after !== undefined ? (data.size_after / 1048576).toFixed(1) : '?';
        const beforeCommits = data.commits_before !== undefined ? data.commits_before : '?';
        const afterCommits = data.commits_after !== undefined ? data.commits_after : '?';
        addMsg('system', 'Git history pruned: ' + beforeCommits + ' -> ' + afterCommits + ' commits, ' + beforeMb + ' MB -> ' + afterMb + ' MB.');
        return;
      }
      const agent = data.agent || 'shared';
      // Authorise the next gauge decrease for this agent — the
      // post-compact `message_meta` will be smaller than the cached
      // value by design. Without this, the monotonic guard in
      // setContextUsage would reject the drop.
      if (typeof markCompactJustHappened === 'function') {
        markCompactJustHappened(agent);
      }
      // Gauge is updated by the authoritative message_meta event emitted
      // after the server refreshes the compacted PawFlow context.
      // Show TOTAL conversation msg count as the reference (what the
      // user thinks of as "the conversation size"). `before` is the
      // per-agent context pre-compact — meaningless to display
      // without the total, which is what they see in the history panel.
      const total = data.conv_total_messages !== undefined
        ? data.conv_total_messages
        : (data.before !== undefined ? data.before : '?');
      const after = data.after !== undefined ? data.after : '?';
      const tokAfter = data.tokens_after !== undefined ? data.tokens_after : '?';
      const tokTarget = data.target_tokens !== undefined ? data.target_tokens : null;
      const tokenText = tokTarget !== null ? (tokAfter + '/' + tokTarget) : tokAfter;
      addMsg('system', t('contextCompactedStatus', {
        agent: agent,
        before: total,
        after: after,
        tokens: tokenText,
      }));
    } else if (data.stage === 'error') {
      hideContextOp();
      addMsg('error', t('contextOperationFailed', { error: data.error }));
    }
  });

  eventSource.addEventListener('task_progress', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    const agent = displayAgentName(data.agent || '?');
    if (data.stage === 'assigned') {
      const v = data.verifier ? t('taskVerifierSuffix', { verifier: displayAgentName(data.verifier) }) : '';
      addMsg('system', '\u{1F4CB} ' + t('taskAssignedTo', { agent: agent, verifier: v, task: (data.task || '').substring(0, 150) }));
    } else if (data.stage === 'verified') {
      const icon = data.approved ? '\u2705' : '\u274C';
      const verifier = displayAgentName(data.verifier || '?');
      addMsg('system', icon + ' ' + t(data.approved ? 'taskApprovedBy' : 'taskRejectedBy', { agent: agent, verifier: verifier, reason: data.reason ? ': ' + data.reason : '' }));
    } else if (data.done) {
      addMsg('system', '\u2705 ' + t('taskCompleteFor', { agent: agent, result: data.result || data.progress || '' }));
      if (data.task_id) {
        _finalizeTaskBlock(data.task_id, data.task_iteration || data.iterations, '\u2713 done', '#4ecdc4');
      }
    } else if (data.progress) {
      addMsg('system', '\u{1F4CA} Task progress (' + agent + ', iter ' + (data.iterations || '?') + '): ' + data.progress);
      // Finalize current iteration block (next event with new iteration will create a new one)
      if (data.task_id) {
        _finalizeTaskBlock(data.task_id, data.task_iteration || data.iterations, '\u2713 done', '#4ecdc4');
      }
    }
    scrollBottom();
  });

  eventSource.addEventListener('task_stopped', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    if (data.task_id) {
      // Stop all iteration blocks for this task
      for (const [key, block] of Object.entries(_taskBlocks)) {
        if (key.startsWith(data.task_id + '::')) {
          const s = block.summary.querySelector('.task-block-status');
          if (s && s.textContent.includes('running')) {
            s.textContent = data.force ? '\u2718 stopped' : '\u23F8 paused';
            s.style.color = data.force ? '#e94560' : '#f39c12';
            block.el.removeAttribute('open');
          }
        }
      }
      clearStream(data.agent_name || '');
    }
  });

  eventSource.addEventListener('task_msg', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    if (data.task_id && data.from === 'user') {
      const block = _getTaskBlock(data.task_id, data.task_iteration, '');
      if (block) {
        const el = document.createElement('div');
        el.className = 'msg user';
        el.textContent = data.message;
        block.content.appendChild(el);
      } else {
        addMsg('user', data.message || '', {
          task_id: data.task_id,
          source: data.source || { type: 'task', task_id: data.task_id },
        });
      }
    }
  });

  // ── Plan events ──────────────────────────────────────────────
  eventSource.addEventListener('plan_created', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    const plan = data.plan || data;
    const title = plan.title || data.title || '';
    const stepCount = (plan.steps && plan.steps.length) || data.steps || 0;
    const planId = plan.id || plan.plan_id || '';
    const isPendingApproval = (plan.status || '') === 'pending_approval';
    let msgHtml = '\u{1F4CB} Plan created: <strong>' + escapeHtml(title) + '</strong> (' + stepCount + ' steps)';
    if (isPendingApproval && planId) {
      msgHtml += ' &mdash; <button onclick="planAction(\'approve_plan\',' + jsStringArg(planId) + ')" style="margin-left:6px;padding:2px 10px;background:#6c5ce7;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:0.9em">\u2705 Approve</button>';
    }
    // Show step list
    if (plan.steps && plan.steps.length) {
      msgHtml += '<ol style="margin:6px 0 0 16px;padding:0;font-size:0.9em;color:#c0c0d0">';
      for (const s of plan.steps) {
        const desc = typeof s === 'string' ? s : (s.description || s.title || '');
        const icon = (s.status === 'done') ? '\u2713' : '\u25CB';
        msgHtml += '<li style="margin:2px 0">' + icon + ' ' + escapeHtml(desc) + '</li>';
      }
      msgHtml += '</ol>';
    }
    addMsg('system', msgHtml, {html: true});
    // Refresh plans panel if open
    if (document.getElementById('plansPanel').style.display !== 'none') loadPlans();
    scrollBottom();
  });

  eventSource.addEventListener('plan_updated', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    if (document.getElementById('plansPanel').style.display !== 'none') loadPlans();
  });

  eventSource.addEventListener('plan_deleted', (e) => {
    lastSSEActivity = Date.now();
    if (document.getElementById('plansPanel').style.display !== 'none') loadPlans();
  });

  eventSource.addEventListener('relay_status_changed', (e) => {
    lastSSEActivity = Date.now();
    loadResources();
  });

  eventSource.addEventListener('notification', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    const urgencyIcon = data.urgency === 'high' ? '\u{1F534}' : data.urgency === 'low' ? '\u{26AA}' : '\u{1F535}';
    addMsg('system', urgencyIcon + ' ' + (data.message || ''));
    scrollBottom();
    // Browser notification if page is not visible
    if (document.hidden && Notification.permission === 'granted') {
      new Notification('PawFlow Agent', { body: data.message });
    }
  });

  eventSource.addEventListener('ask_user', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    // Display the question prominently with optional buttons
    let html = '<div class="ask-user-box">' + escapeHtml(data.question);
    if (data.options && data.options.length) {
      html += '<div class="ask-user-options">';
      for (const opt of data.options) {
        html += '<button class="btn ask-user-btn" onclick="document.getElementById(\'input\').value=' + jsStringArg(opt) + ';sendMsg()">' + escapeHtml(opt) + '</button>';
      }
      html += '</div>';
    }
    html += '</div>';
    addMsg('system', html);
    scrollBottom();
  });

  eventSource.addEventListener('discard', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    const agentName = data.agent_name || '';
    trackAgentDone(agentName);
    // Remove any streamed tokens for this agent
    const ds = streams[agentName.toLowerCase()];
    if (ds) {
      for (const c of ds.chunks) { if (c && c.parentNode) c.remove(); }
      clearStream(agentName);
    }
    if (Object.keys(activeInteractions).length === 0) {
      sending = false;
      document.getElementById('status').textContent = t('ready');
    }
  });

  eventSource.addEventListener('active_released', (e) => {
    lastSSEActivity = Date.now();
    const data = e.data ? JSON.parse(e.data) : {};
    const agentName = data.agent_name || '';
    if (agentName) _finalizeLiveToolCalls(agentName, '[Stopped]');
    if (agentName) trackAgentDone(agentName);
    if (Object.keys(activeInteractions).length === 0) {
      sending = false;
      document.getElementById('sendBtn').disabled = false;
      document.getElementById('stopBtn').style.display = 'none';
      document.getElementById('status').textContent = t('ready');
      hideTyping();
    }
    if (typeof syncActiveFromServer === 'function') {
      setTimeout(() => syncActiveFromServer(true), 250);
    }
  });

  eventSource.addEventListener('done', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    const doneAgent = data.agent_name || data.source?.name || '';
    // Task done: finalize task block
    if (data.task_id) {
      finalizeThinking(doneAgent, 'done');
      // Show agent's final message inside the task block before closing it
      let taskResp = (data.response || '').replace(/\s*\[NO_PENDING_WORK\]/g, '').replace(/\s*\[RECHECK_IN:\d+\]/g, '').trim();
      taskResp = taskResp.replace(/^\[[^\]]+\]:\s*/, '');
      const block = _getTaskBlock(data.task_id, data.task_iteration, doneAgent);
      if (taskResp) {
        const src = data.source || {type: 'agent', name: doneAgent};
        const msgEl = addMsg('assistant', taskResp, {source: src, msg_id: data.msg_id || ''});
        if (msgEl && block) block.content.appendChild(msgEl);
      }
      _finalizeTaskBlock(data.task_id, data.task_iteration, '\u2713 done', '#4ecdc4');
      trackAgentDone(doneAgent, data.task_id);
      clearStream(doneAgent);
      return;
    }
    // Single update path — setContextUsage enforces the monotonic
    // invariants and mirrors to both caches (active panel + header /
    // Resource Panel).
    if (doneAgent && (data.context_max || 0) > 0
        && typeof setContextUsage === 'function') {
      setContextUsage(doneAgent, {
        conversation_id: data.conversation_id || cid,
        used: data.context_used,
        max: data.context_max,
        pct: data.context_pct,
        updated_at: data.updated_at || data.ts,
      });
    }
    // Finalize any open thinking block for this agent
    finalizeThinking(doneAgent, 'done');
    // Close any pending tool calls owned by THIS agent only — other
    // agents may still be running concurrently.
    const _doneAgentKey = (doneAgent || '').toLowerCase();
    document.querySelectorAll('.tc-bullet.pending').forEach(bullet => {
      const row = bullet.closest('[data-agent]') || bullet.closest('.msg');
      const rowAgent = row && row.dataset ? (row.dataset.agent || '').toLowerCase() : '';
      if (rowAgent && rowAgent !== _doneAgentKey) return;
      bullet.classList.remove('pending');
      bullet.classList.add('done');
      const msgRow = bullet.closest('.msg');
      if (msgRow) {
        msgRow.querySelectorAll('.tc-bg-btn, .tc-kl-btn').forEach(b => b.remove());
      }
      // Safety net: if the tool_call element has a tc_id but no tc-result
      // child, the tool_result SSE event never arrived (lost in transit or
      // dropped by a filter). Attach a placeholder so the user sees the
      // tool call is finalized instead of leaving it visually stuck.
      const tcEl = bullet.closest('[data-tc-id]');
      if (tcEl && !tcEl.querySelector('.tc-result')) {
        const tcId = tcEl.dataset ? (tcEl.dataset.tcId || '') : '';
        if (tcId && _attachPendingToolResult(tcEl, tcId)) return;
        try { _attachToolResult(tcEl, '[result not delivered]'); } catch (e) {}
      }
    });
    trackAgentDone(doneAgent);
    pawflowDebugLog('[SSE done]', doneAgent, data.response ? data.response.substring(0, 100) : '(empty)');
    // Sync message count/offset to prevent load-more overlap.
    if (typeof _noteLiveHistoryAppend === 'function') {
      _noteLiveHistoryAppend(data.message_count, 0);
    }
    // Remove ONLY this agent's streaming chunks (not other agents').
    // Use both the tracked chunks AND a DOM scan, because tool_call
    // events may have cleared the JS references while leaving DOM elements.
    const s = streams[doneAgent.toLowerCase()] || { el: null, text: '', chunks: [] };
    // Strip internal tags that may leak into the response
    let resp = data.response || '';
    resp = resp.replace(/\s*\[NO_PENDING_WORK\]/g, '').replace(/\s*\[RECHECK_IN:\d+\]/g, '').trim();
    resp = resp.replace(/^\[[^\]]+\]:\s*/, '');
    // Strip context-ack echoes from done response too
    if (_CONTEXT_ACKS.has(resp.trim())) resp = '';
    const finalText = resp || s.text.replace(/^\[[^\]]+\]:\s*/, '') || '';
    // Build metadata — these fields ALWAYS exist for every message
    const extra = {};
    extra.msg_id = data.msg_id || '';
    extra.source = data.source || {type: 'agent', name: doneAgent};
    extra.model = data.model || '';
    extra.provider = data.provider || '';
    extra.base_url = data.base_url || '';
    extra.tokens_in = data.tokens_in || 0;
    extra.tokens_out = data.tokens_out || 0;
    extra.cost_usd = data.cost_usd || 0;
    extra.duration_ms = data.duration_ms || 0;
    extra.ts = data.ts;
    // Register ALL msg_ids from this turn (prevents replay duplicates)
    const allIds = data.all_msg_ids || [];
    if (extra.msg_id) allIds.push(extra.msg_id);
    if (s.msg_id) allIds.push(s.msg_id);
    for (const id of allIds) {
      if (id && typeof _seenMsgIds !== 'undefined') _seenMsgIds.add(id);
    }
    // Finalize active streaming element (remove streaming class)
    if (s.el && s.el.parentNode) {
      s.el.classList.remove('streaming');
    }
    // Find existing element or create one if nothing was streamed
    let anyExists = !!s.el;
    let existingEl = s.el;
    if (!anyExists) {
      for (const mid of allIds) {
        if (mid) {
          const found = document.querySelector('#messages [data-msgid="' + mid + '"]');
          if (found) { anyExists = true; existingEl = found; break; }
        }
      }
    }
    if (finalText && !anyExists && !data.force_stopped) {
      addMsg('assistant', finalText, extra);
    }
    if (typeof conversationTTSOnDone === 'function') {
      try { conversationTTSOnDone(Object.assign({}, data, extra, {response: finalText})); } catch (_ttsErr) {}
    }
    // Update metadata on existing element (replace estimated with real values)
    if (existingEl) {
      const meta = buildMetaLine(extra);
      if (meta) {
        const existMeta = existingEl.querySelector('.msg-meta');
        if (existMeta) existMeta.outerHTML = meta;
        else existingEl.insertAdjacentHTML('beforeend', meta);
      }
    }
    clearStream(doneAgent);
    scrollBottom();

    if (data.continuing) {
      // Intermediate round — agent will continue autonomously
      document.getElementById('status').textContent = t('continuing');
    } else {
      // Final response — ensure active panel is cleaned up
      sending = false;
      document.getElementById('sendBtn').disabled = false;
      document.getElementById('stopBtn').style.display = 'none';
      document.getElementById('status').textContent = t('ready');
      // Force-clean all active interactions for this agent
      if (doneAgent) {
        trackAgentDone(doneAgent);
      } else {
        // No agent name — clean everything
        activeInteractions = {};
        updateActivePanel();
      }
      if (typeof syncActiveFromServer === 'function') {
        setTimeout(() => syncActiveFromServer(true), 250);
        setTimeout(() => syncActiveFromServer(true), 1500);
      }
      if (Object.keys(activeInteractions).length === 0 && activeTimer) {
        clearInterval(activeTimer); activeTimer = null;
      }
    }
    // Refresh conversation list
    loadConversations();
    // Don't close SSE — keep listening for timer-triggered events
  });

  // Auto-generated conversation title
  eventSource.addEventListener('conversation_title', (e) => {
    const data = JSON.parse(e.data);
    const cid = data.conversation_id || conversationId;
    const title = data.title || '';
    if (!title) return;
    // Update sidebar entry in-place without full reload
    const convEl = document.querySelector('.conv-item[data-cid="' + cid + '"] .conv-preview');
    if (convEl) {
      // Preserve status dot if present
      const dot = convEl.querySelector('.conv-status');
      convEl.textContent = '';
      if (dot) convEl.appendChild(dot);
      convEl.appendChild(document.createTextNode(title));
    }
  });

  eventSource.addEventListener('cancelled', (e) => {
    lastSSEActivity = Date.now();
    const cancelData = e.data ? JSON.parse(e.data) : {};
    const cancelAgent = cancelData.agent_name || 'all';
    _finalizeLiveToolCalls(cancelAgent === 'all' ? '' : cancelAgent, '[Cancelled]');
    if (cancelAgent === 'all') {
      // Don't clear activeInteractions — server is source of truth via syncActive
      syncActiveFromServer();
      document.querySelectorAll('#messages .thinking-block').forEach(el => el.remove());
    } else {
      trackAgentDone(cancelAgent);
    }
    // Finalize streaming chunks instead of removing them (preserve visible text)
    if (cancelAgent === 'all') {
      for (const a of Object.keys(streams)) {
        const s = streams[a];
        if (s.el && s.el.parentNode) s.el.classList.remove('streaming');
      }
      clearAllStreamsKeepDOM();
    } else {
      const cs = streams[cancelAgent.toLowerCase()];
      if (cs) {
        if (cs.el && cs.el.parentNode) cs.el.classList.remove('streaming');
        clearStream(cancelAgent);
      }
    }
    addMsg('system', cancelAgent !== 'all' ? '[' + displayAgentName(cancelAgent) + '] ' + t('cancelled') : t('cancelled'));
    scrollBottom();
    sending = false;
    document.getElementById('sendBtn').disabled = false;
    document.getElementById('stopBtn').style.display = 'none';
    document.getElementById('status').textContent = t('ready');
  });

  // --- BTW (side-channel) events ---
  let btwElements = {};  // agent_name → streaming element
  let btwTexts = {};

  eventSource.addEventListener('btw_thinking', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    const agent = data.agent_name || '';
    const bKey = agent.toLowerCase();
    const dName = displayAgentName(agent);
    const el = addMsg('btw', '');
    el.innerHTML = makeTimeHtml() + '<span style="color:#60a5fa;font-size:11px;">[' + escapeHtml(dName) + ' \u00b7 btw] </span><em style="color:#888;">thinking...</em>';
    btwElements[bKey] = el;
    btwTexts[bKey] = '';
    scrollBottom();
  });

  eventSource.addEventListener('btw_token', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    const agent = data.agent_name || '';
    const bKey = agent.toLowerCase();
    const dName = displayAgentName(agent);
    btwTexts[bKey] = (btwTexts[bKey] || '') + data.text;
    const el = btwElements[bKey];
    if (el) {
      el.innerHTML = makeTimeHtml() + '<span style="color:#60a5fa;font-size:11px;">[' + escapeHtml(dName) + ' \u00b7 btw] </span>' + renderMarkdown(btwTexts[bKey]);
      scrollBottom();
    }
  });

  eventSource.addEventListener('btw_done', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    const agent = data.agent_name || '';
    const bKey = agent.toLowerCase();
    const dName = displayAgentName(agent);
    if (data.error) {
      const el = btwElements[bKey];
      if (el) { el.innerHTML = makeTimeHtml() + '<span style="color:#f87171;font-size:11px;">[' + escapeHtml(dName) + ' \u00b7 btw] Error: ' + escapeHtml(data.error) + '</span>'; }
      else { addMsg('error', '[' + dName + ' \u00b7 btw] ' + data.error); }
    } else if (data.response && !btwTexts[bKey]) {
      // Non-streaming fallback
      const el = btwElements[bKey] || addMsg('btw', '');
      el.innerHTML = makeTimeHtml() + '<span style="color:#60a5fa;font-size:11px;">[' + escapeHtml(dName) + ' \u00b7 btw] </span>' + renderMarkdown(data.response);
    }
    delete btwElements[bKey];
    delete btwTexts[bKey];
    scrollBottom();
  });

  eventSource.addEventListener('interrupting', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    addMsg('system', t('interruptingAgentImmediateResponse', { agent: displayAgentName(data.agent) }));
    scrollBottom();
  });

  // NOTE: duplicate 'discard' listener removed — handled by the first one above

  eventSource.addEventListener('exec_approval_request', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    showExecApprovalDialog(data);
  });

  eventSource.addEventListener('exec_output', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    appendExecOutput(data);
  });

  eventSource.addEventListener('tool_approval_request', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    showToolApprovalDialog(data);
  });

  eventSource.addEventListener('command_result', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    // Feed into RxJS bus — all subscribers (action$) will receive this
    if (typeof _pushCommandResult === 'function') _pushCommandResult(data);
  });

  eventSource.addEventListener('vnc_login_ready', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    // `cli` is one of 'claude' | 'codex' | 'gemini' | 'rclone' — picks the right
    // server status/cleanup action namespace inside the dialog.
    _openVncLoginDialog(data.session_id, data.service_id, data.token || '', null, data.cli || 'claude', data.scope || '');
  });

  eventSource.addEventListener('notification', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    showNotification(data);
  });

  eventSource.addEventListener('error_event', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    addMsg('error', data.message || t('unknownError'));
    // Error could be from any agent — clear the agent's stream + active interaction
    const errAgent = data.agent_name || '';
    _finalizeLiveToolCalls(errAgent, '[Error]');
    clearStream(errAgent);
    if (errAgent) {
      trackAgentDone(errAgent);
    } else {
      activeInteractions = {};
      updateActivePanel();
    }
    sending = false;
    document.getElementById('sendBtn').disabled = false;
    document.getElementById('stopBtn').style.display = 'none';
    document.getElementById('status').textContent = t('error');
  });

  eventSource.addEventListener('agent_response', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    const extra = {};
    if (data.source) extra.source = data.source;
    if (data.model) extra.model = data.model;
    if (data.provider) extra.provider = data.provider;
    if (data.base_url) extra.base_url = data.base_url;
    if (data.tokens_in || data.tokens_out) { extra.tokens_in = data.tokens_in || 0; extra.tokens_out = data.tokens_out || 0; }
    if (data.duration_ms) extra.duration_ms = data.duration_ms;
    extra.ts = data.ts;
    addMsg('assistant', data.response || '', extra);
    scrollBottom();
  });

  eventSource.addEventListener('broadcast_done', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    if (typeof _noteLiveHistoryAppend === 'function') {
      _noteLiveHistoryAppend(data.message_count, 0);
    }
    sending = false;
    document.getElementById('sendBtn').disabled = false;
    document.getElementById('stopBtn').style.display = 'none';
    document.getElementById('status').textContent = t('ready');
    addMsg('system', `Broadcast complete — ${data.agent_count} agent(s) responded.`);
    scrollBottom();
    loadConversations();
  });

  eventSource.addEventListener('thought_scheduled', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    addMsg('system', t('thoughtScheduled', { agent: displayAgentName(data.agent), delay: data.delay || '?' }));
    scrollBottom();
  });

  eventSource.addEventListener('thought_firing', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    trackAgentStart(data.agent || '');
    addMsg('system', t('thoughtFiring', { agent: displayAgentName(data.agent) }));
  });

  eventSource.addEventListener('theme', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    if (typeof applyThemeCss === 'function') applyThemeCss(data.css || '');
  });

  let sseHadError = false;  // track any error on this EventSource
  let sseEverConnected = false;  // distinguish reconnects from initial connect hiccups

  eventSource.onerror = (err) => {
    const state = eventSource ? eventSource.readyState : EventSource.CLOSED;
    console.warn('[SSE] error, readyState:', state, err);
    sseHadError = true;
    document.getElementById('status').textContent = t('reconnecting');
    // Do not rely on the browser's opaque EventSource retry after the server
    // intentionally closes a long-lived stream. Own the reconnect so live
    // rendering stays on the SSE channel instead of falling back to polling.
    if (eventSource) { try { eventSource.close(); } catch (_) {} }
    eventSource = null;
    _scheduleSSEReconnect(cid);
  };

  eventSource.onopen = () => {
    pawflowDebugLog('[SSE] connected for', cid, sseHadError ? '(reconnect)' : '(initial)');
    if (_sseOnReadyCallback) { _sseOnReadyCallback(); _sseOnReadyCallback = null; }
    const wasDisconnected = sseEverConnected && sseHadError;
    sseEverConnected = true;
    sseRetryCount = 0;
    sseHadError = false;
    lastSSEActivity = Date.now();  // prime the watchdog
    // SSE health timer always on — protects paths that open a conversation
    // without explicitly arming the watchdog (direct URL, refresh).
    startSSEHealthTimer();
    if (wasDisconnected) {
      pawflowDebugLog('[SSE] reconnected; continuing with live SSE events');
      syncActiveFromServer();
    }
  };

  // Server emits `sse_ping` alongside the comment keepalive every ~15s.
  // The comment form is invisible to JS (SSE spec), the typed ping lets
  // us watchdog a silently half-open socket where EventSource never fires
  // onerror (laptop sleep, NAT eviction, proxy idle-kill).
  eventSource.addEventListener('sse_ping', () => {
    lastSSEActivity = Date.now();
  });

  eventSource.addEventListener('sse_reconnect', (e) => {
    lastSSEActivity = Date.now();
    pawflowDebugLog('[SSE] server requested reconnect', e.data || '');
    if (eventSource) { try { eventSource.close(); } catch (_) {} }
    eventSource = null;
    _scheduleSSEReconnect(cid);
  });
}

function _forceSSEReconnect(cid, opts) {
  if (!cid || cid !== conversationId) return;
  if (eventSource) { try { eventSource.close(); } catch (_) {} }
  eventSource = null;
  lastSSEActivity = 0;
  if (sseReconnectTimer) { clearTimeout(sseReconnectTimer); sseReconnectTimer = null; }
  connectSSE(cid, null, opts || { noReplay: true });
}

function _waitForSSEOpen(timeoutMs) {
  if (!eventSource) return Promise.resolve(false);
  if (eventSource.readyState === EventSource.OPEN) return Promise.resolve(true);
  const es = eventSource;
  return new Promise(resolve => {
    let done = false;
    const finish = ok => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      resolve(!!ok && es === eventSource && eventSource.readyState === EventSource.OPEN);
    };
    const timer = setTimeout(() => finish(false), timeoutMs || 2000);
    es.addEventListener('open', () => finish(true), { once: true });
    es.addEventListener('error', () => finish(false), { once: true });
  });
}

function _ensureSSEBeforeUserAction() {
  if (!conversationId) return Promise.resolve(false);
  if (_sseIsHealthy()) return Promise.resolve(true);
  if (eventSource && eventSource.readyState === EventSource.CONNECTING
      && Date.now() - _sseCreatedAt < 3000) {
    return _waitForSSEOpen(2000);
  }
  console.warn('[SSE] user action while stream is stale — reconnecting before send');
  _forceSSEReconnect(conversationId, {});
  return _waitForSSEOpen(2000);
}

// SSE liveness watchdog. Pings arrive every ~15s; if we haven't seen one
// in 45s the stream is silently dead even if readyState still says OPEN.
// Close + reconnect forcefully; event rendering must stay SSE-only.
var _sseWatchdogTimer = null;
function _startSSEWatchdog() {
  if (_sseWatchdogTimer) clearInterval(_sseWatchdogTimer);
  _sseWatchdogTimer = setInterval(() => {
    if (!eventSource || !conversationId) return;
    if (!lastSSEActivity) return;  // not yet connected
    const silentFor = Date.now() - lastSSEActivity;
    if (silentFor > 45000) {
      console.warn('[SSE] watchdog: no activity for', silentFor, 'ms — forcing reconnect');
      _forceSSEReconnect(conversationId, { noReplay: true });
    }
  }, 10000);
}
_startSSEWatchdog();

// ── Command result dispatchers ──────────────────────────────────
// Old _dispatchCommandResult and _renderLoadedHistory removed —
// all dispatch is now via RxJS action$ subscriptions in each module.

// Track the server's process-start epoch. Every /api/agent + /api/ui
// ack carries server_start_time; if it moves we know the backend was
// restarted while the browser wasn't looking — its EventSource may
// still appear OPEN (half-open TCP) but the new process has no
// subscribers for this conversation, so events get buffered and the
// UI never sees them. Force a clean reconnect without replaying stale
// buffered events into the chat renderer.
var _lastServerStartTime = null;
var _lastRestartReconnectAt = 0;
function _checkServerRestart(data) {
  // Only reconnect on an explicit server bounce (start_time changed).
  // The earlier "readyState !== OPEN → reconnect" heuristic ran every
  // time the SSE was legitimately mid-CONNECTING (e.g. right after a
  // reconnect) and re-triggered itself on every subsequent ack → the
  // stream never stabilised and responses disappeared. Keep this
  // strictly gated on the start_time signal; the scheduled backoff in
  // _scheduleSSEReconnect already handles truly dead sockets.
  if (!data || typeof data.server_start_time !== 'number') return;
  const prev = _lastServerStartTime;
  _lastServerStartTime = data.server_start_time;
  if (prev === null || prev === data.server_start_time) return;
  // Debounce: if we just reconnected for this reason, don't stack
  // another reconnect for every response that's racing in behind.
  const now = Date.now();
  if (now - _lastRestartReconnectAt < 3000) return;
  _lastRestartReconnectAt = now;
  console.warn('[SSE] server restart detected (start_time ' + prev
    + ' → ' + data.server_start_time + ') — reconnecting SSE');
  if (typeof _reconnectUIActionSSE === 'function') {
    try { _reconnectUIActionSSE(); } catch (_) {}
  }
  _forceSSEReconnect(conversationId, { noReplay: true });
}

function _scheduleSSEReconnect(cid) {
  if (sseReconnectTimer) clearTimeout(sseReconnectTimer);
  // Exponential backoff: 1s, 2s, 4s, 8s, 16s, 30s, 60s
  const delay = Math.min(1000 * Math.pow(2, sseRetryCount), 60000);
  sseRetryCount++;
  pawflowDebugLog('[SSE] reconnecting in', delay, 'ms (attempt', sseRetryCount, ')');
  sseReconnectTimer = setTimeout(() => {
    sseReconnectTimer = null;
    if (!cid || cid !== conversationId) return;  // conversation changed, skip
    connectSSE(cid);
  }, delay);
}

// ── Idle-safe SSE reconnection ─────────────────────────────────────
function _sseIsHealthy() {
  if (!eventSource || eventSource.readyState !== EventSource.OPEN) return false;
  if (!lastSSEActivity) return false;
  return Date.now() - lastSSEActivity <= 45000;
}

function startSSEHealthTimer() {
  stopSSEHealthTimer();
  sseHealthTimer = setInterval(() => {
    if (!conversationId) return;
    if (typeof document !== 'undefined' && document.hidden && _sseIsHealthy()) return;
    if (_sseIsHealthy()) return;
    _forceSSEReconnect(conversationId, { noReplay: true });
  }, 15000);
  // Resource refresh is a fallback hydration path, not a hot idle loop.
  if (!resourcesTimer) {
    resourcesTimer = setInterval(() => {
      if (!conversationId) return;
      if (typeof document !== 'undefined' && document.hidden) return;
      loadResources();
    }, 120000);
  }
}
function stopSSEHealthTimer() {
  if (sseHealthTimer) { clearInterval(sseHealthTimer); sseHealthTimer = null; }
  if (resourcesTimer) { clearInterval(resourcesTimer); resourcesTimer = null; }
}

async function showPrompts() {
  try {
    const data = await rxjs.firstValueFrom(action$('list_skills'));
    const skills = data.skills || [];
    if (!skills.length) { addMsg('system', t('noSkillsAvailableCreateHint')); return; }
    let overlay = document.getElementById('promptOverlay');
    if (overlay) overlay.remove();
    overlay = document.createElement('div');
    overlay.id = 'promptOverlay';
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;z-index:9999';
    let html = '<div style="background:#1a1a2e;border:1px solid #0f3460;border-radius:12px;max-width:500px;width:90%;max-height:70vh;overflow-y:auto;padding:20px">';
    html += '<h3 style="margin:0 0 12px;color:#e94560">' + escapeHtml(t('skills')) + '</h3>';
    for (const s of skills) {
      html += '<div class="prompt-item" data-name="' + escapeHtml(s.name) + '" style="padding:10px;margin:4px 0;background:#16213e;border-radius:8px;cursor:pointer;border:1px solid transparent" onmouseenter="this.style.borderColor=\'#e94560\'" onmouseleave="this.style.borderColor=\'transparent\'">';
      html += '<div style="font-weight:600;color:#fff">' + escapeHtml(s.name) + '</div>';
      if (s.description) html += '<span style="font-size:11px;color:#aaa">' + escapeHtml(s.description) + '</span>';
      if (s.preview) html += '<div style="font-size:11px;color:#666;margin-top:4px">' + escapeHtml(s.preview) + '...</div>';
      html += '</div>';
    }
    html += '<button onclick="document.getElementById(\'promptOverlay\').remove()" style="margin-top:12px;padding:6px 16px;background:#0f3460;color:#fff;border:none;border-radius:6px;cursor:pointer">' + escapeHtml(t('close')) + '</button>';
    html += '</div>';
    overlay.innerHTML = html;
    overlay.querySelectorAll('.prompt-item').forEach(item => {
      item.addEventListener('click', async () => {
        const name = item.dataset.name;
        try {
          const d2 = await rxjs.firstValueFrom(action$('get_skill', { name: name }));
          if (d2.prompt) {
            document.getElementById('input').value = d2.prompt;
            document.getElementById('input').focus();
          }
        } catch(e) { addMsg('error', t('skillLoadFailed', { error: e.message })); }
        overlay.remove();
      });
    });
    document.body.appendChild(overlay);
  } catch (e) { addMsg('error', t('promptsListFailed', { error: e.message })); }
}
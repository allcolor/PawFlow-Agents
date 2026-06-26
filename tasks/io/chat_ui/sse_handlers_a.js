// Auto-split from sse.js: SSE event-handler registrations (group A).
// Called by connectSSE() in sse.js after the EventSource is created.
function _sseWireA() {

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
          tag: 'pawflow-notif-' + (_sseCid || ''),
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
        conversation_id: data.conversation_id || _sseCid,
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
}

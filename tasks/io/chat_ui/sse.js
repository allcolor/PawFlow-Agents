// Connect SSE for a conversation
function connectSSE(cid) {
  if (eventSource) eventSource.close();
  if (sseReconnectTimer) { clearTimeout(sseReconnectTimer); sseReconnectTimer = null; }
  startActiveSync();
  sseRetryCount = 0;  // reset so onopen doesn't think we're reconnecting
  const token = getToken();
  const url = SSE_URL + '?conversation_id=' + encodeURIComponent(cid)
    + (token ? '&token=' + encodeURIComponent(token) : '');
  eventSource = new EventSource(url);

  // ── Task block grouping ─────────────────────────────────────────
  const _taskBlocks = {};

  function _getTaskBlock(taskId, iteration, agentName) {
    if (!taskId) return null;
    const blockKey = taskId + '::iter' + (iteration || 0);
    if (_taskBlocks[blockKey]) return _taskBlocks[blockKey];
    console.warn('[TASK BLOCK CREATE]', blockKey, 'iteration=', iteration, 'existing keys=', Object.keys(_taskBlocks));
    console.trace('[TASK BLOCK CREATE STACK]');
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

  function _taskBlockAppend(taskId, childEl) {
    const block = _taskBlocks[taskId];
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
    if (agentName) _cancelledAgents.delete(agentName.toLowerCase());
    trackAgentStart(agentName);
    const wait = data.waiting_seconds || 0;
    const verb = randomVerb();
    let status = wait > 5 ? verb + '... (' + wait + 's)' : (data.round > 1 ? verb + '... (round ' + data.round + ')' : verb + '...');
    document.getElementById('status').textContent = status;
  });

  // ── Extended thinking (Anthropic) ──
  let thinkingElements = {};  // agentKey → {el, text, startTime}
  eventSource.addEventListener('thinking_content', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    const agent = data.agent_name || '';
    const aKey = agentKey(agent);
    if (!thinkingElements[aKey]) {
      // Create collapsible details element
      const details = document.createElement('details');
      details.className = 'msg thinking-block';
      details.setAttribute('open', '');
      details.style.cssText = 'margin:4px 0;border-left:3px solid #6b7280;padding:4px 8px;opacity:0.7;';
      const summary = document.createElement('summary');
      summary.style.cssText = 'cursor:pointer;font-size:12px;color:#9ca3af;font-style:italic;user-select:none;';
      summary.textContent = 'Thinking...';
      details.appendChild(summary);
      const content = document.createElement('div');
      content.style.cssText = 'font-size:12px;color:#9ca3af;font-style:italic;white-space:pre-wrap;max-height:300px;overflow-y:auto;';
      details.appendChild(content);
      if (data.task_id) {
        const tb = _getTaskBlock(data.task_id, data.task_iteration, agent);
        if (tb) { tb.content.appendChild(details); scrollBottom(); }
        else { document.getElementById('messages').appendChild(details); }
      } else {
        const _msgContainer = document.getElementById('messages');
        const _typingEl = document.getElementById('typing');
        if (_typingEl) _msgContainer.insertBefore(details, _typingEl);
        else _msgContainer.appendChild(details);
      }
      thinkingElements[aKey] = {el: details, content: content, summary: summary, text: '', startTime: Date.now()};
      scrollBottom();
    }
    const te = thinkingElements[aKey];
    te.text += data.text;
    te.content.textContent = te.text;
    scrollBottom();
  });

  // Finalize thinking block when tokens start arriving (thinking is done)
  function finalizeThinking(agentName) {
    const aKey = agentKey(agentName || '');
    const te = thinkingElements[aKey];
    if (te) {
      const elapsed = (Date.now() - te.startTime) / 1000;
      // Remove empty thinking blocks — keep all that have content
      if (!te.text.trim()) {
        te.el.remove();
      } else {
        te.summary.textContent = 'Thought for ' + elapsed.toFixed(1) + 's';
        te.el.removeAttribute('open');  // collapse
      }
      delete thinkingElements[aKey];
    }
  }

  eventSource.addEventListener('token', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    const agent = data.agent_name || streamingAgent || '';
    // Finalize thinking block when first text token arrives
    finalizeThinking(agent);
    streamingAgent = agent;  // legacy global
    const s = getStream(agent);
    s.text += data.text;
    s.msg_id = data.msg_id || s.msg_id || '';  // track msg_id from tokens
    streamingText = s.text;  // legacy global
    // Always have a source — every response comes from an agent
    const src = data.source || {type: 'agent', name: agent};
    if (!s.el) {
      s.el = addMsg('assistant', '', {source: src, msg_id: s.msg_id});
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
      streamingEl = s.el;  // legacy global
      streamingChunks = s.chunks;
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
          + '<button onclick="setReplyTo(this)" title="Reply">\u21A9</button>'
          + '<button onclick="copyMsg(this)" title="Copy">\uD83D\uDCCB</button>'
          + '<button onclick="deleteMsg(this)" title="Delete">\uD83D\uDDD1</button>'
          + '</span>');
      if (meta) s.el.appendChild(meta);
    }
    contentEl.innerHTML = badge + renderMarkdown(displayText);
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
    if (!data.msg_id) return;
    // Register msg_id to prevent poll/replay duplicates
    if (typeof _seenMsgIds !== 'undefined') _seenMsgIds.add(data.msg_id);
    // Find the element by data-msgid and update metadata (replace if exists)
    const el = document.querySelector('#messages [data-msgid="' + data.msg_id + '"]');
    if (el) {
      const meta = buildMetaLine(data);
      if (meta) {
        const existing = el.querySelector('.msg-meta');
        if (existing) existing.outerHTML = meta;
        else el.insertAdjacentHTML('beforeend', meta);
      }
    }
  });

  // Narration: separate from token stream — not persisted, ephemeral display
  eventSource.addEventListener('narration', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    const agent = data.agent_name || '';
    const src = data.source || {type: 'agent', name: agent};
    const badge = sourceBadge(src);
    const el = document.createElement('div');
    el.className = 'msg narration';
    el.dataset.finalizedAgent = agent.toLowerCase();
    el.innerHTML = makeTimeHtml() + badge + '<em>' + escapeHtml(data.text || '') + '</em>';
    // Route into task block if this is a task event
    if (data.task_id) {
      const tb = _getTaskBlock(data.task_id, data.task_iteration, agent);
      if (tb) { tb.content.appendChild(el); scrollBottom(); }
      else document.getElementById('messages').appendChild(el);
    } else {
      const _narContainer = document.getElementById('messages');
      const _narTyping = document.getElementById('typing');
      if (_narTyping) _narContainer.insertBefore(el, _narTyping);
      else _narContainer.appendChild(el);
    }
    scrollBottom();
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
    // Multi-tour: show compact progress message in chat when iteration advances
    if (data.iteration > 1 || data.round > 1) {
      const lastShown = activeInteractions[aKey] ? activeInteractions[aKey]._lastShownIter : undefined;
      if (data.iteration !== lastShown) {
        addMsg('system-compact', t('iterProgress', {
          agent: displayAgentName(agentName), i: data.iteration, r: data.round,
          mr: data.max_rounds, t: data.total_tools
        }));
        if (activeInteractions[aKey]) {
          activeInteractions[aKey]._lastShownIter = data.iteration;
        }
      }
    }
  });

  // FlowFile incoming indicator
  eventSource.addEventListener('flowfile_in', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    const parts = [];
    if (data.agent) parts.push(data.agent);
    if (data.reason) parts.push(data.reason);
    else if (data.path) parts.push(data.source + ' ' + data.path);
    if (data.size > 0) parts.push((data.size / 1024).toFixed(1) + ' KB');
    if (parts.length) {
      addMsg('system-compact', '\u25b6 ' + parts.join(' \u00b7 '));
      scrollBottom();
    }
  });

  // Sub-agent visibility
  eventSource.addEventListener('sub_agent_start', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    trackAgentStart(data.agent_name, data.message ? data.message.substring(0, 40) : '');
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

  eventSource.addEventListener('sub_agent_tool', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    const agentName = data.agent_name || 'sub-agent';
    trackAgentTool(agentName, data.tool);
  });

  eventSource.addEventListener('sub_agent_done', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    const agent = data.agent_name || 'sub-agent';
    trackAgentDone(agent);
    const svcInfo = data.llm_service ? ' via ' + data.llm_service : '';
    const srcInfo = data.source_agent ? displayAgentName(data.source_agent) + ' \u2192 ' : '';
    const header = srcInfo + displayAgentName(agent) + svcInfo;
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
      addMsg('agent-result', 'Error: ' + data.error, agent);
    }
    scrollBottom();
  });

  // Track cancelled agents — suppress their events until done/new message
  const _cancelledAgents = new Set();

  eventSource.addEventListener('tool_call', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    // Suppress events from cancelled agents (but NOT claude-code — its events
    // come from the active subprocess, not a stale agent loop iteration)
    if (_cancelledAgents.has((data.agent_name || '').toLowerCase()) && data.via !== 'claude-code') return;
    // Finalize thinking block before showing tool call
    finalizeThinking(data.agent_name || '');
    if (data.message_count) serverMsgCount = data.message_count;
    console.log('[SSE] tool_call received:', data.tool, data.agent_name, data.llm_service, JSON.stringify(data.arguments || {}).substring(0, 200));
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
    trackAgentTool(tcAgent, data.tool);
    // Single rendering path: addMsg handles ALL tool_call rendering
    const tcExtra = {
      tool_name: data.tool,
      tool_args: data.arguments || {},
      tc_id: data.tc_id || '',
      source: data.source || {type: 'agent', name: tcAgent, llm_service: data.llm_service || ''},
      agent_name: tcAgent,
      llm_service: data.llm_service || '',
      ts: data.ts,
      live: true,
    };
    if (data.parent_tc_id) tcExtra.parent_tc_id = data.parent_tc_id;
    const tcEl = addMsg('tool_call', data.tool, tcExtra);
    // Move into task block if this is a task event
    if (data.task_id && tcEl && !data.parent_tc_id) {
      const tb = _getTaskBlock(data.task_id, data.task_iteration, tcAgent);
      if (tb) { tb.content.appendChild(tcEl); scrollBottom(); }
    }
    // Group under parent agent tool_call if this is a sub-agent tool
    if (data.parent_tc_id && tcEl) {
      const parentEl = document.querySelector('[data-tc-id="' + data.parent_tc_id + '"]');
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
    if (!data.task_id) document.getElementById('status').textContent = t('usingTool', {tool: data.tool});
  });

  eventSource.addEventListener('tool_result', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    if (_cancelledAgents.has((data.agent_name || '').toLowerCase()) && data.via !== 'claude-code') return;
    if (data.agent_name) trackAgentToolDone(data.agent_name, data.tool);
    // Try to attach to matching tool_call element
    const tcId = data.tc_id || '';
    if (tcId) {
      const tcEl = document.querySelector('[data-tc-id="' + tcId + '"]');
      if (tcEl) {
        _attachToolResult(tcEl, data.result || '');
        return;
      }
    }
    // Fallback: standalone element
    const trEl = addMsg('tool_result', data.result || '', {
      tool_name: data.tool,
      tool: data.tool,
      source: data.source || {type: 'agent', name: data.agent_name || '', llm_service: data.llm_service || ''},
      agent_name: data.agent_name || '',
      llm_service: data.llm_service || '',
      path: data.path || '',
      ts: data.ts,
      tc_id: tcId,
    });
    // Route into task block if this is a task event
    if (data.task_id && trEl) {
      const tb = _getTaskBlock(data.task_id, data.task_iteration, data.agent_name || '');
      if (tb) { tb.content.appendChild(trEl); scrollBottom(); }
    }
  });

  eventSource.addEventListener('bg_task_update', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    const tcId = data.tc_id || '';
    if (tcId) {
      const tcEl = document.querySelector('[data-tc-id="' + tcId + '"]');
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
      var opLabel = data.detail || 'compact';
      showContextOp(opLabel.charAt(0).toUpperCase() + opLabel.slice(1) + ' ' + (data.agent || '') + '...');
    } else if (data.stage === 'chunking' || data.stage === 'summarizing') {
      showContextOp((data.detail || data.stage) + '...');
    } else if (data.stage === 'done') {
      hideContextOp();
      const agent = data.agent || 'shared';
      const before = data.before !== undefined ? data.before : '?';
      const after = data.after !== undefined ? data.after : '?';
      const tokAfter = data.tokens_after !== undefined ? data.tokens_after : '?';
      addMsg('system', agent + ': ' + before + ' messages \u2192 ' + after + ' messages (~' + tokAfter + ' tokens)');
    } else if (data.stage === 'error') {
      hideContextOp();
      addMsg('error', 'Context operation failed: ' + data.error);
    }
  });

  eventSource.addEventListener('task_progress', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    const agent = displayAgentName(data.agent || '?');
    if (data.stage === 'assigned') {
      const v = data.verifier ? ' (verifier: ' + displayAgentName(data.verifier) + ')' : '';
      addMsg('system', '\u{1F4CB} Task assigned to ' + agent + v + ': ' + (data.task || '').substring(0, 150));
    } else if (data.stage === 'verified') {
      const icon = data.approved ? '\u2705' : '\u274C';
      const verifier = displayAgentName(data.verifier || '?');
      addMsg('system', icon + ' Task for ' + agent + (data.approved ? ' approved' : ' rejected') + ' by ' + verifier + (data.reason ? ': ' + data.reason : ''));
    } else if (data.done) {
      addMsg('system', '\u2705 Task complete (' + agent + '): ' + (data.result || data.progress || ''));
      if (data.task_id) {
        _finalizeTaskBlock(data.task_id, data.iterations, '\u2713 done', '#4ecdc4');
      }
    } else if (data.progress) {
      addMsg('system', '\u{1F4CA} Task progress (' + agent + ', iter ' + (data.iterations || '?') + '): ' + data.progress);
      // Finalize current iteration block (next event with new iteration will create a new one)
      if (data.task_id) {
        _finalizeTaskBlock(data.task_id, data.iterations, '\u2713 done', '#4ecdc4');
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
    let msgHtml = '\u{1F4CB} Plan created: <strong>' + title + '</strong> (' + stepCount + ' steps)';
    if (isPendingApproval && planId) {
      msgHtml += ' &mdash; <button onclick="planAction(\'approve_plan\',\'' + planId + '\')" style="margin-left:6px;padding:2px 10px;background:#6c5ce7;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:0.9em">\u2705 Approve</button>';
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
        html += '<button class="btn ask-user-btn" onclick="document.getElementById(\'input\').value=\'' + opt.replace(/'/g, "\\'") + '\';sendMsg()">' + escapeHtml(opt) + '</button>';
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

  eventSource.addEventListener('done', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    const doneAgent = data.agent_name || data.source?.name || '';
    // Task done: finalize task block
    if (data.task_id) {
      finalizeThinking(doneAgent);
      // Show agent's final message inside the task block before closing it
      let taskResp = (data.response || '').replace(/\s*\[NO_PENDING_WORK\]/g, '').replace(/\s*\[RECHECK_IN:\d+\]/g, '').trim();
      taskResp = taskResp.replace(/^\[[^\]]+\]:\s*/, '');
      const block = _getTaskBlock(data.task_id, data.task_iteration, doneAgent);
      if (taskResp && block) {
        const src = data.source || {type: 'agent', name: doneAgent};
        const msgEl = addMsg('assistant', taskResp, {source: src, msg_id: data.msg_id || ''});
        if (msgEl) block.content.appendChild(msgEl);
      }
      _finalizeTaskBlock(data.task_id, data.task_iteration, '\u2713 done', '#4ecdc4');
      clearStream(doneAgent);
      return;
    }
    _cancelledAgents.delete(doneAgent.toLowerCase());  // allow new events for next turn
    // Finalize any open thinking block for this agent
    finalizeThinking(doneAgent);
    trackAgentDone(doneAgent);
    console.log('[SSE done]', doneAgent, data.response ? data.response.substring(0, 100) : '(empty)');
    // Sync message count to prevent poll from re-fetching these messages
    if (data.message_count) serverMsgCount = data.message_count;
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
    // Register ALL msg_ids from this turn (prevents poll/replay duplicates)
    const allIds = data.all_msg_ids || [];
    if (extra.msg_id) allIds.push(extra.msg_id);
    if (s.msg_id) allIds.push(s.msg_id);
    for (const id of allIds) {
      if (id && typeof _seenMsgIds !== 'undefined') _seenMsgIds.add(id);
    }
    // Clean up narrations
    const agentLower = doneAgent.toLowerCase();
    document.querySelectorAll('#messages .narration').forEach(el => {
      if (el.dataset.finalizedAgent === agentLower) el.remove();
    });
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
    // Suppress subsequent tool events from this agent
    if (cancelAgent === 'all') {
      Object.keys(streams).forEach(k => _cancelledAgents.add(k));
    } else {
      _cancelledAgents.add(cancelAgent.toLowerCase());
    }
    if (cancelAgent === 'all') {
      // Don't clear activeInteractions — server is source of truth via syncActive
      syncActiveFromServer();
      // Clean up all narrations + thinking
      document.querySelectorAll('#messages .narration').forEach(el => el.remove());
      document.querySelectorAll('#messages .thinking-block').forEach(el => el.remove());
    } else {
      trackAgentDone(cancelAgent);
      // Clean up this agent's narrations
      document.querySelectorAll('#messages .narration').forEach(el => {
        if (el.dataset.finalizedAgent === cancelAgent.toLowerCase()) el.remove();
      });
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
    addMsg('system', 'Interrupting ' + displayAgentName(data.agent) + ' — requesting immediate response...');
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
    _openVncLoginDialog(data.session_id, data.service_id, null);
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
    if (data.message_count) serverMsgCount = data.message_count;
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
    let existing = document.getElementById('custom-theme');
    if (!existing) {
      existing = document.createElement('style');
      existing.id = 'custom-theme';
      document.head.appendChild(existing);
    }
    existing.textContent = data.css || '';
  });

  let sseHadError = false;  // track any error on this EventSource
  let sseEverConnected = false;  // only recover after a real disconnect (not initial connect hiccup)

  eventSource.onerror = (err) => {
    console.warn('[SSE] error, readyState:', eventSource.readyState, err);
    sseHadError = true;
    document.getElementById('status').textContent = t('reconnecting');
    if (eventSource.readyState === EventSource.CLOSED) {
      // Connection permanently closed — schedule reconnect with backoff
      _scheduleSSEReconnect(cid);
    }
    // readyState === CONNECTING: browser is auto-retrying, we just update status
  };

  eventSource.onopen = () => {
    console.log('[SSE] connected for', cid, sseHadError ? '(reconnect)' : '(initial)');
    // Only recover if we were previously connected and then lost the connection.
    // This avoids re-fetching the user message on the initial connection hiccup.
    const wasDisconnected = sseEverConnected && sseHadError;
    sseEverConnected = true;
    sseRetryCount = 0;
    sseHadError = false;
    if (wasDisconnected) {
      // We just reconnected (browser auto-retry or manual) — recover missed messages
      console.log('[SSE] recovering after reconnect...');
      _recoverConversation(cid);
      // Force-sync active agents: SSE events (done/error_event) may have been
      // lost during the disconnect, leaving ghost entries in activeInteractions.
      syncActiveFromServer();
    }
  };
}

// ── Command result dispatchers ──────────────────────────────────
// Old _dispatchCommandResult and _renderLoadedHistory removed —
// all dispatch is now via RxJS action$ subscriptions in each module.

function _scheduleSSEReconnect(cid) {
  if (sseReconnectTimer) clearTimeout(sseReconnectTimer);
  // Exponential backoff: 1s, 2s, 4s, 8s, 16s, 30s, 60s
  const delay = Math.min(1000 * Math.pow(2, sseRetryCount), 60000);
  sseRetryCount++;
  console.log('[SSE] reconnecting in', delay, 'ms (attempt', sseRetryCount, ')');
  sseReconnectTimer = setTimeout(() => {
    sseReconnectTimer = null;
    if (!cid || cid !== conversationId) return;  // conversation changed, skip
    // Recover missed messages first, then reconnect SSE
    _recoverConversation(cid).then(() => {
      if (cid === conversationId) connectSSE(cid);
    });
  }, delay);
}

// ── Fallback Poll (30s) ──────────────────────────────────────────
// ── Fallback Poll (15s) ──────────────────────────────────────────────────────
function startPollTimer() {
  stopPollTimer();
  pollTimer = setInterval(() => {
    if (!conversationId) return;
    _recoverConversation(conversationId);
  }, 15000);
  // Refresh resources panel every 30s (no-op if content unchanged)
  if (!resourcesTimer) {
    resourcesTimer = setInterval(() => {
      if (conversationId) loadResources();
    }, 30000);
  }
}
function stopPollTimer() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  if (resourcesTimer) { clearInterval(resourcesTimer); resourcesTimer = null; }
}

async function showPrompts() {
  try {
    const data = await rxjs.firstValueFrom(action$('list_prompts'));
    const prompts = data.prompts || [];
    if (!prompts.length) { addMsg('system', 'No prompts available. Create prompts via /prompt or manage_resource.'); return; }
    let overlay = document.getElementById('promptOverlay');
    if (overlay) overlay.remove();
    overlay = document.createElement('div');
    overlay.id = 'promptOverlay';
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;z-index:9999';
    let html = '<div style="background:#1a1a2e;border:1px solid #0f3460;border-radius:12px;max-width:500px;width:90%;max-height:70vh;overflow-y:auto;padding:20px">';
    html += '<h3 style="margin:0 0 12px;color:#e94560">Prompt Library</h3>';
    for (const p of prompts) {
      html += '<div class="prompt-item" data-name="' + escapeHtml(p.name) + '" style="padding:10px;margin:4px 0;background:#16213e;border-radius:8px;cursor:pointer;border:1px solid transparent" onmouseenter="this.style.borderColor=\'#e94560\'" onmouseleave="this.style.borderColor=\'transparent\'">';
      html += '<div style="font-weight:600;color:#fff">' + escapeHtml(p.title || p.name) + '</div>';
      if (p.category) html += '<span style="font-size:11px;color:#888;margin-right:8px">' + escapeHtml(p.category) + '</span>';
      if (p.description) html += '<span style="font-size:11px;color:#aaa">' + escapeHtml(p.description) + '</span>';
      if (p.preview) html += '<div style="font-size:11px;color:#666;margin-top:4px">' + escapeHtml(p.preview) + '...</div>';
      html += '</div>';
    }
    html += '<button onclick="document.getElementById(\'promptOverlay\').remove()" style="margin-top:12px;padding:6px 16px;background:#0f3460;color:#fff;border:none;border-radius:6px;cursor:pointer">Close</button>';
    html += '</div>';
    overlay.innerHTML = html;
    overlay.querySelectorAll('.prompt-item').forEach(item => {
      item.addEventListener('click', async () => {
        const name = item.dataset.name;
        try {
          const d2 = await rxjs.firstValueFrom(action$('get_prompt', { name: name }));
          if (d2.content) {
            document.getElementById('input').value = d2.content;
            document.getElementById('input').focus();
          }
        } catch(e) { addMsg('error', 'Failed to load prompt: ' + e.message); }
        overlay.remove();
      });
    });
    document.body.appendChild(overlay);
  } catch (e) { addMsg('error', 'Failed to list prompts: ' + e.message); }
}
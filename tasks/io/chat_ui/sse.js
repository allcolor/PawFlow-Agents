  sseRetryCount = 0;  // reset so onopen doesn't think we're reconnecting
  const token = getToken();
  const url = SSE_URL + '?conversation_id=' + encodeURIComponent(cid)
    + (token ? '&token=' + encodeURIComponent(token) : '');
  eventSource = new EventSource(url);

  eventSource.addEventListener('thinking', (e) => {
    lastSSEActivity = Date.now();
    showTyping();
    const data = e.data ? JSON.parse(e.data) : {};
    const agentName = data.agent_name || '';
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
      document.getElementById('messages').appendChild(details);
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
      const elapsed = ((Date.now() - te.startTime) / 1000).toFixed(1);
      te.summary.textContent = 'Thought for ' + elapsed + 's';
      te.el.removeAttribute('open');  // collapse
      delete thinkingElements[aKey];
    }
  }

  eventSource.addEventListener('token', (e) => {
    lastSSEActivity = Date.now();
    hideTyping();
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
      // Apply subagent class if not main assistant
      const srcName = (src.name || '').toLowerCase();
      if (srcName) {
        s.el.className = 'msg subagent';
      }
      // Tag with agent name for done handler lookup
      if (s.el) s.el.dataset.agent = (agent || '').toLowerCase();
      s.chunks.push(s.el);
      streamingEl = s.el;  // legacy global
      streamingChunks = s.chunks;
    }
    // Update content with badge — strip identity prefix if LLM echoed it
    const badge = sourceBadge(src);
    const displayText = s.text.replace(/^\[[^\]]+\]:\s*/, '');
    const shouldScroll = isNearBottom();
    s.el.innerHTML = badge + renderMarkdown(displayText);
    scrollBottom(shouldScroll);
    document.getElementById('status').textContent = t('streaming');
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
    el.innerHTML = badge + '<em>' + escapeHtml(data.text || '') + '</em>';
    document.getElementById('messages').appendChild(el);
    scrollBottom();
  });

  eventSource.addEventListener('iteration_status', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    const agentName = data.agent_name || '';
    const aKey = agentKey(agentName);
    if (activeInteractions[aKey]) {
      activeInteractions[aKey].iteration = data.iteration;
      activeInteractions[aKey].maxIterations = data.max_iterations;
      activeInteractions[aKey].round = data.round;
      activeInteractions[aKey].maxRounds = data.max_rounds;
      activeInteractions[aKey].totalTools = data.total_tools;
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
    showTyping();
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
    hideTyping();
    const svcInfo = data.llm_service ? ' via ' + data.llm_service : '';
    const srcInfo = data.source_agent ? displayAgentName(data.source_agent) + ' \u2192 ' : '';
    const header = srcInfo + displayAgentName(agent) + svcInfo;
    if (data.response) {
      const extra = { source: { type: 'agent', name: agent, llm_service: data.llm_service || '' } };
      if (data.source_agent) extra.source.reply_to = data.source_agent;
      extra.model = data.model || '';
      extra.provider = data.provider || '';
      extra.tokens_in = data.tokens_in || 0;
      extra.tokens_out = data.tokens_out || 0;
      extra.duration_ms = (data.duration_s || 0) * 1000;
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
    // Suppress events from cancelled agents
    if (_cancelledAgents.has((data.agent_name || '').toLowerCase())) return;
    // Finalize thinking block before showing tool call
    finalizeThinking(data.agent_name || '');
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
    const srcLabel = displayAgentName(tcAgent) + (data.llm_service ? ' via ' + data.llm_service : '');
    // Parse arguments if string (some LLM providers return args as JSON string)
    let args = data.arguments || {};
    if (typeof args === 'string') { try { args = JSON.parse(args); } catch(e) {} }
    if (data.tool === 'spawn_agents' && args && args.tasks) {
      const lines = args.tasks.map(task => {
        const dst = displayAgentName(task.agent || '?');
        const preview = (task.message || '').substring(0, 80);
        return '\u27A1 ' + srcLabel + ' \u2192 ' + dst + (preview ? ': ' + preview : '');
      });
      addMsg('tool', lines.join('\n'));
    } else if (data.tool === 'filesystem' && args.action === 'edit' && args.path) {
      // Special rendering for edit operations — show inline diff preview
      const fpath = args.path || '?';
      const oldStr = args.old_string || '';
      const newStr = args.new_string || '';
      const startLn = args.start_line || '';
      const endLn = args.end_line || '';
      let editHtml = '<span style="color:#4ecdc4;font-size:11px">\u270E [' + escapeHtml(srcLabel) + '] Edit(' + escapeHtml(fpath) + ')</span>';
      if (startLn && endLn) {
        editHtml += '<span style="color:#8b949e;font-size:11px"> lines ' + startLn + '-' + endLn + '</span>';
      }
      // Detect language from file extension for syntax highlighting
      const _ext = fpath.split('.').pop().toLowerCase();
      const _langMap = {js:'javascript',ts:'typescript',py:'python',rb:'ruby',rs:'rust',go:'go',java:'java',cpp:'cpp',c:'c',cs:'csharp',php:'php',sh:'bash',bash:'bash',json:'json',html:'xml',xml:'xml',css:'css',sql:'sql',yaml:'yaml',yml:'yaml',md:'markdown',jsx:'javascript',tsx:'typescript',vue:'xml',svelte:'xml'};
      const _lang = _langMap[_ext] || '';
      // Build unified diff: common prefix as context, then changes, then common suffix
      const oldLines = oldStr ? oldStr.split('\n') : [];
      const newLines = newStr ? newStr.split('\n') : [];
      // Find common prefix
      let cpx = 0;
      while (cpx < oldLines.length && cpx < newLines.length && oldLines[cpx] === newLines[cpx]) cpx++;
      // Find common suffix
      let csx = 0;
      while (csx < (oldLines.length - cpx) && csx < (newLines.length - cpx) && oldLines[oldLines.length - 1 - csx] === newLines[newLines.length - 1 - csx]) csx++;
      const diffLines = [];
      const maxLines = 12;
      let shown = 0;
      // Context prefix (max 2 lines)
      const ctxPrefix = oldLines.slice(Math.max(0, cpx - 2), cpx);
      ctxPrefix.forEach(l => { diffLines.push('<div><span style="color:#8b949e;user-select:none">  </span>' + _synLine(l, _lang) + '</div>'); });
      // Removed lines
      const removed = oldLines.slice(cpx, oldLines.length - csx);
      removed.slice(0, 6).forEach(l => { diffLines.push('<div style="background:rgba(248,81,73,0.15)"><span style="color:#f85149;user-select:none">- </span>' + _synLine(l, _lang) + '</div>'); shown++; });
      if (removed.length > 6) diffLines.push('<div style="color:#8b949e">  ... +' + (removed.length - 6) + ' lines removed</div>');
      // Added lines
      const added = newLines.slice(cpx, newLines.length - csx);
      added.slice(0, 6).forEach(l => { diffLines.push('<div style="background:rgba(63,185,80,0.15)"><span style="color:#3fb950;user-select:none">+ </span>' + _synLine(l, _lang) + '</div>'); shown++; });
      if (added.length > 6) diffLines.push('<div style="color:#8b949e">  ... +' + (added.length - 6) + ' lines added</div>');
      // Context suffix (max 2 lines)
      const ctxSuffix = oldLines.slice(oldLines.length - csx, oldLines.length - csx + 2);
      ctxSuffix.forEach(l => { diffLines.push('<div><span style="color:#8b949e;user-select:none">  </span>' + _synLine(l, _lang) + '</div>'); });
      // Summary line
      const _addedCount = added.length;
      const _removedCount = removed.length;
      if (_addedCount || _removedCount) {
        const parts = [];
        if (_addedCount) parts.push(_addedCount + ' added');
        if (_removedCount) parts.push(_removedCount + ' removed');
        editHtml += '<span style="color:#8b949e;font-size:10px;margin-left:8px">(' + parts.join(', ') + ')</span>';
      }
      if (diffLines.length > 0) {
        editHtml += '<pre class="diff-output' + (_lang ? ' language-' + _lang : '') + '" style="margin:2px 0 0 0;font-size:11px">' + diffLines.join('') + '</pre>';
      }
      const el = document.createElement('div');
      el.className = 'msg tool';
      el.innerHTML = editHtml;
      const shouldScroll = isNearBottom();
      const container = document.getElementById('messages');
      const typingEl = document.getElementById('typing');
      if (typingEl) { container.insertBefore(el, typingEl); } else { container.appendChild(el); }
      scrollBottom(shouldScroll);
    } else {
      // Show agent source + tool name + arguments preview
      const argKeys = Object.keys(args);
      let argPreview = '';
      if (argKeys.length > 0) {
        argPreview = argKeys.map(k => {
          const v = typeof args[k] === 'string' ? args[k].substring(0, 60) : JSON.stringify(args[k]).substring(0, 60);
          return k + '=' + v;
        }).join(', ');
        if (argPreview.length > 120) argPreview = argPreview.substring(0, 120) + '...';
      }
      addMsg('tool', '\u{1F527} [' + srcLabel + '] ' + data.tool + (argPreview ? '(' + argPreview + ')' : ''));
    }
    document.getElementById('status').textContent = t('usingTool', {tool: data.tool});
  });

  eventSource.addEventListener('tool_result', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    if (_cancelledAgents.has((data.agent_name || '').toLowerCase())) return;
    console.log('[SSE] tool_result received:', data.tool, (data.result || '').substring(0, 100));
    // spawn_agents: responses are shown via sub_agent_done events in real-time
    // tool_result just shows a compact summary (don't duplicate responses)
    if (data.tool === 'spawn_agents' && data.result) {
      const srcAgent = displayAgentName(data.agent_name || '');
      const srcSvc = data.llm_service ? ' via ' + data.llm_service : '';
      try {
        const agents = JSON.parse(data.result);
        if (Array.isArray(agents)) {
          const summary = agents.map(a => displayAgentName(a.agent || '?') + ': ' + a.status).join(', ');
          addMsg('tool', '\u2705 [' + srcAgent + srcSvc + '] spawn_agents: ' + summary);
        } else {
          addMsg('tool', '\u2705 [' + srcAgent + srcSvc + '] spawn_agents: ' + data.result.substring(0, 200));
        }
      } catch(ex) {
        // Result is an error string (e.g. self-call), not JSON
        addMsg('tool', '\u2705 [' + srcAgent + srcSvc + '] spawn_agents: ' + data.result.substring(0, 200));
      }
      showTyping();
      return;
    }
    if (data.agent_name) trackAgentToolDone(data.agent_name, data.tool);
    const resultAgent = displayAgentName(data.agent_name || '');
    const resultSvc = data.llm_service ? ' via ' + data.llm_service : '';
    const fullResult = data.result || '';
    // Check if result contains a diff — render it fully with colors
    const diffRendered = _renderDiff(fullResult, data.path || '');
    if (diffRendered) {
      const el = document.createElement('div');
      el.className = 'msg tool';
      el.innerHTML = '<span style="color:#4ecdc4;font-size:11px">\u2705 [' + escapeHtml(resultAgent + resultSvc) + '] ' + escapeHtml(data.tool) + '</span>' + diffRendered;
      const shouldScroll = isNearBottom();
      const container = document.getElementById('messages');
      const typingEl = document.getElementById('typing');
      if (typingEl) { container.insertBefore(el, typingEl); } else { container.appendChild(el); }
      scrollBottom(shouldScroll);
    } else if (fullResult.length > 300) {
      // Long result — collapsible details with optional syntax highlighting
      const firstLine = fullResult.split('\n')[0].substring(0, 200);
      const el = document.createElement('div');
      el.className = 'msg tool';
      // Detect language from file path for read_file results
      const _trPath = data.path || '';
      const _trExt = _trPath.split('.').pop().toLowerCase();
      const _trLangMap = {js:'javascript',ts:'typescript',py:'python',rb:'ruby',rs:'rust',go:'go',java:'java',cpp:'cpp',c:'c',cs:'csharp',php:'php',sh:'bash',json:'json',html:'xml',xml:'xml',css:'css',sql:'sql',yaml:'yaml',yml:'yaml',jsx:'javascript',tsx:'typescript'};
      const _trLang = _trLangMap[_trExt] || '';
      const _trCls = (data.fs_action === 'read_file' && _trLang) ? ' class="language-' + _trLang + '"' : '';
      el.innerHTML = '<details><summary style="color:#4ecdc4;font-size:11px;cursor:pointer">\u2705 [' + escapeHtml(resultAgent + resultSvc) + '] ' + escapeHtml(data.tool) + ': ' + escapeHtml(firstLine) + '</summary><pre style="font-size:11px;margin:4px 0 0 0;max-height:300px;overflow-y:auto"><code' + _trCls + '>' + escapeHtml(fullResult) + '</code></pre></details>';
      const shouldScroll = isNearBottom();
      const container = document.getElementById('messages');
      const typingEl = document.getElementById('typing');
      if (typingEl) { container.insertBefore(el, typingEl); } else { container.appendChild(el); }
      // Apply syntax highlighting when details is opened
      if (typeof hljs !== 'undefined' && _trLang) {
        el.querySelector('details').addEventListener('toggle', function() {
          if (this.open) { el.querySelectorAll('pre code').forEach(b => hljs.highlightElement(b)); }
        }, { once: true });
      }
      scrollBottom(shouldScroll);
    } else if (data.fs_action === 'read_file' && data.path && fullResult.length > 50) {
      // read_file: render as syntax-highlighted code block
      const _rfExt = (data.path || '').split('.').pop().toLowerCase();
      const _rfLangMap = {js:'javascript',ts:'typescript',py:'python',rb:'ruby',rs:'rust',go:'go',java:'java',cpp:'cpp',c:'c',cs:'csharp',php:'php',sh:'bash',json:'json',html:'xml',xml:'xml',css:'css',sql:'sql',yaml:'yaml',yml:'yaml',jsx:'javascript',tsx:'typescript'};
      const _rfLang = _rfLangMap[_rfExt] || '';
      const _rfCls = _rfLang ? ' class="language-' + _rfLang + '"' : '';
      const el = document.createElement('div');
      el.className = 'msg tool';
      const _rfFirstLine = fullResult.split('\n')[0].substring(0, 150);
      if (fullResult.length > 300) {
        el.innerHTML = '<details><summary style="color:#4ecdc4;font-size:11px;cursor:pointer">\u2705 [' + escapeHtml(resultAgent + resultSvc) + '] read_file: ' + escapeHtml(_rfFirstLine) + '</summary><pre style="margin:4px 0 0 0;max-height:400px;overflow-y:auto"><code' + _rfCls + '>' + escapeHtml(fullResult) + '</code></pre></details>';
        if (typeof hljs !== 'undefined' && _rfLang) {
          el.querySelector('details').addEventListener('toggle', function() {
            if (this.open) { el.querySelectorAll('pre code').forEach(b => hljs.highlightElement(b)); }
          }, { once: true });
        }
      } else {
        el.innerHTML = '<span style="color:#4ecdc4;font-size:11px">\u2705 [' + escapeHtml(resultAgent + resultSvc) + '] read_file</span><pre style="margin:4px 0 0 0"><code' + _rfCls + '>' + escapeHtml(fullResult) + '</code></pre>';
        if (typeof hljs !== 'undefined' && _rfLang) {
          el.querySelectorAll('pre code').forEach(b => hljs.highlightElement(b));
        }
      }
      const shouldScroll = isNearBottom();
      const container = document.getElementById('messages');
      const typingEl = document.getElementById('typing');
      if (typingEl) { container.insertBefore(el, typingEl); } else { container.appendChild(el); }
      scrollBottom(shouldScroll);
    } else {
      addMsg('tool', '\u2705 [' + resultAgent + resultSvc + '] ' + data.tool + ': ' + escapeHtml(fullResult));
    }
    // User /call has no agent loop following — don't show typing
    if (data.agent_name === 'user') {
      hideTyping();
    } else {
      showTyping();
    }
  });

  eventSource.addEventListener('compact_progress', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    if (data.stage === 'start') {
      showContextOp('Compacting ' + (data.agent || '') + ' (' + data.messages + ' messages, ~' + data.tokens + ' tokens)');
    } else if (data.stage === 'chunking' || data.stage === 'summarizing') {
      showContextOp('Compacting: ' + (data.detail || data.stage));
    } else if (data.stage === 'done') {
      hideContextOp();
      contextOpInProgress = false;
      const agent = data.agent || 'shared';
      addMsg('system', 'Compacted (' + agent + '): ' + data.before + ' messages \u2192 ' + data.after + ' messages (~' + data.tokens_after + ' tokens)');
    } else if (data.stage === 'error') {
      hideContextOp();
      contextOpInProgress = false;
      addMsg('error', 'Compaction failed: ' + data.error);
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
    } else if (data.progress) {
      addMsg('system', '\u{1F4CA} Task progress (' + agent + ', iter ' + (data.iterations || '?') + '): ' + data.progress);
    }
    scrollBottom();
  });

  // ── Plan events ──────────────────────────────────────────────
  eventSource.addEventListener('plan_created', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    const plan = data.plan || data;
    const title = plan.title || data.title || '';
    const stepCount = (plan.steps && plan.steps.length) || data.steps || 0;
    addMsg('system', '\u{1F4CB} Plan created: ' + title + ' (' + stepCount + ' steps)');
    // Show plans button and refresh panel if open
    const plansBtn = document.getElementById('plansBtn');
    if (plansBtn) plansBtn.style.display = '';
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
    hideTyping();
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
    hideTyping();
    const data = JSON.parse(e.data);
    const doneAgent = data.agent_name || data.source?.name || '';
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
    extra.duration_ms = data.duration_ms || 0;
    // Register done msg_id (prevents poll/replay re-add)
    if (extra.msg_id && typeof _seenMsgIds !== 'undefined') {
      _seenMsgIds.add(extra.msg_id);
    }
    // Also register ALL msg_ids we've seen in this turn's token stream
    // (they may differ from the done msg_id due to multi-iteration turns)
    if (s.msg_id && typeof _seenMsgIds !== 'undefined') {
      _seenMsgIds.add(s.msg_id);
    }
    const agentLower = doneAgent.toLowerCase();
    // Clean up narration-only elements (ephemeral)
    document.querySelectorAll('#messages .narration').forEach(el => {
      if (el.dataset.finalizedAgent === agentLower) el.remove();
    });
    // Done NEVER creates a new message. The response was already streamed.
    // Find the LAST assistant/subagent message from this agent and add meta.
    if (s.el && s.el.parentNode) {
      // Active streaming element — finalize it
      s.el.classList.remove('streaming');
      s.el.dataset.rawText = finalText.substring(0, 500);
      const meta = buildMetaLine(extra);
      if (meta && !s.el.querySelector('.msg-meta')) {
        s.el.insertAdjacentHTML('beforeend', meta);
      }
    } else {
      // No active stream — find last message from this agent by data-agent tag
      const allMsgs = document.querySelectorAll('#messages [data-agent="' + agentLower + '"]');
      const lastAgentEl = allMsgs.length ? allMsgs[allMsgs.length - 1] : null;
      if (lastAgentEl) {
        lastAgentEl.classList.remove('finalized', 'streaming');
        lastAgentEl.classList.add('msg', 'assistant');
        lastAgentEl.dataset.rawText = finalText.substring(0, 500);
        const meta = buildMetaLine(extra);
        if (meta && !lastAgentEl.querySelector('.msg-meta')) {
          lastAgentEl.insertAdjacentHTML('beforeend', meta);
        }
      } else if (finalText) {
        // Truly no element exists (poll wakeup, zero tokens streamed)
        addMsg('assistant', finalText, extra);
      }
    }
    clearStream(doneAgent);
    scrollBottom();

    if (data.continuing) {
      // Intermediate round — agent will continue autonomously
      document.getElementById('status').textContent = t('continuing');
      showTyping();
    } else {
      // Final response — ensure active panel is cleaned up
      sending = false;
      document.getElementById('sendBtn').disabled = false;
      document.getElementById('stopBtn').style.display = 'none';
      document.getElementById('status').textContent = t('ready');
      // Belt-and-suspenders: if no more active interactions, clear timer
      if (Object.keys(activeInteractions).length === 0 && activeTimer) {
        clearInterval(activeTimer); activeTimer = null;
      }
    }
    // Refresh conversation list
    loadConversations();
    // Don't close SSE — keep listening for timer-triggered events
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
      // Clear all interactions
      activeInteractions = {};
      updateActivePanel();
    } else {
      trackAgentDone(cancelAgent);
    }
    hideTyping();
    // Remove streaming chunks for the cancelled agent(s)
    if (cancelAgent === 'all') {
      clearAllStreams();
    } else {
      const cs = streams[cancelAgent.toLowerCase()];
      if (cs) {
        for (const c of cs.chunks) { if (c && c.parentNode) c.remove(); }
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
    el.innerHTML = '<span style="color:#60a5fa;font-size:11px;">[' + escapeHtml(dName) + ' \u00b7 btw] </span><em style="color:#888;">thinking...</em>';
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
      el.innerHTML = '<span style="color:#60a5fa;font-size:11px;">[' + escapeHtml(dName) + ' \u00b7 btw] </span>' + renderMarkdown(btwTexts[bKey]);
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
      if (el) { el.innerHTML = '<span style="color:#f87171;font-size:11px;">[' + escapeHtml(dName) + ' \u00b7 btw] Error: ' + escapeHtml(data.error) + '</span>'; }
      else { addMsg('error', '[' + dName + ' \u00b7 btw] ' + data.error); }
    } else if (data.response && !btwTexts[bKey]) {
      // Non-streaming fallback
      const el = btwElements[bKey] || addMsg('btw', '');
      el.innerHTML = '<span style="color:#60a5fa;font-size:11px;">[' + escapeHtml(dName) + ' \u00b7 btw] </span>' + renderMarkdown(data.response);
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

  eventSource.addEventListener('file_request', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    handleFileRequest(data);
  });

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

  eventSource.addEventListener('notification', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    showNotification(data);
  });

  eventSource.addEventListener('error_event', (e) => {
    lastSSEActivity = Date.now();
    hideTyping();
    const data = JSON.parse(e.data);
    addMsg('error', data.message || t('unknownError'));
    // Error could be from any agent — clear the agent's stream if specified
    const errAgent = data.agent_name || '';
    clearStream(errAgent);
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
    addMsg('assistant', data.response || '', extra);
    scrollBottom();
  });

  eventSource.addEventListener('broadcast_done', (e) => {
    lastSSEActivity = Date.now();
    hideTyping();
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
    showTyping();
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
    }
  };
}

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
function startPollTimer() {
  stopPollTimer();
  pollTimer = setInterval(() => {
    if (!conversationId) return;
    _recoverConversation(conversationId);
  }, 30000);
  // Refresh resources panel every 10s
  if (!resourcesTimer) {
    resourcesTimer = setInterval(() => {
      if (conversationId) loadResources();
    }, 10000);
  }
}
function stopPollTimer() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  if (resourcesTimer) { clearInterval(resourcesTimer); resourcesTimer = null; }
}

// ── Local Files (File System Access API) ─────────────────────────
let localDirHandle = null;
let localDirName = '';

async function showPrompts() {
  try {
    const r = await fetch(AGENT_PATH, {
      method: 'POST', headers: {'Content-Type':'application/json', ...authHeaders()},
      body: JSON.stringify({action:'list_prompts', conversation_id: conversationId})
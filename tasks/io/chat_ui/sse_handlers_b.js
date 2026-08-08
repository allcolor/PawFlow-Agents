// Auto-split from sse.js: SSE event-handler registrations (group B).
// Called by connectSSE() in sse.js after the EventSource is created.
function _sseWireB() {

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
    if (typeof turnViewFail === 'function') turnViewFail(data.turn_id || data.request_msg_id, 'stopped');
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
    if (typeof turnViewFail === 'function') turnViewFail(data.turn_id || data.request_msg_id, 'stopped');
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
    if (data.cancelled && typeof turnViewFail === 'function') {
      turnViewFail(data.turn_id || data.request_msg_id, 'cancelled');
    }
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
        conversation_id: data.conversation_id || _sseCid,
        used: data.context_used,
        max: data.context_max,
        pct: data.context_pct,
        cli_context_state: data.cli_context_state,
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
        try { _attachToolResult(tcEl, '[result not delivered]', {placeholder: true}); } catch (e) {}
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
    extra.turn_id = data.turn_id || data.request_msg_id || '';
    extra.turn_final = !!data.final_msg_id;
    const allIds = data.all_msg_ids || [];
    if (extra.msg_id) allIds.push(extra.msg_id);
    if (s.msg_id) allIds.push(s.msg_id);
    // The streamed element can still be anonymous — tokens may arrive before
    // any event carries the msg_id. Stamp it now so it counts as rendered.
    if (s.el && s.el.dataset && !s.el.dataset.msgid && extra.msg_id) {
      s.el.dataset.msgid = extra.msg_id;
    }
    // Mark as seen ONLY the ids that actually reached the DOM, so a replay of
    // this turn cannot duplicate them. Marking the whole turn was silent
    // message loss: an id whose event never arrived (socket down during the
    // gap) was recorded as "already rendered", so addMsg refused the later
    // delivery too and that message stayed invisible until a manual reload.
    for (const id of allIds) {
      if (!id || typeof _seenMsgIds === 'undefined') continue;
      if (document.querySelector('#messages [data-msgid="' + id + '"]')) {
        _seenMsgIds.add(id);
      }
    }
    // Finalize active streaming element (remove streaming class)
    if (s.el && s.el.parentNode) {
      s.el.classList.remove('streaming');
    }
    // Find existing element or create one if nothing was streamed.
    // Scan NEWEST-first: all_msg_ids is ordered oldest→newest, and a turn
    // can persist several messages (a CLI provider narrates, calls tools,
    // then answers). The done payload describes the END of the turn — its
    // tokens, cost, duration and ts. Matching the first id instead stamped
    // the turn's totals onto its opening line and, worse, re-sorted that
    // opening line to the done timestamp, physically moving the first
    // message of the turn below the final answer.
    let anyExists = !!s.el;
    let existingEl = s.el;
    if (!anyExists) {
      for (let i = allIds.length - 1; i >= 0; i--) {
        const mid = allIds[i];
        if (mid) {
          const found = document.querySelector('#messages [data-msgid="' + mid + '"]');
          if (found) { anyExists = true; existingEl = found; break; }
        }
      }
    }
    if (finalText && !anyExists && !data.force_stopped) {
      existingEl = addMsg('assistant', finalText, extra);
      anyExists = !!existingEl;
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
      // The streaming placeholder was positioned at first-token time using
      // the browser's local clock (no server ts is known yet — see addMsg's
      // fallback in _messageSortTs). Now that the authoritative server ts
      // has arrived, re-sort it into place; otherwise any client/server
      // clock drift leaves it stuck wherever the local clock guessed,
      // which can land it above older, correctly server-timestamped
      // history instead of at the bottom where it belongs. Only reposition
      // top-level messages — elements nested in a task/delegate block are
      // already correctly placed by their container.
      // ONLY the live placeholder: an element rendered from a persisted
      // event already carries the server's own ts for that message, and
      // the done ts belongs to the end of the turn, not to it. Re-sorting
      // it here would move a correctly-placed message to the bottom.
      if (extra.ts && existingEl === s.el
          && existingEl.parentNode && existingEl.parentNode.id === 'messages') {
        _insertMessageChronologically(existingEl.parentNode, existingEl, Number(extra.ts), true);
      }
    }
    // A done that is not `continuing` ends the turn. It used to close the
    // simplified block only when the payload also carried is_final AND a
    // final_msg_id; a provider that ends a turn without naming its last
    // message -- the CLI ones do -- left the block on "working" for good, with
    // its answer buried inside it. What the server names still decides which
    // row is hoisted out; whether the turn is over does not depend on it.
    if (!data.continuing && typeof turnViewFinalize === 'function') {
      const finalId = data.final_msg_id || '';
      const exactFinalEl = finalId
        ? document.querySelector('#messages [data-msgid="' + CSS.escape(finalId) + '"]')
        : null;
      const finalData = Object.assign({}, data, extra, {
        msg_id: finalId || extra.msg_id, final_msg_id: finalId, turn_final: !!finalId,
      });
      if (finalId && typeof turnViewIngest === 'function') {
        turnViewIngest('assistant', finalData, exactFinalEl || existingEl);
      }
      if (data.force_stopped) {
        if (typeof turnViewFail === 'function') {
          turnViewFail(extra.turn_id, 'stopped');
        }
      } else {
        turnViewFinalize(finalData);
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
    if (typeof turnViewFail === 'function') turnViewFail(cancelData.turn_id || cancelData.request_msg_id, 'cancelled');
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
    // An interrupt aborts whatever tool the agent had in flight, so its
    // tool_result never arrives -- unlike stop/cancel/error, the turn keeps
    // running, so none of the other finalizers fire either and the row was
    // left pending (spinner + BG/kill buttons) with no result, forever.
    // Mark it now; a real result that still lands replaces this placeholder.
    _finalizeLiveToolCalls(data.agent || '', '[Interrupted]');
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

  eventSource.addEventListener('cli_image_build', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    // Only the admin Updates dialog renders this; it no-ops when closed.
    if (typeof adminBuildProgress === 'function') adminBuildProgress(data);
  });

  eventSource.addEventListener('relay_image_build', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    if (typeof adminRelayBuildProgress === 'function') adminRelayBuildProgress(data);
  });

  eventSource.addEventListener('relay_restart', (e) => {
    lastSSEActivity = Date.now();
    const data = JSON.parse(e.data);
    if (typeof adminRelayRestartProgress === 'function') adminRelayRestartProgress(data);
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
    if (typeof turnViewFail === 'function') turnViewFail(data.turn_id || data.request_msg_id, 'error', data.message || '');
    // data.ts is the server's real publish_event() timestamp -- stamped at
    // the moment the error actually happened, BEFORE any event-bus buffering
    // for a disconnected client. A buffered error can be replayed minutes
    // (or longer) after the fact once a new SSE subscriber connects; without
    // passing ts through, addMsg falls back to the browser's clock at
    // *replay* time, making an already-resolved, long-past error render as
    // if it just happened right now, with no relation to current activity.
    addMsg('error', data.message || t('unknownError'), { ts: data.ts });
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
    const rEl = addMsg('assistant', data.response || '', extra);
    if (typeof turnViewIngest === 'function') turnViewIngest('assistant', data, rEl);
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
    if (data.conversation_id && data.conversation_id !== conversationId) return;
    if (typeof applyThemeCss === 'function') applyThemeCss(data.css || '');
  });

}

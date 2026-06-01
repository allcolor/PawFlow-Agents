// ── Agent commands ──────────────────────────────────────────────
// /interrupt, /stop, /agent, /msg, /btw, /setname
// Loaded before commands.js — all functions are global.

function _parseAgentTask(raw) {
  // Parse "agent::taskid" or just "agent"
  const idx = raw.indexOf('::');
  if (idx === -1) return { agent: raw, taskId: '' };
  return { agent: resolveAgentName(raw.substring(0, idx)), taskId: raw.substring(idx + 2) };
}

function cmdInterrupt(text, parts) {
  const raw = parts.length > 1 ? resolveAgentName(stripTarget(parts[1])) : (selectedAgent || 'ALL');
  const parsed = _parseAgentTask(raw);
  if (parsed.taskId) {
    interruptSingle(parsed.agent, parsed.taskId);
    addMsg('system', t('interruptingTask', { task: parsed.taskId, agent: parsed.agent }));
  } else {
    cmdAgentInterrupt(parsed.agent);
  }
  return true;
}

function cmdForceStop(text, parts) {
  const raw = parts.length > 1 ? resolveAgentName(stripTarget(parts[1])) : (selectedAgent || 'ALL');
  const parsed = _parseAgentTask(raw);
  if (parsed.taskId) {
    stopSingle(parsed.agent, parsed.taskId);
    addMsg('system', t('stoppingTask', { task: parsed.taskId, agent: parsed.agent }));
  } else {
    cancelAgent(parsed.agent);
  }
  return true;
}

function cmdAgent(text, parts) {
  const qargs = parseQuotedArgs(text);
  const sub = (qargs[1] || 'list').toLowerCase();
  if (sub === 'list') {
    cmdAgentList();
  } else if (sub === 'create') {
    cmdAgentCreate();
  } else if (sub === 'select') {
    const name = resolveAgentName(stripTarget(qargs[2] || ''));
    cmdAgentSelect(name);
  } else if (sub === 'delete' || sub === 'del') {
    const name = resolveAgentName(stripTarget(qargs[2]));
    if (!name) { addMsg('system', t('agentDeleteUsage')); }
    else { cmdAgentDelete(name); }
  } else if (sub === 'msg' || sub === 'message') {
    const target = resolveAgentName(stripTarget(qargs[2] || ''));
    const msgText = qargs.slice(3).join(' ');
    if (!target) { addMsg('system', t('agentMsgUsage')); }
    else if (!msgText) { addMsg('system', t('agentMsgTargetUsage', { target: target })); }
    else if (target.toUpperCase() === 'ALL') { cmdAgentMsgAll(msgText); }
    else { cmdAgentMsg(target, msgText); }
  } else if (sub === 'btw') {
    const target = resolveAgentName(stripTarget(qargs[2] || ''));
    const btwText = qargs.slice(3).join(' ');
    if (!btwText && !target) { addMsg('system', t('agentBtwUsage')); }
    else if (!btwText) {
      cmdAgentBtw('', target + ' ' + qargs.slice(3).join(' '));
    } else {
      cmdAgentBtw(target, btwText);
    }
  } else if (sub === 'resume') {
    const target = resolveAgentName(stripTarget(qargs[2] || ''));
    const resumeMsg = qargs.slice(3).join(' ') || t('continueFromLast');
    if (target.toUpperCase() === 'ALL') { cmdAgentMsgAll(resumeMsg); }
    else if (target) { cmdAgentMsg(target, resumeMsg); }
    else {
      sending = true;
      const body = { message: resumeMsg };
      if (conversationId) body.conversation_id = conversationId;
      addMsg('user', resumeMsg);
      fetch(API, { method: 'POST', headers: getAuthHeaders(), body: JSON.stringify(body) })
        .then(r => r.json())
        .then(data => {
          if (data.conversation_id && !conversationId) { conversationId = data.conversation_id; connectSSE(conversationId); }
          _checkServerRestart(data);
        })
        .catch(e => addMsg('error', e.message))
        .finally(() => { sending = false; });
    }
  } else if (sub === 'setname' || sub === 'rename') {
    const qargs2 = parseQuotedArgs(text);
    const realName = stripTarget(qargs2[2] || '');
    const nickname = qargs2[3] || '';
    if (!realName) {
      addMsg('system', t('agentSetnameUsage'));
    } else {
      cmdAgentSetname(realName, nickname || realName);
    }
  } else if (sub === 'disable' && parts[2]) {
    fireAction('manage_resource', { resource_type: 'agent', name: stripTarget(parts[2]),
      data: {}, _action: 'disable' });
    fireAction('agent_disable', { agent_name: stripTarget(parts[2]) });
    addMsg('system', t('agentDisabled'));
  } else if (sub === 'enable' && parts[2]) {
    action$('agent_enable', { agent_name: stripTarget(parts[2]) }).subscribe(data => {
      addMsg('system', data.result || data.error || t('agentEnabled'));
    });
  } else if (sub === 'promote' && parts[2] && parts[3]) {
    action$('agent_promote', { agent_name: stripTarget(parts[2]), target_scope: parts[3] }).subscribe(data => {
      addMsg('system', data.result || data.error || t('agentPromoted'));
    });
  } else if (sub === 'create-conv') {
    const qargs2 = parseQuotedArgs(text);
    const cname = stripTarget(qargs2[2] || '');
    const cprompt = qargs2[3] || '';
    if (!cname || !cprompt) { addMsg('system', t('agentCreateConvUsage')); return true; }
    action$('create_agent', { name: cname, prompt: cprompt, scope: 'conversation' }).subscribe(data => {
      addMsg('system', data.result || data.error || t('agentCreated'));
    });
  } else {
    addMsg('system', t('agentUsage'));
  }
  return true;
}

function cmdMsg(text) {
  const margs = parseQuotedArgs(text);
  let target, msgText;
  if (margs[1] && margs[1].startsWith('@')) {
    // Explicit target: /msg @agent message
    target = resolveAgentName(stripTarget(margs[1]));
    msgText = margs.slice(2).join(' ');
  } else if (margs[1] && resolveAgentName(margs[1]) !== margs[1]) {
    // Resolved via nickname: /msg nickname message
    target = resolveAgentName(margs[1]);
    msgText = margs.slice(2).join(' ');
  } else {
    // No target: /msg message → use selected agent
    target = selectedAgent;
    msgText = margs.slice(1).join(' ');
  }
  if (!target) { addMsg('system', t('msgUsage')); }
  else if (!msgText) { addMsg('system', t('msgTargetUsage', { target: target })); }
  else if (/^t_[0-9a-f]+$/.test(target)) { cmdTaskMsg(target, msgText); }
  else if (target.toUpperCase() === 'ALL') { cmdAgentMsgAll(msgText); }
  else { cmdAgentMsg(target, msgText); }
  return true;
}

function cmdBtw(text) {
  const bargs = parseQuotedArgs(text);
  let target, btwText;
  if (bargs[1] && bargs[1].startsWith('@')) {
    target = resolveAgentName(stripTarget(bargs[1]));
    btwText = bargs.slice(2).join(' ');
  } else if (bargs[1] && resolveAgentName(bargs[1]) !== bargs[1]) {
    target = resolveAgentName(bargs[1]);
    btwText = bargs.slice(2).join(' ');
  } else {
    target = selectedAgent;
    btwText = bargs.slice(1).join(' ');
  }
  if (!btwText && !target) { addMsg('system', t('btwUsage')); }
  else if (!btwText) {
    cmdAgentBtw('', target + ' ' + bargs.slice(2).join(' '));
  } else {
    cmdAgentBtw(target, btwText);
  }
  return true;
}

function cmdSetname(text) {
  const sargs = parseQuotedArgs(text);
  const realName = stripTarget(sargs[1] || '');
  const nickname = sargs[2] || '';
  if (!realName) { addMsg('system', t('setnameUsage')); return true; }
  cmdAgentSetname(realName, nickname || realName);
  return true;
}

function cmdAgentList() {
  if (!conversationId) { addMsg('system', t('noConv')); return; }
  action$('list_agents').subscribe(data => {
    const agents = data.agents || {};
    const selected = data.selected || '';
    const names = Object.keys(agents);
    if (names.length === 0) {
      addMsg('system', t('noAgentsDefinedUsage'));
    } else {
      const scopeIcons = {'global': '\u{1F310}', 'user': '\u{1F464}', 'conversation': '\u{1F4AC}'};
      const lines = names.map(n => {
        const a = agents[n];
        const marker = n === selected ? ' \u2705' : '';
        const scope = scopeIcons[a._scope || ''] || '';
        const pr = (a.prompt || '').substring(0, 80);
        return '\u2022 ' + scope + ' **' + n + '**' + marker + ' \u2014 ' + pr + '...';
      });
      addMsg('system', t('agentsListTitle', { state: selected ? t('activeAgentState', { agent: selected }) : t('noneSelected'), lines: lines.join('\n') }));
    }
  });
}

function cmdAgentCreate() {
  showResourceCreator('agent');
}

// showResourceCreator and _saveResourceCreate are defined in resources.js

function updateActiveAgentBadge() {
  const badge = document.getElementById('activeAgentBadge');
  const agent = selectedAgent || '';
  let h = 0;
  for (let i = 0; i < agent.length; i++) h = ((h << 5) - h + agent.charCodeAt(i)) | 0;
  const hue = Math.abs(h) % 360;
  badge.style.background = 'hsl(' + hue + ',60%,25%)';
  badge.style.color = 'hsl(' + hue + ',80%,80%)';
  badge.title = !agent ? t('defaultAgentTitle') : t('activeAgentTitle', { agent: agent });
  // Compose label + inline context gauge (if we have a cached value for this agent)
  const label = '\u2192 ' + displayAgentName(agent);
  let gaugeHtml = '';
  try {
    const usage = (window._contextUsage || {})[(agent || '').toLowerCase()];
    if (usage && typeof renderCtxGauge === 'function') {
      gaugeHtml = '<span style="margin-left:8px;">' + renderCtxGauge(usage, {width: 50}) + '</span>';
    } else if (agent && typeof hydrateContextUsage === 'function') {
      hydrateContextUsage();
    }
  } catch (e) {}
  badge.innerHTML = escapeHtml(label) + gaugeHtml;
  badge.style.display = '';
}

function cmdAgentSelect(name) {
  if (!name) {
    addMsg('error', t('bugAgentRequired'));
    return Promise.resolve(false);
  }
  var _prevAgent = selectedAgent;
  if (!conversationId) {
    pendingAgent = name;
    selectedAgent = name;
    updateActiveAgentBadge();
    addMsg('system', t('agentSelectedPending', { name: name }));
    if (window._pawflowExtRuntime) {
      window._pawflowExtRuntime.fireHook('agent_changed',
        { oldAgent: _prevAgent || null, newAgent: name, pending: true });
    }
    return Promise.resolve(true);
  }
  return new Promise(resolve => {
    action$('select_agent', { name }).subscribe(data => {
      if (data.error) { addMsg('error', data.error); resolve(false); return; }
      selectedAgent = name;
      updateActiveAgentBadge();
      addMsg('system', t('agentSelected', { name: name }));
      loadResources();
      if (window._pawflowExtRuntime) {
        window._pawflowExtRuntime.fireHook('agent_changed',
          { oldAgent: _prevAgent || null, newAgent: name, pending: false });
      }
      resolve(true);
    });
  });
}

function cmdAgentDelete(name) {
  if (!conversationId) { addMsg('system', t('noConv')); return; }
  action$('delete_agent', { name: name }).subscribe(data => {
    if (data.error) { addMsg('error', data.error); return; }
    addMsg('system', data.deleted ? t('agentDeleted', { name: name }) : t('agentNotFound', { name: name }));
    loadResources();
  });
}

function cmdAgentSetname(realName, nickname) {
  if (!conversationId) { addMsg('system', t('noConv')); return; }
  action$('set_agent_nickname', { agent_name: realName, nickname: nickname }).subscribe(data => {
    if (data.error) { addMsg('error', data.error); return; }
    nicknameMap[realName] = nickname;
    addMsg('system', t('agentRenamed', { real: realName, nick: nickname }));
  });
}

function cmdAgentMsg(agentName, text) {
  if (pendingFiles.some(f => f.uploading)) {
    addMsg('system', t('filesUploadingWait')); return;
  }
  const attachments = pendingFiles.map(f => ({
    filename: f.filename, mime_type: f.mime_type, file_id: f.file_id,
  }));
  const attachmentsForDisplay = [...pendingFiles];
  pendingFiles = [];
  renderAttachments();

  const userSource = { type: 'user', name: '', target_agent: agentName };
  const msgEl = addMsg('user', text, { source: userSource });
  if (attachmentsForDisplay.length > 0) {
    msgEl.innerHTML = sourceBadge(userSource) + escapeHtml(text) + renderUserAttachments(attachmentsForDisplay);
  }
  clearStream(agentName);
  if (typeof _ensureSSEBeforeUserAction === 'function') _ensureSSEBeforeUserAction();
  sending = true;
  document.getElementById('status').textContent = t('sending');

  const body = { message: text, target_agent: agentName };
  if (conversationId) body.conversation_id = conversationId;
  if (attachments.length > 0) body.attachments = attachments;
  const ttlVal = parseInt(document.getElementById('ttlSelect').value, 10);
  if (ttlVal > 0) body.ttl = ttlVal;

  fetch(API, {
    method: 'POST', headers: getAuthHeaders(),
    body: JSON.stringify(body),
    credentials: 'same-origin',
  }).then(r => r.json())
    .then(data => {
      if (data.error) { addMsg('error', data.error); sending = false; return; }
      if (data.conversation_id && !conversationId) { conversationId = data.conversation_id; connectSSE(conversationId); }
      if (data.message_count) serverMsgCount = data.message_count;
      _checkServerRestart(data);
    }).catch(e => {
      addMsg('error', t('failedSendAgent', { error: e.message }));
      sending = false;
    });
}

function cmdTaskMsg(taskId, text) {
  if (!conversationId) { addMsg('system', t('noConv')); return; }
  addMsg('user', text, { source: { type: 'user', name: '', target_task: taskId } });
  action$('msg_task', { task_id: taskId, message: text }).subscribe(data => {
    if (data.error) { addMsg('error', data.error); }
    else { addMsg('system', t('messageSentTask', { task: taskId })); }
  });
}

function cmdAgentMsgAll(text) {
  if (!conversationId) {
    addMsg('system', t('broadcastFirst'));
    return;
  }
  addMsg('user', text, { source: { type: 'user', name: '', target_agent: 'ALL' } });
  if (typeof _ensureSSEBeforeUserAction === 'function') _ensureSSEBeforeUserAction();
  sending = true;
  document.getElementById('status').textContent = t('broadcasting');

  action$('broadcast_agents', { message: text }).subscribe(data => {
    if (data.error) { addMsg('error', data.error); sending = false; }
  });
}

function cmdAgentInterrupt(target) {
  if (!conversationId) { addMsg('system', t('noConv')); return; }
  const isAll = target.toUpperCase() === 'ALL';
  addMsg('system', isAll ? t('interruptingAllAgents') : t('interruptingAgent', { agent: target || t('agent') }));
  if (isAll) {
    fireAction('interrupt', { agent_name: '' });
    action$('list_agents').subscribe(data => {
      for (const name of Object.keys(data.agents || {})) {
        fireAction('interrupt', { agent_name: name });
      }
    });
  } else {
    fireAction('interrupt', { agent_name: target || '' });
  }
}

function cmdAgentBtw(target, question) {
  if (!conversationId) { addMsg('system', t('noConv')); return; }
  const agent = target || '';
  const isAll = agent.toUpperCase() === 'ALL';
  addMsg('user', question, { source: { type: 'user', name: '', target_agent: agent || '', btw: true } });
  fireAction('btw', { agent_name: isAll ? 'ALL' : agent, message: question });
}

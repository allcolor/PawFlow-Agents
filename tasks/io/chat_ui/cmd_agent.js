// ── Agent commands ──────────────────────────────────────────────
// /stop, /agent, /msg, /btw, /setname, /interrupt
// Loaded before commands.js — all functions are global.

function cmdStop(text, parts) {
  const force = parts.includes('-f') || parts.includes('--force');
  const targetParts = parts.slice(1).filter(p => p !== '-f' && p !== '--force');
  const target = targetParts.length > 0 ? resolveAgentName(stripTarget(targetParts[0])) : (selectedAgent || 'ALL');
  if (force) {
    cancelAgent(target, true);
  } else {
    cmdAgentInterrupt(target);
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
    if (!name) { addMsg('system', 'Usage: /agent delete @<name>'); }
    else { cmdAgentDelete(name); }
  } else if (sub === 'msg' || sub === 'message') {
    const target = resolveAgentName(stripTarget(qargs[2] || ''));
    const msgText = qargs.slice(3).join(' ');
    if (!target) { addMsg('system', 'Usage: /agent msg @<name|ALL> <message>'); }
    else if (!msgText) { addMsg('system', 'Usage: /agent msg ' + target + ' <message>'); }
    else if (target.toUpperCase() === 'ALL') { cmdAgentMsgAll(msgText); }
    else { cmdAgentMsg(target, msgText); }
  } else if (sub === 'interrupt' || sub === 'int') {
    const target = resolveAgentName(stripTarget(qargs[2] || ''));
    cmdAgentInterrupt(target);
  } else if (sub === 'btw') {
    const target = resolveAgentName(stripTarget(qargs[2] || ''));
    const btwText = qargs.slice(3).join(' ');
    if (!btwText && !target) { addMsg('system', 'Usage: /agent btw @<name|ALL> <question>'); }
    else if (!btwText) {
      cmdAgentBtw('', target + ' ' + qargs.slice(3).join(' '));
    } else {
      cmdAgentBtw(target, btwText);
    }
  } else if (sub === 'resume') {
    const target = resolveAgentName(stripTarget(qargs[2] || ''));
    const resumeMsg = qargs.slice(3).join(' ') || 'Continue from where you left off.';
    if (target.toUpperCase() === 'ALL') { cmdAgentMsgAll(resumeMsg); }
    else if (target) { cmdAgentMsg(target, resumeMsg); }
    else {
      sending = true;
      const body = { message: resumeMsg };
      if (conversationId) body.conversation_id = conversationId;
      addMsg('user', resumeMsg);
      fetch(API, { method: 'POST', headers: getAuthHeaders(), body: JSON.stringify(body) })
        .then(r => r.json()).then(data => {
          if (data.conversation_id && !conversationId) { conversationId = data.conversation_id; connectSSE(conversationId); }
        }).catch(e => addMsg('error', e.message))
        .finally(() => { sending = false; });
    }
  } else if (sub === 'setname' || sub === 'rename') {
    const qargs2 = parseQuotedArgs(text);
    const realName = stripTarget(qargs2[2] || '');
    const nickname = qargs2[3] || '';
    if (!realName) {
      addMsg('system', 'Usage: /agent setname @<realname> [nickname]  (omit nickname to reset)');
    } else {
      cmdAgentSetname(realName, nickname || realName);
    }
  } else if (sub === 'disable' && parts[2]) {
    fetch(API, { method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'manage_resource', resource_type: 'agent', name: stripTarget(parts[2]),
        data: {}, conversation_id: conversationId, _action: 'disable' }),
    }).then(() =>
      fetch(API, { method: 'POST', headers: getAuthHeaders(),
        body: JSON.stringify({ action: 'agent_disable', agent_name: stripTarget(parts[2]), conversation_id: conversationId }),
      })
    ).then(r => r.json()).then(data => {
      addMsg('system', data.result || data.error || 'Agent disabled.');
    }).catch(e => addMsg('error', e.message));
  } else if (sub === 'enable' && parts[2]) {
    fetch(API, { method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'agent_enable', agent_name: stripTarget(parts[2]), conversation_id: conversationId }),
    }).then(r => r.json()).then(data => {
      addMsg('system', data.result || data.error || 'Agent enabled.');
    }).catch(e => addMsg('error', e.message));
  } else if (sub === 'promote' && parts[2] && parts[3]) {
    fetch(API, { method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'agent_promote', agent_name: stripTarget(parts[2]), target_scope: parts[3],
        conversation_id: conversationId }),
    }).then(r => r.json()).then(data => {
      addMsg('system', data.result || data.error || 'Agent promoted.');
    }).catch(e => addMsg('error', e.message));
  } else if (sub === 'create-conv') {
    const qargs2 = parseQuotedArgs(text);
    const cname = stripTarget(qargs2[2] || '');
    const cprompt = qargs2[3] || '';
    if (!cname || !cprompt) { addMsg('system', 'Usage: /agent create-conv @<name> "<prompt>"'); return true; }
    fetch(API, { method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'create_agent', conversation_id: conversationId,
        name: cname, prompt: cprompt, scope: 'conversation' }),
    }).then(r => r.json()).then(data => {
      addMsg('system', data.result || data.error || 'Agent created.');
    }).catch(e => addMsg('error', e.message));
  } else {
    addMsg('system', 'Usage: /agent list | create | create-conv | select | delete | msg | disable | enable | promote | setname');
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
  if (!target) { addMsg('system', 'Usage: /msg [@agent] <message> (defaults to selected agent)'); }
  else if (!msgText) { addMsg('system', 'Usage: /msg ' + target + ' <message>'); }
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
  if (!btwText && !target) { addMsg('system', 'Usage: /btw [@agent] <question> (defaults to selected agent)'); }
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
  if (!realName) { addMsg('system', 'Usage: /setname @<agent> [nickname]  (omit nickname to reset)'); return true; }
  cmdAgentSetname(realName, nickname || realName);
  return true;
}

function cmdAgentList() {
  if (!conversationId) { addMsg('system', 'No active conversation'); return; }
  fetch(API, {
    method: 'POST', headers: getAuthHeaders(),
    body: JSON.stringify({ action: 'list_agents', conversation_id: conversationId }),
  }).then(r => r.json()).then(data => {
    const agents = data.agents || {};
    const selected = data.selected || '';
    const names = Object.keys(agents);
    if (names.length === 0) {
      addMsg('system', 'No agents defined. Use /agent create to add one.');
    } else {
      const scopeIcons = {'global': '\u{1F310}', 'user': '\u{1F464}', 'conversation': '\u{1F4AC}'};
      const lines = names.map(n => {
        const a = agents[n];
        const marker = n === selected ? ' \u2705' : '';
        const scope = scopeIcons[a._scope || ''] || '';
        const pr = (a.prompt || '').substring(0, 80);
        return '\u2022 ' + scope + ' **' + n + '**' + marker + ' \u2014 ' + pr + '...';
      });
      addMsg('system', 'Agents (' + (selected ? 'active: ' + selected : 'none selected') + '):\n' + lines.join('\n'));
    }
  }).catch(e => addMsg('error', 'Failed to list agents: ' + e.message));
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
  badge.textContent = '\u2192 ' + displayAgentName(agent);
  badge.title = !agent ? 'Default agent' : 'Active: ' + agent + ' — click to switch back';
  badge.style.display = '';
}

function cmdAgentSelect(name) {
  const isDefault = !name;
  if (!conversationId) {
    pendingAgent = isDefault ? null : name;
    selectedAgent = isDefault ? '' : name;
    updateActiveAgentBadge();
    addMsg('system', isDefault ? 'Switched to default agent (assistant).' : `Agent '${name}' selected (will activate on first message).`);
    return;
  }
  fetch(API, {
    method: 'POST', headers: getAuthHeaders(),
    body: JSON.stringify({
      action: 'select_agent', conversation_id: conversationId,
      name: isDefault ? '' : name,
    }),
  }).then(r => r.json()).then(data => {
    if (data.error) { addMsg('error', data.error); return; }
    selectedAgent = isDefault ? '' : name;
    updateActiveAgentBadge();
    addMsg('system', isDefault ? 'Switched to default agent (assistant).' : `Agent '${name}' selected. Messages now go to ${name}.`);
    loadResources();
  }).catch(e => addMsg('error', 'Failed to select agent: ' + e.message));
}

function cmdAgentDelete(name) {
  if (!conversationId) { addMsg('system', 'No active conversation'); return; }
  fetch(API, {
    method: 'POST', headers: getAuthHeaders(),
    body: JSON.stringify({
      action: 'delete_agent', conversation_id: conversationId,
      name: name,
    }),
  }).then(r => r.json()).then(data => {
    if (data.error) { addMsg('error', data.error); return; }
    addMsg('system', data.deleted ? `Agent '${name}' deleted.` : `Agent '${name}' not found.`);
    loadResources();
  }).catch(e => addMsg('error', 'Failed to delete agent: ' + e.message));
}

function cmdAgentSetname(realName, nickname) {
  if (!conversationId) { addMsg('system', 'No active conversation'); return; }
  fetch(API, {
    method: 'POST', headers: getAuthHeaders(),
    body: JSON.stringify({
      action: 'set_agent_nickname', conversation_id: conversationId,
      agent_name: realName, nickname: nickname,
    }),
  }).then(r => r.json()).then(data => {
    if (data.error) { addMsg('error', data.error); return; }
    nicknameMap[realName] = nickname;
    addMsg('system', t('agentRenamed', { real: realName, nick: nickname }));
  }).catch(e => addMsg('error', 'Failed: ' + e.message));
}

function cmdAgentMsg(agentName, text) {
  const attachments = pendingFiles.map(f => ({
    filename: f.filename, mime_type: f.mime_type, data: f.data,
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
  sending = true;
  lastSSEActivity = Date.now();
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
  }).then(r => r.json()).then(data => {
    if (data.error) { addMsg('error', data.error); sending = false; return; }
    if (data.conversation_id && !conversationId) {
      conversationId = data.conversation_id;
      connectSSE(conversationId);
    }
    if (data.message_count) serverMsgCount = data.message_count;
  }).catch(e => {
    addMsg('error', 'Failed to send to agent: ' + e.message);
    sending = false;
  });
}

function cmdAgentMsgAll(text) {
  if (!conversationId) {
    addMsg('system', 'Start a conversation first before broadcasting.');
    return;
  }
  addMsg('user', text, { source: { type: 'user', name: '', target_agent: 'ALL' } });
  sending = true;
  lastSSEActivity = Date.now();
  document.getElementById('status').textContent = 'Broadcasting...';

  fetch(API, {
    method: 'POST', headers: getAuthHeaders(),
    body: JSON.stringify({
      action: 'broadcast_agents',
      conversation_id: conversationId,
      message: text,
    }),
    credentials: 'same-origin',
  }).then(r => r.json()).then(data => {
    if (data.error) { addMsg('error', data.error); sending = false; }
  }).catch(e => {
    addMsg('error', 'Broadcast failed: ' + e.message);
    sending = false;
  });
}

function cmdAgentInterrupt(target) {
  if (!conversationId) { addMsg('system', 'No active conversation.'); return; }
  const isAll = target.toUpperCase() === 'ALL';
  addMsg('system', isAll ? 'Interrupting all agents...' : ('Interrupting ' + (target || 'agent') + '...'));
  if (isAll) {
    fetch(API, { method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'interrupt', conversation_id: conversationId, agent_name: '' }),
    }).catch(e => addMsg('error', 'Interrupt failed: ' + e.message));
    fetch(API, { method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'list_agents', conversation_id: conversationId }),
    }).then(r => r.json()).then(data => {
      for (const name of Object.keys(data.agents || {})) {
        fetch(API, { method: 'POST', headers: getAuthHeaders(),
          body: JSON.stringify({ action: 'interrupt', conversation_id: conversationId, agent_name: name }),
        }).catch(() => {});
      }
    }).catch(() => {});
  } else {
    fetch(API, { method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'interrupt', conversation_id: conversationId, agent_name: target || '' }),
    }).catch(e => addMsg('error', 'Interrupt failed: ' + e.message));
  }
}

function cmdAgentBtw(target, question) {
  if (!conversationId) { addMsg('system', 'No active conversation.'); return; }
  const agent = target || '';
  const isAll = agent.toUpperCase() === 'ALL';
  addMsg('user', question, { source: { type: 'user', name: '', target_agent: agent || '', btw: true } });
  fetch(API, { method: 'POST', headers: getAuthHeaders(),
    body: JSON.stringify({
      action: 'btw', conversation_id: conversationId,
      agent_name: isAll ? 'ALL' : agent, message: question,
    }),
  }).catch(e => addMsg('error', 'BTW failed: ' + e.message));
}

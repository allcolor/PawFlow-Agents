// ── Context commands ────────────────────────────────────────────
// /compact, /rebuild, /restart_from, /context, /summary, /resume
// Loaded before commands.js — all functions are global.

function cmdRestartFrom(text, parts) {
  let restartAgent = '';
  let restartN = 5;
  for (let i = 1; i < parts.length; i++) {
    const v = parseInt(parts[i]);
    if (!isNaN(v)) { restartN = v; }
    else { restartAgent = stripTarget(parts[i]); }
  }
  if (!conversationId) { addMsg('system', t('noConv')); return true; }
  if (contextOpInProgress) { addMsg('system', t('contextOpBusy')); return true; }
  contextOpInProgress = true;
  showContextOp('Restarting');
  const restartBody = { action: 'restart_from', conversation_id: conversationId, keep_last: restartN };
  if (restartAgent) restartBody.agent_name = restartAgent;
  fetch(API, {
    method: 'POST', headers: getAuthHeaders(),
    body: JSON.stringify(restartBody),
    credentials: 'same-origin',
  }).then(r => r.json()).then(data => {
    if (data.error) { addMsg('error', data.error); hideContextOp(); contextOpInProgress = false; }
  }).catch(e => { addMsg('error', e.message); hideContextOp(); contextOpInProgress = false; })
    .finally(() => { hideContextOp(); contextOpInProgress = false; });
  return true;
}

function cmdResume(text) {
  const rargs = parseQuotedArgs(text);
  const target = resolveAgentName(stripTarget(rargs[1] || ''));
  if (!target) { addMsg('system', 'Usage: /resume @<agent|ALL>'); return true; }
  const resumeMsg = rargs.slice(2).join(' ') || 'Continue from where you left off.';
  if (target.toUpperCase() === 'ALL') { cmdAgentMsgAll(resumeMsg); }
  else { cmdAgentMsg(target, resumeMsg); }
  return true;
}

function cmdSummary(text, parts) {
  let summaryAgent = '';
  let summaryTokens = 500;
  for (let i = 1; i < parts.length; i++) {
    const v = parseInt(parts[i]);
    if (!isNaN(v)) { summaryTokens = v; }
    else { summaryAgent = stripTarget(parts[i]); }
  }
  if (!conversationId) { addMsg('system', t('noConv')); return true; }
  if (contextOpInProgress) { addMsg('system', t('contextOpBusy')); return true; }
  contextOpInProgress = true;
  const label = summaryAgent ? 'Summarizing (' + summaryAgent + ')' : 'Summarizing';
  showContextOp(label);
  const summaryBody = { action: 'resume_conversation', conversation_id: conversationId, max_tokens: summaryTokens };
  if (summaryAgent) summaryBody.agent_name = summaryAgent;
  fetch(API, {
    method: 'POST', headers: getAuthHeaders(),
    body: JSON.stringify(summaryBody),
    credentials: 'same-origin',
  }).then(r => r.json()).then(data => {
    if (data.error) { addMsg('error', data.error); hideContextOp(); contextOpInProgress = false; }
  }).catch(e => { addMsg('error', e.message); hideContextOp(); contextOpInProgress = false; });
  return true;
}

function cmdCompactCmd(text, parts) {
  if (contextOpInProgress) { addMsg('system', t('contextOpBusy')); return true; }
  cmdCompact(stripTarget(parts[1] || ''));
  return true;
}

function cmdRebuildCmd(text, parts) {
  if (contextOpInProgress) { addMsg('system', t('contextOpBusy')); return true; }
  cmdRebuild(stripTarget(parts[1] || ''));
  return true;
}

function cmdContextCmd(text, parts) {
  cmdShowContext(stripTarget(parts[1] || ''));
  return true;
}

function cmdCompact(agentName) {
  if (!conversationId) { addMsg('system', 'No active conversation'); return; }
  contextOpInProgress = true;
  const _compactLabel = agentName || selectedAgent || '';
  const label = _compactLabel ? 'Compacting (' + _compactLabel + ')' : 'Compacting';
  showContextOp(label);
  const body = { action: 'compact', conversation_id: conversationId };
  const _compactAgent = (agentName && agentName.toLowerCase() === 'shared') ? '' : (agentName || selectedAgent || '');
  if (_compactAgent) body.agent_name = _compactAgent;
  fetch(API, {
    method: 'POST', headers: getAuthHeaders(),
    body: JSON.stringify(body),
  }).then(r => r.json()).then(data => {
    if (data.error) {
      addMsg('error', 'Compaction failed: ' + data.error);
      hideContextOp(); contextOpInProgress = false;
    }
  }).catch(e => {
    addMsg('error', 'Compaction failed: ' + e.message);
    hideContextOp(); contextOpInProgress = false;
  });
}

function cmdRebuild(agentName) {
  if (!conversationId) { addMsg('system', t('noConv')); return; }
  contextOpInProgress = true;
  const label = agentName ? 'Rebuilding (' + agentName + ')' : 'Rebuilding';
  showContextOp(label);
  const body = { action: 'rebuild', conversation_id: conversationId };
  const _rebuildAgent = (agentName && agentName.toLowerCase() === 'shared') ? '' : (agentName || selectedAgent || '');
  if (_rebuildAgent) body.agent_name = _rebuildAgent;
  fetch(API, {
    method: 'POST', headers: getAuthHeaders(),
    body: JSON.stringify(body),
  }).then(r => r.json()).then(data => {
    if (data.error) { addMsg('error', 'Rebuild failed: ' + data.error); hideContextOp(); contextOpInProgress = false; }
  }).catch(e => { addMsg('error', 'Rebuild failed: ' + e.message); hideContextOp(); contextOpInProgress = false; });
}


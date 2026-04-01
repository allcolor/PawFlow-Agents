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
  const restartParams = { keep_last: restartN };
  if (restartAgent) restartParams.agent_name = restartAgent;
  action$('restart_from', restartParams).subscribe(data => {
    if (data.error) { addMsg('error', data.error); }
    hideContextOp(); contextOpInProgress = false;
  });
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
  const summaryParams = { max_tokens: summaryTokens };
  if (summaryAgent) summaryParams.agent_name = summaryAgent;
  action$('resume_conversation', summaryParams).subscribe(data => {
    if (data.error) { addMsg('error', data.error); hideContextOp(); contextOpInProgress = false; }
  });
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
  const params = {};
  const _compactAgent = (agentName && agentName.toLowerCase() === 'shared') ? '' : (agentName || selectedAgent || '');
  if (_compactAgent) params.agent_name = _compactAgent;
  action$('compact', params).subscribe(data => {
    if (data.error) {
      addMsg('error', 'Compaction failed: ' + data.error);
      hideContextOp(); contextOpInProgress = false;
    }
  });
}

function cmdRebuild(agentName) {
  if (!conversationId) { addMsg('system', t('noConv')); return; }
  contextOpInProgress = true;
  const label = agentName ? 'Rebuilding (' + agentName + ')' : 'Rebuilding';
  showContextOp(label);
  const params = {};
  const _rebuildAgent = (agentName && agentName.toLowerCase() === 'shared') ? '' : (agentName || selectedAgent || '');
  if (_rebuildAgent) params.agent_name = _rebuildAgent;
  action$('rebuild', params).subscribe(data => {
    if (data.error) { addMsg('error', 'Rebuild failed: ' + data.error); hideContextOp(); contextOpInProgress = false; }
  });
}

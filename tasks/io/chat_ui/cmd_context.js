// ── Context commands ────────────────────────────────────────────
// /compact, /git-prune, /rebuild, /restart_from, /context, /summary, /resume
// Loaded before commands.js — all functions are global.

function cmdRestartFrom(text, parts) {
  let restartTarget = '';
  for (let i = 1; i < parts.length; i++) {
    const part = stripTarget(parts[i]);
    if (!part || parts[i].startsWith('@')) continue;
    restartTarget = part;
  }
  if (!conversationId) { addMsg('system', t('noConv')); return true; }
  if (!restartTarget) { addMsg('system', 'Usage: /restart_from <index|msg_id>'); return true; }
  showContextOp(t('contextRestarting'));
  let restartPromptText = '';
  let restartParams = /^\d+$/.test(restartTarget)
    ? { restart_index: parseInt(restartTarget, 10) }
    : { msg_id: restartTarget };
  if (!/^\d+$/.test(restartTarget)) {
    const messages = Array.from(document.querySelectorAll('#messages .msg[data-msgid]'));
    const msg = messages.find(el => el.dataset.msgid === restartTarget);
    if (msg && msg.dataset.messageRole === 'user' && typeof restartParamsForMessage === 'function') {
      restartParams = restartParamsForMessage(msg) || restartParams;
      if (typeof messageTextForAction === 'function') restartPromptText = messageTextForAction(msg);
    }
  }
  action$('restart_from', restartParams).subscribe(data => {
    if (data.error) addMsg('error', data.error);
    else if (restartPromptText && typeof setPromptTextForRestart === 'function') setPromptTextForRestart(restartPromptText);
  });
  return true;
}

function cmdResume(text) {
  const rargs = parseQuotedArgs(text);
  const target = resolveAgentName(stripTarget(rargs[1] || ''));
  if (!target) { addMsg('system', t('resumeUsage')); return true; }
  const resumeMsg = rargs.slice(2).join(' ') || t('continueFromLast');
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
  const label = summaryAgent ? t('summarizingAgent', { agent: summaryAgent }) : t('summarizing');
  showContextOp(label);
  const summaryParams = { max_tokens: summaryTokens };
  if (summaryAgent) summaryParams.agent_name = summaryAgent;
  action$('resume_conversation', summaryParams).subscribe(data => {
    if (data.error) addMsg('error', data.error);
    hideContextOp();
  });
  return true;
}

function cmdCompactCmd(text, parts) {
  cmdCompact(stripTarget(parts[1] || ''));
  return true;
}

function cmdGitPruneCmd(text, parts) {
  cmdGitPrune();
  return true;
}

function cmdRebuildCmd(text, parts) {
  cmdRebuild(stripTarget(parts[1] || ''));
  return true;
}

function cmdContextCmd(text, parts) {
  cmdShowContext(stripTarget(parts[1] || ''));
  return true;
}

function cmdCompact(agentName) {
  if (!conversationId) { addMsg('system', t('noConv')); return; }
  const _compactLabel = agentName || selectedAgent || '';
  const label = _compactLabel ? t('compactingAgent', { agent: _compactLabel }) : t('compacting');
  showContextOp(label);
  const params = {};
  const _compactAgent = (agentName && agentName.toLowerCase() === 'shared') ? '' : (agentName || selectedAgent || '');
  if (_compactAgent) params.agent_name = _compactAgent;
  action$('compact', params).subscribe(data => {
    if (data.error) addMsg('error', t('compactionFailed', { error: data.error }));
    hideContextOp();
  });
}

function cmdGitPrune() {
  if (!conversationId) { addMsg('system', t('noConv')); return; }
  showContextOp('Pruning conversation Git history...');
  action$('git_prune', {}).subscribe(data => {
    if (data.error) addMsg('error', data.error);
    hideContextOp();
  });
}

function cmdRebuild(agentName) {
  if (!conversationId) { addMsg('system', t('noConv')); return; }
  const label = agentName ? t('rebuildingAgent', { agent: agentName }) : t('rebuildingShort');
  showContextOp(label);
  const params = {};
  const _rebuildAgent = (agentName && agentName.toLowerCase() === 'shared') ? '' : (agentName || selectedAgent || '');
  if (_rebuildAgent) params.agent_name = _rebuildAgent;
  action$('rebuild', params).subscribe(data => {
    if (data.error) addMsg('error', t('rebuildFailed', { error: data.error }));
    hideContextOp();
  });
}

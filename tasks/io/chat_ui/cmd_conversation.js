// ── Conversation commands ───────────────────────────────────────
// All server calls use action$() / fireAction() from rxbus.js.

function cmdNew() { newChat(); return true; }
function cmdConv() { loadConversations(); return true; }

function cmdHistory(text, parts) {
  if (!conversationId) { addMsg('system', t('noConv')); return true; }
  const n = parseInt(parts[1]) || 50;
  const offset = parseInt(parts[2]) || 0;
  action$('load_history', { conversation_id: conversationId, limit: n, offset })
    .subscribe(data => {
      if (data.error) { addMsg('error', data.error); return; }
      const msgs = data.messages || [];
      for (const m of msgs) {
        let content = m.content || '';
        if ((m.type === 'assistant' || m.role === 'assistant') && typeof content === 'string')
          content = content.replace(/^\[[^\]]+\]:\s*/, '');
        addMsg(m.type || m.role, content, m);
      }
      addMsg('system', t('messagesLoaded', { n: msgs.length }));
    });
  return true;
}

function cmdExport(text, parts, cmd) {
  if (!conversationId) { addMsg('system', t('noConv')); return true; }
  const fmt = parts[1] || 'markdown';
  action$('export', { conversation_id: conversationId, format: fmt })
    .subscribe(data => {
      if (data.error) { addMsg('error', data.error); return; }
      if (data.url) {
        const a = document.createElement('a');
        a.href = data.url; a.download = data.filename || 'export'; a.click();
        addMsg('system', t('exportedFile', { file: data.filename || data.url }));
      }
    });
  return true;
}

function cmdRename(text, parts, cmd) {
  if (!conversationId) { addMsg('system', t('noConv')); return true; }
  const title = text.slice(cmd.length).trim();
  if (!title) { addMsg('system', t('usageRename')); return true; }
  action$('set_conv_title', { conversation_id: conversationId, title })
    .subscribe(data => {
      if (data.error) addMsg('error', data.error);
      else addMsg('system', t('renamedTo', { title: title }));
    });
  return true;
}

// Invoked from the locked-history banner's Unlock button.
function encryptUnlockCurrent() {
  cmdEncrypt('/encrypt unlock', ['/encrypt', 'unlock']);
}

function cmdEncrypt(text, parts) {
  if (!conversationId) { addMsg('system', t('noConv')); return true; }
  const cid = conversationId;
  const sub = (parts[1] || 'status').toLowerCase();
  const refreshList = () => { if (typeof loadConversations === 'function') loadConversations(); };
  const reportErr = (data) => {
    if (data.error === 'wrong_passphrase') { addMsg('error', t('wrongPassphrase')); return true; }
    if (data.error === 'locked') { addMsg('error', t('unlockBeforeDisable')); return true; }
    if (data.error) { addMsg('error', data.error); return true; }
    return false;
  };
  if (sub === 'status') {
    action$('conv_encrypt_status', { conversation_id: cid }).subscribe(data => {
      if (reportErr(data)) return;
      addMsg('system', t('encryptionStatusLine', { state: data.state }));
    });
  } else if (sub === 'on' || sub === 'enable') {
    const p1 = prompt(t('setPassphrasePrompt'));
    if (!p1) return true;
    const p2 = prompt(t('confirmPassphrase'));
    if (p1 !== p2) { addMsg('error', t('passphraseMismatch')); return true; }
    addMsg('system', t('encryptionMigrating'));
    action$('conv_encrypt_enable', { conversation_id: cid, passphrase: p1 }).subscribe(data => {
      if (reportErr(data)) return;
      addMsg('system', t('encryptionEnabled'));
      refreshList();
    });
  } else if (sub === 'unlock') {
    const pw = prompt(t('enterPassphrase'));
    if (!pw) return true;
    action$('conv_encrypt_unlock', { conversation_id: cid, passphrase: pw }).subscribe(data => {
      if (reportErr(data)) return;
      addMsg('system', t('unlocked'));
      if (typeof loadHistory === 'function') loadHistory(cid);
    });
  } else if (sub === 'lock') {
    action$('conv_encrypt_lock', { conversation_id: cid }).subscribe(data => {
      if (reportErr(data)) return;
      addMsg('system', t('locked'));
    });
  } else if (sub === 'off' || sub === 'disable') {
    if (!confirm(t('confirmDisableEncryption'))) return true;
    action$('conv_encrypt_disable', { conversation_id: cid }).subscribe(data => {
      if (reportErr(data)) return;
      addMsg('system', t('encryptionDisabled'));
      refreshList();
    });
  } else if (sub === 'passwd' || sub === 'password') {
    const oldp = prompt(t('enterPassphrase'));
    if (!oldp) return true;
    const newp = prompt(t('setPassphrasePrompt'));
    if (!newp) return true;
    action$('conv_encrypt_passwd', { conversation_id: cid, old_passphrase: oldp, new_passphrase: newp }).subscribe(data => {
      if (reportErr(data)) return;
      addMsg('system', t('passphraseChanged'));
    });
  } else if (sub === 'escrow') {
    const onoff = (parts[2] || '').toLowerCase();
    if (onoff === 'off') {
      action$('conv_encrypt_remove_escrow', { conversation_id: cid }).subscribe(data => {
        if (reportErr(data)) return; addMsg('system', t('ok'));
      });
    } else {
      const rp = prompt(t('setRecoveryPrompt'));
      if (!rp) return true;
      action$('conv_encrypt_set_escrow', { conversation_id: cid, recovery_passphrase: rp }).subscribe(data => {
        if (reportErr(data)) return; addMsg('system', t('recoveryAdded'));
      });
    }
  } else if (sub === 'recover') {
    const rp = prompt(t('enterRecovery'));
    if (!rp) return true;
    action$('conv_encrypt_recover', { conversation_id: cid, recovery_passphrase: rp }).subscribe(data => {
      if (reportErr(data)) return;
      addMsg('system', t('unlocked'));
      if (typeof loadHistory === 'function') loadHistory(cid);
    });
  } else {
    addMsg('error', t('usageEncrypt'));
  }
  return true;
}

function cmdDelete(text, parts) {
  const target = parts[1] || '';
  if (!target) { addMsg('system', t('usageDeleteConversation')); return true; }
  action$('delete_conversation', { conversation_id: target })
    .subscribe(data => {
      if (data.error) { addMsg('error', data.error); return; }
      addMsg('system', t('conversationDeletedShort', { id: target.slice(0, 8) }));
      if (conversationId === target) newChat();
      loadConversations();
    });
  return true;
}

function cmdDeleteMsg(text, parts) {
  if (!conversationId) { addMsg('system', t('noConv')); return true; }
  const idx = parseInt(parts[1]);
  if (isNaN(idx)) { addMsg('system', t('usageDeleteMessage')); return true; }
  action$('delete_message', { conversation_id: conversationId, index: idx })
    .subscribe(data => {
      if (data.error) addMsg('error', data.error);
      else addMsg('system', t('messageDeleted', { index: idx }));
    });
  return true;
}

function cmdSearch(text, parts, cmd) {
  if (!conversationId) { addMsg('system', t('noConv')); return true; }
  const query = text.slice(cmd.length).trim();
  if (!query) { addMsg('system', t('usageSearch')); return true; }
  action$('load_history', { conversation_id: conversationId, limit: 500, offset: 0 })
    .subscribe(data => {
      const messages = data.messages || [];
      const lq = query.toLowerCase();
      const found = [];
      for (const m of messages) {
        const content = m.content || '';
        if (typeof content === 'string' && content.toLowerCase().includes(lq))
          found.push('[' + (m.type || m.role || '?') + '] ' + content.slice(0, 100));
      }
      if (found.length) addMsg('system', t('matchesFound', { n: found.length }) + '\n' + found.slice(0, 20).join('\n'));
      else addMsg('system', t('noMatchesFound'));
    });
  return true;
}

function cmdClear() {
  const container = document.getElementById('messages');
  if (!container) return true;
  const knownTotal = serverMsgCount || currentOffset || document.querySelectorAll('#messages > .msg').length;
  _expectingClear = true;
  container.innerHTML = '';
  _expectingClear = false;
  _seenMsgIds.clear();
  if (typeof _liveCountedMsgIds !== 'undefined' && _liveCountedMsgIds.clear) _liveCountedMsgIds.clear();
  if (typeof _selectedMsgIds !== 'undefined' && _selectedMsgIds.clear) _selectedMsgIds.clear();
  _histTaskBlocks = {};
  clearAllStreams();
  currentOffset = 0;
  serverMsgCount = knownTotal;
  hasMoreMessages = knownTotal > 0;
  _updateLoadMoreBanner();
  return true;
}

function cmdClearStore(text, parts) {
  if (!conversationId) { addMsg('system', t('noConv')); return true; }
  const csArg = stripTarget((parts[1] || '').trim());
  const params = { conversation_id: conversationId };
  if (csArg && csArg.toUpperCase() === 'ALL') params.scope = 'all_agents';
  else if (csArg) params.agent_name = csArg;
  action$('clear_store', params).subscribe(data => {
    if (data.error) addMsg('error', data.error);
    else if (data.deleted !== undefined) addMsg('system', t('fileStoreDeleted', { n: data.deleted, scope: data.scope ? ' (' + data.scope + ')' : '' }));
  });
  return true;
}

function cmdUpload() {
  const fileInput = document.getElementById('fileInput');
  if (fileInput) fileInput.click();
  else addMsg('system', t('fileUploadUnavailable'));
  return true;
}

function cmdCopy(text, parts) {
  const msgs = document.querySelectorAll('.msg.assistant');
  if (!msgs.length) { addMsg('system', t('noResponsesToCopy')); return true; }
  const n = parseInt(parts[1]) || 1;
  const target = msgs[msgs.length - n];
  if (!target) { addMsg('system', t('onlyResponsesAvailable', { n: msgs.length })); return true; }
  const text_to_copy = target.textContent || '';
  navigator.clipboard.writeText(text_to_copy).then(() => {
    addMsg('system', t('copiedCharsToClipboard', { n: text_to_copy.length }));
  }).catch(e => addMsg('error', t('copyFailed', { error: e.message })));
  return true;
}

function cmdPaste() {
  navigator.clipboard.read().then(items => {
    for (const item of items) {
      if (item.types.includes('image/png')) {
        item.getType('image/png').then(blob => {
          const file = new File([blob], 'clipboard.png', { type: 'image/png' });
          handleFiles([file]);
          addMsg('system', t('imagePastedFromClipboard'));
        });
        return;
      }
    }
    navigator.clipboard.readText().then(text_content => {
      if (text_content) {
        document.getElementById('chatInput').value += text_content;
        addMsg('system', t('textPastedFromClipboard'));
      }
    });
  }).catch(e => addMsg('error', t('pasteFailed', { error: e.message })));
  return true;
}

function cmdDiff(text, parts) {
  const ref = parts.slice(1).join(' ') || '.';
  action$('fs_exec', { service: '', command: 'git diff ' + ref, timeout: 15 })
    .subscribe(data => {
      if (data.error) { addMsg('error', data.error); return; }
      const output = data.stdout || '';
      if (!output) { addMsg('system', t('noChanges')); return; }
      const lines = output.split('\n');
      const html = lines.map(function(l) {
        if (l.startsWith('+')) return '<span class="diff-add">' + escapeHtml(l) + '</span>';
        if (l.startsWith('-')) return '<span class="diff-del">' + escapeHtml(l) + '</span>';
        if (l.startsWith('@@')) return '<span class="diff-hunk">' + escapeHtml(l) + '</span>';
        return '<span class="diff-ctx">' + escapeHtml(l) + '</span>';
      }).join('\n');
      const el = addMsg('system', '');
      el.innerHTML = '<pre class="diff">' + html + '</pre>';
    });
  return true;
}

function cmdPlan(text, parts, cmd) {
  const arg = text.slice(cmd.length).trim();
  if (!arg || arg === 'list') {
    const panel = document.getElementById('plansPanel');
    if (panel.style.display === 'none') panel.style.display = 'block';
    loadPlans();
    if (arg === 'list') {
      action$('get_plans', { conversation_id: conversationId }).subscribe(data => {
        let planArr = Array.isArray(data.plans) ? data.plans : Object.values(data.plans || {});
        if (!planArr.length) { addMsg('system', t('noActivePlans')); return; }
        let lines = [t('plansHeader')];
        for (const p of planArr) {
          if (!p || !p.title) continue;
          const steps = p.steps || [];
          const done = steps.filter(s => s.status === 'done').length;
          const icon = {'pending_approval': '\u23F3', 'approved': '\u2705', 'in_progress': '\u25B6', 'completed': '\u2714', 'cancelled': '\u274C'}[p.status] || '\u2753';
          lines.push('  ' + icon + ' **' + p.title + '** (`' + (p.id || '?') + '`) \u2014 ' + p.status + ' \u2014 ' + t('planStepsDone', { done: done, total: steps.length }));
        }
        addMsg('system', lines.join('\n'));
      });
    }
    return true;
  }
  const planParts = arg.split(/\s+/);
  const subcmd = planParts[0].toLowerCase();
  if (['approve', 'cancel', 'delete', 'reset'].includes(subcmd)) {
    const planId = planParts[1];
    if (!planId) { addMsg('system', t('usagePlanSubcommand', { subcommand: subcmd })); return true; }
    const actionMap = { 'approve': 'approve_plan', 'cancel': 'cancel_plan', 'delete': 'delete_plan', 'reset': 'reset_plan' };
    planAction(actionMap[subcmd], planId);
    return true;
  }
  const planMsg = '[Create a structured plan using the create_plan tool. Analyze the request, identify steps, then call create_plan.]\n\n' + arg;
  addMsg('user', '/plan ' + arg);
  const body = { message: planMsg };
  if (conversationId) body.conversation_id = conversationId;
  fetch(API, { method: 'POST', headers: getAuthHeaders(), body: JSON.stringify(body) })
    .then(r => r.json())
    .then(data => { if (data.conversation_id && !conversationId) { conversationId = data.conversation_id; connectSSE(conversationId); } })
    .catch(e => addMsg('error', e.message));
  return true;
}

function cmdWatch() { addMsg('system', t('watchUnavailableWeb')); return true; }
function cmdClearFiles() { pendingFiles = []; addMsg('system', t('pendingAttachmentsCleared')); return true; }

function cmdRun(text, parts, cmd) {
  const command = text.slice(cmd.length).trim();
  if (!command) { addMsg('system', t('usageRun')); return true; }
  action$('fs_exec', { service: '', command, timeout: 30 }).subscribe(data => {
    if (data.error) { addMsg('error', data.error); return; }
    const out = (data.stdout || '') + (data.stderr ? '\n[stderr] ' + data.stderr : '');
    addMsg('system', '$ ' + command + ' (exit ' + (data.returncode || 0) + ')\n' + out);
  });
  return true;
}

function cmdLoop(text, parts) {
  if (!conversationId) { addMsg('system', t('noConv')); return true; }
  const loopArg = parts[1] || '';
  if (loopArg === 'list') {
    action$('loop_list', { conversation_id: conversationId }).subscribe(data => {
      const loops = data.loops || [];
      if (loops.length === 0) addMsg('system', t('noActiveLoops'));
      else {
        const lines = loops.map(l => l.key + ' — every ' + l.interval_seconds + 's: ' + (l.prompt || '?'));
        addMsg('system', t('activeLoopsHeader') + '\n' + lines.join('\n'));
      }
    });
    return true;
  }
  if (loopArg === 'stop') {
    const loopKey = parts[2] || '';
    if (!loopKey) { addMsg('system', t('usageLoopStop')); return true; }
    action$('loop_stop', { key: loopKey }).subscribe(data => {
      addMsg('system', data.stopped ? t('loopStopped', { key: loopKey }) : t('loopNotFound', { key: loopKey }));
    });
    return true;
  }
  const _units = {s:1, m:60, h:3600, d:86400};
  let intervalSec = 0;
  const acMatch = loopArg.match(/^(\d+)(?:-(\d+))?\/(\d*)([smhd])$/);
  if (acMatch) {
    const countMin = parseInt(acMatch[1]);
    const durationNum = parseInt(acMatch[3] || '1');
    const period = durationNum * _units[acMatch[4]];
    intervalSec = Math.floor(period / countMin);
  } else {
    const simpleMatch = loopArg.match(/^(\d+)([smhd])$/);
    if (simpleMatch) intervalSec = parseInt(simpleMatch[1]) * _units[simpleMatch[2]];
  }
  if (!intervalSec || intervalSec < 5) {
    addMsg('system', t('usageLoopInterval'));
    return true;
  }
  const loopPrompt = parts.slice(2).join(' ').trim();
  if (!loopPrompt) { addMsg('system', t('usageLoop')); return true; }
  action$('loop_start', { conversation_id: conversationId, interval_seconds: intervalSec, prompt: loopPrompt })
    .subscribe(data => {
      if (data.started) addMsg('system', t('loopStarted', { interval: intervalSec, prompt: loopPrompt, key: data.key }));
      else addMsg('error', data.error || t('loopStartFailed'));
    });
  return true;
}

function cmdBatch(text) {
  const batchText = text.replace(/^\/batch\s*/, '').trim();
  if (!batchText) { addMsg('system', t('usageBatch')); return true; }
  let batchFiles = '';
  let batchInstruction = batchText;
  const filesMatch = batchText.match(/--files\s+(\S+)/);
  if (filesMatch) { batchFiles = filesMatch[1]; batchInstruction = batchText.replace(/--files\s+\S+/, '').trim(); }
  const batchMsg = '[System: BATCH MODE — Apply the following change across multiple files in parallel.\n'
    + 'Instruction: ' + batchInstruction + '\n'
    + (batchFiles ? 'File pattern: ' + batchFiles + '\n' : '')
    + 'Steps:\n1. Use glob(...) or grep(...) to find all matching files\n'
    + '2. Split files into groups of 3-5\n3. Use delegate to process each group in parallel\n'
    + '4. Report a summary of all changes made\nUse the current agent for each sub-task.]';
  sendMessage(batchMsg);
  return true;
}

function cmdSchedulesList() {
  if (!conversationId) { addMsg('system', t('noConv')); return; }
  action$('list_schedules', { conversation_id: conversationId }).subscribe(data => {
    const scheds = data.schedules || [];
    if (scheds.length === 0) addMsg('system', t('noScheduledRechecks'));
    else {
      const lines = scheds.map(s => {
        const dt = new Date(s.recheck_at * 1000).toLocaleString();
        return '\u2022 ' + dt + ' \u2014 ' + (s.reason || t('noReasonParen'));
      });
      addMsg('system', t('scheduledRechecksHeader') + '\n' + lines.join('\n'));
    }
  });
}

function cmdSchedulesDel() {
  if (!conversationId) { addMsg('system', t('noConv')); return; }
  action$('delete_schedule', { conversation_id: conversationId }).subscribe(data => {
    addMsg('system', data.cancelled ? t('scheduleCancelled') : t('noScheduleToCancel'));
  });
}

function cmdSchedulesAdd(dateStr, reason) {
  if (!conversationId) { addMsg('system', t('noConv')); return; }
  if (!/^\d{14}$/.test(dateStr)) { addMsg('system', t('invalidScheduleDateFormat')); return; }
  action$('add_schedule', { conversation_id: conversationId, at: dateStr, reason: reason || t('manualSchedule') })
    .subscribe(data => {
      if (data.error) { addMsg('error', data.error); return; }
      const dt = new Date(data.at * 1000).toLocaleString();
      addMsg('system', t('scheduleAdded', { date: dt }));
    });
}

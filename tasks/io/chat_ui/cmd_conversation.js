// ── Conversation commands ───────────────────────────────────────
// /new, /conv, /history, /export, /rename, /delete, /delete-msg, /search,
// /clear, /clear-store, /upload, /copy, /paste, /diff, /plan, /loop,
// /batch, /watch, /clear-files, /run
// Loaded before commands.js — all functions are global.

function cmdNew() {
  newChat();
  return true;
}

function cmdConv() {
  loadConversations();
  return true;
}

function cmdHistory(text, parts) {
  if (!conversationId) { addMsg('system', t('noConv')); return true; }
  const n = parseInt(parts[1]) || 50;
  const offset = parseInt(parts[2]) || 0;
  fetch(API, {
    method: 'POST', headers: getAuthHeaders(),
    body: JSON.stringify({ action: 'load_history', conversation_id: conversationId, limit: n, offset }),
  }).then(r => r.json()).then(data => {
    if (data.error) { addMsg('error', data.error); }
    else {
      const msgs = data.messages || [];
      for (const m of msgs) {
        let content = m.content || '';
        if ((m.type === 'assistant' || m.role === 'assistant') && typeof content === 'string') {
          content = content.replace(/^\[[^\]]+\]:\s*/, '');
        }
        addMsg(m.type || m.role, content, m);
      }
      addMsg('system', msgs.length + ' message(s) loaded.');
    }
  }).catch(e => addMsg('error', 'Failed: ' + e.message));
  return true;
}

function cmdExport(text, parts, cmd) {
  if (!conversationId) { addMsg('system', t('noConv')); return true; }
  const fmt = parts[1] || 'markdown';
  fetch(API, {
    method: 'POST', headers: getAuthHeaders(),
    body: JSON.stringify({ action: 'export', conversation_id: conversationId, format: fmt }),
  }).then(r => r.json()).then(data => {
    if (data.error) { addMsg('error', data.error); }
    else if (data.url) {
      const a = document.createElement('a');
      a.href = data.url;
      a.download = data.filename || 'export';
      a.click();
      addMsg('system', 'Exported: ' + (data.filename || data.url));
    }
  }).catch(e => addMsg('error', 'Export failed: ' + e.message));
  return true;
}

function cmdRename(text, parts, cmd) {
  if (!conversationId) { addMsg('system', t('noConv')); return true; }
  const title = text.slice(cmd.length).trim();
  if (!title) { addMsg('system', 'Usage: /rename <new title>'); return true; }
  fetch(API, {
    method: 'POST', headers: getAuthHeaders(),
    body: JSON.stringify({ action: 'set_conv_title', conversation_id: conversationId, title }),
  }).then(r => r.json()).then(data => {
    if (data.error) { addMsg('error', data.error); }
    else { addMsg('system', 'Renamed to: ' + title); }
  }).catch(e => addMsg('error', 'Rename failed: ' + e.message));
  return true;
}

function cmdDelete(text, parts) {
  const target = parts[1] || '';
  if (!target) { addMsg('system', 'Usage: /delete <conversation_id>'); return true; }
  fetch(API, {
    method: 'POST', headers: getAuthHeaders(),
    body: JSON.stringify({ action: 'delete_conversation', conversation_id: target }),
  }).then(r => r.json()).then(data => {
    if (data.error) { addMsg('error', data.error); }
    else if (data.deleted) {
      addMsg('system', 'Deleted ' + target.slice(0, 8));
      if (conversationId === target) { newChat(); }
    }
  }).catch(e => addMsg('error', 'Delete failed: ' + e.message));
  return true;
}

function cmdDeleteMsg(text, parts) {
  if (!conversationId) { addMsg('system', t('noConv')); return true; }
  const idx = parseInt(parts[1]);
  if (isNaN(idx)) { addMsg('system', 'Usage: /delete-msg <index>'); return true; }
  fetch(API, {
    method: 'POST', headers: getAuthHeaders(),
    body: JSON.stringify({ action: 'delete_message', conversation_id: conversationId, index: idx }),
  }).then(r => r.json()).then(data => {
    if (data.error) { addMsg('error', data.error); }
    else { addMsg('system', 'Message ' + idx + ' deleted'); }
  }).catch(e => addMsg('error', 'Failed: ' + e.message));
  return true;
}

function cmdSearch(text, parts, cmd) {
  if (!conversationId) { addMsg('system', t('noConv')); return true; }
  const query = text.slice(cmd.length).trim();
  if (!query) { addMsg('system', 'Usage: /search <query>'); return true; }
  fetch(API, {
    method: 'POST', headers: getAuthHeaders(),
    body: JSON.stringify({ action: 'load_history', conversation_id: conversationId, limit: 500, offset: 0 }),
  }).then(r => r.json()).then(data => {
    const messages = data.messages || [];
    const lq = query.toLowerCase();
    const found = [];
    for (const m of messages) {
      const content = m.content || '';
      if (typeof content === 'string' && content.toLowerCase().includes(lq)) {
        found.push('[' + (m.type || m.role || '?') + '] ' + content.slice(0, 100));
      }
    }
    if (found.length) {
      addMsg('system', 'Found ' + found.length + ' match(es):\n' + found.slice(0, 20).join('\n'));
    } else {
      addMsg('system', 'No matches found.');
    }
  }).catch(e => addMsg('error', 'Search failed: ' + e.message));
  return true;
}

function cmdClear() {
  document.getElementById('messages').innerHTML = '';
  return true;
}

function cmdClearStore(text, parts) {
  if (!conversationId) { addMsg('system', 'No active conversation'); return true; }
  const csArg = stripTarget((parts[1] || '').trim());
  const csPayload = {action: 'clear_store', conversation_id: conversationId};
  if (csArg && csArg.toUpperCase() === 'ALL') {
    csPayload.scope = 'all_agents';
  } else if (csArg) {
    csPayload.agent_name = csArg;
  }
  fetch(API, {
    method: 'POST', headers: getAuthHeaders(),
    body: JSON.stringify(csPayload),
  }).then(r => r.json()).then(r => {
    if (r && r.deleted !== undefined) {
      addMsg('system', 'FileStore: deleted ' + r.deleted + ' file(s)' + (r.scope ? ' (' + r.scope + ')' : ''));
    } else if (r && r.error) {
      addMsg('error', r.error);
    }
  }).catch(e => addMsg('error', 'clear-store failed: ' + e.message));
  return true;
}

function cmdUpload() {
  const fileInput = document.getElementById('fileInput');
  if (fileInput) { fileInput.click(); }
  else { addMsg('system', 'File upload not available. Drag & drop files into the chat.'); }
  return true;
}

function cmdCopy(text, parts) {
  const msgs = document.querySelectorAll('.msg.assistant');
  if (!msgs.length) { addMsg('system', 'No responses to copy.'); return true; }
  const n = parseInt(parts[1]) || 1;
  const target = msgs[msgs.length - n];
  if (!target) { addMsg('system', 'Only ' + msgs.length + ' responses available.'); return true; }
  const text_to_copy = target.textContent || '';
  navigator.clipboard.writeText(text_to_copy).then(() => {
    addMsg('system', 'Copied ' + text_to_copy.length + ' chars to clipboard.');
  }).catch(e => addMsg('error', 'Copy failed: ' + e.message));
  return true;
}

function cmdPaste() {
  navigator.clipboard.read().then(items => {
    for (const item of items) {
      if (item.types.includes('image/png')) {
        item.getType('image/png').then(blob => {
          const reader = new FileReader();
          reader.onload = function() {
            const b64 = reader.result.split(',')[1];
            pendingFiles.push({ filename: 'clipboard.png', mime_type: 'image/png', data: b64 });
            addMsg('system', 'Image pasted from clipboard (' + pendingFiles.length + ' file(s) queued).');
          };
          reader.readAsDataURL(blob);
        });
        return;
      }
    }
    navigator.clipboard.readText().then(text_content => {
      if (text_content) {
        document.getElementById('chatInput').value += text_content;
        addMsg('system', 'Text pasted from clipboard.');
      }
    });
  }).catch(e => addMsg('error', 'Paste failed: ' + e.message));
  return true;
}

function cmdDiff(text, parts) {
  const ref = parts.slice(1).join(' ') || '.';
  fetch(API, {
    method: 'POST', headers: getAuthHeaders(),
    body: JSON.stringify({ action: 'fs_exec', service: '', command: 'git diff ' + ref, timeout: 15 }),
  }).then(r => r.json()).then(data => {
    const output = data.stdout || '';
    if (!output) { addMsg('system', 'No changes.'); }
    else {
      const lines = output.split('\n');
      const html = lines.map(function(l) {
        if (l.startsWith('+')) return '<span class="diff-add">' + escapeHtml(l) + '</span>';
        if (l.startsWith('-')) return '<span class="diff-del">' + escapeHtml(l) + '</span>';
        if (l.startsWith('@@')) return '<span class="diff-hunk">' + escapeHtml(l) + '</span>';
        return '<span class="diff-ctx">' + escapeHtml(l) + '</span>';
      }).join('\n');
      const el = addMsg('system', '');
      el.innerHTML = '<pre class="diff">' + html + '</pre>';
    }
  }).catch(e => addMsg('error', 'Diff failed: ' + e.message));
  return true;
}

function cmdPlan(text, parts, cmd) {
  const arg = text.slice(cmd.length).trim();
  if (!arg || arg === 'list') {
    const panel = document.getElementById('plansPanel');
    if (panel.style.display === 'none') {
      panel.style.display = 'block';
    }
    loadPlans();
    if (arg === 'list') {
      fetch(API, {
        method: 'POST', headers: getAuthHeaders(),
        body: JSON.stringify({ action: 'get_plans', conversation_id: conversationId }),
      }).then(r => r.json()).then(data => {
        let planArr = Array.isArray(data.plans) ? data.plans : Object.values(data.plans || {});
        if (!planArr.length) { addMsg('system', 'No active plans.'); return; }
        let lines = ['**Plans:**'];
        for (const p of planArr) {
          if (!p || !p.title) continue;
          const steps = p.steps || [];
          const done = steps.filter(s => s.status === 'done').length;
          const icon = {'pending_approval': '\u23F3', 'approved': '\u2705', 'in_progress': '\u25B6', 'completed': '\u2714', 'cancelled': '\u274C'}[p.status] || '\u2753';
          lines.push('  ' + icon + ' **' + p.title + '** (`' + (p.id || '?') + '`) \u2014 ' + p.status + ' \u2014 ' + done + '/' + steps.length + ' done');
        }
        addMsg('system', lines.join('\n'));
      }).catch(e => addMsg('error', 'Failed to list plans: ' + e.message));
    }
    return true;
  }
  const planParts = arg.split(/\s+/);
  const subcmd = planParts[0].toLowerCase();
  if (['approve', 'cancel', 'delete', 'reset'].includes(subcmd)) {
    const planId = planParts[1];
    if (!planId) { addMsg('system', 'Usage: /plan ' + subcmd + ' <plan_id>'); return true; }
    const actionMap = { 'approve': 'approve_plan', 'cancel': 'cancel_plan', 'delete': 'delete_plan', 'reset': 'reset_plan' };
    planAction(actionMap[subcmd], planId);
    return true;
  }
  const planMsg = '[Create a structured plan using the create_plan tool. Analyze the request, identify steps, then call create_plan.]\n\n' + arg;
  addMsg('user', '/plan ' + arg);
  const body = { message: planMsg };
  if (conversationId) body.conversation_id = conversationId;
  fetch(API, { method: 'POST', headers: getAuthHeaders(), body: JSON.stringify(body) })
    .then(r => r.json()).then(data => {
      if (data.conversation_id && !conversationId) { conversationId = data.conversation_id; connectSSE(conversationId); }
    }).catch(e => addMsg('error', e.message));
  return true;
}

function cmdWatch() {
  addMsg('system', '/watch is not available in the web UI. Use the CLI for file watching.');
  return true;
}

function cmdClearFiles() {
  pendingFiles = [];
  addMsg('system', 'Pending attachments cleared.');
  return true;
}

function cmdRun(text, parts, cmd) {
  const command = text.slice(cmd.length).trim();
  if (!command) { addMsg('system', 'Usage: /run <command>'); return true; }
  fetch(API, {
    method: 'POST', headers: getAuthHeaders(),
    body: JSON.stringify({ action: 'fs_exec', service: '', command, timeout: 30 }),
  }).then(r => r.json()).then(data => {
    if (data.error) { addMsg('error', data.error); }
    else {
      const out = (data.stdout || '') + (data.stderr ? '\n[stderr] ' + data.stderr : '');
      addMsg('system', '$ ' + command + ' (exit ' + (data.returncode || 0) + ')\n' + out);
    }
  }).catch(e => addMsg('error', 'Exec failed: ' + e.message));
  return true;
}

function cmdLoop(text, parts) {
  if (!conversationId) { addMsg('system', 'No active conversation'); return true; }
  const loopArg = parts[1] || '';
  if (loopArg === 'list') {
    fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({action: 'loop_list', conversation_id: conversationId}),
    }).then(r => r.json()).then(r => {
      const loops = r.loops || [];
      if (loops.length === 0) { addMsg('system', 'No active loops'); }
      else {
        const lines = loops.map(l => l.key + ' — every ' + l.interval_seconds + 's: ' + (l.prompt || '?'));
        addMsg('system', 'Active loops:\n' + lines.join('\n'));
      }
    }).catch(e => addMsg('error', e.message));
    return true;
  }
  if (loopArg === 'stop') {
    const loopKey = parts[2] || '';
    if (!loopKey) { addMsg('system', 'Usage: /loop stop <key>'); return true; }
    fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({action: 'loop_stop', key: loopKey}),
    }).then(r => r.json()).then(r => {
      addMsg('system', r.stopped ? 'Loop stopped: ' + loopKey : 'Loop not found: ' + loopKey);
    }).catch(e => addMsg('error', e.message));
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
    if (simpleMatch) {
      intervalSec = parseInt(simpleMatch[1]) * _units[simpleMatch[2]];
    }
  }
  if (!intervalSec || intervalSec < 5) {
    addMsg('system', 'Usage: /loop <interval> <prompt>\nInterval: 5m, 30s, 2h, 2-3/h, 1/30s, 6/1m (min 5s)');
    return true;
  }
  const loopPrompt = parts.slice(2).join(' ').trim();
  if (!loopPrompt) { addMsg('system', 'Usage: /loop <interval> <prompt>'); return true; }
  fetch(API, {
    method: 'POST', headers: getAuthHeaders(),
    body: JSON.stringify({action: 'loop_start', conversation_id: conversationId,
                          interval_seconds: intervalSec, prompt: loopPrompt}),
  }).then(r => r.json()).then(r => {
    if (r.started) {
      addMsg('system', 'Loop started: every ' + intervalSec + 's — ' + loopPrompt + '\nKey: ' + r.key);
    } else { addMsg('error', r.error || 'Failed to start loop'); }
  }).catch(e => addMsg('error', e.message));
  return true;
}

function cmdBatch(text) {
  const batchText = text.replace(/^\/batch\s*/, '').trim();
  if (!batchText) { addMsg('system', 'Usage: /batch <instruction> [--files <glob>]'); return true; }
  let batchFiles = '';
  let batchInstruction = batchText;
  const filesMatch = batchText.match(/--files\s+(\S+)/);
  if (filesMatch) {
    batchFiles = filesMatch[1];
    batchInstruction = batchText.replace(/--files\s+\S+/, '').trim();
  }
  const batchMsg = '[System: BATCH MODE — Apply the following change across multiple files in parallel.\n'
    + 'Instruction: ' + batchInstruction + '\n'
    + (batchFiles ? 'File pattern: ' + batchFiles + '\n' : '')
    + 'Steps:\n'
    + '1. Use glob(...) or grep(...) to find all matching files\n'
    + '2. Split files into groups of 3-5\n'
    + '3. Use spawn_agents to process each group in parallel — each agent applies the instruction to its files\n'
    + '4. Report a summary of all changes made\n'
    + 'Use the current agent for each sub-task. Work in parallel for speed.]';
  sendMessage(batchMsg);
  return true;
}

function cmdSchedulesList() {
  if (!conversationId) { addMsg('system', 'No active conversation'); return; }
  fetch(API, {
    method: 'POST', headers: getAuthHeaders(),
    body: JSON.stringify({ action: 'list_schedules', conversation_id: conversationId }),
  }).then(r => r.json()).then(data => {
    const scheds = data.schedules || [];
    if (scheds.length === 0) {
      addMsg('system', 'No scheduled rechecks for this conversation.');
    } else {
      const lines = scheds.map(s => {
        const dt = new Date(s.recheck_at * 1000).toLocaleString();
        return `\u2022 ${dt} \u2014 ${s.reason || '(no reason)'}`;
      });
      addMsg('system', 'Scheduled rechecks:\n' + lines.join('\n'));
    }
  }).catch(e => addMsg('error', 'Failed to list schedules: ' + e.message));
}

function cmdSchedulesDel() {
  if (!conversationId) { addMsg('system', 'No active conversation'); return; }
  fetch(API, {
    method: 'POST', headers: getAuthHeaders(),
    body: JSON.stringify({ action: 'delete_schedule', conversation_id: conversationId }),
  }).then(r => r.json()).then(data => {
    addMsg('system', data.cancelled ? 'Schedule cancelled.' : 'No schedule to cancel.');
  }).catch(e => addMsg('error', 'Failed to delete schedule: ' + e.message));
}

function cmdSchedulesAdd(dateStr, reason) {
  if (!conversationId) { addMsg('system', 'No active conversation'); return; }
  if (!/^\d{14}$/.test(dateStr)) {
    addMsg('system', 'Invalid date format. Use YYYYMMDDHHmmss (e.g. 20260312140000)');
    return;
  }
  fetch(API, {
    method: 'POST', headers: getAuthHeaders(),
    body: JSON.stringify({
      action: 'add_schedule', conversation_id: conversationId,
      at: dateStr, reason: reason || 'manual schedule',
    }),
  }).then(r => r.json()).then(data => {
    if (data.error) { addMsg('error', data.error); return; }
    const dt = new Date(data.at * 1000).toLocaleString();
    addMsg('system', 'Schedule added: ' + dt);
  }).catch(e => addMsg('error', 'Failed to add schedule: ' + e.message));
}

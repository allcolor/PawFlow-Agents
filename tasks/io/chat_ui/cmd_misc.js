// ── Misc commands ───────────────────────────────────────────────
// /help, /usage, /cost, /memory, /tools, /link, /model, /debug, /login,
// /call, /add-secret, /list-secrets, /add-variable, /list-variables,
// /autoconv, /schedules, /llm, /files, /flows, /tasks
// Loaded before commands.js — all functions are global.

function cmdHelp(topic) {
  if (!topic) {
    let lines = ['<b>Available commands:</b>', ''];
    const cmds = Object.keys(HELP_DATA).sort();
    for (const cmd of cmds) {
      const h = HELP_DATA[cmd];
      lines.push('<code>' + cmd + '</code> — ' + escapeHtml(h.short));
    }
    lines.push('', 'Type <code>/help &lt;command&gt;</code> for detailed documentation.');
    const el = addMsg('system', '');
    el.innerHTML = lines.join('<br>');
  } else {
    const helpParts = topic.split(/\s+/);
    if (helpParts[0] === 'call') {
      if (helpParts[1]) {
        cmdHelpTool(helpParts[1]);
      } else {
        cmdHelpToolList();
      }
      return;
    }
    const key = topic.startsWith('/') ? topic : '/' + topic;
    const h = HELP_DATA[key];
    if (!h) {
      addMsg('system', 'Unknown command: ' + key + '. Type /help to see available commands.');
      return;
    }
    let lines = [
      '<b>' + escapeHtml(key) + '</b>',
      '',
      '<b>Usage:</b> <code>' + escapeHtml(h.usage) + '</code>',
      '',
      '<pre style="margin:8px 0;white-space:pre-wrap;font-size:12px;background:rgba(255,255,255,0.05);padding:8px;border-radius:4px;">' + escapeHtml(h.detail) + '</pre>',
    ];
    const el = addMsg('system', '');
    el.innerHTML = lines.join('<br>');
  }
}

function cmdHelpToolList() {
  action$('get_tool_schemas', {}).subscribe(data => {
    const tools = (data.tools || []).sort((a, b) => a.name.localeCompare(b.name));
    let lines = ['<b>Available tools for /call:</b>', ''];
    for (const t of tools) {
      const params = t.parameters?.properties ? Object.keys(t.parameters.properties) : [];
      const paramStr = params.length ? '(' + params.join(', ') + ')' : '()';
      lines.push('  <code>' + t.name + paramStr + '</code> — ' + escapeHtml((t.description || '').substring(0, 80)));
    }
    lines.push('', 'Type <code>/help call &lt;toolname&gt;</code> for detailed parameter info.');
    const el = addMsg('system', '');
    el.innerHTML = lines.join('<br>');
  });
}

function cmdHelpTool(toolName) {
  action$('get_tool_schemas', {}).subscribe(data => {
    const tools = data.tools || [];
    const tool = tools.find(t => t.name === toolName);
    if (!tool) {
      const names = tools.map(t => t.name).sort();
      addMsg('system', 'Tool "' + toolName + '" not found. Available tools:\n' + names.map(n => '  \u2022 ' + n).join('\n'));
      return;
    }
    const params = tool.parameters || {};
    const props = params.properties || {};
    const required = params.required || [];
    let lines = [
      '<b>/call ' + tool.name + '</b>',
      '',
      '<span style="color:#a0a0c0">' + escapeHtml(tool.description) + '</span>',
      '',
      '<b>Parameters:</b>',
    ];
    for (const [key, schema] of Object.entries(props)) {
      const req = required.includes(key) ? '<span style="color:#e74c3c">*</span>' : '';
      const type = schema.type || '?';
      const desc = schema.description || '';
      lines.push('  <code>' + key + '</code> (' + type + ')' + req + ' — ' + escapeHtml(desc));
    }
    if (Object.keys(props).length === 0) {
      lines.push('  <i>(no parameters)</i>');
    }
    lines.push('', '<b>Example:</b>');
    const exArgs = [];
    for (const [key, schema] of Object.entries(props)) {
      if (required.includes(key)) {
        const ex = schema.type === 'string' ? '"..."' : schema.type === 'integer' ? '0' : schema.type === 'boolean' ? 'true' : '...';
        exArgs.push(key + '=' + ex);
      }
    }
    lines.push('  <code>/call ' + tool.name + '(' + exArgs.join(', ') + ')</code>');
    const el = addMsg('system', '');
    el.innerHTML = lines.join('<br>');
  });
}

function cmdUsage() {
  action$('get_usage', {}).subscribe(data => {
    const usage = data.usage || {};
    const lines = [];
    for (const [uid, u] of Object.entries(usage)) {
      const totalIn = (u.total_in || 0).toLocaleString();
      const totalOut = (u.total_out || 0).toLocaleString();
      lines.push(`**${uid}**: ${totalIn} in / ${totalOut} out`);
      const models = u.models || {};
      for (const [model, m] of Object.entries(models)) {
        lines.push(`  \u2022 ${model}: ${m.in.toLocaleString()} in / ${m.out.toLocaleString()} out`);
      }
    }
    if (lines.length === 0) { addMsg('system', 'No token usage recorded yet.'); }
    else { addMsg('system', 'Token usage:\n' + lines.join('\n')); }
  });
}

function cmdCost(text) {
  const cargs = parseQuotedArgs(text);
  const target = stripTarget(cargs[1] || '') || selectedAgent || 'ALL';

  // Fetch both user-level cost (existing) and conversation-level cost (new CostTracker)
  const userCost$ = action$('cost', { agent: target });
  const convCost$ = conversationId
    ? action$('get_cost', {})
    : rxjs.of(null);

  rxjs.forkJoin([userCost$, convCost$]).subscribe(([data, convData]) => {
    const lines = [];

    // Conversation cost (from CostTracker — model-aware pricing)
    if (convData && !convData.error && convData.total_usd) {
      lines.push('**Conversation cost: $' + convData.total_usd.toFixed(6) + '**');
      const byModel = convData.by_model || {};
      for (const [model, m] of Object.entries(byModel)) {
        const tokIn = (m.in || 0).toLocaleString();
        const tokOut = (m.out || 0).toLocaleString();
        const cacheR = m.cache_read || 0;
        const cacheW = m.cache_write || 0;
        let detail = '  ' + model + ': ' + tokIn + ' in / ' + tokOut + ' out — $' + (m.cost || 0).toFixed(6);
        if (cacheR || cacheW) {
          detail += ' (cache: ' + cacheR.toLocaleString() + ' read, ' + cacheW.toLocaleString() + ' write)';
        }
        lines.push(detail);
      }
      lines.push('');
    }

    // User-level cost (existing per-agent breakdown)
    const services = data.services || [];
    if (services.length === 0 && lines.length === 0) {
      addMsg('system', 'No usage data found.');
      return;
    }
    if (services.length > 0) {
      lines.push('**User total (all conversations):**');
      for (const s of services) {
        const svc = s.llm_service || '?';
        const model = s.model || '';
        const tokIn = (s.tokens_in || 0).toLocaleString();
        const tokOut = (s.tokens_out || 0).toLocaleString();
        const calls = s.calls || 0;
        let line = '  ' + svc + (model ? ' (' + model + ')' : '') + ': ' + tokIn + ' in / ' + tokOut + ' out (' + calls + ' calls)';
        if (s.cost !== undefined) {
          line += ' — $' + s.cost.toFixed(6);
        } else {
          line += ' — cost: not configured';
        }
        lines.push(line);
      }
      const totalIn = services.reduce((sum, s) => sum + (s.tokens_in || 0), 0);
      const totalOut = services.reduce((sum, s) => sum + (s.tokens_out || 0), 0);
      const totalCost = services.reduce((sum, s) => sum + (s.cost || 0), 0);
      lines.push('---');
      lines.push('Total: ' + totalIn.toLocaleString() + ' in / ' + totalOut.toLocaleString() + ' out'
        + (totalCost > 0 ? ' — $' + totalCost.toFixed(6) : ''));
    }
    addMsg('system', lines.join('\n'));
  });
  return true;
}

function cmdMemory(text, parts) {
  const sub = (parts[1] || '').toLowerCase();
  if (!sub || sub === 'panel') {
    cmdShowMemories();
  } else if (sub === 'list') {
    const agentFilter = parts[2] ? stripTarget(parts[2]) : null;
    cmdMemoryList(agentFilter);
  } else if (sub === 'del' || sub === 'delete') {
    const memId = parts[2];
    if (!memId) { addMsg('system', 'Usage: /memory del <memory_id>'); }
    else { cmdMemoryDel(memId); }
  } else if (sub === 'add') {
    const rest = text.replace(/^\/memory\s+add\s*/i, '');
    if (!rest.trim()) { addMsg('system', 'Usage: /memory add <text> [#tag1 #tag2] [@agent]'); return true; }
    const agentMatch = rest.match(/@(\S+)\s*$/);
    let agent = '';
    let memText = rest;
    if (agentMatch) { agent = agentMatch[1]; memText = rest.slice(0, agentMatch.index).trim(); }
    const tagMatches = memText.match(/#(\S+)/g) || [];
    const tags = tagMatches.map(t => t.slice(1));
    memText = memText.replace(/#\S+/g, '').trim();
    if (!memText) { addMsg('system', 'Usage: /memory add <text> [#tag1 #tag2] [@agent]'); return true; }
    action$('add_memory', { text: memText, tags, agent }).subscribe(data => {
      addMsg('system', 'Memory added (id: ' + (data.id || '?') + ', agent: ' + (data.agent || 'global') + ')');
    });
  } else if (sub === 'edit') {
    const memId = parts[2];
    const newText = parts.slice(3).join(' ');
    if (!memId || !newText) { addMsg('system', 'Usage: /memory edit <id> <new text>'); return true; }
    action$('edit_memory', { memory_id: memId, text: newText }).subscribe(data => {
      addMsg('system', data.updated ? 'Memory updated.' : 'Memory not found.');
    });
  } else if (sub === 'search') {
    const query = parts.slice(2).join(' ');
    if (!query) { addMsg('system', 'Usage: /memory search <query>'); return true; }
    cmdMemoryList(null, query);
  } else {
    addMsg('system', 'Usage: /memory [list [@agent] | add | edit | del | search | panel]');
  }
  return true;
}

function cmdMemoryList(agentFilter, searchQuery) {
  const params = {};
  if (agentFilter !== undefined && agentFilter !== null) params.agent_name = agentFilter;
  action$('list_memories', params).subscribe(data => {
    let mems = data.memories || [];
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      mems = mems.filter(m => m.text.toLowerCase().includes(q) || (m.tags || []).some(t => t.includes(q)));
    }
    if (mems.length === 0) {
      addMsg('system', 'No memories found.' + (searchQuery ? ' Try a different query.' : ''));
    } else {
      const lines = mems.map(m => {
        const agent = m.agent ? '\u{1F916} ' + m.agent : '\u{1F310} global';
        const tags = m.tags && m.tags.length ? ' [' + m.tags.join(', ') + ']' : '';
        return '\u2022 `' + m.id + '` ' + agent + tags + ' \u2014 ' + m.text;
      });
      const title = searchQuery ? 'Search results' : (agentFilter !== null && agentFilter !== undefined ? 'Memories for ' + (agentFilter || 'global') : 'All memories');
      addMsg('system', title + ' (' + mems.length + '):\n' + lines.join('\n'));
    }
  });
}

function cmdMemoryDel(memId) {
  action$('delete_memory', { memory_id: memId }).subscribe(data => {
    if (data.error) { addMsg('error', data.error); return; }
    addMsg('system', data.deleted ? `Memory ${memId} deleted.` : `Memory ${memId} not found.`);
  });
}

function cmdToolsList() {
  action$('list_tools', {}).subscribe(data => {
    const tools = data.tools || [];
    if (tools.length === 0) {
      addMsg('system', 'No dynamic tools installed. Use /install to add one.');
    } else {
      const lines = tools.map(t =>
        `\u2022 **${t.tool_name}** \u2014 ${t.description} (by ${t.owner})`
      );
      addMsg('system', 'Dynamic tools:\n' + lines.join('\n'));
    }
  });
}

function cmdUninstallTool(toolName) {
  action$('uninstall_tool', { tool_name: toolName }).subscribe(data => {
    if (data.error) { addMsg('error', data.error); return; }
    addMsg('system', data.uninstalled ? `Tool '${toolName}' uninstalled.` : `Tool '${toolName}' not found.`);
  });
}

function cmdLinkAccount(provider, providerId, botToken) {
  const params = { provider, provider_id: providerId };
  if (botToken) { params.bot_token = botToken; }
  action$('link_account', params).subscribe(data => {
    if (data.error) { addMsg('error', data.error); }
    else {
      let msg = provider + ' account ' + providerId + ' linked successfully!';
      if (data.bot_username) { msg += ' Bot: @' + data.bot_username; }
      if (data.bot_warning) { msg += '\n\u26a0\ufe0f ' + data.bot_warning; }
      addMsg('system', msg);
    }
  });
}

function cmdUnlinkAccount(provider) {
  action$('unlink_account', { provider }).subscribe(data => {
    if (data.unlinked) { addMsg('system', provider + ' account unlinked.'); }
    else { addMsg('system', 'No ' + provider + ' link found.'); }
  });
}

function cmdLinkStatus() {
  action$('list_linked_accounts', {}).subscribe(data => {
    const links = data.links || {};
    if (Object.keys(links).length === 0) {
      addMsg('system', 'No linked accounts. Use /link <provider> <id> to link.');
    } else {
      const lines = Object.entries(links).map(function(entry) { return '\u2022 ' + entry[0] + ': ' + entry[1]; });
      addMsg('system', 'Linked accounts:\n' + lines.join('\n'));
    }
  });
}

function cmdLink(text, parts) {
  const sub = (parts[1] || '').toLowerCase();
  if (sub === 'status' || !sub) {
    cmdLinkStatus();
  } else if (sub === 'unlink') {
    const provider = parts[2] || '';
    if (!provider) { addMsg('system', 'Usage: /link unlink <provider>'); return true; }
    cmdUnlinkAccount(provider);
  } else {
    const provider = parts[1];
    const providerId = parts[2] || '';
    const botToken = parts[3] || '';
    if (!providerId) { addMsg('system', 'Usage: /link <provider> <id> [bot_token]'); return true; }
    cmdLinkAccount(provider, providerId, botToken);
  }
  return true;
}

function cmdAddSecretCmd(text, parts) {
  const name = parts[1];
  const value = parts.slice(2).join(' ');
  if (!name || !value) { addMsg('system', t('secretAddUsage')); return true; }
  cmdAddSecret(name, value);
  return true;
}

function cmdListSecretsCmd() {
  cmdListSecrets();
  return true;
}

function cmdAddVariableCmd(text, parts) {
  const name = parts[1];
  const value = parts.slice(2).join(' ');
  if (!name || !value) { addMsg('system', t('variableAddUsage')); return true; }
  cmdAddVariable(name, value);
  return true;
}

function cmdListVariablesCmd() {
  cmdListVariables();
  return true;
}

function cmdModel(text, parts) {
  let agent = '';
  let modelName = '';
  if (parts[1] && parts[1].startsWith('@')) {
    agent = stripTarget(parts[1]);
    modelName = parts[2] || '';
  } else {
    modelName = parts[1] || '';
  }
  if (!modelName) { addMsg('system', 'Usage: /model [@agent] <name>'); return true; }
  action$('model', { model: modelName, agent: agent }).subscribe(data => {
    addMsg('system', data.message || data.error || 'Model updated');
  });
  return true;
}

function cmdDebug(text, parts) {
  const debugDesc = parts.slice(1).join(' ').trim();
  const debugMsg = '/call use_skill(skill_name="debug"' + (debugDesc ? ', context="' + debugDesc.replace(/"/g, '\\"') + '"' : '') + ')';
  sendMessage(debugMsg);
  return true;
}

function cmdLogin() {
  window.location.href = '/login';
  return true;
}

function cmdCall(text) {
  const callText = text.replace(/^\/call\s+/, '').trim();
  if (!callText) {
    addMsg('system', 'Usage: /call tool_name(key=value, ...) or /call tool_name {"key": "value"}\nType /help call for details.');
    return true;
  }
  const parsed = _parseToolCall(callText);
  if (parsed.error) {
    addMsg('system', 'Parse error: ' + parsed.error + '\nType /help call <toolname> for parameter info.');
    return true;
  }
  action$('call_tool', {
    tool_name: parsed.name,
    arguments: parsed.args,
    positional_args: parsed.positional || [],
  }).subscribe(data => {
    if (data.error) {
      addMsg('error', data.error);
    }
  });
  return true;
}

function cmdAutoconv(text) {
  if (!conversationId) { addMsg('system', t('thoughtNoConv')); return true; }
  const qargs = parseQuotedArgs(text);
  const sub = (qargs[1] || '').toLowerCase();
  if (!sub || !['on', 'off', 'status', 'now'].includes(sub)) {
    addMsg('system', 'Usage: /autoconv <on|off|status|now> @<agent|ALL> [freq]');
    return true;
  }
  const params = { sub };
  const freqPattern = /^\d+(-\d+)?\/\d*[smhd]$/;
  if (sub === 'on') {
    if (!qargs[2]) { addMsg('system', 'Usage: /autoconv on @<agent|ALL> [freq]'); return true; }
    if (freqPattern.test(qargs[2])) {
      addMsg('system', 'Usage: /autoconv on @<agent|ALL> [freq]');
      return true;
    }
    params.agent = resolveAgentName(stripTarget(qargs[2]));
    params.frequency = qargs[3] || '6/1m';
  } else {
    if (!qargs[2]) { addMsg('system', 'Usage: /autoconv ' + sub + ' @<agent|ALL>'); return true; }
    params.agent = resolveAgentName(stripTarget(qargs[2]));
  }
  action$('random_thought', params).subscribe(data => {
    if (data.error) { addMsg('error', data.error); }
    else if (sub === 'on') {
      const agents = data.agents || [data.agent];
      addMsg('system', t('thoughtEnabled', { agent: agents.map(displayAgentName).join(', '), freq: data.frequency, delay: data.next_in_seconds }));
    }
    else if (sub === 'off') {
      const agents = data.agents || [data.agent];
      addMsg('system', t('thoughtDisabled', { agent: agents.map(displayAgentName).join(', ') }));
    }
    else if (sub === 'now') { addMsg('system', t('thoughtTriggered', { agent: displayAgentName(data.agent) })); }
    else {
      if (data.agents && Array.isArray(data.agents)) {
        const lines = data.agents.map(a =>
          a.enabled
            ? t('thoughtStatus', { agent: displayAgentName(a.agent), freq: a.frequency, delay: a.next_in_seconds })
            : t('thoughtStatusOff', { agent: displayAgentName(a.agent) })
        );
        addMsg('system', lines.join('\n'));
      } else {
        addMsg('system', data.enabled ? t('thoughtStatus', { agent: displayAgentName(data.agent), freq: data.frequency, delay: data.next_in_seconds }) : t('thoughtStatusOff', { agent: displayAgentName(data.agent) }));
      }
    }
  });
  return true;
}

function cmdSchedules(text, parts) {
  const sub = (parts[1] || 'list').toLowerCase();
  if (sub === 'list') {
    cmdSchedulesList();
  } else if (sub === 'del' || sub === 'delete') {
    cmdSchedulesDel();
  } else if (sub === 'add' && parts[2]) {
    cmdSchedulesAdd(parts[2], parts.slice(3).join(' '));
  } else {
    addMsg('system', 'Usage: /schedules list | /schedules del | /schedules add YYYYMMDDHHmmss [reason]');
  }
  return true;
}

function cmdLlm(text, parts) {
  const agent = stripTarget(parts[1] || '');
  const svc = parts.slice(2).join(' ') || '';
  if (!agent || !svc) {
    addMsg('system', 'Usage: /llm @<agent|assistant> <service_name|${variable}|restore>');
    return true;
  }
  action$('set_llm_service', {
    agent_name: agent, llm_service: svc,
  }).subscribe(data => {
    addMsg('system', data.result || data.error || 'Done.');
  });
  return true;
}

function cmdFiles() {
  toggleFilesPanel();
  return true;
}

function cmdFlows() {
  toggleResourcesSection();
  return true;
}

function cmdTasks() {
  toggleSchedsPanel();
  return true;
}

function cmdToolsCmd() {
  cmdToolsList();
  return true;
}

function cmdUsageDeprecated() {
  addMsg('system', '/usage is deprecated. Use /cost <agent|ALL> instead.');
  return true;
}

function cmdRelay(text, parts) {
  var sub = (parts[1] || '').toLowerCase();
  if (!sub || sub === 'status') {
    action$('relay_status').subscribe(function(data) {
      if (data.error) { addMsg('error', data.error); return; }
      addMsg('system', data.message || JSON.stringify(data, null, 2));
    });
  } else if (sub === 'list') {
    action$('relay_list_available').subscribe(function(data) {
      if (data.error) { addMsg('error', data.error); return; }
      var relays = data.relays || [];
      if (!relays.length) { addMsg('system', 'No relays available.'); return; }
      var lines = ['**Available relays:**'];
      relays.forEach(function(r) {
        var info = r.relay_id;
        if (r.host_root) info += ' — local: ' + r.host_root;
        if (r.root) info += ' — docker: ' + r.root;
        if (r.allow_local) info += ' (allow_local)';
        lines.push('  ' + info);
      });
      addMsg('system', lines.join('\n'));
    });
  } else if (sub === 'link') {
    var rid = parts[2];
    if (!rid) { _showRelayLinkDialog(); return true; }
    fireAction('relay_link', {relay_id: rid});
    setTimeout(loadResources, 500);
    addMsg('system', 'Linking relay ' + rid + '...');
  } else if (sub === 'unlink') {
    var rid2 = parts[2];
    if (!rid2) { addMsg('error', 'Usage: /relay unlink <relay_id>'); return true; }
    fireAction('relay_unlink', {relay_id: rid2});
    setTimeout(loadResources, 500);
    addMsg('system', 'Unlinking relay ' + rid2 + '...');
  } else if (sub === 'default') {
    var rid3 = parts[2];
    if (!rid3) { addMsg('error', 'Usage: /relay default <relay_id>'); return true; }
    fireAction('relay_default', {relay_id: rid3});
    setTimeout(loadResources, 500);
    addMsg('system', 'Setting default relay to ' + rid3 + '...');
  } else if (sub === 'local') {
    // /relay local true|false [@agent]
    var val = (parts[2] || '').toLowerCase();
    if (val !== 'true' && val !== 'false') { addMsg('error', 'Usage: /relay local true|false [@agent]'); return true; }
    var agent = (parts[3] || '').replace(/^@/, '');
    action$('relay_set_local', {local: val === 'true', agent: agent}).subscribe(function(data) {
      if (data.error) addMsg('error', data.error);
      else addMsg('system', data.message || 'OK');
    });
  } else {
    addMsg('error', 'Unknown /relay subcommand: ' + sub + '. Use: status, list, link, unlink, default, local');
  }
  return true;
}

/* commands.js — Command dispatcher */

var COMMANDS = {
  // === Client-side commands ===
  '/new':         { handler: 'newChat' },
  '/conv':        { handler: 'loadConvs' },
  '/conversations': { handler: 'loadConvs' },
  '/clear':       { handler: 'clearChat' },
  '/help':        { handler: 'showHelp' },

  // === Action-based commands (call sendCmd) ===
  '/compact':       { action: 'compact', argName: 'agent_name' },
  '/rebuild':       { action: 'rebuild', argName: 'agent_name' },
  '/rebuild-full':  { action: 'rebuild_full', argName: 'agent_name' },
  '/rebuild_clean': { action: 'rebuild_full', argName: 'agent_name' },
  '/restart':       { action: 'restart_from', parser: 'restartParser' },
  '/restart_from':  { action: 'restart_from', parser: 'restartParser' },
  '/summary':       { action: 'resume_conversation', parser: 'summaryParser' },
  '/context':       { action: 'get_context', argName: 'agent_name' },

  '/model':       { action: 'model', argName: 'model' },
  '/llm':         { action: 'set_llm_service', parser: 'llmParser' },
  '/set_llm_service': { action: 'set_llm_service', parser: 'llmParser' },

  '/resources':   { action: 'list_resources' },
  '/tools':       { action: 'list_tools' },
  '/secrets':     { action: 'list_secrets' },
  '/list-secrets': { action: 'list_secrets' },
  '/variables':   { action: 'list_variables' },
  '/list-variables': { action: 'list_variables' },
  '/vars':        { action: 'list_variables' },
  '/cost':        { action: 'cost', argName: 'agent' },
  '/usage':       { action: 'cost', argName: 'agent' },
  '/files':       { action: 'list_conv_files' },

  '/agent':       { parser: 'agentParser' },
  '/msg':         { parser: 'msgParser' },
  '/btw':         { parser: 'btwParser' },
  '/stop':        { parser: 'stopParser' },
  '/resume':      { parser: 'resumeParser' },
  '/setname':     { parser: 'setnameParser' },

  '/skill':       { parser: 'skillParser' },
  '/task':        { parser: 'taskParser' },
  '/service':     { parser: 'serviceParser' },
  '/flow':        { parser: 'flowParser' },
  '/prompt':      { parser: 'promptParser' },
  '/memory':      { parser: 'memoryParser' },
  '/schedules':   { parser: 'schedulesParser' },
  '/link':        { parser: 'linkParser' },
  '/autoconv':    { parser: 'autoconvParser' },
  '/vidservice':  { parser: 'mediaServiceParser', mediaType: 'video' },
  '/imgservice':  { parser: 'mediaServiceParser', mediaType: 'image' },

  '/activate':    { action: 'activate_resource', parser: 'activateParser' },
  '/deactivate':  { action: 'deactivate_resource', parser: 'activateParser' },
  '/share':       { parser: 'shareParser' },

  '/add-secret':  { parser: 'addSecretParser' },
  '/add-variable': { parser: 'addVariableParser' },
  '/add-var':     { parser: 'addVariableParser' },

  '/upload':      { handler: 'triggerUpload' },
  '/copy':        { handler: 'copyLastMsg' },
  '/paste':       { handler: 'pasteClipboard' },
  '/view':        { parser: 'viewParser' },
  '/call':        { parser: 'callParser' },
  '/install':     { handler: 'showInstallHelp' },
  '/uninstall':   { parser: 'uninstallParser' },
  '/run':         { parser: 'runParser' },
  '/diff':        { parser: 'diffParser' },
  '/plan':        { parser: 'planParser' },
  '/watch':       { handler: 'showWatchNotAvailable' },
  '/clear-files': { handler: 'clearAttachments' },
  '/detach':      { handler: 'clearAttachments' },

  '/history':     { parser: 'historyParser' },
  '/export':      { parser: 'exportParser' },
  '/rename':      { parser: 'renameParser' },
  '/delete':      { parser: 'deleteParser' },
  '/delete-msg':  { parser: 'deleteMsgParser' },
  '/search':      { parser: 'searchParser' },

  '/connect':     { parser: 'connectParser' },
  '/disconnect':  { handler: 'disconnectRelay' },

  '/login':       { handler: 'showLoginMsg' },
  '/quit':        { handler: 'showQuitMsg' },
  '/exit':        { handler: 'showQuitMsg' },

  // Claude login — /clr and /clc go to server via fallback
  // /cls blocked (server login only available from webchat)
  '/claude-login-server':      { handler: 'blockedServerLogin' },
  '/cls':                      { handler: 'blockedServerLogin' },
};

// Client-side handler functions
function clearChat() { messagesEl.innerHTML = ''; }
function showHelp() {
  var cmds = Object.keys(COMMANDS).filter(function(c) { return c.charAt(1) !== '_' && !c.includes('_clean'); }).sort();
  var lines = ['<b>Available commands:</b><br>'];
  for (var i = 0; i < cmds.length; i++) {
    lines.push('<code>' + esc(cmds[i]) + '</code>');
  }
  var el = addMsg('system', '');
  el.innerHTML = lines.join(' ');
}
function triggerUpload() { addMsg('system', 'Drag & drop files into the chat or use the file attach button.'); }
function copyLastMsg(arg) {
  var msgs = document.querySelectorAll('.msg.assistant');
  if (!msgs.length) { addMsg('system', 'No responses to copy.'); return; }
  var n = parseInt(arg) || 1;
  var target = msgs[msgs.length - n];
  if (!target) { addMsg('system', 'Only ' + msgs.length + ' responses available.'); return; }
  var txt = target.textContent || '';
  vscode.postMessage({ type: 'command', command: 'clipboard_write', arg: txt });
  addMsg('system', 'Copied ' + txt.length + ' chars.');
}
function pasteClipboard() { vscode.postMessage({ type: 'command', command: 'clipboard_read' }); }
function showInstallHelp() { addMsg('system', 'To install a tool, drag & drop a .py file into the chat.'); }
function showWatchNotAvailable() { addMsg('system', '/watch is not available in VSCode. Use the CLI.'); }
function clearAttachments() { vscode.postMessage({ type: 'command', command: 'clear_attachments' }); addMsg('system', 'Attachments cleared.'); }
function disconnectRelay(arg) {
  addMsg('system', 'PawFlow relays are managed from webchat resources or PawFlow Relay Desktop/CLI.');
}
function connectParser(parts, text) {
  addMsg('system', 'PawFlow relays are managed from webchat resources or PawFlow Relay Desktop/CLI.');
  return null;
}
function showLoginMsg() { addMsg('system', 'Use the PawFlow: Login command from the command palette (Ctrl+Shift+P).'); }
function showQuitMsg() { addMsg('system', '/quit is not applicable in VSCode.'); }
function blockedServerLogin() { addMsg('error', 'Server login (/cls) is only available from the webchat. Use /clr (relay) or /clc (credentials) instead.'); }

// Parsers for complex commands
function restartParser(parts, text) {
  var agent = '', keep = 5;
  for (var i = 1; i < parts.length; i++) {
    var v = parseInt(parts[i]);
    if (!isNaN(v)) keep = v;
    else agent = parts[i];
  }
  var p = { keep_last: keep };
  if (agent) p.agent_name = agent;
  return p;
}
function summaryParser(parts, text) {
  var agent = '', tokens = 500;
  for (var i = 1; i < parts.length; i++) {
    var v = parseInt(parts[i]);
    if (!isNaN(v)) tokens = v;
    else agent = parts[i];
  }
  var p = { max_tokens: tokens };
  if (agent) p.agent_name = agent;
  return p;
}
function llmParser(parts, text) {
  if (parts.length < 3) { addMsg('system', 'Usage: /llm <agent> <service>'); return null; }
  return { agent_name: parts[1], llm_service: parts.slice(2).join(' ') };
}
function agentParser(parts, text) {
  var sub = (parts[1] || 'list').toLowerCase();
  if (sub === 'list') return { _action: 'list_agents' };
  if (sub === 'select' || (sub !== 'create' && sub !== 'delete' && sub !== 'msg' && sub !== 'btw' && sub !== 'interrupt' && sub !== 'setname' && sub !== 'enable' && sub !== 'disable' && sub !== 'promote' && sub !== 'resume'))
    return { _action: 'select_agent', name: parts[2] || sub };
  if (sub === 'create') return { _action: 'create_agent', name: parts[2] || '', prompt: parts.slice(3).join(' ') };
  if (sub === 'delete') return { _action: 'delete_agent', name: parts[2] || '' };
  if (sub === 'setname') return { _action: 'set_agent_nickname', real_name: parts[2] || '', nickname: parts[3] || '' };
  if (sub === 'enable') return { _action: 'agent_enable', agent_name: parts[2] || '' };
  if (sub === 'disable') return { _action: 'agent_disable', agent_name: parts[2] || '' };
  if (sub === 'promote') return { _action: 'agent_promote', agent_name: parts[2] || '', target_scope: parts[3] || 'user' };
  if (sub === 'msg' || sub === 'message') {
    var target = parts[2] || '';
    var msg = parts.slice(3).join(' ');
    if (target.toUpperCase() === 'ALL') return { _action: 'broadcast_agents', message: msg };
    return { _sendMessage: true, target: target, text: msg };
  }
  if (sub === 'btw') return { _action: 'btw', agent_name: parts[2] || '', message: parts.slice(3).join(' ') };
  if (sub === 'interrupt') {
    var t = parts[2] || '';
    return { _action: 'interrupt', target: t, agent_name: t };
  }
  if (sub === 'resume') {
    var t2 = parts[2] || '';
    return { _sendMessage: true, target: t2, text: parts.slice(3).join(' ') || 'Continue from where you left off.' };
  }
  return { _action: 'list_agents' };
}
function msgParser(parts, text) {
  var target = parts[1] || '';
  var msg = parts.slice(2).join(' ');
  if (!target || !msg) { addMsg('system', 'Usage: /msg <agent|ALL> <text>'); return null; }
  if (target.toUpperCase() === 'ALL') return { _action: 'broadcast_agents', message: msg };
  return { _sendMessage: true, target: target, text: msg };
}
function btwParser(parts, text) {
  if (parts.length < 2) { addMsg('system', 'Usage: /btw [agent] <text> (defaults to selected agent)'); return null; }
  if (parts.length < 3) {
    // No agent specified — use selected agent, entire text is the message
    return { _action: 'btw', agent_name: '', message: parts.slice(1).join(' ') };
  }
  return { _action: 'btw', agent_name: parts[1], message: parts.slice(2).join(' ') };
}
function stopParser(parts, text) {
  var force = parts.includes('-f');
  var target = parts.filter(function(p) { return p !== '-f' && p !== parts[0]; })[0] || '';
  if (!target) { addMsg('system', 'Usage: /stop <agent|ALL> [-f]'); return null; }
  return { _action: force ? 'cancel' : 'interrupt', target: target, agent_name: target };
}
function resumeParser(parts, text) {
  var target = parts[1] || '';
  if (!target) { addMsg('system', 'Usage: /resume <agent|ALL>'); return null; }
  return { _sendMessage: true, target: target, text: parts.slice(2).join(' ') || 'Continue from where you left off.' };
}
function setnameParser(parts, text) {
  if (!parts[1]) { addMsg('system', 'Usage: /setname <agent> [nickname]'); return null; }
  return { _action: 'set_agent_nickname', real_name: parts[1], nickname: parts[2] || '' };
}
function activateParser(parts, text) {
  if (parts.length < 3) { addMsg('system', 'Usage: ' + parts[0] + ' <type> <name>'); return null; }
  return { resource_type: parts[1], name: parts[2] };
}
function shareParser(parts, text) {
  if (parts.length < 4) { addMsg('system', 'Usage: /share <type> <name> <conv_id>'); return null; }
  return { _action: 'share_resource', resource_type: parts[1], name: parts[2], target_conversation_id: parts[3] };
}
function skillParser(parts, text) {
  var sub = (parts[1] || 'list').toLowerCase();
  if (sub === 'list') return { _action: 'list_resources' };
  if (sub === 'add' || sub === 'create') return { _action: 'create_resource', resource_type: 'skill', name: parts[2] || '', prompt: parts.slice(3).join(' ') };
  if (sub === 'del' || sub === 'delete') return { _action: 'delete_resource', resource_type: 'skill', name: parts[2] || '' };
  if (sub === 'run' || sub === 'search' || sub === 'import') return { _action: 'command', text: text, agent_name: window._selectedAgent || '' };
  addMsg('system', 'Usage: /skill list | add <name> <prompt> | del <name> | run [@agent] <name> [args...]');
  return null;
}
function taskParser(parts, text) {
  var sub = (parts[1] || 'list').toLowerCase();
  if (sub === 'list' || sub === 'status') return { _action: 'task_status', include_library: true };
  if (sub === 'create') return { _action: 'create_task_def', name: parts[2] || '', prompt: parts.slice(3).join(' ') };
  if (sub === 'assign') return { _action: 'assign_task', agent_name: parts[2] || '', task_name: parts.slice(3).join(' ') };
  if (sub === 'del' || sub === 'delete') return { _action: 'delete_task_def', name: parts[2] || '' };
  if (sub === 'pause' || sub === 'resume' || sub === 'cancel') return { _action: sub + '_task', task_id: parts[2] || '' };
  if (sub === 'log') return { _action: 'task_log', name: parts[2] || '' };
  addMsg('system', 'Usage: /task list | create | assign | del | pause | resume | cancel | log');
  return null;
}
function serviceParser(parts, text) {
  var sub = (parts[1] || 'list').toLowerCase();
  if (sub === 'list') return { _action: 'service_list' };
  if (sub === 'install') return { _action: 'service_install', service_type: parts[2] || '', service_name: parts[3] || '', config_str: parts.slice(4).join(' ') };
  if (sub === 'uninstall') return { _action: 'service_uninstall', service_id: parts[2] || '' };
  if (sub === 'enable') return { _action: 'service_enable', service_id: parts[2] || '' };
  if (sub === 'disable') return { _action: 'service_disable', service_id: parts[2] || '' };
  addMsg('system', 'Usage: /service list | install | uninstall | enable | disable');
  return null;
}
function flowParser(parts, text) {
  var sub = (parts[1] || 'list').toLowerCase();
  if (sub === 'list') return { _action: 'list_conv_flows' };
  if (sub === 'templates') return { _action: 'list_available_flows' };
  if (sub === 'deploy') return { _action: 'deploy_flow', template_id: parts[2] || '', scope: parts[3] || 'user' };
  if (sub === 'start') return { _action: 'start_flow', instance_id: parts[2] || '' };
  if (sub === 'stop') return { _action: 'stop_flow', instance_id: parts[2] || '' };
  if (sub === 'params') return { _action: 'get_flow_instance', instance_id: parts[2] || '' };
  if (sub === 'undeploy') return { _action: 'undeploy_flow', instance_id: parts[2] || '' };
  if (sub === 'promote') return { _action: 'promote_flow', instance_id: parts[2] || '', target_scope: 'user' };
  addMsg('system', 'Usage: /flow list | templates | deploy | start | stop | params | undeploy | promote');
  return null;
}
function promptParser(parts, text) {
  var sub = (parts[1] || 'list').toLowerCase();
  if (sub === 'list') return { _action: 'list_prompts' };
  if (sub === 'use') return { _action: 'get_prompt', name: parts[2] || '' };
  addMsg('system', 'Usage: /prompt list | use <name>');
  return null;
}
function memoryParser(parts, text) {
  var sub = (parts[1] || 'list').toLowerCase();
  if (sub === 'list') return { _action: 'list_memories', agent_name: parts[2] || '' };
  if (sub === 'add') return { _action: 'add_memory', content: parts.slice(2).join(' ') };
  if (sub === 'del' || sub === 'delete') return { _action: 'delete_memory', memory_id: parts[2] || '' };
  if (sub === 'edit') return { _action: 'edit_memory', memory_id: parts[2] || '', content: parts.slice(3).join(' ') };
  if (sub === 'search') return { _action: 'search_memories', query: parts.slice(2).join(' ') };
  addMsg('system', 'Usage: /memory list | add | del | edit | search');
  return null;
}
function schedulesParser(parts, text) {
  var sub = (parts[1] || 'list').toLowerCase();
  if (sub === 'list') return { _action: 'list_schedules' };
  if (sub === 'add') return { _action: 'add_schedule', when: parts[2] || '', reason: parts.slice(3).join(' ') };
  if (sub === 'del' || sub === 'delete' || sub === 'clear') return { _action: 'delete_schedule' };
  addMsg('system', 'Usage: /schedules list | add <when> | del');
  return null;
}
function linkParser(parts, text) {
  var sub = (parts[1] || '').toLowerCase();
  if (!sub || sub === 'status') return { _action: 'list_linked_accounts' };
  if (sub === 'unlink') return { _action: 'unlink_account', provider: parts[2] || '' };
  return { _action: 'link_account', provider: parts[1], provider_id: parts[2] || '', bot_token: parts[3] || '' };
}
function autoconvParser(parts, text) {
  var sub = (parts[1] || '').toLowerCase();
  if (!sub) { addMsg('system', 'Usage: /autoconv <on|off|status|now> <agent|ALL> [freq]'); return null; }
  var p = { _action: 'random_thought', sub: sub, agent: parts[2] || '' };
  if (sub === 'on') p.frequency = parts[3] || '6/1m';
  return p;
}
function mediaServiceParser(parts, text) {
  var isVideo = COMMANDS[parts[0]] && COMMANDS[parts[0]].mediaType === 'video';
  var prefix = isVideo ? 'video' : 'image';
  var sub = (parts[1] || 'list').toLowerCase();
  if (sub === 'list') return { _action: 'list_' + prefix + '_services' };
  if (sub === 'select') return { _action: 'set_' + prefix + '_service', service_name: parts[2] || '', agent_name: parts[3] || '*' };
  if (sub === 'clear') return { _action: 'clear_' + prefix + '_service', agent_name: parts[2] || '' };
  addMsg('system', 'Usage: /' + prefix + 'service list | select <name> [agent] | clear [agent]');
  return null;
}
function addSecretParser(parts, text) {
  if (parts.length < 3) { addMsg('system', 'Usage: /add-secret <name> <value>'); return null; }
  return { _action: 'add_secret', name: parts[1], value: parts.slice(2).join(' ') };
}
function addVariableParser(parts, text) {
  if (parts.length < 3) { addMsg('system', 'Usage: /add-variable <name> <value>'); return null; }
  return { _action: 'add_variable', name: parts[1], value: parts.slice(2).join(' ') };
}
function viewParser(parts, text) {
  if (!parts[1]) { addMsg('system', 'Usage: /view <filename>'); return null; }
  return { _action: 'view_file', filename: parts.slice(1).join(' ') };
}
function callParser(parts, text) {
  if (!parts[1]) { addMsg('system', 'Usage: /call <tool> {json}'); return null; }
  var toolName = parts[1];
  var argsJson = parts.slice(2).join(' ');
  var args = {};
  try { if (argsJson) args = JSON.parse(argsJson); } catch(e) { addMsg('system', 'Invalid JSON: ' + e.message); return null; }
  return { _action: 'call_tool', tool_name: toolName, arguments: args };
}
function uninstallParser(parts, text) {
  if (!parts[1]) { addMsg('system', 'Usage: /uninstall <tool_name>'); return null; }
  return { _action: 'uninstall_tool', name: parts[1] };
}
function runParser(parts, text) {
  var cmd = text.replace(/^\/run\s+/, '');
  if (!cmd) { addMsg('system', 'Usage: /run <command>'); return null; }
  return { _action: 'fs_exec', command: cmd, timeout: 30 };
}
function diffParser(parts, text) {
  var ref = parts.slice(1).join(' ') || '.';
  return { _action: 'fs_exec', command: 'git diff ' + ref, timeout: 15 };
}
function planParser(parts, text) {
  var arg = text.replace(/^\/plan\s*/, '').trim();
  if (!arg) { showPanel('plans'); return null; }
  var sub = arg.split(/\s+/);
  if (sub[0] === 'list') { showPanel('plans'); return null; }
  if (sub[0] === 'approve' && sub[1]) { return { _action: 'approve_plan', plan_id: sub[1] }; }
  if (sub[0] === 'cancel' && sub[1]) { return { _action: 'cancel_plan', plan_id: sub[1] }; }
  if (sub[0] === 'delete' && sub[1]) { return { _action: 'delete_plan', plan_id: sub[1] }; }
  return { _sendPlan: true, text: arg };
}
function historyParser(parts, text) {
  var n = parseInt(parts[1]) || 50;
  var offset = parseInt(parts[2]) || 0;
  return { _action: 'load_history', limit: n, offset: offset };
}
function exportParser(parts, text) {
  return { _action: 'export', format: parts[1] || 'markdown' };
}
function renameParser(parts, text) {
  var title = text.replace(/^\/rename\s+/, '');
  if (!title) { addMsg('system', 'Usage: /rename <title>'); return null; }
  return { _action: 'set_conv_title', title: title };
}
function deleteParser(parts, text) {
  if (!parts[1]) { addMsg('system', 'Usage: /delete <conversation_id>'); return null; }
  return { _action: 'delete_conversation', conversation_id: parts[1] };
}
function deleteMsgParser(parts, text) {
  var idx = parseInt(parts[1]);
  if (isNaN(idx)) { addMsg('system', 'Usage: /delete-msg <index>'); return null; }
  return { _action: 'delete_message', index: idx };
}
function searchParser(parts, text) {
  var query = text.replace(/^\/search\s+/, '');
  if (!query) { addMsg('system', 'Usage: /search <query>'); return null; }
  return { _action: 'search_messages', query: query };
}

function dispatchCommand(text) {
  var parts = text.split(/\s+/);
  var cmd = parts[0].toLowerCase();
  var arg = text.slice(cmd.length).trim();
  var def = COMMANDS[cmd];
  if (!def) {
    // Fallback: try server-side command parser for new/unknown commands
    sendCmd('command', JSON.stringify({ text: text, agent_name: window._selectedAgent || '' }));
    return true;
  }

  // Only commands that manipulate VS Code itself stay local.  Domain
  // commands are sent as raw text to the unified server parser; keeping a
  // second parser here caused stale action names and silent no-ops.
  var localCommands = {
    '/new': 1, '/conv': 1, '/conversations': 1, '/clear': 1,
    '/upload': 1, '/copy': 1, '/paste': 1, '/files': 1,
    '/view': 1, '/login': 1, '/clear-files': 1, '/detach': 1,
    '/watch': 1, '/terminal': 1, '/term': 1, '/code': 1,
    '/audio': 1, '/desktop': 1, '/port-forward': 1, '/fwd': 1,
    '/vm': 1, '/flows': 1, '/tasks': 1, '/graph': 1, '/kg': 1,
    '/claude-login-server': 1, '/cls': 1,
    '/claude-login-relay': 1, '/clr': 1,
    '/claude-login-credentials': 1, '/clc': 1,
  };
  if (!localCommands[cmd]) {
    sendCmd('command', JSON.stringify({
      text: text,
      agent_name: window._selectedAgent || '',
    }));
    return true;
  }

  if (def.handler) {
    window[def.handler](arg);
    return true;
  }

  var params = null;
  if (def.parser) {
    var parserFn = window[def.parser];
    if (parserFn) params = parserFn(parts, text);
    if (params === null) return true;
  } else if (def.action) {
    params = {};
    if (def.argName && arg) params[def.argName] = arg;
  }

  if (!params) params = {};

  if (params._sendMessage) {
    vscode.postMessage({ type: 'sendMessage', text: params.text, target: params.target });
    addMsg('user', '/msg ' + params.target + ' ' + params.text);
    return true;
  }
  if (params._sendPlan) {
    vscode.postMessage({ type: 'sendMessage', text: '[Create a structured plan using the create_plan tool. Analyze the request, identify steps, then call create_plan.]\n\n' + params.text });
    addMsg('user', '/plan ' + params.text);
    return true;
  }

  var action = params._action || def.action;
  if (!action) return false;
  delete params._action;

  sendCmd(action, JSON.stringify(params));
  return true;
}

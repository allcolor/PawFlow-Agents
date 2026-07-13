// ── Slash commands ───────────────────────────────────────────────
// HELP_DATA lives in commands_help.js (loaded before this file).

function resolveAgentName(nameOrNick) {
  if (!nameOrNick) return nameOrNick;
  for (const [real, nick] of Object.entries(nicknameMap)) {
    if (nick.toLowerCase() === nameOrNick.toLowerCase()) return real;
  }
  return nameOrNick;
}

function displayAgentName(realName) {
  const key = (realName || '').toLowerCase();
  for (const k of Object.keys(nicknameMap)) {
    if (k.toLowerCase() === key) return nicknameMap[k];
  }
  return realName || '';
}

function parseQuotedArgs(text) {
  const args = [];
  const re = /@"([^"]*)"|@(\S+)|"([^"]*)"|\S+/g;
  let m;
  while ((m = re.exec(text)) !== null) {
    if (m[1] !== undefined) args.push('@' + m[1]);       // @"quoted target"
    else if (m[2] !== undefined) args.push('@' + m[2]);   // @target
    else if (m[3] !== undefined) args.push(m[3]);          // "quoted string"
    else args.push(m[0]);                                   // plain word
  }
  return args;
}

// ── Command aliases ─────────────────────────────────────────────
const _CMD_ALIASES = {
  '/conversations': '/conv',
  '/restart': '/restart_from',
  '/restart-from': '/restart_from',
  '/set_llm_service': '/llm',
  '/detach': '/clear-files',
  '/add-var': '/add-variable',
  '/list-secrets': '/secrets',
  '/list-variables': '/variables',
  '/vars': '/variables',
  '/int': '/interrupt',
};

// Commands that directly manipulate webchat UI state remain local. All domain
// commands use the server parser so their syntax and action schemas cannot
// drift between webchat, PawCode, Telegram, and VS Code.
const _LOCAL_COMMANDS = new Set([
  '/new', '/conv', '/clear', '/clear-files',
  '/upload', '/copy', '/paste', '/watch',
  '/export', '/encrypt', '/delete-msg', '/restart_from', '/plan',
  '/files', '/flows', '/tasks', '/graph', '/kg',
  '/login',
  '/claude-login-server', '/cls',
  '/claude-login-relay', '/clr',
  '/claude-login-credentials', '/clc',
  '/terminal', '/term', '/code', '/relay-audio', '/desktop',
  '/port-forward', '/fwd', '/vm',
]);

// ── Command dispatch table ──────────────────────────────────────
// Each handler receives (text, parts, cmd) and returns true.
// Handlers are defined in: cmd_agent.js, cmd_context.js, cmd_resources.js,
// cmd_conversation.js, cmd_misc.js
const _CMD_HANDLERS = {
  // Agent management (cmd_agent.js)
  '/interrupt':   (text, parts, cmd) => cmdInterrupt(text, parts),
  '/stop':        (text, parts, cmd) => cmdForceStop(text, parts),
  '/agent':       (text, parts, cmd) => cmdAgent(text, parts),
  '/msg':         (text, parts, cmd) => cmdMsg(text),
  '/btw':         (text, parts, cmd) => cmdBtw(text),
  '/setname':     (text, parts, cmd) => cmdSetname(text),

  // Context operations (cmd_context.js)
  '/restart_from': (text, parts, cmd) => cmdRestartFrom(text, parts),
  '/resume':       (text, parts, cmd) => cmdResume(text),
  '/summary':      (text, parts, cmd) => cmdSummary(text, parts),
  '/compact':      (text, parts, cmd) => cmdCompactCmd(text, parts),
  '/git-prune':    (text, parts, cmd) => cmdGitPruneCmd(text, parts),
  '/prune-git':    (text, parts, cmd) => cmdGitPruneCmd(text, parts),
  '/rebuild':      (text, parts, cmd) => cmdRebuildCmd(text, parts),
  '/context':      (text, parts, cmd) => cmdContextCmd(text, parts),

  // Resources (cmd_resources.js)
  '/task':        (text, parts, cmd) => cmdTask(text, parts),
  '/goal':        (text, parts, cmd) => cmdGoal(text, parts),
  '/vidservice':  (text, parts, cmd) => cmdVidservice(text, parts),
  '/imgservice':  (text, parts, cmd) => cmdImgservice(text, parts),
  '/skill':       (text, parts, cmd) => cmdSkill(text, parts),
  '/add-skill':   (text, parts, cmd) => cmdAddSkill(text, parts),
  '/resources':   (text, parts, cmd) => cmdResources(),
  '/activate':    (text, parts, cmd) => cmdActivate(text, parts),
  '/deactivate':  (text, parts, cmd) => cmdDeactivate(text, parts),
  '/share':       (text, parts, cmd) => cmdShare(text, parts),
  '/view':        (text, parts, cmd) => cmdView(text, parts),
  '/service':     (text, parts, cmd) => cmdService(text, parts),
  '/claude-login-server': (text, parts) => cmdClaudeLoginServer(parts),
  '/cls':                 (text, parts) => cmdClaudeLoginServer(parts),
  '/claude-login-relay':  (text, parts) => cmdClaudeLoginRelay(parts),
  '/clr':                 (text, parts) => cmdClaudeLoginRelay(parts),
  '/claude-login-credentials': (text, parts, cmd) => cmdClaudeLoginCredentials(text, parts),
  '/clc':                 (text, parts, cmd) => cmdClaudeLoginCredentials(text, parts),
  '/relay':       (text, parts, cmd) => cmdRelay(text, parts),
  '/flow':        (text, parts, cmd) => cmdFlow(text, parts),
  '/install':     (text, parts, cmd) => cmdInstall(),
  '/uninstall':   (text, parts, cmd) => cmdUninstall(text, parts),

  // Conversation operations (cmd_conversation.js)
  '/new':         (text, parts, cmd) => cmdNew(),
  '/conv':        (text, parts, cmd) => cmdConv(),
  '/history':     (text, parts, cmd) => cmdHistory(text, parts),
  '/export':      (text, parts, cmd) => cmdExport(text, parts, cmd),
  '/rename':      (text, parts, cmd) => cmdRename(text, parts, cmd),
  '/delete':      (text, parts, cmd) => cmdDelete(text, parts),
  '/encrypt':     (text, parts, cmd) => cmdEncrypt(text, parts),
  '/delete-msg':  (text, parts, cmd) => cmdDeleteMsg(text, parts),
  '/search':      (text, parts, cmd) => cmdSearch(text, parts, cmd),
  '/clear':       (text, parts, cmd) => cmdClear(),
  '/clear-store': (text, parts, cmd) => cmdClearStore(text, parts),
  '/clear-files': (text, parts, cmd) => cmdClearFiles(),
  '/upload':      (text, parts, cmd) => cmdUpload(),
  '/copy':        (text, parts, cmd) => cmdCopy(text, parts),
  '/paste':       (text, parts, cmd) => cmdPaste(),
  '/diff':        (text, parts, cmd) => cmdDiff(text, parts),
  '/plan':        (text, parts, cmd) => cmdPlan(text, parts, cmd),
  '/watch':       (text, parts, cmd) => cmdWatch(),
  '/run':         (text, parts, cmd) => cmdRun(text, parts, cmd),
  '/loop':        (text, parts, cmd) => cmdLoop(text, parts),
  '/batch':       (text, parts, cmd) => cmdBatch(text),

  // Misc (cmd_misc.js)
  '/help':          (text, parts, cmd) => { cmdHelp(parts.slice(1).join(' ')); return true; },
  '/schedules':     (text, parts, cmd) => cmdSchedules(text, parts),
  '/cost':          (text, parts, cmd) => cmdCost(text),
  '/usage':         (text, parts, cmd) => cmdUsageDeprecated(),
  '/memory':        (text, parts, cmd) => cmdMemory(text, parts),
  '/diary':         (text, parts, cmd) => cmdDiary(text, parts),
  '/learn':         (text, parts, cmd) => cmdLearn(text, parts),
  '/tools':         (text, parts, cmd) => cmdToolsCmd(),
  '/model':         (text, parts, cmd) => cmdModel(text, parts),
  '/debug':         (text, parts, cmd) => cmdDebug(text, parts),
  '/login':         (text, parts, cmd) => cmdLogin(),
  '/call':          (text, parts, cmd) => cmdCall(text),
  '/autoconv':      (text, parts, cmd) => cmdAutoconv(text),
  '/llm':           (text, parts, cmd) => cmdLlm(text, parts),
  '/link':          (text, parts, cmd) => cmdLink(text, parts),
  '/add-secret':    (text, parts, cmd) => cmdAddSecretCmd(text, parts),
  '/secrets':       (text, parts, cmd) => cmdListSecretsCmd(),
  '/add-variable':  (text, parts, cmd) => cmdAddVariableCmd(text, parts),
  '/variables':     (text, parts, cmd) => cmdListVariablesCmd(),
  '/files':         (text, parts, cmd) => cmdFiles(),
  '/flows':         (text, parts, cmd) => cmdFlows(),
  '/tasks':         (text, parts, cmd) => cmdTasks(),
  '/graph':         (text, parts, cmd) => cmdGraph(text, parts),
  '/kg':            (text, parts, cmd) => cmdKg(text, parts),

  // Terminal / code-server (terminal.js)
  '/terminal':      (text, parts, cmd) => cmdTerminal(text, parts),
  '/term':          (text, parts, cmd) => cmdTerminal(text, parts),
  '/code':          (text, parts, cmd) => cmdCode(text, parts),
  '/relay-audio':   (text, parts, cmd) => cmdAudio(text, parts),
  '/desktop':       (text, parts, cmd) => cmdDesktop(text, parts),
  '/port-forward':  (text, parts, cmd) => cmdPortForward(text, parts),
  '/fwd':           (text, parts, cmd) => cmdPortForward(text, parts),

  // VM management
  '/vm':            (text, parts, cmd) => cmdVm(text, parts),
};

/**
 * Tokenize a command string. Handles:
 * - Quoted strings: "hello world" → single token (quotes stripped)
 * - @target with quotes: @"Name With Spaces" → @Name With Spaces (quotes stripped, @ preserved)
 * - Regular words split on whitespace
 *
 * The @ prefix is semantic (marks a target) and preserved in the token.
 * Handlers call stripTarget() to get the name without @.
 */
function tokenizeCommand(text) {
  const tokens = [];
  let i = 0;
  while (i < text.length) {
    if (/\s/.test(text[i])) { i++; continue; }
    // @target with optional quotes: @name or @"Name With Spaces"
    if (text[i] === '@' && i + 1 < text.length) {
      i++; // skip @
      if (text[i] === '"' || text[i] === "'") {
        const q = text[i]; i++;
        const start = i;
        while (i < text.length && text[i] !== q) i++;
        tokens.push('@' + text.slice(start, i));
        if (i < text.length) i++;
      } else {
        const start = i;
        while (i < text.length && !/\s/.test(text[i])) i++;
        tokens.push('@' + text.slice(start, i));
      }
      continue;
    }
    // Quoted string (no @)
    if (text[i] === '"' || text[i] === "'") {
      const q = text[i]; i++;
      const start = i;
      while (i < text.length && text[i] !== q) i++;
      tokens.push(text.slice(start, i));
      if (i < text.length) i++;
      continue;
    }
    // Regular word
    const start = i;
    while (i < text.length && !/\s/.test(text[i])) i++;
    tokens.push(text.slice(start, i));
  }
  return tokens;
}

/** Strip @ prefix from a target token. */
function stripTarget(s) { return (s && s.startsWith('@')) ? s.slice(1) : s; }

async function handleSlashCommand(text) {
  const parts = tokenizeCommand(text);
  const cmd = parts[0].toLowerCase();

  if (window._pawflowExtRuntime) {
    window._pawflowExtRuntime.fireHook('command_submitted', {
      command: cmd, args: parts.slice(1), text: text,
    });
  }

  // Extension-provided commands resolve before built-ins so a package
  // can shadow nothing it did not register, but the dispatch lets it own
  // its namespace once registered.
  if (window.pawflow && typeof window.pawflow.getCommand === 'function') {
    const extCmd = window.pawflow.getCommand(cmd);
    if (extCmd && typeof extCmd.handler === 'function') {
      try {
        const out = extCmd.handler(text, parts);
        return Promise.resolve(out).then(function (v) { return v !== false; });
      } catch (err) {
        console.warn('[ext:' + extCmd.pkg + '] command ' + cmd + ': ' + err.message);
      }
    }
  }

  // Resolve aliases
  const resolved = _CMD_ALIASES[cmd] || cmd;

  if (!_LOCAL_COMMANDS.has(resolved)) {
    return await tryServerCommand(text);
  }

  const handler = _CMD_HANDLERS[resolved];
  if (handler) return handler(text, parts, cmd);

  // Unknown slash command — try server-side command parser
  return await tryServerCommand(text);
}

/**
 * Fallback: send raw /command text to the server-side unified parser.
 * New commands added server-side auto-work without client changes.
 */
function tryServerCommand(text) {
  action$('command', {
    text,
    conversation_id: conversationId || '',
    agent_name: selectedAgent || '',
  }).subscribe(data => {
    if (data.client_only) {
      addMsg('system', t('unknownCommandHelp', { command: text.split(/\s+/)[0] }));
      return;
    }
    if (data.help) { addMsg('system', data.help); return; }
    if (data.display) {
      addMsg('system', data.display);
    } else {
      if (data.output) { addMsg('system', data.output); }
      if (data.message) { addMsg('system', data.message); }
      if (data.error) { addMsg('system', '\u26a0 ' + data.error); }
    }
    if (data.conversation_id && data.ok && data.source) {
      addMsg('system', t('switchingToForkedConversation'));
      if (typeof switchConversation === 'function') {
        switchConversation(data.conversation_id);
      }
    }
    if (data.checkpoints && !data.error) {
      // Rewind checkpoint list — already rendered via data.message
    }
  });
  return true;
}

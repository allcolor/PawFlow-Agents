// ── Slash commands ───────────────────────────────────────────────
const HELP_DATA = {
  '/help': {
    usage: '/help [command]',
    short: 'Show available commands or detailed help for a command',
    detail: 'Without arguments, lists all commands. With a command name, shows detailed documentation.\nExample: /help agent',
  },
  '/msg': {
    usage: '/msg <name|ALL> <message>',
    short: 'Send a message to a specific agent (shortcut for /agent msg)',
    detail: 'Send a message to a specific agent without changing the active agent.\n\nExamples:\n  /msg grok Explain this code\n  /msg ALL What do you think?',
  },
  '/btw': {
    usage: '/btw <name|ALL> <question>',
    short: 'Side-channel question to an agent (shortcut for /agent btw)',
    detail: 'Ask a quick question to an agent without interrupting its current work.\n\nExamples:\n  /btw claude What is the time complexity?\n  /btw ALL Any thoughts on this?',
  },
  '/call': {
    usage: '/call tool_name(key=value, ...) or /call tool_name {"key": "value"}',
    short: 'Call a tool directly',
    detail: 'Execute any agent tool from the chat.\n\n'
      + 'Syntax:\n'
      + '  /call web_search(query="quantum computing")         \u2014 function-call style\n'
      + '  /call fetch_http(url="https://example.com")         \u2014 named params\n'
      + '  /call remember(text="important fact", tags=["note"]) \u2014 with array param\n'
      + '  /call web_search {"query": "quantum computing"}     \u2014 JSON style\n\n'
      + 'Help:\n'
      + '  /help call              \u2014 this help\n'
      + '  /help call <toolname>   \u2014 show tool parameters and description\n',
  },
  '/vidservice': {
    usage: '/vidservice [list | select <name> [agent] | clear [agent]]',
    short: 'Manage video generation service',
    detail: 'Choose which video generation service to use in this conversation.\n\n'
      + '  /vidservice list                  \u2014 Show available video services\n'
      + '  /vidservice select <name>         \u2014 Set default for all agents\n'
      + '  /vidservice select <name> <agent> \u2014 Set for a specific agent\n'
      + '  /vidservice clear                 \u2014 Remove all preferences (auto-select)\n'
      + '  /vidservice clear <agent>         \u2014 Remove preference for one agent\n',
  },
  '/task': {
    usage: '/task create | assign | list | delete | pause | resume | cancel',
    short: 'Create, assign and manage agent tasks',
    detail: 'Task library + autonomous task assignment. Tasks can be reusable definitions or inline.\n\n'
      + '**Library (reusable definitions):**\n'
      + '  /task create <name> "<prompt>" [--criteria "..."] [--interval XX]\n'
      + '  /task delete <name>           \u2014 Delete a task definition\n'
      + '  /task list                    \u2014 Show library + running tasks\n\n'
      + '**Assignment (from library or inline):**\n'
      + '  /task assign <agent> <taskname>              \u2014 From library\n'
      + '  /task assign <agent> <taskname> --var nbr_images=20 --var style=cyberpunk\n'
      + '  /task assign <agent> <taskname> --interval XX \u2014 Override interval\n'
      + '  /task assign <agent> "<inline task>" [--criteria "..."] [--interval XX] [--verifier <agent>]\n\n'
      + 'Variables: use ${name} in task definitions, resolved at assign time.\n'
      + 'Use \\${...} to keep literal ${...}. ${global.*} and ${secrets.*} also resolved.\n\n'
      + '**Control:**\n'
      + '  /task pause <task_id|agent>   \u2014 Pause a task or all tasks of an agent\n'
      + '  /task resume <task_id|agent>  \u2014 Resume a paused task or all of an agent\n'
      + '  /task cancel <task_id|agent>  \u2014 Cancel a task or all of an agent\n\n'
      + 'Task IDs look like t_xxxxxxxx. Use /task list to see them.\n'
      + 'Tasks survive server restarts and reschedule automatically.\n\n'
      + 'Example: /task assign grok "Scrape the top 100 HN posts" --verifier claude --interval 120 --criteria "all 100 posts summarized"',
  },
  '/imgservice': {
    usage: '/imgservice [list | select <name> [agent] | clear [agent]]',
    short: 'Manage image generation service',
    detail: 'Choose which image generation service to use in this conversation.\n\n'
      + '  /imgservice list                  \u2014 Show available image services\n'
      + '  /imgservice select <name>         \u2014 Set default for all agents\n'
      + '  /imgservice select <name> <agent> \u2014 Set for a specific agent\n'
      + '  /imgservice clear                 \u2014 Remove all preferences (auto-select)\n'
      + '  /imgservice clear <agent>         \u2014 Remove preference for one agent\n',
  },
  '/agent': {
    usage: '/agent list | create | select | delete | msg | interrupt | btw | resume | setname',
    short: 'Manage AI agents',
    detail: 'Create, list, select, message, or control AI agents.\n\n'
      + '  /agent list                       — List all agents (user + global)\n'
      + '  /agent create                     — Create a new agent (interactive)\n'
      + '  /agent select <name>              — Activate an agent (use real name or nickname)\n'
      + '  /agent select assistant            — Switch back to the default assistant\n'
      + '  /agent delete <name>              — Delete an agent by name\n'
      + '  /agent msg <name> <text>          — Send a message to a specific agent\n'
      + '  /agent msg ALL <text>             — Broadcast to all agents in parallel\n'
      + '  /agent interrupt <name|ALL>       — Force agent to stop and respond immediately\n'
      + '  /agent btw <name|ALL> <text>      — Side-channel question (no interruption)\n'
      + '  /agent resume <name>              — Tell agent to continue from where it stopped\n'
      + '  /agent setname <real> [nickname]  — Set or reset display name (omit to reset)\n\n'
      + 'Agents define a system prompt, tools, model, and LLM service. '
      + 'The active agent shapes the AI\'s behavior for the conversation.',
  },
  '/skill': {
    usage: '/skill list | add <name> <prompt> | del <name>',
    short: 'Manage skills (single-shot prompt templates)',
    detail: 'Create, list, or delete skills.\n\n'
      + '  /skill list              — List all skills with active status\n'
      + '  /skill add <name> <prompt> — Create a skill with given prompt\n'
      + '  /skill del <name>        — Delete a skill\n\n'
      + 'Skills are prompt-only resources injected into the system prompt when active.',
  },
  '/add-skill': {
    usage: '/add-skill <name> <prompt>',
    short: 'Shortcut to create a skill',
    detail: 'Same as /skill add <name> <prompt>.',
  },
  '/resources': {
    usage: '/resources',
    short: 'List all resources (agents, skills, MCP servers)',
    detail: 'Shows all defined resources grouped by type, with activation status for the current conversation.',
  },
  '/activate': {
    usage: '/activate <agent|skill|mcp> <name>',
    short: 'Activate a resource for this conversation',
    detail: 'Activates an agent, skill, or MCP server.\n\n'
      + '  /activate agent researcher  — Activate the "researcher" agent\n'
      + '  /activate skill summarizer  — Activate the "summarizer" skill\n'
      + '  /activate mcp my_server     — Activate an MCP server',
  },
  '/deactivate': {
    usage: '/deactivate <agent|skill|mcp> <name>',
    short: 'Deactivate a resource from this conversation',
    detail: 'Deactivates an agent, skill, or MCP server for the current conversation.',
  },
  '/share': {
    usage: '/share <agent|skill|mcp> <name> <conversation_id>',
    short: 'Share a resource to another conversation',
    detail: 'Copies a resource activation to another conversation by ID.',
  },
  '/service': {
    usage: '/service list | install <type> <name> [config] | uninstall <name> | enable <name> | disable <name>',
    short: 'Manage LLM and external services',
    detail: 'Install, list, enable/disable, or uninstall services.\n\n'
      + '  /service list                    — List installed services\n'
      + '  /service install <type> <name> [key=val,...] — Install a service\n'
      + '  /service uninstall <name>        — Remove a service\n'
      + '  /service enable <name>           — Enable a service\n'
      + '  /service disable <name>          — Disable a service',
  },
  '/schedules': {
    usage: '/schedules list | del | add <YYYYMMDDHHmmss> [reason]',
    short: 'Manage scheduled poll rechecks',
    detail: 'List, add, or delete scheduled recheck times.\n\n'
      + '  /schedules list           — List pending schedules\n'
      + '  /schedules add <datetime> — Add a recheck (format: YYYYMMDDHHmmss)\n'
      + '  /schedules del            — Delete all schedules',
  },
  '/llm': {
    usage: '/llm <agent|assistant> <service|${variable}|restore>',
    short: 'Change LLM service for an agent in this conversation',
    detail: 'Override the LLM service for any agent in the current conversation.\n\n'
      + '  /llm assistant grok_llm_service    \u2014 Switch assistant to grok\n'
      + '  /llm grok qwen_llm_service         \u2014 Switch grok to local qwen\n'
      + '  /llm assistant ${user.my_service}   \u2014 Use a variable reference\n'
      + '  /llm grok restore                   \u2014 Restore grok\'s default service\n\n'
      + 'The override is per-conversation and persists across restarts.',
  },
  '/stop': {
    usage: '/stop <agent|ALL> [-f]',
    short: 'Stop an agent — asks it to respond immediately',
    detail: 'Interrupts the agent and asks it to give its best answer now.\n\n'
      + '  /stop ALL          — Stop all agents (they respond with what they have)\n'
      + '  /stop grok         — Stop only grok\n'
      + '  /stop ALL -f       — Force stop all (immediate cancel, no response)\n'
      + '  /stop grok -f      — Force stop grok (immediate cancel)',
  },
  '/restart_from': {
    usage: '/restart_from [agent|ALL] [N]',
    short: 'Restart context from last N messages (default 5, 0 = empty)',
    detail: 'Keeps only the last N messages as LLM context. Earlier messages stay in history but are ignored by the agent.\n\n'
      + '  /restart_from          \u2014 Keep last 5 messages (shared)\n'
      + '  /restart_from 10       \u2014 Keep last 10 messages\n'
      + '  /restart_from grok 3   \u2014 Keep last 3 for grok\'s context\n'
      + '  /restart_from ALL 5    \u2014 Restart all agents\n'
      + '  /restart_from 0    — Empty context (fresh start, keeps system prompt)\n\n'
      + 'Useful when the conversation gets too long or the agent loses focus.',
  },
  '/summary': {
    usage: '/summary [agent|ALL] [tokens]',
    short: 'Summarize context to N tokens and restart from summary',
    detail: 'Asks the LLM to summarize the context to approximately N tokens (default 500), then restarts from that summary.\n\n'
      + '  /summary              \u2014 Summarize shared context to ~500 tokens\n'
      + '  /summary 1000         \u2014 Summarize to ~1000 tokens\n'
      + '  /summary grok         \u2014 Summarize grok\'s context\n'
      + '  /summary ALL          \u2014 Summarize all agents\' contexts\n'
      + '  /summary qwen 2000    \u2014 Summarize qwen\'s context to ~2000 tokens\n\n'
      + 'The summary replaces previous context for that agent. New messages build on top.',
  },
  '/resume': {
    usage: '/resume <agent|ALL>',
    short: 'Tell an agent to continue from where it stopped',
    detail: 'Resumes an agent that was interrupted or stopped.\n\nExamples:\n  /resume grok\n  /resume ALL',
  },
  '/compact': {
    usage: '/compact [agent|ALL]',
    short: 'Compact context (summarize old messages)',
    detail: 'Summarizes older messages to reduce context size while preserving key information.\n\n'
      + '  /compact        \u2014 Compact current agent\'s context (or shared if none selected)\n'
      + '  /compact shared \u2014 Compact the shared context\n'
      + '  /compact grok   \u2014 Compact grok\'s context only\n'
      + '  /compact ALL    \u2014 Compact all agents\' contexts',
  },
  '/rebuild': {
    usage: '/rebuild [agent|ALL]',
    short: 'Rebuild context from full conversation history',
    detail: 'Reconstructs the LLM context from the complete conversation. If everything fits, restores fully; otherwise compacts.\n\n'
      + '  /rebuild        \u2014 Rebuild shared context\n'
      + '  /rebuild grok   \u2014 Rebuild grok\'s context\n'
      + '  /rebuild ALL    \u2014 Rebuild all agents',
  },
  '/rebuild_clean': {
    usage: '/rebuild_clean',
    short: 'Set context = full conversation (no compaction, deprecated — use /rebuild-full)',
    detail: 'Deprecated. Use /rebuild-full instead.',
  },
  '/rebuild-full': {
    usage: '/rebuild-full [agent|ALL]',
    short: 'Set context = full conversation (no compaction)',
    detail: 'Copies the entire conversation history into the LLM context as-is, without any compaction or summarization. Use when you want the agent to see everything.\n\n'
      + '  /rebuild-full        \u2014 Rebuild shared context\n'
      + '  /rebuild-full grok   \u2014 Rebuild grok\'s context\n'
      + '  /rebuild-full ALL    \u2014 Rebuild all agents\' contexts',
  },
  '/context': {
    usage: '/context [agent]',
    short: 'View the LLM context',
    detail: 'Shows what the LLM actually sees: messages, token estimate, divergence status.\n\n'
      + '  /context        \u2014 View shared context\n'
      + '  /context grok   \u2014 View grok\'s context\n\n'
      + 'The overlay includes an agent dropdown to switch between agent contexts.',
  },
  '/files': {
    usage: '/files',
    short: 'Toggle the files panel',
    detail: 'Shows or hides the file browser panel for viewing and managing uploaded files.',
  },
  '/flows': {
    usage: '/flows',
    short: 'Toggle the flows panel',
    detail: 'Shows or hides the flows panel for monitoring active data flows.',
  },
  '/tasks': {
    usage: '/tasks',
    short: 'Toggle the scheduled tasks panel',
    detail: 'Shows or hides the panel listing scheduled background tasks.',
  },
  '/tools': {
    usage: '/tools',
    short: 'List available tools',
    detail: 'Shows all tools available to the AI agent in the current conversation, including builtins and custom tools.',
  },
  '/usage': {
    usage: '/usage',
    short: 'Show token usage statistics',
    detail: 'Displays token usage for the current conversation (prompt tokens, completion tokens, total).',
  },
  '/memory': {
    usage: '/memory [list [agent] | add | edit | del | search | panel]',
    short: 'Manage agent memories',
    detail: 'View, add, edit and delete persistent agent memories.\n\n'
      + '  /memory                              \u2014 Open memory panel (visual editor)\n'
      + '  /memory list                         \u2014 List all memories\n'
      + '  /memory list <agent>                 \u2014 List memories for an agent\n'
      + '  /memory add <text> [#tag1] [@agent]  \u2014 Add a memory manually\n'
      + '  /memory edit <id> <new text>         \u2014 Edit a memory\n'
      + '  /memory del <id>                     \u2014 Delete a memory\n'
      + '  /memory search <query>               \u2014 Search memories by text',
  },
  '/install': {
    usage: '/install <filename.py>',
    short: 'Install a custom tool',
    detail: 'Install a custom tool from a Python file. Drag & drop a .py file into the chat or paste code.',
  },
  '/uninstall': {
    usage: '/uninstall <tool_name>',
    short: 'Uninstall a custom tool',
    detail: 'Remove a previously installed custom tool by name.',
  },
  '/link': {
    usage: '/link <provider> <id> [bot_token] | unlink <provider> | status',
    short: 'Link/unlink external accounts',
    detail: 'Link your account to an external provider for cross-platform messaging.\n\n'
      + '  /link <provider> <id> [bot_token] — Link account\n'
      + '  /link unlink <provider>           — Unlink account\n'
      + '  /link status                      — Show linked accounts',
  },
  '/add-secret': {
    usage: '/add-secret <name> <value>',
    short: 'Store an encrypted secret',
    detail: 'Stores a secret value encrypted at rest. Available as ${secrets.key} in expressions.',
  },
  '/secrets': {
    usage: '/secrets',
    short: 'List stored secrets',
    detail: 'Lists all stored secret names (values are not shown). Also accessible as /list-secrets.',
  },
  '/add-variable': {
    usage: '/add-variable <name> <value>',
    short: 'Store a plaintext variable',
    detail: 'Stores a plaintext variable. Available as ${var.key} in expressions. Also: /add-var.',
  },
  '/variables': {
    usage: '/variables',
    short: 'List stored variables',
    detail: 'Lists all stored variables with their values. Also: /vars, /list-variables.',
  },
  '/view': {
    usage: '/view <filename>',
    short: 'Preview a file (image, PDF, text, code)',
    detail: 'Opens the file viewer overlay to preview a file by name. Supports images, PDF, text, and code files.',
  },
  '/cost': {
    usage: '/cost <agent|ALL>',
    short: 'Show token usage and estimated cost per agent',
    detail: 'Displays input/output tokens, call count, and estimated cost per agent.\n\n'
      + '  /cost ALL     — All agents\n'
      + '  /cost grok    — Specific agent\n\n'
      + 'Cost is calculated from cost_per_1m_input/output ($ per million tokens) on the LLM service.\n'
      + 'If not configured, shows "not configured".',
  },
  '/autoconv': {
    usage: '/autoconv <on|off|status|now> <agent|ALL> [freq]',
    short: 'Auto-conversation — agents contribute to the conversation autonomously',
    detail: 'Enable autonomous conversation contributions from an agent.\n\n'
      + '  /autoconv on ALL              — All agents, default 6/1m\n'
      + '  /autoconv on grok 2-3/h       — Grok, 2-3 times per hour\n'
      + '  /autoconv on ALL 1/2h         — All agents, once per 2h\n'
      + '  /autoconv off ALL             — Disable for all agents\n'
      + '  /autoconv off grok            — Disable for grok\n'
      + '  /autoconv status ALL          — Show config for all agents\n'
      + '  /autoconv now ALL             — Trigger all immediately\n\n'
      + 'Frequency format: <min>[-<max>]/<duration>. Units: s, m, h, d.\n'
      + 'Only one schedule per agent — re-running /autoconv on replaces the previous.\n'
      + 'Only fires when the conversation is idle (no active interaction).',
  },
  '/new': {
    usage: '/new',
    short: 'Start a new conversation',
    detail: 'Starts a fresh conversation, disconnecting from the current one.',
  },
  '/conv': {
    usage: '/conv',
    short: 'List/switch conversations',
    detail: 'Shows a list of conversations to switch between.',
  },
  '/history': {
    usage: '/history [N] [offset]',
    short: 'Show conversation messages',
    detail: 'Display messages from the current conversation.\n\n'
      + '  /history          \u2014 Show last 50 messages\n'
      + '  /history 100      \u2014 Show last 100\n'
      + '  /history 50 100   \u2014 Show 50 messages starting from offset 100',
  },
  '/export': {
    usage: '/export [json|md]',
    short: 'Export conversation',
    detail: 'Export the current conversation as JSON or Markdown.',
  },
  '/rename': {
    usage: '/rename <title>',
    short: 'Rename current conversation',
    detail: 'Set a title for the current conversation.',
  },
  '/delete': {
    usage: '/delete <conversation_id>',
    short: 'Delete a conversation',
    detail: 'Permanently delete a conversation by ID.',
  },
  '/delete-msg': {
    usage: '/delete-msg <index>',
    short: 'Delete a message by index',
    detail: 'Remove a specific message from the conversation by its index.',
  },
  '/search': {
    usage: '/search <query>',
    short: 'Search messages in current conversation',
    detail: 'Search for text in all messages of the current conversation.',
  },
  '/model': {
    usage: '/model <name>',
    short: 'Switch LLM model',
    detail: 'Change the LLM model for the current agent.\n\n  /model gpt-4o\n  /model reset',
  },
  '/flow': {
    usage: '/flow list | templates | deploy | start | stop | params | undeploy | promote',
    short: 'Manage data flows',
    detail: 'Deploy, start, stop and manage data flows.\n\n'
      + '  /flow list                     \u2014 List deployed flows\n'
      + '  /flow templates                \u2014 List available templates\n'
      + '  /flow deploy <id> [scope]      \u2014 Deploy a flow\n'
      + '  /flow start <id> [key=val ...] \u2014 Start a flow\n'
      + '  /flow stop <id>                \u2014 Stop a flow\n'
      + '  /flow params <id>              \u2014 View flow parameters\n'
      + '  /flow undeploy <id>            \u2014 Remove a flow\n'
      + '  /flow promote <id>             \u2014 Promote to user scope',
  },
  '/prompt': {
    usage: '/prompt list | use <name>',
    short: 'Manage prompts',
    detail: 'List available prompts or view a specific prompt.\n\n'
      + '  /prompt list       \u2014 List all prompts\n'
      + '  /prompt use <name> \u2014 Show prompt content',
  },
  '/run': {
    usage: '/run <command>',
    short: 'Execute shell command via relay',
    detail: 'Run a command on the filesystem relay. Requires an active relay connection.',
  },
  '/diff': {
    usage: '/diff [file|ref]',
    short: 'Show git diff',
    detail: 'Show git diff via the filesystem relay.\n\n  /diff\n  /diff HEAD~1\n  /diff src/main.py',
  },
  '/copy': {
    usage: '/copy [N]',
    short: 'Copy last response to clipboard',
    detail: 'Copy the last (or Nth) assistant response to clipboard.',
  },
  '/paste': {
    usage: '/paste',
    short: 'Paste clipboard content',
    detail: 'Paste image or text from clipboard as an attachment.',
  },
  '/upload': {
    usage: '/upload',
    short: 'Upload a file',
    detail: 'Opens the file picker to upload a file as attachment.',
  },
  '/plan': {
    usage: '/plan [list | show <id> | approve <id> | cancel <id> | delete <id> | <description>]',
    short: 'View and manage plans',
    detail: 'View, approve, cancel, or delete plans. Or ask the agent to create one.\n\n'
      + '  /plan                      \u2014 Open the plans panel\n'
      + '  /plan list                 \u2014 List all plans in chat\n'
      + '  /plan approve <id>         \u2014 Approve a pending plan\n'
      + '  /plan cancel <id>          \u2014 Cancel a plan\n'
      + '  /plan delete <id>          \u2014 Delete a plan\n'
      + '  /plan <description>        \u2014 Ask the agent to create a plan\n',
  },
  '/watch': {
    usage: '/watch <path>|stop',
    short: 'Watch file for changes',
    detail: 'Not available in web UI. Use the CLI for file watching.',
  },
  '/clear-files': {
    usage: '/clear-files',
    short: 'Clear pending attachments',
    detail: 'Remove all queued file attachments.',
  },
  '/clear': {
    usage: '/clear',
    short: 'Clear the chat display',
    detail: 'Removes all messages from the visible chat. History is preserved server-side.',
  },
  '/clear-store': {
    usage: '/clear-store [agent|ALL]',
    short: 'Clean up FileStore files',
    detail: '/clear-store — delete all FileStore files for this conversation.\n/clear-store <agent> — delete tool results for a specific agent.\n/clear-store ALL — delete tool results for all agents.',
  },
  '/batch': {
    usage: '/batch <instruction> [--files <glob>]',
    short: 'Parallel changes across multiple files',
    detail: '/batch "add JSDoc to all functions" --files src/**/*.js\n/batch "convert to async/await" --files *.ts\nThe agent will split files into groups and use spawn_agents to process them in parallel.',
  },
  '/debug': {
    usage: '/debug [description]',
    short: 'Diagnose session issues',
    detail: 'Analyzes context state, recent errors, agent loops, and service health. Optionally describe the problem.',
  },
  '/loop': {
    usage: '/loop <interval> <prompt> | list | stop <key>',
    short: 'Run a prompt on a recurring interval',
    detail: '/loop 5m "check build status" — runs every 5 minutes\n/loop 30s /compact — runs /compact every 30s\n/loop 2-3/h "check deploy" — 2-3 times per hour (autoconv syntax)\n/loop 1/30s "ping" — once per 30 seconds\n/loop list — show active loops\n/loop stop <key> — stop a loop',
  },
  '/login': {
    usage: '/login',
    short: 'Re-authenticate',
    detail: 'Redirects to the login page.',
  },
};

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
    // Handle /help call [toolname] — show tool schema or list
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

async function cmdHelpToolList() {
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'get_tool_schemas' }),
    });
    const data = await resp.json();
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
  } catch (e) { addMsg('error', 'Failed: ' + e.message); }
}

async function cmdHelpTool(toolName) {
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'get_tool_schemas' }),
    });
    const data = await resp.json();
    const tools = data.tools || [];
    const tool = tools.find(t => t.name === toolName);
    if (!tool) {
      // Show all available tools
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
    // Build example call
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
  } catch (e) { addMsg('error', 'Failed to load tool schema: ' + e.message); }
}

function resolveAgentName(nameOrNick) {
  // Resolve a nickname to the real agent name, or return as-is
  if (!nameOrNick) return nameOrNick;
  for (const [real, nick] of Object.entries(nicknameMap)) {
    if (nick.toLowerCase() === nameOrNick.toLowerCase()) return real;
  }
  return nameOrNick;
}

function displayAgentName(realName) {
  // Return nickname if set, otherwise real name (case-insensitive lookup)
  const key = (realName || '').toLowerCase();
  for (const k of Object.keys(nicknameMap)) {
    if (k.toLowerCase() === key) return nicknameMap[k];
  }
  return realName || '';
}

function parseQuotedArgs(text) {
  // Parse command arguments supporting quoted strings: /cmd "arg one" "arg two" plain
  const args = [];
  const re = /"([^"]*)"|\S+/g;
  let m;
  while ((m = re.exec(text)) !== null) {
    args.push(m[1] !== undefined ? m[1] : m[0]);
  }
  return args;
}

async function handleSlashCommand(text) {
  const parts = text.split(/\s+/);
  const cmd = parts[0].toLowerCase();

  if (cmd === '/llm' || cmd === '/set_llm_service') {
    // /llm <agent> <service_or_variable>   or   /llm <agent> restore
    const agent = parts[1] || '';
    const svc = parts.slice(2).join(' ') || '';
    if (!agent || !svc) {
      addMsg('system', 'Usage: /llm <agent|assistant> <service_name|${variable}|restore>');
      return true;
    }
    try {
      const resp = await fetch(API, {
        method: 'POST', headers: getAuthHeaders(),
        body: JSON.stringify({
          action: 'set_llm_service', conversation_id: conversationId,
          agent_name: agent, llm_service: svc,
        }),
      });
      const data = await resp.json();
      addMsg('system', data.result || data.error || 'Done.');
    } catch (e) { addMsg('error', e.message); }
    return true;
  }

  if (cmd === '/stop') {
    const force = parts.includes('-f') || parts.includes('--force');
    const targetParts = parts.slice(1).filter(p => p !== '-f' && p !== '--force');
    // Default to current agent (or ALL if none selected)
    const target = targetParts.length > 0 ? resolveAgentName(targetParts[0]) : (selectedAgent || 'ALL');
    if (force) {
      await cancelAgent(target, true);
    } else {
      await cmdAgentInterrupt(target);
    }
    return true;
  }

  if (cmd === '/restart_from' || cmd === '/restart') {
    // Parse: /restart_from [agent|ALL] [N]
    let restartAgent = '';
    let restartN = 5;
    for (let i = 1; i < parts.length; i++) {
      const v = parseInt(parts[i]);
      if (!isNaN(v)) { restartN = v; }
      else { restartAgent = parts[i]; }
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
      // SSE compact_progress events handle the display
    }).catch(e => { addMsg('error', e.message); hideContextOp(); contextOpInProgress = false; })
      .finally(() => { hideContextOp(); contextOpInProgress = false; });
    return true;
  }

  if (cmd === '/resume') {
    const rargs = parseQuotedArgs(text);
    const target = resolveAgentName(rargs[1] || '');
    if (!target) { addMsg('system', 'Usage: /resume <agent|ALL>'); return true; }
    const resumeMsg = rargs.slice(2).join(' ') || 'Continue from where you left off.';
    if (target.toUpperCase() === 'ALL') { await cmdAgentMsgAll(resumeMsg); }
    else { await cmdAgentMsg(target, resumeMsg); }
    return true;
  }

  if (cmd === '/summary') {
    // Parse: /summary [agent|ALL] [tokens]
    let summaryAgent = '';
    let summaryTokens = 500;
    for (let i = 1; i < parts.length; i++) {
      const v = parseInt(parts[i]);
      if (!isNaN(v)) { summaryTokens = v; }
      else { summaryAgent = parts[i]; }
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

  if (cmd === '/help') {
    cmdHelp(parts.slice(1).join(' '));
    return true;
  }

  if (cmd === '/schedules') {
    const sub = (parts[1] || 'list').toLowerCase();
    if (sub === 'list') {
      await cmdSchedulesList();
    } else if (sub === 'del' || sub === 'delete') {
      await cmdSchedulesDel();
    } else if (sub === 'add' && parts[2]) {
      await cmdSchedulesAdd(parts[2], parts.slice(3).join(' '));
    } else {
      addMsg('system', 'Usage: /schedules list | /schedules del | /schedules add YYYYMMDDHHmmss [reason]');
    }
    return true;
  }

  if (cmd === '/compact') {
    if (contextOpInProgress) { addMsg('system', t('contextOpBusy')); return true; }
    cmdCompact(parts[1] || '');
    return true;
  }

  if (cmd === '/rebuild') {
    if (contextOpInProgress) { addMsg('system', t('contextOpBusy')); return true; }
    cmdRebuild(parts[1] || '');
    return true;
  }

  if (cmd === '/cost') {
    const cargs = parseQuotedArgs(text);
    const target = cargs[1] || selectedAgent || 'ALL';
    try {
      const resp = await fetch(API, {
        method: 'POST', headers: getAuthHeaders(),
        body: JSON.stringify({ action: 'cost', agent: target }),
        credentials: 'same-origin',
      });
      const data = await resp.json();
      const services = data.services || [];
      if (services.length === 0) {
        addMsg('system', 'No usage data found.');
      } else {
        const lines = services.map(s => {
          const svc = s.llm_service || '?';
          const model = s.model || '';
          const provider = s.provider || '';
          const tokIn = (s.tokens_in || 0).toLocaleString();
          const tokOut = (s.tokens_out || 0).toLocaleString();
          const calls = s.calls || 0;
          let line = svc + (model ? ' (' + model + ')' : '') + ': ' + tokIn + ' in / ' + tokOut + ' out (' + calls + ' calls)';
          if (s.cost !== undefined) {
            line += ' — $' + s.cost.toFixed(6);
          } else {
            line += ' — cost: not configured';
          }
          return line;
        });
        const totalIn = services.reduce((sum, s) => sum + (s.tokens_in || 0), 0);
        const totalOut = services.reduce((sum, s) => sum + (s.tokens_out || 0), 0);
        const totalCost = services.reduce((sum, s) => sum + (s.cost || 0), 0);
        lines.push('---');
        lines.push('Total: ' + totalIn.toLocaleString() + ' in / ' + totalOut.toLocaleString() + ' out'
          + (totalCost > 0 ? ' — $' + totalCost.toFixed(6) : ''));
        addMsg('system', lines.join('\n'));
      }
    } catch (e) { addMsg('error', 'Failed: ' + e.message); }
    return true;
  }

  if (cmd === '/rebuild_clean' || cmd === '/rebuild-full') {
    if (contextOpInProgress) { addMsg('system', t('contextOpBusy')); return true; }
    const rfAgent = parts[1] || '';
    if (!conversationId) { addMsg('system', t('noConv')); return true; }
    contextOpInProgress = true;
    const rfLabel = rfAgent ? 'Rebuilding full (' + rfAgent + ')' : 'Rebuilding full';
    showContextOp(rfLabel);
    const rfBody = { action: 'rebuild_full', conversation_id: conversationId };
    if (rfAgent) rfBody.agent_name = rfAgent;
    fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify(rfBody),
    }).then(r => r.json()).then(data => {
      if (data.error) { addMsg('error', 'Rebuild full failed: ' + data.error); hideContextOp(); contextOpInProgress = false; }
    }).catch(e => { addMsg('error', 'Rebuild full failed: ' + e.message); hideContextOp(); contextOpInProgress = false; });
    return true;
  }

  if (cmd === '/context') {
    await cmdShowContext(parts[1] || '');
    return true;
  }

  if (cmd === '/files') {
    toggleFilesPanel();
    return true;
  }

  if (cmd === '/flows') {
    toggleResourcesSection();
    return true;
  }

  if (cmd === '/tasks') {
    toggleSchedsPanel();
    return true;
  }

  if (cmd === '/tools') {
    await cmdToolsList();
    return true;
  }

  if (cmd === '/usage') {
    addMsg('system', '/usage is deprecated. Use /cost <agent|ALL> instead.');
    return true;
  }


  if (cmd === '/setname') {
    const sargs = parseQuotedArgs(text);
    const realName = sargs[1] || '';
    const nickname = sargs[2] || '';
    if (!realName) { addMsg('system', 'Usage: /setname <agent> [nickname]  (omit nickname to reset)'); return true; }
    await cmdAgentSetname(realName, nickname || realName);
    return true;
  }

  if (cmd === '/msg') {
    const margs = parseQuotedArgs(text);
    let target = resolveAgentName(margs[1] || '');
    let msgText = margs.slice(2).join(' ');
    // If target not found, treat it as message to selected agent
    if (!target && margs[1] && selectedAgent) {
      target = selectedAgent;
      msgText = margs.slice(1).join(' ');
    }
    if (!target) { addMsg('system', 'Usage: /msg [agent] <message> (defaults to selected agent)'); }
    else if (!msgText) { addMsg('system', 'Usage: /msg ' + target + ' <message>'); }
    else if (target.toUpperCase() === 'ALL') { await cmdAgentMsgAll(msgText); }
    else { await cmdAgentMsg(target, msgText); }
    return true;
  }

  if (cmd === '/btw') {
    const bargs = parseQuotedArgs(text);
    let target = resolveAgentName(bargs[1] || '');
    let btwText = bargs.slice(2).join(' ');
    // If target not found, treat it as message to selected agent
    if (!target && bargs[1] && selectedAgent) {
      target = selectedAgent;
      btwText = bargs.slice(1).join(' ');
    }
    if (!btwText && !target) { addMsg('system', 'Usage: /btw [agent] <question> (defaults to selected agent)'); }
    else if (!btwText) {
      await cmdAgentBtw('', target + ' ' + bargs.slice(2).join(' '));
    } else {
      await cmdAgentBtw(target, btwText);
    }
    return true;
  }

  if (cmd === '/task') {
    const sub = (parts[1] || 'status').toLowerCase();
    if (sub === 'create') {
      // Parse: /task create <name> --prompt "..." [--criteria "..."] [--interval XX]
      // Also supports: /task create <name> "inline prompt" [--criteria "..."]
      const rawText = text.replace(/^\/task\s+create\s+/i, '');
      // Extract name (first word)
      const nameMatch = rawText.match(/^(\S+)/);
      const taskName = nameMatch ? nameMatch[1] : '';
      const afterName = rawText.substring(taskName.length).trim();
      // Extract --option "value" or --option value pairs
      function extractOpt(txt, opt) {
        // Match --opt "multi\nline\ncontent" or --opt value
        const re = new RegExp('--' + opt + '\\s+(?:"([\\s\\S]*?)"|\'([\\s\\S]*?)\'|(\\S+))', 'i');
        const m = txt.match(re);
        return m ? (m[1] ?? m[2] ?? m[3] ?? '') : '';
      }
      let taskPrompt = extractOpt(afterName, 'prompt');
      let criteria = extractOpt(afterName, 'criteria');
      let interval = extractOpt(afterName, 'interval');
      // Fallback: if no --prompt, treat first quoted arg as prompt (old syntax)
      if (!taskPrompt) {
        const qargs = parseQuotedArgs(text);
        taskPrompt = qargs[3] || '';
        if (!criteria) {
          for (let i = 4; i < qargs.length; i++) {
            if (qargs[i] === '--criteria' && qargs[i+1]) criteria = qargs[++i];
            else if (qargs[i] === '--interval' && qargs[i+1]) interval = qargs[++i];
          }
        }
      }
      if (!taskName || !taskPrompt) {
        addMsg('system', 'Usage: /task create <name> --prompt "..." [--criteria "..."] [--interval XX]\n       /task create <name> "inline prompt" [--criteria "..."]');
        return true;
      }
      fetch(API, {
        method: 'POST', headers: getAuthHeaders(),
        body: JSON.stringify({
          action: 'create_task_def',
          name: taskName,
          data: { prompt: taskPrompt, criteria, default_interval: interval || '6/1m' },
        }),
      }).then(r => r.json()).then(data => {
        if (data.error) addMsg('error', data.error);
        else addMsg('system', `Task definition '${taskName}' created.`);
      }).catch(e => addMsg('error', e.message));
    } else if (sub === 'assign') {
      // /task assign <agent> <taskname_or_"description"> [--interval N] [--max N] [--verifier <agent>] [--criteria "<text>"]
      const qargs = parseQuotedArgs(text);
      const taskAgent = qargs[2] || '';
      const taskArg = qargs[3] || '';
      if (!taskAgent || !taskArg) {
        addMsg('system', 'Usage: /task assign <agent> <taskname> [--interval N]\n       /task assign <agent> "<inline description>" [--criteria "..."] [--interval N]');
        return true;
      }
      let interval = null, maxIter = 50, verifier = '', criteria = '';
      const variables = {};
      for (let i = 4; i < qargs.length; i++) {
        if (qargs[i] === '--interval' && qargs[i+1]) { interval = qargs[++i]; }
        else if (qargs[i] === '--max' && qargs[i+1]) { maxIter = parseInt(qargs[++i]) || 50; }
        else if (qargs[i] === '--verifier' && qargs[i+1]) { verifier = qargs[++i]; }
        else if (qargs[i] === '--criteria' && qargs[i+1]) { criteria = qargs[++i]; }
        else if (qargs[i] === '--var' && qargs[i+1]) {
          const kv = qargs[++i];
          const eq = kv.indexOf('=');
          if (eq > 0) variables[kv.substring(0, eq)] = kv.substring(eq + 1);
        }
      }
      // Detect library name vs inline description:
      // If taskArg has no spaces and no --criteria was given → library lookup
      const isLibrary = !taskArg.includes(' ') && !criteria;
      const body = {
        action: 'assign_task', conversation_id: conversationId,
        agent_name: taskAgent, max_iterations: maxIter, verifier,
        ...(interval != null ? { interval } : {}),
        ...(Object.keys(variables).length ? { variables } : {}),
      };
      if (isLibrary) {
        body.task_def_name = taskArg;
      } else {
        body.task = taskArg;
        body.completion_criteria = criteria;
      }
      fetch(API, {
        method: 'POST', headers: getAuthHeaders(),
        body: JSON.stringify(body),
      }).then(r => r.json()).then(data => {
        if (data.error) { addMsg('error', data.error); }
        else { addMsg('system', data.result || 'Task assigned.'); }
      }).catch(e => addMsg('error', e.message));
    } else if (sub === 'delete' || sub === 'del') {
      // /task delete <taskname> — delete a task definition from library
      const taskName = parts[2] || '';
      if (!taskName) { addMsg('system', 'Usage: /task delete <taskname>'); return true; }
      fetch(API, {
        method: 'POST', headers: getAuthHeaders(),
        body: JSON.stringify({
          action: 'delete_task_def',
          name: taskName,
        }),
      }).then(r => r.json()).then(data => {
        if (data.error) addMsg('error', data.error);
        else addMsg('system', `Task definition '${taskName}' deleted.`);
      }).catch(e => addMsg('error', e.message));
    } else if (sub === 'status' || sub === 'list') {
      const listAgent = parts[2] || '';
      // Show both library definitions and running instances
      const listBody = { action: 'task_status', conversation_id: conversationId, include_library: true };
      if (listAgent) listBody.agent_name = listAgent;
      fetch(API, {
        method: 'POST', headers: getAuthHeaders(),
        body: JSON.stringify(listBody),
      }).then(r => r.json()).then(data => {
        const defs = data.definitions || [];
        const tasks = data.tasks || [];
        const lines = [];
        if (defs.length) {
          lines.push('**Library:**');
          for (const d of defs) {
            lines.push('\u2022 `' + d.name + '` — ' + (d.description || d.prompt.substring(0, 60)) + ' [' + (d.default_interval || '6/1m') + ']');
          }
        }
        if (tasks.length) {
          if (lines.length) lines.push('');
          lines.push('**Running:**');
          for (const t of tasks) {
            let line = '\u2022 `' + (t.task_id || '?') + '` ' + t.agent + ': ' + t.task.substring(0, 80);
            const ivLabel = typeof t.interval === 'object' ? (t.interval.spec || t.interval.min + '-' + t.interval.max + 's') : t.interval + 's';
            line += ' [' + t.status + ', iter ' + t.iterations + '/' + t.max_iterations + ', ' + ivLabel + ']';
            if (t.task_def_name) line += ' (def: ' + t.task_def_name + ')';
            if (t.verifier) line += ' (verifier: ' + t.verifier + ')';
            if (t.last_result) line += '\n  Last: ' + t.last_result.substring(0, 100);
            lines.push(line);
          }
        }
        if (!lines.length) addMsg('system', 'No task definitions or running tasks.');
        else addMsg('system', lines.join('\n'));
      }).catch(e => addMsg('error', e.message));
    } else if (sub === 'pause' || sub === 'resume' || sub === 'cancel') {
      const taskAgent = parts[2];
      if (!taskAgent) { addMsg('system', 'Usage: /task ' + sub + ' <task_id|agent>'); return true; }
      fetch(API, {
        method: 'POST', headers: getAuthHeaders(),
        body: JSON.stringify({
          action: sub + '_task', conversation_id: conversationId,
          task_id: taskAgent.startsWith('t_') ? taskAgent : '',
          agent_name: taskAgent.startsWith('t_') ? '' : taskAgent,
        }),
      }).then(r => r.json()).then(data => {
        if (data.error) { addMsg('error', data.error); }
        else { addMsg('system', 'Task ' + sub + 'd for ' + taskAgent + '.'); }
      }).catch(e => addMsg('error', e.message));
    } else {
      addMsg('system', 'Usage: /task create | assign | list | delete | pause | resume | cancel');
    }
    return true;
  }

  if (cmd === '/call') {
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
    // Submit — tool_call + tool_result will arrive via SSE events
    // (same display path as agent tool calls)
    showTyping();
    fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({
        action: 'call_tool',
        tool_name: parsed.name,
        arguments: parsed.args,
        positional_args: parsed.positional || [],
        conversation_id: conversationId,
      }),
    }).then(r => r.json()).then(data => {
      if (data.error) {
        hideTyping();
        addMsg('error', data.error);
      }
      // No display here — SSE tool_call + tool_result events handle it
    }).catch(e => { hideTyping(); addMsg('error', 'Tool call failed: ' + e.message); });
    return true;
  }

  if (cmd === '/vidservice') {
    const sub = (parts[1] || 'list').toLowerCase();
    if (sub === 'list') {
      try {
        const resp = await fetch(API, {
          method: 'POST', headers: getAuthHeaders(),
          body: JSON.stringify({ action: 'list_video_services', conversation_id: conversationId }),
        });
        const services = await resp.json();
        if (!Array.isArray(services) || services.length === 0) {
          addMsg('system', 'No video generation services deployed.');
        } else {
          const lines = services.map(s => {
            let line = '  \u2022 ' + s.id + ' (' + s.type + ', ' + s.scope + ')';
            if (s.selected_for && s.selected_for.length > 0) {
              line += ' \u2190 selected for: ' + s.selected_for.join(', ');
            }
            return line;
          });
          addMsg('system', 'Video services available:\n' + lines.join('\n'));
        }
      } catch (e) { addMsg('error', e.message); }
    } else if (sub === 'select' && parts[2]) {
      const serviceName = parts[2];
      const agentName = parts[3] || '*';
      try {
        const resp = await fetch(API, {
          method: 'POST', headers: getAuthHeaders(),
          body: JSON.stringify({
            action: 'set_video_service', conversation_id: conversationId,
            service_name: serviceName, agent_name: agentName,
          }),
        });
        const data = await resp.json();
        if (data.ok) {
          const target = agentName === '*' ? 'all agents' : agentName;
          addMsg('system', 'Video service set to "' + serviceName + '" for ' + target + '.');
        } else {
          addMsg('error', data.error || 'Failed to set video service');
        }
      } catch (e) { addMsg('error', e.message); }
    } else if (sub === 'clear') {
      const agentName = parts[2] || '';
      try {
        const resp = await fetch(API, {
          method: 'POST', headers: getAuthHeaders(),
          body: JSON.stringify({
            action: 'clear_video_service', conversation_id: conversationId,
            agent_name: agentName,
          }),
        });
        const data = await resp.json();
        if (data.ok) {
          addMsg('system', agentName
            ? 'Video service preference cleared for ' + agentName + '.'
            : 'All video service preferences cleared.');
        } else {
          addMsg('error', data.error || 'Failed to clear');
        }
      } catch (e) { addMsg('error', e.message); }
    } else {
      addMsg('system', 'Usage: /vidservice list | select <name> [agent] | clear [agent]');
    }
    return true;
  }

  if (cmd === '/imgservice') {
    const sub = (parts[1] || 'list').toLowerCase();
    if (sub === 'list') {
      try {
        const resp = await fetch(API, {
          method: 'POST', headers: getAuthHeaders(),
          body: JSON.stringify({ action: 'list_image_services', conversation_id: conversationId }),
        });
        const services = await resp.json();
        if (!Array.isArray(services) || services.length === 0) {
          addMsg('system', 'No image generation services deployed.');
        } else {
          const lines = services.map(s => {
            let line = '  \u2022 ' + s.id + ' (' + s.type + ', ' + s.scope + ')';
            if (s.selected_for && s.selected_for.length > 0) {
              line += ' \u2190 selected for: ' + s.selected_for.join(', ');
            }
            return line;
          });
          addMsg('system', 'Image services available:\n' + lines.join('\n'));
        }
      } catch (e) { addMsg('error', e.message); }
    } else if (sub === 'select' && parts[2]) {
      const serviceName = parts[2];
      const agentName = parts[3] || '*';
      try {
        const resp = await fetch(API, {
          method: 'POST', headers: getAuthHeaders(),
          body: JSON.stringify({
            action: 'set_image_service', conversation_id: conversationId,
            service_name: serviceName, agent_name: agentName,
          }),
        });
        const data = await resp.json();
        if (data.ok) {
          const target = agentName === '*' ? 'all agents' : agentName;
          addMsg('system', 'Image service set to "' + serviceName + '" for ' + target + '.');
        } else {
          addMsg('error', data.error || 'Failed to set image service');
        }
      } catch (e) { addMsg('error', e.message); }
    } else if (sub === 'clear') {
      const agentName = parts[2] || '';
      try {
        const resp = await fetch(API, {
          method: 'POST', headers: getAuthHeaders(),
          body: JSON.stringify({
            action: 'clear_image_service', conversation_id: conversationId,
            agent_name: agentName,
          }),
        });
        const data = await resp.json();
        if (data.ok) {
          addMsg('system', agentName
            ? 'Image service preference cleared for ' + agentName + '.'
            : 'All image service preferences cleared.');
        } else {
          addMsg('error', data.error || 'Failed to clear');
        }
      } catch (e) { addMsg('error', e.message); }
    } else {
      addMsg('system', 'Usage: /imgservice list | select <name> [agent] | clear [agent]');
    }
    return true;
  }

  if (cmd === '/agent') {
    const qargs = parseQuotedArgs(text);  // handles "quoted agent names"
    const sub = (qargs[1] || 'list').toLowerCase();
    if (sub === 'list') {
      await cmdAgentList();
    } else if (sub === 'create') {
      await cmdAgentCreate();
    } else if (sub === 'select') {
      const name = resolveAgentName(qargs[2] || '');
      await cmdAgentSelect(name);
    } else if (sub === 'delete' || sub === 'del') {
      const name = resolveAgentName(qargs[2]);
      if (!name) { addMsg('system', 'Usage: /agent delete <name>'); }
      else { await cmdAgentDelete(name); }
    } else if (sub === 'msg' || sub === 'message') {
      const target = resolveAgentName(qargs[2] || '');
      const msgText = qargs.slice(3).join(' ');
      if (!target) { addMsg('system', 'Usage: /agent msg <name|ALL> <message>'); }
      else if (!msgText) { addMsg('system', 'Usage: /agent msg ' + target + ' <message>'); }
      else if (target.toUpperCase() === 'ALL') { await cmdAgentMsgAll(msgText); }
      else { await cmdAgentMsg(target, msgText); }
    } else if (sub === 'interrupt' || sub === 'int') {
      const target = resolveAgentName(qargs[2] || '');
      await cmdAgentInterrupt(target);
    } else if (sub === 'btw') {
      const target = resolveAgentName(qargs[2] || '');
      const btwText = qargs.slice(3).join(' ');
      if (!btwText && !target) { addMsg('system', 'Usage: /agent btw <name|ALL> <question>'); }
      else if (!btwText) {
        // No agent name given — treat target as message, send to assistant
        await cmdAgentBtw('', target + ' ' + qargs.slice(3).join(' '));
      } else {
        await cmdAgentBtw(target, btwText);
      }
    } else if (sub === 'resume') {
      const target = resolveAgentName(qargs[2] || '');
      const resumeMsg = qargs.slice(3).join(' ') || 'Continue from where you left off.';
      if (target.toUpperCase() === 'ALL') { await cmdAgentMsgAll(resumeMsg); }
      else if (target) { await cmdAgentMsg(target, resumeMsg); }
      else {
        // Resume default assistant
        sending = true;
        const body = { message: resumeMsg };
        if (conversationId) body.conversation_id = conversationId;
        addMsg('user', resumeMsg);
        showTyping();
        try {
          const resp = await fetch(API, { method: 'POST', headers: getAuthHeaders(), body: JSON.stringify(body) });
          const data = await resp.json();
          if (data.conversation_id && !conversationId) { conversationId = data.conversation_id; connectSSE(conversationId); }
        } catch(e) { addMsg('error', e.message); hideTyping(); }
        sending = false;
      }
    } else if (sub === 'setname' || sub === 'rename') {
      const qargs = parseQuotedArgs(text);  // ['/agent', 'setname', 'realname', 'nickname']
      const realName = qargs[2] || '';
      const nickname = qargs[3] || '';
      if (!realName) {
        addMsg('system', 'Usage: /agent setname <realname> [nickname]  (omit nickname to reset)');
      } else {
        await cmdAgentSetname(realName, nickname || realName);
      }
    } else if (sub === 'disable' && parts[2]) {
      try {
        const resp = await fetch(API, { method: 'POST', headers: getAuthHeaders(),
          body: JSON.stringify({ action: 'manage_resource', resource_type: 'agent', name: parts[2],
            data: {}, conversation_id: conversationId, _action: 'disable' }),
        });
        // manage_resource doesn't have direct disable — use dedicated action
        const resp2 = await fetch(API, { method: 'POST', headers: getAuthHeaders(),
          body: JSON.stringify({ action: 'agent_disable', agent_name: parts[2], conversation_id: conversationId }),
        });
        const data = await resp2.json();
        addMsg('system', data.result || data.error || 'Agent disabled.');
      } catch (e) { addMsg('error', e.message); }
    } else if (sub === 'enable' && parts[2]) {
      try {
        const resp = await fetch(API, { method: 'POST', headers: getAuthHeaders(),
          body: JSON.stringify({ action: 'agent_enable', agent_name: parts[2], conversation_id: conversationId }),
        });
        const data = await resp.json();
        addMsg('system', data.result || data.error || 'Agent enabled.');
      } catch (e) { addMsg('error', e.message); }
    } else if (sub === 'promote' && parts[2] && parts[3]) {
      try {
        const resp = await fetch(API, { method: 'POST', headers: getAuthHeaders(),
          body: JSON.stringify({ action: 'agent_promote', agent_name: parts[2], target_scope: parts[3],
            conversation_id: conversationId }),
        });
        const data = await resp.json();
        addMsg('system', data.result || data.error || 'Agent promoted.');
      } catch (e) { addMsg('error', e.message); }
    } else if (sub === 'create-conv') {
      const qargs = parseQuotedArgs(text);
      const cname = qargs[2] || '';
      const cprompt = qargs[3] || '';
      if (!cname || !cprompt) { addMsg('system', 'Usage: /agent create-conv <name> "<prompt>"'); return true; }
      try {
        const resp = await fetch(API, { method: 'POST', headers: getAuthHeaders(),
          body: JSON.stringify({ action: 'create_agent', conversation_id: conversationId,
            name: cname, prompt: cprompt, scope: 'conversation' }),
        });
        const data = await resp.json();
        addMsg('system', data.result || data.error || 'Agent created.');
      } catch (e) { addMsg('error', e.message); }
    } else {
      addMsg('system', 'Usage: /agent list | create | create-conv | select | delete | msg | disable | enable | promote | setname');
    }
    return true;
  }

  if (cmd === '/memory') {
    const sub = (parts[1] || '').toLowerCase();
    if (!sub || sub === 'panel') {
      // No subcommand or /memory panel → open overlay
      await cmdShowMemories();
    } else if (sub === 'list') {
      const agentFilter = parts[2] || null;
      await cmdMemoryList(agentFilter);
    } else if (sub === 'del' || sub === 'delete') {
      const memId = parts[2];
      if (!memId) { addMsg('system', 'Usage: /memory del <memory_id>'); }
      else { await cmdMemoryDel(memId); }
    } else if (sub === 'add') {
      // /memory add text here #tag1 #tag2 @agent
      const rest = text.replace(/^\/memory\s+add\s*/i, '');
      if (!rest.trim()) { addMsg('system', 'Usage: /memory add <text> [#tag1 #tag2] [@agent]'); return true; }
      // Extract @agent from end
      const agentMatch = rest.match(/@(\S+)\s*$/);
      let agent = '';
      let memText = rest;
      if (agentMatch) { agent = agentMatch[1]; memText = rest.slice(0, agentMatch.index).trim(); }
      // Extract #tags
      const tagMatches = memText.match(/#(\S+)/g) || [];
      const tags = tagMatches.map(t => t.slice(1));
      memText = memText.replace(/#\S+/g, '').trim();
      if (!memText) { addMsg('system', 'Usage: /memory add <text> [#tag1 #tag2] [@agent]'); return true; }
      try {
        const resp = await fetch(API, {
          method: 'POST', headers: getAuthHeaders(),
          body: JSON.stringify({ action: 'add_memory', text: memText, tags, agent }),
        });
        const data = await resp.json();
        addMsg('system', 'Memory added (id: ' + (data.id || '?') + ', agent: ' + (data.agent || 'global') + ')');
      } catch (e) { addMsg('error', e.message); }
    } else if (sub === 'edit') {
      const memId = parts[2];
      const newText = parts.slice(3).join(' ');
      if (!memId || !newText) { addMsg('system', 'Usage: /memory edit <id> <new text>'); return true; }
      try {
        const resp = await fetch(API, {
          method: 'POST', headers: getAuthHeaders(),
          body: JSON.stringify({ action: 'edit_memory', memory_id: memId, text: newText }),
        });
        const data = await resp.json();
        addMsg('system', data.updated ? 'Memory updated.' : 'Memory not found.');
      } catch (e) { addMsg('error', e.message); }
    } else if (sub === 'search') {
      const query = parts.slice(2).join(' ');
      if (!query) { addMsg('system', 'Usage: /memory search <query>'); return true; }
      await cmdMemoryList(null, query);
    } else {
      addMsg('system', 'Usage: /memory [list [agent] | add | edit | del | search | panel]');
    }
    return true;
  }

  if (cmd === '/install') {
    addMsg('system', 'To install a tool, drag & drop a .py file into the chat or paste the code with:\n/install filename.py\n```python\n# your code here\n```');
    return true;
  }

  if (cmd === '/uninstall') {
    const toolName = parts[1];
    if (!toolName) { addMsg('system', 'Usage: /uninstall <tool_name>'); return true; }
    await cmdUninstallTool(toolName);
    return true;
  }

  if (cmd === '/link') {
    const sub = (parts[1] || '').toLowerCase();
    if (sub === 'status' || !sub) {
      await cmdLinkStatus();
    } else if (sub === 'unlink') {
      const provider = parts[2] || '';
      if (!provider) { addMsg('system', 'Usage: /link unlink <provider>'); return true; }
      await cmdUnlinkAccount(provider);
    } else {
      // /link <provider> <id> [bot_token]
      const provider = parts[1];
      const providerId = parts[2] || '';
      const botToken = parts[3] || '';
      if (!providerId) { addMsg('system', 'Usage: /link <provider> <id> [bot_token]'); return true; }
      await cmdLinkAccount(provider, providerId, botToken);
    }
    return true;
  }

  if (cmd === '/add-secret') {
    const name = parts[1];
    const value = parts.slice(2).join(' ');
    if (!name || !value) { addMsg('system', t('secretAddUsage')); return true; }
    await cmdAddSecret(name, value);
    return true;
  }

  if (cmd === '/list-secrets' || cmd === '/secrets') {
    await cmdListSecrets();
    return true;
  }

  if (cmd === '/add-variable' || cmd === '/add-var') {
    const name = parts[1];
    const value = parts.slice(2).join(' ');
    if (!name || !value) { addMsg('system', t('variableAddUsage')); return true; }
    await cmdAddVariable(name, value);
    return true;
  }

  if (cmd === '/list-variables' || cmd === '/variables' || cmd === '/vars') {
    await cmdListVariables();
    return true;
  }

  if (cmd === '/skill') {
    const sub = (parts[1] || 'list').toLowerCase();
    if (sub === 'list') {
      await cmdSkillList();
    } else if (sub === 'add' || sub === 'create') {
      const name = parts[2];
      const prompt = parts.slice(3).join(' ');
      if (!name || !prompt) { addMsg('system', 'Usage: /skill add <name> <prompt>'); return true; }
      await cmdResourceAction('create_skill', {name, prompt});
    } else if (sub === 'del' || sub === 'delete') {
      const name = parts[2];
      if (!name) { addMsg('system', 'Usage: /skill del <name>'); return true; }
      await cmdResourceAction('delete_skill', {name});
    } else {
      addMsg('system', 'Usage: /skill list | add <name> <prompt> | del <name>');
    }
    return true;
  }

  if (cmd === '/add-skill') {
    const name = parts[1];
    const prompt = parts.slice(2).join(' ');
    if (!name || !prompt) { addMsg('system', 'Usage: /add-skill <name> <prompt>'); return true; }
    await cmdResourceAction('create_skill', {name, prompt});
    return true;
  }

  if (cmd === '/resources') {
    await cmdListResources();
    return true;
  }

  if (cmd === '/activate') {
    const rtype = parts[1];
    const rname = parts[2];
    if (!rtype || !rname) { addMsg('system', 'Usage: /activate <agent|skill|mcp> <name>'); return true; }
    await cmdResourceAction('activate_resource', {resource_type: rtype, name: rname});
    return true;
  }

  if (cmd === '/deactivate') {
    const rtype = parts[1];
    const rname = parts[2];
    if (!rtype || !rname) { addMsg('system', 'Usage: /deactivate <agent|skill|mcp> <name>'); return true; }
    await cmdResourceAction('deactivate_resource', {resource_type: rtype, name: rname});
    return true;
  }

  if (cmd === '/share') {
    const rtype = parts[1];
    const rname = parts[2];
    const targetConv = parts[3];
    if (!rtype || !rname || !targetConv) {
      addMsg('system', 'Usage: /share <agent|skill|mcp> <name> <conversation_id>');
      return true;
    }
    await cmdResourceAction('share_resource', {
      resource_type: rtype, name: rname, target_conversation_id: targetConv
    });
    return true;
  }

  if (cmd === '/view') {
    const filename = parts.slice(1).join(' ');
    if (!filename) { addMsg('system', 'Usage: /view <filename>'); return true; }
    openFileViewer(filename);
    return true;
  }

  if (cmd === '/service') {
    const sub = (parts[1] || 'list').toLowerCase();
    if (sub === 'list') {
      await cmdServiceList();
    } else if (sub === 'install') {
      const svcType = parts[2];
      const svcName = parts[3];
      const configStr = parts.slice(4).join(' ');
      if (!svcType || !svcName) {
        addMsg('system', 'Usage: /service install <type> <name> [key=val,key2=val2,...]');
        return true;
      }
      await cmdServiceAction('service_install', {
        service_type: svcType, service_name: svcName, config_str: configStr
      });
    } else if (sub === 'uninstall') {
      const svcName = parts[2];
      if (!svcName) { addMsg('system', 'Usage: /service uninstall <name>'); return true; }
      await cmdServiceAction('service_uninstall', {service_id: svcName});
    } else if (sub === 'enable') {
      const svcName = parts[2];
      if (!svcName) { addMsg('system', 'Usage: /service enable <name>'); return true; }
      await cmdServiceAction('service_enable', {service_id: svcName});
    } else if (sub === 'disable') {
      const svcName = parts[2];
      if (!svcName) { addMsg('system', 'Usage: /service disable <name>'); return true; }
      await cmdServiceAction('service_disable', {service_id: svcName});
    } else {
      addMsg('system', 'Usage: /service list | install <type> <name> [config] | uninstall <name> | enable <name> | disable <name>');
    }
    return true;
  }

  if (cmd === '/autoconv') {
    if (!conversationId) { addMsg('system', t('thoughtNoConv')); return true; }
    const qargs = parseQuotedArgs(text);  // ['/autoconv', sub, agent, freq]
    const sub = (qargs[1] || '').toLowerCase();
    if (!sub || !['on', 'off', 'status', 'now'].includes(sub)) {
      addMsg('system', 'Usage: /autoconv <on|off|status|now> <agent|ALL> [freq]');
      return true;
    }
    const body = { action: 'random_thought', conversation_id: conversationId, sub };
    const freqPattern = /^\d+(-\d+)?\/\d*[smhd]$/;
    if (sub === 'on') {
      // /autoconv on <agent> [freq] OR /autoconv on ALL [freq]
      if (!qargs[2]) { addMsg('system', 'Usage: /autoconv on <agent|ALL> [freq]'); return true; }
      if (freqPattern.test(qargs[2])) {
        // /autoconv on 3/h — missing agent
        addMsg('system', 'Usage: /autoconv on <agent|ALL> [freq]');
        return true;
      }
      body.agent = resolveAgentName(qargs[2]);
      body.frequency = qargs[3] || '6/1m';
    } else {
      // off, status, now — require agent
      if (!qargs[2]) { addMsg('system', 'Usage: /autoconv ' + sub + ' <agent|ALL>'); return true; }
      body.agent = resolveAgentName(qargs[2]);
    }
    try {
      const resp = await fetch(API, { method: 'POST', headers: getAuthHeaders(), body: JSON.stringify(body) });
      const data = await resp.json();
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
    } catch (e) { addMsg('error', 'Failed: ' + e.message); }
    return true;
  }

  if (cmd === '/new') {
    newChat();
    return true;
  }

  if (cmd === '/conv') {
    loadConversations();
    return true;
  }

  if (cmd === '/history') {
    if (!conversationId) { addMsg('system', t('noConv')); return true; }
    const n = parseInt(parts[1]) || 50;
    const offset = parseInt(parts[2]) || 0;
    try {
      const resp = await fetch(API, {
        method: 'POST', headers: getAuthHeaders(),
        body: JSON.stringify({ action: 'load_history', conversation_id: conversationId, limit: n, offset }),
      });
      const data = await resp.json();
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
    } catch (e) { addMsg('error', 'Failed: ' + e.message); }
    return true;
  }

  if (cmd === '/export') {
    if (!conversationId) { addMsg('system', t('noConv')); return true; }
    const fmt = parts[1] || 'markdown';
    try {
      const resp = await fetch(API, {
        method: 'POST', headers: getAuthHeaders(),
        body: JSON.stringify({ action: 'export', conversation_id: conversationId, format: fmt }),
      });
      const data = await resp.json();
      if (data.error) { addMsg('error', data.error); }
      else if (data.url) {
        const a = document.createElement('a');
        a.href = data.url;
        a.download = data.filename || 'export';
        a.click();
        addMsg('system', 'Exported: ' + (data.filename || data.url));
      }
    } catch (e) { addMsg('error', 'Export failed: ' + e.message); }
    return true;
  }

  if (cmd === '/rename') {
    if (!conversationId) { addMsg('system', t('noConv')); return true; }
    const title = text.slice(cmd.length).trim();
    if (!title) { addMsg('system', 'Usage: /rename <new title>'); return true; }
    try {
      const resp = await fetch(API, {
        method: 'POST', headers: getAuthHeaders(),
        body: JSON.stringify({ action: 'set_conv_title', conversation_id: conversationId, title }),
      });
      const data = await resp.json();
      if (data.error) { addMsg('error', data.error); }
      else { addMsg('system', 'Renamed to: ' + title); }
    } catch (e) { addMsg('error', 'Rename failed: ' + e.message); }
    return true;
  }

  if (cmd === '/delete') {
    const target = parts[1] || '';
    if (!target) { addMsg('system', 'Usage: /delete <conversation_id>'); return true; }
    try {
      const resp = await fetch(API, {
        method: 'POST', headers: getAuthHeaders(),
        body: JSON.stringify({ action: 'delete_conversation', conversation_id: target }),
      });
      const data = await resp.json();
      if (data.error) { addMsg('error', data.error); }
      else if (data.deleted) {
        addMsg('system', 'Deleted ' + target.slice(0, 8));
        if (conversationId === target) { newChat(); }
      }
    } catch (e) { addMsg('error', 'Delete failed: ' + e.message); }
    return true;
  }

  if (cmd === '/delete-msg') {
    if (!conversationId) { addMsg('system', t('noConv')); return true; }
    const idx = parseInt(parts[1]);
    if (isNaN(idx)) { addMsg('system', 'Usage: /delete-msg <index>'); return true; }
    try {
      const resp = await fetch(API, {
        method: 'POST', headers: getAuthHeaders(),
        body: JSON.stringify({ action: 'delete_message', conversation_id: conversationId, index: idx }),
      });
      const data = await resp.json();
      if (data.error) { addMsg('error', data.error); }
      else { addMsg('system', 'Message ' + idx + ' deleted'); }
    } catch (e) { addMsg('error', 'Failed: ' + e.message); }
    return true;
  }

  if (cmd === '/search') {
    if (!conversationId) { addMsg('system', t('noConv')); return true; }
    const query = text.slice(cmd.length).trim();
    if (!query) { addMsg('system', 'Usage: /search <query>'); return true; }
    try {
      const resp = await fetch(API, {
        method: 'POST', headers: getAuthHeaders(),
        body: JSON.stringify({ action: 'load_history', conversation_id: conversationId, limit: 500, offset: 0 }),
      });
      const data = await resp.json();
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
    } catch (e) { addMsg('error', 'Search failed: ' + e.message); }
    return true;
  }

  if (cmd === '/model') {
    const modelName = parts[1] || '';
    if (!modelName) { addMsg('system', 'Usage: /model <name>'); return true; }
    try {
      const resp = await fetch(API, {
        method: 'POST', headers: getAuthHeaders(),
        body: JSON.stringify({ action: 'model', model: modelName, agent: '', conversation_id: conversationId || '' }),
      });
      const data = await resp.json();
      addMsg('system', data.message || data.error || 'Model updated');
    } catch (e) { addMsg('error', 'Failed: ' + e.message); }
    return true;
  }

  if (cmd === '/flow') {
    const sub = (parts[1] || 'list').toLowerCase();
    if (sub === 'list') {
      try {
        const resp = await fetch(API, {
          method: 'POST', headers: getAuthHeaders(),
          body: JSON.stringify({ action: 'list_conv_flows' }),
        });
        const data = await resp.json();
        const flows = data.flows || [];
        if (!flows.length) { addMsg('system', 'No deployed flows.'); }
        else {
          const lines = flows.map(function(f) { return (f.status === 'running' ? '\u25b6' : '\u23f9') + ' ' + f.id + ' \u2014 ' + f.name + ' [' + f.status + ']'; });
          addMsg('system', 'Flows:\n' + lines.join('\n'));
        }
      } catch (e) { addMsg('error', e.message); }
    } else if (sub === 'templates') {
      try {
        const resp = await fetch(API, {
          method: 'POST', headers: getAuthHeaders(),
          body: JSON.stringify({ action: 'list_available_flows' }),
        });
        const data = await resp.json();
        const templates = data.templates || [];
        if (!templates.length) { addMsg('system', 'No flow templates.'); }
        else {
          const lines = templates.map(function(tmpl) { return tmpl.id + (tmpl.version ? ' v' + tmpl.version : '') + ' \u2014 ' + tmpl.name + ' (' + tmpl.tasks_count + ' tasks)'; });
          addMsg('system', 'Flow templates:\n' + lines.join('\n'));
        }
      } catch (e) { addMsg('error', e.message); }
    } else if (sub === 'deploy') {
      const templateId = parts[2];
      const scope = parts[3] || 'user';
      if (!templateId) { addMsg('system', 'Usage: /flow deploy <template_id> [user|conversation]'); return true; }
      try {
        const resp = await fetch(API, {
          method: 'POST', headers: getAuthHeaders(),
          body: JSON.stringify({ action: 'deploy_flow', template_id: templateId, scope, conversation_id: conversationId || '' }),
        });
        const data = await resp.json();
        if (data.error) { addMsg('error', data.error); }
        else { addMsg('system', 'Deployed: ' + (data.instance_id || '?') + ' (' + scope + ')'); }
      } catch (e) { addMsg('error', e.message); }
    } else if (sub === 'start') {
      const iid = parts[2];
      if (!iid) { addMsg('system', 'Usage: /flow start <instance_id> [key=val ...]'); return true; }
      const overrides = {};
      for (let i = 3; i < parts.length; i++) {
        if (parts[i].includes('=')) {
          const [k, ...v] = parts[i].split('=');
          overrides[k] = v.join('=');
        }
      }
      try {
        if (Object.keys(overrides).length) {
          await fetch(API, {
            method: 'POST', headers: getAuthHeaders(),
            body: JSON.stringify({ action: 'update_flow_params', instance_id: iid, parameters: overrides }),
          });
        }
        const resp = await fetch(API, {
          method: 'POST', headers: getAuthHeaders(),
          body: JSON.stringify({ action: 'start_flow', instance_id: iid }),
        });
        const data = await resp.json();
        if (data.error) { addMsg('error', data.error); }
        else { addMsg('system', 'Flow \'' + iid + '\' started'); }
      } catch (e) { addMsg('error', e.message); }
    } else if (sub === 'stop') {
      const iid = parts[2];
      if (!iid) { addMsg('system', 'Usage: /flow stop <instance_id>'); return true; }
      try {
        const resp = await fetch(API, {
          method: 'POST', headers: getAuthHeaders(),
          body: JSON.stringify({ action: 'stop_flow', instance_id: iid }),
        });
        const data = await resp.json();
        if (data.error) { addMsg('error', data.error); }
        else { addMsg('system', 'Flow \'' + iid + '\' stopped'); }
      } catch (e) { addMsg('error', e.message); }
    } else if (sub === 'params') {
      const iid = parts[2];
      if (!iid) { addMsg('system', 'Usage: /flow params <instance_id>'); return true; }
      try {
        const resp = await fetch(API, {
          method: 'POST', headers: getAuthHeaders(),
          body: JSON.stringify({ action: 'get_flow_instance', instance_id: iid }),
        });
        const data = await resp.json();
        if (data.error) { addMsg('error', data.error); }
        else {
          const params = { ...(data.template_parameters || {}), ...(data.parameters || {}) };
          const lines = Object.entries(params).map(function(entry) { return '  ' + entry[0] + ' = ' + entry[1]; });
          addMsg('system', 'Flow ' + (data.flow_name || iid) + ' [' + (data.status || '?') + ']:\n' + lines.join('\n'));
        }
      } catch (e) { addMsg('error', e.message); }
    } else if (sub === 'undeploy') {
      const iid = parts[2];
      if (!iid) { addMsg('system', 'Usage: /flow undeploy <instance_id>'); return true; }
      try {
        const resp = await fetch(API, {
          method: 'POST', headers: getAuthHeaders(),
          body: JSON.stringify({ action: 'undeploy_flow', instance_id: iid }),
        });
        const data = await resp.json();
        if (data.error) { addMsg('error', data.error); }
        else { addMsg('system', 'Flow \'' + iid + '\' undeployed'); }
      } catch (e) { addMsg('error', e.message); }
    } else if (sub === 'promote') {
      const iid = parts[2];
      if (!iid) { addMsg('system', 'Usage: /flow promote <instance_id>'); return true; }
      try {
        const resp = await fetch(API, {
          method: 'POST', headers: getAuthHeaders(),
          body: JSON.stringify({ action: 'promote_flow', instance_id: iid, target_scope: 'user' }),
        });
        const data = await resp.json();
        if (data.error) { addMsg('error', data.error); }
        else { addMsg('system', 'Flow \'' + iid + '\' promoted to user scope'); }
      } catch (e) { addMsg('error', e.message); }
    } else {
      addMsg('system', 'Usage: /flow list | templates | deploy | start | stop | params | undeploy | promote');
    }
    return true;
  }

  if (cmd === '/prompt') {
    const sub = (parts[1] || 'list').toLowerCase();
    if (sub === 'list') {
      try {
        const resp = await fetch(API, {
          method: 'POST', headers: getAuthHeaders(),
          body: JSON.stringify({ action: 'list_prompts', conversation_id: conversationId || '' }),
        });
        const data = await resp.json();
        const prompts = data.prompts || [];
        if (!prompts.length) { addMsg('system', 'No prompts.'); }
        else {
          const lines = prompts.map(function(p) { return '\u2022 ' + p.name + ': ' + (p.description || p.content || '').slice(0, 60); });
          addMsg('system', 'Prompts:\n' + lines.join('\n'));
        }
      } catch (e) { addMsg('error', e.message); }
    } else if (sub === 'use') {
      const name = parts[2] || '';
      if (!name) { addMsg('system', 'Usage: /prompt use <name>'); return true; }
      try {
        const resp = await fetch(API, {
          method: 'POST', headers: getAuthHeaders(),
          body: JSON.stringify({ action: 'get_prompt', conversation_id: conversationId || '', name }),
        });
        const data = await resp.json();
        if (data.content) { addMsg('system', 'Prompt \'' + name + '\':\n' + data.content); }
        else { addMsg('error', 'Prompt \'' + name + '\' not found'); }
      } catch (e) { addMsg('error', e.message); }
    } else {
      addMsg('system', 'Usage: /prompt list | use <name>');
    }
    return true;
  }

  if (cmd === '/run') {
    const command = text.slice(cmd.length).trim();
    if (!command) { addMsg('system', 'Usage: /run <command>'); return true; }
    try {
      const resp = await fetch(API, {
        method: 'POST', headers: getAuthHeaders(),
        body: JSON.stringify({ action: 'fs_exec', service: '', command, timeout: 30 }),
      });
      const data = await resp.json();
      if (data.error) { addMsg('error', data.error); }
      else {
        const out = (data.stdout || '') + (data.stderr ? '\n[stderr] ' + data.stderr : '');
        addMsg('system', '$ ' + command + ' (exit ' + (data.returncode || 0) + ')\n' + out);
      }
    } catch (e) { addMsg('error', 'Exec failed: ' + e.message); }
    return true;
  }

  if (cmd === '/diff') {
    const ref = parts.slice(1).join(' ') || '.';
    try {
      const resp = await fetch(API, {
        method: 'POST', headers: getAuthHeaders(),
        body: JSON.stringify({ action: 'fs_exec', service: '', command: 'git diff ' + ref, timeout: 15 }),
      });
      const data = await resp.json();
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
    } catch (e) { addMsg('error', 'Diff failed: ' + e.message); }
    return true;
  }

  if (cmd === '/copy') {
    const msgs = document.querySelectorAll('.msg.assistant');
    if (!msgs.length) { addMsg('system', 'No responses to copy.'); return true; }
    const n = parseInt(parts[1]) || 1;
    const target = msgs[msgs.length - n];
    if (!target) { addMsg('system', 'Only ' + msgs.length + ' responses available.'); return true; }
    const text_to_copy = target.textContent || '';
    try {
      await navigator.clipboard.writeText(text_to_copy);
      addMsg('system', 'Copied ' + text_to_copy.length + ' chars to clipboard.');
    } catch (e) { addMsg('error', 'Copy failed: ' + e.message); }
    return true;
  }

  if (cmd === '/paste') {
    try {
      const items = await navigator.clipboard.read();
      for (const item of items) {
        if (item.types.includes('image/png')) {
          const blob = await item.getType('image/png');
          const reader = new FileReader();
          reader.onload = function() {
            const b64 = reader.result.split(',')[1];
            pendingFiles.push({ filename: 'clipboard.png', mime_type: 'image/png', data: b64 });
            addMsg('system', 'Image pasted from clipboard (' + pendingFiles.length + ' file(s) queued).');
          };
          reader.readAsDataURL(blob);
          return true;
        }
      }
      // No image — try text
      const text_content = await navigator.clipboard.readText();
      if (text_content) {
        document.getElementById('chatInput').value += text_content;
        addMsg('system', 'Text pasted from clipboard.');
      }
    } catch (e) { addMsg('error', 'Paste failed: ' + e.message); }
    return true;
  }

  if (cmd === '/upload') {
    const fileInput = document.getElementById('fileInput');
    if (fileInput) { fileInput.click(); }
    else { addMsg('system', 'File upload not available. Drag & drop files into the chat.'); }
    return true;
  }

  if (cmd === '/plan') {
    const arg = text.slice(cmd.length).trim();
    // No args or "list" — open plans panel
    if (!arg || arg === 'list') {
      const panel = document.getElementById('plansPanel');
      if (panel.style.display === 'none') {
        panel.style.display = 'block';
      }
      await loadPlans();
      if (arg === 'list') {
        // Also show plans inline in chat
        try {
          const resp = await fetch(API, {
            method: 'POST', headers: getAuthHeaders(),
            body: JSON.stringify({ action: 'get_plans', conversation_id: conversationId }),
          });
          const data = await resp.json();
          let planArr = Array.isArray(data.plans) ? data.plans : Object.values(data.plans || {});
          if (!planArr.length) { addMsg('system', 'No active plans.'); return true; }
          let lines = ['**Plans:**'];
          for (const p of planArr) {
            if (!p || !p.title) continue;
            const steps = p.steps || [];
            const done = steps.filter(s => s.status === 'done').length;
            const icon = {'pending_approval': '\u23F3', 'approved': '\u2705', 'in_progress': '\u25B6', 'completed': '\u2714', 'cancelled': '\u274C'}[p.status] || '\u2753';
            lines.push('  ' + icon + ' **' + p.title + '** (`' + (p.id || '?') + '`) \u2014 ' + p.status + ' \u2014 ' + done + '/' + steps.length + ' done');
          }
          addMsg('system', lines.join('\n'));
        } catch (e) { addMsg('error', 'Failed to list plans: ' + e.message); }
      }
      return true;
    }
    // Subcommands: approve, cancel, delete
    const parts = arg.split(/\s+/);
    const subcmd = parts[0].toLowerCase();
    if (['approve', 'cancel', 'delete'].includes(subcmd)) {
      const planId = parts[1];
      if (!planId) { addMsg('system', 'Usage: /plan ' + subcmd + ' <plan_id>'); return true; }
      const actionMap = { 'approve': 'approve_plan', 'cancel': 'cancel_plan', 'delete': 'delete_plan' };
      await planAction(actionMap[subcmd], planId);
      return true;
    }
    // Otherwise treat as plan creation request to agent
    const planMsg = '[Create a structured plan using the create_plan tool. Analyze the request, identify steps, then call create_plan.]\n\n' + arg;
    addMsg('user', '/plan ' + arg);
    showTyping();
    try {
      const body = { message: planMsg };
      if (conversationId) body.conversation_id = conversationId;
      const resp = await fetch(API, { method: 'POST', headers: getAuthHeaders(), body: JSON.stringify(body) });
      const data = await resp.json();
      if (data.conversation_id && !conversationId) { conversationId = data.conversation_id; connectSSE(conversationId); }
    } catch (e) { hideTyping(); addMsg('error', e.message); }
    return true;
  }

  if (cmd === '/watch') {
    addMsg('system', '/watch is not available in the web UI. Use the CLI for file watching.');
    return true;
  }

  if (cmd === '/clear-files' || cmd === '/detach') {
    pendingFiles = [];
    addMsg('system', 'Pending attachments cleared.');
    return true;
  }

  if (cmd === '/clear') {
    document.getElementById('messages').innerHTML = '';
    return true;
  }

  if (cmd === '/clear-store') {
    if (!conversationId) { addMsg('system', 'No active conversation'); return true; }
    const csArg = (parts[1] || '').trim();
    const csPayload = {action: 'clear_store', conversation_id: conversationId};
    if (csArg && csArg.toUpperCase() === 'ALL') {
      csPayload.scope = 'all_agents';
    } else if (csArg) {
      csPayload.agent_name = csArg;
    }
    try {
      const r = await fetch(API, {
        method: 'POST', headers: getAuthHeaders(),
        body: JSON.stringify(csPayload),
      }).then(res => res.json());
      if (r && r.deleted !== undefined) {
        addMsg('system', 'FileStore: deleted ' + r.deleted + ' file(s)' + (r.scope ? ' (' + r.scope + ')' : ''));
      } else if (r && r.error) {
        addMsg('error', r.error);
      }
    } catch (e) {
      addMsg('error', 'clear-store failed: ' + e.message);
    }
    return true;
  }

  if (cmd === '/loop') {
    if (!conversationId) { addMsg('system', 'No active conversation'); return true; }
    const loopArg = parts[1] || '';
    if (loopArg === 'list') {
      try {
        const r = await fetch(API, {
          method: 'POST', headers: getAuthHeaders(),
          body: JSON.stringify({action: 'loop_list', conversation_id: conversationId}),
        }).then(res => res.json());
        const loops = r.loops || [];
        if (loops.length === 0) { addMsg('system', 'No active loops'); }
        else {
          const lines = loops.map(l => l.key + ' — every ' + l.interval_seconds + 's: ' + (l.prompt || '?'));
          addMsg('system', 'Active loops:\n' + lines.join('\n'));
        }
      } catch(e) { addMsg('error', e.message); }
      return true;
    }
    if (loopArg === 'stop') {
      const loopKey = parts[2] || '';
      if (!loopKey) { addMsg('system', 'Usage: /loop stop <key>'); return true; }
      try {
        const r = await fetch(API, {
          method: 'POST', headers: getAuthHeaders(),
          body: JSON.stringify({action: 'loop_stop', key: loopKey}),
        }).then(res => res.json());
        addMsg('system', r.stopped ? 'Loop stopped: ' + loopKey : 'Loop not found: ' + loopKey);
      } catch(e) { addMsg('error', e.message); }
      return true;
    }
    // Parse interval: supports "5m", "30s", "2h" AND autoconv "2-3/h", "1/30s", "6/1m"
    const _units = {s:1, m:60, h:3600, d:86400};
    let intervalSec = 0;
    // Try autoconv format: N[-M]/[D]U
    const acMatch = loopArg.match(/^(\d+)(?:-(\d+))?\/(\d*)([smhd])$/);
    if (acMatch) {
      const countMin = parseInt(acMatch[1]);
      const durationNum = parseInt(acMatch[3] || '1');
      const period = durationNum * _units[acMatch[4]];
      intervalSec = Math.floor(period / countMin);
    } else {
      // Try simple format: 5m, 30s, 2h
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
    try {
      const r = await fetch(API, {
        method: 'POST', headers: getAuthHeaders(),
        body: JSON.stringify({action: 'loop_start', conversation_id: conversationId,
                              interval_seconds: intervalSec, prompt: loopPrompt}),
      }).then(res => res.json());
      if (r.started) {
        addMsg('system', 'Loop started: every ' + intervalSec + 's — ' + loopPrompt + '\nKey: ' + r.key);
      } else { addMsg('error', r.error || 'Failed to start loop'); }
    } catch(e) { addMsg('error', e.message); }
    return true;
  }

  if (cmd === '/batch') {
    const batchText = text.replace(/^\/batch\s*/, '').trim();
    if (!batchText) { addMsg('system', 'Usage: /batch <instruction> [--files <glob>]'); return true; }
    // Parse --files flag
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

  if (cmd === '/debug') {
    const debugDesc = parts.slice(1).join(' ').trim();
    const debugMsg = '/call use_skill(skill_name="debug"' + (debugDesc ? ', context="' + debugDesc.replace(/"/g, '\\"') + '"' : '') + ')';
    sendMessage(debugMsg);
    return true;
  }

  if (cmd === '/login') {
    window.location.href = '/login';
    return true;
  }

  // Unknown slash command — try server-side command parser
  return await tryServerCommand(text);
}

/**
 * Fallback: send raw /command text to the server-side unified parser.
 * New commands added server-side auto-work without client changes.
 */
async function tryServerCommand(text) {
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({
        action: 'command', text,
        conversation_id: conversationId || '',
        agent_name: selectedAgent || '',
      }),
    });
    const data = await resp.json();
    if (data.client_only) {
      addMsg('system', 'Unknown command: ' + text.split(/\s+/)[0] + '. Type /help for available commands.');
      return true;
    }
    if (data.help) { addMsg('system', data.help); return true; }
    if (data.message) { addMsg('system', data.message); }
    if (data.error) { addMsg('system', '⚠ ' + data.error); }
    // Handle special response types
    if (data.conversation_id && data.ok && data.source) {
      // Fork — switch to new conversation
      addMsg('system', 'Switching to forked conversation...');
      if (typeof switchConversation === 'function') {
        switchConversation(data.conversation_id);
      }
    }
    if (data.checkpoints && !data.error) {
      // Rewind checkpoint list — already rendered via data.message
    }
    return true;
  } catch (e) {
    addMsg('system', 'Command failed: ' + e.message);
    return true;
  }
}

async function cmdSchedulesList() {
  if (!conversationId) { addMsg('system', 'No active conversation'); return; }
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'list_schedules', conversation_id: conversationId }),
    });
    const data = await resp.json();
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
  } catch (e) { addMsg('error', 'Failed to list schedules: ' + e.message); }
}

async function cmdSchedulesDel() {
  if (!conversationId) { addMsg('system', 'No active conversation'); return; }
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'delete_schedule', conversation_id: conversationId }),
    });
    const data = await resp.json();
    addMsg('system', data.cancelled ? 'Schedule cancelled.' : 'No schedule to cancel.');
  } catch (e) { addMsg('error', 'Failed to delete schedule: ' + e.message); }
}

async function cmdSchedulesAdd(dateStr, reason) {
  if (!conversationId) { addMsg('system', 'No active conversation'); return; }
  if (!/^\d{14}$/.test(dateStr)) {
    addMsg('system', 'Invalid date format. Use YYYYMMDDHHmmss (e.g. 20260312140000)');
    return;
  }
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({
        action: 'add_schedule', conversation_id: conversationId,
        at: dateStr, reason: reason || 'manual schedule',
      }),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); return; }
    const dt = new Date(data.at * 1000).toLocaleString();
    addMsg('system', 'Schedule added: ' + dt);
  } catch (e) { addMsg('error', 'Failed to add schedule: ' + e.message); }
}

async function cmdUsage() {
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'get_usage' }),
    });
    const data = await resp.json();
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
  } catch (e) { addMsg('error', 'Failed to get usage: ' + e.message); }
}

async function cmdLinkAccount(provider, providerId, botToken) {
  try {
    const payload = { action: 'link_account', provider, provider_id: providerId };
    if (botToken) { payload.bot_token = botToken; }
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify(payload),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); }
    else {
      let msg = provider + ' account ' + providerId + ' linked successfully!';
      if (data.bot_username) { msg += ' Bot: @' + data.bot_username; }
      if (data.bot_warning) { msg += '\n\u26a0\ufe0f ' + data.bot_warning; }
      addMsg('system', msg);
    }
  } catch (e) { addMsg('error', 'Failed to link: ' + e.message); }
}

async function cmdUnlinkAccount(provider) {
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'unlink_account', provider }),
    });
    const data = await resp.json();
    if (data.unlinked) { addMsg('system', provider + ' account unlinked.'); }
    else { addMsg('system', 'No ' + provider + ' link found.'); }
  } catch (e) { addMsg('error', 'Failed to unlink: ' + e.message); }
}

async function cmdLinkStatus() {
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'list_linked_accounts' }),
    });
    const data = await resp.json();
    const links = data.links || {};
    if (Object.keys(links).length === 0) {
      addMsg('system', 'No linked accounts. Use /link <provider> <id> to link.');
    } else {
      const lines = Object.entries(links).map(function(entry) { return '\u2022 ' + entry[0] + ': ' + entry[1]; });
      addMsg('system', 'Linked accounts:\n' + lines.join('\n'));
    }
  } catch (e) { addMsg('error', 'Failed to get links: ' + e.message); }
}

async function cmdAgentList() {
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

async function cmdAgentCreate() {
  showResourceCreator('agent');
}

function showResourceCreator(rtype) {
  let overlay = document.getElementById('resourceEditorOverlay');
  if (overlay) overlay.remove();
  overlay = document.createElement('div');
  overlay.id = 'resourceEditorOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;z-index:9999;';
  const title = {agent:'Create Agent',skill:'Create Skill',mcp:'Create MCP Server',task_def:'Create Task',prompt:'Create Prompt'}[rtype] || 'Create ' + rtype;
  const panel = document.createElement('div');
  panel.style.cssText = 'background:#16213e;border-radius:8px;padding:20px;width:500px;max-height:80vh;overflow-y:auto;border:1px solid #333;';
  panel.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
    <h3 style="margin:0;color:#e0e0e0;font-size:14px;">${escapeHtml(title)}</h3>
    <button onclick="document.getElementById('resourceEditorOverlay').remove()" style="background:none;border:none;color:#888;cursor:pointer;font-size:18px;">&times;</button>
  </div>` + _buildResourceForm(rtype, {}, true)
    + `<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">
    <button onclick="document.getElementById('resourceEditorOverlay').remove()" style="background:#333;color:#ccc;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">Cancel</button>
    <button onclick="_submitResourceCreate('${rtype}')" style="background:#6c5ce7;color:white;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">Create</button>
  </div>`;
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
  document.getElementById('res-name')?.focus();
}

async function _submitResourceCreate(rtype) {
  const name = (document.getElementById('res-name')?.value || '').trim();
  const scope = document.getElementById('res-scope')?.value || 'user';
  if (!name) { addMsg('error', 'Name is required'); return; }
  if (scope === 'global') { addMsg('error', 'Cannot create global resources from chat. Use the admin GUI.'); return; }

  const fields = _RESOURCE_FIELDS[rtype] || [];
  const data = {};
  for (const [key, type] of fields) {
    const el = document.getElementById('res-' + key);
    if (el) data[key] = type === 'number' ? parseInt(el.value) || 0 : el.value;
  }

  // Route to the correct action
  let action = 'create_resource';
  let body = { action, resource_type: rtype, name, data, scope, conversation_id: conversationId };
  if (rtype === 'agent') {
    action = 'create_agent';
    body = { action, name, prompt: data.prompt || '', conversation_id: conversationId,
             model: data.model, description: data.description, llm_service: data.llm_service };
  } else if (rtype === 'task_def') {
    action = 'create_task_def';
    body = { action, name, prompt: data.prompt || '', conversation_id: conversationId,
             criteria: data.criteria, default_interval: data.default_interval, description: data.description };
  }
  body.action = action;

  try {
    const resp = await fetch(API, { method: 'POST', headers: getAuthHeaders(), body: JSON.stringify(body) });
    const result = await resp.json();
    if (result.error) { addMsg('error', result.error); }
    else { addMsg('system', `${rtype} '${name}' created.`); document.getElementById('resourceEditorOverlay').remove(); loadResources(); }
  } catch (e) { addMsg('error', e.message); }
}

function updateActiveAgentBadge() {
  const badge = document.getElementById('activeAgentBadge');
  const agent = selectedAgent || '';
  // Color from agent name hash (same algo as source badges)
  let h = 0;
  for (let i = 0; i < agent.length; i++) h = ((h << 5) - h + agent.charCodeAt(i)) | 0;
  const hue = Math.abs(h) % 360;
  badge.style.background = 'hsl(' + hue + ',60%,25%)';
  badge.style.color = 'hsl(' + hue + ',80%,80%)';
  badge.textContent = '\u2192 ' + displayAgentName(agent);
  badge.title = !agent ? 'Default agent' : 'Active: ' + agent + ' — click to switch back';
  badge.style.display = '';
}

async function cmdAgentSelect(name) {
  const isDefault = !name;
  if (!conversationId) {
    // No conversation yet — store pending selection, will be applied on first message
    pendingAgent = isDefault ? null : name;
    selectedAgent = isDefault ? '' : name;
    updateActiveAgentBadge();
    addMsg('system', isDefault ? 'Switched to default agent (assistant).' : `Agent '${name}' selected (will activate on first message).`);
    return;
  }
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({
        action: 'select_agent', conversation_id: conversationId,
        name: isDefault ? '' : name,
      }),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); return; }
    selectedAgent = isDefault ? '' : name;
    updateActiveAgentBadge();
    addMsg('system', isDefault ? 'Switched to default agent (assistant).' : `Agent '${name}' selected. Messages now go to ${name}.`);
    loadResources();
  } catch (e) { addMsg('error', 'Failed to select agent: ' + e.message); }
}

async function cmdAgentDelete(name) {
  if (!conversationId) { addMsg('system', 'No active conversation'); return; }
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({
        action: 'delete_agent', conversation_id: conversationId,
        name: name,
      }),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); return; }
    addMsg('system', data.deleted ? `Agent '${name}' deleted.` : `Agent '${name}' not found.`);
    loadResources();
  } catch (e) { addMsg('error', 'Failed to delete agent: ' + e.message); }
}

async function cmdAgentSetname(realName, nickname) {
  if (!conversationId) { addMsg('system', 'No active conversation'); return; }
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({
        action: 'set_agent_nickname', conversation_id: conversationId,
        agent_name: realName, nickname: nickname,
      }),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); return; }
    nicknameMap[realName] = nickname;
    addMsg('system', t('agentRenamed', { real: realName, nick: nickname }));
  } catch (e) { addMsg('error', 'Failed: ' + e.message); }
}

function cmdAgentMsg(agentName, text) {
  // Send a message to a specific agent without changing the active agent
  // Capture and include any pending attachments
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
  showTyping();
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
    if (data.error) { addMsg('error', data.error); hideTyping(); sending = false; return; }
    if (data.conversation_id && !conversationId) {
      conversationId = data.conversation_id;
      connectSSE(conversationId);
    }
    if (data.message_count) serverMsgCount = data.message_count;
  }).catch(e => {
    addMsg('error', 'Failed to send to agent: ' + e.message);
    hideTyping();
    sending = false;
  });
}

function cmdAgentMsgAll(text) {
  // Broadcast a message to ALL agents in parallel
  if (!conversationId) {
    // Need a conversation first — send a dummy to create one
    addMsg('system', 'Start a conversation first before broadcasting.');
    return;
  }
  addMsg('user', text, { source: { type: 'user', name: '', target_agent: 'ALL' } });
  showTyping();
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
    if (data.error) { addMsg('error', data.error); hideTyping(); sending = false; }
  }).catch(e => {
    addMsg('error', 'Broadcast failed: ' + e.message);
    hideTyping();
    sending = false;
  });
}

function cmdAgentInterrupt(target) {
  if (!conversationId) { addMsg('system', 'No active conversation.'); return; }
  const isAll = target.toUpperCase() === 'ALL';
  addMsg('system', isAll ? 'Interrupting all agents...' : ('Interrupting ' + (target || 'agent') + '...'));
  if (isAll) {
    // Interrupt default + all agents
    fetch(API, { method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'interrupt', conversation_id: conversationId, agent_name: '' }),
    }).catch(e => addMsg('error', 'Interrupt failed: ' + e.message));
    // Also interrupt each known agent
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
  // Response comes via SSE btw_token/btw_done events
}

async function cmdMemoryList(agentFilter, searchQuery) {
  try {
    const body = { action: 'list_memories' };
    if (agentFilter !== undefined && agentFilter !== null) body.agent_name = agentFilter;
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    let mems = data.memories || [];
    // Client-side text search if query provided
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
  } catch (e) { addMsg('error', 'Failed to list memories: ' + e.message); }
}

async function cmdMemoryDel(memId) {
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'delete_memory', memory_id: memId }),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); return; }
    addMsg('system', data.deleted ? `Memory ${memId} deleted.` : `Memory ${memId} not found.`);
  } catch (e) { addMsg('error', 'Failed to delete memory: ' + e.message); }
}

async function cmdToolsList() {
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'list_tools' }),
    });
    const data = await resp.json();
    const tools = data.tools || [];
    if (tools.length === 0) {
      addMsg('system', 'No dynamic tools installed. Use /install to add one.');
    } else {
      const lines = tools.map(t =>
        `\u2022 **${t.tool_name}** \u2014 ${t.description} (by ${t.owner})`
      );
      addMsg('system', 'Dynamic tools:\n' + lines.join('\n'));
    }
  } catch (e) { addMsg('error', 'Failed to list tools: ' + e.message); }
}

async function cmdUninstallTool(toolName) {
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'uninstall_tool', tool_name: toolName }),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); return; }
    addMsg('system', data.uninstalled ? `Tool '${toolName}' uninstalled.` : `Tool '${toolName}' not found.`);
  } catch (e) { addMsg('error', 'Failed to uninstall tool: ' + e.message); }
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
    // status=accepted → compaction runs in background, SSE events will report progress
    // contextOpInProgress stays true until compact_progress done event arrives
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

function cmdRebuildClean() {
  if (!conversationId) { addMsg('system', t('noConv')); return; }
  contextOpInProgress = true;
  showContextOp('Rebuilding');
  fetch(API, {
    method: 'POST', headers: getAuthHeaders(),
    body: JSON.stringify({ action: 'rebuild_clean', conversation_id: conversationId }),
  }).then(r => r.json()).then(data => {
    if (data.error) { addMsg('error', 'Rebuild clean failed: ' + data.error); return; }
    addMsg('system', t('rebuiltClean', {messages: data.messages, tokens: data.token_estimate}));
  }).catch(e => addMsg('error', 'Rebuild clean failed: ' + e.message))
    .finally(() => { hideContextOp(); contextOpInProgress = false; });
}
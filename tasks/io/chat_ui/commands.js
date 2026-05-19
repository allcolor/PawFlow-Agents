// ── Slash commands ───────────────────────────────────────────────
const HELP_DATA = {
  '/help': {
    usage: '/help [command]',
    short: t('commandShort.1'),
    detail: 'Without arguments, lists all commands. With a command name, shows detailed documentation.\nExample: /help agent',
  },
  '/msg': {
    usage: '/msg [@agent|@t_taskid] <message>',
    short: t('commandShort.2'),
    detail: 'Send a message to a specific agent or task without changing the active agent.\n\nExamples:\n  /msg @grok Explain this code\n  /msg @ALL What do you think?\n  /msg @t_8953b308 Check the latest post\n  /msg @"Agent With Spaces" Hello',
  },
  '/btw': {
    usage: '/btw [@agent|ALL] <question>',
    short: t('commandShort.3'),
    detail: 'Ask a quick question to an agent without interrupting its current work.\n\nExamples:\n  /btw @claude What is the time complexity?\n  /btw @ALL Any thoughts on this?',
  },
  '/call': {
    usage: '/call tool_name(key=value, ...) or /call tool_name {"key": "value"}',
    short: t('commandShort.4'),
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
    usage: '/vidservice [list | select <name> [@agent] | clear [@agent]]',
    short: t('commandShort.5'),
    detail: 'Choose which video generation service to use in this conversation.\n\n'
      + '  /vidservice list                  \u2014 Show available video services\n'
      + '  /vidservice select <name>          \u2014 Set default for all agents\n'
      + '  /vidservice select <name> @<agent> \u2014 Set for a specific agent\n'
      + '  /vidservice clear                  \u2014 Remove all preferences (auto-select)\n'
      + '  /vidservice clear @<agent>         \u2014 Remove preference for one agent\n',
  },
  '/task': {
    usage: '/task create | assign | list | edit | delete | pause | resume | cancel',
    short: t('commandShort.6'),
    detail: 'Task library + autonomous task assignment. Tasks can be reusable definitions or inline.\n\n'
      + '**Library (reusable definitions):**\n'
      + '  /task create <name> "<prompt>" [--criteria "..."] [--interval XX] [--interactive]\n'
      + '  /task delete <name>           \u2014 Delete a task definition\n'
      + '  /task list                    \u2014 Show library + running tasks\n\n'
      + '**Assignment (from library or inline):**\n'
      + '  /task assign @<agent> <taskname>              \u2014 From library\n'
      + '  /task assign @<agent> <taskname> --var nbr_images=20 --var style=cyberpunk\n'
      + '  /task assign @<agent> <taskname> --interval XX \u2014 Override interval\n'
      + '  /task assign @<agent> "<inline task>" [--criteria "..."] [--interval XX] [--verifier @<agent>] [--interactive]\n\n'
      + 'Variables: use ${name} in task definitions, resolved at assign time.\n'
      + 'Use \\${...} to keep literal ${...}. Cascade: secrets → params → env.\n\n'
      + '**Limits (on assign or edit):**\n'
      + '  --budget $5          \u2014 Cancel if cost exceeds\n'
      + '  --turn-time 5m       \u2014 Interrupt if a single turn takes too long\n'
      + '  --total-time 1h      \u2014 Cancel if total elapsed time exceeds\n'
      + '  --max-reschedules 20 \u2014 Cancel after N reschedules\n'
      + '  --interactive       \u2014 Scheduled wakes are system-marked, not user input\n\n'
      + '**Control:**\n'
      + '  /task edit <task_id> [--budget $X] [--turn-time Xm] [--total-time Xh] [--max-reschedules N]\n'
      + '  /task pause <task_id|agent>   \u2014 Pause a task or all tasks of an agent\n'
      + '  /task resume <task_id|agent>  \u2014 Resume a paused task or all of an agent\n'
      + '  /task cancel <task_id|agent>  \u2014 Cancel a task or all of an agent\n\n'
      + 'Task IDs look like t_xxxxxxxx. Use /task list to see them.\n'
      + 'Tasks survive server restarts and reschedule automatically.\n\n'
      + 'Example: /task assign @grok "Scrape the top 100 HN posts" --verifier claude --interval 120 --criteria "all 100 posts summarized"',
  },
  '/goal': {
    usage: '/goal [@agent] "<objective>" [task options]',
    short: 'Create and assign a conversation goal task',
    detail: 'Creates a conversation-scoped task definition with a generated name and assigns it immediately. If @agent is omitted, the selected conversation agent is used. The objective is copied to criteria unless --criteria is provided.\n\n'
      + 'Options: --criteria, --interval, --verifier, --budget, --turn-time, --total-time, --max-reschedules, --max, --context, --var, --auto-allow, --interactive.\n\n'
      + 'Example: /goal @grok "Migrate X until tests pass and final audit is done" --interval 120 --verifier @assistant',
  },
  '/imgservice': {
    usage: '/imgservice [list | select <name> [@agent] | clear [@agent]]',
    short: t('commandShort.7'),
    detail: 'Choose which image generation service to use in this conversation.\n\n'
      + '  /imgservice list                  \u2014 Show available image services\n'
      + '  /imgservice select <name>          \u2014 Set default for all agents\n'
      + '  /imgservice select <name> @<agent> \u2014 Set for a specific agent\n'
      + '  /imgservice clear                  \u2014 Remove all preferences (auto-select)\n'
      + '  /imgservice clear @<agent>         \u2014 Remove preference for one agent\n',
  },
  '/agent': {
    usage: '/agent list | create | select | delete | msg | btw | resume | setname',
    short: t('commandShort.8'),
    detail: 'Create, list, select, message, or control AI agents.\n\n'
      + '  /agent list                       — List all agents (user + global)\n'
      + '  /agent create                     — Create a new agent (interactive)\n'
      + '  /agent select @<name>              — Activate an agent (use real name or nickname)\n'
      + '  /agent select assistant             — Switch back to the default assistant\n'
      + '  /agent delete @<name>              — Delete an agent by name\n'
      + '  /agent msg @<name> <text>          — Send a message to a specific agent\n'
      + '  /agent msg @ALL <text>             — Broadcast to all agents in parallel\n'
      + '  /agent btw @<name|ALL> <text>      — Side-channel question (no interruption)\n'
      + '  /agent resume @<name>              — Tell agent to continue from where it stopped\n'
      + '  /agent setname @<real> [nickname]  — Set or reset display name (omit to reset)\n\n'
      + 'Agents define a system prompt, tools, model, and LLM service. '
      + 'The active agent shapes the AI\'s behavior for the conversation.',
  },
  '/skill': {
    usage: '/skill list | add @name <prompt> | del @name | assign @agent @skill | unassign @agent @skill | assigned @agent | run [@agent] <name> [args...] | //<name> [@agent] [args...]',
    short: t('commandShort.9'),
    detail: 'Create, list, assign, delete, or run skills.\n\n'
      + '  /skill list                     — List all skills and agent assignments\n'
      + '  /skill add @name <prompt>       — Create a skill with given prompt\n'
      + '  /skill del @name                — Delete a skill\n'
      + '  /skill assign @agent @skill     — Assign a skill to an agent\n'
      + '  /skill unassign @agent @skill   — Remove a skill from an agent\n'
      + '  /skill assigned @agent          — List skills assigned to an agent\n'
      + '  /skill run [@agent] <name> [args...] — Invoke a skill now\n'
      + '  //<name> [@agent] [args...]     — Shortcut for /skill run\n\n'
      + 'Skills are prompt resources injected only when assigned or run explicitly.',
  },
  '/add-skill': {
    usage: '/add-skill <name> <prompt>',
    short: t('commandShort.10'),
    detail: 'Same as /skill add <name> <prompt>.',
  },
  '/resources': {
    usage: '/resources',
    short: t('commandShort.11'),
    detail: 'Shows all defined resources grouped by type, with activation status for the current conversation.',
  },
  '/activate': {
    usage: '/activate <agent|mcp> <name>',
    short: t('commandShort.12'),
    detail: 'Activates an agent or MCP server.\n\n'
      + '  /activate agent researcher  — Activate the "researcher" agent\n'
      + '  /activate mcp my_server     — Activate an MCP server\n\n'
      + 'Skills are assigned with /skill assign @agent @skill.',
  },
  '/deactivate': {
    usage: '/deactivate <agent|mcp> <name>',
    short: t('commandShort.13'),
    detail: 'Deactivates an agent, skill, or MCP server for the current conversation.',
  },
  '/share': {
    usage: '/share <agent|skill|mcp> <name> <conversation_id>',
    short: t('commandShort.14'),
    detail: 'Copies a resource activation to another conversation by ID.',
  },
  '/claude-login-server': {
    usage: '/claude-login-server <service_name>',
    short: t('commandShort.15'),
    detail: 'Opens a browser in a server Docker container for Claude OAuth login.\n\n'
      + '  /claude-login-server claude_code_llm_service\n'
      + '  Shortcut: /cls',
  },
  '/cls': { alias: '/claude-login-server' },
  '/claude-login-relay': {
    usage: '/claude-login-relay <service_name> [relay_name]',
    short: t('commandShort.16'),
    detail: 'Runs claude auth login on the relay machine.\n\n'
      + '  /claude-login-relay claude_code_llm_service\n'
      + '  /claude-login-relay claude_code_llm_service my_relay\n'
      + '  Shortcut: /clr',
  },
  '/clr': { alias: '/claude-login-relay' },
  '/claude-login-credentials': {
    usage: '/claude-login-credentials <service_name> <credentials_json>',
    short: t('commandShort.17'),
    detail: 'Paste the content of ~/.claude/.credentials.json.\n\n'
      + '  /claude-login-credentials claude_code_llm_service {"claudeAiOauth":...}\n'
      + '  Shortcut: /clc',
  },
  '/clc': { alias: '/claude-login-credentials' },
  '/terminal': {
    usage: '/terminal [relay_name] | /terminal close',
    short: t('commandShort.18'),
    detail: 'Opens xterm.js in a new tab connected to a PTY on the relay.\n\n'
      + '  /terminal              \u2014 Open on first connected relay\n'
      + '  /terminal my_relay     \u2014 Open on a specific relay\n'
      + '  /terminal close        \u2014 Close the active terminal tab\n'
      + '  Shortcut: /term\n\n'
      + 'You can open multiple terminals (each gets its own tab).\n'
      + 'Close a tab by clicking \u00d7 on the tab or /terminal close.',
  },
  '/term': { alias: '/terminal' },
  '/code': {
    usage: '/code [relay_name] | /code close',
    short: t('commandShort.19'),
    detail: 'Opens code-server in a tab (one per relay).\n\n'
      + '  /code                  \u2014 Start on first connected relay\n'
      + '  /code my_relay         \u2014 Start on a specific relay\n'
      + '  /code close            \u2014 Close VS Code tab',
  },
  '/audio': {
    usage: '/audio [relay_name] | /audio stop',
    short: t('commandShort.20'),
    detail: 'Streams audio from the relay without opening the full desktop.\n\n'
      + '  /audio              \u2014 Start on first relay\n'
      + '  /audio my_relay     \u2014 Start on a specific relay\n'
      + '  /audio stop         \u2014 Close the audio tab',
  },
  '/desktop': {
    usage: '/desktop [relay_name] | /desktop local [relay] | /desktop docker [relay] | /desktop close',
    short: t('commandShort.21'),
    detail: 'Opens noVNC in a tab connected to a virtual desktop on the relay.\n\n'
      + '  /desktop              \u2014 Open on first relay (choose mode if local screen available)\n'
      + '  /desktop my_relay     \u2014 Open on a specific relay\n'
      + '  /desktop local        \u2014 Open user\'s local screen via VNC\n'
      + '  /desktop docker       \u2014 Open Docker virtual desktop\n'
      + '  /desktop close        \u2014 Close the active desktop tab',
  },
  '/port-forward': {
    usage: '/port-forward <add|remove|list|open> [relay_id] [port]',
    short: t('commandShort.22'),
    detail: 'Forward a relay\'s local port through PawFlow.\n\n'
      + '  /port-forward add [relay] [port]   \u2014 Add a forward rule\n'
      + '  /port-forward remove <relay> <port> \u2014 Remove a forward rule\n'
      + '  /port-forward list                  \u2014 List active forwards\n'
      + '  /port-forward open <relay> <port>   \u2014 Open in a browser tab\n'
      + '  Shortcut: /fwd',
  },
  '/fwd': { alias: '/port-forward' },
  '/relay': {
    usage: '/relay [status|list|link|unlink|default] [relay_id]',
    short: t('commandShort.23'),
    detail: 'View and manage which relays are linked to the current conversation.\n\n'
      + '  /relay              \u2014 Show linked relays (status)\n'
      + '  /relay list         \u2014 List all available relays\n'
      + '  /relay link <id>    \u2014 Link a relay to this conversation\n'
      + '  /relay unlink <id>  \u2014 Unlink a relay\n'
      + '  /relay default <id> \u2014 Set a relay as default for this conversation\n'
      + '  /relay local <id> true|false [@agent] \u2014 Set default execution mode for a relay (local=host, false=docker)',
  },
  '/service': {
    usage: '/service list | install <type> <name> [config] | uninstall <name> | enable <name> | disable <name>',
    short: t('commandShort.24'),
    detail: 'Install, list, enable/disable, or uninstall services.\n\n'
      + '  /service list                    — List installed services\n'
      + '  /service install <type> <name> [key=val,...] — Install a service\n'
      + '  /service uninstall <name>        — Remove a service\n'
      + '  /service enable <name>           — Enable a service\n'
      + '  /service disable <name>          — Disable a service',
  },
  '/schedules': {
    usage: '/schedules list | del | add <YYYYMMDDHHmmss> [reason]',
    short: t('commandShort.25'),
    detail: 'List, add, or delete scheduled recheck times.\n\n'
      + '  /schedules list           — List pending schedules\n'
      + '  /schedules add <datetime> — Add a recheck (format: YYYYMMDDHHmmss)\n'
      + '  /schedules del            — Delete all schedules',
  },
  '/llm': {
    usage: '/llm @<agent> <service|restore> | /llm rotate @<service>',
    short: t('commandShort.26'),
    detail: 'Override the LLM service for any agent in the current conversation.\n\n'
      + '  /llm @assistant grok_llm_service    \u2014 Switch assistant to grok\n'
      + '  /llm @grok qwen_llm_service         \u2014 Switch grok to local qwen\n'
      + '  /llm @assistant ${my_service}         \u2014 Use a variable reference\n'
      + '  /llm @grok restore                   \u2014 Restore grok\'s default service\n\n'
      + 'The override is per-conversation and persists across restarts.',
  },
  '/interrupt': {
    usage: '/interrupt [@agent|@agent::taskid|ALL]',
    short: t('commandShort.27'),
    detail: 'Asks the agent to wrap up and give its best answer now.\n\n'
      + '  /interrupt               — Interrupt active agent (or all)\n'
      + '  /interrupt @grok         — Interrupt only grok\n'
      + '  /interrupt @grok::t_abc  — Interrupt a specific task\n'
      + '  /interrupt @ALL          — Interrupt all agents',
  },
  '/stop': {
    usage: '/stop [@agent|@agent::taskid|ALL]',
    short: t('commandShort.28'),
    detail: 'Immediately kills the agent with no response.\n\n'
      + '  /stop                   — Force stop active agent (or all)\n'
      + '  /stop @grok             — Force stop only grok\n'
      + '  /stop @grok::t_abc      — Force stop a specific task\n'
      + '  /stop @ALL              — Force stop all agents',
  },
  '/restart_from': {
    usage: '/restart_from <index|msg_id>',
    short: t('commandShort.29'),
    detail: 'Truncates the current conversation transcript at an absolute index or message id. Shared context is rebuilt and agent contexts are deleted.\n\n'
      + '  /restart_from 0          — Empty transcript and contexts\n'
      + '  /restart_from 10         — Keep the first 10 transcript messages\n'
      + '  /restart_from abc123     — Keep messages through msg_id abc123\n\n'
      + 'Use the message action button to copy a msg_id or restart from that message.',
  },
  '/summary': {
    usage: '/summary [@agent|ALL] [tokens]',
    short: t('commandShort.30'),
    detail: 'Asks the LLM to summarize the context to approximately N tokens (default 500), then restarts from that summary.\n\n'
      + '  /summary               \u2014 Summarize shared context to ~500 tokens\n'
      + '  /summary 1000          \u2014 Summarize to ~1000 tokens\n'
      + '  /summary @grok         \u2014 Summarize grok\'s context\n'
      + '  /summary @ALL          \u2014 Summarize all agents\' contexts\n'
      + '  /summary @qwen 2000    \u2014 Summarize qwen\'s context to ~2000 tokens\n\n'
      + 'The summary replaces previous context for that agent. New messages build on top.',
  },
  '/resume': {
    usage: '/resume @<agent|ALL>',
    short: t('commandShort.31'),
    detail: 'Resumes an agent that was interrupted or stopped.\n\nExamples:\n  /resume @grok\n  /resume @ALL',
  },
  '/compact': {
    usage: '/compact [@agent|ALL]',
    short: t('commandShort.32'),
    detail: 'Summarizes older messages to reduce context size while preserving key information.\n\n'
      + '  /compact          \u2014 Compact current agent\'s context (or shared if none selected)\n'
      + '  /compact shared   \u2014 Compact the shared context\n'
      + '  /compact @grok    \u2014 Compact grok\'s context only\n'
      + '  /compact @ALL     \u2014 Compact all agents\' contexts',
  },
  '/git-prune': {
    usage: '/git-prune',
    short: 'Prune conversation Git history',
    detail: 'Runs the configured sliding-window retention for the current conversation Git snapshots and reclaims disk space with git gc.',
  },
  '/rebuild': {
    usage: '/rebuild [@agent|ALL]',
    short: t('commandShort.33'),
    detail: 'Reconstructs the LLM context from the complete conversation. If everything fits, restores fully; otherwise compacts.\n\n'
      + '  /rebuild          \u2014 Rebuild shared context\n'
      + '  /rebuild @grok    \u2014 Rebuild grok\'s context\n'
      + '  /rebuild @ALL     \u2014 Rebuild all agents',
  },
  '/context': {
    usage: '/context [@agent]',
    short: t('commandShort.34'),
    detail: 'Shows what the LLM actually sees: messages, token estimate, divergence status.\n\n'
      + '  /context          \u2014 View shared context\n'
      + '  /context @grok    \u2014 View grok\'s context\n\n'
      + 'The overlay includes an agent dropdown to switch between agent contexts.',
  },
  '/files': {
    usage: '/files',
    short: t('commandShort.35'),
    detail: 'Shows or hides the file browser panel for viewing and managing uploaded files.',
  },
  '/flows': {
    usage: '/flows',
    short: t('commandShort.36'),
    detail: 'Shows or hides the flows panel for monitoring active data flows.',
  },
  '/tasks': {
    usage: '/tasks',
    short: t('commandShort.37'),
    detail: 'Shows or hides the panel listing scheduled background tasks.',
  },
  '/tools': {
    usage: '/tools',
    short: t('commandShort.38'),
    detail: 'Shows all tools available to the AI agent in the current conversation, including builtins and custom tools.',
  },
  '/tool-metrics': {
    usage: '/tool-metrics',
    short: t('commandShort.39'),
    detail: 'Shows per-tool call counts, success/error totals, latency, and the latest server-side tool error. Alias: /toolmetrics.',
  },
  '/toolmetrics': {
    usage: '/toolmetrics',
    short: t('commandShort.39'),
    detail: 'Alias for /tool-metrics.',
  },
  '/usage': {
    usage: '/usage',
    short: t('commandShort.40'),
    detail: 'Displays token usage for the current conversation (prompt tokens, completion tokens, total).',
  },
  '/memory': {
    usage: '/memory [list [@agent] | add | edit | del | search | panel]',
    short: t('commandShort.41'),
    detail: 'View, add, edit and delete persistent agent memories.\n\n'
      + '  /memory                              \u2014 Open memory panel (visual editor)\n'
      + '  /memory list                         \u2014 List all memories\n'
      + '  /memory list @<agent>                \u2014 List memories for an agent\n'
      + '  /memory add <text> [#tag1] [@agent]  \u2014 Add a memory manually\n'
      + '  /memory edit <id> <new text>         \u2014 Edit a memory\n'
      + '  /memory del <id>                     \u2014 Delete a memory\n'
      + '  /memory search <query>               \u2014 Search memories by text',
  },
  '/kg': {
    usage: '/kg [panel | add <s> <p> <o> | stats]',
    short: t('commandShort.42'),
    detail: 'Browse and manage the knowledge graph.\n\n'
      + '  /kg                                  \u2014 Open KG panel (visual editor)\n'
      + '  /kg panel                            \u2014 Open KG panel\n'
      + '  /kg add <subject> <predicate> <object> \u2014 Quick-add a triple\n'
      + '  /kg stats                            \u2014 Show KG statistics in chat',
  },
  '/diary': {
    usage: '/diary [panel | list [type] | add <text> [#tag1 #tag2]]',
    short: t('commandShort.43'),
    detail: 'View and add entries to the current agent\'s personal diary.\n\n'
      + '  /diary                               \u2014 Open diary panel (visual editor)\n'
      + '  /diary list                          \u2014 List recent diary entries in chat\n'
      + '  /diary list <type>                   \u2014 Filter by type (observation/decision/learning/reflection)\n'
      + '  /diary add <text> [#tag1 #tag2]      \u2014 Quick add an observation entry',
  },
  '/install': {
    usage: '/install <filename.py>',
    short: t('commandShort.44'),
    detail: 'Install a custom tool from a Python file. Drag & drop a .py file into the chat or paste code.',
  },
  '/uninstall': {
    usage: '/uninstall <tool_name>',
    short: t('commandShort.45'),
    detail: 'Remove a previously installed custom tool by name.',
  },
  '/link': {
    usage: '/link <provider> <id> [bot_token] | unlink <provider> | status',
    short: t('commandShort.46'),
    detail: 'Link your account to an external provider for cross-platform messaging.\n\n'
      + '  /link <provider> <id> [bot_token] — Link account\n'
      + '  /link unlink <provider>           — Unlink account\n'
      + '  /link status                      — Show linked accounts',
  },
  '/add-secret': {
    usage: '/add-secret <name> <value>',
    short: t('commandShort.47'),
    detail: 'Stores a secret value encrypted at rest. Available as ${key} in expressions.',
  },
  '/secrets': {
    usage: '/secrets',
    short: t('commandShort.48'),
    detail: 'Lists all stored secret names (values are not shown). Also accessible as /list-secrets.',
  },
  '/add-variable': {
    usage: '/add-variable <name> <value>',
    short: t('commandShort.49'),
    detail: 'Stores a plaintext variable. Available as ${key} in expressions. Also: /add-var.',
  },
  '/variables': {
    usage: '/variables',
    short: t('commandShort.50'),
    detail: 'Lists all stored variables with their values. Also: /vars, /list-variables.',
  },
  '/view': {
    usage: '/view <filename>',
    short: t('commandShort.51'),
    detail: 'Opens the file viewer overlay to preview a file by name. Supports images, PDF, text, and code files.',
  },
  '/cost': {
    usage: '/cost [@agent|ALL]',
    short: t('commandShort.52'),
    detail: 'Displays input/output tokens, call count, and estimated cost per agent.\n\n'
      + '  /cost @ALL     — All agents\n'
      + '  /cost @grok    — Specific agent\n\n'
      + 'Cost is calculated from cost_per_1m_input/output ($ per million tokens) on the LLM service.\n'
      + 'If not configured, shows "not configured".',
  },
  '/autoconv': {
    usage: '/autoconv <on|off|status|now> @<agent|ALL> [freq]',
    short: t('commandShort.53'),
    detail: 'Enable autonomous conversation contributions from an agent.\n\n'
      + '  /autoconv on @ALL              — All agents, default 6/1m\n'
      + '  /autoconv on @grok 2-3/h       — Grok, 2-3 times per hour\n'
      + '  /autoconv on @ALL 1/2h         — All agents, once per 2h\n'
      + '  /autoconv off @ALL             — Disable for all agents\n'
      + '  /autoconv off @grok            — Disable for grok\n'
      + '  /autoconv status @ALL          — Show config for all agents\n'
      + '  /autoconv now @ALL             — Trigger all immediately\n\n'
      + 'Frequency format: <min>[-<max>]/<duration>. Units: s, m, h, d.\n'
      + 'Only one schedule per agent — re-running /autoconv on replaces the previous.\n'
      + 'Only fires when the conversation is idle (no active interaction).',
  },
  '/new': {
    usage: '/new',
    short: t('commandShort.54'),
    detail: 'Starts a fresh conversation, disconnecting from the current one.',
  },
  '/conv': {
    usage: '/conv',
    short: t('commandShort.55'),
    detail: 'Shows a list of conversations to switch between.',
  },
  '/history': {
    usage: '/history [N] [offset]',
    short: t('commandShort.56'),
    detail: 'Display messages from the current conversation.\n\n'
      + '  /history          \u2014 Show last 50 messages\n'
      + '  /history 100      \u2014 Show last 100\n'
      + '  /history 50 100   \u2014 Show 50 messages starting from offset 100',
  },
  '/export': {
    usage: '/export [json|md]',
    short: t('commandShort.57'),
    detail: 'Export the current conversation as JSON or Markdown.',
  },
  '/rename': {
    usage: '/rename <title>',
    short: t('commandShort.58'),
    detail: 'Set a title for the current conversation.',
  },
  '/delete': {
    usage: '/delete <conversation_id>',
    short: t('commandShort.59'),
    detail: 'Permanently delete a conversation by ID.',
  },
  '/delete-msg': {
    usage: '/delete-msg <index>',
    short: t('commandShort.60'),
    detail: 'Remove a specific message from the conversation by its index.',
  },
  '/search': {
    usage: '/search <query>',
    short: t('commandShort.61'),
    detail: 'Search for text in all messages of the current conversation.',
  },
  '/model': {
    usage: '/model [@agent] <name>',
    short: t('commandShort.62'),
    detail: 'Change the LLM model for the current (or specified) agent.\n\n  /model gpt-4o\n  /model @grok gpt-4o\n  /model reset',
  },
  '/flow': {
    usage: '/flow list | templates | deploy | start | stop | params | undeploy | promote',
    short: t('commandShort.63'),
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
    short: t('commandShort.64'),
    detail: 'List available prompts or view a specific prompt.\n\n'
      + '  /prompt list       \u2014 List all prompts\n'
      + '  /prompt use <name> \u2014 Show prompt content',
  },
  '/run': {
    usage: '/run <command>',
    short: t('commandShort.65'),
    detail: 'Run a command on the filesystem relay. Requires an active relay connection.',
  },
  '/diff': {
    usage: '/diff [file|ref]',
    short: t('commandShort.66'),
    detail: 'Show git diff via the filesystem relay.\n\n  /diff\n  /diff HEAD~1\n  /diff src/main.py',
  },
  '/copy': {
    usage: '/copy [N]',
    short: t('commandShort.67'),
    detail: 'Copy the last (or Nth) assistant response to clipboard.',
  },
  '/paste': {
    usage: '/paste',
    short: t('commandShort.68'),
    detail: 'Paste image or text from clipboard as an attachment.',
  },
  '/upload': {
    usage: '/upload',
    short: t('commandShort.69'),
    detail: 'Opens the file picker to upload a file as attachment.',
  },
  '/plan': {
    usage: '/plan [list | show <id> | approve <id> | cancel <id> | delete <id> | <description>]',
    short: t('commandShort.70'),
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
    short: t('commandShort.71'),
    detail: 'Not available in web UI. Use the CLI for file watching.',
  },
  '/clear-files': {
    usage: '/clear-files',
    short: t('commandShort.72'),
    detail: 'Remove all queued file attachments.',
  },
  '/clear': {
    usage: '/clear',
    short: t('commandShort.73'),
    detail: 'Removes all messages from the visible chat. History is preserved server-side.',
  },
  '/clear-store': {
    usage: '/clear-store [@agent|ALL]',
    short: t('commandShort.74'),
    detail: '/clear-store — delete all FileStore files for this conversation.\n/clear-store @<agent> — delete tool results for a specific agent.\n/clear-store @ALL — delete tool results for all agents.',
  },
  '/batch': {
    usage: '/batch <instruction> [--files <glob>]',
    short: t('commandShort.75'),
    detail: '/batch "add JSDoc to all functions" --files src/**/*.js\n/batch "convert to async/await" --files *.ts\nThe agent will split files into groups and use delegate to process them in parallel.',
  },
  '/debug': {
    usage: '/debug [description]',
    short: t('commandShort.76'),
    detail: 'Analyzes context state, recent errors, agent loops, and service health. Optionally describe the problem.',
  },
  '/loop': {
    usage: '/loop <interval> <prompt> | list | stop <key>',
    short: t('commandShort.77'),
    detail: '/loop 5m "check build status" — runs every 5 minutes\n/loop 30s /compact — runs /compact every 30s\n/loop 2-3/h "check deploy" — 2-3 times per hour (autoconv syntax)\n/loop 1/30s "ping" — once per 30 seconds\n/loop list — show active loops\n/loop stop <key> — stop a loop',
  },
  '/login': {
    usage: '/login',
    short: t('commandShort.78'),
    detail: 'Redirects to the login page.',
  },
  '/graph': {
    usage: '/graph [panel | build | report | query <question>]',
    short: t('commandShort.79'),
    detail: 'Build, view and query the project code structure graph (AST-based).\n\n'
      + '  /graph                   \u2014 Open project graph panel\n'
      + '  /graph panel             \u2014 Open project graph panel\n'
      + '  /graph build             \u2014 Build/rebuild graph from codebase (requires relay)\n'
      + '  /graph report            \u2014 Show graph report (stats, god nodes)\n'
      + '  /graph query <question>  \u2014 Search graph for matching edges',
  },
};

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
  '/restart': '/restart_from',
  '/set_llm_service': '/llm',
  '/detach': '/clear-files',
  '/add-var': '/add-variable',
  '/list-secrets': '/secrets',
  '/list-variables': '/variables',
  '/vars': '/variables',
  '/int': '/interrupt',
};

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
  '/prompt':      (text, parts, cmd) => cmdPrompt(text, parts),
  '/install':     (text, parts, cmd) => cmdInstall(),
  '/uninstall':   (text, parts, cmd) => cmdUninstall(text, parts),

  // Conversation operations (cmd_conversation.js)
  '/new':         (text, parts, cmd) => cmdNew(),
  '/conv':        (text, parts, cmd) => cmdConv(),
  '/history':     (text, parts, cmd) => cmdHistory(text, parts),
  '/export':      (text, parts, cmd) => cmdExport(text, parts, cmd),
  '/rename':      (text, parts, cmd) => cmdRename(text, parts, cmd),
  '/delete':      (text, parts, cmd) => cmdDelete(text, parts),
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
  '/audio':         (text, parts, cmd) => cmdAudio(text, parts),
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
    if (data.output) { addMsg('system', data.output); }
    if (data.message) { addMsg('system', data.message); }
    if (data.error) { addMsg('system', '\u26a0 ' + data.error); }
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

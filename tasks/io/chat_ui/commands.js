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
  '/claude-login-server': {
    usage: '/claude-login-server <service_name>',
    short: 'Login to Claude Code via server (noVNC)',
    detail: 'Opens a browser in a server Docker container for Claude OAuth login.\n\n'
      + '  /claude-login-server claude_code_llm_service\n'
      + '  Shortcut: /cls',
  },
  '/cls': { alias: '/claude-login-server' },
  '/claude-login-relay': {
    usage: '/claude-login-relay <service_name> [relay_name]',
    short: 'Login to Claude Code via relay',
    detail: 'Runs claude auth login on the relay machine.\n\n'
      + '  /claude-login-relay claude_code_llm_service\n'
      + '  /claude-login-relay claude_code_llm_service my_relay\n'
      + '  Shortcut: /clr',
  },
  '/clr': { alias: '/claude-login-relay' },
  '/claude-login-credentials': {
    usage: '/claude-login-credentials <service_name> <credentials_json>',
    short: 'Set Claude Code credentials from .credentials.json',
    detail: 'Paste the content of ~/.claude/.credentials.json.\n\n'
      + '  /claude-login-credentials claude_code_llm_service {"claudeAiOauth":...}\n'
      + '  Shortcut: /clc',
  },
  '/clc': { alias: '/claude-login-credentials' },
  '/terminal': {
    usage: '/terminal [relay_name] | /terminal close',
    short: 'Open a terminal tab on a relay',
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
    short: 'Open VS Code (code-server) tab on a relay',
    detail: 'Opens code-server in a tab. Only one VS Code instance at a time.\n\n'
      + '  /code                  \u2014 Start on first connected relay\n'
      + '  /code my_relay         \u2014 Start on a specific relay\n'
      + '  /code close            \u2014 Close VS Code tab',
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
  const re = /"([^"]*)"|\S+/g;
  let m;
  while ((m = re.exec(text)) !== null) {
    args.push(m[1] !== undefined ? m[1] : m[0]);
  }
  return args;
}

// ── Command aliases ─────────────────────────────────────────────
const _CMD_ALIASES = {
  '/restart': '/restart_from',
  '/rebuild-full': '/rebuild_clean',
  '/set_llm_service': '/llm',
  '/detach': '/clear-files',
  '/add-var': '/add-variable',
  '/list-secrets': '/secrets',
  '/list-variables': '/variables',
  '/vars': '/variables',
};

// ── Command dispatch table ──────────────────────────────────────
// Each handler receives (text, parts, cmd) and returns true.
// Handlers are defined in: cmd_agent.js, cmd_context.js, cmd_resources.js,
// cmd_conversation.js, cmd_misc.js
const _CMD_HANDLERS = {
  // Agent management (cmd_agent.js)
  '/stop':        (text, parts, cmd) => cmdStop(text, parts),
  '/agent':       (text, parts, cmd) => cmdAgent(text, parts),
  '/msg':         (text, parts, cmd) => cmdMsg(text),
  '/btw':         (text, parts, cmd) => cmdBtw(text),
  '/setname':     (text, parts, cmd) => cmdSetname(text),

  // Context operations (cmd_context.js)
  '/restart_from': (text, parts, cmd) => cmdRestartFrom(text, parts),
  '/resume':       (text, parts, cmd) => cmdResume(text),
  '/summary':      (text, parts, cmd) => cmdSummary(text, parts),
  '/compact':      (text, parts, cmd) => cmdCompactCmd(text, parts),
  '/rebuild':      (text, parts, cmd) => cmdRebuildCmd(text, parts),
  '/rebuild_clean': (text, parts, cmd) => cmdRebuildFullCmd(text, parts),
  '/context':      (text, parts, cmd) => cmdContextCmd(text, parts),

  // Resources (cmd_resources.js)
  '/task':        (text, parts, cmd) => cmdTask(text, parts),
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

  // Terminal / code-server (terminal.js)
  '/terminal':      (text, parts, cmd) => cmdTerminal(text, parts),
  '/term':          (text, parts, cmd) => cmdTerminal(text, parts),
  '/code':          (text, parts, cmd) => cmdCode(text, parts),
};

async function handleSlashCommand(text) {
  const parts = text.split(/\s+/);
  const cmd = parts[0].toLowerCase();

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
    if (data.error) { addMsg('system', '\u26a0 ' + data.error); }
    if (data.conversation_id && data.ok && data.source) {
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

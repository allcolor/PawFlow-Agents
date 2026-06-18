"""Unified server-side command parser.

Clients send {action: "command", text: "/compact grok", conversation_id: "..."}
and this module parses, dispatches, and returns the result.

This is the SINGLE source of truth for all /commands — webchat, vscode, and
pawcode CLI all use it. Client-only commands (clear-ui, connect, disconnect)
remain client-side.
"""

import logging
import re as _re_cmd
from typing import Dict


logger = logging.getLogger(__name__)


# ── Help registry ─────────────────────────────────────────────────────

HELP: Dict[str, Dict[str, str]] = {
    # ── Conversation ──
    "/new": {
        "usage": "/new",
        "short": "Start a new conversation",
        "detail": "Creates a new conversation and switches to it.",
    },
    "/conv": {
        "usage": "/conv [list | select <id> | info]",
        "short": "List or switch conversations",
        "detail": (
            "  /conv list          — List all conversations\n"
            "  /conv select <id>   — Switch to a conversation\n"
            "  /conv info          — Show current conversation info"
        ),
        "aliases": "/conversations",
    },
    "/delete": {
        "usage": "/delete [conversation_id]",
        "short": "Delete a conversation",
        "detail": "Delete the current or specified conversation.",
    },
    "/rename": {
        "usage": "/rename <name>",
        "short": "Rename current conversation",
        "detail": "Set a display name for the current conversation.",
    },
    "/export": {
        "usage": "/export [json | md]",
        "short": "Export conversation",
        "detail": "Export the current conversation as JSON or Markdown.",
    },
    "/history": {
        "usage": "/history [count]",
        "short": "Show conversation messages",
        "detail": "Display the last N messages (default: 20).",
    },
    "/search": {
        "usage": "/search <text>",
        "short": "Search messages",
        "detail": "Search for text in all messages of the current conversation.",
    },
    "/fork": {
        "usage": "/fork [name]",
        "short": "Fork the current conversation",
        "detail": (
            "Create a copy of the current conversation with all messages and context.\n"
            "The original is preserved — you can explore a different direction.\n\n"
            "  /fork                — Fork with auto-generated name\n"
            "  /fork experiment-1   — Fork with a specific name"
        ),
        "aliases": "/branch",
    },

    # ── Agent management ──
    "/agent": {
        "usage": "/agent list | create | select | delete | msg | interrupt | btw | resume | setname",
        "short": "Manage AI agents",
        "detail": (
            "  /agent list                        — List all agents\n"
            "  /agent create                      — Create a new agent (interactive)\n"
            "  /agent select <name>               — Activate an agent\n"
            "  /agent delete <name>               — Delete an agent\n"
            "  /agent msg <name> <text>           — Message a specific agent\n"
            "  /agent msg ALL <text>              — Broadcast to all agents\n"
            "  /agent interrupt <name|ALL>        — Stop and respond immediately\n"
            "  /agent btw <name|ALL> <text>       — Side-channel question\n"
            "  /agent resume <name>               — Continue from where it stopped\n"
            "  /agent setname <real> [nickname]    — Set display nickname"
        ),
    },
    "/msg": {
        "usage": "/msg <name> <text>",
        "short": "Send message to specific agent",
        "detail": "Send a message without changing the active agent.\n\n  /msg grok Explain this code\n  /msg ALL What do you think?",
        "aliases": "/message",
    },
    "/btw": {
        "usage": "/btw <name|ALL> <question>",
        "short": "Side-channel question (no interruption)",
        "detail": "Quick question to an agent without interrupting its current work.",
    },
    "/stop": {
        "usage": "/stop [agent] [-f]",
        "short": "Stop agent execution",
        "detail": (
            "Stop the current or specified agent.\n\n"
            "  /stop            — Ask current agent to wrap up\n"
            "  /stop grok       — Ask specific agent to wrap up\n"
            "  /stop -f         — Force stop immediately (no response)\n"
            "  /stop grok -f    — Force stop specific agent"
        ),
    },
    "/resume": {
        "usage": "/resume [agent]",
        "short": "Tell agent to continue",
        "detail": "Tell the current or specified agent to continue from where it stopped.",
    },
    "/setname": {
        "usage": "/setname <agent> [nickname]",
        "short": "Set agent display nickname",
        "detail": "Set or reset (omit nickname) the display name for an agent.",
    },

    # ── Context management ──
    "/compact": {
        "usage": "/compact [agent] [instructions]",
        "short": "Compact context (summarize old messages)",
        "detail": (
            "Compress conversation context by summarizing old messages.\n\n"
            "  /compact              — Compact current agent's context\n"
            "  /compact grok         — Compact specific agent\n"
            "  /compact -- focus on the auth refactor  — Focus compaction on a topic\n\n"
            "Like Claude Code, you can add instructions to focus the compaction."
        ),
    },
    "/git-prune": {
        "usage": "/git-prune",
        "short": "Prune conversation Git snapshot history",
        "detail": (
            "Run conversation Git retention now for the current conversation.\n"
            "This blocks conversation context operations like /compact, rewrites "
            "the bounded live history, expires reflogs, and runs git gc to reclaim space."
        ),
        "aliases": "/prune-git",
    },
    "/context": {
        "usage": "/context [agent]",
        "short": "View the LLM context",
        "detail": "Show the current agent context (messages, tokens, etc.).",
    },
    "/model": {
        "usage": "/model <name> | reset",
        "short": "Switch LLM model",
        "detail": (
            "Override the model for the current agent in this conversation.\n\n"
            "  /model gpt-4o       — Use gpt-4o\n"
            "  /model reset        — Revert to default\n\n"
            "This changes the model, not the service. Use /llm to change the service."
        ),
    },
    "/llm": {
        "usage": "/llm <service_name> [agent]",
        "short": "Change LLM service for agent",
        "detail": (
            "  /llm grok_service        — Set for current agent\n"
            "  /llm grok_service claude  — Set for specific agent\n\n"
            "Supports expressions: /llm ${preferred_llm}"
        ),
        "aliases": "/set_llm_service",
    },
    "/effort": {
        "usage": "/effort low | medium | high | max | reset",
        "short": "Set thinking effort level",
        "detail": (
            "Control how much the model thinks before responding.\n\n"
            "  /effort low    — No extended thinking (budget=0)\n"
            "  /effort medium — Moderate thinking (budget=5000)\n"
            "  /effort high   — Deep thinking (budget=10000)\n"
            "  /effort max    — Maximum thinking (budget=20000)\n"
            "  /effort reset  — Revert to default\n\n"
            "Supports expressions: /effort ${default_effort}"
        ),
    },
    "/fast": {
        "usage": "/fast [on | off | <model>]",
        "short": "Toggle fast/cheap model",
        "detail": (
            "Switch to a fast/cheap model for quick tasks.\n\n"
            "  /fast           — Toggle on/off\n"
            "  /fast on        — Enable (uses configured fast model)\n"
            "  /fast off       — Disable (revert to normal model)\n"
            "  /fast gpt-4o-mini  — Use a specific fast model\n\n"
            "Configure default: /add-variable fast_model gpt-4o-mini"
        ),
    },
    "/rebuild": {
        "usage": "/rebuild",
        "short": "Rebuild shared context, buckets, and compacted agent contexts",
        "detail": "Rebuild shared context from transcript, rebuild buckets, then compact every conversation agent.",
    },
    "/rebuild-full": {
        "usage": "/rebuild-full [agent]",
        "short": "Set context = full conversation",
        "detail": "Set the agent context to the full conversation (no compaction).",
    },
    "/restart": {
        "usage": "/restart <index|msg_id>",
        "short": "Restart conversation from a point",
        "detail": (
            "Truncate transcript/shared context at an absolute index or msg_id.\n\n"
            "  /restart 0          — Empty transcript and contexts\n"
            "  /restart 10         — Keep the first 10 messages\n"
            "  /restart abc123     — Keep messages through msg_id abc123"
        ),
        "aliases": "/restart_from",
    },
    "/rewind": {
        "usage": "/rewind [checkpoint]",
        "short": "Rewind to a previous checkpoint",
        "detail": (
            "Undo file changes and/or conversation to a previous point.\n\n"
            "  /rewind         — Show available checkpoints\n"
            "  /rewind 3       — Rewind to checkpoint #3\n\n"
            "Options: restore code+conversation, conversation only, code only, or summarize."
        ),
        "aliases": "/checkpoint",
    },
    "/summary": {
        "usage": "/summary [tokens] [agent]",
        "short": "Summarize context to N tokens",
        "detail": "Summarize the context to approximately N tokens.",
    },
    "/cc_restart": {
        "usage": "/cc_restart [agent]",
        "short": "Kill warm Claude Code session(s) for this conv",
        "detail": (
            "Force a fresh Claude Code subprocess spawn on the next turn.\n\n"
            "  /cc_restart          — kill every live CC session in this conv\n"
            "  /cc_restart claude   — kill only the 'claude' agent's session"
        ),
    },
    "/cc_live": {
        "usage": "/cc_live [agent]",
        "short": "Show warm Claude Code sessions for this conv",
        "detail": (
            "List live CC subprocesses still attached to this conv:\n"
            "idle time, reuse count, spawn age, service/pool index."
        ),
    },
    "/codex_restart": {
        "usage": "/codex_restart [agent]",
        "short": "Kill warm codex container(s) for this conv",
        "detail": (
            "Force a fresh codex container spawn on the next turn.\n\n"
            "  /codex_restart        — kill every live codex container in this conv\n"
            "  /codex_restart agent  — kill only that agent's container"
        ),
    },
    "/codex_live": {
        "usage": "/codex_live [agent]",
        "short": "Show warm codex containers for this conv",
        "detail": (
            "List live codex containers still pinned to this conv:\n"
            "idle time, reuse count, spawn age, service."
        ),
    },
    "/gemini_restart": {
        "usage": "/gemini_restart [agent]",
        "short": "Kill warm gemini container(s) for this conv",
        "detail": (
            "Force a fresh gemini container spawn on the next turn.\n\n"
            "  /gemini_restart        — kill every live gemini container in this conv\n"
            "  /gemini_restart agent  — kill only that agent's container"
        ),
    },
    "/gemini_live": {
        "usage": "/gemini_live [agent]",
        "short": "Show warm gemini containers for this conv",
        "detail": (
            "List live gemini containers still pinned to this conv:\n"
            "idle time, reuse count, spawn age, service."
        ),
    },

    # ── Resources ──
    "/resources": {
        "usage": "/resources",
        "short": "List all resources",
        "detail": "Show all defined resources (agents, skills, MCP servers) with status.",
    },
    "/tools": {
        "usage": "/tools",
        "short": "List available tools",
        "detail": "List all tools available to the current agent.",
    },
    "/tool-metrics": {
        "usage": "/tool-metrics",
        "short": "Show tool execution metrics",
        "detail": "Show per-tool call counts, errors, latency, and the latest error.",
    },
    "/toolmetrics": {
        "usage": "/toolmetrics",
        "short": "Show tool execution metrics",
        "detail": "Alias for /tool-metrics.",
    },
    "/call": {
        "usage": '/call tool_name(key=value, ...) or /call tool_name {"key": "value"}',
        "short": "Call a tool directly",
        "detail": (
            "Execute any agent tool from the chat.\n\n"
            "  /call web_search(query=\"quantum computing\")\n"
            "  /call list_dir(path=\"/home\")\n"
            "  /call web_search {\"query\": \"quantum computing\"}"
        ),
    },
    "/skill": {
        "usage": "/skill list | search [--source src] <query> | import [--source src] [--review-only] [--force] [--scope global|user|conversation] [--name name] <ref> | add [--force] <name> <prompt> | update [--force] <name> <prompt> | del <name> | assign @agent @skill | unassign @agent @skill | assigned @agent | run [@agent] <name> [args...] | //<name> [@agent] [args...]",
        "short": "Manage skills",
        "detail": (
            "  /skill list                    — List all skills\n"
            "  /skill search [--source src] <query> — Search external skill marketplaces\n"
            "  /skill import [--source src] [--review-only] [--force] [--scope global|user|conversation] [--name name] <ref> — Review/import an external skill\n"
            "  /skill add [--force] <name> <prompt> — Create a skill\n"
            "  /skill update [--force] <name> <prompt> — Update a skill\n"
            "  /skill del <name>              — Delete a skill\n"
            "  /skill assign @agent @skill    — Assign a skill to an agent\n"
            "  /skill unassign @agent @skill  — Remove a skill from an agent\n"
            "  /skill assigned @agent         — List skills assigned to an agent\n"
            "  /skill run [@agent] <name> [args...] — Invoke a skill now\n"
            "  //<name> [@agent] [args...]    — Shortcut for /skill run"
        ),
    },
    "/pfp": {
        "usage": "/pfp inspect|install|update|build|dev-load|dev-unload|export|uninstall|list|reload-tasks|search|registry|key-create ...",
        "short": "Manage PawFlow packages",
        "detail": (
            "  /pfp key-create                         — Create an Ed25519 signing key\n"
            "  /pfp build <pfpdir> --key-env VAR [--out file.pfp]\n"
            "  /pfp inspect <file.pfp|pfpdir|ref|url> [--confirm-download] — Verify and preview objects/capabilities\n"
            "  /pfp install <file.pfp|ref|url> [--confirm-download] [--scope user|conversation] [--include ids] [--secret logical=stored_key] [--force]\n"
            "  /pfp dev-load <pfpdir> [--scope conversation|user] [--include ids] [--exclude ids] [--secret logical=stored_key] [--replace]\n"
            "  /pfp dev-unload <package> [--scope conversation|user]\n"
            "  /pfp update <file.pfp|ref|url> [--confirm-download] [--include ids] [--exclude ids] [--force]\n"
            "  /pfp search <query>                     — Search configured decentralized registries\n"
            "  /pfp registry add <url> [--name name] [--trusted] — Add a static registry index\n"
            "  /pfp registry list|remove <name-or-url> — Manage configured registries\n"
            "  /pfp uninstall <package> [--scope user|conversation]\n"
            "  /pfp list [--scope user|conversation]   — List installed packages\n"
            "  /pfp reload-tasks [--scope user|conversation] — Reload installed package task proxies\n"
            "  /pfp export --package id --version v --include type:name[,type:name] --out dir"
        ),
    },
    "/task": {
        "usage": "/task create | assign | list | delete | pause | resume | cancel",
        "short": "Manage agent tasks",
        "detail": (
            "  /task create <name> \"<prompt>\" [--criteria \"...\"] [--interval XX]\n"
            "  /task assign <agent> <name> [--var k=v] [--verifier <agent>]\n"
            "  /task list            — Show library + running tasks\n"
            "  /task pause <id>      — Pause a task\n"
            "  /task resume <id>     — Resume a paused task\n"
            "  /task cancel <id>     — Cancel a task"
        ),
    },
    "/goal": {
        "usage": "/goal [@agent] \"<objective>\" [task options]",
        "short": "Create and assign a conversation-scoped goal task",
        "detail": (
            "Create a conversation-scoped task definition with a generated name "
            "and assign it immediately. If no agent is provided, the selected "
            "conversation agent is used. The objective is also used as criteria "
            "unless --criteria is provided.\n\n"
            "Options mirror /task assign: --criteria, --interval, --verifier, "
            "--budget, --turn-time, --total-time, --max-reschedules, --max, "
            "--context, --var, --auto-allow, --interactive."
        ),
    },
    "/permission": {
        "usage": "/permission [default | approve_edits | read_only | auto | tool <name> allow|deny|confirm|reset | tools]",
        "short": "Set tool permission mode or per-tool permissions",
        "detail": (
            "Set how tool calls are authorized for this conversation:\n\n"
            "  /permission                              — Show current mode\n"
            "  /permission default                      — Normal approval gate\n"
            "  /permission approve_edits                — Same as default (explicit approval)\n"
            "  /permission read_only                    — Block all write operations\n"
            "  /permission auto                         — Auto-approve everything (no prompts)\n"
            "  /permission tool <name> allow            — Always allow this tool (no prompt)\n"
            "  /permission tool <name> deny             — Always block this tool\n"
            "  /permission tool <name> confirm          — Always ask for confirmation\n"
            "  /permission tool <name> reset            — Remove per-tool override\n"
            "  /permission tools                        — List per-tool overrides"
        ),
    },
    "/service": {
        "usage": "/service list | add | delete | test",
        "short": "Manage LLM and external services",
        "detail": "Manage LLM services, image/video services, filesystem services, etc.",
    },
    "/workspace": {
        "usage": "/workspace [status | destroy]",
        "short": "Server-side workspace (Docker, persistent)",
        "detail": (
            "  /workspace          — Create a server workspace for this conversation\n"
            "  /workspace status   — Show workspace status\n"
            "  /workspace destroy  — Stop container and delete workspace volume"
        ),
    },
    "/relay": {
        "usage": "/relay [list | link <id> | unlink <id> | default <id>]",
        "short": "Manage relay bindings for this conversation",
        "detail": (
            "  /relay              — List relays linked to this conversation\n"
            "  /relay list         — List all available relays\n"
            "  /relay link <id>    — Link a relay to this conversation\n"
            "  /relay unlink <id>  — Unlink a relay from this conversation\n"
            "  /relay default <id> — Set the default relay for this conversation"
        ),
    },
    "/flow": {
        "usage": "/flow list | templates | deploy | start | stop | undeploy | promote",
        "short": "Manage data flows",
        "detail": "Manage NiFi-style data processing flows.",
    },
    "/prompt": {
        "usage": "/prompt list | use <name>",
        "short": "Manage prompts",
        "detail": "List available prompts or inject one into the conversation.",
    },
    "/memory": {
        "usage": "/memory list | add | edit | del | search",
        "short": "Manage agent memories",
        "detail": (
            "  /memory list [agent]      — List memories\n"
            "  /memory add <text>        — Add a memory\n"
            "  /memory search <query>    — Search memories\n"
            "  /memory del <id>          — Delete a memory"
        ),
    },
    "/cost": {
        "usage": "/cost [agent]",
        "short": "Show token usage and estimated cost",
        "detail": (
            "Show token usage per agent and estimated cost for the session.\n"
            "If max_budget_usd is configured on the LLM service, also shows remaining budget.\n"
            "A budget_warning SSE event fires at 80% usage. Agent stops at 100%."
        ),
        "aliases": "/usage",
    },

    "/image": {
        "usage": "/image [@service] prompt [--width N] [--height N] [--style S] [--negative_prompt S]",
        "short": "Generate an image",
        "detail": "Generate an image using the configured image service. Use @service to pick a specific service.",
    },
    "/video": {
        "usage": "/video [@service] prompt [--duration N] [--width N] [--height N]",
        "short": "Generate a video",
        "detail": "Generate a video using the configured video service. Use @service to pick a specific service.",
    },
    "/connect": {
        "usage": "/connect [@relay_source] /path/to/dir",
        "short": "Add a filesystem service from a connected relay",
        "detail": (
            "Spawns a new relay process on the remote machine for the given directory.\n"
            "Use @relay to specify which connected relay to use. If omitted, uses the user's default relay."
        ),
    },
    "/disconnect": {
        "usage": "/disconnect @service",
        "short": "Disconnect and remove a filesystem service",
        "detail": "Stops the remote relay process and removes the service.",
    },
    "/audio": {
        "usage": "/audio [@service] prompt [--duration N] [--style S] [--instrumental] [--lyrics TEXT]",
        "short": "Generate audio/music",
        "detail": "Generate audio or music using the configured audio service. Use @service to pick a specific service.",
    },
    "/claude-code-auth": {
        "usage": "/claude-code-auth @service_name {credentials JSON}",
        "short": "Authenticate Claude Code with subscription credentials",
        "detail": (
            "Paste the content of ~/.claude/.credentials.json after running `claude auth login` locally.\n"
            "Admin required for global services. Users can auth their own services."
        ),
    },

    # ── Secrets & Variables ──
    "/secrets": {
        "usage": "/secrets",
        "short": "List stored secrets",
        "detail": "List all encrypted secrets (values are hidden).",
        "aliases": "/list-secrets",
    },
    "/add-secret": {
        "usage": "/add-secret <name> <value>",
        "short": "Store an encrypted secret",
        "detail": "Store a secret value (API key, password, etc.). Encrypted at rest.",
    },
    "/variables": {
        "usage": "/variables",
        "short": "List stored variables",
        "detail": "List all plaintext variables.",
        "aliases": "/vars, /list-variables",
    },
    "/add-variable": {
        "usage": "/add-variable <name> <value>",
        "short": "Store a plaintext variable",
        "detail": "Store a variable accessible via ${name} in expressions.",
        "aliases": "/add-var",
    },

    # ── Scheduling ──
     "/schedules": {
         "usage": "/schedules list | add <datetime> [reason] [@agent] [--loop <seconds>] | del <key>",
         "short": "Manage scheduled wake-ups",
         "detail": "Manage scheduled poll rechecks for agents.\n\n"
                   "  /schedules list                     — List pending schedules\n"
                   "  /schedules add <YYYYMMDDHHmmss> [reason] [@agent] [--loop N]\n"
                   "                                      — Add a schedule (optionally recurring every N seconds)\n"
                   "  /schedules del <key>                — Delete a specific schedule by key\n"
                   "  /schedules del all                  — Delete all schedules",
    },
    "/autoconv": {
        "usage": "/autoconv <agent> [on|off|status] [min] [max]",
        "short": "Auto-conversation (agent thinks autonomously)",
        "detail": (
            "Enable agents to contribute thoughts at random intervals.\n\n"
            "  /autoconv grok on 30 120   — grok thinks every 30-120s\n"
            "  /autoconv grok off         — Disable\n"
            "  /autoconv grok status      — Show current settings"
        ),
    },
    "/loop": {
        "usage": "/loop [interval] <prompt or /command>",
        "short": "Run a prompt on a recurring interval",
        "detail": (
            "  /loop 5m check if deploy finished\n"
            "  /loop 10m /cost\n\n"
            "Default interval: 10 minutes."
        ),
    },

    # ── Files ──
    "/files": {
        "usage": "/files",
        "short": "Toggle the files panel",
        "detail": "Show/hide the files panel listing conversation attachments.",
    },
    "/upload": {
        "usage": "/upload",
        "short": "Upload a file",
        "detail": "Open file picker to upload an attachment.",
    },
    "/paste": {
        "usage": "/paste",
        "short": "Paste clipboard content",
        "detail": "Paste clipboard text as a message or attachment.",
    },
    "/copy": {
        "usage": "/copy",
        "short": "Copy last response to clipboard",
        "detail": "Copy the last assistant response to the clipboard.",
    },
    "/view": {
        "usage": "/view <file_id or path>",
        "short": "Preview a file",
        "detail": "Preview an image, PDF, text, or code file.",
    },
    "/run": {
        "usage": "/run [@service] <command>",
        "short": "Execute shell command via relay",
        "detail": (
            "Run a shell command through the connected relay.\n"
            "Output is shown directly, not sent to the agent.\n\n"
            "  /run git status\n"
            "  /run @my-relay ls -la\n"
            "  /run npm test\n\n"
            "If multiple relays are connected, use @service to pick one."
        ),
    },
    "/diff": {
        "usage": "/diff",
        "short": "Show git diff",
        "detail": "Show uncommitted changes in the connected workspace.",
    },
    "/plan": {
        "usage": "/plan [on | off | status]",
        "short": "Plan mode (approval before execution)",
        "detail": (
            "Toggle plan mode — agent proposes actions and waits for approval.\n\n"
            "  /plan          — Toggle on/off\n"
            "  /plan on       — Enable plan mode\n"
            "  /plan off      — Disable plan mode\n"
            "  /plan status   — Show current state"
        ),
    },

    # ── Activation & Sharing ──
    "/activate": {
        "usage": "/activate <type> <name>",
        "short": "Activate a resource",
        "detail": "Activate an agent, skill, or MCP server for this conversation.",
    },
    "/deactivate": {
        "usage": "/deactivate <type> <name>",
        "short": "Deactivate a resource",
        "detail": "Deactivate a resource from this conversation.",
    },
    "/share": {
        "usage": "/share <type> <name> <target_conv>",
        "short": "Share a resource",
        "detail": "Share a resource to another conversation.",
    },
    "/link": {
        "usage": "/link <provider> <id>",
        "short": "Link/unlink external accounts",
        "detail": "Link an external account (Telegram, Discord, etc.) to your PawFlow user.",
    },

    # ── Session ──
    "/login": {
        "usage": "/login",
        "short": "Re-authenticate",
        "detail": "Re-authenticate your session.",
    },
    "/help": {
        "usage": "/help [command]",
        "short": "Show available commands",
        "detail": "Show all commands or detailed help for a specific command.",
    },
    "/doctor": {
        "usage": "/doctor",
        "short": "Diagnose system health",
        "detail": (
            "Check the health of all system components:\n"
            "  — LLM services (configured, reachable)\n"
            "  — Relay connection\n"
            "  — FileStore\n"
            "  — Agent definitions\n"
            "  — Authentication"
        ),
    },
    "/add-dir": {
        "usage": "/add-dir <path>",
        "short": "Add a workspace directory",
        "detail": (
            "Create a filesystem service pointing to the given path.\n\n"
            "  /add-dir /home/user/project\n"
            "  /add-dir .                    — Current relay directory"
        ),
    },

    # ── Stats & Analysis ──
    "/stats": {
        "usage": "/stats",
        "short": "Show session statistics",
        "detail": "Token usage per agent, model breakdown, message counts.",
    },
    "/pr-comments": {
        "usage": "/pr-comments [PR number or URL]",
        "short": "Fetch GitHub PR comments",
        "detail": "Fetch and display comments from a GitHub pull request.",
    },
    "/security-review": {
        "usage": "/security-review",
        "short": "Security audit of pending changes",
        "detail": "Analyze git diff for security vulnerabilities (injection, auth, secrets, etc.).",
    },
    "/insights": {
        "usage": "/insights",
        "short": "Generate session insights",
        "detail": "Analyze conversation history for patterns, friction points, and suggestions.",
    },
    "/feedback": {
        "usage": "/feedback <description>",
        "short": "Report an issue or give feedback",
        "detail": "Report a bug or suggest an improvement.",
        "aliases": "/bug",
    },

    # ── Developer ──
    "/install": {
        "usage": "/install <tool_url_or_path>",
        "short": "Install a custom tool",
        "detail": "Install a custom tool handler from a URL or file path.",
    },
    "/uninstall": {
        "usage": "/uninstall <tool_name>",
        "short": "Uninstall a custom tool",
        "detail": "Remove a custom tool handler.",
    },
    "/batch": {
        "usage": "/batch <instruction>",
        "short": "Parallel changes across multiple files",
        "detail": "Instruct the agent to make coordinated changes across multiple files.",
    },
    "/debug": {
        "usage": "/debug",
        "short": "Diagnose session issues",
        "detail": "Run diagnostics on the current session (context, services, etc.).",
    },
    "/clear-store": {
        "usage": "/clear-store [agent | ALL]",
        "short": "Clean up FileStore files",
        "detail": "Delete stored files for the current agent or all agents.",
    },
    "/hooks": {
        "usage": "/hooks [list | add | del]",
        "short": "Manage tool execution hooks",
        "detail": (
            "Configure scripts that run before/after tool execution.\n\n"
            "  /hooks list                           — List active hooks\n"
            "  /hooks add pre:write eslint --fix ${path}\n"
            "  /hooks del <hook_id>"
        ),
    },
}

# Build alias map
ALIASES: Dict[str, str] = {}
for _cmd, _info in HELP.items():
    for _alias in (_info.get("aliases") or "").split(","):
        _alias = _alias.strip()
        if _alias:
            ALIASES[_alias] = _cmd


# ── Effort mapping ────────────────────────────────────────────────────

EFFORT_MAP = {
    "low": "0",
    "medium": "5000",
    "high": "10000",
    "max": "20000",
}


# ── Command parser ────────────────────────────────────────────────────

def _extract_at_agent(arg: str, default_agent: str) -> tuple:
    """Extract @agent from command arguments.

    Returns (agent_name, remaining_arg).
    @agent can appear anywhere in the arg. If not present, uses default_agent.
    "ALL" is a special target (broadcast).
    """
    m = _re_cmd.search(r'@(\S+)', arg)
    if m:
        agent = m.group(1)
        remaining = (arg[:m.start()] + arg[m.end():]).strip()
        return agent, remaining
    return default_agent, arg



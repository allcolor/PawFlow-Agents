"""Unified server-side command parser.

Clients send {action: "command", text: "/compact grok", conversation_id: "..."}
and this module parses, dispatches, and returns the result.

This is the SINGLE source of truth for all /commands — webchat, vscode, and
pawcode CLI all use it. Client-only commands (clear-ui, connect, disconnect)
remain client-side.
"""

import json
import logging
import re
import shlex
from typing import Dict, Any, List, Optional, Callable

from core import FlowFile

logger = logging.getLogger(__name__)


# ── Help registry ─────────────────────────────────────────────────────

HELP: Dict[str, Dict[str, str]] = {
    # ── Conversation ──
    "/new": {
        "usage": "/new",
        "short": "Start a new conversation",
        "detail": "Creates a new conversation and switches to it.",
        "aliases": "/clear",
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
        "usage": "/rebuild [agent]",
        "short": "Rebuild context from full history",
        "detail": "Rebuild the agent's context from the full conversation transcript.",
    },
    "/rebuild-full": {
        "usage": "/rebuild-full [agent]",
        "short": "Set context = full conversation",
        "detail": "Set the agent context to the full conversation (no compaction).",
    },
    "/restart": {
        "usage": "/restart [N] [agent]",
        "short": "Restart context from last N messages",
        "detail": "Keep only the last N messages (default: 5) as context.\n\n  /restart 10 grok",
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
    "/call": {
        "usage": '/call tool_name(key=value, ...) or /call tool_name {"key": "value"}',
        "short": "Call a tool directly",
        "detail": (
            "Execute any agent tool from the chat.\n\n"
            "  /call web_search(query=\"quantum computing\")\n"
            "  /call filesystem(action=\"list_dir\", path=\"/home\")\n"
            "  /call web_search {\"query\": \"quantum computing\"}"
        ),
    },
    "/skill": {
        "usage": "/skill list | add <name> <prompt> | del <name>",
        "short": "Manage skills",
        "detail": (
            "  /skill list              — List all skills\n"
            "  /skill add <name> <prompt> — Create a skill\n"
            "  /skill del <name>        — Delete a skill"
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
    "/service": {
        "usage": "/service list | add | delete | test",
        "short": "Manage LLM and external services",
        "detail": "Manage LLM services, image/video services, filesystem services, etc.",
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
        "detail": "Show token usage per agent and estimated cost for the session.",
        "aliases": "/usage",
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
        "usage": "/schedules [list | add | del]",
        "short": "Manage scheduled wake-ups",
        "detail": "Manage scheduled poll rechecks for agents.",
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
        "usage": "/run <command>",
        "short": "Execute shell command via relay",
        "detail": "Run a shell command through the connected relay.",
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
            "  /hooks add pre:filesystem.write eslint --fix ${path}\n"
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

def _parse_command(text: str, conversation_id: str, user_id: str,
                   agent_name: str = "") -> Optional[Dict[str, Any]]:
    """Parse a /command text into an action body dict.

    Returns None if the command is not recognized or is client-only.
    Returns {"_client_only": True, ...} for commands handled client-side.
    """
    text = text.strip()
    if not text.startswith("/"):
        return None

    # Split command and args
    parts = text.split(None, 1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    # Resolve aliases
    cmd = ALIASES.get(cmd, cmd)

    # Common fields
    base = {"conversation_id": conversation_id}

    # ── Conversation ──
    if cmd == "/new":
        return {"action": "new_conversation", **base}

    if cmd == "/delete":
        target = arg.strip() or conversation_id
        return {"action": "delete_conversation", "conversation_id": target}

    if cmd == "/rename":
        return {"action": "rename_conversation", "name": arg.strip(), **base}

    if cmd == "/export":
        fmt = arg.strip().lower() or "markdown"
        return {"action": "export", "format": fmt, **base}

    if cmd == "/history":
        limit = int(arg.strip()) if arg.strip().isdigit() else 20
        return {"action": "load_history", "limit": limit, "offset": 0, **base}

    if cmd == "/search":
        return {"action": "search_messages", "query": arg.strip(), **base}

    if cmd == "/fork":
        return {"action": "fork_conversation", "name": arg.strip(), **base}

    # ── Context ──
    if cmd == "/compact":
        # Parse: /compact [agent] [-- instructions]
        instructions = ""
        agent = agent_name
        if "--" in arg:
            before_dash, instructions = arg.split("--", 1)
            agent = before_dash.strip() or agent_name
            instructions = instructions.strip()
        elif arg.strip():
            agent = arg.strip()
        return {"action": "compact", "agent_name": agent,
                "instructions": instructions, **base}

    if cmd == "/context":
        return {"action": "view_context", "agent_name": arg.strip() or agent_name,
                **base}

    if cmd == "/model":
        return {"action": "model", "model": arg.strip(), "agent": agent_name,
                **base}

    if cmd in ("/llm", "/set_llm_service"):
        p = arg.split(None, 1)
        svc = p[0] if p else ""
        agt = p[1] if len(p) > 1 else agent_name
        return {"action": "set_llm_service", "service": svc, "agent": agt,
                **base}

    if cmd == "/effort":
        val = arg.strip().lower()
        if val == "reset":
            return {"action": "set_effort", "value": "reset", **base}
        resolved = EFFORT_MAP.get(val, val)
        return {"action": "set_effort", "value": resolved, **base}

    if cmd == "/fast":
        val = arg.strip().lower()
        if not val or val == "on":
            return {"action": "set_fast", "enabled": True, **base}
        if val == "off":
            return {"action": "set_fast", "enabled": False, **base}
        # Specific model name
        return {"action": "set_fast", "enabled": True, "model": val, **base}

    if cmd == "/rebuild":
        return {"action": "rebuild", "agent_name": arg.strip() or agent_name,
                **base}

    if cmd == "/rebuild-full":
        return {"action": "rebuild_full", "agent_name": arg.strip() or agent_name,
                **base}

    if cmd in ("/restart", "/restart_from"):
        p = arg.split()
        n = 5
        agt = agent_name
        for part in p:
            if part.isdigit():
                n = int(part)
            else:
                agt = part
        return {"action": "restart_from", "count": n, "agent_name": agt,
                **base}

    if cmd in ("/rewind", "/checkpoint"):
        return {"action": "rewind", "checkpoint": arg.strip(), **base}

    if cmd == "/summary":
        p = arg.split()
        tokens = 500
        agt = agent_name
        for part in p:
            if part.isdigit():
                tokens = int(part)
            else:
                agt = part
        return {"action": "resume_conversation", "max_tokens": tokens,
                "agent_name": agt, **base}

    # ── Agent ──
    if cmd == "/agent":
        return _parse_agent_command(arg, base, agent_name)

    if cmd in ("/msg", "/message"):
        p = arg.split(None, 1)
        target = p[0] if p else ""
        message = p[1] if len(p) > 1 else ""
        if target.upper() == "ALL":
            return {"action": "broadcast", "message": message, **base}
        return {"action": "agent_msg", "target_agent": target,
                "message": message, **base}

    if cmd == "/btw":
        p = arg.split(None, 1)
        target = p[0] if p else ""
        question = p[1] if len(p) > 1 else ""
        return {"action": "btw", "agent_name": target, "question": question,
                **base}

    if cmd == "/stop":
        force = "-f" in arg
        agt = arg.replace("-f", "").strip() or agent_name
        if force:
            return {"action": "force_stop", "agent_name": agt, **base}
        return {"action": "cancel_agent", "agent_name": agt, **base}

    if cmd == "/resume":
        return {"action": "resume_agent", "agent_name": arg.strip() or agent_name,
                **base}

    if cmd == "/setname":
        p = arg.split(None, 1)
        real = p[0] if p else ""
        nick = p[1] if len(p) > 1 else ""
        return {"action": "set_nickname", "agent_name": real, "nickname": nick,
                **base}

    # ── Resources ──
    if cmd == "/resources":
        return {"action": "list_resources", **base}

    if cmd == "/tools":
        return {"action": "list_tools", **base}

    if cmd == "/call":
        return {"action": "user_tool_call", "call_text": arg, **base}

    if cmd == "/skill":
        return _parse_skill_command(arg, base)

    if cmd == "/task":
        return _parse_task_command(arg, base)

    if cmd == "/service":
        return _parse_service_command(arg, base, user_id)

    if cmd == "/flow":
        return _parse_flow_command(arg, base)

    if cmd == "/prompt":
        p = arg.split(None, 1)
        subcmd = p[0] if p else "list"
        if subcmd == "use":
            return {"action": "use_prompt", "name": p[1] if len(p) > 1 else "",
                    **base}
        return {"action": "list_prompts", **base}

    if cmd == "/memory":
        return _parse_memory_command(arg, base, agent_name)

    if cmd in ("/cost", "/usage"):
        return {"action": "cost", "agent_name": arg.strip() or "", **base}

    # ── Secrets & Variables ──
    if cmd in ("/secrets", "/list-secrets"):
        return {"action": "list_secrets", **base}

    if cmd == "/add-secret":
        p = arg.split(None, 1)
        return {"action": "add_secret", "name": p[0] if p else "",
                "value": p[1] if len(p) > 1 else "", **base}

    if cmd in ("/variables", "/vars", "/list-variables"):
        return {"action": "list_variables", **base}

    if cmd in ("/add-variable", "/add-var"):
        p = arg.split(None, 1)
        return {"action": "add_variable", "name": p[0] if p else "",
                "value": p[1] if len(p) > 1 else "", **base}

    # ── Scheduling ──
    if cmd == "/schedules":
        return _parse_schedules_command(arg, base)

    if cmd == "/autoconv":
        return _parse_autoconv_command(arg, base, agent_name)

    # ── Files ──
    if cmd == "/run":
        return {"action": "relay_exec", "command": arg, **base}

    if cmd == "/diff":
        return {"action": "relay_exec", "command": "git diff", **base}

    if cmd == "/view":
        return {"action": "view_file", "path": arg.strip(), **base}

    # ── Activation ──
    if cmd == "/activate":
        p = arg.split(None, 1)
        return {"action": "activate_resource", "type": p[0] if p else "",
                "name": p[1] if len(p) > 1 else "", **base}

    if cmd == "/deactivate":
        p = arg.split(None, 1)
        return {"action": "deactivate_resource", "type": p[0] if p else "",
                "name": p[1] if len(p) > 1 else "", **base}

    if cmd == "/share":
        p = arg.split()
        return {"action": "share_resource",
                "type": p[0] if len(p) > 0 else "",
                "name": p[1] if len(p) > 1 else "",
                "target": p[2] if len(p) > 2 else "",
                **base}

    if cmd == "/link":
        p = arg.split(None, 1)
        return {"action": "link_account", "provider": p[0] if p else "",
                "external_id": p[1] if len(p) > 1 else "", **base}

    # ── Session ──
    if cmd == "/login":
        return {"action": "login", **base}

    if cmd == "/help":
        topic = arg.strip().lstrip("/")
        return {"action": "help", "topic": topic, **base}

    if cmd == "/doctor":
        return {"action": "doctor", **base}

    if cmd == "/add-dir":
        return {"action": "add_dir", "path": arg.strip(), **base}

    # ── Developer ──
    if cmd == "/install":
        return {"action": "install_tool", "source": arg.strip(), **base}

    if cmd == "/uninstall":
        return {"action": "uninstall_tool", "name": arg.strip(), **base}

    if cmd == "/batch":
        return {"action": "batch", "instruction": arg, **base}

    if cmd == "/debug":
        return {"action": "debug", **base}

    if cmd == "/clear-store":
        return {"action": "clear_store", "target": arg.strip() or "", **base}

    if cmd == "/hooks":
        return _parse_hooks_command(arg, base)

    if cmd == "/plan":
        val = arg.strip().lower()
        if val == "off":
            return {"action": "set_plan_mode", "enabled": False, **base}
        if val == "status":
            return {"action": "get_plan_mode", **base}
        # on or toggle
        return {"action": "set_plan_mode", "enabled": True, **base}

    # ── Media ──
    if cmd == "/imgservice":
        return _parse_media_service_command(arg, base, "image")

    if cmd == "/vidservice":
        return _parse_media_service_command(arg, base, "video")

    # ── Stats & Analysis ──
    if cmd == "/stats":
        return {"action": "stats", **base}

    if cmd == "/pr-comments":
        return {"action": "pr_comments", "pr": arg.strip(), **base}

    if cmd == "/security-review":
        return {"action": "security_review", **base}

    if cmd == "/insights":
        return {"action": "insights", **base}

    if cmd in ("/feedback", "/bug"):
        return {"action": "feedback", "report": arg, **base}

    # ── Client-only (not handled server-side) ──
    if cmd in ("/clear-ui", "/connect", "/disconnect", "/exit", "/quit",
               "/copy", "/paste", "/upload", "/files"):
        return {"_client_only": True, "command": cmd, "arg": arg}

    # Unknown command — not recognized
    return None


# ── Sub-parsers ───────────────────────────────────────────────────────

def _parse_agent_command(arg: str, base: dict, agent_name: str) -> dict:
    """Parse /agent subcommands."""
    p = arg.split(None, 2)
    subcmd = p[0] if p else "list"

    if subcmd == "list":
        return {"action": "list_agents", **base}
    if subcmd == "create":
        return {"action": "create_agent_interactive", **base}
    if subcmd == "select":
        _name = p[1] if len(p) > 1 else ""
        return {"action": "select_agent", "agent_name": _name, "name": _name,
                **base}
    if subcmd == "delete":
        return {"action": "delete_agent", "agent_name": p[1] if len(p) > 1 else "",
                **base}
    if subcmd == "msg":
        target = p[1] if len(p) > 1 else ""
        msg = p[2] if len(p) > 2 else ""
        if target.upper() == "ALL":
            return {"action": "broadcast", "message": msg, **base}
        return {"action": "agent_msg", "target_agent": target, "message": msg,
                **base}
    if subcmd == "interrupt":
        return {"action": "cancel_agent", "agent_name": p[1] if len(p) > 1 else "",
                **base}
    if subcmd == "btw":
        target = p[1] if len(p) > 1 else ""
        question = p[2] if len(p) > 2 else ""
        return {"action": "btw", "agent_name": target, "question": question,
                **base}
    if subcmd == "resume":
        return {"action": "resume_agent",
                "agent_name": p[1] if len(p) > 1 else agent_name, **base}
    if subcmd == "setname":
        real = p[1] if len(p) > 1 else ""
        nick = p[2] if len(p) > 2 else ""
        return {"action": "set_nickname", "agent_name": real, "nickname": nick,
                **base}
    # Unknown subcommand — treat as select
    return {"action": "select_agent", "agent_name": subcmd, **base}


def _parse_skill_command(arg: str, base: dict) -> dict:
    p = arg.split(None, 2)
    subcmd = p[0] if p else "list"
    if subcmd == "list":
        return {"action": "list_skills", **base}
    if subcmd == "add":
        return {"action": "create_skill", "name": p[1] if len(p) > 1 else "",
                "prompt": p[2] if len(p) > 2 else "", **base}
    if subcmd == "del":
        return {"action": "delete_skill", "name": p[1] if len(p) > 1 else "",
                **base}
    return {"action": "list_skills", **base}


def _parse_task_command(arg: str, base: dict) -> dict:
    p = arg.split(None, 1)
    subcmd = p[0] if p else "list"
    rest = p[1] if len(p) > 1 else ""

    if subcmd == "list":
        return {"action": "list_tasks", **base}
    if subcmd in ("create", "assign", "delete", "pause", "resume", "cancel"):
        return {"action": f"task_{subcmd}", "args": rest, **base}
    return {"action": "list_tasks", **base}


def _parse_service_command(arg: str, base: dict, user_id: str) -> dict:
    p = arg.split(None, 1)
    subcmd = p[0] if p else "list"
    if subcmd == "list":
        return {"action": "list_services", **base}
    return {"action": "service_command", "subcommand": subcmd,
            "args": p[1] if len(p) > 1 else "", **base}


def _parse_flow_command(arg: str, base: dict) -> dict:
    p = arg.split(None, 1)
    subcmd = p[0] if p else "list"
    return {"action": "flow_command", "subcommand": subcmd,
            "args": p[1] if len(p) > 1 else "", **base}


def _parse_memory_command(arg: str, base: dict, agent_name: str) -> dict:
    p = arg.split(None, 2)
    subcmd = p[0] if p else "list"
    if subcmd == "list":
        return {"action": "list_memories",
                "agent_name": p[1] if len(p) > 1 else agent_name, **base}
    if subcmd == "add":
        return {"action": "add_memory", "text": p[1] if len(p) > 1 else "",
                "agent_name": agent_name, **base}
    if subcmd == "search":
        return {"action": "search_memories", "query": p[1] if len(p) > 1 else "",
                "agent_name": agent_name, **base}
    if subcmd == "del":
        return {"action": "delete_memory", "memory_id": p[1] if len(p) > 1 else "",
                **base}
    if subcmd == "edit":
        return {"action": "edit_memory",
                "memory_id": p[1] if len(p) > 1 else "",
                "text": p[2] if len(p) > 2 else "", **base}
    return {"action": "list_memories", "agent_name": agent_name, **base}


def _parse_schedules_command(arg: str, base: dict) -> dict:
    p = arg.split(None, 1)
    subcmd = p[0] if p else "list"
    if subcmd == "list":
        return {"action": "list_schedules", **base}
    if subcmd == "add":
        return {"action": "add_schedule", "args": p[1] if len(p) > 1 else "",
                **base}
    if subcmd == "del":
        return {"action": "delete_schedule", "key": p[1] if len(p) > 1 else "",
                **base}
    return {"action": "list_schedules", **base}


def _parse_autoconv_command(arg: str, base: dict, agent_name: str) -> dict:
    p = arg.split()
    agt = p[0] if p else agent_name
    subcmd = p[1] if len(p) > 1 else "status"
    if subcmd in ("on", "enable"):
        min_iv = int(p[2]) if len(p) > 2 else 60
        max_iv = int(p[3]) if len(p) > 3 else min_iv * 4
        return {"action": "autoconv", "agent_name": agt, "enabled": True,
                "min_interval": min_iv, "max_interval": max_iv, **base}
    if subcmd in ("off", "disable"):
        return {"action": "autoconv", "agent_name": agt, "enabled": False,
                **base}
    return {"action": "autoconv_status", "agent_name": agt, **base}


def _parse_media_service_command(arg: str, base: dict, media_type: str) -> dict:
    p = arg.split()
    subcmd = p[0] if p else "list"
    if subcmd == "list":
        return {"action": f"list_{media_type}_services", **base}
    if subcmd == "select":
        svc = p[1] if len(p) > 1 else ""
        agt = p[2] if len(p) > 2 else "*"
        return {"action": f"set_{media_type}_service",
                "service_name": svc, "agent_name": agt, **base}
    if subcmd == "clear":
        agt = p[1] if len(p) > 1 else "*"
        return {"action": f"clear_{media_type}_service", "agent_name": agt,
                **base}
    return {"action": f"list_{media_type}_services", **base}


def _parse_hooks_command(arg: str, base: dict) -> dict:
    p = arg.split(None, 2)
    subcmd = p[0] if p else "list"
    if subcmd == "list":
        return {"action": "list_hooks", **base}
    if subcmd == "add":
        return {"action": "add_hook", "spec": p[1] if len(p) > 1 else "",
                "command": p[2] if len(p) > 2 else "", **base}
    if subcmd == "del":
        return {"action": "delete_hook", "hook_id": p[1] if len(p) > 1 else "",
                **base}
    return {"action": "list_hooks", **base}


# ── Main handler ──────────────────────────────────────────────────────

def _handle_command_dispatch(self, action, body, store, user_id, flowfile):
    """Handle the unified 'command' action.

    Receives {action: "command", text: "/compact grok", conversation_id: "..."}
    Parses the text, and either:
    - Returns a result directly (for /help, etc.)
    - Re-dispatches to existing action handlers
    """
    if action != "command":
        return None

    text = body.get("text", "").strip()
    conversation_id = body.get("conversation_id", "")
    agent_name = body.get("agent_name", "")

    if not text.startswith("/"):
        return None

    # Parse the command
    parsed = _parse_command(text, conversation_id, user_id, agent_name)

    if parsed is None:
        # Unknown command
        flowfile.set_content(json.dumps({
            "error": f"Unknown command: {text.split()[0]}",
            "hint": "Type /help to see available commands.",
        }).encode())
        flowfile.set_attribute("http.response.status", "400")
        return [flowfile]

    if parsed.get("_client_only"):
        # Client-only command — tell client to handle it
        flowfile.set_content(json.dumps({
            "client_only": True,
            "command": parsed["command"],
            "arg": parsed.get("arg", ""),
        }).encode())
        return [flowfile]

    # Handle /help directly
    if parsed.get("action") == "help":
        return _handle_help(parsed.get("topic", ""), flowfile)

    # Re-dispatch: inject parsed action into body and let normal handlers process it
    parsed_action = parsed.pop("action")
    # Merge parsed fields into body for the handler
    dispatch_body = {**body, **parsed, "action": parsed_action}

    # Update flowfile with new body
    flowfile.set_content(json.dumps(dispatch_body).encode())

    # Return None to let the normal action dispatch handle it
    # The caller should re-dispatch with the new action
    return {"_redispatch": True, "body": dispatch_body, "flowfile": flowfile}


def _handle_help(topic: str, flowfile: FlowFile) -> list:
    """Handle /help [topic] — return help text."""
    if not topic:
        # List all commands grouped by category
        categories = {
            "Conversation": ["/new", "/conv", "/delete", "/rename", "/export",
                             "/history", "/search", "/fork"],
            "Agent": ["/agent", "/msg", "/btw", "/stop", "/resume", "/setname"],
            "Context": ["/compact", "/context", "/model", "/llm", "/effort",
                        "/fast", "/rebuild", "/restart", "/rewind", "/summary"],
            "Resources": ["/resources", "/tools", "/call", "/skill", "/task",
                          "/service", "/flow", "/prompt", "/memory", "/cost"],
            "Secrets & Variables": ["/secrets", "/add-secret", "/variables",
                                    "/add-variable"],
            "Scheduling": ["/schedules", "/autoconv", "/loop"],
            "Files": ["/files", "/upload", "/paste", "/copy", "/view",
                      "/run", "/diff"],
            "Mode": ["/plan", "/hooks"],
            "Activation": ["/activate", "/deactivate", "/share", "/link"],
            "Session": ["/login", "/help", "/doctor", "/add-dir"],
            "Analysis": ["/stats", "/insights", "/pr-comments",
                         "/security-review", "/feedback"],
            "Developer": ["/install", "/uninstall", "/batch", "/debug",
                          "/clear-store"],
        }
        lines = []
        for cat, cmds in categories.items():
            lines.append(f"\n**{cat}**")
            for c in cmds:
                info = HELP.get(c, {})
                short = info.get("short", "")
                aliases = info.get("aliases", "")
                alias_str = f" ({aliases})" if aliases else ""
                lines.append(f"  `{c}`{alias_str} — {short}")
        text = "## Available Commands\n" + "\n".join(lines)
        text += "\n\nType `/help <command>` for detailed help."
        flowfile.set_content(json.dumps({"help": text}).encode())
        return [flowfile]

    # Specific command help
    cmd = f"/{topic}" if not topic.startswith("/") else topic
    cmd = ALIASES.get(cmd, cmd)
    info = HELP.get(cmd)
    if not info:
        flowfile.set_content(json.dumps({
            "error": f"No help for '{topic}'",
            "hint": "Type /help to see all commands.",
        }).encode())
        return [flowfile]

    text = f"**{cmd}**"
    if info.get("aliases"):
        text += f" (aliases: {info['aliases']})"
    text += f"\n\nUsage: `{info.get('usage', cmd)}`\n\n{info.get('detail', info.get('short', ''))}"
    flowfile.set_content(json.dumps({"help": text}).encode())
    return [flowfile]

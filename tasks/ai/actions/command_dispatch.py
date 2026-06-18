"""Unified server-side command parser.

Clients send {action: "command", text: "/compact grok", conversation_id: "..."}
and this module parses, dispatches, and returns the result.

This is the SINGLE source of truth for all /commands — webchat, vscode, and
pawcode CLI all use it. Client-only commands (clear-ui, connect, disconnect)
remain client-side.

The help registry and the @-agent helper live in _cmd_help.py; the per-domain
argument parsers live in _cmd_parsers.py. This module keeps the top-level
_parse_command router and the _handle_* action handlers.
"""

import json
import logging
from typing import Any, Dict, Optional

from core import FlowFile

from tasks.ai.actions._cmd_help import ALIASES, EFFORT_MAP, HELP, _extract_at_agent
from tasks.ai.actions._cmd_parsers import (
    _parse_agent_command,
    _parse_autoconv_command,
    _parse_flow_command,
    _parse_goal_command,
    _parse_hooks_command,
    _parse_media_service_command,
    _parse_memory_command,
    _parse_pfp_command,
    _parse_schedules_command,
    _parse_service_command,
    _parse_skill_command,
    _parse_skill_sugar_command,
    _parse_task_command,
)

logger = logging.getLogger(__name__)


def _parse_command(text: str, conversation_id: str, user_id: str,
                   agent_name: str = "") -> Optional[Dict[str, Any]]:
    """Parse a /command text into an action body dict.

    Returns None if the command is not recognized or is client-only.
    Returns {"_client_only": True, ...} for commands handled client-side.

    Convention: @agent in any argument targets that agent.
    If no @agent, uses the currently selected agent.
    """
    text = text.strip()
    if not text.startswith("/"):
        return None

    # Skill-run sugar: //skill-name [@agent] [args...]
    if text.startswith("//"):
        return _parse_skill_sugar_command(
            text, {"conversation_id": conversation_id}, agent_name)

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
        # Parse: /compact [@agent] [--force] [-- instructions]
        agent, rest = _extract_at_agent(arg, agent_name)
        force = False
        if "--force" in rest:
            force = True
            rest = rest.replace("--force", "").strip()
        instructions = ""
        if "--" in rest:
            _, instructions = rest.split("--", 1)
            instructions = instructions.strip()
        return {"action": "compact", "agent_name": agent,
                "instructions": instructions, "force": force, **base}

    if cmd == "/git-prune":
        return {"action": "git_prune", **base}

    if cmd == "/context":
        agent, _ = _extract_at_agent(arg, agent_name)
        return {"action": "view_context", "agent_name": agent, **base}

    if cmd == "/model":
        return {"action": "model", "model": arg.strip(), "agent": agent_name,
                **base}

    if cmd in ("/llm", "/set_llm_service"):
        agent, svc = _extract_at_agent(arg, agent_name)
        return {"action": "set_llm_service", "service": svc.strip(), "agent": agent,
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
        return {"action": "rebuild", **base}

    if cmd == "/rebuild-full":
        agt, _ = _extract_at_agent(arg, agent_name)
        return {"action": "rebuild_full", "agent_name": agt, **base}

    if cmd in ("/restart", "/restart_from"):
        p = arg.split()
        restart_index = None
        restart_msg_id = ""
        for part in p:
            if part.startswith("@"):
                continue
            if part.isdigit():
                restart_index = int(part)
            else:
                restart_msg_id = part
        out = {"action": "restart_from", **base}
        if restart_msg_id:
            out["msg_id"] = restart_msg_id
        elif restart_index is not None:
            out["restart_index"] = restart_index
        else:
            out["error"] = "Usage: /restart_from <index|msg_id>"
        return out

    if cmd in ("/rewind", "/checkpoint"):
        return {"action": "rewind", "checkpoint": arg.strip(), **base}

    if cmd == "/summary":
        agt, rest = _extract_at_agent(arg, agent_name)
        tokens = 500
        for part in rest.split():
            if part.isdigit():
                tokens = int(part)
        return {"action": "resume_conversation", "max_tokens": tokens,
                "agent_name": agt, **base}

    if cmd == "/cc_restart":
        # Optional single positional arg = agent name. Empty = all agents.
        _arg = arg.strip()
        agt = _arg if _arg else ""
        return {"action": "cc_restart", "agent_name": agt, **base}

    if cmd == "/cc_live":
        _arg = arg.strip()
        agt = _arg if _arg else ""
        return {"action": "cc_live_status", "agent_name": agt, **base}

    if cmd == "/codex_restart":
        _arg = arg.strip()
        agt = _arg if _arg else ""
        return {"action": "codex_restart", "agent_name": agt, **base}

    if cmd == "/codex_live":
        _arg = arg.strip()
        agt = _arg if _arg else ""
        return {"action": "codex_live_status", "agent_name": agt, **base}

    if cmd == "/gemini_restart":
        _arg = arg.strip()
        agt = _arg if _arg else ""
        return {"action": "gemini_restart", "agent_name": agt, **base}

    if cmd == "/gemini_live":
        _arg = arg.strip()
        agt = _arg if _arg else ""
        return {"action": "gemini_live_status", "agent_name": agt, **base}

    # ── Agent ──
    if cmd == "/agent":
        return _parse_agent_command(arg, base, agent_name)

    if cmd in ("/msg", "/message"):
        target, message = _extract_at_agent(arg, agent_name)
        if target.upper() == "ALL":
            return {"action": "broadcast_agents", "message": message, **base}
        return {"action": "agent_msg", "target_agent": target,
                "message": message, **base}

    if cmd == "/btw":
        target, question = _extract_at_agent(arg, agent_name)
        return {"action": "btw", "agent_name": target, "question": question,
                **base}

    if cmd == "/stop":
        _stop_arg = arg.replace("-f", "").strip()
        agt, _ = _extract_at_agent(_stop_arg, agent_name)
        return {"action": "cancel", "agent_name": agt, **base}

    if cmd == "/resume":
        agt, _ = _extract_at_agent(arg, agent_name)
        return {"action": "resume_agent", "agent_name": agt, **base}

    if cmd == "/setname":
        real, nick = _extract_at_agent(arg, agent_name)
        return {"action": "set_nickname", "agent_name": real, "nickname": nick.strip(),
                **base}

    # ── Filesystem relay ──
    if cmd == "/connect":
        # /connect @relay_source /path/to/dir — or just /connect /path (uses default relay)
        svc, path = _extract_at_agent(arg, "")
        return {"action": "relay_connect", "relay_source": svc,
                "path": path.strip(), **base}

    if cmd == "/disconnect":
        svc, _ = _extract_at_agent(arg, "")
        return {"action": "relay_disconnect", "service_id": svc, **base}

    # ── Resources ──
    if cmd == "/resources":
        return {"action": "list_resources", **base}

    if cmd == "/tools":
        return {"action": "list_tools", **base}

    if cmd in ("/tool-metrics", "/toolmetrics"):
        return {"action": "tool_metrics", **base}

    if cmd == "/call":
        return {"action": "user_tool_call", "call_text": arg, **base}

    if cmd == "/skill":
        return _parse_skill_command(arg, base, agent_name)

    if cmd == "/add-skill":
        return _parse_skill_command("add " + arg, base, agent_name)

    if cmd == "/pfp":
        return _parse_pfp_command(arg, base)

    if cmd == "/task":
        return _parse_task_command(arg, base)

    if cmd == "/goal":
        return _parse_goal_command(arg, base, agent_name)

    if cmd == "/permission":
        parts = arg.strip().split() if arg.strip() else []
        if not parts:
            return {"action": "get_permission_mode", **base}
        subcmd = parts[0].lower()
        if subcmd == "tools":
            return {"action": "get_tool_permissions", **base}
        if subcmd == "tool":
            if len(parts) < 3:
                return {"display": "Usage: /permission tool <name> allow|deny|confirm|reset"}
            tool_name = parts[1]
            perm = parts[2].lower()
            if perm == "reset":
                perm = ""
            elif perm not in ("allow", "deny", "confirm"):
                return {"display": f"Invalid permission: {perm}. Use allow|deny|confirm|reset"}
            return {"action": "set_tool_permission", "tool_name": tool_name, "permission": perm, **base}
        # Global mode
        mode = subcmd
        if mode not in ("default", "approve_edits", "read_only", "auto"):
            return {"display": f"Invalid mode: {mode}. Valid: default, approve_edits, read_only, auto"}
        return {"action": "set_permission_mode", "mode": mode, **base}

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

    if cmd in ("/image", "/video", "/audio"):
        # /image [@service] prompt text [--param value ...]
        # /video [@service] prompt text [--param value ...]
        # /audio [@service] prompt text [--param value ...]
        _media_map = {"/image": "generate_image", "/video": "generate_video", "/audio": "generate_audio"}
        tool_name = _media_map[cmd]
        svc, rest = _extract_at_agent(arg, "")  # @service, not @agent
        # Parse --key value params from the rest (supports multiline prompts)
        params = {}
        # Split on " --" or "\n--" to separate prompt from params
        import re as _re_media
        parts = _re_media.split(r'(?:^|\s)--', rest, maxsplit=0)
        params["prompt"] = parts[0].strip()
        for part in parts[1:]:
            kv = part.split(None, 1)
            if len(kv) == 2:
                params[kv[0]] = kv[1].strip().strip('"').strip("'")
            elif kv:
                params[kv[0]] = "true"
        # Convert numeric params
        for _nk in ("width", "height", "num_inference_steps", "duration"):
            if _nk in params:
                try:
                    params[_nk] = int(params[_nk])
                except ValueError:
                    pass
        for _fk in ("guidance_scale",):
            if _fk in params:
                try:
                    params[_fk] = float(params[_fk])
                except ValueError:
                    pass
        # Convert boolean params
        for _bk in ("instrumental",):
            if _bk in params:
                params[_bk] = params[_bk].lower() in ("true", "1", "yes", "")
        if svc:
            params["_service"] = svc
        # Dispatch as call_tool — same path as /call
        return {"action": "call_tool", "tool_name": tool_name,
                "arguments": params, **base}

    if cmd == "/claude-code-auth":
        # /claude-code-auth @service_name {credentials JSON}
        # Extract service name via @, rest is the credentials JSON
        svc_name, creds = _extract_at_agent(arg, "")
        return {"action": "claude_code_auth", "service_id": svc_name,
                "credentials": creds.strip(), **base}

    if cmd == "/memory":
        return _parse_memory_command(arg, base, agent_name)

    if cmd in ("/cost", "/usage"):
        agt, _ = _extract_at_agent(arg, "")
        return {"action": "cost", "agent_name": agt, **base}

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
        _run_arg = arg.strip()
        _run_svc = ""
        if _run_arg.startswith("@"):
            _parts = _run_arg.split(None, 1)
            _run_svc = _parts[0][1:]  # strip @
            _run_arg = _parts[1] if len(_parts) > 1 else ""
        return {"action": "exec_inline", "command": _run_arg, "service": _run_svc, **base}

    if cmd == "/diff":
        return {"action": "exec_inline", "command": "git diff", **base}

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

    # ── Server workspace ──
    if cmd == "/workspace":
        sub = arg.strip().lower()
        if sub == "destroy":
            return {"action": "destroy_server_workspace", **base}
        if sub == "status":
            return {"action": "server_workspace_status", **base}
        # Default: create (idempotent)
        return {"action": "create_server_workspace", **base}

    # ── Relay bindings ──
    if cmd == "/relay":
        p = arg.strip().split(None, 1)
        sub = (p[0] if p else "").lower()
        rest = p[1].strip() if len(p) > 1 else ""
        if sub == "link":
            return {"action": "relay_link", "relay_id": rest, **base}
        if sub == "unlink":
            return {"action": "relay_unlink", "relay_id": rest, **base}
        if sub == "default":
            return {"action": "relay_default", "relay_id": rest, **base}
        if sub == "list":
            return {"action": "relay_list_available", **base}
        if sub == "local":
            # /relay local <relay_id> true|false [@agent]
            parts = rest.split()
            if len(parts) < 2:
                return {"action": "relay_set_local", "error": "Usage: /relay local <relay_id> true|false [@agent]", **base}
            rid = parts[0]
            val = parts[1].lower() in ("true", "1", "on", "yes")
            agent = parts[2].lstrip("@") if len(parts) > 2 else ""
            return {"action": "relay_set_local", "relay_id": rid, "local": val, "agent": agent, **base}
        # Default: show linked relays
        return {"action": "relay_status", **base}

    # ── Conversation remote filesystem mounts ──
    if cmd in ("/remote-fs", "/remotefs"):
        p = arg.strip().split(None, 1)
        sub = (p[0] if p else "").lower()
        rest = p[1].strip() if len(p) > 1 else ""
        if sub == "link":
            parts = rest.split()
            return {
                "action": "remote_fs_link",
                "service_id": parts[0] if parts else "",
                "scope": parts[1] if len(parts) > 1 else "",
                **base,
            }
        if sub == "unlink":
            return {"action": "remote_fs_unlink", "service_id": rest, **base}
        if sub == "list":
            return {"action": "remote_fs_list_available", **base}
        return {"action": "remote_fs_status", **base}

    # ── Client-only (not handled server-side) ──
    if cmd in ("/clear", "/clear-ui", "/connect", "/disconnect", "/exit", "/quit",
               "/copy", "/paste", "/upload", "/files"):
        return {"_client_only": True, "command": cmd, "arg": arg}

    # Unknown command — not recognized
    return None


# ── Sub-parsers ───────────────────────────────────────────────────────



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
                             "/history", "/search", "/fork", "/encrypt"],
            "Agent": ["/agent", "/msg", "/btw", "/stop", "/resume", "/setname"],
            "Context": ["/compact", "/git-prune", "/context", "/model", "/llm", "/effort",
                        "/fast", "/rebuild", "/restart", "/rewind", "/summary",
                        "/cc_restart", "/cc_live",
                        "/codex_restart", "/codex_live",
                        "/gemini_restart", "/gemini_live"],
            "Resources": ["/resources", "/tools", "/call", "/skill", "/task",
                          "/service", "/flow", "/prompt", "/memory", "/cost"],
            "Secrets & Variables": ["/secrets", "/add-secret", "/variables",
                                    "/add-variable"],
            "Scheduling": ["/schedules", "/autoconv", "/loop"],
            "Files": ["/files", "/upload", "/paste", "/copy", "/view",
                      "/run", "/diff", "/relay", "/workspace"],
            "Mode": ["/plan", "/hooks", "/permission"],
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

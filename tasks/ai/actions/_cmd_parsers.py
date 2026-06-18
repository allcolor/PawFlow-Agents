"""Per-domain /command argument parsers extracted from command_dispatch.py.

Depends downward on tasks.ai.actions._cmd_help only. The command_dispatch
facade imports these parsers and routes to them from _parse_command.
"""

import shlex
from typing import List

from tasks.ai.actions._cmd_help import _extract_at_agent


def _parse_agent_command(arg: str, base: dict, agent_name: str) -> dict:
    """Parse /agent subcommands. @agent convention for all agent params."""
    p = arg.split(None, 1)
    subcmd = (p[0] if p else "list").lower()
    rest = p[1] if len(p) > 1 else ""

    if subcmd == "list":
        return {"action": "list_agents", **base}
    if subcmd == "create":
        return {"action": "create_agent_interactive", **base}
    if subcmd == "select":
        agt, _ = _extract_at_agent(rest, "")
        return {"action": "select_agent", "agent_name": agt, "name": agt, **base}
    if subcmd == "delete":
        agt, _ = _extract_at_agent(rest, "")
        return {"action": "delete_agent", "agent_name": agt, **base}
    if subcmd == "msg":
        target, msg = _extract_at_agent(rest, agent_name)
        if target.upper() == "ALL":
            return {"action": "broadcast_agents", "message": msg, **base}
        return {"action": "agent_msg", "target_agent": target, "message": msg, **base}
    if subcmd == "interrupt":
        agt, _ = _extract_at_agent(rest, agent_name)
        return {"action": "interrupt", "agent_name": agt, **base}
    if subcmd == "btw":
        target, question = _extract_at_agent(rest, agent_name)
        return {"action": "btw", "agent_name": target, "question": question, **base}
    if subcmd == "resume":
        agt, _ = _extract_at_agent(rest, agent_name)
        return {"action": "resume_agent", "agent_name": agt, **base}
    if subcmd == "setname":
        real, nick = _extract_at_agent(rest, agent_name)
        return {"action": "set_nickname", "agent_name": real, "nickname": nick.strip(),
                **base}
    # Unknown subcommand — treat as select (supports @agent or plain name)
    agt, _ = _extract_at_agent(arg, subcmd)
    return {"action": "select_agent", "agent_name": agt, **base}


def _parse_skill_sugar_command(text: str, base: dict, agent_name: str = "") -> dict:
    """Parse //skill-name [@agent] [args...] as /skill run syntax."""
    rest = text[2:].strip()
    parts = rest.split(None, 2)
    skill_name = parts[0].lstrip("@") if parts else ""
    if skill_name.startswith("/"):
        skill_name = ""
    target = agent_name
    arguments = ""
    if len(parts) > 1:
        if parts[1].startswith("@"):
            target = parts[1].lstrip("@")
            arguments = parts[2] if len(parts) > 2 else ""
        else:
            arguments = parts[1]
            if len(parts) > 2:
                arguments += " " + parts[2]
    return {
        "action": "run_skill",
        "target_agent": target,
        "skill_name": skill_name,
        "arguments": arguments,
        **base,
    }


def _skill_short_description(body: str) -> str:
    """Derive a concise manifest description from a skill body.

    `/skill add` only takes a single free-text argument for the body, so the
    description (injected into the system prompt manifest) is taken from the
    first non-empty line rather than the whole body.
    """
    for line in str(body or "").splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line[:200]
    return ""


def _parse_skill_command(arg: str, base: dict, agent_name: str = "") -> dict:
    p = arg.split(None, 2)
    subcmd = p[0] if p else "list"
    if subcmd == "list":
        return {"action": "list_skills", **base}
    if subcmd == "add":
        force, rest = _parse_leading_force(arg[len(subcmd):].strip())
        parts = rest.split(None, 1)
        body = parts[1] if len(parts) > 1 else ""
        return {"action": "create_skill", "name": parts[0].lstrip("@") if parts else "",
                "description": _skill_short_description(body),
                "instructions": body, "force": force, **base}
    if subcmd in ("update", "modify", "edit"):
        force, rest = _parse_leading_force(arg[len(subcmd):].strip())
        parts = rest.split(None, 1)
        body = parts[1] if len(parts) > 1 else ""
        return {"action": "update_skill", "name": parts[0].lstrip("@") if parts else "",
                "description": _skill_short_description(body),
                "instructions": body, "force": force, **base}
    if subcmd == "del":
        return {"action": "delete_skill", "name": p[1].lstrip("@") if len(p) > 1 else "",
                **base}
    if subcmd == "assign":
        return {
            "action": "assign_skill",
            "agent_name": p[1].lstrip("@") if len(p) > 1 else "",
            "skill_name": p[2].split(None, 1)[0].lstrip("@") if len(p) > 2 else "",
            **base,
        }
    if subcmd == "unassign":
        return {
            "action": "unassign_skill",
            "agent_name": p[1].lstrip("@") if len(p) > 1 else "",
            "skill_name": p[2].split(None, 1)[0].lstrip("@") if len(p) > 2 else "",
            **base,
        }
    if subcmd == "assigned":
        return {
            "action": "list_agent_skills",
            "agent_name": p[1].lstrip("@") if len(p) > 1 else "",
            **base,
        }
    if subcmd == "search":
        rest = arg[len(subcmd):].strip()
        try:
            tokens = shlex.split(rest)
        except ValueError:
            tokens = rest.split()
        source = "all"
        query_parts = []
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if tok == "--source" and i + 1 < len(tokens):
                source = tokens[i + 1]
                i += 2
                continue
            query_parts.append(tok)
            i += 1
        return {
            "action": "search_skill_marketplace",
            "source": source,
            "query": " ".join(query_parts).strip(),
            **base,
        }
    if subcmd == "import":
        rest = arg[len(subcmd):].strip()
        try:
            tokens = shlex.split(rest)
        except ValueError as exc:
            return {"action": "import_skill_marketplace", "ref": "", "error": str(exc), **base}
        source = ""
        review_only = False
        force = False
        scope = "user"
        name = ""
        ref_parts = []
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if tok == "--source" and i + 1 < len(tokens):
                source = tokens[i + 1]
                i += 2
                continue
            if tok == "--review-only":
                review_only = True
                i += 1
                continue
            if tok == "--force":
                force = True
                i += 1
                continue
            if tok == "--scope" and i + 1 < len(tokens):
                scope = tokens[i + 1]
                i += 2
                continue
            if tok == "--name" and i + 1 < len(tokens):
                name = tokens[i + 1]
                i += 2
                continue
            ref_parts.append(tok)
            i += 1
        return {
            "action": "import_skill_marketplace",
            "source": source,
            "ref": " ".join(ref_parts).strip(),
            "name": name,
            "review_only": review_only,
            "force": force,
            "scope": scope,
            **base,
        }
    if subcmd == "run":
        rest = arg[len(subcmd):].strip()
        target, rest = _extract_at_agent(rest, agent_name)
        parts = rest.split(None, 1)
        skill_name = parts[0].lstrip("@") if parts else ""
        arguments = parts[1] if len(parts) > 1 else ""
        return {
            "action": "run_skill",
            "target_agent": target,
            "skill_name": skill_name,
            "arguments": arguments,
            **base,
        }
    return {"action": "list_skills", **base}


def _parse_leading_force(rest: str) -> tuple[bool, str]:
    rest = (rest or "").strip()
    if rest == "--force":
        return True, ""
    if rest.startswith("--force "):
        return True, rest[len("--force "):].strip()
    return False, rest


def _parse_pfp_command(arg: str, base: dict) -> dict:
    try:
        tokens = shlex.split(arg or "")
    except ValueError as exc:
        return {"action": "pfp_error", "error": str(exc), **base}
    subcmd = tokens[0].lower() if tokens else "list"
    rest = tokens[1:]
    result = {"action": f"pfp_{subcmd.replace('-', '_')}", **base}
    if subcmd in ("key-create", "key_create"):
        result["action"] = "pfp_key_create"
        return result
    if subcmd in ("list", "list-installed", "list_installed"):
        result["action"] = "pfp_list_installed"
        result.update(_parse_pfp_flags(rest))
        return result
    if subcmd in ("reload-tasks", "reload_tasks"):
        result["action"] = "pfp_reload_tasks"
        result.update(_parse_pfp_flags(rest))
        return result
    if subcmd == "search":
        flags = _parse_pfp_flags(rest)
        result.update(flags)
        result["query"] = " ".join(flags.get("_positionals", [])).strip()
        return result
    if subcmd == "registry":
        return _parse_pfp_registry_command(rest, base)
    if subcmd == "inspect":
        flags = _parse_pfp_flags(rest)
        result.update(flags)
        result["path"] = flags.get("path", "")
        return result
    if subcmd == "build":
        flags = _parse_pfp_flags(rest)
        result.update(flags)
        result["source_dir"] = flags.get("path", "")
        return result
    if subcmd == "install":
        flags = _parse_pfp_flags(rest)
        result.update(flags)
        result["path"] = flags.get("path", "")
        return result
    if subcmd in ("dev-load", "dev_load"):
        flags = _parse_pfp_flags(rest)
        if "--scope" not in rest:
            flags["scope"] = "conversation"
        result["action"] = "pfp_dev_load"
        result.update(flags)
        result["source_dir"] = flags.get("path", "")
        return result
    if subcmd in ("dev-unload", "dev_unload"):
        flags = _parse_pfp_flags(rest)
        if "--scope" not in rest:
            flags["scope"] = "conversation"
        result["action"] = "pfp_dev_unload"
        result.update(flags)
        result["package"] = flags.get("path", "")
        return result
    if subcmd == "update":
        flags = _parse_pfp_flags(rest)
        result.update(flags)
        result["path"] = flags.get("path", "")
        return result
    if subcmd == "uninstall":
        flags = _parse_pfp_flags(rest)
        result.update(flags)
        result["package"] = flags.get("path", "")
        return result
    if subcmd == "export":
        result.update(_parse_pfp_flags(rest))
        return result
    return {"action": "pfp_error", "error": f"Unknown /pfp subcommand: {subcmd}", **base}


def _parse_pfp_flags(tokens: List[str]) -> dict:
    data = {
        "scope": "user",
        "include": [],
        "exclude": [],
        "force": False,
        "replace": False,
        "dry_run": False,
        "path": "",
        "secret_bindings": {},
    }
    positional = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--scope" and i + 1 < len(tokens):
            data["scope"] = tokens[i + 1]
            i += 2
            continue
        if tok == "--include" and i + 1 < len(tokens):
            data["include"].extend(_split_csv(tokens[i + 1]))
            i += 2
            continue
        if tok == "--exclude" and i + 1 < len(tokens):
            data["exclude"].extend(_split_csv(tokens[i + 1]))
            i += 2
            continue
        if tok == "--out" and i + 1 < len(tokens):
            data["output_path"] = tokens[i + 1]
            data["output_dir"] = tokens[i + 1]
            i += 2
            continue
        if tok == "--key" and i + 1 < len(tokens):
            data["private_key"] = tokens[i + 1]
            i += 2
            continue
        if tok == "--key-env" and i + 1 < len(tokens):
            data["private_key_env"] = tokens[i + 1]
            i += 2
            continue
        if tok == "--package" and i + 1 < len(tokens):
            data["package"] = tokens[i + 1]
            i += 2
            continue
        if tok == "--version" and i + 1 < len(tokens):
            data["version"] = tokens[i + 1]
            i += 2
            continue
        if tok == "--name" and i + 1 < len(tokens):
            data["name"] = tokens[i + 1]
            i += 2
            continue
        if tok == "--sha256" and i + 1 < len(tokens):
            data["sha256"] = tokens[i + 1]
            i += 2
            continue
        if tok == "--secret" and i + 1 < len(tokens):
            name, _, key = tokens[i + 1].partition("=")
            if name.strip() and key.strip():
                data["secret_bindings"][name.strip()] = key.strip()
            i += 2
            continue
        if tok == "--limit" and i + 1 < len(tokens):
            try:
                data["limit"] = int(tokens[i + 1])
            except ValueError:
                data["limit"] = tokens[i + 1]
            i += 2
            continue
        if tok == "--force":
            data["force"] = True
            i += 1
            continue
        if tok == "--confirm-download":
            data["confirm_download"] = True
            i += 1
            continue
        if tok == "--replace":
            data["replace"] = True
            i += 1
            continue
        if tok == "--trusted":
            data["trusted"] = True
            i += 1
            continue
        if tok == "--dry-run":
            data["dry_run"] = True
            i += 1
            continue
        positional.append(tok)
        i += 1
    if positional and not data.get("path"):
        data["path"] = positional[0]
    data["_positionals"] = positional
    return data


def _split_csv(value: str) -> List[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def _parse_pfp_registry_command(tokens: List[str], base: dict) -> dict:
    subcmd = tokens[0].lower() if tokens else "list"
    rest = tokens[1:]
    flags = _parse_pfp_flags(rest)
    if subcmd == "add":
        return {"action": "pfp_registry_add", "url": flags.get("path", ""), **flags, **base}
    if subcmd in ("remove", "rm", "delete", "del"):
        return {"action": "pfp_registry_remove", "name": flags.get("path", ""), **flags, **base}
    if subcmd == "list":
        return {"action": "pfp_registry_list", **flags, **base}
    return {"action": "pfp_error", "error": f"Unknown /pfp registry subcommand: {subcmd}", **base}


def _parse_task_command(arg: str, base: dict) -> dict:
    p = arg.split(None, 1)
    subcmd = p[0] if p else "list"
    rest = p[1] if len(p) > 1 else ""

    if subcmd == "list":
        return {"action": "list_tasks", **base}
    if subcmd in ("create", "assign", "delete", "pause", "resume", "cancel"):
        return {"action": f"task_{subcmd}", "args": rest, **base}
    return {"action": "list_tasks", **base}


def _parse_goal_command(arg: str, base: dict, agent_name: str) -> dict:
    import shlex
    try:
        tokens = shlex.split(arg or "")
    except ValueError as e:
        return {"action": "goal", "prompt": "", "error": str(e), **base}
    target = ""
    prompt_parts = []
    variables = {}
    result = {"action": "goal", **base}
    i = 0
    if tokens and tokens[0].startswith("@"):
        target = tokens[0][1:]
        i = 1
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--criteria" and i + 1 < len(tokens):
            result["criteria"] = tokens[i + 1]; i += 2; continue
        if tok == "--interval" and i + 1 < len(tokens):
            result["interval"] = tokens[i + 1]; i += 2; continue
        if tok == "--verifier" and i + 1 < len(tokens):
            v = tokens[i + 1]
            result["verifier"] = v[1:] if v.startswith("@") else v
            i += 2; continue
        if tok == "--budget" and i + 1 < len(tokens):
            result["max_budget"] = tokens[i + 1]; i += 2; continue
        if tok == "--turn-time" and i + 1 < len(tokens):
            result["max_turn_time"] = tokens[i + 1]; i += 2; continue
        if tok == "--total-time" and i + 1 < len(tokens):
            result["max_total_time"] = tokens[i + 1]; i += 2; continue
        if tok == "--max-reschedules" and i + 1 < len(tokens):
            try:
                result["max_reschedules"] = int(tokens[i + 1])
            except ValueError:
                result["max_reschedules"] = 0
            i += 2; continue
        if tok == "--max" and i + 1 < len(tokens):
            try:
                result["max_iterations"] = int(tokens[i + 1])
            except ValueError:
                result["max_iterations"] = 0
            i += 2; continue
        if tok == "--context" and i + 1 < len(tokens):
            result["context"] = tokens[i + 1]; i += 2; continue
        if tok == "--var" and i + 1 < len(tokens):
            kv = tokens[i + 1]
            if "=" in kv:
                k, v = kv.split("=", 1)
                if k:
                    variables[k] = v
            i += 2; continue
        if tok == "--auto-allow":
            result["auto_allow"] = True; i += 1; continue
        if tok == "--interactive":
            result["interactive"] = True; i += 1; continue
        prompt_parts.append(tok)
        i += 1
    result["agent_name"] = target or agent_name
    result["prompt"] = " ".join(prompt_parts).strip()
    if variables:
        result["variables"] = variables
    return result


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
        rest = p[1] if len(p) > 1 else ""
        # Parse: <datetime> [reason words...] [@agent] [--loop N]
        parts = rest.split()
        at_str = parts[0] if parts else ""
        agent = ""
        loop_seconds = 0
        reason_parts = []
        i = 1
        while i < len(parts):
            if parts[i].startswith("@"):
                agent = parts[i][1:]  # strip @
                i += 1
            elif parts[i] == "--loop" and i + 1 < len(parts):
                try:
                    loop_seconds = int(parts[i + 1])
                except ValueError:
                    pass
                i += 2
            else:
                reason_parts.append(parts[i])
                i += 1
        reason = " ".join(reason_parts) if reason_parts else "manual schedule"
        result = {"action": "add_schedule", "at": at_str, "reason": reason, **base}
        if agent:
            result["agent"] = agent
        if loop_seconds > 0:
            result["loop_seconds"] = loop_seconds
        return result
    if subcmd == "del":
        key = p[1].strip() if len(p) > 1 else ""
        return {"action": "delete_schedule", "key": key, **base}
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


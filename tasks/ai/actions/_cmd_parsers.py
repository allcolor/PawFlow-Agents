"""Per-domain /command argument parsers extracted from command_dispatch.py.

Depends downward on tasks.ai.actions._cmd_help only. The command_dispatch
facade imports these parsers and routes to them from _parse_command.
"""

import shlex
from typing import List

from tasks.ai.actions._cmd_help import _extract_at_agent, _extract_target

def _parse_agent_command(arg: str, base: dict, agent_name: str) -> dict:
    """Parse /agent subcommands. @agent convention for all agent params."""
    p = arg.split(None, 1)
    subcmd = (p[0] if p else "list").lower()
    rest = p[1] if len(p) > 1 else ""

    if subcmd == "list":
        return {"action": "list_agents", **base}
    if subcmd == "create":
        return {"display": "Agent creation is interactive. Open Resources → Agents → Create agent.", **base}
    if subcmd == "select":
        agt, _ = _extract_target(rest, "")
        return {"action": "select_agent", "agent_name": agt, "name": agt, **base}
    if subcmd == "delete":
        agt, _ = _extract_target(rest, "")
        return {"action": "delete_agent", "name": agt, **base}
    if subcmd == "msg":
        target, msg = _extract_target(rest, agent_name)
        if target.upper() == "ALL":
            return {"action": "broadcast_agents", "message": msg, **base}
        return {"action": "agent_msg", "target_agent": target, "message": msg, **base}
    if subcmd == "interrupt":
        agt, _ = _extract_target(rest, agent_name)
        return {"action": "interrupt", "agent_name": agt, **base}
    if subcmd == "btw":
        target, question = _extract_target(rest, agent_name)
        return {"action": "btw", "agent_name": target, "question": question, **base}
    if subcmd == "resume":
        agt, _ = _extract_target(rest, agent_name)
        return {"action": "resume_agent", "agent_name": agt, **base}
    if subcmd == "setname":
        real, nick = _extract_target(rest, agent_name)
        return {"action": "set_agent_nickname", "agent_name": real,
                "nickname": nick.strip(),
                **base}
    # Unknown subcommand — treat as select (supports @agent or plain name)
    agt, _ = _extract_target(arg, subcmd)
    return {"action": "select_agent", "agent_name": agt, "name": agt, **base}

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
    try:
        tokens = shlex.split(arg or "")
    except ValueError as exc:
        return {"display": f"Invalid /task arguments: {exc}", **base}
    subcmd = tokens[0].lower() if tokens else "list"
    rest = tokens[1:]

    if subcmd in ("list", "status"):
        result = {"action": "task_status", "include_library": True, **base}
        if rest:
            result["agent_name"] = rest[0].lstrip("@")
        return result

    if subcmd == "create":
        if not rest:
            return {"display": "Usage: /task create <name> \"<prompt>\" [--criteria \"...\"] [--interval XX]", **base}
        name = rest[0]
        flags, positional = _parse_named_flags(rest[1:])
        prompt = str(flags.get("prompt") or " ".join(positional)).strip()
        if not prompt:
            return {"display": "Usage: /task create <name> \"<prompt>\" [--criteria \"...\"] [--interval XX]", **base}
        return {
            "action": "create_task_def", "name": name,
            "data": {
                "prompt": prompt,
                "criteria": flags.get("criteria", ""),
                "default_interval": flags.get("interval", "6/1m"),
                "interactive": bool(flags.get("interactive", False)),
            },
            **base,
        }

    if subcmd == "assign":
        if len(rest) < 2:
            return {"display": "Usage: /task assign <agent> <task_def_name> [--var k=v] [--verifier <agent>]", **base}
        flags, _ = _parse_named_flags(rest[2:])
        result = {
            "action": "assign_task",
            "agent_name": rest[0].lstrip("@"),
            "task_def_name": rest[1],
            **base,
        }
        _apply_task_flags(result, flags)
        return result

    if subcmd in ("delete", "del"):
        target = rest[0].lstrip("@") if rest else ""
        if not target:
            return {"display": "Usage: /task delete <task_def_name|task_id>", **base}
        if target.startswith("t_"):
            return {"action": "delete_task", "task_id": target, **base}
        return {"action": "delete_task_def", "name": target, **base}

    if subcmd in ("pause", "resume", "cancel"):
        target = rest[0].lstrip("@") if rest else ""
        if not target:
            return {"display": f"Usage: /task {subcmd} <task_id|agent>", **base}
        return {
            "action": f"{subcmd}_task",
            "task_id": target if target.startswith("t_") else "",
            "agent_name": "" if target.startswith("t_") else target,
            **base,
        }

    if subcmd in ("edit", "set"):
        target = rest[0] if rest else ""
        if not target:
            return {"display": "Usage: /task edit <task_id> [--budget X] [--interval X] [--max N]", **base}
        flags, _ = _parse_named_flags(rest[1:])
        result = {"action": "edit_task", "task_id": target, **base}
        _apply_task_flags(result, flags)
        return result

    return {"display": "Usage: /task create | assign | list | edit | delete | pause | resume | cancel", **base}


def _parse_named_flags(tokens: List[str]) -> tuple[dict, List[str]]:
    """Parse ``--name value``, boolean flags and repeated ``--var k=v``."""
    flags: dict = {}
    positional: List[str] = []
    variables = {}
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if not token.startswith("--"):
            positional.append(token)
            i += 1
            continue
        key = token[2:].replace("-", "_")
        if key in ("interactive", "auto_allow"):
            flags[key] = True
            i += 1
            continue
        value = tokens[i + 1] if i + 1 < len(tokens) else ""
        if key == "var":
            name, sep, variable_value = value.partition("=")
            if sep and name:
                variables[name] = variable_value
        else:
            flags[key] = value
        i += 2
    if variables:
        flags["variables"] = variables
    return flags, positional


def _apply_task_flags(result: dict, flags: dict) -> None:
    aliases = {
        "criteria": "completion_criteria",
        "budget": "max_budget",
        "turn_time": "max_turn_time",
        "total_time": "max_total_time",
        "max": "max_iterations",
    }
    integer_fields = {"max_iterations", "max_reschedules"}
    for key, value in flags.items():
        target = aliases.get(key, key)
        if target in integer_fields:
            try:
                value = int(value)
            except (TypeError, ValueError):
                value = 0
        if target == "verifier":
            value = str(value).lstrip("@")
        result[target] = value


def _parse_service_command(arg: str, base: dict, user_id: str) -> dict:
    try:
        tokens = shlex.split(arg or "")
    except ValueError as exc:
        return {"display": f"Invalid /service arguments: {exc}", **base}
    subcmd = tokens[0].lower() if tokens else "list"
    rest = tokens[1:]
    if subcmd == "list":
        return {"action": "list_services", **base}
    if subcmd in ("install", "add"):
        if len(rest) < 2:
            return {"display": "Usage: /service install <type> <name> [config]", **base}
        return {
            "action": "service_install", "service_type": rest[0],
            "service_name": rest[1], "config_str": " ".join(rest[2:]),
            **base,
        }
    if subcmd in ("uninstall", "delete", "del"):
        if not rest:
            return {"display": f"Usage: /service {subcmd} <service_id>", **base}
        return {"action": "service_uninstall", "service_id": rest[0], **base}
    if subcmd in ("enable", "disable"):
        if not rest:
            return {"display": f"Usage: /service {subcmd} <service_id>", **base}
        return {"action": f"service_{subcmd}", "service_id": rest[0], **base}
    if subcmd in ("show", "detail", "test"):
        if not rest:
            return {"display": f"Usage: /service {subcmd} <service_id>", **base}
        return {"action": "get_service_detail", "service_id": rest[0], **base}
    return {"display": "Usage: /service list | install | uninstall | enable | disable | detail", **base}


def _parse_flow_command(arg: str, base: dict) -> dict:
    try:
        tokens = shlex.split(arg or "")
    except ValueError as exc:
        return {"display": f"Invalid /flow arguments: {exc}", **base}
    subcmd = tokens[0].lower() if tokens else "list"
    rest = tokens[1:]
    if subcmd == "list":
        return {"action": "list_conv_flows", **base}
    if subcmd == "templates":
        return {"action": "list_available_flows", **base}
    if subcmd == "deploy":
        if not rest:
            return {"display": "Usage: /flow deploy <template_id> [user|conversation]", **base}
        return {"action": "deploy_flow", "template_id": rest[0],
                "scope": rest[1] if len(rest) > 1 else "user", **base}
    if subcmd in ("start", "stop", "undeploy", "params", "promote"):
        if not rest:
            return {"display": f"Usage: /flow {subcmd} <instance_id>", **base}
        action = {
            "start": "start_flow", "stop": "stop_flow",
            "undeploy": "undeploy_flow", "params": "get_flow_instance",
            "promote": "promote_flow",
        }[subcmd]
        result = {"action": action, "instance_id": rest[0], **base}
        if subcmd == "start":
            parameters = {}
            for item in rest[1:]:
                name, sep, value = item.partition("=")
                if sep and name:
                    parameters[name] = value
            if parameters:
                result["parameters"] = parameters
        if subcmd == "promote":
            result["target_scope"] = rest[1] if len(rest) > 1 else "user"
        return result
    return {"display": "Usage: /flow list | templates | deploy | start | stop | params | undeploy | promote", **base}


def _parse_memory_command(arg: str, base: dict, agent_name: str) -> dict:
    p = arg.split(None, 2)
    subcmd = p[0] if p else "list"
    if subcmd == "list":
        return {"action": "list_memories",
                "agent_name": p[1].lstrip("@") if len(p) > 1 else agent_name, **base}
    if subcmd == "add":
        return {"action": "add_memory", "text": " ".join(p[1:]) if len(p) > 1 else "",
                "agent": agent_name, **base}
    if subcmd == "search":
        return {"action": "search_memories", "query": " ".join(p[1:]) if len(p) > 1 else "",
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
    # Accept both documented forms:
    #   /autoconv <agent> on [min] [max]
    #   /autoconv on @<agent> [frequency]
    if p and p[0].lower() in ("on", "off", "status", "now", "enable", "disable"):
        subcmd = p[0].lower()
        agt = p[1].lstrip("@") if len(p) > 1 else agent_name
        rest = p[2:]
    else:
        agt = p[0].lstrip("@") if p else agent_name
        subcmd = p[1].lower() if len(p) > 1 else "status"
        rest = p[2:]
    subcmd = {"enable": "on", "disable": "off"}.get(subcmd, subcmd)
    result = {"action": "random_thought", "sub": subcmd,
              "agent": agt, **base}
    if subcmd == "on":
        if rest and "/" in rest[0]:
            result["frequency"] = rest[0]
        elif rest:
            try:
                minimum = int(rest[0])
                maximum = int(rest[1]) if len(rest) > 1 else minimum * 4
            except ValueError:
                return {"display": "Intervals must be seconds or a frequency such as 6/1m.", **base}
            result["frequency"] = f"{minimum}-{maximum}s"
        else:
            result["frequency"] = "6/1m"
    return result


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
        return {"action": "get_conversation_hooks", **base}
    if subcmd == "add":
        return {"display": "Hooks are signed agent-hook resources. Install or create one in Resources, then bind it to this conversation.", **base}
    if subcmd == "del":
        return {"display": "Remove hook bindings from the Resources panel; hook resources are managed by scope.", **base}
    return {"display": "Usage: /hooks list", **base}


# ── Main handler ──────────────────────────────────────────────────────


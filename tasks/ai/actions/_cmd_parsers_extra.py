"""Small parsers for slash commands with structured argument syntax."""

from __future__ import annotations

import ast
import json
import re
import shlex


def _parse_tool_call_command(arg: str, base: dict) -> dict:
    """Parse ``/call name(...)``, ``name {json}``, or positional syntax."""
    text = (arg or "").strip()
    match = re.match(r"^([A-Za-z_][A-Za-z0-9_.-]*)([\s\S]*)$", text)
    if not match:
        return {"display": "Usage: /call tool_name(key=value, ...) or /call tool_name {\"key\": \"value\"}", **base}
    name, remainder = match.group(1), match.group(2).strip()
    arguments = {}
    positional = []
    try:
        if not remainder:
            pass
        elif remainder.startswith("{"):
            arguments = json.loads(remainder)
            if not isinstance(arguments, dict):
                raise ValueError("JSON arguments must be an object")
        elif remainder.startswith("(") and remainder.endswith(")"):
            expression = ast.parse(f"_tool{remainder}", mode="eval").body
            if not isinstance(expression, ast.Call):
                raise ValueError("invalid call syntax")
            positional = [ast.literal_eval(value) for value in expression.args]
            for keyword in expression.keywords:
                if keyword.arg is None:
                    raise ValueError("**kwargs are not supported")
                arguments[keyword.arg] = ast.literal_eval(keyword.value)
        else:
            positional = shlex.split(remainder)
    except (ValueError, SyntaxError, json.JSONDecodeError) as exc:
        return {"display": f"Invalid /call arguments: {exc}", **base}
    return {
        "action": "call_tool", "tool_name": name,
        "arguments": arguments, "positional_args": positional, **base,
    }


def _parse_loop_command(arg: str, base: dict) -> dict:
    """Parse recurring prompt commands into the existing loop actions."""
    try:
        tokens = shlex.split(arg or "")
    except ValueError as exc:
        return {"display": f"Invalid /loop arguments: {exc}", **base}
    if not tokens or tokens[0].lower() == "list":
        return {"action": "loop_list", **base}
    if tokens[0].lower() == "stop":
        if len(tokens) < 2:
            return {"display": "Usage: /loop stop <key>", **base}
        return {"action": "loop_stop", "key": tokens[1], **base}

    interval = _interval_seconds(tokens[0])
    if interval < 5:
        return {"display": "Usage: /loop <interval: 5s|10m|2h> <prompt or /command>", **base}
    prompt = " ".join(tokens[1:]).strip()
    if not prompt:
        return {"display": "Usage: /loop <interval> <prompt or /command>", **base}
    return {
        "action": "loop_start", "interval_seconds": interval,
        "prompt": prompt, **base,
    }


def _parse_encrypt_command(arg: str, base: dict) -> dict:
    """Parse conversation encryption commands without client-side prompts."""
    try:
        tokens = shlex.split(arg or "")
    except ValueError as exc:
        return {"display": f"Invalid /encrypt arguments: {exc}", **base}
    subcmd = tokens[0].lower() if tokens else "status"
    rest = tokens[1:]
    aliases = {"on": "enable", "off": "disable"}
    subcmd = aliases.get(subcmd, subcmd)
    action = {
        "status": "conv_encrypt_status",
        "enable": "conv_encrypt_enable",
        "disable": "conv_encrypt_disable",
        "unlock": "conv_encrypt_unlock",
        "lock": "conv_encrypt_lock",
        "passwd": "conv_encrypt_passwd",
        "set-relay": "conv_encrypt_set_relay",
        "remove-relay": "conv_encrypt_remove_relay",
        "set-escrow": "conv_encrypt_set_escrow",
        "remove-escrow": "conv_encrypt_remove_escrow",
        "recover": "conv_encrypt_recover",
    }.get(subcmd)
    if not action:
        return {"display": "Usage: /encrypt status | on | off | unlock | lock | passwd", **base}
    result = {"action": action, **base}
    if subcmd in ("enable", "unlock"):
        result["passphrase"] = rest[0] if rest else ""
    elif subcmd == "passwd":
        result["old_passphrase"] = rest[0] if rest else ""
        result["new_passphrase"] = rest[1] if len(rest) > 1 else ""
    elif subcmd == "set-relay":
        result["relay_pubkey"] = rest[0] if rest else ""
    elif subcmd in ("set-escrow", "recover"):
        result["recovery_passphrase"] = rest[0] if rest else ""
    return result


def _parse_goal_command(arg: str, base: dict, agent_name: str) -> dict:
    """Parse a goal prompt, target, verification policy, and resource limits."""
    try:
        tokens = shlex.split(arg or "")
    except ValueError as exc:
        return {"action": "goal", "prompt": "", "error": str(exc), **base}
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
            result["criteria"] = tokens[i + 1]
            i += 2
            continue
        if tok == "--interval" and i + 1 < len(tokens):
            result["interval"] = tokens[i + 1]
            i += 2
            continue
        if tok == "--verifier" and i + 1 < len(tokens):
            verifier = tokens[i + 1]
            result["verifier"] = verifier[1:] if verifier.startswith("@") else verifier
            i += 2
            continue
        if tok == "--budget" and i + 1 < len(tokens):
            result["max_budget"] = tokens[i + 1]
            i += 2
            continue
        if tok == "--turn-time" and i + 1 < len(tokens):
            result["max_turn_time"] = tokens[i + 1]
            i += 2
            continue
        if tok == "--total-time" and i + 1 < len(tokens):
            result["max_total_time"] = tokens[i + 1]
            i += 2
            continue
        if tok == "--max-reschedules" and i + 1 < len(tokens):
            try:
                result["max_reschedules"] = int(tokens[i + 1])
            except ValueError:
                result["max_reschedules"] = 0
            i += 2
            continue
        if tok == "--max" and i + 1 < len(tokens):
            try:
                result["max_iterations"] = int(tokens[i + 1])
            except ValueError:
                result["max_iterations"] = 0
            i += 2
            continue
        if tok == "--context" and i + 1 < len(tokens):
            result["context"] = tokens[i + 1]
            i += 2
            continue
        if tok == "--var" and i + 1 < len(tokens):
            key_value = tokens[i + 1]
            if "=" in key_value:
                key, value = key_value.split("=", 1)
                if key:
                    variables[key] = value
            i += 2
            continue
        if tok == "--auto-allow":
            result["auto_allow"] = True
            i += 1
            continue
        if tok == "--interactive":
            result["interactive"] = True
            i += 1
            continue
        prompt_parts.append(tok)
        i += 1
    result["agent_name"] = target or agent_name
    result["prompt"] = " ".join(prompt_parts).strip()
    if variables:
        result["variables"] = variables
    return result


def _interval_seconds(spec: str) -> int:
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    simple = re.fullmatch(r"(\d+)([smhd])", spec.lower())
    if simple:
        return int(simple.group(1)) * units[simple.group(2)]
    frequency = re.fullmatch(r"(\d+)(?:-(\d+))?/(\d*)([smhd])", spec.lower())
    if frequency:
        count = int(frequency.group(1))
        duration = int(frequency.group(3) or "1") * units[frequency.group(4)]
        return duration // count if count else 0
    return 0

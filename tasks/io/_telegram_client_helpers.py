"""Telegram agent client tasks.

These tasks make Telegram a transport for the shared agent runtime instead of
running a separate Telegram-only AgentLoopTask.
"""

from __future__ import annotations

import logging
import json
import re
import shlex
import threading
import time
from typing import Any, Dict, List, Optional

from core import FlowFile

logger = logging.getLogger(__name__)
# Split out of telegram_agent_client.py for the <=800-line rule; re-exported
# from tasks.io.telegram_agent_client (invariant 1: import-path stability).

_WIZARD_TTL_SECONDS = 900
_WIZARDS: Dict[str, Dict[str, Any]] = {}
_WIZARD_LOCK = threading.Lock()


def _parse_new_conversation_args(args: str) -> Dict[str, Any]:
    parts = shlex.split(args or "")
    opts = {"agent": "", "llm": "", "title": "", "relays": []}
    title_parts = []
    i = 0
    while i < len(parts):
        part = parts[i]
        if part in ("--agent", "-a") and i + 1 < len(parts):
            opts["agent"] = parts[i + 1].lstrip("@")
            i += 2
            continue
        if part in ("--llm", "--service") and i + 1 < len(parts):
            opts["llm"] = parts[i + 1]
            i += 2
            continue
        if part == "--relay" and i + 1 < len(parts):
            opts["relays"].append(parts[i + 1])
            i += 2
            continue
        if part == "--title" and i + 1 < len(parts):
            title_words = []
            i += 1
            while i < len(parts) and not parts[i].startswith("--"):
                title_words.append(parts[i])
                i += 1
            opts["title"] = " ".join(title_words).strip()
            continue
        if not opts["agent"] and not part.startswith("-"):
            opts["agent"] = part.lstrip("@")
        else:
            title_parts.append(part)
        i += 1
    if not opts["title"] and title_parts:
        opts["title"] = " ".join(title_parts).strip()
    return opts


def _guess_llm_service(agent_name: str, services: List[Any]) -> str:
    names = [getattr(s, "service_id", "") for s in services]
    for suffix in ("_llm_service", "_llm"):
        candidate = f"{agent_name}{suffix}"
        if candidate in names:
            return candidate
    return names[0] if names else ""


def _validate_relays(relay_ids: List[str], user_id: str = "") -> List[str]:
    try:
        from core.relay_bindings import list_available_relays
        available = {
            str(r.get("relay_id") or "")
            for r in list_available_relays(user_id=user_id)
            if r.get("connected", True)
        }
    except Exception:
        logger.debug("Failed to list connected relays", exc_info=True)
        available = set()
    invalid = [relay_id for relay_id in relay_ids if relay_id not in available]
    if invalid:
        raise ValueError(f"Relay not found or disconnected: {', '.join(invalid)}")
    return list(relay_ids)


def _wizard_key(user_id: str, chat_id: str) -> str:
    return f"{user_id}:{chat_id}"


def _get_wizard(key: str) -> Optional[Dict[str, Any]]:
    now = time.time()
    with _WIZARD_LOCK:
        state = _WIZARDS.get(key)
        if not state:
            return None
        if now - float(state.get("updated_at", 0) or 0) > _WIZARD_TTL_SECONDS:
            _WIZARDS.pop(key, None)
            return None
        return dict(state)


def _save_wizard(key: str, state: Dict[str, Any]) -> None:
    state = dict(state)
    state["updated_at"] = time.time()
    with _WIZARD_LOCK:
        _WIZARDS[key] = state


def _clear_wizard(key: str) -> None:
    with _WIZARD_LOCK:
        _WIZARDS.pop(key, None)


def _telegram_response(text: str, reply_markup: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"text": text, "reply_markup": reply_markup or {}}


def _telegram_command_name(text: str) -> str:
    command = str(text or "").strip().split(None, 1)[0].lower()
    if "@" in command:
        command = command.split("@", 1)[0]
    return command


def _normalize_telegram_command_text(text: str) -> str:
    stripped = str(text or "").strip()
    if not stripped:
        return ""
    parts = stripped.split(None, 1)
    command = _telegram_command_name(stripped)
    return command + ((" " + parts[1]) if len(parts) > 1 else "")


def _apply_telegram_response(flowfile: FlowFile, response: Any) -> None:
    if isinstance(response, dict):
        flowfile.set_content(str(response.get("text") or "").encode("utf-8"))
        markup = response.get("reply_markup")
        if markup:
            flowfile.set_attribute("telegram.reply_markup", json.dumps(markup))
        return
    flowfile.set_content(str(response).encode("utf-8"))


def _format_telegram_command_result(raw: str) -> str:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if not isinstance(payload, dict):
        return str(payload)
    if payload.get("help"):
        return _telegram_markdown_help(str(payload["help"]))
    if payload.get("error"):
        text = f"Error: {payload['error']}"
        if payload.get("hint"):
            text += f"\n{payload['hint']}"
        return text
    if payload.get("output") is not None:
        return str(payload["output"])
    if payload.get("message") is not None:
        return str(payload["message"])
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _telegram_markdown_help(text: str) -> str:
    lines = []
    for line in str(text or "").splitlines():
        if line.startswith("## "):
            lines.append(f"*{line[3:].strip()}*")
        else:
            lines.append(re.sub(r"\*\*([^*]+)\*\*", r"*\1*", line))
    return "\n".join(lines).strip()


def _inline_keyboard(rows: List[List[Dict[str, str]]]) -> Dict[str, Any]:
    return {"inline_keyboard": rows}


def _button(text: str, callback_data: str) -> Dict[str, str]:
    return {"text": text[:64], "callback_data": callback_data[:64]}


def _start_new_conversation_wizard(user_id: str, chat_id: str) -> Dict[str, Any]:
    key = _wizard_key(user_id, chat_id)
    _save_wizard(key, {
        "mode": "new",
        "step": "title",
        "title": "",
        "agents": [],
        "relays": [],
        "default_relay": "",
    })
    return _telegram_response(
        "New conversation\n\nSend the conversation title.",
        _inline_keyboard([[_button("Cancel", "conv:new:cancel")]]),
    )


def _handle_resume_callback(callback_data: str, user_id: str) -> Dict[str, Any]:
    from core.conversation_store import ConversationStore
    from core.identity_service import IdentityService
    conv_id = callback_data.split(":", 2)[2]
    convs = ConversationStore.instance().list_conversations(user_id=user_id)
    match = next((c.get("conversation_id", "") for c in convs
                  if c.get("conversation_id") == conv_id), "")
    if not match:
        return _telegram_response("Conversation not found. Use /conv list.")
    IdentityService.instance().set_active_conv(user_id, "telegram", match)
    return _telegram_response(f"Selected conversation: {match}")


def _conversation_keyboard(convs: List[Dict[str, Any]]) -> Dict[str, Any]:
    rows = []
    for conv in convs[:10]:
        cid = str(conv.get("conversation_id") or "")
        title = str(conv.get("title") or conv.get("preview") or cid)[:40]
        rows.append([_button(title, f"conv:resume:{cid}")])
    rows.append([_button("New conversation", "conv:new:start")])
    return _inline_keyboard(rows)


def _handle_new_conversation_callback(
    callback_data: str, user_id: str, chat_id: str,
) -> Dict[str, Any]:
    key = _wizard_key(user_id, chat_id)
    if callback_data == "conv:new:cancel":
        _clear_wizard(key)
        return _telegram_response("Conversation creation cancelled.")
    state = _get_wizard(key)
    if not state:
        return _start_new_conversation_wizard(user_id, chat_id)

    parts = callback_data.split(":")
    action = parts[2] if len(parts) > 2 else ""
    value = parts[3] if len(parts) > 3 else ""
    if action == "agent":
        agents = _available_agents(user_id)
        idx = int(value) if value.isdigit() else -1
        if idx < 0 or idx >= len(agents):
            return _choose_agent_definition(user_id)
        definition = str(agents[idx].get("name") or "")
        state["pending_definition"] = definition
        state["pending_instance_name"] = _next_agent_instance_name(state, definition)
        state["step"] = "agent_name"
        _save_wizard(key, state)
        return _telegram_response(
            f"Agent definition: {definition}\n\nSend the instance name, or use the default.",
            _inline_keyboard([
                [_button(f"Use {state['pending_instance_name']}", "conv:new:name_default")],
                [_button("Cancel", "conv:new:cancel")],
            ]),
        )
    if action == "name_default":
        state["step"] = "llm"
        _save_wizard(key, state)
        return _choose_llm_service(user_id)
    if action == "llm":
        services = _available_llm_services(user_id)
        idx = int(value) if value.isdigit() else -1
        if idx < 0 or idx >= len(services):
            return _choose_llm_service(user_id)
        definition = state.get("pending_definition", "")
        instance_name = state.get("pending_instance_name", "")
        agent_def = _agent_definition(user_id, definition)
        state.setdefault("agents", []).append({
            "instance_name": instance_name,
            "definition": definition,
            "llm_service": getattr(services[idx], "service_id", ""),
            "params": {"name": instance_name},
            "model": str(agent_def.get("model") or ""),
            "tools": agent_def.get("tools") or [],
            "max_depth": int(agent_def.get("max_depth", 1000) or 1000),
            "skills": agent_def.get("skills") or [],
        })
        state.pop("pending_definition", None)
        state.pop("pending_instance_name", None)
        state["step"] = "summary"
        _save_wizard(key, state)
        return _new_wizard_summary(state)
    if action == "add_agent":
        state["step"] = "agent_def"
        _save_wizard(key, state)
        return _choose_agent_definition(user_id)
    if action == "relays":
        state["step"] = "relays"
        _save_wizard(key, state)
        return _choose_relays(user_id, state)
    if action == "relay":
        relays = _available_relays(user_id)
        idx = int(value) if value.isdigit() else -1
        if 0 <= idx < len(relays):
            relay_id = str(relays[idx].get("relay_id") or "")
            selected = list(state.get("relays") or [])
            if relay_id in selected:
                selected.remove(relay_id)
            else:
                selected.append(relay_id)
            state["relays"] = selected
            if state.get("default_relay") not in selected:
                state["default_relay"] = selected[0] if selected else ""
            _save_wizard(key, state)
        return _choose_relays(user_id, state)
    if action == "relays_done":
        if not state.get("relays"):
            return _choose_relays(user_id, state, "Select at least one relay.")
        state["step"] = "summary"
        _save_wizard(key, state)
        return _new_wizard_summary(state)
    if action == "default":
        relays = list(state.get("relays") or [])
        idx = int(value) if value.isdigit() else -1
        if 0 <= idx < len(relays):
            state["default_relay"] = relays[idx]
            _save_wizard(key, state)
        return _new_wizard_summary(state)
    if action == "create":
        return _create_from_wizard(user_id, key, state)
    return _new_wizard_summary(state)


def _available_agents(user_id: str) -> List[Dict[str, Any]]:
    from core.resource_store import ResourceStore
    return ResourceStore.instance().list_all("agent", user_id)


def _agent_definition(user_id: str, name: str) -> Dict[str, Any]:
    from core.resource_store import ResourceStore
    return ResourceStore.instance().get_any("agent", name, user_id) or {}


def _available_llm_services(user_id: str) -> List[Any]:
    from core.service_registry import ServiceRegistry
    return ServiceRegistry.get_instance().resolve_by_type(
        "llmConnection", user_id=user_id)


def _available_relays(user_id: str) -> List[Dict[str, Any]]:
    from core.relay_bindings import list_available_relays
    return [r for r in list_available_relays(user_id=user_id)
            if r.get("connected", True)]


def _choose_agent_definition(user_id: str) -> Dict[str, Any]:
    agents = _available_agents(user_id)
    if not agents:
        return _telegram_response("No agent definitions are available.")
    rows = [[_button(str(a.get("name") or "agent")[:40], f"conv:new:agent:{i}")]
            for i, a in enumerate(agents[:20])]
    rows.append([_button("Cancel", "conv:new:cancel")])
    return _telegram_response("Choose an agent definition:", _inline_keyboard(rows))


def _choose_llm_service(user_id: str) -> Dict[str, Any]:
    services = _available_llm_services(user_id)
    if not services:
        return _telegram_response("No enabled LLM service is available.")
    rows = [[_button(getattr(s, "service_id", "llm")[:40], f"conv:new:llm:{i}")]
            for i, s in enumerate(services[:20])]
    rows.append([_button("Cancel", "conv:new:cancel")])
    return _telegram_response("Choose the LLM service for this agent:", _inline_keyboard(rows))


def _choose_relays(user_id: str, state: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
    relays = _available_relays(user_id)
    if not relays:
        return _telegram_response("No connected relay is available.")
    selected = set(state.get("relays") or [])
    rows = []
    for i, relay in enumerate(relays[:20]):
        relay_id = str(relay.get("relay_id") or "")
        mark = "[x]" if relay_id in selected else "[ ]"
        rows.append([_button(f"{mark} {relay_id}", f"conv:new:relay:{i}")])
    rows.append([_button("Done", "conv:new:relays_done"), _button("Cancel", "conv:new:cancel")])
    text = f"{prefix}\n\n" if prefix else ""
    text += "Select one or more relays:"
    return _telegram_response(text, _inline_keyboard(rows))


def _new_wizard_summary(state: Dict[str, Any]) -> Dict[str, Any]:
    lines = [f"Title: {state.get('title') or '(missing)'}", "", "Agents:"]
    for agent in state.get("agents") or []:
        lines.append(
            f"- {agent.get('instance_name')} ({agent.get('definition')}) via {agent.get('llm_service')}")
    if not state.get("agents"):
        lines.append("- none")
    lines.extend(["", f"Relays: {', '.join(state.get('relays') or []) or '(none)'}"])
    lines.append(f"Default relay: {state.get('default_relay') or '(none)'}")
    relay_rows = []
    relays = list(state.get("relays") or [])
    if len(relays) > 1:
        relay_rows = [[_button(f"Default: {rid}", f"conv:new:default:{i}")]
                      for i, rid in enumerate(relays)]
    rows = [
        [_button("Add agent", "conv:new:add_agent"), _button("Relays", "conv:new:relays")],
        *relay_rows,
        [_button("Create", "conv:new:create"), _button("Cancel", "conv:new:cancel")],
    ]
    return _telegram_response("\n".join(lines), _inline_keyboard(rows))


def _create_from_wizard(user_id: str, key: str, state: Dict[str, Any]) -> Dict[str, Any]:
    if not state.get("title"):
        return _telegram_response("Conversation title is required.")
    if not state.get("agents"):
        state["step"] = "agent_def"
        _save_wizard(key, state)
        return _choose_agent_definition(user_id)
    names = [str(a.get("instance_name") or "") for a in state.get("agents") or []]
    if len(names) != len(set(names)):
        return _telegram_response("Agent instance names must be unique.")
    if not state.get("relays"):
        state["step"] = "relays"
        _save_wizard(key, state)
        return _choose_relays(user_id, state, "At least one relay is required.")
    from core.conversation_creation import create_conversation
    from core.identity_service import IdentityService
    result = create_conversation(user_id, {
        "title": state["title"],
        "agents": state["agents"],
        "relays": state["relays"],
        "default_relay": state.get("default_relay") or state["relays"][0],
    })
    conv_id = result["conversation_id"]
    IdentityService.instance().set_active_conv(user_id, "telegram", conv_id)
    _clear_wizard(key)
    return _telegram_response(f"Created and selected conversation: {conv_id}")


def _next_agent_instance_name(state: Dict[str, Any], definition: str) -> str:
    used = {str(a.get("instance_name") or "") for a in state.get("agents") or []}
    base = _clean_instance_name(definition) or "agent"
    if base not in used:
        return base
    i = 2
    while f"{base}_{i}" in used:
        i += 1
    return f"{base}_{i}"


def _clean_instance_name(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in value.strip())
    return cleaned.strip("_")[:64]

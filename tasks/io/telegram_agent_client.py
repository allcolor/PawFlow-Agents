"""Telegram agent client tasks.

These tasks make Telegram a transport for the shared agent runtime instead of
running a separate Telegram-only AgentLoopTask.
"""

from __future__ import annotations

import logging
import json
import shlex
import threading
import time
from typing import Any, Dict, List, Optional

from core import FlowFile, TaskFactory
from core.base_task import BaseTask
from core.agent_runtime_api import AgentRequest, AgentRuntimeAPI

logger = logging.getLogger(__name__)

_WIZARD_TTL_SECONDS = 900
_WIZARDS: Dict[str, Dict[str, Any]] = {}
_WIZARD_LOCK = threading.Lock()


class TelegramAgentClientTask(BaseTask):
    """Submit a Telegram message to the shared agent runtime and wait for done."""

    TYPE = "telegramAgentClient"
    VERSION = "1.0.0"
    NAME = "Telegram Agent Client"
    DESCRIPTION = "Submit Telegram messages through the shared agent API"
    ICON = "telegram"
    TAGS = ["telegram", "agent", "client"]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "target_agent": {
                "type": "string", "required": False, "default": "",
                "description": "Agent instance to target. Empty uses the active conversation agent.",
            },
            "timeout": {
                "type": "number", "required": False, "default": 600,
                "description": "Seconds to wait for the final agent response.",
            },
            "agent_runtime_port": {
                "type": "string", "required": False,
                "default": "pawflow_agent.agent_runtime_in",
                "description": "Visible target runtime port for the shared AgentLoop runtime.",
            },
            "create_conversation": {
                "type": "boolean", "required": False, "default": False,
                "description": "Deprecated. Telegram requires an explicit /conv select resume.",
            },
        }

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        tg_user_id = flowfile.get_attribute("telegram.user_id") or ""
        chat_id = flowfile.get_attribute("telegram.chat_id") or ""
        if not tg_user_id or not chat_id:
            flowfile.set_content(b"Telegram user and chat attributes are required.")
            return [flowfile]

        from core.identity_service import IdentityService
        ids = IdentityService.instance()
        user_id = ids.resolve_user("telegram", tg_user_id)
        if not user_id:
            flowfile.set_content(
                b"Access denied. Link your Telegram account from PawFlow first."
            )
            return [flowfile]

        text = flowfile.get_content().decode("utf-8", errors="replace").strip()
        callback_data = flowfile.get_attribute("telegram.callback_data") or ""
        wizard_response = self._handle_wizard_input(
            text, callback_data, user_id, chat_id)
        if wizard_response is not None:
            _apply_telegram_response(flowfile, wizard_response)
            return [flowfile]

        command_response = self._handle_command(text, user_id, chat_id)
        if command_response is not None:
            _apply_telegram_response(flowfile, command_response)
            return [flowfile]

        configured_target = str(self.config.get("target_agent") or "").strip()
        conversation_id = ids.get_active_conv(user_id, "telegram") or ""
        if not conversation_id:
            flowfile.set_content(
                b"No resumed conversation. Use /conv list then /conv select <id>."
            )
            return [flowfile]

        target_agent = configured_target or self._selected_agent_for_conversation(
            conversation_id)
        if not target_agent:
            flowfile.set_content(
                b"No selected agent for this conversation. Select an agent before sending a message."
            )
            return [flowfile]

        msg_id = f"telegram:{chat_id}:{flowfile.get_attribute('telegram.message_id') or ''}"
        request = AgentRequest(
            user_id=user_id,
            conversation_id=conversation_id,
            target_agent=target_agent,
            message=text,
            msg_id=msg_id,
            channel="telegram",
            runtime_port=str(self.config.get("agent_runtime_port") or "").strip(),
            source_attributes={
                "telegram.chat_id": chat_id,
                "telegram.user_id": tg_user_id,
                "telegram.message_id": flowfile.get_attribute("telegram.message_id") or "",
            },
        )
        try:
            submission = AgentRuntimeAPI.submit_message(request)
            timeout = float(self.config.get("timeout", 600) or 600)
            result = AgentRuntimeAPI.wait_for_done(
                submission.conversation_id, submission.turn_id, timeout=timeout)
        except Exception as exc:
            logger.warning("Telegram agent submit failed: %s", exc, exc_info=True)
            runtime_port = str(self.config.get("agent_runtime_port") or "").strip()
            suffix = f" (runtime port: {runtime_port})" if runtime_port else ""
            flowfile.set_content(f"Agent request failed{suffix}: {exc}".encode("utf-8"))
            return [flowfile]

        if result is None:
            flowfile.set_content(b"Agent response timed out.")
            return [flowfile]
        if result.error:
            flowfile.set_content(f"Agent error: {result.error}".encode("utf-8"))
            return [flowfile]
        flowfile.set_content((result.response or "").encode("utf-8"))
        flowfile.set_attribute("agent.conversation_id", submission.conversation_id)
        flowfile.set_attribute("agent.turn_id", submission.turn_id)
        return [flowfile]

    def _handle_command(self, text: str, user_id: str, chat_id: str) -> Optional[str]:
        if not text.startswith("/conv"):
            return None
        from core.identity_service import IdentityService
        from core.conversation_store import ConversationStore
        ids = IdentityService.instance()
        store = ConversationStore.instance()
        parts = text.split(maxsplit=2)
        sub = parts[1] if len(parts) > 1 else "current"
        if sub == "current":
            active = ids.get_active_conv(user_id, "telegram") or ""
            return f"Active conversation: {active or '(none)'}"
        if sub == "new":
            if len(parts) <= 2:
                return _start_new_conversation_wizard(user_id, chat_id)
            try:
                cid, agent_name = self._create_conversation_from_command(
                    parts[2] if len(parts) > 2 else "", user_id)
            except ValueError as exc:
                return str(exc)
            ids.set_active_conv(user_id, "telegram", cid)
            return f"Created and selected conversation: {cid}\nAgent: {agent_name}"
        if sub == "list":
            convs = store.list_conversations(user_id=user_id)[:10]
            if not convs:
                return _telegram_response(
                    "No conversations yet.",
                    _inline_keyboard([[{"text": "New conversation", "callback_data": "conv:new:start"}]])
                )
            lines = ["Conversations:"]
            for idx, conv in enumerate(convs, 1):
                title = conv.get("title") or conv.get("preview") or conv["conversation_id"]
                lines.append(f"{idx}. {title[:60]} — {conv['conversation_id']}")
            return _telegram_response("\n".join(lines), _conversation_keyboard(convs))
        if sub == "select" and len(parts) <= 2:
            convs = store.list_conversations(user_id=user_id)[:10]
            if not convs:
                return _telegram_response(
                    "No conversations yet.",
                    _inline_keyboard([[{"text": "New conversation", "callback_data": "conv:new:start"}]])
                )
            return _telegram_response("Select a conversation to resume:", _conversation_keyboard(convs))
        if sub == "select" and len(parts) > 2:
            wanted = parts[2]
            convs = store.list_conversations(user_id=user_id)
            selected = ""
            if wanted.isdigit():
                idx = int(wanted) - 1
                if 0 <= idx < min(len(convs), 10):
                    selected = convs[idx]["conversation_id"]
            else:
                for conv in convs:
                    cid = conv["conversation_id"]
                    if cid == wanted or cid.startswith(wanted):
                        selected = cid
                        break
            if not selected:
                return "Conversation not found. Use /conv list."
            ids.set_active_conv(user_id, "telegram", selected)
            return f"Selected conversation: {selected}"
        return "Usage: /conv current | /conv list | /conv select <n|id> | /conv new <agent> --title <title> --relay <relay_id> [--llm <service>]"

    def _handle_wizard_input(
        self, text: str, callback_data: str, user_id: str, chat_id: str,
    ) -> Optional[Any]:
        key = _wizard_key(user_id, chat_id)
        if callback_data.startswith("conv:resume:"):
            return _handle_resume_callback(callback_data, user_id)
        if callback_data == "conv:new:start":
            return _start_new_conversation_wizard(user_id, chat_id)
        if callback_data.startswith("conv:new:"):
            return _handle_new_conversation_callback(callback_data, user_id, chat_id)
        state = _get_wizard(key)
        if not state or state.get("mode") != "new":
            return None
        if text.startswith("/"):
            return None
        step = state.get("step")
        if step == "title":
            if not text:
                return "Send a title for the conversation."
            state["title"] = text[:120]
            state["step"] = "agent_def"
            _save_wizard(key, state)
            return _choose_agent_definition(user_id)
        if step == "agent_name":
            name = _clean_instance_name(text)
            if not name:
                return "Send a valid agent instance name."
            used = {str(a.get("instance_name") or "") for a in state.get("agents") or []}
            if name in used:
                return "This agent instance name is already used. Send another name."
            state["pending_instance_name"] = name
            state["step"] = "llm"
            _save_wizard(key, state)
            return _choose_llm_service(user_id)
        return None

    @staticmethod
    def _selected_agent_for_conversation(conversation_id: str) -> str:
        from core.conversation_store import ConversationStore
        from core.conv_agent_config import get_all_agent_configs
        store = ConversationStore.instance()
        active = store.get_extra(conversation_id, "active_resources") or {}
        selected = str(active.get("agent") or "").strip()
        if selected:
            return selected
        members = get_all_agent_configs(conversation_id)
        if not members:
            return ""
        selected = next(iter(members.keys()), "")
        if selected:
            active["agent"] = selected
            store.set_extra(conversation_id, "active_resources", active)
        return selected

    @staticmethod
    def _create_conversation_from_command(args: str, user_id: str):
        opts = _parse_new_conversation_args(args)
        agent_name = opts["agent"]
        if not agent_name:
            raise ValueError(
                "Usage: /conv new <agent> --title <title> --relay <relay_id> [--llm <service>]")
        if not opts["title"]:
            raise ValueError("Conversation title is required: add --title <title>.")
        if not opts["relays"]:
            raise ValueError("At least one relay is required: add --relay <relay_id>.")

        from core.conversation_creation import create_conversation
        from core.resource_store import ResourceStore
        from core.service_registry import ServiceRegistry

        rs = ResourceStore.instance()
        agent_def = rs.get_any("agent", agent_name, user_id)
        if not agent_def:
            raise ValueError(f"Agent definition not found: {agent_name}")

        reg = ServiceRegistry.get_instance()
        services = [
            s for s in reg.resolve_by_type("llmConnection", user_id=user_id)
            if getattr(s, "enabled", True)
        ]
        service_ids = {s.service_id for s in services}
        llm_service = opts["llm"] or _guess_llm_service(agent_name, services)
        if not llm_service:
            raise ValueError("No enabled LLM service available. Configure one first.")
        if llm_service not in service_ids:
            raise ValueError(f"LLM service not found or disabled: {llm_service}")

        relay_ids = _validate_relays(opts["relays"], user_id=user_id)

        result = create_conversation(user_id, {
            "agents": [{
                "instance_name": agent_name,
                "definition": agent_name,
                "llm_service": llm_service,
                "params": {"name": agent_name},
                "model": str(agent_def.get("model") or ""),
                "tools": agent_def.get("tools") or [],
                "max_depth": int(agent_def.get("max_depth", 1000) or 1000),
                "skills": agent_def.get("skills") or [],
            }],
            "title": opts["title"],
            "relays": relay_ids,
            "default_relay": relay_ids[0],
        })
        cid = result["conversation_id"]
        return cid, agent_name


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


def _apply_telegram_response(flowfile: FlowFile, response: Any) -> None:
    if isinstance(response, dict):
        flowfile.set_content(str(response.get("text") or "").encode("utf-8"))
        markup = response.get("reply_markup")
        if markup:
            flowfile.set_attribute("telegram.reply_markup", json.dumps(markup))
        return
    flowfile.set_content(str(response).encode("utf-8"))


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


class TelegramConversationBridgeTask(BaseTask):
    """Relay shared conversation events to Telegram chats in compact mode."""

    TYPE = "telegramConversationBridge"
    VERSION = "1.0.0"
    NAME = "Telegram Conversation Bridge"
    DESCRIPTION = "Forward conversation events to active Telegram subscribers"
    ICON = "telegram"
    TAGS = ["telegram", "agent", "events"]

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._registered = False

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "service_id": {
                "type": "string", "required": True,
                "description": "Fallback TelegramBotService for users without a personal bot token.",
            },
        }

    def initialize(self):
        if self._registered:
            return
        from core.conversation_event_bus import ConversationEventBus
        ConversationEventBus.instance().add_listener(self._on_event)
        self._registered = True

    def cleanup(self):
        if not self._registered:
            return
        from core.conversation_event_bus import ConversationEventBus
        ConversationEventBus.instance().remove_listener(self._on_event)
        self._registered = False

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        return [flowfile]

    def _on_event(self, conversation_id: str, event_type: str, data: Any) -> None:
        if event_type not in {"new_message", "done", "error_event"}:
            return
        if not isinstance(data, dict):
            return
        if data.get("channel") == "telegram":
            return
        text = self._format_event(event_type, data)
        if not text:
            return
        for user_id, chat_id in self._telegram_subscribers(conversation_id):
            self._send(user_id, chat_id, text)

    def _format_event(self, event_type: str, data: Dict[str, Any]) -> str:
        if event_type == "new_message":
            if data.get("role") != "user":
                return ""
            source = data.get("source") if isinstance(data.get("source"), dict) else {}
            name = source.get("name") or data.get("channel") or "user"
            content = str(data.get("content") or "").strip()
            return f"[{name}] {content}" if content else ""
        if event_type == "done":
            agent = data.get("agent_name") or "assistant"
            response = str(data.get("response") or "").strip()
            return f"[{agent}] {response}" if response else ""
        if event_type == "error_event":
            message = str(data.get("message") or "").strip()
            return f"[error] {message}" if message else ""
        return ""

    @staticmethod
    def _telegram_subscribers(conversation_id: str):
        from core.identity_service import IdentityService
        ids = IdentityService.instance()
        for user_id, links in ids.list_all().items():
            chat_id = links.get("telegram") if isinstance(links, dict) else ""
            if not chat_id:
                continue
            if ids.get_active_conv(user_id, "telegram") == conversation_id:
                yield user_id, chat_id

    def _send(self, user_id: str, chat_id: str, text: str) -> None:
        try:
            from core.identity_service import IdentityService
            bot_token = IdentityService.instance().get_bot_token(user_id, "telegram")
            if bot_token:
                from services.telegram_bot_service import TelegramBotPool
                TelegramBotPool.instance().send_message(bot_token, chat_id, text)
                return
            svc = self.get_service(self.config.get("service_id", ""))
            if svc:
                svc.ensure_connected()
                svc.send_message(chat_id, text)
        except Exception as exc:
            logger.warning("Telegram bridge send failed for %s: %s", chat_id, exc)


TaskFactory.register(TelegramAgentClientTask)
TaskFactory.register(TelegramConversationBridgeTask)


"""Auto-extracted from core/tool_registry.py — see core/handlers/__init__.py"""

import json
import logging
import re
import threading
import time
from typing import Dict, Any, List, Optional

from core.tool_handler import ToolHandler

logger = logging.getLogger(__name__)



class ManageResourceHandler(ToolHandler):
    """CRUD for user resources: agents, skills, MCP servers, task definitions.

    Both users (via slash commands) and agents (via tool calls) can manage
    resources stored in data/repository/ (1 file per resource, scoped).
    """

    def __init__(self):
        self._user_id = ""
        self._conversation_id = ""
        self._agent_name = ""      # which agent is calling (empty = assistant/user)
        self._llm_service = ""     # active agent's llm_service (for inheritance)

    @property
    def name(self) -> str:
        return "manage_resource"

    @property
    def description(self) -> str:
        return (
            "Manage user resources (agents, skills, MCP servers, task definitions, tools). Actions:\n"
            "- create: Create a new resource\n"
            "- update: Modify an existing resource\n"
            "- delete: Delete a resource\n"
            "- list: List all resources of a type\n"
            "- get: Get details of a specific resource\n"
            "- review: Review an untrusted skill before import\n"
            "- search_marketplace: Search external skill marketplaces\n"
            "- import_marketplace: Review/import an external Agent Skill\n"
            "- activate: Activate a resource in the current conversation\n"
            "- deactivate: Deactivate a resource from the current conversation\n\n"
            "Resource types: agent, skill, mcp, task_def, tool\n\n"
            "Agent fields: prompt (required), model, tools (list), "
            "max_depth, timeout, description, llm_service\n"
            "Skill fields: description (required), instructions (required), "
            "allowed-tools (list), license, metadata\n"
            "MCP fields: url (required), auth (dict)\n"
            "Task def fields: prompt (required), criteria, default_interval, description\n"
            "Tool fields: source (required — Python ToolHandler subclass), "
            "description, parameters (JSON Schema). Source is sandbox-validated at create."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create", "update", "delete", "list",
                             "get", "review", "search_marketplace",
                             "import_marketplace", "activate", "deactivate"],
                    "description": "Action to perform",
                },
                "resource_type": {
                    "type": "string",
                    "enum": ["agent", "skill", "mcp", "task_def", "tool"],
                    "description": "Type of resource",
                },
                "name": {
                    "type": "string",
                    "description": "Resource name (required for create/update/delete/get/activate/deactivate)",
                },
                "data": {
                    "type": "object",
                    "description": "Resource data (for create/update). Must include required fields.",
                },
                "source": {
                    "type": "string",
                    "description": "Marketplace source for skill search/import: codex, claude, hermes, openclaw, all.",
                },
                "query": {
                    "type": "string",
                    "description": "Search query for skill marketplace search.",
                },
                "ref": {
                    "type": "string",
                    "description": "Marketplace skill ref or GitHub tree URL for skill import.",
                },
                "review_only": {
                    "type": "boolean",
                    "description": "Only fetch and review; do not create the skill.",
                },
                "force": {
                    "type": "boolean",
                    "description": "Override the review verdict and write/import the skill anyway, after inspecting the findings. Clears every verdict, including a hard block.",
                },
            },
            "required": ["action", "resource_type"],
        }

    def set_user_id(self, uid: str):
        self._user_id = uid

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    def set_agent_name(self, name: str):
        self._agent_name = name or ""

    def set_llm_service(self, svc: str):
        self._llm_service = svc or ""

    def execute(self, arguments: Dict[str, Any]) -> str:
        from core.resource_store import ResourceStore, GLOBAL_USER_ID
        from core.conversation_store import ConversationStore

        action = arguments.get("action", "")
        rtype = arguments.get("resource_type", "")
        name = arguments.get("name", "")
        data = arguments.get("data", {})
        user_id = self._user_id
        store = ResourceStore.instance()

        try:
            if action == "create":
                if not name:
                    return "Error: 'name' is required for create"
                scope = data.pop("scope", "user") if isinstance(data, dict) else "user"

                # Agent-side resource creation is conversation-local. UI/user
                # calls may still create any scope allowed by the user's role.
                if self._conversation_id and self._agent_name:
                    scope = "conversation"

                if rtype in ("agent", "skill") and self._agent_name:
                    data["_created_by"] = self._agent_name

                if rtype == "skill":
                    from core.review_bindings import (
                        attach_review_metadata, review_for_write,
                    )
                    package_files = data.get("package_files", {}) if isinstance(data, dict) else {}
                    review_subject = {
                        k: v for k, v in data.items()
                        if k != "package_files"
                    } if isinstance(data, dict) else {}
                    review_meta = review_for_write(
                        review_subject,
                        operation="create",
                        user_id=user_id,
                        conversation_id=self._conversation_id,
                        package_files=package_files if isinstance(package_files, dict) else {},
                        force=bool(arguments.get("force", False)),
                    )
                    if review_meta:
                        data = attach_review_metadata(data, review_meta)

                if rtype == "tool":
                    src = data.get("source", "") if isinstance(data, dict) else ""
                    if not src:
                        return "Error: tool source is required"
                    from core.tool_validation import validate_and_load
                    try:
                        validate_and_load(src)
                    except ValueError as ve:
                        return f"Error: {ve}"

                if scope == "conversation" and self._conversation_id:
                    if rtype == "task_def":
                        from core.conversation_store import ConversationStore
                        cs = ConversationStore.instance()
                        conv_defs = cs.get_extra(self._conversation_id, "conversation_task_defs") or {}
                        conv_defs[name] = data
                        cs.set_extra(self._conversation_id, "conversation_task_defs", conv_defs)
                    else:
                        store.create(rtype, name, user_id, data,
                                     conversation_id=self._conversation_id)
                        if rtype == "agent":
                            from core.conv_agent_config import add_agent_to_conv
                            add_agent_to_conv(
                                self._conversation_id, name,
                                llm_service=self._llm_service or "",
                            )
                else:
                    store.create(rtype, name, user_id, data)
                    if rtype == "agent" and self._conversation_id:
                        from core.conv_agent_config import add_agent_to_conv
                        add_agent_to_conv(
                            self._conversation_id, name,
                            llm_service=self._llm_service or "",
                        )
                if rtype != "skill":
                    self._activate_resource(rtype, name)
                creator = f" (by {self._agent_name})" if self._agent_name else ""
                return f"Created {rtype} '{name}' (scope: {scope}).{creator}"

            elif action == "update":
                if not name:
                    return "Error: 'name' is required for update"
                existing = store.get_any(
                    rtype, name, user_id,
                    conversation_id=self._conversation_id) or {}
                if rtype == "skill":
                    merged = {k: v for k, v in existing.items()
                              if not str(k).startswith("_")}
                    merged.update(data if isinstance(data, dict) else {})
                    package_files = merged.pop("package_files", {})
                    if not package_files and merged.get("skill_root"):
                        from pathlib import Path
                        from core.repository import ScopedRepository
                        package_files = ScopedRepository._read_skill_package_files(
                            Path(merged["skill_root"]))
                    from core.review_bindings import (
                        attach_review_metadata, review_for_write,
                    )
                    review_meta = review_for_write(
                        merged,
                        operation="update",
                        user_id=user_id,
                        conversation_id=self._conversation_id,
                        package_files=package_files if isinstance(package_files, dict) else {},
                        force=bool(arguments.get("force", False)),
                    )
                    if review_meta:
                        data = attach_review_metadata(data, review_meta)
                # Update in the scope the resource actually lives in:
                # get_any cascades conversation → user → global, so without
                # the conversation_id a conv-scoped resource would
                # mis-target the user scope and the update would fail.
                upd_kwargs = {}
                if (existing.get("_scope") == "conversation"
                        and self._conversation_id):
                    upd_kwargs["conversation_id"] = self._conversation_id
                store.update(rtype, name, user_id, data, **upd_kwargs)
                if rtype == "skill" and self._conversation_id:
                    from core.skill_lifecycle import notify_skill_updated
                    updated = store.get_any(
                        "skill", name, user_id,
                        conversation_id=self._conversation_id) or data
                    notify_skill_updated(
                        name, updated, user_id, self._conversation_id,
                        resource_store=store)
                return f"Updated {rtype} '{name}'."

            elif action == "delete":
                if not name:
                    return "Error: 'name' is required for delete"
                # Ownership check for agent/skill deletion
                if rtype in ("agent", "skill"):
                    existing = store.get_any(
                        rtype, name, user_id,
                        conversation_id=self._conversation_id)
                    if existing:
                        created_by = existing.get("_created_by", existing.get("created_by"))
                        if created_by is not None and created_by != (self._agent_name or ""):
                            return (f"Error: {rtype} '{name}' was created by "
                                    f"'{created_by}' — you can only delete "
                                    f"resources you created.")
                delete_kwargs = {}
                if (rtype == "skill" and existing.get("_scope") == "conversation"
                        and self._conversation_id):
                    delete_kwargs["conversation_id"] = self._conversation_id
                if store.delete(rtype, name, user_id, **delete_kwargs):
                    if rtype == "skill":
                        from core.skill_lifecycle import remove_skill_assignments
                        remove_skill_assignments(
                            name, user_id, self._conversation_id,
                            resource_store=store, source="skill_delete")
                    return f"Deleted {rtype} '{name}'."
                return f"{rtype} '{name}' not found."

            elif action == "list":
                items = store.list_all(rtype, user_id,
                                       conversation_id=self._conversation_id)
                if not items:
                    return f"No {rtype}s found."
                scope_icons = {"global": "🌐", "user": "👤", "conversation": "💬"}
                lines = [f"Your {rtype}s ({len(items)}):"]
                for item in items:
                    desc = item.get("description", "") or item.get("prompt", "")[:60]
                    scope = scope_icons.get(item.get("_scope", ""), "")
                    creator = item.get("_created_by", "")
                    suffix = f" [by {creator}]" if creator else ""
                    lines.append(f"- {scope} {item['name']}: {desc}{suffix}")
                return "\n".join(lines)

            elif action == "get":
                if not name:
                    return "Error: 'name' is required for get"
                item = store.get_any(rtype, name, user_id,
                                     conversation_id=self._conversation_id)
                if not item:
                    return f"{rtype} '{name}' not found."
                return json.dumps(item, ensure_ascii=False, indent=2)

            elif action == "review":
                if rtype != "skill":
                    return "Error: review is only supported for skills"
                skill_data = dict(data or {}) if isinstance(data, dict) else {}
                if not skill_data and name:
                    item = store.get_any(rtype, name, user_id,
                                         conversation_id=self._conversation_id)
                    if not item:
                        return f"skill '{name}' not found."
                    skill_data = item
                if not (skill_data.get("instructions") or skill_data.get("prompt")):
                    return "Error: skill instructions are required for review"
                package_files = skill_data.pop("package_files", {})
                if not package_files and skill_data.get("skill_root"):
                    # Bundled assets are read on demand for review, not kept
                    # on every skill read (see ScopedRepository._read_skill).
                    from pathlib import Path
                    from core.repository import ScopedRepository
                    package_files = ScopedRepository._read_skill_package_files(
                        Path(skill_data["skill_root"]))
                from core.review_bindings import review_now
                result = review_now(
                    skill_data,
                    operation="review",
                    user_id=user_id,
                    conversation_id=self._conversation_id,
                    package_files=package_files if isinstance(package_files, dict) else {},
                )
                return json.dumps(result, ensure_ascii=False, indent=2)

            elif action == "search_marketplace":
                if rtype != "skill":
                    return "Error: search_marketplace is only supported for skills"
                from core.skill_marketplace import search_marketplace
                result = search_marketplace(
                    source=str(arguments.get("source", "all") or "all"),
                    query=str(arguments.get("query", "") or ""),
                    limit=int(arguments.get("limit", 10) or 10),
                )
                return json.dumps(result, ensure_ascii=False, indent=2)

            elif action == "import_marketplace":
                if rtype != "skill":
                    return "Error: import_marketplace is only supported for skills"
                ref = str(arguments.get("ref", "") or "")
                if not ref:
                    return "Error: 'ref' is required for import_marketplace"
                from core.skill_marketplace import import_marketplace_skill
                result = import_marketplace_skill(
                    source=str(arguments.get("source", "") or ""),
                    ref=ref,
                    name=name,
                    user_id=user_id,
                    conversation_id=self._conversation_id,
                    review_only=bool(arguments.get("review_only", False)),
                    force=bool(arguments.get("force", False)),
                    scope=str(arguments.get("scope", "user") or "user"),
                )
                return json.dumps(result, ensure_ascii=False, indent=2)

            elif action == "activate":
                if not name:
                    return "Error: 'name' is required for activate"
                if rtype == "skill":
                    return "Error: skills are injected only through agent.assigned_skills. Use /skill assign @agent @skill."
                if store.get_any(rtype, name, user_id) is None:
                    return f"{rtype} '{name}' not found."
                self._activate_resource(rtype, name)
                return f"Activated {rtype} '{name}' in this conversation."

            elif action == "deactivate":
                if not name:
                    return "Error: 'name' is required for deactivate"
                if rtype == "skill":
                    return "Error: skills are injected only through agent.assigned_skills. Use /skill unassign @agent @skill."
                self._deactivate_resource(rtype, name)
                return f"Deactivated {rtype} '{name}' from this conversation."

            elif action == "disable":
                if not name or not self._conversation_id:
                    return "Error: 'name' and conversation required"
                from core.conversation_store import ConversationStore
                cs = ConversationStore.instance()
                disabled = cs.get_extra(self._conversation_id, "disabled_agents") or []
                if name not in disabled:
                    disabled.append(name)
                    cs.set_extra(self._conversation_id, "disabled_agents", disabled)
                return f"Agent '{name}' disabled in this conversation."

            elif action == "enable":
                if not name or not self._conversation_id:
                    return "Error: 'name' and conversation required"
                from core.conversation_store import ConversationStore
                cs = ConversationStore.instance()
                disabled = cs.get_extra(self._conversation_id, "disabled_agents") or []
                if name in disabled:
                    disabled.remove(name)
                    cs.set_extra(self._conversation_id, "disabled_agents", disabled)
                return f"Agent '{name}' enabled in this conversation."

            elif action == "promote":
                if not name:
                    return "Error: 'name' is required"
                target_scope = data.get("target_scope", "user")
                # Get the agent from any scope
                item = store.get_any(rtype, name, user_id,
                                     conversation_id=self._conversation_id)
                if not item:
                    return f"{rtype} '{name}' not found."
                current_scope = item.get("_scope", "user")
                # Remove scope metadata before copying
                promote_data = {k: v for k, v in item.items()
                                if not k.startswith("_") and k != "name"}
                if target_scope == "user":
                    store.create(rtype, name, user_id, promote_data)
                elif target_scope == "global":
                    return "Error: Cannot promote to global scope from chat. Use the admin GUI."
                elif target_scope == "conversation" and self._conversation_id:
                    from core.conversation_store import ConversationStore
                    cs = ConversationStore.instance()
                    conv_agents = cs.get_extra(self._conversation_id, "conversation_agents") or {}
                    conv_agents[name] = promote_data
                    cs.set_extra(self._conversation_id, "conversation_agents", conv_agents)
                else:
                    return f"Invalid target scope: {target_scope}"
                return f"{rtype} '{name}' promoted from {current_scope} to {target_scope}."

            else:
                return f"Unknown action: {action}"

        except (ValueError, KeyError) as e:
            return f"Error: {e}"

    def _activate_resource(self, rtype: str, name: str):
        """Add resource to conversation's active_resources."""
        if not self._conversation_id:
            return
        if rtype == "skill":
            return
        from core.conversation_store import ConversationStore
        cs = ConversationStore.instance()
        active = cs.get_extra(self._conversation_id, "active_resources") or {}
        if rtype == "agent":
            active["agent"] = name
        else:
            key = rtype + "s"  # skills, mcps
            lst = active.get(key, [])
            if name not in lst:
                lst.append(name)
            active[key] = lst
        cs.set_extra(self._conversation_id, "active_resources", active)

    def _deactivate_resource(self, rtype: str, name: str):
        """Remove resource from conversation's active_resources."""
        if not self._conversation_id:
            return
        if rtype == "skill":
            return
        from core.conversation_store import ConversationStore
        cs = ConversationStore.instance()
        active = cs.get_extra(self._conversation_id, "active_resources") or {}
        if rtype == "agent":
            if active.get("agent") == name:
                active.pop("agent", None)
        else:
            key = rtype + "s"
            lst = active.get(key, [])
            if name in lst:
                lst.remove(name)
            active[key] = lst
        cs.set_extra(self._conversation_id, "active_resources", active)


class SpawnAgentsHandler(ToolHandler):
    """Spawn one or more sub-agents to work in parallel.

    The main agent can delegate complex sub-tasks to specialized agents
    defined in the resource store. Results are aggregated and returned.
    """

    def __init__(self):
        self._user_id = ""
        self._conversation_id = ""
        self._available_agents: List[str] = []
        self._local = threading.local()  # thread-safe source agent
        self._client_resolver = None  # callable(svc_id, uid) -> (client, svc)
        self._on_event = None  # callable(event_type, data)
        self._default_client = None  # fallback LLM client

    def set_conversation_id(self, conversation_id: str) -> None:
        self._conversation_id = conversation_id

    def set_spawn_deps(self, client, client_resolver, on_event, registry=None):
        """Set dependencies for spawning sub-agents."""
        self._default_client = client
        self._client_resolver = client_resolver
        self._on_event = on_event
        self._registry = registry

    def set_source_agent(self, agent_name: str, llm_service: str = "") -> None:
        self._local.source_agent = agent_name
        self._local.source_llm_service = llm_service

    def set_delegate_tc_id(self, tc_id: str) -> None:
        """Set the tool_call ID of the delegate call (thread-local)."""
        self._local.delegate_tc_id = tc_id

    def set_available_agents(self, agents: List):
        """Set the list of available agents (names or dicts with details)."""
        self._available_agents = list(agents)

    @property
    def name(self) -> str:
        return "delegate"

    @property
    def description(self) -> str:
        base = (
            "Send a private message to another agent in this conversation. "
            "Always ASYNCHRONOUS — returns IMMEDIATELY, YOU ARE NOT BLOCKED.\n\n"
            "Default context='shared': the target agent uses its own "
            "conversation context to read your message and reply. You will "
            "receive their answer as a private '[Delegate result …]' "
            "message that YOU MUST READ and REACT TO (integrate, reply to "
            "the user, or delegate again).\n\n"
            "context='isolated' / 'last:N': spawns a separate sub-agent "
            "with an empty (or sliced) context — use this ONLY when you "
            "genuinely need a fresh workspace (a self-contained research "
            "task). Agents that are themselves running as a delegate can "
            "ONLY use context='shared' (nested private sub-contexts are "
            "forbidden).\n\n"
            "Delegate is bidirectional: an agent called via delegate can "
            "call delegate(caller, …) to reply or ask a follow-up.\n\n"
            "Delegates are de-duplicated per (caller, target) pair: if "
            "you call delegate again for a target that's still working on "
            "your previous request, the new message is injected into "
            "their running loop instead of spawning a second one."
        )
        if self._available_agents:
            lines = []
            for a in self._available_agents:
                if isinstance(a, dict):
                    name = a.get("name", "")
                    desc = a.get("description", "") or ""
                    svc = a.get("llm_service", "") or ""
                    tools = a.get("tools") or []
                    parts = [f"- {name}"]
                    if desc:
                        parts[0] += f": {desc}"
                    extras = []
                    if svc:
                        extras.append(f"via {svc}")
                    if tools:
                        extras.append(f"tools: {', '.join(tools[:8])}")
                    if extras:
                        parts[0] += f" ({', '.join(extras)})"
                    lines.append(parts[0])
                else:
                    lines.append(f"- {a}")
            base += "\n\nAvailable agents:\n" + "\n".join(lines)
            base += "\n\nUse these exact names in the 'agent' field."
        return base

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "agent": {
                                "type": "string",
                                "description": "Exact name of an existing agent (from available agents list)",
                            },
                            "message": {
                                "type": "string",
                                "description": "The task/message to send to the agent",
                            },
                            "id": {
                                "type": "string",
                                "description": "Optional task ID for tracking",
                            },
                            "context": {
                                "type": "string",
                                "description": (
                                    "Context mode (default: 'shared'): "
                                    "'shared' — target agent uses its existing "
                                    "conversation context (no separate sub-agent, "
                                    "just a private message delivered in the conv); "
                                    "'isolated' — fresh empty sub-agent context "
                                    "(spawns a separate sub-agent); "
                                    "'last:N' — fresh sub-agent with last N "
                                    "messages from the parent conv. "
                                    "Agents that are themselves a delegate can "
                                    "only use 'shared' — isolated/last are rejected "
                                    "to prevent nested private sub-contexts."
                                ),
                            },
                            "skills": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Skill names to inject into the delegate agent's prompt (replaces the agent's own assigned_skills)",
                            },
                            "persist": {
                                "type": "boolean",
                                "description": "Only for context='isolated' or 'last:N': keep the sub-agent's sub-conversation after completion for later resume. Ignored in context='shared' (the target uses the main conv, nothing separate to persist).",
                            },
                        },
                        "required": ["agent", "message"],
                    },
                    "description": "List of tasks to spawn",
                },
            },
            "required": ["tasks"],
        }

    def set_user_id(self, uid: str):
        self._user_id = uid

    def execute(self, arguments: Dict[str, Any]) -> str:
        if not self._client_resolver:
            return "Error: Agent executor not configured (missing client_resolver)."

        from core.agent_executor import resolve_agent_task, SubAgentExecutor
        import uuid

        from core.handlers._arg_normalize import validate_object_list
        tasks_spec, _err = validate_object_list(
            arguments.get("tasks"),
            param_name="tasks",
            required_keys=["agent", "message"],
            example=('tasks=[{"agent": "<existing-agent-name>", '
                     '"message": "<text>", "id"?: "<optional>", '
                     '"context"?: "shared"|"isolated"|"last:N"}, ...]'),
        )
        if _err:
            return f"Error: {_err}"
        # Delegate is ALWAYS async (fire-and-forget). Results come back
        # via the preempt (caller running) / wake (caller idle) path.
        # No more 'wait' param — concurrency is the whole point.
        user_id = self._user_id

        # Detect task sub-conv: when an agent running inside a task
        # delegates, self._conversation_id is the sub-conv
        # (parent::task::tid). Agent resolution, routing, and delivery
        # must use the parent conv (where agents are registered).
        # Result delivery back to the calling task agent uses the raw
        # sub-conv ID so the preempt/wake targets the correct context.
        _raw_conv_id = self._conversation_id
        _parent_conv_id = _raw_conv_id
        _source_task_id = ""
        if "::task::" in _raw_conv_id:
            _parent_conv_id = _raw_conv_id.split("::task::")[0]
            _source_task_id = _raw_conv_id.split("::task::", 1)[1]

        # Thread-safe source agent (each agent loop runs in its own thread)
        _src_agent = getattr(self._local, 'source_agent', '') or ''
        _src_svc = getattr(self._local, 'source_llm_service', '') or ''
        _delegate_tc_id = getattr(self._local, 'delegate_tc_id', '') or ''

        # Resolve self-name and nicknames to detect self-calls
        _self_names = {_src_agent.lower()} if _src_agent else set()
        _src_nickname = ""
        if _parent_conv_id and _src_agent:
            try:
                from core.conversation_store import ConversationStore
                _nicks = ConversationStore.instance().get_extra(
                    _parent_conv_id, "agent_nicknames") or {}
                _src_nickname = _nicks.get(_src_agent, "")
                if _src_nickname:
                    _self_names.add(_src_nickname.lower())
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

        # Per-pair de-duplication: if the same caller delegates to the
        # same target while a previous delegate is still running, inject
        # the new message into the running sub-agent's loop (preempt)
        # rather than spawning a parallel one. Only unique (caller,
        # target) pairs with no live delegate go through the spawn path.
        from core.agent_executor import (
            get_live_delegate,
            queue_live_delegate_message,
        )
        agent_tasks = []
        _injected_results = []
        for spec in tasks_spec:
            agent_name = spec.get("agent", "")
            message = spec.get("message", "")
            task_id = spec.get("id", uuid.uuid4().hex[:8])

            # Preempt path: a delegate for (_src_agent, agent_name) is
            # already running in this conversation — inject the message
            # into its loop instead of spawning a second one.
            if (_parent_conv_id and _src_agent and agent_name):
                _live = get_live_delegate(
                    _parent_conv_id, _src_agent, agent_name)
                if _live:
                    _live_client = _live.get("client")
                    _live_tid = _live.get("task_id", "")
                    _delivered = False
                    if _live_client and hasattr(_live_client, "send_user_message"):
                        try:
                            _delivered = bool(
                                _live_client.send_user_message(message))
                        except Exception as _pe:
                            logger.warning(
                                "[delegate] preempt to live delegate %s failed: %s",
                                _live_tid, _pe)
                    if not _delivered:
                        queue_live_delegate_message(
                            _parent_conv_id, _src_agent, agent_name, message)
                    _injected_results.append({
                        "task_id": _live_tid,
                        "agent": agent_name,
                        "status": "injected" if _delivered else "injected_queued",
                        "message": (
                            f"A delegate for '{agent_name}' was already "
                            f"running (task_id={_live_tid}) — your new "
                            f"message was {'sent as preempt' if _delivered else 'queued'}. "
                            f"You will receive a single follow-up result "
                            f"when that delegate finishes."
                        ),
                    })
                    logger.info(
                        "[delegate] preempt: (%s→%s) live task %s, "
                        "new message injected (delivered=%s)",
                        _src_agent, agent_name, _live_tid, _delivered)
                    continue

            # Resolve context mode — default "shared" (new semantics:
            # target agent uses its own conversation context, the
            # delegate is just a private message in the conv; no
            # sub-agent spawning).
            context_mode = spec.get("context", "shared")

            # A delegate agent can only use "shared" when it calls
            # delegate itself — prevents nested private sub-contexts.
            if context_mode != "shared" and self._is_caller_a_delegate():
                return (
                    f"Error: agent '{_src_agent}' is itself a delegate — "
                    f"sub-delegates must use context='shared' (isolated and "
                    f"last:N are reserved for top-level agents). Use "
                    f"context='shared' or drop the parameter."
                )

            if context_mode == "shared":
                # SHARED PATH: no sub-agent spawn. Persist a private
                # delegate message routed (from, to), then trigger the
                # target agent (preempt if running, wake if idle).
                if agent_name.lower() in _self_names:
                    return (
                        f"Error: You ('{_src_agent}') cannot delegate to "
                        f"yourself ('{agent_name}')."
                    )
                _deliver_info = self._deliver_shared_delegate(
                    from_agent=_src_agent, to_agent=agent_name,
                    message=message, user_id=user_id,
                    conv_id=_parent_conv_id)
                _injected_results.append({
                    "task_id": task_id,
                    "agent": agent_name,
                    "status": "delivered",
                    "mode": "shared",
                    "message": (
                        f"Delegate message delivered privately to '{agent_name}' "
                        f"(shared context — they use their own conv context). "
                        f"Target is {_deliver_info['state']}. You will receive "
                        f"'[Delegate result for task_id={task_id}]' when they "
                        f"reply — READ it and REACT."
                    ),
                })
                continue

            # ISOLATED / last:N / summary:N / full path — spawn a real
            # sub-agent via SubAgentExecutor.
            try:
                from core.handlers._arg_normalize import normalize_string_list
                extra_skills = normalize_string_list(spec.get("skills"))
                task = resolve_agent_task(agent_name, message, user_id,
                                         conversation_id=_parent_conv_id,
                                         extra_skills=extra_skills)
                task.id = task_id
                task.source_agent = _src_agent
                task.source_agent_nickname = _src_nickname
                task.source_llm_service = _src_svc
                task.delegate_tc_id = _delegate_tc_id
                task.persist = bool(spec.get("persist", False))

                task.context_mode = context_mode
                task.parent_conversation_id = _parent_conv_id
                task.source_task_id = _source_task_id

                if context_mode != "isolated" and _parent_conv_id:
                    task.context_messages = self._resolve_context(
                        context_mode, _parent_conv_id, user_id)

                # Prevent agent from calling itself
                if agent_name.lower() in _self_names:
                    return (f"Error: You ('{_src_agent}' via {_src_svc}) "
                            f"cannot call yourself as '{agent_name}' (via {task.llm_service}). "
                            f"Use a different agent or respond directly.")

                agent_tasks.append(task)
            except KeyError as e:
                return f"Error: {e}"

        if not agent_tasks:
            # Every spec was a preempt into an already-running delegate.
            if _injected_results:
                return json.dumps(_injected_results, ensure_ascii=False, indent=2)
            return "Error: no valid tasks to spawn."

        # Emit group start event so the UI can create a parent container
        if self._on_event and _delegate_tc_id:
            self._on_event("delegate_group_start", {
                "delegate_tc_id": _delegate_tc_id,
                "source_agent": _src_agent,
                "agents": [
                    {"name": t.agent_name, "task_id": t.id,
                     "message": t.message, "llm_service": t.llm_service}
                    for t in agent_tasks
                ],
                "total": len(agent_tasks),
                "source_task_id": _source_task_id,
            })

        # Create executor on-the-fly
        executor = SubAgentExecutor(
            self._default_client, self._registry, max_workers=4,
            client_resolver=self._client_resolver,
            on_event=self._on_event,
        )

        # Always async. Background completion callback ships the
        # isolated/last:N sub-agent's result back to the caller via
        # preempt/wake.
        # Result delivery uses _raw_conv_id so the preempt/wake
        # targets the caller in the task sub-conv (not the parent).
        _result_conv_id = _raw_conv_id
        _uid = user_id
        _src = _src_agent
        def _bg_callback(result, task):
            self._inject_bg_result(result, task, _result_conv_id, _uid, _src)

        results = executor.spawn(agent_tasks, wait=False,
                                 on_bg_complete=_bg_callback)

        ids = [r.task_id for r in results]
        _reply = {
            "status": "spawned",
            "task_ids": ids,
            "message": (
                f"Spawned {len(ids)} isolated sub-agent(s) in background. "
                f"You are NOT blocked — continue your own work. "
                f"When each sub-agent finishes you will receive a "
                f"message '[Delegate result for task_id=<id>]' "
                f"containing their response: READ IT and REACT "
                f"(integrate into your work, or reply to the user). "
                f"Track these task_ids: {ids}."
            ),
        }
        if _injected_results:
            _reply["injected"] = _injected_results
        return json.dumps(_reply, ensure_ascii=False)

    def _resolve_context(self, mode: str, conversation_id: str,
                         user_id: str) -> list:
        """Resolve context messages based on mode."""
        from core.conversation_store import ConversationStore
        store = ConversationStore.instance()

        if mode == "full":
            raw = store.load(conversation_id, user_id=user_id) or []
            # Filter out system messages, keep user/assistant/tool
            return [m for m in raw if m.get("role") != "system"]

        if mode.startswith("last:"):
            try:
                n = int(mode.split(":")[1])
            except (ValueError, IndexError):
                n = 10
            raw = store.load(conversation_id, user_id=user_id) or []
            non_system = [m for m in raw if m.get("role") != "system"]
            return non_system[-n:]

        if mode.startswith("summary:"):
            try:
                max_tokens = int(mode.split(":")[1])
            except (ValueError, IndexError):
                max_tokens = 2000
            raw = store.load(conversation_id, user_id=user_id) or []
            # Build a simple text summary from recent messages
            text_parts = []
            for m in raw[-50:]:  # last 50 messages for summary input
                role = m.get("role", "")
                content = m.get("content", "")
                if role in ("user", "assistant") and content:
                    text_parts.append(f"{role}: {content[:200]}")
            summary = "\n".join(text_parts)
            # Truncate to approximate token limit
            if len(summary) > max_tokens * 4:
                summary = summary[-(max_tokens * 4):]
            return [{"role": "user",
                     "content": f"[Context summary from parent conversation]"
                                f"\n{summary}"}]

        return []  # isolated

    def _is_caller_a_delegate(self) -> bool:
        """True if the currently-executing agent was itself triggered by a
        shared delegate message. Sub-delegates are restricted to
        context='shared' to prevent nested private sub-contexts.

        Checks AgentLoopTask._active_contexts for the caller's ctx and
        looks for a _turn_mode.type == 'delegate_reply'. Conservative:
        if we can't determine, returns False (allow).
        """
        try:
            _src = getattr(self._local, "source_agent", "") or ""
            if not (self._conversation_id and _src):
                return False
            from tasks.ai.agent_loop import AgentLoopTask
            inst = AgentLoopTask._live_instance
            if not inst:
                return False
            key = f"{self._conversation_id}:{_src}"
            with inst._active_contexts_lock:
                ctx = inst._active_contexts.get(key)
            if not ctx:
                return False
            tm = ctx.get("_turn_mode") or {}
            return tm.get("type") == "delegate_reply"
        except Exception:
            return False

    # Per-pair short-window dedup: LLMs sometimes call delegate twice
    # in rapid succession with identical content (hallucinated retry,
    # or mid-turn "just checking"). Skipping the duplicate prevents
    # double blocks in the UI and a double wake/preempt of the target.
    _SHARED_DEDUP_TTL_SEC = 30
    _shared_dedup: Dict[str, float] = {}
    _shared_dedup_lock = threading.Lock()

    def _is_duplicate_shared_delegate(self, conv_id: str, from_agent: str,
                                       to_agent: str, message: str) -> bool:
        import hashlib as _h
        import time as _t
        _key = "|".join([
            conv_id, from_agent, to_agent,
            _h.sha1(
                message.encode("utf-8", errors="replace"),
                usedforsecurity=False,
            ).hexdigest(),
        ])
        now = _t.time()
        with self._shared_dedup_lock:
            # Garbage-collect old entries so the dict doesn't grow
            # unboundedly over a long conversation.
            _cutoff = now - self._SHARED_DEDUP_TTL_SEC
            for _k in [k for k, ts in self._shared_dedup.items() if ts < _cutoff]:
                self._shared_dedup.pop(_k, None)
            last = self._shared_dedup.get(_key, 0.0)
            if last and (now - last) < self._SHARED_DEDUP_TTL_SEC:
                return True
            self._shared_dedup[_key] = now
        return False

    def _deliver_shared_delegate(self, from_agent: str, to_agent: str,
                                 message: str, user_id: str,
                                 conv_id: str = "") -> Dict[str, str]:
        """Persist a private delegate message and trigger the target.

        Routing (via ConversationStore.append_message):
          - transcript
          - from_agent's context (prefixed [delegate from→to])
          - to_agent's context (raw, role=user)
          - NOT shared, NOT other agents

        Target trigger:
          - running → preempt queue (stdin injection via send_user_message,
            OR turn-boundary preempt if turn_mode mismatch)
          - idle    → wake by spawning a new agent loop
        """
        import uuid as _uuid
        conv_id = conv_id or self._conversation_id or ""
        # Membership guard: the target agent MUST be a member of this
        # conversation before we persist the delegate msg and wake its
        # loop. Without this, a caller (or a hallucinated tool call)
        # asking to delegate to an unknown agent silently enqueues a
        # message for a phantom, spawns a wake loop, and the phantom's
        # _resolve_agent_client hard-fails late — leaving a dangling
        # message, an orphaned relay, and a confusing error.
        # require_agent_member auto-registers from a global/user agent
        # definition when possible, so valid cross-conv agents "just
        # work"; returns an actionable error otherwise.
        from core.conv_agent_config import require_agent_member
        _member_err = require_agent_member(
            conv_id, to_agent, user_id=user_id)
        if _member_err:
            logger.warning(
                "[delegate-shared] membership check failed: %s",
                _member_err)
            return {"state": f"error: {_member_err}"}
        # Dedup: skip if the same (from, to, message) was just sent.
        if conv_id and self._is_duplicate_shared_delegate(
                conv_id, from_agent, to_agent, message):
            logger.info(
                "[delegate-shared] duplicate within %ds — skipped "
                "(%s -> %s)",
                self._SHARED_DEDUP_TTL_SEC, from_agent, to_agent)
            return {"state": "duplicate (ignored)"}
        _msg_id = _uuid.uuid4().hex[:12]
        _src = {
            "type": "agent_delegate",
            "from": from_agent,
            "to": to_agent,
        }
        # Persist via ConversationWriter.append_message — the unified
        # router reads source.type == "agent_delegate" and routes the
        # message privately to (transcript + from ctx + to ctx) with
        # proper prefixes, skipping shared broadcast and other agents.
        from core.conversation_writer import ConversationWriter
        from core.llm_client import stamp_message
        _delegate_msg = stamp_message({
            "role": "user",
            "content": message,
            "msg_id": _msg_id,
            "source": _src,
        }, conv_id)
        # Publish a live SSE event AFTER the message lands on disk so
        # the webchat renders the delegate block in real time without
        # ever racing ahead of persisted state (visible => persisted).
        _sse_new_msg = {
            "type": "new_message",
            "data": {
                "role": _delegate_msg["role"],
                "content": message,
                "msg_id": _msg_id,
                "source": _src,
                "ts": _delegate_msg.get("ts"),
            },
        }
        try:
            ConversationWriter.for_conversation(conv_id).enqueue_message(
                _delegate_msg, agent_name=from_agent, user_id=user_id,
                sse_events=[_sse_new_msg])
        except Exception as e:
            logger.warning("[delegate-shared] persist failed: %s", e)

        # Trigger the target. Same preempt/wake helpers used by the
        # sub-agent result delivery path — they already know how to
        # route to a specific agent within a conv.
        try:
            from tasks.ai.agent_loop import AgentLoopTask
            inst = AgentLoopTask._live_instance
            if inst:
                key = f"{conv_id}:{to_agent}" if to_agent else conv_id
                # _route_conv_id is the conv the target agent actually
                # runs in — usually conv_id, but if the target is inside
                # a task sub-conv (parent::task::tid:agent) we must use
                # that sub-conv for preempt/wake routing.
                _route_conv_id = conv_id
                with inst._active_contexts_lock:
                    running = key in inst._active_contexts
                    if not running and to_agent:
                        # Scan for the agent in a task sub-conv
                        _prefix = f"{conv_id}::task::"
                        _suffix = f":{to_agent}"
                        for k in inst._active_contexts:
                            if k.startswith(_prefix) and k.endswith(_suffix):
                                key = k
                                # Extract sub-conv ID (everything before :agent)
                                _route_conv_id = k[: -len(_suffix)]
                                running = True
                                break
                if running:
                    logger.info(
                        "[delegate-shared] target '%s' running (key=%s) — preempt",
                        to_agent, key)
                    self._preempt_caller(inst, _route_conv_id, to_agent,
                                         message, _msg_id, _src)
                    return {"state": "running (preempted)"}
                else:
                    logger.info(
                        "[delegate-shared] target '%s' idle — wake", to_agent)
                    self._wake_caller(inst, conv_id, to_agent, user_id,
                                      message, _msg_id, source=_src)
                    return {"state": "idle (waking)"}
        except Exception as e:
            logger.error("[delegate-shared] trigger failed: %s", e)
        return {"state": "unknown (no AgentLoopTask instance)"}

    def _inject_bg_result(self, result, task, conv_id, user_id, source_agent):
        """Deliver a sub-agent's result back to the caller agent.

        Private A↔B channel: only the caller (source_agent) sees this
        message — NOT other agents linked to the conversation. The user
        sees it in the transcript (user sees everything).

        Delivery:
          1. Full response persisted to FileStore (category="delegate_result")
             so the caller can read it in full if needed.
          2. A short "[Delegate result for task_id=X] — read file Y, react"
             prompt-style user message is injected into the caller's
             context only.
          3. If the caller is currently running → preempt (append to
             _pending_user_msgs so the current loop picks it up).
             If the caller is idle → wake a new loop via agent_loop.
        """
        import uuid as _uuid
        import time as _time
        try:
            # 1. Persist the full result to the FileStore — the caller
            #    can `read` it if the short summary isn't enough.
            _full_text_parts = [
                f"# Delegate result\n",
                f"task_id: {result.task_id}\n",
                f"agent: {result.agent_name}\n",
                f"status: {result.status}\n",
                f"duration: {result.duration_ms/1000:.1f}s\n",
                f"tokens_in: {result.tokens_in}, tokens_out: {result.tokens_out}\n",
            ]
            if result.model:
                _full_text_parts.append(f"model: {result.model}\n")
            if result.tools_called:
                _full_text_parts.append(
                    f"tools_called: {', '.join(result.tools_called)}\n")
            _full_text_parts.append("\n---\n\n")
            if result.response:
                _full_text_parts.append(f"## Response\n\n{result.response}\n")
            if result.error:
                _full_text_parts.append(f"\n## Error\n\n{result.error}\n")
            if result.question:
                _full_text_parts.append(
                    f"\n## Agent needs input\n\n{result.question}\n"
                    f"\nReply by calling delegate("
                    f"agent='{result.agent_name}', message='<your answer>').\n")
            _full_text = "".join(_full_text_parts)

            _file_id = ""
            try:
                from core.file_store import FileStore
                _file_id = FileStore.instance().store(
                    f"delegate_{result.task_id}.md",
                    _full_text.encode("utf-8"),
                    "text/markdown",
                    user_id=user_id, conversation_id=conv_id,
                    category="delegate_result")
            except Exception as _fe:
                logger.warning("[bg-delegate] FileStore persist failed: %s", _fe)

            # 2. Build the short nudge shown in the caller's context.
            #    Deliberately phrased as an imperative user message so the
            #    LLM reacts (same pattern as plan/task injections).
            if result.status == "needs_input" and result.question:
                _summary = (
                    f"[Delegate result for task_id={result.task_id}] "
                    f"Sub-agent '{result.agent_name}' needs your input. "
                    f"Question:\n\n{result.question}\n\n"
                    f"You MUST read the full context in file "
                    f"{_file_id or '<unavailable>'} and reply by calling "
                    f"delegate(agent='{result.agent_name}', "
                    f"message='<your answer>')."
                )
            elif result.error:
                _summary = (
                    f"[Delegate result for task_id={result.task_id}] "
                    f"Sub-agent '{result.agent_name}' FAILED: {result.error[:300]}.\n"
                    f"Full trace in file {_file_id or '<unavailable>'}. "
                    f"Read it and decide how to react (retry, fallback, tell the user)."
                )
            else:
                # Cap inline preview so the context isn't flooded.
                _preview = (result.response or "")[:800]
                _more = (len(result.response or "") > 800)
                _summary = (
                    f"[Delegate result for task_id={result.task_id}] "
                    f"Sub-agent '{result.agent_name}' finished.\n\n"
                    f"{_preview}{'…' if _more else ''}\n\n"
                    f"{'Full response in file ' + _file_id + ' — read it with `read` if you need more.' if _file_id and _more else ''}\n"
                    f"READ this result and REACT: integrate it into your work, "
                    f"or reply to the user with what you learned. Do not ignore it."
                ).rstrip()

            _msg_id = _uuid.uuid4().hex[:12]

            # 3. Deliver to the caller: preempt if running, wake if idle.
            self._deliver_to_caller(
                conv_id=conv_id, caller_agent=source_agent,
                user_id=user_id, text=_summary, msg_id=_msg_id,
                task_id=result.task_id, delegate_agent=result.agent_name,
                file_id=_file_id,
            )
        except Exception as e:
            logger.exception("[bg-delegate] Failed to deliver result for task %s: %s",
                             result.task_id, e)

    def _deliver_to_caller(self, conv_id, caller_agent, user_id, text, msg_id,
                           task_id, delegate_agent, file_id):
        """Route the delegate result to the caller — preempt-or-wake.

        A delegate is a private A↔B channel: this nudge goes ONLY into
        caller_agent's context (not shared, not other agents). The user
        sees it via the transcript (display_only publish).
        """
        _source = {
            "type": "user",
            "name": "system",
            "target_agent": caller_agent,
            "delegate": {
                "task_id": task_id,
                "agent": delegate_agent,
                "file_id": file_id,
            },
        }

        # Persist + publish display_only nudge so the user sees it in
        # chat AFTER it's on disk (visible ⇒ persisted). Router handles
        # display_only=True → transcript-only. When the caller is a task
        # agent, conv_id is the sub-conv (parent::task::tid) but SSE must
        # go to the parent conv.
        _sse_cid = conv_id.split("::task::")[0] if "::task::" in conv_id else conv_id
        from core.conversation_writer import ConversationWriter
        from core.llm_client import stamp_message
        _nudge_msg = stamp_message({
            "role": "user",
            "content": text,
            "msg_id": msg_id,
            "display_only": True,
            "source": _source,
        }, conv_id)
        _sse_evt = {
            "type": "new_message",
            "cid": _sse_cid,
            "data": {
                "role": "user",
                "content": text,
                "msg_id": msg_id,
                "display_only": True,
                "source": _source,
            },
        }
        try:
            ConversationWriter.for_conversation(conv_id).enqueue_message(
                _nudge_msg, agent_name=caller_agent, user_id=user_id,
                sse_events=[_sse_evt])
        except Exception as e:
            logger.error("[bg-delegate] persist nudge failed: %s", e, exc_info=True)

        # Check caller state via AgentLoopTask._active_contexts.
        from tasks.ai.agent_loop import AgentLoopTask
        inst = AgentLoopTask._live_instance
        if not inst:
            logger.warning(
                "[bg-delegate] no AgentLoopTask instance — cannot deliver "
                "result for task %s to caller %s", task_id, caller_agent)
            return

        _key = f"{conv_id}:{caller_agent}" if caller_agent else conv_id
        with inst._active_contexts_lock:
            _is_running = _key in inst._active_contexts

        if _is_running:
            # Preempt path: append to the caller's pending queue so the
            # active loop injects it on its next turn boundary.
            logger.info(
                "[bg-delegate] caller '%s' is running — preempting with "
                "result for task %s", caller_agent, task_id)
            self._preempt_caller(inst, conv_id, caller_agent, text, msg_id, _source)
        else:
            # Wake path: no active loop → spawn a fresh stream so the
            # caller reads + reacts to the result.
            logger.info(
                "[bg-delegate] caller '%s' is idle — waking with result "
                "for task %s", caller_agent, task_id)
            self._wake_caller(inst, conv_id, caller_agent, user_id, text, msg_id)

    @staticmethod
    def _preempt_caller(inst, conv_id, caller_agent, text, msg_id, source):
        """Append the delegate result to the caller's PendingQueue — the
        running agent loop will drain it at its next turn boundary."""
        try:
            from core.pending_queue import PendingQueue
            from core.llm_client import stamp_message
            msg = stamp_message({
                "role": "user",
                "content": text,
                "source": source or {"type": "agent_delegate"},
                "msg_id": msg_id or None,
            }, conv_id)
            PendingQueue.for_agent(conv_id, caller_agent or "").enqueue(
                msg, source="delegate_reply")
        except Exception as e:
            logger.error("[bg-delegate] preempt failed: %s", e)

    @staticmethod
    def _wake_caller(inst, conv_id, caller_agent, user_id, text, msg_id,
                     source=None):
        """Wake an idle caller by running a fresh agent loop with the
        result as the user input. `source` (if given) identifies the
        trigger so the agent loop can set ctx._turn_mode accordingly
        (e.g. agent_delegate → delegate_reply mode auto-tags the flush)."""
        try:
            from core import FlowFile
            body = json.dumps({
                "message": text,
                "conversation_id": conv_id,
                "msg_id": msg_id,
                "target_agent": caller_agent,
            })
            ff = FlowFile(body.encode("utf-8"))
            ff.set_attribute("http.auth.principal", user_id)
            ff.set_attribute("target_agent", caller_agent)
            # The caller already pre-persisted the nudge via writer
            # (see _deliver_to_caller / _deliver_shared_delegate) — tell
            # agent_streaming.py to skip its own pre-persist so we don't
            # write the same msg_id twice.
            ff.set_attribute("skip_pre_persist", "1")
            if source:
                ff.set_attribute("message_source", json.dumps(source))
            # Run in a thread so we don't block the completion callback
            # (which is running on the SubAgentExecutor's pool).
            import threading as _th
            _th.Thread(
                target=inst._execute_streaming,
                args=(ff,),
                daemon=True,
                name=f"wake-{caller_agent}",
            ).start()
        except Exception as e:
            logger.error("[bg-delegate] wake failed: %s", e)


class FlashAgentHandler(SpawnAgentsHandler):
    """Create temporary task-specific agents and delegate to them."""

    @property
    def name(self) -> str:
        return "flash_delegate"

    @property
    def description(self) -> str:
        return (
            "Create temporary flash agents for independent parallel work. "
            "Each flash agent starts with an empty context, uses the "
            "calling agent's current llm_service, runs asynchronously, "
            "and disappears when its delegated task completes. Include all "
            "context the flash agent needs in its prompt and message."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "Short temporary agent name chosen by the caller",
                            },
                            "prompt": {
                                "type": "string",
                                "description": "System instructions for this temporary agent",
                            },
                            "message": {
                                "type": "string",
                                "description": "The task/message to send to the flash agent",
                            },
                            "id": {
                                "type": "string",
                                "description": "Optional task ID for tracking",
                            },
                            "tools": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Optional tool whitelist for the flash agent",
                            },
                            "skills": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Optional skills to inject into the flash prompt",
                            },
                        },
                        "required": ["name", "prompt", "message"],
                    },
                    "description": "Temporary agents to create and run in parallel",
                },
            },
            "required": ["tasks"],
        }

    @staticmethod
    def _runtime_name(parent_agent: str, flash_name: str) -> str:
        parent = re.sub(r"[^A-Za-z0-9_.-]+", "_", parent_agent or "agent").strip("_")
        name = re.sub(r"[^A-Za-z0-9_.-]+", "_", flash_name or "flash").strip("_")
        if not name:
            name = "flash"
        return f"{parent or 'agent'}::flash::{name}"

    def execute(self, arguments: Dict[str, Any]) -> str:
        if not self._client_resolver:
            return "Error: Agent executor not configured (missing client_resolver)."

        from core.agent_executor import AgentTask, SubAgentExecutor
        from core.handlers._arg_normalize import validate_object_list, normalize_string_list
        from core.agent_prompt_policy import inject_common_agent_system_prompt
        import uuid

        tasks_spec, err = validate_object_list(
            arguments.get("tasks"),
            param_name="tasks",
            required_keys=["name", "prompt", "message"],
            example=('tasks=[{"name": "critic", "prompt": "<role>", '
                     '"message": "<task>", "id"?: "<optional>"}, ...]'),
        )
        if err:
            return f"Error: {err}"

        user_id = self._user_id
        raw_conv_id = self._conversation_id
        parent_conv_id = raw_conv_id
        source_task_id = ""
        if "::task::" in raw_conv_id:
            parent_conv_id = raw_conv_id.split("::task::")[0]
            source_task_id = raw_conv_id.split("::task::", 1)[1]

        src_agent = getattr(self._local, 'source_agent', '') or ''
        src_svc = getattr(self._local, 'source_llm_service', '') or ''
        delegate_tc_id = getattr(self._local, 'delegate_tc_id', '') or ''
        if not src_agent or not src_svc:
            return "Error: BUG: flash_delegate requires an active source agent and llm_service."

        from core.agent_executor import get_live_delegate, queue_live_delegate_message
        agent_tasks = []
        injected_results = []
        seen_runtime_names = set()
        for spec in tasks_spec:
            flash_name = str(spec.get("name", "")).strip()
            prompt = str(spec.get("prompt", "")).strip()
            message = str(spec.get("message", ""))
            if not flash_name or not prompt or not message:
                return "Error: each flash task requires non-empty name, prompt, and message"
            runtime_name = self._runtime_name(src_agent, flash_name)
            if runtime_name in seen_runtime_names:
                return f"Error: duplicate flash agent name '{flash_name}' in one call"
            seen_runtime_names.add(runtime_name)
            task_id = spec.get("id", uuid.uuid4().hex[:8])

            if parent_conv_id:
                live = get_live_delegate(parent_conv_id, src_agent, runtime_name)
                if live:
                    live_client = live.get("client")
                    live_tid = live.get("task_id", "")
                    delivered = False
                    if live_client and hasattr(live_client, "send_user_message"):
                        try:
                            delivered = bool(live_client.send_user_message(message))
                        except Exception as exc:
                            logger.warning(
                                "[flash-delegate] preempt to %s failed: %s",
                                live_tid, exc)
                    if not delivered:
                        queue_live_delegate_message(
                            parent_conv_id, src_agent, runtime_name, message)
                    injected_results.append({
                        "task_id": live_tid,
                        "name": flash_name,
                        "agent": runtime_name,
                        "status": "injected" if delivered else "injected_queued",
                    })
                    continue

            identity = (
                f"[IDENTITY] You are temporary flash agent \"{flash_name}\". "
                f"Runtime id: \"{runtime_name}\". You were created by "
                f"agent \"{src_agent}\" for one delegated task. You start "
                f"with empty context and disappear when this task completes.\n\n"
            )
            system_prompt = inject_common_agent_system_prompt(identity + prompt)
            skills = normalize_string_list(spec.get("skills"))
            if skills:
                from core.skill_resolver import inject_available_skills_into_prompt
                system_prompt = inject_available_skills_into_prompt(
                    system_prompt, skills, user_id,
                    conversation_id=self._conversation_id)
            tools = normalize_string_list(spec.get("tools")) or None
            task = AgentTask(
                id=task_id,
                agent_name=runtime_name,
                message=message,
                system_prompt=system_prompt,
                tools=tools,
                max_iterations=50,
                max_depth=1000,
                timeout=180,
                llm_service=src_svc,
                user_id=user_id,
                source_agent=src_agent,
                source_llm_service=src_svc,
                context_mode="isolated",
                parent_conversation_id=parent_conv_id,
                delegate_tc_id=delegate_tc_id,
                persist=False,
                source_task_id=source_task_id,
            )
            agent_tasks.append(task)

        if not agent_tasks:
            if injected_results:
                return json.dumps(injected_results, ensure_ascii=False, indent=2)
            return "Error: no valid flash tasks to spawn."

        if self._on_event and delegate_tc_id:
            self._on_event("delegate_group_start", {
                "delegate_tc_id": delegate_tc_id,
                "source_agent": src_agent,
                "mode": "flash",
                "agents": [
                    {"name": t.agent_name, "task_id": t.id,
                     "message": t.message, "llm_service": t.llm_service}
                    for t in agent_tasks
                ],
                "total": len(agent_tasks),
                "source_task_id": source_task_id,
            })

        executor = SubAgentExecutor(
            self._default_client, self._registry, max_workers=4,
            client_resolver=self._client_resolver,
            on_event=self._on_event,
        )
        result_conv_id = raw_conv_id
        def _bg_callback(result, task):
            self._inject_bg_result(result, task, result_conv_id, user_id, src_agent)

        results = executor.spawn(agent_tasks, wait=False,
                                 on_bg_complete=_bg_callback)
        spawned = [
            {"task_id": r.task_id, "agent": t.agent_name,
             "name": t.agent_name.split("::flash::", 1)[-1]}
            for r, t in zip(results, agent_tasks)
        ]
        reply = {
            "status": "spawned",
            "flash_agents": spawned,
            "message": (
                f"Spawned {len(spawned)} flash agent(s) in background. "
                "You are not blocked. Read and integrate each delegate "
                "result when it returns."
            ),
        }
        if injected_results:
            reply["injected"] = injected_results
        return json.dumps(reply, ensure_ascii=False)


class ShowFileHandler(ToolHandler):
    """Display a file in the chat UI viewer (images, PDFs, text, code)."""

    def __init__(self):
        self._base_url = "http://localhost:9090"
        self._user_id = ""

    @property
    def name(self) -> str:
        return "show_file"

    @property
    def description(self) -> str:
        return (
            "Display a file to the USER in their chat viewer panel.\n\n"
            "This opens a file in the user's UI — it does NOT return the file content to you. "
            "Use this when the user asks to SEE something (an image, a PDF, a code file, a chart). "
            "If YOU need to analyze or read the file content yourself, use 'see' or 'read' instead.\n\n"
            "Key parameters:\n"
            "- file_id: The FileStore file ID (from execute_script output, upload results, etc.).\n"
            "- filename: Alternative to file_id — search by filename in FileStore.\n"
            "- path + service: Show a file from a filesystem service (relay). Provide both "
            "the file path and the service name.\n\n"
            "Supports images (PNG, JPG, SVG), PDFs, code files, text, and other file types "
            "that the chat UI can render. The file must exist in FileStore or on the specified "
            "filesystem service."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_id": {
                    "type": "string",
                    "description": "FileStore file ID",
                },
                "filename": {
                    "type": "string",
                    "description": "Filename to search for in FileStore",
                },
                "path": {
                    "type": "string",
                    "description": "File path on a filesystem service (e.g. 'assets/player.png')",
                },
                "service": {
                    "type": "string",
                    "description": "Filesystem service name (e.g. 'localFS') — required when using path",
                },
            },
        }

    def set_base_url(self, url: str):
        self._base_url = url.rstrip("/")

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def _find_fs_service(self, service_name: str):
        """Find a filesystem service by name (conv > user > global)."""
        try:
            from core.service_registry import ServiceRegistry
            return ServiceRegistry.get_instance().resolve(
                service_name, user_id=self._user_id)
        except Exception:
            return None

    def execute(self, arguments: Dict[str, Any]) -> str:
        from core.file_store import FileStore
        import mimetypes

        store = FileStore.instance()
        file_id = arguments.get("file_id", "")
        filename = arguments.get("filename", "")
        fs_path = arguments.get("path", "")
        fs_service = arguments.get("service", "")

        if file_id:
            # Extract file_id from URL if needed
            import re as _re_sf
            url_match = _re_sf.search(r'/files/([a-f0-9]{12})', file_id)
            if url_match:
                file_id = url_match.group(1)
            result = store.get(file_id, user_id=self._user_id)
            if not result:
                # Try by name
                found_id = store.find_by_name(file_id, user_id=self._user_id)
                if found_id:
                    result = store.get(found_id, user_id=self._user_id)
                    file_id = found_id
            if not result:
                return f"Error: File ID '{file_id}' not found."
            fname, data, content_type = result
        elif fs_path:
            # Read from filesystem service, cache in FileStore
            svc = self._find_fs_service(fs_service) if fs_service else None
            if not svc:
                return f"Error: Filesystem service '{fs_service}' not found or not connected."
            try:
                data = svc.read_file(fs_path)
            except Exception as e:
                return f"Error reading '{fs_path}' from {fs_service}: {e}"
            fname = fs_path.rsplit("/", 1)[-1] if "/" in fs_path else fs_path
            content_type = mimetypes.guess_type(fname)[0] or "application/octet-stream"
            # Store in FileStore for the viewer URL
            file_id = store.store(fname, data, content_type=content_type,
                                  user_id=self._user_id,
                                  conversation_id=getattr(self, '_conversation_id', '') or '')
        elif filename:
            # Search by filename in FileStore
            found = None
            for f in store.list_files(user_id=self._user_id):
                if f["filename"] == filename:
                    found = f
                    break
            if not found:
                # Fuzzy search
                found_id = store.find_by_name(filename, user_id=self._user_id)
                if found_id:
                    found = {"file_id": found_id, "filename": filename}
            if not found:
                return (f"Error: File '{filename}' not found in FileStore. "
                        f"Use path+service to show files from a filesystem service.")
            file_id = found["file_id"]
            fname = found["filename"]
            result = store.get(file_id, user_id=self._user_id)
            if not result:
                return f"Error: Could not load file '{filename}'."
            fname, data, content_type = result
        else:
            return "Error: Provide file_id, filename, or path+service."

        url = f"fs://filestore/{file_id}/{fname}"
        size_kb = len(data) / 1024

        # Return a special marker that the chat UI will intercept
        return json.dumps({
            "__show_file__": True,
            "url": url,
            "filename": fname,
            "content_type": content_type,
            "size_kb": round(size_kb, 1),
            "file_id": file_id,
        })

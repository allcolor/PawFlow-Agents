"""ManageResourceHandler — extracted from resource_agent.py (<=800 lines).

Re-exported from core.handlers.resource_agent for import stability.
"""

import json
import logging
from typing import Any, Dict

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
            "- resolve_import_source: Resolve a GitHub repo into importable skill paths\n"
            "- import_marketplace: Review/import an external Agent Skill\n"
            "- assign_skill: Assign a skill to an agent and notify it\n"
            "- unassign_skill: Remove a skill from an agent and notify it\n"
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
                             "get", "review", "search_marketplace", "resolve_import_source",
                             "import_marketplace", "assign_skill", "unassign_skill",
                             "activate", "deactivate"],
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
                    "description": "Marketplace source for skill search/import: codex, claude, hermes, openclaw, github, all.",
                },
                "query": {
                    "type": "string",
                    "description": "Search query for skill marketplace search.",
                },
                "ref": {
                    "type": "string",
                    "description": "Marketplace skill ref, GitHub repo, or GitHub tree URL for skill import.",
                },
                "selected_ref": {
                    "type": "string",
                    "description": "Git branch or tag to inspect when resolving a skill import source.",
                },
                "path": {
                    "type": "string",
                    "description": "Repository subdirectory to inspect when resolving a skill import source.",
                },
                "agent_name": {
                    "type": "string",
                    "description": "Agent name for assign_skill/unassign_skill.",
                },
                "skill_name": {
                    "type": "string",
                    "description": "Skill name for assign_skill/unassign_skill. Defaults to name.",
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
        from core.resource_store import ResourceStore
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

                if scope == "global":
                    return "Error: Global resource writes require admin scope. Use the admin UI/API."

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
                if (self._agent_name and existing
                        and existing.get("_scope") != "conversation"):
                    return (f"Error: {rtype} '{name}' is read-only in "
                            f"{existing.get('_scope', 'global')} scope for agents. "
                            "Create or edit a conversation-scoped copy instead.")
                if existing and existing.get("_scope") == "global":
                    return "Error: Global resource writes require admin scope. Use the admin UI/API."
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
                        if (self._agent_name
                                and existing.get("_scope") != "conversation"):
                            return (f"Error: {rtype} '{name}' is read-only in "
                                    f"{existing.get('_scope', 'global')} scope for agents. "
                                    "Create or edit a conversation-scoped copy instead.")
                        if existing.get("_scope") == "global":
                            return "Error: Global resource writes require admin scope. Use the admin UI/API."
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
                scope = str(arguments.get("scope", "user") or "user")
                if self._conversation_id and self._agent_name:
                    scope = "conversation"
                if scope == "global":
                    return "Error: Global resource writes require admin scope. Use the admin UI/API."
                result = import_marketplace_skill(
                    source=str(arguments.get("source", "") or ""),
                    ref=ref,
                    name=name,
                    user_id=user_id,
                    conversation_id=self._conversation_id,
                    review_only=bool(arguments.get("review_only", False)),
                    force=bool(arguments.get("force", False)),
                    scope=scope,
                )
                return json.dumps(result, ensure_ascii=False, indent=2)

            elif action == "resolve_import_source":
                if rtype != "skill":
                    return "Error: resolve_import_source is only supported for skills"
                ref = str(arguments.get("ref", "") or "")
                if not ref:
                    return "Error: 'ref' is required for resolve_import_source"
                from core.skill_marketplace import resolve_skill_import_source
                result = resolve_skill_import_source(
                    ref=ref,
                    selected_ref=str(arguments.get("selected_ref", "") or ""),
                    path=str(arguments.get("path", "") or ""),
                    limit=int(arguments.get("limit", 40) or 40),
                )
                return json.dumps(result, ensure_ascii=False, indent=2)

            elif action in {"assign_skill", "unassign_skill"}:
                if rtype != "skill":
                    return f"Error: {action} is only supported for skills"
                agent_name = str(arguments.get("agent_name") or arguments.get("target_agent") or "").strip()
                skill_name = str(arguments.get("skill_name") or name or "").strip()
                if not agent_name or not skill_name:
                    return "Error: 'agent_name' and 'skill_name' are required"
                if action == "assign_skill":
                    from core.skill_lifecycle import assign_skill_to_agent
                    result = assign_skill_to_agent(
                        agent_name, skill_name, user_id, self._conversation_id,
                        resource_store=store,
                        conversation_store=ConversationStore.instance(),
                        source="skill_assign")
                else:
                    from core.skill_lifecycle import unassign_skill_from_agent
                    result = unassign_skill_from_agent(
                        agent_name, skill_name, user_id, self._conversation_id,
                        resource_store=store,
                        conversation_store=ConversationStore.instance(),
                        source="skill_unassign")
                return json.dumps(result, ensure_ascii=False, indent=2)

            elif action == "activate":
                if not name:
                    return "Error: 'name' is required for activate"
                if rtype == "skill":
                    return "Error: skills are injected only through agent.assigned_skills. Use /skill assign @agent @skill."
                if store.get_any(rtype, name, user_id,
                                 conversation_id=self._conversation_id) is None:
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



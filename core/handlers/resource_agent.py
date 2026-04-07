"""Auto-extracted from core/tool_registry.py — see core/handlers/__init__.py"""

import json
import logging
import re
import threading
from typing import Dict, Any, List, Optional

from core.tool_handler import ToolHandler

logger = logging.getLogger(__name__)



class ManageResourceHandler(ToolHandler):
    """CRUD for user resources: agents, skills, MCP servers, prompts.

    Both users (via slash commands) and agents (via tool calls) can manage
    resources. Resources are user-scoped and persist in config/ JSON files.
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
            "Manage user resources (agents, skills, MCP servers, prompts). Actions:\n"
            "- create: Create a new resource\n"
            "- update: Modify an existing resource\n"
            "- delete: Delete a resource\n"
            "- list: List all resources of a type\n"
            "- get: Get details of a specific resource\n"
            "- activate: Activate a resource in the current conversation\n"
            "- deactivate: Deactivate a resource from the current conversation\n\n"
            "Resource types: agent, skill, mcp, prompt\n\n"
            "Agent fields: prompt (required), model, tools (list), "
            "max_depth, timeout, description, llm_service\n"
            "Skill fields: prompt (required), description\n"
            "MCP fields: url (required), auth (dict)\n"
            "Prompt fields: content (required), title, category, description"
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create", "update", "delete", "list",
                             "get", "activate", "deactivate"],
                    "description": "Action to perform",
                },
                "resource_type": {
                    "type": "string",
                    "enum": ["agent", "skill", "mcp", "prompt", "task_def"],
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
                # Profile shortcut for agents: resolve llm_service from profile
                if rtype == "agent" and isinstance(data, dict):
                    profile_name = data.pop("profile", "")
                    if profile_name:
                        from core.llm_profiles import apply_profile, get_profile_info
                        try:
                            profile_config = apply_profile(
                                profile_name,
                                overrides={"default_model": data.pop("model", "") or ""},
                            )
                            # Install a service named after the profile if not yet installed
                            svc_id = f"_profile_{profile_name}"
                            try:
                                from core.resource_store import ResourceStore as _RS
                                from gui.services.user_service_registry import UserServiceRegistry as _USR
                                _reg = _USR.get_instance()
                                if not _reg.get_definition(user_id, svc_id):
                                    _reg.install(
                                        user_id=user_id,
                                        service_id=svc_id,
                                        service_type="llmConnection",
                                        config=profile_config,
                                        description=get_profile_info(profile_name).get("description", ""),
                                    )
                            except Exception as _se:
                                pass
                            if not data.get("llm_service"):
                                data["llm_service"] = svc_id
                        except ValueError as _pe:
                            return f"Error: {_pe}"
                if rtype in ("agent", "skill") and self._agent_name:
                    data["_created_by"] = self._agent_name
                if rtype == "agent" and not data.get("llm_service") and self._llm_service:
                    data["llm_service"] = self._llm_service
                if scope == "conversation" and self._conversation_id:
                    # Store in conversation extras
                    from core.conversation_store import ConversationStore
                    cs = ConversationStore.instance()
                    if rtype == "task_def":
                        conv_defs = cs.get_extra(self._conversation_id, "conversation_task_defs") or {}
                        conv_defs[name] = data
                        cs.set_extra(self._conversation_id, "conversation_task_defs", conv_defs)
                    else:
                        conv_agents = cs.get_extra(self._conversation_id, "conversation_agents") or {}
                        conv_agents[name] = data
                        cs.set_extra(self._conversation_id, "conversation_agents", conv_agents)
                else:
                    store.create(rtype, name, user_id, data)
                self._activate_resource(rtype, name)
                creator = f" (by {self._agent_name})" if self._agent_name else ""
                return f"Created {rtype} '{name}' (scope: {scope}).{creator}"

            elif action == "update":
                if not name:
                    return "Error: 'name' is required for update"
                store.update(rtype, name, user_id, data)
                return f"Updated {rtype} '{name}'."

            elif action == "delete":
                if not name:
                    return "Error: 'name' is required for delete"
                # Ownership check for agent/skill deletion
                if rtype in ("agent", "skill"):
                    existing = store.get_any(rtype, name, user_id)
                    if existing:
                        created_by = existing.get("created_by")  # None if legacy
                        if created_by is not None and created_by != (self._agent_name or ""):
                            return (f"Error: {rtype} '{name}' was created by "
                                    f"'{created_by}' — you can only delete "
                                    f"resources you created.")
                if store.delete(rtype, name, user_id):
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

            elif action == "activate":
                if not name:
                    return "Error: 'name' is required for activate"
                if store.get_any(rtype, name, user_id) is None:
                    return f"{rtype} '{name}' not found."
                self._activate_resource(rtype, name)
                return f"Activated {rtype} '{name}' in this conversation."

            elif action == "deactivate":
                if not name:
                    return "Error: 'name' is required for deactivate"
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

    def set_available_agents(self, names: List[str]):
        """Set the list of available agent names (for description injection)."""
        self._available_agents = list(names)

    @property
    def name(self) -> str:
        return "delegate"

    @property
    def description(self) -> str:
        base = (
            "Delegate tasks to one or more agents. "
            "Each agent runs independently with its own LLM service and tools. "
            "Waits for all results by default (wait=true). "
            "Set wait=false to run in background."
        )
        if self._available_agents:
            base += (
                f"\n\nAvailable agents: {', '.join(self._available_agents)}. "
                f"Use these exact names in the 'agent' field."
            )
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
                                "description": "Context mode: 'isolated' (default), 'last:N' (last N messages), 'summary:N' (summary of N tokens), 'full' (entire parent context)",
                            },
                            "skills": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Skill names to inject into the delegate agent's prompt (replaces the agent's own assigned_skills)",
                            },
                        },
                        "required": ["agent", "message"],
                    },
                    "description": "List of tasks to spawn",
                },
                "wait": {
                    "type": "boolean",
                    "description": "Wait for all results (default: true).",
                },
            },
            "required": ["tasks"],
        }

    def set_user_id(self, uid: str):
        self._user_id = uid

    def execute(self, arguments: Dict[str, Any]) -> str:
        if not self._client_resolver or not self._default_client:
            return "Error: Agent executor not configured (missing client_resolver)."

        from core.agent_executor import resolve_agent_task, SubAgentExecutor
        import uuid

        tasks_spec = arguments.get("tasks", [])
        wait = arguments.get("wait", True)
        user_id = self._user_id

        # Thread-safe source agent (each agent loop runs in its own thread)
        _src_agent = getattr(self._local, 'source_agent', '') or ''
        _src_svc = getattr(self._local, 'source_llm_service', '') or ''

        # Resolve self-name and nicknames to detect self-calls
        _self_names = {_src_agent.lower()} if _src_agent else set()
        _src_nickname = ""
        if self._conversation_id and _src_agent:
            try:
                from core.conversation_store import ConversationStore
                _nicks = ConversationStore.instance().get_extra(
                    self._conversation_id, "agent_nicknames") or {}
                _src_nickname = _nicks.get(_src_agent, "")
                if _src_nickname:
                    _self_names.add(_src_nickname.lower())
            except Exception:
                pass

        agent_tasks = []
        for spec in tasks_spec:
            agent_name = spec.get("agent", "")
            message = spec.get("message", "")
            task_id = spec.get("id", uuid.uuid4().hex[:8])

            try:
                extra_skills = spec.get("skills") or []
                task = resolve_agent_task(agent_name, message, user_id,
                                         conversation_id=self._conversation_id,
                                         extra_skills=extra_skills)
                task.id = task_id
                task.source_agent = _src_agent
                task.source_agent_nickname = _src_nickname
                task.source_llm_service = _src_svc

                # Resolve context mode
                context_mode = spec.get("context", "isolated")
                task.context_mode = context_mode
                task.parent_conversation_id = self._conversation_id

                if context_mode != "isolated" and self._conversation_id:
                    task.context_messages = self._resolve_context(
                        context_mode, self._conversation_id, user_id)

                # Prevent agent from calling itself
                if agent_name.lower() in _self_names:
                    return (f"Error: You ('{_src_agent}' via {_src_svc}) "
                            f"cannot call yourself as '{agent_name}' (via {task.llm_service}). "
                            f"Use a different agent or respond directly.")

                agent_tasks.append(task)
            except KeyError as e:
                return f"Error: {e}"

        if not agent_tasks:
            return "Error: no valid tasks to spawn."

        # Create executor on-the-fly
        executor = SubAgentExecutor(
            self._default_client, self._registry, max_workers=4,
            client_resolver=self._client_resolver,
            on_event=self._on_event,
        )
        results = executor.spawn(agent_tasks, wait=wait)

        if not wait:
            ids = [r.task_id for r in results]
            return json.dumps({
                "status": "spawned",
                "task_ids": ids,
                "message": f"Spawned {len(ids)} agents. Use get_agent_results to check.",
            })

        # Format results
        output = []
        for r in results:
            entry = {
                "task_id": r.task_id,
                "agent": r.agent_name,
                "status": r.status,
            }
            if r.response:
                entry["response"] = r.response
            if r.error:
                entry["error"] = r.error
            entry["tokens"] = {"in": r.tokens_in, "out": r.tokens_out}
            entry["tools_called"] = r.tools_called
            output.append(entry)

        return json.dumps(output, ensure_ascii=False, indent=2)

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


class GetAgentResultsHandler(ToolHandler):
    """Retrieve results from previously spawned background agents."""

    def __init__(self):
        pass

    @property
    def name(self) -> str:
        return "get_agent_results"

    @property
    def description(self) -> str:
        return (
            "Get results from agents spawned with wait=false. "
            "Pass the task_ids returned by delegate."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of task IDs to check",
                },
            },
            "required": ["task_ids"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        return "Error: get_agent_results is not supported. Use delegate with wait=true (default)."



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
        """Find a filesystem service by name (global or user)."""
        try:
            from gui.services.global_service_registry import GlobalServiceRegistry
            svc = GlobalServiceRegistry.get_instance().get_live_instance(service_name)
            if svc:
                return svc
        except Exception:
            pass
        if self._user_id:
            try:
                from gui.services.user_service_registry import UserServiceRegistry
                svc = UserServiceRegistry.get_instance().get_live_instance(
                    self._user_id, service_name)
                if svc:
                    return svc
            except Exception:
                pass
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
            url_match = _re_sf.search(r'/files/([^/]+)/', file_id)
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
                                  user_id=self._user_id)
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

        url = f"{self._base_url}/files/{file_id}/{fname}"
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

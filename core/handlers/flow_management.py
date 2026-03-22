"""Auto-extracted from core/tool_registry.py — see core/handlers/__init__.py"""

import json
import logging
import re
import threading
import uuid
from typing import Dict, Any, List, Optional

from core.tool_handler import ToolHandler

logger = logging.getLogger(__name__)



class CreateToolHandler(ToolHandler):
    """Create a new reusable tool from Python code.

    The agent can code its own tools and install them for future use.
    The code must define a ToolHandler subclass with name, description,
    parameters_schema, and execute method.
    Tools are tagged with conversation_id for lifecycle management.
    """

    def __init__(self):
        self._user_id = ""
        self._conversation_id = ""

    @property
    def name(self) -> str:
        return "create_tool"

    @property
    def description(self) -> str:
        return (
            "Create a new reusable tool by writing Python code. The code must "
            "define a class that inherits from ToolHandler with: name (property), "
            "description (property), parameters_schema (property returning a JSON "
            "Schema dict), and execute(self, arguments) method returning a string. "
            "The tool will be validated, sandboxed, and made available immediately. "
            "Allowed imports: math, datetime, json, re, collections, requests, etc. "
            "Forbidden: os, subprocess, eval, exec, open, file I/O."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "tool_name": {
                    "type": "string",
                    "description": "Short name for the tool file (e.g. 'csv_converter')",
                },
                "source_code": {
                    "type": "string",
                    "description": "Python source code defining a ToolHandler subclass",
                },
            },
            "required": ["tool_name", "source_code"],
        }

    def set_user_id(self, uid: str):
        self._user_id = uid

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    def execute(self, arguments: Dict[str, Any]) -> str:
        tool_name = arguments.get("tool_name", "")
        source = arguments.get("source_code", "")
        if not tool_name or not source:
            return "Error: tool_name and source_code are required"

        user_id = self._user_id or "agent"
        try:
            from core.dynamic_tool_store import DynamicToolStore
            result = DynamicToolStore.instance().install(
                user_id, f"{tool_name}.py", source,
                conversation_id=self._conversation_id,
            )
            return (
                f"Tool '{result['tool_name']}' created successfully!\n"
                f"Description: {result['description']}\n"
                f"You can now call it like any other tool."
            )
        except ValueError as e:
            return f"Tool creation failed:\n{e}"


class FlowManagerHandler(ToolHandler):
    """Manage PawFlow flows — create, start, stop, delete.

    The agent can only manage flows it created (tagged with user_id).
    Flows are scoped to the current conversation.
    Flow definitions are standard PawFlow JSON flow format.
    """

    def __init__(self):
        self._user_id = ""
        self._conversation_id = ""

    @property
    def name(self) -> str:
        return "manage_flow"

    @property
    def description(self) -> str:
        return (
            "Manage PawFlow data flows. Actions:\n"
            "- catalog: List available flow templates from the repository\n"
            "- deploy: Deploy an existing template as a new instance\n"
            "- list: List flow instances in this conversation\n"
            "- list_all: List all your flow instances across conversations\n"
            "- create: Create a new flow from a JSON definition\n"
            "- start: Start a stopped flow instance\n"
            "- stop: Stop a running flow instance\n"
            "- status: Get flow instance status\n"
            "- update: Update flow instance parameters\n"
            "- delete: Delete a flow instance\n\n"
            "IMPORTANT — Flow JSON structure for 'create' action:\n"
            "The 'definition' object MUST have this EXACT top-level structure:\n"
            "{\n"
            '  "id": "my-flow-id",\n'
            '  "name": "My Flow Name",\n'
            '  "version": "1.0.0",\n'
            '  "parameters": {},\n'
            '  "tasks": {\n'
            '    "taskA": {"type": "cronTrigger", "parameters": {"schedule": "0 7 * * *"}},\n'
            '    "taskB": {"type": "fetchHTTP", "parameters": {"url": "..."}},\n'
            '    "taskC": {"type": "executeScript", "parameters": {"script": "..."}},\n'
            '    "taskD": {"type": "sendEmail", "parameters": {"to": "...", ...}}\n'
            "  },\n"
            '  "relations": [\n'
            '    {"from": "taskA", "to": "taskB", "type": "success"},\n'
            '    {"from": "taskB", "to": "taskC", "type": "success"},\n'
            '    {"from": "taskC", "to": "taskD", "type": "success"}\n'
            "  ],\n"
            '  "services": {}\n'
            "}\n\n"
            "RULES:\n"
            "- Each task is a SEPARATE key in the top-level 'tasks' dict\n"
            "- Do NOT nest tasks inside other tasks\n"
            "- 'relations' is a top-level array (NOT inside tasks)\n"
            "- Each relation has 'from', 'to', and 'type' (success/failure/all)\n"
            "- Services use 'parameters' (NOT 'config')\n"
            "- For scheduled flows, use cronTrigger as root task (NOT generateFlowFile)\n"
            "- generateFlowFile fires ONCE then the flow auto-stops\n"
            "- ROUTING: each output FlowFile is CLONED to ALL matching outgoing relations\n"
            "- To fan out to 2+ branches: add multiple relations from the SAME task\n"
            "- Do NOT use duplicateContent to fan out — it multiplies copies × relations\n"
            "- mergeContent: params are 'separator' (NOT 'delimiter'), 'min_entries'\n"
            "- sendEmail params: 'to', 'from', 'subject', 'smtp_host', 'smtp_port', 'use_tls', "
            "'auth_type' (password|oauth2), 'username', 'password', "
            "'oauth2_client_id', 'oauth2_client_secret', 'oauth2_refresh_token', "
            "'content_type' (text/plain|text/html), 'cc', 'bcc'\n"
            "- inferLLM: can use 'service' param to reference an llmConnection service "
            "(no need for api_key/provider/base_url when service is set)\n"
            "- Available task types: cronTrigger, generateFlowFile, fetchHTTP, "
            "executeScript, sendEmail, inferLLM, log, parseJSON, transformJSON, "
            "updateAttribute, routeOnAttribute, routeOnContent, mergeContent, "
            "splitContent, filterAttribute, replaceText, hashContent, validateJSON, "
            "scraplingFetch, agentLoop, httpReceiver, handleHTTPResponse, duplicateContent\n"
            "- You can only manage flows you created/deployed."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["catalog", "deploy", "list", "list_all", "create",
                             "start", "stop", "status", "update", "delete"],
                    "description": "Action to perform",
                },
                "flow_id": {
                    "type": "string",
                    "description": "Flow instance ID (for start/stop/status/update/delete)",
                },
                "template_id": {
                    "type": "string",
                    "description": "Template flow ID from catalog (for deploy action)",
                },
                "definition": {
                    "type": "object",
                    "description": (
                        "Flow JSON definition (for create action). "
                        "MUST have top-level keys: id (string), name (string), "
                        "tasks (object with each task as a separate key), "
                        "relations (array of {from, to, type} objects). "
                        "Do NOT nest tasks inside other tasks. "
                        "Do NOT put relations inside tasks. "
                        "Services use 'parameters' not 'config'."
                    ),
                },
                "parameters": {
                    "type": "object",
                    "description": "Flow parameters to set on start",
                },
            },
            "required": ["action"],
        }

    def set_user_id(self, uid: str):
        self._user_id = uid

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    def execute(self, arguments: Dict[str, Any]) -> str:
        action = arguments.get("action", "")
        flow_id = arguments.get("flow_id", "")

        if action == "catalog":
            return self._catalog()
        elif action == "deploy":
            template_id = arguments.get("template_id", "")
            params = arguments.get("parameters", {})
            return self._deploy_template(template_id, params)
        elif action == "list":
            return self._list_flows(conversation_only=True)
        elif action == "list_all":
            return self._list_flows(conversation_only=False)
        elif action == "create":
            definition = arguments.get("definition", {})
            return self._create_flow(definition)
        elif action == "start":
            params = arguments.get("parameters", {})
            return self._start_flow(flow_id, params)
        elif action == "stop":
            return self._stop_flow(flow_id)
        elif action == "status":
            return self._flow_status(flow_id)
        elif action == "update":
            params = arguments.get("parameters", {})
            return self._update_flow(flow_id, params)
        elif action == "delete":
            return self._delete_flow(flow_id)
        return f"Error: unknown action '{action}'"

    def _get_deployment_registry(self):
        from gui.services.deployment_registry import DeploymentRegistry
        return DeploymentRegistry.get_instance()

    def _owner_tag(self) -> str:
        return self._user_id or None

    @staticmethod
    def _get_template_dirs():
        """Return directories where flow templates can be found."""
        from pathlib import Path
        dirs = [Path("flows")]
        # Also check configured flow directories
        env_dir = __import__("os").environ.get("PAWFLOW_FLOWS_DIR", "")
        if env_dir:
            dirs.append(Path(env_dir))
        return [d for d in dirs if d.exists()]

    def _catalog(self) -> str:
        """List available flow templates from the repository."""
        templates = []
        for tdir in self._get_template_dirs():
            for f in sorted(tdir.glob("*.json")):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    templates.append({
                        "id": data.get("id", f.stem),
                        "name": data.get("name", f.stem),
                        "version": data.get("version", ""),
                        "description": data.get("description", ""),
                        "path": str(f),
                    })
                except Exception:
                    continue
        if not templates:
            return "No flow templates found in the repository."
        lines = []
        for t in templates:
            ver = f" v{t['version']}" if t["version"] else ""
            desc = f" — {t['description']}" if t["description"] else ""
            lines.append(f"- {t['id']}{ver}: {t['name']}{desc}")
        return f"Available templates ({len(templates)}):\n" + "\n".join(lines)

    def _deploy_template(self, template_id: str, params: dict = None) -> str:
        """Deploy a flow template as a new instance in this conversation."""
        if not template_id:
            return "Error: template_id is required"

        # Find the template file
        template_path = None
        template_name = template_id
        for tdir in self._get_template_dirs():
            for f in tdir.glob("*.json"):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    if data.get("id") == template_id or f.stem == template_id:
                        template_path = str(f)
                        template_name = data.get("name", template_id)
                        break
                except Exception:
                    continue
            if template_path:
                break

        if not template_path:
            return (
                f"Error: template '{template_id}' not found. "
                "Use action 'catalog' to see available templates."
            )

        try:
            dep_reg = self._get_deployment_registry()
            instance_id = dep_reg.deploy(
                template_path=template_path,
                owner=self._owner_tag(),
                parameters=params or {},
                source="agent",
                conversation_id=self._conversation_id,
            )
            return (
                f"Template '{template_name}' deployed as instance "
                f"'{instance_id}'. Use start to run it."
            )
        except Exception as e:
            return f"Error deploying template: {e}"

    def _list_flows(self, conversation_only: bool = True) -> str:
        dep_reg = self._get_deployment_registry()
        dep_reg.sync_with_executors()
        owner = self._owner_tag()

        if conversation_only and self._conversation_id:
            instances = dep_reg.get_by_conversation(self._conversation_id, owner=owner)
        else:
            instances = dep_reg.get_by_owner(owner)

        if not instances:
            return "No flows found. Use catalog/deploy or create."

        lines = []
        for inst in instances:
            extras = []
            if inst.flow_id != inst.instance_id:
                extras.append(f"from: {inst.flow_id}")
            suffix = f" ({', '.join(extras)})" if extras else ""
            lines.append(f"- {inst.instance_id}: {inst.flow_name} [{inst.status}]{suffix}")
        return f"Your flow instances ({len(instances)}):\n" + "\n".join(lines)

    def _create_flow(self, definition: Dict) -> str:
        if not definition or "id" not in definition:
            return "Error: definition must include at least 'id' and 'tasks'"

        # Validate structure
        tasks = definition.get("tasks", {})
        if not isinstance(tasks, dict) or not tasks:
            return (
                "Error: 'tasks' must be a dict with each task as a separate key. "
                "Example: {\"taskA\": {\"type\": \"fetchHTTP\", ...}, "
                "\"taskB\": {\"type\": \"log\", ...}}"
            )
        # Check for common LLM mistake: nesting tasks inside other tasks
        for task_key, task_val in tasks.items():
            if not isinstance(task_val, dict):
                return f"Error: task '{task_key}' must be a dict with 'type' and 'parameters'"
            if "type" not in task_val:
                return (
                    f"Error: task '{task_key}' is missing 'type'. "
                    f"Each task must have a 'type' field. "
                    f"Found keys: {list(task_val.keys())}"
                )
            # Detect tasks nested inside parameters of another task
            params = task_val.get("parameters", {})
            if isinstance(params, dict):
                for pk, pv in params.items():
                    if isinstance(pv, dict) and "type" in pv and pk not in (
                        "headers", "attributes", "set", "conditions",
                    ):
                        return (
                            f"Error: it looks like task '{pk}' is nested inside "
                            f"task '{task_key}'.parameters. Tasks must be "
                            f"SEPARATE top-level keys in the 'tasks' dict, "
                            f"not nested inside other tasks."
                        )
        # Validate relations (accept legacy "connections" key too)
        conns = definition.get("relations", definition.get("connections", []))
        if not isinstance(conns, list):
            return (
                "Error: 'relations' must be a top-level array, not inside tasks. "
                "Example: [{\"from\": \"taskA\", \"to\": \"taskB\", \"type\": \"success\"}]"
            )
        # Normalize: ensure the key is "relations"
        if "connections" in definition and "relations" not in definition:
            definition["relations"] = definition.pop("connections")

        flow_id = definition["id"]
        flow_name = definition.get("name", flow_id)

        # Save the flow definition as a template in a temp location
        from pathlib import Path
        tmp_dir = Path("data/agent_templates")
        tmp_dir.mkdir(parents=True, exist_ok=True)
        # Strip internal fields
        clean_def = {k: v for k, v in definition.items() if not k.startswith("_")}
        tmp_path = tmp_dir / f"{flow_id}.json"
        tmp_path.write_text(
            json.dumps(clean_def, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # Deploy via DeploymentRegistry
        try:
            dep_reg = self._get_deployment_registry()
            instance_id = dep_reg.deploy(
                template_path=str(tmp_path),
                owner=self._owner_tag(),
                parameters=definition.get("parameters", {}),
                source="agent",
                conversation_id=self._conversation_id,
                instance_id=flow_id,  # Use flow_id as instance_id for created flows
            )
            return f"Flow '{instance_id}' created. Use start to run it."
        except Exception as e:
            return f"Error creating flow: {e}"

    def _start_flow(self, flow_id: str, params: Dict = None) -> str:
        if not flow_id:
            return "Error: flow_id is required"

        dep_reg = self._get_deployment_registry()
        inst = dep_reg.get(flow_id)
        if inst is None:
            return f"Error: flow '{flow_id}' not found"
        if inst.owner != self._owner_tag():
            return f"Error: flow '{flow_id}' belongs to another user"

        # Merge parameters
        if params:
            inst.parameters.update(params)
            dep_reg._save_instance(inst)

        # Try to start via executor registry
        try:
            from gui.services.executor_registry import ExecutorRegistry
            from engine.parser import FlowParser
            from engine.continuous_executor import ContinuousFlowExecutor

            # Load the template
            flow_path = inst.flow_path
            if not flow_path or not Path(flow_path).exists():
                flow_path = dep_reg._find_flow_path(inst.flow_id)
            if not flow_path:
                dep_reg.update_status(flow_id, "error", "Template file not found")
                return f"Error: template file not found for '{flow_id}'"

            with open(flow_path, "r", encoding="utf-8") as ff:
                raw = json.load(ff)
            clean = {k: v for k, v in raw.items() if not k.startswith("_")}
            # Apply instance parameters
            if inst.parameters:
                clean.setdefault("parameters", {}).update(inst.parameters)
            flow = FlowParser.parse(clean)

            reg = ExecutorRegistry.get_instance()
            # Stop existing executor if any
            existing = reg.get(flow_id)
            if existing:
                try:
                    existing.stop()
                except Exception:
                    pass
                reg.unregister(flow_id)

            executor = ContinuousFlowExecutor(
                flow, max_workers=inst.max_workers, max_retries=inst.max_retries
            )
            executor.start()
            reg.register(flow_id, executor)
            msg = f"Flow '{flow_id}' started."
        except Exception as e:
            dep_reg.update_status(flow_id, "error", str(e))
            msg = f"Flow '{flow_id}' failed to start: {e}"

        return msg

    def _stop_flow(self, flow_id: str) -> str:
        if not flow_id:
            return "Error: flow_id is required"

        dep_reg = self._get_deployment_registry()
        inst = dep_reg.get(flow_id)
        if inst is None:
            return f"Error: flow '{flow_id}' not found"
        if inst.owner != self._owner_tag():
            return f"Error: flow '{flow_id}' belongs to another user"

        try:
            from gui.services.executor_registry import ExecutorRegistry
            reg = ExecutorRegistry.get_instance()
            executor = reg.get(flow_id)
            if executor:
                executor.stop()
                reg.unregister(flow_id)
            return f"Flow '{flow_id}' stopped."
        except Exception as e:
            return f"Flow '{flow_id}' marked stopped but error: {e}"

    def _flow_status(self, flow_id: str) -> str:
        if not flow_id:
            return "Error: flow_id is required"

        dep_reg = self._get_deployment_registry()
        inst = dep_reg.get(flow_id)
        if inst is None:
            return f"Error: flow '{flow_id}' not found"
        if inst.owner != self._owner_tag():
            return f"Error: flow '{flow_id}' belongs to another user"

        # Check real executor status
        real_status = inst.status
        try:
            from gui.services.executor_registry import ExecutorRegistry
            reg = ExecutorRegistry.get_instance()
            executor = reg.get(flow_id)
            if executor:
                status_info = executor.get_status()
                real_status = "running" if status_info.get("is_running", False) else "stopped"
            elif real_status == "running":
                real_status = "not_running (no executor)"
        except Exception:
            pass

        template_info = f"\nTemplate: {inst.flow_id}" if inst.flow_id != inst.instance_id else ""
        sched_info = ""
        return (
            f"Flow: {inst.flow_name}\n"
            f"Instance: {flow_id}\n"
            f"Status: {real_status}\n"
            f"Parameters: {json.dumps(inst.parameters)}"
            f"{template_info}{sched_info}"
        )

    def _delete_flow(self, flow_id: str) -> str:
        if not flow_id:
            return "Error: flow_id is required"

        dep_reg = self._get_deployment_registry()
        inst = dep_reg.get(flow_id)
        if inst is None:
            return f"Error: flow '{flow_id}' not found"
        if inst.owner != self._owner_tag():
            return f"Error: flow '{flow_id}' belongs to another user"

        dep_reg.undeploy(flow_id)
        return f"Flow '{flow_id}' deleted."

    def _update_flow(self, flow_id: str, params: Dict) -> str:
        if not flow_id:
            return "Error: flow_id is required"
        if not params:
            return "Error: parameters are required for update"

        dep_reg = self._get_deployment_registry()
        inst = dep_reg.get(flow_id)
        if inst is None:
            return f"Error: flow '{flow_id}' not found"
        if inst.owner != self._owner_tag():
            return f"Error: flow '{flow_id}' belongs to another user"

        inst.parameters.update(params)
        dep_reg._save_instance(inst)
        return f"Flow '{flow_id}' parameters updated: {json.dumps(params)}"

    @staticmethod
    def cleanup_conversation(conversation_id: str):
        """Delete all flows belonging to a conversation. Called on conv delete."""
        try:
            from gui.services.deployment_registry import DeploymentRegistry
            dep_reg = DeploymentRegistry.get_instance()
            instances = dep_reg.get_by_conversation(conversation_id)
            deleted = 0
            for inst in instances:
                dep_reg.undeploy(inst.instance_id)
                deleted += 1
            if deleted:
                logger.info("[cleanup] deleted %d flows for conversation %s", deleted, conversation_id)
        except Exception as e:
            logger.warning("Failed to cleanup conversation flows: %s", e)


class AskAgentHandler(ToolHandler):
    """Ask another agent defined in the current conversation.

    Sends a question to a named agent and returns its response.
    The target agent has its own system prompt/persona but shares
    the same conversation context.
    """

    def __init__(self):
        self._conversation_id = ""
        self._user_id = ""
        self._llm_client = None
        self._client_resolver = None
        self._model = ""

    @property
    def name(self) -> str:
        return "ask_agent"

    @property
    def description(self) -> str:
        return (
            "Ask another agent defined in this conversation. "
            "Each agent has a specialized persona/prompt. Use this to "
            "delegate questions to a more specialized agent."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "agent_name": {
                    "type": "string",
                    "description": "Name of the agent to ask",
                },
                "question": {
                    "type": "string",
                    "description": "Question or task for the agent",
                },
            },
            "required": ["agent_name", "question"],
        }

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    def set_user_id(self, uid: str):
        self._user_id = uid

    def set_llm_client(self, client, model: str):
        self._llm_client = client
        self._model = model

    def set_client_resolver(self, resolver):
        self._client_resolver = resolver

    def execute(self, arguments: Dict[str, Any]) -> str:
        agent_name = arguments.get("agent_name", "")
        question = arguments.get("question", "")
        if not agent_name or not question:
            return "Error: agent_name and question are required"

        if not self._conversation_id:
            return "Error: no conversation context"

        try:
            from core.resource_store import ResourceStore
            rs = ResourceStore.instance()
            uid = self._user_id or "anonymous"
            agent_def = rs.get_any("agent", agent_name, uid,
                                   conversation_id=self._conversation_id)
            if not agent_def:
                # Case-insensitive fallback
                for a in rs.list_all("agent", uid,
                                     conversation_id=self._conversation_id):
                    if a["name"].lower() == agent_name.lower():
                        agent_def = a
                        agent_name = a["name"]
                        break
            if not agent_def:
                all_agents = rs.list_all("agent", uid,
                                         conversation_id=self._conversation_id)
                available = ", ".join(a["name"] for a in all_agents) or "none"
                return f"Error: agent '{agent_name}' not found. Available: {available}"

            # Resolve LLM client for this agent
            client = self._llm_client
            model = self._model
            llm_svc = agent_def.get("llm_service", "")
            if llm_svc and "${" in llm_svc:
                from core.expression import resolve_expression
                llm_svc = resolve_expression(llm_svc, owner=uid)
                if "${" in llm_svc:
                    llm_svc = ""
            if llm_svc and self._client_resolver:
                try:
                    resolved_client, _ = self._client_resolver(llm_svc, uid)
                    if resolved_client:
                        client = resolved_client
                except Exception:
                    pass
            agent_model = agent_def.get("model", "")
            if agent_model:
                model = agent_model

            if not client:
                return "Error: LLM client not configured"

            # Single-turn call to the target agent
            from core.llm_client import LLMMessage
            messages = [
                LLMMessage(role="system", content=agent_def["prompt"]),
                LLMMessage(role="user", content=question),
            ]
            response = client.complete(
                messages=messages,
                model=model or None,
                max_tokens=0,
            )
            return f"[{agent_name}]: {response.content}"
        except Exception as e:
            return f"Error calling agent '{agent_name}': {e}"


class CreatePlanHandler(ToolHandler):
    """Create a structured plan for a multi-step task."""

    def __init__(self):
        self._conversation_id = ""

    @property
    def name(self) -> str:
        return "create_plan"

    @property
    def description(self) -> str:
        return (
            "Create a structured plan for a multi-step task. Each step has a "
            "description and status. The plan requires user approval before execution. "
            "Use assign_plan to assign it to agents after approval."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Plan title (short summary of the goal)",
                },
                "steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string"},
                        },
                        "required": ["description"],
                    },
                    "description": "List of plan steps",
                },
            },
            "required": ["title", "steps"],
        }

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    def execute(self, arguments: Dict[str, Any]) -> str:
        import time
        title = arguments.get("title", "")
        steps = arguments.get("steps", [])
        if not title or not steps:
            return "Error: title and steps are required"

        plan_id = f"p_{uuid.uuid4().hex[:8]}"
        plan = {
            "id": plan_id,
            "title": title,
            "status": "pending_approval",
            "created_by": "agent",
            "created_at": time.time(),
            "assigned_to": [],
            "steps": [
                {
                    "index": i + 1,
                    "description": s.get("description", ""),
                    "status": "pending",
                    "note": "",
                    "task_id": "",
                    "assigned_to": "",
                }
                for i, s in enumerate(steps)
            ],
        }

        if self._conversation_id:
            try:
                from core.conversation_store import ConversationStore
                store = ConversationStore.instance()
                plans = store.get_extra(self._conversation_id, "plans") or {}
                plans[plan_id] = plan
                store.set_extra(self._conversation_id, "plans", plans)
                # Emit SSE event
                try:
                    from core.conversation_event_bus import ConversationEventBus
                    ConversationEventBus.instance().publish_event(
                        self._conversation_id, "plan_created", {"plan": plan})
                except Exception:
                    pass
            except Exception as e:
                logger.warning(f"Failed to persist plan: {e}")

        lines = [f"Plan '{plan_id}' created: **{title}** ({len(steps)} steps)"]
        lines.append("Status: pending_approval \u2014 waiting for user to approve.")
        for s in plan["steps"]:
            lines.append(f"  \u25cb {s['index']}. {s['description']}")
        return "\n".join(lines)

class UpdatePlanHandler(ToolHandler):
    """Update the status of steps in a plan."""

    def __init__(self):
        self._conversation_id = ""

    @property
    def name(self) -> str:
        return "update_plan"

    @property
    def description(self) -> str:
        return (
            "Update the status of one or more steps in a plan. "
            "Call this as you complete steps to show progress to the user."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "plan_id": {
                    "type": "string",
                    "description": "Plan ID (e.g. p_abc12345)",
                },
                "updates": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "step": {"type": "integer", "description": "Step number (1-based)"},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "done", "skipped", "error"],
                            },
                            "note": {"type": "string", "description": "Optional note"},
                        },
                        "required": ["step", "status"],
                    },
                },
            },
            "required": ["plan_id", "updates"],
        }

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    def execute(self, arguments: Dict[str, Any]) -> str:
        plan_id = arguments.get("plan_id", "")
        updates = arguments.get("updates", [])
        if not plan_id or not updates:
            return "Error: plan_id and updates are required"

        plan = None
        if self._conversation_id:
            try:
                from core.conversation_store import ConversationStore
                store = ConversationStore.instance()
                plans = store.get_extra(self._conversation_id, "plans") or {}
                plan = plans.get(plan_id)
            except Exception:
                pass

        if not plan:
            return f"Error: plan '{plan_id}' not found."

        for u in updates:
            step_num = int(u.get("step", 0))
            status = u.get("status", "")
            note = u.get("note", "")
            for s in plan["steps"]:
                if s["index"] == step_num:
                    s["status"] = status
                    if note:
                        s["note"] = note
                    break

        # Auto-update plan status
        statuses = [s["status"] for s in plan["steps"]]
        if all(s in ("done", "skipped") for s in statuses):
            plan["status"] = "completed"
        elif any(s == "in_progress" for s in statuses):
            plan["status"] = "in_progress"

        # Persist
        if self._conversation_id:
            try:
                from core.conversation_store import ConversationStore
                store = ConversationStore.instance()
                plans = store.get_extra(self._conversation_id, "plans") or {}
                plans[plan_id] = plan
                store.set_extra(self._conversation_id, "plans", plans)
                # Emit SSE event
                try:
                    from core.conversation_event_bus import ConversationEventBus
                    ConversationEventBus.instance().publish_event(
                        self._conversation_id, "plan_updated", {"plan": plan})
                except Exception:
                    pass
            except Exception:
                pass

        # Format
        done_count = sum(1 for s in plan["steps"] if s["status"] == "done")
        total = len(plan["steps"])
        lines = [f"**{plan['title']}** — {done_count}/{total} done [{plan['status']}]"]
        for s in plan["steps"]:
            icon = {"pending": "○", "in_progress": "◔", "done": "✓",
                    "skipped": "–", "error": "✗"}.get(s["status"], "○")
            note = f' — {s["note"]}' if s.get("note") else ""
            lines.append(f"  {icon} {s['index']}. {s['description']}{note}")
        return "\n".join(lines)


class ApprovePlanHandler(ToolHandler):
    """Approve a plan (agent can approve plans created by other agents)."""

    def __init__(self):
        self._conversation_id = ""

    @property
    def name(self) -> str:
        return "approve_plan"

    @property
    def description(self) -> str:
        return "Approve a plan that is pending approval. Only approve plans you did not create yourself."

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "plan_id": {"type": "string", "description": "Plan ID to approve"},
            },
            "required": ["plan_id"],
        }

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    def execute(self, arguments: Dict[str, Any]) -> str:
        plan_id = arguments.get("plan_id", "")
        if not plan_id or not self._conversation_id:
            return "Error: plan_id required"
        try:
            from core.conversation_store import ConversationStore
            store = ConversationStore.instance()
            plans = store.get_extra(self._conversation_id, "plans") or {}
            plan = plans.get(plan_id)
            if not plan:
                return f"Error: plan '{plan_id}' not found"
            if plan["status"] != "pending_approval":
                return f"Plan is already {plan['status']}"
            plan["status"] = "approved"
            plans[plan_id] = plan
            store.set_extra(self._conversation_id, "plans", plans)
            try:
                from core.conversation_event_bus import ConversationEventBus
                bus = ConversationEventBus.instance()
                bus.publish_event(self._conversation_id, "plan_updated", {"plan": plan})
                created_by = plan.get("created_by", "")
                if created_by and created_by != "user":
                    bus.publish_event(self._conversation_id, "notification", {
                        "message": f"Plan '{plan['title']}' ({plan_id}) approved. Proceed with execution.",
                        "target_agent": created_by,
                    })
            except Exception:
                pass
            return f"Plan '{plan_id}' approved."
        except Exception as e:
            return f"Error: {e}"


class AssignPlanHandler(ToolHandler):
    """Assign a plan to an agent, creating tasks for execution."""

    def __init__(self):
        self._conversation_id = ""

    @property
    def name(self) -> str:
        return "assign_plan"

    @property
    def description(self) -> str:
        return (
            "Assign a plan to an agent for execution. Implies approval if pending. "
            "By default assigns unassigned steps. Use step_range for specific steps "
            "(e.g. '1-3', '2,4,5') or 'remaining' for all non-completed steps. "
            "Can reassign steps that are pending, in_progress, or error."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "plan_id": {"type": "string", "description": "Plan ID to assign"},
                "agent": {"type": "string", "description": "Agent name or ALL"},
                "step_range": {
                    "type": "string",
                    "description": "Optional step range (e.g. '1-3', '2,4,5'). If omitted, assigns full plan as one task.",
                },
            },
            "required": ["plan_id", "agent"],
        }

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    def execute(self, arguments: Dict[str, Any]) -> str:
        plan_id = arguments.get("plan_id", "")
        agent = arguments.get("agent", "")
        step_range = arguments.get("step_range", "")
        if not plan_id or not agent or not self._conversation_id:
            return "Error: plan_id and agent required"

        try:
            from core.conversation_store import ConversationStore
            store = ConversationStore.instance()
            plans = store.get_extra(self._conversation_id, "plans") or {}
            plan = plans.get(plan_id)
            if not plan:
                return f"Error: plan '{plan_id}' not found"
            if plan["status"] == "cancelled":
                return "Error: cannot assign a cancelled plan"
            # Assign implies approval
            if plan["status"] == "pending_approval":
                plan["status"] = "approved"
            plan["status"] = "in_progress"
            if agent not in plan.get("assigned_to", []):
                plan.setdefault("assigned_to", []).append(agent)

            reassignable = ("pending", "in_progress", "error")
            assigned_count = 0
            if step_range == "remaining":
                for s in plan["steps"]:
                    if s["status"] in reassignable:
                        s["assigned_to"] = agent
                        assigned_count += 1
            elif step_range:
                target_steps = []
                for part in step_range.split(","):
                    part = part.strip()
                    if "-" in part:
                        a, b = part.split("-", 1)
                        target_steps.extend(range(int(a), int(b) + 1))
                    else:
                        target_steps.append(int(part))
                for s in plan["steps"]:
                    if s["index"] in target_steps and s["status"] in reassignable:
                        s["assigned_to"] = agent
                        assigned_count += 1
            else:
                for s in plan["steps"]:
                    if not s.get("assigned_to") and s["status"] in reassignable:
                        s["assigned_to"] = agent
                        assigned_count += 1

            plans[plan_id] = plan
            store.set_extra(self._conversation_id, "plans", plans)
            try:
                from core.conversation_event_bus import ConversationEventBus
                ConversationEventBus.instance().publish_event(
                    self._conversation_id, "plan_updated", {"plan": plan})
            except Exception:
                pass
            mode = f"steps {step_range}" if step_range else "full plan"
            return f"Plan '{plan_id}' assigned to {agent} ({mode}, {assigned_count} steps)."
        except Exception as e:
            return f"Error: {e}"


class CancelPlanHandler(ToolHandler):
    """Cancel a plan."""

    def __init__(self):
        self._conversation_id = ""

    @property
    def name(self) -> str:
        return "cancel_plan"

    @property
    def description(self) -> str:
        return "Cancel a plan. Steps in progress may still complete."

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "plan_id": {"type": "string", "description": "Plan ID to cancel"},
            },
            "required": ["plan_id"],
        }

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    def execute(self, arguments: Dict[str, Any]) -> str:
        plan_id = arguments.get("plan_id", "")
        if not plan_id or not self._conversation_id:
            return "Error: plan_id required"
        try:
            from core.conversation_store import ConversationStore
            store = ConversationStore.instance()
            plans = store.get_extra(self._conversation_id, "plans") or {}
            plan = plans.get(plan_id)
            if not plan:
                return f"Error: plan '{plan_id}' not found"
            plan["status"] = "cancelled"
            plans[plan_id] = plan
            store.set_extra(self._conversation_id, "plans", plans)
            try:
                from core.conversation_event_bus import ConversationEventBus
                ConversationEventBus.instance().publish_event(
                    self._conversation_id, "plan_updated", {"plan": plan})
            except Exception:
                pass
            return f"Plan '{plan_id}' cancelled."
        except Exception as e:
            return f"Error: {e}"


class DeletePlanHandler(ToolHandler):
    """Delete a plan permanently."""

    def __init__(self):
        self._conversation_id = ""

    @property
    def name(self) -> str:
        return "delete_plan"

    @property
    def description(self) -> str:
        return "Permanently delete a plan from the conversation."

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "plan_id": {"type": "string", "description": "Plan ID to delete"},
            },
            "required": ["plan_id"],
        }

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    def execute(self, arguments: Dict[str, Any]) -> str:
        plan_id = arguments.get("plan_id", "")
        if not plan_id or not self._conversation_id:
            return "Error: plan_id required"
        try:
            from core.conversation_store import ConversationStore
            store = ConversationStore.instance()
            plans = store.get_extra(self._conversation_id, "plans") or {}
            if plan_id not in plans:
                return f"Error: plan '{plan_id}' not found"
            del plans[plan_id]
            store.set_extra(self._conversation_id, "plans", plans)
            try:
                from core.conversation_event_bus import ConversationEventBus
                ConversationEventBus.instance().publish_event(
                    self._conversation_id, "plan_deleted", {"plan_id": plan_id})
            except Exception:
                pass
            return f"Plan '{plan_id}' deleted."
        except Exception as e:
            return f"Error: {e}"

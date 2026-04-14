"""Auto-extracted from core/tool_registry.py — see core/handlers/__init__.py"""

import json
import logging
import os
import re
import threading
import uuid
from typing import Dict, Any, List, Optional

from core.tool_handler import ToolHandler

logger = logging.getLogger(__name__)



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
                             "start", "stop", "status", "update", "delete",
                             "run"],
                    "description": (
                        "Action to perform. 'run' loads a template by FQN, "
                        "executes it ONCE synchronously with the given "
                        "parameters and optional input content, returns "
                        "output FlowFiles' content/attributes — no "
                        "deployment, no background instance."),
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
                        "Services use 'parameters' not 'config'.\n\n"
                        "Optional 'groups' key composes other flows as "
                        "sub-flows. Each entry: {<group_id>: {name, "
                        "flow_ref: {path, version}, parameter_mapping: "
                        "{<child_param>: '${<parent_expr>}'}, "
                        "port_mapping: {input: {port_task_id: <id>}, "
                        "output: {<output_port_id>: <relationship>}}, "
                        "pass_attributes: bool}}. Both flow_ref.version "
                        "(must match the child's version exactly) and "
                        "every port id are validated at parse time — "
                        "typos fail fast with the valid candidates listed."
                    ),
                },
                "parameters": {
                    "type": "object",
                    "description": "Flow parameters to set on start/run",
                },
                "input": {
                    "type": "string",
                    "description": (
                        "Optional FlowFile content to feed into the flow "
                        "(action='run' only). UTF-8 string."),
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
        elif action == "run":
            template_id = arguments.get("template_id", "")
            params = arguments.get("parameters", {})
            input_text = arguments.get("input", "")
            return self._run_template(template_id, params, input_text)
        return f"Error: unknown action '{action}'"

    def _get_deployment_registry(self):
        from core.deployment_registry import DeploymentRegistry
        return DeploymentRegistry.get_instance()

    def _owner_tag(self) -> str:
        return self._user_id or None

    def _catalog(self) -> str:
        """List available flow templates from the repository."""
        import core.paths as _p
        from core.repository import ScopedRepository
        repo = ScopedRepository.instance()
        # List all flow packages in global scope
        flows_dir = _p.REPOSITORY_DIR / "flows" / "global"
        templates = []
        if flows_dir.exists():
            for pkg_dir in sorted(flows_dir.iterdir()):
                if not pkg_dir.is_dir():
                    continue
                for flow_dir in sorted(pkg_dir.iterdir()):
                    if not flow_dir.is_dir():
                        continue
                    latest = flow_dir / "latest.json"
                    if not latest.exists():
                        continue
                    try:
                        ver_info = json.loads(latest.read_text(encoding="utf-8"))
                        ver = ver_info.get("version", "")
                        ver_file = flow_dir / "versions" / f"{ver}.json"
                        if ver_file.exists():
                            data = json.loads(ver_file.read_text(encoding="utf-8"))
                        else:
                            data = {}
                        fqn = f"{pkg_dir.name}.{flow_dir.name}:{ver}"
                        templates.append({
                            "fqn": fqn,
                            "name": data.get("name", flow_dir.name),
                            "version": ver,
                            "description": data.get("description", ""),
                        })
                    except Exception:
                        continue
        if not templates:
            return "No flow templates found in the repository."
        lines = []
        for t in templates:
            ver = f" v{t['version']}" if t["version"] else ""
            desc = f" — {t['description']}" if t["description"] else ""
            lines.append(f"- {t['fqn']}: {t['name']}{desc}")
        return f"Available templates ({len(templates)}):\n" + "\n".join(lines)

    def _deploy_template(self, template_id: str, params: dict = None) -> str:
        """Deploy a flow from the repository. template_id is a FQN like default.hello_world:1.0.0."""
        if not template_id:
            return "Error: template_id is required (use FQN like default.flow_name:1.0.0)"

        # Resolve FQN to repository flow
        from core.repository import ScopedRepository
        from core.paths import parse_flow_fqn
        try:
            package, flowname, version = parse_flow_fqn(template_id)
        except Exception:
            return f"Error: invalid FQN '{template_id}'. Use package.flow_name:version"

        repo = ScopedRepository.instance()
        flow_data = repo.get_flow(template_id, "global")
        if flow_data is None:
            return (
                f"Error: flow '{template_id}' not found in repository. "
                "Use action 'catalog' to see available flows."
            )

        # Write to a temp file for deploy (DeploymentRegistry.deploy expects a path)
        import tempfile
        tmp = tempfile.NamedTemporaryFile(
            suffix=".json", prefix=f"{flowname}_", delete=False, mode="w")
        json.dump(flow_data, tmp, ensure_ascii=False, indent=2)
        tmp.close()

        try:
            dep_reg = self._get_deployment_registry()
            instance_id = dep_reg.deploy(
                template_path=tmp.name,
                owner=self._owner_tag(),
                parameters=params or {},
                source="agent",
                conversation_id=self._conversation_id,
            )
            # Update the deployment with FQN
            dep_reg._ensure_loaded()
            inst = dep_reg._instances.get(instance_id)
            if inst:
                inst.flow_fqn = template_id
                dep_reg._save_instance(inst)
            return (
                f"Flow '{template_id}' deployed as instance "
                f"'{instance_id}'. Use start to run it."
            )
        except Exception as e:
            return f"Error deploying template: {e}"

    def _run_template(self, template_id: str, params: dict = None,
                      input_text: str = "") -> str:
        """Synchronously execute a flow once and return the outputs.

        No deployment, no background instance. Loads the flow definition
        from the repository (FQN), parses it with the given parameters,
        feeds an optional input FlowFile, and runs it through
        ContinuousFlowExecutor.run_batch(). Returns the output
        FlowFiles' content + attributes.
        """
        if not template_id:
            return ("Error: template_id is required (FQN like "
                    "default.flow_name:1.0.0).")
        from core.repository import ScopedRepository
        flow_data = ScopedRepository.instance().get_flow(template_id, "global")
        if flow_data is None:
            return (f"Error: flow '{template_id}' not found in repository. "
                    f"Use action 'catalog' to see available flows.")
        try:
            from engine import FlowParser
            from engine.continuous_executor import ContinuousFlowExecutor
            from core import FlowFile
        except Exception as e:
            return f"Error: failed to import flow engine: {e}"
        # Apply caller-supplied parameter overrides on top of the flow's
        # declared defaults before parsing — same merge order as the
        # deployment path.
        merged_params = dict(flow_data.get("parameters") or {})
        merged_params.update(params or {})
        flow_data = dict(flow_data)
        flow_data["parameters"] = merged_params
        try:
            flow = FlowParser.parse(flow_data)
        except Exception as e:
            return f"Error: parse failed for '{template_id}': {e}"
        ff = FlowFile(content=(input_text or "").encode("utf-8"))
        try:
            result = ContinuousFlowExecutor.run_batch(
                flow, input_flowfiles=[ff], max_workers=1)
        except Exception as e:
            return f"Error: execution failed for '{template_id}': {e}"
        outs = []
        for _f in (getattr(result, "output_flowfiles", []) or []):
            try:
                _content = _f.get_content()
                if isinstance(_content, bytes):
                    _content = _content.decode("utf-8", errors="replace")
            except Exception:
                _content = ""
            outs.append({
                "attributes": dict(_f.attributes or {}),
                "content": _content[:8000],
            })
        return json.dumps({
            "template_id": template_id,
            "success": bool(getattr(result, "success", False)),
            "outputs": outs,
        }, ensure_ascii=False, indent=2)

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
        from core.paths import RUNTIME_DIR; tmp_dir = RUNTIME_DIR / "agent_templates"
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
            from core.executor_registry import ExecutorRegistry
            from engine.parser import FlowParser
            from engine.continuous_executor import ContinuousFlowExecutor

            # Load the template
            flow_path = inst.flow_path
            if not flow_path or not os.path.exists(flow_path):
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
            from core.executor_registry import ExecutorRegistry
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
            from core.executor_registry import ExecutorRegistry
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
            from core.deployment_registry import DeploymentRegistry
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


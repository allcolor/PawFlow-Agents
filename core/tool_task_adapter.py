"""ToolTaskAdapter — Automatically wraps ToolHandlers as flow Tasks.

Every tool an agent can call becomes a flow Task node via this adapter.
No code duplication — the adapter calls handler.execute(arguments).

Task TYPE: tool.<handler_name> (e.g. tool.generate_image, tool.notify_user)
Arguments: merged from task config + FlowFile attributes + FlowFile content (JSON)
Output: handler result as FlowFile content + tool.name/tool.status attributes
"""

import json
import logging
from typing import Any, Dict, List

from core import Task, FlowFile, TaskFactory

logger = logging.getLogger(__name__)

# Tools that should NOT become flow tasks (meta-tools, agent-internal)
SKIP_TOOLS = {
    "get_tool_schema", "use_tool",  # lazy tools meta
    "ScheduleWakeup",  # agent-loop internal
    "PushNotification",  # agent-loop internal (conv bell)
    "complete_task", "verify_task",  # task lifecycle (agent-only)
    "flash_delegate",  # agent-internal temporary sub-agents
    "manage_resource",  # resource CRUD
    "create_tool",  # dynamic tool creation
    "pawflow_help",  # help text
    "update_plan", "create_plan",  # plan management
    "link_identity",  # identity linking
    "browser_action",  # browser automation (needs browser service)
}


class ToolTaskAdapter(Task):
    """Generic adapter that wraps a ToolHandler as a flow Task.

    Subclasses are created dynamically by register_tool_tasks().
    Each subclass has a _handler_class attribute pointing to the
    ToolHandler class to instantiate.
    """

    TYPE = ""
    DESCRIPTION = ""
    _handler_class = None

    def get_parameter_schema(self) -> Dict[str, Any]:
        """Convert ToolHandler JSON Schema to PawFlow task schema."""
        if not self._handler_class:
            return {}
        handler = self._handler_class()
        schema = handler.parameters_schema
        result = {}
        for prop_name, prop_def in schema.get("properties", {}).items():
            required = prop_name in schema.get("required", [])
            result[prop_name] = {
                "type": prop_def.get("type", "string"),
                "description": prop_def.get("description", ""),
                "required": required,
            }
        return result

    def _inject_context(self, handler, flowfile: FlowFile):
        """Inject runtime context from FlowFile attributes and task config."""
        user_id = (flowfile.get_attribute("http.auth.principal")
                   or self.config.get("user_id", ""))
        conv_id = (flowfile.get_attribute("conversation.id")
                   or self.config.get("conversation_id", ""))
        base_url = self.config.get("file_base_url", "")

        if hasattr(handler, "set_user_id") and user_id:
            handler.set_user_id(user_id)
        if hasattr(handler, "set_conversation_id") and conv_id:
            handler.set_conversation_id(conv_id)
        if hasattr(handler, "set_base_url") and base_url:
            handler.set_base_url(base_url)
        if hasattr(handler, "set_agent_name"):
            handler.set_agent_name(self.config.get("agent_name", "flow"))

        # Service resolvers for image/video generation
        if hasattr(handler, "set_service_resolver"):
            svc_id = self.config.get("image_service", "") or self.config.get("video_service", "")
            if svc_id and self._services:
                svc = self._services.get(svc_id)
                if svc:
                    handler.set_service_resolver(lambda: [(svc_id, type(svc).__name__, "flow")])

        # Embed function for memory handlers
        if hasattr(handler, "set_embed_fn"):
            # No embedding in flow context (would need LLM client)
            pass

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        if not self._handler_class:
            flowfile.set_content(b"Error: no handler class configured")
            flowfile.set_attribute("tool.status", "error")
            return [flowfile]

        handler = self._handler_class()

        # 1. Build arguments from config + attributes + content
        # Start with task config (static parameters from flow JSON)
        arguments = {}
        schema = handler.parameters_schema
        schema_props = set(schema.get("properties", {}).keys())

        # Config values that match schema properties
        for key in schema_props:
            if key in self.config:
                arguments[key] = self.config[key]

        # Overlay FlowFile attributes matching schema properties
        for prop in schema_props:
            attr_val = flowfile.get_attribute(prop)
            if attr_val is not None:
                arguments[prop] = attr_val

        # Overlay FlowFile content if valid JSON
        try:
            content = flowfile.get_content().decode("utf-8")
            if content.strip():
                content_args = json.loads(content)
                if isinstance(content_args, dict):
                    for k, v in content_args.items():
                        if k in schema_props:
                            arguments[k] = v
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

        # 2. Inject context
        self._inject_context(handler, flowfile)

        # 3. Execute
        try:
            result_text = handler.execute(arguments) or ""
        except Exception as e:
            logger.error("Tool task '%s' failed: %s", self.TYPE, e)
            result_text = f"Error: {e}"

        # 4. Build output FlowFile
        out = flowfile.clone()
        out.set_content(result_text.encode("utf-8"))
        out.set_attribute("tool.name", handler.name)
        out.set_attribute("tool.status",
                          "error" if result_text.startswith("Error") else "success")
        return [out]


def register_tool_tasks():
    """Auto-register all ToolHandlers as flow Tasks.

    Called from register_all_tasks(). Creates a unique Task class per tool
    with TYPE = "tool.<handler_name>".
    """
    from core.tool_registry import create_default_registry

    registry = create_default_registry()
    registered = 0

    for handler in registry.list_tools():
        if handler.name in SKIP_TOOLS:
            continue

        task_type = f"tool.{handler.name}"

        # Skip if already registered (idempotent)
        try:
            if TaskFactory.get(task_type):
                continue
        except Exception:
            pass

        handler_class = type(handler)
        desc = handler.description[:120] if handler.description else handler.name

        # Create a unique class per tool
        task_class = type(
            f"ToolTask_{handler.name}",
            (ToolTaskAdapter,),
            {
                "TYPE": task_type,
                "DESCRIPTION": desc,
                "_handler_class": handler_class,
            },
        )

        TaskFactory.register(task_class)
        registered += 1

    if registered:
        logger.info("Registered %d tool-task adapters (tool.*)", registered)

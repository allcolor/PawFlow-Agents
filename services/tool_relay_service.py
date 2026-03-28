"""Tool Relay Service — WebSocket listener for MCP bridge connections.

Same pattern as RelayService: binds a port, accepts relay connections.
The MCP bridge (running as Claude Code subprocess) connects here to
execute PawFlow tools.

Config:
    port: int       — WS listener port (default: 9091, shared with filesystem)
    path: str       — WS endpoint path (default: /ws/tools)
    token: str      — Shared token (bridge must match to connect)

Protocol:
    Bridge → Server: {"type": "register", "token": "xxx", "user_id": "...", "conversation_id": "...", "agent_name": "..."}
    Server → Bridge: {"type": "registered"}

    Bridge → Server: {"type": "request", "request_id": "abc", "method": "list_tools"}
    Server → Bridge: {"type": "result", "request_id": "abc", "data": [...]}

    Bridge → Server: {"type": "request", "request_id": "def", "method": "get_tool_schema", "tool_name": "filesystem"}
    Server → Bridge: {"type": "result", "request_id": "def", "data": {...}}

    Bridge → Server: {"type": "request", "request_id": "ghi", "method": "execute_tool", "tool_name": "filesystem", "arguments": {...}}
    Server → Bridge: {"type": "result", "request_id": "ghi", "data": "...result..."}
"""

import json
import logging
import threading
from typing import Any, Dict, Optional

from core import ServiceFactory
from core.base_service import BaseService

logger = logging.getLogger(__name__)


class ToolRelayService(BaseService):
    """Tool execution service for MCP bridge connections."""

    TYPE = "toolRelay"
    VERSION = "1.0.0"
    NAME = "Tool Relay"
    DESCRIPTION = "Exposes PawFlow tools to Claude Code via WebSocket relay"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._port = int(config.get("port", 9091))
        self._path = config.get("path", "/ws/tools")
        self._token = config.get("token", "")
        self._service_id = config.get("_service_id", "")
        self._connection = None  # WSListener ref

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "port": {"type": "integer", "required": False, "default": 9091,
                     "description": "WebSocket listener port (shared with filesystem relay)"},
            "path": {"type": "string", "required": False, "default": "/ws/tools",
                     "description": "WebSocket endpoint path"},
            "token": {"type": "string", "required": True, "sensitive": True,
                      "description": "Authentication token (MCP bridge must match)"},
        }

    @property
    def service_id(self) -> str:
        return self._service_id

    def connect(self):
        """Register route on the shared WS listener (same port as filesystem)."""
        from services.filesystem_service import WSListener
        listener = WSListener.get_or_create(self._port)
        listener.register_route(self._path, self)
        self._connection = listener
        logger.info("ToolRelayService '%s' listening on port %d path %s",
                     self._service_id, self._port, self._path)

    def disconnect(self):
        if self._connection:
            self._connection.unregister_route(self._path)
            self._connection = None

    # ── WebSocket message handling ──
    # Called by WSListener when a connection comes in on our path.
    # We override the relay pattern: instead of storing the connection for
    # later _request() calls, we handle requests inline in the WS loop.
    #
    # The WSListener calls service._set_relay() after registration.
    # We intercept the message loop by providing handle_tool_request().

    def _set_relay(self, reader, writer, loop):
        """Called by WSListener after registration — NOT used for tool relay.

        Tool relay handles requests in the WS message loop directly.
        We store writer/loop for sending responses.
        """
        # Not used — tool relay has its own message handling
        pass

    def _clear_relay(self, reader=None):
        pass

    def _resolve_pending(self, msg):
        """Not used — tool relay is server-side, not client-side."""
        pass

    # Cancelled (conv_id, agent_name) tuples — tool calls return error immediately
    _cancelled: set = set()

    @classmethod
    def cancel_agent(cls, conversation_id: str, agent_name: str):
        """Mark a (conv, agent) as cancelled — tool calls return error."""
        cls._cancelled.add((conversation_id, agent_name))

    @classmethod
    def uncancel_agent(cls, conversation_id: str, agent_name: str):
        """Clear cancelled state (new request starting)."""
        cls._cancelled.discard((conversation_id, agent_name))

    def handle_tool_request(self, msg: dict, user_id: str = "",
                            conversation_id: str = "",
                            agent_name: str = "") -> dict:
        """Handle a tool request from the MCP bridge."""
        method = msg.get("method", "")
        request_id = msg.get("request_id", "")

        # Reject tool calls for cancelled (conv, agent)
        if (conversation_id, agent_name) in self._cancelled:
            return {"type": "error", "request_id": request_id,
                    "error": "Request cancelled by user"}

        if method == "list_tools":
            return self._handle_list_tools(request_id, user_id, conversation_id)
        elif method == "get_tool_schema":
            return self._handle_get_schema(request_id, msg.get("tool_name", ""),
                                           user_id=user_id)
        elif method == "execute_tool":
            return self._handle_execute(
                request_id, msg.get("tool_name", ""),
                msg.get("arguments", {}),
                user_id, conversation_id, agent_name,
            )
        else:
            return {"type": "error", "request_id": request_id,
                    "error": f"Unknown method: {method}"}

    def _get_registry(self, user_id: str = "", conversation_id: str = ""):
        """Get a configured tool registry for this request context.

        CRITICAL: injects the live filesystem service instance (the one
        with the relay connection) into the handler. Without this, the
        handler creates a new disconnected instance.
        """
        from core.tool_registry import create_default_registry
        registry = create_default_registry()

        # Find the live filesystem service (the one with relay connected)
        fs_svc = self._find_filesystem_service(user_id)

        # Configure ALL handlers that need user/filesystem context
        for h in registry.list_tools():
            # Set user_id on any handler that supports it
            if hasattr(h, 'set_user_id') and user_id:
                h.set_user_id(user_id)
            if hasattr(h, 'set_conversation_id') and conversation_id:
                h.set_conversation_id(conversation_id)
            if hasattr(h, '_user_id'):
                h._user_id = user_id
            if hasattr(h, '_conversation_id'):
                h._conversation_id = conversation_id
            # Inject live filesystem service where needed
            if fs_svc:
                if hasattr(h, 'set_fs_service'):
                    h.set_fs_service(fs_svc)
                if hasattr(h, '_fs_service') and not getattr(h, '_fs_service', None):
                    h._fs_service = fs_svc
                if hasattr(h, 'set_service'):
                    try:
                        h.set_service(fs_svc)
                    except Exception:
                        pass

        # Populate available services on all BaseFsHandler instances
        from core.handlers._fs_base import BaseFsHandler, _FS_TYPES
        _fs_handlers = [h for h in registry.list_tools() if isinstance(h, BaseFsHandler)]
        if _fs_handlers:
            try:
                available = []
                from gui.services.global_service_registry import GlobalServiceRegistry
                greg = GlobalServiceRegistry.get_instance()
                for sid, sdef in greg.get_all_definitions().items():
                    stype = getattr(sdef, "service_type", "")
                    if stype in _FS_TYPES:
                        svc = greg.get_live_instance(sid)
                        if svc:
                            available.append({
                                "id": sid, "type": stype,
                                "root": getattr(svc, "root_path", "?"),
                            })
                if user_id:
                    from gui.services.user_service_registry import UserServiceRegistry
                    ureg = UserServiceRegistry.get_instance()
                    for sid, sdef in ureg.get_all_for_user(user_id).items():
                        stype = getattr(sdef, "service_type", "")
                        if stype in _FS_TYPES:
                            svc = ureg.get_live_instance(user_id, sid)
                            if svc:
                                available.append({
                                    "id": sid, "type": stype,
                                    "root": getattr(svc, "root_path", "?"),
                                })
                if available:
                    for h in _fs_handlers:
                        h._available_services = available
                    logger.info("Filesystem services for user '%s': %s",
                                user_id, [s["id"] for s in available])
            except Exception as e:
                logger.error("Failed to enumerate filesystem services: %s", e)

        return registry

    @staticmethod
    def _find_filesystem_service(user_id: str = ""):
        """Find the first live filesystem service for this user.

        Same logic as agent_utils._find_filesystem_service but standalone.
        """
        fs_types = ("relay", "filesystem", "googleDrive", "oneDrive")
        # Global services
        try:
            from gui.services.global_service_registry import GlobalServiceRegistry
            greg = GlobalServiceRegistry.get_instance()
            for sid, sdef in greg.get_all_definitions().items():
                if getattr(sdef, "service_type", "") in fs_types:
                    svc = greg.get_live_instance(sid)
                    if svc:
                        return svc
        except Exception:
            pass
        # User services
        if user_id:
            try:
                from gui.services.user_service_registry import UserServiceRegistry
                ureg = UserServiceRegistry.get_instance()
                for sid, sdef in ureg.get_all_for_user(user_id).items():
                    if getattr(sdef, "service_type", "") in fs_types:
                        svc = ureg.get_live_instance(user_id, sid)
                        if svc:
                            return svc
            except Exception:
                pass
        return None

    def _handle_list_tools(self, request_id: str,
                           user_id: str, conversation_id: str) -> dict:
        registry = self._get_registry(user_id, conversation_id)
        tools = []
        for h in registry.list_tools():
            tools.append({
                "name": h.name,
                "description": h.description or "",
                "parameters": h.parameters_schema or {},
            })
        return {"type": "result", "request_id": request_id, "data": tools}

    def _handle_get_schema(self, request_id: str, tool_name: str,
                           user_id: str = "") -> dict:
        registry = self._get_registry(user_id)
        handler = registry.get(tool_name)
        if not handler:
            available = [h.name for h in registry.list_tools()]
            return {"type": "error", "request_id": request_id,
                    "error": f"Unknown tool '{tool_name}'. Available: {', '.join(available)}"}
        return {"type": "result", "request_id": request_id, "data": {
            "name": handler.name,
            "description": handler.description,
            "parameters": handler.parameters_schema,
        }}

    def _handle_execute(self, request_id: str, tool_name: str,
                        arguments: dict, user_id: str,
                        conversation_id: str, agent_name: str) -> dict:
        registry = self._get_registry(user_id, conversation_id)

        # ── Tool Approval Gate ───────────────────────────────────
        # Check approval before executing — same gate used by API providers.
        try:
            from core.tool_approval import ToolApprovalGate
            if ToolApprovalGate.is_enabled(conversation_id):
                _path = arguments.get("path", "") if isinstance(arguments, dict) else ""
                action_summary = f"{tool_name}({_path})" if _path else tool_name
                approval = ToolApprovalGate.check(
                    tool_name, action_summary, conversation_id, user_id, arguments
                )
                if approval != "approved":
                    return {"type": "result", "request_id": request_id,
                            "data": f"Error: Tool execution denied ({approval}): {action_summary}"}
        except Exception as e:
            logger.warning("Tool approval check failed: %s", e)

        # NO SSE events here — the stream handler (claude_code.py) publishes
        # tool_call/tool_result events from the Claude Code output stream.
        # Publishing here would create duplicates.

        # Execute
        try:
            result = registry.execute(tool_name, arguments)
            result_str = str(result) if result is not None else "(no output)"
        except Exception as e:
            result_str = f"Error: {e}"
            logger.error("Tool relay execute '%s' failed: %s", tool_name, e)

        return {"type": "result", "request_id": request_id, "data": result_str}


# Register with ServiceFactory
ServiceFactory.register(ToolRelayService)

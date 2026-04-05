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


def _resolve_vars_in_args(arguments: dict, env: dict, skip_keys: set = None):
    """Resolve $VAR and ${VAR} patterns in all string values of arguments.

    Mutates arguments in-place. Recurses into dicts and lists.
    Skips keys starting with _ (internal params like _secret_env).
    Skips keys in skip_keys (e.g. 'command' for bash — shell resolves itself).
    """
    import re
    _skip = skip_keys or set()
    _pattern = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)')

    def _replace(match):
        name = match.group(1) or match.group(2)
        return env.get(name, env.get(name.upper(), match.group(0)))

    def _resolve(obj):
        if isinstance(obj, str):
            return _pattern.sub(_replace, obj)
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k.startswith('_') or k in _skip:
                    continue
                obj[k] = _resolve(v)
            return obj
        if isinstance(obj, list):
            return [_resolve(item) for item in obj]
        return obj

    _resolve(arguments)


def _redact_secrets(text: str, secret_values: set,
                    secret_names: dict = None) -> str:
    """Replace exact occurrences of secret values in text with a redaction marker.

    Only exact matches — no partial prefix/suffix matching (causes false
    positives when secrets are substrings of other data like verification codes).
    """
    if len(text) > 1_000_000 or '\x00' in text:
        return text
    _names = secret_names or {}
    for val in secret_values:
        if val in text:
            _varname = _names.get(val, "")
            _marker = f"<****Redacted — use ${_varname}****>" if _varname else "<****Redacted****>"
            text = text.replace(val, _marker)
    return text


class ToolRelayService(BaseService):
    """Tool execution service for MCP bridge connections."""

    TYPE = "toolRelay"
    VERSION = "1.0.0"
    NAME = "Tool Relay"
    DESCRIPTION = "Exposes PawFlow tools to Claude Code via WebSocket relay"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._port = int(config.get("port", 9091))
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
        path = self.config.get("path", "/ws/tools")
        listener = WSListener.get_or_create(self._port)
        listener.register_route(path, self)
        self._connection = listener
        logger.info("ToolRelayService '%s' listening on port %d path %s",
                     self._service_id, self._port, path)

    def disconnect(self):
        if self._connection:
            self._connection.unregister_route(self.config.get("path", "/ws/tools"))
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
    # In-flight request_id → (conv_id, agent_name) for targeted cancellation
    _inflight: Dict[str, tuple] = {}
    _inflight_lock = threading.Lock()

    @classmethod
    def cancel_request(cls, request_id: str):
        """Cancel a single in-flight tool request by its request/tc id."""
        with cls._inflight_lock:
            info = cls._inflight.get(request_id)
        if info and isinstance(info, dict):
            cancel_evt = info.get("cancel")
            if cancel_evt:
                cancel_evt.set()
                logger.info("[tool-relay] cancelled request %s", request_id)

    @classmethod
    def cancel_agent(cls, conversation_id: str, agent_name: str):
        """Cancel all in-flight tool calls for a (conv, agent).

        In-flight requests get their _executing Event set so they unblock,
        and are added to the result cache as "interrupted".
        Does NOT reject future requests — only kills current in-flight ones.
        """
        # Set cancel_event on all in-flight requests for this agent
        with cls._inflight_lock:
            to_cancel = [(rid, info) for rid, info in cls._inflight.items()
                         if isinstance(info, dict)
                         and info.get("conv") == conversation_id
                         and (not agent_name or info.get("agent") == agent_name)]
        for rid, info in to_cancel:
            cancel_evt = info.get("cancel")
            if cancel_evt:
                cancel_evt.set()
        if to_cancel:
            logger.info("[tool-relay] cancelled %d in-flight request(s) for %s/%s",
                        len(to_cancel), conversation_id, agent_name)

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

        if method == "list_tools":
            return self._handle_list_tools(request_id, user_id, conversation_id)
        elif method == "get_tool_schema":
            return self._handle_get_schema(request_id, msg.get("tool_name", ""),
                                           user_id=user_id)
        elif method == "execute_tool":
            _raw_args = msg.get("arguments", {})
            # Defensive: double-encoded JSON string
            if isinstance(_raw_args, str):
                try:
                    _raw_args = json.loads(_raw_args)
                except (json.JSONDecodeError, TypeError):
                    pass
            _tool = msg.get("tool_name", "")
            if not _raw_args or _raw_args == {}:
                logger.warning("[tool-relay] EMPTY ARGS received for %s (request=%s) raw msg keys: %s",
                               _tool, request_id, list(msg.keys()))
                # Don't execute tools with empty args — return empty result
                # This is a phantom call from CC's incremental update pattern
                return {"type": "response", "request_id": request_id,
                        "result": ""}
            return self._handle_execute(
                request_id, _tool, _raw_args,
                user_id, conversation_id, agent_name,
            )
        else:
            return {"type": "error", "request_id": request_id,
                    "error": f"Unknown method: {method}"}

    def _get_registry(self, user_id: str = "", conversation_id: str = "",
                       agent_name: str = ""):
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
            if hasattr(h, 'set_agent_name') and agent_name:
                h.set_agent_name(agent_name)
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

        # Configure media service resolvers (image/video/audio generation)
        from core.handlers.media import ImageGenerationHandler, ImageModelInfoHandler
        from core.handlers.media import VideoGenerationHandler, AudioGenerationHandler
        file_base_url = self.config.get("file_base_url", "") or ""
        for h in registry.list_tools():
            if isinstance(h, (ImageGenerationHandler, ImageModelInfoHandler)):
                if file_base_url and hasattr(h, 'set_base_url'):
                    h.set_base_url(file_base_url)
                h.set_service_resolver(
                    self._make_media_resolver(user_id, conversation_id, "image"))
            elif isinstance(h, VideoGenerationHandler):
                if file_base_url:
                    h.set_base_url(file_base_url)
                h.set_service_resolver(
                    self._make_media_resolver(user_id, conversation_id, "video"))
            elif isinstance(h, AudioGenerationHandler):
                if file_base_url:
                    h.set_base_url(file_base_url)
                h.set_service_resolver(
                    self._make_media_resolver(user_id, conversation_id, "audio"))

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
                    logger.debug("Filesystem services for user '%s': %s",
                                 user_id, [s["id"] for s in available])
            except Exception as e:
                logger.error("Failed to enumerate filesystem services: %s", e)

        return registry

    @staticmethod
    def _make_media_resolver(user_id: str, conversation_id: str, media_type: str):
        """Build a resolver closure for image/video/audio services."""
        def resolver():
            type_map = {
                "image": ("base_image_generation", "BaseImageGenerationService"),
                "video": ("base_video_generation", "BaseVideoGenerationService"),
                "audio": ("base_audio_generation", "BaseAudioGenerationService"),
            }
            mod_name, cls_name = type_map[media_type]
            import importlib
            mod = importlib.import_module(f"services.{mod_name}")
            base_class = getattr(mod, cls_name)

            # Discover valid service types
            try:
                from tasks import _register_all_services
                _register_all_services()
            except Exception:
                pass
            from core import ServiceFactory
            valid_types = set()
            for stype, sclass in ServiceFactory._services.items():
                try:
                    if issubclass(sclass, base_class):
                        valid_types.add(stype)
                except TypeError:
                    pass

            # Find deployed services
            available = []
            try:
                from gui.services.global_service_registry import GlobalServiceRegistry
                greg = GlobalServiceRegistry.get_instance()
                for sid, sdef in greg.get_all_definitions().items():
                    if not getattr(sdef, "enabled", True):
                        continue
                    if (getattr(sdef, "service_type", "") or "") in valid_types:
                        available.append(sid)
            except Exception:
                pass
            if user_id:
                try:
                    from gui.services.user_service_registry import UserServiceRegistry
                    ureg = UserServiceRegistry.get_instance()
                    for sid, sdef in ureg.get_all_for_user(user_id).items():
                        if sid not in available and getattr(sdef, "enabled", True):
                            if (getattr(sdef, "service_type", "") or "") in valid_types:
                                available.append(sid)
                except Exception:
                    pass

            if not available:
                return None, f"No {media_type} generation service deployed"
            # Resolve the first one (or single one)
            sid = available[0]
            try:
                from gui.services.user_service_registry import UserServiceRegistry
                svc = UserServiceRegistry.get_instance().get_live_instance(user_id, sid)
                if svc and hasattr(svc, 'generate'):
                    return svc, None
            except Exception:
                pass
            try:
                from gui.services.global_service_registry import GlobalServiceRegistry
                svc = GlobalServiceRegistry.get_instance().get_live_instance(sid)
                if svc and hasattr(svc, 'generate'):
                    return svc, None
            except Exception:
                pass
            return None, f"{media_type.title()} service '{sid}' failed to connect"
        return resolver

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
                "display_name": h.display_name,
                "description": (h.description or "")[:150],
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

    # Cache for idempotent retries: request_id → result dict
    _result_cache = {}  # shared across instances (class-level)
    _executing = {}     # request_id → threading.Event (in-flight)
    _cache_lock = threading.Lock()

    def _handle_execute(self, request_id: str, tool_name: str,
                        arguments, user_id: str,
                        conversation_id: str, agent_name: str) -> dict:
        # Defensive: arguments may arrive as JSON string (double-encoded by LLM)
        for _ in range(3):
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except (json.JSONDecodeError, TypeError):
                    break
            else:
                break
        # Idempotent: if this request_id was already executed, return cached result
        with self._cache_lock:
            if request_id in self._result_cache:
                logger.info("[tool-relay] returning cached result for %s", request_id)
                return self._result_cache[request_id]
            if request_id in self._executing:
                # Another connection is executing this — wait for it
                evt = self._executing[request_id]
        if request_id in self._executing:
            logger.info("[tool-relay] waiting for in-flight request %s", request_id)
            evt.wait(timeout=300)
            with self._cache_lock:
                if request_id in self._result_cache:
                    return self._result_cache[request_id]
            return {"type": "result", "request_id": request_id,
                    "data": "Error: in-flight request timed out"}

        # Mark as executing — cancel_event can be set to abort immediately
        evt = threading.Event()
        cancel_event = threading.Event()
        with self._cache_lock:
            self._executing[request_id] = evt
        with self._inflight_lock:
            self._inflight[request_id] = {
                "conv": conversation_id,
                "agent": agent_name,
                "cancel": cancel_event,
            }

        # Execute in a daemon thread so cancel can abandon it
        _result_holder = [None]

        def _exec():
            try:
                _result_holder[0] = self._do_execute(
                    request_id, tool_name, arguments,
                    user_id, conversation_id, agent_name)
            except Exception as e:
                _result_holder[0] = {"type": "result", "request_id": request_id,
                                      "data": f"Error: {e}"}
            finally:
                evt.set()

        exec_thread = threading.Thread(target=_exec, daemon=True)
        exec_thread.start()

        # Wait for completion OR cancellation
        while not evt.is_set():
            if cancel_event.wait(timeout=0.5):
                # Cancelled — return interrupt result immediately
                result = {"type": "result", "request_id": request_id,
                          "data": "[Interrupted by user — stop current work and respond to the new message]"}
                with self._cache_lock:
                    self._result_cache[request_id] = result
                    self._executing.pop(request_id, None)
                with self._inflight_lock:
                    self._inflight.pop(request_id, None)
                return result

        result = _result_holder[0]
        # If cancelled while executing, check cache
        with self._cache_lock:
            if request_id in self._result_cache:
                result = self._result_cache[request_id]

        try:
            pass
        finally:
            with self._cache_lock:
                self._result_cache[request_id] = result
                self._executing.pop(request_id, None)
                evt.set()
            with self._inflight_lock:
                self._inflight.pop(request_id, None)
            # Cleanup old cache entries (keep last 100)
            with self._cache_lock:
                if len(self._result_cache) > 100:
                    oldest = list(self._result_cache.keys())[:50]
                    for k in oldest:
                        self._result_cache.pop(k, None)

        return result

    def _do_execute(self, request_id, tool_name, arguments,
                    user_id, conversation_id, agent_name):
        registry = self._get_registry(user_id, conversation_id, agent_name)

        # Tool Approval Gate — reads permission_mode from conversation
        # For task sub-conversations (conv::task::tid), inherit parent's permissions
        _perm_cid = conversation_id
        if conversation_id and '::task::' in conversation_id:
            _perm_cid = conversation_id.split('::task::')[0]
        try:
            _perm_mode = "default"
            _tool_perm = ""
            if _perm_cid:
                from core.conversation_store import ConversationStore
                _cs = ConversationStore.instance()
                _perm_mode = _cs.get_extra(_perm_cid, "permission_mode") or "default"
                _tperms = _cs.get_extra(_perm_cid, "tool_permissions") or {}
                _tool_perm = _tperms.get(tool_name, "")

            # Per-tool override
            if _tool_perm == "deny":
                return {"type": "result", "request_id": request_id,
                        "data": f"Error: Tool '{tool_name}' is denied by permission settings."}
            elif _tool_perm == "allow":
                pass  # explicitly allowed — skip further checks
            elif _tool_perm == "confirm":
                from core.tool_approval import ToolApprovalGate
                _path = arguments.get("path", "") if isinstance(arguments, dict) else ""
                action_summary = f"{tool_name}({_path})" if _path else tool_name
                approval = ToolApprovalGate.check(
                    tool_name, action_summary, _perm_cid, user_id, arguments)
                if approval != "approved":
                    return {"type": "result", "request_id": request_id,
                            "data": f"Error: Tool '{tool_name}' was {approval} by the user."}
            elif _perm_mode == "auto":
                # Auto mode: approve everything EXCEPT catastrophic patterns → always ask
                from core.tool_approval import ToolApprovalGate
                if tool_name in ("bash", "execute_script") and isinstance(arguments, dict):
                    _cmd = arguments.get("command", "") or arguments.get("code", "")
                    if ToolApprovalGate._is_catastrophic_command(_cmd):
                        action_summary = f"\u26a0\ufe0f CATASTROPHIC: {tool_name}({_cmd[:100]})"
                        approval = ToolApprovalGate.check(
                            tool_name, action_summary, _perm_cid, user_id, arguments)
                        if approval != "approved":
                            return {"type": "result", "request_id": request_id,
                                    "data": f"Error: Command rejected by user: {_cmd[:100]}"}
            elif _perm_mode == "read_only":
                _write_tools = {"write", "edit", "batch_edit", "apply_patch", "find_replace",
                                "delete", "mkdir", "bash", "notebook_edit", "execute_script"}
                _fs_write_actions = {"write_file", "edit", "batch_edit", "apply_patch",
                                     "find_replace", "delete_file", "mkdir", "exec",
                                     "git_commit", "git_push", "git_checkout"}
                if tool_name in _write_tools:
                    return {"type": "result", "request_id": request_id,
                            "data": "Error: write operations blocked (read-only mode)."}
                if tool_name == "filesystem" and isinstance(arguments, dict):
                    fs_action = arguments.get("action", "")
                    if fs_action in _fs_write_actions:
                        return {"type": "result", "request_id": request_id,
                                "data": "Error: write operations blocked (read-only mode)."}
            else:
                # default / approve_edits — use approval gate
                from core.tool_approval import ToolApprovalGate
                _path = arguments.get("path", "") if isinstance(arguments, dict) else ""
                action_summary = f"{tool_name}({_path})" if _path else tool_name
                approval = ToolApprovalGate.check(
                    tool_name, action_summary, _perm_cid, user_id, arguments)
                if approval != "approved":
                    return {"type": "result", "request_id": request_id,
                            "data": f"Error: Tool '{tool_name}' was {approval} by the user."}
        except Exception as e:
            logger.warning("Tool approval check failed: %s", e)

        # Resolve env vars (all variables + secrets) and secret values (for redaction)
        _secret_values = set()
        _secret_names = {}
        _all_env = {}
        _secret_cid = conversation_id.split('::task::')[0] if conversation_id and '::task::' in conversation_id else conversation_id
        if user_id and isinstance(arguments, dict):
            try:
                _all_env = resolve_secrets_env(user_id, _secret_cid)
                if _all_env:
                    # Inject as process env vars for shell tools
                    if tool_name in {"bash", "execute_script"}:
                        arguments["_secret_env"] = _all_env
                    # Resolve $VAR / ${VAR} in string arguments
                    # bash: skip 'command' (shell resolves $VAR itself)
                    # execute_script: skip 'code' (Python uses os.environ)
                    _skip = set()
                    if tool_name == "bash":
                        _skip = {"command"}
                    elif tool_name == "execute_script":
                        _skip = {"code"}
                    _resolve_vars_in_args(arguments, _all_env, skip_keys=_skip)
                # Only secrets → redaction
                _secret_values, _secret_names = resolve_secret_values(user_id, _secret_cid)
            except Exception as _se:
                logger.warning("[tool-relay] failed to resolve env/secrets: %s", _se)

        try:
            logger.debug("[tool-relay] execute %s [req=%s]", tool_name, request_id)
            result = registry.execute(tool_name, arguments)
            result_str = str(result) if result is not None else "(no output)"
        except Exception as e:
            result_str = f"Error: {e}"
            logger.error("Tool relay execute '%s' failed: %s", tool_name, e)

        # Sanitize tool result to strip invisible/malicious unicode
        from core.sanitization import sanitize_unicode
        result_str = sanitize_unicode(result_str)

        # Redact secret values from tool output
        if _secret_values:
            result_str = _redact_secrets(result_str, _secret_values,
                                         secret_names=_secret_names)

        return {"type": "result", "request_id": request_id, "data": result_str}

    def _resolve_secrets_env(self, user_id: str, conversation_id: str) -> dict:
        return resolve_secrets_env(user_id, conversation_id)


def resolve_secrets_env(user_id: str, conversation_id: str) -> dict:
    """Resolve ALL variables + secrets into a flat dict for env injection.

    Cascade: global → user → conversation (later overrides earlier).
    Both params (variables) AND secrets are included.
    Returns dict of {KEY: value}. Keys are uppercased.
    """
    from pathlib import Path
    from core.config_store import ConfigStore

    env = {}

    # ── Global variables ──
    _global_params = Path("config/global_params.json")
    for k, cv in ConfigStore.load_params(_global_params).items():
        env[k.upper()] = cv.value if hasattr(cv, 'value') else str(cv)

    # ── Global secrets ──
    _global_secrets = Path("config/global_secrets.json")
    for k, cv in ConfigStore.load_secrets(_global_secrets).items():
        env[k.upper()] = cv.value if hasattr(cv, 'value') else str(cv)

    # ── User variables (override global) ──
    if user_id:
        _user_params = Path("config/users") / user_id / "params.json"
        for k, cv in ConfigStore.load_params(_user_params).items():
            env[k.upper()] = cv.value if hasattr(cv, 'value') else str(cv)

    # ── User secrets (override global) ──
    if user_id:
        _user_secrets = Path("config/users") / user_id / "secrets.json"
        for k, cv in ConfigStore.load_secrets(_user_secrets).items():
            env[k.upper()] = cv.value if hasattr(cv, 'value') else str(cv)

    # ── Conversation variables + secrets (override user) ──
    if conversation_id:
        try:
            from core.conversation_store import ConversationStore
            from core.secrets import get_secrets_manager
            store = ConversationStore.instance()
            sm = get_secrets_manager()

            # Conv params (variables)
            _conv_params = store.get_extra(conversation_id, "conv_params") or {}
            for k, v in _conv_params.items():
                env[k.upper()] = str(v)

            # Conv secrets
            _conv_secrets = store.get_extra(conversation_id, "conv_secrets") or {}
            for k, v in _conv_secrets.items():
                try:
                    env[k.upper()] = sm.decrypt(v) if isinstance(v, str) and v.startswith("enc:") else str(v)
                except Exception:
                    env[k.upper()] = str(v)
        except Exception:
            pass

    return env


def resolve_secret_values(user_id: str, conversation_id: str) -> tuple:
    """Resolve ONLY secret values for redaction (not variables).

    Returns (secret_values: set, secret_names: dict{value→key}).
    """
    from pathlib import Path
    from core.config_store import ConfigStore

    values = set()
    names = {}

    # Global secrets
    _global_secrets = Path("config/global_secrets.json")
    for k, cv in ConfigStore.load_secrets(_global_secrets).items():
        v = cv.value if hasattr(cv, 'value') else str(cv)
        if v and len(v) >= 4:
            values.add(v)
            names[v] = k.upper()

    # User secrets
    if user_id:
        _user_secrets = Path("config/users") / user_id / "secrets.json"
        for k, cv in ConfigStore.load_secrets(_user_secrets).items():
            v = cv.value if hasattr(cv, 'value') else str(cv)
            if v and len(v) >= 4:
                values.add(v)
                names[v] = k.upper()

    # Conversation secrets
    if conversation_id:
        try:
            from core.conversation_store import ConversationStore
            from core.secrets import get_secrets_manager
            _raw = ConversationStore.instance().get_extra(
                conversation_id, "conv_secrets") or {}
            sm = get_secrets_manager()
            for k, v in _raw.items():
                try:
                    v = sm.decrypt(v) if isinstance(v, str) and v.startswith("enc:") else str(v)
                except Exception:
                    v = str(v)
                if v and len(v) >= 4:
                    values.add(v)
                    names[v] = k.upper()
        except Exception:
            pass

    return values, names


# Register with ServiceFactory
ServiceFactory.register(ToolRelayService)

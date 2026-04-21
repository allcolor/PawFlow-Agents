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
import time
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
        from services.http_listener_service import HTTPListenerService
        instances = getattr(HTTPListenerService, '_instances', {}) or {}
        if not instances:
            logger.warning('ToolRelayService %s: no HTTPListenerService running yet, route not registered',
                           self._service_id)
            return
        listener = next(iter(instances.values()))
        route = f'/ws/tools/{self._service_id}'
        self._route_path = route
        listener.register_route('GET', route, self._service_id, callback=None, ws_handler=self._handle_ws)
        self._connection = listener
        logger.info('ToolRelayService %s registered on main listener path %s', self._service_id, route)

    def disconnect(self):
        if self._connection and getattr(self, '_route_path', ''):
            try:
                self._connection.unregister_routes(self._service_id)
            except Exception as e:
                logger.error('Failed to unregister tool relay route %s: %s', self._route_path, e, exc_info=True)
            self._connection = None

    def _handle_ws(self, sock, path_params, meta):
        import asyncio
        remote = meta.get('remote_addr', '?')
        try:
            sock.setblocking(False)
            loop = asyncio.new_event_loop()
            try:
                reader = asyncio.StreamReader(loop=loop)
                protocol = asyncio.StreamReaderProtocol(reader, loop=loop)
                transport, _ = loop.run_until_complete(
                    loop.connect_accepted_socket(lambda: protocol, sock))
                writer = asyncio.StreamWriter(transport, protocol, reader, loop)
                loop.run_until_complete(self._serve_tool_session(reader, writer, loop, remote))
            finally:
                loop.close()
        except Exception as e:
            logger.error('Tool relay WS handler error (%s): %s', remote, e, exc_info=True)

    async def _serve_tool_session(self, reader, writer, loop, remote):
        import asyncio
        from services.filesystem_service import _ws_recv_frame, _ws_send_frame
        service = self
        try:
            opcode, payload = await _ws_recv_frame(reader)
            if opcode != 0x01:
                return
            reg = json.loads(payload.decode('utf-8'))
            if reg.get('type') != 'register':
                return
            relay_token = reg.get('token', '')
            if not relay_token or relay_token != service.config.get('token', ''):
                await _ws_send_frame(writer, json.dumps(
                    {'type': 'error', 'message': 'Token mismatch'}).encode())
                return
            relay_id = reg.get('relay_id', '')
            _user_id = reg.get('user_id', '')
            _conv_id = reg.get('conversation_id', '')
            _agent_name = reg.get('agent_name', '')
            logger.info('Tool relay connected: user=%s conv=%s agent=%s addr=%s',
                         _user_id, _conv_id, _agent_name, remote)
            await _ws_send_frame(writer, json.dumps({
                'type': 'registered', 'relay_id': relay_id}).encode())
            KEEPALIVE = 120
            while True:
                try:
                    opcode, payload = await asyncio.wait_for(
                        _ws_recv_frame(reader), timeout=KEEPALIVE)
                except asyncio.TimeoutError:
                    await _ws_send_frame(writer, json.dumps({'type': 'ping'}).encode())
                    continue
                if opcode == 0x08:
                    break
                if opcode == 0x09:
                    await _ws_send_frame(writer, payload, opcode=0x0A)
                    continue
                if opcode != 0x01:
                    continue
                msg = json.loads(payload.decode('utf-8'))
                if msg.get('type') == 'ping':
                    await _ws_send_frame(writer, json.dumps({'type': 'pong'}).encode())
                    continue
                if msg.get('type') != 'request':
                    continue
                def _exec(m=msg, _ui=_user_id, _ci=_conv_id, _an=_agent_name):
                    try:
                        resp = service.handle_tool_request(m, _ui, _ci, _an)
                    except Exception as e:
                        resp = {'type': 'error',
                                'request_id': m.get('request_id', ''),
                                'error': str(e)}
                    asyncio.run_coroutine_threadsafe(
                        _ws_send_frame(writer, json.dumps(resp).encode('utf-8')),
                        loop)
                threading.Thread(target=_exec, daemon=True,
                                  name=f'tool-relay-{msg.get("method", "?")}').start()
        except Exception as e:
            _err_str = str(e)
            if '0 bytes read' in _err_str:
                logger.info('Tool relay disconnected: %s (closed by peer)', remote)
            else:
                logger.error('Tool relay connection error (%s): %s', remote, e, exc_info=True)
        finally:
            try:
                writer.close()
            except Exception as e:
                logger.debug('writer.close failed: %s', e, exc_info=True)
            logger.info('Tool relay disconnected: %s', remote)



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
    def cancel_request(cls, request_id: str) -> bool:
        """Cancel a single in-flight tool request by its request/tc id.

        `request_id` may be the MCP request_id (internal) OR the CC
        tool_use id (UI-visible) — we try both so kill works regardless
        of which one the caller knows. Returns True if a matching
        in-flight entry was found and cancelled.
        """
        with cls._inflight_lock:
            info = cls._inflight.get(request_id)
            if info is None:
                # Fallback: search by cc_tc_id (what the UI sends)
                for _rid, _info in cls._inflight.items():
                    if isinstance(_info, dict) and _info.get("cc_tc_id") == request_id:
                        info = _info
                        break
        if info and isinstance(info, dict):
            cancel_evt = info.get("cancel")
            if cancel_evt:
                cancel_evt.set()
                logger.info("[tool-relay] cancelled request (cc_tc=%s)",
                            info.get("cc_tc_id") or request_id)
                return True
        return False

    @classmethod
    def background_by_tc_id(cls, tc_id: str) -> bool:
        """Flag an in-flight tool call for backgrounding by its CC tc_id.

        Sets the per-inflight background_event; the wait loop in
        _handle_execute returns the placeholder to CC immediately and
        lets the daemon thread continue. When the thread finishes,
        _inject_result publishes the actual result as a user message.
        """
        with cls._inflight_lock:
            for _rid, info in cls._inflight.items():
                if isinstance(info, dict) and info.get("cc_tc_id") == tc_id:
                    bg_evt = info.get("background")
                    if bg_evt and not bg_evt.is_set():
                        bg_evt.set()
                        logger.info("[tool-relay] backgrounded tc_id=%s (request_id=%s)",
                                    tc_id, _rid)
                        return True
        return False

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
                                           user_id=user_id,
                                           conversation_id=conversation_id)
        elif method == "execute_tool":
            _raw_args = msg.get("arguments", {})
            _tool = msg.get("tool_name", "")
            # Defensive: double-encoded JSON string
            _decode_ok = True
            _original = _raw_args
            _decode_err = None
            if isinstance(_raw_args, str):
                try:
                    _raw_args = json.loads(_raw_args)
                except (json.JSONDecodeError, TypeError) as _je:
                    _decode_err = _je
                    # Forensic dump (no truncation): so we can see the
                    # raw bytes we received from the MCP bridge when this
                    # fires. Pair with mcp_bridge.py's matching dump.
                    logger.warning("[tool-relay] JSON decode FAIL for %s "
                                   "at pos=%s: %s; raw_len=%d raw=%r",
                                   _tool, getattr(_je, "pos", "?"), _je,
                                   len(_original), _original)
                    _decode_ok = False
            # Decode failed on non-empty input → error with position + window
            if not _decode_ok and _original and _original != "{}":
                _detail = str(_decode_err) if _decode_err else "unknown JSON error"
                _raw_str = _original if isinstance(_original, str) else str(_original)
                _window = ""
                _pos = getattr(_decode_err, "pos", None)
                if isinstance(_pos, int) and 0 <= _pos <= len(_raw_str):
                    _lo = max(0, _pos - 120)
                    _hi = min(len(_raw_str), _pos + 120)
                    _prefix = "…" if _lo > 0 else ""
                    _suffix = "…" if _hi < len(_raw_str) else ""
                    _window = (f" Window around char {_pos}: "
                               f"{_prefix}{_raw_str[_lo:_hi]!r}{_suffix}")
                return {"type": "response", "request_id": request_id,
                        "result": (
                            f"Error: failed to decode arguments for {_tool}. "
                            f"Arguments must be a JSON object (dict), not a "
                            f"JSON-encoded string. Parse error: {_detail}.{_window} "
                            f"Fix: resend with arguments as a literal dict; "
                            f"escape embedded newlines/quotes once (\\\\n, \\\\\\\") "
                            f"but do NOT wrap the whole value in a string."
                        )}
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

        Also loads dynamic tools (per-conversation) and MCP server tools
        (per-agent) so they are available via the MCP bridge.
        """
        from core.tool_registry import create_default_registry
        registry = create_default_registry()

        # Load dynamic tools for this conversation
        if conversation_id:
            try:
                from core.handlers.dynamic_tool import load_dynamic_tools
                _parent_cid = (conversation_id.split("::task::")[0]
                               if "::task::" in conversation_id
                               else conversation_id)
                load_dynamic_tools(_parent_cid, registry)
            except Exception as e:
                logger.warning("[tool-relay] Failed to load dynamic tools: %s", e)

        # Load MCP server tools for the active agent
        if conversation_id and user_id:
            self._load_mcp_tools(registry, user_id, conversation_id)

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

        # Configure SpawnAgentsHandler (delegate) — needs a client_resolver
        # to look up per-agent LLM services. Without this, delegate fails
        # with "Agent executor not configured (missing client_resolver)".
        try:
            from core.handlers.resource_agent import SpawnAgentsHandler
            from core.llm_client import LLMClient
            from core.service_registry import ServiceRegistry as _SR

            def _client_resolver(svc_id, uid):
                _reg = _SR.get_instance()
                _tried = []
                for _scope, _sid in (("user", uid), ("global", "")):
                    try:
                        _svc_def = _reg.get_definition(_scope, _sid, svc_id)
                        if not _svc_def:
                            _tried.append(f"{_scope}/{_sid}:missing")
                            continue
                        _live = _reg.get_live_instance(_scope, _sid, svc_id)
                        if _live and hasattr(_live, "get_client"):
                            return _live.get_client(), _live
                        _tried.append(f"{_scope}/{_sid}:no-live-instance")
                    except Exception as _re:
                        _tried.append(f"{_scope}/{_sid}:{type(_re).__name__}:{_re}")
                logger.warning(
                    "[tool-relay] could not resolve llm_service '%s' "
                    "for user '%s' (tried: %s)",
                    svc_id, uid, ", ".join(_tried) or "none")
                return None, None

            # NO default LLM client. An agent's llm_service is always
            # resolved per-task via _client_resolver (from the conv_agents
            # link of the delegate target). If resolution fails, the
            # sub-agent errors out — never silently falls back to
            # "whatever LLM was enabled first".

            # Bridge sub-agent events to the conversation SSE bus so the
            # webchat can render delegate blocks live (mirrors the wiring in
            # tasks/ai/agent_context.py for non-CC agents).
            _parent_cid_for_events = (
                conversation_id.split("::task::")[0]
                if conversation_id and "::task::" in conversation_id
                else (conversation_id or "")
            )

            def _sub_on_event(event_type, data):
                if not _parent_cid_for_events:
                    return
                try:
                    from core.conversation_event_bus import ConversationEventBus
                    ConversationEventBus.instance().publish_event(
                        _parent_cid_for_events, event_type, data)
                except Exception:
                    pass

            for h in registry.list_tools():
                if isinstance(h, SpawnAgentsHandler):
                    h.set_spawn_deps(None, _client_resolver,
                                      on_event=_sub_on_event, registry=registry)
        except Exception as _e:
            logger.warning("[tool-relay] SpawnAgents wiring failed: %s", _e)

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
            elif h.name in ("describe_image", "remix_image"):
                if file_base_url and hasattr(h, 'set_base_url'):
                    h.set_base_url(file_base_url)
                if hasattr(h, 'set_user_id'):
                    h.set_user_id(user_id)
                if hasattr(h, 'set_conversation_id'):
                    h.set_conversation_id(conversation_id)
                h.set_service_resolver(
                    self._make_media_resolver(user_id, conversation_id, "image"))
            elif h.name in ("speech_to_video",):
                if file_base_url and hasattr(h, 'set_base_url'):
                    h.set_base_url(file_base_url)
                if hasattr(h, 'set_user_id'):
                    h.set_user_id(user_id)
                if hasattr(h, 'set_conversation_id'):
                    h.set_conversation_id(conversation_id)
                h.set_service_resolver(
                    self._make_media_resolver(user_id, conversation_id, "video"))
            elif h.name in ("upscale_video", "remove_background"):
                if file_base_url and hasattr(h, 'set_base_url'):
                    h.set_base_url(file_base_url)
                if hasattr(h, 'set_user_id'):
                    h.set_user_id(user_id)
                if hasattr(h, 'set_conversation_id'):
                    h.set_conversation_id(conversation_id)
                h.set_service_resolver(
                    self._make_media_resolver(user_id, conversation_id, "upscale"))

        # Populate available services on all BaseFsHandler instances
        from core.handlers._fs_base import BaseFsHandler, _FS_TYPES
        _fs_handlers = [h for h in registry.list_tools() if isinstance(h, BaseFsHandler)]
        if _fs_handlers:
            try:
                available = []
                from core.service_registry import ServiceRegistry
                _sreg = ServiceRegistry.get_instance()
                for fs_type in _FS_TYPES:
                    for sdef in _sreg.resolve_by_type(fs_type, user_id=user_id):
                        svc = _sreg.resolve(sdef.service_id, user_id=user_id)
                        if svc:
                            available.append({
                                "id": sdef.service_id, "type": sdef.service_type,
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
                "upscale": ("base_capabilities", "BaseImageUpscaleService"),
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
            # Find all matching services across scopes (conv > user > global)
            from core.service_registry import ServiceRegistry
            _sreg = ServiceRegistry.get_instance()
            matching = []
            for vtype in valid_types:
                matching.extend(_sreg.resolve_by_type(vtype, user_id=user_id))

            if not matching:
                return None, f"No {media_type} generation service deployed"
            # Resolve the first one
            sid = matching[0].service_id
            svc = _sreg.resolve(sid, user_id=user_id)
            if svc:
                # Different service types expose different methods
                check = {'upscale': 'upscale'}.get(media_type, 'generate')
                if hasattr(svc, check):
                    return svc, None
            return None, f"{media_type.title()} service '{sid}' failed to connect"
        return resolver

    def _load_mcp_tools(self, registry, user_id: str, conversation_id: str):
        """Load MCP server tools for the active agent into registry."""
        try:
            from core.resource_store import ResourceStore
            rs = ResourceStore.instance()

            # All MCP servers accessible in scope (global + user + conversation)
            # are auto-active — no per-conversation linking required.
            _all_mcps = rs.list_all("mcp", user_id, conversation_id=conversation_id) or []
            active_mcps = [m.get("name", "") for m in _all_mcps if m.get("name")]
            if not active_mcps:
                return

            for mcp_name in active_mcps:
                try:
                    raw_def = rs.get_any("mcp", mcp_name, user_id)
                    if not raw_def:
                        continue
                    from core.expression import resolve_value
                    mcp_def = resolve_value(raw_def, owner=user_id,
                                             conversation_id=conversation_id)
                    transport = mcp_def.get("transport", "http")
                    via = mcp_def.get("via", "") or (
                        "relay" if transport == "stdio" else "direct")
                    auth = mcp_def.get("auth", {})
                    if isinstance(auth, str):
                        auth = {"Authorization": auth}

                    disc_tools = []
                    relay_svc = None

                    if via == "relay":
                        _rsid = mcp_def.get("relay_service", "")
                        if _rsid:
                            try:
                                from core.service_registry import ServiceRegistry
                                relay_svc = ServiceRegistry.get_instance().resolve(
                                    _rsid, user_id=user_id)
                            except Exception:
                                pass
                        if not relay_svc:
                            relay_svc = self._find_filesystem_service(user_id)
                        if not relay_svc:
                            logger.warning("[tool-relay][mcp] No relay for '%s'", mcp_name)
                            continue
                        if transport == "stdio":
                            try:
                                relay_svc._request("mcp_start", ".", **{
                                    "server_id": mcp_name,
                                    "command": mcp_def.get("command", ""),
                                    "args": mcp_def.get("args", []),
                                    "env": mcp_def.get("env", {}),
                                })
                            except Exception as e:
                                if "already_running" not in str(e):
                                    logger.error("[tool-relay][mcp] Start failed '%s': %s",
                                                 mcp_name, e)
                                    continue
                        try:
                            disc = relay_svc._request("mcp_discover", ".",
                                                      server_id=mcp_name)
                            disc_tools = (disc.get("tools", [])
                                          if isinstance(disc, dict) else [])
                        except Exception as e:
                            logger.error("[tool-relay][mcp] Discovery failed '%s': %s",
                                         mcp_name, e)
                    else:
                        url = mcp_def.get("url", "")
                        if not url:
                            continue
                        from core.tool_registry import discover_mcp_tools
                        disc_tools = discover_mcp_tools(
                            url, headers=auth, timeout=10)

                    from core.handlers.agent_tools import MCPToolHandler
                    for mt in disc_tools:
                        h = MCPToolHandler(
                            tool_name=mt["name"],
                            tool_description=mt.get("description", ""),
                            tool_parameters=mt.get("inputSchema", {
                                "type": "object", "properties": {}}),
                            server_url=mcp_def.get("url", ""),
                            mcp_tool_name=mt["name"],
                            headers=auth,
                            transport=transport if via == "relay" else "http",
                            server_id=mcp_name,
                            relay_service=relay_svc,
                        )
                        registry.register(h)
                    if disc_tools:
                        logger.info("[tool-relay][mcp] Loaded %d tools from '%s' (%s/%s)",
                                    len(disc_tools), mcp_name, via, transport)
                except Exception as e:
                    logger.warning("[tool-relay][mcp] Failed to load '%s': %s", mcp_name, e)
        except Exception as e:
            logger.warning("[tool-relay] Failed to load MCP tools: %s", e)

    @staticmethod
    def _find_filesystem_service(user_id: str = ""):
        """Find the first live filesystem service for this user.

        Same logic as agent_utils._find_filesystem_service but standalone.
        """
        fs_types = ("relay", "filesystem", "googleDrive", "oneDrive")
        try:
            from core.service_registry import ServiceRegistry
            reg = ServiceRegistry.get_instance()
            for fs_type in fs_types:
                for sdef in reg.resolve_by_type(fs_type, user_id=user_id):
                    svc = reg.resolve(sdef.service_id, user_id=user_id)
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
                           user_id: str = "",
                           conversation_id: str = "") -> dict:
        registry = self._get_registry(user_id, conversation_id)
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

        # Match CC tool_use id (enqueued by claude_code provider when it
        # emitted the tool_call SSE event). Matching lets background /
        # kill actions, keyed by UI-visible tc_id, reach this request.
        cc_tc_id = ""
        try:
            from core.background_tool import pop_cc_tc, _args_hash
            cc_tc_id = pop_cc_tc(
                conversation_id, agent_name, tool_name,
                _args_hash(arguments))
        except Exception as _me:
            logger.debug("[tool-relay] cc_tc match skipped: %s", _me)

        # Mark as executing — cancel_event can abort, background_event
        # detaches the call (returns placeholder, thread keeps running).
        evt = threading.Event()
        cancel_event = threading.Event()
        background_event = threading.Event()
        started_at = time.time()
        with self._cache_lock:
            self._executing[request_id] = evt
        with self._inflight_lock:
            self._inflight[request_id] = {
                "conv": conversation_id,
                "agent": agent_name,
                "cancel": cancel_event,
                "background": background_event,
                "cc_tc_id": cc_tc_id,
                "tool_name": tool_name,
                "started_at": started_at,
            }

        # Execute in a daemon thread so cancel/background can let it run on.
        _result_holder = [None]

        def _exec():
            # Expose the cancel event to the tool's call stack via
            # thread-local — long-running tools (Pixazo poll loops,
            # browser automation, anything with its own retry/wait)
            # can read it and abort early instead of hammering the
            # remote API after the user already clicked Kill.
            _set_current_cancel_event(cancel_event)
            try:
                _result_holder[0] = self._do_execute(
                    request_id, tool_name, arguments,
                    user_id, conversation_id, agent_name)
            except Exception as e:
                _result_holder[0] = {"type": "result", "request_id": request_id,
                                      "data": f"Error: {e}"}
            finally:
                _set_current_cancel_event(None)
                evt.set()

        exec_thread = threading.Thread(target=_exec, daemon=True)
        exec_thread.start()

        # Wait for completion, cancel, or background (including auto-BG
        # after 5 minutes — project rule: long-running tools must not
        # block the agent loop).
        _auto_bg_after = 300.0  # 5 min — matches UI expectation
        while not evt.is_set():
            if cancel_event.wait(timeout=0.5):
                # Cancelled — return interrupt result immediately. The
                # daemon thread is abandoned; best-effort subprocess kill
                # is the relay's responsibility.
                result = {"type": "result", "request_id": request_id,
                          "data": "[Interrupted by user — stop current work and respond to the new message]"}
                with self._cache_lock:
                    self._result_cache[request_id] = result
                    self._executing.pop(request_id, None)
                with self._inflight_lock:
                    self._inflight.pop(request_id, None)
                return result

            # Auto-BG after 5 minutes — only meaningful when we have a
            # cc_tc_id (LLM-API providers have their own backgrounding
            # via agent_tool_exec.py).
            if (cc_tc_id and not background_event.is_set()
                    and time.time() - started_at >= _auto_bg_after):
                logger.info("[tool-relay] auto-background after %ds for tc_id=%s",
                            int(_auto_bg_after), cc_tc_id)
                background_event.set()

            if background_event.is_set():
                # Return placeholder now; spawn a watcher to inject the
                # real result (or kill notice) as a user message when
                # the daemon thread finishes.
                placeholder = (
                    f"[Running in background (tc_id={cc_tc_id})]\n"
                    f"The actual result will be delivered in a separate "
                    f"user message once the tool completes. Continue your "
                    f"work — do not wait for it."
                )
                result = {"type": "result", "request_id": request_id,
                          "data": placeholder}

                def _watch_bg_completion(_evt, _holder, _tc, _conv, _agent,
                                         _tool, _uid, _cancel):
                    # Wake up on either exec-done or user-kill, whichever
                    # comes first. Hard 8h ceiling guards against a stuck
                    # subprocess on a crashed relay.
                    _deadline = time.time() + 8 * 3600
                    while time.time() < _deadline:
                        if _evt.wait(timeout=0.5):
                            break
                        if _cancel.is_set():
                            break
                    _was_cancelled = _cancel.is_set() and not _evt.is_set()
                    _res = _holder[0] or {}
                    _payload = _res.get("data", "") if isinstance(_res, dict) else str(_res)
                    if _was_cancelled and not _payload:
                        _payload = "[Cancelled before any output]"
                    try:
                        import core.background_tool as _bg
                        # Register lazily so _inject_result has context
                        # (we don't use a real Future here — the exec is
                        # already captured in _holder).
                        with _bg._lock:
                            _bg._backgrounded[_tc] = {
                                "future": None,
                                "conversation_id": _conv,
                                "agent_name": _agent,
                                "tool_name": _tool,
                                "user_id": _uid,
                                "is_claude_code": True,
                                "started_at": started_at,
                                "status": "cancelled" if _was_cancelled else "done",
                                "result": _payload,
                            }
                        _bg._inject_result(_tc, _payload, is_cancel=_was_cancelled)
                    except Exception as _ie:
                        logger.error("[tool-relay] bg inject failed for %s: %s",
                                     _tc, _ie)

                if cc_tc_id:
                    threading.Thread(
                        target=_watch_bg_completion,
                        args=(evt, _result_holder, cc_tc_id, conversation_id,
                              agent_name, tool_name, user_id, cancel_event),
                        daemon=True,
                        name=f"bg-watch-{cc_tc_id[:12]}",
                    ).start()

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

        # For delegate calls, set thread-local source_agent + delegate_tc_id
        # on the SpawnAgentsHandler so sub_agent_* SSE events carry the
        # delegate_tc_id that the chat UI uses to render delegate-blocks
        # (otherwise the events fall back to a generic task-block).
        if tool_name == "delegate":
            try:
                from core.handlers.resource_agent import SpawnAgentsHandler
                _src_svc = ""
                try:
                    _parent_cid = (conversation_id.split("::task::")[0]
                                   if conversation_id and "::task::" in conversation_id
                                   else conversation_id)
                    if _parent_cid and agent_name:
                        from core.conv_agent_config import get_agent_config as _gac
                        _src_svc = (_gac(_parent_cid, agent_name) or {}).get("llm_service", "") or ""
                except Exception:
                    pass
                for _h in registry.list_tools():
                    if isinstance(_h, SpawnAgentsHandler):
                        _h.set_source_agent(agent_name or "", _src_svc)
                        _h.set_delegate_tc_id(request_id)
            except Exception as _de:
                logger.debug("[tool-relay] failed to set delegate ctx: %s", _de)

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

    from core.paths import GLOBAL_PARAMS_FILE, GLOBAL_SECRETS_FILE, USER_CONFIG_DIR

    # ── Global variables ──
    for k, cv in ConfigStore.load_params(GLOBAL_PARAMS_FILE).items():
        env[k.upper()] = cv.value if hasattr(cv, 'value') else str(cv)

    # ── Global secrets ──
    for k, cv in ConfigStore.load_secrets(GLOBAL_SECRETS_FILE).items():
        env[k.upper()] = cv.value if hasattr(cv, 'value') else str(cv)

    # ── User variables (override global) ──
    if user_id:
        for k, cv in ConfigStore.load_params(USER_CONFIG_DIR / user_id / "params.json").items():
            env[k.upper()] = cv.value if hasattr(cv, 'value') else str(cv)

    # ── User secrets (override global) ──
    if user_id:
        for k, cv in ConfigStore.load_secrets(USER_CONFIG_DIR / user_id / "secrets.json").items():
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

    from core.paths import GLOBAL_SECRETS_FILE, USER_CONFIG_DIR

    # Global secrets
    for k, cv in ConfigStore.load_secrets(GLOBAL_SECRETS_FILE).items():
        v = cv.value if hasattr(cv, 'value') else str(cv)
        if v and len(v) >= 4:
            values.add(v)
            names[v] = k.upper()

    # User secrets
    if user_id:
        for k, cv in ConfigStore.load_secrets(USER_CONFIG_DIR / user_id / "secrets.json").items():
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


# ── Thread-local cancel-event ────────────────────────────────────────
# Populated by `_handle_execute._exec()` for the lifetime of one tool
# dispatch so any code in the call stack (Pixazo poll loops, browser
# automation, long-running waits) can check `current_cancel_event()`
# and abort early when the user clicks Kill.
_thread_local = threading.local()


def _set_current_cancel_event(evt):
    _thread_local.cancel_event = evt


def current_cancel_event():
    """Return the cancel Event for the currently-executing tool, or
    None when not running inside a tool dispatch (tests, direct calls).
    """
    return getattr(_thread_local, "cancel_event", None)

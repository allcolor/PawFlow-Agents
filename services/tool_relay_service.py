"""Tool Relay Service — WebSocket listener for MCP bridge connections.

Same pattern as RelayService: registers /ws/tools/<service_id> on the main
HTTPListenerService and accepts MCP bridge connections (Claude Code
subprocess) that execute PawFlow tools.

Config:
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
        self._service_id = config.get("_service_id", "")
        self._connection = None  # main HTTPListenerService ref (set by connect)
        try:
            self._auto_bg_after_seconds = float(
                config.get("auto_background_after_seconds", 0) or 0)
        except (TypeError, ValueError):
            self._auto_bg_after_seconds = 0.0

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "token": {"type": "string", "required": True, "sensitive": True,
                      "description": "Authentication token (MCP bridge must match)"},
            "auto_background_after_seconds": {
                "type": "number",
                "required": False,
                "default": 0,
                "description": (
                    "Optional auto-background delay for long tool calls. "
                    "0 disables implicit backgrounding; use explicit "
                    "run_in_background/background controls instead."),
            },
        }

    @property
    def service_id(self) -> str:
        return self._service_id

    def connect(self):
        from services.http_listener_service import HTTPListenerService
        instances = HTTPListenerService.all_instances()
        if not instances:
            logger.warning('ToolRelayService %s: no HTTPListenerService running yet, route not registered',
                           self._service_id)
            self._connection = None
            self._initialized = False
            return
        listener = next(iter(instances.values()))
        route = f'/ws/tools/{self._service_id}'
        self._route_path = route
        listener.register_route('GET', route, self._service_id, callback=None, ws_handler=self._handle_ws)
        self._connection = listener
        self._initialized = True
        logger.info('ToolRelayService %s registered on main listener path %s', self._service_id, route)

    def disconnect(self):
        if self._connection and getattr(self, '_route_path', ''):
            try:
                self._connection.unregister_routes(self._service_id)
            except Exception as e:
                logger.error('Failed to unregister tool relay route %s: %s', self._route_path, e, exc_info=True)
        self._connection = None
        self._initialized = False
        self._route_path = ''

    def _handle_ws(self, sock, path_params, meta):
        import asyncio
        from services.filesystem_service import _attach_sync_sock_to_loop
        remote = meta.get('remote_addr', '?')
        try:
            loop = asyncio.new_event_loop()
            try:
                reader, writer = _attach_sync_sock_to_loop(sock, loop)
                loop.run_until_complete(self._serve_tool_session(reader, writer, loop, remote))
            finally:
                loop.close()
        except Exception as e:
            logger.error('Tool relay WS handler error (%s): %s', remote, e, exc_info=True)

    async def _serve_tool_session(self, reader, writer, loop, remote):
        import asyncio
        from services.filesystem_service import _ws_recv_frame, _ws_send_frame
        service = self
        # Capture for the finally-log so a disconnect can be traced
        # back to the specific CC session it belonged to. Without this,
        # every disconnect just says "addr=10.x.x.x" and the previous
        # CC's lagging cleanup looks identical to a newly-started
        # session failing immediately.
        _conv_id = ""
        _agent_name = ""
        _already_logged_disconnect = False
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
                    # Shutdown race: the worker thread may still be
                    # running handle_tool_request when the server tears
                    # down and closes `loop`. Build the coroutine only
                    # if the loop is still alive; otherwise close it so
                    # asyncio doesn't warn about an un-awaited coroutine.
                    if loop.is_closed():
                        logger.debug(
                            "[tool-relay] skipping response for %s — "
                            "loop closed (server shutdown)",
                            m.get('method', '?'))
                        return
                    _coro = _ws_send_frame(
                        writer, json.dumps(resp).encode('utf-8'))
                    try:
                        asyncio.run_coroutine_threadsafe(_coro, loop)
                    except RuntimeError as _re:
                        _coro.close()
                        logger.debug(
                            "[tool-relay] response dropped for %s: %s",
                            m.get('method', '?'), _re)
                threading.Thread(target=_exec, daemon=True,
                                  name=f'tool-relay-{msg.get("method", "?")}').start()
        except Exception as e:
            _err_str = str(e)
            # Peer-initiated close (normal end of session): treat as info.
            # Covers "0 bytes read" (clean FIN), ECONNRESET (WinError 10054
            # on Windows — container killed mid-read), IncompleteReadError
            # from StreamReader, and ConnectionResetError from the sync
            # reader pump.
            _peer_close = (
                '0 bytes read' in _err_str
                or '10054' in _err_str
                or 'reset by peer' in _err_str.lower()
                or isinstance(e, (ConnectionResetError, asyncio.IncompleteReadError)))
            if _peer_close:
                logger.info(
                    'Tool relay disconnected (closed by peer): '
                    'conv=%s agent=%s addr=%s',
                    _conv_id, _agent_name, remote)
            else:
                logger.error(
                    'Tool relay connection error: conv=%s agent=%s '
                    'addr=%s err=%s',
                    _conv_id, _agent_name, remote, e, exc_info=True)
            _already_logged_disconnect = True
        finally:
            try:
                writer.close()
            except Exception as e:
                logger.debug('writer.close failed: %s', e, exc_info=True)
            # Only log here if the except didn't already (registration
            # reject / clean exit from the receive loop with no
            # exception). Prevents the old double-line spam.
            if not _already_logged_disconnect:
                logger.info(
                    'Tool relay disconnected: conv=%s agent=%s addr=%s',
                    _conv_id, _agent_name, remote)



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
    _auto_bg_after_seconds: float = 0.0

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
            _hooks = list(info.get("kill_hooks") or [])
            _success = 0
            _failure = 0
            for hook in _hooks:
                try:
                    hook()
                    _success += 1
                except Exception as _he:
                    _failure += 1
                    logger.warning(
                        "[tool-relay] kill_hook failed for targeted %s tool=%s: %s",
                        request_id, info.get("tool_name"), _he)
            logger.info(
                "[tool-relay] targeted cancel request=%s tool=%s "
                "kill_hook_count=%d kill_hook_success=%d kill_hook_failed=%d",
                request_id, info.get("tool_name", "?"),
                len(_hooks), _success, _failure)
            if cancel_evt:
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
                    elif bg_evt and bg_evt.is_set():
                        logger.info(
                            "[tool-relay] tc_id=%s already backgrounded (request_id=%s)",
                            tc_id, _rid)
                        return True
                if isinstance(info, dict) and (_rid == tc_id or info.get("bg_tc_id") == tc_id):
                    bg_evt = info.get("background")
                    if bg_evt and not bg_evt.is_set():
                        bg_evt.set()
                        logger.info("[tool-relay] backgrounded request_id=%s", _rid)
                        return True
                    elif bg_evt and bg_evt.is_set():
                        logger.info("[tool-relay] request_id=%s already backgrounded", _rid)
                        return True
            # No match — report the available cc_tc_ids so we can see whether
            # the in-flight request registered a different id, or none at all.
            _inflight_snap = [
                (_rid, (info or {}).get("cc_tc_id", ""),
                 (info or {}).get("tool_name", ""))
                for _rid, info in cls._inflight.items()
                if isinstance(info, dict)
            ]
        logger.info(
            "[tool-relay] bg MISS tc_id=%s — in-flight=%s",
            tc_id, _inflight_snap)
        return False

    @classmethod
    def bind_pending_cc_tc(cls, conversation_id: str, agent_name: str,
                           tc_id: str, tool_name: str,
                           args_hash: str) -> bool:
        """Attach a provider tool_call id to an already in-flight relay request.

        Codex/Gemini app-server can dispatch the MCP execute request before
        the provider stream publishes the UI-visible tool_call event. This
        late bind repairs that ordering so background/kill still targets the
        running request.
        """
        with cls._inflight_lock:
            for rid, info in cls._inflight.items():
                if not isinstance(info, dict):
                    continue
                if info.get("conv") != conversation_id:
                    continue
                if info.get("agent") != agent_name:
                    continue
                if info.get("tool_name") != tool_name:
                    continue
                if info.get("args_hash") != args_hash:
                    continue
                info["cc_tc_id"] = tc_id
                info["bg_tc_id"] = tc_id
                bg_evt = info.get("background")
                try:
                    from core.background_tool import is_backgrounded
                    if bg_evt and is_backgrounded(tc_id):
                        bg_evt.set()
                except Exception:
                    pass
                logger.debug(
                    "[tool-relay] late-bound cc_tc=%s to request_id=%s tool=%s",
                    tc_id, rid, tool_name)
                return True
        return False

    @classmethod
    def cancel_agent(cls, conversation_id: str, agent_name: str):
        """Cancel all in-flight tool calls for a (conv, agent).

        Two-phase: 1) set cancel_event so cooperative loops abort,
        2) invoke every registered kill_hook so subprocesses, sockets,
        and other non-cooperative resources are torn down. Without
        phase 2 the daemon exec thread keeps running after FORCE STOP
        — a real risk for tools with side effects (HTTP writes, file
        writes, spawned processes).

        Does NOT reject future requests — only kills current in-flight ones.
        """
        with cls._inflight_lock:
            to_cancel = [(rid, info) for rid, info in cls._inflight.items()
                         if isinstance(info, dict)
                         and info.get("conv") == conversation_id
                         and (not agent_name or info.get("agent") == agent_name)]
        _hook_total = 0
        _hook_failed = 0
        for rid, info in to_cancel:
            cancel_evt = info.get("cancel")
            cancel_evt_set = False
            if cancel_evt:
                cancel_evt.set()
                cancel_evt_set = True
            _hooks = list(info.get("kill_hooks") or [])
            _success = 0
            _failure = 0
            for hook in _hooks:
                try:
                    hook()
                    _success += 1
                except Exception as _he:
                    _failure += 1
                    logger.warning(
                        "[tool-relay] kill_hook failed for %s tool=%s: %s",
                        rid, info.get("tool_name"), _he)
            _hook_total += _success + _failure
            _hook_failed += _failure
            # Per-request structured trace so the cancellation path is
            # observable in production logs without enabling debug.
            logger.info(
                "[tool-relay] cancel rid=%s tool=%s "
                "cancel_event_set=%s kill_hook_count=%d kill_hook_success=%d "
                "kill_hook_failed=%d",
                rid, info.get("tool_name", "?"),
                cancel_evt_set, len(_hooks), _success, _failure)
        if to_cancel:
            logger.info(
                "[tool-relay] cancelled %d in-flight request(s) for %s/%s "
                "(kill_hooks total=%d failed=%d)",
                len(to_cancel), conversation_id, agent_name,
                _hook_total, _hook_failed)

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

        # Load dynamic tools (global + user + conv) for this user/conv.
        if user_id:
            try:
                from core.tool_loader import load_tools_into_registry
                _parent_cid = (conversation_id.split("::task::")[0]
                               if conversation_id and "::task::" in conversation_id
                               else (conversation_id or ""))
                load_tools_into_registry(
                    registry, user_id, _parent_cid)
            except Exception as e:
                logger.warning("[tool-relay] Failed to load dynamic tools: %s", e)

        # Load MCP server tools for the active agent
        if conversation_id and user_id:
            self._load_mcp_tools(registry, user_id, conversation_id, agent_name)

        if conversation_id:
            try:
                from core.tool_mcp_filters import is_tool_enabled
                for _handler in list(registry.list_tools()):
                    if not is_tool_enabled(
                            conversation_id, _handler.name, agent_name,
                            getattr(_handler, "_origin", "builtin"),
                            getattr(_handler, "_origin_scope", "")):
                        registry.unregister(_handler.name)
            except Exception as e:
                logger.debug("[tool-relay] tool availability filter failed: %s", e)

        # Find the default linked filesystem service for this conversation.
        fs_svc = self._find_filesystem_service(user_id, conversation_id)
        fs_resolver = self._make_filesystem_resolver(
            user_id, conversation_id, default_service=fs_svc)

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
            if fs_svc or fs_resolver:
                if hasattr(h, 'set_fs_resolver') and fs_resolver:
                    h.set_fs_resolver(fs_resolver)
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

        # Populate conversation-linked filesystems on all BaseFsHandler instances.
        from core.handlers._fs_base import BaseFsHandler, _FS_TYPES
        _fs_handlers = [h for h in registry.list_tools() if isinstance(h, BaseFsHandler)]
        if _fs_handlers:
            try:
                available = self._list_available_filesystem_services(
                    user_id, conversation_id, _FS_TYPES)
                for h in _fs_handlers:
                    h.set_available_services(available)
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

    def _load_mcp_tools(self, registry, user_id: str, conversation_id: str,
                        agent_name: str = ""):
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
                    from core.tool_mcp_filters import is_enabled
                    if not is_enabled(conversation_id, mcp_name, agent_name, kind="mcps"):
                        continue
                    raw_def = rs.get_any("mcp", mcp_name, user_id,
                                         conversation_id=conversation_id)
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
                                    _rsid, user_id=user_id, conv_id=conversation_id)
                            except Exception:
                                pass
                        if not relay_svc:
                            relay_svc = self._find_filesystem_service(user_id, conversation_id)
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
                                    "local": bool(mcp_def.get("local")),
                                })
                            except Exception as e:
                                if "already_running" not in str(e):
                                    logger.error("[tool-relay][mcp] Start failed '%s': %s",
                                                 mcp_name, e)
                                    continue
                        try:
                            disc = relay_svc._request("mcp_discover", ".",
                                                      server_id=mcp_name,
                                                      local=bool(mcp_def.get("local")))
                            disc_tools = (disc.get("tools", [])
                                          if isinstance(disc, dict) else [])
                        except Exception as e:
                            logger.error("[tool-relay][mcp] Discovery failed '%s': %s",
                                         mcp_name, e)
                    else:
                        url = mcp_def.get("url", "")
                        if not url:
                            continue
                        try:
                            from core.relay_proxy_url import maybe_transform_relay_proxy_url
                            url = maybe_transform_relay_proxy_url(url, user_id=user_id) or url
                        except Exception:
                            logger.debug("mcp relay-proxy URL transform failed", exc_info=True)
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
                            server_url=url if via != "relay" else mcp_def.get("url", ""),
                            mcp_tool_name=mt["name"],
                            headers=auth,
                            transport=transport if via == "relay" else "http",
                            server_id=mcp_name,
                            relay_service=relay_svc,
                            local=bool(mcp_def.get("local")),
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
    def _list_available_filesystem_services(user_id: str = "", conversation_id: str = "",
                                            fs_types=("relay", "filesystem", "googleDrive", "oneDrive")):
        """List filesystem services explicitly linked to this conversation."""
        available = []
        seen = set()
        try:
            from core.service_registry import ServiceRegistry
            reg = ServiceRegistry.get_instance()
            if conversation_id:
                try:
                    from core.relay_bindings import get_linked
                    for sid in get_linked(conversation_id):
                        if sid in seen:
                            continue
                        sdef = reg.resolve_definition(sid, user_id=user_id, conv_id=conversation_id)
                        if not sdef or sdef.service_type not in ("relay", "filesystem"):
                            continue
                        seen.add(sid)
                        svc = reg.resolve(sid, user_id=user_id, conv_id=conversation_id)
                        available.append({
                            "id": sid,
                            "type": sdef.service_type,
                            "scope": sdef.scope,
                            "root": getattr(svc, "root_path", "?") if svc else "?",
                        })
                except Exception:
                    pass
                try:
                    from core.remote_fs_bindings import list_tool_filesystems
                    for item in list_tool_filesystems(user_id, conversation_id):
                        sid = item.get("id", "")
                        if not sid or sid in seen:
                            continue
                        seen.add(sid)
                        available.append(item)
                except Exception:
                    pass
                return available

            for fs_type in fs_types:
                for sdef in reg.resolve_by_type(fs_type, user_id=user_id):
                    if sdef.service_id in seen:
                        continue
                    svc = reg.resolve(sdef.service_id, user_id=user_id)
                    if svc:
                        seen.add(sdef.service_id)
                        available.append({
                            "id": sdef.service_id, "type": sdef.service_type,
                            "scope": sdef.scope,
                            "root": getattr(svc, "root_path", "?"),
                        })
        except Exception:
            pass
        return available

    @staticmethod
    def _make_filesystem_resolver(user_id: str = "", conversation_id: str = "",
                                  default_service=None):
        def resolver(service_id: str = "", *_args):
            try:
                from core.service_registry import ServiceRegistry
                reg = ServiceRegistry.get_instance()
                available = ToolRelayService._list_available_filesystem_services(
                    user_id, conversation_id)
                allowed = [item.get("id", "") for item in available if item.get("id")]
                if service_id in ("", "workspace", "ws", "local") and default_service:
                    return default_service
                if conversation_id:
                    if service_id in ("", "workspace", "ws", "local"):
                        service_id = allowed[0] if allowed else ""
                    if not service_id or service_id not in allowed:
                        return None
                return reg.resolve(service_id, user_id=user_id, conv_id=conversation_id)
            except Exception:
                return None
        return resolver

    @staticmethod
    def _find_filesystem_service(user_id: str = "", conversation_id: str = ""):
        """Find the first live filesystem service for this user.

        Same logic as agent_utils._find_filesystem_service but standalone.
        """
        try:
            from core.service_registry import ServiceRegistry
            reg = ServiceRegistry.get_instance()
            available = ToolRelayService._list_available_filesystem_services(
                user_id, conversation_id)
            if available:
                for item in available:
                    svc = reg.resolve(
                        item.get("id", ""), user_id=user_id, conv_id=conversation_id)
                    if svc:
                        return svc
            if conversation_id:
                return None
            for fs_type in ("relay", "filesystem", "googleDrive", "oneDrive"):
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
        try:
            from core.handlers.meta_tools import _schema_with_local
            schema = _schema_with_local(handler)
        except Exception:
            schema = handler.parameters_schema
        return {"type": "result", "request_id": request_id, "data": {
            "name": handler.name,
            "description": handler.description,
            "parameters": schema,
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
        #
        # Race: the MCP bridge (in-container) forwards the execute_tool
        # request to us CONCURRENTLY with the claude-code provider's
        # stdout reader processing the same `assistant` event and
        # calling `enqueue_cc_tc`. The bridge often wins — pop fires
        # before enqueue lands. Without a wait, every fast-dispatching
        # tool (bash, grep, etc.) spuriously MISSes on a healthy loop.
        # Short retry with timeout lets the enqueue catch up while
        # still capping the blocking window so a genuine miss
        # (sentinel conv, crashed provider) doesn't stall.
        #
        # Sentinel conversations (_compact, _memory_extract, …) never
        # push cc_tc — they have no UI subscribers, tool_call SSE is a
        # no-op, and they can't be backgrounded or killed per-tool
        # anyway (the whole sentinel session is the unit of cancel).
        # Skip BOTH the retry and the MISS log for them.
        cc_tc_id = ""
        _is_sentinel = bool(conversation_id) and conversation_id.startswith("_")
        try:
            from core.background_tool import pop_cc_tc, _args_hash
            _ah = _args_hash(arguments)
            cc_tc_id = pop_cc_tc(
                conversation_id, agent_name, tool_name, _ah)
            if not cc_tc_id and not _is_sentinel:
                # Retry with bounded wait (up to 500ms, polling every
                # 50ms). The provider's enqueue runs on the CC stdout
                # reader thread and typically catches up within 10-
                # 100ms; 500ms is a generous upper bound.
                import time as _t
                _deadline = _t.monotonic() + 0.5
                while _t.monotonic() < _deadline:
                    _t.sleep(0.05)
                    cc_tc_id = pop_cc_tc(
                        conversation_id, agent_name, tool_name, _ah)
                    if cc_tc_id:
                        break
            if not cc_tc_id and not _is_sentinel:
                # This can be a healthy relay-first race. The provider stream
                # may late-bind the tc_id once its tool_call item arrives.
                from core.background_tool import snapshot_cc_pending
                _pending_now = snapshot_cc_pending(conversation_id, agent_name)
                logger.debug(
                    "[tool-relay] cc_tc pending provider id conv=%s agent=%s "
                    "tool=%s args_hash=%s pending=%s",
                    conversation_id[:8], agent_name, tool_name, _ah,
                    _pending_now or "[]")
            elif cc_tc_id:
                logger.debug(
                    "[tool-relay] cc_tc matched tc_id=%s (tool=%s)",
                    cc_tc_id, tool_name)
        except Exception as _me:
            logger.debug("[tool-relay] cc_tc match skipped: %s", _me)

        # Mark as executing — cancel_event can abort, background_event
        # detaches the call (returns placeholder, thread keeps running).
        # Use the provider-visible tool id when available; otherwise fall
        # back to request_id so MCP calls without a mapped tool_call can
        # still auto-background before the transport timeout.
        bg_tc_id = cc_tc_id or request_id
        evt = threading.Event()
        cancel_event = threading.Event()
        background_event = threading.Event()
        started_at = time.time()
        # Shared mutable list — the exec thread populates it via
        # register_kill_hook(); cancel_agent reads + invokes each hook.
        kill_hooks: list = []
        with self._cache_lock:
            self._executing[request_id] = evt
        with self._inflight_lock:
            self._inflight[request_id] = {
                "conv": conversation_id,
                "agent": agent_name,
                "cancel": cancel_event,
                "background": background_event,
                "cc_tc_id": cc_tc_id,
                "bg_tc_id": bg_tc_id,
                "tool_name": tool_name,
                "args_hash": _ah,
                "started_at": started_at,
                "kill_hooks": kill_hooks,
            }

        # Execute in a daemon thread so cancel/background can let it run on.
        _result_holder = [None]

        def _exec():
            # Expose the cancel event + kill-hook registry to the tool's
            # call stack via thread-local — long-running tools (Pixazo
            # poll loops, browser automation, anything with its own
            # retry/wait) can read the event and abort early instead of
            # hammering the remote API after the user clicked Kill.
            # Tools that spawn subprocesses MUST also call
            # register_kill_hook(proc.terminate) so FORCE STOP can
            # actually tear them down.
            _set_current_cancel_event(cancel_event)
            _set_current_kill_hooks(kill_hooks)
            try:
                _result_holder[0] = self._do_execute(
                    request_id, tool_name, arguments,
                    user_id, conversation_id, agent_name)
            except Exception as e:
                _result_holder[0] = {"type": "result", "request_id": request_id,
                                      "data": f"Error: {e}"}
            finally:
                _set_current_cancel_event(None)
                _set_current_kill_hooks(None)
                evt.set()

        exec_thread = threading.Thread(target=_exec, daemon=True)
        exec_thread.start()

        # Wait for completion, cancel, or explicit background. Optional auto-BG
        # is disabled by default: there is no implicit timeout/backgrounding.
        _auto_bg_after = max(0.0, float(getattr(self, "_auto_bg_after_seconds", 0.0) or 0.0))
        while not evt.is_set():
            _elapsed = time.time() - started_at
            # Auto-BG must not depend on a provider tool_call id: direct
            # MCP/tool-relay calls still need a placeholder before the
            # outer transport timeout.
            if (_auto_bg_after > 0 and not background_event.is_set()
                    and _elapsed >= _auto_bg_after):
                logger.info("[tool-relay] auto-background after %ds for tc_id=%s",
                            int(_auto_bg_after), bg_tc_id)
                background_event.set()

            if background_event.is_set():
                # Return placeholder now; spawn a watcher to inject the
                # real result (or kill notice) as a user message when
                # the daemon thread finishes.
                placeholder = (
                    f"[Running in background (tc_id={bg_tc_id})]\n"
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

                threading.Thread(
                    target=_watch_bg_completion,
                    args=(evt, _result_holder, bg_tc_id, conversation_id,
                          agent_name, tool_name, user_id, cancel_event),
                    daemon=True,
                    name=f"bg-watch-{bg_tc_id[:12]}",
                ).start()

                with self._cache_lock:
                    self._result_cache[request_id] = result
                    self._executing.pop(request_id, None)
                with self._inflight_lock:
                    self._inflight.pop(request_id, None)
                return result

            if _auto_bg_after > 0:
                _remaining = _auto_bg_after - (time.time() - started_at)
                _wait = min(0.5, max(0.01, _remaining)) if _remaining > 0 else 0.01
            else:
                _wait = 0.5
            if cancel_event.wait(timeout=_wait):
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

            # read_only mode takes precedence over EVERY per-tool
            # override — a stale `allow` permission left from a
            # previous mode must not let a write tool through once the
            # conversation has been switched to read_only. The
            # allowlist is fail-closed (anything not classified is
            # denied).
            if _perm_mode == "read_only":
                from core.tool_approval import ToolApprovalGate
                if not ToolApprovalGate.is_read_only_allowed(
                        tool_name,
                        arguments if isinstance(arguments, dict) else None):
                    return {"type": "result", "request_id": request_id,
                            "data": f"Error: tool '{tool_name}' is not allowed in read-only mode."}
                # Allowed by read_only — fall through, but skip the
                # per-tool override below (it would be redundant for
                # an allowlisted read tool).
                _tool_perm = ""

            # Per-tool override (only consulted outside read_only).
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
            logger.error("Tool approval check failed; denying tool for safety: %s", e,
                         exc_info=True)
            return {"type": "result", "request_id": request_id,
                    "data": "Error: tool approval check failed; denied for safety."}

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
            try:
                handler = registry.get(tool_name)
                from core.handlers.meta_tools import _normalize_tool_args
                if handler and isinstance(arguments, dict):
                    arguments = _normalize_tool_args(tool_name, arguments)
            except Exception:
                pass
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

        # Convert __image_data__: markers into MCP content blocks server-side,
        # gated on the handler's _returns_images flag. Without this gate, a
        # grep result matching the literal marker string would be wrongly
        # split into separate text/image blocks by the bridge.
        _h_for_img = next((h for h in registry.list_tools() if h.name == tool_name), None)
        _returns_images = bool(getattr(_h_for_img, '_returns_images', False)) if _h_for_img else False
        if _returns_images and "__image_data__:" in result_str:
            blocks = []
            for rline in result_str.split("\n"):
                if rline.startswith("__image_data__:"):
                    parts = rline.split(":", 2)
                    if len(parts) == 3:
                        blocks.append({"type": "image",
                                       "data": parts[2],
                                       "mimeType": parts[1]})
                elif rline.strip():
                    blocks.append({"type": "text", "text": rline})
            if blocks:
                return {"type": "result", "request_id": request_id,
                        "data": blocks}

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


# ── Thread-local cancel-event + kill-hooks ────────────────────────────
# Populated by `_handle_execute._exec()` for the lifetime of one tool
# dispatch so any code in the call stack (Pixazo poll loops, browser
# automation, long-running waits) can check `current_cancel_event()`
# and abort early when the user clicks Kill.
#
# Kill-hooks: callables a tool can register to be invoked from
# `cancel_agent`. Use this for resources that don't observe the cancel
# event mid-syscall — typically `subprocess.Popen` instances. Without a
# hook, the daemon thread that owned the tool's _exec() keeps running
# after FORCE STOP because Python threads can't be killed safely; the
# hook gives `cancel_agent` a way to terminate the underlying process
# so the thread's blocking `proc.wait()` returns.
_thread_local = threading.local()


def _set_current_cancel_event(evt):
    _thread_local.cancel_event = evt


def current_cancel_event():
    """Return the cancel Event for the currently-executing tool, or
    None when not running inside a tool dispatch (tests, direct calls).
    """
    return getattr(_thread_local, "cancel_event", None)


def _set_current_kill_hooks(hooks_list):
    _thread_local.kill_hooks = hooks_list


def register_kill_hook(callback) -> None:
    """Register a callable to be invoked when the current tool is killed.

    Tools that spawn external resources (subprocess.Popen, websockets,
    HTTP sessions) MUST register a hook so FORCE STOP can shut them
    down explicitly. The hook runs from `cancel_agent` and should be
    fast and idempotent (terminate, close, signal — not block).

    No-op when called outside a tool dispatch.
    """
    hooks = getattr(_thread_local, "kill_hooks", None)
    if hooks is None:
        return
    hooks.append(callback)
    cancel_evt = current_cancel_event()
    if cancel_evt is not None and cancel_evt.is_set():
        callback()

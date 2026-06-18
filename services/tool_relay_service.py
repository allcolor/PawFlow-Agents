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
from typing import Any, Dict

from core import ServiceFactory
from core.base_service import BaseService

logger = logging.getLogger(__name__)
from services._tool_relay_base import (  # noqa: F401,E402
    _RELAY_TRANSPORT_RETRY_ATTEMPTS, _RELAY_TRANSPORT_RETRY_DELAY_SECONDS, _RELAY_TRANSPORT_RETRY_EXHAUSTED_MARKER, _RELAY_TRANSPORT_ERROR_MARKERS, _RELAY_TRANSPORT_RESULT_PREFIXES, _contains_relay_transport_marker, _is_relay_transport_result, _is_relay_transport_error, _resolve_vars_in_args, _redact_secrets, resolve_secrets_env, resolve_secret_values, _thread_local, _set_current_cancel_event, current_cancel_event, _set_current_kill_hooks, register_kill_hook)
from services._tool_relay_cache_req import _ToolRelayCacheReqMixin  # noqa: E402
from services._tool_relay_registry import _ToolRelayRegistryMixin  # noqa: E402
from services._tool_relay_tools import _ToolRelayToolsMixin  # noqa: E402
from services._tool_relay_execute import _ToolRelayExecuteMixin  # noqa: E402


class ToolRelayService(
    _ToolRelayCacheReqMixin,
    _ToolRelayRegistryMixin,
    _ToolRelayToolsMixin,
    _ToolRelayExecuteMixin,
    BaseService,
):
    """Tool execution service for MCP bridge connections."""

    TYPE = "toolRelay"
    VERSION = "1.0.0"
    NAME = "Tool Relay"
    DESCRIPTION = "Exposes PawFlow tools to Claude Code via WebSocket relay"
    _registry_cache: Dict[tuple, Any] = {}
    _registry_cache_tool_counts: Dict[tuple, int] = {}
    _registry_building: Dict[tuple, threading.Event] = {}
    _registry_cache_lock = threading.RLock()
    _runtime_cache_lock = threading.RLock()
    _secret_env_cache: Dict[tuple, tuple[tuple, dict]] = {}
    _secret_values_cache: Dict[tuple, tuple[tuple, set, dict]] = {}
    _ENV_SECRET_TOOLS = frozenset({"bash", "execute_script"})
    _SECRET_MUTATION_TOOLS = frozenset({
        "store_secret", "manage_package", "manage_resource", "delete_tool",
        "create_tool",
    })












    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._service_id = self.config.get("_service_id", "")
        self._connection = None  # main HTTPListenerService ref (set by connect)
        try:
            self._auto_bg_after_seconds = float(
                self.config.get("auto_background_after_seconds", 0) or 0)
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
        self.clear_registry_cache()
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
        # One writer lock per tool-relay WS connection. Tool execution runs in
        # worker threads and schedules response writes back onto this loop;
        # without serialization, concurrent writer.write()/drain() calls can
        # interleave frames on the MCP bridge connection under tool bursts.
        send_lock = asyncio.Lock()

        async def _send_tool_frame(payload: bytes, opcode: int = 0x01,
                                   request_id: str = "", method: str = "",
                                   started_at: float = 0.0):
            wait_started = time.perf_counter()
            async with send_lock:
                lock_wait_ms = (time.perf_counter() - wait_started) * 1000
                send_started = time.perf_counter()
                await _ws_send_frame(writer, payload, opcode=opcode)
                send_ms = (time.perf_counter() - send_started) * 1000
            if request_id:
                total_ms = ((time.perf_counter() - started_at) * 1000
                            if started_at else 0.0)
                logger.debug(
                    "[tool-relay] timing ws_send request=%s method=%s "
                    "lock_wait_ms=%.1f write_ms=%.1f total_worker_to_wire_ms=%.1f bytes=%d",
                    request_id, method or "?", lock_wait_ms, send_ms,
                    total_ms, len(payload))

        try:
            opcode, payload = await _ws_recv_frame(reader)
            if opcode != 0x01:
                return
            reg = json.loads(payload.decode('utf-8'))
            if reg.get('type') != 'register':
                return
            relay_token = reg.get('token', '')
            if not relay_token or relay_token != service.config.get('token', ''):
                await _send_tool_frame(json.dumps(
                    {'type': 'error', 'message': 'Token mismatch'}).encode())
                return
            relay_id = reg.get('relay_id', '')
            _user_id = reg.get('user_id', '')
            _conv_id = reg.get('conversation_id', '')
            _agent_name = reg.get('agent_name', '')
            logger.info('Tool relay connected: user=%s conv=%s agent=%s addr=%s',
                         _user_id, _conv_id, _agent_name, remote)
            await _send_tool_frame(json.dumps({
                'type': 'registered', 'relay_id': relay_id}).encode())
            KEEPALIVE = 120
            while True:
                try:
                    opcode, payload = await asyncio.wait_for(
                        _ws_recv_frame(reader), timeout=KEEPALIVE)
                except asyncio.TimeoutError:
                    await _send_tool_frame(json.dumps({'type': 'ping'}).encode())
                    continue
                if opcode == 0x08:
                    break
                if opcode == 0x09:
                    await _send_tool_frame(payload, opcode=0x0A)
                    continue
                if opcode != 0x01:
                    continue
                msg = json.loads(payload.decode('utf-8'))
                if msg.get('type') == 'ping':
                    await _send_tool_frame(json.dumps({'type': 'pong'}).encode())
                    continue
                if msg.get('type') != 'request':
                    continue
                msg['_relay_received_perf'] = time.perf_counter()
                def _exec(m=msg, _ui=_user_id, _ci=_conv_id, _an=_agent_name):
                    worker_started = time.perf_counter()
                    try:
                        resp = service.handle_tool_request(m, _ui, _ci, _an)
                    except Exception as e:
                        resp = {'type': 'error',
                                'request_id': m.get('request_id', ''),
                                'error': str(e)}
                    handle_ms = (time.perf_counter() - worker_started) * 1000
                    req_id = m.get('request_id', '')
                    method = m.get('method', '?')
                    try:
                        resp_payload = json.dumps(resp).encode('utf-8')
                    except Exception:
                        resp_payload = json.dumps({
                            'type': 'error',
                            'request_id': req_id,
                            'error': 'failed to encode tool relay response',
                        }).encode('utf-8')
                    logger.debug(
                        "[tool-relay] timing handled request=%s method=%s "
                        "handle_ms=%.1f response_bytes=%d",
                        req_id, method, handle_ms, len(resp_payload))
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
                    _coro = _send_tool_frame(
                        resp_payload, request_id=req_id, method=method,
                        started_at=worker_started)
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























    # Cache for idempotent retries: request_id → result dict
    _result_cache = {}  # shared across instances (class-level)
    _executing = {}     # request_id → threading.Event (in-flight)
    _cache_lock = threading.Lock()





ServiceFactory.register(ToolRelayService)

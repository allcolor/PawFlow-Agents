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

import hashlib
import json
import logging
import threading
import time
from typing import Any, Dict, Optional

from core import ServiceFactory
from core.base_service import BaseService

logger = logging.getLogger(__name__)

_RELAY_TRANSPORT_RETRY_ATTEMPTS = 5
_RELAY_TRANSPORT_RETRY_DELAY_SECONDS = 5.0
_RELAY_TRANSPORT_RETRY_EXHAUSTED_MARKER = "Relay transport retry attempts exhausted"
_RELAY_TRANSPORT_ERROR_MARKERS = (
    "Relay disconnected",
    "Relay not connected",
    "Failed to send to relay",
)
_RELAY_TRANSPORT_RESULT_PREFIXES = (
    "Error reading",
    "Error writing",
    "Error editing",
    "Error copying",
    "Error deleting",
    "Error executing command",
    "Error: Relay disconnected",
    "Error: Relay not connected",
    "Error: Failed to send to relay",
)


def _contains_relay_transport_marker(text: str) -> bool:
    return any(marker in text for marker in _RELAY_TRANSPORT_ERROR_MARKERS)


def _is_relay_transport_result(result: Any) -> bool:
    if not isinstance(result, str):
        return False
    text = result.strip()
    if _RELAY_TRANSPORT_RETRY_EXHAUSTED_MARKER in text:
        return False
    if not _contains_relay_transport_marker(text):
        return False
    return any(text.startswith(prefix) for prefix in _RELAY_TRANSPORT_RESULT_PREFIXES)


def _is_relay_transport_error(exc: Exception) -> bool:
    seen = set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        text = str(current)
        if _RELAY_TRANSPORT_RETRY_EXHAUSTED_MARKER in text:
            return False
        if _contains_relay_transport_marker(text):
            return True
        current = current.__cause__ or current.__context__
    return False


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

    @staticmethod
    def _root_conversation_id(conversation_id: str) -> str:
        if conversation_id and '::task::' in conversation_id:
            return conversation_id.split('::task::')[0]
        return conversation_id or ""

    @staticmethod
    def _args_reference_env(arguments: Any) -> bool:
        if isinstance(arguments, str):
            return "$" in arguments
        if isinstance(arguments, dict):
            return any(not str(k).startswith("_")
                       and ToolRelayService._args_reference_env(v)
                       for k, v in arguments.items())
        if isinstance(arguments, list):
            return any(ToolRelayService._args_reference_env(v)
                       for v in arguments)
        return False

    @staticmethod
    def _conversation_extra_fast(conversation_id: str, key: str,
                                 default: Any = None) -> Any:
        if not conversation_id:
            return default
        from core.conversation_store import ConversationStore
        store = ConversationStore.instance()
        sentinel = object()
        try:
            value = store.get_extra_snapshot(conversation_id, key, sentinel)
            if value is not sentinel:
                return value
            # A warm conversation cache knows the key is absent. Avoid falling
            # back to extras.json on hot tool paths for normal missing keys
            # such as conversation_hooks/tool_permissions.
            try:
                with store._cache_lock:
                    if conversation_id in store._cache:
                        return default
            except Exception:
                logging.getLogger(__name__).debug(
                    "extra cache warm check failed", exc_info=True)
        except Exception:
            logging.getLogger(__name__).debug("extra snapshot failed", exc_info=True)
        return store.get_extra(conversation_id, key, default)

    @staticmethod
    def _stable_config_fingerprint(value: Any) -> tuple:
        try:
            payload = json.dumps(value or {}, sort_keys=True, default=str,
                                 separators=(",", ":"))
        except Exception:
            payload = str(value or {})
        digest = hashlib.sha1(
            payload.encode("utf-8", "ignore"), usedforsecurity=False,
        ).hexdigest()
        return (len(payload), digest)

    @classmethod
    def _conversation_has_hooks(cls, conversation_id: str, user_id: str) -> bool:
        raw = cls._conversation_extra_fast(
            conversation_id, "conversation_hooks", [],)
        if isinstance(raw, dict):
            raw = raw.get("hooks") if isinstance(raw.get("hooks"), list) else list(raw.values())
        return bool(raw)

    @classmethod
    def clear_runtime_caches(cls, conversation_id: str = "", user_id: str = ""):
        conv = cls._root_conversation_id(conversation_id)
        uid = user_id or ""
        with cls._runtime_cache_lock:
            if not conv and not uid:
                cls._secret_env_cache.clear()
                cls._secret_values_cache.clear()
                return
            keys = (set(cls._secret_env_cache.keys()) |
                    set(cls._secret_values_cache.keys()))
            for key in list(keys):
                _uid, _conv = key
                if uid and _uid != uid:
                    continue
                if conv and _conv != conv:
                    continue
                cls._secret_env_cache.pop(key, None)
                cls._secret_values_cache.pop(key, None)

    @staticmethod
    def _path_fingerprint(path) -> tuple:
        try:
            st = path.stat()
            return (str(path), st.st_size, st.st_mtime_ns)
        except OSError:
            return (str(path), -1, -1)

    @classmethod
    def _secret_config_fingerprint(cls, user_id: str, conversation_id: str) -> tuple:
        from core.paths import GLOBAL_PARAMS_FILE, GLOBAL_SECRETS_FILE, USER_CONFIG_DIR
        conv = cls._root_conversation_id(conversation_id)
        parts = [
            cls._path_fingerprint(GLOBAL_PARAMS_FILE),
            cls._path_fingerprint(GLOBAL_SECRETS_FILE),
        ]
        if user_id:
            user_dir = USER_CONFIG_DIR / user_id
            parts.extend((
                cls._path_fingerprint(user_dir / "params.json"),
                cls._path_fingerprint(user_dir / "secrets.json"),
            ))
        if conv:
            parts.extend((
                ("conv_params", cls._stable_config_fingerprint(
                    cls._conversation_extra_fast(conv, "conv_params", {}) or {})),
                ("conv_secrets", cls._stable_config_fingerprint(
                    cls._conversation_extra_fast(conv, "conv_secrets", {}) or {})),
            ))
        return tuple(parts)

    @classmethod
    def _cached_secrets_env(cls, user_id: str, conversation_id: str) -> dict:
        if not user_id:
            return {}
        conv = cls._root_conversation_id(conversation_id)
        key = (user_id or "", conv)
        with cls._runtime_cache_lock:
            cached = cls._secret_env_cache.get(key)
            if cached:
                return dict(cached[1])
        fingerprint = cls._secret_config_fingerprint(user_id, conv)
        env = resolve_secrets_env(user_id, conv)
        with cls._runtime_cache_lock:
            cls._secret_env_cache[key] = (fingerprint, dict(env))
        return env

    @classmethod
    def _cached_secret_values(cls, user_id: str, conversation_id: str) -> tuple:
        if not user_id:
            return set(), {}
        conv = cls._root_conversation_id(conversation_id)
        key = (user_id or "", conv)
        with cls._runtime_cache_lock:
            cached = cls._secret_values_cache.get(key)
            if cached:
                return set(cached[1]), dict(cached[2])
        fingerprint = cls._secret_config_fingerprint(user_id, conv)
        values, names = resolve_secret_values(user_id, conv)
        with cls._runtime_cache_lock:
            cls._secret_values_cache[key] = (fingerprint, set(values), dict(names))
        return values, names

    @classmethod
    def clear_registry_cache(cls, conversation_id: str = "",
                             user_id: str = "", agent_name: str = ""):
        """Invalidate cached per-agent tool registries."""
        cls.clear_runtime_caches(conversation_id=conversation_id, user_id=user_id)
        conv = conversation_id or ""
        uid = user_id or ""
        agent = agent_name or ""
        with cls._registry_cache_lock:
            if not any((conv, uid, agent)):
                cls._registry_cache.clear()
                cls._registry_cache_tool_counts.clear()
                for evt in cls._registry_building.values():
                    evt.set()
                cls._registry_building.clear()
                return
            keys = set(cls._registry_cache.keys()) | set(cls._registry_building.keys())
            for key in list(keys):
                _service_id, _uid, _conv, _agent, _file_base = key
                if conv and _conv != conv:
                    continue
                if uid and _uid != uid:
                    continue
                if agent and _agent != agent:
                    continue
                cls._registry_cache.pop(key, None)
                cls._registry_cache_tool_counts.pop(key, None)
                evt = cls._registry_building.pop(key, None)
                if evt:
                    evt.set()

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
            wake_evt = info.get("wake")
            if wake_evt:
                wake_evt.set()
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
                        wake_evt = info.get("wake")
                        if wake_evt:
                            wake_evt.set()
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
                        wake_evt = info.get("wake")
                        if wake_evt:
                            wake_evt.set()
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
                        wake_evt = info.get("wake")
                        if wake_evt:
                            wake_evt.set()
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
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
            wake_evt = info.get("wake")
            if wake_evt:
                wake_evt.set()
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
        relay_received_at = float(msg.get("_relay_received_perf") or 0.0)
        dispatch_started_at = time.perf_counter()

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
                relay_received_at=relay_received_at,
                dispatch_started_at=dispatch_started_at,
            )
        elif method == "execute_pfp_host_call":
            return self._handle_pfp_host_call(
                request_id,
                msg.get("invocation", {}),
                msg.get("host_call", {}),
                user_id,
                conversation_id,
                agent_name,
            )
        else:
            return {"type": "error", "request_id": request_id,
                    "error": f"Unknown method: {method}"}

    def _handle_pfp_host_call(self, request_id: str, invocation: Dict[str, Any],
                              host_call: Dict[str, Any], user_id: str,
                              conversation_id: str, agent_name: str) -> dict:
        from core import pfp_runtime
        try:
            from core.service_registry import ServiceRegistry
            self._validate_pfp_host_call_context(
                invocation, user_id, conversation_id, agent_name)
            registry = self._get_registry(user_id, conversation_id, agent_name)
            host = pfp_runtime.runtime_host_from_invocation(
                invocation,
                tool_registry=registry,
                service_registry=ServiceRegistry.get_instance(),
            )
            result = host.handle_host_call(host_call)
            payload = {
                "format": pfp_runtime.RUNTIME_RESULT_FORMAT,
                "ok": True,
                "result": result,
            }
        except Exception as exc:
            payload = {
                "format": pfp_runtime.RUNTIME_RESULT_FORMAT,
                "ok": False,
                "error": str(exc),
            }
        return {"type": "result", "request_id": request_id, "data": payload}

    @staticmethod
    def _validate_pfp_host_call_context(invocation: Dict[str, Any], user_id: str,
                                      conversation_id: str, agent_name: str) -> None:
        context = invocation.get("context") if isinstance(invocation, dict) else {}
        if not isinstance(context, dict):
            raise ValueError("invalid PFP invocation context")
        expected = {
            "user_id": user_id or "",
            "conversation_id": conversation_id or "",
            "agent_name": agent_name or "",
        }
        for key, value in expected.items():
            actual = str(context.get(key) or "")
            if actual != value:
                raise ValueError(f"PFP host-call context mismatch: {key}")

    @classmethod
    def _active_tool_result_max_chars(cls, user_id: str, conversation_id: str,
                                      agent_name: str) -> Optional[int]:
        if not (user_id and conversation_id and agent_name):
            return None
        conv_id = cls._root_conversation_id(conversation_id)
        from core.conv_agent_config import get_agent_config
        cfg = get_agent_config(conv_id, agent_name)
        llm_service = str(cfg.get("llm_service") or "").strip()
        if not llm_service:
            return None
        from core.service_registry import ServiceRegistry
        sdef = ServiceRegistry.get_instance().resolve_definition(
            llm_service, user_id=user_id, conv_id=conv_id)
        if not sdef:
            return None
        value = (getattr(sdef, "config", {}) or {}).get("tool_result_max_chars", 0)
        max_chars = int(value or 0)
        return max_chars if max_chars > 0 else None

    def _get_registry(self, user_id: str = "", conversation_id: str = "",
                       agent_name: str = ""):
        """Get a configured tool registry for this request context.

        CRITICAL: injects the live filesystem service instance (the one
        with the relay connection) into the handler. Without this, the
        handler creates a new disconnected instance.

        Also loads dynamic tools (per-conversation) and MCP server tools
        (per-agent) so they are available via the MCP bridge.
        """
        cache_key = (
            self._service_id, user_id or "", conversation_id or "",
            agent_name or "", self.config.get("file_base_url", "") or "")
        cache_started = time.perf_counter()
        build_owner = False
        with self._registry_cache_lock:
            cached = self._registry_cache.get(cache_key)
            if cached is not None:
                tool_count = self._registry_cache_tool_counts.get(cache_key, 0)
                logger.debug(
                    "[tool-relay] timing get_registry_cache user=%s conv=%s "
                    "agent=%s total_ms=%.1f tools=%d",
                    user_id, (conversation_id or "")[:8], agent_name,
                    (time.perf_counter() - cache_started) * 1000,
                    tool_count)
                return cached
            build_evt = self._registry_building.get(cache_key)
            if build_evt is None:
                build_evt = threading.Event()
                self._registry_building[cache_key] = build_evt
                build_owner = True

        if not build_owner:
            build_evt.wait()
            with self._registry_cache_lock:
                cached = self._registry_cache.get(cache_key)
                tool_count = self._registry_cache_tool_counts.get(cache_key, 0)
            if cached is not None:
                logger.debug(
                    "[tool-relay] timing get_registry_cache user=%s conv=%s "
                    "agent=%s total_ms=%.1f tools=%d waited_for_build=yes",
                    user_id, (conversation_id or "")[:8], agent_name,
                    (time.perf_counter() - cache_started) * 1000,
                    tool_count)
                return cached
            return self._get_registry(user_id, conversation_id, agent_name)

        registry_total_started = time.perf_counter()
        dynamic_ms = 0.0
        mcp_ms = 0.0
        filter_ms = 0.0
        fs_find_ms = 0.0
        context_ms = 0.0
        spawn_ms = 0.0
        media_ms = 0.0
        fs_available_ms = 0.0
        from core.tool_registry import create_default_registry
        default_started = time.perf_counter()
        registry = create_default_registry()
        default_ms = (time.perf_counter() - default_started) * 1000

        # Load dynamic tools (global + user + conv) for this user/conv.
        if user_id:
            dynamic_started = time.perf_counter()
            try:
                from core.tool_loader import load_tools_into_registry
                _parent_cid = conversation_id or ""
                for _sep in ("::task::", "::task_verify::", "::delegate::"):
                    if _sep in _parent_cid:
                        _parent_cid = _parent_cid.split(_sep, 1)[0]
                        break
                load_tools_into_registry(
                    registry, user_id, _parent_cid)
            except Exception as e:
                logger.warning("[tool-relay] Failed to load dynamic tools: %s", e)
            dynamic_ms = (time.perf_counter() - dynamic_started) * 1000

        # Load MCP server tools for the active agent
        if conversation_id and user_id:
            mcp_started = time.perf_counter()
            self._load_mcp_tools(registry, user_id, conversation_id, agent_name)
            mcp_ms = (time.perf_counter() - mcp_started) * 1000

        if conversation_id:
            filter_started = time.perf_counter()
            try:
                from core.tool_mcp_filters import get_filters, is_tool_enabled_from_filters
                _filters = get_filters(conversation_id)
                for _handler in list(registry.list_tools()):
                    if not is_tool_enabled_from_filters(
                            _filters, _handler.name, agent_name,
                            getattr(_handler, "_origin", "builtin"),
                            getattr(_handler, "_origin_scope", "")):
                        registry.unregister(_handler.name)
            except Exception as e:
                logger.debug("[tool-relay] tool availability filter failed: %s", e)
            filter_ms = (time.perf_counter() - filter_started) * 1000

        available_fs = None
        # Find the default linked filesystem service for this conversation.
        fs_find_started = time.perf_counter()
        if conversation_id:
            available_fs = self._list_available_filesystem_services(
                user_id, conversation_id, agent_name)
            fs_svc = self._filesystem_service_from_available(
                available_fs, user_id, conversation_id, agent_name)
        else:
            fs_svc = self._find_filesystem_service(
                user_id, conversation_id, agent_name)
        fs_resolver = self._make_filesystem_resolver(
            user_id, conversation_id, agent_name, default_service=fs_svc)
        fs_find_ms = (time.perf_counter() - fs_find_started) * 1000

        tool_result_max_chars = self._active_tool_result_max_chars(
            user_id, conversation_id, agent_name)

        # Configure ALL handlers that need user/filesystem context
        context_started = time.perf_counter()
        for h in registry.list_tools():
            if (tool_result_max_chars is not None and
                    hasattr(h, '_tool_result_max_chars')):
                h._tool_result_max_chars = tool_result_max_chars
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
                        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        context_ms = (time.perf_counter() - context_started) * 1000

        # Configure SpawnAgentsHandler (delegate) — needs a client_resolver
        # to look up per-agent LLM services. Without this, delegate fails
        # with "Agent executor not configured (missing client_resolver)".
        spawn_started = time.perf_counter()
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
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

            for h in registry.list_tools():
                if isinstance(h, SpawnAgentsHandler):
                    h.set_spawn_deps(None, _client_resolver,
                                      on_event=_sub_on_event, registry=registry)
        except Exception as _e:
            logger.warning("[tool-relay] SpawnAgents wiring failed: %s", _e)
        spawn_ms = (time.perf_counter() - spawn_started) * 1000

        # Configure media service resolvers (image/video/audio/capabilities)
        media_started = time.perf_counter()
        from core.handlers.media import EditImageHandler, ImageGenerationHandler, ImageModelInfoHandler
        from core.handlers.media import VideoGenerationHandler, AudioGenerationHandler
        file_base_url = self.config.get("file_base_url", "") or ""
        for h in registry.list_tools():
            if isinstance(h, (ImageGenerationHandler, EditImageHandler,
                              ImageModelInfoHandler)):
                if file_base_url and hasattr(h, 'set_base_url'):
                    h.set_base_url(file_base_url)
                image_methods = ("generate",)
                if isinstance(h, EditImageHandler):
                    image_methods = ("edit_image",)
                elif isinstance(h, ImageModelInfoHandler):
                    image_methods = ("get_model_info",)
                h.set_service_resolver(
                    self._make_media_resolver(
                        user_id, conversation_id, "image", image_methods))
            elif isinstance(h, VideoGenerationHandler):
                if file_base_url:
                    h.set_base_url(file_base_url)
                h.set_service_resolver(
                    self._make_media_resolver(user_id, conversation_id, "video"))
            elif isinstance(h, AudioGenerationHandler):
                if file_base_url:
                    h.set_base_url(file_base_url)
                h.set_service_resolver(
                    self._make_media_resolver(
                        user_id, conversation_id, "audio", ("generate",)))
            elif h.name in ("generate_3d",):
                if file_base_url and hasattr(h, 'set_base_url'):
                    h.set_base_url(file_base_url)
                h.set_service_resolver(
                    self._make_media_resolver(
                        user_id, conversation_id, "3d", ("generate_3d",)))
            elif h.name in ("upscale_image",):
                if file_base_url and hasattr(h, 'set_base_url'):
                    h.set_base_url(file_base_url)
                h.set_service_resolver(
                    self._make_media_resolver(
                        user_id, conversation_id, "upscale", ("upscale",)))
            elif h.name in ("try_on",):
                if file_base_url and hasattr(h, 'set_base_url'):
                    h.set_base_url(file_base_url)
                h.set_service_resolver(
                    self._make_media_resolver(
                        user_id, conversation_id, "tryon", ("try_on",)))
            elif h.name in ("lipsync",):
                if file_base_url and hasattr(h, 'set_base_url'):
                    h.set_base_url(file_base_url)
                h.set_service_resolver(
                    self._make_media_resolver(
                        user_id, conversation_id, "lipsync", ("lipsync",)))
            elif h.name in ("train_image_model",):
                if file_base_url and hasattr(h, 'set_base_url'):
                    h.set_base_url(file_base_url)
                h.set_service_resolver(
                    self._make_media_resolver(
                        user_id, conversation_id, "trainer", ("train",)))
            elif h.name in ("clone_voice", "speak", "delete_voice"):
                if file_base_url and hasattr(h, 'set_base_url'):
                    h.set_base_url(file_base_url)
                if hasattr(h, 'set_user_id'):
                    h.set_user_id(user_id)
                if hasattr(h, 'set_conversation_id'):
                    h.set_conversation_id(conversation_id)
                voice_methods = {
                    "clone_voice": ("clone_speak",),
                    "speak": ("speak",),
                    "delete_voice": ("delete_voice_id",),
                }[h.name]
                media_type = "tts" if h.name == "speak" else "voice"
                h.set_service_resolver(
                    self._make_media_resolver(
                        user_id, conversation_id, media_type, voice_methods))
            elif h.name in ("describe_image", "remix_image"):
                if file_base_url and hasattr(h, 'set_base_url'):
                    h.set_base_url(file_base_url)
                if hasattr(h, 'set_user_id'):
                    h.set_user_id(user_id)
                if hasattr(h, 'set_conversation_id'):
                    h.set_conversation_id(conversation_id)
                image_methods = {
                    "describe_image": ("describe_image",),
                    "remix_image": ("remix_image",),
                }[h.name]
                h.set_service_resolver(
                    self._make_media_resolver(
                        user_id, conversation_id, "image", image_methods))
            elif h.name in ("speech_to_video",):
                if file_base_url and hasattr(h, 'set_base_url'):
                    h.set_base_url(file_base_url)
                if hasattr(h, 'set_user_id'):
                    h.set_user_id(user_id)
                if hasattr(h, 'set_conversation_id'):
                    h.set_conversation_id(conversation_id)
                h.set_service_resolver(
                    self._make_media_resolver(
                        user_id, conversation_id, "speech_to_video",
                        ("speech_to_video",)))
            elif h.name in ("upscale_video", "remove_background"):
                if file_base_url and hasattr(h, 'set_base_url'):
                    h.set_base_url(file_base_url)
                if hasattr(h, 'set_user_id'):
                    h.set_user_id(user_id)
                if hasattr(h, 'set_conversation_id'):
                    h.set_conversation_id(conversation_id)
                upscale_methods = {
                    "upscale_video": ("upscale_video",),
                    "remove_background": ("remove_background",),
                }[h.name]
                h.set_service_resolver(
                    self._make_media_resolver(
                        user_id, conversation_id, "upscale", upscale_methods))
        media_ms = (time.perf_counter() - media_started) * 1000

        # Populate conversation-linked filesystems on all BaseFsHandler instances.
        from core.handlers._fs_base import BaseFsHandler, _FS_TYPES
        _fs_handlers = [h for h in registry.list_tools() if isinstance(h, BaseFsHandler)]
        if _fs_handlers:
            fs_available_started = time.perf_counter()
            try:
                available = available_fs
                if available is None:
                    available = self._list_available_filesystem_services(
                        user_id, conversation_id, agent_name, fs_types=_FS_TYPES)
                for h in _fs_handlers:
                    h.set_available_services(available)
                logger.debug("Filesystem services for user '%s': %s",
                             user_id, [s["id"] for s in available])
            except Exception as e:
                logger.error("Failed to enumerate filesystem services: %s", e)
            fs_available_ms = (time.perf_counter() - fs_available_started) * 1000

        tool_count = len(registry.list_tools())
        total_ms = (time.perf_counter() - registry_total_started) * 1000
        if total_ms >= 100.0:
            logger.debug(
                "[tool-relay] timing get_registry user=%s conv=%s agent=%s "
                "total_ms=%.1f default_ms=%.1f dynamic_ms=%.1f "
                "mcp_ms=%.1f filter_ms=%.1f fs_find_ms=%.1f "
                "context_ms=%.1f spawn_ms=%.1f media_ms=%.1f "
                "fs_available_ms=%.1f tools=%d",
                user_id, (conversation_id or "")[:8], agent_name,
                total_ms, default_ms, dynamic_ms, mcp_ms, filter_ms,
                fs_find_ms, context_ms, spawn_ms, media_ms,
                fs_available_ms, tool_count)

        with self._registry_cache_lock:
            self._registry_cache[cache_key] = registry
            self._registry_cache_tool_counts[cache_key] = tool_count
            evt = self._registry_building.pop(cache_key, None)
            if evt:
                evt.set()
        return registry

    @staticmethod
    def _make_media_resolver(user_id: str, conversation_id: str, media_type: str,
                             required_methods=()):
        """Build a resolver closure for image/video/audio services."""
        required_methods = tuple(required_methods or ())
        def resolver(required_methods_override=()):
            type_map = {
                "image": ("base_image_generation", "BaseImageGenerationService"),
                "video": ("base_video_generation", "BaseVideoGenerationService"),
                "speech_to_video": ("base_video_generation", "BaseVideoGenerationService"),
                "audio": ("base_audio_generation", "BaseAudioGenerationService"),
                "tts": ("base_tts", "BaseTTSService"),
                "3d": ("base_capabilities", "BaseImage3DService"),
                "upscale": ("base_capabilities", "BaseImageUpscaleService"),
                "tryon": ("base_capabilities", "BaseTryOnService"),
                "lipsync": ("base_capabilities", "BaseLipsyncService"),
                "trainer": ("base_capabilities", "BaseImageTrainerService"),
                "voice": ("base_voice_clone", "BaseVoiceCloneService"),
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
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
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
                matching.extend(_sreg.resolve_by_type(
                    vtype, user_id=user_id, conv_id=conversation_id))
            pfp_capabilities = {
                "image": {"media.image_generation"},
                "video": {"media.video_generation"},
                "speech_to_video": {"media.video_generation", "media.lipsync"},
                "audio": {"media.audio_generation"},
                "tts": {"media.tts", "media.audio_generation", "media.voice_clone"},
                "3d": {"media.3d_generation"},
                "upscale": {
                    "media.image_upscale", "media.video_upscale",
                    "media.background_removal",
                },
                "tryon": {"media.try_on"},
                "lipsync": {"media.lipsync"},
                "trainer": {"media.image_training"},
                "voice": {"media.voice_clone"},
            }.get(media_type, set())
            if pfp_capabilities:
                for sdef in _sreg.resolve_by_type(
                        "packageRuntime", user_id=user_id,
                        conv_id=conversation_id):
                    runtime = (sdef.config or {}).get("package_runtime") or {}
                    provides = set(runtime.get("provides") or [])
                    if provides.intersection(pfp_capabilities):
                        matching.append(sdef)
            matching = [
                sdef for _idx, sdef in sorted(
                    enumerate(matching),
                    key=lambda item: (ToolRelayService._service_scope_rank(item[1]), item[0]),
                )
            ]

            if not matching:
                return None, f"No {media_type} generation service deployed"
            method_map = {
                "image": ("generate",),
                "video": (
                    "generate", "frame_to_video", "image_to_video",
                    "reference_to_video", "video_edit"),
                "speech_to_video": ("speech_to_video",),
                "audio": ("generate",),
                "tts": ("speak",),
                "3d": ("generate_3d",),
                "upscale": (
                    "upscale", "upscale_video", "remove_background"),
                "tryon": ("try_on",),
                "lipsync": ("lipsync",),
                "trainer": ("train",),
                "voice": ("clone_speak",),
            }
            required = tuple(
                required_methods_override or required_methods
                or method_map.get(media_type, ()))
            def _service_supports_required_methods(svc):
                if not svc:
                    return False
                native_proxy_methods = {"get_model_info"}
                if any(method in native_proxy_methods
                       and callable(getattr(svc, method, None))
                       for method in required):
                    return True
                operation_getter = getattr(svc, "get_operations", None)
                if callable(operation_getter):
                    operations = operation_getter() or {}
                    if isinstance(operations, dict):
                        operation_names = set(operations)
                        if not operation_names:
                            return False
                    elif isinstance(operations, (list, tuple, set)):
                        operation_names = {
                            str(name) for name in operations if str(name or "")}
                        if not operation_names:
                            return False
                    else:
                        operation_names = set()
                    if operation_names and not any(method in operation_names for method in required):
                        return False
                return any(hasattr(svc, method) for method in required)

            first_sid = matching[0].service_id
            for sdef in matching:
                svc = ToolRelayService._resolve_service_definition(
                    _sreg, sdef, user_id=user_id,
                    conversation_id=conversation_id)
                if _service_supports_required_methods(svc):
                    return svc, None
            return None, f"{media_type.title()} service '{first_sid}' failed to connect"
        return resolver

    @staticmethod
    def _resolve_service_definition(registry, service_def, *, user_id: str,
                                    conversation_id: str):
        service_id = str(getattr(service_def, "service_id", "") or "")
        if not service_id:
            return None
        scoped_getter = getattr(registry, "get_live_instance", None)
        if callable(scoped_getter):
            scope = str(getattr(service_def, "scope", "") or "")
            scope_id = str(getattr(service_def, "scope_id", "") or "")
            if scope and scope_id:
                return scoped_getter(scope, scope_id, service_id)
        return registry.resolve(
            service_id, user_id=user_id, conv_id=conversation_id)

    @staticmethod
    def _service_scope_rank(service_def) -> int:
        scope = str(getattr(service_def, "scope", "") or "").lower()
        if scope in {"conv", "conversation"}:
            return 0
        if scope == "user":
            return 1
        return 2

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
                                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                        if not relay_svc:
                            relay_svc = self._find_filesystem_service(
                                user_id, conversation_id, agent_name)
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
                                            agent_name: str = "",
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
                    for sid in get_linked(conversation_id, agent_name):
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
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                try:
                    from core.remote_fs_bindings import list_tool_filesystems
                    for item in list_tool_filesystems(user_id, conversation_id):
                        sid = item.get("id", "")
                        if not sid or sid in seen:
                            continue
                        seen.add(sid)
                        available.append(item)
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
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
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        return available

    @staticmethod
    def _filesystem_service_from_available(available, user_id: str = "",
                                           conversation_id: str = "",
                                           agent_name: str = ""):
        try:
            from core.service_registry import ServiceRegistry
            reg = ServiceRegistry.get_instance()
            if conversation_id and available:
                try:
                    from core.relay_bindings import get_default
                    default_id = get_default(conversation_id, agent_name) or ""
                except Exception:
                    default_id = ""
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                if default_id and any(item.get("id") == default_id for item in available):
                    svc = reg.resolve(default_id, user_id=user_id,
                                      conv_id=conversation_id)
                    if svc:
                        return svc
            for item in available or []:
                svc = reg.resolve(
                    item.get("id", ""), user_id=user_id,
                    conv_id=conversation_id)
                if svc:
                    return svc
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        return None

    @staticmethod
    def _make_filesystem_resolver(user_id: str = "", conversation_id: str = "",
                                  agent_name: str = "", default_service=None):
        def resolver(service_id: str = "", *_args):
            try:
                from core.service_registry import ServiceRegistry
                reg = ServiceRegistry.get_instance()
                available = ToolRelayService._list_available_filesystem_services(
                    user_id, conversation_id, agent_name)
                allowed = [item.get("id", "") for item in available if item.get("id")]
                default_id = ""
                if conversation_id:
                    try:
                        from core.relay_bindings import get_default
                        default_id = get_default(conversation_id, agent_name) or ""
                    except Exception:
                        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                if service_id in ("", "workspace", "ws", "local") and default_service:
                    return default_service
                if conversation_id:
                    if service_id in ("", "workspace", "ws", "local"):
                        service_id = default_id or (allowed[0] if allowed else "")
                    if not service_id or service_id not in allowed:
                        return None
                return reg.resolve(service_id, user_id=user_id, conv_id=conversation_id)
            except Exception:
                return None
        return resolver

    @staticmethod
    def _find_filesystem_service(user_id: str = "", conversation_id: str = "",
                                 agent_name: str = ""):
        """Find the first live filesystem service for this user.

        Same logic as agent_utils._find_filesystem_service but standalone.
        """
        try:
            from core.service_registry import ServiceRegistry
            reg = ServiceRegistry.get_instance()
            available = ToolRelayService._list_available_filesystem_services(
                user_id, conversation_id, agent_name)
            if conversation_id and available:
                try:
                    from core.relay_bindings import get_default
                    default_id = get_default(conversation_id, agent_name) or ""
                except Exception:
                    default_id = ""
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                if default_id and any(item.get("id") == default_id for item in available):
                    svc = reg.resolve(default_id, user_id=user_id,
                                      conv_id=conversation_id)
                    if svc:
                        return svc
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
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
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
                        conversation_id: str, agent_name: str,
                        relay_received_at: float = 0.0,
                        dispatch_started_at: float = 0.0) -> dict:
        handle_started = dispatch_started_at or time.perf_counter()
        relay_received_at = relay_received_at or handle_started
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
            evt.wait()
            with self._cache_lock:
                if request_id in self._result_cache:
                    return self._result_cache[request_id]
            return {"type": "result", "request_id": request_id,
                    "data": "Error: in-flight request completed without a cached result"}

        # Match CC tool_use id (enqueued by claude_code provider when it
        # emitted the tool_call SSE event). Matching lets background /
        # kill actions, keyed by UI-visible tc_id, reach this request.
        #
        # Race: the MCP bridge can forward execute_tool before the provider
        # stream exposes the UI-visible tool_call id. In that case the request
        # starts with request_id as its background id; enqueue_cc_tc can still
        # late-bind the provider id through bind_pending_cc_tc.
        #
        # Sentinel conversations (_compact, _memory_extract, …) never
        # push cc_tc — they have no UI subscribers, tool_call SSE is a
        # no-op, and they can't be backgrounded or killed per-tool
        # anyway (the whole sentinel session is the unit of cancel).
        # Skip the MISS log for them.
        cc_tc_id = ""
        _is_sentinel = bool(conversation_id) and conversation_id.startswith("_")
        try:
            from core.background_tool import pop_cc_tc, _args_hash
            _ah = _args_hash(arguments)
            cc_tc_id = pop_cc_tc(
                conversation_id, agent_name, tool_name, _ah)
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
        # still use explicit auto-background.
        bg_tc_id = cc_tc_id or request_id
        evt = threading.Event()
        cancel_event = threading.Event()
        background_event = threading.Event()
        wake_event = threading.Event()
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
                "wake": wake_event,
                "cc_tc_id": cc_tc_id,
                "bg_tc_id": bg_tc_id,
                "tool_name": tool_name,
                "args_hash": _ah,
                "started_at": started_at,
                "kill_hooks": kill_hooks,
            }

        if not cc_tc_id and not _is_sentinel:
            try:
                from core.background_tool import pop_cc_tc
                cc_tc_id = pop_cc_tc(
                    conversation_id, agent_name, tool_name, _ah)
            except Exception as _me:
                logger.debug("[tool-relay] late cc_tc pop skipped: %s", _me)
            if cc_tc_id:
                bg_tc_id = cc_tc_id
                with self._inflight_lock:
                    info = self._inflight.get(request_id)
                    if info:
                        info["cc_tc_id"] = cc_tc_id
                        info["bg_tc_id"] = cc_tc_id

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
                for attempt in range(1, _RELAY_TRANSPORT_RETRY_ATTEMPTS + 1):
                    try:
                        _result_holder[0] = self._do_execute(
                            request_id, tool_name, arguments,
                            user_id, conversation_id, agent_name)
                        break
                    except Exception as e:
                        if (not _is_relay_transport_error(e)
                                or attempt >= _RELAY_TRANSPORT_RETRY_ATTEMPTS):
                            _result_holder[0] = {
                                "type": "result", "request_id": request_id,
                                "data": f"Error: {e}"}
                            break
                        logger.warning(
                            "[tool-relay] relay transport error during %s "
                            "request=%s; retrying in %.1fs (attempt %d/%d): %s",
                            tool_name, request_id,
                            _RELAY_TRANSPORT_RETRY_DELAY_SECONDS,
                            attempt, _RELAY_TRANSPORT_RETRY_ATTEMPTS, e)
                        time.sleep(_RELAY_TRANSPORT_RETRY_DELAY_SECONDS)
            except Exception as e:
                _result_holder[0] = {"type": "result", "request_id": request_id,
                                      "data": f"Error: {e}"}
            finally:
                _set_current_cancel_event(None)
                _set_current_kill_hooks(None)
                evt.set()
                wake_event.set()

        exec_thread = threading.Thread(target=_exec, daemon=True)
        exec_thread.start()

        # Wait for completion, cancel, or explicit background. Optional auto-BG
        # is disabled by default: there is no implicit timeout/backgrounding.
        _auto_bg_after = max(0.0, float(getattr(self, "_auto_bg_after_seconds", 0.0) or 0.0))
        auto_bg_timer = None
        if _auto_bg_after > 0:
            def _auto_background():
                if evt.is_set() or cancel_event.is_set():
                    return
                logger.info("[tool-relay] auto-background after %ds for tc_id=%s",
                            int(_auto_bg_after), bg_tc_id)
                background_event.set()
                wake_event.set()

            auto_bg_timer = threading.Timer(_auto_bg_after, _auto_background)
            auto_bg_timer.daemon = True
            auto_bg_timer.start()

        while not evt.is_set():

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
                    _bg_wake = threading.Event()

                    def _relay_event(_src):
                        _src.wait()
                        _bg_wake.set()

                    threading.Thread(
                        target=_relay_event, args=(_evt,), daemon=True).start()
                    threading.Thread(
                        target=_relay_event, args=(_cancel,), daemon=True).start()
                    _bg_wake.wait()
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
                if auto_bg_timer:
                    auto_bg_timer.cancel()
                logger.debug(
                    "[tool-relay] timing execute_background request=%s tool=%s "
                    "relay_queue_ms=%.1f total_ms=%.1f cc_tc=%s",
                    request_id, tool_name,
                    (handle_started - relay_received_at) * 1000,
                    (time.perf_counter() - handle_started) * 1000,
                    cc_tc_id or "")
                return result

            if cancel_event.is_set():
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
                if auto_bg_timer:
                    auto_bg_timer.cancel()
                logger.debug(
                    "[tool-relay] timing execute_cancelled request=%s tool=%s "
                    "relay_queue_ms=%.1f total_ms=%.1f cc_tc=%s",
                    request_id, tool_name,
                    (handle_started - relay_received_at) * 1000,
                    (time.perf_counter() - handle_started) * 1000,
                    cc_tc_id or "")
                return result

            wake_event.wait()
            wake_event.clear()

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
            if auto_bg_timer:
                auto_bg_timer.cancel()
            # Cleanup old cache entries (keep last 100)
            with self._cache_lock:
                if len(self._result_cache) > 100:
                    oldest = list(self._result_cache.keys())[:50]
                    for k in oldest:
                        self._result_cache.pop(k, None)

        data = result.get("data") if isinstance(result, dict) else result
        try:
            result_len = len(data) if isinstance(data, str) else len(json.dumps(data, default=str))
        except Exception:
            result_len = len(str(data))
        logger.debug(
            "[tool-relay] timing execute_done request=%s tool=%s "
            "relay_queue_ms=%.1f total_ms=%.1f result_len=%d cc_tc=%s",
            request_id, tool_name,
            (handle_started - relay_received_at) * 1000,
            (time.perf_counter() - handle_started) * 1000,
            result_len, cc_tc_id or "")
        return result

    def _do_execute(self, request_id, tool_name, arguments,
                    user_id, conversation_id, agent_name):
        total_started = time.perf_counter()
        registry_started = time.perf_counter()
        registry = self._get_registry(user_id, conversation_id, agent_name)
        registry_ms = (time.perf_counter() - registry_started) * 1000
        pre_hook_ms = 0.0
        approval_ms = 0.0
        secrets_ms = 0.0
        tool_exec_ms = 0.0
        post_hook_ms = 0.0
        _hook_runner = None
        _hook_enabled = False
        _perm_cid = self._root_conversation_id(conversation_id)

        try:
            hook_started = time.perf_counter()
            try:
                _hook_enabled = self._conversation_has_hooks(_perm_cid, user_id)
            except Exception as _detect_error:
                logger.warning(
                    "pre_tool_call hook detection failed; approval gate will decide: %s",
                    _detect_error)
                _hook_enabled = False
            if _hook_enabled:
                from core.agent_hooks import AgentHookRunner
                _hook_runner = AgentHookRunner(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    agent_name=agent_name,
                )
                _pre = _hook_runner.run("pre_tool_call", {
                    "tool_call_id": request_id,
                    "tool_name": tool_name,
                    "arguments": arguments if isinstance(arguments, dict) else {},
                }, fail_policy="closed")
                if _pre.get("decision") == "block":
                    reason = _pre.get("reason") or "blocked by hook"
                    return {"type": "result", "request_id": request_id,
                            "data": f"Blocked by hook: {reason}"}
                if _pre.get("decision") == "replace":
                    _payload = _pre.get("payload") or {}
                    tool_name = str(_payload.get("tool_name") or tool_name)
                    _new_args = _payload.get("arguments")
                    arguments = _new_args if isinstance(_new_args, dict) else {}
            pre_hook_ms = (time.perf_counter() - hook_started) * 1000
        except Exception as _he:
            logger.error("pre_tool_call hook failed; denying relay tool: %s", _he,
                         exc_info=True)
            return {"type": "result", "request_id": request_id,
                    "data": f"Error: pre_tool_call hook failed: {_he}"}

        # Tool Approval Gate — reads permission_mode from conversation
        # For task sub-conversations (conv::task::tid), inherit parent's permissions
        try:
            approval_started = time.perf_counter()
            _perm_mode = "default"
            _tool_perm = ""
            if _perm_cid:
                _perm_mode = self._conversation_extra_fast(
                    _perm_cid, "permission_mode", "default") or "default"
                _tperms = self._conversation_extra_fast(
                    _perm_cid, "tool_permissions", {}) or {}
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
            approval_ms = (time.perf_counter() - approval_started) * 1000
        except Exception as e:
            logger.error("Tool approval check failed; denying tool for safety: %s", e,
                         exc_info=True)
            return {"type": "result", "request_id": request_id,
                    "data": "Error: tool approval check failed; denied for safety."}

        # Resolve env vars (all variables + secrets) and secret values (for redaction)
        _secret_values = set()
        _secret_names = {}
        _all_env = {}
        _secret_cid = _perm_cid
        if user_id and isinstance(arguments, dict):
            try:
                secrets_started = time.perf_counter()
                _needs_env = (tool_name in self._ENV_SECRET_TOOLS
                              or self._args_reference_env(arguments))
                if _needs_env:
                    _all_env = self._cached_secrets_env(user_id, _secret_cid)
                if _needs_env and _all_env:
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
                _secret_values, _secret_names = self._cached_secret_values(
                    user_id, _secret_cid)
                secrets_ms = (time.perf_counter() - secrets_started) * 1000
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
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
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
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            tool_exec_started = time.perf_counter()
            result = registry.execute(tool_name, arguments)
            tool_exec_ms = (time.perf_counter() - tool_exec_started) * 1000
            if tool_name in {
                    "create_tool", "delete_tool", "manage_resource",
                    "link_resource", "manage_package"}:
                self.clear_registry_cache(
                    conversation_id=conversation_id, user_id=user_id,
                    agent_name=agent_name)
            result_str = str(result) if result is not None else "(no output)"
            if _is_relay_transport_result(result_str):
                raise RuntimeError(result_str)
            if tool_name in self._SECRET_MUTATION_TOOLS:
                self.clear_runtime_caches(
                    conversation_id=conversation_id, user_id=user_id)
        except Exception as e:
            tool_exec_ms = (time.perf_counter() - tool_exec_started) * 1000 if 'tool_exec_started' in locals() else 0.0
            if _is_relay_transport_error(e):
                logger.warning("Tool relay transport failure in '%s': %s", tool_name, e)
                raise
            result_str = f"Error: {e}"
            logger.error("Tool relay execute '%s' failed: %s", tool_name, e)

        # Sanitize tool result to strip invisible/malicious unicode
        from core.sanitization import sanitize_unicode
        result_str = sanitize_unicode(result_str)

        # Redact secret values from tool output
        if _secret_values:
            result_str = _redact_secrets(result_str, _secret_values,
                                         secret_names=_secret_names)

        try:
            post_hook_started = time.perf_counter()
            if _hook_enabled and _hook_runner is not None:
                _post = _hook_runner.run("post_tool_call", {
                    "tool_call_id": request_id,
                    "tool_name": tool_name,
                    "arguments": arguments if isinstance(arguments, dict) else {},
                    "result": result_str,
                })
                if _post.get("decision") == "replace":
                    _payload = _post.get("payload") or {}
                    if "result" in _payload:
                        result_str = str(_payload.get("result") or "")
                elif _post.get("decision") == "block":
                    reason = _post.get("reason") or "blocked by hook"
                    result_str = f"Blocked by hook: {reason}"
            post_hook_ms = (time.perf_counter() - post_hook_started) * 1000
        except Exception as _he:
            logger.warning("post_tool_call hook failed: %s", _he, exc_info=True)

        logger.debug(
            "[tool-relay] timing do_execute request=%s tool=%s "
            "total_ms=%.1f registry_ms=%.1f pre_hook_ms=%.1f "
            "approval_ms=%.1f secrets_ms=%.1f exec_ms=%.1f "
            "post_hook_ms=%.1f result_len=%d",
            request_id, tool_name,
            (time.perf_counter() - total_started) * 1000,
            registry_ms, pre_hook_ms, approval_ms, secrets_ms,
            tool_exec_ms, post_hook_ms, len(result_str))

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
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

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
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

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

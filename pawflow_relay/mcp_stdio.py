"""Local stdio MCP bridge for a published PawFlow conversation.

The bridge proxies standard MCP JSON-RPC to PawFlow's Streamable HTTP endpoint
and owns the local filesystem relay subprocess.  Relay lifecycle tools are
implemented locally because the remote server must never choose a host path.
"""

from __future__ import annotations

import argparse
import atexit
import http.client
import json
import os
from pathlib import Path
import secrets
import subprocess  # nosec B404 - fixed interpreter/module invocation
import sys
import threading
import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse


_LOCAL_TOOLS = [
    {
        "name": "pawflow_relay_connect",
        "description": "Connect the configured local project directory to this PawFlow MCP conversation.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "pawflow_relay_disconnect",
        "description": "Disconnect this CLI's local project relay without disabling the MCP server.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "pawflow_relay_status",
        "description": "Show the local and PawFlow-side status of this CLI project relay. Never returns secrets.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "pawflow_relay_reconnect",
        "description": "Rotate the relay lease and reconnect the configured local project directory.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _log(message: str) -> None:
    sys.stderr.write(f"[pawflow-mcp] {message}\n")
    sys.stderr.flush()


# While PawFlow restarts, its proxy answers 502/503 or nothing listens (0 here):
# such a request was not processed and is sent again for up to this long.
_UNAVAILABLE_STATUSES = frozenset({0, 502, 503})
_UNAVAILABLE_RETRY_SECONDS = 120.0


def _session_lost(payload: Any) -> bool:
    """True for the server's 404 body for an MCP session it does not know."""
    return isinstance(payload, dict) and payload.get("error") == "MCP session not found"


def _use_utf8_stdio() -> None:
    """Force UTF-8 on the stdio JSON-RPC channel, as MCP stdio requires.

    On Windows a piped stdin/stdout defaults to the ANSI code page (cp1252).
    Writing any reply holding a character outside it (an arrow, an em dash,
    an emoji) then raises UnicodeEncodeError inside the call thread: the
    reply is never written and the MCP client waits on it forever. Reading
    is just as wrong: a UTF-8 prompt from the client decodes as mojibake.
    """
    for stream, options in ((sys.stdin, {}), (sys.stdout, {"newline": "\n"}),
                            (sys.stderr, {"errors": "backslashreplace"})):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", **options)


class HTTPBridge:
    def __init__(self, endpoint: str, api_key: str, gateway_key: str = "") -> None:
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.gateway_key = gateway_key
        self.session_id = ""
        # The client's initialize message, replayed when the session is lost.
        self._initialize: Optional[Dict[str, Any]] = None
        self._session_lock = threading.RLock()

    def _request(self, method: str, url: str, payload: Any = None,
                 include_session: bool = False) -> Tuple[int, Dict[str, str], Any]:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("PawFlow MCP URL must be an absolute HTTP(S) URL")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if parsed.scheme == "https":
            import ssl
            context = ssl.create_default_context()
            if os.environ.get("PAWFLOW_RELAY_INSECURE") == "1":
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
            connection = http.client.HTTPSConnection(
                parsed.hostname, port, context=context, timeout=60)
        else:
            connection = http.client.HTTPConnection(parsed.hostname, port, timeout=60)
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if self.gateway_key:
            headers["X-PawFlow-Gateway-Key"] = self.gateway_key
        with self._session_lock:
            if include_session and self.session_id:
                headers["Mcp-Session-Id"] = self.session_id
        connection.request(method, parsed.path or "/", body=body, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        response_headers = {key.lower(): value for key, value in response.getheaders()}
        connection.close()
        decoded = None
        if raw:
            try:
                decoded = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                decoded = {"error": raw.decode("utf-8", errors="replace")[:1000]}
        if response_headers.get("mcp-session-id"):
            with self._session_lock:
                self.session_id = response_headers["mcp-session-id"]
        return response.status, response_headers, decoded

    def rpc(self, message: Dict[str, Any]) -> Tuple[int, Any]:
        """Forward one JSON-RPC message, surviving a PawFlow server restart.

        The server keeps MCP sessions in memory, so after a restart it answers
        the old Mcp-Session-Id with 404. A stdio client never sees that
        session and cannot open a new one: every call failed until the CLI
        was relaunched. The bridge replays the client's own initialize, then
        sends the message once more. While the server is down, a refused
        connection or a proxy's 502/503 is retried for a while. Each retry
        is for a request the server did not process, so none runs twice.
        """
        method = str(message.get("method") or "")
        if method == "initialize":
            self._initialize = dict(message)
        session = self.session_id
        status, payload = self._post_rpc(message, method)
        if (status == 404 and method != "initialize"
                and _session_lost(payload) and self._reinitialize(session)):
            status, payload = self._post_rpc(message, method)
        return status, payload

    def _post_rpc(self, message: Dict[str, Any], method: str) -> Tuple[int, Any]:
        deadline = time.monotonic() + _UNAVAILABLE_RETRY_SECONDS
        delay = 1.0
        while True:
            try:
                status, _headers, payload = self._request(
                    "POST", self.endpoint, message,
                    include_session=method != "initialize",
                )
            except ConnectionRefusedError:
                if time.monotonic() + delay > deadline:
                    raise
                status, payload = 0, None
            if status not in _UNAVAILABLE_STATUSES:
                return status, payload
            if time.monotonic() + delay > deadline:
                return status, payload
            _log(f"PawFlow unavailable ({status or 'connection refused'}); "
                 f"retrying {method} in {delay:.0f}s")
            time.sleep(delay)
            delay = min(delay * 2, 15.0)

    def _reinitialize(self, failed_session: str) -> bool:
        """Open a new MCP session with the client's initialize parameters."""
        with self._session_lock:
            if self.session_id and self.session_id != failed_session:
                return True  # another call already opened one
            initialize = self._initialize
            if initialize is None:
                return False
            _log("MCP session lost (PawFlow restarted?); re-initializing")
            message = dict(initialize, id=f"pawflow-bridge-reinit-{secrets.token_hex(4)}")
            status, _payload = self._post_rpc(message, "initialize")
            if status >= 400 or not status:
                return False
            self._post_rpc({"jsonrpc": "2.0", "method": "notifications/initialized"},
                           "notifications/initialized")
            return True

    def control(self, action: str, payload: Optional[Dict[str, Any]] = None) -> Any:
        status, _headers, result = self._request(
            "POST", f"{self.endpoint}/relay/{action}", payload or {},
            include_session=False,
        )
        if status >= 400:
            message = result.get("message") if isinstance(result, dict) else ""
            error = result.get("error") if isinstance(result, dict) else ""
            raise RuntimeError(message or error or f"PawFlow relay API returned HTTP {status}")
        return result or {}


class RelayController:
    def __init__(self, bridge: HTTPBridge, root: Path, client_name: str,
                 readonly: bool = False, allow_exec: bool = False,
                 allow_service_tunnels: bool = False,
                 terminal_session_id: str = "", terminal_kind: str = "",
                 terminal_target: str = "",
                 terminal_secret: Optional[str] = None,
                 terminal_state_path: str = "",
                 terminal_file: str = "") -> None:
        self.bridge = bridge
        self.root = root.resolve()
        if not self.root.is_dir():
            raise ValueError(f"Relay directory does not exist: {self.root}")
        self.client_name = client_name or "CLI"
        self.client_id = "cli_" + secrets.token_urlsafe(18)
        self.readonly = bool(readonly)
        self.allow_exec = bool(allow_exec)
        self.allow_service_tunnels = bool(allow_service_tunnels)
        self.terminal_session_id = str(terminal_session_id or "")
        self.terminal_kind = str(terminal_kind or "")
        self.terminal_target = str(terminal_target or "")
        self.terminal_secret = str(terminal_secret or "")
        self.terminal_state_path = str(terminal_state_path or "")
        # Detached mode: a separately started return channel publishes its
        # terminal here. Ignored when the launcher passed a terminal directly.
        self.terminal_file = str(terminal_file or "")
        self._detached_key: Optional[tuple] = None
        self._process: Optional[subprocess.Popen] = None
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._relay_id = ""
        self.heartbeat_ticks = 6

    def _alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def connect(self) -> Dict[str, Any]:
        with self._lock:
            if self._alive():
                return self.status()
            registration = self.bridge.control("connect", {
                "client_id": self.client_id,
                "client_name": self.client_name,
                "readonly": self.readonly,
            })
            relay_id = str(registration.get("relay_id") or "")
            ws_url = str(registration.get("ws_url") or "")
            relay_token = str(registration.get("relay_token") or "")
            if not relay_id or not ws_url or not relay_token:
                raise RuntimeError("PawFlow returned an incomplete relay registration")
            env = os.environ.copy()
            env.update({
                "PAWFLOW_RELAY_SERVER": ws_url,
                "PAWFLOW_RELAY_TOKEN": relay_token,
                "PAWFLOW_RELAY_ID": relay_id,
                "PAWFLOW_RELAY_DIR": str(self.root),
                "PAWFLOW_GATEWAY_KEY": self.bridge.gateway_key,
                "PAWFLOW_RELAY_ALLOW_EXEC": "1" if self.allow_exec else "0",
            })
            runtime_root = str(Path(__file__).resolve().parent.parent)
            python_path = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = (
                runtime_root + (os.pathsep + python_path if python_path else "")
            )
            env["PAWFLOW_RELAY_RUNTIME_ROOT"] = runtime_root
            # The token and local path stay out of argv.  stderr is inherited for
            # diagnostics; stdout must never share the MCP JSON-RPC channel.
            command = [sys.executable, "-m", "pawflow_relay.cli"]
            if self.allow_service_tunnels:
                command.append("--allow-service-tunnels")
            try:
                self._process = subprocess.Popen(  # nosec B603
                    command,
                    env=env, stdin=subprocess.DEVNULL,
                    stdout=sys.stderr, stderr=sys.stderr,
                )
            except Exception:
                self.bridge.control("disconnect", {
                    "client_id": self.client_id,
                    "release_client": True,
                })
                raise
            self._relay_id = relay_id
            if self.terminal_session_id:
                try:
                    self.bridge.control("terminal", {
                        "client_id": self.client_id,
                        "operation": "register",
                        "session_id": self.terminal_session_id,
                        "kind": self.terminal_kind,
                        "target": self.terminal_target,
                        "secret": self.terminal_secret,
                        "state_path": self.terminal_state_path,
                    })
                except Exception:
                    self._process.terminate()
                    self._process.wait(timeout=5)
                    self._process = None
                    self.bridge.control("disconnect", {
                        "client_id": self.client_id,
                        "release_client": True,
                    })
                    raise
            self._detached_key = None
            try:
                self.sync_detached_terminal()
            except Exception as exc:
                _log(f"detached terminal registration failed: {exc}")
            self._ensure_heartbeat()
            return {
                "connected": True,
                "starting": True,
                "relay_id": relay_id,
                "root": str(self.root),
                "readonly": self.readonly,
                "allow_exec": self.allow_exec,
                "allow_service_tunnels": self.allow_service_tunnels,
                "auto_default": False,
            }

    def connect_until_ready(self, retry_seconds: float = 10.0) -> threading.Thread:
        """Retry the first relay connection in the background until it works.

        An MCP client that kills its server process (Agy on exit) never lets
        the bridge release its CLI lease, so a bridge restarted within
        ``CLIENT_LEASE_TTL_SECONDS`` is refused with "already has an active
        CLI instance". Exiting then closed stdio before ``initialize`` and
        the client showed the whole server as dead. The bridge keeps serving
        MCP and takes the relay as soon as the stale lease expires.
        """
        def _retry() -> None:
            while not self._stop.wait(retry_seconds):
                try:
                    state = self.connect()
                except Exception as exc:
                    _log(f"relay connection retry failed: {exc}")
                    continue
                _log(f"relay registered: {state.get('relay_id')} root={self.root}")
                return

        thread = threading.Thread(
            target=_retry, daemon=True, name="pawflow-mcp-relay-connect")
        thread.start()
        return thread

    def _ensure_heartbeat(self) -> None:
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            return
        self._stop.clear()

        def _heartbeat() -> None:
            # Ticks of 5 s: the detached terminal file is checked every tick,
            # the server status every ``heartbeat_ticks`` ticks (30 s).
            tick = 0
            while not self._stop.wait(5):
                tick += 1
                if tick % self.heartbeat_ticks:
                    try:
                        self.sync_detached_terminal()
                    except Exception as exc:
                        _log(f"detached terminal sync failed: {exc}")
                    continue
                try:
                    remote = self.bridge.control("status", {"client_id": self.client_id})
                except Exception as exc:
                    _log(f"relay heartbeat failed: {exc}")
                    continue
                self._recover(remote)
                try:
                    self.sync_detached_terminal(remote)
                except Exception as exc:
                    _log(f"detached terminal sync failed: {exc}")

        self._heartbeat_thread = threading.Thread(
            target=_heartbeat, daemon=True, name="pawflow-mcp-relay-heartbeat")
        self._heartbeat_thread.start()

    def _read_detached_record(self) -> Optional[Dict[str, str]]:
        try:
            value = json.loads(Path(self.terminal_file).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(value, dict):
            return None
        record = {key: str(value.get(key) or "") for key in (
            "session_id", "kind", "target", "secret", "state_path")}
        if (record["kind"] not in {"tmux", "winpty"}
                or not record["session_id"] or not record["target"]):
            return None
        return record

    def sync_detached_terminal(
            self, remote: Optional[Dict[str, Any]] = None) -> None:
        """Register, refresh or clear the detached return channel.

        Registers when the file appears or changes, re-registers when the
        server reports no terminal (for example after a server restart), and
        clears the registration when the attach process removed the file.
        """
        if self.terminal_session_id or not self.terminal_file:
            return
        if remote is not None and str(remote.get("client_id") or "") not in (
                "", self.client_id):
            return
        with self._lock:
            if not self._alive():
                return
            record = self._read_detached_record()
            if record is None:
                if self._detached_key is not None:
                    self._detached_key = None
                    self.bridge.control("terminal", {
                        "client_id": self.client_id, "operation": "clear"})
                    _log("detached terminal cleared")
                return
            key = tuple(record[name] for name in sorted(record))
            lost = remote is not None and not remote.get("terminal_ready")
            if key == self._detached_key and not lost:
                return
            result = self.bridge.control("terminal", {
                "client_id": self.client_id,
                "operation": "register",
                "session_id": record["session_id"],
                "kind": record["kind"],
                "target": record["target"],
                "secret": record["secret"],
                "state_path": record["state_path"],
            })
            if result.get("terminal_ready"):
                self._detached_key = key
                _log(f"detached terminal registered ({record['kind']})")

    def disconnect(self, release_client: bool = False) -> Dict[str, Any]:
        with self._lock:
            if release_client:
                self._stop.set()
            process = self._process
            self._process = None
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            try:
                remote = self.bridge.control(
                    "disconnect", {
                        "client_id": self.client_id,
                        "release_client": bool(release_client),
                    })
            except Exception as exc:
                remote = {"ok": False, "error": str(exc)}
            return {
                "connected": False, "relay_id": self._relay_id,
                "root": str(self.root), "client_released": bool(release_client),
                "remote": remote,
            }

    def close(self) -> Dict[str, Any]:
        """Stop the local relay and release this bridge's logical CLI lease."""
        return self.disconnect(release_client=True)

    def status(self) -> Dict[str, Any]:
        try:
            remote = self.bridge.control("status", {"client_id": self.client_id})
        except Exception as exc:
            remote = {"connected": False, "error": str(exc)}
        return {
            "connected": self._alive() and bool(remote.get("connected")),
            "process_running": self._alive(),
            "relay_id": self._relay_id or remote.get("relay_id", ""),
            "root": str(self.root),
            "readonly": self.readonly,
            "allow_exec": self.allow_exec,
            "allow_service_tunnels": self.allow_service_tunnels,
            "auto_default": False,
            "server": remote,
            "terminal_session_id": self.terminal_session_id,
            "terminal_kind": self.terminal_kind,
        }

    def reconnect(self) -> Dict[str, Any]:
        self.disconnect()
        return self.connect()

    def _recover(self, remote: Dict[str, Any]) -> None:
        """Reconnect a relay whose process died or whose lease was released.

        A lease held by another client is left alone: that client owns the
        publication now.
        """
        if self._stop.is_set():
            return
        dead = not self._alive()
        released = not str(remote.get("client_id") or "")
        if not (dead or released):
            return
        _log("relay " + ("process exited" if dead else "lease released")
             + "; reconnecting")
        try:
            self.reconnect()
        except Exception as exc:
            _log(f"relay reconnect failed: {exc}")


def _http_error_response(request_id: Any, method: str, status: int,
                         payload: Any) -> Optional[Dict[str, Any]]:
    """The JSON-RPC error for a failed forward, or None for a notification.

    A notification has no id and must never get a response. Answering one
    with ``"id": null`` (for example after a server restart lost the MCP
    session) made Agy's MCP client treat the stream as invalid, close the
    connection, and refuse every later call until /mcp reconnected it.
    """
    message = (payload.get("error") if isinstance(payload, dict)
               else f"PawFlow returned HTTP {status}")
    if request_id is None:
        _log(f"notification {method or '?'} failed: HTTP {status} {message}")
        return None
    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": -32000, "message": str(message)}}


def _tool_result(value: Any, is_error: bool = False) -> Dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(
            value, ensure_ascii=False, indent=2)}],
        "isError": bool(is_error),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pawflow-mcp", description="PawFlow published-conversation MCP bridge")
    parser.add_argument("--url", default=os.environ.get("PAWFLOW_MCP_URL", ""),
                        help="Published endpoint URL, e.g. https://host/mcp/srv_...")
    parser.add_argument("--relay-dir", default=os.environ.get("PAWFLOW_RELAY_DIR", "."),
                        help="Local project directory to share (default: current directory)")
    parser.add_argument("--client-name", default=os.environ.get("PAWFLOW_MCP_CLIENT_NAME", "CLI"))
    parser.add_argument("--readonly", action="store_true")
    parser.add_argument("--allow-exec", action="store_true")
    parser.add_argument(
        "--allow-service-tunnels", action="store_true",
        help="Allow explicitly approved FRP TCP service tunnels")
    parser.add_argument("--terminal-session-id", default=os.environ.get(
        "PAWFLOW_MCP_TERMINAL_SESSION_ID", ""))
    parser.add_argument("--terminal-kind", default=os.environ.get(
        "PAWFLOW_MCP_TERMINAL_KIND", ""))
    parser.add_argument("--terminal-target", default=os.environ.get(
        "PAWFLOW_MCP_TERMINAL_TARGET", ""))
    parser.add_argument("--terminal-secret", default=os.environ.get(
        "PAWFLOW_MCP_TERMINAL_SECRET", ""))
    parser.add_argument("--terminal-state-path", default=os.environ.get(
        "PAWFLOW_MCP_TERMINAL_STATE_PATH", ""))
    parser.add_argument(
        "--terminal-file", default=os.environ.get("PAWFLOW_MCP_TERMINAL_FILE", ""),
        help="Detached mode: JSON file written by a separately started return "
             "channel (run-client.py --attach-pid/--attach-tmux)")
    return parser


def main(argv=None) -> int:
    _use_utf8_stdio()
    args = build_parser().parse_args(argv)
    api_key = os.environ.get("PAWFLOW_MCP_API_KEY", "")
    gateway_key = os.environ.get("PAWFLOW_GATEWAY_KEY", "")
    if not args.url or not api_key:
        _log("PAWFLOW_MCP_URL/--url and PAWFLOW_MCP_API_KEY are required")
        return 2
    bridge = HTTPBridge(args.url, api_key, gateway_key)
    relay = RelayController(
        bridge, Path(args.relay_dir), args.client_name,
        readonly=args.readonly, allow_exec=args.allow_exec,
        allow_service_tunnels=args.allow_service_tunnels,
        terminal_session_id=args.terminal_session_id,
        terminal_kind=args.terminal_kind,
        terminal_target=args.terminal_target,
        terminal_secret=args.terminal_secret,
        terminal_state_path=args.terminal_state_path,
        terminal_file=args.terminal_file,
    )
    try:
        state = relay.connect()
        _log(f"relay registered: {state.get('relay_id')} root={relay.root}")
    except Exception as exc:
        _log(f"automatic relay connection failed: {exc}; "
             "MCP stays up and the relay connection is retried")
        relay.connect_until_ready()

    cleaned = False

    def _cleanup() -> None:
        nonlocal cleaned
        if cleaned:
            return
        cleaned = True
        relay.close()

    atexit.register(_cleanup)
    output_lock = threading.Lock()
    call_threads = set()
    call_threads_lock = threading.Lock()

    def respond(payload: Dict[str, Any]) -> None:
        with output_lock:
            sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
            sys.stdout.flush()

    def dispatch(message: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            request_id = message.get("id") if isinstance(message, dict) else None
            return {"jsonrpc": "2.0", "id": request_id,
                    "error": {"code": -32600, "message": "Invalid Request"}}
        request_id = message.get("id")
        method = str(message.get("method") or "")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        try:
            if method == "tools/call" and str(params.get("name") or "").startswith("pawflow_relay_"):
                name = str(params.get("name") or "")
                operation = name[len("pawflow_relay_"):]
                if operation == "connect":
                    value = relay.connect()
                elif operation == "disconnect":
                    value = relay.disconnect()
                elif operation == "status":
                    value = relay.status()
                elif operation == "reconnect":
                    value = relay.reconnect()
                else:
                    raise ValueError(f"Unknown relay operation: {operation}")
                return (
                    {"jsonrpc": "2.0", "id": request_id,
                     "result": _tool_result(value)}
                    if request_id is not None else None
                )

            status, payload = bridge.rpc(message)
            if status >= 400:
                return _http_error_response(request_id, method, status, payload)
            if method == "tools/list" and isinstance(payload, dict):
                tools = (((payload.get("result") or {}).get("tools")) or [])
                payload["result"]["tools"] = tools + _LOCAL_TOOLS
            return payload if request_id is not None and payload is not None else None
        except Exception as exc:
            return (
                {"jsonrpc": "2.0", "id": request_id,
                 "error": {"code": -32000, "message": str(exc)}}
                if request_id is not None else None
            )

    def handle(message: Any) -> None:
        if isinstance(message, list):
            if not message:
                respond({"jsonrpc": "2.0", "id": None,
                         "error": {"code": -32600, "message": "Invalid Request"}})
                return
            responses = [response for response in
                         (dispatch(item) for item in message)
                         if response is not None]
            if responses:
                respond(responses)
            return
        response = dispatch(message)
        if response is not None:
            respond(response)

    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            message = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            respond({"jsonrpc": "2.0", "id": None,
                     "error": {"code": -32700, "message": str(exc)}})
            continue
        is_tool_call = (
            isinstance(message, dict) and message.get("method") == "tools/call"
        ) or (
            isinstance(message, list)
            and any(isinstance(item, dict) and item.get("method") == "tools/call"
                    for item in message)
        )
        if is_tool_call:
            def _run(value=message):
                thread = threading.current_thread()
                try:
                    handle(value)
                finally:
                    with call_threads_lock:
                        call_threads.discard(thread)
            thread = threading.Thread(target=_run, daemon=False,
                                      name=f"pawflow-mcp-call-{message.get('id')}")
            with call_threads_lock:
                call_threads.add(thread)
            thread.start()
        else:
            handle(message)

    while True:
        with call_threads_lock:
            active = [thread for thread in call_threads if thread.is_alive()]
        if not active:
            break
        for thread in active:
            thread.join(timeout=0.5)
    _cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

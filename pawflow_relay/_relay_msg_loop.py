"""Per-connection WS message router for the relay worker.

`ConnSession` owns the inner ``while True`` recv/dispatch loop that used to
live inside ``_ws_connect``. The outer reconnect loop (connect, handshake,
register, per-connection resource setup, teardown/backoff) stays in
``worker.py``; once the socket and its connection-scoped resources exist they
are bundled into a :class:`ConnContext` and handed to a fresh ``ConnSession``
per reconnect.

The split is mechanical: the message-loop locals (`_inflight_cmds`,
`_inflight_lock`, `_disconnect_reason`, `_close_info`, the three FUSE
clients) became attributes, and each ``if _mtype == ...`` branch became a
method. Behaviour is unchanged — including the SSL send-lock discipline
(concurrent writes on an SSL socket interleave mid-record), the inflight
command tracking, and the thread-pool execution of `command` actions.

The ``spawn_relay`` Docker context is resolved by a worker-supplied callback
(`ctx.resolve_spawn_docker_env`) so the ``globals()`` reads it depends on
stay in the worker module's scope, not this module's.
"""
import json
import socket
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from pawflow_relay.proc_registry import kill_inflight_proc
from pawflow_relay._relay_session import close_frame_info


@dataclass
class ConnContext:
    """Connection-scoped resources the message loop closes over.

    Built once per (re)connection in ``worker._ws_connect`` after the socket
    is live and the per-connection helpers (pool, terminal manager, child
    manager, FUSE clients) exist.
    """
    sock: Any
    send_lock: Any
    ws_frame_send: Callable
    ws_frame_recv: Callable
    socket_diag: dict
    last_activity: list
    pool: Any
    execute_command: Callable
    term_mgr: Any
    children: Any
    child_cfg: Any
    term_send: Callable
    fuse_clients: tuple  # (server, filestore, skills) ServerFsClient | None
    remote_mount_mgr: Optional[Any]
    resolve_spawn_docker_env: Callable  # () -> DockerEnv


class ConnSession:
    """Drives one WS connection's message loop until it disconnects."""

    def __init__(self, ctx: ConnContext):
        self.ctx = ctx
        self.sock = ctx.sock
        self.send_lock = ctx.send_lock
        self.ws_frame_send = ctx.ws_frame_send
        self.ws_frame_recv = ctx.ws_frame_recv
        self.socket_diag = ctx.socket_diag
        self.last_activity = ctx.last_activity
        self.pool = ctx.pool
        self.execute_command = ctx.execute_command
        self.term_mgr = ctx.term_mgr
        self.children = ctx.children
        self.child_cfg = ctx.child_cfg
        self.term_send = ctx.term_send
        self.remote_mount_mgr = ctx.remote_mount_mgr
        (self.server_fs_client, self.filestore_fs_client,
         self.skills_fs_client) = ctx.fuse_clients
        self.inflight_cmds: dict = {}
        self.inflight_lock = threading.Lock()
        self.disconnect_reason = "unknown"
        self.close_info = ""

    # ── Diagnostics ───────────────────────────────────────────────────

    def active_cmd_summary(self) -> str:
        with self.inflight_lock:
            if not self.inflight_cmds:
                return "none"
            now = time.time()
            parts = []
            for rid, item in list(self.inflight_cmds.items())[:6]:
                parts.append(
                    f"{item.get('action', '?')}:{rid[:8]}:{now - item.get('ts', now):.1f}s")
            extra = len(self.inflight_cmds) - len(parts)
            return ",".join(parts) + (f",+{extra}" if extra > 0 else "")

    # ── Main loop ─────────────────────────────────────────────────────

    def run(self) -> str:
        """Receive and dispatch frames until disconnect.

        Returns the disconnect reason on a clean break (server close frame or
        a failed keepalive ping). Lets recv/transport exceptions propagate to
        the worker's reconnect handler.
        """
        while True:
            try:
                opcode, payload = self.ws_frame_recv(self.sock)
                self.last_activity[0] = time.time()
            except socket.timeout:
                # Send app-level ping to keep connection alive. MUST hold
                # send_lock — worker threads from the pool also send on this
                # socket; concurrent writes on an SSL socket interleave bytes
                # mid-record and the server sees WRONG_VERSION_NUMBER (ssl is
                # not thread-safe for writes).
                if not self._send_ping():
                    break  # send failed -> connection dead
                continue

            if opcode == 0x08:
                self.close_info = close_frame_info(payload)
                self.disconnect_reason = f"server close frame {self.close_info}"
                sys.stderr.write(
                    f"[FSRelay] Disconnected: {self.disconnect_reason} "
                    f"inflight={self.active_cmd_summary()}\n")
                break
            elif opcode == 0x09:
                # Same reasoning as the ping above: SSL writes must be
                # serialized with worker-thread sends.
                with self.send_lock:
                    self.ws_frame_send(self.sock, payload, opcode=0x0A)
                continue
            elif opcode != 0x01:
                continue

            msg = json.loads(payload.decode("utf-8"))
            self._route(msg)
        return self.disconnect_reason

    def _send_ping(self) -> bool:
        try:
            with self.send_lock:
                self.ws_frame_send(
                    self.sock, json.dumps({"type": "ping"}).encode("utf-8"))
            self.last_activity[0] = time.time()  # successful send = alive
            return True
        except Exception as ping_err:
            self.disconnect_reason = f"ping send failed: {ping_err}"
            self.socket_diag["last_send_error"] = f"ping:{ping_err}"
            return False

    def _route(self, msg: dict):
        mtype = msg.get("type")
        if mtype == "relay_response":
            self._handle_relay_response(msg)
        elif mtype == "cancel_request":
            self._handle_cancel_request(msg)
        elif mtype == "remote_mount_manifest":
            self._handle_remote_mount_manifest(msg)
        elif mtype == "spawn_relay":
            self._handle_spawn_relay(msg)
        elif mtype == "stop_relay":
            self.children.handle_stop(msg, self.term_send)
        elif mtype == "terminal_input":
            self._handle_terminal_input(msg)
        elif mtype == "terminal_resize":
            self._handle_terminal_resize(msg)
        elif mtype == "command":
            self._handle_command(msg)

    # ── Per-message-type handlers ─────────────────────────────────────

    def _handle_relay_response(self, msg: dict):
        # Inverse-direction reply for a relay->server FS op. Wake the FUSE
        # callback waiting on this request_id. Try each client in turn —
        # request_ids are uuids so only one will own a given response.
        delivered = False
        for fsc in (self.server_fs_client, self.filestore_fs_client,
                    self.skills_fs_client):
            if fsc is not None and fsc.dispatch_response(msg):
                delivered = True
                break
        if not delivered and (self.server_fs_client is not None
                              or self.filestore_fs_client is not None
                              or self.skills_fs_client is not None):
            sys.stderr.write(
                f"[FSRelay] orphan relay_response: {msg.get('request_id', '?')}\n")

    def _handle_cancel_request(self, msg: dict):
        # Server-initiated kill: a tool action that spawned a Popen and
        # registered it via register_inflight_proc() gets terminated. After
        # this returns, the action's blocked proc.wait() unblocks and the
        # action exits — the original tool caller server-side has already
        # given up on the result, so we don't send a response here.
        rid = msg.get("request_id", "")
        if rid:
            ok = kill_inflight_proc(rid)
            sys.stderr.write(
                f"[FSRelay] cancel_request rid={rid} "
                f"hit={'yes' if ok else 'no-such-proc'}\n")

    def _handle_remote_mount_manifest(self, msg: dict):
        if self.remote_mount_mgr is None:
            return
        manifest = msg.get("manifest") or {}

        def _reconcile_remote_mounts(_m=manifest):
            try:
                self.remote_mount_mgr.reconcile(_m)
            except Exception as rme:
                sys.stderr.write(f"[RemoteFS] reconcile failed: {rme}\n")
        threading.Thread(
            target=_reconcile_remote_mounts, daemon=True,
            name="remote-mount-reconcile").start()

    def _handle_spawn_relay(self, msg: dict):
        # The parent's Docker context is resolved by the worker-supplied
        # callback so its globals()/args reads stay in worker module scope.
        self.children.handle_spawn(
            msg, self.child_cfg,
            self.ctx.resolve_spawn_docker_env(),
            self.term_send)

    def _handle_terminal_input(self, msg: dict):
        tid = msg.get("session_id", "")
        if tid in self.term_mgr.sessions:
            ok, err = self.term_mgr.write(tid, msg.get("data", ""))
            if not ok and err:
                sys.stderr.write(f"[FSRelay] terminal write error: {err}\n")

    def _handle_terminal_resize(self, msg: dict):
        tid = msg.get("session_id", "")
        if tid in self.term_mgr.sessions:
            self.term_mgr.resize(
                tid, cols=msg.get("cols", 80), rows=msg.get("rows", 24))

    def _handle_command(self, msg: dict):
        request_id = msg.get("request_id", "")
        sys.stderr.write(f"[FSRelay] Command: {msg.get('action', '?')}\n")
        if msg.get("action") in ("cs_ws_send", "cs_ws_close"):
            self._run_command_sync(msg, request_id)
            return
        with self.inflight_lock:
            self.inflight_cmds[request_id] = {
                "action": msg.get('action', '?'),
                "ts": time.time(),
            }
        # Execute in thread pool for parallel command handling.
        self.pool.submit(
            self._run_command, msg, request_id, self.sock, self.ws_frame_send)

    def _run_command_sync(self, msg: dict, request_id: str):
        # cs_ws_send / cs_ws_close run inline (no pool, no inflight tracking):
        # they must preserve the ordering of code-server WS frames.
        try:
            result = self.execute_command(msg)
        except Exception as e:
            result = {"ok": False, "error": str(e)}
        resp = json.dumps({
            "type": "result",
            "request_id": request_id,
            "data": result.get("data", result),
        }).encode("utf-8")
        try:
            with self.send_lock:
                self.socket_diag["last_send"] = (
                    f"result:{msg.get('action', '?')}:{request_id[:8]}")
                self.ws_frame_send(self.sock, resp)
                self.socket_diag["last_send_error"] = ""
        except Exception as send_err:
            self.socket_diag["last_send_error"] = (
                f"result:{msg.get('action', '?')}:{request_id[:8]}:{send_err}")
            sys.stderr.write(
                f"[FSRelay] result send failed: action={msg.get('action', '?')} "
                f"rid={request_id[:8]} err={send_err}\n")

    def _run_command(self, _msg, _rid, _sock, _send_fn):
        _action = _msg.get("action", "?")
        # Streaming callback for exec_stream
        _on_output = None
        if _msg.get("action") == "exec_stream":
            def _on_output(stream, data):
                _frame = json.dumps({
                    "type": "exec_output",
                    "request_id": _rid,
                    "stream": stream,
                    "data": data,
                }).encode("utf-8")
                with self.send_lock:
                    _send_fn(_sock, _frame)
        try:
            _result = self.execute_command(_msg, on_output=_on_output)
            _resp = json.dumps({
                "type": "result",
                "request_id": _rid,
                "data": _result.get("data", _result),
            }).encode("utf-8")
        except Exception as _e:
            _resp = json.dumps({
                "type": "result",
                "request_id": _rid,
                "data": {"ok": False, "error": str(_e)},
            }).encode("utf-8")
        try:
            with self.send_lock:
                self.socket_diag["last_send"] = f"result:{_action}:{_rid[:8]}"
                _send_fn(_sock, _resp)
                self.socket_diag["last_send_error"] = ""
        except Exception as _send_err:
            self.socket_diag["last_send_error"] = (
                f"result:{_action}:{_rid[:8]}:{_send_err}")
            sys.stderr.write(
                f"[FSRelay] result send failed: action={_action} "
                f"rid={_rid[:8]} err={_send_err}\n")
        finally:
            with self.inflight_lock:
                self.inflight_cmds.pop(_rid, None)

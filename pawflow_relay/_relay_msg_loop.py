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
import queue
import socket
import threading
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from pawflow_relay.proc_registry import kill_inflight_proc
from pawflow_relay._relay_session import close_frame_info


#: Actions a person is waiting on right now: opening a terminal, a keystroke, a
#: resize, a desktop or code-server start. They run on their own thread instead
#: of the shared command pool, because that pool is a fixed number of workers
#: and a handful of agents running long tools keep every one of them busy for
#: minutes -- a terminal open then queued behind them and took 188s and 97s on a
#: live relay, waiting exactly until tool results freed a worker. The server
#: side sends these names (see ``tasks/ai/actions/_sf_k6.py``).
_INTERACTIVE_ACTIONS = frozenset({
    "open_terminal", "open_local_terminal",
    "list_terminals", "mcp_terminal_inject",
    "start_desktop", "stop_desktop", "desktop_status",
    "desktop_audio_open", "desktop_audio_close",
    "start_code_server", "stop_code_server",
    "cs_ws_open", "desktop_ws_open", "novnc_asset",
    "website_browser_start", "website_browser_stop",
})

#: Terminal I/O keeps its order: two keystrokes arriving close together, or the
#: two halves of a paste, must not reach the PTY the wrong way round. They run
#: one after another in one FIFO per session -- not inline in the message loop,
#: where an ``os.write`` to a full PTY or a forward to the host helper would
#: stall every other command.
#: ``close_terminal`` is here too: it must not overtake the keystrokes still
#: waiting for that session, or they reach a session that no longer exists. It
#: takes its place in the same FIFO, and the FIFO is retired with it.
_TERMINAL_IO_ACTIONS = frozenset({
    "write_terminal", "resize_terminal", "close_terminal"})

#: Log threshold for a command that had to wait for a worker. It is a diagnostic,
#: not a limit: nothing is cancelled or refused when it is crossed.
_POOL_WAIT_LOG_SECONDS = 5.0


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
        # One FIFO per terminal session, for keystrokes and resizes only.
        self._term_io_queues: dict = {}
        # Sessions whose close went through the FIFO: a keystroke arriving after
        # it is answered rather than queued behind the worker's sentinel, where
        # it would never run and its request would never be answered.
        self._closed_term_sessions: dict = {}
        self._term_io_lock = threading.Lock()
        # Run-fence high-waters (B1-O): armed by the server's
        # fence_snapshot BEFORE registration completes, raised by
        # fence_raise frames and monotonically by the tokens the
        # commands themselves carry. A command with a LOWER token than
        # the recorded watermark is refused WITHOUT executing.
        self.fence_highwaters: dict = {}
        self.fence_lock = threading.Lock()
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
        elif mtype in ("fence_snapshot", "fence_raise"):
            self._handle_fence_update(msg)
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

    def _handle_fence_update(self, msg: dict):
        """Merge fence watermarks monotonically; acknowledge snapshots."""
        highwaters = msg.get("highwaters") or {}
        with self.fence_lock:
            for key, token in highwaters.items():
                try:
                    token = int(token)
                except (TypeError, ValueError):
                    continue
                if token > int(self.fence_highwaters.get(key, 0)):
                    self.fence_highwaters[key] = token
        if msg.get("type") == "fence_snapshot":
            frame = json.dumps({
                "type": "fence_ack",
                "count": len(highwaters),
            }).encode("utf-8")
            with self.send_lock:
                self.ws_frame_send(self.sock, frame)

    def _fence_refuses(self, msg: dict) -> bool:
        """Atomic relay-side admission: refuse a stale token, raise the
        watermark otherwise — one lock, no window between the decision
        and what later commands compare against."""
        fence_key = msg.get("fence_key")
        fence_token = msg.get("fence_token")
        if not fence_key or fence_token is None:
            return False
        try:
            fence_token = int(fence_token)
        except (TypeError, ValueError):
            return False
        with self.fence_lock:
            if fence_token < int(self.fence_highwaters.get(fence_key, 0)):
                return True
            self.fence_highwaters[fence_key] = fence_token
        return False

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
            # "no-such-proc" is the ordinary outcome, not a failure: only the
            # actions that spawn a process (exec, bash, docker exec, scripts)
            # register one, so a read, a glob, a screen capture or a desktop
            # action has nothing to kill — and a command that already finished
            # unregisters itself in its finally block. Say which it is instead
            # of leaving a bare 'no-such-proc' for the reader to decode.
            sys.stderr.write(
                f"[FSRelay] cancel_request rid={rid} "
                + ("killed a running process\n" if ok else
                   "nothing to kill: the command already finished, or it "
                   "runs no process of its own\n"))

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
        if self._fence_refuses(msg):
            sys.stderr.write(
                f"[FSRelay] fence refuses {msg.get('action', '?')} "
                f"rid={request_id[:8]} (stale token)\n")
            resp = json.dumps({
                "type": "result",
                "request_id": request_id,
                "data": {"ok": False,
                         "error": ("fence_stale: run superseded — "
                                   "action not executed")},
            }).encode("utf-8")
            with self.send_lock:
                self.ws_frame_send(self.sock, resp)
            return
        if msg.get("action") in ("cs_ws_send", "cs_ws_close"):
            self._run_command_sync(msg, request_id)
            return
        if msg.get("action") in ("desktop_ws_send", "desktop_ws_close"):
            self._run_command_sync(msg, request_id)
            return
        with self.inflight_lock:
            self.inflight_cmds[request_id] = {
                "action": msg.get('action', '?'),
                "ts": time.time(),
            }
        # An interactive op runs on its own thread, never on the shared pool.
        # That pool is a fixed number of workers, and the tool commands of a
        # handful of agents keep every one of them busy for minutes: a terminal
        # open then queued behind them and took 188s (and its keystrokes would
        # have queued too). Spawning a PTY, writing a key and resizing are cheap
        # and rare -- there is nothing to bound, and a lane that can still queue
        # would only move the starvation.
        if msg.get("action") in _INTERACTIVE_ACTIONS:
            threading.Thread(
                target=self._run_command,
                args=(msg, request_id, self.sock, self.ws_frame_send),
                name=f"relay-term-{request_id[:8]}", daemon=True).start()
            return
        if msg.get("action") in _TERMINAL_IO_ACTIONS:
            # Ordered, one session at a time: see _TERMINAL_IO_ACTIONS.
            session_id = str(msg.get("session_id") or "")
            with self._term_io_lock:
                closed = session_id in self._closed_term_sessions
            if closed:
                self._reply_closed_terminal(request_id, session_id)
                return
            self._term_io_queue(session_id).put(
                (msg, request_id, self.sock, self.ws_frame_send))
            if msg.get("action") == "close_terminal":
                self._retire_term_io_queue(session_id)
            return
        self._submit_command(msg, request_id)

    def _submit_command(self, msg: dict, request_id: str) -> None:
        """Run a command, on the pool when the operator set one.

        By default there is no pool: every command gets its own thread. A fixed
        number of workers turned the relay into a bottleneck -- long tool runs
        kept them all busy, an ``open_terminal`` waited 188s, and other agents'
        calls failed with ``Relay timeout for exec`` while the relay was in fact
        healthy. A ceiling is the operator's to set
        (``PAWFLOW_RELAY_COMMAND_WORKERS``), and a command that then has to wait
        for a worker reports the wait instead of looking like a slow reply.
        """
        action = msg.get("action", "?")
        queued_at = time.time()

        if self.pool is None:
            threading.Thread(
                target=self._run_command,
                args=(msg, request_id, self.sock, self.ws_frame_send),
                name=f"relay-cmd-{action}-{request_id[:8]}", daemon=True).start()
            return

        def _run_after_wait():
            waited = time.time() - queued_at
            if waited > _POOL_WAIT_LOG_SECONDS:
                sys.stderr.write(
                    f"[FSRelay] {action} waited {waited:.1f}s for a command "
                    "worker; interactive actions have their own lane, but a "
                    "busy pool delays everything else (see "
                    "PAWFLOW_RELAY_COMMAND_WORKERS)\n")
            self._run_command(msg, request_id, self.sock, self.ws_frame_send)

        self.pool.submit(_run_after_wait)

    def _term_io_queue(self, session_id: str) -> "queue.Queue":
        """Return the FIFO that serializes one terminal session's I/O."""
        with self._term_io_lock:
            entry = self._term_io_queues.get(session_id)
            if entry is None:
                entry = queue.Queue()
                self._term_io_queues[session_id] = entry
                threading.Thread(
                    target=self._term_io_worker, args=(entry,),
                    name=f"relay-term-io-{session_id[:8] or 'default'}",
                    daemon=True).start()
            return entry

    def _term_io_worker(self, fifo: "queue.Queue") -> None:
        while True:
            item = fifo.get()
            if item is None:
                return
            msg, request_id, sock, send_fn = item
            try:
                # _run_command reports its own failures on the wire.
                self._run_command(msg, request_id, sock, send_fn)
            except Exception as exc:
                sys.stderr.write(
                    f"[FSRelay] terminal io command failed: {exc}\n")
            finally:
                fifo.task_done()

    def _retire_term_io_queue(self, session_id: str) -> None:
        """Retire a session's FIFO once the close it queued has run.

        Nothing used to stop these workers: one thread stayed parked on
        ``get()`` for the life of the connection, and because ``ConnSession`` is
        rebuilt at every reconnect, a fresh series started each time.
        """
        with self._term_io_lock:
            fifo = self._term_io_queues.get(session_id)
            self._closed_term_sessions[session_id] = True
        if fifo is None:
            return
        threading.Thread(
            target=self._retire_term_io_worker, args=(session_id, fifo),
            name=f"relay-term-retire-{session_id[:8] or 'default'}",
            daemon=True).start()

    def _retire_term_io_worker(self, session_id: str,
                              fifo: "queue.Queue") -> None:
        fifo.join()  # the close, and everything queued before it, has run
        fifo.put(None)  # stops _term_io_worker
        with self._term_io_lock:
            if self._term_io_queues.get(session_id) is fifo:
                del self._term_io_queues[session_id]

    def shutdown_term_io(self) -> None:
        """Stop every session FIFO; call this when the connection ends."""
        with self._term_io_lock:
            queues = list(self._term_io_queues.items())
            self._term_io_queues.clear()
        for _session_id, fifo in queues:
            fifo.put(None)

    def _reply_closed_terminal(self, request_id: str, session_id: str) -> None:
        """Answer a command aimed at a session that is already closed."""
        try:
            with self.send_lock:
                self.ws_frame_send(self.sock, json.dumps({
                    "type": "result",
                    "request_id": request_id,
                    "data": {"ok": False,
                             "error": f"terminal session {session_id[:8]} is closed"},
                }).encode("utf-8"))
        except Exception as exc:
            sys.stderr.write(
                f"[FSRelay] closed-terminal reply failed: {exc}\n")
        with self.inflight_lock:
            self.inflight_cmds.pop(request_id, None)

    def _run_command_sync(self, msg: dict, request_id: str):
        # WebSocket sends and closes run inline (no pool or inflight tracking)
        # to preserve each code-server and VNC byte stream's wire ordering.
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
        # Streaming callbacks use distinct wire message types.  The HTTP
        # callback is required by _relay_actions.http_proxy: without it the
        # action deliberately rejects the buffering inline contract.
        _on_output = None
        if _action == "exec_stream":
            def _on_output(stream, data):
                _frame = json.dumps({
                    "type": "exec_output",
                    "request_id": _rid,
                    "stream": stream,
                    "data": data,
                }).encode("utf-8")
                with self.send_lock:
                    _send_fn(_sock, _frame)
        elif _action in ("http_proxy", "http_fetch"):
            def _on_output(kind, data):
                _frame = json.dumps({
                    "type": "http_response",
                    "request_id": _rid,
                    "kind": kind,
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

"""PawFlow relay — worker-side WS protocol, action dispatch.

This module is the body of the relay worker. It runs either natively on
the user's host or inside the relay Docker container; in both cases
`pawflow_relay/` is on the Python path (mounted alongside the launcher
script in the container; importable via the source tree on the host).

The per-action handlers live in sibling modules — `_relay_terminal`,
`_relay_codeserver`, `_relay_desktop`, `_relay_actions` — and the
connection drives them through the `_execute_command` dispatcher.

Public entry points:
    _ws_connect(url, token, secret, relay_id, root_dir, readonly, ...)
    _is_allowed_tmp_path(path)

Stdlib-only plus the in-tools sibling modules fs_common / fs_actions /
fs_exec / fs_screen / fs_mcp / fs_http, which are imported lazily where
needed so this module can be introspected without pulling in the whole
world.
"""
import logging

import json
import os
import socket
import struct
import sys
import tempfile
import time
from pathlib import Path

from pawflow_relay.auth import (
    forward_to_host_helper as _forward_to_host_helper,
)



# ── Path allowlist (outside root_dir) ─────────────────────────────
#
# The relay is a filesystem sandbox bounded by --dir (root_dir). But
# a few absolute path prefixes are safe to allow through even when
# they don't live under the root: system temp dirs. They are local to
# the container/process, opaque to the host, and blocking them just
# breaks legitimate ephemeral writes (test fixtures, scratch files,
# commit message files, editor swap files).

def _tmp_allowlist():
    dirs = ["/tmp", "/var/tmp"]  # nosec B108 - explicit temp-dir allowlist for relay sandbox.
    try:
        dirs.append(tempfile.gettempdir())
    except Exception:
        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
    # Resolve + dedup
    resolved = []
    seen = set()
    for d in dirs:
        try:
            rd = str(Path(d).resolve())
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            continue
        if rd not in seen:
            seen.add(rd)
            resolved.append(rd)
    return resolved


_TMP_ALLOWLIST = _tmp_allowlist()


# ── In-flight subprocess registry ───────────────────────────────────
# Lives in `pawflow_relay.proc_registry` to avoid a circular import
# with the action handlers (fs_actions/fs_exec/...). Re-exported here
# so call sites that already import from `worker` don't have to change.
from pawflow_relay.proc_registry import (  # noqa: E402
    kill_inflight_proc,
)
from pawflow_relay._relay_state import RelayWorkerState  # noqa: E402
from pawflow_relay._relay_children import (  # noqa: E402
    ChildRelayManager, ChildRelayConfig, DockerEnv,
)
from pawflow_relay._relay_terminal import TerminalManager  # noqa: E402
from pawflow_relay._relay_dispatch import (  # noqa: E402
    DispatchCtx,
    execute_command as _dispatch_execute,
)
from pawflow_relay._relay_fs_setup import setup_combined_fs as _setup_combined_fs  # noqa: E402
from pawflow_relay._relay_conn import connect_and_handshake as _connect_and_handshake  # noqa: E402


def _is_allowed_tmp_path(path: str) -> bool:
    """True when `path` is absolute and falls under a system temp dir."""
    if not path or not isinstance(path, str):
        return False
    p = Path(path)
    if not p.is_absolute():
        return False
    try:
        resolved = p.resolve()
    except Exception:
        return False
    for allowed in _TMP_ALLOWLIST:
        try:
            resolved.relative_to(allowed)
            return True
        except ValueError:
            continue
    return False


# ── WS Reverse client ─────────────────────────────────────────────

def _ws_connect(url, token, secret, relay_id, root_dir, readonly, allow_exec=False,  # nosec B107
                allow_automation=False, allow_local_screen=False, allow_local=False,
                gateway_cookie="", gateway_key="", session_token="", server_mount="",
                filestore_mount="", skills_mount=""):
    """Connect to the PawFlow server via WebSocket and process filesystem commands.

    server_mount: if set, mount a FUSE proxy at this local path that
    forwards each syscall to the server's RelayServerFs handler over
    the same WS tunnel. Read-only in this phase. The path is bind-mounted
    by the operator into any docker container that needs to see the
    user's CLAUDE_SESSIONS_DIR slot.

    filestore_mount: if set, mount a second FUSE proxy at this local
    path that exposes the server FileStore as a virtualized hierarchy
    (/<file_id>/<filename>). Read-only — writes go through the
    HTTP/MCP FileStore APIs, not the FUSE mount.

    skills_mount: if set, mount a third FUSE proxy at this local path
    that exposes the server Agent Skills repository (global + this
    user's skill tree). Read-only — it lets non-CLI providers reach a
    skill's asset files referenced from its instructions.
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)
    use_ssl = parsed.scheme in ("wss", "https")
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if use_ssl else 80)
    path = parsed.path or "/ws/relay"

    mode = "read" if readonly else "readwrite"
    # Detect available shells for exec
    try:
        from fs_actions import detect_available_shells
        _shells = detect_available_shells()
    except Exception:
        _shells = {}
    def _is_containerized():
        return os.path.exists("/.dockerenv") or bool(os.environ.get("PAWFLOW_DOCKER_IMAGE"))

    # host_root: the original path on the user's machine (before Docker mount)
    # Always use forward slashes (Windows backslashes break JSON display)
    _host_root = os.environ.get("PAWFLOW_HOST_WORKDIR", "")
    if not _host_root and not _is_containerized():
        _host_root = root_dir
    _host_root = _host_root.replace("\\", "/")

    info = {
        "platform": sys.platform,
        "root": root_dir,
        "host_root": _host_root,
        "mode": mode,
        "shells": list(_shells.keys()),
        "containerized": _is_containerized(),
        "docker_image": os.environ.get("PAWFLOW_DOCKER_IMAGE", ""),
        "container_id": socket.gethostname() if _is_containerized() else "",
        "allow_exec": allow_exec,
        "allow_automation": allow_automation,
        "allow_local_screen": allow_local_screen,
        "allow_local": allow_local,
    }

    def _resolve(rel_path):
        if _is_allowed_tmp_path(rel_path):
            return str(Path(rel_path).resolve())
        root = Path(root_dir).resolve()
        target = (root / rel_path).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            return None
        return str(target)

    class MockHandler:
        pass
    MockHandler.root_dir = root_dir
    MockHandler.secret = secret
    MockHandler.readonly = readonly
    MockHandler.allow_exec = allow_exec
    MockHandler.allow_automation = allow_automation
    MockHandler.allow_local_screen = allow_local_screen
    MockHandler.allow_local = allow_local
    mock = MockHandler()

    # Per-connection mutable state (process/session handles). Captured by
    # the action closures below; replaces the old _execute_command.<attr>
    # function-attribute stash.
    _state = RelayWorkerState()

    from pawflow_relay.ws_frame import ws_send as _ws_frame_send, ws_recv as _ws_frame_recv

    def _execute_command(msg, on_output=None):
        # Connection-scoped deps are bundled into _dispatch_ctx (built per
        # reconnect, once the socket / send-lock / terminal-manager exist);
        # the per-action routing lives in pawflow_relay._relay_dispatch.
        return _dispatch_execute(_dispatch_ctx, msg, on_output=on_output)

    # ── FUSE mounts (one-shot, survive WS reconnects) ────────────────
    # Create the FUSE mounts BEFORE entering the reconnect loop and
    # tear them down only on final exit. Each WS reconnect swaps the
    # underlying ServerFsClient via SwappableServerFsClient.set_inner;
    # the kernel-side mount and inode allocations stay stable, so
    # bind-mounts of /cc_sessions and /filestore in downstream
    # containers (notably CC) keep working across relay reconnects.
    # Encrypted conversation workspace: before serving any FS op, mount the
    # CryFS cipher-store as the plaintext view at the workspace root. Gated on
    # env the server sets ONLY for an unlocked encrypted workspace; plaintext
    # relays never set these, so this is a no-op for them. The DEK lives in the
    # container's RAM only -- the relay (and its key) vanish when it stops.
    _ws_dek = os.environ.get("PAWFLOW_WS_DEK_B64", "")
    _ws_cipher = os.environ.get("PAWFLOW_WS_CIPHER_DIR", "")
    _ws_mount = os.environ.get("PAWFLOW_WS_MOUNT", "") or root_dir
    if _ws_dek and _ws_cipher:
        try:
            from pawflow_relay import key_ops as _kops
            _wr = _kops.mount_encrypted_workspace(
                {"cipher_dir": _ws_cipher, "mount_dir": _ws_mount, "dek": _ws_dek})
            sys.stderr.write(f"[FSRelay] workspace cryfs mount: {_wr}\n")
        except Exception as _we:
            sys.stderr.write(f"[FSRelay] workspace cryfs mount failed: {_we}\n")

    _server_fs_swap = None
    (_server_fs_swap, _filestore_fs_swap, _skills_fs_swap,
     _server_fs_mount) = _setup_combined_fs(
        server_mount, filestore_mount, skills_mount)

    try:
        from pawflow_relay.remote_mounts import RemoteMountManager
        _remote_mount_mgr = RemoteMountManager()
    except Exception as _rme:
        sys.stderr.write(f"[RemoteFS] manager init failed: {_rme}\n")
        _remote_mount_mgr = None

    reconnect_delay = 1

    while True:
        _disconnect_reason = "connect setup"
        _last_activity = [time.time()]
        try:
            sys.stderr.write(f"[FSRelay] Connecting to {url} ...\n")
            sock = _connect_and_handshake(
                host, port, path, use_ssl, gateway_cookie,
                session_token, gateway_key)

            sys.stderr.write(f"[FSRelay] Connected to {url}\n")

            reg_msg = json.dumps({
                "type": "register",
                "token": token,
                "secret": secret,
                "relay_type": "relay",
                "relay_id": relay_id,
                "info": info,
            }).encode("utf-8")
            _ws_frame_send(sock, reg_msg)

            opcode, payload = _ws_frame_recv(sock)
            if opcode == 0x01:
                reg_resp = json.loads(payload.decode("utf-8"))
                if reg_resp.get("type") == "registered":
                    sys.stderr.write(f"[FSRelay] Registered as '{reg_resp.get('relay_id')}'\n")

            reconnect_delay = 1
            _KEEPALIVE_INTERVAL = 30
            _DEAD_TIMEOUT = 90  # force reconnect if no data for this long
            sock.settimeout(_KEEPALIVE_INTERVAL)
            ws_sock_ref = [sock]  # mutable ref for _execute_command closures
            _last_activity = [time.time()]  # updated on any recv
            import threading as _threading
            from concurrent.futures import ThreadPoolExecutor
            _pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="relay-cmd")
            _socket_diag = {
                "local_close": "",
                "last_send": "",
                "last_send_error": "",
            }

            def _diag_summary():
                parts = []
                for key in ("local_close", "last_send", "last_send_error"):
                    value = _socket_diag.get(key) or ""
                    if value:
                        parts.append(f"{key}={value}")
                return ";".join(parts) or "none"

            # Watchdog: force-close socket if no activity for _DEAD_TIMEOUT
            _watchdog_stop = _threading.Event()
            def _watchdog():
                while not _watchdog_stop.is_set():
                    _watchdog_stop.wait(15)
                    if _watchdog_stop.is_set():
                        break
                    idle = time.time() - _last_activity[0]
                    if idle > _DEAD_TIMEOUT:
                        _socket_diag["local_close"] = f"watchdog idle={idle:.0f}s"
                        sys.stderr.write(f"[FSRelay] Watchdog: no activity for {idle:.0f}s, forcing reconnect\n")
                        try:
                            sock.close()
                        except Exception:
                            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                        break
            _wd_thread = _threading.Thread(target=_watchdog, daemon=True, name="relay-watchdog")
            _wd_thread.start()
            _send_lock = _threading.Lock()
            # Child-relay tracking, recreated per connection (mirrors the old
            # per-connection _child_relays = {} reset). cfg is stable across
            # reconnects; the DockerEnv is resolved per spawn below.
            _children = ChildRelayManager()
            _child_cfg = ChildRelayConfig(
                url=url, token=token, secret=secret, readonly=readonly,
                allow_exec=allow_exec, allow_automation=allow_automation,
                allow_local_screen=allow_local_screen, allow_local=allow_local)

            def _term_send(_frame):
                # PTY reader threads stream output here; take the shared
                # send lock before writing to the (SSL) socket — concurrent
                # writes interleave mid-record and corrupt the stream.
                with _send_lock:
                    _ws_frame_send(sock, _frame)
            # Recreated per (re)connection so PTY sessions never outlive
            # their socket (mirrors the old per-connection _terminal_sessions).
            _term_mgr = TerminalManager(root_dir, _term_send)
            # Connection-scoped dependencies the action dispatcher closes over.
            # Rebuilt per reconnect so it carries the live socket ref / send
            # lock / terminal manager; _state and the flags are stable.
            _dispatch_ctx = DispatchCtx(
                state=_state, term_mgr=_term_mgr, send_lock=_send_lock,
                ws_sock_ref=ws_sock_ref, ws_frame_send=_ws_frame_send,
                resolve=_resolve, forward_to_host_helper=_forward_to_host_helper,
                root_dir=root_dir, readonly=readonly, allow_exec=allow_exec,
                allow_local=allow_local, allow_local_screen=allow_local_screen,
                allow_automation=allow_automation)
            _disconnect_reason = "unknown"
            _close_info = ""
            _inflight_cmds = {}
            _inflight_lock = _threading.Lock()

            def _active_cmd_summary():
                with _inflight_lock:
                    if not _inflight_cmds:
                        return "none"
                    now = time.time()
                    parts = []
                    for rid, item in list(_inflight_cmds.items())[:6]:
                        parts.append(
                            f"{item.get('action', '?')}:{rid[:8]}:{now - item.get('ts', now):.1f}s")
                    extra = len(_inflight_cmds) - len(parts)
                    return ",".join(parts) + (f",+{extra}" if extra > 0 else "")

            def _close_frame_info(payload: bytes) -> str:
                if not payload:
                    return "code=none reason=''"
                try:
                    if len(payload) >= 2:
                        code = struct.unpack("!H", payload[:2])[0]
                        reason = payload[2:].decode("utf-8", errors="replace")
                        return f"code={code} reason={reason!r}"
                    return f"code=none reason={payload.decode('utf-8', errors='replace')!r}"
                except Exception:
                    return f"malformed={payload[:80]!r}"

            # ── Per-WS-connection FUSE clients ──────────────────────────
            # The FUSE mounts themselves were created once before the
            # reconnect loop; here we just build a fresh ServerFsClient
            # bound to THIS sock and swap it into the swappable handle
            # the mount is holding. The kernel-side mount stays live
            # across WS reconnects so downstream container bind-mounts
            # of /cc_sessions and /filestore remain valid.
            from pawflow_relay.server_fs_client import ServerFsClient
            _server_fs_client = None
            if _server_fs_swap is not None:
                _server_fs_client = ServerFsClient(
                    send_callable=lambda b: _ws_frame_send(sock, b),
                    send_lock=_send_lock)
                _server_fs_swap.set_inner(_server_fs_client)

            _filestore_fs_client = None
            if _filestore_fs_swap is not None:
                _filestore_fs_client = ServerFsClient(
                    send_callable=lambda b: _ws_frame_send(sock, b),
                    send_lock=_send_lock)
                _filestore_fs_swap.set_inner(_filestore_fs_client)

            _skills_fs_client = None
            if _skills_fs_swap is not None:
                _skills_fs_client = ServerFsClient(
                    send_callable=lambda b: _ws_frame_send(sock, b),
                    send_lock=_send_lock)
                _skills_fs_swap.set_inner(_skills_fs_client)

            while True:
                try:
                    opcode, payload = _ws_frame_recv(sock)
                    _last_activity[0] = time.time()
                except socket.timeout:
                    # Send app-level ping to keep connection alive.
                    # MUST hold _send_lock — worker threads from _pool also send
                    # on this socket with the lock; concurrent writes on an SSL
                    # socket interleave bytes mid-record and the server sees
                    # WRONG_VERSION_NUMBER (ssl is not thread-safe for writes).
                    try:
                        with _send_lock:
                            _ws_frame_send(sock, json.dumps({"type": "ping"}).encode("utf-8"))
                        _last_activity[0] = time.time()  # successful send = connection alive
                    except Exception as _ping_err:
                        _disconnect_reason = f"ping send failed: {_ping_err}"
                        _socket_diag["last_send_error"] = f"ping:{_ping_err}"
                        break  # send failed → connection dead
                    continue

                if opcode == 0x08:
                    _close_info = _close_frame_info(payload)
                    _disconnect_reason = f"server close frame {_close_info}"
                    sys.stderr.write(
                        f"[FSRelay] Disconnected: {_disconnect_reason} "
                        f"inflight={_active_cmd_summary()}\n")
                    break
                elif opcode == 0x09:
                    # Same reasoning as the ping above: SSL writes must be
                    # serialized with worker-thread sends.
                    with _send_lock:
                        _ws_frame_send(sock, payload, opcode=0x0A)
                    continue
                elif opcode != 0x01:
                    continue

                msg = json.loads(payload.decode("utf-8"))
                _mtype = msg.get("type")
                if _mtype == "relay_response":
                    # Inverse-direction reply for a relay→server FS op.
                    # Wake the FUSE callback waiting on this request_id.
                    # Try each client in turn — request_ids are uuids so
                    # only one will own a given response.
                    _delivered = False
                    for _fsc in (_server_fs_client, _filestore_fs_client,
                                 _skills_fs_client):
                        if _fsc is not None and _fsc.dispatch_response(msg):
                            _delivered = True
                            break
                    if not _delivered and (_server_fs_client is not None
                                            or _filestore_fs_client is not None
                                            or _skills_fs_client is not None):
                        sys.stderr.write(
                            f"[FSRelay] orphan relay_response: {msg.get('request_id', '?')}\n")
                    continue
                if _mtype == "cancel_request":
                    # Server-initiated kill: a tool action that spawned a
                    # Popen and registered it via register_inflight_proc()
                    # gets terminated. After this returns, the action's
                    # blocked `proc.wait()` unblocks and the action exits
                    # — the original tool caller server-side has already
                    # given up on the result, so we don't need to send a
                    # response here.
                    _rid = msg.get("request_id", "")
                    if _rid:
                        _ok = kill_inflight_proc(_rid)
                        sys.stderr.write(
                            f"[FSRelay] cancel_request rid={_rid} "
                            f"hit={'yes' if _ok else 'no-such-proc'}\n")
                    continue
                if _mtype == "remote_mount_manifest":
                    if _remote_mount_mgr is not None:
                        _manifest = msg.get("manifest") or {}
                        def _reconcile_remote_mounts(_m=_manifest):
                            try:
                                _remote_mount_mgr.reconcile(_m)
                            except Exception as _rme:
                                sys.stderr.write(f"[RemoteFS] reconcile failed: {_rme}\n")
                        _threading.Thread(
                            target=_reconcile_remote_mounts, daemon=True,
                            name="remote-mount-reconcile").start()
                    continue
                if _mtype == "spawn_relay":
                    # Resolve the parent's Docker context here (globals()/args
                    # must be read in worker module scope), then delegate.
                    _parent_docker = globals().get('_DOCKER_EXEC_CONTAINER') or \
                                     getattr(__import__('fs_actions'), '_DOCKER_EXEC_CONTAINER', None) \
                                     if 'fs_actions' in sys.modules else None
                    _docker_cpus = getattr(globals().get('args', None), 'docker_cpus', '2')
                    _docker_memory = getattr(globals().get('args', None), 'docker_memory', '4g')
                    _children.handle_spawn(
                        msg, _child_cfg,
                        DockerEnv(parent_docker=bool(_parent_docker),
                                  cpus=_docker_cpus, memory=_docker_memory),
                        _term_send)

                elif msg.get("type") == "stop_relay":
                    _children.handle_stop(msg, _term_send)

                elif msg.get("type") == "terminal_input":
                    _tid = msg.get("session_id", "")
                    if _tid in _term_mgr.sessions:
                        _ok, _err = _term_mgr.write(_tid, msg.get("data", ""))
                        if not _ok and _err:
                            sys.stderr.write(f"[FSRelay] terminal write error: {_err}\n")

                elif msg.get("type") == "terminal_resize":
                    _tid = msg.get("session_id", "")
                    if _tid in _term_mgr.sessions:
                        _term_mgr.resize(_tid, cols=msg.get("cols", 80), rows=msg.get("rows", 24))

                elif msg.get("type") == "command":
                    request_id = msg.get("request_id", "")
                    sys.stderr.write(f"[FSRelay] Command: {msg.get('action', '?')}\n")
                    if msg.get("action") in ("cs_ws_send", "cs_ws_close"):
                        try:
                            _result = _execute_command(msg)
                        except Exception as _e:
                            _result = {"ok": False, "error": str(_e)}
                        _resp = json.dumps({
                            "type": "result",
                            "request_id": request_id,
                            "data": _result.get("data", _result),
                        }).encode("utf-8")
                        try:
                            with _send_lock:
                                _socket_diag["last_send"] = (
                                    f"result:{msg.get('action', '?')}:{request_id[:8]}")
                                _ws_frame_send(sock, _resp)
                                _socket_diag["last_send_error"] = ""
                        except Exception as _send_err:
                            _socket_diag["last_send_error"] = (
                                f"result:{msg.get('action', '?')}:{request_id[:8]}:{_send_err}")
                            sys.stderr.write(
                                f"[FSRelay] result send failed: action={msg.get('action', '?')} "
                                f"rid={request_id[:8]} err={_send_err}\n")
                        continue
                    with _inflight_lock:
                        _inflight_cmds[request_id] = {
                            "action": msg.get('action', '?'),
                            "ts": time.time(),
                        }
                    # Execute in thread pool for parallel command handling
                    def _run_cmd(_msg, _rid, _sock, _send_fn):
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
                                with _send_lock:
                                    _send_fn(_sock, _frame)
                        try:
                            _result = _execute_command(_msg, on_output=_on_output)
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
                            with _send_lock:
                                _socket_diag["last_send"] = f"result:{_action}:{_rid[:8]}"
                                _send_fn(_sock, _resp)
                                _socket_diag["last_send_error"] = ""
                        except Exception as _send_err:
                            _socket_diag["last_send_error"] = (
                                f"result:{_action}:{_rid[:8]}:{_send_err}")
                            sys.stderr.write(
                                f"[FSRelay] result send failed: action={_action} "
                                f"rid={_rid[:8]} err={_send_err}\n")
                        finally:
                            with _inflight_lock:
                                _inflight_cmds.pop(_rid, None)
                    _pool.submit(_run_cmd, msg, request_id, sock, _ws_frame_send)

        except KeyboardInterrupt:
            sys.stderr.write("\n[FSRelay] Shutting down.\n")
            _tm = locals().get('_term_mgr')
            if _tm:
                _tm.close_all()
            # Final exit — unmount the combined FUSE filesystem before
            # returning so we don't leave a dangling pyfuse3 mount
            # pointing at a dead WS.
            if _server_fs_mount is not None:
                try:
                    _server_fs_mount.stop()
                except Exception as _se:
                    sys.stderr.write(f"[FSRelay] combined-fs stop: {_se}\n")
            try:
                _socket_diag["local_close"] = "keyboard_interrupt"
                sock.close()
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            return
        except Exception as e:
            _idle = 0.0
            try:
                _idle = time.time() - _last_activity[0]
            except Exception:
                logging.getLogger(__name__).debug(
                    "Failed to compute relay idle time", exc_info=True)
            sys.stderr.write(
                f"[FSRelay] Connection error: {type(e).__name__}: {e} "
                f"(last_activity={_idle:.1f}s "
                f"reason={locals().get('_disconnect_reason', 'exception')} "
                f"inflight={locals().get('_active_cmd_summary', lambda: 'unknown')()} "
                f"diag={locals().get('_diag_summary', lambda: 'unknown')()})\n")
        finally:
            # Guard: on early connect errors, _term_mgr may not be defined
            # yet (it is created during per-connection setup, past the
            # handshake).
            _tm = locals().get('_term_mgr')
            if _tm:
                _tm.close_all()
            # Detach the per-WS ServerFsClient from the FUSE mount and
            # cancel its pending requests with EIO so the kernel doesn't
            # hang on the dead socket. The FUSE mount itself stays up
            # across reconnects — see the one-shot setup before this loop.
            for _swap in (_server_fs_swap, _filestore_fs_swap, _skills_fs_swap):
                if _swap is not None:
                    try:
                        _swap.clear_inner()
                    except Exception:
                        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            for _name in ('_server_fs_client', '_filestore_fs_client',
                          '_skills_fs_client'):
                _c = locals().get(_name)
                if _c is not None:
                    try:
                        _c.cancel_all('relay disconnected')
                    except Exception:
                        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            # Stop watchdog
            try:
                _watchdog_stop.set()
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            _cmd_pool = locals().get('_pool')
            if _cmd_pool is not None:
                try:
                    _cmd_pool.shutdown(wait=False, cancel_futures=True)
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            # Always close socket before reconnecting — prevents socket leak
            try:
                sock.close()
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

        sys.stderr.write(f"[FSRelay] Reconnecting in {reconnect_delay}s ...\n")
        time.sleep(reconnect_delay)
        # Exponential backoff: 1s → 2s → 4s → 8s → 16s → 30s → 60s
        reconnect_delay = min(reconnect_delay * 2, 60)





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
from pawflow_relay.proc_registry import (  # noqa: E402,F401
    kill_inflight_proc,  # re-exported for external `from worker import` callers
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
from pawflow_relay._relay_session import (  # noqa: E402
    build_connection_params as _build_connection_params,
    attach_fuse_clients as _attach_fuse_clients,
    detach_fuse_clients as _detach_fuse_clients,
)
from pawflow_relay._relay_msg_loop import ConnContext, ConnSession  # noqa: E402


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
    _cp = _build_connection_params(
        url, root_dir, readonly, allow_exec, allow_automation,
        allow_local_screen, allow_local)
    host, port, path = _cp.host, _cp.port, _cp.path
    use_ssl = _cp.use_ssl
    info = _cp.info

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

            # ── Per-WS-connection FUSE clients ──────────────────────────
            # The FUSE mounts themselves were created once before the
            # reconnect loop; here we just build a fresh ServerFsClient
            # bound to THIS sock and swap it into the swappable handle
            # the mount is holding. The kernel-side mount stays live
            # across WS reconnects so downstream container bind-mounts
            # of /cc_sessions and /filestore remain valid.
            _fuse_swaps = (_server_fs_swap, _filestore_fs_swap, _skills_fs_swap)
            _fuse_clients = _attach_fuse_clients(sock, _send_lock, _fuse_swaps)

            def _resolve_spawn_docker_env():
                # globals()/args must be read in this (worker) module scope;
                # resolved fresh per spawn so a late-set Docker context is
                # picked up. Mirrors the old inline spawn_relay resolution.
                _parent_docker = globals().get('_DOCKER_EXEC_CONTAINER') or \
                                 getattr(__import__('fs_actions'), '_DOCKER_EXEC_CONTAINER', None) \
                                 if 'fs_actions' in sys.modules else None
                _docker_cpus = getattr(globals().get('args', None), 'docker_cpus', '2')
                _docker_memory = getattr(globals().get('args', None), 'docker_memory', '4g')
                return DockerEnv(parent_docker=bool(_parent_docker),
                                 cpus=_docker_cpus, memory=_docker_memory)

            # The inner recv/dispatch loop lives in ConnSession; bundle the
            # connection-scoped resources and run it until disconnect.
            _session = ConnSession(ConnContext(
                sock=sock, send_lock=_send_lock,
                ws_frame_send=_ws_frame_send, ws_frame_recv=_ws_frame_recv,
                socket_diag=_socket_diag, last_activity=_last_activity,
                pool=_pool, execute_command=_execute_command,
                term_mgr=_term_mgr, children=_children, child_cfg=_child_cfg,
                term_send=_term_send, fuse_clients=_fuse_clients,
                remote_mount_mgr=_remote_mount_mgr,
                resolve_spawn_docker_env=_resolve_spawn_docker_env))
            # Exposed for the reconnect handler's diagnostic logging below.
            _active_cmd_summary = _session.active_cmd_summary
            _disconnect_reason = _session.run()

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
            # _fuse_clients may be undefined on an early connect error;
            # detach still clears the swaps (which are always defined).
            _detach_fuse_clients(
                (_server_fs_swap, _filestore_fs_swap, _skills_fs_swap),
                locals().get('_fuse_clients'))
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





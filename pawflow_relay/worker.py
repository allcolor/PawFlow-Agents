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
    _WRITE_ACTIONS

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
import subprocess  # nosec B404
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from fs_common import (
    _docker_cmd, _translate_path, _to_host_path,
)
from pawflow_relay.auth import (
    claude_auth_login as _claude_auth_login,
    codex_auth_login as _codex_auth_login,
    gemini_auth_login as _gemini_auth_login,
    forward_to_host_helper as _forward_to_host_helper,
)


_WRITE_ACTIONS = frozenset({
    "write_file", "delete_file", "mkdir", "find_replace", "edit", "exec",
})


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
from pawflow_relay._relay_terminal import TerminalManager  # noqa: E402
from pawflow_relay._relay_codeserver import (  # noqa: E402
    start_code_server as _cs_start,
    stop_code_server as _cs_stop,
    cs_ws_open as _cs_ws_open,
    cs_ws_send as _cs_ws_send,
    cs_ws_close as _cs_ws_close,
)
from pawflow_relay._relay_actions import (  # noqa: E402
    http_proxy as _act_http_proxy,
    script_hash as _act_script_hash,
    update_scripts as _act_update_scripts,
)
from pawflow_relay._relay_desktop import (  # noqa: E402
    desktop_ws_open as _dt_ws_open,
    desktop_ws_send as _dt_ws_send,
    desktop_ws_close as _dt_ws_close,
    start_desktop as _dt_start_desktop,
    stop_desktop as _dt_stop_desktop,
    desktop_status as _dt_desktop_status,
    start_local_desktop as _dt_start_local_desktop,
    stop_local_desktop as _dt_stop_local_desktop,
    local_screen_check as _dt_local_screen_check,
)


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


@dataclass
class RelayWorkerState:
    """Per-connection mutable state for a WS relay worker.

    Holds the long-lived process/session handles that the action
    handlers in `_execute_command` read and mutate. Previously these
    lived as ad-hoc attributes stashed on the `_execute_command`
    function object; a fresh instance is created per `_ws_connect`
    call so nothing leaks across connections. Defaults mirror the old
    lazy-init values exactly (None for handles/ports, a fresh dict for
    each WS-session map).
    """
    # code-server
    code_server_proc: object = None
    code_server_port: object = None
    code_server_base_path: str = ""
    cs_ws_sessions: dict = field(default_factory=dict)
    # desktop (containerized)
    desktop_procs: object = None
    desktop_essential_procs: object = None
    desktop_vnc_port: object = None
    desktop_novnc_port: object = None
    desktop_display: object = None
    desktop_watchdog_stop: object = None
    desktop_watchdog_thread: object = None
    desktop_ws_sessions: dict = field(default_factory=dict)
    # local desktop (host screen)
    local_desktop_procs: object = None
    local_desktop_vnc_port: object = None
    local_desktop_novnc_port: object = None


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
    import ssl
    import base64 as b64
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
        action = msg.get("action", "")
        rel_path = msg.get("path", ".")

        # Token already validated at WS connect time — no per-command secret check

        if readonly and action in _WRITE_ACTIONS:
            return {"ok": False, "error": "Operation not allowed in readonly mode"}

        # Encryption ops (phase 5b/6) -- opt-in: only when the server sends one
        # of these new actions. A relay that never receives them is unaffected.
        try:
            from pawflow_relay import key_ops as _key_ops
        except Exception:
            _key_ops = None
        if _key_ops is not None and _key_ops.is_key_action(action):
            return _key_ops.handle(action, msg)

        if msg.get("local", False):
            if not allow_local:
                return {"ok": False, "error": "Local execution disabled. Start relay with --allow-local"}
            _host_helper = os.environ.get("PAWFLOW_HOST_HELPER", "")
            if not _host_helper:
                return {"ok": False, "error": "Local execution requested but host helper is unavailable"}
            _fwd = dict(msg)
            return _forward_to_host_helper(_host_helper, _fwd, ws_sock_ref[0], _ws_frame_send)

        abs_path = _resolve(rel_path)
        if abs_path is None:
            return {"ok": False, "error": f"Path traversal blocked: {rel_path}"}

        # Host-level action: per-CLI auth login (claude / codex / gemini).
        # If in Docker → forward to host helper; if native → run directly.
        # The 3 actions share the same dispatch shape: pick the matching
        # auth helper, stream URL via send_progress, return the credentials.
        if action in ("claude_auth_login", "codex_auth_login", "gemini_auth_login"):
            host_helper = os.environ.get("PAWFLOW_HOST_HELPER", "")
            if host_helper:
                return _forward_to_host_helper(host_helper, msg, ws_sock_ref[0], _ws_frame_send)
            else:
                def _send_progress(data):
                    if ws_sock_ref[0]:
                        progress = json.dumps({
                            "type": "progress",
                            "request_id": msg.get("request_id", ""),
                            "data": data,
                        }).encode("utf-8")
                        try:
                            _ws_frame_send(ws_sock_ref[0], progress)
                        except Exception:
                            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                _login_fn = {
                    "claude_auth_login": _claude_auth_login,
                    "codex_auth_login": _codex_auth_login,
                    "gemini_auth_login": _gemini_auth_login,
                }[action]
                try:
                    result = _login_fn(msg, send_progress=_send_progress)
                    if "error" in result:
                        return {"ok": False, "error": result["error"]}
                    return {"ok": True, "data": result}
                except Exception as e:
                    return {"ok": False, "error": str(e)}

        # Terminal actions (handled here, not in fs_actions)
        if action == "open_terminal":
            if not allow_exec:
                return {"ok": False, "error": "Exec not allowed"}
            try:
                _sid = _term_mgr.open(
                    cols=msg.get("cols", 80),
                    rows=msg.get("rows", 24),
                    shell=msg.get("shell"),  # nosec B604 - terminal tool intentionally opens requested shell.
                )
                return {"ok": True, "data": {"session_id": _sid}}
            except Exception as e:
                return {"ok": False, "error": f"Failed to open terminal: {e}"}

        if action == "close_terminal":
            _sid = msg.get("session_id", "")
            if not _sid:
                return {"ok": False, "error": "Missing session_id"}
            if _sid.startswith("local_term_"):
                _hh = os.environ.get("PAWFLOW_HOST_HELPER", "")
                if _hh:
                    return _forward_to_host_helper(_hh, msg, ws_sock_ref[0], _ws_frame_send)
            ok = _term_mgr.close(_sid)
            return {"ok": ok, "error": "" if ok else "Session not found"}

        if action == "write_terminal":
            _sid = msg.get("session_id", "")
            if _sid.startswith("local_term_"):
                _hh = os.environ.get("PAWFLOW_HOST_HELPER", "")
                if _hh:
                    return _forward_to_host_helper(_hh, msg, ws_sock_ref[0], _ws_frame_send)
            _ok, _err = _term_mgr.write(_sid, msg.get("data", ""))
            return {"ok": True} if _ok else {"ok": False, "error": _err}

        if action == "resize_terminal":
            _sid = msg.get("session_id", "")
            if _sid.startswith("local_term_"):
                _hh = os.environ.get("PAWFLOW_HOST_HELPER", "")
                if _hh:
                    return _forward_to_host_helper(_hh, msg, ws_sock_ref[0], _ws_frame_send)
            _ok, _err = _term_mgr.resize(_sid, cols=msg.get("cols", 80), rows=msg.get("rows", 24))
            return {"ok": True} if _ok else {"ok": False, "error": _err}

        if action == "list_terminals":
            return {"ok": True, "data": {"sessions": _term_mgr.list()}}

        if action == "http_proxy":
            if not allow_exec:
                return {"ok": False, "error": "Exec not allowed"}
            return _act_http_proxy(msg)

        if action == "start_code_server":
            if not allow_exec:
                return {"ok": False, "error": "Exec not allowed"}
            return _cs_start(_state, msg, root_dir)

        # -- Code-server WS tunnel --
        if action == "cs_ws_open":
            if not allow_exec:
                return {"ok": False, "error": "Exec not allowed"}

            def _cs_send(_frame):
                with _send_lock:
                    _ws_frame_send(ws_sock_ref[0], _frame)
            return _cs_ws_open(_state, msg, _cs_send)

        if action == "cs_ws_send":
            return _cs_ws_send(_state, msg)

        if action == "cs_ws_close":
            return _cs_ws_close(_state, msg)

        if action == "stop_code_server":
            return _cs_stop(_state)

        # ── Forward local screen/desktop to host helper if in Docker ────
        _explicitly_local = action in (
            "start_local_desktop", "stop_local_desktop", "local_screen_check",
            "open_local_terminal", "start_local_code_server")
        # NOTE: write_terminal/resize_terminal/close_terminal for local_term_*
        # are forwarded inline in the terminal action handlers above.
        _screen_with_flag = action.startswith("screen_") and msg.get("local", False)
        _host_helper = os.environ.get("PAWFLOW_HOST_HELPER", "")
        if (_explicitly_local or _screen_with_flag) and _host_helper:
            _fwd = dict(msg)
            return _forward_to_host_helper(_host_helper, _fwd, ws_sock_ref[0], _ws_frame_send)

        # ── Desktop VNC (singleton) ──
        if action == "start_desktop":
            if not allow_exec:
                return {"ok": False, "error": "Exec not allowed"}
            return _dt_start_desktop(_state, msg)

        if action == "stop_desktop":
            return _dt_stop_desktop(_state)

        if action == "desktop_status":
            return _dt_desktop_status(_state)

        # NOTE: local action forwarding is handled by the main dispatch
        # block at the top of _execute_command. No duplicate here.
        if action == "start_local_desktop":
            return _dt_start_local_desktop(_state, msg)

        if action == "stop_local_desktop":
            return _dt_stop_local_desktop(_state)

        if action == "local_screen_check":
            return _dt_local_screen_check(allow_local_screen)

        # ── Desktop VNC WS tunnel (same pattern as cs_ws_*) ────────────────
        if action == "desktop_ws_open":
            if not allow_exec:
                return {"ok": False, "error": "Exec not allowed"}

            def _dt_send(_frame):
                with _send_lock:
                    _ws_frame_send(ws_sock_ref[0], _frame)
            return _dt_ws_open(_state, msg, _dt_send)

        if action == "desktop_ws_send":
            return _dt_ws_send(_state, msg)

        if action == "desktop_ws_close":
            return _dt_ws_close(_state, msg)

        if action == "script_hash":
            return _act_script_hash()

        if action == "update_scripts":
            return _act_update_scripts(msg)

        # Note: permission checks are enforced server-side by ToolApprovalGate.
        # (local_screen forwarding handled earlier, before desktop handlers)

        # Generic local=True forward: any action with local=true runs on the
        # user's host (via PawCode CLI helper), not in this relay container.
        # This is the equivalent of "exec on host" for all tools — used by
        # http_fetch (LLM proxy) and any other tool that needs the user's
        # actual localhost / host network.
        #
        # STRICT: local=True is a contract, not a hint. If we can't honour
        # it, we MUST fail loud. The previous fallthrough silently ran the
        # action inside the relay container — which means
        # `http_fetch("http://localhost:8080/")` hit the container's
        # network namespace instead of the user's host. Repro: CC gets
        # HTTP 200 with an empty/malformed body (whatever happens to
        # listen on :8080 INSIDE the container, or an immediate EOF from
        # the in-container proxy), qwen on the user's host sees zero
        # requests, and the operator spends an afternoon hunting a ghost.
        # Fail explicitly so the error surfaces as "host helper
        # unavailable" rather than a misleading upstream error.
        if msg.get("local"):
            _hh = os.environ.get("PAWFLOW_HOST_HELPER", "")
            if not _hh:
                return {
                    "ok": False,
                    "error": (
                        "local=True requested but PAWFLOW_HOST_HELPER is "
                        "not configured on the relay container. "
                        "Host-forwarding is required for this action "
                        "(e.g. http_fetch to the user's localhost). "
                        "Restart the relay via the managed path so the "
                        "host-helper thread starts and the env var is "
                        "propagated."),
                }
            if not ws_sock_ref[0]:
                return {
                    "ok": False,
                    "error": (
                        "local=True requested but the relay's WS to the "
                        "server is not alive — cannot stream progress "
                        "back from the host helper."),
                }
            _fwd = dict(msg)
            return _forward_to_host_helper(
                _hh, _fwd, ws_sock_ref[0], _ws_frame_send)

        from fs_actions import ACTIONS as _FS_ACTIONS
        handler_func = _FS_ACTIONS.get(action)
        if not handler_func:
            return {"ok": False, "error": f"Unknown action: {action}"}

        try:
            if action in ("exec", "exec_stream"):
                result = handler_func(root_dir, abs_path, msg,
                                       allow_exec=getattr(mock, 'allow_exec', False),
                                       **({"on_output": on_output} if action == "exec_stream" and on_output else {}))
            elif action == "http_fetch":
                # http_fetch: stream chunks when the caller wired
                # on_output (LLM proxy, SSE relay), else run in sync
                # mode so the action returns {status, headers, body}
                # inline (Pixazo polling, generic GET).
                if on_output:
                    def _on_chunk(kind, data):
                        on_output(kind, data)
                    result = handler_func(root_dir, abs_path, msg,
                                           on_chunk=_on_chunk)
                else:
                    result = handler_func(root_dir, abs_path, msg)
            else:
                result = handler_func(root_dir, abs_path, msg)
            return {"ok": True, "data": result}
        except Exception as e:
            return {"ok": False, "error": str(e)}

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
    _server_fs_mount = None
    _filestore_fs_swap = None
    _skills_fs_swap = None
    if server_mount or filestore_mount or skills_mount:
        from pawflow_relay.server_fs_client import SwappableServerFsClient
        from pawflow_relay.server_fs_mount import CombinedServerFsMount
        # ONE pyfuse3 mount at /pawflow_fs serving both cc_sessions
        # (sfs.*) and filestore (ffs.*) subtrees. Required because
        # pyfuse3 keeps a single global session per process — two
        # separate mounts race on `pyfuse3.init()`, the second wins,
        # the first goes orphan, and any syscall on the orphan blocks
        # forever (no userspace daemon answers the kernel). The
        # canonical paths /cc_sessions and /filestore are restored
        # via `mount --bind` against the routed subtrees.
        _server_fs_swap = SwappableServerFsClient()
        _filestore_fs_swap = SwappableServerFsClient()
        _skills_fs_swap = SwappableServerFsClient()
        # Mountpoint under /tmp because it's tmpfs (always writable
        # by the relay user, no Dockerfile change required to grant
        # ownership). The bind-mounts that follow expose the canonical
        # /cc_sessions and /filestore paths so downstream consumers
        # don't see the temp location.
        _combined_root = "/tmp/pf_combined_fs"  # nosec B108 - relay-local FUSE mount root.
        try:
            _server_fs_mount = CombinedServerFsMount(
                _combined_root, _server_fs_swap, _filestore_fs_swap,
                _skills_fs_swap)
            _server_fs_mount.start()
            sys.stderr.write(
                f"[FSRelay] combined-fs mounted at {_combined_root}\n")
            # Expose each canonical path as a symlink to the routed
            # subtree of the combined FUSE mount. Symlinks rather than
            # `mount --bind` because they're cheaper and survive any
            # future restructuring; we route every filesystem op
            # through `sudo` because the canonical paths live in `/`
            # (root-owned) and pawflow can't rmdir entries there
            # without escalating privileges. The Dockerfile grants
            # pawflow NOPASSWD sudo precisely to enable this.
            _aliases = []
            if server_mount:
                _aliases.append((f"{_combined_root}/cc_sessions", server_mount))
            if filestore_mount:
                _aliases.append((f"{_combined_root}/filestore", filestore_mount))
            if skills_mount:
                _aliases.append((f"{_combined_root}/skills", skills_mount))

            def _sudo_run(argv: list, _what: str):
                _rc = subprocess.run(  # nosec B603
                    ["sudo", "-n"] + argv,
                    capture_output=True, text=True, timeout=5)
                if _rc.returncode != 0:
                    sys.stderr.write(
                        f"[FSRelay] {_what} FAILED rc={_rc.returncode} "
                        f"stdout={_rc.stdout.strip()!r} "
                        f"stderr={_rc.stderr.strip()!r}\n")
                return _rc.returncode == 0

            for _src, _dst in _aliases:
                try:
                    # Wipe whatever's at the canonical path (empty dir
                    # from the Dockerfile, leftover symlink from a
                    # previous run, …). `rm -rf` covers all cases.
                    if not _sudo_run(["rm", "-rf", _dst],
                                     f"sudo rm -rf {_dst}"):
                        continue
                    # Re-create the parent dir if `rm -rf` removed it
                    # (it shouldn't for top-level paths like /cc_sessions
                    # but be defensive).
                    _parent = os.path.dirname(_dst) or "/"
                    if not os.path.isdir(_parent):
                        _sudo_run(["mkdir", "-p", _parent],
                                  f"sudo mkdir -p {_parent}")
                    if not _sudo_run(["ln", "-s", _src, _dst],
                                     f"sudo ln -s {_src} {_dst}"):
                        continue
                    sys.stderr.write(
                        f"[FSRelay] symlinked {_dst} → {_src}\n")
                except Exception as _serr:
                    sys.stderr.write(
                        f"[FSRelay] symlink {_dst} → {_src} "
                        f"FAILED: {_serr}\n")
        except Exception as _smerr:
            import traceback as _tb
            _full_tb = _tb.format_exc()
            sys.stderr.write(
                f"[FSRelay] combined-fs mount FAILED: {_smerr}\n"
                "  Likely cause: missing pyfuse3 / libfuse3, or no "
                "CAP_SYS_ADMIN. Continuing without combined-fs.\n"
                f"  full traceback follows:\n{_full_tb}")
            # Also write the traceback into the FUSE trace file so
            # users diagnosing a mount failure don't have to dig
            # through relay.log to find the cause.
            try:
                from pawflow_relay.server_fs_mount import _fuse_trace_emit
                _fuse_trace_emit(
                    f"[FSRelay] combined-fs mount FAILED err={_smerr}\n"
                    f"--- traceback ---\n{_full_tb}--- end ---")
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            _server_fs_mount = None
            _server_fs_swap = None
            _filestore_fs_swap = None
            _skills_fs_swap = None

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
            sock = socket.create_connection((host, port), timeout=10)
            # TCP keepalive: detect dead connections at OS level
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            try:
                # Linux: start probing after 30s idle, every 10s, fail after 3 misses
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 30)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
            except (AttributeError, OSError):
                pass  # not available on all platforms
            if use_ssl:
                ctx = ssl.create_default_context()
                if os.environ.get('PAWFLOW_RELAY_INSECURE') == '1':
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                sock = ctx.wrap_socket(sock, server_hostname=host)

            ws_key = b64.b64encode(os.urandom(16)).decode()
            _cookies = []
            if gateway_cookie:
                _cookies.append(f'_pf_gw={gateway_cookie}')
            if session_token:
                _cookies.append(f'pawflow_token={session_token}')
            internal_token = os.environ.get('PAWFLOW_INTERNAL_TOKEN', '')
            if internal_token:
                _cookies.append(f'pawflow_internal={internal_token}')
            _extra_hdrs = ''
            if _cookies:
                _extra_hdrs = 'Cookie: ' + '; '.join(_cookies) + '\r\n'
            if gateway_key:
                _extra_hdrs += f'X-PawFlow-Gateway-Key: {gateway_key}\r\n'
            handshake = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                f"Upgrade: websocket\r\n"
                f"Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {ws_key}\r\n"
                f"Sec-WebSocket-Version: 13\r\n"
                f"{_extra_hdrs}"
                f"\r\n"
            )
            sock.sendall(handshake.encode())

            resp = b""
            while b"\r\n\r\n" not in resp:
                chunk = sock.recv(4096)
                if not chunk:
                    raise ConnectionError("Handshake failed")
                resp += chunk

            if b"101" not in resp.split(b"\r\n")[0]:
                _status_line = resp.split(b"\r\n")[0]
                raise ConnectionError(f"Handshake failed: {_status_line}")

            # Any bytes after \r\n\r\n are the start of the first WS frame
            # — push them back into the socket buffer via a wrapper
            _header_end = resp.index(b"\r\n\r\n") + 4
            _leftover = resp[_header_end:]
            if _leftover:
                _orig_recv = sock.recv
                _buf = [_leftover]
                def _patched_recv(n, _flags=0):
                    if _buf:
                        data = _buf.pop(0)
                        return data[:n]  # may need to re-buffer if data > n
                    return _orig_recv(n)
                sock.recv = _patched_recv

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
            _child_relays = {}  # relay_id → thread (child relay instances)

            def _term_send(_frame):
                # PTY reader threads stream output here; take the shared
                # send lock before writing to the (SSL) socket — concurrent
                # writes interleave mid-record and corrupt the stream.
                with _send_lock:
                    _ws_frame_send(sock, _frame)
            # Recreated per (re)connection so PTY sessions never outlive
            # their socket (mirrors the old per-connection _terminal_sessions).
            _term_mgr = TerminalManager(root_dir, _term_send)
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
                    # Server asks us to create a child relay for a different root
                    _sr_root = msg.get("root", "")
                    _sr_id = msg.get("relay_id", "")
                    _sr_token = msg.get("token", token)
                    _sr_secret = msg.get("secret", secret)
                    _sr_rid = msg.get("request_id", "")
                    if not _sr_root or not os.path.isdir(_sr_root):
                        _resp = json.dumps({"type": "result", "request_id": _sr_rid,
                            "data": {"ok": False, "error": f"Directory not found: {_sr_root}"}}).encode("utf-8")
                        with _send_lock:
                            _ws_frame_send(sock, _resp)
                    else:
                        sys.stderr.write(f"[FSRelay] Spawning child relay: {_sr_id} -> {_sr_root}\n")
                        # If parent uses Docker, child starts its own container
                        _parent_docker = globals().get('_DOCKER_EXEC_CONTAINER') or \
                                         getattr(__import__('fs_actions'), '_DOCKER_EXEC_CONTAINER', None) \
                                         if 'fs_actions' in sys.modules else None
                        _child_docker_image = msg.get("docker_image", "")

                        _docker_cpus = getattr(globals().get('args', None), 'docker_cpus', '2')
                        _docker_memory = getattr(globals().get('args', None), 'docker_memory', '4g')
                        def _child_relay(_url, _tok, _sec, _rid, _root,
                                         _docker_img="", _parent_has_docker=False,
                                         _cpus=_docker_cpus, _mem=_docker_memory):
                            _child_container = None
                            try:
                                # Start child Docker container if parent uses Docker
                                if _docker_img or _parent_has_docker:
                                    import uuid as _uuid_child
                                    _img = _docker_img or "pawflow-relay-dev:latest"
                                    _child_container = f"pawflow-relay-child-{_uuid_child.uuid4().hex[:8]}"
                                    _dr = subprocess.run(_docker_cmd() + [  # nosec B603
                                        "run", "-d",
                                        "--name", _child_container,
                                        "--init",
                                        "-v", f"{_translate_path(_to_host_path(_root))}:/workspace",
                                        "-w", "/workspace",
                                        "--cpus", _cpus, "--memory", _mem,
                                        "--security-opt", "no-new-privileges",
                                        _img, "tail", "-f", "/dev/null",
                                    ], capture_output=True, text=True)
                                    if _dr.returncode == 0:
                                        # Register container for this root dir
                                        import fs_actions as _fsa
                                        if not hasattr(_fsa, '_DOCKER_CONTAINERS'):
                                            _fsa._DOCKER_CONTAINERS = {}
                                        _fsa._DOCKER_CONTAINERS[str(Path(_root).resolve())] = _child_container
                                        sys.stderr.write(f"[FSRelay] Child container: {_child_container}\n")
                                    else:
                                        _child_container = None
                                _ws_connect(_url, _tok, _sec, _rid, _root,
                                            readonly=readonly, allow_exec=allow_exec,
                                            allow_automation=allow_automation,
                                            allow_local_screen=allow_local_screen,
                                            allow_local=allow_local)
                            except Exception as _ce:
                                sys.stderr.write(f"[FSRelay] Child {_rid} died: {_ce}\n")
                            finally:
                                if _child_container:
                                    # Unregister from dict
                                    try:
                                        import fs_actions as _fsa2
                                        _fsa2._DOCKER_CONTAINERS.pop(str(Path(_root).resolve()), None)
                                    except Exception:
                                        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                                    try:
                                        subprocess.run(_docker_cmd() + ["rm", "-f", _child_container],  # nosec B603
                                                       capture_output=True, timeout=10)
                                    except Exception:
                                        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                        _child_thread = _threading.Thread(
                            target=_child_relay,
                            args=(url, _sr_token, _sr_secret, _sr_id, _sr_root,
                                  _child_docker_image, bool(_parent_docker)),
                            daemon=True, name=f"relay-child-{_sr_id}")
                        _child_thread.start()
                        _child_relays[_sr_id] = _child_thread
                        _resp = json.dumps({"type": "result", "request_id": _sr_rid,
                            "data": {"ok": True, "relay_id": _sr_id, "root": _sr_root}}).encode("utf-8")
                        with _send_lock:
                            _ws_frame_send(sock, _resp)

                elif msg.get("type") == "stop_relay":
                    # Server asks us to stop a child relay
                    _stop_id = msg.get("relay_id", "")
                    _stop_rid = msg.get("request_id", "")
                    _child = _child_relays.pop(_stop_id, None)
                    # Child relays run _ws_connect which reconnects forever.
                    # Signal them to stop by removing from tracking.
                    # The child will die on next reconnect failure or be cleaned up.
                    sys.stderr.write(f"[FSRelay] Stopping child relay: {_stop_id}\n")
                    _resp = json.dumps({"type": "result", "request_id": _stop_rid,
                        "data": {"ok": True, "stopped": _stop_id}}).encode("utf-8")
                    with _send_lock:
                        _ws_frame_send(sock, _resp)

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





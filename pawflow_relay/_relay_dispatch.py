"""Per-message action dispatcher for the relay worker.

Extracted verbatim from _ws_connect._execute_command. The connection-scoped
values it used to close over (process state, the terminal manager, the send
lock, the live socket ref, the path resolver, host-forward helper, and the
--allow-* flags) are carried in a DispatchCtx and rebound to their original
local names at the top of execute_command, so the dispatch body is identical
to the in-closure version.
"""
import json
import logging
import os
from dataclasses import dataclass

from pawflow_relay.auth import (
    claude_auth_login as _claude_auth_login,
    codex_auth_login as _codex_auth_login,
    gemini_auth_login as _gemini_auth_login,
)
from pawflow_relay._relay_codeserver import (
    start_code_server as _cs_start,
    stop_code_server as _cs_stop,
    cs_ws_open as _cs_ws_open,
    cs_ws_send as _cs_ws_send,
    cs_ws_close as _cs_ws_close,
)
from pawflow_relay._relay_desktop import (
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
from pawflow_relay._relay_actions import (
    http_proxy as _act_http_proxy,
    script_hash as _act_script_hash,
    update_scripts as _act_update_scripts,
)

# Actions refused in readonly mode (mirrors the relay HTTP write set).
_WRITE_ACTIONS = frozenset({
    "write_file", "delete_file", "mkdir", "find_replace", "edit", "exec",
})


@dataclass
class DispatchCtx:
    """Connection-scoped dependencies for execute_command."""
    state: object
    term_mgr: object
    send_lock: object
    ws_sock_ref: object
    ws_frame_send: object
    resolve: object
    forward_to_host_helper: object
    root_dir: str
    readonly: bool
    allow_exec: bool
    allow_local: bool
    allow_local_screen: bool
    allow_automation: bool


def execute_command(ctx, msg, on_output=None):
    _state = ctx.state
    _term_mgr = ctx.term_mgr
    _send_lock = ctx.send_lock
    ws_sock_ref = ctx.ws_sock_ref
    _ws_frame_send = ctx.ws_frame_send
    _resolve = ctx.resolve
    _forward_to_host_helper = ctx.forward_to_host_helper
    root_dir = ctx.root_dir
    readonly = ctx.readonly
    allow_exec = ctx.allow_exec
    allow_local = ctx.allow_local
    allow_local_screen = ctx.allow_local_screen
    allow_automation = ctx.allow_automation
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
                                   allow_exec=allow_exec,
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

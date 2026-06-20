"""Per-message action dispatcher for the relay worker.

The ordered gates (readonly check, key-ops opt-in, local-forward, path
resolution, the host-auth logins, the explicitly-local forward, and the
fs_actions fallback) stay imperative because their RELATIVE ORDER carries
meaning. The order-independent "pure" actions (terminal, code-server, desktop
VNC + their WS tunnels, script sync) are routed through the _DISPATCH table.

This is order-preserving: none of the table actions are in the
``_EXPLICITLY_LOCAL`` set nor start with ``screen_``, so consulting the table
right after path resolution (before the explicitly-local forward block) is
equivalent to their previous positions in the if/elif chain. The local=True
forward at the top already returns for every local-flagged message, so the
table is only ever reached for non-local actions.

Connection-scoped dependencies (process/session state, the terminal manager,
send lock, live socket ref, path resolver, host-forward helper, --allow-*
flags) are carried in a DispatchCtx and read directly as ``ctx.<field>``.
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

# Host-level actions forwarded to the host helper when running in Docker.
_EXPLICITLY_LOCAL = (
    "start_local_desktop", "stop_local_desktop", "local_screen_check",
    "open_local_terminal", "start_local_code_server")

_EXEC_DENIED = {"ok": False, "error": "Exec not allowed"}


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


def _forward(ctx, msg):
    """Forward a message to the host helper over the live socket."""
    _host_helper = os.environ.get("PAWFLOW_HOST_HELPER", "")
    return ctx.forward_to_host_helper(
        _host_helper, dict(msg), ctx.ws_sock_ref[0], ctx.ws_frame_send)


def _fwd_local_term(ctx, msg):
    """Forward a local_term_* terminal op to the host helper, or None.

    Returns the forward result when a host helper is configured (the terminal
    lives on the user's host), else None so the caller falls through to the
    in-relay terminal manager.
    """
    if os.environ.get("PAWFLOW_HOST_HELPER", ""):
        return _forward(ctx, msg)
    return None


# ── Pure action handlers (order-independent; routed via _DISPATCH) ──

def _h_open_terminal(ctx, msg, on_output=None):
    if not ctx.allow_exec:
        return _EXEC_DENIED
    try:
        _sid = ctx.term_mgr.open(
            cols=msg.get("cols", 80),
            rows=msg.get("rows", 24),
            shell=msg.get("shell"),  # nosec B604 - terminal tool intentionally opens requested shell.
        )
        return {"ok": True, "data": {"session_id": _sid}}
    except Exception as e:
        return {"ok": False, "error": f"Failed to open terminal: {e}"}


def _h_close_terminal(ctx, msg, on_output=None):
    _sid = msg.get("session_id", "")
    if not _sid:
        return {"ok": False, "error": "Missing session_id"}
    if _sid.startswith("local_term_"):
        _fwd = _fwd_local_term(ctx, msg)
        if _fwd is not None:
            return _fwd
    ok = ctx.term_mgr.close(_sid)
    return {"ok": ok, "error": "" if ok else "Session not found"}


def _h_write_terminal(ctx, msg, on_output=None):
    _sid = msg.get("session_id", "")
    if _sid.startswith("local_term_"):
        _fwd = _fwd_local_term(ctx, msg)
        if _fwd is not None:
            return _fwd
    _ok, _err = ctx.term_mgr.write(_sid, msg.get("data", ""))
    return {"ok": True} if _ok else {"ok": False, "error": _err}


def _h_resize_terminal(ctx, msg, on_output=None):
    _sid = msg.get("session_id", "")
    if _sid.startswith("local_term_"):
        _fwd = _fwd_local_term(ctx, msg)
        if _fwd is not None:
            return _fwd
    _ok, _err = ctx.term_mgr.resize(
        _sid, cols=msg.get("cols", 80), rows=msg.get("rows", 24))
    return {"ok": True} if _ok else {"ok": False, "error": _err}


def _h_list_terminals(ctx, msg, on_output=None):
    return {"ok": True, "data": {"sessions": ctx.term_mgr.list()}}


def _h_http_proxy(ctx, msg, on_output=None):
    if not ctx.allow_exec:
        return _EXEC_DENIED
    return _act_http_proxy(msg)


def _h_start_code_server(ctx, msg, on_output=None):
    if not ctx.allow_exec:
        return _EXEC_DENIED
    return _cs_start(ctx.state, msg, ctx.root_dir)


def _h_cs_ws_open(ctx, msg, on_output=None):
    if not ctx.allow_exec:
        return _EXEC_DENIED

    def _cs_send(_frame):
        with ctx.send_lock:
            ctx.ws_frame_send(ctx.ws_sock_ref[0], _frame)
    return _cs_ws_open(ctx.state, msg, _cs_send)


def _h_cs_ws_send(ctx, msg, on_output=None):
    return _cs_ws_send(ctx.state, msg)


def _h_cs_ws_close(ctx, msg, on_output=None):
    return _cs_ws_close(ctx.state, msg)


def _h_stop_code_server(ctx, msg, on_output=None):
    return _cs_stop(ctx.state)


def _h_start_desktop(ctx, msg, on_output=None):
    if not ctx.allow_exec:
        return _EXEC_DENIED
    return _dt_start_desktop(ctx.state, msg)


def _h_stop_desktop(ctx, msg, on_output=None):
    return _dt_stop_desktop(ctx.state)


def _h_desktop_status(ctx, msg, on_output=None):
    return _dt_desktop_status(ctx.state)


def _h_desktop_ws_open(ctx, msg, on_output=None):
    if not ctx.allow_exec:
        return _EXEC_DENIED

    def _dt_send(_frame):
        with ctx.send_lock:
            ctx.ws_frame_send(ctx.ws_sock_ref[0], _frame)
    return _dt_ws_open(ctx.state, msg, _dt_send)


def _h_desktop_ws_send(ctx, msg, on_output=None):
    return _dt_ws_send(ctx.state, msg)


def _h_desktop_ws_close(ctx, msg, on_output=None):
    return _dt_ws_close(ctx.state, msg)


def _h_script_hash(ctx, msg, on_output=None):
    return _act_script_hash()


def _h_update_scripts(ctx, msg, on_output=None):
    return _act_update_scripts(msg)


_DISPATCH = {
    "open_terminal": _h_open_terminal,
    "close_terminal": _h_close_terminal,
    "write_terminal": _h_write_terminal,
    "resize_terminal": _h_resize_terminal,
    "list_terminals": _h_list_terminals,
    "http_proxy": _h_http_proxy,
    "start_code_server": _h_start_code_server,
    "cs_ws_open": _h_cs_ws_open,
    "cs_ws_send": _h_cs_ws_send,
    "cs_ws_close": _h_cs_ws_close,
    "stop_code_server": _h_stop_code_server,
    "start_desktop": _h_start_desktop,
    "stop_desktop": _h_stop_desktop,
    "desktop_status": _h_desktop_status,
    "desktop_ws_open": _h_desktop_ws_open,
    "desktop_ws_send": _h_desktop_ws_send,
    "desktop_ws_close": _h_desktop_ws_close,
    "script_hash": _h_script_hash,
    "update_scripts": _h_update_scripts,
}


def _handle_auth_login(ctx, msg, action):
    """Per-CLI auth login (claude/codex/gemini): forward in Docker, else run."""
    host_helper = os.environ.get("PAWFLOW_HOST_HELPER", "")
    if host_helper:
        return ctx.forward_to_host_helper(
            host_helper, msg, ctx.ws_sock_ref[0], ctx.ws_frame_send)

    def _send_progress(data):
        if ctx.ws_sock_ref[0]:
            progress = json.dumps({
                "type": "progress",
                "request_id": msg.get("request_id", ""),
                "data": data,
            }).encode("utf-8")
            try:
                ctx.ws_frame_send(ctx.ws_sock_ref[0], progress)
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


def execute_command(ctx, msg, on_output=None):
    action = msg.get("action", "")
    rel_path = msg.get("path", ".")

    # Token already validated at WS connect time — no per-command secret check

    if ctx.readonly and action in _WRITE_ACTIONS:
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
        if not ctx.allow_local:
            return {"ok": False, "error": "Local execution disabled. Start relay with --allow-local"}
        _host_helper = os.environ.get("PAWFLOW_HOST_HELPER", "")
        if not _host_helper:
            return {"ok": False, "error": "Local execution requested but host helper is unavailable"}
        return _forward(ctx, msg)

    abs_path = ctx.resolve(rel_path)
    if abs_path is None:
        return {"ok": False, "error": f"Path traversal blocked: {rel_path}"}

    # Host-level action: per-CLI auth login (claude / codex / gemini).
    if action in ("claude_auth_login", "codex_auth_login", "gemini_auth_login"):
        return _handle_auth_login(ctx, msg, action)

    # Order-independent pure actions (terminal, code-server, desktop VNC, WS
    # tunnels, script sync). Safe to consult here: none are in
    # _EXPLICITLY_LOCAL nor start with "screen_", and local-flagged messages
    # already returned above.
    _handler = _DISPATCH.get(action)
    if _handler is not None:
        return _handler(ctx, msg, on_output)

    # ── Forward local screen/desktop to host helper if in Docker ────
    _explicitly_local = action in _EXPLICITLY_LOCAL
    _screen_with_flag = action.startswith("screen_") and msg.get("local", False)
    _host_helper = os.environ.get("PAWFLOW_HOST_HELPER", "")
    if (_explicitly_local or _screen_with_flag) and _host_helper:
        return _forward(ctx, msg)

    # Local desktop (host screen) — reached only when no host helper is set,
    # so the action runs in this process. Kept after the forward block above.
    if action == "start_local_desktop":
        return _dt_start_local_desktop(ctx.state, msg)
    if action == "stop_local_desktop":
        return _dt_stop_local_desktop(ctx.state)
    if action == "local_screen_check":
        return _dt_local_screen_check(ctx.allow_local_screen)

    # Generic local=True forward: any action with local=true runs on the
    # user's host (via PawCode CLI helper), not in this relay container.
    # STRICT: local=True is a contract — fail loud rather than silently
    # running inside the relay container's network namespace. (In practice
    # the local=True gate at the top already returned, so this is a defensive
    # backstop that documents the contract.)
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
        if not ctx.ws_sock_ref[0]:
            return {
                "ok": False,
                "error": (
                    "local=True requested but the relay's WS to the "
                    "server is not alive — cannot stream progress "
                    "back from the host helper."),
            }
        return _forward(ctx, msg)

    from fs_actions import ACTIONS as _FS_ACTIONS
    handler_func = _FS_ACTIONS.get(action)
    if not handler_func:
        return {"ok": False, "error": f"Unknown action: {action}"}

    try:
        if action in ("exec", "exec_stream"):
            result = handler_func(ctx.root_dir, abs_path, msg,
                                   allow_exec=ctx.allow_exec,
                                   **({"on_output": on_output} if action == "exec_stream" and on_output else {}))
        elif action == "http_fetch":
            # http_fetch: stream chunks when the caller wired on_output (LLM
            # proxy, SSE relay), else run in sync mode so the action returns
            # {status, headers, body} inline (Pixazo polling, generic GET).
            if on_output:
                def _on_chunk(kind, data):
                    on_output(kind, data)
                result = handler_func(ctx.root_dir, abs_path, msg,
                                       on_chunk=_on_chunk)
            else:
                result = handler_func(ctx.root_dir, abs_path, msg)
        else:
            result = handler_func(ctx.root_dir, abs_path, msg)
        return {"ok": True, "data": result}
    except Exception as e:
        return {"ok": False, "error": str(e)}

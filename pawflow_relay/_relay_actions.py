"""Leaf relay-worker actions extracted from the _ws_connect dispatcher.

Self-contained handlers that need none of the connection's socket state:
  - http_proxy: proxy one HTTP request to a localhost port (code-server / UI).
  - script_hash / update_scripts: the relay script self-sync mechanism.

The script dir is resolved from this module's location exactly as before:
`dirname(dirname(__file__))` = the parent of the pawflow_relay package
(/opt/pawflow), where the relay scripts live. This module sits in the
same package as worker.py, so the resolution is unchanged by the move.
"""
import base64
import hashlib
import importlib
import logging
import os
import sys

_log = logging.getLogger(__name__)

# Relay scripts kept in sync with the server (bind-mounted read-only in
# dev, written in legacy sync setups). Order is irrelevant to the hash.
_RELAY_SCRIPTS = [
    "pawflow_relay_launcher.py", "fs_actions.py",
    "_fs_paths.py", "_fs_read.py", "_fs_grep.py",
    "_fs_edit.py", "fs_exec.py",
    "fs_screen.py", "fs_mcp.py", "fs_common.py",
]


def _script_dir():
    # Scripts live one level up from the pawflow_relay package dir
    # (/opt/pawflow/), not inside it.
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def http_proxy(msg, on_output=None):
    """Proxy one HTTP request without retaining its response in memory.

    Response metadata and body chunks are emitted through ``on_output`` using
    the same start/chunk/end protocol as ``http_fetch``. Inline mode is
    deliberately rejected: a JSON/base64 response would necessarily buffer
    the complete upstream body and multiplies its peak memory cost.
    """
    import http.client
    _target_port = msg.get("port", 0)
    _method = msg.get("method", "GET")
    _req_path = msg.get("req_path", "/")
    _req_headers = msg.get("req_headers", {})
    _req_body = msg.get("req_body", "")  # base64
    if not _target_port:
        return {"ok": False, "error": "Missing port"}
    if on_output is None:
        return {
            "ok": False,
            "error": "http_proxy requires the streaming response transport",
        }
    conn = None
    try:
        _timeout = int(msg.get("timeout") or 300)
        conn = http.client.HTTPConnection(
            "127.0.0.1", _target_port, timeout=_timeout)
        _body_bytes = base64.b64decode(_req_body) if _req_body else None
        conn.request(_method, _req_path, body=_body_bytes, headers=_req_headers)
        resp = conn.getresponse()
        _resp_headers = dict(resp.getheaders())
        on_output("start", {
            "status": resp.status,
            "reason": resp.reason,
            "headers": _resp_headers,
        })
        while True:
            _chunk = resp.read(64 * 1024)
            if not _chunk:
                break
            on_output("chunk", base64.b64encode(_chunk).decode("ascii"))
        on_output("end", None)
        return {"ok": True, "data": {
            "status": resp.status,
            "reason": resp.reason,
            "headers": _resp_headers,
        }}
    except Exception as e:
        return {"ok": False, "error": f"Proxy error: {e}"}
    finally:
        if conn is not None:
            conn.close()


def script_hash():
    """Return a short hash of the current relay scripts for version checks."""
    _dir = _script_dir()
    _h = hashlib.sha256()
    for _sf in _RELAY_SCRIPTS:
        _sp = os.path.join(_dir, _sf)
        if os.path.exists(_sp):
            with open(_sp, "rb") as _f:
                _h.update(_f.read())
    return {"ok": True, "data": {"hash": _h.hexdigest()[:16]}}


def update_scripts(msg):
    """Receive updated relay scripts, write them to the script dir, hot-reload.

    Read-only bind mounts (EROFS) are not an error: the mount itself is the
    update, so skip silently unless the content actually differs.
    """
    _scripts = msg.get("scripts", {})
    _new_hash = msg.get("script_hash", "")
    if not _scripts:
        return {"ok": False, "error": "No scripts provided"}
    _dir = _script_dir()
    _updated = []
    _readonly_skipped = []
    for _fname, _content_b64 in _scripts.items():
        if _fname not in _RELAY_SCRIPTS:
            continue  # Only accept known relay files
        _dst = os.path.join(_dir, _fname)
        _data = base64.b64decode(_content_b64)
        try:
            with open(_dst, "wb") as _f:
                _f.write(_data)
            _updated.append(_fname)
        except OSError as _e:
            # EROFS (errno 30): file is bind-mounted read-only from the host
            # in dev setups. The mount IS the "update" — host edits are
            # already visible. Skip silently instead of failing the sync.
            if getattr(_e, "errno", 0) == 30:
                try:
                    with open(_dst, "rb") as _f:
                        _current = _f.read()
                except OSError:
                    _current = None
                if _current != _data:
                    _readonly_skipped.append(_fname)
            else:
                raise
    # Hot-reload importable modules (not the launcher itself)
    for _mod_name in ["fs_common", "fs_actions", "fs_exec", "fs_screen", "fs_mcp"]:
        if f"{_mod_name}.py" in _updated and _mod_name in sys.modules:
            try:
                importlib.reload(sys.modules[_mod_name])
            except Exception as _e:
                sys.stderr.write(f"[FSRelay] Failed to reload {_mod_name}: {_e}\n")
    _needs_restart = "pawflow_relay_launcher.py" in _updated
    if _updated or _readonly_skipped:
        sys.stderr.write(
            f"[FSRelay] Scripts updated={_updated} "
            f"readonly_skipped={_readonly_skipped} hash={_new_hash}"
            f"{' (restart needed)' if _needs_restart else ''}\n")
    return {"ok": True, "data": {
        "updated": _updated,
        "readonly_skipped": _readonly_skipped,
        "needs_restart": _needs_restart}}

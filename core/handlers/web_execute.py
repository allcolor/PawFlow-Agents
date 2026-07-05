"""ExecuteScriptHandler — extracted from web_fetch.py to keep files <=800 lines.

Re-exported from core.handlers.web_fetch for import stability.
"""

import logging
import threading
from typing import Any, Dict

from core.tool_handler import ToolHandler

logger = logging.getLogger(__name__)


class ExecuteScriptHandler(ToolHandler):
    """Execute a Python expression or short script and return the result.

    File I/O is sandboxed through a virtual filesystem backed by FileStore.
    Uses the unified sandbox from core.sandbox.
    """

    _base_url: str = "http://localhost:9090"
    _vfs: Dict[str, bytes]

    _conversation_id: str = ""

    def __init__(self):
        self._vfs = {}
        self._vfs_lock = threading.Lock()
        self._fs_resolver = None

    def set_base_url(self, base_url: str):
        self._base_url = base_url.rstrip("/")

    def set_conversation_id(self, conversation_id: str):
        self._conversation_id = conversation_id or ""

    def set_fs_resolver(self, resolver):
        """Set filesystem service resolver: (service_id) -> service instance."""
        self._fs_resolver = resolver

    @property
    def name(self) -> str:
        return "execute_script"

    _user_id: str = ""

    @property
    def description(self) -> str:
        return (
            "Execute Python code and return the result.\n\n"
            "Execution target (auto-detected):\n"
            "- If a relay is connected, code runs on the user's machine via the relay. "
            "This gives full access to the user's Python environment, installed packages, "
            "and filesystem.\n"
            "- If no relay is connected, code runs in a server-side sandbox with restricted "
            "imports. Force sandbox mode with destination='sandbox'.\n"
            "- You can specify a relay service name in destination to target a specific machine.\n\n"
            "Getting output:\n"
            "- Use print() to produce output — all printed text is captured and returned.\n"
            "- Set a variable named 'result' and its value will be returned (sandbox mode only).\n"
            "- If neither print() nor result is used, you get 'Script executed (no output)'.\n\n"
            "File I/O (sandbox mode):\n"
            "- open('filestore://name.zip', 'wb') — creates a downloadable file in FileStore.\n"
            "- open('fs://service_name/path', 'rb'/'wb') — reads/writes via a filesystem service.\n\n"
            "Key parameters:\n"
            "- code (required): Python code to execute. Can be a single expression ('2+2') "
            "or a full script with multiple statements.\n"
            "- destination: 'sandbox' (force server sandbox), a relay service name, or omit "
            "for auto-detection.\n"
            "- max_output: Max output characters (default 4000). Larger outputs are "
            "auto-saved to FileStore and a download link is returned.\n\n"
            "Available in sandbox: math, json, re, csv, datetime, collections, itertools, "
            "functools, statistics, zipfile, pathlib, textwrap, html, base64, hashlib, "
            "urllib.parse, and more. No network access or os/subprocess in sandbox mode."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": (
                        "Python code to execute. Can be an expression ('2+2') "
                        "or statements. Use 'result' variable for output."
                    ),
                },
                "destination": {
                    "type": "string",
                    "description": (
                        "Where to execute: auto (default — relay if connected, else sandbox), "
                        "'sandbox' (force server sandbox), or relay service name"
                    ),
                },
                "max_output": {
                    "type": "integer",
                    "description": "Max output chars (default: 4000). Large outputs are auto-saved to FileStore.",
                },
            },
            "required": ["code"],
        }

    def set_user_id(self, uid: str):
        self._user_id = uid

    def execute(self, arguments: Dict[str, Any]) -> str:
        code = arguments.get("code", "")
        destination = arguments.get("destination", "")
        if not code:
            return "Error: no code provided"

        _secret_env = arguments.get("_secret_env") or {}

        # Explicit relay service name → execute remote
        _dest_raw = destination.strip()
        _dest_kind = _dest_raw.lower()
        if _dest_raw and _dest_kind not in ("server", "sandbox", "local", ""):
            return self._execute_remote(code, _dest_raw, env=_secret_env)

        # Explicit sandbox request
        if _dest_kind in ("server", "sandbox"):
            return self._execute_sandbox(code, env=_secret_env)

        # Auto-detect: if a relay is connected, use it; else sandbox
        _relay_svc = self._find_default_relay()
        if _relay_svc:
            _svc_id = getattr(_relay_svc, '_service_id', '') or getattr(_relay_svc, 'name', '')
            if _svc_id:
                return self._execute_remote(code, _svc_id, env=_secret_env)

        # Fallback: server sandbox
        return self._execute_sandbox(code, env=_secret_env)

    def _execute_sandbox(self, code: str, env: dict = None) -> str:
        """Execute in server-side sandbox."""
        from core.sandbox import execute_sandboxed
        # Inject env vars as globals accessible via os.environ in sandbox
        # (sandbox doesn't allow os, so inject as pre-defined variables)
        _env_prefix = ""
        if env:
            for k, v in env.items():
                _env_prefix += f"{k} = {repr(v)}\n"
            code = _env_prefix + code
        try:
            with self._vfs_lock:
                output, created_files, _ = execute_sandboxed(
                    code,
                    base_url=self._base_url,
                    vfs=self._vfs,
                    fs_resolver=self._fs_resolver,
                    user_id=self._user_id,
                    conversation_id=self._conversation_id,
                )
        except Exception as e:
            return f"Error: {e}"

        if not output:
            output = "Script executed (no 'result' variable set)"

        if created_files:
            output += "\n\nFiles created:\n"
            for url in created_files:
                import re as _re_fid
                _m = _re_fid.search(r'/files/([a-f0-9]+)', url)
                _fid = _m.group(1) if _m else ""
                output += f"- {url}" + (f" (file_id: {_fid})" if _fid else "") + "\n"
        return output

    def _find_default_relay(self):
        """Find the default relay service (same resolution as bash/fs tools)."""
        if self._fs_resolver:
            try:
                svc = self._fs_resolver("")  # empty = auto-detect default
                if svc and hasattr(svc, 'exec'):
                    return svc
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        try:
            from core.handlers._fs_base import find_fs_service
            svc = find_fs_service(self._user_id,
                                  conversation_id=self._conversation_id)
            if svc and hasattr(svc, 'exec'):
                return svc
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        return None

    def _execute_remote(self, code: str, service_name: str, env: dict = None) -> str:
        """Execute code on a remote filesystem service via relay."""
        # Inject PawFlow SDK env vars so scripts can use `from pawflow import tools`
        from core.handlers._fs_base import get_tool_relay_env
        _sdk_env = get_tool_relay_env()
        if _sdk_env:
            env = {**_sdk_env, **(env or {})}
        svc_name = service_name.replace("fs:", "", 1) if service_name.startswith("fs:") else service_name
        svc = None
        if self._fs_resolver:
            svc = self._fs_resolver(svc_name)
        if not svc:
            try:
                from core.service_registry import ServiceRegistry
                svc = ServiceRegistry.get_instance().resolve(
                    svc_name, user_id=self._user_id,
                    conv_id=self._conversation_id)
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        if not svc:
            return f"Error: filesystem service '{svc_name}' not found"
        try:
            if hasattr(svc, 'exec'):
                # Write to temp file then execute (avoids shell escaping issues)
                import os
                import tempfile
                import uuid as _uuid_exec
                _exec_id = _uuid_exec.uuid4().hex[:8]
                _fname = f".pawflow_exec_{_exec_id}.py"
                _exec_env = dict(env or {})
                _exec_env.setdefault(
                    "PAWFLOW_DATA_DIR",
                    os.path.join(tempfile.gettempdir(), "pawflow-exec-data", _exec_id))
                svc.write_file(_fname, code.encode("utf-8"))
                try:
                    result = svc.exec(".", f"python3 {_fname}", env=_exec_env)
                finally:
                    try:
                        svc.delete_file(_fname)
                    except Exception:
                        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            else:
                result = svc.execute_command({
                    "action": "exec",
                    "command": f"python -c {repr(code)}",
                })
            if isinstance(result, dict):
                stdout = result.get("stdout", "")
                stderr = result.get("stderr", "")
                output = stdout
                if stderr:
                    output += f"\nSTDERR: {stderr}"
                exit_code = result.get("exit_code", 0)
                if exit_code:
                    output += f"\n(exit code: {exit_code})"
                return output or "Script executed (no output)"
            return str(result)
        except Exception as e:
            return f"Error executing on '{svc_name}': {e}"



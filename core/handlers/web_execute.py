"""ExecuteScriptHandler — extracted from web_fetch.py to keep files <=800 lines.

Re-exported from core.handlers.web_fetch for import stability.
"""

import logging
import threading
from typing import Any, Dict

from core.tool_handler import ToolHandler

logger = logging.getLogger(__name__)

# Persistent kernels (docker/pawflow_sdk/pawflow_kernel.py): idle timeout an
# agent may choose per call, within a hard ceiling.
KERNEL_DEFAULT_IDLE_MINUTES = 30
KERNEL_MAX_IDLE_MINUTES = 8 * 60


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
    _agent_name: str = ""

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
            "Calling PawFlow tools from the script (relay mode, including local=true):\n"
            "- `from pawflow import tools, fs`; `tools.call('grep', pattern='TODO', path='src')` "
            "returns the tool's text result; `tools.get_schema(name)` gives its parameters; "
            "`fs.read_file`, `fs.write_file`, `fs.exec`, `fs.grep`, ... wrap the matching tools.\n"
            "- Use it when a task needs many tool calls plus processing (a grep then a "
            "read of each hit, a check across dozens of files): one script does every "
            "call, filters in Python, and prints only the summary. Intermediate results "
            "never enter your context; only what the script prints does.\n"
            "- Each call runs as you, with the usual approvals and limits, and shows in "
            "the chat as a Script tool row. A failed or refused call raises "
            "RuntimeError('Tool error: ...'). Tools that ask the user, plan or schedule "
            "your turn (ask_user, request_confirmation, schedule_continuation, plan mode) "
            "and execute_script itself cannot be called from a script.\n"
            "- A tool result reaches the script in full up to 2,000,000 characters "
            "(beyond that, a FileStore reference); `read` still pages files at its "
            "own page size.\n\n"
            "Persistent kernel (relay mode, persistent=true):\n"
            "- The code runs in a Python process kept alive for you (one per relay "
            "surface, conversation and agent): variables, imports and loaded data "
            "survive from one call to the next. The value of a final bare "
            "expression is printed, as in a REPL.\n"
            "- Use it for multi-step work on the same data: load or compute once "
            "(`hits = tools.call('grep', ...)`), then filter, count or inspect "
            "`hits` in later calls without redoing the calls. Do not use it for a "
            "one-off script (omit persistent) or a plain shell command (use bash).\n"
            "- The kernel stops after idle_timeout_minutes without a call (default "
            f"{KERNEL_DEFAULT_IDLE_MINUTES}, max {KERNEL_MAX_IDLE_MINUTES}); "
            "restart=true starts a fresh one. When a call lands on a new kernel the "
            "output starts with '[pawflow kernel] ...: earlier variables are gone.'\n"
            "- Print only what you need: everything printed enters your context. "
            "Output of child processes is not captured: use "
            "subprocess.run(..., capture_output=True) and print the part you need.\n"
            "- Example: call 1 `rows = [l for l in fs.read_file('big.csv').splitlines()]`"
            " then `len(rows)`; call 2 `sorted(rows, key=len)[-3:]`.\n\n"
            "Key parameters:\n"
            "- code (required): Python code to execute. Can be a single expression ('2+2') "
            "or a full script with multiple statements.\n"
            "- destination: 'sandbox' (force server sandbox), a relay service name, or omit "
            "for auto-detection.\n"
            "- local: run on the relay host instead of its container (like bash local=true).\n"
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
                "local": {
                    "type": "boolean",
                    "description": (
                        "Relay mode only: run on the relay host (or the server-local "
                        "surface of a managed relay) instead of its container."
                    ),
                },
                "max_output": {
                    "type": "integer",
                    "description": "Max output chars (default: 4000). Large outputs are auto-saved to FileStore.",
                },
                "persistent": {
                    "type": "boolean",
                    "description": (
                        "Relay mode only: run in your persistent Python kernel, "
                        "which keeps variables and imports between calls."
                    ),
                },
                "restart": {
                    "type": "boolean",
                    "description": "With persistent=true: start a fresh kernel first.",
                },
                "idle_timeout_minutes": {
                    "type": "integer",
                    "description": (
                        "With persistent=true: stop the kernel after this many "
                        f"minutes without a call (default {KERNEL_DEFAULT_IDLE_MINUTES}, "
                        f"1 to {KERNEL_MAX_IDLE_MINUTES})."
                    ),
                },
            },
            "required": ["code"],
        }

    def set_user_id(self, uid: str):
        self._user_id = uid

    def set_agent_name(self, agent_name: str):
        self._agent_name = agent_name or ""

    def execute(self, arguments: Dict[str, Any]) -> str:
        code = arguments.get("code", "")
        destination = arguments.get("destination", "")
        if not code:
            return "Error: no code provided"

        _secret_env = arguments.get("_secret_env") or {}
        _local = bool(arguments.get("local", False))
        try:
            _kernel = _kernel_options(arguments)
        except ValueError as exc:
            return f"Error: {exc}"

        # Explicit relay service name → execute remote
        _dest_raw = destination.strip()
        _dest_kind = _dest_raw.lower()
        if _dest_raw and _dest_kind not in ("server", "sandbox", "local", ""):
            return self._execute_remote(code, _dest_raw, env=_secret_env,
                                        local=_local, kernel=_kernel)

        # Explicit sandbox request
        if _dest_kind in ("server", "sandbox"):
            if _local:
                return "Error: local=true needs a relay; the sandbox has no host"
            if _kernel:
                return "Error: persistent=true needs a relay; the sandbox is stateless"
            return self._execute_sandbox(code, env=_secret_env)

        # Auto-detect: if a relay is connected, use it; else sandbox
        _relay_svc = self._find_default_relay()
        if _relay_svc:
            _svc_id = getattr(_relay_svc, '_service_id', '') or getattr(_relay_svc, 'name', '')
            if _svc_id:
                return self._execute_remote(code, _svc_id, env=_secret_env,
                                            local=_local, kernel=_kernel)

        if _local:
            return "Error: local=true needs a connected relay"
        if _kernel:
            return "Error: persistent=true needs a connected relay"

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

    def _execute_remote(self, code: str, service_name: str, env: dict = None,
                        local: bool = False, kernel: dict = None) -> str:
        """Execute code on a remote filesystem service via relay."""
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
        # `from pawflow import tools` in the script reaches PawFlow tools
        # through this relay's tool bridge, with a capability bound to this
        # relay and this agent run, revoked when the script ends.
        from core import script_capability
        _cap_env, _cap_token = script_capability.mint_for_relay(
            svc, user_id=self._user_id,
            conversation_id=self._conversation_id,
            agent_name=self._agent_name)
        _kernel_key = ""
        if kernel:
            if not _cap_env:
                return ("Error: persistent=true needs a known conversation, "
                        "agent and relay")
            _kernel_key = _kernel_key_for(
                _cap_env, getattr(svc, "_service_id", "") or svc_name, local)
        env = {**(env or {}), **_cap_env}
        # Only pass `local` when asked: relay doubles of other surfaces take
        # the plain signature.
        _where = {"local": True} if local else {}
        try:
            if hasattr(svc, 'exec'):
                # Write to temp files then execute (avoids shell escaping
                # issues). The SDK sits beside the script: Python puts the
                # script's directory first on sys.path, so `import pawflow`
                # works on any surface (container, relay host, server-local)
                # while the working directory stays the relay root.
                import os
                import tempfile
                import uuid as _uuid_exec
                _exec_id = _uuid_exec.uuid4().hex[:8]
                _run_dir = f".pawflow_exec_{_exec_id}"
                _server_local = getattr(svc, "_server_local_requested", None)
                if local and callable(_server_local) and _server_local({"local": True}):
                    # The server-local surface is rooted at "/", which the
                    # server user cannot write: use the server's temp dir.
                    _run_dir = os.path.join(tempfile.gettempdir(), _run_dir)
                _script = f"{_run_dir}/script.py"
                _exec_env = dict(env or {})
                _exec_env.setdefault(
                    "PAWFLOW_DATA_DIR",
                    os.path.join(tempfile.gettempdir(), "pawflow-exec-data", _exec_id))
                svc.write_file(_script, code.encode("utf-8"), **_where)
                svc.write_file(f"{_run_dir}/pawflow.py", _sdk_source(), **_where)
                _command = f"{_python_command(svc, local)} {_script}"
                if kernel:
                    svc.write_file(f"{_run_dir}/pawflow_kernel.py",
                                   _sdk_source("pawflow_kernel.py"), **_where)
                    _command = (
                        f"{_python_command(svc, local)} "
                        f"{_run_dir}/pawflow_kernel.py client --key {_kernel_key} "
                        f"--idle {kernel['idle_seconds']}"
                        + (" --restart" if kernel["restart"] else "")
                        + f" {_script}")
                try:
                    result = svc.exec(
                        ".", _command, env=_exec_env, **_where)
                finally:
                    try:
                        svc.delete_file(_run_dir, **_where)
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
        finally:
            script_capability.revoke(_cap_token)


def _kernel_options(arguments: Dict[str, Any]) -> Dict[str, Any] | None:
    """Validated persistent-kernel options, or None for a one-off script."""
    persistent = bool(arguments.get("persistent", False))
    restart = bool(arguments.get("restart", False))
    idle = arguments.get("idle_timeout_minutes")
    if not persistent:
        if restart or idle is not None:
            raise ValueError("restart and idle_timeout_minutes need persistent=true")
        return None
    if idle is None:
        minutes = KERNEL_DEFAULT_IDLE_MINUTES
    else:
        try:
            minutes = int(idle)
        except (TypeError, ValueError):
            raise ValueError("idle_timeout_minutes must be an integer") from None
        if not 1 <= minutes <= KERNEL_MAX_IDLE_MINUTES:
            raise ValueError(
                f"idle_timeout_minutes must be between 1 and {KERNEL_MAX_IDLE_MINUTES}")
    return {"restart": restart, "idle_seconds": minutes * 60}


def _kernel_key_for(cap_env: Dict[str, str], relay_id: str, local: bool) -> str:
    """One kernel per relay surface, conversation and agent."""
    import hashlib
    surface = "local" if local else "container"
    ident = "|".join((relay_id, surface, cap_env["PAWFLOW_CONVERSATION_ID"],
                      cap_env["PAWFLOW_AGENT_NAME"]))
    return hashlib.sha256(ident.encode("utf-8")).hexdigest()[:24]


def _sdk_source(name: str = "pawflow.py") -> bytes:
    """A PawFlow SDK module shipped beside every relay script."""
    from pathlib import Path
    path = (Path(__file__).resolve().parents[2]
            / "docker" / "pawflow_sdk" / name)
    return path.read_bytes()


def _python_command(svc, local: bool) -> str:
    """Interpreter for the script: relay containers and the server have
    ``python3``; a Windows relay host (its root is a drive path such as
    ``D:/work``) has ``python``."""
    if not local:
        return "python3"
    import re
    info = getattr(svc, "_relay_info", None) or {}
    host_root = str(info.get("host_root") or "")
    if info.get("platform") == "win32" or re.match(r"^[A-Za-z]:[/\\]", host_root):
        return "python"
    return "python3"

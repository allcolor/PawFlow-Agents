"""bash — Execute a shell command via relay."""

import logging
import re
from typing import Any, Dict, Optional

from core.handlers._fs_base import BaseFsHandler, cap_binary_output

logger = logging.getLogger(__name__)


# ── Dangerous command patterns (defense-in-depth) ──────────────────────
# The tool approval system is the primary defense. This is a safety net.

_DANGEROUS_PATTERNS = [
    # Recursive delete of root or home
    (re.compile(r'\brm\s+.*-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\s+/\s*$'
                r'|\brm\s+.*-[a-zA-Z]*f[a-zA-Z]*r[a-zA-Z]*\s+/\s*$'
                r'|\brm\s+.*-rf\s+~\s*$'
                r'|\brm\s+.*-rf\s+~/\s*$'
                r'|\brm\s+.*-rf\s+/\s',
                re.MULTILINE),
     "Blocked: recursive delete of root or home directory"),
    # Disk overwrite via redirect
    (re.compile(r'>\s*/dev/sd[a-z]|>\s*/dev/nvme|>\s*/dev/hd[a-z]'),
     "Blocked: direct disk device overwrite"),
    # Filesystem format
    (re.compile(r'\bmkfs\b'),
     "Blocked: filesystem format command"),
    # Disk wipe via dd
    (re.compile(r'\bdd\b.*\bof=/dev/'),
     "Blocked: dd write to disk device"),
    # Fork bomb
    (re.compile(r':\(\)\s*\{\s*:\|:\s*&\s*\}\s*;\s*:'),
     "Blocked: fork bomb detected"),
]


def _check_dangerous_command(command: str) -> Optional[str]:
    """Check if a command matches known dangerous patterns.

    Returns an error message if dangerous, None if safe.
    This is defense-in-depth — the tool approval system is the primary defense.
    """
    for pattern, message in _DANGEROUS_PATTERNS:
        if pattern.search(command):
            logger.warning("[bash] Dangerous command blocked: %s — %s",
                          command[:100], message)
            return f"Error: {message}. This command was blocked as a safety measure."
    return None


class BashHandler(BaseFsHandler):

    @property
    def name(self):
        return "bash"

    @property
    def description(self):
        return (
            "Execute a shell command. Default shell is bash; use shell parameter "
            "for powershell, cmd, python, or node. "
            "Requires a relay service (connected machine)."
        )

    @property
    def parameters_schema(self):
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"},
                "description": {"type": "string", "description": "Short description of what the command does."},
                "timeout": {"type": "integer", "description": "Timeout in milliseconds (default: 120000)"},
                "run_in_background": {"type": "boolean", "description": "Run command in background. Use read to check output later."},
                "shell": {
                    "type": "string",
                    "description": "Shell to use (default: bash). Options: bash, powershell, cmd, python, node.",
                },
                "path": {"type": "string", "description": "Working directory for the command"},
                "max_output": {"type": "integer", "description": "Max output chars (default: 30000, max: 150000)"},
                "relay": {"type": "string", "description": "Relay service name. Omit for default."},
            },
            "required": ["command"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        arguments = self._unwrap_json(arguments)
        arguments = self._resolve_expressions(arguments)
        command = arguments.get("command", "")
        if not command:
            logger.warning("[bash] called with empty command. raw args: %s", repr(arguments)[:300])
            return "(no command provided — ignored)"

        # Defense-in-depth: block known dangerous patterns
        danger = _check_dangerous_command(command)
        if danger:
            return danger

        relay = arguments.get("relay", "")
        svc, workdir = self._resolve(relay)

        # Workdir fallback (Claude Code container — exec local)
        if workdir:
            return self._exec_local(command, arguments)

        if svc is None or svc == "filestore":
            if svc == "filestore":
                return "Error: cannot execute commands on FileStore. Connect a relay."
            return self._no_target_error(relay)

        # Check relay type
        _svc_type = getattr(svc, 'TYPE', '') or getattr(svc, 'service_type', '')
        if _svc_type and _svc_type != "relay":
            return (f"Error: 'bash' requires a relay service. "
                    f"Service '{relay}' is type '{_svc_type}' (storage only).")

        try:
            path = arguments.get("path", ".")
            shell = arguments.get("shell", "")
            _bash_default = 30000
            _bash_max = 150000
            _max_out = min(int(arguments.get("max_output", _bash_default) or _bash_default), _bash_max)

            _exec_kwargs = {"shell": shell}
            if "timeout" in arguments:
                _exec_kwargs["timeout"] = arguments["timeout"]
            # Pass secret env vars (injected by tool_relay_service)
            if arguments.get("_secret_env"):
                _exec_kwargs["env"] = arguments["_secret_env"]
            result = svc.exec(path, command, **_exec_kwargs)
            output = result.get("stdout", "")
            if result.get("stderr"):
                output += "\nSTDERR:\n" + result["stderr"]
            if result.get("returncode", 0) != 0:
                output += f"\n(exit code: {result['returncode']})"
            if not output:
                return "(no output)"
            output = cap_binary_output(output, _max_out)
            if len(output) > _max_out:
                output = output[:_max_out] + (
                    f"\n\n... [{len(output) - _max_out} chars truncated"
                    f" — use max_output to see more]")
            return output
        except Exception as e:
            return f"Error executing command: {e}"

    def _exec_local(self, command: str, arguments: dict) -> str:
        """Execute locally in the agent workdir (Claude Code container mode)."""
        import subprocess
        shell_name = arguments.get("shell", "") or "bash"
        cwd = arguments.get("path", "") or self._workdir
        if cwd and not cwd.startswith("/"):
            cwd = self._sandbox_path(cwd, self._workdir)

        _run_kwargs = dict(shell=True, capture_output=True, text=True,
                           cwd=cwd or self._workdir)
        if "timeout" in arguments:
            _run_kwargs["timeout"] = arguments["timeout"]
        # Inject secret env vars
        if arguments.get("_secret_env"):
            import os
            _env = os.environ.copy()
            _env.update(arguments["_secret_env"])
            _run_kwargs["env"] = _env

        try:
            result = subprocess.run(command, **_run_kwargs,
                executable=f"/bin/{shell_name}" if shell_name in ("bash", "sh") else None,
            )
            output = result.stdout
            if result.stderr:
                output += "\nSTDERR:\n" + result.stderr
            if result.returncode != 0:
                output += f"\n(exit code: {result.returncode})"
            return output or "(no output)"
        except subprocess.TimeoutExpired:
            return f"Error: command timed out after {timeout}s"
        except Exception as e:
            return f"Error: {e}"

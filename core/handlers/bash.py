"""bash — Execute a shell command via relay."""

import logging
import re
import subprocess
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
            "Executes a given shell command and returns its output. "
            "Default shell is bash; use shell parameter for powershell, cmd, python, or node. "
            "Requires a relay service (connected machine).\n\n"
            "The working directory persists between commands, but shell state does not "
            "(env vars, aliases, functions are reset each call).\n\n"
            "IMPORTANT: Avoid using this tool to run cat, head, tail, sed, awk, echo, find, "
            "grep, or rg commands. Instead, use the appropriate dedicated tool:\n"
            " - File search: Use glob (NOT find or ls)\n"
            " - Content search: Use grep (NOT grep/rg as bash command)\n"
            " - Read files: Use read (NOT cat/head/tail)\n"
            " - Edit files: Use edit (NOT sed/awk)\n"
            " - Write files: Use write (NOT echo >/cat <<EOF)\n\n"
            "Parameters:\n"
            " - command: The shell command to execute.\n"
            " - description: A short description of what the command does. "
            "This is logged for auditability but NOT executed.\n"
            " - timeout: Optional timeout in milliseconds. No default — "
            "commands run without a time limit unless you set this. "
            "If > 1000, treated as ms.\n"
            " - run_in_background: Set to true to run in background. "
            "Use read to check the output file later. "
            "Do not use '&' at the end — use this parameter instead.\n"
            " - shell: Shell to use (bash, powershell, cmd, python, node).\n"
            " - path: Working directory for the command.\n\n"
            "Git safety rules:\n"
            " - NEVER force push (git push --force) unless explicitly asked.\n"
            " - NEVER amend commits (git commit --amend) unless explicitly asked. "
            "After a hook failure, create a NEW commit instead.\n"
            " - NEVER skip hooks (--no-verify) or bypass signing unless explicitly asked.\n"
            " - NEVER commit changes unless explicitly asked.\n"
            " - Prefer staging specific files by name over 'git add -A' or 'git add .' "
            "which can accidentally include secrets or large binaries.\n"
            " - NEVER run destructive git commands (reset --hard, checkout ., restore ., "
            "clean -f, branch -D) unless explicitly asked."
        )

    @property
    def parameters_schema(self):
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"},
                "description": {"type": "string", "description": "Short description of what the command does."},
                "timeout": {"type": "integer", "description": "Optional timeout in milliseconds. Omit for no timeout (command runs until it exits)."},
                "run_in_background": {"type": "boolean", "description": "Run command in background. Use read to check output later."},
                "shell": {
                    "type": "string",
                    "description": "Shell to use (default: bash). Options: bash, powershell, cmd, python, node.",
                },
                "path": {"type": "string", "description": "Working directory for the command"},
                "max_output": {"type": "integer", "description": "Max output chars (default: 30000, max: 150000)"},
                "relay": {"type": "string", "description": "Relay service name. Omit for default."},
                "source": {"type": "string", "description": "Alias for relay."},
                "filesystem": {"type": "string", "description": "Alias for relay."},
                "service": {"type": "string", "description": "Alias for relay."},
            },
            "required": ["command"],
        }

    # Background task storage: bg_id → {thread, output_file, command, started_at}
    _bg_tasks: Dict[str, dict] = {}

    def _resolve_timeout(self, arguments: dict):
        """Resolve timeout: CC sends milliseconds, convert to seconds.

        Returns None when no timeout is specified — the command then
        runs without a time limit. Project rule: no arbitrary timeouts
        (only the LLM watchdog has one). If the LLM needs a cutoff it
        passes `timeout` explicitly.
        """
        raw = arguments.get("timeout")
        if raw is None:
            return None  # no timeout by default
        raw = int(raw)
        # CC sends milliseconds. Heuristic: values > 1000 are ms.
        if raw > 1000:
            return raw // 1000
        return raw

    def execute(self, arguments: Dict[str, Any]) -> str:
        arguments = self._unwrap_json(arguments)
        arguments = self._resolve_expressions(arguments)
        command = arguments.get("command", "")
        if not command:
            logger.warning("[bash] called with empty command. raw args: %s", repr(arguments)[:300])
            return "(no command provided — ignored)"

        # Log description if provided (CC UI metadata)
        desc = arguments.get("description", "")
        if desc:
            logger.info("[bash] %s: %s", desc, command[:100])

        # Defense-in-depth: block known dangerous patterns
        danger = _check_dangerous_command(command)
        if danger:
            return danger

        # Background execution: run in thread, return immediately
        if arguments.get("run_in_background"):
            return self._run_background(command, arguments)

        relay = (arguments.get("relay", "") or arguments.get("source", "")
                 or arguments.get("filesystem", "") or arguments.get("service", ""))
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
            timeout = self._resolve_timeout(arguments)

            _exec_kwargs = {"shell": shell, "timeout": timeout}
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

    def _run_background(self, command: str, arguments: dict) -> str:
        """Run command in background thread, store output to temp file."""
        import threading, tempfile, os, time, uuid
        bg_id = f"bg_{uuid.uuid4().hex[:8]}"
        relay = (arguments.get("relay", "") or arguments.get("source", "")
                 or arguments.get("filesystem", "") or arguments.get("service", ""))
        svc, workdir = self._resolve(relay)

        out_path = ""
        out_disk_path = None
        filename = f"bash_bg_{bg_id}.out"
        if self._user_id and self._conversation_id:
            try:
                from core.file_store import FileStore
                store = FileStore.instance()
                file_id = store.store(
                    filename,
                    b"(background command still running)\n",
                    "text/plain",
                    conversation_id=self._conversation_id,
                    user_id=self._user_id,
                    agent_name=self._agent_name,
                    category="tool_result",
                )
                out_path = f"fs://filestore/{file_id}/{filename}"
                out_disk_path = store.get_disk_path(file_id, user_id=self._user_id)
            except Exception:
                logger.debug("[bash-bg] failed to allocate FileStore output", exc_info=True)

        # Fallback only for non-agent contexts without FileStore scope.
        if not out_path and workdir:
            out_dir = workdir
            out_path = os.path.join(out_dir, f".bash_bg_{bg_id}.out")
        elif not out_path:
            out_dir = tempfile.gettempdir()
            out_path = os.path.join(out_dir, f".bash_bg_{bg_id}.out")

        def _write_output(text: str):
            content = text or "(no output)"
            if out_disk_path is not None:
                out_disk_path.write_text(content, encoding="utf-8")
                return
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(content)

        def _bg_run():
            try:
                if workdir:
                    result = self._exec_local_raw(command, arguments)
                elif svc and svc != "filestore":
                    path = arguments.get("path", ".")
                    shell = arguments.get("shell", "")
                    timeout = self._resolve_timeout(arguments)
                    result = svc.exec(path, command, shell=shell, timeout=timeout)
                else:
                    result = {"stdout": "Error: no relay", "returncode": 1}
                output = result.get("stdout", "")
                if result.get("stderr"):
                    output += "\nSTDERR:\n" + result["stderr"]
                if result.get("returncode", 0) != 0:
                    output += f"\n(exit code: {result['returncode']})"
                _write_output(output or "(no output)")
            except Exception as e:
                try:
                    _write_output(f"Error: {e}")
                except Exception:
                    logger.exception("[bash-bg] failed to write background output")

        thread = threading.Thread(target=_bg_run, daemon=True, name=f"bash-bg-{bg_id}")
        thread.start()
        BashHandler._bg_tasks[bg_id] = {
            "thread": thread, "output_file": out_path,
            "command": command[:100], "started_at": time.time(),
        }
        read_hint = f'read(path="{out_path}"'
        read_hint += ")"
        return (f"Background command started (id: {bg_id}). Output file: {out_path}\n"
                f"Use {read_hint} to check output.")

    def _exec_local_raw(self, command: str, arguments: dict) -> dict:
        """Execute locally, return dict with stdout/stderr/returncode."""
        import subprocess
        shell_name = arguments.get("shell", "") or "bash"
        cwd = arguments.get("path", "") or self._workdir
        if cwd and not cwd.startswith("/"):
            cwd = self._sandbox_path(cwd, self._workdir)
        timeout = self._resolve_timeout(arguments)
        _run_kwargs = dict(shell=True, capture_output=True, text=True,
                           cwd=cwd or self._workdir, timeout=timeout)
        if arguments.get("_secret_env"):
            import os
            _env = os.environ.copy()
            _env.update(arguments["_secret_env"])
            _run_kwargs["env"] = _env
        result = subprocess.run(command, **_run_kwargs,
            executable=f"/bin/{shell_name}" if shell_name in ("bash", "sh") else None)
        return {"stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode}

    def _exec_local(self, command: str, arguments: dict) -> str:
        """Execute locally in the agent workdir (Claude Code container mode)."""
        try:
            result = self._exec_local_raw(command, arguments)
            output = result["stdout"]
            if result["stderr"]:
                output += "\nSTDERR:\n" + result["stderr"]
            if result["returncode"] != 0:
                output += f"\n(exit code: {result['returncode']})"
            return output or "(no output)"
        except subprocess.TimeoutExpired:
            timeout = self._resolve_timeout(arguments)
            return f"Error: command timed out after {timeout}s"
        except Exception as e:
            return f"Error: {e}"

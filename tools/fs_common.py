"""Shared utilities for filesystem relay modules.

Extracted to break circular imports between fs_actions and fs_exec.
"""
import logging

import os
import shutil as _shutil
import subprocess  # nosec B404
from pathlib import Path
from typing import Dict, Optional, Tuple

# Size limits
MAX_EXEC_OUTPUT = 10 * 1024 * 1024  # 10 MB for stdout/stderr


# Docker / path / host-IP helpers canonicalized in pawflow_relay.utils.
# Re-exported here under the underscore aliases that fs_actions, fs_exec,
# tools/pawflow_relay.py have always used, so downstream imports stay put.
from pawflow_relay.utils import (
    docker_cmd as _docker_cmd,
    translate_path as _translate_path,
    to_host_path as _to_host_path,
    get_host_ip as _get_host_ip,
)


def detect_available_shells() -> Dict[str, str]:
    """Detect available shells on this system. Returns {name: path}."""
    shells: Dict[str, str] = {}
    if os.name == "nt":
        # Windows shells
        _cmd = os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe")
        if os.path.isfile(_cmd):
            shells["cmd"] = _cmd
        for _ps in ("pwsh", "powershell"):
            _p = _shutil.which(_ps)
            if _p:
                shells["powershell"] = _p
                break
        # Git Bash: lives in Git\bin\bash.exe, NOT in PATH by default
        _git_bash = None
        _git = _shutil.which("git")
        if _git:
            _git_bin = str(Path(_git).resolve().parent.parent / "bin" / "bash.exe")
            if os.path.isfile(_git_bin):
                _git_bash = _git_bin
        if not _git_bash:
            for _gb in (r"C:\Program Files\Git\bin\bash.exe",
                        r"C:\Program Files (x86)\Git\bin\bash.exe"):
                if os.path.isfile(_gb):
                    _git_bash = _gb
                    break
        if _git_bash:
            shells["bash"] = _git_bash
        # WSL bash: system32\bash.exe
        _wsl_bash = _shutil.which("bash")
        if _wsl_bash:
            _wbl = _wsl_bash.lower().replace("\\", "/")
            if "system32" in _wbl or "wsl" in _wbl:
                shells["wsl"] = _wsl_bash
            elif not _git_bash:
                shells["bash"] = _wsl_bash
    else:
        # Unix shells
        for _sh in ("bash", "sh", "zsh", "fish"):
            _p = _shutil.which(_sh)
            if _p:
                shells[_sh] = _p
    # Interpreters (cross-platform)
    for _interp in ("python", "python3", "node"):
        _p = _shutil.which(_interp)
        if _p:
            shells[_interp] = _p
    # Docker-based shells (isolated execution)
    try:
        _dr = subprocess.run(_docker_cmd() + ["info"], capture_output=True, timeout=10)  # nosec B603
        if _dr.returncode == 0:
            _docker_bin = _docker_cmd()[0]
            shells["docker-python"] = _docker_bin
            shells["docker-node"] = _docker_bin
            shells["docker-bash"] = _docker_bin
    except Exception:
        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
    return shells


def _resolve_shell(name: str) -> str:
    """Resolve a shell name to its executable path. Returns '' if not found."""
    shells = detect_available_shells()
    if name in shells:
        return shells[name]
    _aliases = {"ps": "powershell", "pwsh": "powershell", "py": "python",
                "python3": "python", "js": "node"}
    canonical = _aliases.get(name.lower(), name.lower())
    return shells.get(canonical, "")


def is_unc_path(path: str) -> bool:
    raw = str(path or "")
    return raw.startswith("\\\\") or raw.startswith("//")


def _path_name(path: str) -> str:
    raw = str(path or "")
    return raw.replace("\\", "/").rstrip("/").split("/")[-1]


def windows_shell_cwd(command: str, cwd: str,
                      shell_name: str = "",
                      executable: str = "") -> Tuple[str, Optional[str]]:
    """Return a Windows-safe command/cwd pair for shell execution.

    cmd.exe cannot use a UNC path as its current directory. When local=True
    forwards execution to the Windows host helper and the project lives under
    a WSL UNC path, run through pushd instead; pushd maps the UNC path to a
    temporary drive for the command, then popd releases it.
    """
    if os.name != "nt" or not is_unc_path(cwd):
        return command, cwd
    shell_key = (shell_name or "cmd").lower()
    exe_name = _path_name(executable or "cmd.exe").lower()
    if shell_key not in ("", "cmd") and exe_name != "cmd.exe":
        return command, cwd
    quoted = cwd.replace('"', '""')
    return f'pushd "{quoted}" >nul && {command} & popd', None


def run_cancellable(request_id: str, cmd, *, timeout=None, **popen_kwargs):
    """Drop-in replacement for `subprocess.run(capture_output=True, ...)`
    that registers the spawned Popen so the server's cancel_request
    envelope can terminate it (via pawflow_relay.proc_registry).

    Use this for any subprocess call that may run long enough for the
    user to want to FORCE STOP it. Short ops (`docker info`,
    `git branch --show-current`, ...) don't need it.

    Returns a `subprocess.CompletedProcess`. Caller passes
    `text=True` / `encoding=...` / `errors=...` / `cwd=...` / `env=...`
    through **popen_kwargs.
    """
    from pawflow_relay.proc_registry import (
        register_inflight_proc, unregister_inflight_proc,
    )
    capture = popen_kwargs.pop("capture_output", False)
    if capture:
        popen_kwargs["stdout"] = subprocess.PIPE
        popen_kwargs["stderr"] = subprocess.PIPE
    proc = subprocess.Popen(cmd, **popen_kwargs)  # nosec B603
    register_inflight_proc(request_id, proc)
    try:
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            raise
    finally:
        unregister_inflight_proc(request_id)
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)

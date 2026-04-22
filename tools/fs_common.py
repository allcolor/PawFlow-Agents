"""Shared utilities for filesystem relay modules.

Extracted to break circular imports between fs_actions and fs_exec.
"""

import os
import shutil as _shutil
import subprocess
from pathlib import Path
from typing import Dict

# Size limits
MAX_EXEC_OUTPUT = 10 * 1024 * 1024  # 10 MB for stdout/stderr


def _docker_cmd():
    if os.name == "nt":
        return ["wsl", "docker"]
    return ["docker"]


def _translate_path(p):
    if os.name != "nt":
        return p
    p = p.replace("\\", "/")
    if len(p) >= 2 and p[1] == ":":
        return f"/mnt/{p[0].lower()}{p[2:]}"
    return p


def _get_host_ip():
    """IP that a container can use to reach the host.

    On Windows the Docker-backed WSL distro doesn't always resolve
    host.docker.internal to the LAN IP, so probe via a UDP connect."""
    if os.name == "nt":
        import socket as _s
        try:
            s = _s.socket(_s.AF_INET, _s.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            pass
    return "host.docker.internal"


def _to_host_path(container_path):
    """Translate container path to host path for DinD volume mounts."""
    host_workdir = os.environ.get("PAWFLOW_HOST_WORKDIR")
    if not host_workdir:
        return container_path
    container_workdir = os.environ.get("PAWFLOW_WORKDIR", "/workspace")
    try:
        rel = os.path.relpath(container_path, container_workdir)
        if rel.startswith(".."):
            return container_path
        if rel == ".":
            return host_workdir
        return os.path.join(host_workdir, rel).replace("\\", "/")
    except ValueError:
        return container_path


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
        _dr = subprocess.run(_docker_cmd() + ["info"], capture_output=True, timeout=10)
        if _dr.returncode == 0:
            _docker_bin = _docker_cmd()[0]
            shells["docker-python"] = _docker_bin
            shells["docker-node"] = _docker_bin
            shells["docker-bash"] = _docker_bin
    except Exception:
        pass
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

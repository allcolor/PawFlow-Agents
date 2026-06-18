"""Shared module-level helpers/consts for the pawflow_relay thread split."""

import logging

import os
import secrets
import shutil
import subprocess  # nosec B404
import sys
from pathlib import Path

from pawflow_relay.utils import (
    docker_cmd,
)


_RELAY_APPARMOR_PROFILE = "pawflow-relay"
_relay_apparmor_resolved = None


def _relay_apparmor_security_opts(image: str) -> list:
    """AppArmor option for the relay container, resolved once per process.

    Mirrors core.apparmor (not importable here: this module runs on the
    relay host, which may not ship the server codebase): if the
    pawflow-relay profile is loaded on the Docker host, confine the relay
    with it; otherwise fall back to apparmor=unconfined (previous
    behaviour, and a no-op on hosts without AppArmor such as
    Windows/macOS Docker Desktop). PAWFLOW_RELAY_APPARMOR_PROFILE
    overrides the detection with a verbatim profile name.
    """
    global _relay_apparmor_resolved
    forced = os.environ.get("PAWFLOW_RELAY_APPARMOR_PROFILE", "").strip()
    if forced:
        return ["--security-opt", f"apparmor={forced}"]
    if _relay_apparmor_resolved is None:
        try:
            probe = subprocess.run(  # nosec B603
                docker_cmd() + [
                    "run", "--rm",
                    "--security-opt", f"apparmor={_RELAY_APPARMOR_PROFILE}",
                    "--entrypoint", "/bin/true", image,
                ],
                capture_output=True, text=True, timeout=60)
            _relay_apparmor_resolved = (
                _RELAY_APPARMOR_PROFILE if probe.returncode == 0
                else "unconfined")
        except Exception:
            _relay_apparmor_resolved = "unconfined"
    return ["--security-opt", f"apparmor={_relay_apparmor_resolved}"]


def _relay_container_prefix(relay_id: str) -> str:
    return f"pf-{relay_id[:12].replace('.', '-').replace('_', '-')}"


def _make_relay_container_name(relay_id: str, purpose: str) -> str:
    return f"{_relay_container_prefix(relay_id)}-{purpose}-{secrets.token_hex(4)}"


def _is_windows_drive_absolute_path(path: str) -> bool:
    raw = str(path or "").replace("\\", "/")
    return len(raw) >= 3 and raw[1] == ":" and raw[2] == "/"


def _is_host_absolute_path(path: str) -> bool:
    raw = str(path or "").replace("\\", "/")
    return raw.startswith("/") or raw.startswith("//") or _is_windows_drive_absolute_path(raw)


def _relay_runtime_root() -> Path:
    """Return the relay runtime root for source and packaged desktop modes."""
    override = os.environ.get("PAWFLOW_RELAY_RUNTIME_ROOT", "")
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parent.parent


def _relay_tools_dir() -> str:
    return str(_relay_runtime_root() / "tools")


def _host_python_command() -> str:
    """Return a real host Python, not the packaged pawflow-relay binary."""
    override = os.environ.get("PAWFLOW_RELAY_PYTHON", "") or os.environ.get("PYTHON", "")
    if override:
        return override
    if not getattr(sys, "frozen", False):
        return sys.executable
    return shutil.which("python") or shutil.which("python3") or ""


def _host_abs_path(raw_path: str, root_dir: str) -> str:
    raw = str(raw_path or ".").replace("\\", "/")
    if raw.startswith("fs://"):
        parts = raw[5:].split("/", 1)
        raw = parts[1] if len(parts) > 1 else "."
    root = Path(root_dir).resolve()
    if raw == "/workspace":
        target = root
    elif raw.startswith("/workspace/"):
        target = root / raw[len("/workspace/"):]
    elif _is_windows_drive_absolute_path(raw):
        return raw
    elif _is_host_absolute_path(raw):
        target = Path(raw).resolve()
    else:
        target = (root / raw).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            raise ValueError(f"Path traversal blocked: {raw_path}")
    return str(target)


def _kill_relay_containers(relay_id: str) -> int:
    prefix = _relay_container_prefix(relay_id)
    try:
        result = subprocess.run(  # nosec B603
            docker_cmd() + [
                "ps", "-a", "--filter", f"name={prefix}",
                "--format", "{{.ID}}\t{{.Names}}",
            ],
            capture_output=True, text=True, timeout=10)
    except Exception:
        return 0
    killed = 0
    for line in result.stdout.strip().splitlines():
        if not line.strip():
            continue
        container_id = line.split("\t", 1)[0]
        try:
            subprocess.run(docker_cmd() + ["rm", "-f", container_id],  # nosec B603
                           capture_output=True, timeout=10)
            killed += 1
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
    return killed


def cleanup_relay_containers(relay_id: str) -> int:
    """Remove Docker containers owned by one relay id."""
    return _kill_relay_containers(relay_id)

"""Helpers for service installation preparation and progress reporting."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess  # nosec B404 - callers pass explicit argv with shell disabled.
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List

from core import ServiceError

logger = logging.getLogger(__name__)


_INSTALL_HINTS = {
    "git": {
        "ubuntu": "sudo apt install -y git",
        "macos": "brew install git",
        "windows": "winget install Git.Git",
    },
    "ffmpeg": {
        "ubuntu": "sudo apt install -y ffmpeg",
        "macos": "brew install ffmpeg",
        "windows": "winget install Gyan.FFmpeg",
    },
    "python3-venv": {
        "ubuntu": "sudo apt install -y python3-venv",
        "macos": "python3 includes venv; install Python with brew install python if missing",
        "windows": "Install Python from python.org and enable pip/venv",
    },
    "wsl.exe": {
        "windows": "wsl --install",
    },
}


class ServiceInstallReporter:
    """Publish service installation progress to logs and the conversation UI."""

    def __init__(self, conversation_id: str = "", service_id: str = "",
                 service_type: str = ""):
        self.conversation_id = conversation_id or ""
        self.service_id = service_id or ""
        self.service_type = service_type or ""

    def step(self, phase: str, message: str = "", status: str = "running",
             progress: float | None = None, **extra: Any) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "service_id": self.service_id,
            "service_type": self.service_type,
            "phase": phase,
            "status": status,
            "message": message or phase.replace("_", " "),
            "ts": time.time(),
        }
        if progress is not None:
            payload["progress"] = progress
        payload.update({k: v for k, v in extra.items() if v is not None})
        logger.info(
            "[service-install:%s] %s %s: %s",
            self.service_id or self.service_type or "service", status, phase,
            payload["message"],
        )
        if self.conversation_id:
            try:
                from core.conversation_event_bus import ConversationEventBus
                ConversationEventBus.instance().publish_event(
                    self.conversation_id, "service_install_progress", payload)
            except Exception:
                logger.debug("service install progress publish failed", exc_info=True)
        return payload


def executable_requirement(name: str, *, required: bool = True,
                           install_name: str = "") -> Dict[str, Any]:
    path = shutil.which(name)
    return {
        "name": install_name or name,
        "type": "binary",
        "required": required,
        "ok": bool(path),
        "path": path or "",
        "install": _INSTALL_HINTS.get(install_name or name, {}),
    }


def python_venv_requirement(python_executable: str = "") -> Dict[str, Any]:
    python = python_executable or sys.executable or shutil.which("python3") or shutil.which("python") or ""
    result = {
        "name": "python3-venv",
        "type": "python_module",
        "required": True,
        "ok": False,
        "path": python,
        "install": _INSTALL_HINTS["python3-venv"],
        "detail": "",
    }
    if not python:
        result["detail"] = "python executable not found"
        return result
    with tempfile.TemporaryDirectory(prefix="pawflow-venv-check-") as tmp:
        target = Path(tmp) / "venv"
        proc = subprocess.run(  # nosec B603 - python argv is explicit and shell=False.
            [python, "-m", "venv", str(target)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
            check=False,
        )
    result["ok"] = proc.returncode == 0
    if proc.returncode != 0:
        result["detail"] = tail(proc.stderr or proc.stdout)
    return result


def assert_requirements(requirements: Iterable[Dict[str, Any]]) -> None:
    missing = [req for req in requirements if req.get("required", True) and not req.get("ok")]
    if not missing:
        return
    lines = ["Missing service installation requirement(s):"]
    for req in missing:
        detail = f" ({req.get('detail')})" if req.get("detail") else ""
        lines.append(f"- {req.get('name')}{detail}")
        install = req.get("install") or {}
        if install:
            hints = "; ".join(f"{os_name}: {cmd}" for os_name, cmd in install.items())
            lines.append(f"  Install: {hints}")
    raise ServiceError("\n".join(lines))


def tail(text: str, limit: int = 1600) -> str:
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    return text[-limit:]


def run_checked(cmd: List[str], *, cwd: str | None = None,
                reporter: ServiceInstallReporter | None = None,
                phase: str = "command", timeout: int | None = None) -> None:
    if reporter:
        reporter.step(phase, "Running " + " ".join(cmd[:3]))
    proc = subprocess.run(  # nosec B603 - managed install commands use explicit argv.
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        detail = tail((proc.stderr or "") + "\n" + (proc.stdout or ""))
        raise ServiceError(
            f"Service install command failed ({' '.join(cmd[:4])}): {detail}")


def write_install_state(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(data)
    payload.setdefault("installed_at", time.time())
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


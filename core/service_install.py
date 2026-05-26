"""Helpers for service installation preparation and progress reporting."""

from __future__ import annotations

import json
import logging
import hashlib
import shutil
import subprocess  # nosec B404 - callers pass explicit argv with shell disabled.
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, List

from core import ServiceError
from core import paths as _paths

logger = logging.getLogger(__name__)


_INSTALL_LOCKS: Dict[str, threading.RLock] = {}
_INSTALL_LOCKS_GUARD = threading.Lock()
_TERMINAL_STATUSES = {"ready", "failed", "cancelled"}


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
                 service_type: str = "", scope: str = "", scope_id: str = ""):
        self.conversation_id = conversation_id or ""
        self.service_id = service_id or ""
        self.service_type = service_type or ""
        self.scope = scope or ""
        self.scope_id = scope_id or ""

    def step(self, phase: str, message: str = "", status: str = "running",
             progress: float | None = None, **extra: Any) -> Dict[str, Any]:
        if status not in _TERMINAL_STATUSES:
            self.check_cancelled()
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
        if self.scope and self.service_id:
            append_install_log(self.scope, self.scope_id, self.service_id, payload)
            status_for_state = status if status in _TERMINAL_STATUSES else "installing"
            update_install_state(
                self.scope, self.scope_id, self.service_id,
                status=status_for_state,
                service_type=self.service_type,
                phase=phase,
                message=payload["message"],
                progress=progress,
            )
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

    def check_cancelled(self) -> None:
        if self.scope and self.service_id and install_cancel_requested(
                self.scope, self.scope_id, self.service_id):
            raise ServiceError(f"Service installation cancelled: {self.service_id}")


def _safe_part(value: str) -> str:
    text = str(value or "").strip() or "_"
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in text)
    if len(safe) <= 80:
        return safe
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]  # nosec B324 - non-security path shortening.
    return f"{safe[:60]}_{digest}"


def _install_dir(scope: str, scope_id: str, service_id: str) -> Path:
    return (_paths.RUNTIME_DIR / "service_installs" / _safe_part(scope)
            / _safe_part(scope_id or "global") / _safe_part(service_id))


def _state_file(scope: str, scope_id: str, service_id: str) -> Path:
    return _install_dir(scope, scope_id, service_id) / "state.json"


def _log_file(scope: str, scope_id: str, service_id: str) -> Path:
    return _install_dir(scope, scope_id, service_id) / "install.log.jsonl"


def _lock_key(scope: str, scope_id: str, service_id: str) -> str:
    return f"{scope}\0{scope_id}\0{service_id}"


def _install_lock(scope: str, scope_id: str, service_id: str) -> threading.RLock:
    key = _lock_key(scope, scope_id, service_id)
    with _INSTALL_LOCKS_GUARD:
        lock = _INSTALL_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _INSTALL_LOCKS[key] = lock
        return lock


def read_install_state(scope: str, scope_id: str, service_id: str) -> Dict[str, Any]:
    path = _state_file(scope, scope_id, service_id)
    if not path.exists():
        return {"status": "not_installed", "scope": scope, "scope_id": scope_id, "service_id": service_id}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("status", "not_installed")
        data.setdefault("scope", scope)
        data.setdefault("scope_id", scope_id)
        data.setdefault("service_id", service_id)
        return data
    except Exception:
        logger.debug("failed to read service install state %s", path, exc_info=True)
        return {"status": "unknown", "scope": scope, "scope_id": scope_id, "service_id": service_id}


def update_install_state(scope: str, scope_id: str, service_id: str,
                         **updates: Any) -> Dict[str, Any]:
    path = _state_file(scope, scope_id, service_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    state = read_install_state(scope, scope_id, service_id)
    now = time.time()
    state.update({k: v for k, v in updates.items() if v is not None})
    state.update({"scope": scope, "scope_id": scope_id, "service_id": service_id, "updated_at": now})
    if state.get("status") == "installing":
        state.setdefault("started_at", now)
    if state.get("status") in _TERMINAL_STATUSES:
        state["finished_at"] = now
        state.pop("cancel_requested", None)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    return state


def append_install_log(scope: str, scope_id: str, service_id: str,
                       event: Dict[str, Any]) -> None:
    path = _log_file(scope, scope_id, service_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(event)
    payload.setdefault("ts", time.time())
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def read_install_log(scope: str, scope_id: str, service_id: str,
                     limit: int = 200) -> Dict[str, Any]:
    path = _log_file(scope, scope_id, service_id)
    if not path.exists():
        return {"log": [], "path": str(path)}
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if limit > 0:
        lines = lines[-limit:]
    events = []
    for line in lines:
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            events.append({"message": line})
    return {"log": events, "path": str(path)}


def request_install_cancel(scope: str, scope_id: str, service_id: str) -> Dict[str, Any]:
    lock = _install_lock(scope, scope_id, service_id)
    if lock.acquire(blocking=False):
        try:
            state = read_install_state(scope, scope_id, service_id)
            if state.get("status") == "installing":
                cancelled = update_install_state(
                    scope, scope_id, service_id,
                    status="cancelled",
                    service_type=state.get("service_type", ""),
                    phase="cancelled",
                    message="Installation cancelled",
                    error="Installation cancelled",
                )
                append_install_log(scope, scope_id, service_id, {
                    "status": "cancelled",
                    "phase": "cancelled",
                    "message": "Installation cancelled",
                })
                return cancelled
            return state
        finally:
            lock.release()
    return update_install_state(
        scope, scope_id, service_id,
        cancel_requested=True,
        message="Cancellation requested",
    )


def install_cancel_requested(scope: str, scope_id: str, service_id: str) -> bool:
    return bool(read_install_state(scope, scope_id, service_id).get("cancel_requested"))


@contextmanager
def service_install_session(scope: str, scope_id: str, service_id: str,
                            service_type: str):
    lock = _install_lock(scope, scope_id, service_id)
    if not lock.acquire(blocking=False):
        raise ServiceError(f"Service installation already running: {service_id}")
    state = read_install_state(scope, scope_id, service_id)
    if state.get("status") == "installing":
        lock.release()
        raise ServiceError(f"Service installation already running: {service_id}")
    try:
        update_install_state(
            scope, scope_id, service_id,
            status="installing",
            service_type=service_type,
            phase="queued",
            message="Preparing service installation",
            progress=0.0,
            cancel_requested=False,
        )
        yield
    except Exception as exc:
        status = "cancelled" if "cancelled" in str(exc).lower() else "failed"
        update_install_state(
            scope, scope_id, service_id,
            status=status,
            service_type=service_type,
            phase=status,
            message=str(exc),
            error=str(exc),
        )
        append_install_log(scope, scope_id, service_id, {
            "status": status,
            "phase": status,
            "message": str(exc),
        })
        raise
    finally:
        lock.release()


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
        reporter.check_cancelled()
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


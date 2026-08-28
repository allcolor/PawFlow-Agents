"""Durable, atomic and secret-free installer operation state."""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Literal

from pawflow_installer.events import redact
from pawflow_installer.models import InstallRequest, StrictModel, utc_now

StepStatus = Literal["pending", "running", "completed", "failed", "cancelled"]


class StepResult(StrictModel):
    status: StepStatus
    started_at: str | None
    finished_at: str | None
    evidence: dict[str, Any]
    error: str | None


class OperationState(StrictModel):
    version: Literal[1]
    operation_id: str
    created_at: str
    updated_at: str
    request_digest: str
    target_fingerprint: str
    request: dict[str, Any]
    phase: str
    completed_steps: list[str]
    step_results: dict[str, StepResult]
    cancelled: bool

    def touch(self) -> None:
        self.updated_at = utc_now()


def default_state_root() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            raise ValueError("LOCALAPPDATA is required for installer state on Windows")
        return Path(base) / "PawFlow" / "Installer" / "operations"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "PawFlow" / "Installer" / "operations"
    base = os.environ.get("XDG_STATE_HOME")
    return (Path(base) if base else Path.home() / ".local" / "state") / "pawflow" / "installer"


def target_fingerprint(request: InstallRequest) -> str:
    target = request.target
    if target.kind == "local":
        return "local"
    return f"ssh:{target.user}@{target.host}:{target.port}"


class InstallerStateStore:
    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root is not None else default_state_root()

    def _path(self, operation_id: str) -> Path:
        safe_id = str(uuid.UUID(operation_id))
        return self.root / f"{safe_id}.json"

    def create(self, request: InstallRequest) -> OperationState:
        now = utc_now()
        state = OperationState(
            version=1,
            operation_id=str(uuid.uuid4()),
            created_at=now,
            updated_at=now,
            request_digest=request.digest(),
            target_fingerprint=target_fingerprint(request),
            request=request.model_dump(mode="json"),
            phase="request_validated",
            completed_steps=[],
            step_results={},
            cancelled=False,
        )
        self.save(state)
        return state

    def save(self, state: OperationState) -> None:
        state.touch()
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(state.operation_id)
        temporary = self.root / f".{state.operation_id}.{uuid.uuid4().hex}.tmp"
        payload = redact(state.model_dump(mode="json"))
        encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
        try:
            with open(temporary, "xb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            try:
                directory_fd = os.open(self.root, os.O_RDONLY)
            except (AttributeError, OSError):
                return
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temporary.exists():
                temporary.unlink()

    def load(self, operation_id: str) -> OperationState:
        path = self._path(operation_id)
        with open(path, encoding="utf-8") as handle:
            return OperationState.model_validate(json.load(handle))

    def list(self) -> list[OperationState]:
        if not self.root.exists():
            return []
        states = []
        for path in sorted(self.root.glob("*.json")):
            try:
                states.append(self.load(path.stem))
            except (OSError, ValueError):
                continue
        return sorted(states, key=lambda state: state.updated_at, reverse=True)

    def mark_cancelled(self, operation_id: str) -> OperationState:
        state = self.load(operation_id)
        state.cancelled = True
        self.save(state)
        return state

    def cleanup(self, operation_id: str) -> None:
        path = self._path(operation_id)
        path.unlink(missing_ok=True)

    def assert_matches(self, state: OperationState, request: InstallRequest) -> None:
        if state.request_digest != request.digest():
            raise ValueError("request does not match the resumable operation")
        if state.target_fingerprint != target_fingerprint(request):
            raise ValueError("target does not match the resumable operation")

"""Read-only preflight checks for installer clients and targets."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Literal

from pydantic import Field

from pawflow_installer.commands import CommandSpec, doctor_command
from pawflow_installer.models import InstallRequest, StrictModel
from pawflow_installer.transports.base import InstallTransport

CheckStatus = Literal["pass", "warning", "failure"]


class PreflightCheck(StrictModel):
    check_id: str
    status: CheckStatus
    message: str
    evidence: dict[str, str] = Field(default_factory=dict)


class PreflightReport(StrictModel):
    checks: list[PreflightCheck]

    @property
    def passed(self) -> bool:
        return not any(check.status == "failure" for check in self.checks)

    def as_evidence(self) -> dict:
        return {
            "passed": self.passed,
            "checks": [check.model_dump(mode="json") for check in self.checks],
        }


def _check(check_id: str, ok: bool, success: str, failure: str, **evidence: str) -> PreflightCheck:
    return PreflightCheck(
        check_id=check_id,
        status="pass" if ok else "failure",
        message=success if ok else failure,
        evidence=evidence,
    )


def client_preflight(request: InstallRequest, state_root: Path) -> PreflightReport:
    system = sys.platform
    supported = system.startswith(("linux", "win", "darwin"))
    state_parent = state_root
    while not state_parent.exists() and state_parent != state_parent.parent:
        state_parent = state_parent.parent
    writable_state_parent = state_parent.is_dir() and os.access(state_parent, os.W_OK)
    checks = [
        _check(
            "client_os",
            supported,
            f"Supported client platform: {system}",
            f"Unsupported client platform: {system}",
            platform=system,
        ),
        _check(
            "state_parent",
            writable_state_parent,
            "Installer state parent is writable",
            f"Installer state cannot be created below: {state_parent}",
            path=str(state_parent),
        ),
    ]
    if request.target.kind == "ssh":
        checks.append(_check(
            "openssh",
            shutil.which("ssh") is not None and shutil.which("scp") is not None,
            "OpenSSH client and SCP are available",
            "OpenSSH client and SCP are required for an SSH target",
        ))
    if request.relay_desktop.install:
        checks.append(_check(
            "relay_keychain",
            _relay_keychain_available(),
            "A supported OS credential store is available",
            "Relay Desktop association requires an OS credential store",
        ))
    return PreflightReport(checks=checks)


def _relay_keychain_available() -> bool:
    try:
        import keyring
        backend = keyring.get_keyring()
        return float(getattr(backend, "priority", 0)) > 0
    except (ImportError, RuntimeError):
        return False


def target_preflight(
    request: InstallRequest,
    transport: InstallTransport,
    scripts_root: str | None = None,
) -> PreflightReport:
    system, architecture = transport.platform()
    checks = [
        _check(
            "target_os",
            system in {"linux", "macos", "windows"},
            f"Supported target platform: {system}/{architecture}",
            f"Unsupported target platform: {system}/{architecture}",
            platform=system,
            architecture=architecture,
        ),
        _check(
            "docker_command",
            transport.command_exists("docker"),
            "Docker command is available",
            "Docker is required on the target",
        ),
    ]
    if checks[-1].status == "pass":
        result = transport.run(CommandSpec(("docker", "info"), mutating=False))
        checks.append(_check(
            "docker_daemon",
            result.ok,
            "Docker daemon is reachable",
            result.stderr.strip() or "Docker daemon is not reachable",
        ))
    if request.install.source == "source":
        checks.append(_check(
            "git",
            transport.command_exists("git"),
            "Git is available for source installation",
            "Git is required for source installation",
        ))
    if scripts_root:
        result = transport.run(doctor_command(request, system, scripts_root))
        checks.append(_check(
            "canonical_doctor",
            result.ok,
            "Canonical PawFlow doctor passed",
            result.stderr.strip() or result.stdout.strip() or "PawFlow doctor failed",
        ))
    else:
        checks.append(PreflightCheck(
            check_id="canonical_doctor",
            status="warning",
            message="Canonical doctor will run from the verified remote payload before install",
            evidence={},
        ))
    return PreflightReport(checks=checks)

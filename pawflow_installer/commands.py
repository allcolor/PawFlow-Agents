"""Pure command builders for canonical PawFlow installer scripts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from pawflow_installer.models import InstallRequest


@dataclass(frozen=True)
class CommandSpec:
    argv: tuple[str, ...]
    mutating: bool
    cwd: str | None = None
    env: Mapping[str, str] = field(default_factory=dict)

    def display(self) -> str:
        return " ".join(self.argv)


def doctor_command(request: InstallRequest, target_os: str, scripts_root: str) -> CommandSpec:
    port = str(request.install.port)
    if target_os == "windows":
        argv = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(Path(scripts_root) / "doctor-pawflow.ps1"),
            "-Port",
            port,
        ]
        if request.install.source == "source":
            argv.append("-Source")
    else:
        argv = [
            "bash",
            str(Path(scripts_root) / "doctor-pawflow.sh"),
            "--port",
            port,
        ]
        if request.install.source == "source":
            argv.append("--source")
    return CommandSpec(tuple(argv), mutating=False)


def server_install_command(
    request: InstallRequest, target_os: str, scripts_root: str
) -> CommandSpec:
    install = request.install
    if target_os == "windows":
        if install.source == "source" or install.native or install.skip_apparmor:
            raise ValueError(
                "native Windows supports published Docker installs only; use WSL2 for source/native options"
            )
        argv = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(Path(scripts_root) / "install-pawflow.ps1"),
            "-Port",
            str(install.port),
            "-PawFlowHome",
            install.pawflow_home,
            "-PullImages",
        ]
        if install.version:
            argv.extend(["-Version", install.version])
        if install.keep_old_images:
            argv.append("-KeepOldImages")
        return CommandSpec(tuple(argv), mutating=True)

    argv = [
        "bash",
        str(Path(scripts_root) / "install-pawflow.sh"),
        "--port",
        str(install.port),
        "--home",
        install.pawflow_home,
    ]
    argv.append("--from-source" if install.source == "source" else "--pull-images")
    if install.version:
        argv.extend(["--version", install.version])
    if install.native:
        argv.append("--native")
    if install.skip_apparmor:
        argv.append("--skip-apparmor")
    if install.keep_old_images:
        argv.append("--keep-old-images")
    return CommandSpec(tuple(argv), mutating=True)

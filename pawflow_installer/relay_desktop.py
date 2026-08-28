"""Relay Desktop artifact, configuration, autostart and verification adapters."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import plistlib
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from pawflow_installer.commands import CommandSpec
from pawflow_installer.models import RelayDesktopConfig


@dataclass(frozen=True)
class RelayDesktopArtifact:
    name: str
    url: str
    sha256: str | None = None


@dataclass(frozen=True)
class GeneratedAutostart:
    path: Path | None
    content: bytes | None
    commands: tuple[CommandSpec, ...]


def normalized_platform(
    system: str | None = None, machine: str | None = None
) -> tuple[str, str]:
    system = (system or platform.system()).lower()
    machine = (machine or platform.machine()).lower()
    system = {"darwin": "macos", "win32": "windows"}.get(system, system)
    machine = {
        "amd64": "x86_64",
        "x64": "x86_64",
        "arm64": "aarch64",
    }.get(machine, machine)
    if system not in {"windows", "linux", "macos"}:
        raise ValueError(f"unsupported Relay Desktop platform: {system}")
    if machine not in {"x86_64", "aarch64"}:
        raise ValueError(f"unsupported Relay Desktop architecture: {machine}")
    return system, machine


def select_artifact(
    assets: Iterable[RelayDesktopArtifact],
    *,
    system: str | None = None,
    machine: str | None = None,
) -> RelayDesktopArtifact:
    system, machine = normalized_platform(system, machine)
    preferences = {
        "windows": (".exe", ".zip"),
        "linux": (".appimage", ".deb", ".tar.gz"),
        "macos": (".dmg", ".zip"),
    }[system]
    aliases = {
        "x86_64": ("x86_64", "amd64", "x64"),
        "aarch64": ("aarch64", "arm64"),
    }[machine]
    candidates = [
        asset for asset in assets
        if "pawflow relay desktop" in asset.name.lower().replace("-", " ")
        and any(alias in asset.name.lower() for alias in aliases)
    ]
    for suffix in preferences:
        for asset in candidates:
            if asset.name.lower().endswith(suffix):
                return asset
    raise ValueError(f"no Relay Desktop artifact for {system}/{machine}")


def verify_artifact(path: Path, expected_sha256: str) -> None:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual.lower() != expected_sha256.lower():
        raise ValueError(
            f"Relay Desktop checksum mismatch: expected {expected_sha256}, got {actual}"
        )


def install_commands(
    artifact: Path, system: str, install_root: Path
) -> list[CommandSpec]:
    name = artifact.name.lower()
    if system == "windows":
        if name.endswith(".exe"):
            return [CommandSpec((str(artifact),), mutating=True)]
        if name.endswith(".zip"):
            return [CommandSpec((
                "powershell", "-NoProfile", "-Command",
                "Expand-Archive -LiteralPath $args[0] -DestinationPath $args[1] -Force",
                str(artifact), str(install_root),
            ), mutating=True)]
    if system == "linux":
        if name.endswith(".appimage"):
            destination = install_root / "PawFlowRelayDesktop.AppImage"
            return [CommandSpec((
                "install", "-D", "-m", "755", str(artifact), str(destination)
            ), mutating=True)]
        if name.endswith(".deb"):
            return [CommandSpec(("dpkg", "-i", str(artifact)), mutating=True)]
        if name.endswith(".tar.gz"):
            return [CommandSpec((
                "tar", "-xzf", str(artifact), "-C", str(install_root)
            ), mutating=True)]
    if system == "macos":
        if name.endswith(".dmg"):
            return [CommandSpec(("open", str(artifact)), mutating=True)]
        if name.endswith(".zip"):
            return [CommandSpec((
                "ditto", "-x", "-k", str(artifact), str(install_root)
            ), mutating=True)]
    raise ValueError(f"unsupported Relay Desktop artifact: {artifact.name}")


def workspace_names(config: RelayDesktopConfig) -> list[tuple[str, str]]:
    return [
        (config.workspace_name if index == 1 else f"{config.workspace_name}-{index}", path)
        for index, path in enumerate(config.paths, start=1)
    ]


def server_add_command(config: RelayDesktopConfig) -> CommandSpec:
    return CommandSpec((
        "pawflow-relay", "server", "add",
        str(config.server_name), str(config.server_url),
    ), mutating=True)


def server_login_command(config: RelayDesktopConfig) -> CommandSpec:
    return CommandSpec((
        "pawflow-relay", "server", "login", str(config.server_name)
    ), mutating=True)


def workspace_add_commands(config: RelayDesktopConfig) -> list[CommandSpec]:
    commands = []
    capabilities = set(config.capabilities)
    mode = "rw" if "filesystem.write" in capabilities else "ro"
    for name, path in workspace_names(config):
        argv = [
            "pawflow-relay", "workspace", "add", name,
            "--server", str(config.server_name),
            "--path", path,
            "--mode", mode,
        ]
        if not capabilities.intersection({"shell.exec", "container.exec", "automation"}):
            argv.append("--no-exec")
        if "desktop.control" not in capabilities:
            argv.append("--no-remote-desktop")
        if "host.local" in capabilities:
            argv.append("--allow-local")
        if "service.tunnels" in capabilities:
            argv.append("--allow-service-tunnels")
        commands.append(CommandSpec(tuple(argv), mutating=True))
    return commands


def relay_start_command(workspace_name: str) -> CommandSpec:
    return CommandSpec(("pawflow-relay", "start", workspace_name), mutating=True)


def relay_verify_command(workspace_name: str) -> CommandSpec:
    return CommandSpec((
        "pawflow-relay", "--json", "verify", workspace_name
    ), mutating=False)


def render_systemd_unit(executable: str, workspace_name: str) -> str:
    return (
        "[Unit]\nDescription=PawFlow Relay Desktop workspace\nAfter=network-online.target\n\n"
        "[Service]\nType=simple\n"
        f"ExecStart={executable} start {workspace_name}\n"
        "Restart=on-failure\n\n[Install]\nWantedBy=default.target\n"
    )


def render_launch_agent(executable: str, workspace_name: str) -> bytes:
    return plistlib.dumps({
        "Label": f"org.allcolor.pawflow.relay.{workspace_name}",
        "ProgramArguments": [executable, "start", workspace_name],
        "RunAtLoad": True,
        "KeepAlive": True,
    })


def windows_autostart_command(executable: str, workspace_name: str) -> CommandSpec:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", workspace_name)
    action = f'"{executable}" start "{workspace_name}"'
    return CommandSpec((
        "schtasks", "/Create", "/SC", "ONLOGON", "/TN",
        f"PawFlow Relay {safe}", "/TR", action, "/F",
    ), mutating=True)


def autostart_plan(
    system: str,
    executable: str,
    workspace_name: str,
    *,
    home: Path | None = None,
    uid: int | None = None,
) -> GeneratedAutostart:
    home = home or Path.home()
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", workspace_name)
    if system == "windows":
        return GeneratedAutostart(
            path=None,
            content=None,
            commands=(windows_autostart_command(executable, workspace_name),),
        )
    if system == "linux":
        unit_name = f"pawflow-relay-{safe}.service"
        path = home / ".config" / "systemd" / "user" / unit_name
        return GeneratedAutostart(
            path=path,
            content=render_systemd_unit(executable, workspace_name).encode("utf-8"),
            commands=(
                CommandSpec(("systemctl", "--user", "daemon-reload"), mutating=True),
                CommandSpec(
                    ("systemctl", "--user", "enable", "--now", unit_name),
                    mutating=True,
                ),
            ),
        )
    if system == "macos":
        label = f"org.allcolor.pawflow.relay.{safe}"
        path = home / "Library" / "LaunchAgents" / f"{label}.plist"
        user_id = os.getuid() if uid is None else uid
        return GeneratedAutostart(
            path=path,
            content=render_launch_agent(executable, workspace_name),
            commands=(
                CommandSpec(
                    ("launchctl", "bootstrap", f"gui/{user_id}", str(path)),
                    mutating=True,
                ),
            ),
        )
    raise ValueError(f"unsupported autostart platform: {system}")


def write_autostart(definition: GeneratedAutostart) -> None:
    if definition.path is None or definition.content is None:
        return
    definition.path.parent.mkdir(parents=True, exist_ok=True)
    temporary = definition.path.parent / (
        f".{definition.path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with open(temporary, "xb") as handle:
            handle.write(definition.content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, definition.path)
    finally:
        temporary.unlink(missing_ok=True)


def parse_verification(payload: str) -> dict:
    data = json.loads(payload)
    if not isinstance(data, dict) or "connected" not in data or "relay_id" not in data:
        raise ValueError("invalid relay verification response")
    return {
        "relay_id": str(data["relay_id"]),
        "connected": bool(data["connected"]),
    }


def broad_shared_paths(config: RelayDesktopConfig) -> list[str]:
    broad = []
    home = str(Path.home().resolve())
    for raw in config.paths:
        resolved = str(Path(raw).expanduser().resolve())
        drive, tail = os.path.splitdrive(resolved)
        if resolved == os.path.sep or (drive and tail in {os.path.sep, ""}) or resolved == home:
            broad.append(raw)
    return broad

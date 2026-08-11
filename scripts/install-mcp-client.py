#!/usr/bin/env python3
"""Guided installer for the standalone PawFlow MCP stdio client."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import getpass
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import Iterable
from urllib.parse import urlparse


CLIENT_ALIASES = {
    "cc": "cc",
    "claude": "cc",
    "claude-code": "cc",
    "codex": "codex",
    "agy": "agy",
    "antigravity": "agy",
}
CLIENT_LABELS = {
    "cc": "Claude Code",
    "codex": "Codex",
    "agy": "Agy",
}
_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class InstallRequest:
    name: str
    url: str
    api_key: str
    gateway_key: str
    relay_dir: Path
    clients: tuple[str, ...]
    readonly: bool = False
    allow_exec: bool = False


def default_install_root() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            raise ValueError("LOCALAPPDATA is required on Windows")
        return Path(base) / "PawFlow" / "MCP"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "PawFlow" / "MCP"
    base = os.environ.get("XDG_DATA_HOME")
    return (Path(base) if base else Path.home() / ".local" / "share") / "pawflow" / "mcp"


def _validate_request(request: InstallRequest) -> InstallRequest:
    if not _NAME_RE.fullmatch(request.name):
        raise ValueError("Server name may contain only letters, digits, dot, dash, and underscore")
    parsed = urlparse(request.url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Published URL must be an absolute HTTP(S) URL")
    if "/mcp/" not in parsed.path:
        raise ValueError("Published URL must contain /mcp/")
    if not request.api_key:
        raise ValueError("A PawFlow MCP API key is required")
    if not request.relay_dir.expanduser().is_dir():
        raise ValueError(f"Relay directory does not exist: {request.relay_dir}")
    if not request.clients:
        raise ValueError("Select at least one client: cc, codex, or agy")
    unknown = set(request.clients) - set(CLIENT_LABELS)
    if unknown:
        raise ValueError(f"Unsupported clients: {', '.join(sorted(unknown))}")
    return request


def parse_clients(value: str | Iterable[str]) -> tuple[str, ...]:
    raw = value.split(",") if isinstance(value, str) else list(value)
    clients: list[str] = []
    for item in raw:
        key = str(item).strip().lower()
        if not key:
            continue
        client = CLIENT_ALIASES.get(key)
        if not client:
            raise ValueError(f"Unsupported client {item!r}; use cc, codex, or agy")
        if client not in clients:
            clients.append(client)
    return tuple(clients)


def _safe_chmod(path: Path, mode: int) -> None:
    try:
        path.chmod(mode)
    except OSError:
        pass


def _backup(path: Path) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    candidate = path.with_name(f"{path.name}.bak-{stamp}-{time.time_ns()}")
    shutil.copy2(path, candidate)
    return candidate


def _atomic_write(path: Path, content: str, *, secret: bool = False,
                  backup: bool = True) -> tuple[bool, Path | None]:
    encoded = content.encode("utf-8")
    if path.exists() and path.read_bytes() == encoded:
        if secret:
            _safe_chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        return False, None
    path.parent.mkdir(parents=True, exist_ok=True)
    if secret:
        _safe_chmod(path.parent, stat.S_IRWXU)
    backup_path = _backup(path) if backup and path.exists() else None
    handle = tempfile.NamedTemporaryFile(
        mode="wb", prefix=f".{path.name}.", dir=path.parent, delete=False)
    tmp = Path(handle.name)
    try:
        with handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        _safe_chmod(
            tmp,
            stat.S_IRUSR | stat.S_IWUSR if secret
            else stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH,
        )
        os.replace(tmp, path)
        if secret:
            _safe_chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    finally:
        if tmp.exists():
            tmp.unlink()
    return True, backup_path


def _load_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return dict(default)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read JSON configuration {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON configuration must contain an object: {path}")
    return value


def _write_json(path: Path, value: dict, *, secret: bool = False,
                backup: bool = True) -> tuple[bool, Path | None]:
    return _atomic_write(
        path,
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        secret=secret,
        backup=backup,
    )


def _replace_runtime(bundle_root: Path, install_root: Path) -> None:
    source = bundle_root / "runtime"
    launcher_source = bundle_root / "launcher.py"
    client_source = bundle_root / "client.py"
    if not (source / "pawflow_relay" / "mcp_stdio.py").is_file():
        raise ValueError(f"Bundle runtime is incomplete: {source}")
    if not launcher_source.is_file():
        raise ValueError(f"Bundle launcher is missing: {launcher_source}")
    if not client_source.is_file():
        raise ValueError(f"Bundle client launcher is missing: {client_source}")
    install_root.mkdir(parents=True, exist_ok=True)
    target = install_root / "runtime"
    staged = install_root / f".runtime-new-{os.getpid()}-{time.time_ns()}"
    old = install_root / f".runtime-old-{os.getpid()}-{time.time_ns()}"
    shutil.copytree(source, staged)
    try:
        if target.exists():
            os.replace(target, old)
        os.replace(staged, target)
    except Exception:
        if not target.exists() and old.exists():
            os.replace(old, target)
        raise
    finally:
        if staged.exists():
            shutil.rmtree(staged)
        if old.exists():
            shutil.rmtree(old)
    launcher_target = install_root / "launch.py"
    tmp_launcher = install_root / f".launch-{os.getpid()}-{time.time_ns()}.py"
    shutil.copy2(launcher_source, tmp_launcher)
    os.replace(tmp_launcher, launcher_target)
    _safe_chmod(
        launcher_target,
        stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
        | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH,
    )
    client_target = install_root / "run-client.py"
    tmp_client = install_root / f".client-{os.getpid()}-{time.time_ns()}.py"
    shutil.copy2(client_source, tmp_client)
    os.replace(tmp_client, client_target)
    _safe_chmod(
        client_target,
        stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
        | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH,
    )


def _entry(install_root: Path, profile_path: Path, python: str,
           relay_dir: Path, client: str) -> dict:
    return {
        "type": "stdio",
        "command": str(Path(python).resolve()),
        "args": [
            str((install_root / "launch.py").resolve()),
            "--profile",
            str(profile_path.resolve()),
            "--client-name",
            CLIENT_LABELS[client],
        ],
        "cwd": str(relay_dir.resolve()),
    }


def _agy_list_entry(name: str, entry: dict) -> dict:
    return {"serverName": name, **entry, "disabled": False}


def _write_agy_home(session_dir: Path, name: str, entry: dict) -> list[Path]:
    """Write an isolated Agy/Gemini home for one local client instance."""
    results: list[Path] = []
    gemini = session_dir / "agy-home" / ".gemini"
    cli_dir = gemini / "antigravity-cli"
    cli_settings_path = cli_dir / "settings.json"
    settings = {
        "mcpServers": {name: entry},
        "allowMCPServers": [name],
        "mcp": {"allowed": [name]},
        "permissions": {"allow": [f"mcp({name}/*)", f"mcp_{name}_*"]},
    }
    _write_json(cli_settings_path, settings, backup=False)
    results.append(cli_settings_path)
    list_path = cli_dir / "mcp_config.json"
    _write_json(
        list_path, {"mcpServers": [_agy_list_entry(name, entry)]},
        backup=False)
    results.append(list_path)
    settings_path = gemini / "settings.json"
    _write_json(settings_path, settings, backup=False)
    results.append(settings_path)
    root_config_path = gemini / "mcp_config.json"
    _write_json(
        root_config_path, {"mcpServers": {name: entry}}, backup=False)
    results.append(root_config_path)
    return results


def install(request: InstallRequest, *, bundle_root: Path,
            install_root: Path | None = None, python: str | None = None) -> dict:
    request = _validate_request(request)
    bundle_root = bundle_root.resolve()
    install_root = (install_root or default_install_root()).expanduser().resolve()
    python = python or sys.executable
    _replace_runtime(bundle_root, install_root)

    session_dir = install_root / "sessions" / request.name
    profile_path = session_dir / "profile.json"
    profile = {
        "version": 1,
        "url": request.url.rstrip("/"),
        "api_key": request.api_key,
        "gateway_key": request.gateway_key,
        "relay_dir": str(request.relay_dir.expanduser().resolve()),
        "readonly": bool(request.readonly),
        "allow_exec": bool(request.allow_exec),
    }
    _write_json(profile_path, profile, secret=True, backup=False)

    relay_dir = request.relay_dir.expanduser().resolve()
    entries = {
        client: _entry(install_root, profile_path, python, relay_dir, client)
        for client in request.clients
    }
    generic_entry = {
        **_entry(install_root, profile_path, python, relay_dir, "cc"),
    }
    generic_entry["args"][-1] = "MCP Client"
    configs: list[str] = []
    generic_path = session_dir / "mcp.json"
    entry_path = session_dir / "entry.json"
    _write_json(generic_path, {"mcpServers": {
        request.name: generic_entry}}, backup=False)
    _write_json(entry_path, generic_entry, backup=False)
    configs.extend([str(generic_path), str(entry_path)])
    config_map = {"generic": str(generic_path)}
    if "cc" in entries:
        cc_path = session_dir / "claude.mcp.json"
        _write_json(
            cc_path, {"mcpServers": {request.name: entries["cc"]}},
            backup=False)
        config_map["cc"] = str(cc_path)
        configs.append(str(cc_path))
    if "agy" in entries:
        agy_paths = _write_agy_home(session_dir, request.name, entries["agy"])
        config_map["agy_home"] = str(session_dir / "agy-home")
        configs.extend(str(path) for path in agy_paths)
    session_path = session_dir / "session.json"
    session = {
        "version": 1,
        "name": request.name,
        "relay_dir": str(relay_dir),
        "clients": list(request.clients),
        "entries": entries,
        "configs": config_map,
    }
    _write_json(session_path, session, backup=False)
    configs.append(str(session_path))
    client_launcher = install_root / "run-client.py"
    launch_commands = {
        client: [
            str(Path(python).resolve()), str(client_launcher),
            "--session", str(session_path), "--client", client,
        ]
        for client in request.clients
    }
    return {
        "name": request.name,
        "install_root": str(install_root),
        "profile": str(profile_path),
        "clients": list(request.clients),
        "configs": configs,
        "session": str(session_path),
        "launch_commands": launch_commands,
        "relay_dir": str(relay_dir),
        "readonly": request.readonly,
        "allow_exec": request.allow_exec,
    }


def _prompt(label: str, default: str = "", *, secret: bool = False,
            required: bool = False) -> str:
    suffix = f" [{default}]" if default and not secret else ""
    while True:
        value = (getpass.getpass if secret else input)(f"{label}{suffix}: ").strip()
        if value:
            return value
        if default:
            return default
        if not required:
            return ""
        print(f"{label} is required.", file=sys.stderr)


def _prompt_bool(label: str, default: bool = False) -> bool:
    suffix = "Y/n" if default else "y/N"
    while True:
        value = input(f"{label} [{suffix}]: ").strip().lower()
        if not value:
            return default
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False
        print("Answer y or n.", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install and configure the PawFlow MCP stdio client")
    parser.add_argument("--name", default="pawflow")
    parser.add_argument("--url", default=os.environ.get("PAWFLOW_MCP_URL", ""))
    parser.add_argument("--relay-dir", default=str(Path.cwd()))
    parser.add_argument("--clients", default="cc,codex,agy")
    parser.add_argument("--readonly", action="store_true")
    parser.add_argument("--allow-exec", action="store_true")
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--api-key-env", default="PAWFLOW_MCP_API_KEY")
    parser.add_argument("--gateway-key-env", default="PAWFLOW_GATEWAY_KEY")
    parser.add_argument("--install-dir")
    parser.add_argument("--python", default=sys.executable)
    return parser


def main(argv: list[str] | None = None) -> int:
    if sys.version_info < (3, 10):
        print("Python 3.10 or newer is required.", file=sys.stderr)
        return 2
    args = build_parser().parse_args(argv)
    if args.non_interactive:
        api_key = os.environ.get(args.api_key_env, "")
        gateway_key = os.environ.get(args.gateway_key_env, "")
        name = args.name
        url = args.url
        relay_dir = args.relay_dir
        clients = args.clients
        readonly = args.readonly
        allow_exec = args.allow_exec
    else:
        print("PawFlow MCP client guided installation")
        print("Secrets are stored only in a private local profile.")
        name = _prompt("Server name", args.name, required=True)
        url = _prompt("Published PawFlow MCP URL", args.url, required=True)
        api_key = _prompt("PawFlow MCP API key", secret=True, required=True)
        gateway_key = _prompt("Private Gateway key (optional)", secret=True)
        relay_dir = _prompt("Project directory to share", args.relay_dir, required=True)
        clients = _prompt("Clients (cc,codex,agy)", args.clients, required=True)
        readonly = _prompt_bool("Make the relay read-only", args.readonly)
        allow_exec = _prompt_bool("Allow shell execution through the relay", args.allow_exec)
    try:
        request = InstallRequest(
            name=name,
            url=url,
            api_key=api_key,
            gateway_key=gateway_key,
            relay_dir=Path(relay_dir),
            clients=parse_clients(clients),
            readonly=readonly,
            allow_exec=allow_exec,
        )
        result = install(
            request,
            bundle_root=Path(__file__).resolve().parent,
            install_root=Path(args.install_dir) if args.install_dir else None,
            python=args.python,
        )
    except (OSError, ValueError) as exc:
        print(f"Installation failed: {exc}", file=sys.stderr)
        return 1
    print(f"Installed PawFlow MCP client {result['name']!r}.")
    print(f"Runtime: {result['install_root']}")
    print(f"Private profile: {result['profile']}")
    print(f"Configured clients: {', '.join(result['clients'])}")
    print(f"Shared directory: {result['relay_dir']}")
    print("Session-bound launch commands:")
    for client, command in result["launch_commands"].items():
        print(f"  {client}: {subprocess.list2cmdline(command)}")
    print("Use these launch commands instead of starting the client normally.")
    print("One session bundle maps to exactly one published conversation and agent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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
    if not (source / "pawflow_relay" / "mcp_stdio.py").is_file():
        raise ValueError(f"Bundle runtime is incomplete: {source}")
    if not launcher_source.is_file():
        raise ValueError(f"Bundle launcher is missing: {launcher_source}")
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


def _install_claude(home: Path, name: str, entry: dict) -> tuple[Path, Path | None]:
    path = home / ".claude.json"
    config = _load_json(path, {})
    servers = config.get("mcpServers")
    if servers is None:
        servers = {}
    if not isinstance(servers, dict):
        raise ValueError(f"mcpServers must be an object in {path}")
    servers[name] = entry
    config["mcpServers"] = servers
    _changed, backup = _write_json(path, config)
    return path, backup


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


def _codex_block(name: str, entry: dict) -> str:
    args = ", ".join(_toml_string(str(value)) for value in entry["args"])
    return (
        f"# BEGIN PAWFLOW MCP: {name}\n"
        f"[mcp_servers.{_toml_string(name)}]\n"
        f"command = {_toml_string(entry['command'])}\n"
        f"args = [{args}]\n"
        f"cwd = {_toml_string(entry['cwd'])}\n"
        f"# END PAWFLOW MCP: {name}\n"
    )


def _install_codex(home: Path, name: str, entry: dict) -> tuple[Path, Path | None]:
    path = home / ".codex" / "config.toml"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    marker = re.compile(
        rf"(?ms)^# BEGIN PAWFLOW MCP: {re.escape(name)}\n.*?"
        rf"^# END PAWFLOW MCP: {re.escape(name)}\n?"
    )
    block = _codex_block(name, entry)
    if marker.search(text):
        updated = marker.sub(block, text, count=1)
    else:
        header = re.compile(
            rf"(?m)^\[mcp_servers\.(?:{re.escape(name)}|"
            rf"{re.escape(_toml_string(name))})\]\s*$")
        if header.search(text):
            raise ValueError(
                f"{path} already contains an unmanaged MCP server named {name!r}; "
                "remove or rename that table before installing")
        updated = text
        if updated and not updated.endswith("\n"):
            updated += "\n"
        if updated:
            updated += "\n"
        updated += block
    _changed, backup = _atomic_write(path, updated)
    return path, backup


def _append_unique(values: object, additions: Iterable[str]) -> list[str]:
    result = [str(value) for value in values] if isinstance(values, list) else []
    for addition in additions:
        if addition not in result:
            result.append(addition)
    return result


def _agy_list_entry(name: str, entry: dict) -> dict:
    return {"serverName": name, **entry, "disabled": False}


def _install_agy(home: Path, name: str, entry: dict) -> list[tuple[Path, Path | None]]:
    results: list[tuple[Path, Path | None]] = []
    gemini = home / ".gemini"
    cli_dir = gemini / "antigravity-cli"

    cli_settings_path = cli_dir / "settings.json"
    cli_settings = _load_json(cli_settings_path, {})
    cli_servers = cli_settings.get("mcpServers")
    if cli_servers is None:
        cli_servers = {}
    if not isinstance(cli_servers, dict):
        raise ValueError(f"mcpServers must be an object in {cli_settings_path}")
    cli_servers[name] = entry
    cli_settings["mcpServers"] = cli_servers
    cli_settings["allowMCPServers"] = _append_unique(
        cli_settings.get("allowMCPServers"), [name])
    mcp_policy = cli_settings.get("mcp")
    if not isinstance(mcp_policy, dict):
        mcp_policy = {}
    mcp_policy["allowed"] = _append_unique(mcp_policy.get("allowed"), [name])
    cli_settings["mcp"] = mcp_policy
    permissions = cli_settings.get("permissions")
    if not isinstance(permissions, dict):
        permissions = {}
    permissions["allow"] = _append_unique(
        permissions.get("allow"),
        [f"mcp({name}/*)", f"mcp_{name}_*"],
    )
    cli_settings["permissions"] = permissions
    _changed, backup = _write_json(cli_settings_path, cli_settings)
    results.append((cli_settings_path, backup))

    list_path = cli_dir / "mcp_config.json"
    list_config = _load_json(list_path, {})
    rows = list_config.get("mcpServers")
    if rows is None:
        rows = []
    if not isinstance(rows, list):
        raise ValueError(f"mcpServers must be an array in {list_path}")
    rows = [
        row for row in rows
        if not isinstance(row, dict) or str(row.get("serverName") or "") != name
    ]
    rows.append(_agy_list_entry(name, entry))
    list_config["mcpServers"] = rows
    _changed, backup = _write_json(list_path, list_config)
    results.append((list_path, backup))

    settings_path = gemini / "settings.json"
    settings = _load_json(settings_path, {})
    settings_servers = settings.get("mcpServers")
    if settings_servers is None:
        settings_servers = {}
    if not isinstance(settings_servers, dict):
        raise ValueError(f"mcpServers must be an object in {settings_path}")
    settings_servers[name] = entry
    settings["mcpServers"] = settings_servers
    settings_permissions = settings.get("permissions")
    if not isinstance(settings_permissions, dict):
        settings_permissions = {}
    settings_permissions["allow"] = _append_unique(
        settings_permissions.get("allow"),
        [f"mcp({name}/*)", f"mcp_{name}_*"],
    )
    settings["permissions"] = settings_permissions
    _changed, backup = _write_json(settings_path, settings)
    results.append((settings_path, backup))

    root_config_path = gemini / "mcp_config.json"
    root_config = _load_json(root_config_path, {})
    root_servers = root_config.get("mcpServers")
    if root_servers is None:
        root_servers = {}
    if not isinstance(root_servers, dict):
        raise ValueError(f"mcpServers must be an object in {root_config_path}")
    root_servers[name] = entry
    root_config["mcpServers"] = root_servers
    _changed, backup = _write_json(root_config_path, root_config)
    results.append((root_config_path, backup))
    return results


def install(request: InstallRequest, *, bundle_root: Path,
            install_root: Path | None = None, home: Path | None = None,
            python: str | None = None) -> dict:
    request = _validate_request(request)
    bundle_root = bundle_root.resolve()
    home = (home or Path.home()).expanduser().resolve()
    install_root = (install_root or default_install_root()).expanduser().resolve()
    python = python or sys.executable
    _replace_runtime(bundle_root, install_root)

    profile_dir = install_root / "profiles"
    profile_path = profile_dir / f"{request.name}.json"
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

    configs: list[str] = []
    backups: list[str] = []
    for client in request.clients:
        entry = _entry(
            install_root, profile_path, python,
            request.relay_dir.expanduser(), client)
        if client == "cc":
            path, backup = _install_claude(home, request.name, entry)
            pairs = [(path, backup)]
        elif client == "codex":
            path, backup = _install_codex(home, request.name, entry)
            pairs = [(path, backup)]
        else:
            pairs = _install_agy(home, request.name, entry)
        for path, backup in pairs:
            configs.append(str(path))
            if backup:
                backups.append(str(backup))
    return {
        "name": request.name,
        "install_root": str(install_root),
        "profile": str(profile_path),
        "clients": list(request.clients),
        "configs": configs,
        "backups": backups,
        "relay_dir": str(request.relay_dir.expanduser().resolve()),
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
    parser.add_argument("--home")
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
            home=Path(args.home) if args.home else None,
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
    if result["backups"]:
        print("Backups:")
        for path in result["backups"]:
            print(f"  {path}")
    print("Restart each configured client before using the new MCP server.")
    print("Only one client instance may use a published conversation at a time.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

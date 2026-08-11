#!/usr/bin/env python3
"""Launch one local AI client with one isolated PawFlow MCP publication."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any


CLIENT_BINARIES = {
    "cc": ("claude",),
    "codex": ("codex",),
    "agy": ("agy", "gemini"),
}


def _load_session(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read session manifest {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("session manifest must contain an object")
    if not value.get("relay_dir") or not isinstance(value.get("entries"), dict):
        raise ValueError("session manifest is missing relay_dir or entries")
    return value


def _binary(client: str, override: str = "") -> str:
    if override:
        return override
    for candidate in CLIENT_BINARIES[client]:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return CLIENT_BINARIES[client][0]


def _toml_string(value: Any) -> str:
    return json.dumps(str(value), ensure_ascii=True)


def _toml_array(values: list) -> str:
    return "[" + ", ".join(_toml_string(value) for value in values) + "]"


def _codex_mcp_override(name: str, entry: dict) -> str:
    fields = [
        f"command = {_toml_string(entry['command'])}",
        f"args = {_toml_array(list(entry.get('args') or []))}",
        f"cwd = {_toml_string(entry['cwd'])}",
    ]
    server = "{ " + ", ".join(fields) + " }"
    return "mcp_servers={ " + _toml_string(name) + " = " + server + " }"


def build_launch(
        session_path: Path, client: str, passthrough: list[str],
        binary: str = "") -> tuple[list[str], dict, str]:
    session_path = session_path.expanduser().resolve()
    session = _load_session(session_path)
    entries = session["entries"]
    if client not in CLIENT_BINARIES or client not in entries:
        raise ValueError(f"client is not configured for this session: {client}")
    executable = _binary(client, binary)
    relay_dir = str(Path(session["relay_dir"]).expanduser().resolve())
    env = dict(os.environ)
    if client == "cc":
        config = str(Path(session["configs"]["cc"]).resolve())
        command = [
            executable, "--mcp-config", config, "--strict-mcp-config",
            *passthrough,
        ]
    elif client == "codex":
        command = [
            executable, "-C", relay_dir, "-c",
            _codex_mcp_override(str(session["name"]), entries["codex"]),
            *passthrough,
        ]
    else:
        isolated_home = str(Path(session["configs"]["agy_home"]).resolve())
        env["HOME"] = isolated_home
        env["USERPROFILE"] = isolated_home
        command = [executable, *passthrough]
    return command, env, relay_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Launch one client instance bound to one PawFlow conversation")
    parser.add_argument("--session", required=True)
    parser.add_argument("--client", required=True, choices=sorted(CLIENT_BINARIES))
    parser.add_argument("--binary", default="")
    parser.add_argument("client_args", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    passthrough = list(args.client_args)
    if passthrough[:1] == ["--"]:
        passthrough = passthrough[1:]
    try:
        command, env, cwd = build_launch(
            Path(args.session), args.client, passthrough, args.binary)
    except ValueError as exc:
        print(f"[pawflow-mcp] {exc}", file=sys.stderr)
        return 2
    try:
        return subprocess.call(command, cwd=cwd, env=env)
    except FileNotFoundError:
        print(
            f"[pawflow-mcp] client executable not found: {command[0]}",
            file=sys.stderr,
        )
        return 127


if __name__ == "__main__":
    raise SystemExit(main())

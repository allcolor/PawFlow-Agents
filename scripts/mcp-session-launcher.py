#!/usr/bin/env python3
"""Launch one isolated MCP client in a persistent, remotely injectable terminal."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import socketserver
import subprocess  # nosec B404 - this is the explicit client launcher
import sys
import threading
import time
from typing import Any


CLIENT_BINARIES = {
    "cc": ("claude",),
    "codex": ("codex",),
    "agy": ("agy", "gemini"),
    "opencode": ("opencode",),
    "jcode": ("jcode",),
    "pi": ("pi",),
    "hermes": ("hermes",),
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
        settings = str(Path(session["configs"]["cc_settings"]).resolve())
        command = [
            executable, "--mcp-config", config, "--strict-mcp-config",
            "--settings", settings, *passthrough,
        ]
    elif client == "codex":
        codex_home = str(Path(session["configs"]["codex_home"]).resolve())
        env["CODEX_HOME"] = codex_home
        command = [
            executable, "-C", relay_dir, "-c",
            _codex_mcp_override(str(session["name"]), entries["codex"]),
            *passthrough,
        ]
    elif client == "agy":
        isolated_home = str(Path(session["configs"]["agy_home"]).resolve())
        env["HOME"] = isolated_home
        env["USERPROFILE"] = isolated_home
        command = [executable, *passthrough]
    elif client == "opencode":
        isolated_home = str(Path(session["configs"]["opencode_home"]).resolve())
        env["OPENCODE_CONFIG"] = str(Path(isolated_home) / "opencode.json")
        env["OPENCODE_CONFIG_DIR"] = isolated_home
        command = [executable, relay_dir, *passthrough]
    elif client == "jcode":
        env["JCODE_HOME"] = str(Path(session["configs"]["jcode_home"]).resolve())
        command = [executable, *passthrough]
    elif client == "pi":
        isolated_home = str(Path(session["configs"]["pi_home"]).resolve())
        extension = str(Path(isolated_home) / "extensions" / "pawflow.js")
        env["PI_CODING_AGENT_DIR"] = isolated_home
        command = [executable, "--extension", extension, *passthrough]
    else:
        env["HERMES_HOME"] = str(Path(session["configs"]["hermes_home"]).resolve())
        command = [executable, *passthrough]
    return command, env, relay_dir


def terminal_session_name(session_path: Path, client: str) -> str:
    digest = hashlib.sha256(
        f"{session_path.expanduser().resolve()}:{client}".encode("utf-8")
    ).hexdigest()[:16]
    return f"pawflow-mcp-{client}-{digest}"


def _append_marker(path: Path, message_id: str, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "message_id": message_id,
        "prompt_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "created_at": time.time(),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, separators=(",", ":")) + "\n")
        handle.flush()


def _terminal_env(session_id: str, kind: str, target: str,
                  marker_path: Path, secret: str = "") -> dict[str, str]:
    values = {
        "PAWFLOW_MCP_TERMINAL_SESSION_ID": session_id,
        "PAWFLOW_MCP_TERMINAL_KIND": kind,
        "PAWFLOW_MCP_TERMINAL_TARGET": target,
        "PAWFLOW_MCP_TERMINAL_STATE_PATH": str(marker_path.resolve()),
    }
    if secret:
        values["PAWFLOW_MCP_TERMINAL_SECRET"] = secret
    return values


def _run_tmux(session_path: Path, client: str, command: list[str],
              env: dict, cwd: str, marker_path: Path) -> int:
    tmux = shutil.which("tmux")
    if not tmux:
        raise ValueError(
            "tmux is required for persistent PawFlow MCP terminals on POSIX")
    name = terminal_session_name(session_path, client)
    target = f"{name}:0.0"
    exists = subprocess.run(  # nosec B603
        [tmux, "has-session", "-t", name],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if not exists:
        terminal_env = _terminal_env(name, "tmux", target, marker_path)
        child_env = dict(env)
        child_env.update(terminal_env)
        exported = ["env", *[f"{key}={value}" for key, value in terminal_env.items()],
                    *command]
        created = subprocess.run(  # nosec B603
            [tmux, "new-session", "-d", "-s", name, "-c", cwd,
             shlex.join(exported)], env=child_env)
        if created.returncode:
            raise RuntimeError("tmux could not create the PawFlow MCP session")
    return subprocess.call([tmux, "attach-session", "-t", name], env=env)  # nosec B603


def _write_windows_console(text: str) -> None:
    """Inject Unicode key records into the inherited Windows console."""
    import ctypes
    from ctypes import wintypes
    import msvcrt

    class _Char(ctypes.Union):
        _fields_ = [("UnicodeChar", wintypes.WCHAR),
                    ("AsciiChar", ctypes.c_char)]

    class _KeyEvent(ctypes.Structure):
        _fields_ = [
            ("bKeyDown", wintypes.BOOL),
            ("wRepeatCount", wintypes.WORD),
            ("wVirtualKeyCode", wintypes.WORD),
            ("wVirtualScanCode", wintypes.WORD),
            ("uChar", _Char),
            ("dwControlKeyState", wintypes.DWORD),
        ]

    class _Event(ctypes.Union):
        _fields_ = [("KeyEvent", _KeyEvent),
                    ("padding", ctypes.c_byte * 16)]

    class _InputRecord(ctypes.Structure):
        _fields_ = [("EventType", wintypes.WORD), ("Event", _Event)]

    records = []
    for char in text + "\r":
        for down in (True, False):
            record = _InputRecord()
            record.EventType = 0x0001
            record.Event.KeyEvent.bKeyDown = bool(down)
            record.Event.KeyEvent.wRepeatCount = 1
            record.Event.KeyEvent.wVirtualKeyCode = 13 if char == "\r" else 0
            record.Event.KeyEvent.uChar.UnicodeChar = char
            records.append(record)
    array = (_InputRecord * len(records))(*records)
    written = wintypes.DWORD()
    handle = msvcrt.get_osfhandle(sys.stdin.fileno())
    if not ctypes.windll.kernel32.WriteConsoleInputW(  # type: ignore[attr-defined]
            handle, array, len(records), ctypes.byref(written)):
        raise OSError("WriteConsoleInputW failed")


class _LoopbackServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = False
    daemon_threads = True

    def __init__(self, secret: str, marker_path: Path):
        self.secret = secret
        self.marker_path = marker_path
        super().__init__(("127.0.0.1", 0), _LoopbackHandler)


class _LoopbackHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        try:
            request = json.loads(self.rfile.readline(65536).decode("utf-8"))
            if not secrets.compare_digest(
                    str(request.get("secret") or ""), self.server.secret):
                raise PermissionError("terminal injection secret mismatch")
            message_id = str(request.get("message_id") or "")
            content = base64.b64decode(
                str(request.get("content_b64") or ""), validate=True
            ).decode("utf-8")
            if not message_id or not content.strip():
                raise ValueError("message_id and non-empty content are required")
            _append_marker(self.server.marker_path, message_id, content)
            _write_windows_console(content)
            response = {"ok": True}
        except Exception as exc:
            response = {"ok": False, "error": str(exc)}
        self.wfile.write(json.dumps(response).encode("utf-8") + b"\n")


def _run_windows(session_path: Path, client: str, command: list[str],
                 env: dict, cwd: str, marker_path: Path) -> int:
    secret = secrets.token_urlsafe(32)
    server = _LoopbackServer(secret, marker_path)
    host, port = server.server_address
    session_id = terminal_session_name(session_path, client)
    child_env = dict(env)
    child_env.update(_terminal_env(
        session_id, "winpty", f"{host}:{port}", marker_path, secret))
    thread = threading.Thread(
        target=server.serve_forever, daemon=True,
        name=f"{session_id}-terminal-inject")
    thread.start()
    try:
        return subprocess.call(command, cwd=cwd, env=child_env)  # nosec B603
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


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
    session_path = Path(args.session).expanduser().resolve()
    try:
        command, env, cwd = build_launch(
            session_path, args.client, passthrough, args.binary)
        session = _load_session(session_path)
        marker_path = Path(session["terminal"]["marker_path"])
        if os.name == "nt":
            return _run_windows(
                session_path, args.client, command, env, cwd, marker_path)
        return _run_tmux(
            session_path, args.client, command, env, cwd, marker_path)
    except ValueError as exc:
        print(f"[pawflow-mcp] {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError:
        print(
            f"[pawflow-mcp] client executable not found: "
            f"{command[0] if 'command' in locals() else args.binary}",
            file=sys.stderr)
        return 127


if __name__ == "__main__":
    raise SystemExit(main())

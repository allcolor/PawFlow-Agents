#!/usr/bin/env python3
"""Launch one isolated MCP client in a persistent, remotely injectable terminal.

Two modes share this script:

* launch (default): start the client binary as a child of this process;
* detached (``--attach-pid`` / ``--attach-tmux``): the user starts the client
  themselves and this process only provides the return channel, published
  through ``detached-terminal.json`` in the session directory.
"""

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

DETACHED_TERMINAL_FILE = "detached-terminal.json"
_DETACHED_PROCESS = 0x00000008
_SAFE_TMUX_TARGET_CHARS = set(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.:-")


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


def _write_console_handle(handle: int, text: str) -> None:
    """Write ``text`` plus Enter as Unicode key records to a console input."""
    import ctypes
    from ctypes import wintypes

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
    if not ctypes.windll.kernel32.WriteConsoleInputW(  # type: ignore[attr-defined]
            handle, array, len(records), ctypes.byref(written)):
        raise OSError("WriteConsoleInputW failed")


def _write_windows_console(text: str) -> None:
    """Inject Unicode key records into the inherited Windows console."""
    import msvcrt

    _write_console_handle(msvcrt.get_osfhandle(sys.stdin.fileno()), text)


def _write_process_console(pid: int, text: str) -> None:
    """Attach to the console of ``pid`` and inject ``text`` into its input.

    Runs in a short-lived helper started with DETACHED_PROCESS, so the
    attaching process never gives up its own console.
    """
    import ctypes

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.FreeConsole()
    if not kernel32.AttachConsole(int(pid)):
        raise OSError(f"AttachConsole({pid}) failed: {ctypes.GetLastError()}")
    try:
        handle = kernel32.CreateFileW(
            "CONIN$", 0x80000000 | 0x40000000, 0x1 | 0x2, None, 3, 0, None)
        if handle in (None, ctypes.c_void_p(-1).value):
            raise OSError("cannot open CONIN$ of the attached console")
        try:
            _write_console_handle(handle, text)
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
    finally:
        kernel32.FreeConsole()


def _inject_into_pid(pid: int, text: str) -> None:
    """Run the console helper for ``pid`` without touching our own console."""
    result = subprocess.run(  # nosec B603 - fixed interpreter and script
        [sys.executable, str(Path(__file__).resolve()),
         "--write-console-pid", str(int(pid))],
        input=text.encode("utf-8"), capture_output=True, timeout=15,
        creationflags=_DETACHED_PROCESS)
    if result.returncode:
        raise OSError(
            result.stderr.decode("utf-8", errors="replace")[-500:]
            or "console helper failed")


class _LoopbackServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = False
    daemon_threads = True

    def __init__(self, secret: str, marker_path: Path, writer=None):
        self.secret = secret
        self.marker_path = marker_path
        self.writer = writer or _write_windows_console
        self.write_lock = threading.Lock()
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
            with self.server.write_lock:
                _append_marker(self.server.marker_path, message_id, content)
                self.server.writer(content)
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


def detached_terminal_path(session_path: Path) -> Path:
    """Where a detached return channel publishes itself for the bridge."""
    return session_path.expanduser().resolve().parent / DETACHED_TERMINAL_FILE


def _write_detached_record(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}")
    temp.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(temp, 0o600)
    except OSError:
        pass
    os.replace(temp, path)


def _remove_detached_record(path: Path, session_id: str) -> None:
    """Remove the record only while it still belongs to this attach."""
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if isinstance(current, dict) and current.get("session_id") == session_id:
        try:
            path.unlink()
        except OSError:
            pass


def _tmux_alive(target: str) -> bool:
    tmux = shutil.which("tmux")
    if not tmux:
        raise ValueError("tmux is required for --attach-tmux")
    return subprocess.run(  # nosec B603
        [tmux, "has-session", "-t", target],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def _windows_process_alive(pid: int) -> bool:
    import ctypes

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(0x00100000 | 0x1000, False, int(pid))
    if not handle:
        return False
    try:
        return kernel32.WaitForSingleObject(handle, 0) == 0x00000102
    finally:
        kernel32.CloseHandle(handle)


def attach_detached(session_path: Path, *, pid: int = 0, tmux_target: str = "",
                    poll_seconds: float = 5.0, alive=None,
                    stop: threading.Event | None = None) -> int:
    """Provide the return channel for a client the user started themselves.

    Publishes ``detached-terminal.json`` next to the session manifest; the
    running bridge picks it up and registers the terminal with PawFlow. The
    record is removed when the client exits or this process stops.
    """
    session_path = session_path.expanduser().resolve()
    session = _load_session(session_path)
    marker_path = Path(session["terminal"]["marker_path"])
    record_path = detached_terminal_path(session_path)
    stop = stop or threading.Event()
    server = None
    if bool(pid) == bool(tmux_target):
        raise ValueError("exactly one of --attach-pid or --attach-tmux is required")
    if tmux_target:
        if (len(tmux_target) > 160
                or not set(tmux_target) <= _SAFE_TMUX_TARGET_CHARS):
            raise ValueError("invalid tmux target")
        alive = alive or (lambda: _tmux_alive(tmux_target))
        if not alive():
            raise ValueError(f"tmux target is not running: {tmux_target}")
        session_id = terminal_session_name(session_path, "detached-tmux")
        record = {"session_id": session_id, "kind": "tmux",
                  "target": tmux_target, "secret": "",
                  "state_path": str(marker_path.resolve())}
    else:
        if os.name != "nt":
            raise ValueError("--attach-pid is Windows-only; use --attach-tmux")
        alive = alive or (lambda: _windows_process_alive(pid))
        if not alive():
            raise ValueError(f"process is not running: {pid}")
        secret = secrets.token_urlsafe(32)
        server = _LoopbackServer(
            secret, marker_path, writer=lambda text: _inject_into_pid(pid, text))
        host, port = server.server_address
        session_id = terminal_session_name(session_path, f"detached-{pid}")
        record = {"session_id": session_id, "kind": "winpty",
                  "target": f"{host}:{port}", "secret": secret,
                  "state_path": str(marker_path.resolve())}
        threading.Thread(target=server.serve_forever, daemon=True,
                         name=f"{session_id}-terminal-inject").start()
    _write_detached_record(record_path, record)
    print(f"[pawflow-mcp] return channel attached ({record['kind']}); "
          "the bridge registers it within a few seconds. Ctrl+C to detach.",
          file=sys.stderr)
    try:
        while alive() and not stop.wait(poll_seconds):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        _remove_detached_record(record_path, session_id)
        if server is not None:
            server.shutdown()
            server.server_close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Launch one client instance bound to one PawFlow conversation")
    parser.add_argument("--session", required=True)
    parser.add_argument("--client", choices=sorted(CLIENT_BINARIES))
    parser.add_argument("--binary", default="")
    attach = parser.add_mutually_exclusive_group()
    attach.add_argument(
        "--attach-pid", type=int, default=0,
        help="Detached mode (Windows): provide the return channel for an "
             "already running client process instead of launching one")
    attach.add_argument(
        "--attach-tmux", default="",
        help="Detached mode (POSIX): provide the return channel for a client "
             "already running in this tmux target, e.g. work:0.0")
    parser.add_argument("client_args", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--write-console-pid"] and len(argv) == 2:
        try:
            _write_process_console(int(argv[1]), sys.stdin.buffer.read().decode("utf-8"))
        except Exception as exc:
            print(str(exc), file=sys.stderr)
            return 1
        return 0
    args = build_parser().parse_args(argv)
    if args.attach_pid or args.attach_tmux:
        try:
            return attach_detached(
                Path(args.session), pid=args.attach_pid,
                tmux_target=args.attach_tmux)
        except ValueError as exc:
            print(f"[pawflow-mcp] {exc}", file=sys.stderr)
            return 2
    if not args.client:
        print("[pawflow-mcp] --client is required to launch a client",
              file=sys.stderr)
        return 2
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

"""PyInstaller entry point for the packaged PawFlow Relay client."""

import json
import os
import sys
from pathlib import Path

from pawflow_relay.__main__ import main


def _runtime_root() -> Path:
    override = os.environ.get("PAWFLOW_RELAY_RUNTIME_ROOT", "")
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent.parent
    return Path(__file__).resolve().parents[1]


def _screen_action_child(action: str) -> int:
    tools_dir = str(_runtime_root() / "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    from screen_actions import _handle_screen_action_direct

    request = json.loads(sys.stdin.read() or "{}")
    sys.stdout.write(json.dumps(_handle_screen_action_direct(action, request)))
    return 0


def _windows_terminal_backend_check() -> int:
    """Verify that the packaged Windows PTY can spawn, write, and read."""
    import socket
    import time

    from winpty import PtyProcess

    shell = os.environ.get("COMSPEC")
    if not shell:
        raise RuntimeError("COMSPEC is required for the Windows PTY check")

    sentinel = b"__PAWFLOW_WINPTY_INTERACTIVE_OK__"
    process = PtyProcess.spawn([shell], dimensions=(24, 80))
    received = bytearray()
    try:
        process.fileobj.settimeout(0.25)
        process.write(f"echo {sentinel.decode('ascii')}\r\n")
        deadline = time.monotonic() + 5
        while sentinel not in received and time.monotonic() < deadline:
            try:
                chunk = process.fileobj.recv(4096)
            except socket.timeout:
                continue
            if not chunk:
                break
            received.extend(chunk)
    finally:
        try:
            process.close(force=True)
        except Exception:
            pass

    if sentinel not in received:
        raise RuntimeError(
            "Packaged Windows PTY did not echo the interactive probe")

    sys.stdout.write(json.dumps({
        "ok": True,
        "backend": PtyProcess.__name__,
        "interactive": True,
    }))
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "__pawflow_screen_action_child__":
        raise SystemExit(_screen_action_child(sys.argv[2]))
    if len(sys.argv) > 1 and sys.argv[1] == "__pawflow_winpty_check__":
        raise SystemExit(_windows_terminal_backend_check())
    raise SystemExit(main() or 0)

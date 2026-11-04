"""voxl: the default desktop viewer (H.264 over one WebSocket, WebCodecs).

voxl (https://github.com/tembo/voxl) streams an X11 display, a Mac's screen
or a Windows desktop to a small web client. PawFlow runs it next to the
existing x11vnc + websockify stack, which stays as the fallback viewer.

Access: voxl listens on a random port and requires a random per-start
password. The relay hands port and password to the PawFlow server only; the
server adds ``Authorization: Bearer <password>`` to every request it proxies,
so the browser never sees it. The browser reaches voxl only through PawFlow's
authenticated ``/vnc/<session>/<token>/voxl/`` capability route.

Stdlib only: imported by the relay worker and the host helper.
"""

import os
import secrets
import shutil
import socket
import subprocess  # nosec B404
import sys
import time
from pathlib import Path

#: Adaptive bitrate ceiling. Frames cross the relay link and the PawFlow
#: server; voxl lowers its rate on its own when the link is slower.
MAX_KBPS = 20000


def find_voxl() -> str:
    """Return the voxl executable, or "" when this runtime has none.

    Order: ``PAWFLOW_VOXL_BIN``, the packaged runtime's ``bin/`` directory
    (Relay CLI / Relay Desktop bundles), the frozen executable's own folder
    (Windows Relay CLI install puts voxl.exe next to pawflow-relay.exe), then
    ``PATH`` (relay Docker image: /usr/local/bin/voxl).
    """
    override = os.environ.get("PAWFLOW_VOXL_BIN", "").strip()
    if override:
        return override if os.path.isfile(override) else ""
    name = "voxl.exe" if sys.platform == "win32" else "voxl"
    roots = []
    runtime_root = os.environ.get("PAWFLOW_RELAY_RUNTIME_ROOT", "").strip()
    if runtime_root:
        roots.append(Path(runtime_root))
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent.parent)
        roots.append(Path(sys.executable).resolve().parent)
    roots.append(Path(__file__).resolve().parent.parent)
    for root in roots:
        candidate = root / "bin" / name
        if candidate.is_file():
            return str(candidate)
    if getattr(sys, "frozen", False):
        beside = Path(sys.executable).resolve().parent / name
        if beside.is_file():
            return str(beside)
    return shutil.which(name) or ""


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def voxl_ready(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    """True when voxl answers its unauthenticated health check."""
    if not port:
        return False
    try:
        with socket.create_connection((host, int(port)), timeout=timeout) as sock:
            sock.sendall(b"GET /healthz HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                         b"Connection: close\r\n\r\n")
            head = sock.recv(64)
        return b" 200 " in head.split(b"\r\n", 1)[0]
    except OSError:
        return False


def build_command(binary: str, port: int, *, addr: str,
                  audio_device: str = "", files_root: str = "") -> list:
    """The voxl command line for one desktop."""
    command = [
        binary, f"--addr={addr}", f"--port={port}",
        "--title=PawFlow Desktop",
        f"--max-kbps={MAX_KBPS}",
    ]
    if audio_device:
        command += ["--audio-enabled=true", f"--audio-device-name={audio_device}"]
    if files_root:
        command.append(f"--files-root={files_root}")
    return command


def start_voxl(*, env=None, addr: str = "127.0.0.1", audio_device: str = "",
               files_root: str = "", log=None, ready_timeout: float = 8.0):
    """Start voxl; return ``(process, port, password)`` or ``None``.

    ``None`` means no voxl binary or a voxl that did not become ready; the
    caller keeps the noVNC stack as the desktop viewer.
    """
    binary = find_voxl()
    if not binary:
        return None
    port = _free_port()
    password = secrets.token_urlsafe(24)
    child_env = dict(env if env is not None else os.environ)
    child_env["VOXL_PASSWORD"] = password
    command = build_command(binary, port, addr=addr, audio_device=audio_device,
                            files_root=files_root)
    try:
        process = subprocess.Popen(  # nosec B603 - fixed binary, built argv.
            command, env=child_env,
            stdout=log or subprocess.DEVNULL, stderr=log or subprocess.DEVNULL)
    except OSError as exc:
        sys.stderr.write(f"[voxl] failed to start {binary}: {exc}\n")
        return None
    deadline = time.monotonic() + ready_timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        # Comparison only (probe a wildcard bind via loopback); voxl itself
        # binds 127.0.0.1 at every call site.
        if voxl_ready(port, "127.0.0.1" if addr in ("0.0.0.0", "127.0.0.1") else addr,  # nosec B104
                      timeout=0.5):
            sys.stderr.write(f"[voxl] ready on port {port}\n")
            return process, port, password
        time.sleep(0.2)
    sys.stderr.write(
        f"[voxl] not ready after {ready_timeout:.0f}s "
        f"(exit={process.poll()}); keeping noVNC\n")
    stop_voxl(process)
    return None


def stop_voxl(process) -> None:
    if process is None:
        return
    try:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
    except OSError:
        pass

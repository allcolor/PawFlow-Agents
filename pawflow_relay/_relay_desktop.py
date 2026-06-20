"""Desktop (VNC/noVNC) WebSocket tunnel for the relay worker.

Extracted from `pawflow_relay/worker.py`'s `_ws_connect` closure (the
`desktop_ws_*` actions). The desktop lifecycle actions (start/stop/status
of the containerized X11+VNC stack and the host-screen local desktop)
still live in worker.py and will move here in a follow-up step.

Like the code-server tunnel, the backend reader forwards frames through
an injected ``send_frame(bytes)`` callback rather than writing to the
relay socket directly. State (the open backend WS sessions) lives in the
caller's ``RelayWorkerState`` and is passed in, so the per-connection
lifecycle is unchanged.

Differences from the code-server tunnel, preserved verbatim:
  - Browser/proxy headers ARE forwarded to the VNC backend handshake
    (minus the hop-by-hop WS handshake headers); code-server strips them.
  - The reader answers WS pings (0x09) with pongs (0x0A) locally.
  - Forwarded frames carry the real opcode (not -1) and only the
    unmasked payload (no raw frame).
  - cs uses opcode 1 (text) for sends; desktop defaults to 2 (binary/VNC).

Frame shapes (unchanged):
  {"type": "desktop_ws_data",  "session_id": <sid>, "data": <b64>, "opcode": <op>}
  {"type": "desktop_ws_close", "session_id": <sid>}
"""
import base64
import json
import logging
import os
import socket
import subprocess  # nosec B404
import sys
import threading
from pathlib import Path

from pawflow_relay._relay_ws_proto import encode_masked_frame, read_ws_frame

_log = logging.getLogger(__name__)


def desktop_ws_open(state, msg, send_frame):
    """Open a backend WS to the VNC/noVNC server and stream frames out."""
    _ws_sid = msg.get("session_id", "")
    _ws_port = msg.get("port", 0)
    _ws_path = msg.get("ws_path", "/")
    _ws_headers = msg.get("headers", {})
    if not _ws_sid or not _ws_port:
        return {"ok": False, "error": "Missing session_id or port"}
    try:
        _ws_key = base64.b64encode(os.urandom(16)).decode()
        _hdr_lines = [
            f"GET {_ws_path} HTTP/1.1",
            f"Host: 127.0.0.1:{_ws_port}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {_ws_key}",
            "Sec-WebSocket-Version: 13",
        ]
        for _hk, _hv in _ws_headers.items():
            _hkl = _hk.lower()
            if _hkl not in ("host", "upgrade", "connection",
                            "sec-websocket-key", "sec-websocket-version"):
                _hdr_lines.append(f"{_hk}: {_hv}")
        _handshake = "\r\n".join(_hdr_lines) + "\r\n\r\n"
        sys.stderr.write(f"[FSRelay] desktop_ws_open connecting to 127.0.0.1:{_ws_port} path={_ws_path[:80]}\n")
        _vnc_sock = socket.create_connection(("127.0.0.1", _ws_port), timeout=10)
        _vnc_sock.sendall(_handshake.encode())
        _resp = b""
        while b"\r\n\r\n" not in _resp:
            _chunk = _vnc_sock.recv(4096)
            if not _chunk:
                raise ConnectionError("WS handshake failed")
            _resp += _chunk
        _status_line = _resp.split(b"\r\n")[0]
        if b"101" not in _status_line:
            sys.stderr.write(f"[FSRelay] desktop_ws_open handshake rejected: {_resp[:500]}\n")
            _vnc_sock.close()
            return {"ok": False, "error": f"WS handshake rejected: {_status_line.decode(errors='replace')}"}
        state.desktop_ws_sessions[_ws_sid] = {"sock": _vnc_sock}

        def _desktop_ws_reader(_sock, _sid):
            sys.stderr.write(f"[FSRelay] desktop_ws_reader started for {_sid}\n")
            try:
                while True:
                    _frame = read_ws_frame(_sock)
                    if _frame is None:
                        break
                    _op = _frame.op
                    _payload = _frame.payload
                    if _op == 0x08:
                        break
                    if _op == 0x09:
                        _pong = bytes([0x80 | 0x0A])
                        if len(_payload) < 126:
                            _pong += bytes([len(_payload)])
                        _pong += _payload
                        try:
                            _sock.sendall(_pong)
                        except Exception:
                            break
                        continue
                    _fwd = json.dumps({
                        "type": "desktop_ws_data",
                        "session_id": _sid,
                        "data": base64.b64encode(_payload).decode("ascii"),
                        "opcode": _op,
                    })
                    send_frame(_fwd.encode("utf-8"))
            except Exception:
                _log.debug("Ignored exception", exc_info=True)
            finally:
                try:
                    _sock.close()
                except Exception:
                    _log.debug("Ignored exception", exc_info=True)
                state.desktop_ws_sessions.pop(_sid, None)
                try:
                    send_frame(json.dumps({"type": "desktop_ws_close", "session_id": _sid}).encode("utf-8"))
                except Exception:
                    _log.debug("Ignored exception", exc_info=True)

        _t = threading.Thread(target=_desktop_ws_reader, args=(_vnc_sock, _ws_sid), daemon=True)
        _t.start()
        state.desktop_ws_sessions[_ws_sid]["reader"] = _t
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": f"desktop_ws_open error: {e}"}


def desktop_ws_send(state, msg):
    """Send a masked frame (binary by default) to a backend VNC WS."""
    _ws_sid = msg.get("session_id", "")
    _ws_data = msg.get("data", "")
    _ws_op = msg.get("opcode", 2)  # binary by default for VNC
    _ws_sess = state.desktop_ws_sessions.get(_ws_sid)
    if not _ws_sess:
        return {"ok": False, "error": f"Desktop WS session not found: {_ws_sid}"}
    try:
        _raw = base64.b64decode(_ws_data)
        _frame = encode_masked_frame(_ws_op, _raw)
        _ws_sess["sock"].sendall(_frame)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def desktop_ws_close(state, msg):
    """Close one backend VNC WS session."""
    _ws_sid = msg.get("session_id", "")
    _ws_sess = state.desktop_ws_sessions.pop(_ws_sid, None)
    if _ws_sess and _ws_sess.get("sock"):
        try:
            _ws_sess["sock"].close()
        except Exception:
            _log.debug("Ignored exception", exc_info=True)
    return {"ok": True}


# ── Desktop lifecycle (containerized X11+VNC and host-screen) ──


def novnc_http_ready(state, port=None, timeout=1.0):
    port = int(port or getattr(state, 'desktop_novnc_port', 0) or 0)
    if not port:
        return False
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as sock:
            sock.sendall(b"GET /vnc.html HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
            resp = sock.recv(128)
        status = resp.split(b"\r\n", 1)[0]
        return b" 200 " in status or b" 301 " in status or b" 302 " in status
    except Exception:
        return False

def desktop_is_healthy(state):
    procs = getattr(state, 'desktop_procs', None)
    if not procs:
        return False
    essential = getattr(state, 'desktop_essential_procs', None) or procs
    return all(p.poll() is None for p in essential) and novnc_http_ready(state)

def desktop_cleanup(state, reason=""):
    stop = getattr(state, 'desktop_watchdog_stop', None)
    if stop:
        stop.set()
    procs = getattr(state, 'desktop_procs', None) or []
    for p in procs:
        try:
            if p.poll() is None:
                p.terminate()
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
    for p in procs:
        try:
            if p.poll() is None:
                p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                p.kill()
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
    state.desktop_procs = None
    state.desktop_essential_procs = None
    state.desktop_vnc_port = None
    state.desktop_novnc_port = None
    state.desktop_display = None
    state.desktop_watchdog_stop = None
    state.desktop_watchdog_thread = None
    if "DISPLAY" in os.environ:
        del os.environ["DISPLAY"]
    if reason:
        sys.stderr.write(f"[FSRelay] Desktop stopped: {reason}\n")

def start_desktop_watchdog(state, procs):
    stop = threading.Event()
    state.desktop_watchdog_stop = stop

    def _watchdog():
        while not stop.wait(5):
            if getattr(state, 'desktop_procs', None) is not procs:
                return
            if not desktop_is_healthy(state):
                desktop_cleanup(state, "healthcheck failed")
                return

    t = threading.Thread(target=_watchdog, daemon=True, name="desktop-healthcheck")
    state.desktop_watchdog_thread = t
    t.start()


def start_desktop(state, msg):
    # Idempotent: if already running, return existing info
    if hasattr(state, 'desktop_procs') and state.desktop_procs:
        if desktop_is_healthy(state):
            return {"ok": True, "data": {
                "vnc_port": state.desktop_vnc_port,
                "novnc_port": state.desktop_novnc_port,
                "display": state.desktop_display,
                "already_running": True
            }}
        desktop_cleanup(state, "stale desktop process")

    _resolution = msg.get("resolution", "1280x800")
    _depth = msg.get("depth", 24)
    _display_num = msg.get("display", 99)
    _display = f":{_display_num}"
    _vnc_port = msg.get("vnc_port", 0)
    # Use fixed port from env (Docker published) or find a free one
    _novnc_port = int(os.environ.get("PAWFLOW_DESKTOP_NOVNC_PORT", 0)) or msg.get("novnc_port", 0)
    if not _vnc_port:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as _s:
            _s.bind(("", 0))
            _vnc_port = _s.getsockname()[1]
    if not _novnc_port:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as _s:
            _s.bind(("", 0))
            _novnc_port = _s.getsockname()[1]
    try:
        import time as _time_mod
        _log_d = open("/tmp/desktop.log", "w")  # nosec B108 - relay-local desktop log.
        _procs = []

        # Desktop runs as current user (pawflow via Dockerfile USER)
        _desktop_user = os.environ.get("USER", "pawflow")
        _desktop_home = os.environ.get("HOME", "/home/pawflow")

        _user_env = {
            **os.environ,
            "DISPLAY": _display,
            "HOME": _desktop_home,
            "USER": _desktop_user,
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/dbus-desktop",  # nosec B108 - relay-local desktop bus path.
            "XDG_RUNTIME_DIR": f"/tmp/xdg-{_desktop_user}",  # nosec B108 - relay-local desktop runtime dir.
        }
        os.makedirs(_user_env["XDG_RUNTIME_DIR"], mode=0o700, exist_ok=True)

        # 1. Xvfb
        _p_xvfb = subprocess.Popen(  # nosec B603, B607
            ["Xvfb", _display, "-screen", "0", f"{_resolution}x{_depth}",
             "-ac", "+extension", "GLX", "+render", "-noreset"],
            stdout=_log_d, stderr=_log_d)
        _procs.append(_p_xvfb)
        os.environ["DISPLAY"] = _display
        _time_mod.sleep(0.5)

        # 2. D-Bus session (needed by XFCE)
        _p_dbus = subprocess.Popen(  # nosec B603, B607
            ["dbus-daemon", "--session", "--nofork",
             "--address=unix:path=/tmp/dbus-desktop"],
            env=_user_env,
            stdout=_log_d, stderr=_log_d)
        _procs.append(_p_dbus)
        _time_mod.sleep(0.3)

        # 3. PulseAudio (BEFORE XFCE — so desktop apps find PA already running)
        import shutil as _shutil
        _audio_port = 0
        if _shutil.which("pulseaudio"):
            _pa_conf_dir = Path(_desktop_home) / ".config" / "pulse"
            _pa_conf_dir.mkdir(parents=True, exist_ok=True)
            (_pa_conf_dir / "daemon.conf").write_text(
                "default-sample-rate = 48000\n"
                "alternate-sample-rate = 48000\n"
            )
            if _desktop_user:
                subprocess.run(["chown", "-R", _desktop_user,  # nosec B603, B607
                                str(_pa_conf_dir)], check=False)
            subprocess.run(["pulseaudio", "--kill"], env=_user_env,  # nosec B603, B607
                           stdout=_log_d, stderr=_log_d, timeout=5)
            _time_mod.sleep(0.3)
            _p_pulse = subprocess.Popen(  # nosec B603, B607
                ["pulseaudio", "--start", "--exit-idle-time=-1",
                 "--load=module-null-sink sink_name=virtual_out rate=48000",
                 "--load=module-always-sink"],
                env=_user_env, stdout=_log_d, stderr=_log_d)
            _procs.append(_p_pulse)
            _time_mod.sleep(0.5)
            for _pa_cmd, _pa_label in [
                (["pactl", "info"], "PA info"),
                (["pactl", "list", "short", "sinks"], "PA sinks"),
            ]:
                try:
                    _pa_out = subprocess.check_output(  # nosec B603
                        _pa_cmd, env=_user_env, timeout=5, text=True)
                    sys.stderr.write(f"[FSRelay] {_pa_label}:\n{_pa_out.strip()}\n")
                except Exception as _pa_err:
                    sys.stderr.write(f"[FSRelay] {_pa_label} failed: {_pa_err}\n")
            _audio_port = _novnc_port + 100
            _audio_script = Path("/opt/pawflow/audio_capture.py")
            if _audio_script.exists():
                _p_audio = subprocess.Popen(  # nosec B603
                    [sys.executable, str(_audio_script),
                     "--port", str(_audio_port), "--source", "pulse"],
                    env=_user_env, stdout=_log_d, stderr=_log_d)
                _procs.append(_p_audio)
                sys.stderr.write(f"[FSRelay] Audio capture on port {_audio_port}\n")
            else:
                _audio_port = 0

        # Keep the X11 clipboard used by desktop apps in sync with the
        # VNC clipboard, so browser copy/paste behaves like a local desktop.
        if _shutil.which("autocutsel"):
            for _selection in ("CLIPBOARD", "PRIMARY"):
                _p_clip = subprocess.Popen(  # nosec B603, B607
                    ["autocutsel", "-selection", _selection],
                    env=_user_env, stdout=_log_d, stderr=_log_d)
                _procs.append(_p_clip)

        # 4. XFCE desktop session (PA already running — no plugin conflict)
        _p_wm = subprocess.Popen(  # nosec B603, B607
            ["startxfce4"], env=_user_env,
            stdout=_log_d, stderr=_log_d)
        _procs.append(_p_wm)
        _time_mod.sleep(1)

        # 5. x11vnc
        _p_vnc = subprocess.Popen(  # nosec B603, B607
            ["x11vnc", "-display", _display, "-forever", "-nopw",
             "-rfbport", str(_vnc_port), "-shared", "-noxdamage",
             "-defer", "33"],
            stdout=_log_d, stderr=_log_d)
        _procs.append(_p_vnc)

        # 6. websockify (noVNC)
        _novnc_web = "/usr/share/novnc"
        _p_novnc = subprocess.Popen(  # nosec B603, B607
            ["websockify", "--web", _novnc_web,
             "--heartbeat", "30",
             f"0.0.0.0:{_novnc_port}", f"localhost:{_vnc_port}"],
            stdout=_log_d, stderr=_log_d)
        _procs.append(_p_novnc)
        state.desktop_procs = _procs
        state.desktop_essential_procs = [_p_xvfb, _p_vnc, _p_novnc]
        state.desktop_vnc_port = _vnc_port
        state.desktop_novnc_port = _novnc_port
        state.desktop_display = _display

        _deadline = _time_mod.time() + 8
        _novnc_ready = False
        while _time_mod.time() < _deadline:
            if _p_novnc.poll() is not None:
                break
            if novnc_http_ready(state, _novnc_port, timeout=0.5):
                _novnc_ready = True
                break
            _time_mod.sleep(0.2)
        if not _novnc_ready:
            desktop_cleanup(state, "noVNC failed to become ready")
            return {"ok": False, "error": "noVNC failed to become ready"}

        start_desktop_watchdog(state, _procs)
        sys.stderr.write(f"[FSRelay] Desktop started: display={_display} vnc={_vnc_port} novnc={_novnc_port} audio={_audio_port} res={_resolution}\n")
        return {"ok": True, "data": {
            "vnc_port": _vnc_port, "novnc_port": _novnc_port,
            "audio_port": _audio_port,
            "display": _display, "resolution": _resolution
        }}
    except FileNotFoundError as e:
        return {"ok": False, "error": f"Desktop dependency not installed: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"Failed to start desktop: {e}"}


def stop_desktop(state):
    if hasattr(state, 'desktop_procs') and state.desktop_procs:
        desktop_cleanup(state, "requested")
        return {"ok": True}
    return {"ok": True, "data": {"was_running": False}}


def desktop_status(state):
    _running = desktop_is_healthy(state)
    if getattr(state, 'desktop_procs', None) and not _running:
        desktop_cleanup(state, "healthcheck failed")
    _local_running = False
    if hasattr(state, 'local_desktop_procs') and state.local_desktop_procs:
        _local_running = all(p.poll() is None for p in state.local_desktop_procs)
    _novnc = getattr(state, 'desktop_novnc_port', None)
    return {"ok": True, "data": {
        "running": _running,
        "display": getattr(state, 'desktop_display', None),
        "vnc_port": getattr(state, 'desktop_vnc_port', None),
        "novnc_port": _novnc,
        "audio_port": (_novnc + 100) if _novnc and _running else 0,
        "local_screen_running": _local_running,
        "local_screen_novnc_port": getattr(state, 'local_desktop_novnc_port', None),
    }}


def start_local_desktop(state, msg):
    # Idempotent
    if hasattr(state, 'local_desktop_procs') and state.local_desktop_procs:
        _alive = all(p.poll() is None for p in state.local_desktop_procs)
        if _alive:
            return {"ok": True, "data": {
                "novnc_port": state.local_desktop_novnc_port,
                "already_running": True
            }}
        else:
            for p in state.local_desktop_procs:
                try:
                    p.kill()
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            state.local_desktop_procs = None

    # Detect available VNC server
    _vnc_cmd = None
    _platform = sys.platform
    _vnc_port = 0
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as _s:
        _s.bind(("", 0))
        _vnc_port = _s.getsockname()[1]
    _novnc_port = int(msg.get("novnc_port", 0))
    if not _novnc_port:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as _s:
            _s.bind(("", 0))
            _novnc_port = _s.getsockname()[1]

    try:
        import shutil
        _procs = []
        _log_d = open("/tmp/local_desktop.log", "w") if _platform != "win32" else open(os.path.join(os.environ.get("TEMP", "."), "local_desktop.log"), "w")  # nosec B108 - relay-local desktop log.

        if _platform == "linux":
            # Linux: use x11vnc to share the real display :0
            _display = os.environ.get("DISPLAY", ":0")
            if not shutil.which("x11vnc"):
                return {"ok": False, "error": "x11vnc not installed. Install with: apt install x11vnc"}
            if not shutil.which("websockify"):
                return {"ok": False, "error": "websockify not installed. Install with: pip install websockify"}
            _p_vnc = subprocess.Popen(  # nosec B603, B607
                ["x11vnc", "-display", _display, "-forever", "-nopw",
                 "-rfbport", str(_vnc_port), "-shared", "-noxdamage",
                 "-defer", "33"],
                stdout=_log_d, stderr=_log_d)
            _procs.append(_p_vnc)

        elif _platform == "win32":
            # Windows: use TightVNC or UltraVNC via WinVNC if available,
            # else try built-in Windows VNC (Remote Desktop) — but for noVNC we need a VNC server.
            # Check for common VNC servers
            _winvnc = None
            for _candidate in [
                r"C:\Program Files\TightVNC\tvnserver.exe",
                r"C:\Program Files\uvnc bvba\UltraVNC\winvnc.exe",
                r"C:\Program Files (x86)\TightVNC\tvnserver.exe",
            ]:
                if os.path.exists(_candidate):
                    _winvnc = _candidate
                    break
            if not _winvnc:
                _winvnc = shutil.which("tvnserver") or shutil.which("winvnc")
            if not _winvnc:
                return {"ok": False, "error": "No VNC server found on Windows. Install TightVNC or UltraVNC."}
            _websockify = shutil.which("websockify")
            if not _websockify:
                return {"ok": False, "error": "websockify not installed. Install with: pip install websockify"}
            # Start VNC server on the specified port
            _p_vnc = subprocess.Popen(  # nosec B603
                [_winvnc, "-rfbport", str(_vnc_port), "-localhost"],
                stdout=_log_d, stderr=_log_d)
            _procs.append(_p_vnc)

        elif _platform == "darwin":
            # macOS: built-in VNC server (Screen Sharing)
            # Enable via: System Preferences → Sharing → Screen Sharing
            # Or start with: /System/Library/CoreServices/RemoteManagement/ARDAgent.app/...
            if not shutil.which("websockify"):
                return {"ok": False, "error": "websockify not installed. Install with: pip install websockify"}
            # macOS VNC server usually runs on port 5900
            _vnc_port = 5900
            # Just check it's accessible
            try:
                _test = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                _test.settimeout(2)
                _test.connect(("localhost", 5900))
                _test.close()
            except Exception:
                return {"ok": False, "error": "macOS Screen Sharing not enabled. Enable in System Preferences → Sharing → Screen Sharing."}

        else:
            return {"ok": False, "error": f"Unsupported platform for local screen: {_platform}"}

        # Start websockify (noVNC)
        import time as _time_mod
        _time_mod.sleep(0.5)
        _novnc_web = "/usr/share/novnc"
        if _platform == "win32":
            _novnc_web = os.path.join(os.environ.get("PROGRAMFILES", "C:\\Program Files"), "noVNC")
            if not os.path.isdir(_novnc_web):
                _novnc_web = ""
        elif _platform == "darwin":
            _novnc_web = "/usr/local/share/novnc"
            if not os.path.isdir(_novnc_web):
                _novnc_web = ""

        _ws_args = ["websockify", str(_novnc_port), f"localhost:{_vnc_port}"]
        if _novnc_web and os.path.isdir(_novnc_web):
            _ws_args = ["websockify", "--web", _novnc_web, str(_novnc_port), f"localhost:{_vnc_port}"]
        _p_novnc = subprocess.Popen(_ws_args, stdout=_log_d, stderr=_log_d)  # nosec B603
        _procs.append(_p_novnc)

        state.local_desktop_procs = _procs
        state.local_desktop_vnc_port = _vnc_port
        state.local_desktop_novnc_port = _novnc_port
        sys.stderr.write(f"[FSRelay] Local desktop started: vnc={_vnc_port} novnc={_novnc_port} platform={_platform}\n")
        return {"ok": True, "data": {
            "vnc_port": _vnc_port, "novnc_port": _novnc_port,
            "platform": _platform, "local_screen": True
        }}
    except FileNotFoundError as e:
        return {"ok": False, "error": f"Local desktop dependency not installed: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"Failed to start local desktop: {e}"}


def stop_local_desktop(state):
    if hasattr(state, 'local_desktop_procs') and state.local_desktop_procs:
        for p in state.local_desktop_procs:
            if p.poll() is None:
                p.terminate()
        for p in state.local_desktop_procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        state.local_desktop_procs = None
        state.local_desktop_vnc_port = None
        state.local_desktop_novnc_port = None
        sys.stderr.write("[FSRelay] Local desktop stopped\n")
        return {"ok": True}
    return {"ok": True, "data": {"was_running": False}}


def local_screen_check(allow_local_screen):
    # Check if local screen VNC dependencies are available
    import shutil
    _checks = {}
    _platform = sys.platform
    _checks["platform"] = _platform
    _checks["allow_local_screen"] = allow_local_screen
    if _platform == "linux":
        _checks["x11vnc"] = bool(shutil.which("x11vnc"))
        _checks["websockify"] = bool(shutil.which("websockify"))
        _checks["display"] = os.environ.get("DISPLAY", "")
        _checks["ready"] = _checks["x11vnc"] and _checks["websockify"] and bool(_checks["display"])
    elif _platform == "win32":
        _has_vnc = False
        for _c in [r"C:\Program Files\TightVNC\tvnserver.exe",
                   r"C:\Program Files\uvnc bvba\UltraVNC\winvnc.exe",
                   r"C:\Program Files (x86)\TightVNC\tvnserver.exe"]:
            if os.path.exists(_c):
                _has_vnc = True
                break
        _has_vnc = _has_vnc or bool(shutil.which("tvnserver")) or bool(shutil.which("winvnc"))
        _checks["vnc_server"] = _has_vnc
        _checks["websockify"] = bool(shutil.which("websockify"))
        _checks["ready"] = _has_vnc and _checks["websockify"]
    elif _platform == "darwin":
        _checks["websockify"] = bool(shutil.which("websockify"))
        try:
            _test = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            _test.settimeout(2)
            _test.connect(("localhost", 5900))
            _test.close()
            _checks["screen_sharing"] = True
        except Exception:
            _checks["screen_sharing"] = False
        _checks["ready"] = _checks["websockify"] and _checks["screen_sharing"]
    else:
        _checks["ready"] = False
    return {"ok": True, "data": _checks}

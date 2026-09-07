"""Read NumLock on the desktop that owns a VNC session."""

import json
import subprocess  # nosec B404
import threading


# Executed with an explicit argument vector, including on native Windows.
# XKB's named indicator avoids depending on xset in the minimal login image.
KEYBOARD_STATE_SCRIPT = """
import ctypes
import json
import sys

def numlock_state():
    if sys.platform == 'win32':
        return bool(ctypes.windll.user32.GetKeyState(0x90) & 1)
    if not sys.platform.startswith('linux'):
        return None
    x11 = ctypes.CDLL('libX11.so.6')
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    x11.XInternAtom.restype = ctypes.c_ulong
    x11.XkbGetNamedIndicator.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_int), ctypes.c_void_p, ctypes.c_void_p]
    x11.XkbGetNamedIndicator.restype = ctypes.c_int
    display = x11.XOpenDisplay(None)
    if not display:
        return None
    try:
        atom = x11.XInternAtom(display, b'Num Lock', 1)
        state = ctypes.c_int()
        if atom and x11.XkbGetNamedIndicator(
                display, atom, None, ctypes.byref(state), None, None):
            return bool(state.value)
        return None
    finally:
        x11.XCloseDisplay(display)

print(json.dumps({'numlock': numlock_state()}))
"""


def read_keyboard_state(session):
    """Query only the registered target; request parameters cannot select it."""
    relay = session.get("keyboard_relay_service") or session.get("relay_service")
    if relay is not None:
        display = session.get("keyboard_display")
        result = relay._request(
            "screen_keyboard_state", ".", _request_timeout=3, timeout=2,
            local=bool(session.get("keyboard_local", session.get("local_screen"))),
            display=display)
        if result.get("error") or not result.get("ok", True):
            raise RuntimeError("Keyboard state query failed")
    elif session.get("container"):
        from core.docker_utils import docker_cmd
        result = subprocess.run(  # nosec B603 - registered container, fixed read-only probe.
            docker_cmd() + ["exec", "-e", "DISPLAY=:99", session["container"],
                             "python3", "-c", KEYBOARD_STATE_SCRIPT],
            capture_output=True, text=True, timeout=3, check=True)
        result = json.loads(result.stdout)
    else:
        raise ValueError("VNC session has no keyboard target")
    state = result["numlock"]
    if state is not None and type(state) is not bool:
        raise ValueError("Invalid NumLock state")
    return {"numlock": state}


def serve_keyboard_state(pending_req, session_id, session):
    """Finish the authenticated read asynchronously, without caching LED state."""
    if getattr(pending_req, "method", "GET") != "GET":
        pending_req.complete(405, {"Allow": "GET"}, b"")
        return

    def query():
        from services.vnc_proxy import _lock, _sessions
        try:
            result = read_keyboard_state(session)
            status = 200
        except Exception:
            result = {"numlock": None, "error": "Keyboard state unavailable"}
            status = 503
        with _lock:
            if _sessions.get(session_id) is not session:
                result = {"error": "VNC session changed"}
                status = 409
        pending_req.complete(
            status, {"Content-Type": "application/json", "Cache-Control": "no-store"},
            json.dumps(result).encode("utf-8"))

    threading.Thread(target=query, daemon=True, name="vnc-keyboard-state").start()

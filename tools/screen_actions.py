"""Screen actions for Windows/macOS host.

Used by the relay host helper for local screen actions on the user's actual
machine. Linux Docker containers use fs_screen.py (xdotool/mss) instead.
"""
import logging

import base64
import io
import json
import os
import subprocess  # nosec B404
import sys

_BUTTON_MAP = {"left": "left", "right": "right", "middle": "middle"}
_CHILD_ENV = "PAWFLOW_SCREEN_ACTION_CHILD"
_SCREEN_GUARD_MAX_DIFFERENCE = 0.06

# Make process DPI-aware on Windows so coordinates use physical pixels matching
# the screenshots returned by ImageGrab/pyautogui.
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)


def _get_pyautogui():
    try:
        import pyautogui
        pyautogui.FAILSAFE = False
        return pyautogui
    except ImportError:
        raise RuntimeError(
            "pyautogui not installed. Install with: pip install pyautogui"
        )


def handle_screen_action(action: str, req: dict) -> dict:
    """Dispatch a screen_* action. Returns dict result.

    With PAWFLOW_SCREEN_MODE=cua, actions route through cua-driver
    (background computer use, no cursor/focus steal) — see
    screen_actions_cua.py. cua calls are already per-action subprocesses
    with their own timeout, so the killable-child wrapper is skipped.

    Host GUI libraries can block inside OS APIs. Keep the relay host-helper
    thread killable by running every screen action in a subprocess with the
    request timeout. The child process executes the direct implementation.
    """
    if os.environ.get(_CHILD_ENV) != "1":
        mode = (os.environ.get("PAWFLOW_SCREEN_MODE") or "").strip().lower()
        if mode != "cua" or action == "screen_keyboard_state":
            return _screen_action_subprocess(action, req)
        try:
            from tools.screen_actions_cua import handle_screen_action_cua
        except ImportError:
            from screen_actions_cua import handle_screen_action_cua
        return handle_screen_action_cua(action, req)
    return _handle_screen_action_direct(action, req)


def _handle_screen_action_direct(action: str, req: dict) -> dict:
    _dispatch = {
        "screen_screenshot": _screenshot,
        "screen_click": _click,
        "screen_double_click": _double_click,
        "screen_type": _type,
        "screen_key": _key,
        "screen_move": _move,
        "screen_scroll": _scroll,
        "screen_mouse_position": _mouse_position,
        "screen_keyboard_state": _keyboard_state,
        "screen_status": _status_pawflow,
        "screen_windows": _dispatch_windows,
        "screen_window_state": _dispatch_window_state,
    }
    fn = _dispatch.get(action)
    if not fn:
        return {"error": f"Unknown screen action: {action}"}
    try:
        return fn(req)
    except Exception as e:
        return {"error": str(e)}


def _keyboard_state(req):
    """Read the selected desktop LED without an external Python or xset."""
    import ctypes

    if sys.platform == "win32":
        return {"numlock": bool(ctypes.windll.user32.GetKeyState(0x90) & 1)}
    if not sys.platform.startswith("linux"):
        return {"numlock": None}
    x11 = ctypes.CDLL("libX11.so.6")
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    x11.XInternAtom.restype = ctypes.c_ulong
    x11.XkbGetNamedIndicator.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_int), ctypes.c_void_p, ctypes.c_void_p]
    x11.XkbGetNamedIndicator.restype = ctypes.c_int
    display_name = req.get("display")
    display = x11.XOpenDisplay(display_name.encode() if display_name else None)
    if not display:
        return {"numlock": None}
    try:
        atom = x11.XInternAtom(display, b"Num Lock", 1)
        state = ctypes.c_int()
        if atom and x11.XkbGetNamedIndicator(
                display, atom, None, ctypes.byref(state), None, None):
            return {"numlock": bool(state.value)}
        return {"numlock": None}
    finally:
        x11.XCloseDisplay(display)


def _status_pawflow(req: dict) -> dict:
    # A status response is a health check, not merely a mode label. Import the
    # same runtime dependencies used by input and screenshot actions so a
    # packaged relay cannot advertise a healthy backend that fails on first use.
    _get_pyautogui()
    from PIL import ImageGrab
    if not callable(getattr(ImageGrab, "grab", None)):
        raise RuntimeError("Pillow ImageGrab backend is unavailable")
    return {"mode": "pawflow",
            "note": "default backend (pyautogui/win32); set "
                    "PAWFLOW_SCREEN_MODE=cua for background computer use"}


def _cua_only(req: dict) -> dict:
    return {"error": (
        "This action needs the CUA screen backend: set "
        "PAWFLOW_SCREEN_MODE=cua on the relay (cua-driver must be "
        "installed). AX window/element addressing is not available in "
        "the default pawflow backend.")}


# ── Native Windows window enumeration (no external binary) ──────────────
#
# cua-driver is installed by a Linux-only script and has no Windows build, so
# PAWFLOW_SCREEN_MODE=cua can never work on a Windows relay. Windows exposes
# window enumeration through user32 and the accessibility tree through UIA,
# both reachable from ctypes/pywin32 with nothing to install.

_NATIVE_WINDOWS_HINT = (
    "window enumeration needs the Windows user32 API, which this relay build "
    "does not expose; update the relay to a build that ships uiautomation")


def _native_windows_available() -> bool:
    """Whether this process can enumerate Windows windows natively."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        return bool(ctypes.windll.user32)
    except Exception:
        return False


def _native_list_windows(req: dict) -> list:
    """Enumerate top-level windows through user32.

    Returns one dict per visible window with the same identifiers the CUA
    backend exposes (pid, window_id, title) plus the window rectangle, so
    screen_window_state and coordinate math work unchanged.
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    rows: list = []

    WNDENUMPROC = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()
        if not title:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        rect = wintypes.RECT()
        try:
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            box = [rect.left, rect.top,
                   max(0, rect.right - rect.left),
                   max(0, rect.bottom - rect.top)]
        except Exception:
            box = [0, 0, 0, 0]
        rows.append({
            "pid": int(pid.value),
            "window_id": hex(int(hwnd)),
            "title": title,
            "rect": box,
        })
        return True

    user32.EnumWindows(WNDENUMPROC(_callback), 0)
    limit = int(req.get("limit") or 0)
    return rows[:limit] if limit > 0 else rows


def _native_window_state(req: dict) -> dict:
    """Snapshot one window: rectangle, title and its accessibility tree.

    The tree comes from UIA when ``uiautomation`` is installed; without it the
    window is still described (title/rect/pid) so callers get a usable answer
    instead of a dead end. Element indexes are only produced by the UIA path.
    """
    import ctypes
    from ctypes import wintypes

    window_id = req.get("window_id")
    pid = req.get("pid")
    if not window_id and pid is None:
        return {"error": ("screen_window_state requires pid and/or "
                          "window_id (see screen_windows)")}
    user32 = ctypes.windll.user32
    hwnd = None
    if window_id:
        try:
            hwnd = int(str(window_id), 16) if str(window_id).startswith("0x") \
                else int(window_id)
        except (TypeError, ValueError):
            return {"error": f"invalid window_id: {window_id!r}"}
    if hwnd is None:
        # Resolve by pid: first visible window owned by that process.
        target = int(pid)
        found = []

        WNDENUMPROC = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _callback(h, _l):
            if not user32.IsWindowVisible(h):
                return True
            owner = wintypes.DWORD()
            user32.GetWindowThreadProcessId(h, ctypes.byref(owner))
            if int(owner.value) == target:
                found.append(int(h))
                return False
            return True

        user32.EnumWindows(WNDENUMPROC(_callback), 0)
        if not found:
            return {"error": f"no visible window for pid {pid}"}
        hwnd = found[0]
    if not user32.IsWindow(hwnd):
        return {"error": f"window not found: {window_id or pid}"}

    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    owner = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))

    out = {
        "backend": "native",
        "pid": int(owner.value),
        "window_id": hex(hwnd),
        "title": buf.value.strip(),
        "rect": [rect.left, rect.top,
                 max(0, rect.right - rect.left),
                 max(0, rect.bottom - rect.top)],
    }
    elements = _native_uia_elements(hwnd, req)
    if elements is not None:
        out["state"] = {"elements": elements}
    else:
        out["state"] = {"elements": [],
                        "note": ("accessibility tree needs uiautomation "
                                 "(pip install uiautomation); window "
                                 "geometry is still reported")}
    return out


def _native_uia_elements(hwnd: int, req: dict):
    """Return the window's UIA elements, or None when UIA is unavailable."""
    try:
        import uiautomation as auto
    except ImportError:
        return None
    try:
        control = auto.ControlFromHandle(hwnd)
        if control is None:
            return []
        limit = int(req.get("max_elements") or 400)
        rows = []
        for index, child in enumerate(control.GetChildren()):
            if index >= limit:
                break
            try:
                box = child.BoundingRectangle
                rows.append({
                    "element_index": index,
                    "name": child.Name or "",
                    "control_type": child.ControlTypeName or "",
                    "automation_id": child.AutomationId or "",
                    "rect": [int(box.left), int(box.top),
                             max(0, int(box.right - box.left)),
                             max(0, int(box.bottom - box.top))],
                })
            except Exception:
                continue
        return rows
    except Exception:
        return None


def _windows_native_or_error(req: dict) -> dict:
    """screen_windows on win32: native list, or an honest error."""
    if not _native_windows_available():
        return {"error": _NATIVE_WINDOWS_HINT}
    try:
        return {"windows": _native_list_windows(req), "backend": "native"}
    except Exception as e:
        return {"error": f"native window enumeration failed: {e}"}


def _window_state_native_or_error(req: dict) -> dict:
    """screen_window_state on win32: native snapshot, or an honest error."""
    if not _native_windows_available():
        return {"error": _NATIVE_WINDOWS_HINT}
    try:
        return _native_window_state(req)
    except Exception as e:
        return {"error": f"native window snapshot failed: {e}"}


def _dispatch_windows(req: dict) -> dict:
    """Route window enumeration: native on win32, CUA-only elsewhere."""
    if sys.platform == "win32":
        return _windows_native_or_error(req)
    return _cua_only(req)


def _dispatch_window_state(req: dict) -> dict:
    """Route a window snapshot: native on win32, CUA-only elsewhere."""
    if sys.platform == "win32":
        return _window_state_native_or_error(req)
    return _cua_only(req)


def _screen_action_subprocess(action: str, req: dict) -> dict:
    try:
        timeout = float(req.get("timeout", 15) or 15)
    except (TypeError, ValueError):
        timeout = 15
    env = dict(os.environ)
    env[_CHILD_ENV] = "1"
    cmd = _screen_action_child_command(action)
    try:
        proc = subprocess.run(  # nosec B603
            cmd,
            input=json.dumps(req),
            text=True,
            capture_output=True,
            timeout=max(1, timeout),
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"error": f"{action} timed out after {int(timeout)}s"}
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or f"exit code {proc.returncode}").strip()
        return {"error": err}
    try:
        return json.loads(proc.stdout or "{}")
    except Exception as e:
        return {"error": f"Invalid {action} response: {e}"}


def _screen_action_child_command(action: str) -> list[str]:
    """Return the subprocess command for a killable screen action child.

    In source mode, sys.executable is a Python interpreter and can execute this
    file directly. In the packaged desktop relay, sys.executable is the
    pawflow-relay binary; running it with `screen_actions.py` would enter the
    normal CLI and fail before the screen action runs.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, "__pawflow_screen_action_child__", action]
    return [sys.executable, __file__, action]


def _screenshot(req):
    screenshot = _grab_screenshot()
    w, h = screenshot.size
    buf = io.BytesIO()
    screenshot.save(buf, format="PNG")
    return {"image": base64.b64encode(buf.getvalue()).decode("ascii"), "width": w, "height": h}


def _grab_screenshot():
    if sys.platform == "win32":
        try:
            from PIL import ImageGrab
            return ImageGrab.grab(all_screens=True)
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
    pag = _get_pyautogui()
    return pag.screenshot()


def _capture_guard_region_png(region):
    """Capture one physical-pixel region immediately before guarded input."""
    x = int(region["x"])
    y = int(region["y"])
    width = int(region["width"])
    height = int(region["height"])
    if width <= 0 or height <= 0:
        raise ValueError("screen guard region is empty")
    screenshot = _grab_screenshot().crop((x, y, x + width, y + height))
    output = io.BytesIO()
    screenshot.save(output, format="PNG")
    return output.getvalue()


def _screen_difference_score(expected_png, current_png):
    """Return a bounded perceptual difference score without external services."""
    from PIL import Image

    with Image.open(io.BytesIO(expected_png)) as expected_source:
        expected_source.load()
        expected = expected_source.convert("RGB")
    with Image.open(io.BytesIO(current_png)) as current_source:
        current_source.load()
        current = current_source.convert("RGB")
    if expected.size != current.size:
        return 1.0

    expected.thumbnail((256, 256))
    resampling = getattr(Image, "Resampling", Image).BILINEAR
    current = current.resize(expected.size, resampling)
    # get_flattened_data replaces getdata (deprecated, removed in Pillow 14).
    expected_pixels = list(
        expected.get_flattened_data()
        if hasattr(expected, "get_flattened_data") else expected.getdata())
    current_pixels = list(
        current.get_flattened_data()
        if hasattr(current, "get_flattened_data") else current.getdata())
    if not expected_pixels:
        return 1.0

    absolute_total = 0
    changed = 0
    for before, now in zip(expected_pixels, current_pixels):
        deltas = tuple(abs(a - b) for a, b in zip(before, now))
        absolute_total += sum(deltas)
        if max(deltas) >= 24:
            changed += 1
    mean_delta = absolute_total / (len(expected_pixels) * 3 * 255)
    changed_fraction = changed / len(expected_pixels)
    return min(1.0, max(mean_delta * 2, changed_fraction))


def _validate_screen_guard(req):
    """Return a stale-screen result or None when local validation succeeds."""
    guard = req.get("_screen_guard")
    if not isinstance(guard, dict):
        return {
            "stale_screen": True,
            "reason": "missing_screen_guard",
        }
    try:
        region = guard["region"]
        expected = base64.b64decode(guard["expected_image"], validate=True)
        current = _capture_guard_region_png(region)
        difference = _screen_difference_score(expected, current)
    except Exception as exc:
        return {
            "stale_screen": True,
            "reason": f"screen_guard_failed: {exc}",
        }
    if difference > _SCREEN_GUARD_MAX_DIFFERENCE:
        return {
            "stale_screen": True,
            "reason": "target_region_changed",
            "difference": round(difference, 6),
            "threshold": _SCREEN_GUARD_MAX_DIFFERENCE,
            "screen_revision": guard.get("revision", ""),
        }
    return None


def _win_user32():
    import ctypes
    return ctypes.windll.user32


def _win_set_cursor(x: int, y: int) -> None:
    user32 = _win_user32()
    if not user32.SetCursorPos(int(x), int(y)):
        raise RuntimeError("SetCursorPos failed")


def _win_mouse_event(flags: int, data: int = 0) -> None:
    user32 = _win_user32()
    user32.mouse_event(flags, 0, 0, data, 0)


def _win_mouse_down_up(button: str):
    flags = {
        "left": (0x0002, 0x0004),
        "right": (0x0008, 0x0010),
        "middle": (0x0020, 0x0040),
    }.get(button, (0x0002, 0x0004))
    _win_mouse_event(flags[0])
    _win_mouse_event(flags[1])


def _click(req):
    stale = _validate_screen_guard(req)
    if stale:
        return stale
    x, y = int(req.get("x", 0)), int(req.get("y", 0))
    button = _BUTTON_MAP.get(req.get("button", "left"), "left")
    if sys.platform == "win32":
        _win_set_cursor(x, y)
        _win_mouse_down_up(button)
    else:
        pag = _get_pyautogui()
        pag.click(x, y, button=button)
    return {"clicked": True, "x": x, "y": y}


def _double_click(req):
    stale = _validate_screen_guard(req)
    if stale:
        return stale
    x, y = int(req.get("x", 0)), int(req.get("y", 0))
    button = _BUTTON_MAP.get(req.get("button", "left"), "left")
    if sys.platform == "win32":
        _win_set_cursor(x, y)
        _win_mouse_down_up(button)
        _win_mouse_down_up(button)
    else:
        pag = _get_pyautogui()
        pag.doubleClick(x, y)
    return {"double_clicked": True, "x": x, "y": y}


def _type(req):
    text = req.get("text", "")
    if sys.platform == "win32":
        # Use Win32 SendInput with KEYEVENTF_UNICODE — reliable for all chars.
        import ctypes
        from ctypes import wintypes
        INPUT_KEYBOARD = 1
        KEYEVENTF_UNICODE = 0x0004
        KEYEVENTF_KEYUP = 0x0002

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
            ]

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [
                ("dx", ctypes.c_long),
                ("dy", ctypes.c_long),
                ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
            ]

        class HARDWAREINPUT(ctypes.Structure):
            _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

        class _INPUTunion(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("u", _INPUTunion)]

        SendInput = ctypes.windll.user32.SendInput
        SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
        SendInput.restype = wintypes.UINT

        for ch in text:
            inputs = (INPUT * 2)()
            inputs[0].type = INPUT_KEYBOARD
            inputs[0].u.ki.wVk = 0
            inputs[0].u.ki.wScan = ord(ch)
            inputs[0].u.ki.dwFlags = KEYEVENTF_UNICODE
            inputs[1].type = INPUT_KEYBOARD
            inputs[1].u.ki.wVk = 0
            inputs[1].u.ki.wScan = ord(ch)
            inputs[1].u.ki.dwFlags = KEYEVENTF_UNICODE | KEYEVENTF_KEYUP
            SendInput(2, inputs, ctypes.sizeof(INPUT))
    else:
        pag = _get_pyautogui()
        pag.write(text, interval=0.02)
    return {"typed": len(text)}


def _key(req):
    pag = _get_pyautogui()
    key = req.get("key", "")
    if "+" in key:
        parts = [k.strip().lower() for k in key.split("+")]
        pag.hotkey(*parts)
    else:
        pag.press(key.lower())
    return {"pressed": key}


def _move(req):
    x, y = int(req.get("x", 0)), int(req.get("y", 0))
    if sys.platform == "win32":
        _win_set_cursor(x, y)
    else:
        pag = _get_pyautogui()
        pag.moveTo(x, y)
    return {"moved": True, "x": x, "y": y}


def _scroll(req):
    x, y = int(req.get("x", 0)), int(req.get("y", 0))
    amount = int(req.get("amount", 3))
    if sys.platform == "win32":
        _win_set_cursor(x, y)
        _win_mouse_event(0x0800, -amount * 120)
    else:
        pag = _get_pyautogui()
        pag.scroll(-amount, x=x, y=y)
    return {"scrolled": amount}


def _mouse_position(req):
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class POINT(ctypes.Structure):
            _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

        pt = POINT()
        if not ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
            raise RuntimeError("GetCursorPos failed")
        return {"x": int(pt.x), "y": int(pt.y)}
    pag = _get_pyautogui()
    pos = pag.position()
    return {"x": pos.x, "y": pos.y}


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1].startswith("screen_"):
    request = json.loads(sys.stdin.read() or "{}")
    sys.stdout.write(json.dumps(handle_screen_action(sys.argv[1], request)))

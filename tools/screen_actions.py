"""Screen actions for Windows/macOS host (via pyautogui).

Used by pawflow_cli/relay.py for local_screen actions on the user's
actual desktop. Linux Docker containers use fs_screen.py (xdotool) instead.
"""

import base64
import io
import sys

_BUTTON_MAP = {"left": "left", "right": "right", "middle": "middle"}


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
    """Dispatch a screen_* action. Returns dict result."""
    _dispatch = {
        "screen_screenshot": _screenshot,
        "screen_click": _click,
        "screen_double_click": _double_click,
        "screen_type": _type,
        "screen_key": _key,
        "screen_move": _move,
        "screen_scroll": _scroll,
        "screen_mouse_position": _mouse_position,
    }
    fn = _dispatch.get(action)
    if not fn:
        return {"error": f"Unknown screen action: {action}"}
    try:
        return fn(req)
    except Exception as e:
        return {"error": str(e)}


def _screenshot(req):
    pag = _get_pyautogui()
    screenshot = pag.screenshot()
    buf = io.BytesIO()
    screenshot.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _click(req):
    pag = _get_pyautogui()
    x, y = int(req.get("x", 0)), int(req.get("y", 0))
    button = _BUTTON_MAP.get(req.get("button", "left"), "left")
    pag.click(x, y, button=button)
    return {"clicked": True, "x": x, "y": y}


def _double_click(req):
    pag = _get_pyautogui()
    x, y = int(req.get("x", 0)), int(req.get("y", 0))
    pag.doubleClick(x, y)
    return {"double_clicked": True, "x": x, "y": y}


def _type(req):
    text = req.get("text", "")
    import sys
    if sys.platform == "win32":
        # Use Win32 SendInput with UNICODE flag — works regardless of focus/session
        import ctypes
        from ctypes import wintypes
        INPUT_KEYBOARD = 1
        KEYEVENTF_UNICODE = 0x0004
        KEYEVENTF_KEYUP = 0x0002

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

        class INPUT(ctypes.Structure):
            class _U(ctypes.Union):
                _fields_ = [("ki", KEYBDINPUT)]
            _fields_ = [("type", wintypes.DWORD), ("u", _U)]

        inputs = []
        for ch in text:
            code = ord(ch)
            down = INPUT(type=INPUT_KEYBOARD)
            down.u.ki = KEYBDINPUT(wVk=0, wScan=code,
                                    dwFlags=KEYEVENTF_UNICODE, time=0, dwExtraInfo=None)
            up = INPUT(type=INPUT_KEYBOARD)
            up.u.ki = KEYBDINPUT(wVk=0, wScan=code,
                                  dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, time=0, dwExtraInfo=None)
            inputs.extend([down, up])

        arr = (INPUT * len(inputs))(*inputs)
        ctypes.windll.user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
    else:
        pag = _get_pyautogui()
        pag.typewrite(text, interval=0.02) if text.isascii() else pag.write(text)
    return {"typed": len(text)}


def _key(req):
    pag = _get_pyautogui()
    key = req.get("key", "")
    # Handle combos like ctrl+c, alt+tab
    if "+" in key:
        parts = [k.strip().lower() for k in key.split("+")]
        pag.hotkey(*parts)
    else:
        pag.press(key.lower())
    return {"pressed": key}


def _move(req):
    pag = _get_pyautogui()
    x, y = int(req.get("x", 0)), int(req.get("y", 0))
    pag.moveTo(x, y)
    return {"moved": True, "x": x, "y": y}


def _scroll(req):
    pag = _get_pyautogui()
    x, y = int(req.get("x", 0)), int(req.get("y", 0))
    amount = int(req.get("amount", 3))
    pag.scroll(-amount, x=x, y=y)  # pyautogui: negative = down
    return {"scrolled": amount}


def _mouse_position(req):
    pag = _get_pyautogui()
    pos = pag.position()
    return {"x": pos.x, "y": pos.y}

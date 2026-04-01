"""Screen actions for Windows/macOS host (via pyautogui).

Used by pawflow_cli/relay.py for local_screen actions on the user's
actual desktop. Linux Docker containers use fs_screen.py (xdotool) instead.
"""

import base64
import io
import sys

_BUTTON_MAP = {"left": "left", "right": "right", "middle": "middle"}
_dpi_scale = None


def _get_dpi_scale():
    """Get DPI scale factor (physical pixels / logical pixels)."""
    global _dpi_scale
    if _dpi_scale is not None:
        return _dpi_scale
    if sys.platform == "win32":
        try:
            import ctypes
            # Make process DPI-aware to get correct coordinates
            ctypes.windll.user32.SetProcessDPIAware()
            # Get system DPI (96 = 100%, 120 = 125%, 144 = 150%)
            dc = ctypes.windll.user32.GetDC(0)
            dpi = ctypes.windll.gdi32.GetDeviceCaps(dc, 88)  # LOGPIXELSX
            ctypes.windll.user32.ReleaseDC(0, dc)
            _dpi_scale = dpi / 96.0
        except Exception:
            _dpi_scale = 1.0
    else:
        _dpi_scale = 1.0
    return _dpi_scale


def _scale_coords(x, y):
    """Convert screenshot pixel coords to pyautogui logical coords."""
    s = _get_dpi_scale()
    return int(x / s), int(y / s)


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
    x, y = _scale_coords(int(req.get("x", 0)), int(req.get("y", 0)))
    button = _BUTTON_MAP.get(req.get("button", "left"), "left")
    pag.click(x, y, button=button)
    return {"clicked": True, "x": x, "y": y}


def _double_click(req):
    pag = _get_pyautogui()
    x, y = _scale_coords(int(req.get("x", 0)), int(req.get("y", 0)))
    pag.doubleClick(x, y)
    return {"double_clicked": True, "x": x, "y": y}


def _type(req):
    pag = _get_pyautogui()
    text = req.get("text", "")
    # pyautogui.write() uses keyboard events; on Windows it may fail
    # for special chars. Try write first, fall back to press per char.
    try:
        pag.write(text, interval=0.02)
    except Exception:
        for ch in text:
            try:
                pag.press(ch)
            except Exception:
                pass
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
    x, y = _scale_coords(int(req.get("x", 0)), int(req.get("y", 0)))
    pag.moveTo(x, y)
    return {"moved": True, "x": x, "y": y}


def _scroll(req):
    pag = _get_pyautogui()
    x, y = _scale_coords(int(req.get("x", 0)), int(req.get("y", 0)))
    amount = int(req.get("amount", 3))
    pag.scroll(-amount, x=x, y=y)  # pyautogui: negative = down
    return {"scrolled": amount}


def _mouse_position(req):
    pag = _get_pyautogui()
    pos = pag.position()
    return {"x": pos.x, "y": pos.y}

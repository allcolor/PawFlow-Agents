"""Screen automation actions for the filesystem relay.

Split from fs_actions.py — pyautogui/mss screen control.
"""


def _get_screen_libs():
    """Lazy import screen automation libs. Returns (pyautogui, mss) or raises."""
    try:
        import pyautogui
        pyautogui.FAILSAFE = True  # move mouse to corner to abort
        return pyautogui
    except ImportError:
        raise RuntimeError(
            "pyautogui not installed. Run: pip install pyautogui mss")

def action_screen_screenshot(root_dir, abs_path, req):
    import base64
    try:
        import mss
        with mss.mss() as sct:
            img = sct.grab(sct.monitors[0])
            # Convert to PNG bytes
            from mss.tools import to_png
            png = to_png(img.rgb, img.size)
        return base64.b64encode(png).decode("ascii")
    except ImportError:
        pag = _get_screen_libs()
        import io
        screenshot = pag.screenshot()
        buf = io.BytesIO()
        screenshot.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")

def action_screen_click(root_dir, abs_path, req):
    pag = _get_screen_libs()
    x, y = int(req.get("x", 0)), int(req.get("y", 0))
    button = req.get("button", "left")
    pag.click(x, y, button=button)
    return {"clicked": True, "x": x, "y": y}

def action_screen_double_click(root_dir, abs_path, req):
    pag = _get_screen_libs()
    x, y = int(req.get("x", 0)), int(req.get("y", 0))
    pag.doubleClick(x, y)
    return {"double_clicked": True, "x": x, "y": y}

def action_screen_type(root_dir, abs_path, req):
    pag = _get_screen_libs()
    text = req.get("text", "")
    pag.write(text, interval=0.02)
    return {"typed": len(text)}

def action_screen_key(root_dir, abs_path, req):
    pag = _get_screen_libs()
    key = req.get("key", "")
    # Support combos like "ctrl+c", "alt+tab"
    if "+" in key:
        keys = [k.strip() for k in key.split("+")]
        pag.hotkey(*keys)
    else:
        pag.press(key)
    return {"pressed": key}

def action_screen_move(root_dir, abs_path, req):
    pag = _get_screen_libs()
    x, y = int(req.get("x", 0)), int(req.get("y", 0))
    pag.moveTo(x, y, duration=0.2)
    return {"moved": True, "x": x, "y": y}

def action_screen_scroll(root_dir, abs_path, req):
    pag = _get_screen_libs()
    x, y = int(req.get("x", 0)), int(req.get("y", 0))
    amount = int(req.get("amount", 3))
    pag.scroll(amount, x=x, y=y)
    return {"scrolled": amount}

def action_screen_mouse_position(root_dir, abs_path, req):
    pag = _get_screen_libs()
    pos = pag.position()
    return {"x": pos.x, "y": pos.y}

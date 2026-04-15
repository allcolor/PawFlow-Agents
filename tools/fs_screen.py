"""Screen automation actions for the filesystem relay.

Split from fs_actions.py — xdotool + mss screen control.
Uses xdotool for all input actions (click, type, key, move, scroll)
and mss for screenshots. Falls back to pyautogui only if xdotool
is unavailable.

Auto-starts a virtual desktop (Xvfb+openbox) if no DISPLAY is available.
"""

import os
import shutil
import subprocess
import time

_desktop_started = False


def _ensure_desktop():
    """Ensure a virtual desktop is running. Idempotent."""
    global _desktop_started
    if _desktop_started and os.environ.get("DISPLAY"):
        return
    if os.environ.get("DISPLAY"):
        _desktop_started = True
        return
    _display = ":99"
    try:
        subprocess.Popen(
            ["Xvfb", _display, "-screen", "0", "1280x800x24",
             "-ac", "+extension", "GLX", "+render", "-noreset"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.environ["DISPLAY"] = _display
        time.sleep(0.5)
        subprocess.Popen(
            ["openbox-session"],
            env={**os.environ, "DISPLAY": _display},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.3)
        _desktop_started = True
    except FileNotFoundError:
        pass


def _display_env():
    """Return env dict with DISPLAY set."""
    return {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":99")}


def _xdo(*args, timeout=5):
    """Run an xdotool command. Returns stdout."""
    result = subprocess.run(
        ["xdotool"] + list(args),
        capture_output=True, text=True, timeout=timeout,
        env=_display_env())
    if result.returncode != 0 and result.stderr.strip():
        raise RuntimeError(f"xdotool failed: {result.stderr.strip()}")
    return result.stdout.strip()


_BUTTON_MAP = {"left": "1", "middle": "2", "right": "3"}

_KEY_MAP = {
    "enter": "Return", "return": "Return",
    "tab": "Tab", "escape": "Escape", "esc": "Escape",
    "space": "space", "backspace": "BackSpace",
    "delete": "Delete", "del": "Delete",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "home": "Home", "end": "End",
    "pageup": "Page_Up", "page_up": "Page_Up",
    "pagedown": "Page_Down", "page_down": "Page_Down",
    "insert": "Insert",
    "f1": "F1", "f2": "F2", "f3": "F3", "f4": "F4",
    "f5": "F5", "f6": "F6", "f7": "F7", "f8": "F8",
    "f9": "F9", "f10": "F10", "f11": "F11", "f12": "F12",
    "ctrl": "ctrl", "alt": "alt", "shift": "shift",
    "super": "super", "win": "super", "meta": "super",
    "capslock": "Caps_Lock", "numlock": "Num_Lock",
}


def _map_key(key):
    """Map a key name (possibly a combo like ctrl+c) to xdotool keysym(s)."""
    if "+" in key:
        parts = [_KEY_MAP.get(k.strip().lower(), k.strip()) for k in key.split("+")]
        return "+".join(parts)
    return _KEY_MAP.get(key.lower(), key)


def action_screen_screenshot(root_dir, abs_path, req):
    _ensure_desktop()
    import base64
    try:
        import mss
        with mss.mss() as sct:
            img = sct.grab(sct.monitors[0])
            w, h = img.size
            from mss.tools import to_png
            png = to_png(img.rgb, img.size)
        return {"image": base64.b64encode(png).decode("ascii"), "width": w, "height": h}
    except ImportError:
        import io, pyautogui
        screenshot = pyautogui.screenshot()
        w, h = screenshot.size
        buf = io.BytesIO()
        screenshot.save(buf, format="PNG")
        return {"image": base64.b64encode(buf.getvalue()).decode("ascii"), "width": w, "height": h}


def action_screen_screenshot_region(root_dir, abs_path, req):
    """Screenshot a specific region. Params: x, y, width, height."""
    _ensure_desktop()
    import base64
    x = int(req.get("x", 0))
    y = int(req.get("y", 0))
    w = int(req.get("width", 400))
    h = int(req.get("height", 300))
    try:
        import mss
        with mss.mss() as sct:
            region = {"left": x, "top": y, "width": w, "height": h}
            img = sct.grab(region)
            from mss.tools import to_png
            png = to_png(img.rgb, img.size)
        return base64.b64encode(png).decode("ascii")
    except ImportError:
        import io, pyautogui
        screenshot = pyautogui.screenshot(region=(x, y, w, h))
        buf = io.BytesIO()
        screenshot.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")


def action_screen_click(root_dir, abs_path, req):
    _ensure_desktop()
    x, y = int(req.get("x", 0)), int(req.get("y", 0))
    button = req.get("button", "left")
    btn = _BUTTON_MAP.get(button, "1")
    _xdo("mousemove", str(x), str(y))
    _xdo("click", btn)
    return {"clicked": True, "x": x, "y": y}


def action_screen_double_click(root_dir, abs_path, req):
    _ensure_desktop()
    x, y = int(req.get("x", 0)), int(req.get("y", 0))
    _xdo("mousemove", str(x), str(y))
    _xdo("click", "--repeat", "2", "--delay", "50", "1")
    return {"double_clicked": True, "x": x, "y": y}


def action_screen_triple_click(root_dir, abs_path, req):
    _ensure_desktop()
    x, y = int(req.get("x", 0)), int(req.get("y", 0))
    _xdo("mousemove", str(x), str(y))
    _xdo("click", "--repeat", "3", "--delay", "50", "1")
    return {"triple_clicked": True, "x": x, "y": y}


def action_screen_right_click(root_dir, abs_path, req):
    _ensure_desktop()
    x, y = int(req.get("x", 0)), int(req.get("y", 0))
    _xdo("mousemove", str(x), str(y))
    _xdo("click", "3")
    return {"right_clicked": True, "x": x, "y": y}


def action_screen_type(root_dir, abs_path, req):
    _ensure_desktop()
    text = req.get("text", "")
    _xdo("type", "--clearmodifiers", "--delay", "20", "--", text)
    return {"typed": len(text)}


def action_screen_key(root_dir, abs_path, req):
    _ensure_desktop()
    key = req.get("key", "")
    mapped = _map_key(key)
    _xdo("key", "--clearmodifiers", mapped)
    return {"pressed": key}


def action_screen_move(root_dir, abs_path, req):
    _ensure_desktop()
    x, y = int(req.get("x", 0)), int(req.get("y", 0))
    _xdo("mousemove", str(x), str(y))
    return {"moved": True, "x": x, "y": y}


def action_screen_scroll(root_dir, abs_path, req):
    _ensure_desktop()
    x, y = int(req.get("x", 0)), int(req.get("y", 0))
    amount = int(req.get("amount", 3))
    _xdo("mousemove", str(x), str(y))
    if amount > 0:
        btn = "5"
    else:
        btn = "4"
        amount = -amount
    for _ in range(amount):
        _xdo("click", btn)
    return {"scrolled": int(req.get("amount", 3))}


def action_screen_mouse_position(root_dir, abs_path, req):
    _ensure_desktop()
    out = _xdo("getmouselocation")
    parts = dict(p.split(":", 1) for p in out.split() if ":" in p)
    return {"x": int(parts.get("x", 0)), "y": int(parts.get("y", 0))}


def action_screen_drag(root_dir, abs_path, req):
    """Drag from (x1,y1) to (x2,y2)."""
    _ensure_desktop()
    x1, y1 = int(req.get("x1", 0)), int(req.get("y1", 0))
    x2, y2 = int(req.get("x2", 0)), int(req.get("y2", 0))
    button = req.get("button", "left")
    btn = _BUTTON_MAP.get(button, "1")
    _xdo("mousemove", "--sync", str(x1), str(y1))
    _xdo("mousedown", btn)
    _xdo("mousemove", "--sync", str(x2), str(y2))
    _xdo("mouseup", btn)
    return {"dragged": True, "from": [x1, y1], "to": [x2, y2]}


def action_screen_size(root_dir, abs_path, req):
    _ensure_desktop()
    try:
        import mss
        with mss.mss() as sct:
            m = sct.monitors[0]
            return {"width": m["width"], "height": m["height"]}
    except ImportError:
        out = subprocess.run(
            ["xdpyinfo"], capture_output=True, text=True,
            timeout=5, env=_display_env())
        for line in out.stdout.split("\n"):
            if "dimensions:" in line:
                dim = line.split(":", 1)[1].strip().split()[0]
                w, h = dim.split("x")
                return {"width": int(w), "height": int(h)}
        return {"error": "Could not determine screen size"}


def action_screen_wait(root_dir, abs_path, req):
    secs = float(req.get("seconds", 1))
    secs = min(secs, 30)
    time.sleep(secs)
    return {"waited": secs}


def action_screen_open_app(root_dir, abs_path, req):
    _ensure_desktop()
    cmd = req.get("command", "")
    wait = req.get("wait", False)
    if not cmd:
        return {"error": "No command specified"}
    import shlex
    cmd_list = shlex.split(cmd) if isinstance(cmd, str) else cmd
    proc = subprocess.Popen(
        cmd_list, env=_display_env(),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if wait:
        time.sleep(float(req.get("wait_seconds", 2)))
    return {"pid": proc.pid, "command": cmd_list[0]}


def action_screen_clipboard_read(root_dir, abs_path, req):
    _ensure_desktop()
    try:
        result = subprocess.run(
            ["xclip", "-selection", "clipboard", "-o"],
            capture_output=True, text=True, timeout=5, env=_display_env())
        return {"text": result.stdout}
    except FileNotFoundError:
        return {"error": "xclip not installed"}


def action_screen_clipboard_write(root_dir, abs_path, req):
    _ensure_desktop()
    text = req.get("text", "")
    try:
        proc = subprocess.Popen(
            ["xclip", "-selection", "clipboard"],
            stdin=subprocess.PIPE, env=_display_env())
        proc.communicate(input=text.encode("utf-8"), timeout=5)
        return {"written": len(text)}
    except FileNotFoundError:
        return {"error": "xclip not installed"}


def action_screen_window_list(root_dir, abs_path, req):
    _ensure_desktop()
    try:
        result = subprocess.run(
            ["wmctrl", "-l", "-p"],
            capture_output=True, text=True, timeout=5, env=_display_env())
        windows = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split(None, 4)
            windows.append({
                "id": parts[0] if len(parts) > 0 else "",
                "desktop": parts[1] if len(parts) > 1 else "",
                "pid": parts[2] if len(parts) > 2 else "",
                "host": parts[3] if len(parts) > 3 else "",
                "title": parts[4] if len(parts) > 4 else "",
            })
        return {"windows": windows}
    except FileNotFoundError:
        return {"error": "wmctrl not installed"}


def action_screen_window_focus(root_dir, abs_path, req):
    _ensure_desktop()
    title = req.get("title", "")
    wid = req.get("id", "")
    try:
        if wid:
            subprocess.run(["wmctrl", "-i", "-a", wid], env=_display_env(), timeout=5)
        elif title:
            subprocess.run(["wmctrl", "-a", title], env=_display_env(), timeout=5)
        else:
            return {"error": "No title or id specified"}
        return {"focused": title or wid}
    except FileNotFoundError:
        return {"error": "wmctrl not installed"}


def action_screen_window_close(root_dir, abs_path, req):
    _ensure_desktop()
    title = req.get("title", "")
    wid = req.get("id", "")
    try:
        if wid:
            subprocess.run(["wmctrl", "-i", "-c", wid], env=_display_env(), timeout=5)
        elif title:
            subprocess.run(["wmctrl", "-c", title], env=_display_env(), timeout=5)
        else:
            return {"error": "No title or id specified"}
        return {"closed": title or wid}
    except FileNotFoundError:
        return {"error": "wmctrl not installed"}


def action_screen_window_resize(root_dir, abs_path, req):
    _ensure_desktop()
    wid = req.get("id", "")
    title = req.get("title", "")
    x = int(req.get("x", 0))
    y = int(req.get("y", 0))
    w = int(req.get("width", 800))
    h = int(req.get("height", 600))
    mvarg = f"0,{x},{y},{w},{h}"
    try:
        if wid:
            subprocess.run(["wmctrl", "-i", "-r", wid, "-e", mvarg], env=_display_env(), timeout=5)
        elif title:
            subprocess.run(["wmctrl", "-r", title, "-e", mvarg], env=_display_env(), timeout=5)
        else:
            return {"error": "No title or id specified"}
        return {"resized": True, "geometry": {"x": x, "y": y, "width": w, "height": h}}
    except FileNotFoundError:
        return {"error": "wmctrl not installed"}


def action_screen_window_minimize(root_dir, abs_path, req):
    _ensure_desktop()
    wid = req.get("id", "")
    title = req.get("title", "")
    try:
        if wid:
            _xdo("windowminimize", wid)
        elif title:
            out = _xdo("search", "--name", title)
            for line in out.split("\n"):
                if line.strip():
                    _xdo("windowminimize", line.strip())
                    break
        return {"minimized": title or wid}
    except FileNotFoundError:
        return {"error": "xdotool not installed"}


def action_screen_window_maximize(root_dir, abs_path, req):
    _ensure_desktop()
    wid = req.get("id", "")
    title = req.get("title", "")
    try:
        cmd_base = ["wmctrl"]
        if wid:
            cmd_base += ["-i", "-r", wid]
        elif title:
            cmd_base += ["-r", title]
        else:
            return {"error": "No title or id specified"}
        subprocess.run(cmd_base + ["-b", "add,maximized_vert,maximized_horz"],
                       env=_display_env(), timeout=5)
        return {"maximized": title or wid}
    except FileNotFoundError:
        return {"error": "wmctrl not installed"}


def action_screen_ocr(root_dir, abs_path, req):
    _ensure_desktop()
    x = req.get("x")
    y = req.get("y")
    w = req.get("width")
    h = req.get("height")
    lang = req.get("lang", "eng")
    try:
        import mss
        from PIL import Image
        with mss.mss() as sct:
            if x is not None and y is not None and w and h:
                region = {"left": int(x), "top": int(y), "width": int(w), "height": int(h)}
            else:
                region = sct.monitors[0]
            img_mss = sct.grab(region)
            img = Image.frombytes("RGB", img_mss.size, img_mss.bgra, "raw", "BGRX")
    except ImportError:
        import pyautogui
        if x is not None and y is not None and w and h:
            img = pyautogui.screenshot(region=(int(x), int(y), int(w), int(h)))
        else:
            img = pyautogui.screenshot()
    try:
        import pytesseract
        text = pytesseract.image_to_string(img, lang=lang)
        return {"text": text.strip()}
    except ImportError:
        return {"error": "pytesseract not installed. apt install tesseract-ocr && pip install pytesseract"}


def action_screen_locate(root_dir, abs_path, req):
    _ensure_desktop()
    import base64, io
    template_b64 = req.get("template", "")
    confidence = float(req.get("confidence", 0.8))
    if not template_b64:
        return {"error": "No template image provided (base64 PNG)"}
    from PIL import Image
    template_bytes = base64.b64decode(template_b64)
    template_img = Image.open(io.BytesIO(template_bytes))
    tmp = "/tmp/_locate_template.png"
    template_img.save(tmp)
    try:
        import pyautogui
        loc = pyautogui.locateOnScreen(tmp, confidence=confidence)
        if loc:
            cx, cy = pyautogui.center(loc)
            return {"found": True, "x": cx, "y": cy, "left": loc.left, "top": loc.top,
                    "width": loc.width, "height": loc.height}
        return {"found": False}
    except Exception as e:
        return {"error": str(e)}

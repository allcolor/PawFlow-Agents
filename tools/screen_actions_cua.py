"""CUA screen backend — background computer use via cua-driver.

Enabled with ``PAWFLOW_SCREEN_MODE=cua`` on the relay host. Maps PawFlow's
coordinate-based ``screen_*`` actions onto cua-driver desktop-scope tools
using the CLI form (``cua-driver <tool> '<json>'``) — the same
subprocess-per-action pattern as the pyautogui/xdotool backends, no MCP
client required.

Contract (see docs/CUA_MODE_PLAN.md):
- the real OS cursor is never moved; each session gets its own overlay
  agent cursor (session id from ``PAWFLOW_CUA_SESSION``, default
  "pawflow");
- the pre-click screen guard keeps running exactly as in pawflow mode;
- structured refusals (``background_unavailable`` /
  ``background_occluded``) and driver errors surface verbatim as tool
  errors — there is NO silent fallback to foreground injection.
"""

import base64
import json
import logging
import os
import re
import subprocess  # nosec B404
import tempfile

logger = logging.getLogger(__name__)

MODE_ENV = "PAWFLOW_SCREEN_MODE"
BIN_ENV = "PAWFLOW_CUA_BIN"
SESSION_ENV = "PAWFLOW_CUA_SESSION"

_DEFAULT_TIMEOUT = 15


def cua_mode_enabled() -> bool:
    return (os.environ.get(MODE_ENV) or "").strip().lower() == "cua"


def _cua_bin() -> str:
    return (os.environ.get(BIN_ENV) or "").strip() or "cua-driver"


def _session(req: dict) -> str:
    return (
        str(req.get("_cua_session") or "").strip()
        or (os.environ.get(SESSION_ENV) or "").strip()
        or "pawflow"
    )


def _screen_actions_module():
    """Shared helpers live in screen_actions; import works both from the
    repo package (tools.screen_actions) and the staged flat runtime dir."""
    try:
        from tools import screen_actions as sa
    except ImportError:
        import screen_actions as sa
    return sa


def _run_tool(tool: str, args: dict, timeout: float) -> dict:
    """Run one cua-driver tool. Returns {} on success (plus parsed JSON
    payload when the driver prints one) or {"error": ...} on failure."""
    cmd = [_cua_bin(), tool, json.dumps(args)]
    try:
        proc = subprocess.run(  # nosec B603
            cmd, capture_output=True, text=True,
            timeout=max(1, timeout), check=False)
    except FileNotFoundError:
        return {"error": (
            f"cua-driver binary not found ({_cua_bin()!r}). Install it "
            "(https://cua.ai/driver/install.sh) or unset "
            f"{MODE_ENV} to return to the default screen backend.")}
    except subprocess.TimeoutExpired:
        return {"error": f"cua-driver {tool} timed out after {int(timeout)}s"}
    out = (proc.stdout or "").strip()
    if proc.returncode != 0:
        # Structured refusals (background_unavailable/background_occluded)
        # and driver errors arrive here — surfaced verbatim, never
        # downgraded to foreground injection.
        return {"error": (proc.stderr or out
                          or f"cua-driver {tool} exit {proc.returncode}").strip()}
    payload = _parse_json_payload(out)
    result = {"cua": True}
    if payload is not None:
        result["result"] = payload
    return result


def _parse_json_payload(text: str):
    """Extract the trailing JSON object/array from a CLI response, if any
    (responses are a '✅ summary' line plus optional structured content)."""
    match = re.search(r"(\{.*\}|\[.*\])\s*$", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except (ValueError, TypeError):
        return None


def _timeout(req: dict) -> float:
    try:
        return float(req.get("timeout", _DEFAULT_TIMEOUT) or _DEFAULT_TIMEOUT)
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT


def handle_screen_action_cua(action: str, req: dict) -> dict:
    dispatch = {
        "screen_screenshot": _screenshot,
        "screen_click": _click,
        "screen_double_click": _double_click,
        "screen_type": _type,
        "screen_key": _key,
        "screen_move": _move,
        "screen_scroll": _scroll,
        "screen_mouse_position": _mouse_position,
        "screen_status": _status,
    }
    fn = dispatch.get(action)
    if not fn:
        return {"error": f"Unknown screen action: {action}"}
    try:
        return fn(req)
    except Exception as e:  # match pawflow-backend behavior
        return {"error": str(e)}


def _screenshot(req: dict) -> dict:
    fd, path = tempfile.mkstemp(suffix=".png", prefix="pawflow_cua_")
    os.close(fd)
    try:
        result = _run_tool("get_desktop_state", {
            "screenshot_out_file": path,
            "session": _session(req),
        }, _timeout(req))
        if "error" in result:
            return result
        with open(path, "rb") as fh:
            png = fh.read()
        if not png:
            return {"error": "cua-driver returned an empty screenshot"}
        width, height = _png_size(png)
        return {
            "image": base64.b64encode(png).decode("ascii"),
            "width": width, "height": height,
        }
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _png_size(png: bytes):
    """Width/height from the IHDR chunk (no PIL dependency needed)."""
    if len(png) < 24 or png[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    return (int.from_bytes(png[16:20], "big"),
            int.from_bytes(png[20:24], "big"))


def _click(req: dict) -> dict:
    stale = _screen_actions_module()._validate_screen_guard(req)
    if stale:
        return stale
    x, y = int(req.get("x", 0)), int(req.get("y", 0))
    button = str(req.get("button", "left") or "left")
    result = _run_tool("click", {
        "x": x, "y": y, "scope": "desktop", "button": button,
        "session": _session(req),
    }, _timeout(req))
    if "error" in result:
        return result
    return {"clicked": True, "x": x, "y": y, "cua": True}


def _double_click(req: dict) -> dict:
    stale = _screen_actions_module()._validate_screen_guard(req)
    if stale:
        return stale
    x, y = int(req.get("x", 0)), int(req.get("y", 0))
    result = _run_tool("click", {
        "x": x, "y": y, "scope": "desktop", "button": "left",
        "click_count": 2, "session": _session(req),
    }, _timeout(req))
    if "error" in result:
        return result
    return {"double_clicked": True, "x": x, "y": y, "cua": True}


def _type(req: dict) -> dict:
    text = str(req.get("text", ""))
    result = _run_tool("type_text", {
        "text": text, "scope": "desktop", "session": _session(req),
    }, _timeout(req))
    if "error" in result:
        return result
    return {"typed": len(text), "cua": True}


def _key(req: dict) -> dict:
    key = str(req.get("key", ""))
    result = _run_tool("press_key", {
        "keys": key, "scope": "desktop", "session": _session(req),
    }, _timeout(req))
    if "error" in result:
        return result
    return {"pressed": key, "cua": True}


def _move(req: dict) -> dict:
    # cua-driver never moves the real OS cursor — that is the whole point
    # of the mode. Report honestly instead of pretending.
    return {
        "moved": False,
        "reason": "cua_background_mode",
        "note": ("CUA mode never moves the real cursor; the overlay agent "
                 "cursor follows click/scroll targets automatically."),
    }


def _scroll(req: dict) -> dict:
    x, y = int(req.get("x", 0)), int(req.get("y", 0))
    amount = int(req.get("amount", 3))
    result = _run_tool("scroll", {
        "x": x, "y": y, "scope": "desktop", "amount": amount,
        "session": _session(req),
    }, _timeout(req))
    if "error" in result:
        return result
    return {"scrolled": amount, "cua": True}


def _mouse_position(req: dict) -> dict:
    result = _run_tool("get_cursor_position", {}, _timeout(req))
    if "error" in result:
        return result
    payload = result.get("result") or {}
    if isinstance(payload, dict) and "x" in payload and "y" in payload:
        return {"x": int(payload["x"]), "y": int(payload["y"])}
    return result


def _status(req: dict) -> dict:
    """Health check: surfaces cua-driver's structured health_report."""
    result = _run_tool("health_report", {}, _timeout(req))
    result.setdefault("mode", "cua")
    result.setdefault("binary", _cua_bin())
    return result

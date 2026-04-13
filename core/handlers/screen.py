"""Screen interaction tool — screenshots, clicks, typing via relay."""
import json
import logging
from typing import Any, Dict

from core.handlers._fs_base import BaseFsHandler

logger = logging.getLogger(__name__)


class ScreenHandler(BaseFsHandler):
    """Control the desktop: screenshots, mouse, keyboard.

    Routes screen_* actions to a filesystem relay (user's PC) or
    executes locally on the Docker virtual screen.

    local=true  → relay (user's actual desktop)
    local=false → Docker Xvfb virtual screen
    """

    _fs_service = None
    _user_id: str = ""
    _base_url: str = ""

    @property
    def name(self) -> str:
        return "screen"

    @property
    def description(self) -> str:
        return (
            "Interact with a desktop screen. "
            "Take screenshots to see what's on screen, click elements, type text, "
            "press keys, scroll. Useful for GUI testing, visual verification, "
            "or when you need to see what the user sees.\n"
            "IMPORTANT: Set local=true to act on the user's real PC display, "
            "or local=false (default) for the Docker virtual screen.\n"
            "COORDINATES: x,y are in physical pixels matching the screenshot resolution. "
            "Always take a screenshot first — the result includes the screen resolution "
            "(e.g. 2560x1440). Use those pixel dimensions to calculate click/move coordinates."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["screenshot", "click", "double_click", "type",
                             "key", "move", "scroll", "mouse_position"],
                    "description": (
                        "screenshot: capture the screen (returns image). "
                        "click: click at (x,y). double_click: double-click at (x,y). "
                        "type: type text string. key: press a key (Enter, Tab, Escape, etc.). "
                        "move: move mouse to (x,y). scroll: scroll at (x,y). "
                        "mouse_position: get current mouse coordinates."
                    ),
                },
                "x": {"type": "integer", "description": "X coordinate (for click/move/scroll)"},
                "y": {"type": "integer", "description": "Y coordinate (for click/move/scroll)"},
                "text": {"type": "string", "description": "Text to type (for type action)"},
                "key": {"type": "string", "description": "Key name: Enter, Tab, Escape, Space, Backspace, Delete, Up, Down, Left, Right, F1-F12, ctrl+c, alt+tab, etc."},
                "button": {"type": "string", "description": "Mouse button: left (default), right, middle"},
                "amount": {"type": "integer", "description": "Scroll amount (positive=down, negative=up, default 3)"},
                "relay": {"type": "string", "description": "Relay service name. Omit to auto-select."},
                "local": {"type": "boolean", "description": "If true, execute on the user's local desktop (via relay). If false, use the Docker virtual screen. Default false."},
            },
            "required": ["action"],
        }

    def set_service(self, svc):
        self._fs_service = svc

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def set_base_url(self, base_url: str):
        self._base_url = base_url.rstrip("/")

    def _find_relay(self, relay_name: str = ""):
        """Find any relay service. Returns the service instance or None."""
        from core.handlers._fs_base import find_fs_service

        if relay_name:
            return find_fs_service(self._user_id, relay_name)

        if self._fs_service:
            return self._fs_service
        return find_fs_service(self._user_id)

    def execute(self, arguments: Dict[str, Any]) -> str:
        action = arguments.get("action", "")
        if not action:
            return "Error: missing action parameter"

        relay_name = arguments.get("relay", "")
        local = self._resolve_local(arguments)

        if local:
            # Route to relay — user's actual desktop
            svc = self._find_relay(relay_name)
            if not svc:
                return "Error: no relay connected. Connect a filesystem relay to capture the user's screen."
            return self._exec_via_relay(svc, action, arguments)
        else:
            # Execute locally on Docker virtual screen
            return self._exec_local(action, arguments)

    def _exec_via_relay(self, svc, action: str, arguments: dict) -> str:
        """Execute screen action via relay (user's desktop)."""
        req_args = {k: v for k, v in arguments.items()
                    if k not in ("action", "relay")}
        try:
            result = svc._request(f"screen_{action}", ".", **req_args)
        except Exception as e:
            err = str(e)
            if "Unknown action" in err or "not supported" in err:
                return (
                    "Error: relay does not support screen actions. "
                    "Update the relay or install screen automation dependencies "
                    "(pyautogui for Python relay, @nut-tree/nut-js for VS Code relay)."
                )
            return f"Error: screen action failed: {e}"

        if isinstance(result, dict) and not result.get("ok", True):
            return f"Error: {result.get('error', 'unknown error')}"

        return self._handle_result(action, result)

    def _exec_local(self, action: str, arguments: dict) -> str:
        """Execute screen action locally on Docker virtual screen."""
        try:
            from tools.fs_screen import (
                action_screen_screenshot, action_screen_click,
                action_screen_double_click, action_screen_type,
                action_screen_key, action_screen_move,
                action_screen_scroll, action_screen_mouse_position,
            )
        except ImportError:
            return "Error: screen automation not available in this environment (missing fs_screen)."

        req = {k: v for k, v in arguments.items()
               if k not in ("action", "relay", "local")}

        _actions = {
            "screenshot": action_screen_screenshot,
            "click": action_screen_click,
            "double_click": action_screen_double_click,
            "type": action_screen_type,
            "key": action_screen_key,
            "move": action_screen_move,
            "scroll": action_screen_scroll,
            "mouse_position": action_screen_mouse_position,
        }
        fn = _actions.get(action)
        if not fn:
            return f"Error: unknown screen action '{action}'"

        try:
            result = fn(".", ".", req)
        except Exception as e:
            return f"Error: screen action failed: {e}"

        return self._handle_result(action, result)

    def _handle_result(self, action: str, data) -> str:
        """Process screen action result — store screenshots, format responses."""
        # Screenshot: accept both raw base64 string and {image, width, height} dict
        if action == "screenshot":
            b64_data = None
            width = height = None
            if isinstance(data, dict) and "image" in data:
                b64_data = data["image"]
                width = data.get("width")
                height = data.get("height")
            elif isinstance(data, str):
                b64_data = data
            if b64_data:
                try:
                    import base64
                    img_bytes = base64.b64decode(b64_data)
                    from core.file_store import FileStore
                    import time
                    fname = f"screenshot_{int(time.time())}.png"
                    fid = FileStore.instance().store(
                        fname, img_bytes, "image/png",
                        user_id=self._user_id,
                        conversation_id=getattr(self, '_conversation_id', '') or '',
                        category="screenshot")
                    url = f"fs://filestore/{fid}/{fname}"
                    size_info = f"\nScreen resolution: {width}x{height}" if width and height else ""
                    coord_hint = f" — use these dimensions for x,y coordinates in click/move/scroll actions" if width else ""
                    return f"Screenshot captured: {url}\n{len(img_bytes):,} bytes, {b64_data[:20]}...{size_info}{coord_hint}"
                except Exception as e:
                    return f"Screenshot captured but storage failed: {e}"

        if action == "mouse_position" and isinstance(data, dict):
            return f"Mouse position: x={data.get('x', '?')}, y={data.get('y', '?')}"

        return f"OK: {action} completed"

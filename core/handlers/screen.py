"""Screen interaction tool — screenshots, clicks, typing via relay."""
import json
import logging
from typing import Any, Dict

from core.tool_registry import ToolHandler

logger = logging.getLogger(__name__)


class ScreenHandler(ToolHandler):
    """Control the user's desktop through the relay: screenshots, mouse, keyboard.

    Routes screen_* actions to the filesystem service relay (same transport).
    Requires the relay to have exec permissions enabled.
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
            "Interact with the user's desktop screen. "
            "Take screenshots to see what's on screen, click elements, type text, "
            "press keys, scroll. Useful for GUI testing, visual verification, "
            "or when you need to see what the user sees. "
            "Requires a connected relay with exec permissions."
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
            },
            "required": ["action"],
        }

    def set_service(self, svc):
        self._fs_service = svc

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def set_base_url(self, base_url: str):
        self._base_url = base_url.rstrip("/")

    def execute(self, arguments: Dict[str, Any]) -> str:
        if not self._fs_service:
            return "Error: no relay connected. Connect a filesystem relay with exec permissions."

        action = arguments.get("action", "")
        if not action:
            return "Error: missing action parameter"

        # Build relay request
        req_args = {k: v for k, v in arguments.items() if k != "action"}
        try:
            result = self._fs_service._request(f"screen_{action}", ".", **req_args)
        except Exception as e:
            err = str(e)
            if "Unknown action" in err or "not supported" in err:
                return (
                    "Error: relay does not support screen actions. "
                    "Update the relay or install screen automation dependencies "
                    "(pyautogui for Python relay, @nut-tree/nut-js for VS Code relay)."
                )
            return f"Error: screen action failed: {e}"

        if not result.get("ok"):
            return f"Error: {result.get('error', 'unknown error')}"

        data = result.get("data", {})

        # Screenshot: store image in FileStore and return URL
        if action == "screenshot" and isinstance(data, str):
            try:
                import base64
                img_bytes = base64.b64decode(data)
                from core.file_store import FileStore
                import time
                fname = f"screenshot_{int(time.time())}.png"
                fid = FileStore.instance().store(
                    fname, img_bytes, "image/png",
                    user_id=self._user_id, category="screenshot")
                url = f"{self._base_url}/files/{fid}/{fname}" if self._base_url else f"/files/{fid}/{fname}"
                return f"Screenshot captured: {url}\n{len(img_bytes):,} bytes, {data[:20]}..."
            except Exception as e:
                return f"Screenshot captured but storage failed: {e}"

        if action == "mouse_position":
            return f"Mouse position: x={data.get('x', '?')}, y={data.get('y', '?')}"

        return f"OK: {action} completed"

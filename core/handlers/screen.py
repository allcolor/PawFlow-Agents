"""Screen interaction tool — screenshots, clicks, typing via relay."""
import json
import logging
from typing import Any, Dict

from core.tool_registry import ToolHandler

logger = logging.getLogger(__name__)


class ScreenHandler(ToolHandler):
    """Control the user's desktop through the relay: screenshots, mouse, keyboard.

    Routes screen_* actions to a filesystem service relay.
    Supports local_screen mode: actions execute on the user's PC
    (requires a relay with --allow-local-screen).
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
            "or when you need to see what the user sees. "
            "Use local_screen=true to act on the user's own display "
            "(requires relay with --allow-local-screen)."
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
                "local_screen": {"type": "boolean", "description": "If true, execute on the user's local screen instead of Docker virtual screen. Requires relay with --allow-local-screen. Default false."},
            },
            "required": ["action"],
        }

    def set_service(self, svc):
        self._fs_service = svc

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def set_base_url(self, base_url: str):
        self._base_url = base_url.rstrip("/")

    def _find_relay(self, relay_name: str = "", local_screen: bool = False):
        """Find a relay service, optionally filtering by local_screen capability.

        Returns the service instance or None.
        """
        from core.handlers._fs_base import find_fs_service

        if relay_name:
            svc = find_fs_service(self._user_id, relay_name)
            if svc and local_screen:
                info = getattr(svc, '_relay_info', {}) or {}
                if not info.get("allow_local_screen"):
                    return None  # requested relay doesn't have local_screen
            return svc

        # Auto-select: if local_screen, find relay with that capability
        if local_screen:
            return self._find_local_screen_relay()

        # Default: use injected service or first available
        if self._fs_service:
            return self._fs_service
        return find_fs_service(self._user_id)

    def _find_local_screen_relay(self):
        """Find any relay with allow_local_screen=true."""
        try:
            from gui.services.user_service_registry import UserServiceRegistry
            ureg = UserServiceRegistry.get_instance()
            for sid, sdef in ureg.get_all_for_user(self._user_id).items():
                stype = getattr(sdef, "service_type", "")
                if stype != "relay" or not sdef.enabled:
                    continue
                svc = ureg.get_live_instance(self._user_id, sid)
                if svc:
                    info = getattr(svc, '_relay_info', {}) or {}
                    if info.get("allow_local_screen"):
                        return svc
        except Exception:
            pass
        try:
            from gui.services.global_service_registry import GlobalServiceRegistry
            greg = GlobalServiceRegistry.get_instance()
            for sid, sdef in greg.get_all_definitions().items():
                stype = getattr(sdef, "service_type", "")
                if stype != "relay" or not getattr(sdef, "enabled", True):
                    continue
                svc = greg.get_live_instance(sid)
                if svc:
                    info = getattr(svc, '_relay_info', {}) or {}
                    if info.get("allow_local_screen"):
                        return svc
        except Exception:
            pass
        return None

    def execute(self, arguments: Dict[str, Any]) -> str:
        action = arguments.get("action", "")
        if not action:
            return "Error: missing action parameter"

        relay_name = arguments.get("relay", "")
        local_screen = bool(arguments.get("local_screen", False))

        svc = self._find_relay(relay_name, local_screen)
        if not svc:
            if local_screen:
                return (
                    "Error: no relay with local screen access found. "
                    "Start a relay with --allow-local-screen to enable this."
                )
            return "Error: no relay connected. Connect a filesystem relay with exec permissions."

        # Build relay request
        req_args = {k: v for k, v in arguments.items()
                    if k not in ("action", "relay", "local_screen")}
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

        # _request() unwraps the relay response — result IS the data directly
        if isinstance(result, dict) and not result.get("ok", True):
            return f"Error: {result.get('error', 'unknown error')}"

        data = result

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

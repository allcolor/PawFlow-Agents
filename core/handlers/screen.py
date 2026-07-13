"""Screen interaction tool — screenshots, guarded clicks, typing via relay."""
import logging
from typing import Any, Dict

from core.handlers._fs_base import BaseFsHandler
from core.handlers._screen_guard import (
    SCREENSHOT_TTL_SECONDS,
    prepare_click_guard,
    screen_route_key,
    store_screen_capture,
)

__all__ = ["SCREENSHOT_TTL_SECONDS", "ScreenHandler"]

logger = logging.getLogger(__name__)
class ScreenHandler(BaseFsHandler):
    """Control the desktop: screenshots, mouse, keyboard.

    Always routes through the filesystem relay — the PawFlow server
    never runs a display itself.

    local=true  → user's REAL desktop (relay → host helper)
    local=false → Docker virtual screen (relay's own Xvfb / container,
                  i.e. the desktop you started via /desktop docker)
    """

    _fs_service = None
    _user_id: str = ""
    _conversation_id: str = ""
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
            "and an opaque screen revision. Use those pixel dimensions to calculate "
            "click/move coordinates; never use coordinates from the resized screenshot "
            "image rendered in chat. click/double_click require the revision and the relay "
            "compares the target region locally immediately before acting, without another "
            "LLM or vision call."
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
                "x": {"type": "integer", "description": "X coordinate in physical screenshot pixels, not resized chat-image pixels (for click/move/scroll)"},
                "y": {"type": "integer", "description": "Y coordinate in physical screenshot pixels, not resized chat-image pixels (for click/move/scroll)"},
                "text": {"type": "string", "description": "Text to type (for type action)"},
                "key": {"type": "string", "description": "Key name: Enter, Tab, Escape, Space, Backspace, Delete, Up, Down, Left, Right, F1-F12, ctrl+c, alt+tab, etc."},
                "button": {"type": "string", "description": "Mouse button: left (default), right, middle"},
                "amount": {"type": "integer", "description": "Scroll amount (positive=down, negative=up, default 3)"},
                "expected_screen_revision": {
                    "type": "string",
                    "description": (
                        "Opaque revision returned by the screenshot used to choose x,y. "
                        "Required for click and double_click; copy it exactly."
                    ),
                },
                "target_bbox": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": 4,
                    "maxItems": 4,
                    "description": (
                        "Optional target bounds [x, y, width, height] in physical screenshot "
                        "pixels. Improves the local stale-layout comparison."
                    ),
                },
                "timeout": {"type": "number", "description": "Maximum seconds to wait for the relay screen action before cancelling it (default 30)."},
                "relay": {"type": "string", "description": "Relay service name. Omit to auto-select."},
                "local": {"type": "boolean", "description": "If true, act on the user's REAL desktop (relay → host helper). If false (default), act on the Docker virtual desktop (relay's Xvfb / container, i.e. the one started via /desktop docker)."},
            },
            "required": ["action"],
        }

    def set_service(self, svc):
        self._fs_service = svc

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def set_conversation_id(self, conversation_id: str):
        self._conversation_id = conversation_id

    def set_base_url(self, base_url: str):
        self._base_url = base_url.rstrip("/")

    def _find_relay(self, relay_name: str = ""):
        """Find any relay service. Returns the service instance or None."""
        from core.handlers._fs_base import find_fs_service

        if relay_name:
            return find_fs_service(self._user_id, relay_name,
                                   conversation_id=self._conversation_id)

        if self._fs_service:
            return self._fs_service
        return find_fs_service(self._user_id,
                               conversation_id=self._conversation_id)

    def execute(self, arguments: Dict[str, Any]) -> str:
        action = arguments.get("action", "")
        if not action:
            return "Error: missing action parameter"

        relay_name = arguments.get("relay", "")
        svc = self._find_relay(relay_name)
        if not svc:
            return ("Error: no relay connected. Connect a filesystem relay "
                    "(local desktop or Docker desktop) before using screen.")
        # `local` is forwarded to the relay verbatim:
        #   local=true  → relay forwards to host helper (user's real desktop)
        #   local=false → relay's own Xvfb / Docker desktop container
        # The PawFlow server itself never runs a display — no local fallback.

        # Auto-start desktop if not running (local=false only)
        if not arguments.get("local", False):
            self._ensure_desktop_started(svc)

        return self._exec_via_relay(svc, action, arguments)

    def _ensure_desktop_started(self, svc):
        """Auto-start the Docker virtual desktop if not already running."""
        try:
            status = svc._request("desktop_status", ".")
            if isinstance(status, dict):
                data = status.get("data", status)
                if data.get("running"):
                    return
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        try:
            logger.info("[screen] auto-starting Docker desktop")
            svc._request("start_desktop", ".")
        except Exception as e:
            logger.warning("[screen] auto-start desktop failed: %s", e)

    def _exec_via_relay(self, svc, action: str, arguments: dict) -> str:
        """Execute screen action via relay. `local` flag is passed through."""
        req_args = {k: v for k, v in arguments.items()
                    if k not in ("action", "relay")}
        route_key = screen_route_key(svc, bool(req_args.get("local", False)))
        if action in ("click", "double_click"):
            revision = str(req_args.pop("expected_screen_revision", "") or "")
            target_bbox = req_args.pop("target_bbox", None)
            try:
                x = int(req_args["x"])
                y = int(req_args["y"])
                req_args["_screen_guard"] = prepare_click_guard(
                    revision,
                    user_id=self._user_id,
                    conversation_id=self._conversation_id,
                    route_key=route_key,
                    x=x,
                    y=y,
                    target_bbox=target_bbox,
                )
            except KeyError:
                return "Error: x and y are required for click actions"
            except (TypeError, ValueError, FileNotFoundError) as exc:
                return f"Error: {exc}"
        try:
            timeout = float(req_args.pop("timeout", 30) or 30)
        except (TypeError, ValueError):
            timeout = 30
        req_args["timeout"] = max(1, timeout - 1)
        try:
            result = svc._request(
                f"screen_{action}", ".", _request_timeout=timeout,
                **req_args)
        except Exception as e:
            err = str(e)
            if "Unknown action" in err or "not supported" in err:
                return (
                    "Error: relay does not support screen actions. "
                    "Update the relay or install screen automation dependencies "
                    "(pyautogui on the relay host or desktop-capable relay image)."
                )
            return f"Error: screen action failed: {e}"

        if isinstance(result, dict) and not result.get("ok", True):
            return f"Error: {result.get('error', 'unknown error')}"

        return self._handle_result(action, result, route_key=route_key)

    def _handle_result(self, action: str, data, *, route_key: str = "") -> str:
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
                    conversation_id = getattr(self, '_conversation_id', '') or ''
                    url, revision = store_screen_capture(
                        img_bytes,
                        user_id=self._user_id,
                        conversation_id=conversation_id,
                        route_key=route_key)
                    size_info = f"\nScreen resolution: {width}x{height}" if width and height else ""
                    coord_hint = " — use these physical pixel dimensions for x,y coordinates; do not use the resized chat preview" if width else ""
                    return (
                        f"Screenshot captured: {url}\n"
                        f"Screen revision: {revision}\n"
                        "Pass this exact revision as expected_screen_revision for the next "
                        "click/double_click based on this image.\n"
                        f"{len(img_bytes):,} bytes, {b64_data[:20]}..."
                        f"{size_info}{coord_hint}"
                    )
                except Exception as e:
                    return f"Screenshot captured but storage failed: {e}"

        if isinstance(data, dict) and data.get("stale_screen"):
            difference = data.get("difference")
            difference_text = (
                f" (difference={float(difference):.4f})"
                if isinstance(difference, (int, float)) else "")
            return (
                "STALE_SCREEN: click cancelled before any mouse input because the "
                f"target region changed{difference_text}. Take a new screenshot, "
                "re-evaluate the target, and use its new screen revision."
            )
        if isinstance(data, dict) and data.get("error"):
            return f"Error: {data['error']}"

        if action == "mouse_position" and isinstance(data, dict):
            return f"Mouse position: x={data.get('x', '?')}, y={data.get('y', '?')}"

        return f"OK: {action} completed"

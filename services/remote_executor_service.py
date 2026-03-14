"""Remote Executor Service — relay backend for command execution.

Supports two modes:
1. WS Reverse (preferred): relay connects to server via WebSocket,
   commands routed through RelayConnectionManager.
2. HTTP Direct (legacy/local): direct HTTP POST to relay.

Config:
    relay_id: str   — Relay ID for WS mode (set automatically on WS connect)
    host: str       — Relay host for HTTP mode (default: "localhost")
    port: int       — Relay port for HTTP mode (default: 9877)
    secret: str     — Shared secret for authentication
    timeout: int    — Request timeout in seconds (default: 30)
    approval_mode: str — "auto" | "ask" | "strict" (default: "ask")
    allowed_actions: str — Comma-separated allowed actions (default: all)
"""

import json
import logging
import uuid
import urllib.request
import urllib.error
from typing import Any, Dict

from core import ServiceFactory, ServiceError
from core.base_service import BaseService

logger = logging.getLogger(__name__)


class RemoteExecutorService(BaseService):
    """Service wrapping an executor relay on the user's machine."""

    TYPE = "remoteExecutor"
    VERSION = "1.1.0"
    NAME = "Remote Executor"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._relay_id = self.config.get("relay_id", "")
        self._host = self.config.get("host", "localhost")
        self._port = int(self.config.get("port", 9877))
        self._secret = self.config.get("secret", "")
        self._timeout = int(self.config.get("timeout", 30))
        self._approval_mode = self.config.get("approval_mode", "ask")
        self._user_id = self.config.get("user_id", "")
        allowed = self.config.get("allowed_actions", "")
        self._allowed_actions = (
            {a.strip() for a in allowed.split(",") if a.strip()}
            if allowed else None  # None = all allowed
        )
        self._relay_info = None  # cached from ping()

    @property
    def url(self) -> str:
        return f"http://{self._host}:{self._port}"

    @property
    def approval_mode(self) -> str:
        return self._approval_mode

    @property
    def relay_id(self) -> str:
        return self._relay_id

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def _is_ws_mode(self) -> bool:
        """Check if this service should route via WS (relay_id set, no host/port override)."""
        return bool(self._relay_id)

    def _get_relay_connection(self):
        """Get the WS relay connection via RelayConnectionManager."""
        from core.relay_manager import RelayConnectionManager
        mgr = RelayConnectionManager.instance()
        return mgr.get(self._user_id, self._relay_id, relay_type="executor")

    def _request_http(self, payload: Dict[str, Any]) -> Any:
        """Send a request to the relay via HTTP and return the data field."""
        payload["secret"] = self._secret
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.url, data=body, method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                result = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            raise ServiceError(f"Executor relay connection failed: {e}")
        except Exception as e:
            raise ServiceError(f"Executor relay request failed: {e}")

        if not result.get("ok"):
            raise ServiceError(result.get("error", "Unknown relay error"))
        return result.get("data")

    def _request_ws(self, payload: Dict[str, Any]) -> Any:
        """Send a request to the relay via WS and return the data field."""
        from core.relay_manager import RelayConnectionManager
        mgr = RelayConnectionManager.instance()

        conn = mgr.get(self._user_id, self._relay_id, relay_type="executor")
        if not conn:
            raise ServiceError(
                "Relay disconnected. Restart the relay process.\n"
                "Run: python pyfi2_executor_relay.py --connect ws://<server>/ws/relay "
                "--token <api_key> --secret <secret> --dir <path>"
            )

        request_id = uuid.uuid4().hex[:12]
        payload["secret"] = conn.info.get("_secret", self._secret)

        try:
            result = mgr.send_command_sync(
                self._user_id, conn.relay_id,
                request_id, payload, timeout=self._timeout,
            )
        except (ConnectionError, TimeoutError) as e:
            raise ServiceError(str(e))

        if isinstance(result, dict) and not result.get("ok", True):
            raise ServiceError(result.get("error", "Unknown relay error"))
        return result

    def send_command(self, action: str, **kwargs) -> Dict[str, Any]:
        """Send a command to the relay."""
        if self._allowed_actions is not None:
            base = action.split("_")[0] if "_" in action else action
            if action not in self._allowed_actions and base not in self._allowed_actions:
                raise ServiceError(f"Action not allowed: {action}")

        payload = {"action": action, **kwargs}

        if self._is_ws_mode():
            return self._request_ws(payload)
        return self._request_http(payload)

    def ping(self) -> bool:
        """Check relay connectivity."""
        if self._is_ws_mode():
            from core.relay_manager import RelayConnectionManager
            return RelayConnectionManager.instance().is_connected(
                self._user_id, self._relay_id, relay_type="executor"
            )
        # HTTP mode
        try:
            req = urllib.request.Request(self.url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            if result.get("ok"):
                self._relay_info = result.get("data", {})
                return True
            return False
        except Exception:
            return False

    def get_relay_info(self) -> Dict[str, Any]:
        """Get cached relay info, pinging if needed."""
        if self._relay_info is None:
            if self._is_ws_mode():
                conn = self._get_relay_connection()
                if conn:
                    self._relay_info = conn.info
            else:
                self.ping()
        return self._relay_info or {}

    def _create_connection(self):
        return self

    def _close_connection(self):
        pass

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "relay_id": {
                "type": "string", "required": False, "default": "",
                "description": "Relay ID (auto-set when relay connects via WS)",
            },
            "host": {
                "type": "string", "required": False, "default": "localhost",
                "description": "Relay host (HTTP mode only)",
            },
            "port": {
                "type": "integer", "required": False, "default": 9877,
                "description": "Relay port (HTTP mode only)",
            },
            "secret": {
                "type": "string", "required": True, "sensitive": True,
                "description": "Shared secret for relay authentication",
            },
            "timeout": {
                "type": "integer", "required": False, "default": 30,
                "description": "Request timeout in seconds",
            },
            "approval_mode": {
                "type": "select", "required": False, "default": "ask",
                "options": ["auto", "ask", "strict"],
                "description": "Command approval mode: auto (low=auto), ask (medium+=ask), strict (all ask)",
            },
            "allowed_actions": {
                "type": "string", "required": False, "default": "",
                "description": "Comma-separated allowed actions (empty = all)",
            },
        }


ServiceFactory.register(RemoteExecutorService)

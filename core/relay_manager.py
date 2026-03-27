"""Relay Connection Manager — manages active WS connections from relays.

Relays (executor, filesystem) run on the user's machine and maintain a
persistent WebSocket connection to the PawFlow server. This manager tracks
those connections and routes commands to them.

Thread-safe singleton.
"""

import asyncio
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class RelayConnection:
    """Wraps a WebSocket connection from a relay."""

    user_id: str
    relay_id: str
    relay_type: str  # "executor" | "filesystem"
    websocket: Any  # FastAPI WebSocket
    info: Dict[str, Any] = field(default_factory=dict)
    connected_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)

    # Pending requests: {request_id: asyncio.Future}
    _pending: Dict[str, asyncio.Future] = field(default_factory=dict, repr=False)
    _pending_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def to_dict(self) -> dict:
        return {
            "relay_id": self.relay_id,
            "relay_type": self.relay_type,
            "info": self.info,
            "connected_at": self.connected_at,
            "last_activity": self.last_activity,
            "uptime_seconds": int(time.time() - self.connected_at),
        }


class RelayConnectionManager:
    """Singleton. Manages active WS connections from relays."""

    _instance: Optional["RelayConnectionManager"] = None
    _lock = threading.Lock()

    def __init__(self):
        # {user_id: {relay_id: RelayConnection}}
        self._connections: Dict[str, Dict[str, RelayConnection]] = {}
        self._data_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "RelayConnectionManager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls):
        """Reset singleton (for testing)."""
        with cls._lock:
            cls._instance = None

    def register(
        self, user_id: str, relay_id: str, relay_type: str,
        websocket: Any, info: Dict[str, Any],
    ) -> RelayConnection:
        """Register a new relay connection."""
        conn = RelayConnection(
            user_id=user_id,
            relay_id=relay_id,
            relay_type=relay_type,
            websocket=websocket,
            info=info,
        )
        with self._data_lock:
            self._connections.setdefault(user_id, {})[relay_id] = conn

        logger.info("Relay registered: user=%s relay=%s type=%s",
                     user_id, relay_id, relay_type)

        # Auto-install service in UserServiceRegistry
        self._auto_install_service(user_id, relay_id, relay_type, info)

        return conn

    def unregister(self, user_id: str, relay_id: str):
        """Remove a relay connection (disconnect/timeout)."""
        with self._data_lock:
            user_conns = self._connections.get(user_id, {})
            conn = user_conns.pop(relay_id, None)
            if not user_conns:
                self._connections.pop(user_id, None)

        if conn:
            # Cancel any pending requests
            with conn._pending_lock:
                for req_id, fut in conn._pending.items():
                    if not fut.done():
                        fut.cancel()
                conn._pending.clear()

            logger.info("Relay unregistered: user=%s relay=%s", user_id, relay_id)

            # Auto-disable service
            self._auto_disable_service(user_id, relay_id)

            # Notify user via SSE
            self._notify_disconnect(user_id, relay_id, conn.relay_type)

    def get(
        self, user_id: str, relay_id: str = "",
        relay_type: str = "",
    ) -> Optional[RelayConnection]:
        """Get a relay connection. If relay_id empty, return first of matching type."""
        with self._data_lock:
            user_conns = self._connections.get(user_id, {})
            if relay_id:
                return user_conns.get(relay_id)
            if relay_type:
                for conn in user_conns.values():
                    if conn.relay_type == relay_type:
                        return conn
            # Return first available
            return next(iter(user_conns.values()), None) if user_conns else None

    def list_for_user(self, user_id: str) -> List[Dict]:
        """List all active relays for a user."""
        with self._data_lock:
            user_conns = self._connections.get(user_id, {})
            return [conn.to_dict() for conn in user_conns.values()]

    def list_by_type(self, user_id: str, relay_type: str) -> List[RelayConnection]:
        """List all relays of a given type for a user."""
        with self._data_lock:
            user_conns = self._connections.get(user_id, {})
            return [c for c in user_conns.values() if c.relay_type == relay_type]

    async def send_command(
        self, user_id: str, relay_id: str,
        request_id: str, payload: Dict[str, Any],
        timeout: float = 30,
    ) -> Dict[str, Any]:
        """Send a command to a relay and wait for the result."""
        conn = self.get(user_id, relay_id)
        if not conn:
            raise ConnectionError(f"Relay '{relay_id}' not connected for user '{user_id}'")

        loop = asyncio.get_event_loop()
        future = loop.create_future()

        with conn._pending_lock:
            conn._pending[request_id] = future

        try:
            # Send command to relay
            await conn.websocket.send_json({
                "type": "command",
                "request_id": request_id,
                **payload,
            })
            conn.last_activity = time.time()

            # Wait for result
            result = await asyncio.wait_for(future, timeout=timeout)
            return result

        except asyncio.TimeoutError:
            raise TimeoutError(f"Relay '{relay_id}' did not respond within {timeout}s")
        except asyncio.CancelledError:
            raise ConnectionError(f"Relay '{relay_id}' disconnected")
        finally:
            with conn._pending_lock:
                conn._pending.pop(request_id, None)

    def send_command_sync(
        self, user_id: str, relay_id: str,
        request_id: str, payload: Dict[str, Any],
        timeout: float = 30,
    ) -> Dict[str, Any]:
        """Synchronous wrapper for send_command (for use from sync handlers)."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # We're in an async context — run in a thread
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    new_loop = asyncio.new_event_loop()
                    fut = pool.submit(
                        new_loop.run_until_complete,
                        self._send_command_in_loop(
                            user_id, relay_id, request_id, payload, timeout
                        ),
                    )
                    return fut.result(timeout=timeout + 5)
            else:
                return loop.run_until_complete(
                    self.send_command(user_id, relay_id, request_id, payload, timeout)
                )
        except RuntimeError:
            # No event loop — create one
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(
                    self.send_command(user_id, relay_id, request_id, payload, timeout)
                )
            finally:
                loop.close()

    async def _send_command_in_loop(
        self, user_id: str, relay_id: str,
        request_id: str, payload: Dict[str, Any],
        timeout: float,
    ) -> Dict[str, Any]:
        """Helper for running send_command from a new event loop."""
        conn = self.get(user_id, relay_id)
        if not conn:
            raise ConnectionError(f"Relay '{relay_id}' not connected")

        # Use the same future mechanism but with threading.Event for sync
        result_holder = {"data": None, "error": None}
        event = threading.Event()

        req_id = request_id
        with conn._pending_lock:
            # Store a special sync marker
            conn._pending[req_id] = None  # placeholder

        # We can't use async send from a different loop, so we need
        # to use the relay's websocket directly which lives on the main loop
        # Instead, resolve pending when the result arrives
        conn._sync_pending = getattr(conn, '_sync_pending', {})
        conn._sync_pending[req_id] = (event, result_holder)

        try:
            event.wait(timeout=timeout)
            if result_holder["error"]:
                raise ConnectionError(result_holder["error"])
            if result_holder["data"] is None:
                raise TimeoutError(f"Relay did not respond within {timeout}s")
            return result_holder["data"]
        finally:
            conn._sync_pending.pop(req_id, None)
            with conn._pending_lock:
                conn._pending.pop(req_id, None)

    def resolve_pending(self, user_id: str, relay_id: str,
                        request_id: str, data: Dict[str, Any]):
        """Called when a relay sends back a result for a pending command."""
        conn = self.get(user_id, relay_id)
        if not conn:
            logger.warning("resolve_pending: no connection for user=%s relay=%s",
                           user_id, relay_id)
            return

        conn.last_activity = time.time()

        # Check sync pending first
        sync_pending = getattr(conn, '_sync_pending', {})
        sync_entry = sync_pending.get(request_id)
        if sync_entry:
            event, holder = sync_entry
            holder["data"] = data
            event.set()
            return

        # Check async pending
        with conn._pending_lock:
            future = conn._pending.get(request_id)
        if future and not future.done():
            try:
                future.get_loop().call_soon_threadsafe(future.set_result, data)
            except Exception:
                pass
        else:
            logger.warning("resolve_pending: no pending request %s", request_id)

    @staticmethod
    def _relay_description(info: Dict[str, Any]) -> str:
        tag = ""
        if info.get("containerized"):
            img = info.get("docker_image", "")
            tag = f" \U0001f433 [{img}]" if img else " \U0001f433 [container]"
        return f"WS relay: {info.get('platform', 'unknown')} @ {info.get('root', '?')}{tag}"

    def _auto_install_service(self, user_id: str, relay_id: str,
                               relay_type: str, info: Dict[str, Any]):
        """Auto-install/enable the relay as a user service."""
        try:
            from gui.services.user_service_registry import UserServiceRegistry
            registry = UserServiceRegistry.get_instance()
            svc_type = "remoteExecutor" if relay_type == "executor" else "localFilesystem"

            existing = registry.get_definition(user_id, relay_id)
            if existing:
                # Re-enable existing service
                registry.enable(user_id, relay_id)
            else:
                config = {
                    "relay_id": relay_id,
                    "relay_type": relay_type,
                }
                if relay_type == "executor":
                    config["approval_mode"] = info.get("approval_mode", "ask")
                elif relay_type == "filesystem":
                    config["mode"] = info.get("mode", "readwrite")

                registry.install(
                    user_id=user_id,
                    service_id=relay_id,
                    service_type=svc_type,
                    config=config,
                    description=self._relay_description(info),
                    enabled=True,
                )
            logger.info("Auto-installed service '%s' for user '%s'", relay_id, user_id)
        except Exception as e:
            logger.warning("Failed to auto-install service: %s", e)

    def _auto_disable_service(self, user_id: str, relay_id: str):
        """Auto-disable the service when relay disconnects."""
        try:
            from gui.services.user_service_registry import UserServiceRegistry
            registry = UserServiceRegistry.get_instance()
            svc_def = registry.get_definition(user_id, relay_id)
            if svc_def and svc_def.enabled:
                registry.disable(user_id, relay_id)
                logger.info("Auto-disabled service '%s' for user '%s'", relay_id, user_id)
        except Exception as e:
            logger.warning("Failed to auto-disable service: %s", e)

    def _notify_disconnect(self, user_id: str, relay_id: str, relay_type: str):
        """Notify user via SSE that relay disconnected."""
        try:
            from core.conversation_event_bus import ConversationEventBus
            from core.sse_writer import SSEEvent
            bus = ConversationEventBus.instance()
            for conv_id in bus.active_conversations():
                bus.publish_event(conv_id, "notification", {
                    "message": f"Relay '{relay_id}' ({relay_type}) disconnected",
                    "urgency": "high",
                    "relay_id": relay_id,
                    "relay_type": relay_type,
                })
        except Exception as e:
            logger.debug("Failed to notify disconnect: %s", e)

    def is_connected(self, user_id: str, relay_id: str = "",
                     relay_type: str = "") -> bool:
        """Check if a relay is connected."""
        return self.get(user_id, relay_id, relay_type) is not None

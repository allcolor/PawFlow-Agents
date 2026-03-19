"""WebSocket router — real-time streaming for logs, metrics, queue stats, relay connections."""

import asyncio
import json
import logging
import time
import uuid
from typing import Dict, Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from core.bulletin import BulletinBoard

logger = logging.getLogger(__name__)

router = APIRouter()

# Active connections per channel
_connections: Dict[str, Set[WebSocket]] = {
    "bulletins": set(),
    "execution": set(),
    "queues": set(),
}


async def _send_json(ws: WebSocket, data: dict):
    """Send JSON to a websocket, ignoring errors."""
    try:
        await ws.send_json(data)
    except Exception:
        pass


# -- Bulletins stream (logs, warnings, errors) --

@router.websocket("/bulletins")
async def ws_bulletins(websocket: WebSocket):
    """Stream bulletin board events in real-time.

    Messages sent:
        {"type": "bulletin", "level": "...", "source": "...", "message": "...", "timestamp": "..."}
        {"type": "ping"}
    """
    await websocket.accept()
    _connections["bulletins"].add(websocket)
    logger.info("WebSocket client connected to /bulletins")

    try:
        board = BulletinBoard.get_instance()
        last_count = board.get_counts().get("total", 0)

        while True:
            # Check for new bulletins
            current_count = board.get_counts().get("total", 0)
            if current_count > last_count:
                # Get new bulletins
                all_bulletins = board.get_bulletins(limit=current_count - last_count)
                for b in all_bulletins:
                    await _send_json(websocket, {
                        "type": "bulletin",
                        "level": b.get("level", "INFO"),
                        "source": b.get("source_id", ""),
                        "message": b.get("message", ""),
                        "timestamp": b.get("timestamp", ""),
                    })
                last_count = current_count
            else:
                await _send_json(websocket, {"type": "ping"})

            # Check for incoming messages (for keep-alive)
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=2.0)
            except asyncio.TimeoutError:
                pass

    except WebSocketDisconnect:
        pass
    finally:
        _connections["bulletins"].discard(websocket)
        logger.info("WebSocket client disconnected from /bulletins")


# -- Execution stream (continuous executor status) --

@router.websocket("/execution/{flow_id}")
async def ws_execution(websocket: WebSocket, flow_id: str):
    """Stream continuous executor status for a given flow.

    Messages sent:
        {"type": "status", "flow_id": "...", "tasks": {...}, "queues": {...}, "uptime": ...}
        {"type": "ping"}
    """
    await websocket.accept()
    _connections["execution"].add(websocket)
    logger.info(f"WebSocket client connected to /execution/{flow_id}")

    try:
        # Import here to avoid circular imports
        from api.routers.execution_router import _continuous_executors

        while True:
            executor = _continuous_executors.get(flow_id)
            if executor:
                try:
                    status = executor.get_status()
                    await _send_json(websocket, {
                        "type": "status",
                        "flow_id": flow_id,
                        "tasks": status.get("tasks", {}),
                        "queues": status.get("queues", {}),
                        "uptime": status.get("uptime_seconds", 0),
                    })
                except Exception as e:
                    await _send_json(websocket, {
                        "type": "error",
                        "message": str(e),
                    })
            else:
                await _send_json(websocket, {
                    "type": "not_found",
                    "flow_id": flow_id,
                    "message": "No active executor for this flow",
                })

            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=2.0)
            except asyncio.TimeoutError:
                pass

    except WebSocketDisconnect:
        pass
    finally:
        _connections["execution"].discard(websocket)
        logger.info(f"WebSocket client disconnected from /execution/{flow_id}")


# -- System metrics stream --

@router.websocket("/metrics")
async def ws_metrics(websocket: WebSocket):
    """Stream system metrics (memory, spill stats, active executions).

    Messages sent:
        {"type": "metrics", "spill": {...}, "active_executors": ..., "timestamp": ...}
        {"type": "ping"}
    """
    await websocket.accept()
    logger.info("WebSocket client connected to /metrics")

    try:
        while True:
            metrics = _collect_metrics()
            await _send_json(websocket, {
                "type": "metrics",
                **metrics,
                "timestamp": time.time(),
            })

            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=3.0)
            except asyncio.TimeoutError:
                pass

    except WebSocketDisconnect:
        pass
    finally:
        logger.info("WebSocket client disconnected from /metrics")


def _collect_metrics() -> dict:
    """Collect system-wide metrics."""
    result = {}

    # SpillTracker stats
    try:
        from core.stream import get_spill_tracker
        tracker = get_spill_tracker()
        result["spill"] = tracker.get_stats()
    except Exception:
        result["spill"] = {}

    # Active continuous executors
    try:
        from api.routers.execution_router import _continuous_executors
        result["active_executors"] = len(_continuous_executors)
        result["executor_flows"] = list(_continuous_executors.keys())
    except Exception:
        result["active_executors"] = 0
        result["executor_flows"] = []

    # Bulletin counts
    try:
        board = BulletinBoard.get_instance()
        result["bulletin_counts"] = board.get_counts()
    except Exception:
        result["bulletin_counts"] = {}

    return result


# -- Relay WebSocket endpoint (WS Reverse) --

@router.websocket("/relay")
async def ws_relay(websocket: WebSocket):
    """WebSocket endpoint for relay connections (executor/filesystem).

    Protocol:
    1. Relay connects and sends a registration message:
       {"type": "register", "token": "<api_key>", "secret": "...",
        "relay_type": "executor|filesystem", "relay_id": "...", "info": {...}}
    2. Server validates token → identifies user_id → registers relay
    3. Server pushes commands:
       {"type": "command", "request_id": "...", "action": "...", ...}
    4. Relay sends results:
       {"type": "result", "request_id": "...", "data": {...}}
    5. Keepalive: relay sends {"type": "ping"}, server responds {"type": "pong"}
    """
    from core.relay_manager import RelayConnectionManager

    await websocket.accept()

    user_id = ""
    relay_id = ""

    try:
        # First message must be registration
        reg = await asyncio.wait_for(websocket.receive_json(), timeout=30)

        if reg.get("type") != "register":
            await websocket.close(code=4000, reason="First message must be register")
            return

        # Validate token against service config
        token = reg.get("token", "")
        relay_id = reg.get("relay_id", "")
        relay_type = reg.get("relay_type", "filesystem")

        if not token or not relay_id:
            await websocket.close(code=4001, reason="Missing token or relay_id")
            return

        # Match token against the filesystem service config
        user_id = ""
        try:
            from gui.services.global_service_registry import GlobalServiceRegistry
            greg = GlobalServiceRegistry.get_instance()
            sdef = greg.get_all_definitions().get(relay_id)
            if sdef and getattr(sdef, "service_type", "") == "filesystem":
                svc_token = (sdef.config or {}).get("token", "")
                if svc_token and svc_token == token:
                    user_id = relay_id  # use relay_id as user context
                else:
                    await websocket.close(code=4001, reason="Token mismatch")
                    return
        except Exception:
            pass
        # Fallback: try SecurityManager API key validation
        if not user_id:
            try:
                from core.security import SecurityManager
                sm = SecurityManager.get_instance()
                user = sm.validate_api_key(token)
                if user:
                    user_id = user.get("username", user.get("user_id", ""))
            except Exception:
                user_id = token  # dev mode fallback

        if not user_id:
            await websocket.close(code=4001, reason="Invalid token")
            return
        info = reg.get("info", {})
        secret = reg.get("secret", "")

        # Store secret in info for command forwarding
        info["_secret"] = secret

        mgr = RelayConnectionManager.instance()
        conn = mgr.register(user_id, relay_id, relay_type, websocket, info)

        # Confirm registration
        await websocket.send_json({
            "type": "registered",
            "relay_id": relay_id,
            "user_id": user_id,
        })

        logger.info("Relay WS connected: user=%s relay=%s type=%s",
                     user_id, relay_id, relay_type)

        # Main loop: receive results and pings from relay
        while True:
            msg = await websocket.receive_json()
            msg_type = msg.get("type", "")

            if msg_type == "result":
                request_id = msg.get("request_id", "")
                data = msg.get("data", {})
                mgr.resolve_pending(user_id, relay_id, request_id, data)

            elif msg_type == "ping":
                await websocket.send_json({"type": "pong"})
                conn.last_activity = time.time()

            elif msg_type == "error":
                request_id = msg.get("request_id", "")
                error_msg = msg.get("error", "Unknown relay error")
                mgr.resolve_pending(user_id, relay_id, request_id,
                                    {"error": error_msg, "ok": False})

    except WebSocketDisconnect:
        logger.info("Relay WS disconnected: user=%s relay=%s", user_id, relay_id)
    except asyncio.TimeoutError:
        logger.warning("Relay WS registration timeout")
        try:
            await websocket.close(code=4002, reason="Registration timeout")
        except Exception:
            pass
    except Exception as e:
        logger.error("Relay WS error: %s", e)
    finally:
        if user_id and relay_id:
            RelayConnectionManager.instance().unregister(user_id, relay_id)

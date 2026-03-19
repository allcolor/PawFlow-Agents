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


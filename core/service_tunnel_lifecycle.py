"""Background grant refresh and relay reconnect handling for Service Tunnels."""

from __future__ import annotations

import logging
import threading

from core import service_tunnel_control


logger = logging.getLogger(__name__)

_REFRESH_SECONDS = 45 * 60
_refresh_lock = threading.Lock()
_state_lock = threading.Lock()
_stop_event = threading.Event()
_thread = None


def refresh_once(relay_id: str | None = None) -> None:
    """Re-issue grants and re-apply eligible persistent tunnel roles."""
    with _refresh_lock:
        service_tunnel_control.reconcile_for_relay(relay_id)


def _refresh_loop() -> None:
    while not _stop_event.is_set():
        try:
            refresh_once()
        except Exception:
            logger.debug("Service Tunnel periodic refresh failed", exc_info=True)
        if _stop_event.wait(_REFRESH_SECONDS):
            break


def start() -> bool:
    """Start the single daemon refresher. Return False when already running."""
    global _thread
    with _state_lock:
        if _thread is not None and _thread.is_alive():
            return False
        _stop_event.clear()
        _thread = threading.Thread(
            target=_refresh_loop, name="service-tunnel-refresh", daemon=True)
        _thread.start()
        return True


def stop() -> None:
    """Stop the refresher, primarily for orderly shutdown and tests."""
    global _thread
    with _state_lock:
        thread = _thread
        _thread = None
        _stop_event.set()
    if thread is not None:
        thread.join(timeout=5)


def _reconcile_connected(relay_id: str) -> None:
    try:
        refresh_once(relay_id)
    except Exception:
        logger.debug(
            "Service Tunnel reconnect reconciliation failed for relay %s",
            relay_id, exc_info=True)


def on_relay_connected(relay_id: str) -> None:
    """Reconcile a participating relay without blocking its WebSocket loop."""
    try:
        threading.Thread(
            target=_reconcile_connected, args=(relay_id,),
            name=f"service-tunnel-reconcile-{relay_id[:12]}",
            daemon=True,
        ).start()
    except Exception:
        logger.debug(
            "Unable to start Service Tunnel reconciliation for relay %s",
            relay_id, exc_info=True)

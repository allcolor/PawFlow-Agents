"""Client side of the relay→server FS protocol.

Lives in the relay daemon. Sends `relay_request` envelopes over the
shared WebSocket to the PawFlow server's `RelayServerFs` handler and
awaits the matching `relay_response`. The dispatch back from the WS
receive loop happens via `dispatch_response()`.

The WebSocket connection itself is owned by the worker module — this
client just borrows the (sock, send_lock) pair so it shares the same
frame-level serialization. On reconnect, the worker constructs a new
client instance pointing at the new socket; pending requests on the
old instance are unblocked with an error via `cancel_all()`.

Protocol: see docs/relay_server_fs.md.
"""

import json
import logging
import threading
import uuid
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class ServerFsClient:
    """One instance per WebSocket connection.

    Thread-safe: many FUSE callback threads issue concurrent
    `request()` calls; the receive loop calls `dispatch_response()`
    from a single thread.
    """

    DEFAULT_TIMEOUT = 30.0

    def __init__(self, send_callable, send_lock: Optional[threading.Lock] = None):
        """Construct.

        Args:
            send_callable: function taking a single `bytes` payload and
                writing it as a single WS text frame. Typically
                `lambda b: ws_frame_send(sock, b)`. Wrapping lets us
                test without a real socket.
            send_lock: optional lock to serialize sends with other
                writers on the same socket (worker uses one for SSL
                safety). If None, sends are not externally serialized.
        """
        self._send = send_callable
        self._send_lock = send_lock
        self._pending: Dict[str, Tuple[threading.Event, Dict[str, Any]]] = {}
        self._pending_lock = threading.Lock()
        self._closed = False

    # ------------------------------------------------------------------
    # Outbound: FUSE callback → server
    # ------------------------------------------------------------------

    def request(self, method: str, args: Dict[str, Any],
                timeout: Optional[float] = None) -> Dict[str, Any]:
        """Send a request, block until the response arrives.

        Returns the parsed reply (the same dict shape returned by
        `RelayServerFs.handle`: `{'data': ...}` on success or
        `{'error': 'EXXX', 'errno': N, ...}` on failure).

        On timeout or connection close, returns `{'error': 'EIO',
        'errno': 5, 'message': '...'}` so callers don't need to
        distinguish transport failures from sandbox refusals — both
        translate to a FUSE errno on the consumer side.
        """
        if self._closed:
            return {'error': 'EIO', 'errno': 5, 'message': 'client closed'}
        request_id = uuid.uuid4().hex[:12]
        evt = threading.Event()
        holder: Dict[str, Any] = {}
        with self._pending_lock:
            self._pending[request_id] = (evt, holder)
        envelope = {
            'type': 'relay_request',
            'request_id': request_id,
            'method': method,
            'args': args or {},
        }
        payload = json.dumps(envelope).encode('utf-8')
        try:
            if self._send_lock is not None:
                with self._send_lock:
                    self._send(payload)
            else:
                self._send(payload)
        except Exception as e:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            return {'error': 'EIO', 'errno': 5,
                    'message': f'send failed: {e}'}
        if not evt.wait(timeout if timeout is not None else self.DEFAULT_TIMEOUT):
            with self._pending_lock:
                self._pending.pop(request_id, None)
            return {'error': 'EIO', 'errno': 5,
                    'message': f'timeout after {timeout}s waiting for {method}'}
        return dict(holder)

    # ------------------------------------------------------------------
    # Inbound: WS receive loop → wake the waiter
    # ------------------------------------------------------------------

    def dispatch_response(self, msg: Dict[str, Any]) -> bool:
        """Resolve the pending request matching `msg['request_id']`.

        Returns True if a waiter was found and woken; False if the
        request_id is unknown (caller may log a warning).
        """
        request_id = msg.get('request_id', '')
        with self._pending_lock:
            entry = self._pending.pop(request_id, None)
        if entry is None:
            return False
        evt, holder = entry
        # Copy 'data' or 'error'/'errno'/'message' — don't include the
        # envelope's 'type'/'request_id' since they're already known.
        for k in ('data', 'error', 'errno', 'message'):
            if k in msg:
                holder[k] = msg[k]
        evt.set()
        return True

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def cancel_all(self, error: str = 'connection lost') -> int:
        """Wake every pending waiter with an EIO error. Returns the count.

        Call from the worker on WS disconnect / reconnect.
        """
        with self._pending_lock:
            entries = list(self._pending.values())
            self._pending.clear()
            self._closed = True
        for evt, holder in entries:
            holder['error'] = 'EIO'
            holder['errno'] = 5
            holder['message'] = error
            evt.set()
        return len(entries)

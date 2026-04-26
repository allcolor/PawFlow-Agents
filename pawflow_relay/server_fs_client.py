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

        Total wall-clock cap is `timeout` (default 30 s, FUSE side
        passes 5 s). Includes acquiring the shared `_send_lock` —
        previously a long-held lock from other WS traffic could park
        the caller before `evt.wait` ever ran, so the timeout never
        fired and FUSE callbacks froze forever.
        """
        if self._closed:
            return {'error': 'EIO', 'errno': 5, 'message': 'client closed'}
        deadline_total = timeout if timeout is not None else self.DEFAULT_TIMEOUT
        deadline_at = None
        import time as _t
        if deadline_total is not None:
            deadline_at = _t.monotonic() + deadline_total
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
                # Bounded wait on the shared send lock — never an
                # un-timed `with` block. If contention exceeds our
                # remaining budget we bail out as EIO instead of
                # parking the FUSE callback (and through it, the
                # user's shell) indefinitely.
                _budget = (deadline_at - _t.monotonic()
                           if deadline_at is not None else None)
                _budget = max(0.001, _budget) if _budget is not None else None
                if not self._send_lock.acquire(timeout=_budget if _budget is not None else -1):
                    with self._pending_lock:
                        self._pending.pop(request_id, None)
                    return {'error': 'EIO', 'errno': 5,
                            'message': f'send_lock contended >{_budget}s for {method}'}
                try:
                    self._send(payload)
                finally:
                    self._send_lock.release()
            else:
                self._send(payload)
        except Exception as e:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            return {'error': 'EIO', 'errno': 5,
                    'message': f'send failed: {e}'}
        # What's left of the budget is the time to wait for the reply.
        _remaining = (deadline_at - _t.monotonic()
                      if deadline_at is not None else None)
        if _remaining is not None and _remaining <= 0:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            return {'error': 'EIO', 'errno': 5,
                    'message': f'budget exhausted before reply for {method}'}
        if not evt.wait(_remaining):
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


class SwappableServerFsClient:
    """Stable client handle whose underlying ServerFsClient can be swapped.

    The FUSE proxy (ServerFsMount / ServerFsOperations) holds this object
    by reference for the entire mount lifetime. The relay worker creates
    a new ServerFsClient on each WS connect and sets it via set_inner();
    on disconnect it clears it via clear_inner(). Requests issued while
    no inner is set return EIO immediately, mimicking the WS-disconnected
    state without unmounting the FUSE.

    Without this indirection, the FUSE mount would have to be torn down
    and recreated on every WS reconnect, which invalidates kernel-side
    inodes and breaks bind-mounts in downstream containers (e.g. CC).
    """

    def __init__(self):
        self._inner: Optional[ServerFsClient] = None
        self._lock = threading.Lock()

    def set_inner(self, client: ServerFsClient) -> None:
        with self._lock:
            self._inner = client

    def clear_inner(self) -> None:
        with self._lock:
            self._inner = None

    def get_inner(self) -> Optional[ServerFsClient]:
        with self._lock:
            return self._inner

    def request(self, method: str, args: Dict[str, Any],
                timeout: Optional[float] = None) -> Dict[str, Any]:
        with self._lock:
            cli = self._inner
        if cli is None:
            return {'error': 'EIO', 'errno': 5,
                    'message': 'WS not connected (relay reconnecting)'}
        return cli.request(method, args, timeout)

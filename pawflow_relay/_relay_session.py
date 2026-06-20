"""Per-connection helpers for the relay worker's reconnect loop.

Small, self-contained pieces lifted out of _ws_connect:
  - close_frame_info: format a WS close-frame payload for logging (pure).
  - attach_fuse_clients / detach_fuse_clients: bind a fresh per-WS
    ServerFsClient into each SwappableServerFsClient handle for this socket,
    and detach + cancel them on disconnect. The FUSE *mount* is created once
    before the reconnect loop and stays live across reconnects; only the
    client bound to the live socket is swapped per connection.

These were three near-identical inline blocks (cc_sessions / filestore /
skills) plus two teardown loops; centralising them removes the duplication
and makes the swap/cancel contract unit-testable.
"""
import logging
import struct

_log = logging.getLogger(__name__)


def close_frame_info(payload):
    """Render a WS close-frame payload (code + reason) for a log line."""
    if not payload:
        return "code=none reason=''"
    try:
        if len(payload) >= 2:
            code = struct.unpack("!H", payload[:2])[0]
            reason = payload[2:].decode("utf-8", errors="replace")
            return f"code={code} reason={reason!r}"
        return f"code=none reason={payload.decode('utf-8', errors='replace')!r}"
    except Exception:
        return f"malformed={payload[:80]!r}"


def attach_fuse_clients(sock, send_lock, swaps):
    """Bind a fresh ServerFsClient (on this sock) into each swap handle.

    swaps is an ordered tuple of SwappableServerFsClient | None. Returns a
    tuple of the same length holding the new ServerFsClient (or None where the
    swap was None), positionally matching the input.
    """
    from pawflow_relay.server_fs_client import ServerFsClient
    from pawflow_relay.ws_frame import ws_send as _ws_frame_send
    clients = []
    for swap in swaps:
        if swap is None:
            clients.append(None)
            continue
        client = ServerFsClient(
            send_callable=lambda b: _ws_frame_send(sock, b),
            send_lock=send_lock)
        swap.set_inner(client)
        clients.append(client)
    return tuple(clients)


def detach_fuse_clients(swaps, clients, reason="relay disconnected"):
    """Clear each swap handle and cancel its client's pending requests.

    Cancelling with EIO unblocks the kernel so it doesn't hang on the dead
    socket. The FUSE mount itself stays up across reconnects.
    """
    for swap in swaps:
        if swap is not None:
            try:
                swap.clear_inner()
            except Exception:
                _log.debug("Ignored exception", exc_info=True)
    for client in (clients or ()):
        if client is not None:
            try:
                client.cancel_all(reason)
            except Exception:
                _log.debug("Ignored exception", exc_info=True)

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
import os
import socket
import struct
import sys
from dataclasses import dataclass

_log = logging.getLogger(__name__)


@dataclass
class ConnectionParams:
    """Parsed WS endpoint + the registration info payload for a connection."""
    host: str
    port: int
    path: str
    use_ssl: bool
    info: dict


def _is_containerized():
    return os.path.exists("/.dockerenv") or bool(os.environ.get("PAWFLOW_DOCKER_IMAGE"))


def build_connection_params(url, root_dir, readonly, allow_exec,
                            allow_automation, allow_local_screen, allow_local):
    """Parse the WS URL and build the relay registration ``info`` payload.

    Pure given the environment: URL scheme/host/port/path, available shells,
    containerization detection, and the host_root (the user's pre-Docker-mount
    path, slash-normalised for JSON display).
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)
    use_ssl = parsed.scheme in ("wss", "https")
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if use_ssl else 80)
    path = parsed.path or "/ws/relay"

    mode = "read" if readonly else "readwrite"
    try:
        from fs_actions import detect_available_shells
        _shells = detect_available_shells()
    except Exception:
        _shells = {}

    # host_root: the original path on the user's machine (before Docker mount).
    # Always forward slashes (Windows backslashes break JSON display).
    _host_root = os.environ.get("PAWFLOW_HOST_WORKDIR", "")
    if not _host_root and not _is_containerized():
        _host_root = root_dir
    _host_root = _host_root.replace("\\", "/")

    info = {
        "platform": sys.platform,
        "root": root_dir,
        "host_root": _host_root,
        "mode": mode,
        "shells": list(_shells.keys()),
        "containerized": _is_containerized(),
        "docker_image": os.environ.get("PAWFLOW_DOCKER_IMAGE", ""),
        "container_id": socket.gethostname() if _is_containerized() else "",
        "allow_exec": allow_exec,
        "allow_automation": allow_automation,
        "allow_local_screen": allow_local_screen,
        "allow_local": allow_local,
    }
    return ConnectionParams(host=host, port=port, path=path,
                            use_ssl=use_ssl, info=info)


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

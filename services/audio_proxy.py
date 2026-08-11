"""Audio WebSocket Proxy — relay Opus packets between browser and Docker/relay.

Backend protocol (TCP): 2-byte big-endian length + Opus payload.
Browser protocol (WebSocket): binary frames containing raw Opus packets.
"""

import base64
import logging
import select
import socket
import struct
import collections
import threading
import time
import uuid
from typing import Dict, Tuple, Optional

logger = logging.getLogger(__name__)

_audio_sources: Dict[str, dict] = {}
_audio_tokens: Dict[str, str] = {}
_audio_lock = threading.Lock()
# Active proxy sessions: session_id -> [(stop_event, backend_socket), ...]
_active_proxies: Dict[str, list] = {}
_active_relay_proxies: Dict[str, list] = {}


def register_audio_source(session_id: str, host: str, port: int,
                          *, owner_user_id: str = "",
                          conversation_id: str = "",
                          login_session_id: str = "",
                          ttl_seconds: int = 86400,
                          relay_service=None,
                          relay_id: str = "",
                          local_screen: bool = False) -> str:
    """Register an audio source and mint its capability token.

    Returns the token (URL-safe). Caller embeds it in the WS URL
    (`/audio/<session>/<token>/stream`); without it the WS handler
    rejects every connection 401/403.

    `owner_user_id` is required for non-test callers.
    """
    if not port:
        return ""
    if not owner_user_id:
        raise ValueError("register_audio_source: owner_user_id is required")
    from core.capability_routes import mint_route_token
    token = mint_route_token(
        "audio", session_id, owner_user_id,
        conversation_id=conversation_id,
        session_id=login_session_id,
        ttl_seconds=ttl_seconds)
    # Kill any stale proxies from a previous session with the same ID
    with _audio_lock:
        old_proxies = _active_proxies.pop(session_id, [])
        old_relay_proxies = _active_relay_proxies.pop(session_id, [])
        _audio_sources[session_id] = {
            "host": host,
            "port": port,
            "relay_service": relay_service,
            "relay_id": relay_id,
            "local_screen": bool(local_screen),
        }
        _audio_tokens[session_id] = token
    _kill_proxies(old_proxies)
    _kill_relay_proxies(old_relay_proxies)
    if relay_service is not None:
        logger.info("Audio proxy: registered %s through relay %s port %d",
                    session_id, relay_id, port)
    else:
        logger.info("Audio proxy: registered %s -> %s:%d", session_id, host, port)
    return token


def get_audio_token(session_id: str) -> str:
    """Return the capability token for an audio session, or empty
    string if the session is unknown."""
    with _audio_lock:
        return _audio_tokens.get(session_id, "") or ""


def unregister_audio_source(session_id: str):
    with _audio_lock:
        _audio_sources.pop(session_id, None)
        _audio_tokens.pop(session_id, None)
        proxies = _active_proxies.pop(session_id, [])
        relay_proxies = _active_relay_proxies.pop(session_id, [])
    _kill_proxies(proxies)
    _kill_relay_proxies(relay_proxies)
    try:
        from core.capability_routes import revoke_route_tokens
        revoke_route_tokens(session_id)
    except Exception:
        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)


def kill_audio_proxies(session_id: str):
    """Kill all active audio proxy threads for a session."""
    with _audio_lock:
        proxies = _active_proxies.pop(session_id, [])
        relay_proxies = _active_relay_proxies.pop(session_id, [])
    _kill_proxies(proxies)
    _kill_relay_proxies(relay_proxies)


def _kill_proxies(proxies):
    for stop_ev, backend_sock in proxies:
        stop_ev.set()
        try:
            backend_sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        try:
            backend_sock.close()
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
    if proxies:
        logger.info("Audio proxy: killed %d active proxy(ies)", len(proxies))


def _kill_relay_proxies(proxies):
    if not proxies:
        return
    from services.vnc_proxy import _send_command_to_relay
    for proxy in proxies:
        proxy["stop"].set()
        _send_command_to_relay(proxy["relay_service"], {
            "action": "desktop_audio_close",
            "session_id": proxy["relay_session_id"],
        })
        try:
            proxy["browser_sock"].close()
        except Exception:
            logger.debug("Ignored exception", exc_info=True)
    logger.info("Audio proxy: killed %d active relay proxy(ies)", len(proxies))


def _get_audio_target(session_id: str) -> Tuple[str, int]:
    with _audio_lock:
        source = _audio_sources.get(session_id)
    if not source:
        return ("", 0)
    return (source.get("host", ""), source.get("port", 0))


def _get_audio_source(session_id: str) -> Optional[dict]:
    with _audio_lock:
        return _audio_sources.get(session_id)


def audio_ws_proxy(client_sock, path_params: dict, meta: dict):
    """WebSocket handler for /audio/{session_id}/{token}/stream. The
    token binds the requester (auth_user) to this audio session;
    cross-user access is rejected before any backend connect."""
    session_id = path_params.get("session_id", "")
    token = path_params.get("token", "")
    if not session_id:
        _ws_close(client_sock, 4000, "Missing session_id")
        return

    from core.capability_routes import verify_route_ws
    claims, err = verify_route_ws(
        meta or {}, "audio", session_id, token, allow_bearer_only=True)
    if err is not None:
        try:
            client_sock.sendall(err)
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        try:
            client_sock.close()
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        return

    source = _get_audio_source(session_id)
    if source and source.get("relay_service") is not None:
        _audio_ws_relay_proxy(client_sock, session_id, source)
        return

    target_host, target_port = _get_audio_target(session_id)
    if not target_port:
        _ws_close(client_sock, 4001, "No audio source")
        return
    _audio_ws_direct_proxy(client_sock, session_id, target_host, target_port)


def _audio_ws_relay_proxy(client_sock, session_id: str, source: dict):
    """Stream desktop audio packets through an outbound relay connection."""
    relay_service = source["relay_service"]
    relay_id = source.get("relay_id", "")
    relay_session_id = uuid.uuid4().hex[:12]
    stop = threading.Event()
    proxy = {
        "relay_service": relay_service,
        "relay_id": relay_id,
        "relay_session_id": relay_session_id,
        "browser_sock": client_sock,
        "stop": stop,
    }
    with _audio_lock:
        if _audio_sources.get(session_id) is not source:
            _ws_close(client_sock, 4001, "No audio source")
            return
        _active_relay_proxies.setdefault(session_id, []).append(proxy)

    try:
        result = relay_service._request(
            "desktop_audio_open",
            session_id=relay_session_id,
            port=int(source.get("port") or 0),
            local_screen=bool(source.get("local_screen")),
        )
        if not isinstance(result, dict) or not result.get("ok"):
            detail = (result.get("error", "Unknown")
                      if isinstance(result, dict) else str(result))
            _ws_close(client_sock, 4002, f"Failed: {detail}")
            return

        logger.info("Audio relay tunnel connected: relay=%s session=%s port=%d",
                    relay_id, relay_session_id, source.get("port", 0))
        client_sock.settimeout(1.0)
        while not stop.is_set():
            try:
                data = client_sock.recv(4096)
                if not data:
                    break
            except socket.timeout:
                continue
            except Exception:
                break
    except Exception as exc:
        logger.warning("Audio relay tunnel open failed for %s: %s", relay_id, exc)
        _ws_close(client_sock, 4002, f"Failed: {exc}")
    finally:
        from services.vnc_proxy import _send_command_to_relay
        _send_command_to_relay(relay_service, {
            "action": "desktop_audio_close",
            "session_id": relay_session_id,
        })
        with _audio_lock:
            entries = _active_relay_proxies.get(session_id, [])
            if proxy in entries:
                entries.remove(proxy)
            if not entries:
                _active_relay_proxies.pop(session_id, None)
        try:
            client_sock.close()
        except Exception:
            logger.debug("Ignored exception", exc_info=True)
        logger.info("Audio relay tunnel disconnected: relay=%s session=%s",
                    relay_id, relay_session_id)


def dispatch_audio_data(relay_id: str, relay_session_id: str,
                        data_b64: str) -> None:
    """Forward one relay-side Opus packet to its browser WebSocket."""
    with _audio_lock:
        proxy = next((item for proxies in _active_relay_proxies.values()
                      for item in proxies
                      if item["relay_session_id"] == relay_session_id
                      and item["relay_id"] == relay_id),
                     None)
    if not proxy:
        logger.debug("desktop_audio_data: no browser for relay=%s session=%s",
                     relay_id, relay_session_id)
        return
    try:
        from services.vnc_proxy import _ws_build_frame
        proxy["browser_sock"].sendall(
            _ws_build_frame(base64.b64decode(data_b64), opcode=0x02))
    except Exception as exc:
        logger.warning("desktop_audio_data send failed: %s", exc)
        proxy["stop"].set()


def dispatch_audio_close(relay_id: str, relay_session_id: str) -> None:
    """Close the browser audio socket when its relay-side stream ends."""
    proxy = None
    with _audio_lock:
        for session_id, proxies in list(_active_relay_proxies.items()):
            source = _audio_sources.get(session_id, {})
            if source.get("relay_id") != relay_id:
                continue
            proxy = next((item for item in proxies
                          if item["relay_session_id"] == relay_session_id), None)
            if proxy:
                proxies.remove(proxy)
                if not proxies:
                    _active_relay_proxies.pop(session_id, None)
                break
    if proxy:
        proxy["stop"].set()
        _ws_close(proxy["browser_sock"], 1000, "Audio backend closed")


def _audio_ws_direct_proxy(client_sock, session_id: str,
                           target_host: str, target_port: int) -> None:
    """Preserve the direct TCP path used by server-managed desktops."""
    try:
        backend_sock = socket.create_connection((target_host, target_port), timeout=5)
        backend_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        backend_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)
        # Keepalive so a silently-dead backend (pulseaudio crash, network
        # blip, container hiccup) surfaces in seconds, not minutes. The
        # browser's audioRestart button is a fast manual fix; keepalive
        # is the automatic path.
        backend_sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        if hasattr(socket, "TCP_KEEPIDLE"):
            backend_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 10)
        if hasattr(socket, "TCP_KEEPINTVL"):
            backend_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 3)
        if hasattr(socket, "TCP_KEEPCNT"):
            backend_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
        # Timeout so recv() unblocks periodically to check stop flag
        backend_sock.settimeout(1.0)
    except Exception as e:
        logger.warning("Audio proxy: connect failed %s:%d: %s", target_host, target_port, e)
        _ws_close(client_sock, 4002, "Audio source unavailable")
        return

    try:
        client_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        # Keepalive on browser side too, so a silently-dead tab (closed
        # without sending a WS close frame) is detected quickly.
        client_sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        if hasattr(socket, "TCP_KEEPIDLE"):
            client_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 10)
        if hasattr(socket, "TCP_KEEPINTVL"):
            client_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 3)
        if hasattr(socket, "TCP_KEEPCNT"):
            client_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
    except Exception:
        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

    stop = threading.Event()

    # Register this proxy so it can be killed when the audio source is unregistered
    with _audio_lock:
        _active_proxies.setdefault(session_id, []).append((stop, backend_sock))

    pkt_queue = collections.deque(maxlen=250)  # ~5s of 20ms packets
    queue_event = threading.Event()

    def _backend_reader():
        """Read TCP packets into queue without blocking on WS writes."""
        _pkt_count = 0
        _interval_start = time.monotonic()
        try:
            while not stop.is_set():
                hdr = _recv_or_stop(backend_sock, 2, stop)
                if not hdr:
                    break
                pkt_len = struct.unpack("!H", hdr)[0]
                if pkt_len == 0:
                    continue
                pkt = _recv_or_stop(backend_sock, pkt_len, stop)
                if not pkt:
                    break
                pkt_queue.append(pkt)  # deque maxlen auto-drops oldest
                queue_event.set()
                _pkt_count += 1
                _now = time.monotonic()
                if _now - _interval_start >= 5.0:
                    logger.info("Audio proxy reader: %d pkts/5s (queue=%d)", _pkt_count, len(pkt_queue))
                    _pkt_count = 0
                    _interval_start = _now
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        finally:
            stop.set()
            queue_event.set()

    def _backend_to_browser():
        """Send queued packets to browser WS, batched for fewer syscalls."""
        try:
            while not stop.is_set():
                queue_event.wait(timeout=0.005)  # low-latency: forward ASAP
                queue_event.clear()
                # Drain all available packets into one batch
                batch = []
                while pkt_queue:
                    batch.append(pkt_queue.popleft())
                if not batch:
                    continue
                # Send each as individual WS frame (browser expects 1 opus pkt per msg)
                # but use writev-style to reduce syscall overhead
                frames = bytearray()
                for pkt in batch:
                    if len(pkt) < 126:
                        frames.append(0x82)
                        frames.append(len(pkt))
                    else:
                        frames.append(0x82)
                        frames.append(126)
                        frames.extend(struct.pack("!H", len(pkt)))
                    frames.extend(pkt)
                try:
                    t0 = time.monotonic()
                    client_sock.sendall(bytes(frames))
                    dt = time.monotonic() - t0
                    if dt > 0.1:
                        logger.warning("Audio proxy: sendall blocked %.1fms (%d frames, %d bytes)", dt*1000, len(batch), len(frames))
                except Exception:
                    stop.set()
                    return
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        finally:
            stop.set()

    def _browser_to_backend():
        try:
            while not stop.is_set():
                try:
                    ready, _, _ = select.select([client_sock], [], [], 1.0)
                except (ValueError, OSError):
                    break
                if not ready:
                    continue  # timeout — re-check stop flag
                opcode, data = _ws_recv(client_sock)
                if opcode is None or opcode == 0x8:
                    break
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        finally:
            stop.set()

    t0 = threading.Thread(target=_backend_reader, daemon=True)
    t1 = threading.Thread(target=_backend_to_browser, daemon=True)
    t2 = threading.Thread(target=_browser_to_backend, daemon=True)
    t0.start()
    t1.start()
    t2.start()

    # Wait until ANY thread signals stop (browser close, backend EOF,
    # write failure). Joining t0 alone would keep the main thread
    # blocked while pulseaudio keeps streaming — the reader only
    # checks stop between packets, so in a busy stream it never sees
    # the flag quickly. Closing both sockets after stop is what
    # actually unblocks any recv() still in flight.
    stop.wait()
    queue_event.set()

    # shutdown() forces any blocked recv() to fail immediately,
    # even on Windows where close() alone may not unblock it
    # when PulseAudio keeps streaming data.
    try:
        backend_sock.shutdown(socket.SHUT_RDWR)
    except Exception:
        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
    try:
        backend_sock.close()
    except Exception:
        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
    try:
        _ws_close(client_sock, 1000, "")
    except Exception:
        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

    t0.join(timeout=2)
    t1.join(timeout=2)
    t2.join(timeout=2)
    if t0.is_alive():
        logger.warning("Audio proxy: reader thread still alive for %s", session_id)

    # Unregister this proxy from the active list
    with _audio_lock:
        entries = _active_proxies.get(session_id, [])
        try:
            entries.remove((stop, backend_sock))
        except ValueError:
            pass
        if session_id in _active_proxies and not entries:
            del _active_proxies[session_id]

    logger.info("Audio proxy: session %s disconnected", session_id)


def _recv_exact(sock, n: int) -> Optional[bytes]:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def _recv_or_stop(sock, n: int, stop_ev: threading.Event) -> Optional[bytes]:
    """Receive exactly *n* bytes, aborting within 500ms if *stop_ev* is set."""
    buf = bytearray()
    while len(buf) < n:
        if stop_ev.is_set():
            return None
        try:
            ready, _, _ = select.select([sock], [], [], 0.5)
        except (ValueError, OSError):
            return None
        if not ready:
            continue
        try:
            chunk = sock.recv(n - len(buf))
        except (socket.timeout, OSError):
            return None
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


# Largest client frame we accept (mic PCM chunks are a few KiB; JSON
# control frames are tiny). A hostile length field must not make
# _recv_exact buffer without bound.
_WS_MAX_CLIENT_FRAME = 16 * 1024 * 1024


def _ws_recv_frame(sock) -> Tuple[Optional[bool], int, bytes]:
    """Read ONE raw frame → (fin, opcode, payload); (None, 0, b"") on EOF
    or protocol garbage (treated as disconnect by every caller)."""
    hdr = _recv_exact(sock, 2)
    if not hdr:
        return None, 0, b""
    fin = bool(hdr[0] & 0x80)
    opcode = hdr[0] & 0x0F
    masked = bool(hdr[1] & 0x80)
    length = hdr[1] & 0x7F
    if length == 126:
        ext = _recv_exact(sock, 2)
        if not ext:
            return None, 0, b""
        length = struct.unpack("!H", ext)[0]
    elif length == 127:
        ext = _recv_exact(sock, 8)
        if not ext:
            return None, 0, b""
        length = struct.unpack("!Q", ext)[0]
    if length > _WS_MAX_CLIENT_FRAME:
        return None, 0, b""
    mask_key = b""
    if masked:
        mask_key = _recv_exact(sock, 4)
        if not mask_key:
            return None, 0, b""
    payload = _recv_exact(sock, length) if length else b""
    if payload is None:
        return None, 0, b""
    if masked and mask_key:
        payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    return fin, opcode, payload


def _ws_recv(sock) -> Tuple[Optional[int], bytes]:
    """Return (opcode, payload) for one complete MESSAGE.

    Fragmented data frames (FIN=0 + continuations, RFC 6455 §5.4 — browsers
    fragment large sends) are reassembled; interleaved pings/pongs are
    dropped (no caller ever answered them); close and EOF surface
    immediately. The reassembled message shares the per-frame size cap.
    """
    msg_opcode = 0
    msg = bytearray()
    while True:
        fin, opcode, payload = _ws_recv_frame(sock)
        if fin is None or opcode == 0x8:
            return (None, b"") if fin is None else (opcode, payload)
        if opcode >= 0x8:  # ping/pong — never fragmented (§5.5), skip
            continue
        if opcode:  # text/binary: first (or only) fragment
            msg_opcode = opcode
            msg = bytearray(payload)
        elif not msg_opcode:
            return None, b""  # stray continuation — protocol violation
        else:
            msg.extend(payload)
            if len(msg) > _WS_MAX_CLIENT_FRAME:
                return None, b""
        if fin:
            return msg_opcode, bytes(msg)


def _ws_send_binary(sock, data: bytes):
    hdr = bytearray()
    hdr.append(0x82)
    if len(data) < 126:
        hdr.append(len(data))
    elif len(data) < 65536:
        hdr.append(126)
        hdr.extend(struct.pack("!H", len(data)))
    else:
        hdr.append(127)
        hdr.extend(struct.pack("!Q", len(data)))
    sock.sendall(bytes(hdr) + data)


def _ws_close(sock, code: int, reason: str):
    payload = struct.pack("!H", code) + reason.encode("utf-8")[:123]
    hdr = bytearray([0x88, len(payload)])
    try:
        sock.sendall(bytes(hdr) + payload)
        sock.shutdown(socket.SHUT_WR)
    except Exception:
        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

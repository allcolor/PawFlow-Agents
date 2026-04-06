"""Audio WebSocket Proxy — relay Opus packets between browser and Docker/relay.

Backend protocol (TCP): 2-byte big-endian length + Opus payload.
Browser protocol (WebSocket): binary frames containing raw Opus packets.
"""

import logging
import socket
import struct
import collections
import threading
import time
from typing import Dict, Tuple, Optional

logger = logging.getLogger(__name__)

_audio_sources: Dict[str, Tuple[str, int]] = {}
_audio_lock = threading.Lock()


def register_audio_source(session_id: str, host: str, port: int):
    if not port:
        return
    with _audio_lock:
        _audio_sources[session_id] = (host, port)
    logger.info("Audio proxy: registered %s -> %s:%d", session_id, host, port)


def unregister_audio_source(session_id: str):
    with _audio_lock:
        _audio_sources.pop(session_id, None)


def _get_audio_target(session_id: str) -> Tuple[str, int]:
    with _audio_lock:
        return _audio_sources.get(session_id, ("", 0))


def audio_ws_proxy(client_sock, path_params: dict, meta: dict):
    """WebSocket handler for /audio/{session_id}/stream."""
    session_id = path_params.get("session_id", "")
    if not session_id:
        _ws_close(client_sock, 4000, "Missing session_id")
        return

    target_host, target_port = _get_audio_target(session_id)
    if not target_port:
        _ws_close(client_sock, 4001, "No audio source")
        return

    try:
        backend_sock = socket.create_connection((target_host, target_port), timeout=5)
        backend_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        backend_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)
    except Exception as e:
        logger.warning("Audio proxy: connect failed %s:%d: %s", target_host, target_port, e)
        _ws_close(client_sock, 4002, "Audio source unavailable")
        return

    try:
        client_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except Exception:
        pass

    stop = threading.Event()

    pkt_queue = collections.deque(maxlen=250)  # ~5s of 20ms packets
    queue_event = threading.Event()

    def _backend_reader():
        """Read TCP packets into queue without blocking on WS writes."""
        _last_pkt = time.monotonic()
        _pkt_count = 0
        _interval_start = time.monotonic()
        try:
            while not stop.is_set():
                _t0 = time.monotonic()
                hdr = _recv_exact(backend_sock, 2)
                _read_dt = time.monotonic() - _t0
                if not hdr:
                    break
                if _read_dt > 0.1:
                    logger.warning("Audio proxy reader: TCP recv stalled %.0fms", _read_dt * 1000)
                pkt_len = struct.unpack("!H", hdr)[0]
                if pkt_len == 0:
                    continue
                pkt = _recv_exact(backend_sock, pkt_len)
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
            pass
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
            pass
        finally:
            stop.set()

    def _browser_to_backend():
        try:
            while not stop.is_set():
                opcode, data = _ws_recv(client_sock)
                if opcode is None or opcode == 0x8:
                    break
        except Exception:
            pass
        finally:
            stop.set()

    t0 = threading.Thread(target=_backend_reader, daemon=True)
    t1 = threading.Thread(target=_backend_to_browser, daemon=True)
    t2 = threading.Thread(target=_browser_to_backend, daemon=True)
    t0.start()
    t1.start()
    t2.start()
    t0.join()
    stop.set()
    queue_event.set()
    t1.join(timeout=2)
    t2.join(timeout=2)

    try:
        backend_sock.close()
    except Exception:
        pass
    try:
        _ws_close(client_sock, 1000, "")
    except Exception:
        pass


def _recv_exact(sock, n: int) -> Optional[bytes]:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def _ws_recv(sock) -> Tuple[Optional[int], bytes]:
    hdr = _recv_exact(sock, 2)
    if not hdr:
        return None, b""
    opcode = hdr[0] & 0x0F
    masked = bool(hdr[1] & 0x80)
    length = hdr[1] & 0x7F
    if length == 126:
        ext = _recv_exact(sock, 2)
        if not ext:
            return None, b""
        length = struct.unpack("!H", ext)[0]
    elif length == 127:
        ext = _recv_exact(sock, 8)
        if not ext:
            return None, b""
        length = struct.unpack("!Q", ext)[0]
    mask_key = b""
    if masked:
        mask_key = _recv_exact(sock, 4)
        if not mask_key:
            return None, b""
    payload = _recv_exact(sock, length) if length else b""
    if payload is None:
        return None, b""
    if masked and mask_key:
        payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    return opcode, payload


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
        pass

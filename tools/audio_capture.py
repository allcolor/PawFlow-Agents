#!/usr/bin/env python3
"""Audio capture server — captures system audio and streams Opus over TCP.

Protocol: each packet is 2-byte big-endian length + Opus data.
"""

import argparse
import logging
import platform
import socket
import struct
import subprocess
import threading
import time

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

_clients = []
_clients_lock = threading.Lock()


def _broadcast(opus_packet: bytes):
    if not opus_packet:
        return
    frame = struct.pack("!H", len(opus_packet)) + opus_packet
    dead = []
    with _clients_lock:
        for sock in _clients:
            try:
                sock.sendall(frame)
            except Exception:
                dead.append(sock)
        for s in dead:
            _clients.remove(s)
            try:
                s.close()
            except Exception:
                pass


def _detect_pulse_monitor() -> str:
    try:
        out = subprocess.check_output(["pactl", "list", "short", "sources"], text=True, timeout=5)
        for line in out.strip().split("\n"):
            parts = line.split("\t")
            if len(parts) >= 2 and ".monitor" in parts[1]:
                return parts[1]
    except Exception as e:
        logger.warning("Could not detect PulseAudio monitor: %s", e)
    return "default.monitor"


def _start_ffmpeg_pulse(source_name: str) -> subprocess.Popen:
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "warning",
        "-f", "pulse", "-i", source_name,
        "-ac", "1", "-ar", "48000",
        "-c:a", "libopus", "-b:a", "64k",
        "-frame_duration", "20", "-vbr", "on", "-application", "audio",
        "-f", "ogg", "-page_duration", "20000",
        "pipe:1"
    ]
    logger.info("Starting ffmpeg: %s", " ".join(cmd))
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)


def _parse_ogg_pages(stream):
    pages_seen = 0
    while True:
        sync = stream.read(4)
        if len(sync) < 4:
            return
        if sync != b"OggS":
            buf = sync
            while True:
                b = stream.read(1)
                if not b:
                    return
                buf = buf[1:] + b
                if buf == b"OggS":
                    break
        hdr = stream.read(23)
        if len(hdr) < 23:
            return
        n_segments = hdr[22]
        seg_table = stream.read(n_segments)
        if len(seg_table) < n_segments:
            return
        payload_size = sum(seg_table)
        payload = stream.read(payload_size)
        if len(payload) < payload_size:
            return
        pages_seen += 1
        if pages_seen <= 2:
            continue
        yield payload


def _capture_loop(source: str):
    while True:
        try:
            if source in ("pulse", "auto") and platform.system() != "Windows":
                monitor = _detect_pulse_monitor()
                logger.info("Using PulseAudio monitor: %s", monitor)
                proc = _start_ffmpeg_pulse(monitor)
            else:
                logger.error("Unsupported source '%s' on %s", source, platform.system())
                return
            for opus_packet in _parse_ogg_pages(proc.stdout):
                _broadcast(opus_packet)
            proc.wait()
            logger.warning("ffmpeg exited (code %d)", proc.returncode)
        except Exception as e:
            logger.error("Capture error: %s", e)
        logger.info("Restarting capture in 2s...")
        time.sleep(2)


def _tcp_server(port: int):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", port))
    srv.listen(4)
    logger.info("Audio capture TCP server on port %d", port)
    while True:
        conn, addr = srv.accept()
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        with _clients_lock:
            _clients.append(conn)
        logger.info("Audio client connected from %s (%d total)", addr, len(_clients))


def main():
    parser = argparse.ArgumentParser(description="Audio capture server")
    parser.add_argument("--port", type=int, default=5800)
    parser.add_argument("--source", default="auto", choices=["pulse", "wasapi", "auto"])
    args = parser.parse_args()
    threading.Thread(target=_tcp_server, args=(args.port,), daemon=True).start()
    _capture_loop(args.source)


if __name__ == "__main__":
    main()

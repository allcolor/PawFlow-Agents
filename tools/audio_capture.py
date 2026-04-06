#!/usr/bin/env python3
"""Audio capture server — captures system audio and streams Opus over TCP.

Uses parec (PulseAudio) for PCM capture and libopus (via ctypes) for encoding.
No ffmpeg — direct frame-by-frame encoding with immediate flush.

Protocol: each packet is 2-byte big-endian length + Opus data.
"""

import argparse
import ctypes
import ctypes.util
import logging
import platform
import socket
import struct
import subprocess
import threading
import time

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ── Opus encoder via ctypes ─────────────────────────────────────────

OPUS_APPLICATION_AUDIO = 2049
OPUS_OK = 0
OPUS_SET_BITRATE_REQUEST = 4002
OPUS_SET_VBR_REQUEST = 4006


def _load_opus():
    """Load libopus shared library."""
    path = ctypes.util.find_library("opus")
    if not path:
        # Try common locations
        for candidate in ["/usr/lib/x86_64-linux-gnu/libopus.so.0",
                          "/usr/lib/libopus.so.0", "libopus.so.0"]:
            try:
                return ctypes.cdll.LoadLibrary(candidate)
            except OSError:
                continue
        raise RuntimeError("libopus not found")
    return ctypes.cdll.LoadLibrary(path)


class OpusEncoder:
    """Minimal Opus encoder wrapping libopus via ctypes."""

    def __init__(self, sample_rate=48000, channels=1, bitrate=64000,
                 frame_duration_ms=20, application=OPUS_APPLICATION_AUDIO):
        self._lib = _load_opus()
        self._sample_rate = sample_rate
        self._channels = channels
        self._frame_size = sample_rate * frame_duration_ms // 1000  # samples per frame

        # opus_encoder_create
        err = ctypes.c_int(0)
        self._lib.opus_encoder_create.restype = ctypes.c_void_p
        self._enc = self._lib.opus_encoder_create(
            ctypes.c_int(sample_rate), ctypes.c_int(channels),
            ctypes.c_int(application), ctypes.byref(err))
        if err.value != OPUS_OK or not self._enc:
            raise RuntimeError(f"opus_encoder_create failed: {err.value}")

        # Set bitrate
        self._lib.opus_encoder_ctl(self._enc,
                                   ctypes.c_int(OPUS_SET_BITRATE_REQUEST),
                                   ctypes.c_int(bitrate))
        # Enable VBR
        self._lib.opus_encoder_ctl(self._enc,
                                   ctypes.c_int(OPUS_SET_VBR_REQUEST),
                                   ctypes.c_int(1))

    def encode(self, pcm_bytes: bytes) -> bytes:
        """Encode one frame of PCM (s16le) to Opus. Returns Opus packet bytes."""
        pcm_buf = ctypes.create_string_buffer(pcm_bytes)
        out_buf = ctypes.create_string_buffer(4000)  # max opus packet
        self._lib.opus_encode.restype = ctypes.c_int
        n = self._lib.opus_encode(
            self._enc,
            ctypes.cast(pcm_buf, ctypes.POINTER(ctypes.c_int16)),
            ctypes.c_int(self._frame_size),
            out_buf, ctypes.c_int(4000))
        if n < 0:
            raise RuntimeError(f"opus_encode failed: {n}")
        return out_buf.raw[:n]

    @property
    def frame_bytes(self) -> int:
        """PCM bytes needed per frame (s16le)."""
        return self._frame_size * self._channels * 2

    def __del__(self):
        if hasattr(self, '_enc') and self._enc:
            try:
                self._lib.opus_encoder_destroy(self._enc)
            except Exception:
                pass


# ── TCP broadcast ───────────────────────────────────────────────────

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


# ── PulseAudio capture ──────────────────────────────────────────────

def _detect_pulse_monitor() -> str:
    try:
        out = subprocess.check_output(
            ["pactl", "list", "short", "sources"], text=True, timeout=5)
        for line in out.strip().split("\n"):
            parts = line.split("\t")
            if len(parts) >= 2 and ".monitor" in parts[1]:
                return parts[1]
    except Exception as e:
        logger.warning("Could not detect PulseAudio monitor: %s", e)
    return "default.monitor"


def _capture_loop(source: str):
    if platform.system() == "Windows":
        logger.error("Windows not supported (use WASAPI capture)")
        return

    encoder = OpusEncoder(sample_rate=48000, channels=1, bitrate=64000,
                          frame_duration_ms=20)
    frame_bytes = encoder.frame_bytes  # 1920 bytes (960 samples * 2)

    while True:
        try:
            monitor = _detect_pulse_monitor() if source in ("pulse", "auto") else source
            logger.info("Using PulseAudio monitor: %s", monitor)

            proc = subprocess.Popen(
                ["parec", "--format=s16le", "--rate=48000", "--channels=1",
                 "-d", monitor, "--latency-msec=20"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                bufsize=frame_bytes)

            logger.info("Capture started (parec + libopus, %d bytes/frame)", frame_bytes)
            _pkt_count = 0
            _skip_count = 0
            _interval_start = time.monotonic()
            _next_send = time.monotonic()
            _FRAME_DUR = 0.020  # 20ms per frame

            while True:
                # Blocking read of exactly one frame (20ms at 48kHz)
                pcm = proc.stdout.read(frame_bytes)
                if not pcm or len(pcm) < frame_bytes:
                    break

                _now = time.monotonic()
                # Wallclock pacing: send at exactly 50fps regardless of PA clock
                if _now < _next_send - _FRAME_DUR:
                    # We're >1 frame ahead of wallclock — drop this frame
                    _skip_count += 1
                    continue

                if _now < _next_send:
                    time.sleep(_next_send - _now)

                opus_pkt = encoder.encode(pcm)
                _broadcast(opus_pkt)
                _pkt_count += 1
                _next_send += _FRAME_DUR

                # If we fell way behind (>500ms), reset the clock
                if time.monotonic() > _next_send + 0.5:
                    _next_send = time.monotonic()

                if _now - _interval_start >= 10.0:
                    logger.info("Audio capture: %d pkts/10s (skipped %d)",
                                _pkt_count, _skip_count)
                    _pkt_count = 0
                    _skip_count = 0
                    _interval_start = _now

            proc.wait()
            logger.warning("parec exited (code %s)", proc.returncode)
        except Exception as e:
            logger.error("Capture error: %s", e)
        logger.info("Restarting capture in 2s...")
        time.sleep(2)


# ── TCP server ──────────────────────────────────────────────────────

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
    parser.add_argument("--source", default="auto", choices=["pulse", "auto"])
    args = parser.parse_args()
    threading.Thread(target=_tcp_server, args=(args.port,), daemon=True).start()
    _capture_loop(args.source)


if __name__ == "__main__":
    main()

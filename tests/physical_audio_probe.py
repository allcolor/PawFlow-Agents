"""Real PulseAudio, Opus and relay WebSocket isolation checks for disposable CI."""

from __future__ import annotations

import argparse
import base64
import ctypes
import ctypes.util
import math
import os
import queue
import struct
import subprocess
import sys
import time
import uuid
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

if __package__:
    from tests import physical_runtime_probe as kernel
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import physical_runtime_probe as kernel

RATE = 48000
TONES = {"alpha": 440, "beta": 880}
PROBE = "/opt/pawflow/physical_audio_probe.py"


def tone_samples(name, seconds=2):
    return [round(6000 * math.sin(2 * math.pi * TONES[name] * index / RATE))
            for index in range(RATE * seconds)]


def play_tone(name):
    kernel.require_disposable()
    samples = tone_samples(name)
    pcm = struct.pack("<" + str(len(samples)) + "h", *samples)
    result = subprocess.run(
        ["paplay", "--raw", "--format=s16le", "--rate=48000", "--channels=1",
         "--device=virtual_out", "--latency-msec=20"],
        input=pcm, capture_output=True, timeout=8, check=False,
        env={**os.environ, "XDG_RUNTIME_DIR": "/tmp/xdg-pawflow",
             "PULSE_SERVER": "unix:/tmp/xdg-pawflow/pulse/native"},
    )
    kernel.check(result.returncode == 0, "Tone playback failed: " + result.stderr.decode(errors="replace"))


def validate_samples(samples, name):
    """Require the intended tone and reject silence or a sibling tone mixed into it."""
    window = RATE // 10
    kernel.check(len(samples) >= RATE, "Insufficient decoded audio")
    strengths = {}
    for label, frequency in TONES.items():
        sine = [math.sin(2 * math.pi * frequency * index / RATE) for index in range(window)]
        cosine = [math.cos(2 * math.pi * frequency * index / RATE) for index in range(window)]
        power = 0.0
        for start in range(0, len(samples) - window + 1, window):
            chunk = samples[start:start + window]
            real = sum(value * weight for value, weight in zip(chunk, cosine))
            imag = sum(value * weight for value, weight in zip(chunk, sine))
            power += (real * real + imag * imag) * 4 / (window * window)
        strengths[label] = math.sqrt(power / (len(samples) // window))
    own = strengths[name]
    sibling = strengths["beta" if name == "alpha" else "alpha"]
    kernel.check(own > 500, "Expected audio tone missing: " + str(strengths))
    kernel.check(own > sibling * 10, "Audio crossed logical identities: " + str(strengths))
    return {"samples": len(samples), "tone_hz": TONES[name], "strengths": strengths}


def capture_samples(peer, session_id):
    """Decode three seconds of the production relay's framed mono Opus stream."""
    library = ctypes.util.find_library("opus")
    kernel.check(bool(library), "Audio acceptance requires libopus")
    opus = ctypes.CDLL(library)
    opus.opus_decoder_create.argtypes = [ctypes.c_int32, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
    opus.opus_decoder_create.restype = ctypes.c_void_p
    opus.opus_decode.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int32,
                                ctypes.POINTER(ctypes.c_int16), ctypes.c_int, ctypes.c_int]
    opus.opus_decode.restype = ctypes.c_int
    opus.opus_decoder_destroy.argtypes = [ctypes.c_void_p]
    opus.opus_decoder_destroy.restype = None
    error = ctypes.c_int()
    decoder = opus.opus_decoder_create(RATE, 1, ctypes.byref(error))
    kernel.check(bool(decoder) and error.value == 0, "Could not create Opus decoder")
    samples = []
    deadline = time.monotonic() + 10
    try:
        while len(samples) < RATE * 3:
            try:
                event = peer.audio_events.get(timeout=max(0.001, deadline - time.monotonic()))
            except queue.Empty as error:
                raise RuntimeError("Missing audio for " + peer.name) from error
            kernel.check(event.get("session_id") == session_id, "Audio session crossed identities")
            kernel.check(event.get("type") == "desktop_audio_data", "Audio stream closed early")
            packet = base64.b64decode(event["data"], validate=True)
            kernel.check(bool(packet), "Empty Opus packet")
            output = (ctypes.c_int16 * 5760)()
            size = opus.opus_decode(decoder, packet, len(packet), output, 5760, 0)
            kernel.check(size == 960, "Unexpected Opus frame size: " + str(size))
            samples.extend(output[:size])
    finally:
        opus.opus_decoder_destroy(decoder)
    return samples


def exercise(peers, desktops, round_number):
    sessions = {}
    try:
        for name, peer in peers.items():
            port = desktops[name]["desktop"].get("audio_port")
            kernel.check(port == 6180, "Desktop audio missing: " + str(desktops[name]["desktop"]))
            session_id = str(uuid.uuid4())
            result = peer.command("desktop_audio_open", session_id=session_id, port=port)
            kernel.check(result.get("ok") is True, "Audio tunnel failed: " + str(result))
            sessions[name] = session_id
        # Collect both streams while both logical relays play distinct tones.
        with ThreadPoolExecutor(max_workers=4) as pool:
            captures = {name: pool.submit(capture_samples, peer, sessions[name])
                        for name, peer in peers.items()}
            playback = {name: pool.submit(
                peer.command, "exec", path="/workspace", timeout=10,
                argv=[sys.executable, "-I", PROBE, "--tone", name])
                for name, peer in peers.items()}
            for name, future in playback.items():
                result = future.result(timeout=15)
                kernel.check(result.get("returncode") == 0, "Audio playback failed: " + str(result))
            reports = {}
            for name, future in captures.items():
                samples = future.result(timeout=15)
                reports[name] = validate_samples(samples, name)
                path = kernel.OUTPUT / (name + "-audio-" + str(round_number) + ".wav")
                with wave.open(str(path), "wb") as audio:
                    audio.setnchannels(1)
                    audio.setsampwidth(2)
                    audio.setframerate(RATE)
                    audio.writeframes(struct.pack("<" + str(len(samples)) + "h", *samples))
            return reports
    finally:
        for name, session_id in sessions.items():
            peers[name].command("desktop_audio_close", session_id=session_id)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tone", choices=tuple(TONES), required=True)
    args = parser.parse_args()
    play_tone(args.tone)

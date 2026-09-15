"""Exercise the real libopus ABI in a child so native crashes fail the test."""

import ctypes.util
import platform
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(sys.platform != "linux" or platform.libc_ver()[0] != "glibc"
                    or not ctypes.util.find_library("opus"),
                    reason="Linux glibc and libopus are required for the native encoder check")
@pytest.mark.parametrize("mmap_allocations", [False, True])
def test_native_encoder_creates_encodes_and_destroys_without_pointer_truncation(mmap_allocations):
    script = """
import ctypes
import resource
import runpy
import sys

resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
if sys.argv[2] == "True":
    # glibc M_MMAP_THRESHOLD: force native encoder allocation into high addresses.
    assert ctypes.CDLL(None).mallopt(-3, 0) == 1
encoder_class = runpy.run_path(sys.argv[1])["OpusEncoder"]
encoder = encoder_class()
if sys.argv[2] == "True" and ctypes.sizeof(ctypes.c_void_p) == 8:
    assert encoder._enc > 2 ** 32
packet = encoder.encode(bytes(encoder.frame_bytes))
assert packet
print("encoded", len(packet), flush=True)
del encoder
print("closed", flush=True)
"""
    path = Path(__file__).resolve().parents[1] / "tools/audio_capture.py"
    result = subprocess.run([sys.executable, "-I", "-c", script, str(path), str(mmap_allocations)],
                            capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    assert "encoded" in result.stdout
    assert "closed" in result.stdout

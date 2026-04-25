"""Secure credential storage for PawCode CLI.

Uses OS-native credential protection:
- Windows: DPAPI (CryptProtectData/CryptUnprotectData)
- macOS/Linux: AES-256-GCM with machine-derived key

Tokens are never stored in plain text on disk.
"""

import base64
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Optional

# `platform.system()` triggers `_win32_ver` → `_wmi_query` on
# Python 3.14 Windows, which hangs indefinitely when WMI misbehaves.
# `sys.platform` is a constant string baked at interpreter startup —
# no syscall, instant. The exact mapping we need is "Windows" vs not,
# so derive it from `sys.platform` instead.
_SYSTEM = ("Windows" if sys.platform.startswith("win")
            else "Darwin" if sys.platform == "darwin"
            else "Linux")


# ── Windows DPAPI ─────────────────────────────────────────────────

def _dpapi_encrypt(data: bytes) -> bytes:
    """Encrypt using Windows DPAPI (current user scope)."""
    import ctypes
    import ctypes.wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.wintypes.DWORD),
                     ("pbData", ctypes.POINTER(ctypes.c_char))]

    p_in = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), p_in)
    blob_out = DATA_BLOB()

    if not ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(blob_in), None, None, None, None, 0,
            ctypes.byref(blob_out)):
        raise OSError("CryptProtectData failed")

    result = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    return result


def _dpapi_decrypt(data: bytes) -> bytes:
    """Decrypt using Windows DPAPI."""
    import ctypes
    import ctypes.wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.wintypes.DWORD),
                     ("pbData", ctypes.POINTER(ctypes.c_char))]

    p_in = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), p_in)
    blob_out = DATA_BLOB()

    if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0,
            ctypes.byref(blob_out)):
        raise OSError("CryptUnprotectData failed")

    result = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    return result


# ── AES-GCM fallback (macOS / Linux) ─────────────────────────────

def _derive_key() -> bytes:
    """Derive a machine-scoped encryption key."""
    import socket
    # Combine machine-specific identifiers. Use socket.gethostname()
    # rather than `platform.node()` — same value, but avoids importing
    # the `platform` module which is unsafe to load on Python 3.14
    # Windows (see _SYSTEM comment above).
    parts = [
        os.getlogin(),
        str(Path.home()),
        socket.gethostname(),
    ]
    # On Linux, add machine-id if available
    for mid_path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            parts.append(Path(mid_path).read_text().strip())
            break
        except Exception:
            pass
    seed = ":".join(parts).encode()
    return hashlib.pbkdf2_hmac("sha256", seed, b"pawflow-cli-v1", 100000)


def _aes_encrypt(data: bytes) -> bytes:
    """Encrypt with AES-256-GCM using machine-derived key."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = _derive_key()
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, data, None)
    return nonce + ct


def _aes_decrypt(data: bytes) -> bytes:
    """Decrypt with AES-256-GCM using machine-derived key."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = _derive_key()
    nonce = data[:12]
    ct = data[12:]
    return AESGCM(key).decrypt(nonce, ct, None)


# ── Public API ────────────────────────────────────────────────────

def protect(plaintext: str) -> str:
    """Encrypt a string. Returns base64-encoded ciphertext."""
    data = plaintext.encode("utf-8")
    if _SYSTEM == "Windows":
        encrypted = _dpapi_encrypt(data)
    else:
        encrypted = _aes_encrypt(data)
    return base64.b64encode(encrypted).decode("ascii")


def unprotect(ciphertext: str) -> str:
    """Decrypt a base64-encoded ciphertext. Returns plaintext string."""
    data = base64.b64decode(ciphertext)
    if _SYSTEM == "Windows":
        decrypted = _dpapi_decrypt(data)
    else:
        decrypted = _aes_decrypt(data)
    return decrypted.decode("utf-8")

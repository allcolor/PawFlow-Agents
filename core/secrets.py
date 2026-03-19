"""Secrets management -- encrypt sensitive config values at rest."""

import base64
import hashlib
import hmac
import json
import os
from typing import Optional


class SecretsManager:
    """Encrypts/decrypts sensitive configuration values.

    Uses XOR stream cipher with PBKDF2-derived key for simplicity
    (no external deps). For production, use a proper secrets vault.

    The master key is derived from:
    1. PAWFLOW_SECRET_KEY environment variable (recommended)
    2. A generated key stored in config/secret.key (fallback)
    """

    def __init__(self, key: Optional[str] = None):
        self._key = self._resolve_key(key)

    @staticmethod
    def _resolve_key(key: Optional[str] = None) -> bytes:
        if key:
            return hashlib.pbkdf2_hmac('sha256', key.encode(), b'pawflow-salt', 100000)

        env_key = os.environ.get("PAWFLOW_SECRET_KEY")
        if env_key:
            return hashlib.pbkdf2_hmac('sha256', env_key.encode(), b'pawflow-salt', 100000)

        # Fallback: generate and store a key
        key_path = os.path.join("config", "secret.key")
        if os.path.exists(key_path):
            with open(key_path, "rb") as f:
                return f.read(32)

        os.makedirs("config", exist_ok=True)
        generated = os.urandom(32)
        with open(key_path, "wb") as f:
            f.write(generated)
        return generated

    def encrypt(self, plaintext: str) -> str:
        """Encrypt a string. Returns 'enc:base64_data'."""
        if not plaintext or plaintext.startswith("enc:"):
            return plaintext

        data = plaintext.encode('utf-8')
        iv = os.urandom(16)

        # XOR stream cipher with HMAC for integrity
        key_stream = self._derive_stream(iv, len(data))
        encrypted = bytes(a ^ b for a, b in zip(data, key_stream))

        # HMAC for integrity
        mac = hmac.new(self._key, iv + encrypted, hashlib.sha256).digest()[:16]

        payload = base64.b64encode(iv + encrypted + mac).decode()
        return f"enc:{payload}"

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt a string. If not encrypted (no 'enc:' prefix), returns as-is."""
        if not ciphertext or not ciphertext.startswith("enc:"):
            return ciphertext

        try:
            raw = base64.b64decode(ciphertext[4:])
            iv = raw[:16]
            mac = raw[-16:]
            encrypted = raw[16:-16]

            # Verify HMAC
            expected_mac = hmac.new(self._key, iv + encrypted, hashlib.sha256).digest()[:16]
            if not hmac.compare_digest(mac, expected_mac):
                raise ValueError("Integrity check failed -- wrong key or corrupted data")

            key_stream = self._derive_stream(iv, len(encrypted))
            decrypted = bytes(a ^ b for a, b in zip(encrypted, key_stream))
            return decrypted.decode('utf-8')
        except Exception as e:
            raise ValueError(f"Decryption failed: {e}")

    def _derive_stream(self, iv: bytes, length: int) -> bytes:
        """Derive a key stream using PBKDF2."""
        stream = b""
        counter = 0
        while len(stream) < length:
            block = hashlib.pbkdf2_hmac(
                'sha256', self._key + iv, counter.to_bytes(4, 'big'), 1
            )
            stream += block
            counter += 1
        return stream[:length]

    def encrypt_bytes(self, data: bytes) -> bytes:
        """Encrypt raw bytes. Returns iv + encrypted + mac (no base64 wrapping)."""
        iv = os.urandom(16)
        key_stream = self._derive_stream(iv, len(data))
        encrypted = bytes(a ^ b for a, b in zip(data, key_stream))
        mac = hmac.new(self._key, iv + encrypted, hashlib.sha256).digest()[:16]
        return iv + encrypted + mac

    def decrypt_bytes(self, data: bytes) -> bytes:
        """Decrypt raw bytes (iv + encrypted + mac). Returns plaintext bytes."""
        if len(data) < 32:
            raise ValueError("Data too short to contain iv + mac")
        iv = data[:16]
        mac = data[-16:]
        encrypted = data[16:-16]
        expected_mac = hmac.new(self._key, iv + encrypted, hashlib.sha256).digest()[:16]
        if not hmac.compare_digest(mac, expected_mac):
            raise ValueError("Integrity check failed -- wrong key or corrupted data")
        key_stream = self._derive_stream(iv, len(encrypted))
        return bytes(a ^ b for a, b in zip(encrypted, key_stream))

    def is_encrypted(self, value: str) -> bool:
        return bool(value) and value.startswith("enc:")


# Singleton
_instance: Optional[SecretsManager] = None


def get_secrets_manager() -> SecretsManager:
    global _instance
    if _instance is None:
        _instance = SecretsManager()
    return _instance

"""Phase 7 tests: AEAD secrets v2 + legacy compatibility + key rotation."""

import base64
import os

import pytest

from core.secrets import (
    SecretDecryptError,
    SecretsManager,
    _MAGIC,
)


# ---------------------------------------------------------------------------
# v2 roundtrip
# ---------------------------------------------------------------------------


def test_v2_string_roundtrip():
    sm = SecretsManager("some-password")
    enc = sm.encrypt("hello")
    assert enc.startswith("enc:v2:")
    assert sm.decrypt(enc) == "hello"


def test_v2_unicode_roundtrip():
    sm = SecretsManager("some-password")
    plain = "café 🐱 中文 ümläut"
    assert sm.decrypt(sm.encrypt(plain)) == plain


def test_v2_idempotent_encrypt():
    sm = SecretsManager("p")
    enc = sm.encrypt("x")
    assert sm.encrypt(enc) == enc


def test_v2_empty_passthrough():
    sm = SecretsManager("p")
    assert sm.encrypt("") == ""
    assert sm.decrypt("") == ""
    assert sm.decrypt("plain text no enc prefix") == "plain text no enc prefix"


def test_v2_bytes_roundtrip_includes_magic_header():
    sm = SecretsManager("p")
    enc = sm.encrypt_bytes(b"\x00\x01\x02 large opaque blob")
    assert enc[:len(_MAGIC)] == _MAGIC
    assert sm.decrypt_bytes(enc) == b"\x00\x01\x02 large opaque blob"


def test_v2_two_encryptions_differ_on_nonce():
    """AEAD with random nonce — same plaintext encrypts to distinct
    ciphertexts. Catches a regression where nonce becomes static."""
    sm = SecretsManager("p")
    a = sm.encrypt("same")
    b = sm.encrypt("same")
    assert a != b
    assert sm.decrypt(a) == sm.decrypt(b) == "same"


# ---------------------------------------------------------------------------
# Tamper detection
# ---------------------------------------------------------------------------


def _flip_byte(s: str) -> str:
    """Flip one byte of the base64 envelope to corrupt the ciphertext."""
    raw = bytearray(base64.b64decode(s[len("enc:v2:"):]))
    raw[-1] ^= 0xFF
    return "enc:v2:" + base64.b64encode(bytes(raw)).decode()


def test_v2_tamper_raises():
    sm = SecretsManager("p")
    enc = sm.encrypt("hello")
    tampered = _flip_byte(enc)
    with pytest.raises(SecretDecryptError):
        sm.decrypt(tampered)


def test_v2_wrong_key_raises():
    a = SecretsManager("key-A")
    b = SecretsManager("key-B")
    enc = a.encrypt("only-A-can-read")
    with pytest.raises(SecretDecryptError):
        b.decrypt(enc)


def test_v2_malformed_envelope_raises():
    sm = SecretsManager("p")
    with pytest.raises(SecretDecryptError):
        sm.decrypt("enc:v2:not-base64-at-all!")
    with pytest.raises(SecretDecryptError):
        sm.decrypt("enc:v2:" + base64.b64encode(b"{}").decode())


def test_v2_unknown_kid_raises():
    sm = SecretsManager("p")
    enc = sm.encrypt("hi")
    # Strip our key off the keyring -> kid lookup fails.
    sm._keyring.clear()
    with pytest.raises(SecretDecryptError):
        sm.decrypt(enc)


# ---------------------------------------------------------------------------
# Legacy compatibility (XOR + HMAC)
# ---------------------------------------------------------------------------


def _make_legacy_payload(key: bytes, plaintext: str) -> str:
    """Recreate the pre-v2 `enc:<b64>` payload format so we can verify
    that the new manager still reads payloads written by the old one."""
    import hashlib
    import hmac
    iv = os.urandom(16)
    data = plaintext.encode("utf-8")
    stream = b""
    counter = 0
    while len(stream) < len(data):
        stream += hashlib.pbkdf2_hmac(
            "sha256", key + iv, counter.to_bytes(4, "big"), 1)
        counter += 1
    encrypted = bytes(a ^ b for a, b in zip(data, stream[:len(data)]))
    mac = hmac.new(key, iv + encrypted, hashlib.sha256).digest()[:16]
    return "enc:" + base64.b64encode(iv + encrypted + mac).decode()


def test_legacy_string_decrypt_via_same_password():
    """Legacy XOR payload encrypted with password P decrypts under a v2
    manager built with the same P. The legacy KDF profile (PBKDF2 +
    pawflow-salt) is reproduced from the boot password so existing
    deployments don't lose access on upgrade."""
    sm = SecretsManager("shared-password")
    key = sm._legacy_xor_key
    legacy = _make_legacy_payload(key, "old data")
    assert sm.decrypt(legacy) == "old data"


def test_legacy_xor_key_uses_pbkdf2_pawflow_salt():
    """Regression guard: the legacy XOR key MUST be derived with the
    pre-v2 KDF (PBKDF2-HMAC-SHA256 + b'pawflow-salt' + 100k iters)
    when the boot key comes from a password. The first cut of v2
    used the scrypt-derived key for legacy too, which silently broke
    every existing `enc:<v1>` payload at upgrade."""
    import hashlib
    sm = SecretsManager("some-password")
    expected = hashlib.pbkdf2_hmac(
        "sha256", b"some-password", b"pawflow-salt", 100000)
    assert sm._legacy_xor_key == expected


def test_legacy_payload_made_by_pre_v2_code_decrypts():
    """Concrete reproduction: build a payload exactly like the
    pre-v2 SecretsManager would have, then read it back from the
    new manager initialised with the same password."""
    import hashlib
    import hmac
    import os
    pwd = "the-old-password"
    legacy_key = hashlib.pbkdf2_hmac(
        "sha256", pwd.encode("utf-8"), b"pawflow-salt", 100000)
    plaintext = b"old-style-secret"
    iv = os.urandom(16)
    stream = b""
    counter = 0
    while len(stream) < len(plaintext):
        stream += hashlib.pbkdf2_hmac(
            "sha256", legacy_key + iv,
            counter.to_bytes(4, "big"), 1)
        counter += 1
    ct = bytes(a ^ b for a, b in zip(plaintext, stream[:len(plaintext)]))
    mac = hmac.new(legacy_key, iv + ct, hashlib.sha256).digest()[:16]
    payload = "enc:" + base64.b64encode(iv + ct + mac).decode()
    sm = SecretsManager(pwd)
    assert sm.decrypt(payload) == "old-style-secret"


def test_legacy_string_wrong_key_raises():
    a = SecretsManager("key-A")
    b = SecretsManager("key-B")
    legacy = _make_legacy_payload(a._legacy_xor_key, "hi")
    with pytest.raises(SecretDecryptError):
        b.decrypt(legacy)


def test_legacy_string_corrupt_raises():
    sm = SecretsManager("p")
    legacy = _make_legacy_payload(sm._legacy_xor_key, "abc")
    raw = bytearray(base64.b64decode(legacy[4:]))
    raw[-1] ^= 0xFF
    corrupt = "enc:" + base64.b64encode(bytes(raw)).decode()
    with pytest.raises(SecretDecryptError):
        sm.decrypt(corrupt)


# ---------------------------------------------------------------------------
# Key rotation
# ---------------------------------------------------------------------------


def test_rotation_old_then_new_kid():
    """Add a second key under kid `k2`, switch current to it, write a
    new secret. Old secrets (kid `k1`) still decrypt; new ones use k2."""
    sm = SecretsManager("original")
    enc_old = sm.encrypt("value-from-k1")
    new_key = os.urandom(32)
    sm.add_key("k2", new_key)
    sm.set_current("k2")
    enc_new = sm.encrypt("value-from-k2")
    # Both still decrypt.
    assert sm.decrypt(enc_old) == "value-from-k1"
    assert sm.decrypt(enc_new) == "value-from-k2"
    # New payload is stamped k2.
    import json
    payload_new = json.loads(
        base64.b64decode(enc_new[len("enc:v2:"):]).decode())
    assert payload_new["kid"] == "k2"


def test_set_current_unknown_kid_raises():
    sm = SecretsManager("p")
    with pytest.raises(KeyError):
        sm.set_current("nonexistent")


def test_add_key_validates_length():
    sm = SecretsManager("p")
    with pytest.raises(ValueError):
        sm.add_key("k2", b"too short")
    with pytest.raises(ValueError):
        sm.add_key("", os.urandom(32))


# ---------------------------------------------------------------------------
# Bytes (sidecar) legacy + magic header
# ---------------------------------------------------------------------------


def test_bytes_legacy_no_magic_decrypts():
    """Old sidecars have no magic header — we must still read them."""
    sm = SecretsManager("shared")
    # Reproduce the old `iv + encrypted + mac` layout.
    import hashlib
    import hmac
    key = sm._legacy_xor_key
    iv = os.urandom(16)
    data = b"sidecar payload bytes"
    stream = b""
    counter = 0
    while len(stream) < len(data):
        stream += hashlib.pbkdf2_hmac(
            "sha256", key + iv, counter.to_bytes(4, "big"), 1)
        counter += 1
    encrypted = bytes(a ^ b for a, b in zip(data, stream[:len(data)]))
    mac = hmac.new(key, iv + encrypted, hashlib.sha256).digest()[:16]
    legacy = iv + encrypted + mac
    assert sm.decrypt_bytes(legacy) == data


def test_bytes_v2_with_magic_decrypts():
    sm = SecretsManager("p")
    blob = b"\x00" * 1024 + b"interesting bits"
    enc = sm.encrypt_bytes(blob)
    assert enc[:len(_MAGIC)] == _MAGIC
    assert sm.decrypt_bytes(enc) == blob


def test_bytes_tamper_raises():
    sm = SecretsManager("p")
    enc = bytearray(sm.encrypt_bytes(b"hello"))
    enc[-1] ^= 0xFF
    with pytest.raises(SecretDecryptError):
        sm.decrypt_bytes(bytes(enc))


# ---------------------------------------------------------------------------
# Boot key resolution
# ---------------------------------------------------------------------------


def test_b64_env_key(monkeypatch):
    raw = os.urandom(32)
    monkeypatch.setenv("PAWFLOW_SECRET_KEY_B64", base64.b64encode(raw).decode())
    monkeypatch.delenv("PAWFLOW_SECRET_KEY", raising=False)
    sm = SecretsManager()
    assert sm._keyring["k1"] == raw


def test_b64_env_key_wrong_length_raises(monkeypatch):
    monkeypatch.setenv("PAWFLOW_SECRET_KEY_B64",
                       base64.b64encode(b"too-short").decode())
    monkeypatch.delenv("PAWFLOW_SECRET_KEY", raising=False)
    with pytest.raises(SecretDecryptError):
        SecretsManager()


def test_password_env_key_derives_via_scrypt(monkeypatch):
    monkeypatch.setenv("PAWFLOW_SECRET_KEY", "some-password")
    monkeypatch.delenv("PAWFLOW_SECRET_KEY_B64", raising=False)
    sm_env = SecretsManager()
    sm_arg = SecretsManager("some-password")
    # Both derive the same key from the same password.
    assert sm_env._keyring["k1"] == sm_arg._keyring["k1"]

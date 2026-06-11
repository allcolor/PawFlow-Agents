"""Phase 7 tests: AEAD secrets v2 + key rotation."""

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


def test_non_v2_secret_format_raises():
    sm = SecretsManager("p")
    with pytest.raises(SecretDecryptError):
        sm.decrypt("enc:not-v2")


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
# Bytes sidecars
# ---------------------------------------------------------------------------


def test_bytes_without_magic_header_raises():
    sm = SecretsManager("shared")
    with pytest.raises(SecretDecryptError):
        sm.decrypt_bytes(b"sidecar payload bytes")


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


# ---------------------------------------------------------------------------
# Per-install scrypt salt
# ---------------------------------------------------------------------------


def test_no_salt_file_uses_legacy_salt(monkeypatch, tmp_path):
    # No env, no salt file -> legacy fixed salt (existing installs unchanged).
    import core.paths as paths
    import core.secrets as secrets
    monkeypatch.delenv("PAWFLOW_SECRET_SALT_B64", raising=False)
    monkeypatch.setattr(paths, "SECRET_SALT_FILE", tmp_path / "secret.salt")
    assert secrets._resolve_scrypt_salt() == secrets._LEGACY_SCRYPT_SALT


def test_salt_file_overrides_legacy(monkeypatch, tmp_path):
    import core.paths as paths
    import core.secrets as secrets
    monkeypatch.delenv("PAWFLOW_SECRET_SALT_B64", raising=False)
    salt_file = tmp_path / "secret.salt"
    salt_file.write_bytes(b"x" * 32)
    monkeypatch.setattr(paths, "SECRET_SALT_FILE", salt_file)
    assert secrets._resolve_scrypt_salt() == b"x" * 32


def test_salt_env_takes_priority(monkeypatch, tmp_path):
    import base64 as _b64
    import core.paths as paths
    import core.secrets as secrets
    salt_file = tmp_path / "secret.salt"
    salt_file.write_bytes(b"f" * 32)
    monkeypatch.setattr(paths, "SECRET_SALT_FILE", salt_file)
    env_salt = b"e" * 24
    monkeypatch.setenv("PAWFLOW_SECRET_SALT_B64", _b64.b64encode(env_salt).decode())
    assert secrets._resolve_scrypt_salt() == env_salt


def test_per_install_salt_changes_derived_key(monkeypatch, tmp_path):
    # Same password, different salt -> different master key. This is the
    # whole point: two installs sharing a password do not share a key.
    import core.paths as paths
    import core.secrets as secrets
    monkeypatch.delenv("PAWFLOW_SECRET_SALT_B64", raising=False)
    salt_file = tmp_path / "secret.salt"
    monkeypatch.setattr(paths, "SECRET_SALT_FILE", salt_file)

    legacy_key = SecretsManager("shared-pw")._keyring["k1"]
    salt_file.write_bytes(os.urandom(32))
    salted_key = SecretsManager("shared-pw")._keyring["k1"]
    assert legacy_key != salted_key


def test_ensure_install_salt_writes_on_fresh_install(monkeypatch, tmp_path):
    import core.paths as paths
    import core.secrets as secrets
    monkeypatch.delenv("PAWFLOW_SECRET_SALT_B64", raising=False)
    salt_file = tmp_path / "secret.salt"
    monkeypatch.setattr(paths, "SECRET_SALT_FILE", salt_file)
    monkeypatch.setattr(paths, "SECRET_KEY_FILE", tmp_path / "secret.key")
    monkeypatch.setattr(paths, "GLOBAL_SECRETS_FILE", tmp_path / "global_secrets.json")

    assert secrets.ensure_install_salt() is True
    assert salt_file.exists()
    assert len(salt_file.read_bytes()) >= secrets._MIN_SALT_LEN
    # Idempotent: a second call is a no-op (salt already present).
    assert secrets.ensure_install_salt() is False


def test_ensure_install_salt_noop_when_secrets_exist(monkeypatch, tmp_path):
    # Existing install (global secrets already encrypted under legacy salt):
    # must NOT introduce a random salt that would orphan them.
    import core.paths as paths
    import core.secrets as secrets
    monkeypatch.delenv("PAWFLOW_SECRET_SALT_B64", raising=False)
    salt_file = tmp_path / "secret.salt"
    monkeypatch.setattr(paths, "SECRET_SALT_FILE", salt_file)
    monkeypatch.setattr(paths, "SECRET_KEY_FILE", tmp_path / "secret.key")
    gsf = tmp_path / "global_secrets.json"
    gsf.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(paths, "GLOBAL_SECRETS_FILE", gsf)

    assert secrets.ensure_install_salt() is False
    assert not salt_file.exists()

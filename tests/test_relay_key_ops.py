"""Phase 5b/6 relay-side ops (pawflow_relay/key_ops) — opt-in action handlers.

These run inside the relay; the server invokes them only via the new command
actions. Validates dispatch gating, locked-key safety, and that the workspace
mount/unmount degrade cleanly when CryFS isn't present (the unit can't mount a
real FUSE store — that's the user's live validation).
"""

import base64
import os

import pytest

from pawflow_relay import key_ops
from core.relay_keywrap import generate_relay_keypair, seal_dek, key_id_for


def test_is_key_action_gating():
    for a in ("ws_mount_encrypted", "ws_unmount", "key_pubkey_get", "key_unseal"):
        assert key_ops.is_key_action(a)
    for a in ("read", "write", "exec", "list", ""):
        assert not key_ops.is_key_action(a)  # existing actions untouched


def test_unknown_action():
    assert key_ops.handle("nope", {})["ok"] is False


def test_key_serve_locked_by_default(monkeypatch):
    monkeypatch.delenv("PAWFLOW_RELAY_PRIVKEY_B64", raising=False)
    assert key_ops.pubkey_get()["ok"] is False
    assert key_ops.unseal({"items": []})["ok"] is False


def test_key_serve_when_unlocked(monkeypatch):
    priv, pub = generate_relay_keypair()
    monkeypatch.setenv("PAWFLOW_RELAY_PRIVKEY_B64", base64.b64encode(priv).decode())
    pk = key_ops.pubkey_get()
    assert pk["ok"] and pk["key_id"] == key_id_for(pub)
    # unseal a DEK sealed to this relay
    dek = bytes(range(32))
    wrap = seal_dek(dek, pub)
    out = key_ops.unseal({"items": [{"conversation_id": "c1", "wrap": wrap}]})
    assert out["ok"]
    assert base64.b64decode(out["deks"]["c1"]) == dek


def test_key_unseal_skips_foreign_wrap(monkeypatch):
    priv, _ = generate_relay_keypair()
    _, other_pub = generate_relay_keypair()
    monkeypatch.setenv("PAWFLOW_RELAY_PRIVKEY_B64", base64.b64encode(priv).decode())
    wrap = seal_dek(bytes(range(32)), other_pub)  # sealed to a different key
    out = key_ops.unseal({"items": [{"conversation_id": "c1", "wrap": wrap}]})
    assert out["ok"] and out["deks"] == {}


def test_mount_requires_fields():
    assert key_ops.mount_encrypted_workspace({})["ok"] is False
    assert key_ops.unmount_workspace({})["ok"] is False


def test_mount_missing_cryfs_degrades_cleanly(tmp_path, monkeypatch):
    # Force the cryfs binary to be absent -> clean error, no crash.
    monkeypatch.setenv("PATH", "/nonexistent")
    res = key_ops.mount_encrypted_workspace({
        "cipher_dir": str(tmp_path / "cipher"),
        "mount_dir": str(tmp_path / "mnt"),
        "dek": base64.b64encode(bytes(range(32))).decode(),
    })
    assert res["ok"] is False and "cryfs" in res["error"].lower()

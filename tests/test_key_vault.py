"""Phase 1 tests: KeyVault + DEK/KEK passphrase wrap + multi-wrap container.

Covers core/key_vault.py (encryption-at-rest design, RFC phase 1):
  * DEK minting and passphrase wrap/unwrap roundtrip
  * wrong-passphrase -> KeyUnwrapError (AEAD tag failure, no leak)
  * per-wrap random salt: same passphrase, two resources -> different blobs
  * resource-bound AAD: a wrap cannot be transplanted across resources
  * tamper detection
  * multi-wrap container slots + passphrase change
  * KeyVault RAM custody: put/get/drop, zeroise, session/source purge
"""

import pytest

from core.key_vault import (
    DEK_LEN,
    KeyUnwrapError,
    KeyVault,
    create_passphrase_protected,
    new_dek,
    new_wrap_container,
    remove_wrap,
    set_passphrase_wrap,
    unwrap_dek_passphrase,
    unwrap_with_passphrase,
    wrap_dek_passphrase,
    WRAP_SLOTS,
)
from core.secrets import SecretDecryptError


# ---------------------------------------------------------------------------
# DEK + passphrase wrap roundtrip
# ---------------------------------------------------------------------------

def test_new_dek_length_and_randomness():
    a, b = new_dek(), new_dek()
    assert len(a) == DEK_LEN == 32
    assert a != b  # random


def test_passphrase_wrap_roundtrip():
    dek = new_dek()
    wrap = wrap_dek_passphrase(dek, "correct horse", "conv:abc")
    assert wrap["scheme"] == "pf-wrap-v1"
    assert unwrap_dek_passphrase(wrap, "correct horse", "conv:abc") == dek


def test_wrong_passphrase_raises_keyunwraperror():
    dek = new_dek()
    wrap = wrap_dek_passphrase(dek, "right", "conv:abc")
    with pytest.raises(KeyUnwrapError):
        unwrap_dek_passphrase(wrap, "wrong", "conv:abc")


def test_keyunwraperror_is_secretdecrypterror():
    # Existing fail-loud handling (except SecretDecryptError) must catch it.
    assert issubclass(KeyUnwrapError, SecretDecryptError)


def test_per_wrap_salt_differs_for_same_passphrase():
    dek = new_dek()
    w1 = wrap_dek_passphrase(dek, "same-pass", "conv:1")
    w2 = wrap_dek_passphrase(dek, "same-pass", "conv:2")
    assert w1["kdf"]["salt"] != w2["kdf"]["salt"]
    assert w1["aead"]["ct"] != w2["aead"]["ct"]


def test_wrap_is_bound_to_resource_id():
    # A wrap minted for conv:1 must not open under conv:2's AAD even with the
    # right passphrase -- prevents transplanting a wrap between conversations.
    dek = new_dek()
    wrap = wrap_dek_passphrase(dek, "pass", "conv:1")
    assert unwrap_dek_passphrase(wrap, "pass", "conv:1") == dek
    with pytest.raises(KeyUnwrapError):
        unwrap_dek_passphrase(wrap, "pass", "conv:2")


def test_tampered_ciphertext_raises():
    dek = new_dek()
    wrap = wrap_dek_passphrase(dek, "pass", "conv:1")
    wrap["aead"]["ct"] = wrap["aead"]["ct"][:-4] + "AAAA"
    with pytest.raises(KeyUnwrapError):
        unwrap_dek_passphrase(wrap, "pass", "conv:1")


def test_malformed_envelope_raises():
    with pytest.raises(KeyUnwrapError):
        unwrap_dek_passphrase({"scheme": "nope"}, "pass", "conv:1")
    with pytest.raises(KeyUnwrapError):
        unwrap_dek_passphrase({"scheme": "pf-wrap-v1"}, "pass", "conv:1")


def test_wrap_rejects_bad_dek_length():
    with pytest.raises(ValueError):
        wrap_dek_passphrase(b"too-short", "pass", "conv:1")


# ---------------------------------------------------------------------------
# Multi-wrap container
# ---------------------------------------------------------------------------

def test_new_container_has_all_slots_empty():
    c = new_wrap_container("conv:x")
    assert c["resource_id"] == "conv:x"
    assert set(c["wraps"].keys()) == set(WRAP_SLOTS)
    assert all(v is None for v in c["wraps"].values())


def test_create_passphrase_protected_roundtrip():
    dek, container = create_passphrase_protected("conv:x", "hunter2")
    assert len(dek) == DEK_LEN
    assert container["wraps"]["pass"] is not None
    assert unwrap_with_passphrase(container, "hunter2") == dek


def test_passphrase_change_rewraps_same_dek():
    dek, container = create_passphrase_protected("conv:x", "old")
    set_passphrase_wrap(container, dek, "new")
    assert unwrap_with_passphrase(container, "new") == dek
    with pytest.raises(KeyUnwrapError):
        unwrap_with_passphrase(container, "old")


def test_remove_wrap_clears_slot():
    dek, container = create_passphrase_protected("conv:x", "p")
    remove_wrap(container, "pass")
    assert container["wraps"]["pass"] is None
    with pytest.raises(KeyUnwrapError):
        unwrap_with_passphrase(container, "p")


def test_remove_wrap_rejects_unknown_slot():
    c = new_wrap_container("conv:x")
    with pytest.raises(ValueError):
        remove_wrap(c, "bogus")


# ---------------------------------------------------------------------------
# KeyVault RAM custody
# ---------------------------------------------------------------------------

def test_vault_put_get_drop():
    v = KeyVault()
    dek = new_dek()
    assert not v.is_unlocked("conv:1")
    v.put("conv:1", dek)
    assert v.is_unlocked("conv:1")
    assert v.get("conv:1") == dek
    assert v.drop("conv:1") is True
    assert v.get("conv:1") is None
    assert v.drop("conv:1") is False


def test_vault_get_returns_copy_not_internal_buffer():
    v = KeyVault()
    dek = new_dek()
    v.put("conv:1", dek)
    got = bytearray(v.get("conv:1"))
    got[0] ^= 0xFF  # mutate the copy
    assert v.get("conv:1") == dek  # internal buffer untouched


def test_vault_put_replaces_and_zeroises_previous():
    v = KeyVault()
    v.put("conv:1", new_dek())
    dek2 = new_dek()
    v.put("conv:1", dek2)
    assert v.get("conv:1") == dek2


def test_vault_rejects_bad_dek_length():
    v = KeyVault()
    with pytest.raises(ValueError):
        v.put("conv:1", b"short")


def test_vault_purge_session():
    v = KeyVault()
    v.put("conv:1", new_dek(), session_id="sess-A")
    v.put("conv:2", new_dek(), session_id="sess-A")
    v.put("conv:3", new_dek(), session_id="sess-B")
    assert v.purge_session("sess-A") == 2
    assert not v.is_unlocked("conv:1")
    assert not v.is_unlocked("conv:2")
    assert v.is_unlocked("conv:3")  # other session unaffected


def test_vault_purge_source_enforces_relay_gone_invariant():
    v = KeyVault()
    v.put_all([("conv:1", new_dek()), ("conv:2", new_dek())], source="relay-7")
    v.put("conv:3", new_dek(), source="relay-9")
    assert v.purge_source("relay-7") == 2
    assert not v.is_unlocked("conv:1")
    assert not v.is_unlocked("conv:2")
    assert v.is_unlocked("conv:3")


def test_vault_drop_cleans_session_and_source_indexes():
    v = KeyVault()
    v.put("conv:1", new_dek(), session_id="sess-A", source="relay-7")
    v.drop("conv:1")
    # Re-purging the now-empty owners must report zero, not raise.
    assert v.purge_session("sess-A") == 0
    assert v.purge_source("relay-7") == 0


def test_vault_purge_idle():
    v = KeyVault()
    v.put("conv:1", new_dek())
    v.put("conv:2", new_dek())
    # nothing is older than an hour -> no purge
    assert v.purge_idle(3600) == 0
    assert v.is_unlocked("conv:1")
    # a negative budget makes the cutoff the future -> everything is stale
    assert v.purge_idle(-1) == 2
    assert v.unlocked_ids() == set()


def test_vault_get_refreshes_idle_stamp():
    v = KeyVault()
    v.put("conv:1", new_dek())
    assert v.get("conv:1") is not None      # activity
    assert v.purge_idle(3600) == 0          # still fresh


def test_vault_clear():
    v = KeyVault()
    v.put("conv:1", new_dek(), session_id="s")
    v.put("conv:2", new_dek(), source="r")
    v.clear()
    assert v.unlocked_ids() == set()
    assert v.purge_session("s") == 0
    assert v.purge_source("r") == 0


def test_vault_full_lifecycle_with_wrap():
    # End-to-end: enable -> store DEK -> lock -> unlock from disk wrap.
    v = KeyVault()
    dek, container = create_passphrase_protected("conv:42", "s3cret")
    v.put("conv:42", dek, session_id="sess-1")
    assert v.get("conv:42") == dek
    v.drop("conv:42")  # idle-lock
    assert v.get("conv:42") is None
    # Reopen: unwrap from the on-disk container, back into the vault.
    recovered = unwrap_with_passphrase(container, "s3cret")
    v.put("conv:42", recovered, session_id="sess-1")
    assert v.get("conv:42") == dek

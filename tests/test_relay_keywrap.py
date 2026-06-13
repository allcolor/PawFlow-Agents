"""Phase 5: asymmetric key-relay wrap (X25519 sealed-box) + store enrollment.

Proves (RFC #5/#7): a DEK can be sealed to a relay's public key so only the
relay's private key recovers it; the server holds no key that opens the wrap;
the wrap is bound to its key_id; and the ConversationStore enroll/unbind path
writes/clears the relay slot and a relay can unseal a real conversation DEK.
"""

import base64

import pytest

from core.conversation_store import ConversationLockedError, ConversationStore
import core.key_vault as key_vault
from core.key_vault import (
    create_passphrase_protected, get_key_vault, set_relay_wrap,
    unwrap_with_relay, unwrap_with_passphrase,
)
from core.relay_keywrap import (
    generate_relay_keypair, key_id_for, public_from_private, seal_dek,
    unseal_dek,
)
from core.secrets import SecretDecryptError

DEK = bytes(range(32))


@pytest.fixture(autouse=True)
def _reset():
    ConversationStore.reset()
    key_vault._reset_for_tests()
    yield
    ConversationStore.reset()
    key_vault._reset_for_tests()


# -- primitive ---------------------------------------------------------

def test_seal_unseal_roundtrip():
    priv, pub = generate_relay_keypair()
    wrap = seal_dek(DEK, pub)
    assert wrap["scheme"] == "pf-relaywrap-x25519-v1"
    assert unseal_dek(wrap, priv) == DEK


def test_public_from_private_matches_keypair():
    priv, pub = generate_relay_keypair()
    assert public_from_private(priv) == pub


def test_key_id_is_stable_fingerprint():
    _, pub = generate_relay_keypair()
    assert key_id_for(pub) == key_id_for(pub)
    wrap = seal_dek(DEK, pub)
    assert wrap["key_id"] == key_id_for(pub)


def test_wrong_private_key_fails():
    _, pub = generate_relay_keypair()
    other_priv, _ = generate_relay_keypair()
    wrap = seal_dek(DEK, pub)
    with pytest.raises(SecretDecryptError):
        unseal_dek(wrap, other_priv)


def test_seal_is_nondeterministic():
    _, pub = generate_relay_keypair()
    assert seal_dek(DEK, pub)["ct"] != seal_dek(DEK, pub)["ct"]  # ephemeral key


def test_tampered_wrap_rejected():
    priv, pub = generate_relay_keypair()
    wrap = seal_dek(DEK, pub)
    wrap["ct"] = wrap["ct"][:-4] + "AAAA"
    with pytest.raises(SecretDecryptError):
        unseal_dek(wrap, priv)


def test_seal_rejects_bad_dek_length():
    _, pub = generate_relay_keypair()
    with pytest.raises(ValueError):
        seal_dek(b"short", pub)


# -- container slot ----------------------------------------------------

def test_container_relay_slot_roundtrip():
    priv, pub = generate_relay_keypair()
    dek, container = create_passphrase_protected("conv:x", "pw")
    set_relay_wrap(container, dek, pub)
    assert container["wraps"]["relay"] is not None
    # both wraps open the same DEK
    assert unwrap_with_relay(container, priv) == dek
    assert unwrap_with_passphrase(container, "pw") == dek


# -- store enrollment --------------------------------------------------

def _enc_conv(tmp_path):
    s = ConversationStore(store_dir=str(tmp_path / "c"))
    cid = s.generate_id()
    s.save(cid, [], user_id="alice")
    s.enable_encryption(cid, "pw", session_id="sess-1")
    return s, cid


def test_set_conv_relay_enrolls_and_relay_can_unseal(tmp_path):
    priv, pub = generate_relay_keypair()
    s, cid = _enc_conv(tmp_path)
    st = s.set_conv_relay(cid, base64.b64encode(pub).decode())
    assert st["has_relay_wrap"] and st["relay_key_id"] == key_id_for(pub)
    # the relay, holding only its private key, recovers the live DEK
    wrap_container = s._encryption_descriptor(cid)["container"]
    recovered = unwrap_with_relay(wrap_container, priv)
    assert recovered == get_key_vault().get(f"conv:{cid}")


def test_set_conv_relay_requires_unlocked(tmp_path):
    _, pub = generate_relay_keypair()
    s, cid = _enc_conv(tmp_path)
    s.lock_encryption(cid)
    with pytest.raises(ConversationLockedError):
        s.set_conv_relay(cid, base64.b64encode(pub).decode())


def test_remove_conv_relay_clears_slot(tmp_path):
    _, pub = generate_relay_keypair()
    s, cid = _enc_conv(tmp_path)
    s.set_conv_relay(cid, base64.b64encode(pub).decode())
    st = s.remove_conv_relay(cid)
    assert not st["has_relay_wrap"] and st["relay_key_id"] == ""
    assert s.relay_wrap_for(cid) is None


def test_relay_delivered_dek_unlock_and_purge_on_relay_gone(tmp_path):
    # The push-at-connect / need-DEK path: relay unseals, server unlocks with
    # the DEK tagged to the relay connection; relay drop re-locks.
    priv, pub = generate_relay_keypair()
    s, cid = _enc_conv(tmp_path)
    s.set_conv_relay(cid, base64.b64encode(pub).decode())
    wrap = s.relay_wrap_for(cid)
    s.lock_encryption(cid)
    assert s.encryption_status(cid)["state"] == "locked"

    dek = unseal_dek(wrap, priv)               # relay-side
    s.unlock_encryption_with_dek(cid, dek, source="relay-conn-1")
    assert s.encryption_status(cid)["state"] == "unlocked"

    get_key_vault().purge_source("relay-conn-1")   # relay disconnected
    assert s.encryption_status(cid)["state"] == "locked"

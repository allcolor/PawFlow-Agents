"""Phase 5: relay-side key custody (core/relay_key_store) + CLI parser.

The relay private key is passphrase-locked at rest and only the relay can
unseal a wrap_relay. This exercises the file custody and the end-to-end loop:
relay init -> enroll pubkey server-side -> server seals a DEK -> relay unseals
with its passphrase-unlocked private key.
"""

import base64

import pytest

from core import relay_key_store as ks
from core.key_vault import KeyUnwrapError
from core.relay_keywrap import key_id_for, seal_dek, unseal_dek

DEK = bytes(range(32))
PW = "relay-pass-phrase"


def test_init_then_status_and_export(tmp_path):
    assert ks.status(tmp_path)["exists"] is False
    key_id, pub_b64 = ks.init_relay_key(tmp_path, PW)
    assert key_id and pub_b64
    st = ks.status(tmp_path)
    assert st["exists"] and st["key_id"] == key_id and st["pub_b64"] == pub_b64
    assert ks.export_pubkey(tmp_path) == (key_id, pub_b64)
    assert key_id_for(base64.b64decode(pub_b64)) == key_id


def test_private_key_is_passphrase_locked_at_rest(tmp_path):
    ks.init_relay_key(tmp_path, PW)
    raw = ks.key_path(tmp_path).read_text(encoding="utf-8")
    # the file carries only a wrapped private key, never the raw bytes
    assert "priv_wrap" in raw and "pf-wrap-v1" in raw
    assert ks.verify_passphrase(tmp_path, PW) is True
    assert ks.verify_passphrase(tmp_path, "wrong") is False
    with pytest.raises(KeyUnwrapError):
        ks.load_private(tmp_path, "wrong")


def test_file_mode_is_0600(tmp_path):
    import os
    ks.init_relay_key(tmp_path, PW)
    mode = os.stat(ks.key_path(tmp_path)).st_mode & 0o777
    assert mode == 0o600


def test_init_refuses_overwrite_without_rotate(tmp_path):
    ks.init_relay_key(tmp_path, PW)
    with pytest.raises(FileExistsError):
        ks.init_relay_key(tmp_path, PW)


def test_rotate_changes_key_id(tmp_path):
    kid1, _ = ks.init_relay_key(tmp_path, PW)
    kid2, _ = ks.rotate(tmp_path, "new-pass")
    assert kid2 != kid1
    assert ks.verify_passphrase(tmp_path, "new-pass") is True
    assert ks.verify_passphrase(tmp_path, PW) is False


def test_init_requires_passphrase(tmp_path):
    with pytest.raises(ValueError):
        ks.init_relay_key(tmp_path, "")


def test_end_to_end_enroll_and_unseal(tmp_path):
    # relay side: generate + store
    key_id, pub_b64 = ks.init_relay_key(tmp_path, PW)
    # server side: seal a conversation DEK to the enrolled pubkey
    wrap = seal_dek(DEK, base64.b64decode(pub_b64))
    assert wrap["key_id"] == key_id
    # relay side: unlock private key with passphrase, unseal
    priv = ks.load_private(tmp_path, PW)
    assert unseal_dek(wrap, priv) == DEK


def test_cli_parser_has_key_subcommands():
    from pawflow_relay.manager_cli import build_parser
    p = build_parser()
    for sub in ("init", "status", "export-pubkey", "rotate", "verify"):
        ns = p.parse_args(["key", sub])
        assert ns.command == "key" and ns.key_command == sub

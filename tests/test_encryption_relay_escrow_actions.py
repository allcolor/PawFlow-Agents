"""Action-layer wiring for relay enrollment (phase 5) + escrow (phase 7),
plus structural checks that the slash commands are reachable.
"""

import base64
import json
from pathlib import Path

import pytest

from core import FlowFile
from core.conversation_store import ConversationStore
import core.key_vault as key_vault
from core.relay_keywrap import generate_relay_keypair, key_id_for
from tasks.ai.actions.conversation import _handle_conversation

UID = "alice"
PASS = "primary-pass"


@pytest.fixture(autouse=True)
def _reset():
    ConversationStore.reset()
    key_vault._reset_for_tests()
    yield
    ConversationStore.reset()
    key_vault._reset_for_tests()


@pytest.fixture
def enc(tmp_path):
    s = ConversationStore(store_dir=str(tmp_path / "c"))
    cid = s.generate_id()
    s.save(cid, [], user_id=UID)
    s.enable_encryption(cid, PASS, session_id="sess-1")
    return s, cid


def _call(store, action, body):
    ff = FlowFile(content=b"")
    ff.set_attribute("auth.session_id", "sess-1")
    assert _handle_conversation(None, action, body, store, UID, ff) == [ff]
    return json.loads(ff.get_content().decode("utf-8")), ff


def test_set_and_remove_relay_via_action(enc):
    s, cid = enc
    _, pub = generate_relay_keypair()
    pub_b64 = base64.b64encode(pub).decode()
    st, _ = _call(s, "conv_encrypt_set_relay",
                  {"conversation_id": cid, "relay_pubkey": pub_b64})
    assert st["has_relay_wrap"] and st["relay_key_id"] == key_id_for(pub)
    st, _ = _call(s, "conv_encrypt_remove_relay", {"conversation_id": cid})
    assert st["has_relay_wrap"] is False


def test_set_relay_requires_pubkey(enc):
    s, cid = enc
    payload, ff = _call(s, "conv_encrypt_set_relay", {"conversation_id": cid})
    assert ff.get_attribute("http.response.status") == "400"
    assert "relay_pubkey" in payload["error"]


def test_escrow_actions(enc):
    s, cid = enc
    st, _ = _call(s, "conv_encrypt_set_escrow",
                  {"conversation_id": cid, "recovery_passphrase": "rec-pass"})
    assert st["has_escrow"] is True
    # recover via action after lock
    _call(s, "conv_encrypt_lock", {"conversation_id": cid})
    st, _ = _call(s, "conv_encrypt_recover",
                  {"conversation_id": cid, "recovery_passphrase": "rec-pass"})
    assert st["state"] == "unlocked"
    st, _ = _call(s, "conv_encrypt_remove_escrow", {"conversation_id": cid})
    assert st["has_escrow"] is False


def test_recover_wrong_passphrase_inline(enc):
    s, cid = enc
    _call(s, "conv_encrypt_set_escrow",
          {"conversation_id": cid, "recovery_passphrase": "rec-pass"})
    _call(s, "conv_encrypt_lock", {"conversation_id": cid})
    payload, _ = _call(s, "conv_encrypt_recover",
                       {"conversation_id": cid, "recovery_passphrase": "bad"})
    assert payload == {"ok": False, "error": "wrong_passphrase"}


def test_slash_commands_reachable():
    cmd = Path("tasks/io/chat_ui/cmd_conversation.js").read_text(encoding="utf-8")
    assert "sub === 'relay'" in cmd and "conv_encrypt_set_relay" in cmd
    assert "sub === 'escrow'" in cmd and "sub === 'recover'" in cmd
    misc = Path("tasks/io/chat_ui/cmd_misc.js").read_text(encoding="utf-8")
    assert "relay_workspace_encrypt" in misc and "relay_workspace_unlock" in misc


def test_relay_start_has_unlock_key_flag():
    from pawflow_relay.manager_cli import build_parser
    ns = build_parser().parse_args(["start", "ws", "--unlock-key"])
    assert ns.unlock_key is True

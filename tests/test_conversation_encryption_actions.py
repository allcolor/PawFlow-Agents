"""Phase 4b: conv_encrypt_* fireAction handlers + /encrypt wiring.

Drives the action layer (tasks/ai/actions/conversation.py) against a real store
and asserts the JSON contract the chat UI relies on, plus structural checks
that the slash command and i18n keys are wired.
"""

import json
from pathlib import Path

import pytest

from core import FlowFile
from core.conversation_store import ConversationStore
import core.key_vault as key_vault
from tasks.ai.actions.conversation import _handle_conversation

UID = "alice"
PASS = "correct horse battery staple"


@pytest.fixture(autouse=True)
def _reset():
    ConversationStore.reset()
    key_vault._reset_for_tests()
    yield
    ConversationStore.reset()
    key_vault._reset_for_tests()


@pytest.fixture
def store(tmp_path):
    return ConversationStore(store_dir=str(tmp_path / "conversations"))


def _call(store, action, body, session="sess-1"):
    ff = FlowFile(content=b"")
    if session is not None:
        ff.set_attribute("auth.session_id", session)
    out = _handle_conversation(None, action, body, store, UID, ff)
    assert out == [ff]
    return json.loads(ff.get_content().decode("utf-8")), ff


def _conv(store):
    cid = store.generate_id()
    store.save(cid, [], user_id=UID)
    return cid


def test_status_then_enable_unlock_lock_disable(store):
    cid = _conv(store)
    body = {"conversation_id": cid}

    st, _ = _call(store, "conv_encrypt_status", body)
    assert st["state"] == "off"

    st, _ = _call(store, "conv_encrypt_enable", {**body, "passphrase": PASS})
    assert st["state"] == "unlocked" and st["has_pass_wrap"]

    st, _ = _call(store, "conv_encrypt_lock", body)
    assert st["state"] == "locked"

    st, _ = _call(store, "conv_encrypt_unlock", {**body, "passphrase": PASS},
                  session="sess-2")
    assert st["state"] == "unlocked"

    st, _ = _call(store, "conv_encrypt_disable", body)
    assert st["state"] == "off"


def test_enable_requires_passphrase(store):
    cid = _conv(store)
    payload, ff = _call(store, "conv_encrypt_enable", {"conversation_id": cid})
    assert ff.get_attribute("http.response.status") == "400"
    assert "passphrase" in payload["error"]


def test_wrong_passphrase_reports_inline(store):
    cid = _conv(store)
    body = {"conversation_id": cid}
    _call(store, "conv_encrypt_enable", {**body, "passphrase": PASS})
    _call(store, "conv_encrypt_lock", body)
    payload, ff = _call(store, "conv_encrypt_unlock",
                        {**body, "passphrase": "nope"})
    assert payload == {"ok": False, "error": "wrong_passphrase"}
    assert ff.get_attribute("http.response.status") == "200"  # inline, not an error code


def test_disable_while_locked_reports_locked(store):
    cid = _conv(store)
    body = {"conversation_id": cid}
    _call(store, "conv_encrypt_enable", {**body, "passphrase": PASS})
    _call(store, "conv_encrypt_lock", body)
    payload, _ = _call(store, "conv_encrypt_disable", body)
    assert payload["ok"] is False and payload["error"] == "locked"


def test_missing_conversation_id_is_400(store):
    payload, ff = _call(store, "conv_encrypt_status", {})
    assert ff.get_attribute("http.response.status") == "400"
    assert "conversation_id" in payload["error"]


def test_passphrase_change_via_action(store):
    cid = _conv(store)
    body = {"conversation_id": cid}
    _call(store, "conv_encrypt_enable", {**body, "passphrase": PASS})
    payload, _ = _call(store, "conv_encrypt_passwd",
                       {**body, "old_passphrase": PASS, "new_passphrase": "new9"})
    assert payload == {"ok": True}
    _call(store, "conv_encrypt_lock", body)
    bad, _ = _call(store, "conv_encrypt_unlock", {**body, "passphrase": PASS})
    assert bad["error"] == "wrong_passphrase"
    good, _ = _call(store, "conv_encrypt_unlock", {**body, "passphrase": "new9"})
    assert good["state"] == "unlocked"


# ── structural: slash command + i18n wiring ──────────────────────────

_UI = Path("tasks/io/chat_ui")


def test_slash_command_registered():
    commands = (_UI / "commands.js").read_text(encoding="utf-8")
    cmd_conv = (_UI / "cmd_conversation.js").read_text(encoding="utf-8")
    assert "'/encrypt':" in commands
    assert "function cmdEncrypt(" in cmd_conv
    for act in ("conv_encrypt_status", "conv_encrypt_enable",
                "conv_encrypt_unlock", "conv_encrypt_lock",
                "conv_encrypt_disable", "conv_encrypt_passwd"):
        assert act in cmd_conv, f"{act} not wired in cmd_conversation.js"


def test_i18n_keys_present_in_all_locales():
    keys = ["encryption", "setPassphrasePrompt", "confirmPassphrase",
            "wrongPassphrase", "enterPassphrase", "locked", "unlocked",
            "noRecoveryWarning", "encryptionMigrating", "usageEncrypt"]
    for loc in ("en", "fr", "es"):
        d = json.loads((_UI / "i18n" / f"{loc}.json").read_text(encoding="utf-8"))
        missing = [k for k in keys if k not in d]
        assert not missing, f"{loc}.json missing {missing}"

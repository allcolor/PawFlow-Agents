"""Phase 6: server-relay workspace encryption (CryFS) — server-side core.

Covers the testable parts: workspace DEK lifecycle, the conv-scoped-only
constraint (RFC #8), and CryFS command construction (key delivered via stdin,
never argv). The actual FUSE mount runs on the relay (integration).
"""

import base64

import pytest

from core.conversation_store import ConversationLockedError, ConversationStore
import core.key_vault as key_vault
from core.key_vault import KeyUnwrapError, get_key_vault
import core.workspace_encryption as we

UID = "alice"
PW = "ws-pass"


@pytest.fixture(autouse=True)
def _reset():
    ConversationStore.reset()
    key_vault._reset_for_tests()
    yield
    ConversationStore.reset()
    key_vault._reset_for_tests()


@pytest.fixture
def store(tmp_path):
    s = ConversationStore(store_dir=str(tmp_path / "c"))
    cid = s.generate_id()
    s.save(cid, [], user_id=UID)
    return s, cid


def test_scope_constraint():
    assert we.is_conv_scoped({"scope": "conv"}) is True
    assert we.is_conv_scoped({}) is True             # server relays default conv
    assert we.is_conv_scoped({"scope": "user"}) is False
    assert we.is_conv_scoped({"scope": "global"}) is False
    assert we.is_conv_scoped(None) is False


def test_enable_rejects_non_conv_scoped(store):
    s, cid = store
    with pytest.raises(ValueError):
        we.enable(s, cid, PW, relay_meta={"scope": "global"})


def test_lifecycle_enable_unlock_lock_disable(store):
    s, cid = store
    assert we.status(s, cid)["state"] == "off"
    st = we.enable(s, cid, PW, relay_meta={"scope": "conv"}, session_id="sess-1")
    assert st["state"] == "unlocked"
    assert we.workspace_dek_b64(cid) is not None

    we.lock(s, cid)
    assert we.status(s, cid)["state"] == "locked"
    assert we.workspace_dek_b64(cid) is None

    assert we.unlock(s, cid, PW, session_id="sess-2") is True
    assert we.status(s, cid)["state"] == "unlocked"

    st = we.disable(s, cid)
    assert st["state"] == "off"


def test_wrong_passphrase(store):
    s, cid = store
    we.enable(s, cid, PW, relay_meta={"scope": "conv"})
    we.lock(s, cid)
    with pytest.raises(KeyUnwrapError):
        we.unlock(s, cid, "nope")


def test_disable_requires_unlock(store):
    s, cid = store
    we.enable(s, cid, PW, relay_meta={"scope": "conv"})
    we.lock(s, cid)
    with pytest.raises(ConversationLockedError):
        we.disable(s, cid)


def test_dek_bound_to_session_purged_on_logout(store):
    s, cid = store
    we.enable(s, cid, PW, relay_meta={"scope": "conv"}, session_id="sess-x")
    assert get_key_vault().is_unlocked(f"ws:{cid}")
    get_key_vault().purge_session("sess-x")
    assert not get_key_vault().is_unlocked(f"ws:{cid}")


def test_cryfs_command_never_puts_key_on_argv(store):
    s, cid = store
    we.enable(s, cid, PW, relay_meta={"scope": "conv"})
    dek_b64 = we.workspace_dek_b64(cid)
    argv, env, password = we.build_cryfs_mount_command("/cipher", "/mnt", dek_b64)
    assert argv[0] == "cryfs" and "/cipher" in argv and "/mnt" in argv
    assert dek_b64 not in argv                       # key never on argv (/proc)
    assert all(dek_b64 not in v for v in env.values())
    assert password == dek_b64                       # delivered via stdin
    assert env["CRYFS_FRONTEND"] == "noninteractive"


def test_unmount_command():
    assert we.build_cryfs_unmount_command("/mnt") == ["cryfs-unmount", "/mnt"]


# -- action layer (relay_workspace_*) ---------------------------------

def _call(store, action, body):
    from core import FlowFile
    from tasks.ai.actions.conversation import _handle_conversation
    ff = FlowFile(content=b"")
    ff.set_attribute("auth.session_id", "sess-act")
    out = _handle_conversation(None, action, body, store, UID, ff)
    assert out == [ff]
    import json
    return json.loads(ff.get_content().decode("utf-8")), ff


def test_action_encrypt_unlock_lock_off(store):
    s, cid = store
    body = {"conversation_id": cid}
    st, _ = _call(s, "relay_workspace_encrypt", {**body, "passphrase": PW})
    assert st["state"] == "unlocked"
    st, _ = _call(s, "relay_workspace_lock", body)
    assert st["state"] == "locked"
    st, _ = _call(s, "relay_workspace_unlock", {**body, "passphrase": PW})
    assert st["state"] == "unlocked"
    st, _ = _call(s, "relay_workspace_encrypt_off", body)
    assert st["state"] == "off"


def test_action_wrong_passphrase_inline(store):
    s, cid = store
    body = {"conversation_id": cid}
    _call(s, "relay_workspace_encrypt", {**body, "passphrase": PW})
    _call(s, "relay_workspace_lock", body)
    payload, ff = _call(s, "relay_workspace_unlock", {**body, "passphrase": "x"})
    assert payload == {"ok": False, "error": "wrong_passphrase"}
    assert ff.get_attribute("http.response.status") == "200"


def test_action_encrypt_requires_passphrase(store):
    s, cid = store
    payload, ff = _call(s, "relay_workspace_encrypt", {"conversation_id": cid})
    assert ff.get_attribute("http.response.status") == "400"


# -- spawn_env gating (phase 6 container layout) ----------------------

def test_spawn_env_plaintext_is_unchanged(store):
    s, cid = store
    target, env = we.spawn_env(s, cid, is_workspace_kind=True, relay_workspace="/workspace")
    assert target == "/workspace" and env == []


def test_spawn_env_non_workspace_kind_unchanged(store):
    s, cid = store
    we.enable(s, cid, PW, relay_meta={"scope": "conv"})
    target, env = we.spawn_env(s, cid, is_workspace_kind=False, relay_workspace="/workspace")
    assert target == "/workspace" and env == []


def test_spawn_env_encrypted_unlocked_binds_cipher_and_passes_dek(store):
    s, cid = store
    we.enable(s, cid, PW, relay_meta={"scope": "conv"})
    target, env = we.spawn_env(s, cid, is_workspace_kind=True, relay_workspace="/workspace")
    assert target == "/workspace_cipher"
    joined = " ".join(env)
    assert "PAWFLOW_WS_CIPHER_DIR=/workspace_cipher" in joined
    assert "PAWFLOW_WS_MOUNT=/workspace" in joined
    assert "PAWFLOW_WS_DEK_B64=" in joined
    # the DEK in env matches the unlocked vault DEK
    dek_b64 = we.workspace_dek_b64(cid)
    assert f"PAWFLOW_WS_DEK_B64={dek_b64}" in env


def test_spawn_env_encrypted_locked_raises(store):
    from core.conversation_store import ConversationLockedError
    s, cid = store
    we.enable(s, cid, PW, relay_meta={"scope": "conv"})
    we.lock(s, cid)
    with pytest.raises(ConversationLockedError):
        we.spawn_env(s, cid, is_workspace_kind=True, relay_workspace="/workspace")

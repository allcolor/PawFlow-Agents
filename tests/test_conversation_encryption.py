"""Phase 4 integration: encryption lifecycle on a real ConversationStore.

Drives the public API (enable/unlock/lock/disable + append_message/load_page +
agent context) on a temp-backed store and asserts the end-to-end invariants:
  * content is ciphertext ON DISK, metadata (msg_id) stays clear
  * decrypted reads round-trip through transcript, shared and agent contexts
    (this is the canary for any content path not routed through the codec)
  * append after enable keeps writing ciphertext
  * locked => reads don't yield plaintext and writes are refused
  * wrong passphrase rejected; correct passphrase restores access
  * disable decrypts back to clear on disk
  * logout purges the session's DEKs (re-locks)
"""

import time
import uuid

import pytest

from core.conversation_store import ConversationLockedError, ConversationStore
import core.key_vault as key_vault
from core.key_vault import KeyUnwrapError, get_key_vault

UID = "alice"
PASS = "correct horse battery staple"


def _msg(content="hello", role="user", source=None, **kw):
    m = {"role": role, "content": content,
         "msg_id": uuid.uuid4().hex[:12], "ts": time.time()}
    if source:
        m["source"] = source
    m.update(kw)
    return m


def _user(content):
    return _msg(content=content, source={"type": "user", "target_agent": "bot"})


@pytest.fixture(autouse=True)
def _reset():
    ConversationStore.reset()
    key_vault._reset_for_tests()
    yield
    ConversationStore.reset()
    key_vault._reset_for_tests()


@pytest.fixture
def conv(tmp_path):
    s = ConversationStore(store_dir=str(tmp_path / "conversations"))
    cid = s.generate_id()
    s.save(cid, [], user_id=UID)
    return s, cid


def _disk_blob(store, cid):
    from core.segmented_jsonl import SegmentedJsonl
    SegmentedJsonl.flush_all_append_handles()
    out = []
    for path in store._content_log_paths(cid):
        seg = SegmentedJsonl(path)
        if seg.exists():
            out.extend(p.read_text() for p in seg.iter_paths())
    return "".join(out)


def _contents(store, cid):
    page = store.load_page(cid, limit=100, offset=0)
    msgs = page["messages"] if isinstance(page, dict) else page
    return [m.get("content") for m in msgs if m.get("role") in ("user", "assistant")]


# ---------------------------------------------------------------------------

def test_status_off_by_default(conv):
    store, cid = conv
    st = store.encryption_status(cid)
    assert st == {"enabled": False, "unlocked": False, "state": "off",
                  "has_pass_wrap": False, "has_relay_wrap": False,
                  "has_escrow": False}


def test_enable_migrates_existing_to_ciphertext_on_disk(conv):
    store, cid = conv
    store.append_message(cid, _user("SECRET-ALPHA"), agent_name="bot", user_id=UID)
    assert "SECRET-ALPHA" in _disk_blob(store, cid)  # clear before enable

    st = store.enable_encryption(cid, PASS, session_id="sess-1")
    assert st["state"] == "unlocked" and st["has_pass_wrap"]

    blob = _disk_blob(store, cid)
    assert "SECRET-ALPHA" not in blob          # content now ciphertext
    assert "enc:cv1:" in blob                  # ...in our envelope
    assert _contents(store, cid) == ["SECRET-ALPHA"]  # decrypts on read


def test_append_after_enable_stays_encrypted_and_readable(conv):
    store, cid = conv
    store.enable_encryption(cid, PASS, session_id="sess-1")
    store.append_message(cid, _user("SECRET-BETA"), agent_name="bot", user_id=UID)
    assert "SECRET-BETA" not in _disk_blob(store, cid)
    assert "SECRET-BETA" in _contents(store, cid)


def test_assistant_tool_call_arguments_encrypted(conv):
    store, cid = conv
    store.enable_encryption(cid, PASS, session_id="sess-1")
    store.append_message(cid, _user("go"), agent_name="bot", user_id=UID)
    store.append_message(cid, _msg(
        role="assistant", content="calling",
        tool_calls=[{"id": "tc1", "name": "edit",
                     "arguments": {"path": "SECRET-PATH.py"}}],
        source={"type": "agent", "name": "bot"}), agent_name="bot", user_id=UID)
    blob = _disk_blob(store, cid)
    assert "SECRET-PATH" not in blob          # tool args are content -> hidden
    assert "edit" in blob                      # tool name is metadata -> clear


def test_agent_context_roundtrip_encrypted(conv):
    store, cid = conv
    store.enable_encryption(cid, PASS, session_id="sess-1")
    store.save_agent_context(cid, "bot", [
        _msg(content="CTX-SECRET", source={"type": "user", "target_agent": "bot"})])
    assert "CTX-SECRET" not in _disk_blob(store, cid)
    page = store.load_agent_context_page(cid, "bot", limit=50, offset=0)
    found = any("CTX-SECRET" == m.get("content")
                for m in (page or {}).get("messages", []))
    assert found


def test_lock_blocks_writes_and_hides_plaintext(conv):
    store, cid = conv
    store.enable_encryption(cid, PASS, session_id="sess-1")
    store.append_message(cid, _user("SECRET-GAMMA"), agent_name="bot", user_id=UID)
    store.lock_encryption(cid)

    assert store.encryption_status(cid)["state"] == "locked"
    # reads while locked must not expose plaintext
    assert "SECRET-GAMMA" not in "".join(str(c) for c in _contents(store, cid))
    # writes while locked are refused
    with pytest.raises(ConversationLockedError):
        store.append_message(cid, _user("nope"), agent_name="bot", user_id=UID)


def test_unlock_wrong_then_right(conv):
    store, cid = conv
    store.enable_encryption(cid, PASS, session_id="sess-1")
    store.append_message(cid, _user("SECRET-DELTA"), agent_name="bot", user_id=UID)
    store.lock_encryption(cid)

    with pytest.raises(KeyUnwrapError):
        store.unlock_encryption(cid, "wrong pass", session_id="sess-2")
    assert store.encryption_status(cid)["state"] == "locked"

    assert store.unlock_encryption(cid, PASS, session_id="sess-2") is True
    assert store.encryption_status(cid)["state"] == "unlocked"
    assert "SECRET-DELTA" in _contents(store, cid)
    # and writing works again
    store.append_message(cid, _user("after-unlock"), agent_name="bot", user_id=UID)
    assert "after-unlock" in _contents(store, cid)


def test_disable_decrypts_back_to_clear(conv):
    store, cid = conv
    store.append_message(cid, _user("SECRET-EPSILON"), agent_name="bot", user_id=UID)
    store.enable_encryption(cid, PASS, session_id="sess-1")
    assert "SECRET-EPSILON" not in _disk_blob(store, cid)

    st = store.disable_encryption(cid, session_id="sess-1")
    assert st["state"] == "off"
    blob = _disk_blob(store, cid)
    assert "SECRET-EPSILON" in blob and "enc:cv1:" not in blob
    assert _contents(store, cid) == ["SECRET-EPSILON"]


def test_disable_requires_unlock(conv):
    store, cid = conv
    store.enable_encryption(cid, PASS, session_id="sess-1")
    store.lock_encryption(cid)
    with pytest.raises(ConversationLockedError):
        store.disable_encryption(cid)


def test_change_passphrase(conv):
    store, cid = conv
    store.enable_encryption(cid, PASS, session_id="sess-1")
    store.append_message(cid, _user("SECRET-ZETA"), agent_name="bot", user_id=UID)
    assert store.change_encryption_passphrase(cid, PASS, "new-pass-9") is True
    store.lock_encryption(cid)
    with pytest.raises(KeyUnwrapError):
        store.unlock_encryption(cid, PASS)            # old no longer works
    assert store.unlock_encryption(cid, "new-pass-9") is True
    assert "SECRET-ZETA" in _contents(store, cid)


def test_logout_purges_session_deks(conv):
    store, cid = conv
    store.enable_encryption(cid, PASS, session_id="sess-logout")
    assert get_key_vault().is_unlocked(f"conv:{cid}")
    from core.security import SecurityManager
    SecurityManager.get_instance().logout("sess-logout")
    assert not get_key_vault().is_unlocked(f"conv:{cid}")  # re-locked


def test_unencrypted_conversation_unaffected(conv):
    # the default path must be byte-for-byte plaintext and codec-free
    store, cid = conv
    store.append_message(cid, _user("plain-msg"), agent_name="bot", user_id=UID)
    assert store._codec_for(cid) is None
    assert "plain-msg" in _disk_blob(store, cid)
    assert _contents(store, cid) == ["plain-msg"]

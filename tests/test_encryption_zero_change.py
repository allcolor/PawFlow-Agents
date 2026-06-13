"""Non-regression guarantee: encryption is OPT-IN, so a conversation that was
never encrypted must behave EXACTLY as before — on disk and through the API.

This is the "zero change for existing (unencrypted) conversations" proof the
feature must not violate.
"""

import json

import pytest

from core.conversation_store import ConversationStore
import core.key_vault as key_vault
from core.segmented_jsonl import SegmentedJsonl

UID = "alice"


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


def _user(content):
    return {"role": "user", "content": content,
            "source": {"type": "user", "target_agent": "bot"}}


def _conv(store):
    cid = store.generate_id()
    store.save(cid, [], user_id=UID)
    return cid


def test_fresh_conv_has_no_encryption_state(store):
    cid = _conv(store)
    # the descriptor is NEVER written unless enable_encryption is called
    assert store.get_extra(cid, "encryption", None) is None
    assert store._is_encryption_enabled(cid) is False
    assert store._codec_for(cid) is None


def test_codec_resolution_does_not_create_state(store):
    cid = _conv(store)
    # resolving the codec on the hot path must not write anything
    for _ in range(3):
        assert store._codec_for(cid) is None
    assert store.get_extra(cid, "encryption", None) is None


def test_on_disk_transcript_is_plaintext_and_schema_unchanged(store):
    cid = _conv(store)
    store.append_message(cid, _user("hello world"), agent_name="bot", user_id=UID)
    SegmentedJsonl.flush_all_append_handles()

    # raw bytes: plaintext, no envelope, no encryption markers
    blob = "".join(p.read_text() for p in
                   SegmentedJsonl(store._transcript_path(cid)).iter_paths())
    assert "hello world" in blob
    assert "enc:cv1:" not in blob and "enc:v2:" not in blob

    # the persisted row carries only the canonical fields — no encryption keys
    rows = list(SegmentedJsonl(store._transcript_path(cid)).iter_rows())
    user_rows = [r for r in rows if r.get("role") == "user"]
    assert user_rows and user_rows[0]["content"] == "hello world"
    for r in rows:
        assert "enc" not in r and "encryption" not in r


def test_append_then_read_roundtrip_identient(store):
    cid = _conv(store)
    for i in range(5):
        store.append_message(cid, _user(f"msg-{i}"), agent_name="bot", user_id=UID)
    page = store.load_page(cid, limit=50, offset=0)
    msgs = page["messages"] if isinstance(page, dict) else page
    contents = [m["content"] for m in msgs if m.get("role") == "user"]
    assert contents == [f"msg-{i}" for i in range(5)]


def test_legacy_plaintext_transcript_reads_unchanged(store, tmp_path):
    """A transcript written the pre-change way (bare SegmentedJsonl, no codec)
    must read back byte-identically through the post-change store."""
    cid = _conv(store)
    path = store._transcript_path(cid)
    legacy_rows = [
        {"role": "user", "content": "legacy one", "msg_id": "m1", "ts": 1.0,
         "seq": 1, "conversation_id": cid, "user_id": UID},
        {"role": "assistant", "content": "legacy two", "msg_id": "m2", "ts": 2.0,
         "seq": 2, "conversation_id": cid, "user_id": UID},
    ]
    SegmentedJsonl(path).append_dicts(legacy_rows)  # pre-change write path

    read_back = list(store._transcript_log(cid).iter_rows())  # post-change read
    assert read_back == legacy_rows  # identical dicts, no transformation


def test_list_conversations_unencrypted_reports_off(store):
    cid = _conv(store)
    from core import FlowFile
    from tasks.ai.actions.conversation import _handle_conversation
    ff = FlowFile(content=b"")
    _handle_conversation(None, "list_conversations", {}, store, UID, ff)
    data = json.loads(ff.get_content().decode("utf-8"))
    convs = {c["conversation_id"]: c for c in data["conversations"]}
    assert convs[cid]["encryption"] == "off"  # additive field, benign for the UI


def test_load_history_unencrypted_skips_locked_gate(store):
    # The load_history locked-gate only triggers when state == "locked". For an
    # unencrypted conversation state is "off", so the gate is skipped and the
    # original full-render path runs exactly as before.
    cid = _conv(store)
    store.append_message(cid, _user("hi"), agent_name="bot", user_id=UID)
    assert store.encryption_status(cid)["state"] == "off"
    page = store.load_page(cid, limit=50, offset=0)
    msgs = page["messages"] if isinstance(page, dict) else page
    assert any(m.get("content") == "hi" for m in msgs)


def test_logout_without_any_dek_is_noop(store):
    # security.logout now purges the vault; with no DEKs it must be a clean no-op
    from core.key_vault import get_key_vault
    assert get_key_vault().purge_session("never-unlocked-session") == 0

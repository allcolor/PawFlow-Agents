"""/rewind file checkpoints live in checkpoints.json, not in extras.json.

The list grows by one entry per user turn. Kept in extras.json it made every
extras read and write decode and encode it (540 KB on a long conversation).
"""

import json

import pytest

from core.checkpoint import CheckpointManager
from core.conversation_store import ConversationStore


@pytest.fixture(autouse=True)
def reset_singleton():
    ConversationStore.reset()
    yield
    ConversationStore.reset()


@pytest.fixture
def conv(tmp_path, monkeypatch):
    store = ConversationStore(store_dir=str(tmp_path / "conversations"))
    monkeypatch.setattr(ConversationStore, "instance", classmethod(lambda cls: store))
    cid = store.generate_id()
    store.save(cid, [], user_id="alice")
    return store, cid


def _extras_on_disk(store, cid):
    return json.loads(store._extras_path(cid).read_text(encoding="utf-8"))


def test_start_checkpoint_writes_checkpoints_json_and_leaves_extras(conv):
    store, cid = conv
    store.set_extra(cid, "title", "t")
    first = CheckpointManager.start_checkpoint(cid)
    second = CheckpointManager.start_checkpoint(cid)

    assert [c["id"] for c in CheckpointManager.list_checkpoints(cid)] == [first, second]
    on_disk = json.loads(store._file_checkpoints_path(cid).read_text(encoding="utf-8"))
    assert [c["id"] for c in on_disk] == [first, second]
    extras = _extras_on_disk(store, cid)
    assert "checkpoints" not in extras
    assert extras["title"] == "t"


def test_legacy_extras_checkpoints_are_moved_once(conv):
    store, cid = conv
    legacy = [{"id": "old1", "timestamp": 1.0, "message_count": 1},
              {"id": "old2", "timestamp": 2.0, "message_count": 2}]
    store.set_extra(cid, "checkpoints", legacy)
    store.set_extra(cid, "title", "kept")

    assert store.get_file_checkpoints(cid) == legacy

    extras = _extras_on_disk(store, cid)
    assert "checkpoints" not in extras
    assert extras["title"] == "kept"
    assert store.get_extra(cid, "checkpoints") is None
    assert store.get_extras_snapshot(cid).get("checkpoints") is None

    new_id = CheckpointManager.start_checkpoint(cid)
    assert [c["id"] for c in store.get_file_checkpoints(cid)] == ["old1", "old2", new_id]


def test_set_file_checkpoints_replaces_list(conv):
    store, cid = conv
    for _ in range(3):
        CheckpointManager.start_checkpoint(cid)
    kept = store.get_file_checkpoints(cid)[:1]
    store.set_file_checkpoints(cid, kept)
    assert store.get_file_checkpoints(cid) == kept


def test_missing_conversation_has_no_checkpoints(conv):
    store, _cid = conv
    ghost = store.generate_id()
    store.append_file_checkpoint(ghost, {"id": "x"})
    assert store.get_file_checkpoints(ghost) == []


def test_checkpoints_json_is_part_of_git_history(conv):
    store, cid = conv
    CheckpointManager.start_checkpoint(cid)
    assert "checkpoints.json" in store._git_snapshot_files(cid)

import json

from core import FlowFile
from core.conversation_store import ConversationStore
from core.file_store import FileStore
from tasks.ai.actions.files_fs import _handle_files_fs


def _payload(flowfile):
    return json.loads(flowfile.get_content().decode("utf-8"))


def test_delete_file_uses_filestore_metadata_not_transcript_text(tmp_path, monkeypatch):
    conv_store = ConversationStore(store_dir=str(tmp_path / "conversations"))
    conv_store.save("conv1", [
        {"role": "user", "content": "no file reference here", "msg_id": "m1"},
    ], user_id="alice")

    file_store = FileStore(base_dir=str(tmp_path / "files"))
    monkeypatch.setattr(FileStore, "_instance", file_store)
    file_id = file_store.store(
        "report.txt", b"content", "text/plain",
        conversation_id="conv1", user_id="alice")

    ff = FlowFile(content=b"")
    result = _handle_files_fs(
        None, "delete_file",
        {"conversation_id": "conv1", "file_id": file_id},
        conv_store, "alice", ff)

    assert result == [ff]
    assert _payload(ff) == {"ok": True, "file_id": file_id}
    assert not file_store.exists(file_id)


def test_delete_file_rejects_other_conversation_file(tmp_path, monkeypatch):
    conv_store = ConversationStore(store_dir=str(tmp_path / "conversations"))
    conv_store.save("conv1", [], user_id="alice")
    conv_store.save("conv2", [], user_id="alice")

    file_store = FileStore(base_dir=str(tmp_path / "files"))
    monkeypatch.setattr(FileStore, "_instance", file_store)
    file_id = file_store.store(
        "report.txt", b"content", "text/plain",
        conversation_id="conv2", user_id="alice")

    ff = FlowFile(content=b"")
    _handle_files_fs(
        None, "delete_file",
        {"conversation_id": "conv1", "file_id": file_id},
        conv_store, "alice", ff)

    assert ff.get_attribute("http.response.status") == "403"
    assert _payload(ff)["error"] == "File not in this conversation"
    assert file_store.exists(file_id)


def test_delete_files_deletes_only_accessible_conversation_files(tmp_path, monkeypatch):
    conv_store = ConversationStore(store_dir=str(tmp_path / "conversations"))
    conv_store.save("conv1", [], user_id="alice")
    conv_store.save("conv2", [], user_id="alice")

    file_store = FileStore(base_dir=str(tmp_path / "files"))
    monkeypatch.setattr(FileStore, "_instance", file_store)
    keep_id = file_store.store(
        "keep.txt", b"keep", "text/plain",
        conversation_id="conv2", user_id="alice")
    delete_a = file_store.store(
        "a.txt", b"a", "text/plain",
        conversation_id="conv1", user_id="alice")
    delete_b = file_store.store(
        "b.txt", b"b", "text/plain",
        conversation_id="conv1", user_id="alice")

    ff = FlowFile(content=b"")
    _handle_files_fs(
        None, "delete_files",
        {"conversation_id": "conv1", "file_ids": [delete_a, keep_id, delete_b]},
        conv_store, "alice", ff)

    payload = _payload(ff)
    assert payload["ok"] is True
    assert payload["deleted"] == 2
    assert set(payload["file_ids"]) == {delete_a, delete_b}
    assert payload["skipped"] == [{"file_id": keep_id, "error": "File not in this conversation"}]
    assert not file_store.exists(delete_a)
    assert not file_store.exists(delete_b)
    assert file_store.exists(keep_id)


def test_filestore_list_hides_transient_stt_and_tts(tmp_path):
    file_store = FileStore(base_dir=str(tmp_path / "files"))
    visible_id = file_store.store(
        "visible.txt", b"visible", "text/plain",
        conversation_id="conv1", user_id="alice")
    file_store.store(
        "speech.wav", b"wav", "audio/wav",
        conversation_id="conv1", user_id="alice", ttl=300,
        category="webchat_stt")
    file_store.store(
        "speech.mp3", b"mp3", "audio/mpeg",
        conversation_id="conv1", user_id="alice", ttl=300,
        category="voice_clone_tts")

    rows = file_store.list_files(user_id="alice", conversation_id="conv1")

    assert [row["file_id"] for row in rows] == [visible_id]

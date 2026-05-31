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
